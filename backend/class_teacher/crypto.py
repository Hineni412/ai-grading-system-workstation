from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .errors import VaultIntegrityError


KEY_BYTES = 32
SCRYPT_N = 32768
SCRYPT_R = 8
SCRYPT_P = 1
FORMAT_VERSION = 1


def derive_key(
    secret: str,
    salt: bytes,
    *,
    n: int = SCRYPT_N,
    r: int = SCRYPT_R,
    p: int = SCRYPT_P,
) -> bytes:
    return Scrypt(salt=salt, length=KEY_BYTES, n=n, r=r, p=p).derive(
        secret.encode("utf-8")
    )


def open_sealed(
    key: bytes,
    nonce: bytes,
    ciphertext: bytes,
    aad: bytes,
) -> bytes:
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, aad)
    except (InvalidTag, ValueError) as exc:
        raise VaultIntegrityError() from exc


def canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def unb64(value: object) -> bytes:
    try:
        return base64.b64decode(str(value), validate=True)
    except (ValueError, TypeError) as exc:
        raise VaultIntegrityError() from exc


def wipe(value: bytearray | None) -> None:
    if value is None:
        return
    for index in range(len(value)):
        value[index] = 0


__all__ = [
    "FORMAT_VERSION",
    "SCRYPT_N",
    "SCRYPT_P",
    "SCRYPT_R",
    "canonical_json",
    "derive_key",
    "open_sealed",
    "unb64",
    "wipe",
]
