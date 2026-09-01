from __future__ import annotations

import json
import sqlite3
import warnings
import zipfile
from pathlib import Path

import pytest

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient


COMPLETE_RAW_JSON = json.dumps(
    {"grading_completeness": {"status": "complete"}},
    ensure_ascii=False,
)

PERSONAL_NARRATIVE = {
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

CLASS_NARRATIVE = {
    "key_findings": [
        {"title": "证明题得分率低", "detail": "第 2 题全班得分率不足一半", "severity": "high"}
    ],
    "common_issues": [
        {
            "title": "证明步骤缺失",
            "evidence": "S1、S2 均缺关键步骤",
            "teaching_action": "板书示范 10 分钟",
        }
    ],
    "student_notes": [
        {"alias": "S1", "note": "高但证明扣分", "suggestion": "保持", "flags": []},
        {
            "alias": "S2",
            "note": "客观题失分多",
            "suggestion": "建议当面了解",
            "flags": ["needs_review", "priority_talk"],
        },
    ],
    "grouping_advice": "按分数断层分层布置作业",
}


class FakeLLMClient:
    def __init__(self, narrative: dict | None = None, error: Exception | None = None):
        self.calls = 0
        self._narrative = dict(narrative or PERSONAL_NARRATIVE)
        self._error = error

    def json_from_text(self, prompt, extra_kwargs=None, **_kwargs):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return dict(self._narrative)


def _seed_analysis_session(db, root: Path) -> int:
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
    session_id = db.create_grading_session(
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
                    (session_id, student_id, paper_id, awarded, review, COMPLETE_RAW_JSON),
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
        for name, status in (("张三", "present"), ("李四", "present"), ("王五", "absent")):
            conn.execute(
                """
                INSERT INTO session_attendance (
                    session_id, student_id, attendance_status, source_reason
                ) VALUES (?, ?, ?, ?)
                """,
                (session_id, students[name], status, "测试" if status != "present" else None),
            )
    return session_id


@pytest.fixture
def analysis_db(tmp_path: Path):
    from db_manager import DBManager

    db = DBManager(tmp_path / "databases" / "grading.db")
    db.initialize()
    session_id = _seed_analysis_session(db, tmp_path)
    return db, session_id, tmp_path


def _make_generator(db, out_dir: Path, cache_dir: Path, llm_client) -> "object":
    from analysis_report_exporter import AnalysisReportGenerator

    return AnalysisReportGenerator(
        db,
        out_dir,
        llm_client_factory=lambda: llm_client,
        narrative_cache_dir=cache_dir,
        data_root=out_dir.parent,
    )


def test_personal_export_zip_contains_reports_and_absent_list(
    analysis_db, tmp_path: Path
) -> None:
    db, session_id, root = analysis_db
    client = FakeLLMClient()
    generator = _make_generator(db, tmp_path / "out", tmp_path / "cache", client)

    zip_path = Path(
        generator.export_session(
            session_id, "personal_analysis_html", score_revision="rev-1"
        )
    )

    assert zip_path.suffix == ".zip"
    assert client.calls == 2  # 每名正常参考学生恰好一次调用
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        assert "001_张三_个人报告.html" in names
        assert "002_李四_个人报告.html" in names
        assert "未生成清单.txt" in names
        checklist = archive.read("未生成清单.txt").decode("utf-8")
        assert "王五" in checklist and "缺考" in checklist
        personal_html = archive.read("001_张三_个人报告.html").decode("utf-8")

    assert "张三" in personal_html
    assert "家长版" in personal_html
    assert "整体表现稳定" in personal_html
    assert "证明过程缺关键步骤" in personal_html
    assert "单元测试" in personal_html
    # 隐私：家长版不得出现其他学生姓名。
    assert "李四" not in personal_html
    assert "王五" not in personal_html
    # 李四是待复核卷，页眉应有提示。
    with zipfile.ZipFile(zip_path) as archive:
        review_html = archive.read("002_李四_个人报告.html").decode("utf-8")
    assert "成绩可能调整" in review_html


def test_class_report_type_removed_from_export(analysis_db, tmp_path: Path) -> None:
    """班级分析已改为系统内嵌页面，不再是导出类型。"""
    db, session_id, _root = analysis_db
    generator = _make_generator(db, tmp_path / "out", tmp_path / "cache", FakeLLMClient())

    with pytest.raises(ValueError, match="不支持的分析报告类型"):
        generator.export_session(session_id, "class_analysis_html", score_revision="rev-1")


def test_llm_failure_degrades_to_data_only_report(analysis_db, tmp_path: Path) -> None:
    from llm_client import LLMResponseFormatError

    db, session_id, _root = analysis_db
    client = FakeLLMClient(error=LLMResponseFormatError("bad json"))
    generator = _make_generator(db, tmp_path / "out", tmp_path / "cache", client)

    zip_path = Path(
        generator.export_session(
            session_id, "personal_analysis_html", score_revision="rev-1"
        )
    )

    assert client.calls == 2  # 失败不暗中重发
    with zipfile.ZipFile(zip_path) as archive:
        personal_html = archive.read("001_张三_个人报告.html").decode("utf-8")
    assert "AI 分析生成失败，可重新生成" in personal_html
    # 数据段照常渲染。
    assert "逐题得分对比" in personal_html
    assert "丢分题逐题分析" in personal_html


def test_narrative_cache_avoids_repeat_model_calls(analysis_db, tmp_path: Path) -> None:
    db, session_id, _root = analysis_db
    cache_dir = tmp_path / "cache"
    client = FakeLLMClient()
    first = _make_generator(db, tmp_path / "out1", cache_dir, client)
    first_zip = Path(
        first.export_session(session_id, "personal_analysis_html", score_revision="rev-1")
    )
    assert client.calls == 2

    # 下载后文件被删再生成：缓存命中，不再调用模型。
    first_zip.unlink()
    second = _make_generator(db, tmp_path / "out2", cache_dir, client)
    second_zip = Path(
        second.export_session(
            session_id, "personal_analysis_html", score_revision="rev-1"
        )
    )
    assert second_zip.is_file()
    assert client.calls == 2


def test_missing_images_degrade_to_text_cards(analysis_db, tmp_path: Path) -> None:
    # 没有批注图、原卷路径为空、也没有 region：应静默降级为纯文字卡片。
    db, session_id, _root = analysis_db
    client = FakeLLMClient()
    generator = _make_generator(db, tmp_path / "out", tmp_path / "cache", client)

    zip_path = Path(
        generator.export_session(
            session_id, "personal_analysis_html", score_revision="rev-1"
        )
    )

    with zipfile.ZipFile(zip_path) as archive:
        personal_html = archive.read("001_张三_个人报告.html").decode("utf-8")
    assert "data:image/jpeg;base64" not in personal_html
    assert "丢分题逐题分析" in personal_html


def test_question_screenshot_embedded_from_original_scan(
    analysis_db, tmp_path: Path
) -> None:
    from PIL import Image

    db, session_id, root = analysis_db
    scan_dir = root / "exams"
    scan_dir.mkdir(parents=True, exist_ok=True)
    scan_path = scan_dir / "scan1.png"
    Image.new("RGB", (400, 300), (200, 210, 220)).save(scan_path)
    with sqlite3.connect(db.db_path) as conn:
        template_id = int(
            conn.execute(
                """
                INSERT INTO session_templates (
                    session_id, front_template_path, back_template_path
                ) VALUES (?, ?, '')
                """,
                (session_id, str(scan_path)),
            ).lastrowid
        )
        conn.execute(
            """
            INSERT INTO answer_regions (
                region_uuid, session_id, template_id, page, region_order,
                x, y, w, h, mapped_question_id
            ) VALUES ('r-q2', ?, ?, 'front', 1, 10, 10, 200, 100, 'Q2')
            """,
            (session_id, template_id),
        )
        conn.execute(
            "UPDATE exam_papers SET front_image = ? WHERE session_id = ?",
            (str(scan_path), session_id),
        )

    client = FakeLLMClient()
    generator = _make_generator(db, tmp_path / "out", tmp_path / "cache", client)
    zip_path = Path(
        generator.export_session(
            session_id, "personal_analysis_html", score_revision="rev-1"
        )
    )

    with zipfile.ZipFile(zip_path) as archive:
        personal_html = archive.read("001_张三_个人报告.html").decode("utf-8")
    # 张三只有 Q2 丢分 → 恰好一张原卷截图。
    assert personal_html.count("data:image/jpeg;base64") == 1
    assert "原卷截图" in personal_html


@pytest.fixture
def analysis_api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_grading_db,
        get_job_manager,
        get_reports_dir,
    )
    from backend.jobs.manager import JobContext, JobManager
    from backend.jobs.store import JobStore
    from db_manager import DBManager

    db = DBManager(tmp_path / "databases" / "grading.db")
    db.initialize()
    session_id = _seed_analysis_session(db, tmp_path)
    reports_dir = tmp_path / "databases" / ".." / "reports"
    reports_dir = (tmp_path / "reports").resolve()
    reports_dir.mkdir(parents=True, exist_ok=True)

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)

    def report_handler(context: JobContext) -> dict[str, object]:
        report_type = str(context.payload.get("report_type") or "score_excel")
        suffix = ".zip" if report_type == "personal_analysis_html" else ".xlsx"
        report_path = reports_dir / f"report{suffix}"
        report_path.write_bytes(b"fake")
        return {
            "session_id": int(context.payload["session_id"]),
            "file_path": str(report_path),
            "filename": report_path.name,
        }

    manager.register("report_export", report_handler)
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_reports_dir] = lambda: reports_dir
    with TestClient(app) as client:
        try:
            yield client, db, session_id, reports_dir, manager, monkeypatch
        finally:
            manager.shutdown()


def _patch_configured(monkeypatch: pytest.MonkeyPatch, configured: bool) -> None:
    import analysis_report_exporter as exporter
    from llm_client import LLMSettings

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
        lambda: (("测试服务 @ example.com", "content-model") if configured else (None, None)),
    )


def test_analysis_preflight_configured(analysis_api_client) -> None:
    client, db, session_id, _reports_dir, _manager, monkeypatch = analysis_api_client
    _patch_configured(monkeypatch, True)

    response = client.get(
        f"/api/sessions/{session_id}/reports/analysis-preflight",
        params={"report_type": "personal_analysis_html"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "report_type": "personal_analysis_html",
        "configured": True,
        "service_name": "测试服务 @ example.com",
        "model_name": "content-model",
        "call_count": 2,
        "estimated_total_tokens": payload["estimated_total_tokens"],
        "cache_hits": 0,
    }
    assert payload["estimated_total_tokens"] > 0

    class_response = client.get(
        f"/api/sessions/{session_id}/reports/analysis-preflight",
        params={"report_type": "class_analysis_html"},
    )
    # 班级分析已改为系统内嵌页面，preflight 不再接受该类型。
    assert class_response.status_code == 422


def test_analysis_preflight_unconfigured(analysis_api_client) -> None:
    client, _db, session_id, _reports_dir, _manager, monkeypatch = analysis_api_client
    _patch_configured(monkeypatch, False)

    response = client.get(
        f"/api/sessions/{session_id}/reports/analysis-preflight",
        params={"report_type": "personal_analysis_html"},
    )

    payload = response.json()
    assert payload["configured"] is False
    assert payload["service_name"] is None
    assert payload["model_name"] is None
    assert payload["call_count"] == 2


def test_analysis_preflight_counts_cache_hits(analysis_api_client) -> None:
    client, db, session_id, reports_dir, _manager, monkeypatch = analysis_api_client
    _patch_configured(monkeypatch, True)

    from analysis_report_exporter import AnalysisNarrativeCache
    from backend.report_exports import report_rendition_version, score_revision

    revision = score_revision(db, session_id)
    cache = AnalysisNarrativeCache(reports_dir / ".analysis_narrative_cache")
    # 为其中一名学生预置缓存（student_id=1 即张三）。
    key = AnalysisNarrativeCache.cache_key(
        session_id=session_id,
        score_revision=revision,
        rendition_version=report_rendition_version("personal_analysis_html"),
        report_key="personal:1",
    )
    cache.store(key, PERSONAL_NARRATIVE)

    response = client.get(
        f"/api/sessions/{session_id}/reports/analysis-preflight",
        params={"report_type": "personal_analysis_html"},
    )

    payload = response.json()
    assert payload["cache_hits"] == 1
    assert payload["call_count"] == 1


def test_analysis_submit_rejected_when_model_unconfigured(analysis_api_client) -> None:
    client, _db, session_id, _reports_dir, _manager, monkeypatch = analysis_api_client
    _patch_configured(monkeypatch, False)

    response = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={"report_type": "personal_analysis_html"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "content_generation_model_not_configured"


def test_analysis_submit_accepted_when_model_configured(analysis_api_client) -> None:
    client, _db, session_id, _reports_dir, manager, monkeypatch = analysis_api_client
    _patch_configured(monkeypatch, True)

    response = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={"report_type": "personal_analysis_html"},
    )

    assert response.status_code == 202
    created = response.json()
    assert created["payload"]["report_type"] == "personal_analysis_html"
    manager.wait(created["id"], timeout=5)
    loaded = client.get(f"/api/jobs/{created['id']}").json()
    assert loaded["status"] == "succeeded"
    assert loaded["result"]["filename"] == "report.zip"


def test_analysis_submit_rejects_excel_options(analysis_api_client) -> None:
    client, _db, session_id, _reports_dir, _manager, monkeypatch = analysis_api_client
    _patch_configured(monkeypatch, True)

    response = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={
            "report_type": "personal_analysis_html",
            "excel_options": {"hide_bottom_enabled": False},
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_analysis_submit_rejects_removed_class_report_type(analysis_api_client) -> None:
    client, _db, session_id, _reports_dir, _manager, monkeypatch = analysis_api_client
    _patch_configured(monkeypatch, True)

    response = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={"report_type": "class_analysis_html"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
