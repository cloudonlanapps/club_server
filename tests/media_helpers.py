"""Shared helpers for the media requirement tests (#494).

Fixture bytes and upload shortcuts for tests that drive the media API
end to end. The older media test files keep their own copies; these exist
so the rule-by-rule files added for ``media_requirements.md`` do not each
grow another one.
"""

import json
import os
import shutil
import struct
import tempfile
import zlib

import pytest
from httpx import AsyncClient


def auth(token: str) -> dict[str, str]:
    """Bearer header."""
    return {"Authorization": f"Bearer {token}"}


def png_bytes() -> bytes:
    """The smallest valid PNG the upload pipeline accepts."""
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data)
    ihdr = (
        struct.pack(">I", 13)
        + b"IHDR"
        + ihdr_data
        + struct.pack(">I", ihdr_crc & 0xFFFFFFFF)
    )
    comp = zlib.compress(b"\x00\xff\xff\xff")
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


def pdf_bytes() -> bytes:
    """A one-page PDF with a correct cross-reference table, so a renderer
    can draw its first page."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
    out += f"startxref\n{xref_at}\n%%EOF\n".encode()
    return out


def mp4_bytes() -> bytes:
    """Leading bytes of an MP4 container: enough to be classified a video."""
    return b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32


@pytest.fixture
def clean_upload_dir():
    """Uploads land on disk; clear them after the test."""
    upload_dir = os.environ.get("UPLOAD_DIR", "/tmp/club_server_test_uploads")
    os.makedirs(upload_dir, exist_ok=True)
    yield upload_dir
    shutil.rmtree(upload_dir, ignore_errors=True)
    tmp_base = tempfile.gettempdir()
    for entry in os.listdir(tmp_base):
        if entry.startswith("club_media_") or entry.startswith("club_upload_"):
            shutil.rmtree(os.path.join(tmp_base, entry), ignore_errors=True)


async def upload(
    client: AsyncClient,
    token: str,
    *,
    filename: str = "img.png",
    content: bytes | None = None,
    content_type: str = "image/png",
    access_roles: list[str] | None = None,
    preserve: bool = True,
    encrypt: bool = False,
    expect: int = 201,
) -> dict:
    """Upload one file and return the record, asserting the status."""
    data: dict[str, str] = {"preserveOriginal": "true" if preserve else "false"}
    if access_roles is not None:
        data["accessRoles"] = json.dumps(access_roles)
    if encrypt:
        data["encrypt"] = "true"
    response = await client.post(
        "/v1/media",
        files={
            "file": (
                filename,
                png_bytes() if content is None else content,
                content_type,
            )
        },
        data=data,
        headers=auth(token),
    )
    assert response.status_code == expect, response.text
    return response.json()
