"""Evidence: media attached to one answer of an evaluation (#302, #535, R56a).

A link's tag is the id of the question it justifies. Only a question that
allows evidence takes it, and only images, videos and PDFs. The member sees
evidence exactly when they see its question: on public items of a
published evaluation.
"""

from typing import Any

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.evaluation import Evaluation, EvaluationMediaLink
from ..db.models.evaluation_template_item import EvaluationTemplateItem
from ..db.models.media import Media
from ..db.models.user import User
from ..exceptions import EvaluationEvidenceInvalidException, InvalidMediaTypeException
from ..schemas.evaluation import EvaluationEvidence
from ..utils import now_utc_ms
from .evaluation_items import is_question
from .media import MediaService
from .media_links import MediaLinkService

OWNER_TYPE = "evaluation"

# The stored member copy of a published evaluation (R63).
MEMBER_COPY_TAG = "member_copy"

EVIDENCE_MEDIA_TYPES = frozenset({"image", "video", "pdf"})

# An uploaded file of evidence is the member's, and staff's (R56d).
EVIDENCE_UPLOAD_ACCESS_ROLES = ["self", "coach", "admin"]


async def evidence_by_item(
    db: AsyncSession, evaluation_id: int
) -> dict[int, list[EvaluationEvidence]]:
    """Each question's evidence, keyed by item id, oldest attachment first."""
    result = await db.execute(
        select(EvaluationMediaLink)
        .where(EvaluationMediaLink.evaluation_id == evaluation_id)
        .order_by(EvaluationMediaLink.created_at, EvaluationMediaLink.media_uuid)
    )
    grouped: dict[int, list[EvaluationEvidence]] = {}
    for link in result.scalars().all():
        if link.tag.isdigit():
            grouped.setdefault(int(link.tag), []).append(
                EvaluationEvidence(
                    media_uuid=link.media_uuid, metadata=link.metadata_value
                )
            )
    return grouped


async def check_evidence_tag(
    db: AsyncSession, evaluation: Evaluation, tag: str
) -> None:
    """Raise unless the tag is a question of this evaluation that takes evidence."""
    item = await db.get(EvaluationTemplateItem, int(tag)) if tag.isdigit() else None
    if (
        item is None
        or item.template_id != evaluation.template_id
        or not is_question(item)
        or not item.element.get("allowEvidence", False)
    ):
        raise EvaluationEvidenceInvalidException(
            f"'{tag}' is not a question of this evaluation that takes evidence"
        )


async def check_evidence(
    db: AsyncSession, evaluation: Evaluation, tag: str, media_uuid: str
) -> None:
    """Raise unless the tag is an evidence-taking question and the file may be evidence."""
    await check_evidence_tag(db, evaluation, tag)
    media = (
        await db.execute(select(Media).where(Media.uuid == media_uuid))
    ).scalar_one_or_none()
    # A missing item is left to the link service, which answers MEDIA_NOT_FOUND.
    if media is not None and media.media_type not in EVIDENCE_MEDIA_TYPES:
        raise EvaluationEvidenceInvalidException(
            "evidence is an image, a video or a PDF"
        )


async def public_question_tags(db: AsyncSession, evaluation: Evaluation) -> set[str]:
    """What a member may see: the public questions' tags, and the member copy."""
    result = await db.execute(
        select(EvaluationTemplateItem.id).where(
            EvaluationTemplateItem.template_id == evaluation.template_id,
            EvaluationTemplateItem.is_private.is_(False),
        )
    )
    return {str(item_id) for item_id in result.scalars().all()} | {MEMBER_COPY_TAG}


async def list_member_visible_media(
    db: AsyncSession, evaluation: Evaluation, viewer: User
) -> dict[str, Any]:
    """The member's view of an evaluation's media, grouped by tag (R56a, R56b)."""
    grouped = await MediaLinkService(db, OWNER_TYPE).list_grouped(
        evaluation.id, viewer=viewer
    )
    visible = await public_question_tags(db, evaluation)
    return {tag: links for tag, links in grouped.items() if tag in visible}


async def upload_evidence(
    db: AsyncSession, evaluation: Evaluation, item_id: int, file: UploadFile
) -> str:
    """Store a file as evidence for one question and link it (R56d).

    The member the evaluation is about is the file's uploader, so the
    ``self`` role lets them download it beside staff; it is never public.
    Returns the new media uuid.
    """
    tag = str(item_id)
    await check_evidence_tag(db, evaluation, tag)
    try:
        media = await MediaService(db).create_media(
            file,
            uploaded_by=evaluation.created_for,
            preserve_original=True,
            access_roles=EVIDENCE_UPLOAD_ACCESS_ROLES,
        )
    except InvalidMediaTypeException as exc:
        raise EvaluationEvidenceInvalidException(
            "evidence is an image, a video or a PDF"
        ) from exc
    now = now_utc_ms()
    db.add(
        EvaluationMediaLink(
            evaluation_id=evaluation.id,
            media_uuid=media.uuid,
            tag=tag,
            metadata_value=file.filename,
            created_at=now,
            updated_at=now,
        )
    )
    await db.flush()
    return media.uuid
