"""Canonical catalogue of audit-log action identifiers.

Every value written to ``AuditLog.action`` must be a member of this enum. The
enum is a :class:`enum.StrEnum`, so each member *is* its wire string — the value
stored in the (``Text``) ``action`` column is unchanged from the historical
string literals, and no migration is required.

Centralising the vocabulary here makes the action set enumerable (for building
the human-readable summary registry and for the test that asserts every action
has a template), greppable, and type-checked at every call site.

Member values MUST equal the historical literal strings so that rows written by
earlier versions still resolve to a known member.
"""

from enum import StrEnum

SYSTEM_ACTOR = "system"
"""The actor named on an audit row when the server acts on its own: the
credit sweep, or a removal that follows from a rule (credit R52a)."""


class AuditAction(StrEnum):
    """Every distinct audit-log action recorded by the server."""

    # --- Authentication (routers/auth.py) ---
    REGISTER = "register"
    LOGIN = "login"
    LOGOUT = "logout"
    PASSWORD_CHANGED = "password_changed"
    PASSWORD_RESET_REQUESTED = "password_reset_requested"
    TOKEN_REFRESHED = "token_refreshed"

    # --- Admin (routers/admin.py) ---
    ADMIN_PASSWORD_RESET = "admin_password_reset"

    # --- Users (routers/users.py) ---
    SUBMIT_FOR_REVIEW = "submit_for_review"
    UPDATE_USER = "update_user"
    APPROVE_USER = "approve_user"
    BLOCK_USER = "block_user"
    RECONSIDER_USER = "reconsider_user"
    REAPPLY = "reapply"
    UNBLOCK_USER = "unblock_user"
    ASSIGN_ROLE = "assign_role"
    REMOVE_ROLE = "remove_role"
    CREATE_USER = "create_user"
    DELETE_USER = "delete_user"
    HARD_DELETE_USER = "hard_delete_user"
    RESTORE_USER = "restore_user"
    MARK_LEFT = "mark_left"
    REACTIVATE_USER = "reactivate_user"
    TRANSFER_SUPERADMIN = "transfer_superadmin"

    # --- Events (routers/events.py) ---
    CREATE_EVENT = "create_event"
    UPDATE_EVENT = "update_event"
    CORRECT_EVENT = "correct_event"
    SPLIT_EVENT = "split_event"
    CANCEL_EVENT = "cancel_event"
    UNDO_CANCEL_EVENT = "undo_cancel_event"
    RESCHEDULE_EVENT = "reschedule_event"
    SOFT_DELETE_EVENT = "soft_delete_event"
    RESTORE_EVENT = "restore_event"
    WIPEOUT_EVENT = "wipeout_event"

    # --- Enrollment (routers/events.py, routers/myevents.py) ---
    ENROLLMENT_INVITED = "enrollment_invited"
    ENROLLMENT_ASSIGNED = "enrollment_assigned"
    ENROLLMENT_ACCEPTED = "enrollment_accepted"
    ENROLLMENT_DECLINED = "enrollment_declined"
    ENROLLMENT_REQUESTED = "enrollment_requested"
    ENROLLMENT_REJECTED = "enrollment_rejected"
    ENROLLMENT_REMOVED = "enrollment_removed"
    WITHDRAWAL_REQUESTED = "withdrawal_requested"
    WITHDRAWAL_CANCELLED = "withdrawal_cancelled"
    WITHDRAWAL_APPROVED = "withdrawal_approved"
    WITHDRAWAL_REJECTED = "withdrawal_rejected"

    # --- Occurrences (routers/occurrences.py, routers/myevents.py) ---
    RESCHEDULE_OCCURRENCE = "reschedule_occurrence"
    CANCEL_OCCURRENCE = "cancel_occurrence"
    UNDO_CANCEL_OCCURRENCE = "undo_cancel_occurrence"
    ATTENDANCE_MARKED = "attendance_marked"
    ATTENDANCE_CLEARED = "attendance_cleared"
    LEAVE_REQUESTED = "leave_requested"
    LEAVE_CANCELLED = "leave_cancelled"
    LEAVE_APPROVED = "leave_approved"
    LEAVE_REJECTED = "leave_rejected"

    # --- Groups (routers/groups.py, routers/mygroups.py) ---
    CREATE_GROUP = "create_group"
    UPDATE_GROUP = "update_group"
    SOFT_DELETE_GROUP = "soft_delete_group"
    RESTORE_GROUP = "restore_group"
    WIPEOUT_GROUP = "wipeout_group"
    ADD_GROUP_MEMBER = "add_group_member"
    ADD_GROUP_MEMBERS_BULK = "add_group_members_bulk"
    REMOVE_GROUP_MEMBER = "remove_group_member"
    APPROVE_GROUP_JOIN_REQUEST = "approve_group_join_request"
    REJECT_GROUP_JOIN_REQUEST = "reject_group_join_request"
    CREATE_GROUP_JOIN_REQUEST = "create_group_join_request"
    CANCEL_GROUP_JOIN_REQUEST = "cancel_group_join_request"

    # --- Inquiries (routers/admin_inquiries.py) ---
    HANDLE_INQUIRY = "handle_inquiry"
    DELETE_INQUIRY = "delete_inquiry"

    # --- Event marketing (routers/event_marketing.py) ---
    UPDATE_EVENT_MARKETING = "update_event_marketing"
    DELETE_EVENT_MARKETING = "delete_event_marketing"

    # --- System preferences (routers/admin_preferences.py) ---
    UPDATE_SYSTEM_PREFERENCE = "update_system_preference"

    # --- Public staff listing (routers/admin_staff_listing.py) ---
    UPDATE_STAFF_LISTING = "update_staff_listing"
    DELETE_STAFF_LISTING = "delete_staff_listing"

    # --- Venues (routers/venues.py) ---
    CREATE_VENUE = "create_venue"
    UPDATE_VENUE = "update_venue"
    RESTORE_VENUE = "restore_venue"
    SOFT_DELETE_VENUE = "soft_delete_venue"
    WIPEOUT_VENUE = "wipeout_venue"

    # --- Media (routers/media.py) ---
    UPLOAD_MEDIA_V2 = "upload_media_v2"
    UPDATE_MEDIA_V2 = "update_media_v2"
    SOFT_DELETE_MEDIA_V2 = "soft_delete_media_v2"
    RESTORE_MEDIA_V2 = "restore_media_v2"
    HARD_DELETE_MEDIA_V2 = "hard_delete_media_v2"
    ENCRYPT_MEDIA_V2 = "encrypt_media_v2"

    # --- Media links (routers/media_links.py) ---
    # One member per (verb, owner_type) pair; owner_type is one of
    # user / event / group / venue. Resolve dynamically with
    # ``AuditAction(f"{verb}_{owner_type}_media_link")``.
    CREATE_USER_MEDIA_LINK = "create_user_media_link"
    CREATE_EVENT_MEDIA_LINK = "create_event_media_link"
    CREATE_GROUP_MEDIA_LINK = "create_group_media_link"
    CREATE_VENUE_MEDIA_LINK = "create_venue_media_link"
    UPDATE_USER_MEDIA_LINK = "update_user_media_link"
    UPDATE_EVENT_MEDIA_LINK = "update_event_media_link"
    UPDATE_GROUP_MEDIA_LINK = "update_group_media_link"
    UPDATE_VENUE_MEDIA_LINK = "update_venue_media_link"
    DELETE_USER_MEDIA_LINK = "delete_user_media_link"
    DELETE_EVENT_MEDIA_LINK = "delete_event_media_link"
    DELETE_GROUP_MEDIA_LINK = "delete_group_media_link"
    DELETE_VENUE_MEDIA_LINK = "delete_venue_media_link"
    DELETE_USER_MEDIA_TAG = "delete_user_media_tag"
    DELETE_EVENT_MEDIA_TAG = "delete_event_media_tag"
    DELETE_GROUP_MEDIA_TAG = "delete_group_media_tag"
    DELETE_VENUE_MEDIA_TAG = "delete_venue_media_tag"

    # --- Broadcasts (routers/broadcasts.py) ---
    CREATE_BROADCAST = "create_broadcast"
    REVOKE_BROADCAST = "revoke_broadcast"

    # --- Notifications (routers/notifications.py) ---
    CREATE_NOTIFICATION = "create_notification"
    DELETE_NOTIFICATION = "delete_notification"

    # --- Credit system (routers/credits.py) ---
    OPEN_CREDIT_ACCOUNT = "open_credit_account"
    EXTEND_CREDIT_ACCOUNT = "extend_credit_account"
    REVERSE_CREDIT_GRANT = "reverse_credit_grant"
    TRANSFER_CREDIT_ACCOUNT = "transfer_credit_account"
    REOPEN_CREDIT_ACCOUNT = "reopen_credit_account"
    CREDIT_DEDUCTED = "credit_deducted"
    CREDIT_REFUNDED = "credit_refunded"
    CREDIT_RELEASED = "credit_released"

    # --- Lifecycle verbs (routers/event_lifecycle.py) ---
    TERMINATE_EVENT = "terminate_event"
    EXTEND_EVENT = "extend_event"
    DROP_EVENT = "drop_event"
    REINSTATE_EVENT = "reinstate_event"

    # --- Evaluations (routers/evaluations.py, routers/evaluation_templates.py) ---
    CREATE_EVALUATION = "create_evaluation"
    UPDATE_EVALUATION = "update_evaluation"
    SAVE_EVALUATION = "save_evaluation"
    PUBLISH_EVALUATION = "publish_evaluation"
    UNPUBLISH_EVALUATION = "unpublish_evaluation"
    REVERT_EVALUATION = "revert_evaluation"
    TRANSFER_EVALUATION = "transfer_evaluation"
    DELETE_EVALUATION = "delete_evaluation"
    RESTORE_EVALUATION = "restore_evaluation"
    HARD_DELETE_EVALUATION = "hard_delete_evaluation"
    CREATE_EVALUATION_TEMPLATE = "create_evaluation_template"
    UPDATE_EVALUATION_TEMPLATE = "update_evaluation_template"
    DELETE_EVALUATION_TEMPLATE = "delete_evaluation_template"
    RESTORE_EVALUATION_TEMPLATE = "restore_evaluation_template"
    HARD_DELETE_EVALUATION_TEMPLATE = "hard_delete_evaluation_template"
    CREATE_EVALUATION_MEDIA = "create_evaluation_media"
    UPDATE_EVALUATION_MEDIA = "update_evaluation_media"
    DELETE_EVALUATION_MEDIA = "delete_evaluation_media"
    DELETE_EVALUATION_MEDIA_TAG = "delete_evaluation_media_tag"
