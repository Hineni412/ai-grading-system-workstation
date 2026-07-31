from __future__ import annotations

from pathlib import Path

import pytest

from question_bank.database.schema import initialize_database
from question_bank.relations.contracts import (
    KnowledgeRelation,
    RelationStatus,
    RelationType,
)
from question_bank.relations.repository import (
    KnowledgeRelationConfirmationConflict,
    KnowledgeRelationRepository,
    KnowledgeRelationRevisionConflict,
)
from question_bank.relations.review_service import (
    RelationReviewCommand,
    RelationReviewService,
)


@pytest.fixture
def review_store(
    tmp_path: Path,
) -> tuple[RelationReviewService, KnowledgeRelationRepository]:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    return (
        RelationReviewService(database),
        KnowledgeRelationRepository(database),
    )


def _suggest(
    repository: KnowledgeRelationRepository,
    source_key: str,
    target_key: str,
    relation_type: RelationType = RelationType.PARENT,
    *,
    source_kind: str = "model",
):
    return repository.create_suggestion(
        KnowledgeRelation(source_key, target_key, relation_type),
        source_kind=source_kind,
        source_reference=(
            "teacher-synthetic" if source_kind == "teacher" else None
        ),
        rationale="合成审核建议",
        model_name=(
            "fake-relation-model" if source_kind == "model" else None
        ),
        model_version="fake-v1" if source_kind == "model" else None,
        prompt_version="relation-prompt-v1",
        confidence=0.93,
    )


def test_review_queue_exposes_source_reason_conflict_and_revision(
    review_store,
) -> None:
    service, repository = review_store
    model_relation = _suggest(
        repository,
        "kp_alg_real_numbers",
        "kp_alg_equation_properties",
    )
    teacher_relation = _suggest(
        repository,
        "kp_alg_equation_properties",
        "kp_alg_linear_equation",
        source_kind="teacher",
    )

    page = service.list_queue()

    assert page["total"] == 2
    by_id = {item["relation_id"]: item for item in page["items"]}
    assert by_id[model_relation.relation_id]["source_kind"] == "model"
    assert by_id[model_relation.relation_id]["model_name"] == "fake-relation-model"
    assert by_id[teacher_relation.relation_id]["source_kind"] == "teacher"
    assert by_id[teacher_relation.relation_id]["model_name"] is None
    assert all(item["rationale"] == "合成审核建议" for item in page["items"])
    assert all(item["revision"] == 1 for item in page["items"])


def test_preview_and_confirm_never_hide_an_active_conflict(
    review_store,
) -> None:
    service, repository = review_store
    active = _suggest(
        repository,
        "kp_alg_real_numbers",
        "kp_alg_equation_properties",
    )
    service.review_one(
        active.relation_id,
        expected_revision=active.revision,
        action="confirm",
        actor_ref="teacher-a",
        reason="确认既有边",
    )
    reverse = _suggest(
        repository,
        "kp_alg_equation_properties",
        "kp_alg_real_numbers",
    )

    preview = service.preview(reverse.relation_id, action="confirm")

    assert preview["can_apply"] is False
    assert preview["conflict_codes"] == ["reverse_conflict"]
    assert preview["activity_effect"] == "add_to_active_graph"
    with pytest.raises(KnowledgeRelationConfirmationConflict):
        service.review_one(
            reverse.relation_id,
            expected_revision=reverse.revision,
            action="confirm",
            actor_ref="teacher-b",
            reason="不应确认",
        )
    assert len(repository.list_active_relations()) == 1


def test_teacher_can_amend_then_confirm_with_complete_timeline(
    review_store,
) -> None:
    service, repository = review_store
    suggested = _suggest(
        repository,
        "kp_alg_real_numbers",
        "kp_alg_equation_properties",
    )

    amended = service.review_one(
        suggested.relation_id,
        expected_revision=suggested.revision,
        action="amend",
        actor_ref="teacher-a",
        reason="改为更准确的先修关系",
        amended_relation=KnowledgeRelation(
            "kp_alg_linear_equation",
            "kp_alg_equation_properties",
            RelationType.PREREQUISITE,
        ),
    )
    confirmed = service.review_one(
        suggested.relation_id,
        expected_revision=amended["relation"]["revision"],
        action="confirm",
        actor_ref="teacher-a",
        reason="依据课程顺序确认",
    )

    assert confirmed["relation"]["status"] == "confirmed"
    assert confirmed["relation"]["relation_type"] == "prerequisite"
    timeline = confirmed["timeline"]
    assert [event["event_type"] for event in timeline] == [
        "suggested",
        "amended",
        "confirmed",
    ]
    assert [event["revision"] for event in timeline] == [1, 2, 3]
    assert timeline[1]["actor_ref"] == "teacher-a"
    assert timeline[1]["change"]["from"]["relation_type"] == "parent"
    assert timeline[1]["change"]["to"]["relation_type"] == "prerequisite"


def test_reject_and_retire_have_different_active_graph_effects(
    review_store,
) -> None:
    service, repository = review_store
    rejected = _suggest(
        repository,
        "kp_alg_real_numbers",
        "kp_alg_equation_properties",
    )
    rejection = service.review_one(
        rejected.relation_id,
        expected_revision=rejected.revision,
        action="reject",
        actor_ref="teacher-a",
        reason="证据不足",
    )
    assert rejection["relation"]["status"] == "rejected"
    assert repository.list_active_relations() == ()

    confirmed = _suggest(
        repository,
        "kp_alg_equation_properties",
        "kp_alg_linear_equation",
    )
    active = service.review_one(
        confirmed.relation_id,
        expected_revision=confirmed.revision,
        action="confirm",
        actor_ref="teacher-a",
        reason="确认有效",
    )
    assert len(repository.list_active_relations()) == 1
    preview = service.preview(confirmed.relation_id, action="retire")
    assert preview["recommendation_effect"] == "unavailable_immediately"
    retired = service.review_one(
        confirmed.relation_id,
        expected_revision=active["relation"]["revision"],
        action="retire",
        actor_ref="teacher-a",
        reason="课程口径调整",
    )
    assert retired["relation"]["status"] == "retired"
    assert repository.list_active_relations() == ()


def test_stale_page_cannot_overwrite_newer_teacher_decision(
    review_store,
) -> None:
    service, repository = review_store
    suggested = _suggest(
        repository,
        "kp_alg_real_numbers",
        "kp_alg_equation_properties",
    )
    confirmed = service.review_one(
        suggested.relation_id,
        expected_revision=1,
        action="confirm",
        actor_ref="teacher-new",
        reason="新页面先确认",
    )

    with pytest.raises(KnowledgeRelationRevisionConflict) as caught:
        service.review_one(
            suggested.relation_id,
            expected_revision=1,
            action="retire",
            actor_ref="teacher-stale",
            reason="过期页面",
        )
    assert caught.value.current_revision == confirmed["relation"]["revision"]
    assert repository.get_relation(suggested.relation_id).status is RelationStatus.CONFIRMED


def test_batch_never_confirms_mutually_conflicting_edges(
    review_store,
) -> None:
    service, repository = review_store
    forward = _suggest(
        repository,
        "kp_alg_real_numbers",
        "kp_alg_equation_properties",
    )
    reverse = _suggest(
        repository,
        "kp_alg_equation_properties",
        "kp_alg_real_numbers",
    )

    result = service.review_batch(
        [
            RelationReviewCommand(
                forward.relation_id,
                forward.revision,
                "confirm",
                "批量确认",
            ),
            RelationReviewCommand(
                reverse.relation_id,
                reverse.revision,
                "confirm",
                "批量确认",
            ),
        ],
        actor_ref="teacher-a",
    )

    assert result["status"] == "failed"
    assert result["applied_count"] == 0
    assert all(
        item["category"] == "confirmation_conflict"
        for item in result["results"]
    )
    assert repository.list_active_relations() == ()


def test_batch_reports_partial_failure_without_overwriting_stale_item(
    review_store,
) -> None:
    service, repository = review_store
    first = _suggest(
        repository,
        "kp_alg_real_numbers",
        "kp_alg_equation_properties",
    )
    second = _suggest(
        repository,
        "kp_alg_equation_properties",
        "kp_alg_linear_equation",
    )
    service.review_one(
        first.relation_id,
        expected_revision=first.revision,
        action="reject",
        actor_ref="teacher-new",
        reason="先行决定",
    )

    result = service.review_batch(
        [
            RelationReviewCommand(
                first.relation_id,
                first.revision,
                "reject",
                "过期批量决定",
            ),
            RelationReviewCommand(
                second.relation_id,
                second.revision,
                "reject",
                "有效批量决定",
            ),
        ],
        actor_ref="teacher-batch",
    )

    assert result["status"] == "partial"
    assert result["applied_count"] == 1
    assert result["failed_count"] == 1
    assert result["results"][0]["category"] == "revision_conflict"
    assert result["results"][1]["status"] == "applied"
    assert repository.get_relation(first.relation_id).decision_by == "teacher-new"
    assert repository.get_relation(second.relation_id).decision_by == "teacher-batch"
