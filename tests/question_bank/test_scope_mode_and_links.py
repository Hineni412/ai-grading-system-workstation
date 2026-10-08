"""strict/primary/any scope filtering + link-table reads (redesign §8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.services.question_read_service import (
    QuestionBankReadService,
    QuestionReadFilters,
)
from question_bank.solution_evidence.knowledge_links import (
    replace_point_links,
)
from tests.current_knowledge_support import install_current_knowledge

# Section ``bnu24-math-g7-lower-c02-s01`` lives in volume order 2; the g7-upper
# leaf is an earlier volume (order 1).
_SECTION = "kp_bnu24_math_g7_lower_2_1"
_SECTION_ID = "bnu24-math-g7-lower-c02-s01"
_LEAF_IN = "kp_bnu24_math_g7_lower_2_1_1"
_LEAF_SAME_VOL = "kp_bnu24_math_g7_lower_2_2_1"  # sibling section, same volume
_LEAF_EARLIER = "kp_bnu24_math_g7_upper_1_1_1"  # earlier volume


def test_skill_progress_and_strict_scope_use_published_parent_relations():
    from types import SimpleNamespace
    from question_bank.recommendation.personalized import (
        PersonalizedRecommendationConfig,
        _allowed_keys_for_config,
        _question_scope_allowed,
    )
    from dataclasses import replace

    skill = "sk_bnu24_math_g8_upper_1_1_01"
    later = "sk_bnu24_math_g8_upper_2_1_01"
    relations = [
        SimpleNamespace(relation_type="parent", source_key=key, target_key=parent)
        for key, parent in [
            (skill, "kp_bnu24_math_g8_upper_1_1"),
            (later, "kp_bnu24_math_g8_upper_2_1"),
            ("kp_bnu24_math_g8_upper_1_1", "kp_bnu24_math_g8_upper_1"),
        ]
    ]

    # The production resolver is hashable; use the same immutable identity
    # semantics for the cached progress calculation.
    class Resolver:
        pass

    resolver = Resolver()
    resolver.relations = relations
    config = PersonalizedRecommendationConfig(
        curriculum_volume_id="bnu24-math-g8-upper", target_keys=(skill,)
    )
    allowed = _allowed_keys_for_config(config, resolver)
    assert skill in allowed and later not in allowed
    candidate = {
        "stable_keys": [skill],
        "required_keys": [skill],
        "scope_complete": True,
    }
    assert _question_scope_allowed(candidate, config, allowed, resolver)
    # Learning chapter two does not permit a mixed chapter-one/chapter-two
    # exercise when the teacher explicitly requests chapter one only.
    strict = replace(
        config,
        scope_keys=("kp_bnu24_math_g8_upper_1",),
        teaching_progress_chapter_id="bnu24-math-g8-upper-c02",
    )
    mixed = {
        **candidate,
        "stable_keys": [skill, later],
        "required_keys": [skill, later],
    }
    assert not _question_scope_allowed(
        mixed, strict, _allowed_keys_for_config(strict, resolver), resolver
    )


def _evidence(question_id: int) -> dict:
    return {
        "parts": [
            {
                "part_id": "part-1",
                "evidence_points": [
                    {
                        "evidence_point_id": f"q{question_id}-p1",
                        "target": "目标",
                        "observable_evidence": "证据",
                        "fine_term_links": [],
                    }
                ],
            }
        ]
    }


@pytest.fixture()
def bank(tmp_path: Path) -> dict:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    release_id = install_current_knowledge(db_path, taxonomy_revision=4)
    with connect(db_path) as connection:
        connection.execute(
            "INSERT INTO papers(id,title,import_status) VALUES(1,'合成','ready')"
        )
        for qid in range(1, 6):
            connection.execute(
                "INSERT INTO questions(id,paper_id,question_number,question_text)"
                " VALUES(?,1,?,'合成题')",
                (qid, f"{qid}"),
            )
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
                    f"v{qid}" + "0" * 62,
                    qid,
                    "b" * 64,
                    "c" * 64,
                    json.dumps(_evidence(qid), ensure_ascii=False),
                    release_id,
                ),
            )
    return {"db_path": db_path, "release_id": release_id}


def _link(
    bank: dict,
    qid: int,
    links: list[dict],
) -> None:
    with connect(bank["db_path"]) as connection:
        replace_point_links(
            connection,
            evidence_version_id=f"v{qid}" + "0" * 62,
            question_id=qid,
            graph_release_id=bank["release_id"],
            points=[
                {
                    "part_id": "part-1",
                    "evidence_point_id": f"q{qid}-p1",
                    "links": links,
                }
            ],
        )


def _tag(connection, qid: int, tag_type: str, value: str) -> None:
    connection.execute(
        "INSERT INTO question_tags(question_id, tag_type, tag_value, source)"
        " VALUES(?, ?, ?, 'derived')",
        (qid, tag_type, value),
    )


def test_resolve_anchor_keys_climbs_type_parent_relations(tmp_path: Path) -> None:
    """题型键（kp_*_tNN，不在内置课程目录中）通过发布 parent 关系上溯小节。"""
    from question_bank.solution_evidence.knowledge_links import (
        resolve_anchor_keys,
    )

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    release_id = install_current_knowledge(db_path, taxonomy_revision=11)
    type_key = "kp_bnu24_math_g8_upper_1_1_t01"
    with connect(db_path) as connection:
        anchors = resolve_anchor_keys(
            connection, [type_key], preferred_release_id=release_id
        )
        # Ordinary curriculum keys and sk_ keys keep their existing routes.
        leaf = resolve_anchor_keys(
            connection,
            ["kp_bnu24_math_g8_upper_1_1_1", "sk_bnu24_math_g8_upper_1_1_101"],
            preferred_release_id=release_id,
        )
    assert anchors["sections"] == ["kp_bnu24_math_g8_upper_1_1"]
    assert anchors["chapters"] == ["kp_bnu24_math_g8_upper_1"]
    assert leaf["sections"] == ["kp_bnu24_math_g8_upper_1_1"]
    assert leaf["chapters"] == ["kp_bnu24_math_g8_upper_1"]


@pytest.fixture()
def tag_filter_bank(bank: dict) -> QuestionBankReadService:
    sibling = "kp_bnu24_math_g7_lower_2_2"
    with connect(bank["db_path"]) as connection:
        for qid in range(1, 6):
            connection.execute(
                "UPDATE questions SET question_text=?, question_type='解答题', "
                "difficulty='5' WHERE id=?", (f"TEST-标签筛选题 {qid}", qid),
            )
        for qid, leaf in ((1, _LEAF_IN), (2, _LEAF_SAME_VOL),
                          (3, _LEAF_IN), (5, _LEAF_EARLIER)):
            _tag(connection, qid, "knowledge_point", leaf)
        for qid, section, catalog_id in (
            (1, _SECTION, _SECTION_ID), (2, _SECTION, _SECTION_ID),
            (3, sibling, "bnu24-math-g7-lower-c02-s02"),
            (4, sibling, "bnu24-math-g7-lower-c02-s02"),
        ):
            _tag(connection, qid, "curriculum_section", catalog_id)
            connection.execute(
                "INSERT INTO question_scope_summary(question_id,primary_section_id,"
                "direct_section_ids_json,supporting_max_volume_order) VALUES(?,?,?,0)",
                (qid, section, json.dumps([section])),
            )
    return QuestionBankReadService(bank["db_path"], data_root=bank["db_path"].parent)


@pytest.mark.parametrize("mode", ["any", "primary", "strict"])
@pytest.mark.parametrize("field", ["knowledge_points", "knowledge_point"])
def test_knowledge_filters_are_exact_and_intersect_sections(tag_filter_bank, mode, field):
    service = tag_filter_bank
    knowledge = {field: (_LEAF_IN,) if field == "knowledge_points" else _LEAF_IN}
    selected = service.list_questions(QuestionReadFilters(scope_mode=mode, **knowledge))
    assert {item["id"] for item in selected.items} == {1, 3}
    selected = service.list_questions(QuestionReadFilters(
        scope_mode=mode, curriculum_sections=(_SECTION_ID,), **knowledge,
    ))
    assert [item["id"] for item in selected.items] == [1]
    assert selected.total == 1
    assert service.list_questions(QuestionReadFilters(
        scope_mode=mode, knowledge_points=(_LEAF_IN, _LEAF_SAME_VOL),
    )).total == 3
    assert service.list_questions(QuestionReadFilters(
        scope_mode=mode, knowledge_points=("TEST-unknown knowledge",),
    )).total == 0
    facets = service.list_facets(QuestionReadFilters(
        scope_mode=mode, curriculum_sections=(_SECTION_ID,), **knowledge,
    ))
    assert {row["value"]: row["count"] for row in facets["primary_sections"]} == {
        _SECTION: 1, "kp_bnu24_math_g7_lower_2_2": 1,
    }
    assert sorted(row["count"] for row in facets["knowledge_points"]) == [1, 1]
    assert facets["question_types"] == [{"value": "解答题", "count": 1}]


@pytest.mark.parametrize("collapse", [False, True])
def test_primary_section_facets_exclude_their_scope_and_keep_filtered_duplicates(
    bank, tag_filter_bank, collapse,
):
    from dataclasses import replace

    service = tag_filter_bank
    with connect(bank["db_path"]) as connection:
        connection.execute("UPDATE questions SET question_text="
                           "(SELECT question_text FROM questions WHERE id=1) WHERE id=2")
    filters = QuestionReadFilters(
        scope_mode="primary", curriculum_sections=(_SECTION,),
        collapse_duplicates=collapse,
    )
    counts = {row["value"]: row["count"]
              for row in service.list_facets(filters)["primary_sections"]}
    assert counts == {_SECTION: 1 if collapse else 2,
                      "kp_bnu24_math_g7_lower_2_2": 2, "": 1}
    assert service.list_questions(filters).total == counts[_SECTION]
    chapter = service.list_questions(replace(
        filters, curriculum_sections=("kp_bnu24_math_g7_lower_2",),
    ))
    assert chapter.total == counts[_SECTION] + counts["kp_bnu24_math_g7_lower_2_2"]
    knowledge_only = replace(filters, knowledge_points=(_LEAF_SAME_VOL,),
                             exam_scopes=("TEST-other chapter",))
    assert service.list_facets(knowledge_only)["primary_sections"] == [
        {"value": _SECTION, "count": 1},
    ]
    selected = service.list_questions(replace(knowledge_only, exam_scopes=()))
    assert [item["id"] for item in selected.items] == [2]
    assert selected.total == 1
