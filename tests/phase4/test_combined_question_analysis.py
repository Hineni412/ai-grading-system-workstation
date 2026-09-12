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
    GatewayResponseParseError,
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
from question_bank.services.ai_tagging_service import _rule_conflict_notes
from question_bank.services.question_write_service import QuestionBankWriteService
from question_bank.training_criteria.analysis import _response_items
from tests.current_knowledge_support import install_current_knowledge


def _schema_node(root: Mapping[str, Any], node: Mapping[str, Any]) -> Mapping[str, Any]:
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        resolved = root.get("$defs", {}).get(ref.rsplit("/", 1)[-1])
        if isinstance(resolved, Mapping):
            return resolved
    return node


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


def test_missing_actual_image_continues_as_text_only(
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

    summary = module.analyze(
        operation_id="p4-09-missing-image-text-only",
        questions=(_question(1, has_images=True),),
    )

    assert summary["request_count"] == 1
    assert gateway.calls
    assert summary["items"][0].get("tag_error_category") != "missing_image"
    assert summary["items"][0].get("criteria_error_category") != "missing_image"


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
        "question_type_suggestion",
        "tag_analysis",
        "solution_evidence",
        "part_assessments",
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
    assert "uniqueItems" not in point["properties"]["depends_on"]
    assert "uniqueItems" not in json.dumps(schema)
    assert point["properties"]["observable_evidence"]["minLength"] == 1
    assert evidence["properties"]["schema_version"]["enum"] == [
        "question-solution-evidence-v2"
    ]
    assert link["properties"]["fine_term_id"]["minLength"] == 1
    assert link["properties"]["fine_term_name"]["minLength"] == 1
    assert "enum" not in item["properties"]["tag_analysis"]["properties"][
        "knowledge_points"
    ]["items"]
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


def test_combined_schema_locks_controlled_fields_to_batch_ids() -> None:
    schema = combined_response_format(
        "both",
        allowed_term_ids={"knowledge": ["kp_alg_linear_equation"]},
    )
    item = schema["schema"]["properties"]["results"]["items"]
    knowledge_items = _schema_node(
        schema["schema"],
        item["properties"]["tag_analysis"]["properties"]["knowledge_points"]["items"],
    )
    fine_term_id = _schema_node(
        schema["schema"],
        item["properties"]["solution_evidence"]["properties"]["parts"]["items"][
            "properties"
        ]["evidence_points"]["items"]["properties"]["fine_term_links"]["items"][
            "properties"
        ]["fine_term_id"],
    )

    assert knowledge_items["enum"] == ["kp_alg_linear_equation"]
    assert fine_term_id["enum"] == ["kp_alg_linear_equation"]
    assert schema["schema"]["$defs"]["knowledge_id"]["enum"] == [
        "kp_alg_linear_equation"
    ]


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
            "allowed_term_ids": {"knowledge": ["kp_geo_parallel_lines"]},
            "candidate_fingerprint": "should-not-be-sent",
            "candidates": {
                "knowledge": [
                    {
                        "id": "kp_geo_parallel_lines",
                        "name": "平行线",
                        "definition": "very long extra field",
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
    assert "kp_alg_linear_equation" in contracts[1]["candidates"]["knowledge"][0]["id"]
    format_schema = protocol.calls[0]["kwargs"]["text"]["format"]["schema"]
    knowledge_items = _schema_node(
        format_schema,
        format_schema["properties"]["results"]["items"]["properties"]["tag_analysis"][
            "properties"
        ]["knowledge_points"]["items"],
    )
    assert knowledge_items["enum"] == [
        "kp_alg_linear_equation",
        "kp_geo_parallel_lines",
    ]
    fine_term_id = _schema_node(
        format_schema,
        format_schema["properties"]["results"]["items"]["properties"][
            "solution_evidence"
        ]["properties"]["parts"]["items"]["properties"]["evidence_points"]["items"][
            "properties"
        ]["fine_term_links"]["items"]["properties"]["fine_term_id"],
    )
    assert fine_term_id["enum"] == [
        "kp_alg_linear_equation",
        "kp_geo_parallel_lines",
    ]
    assert contracts[2]["candidates"]["knowledge"][0]["id"] == (
        "kp_geo_parallel_lines"
    )
    assert "definition" not in contracts[2]["candidates"]["knowledge"][0]
    assert "allowed_term_ids" not in contracts[2]
    assert "candidate_fingerprint" not in contracts[2]
    assert "existing_tags" not in prompt["questions"][0]["question"]
    rules = prompt["rules"]
    assert "candidate_contract.candidates.knowledge" in rules
    assert "id 与 name 成对原样照抄" in rules
    assert "只能逐字照抄该题 candidate_contract" in rules
    assert "curriculum_sections" in rules
    assert "只能逐字照抄该题 candidate_contract 中对应维度候选条的 id" in rules
    assert "part-1-step-1" in rules
    assert "独立可评分的数学台阶" in rules
    assert "one evidence point" in rules or "一个 evidence point" in rules
    assert "answer_anchor 仅定位参考解答" in rules
    assert "allow_alternative_methods=false 不禁止同一方法的等价表达" in rules
    assert "只抄边长后下结论、平方关系不成立或循环论证" in rules
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
    assert "独立命名空间" in prompt["rules"]
    assert "full_answer" in prompt["rules"]
    assert "若只能确认一个台阶" in prompt["rules"]
    assert "改用匹配的非过程 response_mode" in prompt["rules"]
    assert "不得为凑数量发明步骤" in prompt["rules"]
    assert "不要从标点、等式、角符号或连接词推断证据点个数" in prompt["rules"]


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


# ---------------------------------------------------------------------------
# 重复 result 容错合并（多问大题模型按小问各返回一份 result 的场景）
# ---------------------------------------------------------------------------


def _evidence_part(label: str, *, steps: int = 1) -> dict[str, Any]:
    points = []
    for index in range(1, steps + 1):
        points.append(
            {
                "evidence_point_id": f"part-1-step-{index}",
                "step_index": index,
                "target": f"{label} 台阶 {index}",
                "justification": "合成依据",
                "answer_anchor": "x=1",
                "observable_evidence": "写出 x=1",
                "depends_on": [f"part-1-step-{index - 1}"] if index > 1 else [],
                "fine_term_links": [],
                "equivalent_rules": [],
                "counterexamples": [],
            }
        )
    return {
        "part_id": "part-1",
        "label": label,
        "response_mode": "process_required",
        "canonical_answer": "x=1",
        "accepted_forms": ["x=1"],
        "full_answer": "合成过程",
        "proof_obligations": [],
        "visual_requirements": [],
        "deduction_policy": ["缺少某台阶只影响该台阶"],
        "allow_alternative_methods": True,
        "evidence_points": points,
    }


def _evidence_payload(
    question_id: int,
    label: str,
    *,
    steps: int = 1,
) -> dict[str, Any]:
    return {
        "schema_version": "question-solution-evidence-v2",
        "question_id": question_id,
        "parts": [_evidence_part(label, steps=steps)],
        "auxiliary_rules": [],
        "rationale": "合成证据",
        "confidence": 0.9,
    }


def test_duplicate_results_merge_parts_and_renumber_ids() -> None:
    batch = SimpleNamespace(question_ids=[1])
    first = {
        "question_id": 1,
        "tag_analysis": {
            **_tag_payload(),
            "knowledge_points": ["一元一次方程"],
            "difficulty": 3,
        },
        "solution_evidence": _evidence_payload(1, "第1问", steps=2),
        "reference_assessment": "consistent",
        "reference_assessment_reason": "与参考一致",
    }
    second = {
        "question_id": 1,
        "tag_analysis": {
            **_tag_payload(),
            "knowledge_points": ["一元一次方程", "合并同类项"],
            "difficulty": 5,
        },
        "solution_evidence": _evidence_payload(1, "第2问", steps=1),
        "reference_assessment": "conflict",
        "reference_assessment_reason": "第2问参考缺步骤",
    }

    items, notes = _response_items({"results": [first, second]}, batch)

    merged = items[1]
    parts = merged["solution_evidence"]["parts"]
    assert [part["part_id"] for part in parts] == ["part-1", "part-2"]
    assert [part["label"] for part in parts] == ["第1问", "第2问"]
    ep_ids = [
        point["evidence_point_id"]
        for part in parts
        for point in part["evidence_points"]
    ]
    assert ep_ids == ["part-1-step-1", "part-1-step-2", "part-2-step-1"]
    assert parts[0]["evidence_points"][1]["depends_on"] == ["part-1-step-1"]
    # tag_analysis：列表字段保序去重并集，标量字段取第一份
    assert merged["tag_analysis"]["knowledge_points"] == [
        "一元一次方程",
        "合并同类项",
    ]
    assert merged["tag_analysis"]["difficulty"] == 3
    # reference_assessment 取最保守值，reason 拼接
    assert merged["reference_assessment"] == "conflict"
    assert merged["reference_assessment_reason"] == "与参考一致；第2问参考缺步骤"
    assert "2 份" in notes[1]


def test_duplicate_results_renumber_cross_copy_depends_on() -> None:
    batch = SimpleNamespace(question_ids=[1])
    first = {
        "question_id": 1,
        "solution_evidence": _evidence_payload(1, "第1问", steps=1),
    }
    second_part = _evidence_part("第2问", steps=2)
    # 第二份内部依赖自身命名空间里的 part-1-step-1，合并后必须改写前缀
    second = {
        "question_id": 1,
        "solution_evidence": {
            "schema_version": "question-solution-evidence-v2",
            "question_id": 1,
            "parts": [second_part],
            "auxiliary_rules": ["问间承接写在 auxiliary_rules"],
            "rationale": "合成证据",
            "confidence": 0.8,
        },
    }

    items, _notes = _response_items({"results": [first, second]}, batch)

    parts = items[1]["solution_evidence"]["parts"]
    assert parts[1]["evidence_points"][1]["depends_on"] == ["part-2-step-1"]
    assert items[1]["solution_evidence"]["auxiliary_rules"] == [
        "问间承接写在 auxiliary_rules"
    ]


def test_duplicate_results_fallback_keeps_first_when_parts_empty() -> None:
    batch = SimpleNamespace(question_ids=[1])
    first = {
        "question_id": 1,
        "tag_analysis": _tag_payload(),
        "solution_evidence": _evidence_payload(1, "第1问"),
    }
    broken = {
        "question_id": 1,
        "tag_analysis": _tag_payload(),
        "solution_evidence": {
            **_evidence_payload(1, "第2问"),
            "parts": [],
        },
    }

    items, notes = _response_items({"results": [first, broken]}, batch)

    assert items[1] is first
    assert "保留第一份" in notes[1]


def test_duplicate_results_fallback_on_cross_part_depends_on() -> None:
    batch = SimpleNamespace(question_ids=[1])
    first = {
        "question_id": 1,
        "solution_evidence": _evidence_payload(1, "第1问"),
    }
    cross_part = _evidence_part("第2问", steps=1)
    cross_part["evidence_points"][0]["depends_on"] = ["part-9-step-1"]
    second = {
        "question_id": 1,
        "solution_evidence": {
            "schema_version": "question-solution-evidence-v2",
            "question_id": 1,
            "parts": [cross_part],
            "auxiliary_rules": [],
            "rationale": "合成证据",
            "confidence": 0.8,
        },
    }

    items, notes = _response_items({"results": [first, second]}, batch)

    assert items[1] is first
    assert "保留第一份" in notes[1]


def test_response_items_unknown_or_invalid_question_id_still_rejected() -> None:
    batch = SimpleNamespace(question_ids=[1])
    with pytest.raises(ProjectionValidationError):
        _response_items({"results": [{"question_id": 9}]}, batch)
    with pytest.raises(ProjectionValidationError):
        _response_items({"results": [{"tag_analysis": {}}]}, batch)
    with pytest.raises(ProjectionValidationError):
        _response_items({"results": "not-a-list"}, batch)
    with pytest.raises(ProjectionValidationError):
        _response_items({}, batch)


def test_duplicate_results_merge_end_to_end_with_merge_note(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database)
    duplicate_result = {
        "question_id": 1,
        "tag_analysis": _tag_payload(),
        "training_criteria": _criteria_payload(1),
    }
    gateway = QueueGateway(
        [{"results": [dict(duplicate_result), dict(duplicate_result)]}]
    )
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=FakeTagWriter(),
    )

    summary = module.analyze(
        operation_id="p4-dup-merge",
        questions=(_question(1),),
    )

    item = summary["items"][0]
    assert item["tag_status"] == "succeeded"
    assert item["criteria_status"] == "succeeded"
    assert "合并" in item["merge_note"]


# ---------------------------------------------------------------------------
# 失败原因可观测（脱敏后的异常类型+短消息）
# ---------------------------------------------------------------------------


class _LeakingTagWriter:
    def write(self, *_args: Any, **_kwargs: Any) -> Mapping[str, Any]:
        raise RuntimeError("upstream failed api_key=sk-secret-123")


def test_tag_failure_records_sanitized_error_detail(tmp_path: Path) -> None:
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
        tag_writer=_LeakingTagWriter(),
    )

    summary = module.analyze(
        operation_id="p4-tag-error-detail",
        questions=(_question(1),),
    )

    item = summary["items"][0]
    assert item["tag_status"] == "failed"
    assert item["tag_error_category"] == "tag_validation"
    detail = str(item["tag_error_detail"])
    assert "RuntimeError" in detail
    assert "upstream failed" in detail
    assert "sk-secret-123" not in detail
    assert "***" in detail


def test_criteria_failure_persists_sanitized_validation_error(
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
                        "solution_evidence": {
                            "schema_version": "question-solution-evidence-v2",
                        },
                    }
                ]
            }
        ]
    )
    repository = CombinedAnalysisRepository(database)
    module = CombinedQuestionAnalysisModule(
        repository=repository,
        gateway=gateway,
        tag_writer=FakeTagWriter(),
    )

    summary = module.analyze(
        operation_id="p4-criteria-error-detail",
        questions=(_question(1),),
    )

    item = summary["items"][0]
    assert item["criteria_status"] == "failed"
    assert item["criteria_error_category"] == "evidence_validation"
    assert "solution evidence writer is unavailable" in str(
        item["criteria_error_detail"]
    )
    # 脱敏详情持久化在台账 criteria_payload_json，进程重启后仍随摘要带出
    persisted = repository.operation_summary("p4-criteria-error-detail")
    persisted_item = persisted["items"][0]
    assert "solution evidence writer is unavailable" in str(
        persisted_item["training_criteria"]["validation_error"]
    )
    reloaded = module.analyze(
        operation_id="p4-criteria-error-detail",
        questions=(_question(1),),
    )
    assert "solution evidence writer is unavailable" in str(
        reloaded["items"][0]["criteria_error_detail"]
    )


# ---------------------------------------------------------------------------
# 角平分线规则收窄（新定义题引号包裹的自定义名词不误判）
# ---------------------------------------------------------------------------

_816_STEM = (
    "（5分）在平面直角坐标系中，给出如下定义：点P到x轴、y轴的距离的较大值"
    "称为点P的“长距”，点Q到x轴、y轴的距离相等时，称点Q为“角平分线点”．"
    "（1）点A（﹣3，5）的“长距”为____；"
    "（2）若点C（﹣2，b﹣2）的长距为4，且点C在第三象限内，"
    "请判断点D（9+2b，﹣5）是否为“角平分线点”，并说明理由．"
)


def test_angle_bisector_rule_ignores_quoted_custom_defined_term() -> None:
    context = TaggingContext(question_text=_816_STEM)
    analysis = TagAnalysis.from_dict(_tag_payload())

    notes = _rule_conflict_notes(context, analysis)

    assert not any("角平分线" in note for note in notes)


def test_angle_bisector_rule_still_flags_genuine_bisector_question() -> None:
    context = TaggingContext(
        question_text="如图，AD是△ABC的角平分线，交BC于点D，求证BD=DC。"
    )
    analysis = TagAnalysis.from_dict(_tag_payload())

    notes = _rule_conflict_notes(context, analysis)

    assert any("角平分线" in note for note in notes)


def test_angle_bisector_rule_flags_quoted_term_without_definition_intro() -> None:
    context = TaggingContext(
        question_text="课本把“角平分线”作为重点概念，请完成相关计算。"
    )
    analysis = TagAnalysis.from_dict(_tag_payload())

    notes = _rule_conflict_notes(context, analysis)

    assert any("角平分线" in note for note in notes)


class RepairQueueGateway:
    """QueueGateway variant carrying a channel retry budget.

    Captures each batch's repair_context payloads so tests can assert what
    feedback the model would actually receive.
    """

    def __init__(
        self,
        responses: list[Any],
        *,
        max_auto_retries: int = 0,
    ) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.max_auto_retries = max_auto_retries

    def analyze(
        self,
        batch: Any,
        *,
        projection: Any,
        operation_id: str,
        request_id: str,
    ) -> GatewayBatchResponse:
        self.calls.append(
            {
                "question_ids": batch.question_ids,
                "repair_contexts": [
                    dict(question.repair_context)
                    for question in batch.questions
                ],
            }
        )
        payload = self.responses.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return GatewayBatchResponse(
            payload=payload,
            model_name="synthetic-model",
            usage=GatewayUsage(100, 50, 150),
            latency_ms=12,
        )


def test_shape_failure_retries_with_repair_context(tmp_path: Path) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database, count=1)
    gateway = RepairQueueGateway(
        [
            {"unexpected_shape": True},
            {
                "results": [
                    {
                        "question_id": 1,
                        "tag_analysis": _tag_payload(),
                        "training_criteria": _criteria_payload(1),
                    }
                ]
            },
        ],
        max_auto_retries=2,
    )
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=FakeTagWriter(),
    )

    module.analyze_work_items(
        operation_id="repair-shape",
        work_items=(QuestionAnalysisWorkItem(question=_question(1)),),
    )

    assert len(gateway.calls) == 2
    assert gateway.calls[0]["repair_contexts"] == [{}]
    repair_context = gateway.calls[1]["repair_contexts"][0]
    assert repair_context["mode"] == "repair_previous_rejected_result"
    assert "combined response has no results" in repair_context[
        "validation_error"
    ]
    assert repair_context["previous_result"] == {"unexpected_shape": True}
    repository = CombinedAnalysisRepository(database)
    assert repository.projection_status("repair-shape", 1, "tag") == "succeeded"
    assert (
        repository.projection_status(
            "repair-shape", 1, "training_criteria"
        )
        == "succeeded"
    )


def test_parse_failure_retries_with_raw_output_feedback(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database, count=1)
    gateway = RepairQueueGateway(
        [
            GatewayResponseParseError(
                "combined model response JSON parsing failed",
                raw_text="前缀杂质 {\"broken\": true",
            ),
            {
                "results": [
                    {
                        "question_id": 1,
                        "tag_analysis": _tag_payload(),
                        "training_criteria": _criteria_payload(1),
                    }
                ]
            },
        ],
        max_auto_retries=2,
    )
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=FakeTagWriter(),
    )

    module.analyze_work_items(
        operation_id="repair-parse",
        work_items=(QuestionAnalysisWorkItem(question=_question(1)),),
    )

    assert len(gateway.calls) == 2
    repair_context = gateway.calls[1]["repair_contexts"][0]
    assert repair_context["previous_result"] == {
        "raw_output": "前缀杂质 {\"broken\": true"
    }
    repository = CombinedAnalysisRepository(database)
    assert repository.projection_status("repair-parse", 1, "tag") == "succeeded"


def test_shape_failure_budget_exhaustion_fails_without_extra_calls(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database, count=1)
    gateway = RepairQueueGateway(
        [{"bad": 1}, {"bad": 2}, {"bad": 3}],
        max_auto_retries=2,
    )
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=FakeTagWriter(),
    )

    module.analyze_work_items(
        operation_id="repair-exhausted",
        work_items=(QuestionAnalysisWorkItem(question=_question(1)),),
    )

    assert len(gateway.calls) == 3
    assert not gateway.responses
    repository = CombinedAnalysisRepository(database)
    assert repository.projection_status("repair-exhausted", 1, "tag") == (
        "failed"
    )
    assert (
        repository.projection_status(
            "repair-exhausted", 1, "training_criteria"
        )
        == "failed"
    )


def test_zero_budget_keeps_single_shot_behavior(tmp_path: Path) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database, count=1)
    gateway = RepairQueueGateway([{"bad": 1}], max_auto_retries=0)
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=FakeTagWriter(),
    )

    module.analyze_work_items(
        operation_id="repair-zero",
        work_items=(QuestionAnalysisWorkItem(question=_question(1)),),
    )

    assert len(gateway.calls) == 1
    repository = CombinedAnalysisRepository(database)
    assert repository.projection_status("repair-zero", 1, "tag") == "failed"


def test_question_level_validation_failure_repairs_failed_questions_only(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database, count=2)
    incomplete_tag = {**_tag_payload(), "knowledge_points": []}
    gateway = RepairQueueGateway(
        [
            {
                "results": [
                    {
                        "question_id": 1,
                        "tag_analysis": incomplete_tag,
                        "training_criteria": _criteria_payload(1),
                    },
                    {
                        "question_id": 2,
                        "tag_analysis": _tag_payload(),
                        "training_criteria": _criteria_payload(2),
                    },
                ]
            },
            {
                "results": [
                    {"question_id": 1, "tag_analysis": _tag_payload()}
                ]
            },
        ],
        max_auto_retries=2,
    )
    writer = FakeTagWriter()
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=writer,
    )

    module.analyze_work_items(
        operation_id="repair-question",
        work_items=(
            QuestionAnalysisWorkItem(question=_question(1)),
            QuestionAnalysisWorkItem(question=_question(2)),
        ),
    )

    assert len(gateway.calls) == 2
    assert gateway.calls[1]["question_ids"] == (1,)
    repair_context = gateway.calls[1]["repair_contexts"][0]
    assert (
        "synthetic tag projection is incomplete"
        in repair_context["validation_error"]
    )
    assert repair_context["previous_result"]["tag_analysis"] == incomplete_tag
    repository = CombinedAnalysisRepository(database)
    assert repository.projection_status("repair-question", 1, "tag") == (
        "succeeded"
    )
    # The sibling projection succeeded on the first attempt and the repair
    # response omits it; it must stay succeeded, not be overwritten.
    assert (
        repository.projection_status(
            "repair-question", 1, "training_criteria"
        )
        == "succeeded"
    )
    assert repository.projection_status("repair-question", 2, "tag") == (
        "succeeded"
    )
    assert [call[0] for call in writer.calls] == [2, 1]
