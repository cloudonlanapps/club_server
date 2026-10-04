"""The gate module's occurrence semantics (#382, camp R32–R35).

Ported from the ``ConflictService`` suite that #382 removed: events are
built directly through the ORM and the gates are called as a service. The
cases are the ones camps have relied on — self and deleted events excluded,
a cutoff clipping a series, rest days, cancelled and rescheduled occurrences
— plus the one the chain-era suite got wrong: a programme that still runs
to its cutoff clashes up to that cutoff (enrollment R29b), and only a cutoff
before its first occurrence silences it.
"""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.enrollment import Enrollment, EnrollmentStatus
from club_server.db.models.event import Event
from club_server.db.models.occurrence_override import OccurrenceOverride
from club_server.db.models.user import User, UserStatus
from club_server.db.models.venue import Venue
from club_server.services.auth import AuthService
from club_server.services.conflict_gates import (
    ConflictGate,
    Finding,
    GatePolicy,
    ScheduleTarget,
    check_conflicts,
)
from club_server.utils import now_utc_ms

HOUR_MS = 60 * 60 * 1000
DAY_MS = 24 * HOUR_MS
BASE_TS = 1_700_000_000_000  # fixed reference instant


async def _make_venue(db: AsyncSession, name: str = "Venue A") -> int:
    venue = Venue(name=name, created_at=now_utc_ms(), updated_at=now_utc_ms())
    db.add(venue)
    await db.flush()
    return venue.id


async def _make_user(db: AsyncSession, username: str = "member1") -> str:
    user = User(
        username=username,
        password=AuthService.hash_password("pw"),
        first_name=username,
        status=UserStatus.active.value,
        is_super_admin=0,
        roles=json.dumps({"roles": []}),
        created_at=now_utc_ms(),
    )
    db.add(user)
    await db.flush()
    return username


async def _make_event(
    db: AsyncSession,
    *,
    venue_id: int,
    title: str = "Camp",
    type: str = "camp",
    start_time: int = BASE_TS,
    end_time: int | None = None,
    rrule: str | None = "FREQ=DAILY;COUNT=3",
    until_time: int | None = None,
    organizer_name: str | None = None,
    coach_names: list[str] | None = None,
    deleted_at: int | None = None,
) -> Event:
    for name in [organizer_name, *(coach_names or [])]:
        if name and await db.get(User, name) is None:
            _ = await _make_user(db, name)
    event = Event(
        title=title,
        type=type,
        visibility="public",
        venue_id=venue_id,
        organizer_name=organizer_name,
        coach_names=coach_names,
        rrule=rrule,
        start_time=start_time,
        end_time=end_time if end_time is not None else start_time + 2 * HOUR_MS,
        until_time=until_time,
        is_featured=False,
        created_at=now_utc_ms(),
        updated_at=now_utc_ms(),
        deleted_at=deleted_at,
    )
    db.add(event)
    await db.flush()
    return event


async def _enroll(
    db: AsyncSession,
    *,
    username: str,
    event_id: int,
    status: str = EnrollmentStatus.accepted.value,
) -> None:
    db.add(
        Enrollment(
            membername=username,
            event_id=event_id,
            status=status,
            created_at=now_utc_ms(),
        )
    )
    await db.flush()


async def _make_override(
    db: AsyncSession,
    *,
    event_id: int,
    occurrence_time: int,
    status: str,
    new_start_time: int | None = None,
    new_end_time: int | None = None,
) -> None:
    db.add(
        OccurrenceOverride(
            event_id=event_id,
            occurrence_time=occurrence_time,
            status=status,
            new_start_time=new_start_time,
            new_end_time=new_end_time,
        )
    )
    await db.flush()


def _camp_target(venue_id: int, **extra: object) -> ScheduleTarget:
    return ScheduleTarget(
        event_type="camp",
        venue_id=venue_id,
        start_time=BASE_TS,
        end_time=BASE_TS + 2 * HOUR_MS,
        rrule="FREQ=DAILY;COUNT=3",
        effective_from=BASE_TS,
        **extra,  # pyright: ignore[reportArgumentType]
    )


async def _gate(
    db: AsyncSession, target: ScheduleTarget, gate: ConflictGate
) -> list[Finding]:
    report = await check_conflicts(db, target, {gate: GatePolicy.advise})
    return report.findings


def _other_starts(findings: list[Finding]) -> list[int]:
    return sorted(p[2] for p in findings[0].occurrences)


# --- venue -------------------------------------------------------------


@pytest.mark.asyncio
async def test_should_find_every_shared_day_when_camps_share_a_venue(
    db_session: AsyncSession,
):
    venue_id = await _make_venue(db_session)
    existing = await _make_event(db_session, venue_id=venue_id, title="Existing Camp")

    findings = await _gate(db_session, _camp_target(venue_id), ConflictGate.venue)

    assert [f.event_id for f in findings] == [existing.id]
    assert len(findings[0].occurrences) == 3


@pytest.mark.asyncio
async def test_should_exclude_the_event_itself_when_asked(db_session: AsyncSession):
    venue_id = await _make_venue(db_session)
    existing = await _make_event(db_session, venue_id=venue_id)

    findings = await _gate(
        db_session,
        _camp_target(venue_id, exclude_event_id=existing.id),
        ConflictGate.venue,
    )

    assert findings == []


@pytest.mark.asyncio
async def test_should_exclude_deleted_events(db_session: AsyncSession):
    venue_id = await _make_venue(db_session)
    await _make_event(db_session, venue_id=venue_id, deleted_at=now_utc_ms())

    assert await _gate(db_session, _camp_target(venue_id), ConflictGate.venue) == []


@pytest.mark.asyncio
async def test_should_exclude_camp_cancelled_before_it_starts(db_session: AsyncSession):
    venue_id = await _make_venue(db_session)
    await _make_event(db_session, venue_id=venue_id, until_time=BASE_TS - HOUR_MS)

    assert await _gate(db_session, _camp_target(venue_id), ConflictGate.venue) == []


@pytest.mark.asyncio
async def test_should_count_programme_until_its_cutoff(db_session: AsyncSession):
    """A cutoff after the occurrence leaves it live (R29b); one before silences it."""
    venue_id = await _make_venue(db_session)
    running = await _make_event(
        db_session,
        venue_id=venue_id,
        type="programme",
        rrule=None,
        until_time=BASE_TS + DAY_MS,
    )

    findings = await _gate(db_session, _camp_target(venue_id), ConflictGate.venue)
    assert [f.event_id for f in findings] == [running.id]

    other_venue = await _make_venue(db_session, "B")
    await _make_event(
        db_session,
        venue_id=other_venue,
        type="programme",
        rrule=None,
        until_time=BASE_TS - HOUR_MS,
    )
    assert await _gate(db_session, _camp_target(other_venue), ConflictGate.venue) == []


@pytest.mark.asyncio
async def test_should_drop_rest_day_from_target_when_it_carries_exdate(
    db_session: AsyncSession,
):
    venue_id = await _make_venue(db_session)
    await _make_event(db_session, venue_id=venue_id)
    day2 = datetime.fromtimestamp((BASE_TS + DAY_MS) / 1000, tz=timezone.utc)
    target = ScheduleTarget(
        event_type="camp",
        venue_id=venue_id,
        start_time=BASE_TS,
        end_time=BASE_TS + 2 * HOUR_MS,
        rrule=f"FREQ=DAILY;COUNT=3\nEXDATE:{day2.strftime('%Y%m%dT%H%M%SZ')}",
        effective_from=BASE_TS,
    )

    findings = await _gate(db_session, target, ConflictGate.venue)

    assert len(findings) == 1
    assert BASE_TS + DAY_MS not in [p[0] for p in findings[0].occurrences]


@pytest.mark.asyncio
async def test_should_find_nothing_when_venue_differs(db_session: AsyncSession):
    venue_a = await _make_venue(db_session, "A")
    venue_b = await _make_venue(db_session, "B")
    await _make_event(db_session, venue_id=venue_a)

    assert await _gate(db_session, _camp_target(venue_b), ConflictGate.venue) == []


# --- organizer and coach ----------------------------------------------


@pytest.mark.asyncio
async def test_should_find_organizer_running_two_things_at_once(
    db_session: AsyncSession,
):
    venue_id = await _make_venue(db_session)
    other_venue = await _make_venue(db_session, "Other")
    existing = await _make_event(
        db_session, venue_id=other_venue, organizer_name="alice"
    )

    findings = await _gate(
        db_session,
        _camp_target(venue_id, organizer_name="alice"),
        ConflictGate.organizer,
    )

    assert [f.event_id for f in findings] == [existing.id]


@pytest.mark.asyncio
async def test_should_find_nothing_when_target_names_no_organizer(
    db_session: AsyncSession,
):
    venue_id = await _make_venue(db_session)
    await _make_event(db_session, venue_id=venue_id, organizer_name="alice")

    assert await _gate(db_session, _camp_target(venue_id), ConflictGate.organizer) == []


@pytest.mark.asyncio
async def test_should_find_coach_shared_between_events(db_session: AsyncSession):
    venue_id = await _make_venue(db_session)
    other_venue = await _make_venue(db_session, "Other")
    existing = await _make_event(
        db_session, venue_id=other_venue, coach_names=["bob", "carol"]
    )

    findings = await _gate(
        db_session,
        _camp_target(venue_id, coach_names=("carol", "dan")),
        ConflictGate.coach,
    )

    assert [f.event_id for f in findings] == [existing.id]


@pytest.mark.asyncio
async def test_should_find_nothing_when_coaches_are_disjoint_or_absent(
    db_session: AsyncSession,
):
    venue_id = await _make_venue(db_session)
    await _make_event(db_session, venue_id=venue_id, coach_names=["bob"])

    assert (
        await _gate(
            db_session, _camp_target(venue_id, coach_names=("dan",)), ConflictGate.coach
        )
        == []
    )
    assert await _gate(db_session, _camp_target(venue_id), ConflictGate.coach) == []


# --- member -------------------------------------------------------------


@pytest.mark.asyncio
async def test_should_find_member_enrolled_in_an_overlapping_event(
    db_session: AsyncSession,
):
    venue_id = await _make_venue(db_session)
    other_venue = await _make_venue(db_session, "Other")
    username = await _make_user(db_session)
    existing = await _make_event(db_session, venue_id=other_venue)
    await _enroll(db_session, username=username, event_id=existing.id)

    findings = await _gate(
        db_session, _camp_target(venue_id, membername=username), ConflictGate.member
    )

    assert [f.event_id for f in findings] == [existing.id]


@pytest.mark.asyncio
async def test_should_ignore_terminal_enrollments_and_disjoint_times(
    db_session: AsyncSession,
):
    venue_id = await _make_venue(db_session)
    other_venue = await _make_venue(db_session, "Other")
    username = await _make_user(db_session)
    withdrawn_from = await _make_event(db_session, venue_id=other_venue)
    await _enroll(
        db_session,
        username=username,
        event_id=withdrawn_from.id,
        status=EnrollmentStatus.withdrawn.value,
    )
    far = await _make_event(
        db_session,
        venue_id=other_venue,
        start_time=BASE_TS + 10 * DAY_MS,
        rrule="FREQ=DAILY;COUNT=2",
    )
    await _enroll(db_session, username=username, event_id=far.id)

    findings = await _gate(
        db_session, _camp_target(venue_id, membername=username), ConflictGate.member
    )

    assert findings == []


@pytest.mark.asyncio
async def test_should_exclude_self_and_find_nothing_without_a_member(
    db_session: AsyncSession,
):
    venue_id = await _make_venue(db_session)
    username = await _make_user(db_session)
    self_event = await _make_event(db_session, venue_id=venue_id)
    await _enroll(db_session, username=username, event_id=self_event.id)

    excluded = await _gate(
        db_session,
        _camp_target(venue_id, membername=username, exclude_event_id=self_event.id),
        ConflictGate.member,
    )
    nobody = await _gate(db_session, _camp_target(venue_id), ConflictGate.member)

    assert excluded == []
    assert nobody == []


# --- cutoffs, cancelled and rescheduled occurrences (#231) -----------


@pytest.mark.asyncio
async def test_should_keep_surviving_days_when_camp_is_cancelled_mid_series(
    db_session: AsyncSession,
):
    venue_id = await _make_venue(db_session)
    await _make_event(db_session, venue_id=venue_id, until_time=BASE_TS + DAY_MS)

    findings = await _gate(db_session, _camp_target(venue_id), ConflictGate.venue)

    assert _other_starts(findings) == [BASE_TS]


@pytest.mark.asyncio
async def test_should_drop_cancelled_occurrence_from_every_gate(
    db_session: AsyncSession,
):
    venue_id = await _make_venue(db_session)
    existing = await _make_event(db_session, venue_id=venue_id, coach_names=["carol"])
    await _make_override(
        db_session,
        event_id=existing.id,
        occurrence_time=BASE_TS + DAY_MS,
        status="cancelled",
    )

    venue = await _gate(db_session, _camp_target(venue_id), ConflictGate.venue)
    coach = await _gate(
        db_session, _camp_target(venue_id, coach_names=("carol",)), ConflictGate.coach
    )

    assert _other_starts(venue) == [BASE_TS, BASE_TS + 2 * DAY_MS]
    assert _other_starts(coach) == [BASE_TS, BASE_TS + 2 * DAY_MS]


@pytest.mark.asyncio
async def test_should_follow_rescheduled_occurrence_out_of_and_into_range(
    db_session: AsyncSession,
):
    venue_id = await _make_venue(db_session)
    other_venue = await _make_venue(db_session, "Other")
    existing = await _make_event(db_session, venue_id=venue_id)
    moved = BASE_TS + 30 * DAY_MS
    await _make_override(
        db_session,
        event_id=existing.id,
        occurrence_time=BASE_TS + DAY_MS,
        status="rescheduled",
        new_start_time=moved,
        new_end_time=moved + 2 * HOUR_MS,
    )
    orig_start = BASE_TS + 12 * HOUR_MS
    one_off = await _make_event(
        db_session,
        venue_id=other_venue,
        type="oneOff",
        rrule=None,
        organizer_name="alice",
        start_time=orig_start,
    )
    await _make_override(
        db_session,
        event_id=one_off.id,
        occurrence_time=orig_start,
        status="rescheduled",
        new_start_time=BASE_TS,
        new_end_time=BASE_TS + 2 * HOUR_MS,
    )

    left = await _gate(db_session, _camp_target(venue_id), ConflictGate.venue)
    arrived = await _gate(
        db_session,
        _camp_target(venue_id, organizer_name="alice"),
        ConflictGate.organizer,
    )

    assert _other_starts(left) == [BASE_TS, BASE_TS + 2 * DAY_MS]
    assert [f.event_id for f in arrived] == [one_off.id]
    assert _other_starts(arrived) == [BASE_TS]


@pytest.mark.asyncio
async def test_should_apply_cutoff_and_reschedule_to_member_gate_too(
    db_session: AsyncSession,
):
    venue_id = await _make_venue(db_session)
    other_venue = await _make_venue(db_session, "Other")
    username = await _make_user(db_session)
    clipped = await _make_event(
        db_session, venue_id=other_venue, until_time=BASE_TS + DAY_MS
    )
    await _enroll(db_session, username=username, event_id=clipped.id)

    findings = await _gate(
        db_session, _camp_target(venue_id, membername=username), ConflictGate.member
    )

    assert _other_starts(findings) == [BASE_TS]
