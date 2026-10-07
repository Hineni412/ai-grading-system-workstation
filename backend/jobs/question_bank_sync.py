from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.config_workspace.deferred_analysis import (
    DeferredAnalysisArtifact,
    DeferredAnalysisArtifactStore,
)
from backend.config_workspace.publish import LoadedEditorConfig, load_editor_config
from path_manager import resolve_stored_file_path
from question_bank.current_knowledge import CurrentFineTermResolver
from question_bank.database.schema import connect
from question_bank.parsers.type_detector import question_type_from_rubric
from question_bank.services.question_write_service import QuestionBankWriteService
from question_bank.services.source_question_link_service import (
    SourceQuestionLinkService,
    _normalize_question_number,
    _source_question_id,
)
from question_bank.solution_evidence import (
    SolutionEvidenceRepository,
)
from question_bank.taxonomy.curriculum_catalog import curriculum_volume
from question_bank.training_criteria import (
    ConfirmedQuestionAdoptionLink,
    DeferredCombinedProjectionWriter,
    ExistingTagProjectionWriter,
    QuestionAnalysisInputLoader,
    TrainingCriterionModule,
)
from question_bank.training_criteria.adapters import (
    BankQuestionTypeSuggestionWriter,
)

from .manager import JobCancellationRequested, JobContext
from .question_import import run_question_import_job
from .tagging_sync import _failure, run_tagging_sync_job

QuestionImportRunner = Callable[..., dict[str, object]]
TaggingSyncRunner = Callable[..., dict[str, object]]
LOGGER = logging.getLogger(__name__)


class StaleQuestionBankSyncError(RuntimeError):
    """The session source or editable grading configuration changed."""


@dataclass(frozen=True, slots=True)
class _ChildJobContext:
    parent: JobContext
    payload: dict[str, Any]
    progress_start: float
    progress_end: float
    stage: str

    @property
    def job_id(self) -> int:
        return self.parent.job_id

    @property
    def job_type(self) -> str:
        return self.parent.job_type

    @property
    def store(self):
        return self.parent.store

    def report(self, progress: float, _stage: str, detail: str = "") -> None:
        bounded = min(1.0, max(0.0, float(progress)))
        mapped = self.progress_start + (
            (self.progress_end - self.progress_start) * bounded
        )
        self.parent.report(mapped, self.stage, detail)

    @property
    def cancel_requested(self) -> bool:
        return self.parent.cancel_requested

    def raise_if_cancelled(self) -> None:
        self.parent.raise_if_cancelled()


def run_session_question_bank_sync_job(
    *,
    context: JobContext,
    grading_db: Any,
    question_bank_db_path: Path,
    data_root: Path,
    write_service: QuestionBankWriteService,
    question_import_runner: QuestionImportRunner = run_question_import_job,
    tagging_sync_runner: TaggingSyncRunner = run_tagging_sync_job,
    ai_service_factory: Callable[[], Any],
    taxonomy_governance: Any,
    analysis_artifact_root: Path | None = None,
) -> dict[str, object]:
    """Import an archived session paper, then run governed AI tagging.

    This job adopts a completed analysis into the question bank. It cannot
    publish, replace, or invalidate a grading rubric, and it must not start a
    second tagging run when the analysis artifact is missing.
    """

    payload = context.payload
    session_id = _positive_int(payload.get("session_id"), "session_id")
    source_sha256 = _sha256(
        payload.get("source_paper_sha256"),
        "source_paper_sha256",
    )
    config_revision = _sha256(
        payload.get("config_revision"),
        "config_revision",
    )
    mode = str(payload.get("mode") or "").strip()
    if mode not in {"sync", "sync_retry", "tag_retry"}:
        raise ValueError("unsupported question-bank sync mode")
    asset_overrides = _asset_overrides(payload.get("asset_overrides"))
    volume = curriculum_volume(volume_id=payload.get("curriculum_volume_id"))
    if volume is None:
        raise ValueError("question-bank sync requires a valid curriculum volume")
    deferred_artifact = _load_deferred_artifact(
        payload,
        analysis_artifact_root=analysis_artifact_root,
        session_id=session_id,
        curriculum_volume_id=str(volume["id"]),
    )

    loaded, source_path = _load_current_inputs(
        grading_db,
        session_id=session_id,
        source_sha256=source_sha256,
        config_revision=config_revision,
        data_root=Path(data_root),
        require_configured=deferred_artifact is None,
    )
    # Grading-rubric (LLM) question types govern the imported rows; the local
    # heuristic detector only fills questions the rubric does not cover.
    type_overrides = _rubric_type_overrides(loaded)
    if not context.store.claim_question_bank_sync_state_if_current(
        session_id=session_id,
        job_id=context.job_id,
        source_paper_sha256=source_sha256,
        config_revision=config_revision,
        expected_rubric_path=str(loaded.session.get("rubric_path") or ""),
        expected_answer_key_path=str(
            loaded.session.get("answer_key_path") or ""
        ),
        details=_versioned_sync_details(
            context=context,
            source_sha256=source_sha256,
            config_revision=config_revision,
            stage="tagging" if mode == "tag_retry" else "importing",
            mode=mode,
        ),
    ):
        raise StaleQuestionBankSyncError(
            "question-bank sync ownership changed"
        )

    link_service = SourceQuestionLinkService(Path(question_bank_db_path))
    link_rollback_changes: list[dict[str, Any]] = []
    try:
        context.raise_if_cancelled()
        if mode == "tag_retry":
            question_ids = _question_ids(payload.get("question_ids"))
            import_result: dict[str, object] = {
                "outcome": "complete",
                "successful_question_ids": question_ids,
                "failed_question_ids": [],
                "failed_count": 0,
                "retryable": False,
            }
        else:
            context.report(0.03, "question_bank_sync", "staging")
            source_bytes = source_path.read_bytes()
            if hashlib.sha256(source_bytes).hexdigest() != source_sha256:
                raise StaleQuestionBankSyncError(
                    "source paper changed before question-bank import"
                )
            source_filename = _source_filename(
                payload.get("source_safe_filename"),
                source_path=source_path,
                source_sha256=source_sha256,
            )
            upload = write_service.stage_upload(
                filename=source_filename,
                content=source_bytes,
            )
            request = write_service.create_import_request(upload_id=upload.upload_id)
            import_context = _ChildJobContext(
                parent=context,
                payload={
                    "request_id": request.request_id,
                    "paper_defaults": {
                        "year": str(datetime.now().astimezone().year),
                        "exam_type": "阶段练习",
                        "grade": str(volume["grade"]),
                        "semester": str(volume["semester"]),
                        "textbook_version": str(volume["textbook_version"]),
                    },
                    "asset_overrides": asset_overrides,
                    "type_overrides": type_overrides,
                },
                progress_start=0.05,
                progress_end=0.46,
                stage="question_bank_import",
            )
            import_result = question_import_runner(
                context=import_context,
                question_bank_db_path=Path(question_bank_db_path),
                data_root=Path(data_root),
                write_service=write_service,
            )
            question_ids = _question_ids(
                import_result.get("successful_question_ids"),
                allow_empty=True,
            )

        context.raise_if_cancelled()
        _load_current_inputs(
            grading_db,
            session_id=session_id,
            source_sha256=source_sha256,
            config_revision=config_revision,
            data_root=Path(data_root),
            require_configured=deferred_artifact is None,
        )

        if question_ids and deferred_artifact is None:
            raise ValueError(
                "question-bank intake requires the completed analysis artifact; "
                "refusing a second tagging run"
            )
        elif deferred_artifact is None:
            tagging_result = {
                "outcome": "failed",
                "requested_count": 0,
                "tagged_count": 0,
                "successful_question_ids": [],
                "failed_question_ids": [],
                "failed_count": 0,
                "review_count": 0,
                "proposal_ids": [],
                "retryable": bool(import_result.get("retryable")),
            }

        context.raise_if_cancelled()
        current, _ = _load_current_inputs(
            grading_db,
            session_id=session_id,
            source_sha256=source_sha256,
            config_revision=config_revision,
            data_root=Path(data_root),
            require_configured=deferred_artifact is None,
        )
        candidates = _bank_questions(
            Path(question_bank_db_path),
            question_ids,
            paper_id=_imported_paper_id(import_result),
        )
        source_questions = current.payload.get("rubric", {}).get("questions", [])
        if not isinstance(source_questions, list):
            source_questions = []
        answer_questions = current.payload.get("answer_key", {}).get(
            "questions", []
        )
        if not isinstance(answer_questions, list):
            answer_questions = []
        confirmed_rubrics = {
            str(item.get("question_id") or "").strip(): item
            for item in source_questions
            if isinstance(item, dict)
            and str(item.get("question_id") or "").strip()
        }
        confirmed_answers = {
            str(item.get("question_id") or "").strip(): item
            for item in answer_questions
            if isinstance(item, dict)
            and str(item.get("question_id") or "").strip()
        }
        link_result = link_service.confirm_imported_questions_for_session(
            grading_session_id=session_id,
            source_questions=[
                item for item in source_questions if isinstance(item, dict)
            ],
            imported_bank_questions=candidates,
            sync_job_id=context.job_id,
            sync_config_revision=config_revision,
            preserve_existing_confirmed=mode == "tag_retry",
        )
        raw_rollback_changes = link_result.pop("_rollback_changes", [])
        link_rollback_changes = [
            dict(item)
            for item in raw_rollback_changes
            if isinstance(item, dict)
        ]

        if deferred_artifact is not None:
            context.report(0.68, "question_bank_analysis_adoption", "adopting")
            tagging_result = _adopt_deferred_analysis(
                artifact=deferred_artifact,
                session_id=session_id,
                question_bank_db_path=Path(question_bank_db_path),
                data_root=Path(data_root),
                link_service=link_service,
                ai_service_factory=ai_service_factory,
                taxonomy_governance=taxonomy_governance,
                confirmed_rubrics=confirmed_rubrics,
                confirmed_answers=confirmed_answers,
                cancel_check=context.raise_if_cancelled,
            )

        # §7.2 hard rule: freeze the evidence/link snapshot after links are
        # confirmed and any adoption/tag retry has written evidence versions.
        # §7.2 hard rule: freeze the evidence/link snapshot after links are
        # confirmed and any adoption/tag retry has written evidence versions.
        # The rubric is annotated in place (§7.2 question-level records +
        # §7.1 step ids); only refs/ids are added, scoring content untouched.
        from question_bank.solution_evidence.evidence_snapshot import (
            freeze_session_evidence_snapshot,
        )
        snapshot_path = freeze_session_evidence_snapshot(
            Path(question_bank_db_path),
            grading_session_id=session_id,
            upload_config_dir=Path(data_root) / "config" / "uploaded",
            data_root=Path(data_root),
            rubric_path=resolve_stored_file_path(
                current.session.get("rubric_path"),
                data_root=Path(data_root),
            ),
            answer_key=(
                current.payload.get("answer_key")
                if isinstance(current.payload.get("answer_key"), Mapping)
                else None
            ),
        )

        result = _result(
            session_id=session_id,
            mode=mode,
            import_result=import_result,
            tagging_result=tagging_result,
            link_result=link_result,
        )
        result["evidence_snapshot_frozen"] = snapshot_path is not None
        if mode == "tag_retry":
            result = _merge_tag_retry_result(
                context=context,
                current=result,
                retried_question_ids=question_ids,
            )
        state = {
            "complete": "ready",
            "partial": "partial",
            "failed": "failed",
        }[str(result["outcome"])]
        if not _transition_owned_sync_state(
            context=context,
            session_id=session_id,
            source_sha256=source_sha256,
            config_revision=config_revision,
            state=state,
            details=_versioned_sync_details(
                context=context,
                source_sha256=source_sha256,
                config_revision=config_revision,
                outcome=result["outcome"],
                imported_count=result["imported_count"],
                new_question_count=result.get("new_question_count", 0),
                reused_count=result.get("reused_count", 0),
                tagged_count=result["tagged_count"],
                evidence_count=result["evidence_count"],
                criteria_count=result.get("criteria_count", 0),
                linked_count=result["linked_count"],
                failed_count=result["failed_count"],
                review_count=result["review_count"],
            ),
            error=(
                "Question-bank import or tagging needs attention."
                if state == "failed"
                else None
            ),
        ):
            link_service.rollback_imported_question_links(
                grading_session_id=session_id,
                sync_job_id=context.job_id,
                changes=link_rollback_changes,
            )
            link_rollback_changes = []
            raise StaleQuestionBankSyncError(
                "question-bank sync ownership changed"
            )
        link_rollback_changes = []
        if (
            deferred_artifact is not None
            and result["outcome"] == "complete"
            and not result.get("taxonomy_retry_question_ids")
        ):
            assert analysis_artifact_root is not None
            DeferredAnalysisArtifactStore(
                Path(analysis_artifact_root)
            ).discard(deferred_artifact.artifact_id)
            result["analysis_artifact_consumed"] = True
        context.report(1.0, "question_bank_sync", str(result["outcome"]))
        return result
    except StaleQuestionBankSyncError as exc:
        if link_rollback_changes:
            link_service.rollback_imported_question_links(
                grading_session_id=session_id,
                sync_job_id=context.job_id,
                changes=link_rollback_changes,
            )
            link_rollback_changes = []
        _transition_owned_sync_state(
            context=context,
            session_id=session_id,
            source_sha256=source_sha256,
            config_revision=config_revision,
            state="not_started",
            details=_versioned_sync_details(
                context=context,
                source_sha256=source_sha256,
                config_revision=config_revision,
                stage="stale",
                reason=_stale_reason(exc),
                retryable=False,
            ),
        )
        raise
    except JobCancellationRequested:
        if link_rollback_changes:
            link_service.rollback_imported_question_links(
                grading_session_id=session_id,
                sync_job_id=context.job_id,
                changes=link_rollback_changes,
            )
            link_rollback_changes = []
        _transition_owned_sync_state(
            context=context,
            session_id=session_id,
            source_sha256=source_sha256,
            config_revision=config_revision,
            state="partial",
            details=_versioned_sync_details(
                context=context,
                source_sha256=source_sha256,
                config_revision=config_revision,
                stage="cancelled",
                retryable=True,
            ),
            error=None,
        )
        raise
    except Exception as exc:
        failure_code = _safe_sync_failure_code(exc)
        LOGGER.exception(
            "Session question-bank sync failed for session_id=%s job_id=%s",
            session_id,
            context.job_id,
        )
        if link_rollback_changes:
            link_service.rollback_imported_question_links(
                grading_session_id=session_id,
                sync_job_id=context.job_id,
                changes=link_rollback_changes,
            )
            link_rollback_changes = []
        _transition_owned_sync_state(
            context=context,
            session_id=session_id,
            source_sha256=source_sha256,
            config_revision=config_revision,
            state="failed",
            details=_versioned_sync_details(
                context=context,
                source_sha256=source_sha256,
                config_revision=config_revision,
                stage="failed",
                failure_code=failure_code,
                retryable=True,
            ),
            error="Question-bank import or tagging failed.",
        )
        raise RuntimeError(
            f"session question-bank sync failed [{failure_code}]"
        ) from exc


def _safe_sync_failure_code(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return "local_source_file_missing"
    if isinstance(exc, PermissionError):
        return "local_storage_permission_denied"
    if isinstance(exc, ValueError):
        return "local_import_validation_failed"
    if isinstance(exc, OSError):
        return "local_storage_unavailable"
    return "local_import_failed"


def _load_current_inputs(
    grading_db: Any,
    *,
    session_id: int,
    source_sha256: str,
    config_revision: str,
    data_root: Path,
    require_configured: bool = True,
) -> tuple[LoadedEditorConfig, Path]:
    loaded = load_editor_config(grading_db, session_id)
    if require_configured and not loaded.configured:
        raise ValueError("grading configuration is not ready")
    if loaded.configured and loaded.revision != config_revision:
        raise StaleQuestionBankSyncError("grading configuration changed")
    current_source_sha = str(
        loaded.session.get("source_paper_sha256") or ""
    ).strip().casefold()
    if current_source_sha != source_sha256:
        raise StaleQuestionBankSyncError("source paper binding changed")
    source_path = resolve_stored_file_path(
        loaded.session.get("source_paper_path"),
        data_root=data_root,
    )
    if (
        not source_path.is_file()
        or source_path.suffix.casefold() not in {".docx", ".pdf"}
    ):
        raise ValueError("archived source paper is unavailable")
    actual_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    if actual_sha256 != source_sha256:
        raise StaleQuestionBankSyncError("archived source paper changed")
    return loaded, source_path


def _imported_paper_id(import_result: dict[str, object]) -> int | None:
    paper_ids = import_result.get("imported_paper_ids")
    if not isinstance(paper_ids, list) or len(paper_ids) != 1:
        return None
    try:
        paper_id = int(paper_ids[0])
    except (TypeError, ValueError):
        return None
    return paper_id if paper_id > 0 else None


def _bank_questions(
    db_path: Path,
    question_ids: list[int],
    *,
    paper_id: int | None = None,
) -> list[dict[str, Any]]:
    if not question_ids:
        return []
    placeholders = ",".join("?" for _ in question_ids)
    if paper_id is None:
        with connect(db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT id, question_number, question_text, source_file
                FROM questions
                WHERE COALESCE(is_deleted, 0) = 0
                  AND id IN ({placeholders})
                ORDER BY id
                """,
                question_ids,
            ).fetchall()
        return [dict(row) for row in rows]
    with connect(db_path) as conn:
        # Reused questions carry this paper's own number in the occurrence
        # record, not the canonical row's number.
        rows = conn.execute(
            f"""
            SELECT q.id,
                   COALESCE(occ.question_number, q.question_number) AS question_number,
                   q.question_text, q.source_file
            FROM questions q
            LEFT JOIN paper_question_occurrences occ
              ON occ.question_id = q.id AND occ.paper_id = ?
            WHERE COALESCE(q.is_deleted, 0) = 0
              AND q.id IN ({placeholders})
            ORDER BY q.id
            """,
            [int(paper_id), *question_ids],
        ).fetchall()
    return [dict(row) for row in rows]


def _result(
    *,
    session_id: int,
    mode: str,
    import_result: dict[str, object],
    tagging_result: dict[str, object],
    link_result: dict[str, object],
) -> dict[str, object]:
    successful_ids = _question_ids(
        tagging_result.get("successful_question_ids"),
        allow_empty=True,
    )
    failed_ids = _question_ids(
        tagging_result.get("failed_question_ids"),
        allow_empty=True,
    )
    imported_ids = _question_ids(
        import_result.get("successful_question_ids"),
        allow_empty=True,
    )
    unresolved = [
        str(item)
        for item in link_result.get("unresolved_question_ids", [])
        if str(item).strip()
    ] if isinstance(link_result.get("unresolved_question_ids"), list) else []
    import_complete = str(import_result.get("outcome") or "") == "complete"
    tagging_complete = str(tagging_result.get("outcome") or "") == "complete"
    unresolved_count = int(link_result.get("unresolved") or 0)
    if import_complete and tagging_complete and unresolved_count == 0:
        outcome = "complete"
    elif successful_ids or (mode != "tag_retry" and imported_ids):
        outcome = "partial"
    else:
        outcome = "failed"
    review_count = max(0, int(tagging_result.get("review_count") or 0))
    proposal_ids = [
        str(item)
        for item in tagging_result.get("proposal_ids", [])
        if str(item).strip()
    ] if isinstance(tagging_result.get("proposal_ids"), list) else []
    taxonomy_review_ids = _question_ids(
        tagging_result.get("taxonomy_review_question_ids"),
        allow_empty=True,
    )
    taxonomy_retry_ids = _question_ids(
        tagging_result.get("taxonomy_retry_question_ids"),
        allow_empty=True,
    )
    taxonomy_review_source_refs = list(
        dict.fromkeys(
            str(item or "").strip()
            for item in tagging_result.get("taxonomy_review_source_refs", [])
            if str(item or "").strip()
        )
    )
    failed_count = max(
        len(failed_ids),
        int(import_result.get("failed_count") or 0),
        int(tagging_result.get("failed_count") or 0),
    )
    result: dict[str, object] = {
        "session_id": session_id,
        "outcome": outcome,
        "imported_count": len(imported_ids),
        "question_count": max(
            len(imported_ids),
            int(tagging_result.get("requested_count") or 0),
            len(successful_ids) + len(failed_ids),
        ),
        "tagged_count": max(
            0,
            int(tagging_result.get("tagged_count") or 0),
        ),
        "complete_tagged_count": max(
            int(tagging_result.get("complete_tagged_count") or 0),
            len(successful_ids),
        ),
        "evidence_count": max(
            0,
            int(tagging_result.get("evidence_count") or 0),
        ),
        "criteria_count": max(
            0,
            int(tagging_result.get("criteria_count") or 0),
        ),
        "criteria_failed_question_ids": _question_ids(
            tagging_result.get("criteria_failed_question_ids"),
            allow_empty=True,
        ),
        "linked_count": int(link_result.get("confirmed") or 0),
        "reused_count": max(
            int(tagging_result.get("reused_count") or 0),
            int(import_result.get("analysis_reused_count") or 0),
        ),
        "new_question_count": max(
            0,
            len(imported_ids)
            - int(import_result.get("exact_duplicate_count") or 0),
        ),
        "failed_count": failed_count,
        "successful_question_ids": successful_ids,
        "failed_question_ids": failed_ids,
        "failures": list(tagging_result.get('failures') or []),
        "unresolved_question_ids": unresolved,
        "review_count": review_count,
        "proposal_ids": proposal_ids,
        "taxonomy_review_count": len(taxonomy_review_ids),
        "taxonomy_review_question_ids": taxonomy_review_ids,
        "taxonomy_review_source_refs": taxonomy_review_source_refs,
        "taxonomy_retry_question_ids": taxonomy_retry_ids,
        "retryable": bool(
            taxonomy_retry_ids
            or (
                failed_count
                and (
                    import_result.get("retryable")
                    or tagging_result.get("retryable")
                )
            )
        ),
    }
    if import_result.get("restore_required") is True:
        result.update(
            failure_category="duplicate_in_trash",
            restore_required=True,
            restore_paper_id=import_result.get("restore_paper_id"),
        )
    return result


def _merge_tag_retry_result(
    *,
    context: JobContext,
    current: dict[str, object],
    retried_question_ids: list[int],
) -> dict[str, object]:
    """Report the whole paper after a retry, not only the retried subset."""

    try:
        retry_of_job_id = _positive_int(
            context.payload.get("retry_of_job_id"),
            "retry_of_job_id",
        )
    except (TypeError, ValueError):
        return current
    previous_job = context.store.get_job(retry_of_job_id)
    if (
        previous_job is None
        or previous_job.job_type != "question_bank_sync"
        or int(previous_job.payload.get("session_id") or 0)
        != int(context.payload.get("session_id") or 0)
    ):
        return current
    previous = dict(previous_job.result)
    if not previous:
        return current

    retried = set(retried_question_ids)
    successful_ids = sorted(
        (
            set(_question_ids(
                previous.get("successful_question_ids"),
                allow_empty=True,
            ))
            - retried
        )
        | set(_question_ids(
            current.get("successful_question_ids"),
            allow_empty=True,
        ))
    )
    failed_ids = sorted(
        (
            set(_question_ids(
                previous.get("failed_question_ids"),
                allow_empty=True,
            ))
            - retried
        )
        | set(_question_ids(
            current.get("failed_question_ids"),
            allow_empty=True,
        ))
    )
    question_count = max(
        int(previous.get("question_count") or 0),
        int(previous.get("imported_count") or 0),
        int(current.get("question_count") or 0),
        len(successful_ids) + len(failed_ids),
    )
    proposal_ids = list(dict.fromkeys([
        *(
            str(item).strip()
            for item in previous.get("proposal_ids", [])
            if str(item).strip()
        ),
        *(
            str(item).strip()
            for item in current.get("proposal_ids", [])
            if str(item).strip()
        ),
    ]))
    taxonomy_review_ids = sorted(
        (
            set(_question_ids(
                previous.get("taxonomy_review_question_ids"),
                allow_empty=True,
            ))
            - retried
        )
        | set(_question_ids(
            current.get("taxonomy_review_question_ids"),
            allow_empty=True,
        ))
    )
    taxonomy_retry_ids = sorted(
        (
            set(_question_ids(
                previous.get("taxonomy_retry_question_ids"),
                allow_empty=True,
            ))
            - retried
        )
        | set(_question_ids(
            current.get("taxonomy_retry_question_ids"),
            allow_empty=True,
        ))
    )
    criteria_failed_ids = sorted(
        (
            set(_question_ids(
                previous.get("criteria_failed_question_ids"),
                allow_empty=True,
            ))
            - retried
        )
        | set(_question_ids(
            current.get("criteria_failed_question_ids"),
            allow_empty=True,
        ))
    )
    unresolved = [
        str(item).strip()
        for item in current.get("unresolved_question_ids", [])
        if str(item).strip()
    ]
    failed_count = len(failed_ids)
    complete = (
        question_count > 0
        and len(successful_ids) == question_count
        and failed_count == 0
        and not unresolved
    )
    merged = {
        **current,
        "outcome": "complete" if complete else (
            "partial" if successful_ids else "failed"
        ),
        "imported_count": max(
            int(previous.get("imported_count") or 0),
            int(current.get("imported_count") or 0),
        ),
        "question_count": question_count,
        "tagged_count": min(
            question_count,
            int(previous.get("tagged_count") or 0)
            + int(current.get("tagged_count") or 0),
        ),
        "complete_tagged_count": len(successful_ids),
        "evidence_count": len(successful_ids),
        "criteria_count": min(
            question_count,
            int(previous.get("criteria_count") or 0)
            + int(current.get("criteria_count") or 0),
        ),
        "criteria_failed_question_ids": criteria_failed_ids,
        "linked_count": max(
            int(previous.get("linked_count") or 0),
            int(current.get("linked_count") or 0),
        ),
        "failed_count": failed_count,
        "failures": [
            item for item in previous.get('failures', [])
            if isinstance(item, dict) and item.get('question_id') not in retried
        ] + list(current.get('failures') or []),
        "successful_question_ids": successful_ids,
        "failed_question_ids": failed_ids,
        "unresolved_question_ids": unresolved,
        "review_count": len(proposal_ids),
        "proposal_ids": proposal_ids,
        "taxonomy_review_count": len(taxonomy_review_ids),
        "taxonomy_review_question_ids": taxonomy_review_ids,
        "taxonomy_retry_question_ids": taxonomy_retry_ids,
        "retryable": bool(failed_ids or taxonomy_retry_ids),
    }
    return merged


def _load_deferred_artifact(
    payload: dict[str, Any],
    *,
    analysis_artifact_root: Path | None,
    session_id: int,
    curriculum_volume_id: str,
) -> DeferredAnalysisArtifact | None:
    fields = {
        "artifact_id": str(payload.get("analysis_artifact_id") or "").strip(),
        "content_hash": str(payload.get("analysis_artifact_hash") or "").strip(),
        "source_id": str(payload.get("analysis_source_id") or "").strip(),
        "source_revision": str(
            payload.get("analysis_source_revision") or ""
        ).strip(),
    }
    if not any(fields.values()):
        return None
    if not all(fields.values()) or analysis_artifact_root is None:
        raise ValueError("deferred question analysis hand-off is incomplete")
    artifact = DeferredAnalysisArtifactStore(Path(analysis_artifact_root)).load(
        fields["artifact_id"],
        session_id=session_id,
        source_id=fields["source_id"],
        source_revision=fields["source_revision"],
        curriculum_volume_id=curriculum_volume_id,
        expected_content_hash=fields["content_hash"],
    )
    if artifact.bundle.status != "succeeded":
        raise ValueError("deferred question analysis is not complete")
    return artifact


def _adopt_deferred_analysis(
    *,
    artifact: DeferredAnalysisArtifact,
    session_id: int,
    question_bank_db_path: Path,
    data_root: Path,
    link_service: SourceQuestionLinkService,
    ai_service_factory: Callable[[], Any],
    taxonomy_governance: Any,
    confirmed_rubrics: dict[str, dict[str, Any]] | None = None,
    confirmed_answers: dict[str, dict[str, Any]] | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> dict[str, object]:
    links = {
        str(item.get("source_question_id") or "").strip(): item
        for item in link_service.list_links(session_id)
        if str(item.get("status") or "") == "confirmed"
    }
    return _adopt_deferred_analysis_with_links(
        artifact=artifact,
        session_id=session_id,
        question_bank_db_path=question_bank_db_path,
        data_root=data_root,
        links=links,
        ai_service_factory=ai_service_factory,
        taxonomy_governance=taxonomy_governance,
        confirmed_rubrics=confirmed_rubrics,
        confirmed_answers=confirmed_answers,
        cancel_check=cancel_check,
    )


def _adopt_deferred_analysis_with_links(
    *,
    artifact: DeferredAnalysisArtifact,
    session_id: int,
    question_bank_db_path: Path,
    data_root: Path,
    links: dict[str, dict[str, Any]],
    ai_service_factory: Callable[[], Any],
    taxonomy_governance: Any,
    confirmed_rubrics: dict[str, dict[str, Any]] | None = None,
    confirmed_answers: dict[str, dict[str, Any]] | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> dict[str, object]:
    if cancel_check is not None:
        cancel_check()
    linked_items = [
        (item, links.get(item.source_question_ref))
        for item in artifact.bundle.items
    ]
    bank_ids = [
        int(link["bank_question_id"])
        for _item, link in linked_items
        if isinstance(link, dict)
    ]
    if not bank_ids:
        return {
            "outcome": "failed",
            "requested_count": len(artifact.bundle.items),
            "tagged_count": 0,
            "evidence_count": 0,
            "criteria_count": 0,
            "successful_question_ids": [],
            "failed_question_ids": [],
            "failed_count": len(artifact.bundle.items),
            "review_count": 0,
            "proposal_ids": [],
            "taxonomy_review_count": 0,
            "taxonomy_review_question_ids": [],
            "taxonomy_review_source_refs": [],
            "taxonomy_retry_question_ids": [],
            "retryable": True,
        }

    ai_service = ai_service_factory()
    loader = QuestionAnalysisInputLoader(
        db_path=question_bank_db_path,
        data_root=data_root,
    )
    adoption_ids = sorted({
        int(link["bank_question_id"])
        for item, link in linked_items
        if isinstance(link, dict) and item.reused_from_question_id is None
    })
    load_failures: dict[int, str] = {}
    provisional = loader.load(
        adoption_ids,
        curriculum_volume_id=artifact.curriculum_volume_id,
        load_failures=load_failures,
    ) if adoption_ids else ()
    contracts = ai_service.taxonomy_contracts(
        {item.question_id: item.tagging_context for item in provisional}
    ) if provisional else {}
    questions = {
        item.question_id: replace(
            item, taxonomy_contract=contracts.get(item.question_id, {})
        )
        for item in provisional
    }
    mapping_repository = CurrentFineTermResolver.from_active_database(
        question_bank_db_path
    )
    baseline_result = {
        "status": "current_standard",
        "release_id": mapping_repository.release_id,
    }
    bank_write_service = QuestionBankWriteService(
        question_bank_db_path,
        data_root=data_root,
    )
    tag_writer = ExistingTagProjectionWriter(
        write_service=bank_write_service,
        tagging_service=ai_service,
    )
    writer = DeferredCombinedProjectionWriter(
        tag_writer=tag_writer,
        mapping_repository=mapping_repository,
        evidence_repository=SolutionEvidenceRepository(question_bank_db_path),
        taxonomy_governance=taxonomy_governance,
        criterion_module=TrainingCriterionModule(question_bank_db_path),
        question_type_writer=BankQuestionTypeSuggestionWriter(
            write_service=bank_write_service,
        ),
    )
    adoption_results: list[dict[str, Any]] = []
    missing_links = 0
    for item, raw_link in linked_items:
        if cancel_check is not None:
            cancel_check()
        if not isinstance(raw_link, dict):
            missing_links += 1
            continue
        bank_question_id = int(raw_link["bank_question_id"])
        # Exact-duplicate reuse: the canonical question already owns the
        # analysis products, so adoption must not write them back.
        if item.reused_from_question_id is not None:
            if int(item.reused_from_question_id) != bank_question_id:
                missing_links += 1
                continue
            adoption_results.append(
                {
                    "question_id": bank_question_id,
                    "source_question_ref": item.source_question_ref,
                    "tag_status": "reused",
                    "evidence_status": "reused",
                    "criteria_status": "reused",
                    "reused_from_question_id": bank_question_id,
                }
            )
            continue
        question = questions.get(bank_question_id)
        if question is None:
            reason_code = load_failures[bank_question_id]
            adoption_results.append({
                'question_id': bank_question_id,
                'source_question_ref': item.source_question_ref,
                'tag_status': 'failed',
                'tag_error_category': 'validation',
                'evidence_status': 'failed',
                'evidence_error_category': 'validation',
                'criteria_status': 'failed',
                'criteria_error_category': 'validation',
                'input_error_code': reason_code,
                'message': _failure(bank_question_id, 'validation', reason_code=reason_code)['message'],
            })
            continue
        adoption_results.append(
            writer.adopt_linked(
                item,
                question=question,
                link=ConfirmedQuestionAdoptionLink(
                    source_question_ref=item.source_question_ref,
                    bank_question_id=bank_question_id,
                    confirmed_by=f"question-bank-sync:{session_id}",
                ),
                confirmed_rubric=(confirmed_rubrics or {}).get(
                    item.source_question_ref
                ),
                confirmed_answer=(confirmed_answers or {}).get(
                    item.source_question_ref
                ),
            )
        )
    successful_ids = [
        int(item["question_id"])
        for item in adoption_results
        if item.get("tag_status")
        in {"succeeded", "needs_taxonomy_review", "reused"}
        and item.get("evidence_status")
        in {"succeeded", "needs_taxonomy_review", "reused"}
        and item.get("criteria_status")
        in {"succeeded", "not_requested", "reused"}
    ]
    failed_ids = [
        int(item["question_id"])
        for item in adoption_results
        if item.get("tag_status")
        not in {"succeeded", "needs_taxonomy_review", "reused"}
        or item.get("evidence_status")
        not in {"succeeded", "needs_taxonomy_review", "reused"}
        or item.get("criteria_status")
        not in {"succeeded", "not_requested", "reused"}
    ]
    tagged_count = sum(
        item.get("tag_status") == "succeeded" for item in adoption_results
    )
    evidence_count = sum(
        item.get("evidence_status") == "succeeded" for item in adoption_results
    )
    criteria_count = sum(
        item.get("criteria_status") == "succeeded"
        for item in adoption_results
    )
    criteria_failed_ids = [
        int(item["question_id"])
        for item in adoption_results
        if item.get("criteria_status") == "failed"
    ]
    taxonomy_audit = tag_writer.audit_summary(
        artifact.bundle.operation_id,
        [int(item["question_id"]) for item in adoption_results],
    )
    observed_proposals = [
        dict(item)
        for item in taxonomy_audit.get("proposals", [])
        if isinstance(item, dict)
    ]
    proposal_ids = list(
        dict.fromkeys(
            str(item.get("proposal_id") or item.get("id") or "").strip()
            for item in observed_proposals
            if str(item.get("proposal_id") or item.get("id") or "").strip()
        )
    )
    proposal_ids = list(
        dict.fromkeys(
            [
                *proposal_ids,
                *(
                    str(proposal_id or "").strip()
                    for item in adoption_results
                    for proposal_id in item.get("taxonomy_proposal_ids", [])
                    if str(proposal_id or "").strip()
                ),
            ]
        )
    )
    evidence_taxonomy_review_ids = [
        int(item["question_id"])
        for item in adoption_results
        if item.get("taxonomy_review_required") is True
    ]
    taxonomy_retry_ids = [
        int(item["question_id"])
        for item in adoption_results
        if item.get("taxonomy_retry_required") is True
    ]
    tag_taxonomy_review_ids = [
        int(item["question_id"])
        for item in adoption_results
        if item.get("tag_status") == "needs_taxonomy_review"
    ]
    taxonomy_review_ids = list(
        dict.fromkeys(
            [
                *(
                    int(question_id)
                    for question_id in taxonomy_audit.get(
                        "proposal_question_ids", []
                    )
                ),
                *tag_taxonomy_review_ids,
                *evidence_taxonomy_review_ids,
            ]
        )
    )
    taxonomy_review_id_set = set(taxonomy_review_ids)
    taxonomy_review_source_refs = list(
        dict.fromkeys(
            str(item.get("source_question_ref") or "").strip()
            for item in adoption_results
            if int(item["question_id"]) in taxonomy_review_id_set
            and str(item.get("source_question_ref") or "").strip()
        )
    )
    reused_ids = [
        int(item["question_id"])
        for item in adoption_results
        if item.get("tag_status") == "reused"
    ]
    failed_count = len(failed_ids) + missing_links
    if failed_count == 0 and len(successful_ids) == len(artifact.bundle.items):
        outcome = "complete"
    elif successful_ids or tagged_count or evidence_count:
        outcome = "partial"
    else:
        outcome = "failed"
    return {
        "outcome": outcome,
        "requested_count": len(artifact.bundle.items),
        "tagged_count": tagged_count,
        "reused_count": len(reused_ids),
        "reused_question_ids": reused_ids,
        "complete_tagged_count": tagged_count,
        "evidence_count": evidence_count,
        "criteria_count": criteria_count,
        "criteria_failed_question_ids": criteria_failed_ids,
        "successful_question_ids": successful_ids,
        "failed_question_ids": failed_ids,
        "failed_count": failed_count,
        "review_count": len(proposal_ids),
        "proposal_ids": proposal_ids,
        "taxonomy_review_count": len(taxonomy_review_ids),
        "taxonomy_review_question_ids": taxonomy_review_ids,
        "taxonomy_review_source_refs": taxonomy_review_source_refs,
        "taxonomy_retry_question_ids": taxonomy_retry_ids,
        "retryable": failed_count > 0 or bool(taxonomy_retry_ids),
        "mapping_baseline": baseline_result,
        "adoption_results": adoption_results,
        "failures": [
            _failure(int(item['question_id']), 'validation', reason_code=str(item['input_error_code']))
            for item in adoption_results if item.get('input_error_code')
        ],
    }


def run_deferred_question_bank_intake(
    *,
    context: JobContext,
    session_id: int,
    artifact: DeferredAnalysisArtifact,
    source_filename: str,
    source_content: bytes,
    question_bank_db_path: Path,
    data_root: Path,
    ai_service_factory: Callable[[], Any],
    taxonomy_governance: Any,
    asset_overrides: list[dict[str, Any]] | None = None,
    type_overrides: Mapping[str, str] | None = None,
    confirmed_duplicates: Mapping[str, int] | None = None,
    question_import_runner: QuestionImportRunner = run_question_import_job,
) -> dict[str, object]:
    """Import and adopt completed analysis before score publication, without grading links.

    Teacher-confirmed asset placements and question types follow the import,
    matching the regular sync path.
    """

    if not artifact.bundle.source_fingerprints:
        raise ValueError("deferred question analysis has no source questions")
    clean_filename = Path(str(source_filename or "")).name
    if Path(clean_filename).suffix.casefold() not in {".docx", ".pdf"}:
        raise ValueError("question-bank intake source type is unsupported")
    content = bytes(source_content)
    if not content:
        raise ValueError("question-bank intake source is empty")
    volume = curriculum_volume(volume_id=artifact.curriculum_volume_id)
    if volume is None:
        raise ValueError("question-bank intake requires a valid curriculum volume")
    validated_asset_overrides = _asset_overrides(asset_overrides)
    clean_type_overrides = {
        str(number).strip(): str(question_type).strip()
        for number, question_type in dict(type_overrides or {}).items()
        if str(number).strip() and str(question_type).strip()
    }
    clean_confirmed_duplicates = {
        str(number).strip(): int(bank_id)
        for number, bank_id in dict(confirmed_duplicates or {}).items()
        if str(number).strip() and str(bank_id).strip().lstrip("-").isdigit()
        and int(bank_id) > 0
    }

    write_service = QuestionBankWriteService(
        Path(question_bank_db_path),
        data_root=Path(data_root),
    )
    upload = write_service.stage_upload(filename=clean_filename, content=content)
    request = write_service.create_import_request(upload_id=upload.upload_id)
    import_context = _ChildJobContext(
        parent=context,
        payload={
            "request_id": request.request_id,
            "paper_defaults": {
                "year": str(datetime.now().astimezone().year),
                "exam_type": "阶段练习",
                "grade": str(volume["grade"]),
                "semester": str(volume["semester"]),
                "textbook_version": str(volume["textbook_version"]),
            },
            "asset_overrides": validated_asset_overrides,
            "type_overrides": clean_type_overrides,
            "confirmed_duplicates": clean_confirmed_duplicates,
        },
        progress_start=0.91,
        progress_end=0.95,
        stage="question_bank_intake",
    )
    import_result = question_import_runner(
        context=import_context,
        question_bank_db_path=Path(question_bank_db_path),
        data_root=Path(data_root),
        write_service=write_service,
    )
    context.raise_if_cancelled()
    question_ids = _question_ids(
        import_result.get("successful_question_ids"),
        allow_empty=True,
    )
    candidates = _bank_questions(
        Path(question_bank_db_path),
        question_ids,
        paper_id=_imported_paper_id(import_result),
    )
    matcher = SourceQuestionLinkService(Path(question_bank_db_path))
    match_result = matcher.match_imported_questions(
        source_questions=[
            {"question_id": item.source_question_ref}
            for item in artifact.bundle.items
        ],
        imported_bank_questions=candidates,
    )
    raw_matches = match_result.get("matches")
    matches = {
        str(source_ref): {"bank_question_id": int(bank_question_id)}
        for source_ref, bank_question_id in (
            raw_matches.items() if isinstance(raw_matches, dict) else []
        )
    }
    tagging_result = _adopt_deferred_analysis_with_links(
        artifact=artifact,
        session_id=session_id,
        question_bank_db_path=Path(question_bank_db_path),
        data_root=Path(data_root),
        links=matches,
        ai_service_factory=ai_service_factory,
        taxonomy_governance=taxonomy_governance,
        cancel_check=context.raise_if_cancelled,
    )
    result = _result(
        session_id=session_id,
        mode="sync",
        import_result=import_result,
        tagging_result=tagging_result,
        link_result=match_result,
    )
    result["linked_count"] = 0
    result["provisional_match_count"] = int(match_result.get("confirmed") or 0)
    result["config_link_pending"] = True
    analysis_incomplete_count = max(
        0,
        len(artifact.bundle.source_fingerprints) - len(artifact.bundle.items),
    )
    result["analysis_incomplete_count"] = analysis_incomplete_count
    if analysis_incomplete_count:
        result["failed_count"] = max(
            int(result.get("failed_count") or 0),
            analysis_incomplete_count,
        )
        result["outcome"] = (
            "partial"
            if int(result.get("imported_count") or 0) > 0
            else "failed"
        )
    return result


def _transition_owned_sync_state(
    *,
    context: JobContext,
    session_id: int,
    source_sha256: str,
    config_revision: str,
    state: str,
    details: dict[str, object],
    error: str | None = None,
) -> bool:
    return context.store.transition_question_bank_sync_state_if_owned(
        session_id=session_id,
        job_id=context.job_id,
        source_paper_sha256=source_sha256,
        config_revision=config_revision,
        state=state,
        details=details,
        error=error,
    )


def _versioned_sync_details(
    *,
    context: JobContext,
    source_sha256: str,
    config_revision: str,
    **details: object,
) -> dict[str, object]:
    return {
        "job_id": context.job_id,
        "source_paper_sha256": source_sha256,
        "config_revision": config_revision,
        **details,
    }


def _stale_reason(exc: StaleQuestionBankSyncError) -> str:
    return {
        "grading configuration changed": "grading_configuration_changed",
        "source paper binding changed": "source_binding_changed",
        "archived source paper changed": "archived_source_changed",
        "source paper changed before question-bank import": "source_content_changed",
        "question-bank sync ownership changed": "sync_ownership_changed",
    }.get(str(exc), "sync_inputs_changed")


def _question_ids(value: object, *, allow_empty: bool = False) -> list[int]:
    if not isinstance(value, list):
        if allow_empty and value is None:
            return []
        raise ValueError("question_ids must be a list")
    result: list[int] = []
    seen: set[int] = set()
    for item in value:
        question_id = _positive_int(item, "question_id")
        if question_id not in seen:
            result.append(question_id)
            seen.add(question_id)
    if not result and not allow_empty:
        raise ValueError("question_ids must not be empty")
    return result


def _positive_int(value: object, field: str) -> int:
    try:
        clean = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if clean <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return clean


def _sha256(value: object, field: str) -> str:
    clean = str(value or "").strip().casefold()
    if len(clean) != 64 or any(char not in "0123456789abcdef" for char in clean):
        raise ValueError(f"{field} must be sha256")
    return clean


def _rubric_type_overrides(loaded: LoadedEditorConfig) -> dict[str, str]:
    """Map rubric (LLM) question types onto normalized bank question numbers."""
    payload = loaded.payload if isinstance(loaded.payload, dict) else {}
    rubric = payload.get("rubric")
    questions = rubric.get("questions") if isinstance(rubric, dict) else None
    overrides: dict[str, str] = {}
    if not isinstance(questions, list):
        return overrides
    for item in questions:
        if not isinstance(item, dict):
            continue
        number = _normalize_question_number(_source_question_id(item))
        question_type = question_type_from_rubric(item.get("question_type"))
        if number and question_type:
            overrides[number] = question_type
    return overrides


def _asset_overrides(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("asset overrides must be a list")
    overrides: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("asset override must be an object")
        digest = _sha256(item.get("sha256"), "asset override sha256")
        action = str(item.get("action") or "").strip()
        question_number = item.get("question_number")
        asset_kind = item.get("asset_kind")
        if action == "bind":
            if (
                not isinstance(question_number, str)
                or not question_number.strip().isdigit()
                or asset_kind not in {"question", "answer"}
            ):
                raise ValueError("asset override binding target is invalid")
            overrides.append(
                {
                    "sha256": digest,
                    "action": "bind",
                    "question_number": question_number.strip(),
                    "asset_kind": str(asset_kind),
                }
            )
        elif action == "ignore":
            if question_number is not None or asset_kind is not None:
                raise ValueError("ignored asset override cannot have a target")
            overrides.append(
                {
                    "sha256": digest,
                    "action": "ignore",
                    "question_number": None,
                    "asset_kind": None,
                }
            )
        else:
            raise ValueError("asset override action is invalid")
    return overrides


def _source_filename(
    value: object,
    *,
    source_path: Path,
    source_sha256: str,
) -> str:
    candidate = str(value or "").strip()
    candidate_path = Path(candidate)
    if (
        candidate
        and candidate_path.name == candidate
        and candidate_path.suffix.casefold() == source_path.suffix.casefold()
        and candidate_path.suffix.casefold() in {".docx", ".pdf"}
    ):
        return candidate

    stem = source_path.stem
    for digest_suffix in (source_sha256, source_sha256[:12]):
        marker = f"_{digest_suffix}"
        if stem.casefold().endswith(marker.casefold()):
            stem = stem[: -len(marker)]
            break
    return f"{stem or 'source-paper'}{source_path.suffix.casefold()}"
