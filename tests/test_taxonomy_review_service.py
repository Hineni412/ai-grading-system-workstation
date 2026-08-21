from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from question_bank.models.question import QuestionCreate
from tests.question_bank_support import QuestionBankTestStore
from question_bank.services.question_write_service import (
    ConfirmedQuestionTag,
    QuestionBankWriteService,
)
from question_bank.services.taxonomy_review_service import (
    TaxonomyReviewRequestConflict,
    TaxonomyReviewSelectionInvalid,
    TaxonomyReviewService,
)
from question_bank.taxonomy.governance import TaxonomyGovernance


CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "question_bank"
    / "taxonomy"
    / "catalogs"
    / "tag_vocabulary_v2.json"
)


def _persist_curriculum_proposal(
    governance: TaxonomyGovernance,
    *,
    question_ids: list[int],
) -> dict:
    result = None
    for index, question_id in enumerate(question_ids):
        result = governance.constrain(
            {
                "proposed_tags": [
                    {
                        "dimension": "curriculum",
                        "name": "跨章节综合归属",
                        "reason": "需要同时归入两个已有章节",
                    }
                ]
            },
            context={
                "persist_proposals": True,
                "question_ref": str(question_id),
                "request_token": f"{index + 1:032x}",
            },
        )
    assert result is not None
    return result["proposals"][0]


def _review_service(
    tmp_path: Path,
) -> tuple[
    TaxonomyReviewService,
    TaxonomyGovernance,
    QuestionBankWriteService,
    int,
]:
    db_path = tmp_path / "question-bank.db"
    question_service = QuestionBankTestStore(db_path)
    question_id = question_service.add_question(
        QuestionCreate(question_number="1", question_text="跨章节综合题")
    )
    write_service = QuestionBankWriteService(db_path, data_root=tmp_path)
    write_service.replace_tags(
        question_id,
        expected_revision=write_service.get_revision(question_id),
        tags=[ConfirmedQuestionTag("knowledge_point", "三角形综合")],
    )
    # knowledge_graph_db_path 缺省会指向 path_manager 的会话级共享题库库，
    # 全量跑时其他测试的应用启动会把签入标准(revision 4)装进去，
    # 与本文件词表的 revision 冲突；这里指向本测试私有路径(文件不存在即跳过激活版本检查)。
    governance = TaxonomyGovernance(
        catalog_path=CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=tmp_path / "taxonomy-knowledge-graph.db",
    )
    return (
        TaxonomyReviewService(
            review_state_path=tmp_path / "taxonomy-review-state.json",
            governance=governance,
            write_service=write_service,
        ),
        governance,
        write_service,
        question_id,
    )


def test_multi_target_merge_is_written_back_without_replacing_other_tags(
    tmp_path: Path,
) -> None:
    service, governance, _write_service, question_id = _review_service(tmp_path)
    proposal = _persist_curriculum_proposal(
        governance,
        question_ids=[question_id],
    )
    first = governance.resolve_term(
        "curriculum", "七年级下册 第二章 相交线与平行线"
    )
    second = governance.resolve_term(
        "curriculum", "七年级下册 第四章 三角形"
    )
    assert first and second
    token = "a" * 32

    result = service.review_proposal(
        proposal_id=proposal["id"],
        decision="merge",
        expected_revision=governance.list_proposals(status="pending")["revision"],
        request_token=token,
        target_term_ids=[first["id"], second["id"]],
        question_ids=[question_id],
    )
    replay = service.review_proposal(
        proposal_id=proposal["id"],
        decision="merge",
        expected_revision=result["revision"] - 1,
        request_token=token,
        target_term_ids=[first["id"], second["id"]],
        question_ids=[question_id],
    )

    assert replay == result
    assert result["proposal"]["status"] == "merged"
    assert result["proposal"]["resolved_term_ids"] == [first["id"], second["id"]]
    assert result["application"] == {
        "status": "applied",
        "selected_question_ids": [question_id],
        "applied_question_ids": [question_id],
        "failures": [],
    }
    with sqlite3.connect(service.write_service.db_path) as conn:
        rows = conn.execute(
            """
            SELECT tag_type, tag_value
            FROM question_tags
            WHERE question_id = ?
            ORDER BY tag_type, tag_value
            """,
            (question_id,),
        ).fetchall()
    assert ("knowledge_point", "三角形综合") in rows
    assert ("exam_scope", first["name"]) in rows
    assert ("exam_scope", second["name"]) in rows
    assert rows.count(("exam_scope", first["name"])) == 1
    assert rows.count(("exam_scope", second["name"])) == 1


def test_review_request_token_cannot_be_reused_for_a_different_selection(
    tmp_path: Path,
) -> None:
    service, governance, _write_service, question_id = _review_service(tmp_path)
    proposal = _persist_curriculum_proposal(
        governance,
        question_ids=[question_id],
    )
    target = governance.resolve_term(
        "curriculum", "七年级下册 第二章 相交线与平行线"
    )
    assert target
    revision = governance.list_proposals(status="pending")["revision"]
    token = "b" * 32
    service.review_proposal(
        proposal_id=proposal["id"],
        decision="merge",
        expected_revision=revision,
        request_token=token,
        target_term_ids=[target["id"]],
        question_ids=[question_id],
    )

    with pytest.raises(TaxonomyReviewRequestConflict):
        service.review_proposal(
            proposal_id=proposal["id"],
            decision="merge",
            expected_revision=revision,
            request_token=token,
            target_term_ids=[target["id"]],
            question_ids=[],
        )


def test_review_rejects_question_outside_the_proposal_evidence(
    tmp_path: Path,
) -> None:
    service, governance, _write_service, question_id = _review_service(tmp_path)
    proposal = _persist_curriculum_proposal(
        governance,
        question_ids=[question_id],
    )
    target = governance.resolve_term(
        "curriculum", "七年级下册 第二章 相交线与平行线"
    )
    assert target

    with pytest.raises(TaxonomyReviewSelectionInvalid):
        service.review_proposal(
            proposal_id=proposal["id"],
            decision="merge",
            expected_revision=governance.list_proposals(status="pending")[
                "revision"
            ],
            request_token="c" * 32,
            target_term_ids=[target["id"]],
            question_ids=[question_id + 100],
        )


def test_partial_application_can_retry_only_failed_questions(
    tmp_path: Path,
) -> None:
    service, governance, _write_service, question_id = _review_service(tmp_path)
    missing_question_id = 999
    proposal = _persist_curriculum_proposal(
        governance,
        question_ids=[question_id, missing_question_id],
    )
    target = governance.resolve_term(
        "curriculum", "七年级下册 第二章 相交线与平行线"
    )
    assert target
    first_token = "d" * 32

    first = service.review_proposal(
        proposal_id=proposal["id"],
        decision="merge",
        expected_revision=governance.list_proposals(status="pending")["revision"],
        request_token=first_token,
        target_term_ids=[target["id"]],
        question_ids=[question_id, missing_question_id],
    )

    assert first["application"]["status"] == "partial"
    assert first["application"]["applied_question_ids"] == [question_id]
    assert first["application"]["failures"][0]["question_id"] == missing_question_id

    with sqlite3.connect(service.write_service.db_path) as conn:
        conn.execute(
            """
            INSERT INTO questions (id, question_number, question_text)
            VALUES (?, '2', '稍后恢复的关联题目')
            """,
            (missing_question_id,),
        )
        conn.commit()

    retried = service.retry_application(
        application_token=first_token,
        request_token="e" * 32,
    )

    assert retried["application"]["status"] == "applied"
    assert retried["application"]["applied_question_ids"] == [
        question_id,
        missing_question_id,
    ]
    assert retried["application"]["failures"] == []
    with sqlite3.connect(service.write_service.db_path) as conn:
        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM question_tags
            WHERE question_id = ? AND tag_type = 'exam_scope' AND tag_value = ?
            """,
            (missing_question_id, target["name"]),
        ).fetchone()[0]
    assert count == 1
