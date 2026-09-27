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
    load_release,
    load_release_for_taxonomy_revision,
    preview_install,
    rollback_release,
    stage_release,
)
from question_bank.knowledge_graph_release.contracts import compute_content_hash
from question_bank.taxonomy.governance import TaxonomyGovernance


def _database(tmp_path: Path) -> Path:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    return db_path


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
