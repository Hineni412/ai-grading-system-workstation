from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


DraftLoadStatus = Literal["missing", "compatible", "incompatible", "corrupt"]
_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DraftLoadResult:
    status: DraftLoadStatus
    draft: dict[str, Any] | None = None
    quarantined_path: Path | None = None


class AnswerRegionDraftService:
    def __init__(self, session_dir: Path) -> None:
        self.draft_path = session_dir / "region_draft.json"
        self.temp_path = session_dir / "region_draft.json.tmp"

    def compute_template_fingerprint(self, front_path: Path, back_path: Path) -> str:
        digest = hashlib.sha256()
        for page, path in (("front", front_path), ("back", back_path)):
            content = path.read_bytes()
            label = page.encode("ascii")
            digest.update(len(label).to_bytes(1, "big"))
            digest.update(label)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        return digest.hexdigest()

    def save(
        self,
        *,
        session_id: int,
        template_fingerprint: str,
        revision: int,
        regions: list[dict[str, Any]],
    ) -> Path:
        draft = {
            "schema_version": _SCHEMA_VERSION,
            "session_id": session_id,
            "template_fingerprint": template_fingerprint,
            "revision": revision,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "regions": regions,
        }
        self.draft_path.parent.mkdir(parents=True, exist_ok=True)
        self.temp_path.write_text(
            json.dumps(draft, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.temp_path.replace(self.draft_path)
        return self.draft_path

    def load(self, *, expected_template_fingerprint: str) -> DraftLoadResult:
        try:
            raw_draft = self.draft_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return DraftLoadResult(status="missing")
        except (OSError, UnicodeDecodeError):
            return self._quarantine()

        try:
            draft = json.loads(raw_draft)
        except json.JSONDecodeError:
            return self._quarantine()
        if not _is_valid_draft(draft):
            return self._quarantine()

        status: DraftLoadStatus = (
            "compatible"
            if draft["template_fingerprint"] == expected_template_fingerprint
            else "incompatible"
        )
        return DraftLoadResult(status=status, draft=draft)

    def discard(self) -> None:
        self.draft_path.unlink(missing_ok=True)
        self.temp_path.unlink(missing_ok=True)

    def _quarantine(self) -> DraftLoadResult:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        quarantined_path = self.draft_path.with_name(
            f"region_draft.corrupt-{timestamp}.json"
        )
        quarantined_path = self.draft_path.replace(quarantined_path)
        return DraftLoadResult(status="corrupt", quarantined_path=quarantined_path)


def _is_valid_draft(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    regions = value.get("regions")
    return (
        type(value.get("schema_version")) is int
        and value["schema_version"] == _SCHEMA_VERSION
        and type(value.get("session_id")) is int
        and isinstance(value.get("template_fingerprint"), str)
        and type(value.get("revision")) is int
        and isinstance(value.get("updated_at"), str)
        and isinstance(regions, list)
        and all(isinstance(region, dict) for region in regions)
    )
