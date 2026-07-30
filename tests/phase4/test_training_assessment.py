from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from backend.training_assessment import (
    AssessmentContextExceeded,
    AssessmentInputInvalid,
    AssessmentRevisionConflict,
    FakeTrainingAssessmentGateway,
    OpenAITrainingAssessmentGateway,
    TrainingAssessmentModule,
)
from question_bank.database.schema import connect, initialize_database


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
    page_sha = hashlib.sha256(_PNG).hexdigest()
    relative_page = (
        Path(SCAN_BATCH_ID[:20])
        / "pages"
        / f"{SCAN_PAGE_ID[:32]}.png"
    )
    page_path = (
        data_root
        / "question_bank"
        / "training_submissions"
        / relative_page
    )
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
            connection.execute(
                """
                INSERT INTO personalized_paper_items (
                    paper_instance_id, task_item_code, item_order,
                    bank_question_id, question_content_hash,
                    question_snapshot_json, criterion_version_id,
                    criterion_hash, criterion_snapshot_json,
                    recommendation_snapshot_json
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, '{}')
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


def test_valid_assessment_is_one_request_idempotent_and_never_scores(
    assessment_workspace: tuple[Path, Path],
) -> None:
    db_path, data_root = assessment_workspace
    states = ("met", "not_met", "uncertain", "unreadable")

    def respond(request):
        results = []
        index = 0
        for item in request.items:
            for point in item.points:
                results.append(
                    {
                        "task_item_code": item.task_item_code,
                        "point_id": point["point_id"],
                        "state": states[index],
                        "evidence": f"合成可核对依据 {index + 1}",
                    }
                )
                index += 1
        return {"results": results}

    gateway = FakeTrainingAssessmentGateway(respond)
    module = TrainingAssessmentModule(
        db_path=db_path,
        data_root=data_root,
        gateway=gateway,
    )
    first = module.assess(SUBMISSION_ID, REVISION)
    page_path = (
        data_root
        / "question_bank"
        / "training_submissions"
        / SCAN_BATCH_ID[:20]
        / "pages"
        / f"{SCAN_PAGE_ID[:32]}.png"
    )
    page_path.write_bytes(_PNG + b"changed-after-completion")
    repeated = module.assess(SUBMISSION_ID, REVISION)

    assert first == repeated
    assert first.status == "succeeded"
    assert first.request_count == gateway.calls == 1
    assert first.expected_question_count == 2
    assert first.expected_point_count == 4
    assert first.questions[0]["met_count"] == 1
    assert first.questions[0]["not_met_count"] == 1
    assert first.questions[1]["uncertain_count"] == 1
    assert first.questions[1]["unreadable_count"] == 1
    assert all(item["status"] == "candidate" for item in first.questions)
    serialized = json.dumps(first.to_dict(), ensure_ascii=False).casefold()
    assert "score" not in serialized
    assert "rank" not in serialized
    assert gateway.requests[0].pages[0].content == _PNG
    assert all(
        "asset_path" not in item.question
        for item in gateway.requests[0].items
    )

    with connect(db_path) as connection:
        point_count = connection.execute(
            "SELECT COUNT(*) AS total FROM training_point_results"
        ).fetchone()["total"]
    assert int(point_count) == 4


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
    outcome = TrainingAssessmentModule(
        db_path=db_path,
        data_root=data_root,
        gateway=gateway,
    ).assess(SUBMISSION_ID, REVISION)

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


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (TimeoutError(), "failed", "timeout"),
        (AssessmentContextExceeded(), "failed", "context_limit"),
        (ValueError("bad json"), "failed", "invalid_response"),
        (RuntimeError("model unavailable"), "failed", "model_failure"),
        (asyncio.CancelledError(), "cancelled", "cancelled"),
    ],
)
def test_model_failures_do_not_retry_or_leave_candidate_rows(
    assessment_workspace: tuple[Path, Path],
    error: BaseException,
    expected_status: str,
    expected_code: str,
) -> None:
    db_path, data_root = assessment_workspace

    class RaisingGateway:
        model_name = "raising-fake"

        def __init__(self) -> None:
            self.calls = 0

        def assess(self, request, *, operation_id, request_id):
            del request, operation_id, request_id
            self.calls += 1
            raise error

    gateway = RaisingGateway()
    module = TrainingAssessmentModule(
        db_path=db_path,
        data_root=data_root,
        gateway=gateway,
    )
    first = module.assess(SUBMISSION_ID, REVISION)
    repeated = module.assess(SUBMISSION_ID, REVISION)

    assert repeated == first
    assert first.status == expected_status
    assert first.error_code == expected_code
    assert first.request_count == gateway.calls == 1
    assert first.questions == ()
    with connect(db_path) as connection:
        question_count = connection.execute(
            "SELECT COUNT(*) AS total FROM training_question_results"
        ).fetchone()["total"]
        point_count = connection.execute(
            "SELECT COUNT(*) AS total FROM training_point_results"
        ).fetchone()["total"]
    assert (int(question_count), int(point_count)) == (0, 0)


def test_revision_and_page_hash_changes_fail_before_request(
    assessment_workspace: tuple[Path, Path],
) -> None:
    db_path, data_root = assessment_workspace
    gateway = FakeTrainingAssessmentGateway({"results": []})
    module = TrainingAssessmentModule(
        db_path=db_path,
        data_root=data_root,
        gateway=gateway,
    )
    with pytest.raises(AssessmentRevisionConflict):
        module.assess(SUBMISSION_ID, REVISION - 1)
    page_path = (
        data_root
        / "question_bank"
        / "training_submissions"
        / SCAN_BATCH_ID[:20]
        / "pages"
        / f"{SCAN_PAGE_ID[:32]}.png"
    )
    page_path.write_bytes(_PNG + b"changed")
    with pytest.raises(AssessmentInputInvalid, match="hash mismatch"):
        module.assess(SUBMISSION_ID, REVISION)
    assert gateway.calls == 0


def test_simultaneous_clicks_share_one_physical_request(
    assessment_workspace: tuple[Path, Path],
) -> None:
    db_path, data_root = assessment_workspace

    def respond(request):
        time.sleep(0.05)
        return {
            "results": [
                {
                    "task_item_code": item.task_item_code,
                    "point_id": point["point_id"],
                    "state": "met",
                    "evidence": "并发合成依据",
                }
                for item in request.items
                for point in item.points
            ]
        }

    gateway = FakeTrainingAssessmentGateway(respond)
    modules = [
        TrainingAssessmentModule(
            db_path=db_path,
            data_root=data_root,
            gateway=gateway,
        )
        for _ in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda module: module.assess(SUBMISSION_ID, REVISION),
                modules,
            )
        )
    assert outcomes[0] == outcomes[1]
    assert outcomes[0].status == "succeeded"
    assert gateway.calls == 1


def test_openai_adapter_uses_strict_one_request_and_injection_guard(
    assessment_workspace: tuple[Path, Path],
) -> None:
    db_path, data_root = assessment_workspace
    capture: dict[str, Any] = {}

    class Protocol:
        def responses(self, **kwargs):
            capture.update(kwargs)

            class Response:
                output_text = json.dumps(
                    {
                        "results": [
                            {
                                "task_item_code": f"P4-SYN-Q{order:02d}",
                                "point_id": f"q{order}-p{point}",
                                "state": "met",
                                "evidence": "合成依据",
                            }
                            for order in (1, 2)
                            for point in (1, 2)
                        ]
                    },
                    ensure_ascii=False,
                )
                usage = {
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "total_tokens": 30,
                }

            return Response()

    adapter = OpenAITrainingAssessmentGateway(
        protocol_adapter=Protocol(),
        model_name="synthetic-model",
    )
    outcome = TrainingAssessmentModule(
        db_path=db_path,
        data_root=data_root,
        gateway=adapter,
    ).assess(SUBMISSION_ID, REVISION)
    assert outcome.status == "succeeded"
    assert capture["allow_retry"] is False
    assert capture["kwargs"]["text"]["format"]["strict"] is True
    prompt = json.dumps(
        capture["kwargs"]["input"],
        ensure_ascii=False,
    )
    assert "untrusted" in prompt
    assert "Never produce a score" in prompt
    assert "must-not-leak.png" not in prompt
