from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from question_bank.database.schema import connect
from question_bank.current_knowledge import CurrentFineTermResolver
from question_bank.models.tag_schema import TaggingContext
from question_bank.relations.evidence_governance import (
    EvidenceRelationGovernanceService,
)
from question_bank.services.ai_tagging_service import (
    AITaggingResult,
    AITaggingService,
    classify_tagging_error,
    is_auto_saveable_result,
)
from question_bank.services.question_service import (
    CORE_ANALYSIS_TAG_TYPES,
    QuestionService,
)
from question_bank.solution_evidence import (
    SolutionEvidenceProjectionWriter,
    SolutionEvidenceRepository,
)
from question_bank.training_criteria import (
    CombinedAnalysisRepository,
    CombinedQuestionAnalysisModule,
    ExistingTagProjectionWriter,
    OpenAICombinedAnalysisGateway,
    QuestionAnalysisInputLoader,
    TrainingCriterionModule,
    solution_evidence_source_content_hash,
    training_criteria_from_solution_evidence,
    training_criterion_source_reference,
)

from .execution_locks import keyed_execution_locks
from .manager import JobContext


TaggingFactory = Callable[[], AITaggingService]
_RETRYABLE_CATEGORIES = {
    "rate_limit",
    "timeout",
    "network",
    "parse",
    "quality",
    "save",
    "evidence",
    "unknown",
}
_PUBLIC_FAILURE_MESSAGES = {
    "rate_limit": "AI service rate limited the request.",
    "timeout": "AI tagging request timed out.",
    "network": "AI tagging service was unavailable.",
    "parse": "AI tagging response could not be parsed.",
    "validation": "Question is unavailable for tagging.",
    "quality": "AI tagging result did not meet the save quality gate.",
    "save": "Complete AI tags could not be saved.",
    "evidence": "Solution evidence could not be saved.",
    "training_criteria": "Training points could not be published.",
    "unknown": "AI tagging failed.",
}


def run_tagging_sync_job(
    *,
    context: JobContext,
    question_bank_db_path: Path,
    ai_service_factory: TaggingFactory,
    taxonomy_governance: Any | None = None,
    batch_size: int = 20,
    data_root: Path | None = None,
) -> dict[str, object]:
    question_ids = _normalize_question_ids(context.payload.get("question_ids"))
    retry_evidence_question_ids = _normalize_retry_evidence_question_ids(
        context.payload.get("retry_evidence_question_ids"),
        requested_ids=question_ids,
    )
    retry_relation_question_ids = _normalize_retry_evidence_question_ids(
        context.payload.get("retry_relation_question_ids"),
        requested_ids=question_ids,
    )
    force_retag_question_ids = _normalize_retry_evidence_question_ids(
        context.payload.get("force_retag_question_ids"),
        requested_ids=question_ids,
    )
    db_path = Path(question_bank_db_path)
    lock_keys = [
        f"tagging-sync:{db_path.resolve(strict=False)}:{question_id}"
        for question_id in question_ids
    ]
    with keyed_execution_locks(
        lock_keys,
        cancel_check=context.raise_if_cancelled,
    ):
        return _run_tagging_sync_job_locked(
            context=context,
            question_bank_db_path=db_path,
            ai_service_factory=ai_service_factory,
            taxonomy_governance=taxonomy_governance,
            batch_size=batch_size,
            data_root=(None if data_root is None else Path(data_root)),
            question_ids=question_ids,
            retry_evidence_question_ids=retry_evidence_question_ids,
            retry_relation_question_ids=retry_relation_question_ids,
            force_retag_question_ids=force_retag_question_ids,
            curriculum_volume_id=str(
                context.payload.get("curriculum_volume_id") or ""
            ).strip(),
        )


def _run_tagging_sync_job_locked(
    *,
    context: JobContext,
    question_bank_db_path: Path,
    ai_service_factory: TaggingFactory,
    taxonomy_governance: Any | None,
    batch_size: int,
    data_root: Path | None,
    question_ids: list[int],
    retry_evidence_question_ids: list[int],
    retry_relation_question_ids: list[int],
    force_retag_question_ids: list[int],
    curriculum_volume_id: str,
) -> dict[str, object]:
    size = max(1, min(int(batch_size), 50))
    db_path = Path(question_bank_db_path)
    context.raise_if_cancelled()
    if (
        retry_relation_question_ids
        and set(retry_relation_question_ids) == set(question_ids)
        and not retry_evidence_question_ids
    ):
        return _replay_relation_governance(
            context=context,
            db_path=db_path,
            question_ids=retry_relation_question_ids,
        )
    try:
        analysis_gaps = _load_analysis_gaps(db_path, question_ids)
        analysis_retry_ids = [
            question_id
            for question_id in question_ids
            if not (
                analysis_gaps.get(question_id, {}).get("evidence_ready")
                and analysis_gaps.get(question_id, {}).get("criteria_ready")
            )
        ]
        contexts, complete_ids, unavailable_ids = _load_tagging_candidates(
            db_path,
            question_ids,
            curriculum_volume_id=curriculum_volume_id,
            force_question_ids=set(retry_evidence_question_ids)
            | set(analysis_retry_ids)
            | set(force_retag_question_ids),
        )
    except Exception:
        raise RuntimeError("tagging sync setup failed") from None
    failures = [
        _failure(question_id, "validation") for question_id in unavailable_ids
    ]
    successful_ids = list(complete_ids)
    tagged_count = 0
    pending_ids = [item for item in question_ids if item in contexts]
    complete_set = set(complete_ids)
    evidence_retry_set = set(retry_evidence_question_ids)
    evidence_retry_set.update(analysis_retry_ids)
    evidence_only_ids = [
        item
        for item in pending_ids
        if item in evidence_retry_set and item in complete_set
    ]
    normal_pending_ids = [
        item for item in pending_ids if item not in set(evidence_only_ids)
    ]
    total_batches = max(1, (len(pending_ids) + size - 1) // size)
    try:
        ai_service = ai_service_factory() if pending_ids else None
    except Exception:
        raise RuntimeError("tagging sync setup failed") from None
    service = QuestionService(db_path)
    governance = taxonomy_governance
    taxonomy_revision = 0
    knowledge_graph_release_id = ""
    taxonomy_contracts: dict[int, dict[str, Any]] = {}
    if ai_service is not None:
        if governance is None and isinstance(ai_service, AITaggingService):
            governance = ai_service.taxonomy_governance
        taxonomy_contracts, taxonomy_revision = _plan_taxonomy(
            ai_service,
            governance,
            contexts=contexts,
        )
        knowledge_graph_release_id = _planned_graph_release_id(
            taxonomy_contracts
        )
    if (
        pending_ids
        and data_root is not None
        and isinstance(ai_service, AITaggingService)
    ):
        return _run_unified_tagging_analysis(
            context=context,
            db_path=db_path,
            data_root=data_root,
            ai_service=ai_service,
            pending_ids=pending_ids,
            evidence_only_ids=evidence_only_ids,
            complete_ids=complete_ids,
            unavailable_ids=unavailable_ids,
            taxonomy_contracts=taxonomy_contracts,
            taxonomy_revision=taxonomy_revision,
            knowledge_graph_release_id=knowledge_graph_release_id,
            requested_ids=question_ids,
            retry_evidence_question_ids=retry_evidence_question_ids,
            retry_relation_question_ids=retry_relation_question_ids,
            taxonomy_governance=governance,
            analysis_gaps=analysis_gaps,
        )
    if evidence_only_ids:
        # Evidence-only retry is available only through the combined-v3 path.
        # Fail closed here so an already-successful tag projection is never
        # rewritten by the legacy tag-only implementation.
        failures.extend(_failure(question_id, "evidence") for question_id in evidence_only_ids)
        pending_ids = normal_pending_ids
        total_batches = max(1, (len(pending_ids) + size - 1) // size)
    proposal_ids: list[str] = []
    proposal_keys: set[str] = set()
    review_question_ids: list[int] = []
    retrieval_miss_count = 0
    retrieval_miss_question_ids: list[int] = []
    generation_id = f"tagging-sync:{context.job_id}:legacy"
    observation_sequences = _allocate_observation_sequences(
        governance,
        generation_id=generation_id,
        question_ids=normal_pending_ids,
    )

    context.report(0.05, "tagging_sync", "loading")
    for batch_index, start in enumerate(range(0, len(pending_ids), size)):
        context.raise_if_cancelled()
        batch_ids = pending_ids[start : start + size]
        context.report(
            0.1 + (0.75 * batch_index / total_batches),
            "tagging_sync",
            f"batch {batch_index + 1}/{total_batches}",
        )
        try:
            assert ai_service is not None
            results = ai_service.analyze_questions(
                {question_id: contexts[question_id] for question_id in batch_ids},
                taxonomy_contracts={
                    question_id: taxonomy_contracts[question_id]
                    for question_id in batch_ids
                    if question_id in taxonomy_contracts
                },
                progress_callback=None,
                request_callback=None,
                allow_batch_fallback=False,
                quality_retry_limit=1,
                enable_review=False,
            )
        except Exception as exc:  # noqa: BLE001
            context.raise_if_cancelled()
            category = classify_tagging_error(exc)
            failures.extend(_failure(question_id, category) for question_id in batch_ids)
            continue
        context.raise_if_cancelled()
        for question_id in batch_ids:
            context.raise_if_cancelled()
            result = results.get(question_id)
            if result is None:
                failures.append(_failure(question_id, "validation"))
                continue
            retrieval_misses = getattr(result, "retrieval_misses", [])
            if isinstance(retrieval_misses, list) and retrieval_misses:
                retrieval_miss_count += len(retrieval_misses)
                if question_id not in retrieval_miss_question_ids:
                    retrieval_miss_question_ids.append(question_id)
            if not is_auto_saveable_result(result):
                failures.append(_result_failure(question_id, result))
                continue
            persisted_proposals: list[dict[str, Any]] = []
            if result.proposals or result.quality_status == "needs_review":
                if governance is None:
                    failures.append(_failure(question_id, "save"))
                    continue
                try:
                    assert result.analysis is not None
                    persisted_proposals = _persist_proposals(
                        governance,
                        result,
                        question_id=question_id,
                        job_id=context.job_id,
                        expected_revision=int(result.taxonomy_revision or 0),
                        knowledge_graph_release_id=knowledge_graph_release_id,
                    )
                except Exception:  # noqa: BLE001
                    failures.append(_failure(question_id, "save"))
                    continue
                for item in persisted_proposals:
                    proposal_key = _proposal_key(item)
                    if proposal_key in proposal_keys:
                        continue
                    proposal_keys.add(proposal_key)
                    proposal_id = _proposal_id(item)
                    if proposal_id:
                        proposal_ids.append(proposal_id)
                if question_id not in review_question_ids:
                    review_question_ids.append(question_id)
            try:
                assert result.analysis is not None
                saved = service.save_tag_analysis(
                    question_id,
                    result.analysis,
                    model_name=result.model_name,
                    confidence=result.analysis.confidence,
                    taxonomy_governance=governance,
                )
            except Exception:  # noqa: BLE001
                saved = False
            if not saved:
                failures.append(_failure(question_id, "save"))
                continue
            try:
                _record_successful_observation(
                    governance,
                    question_id=question_id,
                    generation_id=generation_id,
                    proposal_ids=[
                        proposal_id
                        for item in persisted_proposals
                        if (proposal_id := _proposal_id(item))
                    ],
                    taxonomy_revision=int(result.taxonomy_revision or 0),
                    graph_release_id=knowledge_graph_release_id,
                    allocated=observation_sequences,
                )
            except Exception:  # noqa: BLE001
                failures.append(_failure(question_id, "save"))
                continue
            tagged_count += 1
            successful_ids.append(question_id)

    failed_ids = [int(item["question_id"]) for item in failures]
    successful_ids = [item for item in question_ids if item in set(successful_ids)]
    evidence_succeeded_ids = [
        question_id
        for question_id in successful_ids
        if analysis_gaps.get(question_id, {}).get("evidence_ready")
    ]
    criteria_succeeded_ids = [
        question_id
        for question_id in successful_ids
        if analysis_gaps.get(question_id, {}).get("criteria_ready")
    ]
    criteria_failed_ids = [
        question_id
        for question_id in successful_ids
        if not analysis_gaps.get(question_id, {}).get("criteria_ready")
    ]
    if failures:
        outcome = "partial" if successful_ids else "failed"
    else:
        outcome = "complete"
    context.report(1.0, "tagging_sync", outcome)
    return {
        "outcome": outcome,
        "requested_count": len(question_ids),
        "skipped_complete_count": len(complete_ids),
        "tagged_count": tagged_count,
        "failed_count": len(failed_ids),
        "successful_question_ids": successful_ids,
        "failed_question_ids": failed_ids,
        "complete_tagged_count": len(successful_ids),
        "failures": failures,
        "taxonomy_revision": taxonomy_revision,
        "knowledge_graph_release_id": knowledge_graph_release_id,
        "retrieval_miss_count": retrieval_miss_count,
        "retrieval_miss_question_ids": retrieval_miss_question_ids,
        "review_count": len(proposal_keys),
        "review_question_ids": review_question_ids,
        "proposal_ids": proposal_ids,
        "evidence_succeeded_question_ids": evidence_succeeded_ids,
        "evidence_failed_question_ids": list(evidence_only_ids),
        "evidence_count": len(evidence_succeeded_ids),
        "criteria_succeeded_question_ids": criteria_succeeded_ids,
        "criteria_failed_question_ids": criteria_failed_ids,
        "criteria_count": len(criteria_succeeded_ids),
        "analysis_contract": "legacy-tag-only",
        "retryable": any(
            str(item["category"]) in _RETRYABLE_CATEGORIES for item in failures
        ),
    }


class _CancellationAwareCombinedGateway:
    def __init__(self, gateway: Any, context: JobContext) -> None:
        self.gateway = gateway
        self.context = context

    @property
    def max_parallel_requests(self) -> int:
        value = getattr(self.gateway, "max_parallel_requests", 1)
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 1

    def analyze(self, *args: Any, **kwargs: Any) -> Any:
        self.context.raise_if_cancelled()
        response = self.gateway.analyze(*args, **kwargs)
        self.context.raise_if_cancelled()
        return response


def _run_unified_tagging_analysis(
    *,
    context: JobContext,
    db_path: Path,
    data_root: Path,
    ai_service: AITaggingService,
    pending_ids: list[int],
    evidence_only_ids: list[int],
    complete_ids: list[int],
    unavailable_ids: list[int],
    taxonomy_contracts: Mapping[int, Mapping[str, Any]],
    taxonomy_revision: int,
    knowledge_graph_release_id: str,
    requested_ids: list[int],
    retry_evidence_question_ids: list[int],
    retry_relation_question_ids: list[int],
    taxonomy_governance: Any | None,
    analysis_gaps: Mapping[int, Mapping[str, bool]],
) -> dict[str, object]:
    """Run the production tagging entry through combined-v3 once per batch."""

    context.raise_if_cancelled()
    loader = QuestionAnalysisInputLoader(db_path=db_path, data_root=data_root)
    loaded_by_id: dict[int, Any] = {}
    input_failures: list[dict[str, object]] = [
        _failure(question_id, "validation") for question_id in unavailable_ids
    ]
    for question_id in pending_ids:
        try:
            loaded_by_id[question_id] = loader.load(
                (question_id,),
                taxonomy_contracts={
                    question_id: taxonomy_contracts.get(question_id, {})
                },
            )[0]
        except (KeyError, OSError, TypeError, ValueError):
            input_failures.append(_failure(question_id, "validation"))
    if not loaded_by_id:
        failed_ids = [int(item["question_id"]) for item in input_failures]
        outcome = "partial" if complete_ids else "failed"
        context.report(1.0, "tagging_sync", outcome)
        return {
            "outcome": outcome,
            "requested_count": len(requested_ids),
            "skipped_complete_count": len(complete_ids),
            "tagged_count": 0,
            "failed_count": len(failed_ids),
            "successful_question_ids": list(complete_ids),
            "failed_question_ids": failed_ids,
            "complete_tagged_count": len(complete_ids),
            "failures": input_failures,
            "taxonomy_revision": taxonomy_revision,
            "knowledge_graph_release_id": knowledge_graph_release_id,
            "retrieval_miss_count": 0,
            "retrieval_miss_question_ids": [],
            "review_count": 0,
            "review_question_ids": [],
            "proposal_ids": [],
            "evidence_count": 0,
            "evidence_succeeded_question_ids": [],
            "evidence_failed_question_ids": failed_ids,
            "criteria_count": 0,
            "criteria_succeeded_question_ids": [],
            "criteria_failed_question_ids": failed_ids,
            "retryable": False,
        }
    context.report(0.1, "tagging_sync", "combined-v3")
    protocol_adapter = ai_service._protocol_adapter()
    gateway = _CancellationAwareCombinedGateway(
        OpenAICombinedAnalysisGateway(
            protocol_adapter=protocol_adapter,
            model_name=ai_service.model,
        ),
        context,
    )
    mapping_repository = CurrentFineTermResolver.from_active_database(db_path)
    tag_writer = ExistingTagProjectionWriter(
        question_service=QuestionService(db_path),
        tagging_service=ai_service,
    )
    evidence_writer = SolutionEvidenceProjectionWriter(
        mapping_repository=mapping_repository,
        evidence_repository=SolutionEvidenceRepository(db_path),
        taxonomy_governance=(
            taxonomy_governance or ai_service.taxonomy_governance
        ),
    )
    criterion_module = TrainingCriterionModule(db_path)
    evidence_repository = SolutionEvidenceRepository(db_path)
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(db_path),
        gateway=gateway,
        tag_writer=tag_writer,
        evidence_writer=evidence_writer,
        criterion_module=criterion_module,
    )
    criterion_audits: list[Mapping[str, Any]] = []
    local_criterion_only_ids: set[int] = set()
    locally_repaired_criterion_ids: set[int] = set()
    for question in loaded_by_id.values():
        gap = analysis_gaps.get(question.question_id, {})
        if not gap.get("evidence_ready") or gap.get("criteria_ready"):
            continue
        audit = _publish_saved_criterion(
            question=question,
            evidence_repository=evidence_repository,
            criterion_module=criterion_module,
        )
        criterion_audits.append(
            {
                "items": [
                    {
                        "question_id": question.question_id,
                        **audit,
                    }
                ]
            }
        )
        if audit.get("status") in {"succeeded", "needs_review"}:
            locally_repaired_criterion_ids.add(question.question_id)
        elif audit.get("status") == "failed" and question.question_id in set(
            complete_ids
        ):
            # The saved evidence is still usable, but a complete tag set must
            # not be sent through the model again just to retry publication.
            local_criterion_only_ids.add(question.question_id)

    explicit_evidence_retry_set = set(retry_evidence_question_ids)
    evidence_only_set = {
        question_id
        for question_id in evidence_only_ids
        if question_id not in locally_repaired_criterion_ids
        and question_id not in local_criterion_only_ids
    }
    tag_only_set = {
        question_id
        for question_id in pending_ids
        if question_id not in evidence_only_set
        and question_id not in set(complete_ids)
        and question_id not in explicit_evidence_retry_set
        and bool(analysis_gaps.get(question_id, {}).get("evidence_ready"))
    }
    # An explicit evidence retry is always projection-scoped, even if its tag
    # projection is incomplete.  Automatic gap repair keeps new questions on
    # the combined path until the first evidence version exists.
    evidence_only_set.update(
        question_id
        for question_id in pending_ids
        if question_id in explicit_evidence_retry_set
        and question_id not in locally_repaired_criterion_ids
        and question_id not in local_criterion_only_ids
    )
    regular_loaded = tuple(
        loaded_by_id[question_id]
        for question_id in pending_ids
        if (
            question_id in loaded_by_id
            and question_id not in evidence_only_set
            and question_id not in tag_only_set
            and question_id not in local_criterion_only_ids
            and question_id not in locally_repaired_criterion_ids
        )
    )
    tag_only_loaded = tuple(
        loaded_by_id[question_id]
        for question_id in pending_ids
        if question_id in loaded_by_id and question_id in tag_only_set
    )
    evidence_loaded = tuple(
        loaded_by_id[question_id]
        for question_id in pending_ids
        if question_id in loaded_by_id and question_id in evidence_only_set
    )
    summaries: list[Mapping[str, Any]] = []
    audit_rows: list[Mapping[str, Any]] = []
    analysis_total = sum(
        len(group)
        for group in (regular_loaded, tag_only_loaded, evidence_loaded)
    )
    analysis_completed = 0

    def analysis_progress(group_size: int):
        group_start = analysis_completed

        def report(update: Mapping[str, Any]) -> None:
            processed = min(
                group_size,
                max(0, int(update.get("processed_questions") or 0)),
            )
            overall = min(analysis_total, group_start + processed)
            fraction = overall / max(analysis_total, 1)
            context.report(
                0.1 + 0.72 * fraction,
                "tagging_sync",
                f"AI 分析已处理 {overall}/{analysis_total} 道题",
            )

        return report

    if regular_loaded:
        regular_operation_id = f"tagging-sync:{context.job_id}"
        observation_sequences = _allocate_observation_sequences(
            taxonomy_governance or ai_service.taxonomy_governance,
            generation_id=regular_operation_id,
            question_ids=[item.question_id for item in regular_loaded],
        )
        summaries.append(
            module.analyze(
                operation_id=regular_operation_id,
                questions=regular_loaded,
                projection="both",
                progress_callback=analysis_progress(len(regular_loaded)),
            )
        )
        analysis_completed += len(regular_loaded)
        regular_ids = [item.question_id for item in regular_loaded]
        audit_rows.extend(
            (
                tag_writer.audit_summary(regular_operation_id, regular_ids),
                evidence_writer.audit_summary(
                    regular_operation_id,
                    regular_ids,
                ),
            )
        )
        criterion_audits.append(
            _criterion_audit_summary(module, regular_operation_id, regular_ids)
        )
    if tag_only_loaded:
        tag_only_operation_id = f"tagging-sync:{context.job_id}:tag"
        summaries.append(
            module.analyze(
                operation_id=tag_only_operation_id,
                questions=tag_only_loaded,
                projection="tag",
                progress_callback=analysis_progress(len(tag_only_loaded)),
            )
        )
        analysis_completed += len(tag_only_loaded)
        tag_only_ids = [item.question_id for item in tag_only_loaded]
        audit_rows.append(tag_writer.audit_summary(tag_only_operation_id, tag_only_ids))
    if evidence_loaded:
        evidence_operation_id = f"tagging-sync:{context.job_id}:evidence"
        summaries.append(
            module.analyze(
                operation_id=evidence_operation_id,
                questions=evidence_loaded,
                projection="training_criteria",
                progress_callback=analysis_progress(len(evidence_loaded)),
            )
        )
        analysis_completed += len(evidence_loaded)
        audit_rows.append(
            evidence_writer.audit_summary(
                evidence_operation_id,
                [item.question_id for item in evidence_loaded],
            )
        )
        criterion_audits.append(
            _criterion_audit_summary(
                module,
                evidence_operation_id,
                [item.question_id for item in evidence_loaded],
            )
        )
    context.raise_if_cancelled()
    items: dict[int, Mapping[str, Any]] = {}
    for summary in summaries:
        for item in summary.get("items", []):
            if isinstance(item, Mapping):
                items[int(item["question_id"])] = item
    for question_id in local_criterion_only_ids | locally_repaired_criterion_ids:
        items.setdefault(
            question_id,
            {
                "question_id": question_id,
                "tag_status": "not_requested",
                "tag_error_category": "",
                # The stored evidence is already valid; only publishing its
                # score-free training version failed.
                "criteria_status": "succeeded",
                "criteria_error_category": "",
            },
        )
    failures = list(input_failures)
    tag_success = list(complete_ids)
    newly_tagged: list[int] = []
    evidence_success: list[int] = []
    evidence_failed: list[int] = []
    criterion_success: list[int] = []
    criterion_failed: list[int] = []
    criterion_review: list[int] = []
    criterion_audit_by_id = {
        int(item["question_id"]): item
        for audit in criterion_audits
        for item in audit.get("items", [])
        if isinstance(item, Mapping) and int(item.get("question_id") or 0) > 0
    }
    for question_id in pending_ids:
        item = items.get(question_id)
        if item is None:
            if not any(int(entry["question_id"]) == question_id for entry in failures):
                failures.append(_failure(question_id, "validation"))
            evidence_failed.append(question_id)
            continue
        if (
            question_id not in evidence_only_set
            and question_id not in local_criterion_only_ids
            and question_id not in locally_repaired_criterion_ids
        ):
            if str(item.get("tag_status")) == "succeeded":
                question_audit = tag_writer.audit_summary(
                    f"tagging-sync:{context.job_id}", [question_id]
                )
                try:
                    _record_successful_observation(
                        taxonomy_governance or ai_service.taxonomy_governance,
                        question_id=question_id,
                        generation_id=f"tagging-sync:{context.job_id}",
                        proposal_ids=[
                            proposal_id
                            for proposal in question_audit.get("proposals", [])
                            if isinstance(proposal, Mapping)
                            and (proposal_id := _proposal_id(proposal))
                        ],
                        taxonomy_revision=taxonomy_revision,
                        graph_release_id=knowledge_graph_release_id,
                        allocated=locals().get("observation_sequences", {}),
                    )
                except Exception:  # noqa: BLE001
                    failures.append(_failure(question_id, "save"))
                    continue
                tag_success.append(question_id)
                newly_tagged.append(question_id)
            else:
                category = _combined_public_category(
                    str(item.get("tag_error_category") or "")
                )
                failures.append(_failure(question_id, category))
        if question_id not in tag_only_set:
            if str(item.get("criteria_status")) == "succeeded":
                evidence_success.append(question_id)
            else:
                evidence_failed.append(question_id)
                if not any(
                    int(entry["question_id"]) == question_id
                    for entry in failures
                ):
                    failures.append(_failure(question_id, "evidence"))
        criterion_status = str(
            criterion_audit_by_id.get(question_id, {}).get("status")
            or "not_requested"
        )
        if criterion_status == "succeeded":
            criterion_success.append(question_id)
        elif criterion_status == "needs_review":
            criterion_review.append(question_id)
        elif criterion_status == "failed":
            criterion_failed.append(question_id)
            if not any(
                int(entry["question_id"]) == question_id
                and str(entry.get("category") or "") == "training_criteria"
                for entry in failures
            ):
                failures.append(_failure(question_id, "training_criteria"))
        elif (
            question_id in tag_only_set
            and not analysis_gaps.get(question_id, {}).get("criteria_ready")
            and question_id not in locally_repaired_criterion_ids
        ):
            criterion_failed.append(question_id)
            if not any(
                int(entry["question_id"]) == question_id
                and str(entry.get("category") or "") == "training_criteria"
                for entry in failures
            ):
                failures.append(_failure(question_id, "training_criteria"))
    successful_ids = [item for item in requested_ids if item in set(tag_success)]
    failed_set = {
        int(item["question_id"]) for item in failures
    } | set(evidence_failed)
    failed_ids = [item for item in requested_ids if item in failed_set]
    audits = _merge_governance_audits(*audit_rows)
    if retry_relation_question_ids:
        audits["relation_hints"].extend(
            SolutionEvidenceRepository(db_path).relation_hints(
                retry_relation_question_ids,
                operation_id=f"tagging-sync:{context.job_id}:relation-replay",
            )
        )
    context.report(0.9, "tagging_sync", "正在整理知识关系与最终状态")
    try:
        relation_governance = EvidenceRelationGovernanceService(db_path).govern(
            audits["relation_hints"],
            operation_id=f"tagging-sync:{context.job_id}",
        )
        relation_governance["status"] = "complete"
    except Exception as exc:
        # Relation governance is a recoverable secondary projection. A graph
        # write failure must never discard otherwise valid question tags or
        # solution evidence.
        relation_governance = {
            "status": "failed",
            "candidate_count": len(audits["relation_hints"]),
            "auto_confirmed_count": 0,
            "exception_count": 0,
            "reused_count": 0,
            "failed_count": len(audits["relation_hints"]),
            "failed_question_ids": sorted({
                int(item.get("question_id") or 0)
                for item in audits["relation_hints"]
                if int(item.get("question_id") or 0) > 0
            }),
            "error_type": type(exc).__name__,
        }
    relation_failed_ids = [
        int(item)
        for item in relation_governance.get("failed_question_ids", [])
    ]
    if (
        failures
        or evidence_failed
        or criterion_failed
        or criterion_review
        or relation_failed_ids
    ):
        outcome = "partial" if successful_ids or evidence_success else "failed"
    else:
        outcome = "complete"
    context.report(1.0, "tagging_sync", outcome)
    return {
        "outcome": outcome,
        "requested_count": len(requested_ids),
        "skipped_complete_count": len(complete_ids),
        "tagged_count": len(newly_tagged),
        "failed_count": len(failed_ids),
        "successful_question_ids": successful_ids,
        "failed_question_ids": failed_ids,
        "complete_tagged_count": len(successful_ids),
        "failures": failures,
        "taxonomy_revision": taxonomy_revision,
        "knowledge_graph_release_id": knowledge_graph_release_id,
        "retrieval_miss_count": len(audits["retrieval_misses"]),
        "retrieval_miss_question_ids": audits["retrieval_miss_question_ids"],
        "review_count": len(audits["proposals"]),
        "review_question_ids": audits["proposal_question_ids"],
        "proposal_ids": [
            proposal_id
            for item in audits["proposals"]
            if (proposal_id := _proposal_id(item))
        ],
        "evidence_secondary_match_count": len(audits["secondary_matches"]),
        "relation_governance": relation_governance,
        "relation_governance_failed_question_ids": relation_failed_ids,
        "evidence_succeeded_question_ids": evidence_success,
        "evidence_failed_question_ids": evidence_failed,
        "evidence_count": len(evidence_success),
        "criteria_succeeded_question_ids": criterion_success,
        "criteria_failed_question_ids": criterion_failed,
        "criteria_needs_review_question_ids": criterion_review,
        "criteria_count": len(criterion_success),
        "criteria_needs_review_count": len(criterion_review),
        "analysis_contract": "combined-v3",
        "retryable": bool(
            failures or evidence_failed or criterion_failed or relation_failed_ids
        ),
    }


def _replay_relation_governance(
    *,
    context: JobContext,
    db_path: Path,
    question_ids: list[int],
) -> dict[str, object]:
    """Retry the local graph projection from saved evidence without calling AI."""

    context.report(0.1, "tagging_sync", "relation_replay")
    hints = SolutionEvidenceRepository(db_path).relation_hints(
        question_ids,
        operation_id=f"tagging-sync:{context.job_id}:relation-replay",
    )
    try:
        governance = EvidenceRelationGovernanceService(db_path).govern(
            hints,
            operation_id=f"tagging-sync:{context.job_id}:relation-replay",
        )
        governance["status"] = "complete"
    except Exception as exc:
        governance = {
            "status": "failed",
            "candidate_count": len(hints),
            "auto_confirmed_count": 0,
            "exception_count": 0,
            "reused_count": 0,
            "failed_count": len(hints),
            "failed_question_ids": list(question_ids),
            "error_type": type(exc).__name__,
        }
    failed_ids = [
        int(item) for item in governance.get("failed_question_ids", [])
    ]
    outcome = "partial" if failed_ids else "complete"
    context.report(1.0, "tagging_sync", outcome)
    return {
        "outcome": outcome,
        "requested_count": len(question_ids),
        "skipped_complete_count": len(question_ids),
        "tagged_count": 0,
        "failed_count": len(failed_ids),
        "successful_question_ids": [
            item for item in question_ids if item not in set(failed_ids)
        ],
        "failed_question_ids": failed_ids,
        "failures": [],
        "taxonomy_revision": 0,
        "retrieval_miss_count": 0,
        "retrieval_miss_question_ids": [],
        "review_count": 0,
        "review_question_ids": [],
        "proposal_ids": [],
        "evidence_succeeded_question_ids": [],
        "evidence_failed_question_ids": [],
        "relation_governance": governance,
        "relation_governance_failed_question_ids": failed_ids,
        "analysis_contract": "combined-v3-relation-replay",
        "retryable": bool(failed_ids),
    }


def _combined_public_category(category: str) -> str:
    normalized = str(category or "").strip().casefold()
    if normalized in {"tag_validation", "validation"}:
        return "quality"
    if normalized == "missing_image":
        return "validation"
    if normalized in {"training_criteria", "criterionqualityerror"}:
        return "training_criteria"
    if normalized in {"timeout", "parse"}:
        return normalized
    return "unknown"


def _criterion_audit_summary(
    module: Any,
    operation_id: str,
    question_ids: Sequence[int],
) -> dict[str, Any]:
    """Keep the job seam compatible with older test/extension modules."""

    method = getattr(module, "criterion_audit_summary", None)
    if not callable(method):
        return {
            "items": [
                {"question_id": int(question_id), "status": "not_requested"}
                for question_id in question_ids
            ]
        }
    result = method(operation_id, question_ids)
    return dict(result) if isinstance(result, Mapping) else {"items": []}


def _publish_saved_criterion(
    *,
    question: Any,
    evidence_repository: SolutionEvidenceRepository,
    criterion_module: TrainingCriterionModule,
) -> dict[str, Any]:
    """Publish training points from saved evidence without another AI call."""

    try:
        source_hash = solution_evidence_source_content_hash(question)
        resolver = CurrentFineTermResolver.from_active_database(
            evidence_repository.db_path
        )
        evidence = evidence_repository.load_current(
            question.question_id,
            source_content_hash=source_hash,
            resolver=resolver,
        )
        if evidence is None:
            return {"status": "not_available"}
        draft = training_criteria_from_solution_evidence(
            evidence,
            question=question,
        )
        workspace = criterion_module.propose(
            question=question,
            draft=draft,
            source_kind="combined_model",
            source_reference=training_criterion_source_reference(
                question.question_id,
                draft,
            ),
            actor_ref="model:stored-analysis",
            reason="从已保存解题证据发布训练判定点",
        )
        current = workspace.get("current_version") if isinstance(workspace, Mapping) else None
        quality_status = (
            str(current.get("quality_status") or "")
            if isinstance(current, Mapping)
            else ""
        )
        return {
            "status": (
                "succeeded" if quality_status == "passed" else "needs_review"
            ),
            "version_id": (
                str(current.get("version_id") or "")
                if isinstance(current, Mapping)
                else ""
            ),
            "quality_codes": (
                list(current.get("quality_codes") or [])
                if isinstance(current, Mapping)
                else []
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "error_category": type(exc).__name__,
        }


def _merge_governance_audits(*audits: Mapping[str, Any]) -> dict[str, Any]:
    retrieval_misses: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    secondary_matches: list[dict[str, Any]] = []
    relation_hints: list[dict[str, Any]] = []
    retrieval_question_ids: list[int] = []
    proposal_question_ids: list[int] = []
    seen_misses: set[str] = set()
    seen_proposals: set[str] = set()
    seen_secondary: set[str] = set()
    seen_relation_hints: set[str] = set()
    for audit in audits:
        for item in audit.get("retrieval_misses", []):
            if not isinstance(item, Mapping):
                continue
            signature = repr(sorted(dict(item).items()))
            if signature not in seen_misses:
                seen_misses.add(signature)
                retrieval_misses.append(dict(item))
        for item in audit.get("proposals", []):
            if not isinstance(item, Mapping):
                continue
            signature = _proposal_key(item)
            if signature not in seen_proposals:
                seen_proposals.add(signature)
                proposals.append(dict(item))
        for item in audit.get("secondary_matches", []):
            if not isinstance(item, Mapping):
                continue
            signature = repr(sorted(dict(item).items()))
            if signature not in seen_secondary:
                seen_secondary.add(signature)
                secondary_matches.append(dict(item))
        for item in audit.get("relation_hints", []):
            if not isinstance(item, Mapping):
                continue
            signature = repr(sorted(dict(item).items()))
            if signature not in seen_relation_hints:
                seen_relation_hints.add(signature)
                relation_hints.append(dict(item))
        for key, target in (
            ("retrieval_miss_question_ids", retrieval_question_ids),
            ("proposal_question_ids", proposal_question_ids),
        ):
            for question_id in audit.get(key, []):
                value = int(question_id)
                if value not in target:
                    target.append(value)
    return {
        "retrieval_misses": retrieval_misses,
        "proposals": proposals,
        "secondary_matches": secondary_matches,
        "relation_hints": relation_hints,
        "retrieval_miss_question_ids": retrieval_question_ids,
        "proposal_question_ids": proposal_question_ids,
    }


def _normalize_question_ids(value: object) -> list[int]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("question_ids must be a list")
    result: list[int] = []
    seen: set[int] = set()
    for raw in value:
        try:
            question_id = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("question_ids must contain integers") from exc
        if question_id <= 0:
            raise ValueError("question_ids must contain positive integers")
        if question_id not in seen:
            seen.add(question_id)
            result.append(question_id)
    if not result:
        raise ValueError("question_ids must not be empty")
    if len(result) > 500:
        raise ValueError("question_ids exceeds the batch limit")
    return result


def _normalize_retry_evidence_question_ids(
    value: object,
    *,
    requested_ids: Sequence[int],
) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise ValueError("retry_evidence_question_ids must be a list")
    if not value:
        return []
    normalized = _normalize_question_ids(value)
    requested = set(int(item) for item in requested_ids)
    if any(question_id not in requested for question_id in normalized):
        raise ValueError(
            "retry_evidence_question_ids must be a subset of question_ids"
        )
    return normalized


def _load_tagging_candidates(
    db_path: Path,
    question_ids: list[int],
    *,
    curriculum_volume_id: str = "",
    force_question_ids: set[int] | None = None,
) -> tuple[dict[int, TaggingContext], list[int], list[int]]:
    placeholders = ",".join("?" for _ in question_ids)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT q.id, q.question_text, q.answer_text, q.question_number,
                   q.question_type, q.has_images, q.is_deleted,
                   p.grade, p.semester, p.textbook_version,
                   p.exam_type, p.district
            FROM questions q
            LEFT JOIN papers p ON p.id = q.paper_id
            WHERE q.id IN ({placeholders})
            """,
            question_ids,
        ).fetchall()
        tag_rows = conn.execute(
            f"""
            SELECT question_id, tag_type, tag_value FROM question_tags
            WHERE question_id IN ({placeholders})
              AND COALESCE(tag_value, '') <> ''
            ORDER BY question_id ASC, id ASC
            """,
            question_ids,
        ).fetchall()
    rows_by_id = {int(row["id"]): row for row in rows}
    tag_types: dict[int, set[str]] = {}
    for row in tag_rows:
        question_id = int(row["question_id"])
        tag_types.setdefault(question_id, set()).add(str(row["tag_type"]))
    required = set(CORE_ANALYSIS_TAG_TYPES)
    contexts: dict[int, TaggingContext] = {}
    complete: list[int] = []
    unavailable: list[int] = []
    forced = force_question_ids or set()
    for question_id in question_ids:
        row = rows_by_id.get(question_id)
        if row is None or bool(row["is_deleted"]):
            unavailable.append(question_id)
            continue
        if required.issubset(tag_types.get(question_id, set())):
            complete.append(question_id)
            if question_id not in forced:
                continue
        contexts[question_id] = TaggingContext(
            question_text=str(row["question_text"] or ""),
            answer_text=str(row["answer_text"] or ""),
            question_number=str(row["question_number"] or ""),
            question_type=str(row["question_type"] or ""),
            grade=str(row["grade"] or ""),
            semester=str(row["semester"] or ""),
            textbook_version=str(row["textbook_version"] or ""),
            curriculum_volume_id=curriculum_volume_id,
            exam_type=str(row["exam_type"] or ""),
            district=str(row["district"] or ""),
            has_images=bool(row["has_images"]),
        )
    return contexts, complete, unavailable


def _load_analysis_gaps(
    db_path: Path,
    question_ids: Sequence[int],
) -> dict[int, dict[str, bool]]:
    """Read which saved analysis projections can be reused.

    The question-bank "补齐" action must also repair an evidence or training
    point that failed after tags were saved.  The caller can then request only
    the missing projection instead of sending the same combined request again.
    """

    ids = [int(value) for value in question_ids]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT q.id
                   , EXISTS (
                       SELECT 1
                       FROM question_solution_evidence_versions evidence
                       WHERE evidence.question_id = q.id
                         AND evidence.status IN ('proposed', 'approved')
                   ) AS evidence_ready
                   , EXISTS (
                       SELECT 1
                       FROM training_criterion_heads head
                       JOIN training_criterion_versions version
                         ON version.version_id = head.current_version_id
                       WHERE head.question_id = q.id
                         AND version.status IN ('proposed', 'approved')
                   ) AS criteria_ready
            FROM questions q
            WHERE q.id IN ({placeholders})
              AND COALESCE(q.is_deleted, 0) = 0
            ORDER BY q.id
            """,
            ids,
        ).fetchall()
    return {
        int(row["id"]): {
            "evidence_ready": bool(row["evidence_ready"]),
            "criteria_ready": bool(row["criteria_ready"]),
        }
        for row in rows
    }


def _load_missing_analysis_ids(
    db_path: Path,
    question_ids: Sequence[int],
) -> list[int]:
    """Compatibility helper for callers that only need the missing ids."""

    gaps = _load_analysis_gaps(db_path, question_ids)
    return [
        question_id
        for question_id in question_ids
        if not (
            gaps.get(int(question_id), {}).get("evidence_ready")
            and gaps.get(int(question_id), {}).get("criteria_ready")
        )
    ]


def _plan_taxonomy(
    ai_service: Any,
    governance: Any | None,
    *,
    contexts: Mapping[int, TaggingContext],
) -> tuple[dict[int, dict[str, Any]], int]:
    if governance is None:
        freeze = getattr(ai_service, "freeze_taxonomy", None)
        if callable(freeze):
            revision = max(0, int(freeze()))
        else:
            revision = max(
                0,
                int(getattr(ai_service, "taxonomy_revision", 0) or 0),
            )
        return {}, revision
    prompt_contexts = {
        question_id: context.to_dict()
        for question_id, context in contexts.items()
    }
    planner = getattr(governance, "prompt_contracts", None)
    if callable(planner):
        planned = planner(prompt_contexts)
        contracts = {
            int(question_id): dict(contract)
            for question_id, contract in planned.items()
            if question_id in contexts and isinstance(contract, Mapping)
        }
    else:
        contracts = {
            question_id: dict(
                governance.prompt_contract(context.to_dict())
            )
            for question_id, context in contexts.items()
        }
    revisions = {
        _taxonomy_revision(contract) for contract in contracts.values()
    }
    if len(revisions) > 1:
        raise ValueError("per-question taxonomy plans do not share one revision")
    return contracts, next(iter(revisions), 0)


def _planned_graph_release_id(
    contracts: Mapping[int, Mapping[str, Any]],
) -> str:
    release_ids = {
        str(contract.get("knowledge_graph_release_id") or "").strip()
        for contract in contracts.values()
        if str(contract.get("knowledge_graph_release_id") or "").strip()
    }
    if len(release_ids) > 1:
        raise ValueError(
            "per-question taxonomy plans do not share one graph release"
        )
    return next(iter(release_ids), "")


def _persist_proposals(
    governance: Any,
    result: AITaggingResult,
    *,
    question_id: int,
    job_id: str,
    expected_revision: int,
    knowledge_graph_release_id: str,
) -> list[dict[str, Any]]:
    assert result.analysis is not None
    payload = result.analysis.to_dict()
    payload["proposed_tags"] = [
        *payload.get("proposed_tags", []),
        *result.proposals,
    ]
    proposal_context: dict[str, object] = {
        "persist_proposals": True,
        "question_ref": str(question_id),
        "model": str(result.model_name or ""),
        "request_token": (
            f"tagging-sync:{job_id}:question:{question_id}:"
            f"taxonomy:{expected_revision}:graph:{knowledge_graph_release_id or 'none'}"
        ),
    }
    constrained = governance.constrain(payload, context=proposal_context)
    if not isinstance(constrained, Mapping):
        raise ValueError("taxonomy governance returned an invalid result")
    return [
        dict(item)
        for item in constrained.get("proposals", [])
        if isinstance(item, Mapping)
    ]


def _allocate_observation_sequences(
    governance: Any | None,
    *,
    generation_id: str,
    question_ids: Sequence[int],
) -> dict[str, int]:
    allocate = getattr(governance, "allocate_observation_sequences", None)
    if not callable(allocate) or not question_ids:
        return {}
    return dict(
        allocate(
            generation_id=generation_id,
            question_ids=[str(question_id) for question_id in question_ids],
        )
    )


def _record_successful_observation(
    governance: Any | None,
    *,
    question_id: int,
    generation_id: str,
    proposal_ids: Sequence[str],
    taxonomy_revision: int,
    graph_release_id: str,
    allocated: Mapping[str, int],
) -> None:
    record = getattr(governance, "record_successful_observation", None)
    if not callable(record) or str(question_id) not in allocated:
        return
    record(
        question_id=str(question_id),
        generation_id=generation_id,
        proposal_ids=list(proposal_ids),
        taxonomy_revision=max(0, int(taxonomy_revision)),
        graph_release_id=str(graph_release_id or ""),
    )


def _proposal_id(item: Mapping[str, Any]) -> str:
    return str(
        item.get("proposal_id")
        or item.get("id")
        or item.get("term_id")
        or ""
    ).strip()


def _proposal_key(item: Mapping[str, Any]) -> str:
    proposal_id = _proposal_id(item)
    if proposal_id:
        return f"id:{proposal_id}"
    dimension = str(item.get("dimension") or "").strip().casefold()
    name = str(item.get("name") or item.get("proposed_name") or "").strip().casefold()
    return f"value:{dimension}:{name}"


def _taxonomy_revision(contract: object) -> int:
    if not isinstance(contract, Mapping):
        return 0
    raw = contract.get("taxonomy_revision", contract.get("revision", 0))
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _result_failure(question_id: int, result: AITaggingResult) -> dict[str, object]:
    if not result.ok and result.error:
        return _failure(
            question_id,
            classify_tagging_error(RuntimeError(str(result.error))),
        )
    if result.quality_status != "complete":
        return _failure(question_id, "quality")
    error = RuntimeError(str(result.error or "AI tagging failed"))
    return _failure(question_id, classify_tagging_error(error))


def _failure(question_id: int, category: str) -> dict[str, object]:
    safe_category = category if category in _PUBLIC_FAILURE_MESSAGES else "unknown"
    return {
        "question_id": int(question_id),
        "category": safe_category,
        "message": _PUBLIC_FAILURE_MESSAGES[safe_category],
    }
