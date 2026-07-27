from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from backend.config_workspace.publish import LoadedEditorConfig, load_editor_config
from path_manager import resolve_stored_file_path
from question_bank.database.schema import connect
from question_bank.services.question_write_service import QuestionBankWriteService
from question_bank.services.source_question_link_service import (
    SourceQuestionLinkService,
)

from .manager import JobCancellationRequested, JobContext
from .question_import import run_question_import_job
from .tagging_sync import run_tagging_sync_job


QuestionImportRunner = Callable[..., dict[str, object]]
TaggingSyncRunner = Callable[..., dict[str, object]]


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
) -> dict[str, object]:
    """Import an archived session paper, then run governed AI tagging.

    This deliberately runs after grading-config generation as an independent
    job.  Its state and retries cannot publish, replace, or invalidate a
    grading rubric.
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

    loaded, source_path = _load_current_inputs(
        grading_db,
        session_id=session_id,
        source_sha256=source_sha256,
        config_revision=config_revision,
        data_root=Path(data_root),
    )
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
            upload = write_service.stage_upload(
                filename=source_path.name,
                content=source_bytes,
            )
            request = write_service.create_import_request(upload_id=upload.upload_id)
            import_context = _ChildJobContext(
                parent=context,
                payload={"request_id": request.request_id},
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
        )

        if question_ids:
            tag_context = _ChildJobContext(
                parent=context,
                payload={"question_ids": question_ids},
                progress_start=0.48,
                progress_end=0.88,
                stage="question_bank_tagging",
            )
            tagging_result = tagging_sync_runner(
                context=tag_context,
                question_bank_db_path=Path(question_bank_db_path),
                ai_service_factory=ai_service_factory,
                taxonomy_governance=taxonomy_governance,
            )
        else:
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
        )
        candidates = _bank_questions(Path(question_bank_db_path), question_ids)
        source_questions = current.payload.get("rubric", {}).get("questions", [])
        if not isinstance(source_questions, list):
            source_questions = []
        link_result = SourceQuestionLinkService(
            Path(question_bank_db_path)
        ).confirm_imported_questions_for_session(
            grading_session_id=session_id,
            source_questions=[
                item for item in source_questions if isinstance(item, dict)
            ],
            imported_bank_questions=candidates,
        )

        result = _result(
            session_id=session_id,
            mode=mode,
            import_result=import_result,
            tagging_result=tagging_result,
            link_result=link_result,
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
                tagged_count=result["tagged_count"],
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
            raise StaleQuestionBankSyncError(
                "question-bank sync ownership changed"
            )
        context.report(1.0, "question_bank_sync", str(result["outcome"]))
        return result
    except StaleQuestionBankSyncError as exc:
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
                retryable=True,
            ),
            error="Question-bank import or tagging failed.",
        )
        raise RuntimeError("session question-bank sync failed") from exc


def _load_current_inputs(
    grading_db: Any,
    *,
    session_id: int,
    source_sha256: str,
    config_revision: str,
    data_root: Path,
) -> tuple[LoadedEditorConfig, Path]:
    loaded = load_editor_config(grading_db, session_id)
    if not loaded.configured:
        raise ValueError("grading configuration is not ready")
    if loaded.revision != config_revision:
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


def _bank_questions(db_path: Path, question_ids: list[int]) -> list[dict[str, Any]]:
    if not question_ids:
        return []
    placeholders = ",".join("?" for _ in question_ids)
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
    failed_count = max(
        len(failed_ids),
        int(import_result.get("failed_count") or 0),
        int(tagging_result.get("failed_count") or 0),
    )
    return {
        "session_id": session_id,
        "outcome": outcome,
        "imported_count": len(imported_ids),
        "tagged_count": max(
            len(successful_ids),
            int(tagging_result.get("tagged_count") or 0),
        ),
        "linked_count": int(link_result.get("confirmed") or 0),
        "failed_count": failed_count,
        "successful_question_ids": successful_ids,
        "failed_question_ids": failed_ids,
        "unresolved_question_ids": unresolved,
        "review_count": review_count,
        "proposal_ids": proposal_ids,
        "retryable": bool(
            failed_count
            and (
                import_result.get("retryable")
                or tagging_result.get("retryable")
            )
        ),
    }


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
