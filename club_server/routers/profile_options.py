"""The club's profile options: positions and languages (#92, #98, users R69–R69c).

Read here by every signed-in user, approved or not, since the onboarding
step offers them before approval. Written only by the super admin through
``PATCH /v1/admin/preferences/profile_options`` (``admin_preferences``).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..dependencies import get_authenticated_user, get_db
from ..schemas.extended_profile import ProfileOptions
from ..services.profile_options import ProfileOptionsService

router = APIRouter(prefix="/system/preferences", tags=["System preferences"])


@router.get("/profile_options", response_model=ProfileOptions)
async def get_profile_options(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    current_user: Annotated[User, Depends(get_authenticated_user)],
) -> ProfileOptions:
    """The club's positions and languages; empty lists until written."""
    _ = current_user
    return await ProfileOptionsService(db).read()
