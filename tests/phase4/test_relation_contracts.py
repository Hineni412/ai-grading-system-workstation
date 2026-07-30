from __future__ import annotations

import pytest

from question_bank.relations.contracts import (
    KnowledgeIdentity,
    KnowledgeRelation,
    RelationConflict,
    RelationStatus,
    RelationType,
    canonical_relation_key,
    find_confirmation_conflicts,
    normalize_stable_key,
)


ALGEBRA = "kp_alg_real_numbers"
EQUATION = "kp_alg_linear_equation"
FUNCTION = "kp_fun_linear_function"


def _confirmed(
    source: str,
    target: str,
    relation_type: RelationType,
) -> KnowledgeRelation:
    return KnowledgeRelation(
        source_key=source,
        target_key=target,
        relation_type=relation_type,
        status=RelationStatus.CONFIRMED,
    )


def test_stable_identity_survives_display_name_and_alias_changes() -> None:
    original = KnowledgeIdentity(
        stable_key="KP_ALG_REAL_NUMBERS",
        display_name="实数",
        aliases=("有理数", "实数"),
    )
    renamed = KnowledgeIdentity(
        stable_key=original.stable_key,
        display_name="实数及其运算",
        aliases=(*original.aliases, original.display_name),
    )

    assert original.stable_key == renamed.stable_key == ALGEBRA
    assert renamed.aliases == ("有理数", "实数")


@pytest.mark.parametrize(
    "value",
    ["knowledge_point:实数", "kp_含中文", "ki_1234", "skill-12", ""],
)
def test_uncontrolled_or_name_derived_identity_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_stable_key(value)


def test_local_governed_identity_uses_opaque_key() -> None:
    assert normalize_stable_key("KI_0123456789ABCDEF0123456789ABCDEF") == (
        "ki_0123456789abcdef0123456789abcdef"
    )


def test_related_relation_has_one_canonical_unordered_identity() -> None:
    forward = canonical_relation_key(ALGEBRA, EQUATION, RelationType.RELATED)
    reverse = canonical_relation_key(EQUATION, ALGEBRA, RelationType.RELATED)

    assert forward == reverse


def test_self_relation_is_rejected_before_graph_checks() -> None:
    candidate = KnowledgeRelation(ALGEBRA, ALGEBRA, RelationType.PARENT)

    assert find_confirmation_conflicts(candidate, ()) == (
        RelationConflict.SELF_RELATION,
    )


@pytest.mark.parametrize(
    ("existing_type", "candidate_type", "expected"),
    [
        (
            RelationType.PARENT,
            RelationType.PARENT,
            RelationConflict.DUPLICATE,
        ),
        (
            RelationType.PREREQUISITE,
            RelationType.PREREQUISITE,
            RelationConflict.DUPLICATE,
        ),
        (
            RelationType.RELATED,
            RelationType.PARENT,
            RelationConflict.TYPE_CONFLICT,
        ),
    ],
)
def test_same_knowledge_pair_cannot_have_two_active_meanings(
    existing_type: RelationType,
    candidate_type: RelationType,
    expected: RelationConflict,
) -> None:
    existing = _confirmed(ALGEBRA, EQUATION, existing_type)
    candidate = KnowledgeRelation(ALGEBRA, EQUATION, candidate_type)

    assert expected in find_confirmation_conflicts(candidate, (existing,))


def test_reverse_directed_relation_is_a_conflict() -> None:
    existing = _confirmed(ALGEBRA, EQUATION, RelationType.PARENT)
    candidate = KnowledgeRelation(EQUATION, ALGEBRA, RelationType.PARENT)

    assert find_confirmation_conflicts(candidate, (existing,)) == (
        RelationConflict.REVERSE_CONFLICT,
    )


@pytest.mark.parametrize(
    ("relation_type", "expected"),
    [
        (RelationType.PARENT, RelationConflict.PARENT_CYCLE),
        (
            RelationType.PREREQUISITE,
            RelationConflict.PREREQUISITE_CYCLE,
        ),
    ],
)
def test_directed_relation_cycles_are_rejected(
    relation_type: RelationType,
    expected: RelationConflict,
) -> None:
    active = (
        _confirmed(ALGEBRA, EQUATION, relation_type),
        _confirmed(EQUATION, FUNCTION, relation_type),
    )
    candidate = KnowledgeRelation(FUNCTION, ALGEBRA, relation_type)

    assert find_confirmation_conflicts(candidate, active) == (expected,)


def test_suggested_or_retired_relations_do_not_block_active_graph() -> None:
    inactive = (
        KnowledgeRelation(
            ALGEBRA,
            EQUATION,
            RelationType.RELATED,
            RelationStatus.SUGGESTED,
        ),
        KnowledgeRelation(
            EQUATION,
            FUNCTION,
            RelationType.PARENT,
            RelationStatus.RETIRED,
        ),
    )
    candidate = KnowledgeRelation(ALGEBRA, EQUATION, RelationType.PARENT)

    assert find_confirmation_conflicts(candidate, inactive) == ()
