"""Fill the membership date of members approved before it existed (#92, users R68b).

The only record of an approval the database keeps is a review request
closed as approved; an approval with no request behind it left no trace.
So each user with such a request gets the club's calendar day of the
earliest one, and everyone else stays blank: bootstrapped admins, older
imports, members approved without a review request, users not yet
approved. A date already set is never touched.

The club's time zone is read from ``CLUB_TIMEZONE`` or the
``club_timezone`` secrets file rather than through ``Settings`` (see
``age_eligibility``), so alembic needs no more of the environment than it
must.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ...club_calendar import club_day
from .age_eligibility import club_timezone_from_env

# ``services.user_review.RESOLUTION_APPROVED``; not imported, since loading
# the services package reads the server's settings.
RESOLUTION_APPROVED = "approved"


def fill_membership_dates(conn: Connection, tz_name: str | None = None) -> int:
    """Set each blank membership date from the earliest approving review
    request; return how many users were filled."""
    tz = tz_name or club_timezone_from_env()
    rows = conn.execute(
        text(
            "SELECT r.username, MIN(r.resolved_at) "
            "FROM user_review_requests r "
            "JOIN users u ON u.username = r.username "
            "WHERE r.resolution = :approved AND r.resolved_at IS NOT NULL "
            "AND u.membership_date IS NULL "
            "GROUP BY r.username"
        ),
        {"approved": RESOLUTION_APPROVED},
    ).all()
    for username, approved_at in rows:
        _ = conn.execute(
            text("UPDATE users SET membership_date = :day WHERE username = :username"),
            {"day": club_day(int(approved_at), tz), "username": username},
        )
    return len(rows)
