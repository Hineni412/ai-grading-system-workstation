"""解答题子类标签消费点：判定点分组、推荐去重键、频度桶归并。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from question_bank.database.schema import initialize_database
from question_bank.models.tag_schema import TaggingContext
from question_bank.recommendation.personalized import _dedup_type_key
from question_bank.services.question_frequency_service import _normalize_question_type
from question_bank.training_criteria.analysis import (
    QuestionAnalysisInput,
    _question_batch_limit,
)
from question_bank.training_criteria.adapters import QuestionAnalysisInputLoader


def _input_with_special_types(
    special_types: list[str],
    *,
    question_type: str = "解答题",
    text: str = "阅读材料并回答下列问题。",
) -> QuestionAnalysisInput:
    return QuestionAnalysisInput(
        question_id=1,
        tagging_context=TaggingContext(
            question_text=text,
            question_type=question_type,
            existing_tags_by_dimension=(
                {"special_type": special_types} if special_types else {}
            ),
        ),
    )


def test_question_type_group_reads_special_type_tags_first() -> None:
    assert _input_with_special_types(["证明"]).question_type_group == "proof"
    assert _input_with_special_types(["画图"]).question_type_group == "construction"
    assert _input_with_special_types(["计算"]).question_type_group == "calculation"


def test_question_type_group_without_tags_keeps_existing_fallback() -> None:
    # 无标签时兜底链与现状一致：题型字符串判词优先，空题型才看题干关键词。
    assert (
        _input_with_special_types([], text="证明：△ABC≌△DEF。").question_type_group
        == "calculation"
    )
    assert (
        _input_with_special_types(
            [], question_type="", text="证明：△ABC≌△DEF。"
        ).question_type_group
        == "proof"
    )
    assert (
        _input_with_special_types(
            [], question_type="", text="用尺规作图作出角平分线。"
        ).question_type_group
        == "construction"
    )
    assert (
        _input_with_special_types(
            [], question_type="", text="阅读材料并回答下列问题。"
        ).question_type_group
        == "calculation"
    )


def test_tagged_proof_question_stays_single_question_batch() -> None:
    # 证明题仍单独成批分析（不退化）。
    assert _question_batch_limit(_input_with_special_types(["证明"])) == 1
    assert _question_batch_limit(_input_with_special_types(["画图"])) == 1
    assert _question_batch_limit(_input_with_special_types(["计算"])) == 3


def test_loader_populates_special_type_tags_into_tagging_context(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO questions (id, question_number, question_type, question_text)
            VALUES (1, '1', '解答题', '证明：△ABC≌△DEF。')
            """
        )
        connection.execute(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, confidence, source
            ) VALUES (1, 'special_type', '证明', 0.7, 'question_type_migration')
            """
        )

    loaded = QuestionAnalysisInputLoader(
        db_path=db_path,
        data_root=tmp_path,
    ).load((1,))[0]

    assert loaded.tagging_context.existing_tags_by_dimension == {
        "special_type": ["证明"]
    }
    assert loaded.question_type_group == "proof"


def test_dedup_type_key_distinguishes_essay_subtypes() -> None:
    base = {"question_type": "解答题"}
    assert _dedup_type_key({**base, "special_types": ["证明"]}) == "解答题·证明"
    assert _dedup_type_key({**base, "special_types": ["计算"]}) == "解答题·计算"
    assert _dedup_type_key({**base, "special_types": ["画图"]}) == "解答题·画图"
    # 未标注的解答题留在裸"解答题"独立桶，不与任何子类互去重。
    assert _dedup_type_key({**base, "special_types": []}) == "解答题"
    assert _dedup_type_key(base) == "解答题"
    # 非解答题不受子类标签影响。
    assert _dedup_type_key({"question_type": "选择题"}) == "选择题"


def test_normalize_question_type_reads_special_type_tags() -> None:
    assert _normalize_question_type("解答题", special_types=["证明"]) == "证明题"
    assert _normalize_question_type("解答题", special_types=["计算"]) == "解答题"
    assert _normalize_question_type("解答题", special_types=["画图"]) == "解答题"
    assert _normalize_question_type("解答题") == "解答题"
    # 其余归并不变（"多选题"在原归并中保持原值）。
    assert _normalize_question_type("选择题") == "选择题"
    assert _normalize_question_type("多选题") == "多选题"
    assert _normalize_question_type("填空题") == "填空题"
    # 兼容输入：旧六值题型字符串仍按原归并处理。
    assert _normalize_question_type("解答题（证明）") == "证明题"
    assert _normalize_question_type("解答题（计算）") == "解答题"
