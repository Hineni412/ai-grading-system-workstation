"""Evidence-point knowledge links: table reads, projection, job writes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.solution_evidence.knowledge_links import (
    LINK_JOB_KIND,
    MIGRATED_KIND,
    direct_links_for_part,
    direct_targets_for_part,
    links_from_embedded,
    load_point_links,
    project_embedded_links,
    replace_point_links,
    skill_layer_report,
)
from question_bank.solution_evidence.part_assessments import (
    direct_targets,
    training_part_observations,
)
from tests.current_knowledge_support import install_current_knowledge

_VERSION = "a" * 64


def _evidence() -> dict:
    return {
        "parts": [
            {
                "part_id": "part-1",
                "evidence_points": [
                    {
                        "evidence_point_id": "p1",
                        "target": "解方程",
                        "observable_evidence": "写出等式",
                        "fine_term_links": [
                            {
                                "fine_term_id": "kp_a",
                                "fine_term_name": "技能A",
                                "role": "direct",
                                "core_resolution": {
                                    "status": "resolved",
                                    "stable_keys": ["kp_a"],
                                    "reason": "current_release:REL",
                                },
                            },
                            {
                                "fine_term_id": "kp_b",
                                "fine_term_name": "技能B",
                                "role": "supporting_prerequisite",
                                "core_resolution": {
                                    "status": "resolved",
                                    "stable_keys": ["kp_b"],
                                    "reason": "current_release:REL",
                                },
                            },
                            {
                                "fine_term_id": "kp_x",
                                "fine_term_name": "未解析",
                                "role": "direct",
                                "core_resolution": {
                                    "status": "unresolved",
                                    "stable_keys": [],
                                },
                            },
                        ],
                    },
                    {
                        "evidence_point_id": "p2",
                        "target": "验证结果",
                        "observable_evidence": "代回检验",
                        "fine_term_links": [
                            {
                                "fine_term_id": "kp_c",
                                "fine_term_name": "技能C",
                                "role": "direct",
                                "core_resolution": {
                                    "status": "resolved",
                                    "stable_keys": ["kp_c"],
                                    "reason": "current_release:REL",
                                },
                            },
                            {
                                "fine_term_id": "kp_d",
                                "fine_term_name": "技能D",
                                "role": "direct",
                                "core_resolution": {
                                    "status": "resolved",
                                    "stable_keys": ["kp_d"],
                                    "reason": "current_release:REL",
                                },
                            },
                        ],
                    },
                ],
            }
        ]
    }


@pytest.fixture()
def prepared(tmp_path: Path) -> dict:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    release_id = install_current_knowledge(db_path, taxonomy_revision=4)
    payload = json.loads(json.dumps(_evidence()).replace("REL", release_id))
    with connect(db_path) as connection:
        connection.execute(
            "INSERT INTO papers(id,title,import_status) VALUES(1,'合成','ready')"
        )
        connection.execute(
            "INSERT INTO questions(id,paper_id,question_number,question_text)"
            " VALUES(1,1,'1','合成题')"
        )
        connection.execute(
            """
            INSERT INTO question_solution_evidence_versions(
                evidence_version_id, question_id, source_content_hash,
                schema_version, content_hash, evidence_json, status,
                source_kind, source_reference, created_by, graph_release_id
            ) VALUES (?, 1, ?, 'question-solution-evidence-v2', ?, ?,
                      'approved', 'backfill', 'synthetic', 'test', ?)
            """,
            (
                _VERSION,
                "b" * 64,
                "c" * 64,
                json.dumps(payload, ensure_ascii=False),
                release_id,
            ),
        )
    return {"db_path": db_path, "release_id": release_id, "payload": payload}


def test_project_embedded_links_writes_resolved_only(prepared):
    db_path = prepared["db_path"]
    with connect(db_path) as connection:
        inserted = project_embedded_links(
            connection,
            evidence_version_id=_VERSION,
            question_id=1,
            evidence_payload=prepared["payload"],
            default_release_id=prepared["release_id"],
        )
        assert inserted == 4  # unresolved link is skipped
        rows = connection.execute(
            "SELECT * FROM evidence_point_knowledge_links"
            " WHERE evidence_version_id = ?",
            (_VERSION,),
        ).fetchall()
    by_point = {}
    for row in rows:
        by_point.setdefault(row["evidence_point_id"], []).append(row)
    assert {row["role"] for row in by_point["p1"]} == {
        "direct",
        "supporting_prerequisite",
    }
    assert all(row["resolution_status"] == "resolved" for row in rows)
    assert all(row["graph_release_id"] == prepared["release_id"] for row in rows)
    # p2 has two direct links sharing weight.
    weights = {row["term_id"]: row["weight"] for row in by_point["p2"]}
    assert weights == {"kp_c": 0.5, "kp_d": 0.5}
    # Idempotent: a second projection inserts nothing.
    with connect(db_path) as connection:
        assert (
            project_embedded_links(
                connection,
                evidence_version_id=_VERSION,
                question_id=1,
                evidence_payload=prepared["payload"],
                default_release_id=prepared["release_id"],
            )
            == 0
        )


def test_load_point_links_groups_and_prefers_link_job(prepared):
    db_path = prepared["db_path"]
    with connect(db_path) as connection:
        project_embedded_links(
            connection,
            evidence_version_id=_VERSION,
            question_id=1,
            evidence_payload=prepared["payload"],
            default_release_id=prepared["release_id"],
        )
    loaded = load_point_links(db_path, [_VERSION], prepared["release_id"])
    assert set(loaded[_VERSION]) == {"p1", "p2"}
    assert direct_targets_for_part(
        prepared["payload"]["parts"][0], loaded[_VERSION]
    ) == ("kp_a", "kp_c", "kp_d")
    # link_job rows take precedence per point.
    with connect(db_path) as connection:
        replace_point_links(
            connection,
            evidence_version_id=_VERSION,
            question_id=1,
            graph_release_id=prepared["release_id"],
            points=[
                {
                    "part_id": "part-1",
                    "evidence_point_id": "p1",
                    "links": [
                        {"term_id": "sk_job", "stable_key": "sk_job", "role": "direct"}
                    ],
                }
            ],
        )
    loaded = load_point_links(db_path, [_VERSION], prepared["release_id"])
    assert [link.stable_key for link in loaded[_VERSION]["p1"]] == ["sk_job"]
    # p1 now uses link_job; p2 still falls back to migrated rows.
    assert [link.stable_key for link in loaded[_VERSION]["p2"]] == ["kp_c", "kp_d"]


def test_load_point_links_release_fallback(prepared):
    db_path = prepared["db_path"]
    with connect(db_path) as connection:
        project_embedded_links(
            connection,
            evidence_version_id=_VERSION,
            question_id=1,
            evidence_payload=prepared["payload"],
            default_release_id=prepared["release_id"],
        )
    # A different requested release still resolves the migrated rows.
    loaded = load_point_links(db_path, [_VERSION], "kgr_other")
    assert loaded[_VERSION]["p2"][0].stable_key == "kp_c"
    empty = load_point_links(db_path, [_VERSION], prepared["release_id"])
    assert empty


def test_replace_point_links_modes(prepared):
    db_path = prepared["db_path"]
    points = [
        {
            "part_id": "part-1",
            "evidence_point_id": "p1",
            "links": [{"term_id": "sk_a", "role": "direct"}],
        },
        {
            "part_id": "part-1",
            "evidence_point_id": "p2",
            "links": [{"term_id": "sk_b", "role": "direct"}],
        },
    ]
    with connect(db_path) as connection:
        assert (
            replace_point_links(
                connection,
                evidence_version_id=_VERSION,
                question_id=1,
                graph_release_id=prepared["release_id"],
                points=points,
            )
            == 2
        )
        # replace=True deletes prior link_job rows first.
        assert (
            replace_point_links(
                connection,
                evidence_version_id=_VERSION,
                question_id=1,
                graph_release_id=prepared["release_id"],
                points=points[:1],
            )
            == 1
        )
        remaining = connection.execute(
            "SELECT evidence_point_id FROM evidence_point_knowledge_links"
            " WHERE source_kind = ?",
            (LINK_JOB_KIND,),
        ).fetchall()
        assert [row["evidence_point_id"] for row in remaining] == ["p1"]
        # replace=False appends without deleting (missing_only).
        assert (
            replace_point_links(
                connection,
                evidence_version_id=_VERSION,
                question_id=1,
                graph_release_id=prepared["release_id"],
                points=points[1:],
                replace=False,
            )
            == 1
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM evidence_point_knowledge_links"
            " WHERE source_kind = ?",
            (LINK_JOB_KIND,),
        ).fetchone()[0] == 2


def test_direct_targets_delegates_to_table_links(prepared):
    db_path = prepared["db_path"]
    with connect(db_path) as connection:
        project_embedded_links(
            connection,
            evidence_version_id=_VERSION,
            question_id=1,
            evidence_payload=prepared["payload"],
            default_release_id=prepared["release_id"],
        )
    links = load_point_links(db_path, [_VERSION], prepared["release_id"])[
        _VERSION
    ]
    part = prepared["payload"]["parts"][0]
    assert direct_targets(part, links) == ("kp_a", "kp_c", "kp_d")
    direct = direct_links_for_part(part, links)
    assert {link.stable_key: link.weight for link in direct} == {
        "kp_a": 1.0,
        "kp_c": 0.5,
        "kp_d": 0.5,
    }


def test_links_from_embedded_weight_normalization(prepared):
    links = links_from_embedded(prepared["payload"])
    assert {link.stable_key: link.weight for link in links["p2"]} == {
        "kp_c": 0.5,
        "kp_d": 0.5,
    }
    assert all(link.source_kind == MIGRATED_KIND for link in links["p1"])


def test_training_observations_use_table_weights(prepared):
    profile = {
        "available": True,
        "current_source_content_hash": "a" * 64,
        "evidence": prepared["payload"],
        "parts": [{"part_id": "part-1", "difficulty": 5}],
    }
    criteria = {
        "solution_evidence": {"source_content_hash": "a" * 64},
        "points": [
            {
                "point_id": point["evidence_point_id"],
                "target": point["target"],
                "observable_evidence": point["observable_evidence"],
            }
            for point in prepared["payload"]["parts"][0]["evidence_points"]
        ],
    }
    final = [{"point_id": "p1", "state": "met"}, {"point_id": "p2", "state": "not_met"}]
    links = links_from_embedded(prepared["payload"])
    observed = training_part_observations(profile, criteria, final, links)
    weights = {o["stable_key"]: o["weight"] for o in observed}
    # p1 met: weight 1.0/2 observed = 0.5; p2 not_met: each link 0.5/2.
    assert weights == {"kp_a": 0.5, "kp_c": 0.25, "kp_d": 0.25}
    assert sum(weights.values()) == 1.0


def test_skill_layer_report_flags_underused_and_pairs(prepared):
    db_path = prepared["db_path"]
    release_id = prepared["release_id"]
    with connect(db_path) as connection:
        connection.execute(
            "INSERT INTO knowledge_tag_identities(stable_key,display_name,origin,status)"
            " VALUES('sk_a','技能甲','local','active'),"
            "       ('sk_b','技能乙','local','active'),"
            "       ('sk_cold','冷门技能','local','active')"
        )
        for point_id, key in (("p1", "sk_a"), ("p1", "sk_b"), ("p2", "sk_a")):
            connection.execute(
                """
                INSERT INTO evidence_point_knowledge_links(
                    evidence_version_id, question_id, part_id, evidence_point_id,
                    graph_release_id, role, term_id, stable_key,
                    resolution_status, weight, source_kind
                ) VALUES (?, 1, 'part-1', ?, ?, 'direct', ?, ?, 'resolved', 1.0, 'link_job')
                """,
                (_VERSION, point_id, release_id, key, key),
            )
        report = skill_layer_report(connection, release_id)
    counts = {item["stable_key"]: item["question_count"] for item in report["skills"]}
    assert counts == {"sk_a": 1, "sk_b": 1, "sk_cold": 0}
    assert {item["stable_key"] for item in report["underused_skills"]} == {
        "sk_a",
        "sk_b",
        "sk_cold",
    }
