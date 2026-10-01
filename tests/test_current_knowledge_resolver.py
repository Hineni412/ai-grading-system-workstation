from __future__ import annotations

import copy
import sqlite3

import pytest

from question_bank.current_knowledge import (
    CurrentKnowledgeResolver,
    ensure_checked_in_current_standard,
)
from question_bank.database.schema import initialize_database
from question_bank.knowledge_graph_release.loader import (
    load_release,
)
from question_bank.knowledge_graph_release import (
    activate_release,
    rollback_release,
    stage_release,
)
from question_bank.knowledge_graph_release.contracts import (
    KnowledgeGraphRelease,
    compute_content_hash,
)


def test_active_release_switch_and_rollback_change_cache_identity(tmp_path) -> None:
    from question_bank import current_knowledge as current_module

    database = tmp_path / "question_bank.db"
    initialize_database(database)
    first_id = ensure_checked_in_current_standard(database)
    current_module._clear_resolver_cache_for_tests()
    first = CurrentKnowledgeResolver.from_active_database(database)
    payload = copy.deepcopy(load_release().payload)
    payload["release_id"] = "kgr_cache_switch_test"
    payload["predecessor_release_id"] = first_id
    payload["content_hash"] = compute_content_hash(payload)
    selected = KnowledgeGraphRelease.from_mapping(payload)
    stage_release(
        database,
        selected,
        actor_ref="test-suite",
        source_reference="synthetic-cache-release",
    )
    activate_release(
        database,
        selected.release_id,
        expected_active_release_id=first_id,
        actor_ref="test-suite",
        reason="verify resolver cache switch",
    )
    try:
        switched = CurrentKnowledgeResolver.from_active_database(database)
        rollback_release(
            database,
            first_id,
            expected_active_release_id=selected.release_id,
            actor_ref="test-suite",
            reason="verify resolver cache rollback",
        )
        restored = CurrentKnowledgeResolver.from_active_database(database)
    finally:
        current_module._clear_resolver_cache_for_tests()

    assert switched.release_id == selected.release_id
    assert switched is not first
    assert restored is first


def test_resolver_load_reuses_the_release_identity_cache(tmp_path, monkeypatch) -> None:
    from question_bank import current_knowledge as current_module
    from question_bank.knowledge_graph_release import contracts

    database = tmp_path / "question_bank.db"
    initialize_database(database)
    ensure_checked_in_current_standard(database)
    current_module._clear_resolver_cache_for_tests()
    contracts._clear_release_cache_for_tests()

    calls = 0
    original = KnowledgeGraphRelease.from_mapping

    def counting(cls, raw):
        nonlocal calls
        calls += 1
        return original(raw)

    monkeypatch.setattr(
        KnowledgeGraphRelease, "from_mapping", classmethod(counting)
    )

    first = CurrentKnowledgeResolver.from_active_database(database)
    assert calls == 1

    current_module._clear_resolver_cache_for_tests()
    second = CurrentKnowledgeResolver.from_active_database(database)
    assert calls == 1
    assert second is not first
    assert second.release_id == first.release_id
