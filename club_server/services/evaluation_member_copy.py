"""The stored member copy of a published evaluation (#535, R63, R63b).

Publishing renders the member view to PDF and keeps it as an ordinary media
item, linked to the evaluation under ``member_copy``. The member it is
about is its uploader, so the ``self`` access role lets them download it,
and staff may too. Publishing again replaces it: the old file is detached
and soft-deleted, recoverable through the media module.
"""

from io import BytesIO

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from ..db.models.evaluation import Evaluation, EvaluationMediaLink
from ..utils import now_utc_ms
from .evaluation_media import MEMBER_COPY_TAG
from .evaluation_pdf import member_copy_pdf
from .evaluation_views import member_view
from .media import MediaService

MEMBER_COPY_ACCESS_ROLES = ["self", "coach", "admin"]
PDF_CONTENT_TYPE = "application/pdf"


async def retire_member_copies(db: AsyncSession, evaluation_id: int) -> None:
    """Detach and soft-delete every member copy of an evaluation."""
    result = await db.execute(
        select(EvaluationMediaLink).where(
            EvaluationMediaLink.evaluation_id == evaluation_id,
            EvaluationMediaLink.tag == MEMBER_COPY_TAG,
        )
    )
    media = MediaService(db)
    for link in result.scalars().all():
        old = await media.get_by_uuid(link.media_uuid)
        await db.delete(link)
        await db.flush()
        _ = await media.soft_delete(old.id)


async def store_member_copy(db: AsyncSession, evaluation: Evaluation) -> str:
    """Render, store and link the member copy of a published evaluation.

    Returns the new media uuid.
    """
    content = await member_copy_pdf(db, await member_view(db, evaluation))
    await retire_member_copies(db, evaluation.id)
    upload = UploadFile(
        file=BytesIO(content),
        filename=f"evaluation-{evaluation.id}.pdf",
        headers=Headers({"content-type": PDF_CONTENT_TYPE}),
    )
    stored = await MediaService(db).create_media(
        upload,
        uploaded_by=evaluation.created_for,
        preserve_original=True,
        access_roles=MEMBER_COPY_ACCESS_ROLES,
    )
    now = now_utc_ms()
    db.add(
        EvaluationMediaLink(
            evaluation_id=evaluation.id,
            media_uuid=stored.uuid,
            tag=MEMBER_COPY_TAG,
            metadata_value=None,
            created_at=now,
            updated_at=now,
        )
    )
    await db.flush()
    return stored.uuid
