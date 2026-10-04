"""MIME-type resolution for uploaded media (#331).

The content type on a multipart part is whatever the client chose to put
there: it may be absent, generic (``curl -F`` sends
``application/octet-stream`` for any extension it does not recognise), or
simply wrong. Storing it verbatim leaves ``Media.mime_type`` disagreeing
with the bytes on disk and with the type the download endpoint serves.

Resolution order, most to least authoritative:

1. the file's own magic bytes,
2. the declared content type, when present and not generic,
3. the filename extension,
4. ``application/octet-stream`` as a last resort.
"""

from pathlib import Path


GENERIC_MIME_TYPES = frozenset(
    {
        "application/octet-stream",
        "binary/octet-stream",
    }
)
"""Declared types that carry no information and must not be persisted."""

FALLBACK_MIME_TYPE = "application/octet-stream"

SNIFF_HEADER_BYTES = 64
"""How much of the file the signature checks need."""

MIME_BY_EXTENSION: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".pdf": "application/pdf",
}

_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"%PDF-", "application/pdf"),
)

_QUICKTIME_BRANDS = frozenset({b"qt  "})
_MP4_BRAND_PREFIXES = (b"isom", b"iso2", b"mp4", b"avc1", b"M4V", b"M4A", b"dash")


def _sniff_riff(head: bytes) -> str | None:
    """RIFF containers: the form type at offset 8 says which one."""
    form = head[8:12]
    if form == b"WEBP":
        return "image/webp"
    if form == b"AVI ":
        return "video/x-msvideo"
    return None


def _sniff_isobmff(head: bytes) -> str | None:
    """ISO base media containers (``ftyp`` box): MP4, M4V and QuickTime."""
    brand = head[8:12]
    if brand in _QUICKTIME_BRANDS:
        return "video/quicktime"
    if brand.startswith(b"M4V"):
        return "video/x-m4v"
    if any(brand.startswith(prefix) for prefix in _MP4_BRAND_PREFIXES):
        return "video/mp4"
    return None


def _sniff_ebml(head: bytes) -> str | None:
    """Matroska and WebM share the EBML magic; the DocType separates them."""
    if b"webm" in head:
        return "video/webm"
    if b"matroska" in head:
        return "video/x-matroska"
    return None


def sniff_mime_type(head: bytes) -> str | None:
    """Identify a media type from a file's leading bytes.

    Returns ``None`` when the signature is unknown or too ambiguous to name
    a single type, so the caller can fall back to weaker signals.
    """
    for signature, mime in _SIGNATURES:
        if head.startswith(signature):
            return mime
    if head.startswith(b"RIFF"):
        return _sniff_riff(head)
    if head[4:8] == b"ftyp":
        return _sniff_isobmff(head)
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return _sniff_ebml(head)
    return None


# Last-resort type per media type, so a stored row's MIME always implies its
# media type and clients need only the one field (#426).
_FALLBACK_BY_MEDIA_TYPE = {
    "image": "image/jpeg",
    "video": "video/mp4",
    "pdf": "application/pdf",
}


def resolve_mime_type(
    filename: str, declared: str | None, content: bytes, media_type: str | None = None
) -> str:
    """Resolve the MIME type to persist for an uploaded file.

    ``declared`` is the client-supplied content type and is treated as a
    hint: it loses to the file's own magic bytes, and it is ignored
    entirely when generic. See the module docstring for the full order.

    ``media_type`` is ``classify_media``'s answer, which is reached by a
    different route (the extension) and is therefore sometimes known when
    everything here has failed. Given it, the fallback is a real type rather
    than ``application/octet-stream``: a client reads the renderer off the
    MIME type alone, so a generic value would leave it guessing again (#426).
    """
    sniffed = sniff_mime_type(content[:SNIFF_HEADER_BYTES])
    if sniffed is not None:
        return sniffed
    if declared and declared.lower() not in GENERIC_MIME_TYPES:
        return declared
    extension_mime = MIME_BY_EXTENSION.get(Path(filename).suffix.lower())
    if extension_mime is not None:
        return extension_mime
    if media_type is not None:
        return _FALLBACK_BY_MEDIA_TYPE.get(media_type, FALLBACK_MIME_TYPE)
    return FALLBACK_MIME_TYPE
