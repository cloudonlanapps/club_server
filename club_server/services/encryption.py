"""AES-256-GCM encryption helpers for uploads (#150).

The KEK (key-encryption-key) is loaded from ``settings.encryption_key`` — a
base64-encoded 32-byte secret. Each upload gets a fresh DEK; the DEK is
AES-GCM-wrapped with the KEK and persisted in the row's ``encryption_meta``
JSON alongside the artifact nonces. Decryption reverses the process.

This module deliberately does not touch the filesystem — callers pass in
bytes and get bytes back, so the same primitives serve both ingestion and
download paths.
"""

from __future__ import annotations

import base64
import json
import os
import typing

from ..config import settings
from ..exceptions import (
    DecryptionFailedException,
    EncryptionNotConfiguredException,
)

if typing.TYPE_CHECKING:  # pragma: no cover
    pass

ENCRYPTION_VERSION = 1
_NONCE_SIZE = 12  # GCM default
_DEK_SIZE = 32  # AES-256


def _load_kek() -> bytes:
    """Return the KEK bytes or raise if no key is configured."""
    if not settings.encryption_key:
        raise EncryptionNotConfiguredException()
    try:
        kek = base64.b64decode(settings.encryption_key)
    except (ValueError, TypeError) as e:
        raise EncryptionNotConfiguredException(
            f"ENCRYPTION_KEY is not valid base64: {e}",
        )
    if len(kek) != _DEK_SIZE:
        raise EncryptionNotConfiguredException(
            f"ENCRYPTION_KEY must decode to {_DEK_SIZE} bytes (got {len(kek)})",
        )
    return kek


def is_configured() -> bool:
    """Return True iff a usable KEK is configured."""
    try:
        _load_kek()
    except EncryptionNotConfiguredException:
        return False
    return True


def encrypt_payload(plaintext: bytes) -> tuple[bytes, str]:
    """Encrypt ``plaintext`` under a freshly-generated DEK.

    Returns ``(ciphertext, encryption_meta_json)`` — the meta string is ready
    to persist as-is on the row. A second nonce slot (``poster_nonce``) is
    pre-filled with a fresh nonce in case the caller goes on to encrypt a
    poster artifact under the same DEK; for image-only uploads it is unused
    but cheap to carry.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    kek = _load_kek()
    dek = AESGCM.generate_key(bit_length=256)
    payload_nonce = os.urandom(_NONCE_SIZE)
    poster_nonce = os.urandom(_NONCE_SIZE)
    dek_nonce = os.urandom(_NONCE_SIZE)

    aes = AESGCM(dek)
    ciphertext = aes.encrypt(payload_nonce, plaintext, None)

    kek_aes = AESGCM(kek)
    wrapped = kek_aes.encrypt(dek_nonce, dek, None)

    meta = {
        "dek_wrapped": base64.b64encode(wrapped).decode("ascii"),
        "dek_nonce": base64.b64encode(dek_nonce).decode("ascii"),
        "payload_nonce": base64.b64encode(payload_nonce).decode("ascii"),
        "poster_nonce": base64.b64encode(poster_nonce).decode("ascii"),
    }
    return ciphertext, json.dumps(meta)


def encrypt_poster(plaintext: bytes, meta_json: str) -> bytes:
    """Encrypt a poster artifact under the DEK already stored in ``meta_json``.

    Mutates nothing; reuses the persisted DEK and the persisted
    ``poster_nonce`` slot.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    kek = _load_kek()
    meta = json.loads(meta_json)
    wrapped = base64.b64decode(meta["dek_wrapped"])
    dek_nonce = base64.b64decode(meta["dek_nonce"])
    poster_nonce = base64.b64decode(meta["poster_nonce"])

    kek_aes = AESGCM(kek)
    dek = kek_aes.decrypt(dek_nonce, wrapped, None)
    aes = AESGCM(dek)
    return aes.encrypt(poster_nonce, plaintext, None)


def decrypt(ciphertext: bytes, meta_json: str, *, poster: bool = False) -> bytes:
    """Decrypt ``ciphertext`` for the main artifact (``poster=False``) or the
    poster variant (``poster=True``). Raises ``DecryptionFailedException`` on
    AES-GCM auth failure (tampered ciphertext, wrong key, etc.)."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    kek = _load_kek()
    meta = json.loads(meta_json)
    wrapped = base64.b64decode(meta["dek_wrapped"])
    dek_nonce = base64.b64decode(meta["dek_nonce"])
    payload_nonce = base64.b64decode(
        meta["poster_nonce"] if poster else meta["payload_nonce"],
    )

    try:
        kek_aes = AESGCM(kek)
        dek = kek_aes.decrypt(dek_nonce, wrapped, None)
        aes = AESGCM(dek)
        return aes.decrypt(payload_nonce, ciphertext, None)
    except InvalidTag as e:
        raise DecryptionFailedException(str(e))
