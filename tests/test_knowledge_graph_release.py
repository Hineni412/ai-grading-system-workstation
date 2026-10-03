from __future__ import annotations

import copy
from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.knowledge_graph_release import (
    KnowledgeGraphRelease,
    KnowledgeGraphReleaseConflict,
    active_release_id,
    activate_release,
    load_active_release,
    load_release,
    load_release_for_taxonomy_revision,
    preview_install,
    rollback_release,
    stage_release,
)
from question_bank.knowledge_graph_release.contracts import (
    KnowledgeGraphReleaseError,
    _clear_release_cache_for_tests,
    cached_release_from_json,
    compute_content_hash,
)
from question_bank.knowledge_graph_release.loader import (
    _clear_file_release_cache_for_tests,
)
from question_bank.taxonomy.governance import TaxonomyGovernance


def _database(tmp_path: Path) -> Path:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    return db_path


def test_symmetry_skill_release_preserves_existing_definitions() -> None:
    from question_bank.knowledge_graph_release.loader import load_taxonomy_catalog_for_release
    from question_bank.knowledge_graph_release.validation import validate_release

    previous = load_release_for_taxonomy_revision(9)
    candidate = load_release_for_taxonomy_revision(10)
    assert not validate_release(candidate, load_taxonomy_catalog_for_release(candidate)).errors
    old_nodes = {node['stable_key']: node for node in previous.payload['core_nodes']}
    new_nodes = {node['stable_key']: node for node in candidate.payload['core_nodes']}
    assert {key: new_nodes[key] for key in old_nodes} == old_nodes
    added = set(new_nodes) - set(old_nodes)
    assert added == {'sk_bnu24_math_g8_lower_3_2_101'}
    assert new_nodes[next(iter(added))]['node_kind'] == 'skill'
    old_relations = {(r['source_key'], r['target_key'], r['relation_type']) for r in previous.payload['relations']}
    new_relations = {(r['source_key'], r['target_key'], r['relation_type']) for r in candidate.payload['relations']}
    assert new_relations - old_relations == {('sk_bnu24_math_g8_lower_3_2_101', 'kp_bnu24_math_g8_lower_3_2', 'parent')}
    assert old_relations.issubset(new_relations)


def test_release_can_be_staged_activated_and_rolled_back_atomically(
    tmp_path: Path,
) -> None:
    db_path = _database(tmp_path)
    first = load_release_for_taxonomy_revision(3)
    second = load_release_for_taxonomy_revision(4)
    with connect(db_path) as connection:
        active_before_stage = connection.execute(
            """
            SELECT COUNT(*) FROM knowledge_tag_identities
            WHERE status = 'active'
            """
        ).fetchone()[0]
        aliases_before_stage = connection.execute(
            "SELECT COUNT(*) FROM knowledge_tag_aliases"
        ).fetchone()[0]

    assert preview_install(db_path, first).can_activate
    stage_release(
        db_path,
        first,
        actor_ref="teacher:test",
        source_reference="test-release",
    )
    with connect(db_path) as connection:
        assert (
            connection.execute(
                """
            SELECT COUNT(*) FROM knowledge_tag_identities
            WHERE status = 'active'
            """
            ).fetchone()[0]
            == active_before_stage
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM knowledge_tag_aliases").fetchone()[
                0
            ]
            == aliases_before_stage
        )
    activate_release(
        db_path,
        first.release_id,
        expected_active_release_id=None,
        actor_ref="teacher:test",
        reason="test first activation",
    )
    stage_release(
        db_path,
        second,
        actor_ref="teacher:test",
        source_reference="test-release-v2",
    )
    activate_release(
        db_path,
        second.release_id,
        expected_active_release_id=first.release_id,
        actor_ref="teacher:test",
        reason="test second activation",
    )
    rollback_release(
        db_path,
        first.release_id,
        expected_active_release_id=second.release_id,
        actor_ref="teacher:test",
        reason="test rollback",
    )

    assert active_release_id(db_path) == first.release_id
    with connect(db_path) as connection:
        assert (
            connection.execute(
                """
            SELECT COUNT(*) FROM knowledge_tag_identities
            WHERE status = 'active'
            """
            ).fetchone()[0]
            == 70
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM knowledge_tag_aliases").fetchone()[
                0
            ]
            > 70
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_graph_releases WHERE status = 'active'"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM fine_term_core_mappings WHERE status = 'confirmed'"
            ).fetchone()[0]
            == 301
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_relations WHERE status = 'confirmed'"
            ).fetchone()[0]
            == 48
        )


def test_preview_blocks_unaccounted_teacher_relation(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO knowledge_relations (
                relation_id, source_key, target_key, relation_type, status,
                source_kind, source_reference, rationale,
                decision_by, decision_note, decided_at
            ) VALUES (
                'teacher-extra-relation',
                'kp_alg_equation_properties', 'kp_geo_square', 'related',
                'confirmed', 'teacher', 'test', 'teacher evidence',
                'teacher:test', 'keep this relation', datetime('now','localtime')
            )
            """
        )

    preview = preview_install(db_path, load_release())

    assert not preview.can_activate
    assert any(issue.code == "teacher_relation_conflict" for issue in preview.issues)


def test_maintenance_reuses_unchanged_links_and_limits_replacements(tmp_path):
    import json
    from tools.maintain_question_bank import standard_plan, install_standard
    db_path = _database(tmp_path)
    first = load_release_for_taxonomy_revision(3)
    stage_release(db_path, first, actor_ref="test", source_reference="test")
    activate_release(db_path, first.release_id, expected_active_release_id=None, actor_ref="test", reason="test")
    keys = [node["stable_key"] for node in first.payload["core_nodes"][:2]]
    with connect(db_path) as conn:
        for qid, key in enumerate(keys, 1):
            vid = str(qid) * 64
            evidence = {"parts": [{"part_id": "P1", "evidence_points": [{"evidence_point_id": "E1"}]}]}
            conn.execute("INSERT INTO questions(id,question_number,question_text) VALUES(?,?,?)", (qid, str(qid), "合成试题"))
            conn.execute("""INSERT INTO question_solution_evidence_versions
                (evidence_version_id,question_id,source_content_hash,schema_version,content_hash,evidence_json,
                 source_kind,source_reference,created_by,graph_release_id)
                VALUES(?,?,?,'question-solution-evidence-v2',?,?,'backfill','synthetic','test',?)""",
                (vid, qid, "a"*64, "b"*64, json.dumps(evidence), first.release_id))
            conn.execute("""INSERT INTO evidence_point_knowledge_links
                (evidence_version_id,question_id,part_id,evidence_point_id,graph_release_id,role,
                 term_id,stable_key,resolution_status,weight,source_kind,source_reference)
                VALUES(?,?,'P1','E1',?,'direct',?,?,'resolved',1,'link_job','synthetic')""",
                (vid, qid, first.release_id, key, key))
    payload = copy.deepcopy(first.payload)
    payload.pop("content_hash")
    payload.update(release_id="kgr_synthetic_maintenance", predecessor_release_id=first.release_id)
    payload["core_nodes"][1]["definition"] += "修订"
    second = KnowledgeGraphRelease.from_mapping(payload)
    plan = standard_plan(db_path, second)
    assert plan["reusable_question_ids"] == [1]
    assert plan["affected_question_ids"] == [2]
    with pytest.raises(ValueError, match="覆盖受影响"):
        install_standard(db_path, second, plan, [])
    replacements = [{"question_id": 2, "evidence_version_id": "2"*64,
                     "points": [{"part_id": "P1", "evidence_point_id": "E1", "links": [
                         {"role": "direct", "term_id": keys[1], "stable_key": keys[1], "weight": 1}]}]}]
    install_standard(db_path, second, plan, replacements)
    with connect(db_path) as conn:
        old = tuple(conn.execute("SELECT term_id,stable_key,weight,source_kind,source_reference FROM evidence_point_knowledge_links WHERE question_id=1 AND graph_release_id=?", (first.release_id,)).fetchone())
        new = tuple(conn.execute("SELECT term_id,stable_key,weight,source_kind,source_reference FROM evidence_point_knowledge_links WHERE question_id=1 AND graph_release_id=?", (second.release_id,)).fetchone())
        assert new == old
        assert conn.execute("SELECT COUNT(*) FROM evidence_point_knowledge_links").fetchone()[0] == 4
    assert active_release_id(db_path) == second.release_id


def test_active_release_loads_share_one_cached_object(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    first = load_release_for_taxonomy_revision(3)
    stage_release(
        db_path, first, actor_ref="teacher:test", source_reference="test"
    )
    activate_release(
        db_path,
        first.release_id,
        expected_active_release_id=None,
        actor_ref="teacher:test",
        reason="test first activation",
    )
    _clear_release_cache_for_tests()

    one = load_active_release(db_path)
    two = load_active_release(db_path)
    assert one is not None
    assert one is two
    assert one.release_id == first.release_id

    second = load_release_for_taxonomy_revision(4)
    stage_release(
        db_path, second, actor_ref="teacher:test", source_reference="test-v2"
    )
    activate_release(
        db_path,
        second.release_id,
        expected_active_release_id=first.release_id,
        actor_ref="teacher:test",
        reason="test second activation",
    )
    three = load_active_release(db_path)
    assert three is not None
    assert three is not one
    assert three.release_id == second.release_id


def test_load_release_reparses_only_when_the_file_changes(tmp_path: Path) -> None:
    import json
    import os

    payload = {
        "schema_version": "knowledge-graph-release-v1",
        "release_id": "kgr_loader_cache_test",
        "taxonomy_revision": 3,
        "core_nodes": [],
        "mappings": [],
        "relations": [],
        "fine_term_dispositions": [],
        "sources": [],
    }
    payload["content_hash"] = compute_content_hash(payload)
    path = tmp_path / "release.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    _clear_file_release_cache_for_tests()
    _clear_release_cache_for_tests()
    first = load_release(path)
    assert load_release(path) is first

    # Same content, new mtime: a fresh parse replaces the cached object and
    # re-registers the identity so DB loads reuse the new object too.
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    third = load_release(path)
    assert third is not first
    assert third.release_id == first.release_id
    assert load_release(path) is third
    assert (
        cached_release_from_json(
            json.dumps(payload, ensure_ascii=False),
            release_id="kgr_loader_cache_test",
            content_hash=payload["content_hash"],
        )
        is third
    )

    with pytest.raises(KnowledgeGraphReleaseError):
        cached_release_from_json(
            json.dumps(payload, ensure_ascii=False),
            release_id="kgr_loader_cache_wrong_id",
            content_hash=payload["content_hash"],
        )
