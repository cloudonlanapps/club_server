"""Inquiries: public contact and interest submissions with an admin inbox (#407, public R23–R30).

The first unauthenticated write on this server. Spam defence is designed
in: honeypot, a server-issued fill-time token, size caps, and a
dedupe window. Delivery is one email to the club's inquiry address and
one admin notification. Retention is a daily purge.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.inquiry import Inquiry
from club_server.mailer.sender import ConsoleEmailSender
from club_server.services.inquiry import issue_form_token, purge_expired_inquiries
from club_server.utils import now_utc_ms

from .helpers import create_admin_user, create_coach_user, create_member_user

DAY = 86_400_000


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clear_outbox():
    ConsoleEmailSender.clear()
    yield
    ConsoleEmailSender.clear()


def _ready_token() -> str:
    """A token issued long enough ago that the minimum fill time has passed."""
    return issue_form_token(issued_at_ms=now_utc_ms() - 10_000)


def _body(**overrides) -> dict:
    return {
        "kind": "contact",
        "name": "Pat Visitor",
        "email": "pat@example.com",
        "phone": "+911234567890",
        "message": "Do you run beginner sessions on weekends?",
        "token": _ready_token(),
        "website": "",
        **overrides,
    }


async def _set_inquiry_email(client: AsyncClient, admin: str, address: str) -> None:
    response = await client.patch(
        "/v1/admin/preferences/club_info",
        json={"value": {"name": "Test Club", "inquiryEmail": address}},
        headers=_auth(admin),
    )
    assert response.status_code == 200


# ── public write ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.requirement("public:R23")
async def test_should_accept_contact_and_interest_submissions_anonymously(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()
    await _set_inquiry_email(client, admin, "query@example.com")

    response = await client.post("/v1/public/inquiries", json=_body())
    assert response.status_code == 202, response.text
    assert response.content == b""

    response = await client.post(
        "/v1/public/inquiries",
        json=_body(kind="interest", email="kid@example.com", extra={"ageGroup": "U12"}),
    )
    assert response.status_code == 202, response.text

    rows = (
        (await db_session.execute(select(Inquiry).order_by(Inquiry.id))).scalars().all()
    )
    assert [(r.kind, r.email) for r in rows] == [
        ("contact", "pat@example.com"),
        ("interest", "kid@example.com"),
    ]
    assert rows[1].extra == {"ageGroup": "U12"}
    assert rows[0].handled_at is None
    assert rows[0].source_hash and "127.0.0.1" not in rows[0].source_hash


@pytest.mark.asyncio
@pytest.mark.requirement("public:R24")
async def test_should_issue_a_form_token_and_refuse_submissions_without_a_valid_one(
    client: AsyncClient, db_session: AsyncSession
):
    issued = await client.get("/v1/public/inquiries/token")
    assert issued.status_code == 200
    token = issued.json()["token"]
    assert token and token.count(".") == 1

    # Too fast: the token was issued a moment ago.
    response = await client.post("/v1/public/inquiries", json=_body(token=token))
    assert response.status_code == 202
    assert (await db_session.execute(select(Inquiry))).scalars().all() == []

    # Forged and missing tokens are refused loudly: the form is broken, not a bot.
    forged = await client.post("/v1/public/inquiries", json=_body(token="123.deadbeef"))
    assert forged.status_code == 422
    assert forged.json()["detail"]["code"] == "INVALID_FORM_TOKEN"
    missing = await client.post("/v1/public/inquiries", json={**_body(), "token": None})
    assert missing.status_code == 422
    stale = await client.post(
        "/v1/public/inquiries",
        json=_body(token=issue_form_token(issued_at_ms=now_utc_ms() - 2 * 3_600_000)),
    )
    assert stale.status_code == 422
    assert stale.json()["detail"]["code"] == "INVALID_FORM_TOKEN"


@pytest.mark.asyncio
@pytest.mark.requirement("public:R25")
async def test_should_swallow_honeypot_and_duplicate_submissions_silently(
    client: AsyncClient, db_session: AsyncSession
):
    bot = await client.post(
        "/v1/public/inquiries", json=_body(website="http://spam.example")
    )
    assert bot.status_code == 202
    assert (await db_session.execute(select(Inquiry))).scalars().all() == []

    first = await client.post("/v1/public/inquiries", json=_body())
    again = await client.post(
        "/v1/public/inquiries", json=_body(message="Sent twice by mistake")
    )
    assert first.status_code == 202 and again.status_code == 202
    rows = (await db_session.execute(select(Inquiry))).scalars().all()
    assert len(rows) == 1
    assert rows[0].message == "Do you run beginner sessions on weekends?"

    other = await client.post(
        "/v1/public/inquiries", json=_body(email="someone@example.com")
    )
    assert other.status_code == 202
    assert len((await db_session.execute(select(Inquiry))).scalars().all()) == 2


@pytest.mark.asyncio
@pytest.mark.requirement("public:R26")
async def test_should_reject_malformed_submissions(
    client: AsyncClient, db_session: AsyncSession
):
    for bad in (
        {"kind": "complaint"},
        {"email": "not-an-email"},
        {"message": "x" * 2001},
        {"name": ""},
        {"name": "x" * 201},
        {"extra": "not an object"},
        {"unknown": 1},
    ):
        response = await client.post("/v1/public/inquiries", json=_body(**bad))
        assert response.status_code == 422, bad
    assert (await db_session.execute(select(Inquiry))).scalars().all() == []


# ── delivery ───────────────────────────────────────────────────────────────


@pytest.mark.requirement("notifications:R109")
@pytest.mark.asyncio
@pytest.mark.requirement("public:R27")
async def test_should_email_the_inquiry_address_and_notify_admins(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    coach = await create_coach_user(db_session)
    await db_session.commit()
    await _set_inquiry_email(client, admin, "query@example.com")

    response = await client.post("/v1/public/inquiries", json=_body())
    assert response.status_code == 202

    assert len(ConsoleEmailSender.outbox) == 1
    sent = ConsoleEmailSender.outbox[0]
    assert sent.to == "query@example.com"
    assert "Pat Visitor" in sent.text and "beginner sessions" in sent.text
    assert "pat@example.com" in sent.text
    assert "contact" in sent.subject.lower()

    notices = await client.get("/v1/notifications", headers=_auth(admin))
    types = [n["type"] for n in notices.json()["items"]]
    assert types == ["inquiry.received"]
    assert notices.json()["items"][0]["payload"]["data"]["kind"] == "contact"
    coach_notices = await client.get("/v1/notifications", headers=_auth(coach))
    assert [n["type"] for n in coach_notices.json()["items"]] == []


@pytest.mark.requirement("notifications:R109")
@pytest.mark.asyncio
@pytest.mark.requirement("public:R27")
async def test_should_still_store_and_notify_when_no_inquiry_address_is_set(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.post("/v1/public/inquiries", json=_body())
    assert response.status_code == 202
    assert ConsoleEmailSender.outbox == []
    assert len((await db_session.execute(select(Inquiry))).scalars().all()) == 1
    notices = await client.get("/v1/notifications", headers=_auth(admin))
    assert [n["type"] for n in notices.json()["items"]] == ["inquiry.received"]


# ── admin inbox ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.requirement("public:R28")
async def test_should_list_filter_handle_and_delete_inquiries_as_admin(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()
    await client.post("/v1/public/inquiries", json=_body())
    await client.post(
        "/v1/public/inquiries", json=_body(kind="interest", email="kid@example.com")
    )

    listing = await client.get("/v1/admin/inquiries", headers=_auth(admin))
    assert listing.status_code == 200
    page = listing.json()
    assert page["total"] == 2
    assert [i["kind"] for i in page["items"]] == ["interest", "contact"]  # newest first
    assert page["items"][0]["handledAt"] is None
    assert "sourceHash" not in page["items"][0]
    contact_id = page["items"][1]["id"]

    by_kind = await client.get(
        "/v1/admin/inquiries", params={"kind": "contact"}, headers=_auth(admin)
    )
    assert [i["id"] for i in by_kind.json()["items"]] == [contact_id]

    handled = await client.patch(
        f"/v1/admin/inquiries/{contact_id}",
        json={"handled": True},
        headers=_auth(admin),
    )
    assert handled.status_code == 200
    assert handled.json()["handledBy"] == "admin"
    assert handled.json()["handledAt"] is not None

    unhandled = await client.get(
        "/v1/admin/inquiries", params={"handled": "false"}, headers=_auth(admin)
    )
    assert [i["kind"] for i in unhandled.json()["items"]] == ["interest"]

    reopened = await client.patch(
        f"/v1/admin/inquiries/{contact_id}",
        json={"handled": False},
        headers=_auth(admin),
    )
    assert reopened.json()["handledAt"] is None and reopened.json()["handledBy"] is None

    deleted = await client.delete(
        f"/v1/admin/inquiries/{contact_id}", headers=_auth(admin)
    )
    assert deleted.status_code == 204
    assert (await client.get("/v1/admin/inquiries", headers=_auth(admin))).json()[
        "total"
    ] == 1
    assert (
        await client.delete(f"/v1/admin/inquiries/{contact_id}", headers=_auth(admin))
    ).status_code == 404

    audit = await client.get(
        "/v1/audit_log", params={"action": "handle_inquiry"}, headers=_auth(admin)
    )
    assert audit.json()["total"] == 2
    audit = await client.get(
        "/v1/audit_log", params={"action": "delete_inquiry"}, headers=_auth(admin)
    )
    assert audit.json()["total"] == 1


@pytest.mark.asyncio
@pytest.mark.requirement("public:R28")
async def test_should_refuse_the_inbox_to_non_admins(
    client: AsyncClient, db_session: AsyncSession
):
    coach = await create_coach_user(db_session)
    member = await create_member_user(db_session)
    await db_session.commit()

    assert (await client.get("/v1/admin/inquiries")).status_code == 401
    for token in (coach, member):
        assert (
            await client.get("/v1/admin/inquiries", headers=_auth(token))
        ).status_code == 403
        assert (
            await client.patch(
                "/v1/admin/inquiries/1", json={"handled": True}, headers=_auth(token)
            )
        ).status_code == 403


# ── retention ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.requirement("public:R29")
async def test_should_purge_handled_after_180_days_and_unhandled_after_365(
    client: AsyncClient, db_session: AsyncSession
):
    await create_admin_user(db_session)
    await db_session.commit()
    for email in ("a@example.com", "b@example.com", "c@example.com", "d@example.com"):
        assert (
            await client.post("/v1/public/inquiries", json=_body(email=email))
        ).status_code == 202
    rows = (
        (await db_session.execute(select(Inquiry).order_by(Inquiry.id))).scalars().all()
    )
    now = now_utc_ms()
    rows[0].handled_at = now - 181 * DAY  # handled, expired
    rows[0].handled_by = "admin"
    rows[1].handled_at = now - 100 * DAY  # handled, kept
    rows[1].handled_by = "admin"
    rows[2].created_at = now - 366 * DAY  # unhandled, expired
    rows[3].created_at = now - 300 * DAY  # unhandled, kept
    await db_session.commit()

    removed = await purge_expired_inquiries(db_session, now)
    await db_session.commit()
    assert removed == 2
    kept = (
        (await db_session.execute(select(Inquiry.email).order_by(Inquiry.id)))
        .scalars()
        .all()
    )
    assert kept == ["b@example.com", "d@example.com"]


@pytest.mark.asyncio
@pytest.mark.requirement("public:R30")
async def test_should_not_write_submissions_to_the_audit_log(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    await db_session.commit()
    assert (await client.post("/v1/public/inquiries", json=_body())).status_code == 202
    audit = await client.get("/v1/audit_log", headers=_auth(admin))
    assert audit.json()["total"] == 0
