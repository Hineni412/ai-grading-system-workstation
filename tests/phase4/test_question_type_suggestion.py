from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.models.tag_schema import TagAnalysis, TaggingContext
from question_bank.services.question_write_service import QuestionBankWriteService
from question_bank.solution_evidence import (
    CoreResolution,
    SolutionEvidenceProjectionWriter,
    SolutionEvidenceRepository,
)
from question_bank.training_criteria import (
    BankQuestionTypeSuggestionWriter,
    CombinedAnalysisRepository,
    CombinedQuestionAnalysisModule,
    GatewayBatchResponse,
    GatewayUsage,
    QuestionAnalysisInput,
    QuestionTypeSuggestion,
    combined_response_format,
)
from question_bank.training_criteria.analysis import ProjectionValidationError


class FakeTagWriter:
    def write(
        self,
        question: QuestionAnalysisInput,
        payload: Mapping[str, Any],
        *,
        model_name: str,
        operation_id: str,
    ) -> Mapping[str, Any]:
        analysis = TagAnalysis.from_dict(dict(payload))
        return {
            "schema_version": "tag-only-v1",
            "analysis": analysis.to_dict(),
            "model_name": model_name,
        }


class QueueGateway:
    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self.responses = list(responses)

    def analyze(
        self,
        batch,
        *,
        projection,
        operation_id,
        request_id,
    ) -> GatewayBatchResponse:
        return GatewayBatchResponse(
            payload=self.responses.pop(0),
            model_name="synthetic-model",
            usage=GatewayUsage(100, 50, 150),
            latency_ms=12,
        )


class Resolver:
    def resolve(self, fine_term_id: str) -> CoreResolution:
        if fine_term_id == "kp_alg_linear_equation":
            return CoreResolution(
                status="resolved",
                stable_keys=("kp_alg_linear_equation",),
                reason="test",
            )
        return CoreResolution(status="unmapped", reason="test")


def _seed_question(
    database: Path,
    question_id: int,
    *,
    question_type: str,
    question_text: str,
    answer_text: str,
) -> None:
    initialize_database(database)
    with connect(database) as connection:
        paper_id = connection.execute(
            """
            INSERT INTO papers (title, import_status)
            VALUES ('题型建议合成试卷', 'completed')
            """
        ).lastrowid
        connection.execute(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type,
                question_text, answer_text
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                question_id,
                paper_id,
                str(question_id),
                question_type,
                question_text,
                answer_text,
            ),
        )


def _stored_question_type(database: Path, question_id: int) -> str:
    with connect(database) as connection:
        row = connection.execute(
            "SELECT question_type FROM questions WHERE id = ?",
            (int(question_id),),
        ).fetchone()
    assert row is not None
    return str(row["question_type"] or "")


def _stored_special_types(database: Path, question_id: int) -> list[str]:
    with connect(database) as connection:
        rows = connection.execute(
            """
            SELECT tag_value FROM question_tags
            WHERE question_id = ? AND tag_type = 'special_type'
            ORDER BY id
            """,
            (int(question_id),),
        ).fetchall()
    return [str(row["tag_value"]) for row in rows]


def _question(
    question_id: int,
    *,
    question_type: str = "填空题",
    question_type_confirmed: bool = False,
    text: str = "解方程 x + 1 = 2，x = ____。",
    answer_text: str = "x=1",
) -> QuestionAnalysisInput:
    return QuestionAnalysisInput(
        question_id=question_id,
        question_type_confirmed=question_type_confirmed,
        tagging_context=TaggingContext(
            question_text=text,
            answer_text=answer_text,
            question_number=str(question_id),
            question_type=question_type,
        ),
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
        "method_tags": [],
        "thought_tags": ["方程思想"],
        "ability_tags": ["运算能力"],
        "math_model_tags": [],
        "special_type_tags": [],
        "difficulty": 3,
        "taxonomy_revision": 7,
        "proposed_tags": [],
        "reason": "合成标签理由",
        "confidence": 0.9,
    }


def _process_evidence_payload(question_id: int) -> dict[str, Any]:
    """按真实解答题返回的过程证据：两个独立可评分台阶。"""

    return {
        "schema_version": "question-solution-evidence-v1",
        "question_id": question_id,
        "parts": [
            {
                "part_id": "part-1",
                "label": "",
                "response_mode": "process_required",
                "canonical_answer": "x=1",
                "accepted_forms": ["x=1"],
                "full_answer": "由等式性质移项得到 x=2-1，化简得到 x=1。",
                "proof_obligations": [],
                "visual_requirements": [],
                "deduction_policy": ["缺少某一台阶时只影响该台阶"],
                "allow_alternative_methods": True,
                "evidence_points": [
                    {
                        "evidence_point_id": "step-1",
                        "target": "移项得到 x=2-1",
                        "observable_evidence": "写出 x=2-1",
                        "fine_term_links": [
                            {
                                "fine_term_id": "kp_alg_linear_equation",
                                "fine_term_name": "一元一次方程",
                                "role": "direct",
                            }
                        ],
                        "equivalent_rules": [],
                        "counterexamples": [],
                    },
                    {
                        "evidence_point_id": "step-2",
                        "target": "化简得到 x=1",
                        "observable_evidence": "写出 x=1",
                        "fine_term_links": [],
                        "equivalent_rules": ["1=x"],
                        "counterexamples": [],
                        "depends_on": ["step-1"],
                    },
                ],
            }
        ],
        "auxiliary_rules": [],
        "rationale": "按可观察解题过程拆分。",
        "confidence": 0.92,
    }


def _combined_payload(
    question_id: int,
    *,
    suggestion: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "results": [
            {
                "question_id": question_id,
                "tag_analysis": _tag_payload(),
                "solution_evidence": _process_evidence_payload(question_id),
                "question_type_suggestion": dict(suggestion),
            }
        ]
    }


def _module(
    database: Path,
    tmp_path: Path,
    gateway: QueueGateway,
) -> CombinedQuestionAnalysisModule:
    write_service = QuestionBankWriteService(database, data_root=tmp_path)
    return CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=FakeTagWriter(),
        evidence_writer=SolutionEvidenceProjectionWriter(
            mapping_repository=Resolver(),
            evidence_repository=SolutionEvidenceRepository(database),
        ),
        question_type_writer=BankQuestionTypeSuggestionWriter(
            write_service=write_service,
        ),
    )


def test_response_schema_requires_closed_enum_suggestion() -> None:
    schema = combined_response_format("both")["schema"]
    item = schema["properties"]["results"]["items"]
    suggestion = item["properties"]["question_type_suggestion"]
    assert "question_type_suggestion" in item["required"]
    assert suggestion["properties"]["question_type"]["enum"] == [
        "选择题",
        "多选题",
        "填空题",
        "解答题",
    ]
    assert suggestion["properties"]["essay_subtype"]["enum"] == [
        "画图",
        "计算",
        "证明",
        None,
    ]


def test_fraction_marks_are_not_subquestion_labels() -> None:
    fraction = _question(1, text="计算 (1)/(2) 的值，结果为____。")
    assert fraction.explicit_part_labels == ()
    subquestions = _question(
        1,
        question_type="解答题",
        text="已知 x+1=2。（1）求 x 的值；（2）化简 2x。",
    )
    assert subquestions.explicit_part_labels == ("1", "2")


def test_suggestion_matching_local_type_keeps_objective_shape(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_question(
        database,
        1,
        question_type="填空题",
        question_text="解方程 x + 1 = 2，x = ____。",
        answer_text="x=1",
    )
    gateway = QueueGateway(
        [
            _combined_payload(
                1,
                suggestion={
                    "question_type": "填空题",
                    "reason": "只有一个作答空位。",
                },
            )
        ]
    )
    module = _module(database, tmp_path, gateway)

    result = module.analyze(
        operation_id="type-suggestion:same",
        questions=(_question(1),),
        projection="both",
    )

    item = result["items"][0]
    assert item["criteria_status"] == "succeeded"
    criteria = item["training_criteria"]
    assert criteria["question_type"] == "fill_blank"
    # 建议与本地一致：仍按客观形态压成只看答案的一个判定点，不记订正审计。
    assert [point["point_id"] for point in criteria["points"]] == [
        "objective-answer"
    ]
    assert "question_type_suggestion" not in criteria
    assert _stored_question_type(database, 1) == "填空题"


def test_conflicting_suggestion_relaxes_shape_and_corrects_type(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_question(
        database,
        1,
        question_type="填空题",
        question_text="解方程 x + 1 = 2，x = ____。",
        answer_text="x=1",
    )
    gateway = QueueGateway(
        [
            _combined_payload(
                1,
                suggestion={
                    "question_type": "解答题",
                    "reason": "需要完整求解过程，本地填空是误判。",
                    "essay_subtype": "计算",
                },
            )
        ]
    )
    module = _module(database, tmp_path, gateway)

    result = module.analyze(
        operation_id="type-suggestion:corrected",
        questions=(_question(1),),
        projection="both",
    )

    item = result["items"][0]
    assert item["criteria_status"] == "succeeded"
    criteria = item["training_criteria"]
    # 解除客观形态硬约束：判定点按过程题生成。
    assert criteria["question_type"] == "calculation"
    assert [point["target"] for point in criteria["points"]] == [
        "移项得到 x=2-1",
        "化简得到 x=1",
    ]
    audit = criteria["question_type_suggestion"]
    assert audit["action"] == "applied"
    assert audit["local_type"] == "填空题"
    assert audit["suggested_type"] == "解答题"
    assert audit["suggested_subtype"] == "计算"
    assert audit["subtype_action"] == "applied"
    assert _stored_question_type(database, 1) == "解答题"
    assert _stored_special_types(database, 1) == ["计算"]
    latest = SolutionEvidenceRepository(database).latest(1)
    assert latest is not None
    parts = latest["evidence"]["parts"]
    assert parts[0]["response_mode"] == "process_required"


def test_invalid_suggestion_enum_is_ignored(tmp_path: Path) -> None:
    database = tmp_path / "question-bank.db"
    _seed_question(
        database,
        1,
        question_type="填空题",
        question_text="解方程 x + 1 = 2，x = ____。",
        answer_text="x=1",
    )
    gateway = QueueGateway(
        [
            _combined_payload(
                1,
                suggestion={
                    "question_type": "论述题",
                    "reason": "不在封闭枚举内。",
                },
            )
        ]
    )
    module = _module(database, tmp_path, gateway)

    result = module.analyze(
        operation_id="type-suggestion:invalid",
        questions=(_question(1),),
        projection="both",
    )

    item = result["items"][0]
    assert item["criteria_status"] == "succeeded"
    assert "题型枚举" in item["question_type_suggestion_note"]
    criteria = item["training_criteria"]
    # 非法建议不改变本地行为：仍按客观形态只看答案。
    assert criteria["question_type"] == "fill_blank"
    assert [point["point_id"] for point in criteria["points"]] == [
        "objective-answer"
    ]
    assert "question_type_suggestion" not in criteria
    assert _stored_question_type(database, 1) == "填空题"


def test_confirmed_type_conflict_is_recorded_without_changes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_question(
        database,
        1,
        question_type="填空题",
        question_text="解方程 x + 1 = 2，x = ____。",
        answer_text="x=1",
    )
    gateway = QueueGateway(
        [
            _combined_payload(
                1,
                suggestion={
                    "question_type": "解答题",
                    "reason": "模型认为应看过程。",
                    "essay_subtype": "计算",
                },
            )
        ]
    )
    module = _module(database, tmp_path, gateway)

    result = module.analyze(
        operation_id="type-suggestion:confirmed",
        questions=(_question(1, question_type_confirmed=True),),
        projection="both",
    )

    item = result["items"][0]
    assert item["criteria_status"] == "succeeded"
    criteria = item["training_criteria"]
    # 教师确认的题型优先：证据与判定点仍按本地客观形态处理。
    assert criteria["question_type"] == "fill_blank"
    assert [point["point_id"] for point in criteria["points"]] == [
        "objective-answer"
    ]
    audit = criteria["question_type_suggestion"]
    assert audit["action"] == "conflict_only"
    assert audit["suggested_type"] == "解答题"
    assert _stored_question_type(database, 1) == "填空题"
    # 教师确认的题型冲突只登记，子类标签也不写。
    assert _stored_special_types(database, 1) == []


def test_suggestion_dict_validation_rejects_unknown_enum() -> None:
    with pytest.raises(ProjectionValidationError):
        QuestionTypeSuggestion.from_dict(
            {"question_type": "论述题", "reason": "不在枚举内"}
        )
    # 归一前的旧六值题型不再是合法建议值。
    with pytest.raises(ProjectionValidationError):
        QuestionTypeSuggestion.from_dict(
            {"question_type": "解答题（证明）", "reason": "需要证明过程"}
        )
    # 子类建议只接受封闭子类值，且只能挂在解答题上。
    with pytest.raises(ProjectionValidationError):
        QuestionTypeSuggestion.from_dict(
            {"question_type": "解答题", "essay_subtype": "探究"}
        )
    with pytest.raises(ProjectionValidationError):
        QuestionTypeSuggestion.from_dict(
            {"question_type": "填空题", "essay_subtype": "证明"}
        )
    suggestion = QuestionTypeSuggestion.from_dict(
        {
            "question_type": "解答题",
            "reason": "需要证明过程",
            "essay_subtype": "证明",
        }
    )
    assert suggestion.question_type_group == "proof"
