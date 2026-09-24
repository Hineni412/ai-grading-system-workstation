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
        self.requests: list[dict] = []
        self._narrative = dict(narrative or PERSONAL_NARRATIVE)
        self._error = error

    def json_from_text(self, prompt, extra_kwargs=None, **_kwargs):
        return self._respond(prompt, [], extra_kwargs, _kwargs)

    def json_from_images_once(self, prompt, image_blobs, extra_kwargs=None, **kwargs):
        return self._respond(prompt, image_blobs, extra_kwargs, kwargs)

    def _respond(self, prompt, images, extra_kwargs, kwargs):
        self.calls += 1
        self.requests.append({"prompt": prompt, "images": images, "options": extra_kwargs, **kwargs})
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
            if review:
                conn.execute("UPDATE session_details SET confidence_score=70 WHERE result_id=? AND question_id='Q1'", (result_id,))
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
    assert "家长报告" in personal_html
    # 新版式用本地生成的结论句，不展示 overall_comment 原文。
    assert "丢的 10 分都在第2题" in personal_html
    assert "证明过程缺关键步骤" in personal_html
    assert "单元测试" in personal_html
    # 隐私：家长版不得出现其他学生姓名。
    assert "李四" not in personal_html
    assert "王五" not in personal_html
    # 李四是待复核卷，成绩卡应有提示。
    with zipfile.ZipFile(zip_path) as archive:
        review_html = archive.read("002_李四_个人报告.html").decode("utf-8")
    assert "分数可能微调" in review_html


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
    # 无 AI 叙述版降级为老师批语/参考答案，不给家长看内部失败提示与原始批改记录。
    assert "AI 分析生成失败" not in personal_html
    assert "缺关键步骤" not in personal_html
    # 数据段照常渲染。
    assert "本卷答题一览" in personal_html
    assert "全部失分题详解" in personal_html


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


def test_legacy_narrative_cache_hit_skips_model_client(
    analysis_db, tmp_path: Path
) -> None:
    """旧版叙述缓存命中时直接复用：不初始化模型客户端，也不回写新 key。"""
    from analysis_report_exporter import AnalysisNarrativeCache, AnalysisReportGenerator
    from backend.report_exports import LEGACY_PERSONAL_NARRATIVE_VERSIONS

    db, session_id, root = analysis_db
    cache_dir = tmp_path / "cache"
    cache = AnalysisNarrativeCache(cache_dir)
    for student_id in (1, 2):
        key = AnalysisNarrativeCache.cache_key(
            session_id=session_id,
            score_revision="rev-1",
            rendition_version=LEGACY_PERSONAL_NARRATIVE_VERSIONS[0],
            report_key=f"personal:{student_id}",
        )
        cache.store(key, PERSONAL_NARRATIVE)

    def unavailable_factory():
        raise AssertionError("cached reports must not initialize a model client")

    generator = AnalysisReportGenerator(
        db,
        tmp_path / "out",
        llm_client_factory=unavailable_factory,
        narrative_cache_dir=cache_dir,
        data_root=root,
    )
    zip_path = Path(
        generator.export_session(
            session_id, "personal_analysis_html", score_revision="rev-1"
        )
    )
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as archive:
        html_text = archive.read("001_张三_个人报告.html").decode("utf-8")
    assert "证明书写不完整" in html_text  # 复用了旧版叙述内容
    # 旧 key 不回写到新版本：缓存目录仍只有预置的两份。
    assert len(list(cache_dir.glob("*.json"))) == 2


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
    assert "全部失分题详解" in personal_html


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
    # 张三只有 Q2 丢分 → 恰好一张原卷截图（在方格面板/附录等处复用同一张）。
    import re

    shots = set(re.findall(r"data:image/jpeg;base64,[A-Za-z0-9+/=]+", personal_html))
    assert len(shots) == 1
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
        # 前置错因整理：本场 2 道失分题均未整理过 → 各需 1 次调用。
        "cause_call_count": payload["cause_call_count"],
        "cause_total_questions": payload["cause_total_questions"],
        "cause_estimated_tokens": payload["cause_estimated_tokens"],
    }
    assert payload["estimated_total_tokens"] > 0
    assert payload["cause_total_questions"] == 2
    assert payload["cause_call_count"] == 2
    assert payload["cause_estimated_tokens"] > 0

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
    # 未配置模型时仍照常预估整理次数（展示给用户，不会产生调用）。
    assert payload["cause_total_questions"] == 2
    assert payload["cause_call_count"] == 2


def test_analysis_preflight_counts_cache_hits(analysis_api_client) -> None:
    client, db, session_id, reports_dir, _manager, monkeypatch = analysis_api_client
    _patch_configured(monkeypatch, True)

    from analysis_report_exporter import AnalysisNarrativeCache
    from backend.report_exports import report_narrative_version, score_revision

    revision = score_revision(db, session_id)
    cache = AnalysisNarrativeCache(reports_dir / ".analysis_narrative_cache")
    # 为其中一名学生预置缓存（student_id=1 即张三）。
    key = AnalysisNarrativeCache.cache_key(
        session_id=session_id,
        score_revision=revision,
        rendition_version=report_narrative_version("personal_analysis_html"),
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


# ---------------------------------------------------------------------------
# 报告内容口径：题型中译、批改记录清洗、标准答案收敛、学生作答回填
# ---------------------------------------------------------------------------


def test_question_type_label_never_renders_raw_english() -> None:
    from analysis_report_exporter import _question_type_label

    assert _question_type_label("comprehensive") == "解答"
    assert _question_type_label("proof") == "证明"
    assert _question_type_label("judgement") == "判断"
    assert _question_type_label("unknown_new_type") == ""
    assert _question_type_label("") == ""
    assert _question_type_label("综合题") == "综合题"  # 已是中文的兼容输入照常显示


def test_sanitize_grading_text_translates_internal_codes() -> None:
    from analysis_report_exporter import _sanitize_grading_text

    assert (
        _sanitize_grading_text("objective_answer=65")
        == "作答识别为「65」，与参考答案不符"
    )
    assert _sanitize_grading_text("low_confidence") == "作答辨识度较低，需要教师复核"
    assert _sanitize_grading_text("前缀 low_confidence 后缀") == "作答辨识度较低，需要教师复核"
    assert _sanitize_grading_text("缺关键步骤") == "缺关键步骤"
    # 无中文信息的英文原文不展示给家长。
    assert _sanitize_grading_text("No valid answer or proof provided") == ""
    assert _sanitize_grading_text("") == ""


def test_format_secondary_error_dict_to_text() -> None:
    from analysis_report_exporter import _format_secondary_error

    assert (
        _format_secondary_error(
            {"category": "其他", "summary": "推导关系颠倒", "evidence": "x:y=2:1"}
        )
        == "推导关系颠倒（x:y=2:1）"
    )
    assert _format_secondary_error({"category": "未作答"}) == "未作答"
    assert _format_secondary_error("推导关系颠倒") == "推导关系颠倒"


def test_display_answer_text_dedupes_and_caps_variants() -> None:
    from analysis_report_exporter import _display_answer_text

    # 标点/全半角/度数写法差异的重复变体收敛为一个代表形式。
    proof_a = "∠1与∠3相等，理由是：由AB=BC，BE平分∠ABC可得BE⊥AC，故∠BEA=90°。"
    proof_b = "∠1与∠3相等，理由是:由AB＝BC，BE平分∠ABC可得BE⊥AC，故∠BEA＝90度。"
    assert _display_answer_text([proof_a, proof_b]) == proof_a
    # 短答案保留多解并列，精确重复去掉。
    assert _display_answer_text(["65°", "65° ", "50°", "80°"]) == "65° 或 50° 或 80°"
    assert _display_answer_text([]) == ""


def test_student_answer_map_from_raw_json() -> None:
    from analysis_report_exporter import _student_answer_map

    raw = {
        "grading_details": [
            {"question_id": "Q9", "observed_answer": "65°"},
            {"question_id": "Q12(P2)", "observed_answer": ""},
            {"question_id": "Q12(P3)"},
        ]
    }
    assert _student_answer_map(raw) == {"Q9": "65°"}
    # 兼容未解析的 JSON 字符串与坏输入。
    assert _student_answer_map(json.dumps(raw, ensure_ascii=False)) == {"Q9": "65°"}
    assert _student_answer_map("not json") == {}
    assert _student_answer_map(None) == {}


def test_personal_payload_and_record_sanitized(analysis_db) -> None:
    from analysis_report_exporter import (
        assemble_session_analysis,
        build_personal_payload,
    )

    db, session_id, root = analysis_db
    data = assemble_session_analysis(db, session_id, data_root=root)
    lisi = next(s for s in data.students if s.student_name == "李四")
    q1 = next(r for r in lisi.records if r.question_id == "Q1")
    # 客观题调试串被翻译，作答值回填到 student_answer。
    assert q1.deduction_reason == "作答识别为「C」，与参考答案不符"
    assert q1.student_answer == "C"

    payload = build_personal_payload(data, lisi)
    q1_payload = next(q for q in payload["questions"] if q["question_id"] == "Q1")
    assert q1_payload["student_answer"] == "C"
    assert (
        q1_payload["grading_record"]["deduction_reason"]
        == "作答识别为「C」，与参考答案不符"
    )


def test_personal_report_html_sanitized_and_typed(analysis_db, tmp_path: Path) -> None:
    db, session_id, root = analysis_db
    # 把 Q2 改成配置生成对解答大题的真实类型码。
    rubric_path = root / "config" / "uploaded" / "rubric.json"
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric["questions"][1]["question_type"] = "comprehensive"
    rubric_path.write_text(json.dumps(rubric, ensure_ascii=False), encoding="utf-8")

    generator = _make_generator(db, tmp_path / "out", tmp_path / "cache", FakeLLMClient())
    zip_path = Path(
        generator.export_session(
            session_id, "personal_analysis_html", score_revision="rev-1"
        )
    )

    with zipfile.ZipFile(zip_path) as archive:
        zhangsan_html = archive.read("001_张三_个人报告.html").decode("utf-8")
        lisi_html = archive.read("002_李四_个人报告.html").decode("utf-8")

    # 英文类型码不得出现在报告里，大题行显示中文题型。
    assert "comprehensive" not in zhangsan_html
    assert "第2题 · 解答" in zhangsan_html
    # 原始批改记录不再展示（含翻译后的扣分理由），学生作答仍单独成行。
    assert "objective_answer" not in lisi_html
    assert "作答识别为「C」，与参考答案不符" not in lisi_html
    assert "<dt>批改记录</dt>" not in lisi_html
    assert "<dt>学生作答</dt><dd>C</dd>" in lisi_html


def test_report_displays_part_knowledge_difficulty_and_keeps_exam_rate(analysis_db) -> None:
    from analysis_report_exporter import assemble_session_analysis, build_personal_payload, _render_personal_html, _knowledge_rows
    db, session_id, root = analysis_db
    data = assemble_session_analysis(db, session_id, data_root=root)
    student = data.students[0]
    record = next(r for r in student.records if r.lost)
    before = (student.student_score, record.score, record.max_score, student.rank)
    data.knowledge_backfill = {record.question_id: [{'path':'几何｜三角形全等','label':'三角形全等','stable_key':'kp_geo_triangle_congruence'}]}
    data.question_assessments = {record.question_id: {'granularity':'part','part_difficulty':8,'reason':'part_composite_attribution_limited'}}
    payload = build_personal_payload(data,student)
    question = next(q for q in payload['questions'] if q['question_id'] == record.question_id)
    assert question['part_assessment']['part_difficulty'] == 8
    assert question['direct_knowledge'][0]['stable_key'] == 'kp_geo_triangle_congruence'
    assert _knowledge_rows(data.knowledge_backfill,student.records)[0]['rate'] == record.score / record.max_score
    html = _render_personal_html(data,student,PERSONAL_NARRATIVE,{})
    # 新版式：直接考查标签进入「本次考查点」三态版块与方格面板，不再展示
    # 小问难度数值与掌握度百分比。
    assert '三角形全等' in html
    assert '本次考查点' in html
    assert '知识与技能掌握图' not in html
    assert '不以得分率代替掌握度' not in html
    assert before == (student.student_score, record.score, record.max_score, student.rank)


def test_compact_report_preserves_review_solution_and_missing_stem(analysis_db) -> None:
    from analysis_report_exporter import assemble_session_analysis, _render_personal_html

    db, session_id, root = analysis_db
    data = assemble_session_analysis(db, session_id, data_root=root)
    student = data.students[0]
    record = next(r for r in student.records if r.lost)
    info = next(q for q in data.questions if q.question_id == record.question_id)
    info.question_markup = '<p>完整原题表格</p><table><tr><td>x</td><td>20</td></tr><tr><td>y</td><td>___</td></tr></table>[[IMAGE:synthetic/source.png]]'
    record.deduction_reason = "必须隐藏的旧批改记录"
    record.teacher_comment = "教师批语应当保留"
    item = {"question_id": record.question_id, "feedback": "已写正确关系，需补依据。",
            "stem_in_scan": True, "review_note": "原卷与扣分理由冲突。",
            "solution_steps": ["由两底角相等得出关系。", "代入直角得到结论。"],
            "solution_source": "reference", "full_solution": "完整证明细节。",
            "revision_task": "补写两步。"}
    narrative = {**PERSONAL_NARRATIVE, "question_analyses": [item]}
    without_scan = _render_personal_html(data, student, narrative, {})
    assert "<table><tr><td>x</td><td>20</td>" in without_scan
    assert "[[IMAGE:" not in without_scan and "synthetic/source.png" not in without_scan
    assert "教师批语应当保留" in without_scan
    assert "必须隐藏的旧批改记录" not in without_scan
    assert "报告分析提示 · 建议核对" in without_scan and "原卷与扣分理由冲突。" in without_scan
    assert "AI 参考解法" in without_scan  # no supplied reference analysis
    assert "<summary>查看完整解法</summary>" in without_scan
    assert "作答表现" not in without_scan and "针对补练" not in without_scan
    shots = {record.question_id: {"data_uri": "data:image/png;base64,eA==", "caption": "完整作答截图"}}
    with_scan = _render_personal_html(data, student, narrative, shots)
    assert "完整原题表格" in with_scan
    assert 'class="shot"' in with_scan and "完整作答截图" in with_scan
    assert "订正任务" not in with_scan and "补写两步。" not in with_scan
    item["stem_in_scan"] = False
    assert "完整原题表格" in _render_personal_html(data, student, narrative, shots)


def test_report_math_is_offline_and_preserves_explicit_tex() -> None:
    from analysis_report_exporter import _report_inline_math, _report_math_assets, _QuestionInfo, _report_stem_html
    inline = _report_inline_math(r"求y＝x/2与\(\frac{a+b}{c}\)，保留0/6分")
    assert r'data-latex="y=\frac{x}{2}"' in inline
    assert r'data-latex="\frac{a+b}{c}"' in inline
    assert "保留0/6分" in inline
    assert r'\frac{180^{\circ}-x^{\circ}}{2}' in _report_inline_math("两底角均为(180°－x°)/2。")
    assert r'\frac{x^{\circ}}{2}' in _report_inline_math("角为x°/2。")
    assert r'\frac{1}{2x}' not in _report_inline_math("y=1/2x")
    info = _QuestionInfo("Q1", "proof", 5, "", "", question_markup='<span class="qm" data-latex="x^{2}">x²</span>')
    assert 'data-latex="x^{2}"' in _report_stem_html(info, "")
    assets = _report_math_assets()
    assert 'data:font/woff2;base64,' in assets
    assert 'url(fonts/' not in assets


def test_report_lost_questions_follow_numeric_order(analysis_db) -> None:
    from dataclasses import replace
    from analysis_report_exporter import assemble_session_analysis, _render_personal_html
    db, session_id, root = analysis_db
    data = assemble_session_analysis(db, session_id, data_root=root)
    student = data.students[0]
    base = next(r for r in student.records if r.lost)
    info = next(q for q in data.questions if q.question_id == base.question_id)
    student.records = [replace(base, question_id=qid, score=0) for qid in ("Q10", "Q2", "Q1")]
    data.questions = [replace(info, question_id=qid) for qid in ("Q10", "Q2", "Q1")]
    rendered = _render_personal_html(data, student, PERSONAL_NARRATIVE, {})
    import re
    assert re.findall(r'<div class="head"><b>第(\d+)题', rendered) == ["1", "2", "10"]


def test_question_grid_marks_status_and_class_average(analysis_db) -> None:
    from analysis_report_exporter import assemble_session_analysis, _render_personal_html
    db, session_id, root = analysis_db
    data = assemble_session_analysis(db, session_id, data_root=root)
    student = data.students[0]
    rendered = _render_personal_html(data, student, PERSONAL_NARRATIVE, {})
    # 答题一览为每个小问出一枚方格，标注得分状态；面板内给全班平均。
    assert rendered.count('class="qcell ') == len(student.records)
    assert 'class="qcell full"' in rendered  # Q1 满分
    assert 'class="qcell part"' in rendered  # Q2 部分得分
    # 两处原位面板各一次，失分题详解（Q2）再出现一次。
    assert rendered.count("全班平均") == 3
    data.questions[0].attempts = 0
    unknown_mean = _render_personal_html(data, student, PERSONAL_NARRATIVE, {})
    # Q1 面板不再给班均；剩 Q2 面板与失分题详解各一次。
    assert unknown_mean.count("全班平均") == 2


def test_report_review_status_follows_teacher_confirmation(analysis_db) -> None:
    from analysis_report_exporter import assemble_session_analysis, _render_personal_html
    db, session_id, root = analysis_db
    data = assemble_session_analysis(db, session_id, data_root=root)
    student = next(s for s in data.students if s.student_name == "李四")
    assert student.needs_review  # low confidence even without a category marker
    with sqlite3.connect(db.db_path) as conn:
        paper_id = conn.execute("SELECT paper_id FROM session_results WHERE id=?", (student.result_id,)).fetchone()[0]
        conn.execute("""INSERT INTO teacher_score_locks (
            session_id,scan_batch_id,student_id,question_id,score_awarded,max_score,
            source_target_type,source_target_id
        ) VALUES (?, 'synthetic-batch', ?, 'Q1', 30, 60, 'exam_paper', ?)""", (session_id, student.student_id, paper_id))
    before = _score_state(db.db_path)
    updated = assemble_session_analysis(db, session_id, data_root=root)
    confirmed = next(s for s in updated.students if s.student_id == student.student_id)
    assert not confirmed.needs_review
    assert confirmed.student_score == student.student_score
    rendered = _render_personal_html(updated, confirmed, {**PERSONAL_NARRATIVE,
        "question_analyses": [{"question_id": "Q2", "feedback": "需要补充依据。", "review_note": "本次报告发现的独立疑点。"}]}, {})
    assert "有题目等待老师复核" not in rendered
    assert "报告分析提示" in rendered and "本次报告发现的独立疑点。" in rendered
    assert _score_state(db.db_path) == before
    with sqlite3.connect(db.db_path) as conn:
        assert conn.execute("SELECT needs_human_review FROM session_results WHERE id=?", (student.result_id,)).fetchone()[0] == 1
        conn.execute("UPDATE session_details SET confidence_score=60 WHERE result_id=? AND question_id='Q2'", (student.result_id,))
    still_pending = assemble_session_analysis(db, session_id, data_root=root)
    assert next(s for s in still_pending.students if s.student_id == student.student_id).needs_review


def _add_personal_report_scans(db, session_id: int, root: Path) -> list[dict]:
    from PIL import Image

    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        papers = [dict(row) for row in conn.execute(
            "SELECT id, student_id FROM exam_papers WHERE session_id=? ORDER BY id", (session_id,),
        )]
        for index, paper in enumerate(papers):
            path = root / f"synthetic-paper-{paper['id']}.png"
            Image.new("RGB", (400, 300), (220, 30, 30) if index == 0 else (30, 30, 220)).save(path)
            conn.execute("UPDATE exam_papers SET front_image=? WHERE id=?", (str(path), paper["id"]))
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
                (f"report-{qid}", session_id, template_id, index, 10 + index * 110, qid),
            )
    return papers


def _score_state(db_path: Path) -> list[list[tuple]]:
    with sqlite3.connect(db_path) as conn:
        return [conn.execute(sql).fetchall() for sql in (
            "SELECT * FROM session_results ORDER BY id",
            "SELECT * FROM session_details ORDER BY id",
            "SELECT * FROM teacher_score_locks ORDER BY id",
        )]


@pytest.mark.parametrize("manual_only", [True, False])
def test_visual_reports_include_manual_students_and_keep_final_scores(
    analysis_db, tmp_path: Path, manual_only: bool,
) -> None:
    import io
    from PIL import Image
    from analysis_report_exporter import assemble_session_analysis

    db, session_id, root = analysis_db
    papers = _add_personal_report_scans(db, session_id, root)
    selected = papers if manual_only else papers[1:]
    with sqlite3.connect(db.db_path) as conn:
        for paper in selected:
            student_id = paper["student_id"]
            conn.execute("DELETE FROM session_details WHERE result_id IN (SELECT id FROM session_results WHERE student_id=?)", (student_id,))
            conn.execute("DELETE FROM session_results WHERE student_id=?", (student_id,))
            for qid, score, maximum in (("Q1", 40, 60), ("Q2", 15, 40)):
                conn.execute(
                    """INSERT INTO teacher_score_locks (
                        session_id, scan_batch_id, student_id, question_id,
                        score_awarded, max_score, deduction_reason,
                        source_target_type, source_target_id
                    ) VALUES (?, 'synthetic-batch', ?, ?, ?, ?, '教师确认的过程分', 'exam_paper', ?)""",
                    (session_id, student_id, qid, score, maximum, paper["id"]),
                )
    before = _score_state(db.db_path)
    data = assemble_session_analysis(db, session_id, data_root=root)
    assert len(data.students) == 2
    assert sum(student.result_id < 0 for student in data.students) == len(selected)
    client = FakeLLMClient()
    generator = _make_generator(db, tmp_path / "out", tmp_path / "cache", client)
    output = generator.export_session(session_id, "personal_analysis_html", score_revision="manual-v1")
    assert _score_state(db.db_path) == before
    assert client.calls == 2
    with zipfile.ZipFile(output) as archive:
        for student, request in zip(data.students, client.requests, strict=True):
            payload = json.loads(request["prompt"].split("输入 JSON：\n", 1)[1])
            assert payload["student"]["total_score"] == student.student_score
            assert request["use_config_client"] is True
            assert len(request["images"]) == len(payload["image_map"]) >= 2
            assert [item["image_number"] for item in payload["image_map"]] == list(range(1, len(request["images"]) + 1))
            assert any(item["kind"] == "student_overview" for item in payload["image_map"])
            # Different solid colours ensure the report receives its own student's image.
            with Image.open(io.BytesIO(request["images"][0])) as image:
                red, _, blue = image.convert("RGB").getpixel((0, 0))
            assert (red > blue) == (student.student_id == papers[0]["student_id"])
            if student.result_id < 0:
                assert student.student_score == 55
                assert all(q["grading_record"]["teacher_confirmed"] for q in payload["questions"] if q["lost"])
            text = archive.read(f"{student.student_code}_{student.student_name}_个人报告.html").decode("utf-8")
            assert "原卷截图" in text
            assert "未取得该生可读取的答卷图片" not in text
    # Re-entering export reuses the existing narrative; it does not regrade or call twice.
    generator.export_session(session_id, "personal_analysis_html", score_revision="manual-v1")
    assert client.calls == 2
    assert _score_state(db.db_path) == before


def test_visual_report_failure_keeps_scores_and_does_not_retry_as_text(analysis_db, tmp_path: Path) -> None:
    db, session_id, root = analysis_db
    _add_personal_report_scans(db, session_id, root)
    before = _score_state(db.db_path)
    client = FakeLLMClient(error=TimeoutError("uncertain visual response"))
    output = _make_generator(db, tmp_path / "out", tmp_path / "cache", client).export_session(
        session_id, "personal_analysis_html", score_revision="visual-timeout",
    )
    assert client.calls == 2
    assert all(request["images"] for request in client.requests)
    assert _score_state(db.db_path) == before
    with zipfile.ZipFile(output) as archive:
        text = archive.read("001_张三_个人报告.html").decode("utf-8")
    # 无 AI 叙述版降级为数据展示，不向家长暴露失败提示。
    assert "AI 分析生成失败" not in text
    assert "原卷截图" in text


def test_report_uses_saved_hybrid_steps_without_treating_absent_text_as_blank(analysis_db) -> None:
    from analysis_report_exporter import assemble_session_analysis, build_personal_payload

    db, session_id, root = analysis_db
    answer = "已写出的推导过程" * 50
    raw = {"grading_completeness": {"status": "complete"}, "detail_metadata": {
        "Q2": {"observed_answer": answer, "evidence_steps": ["AB=AC"], "missing_steps": ["未说明两角相等的依据"]},
    }}
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE session_results SET raw_json=? WHERE session_id=?", (json.dumps(raw, ensure_ascii=False), session_id))
    data = assemble_session_analysis(db, session_id, data_root=root)
    payload = build_personal_payload(data, data.students[0])
    question = next(item for item in payload["questions"] if item["question_id"] == "Q2")
    assert question["student_answer"] == answer
    assert question["grading_record"]["evidence_steps"] == ["AB=AC"]
    assert question["grading_record"]["missing_steps"] == ["未说明两角相等的依据"]


def test_personal_reference_context_only_uses_the_bound_source(analysis_db, monkeypatch) -> None:
    import base64
    from types import SimpleNamespace
    from analysis_report_exporter import assemble_session_analysis, _enrich_personal_questions
    from backend.config_workspace.sources import ConfigSourceService
    from backend.repositories.access import as_grading_repositories

    db, session_id, root = analysis_db
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE grading_sessions SET source_paper_sha256=? WHERE id=?", ("a" * 64, session_id))
    source = SimpleNamespace(
        sha256="a" * 64,
        private_blocks=({"question_id": "Q1", "question_html": "<p>计算(-3)^2。A.-9 B.9 C.6 D.-6</p><img src='example.png'>", "text": "计算(-3)^2。A.-9 B.9 C.6 D.-6", "analysis": "括号内的负数整体平方。"},),
        private_question_images={"Q1": {"question": base64.b64encode(b"synthetic-question-image").decode()}},
    )
    monkeypatch.setattr(ConfigSourceService, "__init__", lambda *a, **k: None)
    monkeypatch.setattr(ConfigSourceService, "load_active_record", lambda *a, **k: source)
    data = assemble_session_analysis(db, session_id, data_root=root)
    _enrich_personal_questions(as_grading_repositories(db), data, root)
    q1 = next(info for info in data.questions if info.question_id == "Q1")
    assert "A.-9 B.9" in q1.question_text
    assert q1.question_text.count("计算(-3)^2") == 1
    assert q1.reference_analysis == "括号内的负数整体平方。"
    assert q1.reference_images == [("question", b"synthetic-question-image")]
    source.sha256 = "b" * 64
    data = assemble_session_analysis(db, session_id, data_root=root)
    _enrich_personal_questions(as_grading_repositories(db), data, root)
    assert not data.questions[0].question_text
    assert not data.questions[0].reference_images


def test_class_scope_is_shared_by_personal_and_class_batch_exports(analysis_db) -> None:
    from analysis_report_exporter import assemble_session_analysis, split_session_analysis_by_class
    db, session_id, root = analysis_db
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE students SET class_name='2 班' WHERE name='李四'")
    data = assemble_session_analysis(db, session_id, data_root=root)
    groups = split_session_analysis_by_class(data)
    assert [(name, group.present, group.roster_absent, group.stats["avg"])
            for name, group in groups.items()] == [("1 班", 1, 1, 90), ("2 班", 1, 0, 50)]
    assert [s.rank for group in groups.values() for s in group.students] == [1, 1]
    assert groups["1 班"].questions[0].class_avg == 60
    assert groups["2 班"].questions[0].class_avg == 30
    assert [s.rank for s in data.students] == [1, 2]  # 分班不污染原场次对象。
    student = groups["2 班"].students[0]
    fake = FakeLLMClient()
    generator = _make_generator(db, root / "scoped", root / "cache", fake)
    archive = generator.export_session(session_id, "personal_analysis_html", student_ids={student.student_id})
    with zipfile.ZipFile(archive) as package:
        reports = [name for name in package.namelist() if name.endswith(".html")]
        assert len(reports) == 1
        assert "李四" in reports[0]
    payload = json.loads(fake.requests[0]["prompt"].split("输入 JSON：\n", 1)[1])
    assert payload["student"]["class_stats"]["avg"] == 50
    assert payload["student"]["class_stats"]["rank"] == 1
    fake._narrative = CLASS_NARRATIVE
    files = generator.export_classes(session_id)
    assert len(files) == 2
    prompts = [json.loads(request["prompt"].split("输入 JSON：\n", 1)[1]) for request in fake.requests[1:]]
    assert [(p["exam"]["class_name"], p["score_distribution"]["avg"], len(p["students"]))
            for p in prompts] == [("1 班", 90, 1), ("2 班", 50, 1)]
    assert "李四" not in files[0].read_text(encoding="utf-8")
    assert "张三" not in files[1].read_text(encoding="utf-8")
    generator.export_classes(session_id)
    assert fake.calls == 3  # 个人一次、两个班各一次；再次导出使用相同分班缓存。


@pytest.mark.parametrize("full_score", [100, 150])
def test_score_bands_are_disjoint_at_every_boundary(full_score) -> None:
    from analysis_report_exporter import _score_distribution
    stats = _score_distribution([value * full_score / 100 for value in (0, 40, 60, 70, 85, 100)], full_score)
    assert [band["count"] for band in stats["bands"]] == [2, 1, 1, 1, 1]
    assert sum(band["count"] for band in stats["bands"]) == 6


def test_scored_subparts_do_not_leave_unscored_parent_rows(analysis_db) -> None:
    from analysis_report_exporter import assemble_session_analysis, build_class_page_data
    db, session_id, root = analysis_db
    rubric_path = root / "config" / "uploaded" / "rubric.json"
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric["questions"][1]["parts"] = [
        {"part_id": "Q2(P1)", "part_score": 20}, {"part_id": "Q2(P2)", "part_score": 20},
    ]
    rubric_path.write_text(json.dumps(rubric, ensure_ascii=False), encoding="utf-8")
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE session_details SET question_id='Q2(P1)', score_awarded=score_awarded/2 WHERE question_id='Q2'")
        conn.execute("INSERT INTO session_details (result_id, question_id, score_awarded, knowledge_ids) SELECT result_id, 'Q2(P2)', score_awarded, knowledge_ids FROM session_details WHERE question_id='Q2(P1)'")
    data = assemble_session_analysis(db, session_id, data_root=root)
    page = build_class_page_data(data)
    assert [q["question_id"] for q in page["questions"]] == ["Q1", "Q2(P1)", "Q2(P2)"]
    assert sum(q["max_score"] for q in page["questions"]) == 100
    assert all(q["class_avg"] is not None for q in page["questions"])


def test_report_reads_objective_answer_and_deduplicates_legacy_evidence() -> None:
    from analysis_report_exporter import _student_answer_map
    assert _student_answer_map({"detail_metadata": {
        "Q1": {"recognized_answer": "B"},
        "Q2": {"raw_answer": "1.3"},
        "Q3(P1)": {"observed_answer": "AB=AC AB=AC", "evidence_steps": ["AB=AC"]},
    }}) == {"Q1": "B", "Q2": "1.3", "Q3(P1)": "AB=AC"}


def test_class_narrative_maps_aliases_adjacent_to_chinese_without_changing_ids(analysis_db) -> None:
    from analysis_report_exporter import assemble_session_analysis, class_narrative_with_student_names
    db, session_id, root = analysis_db
    data = assemble_session_analysis(db, session_id, data_root=root)
    narrative = {"common_issues": [{"evidence": "S1、S2等学生在计算中失分；S1只写出结论。"}],
                 "student_notes": [{"alias": "S2", "note": "可与S1讨论。"}],
                 "grouping_advice": "让S1与S2分别展示。"}
    mapped = class_narrative_with_student_names(narrative, data.students)
    assert mapped["common_issues"][0]["evidence"] == "张三、李四等学生在计算中失分；张三只写出结论。"
    assert mapped["student_notes"][0]["alias"] == "S2"
    assert mapped["student_notes"][0]["note"] == "可与张三讨论。"
    assert mapped["grouping_advice"] == "让张三与李四分别展示。"
    assert narrative["grouping_advice"] == "让S1与S2分别展示。"


@pytest.mark.parametrize("original_has_figure", [False, True])
def test_docx_figure_recovery_excludes_solution_figures(original_has_figure) -> None:
    import io
    from types import SimpleNamespace
    from docx import Document
    from PIL import Image
    from analysis_report_exporter import _docx_question_figures
    pictures = []
    for color in ("blue", "red"):
        stream = io.BytesIO()
        Image.new("RGB", (50, 50), color).save(stream, format="PNG")
        pictures.append(stream.getvalue())
    stem = "如图，在三角形ABC中，AB等于AC，求边长。"
    document = Document()
    document.add_paragraph("12．" + stem)
    if original_has_figure:
        document.add_picture(io.BytesIO(pictures[0]))
    document.add_paragraph("参考答案与试题解析")
    document.add_paragraph("12．" + stem)
    document.add_picture(io.BytesIO(pictures[1] if original_has_figure else pictures[0]))
    document.add_paragraph("【分析】添加辅助线。")
    document.add_picture(io.BytesIO(pictures[1]))
    output = io.BytesIO()
    document.save(output)
    source = SimpleNamespace(suffix=".docx", private_source_bytes=output.getvalue(),
                             private_blocks=[{"question_id": "Q12", "question_text": stem}])
    assert _docx_question_figures(source) == {"Q12": [pictures[0]]}


@pytest.mark.parametrize("region_ids", [("Q2(1)", "2(2)"), ("Q2", "2")])
def test_each_lost_part_has_its_own_answer_analysis_and_image(analysis_db, region_ids) -> None:
    from dataclasses import replace
    from analysis_report_exporter import (
        assemble_session_analysis, _render_personal_html,
        capture_lost_question_shots, _personal_image_inputs, _student_paper_context,
    )
    from backend.repositories.access import as_grading_repositories

    db, session_id, root = analysis_db
    _add_personal_report_scans(db, session_id, root)
    from PIL import Image
    with sqlite3.connect(db.db_path) as conn:
        image_path = conn.execute("SELECT front_image FROM exam_papers WHERE session_id=? ORDER BY id", (session_id,)).fetchone()[0]
    scan = Image.new("RGB", (400, 300), "white")
    scan.paste("red", (0, 0, 400, 150))
    scan.paste("blue", (0, 150, 400, 300))
    scan.save(image_path)
    data = assemble_session_analysis(db, session_id, data_root=root)
    student = data.students[0]
    base_record = next(r for r in student.records if r.question_id == "Q2")
    base_info = next(q for q in data.questions if q.question_id == "Q2")
    parts = [
        replace(base_record, question_id=f"Q2(P{i})", score=score, max_score=maximum, student_answer=f"第{i}问的作答")
        for i, score, maximum in ((1, 5, 10), (2, 3, 10), (3, 20, 20))
    ]
    student.records = [student.records[0], *parts]
    student.student_score = 88
    data.questions = [data.questions[0], *[
        replace(base_info, question_id=r.question_id, max_score=r.max_score, canonical_answer=f"第{i}问的标准答案")
        for i, r in enumerate(parts, 1)
    ]]
    narrative = {**PERSONAL_NARRATIVE, "question_analyses": [
        {"question_id": f"Q2(P{i})", "observation": f"第{i}问的证据", "possible_cause": f"Q2(P{i})的原因待确认", "evidence": "第一行缺少条件\n第二行有计算", "verification": "口述关键一步", "practice": "重做1道题", "evidence_level": "inferred"}
        for i in (1, 2)
    ]}
    repositories = as_grading_repositories(db)
    paper_context = _student_paper_context(repositories, data, student)
    regions = [
        {"mapped_question_id": region_id, "page": "front", "x": 0, "y": i * 150, "w": 400, "h": 150}
        for i, region_id in enumerate(region_ids)
    ]
    shots = capture_lost_question_shots(
        repositories, data, student, regions=regions, data_root=root,
        paper_context=paper_context,
    )
    assert {shot["region_question_id"] for shot in shots.values()} == set(region_ids)
    images, image_map = _personal_image_inputs(data, student, shots, paper_context, root)
    assert len(images) == 3
    if region_ids[0] == "Q2":
        assert image_map[0]["question_ids"] == ["Q2(P1)", "Q2(P2)", "Q2(P3)"]
        assert image_map[1]["question_ids"] == image_map[0]["question_ids"]
    else:
        assert image_map[0]["question_ids"] == ["Q2(P1)"]
        assert image_map[1]["question_ids"] == ["Q2(P2)"]
    rendered = _render_personal_html(data, student, narrative, shots)
    # 同一截图复用于方格面板/跟进卡/附录；两个失分小问各自一段详解。
    assert rendered.count('class="shot"') >= 2
    assert rendered.count('class="qpart"') == 2
    assert rendered.count("第1问的标准答案") == 1
    assert rendered.count("第2问的标准答案") == 1
    assert "第3问的标准答案" not in rendered
    assert "第2(1)题的原因待确认" in rendered
    assert "第2(2)题的原因待确认" in rendered
    # Stable question ids are also used by local graph links; visible copy
    # still uses the teacher-facing question labels.
    from bs4 import BeautifulSoup
    visible = BeautifulSoup(rendered, 'html.parser')
    for hidden in visible(['script', 'style']):
        hidden.decompose()
    assert "Q2(P" not in visible.get_text()
    assert "得 28 分 / 满分 40 分" in rendered
    assert "<p>第一行缺少条件</p><p>第二行有计算</p>" in rendered


# ---------------------------------------------------------------------------
# 个人学情报告留存：删除接口与重复提交复用
# ---------------------------------------------------------------------------


def _submit_personal_report(client, manager, session_id: int) -> dict:
    response = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={"report_type": "personal_analysis_html"},
    )
    assert response.status_code == 202
    job = response.json()
    manager.wait(job["id"], timeout=5)
    return job


def test_delete_retained_report_file_via_api(analysis_api_client) -> None:
    client, _db, session_id, reports_dir, manager, monkeypatch = analysis_api_client
    _patch_configured(monkeypatch, True)
    job = _submit_personal_report(client, manager, session_id)
    report_path = reports_dir / "report.zip"
    assert report_path.is_file()

    deleted = client.delete(f"/api/sessions/{session_id}/reports/{job['id']}/file")

    assert deleted.status_code == 200
    payload = deleted.json()
    assert payload == {
        "job_id": job["id"],
        "deleted": True,
        "freed_bytes": len(b"fake"),
    }
    assert not report_path.is_file()
    # 幂等：重复删除不再报错，deleted=false。
    again = client.delete(f"/api/sessions/{session_id}/reports/{job['id']}/file")
    assert again.status_code == 200
    assert again.json()["deleted"] is False
    assert again.json()["freed_bytes"] == 0
    # 登记簿中该文件回到「已过期，可重新生成」。
    context = client.get(f"/api/sessions/{session_id}/reports/context").json()
    entry = next(item for item in context["jobs"] if item["id"] == job["id"])
    assert entry["file_status"] == "expired"


def test_delete_retained_report_file_rejects_other_types(analysis_api_client) -> None:
    client, _db, session_id, reports_dir, manager, _m = analysis_api_client
    response = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={"report_type": "score_excel"},
    )
    assert response.status_code == 202
    job = response.json()
    manager.wait(job["id"], timeout=5)
    assert (reports_dir / "report.xlsx").is_file()

    deleted = client.delete(f"/api/sessions/{session_id}/reports/{job['id']}/file")

    assert deleted.status_code == 422
    assert deleted.json()["error"]["code"] == "report_file_not_retained"
    assert (reports_dir / "report.xlsx").is_file()


def test_delete_retained_report_file_rejects_cross_session(analysis_api_client) -> None:
    client, db, session_id, _reports_dir, manager, monkeypatch = analysis_api_client
    _patch_configured(monkeypatch, True)
    job = _submit_personal_report(client, manager, session_id)
    other_session = db.create_grading_session("另一场", "rubric.json", "answer.json")

    deleted = client.delete(f"/api/sessions/{other_session}/reports/{job['id']}/file")

    assert deleted.status_code == 404


def test_retained_report_resubmit_after_download_reuses_job(analysis_api_client) -> None:
    """留存文件下载后仍可 resolve：同 revision 再提交直接复用旧 job，不重新生成。"""
    client, _db, session_id, reports_dir, manager, monkeypatch = analysis_api_client
    _patch_configured(monkeypatch, True)
    job = _submit_personal_report(client, manager, session_id)
    download = client.get(f"/api/jobs/{job['id']}/download")
    assert download.status_code == 200
    assert (reports_dir / "report.zip").is_file()

    resubmitted = client.post(
        f"/api/sessions/{session_id}/reports/export",
        json={"report_type": "personal_analysis_html"},
    )

    assert resubmitted.status_code == 202
    assert resubmitted.json()["id"] == job["id"]


def test_personal_concurrency_keeps_out_of_order_images_results_and_cache_separate(
    analysis_db, tmp_path, monkeypatch,
):
    import threading
    import time
    from dataclasses import replace
    from types import SimpleNamespace
    import analysis_report_exporter as exporter

    db, session_id, root = analysis_db
    data = exporter.assemble_session_analysis(db, session_id, data_root=root)
    data.students = [replace(data.students[0], student_id=i, student_code=f"T{i}", student_name=f"合成{i}") for i in range(1, 7)]
    monkeypatch.setattr(exporter, "_enrich_personal_questions", lambda *_: None)
    monkeypatch.setattr(exporter, "load_session_regions", lambda *_, **__: [])
    monkeypatch.setattr(exporter, "_student_paper_context", lambda *_: {})
    monkeypatch.setattr(exporter, "capture_lost_question_shots", lambda *_, **__: {})
    monkeypatch.setattr(exporter, "_personal_image_inputs", lambda _data, student, *_: ([str(student.student_id).encode()], []))
    # Keep file output deterministic while measuring only the changed scheduling.
    monkeypatch.setattr(exporter, "_render_personal_html", lambda _data, student, narrative, _shots, **_kw: json.dumps({"student": student.student_id, "narrative": narrative}))

    class DelayedClient:
        def __init__(self, limit, fail=None):
            self.config_gateway = SimpleNamespace(execution_snapshot=SimpleNamespace(max_in_flight=limit))
            self.lock = threading.Lock()
            self.active = self.peak = 0
            self.calls = []
            self.completed = []
            self.fail = fail

        def json_from_images_once(self, prompt, images, **kwargs):
            assert kwargs["use_config_client"] is True
            sid = int(images[0])
            with self.lock:
                self.calls.append(sid)
                self.active += 1
                self.peak = max(self.peak, self.active)
            time.sleep({1: .24, 2: .06, 3: .12, 4: .18, 5: .06, 6: .12}[sid])
            with self.lock:
                self.active -= 1
                self.completed.append(sid)
            if sid == self.fail:
                raise TimeoutError("synthetic failure")
            return {"overall_comment": f"分析-{sid}"}

    def export(client, name, revision="same-input"):
        out = tmp_path / name
        out.mkdir(exist_ok=True)
        generator = _make_generator(db, out, tmp_path / f"cache-{name}", client)
        started = time.perf_counter()
        path = generator._export_personal(data, revision)
        elapsed = time.perf_counter() - started
        with zipfile.ZipFile(path) as archive:
            for sid in range(1, 7):
                report = json.loads(archive.read(f"T{sid}_合成{sid}_个人报告.html"))
                assert report["student"] == sid
                expected = None if sid == client.fail else {"overall_comment": f"分析-{sid}"}
                assert report["narrative"] == expected
        return elapsed

    export(DelayedClient(1), "warmup")  # load shared runtime code before either timed run
    serial = DelayedClient(1)
    serial_seconds = export(serial, "serial")
    concurrent = DelayedClient(20)
    concurrent_seconds = export(concurrent, "concurrent")
    assert serial.peak == 1 and concurrent.peak == 3
    assert concurrent.completed != concurrent.calls
    assert sorted(concurrent.calls) == list(range(1, 7))
    assert concurrent_seconds < serial_seconds * .7
    print(f"REPORT_TIMING six identical synthetic inputs: serial={serial_seconds:.3f}s concurrent={concurrent_seconds:.3f}s")
    export(concurrent, "concurrent")
    assert len(concurrent.calls) == 6  # all cache hits on re-entry

    partial = DelayedClient(2, fail=2)
    export(partial, "partial")
    assert partial.peak == 2 and len(partial.calls) == 6
    export(partial, "partial")
    assert partial.calls.count(2) == 2 and len(partial.calls) == 7
    assert len(list((tmp_path / "cache-partial").glob("*.json"))) == 5
    assert not list((tmp_path / "cache-partial").glob("*.tmp"))

    def unavailable_factory():
        raise AssertionError("cached reports must not initialize a model client")

    cached = exporter.AnalysisReportGenerator(db, tmp_path / "concurrent", narrative_cache_dir=tmp_path / "cache-concurrent",
                                              llm_client_factory=unavailable_factory, data_root=root)
    assert cached._export_personal(data, "same-input").is_file()


def test_personal_report_shows_error_classification_and_recurrence(
    analysis_db, tmp_path: Path
) -> None:
    """P1：已整理的失分题显示错误归类与跨场次同类错误徽章。"""
    from analysis_report_exporter import (
        assemble_session_analysis,
        _render_personal_html,
    )

    db, session_id, root = analysis_db
    data = assemble_session_analysis(db, session_id, data_root=root)
    lisi = next(s for s in data.students if s.student_name == "李四")
    error_map = {
        "Q1": [{"category": "审题与条件", "pattern": "选项看错", "kind": "error",
                "manifestation": "误选 C", "step_id": None, "pattern_status": "candidate",
                "score": 30.0, "max_score": 60.0, "lost_points": 30.0}],
        "Q2": [{"category": "过程与依据", "pattern": "缺少关键依据", "kind": "process",
                "manifestation": "未写依据", "step_id": None, "pattern_status": "existing",
                "score": 20.0, "max_score": 40.0, "lost_points": 20.0}],
    }
    error_history = {
        "categories": {"审题与条件": ["第2周测验"], "过程与依据": ["第2周测验"]},
        "patterns": {"选项看错": ["第2周测验"]},
    }

    # 无 AI 叙述的降级卡片：按题显示错误归类，附同类错误复发徽章。
    html = _render_personal_html(
        data, lisi, None, {},
        error_map=error_map, error_history=error_history,
    )
    assert "错误归类（AI 辅助）" in html
    assert "审题与条件" in html and "选项看错" in html
    assert "过程与依据" in html and "缺少关键依据" in html
    assert "同类错误以前出现过" in html
    assert "第2周测验" in html

    # 有 AI 叙述时：跟进卡片题号标签带大类，附录照常显示归类。
    html_with_ai = _render_personal_html(
        data, lisi, PERSONAL_NARRATIVE, {},
        error_map=error_map, error_history=error_history,
    )
    assert "过程与依据" in html_with_ai
    assert "错误归类" in html_with_ai
