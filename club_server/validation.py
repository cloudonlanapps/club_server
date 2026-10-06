"""Shared validation functions for request handlers."""

from fastapi import HTTPException, status

from .utils import MS_PER_DAY


def validate_utc_midnight(value: int | None, field: str) -> None:
    """Ensure a ms-since-epoch timestamp lands at 00:00:00 UTC of some day.

    A date of birth (`users.date_of_birth`) is a calendar date; only
    UTC-midnight ms values are admissible. Non-midnight inputs indicate a client-side
    timezone bug (e.g. a picker emitting local-midnight) and are rejected
    with 422 rather than silently flattened to an unintended calendar date.
    """
    if value is None:
        return
    if value % MS_PER_DAY != 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_DOB_NOT_UTC_MIDNIGHT",
                "message": (
                    f"{field} must be at 00:00:00 UTC (ms divisible by 86_400_000); "
                    "client must floor to UTC midnight before sending."
                ),
                "field": field,
                "value": value,
            },
        )


def validate_required_profile_fields(
    first_name: str | None,
    last_name: str | None,
    gender: object | None,
    date_of_birth_utc: int | None,
    phone: str | None,
) -> None:
    """Validate required profile fields for user creation.

    Raises HTTPException(400) with descriptive error codes if validation fails.
    """
    missing_fields: list[str] = []

    has_first = first_name is not None and first_name.strip() != ""
    has_last = last_name is not None and last_name.strip() != ""
    if not has_first and not has_last:
        missing_fields.append("first_name or last_name")
    if gender is None:
        missing_fields.append("gender")
    if date_of_birth_utc is None:
        missing_fields.append("date_of_birth_utc")
    if phone is None or phone.strip() == "":
        missing_fields.append("phone")

    if missing_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "MISSING_REQUIRED_PROFILE_FIELDS",
                "message": f"Missing required fields: {', '.join(missing_fields)}",
                "missing_fields": missing_fields,
            },
        )
