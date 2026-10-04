"""Shared builders for the evaluation suites (#302, #535)."""

from datetime import datetime, timezone
from typing import Any

from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.attendance import AttendanceRecord
from club_server.utils import MS_PER_DAY, now_utc_ms

from .helpers import create_member_user


WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


def auth(token: str) -> dict[str, str]:
    """Bearer header."""
    return {"Authorization": f"Bearer {token}"}


def future_ms(hours: int) -> int:
    """A timestamp `hours` from now."""
    return now_utc_ms() + hours * 3600 * 1000


def days_ago(days: float) -> int:
    """A timestamp `days` before now: periods lie in the past (R4)."""
    return now_utc_ms() - int(days * MS_PER_DAY)


def past_period(start_days_ago: float, end_days_ago: float) -> dict[str, int]:
    """A period's request fields, both bounds in the past (R4)."""
    return {
        "periodStartUtc": days_ago(start_days_ago),
        "periodEndUtc": days_ago(end_days_ago),
    }


async def record_past_attendance(
    db_session: AsyncSession,
    event_id: int,
    membername: str,
    occurrence_time_utc: int,
) -> None:
    """An attendance row for an occurrence already past.

    Marking goes through the API only for an occurrence about to start, and
    a period must lie in the past (R4), so a period covering an attended
    session needs the record written directly. Committed, so a refused
    request's rollback leaves it in place.
    """
    db_session.add(
        AttendanceRecord(
            event_id=event_id,
            occurrence_time_utc=occurrence_time_utc,
            membername=membername,
            status="present",
            recorded_at=now_utc_ms(),
        )
    )
    await db_session.commit()


def rating_item(question: str = "Skating", **extra: Any) -> dict[str, Any]:
    """A 1..5 rating question that takes evidence and a coach note."""
    return {
        "type": "rating",
        "question": question,
        "rateMin": 1,
        "rateMax": 5,
        "showCommentArea": True,
        "allowEvidence": True,
        **extra,
    }


def qa_item(question: str = "Notes", **extra: Any) -> dict[str, Any]:
    """A written-answer question."""
    return {"type": "qa", "question": question, **extra}


def choice_item(
    question: str = "Position",
    values: tuple[str, ...] = ("forward", "defence"),
    *,
    multiple: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    """A single- or multiple-choice question over `values`."""
    return {
        "type": "multipleChoice" if multiple else "singleChoice",
        "question": question,
        "choices": [{"value": v, "text": v.title()} for v in values],
        **extra,
    }


def info_item(markdown: str = "Read this first.") -> dict[str, Any]:
    """An info text, which asks nothing."""
    return {"type": "info", "markdown": markdown}


async def create_template_response(
    client: AsyncClient,
    admin_token: str,
    *,
    name: str = "General review",
    layout: list[Any] | None = None,
) -> Response:
    """POST a template and return the raw response."""
    return await client.post(
        "/v1/evaluations/templates",
        json={
            "name": name,
            "layout": layout if layout is not None else [rating_item()],
        },
        headers=auth(admin_token),
    )


async def create_template(
    client: AsyncClient,
    admin_token: str,
    *,
    name: str = "General review",
    layout: list[Any] | None = None,
) -> int:
    """Create a template and return its id.

    The default is one rating question, `Skating`, that is not required, so
    a draft written against it can be saved with no answers.
    """
    response = await create_template_response(
        client, admin_token, name=name, layout=layout
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def get_template(
    client: AsyncClient, token: str, template_id: int
) -> dict[str, Any]:
    """Read a template, asserting it is there."""
    response = await client.get(
        f"/v1/evaluations/templates/by_id/{template_id}", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


def flat_layout(layout: list[Any]) -> list[int]:
    """Item ids in layout order, sections opened."""
    ids: list[int] = []
    for entry in layout:
        if isinstance(entry, dict):
            ids.extend(entry["items"])
        else:
            ids.append(entry)
    return ids


async def item_ids(client: AsyncClient, token: str, template_id: int) -> list[int]:
    """The template's item ids, in layout order."""
    return flat_layout((await get_template(client, token, template_id))["layout"])


async def create_venue(client: AsyncClient, admin_token: str) -> int:
    """Create a venue and return its id."""
    response = await client.post(
        "/v1/venues",
        json={"name": "Rink", "address": "1 Ice Way"},
        headers=auth(admin_token),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def create_event_with_coach(
    client: AsyncClient,
    admin_token: str,
    *,
    coach_names: list[str],
    title: str = "Programme",
    type_: str = "programme",
) -> tuple[int, int]:
    """Create an event coached by `coach_names`. Returns (event_id, first occurrence).

    The first occurrence starts ten minutes from now so it is already inside
    the attendance-marking window, which opens thirty minutes before an
    occurrence begins. A fixture a day out cannot record attendance at all.
    """
    venue_id = await create_venue(client, admin_token)
    # Whole seconds: rule expansion is second-granular, so a start carrying
    # milliseconds is not one of the event's own slots (#470).
    start = (now_utc_ms() + 10 * 60 * 1000) // 1000 * 1000
    # A programme's rule may carry only FREQ and BYDAY, and BYDAY is required
    # (#384). BYDAY is derived from the start day so the first occurrence is
    # the event start itself, and therefore inside the attendance window.
    byday = WEEKDAY_CODES[
        datetime.fromtimestamp(start / 1000, tz=timezone.utc).weekday()
    ]
    response = await client.post(
        "/v1/events",
        json={
            "title": title,
            "type": type_,
            "venueId": venue_id,
            "startTimeUtc": start,
            "endTimeUtc": start + 2 * 3600 * 1000,
            "rrule": f"FREQ=WEEKLY;BYDAY={byday}",
            "coachNames": coach_names,
        },
        headers=auth(admin_token),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"], start


async def mark_attendance(
    client: AsyncClient,
    admin_token: str,
    event_id: int,
    occurrence: int,
    membername: str,
    status_value: str = "present",
) -> None:
    """Record an attendance row so the member half of eligibility is satisfied."""
    response = await client.post(
        f"/v1/events/by_id/{event_id}/occurrences/{occurrence}/attendance",
        json={"records": [{"membername": membername, "status": status_value}]},
        headers=auth(admin_token),
    )
    assert response.status_code in (200, 201), response.text


async def create_subject(db_session: AsyncSession, username: str = "alice") -> str:
    """Create an active member to be evaluated. Returns their token."""
    return await create_member_user(db_session, username=username)


async def create_evaluation_response(
    client: AsyncClient,
    token: str,
    template_id: int,
    created_for: str,
    **extra: Any,
) -> Response:
    """POST an evaluation and return the raw response."""
    return await client.post(
        "/v1/evaluations",
        json={"templateId": template_id, "createdFor": created_for, **extra},
        headers=auth(token),
    )


async def create_general_evaluation(
    client: AsyncClient,
    token: str,
    template_id: int,
    created_for: str,
    **extra: Any,
) -> int:
    """Create a draft (general unless `eventId` is passed) and return its id."""
    response = await create_evaluation_response(
        client, token, template_id, created_for, **extra
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def get_evaluation(
    client: AsyncClient, token: str, evaluation_id: int
) -> dict[str, Any]:
    """Read an evaluation on the staff surface, asserting it is there."""
    response = await client.get(
        f"/v1/evaluations/by_id/{evaluation_id}", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def put_answer(
    client: AsyncClient,
    token: str,
    evaluation_id: int,
    item_id: int,
    **body: Any,
) -> Response:
    """Write one answer and return the raw response."""
    return await client.put(
        f"/v1/evaluations/by_id/{evaluation_id}/answers/{item_id}",
        json=body,
        headers=auth(token),
    )


def answer_of(evaluation: dict[str, Any], item_id: int) -> dict[str, Any] | None:
    """The answer to one item in a read evaluation, or None."""
    for answer in evaluation["answers"]:
        if answer["itemId"] == item_id:
            return answer
    return None


async def publish(client: AsyncClient, token: str, evaluation_id: int) -> None:
    """Walk a draft through saved to published."""
    saved = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/save", headers=auth(token)
    )
    assert saved.status_code == 200, saved.text
    published = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/publish", headers=auth(token)
    )
    assert published.status_code == 200, published.text


async def transfer(
    client: AsyncClient, token: str, evaluation_id: int, owner: str
) -> Response:
    """Transfer an evaluation and return the raw response."""
    return await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/transfer",
        json={"owner": owner},
        headers=auth(token),
    )
