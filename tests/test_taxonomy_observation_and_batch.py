from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from question_bank.models.question import QuestionCreate
from question_bank.services.question_service import QuestionService
from question_bank.services.question_write_service import (
    ConfirmedQuestionTag,
    QuestionBankWriteService,
)
from question_bank.services.taxonomy_review_service import TaxonomyReviewService
from question_bank.taxonomy.governance import TaxonomyGovernance


CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "question_bank"
    / "taxonomy"
    / "catalogs"
    / "tag_vocabulary_v2.json"
)


def _governance(tmp_path: Path) -> TaxonomyGovernance:
    return TaxonomyGovernance(
        catalog_path=CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=tmp_path / "question-bank.db",
    )


def _proposal(
    governance: TaxonomyGovernance,
    *,
    question_id: int,
    name: str,
    token: str,
) -> tuple[dict, int]:
    result = governance.constrain(
        {
            "proposed_tags": [
                {
                    "dimension": "knowledge",
                    "name": name,
                    "reason": "没有严格匹配的规范词",
                }
            ]
        },
        context={
            "persist_proposals": True,
            "question_ref": str(question_id),
            "request_token": token,
        },
    )
    return result["proposals"][0], int(result["taxonomy_revision"])


def test_latest_started_success_wins_and_empty_success_clears_current_refs(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    first, first_revision = _proposal(
        governance,
        question_id=41,
        name="第一轮候选知识",
        token="1" * 32,
    )
    second, second_revision = _proposal(
        governance,
        question_id=41,
        name="第二轮候选知识",
        token="2" * 32,
    )
    governance.allocate_observation_sequences(
        generation_id="generation-old", question_ids=["41"]
    )
    governance.allocate_observation_sequences(
        generation_id="generation-new", question_ids=["41"]
    )

    governance.record_successful_observation(
        question_id="41",
        generation_id="generation-new",
        proposal_ids=[second["id"]],
        taxonomy_revision=second_revision,
    )
    governance.record_successful_observation(
        question_id="41",
        generation_id="generation-old",
        proposal_ids=[first["id"]],
        taxonomy_revision=first_revision,
    )

    snapshot = governance.observation_snapshot()
    assert snapshot["current_question_refs"].get(first["id"], []) == []
    assert snapshot["current_question_refs"][second["id"]] == [41]

    governance.allocate_observation_sequences(
        generation_id="generation-empty", question_ids=["41"]
    )
    governance.record_successful_observation(
        question_id="41",
        generation_id="generation-empty",
        proposal_ids=[],
        taxonomy_revision=second_revision,
    )

    assert governance.observation_snapshot()["current_question_refs"] == {}
    history = governance.read_audit_history(question_id=41)["items"]
    assert [item["generation_id"] for item in history] == [
        "generation-empty",
        "generation-new",
        "generation-old",
    ]
    assert history[0]["is_current"] is True


def test_unproven_legacy_refs_stay_in_read_only_reconciliation_preview(
    tmp_path: Path,
) -> None:
    governance = _governance(tmp_path)
    proposal, _revision = _proposal(
        governance,
        question_id=51,
        name="无法证明轮次的旧候选",
        token="3" * 32,
    )

    page = governance.list_proposals(status="pending")
    assert page["items"][0]["active_question_refs"] == []
    preview = governance.legacy_observation_reconciliation_preview()
    assert preview["read_only"] is True
    assert preview["items"] == [
        {
            "proposal_id": proposal["id"],
            "historical_question_refs": [51],
            "classification": "historical_unproven",
        }
    ]


def test_exact_batch_advances_once_is_idempotent_and_undo_keeps_preexisting_tag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "question-bank.db"
    question_id = QuestionService(db_path).add_question(
        QuestionCreate(question_number="1", question_text="一元二次方程测试题")
    )
    governance = _governance(tmp_path)
    proposal, proposal_revision = _proposal(
        governance,
        question_id=question_id,
        name="一元二次方程旧写法测试",
        token="4" * 32,
    )
    generation_id = "batch-current-generation"
    governance.allocate_observation_sequences(
        generation_id=generation_id,
        question_ids=[str(question_id)],
    )
    governance.record_successful_observation(
        question_id=str(question_id),
        generation_id=generation_id,
        proposal_ids=[proposal["id"]],
        taxonomy_revision=proposal_revision,
    )
    target = governance.resolve_term("knowledge", "一元二次方程")
    assert target is not None
    write_service = QuestionBankWriteService(db_path, data_root=tmp_path)
    write_service.add_tags(
        question_id,
        tags=[ConfirmedQuestionTag("knowledge_point", target["name"])],
    )
    service = TaxonomyReviewService(
        review_state_path=tmp_path / "taxonomy-review-state.json",
        governance=governance,
        write_service=write_service,
    )
    snapshot = governance.observation_snapshot()
    run = {
        "run_id": "a" * 32,
        "taxonomy_revision": snapshot["taxonomy_revision"],
        "evidence_revision": snapshot["evidence_revision"],
        "graph_release_id": snapshot["graph_release_id"],
        "items": [
            {
                "proposal_id": proposal["id"],
                "status": "suggested",
                "suggestion": {
                    "relation_kind": "exact",
                    "confidence": 0.91,
                    "target_term_ids": [target["id"]],
                    "reason": "定义和适用边界相同",
                    "source": "ai",
                    "legacy_format": False,
                    "evidence_question_ids": [question_id],
                    "taxonomy_revision": snapshot["taxonomy_revision"],
                    "graph_release_id": snapshot["graph_release_id"],
                },
            }
        ],
    }

    preview = service.preview_suggestion_batch(
        run, base_revision=snapshot["taxonomy_revision"]
    )
    assert preview["counts"] == {"automatic": 1, "manual": 0, "total": 1}

    token = "5" * 32
    original_replace = service._replace_receipt_result
    interrupted = False

    def interrupt_after_governance(request_token, result):
        nonlocal interrupted
        if not interrupted and result.get("status") == "applying":
            interrupted = True
            raise RuntimeError("synthetic interruption after taxonomy commit")
        original_replace(request_token, result)

    monkeypatch.setattr(service, "_replace_receipt_result", interrupt_after_governance)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        service.apply_suggestion_batch(
            run,
            base_revision=snapshot["taxonomy_revision"],
            request_token=token,
        )
    recovered_service = TaxonomyReviewService(
        review_state_path=tmp_path / "taxonomy-review-state.json",
        governance=governance,
        write_service=write_service,
    )
    applied = recovered_service.apply_suggestion_batch(
        run, base_revision=snapshot["taxonomy_revision"], request_token=token
    )
    replay = recovered_service.apply_suggestion_batch(
        run,
        base_revision=snapshot["taxonomy_revision"],
        request_token=token,
    )
    assert replay == applied
    assert applied["taxonomy_revision"] == snapshot["taxonomy_revision"] + 1
    assert applied["status"] == "applied"
    assert applied["outbox"][0]["tags"][0]["preexisting"] is True

    undone = recovered_service.undo_operation(
        operation_id=applied["operation_id"],
        expected_revision=applied["taxonomy_revision"],
        request_token="6" * 32,
    )
    assert undone["status"] == "undone"
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            """
            SELECT COUNT(*) FROM question_tags
            WHERE question_id = ? AND tag_type = 'knowledge_point' AND tag_value = ?
            """,
            (question_id, target["name"]),
        ).fetchone()[0]
    assert count == 1


def test_batch_undo_removes_only_its_insert_and_keeps_the_audit_row(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question-bank.db"
    question_id = QuestionService(db_path).add_question(
        QuestionCreate(question_number="2", question_text="一元二次方程练习")
    )
    governance = _governance(tmp_path)
    proposal, proposal_revision = _proposal(
        governance,
        question_id=question_id,
        name="一元二次方程另一种写法",
        token="7" * 32,
    )
    governance.allocate_observation_sequences(
        generation_id="batch-new-tag", question_ids=[str(question_id)]
    )
    governance.record_successful_observation(
        question_id=str(question_id),
        generation_id="batch-new-tag",
        proposal_ids=[proposal["id"]],
        taxonomy_revision=proposal_revision,
    )
    target = governance.resolve_term("knowledge", "一元二次方程")
    assert target is not None
    service = TaxonomyReviewService(
        review_state_path=tmp_path / "taxonomy-review-state.json",
        governance=governance,
        write_service=QuestionBankWriteService(db_path, data_root=tmp_path),
    )
    snapshot = governance.observation_snapshot()
    run = {
        "run_id": "b" * 32,
        "taxonomy_revision": snapshot["taxonomy_revision"],
        "evidence_revision": snapshot["evidence_revision"],
        "graph_release_id": snapshot["graph_release_id"],
        "items": [
            {
                "proposal_id": proposal["id"],
                "status": "suggested",
                "suggestion": {
                    "relation_kind": "exact",
                    "confidence": 0.93,
                    "target_term_ids": [target["id"]],
                    "reason": "定义相同",
                    "source": "ai",
                    "legacy_format": False,
                    "evidence_question_ids": [question_id],
                    "taxonomy_revision": snapshot["taxonomy_revision"],
                    "graph_release_id": snapshot["graph_release_id"],
                },
            }
        ],
    }

    applied = service.apply_suggestion_batch(
        run,
        base_revision=snapshot["taxonomy_revision"],
        request_token="8" * 32,
    )
    assert applied["outbox"][0]["tags"][0]["preexisting"] is False
    undone = service.undo_operation(
        operation_id=applied["operation_id"],
        expected_revision=applied["taxonomy_revision"],
        request_token="9" * 32,
    )
    assert undone["undo_status"] == "completed"

    with sqlite3.connect(db_path) as conn:
        tag_count = conn.execute(
            """
            SELECT COUNT(*) FROM question_tags
            WHERE question_id = ? AND tag_type = 'knowledge_point' AND tag_value = ?
            """,
            (question_id, target["name"]),
        ).fetchone()[0]
        ledger = conn.execute(
            """
            SELECT question_tag_id, preexisting, write_status, undo_status
            FROM taxonomy_review_applications
            WHERE operation_id = ?
            """,
            (applied["operation_id"],),
        ).fetchone()
    assert tag_count == 0
    assert ledger == (None, 0, "applied", "removed")
