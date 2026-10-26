"""Who may write the extended profile fields, and what they may hold (#92).

The fields ride on the ordinary profile edit (``PATCH /v1/users/by_id/…``);
this module holds the rules that are theirs alone: an unapproved user may
set their own and nothing else (users R66), only an admin sets the
membership date (R67), and a position or language must be one of the
club's options when it is written (R64).
"""

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User, UserStatus
from ..schemas.extended_profile import SELF_EXTENDED_FIELDS
from ..schemas.user import UserUpdate
from ..validation import validate_utc_midnight
from .profile_options import ProfileOptionsService

# ``users`` column → the name a client sends, for each extended field.
EXTENDED_FIELD_NAMES: dict[str, str] = {
    "club_district": "clubDistrict",
    "preferred_position": "preferredPosition",
    "past_experience": "pastExperience",
    "nationality": "nationality",
    "preferred_language": "preferredLanguage",
    "guardian": "guardian",
    "membership_date": "membershipDateUtc",
}
_CHOICE_COLUMNS = ("preferred_position", "preferred_language")


def check_profile_edit_access(
    data: UserUpdate, username: str, current_user: User
) -> None:
    """Refuse what the extended-profile rules forbid before anything is written.

    A user not yet approved may edit only their own extended fields (R66);
    anything else answers as it did before they could edit at all. Only an
    admin may send the membership date (R67).
    """
    # Imported here: ``dependencies`` loads the services package, this module
    # among it.
    from ..dependencies import is_admin

    sent = data.model_fields_set
    if current_user.status != UserStatus.active.value and (
        username != current_user.username
        or not sent
        or not sent <= SELF_EXTENDED_FIELDS
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ACCOUNT_NOT_ACTIVE", "message": "Account is not active"},
        )
    if "membership_date_utc" in sent and not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PROTECTED_FIELDS",
                "message": "These fields can only be edited by an admin",
                "fields": ["membershipDateUtc"],
            },
        )


async def check_extended_changes(
    db: AsyncSession, user: User, changes: dict[str, object]
) -> None:
    """Refuse an extended value the profile cannot hold (R64, R67).

    A position or language must be one of the club's options, unless it is
    the value already stored: an option removed later leaves the member's
    choice as it is, and re-sending it is not a change.
    """
    membership_date = changes.get("membership_date")
    validate_utc_midnight(
        membership_date if isinstance(membership_date, int) else None,
        "membershipDateUtc",
    )
    options = ProfileOptionsService(db)
    for column in _CHOICE_COLUMNS:
        value = changes.get(column)
        if not isinstance(value, str) or value == getattr(user, column):
            continue
        if not await options.allows(column, value):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "INVALID_PROFILE_OPTION",
                    "message": f"'{value}' is not one of the club's options",
                    "field": EXTENDED_FIELD_NAMES[column],
                    "value": value,
                },
            )


def apply_extended_changes(user: User, changes: dict[str, object]) -> list[str]:
    """Write the extended values; return the client names of the fields sent."""
    for column, value in changes.items():
        setattr(user, column, value)
    return [EXTENDED_FIELD_NAMES[column] for column in changes]
