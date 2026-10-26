"""The club's profile options: positions and languages (#92, users R69–R69c).

Read by every signed-in user, approved or not, since the onboarding step
offers them before approval; replaced by an admin. The write belongs to the
optional extended profile and answers 503 while it is off.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..dependencies import (
    get_authenticated_user,
    get_db,
    require_admin,
    require_extended_profile_enabled,
)
from ..schemas.extended_profile import ProfileOptions
from ..services.profile_options import ProfileOptionsService
from ..utils import get_client_ip

router = APIRouter(prefix="/system/preferences", tags=["System preferences"])


@router.get("/profile_options", response_model=ProfileOptions)
async def get_profile_options(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
) -> ProfileOptions:
    """The club's positions and languages; empty lists until written."""
    _ = current_user
    return await ProfileOptionsService(db).read()


@router.put(
    "/profile_options",
    response_model=ProfileOptions,
    dependencies=[Depends(require_extended_profile_enabled)],
)
async def put_profile_options(
    request: Request,
    options: ProfileOptions,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(require_admin())],
) -> ProfileOptions:
    """Replace the club's positions and languages (admin); audited."""
    return await ProfileOptionsService(db).write(
        options, current_user.username, get_client_ip(request)
    )
