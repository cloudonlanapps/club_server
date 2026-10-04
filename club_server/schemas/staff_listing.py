"""Admin staff-listing schemas (#332)."""

from pydantic import Field

from ..db.models.staff_listing import PublicStaffListing
from ..db.models.user import User
from ..utils import get_display_name
from .common import CamelCaseModel


class StaffListingUpdate(CamelCaseModel):
    """Upsert body: any subset; omitted fields keep their stored value."""

    position: int | None = Field(None, ge=0)
    is_guest: bool | None = None
    is_hidden: bool | None = None


class StaffListingRow(CamelCaseModel):
    """One curated coach, joined with the two facts an admin needs beside it."""

    username: str
    display_name: str
    is_public_profile: bool
    position: int | None
    is_guest: bool
    is_hidden: bool

    @classmethod
    def from_models(cls, row: PublicStaffListing, user: User) -> "StaffListingRow":
        """Project a listing row with its user."""
        return cls(
            username=row.username,
            display_name=get_display_name(user),
            is_public_profile=bool(user.is_public_profile),
            position=row.position,
            is_guest=bool(row.is_guest),
            is_hidden=bool(row.is_hidden),
        )
