from __future__ import annotations

import errno
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import (
    get_taxonomy_review_service,
)
from question_bank.models.question import QuestionCreate
from question_bank.services.question_service import QuestionService
from question_bank.services.question_write_service import QuestionBankWriteService
from question_bank.services.taxonomy_review_service import TaxonomyReviewService
from question_bank.taxonomy.governance import (
    TaxonomyGovernance,
    TaxonomyStorageError,
)


CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "question_bank"
    / "taxonomy"
    / "catalogs"
    / "tag_vocabulary_v2.json"
)


def test_teacher_facing_knowledge_graph_release_switch_endpoints_are_removed() -> None:
    app = create_app()
    client = TestClient(app)
    assert client.get(
        "/api/question-bank/knowledge-graph/release-preview"
    ).status_code == 404
    for action in ("stage", "demo-release/activate", "demo-release/rollback"):
        assert client.post(
            f"/api/question-bank/knowledge-graph/releases/{action}", json={}
        ).status_code == 404


def _client_with_curriculum_proposal(
    tmp_path: Path,
) -> tuple[TestClient, TaxonomyGovernance, int, str]:
    db_path = tmp_path / "question-bank.db"
    question_id = QuestionService(db_path).add_question(
        QuestionCreate(
            question_number="1",
            question_text="一道同时涉及平行线和三角形的综合题",
        )
    )
    governance = TaxonomyGovernance(
        catalog_path=CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
    )
    created = governance.constrain(
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
            "request_token": "1" * 32,
        },
    )
    proposal_id = str(created["proposals"][0]["id"])
    service = TaxonomyReviewService(
        review_state_path=tmp_path / "taxonomy-review-state.json",
        governance=governance,
        write_service=QuestionBankWriteService(db_path, data_root=tmp_path),
    )
    app = create_app()
    app.dependency_overrides[get_taxonomy_review_service] = lambda: service
    return TestClient(app), governance, question_id, proposal_id


def test_taxonomy_review_route_maps_one_question_to_multiple_existing_chapters(
    tmp_path: Path,
) -> None:
    client, governance, question_id, proposal_id = (
        _client_with_curriculum_proposal(tmp_path)
    )
    first = governance.resolve_term(
        "curriculum",
        "七年级下册 第二章 相交线与平行线",
    )
    second = governance.resolve_term(
        "curriculum",
        "七年级下册 第四章 三角形",
    )
    assert first and second

    response = client.post(
        f"/api/question-bank/taxonomy/proposals/{proposal_id}/review",
        json={
            "decision": "merge",
            "target_term_ids": [first["id"], second["id"]],
            "question_ids": [question_id],
            "expected_revision": governance.list_proposals(status="pending")[
                "revision"
            ],
            "request_token": "2" * 32,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["proposal"]["resolved_term_ids"] == [
        first["id"],
        second["id"],
    ]
    assert body["application"]["status"] == "applied"
    assert body["application"]["applied_question_ids"] == [question_id]
    assert body["application_status"] == "applied"
    assert body["application_token"] == "2" * 32

    with sqlite3.connect(
        governance.state_path.parent / "question-bank.db"
    ) as conn:
        rows = conn.execute(
            """
            SELECT tag_type, tag_value
            FROM question_tags
            WHERE question_id = ?
            ORDER BY tag_type, tag_value
            """,
            (question_id,),
        ).fetchall()
    assert rows == [
        ("exam_scope", first["name"]),
        ("exam_scope", second["name"]),
    ]


def test_taxonomy_review_can_approve_without_writing_unselected_questions(
    tmp_path: Path,
) -> None:
    client, governance, question_id, proposal_id = (
        _client_with_curriculum_proposal(tmp_path)
    )
    target = governance.resolve_term(
        "curriculum",
        "七年级下册 第二章 相交线与平行线",
    )
    assert target

    response = client.post(
        f"/api/question-bank/taxonomy/proposals/{proposal_id}/review",
        json={
            "decision": "merge",
            "target_term_ids": [target["id"]],
            "question_ids": [],
            "expected_revision": governance.list_proposals(status="pending")[
                "revision"
            ],
            "request_token": "3" * 32,
        },
    )

    assert response.status_code == 200
    assert response.json()["application"]["status"] == "not_requested"
    with sqlite3.connect(
        governance.state_path.parent / "question-bank.db"
    ) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM question_tags WHERE question_id = ?",
            (question_id,),
        ).fetchone()[0]
    assert count == 0


@pytest.mark.parametrize(
    ("failure", "expected_code", "expected_category"),
    [
        (
            PermissionError(errno.EACCES, "write access denied"),
            "taxonomy_storage_read_only",
            "read_only",
        ),
        (
            TaxonomyStorageError(
                "Timed out waiting for taxonomy lock: hidden-machine-path"
            ),
            "taxonomy_storage_busy",
            "busy",
        ),
        (
            RuntimeError("Taxonomy review receipt state is invalid"),
            "taxonomy_storage_invalid",
            "invalid",
        ),
        (
            OSError(errno.EIO, "storage unavailable"),
            "taxonomy_storage_unavailable",
            "unavailable",
        ),
    ],
)
def test_taxonomy_review_route_reports_safe_storage_failure_categories(
    failure: Exception,
    expected_code: str,
    expected_category: str,
) -> None:
    class FailingReviewService:
        def review_proposal(self, **_kwargs: object) -> dict[str, object]:
            raise failure

    app = create_app()
    app.dependency_overrides[get_taxonomy_review_service] = (
        lambda: FailingReviewService()
    )
    request_id = f"taxonomy-{expected_category}-request"
    response = TestClient(app).post(
        "/api/question-bank/taxonomy/proposals/proposal-test/review",
        headers={"x-request-id": request_id},
        json={
            "decision": "reject",
            "expected_revision": 0,
            "request_token": "a" * 32,
        },
    )

    assert response.status_code == 503
    assert response.headers["x-request-id"] == request_id
    body = response.json()["error"]
    assert body["code"] == expected_code
    assert body["details"]["category"] == expected_category
    assert body["request_id"] == request_id
    assert "hidden-machine-path" not in response.text
