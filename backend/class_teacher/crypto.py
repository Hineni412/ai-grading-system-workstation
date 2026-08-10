from __future__ import annotations

import json
from typing import Any


FORMAT_VERSION = 1


def canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


__all__ = [
    "FORMAT_VERSION",
    "canonical_json",
]
