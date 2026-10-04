from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..dependencies import get_db, require_admin
from ..mailer import EmailMessage, get_transactional_sender
from ..mailer import templates as mailer_templates
from ..mailer.config import email_settings
from ..services.club_info import ClubInfoService
from ..schemas.auth import AdminResetPasswordResponse
from ..services.audit import AuditService
from ..services.audit_actions import AuditAction
from ..services.auth import AuthService
from ..utils import get_client_ip

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.post(
    "/reset-password/{username}",
    response_model=AdminResetPasswordResponse,
)
async def admin_reset_password(
    username: str,
    request: Request,
    current_user: Annotated[User, Depends(require_admin())],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
):
    """Reset a user's password to a server-generated default (admin only).

    The new password is returned to the calling admin and, when the target user
    has an email on file, also emailed to them. Emailing is best-effort: the
    admin always receives the plaintext, so a delivery failure does not fail the
    request — it is only recorded in the audit log.
    """
    auth_service = AuthService(db)
    audit_service = AuditService(db)

    target_user = await auth_service.get_user_by_username(username)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": "User not found"},
        )

    if target_user.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "CANNOT_RESET_SUPER_ADMIN",
                "message": "Cannot reset password for a super admin account",
            },
        )

    new_password = AuthService.generate_default_password()
    success = await auth_service.change_password(
        username, new_password, actor_username=current_user.username
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "PASSWORD_RESET_FAILED",
                "message": "Failed to reset password",
            },
        )

    email_sent = False
    if target_user.email:
        club_name, club_short_name = await ClubInfoService(db).branding()
        subject, html, text = mailer_templates.admin_reset(
            club_name=club_name,
            club_short_name=club_short_name,
            first_name=target_user.first_name,
            new_password=new_password,
            login_url=email_settings.login_url,
        )
        result = await get_transactional_sender().send(
            EmailMessage(to=target_user.email, subject=subject, html=html, text=text)
        )
        email_sent = result.success

    await audit_service.log(
        actor_username=current_user.username,
        action=AuditAction.ADMIN_PASSWORD_RESET,
        target_username=username,
        details={"emailSent": email_sent},
        ip_address=get_client_ip(request),
    )

    return AdminResetPasswordResponse(new_password=new_password)
