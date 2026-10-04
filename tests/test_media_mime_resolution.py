"""Unit tests for stored-MIME resolution on upload (#331).

The client's declared content type is a hint: it may be absent, generic
(``application/octet-stream``), or simply wrong. ``resolve_mime_type``
prefers the file's own magic bytes, then a specific declared value, then
the filename extension.
"""

import pytest

from club_server.services.media_mime import resolve_mime_type, sniff_mime_type


def _png_head() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


def _jpeg_head() -> bytes:
    return b"\xff\xd8\xff\xe0" + b"\x00" * 24


def _webp_head() -> bytes:
    return b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 16


def _pdf_head() -> bytes:
    return b"%PDF-1.7\n" + b"\x00" * 16


def _mp4_head() -> bytes:
    return b"\x00\x00\x00\x18ftypisom" + b"\x00" * 16


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (_png_head(), "image/png"),
        (_jpeg_head(), "image/jpeg"),
        (_webp_head(), "image/webp"),
        (_pdf_head(), "application/pdf"),
        (_mp4_head(), "video/mp4"),
        (b"GIF89a" + b"\x00" * 16, "image/gif"),
        (b"BM" + b"\x00" * 16, "image/bmp"),
    ],
)
@pytest.mark.requirement("media:R7")
def test_should_identify_type_when_magic_bytes_known(head: bytes, expected: str):
    assert sniff_mime_type(head) == expected


def test_should_return_none_when_magic_bytes_unrecognised():
    assert sniff_mime_type(b"not a media file at all") is None


def test_should_return_none_when_content_shorter_than_any_signature():
    assert sniff_mime_type(b"") is None


@pytest.mark.requirement("media:R7")
def test_should_derive_from_content_when_declared_type_is_generic():
    resolved = resolve_mime_type(
        filename="rink.webp",
        declared="application/octet-stream",
        content=_webp_head(),
    )
    assert resolved == "image/webp"


def test_should_derive_from_content_when_declared_type_missing():
    resolved = resolve_mime_type(
        filename="rink.png",
        declared=None,
        content=_png_head(),
    )
    assert resolved == "image/png"


@pytest.mark.requirement("media:R7")
def test_should_prefer_content_over_declared_when_declared_type_is_wrong():
    resolved = resolve_mime_type(
        filename="photo.jpg",
        declared="image/jpeg",
        content=_png_head(),
    )
    assert resolved == "image/png"


@pytest.mark.requirement("media:R7")
def test_should_keep_declared_type_when_content_is_unrecognised():
    resolved = resolve_mime_type(
        filename="clip.webm",
        declared="video/webm",
        content=b"unrecognisable bytes",
    )
    assert resolved == "video/webm"


@pytest.mark.requirement("media:R7")
def test_should_fall_back_to_extension_when_declared_generic_and_content_unknown():
    resolved = resolve_mime_type(
        filename="clip.webm",
        declared="application/octet-stream",
        content=b"unrecognisable bytes",
    )
    assert resolved == "video/webm"


@pytest.mark.requirement("media:R7")
def test_should_fall_back_to_octet_stream_when_nothing_identifies_the_file():
    resolved = resolve_mime_type(
        filename="clip",
        declared="binary/octet-stream",
        content=b"unrecognisable bytes",
    )
    assert resolved == "application/octet-stream"


def test_should_treat_binary_octet_stream_as_generic():
    resolved = resolve_mime_type(
        filename="rink.webp",
        declared="binary/octet-stream",
        content=_webp_head(),
    )
    assert resolved == "image/webp"


@pytest.mark.requirement("media:R7")
def test_should_never_store_a_generic_type_for_a_known_extension():
    resolved = resolve_mime_type(
        filename="doc.pdf",
        declared="application/octet-stream",
        content=b"unrecognisable bytes",
    )
    assert resolved == "application/pdf"
