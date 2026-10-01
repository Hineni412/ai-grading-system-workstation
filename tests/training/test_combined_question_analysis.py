from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.models.tag_schema import TagAnalysis, TaggingContext
from question_bank.training_criteria import (
    AnalysisConflictError,
    CombinedAnalysisRepository,
    CombinedQuestionAnalysisModule,
    ExistingTagProjectionWriter,
    GatewayBatchResponse,
    GatewayUsage,
    QuestionAnalysisImage,
    QuestionAnalysisInput,
    QuestionAnalysisWorkItem,
)

from tests.current_knowledge_support import install_current_knowledge


FIXTURE = Path(__file__).parent / "fixtures" / "p4_00_gold_set.json"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 1), (-5, 1), (8, 8), (100, 100), (101, 100), (999, 100),
     ("12", 12), (None, 1), ("invalid", 1), (float("inf"), 1)],
)
def test_analysis_channel_parallel_limit(value: object, expected: int) -> None:
    from types import SimpleNamespace
    from question_bank.training_criteria.analysis import gateway_parallel_limit

    assert gateway_parallel_limit(SimpleNamespace(max_parallel_requests=value)) == expected
    assert gateway_parallel_limit(SimpleNamespace()) == 1


@pytest.mark.parametrize("projection", ["tag", "all"])
def test_prompt_shares_only_identical_candidate_catalogs(projection: str) -> None:
    from question_bank.training_criteria.adapters import _combined_prompt, _prompt_candidate_contract
    from question_bank.training_criteria.analysis import PlannedAnalysisBatch

    questions = tuple(
        QuestionAnalysisInput(
            question_id=number,
            tagging_context=TaggingContext(question_text=f"计算 {number}+1", question_type="填空题"),
            taxonomy_contract={"candidates": {
                "ability": [{"id": "ability-1", "name": "运算能力"}],
                "knowledge": [{"id": f"knowledge-{scope}", "name": f"知识 {scope}"}],
            }, "taxonomy_revision": scope},
        )
        for number, scope in [(1, 1), (2, 1), (3, 2)]
    )
    batch = PlannedAnalysisBatch(questions, 1000, 1000)
    payload = json.loads(_combined_prompt(batch, projection)[1]["content"][0]["text"])
    assert len(payload["candidate_contracts"]) == 2
    for original, sent in zip(questions, payload["questions"], strict=True):
        assert sent["question_id"] == original.question_id
        assert payload["candidate_contracts"][sent["candidate_contract_ref"]] == _prompt_candidate_contract(
            original.taxonomy_contract, include_knowledge=projection != "tag",
        )
    assert payload["questions"][0]["candidate_contract_ref"] == payload["questions"][1]["candidate_contract_ref"]
    single = json.loads(_combined_prompt(PlannedAnalysisBatch(questions[:1], 1000, 1000), projection)[1]["content"][0]["text"])
    assert "candidate_contract" in single["questions"][0]
    assert "candidate_contracts" not in single


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
        if not analysis.reason:
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
            "allowed_dimensions": ["knowledge", "thought", "ability"],
            "candidates": {
                "knowledge": [
                    {
                        "id": "kp_alg_linear_equation",
                        "name": "一元一次方程",
                    }
                ],
                "thought": [
                    {
                        "id": "thought_equation",
                        "name": "方程思想",
                    }
                ],
                "ability": [
                    {
                        "id": "ability_calculation",
                        "name": "运算能力",
                    }
                ],
            },
        },
    )


def _tag_payload() -> dict[str, Any]:
    return {
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
                "part_label": "第1问",
                "solo": 1,
                "reasoning": 0,
                "computation": 1,
                "context": 0,
                "context_kind": "无情境",
                "hidden": 0,
                "cases": 0,
                "param_dynamic": 0,
                "trap": 0,
                "knowledge": 1,
                "evidence": "一步方程即可求出未知数",
            }
        ],
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
            questions=(_question(1, text="同一 operation 下被改写的题目"),),
        )


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
        row["status"] == "failed" and row["error_category"] == "interrupted"
        for row in interrupted
    )


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
                "method_tags": payload.get("method_tags", []),
                "thought_tags": payload.get("thought_tags", []),
                "ability_tags": payload.get("ability_tags", []),
                "math_model_tags": payload.get("math_model_tags", []),
                "special_type_tags": payload.get("special_type_tags", []),
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


# ---------------------------------------------------------------------------
# 重复 result 容错合并（多问大题模型按小问各返回一份 result 的场景）
# ---------------------------------------------------------------------------


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
                    dict(question.repair_context) for question in batch.questions
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
    assert repository.projection_status("repair-exhausted", 1, "tag") == ("failed")
    assert (
        repository.projection_status("repair-exhausted", 1, "training_criteria")
        == "failed"
    )
