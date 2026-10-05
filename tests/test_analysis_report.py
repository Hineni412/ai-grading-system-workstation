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
        {
            "title": "证明题得分率低",
            "detail": "第 2 题全班得分率不足一半",
            "severity": "high",
        }
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
from backend.repositories.grading_database import open_grading_repositories


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
        self.requests.append(
            {"prompt": prompt, "images": images, "options": extra_kwargs, **kwargs}
        )
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
                        COMPLETE_RAW_JSON,
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


@pytest.fixture
def analysis_db(tmp_path: Path):
    

    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    session_id = _seed_analysis_session(db, tmp_path)
    return db, session_id, tmp_path


def _make_generator(db, out_dir: Path, cache_dir: Path, llm_client) -> "object":
    from backend.reporting.analysis_report_exporter import AnalysisReportGenerator

    return AnalysisReportGenerator(
        db,
        out_dir,
        llm_client_factory=lambda: llm_client,
        narrative_cache_dir=cache_dir,
        data_root=out_dir.parent,
    )


def test_llm_failure_degrades_to_data_only_report(analysis_db, tmp_path: Path) -> None:
    from backend.llm.llm_client import LLMResponseFormatError

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
    

    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
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


# ---------------------------------------------------------------------------
# 报告内容口径：题型中译、批改记录清洗、标准答案收敛、学生作答回填
# ---------------------------------------------------------------------------


def _add_personal_report_scans(db, session_id: int, root: Path) -> list[dict]:
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


def _score_state(db_path: Path) -> list[list[tuple]]:
    with sqlite3.connect(db_path) as conn:
        return [
            conn.execute(sql).fetchall()
            for sql in (
                "SELECT * FROM session_results ORDER BY id",
                "SELECT * FROM session_details ORDER BY id",
                "SELECT * FROM teacher_score_locks ORDER BY id",
            )
        ]


@pytest.mark.parametrize("manual_only", [True, False])
def test_visual_reports_include_manual_students_and_keep_final_scores(
    analysis_db,
    tmp_path: Path,
    manual_only: bool,
) -> None:
    import io
    from PIL import Image
    from backend.session_analysis import assemble_session_analysis

    db, session_id, root = analysis_db
    papers = _add_personal_report_scans(db, session_id, root)
    selected = papers if manual_only else papers[1:]
    with sqlite3.connect(db.db_path) as conn:
        for paper in selected:
            student_id = paper["student_id"]
            conn.execute(
                "DELETE FROM session_details WHERE result_id IN (SELECT id FROM session_results WHERE student_id=?)",
                (student_id,),
            )
            conn.execute(
                "DELETE FROM session_results WHERE student_id=?", (student_id,)
            )
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
    output = generator.export_session(
        session_id, "personal_analysis_html", score_revision="manual-v1"
    )
    assert _score_state(db.db_path) == before
    assert client.calls == 2
    with zipfile.ZipFile(output) as archive:
        for student, request in zip(data.students, client.requests, strict=True):
            payload = json.loads(request["prompt"].split("输入 JSON：\n", 1)[1])
            assert payload["student"]["total_score"] == student.student_score
            assert request["use_config_client"] is True
            assert len(request["images"]) == len(payload["image_map"]) >= 2
            assert [item["image_number"] for item in payload["image_map"]] == list(
                range(1, len(request["images"]) + 1)
            )
            assert any(
                item["kind"] == "student_overview" for item in payload["image_map"]
            )
            # Different solid colours ensure the report receives its own student's image.
            with Image.open(io.BytesIO(request["images"][0])) as image:
                red, _, blue = image.convert("RGB").getpixel((0, 0))
            assert (red > blue) == (student.student_id == papers[0]["student_id"])
            if student.result_id < 0:
                assert student.student_score == 55
                assert all(
                    q["grading_record"]["teacher_confirmed"]
                    for q in payload["questions"]
                    if q["lost"]
                )
            text = archive.read(
                f"{student.student_code}_{student.student_name}_个人报告.html"
            ).decode("utf-8")
            assert "原卷截图" in text
            assert "未取得该生可读取的答卷图片" not in text
    # Re-entering export reuses the existing narrative; it does not regrade or call twice.
    generator.export_session(
        session_id, "personal_analysis_html", score_revision="manual-v1"
    )
    assert client.calls == 2
    assert _score_state(db.db_path) == before


# ---------------------------------------------------------------------------
# 个人学情报告留存：删除接口与重复提交复用
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("legacy_version", ["personal_analysis_html_v11_parent_brief", "personal_analysis_html_v10_actions", "personal_analysis_html_v9_problem_refs", "personal_analysis_html_v8_parts"])
def test_knowledge_prompt_upgrade_keeps_old_cache_but_generates_current_narrative(analysis_db, tmp_path, legacy_version):
    from backend.reporting.analysis_report_exporter import AnalysisNarrativeCache
    from backend.session_analysis import assemble_session_analysis

    db, session_id, root = analysis_db
    cache = AnalysisNarrativeCache(tmp_path / "cache")
    data = assemble_session_analysis(db, session_id, data_root=root)
    for student in data.students:
        cache.store(cache.cache_key(session_id=session_id, score_revision="existing-v1",
            rendition_version=legacy_version, report_key=f"personal:{student.student_id}"), PERSONAL_NARRATIVE)
    files_before = sorted(p.name for p in cache.cache_dir.glob("*.json"))
    client = FakeLLMClient()
    generator = _make_generator(db, tmp_path / "out", cache.cache_dir, client)
    output = generator.export_session(session_id, "personal_analysis_html", score_revision="existing-v1", html_only=True)
    assert client.calls == len(data.students)

    assert set(files_before) < {p.name for p in cache.cache_dir.glob("*.json")}
    assert output.is_dir()
    assert len(list(output.glob("*.html"))) == len(data.students)
    assert all(path.suffix == ".html" for path in output.iterdir())
    assert not list((tmp_path / "out").rglob("*.zip"))
    assert not list((tmp_path / "out").rglob("*.pdf"))
    for request in client.requests:
        assert "knowledge_focus" in request["prompt"]
    report = (output / "001_张三_个人报告.html").read_text(encoding="utf-8")
    assert "先做：" in report and "检查：" in report
    assert '<span class="when">本周</span>' in report
    generator.export_session(session_id, "personal_analysis_html", score_revision="existing-v1", html_only=True)
    assert client.calls == len(data.students)

def test_personal_report_revision_keeps_peers_current_and_reads_stale_text(analysis_db, tmp_path):
    from backend.reporting.analysis_report_exporter import AnalysisReportGenerator
    from backend.personal_reports import personal_report_states, render_personal_report
    db, sid, root = analysis_db
    with sqlite3.connect(db.db_path) as conn:
        manual_id = conn.execute("INSERT INTO students(student_code,name,class_name) VALUES ('004','合成纯人工','1 班')").lastrowid
        conn.execute("INSERT INTO session_attendance(session_id,student_id,attendance_status) VALUES (?,?,'present')", (sid, manual_id))
        for qid, score, maximum in [('Q1', 50, 60), ('Q2', 30, 40)]:
            conn.execute("""INSERT INTO teacher_score_locks(session_id,scan_batch_id,student_id,question_id,score_awarded,max_score,deduction_reason,source_target_type,source_target_id)
                VALUES (?,0,?,?,?,?,?,'manual',1)""", (sid, manual_id, qid, score, maximum, '合成批语'))
    reports = root / "reports"
    client = FakeLLMClient()
    generator = AnalysisReportGenerator(db, root / "test-generated", data_root=root, reports_dir=reports,
        narrative_cache_dir=reports / ".analysis_narrative_cache", llm_client_factory=lambda: client)
    generator.export_session(sid, "personal_analysis_html", html_only=True)
    states = personal_report_states(db, sid, reports)["students"]
    current = [s["student_id"] for s in states if s["status"] == "current"]
    assert len(current) == 3
    assert any(s["status"] == "unavailable" for s in states)
    files_before = {p.relative_to(reports): p.read_bytes() for p in reports.rglob("*") if p.is_file()}
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE session_details SET score_awarded=score_awarded-1 WHERE result_id IN (SELECT id FROM session_results WHERE student_id=?) AND question_id='Q2'", (current[0],))
    states = {s["student_id"]: s["status"] for s in personal_report_states(db, sid, reports)["students"]}
    assert states[current[0]] == "stale"
    assert all(states[student_id] == "current" for student_id in current[1:])
    html = render_personal_report(db, sid, current[0], reports, review_links=True)
    assert "选择题全对，基础扎实" in html
    assert 'class="review-link"' in html
    assert files_before == {p.relative_to(reports): p.read_bytes() for p in reports.rglob("*") if p.is_file()}
    assert client.calls == 3


def test_personal_cache_only_and_legacy_lookup_do_not_write_or_create_client(analysis_db, tmp_path):
    from backend.reporting.analysis_report_exporter import AnalysisNarrativeCache, AnalysisReportGenerator
    from backend.report_exports import score_revision, report_narrative_version
    from backend.personal_reports import personal_report_states
    db, sid, root = analysis_db
    data = __import__('backend.session_analysis', fromlist=['assemble_session_analysis']).assemble_session_analysis(db, sid)
    cache = AnalysisNarrativeCache(root / "reports" / ".analysis_narrative_cache")
    legacy_key = cache.cache_key(session_id=sid, score_revision=score_revision(db, sid),
        rendition_version=report_narrative_version("personal_analysis_html"), report_key=f"personal:{data.students[0].student_id}")
    cache.store(legacy_key, PERSONAL_NARRATIVE)
    def forbidden():
        raise AssertionError("只读导出不能初始化模型")
    generator = AnalysisReportGenerator(db, root / "test-bundle", data_root=root, reports_dir=root / "reports",
        narrative_cache_dir=cache.cache_dir, llm_client_factory=forbidden)
    before = cache._path(legacy_key).read_bytes()
    generator.export_session(sid, "personal_analysis_html", narrative_mode="cache_only", html_only=True)
    assert generator.last_personal_summary == dict(generated=1, failed=0, skipped=2)
    assert {s["reason"] for s in generator.last_personal_missing} >= {"未生成", "缺考"}
    assert cache._path(legacy_key).read_bytes() == before
    assert not (cache.cache_dir / "personal_index").exists()
    assert not (root / "reports" / ".analysis_review_notes").exists()
    assert personal_report_states(db, sid, root / "reports")["students"][0]["status"] == "current"


def test_personal_online_and_offline_images_share_content_and_released_notice(analysis_db, monkeypatch):
    import re
    from backend.personal_reports import render_personal_report, RELEASED_SHOT_NOTE
    db, sid, root = analysis_db
    papers = _add_personal_report_scans(db, sid, root)
    student_id = papers[0]["student_id"]
    from unittest.mock import Mock
    from backend.session_analysis import enrich_personal_knowledge
    prepare = Mock(wraps=enrich_personal_knowledge)
    monkeypatch.setattr('backend.session_analysis.enrich_personal_knowledge', prepare)
    online = render_personal_report(db, sid, student_id, root / "reports", narrative_mode="none")
    offline = render_personal_report(db, sid, student_id, root / "reports", narrative_mode="none", online=False)
    assert f'/personal-reports/{student_id}/shots/Q2' in online
    assert 'data:image/jpeg' not in online
    assert 'data:image/jpeg' in offline
    assert 'class="review-link"' not in online
    from backend.reporting.analysis_report_exporter import _PERSONAL_KEYBOARD_JS
    assert 'personal-report:key' in online and 'personal-report:key' not in offline
    normalize = lambda html: re.sub(r'src="(?:/api/[^\"]+|data:image/jpeg[^\"]+)"', 'src="SHOT"', html.replace(_PERSONAL_KEYBOARD_JS, ''))
    assert normalize(online) == normalize(offline)
    assert prepare.call_count == 1  # 同场连续读取只准备一次，在线与离线结果一致。
    from backend.personal_reports import personal_render_context
    from dataclasses import asdict
    before = asdict(personal_render_context(db, sid, root / 'reports')['data'])
    render_personal_report(db, sid, papers[1]['student_id'], root / 'reports', narrative_mode='none')
    assert asdict(personal_render_context(db, sid, root / 'reports')['data']) == before
    monkeypatch.setattr("backend.files.session_originals.originals_state", lambda *_a: "cleared")
    for mode in (True, False):
        html = render_personal_report(db, sid, student_id, root / "reports", narrative_mode="none", online=mode)
        assert RELEASED_SHOT_NOTE in html
        assert '/shots/' not in html and 'data:image/jpeg' not in html


@pytest.mark.parametrize("change", ["teacher", "bank"])
def test_personal_report_teacher_and_bank_changes_invalidate_input(analysis_db, monkeypatch, change):
    from backend.personal_reports import student_report_revision
    db, sid, _root = analysis_db
    student = db.results.get_session_results(sid)[0]
    student_id = student["student_id"]
    before = student_report_revision(db, sid, student_id)
    if change == "bank":
        monkeypatch.setattr("backend.personal_reports._question_bank_report_source", lambda *_a: {"links": [{"revision": "changed"}]})
    else:
        with sqlite3.connect(db.db_path) as conn:
            conn.execute("""INSERT INTO teacher_score_locks(session_id,scan_batch_id,student_id,question_id,score_awarded,max_score,deduction_reason,source_target_type,source_target_id)
                VALUES (?,'test-personal',?,'Q2',29,40,'教师补充批语','exam_paper',?)""", (sid, student_id, db.results.get_result_context(student["result_id"])["paper_id"]))
    assert student_report_revision(db, sid, student_id) != before


def test_personal_index_corruption_and_caller_thread_writes(analysis_db, monkeypatch):
    import threading
    import backend.personal_reports as personal
    from backend.reporting.analysis_report_exporter import AnalysisReportGenerator
    db, sid, root = analysis_db
    owner = threading.get_ident()
    original = personal.publish_personal_index
    writes = []
    def record(*args):
        writes.append(threading.get_ident())
        return original(*args)
    monkeypatch.setattr(personal, "publish_personal_index", record)
    generator = AnalysisReportGenerator(db, root / "test-index", data_root=root,
        narrative_cache_dir=root / "reports" / ".analysis_narrative_cache", llm_client_factory=lambda: FakeLLMClient())
    generator.export_session(sid, "personal_analysis_html", html_only=True)
    assert writes == [owner, owner]
    index = root / "reports" / ".analysis_narrative_cache" / "personal_index" / f"session_{sid}.json"
    index.write_text("[invalid", encoding="utf-8")
    assert personal.read_personal_index(index.parent.parent, sid) == {}
    assert sum(s["status"] == "current" for s in personal.personal_report_states(db, sid, root / "reports")["students"]) == 2
