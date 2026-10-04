"""Evidence attached to evaluations (#302, #535, R55, R56, R56a, R56b).

Evidence is media linked to one answer: its tag is the question's id.
"""

import os
import shutil
import struct
import tempfile
import zlib

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from .evaluation_helpers import (
    answer_of,
    auth,
    create_general_evaluation,
    create_template,
    get_evaluation,
    item_ids,
    publish,
    put_answer,
    qa_item,
    rating_item,
)
from .helpers import (
    create_admin_user,
    create_coach_user,
    create_media_row,
    create_member_user,
)

pytestmark = pytest.mark.usefixtures("evaluations_enabled")


def _png_bytes() -> bytes:
    """Smallest valid PNG the upload pipeline will accept."""
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data)
    ihdr = (
        struct.pack(">I", 13)
        + b"IHDR"
        + ihdr_data
        + struct.pack(">I", ihdr_crc & 0xFFFFFFFF)
    )
    raw = b"\x00\xff\xff\xff"
    comp = zlib.compress(raw)
    idat_crc = zlib.crc32(b"IDAT" + comp)
    idat = (
        struct.pack(">I", len(comp))
        + b"IDAT"
        + comp
        + struct.pack(">I", idat_crc & 0xFFFFFFFF)
    )
    iend_crc = zlib.crc32(b"IEND")
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc & 0xFFFFFFFF)
    return signature + ihdr + idat + iend


@pytest.fixture(autouse=True)
def clean_upload_dir():
    """Uploads land on disk; clear them between tests."""
    upload_dir = os.environ.get("UPLOAD_DIR", "/tmp/club_server_test_uploads")
    os.makedirs(upload_dir, exist_ok=True)
    yield
    shutil.rmtree(upload_dir, ignore_errors=True)
    tmp_base = tempfile.gettempdir()
    for entry in os.listdir(tmp_base):
        if entry.startswith("club_media_") or entry.startswith("club_upload_"):
            shutil.rmtree(os.path.join(tmp_base, entry), ignore_errors=True)


async def _upload(client: AsyncClient, token: str) -> dict[str, object]:
    """Upload a media file.

    Returns the whole record: links are keyed by ``uuid`` while the media
    module's own admin routes are keyed by the integer ``id``.
    """
    response = await client.post(
        "/v1/media",
        files={"file": ("clip.png", _png_bytes(), "image/png")},
        data={"preserveOriginal": "true"},
        headers=auth(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


class _Setup:
    """Tokens, the evaluation, its item tags, and one uploaded media record."""

    def __init__(
        self,
        admin: str,
        coach: str,
        alice: str,
        evaluation_id: int,
        tags: list[str],
        media: dict[str, object],
    ):
        self.admin = admin
        self.coach = coach
        self.alice = alice
        self.evaluation_id = evaluation_id
        self.public_tag, self.private_tag, self.no_evidence_tag = tags
        self.media = media

    @property
    def base(self) -> str:
        return f"/v1/evaluations/by_id/{self.evaluation_id}/media"


async def _setup(client: AsyncClient, db_session: AsyncSession) -> _Setup:
    """A draft whose template has a public and a private question taking
    evidence, and a question that does not."""
    admin_token = await create_admin_user(db_session)
    coach_token = await create_coach_user(db_session, "coach")
    alice_token = await create_member_user(db_session, "alice")
    template_id = await create_template(
        client,
        admin_token,
        layout=[
            rating_item("Skating"),
            qa_item("Private notes", isPrivate=True, allowEvidence=True),
            rating_item("Balance", allowEvidence=False),
        ],
    )
    tags = [str(i) for i in await item_ids(client, admin_token, template_id)]
    evaluation_id = await create_general_evaluation(
        client, coach_token, template_id, "alice"
    )
    media = await _upload(client, coach_token)
    return _Setup(admin_token, coach_token, alice_token, evaluation_id, tags, media)


async def _attach(client: AsyncClient, s: _Setup, tag: str, token: str | None = None):
    return await client.post(
        s.base,
        json={"tag": tag, "mediaUuid": s.media["uuid"], "metadata": "drill 3"},
        headers=auth(token or s.coach),
    )


@pytest.mark.asyncio
@pytest.mark.requirement("media:R95")
@pytest.mark.requirement("evaluation:R55")
@pytest.mark.requirement("evaluation:R56")
@pytest.mark.requirement("evaluation:R56a")
async def test_should_attach_evidence_to_an_answer(
    client: AsyncClient, db_session: AsyncSession
):
    """R56, R56a: evidence is media tagged with the question it justifies."""
    s = await _setup(client, db_session)

    response = await _attach(client, s, s.public_tag)

    assert response.status_code == 201, response.text
    listed = await client.get(s.base, headers=auth(s.coach))
    assert listed.status_code == 200
    assert s.public_tag in listed.json()
    read = await get_evaluation(client, s.coach, s.evaluation_id)
    evidence = answer_of(read, int(s.public_tag))
    assert evidence is not None
    assert [e["mediaUuid"] for e in evidence["evidence"]] == [s.media["uuid"]]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R95")
@pytest.mark.requirement("evaluation:R55")
async def test_should_reject_duplicate_attachment_of_same_media_and_tag(
    client: AsyncClient, db_session: AsyncSession
):
    """The link table's primary key is (evaluation, media, tag)."""
    s = await _setup(client, db_session)
    first = await _attach(client, s, s.public_tag)
    assert first.status_code == 201

    response = await _attach(client, s, s.public_tag)

    assert response.status_code == 409
    listed = await client.get(s.base, headers=auth(s.coach))
    assert len(listed.json()[s.public_tag]) == 1


@pytest.mark.asyncio
@pytest.mark.requirement("media:R95")
@pytest.mark.requirement("evaluation:R55")
async def test_should_update_attachment_metadata(
    client: AsyncClient, db_session: AsyncSession
):
    """Metadata on a link is editable without detaching and re-attaching."""
    s = await _setup(client, db_session)
    attached = await _attach(client, s, s.public_tag)
    assert attached.status_code == 201

    response = await client.patch(
        f"{s.base}/{s.public_tag}/{s.media['uuid']}",
        json={"metadata": "after"},
        headers=auth(s.coach),
    )

    assert response.status_code == 200
    read = await client.get(
        f"{s.base}/{s.public_tag}/{s.media['uuid']}", headers=auth(s.coach)
    )
    assert read.status_code == 200
    assert read.json()["metadata"] == "after"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R95")
@pytest.mark.requirement("evaluation:R55")
async def test_should_detach_media_from_evaluation(
    client: AsyncClient, db_session: AsyncSession
):
    """Detaching removes the link and leaves the media itself alone."""
    s = await _setup(client, db_session)
    attached = await _attach(client, s, s.public_tag)
    assert attached.status_code == 201

    response = await client.delete(
        f"{s.base}/{s.public_tag}/{s.media['uuid']}", headers=auth(s.coach)
    )

    assert response.status_code == 204
    listed = await client.get(s.base, headers=auth(s.coach))
    assert listed.json() == {}
    media_still_there = await client.get(
        f"/v1/media/by_id/{s.media['id']}", headers=auth(s.coach)
    )
    assert media_still_there.status_code == 200


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R56a")
@pytest.mark.parametrize("which", ["not_a_question", "no_evidence"])
async def test_should_refuse_evidence_on_a_tag_that_does_not_take_it(
    client: AsyncClient, db_session: AsyncSession, which: str
):
    """R56a: the tag is a question of the template that allows evidence."""
    s = await _setup(client, db_session)
    tag = "clip" if which == "not_a_question" else s.no_evidence_tag

    response = await _attach(client, s, tag)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_EVIDENCE"
    listed = await client.get(s.base, headers=auth(s.coach))
    assert listed.json() == {}


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R56a")
async def test_should_refuse_evidence_that_is_not_an_image_video_or_pdf(
    client: AsyncClient, db_session: AsyncSession
):
    """R56a: evidence is images, videos and PDFs only."""
    s = await _setup(client, db_session)
    other_uuid = await create_media_row(
        db_session, uploaded_by="coach", public=False, media_type="document"
    )

    response = await client.post(
        s.base,
        json={"tag": s.public_tag, "mediaUuid": other_uuid},
        headers=auth(s.coach),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_EVIDENCE"
    listed = await client.get(s.base, headers=auth(s.coach))
    assert listed.json() == {}


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R56a")
async def test_should_refuse_evidence_once_the_evaluation_is_saved(
    client: AsyncClient, db_session: AsyncSession
):
    """R56a: evidence is draft content, like the answer it justifies."""
    s = await _setup(client, db_session)
    saved = await client.post(
        f"/v1/evaluations/by_id/{s.evaluation_id}/save", headers=auth(s.coach)
    )
    assert saved.status_code == 200, saved.text

    response = await _attach(client, s, s.public_tag)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    listed = await client.get(s.base, headers=auth(s.coach))
    assert listed.json() == {}


@pytest.mark.asyncio
@pytest.mark.requirement("media:R96")
@pytest.mark.requirement("evaluation:R35")
async def test_should_hide_evaluation_from_another_coach_attaching_media(
    client: AsyncClient, db_session: AsyncSession
):
    """R35: for a coach who does not own it, the evaluation does not exist."""
    s = await _setup(client, db_session)
    other_token = await create_coach_user(db_session, "othercoach")

    response = await _attach(client, s, s.public_tag, token=other_token)

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "EVALUATION_NOT_FOUND"
    listed = await client.get(s.base, headers=auth(s.coach))
    assert listed.json() == {}


@pytest.mark.asyncio
@pytest.mark.requirement("media:R96")
@pytest.mark.requirement("evaluation:R45")
async def test_should_reject_plain_member_attaching_media(
    client: AsyncClient, db_session: AsyncSession
):
    """R45: a member cannot write to an assessment about themselves."""
    s = await _setup(client, db_session)

    response = await _attach(client, s, s.public_tag, token=s.alice)

    assert response.status_code == 403
    listed = await client.get(s.base, headers=auth(s.coach))
    assert listed.json() == {}


@pytest.mark.asyncio
@pytest.mark.requirement("media:R96")
async def test_should_return_404_when_attaching_to_missing_evaluation(
    client: AsyncClient, db_session: AsyncSession
):
    """The owner must exist before a link to it can."""
    s = await _setup(client, db_session)

    response = await client.post(
        "/v1/evaluations/by_id/999999/media",
        json={"tag": s.public_tag, "mediaUuid": s.media["uuid"]},
        headers=auth(s.coach),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.requirement("media:R47")
@pytest.mark.requirement("evaluation:R55")
async def test_should_block_media_deletion_while_attached_to_evaluation(
    client: AsyncClient, db_session: AsyncSession
):
    """The MEDIA_IN_USE guard must count evaluation links like every other owner."""
    s = await _setup(client, db_session)
    attached = await _attach(client, s, s.public_tag)
    assert attached.status_code == 201

    response = await client.delete(
        f"/v1/media/by_id/{s.media['id']}", headers=auth(s.admin)
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "MEDIA_IN_USE"


async def _published_with_evidence(client: AsyncClient, s: _Setup) -> None:
    """Attach the media to the public and the private question, then publish."""
    for tag in (s.public_tag, s.private_tag):
        attached = await _attach(client, s, tag)
        assert attached.status_code == 201, attached.text
    await publish(client, s.coach, s.evaluation_id)


@pytest.mark.asyncio
@pytest.mark.requirement("media:R100")
@pytest.mark.requirement("evaluation:R56a")
async def test_should_show_evidence_on_a_public_question_to_the_member(
    client: AsyncClient, db_session: AsyncSession
):
    """R56a: evidence reaches the member exactly when its question does."""
    s = await _setup(client, db_session)
    await _published_with_evidence(client, s)

    media = await client.get(
        f"/v1/myevaluations/by_id/alice/{s.evaluation_id}/media",
        headers=auth(s.alice),
    )
    body = await client.get(
        f"/v1/myevaluations/by_id/alice/{s.evaluation_id}", headers=auth(s.alice)
    )

    assert media.status_code == 200, media.text
    assert sorted(media.json()) == sorted(["member_copy", s.public_tag])
    evidence = answer_of(body.json(), int(s.public_tag))
    assert evidence is not None
    assert [e["mediaUuid"] for e in evidence["evidence"]] == [s.media["uuid"]]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R101")
@pytest.mark.requirement("evaluation:R56a")
async def test_should_hide_evidence_on_a_private_question_from_the_member(
    client: AsyncClient, db_session: AsyncSession
):
    """R56a: evidence on a private item never reaches the member; the owner sees it."""
    s = await _setup(client, db_session)
    await _published_with_evidence(client, s)

    member_view = await client.get(
        f"/v1/myevaluations/by_id/alice/{s.evaluation_id}/media",
        headers=auth(s.alice),
    )
    staff_view = await client.get(s.base, headers=auth(s.coach))

    assert member_view.status_code == 200
    assert s.private_tag not in member_view.json()
    assert staff_view.status_code == 200
    assert sorted(staff_view.json()) == sorted(
        [s.public_tag, s.private_tag, "member_copy"]
    )


@pytest.mark.asyncio
@pytest.mark.requirement("media:R102")
@pytest.mark.requirement("evaluation:R56b")
async def test_should_hide_media_from_subject_before_publication(
    client: AsyncClient, db_session: AsyncSession
):
    """R56b: an unpublished evaluation does not exist for its member."""
    s = await _setup(client, db_session)
    attached = await _attach(client, s, s.public_tag)
    assert attached.status_code == 201

    response = await client.get(
        f"/v1/myevaluations/by_id/alice/{s.evaluation_id}/media",
        headers=auth(s.alice),
    )

    assert response.status_code == 404
    # Assert the domain code, not just the status: a missing route also
    # answers 404, so a bare status check would pass without the endpoint.
    assert response.json()["detail"]["code"] == "EVALUATION_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.requirement("media:R102")
@pytest.mark.requirement("evaluation:R40")
async def test_should_reject_another_member_reading_evaluation_media(
    client: AsyncClient, db_session: AsyncSession
):
    """R40: a member reads only their own evaluations, media included."""
    s = await _setup(client, db_session)
    bob_token = await create_member_user(db_session, "bob")
    await _published_with_evidence(client, s)

    response = await client.get(
        f"/v1/myevaluations/by_id/alice/{s.evaluation_id}/media",
        headers=auth(bob_token),
    )

    assert response.status_code == 403


async def _attached(client: AsyncClient, db_session: AsyncSession):
    """``_setup`` with the media attached to the public question, and a second coach."""
    s = await _setup(client, db_session)
    attached = await _attach(client, s, s.public_tag)
    assert attached.status_code == 201, attached.text
    other_token = await create_coach_user(db_session, "othercoach")
    await db_session.commit()
    return s, other_token


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["", "/{tag}", "/{tag}/{uuid}"],
    ids=["grouped", "by_tag", "one"],
)
@pytest.mark.parametrize("reader", ["othercoach", "admin"])
@pytest.mark.requirement("media:R99")
@pytest.mark.requirement("evaluation:R35")
async def test_should_hide_evaluation_media_from_anyone_but_the_owner(
    client: AsyncClient, db_session: AsyncSession, path: str, reader: str
):
    """R35, R38a: another coach or an admin finds no evaluation to read media from."""
    s, other_token = await _attached(client, db_session)
    token = other_token if reader == "othercoach" else s.admin

    response = await client.get(
        s.base + path.format(tag=s.public_tag, uuid=s.media["uuid"]),
        headers=auth(token),
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "EVALUATION_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["", "/{tag}", "/{tag}/{uuid}"],
    ids=["grouped", "by_tag", "one"],
)
@pytest.mark.requirement("media:R98")
@pytest.mark.requirement("evaluation:R35")
async def test_should_read_evaluation_media_when_owner(
    client: AsyncClient, db_session: AsyncSession, path: str
):
    """R35: the effective owner reads the media."""
    s, _ = await _attached(client, db_session)

    response = await client.get(
        s.base + path.format(tag=s.public_tag, uuid=s.media["uuid"]),
        headers=auth(s.coach),
    )

    assert response.status_code == 200, response.text
    assert str(s.media["uuid"]) in response.text


async def _attach_evaluation_and_venue_media(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[str, int]:
    """One evaluation link and one venue link. Returns (admin token, evaluation id)."""
    from .evaluation_helpers import create_venue

    s = await _setup(client, db_session)
    attached = await _attach(client, s, s.public_tag)
    assert attached.status_code == 201, attached.text
    venue_id = await create_venue(client, s.admin)
    venue_media = await _upload(client, s.admin)
    banner = await client.post(
        f"/v1/venues/by_id/{venue_id}/media",
        json={"tag": "banner", "mediaUuid": venue_media["uuid"]},
        headers=auth(s.admin),
    )
    assert banner.status_code == 201, banner.text
    return s.admin, s.evaluation_id


@pytest.mark.asyncio
@pytest.mark.requirement("media:R103")
async def test_should_filter_link_search_by_evaluation_owner_type(
    client: AsyncClient, db_session: AsyncSession
):
    """#489: evaluation links can be narrowed to, not only seen unfiltered."""
    admin_token, evaluation_id = await _attach_evaluation_and_venue_media(
        client, db_session
    )

    response = await client.get(
        "/v1/media/links", params={"ownerType": "evaluation"}, headers=auth(admin_token)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert [(i["ownerType"], i["ownerId"]) for i in body["items"]] == [
        ("evaluation", str(evaluation_id))
    ]


@pytest.mark.asyncio
@pytest.mark.requirement("media:R103")
async def test_should_filter_link_search_by_evaluation_when_module_is_off(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """#489: leftover evaluation media stays findable after the module is switched off."""
    from club_server.config import settings

    admin_token, evaluation_id = await _attach_evaluation_and_venue_media(
        client, db_session
    )
    monkeypatch.setattr(settings, "evaluations_enabled", False)

    unfiltered = await client.get("/v1/media/links", headers=auth(admin_token))
    filtered = await client.get(
        "/v1/media/links", params={"ownerType": "evaluation"}, headers=auth(admin_token)
    )

    assert unfiltered.status_code == 200
    assert sorted(i["ownerType"] for i in unfiltered.json()["items"]) == [
        "evaluation",
        "venue",
    ]
    assert filtered.status_code == 200, filtered.text
    assert [(i["ownerType"], i["ownerId"]) for i in filtered.json()["items"]] == [
        ("evaluation", str(evaluation_id))
    ]


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R56a")
async def test_should_not_let_an_answer_put_detach_existing_evidence(
    client: AsyncClient, db_session: AsyncSession
):
    """R56a: replacing an answer's value keeps the evidence linked to it."""
    s = await _setup(client, db_session)
    attached = await _attach(client, s, s.public_tag)
    assert attached.status_code == 201

    response = await put_answer(
        client, s.coach, s.evaluation_id, int(s.public_tag), valueNum=3
    )

    assert response.status_code == 200, response.text
    answer = answer_of(response.json(), int(s.public_tag))
    assert answer is not None
    assert [e["mediaUuid"] for e in answer["evidence"]] == [s.media["uuid"]]


async def _upload_evidence(
    client: AsyncClient, s: _Setup, tag: str, token: str | None = None
):
    return await client.post(
        f"/v1/evaluations/by_id/{s.evaluation_id}/evidence/{tag}",
        files={"file": ("clip.png", _png_bytes(), "image/png")},
        headers=auth(token or s.coach),
    )


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R56d")
async def test_should_store_uploaded_evidence_for_the_member_and_staff_only(
    client: AsyncClient, db_session: AsyncSession
):
    """R56d: an uploaded file is the member's, readable by them and staff, never public."""
    s = await _setup(client, db_session)
    other_coach = await create_coach_user(db_session, "othercoach")
    bob = await create_member_user(db_session, "bob")

    response = await _upload_evidence(client, s, s.public_tag)

    assert response.status_code == 201, response.text
    answer = answer_of(response.json(), int(s.public_tag))
    assert answer is not None
    [evidence] = answer["evidence"]
    uuid = evidence["mediaUuid"]
    await publish(client, s.coach, s.evaluation_id)
    downloads = {
        who: await client.get(f"/v1/media/by_id/{uuid}/download", headers=auth(t))
        for who, t in (("alice", s.alice), ("othercoach", other_coach), ("bob", bob))
    }
    anonymous = await client.get(f"/v1/media/by_id/{uuid}/download")
    assert downloads["alice"].status_code == 200
    assert downloads["othercoach"].status_code == 200
    assert downloads["bob"].status_code == 403
    assert anonymous.status_code == 401


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R56d")
async def test_should_refuse_uploaded_evidence_where_evidence_is_not_taken(
    client: AsyncClient, db_session: AsyncSession
):
    """R56d: the R56a tag rule holds for uploads, and nothing is stored on refusal."""
    s = await _setup(client, db_session)

    response = await _upload_evidence(client, s, s.no_evidence_tag)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_EVIDENCE"
    listed = await client.get(s.base, headers=auth(s.coach))
    assert listed.json() == {}


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R56d")
async def test_should_refuse_uploaded_evidence_on_a_saved_evaluation(
    client: AsyncClient, db_session: AsyncSession
):
    """R56d: uploads are draft content, like any evidence."""
    s = await _setup(client, db_session)
    saved = await client.post(
        f"/v1/evaluations/by_id/{s.evaluation_id}/save", headers=auth(s.coach)
    )
    assert saved.status_code == 200, saved.text

    response = await _upload_evidence(client, s, s.public_tag)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_STATE"
    listed = await client.get(s.base, headers=auth(s.coach))
    assert listed.json() == {}


@pytest.mark.asyncio
@pytest.mark.requirement("evaluation:R56d")
async def test_should_hide_the_evaluation_from_another_coach_uploading_evidence(
    client: AsyncClient, db_session: AsyncSession
):
    """R56d, R35: only the effective owner uploads."""
    s = await _setup(client, db_session)
    other_coach = await create_coach_user(db_session, "othercoach")
    await db_session.commit()

    response = await _upload_evidence(client, s, s.public_tag, token=other_coach)

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "EVALUATION_NOT_FOUND"
    listed = await client.get(s.base, headers=auth(s.coach))
    assert listed.json() == {}
