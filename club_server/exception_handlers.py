"""Global FastAPI exception handlers for uniformly-mapped exceptions.

Routers used to wrap every service call in a ``try/except SomeNotFoundException``
block and re-raise as ``HTTPException(404, …)`` with a per-exception ``code``.
Each mapping was mechanical and identical across all routers; centralising
them removes ~14 × 4-line blocks per occurrence.

Only exceptions whose mapping is identical everywhere are registered here.
Exceptions whose status code, error code, or message shape varies across
handlers stay inline so the per-route context is preserved.
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from .exceptions import (
    AttendanceNotFoundException,
    BroadcastNotFoundException,
    EnrollmentNotFoundException,
    EnrollmentStateConflictException,
    EventMarketingNotFoundException,
    EventNotFoundException,
    GroupNotFoundException,
    HardDeleteNeedsSoftDeleteException,
    JoinRequestNotFoundException,
    MediaLinkNotFoundException,
    MediaNotFoundException,
    MemberNotFoundException,
    NotificationNotFoundException,
    OccurrenceNotFoundException,
    OrganizerNotFoundException,
    UserNotFoundException,
    CreditDispositionNotApplicableException,
    CreditDispositionRequiredException,
    InquiryNotFoundException,
    InsufficientCreditException,
    InvalidFormTokenException,
    InvalidOccurrenceTimeException,
    InvalidPreferenceValueException,
    MediaConversionFailedException,
    NotACoachException,
    NothingToRestoreException,
    OwnerDeletedException,
    RecipientNotDeliverableException,
    SiteMediaNotPublicException,
    VenueNotFoundException,
)

NOT_FOUND_CODE_MAP: dict[type[Exception], str] = {
    AttendanceNotFoundException: "ATTENDANCE_NOT_FOUND",
    BroadcastNotFoundException: "BROADCAST_NOT_FOUND",
    EnrollmentNotFoundException: "ENROLLMENT_NOT_FOUND",
    EventMarketingNotFoundException: "EVENT_MARKETING_NOT_FOUND",
    EventNotFoundException: "EVENT_NOT_FOUND",
    GroupNotFoundException: "GROUP_NOT_FOUND",
    InquiryNotFoundException: "INQUIRY_NOT_FOUND",
    JoinRequestNotFoundException: "JOIN_REQUEST_NOT_FOUND",
    MediaLinkNotFoundException: "MEDIA_LINK_NOT_FOUND",
    MediaNotFoundException: "MEDIA_NOT_FOUND",
    MemberNotFoundException: "MEMBER_NOT_FOUND",
    NotificationNotFoundException: "NOTIFICATION_NOT_FOUND",
    OccurrenceNotFoundException: "OCCURRENCE_NOT_FOUND",
    OrganizerNotFoundException: "ORGANIZER_NOT_FOUND",
    UserNotFoundException: "USER_NOT_FOUND",
    VenueNotFoundException: "VENUE_NOT_FOUND",
}


UNPROCESSABLE_CODE_MAP: dict[type[Exception], str] = {
    CreditDispositionNotApplicableException: "CREDIT_DISPOSITION_NOT_APPLICABLE",
    CreditDispositionRequiredException: "CREDIT_DISPOSITION_REQUIRED",
    HardDeleteNeedsSoftDeleteException: "HARD_DELETE_NEEDS_SOFT_DELETE",
    InsufficientCreditException: "INSUFFICIENT_CREDIT",
    InvalidFormTokenException: "INVALID_FORM_TOKEN",
    InvalidOccurrenceTimeException: "INVALID_OCCURRENCE_TIME",
    InvalidPreferenceValueException: "INVALID_PREFERENCE_VALUE",
    MediaConversionFailedException: "MEDIA_CONVERSION_FAILED",
    NotACoachException: "NOT_A_COACH",
    NothingToRestoreException: "NOTHING_TO_RESTORE",
    OwnerDeletedException: "OWNER_DELETED",
    RecipientNotDeliverableException: "RECIPIENT_NOT_DELIVERABLE",
    SiteMediaNotPublicException: "SITE_MEDIA_NOT_PUBLIC",
}


CONFLICT_CODE_MAP: dict[type[Exception], str] = {
    EnrollmentStateConflictException: "INVALID_STATE",
}


def register_exception_handlers(app: FastAPI) -> None:
    """Register global handlers for uniformly-mapped exceptions."""

    def _make_not_found_handler(code: str):
        async def _handler(_request: Request, exc: Exception) -> JSONResponse:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": {"code": code, "message": str(exc)}},
            )

        return _handler

    for exc_class, code in NOT_FOUND_CODE_MAP.items():
        app.add_exception_handler(exc_class, _make_not_found_handler(code))

    def _make_unprocessable_handler(code: str):
        async def _handler(_request: Request, exc: Exception) -> JSONResponse:
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                content={"detail": {"code": code, "message": str(exc)}},
            )

        return _handler

    for exc_class, code in UNPROCESSABLE_CODE_MAP.items():
        app.add_exception_handler(exc_class, _make_unprocessable_handler(code))

    def _make_conflict_handler(code: str):
        async def _handler(_request: Request, exc: Exception) -> JSONResponse:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"detail": {"code": code, "message": str(exc)}},
            )

        return _handler

    for exc_class, code in CONFLICT_CODE_MAP.items():
        app.add_exception_handler(exc_class, _make_conflict_handler(code))
