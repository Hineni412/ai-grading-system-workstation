from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import (
    get_data_root,
    get_job_manager,
    get_question_bank_db_path,
    get_training_criterion_module,
)
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from question_bank.database.schema import connect, initialize_database
from question_bank.models.tag_schema import TaggingContext
from question_bank.training_criteria import (
    JUDGMENT_POINTS_SCHEMA,
    QuestionAnalysisInput,
    TrainingCriteriaDraft,
    TrainingCriterionModule,
    TrainingCriterionPoint,
)


def _seed(database: Path) -> None:
    initialize_database(database)
    with connect(database) as connection:
        paper_id = connection.execute(
            """
            INSERT INTO papers (title, import_status)
            VALUES ('P4-10 API 合成试卷', 'completed')
            """
        ).lastrowid
        connection.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type,
                question_text, answer_text
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (1, paper_id, "1", "计算题", "解方程 x+1=2", "x=1"),
                (2, paper_id, "2", "选择题", "选择正确选项", "C"),
            ),
        )


def _client(tmp_path: Path):
    data_root = tmp_path / "data"
    database = data_root / "databases" / "question_bank.db"
    _seed(database)
    module = TrainingCriterionModule(database)
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)

    def backfill_handler(context):
        ids = module.claim_backfill(context.payload["run_id"])
        for question_id in ids:
            module.finish_backfill_item(
                run_id=context.payload["run_id"],
                question_id=question_id,
                status="failed",
                error_category="synthetic",
            )
        run = module.complete_backfill(context.payload["run_id"])
        return {
            "run_id": run["run_id"],
            "status": run["status"],
            "successful_question_ids": [],
            "failed_question_ids": list(ids),
            "retryable": True,
            "question_text": "must not leak",
        }

    manager.register("criterion_backfill", backfill_handler)
    app = create_app()
    app.dependency_overrides[get_data_root] = lambda: data_root
    app.dependency_overrides[get_question_bank_db_path] = lambda: database
    app.dependency_overrides[get_training_criterion_module] = lambda: module
    app.dependency_overrides[get_job_manager] = lambda: manager
    return TestClient(app), manager, module, database


def _draft_body(*, revision: int, parent: str | None):
    return {
        "expected_revision": revision,
        "parent_version_id": parent,
        "request_token": "1" * 32,
        "reason": "教师手工建立判定点",
        "points": [
            {
                "point_id": "p-relation",
                "target": "建立等量关系",
                "observable_evidence": "列出正确方程",
                "equivalent_rules": [],
                "counterexamples": [],
            },
            {
                "point_id": "p-answer",
                "target": "得到结论",
                "observable_evidence": "写出 x=1",
                "equivalent_rules": ["1=x"],
                "counterexamples": [],
            },
        ],
        "auxiliary_rules": ["书写清晰但不计数"],
        "rationale": "教师核对",
        "confidence": 1,
    }


def test_teacher_can_create_review_and_read_immutable_criterion_version(
    tmp_path: Path,
) -> None:
    client, _manager, _module, database = _client(tmp_path)

    missing = client.get("/api/question-bank/criteria/questions/1")
    created = client.post(
        "/api/question-bank/criteria/questions/1/drafts",
        json=_draft_body(revision=0, parent=None),
    )

    assert missing.status_code == 200
    assert missing.json()["state"] == "missing"
    assert created.status_code == 200, created.text
    proposed = created.json()
    assert proposed["available"] is True
    version_id = proposed["current_version"]["version_id"]
    approved = client.post(
        "/api/question-bank/criteria/questions/1/review",
        json={
            "version_id": version_id,
            "expected_revision": proposed["revision"],
            "action": "approve",
            "reason": "原题、答案和判定点已核对",
        },
    )

    assert approved.status_code == 200, approved.text
    assert approved.json()["available"] is True
    old_version = client.get(f"/api/question-bank/criteria/versions/{version_id}")
    assert old_version.status_code == 200
    assert old_version.json()["status"] == "approved"

    with connect(database) as connection:
        connection.execute(
            "UPDATE questions SET question_text = ? WHERE id = 1",
            ("题干变化：解方程 x+2=3",),
        )
    stale = client.get("/api/question-bank/criteria/questions/1")
    assert stale.status_code == 200
    assert stale.json()["available"] is False
    # 只读视图不再把持久化状态改写成 stale：版本仍记录 approved，
    # 但内容哈希已对不上，available 拒绝其进入训练；stale 标记只在
    # propose/review 写路径发生。
    assert stale.json()["current_version"]["status"] == "approved"
    assert (
        client.get(f"/api/question-bank/criteria/versions/{version_id}").json()[
            "criteria"
        ]
        == old_version.json()["criteria"]
    )


def test_atomic_criterion_approval_and_revision_conflict_are_public(
    tmp_path: Path,
) -> None:
    client, _manager, _module, _database = _client(tmp_path)
    created = client.post(
        "/api/question-bank/criteria/questions/1/drafts",
        json={
            **_draft_body(revision=0, parent=None),
            "points": [
                {
                    "point_id": "p-answer",
                    "target": "完成解答",
                    "observable_evidence": "答案正确",
                    "equivalent_rules": [],
                    "counterexamples": [],
                }
            ],
        },
    ).json()
    version_id = created["current_version"]["version_id"]

    quality = client.post(
        "/api/question-bank/criteria/questions/1/review",
        json={
            "version_id": version_id,
            "expected_revision": created["revision"],
            "action": "approve",
            "reason": "尝试批准",
        },
    )
    conflict = client.post(
        "/api/question-bank/criteria/questions/1/review",
        json={
            "version_id": version_id,
            "expected_revision": 99,
            "action": "reject",
            "reason": "过期页面",
        },
    )

    assert quality.status_code == 200
    assert quality.json()["current_version"]["status"] == "approved"
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == ("criterion_revision_conflict")
