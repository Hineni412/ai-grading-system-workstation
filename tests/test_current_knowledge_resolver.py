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


def test_question_type_helpers_gate_on_release_nodes() -> None:
    """题型键识别 + 无题型节点的发布保持 sk_ 训练目标行为。"""
    from types import SimpleNamespace

    from question_bank.question_types import (
        _clear_type_cache_for_tests,
        is_training_target,
        is_type_key,
        question_type_key,
        type_keys_active,
    )

    _clear_type_cache_for_tests()
    type_key = "kp_bnu24_math_g8_upper_1_1_t01"
    assert is_type_key(type_key)
    assert is_type_key("kp_bnu24_math_g8_upper_7_3_t22")
    assert not is_type_key("kp_bnu24_math_g8_upper_1_1")      # section
    assert not is_type_key("kp_bnu24_math_g8_upper_1_1_1")    # leaf
    assert not is_type_key("sk_bnu24_math_g8_upper_1_1_01")   # skill
    assert not is_type_key("kp_bnu24_math_g8_upper_1_1_t1")   # one digit
    assert not is_type_key("kp_bnu24_math_g8_upper_1_1_t012")  # three digits
    assert not is_type_key("")
    assert not is_type_key(None)

    with_types = SimpleNamespace(
        release_id="test_release_with_types",
        nodes=[
            SimpleNamespace(stable_key="kp_bnu24_math_g8_upper_1_1"),
            SimpleNamespace(stable_key=type_key),
        ],
    )
    without_types = SimpleNamespace(
        release_id="test_release_without_types",
        nodes=[
            SimpleNamespace(stable_key="kp_bnu24_math_g8_upper_1_1"),
            SimpleNamespace(stable_key="sk_bnu24_math_g8_upper_1_1_01"),
        ],
    )
    assert type_keys_active(with_types) is True
    assert type_keys_active(with_types) is True  # second call hits the cache
    assert type_keys_active(with_types, "bnu24-math-g8-upper") is True
    assert type_keys_active(with_types, "bnu24-math-g7-upper") is False
    assert type_keys_active(with_types, "TEST-unknown-volume") is False
    assert type_keys_active(without_types) is False
    assert type_keys_active(None) is False

    assert is_training_target(type_key, with_types)
    assert not is_training_target(
        "sk_bnu24_math_g8_upper_1_1_01", with_types
    )
    # A release without type nodes keeps the legacy sk_ behaviour unchanged.
    assert is_training_target(
        "sk_bnu24_math_g8_upper_1_1_01", without_types
    )
    assert not is_training_target(type_key, without_types)
    assert not is_training_target(type_key, None)

    assert question_type_key(("other", type_key, "kp_x")) == type_key
    assert question_type_key(
        ("sk_bnu24_math_g8_upper_1_1_01", "kp_bnu24_math_g8_upper_1_1")
    ) == ""
    assert question_type_key(()) == ""
    assert question_type_key(None) == ""
    _clear_type_cache_for_tests()


@pytest.mark.parametrize("revision", [10, 11], ids=["v8", "v9-mixed"])
def test_training_targets_follow_each_volumes_active_nodes(revision):
    from question_bank.knowledge_graph_release.loader import (
        load_release_for_taxonomy_revision, load_taxonomy_catalog_for_release,
    )
    from question_bank.question_types import is_training_target, type_keys_active
    from question_bank.taxonomy.curriculum_catalog import load_curriculum_catalog

    release = load_release_for_taxonomy_revision(revision)
    resolver = CurrentKnowledgeResolver(release, load_taxonomy_catalog_for_release(release))
    for volume in load_curriculum_catalog()["volumes"]:
        volume_id = volume["id"]
        converted = revision == 11 and volume_id == "bnu24-math-g8-upper"
        assert type_keys_active(resolver, volume_id) is converted
        # Repeated cross-volume queries must not reuse the first volume's mode.
        assert type_keys_active(resolver, "bnu24-math-g8-upper") is (revision == 11)
        assert type_keys_active(resolver, volume_id) is converted
        skills = [node.stable_key for node in resolver.nodes
                  if node.stable_key.startswith("sk_" + volume_id.replace("-", "_") + "_")]
        for key in skills:
            expected_skill = not converted and not (revision == 11 and volume_id == "bnu24-math-g8-lower")
            assert is_training_target(key, resolver, volume_id) is expected_skill
            assert is_training_target(key, resolver) is expected_skill
    assert is_training_target("kp_bnu24_math_g8_upper_1_1_t01", resolver) is (revision == 11)


def test_training_target_volume_uses_resolver_parent_before_key_namespace():
    from types import SimpleNamespace
    from question_bank.question_types import is_training_target, type_keys_active

    # A synthetic key outside the bundled namespace still has a precise parent.
    resolver = SimpleNamespace(release_id="TEST-mixed-parent-scopes", nodes=[
        SimpleNamespace(stable_key="kp_test_parent_t01"),
        SimpleNamespace(stable_key="sk_TEST_g7"),
        SimpleNamespace(stable_key="sk_TEST_g8"),
    ], relations=[SimpleNamespace(relation_type="parent", source_key=child, target_key=parent)
        for child, parent in (("kp_test_parent_t01", "kp_bnu24_math_g8_upper_1_1"),
                              ("sk_TEST_g7", "kp_bnu24_math_g7_upper_1_1"),
                              ("sk_TEST_g8", "kp_bnu24_math_g8_upper_1_1"))])
    assert type_keys_active(resolver, "bnu24-math-g8-upper")
    assert not type_keys_active(resolver, "bnu24-math-g7-upper")
    assert is_training_target("sk_TEST_g7", resolver)
    assert not is_training_target("sk_TEST_g8", resolver)


def test_chapter_targets_keep_old_volumes_and_mix_new_volume():
    from types import SimpleNamespace
    from question_bank.question_types import (
        chapter_target_kind, chapter_target_kinds, is_training_target, training_keys,
    )
    type_key = "kp_bnu24_math_g8_lower_1_1_t01"
    topic = "kp_bnu24_math_g8_lower_2_1_1"
    old_skill = "sk_bnu24_math_g7_upper_1_1_01"
    current_skill = "sk_bnu24_math_g8_lower_2_1_01"
    resolver = SimpleNamespace(release_id="TEST-chapter-target-policy", taxonomy_revision=11,
        nodes=[SimpleNamespace(stable_key=key) for key in (type_key, topic, old_skill, current_skill)],
        relations=[SimpleNamespace(source_key=type_key, target_key="kp_bnu24_math_g8_lower_1_1",
                                   relation_type="parent")])
    assert chapter_target_kind(resolver, "bnu24-math-g8-lower") == "mixed"
    modes = chapter_target_kinds(resolver, "bnu24-math-g8-lower")
    assert modes["kp_bnu24_math_g8_lower_1"] == "type"
    assert modes["kp_bnu24_math_g8_lower_2"] == "knowledge"
    assert is_training_target(type_key, resolver)
    assert is_training_target(topic, resolver)
    assert not is_training_target(current_skill, resolver)
    assert is_training_target(old_skill, resolver)
    assert training_keys((type_key, topic, current_skill), resolver) == {type_key, topic}
    assert chapter_target_kind(resolver, "bnu24-math-g7-upper") == "skill"
