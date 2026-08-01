from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import pytest

from backend.config_generation.normalization import (
    normalize_new_generated_config_payload,
    validate_generated_config,
)
from question_bank.database.schema import connect, initialize_database
from question_bank.models.question import QuestionCreate
from question_bank.models.tag_schema import TaggingContext
from question_bank.services.question_service import QuestionService
from question_bank.taxonomy.governance import TaxonomyGovernance
from question_bank.solution_evidence import (
    CoreResolution,
    FineTermCoreMappingRepository,
    QuestionSolutionEvidence,
    SolutionEvidenceProjectionWriter,
    SolutionEvidenceRepository,
    build_fine_term_mapping_baseline,
    install_fine_term_mapping_baseline,
)
from question_bank.training_criteria import (
    CombinedAnalysisRepository,
    CombinedQuestionAnalysisModule,
    ConfirmedQuestionAdoptionLink,
    ConfigQuestionAnalysisSource,
    DeferredCombinedAnalysisBundle,
    GatewayBatchResponse,
    InMemoryCombinedQuestionAnalysisModule,
    QuestionAnalysisInput,
    DeferredCombinedProjectionWriter,
    compose_generated_config_from_skeletons,
    grading_config_skeleton_from_solution_evidence,
    question_analysis_input_from_config_source,
    training_criteria_from_solution_evidence,
)


VOLUME_ID = "pep-7-up"


class Resolver:
    def resolve(self, fine_term_id: str) -> CoreResolution:
        if fine_term_id in {"kp_alg_linear_equation", "knowledge-fine-linear"}:
            return CoreResolution(
                status="resolved",
                stable_keys=("kp_alg_linear_equation",),
                reason="test",
            )
        return CoreResolution(status="unmapped", reason="test")


def _taxonomy_contract() -> dict[str, Any]:
    return {
        "taxonomy_revision": 2,
        "allowed_dimensions": ["knowledge"],
        "candidates": {
            "knowledge": [
                {
                    "id": "kp_alg_linear_equation",
                    "name": "一元一次方程",
                    "aliases": ["一次方程"],
                },
                {
                    "id": "knowledge-fine-linear",
                    "name": "移项解方程",
                    "aliases": [],
                },
            ]
        },
    }


def _question(
    question_id: int,
    *,
    source_ref: str = "Q1",
    question_type: str = "证明题",
    text: str = "证明等式成立。",
) -> QuestionAnalysisInput:
    return question_analysis_input_from_config_source(
        {
            "question_id": source_ref,
            "question_text": text,
            "answer_text": "由等式性质移项，结论成立。",
            "question_type": question_type,
        },
        question_id=question_id,
        curriculum_volume_id=VOLUME_ID,
        taxonomy_contract=_taxonomy_contract(),
    )


def _evidence_payload(
    question_id: int,
    *,
    invented_term: bool = False,
) -> dict[str, Any]:
    direct_id = "invented-term" if invented_term else "kp_alg_linear_equation"
    direct_name = "模型新造词" if invented_term else "一元一次方程"
    return {
        "schema_version": "question-solution-evidence-v1",
        "question_id": question_id,
        "parts": [
            {
                "part_id": "part-1",
                "label": "第1问",
                "response_mode": "process_required",
                "canonical_answer": "",
                "accepted_forms": [],
                "full_answer": "由等式性质移项并化简，得到结论。",
                "proof_obligations": ["说明所用等式性质", "写出结论"],
                "visual_requirements": [],
                "deduction_policy": ["缺少关键变形依据时该步骤未达成"],
                "allow_alternative_methods": True,
                "evidence_points": [
                    {
                        "evidence_point_id": "step-1",
                        "target": "正确移项",
                        "observable_evidence": "写出等价变形后的方程",
                        "fine_term_links": [
                            {
                                "fine_term_id": direct_id,
                                "fine_term_name": direct_name,
                                "role": "direct",
                            },
                            {
                                "fine_term_id": "knowledge-fine-linear",
                                "fine_term_name": "移项解方程",
                                "role": "supporting_prerequisite",
                            },
                        ],
                        "equivalent_rules": ["等价变形顺序可不同"],
                        "counterexamples": ["改变等号一侧符号但另一侧不变"],
                    }
                ],
            }
        ],
        "auxiliary_rules": ["符号等价即可"],
        "rationale": "按可观察解题过程拆分。",
        "confidence": 0.92,
    }


def _tag_payload() -> dict[str, Any]:
    return {
        "knowledge_points": ["一元一次方程"],
        "method_tags": ["方程思想"],
        "ability_tags": ["运算能力"],
        "math_model_tags": [],
        "special_type_tags": [],
        "difficulty": 3,
        "error_prone_points": ["移项符号错误"],
        "prerequisite_points": [],
        "textbook_chapters": [],
        "curriculum_sections": [],
        "suitable_student_level": "基础巩固",
        "canonical_knowledge_id": "kp_alg_linear_equation",
        "taxonomy_revision": 2,
        "proposed_tags": [],
        "reason": "考查解方程。",
        "confidence": 0.9,
    }


def _combined_payload(question_id: int, *, invented_term: bool = False) -> dict[str, Any]:
    return {
        "results": [
            {
                "question_id": question_id,
                "tag_analysis": _tag_payload(),
                "solution_evidence": _evidence_payload(
                    question_id,
                    invented_term=invented_term,
                ),
            }
        ]
    }


def _assert_no_score_fields(value: object) -> None:
    banned = {
        "score",
        "max_score",
        "min_score",
        "step_score",
        "total_score",
        "points_awarded",
        "score_awarded",
        "full_score",
    }
    if isinstance(value, Mapping):
        assert not (set(value) & banned)
        for child in value.values():
            _assert_no_score_fields(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_score_fields(child)


def test_solution_evidence_projects_role_preserving_union_and_complete_config() -> None:
    question = _question(1)
    evidence = QuestionSolutionEvidence.from_model_dict(
        _evidence_payload(1),
        question_id=1,
        source_content_hash=(
            __import__(
                "question_bank.training_criteria.analysis",
                fromlist=["solution_evidence_source_content_hash"],
            ).solution_evidence_source_content_hash(question)
        ),
        resolver=Resolver(),
    )

    classification = evidence.whole_question_classification()
    assert classification["direct_resolved_core_node_ids"] == [
        "kp_alg_linear_equation"
    ]
    assert classification["supporting_resolved_core_node_ids"] == [
        "kp_alg_linear_equation"
    ]
    assert classification["resolved_core_node_ids"] == [
        "kp_alg_linear_equation"
    ]
    config = grading_config_skeleton_from_solution_evidence(
        evidence,
        question_ref="Q1",
    )
    rubric = config["rubric_question"]
    answer = config["answer_key_question"]
    assert rubric["question_id"] == "Q1"
    assert rubric["parts"][0]["response_mode"] == "process_required"
    assert answer["parts"][0]["answer"].startswith("由等式性质")
    assert answer["parts"][0]["step_milestones"][0]["step_id"] == "step-1"
    serialized_config = json.dumps(config, ensure_ascii=False)
    assert "fine_term_links" not in serialized_config
    assert "whole_question_classification" not in serialized_config
    _assert_no_score_fields(config)
    criteria = training_criteria_from_solution_evidence(
        evidence,
        question=question,
    )
    assert [item.point_id for item in criteria.points] == ["step-1"]
    _assert_no_score_fields(criteria.to_dict())


def test_solution_evidence_skeletons_compose_into_current_config_contract() -> None:
    skeletons: list[dict[str, Any]] = []
    evidence_versions: list[str] = []
    for question_id in range(1, 7):
        question = _question(
            question_id,
            source_ref=f"Q{question_id}",
            text=f"证明第 {question_id} 个等式成立。",
        )
        evidence = QuestionSolutionEvidence.from_model_dict(
            _evidence_payload(question_id),
            question_id=question_id,
            source_content_hash=(
                __import__(
                    "question_bank.training_criteria.analysis",
                    fromlist=["solution_evidence_source_content_hash"],
                ).solution_evidence_source_content_hash(question)
            ),
            resolver=Resolver(),
        )
        evidence_versions.append(evidence.version_id)
        skeletons.append(
            grading_config_skeleton_from_solution_evidence(
                evidence,
                question_ref=f"Q{question_id}",
            )
        )

    payload = compose_generated_config_from_skeletons(
        skeletons,
        exam_title="证据生成测试卷",
    )

    assert payload["rubric"]["total_score"] == 0
    assert all(
        question["max_score"] == 0
        for question in payload["rubric"]["questions"]
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "solution-evidence-rubric-skeleton" not in serialized
    assert "source_content_hash" not in serialized
    assert "allocation_status" not in serialized
    assert [
        item["source_evidence_version_id"]
        for item in payload["rubric"]["questions"]
    ] == evidence_versions

    normalize_new_generated_config_payload(payload)
    validate_generated_config(payload)

    assert payload["rubric"]["total_score"] == 100
    assert sum(
        question["max_score"]
        for question in payload["rubric"]["questions"]
    ) == 100
    assert [
        item["source_evidence_version_id"]
        for item in payload["rubric"]["questions"]
    ] == evidence_versions


def test_fine_term_baseline_classifies_all_1121_without_forcing_procedures() -> None:
    baseline = build_fine_term_mapping_baseline()
    coverage = baseline.coverage()

    assert coverage == {
        "total": 1121,
        "by_status": {
            "resolved": 555,
            "ambiguous": 90,
            "unmapped": 476,
        },
        "by_term_kind": {
            "core_knowledge": 70,
            "fine_knowledge": 673,
            "procedure": 378,
        },
    }
    core = [item for item in baseline.entries if item.term_kind == "core_knowledge"]
    assert len(core) == 70
    assert all(item.status == "resolved" for item in core)
    fine = [item for item in baseline.entries if item.term_kind == "fine_knowledge"]
    assert sum(item.status == "resolved" for item in fine) == 485
    assert sum(item.status == "ambiguous" for item in fine) == 90
    assert sum(item.status == "unmapped" for item in fine) == 98
    procedures = [item for item in baseline.entries if item.term_kind == "procedure"]
    assert len(procedures) == 378
    assert all(item.status != "resolved" for item in procedures)
    assert sum(coverage["by_status"].values()) == 1121


def test_mapping_repository_requires_governed_confirmation_and_installs_baseline(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    repository = FineTermCoreMappingRepository(database)
    proposed = repository.propose(
        fine_term_id="knowledge-fine-test",
        stable_key="kp_alg_linear_equation",
        source_kind="model",
        source_reference="test:model-proposal",
        rationale="模型候选，仅供审核",
        model_name="synthetic-model",
        model_version="v1",
    )

    assert proposed["status"] == "suggested"
    assert repository.resolve("knowledge-fine-test").status == "ambiguous"
    with pytest.raises(ValueError):
        repository.confirm(
            proposed["mapping_id"],
            expected_revision=1,
            actor_kind="model",  # type: ignore[arg-type]
            actor_ref="model:auto",
            reason="不得自动确认",
        )
    repository.confirm(
        proposed["mapping_id"],
        expected_revision=1,
        actor_kind="teacher",
        actor_ref="teacher:test",
        reason="教师确认",
    )
    assert repository.resolve("knowledge-fine-test").status == "resolved"

    synthetic = repository.propose(
        fine_term_id="knowledge-fine-synthetic-upgrade",
        stable_key="kp_alg_linear_equation",
        source_kind="synthetic",
        source_reference="test:reviewed-synthetic",
        rationale="等待显式安装的确定性映射",
    )
    assert synthetic["status"] == "suggested"
    assert repository.install_synthetic_mappings(
        {
            "knowledge-fine-synthetic-upgrade": (
                "kp_alg_linear_equation",
            )
        },
        source_reference="test:reviewed-synthetic",
        actor_ref="system:reviewed-baseline-test",
    ) == 1
    with connect(database) as connection:
        revisions = [
            int(row["resulting_revision"])
            for row in connection.execute(
                """
                SELECT resulting_revision
                FROM fine_term_core_mapping_events
                WHERE mapping_id = ?
                ORDER BY resulting_revision
                """,
                (synthetic["mapping_id"],),
            ).fetchall()
        ]
    assert revisions == [1, 2]

    baseline = build_fine_term_mapping_baseline()
    installed = install_fine_term_mapping_baseline(
        repository,
        baseline,
        actor_ref="system:reviewed-baseline-test",
    )
    assert installed["total"] == 1121
    with connect(database) as connection:
        count = int(
            connection.execute(
                "SELECT COUNT(*) FROM fine_term_core_mappings WHERE status = 'confirmed'"
            ).fetchone()[0]
        )
    assert count >= installed["installed_mapping_count"]


class QueueGateway:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[int, ...]] = []

    def analyze(self, batch, **_kwargs):
        self.calls.append(batch.question_ids)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return GatewayBatchResponse(
            payload=response,
            model_name="synthetic-combined-model",
        )


def test_in_memory_bundle_checkpoints_round_trips_and_retries_only_failed() -> None:
    first = _question(1, source_ref="Q1", text="证明第一个等式成立。")
    second = _question(2, source_ref="Q2", text="证明第二个等式成立。")
    third = _question(3, source_ref="Q3", text="证明第三个等式成立。")
    sources = (
        ConfigQuestionAnalysisSource("Q1", first),
        ConfigQuestionAnalysisSource("Q2", second),
        ConfigQuestionAnalysisSource("Q3", third),
    )
    checkpoints: list[dict[str, Any]] = []
    gateway = QueueGateway(
        [
            _combined_payload(1),
            ValueError("synthetic rejected request"),
            ValueError("synthetic rejected request"),
        ]
    )
    module = InMemoryCombinedQuestionAnalysisModule(
        gateway=gateway,
        resolver=Resolver(),
    )

    partial = module.analyze(
        operation_id="config-source-analysis:test",
        curriculum_volume_id=VOLUME_ID,
        sources=sources,
        checkpoint=lambda bundle: checkpoints.append(bundle.to_dict()),
    )

    assert partial.status == "partial"
    assert [item.source_question_ref for item in partial.items] == ["Q1"]
    assert partial.failed_source_refs == ("Q2", "Q3")
    assert gateway.calls == [(1,), (2,), (3,)]
    assert checkpoints
    restored = DeferredCombinedAnalysisBundle.from_dict(
        partial.to_dict(),
        resolver=Resolver(),
    )
    assert restored.to_dict() == partial.to_dict()
    assert "image_paths" not in json.dumps(restored.to_dict(), ensure_ascii=False)

    selected_retry_gateway = QueueGateway([_combined_payload(2)])
    selected_retry = InMemoryCombinedQuestionAnalysisModule(
        gateway=selected_retry_gateway,
        resolver=Resolver(),
    ).retry_failed(
        restored,
        sources=sources,
        curriculum_volume_id=VOLUME_ID,
        retry_source_refs=("Q2",),
    )
    assert selected_retry.status == "partial"
    assert selected_retry_gateway.calls == [(2,)]
    assert [
        item.source_question_ref for item in selected_retry.items
    ] == ["Q1", "Q2"]
    assert selected_retry.failed_source_refs == ("Q3",)
    with pytest.raises(ValueError, match="non-empty"):
        InMemoryCombinedQuestionAnalysisModule(
            gateway=QueueGateway([]),
            resolver=Resolver(),
        ).retry_failed(
            selected_retry,
            sources=sources,
            curriculum_volume_id=VOLUME_ID,
            retry_source_refs=(),
        )
    with pytest.raises(ValueError, match="subset"):
        InMemoryCombinedQuestionAnalysisModule(
            gateway=QueueGateway([]),
            resolver=Resolver(),
        ).retry_failed(
            selected_retry,
            sources=sources,
            curriculum_volume_id=VOLUME_ID,
            retry_source_refs=("Q1",),
        )

    retry_gateway = QueueGateway([_combined_payload(3)])
    retried = InMemoryCombinedQuestionAnalysisModule(
        gateway=retry_gateway,
        resolver=Resolver(),
    ).retry_failed(
        selected_retry,
        sources=sources,
        curriculum_volume_id=VOLUME_ID,
    )
    assert retried.status == "succeeded"
    assert retry_gateway.calls == [(3,)]
    assert [item.source_question_ref for item in retried.items] == [
        "Q1",
        "Q2",
        "Q3",
    ]
    composed = retried.compose_generated_config(exam_title="重试完成测试卷")
    assert [
        item["question_id"] for item in composed["rubric"]["questions"]
    ] == ["Q1", "Q2", "Q3"]

    rebound = retried.get("Q1").bind_evidence(
        _question(99, source_ref="Q1", text="证明第一个等式成立。"),
        resolver=Resolver(),
    )
    assert rebound.question_id == 99
    tampered = copy.deepcopy(retried.to_dict())
    tampered["operation_id"] = "tampered"
    with pytest.raises(ValueError, match="content hash"):
        DeferredCombinedAnalysisBundle.from_dict(tampered, resolver=Resolver())
    unknown = copy.deepcopy(retried.to_dict())
    unknown["unknown_path"] = "C:/secret"
    with pytest.raises(ValueError, match="fields"):
        DeferredCombinedAnalysisBundle.from_dict(unknown, resolver=Resolver())


def test_in_memory_unknown_request_outcome_is_durable_and_never_normally_retried() -> None:
    source = ConfigQuestionAnalysisSource("Q1", _question(1, source_ref="Q1"))
    timeout_gateway = QueueGateway([TimeoutError("synthetic timeout after send")])

    uncertain = InMemoryCombinedQuestionAnalysisModule(
        gateway=timeout_gateway,
        resolver=Resolver(),
    ).analyze(
        operation_id="config-source-analysis:unknown-timeout",
        curriculum_volume_id=VOLUME_ID,
        sources=(source,),
    )

    assert uncertain.status == "needs_resolution"
    assert uncertain.failed_source_refs == ()
    assert uncertain.uncertain_source_refs == ("Q1",)
    assert uncertain.requests[-1].status == "outcome_unknown"

    retry_gateway = QueueGateway([_combined_payload(1)])
    unchanged = InMemoryCombinedQuestionAnalysisModule(
        gateway=retry_gateway,
        resolver=Resolver(),
    ).retry_failed(
        DeferredCombinedAnalysisBundle.from_dict(
            uncertain.to_dict(),
            resolver=Resolver(),
        ),
        sources=(source,),
        curriculum_volume_id=VOLUME_ID,
    )
    assert unchanged.to_dict() == uncertain.to_dict()
    assert retry_gateway.calls == []


def test_in_memory_crash_checkpoint_becomes_unknown_without_replaying_request() -> None:
    source = ConfigQuestionAnalysisSource("Q1", _question(1, source_ref="Q1"))
    checkpoints: list[DeferredCombinedAnalysisBundle] = []
    crashing_gateway = QueueGateway([KeyboardInterrupt()])

    with pytest.raises(KeyboardInterrupt):
        InMemoryCombinedQuestionAnalysisModule(
            gateway=crashing_gateway,
            resolver=Resolver(),
        ).analyze(
            operation_id="config-source-analysis:crash-window",
            curriculum_volume_id=VOLUME_ID,
            sources=(source,),
            checkpoint=checkpoints.append,
        )

    interrupted = checkpoints[-1]
    assert interrupted.requests[-1].status == "running"
    request_id = interrupted.requests[-1].request_id
    retry_gateway = QueueGateway([_combined_payload(1)])
    resumed_checkpoints: list[DeferredCombinedAnalysisBundle] = []
    resumed = InMemoryCombinedQuestionAnalysisModule(
        gateway=retry_gateway,
        resolver=Resolver(),
    ).resume_interrupted(
        interrupted,
        sources=(source,),
        curriculum_volume_id=VOLUME_ID,
        checkpoint=resumed_checkpoints.append,
    )

    assert resumed.status == "needs_resolution"
    assert resumed.uncertain_source_refs == ("Q1",)
    assert resumed.requests[-1].status == "outcome_unknown"
    assert resumed.requests[-1].request_id == request_id
    assert resumed_checkpoints == [resumed]
    assert retry_gateway.calls == []


def test_deferred_linked_adoption_allows_format_changes_but_rechecks_candidates(
    tmp_path: Path,
) -> None:
    source = _question(
        101,
        source_ref="Q1",
        text="证明等式成立。",
    )
    bundle = InMemoryCombinedQuestionAnalysisModule(
        gateway=QueueGateway([_combined_payload(101)]),
        resolver=Resolver(),
    ).analyze(
        operation_id="config-source-analysis:linked-adoption",
        curriculum_volume_id=VOLUME_ID,
        sources=(ConfigQuestionAnalysisSource("Q1", source),),
    )
    item = bundle.get("Q1")
    assert item.curriculum_volume_id == VOLUME_ID
    assert item.knowledge_candidates[0].fine_term_id == "kp_alg_linear_equation"

    database = tmp_path / "question-bank.db"
    initialize_database(database)
    actual_id = QuestionService(database).add_question(
        QuestionCreate(
            question_number="1",
            question_text="证明：等式成立。\n（排版整理后）",
            answer_text="由等式性质移项，结论成立。",
            question_type="证明题",
        )
    )
    actual = replace(
        _question(
            actual_id,
            source_ref="Q1",
            text="证明：等式成立。\n（排版整理后）",
        ),
        taxonomy_contract={},
    )
    with pytest.raises(ValueError, match="source content changed"):
        item.bind_evidence(actual, resolver=Resolver())

    writer = DeferredCombinedProjectionWriter(
        tag_writer=SuccessfulTagWriter(),
        mapping_repository=FineTermCoreMappingRepository(database),
        evidence_repository=SolutionEvidenceRepository(database),
    )
    link = ConfirmedQuestionAdoptionLink(
        source_question_ref="Q1",
        bank_question_id=actual_id,
        confirmed_by="test:confirmed-import-link",
    )
    without_volume = replace(
        actual,
        tagging_context=replace(
            actual.tagging_context,
            curriculum_volume_id=None,
        ),
    )
    with pytest.raises(ValueError, match="curriculum volume"):
        item.bind_linked_evidence(
            without_volume,
            link=link,
            resolver=Resolver(),
        )
    result = writer.adopt_linked(item, question=actual, link=link)

    assert result["adoption_mode"] == "confirmed_link"
    assert result["tag_status"] == "succeeded"
    assert result["evidence_status"] == "succeeded"
    latest = SolutionEvidenceRepository(database).latest(actual_id)
    assert latest is not None
    assert result["source_evidence_version_id"] == latest["evidence_version_id"]
    assert latest["source_content_hash"] != item.source_content_hash
    with pytest.raises(ValueError, match="source reference"):
        writer.adopt_linked(
            item,
            question=actual,
            link=ConfirmedQuestionAdoptionLink(
                source_question_ref="Q2",
                bank_question_id=actual_id,
                confirmed_by="test:wrong-link",
            ),
        )


class SuccessfulTagWriter:
    def write(self, *_args, **_kwargs) -> Mapping[str, Any]:
        return {"saved": True}


def test_combined_tag_succeeds_when_hallucinated_evidence_term_is_rejected(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    question_id = QuestionService(database).add_question(
        QuestionCreate(
            question_number="1",
            question_text="证明等式成立。",
            answer_text="由等式性质移项，结论成立。",
            question_type="证明题",
        )
    )
    question = _question(question_id)
    gateway = QueueGateway(
        [_combined_payload(question_id, invented_term=True)]
    )
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=SuccessfulTagWriter(),
        evidence_writer=SolutionEvidenceProjectionWriter(
            mapping_repository=FineTermCoreMappingRepository(database),
            evidence_repository=SolutionEvidenceRepository(database),
        ),
    )

    result = module.analyze(
        operation_id="combined-v3:hallucinated-evidence",
        questions=(question,),
        projection="both",
    )

    assert result["items"][0]["tag_status"] == "succeeded"
    assert result["items"][0]["criteria_status"] == "failed"
    assert result["items"][0]["criteria_error_category"] == "evidence_validation"
    assert SolutionEvidenceRepository(database).latest(question_id) is None


def test_evidence_writer_converges_point_text_against_full_vocabulary_and_audits(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    mapping_repository = FineTermCoreMappingRepository(database)
    install_fine_term_mapping_baseline(
        mapping_repository,
        build_fine_term_mapping_baseline(),
        actor_ref="system:test-baseline",
    )
    governance = TaxonomyGovernance(state_path=tmp_path / "taxonomy-state.json")
    question_id = QuestionService(database).add_question(
        QuestionCreate(
            question_number="1",
            question_text="解一元一次方程。",
            answer_text="移项并合并同类项。",
            question_type="解答题",
        )
    )
    question = _question(question_id)
    payload = _evidence_payload(question_id)
    point = payload["parts"][0]["evidence_points"][0]
    point["fine_term_links"] = point["fine_term_links"][:1]
    point["observable_evidence"] = "先合并同类项，再写出等价变形后的方程。"
    point["fine_term_links"].append(
        {
            "fine_term_id": "invented-local-observation",
            "fine_term_name": "全新代数平衡术",
            "role": "supporting_prerequisite",
        }
    )
    writer = SolutionEvidenceProjectionWriter(
        mapping_repository=mapping_repository,
        evidence_repository=SolutionEvidenceRepository(database),
        taxonomy_governance=governance,
    )

    evidence = writer.write(
        question,
        payload,
        model_name="synthetic-model",
        operation_id="combined-v3:local-convergence",
    )

    links = evidence.parts[0].evidence_points[0].fine_term_links
    assert ("kp_alg_linear_equation", "direct") in {
        (item.fine_term_id, item.role) for item in links
    }
    assert ("kp_alg_polynomial", "supporting_prerequisite") in {
        (item.fine_term_id, item.role) for item in links
    }
    assert all(item.fine_term_name != "全新代数平衡术" for item in links)
    audit = writer.audit_summary(
        "combined-v3:local-convergence",
        (question.question_id,),
    )
    assert audit["retrieval_misses"] == [
        {
            "dimension": "knowledge",
            "submitted_name": "整式运算",
            "canonical_id": "kp_alg_polynomial",
            "canonical_name": "整式运算",
            "source_field": "knowledge_points",
        }
    ]
    assert len(audit["proposals"]) == 1
    assert audit["proposals"][0]["proposed_name"] == "全新代数平衡术"
    assert governance.list_proposals(status="pending")["counts"] == {"pending": 1}
