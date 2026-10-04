"""Shared media-pipeline helpers used by the v2 ``services.media`` module.

Extracted from the retired v1 ``services.upload`` so the v2 stack stays
independent of the legacy module (#182). Covers MIME / extension
classification, the ``access_roles`` validator, and the synchronous
image / PDF conversion shell-outs.
"""

import asyncio
from pathlib import Path

from ..config import settings
from ..exceptions import (
    InvalidAccessRolesException,
    InvalidMediaTypeException,
    MediaConversionFailedException,
)


ALLOWED_ACCESS_ROLES = {"public", "self", "admin", "coach"}
DEFAULT_ACCESS_ROLES: list[str] = ["public"]

ALLOWED_IMAGE_MIMES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/bmp",
    "image/tiff",
    "image/webp",
}
ALLOWED_VIDEO_MIMES = {
    "video/mp4",
    "video/quicktime",
    "video/x-msvideo",
    "video/x-matroska",
    "video/webm",
    "video/x-m4v",
}
ALLOWED_PDF_MIMES = {"application/pdf"}
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp"}
ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
ALLOWED_PDF_EXTS = {".pdf"}


def normalize_access_roles(raw: list[str] | None) -> list[str]:
    """Validate + normalize an ``access_roles`` array.

    - ``None`` returns the application default (``["public"]``).
    - Unknown strings raise ``InvalidAccessRolesException`` (422).
    - Empty array raises (almost certainly a client bug — the file would be
      unreachable).
    - If ``"public"`` is present, collapse to exactly ``["public"]``.
    - Otherwise deduplicate while preserving order.
    """
    if raw is None:
        return list(DEFAULT_ACCESS_ROLES)
    if not isinstance(raw, list):
        raise InvalidAccessRolesException(
            "access_roles must be a JSON array of strings"
        )
    if len(raw) == 0:
        raise InvalidAccessRolesException("access_roles cannot be empty")
    unknown = [r for r in raw if r not in ALLOWED_ACCESS_ROLES]
    if unknown:
        raise InvalidAccessRolesException(
            f"Unknown access role(s): {unknown}. Allowed: {sorted(ALLOWED_ACCESS_ROLES)}",
        )
    if "public" in raw:
        return ["public"]
    seen: set[str] = set()
    out: list[str] = []
    for r in raw:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def get_script_path() -> str:
    """Return absolute path to the media conversion script."""
    if settings.media_convert_script:
        return settings.media_convert_script
    return str(Path(__file__).resolve().parent.parent / "scripts" / "media_convert.sh")


def classify_media(filename: str, content_type: str | None) -> tuple[str, str]:
    """Classify file as image, video, or pdf. Returns ``(media_type, extension)``.

    Raises ``InvalidMediaTypeException`` if unsupported.
    """
    ext = Path(filename).suffix.lower()

    if ext in ALLOWED_IMAGE_EXTS:
        return "image", ext.lstrip(".")
    if ext in ALLOWED_VIDEO_EXTS:
        return "video", ext.lstrip(".")
    if ext in ALLOWED_PDF_EXTS:
        return "pdf", ext.lstrip(".")

    if content_type:
        if content_type in ALLOWED_IMAGE_MIMES:
            return "image", ext.lstrip(".") if ext else "bin"
        if content_type in ALLOWED_VIDEO_MIMES:
            return "video", ext.lstrip(".") if ext else "bin"
        if content_type in ALLOWED_PDF_MIMES:
            return "pdf", ext.lstrip(".") if ext else "pdf"

    raise InvalidMediaTypeException(content_type or ext or "unknown")


async def _run_converter(input_path: Path, output_path: Path) -> tuple[int, str]:
    """Run the conversion script; return its exit code and stderr."""
    proc = await asyncio.create_subprocess_exec(
        "bash",
        get_script_path(),
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--force",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    return proc.returncode or 0, stderr.decode().strip()


async def run_image_conversion(input_path: Path, output_path: Path) -> None:
    """Convert an image to WebP.

    Raises ``MediaConversionFailedException`` when the converter fails (#521).
    """
    code, stderr = await _run_converter(input_path, output_path)
    if code != 0:
        raise MediaConversionFailedException("image", f"exit {code}: {stderr}")


def pdf_poster_path(output_path: Path) -> Path:
    """Where the converter writes a PDF's first-page poster."""
    return output_path.with_name(f"{output_path.stem}_poster.png")


async def run_pdf_conversion(input_path: Path, output_path: Path) -> None:
    """Copy a PDF and render its first page as a poster PNG.

    Raises ``MediaConversionFailedException`` when the converter fails, or
    when it reports success without rendering a poster: a ``%PDF`` header
    over garbage makes the renderer exit 0 having drawn nothing (#521).
    """
    code, stderr = await _run_converter(input_path, output_path)
    if code != 0:
        raise MediaConversionFailedException("pdf", f"exit {code}: {stderr}")
    poster = pdf_poster_path(output_path)
    if not poster.exists() or poster.stat().st_size == 0:
        raise MediaConversionFailedException("pdf", "no first page could be rendered")
