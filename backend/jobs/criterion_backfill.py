from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from question_bank.current_knowledge import CurrentFineTermResolver
from question_bank.models.tag_schema import TaggingContext
from question_bank.services.ai_tagging_service import AITaggingService
from question_bank.services.question_write_service import QuestionBankWriteService
from question_bank.solution_evidence import (
    SolutionEvidenceProjectionWriter,
    SolutionEvidenceRepository,
)
from question_bank.taxonomy.curriculum_catalog import (
    infer_curriculum_volume_from_text,
)
from question_bank.training_criteria import (
    BankQuestionTypeSuggestionWriter,
    CombinedAnalysisRepository,
    CombinedQuestionAnalysisModule,
    CriterionRevisionConflict,
    ExistingTagProjectionWriter,
    OpenAICombinedAnalysisGateway,
    QuestionAnalysisInput,
    QuestionAnalysisInputLoader,
    TrainingCriteriaDraft,
    TrainingCriterionModule,
    combined_analysis_retry_budget,
    usable_training_criterion,
)

from .manager import JobCancellationRequested, JobContext


class _CancellationAwareGateway:
    def __init__(self, gateway: Any, context: JobContext) -> None:
        self.gateway = gateway
        self.context = context

    @property
    def max_parallel_requests(self) -> int:
        try:
            return max(1, int(getattr(self.gateway, "max_parallel_requests", 1)))
        except (TypeError, ValueError):
            return 1

    def analyze(self, *args, **kwargs):
        self.context.raise_if_cancelled()
        return self.gateway.analyze(*args, **kwargs)


def run_criterion_backfill_job(
    *,
    context: JobContext,
    question_bank_db_path: Path,
    data_root: Path,
    ai_service_factory: Callable[[], AITaggingService],
    link_job_submitter: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, object]:
    run_id = str(context.payload.get("run_id") or "").strip().casefold()
    if len(run_id) != 64:
        raise ValueError("criterion backfill run_id is invalid")
    db_path = Path(question_bank_db_path)
    root = Path(data_root)
    criterion_module = TrainingCriterionModule(db_path)
    run = criterion_module.get_backfill_run(run_id)
    regenerate = run["mode"] == "regenerate"
    question_ids = criterion_module.claim_backfill(run_id)
    loader = QuestionAnalysisInputLoader(db_path=db_path, data_root=root)
    context.report(0.05, "criterion_backfill", "loading")

    loaded: dict[int, QuestionAnalysisInput] = {}
    for question_id in question_ids:
        try:
            loaded[question_id] = loader.load((question_id,))[0]
        except (KeyError, OSError, TypeError, ValueError):
            criterion_module.finish_backfill_item(
                run_id=run_id,
                question_id=question_id,
                status="failed",
                error_category="input_unavailable",
            )

    # 两遍加载：第一遍取 tagging_context 生成每题 taxonomy 候选表，
    # 第二遍带候选表重载，判定点正文生成才能拿到合法链接候选
    # （对齐 question_bank_sync._adopt_deferred_analysis_with_links）。
    tagging_service: AITaggingService | None = None
    if loaded:
        tagging_service = ai_service_factory()
        contracts = tagging_service.taxonomy_contracts(
            {
                question_id: _contract_context(question.tagging_context)
                for question_id, question in loaded.items()
            }
        )
        for question_id in list(loaded):
            try:
                loaded[question_id] = loader.load(
                    (question_id,), taxonomy_contracts=contracts
                )[0]
            except (KeyError, OSError, TypeError, ValueError):
                del loaded[question_id]
                criterion_module.finish_backfill_item(
                    run_id=run_id,
                    question_id=question_id,
                    status="failed",
                    error_category="input_unavailable",
                )

    pending: list[QuestionAnalysisInput] = []
    expected_revisions: dict[int, int] = {}
    for question in loaded.values():
        try:
            workspace = criterion_module.read(question)
        except Exception:
            criterion_module.finish_backfill_item(
                run_id=run_id,
                question_id=question.question_id,
                status="failed",
                error_category="version_read",
            )
            continue
        current = workspace.get("current_version")
        already_has_draft = bool(
            isinstance(current, Mapping)
            and current.get("source_content_hash")
            == question.criterion_source_content_hash
            and current.get("status") in {"proposed", "approved"}
        )
        if not regenerate and (
            workspace["available"] or already_has_draft
        ):
            version = usable_training_criterion(workspace) or current
            criterion_module.finish_backfill_item(
                run_id=run_id,
                question_id=question.question_id,
                status="skipped",
                version_id=(
                    str(version.get("version_id"))
                    if isinstance(version, Mapping)
                    else None
                ),
                error_category="already_available",
            )
            continue
        pending.append(question)
        expected_revisions[question.question_id] = int(
            workspace["revision"]
        )

    if pending:
        try:
            context.raise_if_cancelled()
        except JobCancellationRequested:
            criterion_module.cancel_backfill(run_id)
            raise
        try:
            assert tagging_service is not None
            protocol_adapter = tagging_service._protocol_adapter()
            gateway = _CancellationAwareGateway(
                OpenAICombinedAnalysisGateway(
                    protocol_adapter=protocol_adapter,
                    model_name=tagging_service.model,
                    max_auto_retries=combined_analysis_retry_budget(
                        tagging_service
                    ),
                ),
                context,
            )
            mapping_repository = CurrentFineTermResolver.from_active_database(
                db_path
            )
            write_service = QuestionBankWriteService(
                db_path,
                data_root=root,
            )
            analysis_module = CombinedQuestionAnalysisModule(
                repository=CombinedAnalysisRepository(db_path),
                gateway=gateway,
                tag_writer=ExistingTagProjectionWriter(
                    write_service=write_service,
                    tagging_service=tagging_service,
                ),
                evidence_writer=SolutionEvidenceProjectionWriter(
                    mapping_repository=mapping_repository,
                    evidence_repository=SolutionEvidenceRepository(db_path),
                ),
                question_type_writer=BankQuestionTypeSuggestionWriter(
                    write_service=write_service,
                ),
            )
            summary = analysis_module.analyze(
                operation_id=f"criterion-backfill:{run_id}",
                questions=tuple(pending),
                projection="training_criteria",
                progress_callback=lambda update: context.report(
                    0.1
                    + 0.72
                    * int(update.get("processed_questions") or 0)
                    / max(len(pending), 1),
                    "criterion_backfill",
                    (
                        "AI 分析已处理 "
                        f"{int(update.get('processed_questions') or 0)}/"
                        f"{len(pending)} 道题"
                    ),
                ),
            )
        except JobCancellationRequested:
            criterion_module.cancel_backfill(run_id)
            raise
        except Exception:
            for question in pending:
                criterion_module.finish_backfill_item(
                    run_id=run_id,
                    question_id=question.question_id,
                    status="failed",
                    error_category="setup",
                )
        else:
            items = {
                int(item["question_id"]): item
                for item in summary.get("items", [])
                if isinstance(item, Mapping)
            }
            for index, question in enumerate(pending, start=1):
                item = items.get(question.question_id, {})
                criteria = item.get("training_criteria")
                status = str(item.get("criteria_status") or "failed")
                if status != "succeeded" or not isinstance(
                    criteria,
                    Mapping,
                ):
                    criterion_module.finish_backfill_item(
                        run_id=run_id,
                        question_id=question.question_id,
                        status=(
                            "cancelled"
                            if status == "cancelled"
                            else "failed"
                        ),
                        error_category=str(
                            item.get("criteria_error_category")
                            or "analysis"
                        ),
                    )
                    continue
                try:
                    workspace = criterion_module.propose(
                        question=question,
                        draft=TrainingCriteriaDraft.from_model_dict(
                            criteria,
                            question=question,
                        ),
                        source_kind="backfill",
                        source_reference=(
                            f"analysis:criterion-backfill:{run_id}:"
                            f"{question.question_id}"
                        ),
                        actor_ref="criterion_backfill_job",
                        reason="旧题判定点显式回填",
                        expected_revision=expected_revisions[
                            question.question_id
                        ],
                    )
                except CriterionRevisionConflict:
                    criterion_module.finish_backfill_item(
                        run_id=run_id,
                        question_id=question.question_id,
                        status="skipped",
                        error_category="head_changed",
                    )
                except (TypeError, ValueError, RuntimeError):
                    criterion_module.finish_backfill_item(
                        run_id=run_id,
                        question_id=question.question_id,
                        status="failed",
                        error_category="version_save",
                    )
                else:
                    current = workspace["current_version"]
                    quality_status = str(current.get("quality_status") or "")
                    if quality_status == "passed":
                        criterion_module.finish_backfill_item(
                            run_id=run_id,
                            question_id=question.question_id,
                            status="succeeded",
                            version_id=str(current["version_id"]),
                        )
                    else:
                        criterion_module.finish_backfill_item(
                            run_id=run_id,
                            question_id=question.question_id,
                            status="failed",
                            version_id=str(current["version_id"]),
                            error_category="needs_review",
                        )
                context.report(
                    0.82 + 0.15 * index / max(len(pending), 1),
                    "criterion_backfill",
                    f"{index}/{len(pending)}",
                )

    result = criterion_module.complete_backfill(run_id)
    link_job_queued = False
    if link_job_submitter is not None and any(
        item["status"] == "succeeded" for item in result["items"]
    ):
        # §4.4：新版本成为可用版本后，由链接任务 missing_only 补链接。
        try:
            link_job_submitter({"mode": "missing_only", "question_ids": [
                item['question_id'] for item in result['items'] if item['status'] == 'succeeded'
            ]})
            link_job_queued = True
        except Exception:
            link_job_queued = False
    context.report(1.0, "criterion_backfill", str(result["status"]))
    context.raise_if_cancelled()
    return {
        "link_job_queued": link_job_queued,
        "run_id": run_id,
        "status": result["status"],
        "successful_question_ids": [
            item["question_id"]
            for item in result["items"]
            if item["status"] in {"succeeded", "skipped"}
        ],
        "failed_question_ids": [
            item["question_id"]
            for item in result["items"]
            if item["status"] in {"failed", "cancelled"}
        ],
        "retryable": any(
            item["status"] in {"failed", "cancelled"}
            for item in result["items"]
        ),
    }


def _contract_context(context: TaggingContext) -> TaggingContext:
    """Contract-building context: fill curriculum_volume_id from grade/semester."""
    if str(context.curriculum_volume_id or "").strip():
        return context
    volume = infer_curriculum_volume_from_text(
        f"{context.grade or ''}{context.semester or ''}"
    )
    if not isinstance(volume, Mapping):
        return context
    volume_id = str(volume.get("id") or "").strip()
    if not volume_id:
        return context
    return dataclasses.replace(context, curriculum_volume_id=volume_id)


__all__ = ["run_criterion_backfill_job"]
