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
    bootstrap_release,
    load_release,
    load_release_for_taxonomy_revision,
    load_taxonomy_catalog,
    preview_install,
    rollback_release,
    stage_release,
    validate_release,
)
from question_bank.knowledge_graph_release.contracts import compute_content_hash
from question_bank.solution_evidence.baseline import (
    build_fine_term_mapping_baseline,
    install_fine_term_mapping_baseline,
)
from question_bank.solution_evidence.repository import (
    FineTermCoreMappingRepository,
)
from question_bank.taxonomy.governance import TaxonomyGovernance


def _database(tmp_path: Path) -> Path:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    return db_path


def _next_release(
    source: KnowledgeGraphRelease,
    release_id: str,
) -> KnowledgeGraphRelease:
    payload = copy.deepcopy(source.payload)
    payload["release_id"] = release_id
    payload["predecessor_release_id"] = source.release_id
    payload["content_hash"] = compute_content_hash(payload)
    return KnowledgeGraphRelease.from_mapping(payload)


def test_checked_in_release_covers_every_governed_knowledge_term() -> None:
    release = load_release()
    report = validate_release(release, load_taxonomy_catalog())

    assert report.valid, report.to_dict()
    assert len(release.payload["fine_term_dispositions"]) == 1218
    assert len(release.payload["core_nodes"]) == 1218
    assert sum(
        item["status"] == "active"
        for item in release.payload["core_nodes"]
    ) == 1218
    assert sum(
        item["node_kind"] == "skill"
        for item in release.payload["core_nodes"]
    ) == 94
    assert len(release.payload["mappings"]) == 1218
    assert len(release.payload["relations"]) == 1182


def test_bootstrap_release_syncs_identity_states(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    release = load_release()

    bootstrap_release(
        db_path,
        release,
        actor_ref="teacher:test",
        source_reference="test-release",
        reason="test bootstrap",
    )

    expected = {
        str(node["stable_key"]): str(node["display_name"])
        for node in release.payload["core_nodes"]
        if node["status"] == "active"
    }
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT stable_key, display_name
            FROM knowledge_tag_identities
            WHERE status = 'active' AND origin = 'local'
            """
        ).fetchall()
    actual = {str(row[0]): str(row[1]) for row in rows}
    assert actual == expected


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
        assert connection.execute(
            """
            SELECT COUNT(*) FROM knowledge_tag_identities
            WHERE status = 'active'
            """
        ).fetchone()[0] == active_before_stage
        assert connection.execute(
            "SELECT COUNT(*) FROM knowledge_tag_aliases"
        ).fetchone()[0] == aliases_before_stage
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
        assert connection.execute(
            """
            SELECT COUNT(*) FROM knowledge_tag_identities
            WHERE status = 'active'
            """
        ).fetchone()[0] == 70
        assert connection.execute(
            "SELECT COUNT(*) FROM knowledge_tag_aliases"
        ).fetchone()[0] > 70
        assert connection.execute(
            "SELECT COUNT(*) FROM knowledge_graph_releases WHERE status = 'active'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM fine_term_core_mappings WHERE status = 'confirmed'"
        ).fetchone()[0] == 301
        assert connection.execute(
            "SELECT COUNT(*) FROM knowledge_relations WHERE status = 'confirmed'"
        ).fetchone()[0] == 48


def test_activation_rejects_stale_expected_release(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    release = load_release_for_taxonomy_revision(3)
    stage_release(
        db_path,
        release,
        actor_ref="teacher:test",
        source_reference="test-release",
    )

    with pytest.raises(KnowledgeGraphReleaseConflict, match="重新预演"):
        activate_release(
            db_path,
            release.release_id,
            expected_active_release_id="kgr_stale",
            actor_ref="teacher:test",
            reason="stale test",
        )

    assert active_release_id(db_path) is None


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
    assert any(
        issue.code == "teacher_relation_conflict"
        for issue in preview.issues
    )


def test_legacy_mapping_baseline_stops_after_graph_activation(
    tmp_path: Path,
) -> None:
    db_path = _database(tmp_path)
    release = load_release_for_taxonomy_revision(3)
    stage_release(
        db_path,
        release,
        actor_ref="teacher:test",
        source_reference="test-release",
    )
    activate_release(
        db_path,
        release.release_id,
        expected_active_release_id=None,
        actor_ref="teacher:test",
        reason="test activation",
    )

    result = install_fine_term_mapping_baseline(
        FineTermCoreMappingRepository(db_path),
        build_fine_term_mapping_baseline(),
        actor_ref="system:test",
    )

    assert result["installed_mapping_count"] == 0


def test_tagging_contract_follows_activation_and_rollback(
    tmp_path: Path,
) -> None:
    db_path = _database(tmp_path)
    governance = TaxonomyGovernance(
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=db_path,
    )
    first = load_release_for_taxonomy_revision(3)
    second = load_release_for_taxonomy_revision(4)

    def tagging_contract() -> dict[str, object]:
        return governance.prompt_contract(
            {
                "curriculum_volume_id": "bnu24-math-g7-upper",
                "question_text": "利用一元一次方程解决问题",
            }
        )

    assert tagging_contract()["knowledge_graph_release_id"] == ""
    stage_release(
        db_path,
        first,
        actor_ref="teacher:test",
        source_reference="test-release",
    )
    assert tagging_contract()["knowledge_graph_release_id"] == ""
    activate_release(
        db_path,
        first.release_id,
        expected_active_release_id=None,
        actor_ref="teacher:test",
        reason="test first activation",
    )
    first_contract = tagging_contract()
    assert first_contract["knowledge_graph_release_id"] == first.release_id
    assert first_contract["knowledge_catalog_revision"] == 3
    # The revision-3 release carries legacy fine-term ids that are not part
    # of the bundled BNU curriculum tree, so a volume-scoped context has no
    # eligible knowledge candidates and the contract reports "insufficient".
    assert first_contract["candidates"]["knowledge"] == []
    assert first_contract["retrieval_status"] == "insufficient"
    assert governance.snapshot()["base_catalog_revision"] == 3
    assert governance.observation_snapshot()["graph_release_id"] == first.release_id
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
    second_contract = tagging_contract()
    assert second_contract["knowledge_graph_release_id"] == second.release_id
    assert second_contract["knowledge_catalog_revision"] == 4
    assert second_contract["candidates"]["knowledge"]
    assert governance.snapshot()["base_catalog_revision"] == 4
    assert any(
        term["id"] == "kp_bnu24_math_g7_lower_4_3_7"
        for term in governance.catalog()["dimensions"]["knowledge"]
    )
    assert governance.observation_snapshot()["graph_release_id"] == second.release_id
    rollback_release(
        db_path,
        first.release_id,
        expected_active_release_id=second.release_id,
        actor_ref="teacher:test",
        reason="test rollback",
    )
    rollback_contract = tagging_contract()
    assert rollback_contract["knowledge_graph_release_id"] == first.release_id
    assert rollback_contract["knowledge_catalog_revision"] == 3
    assert rollback_contract["candidates"]["knowledge"] == []
    assert rollback_contract["retrieval_status"] == "insufficient"
    assert governance.snapshot()["base_catalog_revision"] == 3
    assert not any(
        term["id"] == "kp_bnu24_math_g7_lower_4_3_7"
        for term in governance.catalog()["dimensions"]["knowledge"]
    )
    assert governance.observation_snapshot()["graph_release_id"] == first.release_id


def test_skill_layer_release_marks_linkable_candidates(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    governance = TaxonomyGovernance(
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=db_path,
    )
    chain = (
        load_release_for_taxonomy_revision(3),
        load_release_for_taxonomy_revision(4),
        load_release(),
    )
    expected_active: str | None = None
    for release in chain:
        stage_release(
            db_path,
            release,
            actor_ref="teacher:test",
            source_reference="test-release",
        )
        activate_release(
            db_path,
            release.release_id,
            expected_active_release_id=expected_active,
            actor_ref="teacher:test",
            reason="test activation",
        )
        expected_active = release.release_id

    contract = governance.prompt_contract(
        {
            "curriculum_volume_id": "bnu24-math-g8-upper",
            "question_text": "利用勾股定理求旗杆高度",
        }
    )
    assert contract["knowledge_catalog_revision"] == 5
    knowledge = contract["candidates"]["knowledge"]
    skills = [item for item in knowledge if item["id"].startswith("sk_")]
    assert skills
    assert all(item["usage"] == "direct_core" for item in skills)
    this_volume_sections = [
        item
        for item in knowledge
        if item.get("level") == 2
        and item.get("volume_id") == "bnu24-math-g8-upper"
    ]
    assert this_volume_sections
    assert all(
        item["usage"] == "direct_core" for item in this_volume_sections
    )
    context_terms = [
        item
        for item in knowledge
        if item["id"].startswith("kp_")
        and item["usage"] not in {"direct_core"}
    ]
    assert context_terms
    assert all(
        item["usage"] == "retrieval_only" for item in context_terms
    )
