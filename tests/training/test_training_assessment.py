from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.training_assessment import (
    AssessmentActionCommand,
    AssessmentOperationConflict,
    AssessmentReviewConflict,
    AssessmentRevisionConflict,
    EvidenceSyncCommand,
    FakeTrainingAssessmentGateway,
    ReviewPointCommand,
    SQLiteTrainingEvidenceSink,
    TrainingAssessmentModule,
)
from question_bank.recommendation.personalized import (
    PersonalizedRecommendationConfig,
)
from question_bank.database.schema import connect, initialize_database
from question_bank.relations.query_service import (
    CurrentGraphQuery,
    CurrentKnowledgeGraphQueryService,
)
from tests.training.test_personalized_recommendation import _install_release_with_skills


DRAFT_ID = "1" * 64
PAPER_ID = "2" * 64
PAPER_BATCH_ID = "3" * 64
SCAN_BATCH_ID = "4" * 64
SUBMISSION_ID = "5" * 64
UPLOAD_ID = "6" * 64
SCAN_PAGE_ID = "7" * 64
REVISION = 3

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4n"
    "GNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
)


@pytest.fixture()
def assessment_workspace(tmp_path: Path) -> tuple[Path, Path]:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "data"
    initialize_database(db_path)
    _install_release_with_skills(
        db_path,
        revision=3,
        skill_parents={
            "sk_alg_linear_equation": "kp_bnu24_math_g7_upper_5_2_1",
            "sk_geo_triangle_congruence": "kp_bnu24_math_g7_lower_4_3_9",
            "sk_geo_construction": "kp_bnu24_math_g7_lower_4_3_4",
            "sk_fun_linear": "kp_bnu24_math_g8_upper_4_2_2",
        },
    )
    page_sha = hashlib.sha256(_PNG).hexdigest()
    relative_page = Path(SCAN_BATCH_ID[:20]) / "pages" / f"{SCAN_PAGE_ID[:32]}.png"
    page_path = data_root / "question_bank" / "training_submissions" / relative_page
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_bytes(_PNG)

    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO personalized_recommendation_drafts (
                draft_id, request_token, input_fingerprint,
                result_version, engine_version, source_version,
                request_json, draft_json, status, revision, created_by
            ) VALUES (?, ?, ?, ?, 'synthetic-engine', ?, '{}', '{}',
                      'reviewed', 1, 'synthetic-teacher')
            """,
            (
                DRAFT_ID,
                "8" * 32,
                "9" * 64,
                "a" * 64,
                "b" * 64,
            ),
        )
        connection.execute(
            """
            INSERT INTO personalized_paper_instances (
                paper_instance_id, operation_token,
                operation_fingerprint, draft_id, draft_revision,
                draft_result_version, paper_batch_id, series_version,
                student_id, student_code_snapshot,
                student_name_snapshot, class_id_snapshot,
                status, revision, layout_version, budget_version,
                budget_json, snapshot_json, signing_secret,
                review_docx_path, review_docx_sha256,
                reviewed_docx_path, reviewed_docx_sha256,
                frozen_pdf_path, frozen_pdf_sha256, page_count,
                created_by, frozen_by
            ) VALUES (?, ?, ?, ?, 1, ?, ?, 1, 'SYN-001', '001',
                      '合成学生', 'SYN-CLASS', 'frozen', 2,
                      'synthetic-layout', 'synthetic-budget', '{}', '{}',
                      ?, 'review.docx', ?, 'reviewed.docx', ?,
                      'frozen.pdf', ?, 1, 'synthetic-teacher',
                      'synthetic-teacher')
            """,
            (
                PAPER_ID,
                "c" * 32,
                "d" * 64,
                DRAFT_ID,
                "a" * 64,
                PAPER_BATCH_ID,
                "e" * 64,
                "f" * 64,
                "f" * 64,
                "f" * 64,
            ),
        )
        for order in (1, 2):
            version_id = f"{order:x}" * 64
            criterion_hash = f"{order + 2:x}" * 64
            task_code = f"P4-SYN-Q{order:02d}"
            points = [
                {
                    "point_id": f"q{order}-p1",
                    "target": f"第{order}题关键过程",
                    "observable_evidence": "答卷中有可核对步骤",
                },
                {
                    "point_id": f"q{order}-p2",
                    "target": f"第{order}题结论",
                    "observable_evidence": "答卷中有明确结论",
                },
            ]
            criterion = {
                "version_id": version_id,
                "criteria_hash": criterion_hash,
                "criteria": {
                    "points": points,
                    "auxiliary_rules": ["等价方法可以达成"],
                },
            }
            question = {
                "tagging_context": {
                    "question_number": str(order),
                    "question_type": "解答题",
                    "question_text": f"合成题目 {order}",
                    "answer_text": f"合成答案 {order}",
                },
                "images": [
                    {
                        "asset_path": "must-not-leak.png",
                        "sha256": "0" * 64,
                    }
                ],
            }
            recommendation = {
                "matched_key": "sk_alg_linear_equation",
                "matched_name": "一元一次方程",
                "difficulty": 4 + order,
                "estimated_minutes": 5 + order,
                "source_paper": "合成来源卷",
            }
            connection.execute(
                """
                INSERT INTO personalized_paper_items (
                    paper_instance_id, task_item_code, item_order,
                    bank_question_id, question_content_hash,
                    question_snapshot_json, criterion_version_id,
                    criterion_hash, criterion_snapshot_json,
                    recommendation_snapshot_json
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    PAPER_ID,
                    task_code,
                    order,
                    f"{order + 4:x}" * 64,
                    json.dumps(question, ensure_ascii=False),
                    version_id,
                    criterion_hash,
                    json.dumps(criterion, ensure_ascii=False),
                    json.dumps(recommendation, ensure_ascii=False),
                ),
            )
        connection.execute(
            """
            INSERT INTO training_scan_batches (
                batch_id, operation_token, operation_fingerprint,
                paper_batch_id, status, revision, created_by
            ) VALUES (?, ?, ?, ?, 'ready', 2, 'synthetic-teacher')
            """,
            (SCAN_BATCH_ID, "1" * 32, "2" * 64, PAPER_BATCH_ID),
        )
        connection.execute(
            """
            INSERT INTO training_submissions (
                submission_id, batch_id, paper_instance_id, student_id,
                status, revision, expected_total_pages
            ) VALUES (?, ?, ?, 'SYN-001', 'ready', ?, 1)
            """,
            (SUBMISSION_ID, SCAN_BATCH_ID, PAPER_ID, REVISION),
        )
        connection.execute(
            """
            INSERT INTO training_submission_uploads (
                upload_id, batch_id, operation_token,
                operation_fingerprint, filename, media_type,
                content_sha256, byte_size, page_count
            ) VALUES (?, ?, ?, ?, 'synthetic.png', 'image/png',
                      ?, ?, 1)
            """,
            (
                UPLOAD_ID,
                SCAN_BATCH_ID,
                "3" * 32,
                "4" * 64,
                page_sha,
                len(_PNG),
            ),
        )
        connection.execute(
            """
            INSERT INTO training_submission_pages (
                scan_page_id, batch_id, upload_id, upload_page_number,
                submission_id, paper_instance_id, claimed_page_number,
                claimed_total_pages, page_identity, image_sha256,
                image_fingerprint, image_path, width_pixels, height_pixels,
                blur_score, brightness_score, contrast_score,
                rotation_degrees, issue_code, state, assignment_revision
            ) VALUES (?, ?, ?, 1, ?, ?, 1, 1, 'synthetic-identity',
                      ?, '1234567890abcdef', ?, 1, 1, 100.0, 255.0,
                      100.0, 0, NULL, 'assigned', ?)
            """,
            (
                SCAN_PAGE_ID,
                SCAN_BATCH_ID,
                UPLOAD_ID,
                SUBMISSION_ID,
                PAPER_ID,
                page_sha,
                relative_page.as_posix(),
                REVISION,
            ),
        )
    return db_path, data_root


def test_structural_errors_quarantine_only_the_affected_question(
    assessment_workspace: tuple[Path, Path],
) -> None:
    db_path, data_root = assessment_workspace
    payload = {
        "results": [
            {
                "task_item_code": "P4-SYN-Q01",
                "point_id": "q1-p1",
                "state": "met",
                "evidence": "第一次返回",
            },
            {
                "task_item_code": "P4-SYN-Q01",
                "point_id": "q1-p1",
                "state": "met",
                "evidence": "重复返回",
            },
            {
                "task_item_code": "P4-SYN-Q01",
                "point_id": "q1-unknown",
                "state": "not_met",
                "evidence": "未知点",
            },
            {
                "task_item_code": "P4-SYN-Q02",
                "point_id": "q2-p1",
                "state": "met",
                "evidence": "过程达成",
            },
            {
                "task_item_code": "P4-SYN-Q02",
                "point_id": "q2-p2",
                "state": "uncertain",
                "evidence": "结论书写不完整",
            },
        ]
    }
    gateway = FakeTrainingAssessmentGateway(payload)
    module = TrainingAssessmentModule(
        db_path=db_path,
        data_root=data_root,
        gateway=gateway,
    )
    outcome = module.assess(SUBMISSION_ID, REVISION)
    pending = module.pending_summary()["items"][0]
    assert pending["review_submission_count"] == 1
    assert pending["publish_submission_count"] == 0
    assert gateway.calls == 1

    first, second = outcome.questions
    assert outcome.status == "manual_review"
    assert first["status"] == "manual_review"
    assert set(first["issue_codes"]) == {
        "duplicate_point",
        "missing_point",
        "unknown_point",
    }
    assert first["points"] == ()
    assert second["status"] == "candidate"
    assert second["met_count"] == 1
    assert second["uncertain_count"] == 1
    assert second["not_met_count"] == 0
    assert len(second["points"]) == 2

    with connect(db_path) as connection:
        stored = connection.execute(
            """
            SELECT task_item_code, COUNT(*) AS total
            FROM training_point_results
            GROUP BY task_item_code
            """
        ).fetchall()
    assert [(row["task_item_code"], row["total"]) for row in stored] == [
        ("P4-SYN-Q02", 2)
    ]


def test_restart_recovery_never_repeats_an_unknown_request(
    assessment_workspace: tuple[Path, Path],
) -> None:
    db_path, data_root = assessment_workspace
    started = threading.Event()
    release = threading.Event()

    class BlockingGateway:
        model_name = "blocking-fake"

        def __init__(self) -> None:
            self.calls = 0

        def assess(self, request, *, operation_id, request_id):
            del operation_id, request_id
            self.calls += 1
            started.set()
            assert release.wait(timeout=5)
            return FakeTrainingAssessmentGateway(
                {
                    "results": [
                        {
                            "task_item_code": item.task_item_code,
                            "point_id": point["point_id"],
                            "state": "met",
                            "evidence": "迟到的响应",
                        }
                        for item in request.items
                        for point in item.points
                    ]
                }
            ).assess(
                request,
                operation_id="late",
                request_id="late",
            )

    gateway = BlockingGateway()
    module = TrainingAssessmentModule(
        db_path=db_path,
        data_root=data_root,
        gateway=gateway,
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(module.assess, SUBMISSION_ID, REVISION)
        assert started.wait(timeout=5)
        command = AssessmentActionCommand(
            operation_token="f" * 32,
            expected_review_revision=1,
            actor_ref="synthetic-teacher",
            reason="模拟应用在请求发出后退出",
        )
        recovered = module.recover(SUBMISSION_ID, REVISION, command)
        repeated = module.recover(SUBMISSION_ID, REVISION, command)
        release.set()
        late = future.result(timeout=5)

    assert recovered == repeated == late
    assert recovered.status == "failed"
    assert recovered.error_code == "interrupted_after_request"
    assert recovered.request_count == gateway.calls == 1
    assert "不会自动再次" in recovered.action_message
    assert all(
        point["state"] is None
        for question in recovered.questions
        for point in question["review_points"]
    )


def test_published_training_changes_current_mastery_and_next_draft_only(
    assessment_workspace: tuple[Path, Path],
) -> None:
    from tests.training.test_personalized_recommendation import (
        _seed_recommendation_sources,
    )

    db_path, data_root = assessment_workspace
    _seed_recommendation_sources(db_path, data_root)
    diagnosis = {
        "students": [
            {
                "student_id": "SYN-001",
                "student_code": "001",
                "student_name": "合成学生",
                "class_id": "SYN-CLASS",
                "weak_points": [
                    {
                        "knowledge_key": "sk_alg_linear_equation",
                        "knowledge_point": "一元一次方程",
                        "mastery": 0.2,
                        "evidence_count": 1,
                        "source_question_refs": [
                            {
                                "session_id": 1,
                                "question_id": "SYN-EX-Q1",
                                "score_awarded": 2,
                                "full_score": 10,
                            }
                        ],
                        "actionable_reasons": ["合成考试证据偏弱"],
                    }
                ],
            }
        ],
        "exam_scope": {"mode": "current", "session_ids": [1]},
        "_mastery_session_times": {"1": "2026-07-01T08:00:00+08:00"},
    }
    config = PersonalizedRecommendationConfig(
        question_count=8,
        expected_minutes=120,
    )
    with connect(db_path) as connection:
        connection.execute(
            """
            UPDATE personalized_recommendation_drafts
            SET request_json = ?
            WHERE draft_id = ?
            """,
            (
                json.dumps(
                    {
                        "diagnosis": diagnosis,
                        "config": config.to_dict(),
                    },
                    ensure_ascii=False,
                ),
                DRAFT_ID,
            ),
        )
        for task_code, question_id in (
            ("P4-SYN-Q01", 3),
            ("P4-SYN-Q02", 6),
        ):
            connection.execute(
                """
                UPDATE personalized_paper_items
                SET bank_question_id = ?,
                    recommendation_snapshot_json = ?
                WHERE task_item_code = ?
                """,
                (
                    question_id,
                    json.dumps(
                        {
                            "matched_key": "sk_alg_linear_equation",
                            "matched_name": "一元一次方程",
                            "difficulty": 5,
                            "estimated_minutes": 6,
                            "source_paper": "合成来源卷",
                        },
                        ensure_ascii=False,
                    ),
                    task_code,
                ),
            )
    module = TrainingAssessmentModule(
        db_path=db_path,
        data_root=data_root,
        gateway=FakeTrainingAssessmentGateway(
            {
                "results": [
                    {
                        "task_item_code": f"P4-SYN-Q{order:02d}",
                        "point_id": f"q{order}-p{point}",
                        "state": "met",
                        "evidence": "合成闭环依据",
                    }
                    for order in (1, 2)
                    for point in (1, 2)
                ]
            }
        ),
        clock=lambda: datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
    )
    module.assess(SUBMISSION_ID, REVISION)
    assert module.pending_summary()["items"][0]["publish_submission_count"] == 1
    feedback = module.sync_evidence(
        SUBMISSION_ID,
        REVISION,
        EvidenceSyncCommand(
            operation_token="2" * 32,
            expected_review_revision=1,
            action="publish",
            actor_ref="synthetic-teacher",
            reason="形成合成闭环",
        ),
    )

    change = next(
        item
        for item in feedback["mastery_changes"]
        if item["stable_key"] == "sk_alg_linear_equation"
    )
    assert module.pending_summary() == {"items": []}
    assert change["mastery_after"]["value"] > change["mastery_before"]["value"]
    assert change["mastery_before"]["observation_count"] == 1
    assert change["mastery_after"]["observation_count"] == 5
    assert change["mastery_after"]["full_correct_count"] == 4
    assert change["mastery_after"]["training_evidence_count"] == 2
    assert change["mastery_after"]["interval_low"] <= change["mastery_after"]["value"] <= change["mastery_after"]["interval_high"]
    graph = CurrentKnowledgeGraphQueryService(
        db_path,
        clock=lambda: datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
    ).query(
        diagnosis,
        CurrentGraphQuery(
            knowledge_keys=("sk_alg_linear_equation",),
            prerequisite_depth=0,
        ),
    )
    graph_node = next(
        node
        for node in graph["nodes"]
        if node["stable_key"] == "sk_alg_linear_equation"
    )
    assert graph_node["mastery"]["value"] == change["mastery_after"]["value"]
    assert (
        graph_node["mastery"]["evidence_count"]
        == (change["mastery_after"]["evidence_count"])
    )
    assert feedback["next_round"]["status"] == "draft", feedback["next_round"]
    assert feedback["next_round"]["draft_id"]
    assert feedback["next_round"]["student"]["targets"][0]["mode"] == "current"
    next_target = next(
        item
        for item in feedback["next_round"]["student"]["targets"]
        if item["stable_key"] == "sk_alg_linear_equation"
    )
    assert next_target["value"] == change["mastery_after"]["value"]
    assert next_target["evidence_count"] == change["mastery_after"]["evidence_count"]
    assert next_target["value"] != 0.2
    next_ids = {
        item["question_id"] for item in feedback["next_round"]["student"]["items"]
    }
    assert {3, 6}.isdisjoint(next_ids)
    assert any(item["type"] == "mastery" for item in feedback["next_round"]["changes"])
    with connect(db_path) as connection:
        paper_count = int(
            connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM personalized_paper_instances
                """
            ).fetchone()["total"]
        )
        next_status = connection.execute(
            """
            SELECT status
            FROM personalized_recommendation_drafts
            WHERE draft_id = ?
            """,
            (feedback["next_round"]["draft_id"],),
        ).fetchone()["status"]
        original_request = json.loads(
            connection.execute(
                "SELECT request_json FROM personalized_recommendation_drafts WHERE draft_id = ?",
                (DRAFT_ID,),
            ).fetchone()["request_json"]
        )
    assert paper_count == 1
    assert next_status == "draft"
    assert (
        original_request["diagnosis"]["students"][0]["weak_points"][0]["mastery"] == 0.2
    )


def test_pending_summary_uses_current_revisions_and_excludes_cancelled_scans(assessment_workspace):
    db_path, data_root = assessment_workspace
    gateway = FakeTrainingAssessmentGateway({"results": [
        dict(task_item_code=f"P4-SYN-Q{order:02d}", point_id=f"q{order}-p{point}",
             state="met", evidence="TEST-pending")
        for order in (1, 2) for point in (1, 2)
    ]})
    module = TrainingAssessmentModule(db_path=db_path, data_root=data_root, gateway=gateway)
    assert module.pending_summary() == {"items": []}  # Unassessed submissions are not reviews.
    module.assess(SUBMISSION_ID, REVISION)
    with connect(db_path) as conn:
        conn.execute("UPDATE training_submission_pages SET issue_code='identity_conflict'")
    before = db_path.read_bytes()
    summary = module.pending_summary()
    item = summary["items"][0]
    assert item["draft_id"] == DRAFT_ID
    assert item["scan_page_count"] == 1
    assert item["review_submission_count"] == 0
    assert item["publish_submission_count"] == 1
    assert db_path.read_bytes() == before
    assert gateway.calls == 1
    with connect(db_path) as conn:
        conn.execute("UPDATE training_submission_pages SET state='dismissed'")
        conn.execute("UPDATE training_submissions SET revision=revision+1")
    assert module.pending_summary() == {"items": []}
    with connect(db_path) as conn:
        conn.execute("UPDATE training_submissions SET revision=?", (REVISION,))
        conn.execute("INSERT INTO training_feedback_snapshots "
                     "(feedback_id,submission_id,submission_revision,source_review_revision,evidence_version,status,feedback_json) "
                     "VALUES (?,?,?,?,?,?,?)", ("a"*64, SUBMISSION_ID, REVISION, 1, "b"*64, "complete",
                     json.dumps(dict(status="complete", source_review_revision=1))))
    assert module.pending_summary() == {"items": []}
    with connect(db_path) as conn:
        conn.execute("UPDATE training_assessment_runs SET review_revision=2")
    assert module.pending_summary()["items"][0]["publish_submission_count"] == 1
    with connect(db_path) as conn:
        conn.execute("UPDATE training_submissions SET status='cancelled'")
        conn.execute("UPDATE training_submission_pages SET state='assigned'")
    assert module.pending_summary() == {"items": []}


def test_next_round_token_matches_legacy_frozen_request(
    assessment_workspace: tuple[Path, Path],
) -> None:
    from backend.training_assessment.contracts import stable_hash
    from backend.training_assessment.evidence import TrainingEvidencePublisher
    from tests.training.test_personalized_recommendation import (
        _seed_recommendation_sources,
    )

    db_path, data_root = assessment_workspace
    _seed_recommendation_sources(db_path, data_root)
    activities = [
        {
            "student_id": "SYN-001",
            "session_id": "1",
            "occurred_at": "2026-07-01T08:00:00+08:00",
        }
    ]
    student = {
        "student_id": "SYN-001",
        "student_code": "001",
        "student_name": "合成学生",
        "class_id": "SYN-CLASS",
        "weak_points": [
            {
                "knowledge_key": "sk_alg_linear_equation",
                "knowledge_point": "一元一次方程",
                "mastery": 0.2,
                "evidence_count": 1,
                "source_question_refs": [
                    {
                        "session_id": 1,
                        "question_id": "SYN-EX-Q1",
                        "score_awarded": 2,
                        "full_score": 10,
                    }
                ],
                "actionable_reasons": ["合成考试证据偏弱"],
            }
        ],
    }
    diagnosis = {
        "students": [student],
        "exam_scope": {"mode": "current", "session_ids": [1]},
        "_mastery_session_times": {"1": "2026-07-01T08:00:00+08:00"},
    }
    publisher = TrainingEvidencePublisher(
        db_path=db_path,
        data_root=data_root,
        outcome_loader=lambda *args: None,
        clock=lambda: datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
    )
    context = {
        "submission_id": "SYN-SUB",
        "submission_revision": 1,
        "student_id": "SYN-001",
        "items": [],
        "recommendation_config": PersonalizedRecommendationConfig(
            question_count=8,
            expected_minutes=120,
        ).to_dict(),
    }
    # Requests frozen before the snapshot refactor embedded the activities
    # inside the stored diagnosis. Rebuilding the same frozen context from the
    # request-level sibling key must produce the identical idempotency token.
    expected_token = stable_hash(
        {
            "kind": "training-next-round-draft-v2-current-mastery",
            "submission_id": context["submission_id"],
            "submission_revision": context["submission_revision"],
            "student_id": "SYN-001",
            "evidence_version": "ev-1",
            "diagnosis": {
                **diagnosis,
                "students": [student],
                "_graded_activities": activities,
            },
        }
    )[:32]
    legacy = publisher._next_round(
        {
            **context,
            "diagnosis": {**diagnosis, "_graded_activities": activities},
        },
        evidence_version="ev-1",
        actor_ref="synthetic",
        mastery_changes=[],
        publication_pending=False,
    )
    assert legacy["status"] == "draft", legacy
    sibling = publisher._next_round(
        {
            **context,
            "diagnosis": diagnosis,
            "graded_activities": activities,
        },
        evidence_version="ev-1",
        actor_ref="synthetic",
        mastery_changes=[],
        publication_pending=False,
    )
    assert sibling["draft_id"] == legacy["draft_id"]
    with connect(db_path) as connection:
        stored = connection.execute(
            """
            SELECT request_token, request_json
            FROM personalized_recommendation_drafts
            WHERE draft_id = ?
            """,
            (legacy["draft_id"],),
        ).fetchone()
    assert str(stored["request_token"]) == expected_token
    stored_request = json.loads(str(stored["request_json"]))
    assert stored_request["graded_activities"] == activities
    assert "_graded_activities" not in stored_request["diagnosis"]
    inherited_config = PersonalizedRecommendationConfig(question_count=8, expected_minutes=120,
        max_questions_per_skill=3, max_written_questions=5, difficulty_max=10, recent_activity_count=20).to_dict()
    inherited_recent = {"SYN-001": [3]}
    inherited = publisher._next_round({**context, "diagnosis": diagnosis, "graded_activities": activities,
        "recommendation_config": inherited_config, "recent_question_ids": inherited_recent},
        evidence_version="ev-2", actor_ref="synthetic", mastery_changes=[], publication_pending=False)
    assert inherited["status"] == "draft", inherited
    with connect(db_path) as connection:
        saved = json.loads(connection.execute(
            "SELECT request_json FROM personalized_recommendation_drafts WHERE draft_id=?",
            (inherited["draft_id"],)).fetchone()[0])
    for field in ("purpose", "max_questions_per_skill", "max_written_questions", "difficulty_max", "recent_activity_count"):
        assert saved["config"][field] == inherited_config[field]
    # A new draft calculates its own window. This unmarked synthetic paper
    # has no completed activity, so the old frozen set must not be copied.
    assert saved["recent_question_ids"] == {"SYN-001": []}
    assert saved["graded_activities"] == activities
