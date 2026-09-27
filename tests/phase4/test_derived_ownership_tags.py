"""Derived ownership tags: exam_scope/curriculum_section from evidence links."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.models.tag_schema import TagAnalysis
from question_bank.services.question_write_service import (
    QuestionBankWriteService,
    refresh_derived_ownership_tags,
)
from tests.current_knowledge_support import install_current_knowledge

_SECTION_KEY = "kp_bnu24_math_g8_upper_1_1"
_SECTION_ID = "bnu24-math-g8-upper-c01-s01"
_CHAPTER_SCOPE = "八年级上册 第一章 勾股定理"
_LEAF_KEY = "kp_bnu24_math_g8_upper_1_1_1"
_SKILL_KEY = "sk_bnu24_math_g8_upper_1_1_01"
_VERSION = "d" * 64


def _setup(tmp_path: Path) -> tuple[Path, str, QuestionBankWriteService]:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    release_id = install_current_knowledge(db_path, taxonomy_revision=5)
    writer = QuestionBankWriteService(db_path, data_root=tmp_path)
    return db_path, release_id, writer


def _seed_question(
    db_path: Path,
    release_id: str,
    links: list[tuple[str, str, str, str]] | None,
    *,
    question_id: int = 1,
) -> None:
    """links: (point_id, role, stable_key, resolution_status); None → no version."""
    with connect(db_path) as connection:
        connection.execute(
            "INSERT INTO papers(id,title,import_status) VALUES(1,'合成','ready')"
        )
        connection.execute(
            "INSERT INTO questions(id,paper_id,question_number,question_text)"
            " VALUES(?,1,'1','合成题')",
            (question_id,),
        )
        if links is None:
            return
        connection.execute(
            """
            INSERT INTO question_solution_evidence_versions(
                evidence_version_id, question_id, source_content_hash,
                schema_version, content_hash, evidence_json, status,
                source_kind, source_reference, created_by, graph_release_id
            ) VALUES (?, ?, ?, 'question-solution-evidence-v2', ?, ?,
                      'approved', 'backfill', 'synthetic', 'test', ?)
            """,
            (
                _VERSION,
                question_id,
                "e" * 64,
                "f" * 64,
                json.dumps(
                    {
                        "parts": [
                            {
                                "part_id": "part-1",
                                "evidence_points": [
                                    {"evidence_point_id": point_id}
                                    for point_id, *_ in links
                                ],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                release_id,
            ),
        )
        connection.executemany(
            """
            INSERT INTO evidence_point_knowledge_links(
                evidence_version_id, question_id, part_id, evidence_point_id,
                graph_release_id, role, term_id, stable_key,
                resolution_status, weight, source_kind, source_reference
            ) VALUES (?, ?, 'part-1', ?, ?, ?, ?, ?, ?, 1.0,
                      'link_job', 'synthetic')
            """,
            [
                (
                    _VERSION,
                    question_id,
                    point_id,
                    release_id,
                    role,
                    stable_key,
                    stable_key,
                    status,
                )
                for point_id, role, stable_key, status in links
            ],
        )


def _analysis(**overrides) -> TagAnalysis:
    payload = {
        "method_tags": ["合成方法"],
        "thought_tags": [],
        "ability_tags": [],
        "math_model_tags": [],
        "special_type_tags": [],
        "difficulty": 3,
        "predicted_error_patterns": [],
        "part_features": [],
        "reason": "合成分析",
        "confidence": 0.8,
    }
    payload.update(overrides)
    return TagAnalysis.from_dict(payload)


def _tags(db_path: Path, question_id: int = 1) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        return connection.execute(
            """
            SELECT tag_type, tag_value, source, model_name
            FROM question_tags WHERE question_id = ? ORDER BY id
            """,
            (question_id,),
        ).fetchall()


def _values(rows: list[sqlite3.Row], tag_type: str) -> list[str]:
    return [
        str(row["tag_value"]) for row in rows if row["tag_type"] == tag_type
    ]


def test_direct_skill_link_derives_section_and_chapter(tmp_path: Path) -> None:
    db_path, release_id, writer = _setup(tmp_path)
    _seed_question(
        db_path, release_id, [("p1", "direct", _SKILL_KEY, "resolved")]
    )

    assert writer.save_tag_analysis(1, _analysis())

    rows = _tags(db_path)
    assert _values(rows, "exam_scope") == [_CHAPTER_SCOPE]
    assert _values(rows, "curriculum_section") == [_SECTION_ID]
    assert _SKILL_KEY in _values(rows, "knowledge_point")
    # 归属只来自链接派生，canonical_knowledge_id 停写。
    assert "七年级下册 第九章 不等式" not in _values(rows, "exam_scope")
    assert "fake-section-id" not in _values(rows, "curriculum_section")
    assert _values(rows, "canonical_knowledge_id") == []
    assert _values(rows, "tag_status") == []


def test_direct_section_link_derives_self_and_chapter(tmp_path: Path) -> None:
    db_path, release_id, writer = _setup(tmp_path)
    _seed_question(
        db_path, release_id, [("p1", "direct", _SECTION_KEY, "resolved")]
    )

    assert writer.save_tag_analysis(1, _analysis())

    rows = _tags(db_path)
    assert _values(rows, "exam_scope") == [_CHAPTER_SCOPE]
    assert _values(rows, "curriculum_section") == [_SECTION_ID]
    assert _SECTION_KEY in _values(rows, "knowledge_point")


def test_direct_leaf_link_derives_section_and_chapter(tmp_path: Path) -> None:
    db_path, release_id, writer = _setup(tmp_path)
    _seed_question(
        db_path, release_id, [("p1", "direct", _LEAF_KEY, "resolved")]
    )

    assert writer.save_tag_analysis(1, _analysis())

    rows = _tags(db_path)
    assert _values(rows, "exam_scope") == [_CHAPTER_SCOPE]
    assert _values(rows, "curriculum_section") == [_SECTION_ID]


def test_mixed_skill_and_leaf_links_dedupe(tmp_path: Path) -> None:
    db_path, release_id, writer = _setup(tmp_path)
    _seed_question(
        db_path,
        release_id,
        [
            ("p1", "direct", _SKILL_KEY, "resolved"),
            ("p2", "direct", _LEAF_KEY, "resolved"),
        ],
    )

    assert writer.save_tag_analysis(1, _analysis())

    rows = _tags(db_path)
    assert _values(rows, "exam_scope") == [_CHAPTER_SCOPE]
    assert _values(rows, "curriculum_section") == [_SECTION_ID]
    knowledge_values = _values(rows, "knowledge_point")
    assert _SKILL_KEY in knowledge_values
    assert _LEAF_KEY in knowledge_values
    assert len(knowledge_values) == len(set(knowledge_values))


def test_prerequisite_only_links_mark_pending(tmp_path: Path) -> None:
    db_path, release_id, writer = _setup(tmp_path)
    _seed_question(
        db_path,
        release_id,
        [("p1", "supporting_prerequisite", _LEAF_KEY, "resolved")],
    )
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO question_tags(question_id, tag_type, tag_value, source)
            VALUES (1, 'exam_scope', '模型旧章值', 'ai'),
                   (1, 'curriculum_section', 'old-section-id', 'ai')
            """
        )

    assert writer.save_tag_analysis(1, _analysis())

    rows = _tags(db_path)
    # 无 direct 链接：旧的非人工归属值被清掉，模型不再提供归属值，
    # 只加 derived_pending 标记等待判定点关联。
    assert _values(rows, "exam_scope") == []
    assert _values(rows, "curriculum_section") == []
    assert _values(rows, "tag_status") == ["derived_pending"]
    assert _LEAF_KEY not in _values(rows, "knowledge_point")


def test_missing_evidence_clears_ownership_and_marks_pending(
    tmp_path: Path,
) -> None:
    db_path, _release_id, writer = _setup(tmp_path)
    _seed_question(db_path, _release_id, None)

    assert writer.save_tag_analysis(1, _analysis())

    rows = _tags(db_path)
    assert _values(rows, "exam_scope") == []
    assert _values(rows, "curriculum_section") == []
    assert _values(rows, "tag_status") == ["derived_pending"]


def test_unresolved_direct_link_marks_pending(tmp_path: Path) -> None:
    db_path, release_id, writer = _setup(tmp_path)
    _seed_question(
        db_path, release_id, [("p1", "direct", _LEAF_KEY, "unresolved")]
    )

    assert writer.save_tag_analysis(1, _analysis())

    rows = _tags(db_path)
    assert _values(rows, "exam_scope") == []
    assert _values(rows, "tag_status") == ["derived_pending"]


def test_legacy_canonical_row_removed_on_rewrite(tmp_path: Path) -> None:
    db_path, release_id, writer = _setup(tmp_path)
    _seed_question(
        db_path, release_id, [("p1", "direct", _SECTION_KEY, "resolved")]
    )
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO question_tags(question_id, tag_type, tag_value, source)
            VALUES (1, 'canonical_knowledge_id', 'kp_legacy', 'taxonomy')
            """
        )

    assert writer.save_tag_analysis(1, _analysis())

    assert _values(_tags(db_path), "canonical_knowledge_id") == []


def test_rewrite_is_idempotent(tmp_path: Path) -> None:
    db_path, release_id, writer = _setup(tmp_path)
    _seed_question(
        db_path, release_id, [("p1", "direct", _SKILL_KEY, "resolved")]
    )

    assert writer.save_tag_analysis(1, _analysis())
    assert writer.save_tag_analysis(1, _analysis())

    rows = _tags(db_path)
    assert _values(rows, "exam_scope") == [_CHAPTER_SCOPE]
    assert _values(rows, "curriculum_section") == [_SECTION_ID]
    keys = [(row["tag_type"], row["tag_value"]) for row in rows]
    assert len(set(keys)) == len(rows)


def test_refresh_keeps_protected_knowledge_point_sources(
    tmp_path: Path,
) -> None:
    db_path, release_id, _writer = _setup(tmp_path)
    _seed_question(
        db_path, release_id, [("p1", "direct", _SKILL_KEY, "resolved")]
    )
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO question_tags(question_id, tag_type, tag_value, source)
            VALUES (1, 'knowledge_point', 'kp_stale_not_linked', 'taxonomy'),
                   (1, 'knowledge_point', 'kp_codex_keep', 'codex_self'),
                   (1, 'knowledge_point', 'kp_manual_keep', 'manual')
            """
        )

    with connect(db_path) as connection:
        assert refresh_derived_ownership_tags(connection, 1)

    rows = _tags(db_path)
    values = _values(rows, "knowledge_point")
    # 过期 taxonomy 派生行被清掉重建；ai/codex_self/manual 等非派生行保留。
    assert "kp_stale_not_linked" not in values
    assert "kp_codex_keep" in values
    assert "kp_manual_keep" in values
    assert _SKILL_KEY in values
