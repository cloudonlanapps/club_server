"""Utility functions for the club server."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from sqlalchemy.exc import IntegrityError

    from .db.models.user import User


def get_client_ip(request: Request) -> str | None:
    """Extract the client IP from a request.

    Prefers the first hop in ``X-Forwarded-For`` (set by the reverse proxy),
    falling back to the direct peer address. Returns ``None`` if neither is
    available. Used to stamp ``ip_address`` on audit-log entries.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def duplicate_user_error_detail(exc: "IntegrityError") -> dict[str, str]:
    """Map a user-table uniqueness violation to an API error detail.

    Distinguishes the email partial-unique index from the username primary
    key by the constraint name in the driver error; unknown collisions fall
    back to the username code since `username` is the only other unique key.
    """
    message = str(exc.orig) if exc.orig is not None else str(exc)
    if "uq_users_email_active" in message:
        return {"code": "DUPLICATE_EMAIL", "message": "Email already registered"}
    return {"code": "DUPLICATE_USERNAME", "message": "Username already taken"}


def get_display_name(user: "User") -> str:
    """Get display name for a user, respecting privacy settings.

    When use_name_publicly is false: return nickname or 'Name not provided'.
    When use_name_publicly is true: return full name, nickname, or username.

    Args:
        user: The User model instance.

    Returns:
        The computed display name.
    """
    if not user.use_name_publicly:
        if user.nickname:
            return user.nickname
        return "Name not provided"

    # Public mode: build full name from parts
    name_parts = [
        part for part in [user.first_name, user.middle_name, user.last_name] if part
    ]
    if name_parts:
        return " ".join(name_parts)

    # Fallback to nickname, then username
    if user.nickname:
        return user.nickname
    return user.username


def generate_public_id(username: str) -> str:
    """Generate a stable, non-reversible public ID for a username.

    Used in public-facing URLs (e.g. ``/public/profile/by_id/<public_id>``)
    so anonymous visitors never see real usernames. Computed as an
    HMAC-SHA256 of the username keyed by ``settings.secret_key``,
    base64url-encoded, first 22 chars (no padding). Deterministic for a
    given username + secret, but not reversible.
    """
    import base64
    import hashlib
    import hmac

    from .config import settings

    key = settings.secret_key.encode("utf-8")
    msg = username.encode("utf-8")
    digest = hmac.new(key, msg, hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return encoded[:22]


def generate_venue_public_id(venue_id: int) -> str:
    """Opaque public id for a venue, for ``/public/venues/{public_id}`` (#307).

    Same HMAC as :func:`generate_public_id`, keyed on ``venue:<id>`` so a
    venue's public id can never collide with a user's or an event's, and
    anonymous callers cannot walk integer ids.
    """
    return generate_public_id(f"venue:{venue_id}")


def generate_event_public_id(event_id: int) -> str:
    """Opaque public id for an event, for ``/public/events/{public_id}`` (#299)."""
    return generate_public_id(f"event:{event_id}")


def now_utc_ms() -> int:
    """Return current UTC time as milliseconds since epoch."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


MS_PER_DAY = 24 * 60 * 60 * 1000


def truncate_to_utc_day(ms: int) -> int:
    """Floor a UTC ms timestamp to 00:00:00 UTC of that day."""
    return (ms // MS_PER_DAY) * MS_PER_DAY


def ceil_to_utc_day(ms: int) -> int:
    """Ceil a UTC ms timestamp to the next 00:00:00 UTC.

    Already-midnight values are returned unchanged; any other value is
    rounded up to the following day's midnight UTC.
    """
    floored = (ms // MS_PER_DAY) * MS_PER_DAY
    return floored if floored == ms else floored + MS_PER_DAY
