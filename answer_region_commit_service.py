from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from answer_region_models import RegionValidationResult, normalize_regions, validate_regions


@dataclass(frozen=True)
class AnswerRegionCommitResult:
    committed: bool
    snapshot_pending: bool
    validation: RegionValidationResult
    snapshot_path: Path | None = None
    error: str | None = None


class AnswerRegionCommitService:
    def __init__(self, db: Any, session_dir: Path, draft_service: Any) -> None:
        self._db = db
        self._session_dir = Path(session_dir)
        self._draft_service = draft_service

    def commit(
        self,
        *,
        session_id: int,
        template_id: int,
        regions: list[dict[str, Any]],
        image_sizes: dict[str, tuple[int, int]],
        template_matches: bool,
    ) -> AnswerRegionCommitResult:
        normalized = normalize_regions(regions)
        validation = validate_regions(
            normalized,
            image_sizes=image_sizes,
            template_matches=template_matches,
        )
        if not validation.can_commit:
            return AnswerRegionCommitResult(False, False, validation)

        try:
            self._db.replace_answer_regions_atomic(
                session_id,
                template_id,
                normalized,
                confirmed=True,
            )
        except Exception as exc:
            return AnswerRegionCommitResult(False, False, validation, error=str(exc))

        return self._write_pending_snapshot(session_id=session_id, validation=validation)

    def retry_pending_snapshot(self, *, session_id: int) -> AnswerRegionCommitResult:
        validation = RegionValidationResult(())
        try:
            template = self._db.get_session_template(session_id)
        except Exception as exc:
            return AnswerRegionCommitResult(False, False, validation, error=str(exc))
        if template is None:
            return AnswerRegionCommitResult(False, False, validation)
        if not bool(template.get("regions_snapshot_pending")):
            return AnswerRegionCommitResult(True, False, validation)
        return self._write_pending_snapshot(session_id=session_id, validation=validation)

    def _write_pending_snapshot(
        self,
        *,
        session_id: int,
        validation: RegionValidationResult,
    ) -> AnswerRegionCommitResult:
        snapshot_path: Path | None = None
        try:
            formal_regions = self._db.list_answer_regions(session_id)
            next_snapshot_path = _new_snapshot_path(self._session_dir)
            _atomic_write_json(next_snapshot_path, formal_regions)
            snapshot_path = next_snapshot_path
            workflow_state = _build_workflow_state(
                self._db,
                session_id=session_id,
                formal_regions=formal_regions,
                snapshot_path=snapshot_path,
                previous=_read_json_safely(self._session_dir / "workflow_state.json"),
            )
            _atomic_write_json(self._session_dir / "workflow_state.json", workflow_state)
            self._db.mark_region_snapshot_complete(session_id)
            self._draft_service.discard()
        except Exception as exc:
            return AnswerRegionCommitResult(
                True,
                _snapshot_is_pending(self._db, session_id),
                validation,
                snapshot_path=snapshot_path,
                error=str(exc),
            )
        return AnswerRegionCommitResult(
            True,
            False,
            validation,
            snapshot_path=snapshot_path,
        )


def _snapshot_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as temp_file:
            json.dump(data, temp_file, ensure_ascii=False, indent=2)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.close(file_descriptor)
        except OSError:
            pass
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _new_snapshot_path(session_dir: Path) -> Path:
    base_path = session_dir / f"regions_confirmed_{_snapshot_timestamp()}.json"
    if not base_path.exists():
        return base_path
    while True:
        candidate = base_path.with_name(f"{base_path.stem}_{uuid4().hex}.json")
        if not candidate.exists():
            return candidate


def _read_json_safely(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _build_workflow_state(
    db: Any,
    *,
    session_id: int,
    formal_regions: list[dict[str, Any]],
    snapshot_path: Path,
    previous: dict[str, Any],
) -> dict[str, Any]:
    session = db.get_grading_session(session_id)
    if session is None:
        raise ValueError(f"grading session {session_id} does not exist")
    template = db.get_session_template(session_id)
    previous_extra = previous.get("extra")
    merged_extra = dict(previous_extra) if isinstance(previous_extra, dict) else {}
    merged_extra["regions_path"] = str(snapshot_path)
    return {
        "session_id": session_id,
        "session_name": session.get("session_name"),
        "stage": "regions_confirmed",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "active_paths": {
            "rubric_path": session.get("rubric_path"),
            "answer_key_path": session.get("answer_key_path"),
            "template_config_path": session.get("template_config_path"),
            "front_template_path": template.get("front_template_path") if template else None,
            "back_template_path": template.get("back_template_path") if template else None,
            "mapping_source_path": template.get("ai_analysis_path") if template else None,
            "template_mapping_path": template.get("template_config_path") if template else None,
            "regions_path": template.get("regions_path") if template else None,
        },
        "template_ready": db.is_template_ready(session_id),
        "region_count": len(formal_regions),
        "progress": db.get_session_progress(session_id),
        "extra": merged_extra,
    }


def _snapshot_is_pending(db: Any, session_id: int) -> bool:
    try:
        template = db.get_session_template(session_id)
    except Exception:
        return True
    return bool(template and template.get("regions_snapshot_pending"))
