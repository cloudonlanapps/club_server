"""The club's logo for the PDFs: evaluation member copies (#535, R63) and
application forms (applications R25a).

The logo is, in order (#135):

1. the one the public site shows, the ``logo`` slot of ``site_media``;
2. otherwise the email banner's logo file, ``<STATIC_DIR>/<EMAIL_LOGO_PATH>``;
3. otherwise none.

A file that is missing or not an image logs a warning and the next source
is tried. A PDF is still produced without a logo; the title then stays
centred.
"""

import io
import logging
from pathlib import Path

from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..exceptions import DecryptionFailedException, EncryptionNotConfiguredException
from ..mailer.config import email_settings
from .club_info import ClubInfoService
from .evaluation_pdf_layout import LOGO_MEDIA_TYPE, LOGO_PURPOSE
from .media import MediaService, resolve_media_file_path

logger = logging.getLogger(__name__)


async def club_logo(db: AsyncSession) -> bytes | None:
    """The bytes of the club's logo image, or ``None``."""
    return await site_logo(db) or email_logo()


async def site_logo(db: AsyncSession) -> bytes | None:
    """The uploaded site logo's bytes, or ``None``."""
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
        _verify(data)
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


def email_logo() -> bytes | None:
    """The email banner's logo file under the static directory, or ``None``
    when ``EMAIL_LOGO_PATH`` is unset or its file is missing or unreadable."""
    relative = email_settings.email_logo_path.lstrip("/")
    if not relative:
        return None
    path = Path(settings.static_dir) / relative
    if not path.is_file():
        logger.warning("email logo %s has no file at %s", relative, path)
        return None
    try:
        data = path.read_bytes()
        _verify(data)
    except (OSError, SyntaxError, ValueError) as error:
        logger.warning("email logo %s at %s is unreadable: %s", relative, path, error)
        return None
    return data


def _verify(data: bytes) -> None:
    """Raise unless Pillow can open ``data`` as an image."""
    with Image.open(io.BytesIO(data)) as image:
        image.verify()
