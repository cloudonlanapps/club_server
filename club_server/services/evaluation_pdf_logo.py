"""The club's logo for the member copy PDF (#535, R63).

The logo is the one the public site shows: the ``logo`` slot of
``site_media``. A copy is still produced without it when none is set or its
file cannot be read; the title then stays centred.
"""

import io
import logging

from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from ..exceptions import DecryptionFailedException, EncryptionNotConfiguredException
from .club_info import ClubInfoService
from .evaluation_pdf_layout import LOGO_MEDIA_TYPE, LOGO_PURPOSE
from .media import MediaService, resolve_media_file_path

logger = logging.getLogger(__name__)


async def club_logo(db: AsyncSession) -> bytes | None:
    """The bytes of the site's logo image, or ``None``."""
    media = await ClubInfoService(db).site_media_item(LOGO_PURPOSE)
    if media is None or media.media_type != LOGO_MEDIA_TYPE:
        return None
    path = resolve_media_file_path(media)
    if not path.is_file():
        logger.warning("site logo %s has no file at %s", media.uuid, path)
        return None
    try:
        found = MediaService(db).resolve_download(media, path)
        data = found.content if found.content is not None else path.read_bytes()
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except (
        OSError,
        SyntaxError,
        ValueError,
        DecryptionFailedException,
        EncryptionNotConfiguredException,
    ) as error:
        logger.warning("site logo %s is unreadable: %s", media.uuid, error)
        return None
    return data
