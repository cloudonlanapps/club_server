"""A member's membership date (#92, users R67–R68b).

A calendar date, carried as a UTC midnight like a date of birth. Only an
admin edits it; approval and admin creation fill it in when blank, and the
migration that adds it fills it for members approved through a review
request.
"""

from datetime import date
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.club_calendar import club_day, club_today, date_to_day
from club_server.db.backfills import age_eligibility
from club_server.db.backfills.age_eligibility import club_timezone_from_env
from club_server.db.backfills.membership_date import fill_membership_dates
from club_server.db.models.user import User, UserStatus
from club_server.db.models.user_review_request import UserReviewRequest

from .extended_profile_helpers import MIDNIGHT_2024_06_01, auth, patch_user, private
from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
    create_regular_admin_user,
    create_user_with_status,
)

MIDNIGHT_2000 = 946684800000


@pytest.mark.requirement("users:R67")
@pytest.mark.asyncio
@pytest.mark.parametrize("admin_kind", ["super", "regular"])
async def test_should_store_membership_date_when_admin_sets_it(
    client: AsyncClient, db_session: AsyncSession, admin_kind: str
):
    super_admin = await create_admin_user(db_session)
    admin = (
        super_admin
        if admin_kind == "super"
        else await create_regular_admin_user(db_session, "ravi")
    )
    member = await create_member_user(db_session, "mira")

    response = await patch_user(
        client, admin, "mira", {"membershipDateUtc": MIDNIGHT_2024_06_01}
    )

    assert response.status_code == 200, response.text
    assert response.json()["membershipDateUtc"] == MIDNIGHT_2024_06_01
    assert (await private(client, member, "mira"))[
        "membershipDateUtc"
    ] == MIDNIGHT_2024_06_01


@pytest.mark.requirement("users:R67")
@pytest.mark.asyncio
async def test_should_clear_membership_date_when_admin_sends_null(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    member = await create_member_user(db_session, "mira")
    first = await patch_user(
        client, admin, "mira", {"membershipDateUtc": MIDNIGHT_2024_06_01}
    )
    assert first.status_code == 200, first.text

    cleared = await patch_user(client, admin, "mira", {"membershipDateUtc": None})

    assert cleared.status_code == 200, cleared.text
    assert (await private(client, member, "mira"))["membershipDateUtc"] is None


@pytest.mark.requirement("users:R67")
@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["member", "coach"])
async def test_should_refuse_membership_date_when_user_edits_their_own(
    client: AsyncClient, db_session: AsyncSession, role: str
):
    admin = await create_admin_user(db_session)
    token = (
        await create_member_user(db_session, "mira")
        if role == "member"
        else await create_coach_user(db_session, "mira")
    )
    await db_session.commit()

    response = await patch_user(
        client,
        token,
        "mira",
        {"membershipDateUtc": MIDNIGHT_2024_06_01, "clubDistrict": "Pune"},
    )

    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "PROTECTED_FIELDS"
    assert detail["fields"] == ["membershipDateUtc"]
    after = await private(client, admin, "mira")
    assert after["membershipDateUtc"] is None
    assert after["clubDistrict"] is None


@pytest.mark.requirement("users:R67")
@pytest.mark.asyncio
async def test_should_refuse_membership_date_when_not_a_utc_midnight(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "mira")
    await db_session.commit()

    response = await patch_user(
        client, admin, "mira", {"membershipDateUtc": MIDNIGHT_2024_06_01 + 3_600_000}
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["field"] == "membershipDateUtc"
    assert (await private(client, admin, "mira"))["membershipDateUtc"] is None


@pytest.mark.requirement("users:R68")
@pytest.mark.asyncio
async def test_should_set_membership_date_to_approval_day_when_blank(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    pending = await create_user_with_status(db_session, "pat", UserStatus.pending)
    before = club_today()

    approved = await client.post("/v1/users/by_id/pat/approve", headers=auth(admin))

    after = club_today()
    assert approved.status_code == 200, approved.text
    day = (await private(client, pending, "pat"))["membershipDateUtc"]
    assert day in {before, after}


@pytest.mark.requirement("users:R68")
@pytest.mark.asyncio
async def test_should_keep_membership_date_when_approving_a_user_who_has_one(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    pending = await create_user_with_status(db_session, "pat", UserStatus.pending)
    set_first = await patch_user(
        client, admin, "pat", {"membershipDateUtc": MIDNIGHT_2024_06_01}
    )
    assert set_first.status_code == 200, set_first.text

    approved = await client.post("/v1/users/by_id/pat/approve", headers=auth(admin))

    assert approved.status_code == 200, approved.text
    assert (await private(client, pending, "pat"))[
        "membershipDateUtc"
    ] == MIDNIGHT_2024_06_01


@pytest.mark.requirement("users:R68a")
@pytest.mark.asyncio
async def test_should_give_admin_created_user_the_creation_day(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    created = await client.post(
        "/v1/users",
        json={
            "username": "newbie",
            "passwordHash": "SecurePass123",
            "firstName": "New",
            "gender": "female",
            "dateOfBirthUtc": MIDNIGHT_2000,
            "phone": "9876543210",
        },
        headers=auth(admin),
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["membershipDateUtc"] == club_day(body["createdAtUtc"])
    read = await private(client, admin, "newbie")
    assert read["membershipDateUtc"] == club_day(read["createdAtUtc"])


@pytest.mark.requirement("users:R68a")
@pytest.mark.asyncio
async def test_should_leave_membership_date_blank_when_user_registers(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    registered = await client.post(
        "/v1/auth/register",
        json={
            "username": "newuser",
            "email": "newuser@example.com",
            "password": "newuserpass123",
            "firstName": "New",
            "gender": "male",
            "dateOfBirthUtc": MIDNIGHT_2000,
            "phone": "1234567890",
        },
    )

    assert registered.status_code == 201, registered.text
    assert (await private(client, admin, "newuser"))["membershipDateUtc"] is None


# --- The migration's backfill (R68b) ---
#
# The test database is built from the models, so the column already exists
# and starts blank; the backfill is run the way the migration runs it.

# 2026-03-01 20:00 UTC is 2026-03-02 01:30 in Asia/Kolkata, the default zone.
LATE_EVENING_UTC = 1772395200000
EARLIER_APPROVAL = LATE_EVENING_UTC - 30 * 86_400_000


def review_row(username: str, resolution: str | None, resolved_at: int | None):
    """A review request for ``username``, closed with ``resolution``."""
    return UserReviewRequest(
        username=username,
        reason="Please fix your name",
        requested_by="admin",
        created_at=(resolved_at or LATE_EVENING_UTC) - 86_400_000,
        resolved_at=resolved_at,
        resolved_by="admin" if resolved_at else None,
        resolution=resolution,
    )


async def membership_dates(db: AsyncSession) -> dict[str, int | None]:
    db.expire_all()
    rows = (await db.execute(select(User.username, User.membership_date))).all()
    return {username: date for username, date in rows}


async def run_backfill(db: AsyncSession) -> int:
    raw = await db.connection()
    filled = await raw.run_sync(fill_membership_dates)
    await db.commit()
    return filled


@pytest.mark.requirement("users:R68b")
@pytest.mark.asyncio
async def test_should_backfill_the_club_day_of_the_earliest_approving_request(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("CLUB_TIMEZONE", raising=False)
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "twice")
    _ = await create_member_user(db_session, "once")
    db_session.add_all(
        [
            review_row("twice", "approved", LATE_EVENING_UTC),
            review_row("twice", "approved", EARLIER_APPROVAL),
            review_row("once", "superseded", EARLIER_APPROVAL),
            review_row("once", "approved", LATE_EVENING_UTC),
        ]
    )
    await db_session.commit()

    filled = await run_backfill(db_session)

    dates = await membership_dates(db_session)
    assert filled == 2
    assert dates["once"] == date_to_day(date(2026, 3, 2))
    assert dates["twice"] == club_day(EARLIER_APPROVAL, "Asia/Kolkata")


@pytest.mark.requirement("users:R68b")
@pytest.mark.asyncio
async def test_should_leave_membership_date_blank_when_no_request_approved_the_user(
    db_session: AsyncSession,
):
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "plain")
    _ = await create_user_with_status(db_session, "blocked", UserStatus.blocked)
    _ = await create_user_with_status(db_session, "waiting", UserStatus.registered)
    db_session.add_all(
        [
            review_row("blocked", "blocked", LATE_EVENING_UTC),
            review_row("waiting", None, None),
        ]
    )
    await db_session.commit()

    filled = await run_backfill(db_session)

    dates = await membership_dates(db_session)
    assert filled == 0
    assert dates == {"admin": None, "plain": None, "blocked": None, "waiting": None}


@pytest.mark.requirement("users:R68b")
@pytest.mark.asyncio
async def test_should_keep_a_membership_date_the_backfill_finds_already_set(
    db_session: AsyncSession,
):
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "set")
    db_session.add(review_row("set", "approved", LATE_EVENING_UTC))
    await db_session.commit()
    _ = await db_session.execute(
        text("UPDATE users SET membership_date = :d WHERE username = 'set'"),
        {"d": MIDNIGHT_2024_06_01},
    )
    await db_session.commit()

    filled = await run_backfill(db_session)

    assert filled == 0
    assert (await membership_dates(db_session))["set"] == MIDNIGHT_2024_06_01


# The time zone the backfills use (#102): the environment first, then the
# secrets file, as ``Settings`` reads it; the default when neither is set.

# 2026-03-01 20:30 UTC: still 1 March in New York, 2 March in Asia/Kolkata.
EVENING_2030_UTC = LATE_EVENING_UTC + 30 * 60_000


def secrets_with_timezone(
    monkeypatch: pytest.MonkeyPatch, directory: Path, zone: str | None
) -> None:
    """Point the backfill at ``directory`` as its secrets dir, holding
    ``club_timezone`` when ``zone`` is given."""
    if zone is not None:
        _ = (directory / "club_timezone").write_text(f"{zone}\n", encoding="utf-8")
    monkeypatch.setattr(age_eligibility, "SECRETS_DIR", directory)


@pytest.mark.requirement("users:R68b")
@pytest.mark.asyncio
async def test_should_backfill_in_the_secrets_file_zone_when_environment_has_none(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.delenv("CLUB_TIMEZONE", raising=False)
    secrets_with_timezone(monkeypatch, tmp_path, "America/New_York")
    _ = await create_admin_user(db_session)
    _ = await create_member_user(db_session, "late")
    db_session.add(review_row("late", "approved", EVENING_2030_UTC))
    await db_session.commit()

    filled = await run_backfill(db_session)

    assert filled == 1
    assert (await membership_dates(db_session))["late"] == date_to_day(date(2026, 3, 1))


@pytest.mark.requirement("users:R68b")
def test_should_prefer_the_environment_zone_when_both_are_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("CLUB_TIMEZONE", "Europe/London")
    secrets_with_timezone(monkeypatch, tmp_path, "America/New_York")

    assert club_timezone_from_env() == "Europe/London"


@pytest.mark.requirement("users:R68b")
def test_should_read_the_secrets_file_zone_when_environment_is_blank(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("CLUB_TIMEZONE", "  ")
    secrets_with_timezone(monkeypatch, tmp_path, "America/New_York")

    assert club_timezone_from_env() == "America/New_York"


@pytest.mark.requirement("users:R68b")
def test_should_use_the_default_zone_when_neither_is_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.delenv("CLUB_TIMEZONE", raising=False)
    secrets_with_timezone(monkeypatch, tmp_path, None)

    assert club_timezone_from_env() == "Asia/Kolkata"
