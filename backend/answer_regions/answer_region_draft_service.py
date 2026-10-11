from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from backend.answer_regions.answer_region_session_lock import get_answer_region_session_lock

DraftLoadStatus = Literal["missing", "compatible", "incompatible", "corrupt"]
_SCHEMA_VERSION = 1
@dataclass(frozen=True)
class DraftLoadResult:
    status: DraftLoadStatus
    draft: dict[str, Any] | None = None
    reason: str | None = None


class DraftRevisionConflictError(RuntimeError):
    pass


class DraftTemplateMismatchError(RuntimeError):
    pass


class AnswerRegionDraftService:
    def __init__(self, session_dir: Path) -> None:
        resolved_dir = Path(session_dir).resolve(strict=False)
        self.draft_path = resolved_dir / "region_draft.json"
        self.temp_path = resolved_dir / "region_draft.json.tmp"
        self._lock = _lock_for(self.draft_path)

    def compute_template_fingerprint(
        self, front_path: str | Path, back_path: str | Path
    ) -> str:
        digest = hashlib.sha256()
        for page, value in (("front", front_path), ("back", back_path)):
            encoded = str(value).encode("utf-8")
            label = page.encode("ascii")
            digest.update(len(label).to_bytes(1, "big"))
            digest.update(label)
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        return digest.hexdigest()

    def save(
        self,
        *,
        session_id: int,
        template_fingerprint: str,
        revision: int,
        regions: list[dict[str, Any]],
        expected_revision: int | None = None,
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
            if expected_revision is not None:
                current = self.load(expected_session_id=session_id)
                if current.status == "incompatible":
                    raise DraftTemplateMismatchError("answer region draft belongs to another session")
                if current.status == "corrupt":
                    raise ValueError("saved answer region draft cannot be read")
                current_revision = (
                    int(current.draft.get("revision", 0))
                    if current.status == "compatible" and current.draft is not None
                    else 0
                )
                if current_revision != int(expected_revision):
                    raise DraftRevisionConflictError(
                        "answer region draft revision changed"
                    )
                if int(revision) <= current_revision:
                    raise ValueError("answer region draft revision must advance")
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

    def load(
        self, *, expected_template_fingerprint: str | None = None,
        expected_session_id: int | None = None,
    ) -> DraftLoadResult:
        with self._lock:
            try:
                raw_draft = self.draft_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return DraftLoadResult(status="missing")
            except (OSError, UnicodeDecodeError):
                return DraftLoadResult(status="corrupt", reason="草稿文件无法读取，原文件已保留。")
            try:
                draft = _normalize_saved_draft(json.loads(raw_draft))
            except (ValueError, TypeError):
                return DraftLoadResult(status="corrupt", reason="草稿内容无法解析，原文件已保留。")
            if expected_session_id is not None and draft["session_id"] != expected_session_id:
                return DraftLoadResult(status="incompatible", reason="草稿属于另一场考试，原文件已保留。")
            return DraftLoadResult(status="compatible", draft=draft)

    def discard(self) -> None:
        with self._lock:
            self.draft_path.unlink(missing_ok=True)
            self.temp_path.unlink(missing_ok=True)


def legacy_template_content_hash(front_path: Path, back_path: Path) -> str:
    """The pre-upgrade fingerprint: a SHA-256 over the two image contents.

    Only used to recognise template bindings recorded before the fingerprint
    became a hash of the stored template path strings.
    """
    digest = hashlib.sha256()
    for page, path in (("front", Path(front_path)), ("back", Path(back_path))):
        content = path.read_bytes()
        label = page.encode("ascii")
        digest.update(len(label).to_bytes(1, "big"))
        digest.update(label)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _normalize_saved_draft(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("draft must be an object")
    draft = dict(value)
    draft.setdefault("schema_version", _SCHEMA_VERSION)
    draft.setdefault("template_fingerprint", "")
    draft.setdefault("revision", 0)
    draft.setdefault("updated_at", "")
    if (
        type(draft.get("session_id")) is not int or draft["session_id"] <= 0
        or type(draft.get("revision")) is not int or draft["revision"] < 0
        or not isinstance(draft.get("regions"), list)
        or any(not isinstance(region, dict) or not _is_nonblank_string(region.get("region_uuid"))
               for region in draft["regions"])
    ):
        raise ValueError("saved draft fields cannot be read")
    return draft


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


def _lock_for(draft_path: Path) -> object:
    return get_answer_region_session_lock(draft_path.parent)
