from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from contextlib import closing
from datetime import datetime

from backend.api.app import ApiError
from backend.api.dependencies import (
    get_config_source_service,
    get_data_root,
    get_grading_db,
    get_job_manager,
    get_question_bank_db_path,
    get_question_bank_read_service,
    get_session_repository,
    get_taxonomy_governance,
    get_taxonomy_suggestion_service,
    get_upload_config_dir,
    get_scan_grading_workspace,
    get_review_application_service,
    get_media_service,
    get_ops_self_check_service,
)
from backend.api.routers.jobs import _job_response
from backend.api.schemas.jobs import JobResponse
from backend.api.schemas.sessions import (
    AnswerRegionListResponse,
    AnswerRegionResponse,
    CreateSessionDraftRequest,
    CreateSessionRequest,
    DeleteSessionRequest,
    PermanentDeleteSessionRequest,
    QuestionBankSyncRequest,
    RenameSessionRequest,
    SessionDeletionImpactResponse,
    SessionDetail,
    SessionListResponse,
    SessionPendingCleanupListResponse,
    SessionPermanentDeletionResponse,
    SessionProgress,
    SessionQuestionBankAnalysisStatus,
    SessionSummary,
    SessionTemplateResponse,
)
from backend.config_workspace.drafts import create_session_draft
from backend.config_workspace.publish import load_editor_config
from backend.config_workspace.sources import (
    AmbiguousAssetDecision,
    ConfigSourceError,
    ConfigSourceService,
)
from backend.jobs.manager import (
    ActiveJobExistsError,
    JobManager,
    UnsupportedJobTypeError,
)
from backend.jobs.store import QuestionBankSyncRequestTokenConflictError
from backend.repositories.access import GradingRepositoryAccess
from backend.repositories.sessions import (
    SessionDeletionActiveWork,
    SessionDeletionConfirmationMismatch,
    SessionDeletionRevisionConflict,
    SessionNameConflict,
    SessionRepositoryGateway,
)
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.taxonomy.curriculum_catalog import (
    curriculum_volume,
    infer_curriculum_volume_from_text,
)
from backend.files.session_cleanup import (
    SessionDerivedTrainingDataExists,
    SessionPermanentDeletionRecoveryFailed,
    SessionStorageDeletionIncomplete,
    hard_delete_session_from_archive,
    list_pending_session_permanent_deletions,
    preview_session_permanent_deletion,
    question_bank_session_reference_impact,
    recover_interrupted_session_permanent_deletion,
    session_lifecycle_guard,
)

router = APIRouter(prefix="/api", tags=["sessions"])
_SESSION_NAME_CONFLICT_MESSAGE = "已存在同名考试，请换一个名称。"


class OriginalsActionRequest(BaseModel):
    expected_revision: str
    confirmation_phrase: str = ""


def _originals_snapshot(session_id, db, data_root, workspace, review_service, backup_service=None, *, shared_refs=None):
    from backend.files.session_originals import measure_session_originals, originals_state, _receipt
    from backend.review.manual_context import current_manual_context
    try:
        impact = db.sessions.session_deletion_impact(session_id)
    except ValueError as exc:
        raise ApiError(404, "session_not_found", "未找到这场考试。") from exc
    session = impact["session"]
    if session.get("is_deleted"):
        raise ApiError(404, "session_not_found", "未找到这场考试。")
    state = originals_state(data_root, session_id)
    measured = measure_session_originals(db, data_root, session_id, shared_refs=shared_refs)
    with closing(db._connect()) as conn:
        unmatched = conn.execute("SELECT COUNT(*) FROM exam_papers WHERE session_id = ? AND COALESCE(match_status, '') != 'matched'", (session_id,)).fetchone()[0]
        latest_paper = conn.execute("SELECT MAX(created_at) FROM exam_papers WHERE session_id = ?", (session_id,)).fetchone()[0]
    reason = None
    if impact["active_jobs"] or impact["active_grading_runs"]:
        reason = "这场考试还有正在运行的任务"
    # get_workspace creates upload manifests; this read must only inspect existing state.
    elif workspace._replacement_manifest_path(session_id).exists() or workspace._replacement_commit_path(session_id).exists():
        reason = "正在替换答卷，完成后再清理"
    elif not impact["permanent_counts"]["grading_results"]:
        reason = "还没有批改结果"
    elif unmatched:
        reason = "还有答卷没有对应到学生"
    else:
        questions = review_service.list_questions(session_id, session, scope=None, manual_context=current_manual_context(session_id, workspace, read_only=True) if workspace._manifest_path(session_id).exists() else None)
        if any(q.needs_review_count + q.ungraded_count + q.failed_count for q in questions):
            reason = "复核完成后可清理"
    latest_backup = None
    covered = False
    if backup_service is not None:
        backups = [item for item in backup_service.list_backups(100)["items"] if item["kind"] == "zip"]
        if backups:
            latest_backup = backups[0]["created_at"]
            try:
                stamp = datetime.fromisoformat(latest_backup)
                dates = [latest_paper, _receipt(data_root, session_id).get("scans_released_at")]
                covered = bool(latest_paper) and all(stamp > datetime.fromisoformat(t) for t in dates if t)
            except (ValueError, TypeError):
                covered = False
    return {**measured, "originals_state": state, "revision": f"{impact['revision']}:{state}",
            "can_release_scans": reason is None and measured["release_bytes"] > 0 and state not in {"clearing", "cleared"},
            "can_clear": reason is None and state != "cleared", "blocked_reason": reason,
            "confirmation_phrase": "确认清除", "latest_backup_at": latest_backup, "backup_covers_originals": covered}


@router.get("/sessions/{session_id}/originals")
def get_session_originals(session_id: int, db=Depends(get_grading_db), data_root: Path=Depends(get_data_root),
                          workspace=Depends(get_scan_grading_workspace), review_service=Depends(get_review_application_service),
                          backup_service=Depends(get_ops_self_check_service)):
    with session_lifecycle_guard(session_id):
        return _originals_snapshot(session_id, db, data_root, workspace, review_service, backup_service)


def _check_originals_action(snapshot, request, *, clear):
    if clear and request.confirmation_phrase != "确认清除":
        raise ApiError(422, "originals_confirmation_mismatch", "请输入「确认清除」继续。")
    if not (clear and snapshot["originals_state"] == "clearing") and snapshot["revision"] != request.expected_revision:
        raise ApiError(409, "originals_revision_changed", "这场考试的状态已变化，请刷新后重新确认。")
    if not snapshot["can_clear" if clear else "can_release_scans"]:
        raise ApiError(409, "originals_not_ready", snapshot["blocked_reason"] or "当前没有可清理的原卷文件。")


@router.post("/sessions/{session_id}/originals/release-scans")
def release_original_scans(session_id: int, request: OriginalsActionRequest, db=Depends(get_grading_db),
                           data_root: Path=Depends(get_data_root), workspace=Depends(get_scan_grading_workspace),
                           review_service=Depends(get_review_application_service)):
    from backend.files.session_originals import release_session_scans
    with session_lifecycle_guard(session_id):
        snapshot = _originals_snapshot(session_id, db, data_root, workspace, review_service)
        _check_originals_action(snapshot, request, clear=False)
        try:
            return release_session_scans(db, data_root, session_id)
        except OSError as exc:
            raise ApiError(409, "originals_release_incomplete", "部分扫描文件未能释放，请关闭占用文件后刷新状态。", {"retryable": True}) from exc


@router.post("/sessions/{session_id}/originals/clear")
def clear_original_pages(session_id: int, request: OriginalsActionRequest, db=Depends(get_grading_db),
                         data_root: Path=Depends(get_data_root), workspace=Depends(get_scan_grading_workspace),
                         review_service=Depends(get_review_application_service), media_service=Depends(get_media_service)):
    from backend.files.session_originals import clear_session_originals
    with session_lifecycle_guard(session_id):
        snapshot = _originals_snapshot(session_id, db, data_root, workspace, review_service)
        if snapshot["originals_state"] == "cleared" and request.confirmation_phrase == "确认清除":
            return {"originals_state": "cleared", "freed_bytes": 0, "deleted_files": 0, "kept_unrendered": 0}
        _check_originals_action(snapshot, request, clear=True)
        try:
            return clear_session_originals(db, data_root, session_id, clear_crop_cache=media_service.clear_detail_crop_cache)
        except Exception as exc:
            raise ApiError(409, "originals_clear_incomplete", "原卷清理未完成，请刷新后点「继续清理」。", {"retryable": True}) from exc


def _bool(value: Any) -> bool:
    return bool(int(value or 0))


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _require_session(
    sessions: SessionRepositoryGateway,
    session_id: int,
) -> dict[str, Any]:
    session = sessions.get_grading_session(int(session_id))
    if session is None:
        raise ApiError(
            404,
            "session_not_found",
            "Session not found",
            {"session_id": int(session_id)},
        )
    return session


def _require_active_session(
    sessions: SessionRepositoryGateway,
    session_id: int,
) -> dict[str, Any]:
    session = _require_session(sessions, session_id)
    if _bool(session.get("is_deleted")):
        raise ApiError(
            409,
            "session_archived",
            "Session is archived",
            {"session_id": int(session_id)},
        )
    return session


def _session_summary(row: dict[str, Any]) -> SessionSummary:
    return SessionSummary(
        id=int(row["id"]),
        name=str(row["session_name"]),
        status=str(row["status"]),
        curriculum_volume_id=(
            str(row.get("curriculum_volume_id") or "").strip() or None
        ),
        is_deleted=_bool(row.get("is_deleted")),
        deleted_at=row.get("deleted_at"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _session_detail(row: dict[str, Any]) -> SessionDetail:
    summary = _session_summary(row).model_dump()
    return SessionDetail(
        **summary,
        rubric_path=str(row.get("rubric_path") or ""),
        answer_key_path=str(row.get("answer_key_path") or ""),
        template_config_path=row.get("template_config_path"),
        source_paper_path=row.get("source_paper_path"),
        source_paper_sha256=row.get("source_paper_sha256"),
        question_bank_sync_state=str(row.get("question_bank_sync_state") or "not_started"),
        question_bank_sync_details=_json_object(row.get("question_bank_sync_details_json")),
        question_bank_sync_error=row.get("question_bank_sync_error"),
        question_bank_sync_updated_at=row.get("question_bank_sync_updated_at"),
    )


def _template_response(row: dict[str, Any]) -> SessionTemplateResponse:
    return SessionTemplateResponse(
        id=int(row["id"]),
        session_id=int(row["session_id"]),
        pages={
            page: {"url": f"/api/sessions/{int(row['session_id'])}/template/pages/{page}"}
            for page in ("front", "back")
        },
        is_confirmed=_bool(row.get("is_confirmed")),
        regions_snapshot_pending=_bool(row.get("regions_snapshot_pending")),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _region_response(row: dict[str, Any]) -> AnswerRegionResponse:
    return AnswerRegionResponse(
        id=int(row["id"]),
        region_uuid=str(row["region_uuid"]),
        session_id=int(row["session_id"]),
        template_id=int(row["template_id"]),
        page=str(row.get("page") or ""),
        region_order=int(row.get("region_order") or 0),
        x=int(row.get("x") or 0),
        y=int(row.get("y") or 0),
        w=int(row.get("w") or 0),
        h=int(row.get("h") or 0),
        detected_question_id=row.get("detected_question_id"),
        mapped_question_id=row.get("mapped_question_id"),
        confidence=float(row.get("confidence") or 0.0),
        is_confirmed=_bool(row.get("is_confirmed")),
        mapping_status=str(row.get("mapping_status") or ""),
        multi_region_confirmed=_bool(row.get("multi_region_confirmed")),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _question_bank_sync_payload(
    *,
    session_id: int,
    mode: str,
    config_revision: str,
    source_paper_sha256: str,
    client_request_token: str,
    curriculum_volume_id: str,
    retry_of_job_id: int | None = None,
    question_ids: list[int] | None = None,
    deferred_analysis: dict[str, Any] | None = None,
    asset_overrides: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "session_id": int(session_id),
        "mode": str(mode),
        "config_revision": str(config_revision),
        "source_paper_sha256": str(source_paper_sha256),
        "curriculum_volume_id": str(curriculum_volume_id),
    }
    if retry_of_job_id is not None:
        identity["retry_of_job_id"] = int(retry_of_job_id)
    if question_ids is not None:
        identity["question_ids"] = list(question_ids)
    if deferred_analysis:
        identity.update(deferred_analysis)
    if asset_overrides:
        identity["asset_overrides"] = [dict(item) for item in asset_overrides]
    fingerprint = hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        **identity,
        "client_request_token": client_request_token,
        "client_request_fingerprint": fingerprint,
    }


def _resolve_sync_curriculum_volume(
    session: dict[str, Any],
    requested_volume_id: object,
) -> dict[str, Any]:
    requested = str(
        requested_volume_id or session.get("curriculum_volume_id") or ""
    ).strip()
    if requested:
        volume = curriculum_volume(volume_id=requested)
        if volume is None:
            raise ApiError(
                422,
                "curriculum_volume_invalid",
                "所选教材册别不在当前本地教材目录中。",
                {},
            )
        return volume
    source_name = Path(str(session.get("source_paper_path") or "")).name
    volume = infer_curriculum_volume_from_text(
        " ".join(
            item
            for item in (str(session.get("name") or ""), source_name)
            if item
        )
    )
    if volume is None:
        raise ApiError(
            409,
            "curriculum_volume_required",
            "入库前需要老师确认试卷对应的年级和上下册。",
            {},
        )
    return volume


def _explicit_curriculum_volume_id(value: object) -> str | None:
    requested = str(value or "").strip()
    if not requested:
        return None
    volume = curriculum_volume(volume_id=requested)
    if volume is None:
        raise ApiError(
            422,
            "curriculum_volume_invalid",
            "所选教材册别不在当前本地教材目录中。",
            {},
        )
    return str(volume["id"])


def _require_current_sync_inputs(
    db: GradingRepositoryAccess,
    session_id: int,
    request: QuestionBankSyncRequest,
) -> tuple[dict[str, Any], str]:
    try:
        loaded = load_editor_config(db, session_id)
    except KeyError as exc:
        raise ApiError(
            404,
            "session_not_found",
            "Session not found",
            {"session_id": int(session_id)},
        ) from exc
    if not loaded.configured:
        raise ApiError(
            409,
            "grading_config_not_ready",
            "Save the grading standard before importing the paper.",
            {"session_id": int(session_id)},
        )
    if loaded.revision != request.config_revision:
        raise ApiError(
            409,
            "config_revision_conflict",
            "The grading standard changed; reload before importing.",
            {"session_id": int(session_id)},
        )
    source_sha256 = str(
        loaded.session.get("source_paper_sha256") or ""
    ).strip().casefold()
    source_path = str(loaded.session.get("source_paper_path") or "").strip()
    if (
        len(source_sha256) != 64
        or any(char not in "0123456789abcdef" for char in source_sha256)
        or not source_path
    ):
        raise ApiError(
            409,
            "source_paper_unavailable",
            "The archived source paper is unavailable for question-bank import.",
            {"session_id": int(session_id)},
        )
    return loaded.session, source_sha256


def _resolve_sync_asset_overrides(
    source_service: ConfigSourceService,
    session_id: int,
    request: QuestionBankSyncRequest,
) -> list[dict[str, Any]] | None:
    if not request.asset_decisions:
        return None
    try:
        record = source_service.load_active_record(session_id=int(session_id))
        return list(
            source_service.resolve_asset_decision_overrides(
                record,
                [
                    AmbiguousAssetDecision(
                        candidate_id=item.candidate_id,
                        action=item.action,
                        question_id=item.question_id,
                        asset_kind=item.asset_kind,
                    )
                    for item in request.asset_decisions
                ],
            )
        )
    except (ConfigSourceError, ValueError) as exc:
        raise ApiError(
            422,
            "asset_decisions_stale",
            "图片归属决定已失效，请回到复核页重新确认图片归属后再提交入库。",
            {"session_id": int(session_id)},
        ) from exc


def _submit_question_bank_sync(
    manager: JobManager,
    payload: dict[str, Any],
) -> JobResponse:
    try:
        job, _created = manager.submit_idempotent_question_bank_sync(payload)
    except QuestionBankSyncRequestTokenConflictError as exc:
        raise ApiError(
            409,
            "question_bank_sync_token_conflict",
            "This request token was already used for another import.",
            {},
        ) from exc
    except ActiveJobExistsError as exc:
        raise ApiError(
            409,
            "question_bank_sync_in_progress",
            "Question-bank import or tagging is already running.",
            {},
        ) from exc
    except UnsupportedJobTypeError as exc:
        raise ApiError(
            503,
            "question_bank_sync_unavailable",
            "Question-bank import is unavailable.",
            {},
        ) from exc
    return _job_response(job)


@router.get("/sessions", response_model=SessionListResponse)
def list_sessions(
    include_deleted: bool = False,
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
) -> SessionListResponse:
    items = [
        _session_summary(row)
        for row in sessions.list_grading_sessions(include_deleted)
    ]
    return SessionListResponse(items=items, total=len(items))


@router.get(
    "/sessions/permanent-cleanups",
    response_model=SessionPendingCleanupListResponse,
)
def list_pending_permanent_cleanups(
    db: GradingRepositoryAccess = Depends(get_grading_db),
    data_root: Path = Depends(get_data_root),
) -> SessionPendingCleanupListResponse:
    items = list_pending_session_permanent_deletions(
        db,
        data_root=data_root,
    )
    return SessionPendingCleanupListResponse(items=items, total=len(items))


@router.get("/sessions/{session_id}", response_model=SessionDetail)
def get_session(
    session_id: int,
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
) -> SessionDetail:
    return _session_detail(_require_session(sessions, session_id))


@router.get("/sessions/{session_id}/progress", response_model=SessionProgress)
def get_session_progress(
    session_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
) -> SessionProgress:
    _require_session(sessions, session_id)
    return SessionProgress(**db.papers.get_session_progress(int(session_id)))


@router.get(
    "/sessions/{session_id}/question-bank-status",
    response_model=SessionQuestionBankAnalysisStatus,
)
def session_question_bank_status(
    session_id: int,
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
    service: QuestionBankReadService = Depends(get_question_bank_read_service),
    taxonomy_governance: Any = Depends(get_taxonomy_governance),
    taxonomy_suggestions: Any = Depends(get_taxonomy_suggestion_service),
) -> SessionQuestionBankAnalysisStatus:
    _require_session(sessions, session_id)
    proposal_page = taxonomy_governance.list_proposals(status="pending")
    proposal_page = taxonomy_suggestions.contextualize_proposal_page(
        proposal_page
    )
    pending_taxonomy_count = int(
        proposal_page.get("counts", {}).get("pending", 0)
    )
    return SessionQuestionBankAnalysisStatus(
        **service.session_analysis_status(session_id),
        pending_taxonomy_count=pending_taxonomy_count,
    )


@router.get("/sessions/{session_id}/template", response_model=SessionTemplateResponse)
def get_session_template(
    session_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
) -> SessionTemplateResponse:
    _require_session(sessions, session_id)
    template = db.templates.get_session_template(int(session_id))
    if template is None:
        raise ApiError(
            404,
            "template_not_found",
            "Session template not found",
            {"session_id": int(session_id)},
        )
    return _template_response(template)


@router.get("/sessions/{session_id}/regions", response_model=AnswerRegionListResponse)
def list_answer_regions(
    session_id: int,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
) -> AnswerRegionListResponse:
    _require_session(sessions, session_id)
    items = [_region_response(row) for row in db.templates.list_answer_regions(int(session_id))]
    return AnswerRegionListResponse(items=items, total=len(items))


@router.post("/sessions", response_model=SessionDetail, status_code=201)
def create_session(
    request: CreateSessionRequest,
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
) -> SessionDetail:
    try:
        volume_id = _explicit_curriculum_volume_id(request.curriculum_volume_id)
        session_id = sessions.create_grading_session(
            request.name,
            request.rubric_path,
            request.answer_key_path,
            source_paper_path=request.source_paper_path,
            source_paper_sha256=request.source_paper_sha256,
            curriculum_volume_id=volume_id,
        )
    except SessionNameConflict as exc:
        raise ApiError(
            409,
            "session_name_conflict",
            _SESSION_NAME_CONFLICT_MESSAGE,
        ) from exc
    except ValueError as exc:
        raise ApiError(400, "invalid_session", str(exc)) from exc
    return _session_detail(_require_session(sessions, session_id))


@router.post("/sessions/drafts", response_model=SessionSummary, status_code=201)
def create_session_draft_route(
    request: CreateSessionDraftRequest,
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
    upload_config_dir: Path = Depends(get_upload_config_dir),
) -> SessionSummary:
    try:
        volume_id = _explicit_curriculum_volume_id(request.curriculum_volume_id)
        session_id = create_session_draft(
            sessions,
            upload_config_dir,
            name=request.name,
            curriculum_volume_id=volume_id,
        )
    except SessionNameConflict as exc:
        raise ApiError(
            409,
            "session_name_conflict",
            _SESSION_NAME_CONFLICT_MESSAGE,
        ) from exc
    except ValueError as exc:
        raise ApiError(400, "invalid_session_draft", str(exc)) from exc
    return _session_summary(_require_session(sessions, session_id))


@router.patch("/sessions/{session_id}", response_model=SessionSummary)
def rename_session(
    session_id: int,
    request: RenameSessionRequest,
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
) -> SessionSummary:
    _require_active_session(sessions, session_id)
    curriculum_provided = "curriculum_volume_id" in request.model_fields_set
    if request.name is None and not curriculum_provided:
        raise ApiError(400, "invalid_session_update", "No session fields were provided.")
    volume_id = (
        _explicit_curriculum_volume_id(request.curriculum_volume_id)
        if curriculum_provided
        else None
    )
    try:
        sessions.update_grading_session(
            int(session_id),
            name=request.name,
            curriculum_volume_id=volume_id,
            curriculum_volume_provided=curriculum_provided,
        )
    except SessionNameConflict as exc:
        raise ApiError(
            409,
            "session_name_conflict",
            _SESSION_NAME_CONFLICT_MESSAGE,
        ) from exc
    return _session_summary(_require_active_session(sessions, session_id))


@router.post(
    "/sessions/{session_id}/question-bank-sync",
    response_model=JobResponse,
    status_code=202,
)
def submit_session_question_bank_sync(
    session_id: int,
    request: QuestionBankSyncRequest,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
    manager: JobManager = Depends(get_job_manager),
    source_service: ConfigSourceService = Depends(get_config_source_service),
) -> JobResponse:
    session, source_sha256 = _require_current_sync_inputs(
        db,
        session_id,
        request,
    )
    volume = _resolve_sync_curriculum_volume(
        session,
        request.curriculum_volume_id,
    )
    sessions.update_grading_session(
        int(session_id),
        curriculum_volume_id=str(volume["id"]),
        curriculum_volume_provided=True,
    )
    payload = _question_bank_sync_payload(
        session_id=session_id,
        mode="sync",
        config_revision=request.config_revision,
        source_paper_sha256=source_sha256,
        client_request_token=request.client_request_token,
        curriculum_volume_id=str(volume["id"]),
        asset_overrides=_resolve_sync_asset_overrides(
            source_service,
            session_id,
            request,
        ),
    )
    return _submit_question_bank_sync(manager, payload)


@router.post(
    "/sessions/{session_id}/question-bank-sync/{job_id}/retry",
    response_model=JobResponse,
    status_code=202,
)
def retry_session_question_bank_sync(
    session_id: int,
    job_id: int,
    request: QuestionBankSyncRequest,
    db: GradingRepositoryAccess = Depends(get_grading_db),
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
    manager: JobManager = Depends(get_job_manager),
    source_service: ConfigSourceService = Depends(get_config_source_service),
) -> JobResponse:
    session, source_sha256 = _require_current_sync_inputs(
        db,
        session_id,
        request,
    )
    source_job = manager.get(job_id)
    if (
        source_job is None
        or source_job.job_type != "question_bank_sync"
        or int(source_job.payload.get("session_id") or 0) != int(session_id)
    ):
        raise ApiError(
            404,
            "question_bank_sync_job_not_found",
            "Question-bank sync job not found.",
            {"job_id": int(job_id)},
        )
    if source_job.status not in {"succeeded", "failed", "cancelled"}:
        raise ApiError(
            409,
            "question_bank_sync_in_progress",
            "Question-bank import or tagging is still running.",
            {"job_id": int(job_id)},
        )
    if (
        source_job.payload.get("config_revision") != request.config_revision
        or source_job.payload.get("source_paper_sha256") != source_sha256
    ):
        raise ApiError(
            409,
            "question_bank_sync_version_conflict",
            "The source paper or grading standard changed; start a new import.",
            {"job_id": int(job_id)},
        )
    volume = _resolve_sync_curriculum_volume(
        session,
        request.curriculum_volume_id
        or source_job.payload.get("curriculum_volume_id"),
    )
    sessions.update_grading_session(
        int(session_id),
        curriculum_volume_id=str(volume["id"]),
        curriculum_volume_provided=True,
    )
    raw_failed_ids = source_job.result.get("failed_question_ids")
    failed_ids = (
        sorted(
            {
                int(item)
                for item in raw_failed_ids
                if isinstance(item, int) and int(item) > 0
            }
        )
        if isinstance(raw_failed_ids, list)
        else []
    )
    retryable = bool(source_job.result.get("retryable"))
    if (
        source_job.status == "succeeded"
        and not retryable
        and not failed_ids
    ):
        raise ApiError(
            409,
            "question_bank_sync_retry_not_available",
            "This question-bank sync has no retryable work.",
            {"job_id": int(job_id)},
        )
    mode = "tag_retry" if failed_ids else "sync_retry"
    payload = _question_bank_sync_payload(
        session_id=session_id,
        mode=mode,
        config_revision=request.config_revision,
        source_paper_sha256=source_sha256,
        client_request_token=request.client_request_token,
        curriculum_volume_id=str(volume["id"]),
        retry_of_job_id=job_id,
        question_ids=failed_ids if failed_ids else None,
        deferred_analysis={
            key: source_job.payload[key]
            for key in (
                "analysis_artifact_id",
                "analysis_artifact_hash",
                "analysis_source_id",
                "analysis_source_revision",
            )
            if key in source_job.payload
        },
        asset_overrides=_resolve_sync_asset_overrides(
            source_service,
            session_id,
            request,
        ),
    )
    return _submit_question_bank_sync(manager, payload)


@router.get(
    "/sessions/{session_id}/deletion-impact",
    response_model=SessionDeletionImpactResponse,
)
def get_session_deletion_impact(
    session_id: int,
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
    db: GradingRepositoryAccess = Depends(get_grading_db),
    data_root: Path = Depends(get_data_root),
    question_bank_db_path: Path = Depends(get_question_bank_db_path),
) -> SessionDeletionImpactResponse:
    # Preview can restore storage left by an interrupted delete. It must not
    # run while the same exam is between staging and database commit.
    with session_lifecycle_guard(int(session_id)):
        try:
            impact = sessions.session_deletion_impact(int(session_id))
        except ValueError as exc:
            raise ApiError(
                404,
                "session_not_found",
                "Session not found",
                {"session_id": int(session_id)},
            ) from exc
        session = _session_summary(impact["session"])
        active_jobs = int(impact["active_jobs"])
        active_grading_runs = int(impact["active_grading_runs"])
        storage_counts = preview_session_permanent_deletion(
            db,
            int(session_id),
            data_root=data_root,
        )
        question_bank_impact = question_bank_session_reference_impact(
            question_bank_db_path,
            int(session_id),
        )
        blocking_training_tasks = [
            str(value)
            for value in question_bank_impact.pop("blocking_training_tasks", [])
        ]
        can_archive = (
            not session.is_deleted
            and active_jobs == 0
            and active_grading_runs == 0
        )
        can_permanently_delete = (
            active_jobs == 0
            and active_grading_runs == 0
            and not blocking_training_tasks
        )
        return SessionDeletionImpactResponse(
            session=session,
            revision=str(impact["revision"]),
            active_jobs=active_jobs,
            active_grading_runs=active_grading_runs,
            can_archive=can_archive,
            can_permanently_delete=can_permanently_delete,
            permanent_delete_phrase=f"永久删除 {session.name}",
            permanent_counts={
                str(key): max(0, int(value))
                for key, value in impact["permanent_counts"].items()
            },
            storage_counts=storage_counts,
            question_bank_counts={
                str(key): max(0, int(value))
                for key, value in question_bank_impact.items()
            },
            blocking_training_tasks=blocking_training_tasks,
            can_delete=can_permanently_delete,
        )


@router.delete("/sessions/{session_id}", response_model=SessionDetail)
def soft_delete_session(
    session_id: int,
    request: DeleteSessionRequest,
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
) -> SessionDetail:
    try:
        with session_lifecycle_guard(int(session_id)):
            deleted = sessions.soft_delete_grading_session_protected(
                int(session_id),
                expected_revision=request.expected_revision,
                confirmation_name=request.confirmation_name,
            )
    except ValueError as exc:
        if isinstance(exc, SessionDeletionConfirmationMismatch):
            raise ApiError(
                422,
                "session_delete_confirmation_mismatch",
                "The confirmation name does not match the session name",
                {"session_id": int(session_id)},
            ) from exc
        raise ApiError(
            404,
            "session_not_found",
            "Session not found",
            {"session_id": int(session_id)},
        ) from exc
    except SessionDeletionRevisionConflict as exc:
        raise ApiError(
            409,
            "session_delete_revision_conflict",
            "The session changed after deletion was reviewed",
            {"session_id": int(session_id)},
        ) from exc
    except SessionDeletionActiveWork as exc:
        raise ApiError(
            409,
            "session_delete_active_work",
            "The session still has active work",
            {
                "session_id": int(session_id),
                "active_jobs": exc.active_jobs,
                "active_grading_runs": exc.active_grading_runs,
            },
        ) from exc
    return _session_detail(deleted)


@router.delete(
    "/sessions/{session_id}/permanent",
    response_model=SessionPermanentDeletionResponse,
)
def permanently_delete_session(
    session_id: int,
    request: PermanentDeleteSessionRequest,
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
    db: GradingRepositoryAccess = Depends(get_grading_db),
    data_root: Path = Depends(get_data_root),
    question_bank_db_path: Path = Depends(get_question_bank_db_path),
) -> SessionPermanentDeletionResponse:
    try:
        with session_lifecycle_guard(int(session_id)):
            result = recover_interrupted_session_permanent_deletion(
                db,
                int(session_id),
                data_root=data_root,
                question_bank_db_path=question_bank_db_path,
            )
            if result is None:
                sessions.assert_permanent_deletion_ready(
                    int(session_id),
                    expected_revision=request.expected_revision,
                    confirmation_phrase=request.confirmation_phrase,
                )
                result = hard_delete_session_from_archive(
                    db,
                    int(session_id),
                    data_root=data_root,
                    question_bank_db_path=question_bank_db_path,
                    expected_revision=request.expected_revision,
                )
    except SessionDeletionConfirmationMismatch as exc:
        raise ApiError(
            422,
            "session_permanent_delete_confirmation_mismatch",
            "The permanent deletion phrase does not match",
            {"session_id": int(session_id)},
        ) from exc
    except SessionDeletionRevisionConflict as exc:
        raise ApiError(
            409,
            "session_delete_revision_conflict",
            "The session changed after permanent deletion was reviewed",
            {"session_id": int(session_id)},
        ) from exc
    except SessionDeletionActiveWork as exc:
        raise ApiError(
            409,
            "session_delete_active_work",
            "The session still has active work",
            {
                "session_id": int(session_id),
                "active_jobs": exc.active_jobs,
                "active_grading_runs": exc.active_grading_runs,
            },
        ) from exc
    except SessionDerivedTrainingDataExists as exc:
        raise ApiError(
            409,
            "session_permanent_delete_training_snapshot_exists",
            "Derived training tasks still contain this exam",
            {
                "session_id": int(session_id),
                "task_codes": list(exc.task_codes),
            },
        ) from exc
    except SessionStorageDeletionIncomplete as exc:
        raise ApiError(
            409,
            "session_permanent_delete_storage_incomplete",
            "Some exam files are still open and could not be deleted",
            {
                "session_id": int(session_id),
                "failed_path_count": len(exc.failed_paths),
            },
        ) from exc
    except SessionPermanentDeletionRecoveryFailed as exc:
        raise ApiError(
            409,
            "session_permanent_delete_recovery_incomplete",
            (
                "The exam was deleted, but cleanup still needs to be retried"
                if exc.deletion_committed
                else "The delete was stopped because its files could not be restored"
            ),
            {
                "session_id": int(session_id),
                "deletion_committed": exc.deletion_committed,
                "recovery_phase": exc.phase,
                "retryable": True,
            },
        ) from exc
    except ValueError as exc:
        raise ApiError(
            404,
            "session_not_found",
            "Session not found",
            {"session_id": int(session_id)},
        ) from exc
    return SessionPermanentDeletionResponse(**result)


@router.post("/sessions/{session_id}/restore", response_model=SessionDetail)
def restore_session(
    session_id: int,
    sessions: SessionRepositoryGateway = Depends(get_session_repository),
) -> SessionDetail:
    with session_lifecycle_guard(int(session_id)):
        _require_session(sessions, session_id)
        sessions.restore_grading_session(int(session_id))
    return _session_detail(_require_session(sessions, session_id))
