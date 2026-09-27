from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from backend.config_workspace.publish import load_editor_config
from backend.config_workspace.deferred_analysis import DeferredAnalysisArtifactStore
from backend.jobs.manager import JobCancellationRequested, JobContext
from backend.jobs.question_bank_sync import (
    StaleQuestionBankSyncError,
    _adopt_deferred_analysis_with_links,
    run_deferred_question_bank_intake,
    run_session_question_bank_sync_job,
)
from backend.jobs.store import (
    JobStore,
    QuestionBankSyncRequestTokenConflictError,
)
from db_manager import DBManager
from question_bank.database.schema import connect, initialize_database
from tests.current_knowledge_support import install_current_knowledge
from question_bank.services.question_write_service import QuestionBankWriteService
from tests.question_bank_support import QuestionBankTestStore
from question_bank.services.source_question_link_service import (
    SourceQuestionLinkService,
)
from question_bank.solution_evidence import SolutionEvidenceRepository
from question_bank.solution_evidence.contracts import QuestionSolutionEvidence
from question_bank.training_criteria import (
    ConfigQuestionAnalysisSource,
    DeferredAnalysisFailure,
    DeferredCombinedAnalysisBundle,
    GatewayBatchResponse,
    InMemoryCombinedQuestionAnalysisModule,
    UnmappedFineTermResolver,
    question_analysis_input_from_config_source,
    reused_analysis_item,
)
from question_bank.training_criteria.analysis import (
    solution_evidence_source_content_hash,
)


LEGACY_CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "question_bank"
    / "taxonomy"
    / "catalogs"
    / "tag_vocabulary_v2.json"
)


def test_stale_job_cannot_overwrite_a_newer_sync_owner(
    tmp_path: Path,
) -> None:
    db, session_id, _source, source_sha256, _revision = _configured_session(tmp_path)
    store = JobStore(db.db_path)
    newer_revision = "d" * 64
    db.update_question_bank_sync_state(
        session_id,
        state="running",
        details={
            "config_revision": newer_revision,
            "job_id": 99,
            "source_paper_sha256": source_sha256,
            "stage": "tagging",
        },
    )

    assert (
        store.transition_question_bank_sync_state_if_owned(
            session_id=session_id,
            job_id=98,
            source_paper_sha256=source_sha256,
            config_revision="e" * 64,
            state="not_started",
            details={"stage": "stale"},
        )
        is False
    )

    unchanged = db.get_grading_session(session_id)
    assert unchanged is not None
    assert unchanged["question_bank_sync_state"] == "running"
    assert json.loads(unchanged["question_bank_sync_details_json"]) == {
        "config_revision": newer_revision,
        "job_id": 99,
        "source_paper_sha256": source_sha256,
        "stage": "tagging",
    }


def _configured_session(
    tmp_path: Path,
) -> tuple[DBManager, int, Path, str, str]:
    data_root = tmp_path / "data"
    databases = data_root / "databases"
    databases.mkdir(parents=True)
    db = DBManager(databases / "grading.db")
    db.initialize()
    rubric_path = data_root / "config" / "rubric.json"
    answer_path = data_root / "config" / "answer.json"
    rubric_path.parent.mkdir(parents=True)
    rubric_path.write_text(
        json.dumps(
            {
                "total_score": 100,
                "questions": [
                    {
                        "question_id": "Q1",
                        "question_type": "choice",
                        "max_score": 100,
                        "parts": [
                            {
                                "part_id": "Q1",
                                "part_score": 100,
                                "response_mode": "exact_objective",
                                "steps": [
                                    {
                                        "step_id": "S1",
                                        "step_score": 100,
                                        "core_goal": "选择正确选项",
                                        "required_elements": ["B"],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    answer_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "canonical_answer": "B",
                        "parts": [{"part_id": "Q1", "answer": "B"}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    source = data_root / "question_bank" / "raw_papers" / "paper.docx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"controlled source paper")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    session_id = db.create_grading_session(
        "同步测试",
        str(rubric_path),
        str(answer_path),
        source_paper_path=str(source),
        source_paper_sha256=source_sha256,
    )
    revision = load_editor_config(db, session_id).revision
    return db, session_id, source, source_sha256, revision


def _deferred_sync_contract() -> dict[str, Any]:
    return {
        "taxonomy_revision": 2,
        "allowed_dimensions": ["knowledge", "ability"],
        "candidates": {
            "knowledge": [
                {
                    "id": "kp_alg_linear_equation",
                    "name": "一元一次方程",
                    "aliases": ["一次方程"],
                }
            ],
            "ability": [
                {
                    "id": "ability-calculation",
                    "name": "运算能力",
                    "aliases": [],
                }
            ],
        },
    }


def _deferred_sync_result(question_id: int) -> dict[str, Any]:
    return {
        "results": [
            {
                "question_id": question_id,
                "tag_analysis": {
                    "method_tags": [],
                    "thought_tags": ["方程思想"],
                    "ability_tags": ["运算能力"],
                    "math_model_tags": [],
                    "special_type_tags": [],
                    "difficulty": 3,
                    "predicted_error_patterns": [],
                    "part_features": [
                        {
                            "part_id": "part-1",
                            "part_label": "第1题",
                            "solo": 1,
                            "reasoning": 0,
                            "computation": 1,
                            "context": 0,
                            "hidden": 0,
                            "cases": 0,
                            "param_dynamic": 0,
                            "trap": 0,
                            "knowledge": 0,
                            "context_kind": "无情境",
                            "evidence": "合成逐小问特征。",
                        }
                    ],
                    "taxonomy_revision": 2,
                    "proposed_tags": [],
                    "reason": "合成延期标签。",
                    "confidence": 0.95,
                },
                "solution_evidence": {
                    "schema_version": "question-solution-evidence-v1",
                    "question_id": question_id,
                    "parts": [
                        {
                            "part_id": "part-1",
                            "label": "第1题",
                            "response_mode": "exact_objective",
                            "canonical_answer": "B",
                            "accepted_forms": ["B"],
                            "full_answer": "B",
                            "proof_obligations": [],
                            "visual_requirements": [],
                            "deduction_policy": ["答案不等价则未达成"],
                            "allow_alternative_methods": False,
                            "evidence_points": [
                                {
                                    "evidence_point_id": "answer-1",
                                    "target": "选出正确答案",
                                    "observable_evidence": "作答为 B",
                                    "fine_term_links": [
                                        {
                                            "fine_term_id": "kp_alg_linear_equation",
                                            "fine_term_name": "一元一次方程",
                                            "role": "direct",
                                        }
                                    ],
                                    "equivalent_rules": [],
                                    "counterexamples": ["作答为 A"],
                                }
                            ],
                        }
                    ],
                    "auxiliary_rules": [],
                    "rationale": "合成延期证据。",
                    "confidence": 0.95,
                },
            }
        ]
    }


class _DeferredSyncGateway:
    def __init__(
        self,
        *,
        invented_term: bool = False,
        empty_links: bool = False,
    ) -> None:
        self.calls: list[tuple[int, ...]] = []
        self.invented_term = bool(invented_term)
        self.empty_links = bool(empty_links)

    def analyze(self, batch: Any, **_kwargs: Any) -> GatewayBatchResponse:
        self.calls.append(batch.question_ids)
        payload = _deferred_sync_result(batch.question_ids[0])
        if self.invented_term:
            result = payload["results"][0]
            result["tag_analysis"]["knowledge_points"] = ["模型新造知识"]
            result["tag_analysis"]["canonical_knowledge_id"] = ""
            for field in (
                "method_tags",
                "thought_tags",
                "ability_tags",
                "math_model_tags",
                "special_type_tags",
                "textbook_chapters",
                "curriculum_sections",
            ):
                result["tag_analysis"][field] = []
            point = result["solution_evidence"]["parts"][0]["evidence_points"][0]
            point["fine_term_links"] = [
                {
                    "fine_term_id": "invented-model-term",
                    "fine_term_name": "模型新造知识",
                    "role": "direct",
                }
            ]
            point["target"] = "选出正确答案"
            point["observable_evidence"] = "作答为 B"
        elif self.empty_links:
            point = payload["results"][0]["solution_evidence"]["parts"][0][
                "evidence_points"
            ][0]
            point["fine_term_links"] = []
            point["target"] = "选出正确答案"
            point["observable_evidence"] = "作答为 B"
        return GatewayBatchResponse(
            payload=payload,
            model_name="synthetic-combined-v3",
        )


class _PassThroughTaxonomyGovernance:
    def snapshot(self) -> dict[str, Any]:
        return {
            "terms_by_dimension": {
                "knowledge": [
                    {
                        "id": "kp_alg_linear_equation",
                        "name": "一元一次方程",
                        "aliases": ["一次方程"],
                    }
                ]
            }
        }

    def resolve_term(
        self,
        dimension: str,
        name: str,
    ) -> dict[str, Any] | None:
        if dimension != "knowledge" or name not in {
            "一元一次方程",
            "一次方程",
        }:
            return None
        return {
            "id": "kp_alg_linear_equation",
            "name": "一元一次方程",
            "aliases": ["一次方程"],
        }

    def constrain(
        self,
        raw_analysis: dict[str, Any],
        context: object = None,
    ) -> dict[str, Any]:
        del context
        accepted_fields = {
            field: list(raw_analysis.get(field) or [])
            for field in (
                "knowledge_points",
                "method_tags",
                "thought_tags",
                "ability_tags",
                "math_model_tags",
                "special_type_tags",
                "textbook_chapters",
            )
        }
        return {
            "accepted_fields": accepted_fields,
            "accepted_terms": {
                "knowledge": [
                    {
                        "id": "kp_alg_linear_equation",
                        "name": "一元一次方程",
                    }
                ]
            },
            "proposals": [],
            "retrieval_misses": [],
            "taxonomy_revision": 2,
            "status": "complete",
            "notes": [],
        }


class _DeferredAdoptionTaggingService:
    model = "synthetic-combined-v3"

    def __init__(self, taxonomy_governance: Any | None = None) -> None:
        self.taxonomy_governance = (
            taxonomy_governance or _PassThroughTaxonomyGovernance()
        )

    def taxonomy_contracts(
        self,
        contexts: dict[int, object],
    ) -> dict[int, dict[str, Any]]:
        return {question_id: _deferred_sync_contract() for question_id in contexts}


def _run_deferred_adoption(
    tmp_path: Path,
    *,
    invented_term: bool = False,
    empty_links: bool = False,
    analysis_governance: Any | None = None,
    adoption_governance: Any | None = None,
    prepublish_intake: bool = False,
    repeat_prepublish_intake: bool = False,
    partial_analysis: bool = False,
    cancel_after_import: bool = False,
    intake_asset_overrides: list[dict[str, Any]] | None = None,
    intake_type_overrides: dict[str, str] | None = None,
    captured_import_payload: dict[str, Any] | None = None,
) -> tuple[
    dict[str, object],
    _DeferredSyncGateway,
    list[int],
    Path,
    Path,
]:
    db, session_id, _source, source_sha256, revision = _configured_session(tmp_path)
    question_bank_db = tmp_path / "data" / "databases" / "question_bank.db"
    initialize_database(question_bank_db)
    install_current_knowledge(question_bank_db)
    source_question = question_analysis_input_from_config_source(
        {
            "question_id": "Q1",
            "question_text": "1 + 1 = ?",
            "answer_text": "B",
            "question_type": "choice",
        },
        question_id=1,
        curriculum_volume_id="bnu24-math-g7-upper",
        taxonomy_contract=_deferred_sync_contract(),
    )
    gateway = _DeferredSyncGateway(
        invented_term=invented_term,
        empty_links=empty_links,
    )
    bundle = InMemoryCombinedQuestionAnalysisModule(
        gateway=gateway,
        taxonomy_governance=analysis_governance,
    ).analyze(
        operation_id="config:synthetic:deferred-adoption",
        curriculum_volume_id="bnu24-math-g7-upper",
        sources=(ConfigQuestionAnalysisSource("Q1", source_question),),
    )
    if partial_analysis:
        bundle = DeferredCombinedAnalysisBundle(
            operation_id=bundle.operation_id,
            curriculum_volume_id=bundle.curriculum_volume_id,
            items=bundle.items,
            failures=(
                DeferredAnalysisFailure(
                    source_question_ref="Q2",
                    analysis_question_id=2,
                    request_id="d" * 64,
                    batch_hash="e" * 64,
                    category="model",
                ),
            ),
            requests=bundle.requests,
            source_fingerprints=(
                *bundle.source_fingerprints,
                ("Q2", "f" * 64),
            ),
        )
    artifact_id = "a" * 32
    source_id = "b" * 32
    source_revision = "c" * 64
    artifact_root = tmp_path / "analysis-artifacts"
    artifact = DeferredAnalysisArtifactStore(artifact_root).save(
        artifact_id=artifact_id,
        session_id=session_id,
        source_id=source_id,
        source_revision=source_revision,
        curriculum_volume_id="bnu24-math-g7-upper",
        bundle=bundle,
    )
    store = JobStore(db.db_path)
    job = store.create_job(
        "question_bank_sync",
        {
            "session_id": session_id,
            "mode": "sync",
            "config_revision": revision,
            "source_paper_sha256": source_sha256,
            "curriculum_volume_id": "bnu24-math-g7-upper",
            "analysis_artifact_id": artifact.artifact_id,
            "analysis_artifact_hash": artifact.content_hash,
            "analysis_source_id": source_id,
            "analysis_source_revision": source_revision,
        },
    )
    assert store.mark_running(job.id)
    context = JobContext(
        job_id=job.id,
        job_type=job.job_type,
        payload=job.payload,
        store=store,
    )
    imported_ids: list[int] = []

    def import_runner(**_kwargs: Any) -> dict[str, object]:
        if captured_import_payload is not None:
            child = _kwargs.get("context")
            captured_import_payload.update(dict(getattr(child, "payload", {})))
        if imported_ids:
            return {
                "outcome": "complete",
                "successful_question_ids": [imported_ids[0]],
                "failed_question_ids": [],
                "failed_count": 0,
                "retryable": False,
            }
        with connect(question_bank_db) as connection:
            numbers = ("1", "2") if partial_analysis else ("1",)
            question_ids = [
                int(
                    connection.execute(
                        """
                        INSERT INTO questions (
                            question_number, question_type, question_text,
                            answer_text, source_file
                        ) VALUES (?, 'choice', ?, 'B', 'paper.docx')
                        """,
                        (number, f"{number} + 1 = ?"),
                    ).lastrowid
                )
                for number in numbers
            ]
        imported_ids.extend(question_ids)
        if cancel_after_import:
            assert store.request_cancel(job.id)
        return {
            "outcome": "complete",
            "successful_question_ids": question_ids,
            "failed_question_ids": [],
            "failed_count": 0,
            "retryable": False,
        }

    def unexpected_tagging_runner(**_kwargs: Any) -> dict[str, object]:
        pytest.fail("deferred adoption must not call the tagging model runner")

    governance = adoption_governance or _PassThroughTaxonomyGovernance()

    if prepublish_intake:
        try:
            result = run_deferred_question_bank_intake(
                context=context,
                session_id=session_id,
                artifact=artifact,
                source_filename="paper.docx",
                source_content=b"controlled source paper",
                question_bank_db_path=question_bank_db,
                data_root=tmp_path / "data",
                asset_overrides=intake_asset_overrides,
                type_overrides=intake_type_overrides,
                question_import_runner=import_runner,
                ai_service_factory=lambda: _DeferredAdoptionTaggingService(governance),
                taxonomy_governance=governance,
            )
        except JobCancellationRequested:
            if not cancel_after_import:
                raise
            result = {"outcome": "cancelled"}
        if repeat_prepublish_intake:
            result = run_deferred_question_bank_intake(
                context=context,
                session_id=session_id,
                artifact=artifact,
                source_filename="paper.docx",
                source_content=b"controlled source paper",
                question_bank_db_path=question_bank_db,
                data_root=tmp_path / "data",
                asset_overrides=intake_asset_overrides,
                type_overrides=intake_type_overrides,
                question_import_runner=import_runner,
                ai_service_factory=lambda: _DeferredAdoptionTaggingService(governance),
                taxonomy_governance=governance,
            )
    else:
        result = run_session_question_bank_sync_job(
            context=context,
            grading_db=db,
            question_bank_db_path=question_bank_db,
            data_root=tmp_path / "data",
            write_service=QuestionBankWriteService(
                question_bank_db,
                data_root=tmp_path / "data",
            ),
            question_import_runner=import_runner,
            tagging_sync_runner=unexpected_tagging_runner,
            ai_service_factory=lambda: _DeferredAdoptionTaggingService(governance),
            taxonomy_governance=governance,
            analysis_artifact_root=artifact_root,
        )

    return (
        result,
        gateway,
        imported_ids,
        question_bank_db,
        artifact_root / f"deferred_question_analysis_{artifact.artifact_id}.json",
    )


def test_sync_adopts_deferred_tags_and_evidence_without_tagging_model(
    tmp_path: Path,
) -> None:
    result, gateway, imported_ids, question_bank_db, artifact_path = (
        _run_deferred_adoption(tmp_path)
    )

    assert result["outcome"] == "complete", result
    assert result["tagged_count"] == 1
    assert result["evidence_count"] == 1
    assert result["criteria_count"] == 1
    assert result["taxonomy_review_count"] == 0
    assert result["taxonomy_review_question_ids"] == []
    assert result["retryable"] is False
    assert result["analysis_artifact_consumed"] is True
    assert gateway.calls == [(1,)]
    saved = QuestionBankTestStore(question_bank_db).get_question(imported_ids[0])
    assert saved is not None
    # 整题知识点改由判定点关联派生，模型标签只带能力等维度。
    assert any(
        tag["tag_type"] == "ability" and tag["tag_value"] == "运算能力"
        for tag in saved["tags"]
    )
    assert not any(
        tag["tag_type"] == "knowledge_point" and tag["tag_value"] == "一元一次方程"
        for tag in saved["tags"]
    )
    evidence = SolutionEvidenceRepository(question_bank_db).latest(imported_ids[0])
    assert evidence is not None
    assert (
        evidence["evidence"]["parts"][0]["evidence_points"][0]["fine_term_links"][0][
            "role"
        ]
        == "direct"
    )
    with connect(question_bank_db) as connection:
        criterion = connection.execute(
            """
            SELECT source_kind, criteria_json
            FROM training_criterion_versions
            WHERE question_id = ?
            """,
            (imported_ids[0],),
        ).fetchone()
    assert criterion is not None
    assert criterion["source_kind"] == "confirmed_rubric_adapter"
    criterion_payload = json.loads(str(criterion["criteria_json"]))
    assert criterion_payload["points"][0]["target"] == "给出正确或等价答案"
    assert criterion_payload["points"][0]["observable_evidence"] == "B"
    assert not artifact_path.exists()


def test_score_pending_intake_imports_tags_and_evidence_without_grading_links(
    tmp_path: Path,
) -> None:
    result, gateway, imported_ids, question_bank_db, artifact_path = (
        _run_deferred_adoption(tmp_path, prepublish_intake=True)
    )

    assert result["outcome"] == "complete", result
    assert result["imported_count"] == 1
    assert result["tagged_count"] == 1
    assert result["evidence_count"] == 1
    assert result["linked_count"] == 0
    assert result["provisional_match_count"] == 1
    assert result["config_link_pending"] is True
    assert SourceQuestionLinkService(question_bank_db).list_links() == []
    saved = QuestionBankTestStore(question_bank_db).get_question(imported_ids[0])
    assert saved is not None
    # 整题知识点改由判定点关联派生，模型标签只带能力等维度。
    assert any(
        tag["tag_type"] == "ability" and tag["tag_value"] == "运算能力"
        for tag in saved["tags"]
    )
    assert not any(
        tag["tag_type"] == "knowledge_point" and tag["tag_value"] == "一元一次方程"
        for tag in saved["tags"]
    )
    assert SolutionEvidenceRepository(question_bank_db).latest(imported_ids[0])
    assert gateway.calls == [(1,)]
    assert artifact_path.exists()


def test_partial_analysis_intake_keeps_paper_and_tags_only_successful_questions(
    tmp_path: Path,
) -> None:
    result, gateway, imported_ids, question_bank_db, artifact_path = (
        _run_deferred_adoption(
            tmp_path,
            prepublish_intake=True,
            partial_analysis=True,
        )
    )

    assert result["outcome"] == "partial", result
    assert result["imported_count"] == 2
    assert result["tagged_count"] == 1
    assert result["evidence_count"] == 1
    assert result["analysis_incomplete_count"] == 1
    assert result["failed_count"] == 1
    assert len(imported_ids) == 2
    assert gateway.calls == [(1,)]
    assert artifact_path.exists()
    assert QuestionBankTestStore(question_bank_db).get_question(imported_ids[0])["tags"]
    assert (
        QuestionBankTestStore(question_bank_db).get_question(imported_ids[1])["tags"]
        == []
    )


_ANNOTATION_KEYS = {
    "evidence_snapshot_ref",
    "source_evidence_version_id",
    "graph_release_id",
    "evidence_part_id",
    "evidence_point_ids",
}


def _strip_snapshot_annotations(value: Any) -> Any:
    """递归删除 §7.1/§7.2 的快照注解键，只保留评分语义内容。"""
    if isinstance(value, dict):
        return {
            key: _strip_snapshot_annotations(item)
            for key, item in value.items()
            if key not in _ANNOTATION_KEYS
        }
    if isinstance(value, list):
        return [_strip_snapshot_annotations(item) for item in value]
    return value
