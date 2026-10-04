"""Issue 249: the media converter must accept every image format the API does.

`POST /v1/media` 500'd for webp because `ALLOWED_IMAGE_EXTS` (the API allow-list)
included `.webp` while `media_convert.sh`'s image case did not — so the upload
was classified as an image, then the converter rejected it (exit 1). These tests
pin the two together and exercise the real conversion.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from club_server.services.media_pipeline import ALLOWED_IMAGE_EXTS, get_script_path


def _script_image_extensions() -> set[str]:
    """Extensions handled by the `MODE="image"` case in media_convert.sh."""
    text = Path(get_script_path()).read_text()
    match = re.search(r'([A-Za-z0-9|]+)\)\s*MODE="image"', text)
    assert match, "could not locate the image case in media_convert.sh"
    return set(match.group(1).split("|"))


@pytest.mark.requirement("media:R8")
def test_converter_handles_every_api_image_extension():
    """Every extension the API classifies as an image must be convertible.

    Otherwise that format uploads fine, then 500s during conversion — which is
    exactly how webp regressed.
    """
    api_exts = {ext.lstrip(".") for ext in ALLOWED_IMAGE_EXTS}
    script_exts = _script_image_extensions()
    missing = api_exts - script_exts
    assert not missing, (
        f"media_convert.sh image case is missing {sorted(missing)}; uploads of "
        f"these formats are accepted by the API but 500 during conversion."
    )


@pytest.mark.skipif(
    shutil.which("magick") is None,
    reason="ImageMagick (magick) not installed in this environment",
)
@pytest.mark.parametrize("ext", ["png", "jpg", "webp"])
def test_media_convert_script_converts_image_to_webp(tmp_path: Path, ext: str):
    """media_convert.sh converts each supported image format to webp (exit 0)."""
    src = tmp_path / f"in.{ext}"
    subprocess.run(
        ["magick", "-size", "8x8", "xc:#3366cc", str(src)],
        check=True,
        capture_output=True,
    )
    out = tmp_path / "out.webp"
    proc = subprocess.run(
        [
            "bash",
            get_script_path(),
            "--input",
            str(src),
            "--output",
            str(out),
            "--force",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}\nstdout: {proc.stdout}"
    assert out.exists() and out.stat().st_size > 0
