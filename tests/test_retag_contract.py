"""八上重打标签方案的单元契约测试（隔离合成库）。

覆盖：TagAnalysis 新字段归一、标准难度公式与存储、预测典型错法候选
替换、整题重打写路径、tag 投影的题型子类落地、判定点关联派生归属的
主小节/考试范围规则。全部使用 tmp_path 合成库，不触碰真实数据。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Mapping


from question_bank.database.schema import connect, initialize_database
from question_bank.models.question import QuestionCreate
from question_bank.models.tag_schema import (
    TagAnalysis,
    TaggingContext,
)
from question_bank.services.predicted_error_patterns import (
    PREDICTED_SOURCE,
    record_predicted_patterns,
)
from question_bank.services.question_write_service import (
    QuestionBankWriteService,
)
from question_bank.training_criteria import (
    CombinedAnalysisRepository,
    CombinedQuestionAnalysisModule,
    GatewayBatchResponse,
    GatewayUsage,
    QuestionAnalysisInput,
)

STAGED_SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "question_bank"
    / "services"
    / "schema_part_difficulty_features.sql"
)

# 课标目录里的真实 stable key：小节锚点 / 只挂章级锚点的新定义技能。
_SECTIONED_KEY = "kp_bnu24_math_g8_upper_1_1_1"
_SECTION_KEY = "kp_bnu24_math_g8_upper_1_1"
_CHAPTER_KEY = "kp_bnu24_math_g8_upper_1"
_SKILL_KEY = "sk_bnu24_math_g8_upper_8_0_101"
_SKILL_SCOPE = "八年级上册 综合与实践"
_SECTION_SCOPE = "八年级上册 第一章 勾股定理"
# 更早册别（七上）与更晚章（八上第四章）的小节键，用于前置知识/进度过滤。
_EARLIER_SECTION_KEY = "kp_bnu24_math_g7_upper_4_1"
_LATER_CHAPTER_SECTION_KEY = "kp_bnu24_math_g8_upper_4_1"


def _base_db(tmp_path: Path) -> Path:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    return database


def _add_question(
    database: Path,
    question_id: int = 1,
    *,
    question_number: str = "1",
) -> int:
    with connect(database) as conn:
        conn.execute(
            """
            INSERT INTO questions (
                id, question_number, question_type, question_text,
                answer_text
            ) VALUES (?, ?, '解答题', '合成题干', '合成答案')
            """,
            (question_id, question_number),
        )
    return question_id


def _seed_evidence_links(
    database: Path,
    question_id: int,
    *,
    version_id: str = "e" * 64,
    direct: tuple[tuple[str, float], ...] = (
        (_SECTIONED_KEY, 1.0),
        (_SKILL_KEY, 1.0),
    ),
    supporting: tuple[str, ...] = (),
) -> str:
    """写入一个可用判定点版本 + 判定点关联（直接 + supporting_prerequisite）。

    启用真实 release v7（修订 9）：技能键的 parent 关系（挂到
    综合与实践章、无小节锚点）直接来自真实图发布。
    """

    from tests.current_knowledge_support import install_current_knowledge

    release_id = install_current_knowledge(database, taxonomy_revision=9)
    link_rows = [
        (
            version_id,
            int(question_id),
            f"part-1-step-{index}",
            release_id,
            "direct",
            key,
            key,
            weight,
        )
        for index, (key, weight) in enumerate(direct, start=1)
    ]
    link_rows.extend(
        (
            version_id,
            int(question_id),
            f"part-1-step-{len(link_rows) + offset}",
            release_id,
            "supporting_prerequisite",
            key,
            key,
            0.5,
        )
        for offset, key in enumerate(supporting, start=1)
    )
    with connect(database) as conn:
        conn.execute(
            """
            INSERT INTO question_solution_evidence_versions (
                evidence_version_id, question_id, source_content_hash,
                schema_version, content_hash, evidence_json, status,
                source_kind, source_reference, created_by, graph_release_id
            ) VALUES (?, ?, ?, 'question-solution-evidence-v2', ?,
                      '{}', 'approved', 'backfill', 'test', 'test', ?)
            """,
            (
                version_id,
                int(question_id),
                "a" * 64,
                "b" * 64,
                release_id,
            ),
        )
        conn.executemany(
            """
            INSERT INTO evidence_point_knowledge_links (
                evidence_version_id, question_id, part_id,
                evidence_point_id, graph_release_id, role, term_id,
                stable_key, resolution_status, weight, source_kind,
                source_reference
            ) VALUES (?, ?, 'part-1', ?, ?, ?, ?, ?, 'resolved',
                      ?, 'link_job', 'test')
            """,
            link_rows,
        )
    return version_id


def _tag_analysis_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "method_tags": [],
        "thought_tags": [],
        "ability_tags": ["运算能力"],
        "math_model_tags": [],
        "special_type_tags": [],
        "difficulty": 5,
        "reason": "合成理由",
        "confidence": 0.9,
        "taxonomy_revision": 9,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------- tag_schema


# ------------------------------------------------------ standard_difficulty


# -------------------------------------------------- predicted_error_patterns


def _pattern_row(
    conn: sqlite3.Connection,
    *,
    pattern: str,
    source: str,
    status: str = "candidate",
    trigger_kind: str = "observation",
    trigger_value: str = "",
) -> None:
    conn.execute(
        "INSERT INTO question_error_patterns (question_id, category, pattern,"
        " explanation, trigger_kind, trigger_value, status, source,"
        " occurrences_json, confirm_token, confirmed_by, confirmed_at)"
        " VALUES (1,'计算与化简',?, '',?,?,?,?, '[]',NULL,'',"
        "datetime('now','localtime'))",
        (pattern, trigger_kind, trigger_value, status, source),
    )


def test_predicted_patterns_replace_only_ai_predicted(tmp_path: Path) -> None:
    database = _base_db(tmp_path)
    _add_question(database)
    with connect(database) as conn:
        _pattern_row(conn, pattern="旧预测错法", source=PREDICTED_SOURCE)
        _pattern_row(conn, pattern="教师确认错法", source="teacher", status="confirmed")
        _pattern_row(
            conn, pattern="观察记录错法", source="auto_pattern", status="candidate"
        )

        inserted = record_predicted_patterns(
            conn,
            1,
            [
                {
                    "category": "计算与化简",
                    "pattern": "新预测错法",
                    "trigger_kind": "step",
                    "trigger_value": "part-1-step-1",
                }
            ],
        )
        assert inserted == 1
        rows = conn.execute(
            "SELECT pattern, source, status FROM question_error_patterns"
            " WHERE question_id = 1 ORDER BY pattern"
        ).fetchall()

    by_pattern = {row["pattern"]: dict(row) for row in rows}
    assert "旧预测错法" not in by_pattern
    assert by_pattern["新预测错法"]["source"] == PREDICTED_SOURCE
    assert by_pattern["新预测错法"]["status"] == "candidate"
    assert by_pattern["教师确认错法"]["status"] == "confirmed"
    assert by_pattern["观察记录错法"]["source"] == "auto_pattern"


# ------------------------------------------------------- save_tag_analysis


# ------------------------------------------------------------------ defect 1


# ------------------------------------------------------------------ defect 2


# ------------------------------------------------- A. 仅打标签请求的裁剪


# ------------------------------------------------- B. 前置知识由判定点关联派生


def test_save_tag_analysis_derives_prerequisite_and_keeps_manual(
    tmp_path: Path,
) -> None:
    database = _base_db(tmp_path)
    _add_question(database)
    _seed_evidence_links(database, 1, supporting=(_EARLIER_SECTION_KEY,))
    with connect(database) as conn:
        conn.executemany(
            "INSERT INTO question_tags (question_id, tag_type, tag_value,"
            " confidence, source) VALUES (1, ?, ?, 1.0, ?)",
            [
                ("prerequisite", "教师认定前置", "manual"),
                ("prerequisite", "旧模型前置", "ai"),
            ],
        )
    writer = QuestionBankWriteService(database, data_root=tmp_path)
    writer.save_tag_analysis(
        1,
        TagAnalysis.from_dict(_tag_analysis_payload()),
        model_name="synthetic-model",
    )
    with connect(database) as conn:
        tags = {
            (row["tag_type"], row["tag_value"], row["source"])
            for row in conn.execute(
                "SELECT tag_type, tag_value, source FROM question_tags"
                " WHERE question_id = 1"
            )
        }
    assert ("prerequisite", _EARLIER_SECTION_KEY, "taxonomy") in tags
    assert ("prerequisite", "教师认定前置", "manual") in tags
    assert ("prerequisite", "旧模型前置", "ai") not in tags


# -------------------------------------------- C/E. 判定点关联候选、降级与词表缺口


# ------------------------------------------------- D. 结构不完整重试与落库口径


# ------------------------------------------------- F. 整题难度来自逐小问公式
