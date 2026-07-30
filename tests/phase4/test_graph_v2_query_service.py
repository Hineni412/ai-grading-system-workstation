from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from question_bank.database.schema import initialize_database
from question_bank.relations.contracts import (
    KnowledgeRelation,
    RelationStatus,
    RelationType,
)
from question_bank.relations.query_service import (
    GraphV2Query,
    KnowledgeGraphV2QueryService,
)
from question_bank.relations.repository import KnowledgeRelationRepository


def _profile() -> dict[str, object]:
    return {
        "scope": {
            "mode": "student",
            "student_ids": ["12"],
            "class_id": "八年级1班",
        },
        "exam_scope": {
            "mode": "current",
            "session_ids": [14],
            "sessions": [{"id": 14, "name": "合成考试"}],
        },
        "coverage": {
            "total_items": 3,
            "covered_items": 3,
            "missing_tag_items": 0,
        },
        "warnings": [],
        "diagnosis_identity": "question_tag",
        "students": [
            {
                "student_id": 12,
                "student_code": "S12",
                "student_name": "合成学生",
                "class_id": "八年级1班",
                "weak_points": [
                    _weak_point("一元一次方程", 0.55, 1),
                    _weak_point("等式的性质", 0.72, 2),
                    _weak_point("未治理合成词", 0.4, 3),
                ],
            }
        ],
    }


def _weak_point(label: str, mastery: float, question_number: int):
    return {
        "knowledge_point": label,
        "knowledge_key": f"knowledge_point:{label}",
        "mastery": mastery,
        "deduction_count": 1,
        "evidence_count": 1,
        "actionable_reasons": ["合成原因"],
        "tag_context": {"ability": ["推理"]},
        "error_counts": {
            "primary": {"计算": 1},
            "secondary": {},
        },
        "source_question_refs": [
            {
                "session_id": 14,
                "session_name": "合成考试",
                "question_id": f"Q{question_number}",
                "bank_question_id": question_number,
                "score_awarded": mastery * 10,
                "full_score": 10,
                "score_rate": mastery,
            }
        ],
    }


def _confirm(
    repository: KnowledgeRelationRepository,
    source_key: str,
    target_key: str,
    relation_type: RelationType = RelationType.PREREQUISITE,
):
    suggested = repository.create_suggestion(
        KnowledgeRelation(source_key, target_key, relation_type),
        source_kind="teacher",
        source_reference="teacher-synthetic",
        rationale="合成图谱关系",
    )
    return repository.transition(
        suggested.relation_id,
        expected_revision=suggested.revision,
        to_status=RelationStatus.CONFIRMED,
        actor_ref="teacher-synthetic",
        reason="合成确认",
    )


def test_query_uses_only_confirmed_edges_and_keeps_isolated_nodes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    repository = KnowledgeRelationRepository(database)
    _confirm(
        repository,
        "kp_alg_linear_equation",
        "kp_alg_equation_properties",
    )
    repository.create_suggestion(
        KnowledgeRelation(
            "kp_alg_equation_properties",
            "kp_alg_real_numbers",
            RelationType.PREREQUISITE,
        ),
        source_kind="model",
        rationale="仍待审核",
        model_name="fake",
        model_version="v1",
    )
    service = KnowledgeGraphV2QueryService(database)

    payload = service.query(
        _profile(),
        GraphV2Query(
            knowledge_keys=(
                "kp_alg_linear_equation",
                "kp_fun_linear",
            ),
            prerequisite_depth=2,
        ),
    )

    assert payload["response_schema_version"] == "knowledge-graph-v2"
    assert len(payload["response_version"]) == 64
    assert {node["stable_key"] for node in payload["nodes"]} == {
        "kp_alg_linear_equation",
        "kp_alg_equation_properties",
        "kp_fun_linear",
    }
    assert [edge["relation_type"] for edge in payload["edges"]] == [
        "prerequisite"
    ]
    assert all(
        edge["target_key"] != "kp_alg_real_numbers"
        for edge in payload["edges"]
    )
    isolated = next(
        node
        for node in payload["nodes"]
        if node["stable_key"] == "kp_fun_linear"
    )
    assert isolated["mastery_v1"]["status"] == "missing"
    assert isolated["mastery_v2"] == {
        "status": "unavailable",
        "value": None,
        "evidence_count": 0,
        "reason": "mastery_v2_not_enabled",
    }
    assert isolated["missing_reasons"] == ["no_evidence_in_scope"]
    assert payload["counts"]["edge_count"] == len(payload["edges"])
    assert payload["counts"]["node_count"] == len(payload["nodes"])


def test_prerequisite_depth_is_bounded_and_multiple_parents_are_preserved(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    repository = KnowledgeRelationRepository(database)
    _confirm(
        repository,
        "kp_alg_linear_equation",
        "kp_alg_equation_properties",
    )
    _confirm(
        repository,
        "kp_alg_equation_properties",
        "kp_alg_real_numbers",
    )
    _confirm(
        repository,
        "kp_alg_linear_equation",
        "kp_alg_real_numbers",
        RelationType.PARENT,
    )
    service = KnowledgeGraphV2QueryService(database)

    depth_one = service.query(
        _profile(),
        GraphV2Query(
            knowledge_keys=("kp_alg_linear_equation",),
            prerequisite_depth=1,
        ),
    )
    depth_two = service.query(
        _profile(),
        GraphV2Query(
            knowledge_keys=("kp_alg_linear_equation",),
            prerequisite_depth=2,
        ),
    )

    assert {node["stable_key"] for node in depth_one["nodes"]} == {
        "kp_alg_linear_equation",
        "kp_alg_equation_properties",
    }
    assert {node["stable_key"] for node in depth_two["nodes"]} == {
        "kp_alg_linear_equation",
        "kp_alg_equation_properties",
        "kp_alg_real_numbers",
    }
    assert {
        (edge["source_key"], edge["target_key"], edge["relation_type"])
        for edge in depth_two["edges"]
    } == {
        (
            "kp_alg_linear_equation",
            "kp_alg_equation_properties",
            "prerequisite",
        ),
        (
            "kp_alg_equation_properties",
            "kp_alg_real_numbers",
            "prerequisite",
        ),
        ("kp_alg_linear_equation", "kp_alg_real_numbers", "parent"),
    }


def test_missing_explanations_do_not_invent_edges_or_identities(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    payload = KnowledgeGraphV2QueryService(database).query(
        _profile(),
        GraphV2Query(
            knowledge_keys=("kp_missing_identity",),
            prerequisite_depth=5,
        ),
    )

    assert payload["nodes"] == []
    assert payload["edges"] == []
    assert payload["missing"] == [
        {
            "kind": "ungoverned_knowledge_label",
            "label": "未治理合成词",
            "count": 1,
        },
        {
            "kind": "requested_identity_not_found",
            "stable_key": "kp_missing_identity",
            "count": 1,
        },
    ]


def test_evidence_is_paginated_by_stable_identity(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    profile = _profile()
    duplicate_student = {
        **profile["students"][0],
        "student_id": 13,
        "student_code": "S13",
        "student_name": "合成学生乙",
    }
    profile["students"] = [profile["students"][0], duplicate_student]
    service = KnowledgeGraphV2QueryService(database)

    first = service.evidence(
        profile,
        stable_key="kp_alg_linear_equation",
        page=1,
        page_size=1,
    )
    second = service.evidence(
        profile,
        stable_key="kp_alg_linear_equation",
        page=2,
        page_size=1,
    )

    assert first["total"] == 2
    assert first["total_pages"] == 2
    assert first["items"][0]["stable_key"] == "kp_alg_linear_equation"
    assert first["items"][0]["knowledge_label"] == "一元一次方程"
    assert second["page"] == 2
    assert second["items"][0]["student_id"] == 13
    assert first["response_version"] != second["response_version"]


def test_retirement_changes_response_version_and_invalidates_active_edges(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    repository = KnowledgeRelationRepository(database)
    confirmed = _confirm(
        repository,
        "kp_alg_linear_equation",
        "kp_alg_equation_properties",
    )
    service = KnowledgeGraphV2QueryService(database)
    query = GraphV2Query(
        knowledge_keys=("kp_alg_linear_equation",),
        prerequisite_depth=1,
    )

    before = service.query(_profile(), query)
    repository.transition(
        confirmed.relation_id,
        expected_revision=confirmed.revision,
        to_status=RelationStatus.RETIRED,
        actor_ref="teacher-synthetic",
        reason="合成退役",
    )
    after = service.query(_profile(), query)

    assert len(before["edges"]) == 1
    assert after["edges"] == []
    assert {
        node["stable_key"] for node in after["nodes"]
    } == {"kp_alg_linear_equation"}
    assert before["response_version"] != after["response_version"]


def test_request_cache_reads_identities_and_relations_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    service = KnowledgeGraphV2QueryService(database)
    counts = {"identities": 0, "relations": 0}
    original_identities = service.repository.list_identities
    original_relations = service.repository.list_active_relations

    def identities(**kwargs):
        counts["identities"] += 1
        return original_identities(**kwargs)

    def relations():
        counts["relations"] += 1
        return original_relations()

    monkeypatch.setattr(service.repository, "list_identities", identities)
    monkeypatch.setattr(service.repository, "list_active_relations", relations)

    service.query(_profile(), GraphV2Query(prerequisite_depth=3))

    assert counts == {"identities": 1, "relations": 1}


def test_thousand_node_synthetic_query_stays_under_frozen_baseline_limit(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    keys = [f"kp_perf_{index:04d}" for index in range(1000)]
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executemany(
            """
            INSERT INTO knowledge_tag_identities (
                stable_key, display_name, origin
            ) VALUES (?, ?, 'builtin')
            """,
            ((key, f"合成节点 {index}") for index, key in enumerate(keys)),
        )
        connection.executemany(
            """
            INSERT INTO knowledge_relations (
                relation_id,
                source_key,
                target_key,
                relation_type,
                status,
                source_kind,
                rationale,
                decision_by,
                decided_at
            ) VALUES (
                ?, ?, ?, 'prerequisite', 'confirmed', 'system',
                '合成性能边', 'synthetic', datetime('now','localtime')
            )
            """,
            (
                (f"kr_perf_{index:04d}", keys[index], keys[index - 1])
                for index in range(1, len(keys))
            ),
        )
        connection.commit()
    service = KnowledgeGraphV2QueryService(database)

    started = time.perf_counter()
    payload = service.query(
        {
            **_profile(),
            "students": [],
        },
        GraphV2Query(
            knowledge_keys=tuple(keys),
            prerequisite_depth=5,
        ),
    )
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert payload["counts"]["node_count"] == 1000
    assert payload["counts"]["edge_count"] == 999
    assert elapsed_ms <= 500
