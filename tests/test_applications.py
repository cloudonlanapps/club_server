"""Application templates over the API: gating, writes, visibility (#93).

R1 (the module switch), R2–R5 (create, edit, delete, duplicate) and R10
(who sees what). Shapes and catalogues are in ``test_application_shapes.py``.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.db.models.user import UserStatus

from .application_helpers import (
    APPLICATIONS,
    auth,
    create_applicant,
    create_application,
    get_application,
    move,
    published_application,
    template_body,
)
from .helpers import create_admin_user, create_coach_user, create_regular_admin_user

pytestmark = pytest.mark.usefixtures("applications_enabled")


async def listed_ids(client: AsyncClient, token: str, query: str = "") -> list[int]:
    response = await client.get(f"{APPLICATIONS}{query}", headers=auth(token))
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


# R1 -----------------------------------------------------------------------


@pytest.mark.requirement("applications:R1")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", ""),
        ("POST", ""),
        ("GET", "/1"),
        ("PATCH", "/1"),
        ("DELETE", "/1"),
        ("POST", "/1/save"),
        ("POST", "/1/duplicate"),
        ("GET", "/1/preview"),
        ("GET", "/1/form"),
        ("GET", "/1/form/ravi"),
        ("GET", "/labels"),
        ("GET", "/clauses"),
    ],
)
async def test_should_refuse_every_route_when_module_is_off(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
):
    from club_server.config import settings

    monkeypatch.setattr(settings, "applications_enabled", False)
    admin = await create_admin_user(db_session)

    response = await client.request(
        method, f"{APPLICATIONS}{path}", json={}, headers=auth(admin)
    )

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "APPLICATIONS_DISABLED"


@pytest.mark.requirement("applications:R1")
@pytest.mark.asyncio
async def test_should_refuse_applications_when_identity_verification_is_off(
    client: AsyncClient, db_session: AsyncSession, identity_verification_off: None
):
    admin = await create_admin_user(db_session)

    response = await client.get(APPLICATIONS, headers=auth(admin))

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "APPLICATIONS_DISABLED"


# R2 -----------------------------------------------------------------------


@pytest.mark.requirement("applications:R2")
@pytest.mark.asyncio
@pytest.mark.parametrize("admin_kind", ["super", "regular"])
async def test_should_create_a_draft_when_admin_sends_a_template(
    client: AsyncClient, db_session: AsyncSession, admin_kind: str
):
    admin = (
        await create_admin_user(db_session)
        if admin_kind == "super"
        else await create_regular_admin_user(db_session)
    )
    body = template_body()

    created = await create_application(client, admin, body)

    assert created["status"] == "draft"
    assert created["layout"] is None
    read = await get_application(client, admin, created["id"])
    assert read.status_code == 200
    stored = read.json()
    for key, value in body.items():
        assert stored[key] == value, key
    assert stored["createdBy"] == (
        "admin" if admin_kind == "super" else "regular_admin"
    )


@pytest.mark.requirement("applications:R2")
@pytest.mark.asyncio
async def test_should_create_an_empty_draft_when_nothing_is_sent(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)

    created = await create_application(client, admin, {})

    read = (await get_application(client, admin, created["id"])).json()
    assert read["languages"] == ["en"]
    assert read["title"] is None
    assert read["terms"] == [] and read["facts"] == {} and read["labelOverrides"] == {}


@pytest.mark.requirement("applications:R2")
@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["coach", "member", "anonymous"])
async def test_should_refuse_create_when_caller_is_not_an_admin(
    client: AsyncClient, db_session: AsyncSession, role: str
):
    admin = await create_admin_user(db_session)
    tokens = {
        "coach": await create_coach_user(db_session),
        "member": await create_applicant(db_session, "ravi"),
    }
    await db_session.commit()

    response = await client.post(
        APPLICATIONS,
        json=template_body(),
        headers=auth(tokens[role]) if role in tokens else {},
    )

    assert response.status_code == (401 if role == "anonymous" else 403), response.text
    assert await listed_ids(client, admin) == []


# R3 -----------------------------------------------------------------------


@pytest.mark.requirement("applications:R3")
@pytest.mark.asyncio
async def test_should_replace_sent_fields_when_admin_edits_a_draft(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    created = await create_application(client, admin)

    response = await client.patch(
        f"{APPLICATIONS}/{created['id']}",
        json={
            "title": {"en": "Camp", "mr": "शिबिर"},
            "subtitle": None,
            "selections": None,
        },
        headers=auth(admin),
    )

    assert response.status_code == 200, response.text
    read = (await get_application(client, admin, created["id"])).json()
    assert read["title"] == {"en": "Camp", "mr": "शिबिर"}
    assert read["subtitle"] is None
    assert read["selections"] == []
    assert read["declaration"] == template_body()["declaration"]  # left out: kept
    assert read["updatedAtUtc"] >= created["updatedAtUtc"]


@pytest.mark.requirement("applications:R3")
@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["saved", "published"])
async def test_should_refuse_an_edit_when_template_is_not_a_draft(
    client: AsyncClient, db_session: AsyncSession, state: str
):
    admin = await create_admin_user(db_session)
    created = await create_application(client, admin)
    for action in ("save", "publish")[: 1 if state == "saved" else 2]:
        assert (await move(client, admin, created["id"], action)).status_code == 200

    response = await client.patch(
        f"{APPLICATIONS}/{created['id']}",
        json={"title": {"en": "X", "mr": "क्ष"}},
        headers=auth(admin),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    read = (await get_application(client, admin, created["id"])).json()
    assert read["title"] == template_body()["title"]
    assert read["status"] == state


@pytest.mark.requirement("applications:R3")
@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["coach", "member"])
async def test_should_refuse_an_edit_when_caller_is_not_an_admin(
    client: AsyncClient, db_session: AsyncSession, role: str
):
    admin = await create_admin_user(db_session)
    created = await create_application(client, admin)
    token = (
        await create_coach_user(db_session)
        if role == "coach"
        else await create_applicant(db_session, "ravi")
    )
    await db_session.commit()

    response = await client.patch(
        f"{APPLICATIONS}/{created['id']}",
        json={"title": {"en": "X"}},
        headers=auth(token),
    )

    assert response.status_code == 403
    assert (await get_application(client, admin, created["id"])).json()[
        "title"
    ] == template_body()["title"]


# R4 -----------------------------------------------------------------------


@pytest.mark.requirement("applications:R4")
@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["draft", "saved"])
async def test_should_soft_delete_when_template_is_draft_or_saved(
    client: AsyncClient, db_session: AsyncSession, state: str
):
    admin = await create_admin_user(db_session)
    created = await create_application(client, admin)
    if state == "saved":
        assert (await move(client, admin, created["id"], "save")).status_code == 200

    response = await client.delete(
        f"{APPLICATIONS}/{created['id']}", headers=auth(admin)
    )

    assert response.status_code == 200, response.text
    assert await listed_ids(client, admin) == []
    gone = await get_application(client, admin, created["id"])
    assert gone.status_code == 404
    assert gone.json()["detail"]["code"] == "APPLICATION_NOT_FOUND"


@pytest.mark.requirement("applications:R4")
@pytest.mark.asyncio
async def test_should_refuse_to_delete_when_template_is_published(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin)

    response = await client.delete(
        f"{APPLICATIONS}/{published['id']}", headers=auth(admin)
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    assert await listed_ids(client, admin) == [published["id"]]


@pytest.mark.requirement("applications:R4")
@pytest.mark.asyncio
async def test_should_refuse_to_delete_when_caller_is_a_coach(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    created = await create_application(client, admin)
    coach = await create_coach_user(db_session)
    await db_session.commit()

    response = await client.delete(
        f"{APPLICATIONS}/{created['id']}", headers=auth(coach)
    )

    assert response.status_code == 403
    assert await listed_ids(client, admin) == [created["id"]]


# R5 -----------------------------------------------------------------------


@pytest.mark.requirement("applications:R5")
@pytest.mark.asyncio
async def test_should_copy_any_template_into_a_new_draft_when_duplicated(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin)

    response = await client.post(
        f"{APPLICATIONS}/{published['id']}/duplicate", headers=auth(admin)
    )

    assert response.status_code == 201, response.text
    copy = (await get_application(client, admin, response.json()["id"])).json()
    assert copy["id"] != published["id"]
    assert copy["status"] == "draft"
    assert copy["layout"] is None
    for key in template_body():
        assert copy[key] == published[key], key
    source = (await get_application(client, admin, published["id"])).json()
    assert source["status"] == "published" and source["layout"] is not None


@pytest.mark.requirement("applications:R5")
@pytest.mark.asyncio
async def test_should_refuse_to_duplicate_when_caller_is_a_member(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin)
    member = await create_applicant(db_session, "ravi")
    await db_session.commit()

    response = await client.post(
        f"{APPLICATIONS}/{published['id']}/duplicate", headers=auth(member)
    )

    assert response.status_code == 403
    assert await listed_ids(client, admin) == [published["id"]]


# R10 ----------------------------------------------------------------------


@pytest.mark.requirement("applications:R10")
@pytest.mark.asyncio
async def test_should_list_every_state_for_admins_and_filter_by_status(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    regular = await create_regular_admin_user(db_session)
    draft = await create_application(client, admin)
    published = await published_application(client, admin)

    assert sorted(await listed_ids(client, regular)) == sorted(
        [draft["id"], published["id"]]
    )
    assert await listed_ids(client, admin, "?status=draft") == [draft["id"]]
    assert await listed_ids(client, admin, "?status=published") == [published["id"]]
    assert (await get_application(client, regular, draft["id"])).status_code == 200


@pytest.mark.requirement("applications:R10")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [UserStatus.registered, UserStatus.pending, UserStatus.active]
)
async def test_should_offer_published_ones_when_member_has_an_identity_document(
    client: AsyncClient, db_session: AsyncSession, status: UserStatus
):
    admin = await create_admin_user(db_session)
    draft = await create_application(client, admin)
    published = await published_application(client, admin)
    member = await create_applicant(db_session, "ravi", status=status)
    coach = await create_coach_user(db_session)
    await db_session.commit()

    assert await listed_ids(client, member) == [published["id"]]
    assert await listed_ids(client, member, "?status=draft") == [published["id"]]
    assert (await get_application(client, member, published["id"])).json()[
        "status"
    ] == "published"
    hidden = await get_application(client, member, draft["id"])
    assert hidden.status_code == 404
    assert hidden.json()["detail"]["code"] == "APPLICATION_NOT_FOUND"
    assert await listed_ids(client, coach) == []  # a coach without a document


@pytest.mark.requirement("applications:R10")
@pytest.mark.asyncio
async def test_should_offer_nothing_when_member_has_no_identity_document(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await create_admin_user(db_session)
    published = await published_application(client, admin)
    member = await create_applicant(db_session, "ravi", identity_document=False)
    await db_session.commit()

    assert await listed_ids(client, member) == []
    response = await get_application(client, member, published["id"])
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "IDENTITY_DOCUMENT_REQUIRED"


@pytest.mark.requirement("applications:R10")
@pytest.mark.asyncio
async def test_should_refuse_the_list_when_caller_is_anonymous(
    client: AsyncClient, db_session: AsyncSession
):
    _ = await create_admin_user(db_session)
    await db_session.commit()

    response = await client.get(APPLICATIONS)

    assert response.status_code == 401
