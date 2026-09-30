from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from question_bank.current_knowledge import CurrentFineTermResolver
from question_bank.database.schema import connect
from question_bank.models.question import (
    DERIVED_PENDING_STATUS,
    analysis_ownership_satisfied,
)
from question_bank.models.tag_schema import TaggingContext
from question_bank.services.ai_tagging_service import (
    AITaggingResult,
    AITaggingService,
    classify_tagging_error,
    is_auto_saveable_result,
)
from question_bank.services.question_write_service import QuestionBankWriteService
from question_bank.solution_evidence import (
    SolutionEvidenceProjectionWriter,
    SolutionEvidenceRepository,
)
from question_bank.taxonomy.snapshot import QuestionTaxonomySnapshot
from question_bank.training_criteria import (
    BankQuestionTypeSuggestionWriter,
    CombinedAnalysisRepository,
    CombinedQuestionAnalysisModule,
    ExistingTagProjectionWriter,
    OpenAICombinedAnalysisGateway,
    QuestionAnalysisInputLoader,
    QuestionAnalysisWorkItem,
    TrainingCriterionModule,
    combined_analysis_retry_budget,
    solution_evidence_source_content_hash,
)

from .execution_locks import keyed_execution_locks
from .manager import JobContext

TaggingFactory = Callable[[], AITaggingService]
LOGGER = logging.getLogger(__name__)
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
    "rate_limit": "模型请求过于频繁，请稍后再试。",
    "timeout": "分析超时，可稍后补齐未完成题目。",
    "network": "暂时连不上分析服务，已保存进度。",
    "parse": "模型返回无法解析，可稍后重试未完成题目。",
    "validation": "这道题暂时无法完成分析。",
    "missing_image": "题目标记有图但没有抽出可用图片。",
    "quality": "分析结果未达到保存标准，需要补齐或审核。",
    "save": "完整标签未能保存。",
    "evidence": "解题证据未能保存。",
    "training_criteria": "训练判定点未能发布。",
    "unknown": "分析失败，已保存进度。",
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
    if context.payload.get("retry_relation_question_ids"):
        raise ValueError("knowledge relation generation has been retired")
    question_ids = _normalize_question_ids(context.payload.get("question_ids"))
    retry_evidence_question_ids = _normalize_retry_evidence_question_ids(
        context.payload.get("retry_evidence_question_ids"),
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
            force_retag_question_ids=force_retag_question_ids,
            curriculum_volume_id=str(
                context.payload.get("curriculum_volume_id") or ""
            ).strip(),
        )



def _run_tagging_sync_job_locked(**kwargs: Any) -> dict[str, object]:
    """Analyze each full-content identity once within the requested batch."""
    from question_bank.services.duplicate_analysis_copy_service import (
        copy_duplicate_analysis,
        exact_identity_map,
        link_exact_duplicate,
    )
    question_ids = kwargs["question_ids"]
    root = kwargs["data_root"]
    if root is None or len(question_ids) < 2:
        return _run_distinct_tagging_sync_job_locked(**kwargs)
    database = kwargs["question_bank_db_path"]
    with connect(database) as conn:
        identities = exact_identity_map(conn, data_root=root)
    groups: dict[str, list[int]] = {}
    for qid in question_ids:
        groups.setdefault(identities.get(qid) or f"unresolved:{qid}", []).append(qid)
    if all(len(group) == 1 for group in groups.values()):
        return _run_distinct_tagging_sync_job_locked(**kwargs)
    gaps = _load_analysis_gaps(database, question_ids, data_root=root,
                               curriculum_volume_id=kwargs["curriculum_volume_id"])
    _, complete_ids, _ = _load_tagging_candidates(database, question_ids,
        curriculum_volume_id=kwargs["curriculum_volume_id"])
    complete = set(complete_ids)
    representatives: list[int] = []
    copies: dict[int, int] = {}
    for group in groups.values():
        representative = min(group, key=lambda qid: (
            -(int(qid in complete) + sum(gaps.get(qid, {}).values())), qid))
        representatives.append(representative)
        copies.update({qid: representative for qid in group if qid != representative})
    distinct = dict(kwargs, question_ids=representatives)
    for field in ("retry_evidence_question_ids", "force_retag_question_ids"):
        requested = set(kwargs[field])
        distinct[field] = list(dict.fromkeys(copies.get(qid, qid) for qid in question_ids if qid in requested))
    result = _run_distinct_tagging_sync_job_locked(**distinct)
    successful = set(result.get("successful_question_ids", []))
    for target, source in copies.items():
        kwargs["context"].raise_if_cancelled()
        if source in successful:
            with connect(database) as conn:
                link_exact_duplicate(conn, question_id=target, source_id=source, signature=identities[source], copy_tags=target not in complete)
            copy_duplicate_analysis(database, source_question_id=source,
                                    target_question_id=target, data_root=root)
    # Verify persisted products; a failed reuse stays a visible gap without an
    # extra paid request being silently appended to this batch.
    _, tagged_ids, _ = _load_tagging_candidates(database, list(copies),
        curriculum_volume_id=kwargs["curriculum_volume_id"])
    saved = _load_analysis_gaps(database, list(copies), data_root=root,
                                curriculum_volume_id=kwargs["curriculum_volume_id"])
    for target, source in copies.items():
        if source in successful and target in tagged_ids:
            result.setdefault("successful_question_ids", []).append(target)
        else:
            result.setdefault("failed_question_ids", []).append(target)
            source_failure = next((entry for entry in result.get("failures", []) if entry["question_id"] == source), None)
            result.setdefault("failures", []).append(dict(source_failure, question_id=target) if source_failure else
                _failure(target, "save", detail="相同题分析尚未完成，可补齐未完成题目。"))
        for product, flag in (("evidence", "evidence_ready"), ("criteria", "criteria_ready")):
            status = "succeeded" if saved.get(target, {}).get(flag) else "failed"
            result.setdefault(f"{product}_{status}_question_ids", []).append(target)
    for field in ("successful_question_ids", "failed_question_ids", "evidence_succeeded_question_ids",
                  "evidence_failed_question_ids", "criteria_succeeded_question_ids", "criteria_failed_question_ids"):
        members = set(result.get(field, []))
        result[field] = [qid for qid in question_ids if qid in members]
    result["tagged_count"] = int(result.get("tagged_count", 0)) + sum(target in result["successful_question_ids"] and target not in complete for target in copies)
    result["skipped_complete_count"] = int(result.get("skipped_complete_count", 0)) + sum(target in complete and all(gaps.get(target, {}).values()) for target in copies)
    result.update(requested_count=len(question_ids),
                  complete_tagged_count=len(result["successful_question_ids"]),
                  failed_count=len(result["failed_question_ids"]),
                  evidence_count=len(result["evidence_succeeded_question_ids"]),
                  criteria_count=len(result["criteria_succeeded_question_ids"]))
    if result["failed_question_ids"] or result["evidence_failed_question_ids"] or result["criteria_failed_question_ids"]:
        result["outcome"] = "partial" if result["successful_question_ids"] else "failed"
        result["retryable"] = True
    return result


def _run_distinct_tagging_sync_job_locked(
    *,
    context: JobContext,
    question_bank_db_path: Path,
    ai_service_factory: TaggingFactory,
    taxonomy_governance: Any | None,
    batch_size: int,
    data_root: Path | None,
    question_ids: list[int],
    retry_evidence_question_ids: list[int],
    force_retag_question_ids: list[int],
    curriculum_volume_id: str,
) -> dict[str, object]:
    size = max(1, min(int(batch_size), 50))
    db_path = Path(question_bank_db_path)
    context.raise_if_cancelled()
    try:
        analysis_gaps = _load_analysis_gaps(
            db_path,
            question_ids,
            data_root=data_root,
            curriculum_volume_id=curriculum_volume_id,
        )
        analysis_retry_ids = [
            question_id
            for question_id in question_ids
            if not (
                analysis_gaps.get(question_id, {}).get("evidence_ready")
                and analysis_gaps.get(question_id, {}).get("criteria_ready")
            )
        ]
        requested_force_ids = (
            set(retry_evidence_question_ids)
            | set(analysis_retry_ids)
            | set(force_retag_question_ids)
        )
        verify_tag_sources = (
            data_root is not None and taxonomy_governance is not None
        )
        contexts, complete_ids, unavailable_ids = _load_tagging_candidates(
            db_path,
            question_ids,
            curriculum_volume_id=curriculum_volume_id,
            force_question_ids=requested_force_ids,
            include_complete_contexts=verify_tag_sources,
        )
        stale_tag_ids: set[int] = set()
        if verify_tag_sources and complete_ids:
            verification_contexts = {
                question_id: contexts[question_id]
                for question_id in complete_ids
                if question_id in contexts
            }
            verification_contracts, _ = _plan_taxonomy(
                None,
                taxonomy_governance,
                contexts=verification_contexts,
            )
            loader = QuestionAnalysisInputLoader(
                db_path=db_path,
                data_root=Path(data_root),
            )
            current_inputs = loader.load(
                complete_ids,
                taxonomy_contracts=verification_contracts,
                curriculum_volume_id=curriculum_volume_id,
            )
            source_current = _load_tag_source_currentness(
                db_path,
                current_inputs=current_inputs,
            )
            stale_tag_ids = {
                question_id
                for question_id, current in source_current.items()
                if not current
            }
            if stale_tag_ids:
                complete_ids = [
                    question_id
                    for question_id in complete_ids
                    if question_id not in stale_tag_ids
                ]
            complete_set_after_source_check = set(complete_ids)
            contexts = {
                question_id: tagging_context
                for question_id, tagging_context in contexts.items()
                if (
                    question_id not in complete_set_after_source_check
                    or question_id in requested_force_ids
                )
            }
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
    force_retag_set = set(force_retag_question_ids) | stale_tag_ids
    evidence_only_ids = [
        item
        for item in pending_ids
        if (
            item in evidence_retry_set
            and item in complete_set
            and item not in force_retag_set
        )
    ]
    normal_pending_ids = [
        item for item in pending_ids if item not in set(evidence_only_ids)
    ]
    total_batches = max(1, (len(pending_ids) + size - 1) // size)
    try:
        ai_service = ai_service_factory() if pending_ids else None
    except Exception:
        raise RuntimeError("tagging sync setup failed") from None
    write_service = QuestionBankWriteService(
        db_path,
        data_root=(data_root or db_path.parent),
    )
    governance = taxonomy_governance
    taxonomy_revision = 0
    knowledge_graph_release_id = ""
    taxonomy_contracts: dict[int, QuestionTaxonomySnapshot] = {}
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
            taxonomy_governance=governance,
            analysis_gaps=analysis_gaps,
            curriculum_volume_id=curriculum_volume_id,
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

    batch_context = (
        write_service.tag_analysis_batch() if pending_ids else nullcontext()
    )
    with batch_context:
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
                    allow_batch_fallback=True,
                    quality_retry_limit=1,
                    enable_review=False,
                )
            except Exception as exc:
                context.raise_if_cancelled()
                category = classify_tagging_error(exc)
                failures.extend(
                    _failure(question_id, category) for question_id in batch_ids
                )
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
                            taxonomy_contract=taxonomy_contracts.get(
                                question_id, {}
                            ),
                        )
                    except Exception:
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
                    saved = write_service.save_tag_analysis(
                        question_id,
                        result.analysis,
                        model_name=result.model_name,
                        confidence=result.analysis.confidence,
                        taxonomy_governance=governance,
                    )
                except Exception:
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
                except Exception:
                    # Same rule as the combined path: the tag is already
                    # saved, so an audit-registration failure is logged but
                    # never marks the question as failed.
                    LOGGER.warning(
                        "observation registration failed for question %s",
                        question_id,
                        exc_info=True,
                    )
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
    taxonomy_governance: Any | None,
    analysis_gaps: Mapping[int, Mapping[str, bool]],
    curriculum_volume_id: str,
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
                curriculum_volume_id=curriculum_volume_id,
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
            max_auto_retries=combined_analysis_retry_budget(ai_service),
        ),
        context,
    )
    mapping_repository = CurrentFineTermResolver.from_active_database(db_path)
    tag_write_service = QuestionBankWriteService(db_path, data_root=data_root)
    tag_writer = ExistingTagProjectionWriter(
        write_service=tag_write_service,
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
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(db_path),
        gateway=gateway,
        tag_writer=tag_writer,
        evidence_writer=evidence_writer,
        criterion_module=criterion_module,
        question_type_writer=BankQuestionTypeSuggestionWriter(
            write_service=tag_write_service,
        ),
    )
    explicit_evidence_retry_set = set(retry_evidence_question_ids)
    evidence_only_set = set(evidence_only_ids) | explicit_evidence_retry_set
    complete_set = set(complete_ids)
    work_items: list[QuestionAnalysisWorkItem] = []
    tag_only_set: set[int] = set()
    for question_id in pending_ids:
        question = loaded_by_id.get(question_id)
        if question is None:
            continue
        gap = analysis_gaps.get(question_id, {})
        evidence_ready = bool(gap.get("evidence_ready"))
        criteria_ready = bool(gap.get("criteria_ready"))
        # 补齐只重做未就绪的投影。判定点过期、缺失或失败时必须再走模型；
        # 仅用已存解题证据本地发布会跳过发送，且过期判定点会写失败。
        analyze_tag = (
            question_id not in complete_set
            and question_id not in set(evidence_only_ids)
        )
        analyze_evidence = (
            question_id in evidence_only_set
            or not evidence_ready
            or not criteria_ready
        )
        if analyze_tag and not analyze_evidence:
            tag_only_set.add(question_id)
        work_items.append(
            QuestionAnalysisWorkItem(
                question=question,
                analyze_tag=analyze_tag,
                analyze_solution_evidence=analyze_evidence,
                publish_saved_criterion=False,
            )
        )

    analysis_total = sum(item.projection is not None for item in work_items)

    def analysis_progress(update: Mapping[str, Any]) -> None:
        processed = min(
            analysis_total,
            max(0, int(update.get("processed_questions") or 0)),
        )
        fraction = processed / max(analysis_total, 1)
        context.report(
            0.1 + 0.72 * fraction,
            "tagging_sync",
            f"AI 分析已处理 {processed}/{analysis_total} 道题",
        )

    operation_id = f"tagging-sync:{context.job_id}"
    tag_request_ids = [
        item.question.question_id for item in work_items if item.analyze_tag
    ]
    tag_request_set = set(tag_request_ids)
    observation_sequences = _allocate_observation_sequences(
        taxonomy_governance or ai_service.taxonomy_governance,
        generation_id=operation_id,
        question_ids=tag_request_ids,
    )
    batch_context = (
        tag_write_service.tag_analysis_batch()
        if tag_request_ids
        else nullcontext()
    )
    with batch_context:
        workflow = module.analyze_work_items(
            operation_id=operation_id,
            work_items=work_items,
            progress_callback=analysis_progress,
        )
        context.raise_if_cancelled()
    items = {
        int(item["question_id"]): item
        for item in workflow.get("items", [])
        if isinstance(item, Mapping)
    }
    criterion_audits = [
        workflow.get("criterion_audit", {"items": []})
    ]
    question_projection_audits = workflow.get(
        "question_projection_audits",
        {},
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
        if question_id in tag_request_set:
            if str(item.get("tag_status")) == "succeeded":
                question_audit = (
                    question_projection_audits.get(question_id, {})
                    if isinstance(question_projection_audits, Mapping)
                    else {}
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
                        allocated=observation_sequences,
                    )
                except Exception:
                    # Observation registration is an audit projection. The tag
                    # itself is already persisted; the re-verification below
                    # re-reads the question-bank truth, so a registration
                    # failure must not mark this question as failed.
                    LOGGER.warning(
                        "observation registration failed for question %s",
                        question_id,
                        exc_info=True,
                    )
                tag_success.append(question_id)
                newly_tagged.append(question_id)
            else:
                category = _combined_public_category(
                    str(item.get("tag_error_category") or "")
                )
                failures.append(
                    _failure(
                        question_id,
                        category,
                        detail=str(item.get("tag_error_detail") or ""),
                    )
                )
        if question_id not in tag_only_set:
            if str(item.get("criteria_status")) == "succeeded":
                evidence_success.append(question_id)
            else:
                evidence_failed.append(question_id)
                if not any(
                    int(entry["question_id"]) == question_id
                    for entry in failures
                ):
                    failures.append(
                        _failure(
                            question_id,
                            "evidence",
                            detail=str(item.get("criteria_error_detail") or ""),
                        )
                    )
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
        ):
            criterion_failed.append(question_id)
            if not any(
                int(entry["question_id"]) == question_id
                and str(entry.get("category") or "") == "training_criteria"
                for entry in failures
            ):
                failures.append(_failure(question_id, "training_criteria"))
    # The workflow ledger is an execution record, not the question-bank truth.
    # A process restart, an old child-operation row, or a projection writer
    # failure can leave it saying "succeeded" while the teacher-visible row is
    # absent.  Re-read all three projections before publishing terminal counts.
    _, persisted_tag_ids, _ = _load_tagging_candidates(
        db_path,
        requested_ids,
        curriculum_volume_id="",
    )
    persisted_gaps = _load_analysis_gaps(
        db_path,
        requested_ids,
        data_root=data_root,
        curriculum_volume_id=curriculum_volume_id,
        current_inputs=loaded_by_id,
    )
    persisted_source_current = _load_tag_source_currentness(
        db_path,
        current_inputs=tuple(loaded_by_id.values()),
    )
    persisted_tag_set = {
        question_id
        for question_id in persisted_tag_ids
        if persisted_source_current.get(question_id, True)
    }
    persisted_evidence_set = {
        question_id
        for question_id, gap in persisted_gaps.items()
        if gap.get("evidence_ready")
    }
    persisted_criterion_set = {
        question_id
        for question_id, gap in persisted_gaps.items()
        if gap.get("criteria_ready")
    }
    claimed_new_tag_ids = {
        question_id
        for question_id in tag_request_set
        if str(items.get(question_id, {}).get("tag_status")) == "succeeded"
    }
    for question_id in requested_ids:
        if question_id not in persisted_tag_set and not any(
            int(entry["question_id"]) == question_id
            for entry in failures
        ):
            failures.append(_failure(question_id, "save"))
        if question_id not in persisted_evidence_set and not any(
            int(entry["question_id"]) == question_id
            and str(entry.get("category") or "") == "evidence"
            for entry in failures
        ):
            failures.append(_failure(question_id, "evidence"))
        if question_id not in persisted_criterion_set and not any(
            int(entry["question_id"]) == question_id
            and str(entry.get("category") or "") == "training_criteria"
            for entry in failures
        ):
            failures.append(_failure(question_id, "training_criteria"))

    successful_ids = [
        item for item in requested_ids if item in persisted_tag_set
    ]
    newly_tagged = [
        item
        for item in requested_ids
        if item in claimed_new_tag_ids and item in persisted_tag_set
    ]
    evidence_success = [
        item for item in requested_ids if item in persisted_evidence_set
    ]
    evidence_failed = [
        item for item in requested_ids if item not in persisted_evidence_set
    ]
    criterion_review_set = set(criterion_review) & persisted_criterion_set
    criterion_review = [
        item for item in requested_ids if item in criterion_review_set
    ]
    criterion_success = [
        item
        for item in requested_ids
        if item in persisted_criterion_set and item not in criterion_review_set
    ]
    criterion_failed = [
        item for item in requested_ids if item not in persisted_criterion_set
    ]
    failed_set = {
        int(item["question_id"]) for item in failures
    } | set(evidence_failed) | set(criterion_failed)
    failed_ids = [item for item in requested_ids if item in failed_set]
    audits = dict(workflow.get("projection_audit") or {})
    if (
        failures
        or evidence_failed
        or criterion_failed
        or criterion_review
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
            failures or evidence_failed or criterion_failed
        ),
    }


def _combined_public_category(category: str) -> str:
    normalized = str(category or "").strip().casefold()
    if normalized in {"tag_validation", "validation"}:
        return "quality"
    if normalized == "missing_image":
        return "missing_image"
    if normalized in {"training_criteria", "criterionqualityerror"}:
        return "training_criteria"
    if normalized in {"timeout", "parse"}:
        return normalized
    return "unknown"


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
    include_complete_contexts: bool = False,
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
    derived_pending: set[int] = set()
    for row in tag_rows:
        question_id = int(row["question_id"])
        tag_type = str(row["tag_type"])
        tag_types.setdefault(question_id, set()).add(tag_type)
        if (
            tag_type == "tag_status"
            and str(row["tag_value"] or "").strip() == DERIVED_PENDING_STATUS
        ):
            derived_pending.add(question_id)
    from question_bank.solution_evidence.repository import (
        load_evidence_parts_for_tagging,
    )

    evidence_parts_by_question = load_evidence_parts_for_tagging(
        db_path,
        [qid for qid in question_ids if qid in rows_by_id],
    )
    contexts: dict[int, TaggingContext] = {}
    complete: list[int] = []
    unavailable: list[int] = []
    forced = force_question_ids or set()
    for question_id in question_ids:
        row = rows_by_id.get(question_id)
        if row is None or bool(row["is_deleted"]):
            unavailable.append(question_id)
            continue
        seen = tag_types.get(question_id, set())
        # 新口径：能力标签在位 + 归属就绪（判定点关联派生或 derived_pending 标记）。
        if "ability" in seen and analysis_ownership_satisfied(
            seen,
            derived_pending=question_id in derived_pending,
        ):
            complete.append(question_id)
            if question_id not in forced and not include_complete_contexts:
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
            evidence_parts=evidence_parts_by_question.get(question_id, []),
        )
    return contexts, complete, unavailable


def _load_tag_source_currentness(
    db_path: Path,
    *,
    current_inputs: Sequence[Any],
) -> dict[int, bool]:
    """Compare current inputs with the latest persisted successful tag run.

    The fingerprint covers question content only (text, answer, type, images,
    volume). Taxonomy/vocabulary state is deliberately excluded: a vocabulary
    revision bump must not invalidate already-saved tags.

    Rows without combined-analysis history are legacy-compatible: their tag
    presence remains authoritative. Once a question has a versioned tag run,
    however, an older source hash must not make the current question complete.
    """

    current_by_id = {
        int(item.question_id): str(item.source_content_hash)
        for item in current_inputs
    }
    if not current_by_id:
        return {}
    question_ids = tuple(current_by_id)
    placeholders = ",".join("?" for _ in question_ids)
    with connect(db_path) as conn:
        table = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'question_analysis_items'
            """
        ).fetchone()
        if table is None:
            return {}
        rows = conn.execute(
            f"""
            SELECT question_id, source_content_hash
            FROM question_analysis_items
            WHERE question_id IN ({placeholders})
              AND tag_status = 'succeeded'
            ORDER BY rowid DESC
            """,
            question_ids,
        ).fetchall()
    latest_hashes: dict[int, str] = {}
    for row in rows:
        latest_hashes.setdefault(
            int(row["question_id"]),
            str(row["source_content_hash"]),
        )
    return {
        question_id: latest_hashes[question_id] == current_hash
        for question_id, current_hash in current_by_id.items()
        if question_id in latest_hashes
    }


def _load_analysis_gaps(
    db_path: Path,
    question_ids: Sequence[int],
    *,
    data_root: Path | None = None,
    curriculum_volume_id: str = "",
    current_inputs: Mapping[int, Any] | None = None,
) -> dict[int, dict[str, bool]]:
    """Read which saved analysis projections can be reused.

    The question-bank "补齐" action must also repair an evidence or training
    point that failed or went stale after tags were saved.  The caller then
    requests only the missing projection; already-complete questions stay
    skipped, so one leftover question does not retag the rest of the paper.
    """

    ids = [int(value) for value in question_ids]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    with connect(db_path) as conn:
        question_rows = conn.execute(
            f"""
            SELECT q.id
            FROM questions q
            WHERE q.id IN ({placeholders})
              AND COALESCE(q.is_deleted, 0) = 0
            ORDER BY q.id
            """,
            ids,
        ).fetchall()
        active_ids = [int(row["id"]) for row in question_rows]
        if not active_ids:
            return {}
        active_placeholders = ",".join("?" for _ in active_ids)
        evidence_rows = conn.execute(
            f"""
            SELECT question_id, source_content_hash
            FROM question_solution_evidence_versions
            WHERE question_id IN ({active_placeholders})
              AND status IN ('proposed', 'approved')
            """,
            active_ids,
        ).fetchall()
        criterion_rows = conn.execute(
            f"""
            SELECT head.question_id,
                   head.current_source_hash,
                   version.source_content_hash AS version_source_hash,
                   version.status
            FROM training_criterion_heads head
            JOIN training_criterion_versions version
              ON version.version_id = head.current_version_id
            WHERE head.question_id IN ({active_placeholders})
            """,
            active_ids,
        ).fetchall()

    evidence_hashes: dict[int, set[str]] = {}
    for row in evidence_rows:
        evidence_hashes.setdefault(int(row["question_id"]), set()).add(
            str(row["source_content_hash"])
        )
    criterion_by_id = {
        int(row["question_id"]): row for row in criterion_rows
    }

    exact_inputs: dict[int, Any] | None = None
    if data_root is not None or current_inputs is not None:
        exact_inputs = {
            int(question_id): value
            for question_id, value in (current_inputs or {}).items()
            if int(question_id) in set(active_ids)
        }
        missing_ids = [
            question_id
            for question_id in active_ids
            if question_id not in exact_inputs
        ]
        if missing_ids and data_root is not None:
            loader = QuestionAnalysisInputLoader(
                db_path=db_path,
                data_root=Path(data_root),
            )
            try:
                loaded = loader.load(
                    missing_ids,
                    curriculum_volume_id=curriculum_volume_id,
                )
            except (KeyError, OSError, TypeError, ValueError):
                loaded = ()
            exact_inputs.update(
                {item.question_id: item for item in loaded}
            )

    result: dict[int, dict[str, bool]] = {}
    for question_id in active_ids:
        if exact_inputs is None:
            evidence_ready = bool(evidence_hashes.get(question_id))
            criterion = criterion_by_id.get(question_id)
            criteria_ready = bool(
                criterion is not None
                and str(criterion["status"]) in {"proposed", "approved"}
            )
        else:
            current = exact_inputs.get(question_id)
            if current is None:
                evidence_ready = False
                criteria_ready = False
            else:
                evidence_hash = solution_evidence_source_content_hash(current)
                criterion_hash = current.criterion_source_content_hash
                evidence_ready = evidence_hash in evidence_hashes.get(
                    question_id,
                    set(),
                )
                criterion = criterion_by_id.get(question_id)
                criteria_ready = bool(
                    criterion is not None
                    and str(criterion["status"]) in {"proposed", "approved"}
                    and str(criterion["current_source_hash"]) == criterion_hash
                    and str(criterion["version_source_hash"]) == criterion_hash
                )
        result[question_id] = {
            "evidence_ready": evidence_ready,
            "criteria_ready": criteria_ready,
        }
    return result


def _plan_taxonomy(
    ai_service: Any,
    governance: Any | None,
    *,
    contexts: Mapping[int, TaggingContext],
) -> tuple[dict[int, QuestionTaxonomySnapshot], int]:
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
            int(question_id): QuestionTaxonomySnapshot.capture(
                int(question_id),
                contract,
            )
            for question_id, contract in planned.items()
            if question_id in contexts and isinstance(contract, Mapping)
        }
    else:
        contracts = {
            question_id: QuestionTaxonomySnapshot.capture(
                question_id,
                governance.prompt_contract(context.to_dict()),
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
        (
            contract.knowledge_graph_release_id
            if isinstance(contract, QuestionTaxonomySnapshot)
            else str(contract.get("knowledge_graph_release_id") or "").strip()
        )
        for contract in contracts.values()
        if (
            contract.knowledge_graph_release_id
            if isinstance(contract, QuestionTaxonomySnapshot)
            else str(contract.get("knowledge_graph_release_id") or "").strip()
        )
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
    taxonomy_contract: Mapping[str, Any],
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
            f"taxonomy:{expected_revision}:graph:{knowledge_graph_release_id or 'none'}:"
            "candidates:"
            f"{str(taxonomy_contract.get('candidate_fingerprint') or 'none')}"
        ),
        "expected_revision": expected_revision,
        "allowed_term_ids": taxonomy_contract.get("allowed_term_ids", {}),
        "knowledge_catalog_revision": taxonomy_contract.get(
            "knowledge_catalog_revision"
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
    if isinstance(contract, QuestionTaxonomySnapshot):
        return contract.taxonomy_revision
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


def _failure(
    question_id: int,
    category: str,
    *,
    detail: str = "",
) -> dict[str, object]:
    safe_category = category if category in _PUBLIC_FAILURE_MESSAGES else "unknown"
    entry: dict[str, object] = {
        "question_id": int(question_id),
        "category": safe_category,
        "message": _PUBLIC_FAILURE_MESSAGES[safe_category],
    }
    safe_detail = str(detail or "").strip()
    if safe_detail:
        entry["detail"] = safe_detail
    return entry
