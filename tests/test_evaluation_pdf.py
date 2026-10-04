"""The member copy of a published evaluation, as a PDF (#535, R63-R64).

Publishing stores it as media under the tag ``member_copy``; the owner can
preview it, unstored, at any status.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .evaluation_helpers import (
    auth,
    create_general_evaluation,
    create_template,
    item_ids,
    publish,
    put_answer,
    qa_item,
    rating_item,
)
from .evaluation_pdf_helpers import pdf_text
from .helpers import (
    create_admin_user,
    create_coach_user,
    create_member_user,
)
from .media_helpers import (
    clean_upload_dir,  # noqa: F401  (fixture, used by name)
    upload,
)

pytestmark = pytest.mark.usefixtures("evaluations_enabled", "clean_upload_dir")

MEMBER_COPY = "member_copy"


class _Setup:
    """Tokens, the evaluation, its public item id and its evidence's uuid."""

    def __init__(
        self,
        admin: str,
        coach: str,
        alice: str,
        evaluation_id: int,
        public_id: int,
        evidence_uuid: str,
    ):
        self.admin = admin
        self.coach = coach
        self.alice = alice
        self.evaluation_id = evaluation_id
        self.public_id = public_id
        self.evidence_uuid = evidence_uuid


async def _evaluation(
    client: AsyncClient, db_session: AsyncSession, *, published: bool = True
) -> _Setup:
    """An evaluation of alice answering a public rating (with a note and
    evidence) and a private Q & A; published unless asked otherwise."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    alice_token = await create_member_user(db_session, "alice")
    template_id = await create_template(
        client,
        admin_token,
        name="Spring review",
        layout=[
            {"section": "Skating", "items": [rating_item("Forward stride")]},
            qa_item("Private notes", isPrivate=True),
        ],
    )
    public_id, private_id = await item_ids(client, admin_token, template_id)
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    for item_id, body in (
        (public_id, {"valueNum": 4, "coachNote": "Long strides"}),
        (private_id, {"valueText": "Speak to the parents"}),
    ):
        written = await put_answer(client, coach_token, evaluation_id, item_id, **body)
        assert written.status_code == 200, written.text
    media = await upload(client, coach_token)
    attached = await client.post(
        f"/v1/evaluations/by_id/{evaluation_id}/media",
        json={"tag": str(public_id), "mediaUuid": media["uuid"]},
        headers=auth(coach_token),
    )
    assert attached.status_code == 201, attached.text
    if published:
        await publish(client, coach_token, evaluation_id)
    return _Setup(
        admin_token, coach_token, alice_token, evaluation_id, public_id, media["uuid"]
    )


async def _member_copies(
    client: AsyncClient, token: str, evaluation_id: int
) -> list[dict[str, Any]]:
    """The member-copy links the member's media view lists."""
    response = await client.get(
        f"/v1/myevaluations/by_id/alice/{evaluation_id}/media", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json().get(MEMBER_COPY, [])


async def _download(client: AsyncClient, token: str, media_uuid: str):
    return await client.get(
        f"/v1/media/by_id/{media_uuid}/download", headers=auth(token)
    )


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R63")
async def test_should_store_the_member_copy_when_published(
    client: AsyncClient, db_session: AsyncSession
):
    """R63: publishing stores a PDF of the member view, listed with its media."""
    s = await _evaluation(client, db_session)

    [link] = await _member_copies(client, s.alice, s.evaluation_id)
    response = await _download(client, s.alice, link["media"]["uuid"])

    assert response.status_code == 200, response.text
    assert response.content.startswith(b"%PDF")
    text = pdf_text(response.content)
    assert "Spring review" in text
    assert "Forward stride" in text
    assert "Long strides" in text


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R63")
async def test_should_leave_private_items_out_of_the_member_copy(
    client: AsyncClient, db_session: AsyncSession
):
    """R63: the stored copy is the member projection — no private item."""
    s = await _evaluation(client, db_session)

    [link] = await _member_copies(client, s.alice, s.evaluation_id)
    response = await _download(client, s.alice, link["media"]["uuid"])

    assert response.status_code == 200, response.text
    text = pdf_text(response.content)
    assert "Forward stride" in text
    assert "Private notes" not in text
    assert "Speak to the parents" not in text


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R63")
async def test_should_let_staff_and_the_member_download_but_not_another_member(
    client: AsyncClient, db_session: AsyncSession
):
    """R63: the member and staff may download the copy; another member may not."""
    s = await _evaluation(client, db_session)
    other_coach = await create_coach_user(db_session, "othercoach")
    bob = await create_member_user(db_session, "bob")
    [link] = await _member_copies(client, s.alice, s.evaluation_id)
    uuid = link["media"]["uuid"]

    allowed = [
        await _download(client, t, uuid) for t in (s.alice, other_coach, s.admin)
    ]
    refused = await _download(client, bob, uuid)

    assert [r.status_code for r in allowed] == [200, 200, 200]
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R63")
async def test_should_hide_the_member_copy_while_withdrawn(
    client: AsyncClient, db_session: AsyncSession
):
    """R63: withdrawn, the evaluation and its copy are gone from the member's view."""
    s = await _evaluation(client, db_session)
    withdrawn = await client.post(
        f"/v1/evaluations/by_id/{s.evaluation_id}/unpublish", headers=auth(s.coach)
    )
    assert withdrawn.status_code == 200, withdrawn.text

    response = await client.get(
        f"/v1/myevaluations/by_id/alice/{s.evaluation_id}/media", headers=auth(s.alice)
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "EVALUATION_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R63b")
async def test_should_replace_the_member_copy_when_published_again(
    client: AsyncClient, db_session: AsyncSession
):
    """R63b: a republish links a new copy and soft-deletes the old one."""
    s = await _evaluation(client, db_session)
    [old] = await _member_copies(client, s.alice, s.evaluation_id)
    for step in ("unpublish", "revert"):
        moved = await client.post(
            f"/v1/evaluations/by_id/{s.evaluation_id}/{step}", headers=auth(s.coach)
        )
        assert moved.status_code == 200, moved.text
    rewritten = await put_answer(
        client, s.coach, s.evaluation_id, s.public_id, valueNum=5, coachNote="Faster"
    )
    assert rewritten.status_code == 200, rewritten.text

    await publish(client, s.coach, s.evaluation_id)

    [new] = await _member_copies(client, s.alice, s.evaluation_id)
    assert new["media"]["uuid"] != old["media"]["uuid"]
    current = await _download(client, s.alice, new["media"]["uuid"])
    assert "Faster" in pdf_text(current.content)
    gone = await _download(client, s.alice, old["media"]["uuid"])
    assert gone.status_code == 404


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R63b")
async def test_should_retire_the_member_copy_when_the_evaluation_is_hard_deleted(
    client: AsyncClient, db_session: AsyncSession
):
    """R63b: a copy is never left behind live, not even by a hard delete."""
    s = await _evaluation(client, db_session)
    [link] = await _member_copies(client, s.alice, s.evaluation_id)
    base = f"/v1/evaluations/by_id/{s.evaluation_id}"
    for step in ("unpublish", "revert"):
        moved = await client.post(f"{base}/{step}", headers=auth(s.coach))
        assert moved.status_code == 200, moved.text
    soft = await client.delete(base, headers=auth(s.coach))
    assert soft.status_code == 200, soft.text

    hard = await client.delete(f"{base}/hard", headers=auth(s.admin))

    assert hard.status_code == 204, hard.text
    gone = await _download(client, s.admin, link["media"]["uuid"])
    assert gone.status_code == 404


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R64")
async def test_should_link_each_file_of_evidence_from_the_pdf(
    client: AsyncClient, db_session: AsyncSession
):
    """R64: the word Evidence links to each file of an answer."""
    s = await _evaluation(client, db_session)

    [link] = await _member_copies(client, s.alice, s.evaluation_id)
    response = await _download(client, s.alice, link["media"]["uuid"])

    assert response.status_code == 200, response.text
    assert "Evidence" in pdf_text(response.content)
    target = f"/v1/media/by_id/{s.evidence_uuid}/download".encode()
    assert b"/URI" in response.content
    assert target in response.content


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R63a")
@pytest.mark.parametrize("published", [False, True], ids=["draft", "published"])
async def test_should_let_the_owner_preview_the_member_copy_unstored(
    client: AsyncClient, db_session: AsyncSession, published: bool
):
    """R63a: the owner previews the member copy at any status; nothing is stored."""
    s = await _evaluation(client, db_session, published=published)
    before = await client.get(
        f"/v1/evaluations/by_id/{s.evaluation_id}/media", headers=auth(s.coach)
    )

    response = await client.get(
        f"/v1/evaluations/by_id/{s.evaluation_id}/pdf", headers=auth(s.coach)
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    text = pdf_text(response.content)
    assert "Forward stride" in text
    assert "Speak to the parents" not in text
    after = await client.get(
        f"/v1/evaluations/by_id/{s.evaluation_id}/media", headers=auth(s.coach)
    )
    assert after.json() == before.json()


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R63a")
async def test_should_hide_the_preview_from_anyone_but_the_owner(
    client: AsyncClient, db_session: AsyncSession
):
    """R63a, R38a: for another coach or an admin there is nothing to preview."""
    s = await _evaluation(client, db_session, published=False)
    other_coach = await create_coach_user(db_session, "othercoach")

    for token in (other_coach, s.admin):
        response = await client.get(
            f"/v1/evaluations/by_id/{s.evaluation_id}/pdf", headers=auth(token)
        )
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "EVALUATION_NOT_FOUND"
