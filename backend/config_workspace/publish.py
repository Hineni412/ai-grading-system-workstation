from __future__ import annotations

import copy
import json
import hashlib
import uuid
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal

from backend.config_workspace.secure_fs import (
    SecureFilesystemError,
    SecureRootFilesystem,
)
from backend.config_workspace.drafts import (
    DRAFT_MARKER_KEY,
    EMPTY_ANSWER_KEY,
    EMPTY_RUBRIC,
)
from backend.config_workspace.editor import (
    ConfigEditorCommand,
    ConfigEditorEdit,
    ConfigEditorValidationError,
    apply_config_editor_changes,
    collect_config_editor_issues,
    project_config_editor,
    validate_config_editor_candidate,
)
from backend.config_workspace.locks import session_config_lock
from backend.config_generation.normalization import (
    validate_generated_config,
)
from path_manager import resolve_stored_file_path
from question_id_contract import canonicalize_grading_config_payload

if TYPE_CHECKING:
    from backend.jobs.store import JobStore


@dataclass(frozen=True, slots=True)
class PublishedConfig:
    rubric_path: Path
    answer_key_path: Path
    created_paths: tuple[Path, ...]


class ConfigRevisionConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LoadedEditorConfig:
    session: dict[str, Any]
    payload: dict[str, Any]
    revision: str
    configured: bool
    knowledge_normalization_pending: bool


@dataclass(frozen=True, slots=True)
class ConfigSaveResult:
    config_saved: bool
    mapping_status: Literal["not_present", "refreshed", "reconfirm_required"]
    mapping_message: str


def publish_generated_config(
    upload_config_dir: Path,
    payload: dict[str, Any],
    *,
    job_id: int | None = None,
    token: str | None = None,
    preserve_scores: bool = False,
) -> PublishedConfig:
    if (job_id is None) == (token is None):
        raise ValueError("exactly one publication identity is required")
    if job_id is not None:
        clean_job_id = int(job_id)
        if clean_job_id <= 0:
            raise ValueError("job_id must be positive")
        stem = f"job-{clean_job_id}"
    else:
        clean_token = str(token or "").strip().casefold()
        if len(clean_token) != 32 or any(ch not in "0123456789abcdef" for ch in clean_token):
            raise ValueError("publication token must be 32 hexadecimal characters")
        stem = f"editor-{clean_token}"
    canonical_payload = canonicalize_grading_config_payload(payload)
    validate_generated_config(
        canonical_payload, normalize=not preserve_scores, enforce_score_policy=not preserve_scores,
    )
    filesystem = SecureRootFilesystem(Path(upload_config_dir))
    rubric_path = filesystem.root / f"rubric_{stem}.json"
    answer_key_path = filesystem.root / f"answer_key_{stem}.json"
    if rubric_path.exists() or answer_key_path.exists():
        raise FileExistsError("config generation output already exists")

    created: list[Path] = []
    try:
        filesystem.atomic_write_bytes(
            rubric_path,
            _json_bytes(canonical_payload["rubric"]),
        )
        created.append(rubric_path)
        filesystem.atomic_write_bytes(
            answer_key_path,
            _json_bytes(canonical_payload["answer_key"]),
        )
        created.append(answer_key_path)
    except BaseException:
        try:
            filesystem.unlink_many(reversed(created))
        except SecureFilesystemError:
            pass
        raise
    return PublishedConfig(
        rubric_path=rubric_path,
        answer_key_path=answer_key_path,
        created_paths=tuple(created),
    )


def remove_published_config(
    upload_config_dir: Path,
    paths: tuple[Path, ...],
) -> None:
    if not paths:
        return
    try:
        SecureRootFilesystem(Path(upload_config_dir)).unlink_many(paths)
    except SecureFilesystemError:
        pass


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")


def config_revision(session: dict[str, Any], payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        {
            "session_id": int(session["id"]),
            "rubric": payload["rubric"],
            "answer_key": payload["answer_key"],
            "source_sha256": str(session.get("source_paper_sha256") or ""),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_editor_config(db: Any, session_id: int) -> LoadedEditorConfig:
    session = db.get_grading_session(int(session_id))
    if session is None or bool(int(session.get("is_deleted") or 0)):
        raise KeyError("grading session is unavailable")
    db_path = Path(db.db_path)
    data_root = db_path.parent.parent if db_path.parent.name == "databases" else None
    rubric = _read_json_object(session.get("rubric_path"), data_root=data_root)
    answer_key = _read_json_object(session.get("answer_key_path"), data_root=data_root)
    payload = {"rubric": rubric, "answer_key": answer_key, "meta": {"warnings": []}}
    configured = not _is_exact_draft(session, rubric, answer_key)
    return LoadedEditorConfig(
        session=session,
        payload=payload,
        revision=config_revision(session, payload),
        configured=configured,
        knowledge_normalization_pending=False,
    )


def editor_response(config: LoadedEditorConfig) -> dict[str, Any]:
    if config.configured:
        rows = [asdict(row) for row in project_config_editor(config.payload)]
        issues = collect_config_editor_issues(config.payload)
        total_score = float(config.payload["rubric"].get("total_score") or 0)
    else:
        rows = []
        issues = []
        total_score = 0.0
    return {
        "session_id": int(config.session["id"]),
        "configured": config.configured,
        "revision": config.revision,
        "rows": rows,
        "total_score": total_score,
        "issues": issues,
        "source": _safe_source(config.session),
    }


def save_editor_config(
    db: Any,
    upload_config_dir: Path,
    *,
    session_id: int,
    expected_revision: str,
    edits: tuple[ConfigEditorEdit, ...],
    commands: tuple[ConfigEditorCommand, ...],
    job_store: JobStore | None = None,
) -> tuple[LoadedEditorConfig, bool]:
    with session_config_lock(Path(upload_config_dir), int(session_id)):
        return _save_editor_config_locked(
            db,
            upload_config_dir,
            session_id=session_id,
            expected_revision=expected_revision,
            edits=edits,
            commands=commands,
            job_store=job_store,
        )


def _save_editor_config_locked(
    db: Any,
    upload_config_dir: Path,
    *,
    session_id: int,
    expected_revision: str,
    edits: tuple[ConfigEditorEdit, ...],
    commands: tuple[ConfigEditorCommand, ...],
    job_store: JobStore | None,
) -> tuple[LoadedEditorConfig, bool]:
    current = load_editor_config(db, session_id)
    _require_revision(expected_revision, current)
    if not edits and not commands:
        return current, False
    if not current.configured:
        raise ConfigEditorValidationError(({
            "code": "draft_not_configured",
            "severity": "error",
            "row_id": None,
            "field": "config",
            "message": "The draft has no grading configuration to edit.",
        },))
    from question_bank.solution_evidence.evidence_snapshot import (
        annotate_rubric_payload,
        load_snapshot,
        validate_rubric_evidence_coverage,
    )
    snapshot = load_snapshot(Path(upload_config_dir), int(session_id))
    base_payload = copy.deepcopy(current.payload)
    if snapshot is not None:
        # Restore verifiable legacy references before applying teacher edits.
        # The loaded revision and files remain unchanged until the normal save.
        annotate_rubric_payload(
            base_payload["rubric"], snapshot, session_id,
            answer_key=base_payload.get("answer_key"),
        )
    candidate = apply_config_editor_changes(base_payload, edits=edits, commands=commands)
    if snapshot is not None:
        violations = validate_rubric_evidence_coverage(
            candidate.get("rubric", {}),
            snapshot,
        )
        if violations:
            raise ConfigEditorValidationError(({
                "code": "evidence_point_coverage_violation",
                "severity": "error",
                "row_id": None,
                "field": "rubric",
                "message": "评分步骤与题库判定点的关联不完整，请检查小问和步骤结构；分值修改已保留。",
            },))
    publication: PublishedConfig | None = None
    try:
        validate_config_editor_candidate(candidate)
        publication = publish_generated_config(
            Path(upload_config_dir), candidate, token=uuid.uuid4().hex,
            preserve_scores=True,
        )
        before_bind = load_editor_config(db, session_id)
        _require_revision(expected_revision, before_bind)
        if (
            str(before_bind.session.get("rubric_path") or "")
            != str(current.session.get("rubric_path") or "")
            or str(before_bind.session.get("answer_key_path") or "")
            != str(current.session.get("answer_key_path") or "")
        ):
            raise ConfigRevisionConflict("config paths changed")
        if job_store is None:
            bound = db.publish_grading_session_config(
                int(session_id),
                rubric_path=str(publication.rubric_path),
                answer_key_path=str(publication.answer_key_path),
                expected_rubric_path=str(current.session.get("rubric_path") or ""),
                expected_answer_key_path=str(current.session.get("answer_key_path") or ""),
                preserve_question_bank_sync=True,
            )
        else:
            bound = job_store.update_session_config_if_idle(
                int(session_id),
                rubric_path=str(publication.rubric_path),
                answer_key_path=str(publication.answer_key_path),
                expected_rubric_path=str(current.session.get("rubric_path") or ""),
                expected_answer_key_path=str(current.session.get("answer_key_path") or ""),
                preserve_question_bank_sync=True,
            )
        if not bound:
            raise ConfigRevisionConflict("config binding changed")
    except BaseException:
        if publication is not None:
            remove_published_config(Path(upload_config_dir), publication.created_paths)
        raise
    return load_editor_config(db, session_id), True


def save_editor_config_and_refresh_mapping(
    db: Any,
    upload_config_dir: Path,
    *,
    session_id: int,
    expected_revision: str,
    edits: tuple[ConfigEditorEdit, ...],
    commands: tuple[ConfigEditorCommand, ...],
    job_store: JobStore,
    mapping_output_dir: Path,
    mapping_refresher: Callable[[], Literal["not_present", "refreshed", "reconfirm_required"]] | None = None,
) -> tuple[LoadedEditorConfig, bool, ConfigSaveResult]:
    with session_config_lock(Path(upload_config_dir), int(session_id)):
        job_store.assert_config_session_idle(session_id)
        current, saved = _save_editor_config_locked(
            db,
            upload_config_dir,
            session_id=session_id,
            expected_revision=expected_revision,
            edits=edits,
            commands=commands,
            job_store=job_store,
        )
        if not saved:
            return current, False, ConfigSaveResult(False, "not_present", "评分依据未变化。")
        refresher = mapping_refresher or (
            lambda: refresh_template_mapping_from_session(
                db,
                session_id,
                output_root=mapping_output_dir,
            )
        )
        return current, True, refresh_mapping_after_config_save(refresher)


def publish_legacy_config_and_refresh_mapping(
    db: Any,
    upload_config_dir: Path,
    *,
    session_id: int,
    rubric_path: str,
    answer_key_path: str,
    source_paper_path: str,
    source_paper_sha256: str,
    mapping_output_dir: Path,
    job_store: JobStore | None = None,
    mapping_refresher: Callable[[], Literal["not_present", "refreshed", "reconfirm_required"]] | None = None,
) -> ConfigSaveResult:
    if job_store is None:
        from backend.jobs.store import JobStore

        store = JobStore(db.db_path)
    else:
        store = job_store
    with session_config_lock(Path(upload_config_dir), int(session_id)):
        current = db.get_grading_session(int(session_id))
        if current is None or bool(int(current.get("is_deleted") or 0)):
            raise ConfigRevisionConflict("session is unavailable")
        bound = store.update_session_config_with_source_if_idle(
            int(session_id),
            rubric_path=str(rubric_path),
            answer_key_path=str(answer_key_path),
            source_paper_path=str(source_paper_path),
            source_paper_sha256=str(source_paper_sha256),
            expected_rubric_path=str(current.get("rubric_path") or ""),
            expected_answer_key_path=str(current.get("answer_key_path") or ""),
        )
        if not bound:
            raise ConfigRevisionConflict("config binding changed")
        if hasattr(db, "_rubric_map_cache"):
            db._rubric_map_cache.pop(int(session_id), None)
        refresher = mapping_refresher or (
            lambda: refresh_template_mapping_from_session(
                db,
                session_id,
                output_root=mapping_output_dir,
            )
        )
        return refresh_mapping_after_config_save(refresher)


def refresh_template_mapping_from_session(
    db: Any,
    session_id: int,
    *,
    output_root: Path,
    after_refresh: Callable[[dict[str, Any]], None] | None = None,
) -> Literal["not_present", "refreshed", "reconfirm_required"]:
    session = db.get_grading_session(int(session_id))
    template = db.get_session_template(int(session_id))
    if not session or not template:
        return "not_present"
    _invalidate_template_mapping_confirmation(db, session_id)
    db_path = Path(db.db_path)
    data_root = db_path.parent.parent if db_path.parent.name == "databases" else None
    front = resolve_stored_file_path(template.get("front_template_path"), data_root=data_root)
    back = resolve_stored_file_path(template.get("back_template_path"), data_root=data_root)
    if not front.exists() or not back.exists():
        return "reconfirm_required"
    from template_analyzer import create_template_mapping_package

    package = create_template_mapping_package(
        front,
        back,
        rubric=_read_json_object(session.get("rubric_path"), data_root=data_root),
        answer_key=_read_json_object(session.get("answer_key_path"), data_root=data_root),
        output_dir=Path(output_root) / f"session_{int(session_id)}",
    )
    db.update_session_template_analysis(
        int(session_id),
        ai_analysis_path=package["paths"]["raw_path"],
        template_config_path=package["paths"]["config_path"],
        regions_path=package["paths"]["regions_path"],
    )
    if after_refresh is not None:
        after_refresh(package)
    return "refreshed"


def _invalidate_template_mapping_confirmation(db: Any, session_id: int) -> None:
    if db.get_session_template(int(session_id)) is not None:
        db.mark_template_confirmed(int(session_id), False)


def refresh_mapping_after_config_save(
    refresher: Callable[[], Literal["not_present", "refreshed", "reconfirm_required"]],
) -> ConfigSaveResult:
    try:
        status = refresher()
    except Exception:
        return ConfigSaveResult(
            config_saved=True,
            mapping_status="reconfirm_required",
            mapping_message="评分依据已保存；样卷映射需要回旧入口重新确认",
        )
    if status == "refreshed":
        return ConfigSaveResult(
            config_saved=True,
            mapping_status="refreshed",
            mapping_message="评分依据已保存，样卷映射已刷新",
        )
    if status == "reconfirm_required":
        return ConfigSaveResult(
            config_saved=True,
            mapping_status="reconfirm_required",
            mapping_message="评分依据已保存；样卷映射需要回到旧入口重新确认。",
        )
    if status != "not_present":
        raise ValueError("mapping refresh returned an invalid status")
    return ConfigSaveResult(
        config_saved=True,
        mapping_status="not_present",
        mapping_message="评分依据已保存；当前考试没有样卷映射。",
    )


def _require_revision(expected: str, current: LoadedEditorConfig) -> None:
    if str(expected) != current.revision:
        raise ConfigRevisionConflict("configuration revision changed")


def _read_json_object(path_value: object, *, data_root: Path | None = None) -> dict[str, Any]:
    path = resolve_stored_file_path(path_value, data_root=data_root)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("stored configuration must be a JSON object")
    return payload


def _is_exact_draft(
    session: dict[str, Any], rubric: dict[str, Any], answer_key: dict[str, Any]
) -> bool:
    marker = str(rubric.get(DRAFT_MARKER_KEY) or "").strip().casefold()
    return (
        len(marker) == 32
        and all(char in "0123456789abcdef" for char in marker)
        and rubric == {
            **EMPTY_RUBRIC,
            "exam_title": str(rubric.get("exam_title") or ""),
            DRAFT_MARKER_KEY: marker,
        }
        and answer_key == {**EMPTY_ANSWER_KEY, DRAFT_MARKER_KEY: marker}
    )


def _safe_source(session: dict[str, Any]) -> dict[str, str] | None:
    sha256 = str(session.get("source_paper_sha256") or "").strip().casefold()
    value = str(session.get("source_paper_path") or "").strip()
    if not value or len(sha256) != 64:
        return None
    safe_name = value.replace("\\", "/").rsplit("/", 1)[-1]
    suffix = Path(safe_name).suffix.casefold()
    if suffix not in {".docx", ".pdf"}:
        return None
    return {"safe_filename": safe_name, "suffix": suffix, "sha256_prefix": sha256[:12]}


__all__ = [
    "PublishedConfig",
    "ConfigRevisionConflict",
    "ConfigSaveResult",
    "LoadedEditorConfig",
    "config_revision",
    "editor_response",
    "load_editor_config",
    "publish_generated_config",
    "refresh_mapping_after_config_save",
    "refresh_template_mapping_from_session",
    "remove_published_config",
    "save_editor_config",
    "save_editor_config_and_refresh_mapping",
    "publish_legacy_config_and_refresh_mapping",
]
