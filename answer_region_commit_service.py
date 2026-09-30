from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from answer_region_models import (
    RegionIssue,
    RegionValidationResult,
    normalize_regions,
    validate_regions,
)
from answer_region_session_lock import get_answer_region_session_lock
from path_manager import resolve_stored_file_path
from question_id_contract import QuestionIdCatalog, QuestionIdContractError

logger = logging.getLogger(__name__)
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9_-]+")
_STUDENT_NAME_REGION_ID = "__student_name__"
_STUDENT_NAME_REGION_ALIASES = {
    _STUDENT_NAME_REGION_ID,
    "student_name",
    "name",
    "姓名",
    "姓名区域",
}


class _SnapshotCollisionError(Exception):
    pass


@dataclass(frozen=True)
class AnswerRegionCommitResult:
    committed: bool
    snapshot_pending: bool
    validation: RegionValidationResult
    snapshot_path: Path | None = None
    error: str | None = None


class AnswerRegionCommitService:
    def __init__(self, db: Any, session_dir: Path, draft_service: Any) -> None:
        resolved_session_dir = Path(session_dir).resolve(strict=False)
        resolved_draft_session_dir = Path(draft_service.draft_path).parent.resolve(strict=False)
        if resolved_session_dir != resolved_draft_session_dir:
            raise ValueError("draft service session directory must match session_dir")
        self._db = db
        self._templates = db.templates
        self._session_dir = resolved_session_dir
        self._draft_service = draft_service
        self._lock = get_answer_region_session_lock(self._session_dir)

    def commit(
        self,
        *,
        session_id: int,
        template_id: int,
        regions: list[dict[str, Any]],
        image_sizes: dict[str, tuple[int, int]],
        template_matches: bool,
        expected_template_fingerprint: str | None = None,
    ) -> AnswerRegionCommitResult:
        with self._lock:
            if template_matches and expected_template_fingerprint is not None:
                template_matches = self._current_template_matches(
                    session_id=session_id,
                    template_id=template_id,
                    expected_template_fingerprint=expected_template_fingerprint,
                )
            normalized = normalize_regions(regions)
            normalized, binding_issues = self._canonicalize_region_bindings(
                session_id=session_id,
                regions=normalized,
            )
            region_validation = validate_regions(
                normalized,
                image_sizes=image_sizes,
                template_matches=template_matches,
            )
            validation = RegionValidationResult(
                tuple(binding_issues) + region_validation.issues
            )
            if not validation.can_commit:
                return AnswerRegionCommitResult(False, False, validation)

            try:
                draft_marker = _draft_marker(self._draft_service.draft_path)
            except Exception:
                logger.exception("Failed to read answer-region draft state before commit")
                return AnswerRegionCommitResult(
                    False,
                    False,
                    validation,
                    error="draft_state_failed",
                )

            try:
                snapshot_token = self._templates.replace_answer_regions_atomic(
                    session_id,
                    template_id,
                    normalized,
                    confirmed=True,
                )
            except Exception:
                logger.exception("Failed to replace formal answer regions")
                return AnswerRegionCommitResult(
                    False,
                    False,
                    validation,
                    error="database_commit_failed",
                )

            return self._write_pending_snapshot(
                session_id=session_id,
                snapshot_token=snapshot_token,
                validation=validation,
                draft_marker=draft_marker,
            )

    def _canonicalize_region_bindings(
        self,
        *,
        session_id: int,
        regions: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[RegionIssue]]:
        try:
            catalog = self._load_question_catalog(session_id)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, QuestionIdContractError):
            logger.exception("Failed to load the question-id catalog before region commit")
            return regions, [
                RegionIssue(
                    "question_catalog_invalid",
                    "The current scoring configuration has invalid question identifiers.",
                )
            ]
        except (TypeError, ValueError, KeyError):
            logger.exception("Failed to resolve the scoring configuration before region commit")
            return regions, [
                RegionIssue(
                    "question_catalog_invalid",
                    "The current scoring configuration is unavailable.",
                )
            ]

        issues: list[RegionIssue] = []
        for region in regions:
            region_uuid = _optional_text(region.get("region_uuid"))
            mapped = _optional_text(region.get("mapped_question_id"))
            if mapped is None:
                continue

            canonical_mapped = _canonical_region_binding(catalog, mapped)
            if canonical_mapped is None:
                issues.append(
                    RegionIssue(
                        "unknown_question_id",
                        f"Question identifier {mapped!r} is not present in the current rubric.",
                        region_uuid=region_uuid,
                        question_id=mapped,
                    )
                )
                continue
            region["mapped_question_id"] = canonical_mapped

            detected = _optional_text(region.get("detected_question_id"))
            if detected is None:
                continue
            canonical_detected = _canonical_region_binding(catalog, detected)
            # A teacher's mapped binding is authoritative.  An unrecognised
            # machine-detected label must not become another formal identity.
            region["detected_question_id"] = canonical_detected

        return regions, issues

    def _load_question_catalog(self, session_id: int) -> QuestionIdCatalog:
        session = self._db.sessions.get_grading_session(session_id)
        if not isinstance(session, dict):
            raise ValueError("grading session is unavailable")
        search_roots = [self._session_dir, self._session_dir.parent]
        db_path = self._db.db_path
        if db_path:
            search_roots.append(Path(db_path).parent)
        rubric_path = resolve_stored_file_path(
            session.get("rubric_path"),
            search_roots=search_roots,
        )
        rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        if not isinstance(rubric, dict):
            raise ValueError("rubric must be an object")
        return QuestionIdCatalog.from_document(rubric)

    def _current_template_matches(
        self,
        *,
        session_id: int,
        template_id: int,
        expected_template_fingerprint: str,
    ) -> bool:
        try:
            template = self._templates.get_session_template(session_id)
            if template is None or int(template.get("id")) != template_id:
                return False
            paths = [
                resolve_stored_file_path(
                    template.get(f"{page}_template_path"),
                    search_roots=[self._session_dir, self._session_dir.parent],
                )
                for page in ("front", "back")
            ]
            if not all(path.is_file() for path in paths):
                return False
            return (
                self._draft_service.compute_template_fingerprint(paths[0], paths[1])
                == expected_template_fingerprint
            )
        except Exception:
            logger.exception("Failed to verify current answer-region template fingerprint")
            return False

    def retry_pending_snapshot(self, *, session_id: int) -> AnswerRegionCommitResult:
        with self._lock:
            validation = RegionValidationResult(())
            try:
                template = self._templates.get_session_template(session_id)
            except Exception:
                logger.exception("Failed to read pending answer-region snapshot state")
                return AnswerRegionCommitResult(
                    False,
                    False,
                    validation,
                    error="snapshot_state_failed",
                )
            if template is None:
                return AnswerRegionCommitResult(
                    False,
                    False,
                    validation,
                    error="session_template_missing",
                )
            if not bool(template.get("regions_snapshot_pending")):
                return AnswerRegionCommitResult(True, False, validation)

            snapshot_token = template.get("regions_snapshot_token")
            if not isinstance(snapshot_token, str) or not snapshot_token:
                return AnswerRegionCommitResult(
                    True,
                    True,
                    validation,
                    error="snapshot_token_missing",
                )
            try:
                draft_marker = _draft_marker(self._draft_service.draft_path)
            except Exception:
                logger.exception("Failed to read answer-region draft state before retry")
                return AnswerRegionCommitResult(
                    True,
                    True,
                    validation,
                    error="draft_state_failed",
                )
            return self._write_pending_snapshot(
                session_id=session_id,
                snapshot_token=snapshot_token,
                validation=validation,
                draft_marker=draft_marker,
            )

    def _write_pending_snapshot(
        self,
        *,
        session_id: int,
        snapshot_token: str,
        validation: RegionValidationResult,
        draft_marker: str | None,
    ) -> AnswerRegionCommitResult:
        snapshot_path: Path | None = None
        if not _valid_snapshot_token(snapshot_token):
            return AnswerRegionCommitResult(
                True,
                True,
                validation,
                error="snapshot_token_invalid",
            )

        try:
            if not self._generation_is_current(session_id, snapshot_token):
                return self._stale_result(validation)
            formal_regions = self._templates.list_answer_regions(session_id)
            if not self._generation_is_current(session_id, snapshot_token):
                return self._stale_result(validation)
        except Exception:
            logger.exception("Failed to read formal answer regions for snapshot")
            return self._pending_error_result(
                session_id,
                validation,
                snapshot_path,
                "snapshot_state_failed",
            )

        next_snapshot_path = self._session_dir / f"regions_confirmed_{snapshot_token}.json"
        try:
            _atomic_write_json(next_snapshot_path, formal_regions)
            snapshot_path = next_snapshot_path
        except _SnapshotCollisionError:
            logger.exception("Answer-region snapshot destination contains a different payload")
            return self._pending_error_result(
                session_id,
                validation,
                snapshot_path,
                "snapshot_collision",
            )
        except Exception:
            logger.exception("Failed to publish answer-region snapshot")
            return self._pending_error_result(
                session_id,
                validation,
                snapshot_path,
                "snapshot_write_failed",
            )

        try:
            workflow_path = self._session_dir / "workflow_state.json"
            previous_workflow_exists = workflow_path.exists()
            previous_workflow = _read_json_safely(workflow_path)
            workflow_state = _build_workflow_state(
                self._db,
                self._templates,
                session_id=session_id,
                formal_regions=formal_regions,
                snapshot_path=snapshot_path,
                snapshot_token=snapshot_token,
                previous=previous_workflow,
            )
            _atomic_write_json(workflow_path, workflow_state)
        except Exception:
            logger.exception("Failed to publish answer-region workflow snapshot")
            return self._pending_error_result(
                session_id,
                validation,
                snapshot_path,
                "workflow_snapshot_failed",
            )

        try:
            if not self._generation_is_current(session_id, snapshot_token):
                return self._stale_after_workflow_result(
                    validation,
                    snapshot_path,
                    workflow_path,
                    snapshot_token,
                    previous_workflow,
                    previous_workflow_exists,
                )
            with self._draft_service._lock:
                if _draft_marker(self._draft_service.draft_path) != draft_marker:
                    return self._pending_error_result(
                        session_id,
                        validation,
                        snapshot_path,
                        "draft_changed_during_commit",
                    )
                self._draft_service.discard()
        except Exception:
            logger.exception("Failed to discard answer-region draft")
            return self._pending_error_result(
                session_id,
                validation,
                snapshot_path,
                "draft_cleanup_failed",
            )

        try:
            if not self._generation_is_current(session_id, snapshot_token):
                return self._stale_after_workflow_result(
                    validation,
                    snapshot_path,
                    workflow_path,
                    snapshot_token,
                    previous_workflow,
                    previous_workflow_exists,
                )
            cleared = self._templates.mark_region_snapshot_complete(
                session_id,
                expected_token=snapshot_token,
            )
        except Exception:
            logger.exception("Failed to mark answer-region snapshot complete")
            return self._pending_error_result(
                session_id,
                validation,
                snapshot_path,
                "snapshot_completion_failed",
            )
        if not cleared:
            try:
                if not self._generation_is_current(session_id, snapshot_token):
                    return self._stale_after_workflow_result(
                        validation,
                        snapshot_path,
                        workflow_path,
                        snapshot_token,
                        previous_workflow,
                        previous_workflow_exists,
                    )
            except Exception:
                logger.exception("Failed to verify answer-region generation after completion")
            return self._pending_error_result(
                session_id,
                validation,
                snapshot_path,
                "snapshot_completion_failed",
            )
        return AnswerRegionCommitResult(
            True,
            False,
            validation,
            snapshot_path=snapshot_path,
        )

    def _generation_is_current(self, session_id: int, snapshot_token: str) -> bool:
        template = self._templates.get_session_template(session_id)
        return bool(
            template
            and template.get("regions_snapshot_pending")
            and template.get("regions_snapshot_token") == snapshot_token
        )

    def _stale_result(
        self,
        validation: RegionValidationResult,
        snapshot_path: Path | None = None,
    ) -> AnswerRegionCommitResult:
        return AnswerRegionCommitResult(
            True,
            True,
            validation,
            snapshot_path=snapshot_path,
            error="stale_snapshot_generation",
        )

    def _stale_after_workflow_result(
        self,
        validation: RegionValidationResult,
        snapshot_path: Path,
        workflow_path: Path,
        snapshot_token: str,
        previous_workflow: dict[str, Any],
        previous_workflow_exists: bool,
    ) -> AnswerRegionCommitResult:
        self._lock.assert_held_by_current_thread()
        try:
            _restore_workflow_if_owned(
                workflow_path,
                snapshot_token=snapshot_token,
                previous=previous_workflow,
                previous_exists=previous_workflow_exists,
            )
        except Exception:
            logger.exception("Failed to restore workflow after stale answer-region generation")
            return AnswerRegionCommitResult(
                True,
                True,
                validation,
                snapshot_path=snapshot_path,
                error="workflow_restore_failed",
            )
        return self._stale_result(validation, snapshot_path)

    def _pending_error_result(
        self,
        session_id: int,
        validation: RegionValidationResult,
        snapshot_path: Path | None,
        error: str,
    ) -> AnswerRegionCommitResult:
        return AnswerRegionCommitResult(
            True,
            _snapshot_is_pending(self._templates, session_id),
            validation,
            snapshot_path=snapshot_path,
            error=error,
        )


def _atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, ensure_ascii=False, indent=2)
    file_descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as temp_file:
            temp_file.write(serialized)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        if path.name.startswith("regions_confirmed_"):
            _publish_exclusively(temp_path, path, data)
        else:
            os.replace(temp_path, path)
    except BaseException:
        try:
            os.close(file_descriptor)
        except OSError:
            pass
        raise
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _publish_exclusively(temp_path: Path, destination: Path, data: object) -> None:
    try:
        os.link(temp_path, destination)
    except FileExistsError:
        if _json_file_matches(destination, data):
            return
        raise _SnapshotCollisionError


def _json_file_matches(path: Path, data: object) -> bool:
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return existing == data


def _read_json_safely(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _build_workflow_state(
    db: Any,
    templates: Any,
    *,
    session_id: int,
    formal_regions: list[dict[str, Any]],
    snapshot_path: Path,
    snapshot_token: str,
    previous: dict[str, Any],
) -> dict[str, Any]:
    session = db.sessions.get_grading_session(session_id)
    if session is None:
        raise ValueError(f"grading session {session_id} does not exist")
    template = templates.get_session_template(session_id)
    previous_extra = previous.get("extra")
    merged_extra = dict(previous_extra) if isinstance(previous_extra, dict) else {}
    merged_extra["regions_path"] = str(snapshot_path)
    return {
        "session_id": session_id,
        "session_name": session.get("session_name"),
        "stage": "regions_confirmed",
        "snapshot_token": snapshot_token,
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
        "template_ready": templates.is_template_ready(session_id),
        "region_count": len(formal_regions),
        "progress": db.papers.get_session_progress(session_id),
        "extra": merged_extra,
    }


def _restore_workflow_if_owned(
    path: Path,
    *,
    snapshot_token: str,
    previous: dict[str, Any],
    previous_exists: bool,
) -> None:
    current = _read_json_safely(path)
    if current.get("snapshot_token") != snapshot_token:
        return
    if previous_exists:
        _atomic_write_json(path, previous)
    else:
        path.unlink(missing_ok=True)


def _draft_marker(draft_path: Path) -> str | None:
    try:
        content = draft_path.read_bytes()
    except FileNotFoundError:
        return None
    return hashlib.sha256(content).hexdigest()


def _snapshot_is_pending(db: Any, session_id: int) -> bool:
    try:
        template = db.templates.get_session_template(session_id)
    except Exception:
        logger.exception("Failed to read answer-region pending state")
        return True
    return bool(template and template.get("regions_snapshot_pending"))


def _valid_snapshot_token(snapshot_token: str) -> bool:
    return bool(_SAFE_TOKEN.fullmatch(snapshot_token))


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _canonical_region_binding(
    catalog: QuestionIdCatalog,
    raw_question_id: object,
) -> str | None:
    text = _optional_text(raw_question_id)
    if text in _STUDENT_NAME_REGION_ALIASES:
        return _STUDENT_NAME_REGION_ID
    return catalog.resolve(text)
