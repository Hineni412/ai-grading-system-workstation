from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

import question_bank.services.question_write_service as question_write_module
from backend.llm.diagnostics import sanitize_request_payload
from question_bank.current_knowledge import CurrentKnowledgeResolver
from question_bank.database.schema import connect, initialize_database
from question_bank.models.tag_schema import TagAnalysis, TaggingContext
from question_bank.services.question_frequency_service import QuestionFrequencyService
from question_bank.training_criteria import (
    AnalysisConflictError,
    CombinedAnalysisRepository,
    CombinedQuestionAnalysisModule,
    ExistingTagProjectionWriter,
    GatewayBatchResponse,
    GatewayUsage,
    OpenAICombinedAnalysisGateway,
    ProjectionValidationError,
    QuestionAnalysisImage,
    QuestionAnalysisInput,
    QuestionAnalysisInputLoader,
    QuestionAnalysisWorkItem,
    TagOnlyV1ResultAdapter,
    TrainingCriteriaDraft,
    combined_response_format,
    criteria_from_confirmed_rubric,
    plan_analysis_batches,
)
from question_bank.services.question_write_service import QuestionBankWriteService
from tests.current_knowledge_support import install_current_knowledge


FIXTURE = (
    Path(__file__).parent / "fixtures" / "p4_00_gold_set.json"
)


class FakeTagWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    def write(
        self,
        question: QuestionAnalysisInput,
        payload: Mapping[str, Any],
        *,
        model_name: str,
        operation_id: str,
    ) -> Mapping[str, Any]:
        analysis = TagAnalysis.from_dict(dict(payload))
        if not analysis.knowledge_points:
            raise ValueError("synthetic tag projection is incomplete")
        self.calls.append((question.question_id, operation_id))
        return {
            "schema_version": "tag-only-v1",
            "analysis": analysis.to_dict(),
            "model_name": model_name,
        }


class QueueGateway:
    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def analyze(
        self,
        batch,
        *,
        projection,
        operation_id,
        request_id,
    ) -> GatewayBatchResponse:
        self.calls.append(
            {
                "question_ids": batch.question_ids,
                "projection": projection,
                "operation_id": operation_id,
                "request_id": request_id,
            }
        )
        return GatewayBatchResponse(
            payload=self.responses.pop(0),
            model_name="synthetic-model",
            usage=GatewayUsage(100, 50, 150),
            latency_ms=12,
        )


class RaisingGateway:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    def analyze(self, *_args, **_kwargs):
        self.calls += 1
        raise self.error


class SyntheticStatusError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def _seed_questions(database: Path, count: int = 8) -> None:
    initialize_database(database)
    with connect(database) as connection:
        paper_id = connection.execute(
            """
            INSERT INTO papers (title, import_status)
            VALUES ('P4-09 合成试卷', 'completed')
            """
        ).lastrowid
        connection.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type,
                question_text, answer_text
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    index,
                    paper_id,
                    str(index),
                    "计算题",
                    f"合成题目 {index}",
                    f"合成答案 {index}",
                )
                for index in range(1, count + 1)
            ),
        )


def _question(
    question_id: int,
    *,
    question_type: str = "计算题",
    has_images: bool = False,
    images: tuple[QuestionAnalysisImage, ...] = (),
    text: str = "解方程 x + 1 = 2。",
) -> QuestionAnalysisInput:
    return QuestionAnalysisInput(
        question_id=question_id,
        tagging_context=TaggingContext(
            question_text=text,
            answer_text="x=1",
            question_number=str(question_id),
            question_type=question_type,
            has_images=has_images,
        ),
        rich_question_blocks=({"text": "保留上标与表格语义"},),
        images=images,
        taxonomy_contract={
            "taxonomy_revision": 7,
            "allowed_dimensions": ["knowledge"],
            "candidates": {
                "knowledge": [
                    {
                        "id": "kp_alg_linear_equation",
                        "name": "一元一次方程",
                    }
                ]
            },
        },
    )


def _tag_payload() -> dict[str, Any]:
    return {
        "knowledge_points": ["一元一次方程"],
        "method_tags": [],
        "thought_tags": ["方程思想"],
        "ability_tags": ["运算能力"],
        "math_model_tags": [],
        "special_type_tags": [],
        "difficulty": 3,
        "error_prone_points": ["运算化简错误"],
        "prerequisite_points": [],
        "textbook_chapters": [],
        "curriculum_sections": [],
        "suitable_student_level": "",
        "canonical_knowledge_id": "kp_alg_linear_equation",
        "taxonomy_revision": 7,
        "proposed_tags": [],
        "reason": "合成标签理由",
        "confidence": 0.9,
    }


def _criteria_payload(
    question_id: int,
    *,
    points: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "training-criteria-draft-v1",
        "question_id": question_id,
        "points": points
        or [
            {
                "point_id": "p-answer",
                "target": "得到未知数的值",
                "observable_evidence": "写出 x=1",
                "equivalent_rules": ["1=x"],
                "counterexamples": [],
            }
        ],
        "auxiliary_rules": ["过程清晰但不计入判定点分母"],
        "rationale": "合成判定点",
        "confidence": 0.9,
    }


def test_question_taxonomy_snapshot_is_bound_and_deeply_immutable() -> None:
    contract = {
        "taxonomy_revision": 7,
        "candidate_fingerprint": "snapshot-7",
        "allowed_term_ids": {"knowledge": ["kp-1"]},
        "candidates": {"knowledge": [{"id": "kp-1", "name": "一次方程"}]},
    }
    question = QuestionAnalysisInput(
        question_id=17,
        tagging_context=TaggingContext(question_text="解方程。", answer_text="x=1"),
        taxonomy_contract=contract,
    )

    contract["allowed_term_ids"]["knowledge"].append("kp-2")
    exported = question.taxonomy_snapshot.to_dict()
    exported["allowed_term_ids"]["knowledge"].append("kp-3")

    assert question.taxonomy_snapshot.question_id == 17
    assert question.taxonomy_snapshot.taxonomy_revision == 7
    assert question.taxonomy_snapshot.candidate_fingerprint == "snapshot-7"
    assert question.taxonomy_snapshot.allowed_term_ids("knowledge") == ("kp-1",)


def test_mixed_projection_work_is_grouped_behind_one_module_call(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database, count=2)
    gateway = QueueGateway(
        [
            {
                "results": [
                    {
                        "question_id": 1,
                        "tag_analysis": _tag_payload(),
                        "training_criteria": _criteria_payload(1),
                    }
                ]
            },
            {
                "results": [
                    {"question_id": 2, "tag_analysis": _tag_payload()}
                ]
            },
        ]
    )
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=FakeTagWriter(),
    )

    outcome = module.analyze_work_items(
        operation_id="mixed-analysis",
        work_items=(
            QuestionAnalysisWorkItem(question=_question(1)),
            QuestionAnalysisWorkItem(
                question=_question(2),
                analyze_solution_evidence=False,
            ),
        ),
    )

    assert [call["projection"] for call in gateway.calls] == ["both", "tag"]
    assert outcome["operation_ids"] == {
        "both": "mixed-analysis",
        "tag": "mixed-analysis:tag",
    }
    assert [item["question_id"] for item in outcome["items"]] == [1, 2]


def test_combined_projection_partial_success_and_single_projection_retry(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database)
    question = _question(1)
    invalid_criteria = {
        **_criteria_payload(1),
        "score": 5,
    }
    gateway = QueueGateway(
        [
            {
                "results": [
                    {
                        "question_id": 1,
                        "tag_analysis": _tag_payload(),
                        "training_criteria": invalid_criteria,
                    }
                ]
            },
            {
                "results": [
                    {
                        "question_id": 1,
                        "training_criteria": _criteria_payload(1),
                    }
                ]
            },
        ]
    )
    writer = FakeTagWriter()
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=writer,
    )

    partial = module.analyze(
        operation_id="p4-09-partial",
        questions=(question,),
    )
    retried = module.retry_failed_projection(
        operation_id="p4-09-partial",
        questions=(question,),
        projection="training_criteria",
    )

    assert partial["status"] == "partial"
    assert partial["items"][0]["tag_status"] == "succeeded"
    assert partial["items"][0]["criteria_status"] == "failed"
    assert retried["status"] == "succeeded"
    assert retried["items"][0]["criteria_status"] == "succeeded"
    assert retried["request_count"] == 2
    assert retried["usage"] == {
        "prompt_tokens": 200,
        "completion_tokens": 100,
        "total_tokens": 300,
    }
    assert writer.calls == [(1, "p4-09-partial")]
    assert [item["projection"] for item in gateway.calls] == [
        "both",
        "training_criteria",
    ]


def test_duplicate_operation_is_idempotent_and_conflicting_content_is_rejected(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database)
    gateway = QueueGateway(
        [
            {
                "results": [
                    {
                        "question_id": 1,
                        "tag_analysis": _tag_payload(),
                        "training_criteria": _criteria_payload(1),
                    }
                ]
            }
        ]
    )
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=FakeTagWriter(),
    )
    question = _question(1)

    first = module.analyze(
        operation_id="p4-09-idempotent",
        questions=(question,),
    )
    duplicate = module.analyze(
        operation_id="p4-09-idempotent",
        questions=(question,),
    )

    assert first == duplicate
    assert len(gateway.calls) == 1
    with pytest.raises(AnalysisConflictError):
        module.analyze(
            operation_id="p4-09-idempotent",
            questions=(
                _question(1, text="同一 operation 下被改写的题目"),
            ),
        )


def test_missing_actual_image_fails_before_model_request(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database)
    gateway = QueueGateway([])
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=FakeTagWriter(),
    )

    summary = module.analyze(
        operation_id="p4-09-missing-image",
        questions=(_question(1, has_images=True),),
    )

    assert summary["status"] == "failed"
    assert summary["request_count"] == 0
    assert summary["items"][0]["tag_error_category"] == "missing_image"
    assert (
        summary["items"][0]["criteria_error_category"]
        == "missing_image"
    )
    assert gateway.calls == []


def test_cancelled_model_call_preserves_cancelled_state_and_can_retry(
    tmp_path: Path,
) -> None:
    class SyntheticCancelledError(RuntimeError):
        pass

    database = tmp_path / "question-bank.db"
    _seed_questions(database)
    cancelled_gateway = RaisingGateway(
        SyntheticCancelledError("cancelled by job context")
    )
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=cancelled_gateway,
        tag_writer=FakeTagWriter(),
    )
    questions = tuple(
        _question(index, question_type="选择题")
        for index in range(1, 7)
    )

    cancelled = module.analyze(
        operation_id="p4-09-cancelled",
        questions=questions,
        projection="training_criteria",
    )

    assert cancelled["status"] == "cancelled"
    assert cancelled["request_count"] == 1
    assert {
        item["criteria_status"] for item in cancelled["items"]
    } == {"cancelled"}
    assert cancelled_gateway.calls == 1


def test_invalid_model_configuration_stops_after_single_canary_request(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database)
    gateway = RaisingGateway(
        SyntheticStatusError(400, "configured model is not supported")
    )
    gateway.max_parallel_requests = 6
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=FakeTagWriter(),
    )
    questions = tuple(
        _question(index, text=f"{index}:" + "长题干" * 170)
        for index in range(1, 7)
    )

    failed = module.analyze(
        operation_id="p4-09-invalid-model-canary",
        questions=questions,
        projection="tag",
    )

    assert failed["status"] == "failed"
    assert failed["request_count"] == 1
    assert gateway.calls == 1
    assert {
        item["tag_error_category"] for item in failed["items"]
    } == {"invalid_request"}


def test_cancelled_projection_retry_does_not_call_unrequested_projection(
    tmp_path: Path,
) -> None:
    class SyntheticCancelledError(RuntimeError):
        pass

    database = tmp_path / "question-bank.db"
    _seed_questions(database)
    writer = FakeTagWriter()
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=RaisingGateway(
            SyntheticCancelledError("cancelled by job context")
        ),
        tag_writer=writer,
    )
    question = _question(1)
    module.analyze(
        operation_id="p4-09-cancelled-retry",
        questions=(question,),
        projection="training_criteria",
    )
    retry_gateway = QueueGateway(
        [
            {
                "results": [
                    {
                        "question_id": 1,
                        "training_criteria": _criteria_payload(1),
                    }
                ]
            }
        ]
    )
    module.gateway = retry_gateway

    retried = module.retry_failed_projection(
        operation_id="p4-09-cancelled-retry",
        questions=(question,),
        projection="training_criteria",
    )

    assert retried["status"] == "succeeded"
    assert writer.calls == []
    assert [call["projection"] for call in retry_gateway.calls] == [
        "training_criteria"
    ]


def test_interrupted_request_is_recovered_without_recalling_success(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database)
    writer = FakeTagWriter()
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=RaisingGateway(KeyboardInterrupt()),
        tag_writer=writer,
    )
    question = _question(1)

    with pytest.raises(KeyboardInterrupt):
        module.analyze(
            operation_id="p4-09-interrupted",
            questions=(question,),
        )

    gateway = QueueGateway(
        [
            {
                "results": [
                    {
                        "question_id": 1,
                        "tag_analysis": _tag_payload(),
                    }
                ]
            },
            {
                "results": [
                    {
                        "question_id": 1,
                        "training_criteria": _criteria_payload(1),
                    }
                ]
            },
        ]
    )
    module.gateway = gateway
    resumed = module.resume_interrupted(
        operation_id="p4-09-interrupted",
        questions=(question,),
    )

    assert resumed["status"] == "succeeded"
    assert resumed["request_count"] == 3
    assert [call["projection"] for call in gateway.calls] == [
        "tag",
        "training_criteria",
    ]
    with connect(database) as connection:
        interrupted = connection.execute(
            """
            SELECT status, error_category
            FROM question_analysis_requests
            WHERE operation_id = ?
            ORDER BY created_at, request_id
            """,
            ("p4-09-interrupted",),
        ).fetchall()
    assert any(
        row["status"] == "failed"
        and row["error_category"] == "interrupted"
        for row in interrupted
    )


def test_dynamic_batching_uses_question_type_output_and_actual_images() -> None:
    image = QuestionAnalysisImage(
        role="question",
        mime_type="image/png",
        content=b"\x89PNG\r\n\x1a\nsynthetic",
    )
    questions = (
        _question(1, question_type="选择题"),
        _question(2, question_type="填空题"),
        _question(3, question_type="计算题"),
        _question(4, question_type="计算题"),
        _question(5, question_type="证明题"),
        _question(
            6,
            question_type="作图题",
            has_images=True,
            images=(image,),
        ),
    )

    batches = plan_analysis_batches(questions, projection="both")

    assert [batch.question_ids for batch in batches] == [
        (1, 2),
        (3, 4),
        (5,),
        (6,),
    ]
    assert all(batch.estimated_input_tokens > 0 for batch in batches)
    assert all(batch.estimated_output_tokens > 0 for batch in batches)


def test_five_gold_question_types_normalize_without_score_fields() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    type_labels = {
        "single_choice": "选择题",
        "fill_blank": "填空题",
        "calculation": "计算题",
        "proof": "证明题",
        "construction": "作图题",
    }

    for index, sample in enumerate(
        fixture["criterion_samples"],
        start=1,
    ):
        question = _question(
            index,
            question_type=type_labels[sample["question_type"]],
        )
        draft = TrainingCriteriaDraft.from_model_dict(
            {
                "schema_version": "training-criteria-draft-v1",
                "question_id": index,
                "points": sample["points"],
                "auxiliary_rules": [],
                "rationale": "P4-00 合成样本",
                "confidence": 1.0,
            },
            question=question,
        )
        assert [point.point_id for point in draft.points] == [
            item["point_id"] for item in sample["points"]
        ]
        serialized = json.dumps(
            draft.to_dict(),
            ensure_ascii=False,
        ).casefold()
        assert '"score"' not in serialized
        assert '"max_score"' not in serialized
        assert '"step_score"' not in serialized


@pytest.mark.parametrize(
    "field_name",
    ["score", "max_score", "step_score", "full_score"],
)
def test_training_criteria_rejects_score_fields_recursively(
    field_name: str,
) -> None:
    question = _question(1)
    payload = _criteria_payload(1)
    payload["points"][0]["metadata"] = {field_name: 2}

    with pytest.raises(ProjectionValidationError):
        TrainingCriteriaDraft.from_model_dict(
            payload,
            question=question,
        )


def test_confirmed_rubric_adapter_removes_scores_and_keeps_obligations() -> None:
    draft = criteria_from_confirmed_rubric(
        question=_question(1, question_type="证明题"),
        rubric_question={
            "max_score": 10,
            "parts": [
                {
                    "part_score": 10,
                    "steps": [
                        {
                            "step_id": "condition",
                            "step_score": 3,
                            "core_goal": "提取必要已知条件",
                            "required_elements": ["两组对应边相等"],
                        },
                        {
                            "step_id": "conclusion",
                            "step_score": 7,
                            "core_goal": "推出待证结论",
                            "required_elements": ["对应边相等"],
                        },
                    ],
                }
            ],
        },
    )

    assert draft.source_kind == "confirmed_rubric_adapter"
    assert [item.point_id for item in draft.points] == [
        "condition",
        "conclusion",
    ]
    serialized = json.dumps(draft.to_dict(), ensure_ascii=False)
    assert "step_score" not in serialized
    assert "max_score" not in serialized


def test_confirmed_multi_blank_rubric_uses_separate_answer_only_units() -> None:
    draft = criteria_from_confirmed_rubric(
        question=_question(
            1,
            question_type="填空题",
            text="分别填写：____，____。",
        ),
        rubric_question={
            "parts": [
                {
                    "steps": [
                        {"step_id": "process-1", "core_goal": "先计算"},
                        {"step_id": "process-2", "core_goal": "再推导"},
                    ]
                }
            ]
        },
        answer_key={
            "parts": [
                {"answer_values": ["3", "5"]},
            ]
        },
    )

    assert [point.point_id for point in draft.points] == [
        "answer-unit-1",
        "answer-unit-2",
    ]
    assert [point.observable_evidence for point in draft.points] == ["3", "5"]
    assert all("过程" not in point.target for point in draft.points)
    assert draft.auxiliary_rules == ()


def test_mixed_fill_and_reasoning_question_keeps_each_part_semantics() -> None:
    question = _question(
        1,
        question_type="填空题",
        text="（1）填写 ____；（2）说明理由。",
    )
    assert question.objective_response_shape == "unknown"

    draft = criteria_from_confirmed_rubric(
        question=question,
        rubric_question={
            "parts": [
                {
                    "steps": [
                        {
                            "step_id": "answer-part",
                            "core_goal": "填写第一个答案",
                            "required_elements": ["3"],
                        }
                    ]
                },
                {
                    "steps": [
                        {
                            "step_id": "reason-part",
                            "core_goal": "说明结论成立的理由",
                            "required_elements": ["给出有效推理"],
                        }
                    ]
                },
            ]
        },
        answer_key={
            "parts": [
                {"answer": "3"},
                {"answer": "由已知条件可推出结论"},
            ]
        },
    )

    assert [point.point_id for point in draft.points] == [
        "p1-answer-part",
        "p2-reason-part",
    ]
    assert draft.points[1].target == "说明结论成立的理由"
    assert draft.points[1].observable_evidence == "给出有效推理"


def test_confirmed_rubric_adapter_prefixes_multi_part_step_ids() -> None:
    # 多小问评分依据中每个小问的 step_id 都从 S1 重新编号，
    # 判定点编号必须带小问序号前缀，保持唯一且与评分标准结构对应。
    draft = criteria_from_confirmed_rubric(
        question=_question(1, question_type="解答题"),
        rubric_question={
            "parts": [
                {
                    "steps": [
                        {"step_id": "S1", "core_goal": "求出∠ADB＝90°"},
                        {"step_id": "S2", "core_goal": "得出∠ABD＝45°"},
                        {"step_id": "S3", "core_goal": "求出∠1＝∠2＝22.5°"},
                    ]
                },
                {
                    "steps": [
                        {"step_id": "S1", "core_goal": "求出∠3的度数"},
                    ]
                },
            ]
        },
    )

    assert [point.point_id for point in draft.points] == [
        "p1-s1",
        "p1-s2",
        "p1-s3",
        "p2-s1",
    ]


def test_combined_schema_is_strict_and_tag_only_v1_adapter_stays_separate() -> None:
    schema = combined_response_format("both")
    item = schema["schema"]["properties"]["results"]["items"]

    assert item["additionalProperties"] is False
    assert set(item["required"]) == {
        "question_id",
        "reference_assessment",
        "reference_assessment_reason",
        "tag_analysis",
        "solution_evidence",
    }
    assert "score" not in json.dumps(schema)
    proposal = item["properties"]["tag_analysis"]["properties"][
        "proposed_tags"
    ]["items"]
    assert proposal["additionalProperties"] is False
    assert set(proposal["required"]) == set(proposal["properties"])
    evidence = item["properties"]["solution_evidence"]
    part = evidence["properties"]["parts"]["items"]
    point = part["properties"]["evidence_points"]["items"]
    link = point["properties"]["fine_term_links"]["items"]
    expected_identifier = "^[a-z][a-z0-9_-]{1,127}$"
    assert part["properties"]["part_id"]["pattern"] == expected_identifier
    assert (
        point["properties"]["evidence_point_id"]["pattern"]
        == expected_identifier
    )
    assert point["properties"]["target"]["minLength"] == 1
    assert point["properties"]["step_index"]["minimum"] == 1
    assert point["properties"]["justification"]["minLength"] == 1
    assert point["properties"]["answer_anchor"]["minLength"] == 1
    assert point["properties"]["depends_on"]["items"]["pattern"] == expected_identifier
    assert point["properties"]["observable_evidence"]["minLength"] == 1
    assert evidence["properties"]["schema_version"]["enum"] == [
        "question-solution-evidence-v2"
    ]
    assert link["properties"]["fine_term_id"]["minLength"] == 1
    assert link["properties"]["fine_term_name"]["minLength"] == 1
    assert part["properties"]["deduction_policy"]["items"]["minLength"] == 1

    @dataclass
    class LegacyResult:
        ok: bool
        analysis: TagAnalysis

    adapted = TagOnlyV1ResultAdapter.adapt(
        1,
        LegacyResult(True, TagAnalysis.from_dict(_tag_payload())),
    )
    assert set(adapted) == {"question_id", "tag_analysis"}
    assert "training_criteria" not in adapted
    assert "solution_evidence" not in adapted


class AcceptingGovernance:
    def constrain(
        self,
        payload: Mapping[str, Any],
        *,
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return {
            "status": "complete",
            "taxonomy_revision": context["expected_revision"],
            "accepted_fields": {
                "knowledge_points": payload["knowledge_points"],
                "prerequisite_points": payload["prerequisite_points"],
                "method_tags": payload["method_tags"],
                "ability_tags": payload["ability_tags"],
                "math_model_tags": payload["math_model_tags"],
                "special_type_tags": payload["special_type_tags"],
                "textbook_chapters": payload["textbook_chapters"],
            },
            "accepted_terms": {
                "knowledge": [
                    {
                        "id": "kp_alg_linear_equation",
                        "name": "一元一次方程",
                    }
                ]
            },
            "proposals": [],
            "notes": [],
        }


class ExistingWriterTaggingStub:
    taxonomy_governance = AcceptingGovernance()

    @staticmethod
    def taxonomy_contract(
        _context: TaggingContext,
    ) -> Mapping[str, Any]:
        return {"taxonomy_revision": 7}


def test_existing_tag_writer_reuses_quality_gate_and_question_save_seam(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database)
    install_current_knowledge(database)
    payload = _tag_payload()
    payload["textbook_chapters"] = ["七年级上册 一元一次方程"]
    writer = ExistingTagProjectionWriter(
        write_service=QuestionBankWriteService(
            database,
            data_root=database.parent,
        ),
        tagging_service=ExistingWriterTaggingStub(),  # type: ignore[arg-type]
    )

    stored = writer.write(
        _question(1),
        payload,
        model_name="synthetic-model",
        operation_id="p4-09-existing-writer",
    )

    assert stored["quality_status"] == "complete"
    with connect(database) as connection:
        rows = connection.execute(
            """
            SELECT tag_type, tag_value, model_name
            FROM question_tags
            WHERE question_id = 1
            """
        ).fetchall()
    assert ("knowledge_point", "一元一次方程", "synthetic-model") in {
        (row["tag_type"], row["tag_value"], row["model_name"])
        for row in rows
    }


def test_existing_tag_writer_accepts_candidate_ids_and_saves_names(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database)
    install_current_knowledge(database)
    payload = _tag_payload()
    payload["knowledge_points"] = ["kp_alg_linear_equation"]
    payload["textbook_chapters"] = ["七年级上册 一元一次方程"]
    writer = ExistingTagProjectionWriter(
        write_service=QuestionBankWriteService(
            database,
            data_root=database.parent,
        ),
        tagging_service=ExistingWriterTaggingStub(),  # type: ignore[arg-type]
    )

    stored = writer.write(
        _question(1),
        payload,
        model_name="synthetic-model",
        operation_id="p4-09-existing-writer-ids",
    )

    assert stored["quality_status"] == "complete"
    with connect(database) as connection:
        rows = connection.execute(
            """
            SELECT tag_type, tag_value, model_name
            FROM question_tags
            WHERE question_id = 1
            """
        ).fetchall()
    assert ("knowledge_point", "一元一次方程", "synthetic-model") in {
        (row["tag_type"], row["tag_value"], row["model_name"])
        for row in rows
    }


def test_existing_tag_writer_reuses_one_prepared_batch_and_defers_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database, count=2)
    install_current_knowledge(database)
    initialize_calls: list[Path] = []
    resolver_calls: list[Path] = []
    refresh_calls: list[tuple[int, ...]] = []
    original_initialize = question_write_module.initialize_database
    original_resolver = CurrentKnowledgeResolver.from_active_database
    original_refresh = (
        QuestionFrequencyService.invalidate_frequency_cache_for_questions
    )

    def tracking_initialize(path: Path) -> None:
        initialize_calls.append(Path(path))
        original_initialize(path)

    def tracking_resolver(
        _cls: type[CurrentKnowledgeResolver],
        path: Path,
    ) -> CurrentKnowledgeResolver:
        resolver_calls.append(Path(path))
        return original_resolver(path)

    def tracking_refresh(
        self: QuestionFrequencyService,
        question_ids,
    ) -> None:
        captured = tuple(int(item) for item in question_ids)
        refresh_calls.append(captured)
        original_refresh(self, captured)

    monkeypatch.setattr(
        question_write_module,
        "initialize_database",
        tracking_initialize,
    )
    monkeypatch.setattr(
        CurrentKnowledgeResolver,
        "from_active_database",
        classmethod(tracking_resolver),
    )
    monkeypatch.setattr(
        QuestionFrequencyService,
        "invalidate_frequency_cache_for_questions",
        tracking_refresh,
    )
    write_service = QuestionBankWriteService(
        database,
        data_root=database.parent,
    )
    writer = ExistingTagProjectionWriter(
        write_service=write_service,
        tagging_service=ExistingWriterTaggingStub(),  # type: ignore[arg-type]
    )
    payload = _tag_payload()
    payload["textbook_chapters"] = ["七年级上册 一元一次方程"]

    with write_service.tag_analysis_batch():
        writer.write(
            _question(1),
            payload,
            model_name="synthetic-model",
            operation_id="p4-09-batched-existing-writer",
        )
        writer.write(
            _question(2),
            payload,
            model_name="synthetic-model",
            operation_id="p4-09-batched-existing-writer",
        )
        assert refresh_calls == []

    assert initialize_calls == [database]
    assert resolver_calls == [database]
    assert refresh_calls == [(1, 2)]


def test_existing_tag_writer_does_not_report_success_without_complete_persisted_tags(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database)
    install_current_knowledge(database)

    class NonPersistingWriteService:
        db_path = database

        @staticmethod
        def save_tag_analysis(*_args, **_kwargs) -> bool:
            return True

    writer = ExistingTagProjectionWriter(
        write_service=NonPersistingWriteService(),  # type: ignore[arg-type]
        tagging_service=ExistingWriterTaggingStub(),  # type: ignore[arg-type]
    )
    payload = _tag_payload()
    payload["textbook_chapters"] = ["七年级上册 一元一次方程"]

    with pytest.raises(ValueError, match="core tags remain incomplete"):
        writer.write(
            _question(1),
            payload,
            model_name="synthetic-model",
            operation_id="persisted-postcondition",
        )


def test_existing_tag_writer_persists_proposals_with_the_question_contract(
    tmp_path: Path,
) -> None:
    class ProposalRecordingGovernance(AcceptingGovernance):
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def constrain(
            self,
            payload: Mapping[str, Any],
            *,
            context: Mapping[str, Any],
        ) -> Mapping[str, Any]:
            self.calls.append(dict(context))
            result = dict(super().constrain(payload, context=context))
            if payload.get("proposed_tags"):
                result["status"] = "needs_review"
                result["proposals"] = [
                    {
                        "id": "proposal-combined",
                        "dimension": "knowledge",
                        "proposed_name": "候选知识点",
                    }
                ]
            return result

    governance = ProposalRecordingGovernance()

    class ProposalTaggingStub(ExistingWriterTaggingStub):
        taxonomy_governance = governance

    database = tmp_path / "question-bank.db"
    _seed_questions(database)
    install_current_knowledge(database)
    contract = {
        "taxonomy_revision": 7,
        "candidate_fingerprint": "combined-question-fingerprint",
        "knowledge_catalog_revision": 4,
        "allowed_dimensions": [
            "knowledge",
            "thought",
            "ability",
            "curriculum",
        ],
        "allowed_term_ids": {
            "knowledge": ["kp_alg_linear_equation"],
            "thought": ["thought_equation"],
            "ability": ["ability_calculation"],
            "curriculum": ["curriculum_linear_equation"],
        },
        "candidates": {
            "knowledge": [
                {
                    "id": "kp_alg_linear_equation",
                    "name": "一元一次方程",
                }
            ],
            "thought": [
                {"id": "thought_equation", "name": "方程思想"}
            ],
            "ability": [
                {"id": "ability_calculation", "name": "运算能力"}
            ],
            "curriculum": [
                {
                    "id": "curriculum_linear_equation",
                    "name": "七年级上册 一元一次方程",
                }
            ],
        },
    }
    question = replace(_question(1), taxonomy_contract=contract)
    payload = _tag_payload()
    payload["textbook_chapters"] = ["七年级上册 一元一次方程"]
    payload["proposed_tags"] = [
        {
            "dimension": "knowledge",
            "name": "候选知识点",
            "definition": "候选定义",
            "reason": "候选目录中没有",
            "nearest_id": "",
            "why_not_reuse": "语义边界不同",
        }
    ]
    writer = ExistingTagProjectionWriter(
        write_service=QuestionBankWriteService(
            database,
            data_root=database.parent,
        ),
        tagging_service=ProposalTaggingStub(),  # type: ignore[arg-type]
    )

    writer.write(
        question,
        payload,
        model_name="synthetic-model",
        operation_id="combined-contract",
    )

    persisted_context = next(
        item for item in governance.calls if item.get("persist_proposals") is True
    )
    assert persisted_context["expected_revision"] == 7
    assert persisted_context["allowed_term_ids"] == contract["allowed_term_ids"]
    assert persisted_context["knowledge_catalog_revision"] == 4
    assert persisted_context["request_token"] == (
        "combined-tag:combined-contract:question:1:taxonomy:7:"
        "candidates:combined-question-fingerprint"
    )


class FakeProtocolResponse:
    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.output_text = json.dumps(payload, ensure_ascii=False)
        self.usage = {
            "input_tokens": 123,
            "output_tokens": 45,
            "total_tokens": 168,
        }


class CapturingProtocolAdapter:
    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def responses(self, **kwargs):
        self.calls.append(kwargs)
        return FakeProtocolResponse(self.payload)


def test_openai_gateway_exposes_shared_execution_parallel_limit() -> None:
    protocol = CapturingProtocolAdapter({"results": []})
    protocol.gateway = SimpleNamespace(
        execution_snapshot=SimpleNamespace(max_in_flight=4),
    )

    gateway = OpenAICombinedAnalysisGateway(
        protocol_adapter=protocol,
        model_name="synthetic-model",
    )

    assert gateway.max_parallel_requests == 4


def test_openai_gateway_uses_current_effective_parallel_limit() -> None:
    protocol = CapturingProtocolAdapter({"results": []})
    protocol.gateway = SimpleNamespace(
        execution_snapshot=SimpleNamespace(max_in_flight=20),
        governor_scope="synthetic-scope",
        governors=SimpleNamespace(
            status=lambda scope, snapshot: {
                "effective_max_in_flight": (
                    2
                    if scope == "synthetic-scope"
                    and snapshot.max_in_flight == 20
                    else 1
                )
            }
        ),
    )

    gateway = OpenAICombinedAnalysisGateway(
        protocol_adapter=protocol,
        model_name="synthetic-model",
    )

    assert gateway.max_parallel_requests == 2


def test_openai_gateway_sends_images_once_without_logging_image_body() -> None:
    image = QuestionAnalysisImage(
        role="question",
        mime_type="image/png",
        content=b"\x89PNG\r\n\x1a\nprivate-synthetic-image",
    )
    question = _question(
        1,
        question_type="作图题",
        has_images=True,
        images=(image,),
    )
    batch = plan_analysis_batches((question,))[0]
    protocol = CapturingProtocolAdapter(
        {
            "results": [
                {
                    "question_id": 1,
                    "tag_analysis": _tag_payload(),
                    "training_criteria": _criteria_payload(1),
                }
            ]
        }
    )
    gateway = OpenAICombinedAnalysisGateway(
        protocol_adapter=protocol,
        model_name="synthetic-model",
    )

    response = gateway.analyze(
        batch,
        projection="both",
        operation_id="p4-09-image",
        request_id="a" * 64,
    )

    assert len(protocol.calls) == 1
    call = protocol.calls[0]
    assert call["allow_retry"] is False
    assert call["operation_id"] == "p4-09-image"
    assert response.usage.total_tokens == 168
    sanitized, attachments = sanitize_request_payload(call["kwargs"])
    assert len(attachments) == 1
    assert attachments[0]["sha256"] == image.sha256
    assert "private-synthetic-image" not in json.dumps(
        sanitized,
        ensure_ascii=False,
    )


def test_gateway_keeps_each_questions_candidate_contract_isolated() -> None:
    first = _question(1)
    second_base = _question(2)
    second = QuestionAnalysisInput(
        question_id=2,
        tagging_context=second_base.tagging_context,
        taxonomy_contract={
            "taxonomy_revision": 7,
            "allowed_dimensions": ["knowledge"],
            "candidates": {
                "knowledge": [
                    {
                        "id": "kp_geo_parallel_lines",
                        "name": "平行线",
                    }
                ]
            },
        },
    )
    protocol = CapturingProtocolAdapter({"results": []})
    gateway = OpenAICombinedAnalysisGateway(
        protocol_adapter=protocol,
        model_name="synthetic-model",
    )

    gateway.analyze(
        plan_analysis_batches((first, second))[0],
        projection="both",
        operation_id="p4-09-contract-isolation",
        request_id="b" * 64,
    )

    user_content = protocol.calls[0]["kwargs"]["input"][1]["content"]
    prompt = json.loads(user_content[0]["text"])
    contracts = {
        item["question_id"]: item["candidate_contract"]
        for item in prompt["questions"]
    }
    assert contracts[1]["candidates"]["knowledge"][0]["id"] == (
        "kp_alg_linear_equation"
    )
    assert contracts[2]["candidates"]["knowledge"][0]["id"] == (
        "kp_geo_parallel_lines"
    )
    assert "existing_tags" not in prompt["questions"][0]["question"]
    rules = prompt["rules"]
    assert "candidate_contract.candidates.knowledge only" in rules
    assert "Copy the id and name together, verbatim" in rules
    assert "only exact candidate id values copied verbatim" in rules
    assert "curriculum_sections" in rules
    assert "只能逐字照抄该题候选契约中候选条的 id" in rules
    assert "part-1-step-1" in rules
    assert "one independently scorable mathematical milestone" in rules
    assert "one evidence point" in rules
    examples = prompt["evidence_examples"]
    assert len(examples["q11_process_positive"]["evidence_points"]) == 3
    assert len(examples["q11_process_negative"]["evidence_points"]) == 1
    assert "do_not_return" in examples["q11_process_negative"]
    atomic = examples["atomic_non_process_positive"]
    assert atomic["response_mode"] == "short_answer_points"
    assert len(atomic["evidence_points"]) == 1
    objective = examples["objective_positive"]
    assert objective["response_mode"] == "exact_objective"
    assert objective["canonical_answer"] == "B"
    assert objective["full_answer"] == ""
    assert objective["evidence_points"][0]["answer_anchor"] == "B"
    assert len(examples["pre_output_checklist"]) >= 5
    assert "exact_objective" in rules and "canonical_answer" in rules


def test_gateway_prompt_turns_teacher_retry_into_targeted_repair() -> None:
    base = _question(11)
    rejected_result = {
        "question_id": 11,
        "tag_analysis": {"knowledge_points": ["三角形内角和定理"]},
        "solution_evidence": {
            "parts": [
                {
                    "part_id": "part-3",
                    "evidence_points": [
                        {
                            "evidence_point_id": "part3-step1",
                            "depends_on": ["part2-step1"],
                        }
                    ],
                }
            ]
        },
    }
    question = QuestionAnalysisInput(
        question_id=base.question_id,
        tagging_context=base.tagging_context,
        taxonomy_contract=base.taxonomy_contract,
        repair_context={
            "mode": "repair_previous_rejected_result",
            "validation_error": (
                "part-3/part3-step1 的 depends_on 不能引用其他小问的 "
                "part2-step1"
            ),
            "previous_result": rejected_result,
        },
    )
    protocol = CapturingProtocolAdapter({"results": []})
    gateway = OpenAICombinedAnalysisGateway(
        protocol_adapter=protocol,
        model_name="synthetic-model",
    )

    gateway.analyze(
        plan_analysis_batches((question,))[0],
        projection="both",
        operation_id="targeted-repair-prompt",
        request_id="c" * 64,
    )

    prompt = json.loads(
        protocol.calls[0]["kwargs"]["input"][1]["content"][0]["text"]
    )
    repair = prompt["questions"][0]["repair_context"]
    assert repair["previous_result"] == rejected_result
    assert "part2-step1" in repair["validation_error"]
    assert "repair" in prompt["rules"].casefold()
    assert "fresh dependency namespace" in prompt["rules"]
    assert "full_answer" in prompt["rules"]
    assert "If only one milestone can be confirmed" in prompt["rules"]
    assert "choose the matching non-process response_mode" in prompt["rules"]
    assert "never invent steps just to satisfy a count" in prompt["rules"]
    assert "Do not infer an exact evidence point count" in prompt["rules"]


def test_input_loader_includes_rich_text_and_controlled_actual_images(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    data_root = tmp_path / "data"
    _seed_questions(database)
    asset = (
        data_root
        / "question_bank"
        / "extracted_images"
        / "synthetic.png"
    )
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"\x89PNG\r\n\x1a\nloader-synthetic")
    rich_root = data_root / "question_bank" / "rich_content"
    rich_root.mkdir(parents=True)
    rich_root.joinpath("question_1.json").write_text(
        json.dumps(
            {
                "version": 3,
                "question_id": 1,
                "question_blocks": [
                    {
                        "text": (
                            "富内容题干"
                            "[[IMAGE:question_bank/extracted_images/"
                            "synthetic.png]]"
                        ),
                        "xml": (
                            '<w:p xmlns:w="http://schemas.openxmlformats.org/'
                            'wordprocessingml/2006/main"><w:r><w:t>富内容题干</w:t>'
                            "</w:r></w:p>"
                        ),
                        "image_relationships": {
                            "rId5": (
                                "question_bank/extracted_images/"
                                "synthetic.png"
                            )
                        },
                    },
                    {
                        "text": "三、解答题（本题共7小题，共61分）",
                        "xml": (
                            '<w:p xmlns:w="http://schemas.openxmlformats.org/'
                            'wordprocessingml/2006/main"><w:r><w:t>'
                            "三、解答题（本题共7小题，共61分）"
                            "</w:t></w:r></w:p>"
                        ),
                    },
                ],
                "answer_blocks": [{"text": "富内容答案"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with connect(database) as connection:
        connection.execute(
            """
            UPDATE questions
            SET has_images = 1,
                image_paths = ?
            WHERE id = 1
            """,
            (
                json.dumps(
                    [
                        "question_bank/extracted_images/"
                        "synthetic.png"
                    ]
                ),
            ),
        )

    loaded = QuestionAnalysisInputLoader(
        db_path=database,
        data_root=data_root,
    ).load((1,), curriculum_volume_id="pep-7-up")

    assert loaded[0].has_required_images is True
    assert loaded[0].tagging_context.curriculum_volume_id == "pep-7-up"
    assert loaded[0].rich_question_blocks == ({"text": "富内容题干"},)
    assert loaded[0].word_question_blocks == (
        {
            "text": (
                "富内容题干"
                "[[IMAGE:question_bank/extracted_images/synthetic.png]]"
            ),
            "xml": (
                '<w:p xmlns:w="http://schemas.openxmlformats.org/'
                'wordprocessingml/2006/main"><w:r><w:t>富内容题干</w:t>'
                "</w:r></w:p>"
            ),
            "image_relationships": {
                "rId5": (
                    "sha256:"
                    + hashlib.sha256(asset.read_bytes()).hexdigest()
                )
            },
        },
    )
    assert len(loaded[0].images) == 1
    assert loaded[0].images[0].content.endswith(b"loader-synthetic")
