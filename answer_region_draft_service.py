from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from answer_region_session_lock import get_answer_region_session_lock

DraftLoadStatus = Literal["missing", "compatible", "incompatible", "corrupt"]
_SCHEMA_VERSION = 1
@dataclass(frozen=True)
class DraftLoadResult:
    status: DraftLoadStatus
    draft: dict[str, Any] | None = None
    quarantined_path: Path | None = None


class AnswerRegionDraftService:
    def __init__(self, session_dir: Path) -> None:
        resolved_dir = Path(session_dir).resolve(strict=False)
        self.draft_path = resolved_dir / "region_draft.json"
        self.temp_path = resolved_dir / "region_draft.json.tmp"
        self._lock = _lock_for(self.draft_path)

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
        if not _is_valid_draft(draft):
            raise ValueError("invalid answer region draft")
        serialized = json.dumps(draft, ensure_ascii=False, indent=2)
        with self._lock:
            self.draft_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.temp_path.write_text(serialized, encoding="utf-8")
            except BaseException:
                try:
                    self.temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            self.temp_path.replace(self.draft_path)
        return self.draft_path

    def load(self, *, expected_template_fingerprint: str) -> DraftLoadResult:
        with self._lock:
            try:
                raw_draft = self.draft_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return DraftLoadResult(status="missing")
            except UnicodeDecodeError:
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
        with self._lock:
            self.draft_path.unlink(missing_ok=True)
            self.temp_path.unlink(missing_ok=True)

    def _quarantine(self) -> DraftLoadResult:
        with self._lock:
            while True:
                quarantined_path = self.draft_path.with_name(
                    f"region_draft.corrupt-{_new_quarantine_suffix()}.json"
                )
                try:
                    os.link(self.draft_path, quarantined_path)
                except FileExistsError:
                    continue
                except (NotImplementedError, OSError):
                    try:
                        _copy_exclusively(self.draft_path, quarantined_path)
                    except FileExistsError:
                        continue
                self.draft_path.unlink()
                break
            return DraftLoadResult(status="corrupt", quarantined_path=quarantined_path)


def _is_valid_draft(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    regions = value.get("regions")
    return (
        type(value.get("schema_version")) is int
        and value["schema_version"] == _SCHEMA_VERSION
        and type(value.get("session_id")) is int
        and value["session_id"] > 0
        and _is_nonblank_string(value.get("template_fingerprint"))
        and type(value.get("revision")) is int
        and value["revision"] >= 0
        and _is_iso8601_timestamp(value.get("updated_at"))
        and isinstance(regions, list)
        and all(
            isinstance(region, dict) and _is_nonblank_string(region.get("region_uuid"))
            for region in regions
        )
    )


def _is_nonblank_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_iso8601_timestamp(value: object) -> bool:
    if not _is_nonblank_string(value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _copy_exclusively(source: Path, destination: Path) -> None:
    destination_file = destination.open("xb")
    try:
        with destination_file:
            with source.open("rb") as source_file:
                shutil.copyfileobj(source_file, destination_file)
    except BaseException:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _lock_for(draft_path: Path) -> object:
    return get_answer_region_session_lock(draft_path.parent)


def _new_quarantine_suffix() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}-{uuid4().hex}"
