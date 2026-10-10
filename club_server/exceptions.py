"""Centralized exception definitions for the club_server application."""


# =============================================================================
# Common Exceptions
# =============================================================================


class InvalidStateException(Exception):
    """Raised when an invalid state transition is attempted."""

    message: str

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class NothingToRestoreException(Exception):
    """Raised when restoring an item that is not soft-deleted (#520, #526)."""

    def __init__(self, entity: str, ident: str | int):
        self.entity = entity
        self.ident = ident
        super().__init__(
            f"{entity} {ident} is not deleted; there is nothing to restore"
        )


class HardDeleteNeedsSoftDeleteException(Exception):
    """Raised when hard-deleting an item that is not soft-deleted (#526)."""

    def __init__(self, entity: str, ident: str | int):
        self.entity = entity
        self.ident = ident
        super().__init__(
            f"{entity} {ident} must be soft-deleted before it is hard-deleted"
        )


# =============================================================================
# User-Related Exceptions
# =============================================================================


class UserNotFoundException(Exception):
    """Raised when a user is not found."""

    username: str
    user_type: str

    def __init__(self, username: str, user_type: str = "User"):
        self.username = username
        self.user_type = user_type
        super().__init__(f"{user_type} {username} not found")


class UserNotActiveException(Exception):
    """Raised when a user is not in active status."""

    username: str
    user_type: str

    def __init__(self, username: str, user_type: str = "User"):
        self.username = username
        self.user_type = user_type
        super().__init__(f"{user_type} {username} is not active")


class RecipientNotDeliverableException(Exception):
    """Raised when a notification is addressed to a user whose account state
    does not receive that type (a user who has left, or a blocked user for a
    type outside the account notices)."""

    username: str
    notification_type: str

    def __init__(self, username: str, notification_type: str):
        self.username = username
        self.notification_type = notification_type
        super().__init__(
            f"User {username} cannot receive a {notification_type} notification"
        )


class CannotBlockSuperAdminException(Exception):
    """Raised when trying to block a super admin."""

    def __init__(self):
        super().__init__("Cannot block super admin")


class RoleAlreadyAssignedException(Exception):
    """Raised when role is already assigned."""

    role: str

    def __init__(self, role: str):
        self.role = role
        super().__init__(f"User already has role: {role}")


class RoleNotFoundException(Exception):
    """Raised when role is not found."""

    role: str

    def __init__(self, role: str):
        self.role = role
        super().__init__(f"User does not have role: {role}")


class UserAlreadyDeletedException(Exception):
    """Raised when user is already deleted."""

    username: str

    def __init__(self, username: str):
        self.username = username
        super().__init__(f"User {username} is already deleted")


class CannotTransferFromNonSuperAdminException(Exception):
    """Raised when trying to transfer super admin from non-super admin."""

    def __init__(self):
        super().__init__("Only super admin can transfer super admin role")


class CannotTransferToSelfException(Exception):
    """Raised when trying to transfer super admin to self."""

    def __init__(self):
        super().__init__("Cannot transfer super admin role to self")


# =============================================================================
# Event-Related Exceptions
# =============================================================================


class EventNotFoundException(Exception):
    """Raised when an event is not found (by integer id or public id)."""

    event_id: int | str

    def __init__(self, event_id: int | str):
        self.event_id = event_id
        super().__init__(f"Event {event_id} not found")


class InvalidRruleException(Exception):
    """Raised when an RRULE string is invalid."""

    rrule: str

    def __init__(self, rrule: str):
        self.rrule = rrule
        super().__init__(f"Invalid RRULE string: {rrule}")


class InvalidCampRruleException(Exception):
    """Raised when an RRULE string is not valid for a camp.

    Camps require FREQ=DAILY and a bound (COUNT or UNTIL).
    Surfaced as HTTP 422 INVALID_RRULE_FOR_CAMP.
    """

    rrule: str
    reason: str

    def __init__(self, rrule: str, reason: str):
        self.rrule = rrule
        self.reason = reason
        super().__init__(f"Invalid camp RRULE ({reason}): {rrule}")


class InvalidProgrammeRruleException(Exception):
    """Raised when an RRULE string is not valid for a programme (R11–R15).

    Weekly, naming its days, with no COUNT, no UNTIL, no EXDATE and no
    INTERVAL. Surfaced as HTTP 422 INVALID_RRULE_FOR_PROGRAMME.
    """

    rrule: str
    reason: str

    def __init__(self, rrule: str, reason: str):
        self.rrule = rrule
        self.reason = reason
        super().__init__(f"Invalid programme RRULE ({reason}): {rrule}")


class InvalidOneOffRruleException(Exception):
    """Raised when a one-off carries a recurrence rule (one-off R13).

    Surfaced as HTTP 422 INVALID_RRULE_FOR_ONEOFF.
    """

    def __init__(self, rrule: str):
        self.rrule = rrule
        super().__init__(f"A one-off carries no recurrence rule: {rrule}")


class CutoffTooSoonException(Exception):
    """Raised when a programme cutoff is under 30 minutes ahead (R3, R24).

    Surfaced as HTTP 422 CUTOFF_TOO_SOON.
    """

    def __init__(self):
        super().__init__("The cutoff must be at least 30 minutes in the future")


class BeyondSchedulingHorizonException(Exception):
    """Raised when a camp or one-off starts beyond the scheduling horizon
    (one-off R20a, R20b). Surfaced as HTTP 422 BEYOND_SCHEDULING_HORIZON."""

    weeks: int

    def __init__(self, weeks: int):
        self.weeks = weeks
        super().__init__(
            f"An event may not be scheduled more than {weeks} weeks from now"
        )


class PostponeOnlyException(Exception):
    """Raised when a reschedule would move an occurrence earlier (R19a).

    Surfaced as HTTP 422 POSTPONE_ONLY.
    """

    def __init__(self):
        super().__init__("an occurrence may only be moved later")


class InvalidDateTimeException(Exception):
    """Raised when datetime validation fails."""

    message: str

    def __init__(self, message: str = "End time must be after start time"):
        self.message = message
        super().__init__(message)


class UserNotEligibleForEventException(Exception):
    """Raised when a user does not satisfy an event's structured eligibility criteria."""

    membername: str
    event_id: int

    def __init__(self, membername: str, event_id: int):
        self.membername = membername
        self.event_id = event_id
        super().__init__(
            f"User {membername} does not satisfy the eligibility criteria for event {event_id}"
        )


class InvalidSessionsException(Exception):
    """Raised when an event's sessions list is malformed or its periods do not sum to the per-occurrence duration."""

    code: str
    expected_minutes: int | None
    actual_minutes: int | None

    def __init__(
        self,
        message: str,
        *,
        code: str = "INVALID_SESSIONS_TOTAL",
        expected_minutes: int | None = None,
        actual_minutes: int | None = None,
    ):
        self.code = code
        self.expected_minutes = expected_minutes
        self.actual_minutes = actual_minutes
        super().__init__(message)


class EventConflictException(Exception):
    """Raised when a schedule write is blocked by a clash (programme R30).

    ``conflict_report`` is the 409 body; ``findings`` the gate findings that
    produced it, for callers that need the clashing event ids.
    """

    def __init__(self, conflict_report: object, findings: list | None = None):
        self.conflict_report = conflict_report
        self.findings = findings or []
        super().__init__("Event has scheduling conflicts")


class EventAlreadyCancelledException(Exception):
    """Raised when trying to cancel an already cancelled event."""

    event_id: int

    def __init__(self, event_id: int):
        self.event_id = event_id
        super().__init__(f"Event {event_id} is already cancelled")


class EventNotCancelledException(Exception):
    """Raised when undoing a cancellation on an event that is not cancelled."""

    event_id: int

    def __init__(self, event_id: int):
        self.event_id = event_id
        super().__init__(f"Event {event_id} is not cancelled")


class InvalidEventTypeException(Exception):
    """Raised when an endpoint is called on an unsupported event type."""

    event_type: str
    allowed_types: list[str]

    def __init__(self, event_type: str, allowed_types: list[str]):
        self.event_type = event_type
        self.allowed_types = allowed_types
        super().__init__(
            f"This operation is not supported for event type '{event_type}'. "
            f"Allowed types: {allowed_types}"
        )


class EventAlreadyStartedException(Exception):
    """Raised when trying to reschedule an event whose series has already begun.

    A schedule reschedule may only run before the first occurrence starts, so
    that no occurrence override or attendance record (both keyed by the
    RRULE-expanded occurrence time) can be orphaned by the schedule change.
    Surfaced as HTTP 422 EVENT_ALREADY_STARTED.
    """

    event_id: int

    def __init__(self, event_id: int):
        self.event_id = event_id
        super().__init__(
            f"Event {event_id} has already started; reschedule is no longer "
            "allowed (use occurrence reschedule, or cancel and recreate)"
        )


class OccurrenceOverridesPresentException(Exception):
    """Raised when a series reschedule is blocked by existing occurrence overrides.

    The overrides are keyed by the old RRULE-expanded slot times and would be
    orphaned by the reschedule. The caller must either clear them or pass
    ``reset_overrides=true`` to wipe them. Surfaced as HTTP 409
    OCCURRENCE_OVERRIDES_PRESENT with the offending occurrence times.
    """

    occurrence_time_utcs: list[int]

    def __init__(self, occurrence_time_utcs: list[int]):
        self.occurrence_time_utcs = occurrence_time_utcs
        super().__init__(
            f"{len(occurrence_time_utcs)} occurrence override(s) exist; "
            "clear them or pass resetOverrides=true to reschedule"
        )


# =============================================================================
# Venue-Related Exceptions
# =============================================================================


class VenueNotFoundException(Exception):
    """Raised when a venue is not found (by integer id or public id)."""

    venue_id: int | str

    def __init__(self, venue_id: int | str):
        self.venue_id = venue_id
        super().__init__(f"Venue {venue_id} not found")


class DefaultVenueExistsException(Exception):
    """Raised when trying to create a second default venue."""

    def __init__(self):
        super().__init__("A default venue already exists")


class CannotDeleteDefaultVenueException(Exception):
    """Raised when trying to delete the default venue."""

    def __init__(self):
        super().__init__("Cannot delete the default venue")


class VenueHasEventsException(Exception):
    """Raised when trying to hard delete a venue that has events."""

    venue_id: int
    event_count: int

    def __init__(self, venue_id: int, event_count: int):
        self.venue_id = venue_id
        self.event_count = event_count
        super().__init__(
            f"Venue {venue_id} has {event_count} events. Delete events first."
        )


class VenueIsDeletedException(Exception):
    """Raised when trying to restore an event whose venue is soft-deleted."""

    venue_id: int

    def __init__(self, venue_id: int):
        self.venue_id = venue_id
        super().__init__(f"Venue {venue_id} is deleted")


# =============================================================================
# Occurrence-Related Exceptions
# =============================================================================


class ScheduleNotFoundException(Exception):
    """Raised when a schedule id is not one of the event's schedules (programme R22d)."""

    event_id: int
    schedule_id: int

    def __init__(self, event_id: int, schedule_id: int):
        self.event_id = event_id
        self.schedule_id = schedule_id
        super().__init__(
            f"Schedule {schedule_id} is not a schedule of event {event_id}"
        )


class OccurrenceNotFoundException(Exception):
    """Raised when an occurrence is not found."""

    event_id: int
    occurrence_time_utc: int

    def __init__(self, event_id: int, occurrence_time_utc: int):
        self.event_id = event_id
        self.occurrence_time_utc = occurrence_time_utc
        super().__init__(f"Occurrence not found for event {event_id}")


class OrganizerNotFoundException(Exception):
    """Raised when an organizer is not found."""

    username: str

    def __init__(self, username: str):
        self.username = username
        super().__init__(f"Organizer {username} not found")


class StaleVersionException(Exception):
    """Raised when a mutation carries a version the event has moved past (#292).

    Carries what the client needs to tell the user who changed the event and
    when, before asking them to reload.
    """

    version: int
    updated_at: int | None
    updated_by: str | None

    def __init__(
        self,
        version: int,
        updated_at: int | None,
        updated_by: str | None,
        subject: str = "Event",
    ):
        self.version = version
        self.updated_at = updated_at
        self.updated_by = updated_by
        super().__init__(f"{subject} is at version {version}")


class StaleOccurrenceVersionException(StaleVersionException):
    """Raised when an occurrence change carries a version it has moved past (#430).

    An occurrence nobody has changed is at version 1 and has no author, so
    ``updated_at`` may be ``None``.
    """

    def __init__(self, version: int, updated_at: int | None, updated_by: str | None):
        super().__init__(version, updated_at, updated_by, subject="Occurrence")


class StaleMarketingVersionException(StaleVersionException):
    """Raised when a marketing write carries a version the block has moved
    past (#13).

    An event with no block is at version 1 and has no author, so
    ``updated_at`` may be ``None``.
    """

    def __init__(self, version: int, updated_at: int | None, updated_by: str | None):
        super().__init__(version, updated_at, updated_by, subject="Event marketing")


class CoachNotFoundException(Exception):
    """Raised when a named coach is not a user (#386)."""

    username: str

    def __init__(self, username: str):
        self.username = username
        super().__init__(f"Coach {username} not found")


class PastOccurrenceException(Exception):
    """Raised when trying to modify a past occurrence."""

    def __init__(self):
        super().__init__("Cannot modify past occurrence")


class CancelledOccurrenceException(Exception):
    """Raised when trying to modify a cancelled occurrence."""

    def __init__(self):
        super().__init__("Cannot modify cancelled occurrence")


class EffectiveTimeNotSessionBoundaryException(Exception):
    """Raised when a cancel time does not match any Camp occurrence start."""

    def __init__(self):
        super().__init__("Effective time must match a Camp occurrence start")


class CancellationLeadTimeViolatedException(Exception):
    """Raised when a Camp cancellation targets an occurrence within the lead time."""

    def __init__(self):
        super().__init__(
            "Cancellation must be at least 30 minutes before the occurrence"
        )


class EffectiveTimeInPastException(Exception):
    """Raised when a Camp cancellation targets an occurrence in the past."""

    def __init__(self):
        super().__init__("Cannot cancel an occurrence in the past")


class NothingToRescheduleException(Exception):
    """Raised when a reschedule request supplies no changes."""

    def __init__(self):
        super().__init__("At least one of start, duration, or venue must be set")


class PastRescheduleTimeException(Exception):
    """Raised when a reschedule targets a new start time in the past."""

    def __init__(self):
        super().__init__("New start time must be in the future")


class RescheduleLeadTimeViolatedException(Exception):
    """Raised when rescheduling an occurrence within the lead time."""

    def __init__(self):
        super().__init__("Reschedule must be at least 30 minutes before the occurrence")


class RangeTooLargeException(Exception):
    """Raised when date range is too large."""

    def __init__(self):
        super().__init__("Date range cannot exceed 1 year")


# =============================================================================
# Enrollment-Related Exceptions
# =============================================================================


class EnrollmentNotFoundException(Exception):
    """Raised when an enrollment is not found."""

    event_id: int
    membername: str

    def __init__(self, event_id: int, membername: str):
        self.event_id = event_id
        self.membername = membername
        super().__init__(f"Enrollment not found for {membername} in event {event_id}")


class AlreadyEnrolledException(Exception):
    """Raised when user already has an active enrollment."""

    membername: str

    def __init__(self, membername: str):
        self.membername = membername
        super().__init__(f"User {membername} already has active enrollment")


class EnrollmentTransitionException(Exception):
    """Raised when an invalid enrollment state transition is attempted."""

    action: str
    current_status: str

    def __init__(self, action: str, current_status: str):
        self.action = action
        self.current_status = current_status
        super().__init__(f"Cannot {action} with status '{current_status}'")


class EnrollmentStateConflictException(Exception):
    """Raised when an enrollment's state forbids the action (409 ``INVALID_STATE``).

    Distinct from ``InvalidStateException``, which the enrollment routes
    answer with 422: this one is a conflict with the row as it stands — a
    pending departure settlement, or a member who has already left.
    """

    message: str

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class EnrollmentTimeConflictException(Exception):
    """Raised when a user has an active enrollment in a time-conflicting event."""

    membername: str
    target_event_id: int
    conflicting_event_ids: list[int]

    def __init__(
        self,
        membername: str,
        target_event_id: int,
        conflicting_event_ids: list[int],
    ):
        self.membername = membername
        self.target_event_id = target_event_id
        self.conflicting_event_ids = conflicting_event_ids
        super().__init__(
            f"User {membername} has time conflicts with events {conflicting_event_ids}"
        )


# =============================================================================
# Attendance-Related Exceptions
# =============================================================================


class AttendanceNotFoundException(Exception):
    """Raised when an attendance record is not found."""

    event_id: int
    occurrence_time_utc: int
    membername: str

    def __init__(self, event_id: int, occurrence_time_utc: int, membername: str):
        self.event_id = event_id
        self.occurrence_time_utc = occurrence_time_utc
        self.membername = membername
        super().__init__(
            f"Attendance not found for {membername} at event {event_id}, occurrence {occurrence_time_utc}"
        )


class EditWindowClosedException(Exception):
    """Raised when the edit window has expired."""

    def __init__(self):
        super().__init__("Attendance edit window (15 days) has expired")


class LeaveWindowClosedException(Exception):
    """Raised when the leave declaration window has expired."""

    def __init__(self):
        super().__init__("Leave must be declared at least 2 hours before occurrence")


class AttendanceNotYetOpenException(Exception):
    """Raised when attendance marking is attempted before the open window."""

    def __init__(self):
        super().__init__(
            "Attendance marking opens 30 minutes before the occurrence start"
        )


class LeaveAlreadyDeclaredException(Exception):
    """Raised when leave has already been declared."""

    def __init__(self):
        super().__init__("Leave has already been declared for this occurrence")


class InvalidAttendanceStatusException(Exception):
    """Raised when an invalid attendance status is provided to mark_attendance."""

    status: str

    def __init__(self, status: str):
        self.status = status
        super().__init__(
            f"Invalid attendance status: '{status}'. Use the leave workflow for leave statuses."
        )


# =============================================================================
# Group-Related Exceptions
# =============================================================================


class GroupNotFoundException(Exception):
    """Raised when a group is not found."""

    group_id: int

    def __init__(self, group_id: int):
        self.group_id = group_id
        super().__init__(f"Group {group_id} not found")


class AlreadyMemberException(Exception):
    """Raised when user is already a member of the group."""

    membername: str
    group_id: int

    def __init__(self, membername: str, group_id: int):
        self.membername = membername
        self.group_id = group_id
        super().__init__(f"User {membername} is already a member of group {group_id}")


class AutoGroupModificationException(Exception):
    """Raised when trying to manually modify auto group members."""

    group_id: int

    def __init__(self, group_id: int):
        self.group_id = group_id
        super().__init__(f"Cannot manually modify members of auto group {group_id}")


class MembersExistException(Exception):
    """Raised when trying to convert a manual group with members to auto."""

    group_id: int
    member_count: int

    def __init__(self, group_id: int, member_count: int):
        self.group_id = group_id
        self.member_count = member_count
        super().__init__(
            f"Cannot set auto-criteria on group {group_id}: "
            f"{member_count} manual member(s) exist. Remove all members first."
        )


class NotEligibleException(Exception):
    """Raised when a user does not satisfy a semi-auto group's eligibility criteria."""

    membername: str
    group_id: int

    def __init__(self, membername: str, group_id: int):
        self.membername = membername
        self.group_id = group_id
        super().__init__(
            f"User {membername} does not satisfy the eligibility criteria for group {group_id}"
        )


class MembersIneligibleException(Exception):
    """Raised when converting manual to semi-auto and existing members fail criteria."""

    group_id: int
    membernames: list[str]

    def __init__(self, group_id: int, membernames: list[str]):
        self.group_id = group_id
        self.membernames = membernames
        super().__init__(
            f"Cannot convert group {group_id} to semi-auto: "
            f"existing members do not satisfy the criteria: {membernames}"
        )


class AutoGroupNotJoinableException(Exception):
    """Raised when attempting to query joinability or create a request for an auto group."""

    group_id: int

    def __init__(self, group_id: int):
        self.group_id = group_id
        super().__init__(
            f"Auto group {group_id} has no joinable list; membership is computed automatically"
        )


class JoinRequestPendingException(Exception):
    """Raised when a pending join request already exists."""

    membername: str
    group_id: int

    def __init__(self, membername: str, group_id: int):
        self.membername = membername
        self.group_id = group_id
        super().__init__(
            f"User {membername} already has a pending join request for group {group_id}"
        )


class JoinRequestNotFoundException(Exception):
    """Raised when a join request is not found."""

    request_id: int

    def __init__(self, request_id: int):
        self.request_id = request_id
        super().__init__(f"Join request {request_id} not found")


class JoinRequestNotPendingException(Exception):
    """Raised when an action requires a pending request but the row is in another state."""

    request_id: int
    status: str

    def __init__(self, request_id: int, status: str):
        self.request_id = request_id
        self.status = status
        super().__init__(
            f"Join request {request_id} cannot be acted on (status={status})"
        )


class MemberNotFoundException(Exception):
    """Raised when member is not found in the group."""

    membername: str
    group_id: int

    def __init__(self, membername: str, group_id: int):
        self.membername = membername
        self.group_id = group_id
        super().__init__(f"User {membername} is not a member of group {group_id}")


# =============================================================================
# Notification-Related Exceptions
# =============================================================================


# =============================================================================
# Upload-Related Exceptions
# =============================================================================


class InvalidMediaTypeException(Exception):
    """Raised when the uploaded file has an unsupported media type."""

    mime_type: str

    def __init__(self, mime_type: str):
        self.mime_type = mime_type
        super().__init__(f"Unsupported media type: {mime_type}")


class EncryptionNotConfiguredException(Exception):
    """Raised when an encryption op is attempted but no usable key is configured.

    Surfaced as HTTP 503 ``ENCRYPTION_NOT_CONFIGURED``.
    """

    def __init__(self, message: str = "Server encryption key is not configured"):
        super().__init__(message)


class EncryptedFileTooLargeException(Exception):
    """Raised when ``encrypt=true`` is combined with a file over the encrypted cap.

    Distinct from ``FileTooLargeException`` so clients can offer the user the
    option of uploading without encryption when only this cap is exceeded.
    Surfaced as HTTP 422 ``ENCRYPTED_FILE_TOO_LARGE``.
    """

    max_size_mb: int

    def __init__(self, max_size_mb: int):
        self.max_size_mb = max_size_mb
        super().__init__(f"Encrypted upload exceeds {max_size_mb} MB cap")


class EncryptionNotSupportedForVideoException(Exception):
    """Raised when ``encrypt=true`` is combined with a video upload.

    Surfaced as HTTP 422 ``ENCRYPTION_NOT_SUPPORTED_FOR_VIDEO``. Video
    encryption is deferred because HTTP Range / segmented-AEAD adds
    significant complexity (see issue #150 scope notes).
    """

    def __init__(self):
        super().__init__("Encryption is not supported for video uploads")


class DecryptionFailedException(Exception):
    """Raised when AES-GCM authentication fails on a stored upload.

    Indicates tampered ciphertext, wrong key, or corrupted metadata.
    Surfaced as HTTP 500 ``DECRYPTION_FAILED``.
    """

    def __init__(self, detail: str = ""):
        super().__init__(f"Decryption failed{f': {detail}' if detail else ''}")


class InvalidAccessRolesException(Exception):
    """Raised when the access_roles array on an upload is invalid.

    Surfaced as HTTP 422 INVALID_ACCESS_ROLES.
    """

    message: str

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class FileTooLargeException(Exception):
    """Raised when the uploaded file exceeds the size limit."""

    max_size_mb: int
    media_type: str | None

    def __init__(self, max_size_mb: int, media_type: str | None = None):
        self.max_size_mb = max_size_mb
        self.media_type = media_type
        if media_type:
            super().__init__(
                f"{media_type.capitalize()} file exceeds maximum size of {max_size_mb} MB",
            )
        else:
            super().__init__(f"File exceeds maximum size of {max_size_mb} MB")


# =============================================================================
# Notification-Related Exceptions
# =============================================================================


class NotificationNotFoundException(Exception):
    """Raised when a notification is not found."""

    notification_id: int

    def __init__(self, notification_id: int):
        self.notification_id = notification_id
        super().__init__(f"Notification {notification_id} not found")


class BroadcastNotFoundException(Exception):
    """Raised when a broadcast is not found."""

    broadcast_id: int

    def __init__(self, broadcast_id: int):
        self.broadcast_id = broadcast_id
        super().__init__(f"Broadcast {broadcast_id} not found")


class InvalidAudienceSelectorException(Exception):
    """Raised when a broadcast's audience_selector is malformed."""

    def __init__(self, message: str):
        super().__init__(message)


class SystemPreferenceNotFoundException(Exception):
    """Raised when a system_preferences key is absent and has no default."""

    key: str

    def __init__(self, key: str):
        self.key = key
        super().__init__(f"System preference '{key}' not found")


class MediaNotFoundException(Exception):
    """Raised when a /v1/media record (#161) is not found."""

    def __init__(self, ident: str | int):
        self.ident = ident
        super().__init__(f"Media not found: {ident}")


class MediaConversionFailedException(Exception):
    """Raised when an uploaded image or PDF cannot be converted (#521)."""

    def __init__(self, media_type: str, reason: str):
        self.media_type = media_type
        self.reason = reason
        super().__init__(f"The {media_type} could not be processed: {reason}")


class MediaFileMissingException(Exception):
    """Raised when a media row's artifact is absent from disk (#285).

    Hit by ``encrypt_in_place`` when the plaintext file it is meant to encrypt
    cannot be found. Surfaced as HTTP 404 ``FILE_NOT_FOUND``.
    """

    def __init__(self, ident: str | int):
        self.ident = ident
        super().__init__(f"Media artifact missing on disk: {ident}")


class MediaLinkNotFoundException(Exception):
    """Raised when a (owner, tag, media_uuid) link is not found (#162)."""

    def __init__(self, owner_type: str, owner_id: str | int, tag: str, media_uuid: str):
        self.owner_type = owner_type
        self.owner_id = owner_id
        self.tag = tag
        self.media_uuid = media_uuid
        super().__init__(
            f"Media link not found: {owner_type}={owner_id} tag={tag} media={media_uuid}"
        )


class OwnerDeletedException(Exception):
    """Raised when writing a link on a soft-deleted owner (#517)."""

    def __init__(self, owner_type: str, owner_id: object):
        self.owner_type = owner_type
        self.owner_id = owner_id
        super().__init__(
            f"{owner_type} {owner_id} is deleted; its media links are read-only"
        )


class MediaLinkExistsException(Exception):
    """Raised on duplicate (owner, tag, media_uuid) link insert (#162)."""

    def __init__(self, owner_type: str, owner_id: str | int, tag: str, media_uuid: str):
        self.owner_type = owner_type
        self.owner_id = owner_id
        self.tag = tag
        self.media_uuid = media_uuid
        super().__init__(
            f"Media link already exists: {owner_type}={owner_id} tag={tag} media={media_uuid}"
        )


class MediaLinkTagFullException(Exception):
    """Raised when (owner, tag) reaches MEDIA_MAX_LINKS_PER_OWNER_TAG (#162)."""

    def __init__(self, owner_type: str, owner_id: str | int, tag: str, limit: int):
        self.owner_type = owner_type
        self.owner_id = owner_id
        self.tag = tag
        self.limit = limit
        super().__init__(
            f"Tag {tag} on {owner_type}={owner_id} is full (limit {limit})"
        )


class MediaLinkTooManyTagsException(Exception):
    """Raised when an owner reaches MEDIA_MAX_TAGS_PER_OWNER distinct tags (#162)."""

    def __init__(self, owner_type: str, owner_id: str | int, limit: int):
        self.owner_type = owner_type
        self.owner_id = owner_id
        self.limit = limit
        super().__init__(
            f"{owner_type}={owner_id} has reached the maximum {limit} distinct media tags"
        )


class MediaInUseException(Exception):
    """Raised when media soft-delete is attempted while any link references it (#162)."""

    def __init__(self, media_uuid: str, links: list[dict]):
        self.media_uuid = media_uuid
        self.links = links
        super().__init__(f"Media {media_uuid} is in use by {len(links)} link(s)")


class IdentityDocumentRequiredException(Exception):
    """Raised when ``submit-for-review`` is called by a user with no live
    media link tagged ``identity_document`` (#142). Surfaced as HTTP 422
    ``IDENTITY_DOCUMENT_REQUIRED``."""

    username: str

    def __init__(self, username: str):
        self.username = username
        super().__init__(
            f"User {username} must upload an identity document "
            f"before submitting for review"
        )


class CreditSystemDisabledException(Exception):
    """Raised when a credit operation is attempted on a deployment that does
    not run on credits (#294). Surfaced as HTTP 503 ``CREDIT_SYSTEM_DISABLED``.

    The endpoints stay registered so that the published API does not vary with
    deployment configuration; only the answer does. See R92-R98."""

    def __init__(self) -> None:
        super().__init__("The credit system is not enabled on this deployment")


class CreditAccountNotFoundException(Exception):
    """Raised when no credit account carries the given code (#294)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"No credit account with id {code}")


class CreditNotApplicableException(Exception):
    """Raised when credit is attached to anything but a programme (#294, R6).

    Camps and one-off events are prepaid or free and never reach the credit
    subsystem."""

    def __init__(self, event_id: int, event_type: str):
        self.event_id = event_id
        self.event_type = event_type
        super().__init__(
            f"Event {event_id} is a {event_type}; only programmes consume credit"
        )


class SuperAdminCannotHoldCreditException(Exception):
    """Raised when credit would be granted to the super admin (#519, R18b)."""

    def __init__(self, username: str):
        self.username = username
        super().__init__(f"{username} is the super admin and cannot hold credit")


class InvalidCreditAmountException(Exception):
    """Raised when a credit amount is not a positive whole number (#294, R19)."""

    def __init__(self, credits: int):
        self.credits = credits
        super().__init__(
            f"Credit amount must be a positive whole number, got {credits}"
        )


class InvalidValidityWindowException(Exception):
    """Raised when a validity window ends before it starts, is wholly in the
    past, or an extension would move the end date backwards (#294, R23, R62)."""

    def __init__(self, message: str):
        super().__init__(message)


class InvalidOccurrenceTimeException(Exception):
    """Raised when a time is not an occurrence of the event's schedule (#470)."""

    def __init__(self, event_id: int, occurrence_time_utc: int):
        self.event_id = event_id
        self.occurrence_time_utc = occurrence_time_utc
        super().__init__(
            f"{occurrence_time_utc} is not an occurrence of event {event_id}"
        )


class InsufficientCreditException(Exception):
    """Raised when a member has no usable credit for a programme (#294, R35, R41)."""

    def __init__(self, membername: str, event_id: int, required: int):
        self.membername = membername
        self.event_id = event_id
        self.required = required
        super().__init__(
            f"User '{membername}' has no usable credit for event {event_id} "
            f"({required} required)"
        )


class InsufficientBalanceException(Exception):
    """Raised when a reversal exceeds what remains unspent (#294, R59)."""

    def __init__(self, code: str, balance: int, requested: int):
        self.code = code
        self.balance = balance
        self.requested = requested
        super().__init__(
            f"Account {code} holds {balance} credits; cannot reverse {requested}"
        )


class CreditAccountClosedException(Exception):
    """Raised when an operation targets an account already drained and closed (#294)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Credit account {code} is closed")


class CreditDispositionRequiredException(Exception):
    """Raised when a member holding programme credit is removed or has their
    withdrawal approved without the admin saying what happens to it (#294, R71).

    Nothing is converted automatically, so the balance would otherwise be
    stranded in an account bound to a programme the member has left."""

    def __init__(self, membername: str, event_id: int, balance: int):
        self.membername = membername
        self.event_id = event_id
        self.balance = balance
        super().__init__(
            f"User '{membername}' holds {balance} credits bound to event "
            f"{event_id}; supply a credit disposition to remove them"
        )


class CreditDispositionNotApplicableException(Exception):
    """Raised when a credit disposition is supplied to a deployment that does
    not run on credits (#294, R98).

    Rejected rather than silently ignored: a client sending it has
    misunderstood the deployment."""

    def __init__(self) -> None:
        super().__init__(
            "The credit system is not enabled on this deployment; "
            "a credit disposition cannot be applied"
        )


class EvaluationNotFoundException(Exception):
    """Raised when an evaluation is not found."""

    evaluation_id: int

    def __init__(self, evaluation_id: int):
        self.evaluation_id = evaluation_id
        super().__init__(f"Evaluation {evaluation_id} not found")


class EvaluationTemplateNotFoundException(Exception):
    """Raised when an evaluation template is not found."""

    template_id: int

    def __init__(self, template_id: int):
        self.template_id = template_id
        super().__init__(f"Evaluation template {template_id} not found")


class EvaluationTransitionException(Exception):
    """Raised when a lifecycle move is not one of the four permitted ones."""

    current_status: str
    action: str

    def __init__(self, current_status: str, action: str):
        self.current_status = current_status
        self.action = action
        super().__init__(f"Cannot {action} an evaluation in status '{current_status}'")


class EvaluationNotEligibleException(Exception):
    """Raised when author or subject does not satisfy the scope's eligibility."""

    reason: str

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class EvaluationAnswerInvalidException(Exception):
    """Raised when an answer does not fit its item (R10)."""

    reason: str

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class EvaluationIncompleteException(Exception):
    """Raised when saving a draft that lacks required answers or notes (R15)."""

    item_ids: list[int]

    def __init__(self, item_ids: list[int]):
        self.item_ids = item_ids
        super().__init__(f"Items {item_ids} need an answer or a coach note")


class EvaluationLayoutInvalidException(Exception):
    """Raised when a layout does not place every item of its template once (R12c)."""

    reason: str

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class EvaluationItemNotFoundException(Exception):
    """Raised when a template item is absent, or not of the template named."""

    item_id: int

    def __init__(self, item_id: int):
        self.item_id = item_id
        super().__init__(f"Template item {item_id} not found")


class EvaluationItemTypeFixedException(Exception):
    """Raised when a replacement would change an item's type (R26a)."""

    item_id: int

    def __init__(self, item_id: int):
        self.item_id = item_id
        super().__init__(f"Item {item_id} keeps its type")


class EvaluationOriginMismatchException(Exception):
    """Raised when a copy's type or answer domain differs from its origin's (R12b)."""

    origin_item_id: int

    def __init__(self, origin_item_id: int):
        self.origin_item_id = origin_item_id
        super().__init__(
            f"A copy of item {origin_item_id} keeps its type and answer domain"
        )


class EvaluationEvidenceInvalidException(Exception):
    """Raised when evidence names no evidence-taking question, or is not a file kind it allows."""

    reason: str

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class EvaluationTemplateInUseException(Exception):
    """Raised when deleting a template that evaluations are validated against."""

    template_id: int
    count: int

    def __init__(self, template_id: int, count: int):
        self.template_id = template_id
        self.count = count
        super().__init__(
            f"Template {template_id} is referenced by {count} evaluation(s)"
        )


class EvaluationTemplateNameTakenException(Exception):
    """Raised when a live template already holds a name (R49a)."""

    name: str

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"A live template is already named '{name}'")


class EvaluationPeriodInFutureException(Exception):
    """Raised when a review period ends after the server's clock (R4)."""

    period_end_utc: int

    def __init__(self, period_end_utc: int):
        self.period_end_utc = period_end_utc
        super().__init__("A review period must end in the past")


class EvaluationDuplicateException(Exception):
    """Raised when a coach would hold two reviews of one member, template and period (R7).

    The message names neither the twin nor its id: before publication an
    evaluation exists only for its owner (R38a), and a transfer's caller may
    not be the receiving coach.
    """

    def __init__(self) -> None:
        super().__init__(
            "The coach already has a review of this member on this template "
            "over this period"
        )


class NotACoachException(Exception):
    """Raised when a staff-listing row is requested for a user without the coach role."""

    username: str

    def __init__(self, username: str):
        self.username = username
        super().__init__(f"User {username} is not a coach")


class InvalidPreferenceValueException(Exception):
    """Raised when a system preference value has a shape its consumer cannot use (#296)."""

    key: str

    def __init__(self, key: str, reason: str):
        self.key = key
        super().__init__(f"Preference '{key}' {reason}")


class SiteMediaNotPublicException(Exception):
    """Raised when a site_media slot names media the public site could not fetch (#296)."""

    purpose: str
    media_uuid: str

    def __init__(self, purpose: str, media_uuid: str):
        self.purpose = purpose
        self.media_uuid = media_uuid
        super().__init__(
            f"site_media slot '{purpose}' names {media_uuid}, which is not live public media"
        )


class EventMarketingNotFoundException(Exception):
    """Raised when an event has no extended marketing block (#410)."""

    event_id: int | str

    def __init__(self, event_id: int | str):
        self.event_id = event_id
        super().__init__(f"Event {event_id} has no marketing block")


class InvalidFormTokenException(Exception):
    """Raised when a public form token is forged, malformed or expired (#407)."""

    def __init__(self) -> None:
        super().__init__("The form token is invalid or expired")


class InquiryNotFoundException(Exception):
    """Raised when an inquiry id is unknown (#407)."""

    inquiry_id: int

    def __init__(self, inquiry_id: int):
        self.inquiry_id = inquiry_id
        super().__init__(f"Inquiry {inquiry_id} not found")
