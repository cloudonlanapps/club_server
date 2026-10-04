from .admin import router as admin_router
from .admin_preferences import router as admin_preferences_router
from .auth import router as auth_router
from .users import router as users_router
from .groups import router as groups_router
from .venues import router as venues_router
from .events import router as events_router
from .event_lifecycle import router as event_lifecycle_router
from .event_enrollments import router as event_enrollments_router
from .myevents import router as myevents_router
from .mygroups import router as mygroups_router
from .occurrences import router as occurrences_router
from .notifications import router as notifications_router
from .broadcasts import router as broadcasts_router
from .public import router as public_router
from .public_venues import router as public_venues_router
from .public_events import router as public_events_router
from .public_club_info import router as public_club_info_router
from .event_marketing import router as event_marketing_router
from .public_event_marketing import router as public_event_marketing_router
from .public_inquiries import router as public_inquiries_router
from .admin_inquiries import router as admin_inquiries_router
from .admin_staff_listing import router as admin_staff_listing_router
from .media import router as media_router
from .media_links import (
    event_media_router,
    group_media_router,
    user_media_router,
    venue_media_router,
)
from .audit_log import router as audit_log_router
from .capabilities import router as capabilities_router
from .credits import router as credits_router
from .credits_query import router as credits_query_router
from .credit_roster import router as credit_roster_router
from .mycredits import router as mycredits_router
from .evaluations import router as evaluations_router
from .evaluation_lifecycle import router as evaluation_lifecycle_router
from .evaluation_templates import router as evaluation_templates_router
from .evaluation_template_items import router as evaluation_template_items_router
from .evaluation_answers import router as evaluation_answers_router
from .myevaluations import router as myevaluations_router
from .evaluation_media import router as evaluation_media_router

__all__ = [
    "admin_router",
    "admin_preferences_router",
    "auth_router",
    "users_router",
    "groups_router",
    "venues_router",
    "events_router",
    "event_lifecycle_router",
    "event_enrollments_router",
    "myevents_router",
    "mygroups_router",
    "occurrences_router",
    "notifications_router",
    "broadcasts_router",
    "public_router",
    "public_venues_router",
    "public_events_router",
    "public_club_info_router",
    "event_marketing_router",
    "public_event_marketing_router",
    "public_inquiries_router",
    "admin_inquiries_router",
    "admin_staff_listing_router",
    "media_router",
    "user_media_router",
    "event_media_router",
    "group_media_router",
    "venue_media_router",
    "audit_log_router",
    "capabilities_router",
    "credits_router",
    "credits_query_router",
    "credit_roster_router",
    "mycredits_router",
    "evaluations_router",
    "evaluation_lifecycle_router",
    "evaluation_templates_router",
    "evaluation_template_items_router",
    "evaluation_answers_router",
    "myevaluations_router",
    "evaluation_media_router",
]
