from __future__ import annotations

import copy
import json
import sqlite3

import pytest

from question_bank.current_knowledge import (
    CurrentKnowledgeResolver,
    CurrentKnowledgeUnavailable,
    ensure_checked_in_current_standard,
)
from question_bank.database.schema import initialize_database
from question_bank.knowledge_graph_release.loader import (
    load_release,
    load_taxonomy_catalog,
)
from question_bank.knowledge_graph_release import activate_release, stage_release
from question_bank.knowledge_graph_release.contracts import (
    KnowledgeGraphRelease,
    compute_content_hash,
)


def test_resolver_uses_current_canonical_output_and_expands_all_targets() -> None:
    release = load_release()
    catalog = load_taxonomy_catalog()
    resolver = CurrentKnowledgeResolver(release, catalog)
    multi = next(
        item
        for item in release.payload["fine_term_dispositions"]
        if item["disposition"] == "maps_to_many"
    )
    term = next(
        item for item in catalog["terms"] if item["id"] == multi["fine_term_id"]
    )
    expected = {
        item["stable_key"]
        for item in release.payload["mappings"]
        if item["fine_term_id"] == term["id"]
    }

    by_id = resolver.resolve(term["id"])
    by_name = resolver.resolve(term["name"])
    by_legacy = resolver.resolve((term.get("legacy_names") or term["aliases"])[0])

    assert {item.stable_key for item in by_id} == expected
    assert by_name == by_id
    assert by_legacy == by_id
    assert {item.canonical_name for item in by_id} == {term["name"]}


def test_resolver_excludes_non_current_dispositions_and_ambiguous_aliases() -> None:
    release = load_release()
    catalog = copy.deepcopy(load_taxonomy_catalog())
    excluded = next(
        item
        for item in release.payload["fine_term_dispositions"]
        if item["disposition"] in {"retrieval_only", "wrong_dimension", "retired"}
    )
    eligible = [
        item
        for item in release.payload["fine_term_dispositions"]
        if item["disposition"] in {"direct_core", "maps_to_core", "maps_to_many"}
    ][:2]
    for item in catalog["terms"]:
        if item["id"] in {value["fine_term_id"] for value in eligible}:
            item.setdefault("aliases", []).append("共同旧别名")
    resolver = CurrentKnowledgeResolver(release, catalog)

    assert resolver.resolve(excluded["fine_term_id"]) == ()
    assert resolver.resolve(excluded["display_name"]) == ()
    assert resolver.resolve("完全未知词") == ()
    assert resolver.resolve("共同旧别名") == ()


def test_active_database_loader_is_read_only_and_fails_closed(tmp_path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(CurrentKnowledgeUnavailable):
        CurrentKnowledgeResolver.from_active_database(missing)
    assert not missing.exists()

    database = tmp_path / "question_bank.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE knowledge_graph_releases (
            release_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL
        )
        """
    )
    release = load_release()
    connection.execute(
        "INSERT INTO knowledge_graph_releases VALUES (?, ?, 'active')",
        (release.release_id, json.dumps(release.to_dict(), ensure_ascii=False)),
    )
    connection.commit()
    connection.close()

    resolver = CurrentKnowledgeResolver.from_active_database(database)
    assert resolver.release_id == release.release_id
    assert len(resolver.nodes) == 70


def test_internal_startup_installs_only_when_no_active_release(tmp_path) -> None:
    database = tmp_path / "question_bank.db"
    initialize_database(database)

    release_id = ensure_checked_in_current_standard(database)
    repeated = ensure_checked_in_current_standard(database)

    assert repeated == release_id == load_release().release_id
    resolver = CurrentKnowledgeResolver.from_active_database(database)
    assert resolver.release_id == release_id
    with sqlite3.connect(database) as connection:
        active_count = connection.execute(
            "SELECT COUNT(*) FROM knowledge_graph_releases WHERE status = 'active'"
        ).fetchone()[0]
        release_count = connection.execute(
            "SELECT COUNT(*) FROM knowledge_graph_releases"
        ).fetchone()[0]
        event_count = connection.execute(
            "SELECT COUNT(*) FROM knowledge_graph_release_events"
        ).fetchone()[0]
    assert active_count == 1
    assert release_count == 1
    assert event_count == 2


def test_internal_bootstrap_ignores_legacy_conflicts_without_deleting_them(
    tmp_path,
) -> None:
    database = tmp_path / "question_bank.db"
    initialize_database(database)
    release = load_release()
    first_mapping = release.payload["mappings"][0]
    conflicting_target = next(
        node["stable_key"]
        for node in release.payload["core_nodes"]
        if node["status"] == "active"
        and node["stable_key"] != first_mapping["stable_key"]
    )
    blocked_relation = release.payload["relations"][0]
    desired_relations = {
        (
            item["source_key"],
            item["target_key"],
            item["relation_type"],
        )
        for item in release.payload["relations"]
    }
    active_keys = sorted(
        node["stable_key"]
        for node in release.payload["core_nodes"]
        if node["status"] == "active"
    )
    extra_relation = next(
        (source, target, "related")
        for index, source in enumerate(active_keys)
        for target in active_keys[index + 1 :]
        if (source, target, "related") not in desired_relations
    )
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        for node in release.payload["core_nodes"]:
            connection.execute(
                """
                INSERT OR IGNORE INTO knowledge_tag_identities (
                    stable_key, display_name, origin, status, retired_at
                ) VALUES (?, ?, 'local', 'retired', datetime('now','localtime'))
                """,
                (node["stable_key"], node["display_name"]),
            )
        connection.execute(
            """
            INSERT INTO fine_term_core_mappings (
                mapping_id, fine_term_id, stable_key, status, source_kind,
                source_reference, rationale, decision_by, decision_note,
                decided_at
            ) VALUES (?, ?, ?, 'confirmed', 'import', 'legacy',
                      'legacy imported mapping', 'import:test', 'keep',
                      datetime('now','localtime'))
            """,
            ("a" * 64, first_mapping["fine_term_id"], conflicting_target),
        )
        connection.execute(
            """
            INSERT INTO knowledge_relations (
                relation_id, source_key, target_key, relation_type, status,
                source_kind, source_reference, rationale,
                decision_by, decision_note, decided_at
            ) VALUES (
                'legacy-extra-relation', ?, ?, ?, 'confirmed',
                'import', 'legacy', 'legacy imported relation',
                'import:test', 'keep', datetime('now','localtime'))
            """,
            extra_relation,
        )
        connection.execute(
            """
            INSERT INTO knowledge_relations (
                relation_id, source_key, target_key, relation_type, status,
                source_kind, source_reference, rationale,
                decision_by, decision_note, decided_at
            ) VALUES (
                'legacy-teacher-relation', ?, ?, ?, 'rejected',
                'teacher', 'legacy', 'legacy teacher relation',
                'teacher:test', 'keep', datetime('now','localtime'))
            """,
            (
                blocked_relation["source_key"],
                blocked_relation["target_key"],
                blocked_relation["relation_type"],
            ),
        )
        connection.commit()
        mapping_before = dict(
            connection.execute(
                "SELECT * FROM fine_term_core_mappings WHERE mapping_id = ?",
                ("a" * 64,),
            ).fetchone()
        )
        relation_before = dict(
            connection.execute(
                "SELECT * FROM knowledge_relations WHERE relation_id = ?",
                ("legacy-teacher-relation",),
            ).fetchone()
        )
        extra_relation_before = dict(
            connection.execute(
                "SELECT * FROM knowledge_relations WHERE relation_id = ?",
                ("legacy-extra-relation",),
            ).fetchone()
        )

    assert ensure_checked_in_current_standard(database) == release.release_id

    resolver = CurrentKnowledgeResolver.from_active_database(database)
    assert resolver.release_id == release.release_id
    assert resolver.resolve(first_mapping["fine_term_id"])
    assert all(
        relation.relation_key != "legacy-teacher-relation"
        for relation in resolver.relations
    )
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        mapping_after = dict(connection.execute(
            "SELECT * FROM fine_term_core_mappings WHERE mapping_id = ?",
            ("a" * 64,),
        ).fetchone())
        relation_after = dict(connection.execute(
            """
            SELECT * FROM knowledge_relations WHERE relation_id = ?
            """,
            ("legacy-teacher-relation",),
        ).fetchone())
        extra_relation_after = dict(connection.execute(
            "SELECT * FROM knowledge_relations WHERE relation_id = ?",
            ("legacy-extra-relation",),
        ).fetchone())
    assert mapping_after == mapping_before
    assert relation_after == relation_before
    assert extra_relation_after == extra_relation_before


def test_internal_bootstrap_failure_rolls_back_and_can_retry(
    tmp_path,
    monkeypatch,
) -> None:
    database = tmp_path / "question_bank.db"
    initialize_database(database)
    release = load_release()

    from question_bank.knowledge_graph_release import repository

    with monkeypatch.context() as patch:
        patch.setattr(
            repository,
            "_insert_release_rows",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("synthetic bootstrap failure")
            ),
        )
        with pytest.raises(RuntimeError, match="synthetic bootstrap failure"):
            ensure_checked_in_current_standard(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM knowledge_graph_releases"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM knowledge_graph_release_events"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM knowledge_graph_node_profiles"
        ).fetchone()[0] == 0

    assert ensure_checked_in_current_standard(database) == release.release_id


def test_internal_startup_does_not_replace_a_different_active_release(
    tmp_path,
) -> None:
    database = tmp_path / "question_bank.db"
    initialize_database(database)
    first_id = ensure_checked_in_current_standard(database)
    first = load_release()
    payload = copy.deepcopy(first.payload)
    payload["release_id"] = "kgr_junior_math_internal_rollback"
    payload["predecessor_release_id"] = first.release_id
    payload["content_hash"] = compute_content_hash(payload)
    selected = KnowledgeGraphRelease.from_mapping(payload)
    stage_release(
        database,
        selected,
        actor_ref="test-suite",
        source_reference="synthetic-internal-release",
    )
    activate_release(
        database,
        selected.release_id,
        expected_active_release_id=first_id,
        actor_ref="test-suite",
        reason="select synthetic internal current standard",
    )

    actual = ensure_checked_in_current_standard(database)

    assert actual == selected.release_id
    assert CurrentKnowledgeResolver.from_active_database(database).release_id == (
        selected.release_id
    )
