from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from question_bank.services.ai_tagging_service import AITaggingService
from question_bank.services.question_service import QuestionService
from question_bank.solution_evidence import (
    FineTermCoreMappingRepository,
    SolutionEvidenceProjectionWriter,
    SolutionEvidenceRepository,
    build_fine_term_mapping_baseline,
    install_fine_term_mapping_baseline,
)
from question_bank.training_criteria import (
    CombinedAnalysisRepository,
    CombinedQuestionAnalysisModule,
    CriterionRevisionConflict,
    ExistingTagProjectionWriter,
    OpenAICombinedAnalysisGateway,
    QuestionAnalysisInput,
    QuestionAnalysisInputLoader,
    TrainingCriteriaDraft,
    TrainingCriterionModule,
)

from .manager import JobCancellationRequested, JobContext


class _CancellationAwareGateway:
    def __init__(self, gateway: Any, context: JobContext) -> None:
        self.gateway = gateway
        self.context = context

    def analyze(self, *args, **kwargs):
        self.context.raise_if_cancelled()
        return self.gateway.analyze(*args, **kwargs)


def run_criterion_backfill_job(
    *,
    context: JobContext,
    question_bank_db_path: Path,
    data_root: Path,
    ai_service_factory: Callable[[], AITaggingService],
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
            version = (
                workspace.get("approved_version")
                if workspace["available"]
                else current
            )
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
            tagging_service = ai_service_factory()
            protocol_adapter = tagging_service._protocol_adapter()
            gateway = _CancellationAwareGateway(
                OpenAICombinedAnalysisGateway(
                    protocol_adapter=protocol_adapter,
                    model_name=tagging_service.model,
                ),
                context,
            )
            mapping_repository = FineTermCoreMappingRepository(db_path)
            install_fine_term_mapping_baseline(
                mapping_repository,
                build_fine_term_mapping_baseline(),
                actor_ref="system:taxonomy-baseline-v1",
            )
            analysis_module = CombinedQuestionAnalysisModule(
                repository=CombinedAnalysisRepository(db_path),
                gateway=gateway,
                tag_writer=ExistingTagProjectionWriter(
                    question_service=QuestionService(db_path),
                    tagging_service=tagging_service,
                ),
                evidence_writer=SolutionEvidenceProjectionWriter(
                    mapping_repository=mapping_repository,
                    evidence_repository=SolutionEvidenceRepository(db_path),
                ),
            )
            summary = analysis_module.analyze(
                operation_id=f"criterion-backfill:{run_id}",
                questions=tuple(pending),
                projection="training_criteria",
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
                    criterion_module.finish_backfill_item(
                        run_id=run_id,
                        question_id=question.question_id,
                        status="succeeded",
                        version_id=str(current["version_id"]),
                    )
                context.report(
                    0.1 + 0.85 * index / max(len(pending), 1),
                    "criterion_backfill",
                    f"{index}/{len(pending)}",
                )

    result = criterion_module.complete_backfill(run_id)
    context.report(1.0, "criterion_backfill", str(result["status"]))
    context.raise_if_cancelled()
    return {
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


__all__ = ["run_criterion_backfill_job"]
