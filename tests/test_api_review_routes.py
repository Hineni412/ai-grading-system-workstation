from __future__ import annotations
import json
import sqlite3
from pathlib import Path
import pytest


from fastapi.testclient import TestClient
from backend.repositories.grading_database import open_grading_repositories


from unittest.mock import MagicMock, patch
from backend.domain_models import GradingResult, QuestionGradingDetail
from backend.repositories.db_manager import DBManager


from typing import Any


from dataclasses import dataclass

from PIL import Image


from dataclasses import replace

from backend.review.service import ReviewApplicationService, ReviewConfirmationInput, ReviewRevisionConflictError
from backend.review.manual_review_service import ManualReviewService


analysis_report_COMPLETE_RAW_JSON = json.dumps(
    {"grading_completeness": {"status": "complete"}},
    ensure_ascii=False,
)


analysis_report_PERSONAL_NARRATIVE = {
    "overall_comment": "整体表现稳定，丢分集中在证明题。",
    "question_analyses": [
        {
            "question_id": "Q2",
            "analysis": "证明过程缺关键步骤，建议写出中间结论。",
            "root_cause_type": "method",
        }
    ],
    "strengths": ["选择题全对，基础扎实"],
    "problems": [{"title": "证明书写不完整", "detail": "第 2 题丢了过程分"}],
    "suggestions": [
        {"title": "重做证明题", "timeframe": "本周", "detail": "练 3 道同类题"}
    ],
}


def analysis_report__seed_analysis_session(db, root: Path) -> int:
    """三名学生：张三/李四正常参考，王五缺考。rubric 两题共 100 分。"""
    config_dir = root / "config" / "uploaded"
    config_dir.mkdir(parents=True, exist_ok=True)
    rubric = {
        "exam_title": "单元测试",
        "subject": "数学",
        "total_score": 100,
        "questions": [
            {
                "question_id": "Q1",
                "question_type": "choice",
                "max_score": 60,
                "stem_summary": "识别轴对称图形",
            },
            {
                "question_id": "Q2",
                "question_type": "proof",
                "max_score": 40,
                "stem_summary": "证明线段数量关系",
            },
        ],
    }
    answer_key = {
        "questions": [
            {"question_id": "Q1", "canonical_answer": "B"},
            {"question_id": "Q2", "canonical_answer": "AB=BD+DH"},
        ]
    }
    rubric_path = config_dir / "rubric.json"
    answer_path = config_dir / "answer_key.json"
    rubric_path.write_text(json.dumps(rubric, ensure_ascii=False), encoding="utf-8")
    answer_path.write_text(json.dumps(answer_key, ensure_ascii=False), encoding="utf-8")
    session_id = db.sessions.create_grading_session(
        "单元测试", str(rubric_path), str(answer_path)
    )

    with sqlite3.connect(db.db_path) as conn:
        students = {}
        for code, name in (("001", "张三"), ("002", "李四"), ("003", "王五")):
            students[name] = int(
                conn.execute(
                    "INSERT INTO students (student_code, name, class_name) VALUES (?, ?, ?)",
                    (code, name, "1 班"),
                ).lastrowid
            )
        for name, total, awarded, review in (
            ("张三", 100, 90, 0),
            ("李四", 100, 50, 1),
        ):
            student_id = students[name]
            paper_id = int(
                conn.execute(
                    """
                    INSERT INTO exam_papers (
                        session_id, front_image, back_image, student_id,
                        match_status, processing_status
                    ) VALUES (?, '', '', ?, 'matched', 'graded')
                    """,
                    (session_id, student_id),
                ).lastrowid
            )
            result_id = int(
                conn.execute(
                    """
                    INSERT INTO session_results (
                        session_id, student_id, paper_id, total_score, student_score,
                        needs_human_review, raw_json
                    ) VALUES (?, ?, ?, 100, ?, ?, ?)
                    """,
                    (
                        session_id,
                        student_id,
                        paper_id,
                        awarded,
                        review,
                        analysis_report_COMPLETE_RAW_JSON,
                    ),
                ).lastrowid
            )
            conn.execute(
                """
                INSERT INTO session_details (
                    result_id, question_id, score_awarded, deduction_reason,
                    knowledge_ids, error_category, error_summary
                ) VALUES (?, 'Q1', ?, ?, '["UNKNOWN"]', ?, ?)
                """,
                (
                    result_id,
                    60 if awarded >= 60 else 30,
                    None if awarded >= 60 else "objective_answer=C",
                    None if awarded >= 60 else "答错",
                    None,
                ),
            )
            if review:
                conn.execute(
                    "UPDATE session_details SET confidence_score=70 WHERE result_id=? AND question_id='Q1'",
                    (result_id,),
                )
            conn.execute(
                """
                INSERT INTO session_details (
                    result_id, question_id, score_awarded, deduction_reason,
                    knowledge_ids, error_category, error_summary
                ) VALUES (?, 'Q2', ?, ?, '["UNKNOWN"]', ?, ?)
                """,
                (
                    result_id,
                    30 if awarded >= 60 else 20,
                    "缺关键步骤",
                    "过程不完整",
                    "缺 BE⊥AC 步骤",
                ),
            )
        for name, status in (
            ("张三", "present"),
            ("李四", "present"),
            ("王五", "absent"),
        ):
            conn.execute(
                """
                INSERT INTO session_attendance (
                    session_id, student_id, attendance_status, source_reason
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    session_id,
                    students[name],
                    status,
                    "测试" if status != "present" else None,
                ),
            )
    return session_id


def analysis_report__patch_configured(monkeypatch: pytest.MonkeyPatch, configured: bool) -> None:
    import backend.reporting.analysis_report_exporter as exporter
    import backend.model_profiles.content_generation as content_generation
    from backend.llm.llm_client import LLMSettings

    settings = (
        LLMSettings(
            api_key="k",
            base_url="https://example.com/v1",
            ocr_model="m",
            grading_model="m",
            config_model="content-model",
        )
        if configured
        else None
    )
    monkeypatch.setattr(
        exporter, "resolve_content_generation_settings", lambda: settings
    )
    monkeypatch.setattr(
        exporter,
        "content_generation_public_info",
        lambda: (
            ("测试服务 @ example.com", "content-model") if configured else (None, None)
        ),
    )
    monkeypatch.setattr(
        content_generation, "resolve_content_generation_settings", lambda: settings
    )
    monkeypatch.setattr(
        content_generation,
        "content_generation_public_info",
        lambda: (
            ("测试服务 @ example.com", "content-model") if configured else (None, None)
        ),
    )


def analysis_report__add_personal_report_scans(db, session_id: int, root: Path) -> list[dict]:
    from PIL import Image

    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        papers = [
            dict(row)
            for row in conn.execute(
                "SELECT id, student_id FROM exam_papers WHERE session_id=? ORDER BY id",
                (session_id,),
            )
        ]
        for index, paper in enumerate(papers):
            path = root / f"synthetic-paper-{paper['id']}.png"
            Image.new(
                "RGB", (400, 300), (220, 30, 30) if index == 0 else (30, 30, 220)
            ).save(path)
            conn.execute(
                "UPDATE exam_papers SET front_image=? WHERE id=?",
                (str(path), paper["id"]),
            )
        template_id = conn.execute(
            "INSERT INTO session_templates (session_id, front_template_path, back_template_path) VALUES (?, ?, '')",
            (session_id, str(root / f"synthetic-paper-{papers[0]['id']}.png")),
        ).lastrowid
        for index, qid in enumerate(("Q1", "Q2")):
            conn.execute(
                """INSERT INTO answer_regions (
                    region_uuid, session_id, template_id, page, region_order,
                    x, y, w, h, mapped_question_id
                ) VALUES (?, ?, ?, 'front', ?, 10, ?, 300, 100, ?)""",
                (
                    f"report-{qid}",
                    session_id,
                    template_id,
                    index,
                    10 + index * 110,
                    qid,
                ),
            )
    return papers


def _seed_review_db(tmp_path: Path, *, result_count: int = 1):
    from backend.api.app import create_app
    from backend.api.dependencies import get_grading_db, get_job_manager
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    

    db_dir = tmp_path / "databases"
    db_dir.mkdir(parents=True, exist_ok=True)
    db = open_grading_repositories(db_dir / "grading.db")
    db.initialize()
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "question_type": "choice",
                        "max_score": 10,
                    },
                    {
                        "question_id": "Q2",
                        "question_type": "proof",
                        "max_score": 5,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    session_id = db.sessions.create_grading_session("Exam A", str(rubric_path), "answer.json")
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        result_id = 0
        q1_detail_id = 0
        for index in range(result_count):
            student_id = int(
                conn.execute(
                    "INSERT INTO students (student_code, name, class_name) VALUES (?, ?, 'Class 1')",
                    (
                        f"S{index + 1:03d}",
                        "Alice" if index == 0 else f"Student {index + 1:03d}",
                    ),
                ).lastrowid
            )
            paper_id = int(
                conn.execute(
                    """
                    INSERT INTO exam_papers (
                        session_id, front_image, back_image, ocr_name, student_id,
                        match_status, processing_status
                    ) VALUES (?, ?, ?, ?, ?, 'matched', 'graded')
                    """,
                    (
                        session_id,
                        f"front-{index}.jpg",
                        f"back-{index}.jpg",
                        "Alice" if index == 0 else f"Student {index + 1:03d}",
                        student_id,
                    ),
                ).lastrowid
            )
            current_result_id = int(
                conn.execute(
                    """
                    INSERT INTO session_results (
                        session_id, student_id, paper_id, total_score, student_score,
                        needs_human_review, raw_json
                    ) VALUES (?, ?, ?, 15, 10, 1, ?)
                    """,
                    (
                        session_id,
                        student_id,
                        paper_id,
                        json.dumps(
                            {
                                "detail_metadata": {
                                    "Q1": {
                                        "question_id": "Q1",
                                        "evidence_steps": [
                                            "student step",
                                            str(tmp_path / "private" / "evidence.png"),
                                        ],
                                        "missing_steps": ["final conclusion"],
                                        "candidate_scores": [
                                            {
                                                "score": 8,
                                                "confidence": 0.55,
                                                "reason": "unclear handwriting",
                                                "image_path": str(
                                                    tmp_path
                                                    / "private"
                                                    / "candidate.png"
                                                ),
                                            }
                                        ],
                                        "source": "legacy-import",
                                        "image_path": str(
                                            tmp_path / "private" / "metadata.png"
                                        ),
                                        "configApiKey": "metadata-secret",
                                    }
                                }
                            },
                            ensure_ascii=False,
                        ),
                    ),
                ).lastrowid
            )
            current_detail_id = int(
                conn.execute(
                    """
                    INSERT INTO session_details (
                        result_id, question_id, score_awarded, deduction_reason, knowledge_ids,
                        error_category, error_summary, confidence_score
                    ) VALUES (?, 'Q1', 8, '需复核：字迹不清', '["K1"]', '需复核', 'unclear', 55)
                    """,
                    (current_result_id,),
                ).lastrowid
            )
            conn.execute(
                """
                INSERT INTO session_details (
                    result_id, question_id, score_awarded, deduction_reason, knowledge_ids,
                    error_category, error_summary, confidence_score
                ) VALUES (?, 'Q2', 2, '计算错误', '["K2"]', '计算错误', 'wrong', 92)
                """,
                (current_result_id,),
            )
            if index == 0:
                result_id = current_result_id
                q1_detail_id = current_detail_id
        conn.commit()

    app = create_app()
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    return TestClient(app), db, session_id, result_id, q1_detail_id


def test_review_confirm_maps_invalid_score_to_stable_domain_error(
    tmp_path: Path,
) -> None:
    # 分数分类由应用服务测试覆盖；这里验证一种非法分的 HTTP 错误映射。
    client, _db, session_id, result_id, detail_id = _seed_review_db(tmp_path)

    response = client.post(
        f"/api/sessions/{session_id}/review/questions/Q1/confirm",
        headers={"x-request-id": "rid-review-score"},
        json={
            "items": [
                {
                    "result_id": result_id,
                    "detail_id": detail_id,
                    "score_awarded": 10.01,
                }
            ]
        },
    )

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "review_validation_error",
        "message": "Invalid review confirmation",
        "details": {"session_id": session_id, "question_id": "Q1"},
        "request_id": "rid-review-score",
    }


def _pipeline_job_recorder(client):
    """在 review_api 的 manager 上注册 class_analysis_generate 记录桩。"""
    from backend.api.dependencies import get_job_manager

    manager = client.app.dependency_overrides[get_job_manager]()
    submitted: list[dict] = []
    manager.register(
        "class_analysis_generate",
        lambda context: submitted.append(dict(context.payload))
        or {"kind": "pipeline", "status": "ready"},
    )
    return manager, submitted


def _sync_pipeline_trigger(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """把复核确认后的后台触发线程改成同步执行，便于断言；返回启动记录。"""
    from backend.api.routers import review as review_router

    launched: list[dict] = []

    def sync_launch(**kwargs):
        launched.append(dict(kwargs))
        review_router._report_pipeline_trigger_runner(**kwargs)

    monkeypatch.setattr(
        review_router, "_launch_report_pipeline_trigger", sync_launch
    )
    return launched


def test_confirm_last_pending_item_triggers_report_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """清掉最后一个待复核条目 → 自动提交「AI 整理」管线（mode=auto）。"""
    from tests.test_api_review_routes import analysis_report__patch_configured as _patch_configured
    from backend.api.dependencies import get_reports_dir

    client, db, session_id, result_id, detail_id = _seed_review_db(tmp_path)
    manager, submitted = _pipeline_job_recorder(client)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(exist_ok=True)
    client.app.dependency_overrides[get_reports_dir] = lambda: reports_dir
    _patch_configured(monkeypatch, True)
    launched = _sync_pipeline_trigger(monkeypatch)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE session_results SET raw_json=?",
            (json.dumps({"grading_completeness": {"status": "complete"}}),),
        )
    response = client.post(
        f"/api/sessions/{session_id}/review/questions/Q1/confirm",
        json={
            "items": [
                {
                    "result_id": result_id,
                    "detail_id": detail_id,
                    "score_awarded": 8,
                }
            ]
        },
    )
    assert response.status_code == 200
    assert len(launched) == 1
    jobs, total = manager.list(
        session_id=session_id, job_types=("class_analysis_generate",), limit=5
    )
    assert total == 1
    manager.wait(jobs[0].id, timeout=5)
    assert submitted and submitted[0]["kind"] == "pipeline"
    assert submitted[0]["mode"] == "auto"


def test_confirm_with_remaining_pending_item_does_not_trigger_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一题还有别的待复核条目：确认单条后该题仍待复核，不启动触发。"""
    from tests.test_api_review_routes import analysis_report__patch_configured as _patch_configured
    from backend.api.dependencies import get_reports_dir

    client, db, session_id, result_id, detail_id = _seed_review_db(
        tmp_path, result_count=2
    )
    _manager, submitted = _pipeline_job_recorder(client)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(exist_ok=True)
    client.app.dependency_overrides[get_reports_dir] = lambda: reports_dir
    _patch_configured(monkeypatch, True)
    launched = _sync_pipeline_trigger(monkeypatch)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE session_results SET raw_json=?",
            (json.dumps({"grading_completeness": {"status": "complete"}}),),
        )
    response = client.post(
        f"/api/sessions/{session_id}/review/questions/Q1/confirm",
        json={
            "items": [
                {
                    "result_id": result_id,
                    "detail_id": detail_id,
                    "score_awarded": 8,
                }
            ]
        },
    )
    assert response.status_code == 200
    assert launched == []
    assert submitted == []


def test_confirm_already_final_item_does_not_trigger_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已是最终分的条目再次确认：不触发自动整理，避免复核改分外的花费。"""
    from tests.test_api_review_routes import analysis_report__patch_configured as _patch_configured
    from backend.api.dependencies import get_reports_dir

    client, db, session_id, result_id, _detail_id = _seed_review_db(tmp_path)
    _manager, submitted = _pipeline_job_recorder(client)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(exist_ok=True)
    client.app.dependency_overrides[get_reports_dir] = lambda: reports_dir
    _patch_configured(monkeypatch, True)
    launched = _sync_pipeline_trigger(monkeypatch)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE session_results SET raw_json=?",
            (json.dumps({"grading_completeness": {"status": "complete"}}),),
        )
        q2_detail_id = conn.execute(
            "SELECT id FROM session_details WHERE result_id=? AND question_id='Q2'",
            (result_id,),
        ).fetchone()[0]
    response = client.post(
        f"/api/sessions/{session_id}/review/questions/Q2/confirm",
        json={
            "items": [
                {
                    "result_id": result_id,
                    "detail_id": q2_detail_id,
                    "score_awarded": 2,
                }
            ]
        },
    )
    assert response.status_code == 200
    assert launched == []
    assert submitted == []


def dual_track_ai_scores__detail_by_question(db: DBManager, result_id: int) -> dict[str, dict[str, object]]:
    return {str(row["question_id"]): row for row in db.results.get_result_details(result_id)}


def test_hybrid_run_grades_locked_questions_and_teacher_score_wins(
    tmp_path: Path,
) -> None:
    from backend.scan_grading.grading_service import GradingService
    from backend.scan_grading.ai_batch_grading_service import AIBatchRunResult, PaperEntry
    from backend.llm.llm_client import LLMClient
    from backend.scan_grading.scanner import ExamPaperGroup

    exams_dir = tmp_path / "exams"
    exams_dir.mkdir()
    front_image = exams_dir / "front.jpg"
    front_image.touch()
    back_image = exams_dir / "back.jpg"
    back_image.touch()
    from path_manager import get_path_manager

    controlled_root = (
        get_path_manager().data_root / "config" / "uploaded" / "dual-track"
    )
    controlled_root.mkdir(parents=True, exist_ok=True)
    rubric_path = controlled_root / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": "Q1", "max_score": 5, "parts": []},
                    {"question_id": "Q2", "max_score": 5, "parts": []},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    answer_key_path = controlled_root / "answer_key.json"
    answer_key_path.write_text("{}", encoding="utf-8")

    db = open_grading_repositories(tmp_path / "hybrid-locks.db")
    db.initialize()
    session_id = db.sessions.create_grading_session(
        "混合双轨", str(rubric_path), str(answer_key_path)
    )

    from PIL import Image

    templates_root = (
        get_path_manager().data_root / "templates" / f"session_{session_id}"
    )
    templates_root.mkdir(parents=True, exist_ok=True)
    front_template = templates_root / "front.png"
    back_template = templates_root / "back.png"
    Image.new("RGB", (2831, 1960), "white").save(front_template)
    Image.new("RGB", (2800, 1900), "white").save(back_template)
    template_id = db.templates.upsert_session_template(
        session_id, str(front_template), str(back_template)
    )
    regions = [
        {
            "region_uuid": f"ru-{index}",
            "page": "front",
            "region_order": index,
            "x": 10,
            "y": 20 + 60 * (index - 1),
            "w": 100,
            "h": 50,
            "detected_question_id": question_id,
            "mapped_question_id": question_id,
            "confidence": 0.9,
            "is_confirmed": True,
            "mapping_status": "manual",
        }
        for index, question_id in enumerate(("Q1", "Q2"), start=1)
    ]
    db.templates.replace_answer_regions_atomic(session_id, template_id, regions, confirmed=True)

    with sqlite3.connect(db.db_path) as conn:
        student_id = int(
            conn.execute(
                "INSERT INTO students (student_code, name) VALUES ('001', 'Alice')"
            ).lastrowid
        )
        paper_id = int(
            conn.execute(
                """
                INSERT INTO exam_papers (
                    session_id, front_image, back_image, ocr_name, student_id,
                    match_status, processing_status
                ) VALUES (?, ?, ?, 'Alice', ?, 'matched', 'failed')
                """,
                (session_id, str(front_image), str(back_image), student_id),
            ).lastrowid
        )
        conn.commit()

    db.reviews.confirm_teacher_score_locks(
        session_id,
        "batch-1",
        [
            {
                "student_id": student_id,
                "question_id": "Q1",
                "score_awarded": 5.0,
                "max_score": 5.0,
                "deduction_reason": "教师认定满分",
                "source_target_type": "exam_paper",
                "source_target_id": paper_id,
                "expected_revision": 0,
            }
        ],
    )

    group = ExamPaperGroup(
        front_image=front_image,
        back_image=back_image,
        student_name="Alice",
        student_id=student_id,
    )
    entry = PaperEntry(
        paper_key="Alice",
        student_id=student_id,
        student_name="Alice",
        group=group,
    )
    ai_result = GradingResult(
        student_name="Alice",
        total_score=10.0,
        student_score=7.0,
        needs_human_review=False,
        grading_details=[
            QuestionGradingDetail(
                question_id="Q1",
                score_awarded=4.0,
                deduction_reason="AI 扣分",
                knowledge_id="K1",
                knowledge_ids=["K1"],
                confidence_score=95.0,
            ),
            QuestionGradingDetail(
                question_id="Q2",
                score_awarded=3.0,
                deduction_reason="计算错误",
                knowledge_id="K2",
                knowledge_ids=["K2"],
                confidence_score=92.0,
            ),
        ],
        raw_json={"Q1": {"score": 4.0}, "Q2": {"score": 3.0}},
    )
    batch_result = AIBatchRunResult(
        paper_entries=[entry],
        results_by_paper_key={"Alice": ai_result},
        fallback_items=[],
        usage_records=[],
        usage_summary={},
    )

    service = GradingService(db, MagicMock(spec=LLMClient))
    with patch(
        "backend.scan_grading.grading_service.run_ai_batch_grading", return_value=batch_result
    ) as run:
        events = list(
            service.run_session_grading(
                session_id,
                exams_dir,
                rubric_path,
                answer_key_path,
                grading_mode="ai",
                scan_batch_id="batch-1",
                failed_only=True,
            )
        )

    # Locked questions are no longer excluded from the AI run.
    assert run.call_args.kwargs["skipped_questions_by_student"] == {}
    assert any(event.get("event") == "graded" for event in events)

    saved = db.results.get_session_results(session_id)[0]
    assert saved["student_score"] == 8.0
    assert saved["ai_student_score"] == 7.0
    details = dual_track_ai_scores__detail_by_question(db, int(saved["result_id"]))
    assert details["Q1"]["score_awarded"] == 5.0
    assert details["Q1"]["ai_score_awarded"] == 4.0
    assert details["Q2"]["score_awarded"] == 3.0
    assert details["Q2"]["ai_score_awarded"] == 3.0


def test_locked_question_without_detail_row_is_inserted(tmp_path: Path) -> None:
    db = open_grading_repositories(tmp_path / "lock-missing.db")
    db.initialize()
    session_id = db.sessions.create_grading_session(
        "锁题补行", "rubric.json", "answer_key.json"
    )
    with sqlite3.connect(db.db_path) as conn:
        student_id = int(
            conn.execute(
                "INSERT INTO students (student_code, name) VALUES ('003', 'Cara')"
            ).lastrowid
        )
        paper_id = int(
            conn.execute(
                """
                INSERT INTO exam_papers (
                    session_id, front_image, back_image, ocr_name, student_id,
                    match_status, processing_status
                ) VALUES (?, 'f.jpg', 'b.jpg', 'Cara', ?, 'matched', 'graded')
                """,
                (session_id, student_id),
            ).lastrowid
        )
        conn.commit()

    result_id = db.results.save_session_result(
        session_id,
        student_id,
        paper_id,
        GradingResult(
            student_name="Cara",
            total_score=5.0,
            student_score=3.0,
            needs_human_review=False,
            grading_details=[
                QuestionGradingDetail(question_id="Q1", score_awarded=3.0, deduction_reason="")
            ],
            raw_json={"Q1": {"score": 3.0}},
        ),
        scan_batch_id="batch-lock",
    )
    db.reviews.confirm_teacher_score_locks(
        session_id,
        "batch-lock",
        [
            {
                "student_id": student_id,
                "question_id": "Q9",
                "score_awarded": 4.0,
                "max_score": 5.0,
                "deduction_reason": "教师认定",
                "source_target_type": "exam_paper",
                "source_target_id": paper_id,
                "expected_revision": 0,
            }
        ],
    )
    # 模拟不完整 AI 结果留下的缺口：锁题没有对应明细行。
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "DELETE FROM session_details WHERE result_id = ? AND question_id = 'Q9'",
            (result_id,),
        )
        conn.commit()

    db.results.replace_result_details_atomic(
        result_id,
        ["Q1"],
        [QuestionGradingDetail(question_id="Q1", score_awarded=2.0, deduction_reason="")],
        student_score=6.0,
        needs_human_review=False,
        raw_json={"Q1": {"score": 2.0}},
        scan_batch_id="batch-lock",
    )

    details = dual_track_ai_scores__detail_by_question(db, result_id)
    assert details["Q9"]["score_awarded"] == 4.0
    assert details["Q9"]["error_summary"] == "teacher_score_locked"
    assert details["Q9"]["error_category"] == "教师已确认"
    assert details["Q1"]["score_awarded"] == 2.0


def manual_review_atomic__seed_two_review_results(tmp_path: Path) -> tuple[DBManager, int, dict[str, int]]:
    db = open_grading_repositories(tmp_path / "manual-review-atomic.db")
    db.initialize()
    session_id = db.sessions.create_grading_session(
        "Atomic review", "rubric.json", "answer.json"
    )
    foreign_session_id = db.sessions.create_grading_session(
        "Foreign atomic review",
        "rubric.json",
        "answer.json",
    )
    ids: dict[str, int] = {"foreign_session_id": foreign_session_id}

    with sqlite3.connect(db.db_path) as conn:
        for label, target_score, other_score in (
            ("first", 4.0, 2.0),
            ("second", 3.0, 1.0),
        ):
            student_id = int(
                conn.execute(
                    "INSERT INTO students (student_code, name) VALUES (?, ?)",
                    (f"S-{label}", f"Student {label}"),
                ).lastrowid
            )
            paper_id = int(
                conn.execute(
                    """
                    INSERT INTO exam_papers (
                        session_id, front_image, back_image, ocr_name, student_id,
                        match_status, processing_status
                    ) VALUES (?, ?, ?, ?, ?, 'matched', 'graded')
                    """,
                    (
                        session_id,
                        f"{label}-front.jpg",
                        f"{label}-back.jpg",
                        f"Student {label}",
                        student_id,
                    ),
                ).lastrowid
            )
            result_id = int(
                conn.execute(
                    """
                    INSERT INTO session_results (
                        session_id, student_id, paper_id, total_score, student_score,
                        needs_human_review, raw_json
                    ) VALUES (?, ?, ?, 15, ?, 1, '{}')
                    """,
                    (session_id, student_id, paper_id, target_score + other_score),
                ).lastrowid
            )
            target_detail_id = int(
                conn.execute(
                    """
                    INSERT INTO session_details (
                        result_id, question_id, score_awarded, deduction_reason,
                        knowledge_ids, error_category, error_summary
                    ) VALUES (?, 'Q1', ?, ?, '["K1"]', ?, ?)
                    """,
                    (
                        result_id,
                        target_score,
                        f"{label} original reason",
                        f"{label} original category",
                        f"{label} original summary",
                    ),
                ).lastrowid
            )
            conn.execute(
                """
                INSERT INTO session_details (
                    result_id, question_id, score_awarded, deduction_reason,
                    knowledge_ids, error_category, error_summary
                ) VALUES (?, 'Q2', ?, 'untouched reason', '["K2"]', 'untouched category', 'untouched summary')
                """,
                (result_id, other_score),
            )
            ids[f"{label}_result_id"] = result_id
            ids[f"{label}_detail_id"] = target_detail_id
        conn.commit()

    return db, session_id, ids


def manual_review_atomic__adjustments(session_id: int, ids: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {
            "session_id": session_id,
            "result_id": ids["first_result_id"],
            "detail_id": ids["first_detail_id"],
            "question_id": "Q1",
            "score_awarded": 8.0,
            "deduction_reason": "first reviewed reason",
            "error_category": "first reviewed category",
            "error_summary": "first reviewed summary",
        },
        {
            "session_id": session_id,
            "result_id": ids["second_result_id"],
            "detail_id": ids["second_detail_id"],
            "question_id": "Q1",
            "score_awarded": 7.0,
            "deduction_reason": "second reviewed reason",
            "error_category": "second reviewed category",
            "error_summary": "second reviewed summary",
        },
    ]


def manual_review_atomic__review_state(
    db: DBManager,
    ids: dict[str, int],
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    detail_ids = (ids["first_detail_id"], ids["second_detail_id"])
    result_ids = (ids["first_result_id"], ids["second_result_id"])
    with sqlite3.connect(db.db_path) as conn:
        details = conn.execute(
            """
            SELECT id, score_awarded, deduction_reason, error_category, error_summary
            FROM session_details
            WHERE id IN (?, ?)
            ORDER BY id
            """,
            detail_ids,
        ).fetchall()
        results = conn.execute(
            """
            SELECT id, student_score
            FROM session_results
            WHERE id IN (?, ?)
            ORDER BY id
            """,
            result_ids,
        ).fetchall()
    return details, results


@pytest.mark.parametrize("forged_owner", ["session_id", "result_id", "question_id"])
def test_atomic_write_revalidates_session_result_and_question_ownership(
    tmp_path: Path,
    forged_owner: str,
) -> None:
    db, session_id, ids = manual_review_atomic__seed_two_review_results(tmp_path)
    adjustment = manual_review_atomic__adjustments(session_id, ids)[0]
    requested_session_id = session_id
    if forged_owner == "session_id":
        requested_session_id = ids["foreign_session_id"]
        adjustment["session_id"] = requested_session_id
    elif forged_owner == "result_id":
        adjustment["result_id"] = ids["second_result_id"]
    else:
        adjustment["question_id"] = "Q2"
    before = manual_review_atomic__review_state(db, ids)

    with pytest.raises(ValueError, match="does not belong"):
        db.reviews.apply_session_review_adjustments(requested_session_id, [adjustment])

    assert manual_review_atomic__review_state(db, ids) == before


def test_second_result_detail_failure_rolls_back_entire_review_batch(
    tmp_path: Path,
) -> None:
    db, session_id, ids = manual_review_atomic__seed_two_review_results(tmp_path)
    before = manual_review_atomic__review_state(db, ids)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            f"""
            CREATE TRIGGER fail_second_review_update
            BEFORE UPDATE ON session_details
            WHEN OLD.id = {ids["second_detail_id"]}
            BEGIN
                SELECT RAISE(ABORT, 'forced second review write failure');
            END;
            """
        )
        conn.commit()

    with pytest.raises(
        sqlite3.IntegrityError, match="forced second review write failure"
    ):
        db.reviews.apply_session_review_adjustments(session_id, manual_review_atomic__adjustments(session_id, ids))

    assert manual_review_atomic__review_state(db, ids) == before


@dataclass(frozen=True)
class review_media_service_SeededMedia:
    service: object
    db: object
    data_root: Path
    exams_dir: Path
    templates_dir: Path
    annotated_dir: Path
    session_id: int
    result_id: int
    detail_id: int
    front_path: Path
    back_path: Path


def review_media_service__image(path: Path, *, color: tuple[int, int, int] = (245, 245, 245)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 300), color).save(path, format="JPEG", quality=95)


def review_media_service__seed_media(tmp_path: Path) -> review_media_service_SeededMedia:
    from backend.media.service import ReviewMediaService


    data_root = tmp_path / "data"
    exams_dir = data_root / "exams"
    templates_dir = data_root / "templates"
    annotated_dir = data_root / "cache" / "annotated_pages"
    db_path = data_root / "databases" / "grading.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = open_grading_repositories(db_path)
    db.initialize()

    front_path = exams_dir / "session_1" / "front.jpg"
    back_path = exams_dir / "session_1" / "back.jpg"
    template_front = templates_dir / "session_1" / "template_front.jpg"
    template_back = templates_dir / "session_1" / "template_back.jpg"
    for path in (front_path, back_path, template_front, template_back):
        review_media_service__image(path)

    session_id = db.sessions.create_grading_session("Media Exam", "rubric.json", "answer.json")
    template_id = db.templates.upsert_session_template(
        session_id,
        str(template_front),
        str(template_back),
    )

    db.templates.add_answer_region(
        session_id,
        template_id,
        {
            "region_uuid": "region-q1",
            "page": "front",
            "region_order": 1,
            "x": 80,
            "y": 60,
            "w": 160,
            "h": 100,
            "mapped_question_id": "Q1",
            "mapping_status": "manual",
            "is_confirmed": True,
        },
    )
    with sqlite3.connect(db.db_path) as conn:
        student_id = int(
            conn.execute(
                "INSERT INTO students (student_code, name, class_name) VALUES ('S001', 'Alice', '1班')"
            ).lastrowid
        )
        paper_id = int(
            conn.execute(
                """
                INSERT INTO exam_papers (
                    session_id, front_image, back_image, ocr_name, student_id,
                    match_status, processing_status
                ) VALUES (?, ?, ?, 'Alice', ?, 'matched', 'graded')
                """,
                (session_id, str(front_path), str(back_path), student_id),
            ).lastrowid
        )
        result_id = int(
            conn.execute(
                """
                INSERT INTO session_results (
                    session_id, student_id, paper_id, total_score, student_score,
                    needs_human_review, raw_json
                ) VALUES (?, ?, ?, 10, 8, 1, '{}')
                """,
                (session_id, student_id, paper_id),
            ).lastrowid
        )
        detail_id = int(
            conn.execute(
                """
                INSERT INTO session_details (
                    result_id, question_id, score_awarded, deduction_reason,
                    knowledge_ids, error_category, error_summary, confidence_score
                ) VALUES (?, 'Q1', 8, '需复核', '["K1"]', '需复核', 'unclear', 55)
                """,
                (result_id,),
            ).lastrowid
        )
        conn.commit()

    service = ReviewMediaService(
        db,
        data_root=data_root,
        exams_dir=exams_dir,
        templates_dir=templates_dir,
        crop_cache_dir=data_root / "cache" / "review_crops",
    )
    return review_media_service_SeededMedia(
        service=service,
        db=db,
        data_root=data_root,
        exams_dir=exams_dir,
        templates_dir=templates_dir,
        annotated_dir=annotated_dir,
        session_id=session_id,
        result_id=result_id,
        detail_id=detail_id,
        front_path=front_path,
        back_path=back_path,
    )


def review_step_confirmation_seed_step_review(tmp_path):
    seed = review_media_service__seed_media(tmp_path)
    rubric_path = seed.data_root / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "question_type": "calculation",
                        "max_score": 7,
                        "parts": [
                            {
                                "part_id": "Q1",
                                "part_score": 7,
                                "response_mode": "process_required",
                                "steps": [
                                    {
                                        "step_id": "S1",
                                        "step_score": 4,
                                        "core_goal": "establish relation",
                                        "evidence_point_ids": ["p1"],
                                    },
                                    {
                                        "step_id": "S2",
                                        "step_score": 3,
                                        "core_goal": "calculate result",
                                        "evidence_point_ids": ["p2"],
                                    },
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with sqlite3.connect(seed.db.db_path) as conn:
        conn.execute(
            "UPDATE grading_sessions SET rubric_path=? WHERE id=?",
            (str(rubric_path), seed.session_id),
        )
        conn.execute(
            "UPDATE session_results SET total_score=7, student_score=7, raw_json=?",
            (
                json.dumps(
                    {
                        "detail_metadata": {
                            "Q1": {
                                "step_assessments": [
                                    {
                                        "step_id": "S1",
                                        "achievement": "full",
                                        "score_awarded": 4,
                                    }
                                ]
                            }
                        }
                    }
                ),
            ),
        )
        conn.execute("UPDATE session_details SET score_awarded=7, ai_score_awarded=7")
    session = seed.db.sessions.get_grading_session(seed.session_id)
    context = {
        "scan_batch_id": "test-step-batch",
        "papers": [{"student_id": 1, "target_type": "group", "target_id": "test"}],
    }
    service = ReviewApplicationService(
        seed.db, ManualReviewService(seed.db)
    )
    return seed, session, context, service


def review_step_confirmation_confirmation(seed, **overrides):
    return ReviewConfirmationInput(
        result_id=seed.result_id,
        detail_id=seed.detail_id,
        score_awarded=6,
        step_scores=[
            {"part_id": "Q1", "step_id": "S1", "score_awarded": 4},
            {"part_id": "Q1", "step_id": "S2", "score_awarded": 2},
        ],
        **overrides,
    )


def test_step_confirmation_roundtrip_partial_means_not_achieved_and_total_only_clears_steps(
    tmp_path,
):
    seed, session, context, service = review_step_confirmation_seed_step_review(tmp_path)
    outcome = service.confirm(
        seed.session_id,
        session,
        "Q1",
        [review_step_confirmation_confirmation(seed)],
        manual_context=context,
    )
    assert outcome.updated_details == 1
    (item,) = service.list_items(seed.session_id, session, manual_context=context)
    assert item.score_awarded == 6 and item.revision == 1
    assert item.metadata["ai_score_awarded"] == 7
    # 无批语且仍有扣分：明细保留原理由，教师锁存确认占位。
    assert review_step_confirmation__detail_reason_and_lock(seed) == ((6.0, "需复核"), "人工复核已确认")
    assert item.needs_review is False
    review = item.metadata["teacher_review"]
    assert [(s["score_awarded"], s["achievement"]) for s in review["steps"]] == [
        (4, "full"),
        (2, "none"),
    ]
    assert review["steps"][1]["evidence_point_ids"] == ["p2"]
    rows = seed.db.results.get_active_assessment_evidence(
        student_ids=["1"], session_ids=[seed.session_id]
    )
    assert rows[0]["assessment_state"]["teacher_review"] == review
    overview_rows = seed.db.results.get_active_assessment_evidence(
        student_ids=["1"], session_ids=[seed.session_id], include_diagnostics=False,
    )
    from integration.diagnosis_profile_service import _teacher_step_records
    from types import SimpleNamespace
    projected = SimpleNamespace(steps=[
        {"part_id": "Q1", "step_id": "S1", "step_score": 4, "evidence_point_ids": ["p1"]},
        {"part_id": "Q1", "step_id": "S2", "step_score": 3, "evidence_point_ids": ["p2"]},
    ])
    assert _teacher_step_records(projected, overview_rows[0]) == review["steps"]
    with pytest.raises(ReviewRevisionConflictError):
        service.confirm(
            seed.session_id,
            session,
            "Q1",
            [review_step_confirmation_confirmation(seed)],
            manual_context=context,
        )
    (unchanged,) = service.list_items(seed.session_id, session, manual_context=context)
    assert unchanged.metadata["teacher_review"] == review
    total_only = replace(
        review_step_confirmation_confirmation(seed), expected_revision=1, score_awarded=5, step_scores=None
    )
    service.confirm(
        seed.session_id,
        session,
        "Q1",
        [total_only],
        manual_context=context,
    )
    (final,) = service.list_items(seed.session_id, session, manual_context=context)
    assert (
        final.score_awarded == 5
        and final.revision == 2
        and "teacher_review" not in final.metadata
    )


def test_teacher_steps_survive_full_ai_regrade_and_missing_ai_metadata(tmp_path):
    from backend.domain_models import GradingResult, QuestionGradingDetail

    seed, session, context, service = review_step_confirmation_seed_step_review(tmp_path)
    service.confirm(
        seed.session_id,
        session,
        "Q1",
        [review_step_confirmation_confirmation(seed)],
        manual_context=context,
    )
    with sqlite3.connect(seed.db.db_path) as conn:
        paper_id = conn.execute(
            "SELECT paper_id FROM session_results WHERE id=?", (seed.result_id,)
        ).fetchone()[0]
    incoming = GradingResult(
        student_name="test",
        total_score=7,
        student_score=0,
        needs_human_review=False,
        grading_details=[
            QuestionGradingDetail(
                question_id="Q1",
                score_awarded=0,
                deduction_reason="new AI",
                knowledge_ids=["K1"],
            )
        ],
        raw_json={"teacher_reviews": {"Q1": {"revision": 999, "steps": []}}},
    )
    seed.db.results.save_session_result(
        seed.session_id, 1, paper_id, incoming, scan_batch_id=context["scan_batch_id"]
    )
    (final,) = service.list_items(seed.session_id, session, manual_context=context)
    assert final.score_awarded == 6 and final.metadata["ai_score_awarded"] == 0
    assert final.metadata["teacher_review"]["revision"] == 1
    assert final.metadata["teacher_review"]["steps"][1]["score_awarded"] == 2


def review_step_confirmation__detail_reason_and_lock(seed):
    with sqlite3.connect(seed.db.db_path) as conn:
        detail = conn.execute(
            "SELECT score_awarded, deduction_reason FROM session_details WHERE id=?",
            (seed.detail_id,),
        ).fetchone()
        lock = conn.execute(
            "SELECT deduction_reason FROM teacher_score_locks WHERE session_id=?",
            (seed.session_id,),
        ).fetchone()
    return detail, lock[0]


@pytest.mark.parametrize("first,second,override,expected", [
    (0, 0, {}, "S1"), (4, 0, {}, None),
    (0, 0, {"carried_error_from": None}, None),
    (0, 0, {"carried_error_from": "OTHER"}, None),
    (0, 1, {"carried_error_from": "S1"}, None),
    (0, 0, {"carried_error_from": "S2"}, None),
    (0, 0, {"carried_error_from": "S1"}, "S1"),
])
def test_carried_error_validates_teacher_scores_and_explicit_null(tmp_path, first, second, override, expected):
    from backend.review.service import _normalize_teacher_steps
    seed, session, context, service = review_step_confirmation_seed_step_review(tmp_path)
    (item,) = service.list_items(seed.session_id, session, manual_context=context)
    item = replace(item, metadata={"step_assessments": [
        {"part_id": "Q1", "step_id": "S2", "score_awarded": 0, "carried_error_from": "S1"}]})
    rubric = json.loads((seed.data_root / "rubric.json").read_text(encoding="utf-8"))
    normalized = _normalize_teacher_steps([
        {"part_id": "Q1", "step_id": "S1", "score_awarded": first},
        {"part_id": "Q1", "step_id": "S2", "score_awarded": second, **override},
    ], rubric, item, first + second)
    assert normalized[1].get("carried_error_from") == expected
    assert "carried_error_from" not in _normalize_teacher_steps([
        {"part_id": "Q1", "step_id": "S1", "score_awarded": 0, "carried_error_from": "S2"},
        {"part_id": "Q1", "step_id": "S2", "score_awarded": 0},
    ], rubric, item, 0)[0]
    rubric["questions"][0]["parts"] = [
        {"part_id": "P1", "part_score": 4, "steps": [{"step_id": "S1", "step_score": 4}]},
        {"part_id": "P2", "part_score": 3, "steps": [{"step_id": "S2", "step_score": 3}]},
    ]
    normalized = _normalize_teacher_steps([
        {"part_id": "P1", "step_id": "S1", "score_awarded": 0},
        {"part_id": "P2", "step_id": "S2", "score_awarded": 0, "carried_error_from": "S1"},
    ], rubric, item, 0)
    assert "carried_error_from" not in normalized[1]
