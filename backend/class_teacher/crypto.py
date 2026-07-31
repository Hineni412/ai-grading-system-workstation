from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .errors import VaultIntegrityError


KEY_BYTES = 32
NONCE_BYTES = 12
SALT_BYTES = 16
SCRYPT_N = 32768
SCRYPT_R = 8
SCRYPT_P = 1
FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class WrappedValue:
    nonce: bytes
    ciphertext: bytes


def random_key() -> bytes:
    return secrets.token_bytes(KEY_BYTES)


def random_salt() -> bytes:
    return secrets.token_bytes(SALT_BYTES)


def generate_recovery_key() -> str:
    raw = base64.b32encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    groups = "-".join(raw[index : index + 4] for index in range(0, len(raw), 4))
    return f"CTRK-{groups}"


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


def seal(key: bytes, plaintext: bytes, aad: bytes) -> WrappedValue:
    nonce = secrets.token_bytes(NONCE_BYTES)
    return WrappedValue(nonce, AESGCM(key).encrypt(nonce, plaintext, aad))


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


def b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


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
    "WrappedValue",
    "b64",
    "canonical_json",
    "derive_key",
    "generate_recovery_key",
    "open_sealed",
    "random_key",
    "random_salt",
    "seal",
    "unb64",
    "wipe",
]
