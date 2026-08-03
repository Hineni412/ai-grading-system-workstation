from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from backend.config_workspace.deferred_analysis import (
    DeferredAnalysisArtifact,
    DeferredAnalysisArtifactStore,
)
from backend.config_workspace.publish import LoadedEditorConfig, load_editor_config
from path_manager import resolve_stored_file_path
from question_bank.database.schema import connect
from question_bank.services.question_write_service import QuestionBankWriteService
from question_bank.services.question_service import QuestionService
from question_bank.services.source_question_link_service import (
    SourceQuestionLinkService,
)

from .manager import JobCancellationRequested, JobContext
from .question_import import run_question_import_job
from .tagging_sync import run_tagging_sync_job
from question_bank.taxonomy.curriculum_catalog import curriculum_volume
from question_bank.solution_evidence import (
    FineTermCoreMappingRepository,
    SolutionEvidenceRepository,
    build_fine_term_mapping_baseline,
    install_fine_term_mapping_baseline,
)
from question_bank.training_criteria import (
    ConfirmedQuestionAdoptionLink,
    DeferredCombinedProjectionWriter,
    ExistingTagProjectionWriter,
    QuestionAnalysisInputLoader,
)


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
    analysis_artifact_root: Path | None = None,
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
        )

        if question_ids and deferred_artifact is None:
            tag_context = _ChildJobContext(
                parent=context,
                payload={
                    "question_ids": question_ids,
                    "curriculum_volume_id": str(volume["id"]),
                },
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
        )
        candidates = _bank_questions(Path(question_bank_db_path), question_ids)
        source_questions = current.payload.get("rubric", {}).get("questions", [])
        if not isinstance(source_questions, list):
            source_questions = []
        link_result = link_service.confirm_imported_questions_for_session(
            grading_session_id=session_id,
            source_questions=[
                item for item in source_questions if isinstance(item, dict)
            ],
            imported_bank_questions=candidates,
            sync_job_id=context.job_id,
            sync_config_revision=config_revision,
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
                cancel_check=context.raise_if_cancelled,
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
                evidence_count=result["evidence_count"],
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
        "tagged_count": max(
            len(successful_ids),
            int(tagging_result.get("tagged_count") or 0),
        ),
        "evidence_count": max(
            0,
            int(tagging_result.get("evidence_count") or 0),
        ),
        "linked_count": int(link_result.get("confirmed") or 0),
        "failed_count": failed_count,
        "successful_question_ids": successful_ids,
        "failed_question_ids": failed_ids,
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
    provisional = loader.load(
        bank_ids,
        curriculum_volume_id=artifact.curriculum_volume_id,
    )
    contracts = ai_service.taxonomy_contracts(
        {item.question_id: item.tagging_context for item in provisional}
    )
    questions = {
        item.question_id: item
        for item in loader.load(
            bank_ids,
            taxonomy_contracts=contracts,
            curriculum_volume_id=artifact.curriculum_volume_id,
        )
    }
    mapping_repository = FineTermCoreMappingRepository(question_bank_db_path)
    baseline_result = install_fine_term_mapping_baseline(
        mapping_repository,
        build_fine_term_mapping_baseline(),
        actor_ref="system:taxonomy-baseline-v1",
    )
    tag_writer = ExistingTagProjectionWriter(
        question_service=QuestionService(question_bank_db_path),
        tagging_service=ai_service,
    )
    writer = DeferredCombinedProjectionWriter(
        tag_writer=tag_writer,
        mapping_repository=mapping_repository,
        evidence_repository=SolutionEvidenceRepository(question_bank_db_path),
        taxonomy_governance=taxonomy_governance,
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
        question = questions.get(bank_question_id)
        if question is None:
            missing_links += 1
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
            )
        )
    successful_ids = [
        int(item["question_id"])
        for item in adoption_results
        if item.get("tag_status")
        in {"succeeded", "needs_taxonomy_review"}
        and item.get("evidence_status")
        in {"succeeded", "needs_taxonomy_review"}
    ]
    failed_ids = [
        int(item["question_id"])
        for item in adoption_results
        if item.get("tag_status")
        not in {"succeeded", "needs_taxonomy_review"}
        or item.get("evidence_status")
        not in {"succeeded", "needs_taxonomy_review"}
    ]
    tagged_count = sum(
        item.get("tag_status") == "succeeded" for item in adoption_results
    )
    evidence_count = sum(
        item.get("evidence_status") == "succeeded" for item in adoption_results
    )
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
        "evidence_count": evidence_count,
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
    question_import_runner: QuestionImportRunner = run_question_import_job,
) -> dict[str, object]:
    """Import and adopt completed analysis before score publication, without grading links."""

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
    candidates = _bank_questions(Path(question_bank_db_path), question_ids)
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
