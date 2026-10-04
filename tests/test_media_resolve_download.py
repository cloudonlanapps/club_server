"""Unit tests for ``MediaService.resolve_download`` (#210).

These lock the bytes/MIME-selection contract that moved out of the
``download_media`` route handler into the service — in particular the
encrypted branch, which the HTTP-level suite does not exercise (no test KEK
is configured). They construct a lightweight media stand-in and monkeypatch
``enc.decrypt`` so no encryption key is required.
"""

from types import SimpleNamespace

import pytest

from club_server.exceptions import DecryptionFailedException
from club_server.services import media as media_module
from club_server.services.media import MediaService


def _service() -> MediaService:
    # resolve_download is synchronous and never touches the DB session.
    return MediaService(db=None)


def _media(*, is_encrypted: bool, mime_type: str = "image/png", encryption_meta=None):
    return SimpleNamespace(
        is_encrypted=is_encrypted,
        mime_type=mime_type,
        original_mime_type=mime_type,
        encryption_meta=encryption_meta,
    )


def test_plain_picks_mime_from_suffix(tmp_path):
    file_path = tmp_path / "asset.webp"
    file_path.write_bytes(b"data")

    result = _service().resolve_download(_media(is_encrypted=False), file_path)

    assert result.file_path == file_path
    assert result.content is None
    assert result.media_type == "image/webp"


def test_plain_unknown_suffix_falls_back_to_octet_stream(tmp_path):
    file_path = tmp_path / "asset.bin"

    result = _service().resolve_download(_media(is_encrypted=False), file_path)

    assert result.media_type == "application/octet-stream"
    assert result.file_path == file_path


def test_encrypted_decrypts_inline_and_uses_inner_suffix(tmp_path, monkeypatch):
    file_path = tmp_path / "asset.webp.enc"
    file_path.write_bytes(b"cipher")
    captured = {}

    def fake_decrypt(ciphertext, meta, *, poster=False):
        captured["call"] = (ciphertext, meta, poster)
        return b"plaintext"

    monkeypatch.setattr(media_module.enc, "decrypt", fake_decrypt)

    result = _service().resolve_download(
        _media(is_encrypted=True, encryption_meta="meta-json"), file_path
    )

    assert result.content == b"plaintext"
    assert result.file_path is None
    assert result.media_type == "image/webp"  # MIME from the inner .webp suffix
    assert captured["call"] == (b"cipher", "meta-json", False)


def test_encrypted_poster_sets_poster_flag(tmp_path, monkeypatch):
    file_path = tmp_path / "asset_poster.png.enc"
    file_path.write_bytes(b"cipher")
    captured = {}

    def fake_decrypt(ciphertext, meta, *, poster=False):
        captured["poster"] = poster
        return b"plaintext"

    monkeypatch.setattr(media_module.enc, "decrypt", fake_decrypt)

    _service().resolve_download(
        _media(is_encrypted=True, encryption_meta="meta-json"), file_path
    )

    assert captured["poster"] is True


@pytest.mark.requirement("media:R91")
def test_encrypted_missing_metadata_raises(tmp_path):
    file_path = tmp_path / "asset.webp.enc"
    file_path.write_bytes(b"cipher")

    with pytest.raises(DecryptionFailedException):
        _service().resolve_download(
            _media(is_encrypted=True, encryption_meta=None), file_path
        )
