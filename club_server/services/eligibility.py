"""Shared enrollment-eligibility predicate.

Used by occurrence listings (#156) and attendance mutations (#157) to decide
whether a user's enrollment covers a given occurrence time `T`.
"""

from ..db.models.enrollment import Enrollment, EnrollmentStatus


ACTIVE_ENROLLMENT_STATUSES: set[str] = {
    EnrollmentStatus.accepted.value,
    EnrollmentStatus.assigned.value,
    EnrollmentStatus.assigned_trial.value,
    EnrollmentStatus.invited.value,
    EnrollmentStatus.requested.value,
    EnrollmentStatus.withdraw_requested.value,
}


def enrollment_covers_occurrence(
    enrollment: Enrollment | None,
    occ_time_utc: int,
    now_ms: int,
) -> bool:
    """Return True if this enrollment makes the user eligible at `occ_time_utc`.

    Future/current occurrences require an active (non-terminal) status.
    Past occurrences require a temporal participation window:
    `enrolled_at <= T` and (`withdrawn_at` is null or `withdrawn_at >= T`).
    """
    if enrollment is None:
        return False
    if occ_time_utc >= now_ms:
        return enrollment.status in ACTIVE_ENROLLMENT_STATUSES
    if enrollment.enrolled_at is None:
        return False
    if enrollment.enrolled_at > occ_time_utc:
        return False
    if enrollment.withdrawn_at is not None and enrollment.withdrawn_at < occ_time_utc:
        return False
    return True
