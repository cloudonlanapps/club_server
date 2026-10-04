import json
import secrets
import string
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models.user import Role, User, UserStatus
from ..mailer import EmailMessage, get_transactional_sender
from ..mailer import templates as mailer_templates
from ..mailer.config import email_settings
from .club_info import ClubInfoService
from ..schemas.auth import TokenPayload
from ..schemas.common import UserRoles
from ..utils import now_utc_ms
from ..validation import validate_utc_midnight


REFRESH_TOKEN_TYPE = "refresh"
REFRESH_TOKEN_LIFETIME = timedelta(days=30)


def new_session_id() -> str:
    """A fresh login-session id, carried by its tokens as ``sid`` (#510)."""
    return uuid.uuid4().hex


def _issued_at() -> float:
    """The ``iat`` claim: seconds since the epoch, to the millisecond (#461)."""
    return now_utc_ms() / 1000


@dataclass(frozen=True)
class PasswordResetOutcome:
    """Server-side result of a forgot-password attempt, for audit only.

    Never serialized to the public response — exposing ``account_exists`` would
    enable user enumeration.
    """

    account_exists: bool
    email_sent: bool


class AuthService:
    """Authentication service."""

    def __init__(self, db: AsyncSession):
        self.db: AsyncSession = db

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt."""
        password_bytes = password.encode("utf-8")
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password_bytes, salt).decode("utf-8")

    @staticmethod
    def generate_default_password(length: int = 12) -> str:
        """Generate a random default password."""
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a password against a hash."""
        password_bytes = plain_password.encode("utf-8")
        hashed_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(password_bytes, hashed_bytes)

    @staticmethod
    def create_access_token(
        username: str, is_super_admin: bool = False, session_id: str | None = None
    ) -> str:
        """Create a JWT access token belonging to ``session_id`` (a new
        session when omitted)."""
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.access_token_expire_minutes
        )
        to_encode = {
            "sub": username,
            "exp": expire,
            "iat": _issued_at(),
            "is_super_admin": is_super_admin,
            "sid": session_id or new_session_id(),
        }
        return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)

    @staticmethod
    def decode_token(token: str) -> TokenPayload | None:
        """Decode and validate an access token.

        A refresh token is refused here (#461). Access tokens carry no
        ``type``, so tokens issued before that change still decode.
        """
        try:
            payload = jwt.decode(
                token, settings.secret_key, algorithms=[settings.algorithm]
            )
            if payload.get("type") == REFRESH_TOKEN_TYPE:
                return None
            return TokenPayload.model_validate(payload)
        except JWTError:
            return None

    @staticmethod
    def issued_before_password_change(payload: TokenPayload, user: User) -> bool:
        """True if the token predates the user's latest password change (#461).

        ``iat`` carries milliseconds, so a token minted in the same second
        as the change is judged by which came first. A token without ``iat``
        predates #461 and is refused once the password has changed.
        """
        if user.password_changed_at is None:
            return False
        if payload.iat is None:
            return True
        return round(payload.iat * 1000) < user.password_changed_at

    async def get_user_by_username(self, username: str) -> User | None:
        """Get a user by username."""
        result = await self.db.execute(
            select(User).where(User.username == username, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_user_by_email(self, email: str) -> User | None:
        """Get a live user by email, ignoring case as uniqueness does (#508)."""
        result = await self.db.execute(
            select(User).where(
                func.lower(User.email) == email.lower(), User.deleted_at.is_(None)
            )
        )
        return result.scalar_one_or_none()

    async def authenticate_user(self, username: str, password: str) -> User | None:
        """Authenticate a user by username and password."""
        user = await self.get_user_by_username(username)
        if not user:
            return None
        if not user.password:
            return None
        if not self.verify_password(password, user.password):
            return None
        return user

    async def create_user(
        self,
        username: str,
        password: str,
        email: str | None = None,
        first_name: str | None = None,
        middle_name: str | None = None,
        last_name: str | None = None,
        phone: str | None = None,
        date_of_birth: int | None = None,
        gender: str | None = None,
        address: str | None = None,
        status: UserStatus = UserStatus.pending,
        is_super_admin: bool = False,
        roles: list[str] | None = None,
    ) -> User:
        """Create a new user."""
        validate_utc_midnight(date_of_birth, "dateOfBirthUtc")
        now = now_utc_ms()

        roles_json = "{}"
        if roles:
            roles_json = json.dumps({"roles": roles})

        user = User(
            username=username,
            password=self.hash_password(password),
            email=email,
            first_name=first_name,
            middle_name=middle_name,
            last_name=last_name,
            phone=phone,
            date_of_birth=date_of_birth,
            gender=gender,
            address=address,
            status=status.value,
            is_super_admin=1 if is_super_admin else 0,
            roles=roles_json,
            created_at=now,
        )
        self.db.add(user)
        await self.db.flush()

        await self.db.commit()

        return user

    async def notify_registration_pending(self, user: User) -> None:
        """Notify all active admins that a new user is awaiting approval.

        Avoids an import-time cycle on NotificationService by deferring the
        import — auth.py is imported by test helpers and bootstrap paths,
        so we keep its top-of-module imports minimal.
        """
        from .notification import NotificationEvent, NotificationService

        result = await self.db.execute(
            select(User).where(
                User.deleted_at.is_(None),
                User.status == UserStatus.active.value,
            )
        )
        admins: list[str] = []
        for u in result.scalars().all():
            if u.is_super_admin:
                admins.append(u.username)
                continue
            if not u.roles:
                continue
            parsed = UserRoles.model_validate_json(u.roles)
            if Role.admin.value in parsed.roles:
                admins.append(u.username)

        if not admins:
            return

        await NotificationService(self.db).notify_for_event(
            NotificationEvent(
                type="user.registration_pending",
                recipients=admins,
                data={
                    "username": user.username,
                    "firstName": user.first_name,
                    "lastName": user.last_name,
                    "email": user.email,
                    "registeredAtUtc": user.created_at,
                },
                pending_action_type="user_approval",
                pending_action_key=user.username,
            )
        )

    async def has_any_users(self) -> bool:
        """Check if any users exist in the database."""
        result = await self.db.execute(
            select(func.count()).select_from(User).where(User.deleted_at.is_(None))
        )
        count = result.scalar_one()
        return count > 0

    async def is_username_taken(self, username: str) -> bool:
        """Check if a username is already taken.

        A soft-deleted user still holds their username (it is the primary
        key, and they can be restored), so registration refuses it; the
        check counts them too so the two agree (#505).
        """
        result = await self.db.execute(
            select(func.count()).select_from(User).where(User.username == username)
        )
        return result.scalar_one() > 0

    @staticmethod
    def create_refresh_token(
        username: str, is_super_admin: bool = False, session_id: str | None = None
    ) -> str:
        """Create a JWT refresh token (longer expiry) belonging to
        ``session_id`` (a new session when omitted)."""
        expire = datetime.now(timezone.utc) + REFRESH_TOKEN_LIFETIME
        to_encode = {
            "sub": username,
            "exp": expire,
            "iat": _issued_at(),
            "is_super_admin": is_super_admin,
            "type": REFRESH_TOKEN_TYPE,
            "sid": session_id or new_session_id(),
        }
        return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)

    @staticmethod
    def decode_refresh_token(token: str) -> TokenPayload | None:
        """Decode and validate a refresh token."""
        try:
            payload = jwt.decode(
                token, settings.secret_key, algorithms=[settings.algorithm]
            )
            if payload.get("type") != REFRESH_TOKEN_TYPE:
                return None
            return TokenPayload.model_validate(payload)
        except JWTError:
            return None

    async def change_password(
        self,
        username: str,
        new_password: str,
        actor_username: str | None = None,
    ) -> bool:
        """Change user password.

        ``actor_username`` identifies who is performing the change.
        When omitted or equal to ``username`` we treat it as a
        self-initiated change. When it differs, we emit the
        admin-driven variant so the recipient can see who reset it.
        """
        from .notification import NotificationEvent, NotificationService

        user = await self.get_user_by_username(username)
        if not user:
            return False
        user.password = self.hash_password(new_password)
        user.password_changed_at = now_utc_ms()
        await self.db.flush()

        is_admin_driven = actor_username is not None and actor_username != user.username
        if is_admin_driven:
            event_type = "account.password_changed_by_admin"
            data: dict[str, str] = {
                "username": user.username,
                "actorUsername": actor_username,  # type: ignore[dict-item]
            }
        else:
            event_type = "account.password_changed_self"
            data = {"username": user.username}

        await NotificationService(self.db).notify_for_event(
            NotificationEvent(
                type=event_type,
                recipients=[user.username],
                data=data,
            )
        )

        return True

    async def initiate_password_reset(self, email: str) -> PasswordResetOutcome:
        """Forgot-password flow: generate a new password and email it.

        Mirrors the admin reset model — there is no token/link step. We look the
        user up by email and, only if the email is delivered, set the new
        password. Sending *before* persisting means a delivery failure leaves
        the user's existing password intact rather than locking them out with a
        password they never received.

        The caller (the public endpoint) returns a uniform response regardless
        of the outcome; the outcome is for the audit log only and must never be
        leaked to the caller (anti-enumeration).
        """
        user = await self.get_user_by_email(email)
        if not user or not user.email:
            return PasswordResetOutcome(account_exists=False, email_sent=False)

        new_password = self.generate_default_password()
        club_name, club_short_name = await ClubInfoService(self.db).branding()
        subject, html, text = mailer_templates.forgot_password(
            club_name=club_name,
            club_short_name=club_short_name,
            first_name=user.first_name,
            new_password=new_password,
            login_url=email_settings.login_url,
        )
        result = await get_transactional_sender().send(
            EmailMessage(to=user.email, subject=subject, html=html, text=text)
        )
        if not result.success:
            return PasswordResetOutcome(account_exists=True, email_sent=False)

        _ = await self.change_password(
            user.username, new_password, actor_username=user.username
        )
        return PasswordResetOutcome(account_exists=True, email_sent=True)
