"""八上重打标签方案的单元契约测试（隔离合成库）。

覆盖：TagAnalysis 新字段归一、标准难度公式与存储、预测典型错法候选
替换、整题重打写路径、tag 投影的题型子类落地、判定点关联派生归属的
主小节/考试范围规则。全部使用 tmp_path 合成库，不触碰真实数据。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.models.question import QuestionCreate
from question_bank.models.tag_schema import (
    TagAnalysis,
    TaggingContext,
)
from question_bank.services import standard_difficulty
from question_bank.services.predicted_error_patterns import (
    PREDICTED_SOURCE,
    record_predicted_patterns,
)
from question_bank.services.question_read_service import (
    QuestionBankReadService,
    QuestionReadFilters,
)
from question_bank.services.question_write_service import (
    QuestionBankWriteService,
    _derived_ownership,
)
from question_bank.solution_evidence.knowledge_links import (
    refresh_question_scope_summary,
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
        (version_id, int(question_id), f"part-1-step-{index}", release_id,
         "direct", key, key, weight)
        for index, (key, weight) in enumerate(direct, start=1)
    ]
    link_rows.extend(
        (version_id, int(question_id),
         f"part-1-step-{len(link_rows) + offset}", release_id,
         "supporting_prerequisite", key, key, 0.5)
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


def _part_feature(**overrides: Any) -> dict[str, Any]:
    feature: dict[str, Any] = {
        "part_id": "part-1",
        "solo": 2,
        "reasoning": 1,
        "computation": 1,
        "context": 1,
        "context_kind": "生活情境",
        "hidden": 0,
        "cases": 0,
        "param_dynamic": 0,
        "trap": 0,
        "knowledge": 1,
        "evidence": "需要两步计算",
    }
    feature.update(overrides)
    return feature


# ---------------------------------------------------------------- tag_schema


def test_part_features_normalize_and_keep_context_kind() -> None:
    analysis = TagAnalysis.from_dict(
        _tag_analysis_payload(part_features=[_part_feature()])
    )

    assert len(analysis.part_features) == 1
    part = analysis.part_features[0]
    assert part["part_id"] == "part-1"
    assert part["context_kind"] == "生活情境"
    assert part["solo"] == 2 and "direct" not in part


def test_part_features_drop_invalid_entries() -> None:
    analysis = TagAnalysis.from_dict(
        _tag_analysis_payload(
            part_features=[
                _part_feature(part_id=""),  # 缺 part_id
                _part_feature(part_id="p-dup"),
                _part_feature(part_id="p-dup"),  # 重复 part_id
                _part_feature(part_id="p-range", solo=5),  # 越界
                _part_feature(
                    part_id="p-kind",
                    context=2,
                    context_kind="不存在类别",
                ),  # 非法情境词且 context>0
                _part_feature(part_id="p-ok"),
            ]
        )
    )

    assert [part["part_id"] for part in analysis.part_features] == [
        "p-dup",
        "p-ok",
    ]


def test_part_features_legacy_direct_key_is_ignored() -> None:
    """兼容输入：旧 payload 残留的 direct 键不丢小问，也不再进入结果。"""
    analysis = TagAnalysis.from_dict(
        _tag_analysis_payload(part_features=[_part_feature(direct=9)])
    )

    assert len(analysis.part_features) == 1
    part = analysis.part_features[0]
    assert part["part_id"] == "part-1"
    assert "direct" not in part


def test_part_features_model_schema_has_no_direct() -> None:
    from question_bank.services.ai_tagging_service import (
        _part_features_response_schema,
    )
    from question_bank.training_criteria.analysis import (
        _part_features_schema,
    )

    for schema in (_part_features_schema(), _part_features_response_schema()):
        properties = schema["items"]["properties"]
        assert "direct" not in properties
        assert "direct" not in schema["items"]["required"]


def test_part_features_context_zero_forces_no_context() -> None:
    analysis = TagAnalysis.from_dict(
        _tag_analysis_payload(
            part_features=[
                _part_feature(
                    part_id="p-1",
                    context=0,
                    context_kind="乱写的类别",
                )
            ]
        )
    )

    assert analysis.part_features[0]["context_kind"] == "无情境"


def test_ability_tags_capped_at_two() -> None:
    analysis = TagAnalysis.from_dict(
        _tag_analysis_payload(
            ability_tags=["运算能力", "推理能力", "空间观念"]
        )
    )

    assert analysis.ability_tags == ["运算能力", "推理能力"]


def test_predicted_error_patterns_contract() -> None:
    analysis = TagAnalysis.from_dict(
        _tag_analysis_payload(
            predicted_error_patterns=[
                {
                    "category": "计算与化简",
                    "pattern": "漏写负根",
                    "trigger_kind": "step",
                    "trigger_value": "part-1-step-2",
                },
                {
                    "category": "不存在的类别",
                    "pattern": "应被丢弃",
                    "trigger_kind": "observation",
                    "trigger_value": "",
                },
                {
                    "category": "审题与条件",
                    "pattern": "看错条件",
                    "trigger_kind": "bogus-kind",  # → observation + 空值
                    "trigger_value": "应被清空",
                },
                {
                    "category": "概念理解",
                    "pattern": "错法三",
                    "trigger_kind": "option",
                    "trigger_value": "B",
                },
                {
                    "category": "未作答",
                    "pattern": "错法四超出上限",
                    "trigger_kind": "observation",
                    "trigger_value": "",
                },
            ]
        )
    )

    patterns = analysis.predicted_error_patterns
    assert len(patterns) == 3  # 上限 3 且非法大类已丢弃
    assert patterns[0] == {
        "category": "计算与化简",
        "pattern": "漏写负根",
        "trigger_kind": "step",
        "trigger_value": "part-1-step-2",
    }
    assert patterns[1]["trigger_kind"] == "observation"
    assert patterns[1]["trigger_value"] == ""
    assert patterns[2]["pattern"] == "错法三"


# ------------------------------------------------------ standard_difficulty


def _difficulty_db(tmp_path: Path) -> Path:
    database = _base_db(tmp_path)
    with connect(database) as conn:
        conn.executescript(STAGED_SCHEMA.read_text(encoding="utf-8"))
    return database


def test_standard_difficulty_formula_and_max_part() -> None:
    # raw = (2-1)*1 + 1*1 + 1*0.75 + 1*0.5 + 0 + 0 + 0 + 0 + 1*0.5 = 3.75
    part_a = _part_feature(part_id="p1")
    part_b = _part_feature(
        part_id="p2",
        solo=4,
        reasoning=2,
        computation=2,
        context=2,
        hidden=2,
        cases=2,
        param_dynamic=1,
        trap=1,
        knowledge=2,
    )
    parts = standard_difficulty.build_part_records([part_a, part_b])

    assert parts[0]["raw"] == pytest.approx(3.75)
    assert parts[0]["formula"] == pytest.approx(
        round(1 + 9 * 3.75 / 13, 1)
    )
    assert parts[1]["raw"] == pytest.approx(13.0)  # 全特征满分
    assert parts[1]["formula"] == pytest.approx(10.0)

    formula = standard_difficulty.summarize_parts(parts)
    assert formula == pytest.approx(10.0)  # 整题取最难小问

    low = _part_feature(part_id="p3")
    assert standard_difficulty.summarize_parts(
        standard_difficulty.build_part_records([low])
    ) == pytest.approx(3.6)


def test_save_assessment_replaces_active_rows(tmp_path: Path) -> None:
    database = _difficulty_db(tmp_path)
    _add_question(database)
    with connect(database) as conn:
        first = standard_difficulty.save_assessment(
            conn,
            question_id=1,
            part_features=[_part_feature(part_id="part-1")],
            content_fingerprint="fp-v1",
            model_name="m1",
        )
        assert first is not None
        second = standard_difficulty.save_assessment(
            conn,
            question_id=1,
            part_features=[
                _part_feature(part_id="part-1"),
                _part_feature(part_id="part-2", solo=3),
            ],
            content_fingerprint="fp-v2",
            model_name="m2",
        )
        assert second is not None
        counts = conn.execute(
            "SELECT is_active, COUNT(*) FROM question_part_difficulty_features"
            " WHERE question_id = 1 GROUP BY is_active"
        ).fetchall()

    assert dict(counts) == {0: 1, 1: 2}

    loaded = standard_difficulty.load_assessment(
        database, 1, current_fingerprint="fp-v2"
    )
    assert loaded is not None
    assert loaded["formula_version"] == "std-difficulty-v1"
    assert loaded["needs_reevaluation"] is False
    assert [p["part_id"] for p in loaded["parts"]] == ["part-1", "part-2"]

    stale = standard_difficulty.load_assessment(
        database, 1, current_fingerprint="fp-v3"
    )
    assert stale is not None and stale["needs_reevaluation"] is True


def test_standard_difficulty_missing_table_is_noop(tmp_path: Path) -> None:
    database = _base_db(tmp_path)
    _add_question(database)
    with connect(database) as conn:
        conn.execute("DROP TABLE question_part_difficulty_features")
        conn.commit()
        assert (
            standard_difficulty.save_assessment(
                conn,
                question_id=1,
                part_features=[_part_feature()],
                content_fingerprint="fp",
            )
            is None
        )
    assert standard_difficulty.load_assessment(database, 1) is None


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
        _pattern_row(
            conn, pattern="教师确认错法", source="teacher", status="confirmed"
        )
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


def test_predicted_patterns_do_not_duplicate_non_predicted_rows(
    tmp_path: Path,
) -> None:
    database = _base_db(tmp_path)
    _add_question(database)
    with connect(database) as conn:
        _pattern_row(
            conn,
            pattern="已有教师错法",
            source="teacher",
            status="confirmed",
            trigger_kind="option",
            trigger_value="B",
        )
        inserted = record_predicted_patterns(
            conn,
            1,
            [
                {
                    "category": "计算与化简",
                    "pattern": "已有教师错法",
                    "trigger_kind": "option",
                    "trigger_value": "B",
                }
            ],
        )
        assert inserted == 0
        count = conn.execute(
            "SELECT COUNT(*) FROM question_error_patterns"
            " WHERE question_id = 1"
        ).fetchone()[0]
        assert count == 1


# ------------------------------------------------------- save_tag_analysis


def test_save_tag_analysis_retires_old_dimensions_and_derives(
    tmp_path: Path,
) -> None:
    database = _base_db(tmp_path)
    _add_question(database)
    _seed_evidence_links(database, 1)
    with connect(database) as conn:
        conn.executemany(
            "INSERT INTO question_tags (question_id, tag_type, tag_value,"
            " confidence, source) VALUES (1,?,?,0.8,?)",
            (
                ("knowledge_point", "kp_old", "ai"),
                ("error_type", "旧错误类型", "ai"),
                ("exam_scope", "旧范围", "taxonomy"),
                ("knowledge_point", "kp_manual", "manual"),
                ("error_type", "教师错误类型", "manual"),
            ),
        )

    writer = QuestionBankWriteService(database, data_root=database.parent)
    saved = writer.save_tag_analysis(
        1,
        TagAnalysis.from_dict(
            _tag_analysis_payload(
                special_type_tags=["计算"],
                predicted_error_patterns=[
                    {
                        "category": "计算与化简",
                        "pattern": "漏写负根",
                        "trigger_kind": "step",
                        "trigger_value": "part-1-step-1",
                    }
                ],
            )
        ),
        model_name="synthetic-model",
    )
    assert saved is True

    with connect(database) as conn:
        tags = {
            (row["tag_type"], row["tag_value"], row["source"])
            for row in conn.execute(
                "SELECT tag_type, tag_value, source FROM question_tags"
                " WHERE question_id = 1"
            )
        }
        difficulty = conn.execute(
            "SELECT difficulty FROM questions WHERE id = 1"
        ).fetchone()[0]
        predicted = [
            dict(row)
            for row in conn.execute(
                "SELECT status, source FROM question_error_patterns"
                " WHERE question_id = 1"
            ).fetchall()
        ]

    # 旧 AI/非手工维度被清掉；手工行保留；归属由判定点关联重新派生。
    assert ("knowledge_point", "kp_old", "ai") not in tags
    assert ("error_type", "旧错误类型", "ai") not in tags
    assert ("exam_scope", "旧范围", "taxonomy") not in tags
    assert ("knowledge_point", "kp_manual", "manual") in tags
    assert ("error_type", "教师错误类型", "manual") in tags
    assert ("special_type", "计算", "ai") in tags
    assert ("exam_scope", _SECTION_SCOPE, "taxonomy") in tags
    assert ("exam_scope", _SKILL_SCOPE, "taxonomy") not in tags  # 章级技能不占范围
    assert ("curriculum_section", "bnu24-math-g8-upper-c01-s01",
            "taxonomy") in tags
    assert ("knowledge_point", _SKILL_KEY, "taxonomy") in tags
    assert ("knowledge_point", _SECTIONED_KEY, "taxonomy") in tags
    assert difficulty == "5"  # 旧 difficulty 字段照旧更新
    assert predicted == [
        {"status": "candidate", "source": PREDICTED_SOURCE}
    ]


# ------------------------------------------------------------------ defect 1


class _RecordingTagWriter:
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
        self.calls.append((question.question_id, operation_id))
        return {
            "schema_version": "tag-only-v1",
            "analysis": analysis.to_dict(),
            "model_name": model_name,
        }


class _QueueGateway:
    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self.responses = list(responses)
        self.batches: list[Any] = []

    def analyze(
        self,
        batch: Any,
        *,
        projection: str,
        operation_id: str,
        request_id: str,
    ) -> GatewayBatchResponse:
        self.batches.append(batch)
        return GatewayBatchResponse(
            payload=self.responses.pop(0),
            model_name="synthetic-model",
            usage=GatewayUsage(10, 5, 15),
            latency_ms=1,
        )


class _RecordingTypeWriter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def apply(
        self,
        question: QuestionAnalysisInput,
        suggestion: Any,
        *,
        model_name: str,
        operation_id: str,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "question_id": question.question_id,
                "question_type": suggestion.question_type,
                "essay_subtype": suggestion.essay_subtype,
            }
        )
        return {"action": "unchanged", "subtype_action": "applied"}


def _analysis_question(
    question_id: int, *, question_type: str
) -> QuestionAnalysisInput:
    return QuestionAnalysisInput(
        question_id=question_id,
        tagging_context=TaggingContext(
            question_text="合成题干",
            answer_text="合成答案",
            question_number=str(question_id),
            question_type=question_type,
        ),
        taxonomy_contract={
            "taxonomy_revision": 9,
            "allowed_term_ids": {},
            "candidates": {},
        },
    )


def _run_tag_projection(
    tmp_path: Path,
    *,
    local_type: str,
    suggestion: dict[str, Any],
) -> tuple[Mapping[str, Any], _RecordingTypeWriter]:
    database = _base_db(tmp_path)
    _add_question(database)
    gateway = _QueueGateway(
        [
            {
                "results": [
                    {
                        "question_id": 1,
                        "tag_analysis": _tag_analysis_payload(),
                        "question_type_suggestion": suggestion,
                    }
                ]
            }
        ]
    )
    type_writer = _RecordingTypeWriter()
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=_RecordingTagWriter(),
        question_type_writer=type_writer,
    )
    outcome = module.analyze(
        operation_id="retag-type-test",
        questions=(_analysis_question(1, question_type=local_type),),
        projection="tag",
    )
    return outcome, type_writer


def test_tag_projection_applies_subtype_when_type_matches(
    tmp_path: Path,
) -> None:
    outcome, type_writer = _run_tag_projection(
        tmp_path,
        local_type="解答题",
        suggestion={
            "question_type": "解答题",
            "essay_subtype": "计算",
            "reason": "需要列式计算",
        },
    )

    assert type_writer.calls == [
        {
            "question_id": 1,
            "question_type": "解答题",
            "essay_subtype": "计算",
        }
    ]
    item = outcome["items"][0]
    assert item["tag_status"] == "succeeded"


def test_tag_projection_does_not_change_type_when_mismatch(
    tmp_path: Path,
) -> None:
    outcome, type_writer = _run_tag_projection(
        tmp_path,
        local_type="解答题",
        suggestion={
            "question_type": "选择题",
            "reason": "模型判断为客观题",
        },
    )

    assert type_writer.calls == []
    item = outcome["items"][0]
    assert item["tag_status"] == "succeeded"
    assert "不改动题型" in str(item.get("question_type_suggestion_note") or "")


# ------------------------------------------------------------------ defect 2


def test_scope_summary_primary_prefers_sectioned_key(tmp_path: Path) -> None:
    database = _base_db(tmp_path)
    _add_question(database)
    _seed_evidence_links(database, 1)
    with connect(database) as conn:
        refresh_question_scope_summary(conn, 1, db_path=database)
        row = conn.execute(
            "SELECT primary_section_id, direct_section_ids_json"
            " FROM question_scope_summary WHERE question_id = 1"
        ).fetchone()

    assert row is not None
    # 平票时章级技能键（字典序更大）曾抢走主小节；现在只在小节键中选。
    assert row["primary_section_id"] == _SECTION_KEY
    assert json.loads(row["direct_section_ids_json"]) == [_SECTION_KEY]


def test_derived_ownership_excludes_chapter_only_scope(tmp_path: Path) -> None:
    database = _base_db(tmp_path)
    _add_question(database)
    _seed_evidence_links(database, 1)
    with connect(database) as conn:
        derived = _derived_ownership(conn, database, 1)

    assert derived is not None
    assert derived["exam_scope"] == [_SECTION_SCOPE]
    assert _SKILL_SCOPE not in derived["exam_scope"]
    assert derived["curriculum_section"] == ["bnu24-math-g8-upper-c01-s01"]
    # 章级技能仍是 knowledge_point 直接键。
    assert _SKILL_KEY in derived["direct_keys"]
    assert _SECTIONED_KEY in derived["direct_keys"]


# ------------------------------------------------- A. 仅打标签请求的裁剪


def _projection_question() -> QuestionAnalysisInput:
    return QuestionAnalysisInput(
        question_id=1,
        tagging_context=TaggingContext(
            question_text="合成题干",
            answer_text="合成答案",
            question_number="1",
            question_type="解答题",
        ),
        taxonomy_contract={
            "taxonomy_revision": 9,
            "allowed_term_ids": {
                "knowledge": [_SECTIONED_KEY],
                "ability": ["ab-1"],
            },
            "candidates": {
                "knowledge": [
                    {"id": _SECTIONED_KEY, "name": "勾股定理的验证"},
                    {"id": _SKILL_KEY, "name": "新定义技能"},
                ],
                "ability": [{"id": "ab-1", "name": "运算能力"}],
            },
            "curriculum_volume": {
                "id": "bnu24-math-g8-upper",
                "label": "八年级上册",
                "sections": [{"id": "s-1", "name": "勾股定理"}],
            },
        },
    )


def _projection_payload(projection: str) -> dict[str, Any]:
    from question_bank.training_criteria.adapters import _combined_prompt
    from question_bank.training_criteria.analysis import PlannedAnalysisBatch

    batch = PlannedAnalysisBatch(
        questions=(_projection_question(),),
        estimated_input_tokens=10,
        estimated_output_tokens=10,
    )
    messages = _combined_prompt(batch, projection)
    user = next(
        content["text"]
        for content in messages[1]["content"]
        if content["type"] == "input_text" and content["text"].startswith("{")
    )
    return json.loads(user)


def test_tag_only_request_drops_knowledge_candidates_and_evidence_rules() -> None:
    payload = _projection_payload("tag")
    contract = payload["questions"][0]["candidate_contract"]

    assert "knowledge" not in contract.get("candidates", {})
    assert "curriculum_volume" not in contract
    assert "evidence_examples" not in payload
    rules = payload["rules"]
    assert "fine_term_links" not in rules
    assert "solution_evidence" not in rules
    # part_features 仍依赖本地判定点 part_id。
    assert "question.evidence_parts" in rules
    assert "predicted_error_patterns" in rules
    assert "part_features" in rules


def test_combined_request_keeps_knowledge_candidates_and_evidence_rules() -> None:
    payload = _projection_payload("both")
    contract = payload["questions"][0]["candidate_contract"]

    assert [t["id"] for t in contract["candidates"]["knowledge"]] == [
        _SECTIONED_KEY,
        _SKILL_KEY,
    ]
    assert "curriculum_volume" in contract
    assert "evidence_examples" in payload
    assert "fine_term_links" in payload["rules"]


def test_tag_schema_has_no_prerequisite_points_in_any_projection() -> None:
    from question_bank.training_criteria import combined_response_format

    for projection in ("tag", "both"):
        schema = combined_response_format(projection)["schema"]
        results = schema["properties"]["results"]["items"]
        tag_props = results["properties"]["tag_analysis"]["properties"]
        assert "prerequisite_points" not in tag_props
        assert "knowledge_points" not in tag_props

    # combined 投影仍要求判定点链接（fine_term_links）的受控知识候选。
    combined_schema = combined_response_format("both")["schema"]
    evidence_props = combined_schema["properties"]["results"]["items"][
        "properties"
    ]["solution_evidence"]["properties"]["parts"]["items"]["properties"][
        "evidence_points"
    ]["items"]["properties"]
    assert "fine_term_links" in evidence_props
    tag_schema = combined_response_format("tag")["schema"]
    assert "solution_evidence" not in tag_schema["properties"]["results"][
        "items"
    ]["properties"]


# ------------------------------------------------- B. 前置知识由判定点关联派生


def test_derived_ownership_includes_supporting_prerequisite_keys(
    tmp_path: Path,
) -> None:
    database = _base_db(tmp_path)
    _add_question(database)
    _seed_evidence_links(
        database, 1, supporting=(_EARLIER_SECTION_KEY, _EARLIER_SECTION_KEY)
    )
    with connect(database) as conn:
        derived = _derived_ownership(conn, database, 1)

    assert derived is not None
    assert derived["prerequisite_keys"] == [_EARLIER_SECTION_KEY]
    assert _EARLIER_SECTION_KEY not in derived["direct_keys"]


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


def test_teaching_progress_filter_accepts_derived_prerequisite_keys(
    tmp_path: Path,
) -> None:
    """前置知识的稳定键与 knowledge_point 同口径参与教学进度过滤。"""
    database = _base_db(tmp_path)
    _add_question(database)
    _add_question(database, question_id=2, question_number="2")
    with connect(database) as conn:
        conn.executemany(
            "INSERT INTO question_tags (question_id, tag_type, tag_value,"
            " confidence, source) VALUES (?, ?, ?, 1.0, 'taxonomy')",
            [
                # 题 1：前置键在更早册别（七上）→ 已学；范围章 ≤ 第二章 → 放行。
                (1, "prerequisite", _EARLIER_SECTION_KEY),
                (1, "exam_scope", _SECTION_SCOPE),
                (1, "ability", "运算能力"),
                # 题 2：前置键落在八上第四章，晚于教学进度上限第二章 → 拦截。
                (2, "prerequisite", _LATER_CHAPTER_SECTION_KEY),
                (2, "exam_scope", _SECTION_SCOPE),
                (2, "ability", "运算能力"),
            ],
        )
    service = QuestionBankReadService(database, data_root=tmp_path)
    page = service.list_questions(
        QuestionReadFilters(
            teaching_progress_chapter="bnu24-math-g8-upper-c02"
        )
    )
    assert [item["id"] for item in page.items] == [1]


def test_public_detail_shows_derived_prerequisite_display_name(
    tmp_path: Path,
) -> None:
    database = _base_db(tmp_path)
    _add_question(database)
    _seed_evidence_links(database, 1, supporting=(_EARLIER_SECTION_KEY,))
    writer = QuestionBankWriteService(database, data_root=tmp_path)
    writer.save_tag_analysis(
        1,
        TagAnalysis.from_dict(_tag_analysis_payload()),
        model_name="synthetic-model",
    )
    from question_bank.current_knowledge import CurrentKnowledgeResolver

    resolver = CurrentKnowledgeResolver.from_active_database(database)
    term = resolver.canonical_term(_EARLIER_SECTION_KEY)
    assert term is not None

    service = QuestionBankReadService(database, data_root=tmp_path)
    detail = service.get_question(1)
    assert detail is not None
    prerequisite_tags = [
        tag for tag in detail["tags"] if tag["tag_type"] == "prerequisite"
    ]
    # 公开读取与 knowledge_point 同口径：稳定键翻译为当前词表显示名。
    assert [tag["tag_value"] for tag in prerequisite_tags] == [term[1]]


# -------------------------------------------- C/E. 判定点关联候选、降级与词表缺口


def test_link_job_earlier_volume_sections_downgrade_and_vocabulary_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """更早册别小节进入候选；模型标 direct 会降级为前置；空链接记入词表缺口。"""
    from backend.jobs.knowledge_link_job import run_knowledge_link_job
    from question_bank.training_criteria.adapters import (
        QuestionAnalysisInputLoader,
    )

    database = _base_db(tmp_path)
    _add_question(database)
    # 判定点版本（无既有链接，regenerate 全覆盖）。
    _seed_evidence_links(database, 1, direct=(), supporting=())
    evidence = {
        "parts": [
            {
                "part_id": "part-1",
                "evidence_points": [
                    {"evidence_point_id": "p1", "target": "应用勾股定理"},
                    {"evidence_point_id": "p2", "target": "写出结果"},
                ],
            }
        ]
    }
    with connect(database) as conn:
        conn.execute(
            "UPDATE question_solution_evidence_versions SET evidence_json = ?",
            (json.dumps(evidence),),
        )

    def _fake_load(self: Any, question_ids: Any) -> tuple[Any, ...]:
        return tuple(
            SimpleNamespace(
                question_id=int(question_id),
                tagging_context=TaggingContext(
                    question_text="合成题干",
                    answer_text="合成答案",
                    question_number=str(question_id),
                    question_type="解答题",
                    curriculum_volume_id="bnu24-math-g8-upper",
                ),
            )
            for question_id in question_ids
        )

    monkeypatch.setattr(QuestionAnalysisInputLoader, "load", _fake_load)

    governance = SimpleNamespace(
        prompt_contracts=lambda contexts: {
            int(question_id): {
                "candidates": {
                    "knowledge": [
                        {
                            "id": _SECTION_KEY,
                            "name": "勾股定理",
                            "usage": "direct_core",
                        }
                    ]
                }
            }
            for question_id in contexts
        }
    )
    requests: list[Mapping[str, Any]] = []

    def _gateway(request: Mapping[str, Any]) -> Mapping[int, Any]:
        requests.append(request)
        return {
            1: [
                {
                    "evidence_point_id": "p1",
                    "links": [
                        # 更早册别小节被模型误标 direct → 本地降级保留。
                        {
                            "fine_term_id": _EARLIER_SECTION_KEY,
                            "role": "direct",
                        },
                        {"fine_term_id": _SECTION_KEY, "role": "direct"},
                    ],
                },
                # p2 空链接 → 词表缺口。
                {"evidence_point_id": "p2", "links": []},
            ]
        }

    context = SimpleNamespace(
        payload={"mode": "regenerate", "question_ids": [1]},
        job_id=1,
        report=lambda *args: None,
        raise_if_cancelled=lambda: None,
    )
    result = run_knowledge_link_job(
        context=context,
        question_bank_db_path=database,
        data_root=tmp_path,
        link_gateway=_gateway,
        taxonomy_governance=governance,
    )

    assert len(requests) == 1
    candidate_ids = {
        item["id"] for item in requests[0]["questions"][0]["candidates"]
    }
    # 七上小节作为“用到的前置知识”候选进入请求。
    assert _EARLIER_SECTION_KEY in candidate_ids
    assert _SECTION_KEY in candidate_ids

    assert result["downgraded_links"] == [
        {
            "question_id": 1,
            "evidence_point_id": "p1",
            "stable_key": _EARLIER_SECTION_KEY,
            "reason_code": "earlier_volume_section",
        }
    ]
    assert result["vocabulary_gap_points"] == [
        {
            "question_id": 1,
            "evidence_point_id": "p2",
            "target": "写出结果",
        }
    ]
    with connect(database) as conn:
        roles = {
            (row["evidence_point_id"], row["stable_key"]): row["role"]
            for row in conn.execute(
                "SELECT evidence_point_id, stable_key, role"
                " FROM evidence_point_knowledge_links WHERE question_id = 1"
            )
        }
    assert roles[("p1", _EARLIER_SECTION_KEY)] == "supporting_prerequisite"
    assert roles[("p1", _SECTION_KEY)] == "direct"


# ------------------------------------------------- D. 结构不完整重试与落库口径


class _StructuralTagWriter:
    """模拟 tag 投影写入端：缺能力标签视为结构性失败。"""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def write(
        self,
        question: QuestionAnalysisInput,
        payload: Mapping[str, Any],
        *,
        model_name: str,
        operation_id: str,
    ) -> Mapping[str, Any]:
        analysis = TagAnalysis.from_dict(dict(payload))
        if not analysis.ability_tags:
            raise ValueError("缺少能力标签")
        self.calls.append(int(question.question_id))
        return {
            "schema_version": "tag-only-v1",
            "analysis": analysis.to_dict(),
        }


def _bad_tag_response() -> dict[str, Any]:
    return {
        "results": [
            {
                "question_id": 1,
                "tag_analysis": {
                    **_tag_analysis_payload(),
                    "ability_tags": [],
                },
            }
        ]
    }


def _good_tag_response() -> dict[str, Any]:
    return {
        "results": [
            {
                "question_id": 1,
                "tag_analysis": _tag_analysis_payload(),
            }
        ]
    }


def test_structurally_invalid_tag_result_retries_once_then_succeeds(
    tmp_path: Path,
) -> None:
    database = _base_db(tmp_path)
    _add_question(database)
    gateway = _QueueGateway([_bad_tag_response(), _good_tag_response()])
    gateway.max_auto_retries = 1
    writer = _StructuralTagWriter()
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=writer,
    )
    outcome = module.analyze(
        operation_id="retag-retry-test",
        questions=(_analysis_question(1, question_type="解答题"),),
        projection="tag",
    )
    item = outcome["items"][0]
    assert item["tag_status"] == "succeeded"
    assert len(gateway.batches) == 2
    repair = gateway.batches[1].questions[0].repair_context
    assert repair["mode"] == "repair_previous_rejected_result"


def test_structurally_invalid_tag_result_without_budget_is_failed(
    tmp_path: Path,
) -> None:
    database = _base_db(tmp_path)
    _add_question(database)
    gateway = _QueueGateway([_bad_tag_response()])
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=_StructuralTagWriter(),
    )
    outcome = module.analyze(
        operation_id="retag-no-budget-test",
        questions=(_analysis_question(1, question_type="解答题"),),
        projection="tag",
    )
    item = outcome["items"][0]
    # 结构不完整结果是普通可重试失败，不是教师复核队列项。
    assert item["tag_status"] == "failed"
    assert len(gateway.batches) == 1


# ------------------------------------------------- F. 整题难度来自逐小问公式


def _part_feature_with_features(
    part_id: str,
    *,
    solo: int,
    reasoning: int = 0,
    computation: int = 0,
    context: int = 0,
    hidden: int = 0,
    cases: int = 0,
    param_dynamic: int = 0,
    trap: int = 0,
    knowledge: int = 0,
) -> dict[str, Any]:
    return {
        "part_id": part_id,
        "solo": solo,
        "reasoning": reasoning,
        "computation": computation,
        "context": context,
        "context_kind": "无情境",
        "hidden": hidden,
        "cases": cases,
        "param_dynamic": param_dynamic,
        "trap": trap,
        "knowledge": knowledge,
        "evidence": "合成依据",
    }


def test_save_tag_analysis_derives_difficulty_from_part_formulas(
    tmp_path: Path,
) -> None:
    """逐小问公式 4.6 / 6.0 → 整题难度取最难小问（而非平均）= "6.0"。"""
    from tests.current_knowledge_support import install_current_knowledge

    database = _base_db(tmp_path)
    install_current_knowledge(database, taxonomy_revision=9)
    _add_question(database)
    analysis = TagAnalysis.from_dict(
        _tag_analysis_payload(
            part_features=[
                # raw = 2+1+0.75+0.5+1.0 = 5.25 → formula 4.6
                _part_feature_with_features(
                    "part-1", solo=3, reasoning=1, computation=1,
                    context=1, knowledge=2,
                ),
                # raw = 3+2+0.75+0.5+1.0 = 7.25 → formula 6.0
                _part_feature_with_features(
                    "part-2", solo=4, reasoning=2, computation=1,
                    context=1, knowledge=2,
                ),
            ],
            difficulty=3,  # 模型整题难度更低：须被公式结果覆盖
        )
    )
    writer = QuestionBankWriteService(database, data_root=tmp_path)
    writer.save_tag_analysis(1, analysis, model_name="synthetic-model")
    with connect(database) as conn:
        row = conn.execute(
            "SELECT difficulty FROM questions WHERE id = 1"
        ).fetchone()
    assert row["difficulty"] == "6.0"


def test_save_tag_analysis_difficulty_keeps_decimal(tmp_path: Path) -> None:
    """公式 8.8 原样存为 "8.8"，不取整不四舍五入。"""
    from tests.current_knowledge_support import install_current_knowledge

    database = _base_db(tmp_path)
    install_current_knowledge(database, taxonomy_revision=9)
    _add_question(database)
    analysis = TagAnalysis.from_dict(
        _tag_analysis_payload(
            part_features=[
                # raw = 3+2+1.5+0.5+2.0+0.75+0.5+0.5+1.0 = 11.75
                # → formula 1 + 9*11.75/13 ≈ 9.1
                _part_feature_with_features(
                    "part-1", solo=4, reasoning=2, computation=2,
                    context=1, hidden=2, cases=1, param_dynamic=1,
                    trap=1, knowledge=2,
                ),
            ],
            difficulty=3,
        )
    )
    writer = QuestionBankWriteService(database, data_root=tmp_path)
    writer.save_tag_analysis(1, analysis, model_name="synthetic-model")
    with connect(database) as conn:
        row = conn.execute(
            "SELECT difficulty FROM questions WHERE id = 1"
        ).fetchone()
    # 9.1346… → formula_score 一位小数 9.1，整题难度原样存 "9.1"。
    assert row["difficulty"] == "9.1"


def test_difficulty_level_half_up_classification() -> None:
    """分类用最近整数档（半上）：6.4→6、6.5→7、7.5→8；越界/非法返回 None。"""
    from question_bank.services.standard_difficulty import difficulty_level

    assert difficulty_level("6.4") == 6
    assert difficulty_level(6.5) == 7
    assert difficulty_level("7.4") == 7
    assert difficulty_level(7.5) == 8
    assert difficulty_level(1.0) == 1
    assert difficulty_level(10.0) == 10
    assert difficulty_level("7") == 7
    assert difficulty_level(0.9) is None
    assert difficulty_level(10.1) is None
    assert difficulty_level("abc") is None
    assert difficulty_level(None) is None


def test_read_filters_difficulty_decimal_semantics(tmp_path: Path) -> None:
    """范围闭区间比较原始小数；精确难度归入最近整数档。"""
    database = _base_db(tmp_path)
    for qid, number, difficulty in (
        (1, "1", "6.9"),
        (2, "2", "7.0"),
        (3, "3", "10.0"),
        (4, "4", "4.2"),
        (5, "5", "3.9"),
        (6, "6", "4.0"),
        (7, "7", "6.0"),
        (8, "8", "6.1"),
        (9, "9", "6.5"),
        (10, "10", "7.4"),
        (11, "11", "7.5"),
        (12, "12", "6.4"),
    ):
        _add_question(database, question_id=qid, question_number=number)
        with connect(database) as conn:
            conn.execute(
                "UPDATE questions SET difficulty = ? WHERE id = ?",
                (difficulty, qid),
            )
    service = QuestionBankReadService(database, data_root=tmp_path)

    # 闭区间 [4,6]：含 4.0/4.2/6.0，不含 3.9/6.1/6.9。
    page = service.list_questions(
        QuestionReadFilters(difficulty_min=4, difficulty_max=6)
    )
    assert sorted(item["id"] for item in page.items) == [4, 6, 7]

    # 闭区间 [6.5,10]：含 6.5/6.9/7.0/7.4/7.5/10.0。
    page = service.list_questions(
        QuestionReadFilters(difficulty_min=6.5, difficulty_max=10)
    )
    assert sorted(item["id"] for item in page.items) == [1, 2, 3, 9, 10, 11]

    # 精确难度是档位：7 命中 6.5–7.4（含 6.9、7.0），不含 6.4/7.5。
    from question_bank.services.question_read_service import (
        build_question_filter_query,
    )

    joins, where, params = build_question_filter_query(difficulty="7")
    with connect(database) as conn:
        ids = {
            row["id"]
            for row in conn.execute(
                "SELECT q.id FROM questions q "
                + " ".join(joins)
                + " WHERE "
                + " AND ".join(where),
                params,
            )
        }
    assert ids == {1, 2, 9, 10}

    # 非数值输入沿用原文精确匹配。
    joins, where, params = build_question_filter_query(difficulty="难")
    assert where[-1] == "q.difficulty = ?" and params[-1] == "难"


def test_difficulty_band_classifies_by_nearest_level() -> None:
    """推荐侧档位按最近整数：3.4→3 档（starter），3.5→4 档。"""
    from question_bank.recommendation.personalized import (
        _difficulty,
        _difficulty_band,
    )

    assert _difficulty("6.9") == 6.9
    assert _difficulty("10.0") == 10.0
    plan = {"starter": 3, "consolidation": 4, "ratios": {}}
    starter = {
        "candidate": {"difficulty": "3.4"},
        "target": {"difficulty_plan": plan},
    }
    assert _difficulty_band(starter) == "starter"
    not_starter = {
        "candidate": {"difficulty": "3.5"},
        "target": {"difficulty_plan": plan},
    }
    assert _difficulty_band(not_starter) == "consolidation"


def test_pool_cap_compares_raw_decimal() -> None:
    """教师上限是原始小数比较：上限 7 排除 7.3。"""
    from question_bank.recommendation.personalized import (
        PersonalizedRecommendationConfig,
        PersonalizedRecommendationModule,
    )

    config = PersonalizedRecommendationConfig(difficulty_max=7)
    module = PersonalizedRecommendationModule.__new__(
        PersonalizedRecommendationModule
    )
    module.current_knowledge = None
    candidates = [
        {"question_id": 1, "difficulty": 7.3,
         "stable_keys": ["k1"], "question_type": "选择题"},
        {"question_id": 2, "difficulty": 7.0,
         "stable_keys": ["k1"], "question_type": "选择题"},
    ]
    result = module._eligible_candidates(
        candidates, stage="direct", target_keys=(), maintenance=True,
        used=set(), recent=set(), excluded=set(), config=config,
        allowed_keys=None, paper_level_max=None,
    )
    assert [item["question_id"] for item in result] == [2]


def test_teaching_progress_allows_activity_skills_but_not_activity_sections() -> None:
    """综合与实践章下的跨章节技能（运用题设新定义）不随章序解锁。"""
    from question_bank.taxonomy.curriculum_catalog import (
        teaching_progress_allowed_stable_keys,
    )

    allowed = teaching_progress_allowed_stable_keys("bnu24-math-g8-upper-c04")
    assert allowed is not None
    exact, prefixes = allowed

    def is_allowed(key: str) -> bool:
        return key in exact or any(key.startswith(prefix) for prefix in prefixes)

    assert is_allowed(_SKILL_KEY)
    assert is_allowed(_LATER_CHAPTER_SECTION_KEY)
    assert not is_allowed("kp_bnu24_math_g8_upper_8_1")
    assert not is_allowed("kp_bnu24_math_g8_upper_5_1")
    assert not is_allowed("sk_bnu24_math_g8_upper_5_1_201")
