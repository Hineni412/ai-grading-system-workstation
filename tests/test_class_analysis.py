"""班级分析内嵌页：GET 三态、settings 持久化、regenerate 防重、job 与自动触发。"""

from __future__ import annotations

import json
import sqlite3
import threading
import warnings
from pathlib import Path

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

import pytest
from fastapi.testclient import TestClient

from tests.test_analysis_report import (
    CLASS_NARRATIVE,
    FakeLLMClient,
    _patch_configured,
    _seed_analysis_session,
)


class BlockingLLMClient(FakeLLMClient):
    """json_from_text 阻塞到 release，用于构造 generating 中间态。"""

    def __init__(self) -> None:
        super().__init__(narrative=CLASS_NARRATIVE)
        self.started = threading.Event()
        self.release = threading.Event()

    def json_from_text(self, prompt, extra_kwargs=None, **kwargs):
        self.started.set()
        assert self.release.wait(3)
        return super().json_from_text(prompt, extra_kwargs=extra_kwargs, **kwargs)


@pytest.fixture
def class_analysis_api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_grading_db,
        get_job_manager,
        get_reports_dir,
        get_upload_config_dir,
    )
    from backend.jobs.default_handlers import _build_class_analysis_generate_handler
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from db_manager import DBManager

    db = DBManager(tmp_path / "databases" / "grading.db")
    db.initialize()
    session_id = _seed_analysis_session(db, tmp_path)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    llm_holder = {"client": FakeLLMClient(narrative=CLASS_NARRATIVE)}
    manager.register(
        "class_analysis_generate",
        _build_class_analysis_generate_handler(
            db_path=db.db_path,
            reports_dir=reports_dir,
            data_root=tmp_path,
            llm_client_factory=lambda: llm_holder["client"],
        ),
    )
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_reports_dir] = lambda: reports_dir
    app.dependency_overrides[get_upload_config_dir] = lambda: tmp_path / "config" / "uploaded"
    with TestClient(app) as client:
        try:
            yield client, db, session_id, reports_dir, manager, llm_holder, monkeypatch
        finally:
            manager.shutdown()


def _generate_via_api(client, session_id: int) -> dict:
    response = client.post(f"/api/sessions/{session_id}/class-analysis/regenerate")
    assert response.status_code == 202
    return response.json()


def test_cause_groups_persist_count_students_per_class_and_update_only_changed_questions(class_analysis_api_client):
    from backend.class_analysis import ClassAnalysisStateStore
    client, db, sid, reports_dir, manager, holder, monkeypatch = class_analysis_api_client
    _patch_configured(monkeypatch, True)

    class CauseClient:
        def __init__(self):
            self.calls = []

        def json_from_text(self, prompt, **kwargs):
            source = json.loads(prompt.rsplit("\n", 1)[1])
            self.calls.append(source)
            return {"groups": [{"kind": "process", "category": "过程与依据", "reason": "缺少直角依据", "manifestation": "未写明直角条件就使用勾股定理", "evidence_ids":
                                [item["id"] for item in source["evidence"]]}]}

    fake = CauseClient()
    holder["client"] = fake
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE students SET class_name='2 班' WHERE name='李四'")
    job = client.post(f"/api/sessions/{sid}/class-analysis/regenerate?kind=causes").json()
    manager.wait(job["id"], timeout=5)
    assert manager.get(job["id"]).status == "succeeded"
    assert len(fake.calls) == 2
    assert all("student_name" not in json.dumps(source) for source in fake.calls)
    assert all(source["rubric"] for source in fake.calls)
    merged = client.get(f"/api/sessions/{sid}/class-analysis?view=summary&class_name=").json()
    assert merged["cause_analysis"]["status"] == "ready"
    q2 = next(q for q in merged["data"]["questions"] if q["question_id"] == "Q2")
    assert q2["causes"][0]["count"] == 2
    assert len({sid for item in q2["causes"][0]["evidence"] for sid in item["student_ids"]}) == 2
    for name in ("1 班", "2 班"):
        payload = client.get(f"/api/sessions/{sid}/class-analysis", params={"view": "summary", "class_name": name}).json()
        scoped = next(q for q in payload["data"]["questions"] if q["question_id"] == "Q2")
        assert scoped["causes"][0]["reason"] == q2["causes"][0]["reason"]
        assert scoped["causes"][0]["count"] == 1
    assert ClassAnalysisStateStore(reports_dir).load(sid)["cause_analysis"]["questions"]["Q2"]["result"]
    assert ClassAnalysisStateStore(reports_dir).load(sid)["narrative"] is None
    # 普通读取、切班、再次点击整理都复用；改批语只重整受影响题目。
    job = client.post(f"/api/sessions/{sid}/class-analysis/regenerate?kind=causes").json()
    manager.wait(job["id"], timeout=5)
    assert len(fake.calls) == 2
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE session_details SET deduction_reason='复核后：计算错误' WHERE question_id='Q2'")
    changed = client.get(f"/api/sessions/{sid}/class-analysis?view=summary&class_name=").json()
    assert changed["cause_analysis"]["stale"] is True
    assert changed["cause_analysis"]["pending_questions"] == 1
    assert not next(q for q in changed["data"]["questions"] if q["question_id"] == "Q2").get("causes_grouped")
    job = client.post(f"/api/sessions/{sid}/class-analysis/regenerate?kind=causes").json()
    manager.wait(job["id"], timeout=5)
    assert len(fake.calls) == 3
    # 图像编码不进入文本归并请求；实际评分要求变化会使该题归并过期。
    rubric_path = Path(db.get_grading_session(sid)["rubric_path"])
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric["questions"][1]["question_image_base64"] = "synthetic-image-payload"
    rubric["questions"][1]["proof_obligations"] = ["必须说明直角条件"]
    rubric_path.write_text(json.dumps(rubric, ensure_ascii=False), encoding="utf-8")
    changed = client.get(f"/api/sessions/{sid}/class-analysis?view=summary").json()
    assert changed["cause_analysis"]["pending_questions"] == 1
    job = client.post(f"/api/sessions/{sid}/class-analysis/regenerate?kind=causes").json()
    manager.wait(job["id"], timeout=5)
    assert len(fake.calls) == 4
    assert "synthetic-image-payload" not in json.dumps(fake.calls[-1])


def test_cause_classification_preserves_positive_uncertain_and_multiple_errors(tmp_path):
    from copy import deepcopy
    from analysis_report_exporter import assemble_session_analysis, build_class_page_data
    from backend.class_analysis import build_cause_inputs, save_cause_result, ClassAnalysisStateStore, apply_cause_results
    from db_manager import DBManager
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    sid = _seed_analysis_session(db, tmp_path)
    data = assemble_session_analysis(db, sid, data_root=tmp_path, page_only=True)
    first = next(r for r in data.students[0].records if r.question_id == "Q2")
    second = next(r for r in data.students[1].records if r.question_id == "Q2")
    first.deduction_reason, first.error_summary, first.error_category = "未写直角；计算错误", "", ""
    second.deduction_reason, second.error_summary, second.error_category = "方程及求解正确", "", ""
    sources = build_cause_inputs(data)
    source = next(s for s in sources if s["question_id"] == "Q2")
    positive = next(e["id"] for e in source["evidence"] if "正确" in e["text"])
    negative = next(e["id"] for e in source["evidence"] if "未写" in e["text"])
    store = ClassAnalysisStateStore(tmp_path / "reports")
    save_cause_result(store, sid, source, {"groups": [
        {"kind": "process", "category": "过程与依据", "reason": "缺少直角依据", "manifestation": "未写直角", "evidence_ids": [negative, negative]},
        {"kind": "error", "category": "计算与化简", "reason": "计算错误", "manifestation": "列式后计算错误", "evidence_ids": [negative]},
    ], "positive_ids": [positive]}, origin="assistant")
    page = build_class_page_data(data, compact=True)
    apply_cause_results(page, data, sources, store.load(sid))
    q2 = next(q for q in page["questions"] if q["question_id"] == "Q2")
    assert [c["count"] for c in q2["causes"]] == [1, 1]
    assert "正确" in q2["cause_review"]["positive"][0]["text"]
    assert q2["cause_review"]["uncertain"] == []
    # 未覆盖的批语不会消失，也不自动被当作错误。
    save_cause_result(store, sid, source, {"groups": [], "positive_ids": [positive]})
    page = build_class_page_data(data, compact=True)
    apply_cause_results(page, data, sources, store.load(sid))
    assert len(next(q for q in page["questions"] if q["question_id"] == "Q2")["cause_review"]["uncertain"]) == 1
    invalid = deepcopy(source)
    with pytest.raises(ValueError, match="reference"):
        save_cause_result(store, sid, invalid, {"groups": [{"kind": "error", "category": "计算与化简", "reason": "虚构", "manifestation": "虚构", "evidence_ids": ["E999"]}]})


def test_cause_model_failure_does_not_retry_or_replace_original_reasons(class_analysis_api_client):
    client, _db, sid, _reports, manager, holder, monkeypatch = class_analysis_api_client
    _patch_configured(monkeypatch, True)
    fake = FakeLLMClient(error=TimeoutError("synthetic timeout"))
    holder["client"] = fake
    job = client.post(f"/api/sessions/{sid}/class-analysis/regenerate?kind=causes").json()
    manager.wait(job["id"], timeout=5)
    assert fake.calls == 2  # 两题各一次，失败不重发。
    payload = client.get(f"/api/sessions/{sid}/class-analysis?view=summary").json()
    assert payload["cause_analysis"]["failed_questions"] == 2
    assert payload["cause_analysis"]["pending_questions"] == 2
    assert payload["data"]["questions"][0]["causes"]


def test_cause_answer_context_survives_reentry_and_invalidates_without_feedback_change(class_analysis_api_client):
    from backend.class_analysis import ClassAnalysisStateStore
    client, db, sid, reports_dir, manager, holder, monkeypatch = class_analysis_api_client
    _patch_configured(monkeypatch, True)
    with sqlite3.connect(db.db_path) as conn:
        result_ids = [row[0] for row in conn.execute("SELECT id FROM session_results WHERE session_id=? ORDER BY id", (sid,))]
        for result_id, answer in zip(result_ids, ("设边长x，化到12x=28，未继续", "设边长x，算得x=2")):
            raw = {"grading_completeness": {"status": "complete"}, "detail_metadata": {
                "Q2": {"observed_answer": answer, "evidence_steps": ["已列方程"], "missing_steps": []}}}
            conn.execute("UPDATE session_results SET raw_json=? WHERE id=?", (json.dumps(raw, ensure_ascii=False), result_id))
        conn.execute("UPDATE session_details SET deduction_reason='求解有误', error_summary='', error_category='' WHERE question_id='Q2'")
    session = db.get_grading_session(sid)
    answer_path = Path(session["answer_key_path"])
    answer_key = json.loads(answer_path.read_text(encoding="utf-8"))
    answer_key["questions"][1]["analysis"] = "列出12x=28，解得x=7/3。"
    answer_path.write_text(json.dumps(answer_key, ensure_ascii=False), encoding="utf-8")
    rubric_path = Path(session["rubric_path"])
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric["questions"][1].update({"question_text": "合成题：求线段长度。", "parts": [{"part_id": "Q2", "part_score": 40,
        "steps": [{"step_id": "S1", "step_score": 40, "core_goal": "列式求解", "required_elements": ["12x=28"],
                   "question_image_base64": "not-for-text-request"}]}]})
    rubric_path.write_text(json.dumps(rubric, ensure_ascii=False), encoding="utf-8")

    class CauseClient:
        calls = []

        def json_from_text(self, prompt, **kwargs):
            source = json.loads(prompt.rsplit("\n", 1)[1])
            self.calls.append(source)
            groups = []
            for item in source["evidence"]:
                unfinished = "未继续" in item["student_answer"]
                groups.append({"kind": "process" if unfinished else "error",
                               "category": "过程与依据" if unfinished else "计算与化简",
                               "reason": "未完成求解" if unfinished else "计算错误",
                               "manifestation": "停在方程" if unfinished else "求值错误", "evidence_ids": [item["id"]]})
            return {"groups": groups}

    fake = CauseClient()
    holder["client"] = fake
    def generate():
        job = client.post(f"/api/sessions/{sid}/class-analysis/regenerate?kind=causes").json()
        manager.wait(job["id"], timeout=5)
        assert manager.get(job["id"]).status == "succeeded"

    generate()
    source = next(s for s in fake.calls if s["question_id"] == "Q2")
    assert len(source["evidence"]) == 2  # 相同批语、不同作答，不提前合并。
    assert source["reference_analysis"] == "列出12x=28，解得x=7/3。"
    assert "合成题" in source["question_text"]
    assert source["rubric"]["parts"][0]["steps"][0]["required_elements"] == ["12x=28"]
    assert "not-for-text-request" not in json.dumps(source)
    first = client.get(f"/api/sessions/{sid}/class-analysis?view=summary&class_name=").json()
    q2 = next(q for q in first["data"]["questions"] if q["question_id"] == "Q2")
    assert {(c["kind"], c["count"]) for c in q2["causes"]} == {("process", 1), ("error", 1)}
    again = client.get(f"/api/sessions/{sid}/class-analysis?view=summary&class_name=").json()
    assert again == first
    reopened = ClassAnalysisStateStore(reports_dir).load(sid)
    # 存储的 input 是不含动态 known_patterns 的输入快照（指纹判定也以它为准）。
    stored_input = reopened["cause_analysis"]["questions"]["Q2"]["input"]
    assert {k: v for k, v in stored_input.items() if k != "known_patterns"} == {
        k: v for k, v in source.items() if k != "known_patterns"
    }
    assert len(fake.calls) == 2
    # 只改作答，不改批语，也必须更新；失败不丢失上次成功结果。
    with sqlite3.connect(db.db_path) as conn:
        raw = {"grading_completeness": {"status": "complete"}, "grading_details": [{"question_id": "Q2", "observed_answer": "已算出x=7/3"}]}
        conn.execute("UPDATE session_results SET raw_json=? WHERE id=?", (json.dumps(raw), result_ids[0]))
    changed = client.get(f"/api/sessions/{sid}/class-analysis?view=summary").json()
    assert changed["cause_analysis"]["pending_questions"] == 1
    holder["client"] = FakeLLMClient(error=TimeoutError("test"))
    generate()
    assert ClassAnalysisStateStore(reports_dir).load(sid)["cause_analysis"]["questions"]["Q2"]["input"] == stored_input
    holder["client"] = fake
    generate()
    current = ClassAnalysisStateStore(reports_dir).load(sid)["cause_analysis"]["questions"]["Q2"]
    assert current["history"][0]["input"] == stored_input
    assert current["input"] != stored_input
    # 参考解答改变也需要重新核对。
    answer_key["questions"][1]["analysis"] = "可接受等价分数，过程需完整。"
    answer_path.write_text(json.dumps(answer_key, ensure_ascii=False), encoding="utf-8")
    assert client.get(f"/api/sessions/{sid}/class-analysis?view=summary").json()["cause_analysis"]["pending_questions"] == 1


def test_cause_multiple_aspects_previous_part_and_legacy_preservation(tmp_path):
    import backend.jobs  # 与应用入口保持相同的现有 jobs 初始化顺序。
    from copy import deepcopy
    from analysis_report_exporter import assemble_session_analysis, build_class_page_data, split_session_analysis_by_class
    from backend.class_analysis import build_cause_inputs, save_cause_result, ClassAnalysisStateStore, apply_cause_results
    from db_manager import DBManager
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    sid = _seed_analysis_session(db, tmp_path)
    data = assemble_session_analysis(db, sid, data_root=tmp_path, page_only=True)
    for info in data.questions:
        info.question_id = "Q2(P1)" if info.question_id == "Q1" else "Q2(P2)"
    for index, student in enumerate(data.students):
        student.class_name = f"{index + 1} 班"
        for record in student.records:
            record.question_id = "Q2(P1)" if record.question_id == "Q1" else "Q2(P2)"
            record.deduction_reason = record.error_summary = "相同概括批语"
            if record.question_id == "Q2(P1)":
                record.student_answer = "绳长16" if index == 0 else "绳长24"
            else:
                record.student_answer = "sqrt161-6" if index == 0 else "17²-7²=240"
    data.attendance_by_class = {"1 班": {"present": 1, "absent": 0}, "2 班": {"present": 1, "absent": 0}}
    sources = build_cause_inputs(data)
    source = next(s for s in sources if s["question_id"] == "Q2(P2)")
    a = next(e["id"] for e in source["evidence"] if e["student_answer"] == "sqrt161-6")
    b = next(e["id"] for e in source["evidence"] if e["student_answer"] == "17²-7²=240")
    assert all(e["previous_answers"][0]["question_id"] == "Q2(P1)" for e in source["evidence"])
    store = ClassAnalysisStateStore(tmp_path / "reports")
    old_source = {key: deepcopy(source[key]) for key in ("question_id", "max_score", "stem_summary", "canonical_answer", "rubric")}
    old_source["evidence"] = [{"id": "E1", "text": source["evidence"][0]["text"]}]
    old = {"version": "class_error_causes_v1", "input": old_source,
           "result": {"groups": [{"reason": "旧归并", "evidence_ids": ["E1"]}], "positive_ids": [], "uncertain_ids": []},
           "origin": "assistant", "generated_at": "2026-01-01T00:00:00"}
    store.save(sid, cause_analysis={"questions": {"Q2(P2)": old}})
    page = build_class_page_data(data, compact=True)
    status = apply_cause_results(page, data, sources, store.load(sid))
    assert status["legacy_questions"] == 1
    assert status["pending_questions"] == len(sources)
    old_page = next(q for q in page["questions"] if q["question_id"] == "Q2(P2)")
    assert old_page["causes_legacy"] is True and old_page["causes"][0]["count"] == 2
    payload = {"groups": [
        {"kind": "carry_forward", "reason": "前问错误结果延续", "manifestation": "沿用绳长16继续计算", "source_question_id": "Q2(P1)", "evidence_ids": [a]},
        {"kind": "process", "category": "过程与依据", "reason": "依据未明确写出", "manifestation": "未写依据", "evidence_ids": [a]},
        {"kind": "error", "category": "审题与条件", "reason": "数量对应错误", "manifestation": "把7用作固定高度", "evidence_ids": [b, b]},
        {"kind": "error", "category": "审题与条件", "reason": "数量对应错误", "manifestation": "把7用作剩余竖段", "evidence_ids": [b]},
        {"kind": "review", "reason": "文字不足以明确", "manifestation": "最后结果依据待核", "evidence_ids": [b]},
    ]}
    save_cause_result(store, sid, source, payload, origin="assistant")
    saved = ClassAnalysisStateStore(tmp_path / "reports").load(sid)
    assert saved["cause_analysis"]["questions"]["Q2(P2)"]["history"] == [old]
    page = build_class_page_data(data, compact=True)
    apply_cause_results(page, data, sources, saved)
    question = next(q for q in page["questions"] if q["question_id"] == "Q2(P2)")
    assert len(question["causes"]) == 4
    error = next(c for c in question["causes"] if c["kind"] == "error")
    assert error["count"] == 1 and len(error["manifestations"]) == 2
    for name, scoped in split_session_analysis_by_class(data).items():
        scoped_page = build_class_page_data(scoped, compact=True)
        apply_cause_results(scoped_page, scoped, sources, saved)
        causes = next(q for q in scoped_page["questions"] if q["question_id"] == "Q2(P2)")["causes"]
        assert {c["kind"] for c in causes} == ({"carry_forward", "process"} if name == "1 班" else {"error", "review"})
        assert all(c["count"] == 1 for c in causes)
    invalid = deepcopy(payload)
    invalid["groups"][0]["source_question_id"] = "Q99"
    with pytest.raises(ValueError, match="previous question"):
        save_cause_result(store, sid, source, invalid)
    with pytest.raises(ValueError, match="conflicting"):
        save_cause_result(store, sid, source, {**payload, "positive_ids": [b]})
    data.students[0].records[0].student_answer = "绳长18，已订正"
    updated = build_cause_inputs(data)
    assert next(s for s in updated if s["question_id"] == "Q2(P2)") != source
    updated_page = build_class_page_data(data, compact=True)
    apply_cause_results(updated_page, data, updated, saved)
    assert not next(q for q in updated_page["questions"] if q["question_id"] == "Q2(P2)").get("causes_grouped")


def test_get_class_analysis_no_data_without_results(
    class_analysis_api_client, tmp_path: Path
) -> None:
    client, db, _session_id, _reports_dir, _manager, _llm, _m = class_analysis_api_client
    empty_session = db.create_grading_session("空场次", "rubric.json", "answer.json")

    response = client.get(f"/api/sessions/{empty_session}/class-analysis")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "no_data"
    assert payload["data"] is None
    assert payload["narrative"] is None
    assert payload["auto_generate"] is True  # 默认开
    assert payload["small_sample"] is False
    assert payload["stale"] is False
    assert payload["active_job_id"] is None


def test_get_class_analysis_ready_maps_narrative_aliases_to_real_names(
    class_analysis_api_client,
) -> None:
    client, _db, session_id, _reports_dir, manager, llm_holder, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, True)

    created = _generate_via_api(client, session_id)
    manager.wait(created["id"], timeout=5)

    response = client.get(f"/api/sessions/{session_id}/class-analysis")

    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["stale"] is False
    assert payload["narrative_failed"] is False
    assert payload["generated_at"]
    assert payload["small_sample"] is True  # 参考 2 人 < 10
    data = payload["data"]
    assert data["exam"]["title"] == "单元测试"
    assert data["score_distribution"]["avg"] == 70
    # 与前端解码器（frontend/src/api/class-analysis.ts）冻结对齐的结构：
    # present / roster_absent（姓名数组）在 data 顶层，bands 为 {分数段: 人数} 字典。
    assert data["present"] == 2
    assert data["roster_absent"] == ["王五"]
    bands = data["score_distribution"]["bands"]
    assert isinstance(bands, dict)
    assert all(isinstance(key, str) for key in bands)
    assert all(isinstance(count, int) and count >= 0 for count in bands.values())
    names = {student["student_name"] for student in data["students"]}
    assert names == {"张三", "李四"}  # 教师本人页面出参不脱敏
    zhangsan = next(s for s in data["students"] if s["student_name"] == "张三")
    assert zhangsan["rank"] == 1
    # lost 条目形状：{question_id, lost_points, record}。
    assert zhangsan["lost"] and zhangsan["lost"][0]["question_id"] == "Q2"
    lost_entry = zhangsan["lost"][0]
    assert set(lost_entry) == {"question_id", "lost_points", "record"}
    assert lost_entry["lost_points"] == 10
    assert isinstance(lost_entry["record"], dict)
    assert lost_entry["record"]["deduction_reason"] == "缺关键步骤"
    lisi = next(s for s in data["students"] if s["student_name"] == "李四")
    assert lisi["needs_review"] is True
    q2 = next(q for q in data["questions"] if q["question_id"] == "Q2")
    assert q2["max_score"] == 40
    # class_rate 恒为数字，不得为 null。
    assert q2["class_rate"] == 0.625
    for question in data["questions"]:
        assert isinstance(question["class_rate"], (int, float))
    assert q2["stem_summary"] == "证明线段数量关系"
    assert q2["canonical_answer"] == "AB=BD+DH"
    assert {r["student_name"] for r in q2["records"]} == {"张三", "李四"}
    # 叙述中的 S1/S2 代号在服务端映射回真实姓名。
    notes = payload["narrative"]["student_notes"]
    assert notes[0]["student_name"] == "张三"
    assert notes[1]["student_name"] == "李四"
    assert payload["narrative"]["key_findings"][0]["title"] == "证明题得分率低"


def test_get_class_analysis_generating_while_job_running(
    class_analysis_api_client,
) -> None:
    client, _db, session_id, _reports_dir, manager, llm_holder, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, True)
    blocking = BlockingLLMClient()
    llm_holder["client"] = blocking

    created = _generate_via_api(client, session_id)
    assert blocking.started.wait(3)
    try:
        payload = client.get(f"/api/sessions/{session_id}/class-analysis").json()
        assert payload["status"] == "generating"
        assert payload["active_job_id"] == created["id"]
        assert payload["data"] is not None  # 数据段照常可读
    finally:
        blocking.release.set()
        manager.wait(created["id"], timeout=5)


def test_get_class_analysis_stale_after_score_change(
    class_analysis_api_client,
) -> None:
    client, db, session_id, _reports_dir, manager, _llm, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, True)
    created = _generate_via_api(client, session_id)
    manager.wait(created["id"], timeout=5)
    assert client.get(f"/api/sessions/{session_id}/class-analysis").json()["stale"] is False

    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE session_details SET deduction_reason = '复核后改判' WHERE question_id = 'Q2'"
        )

    payload = client.get(f"/api/sessions/{session_id}/class-analysis").json()
    assert payload["stale"] is True
    assert payload["narrative"] is None


def test_class_analysis_switches_classes_and_persists_separate_narratives(class_analysis_api_client) -> None:
    client, db, session_id, reports_dir, manager, llm, monkeypatch = class_analysis_api_client
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE students SET class_name='2 班' WHERE name='李四'")
    _patch_configured(monkeypatch, True)
    job = _generate_via_api(client, session_id)
    manager.wait(job["id"], timeout=5)
    assert llm["client"].calls == 2
    for name, score, student in (("1 班", 90, "张三"), ("2 班", 50, "李四")):
        page = client.get(f"/api/sessions/{session_id}/class-analysis", params={"class_name": name}).json()
        assert page["class_names"] == ["1 班", "2 班"]
        assert page["selected_class"] == name
        assert page["data"]["score_distribution"]["avg"] == score
        assert len(page["data"]["students"]) == 1
        assert page["data"]["students"][0]["rank"] == 1
        assert page["narrative"]["student_notes"][0]["student_name"] == student
    state = json.loads((reports_dir / ".class_analysis" / f"{session_id}.json").read_text(encoding="utf-8"))
    assert set(state["class_reports"]) == {"1 班", "2 班"}
    assert state["narrative"] is None


def test_summary_merges_classes_and_counts_only_students_who_lost_points(class_analysis_api_client) -> None:
    client, db, session_id, _reports_dir, _manager, llm, monkeypatch = class_analysis_api_client
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE students SET class_name='2 班' WHERE name='李四'")
        conn.execute("UPDATE session_details SET error_summary='缺 BE⊥AC 步骤；计算错误；计算错误' WHERE question_id='Q2' AND result_id=(SELECT id FROM session_results WHERE student_score=50)")
    monkeypatch.setattr("backend.api.routers.reports.score_revision", lambda *a: pytest.fail("统计页面不应读取 AI 版本明细"))
    payload = client.get(f"/api/sessions/{session_id}/class-analysis", params={"class_name": "", "view": "summary"}).json()
    data = payload["data"]
    assert payload["selected_class"] is None
    assert payload["class_names"] == ["1 班", "2 班"]
    assert data["present"] == 2
    assert data["score_distribution"]["avg"] == 70
    assert data["students"] == []
    q1, q2 = data["questions"]
    assert [(item["student_name"], item["score"]) for item in q1["records"]] == [("李四", 30)]
    assert q2["causes"] == [{"reason": "缺 BE⊥AC 步骤", "count": 2}, {"reason": "计算错误", "count": 1}]
    assert all("error_summary" not in item for item in q2["records"])
    for class_name, expected in (("1 班", 90), ("2 班", 50)):
        selected = client.get(f"/api/sessions/{session_id}/class-analysis", params={"class_name": class_name, "view": "summary"}).json()
        assert selected["data"]["score_distribution"]["avg"] == expected
        assert all(item["class_name"] == class_name for q in selected["data"]["questions"] for item in q["records"])
    assert llm["client"].calls == 0


def test_page_assembly_keeps_scores_without_per_student_report_queries(class_analysis_api_client) -> None:
    from analysis_report_exporter import assemble_session_analysis, build_class_page_data
    from backend.repositories.results import ResultRepositoryGateway

    _client, db, session_id, _reports, _manager, _llm, monkeypatch = class_analysis_api_client
    full = assemble_session_analysis(db, session_id)
    monkeypatch.setattr(ResultRepositoryGateway, "get_result_details", lambda *a: pytest.fail("逐人附加报告信息不应在页面读取"))
    page = assemble_session_analysis(db, session_id, page_only=True)
    assert build_class_page_data(page, compact=True) == build_class_page_data(full, compact=True)


def test_question_preview_uses_bound_source_and_marks_summary_fallback(class_analysis_api_client, tmp_path: Path) -> None:
    from types import SimpleNamespace
    from backend.config_workspace.sources import ConfigSourceService, _project_config_rich_blocks

    client, db, session_id, _reports, _manager, llm, monkeypatch = class_analysis_api_client
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE grading_sessions SET source_paper_sha256=? WHERE id=?", ("a" * 64, session_id))
    blocks = _project_config_rich_blocks('<p>已知直角三角形，两直角边为3和4，求斜边。</p>')
    source = SimpleNamespace(sha256="a" * 64, public_snapshot=lambda: {"questions": [{
        "question_id": "Q1", "question_preview": "完整合成题干", "rich_content": {
            "available": True, "question_blocks": blocks, "question_block_count": len(blocks),
            "answer_blocks": blocks, "answer_block_count": len(blocks),
        },
    }]})
    # 文件系统锚定由配置源套件验证；这里隔离验证绑定与图文出参。
    monkeypatch.setattr(ConfigSourceService, "__init__", lambda *a, **k: None)
    monkeypatch.setattr(ConfigSourceService, "load_active_record", lambda *a, **k: source)
    endpoint = f"/api/sessions/{session_id}/class-analysis/questions/Q1(1)/preview"
    preview = client.get(endpoint).json()
    assert preview["parent_question_id"] == "Q1"
    assert preview["rich_content"]["question_blocks"] == blocks
    assert preview["rich_content"]["answer_blocks"] == []
    assert preview["notice"] == ""
    source.sha256 = "b" * 64
    fallback = client.get(endpoint).json()
    assert fallback["text"] == "识别轴对称图形"
    assert "仅为已保存的题干摘要" in fallback["notice"]
    assert llm["client"].calls == 0


def test_question_preview_preserves_uploaded_formula_and_image(class_analysis_api_client) -> None:
    import hashlib
    from tests.test_api_config_sources import _docx_with_inline_question_media_bytes

    client, db, session_id, _reports, _manager, llm, _monkeypatch = class_analysis_api_client
    source_bytes = _docx_with_inline_question_media_bytes()
    uploaded = client.post(
        f"/api/sessions/{session_id}/config/sources", content=source_bytes,
        headers={"content-type": "application/octet-stream", "x-upload-filename": "synthetic.docx"},
    )
    assert uploaded.status_code == 201, uploaded.text
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE grading_sessions SET source_paper_sha256=? WHERE id=?", (hashlib.sha256(source_bytes).hexdigest(), session_id))
    response = client.get(f"/api/sessions/{session_id}/class-analysis/questions/Q11/preview")
    assert response.status_code == 200
    preview = response.json()
    assert preview["notice"] == ""
    assert "Eleventh question x" in preview["text"]
    blocks = preview["rich_content"]["question_blocks"]
    assert any(segment["superscript"] and segment["text"] == "2" for block in blocks for segment in block["segments"])
    images = [url for block in blocks for url in block["asset_urls"]]
    assert images
    image = client.get(images[0])
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/")
    assert llm["client"].calls == 0


def test_get_class_analysis_narrative_failed_state(
    class_analysis_api_client,
) -> None:
    from llm_client import LLMResponseFormatError

    client, _db, session_id, _reports_dir, manager, llm_holder, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, True)
    llm_holder["client"] = FakeLLMClient(error=LLMResponseFormatError("bad json"))

    created = _generate_via_api(client, session_id)
    manager.wait(created["id"], timeout=5)

    payload = client.get(f"/api/sessions/{session_id}/class-analysis").json()
    assert payload["status"] == "ready"  # 数据仍在，仅叙述失败
    assert payload["narrative"] is None
    assert payload["narrative_failed"] is True
    assert payload["data"] is not None


def test_put_class_analysis_settings_persists(
    class_analysis_api_client,
) -> None:
    client, _db, session_id, reports_dir, _manager, _llm, _m = class_analysis_api_client

    response = client.put(
        f"/api/sessions/{session_id}/class-analysis/settings",
        json={"auto_generate": False},
    )
    assert response.status_code == 200
    assert response.json() == {"auto_generate": False}

    # 重新读取状态文件（模拟重新进入页面）仍然是关闭。
    from backend.class_analysis import ClassAnalysisStateStore

    reloaded = ClassAnalysisStateStore(reports_dir).load(session_id)
    assert reloaded is not None
    assert reloaded["auto_generate"] is False
    assert client.get(f"/api/sessions/{session_id}/class-analysis").json()[
        "auto_generate"
    ] is False

    response = client.put(
        f"/api/sessions/{session_id}/class-analysis/settings",
        json={"auto_generate": True},
    )
    assert response.json() == {"auto_generate": True}


def test_regenerate_reuses_active_job(class_analysis_api_client) -> None:
    client, _db, session_id, _reports_dir, manager, llm_holder, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, True)
    blocking = BlockingLLMClient()
    llm_holder["client"] = blocking

    first = _generate_via_api(client, session_id)
    assert blocking.started.wait(3)
    try:
        second = _generate_via_api(client, session_id)
        assert second["id"] == first["id"]
    finally:
        blocking.release.set()
        manager.wait(first["id"], timeout=5)


def test_regenerate_rejected_when_model_unconfigured(
    class_analysis_api_client,
) -> None:
    client, _db, session_id, _reports_dir, _manager, _llm, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, False)

    response = client.post(f"/api/sessions/{session_id}/class-analysis/regenerate")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "content_generation_model_not_configured"


def test_class_analysis_job_success_writes_state_and_metadata_only(
    class_analysis_api_client,
) -> None:
    client, _db, session_id, reports_dir, manager, llm_holder, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, True)

    created = _generate_via_api(client, session_id)
    manager.wait(created["id"], timeout=5)

    job = manager.get(created["id"])
    assert job.status == "succeeded"
    # result 只放元数据，不放叙述正文。
    assert set(job.result) <= {"session_id", "status", "generated_at", "skipped"}
    assert job.result["status"] == "ready"
    state_path = reports_dir / ".class_analysis" / f"{session_id}.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "ready"
    assert state["narrative"]["key_findings"][0]["title"] == "证明题得分率低"
    assert state["score_revision"]
    assert llm_holder["client"].calls == 1


def test_class_analysis_job_failure_degrades_without_retry(
    class_analysis_api_client,
) -> None:
    from llm_client import LLMResponseFormatError

    client, _db, session_id, reports_dir, manager, llm_holder, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, True)
    failing = FakeLLMClient(error=LLMResponseFormatError("bad json"))
    llm_holder["client"] = failing

    created = _generate_via_api(client, session_id)
    manager.wait(created["id"], timeout=5)

    job = manager.get(created["id"])
    assert job.status == "succeeded"  # 降级失败不是 job 失败
    assert job.result["status"] == "failed"
    assert failing.calls == 1  # 不重发
    state = json.loads(
        (reports_dir / ".class_analysis" / f"{session_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["status"] == "failed"
    assert state["narrative"] is None
    assert state["narrative_error"]


def test_class_analysis_job_skips_model_call_for_same_ready_revision(
    class_analysis_api_client,
) -> None:
    client, db, session_id, _reports_dir, manager, llm_holder, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, True)
    fake = llm_holder["client"]

    first = _generate_via_api(client, session_id)
    manager.wait(first["id"], timeout=5)
    assert fake.calls == 1

    # 非手动（force=False）的同 revision 自动生成：幂等跳过模型调用。
    from backend.class_analysis import submit_class_analysis_generate
    from backend.report_exports import score_revision

    second = submit_class_analysis_generate(
        manager=manager,
        session_id=session_id,
        revision=score_revision(db, session_id),
        force=False,
    )
    manager.wait(second.id, timeout=5)
    assert manager.get(second.id).result["skipped"] is True
    assert fake.calls == 1


# ---------------------------------------------------------------------------
# 自动触发
# ---------------------------------------------------------------------------


@pytest.fixture
def auto_generate_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from backend.jobs.default_handlers import _build_class_analysis_generate_handler
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from db_manager import DBManager

    db = DBManager(tmp_path / "databases" / "grading.db")
    db.initialize()
    session_id = _seed_analysis_session(db, tmp_path)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    fake = FakeLLMClient(narrative=CLASS_NARRATIVE)
    manager.register(
        "class_analysis_generate",
        _build_class_analysis_generate_handler(
            db_path=db.db_path,
            reports_dir=reports_dir,
            data_root=tmp_path,
            llm_client_factory=lambda: fake,
        ),
    )
    try:
        yield db, session_id, reports_dir, manager, fake, monkeypatch
    finally:
        manager.shutdown()


def test_auto_generate_submits_job_once(auto_generate_env) -> None:
    from backend.class_analysis import maybe_auto_generate_class_analysis

    db, session_id, reports_dir, manager, fake, monkeypatch = auto_generate_env
    _patch_configured(monkeypatch, True)

    first = maybe_auto_generate_class_analysis(
        manager=manager, db=db, session_id=session_id, reports_dir=reports_dir
    )
    assert first is not None
    second = maybe_auto_generate_class_analysis(
        manager=manager, db=db, session_id=session_id, reports_dir=reports_dir
    )
    assert second is not None
    assert second.id == first.id  # 进行中 job 防重复用
    manager.wait(first.id, timeout=5)

    # 完成后同 revision 已 ready：幂等跳过，不再提交、不再调用模型。
    third = maybe_auto_generate_class_analysis(
        manager=manager, db=db, session_id=session_id, reports_dir=reports_dir
    )
    assert third is None
    assert fake.calls == 1
    jobs, total = manager.list(
        session_id=session_id, job_types=("class_analysis_generate",), limit=10
    )
    assert total == 1


def test_auto_generate_respects_switch_and_model_config(auto_generate_env) -> None:
    from backend.class_analysis import (
        ClassAnalysisStateStore,
        maybe_auto_generate_class_analysis,
    )

    db, session_id, reports_dir, manager, fake, monkeypatch = auto_generate_env

    # 开关关闭：不触发。
    _patch_configured(monkeypatch, True)
    ClassAnalysisStateStore(reports_dir).set_auto_generate(session_id, False)
    assert (
        maybe_auto_generate_class_analysis(
            manager=manager, db=db, session_id=session_id, reports_dir=reports_dir
        )
        is None
    )
    ClassAnalysisStateStore(reports_dir).set_auto_generate(session_id, True)

    # 未配置模型：不提交 job，状态文件记 not_configured。
    _patch_configured(monkeypatch, False)
    assert (
        maybe_auto_generate_class_analysis(
            manager=manager, db=db, session_id=session_id, reports_dir=reports_dir
        )
        is None
    )
    state = ClassAnalysisStateStore(reports_dir).load(session_id)
    assert state is not None
    assert state["status"] == "not_configured"
    assert state["narrative"] is None
    _jobs, total = manager.list(
        session_id=session_id, job_types=("class_analysis_generate",), limit=10
    )
    assert total == 0
    assert fake.calls == 0


def test_grading_run_completion_triggers_auto_generation(tmp_path: Path, monkeypatch) -> None:
    """阅卷 run 判定 completed 时只触发一次班级分析自动生成。"""
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from db_manager import DBManager

    db = DBManager(tmp_path / "databases" / "grading.db")
    db.initialize()
    session_id = _seed_analysis_session(db, tmp_path)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    _patch_configured(monkeypatch, True)
    fake = FakeLLMClient(narrative=CLASS_NARRATIVE)

    def fake_grading_runner(**kwargs):
        return {
            "session_id": kwargs["session_id"],
            "state": "completed",
            "summary": {"graded": 2, "failed": 0},
        }

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=2)
    try:
        register_default_job_handlers(
            manager,
            db_path=db.db_path,
            reports_dir=reports_dir,
            exams_dir=tmp_path / "exams",
            templates_dir=tmp_path / "templates",
            data_root=tmp_path,
            grading_runner=fake_grading_runner,
            analysis_llm_client_factory=lambda: fake,
            llm_client_factory=lambda: object(),
        )
        grading_job = manager.submit("grading_run", {"session_id": session_id})
        manager.wait(grading_job.id, timeout=5)
        assert manager.get(grading_job.id).status == "succeeded"

        jobs, total = manager.list(
            session_id=session_id, job_types=("class_analysis_generate",), limit=10
        )
        assert total == 1
        manager.wait(jobs[0].id, timeout=5)
        assert manager.get(jobs[0].id).status == "succeeded"
        state = json.loads(
            (reports_dir / ".class_analysis" / f"{session_id}.json").read_text(
                encoding="utf-8"
            )
        )
        assert state["status"] == "ready"
        assert fake.calls == 1
    finally:
        manager.shutdown()


def test_class_analysis_report_serves_generated_class_html(
    class_analysis_api_client,
) -> None:
    client, db, session_id, _reports_dir, manager, _llm, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, True)

    pending = client.get(f"/api/sessions/{session_id}/class-analysis/report")
    assert pending.status_code == 409
    assert pending.json()["error"]["code"] == "class_report_not_ready"

    created = _generate_via_api(client, session_id)
    manager.wait(created["id"], timeout=5)
    assert manager.get(created["id"]).status == "succeeded"

    response = client.get(f"/api/sessions/{session_id}/class-analysis/report")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "班级报告" in body
    # AI 叙述进入报告正文。
    assert "证明题得分率低" in body
    assert "张三" in body
    assert "李四" in body

    named = client.get(
        f"/api/sessions/{session_id}/class-analysis/report",
        params={"class_name": "1 班"},
    )
    assert named.status_code == 200
    assert "1 班" in named.text


def test_class_analysis_report_selects_each_class(
    class_analysis_api_client,
) -> None:
    client, db, session_id, _reports_dir, manager, _llm, monkeypatch = (
        class_analysis_api_client
    )
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE students SET class_name='2 班' WHERE name='李四'")
    _patch_configured(monkeypatch, True)
    created = _generate_via_api(client, session_id)
    manager.wait(created["id"], timeout=5)
    assert manager.get(created["id"]).status == "succeeded"

    first = client.get(
        f"/api/sessions/{session_id}/class-analysis/report",
        params={"class_name": "1 班"},
    )
    second = client.get(
        f"/api/sessions/{session_id}/class-analysis/report",
        params={"class_name": "2 班"},
    )
    assert first.status_code == 200 and second.status_code == 200
    assert "张三" in first.text and "李四" not in first.text
    assert "李四" in second.text and "张三" not in second.text
    # 无此班级时按首个可用班级回退，不产生错误页。
    fallback = client.get(
        f"/api/sessions/{session_id}/class-analysis/report",
        params={"class_name": "9 班"},
    )
    assert fallback.status_code == 200


# ---------------------------------------------------------------------------
# P1 错因体系：7 大类归一、学生错因记录物化、整理编排
# ---------------------------------------------------------------------------


def test_cause_category_normalization_and_internal_code_cleaning() -> None:
    from backend.error_causes import (
        CAUSE_CATEGORIES,
        clean_cause_text,
        normalize_cause_category,
    )

    assert len(CAUSE_CATEGORIES) == 7
    for category in CAUSE_CATEGORIES:
        assert normalize_cause_category(category) == category
    # 批改旧词与题库旧词都换算到 7 大类；占位与未知词不猜。
    assert normalize_cause_category("计算错误") == "计算与化简"
    assert normalize_cause_category("运算化简错误") == "计算与化简"
    assert normalize_cause_category("书写依据不完整") == "过程与依据"
    assert normalize_cause_category("其他") is None
    assert normalize_cause_category("人工复核已确认") is None
    assert normalize_cause_category("不存在的类别") is None
    assert normalize_cause_category(None) is None
    # 批改写出的内部英文码：受控翻译、占位剔除、未登记纯英文剔除、中文保留。
    assert clean_cause_text("blank_or_no_valid_work") == "空白或无有效作答内容"
    assert clean_cause_text("manual_review_confirmed") == ""
    assert clean_cause_text("人工复核已确认") == ""
    assert clean_cause_text("mystery_internal_code") == ""
    assert clean_cause_text("计算时抄错数字") == "计算时抄错数字"
    assert clean_cause_text("objective_answer=65") == "客观题作答：65"


def _cause_source(data, question_id: str) -> dict:
    from backend.class_analysis import build_cause_inputs

    return next(
        source for source in build_cause_inputs(data)
        if source["question_id"] == question_id
    )


def test_normalize_cause_result_requires_valid_category(tmp_path: Path) -> None:
    import backend.jobs  # 与应用入口保持相同的现有 jobs 初始化顺序。
    from analysis_report_exporter import assemble_session_analysis
    from backend.class_analysis import (
        ClassAnalysisStateStore,
        save_cause_result,
    )
    from db_manager import DBManager

    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    sid = _seed_analysis_session(db, tmp_path)
    data = assemble_session_analysis(
        db, sid, data_root=tmp_path, page_only=True, include_answer_evidence=True
    )
    source = _cause_source(data, "Q2")
    evidence_id = source["evidence"][0]["id"]
    store = ClassAnalysisStateStore(tmp_path / "reports")

    group = {
        "kind": "error",
        "reason": "数量对应错误",
        "manifestation": "把 7 用作固定高度",
        "evidence_ids": [evidence_id],
    }
    with pytest.raises(ValueError, match="category"):
        save_cause_result(store, sid, source, {"groups": [group]})
    with pytest.raises(ValueError, match="category"):
        save_cause_result(
            store, sid, source,
            {"groups": [{**group, "category": "未作答"}]},  # error 不允许挂未作答
        )
    with pytest.raises(ValueError, match="category"):
        save_cause_result(
            store, sid, source,
            {"groups": [{**group, "category": "批改旧词未换算"}]},
        )
    # review / carry_forward 结构标记不需要大类。
    save_cause_result(
        store, sid, source,
        {"groups": [{"kind": "review", "reason": "过程原因未明",
                     "manifestation": "仅有最终错误结果", "evidence_ids": [evidence_id]}]},
    )
    saved = store.load(sid)["cause_analysis"]["questions"]["Q2"]["result"]
    assert saved["groups"][0]["category"] is None


def test_student_error_records_materialize_and_invalidate_on_input_change(
    tmp_path: Path,
) -> None:
    import backend.jobs
    from backend.class_analysis import (
        ClassAnalysisStateStore,
        assemble_cause_data,
        build_cause_inputs,
        save_cause_result,
        student_error_map,
    )
    from db_manager import DBManager

    db = DBManager(tmp_path / "databases" / "grading.db")
    db.initialize()
    sid = _seed_analysis_session(db, tmp_path)
    store = ClassAnalysisStateStore(tmp_path / "reports")
    data = assemble_cause_data(db, sid, data_root=tmp_path)
    sources = build_cause_inputs(data)
    source = _cause_source(data, "Q2")
    ids = [item["id"] for item in source["evidence"]]
    save_cause_result(
        store, sid, source,
        {"groups": [
            {"kind": "error", "category": "概念理解", "reason": "垂直关系用错",
             "manifestation": "未证垂直就用性质", "evidence_ids": ids},
        ]},
        data=data,
    )
    state = store.load(sid)
    envelope = state["error_records"]["Q2"]
    assert envelope["input_fingerprint"]
    assert {row["category"] for row in envelope["records"]} == {"概念理解"}
    assert {row["student_id"] for row in envelope["records"]} == {
        student.student_id for student in data.students
        if any(r.question_id == "Q2" and r.lost for r in student.records)
    }
    lisi = next(s for s in data.students if s.student_name == "李四")
    mapped = student_error_map(state, lisi, sources)
    assert [row["pattern"] for row in mapped["Q2"]] == ["垂直关系用错"]
    # 批语变化 → 输入指纹变化 → 物化记录不再匹配，报告不展示旧归类。
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE session_details SET deduction_reason='复核后改判' WHERE question_id='Q2'"
        )
    changed = assemble_cause_data(db, sid, data_root=tmp_path)
    assert student_error_map(state, lisi, build_cause_inputs(changed)) == {}


def test_known_cause_patterns_reuse_and_retry_disabled_skips_failed(
    tmp_path: Path,
) -> None:
    import backend.jobs
    from backend.class_analysis import (
        ClassAnalysisStateStore,
        assemble_cause_data,
        build_cause_inputs,
        known_cause_patterns,
        run_cause_analysis,
        save_cause_result,
    )
    from db_manager import DBManager

    db = DBManager(tmp_path / "databases" / "grading.db")
    db.initialize()
    sid = _seed_analysis_session(db, tmp_path)
    store = ClassAnalysisStateStore(tmp_path / "reports")
    data = assemble_cause_data(db, sid, data_root=tmp_path)
    source = _cause_source(data, "Q2")
    ids = [item["id"] for item in source["evidence"]]
    save_cause_result(
        store, sid, source,
        {"groups": [
            {"kind": "error", "category": "概念理解", "reason": "垂直关系用错",
             "manifestation": "未证垂直", "evidence_ids": ids},
        ]},
        data=data,
    )
    # 本场已整理的错法名进入 shared，供其他题复用。
    patterns = known_cause_patterns(store, None, sid)
    assert {item["reason"] for item in patterns["shared"]} == {"垂直关系用错"}

    class _Ctx:
        payload = {"session_id": sid}
        def raise_if_cancelled(self): pass
        def report(self, *args, **kwargs): pass

    class _FailingClient:
        def __init__(self):
            self.calls = 0
        def json_from_text(self, prompt, **kwargs):
            self.calls += 1
            raise TimeoutError("synthetic")

    failing = _FailingClient()
    result = run_cause_analysis(
        _Ctx(), db=db, data_root=tmp_path, store=store,
        llm_client_factory=lambda: failing, retry_failed=False,
    )
    # Q2 已整理跳过，只有 Q1 调用且失败。
    assert failing.calls == 1
    assert result["status"] == "failed" and result["failed_questions"] == 1
    # 报告前置阶段（retry_failed=False）：失败题不自动重发。
    second = run_cause_analysis(
        _Ctx(), db=db, data_root=tmp_path, store=store,
        llm_client_factory=lambda: failing, retry_failed=False,
    )
    assert failing.calls == 1
    assert second["failed_questions"] == 0
    # 手动整理（retry_failed=True）仍会重试失败题。
    run_cause_analysis(
        _Ctx(), db=db, data_root=tmp_path, store=store,
        llm_client_factory=lambda: failing, retry_failed=True,
    )
    assert failing.calls == 2


def test_choice_question_option_path_and_teacher_confirm(
    class_analysis_api_client, tmp_path: Path,
) -> None:
    """P2 端到端：八上选择题走选项诊断；学生选项直接映射；教师确认写题库。"""
    from question_bank.database.schema import connect, initialize_database

    client, db, sid, reports_dir, manager, holder, monkeypatch = class_analysis_api_client
    _patch_configured(monkeypatch, True)

    qb_path = tmp_path / "databases" / "question_bank.db"
    initialize_database(qb_path)
    with connect(qb_path) as conn:
        conn.execute(
            "INSERT INTO questions (question_number, question_text, answer_text, question_type)"
            " VALUES ('1', '下列图形中是轴对称图形的是 A. 平行四边形 B. 等腰三角形 C. 梯形 D. 三角形',"
            " '【答案】B', '选择题')"
        )
        bank_id = int(conn.execute("SELECT id FROM questions").fetchone()[0])
        conn.execute(
            "INSERT INTO grading_question_links (grading_session_id, source_question_id,"
            " bank_question_id, link_method, status) VALUES (?, 'Q1', ?, 'manual', 'confirmed')",
            (str(sid), bank_id),
        )
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE grading_sessions SET curriculum_volume_id='bnu24-math-g8-upper' WHERE id=?",
            (sid,),
        )

    class OptionAwareClient:
        def __init__(self):
            self.calls = []

        def json_from_text(self, prompt, **kwargs):
            if "选项诊断" in prompt:
                self.calls.append("option")
                return {"options": [
                    {"option": "A", "category": "概念理解", "pattern": "误认平行四边形为轴对称",
                     "explanation": "混淆轴对称与中心对称"},
                    {"option": "C", "category": "概念理解", "pattern": "误认梯形为轴对称",
                     "explanation": "未区分等腰梯形"},
                    {"option": "D", "category": "审题与条件", "pattern": "忽略三角形限定",
                     "explanation": "一般三角形不一定轴对称"},
                ]}
            self.calls.append("v3")
            source = json.loads(prompt.rsplit("\n", 1)[1])
            return {"groups": [{
                "kind": "process", "category": "过程与依据", "reason": "缺少直角依据",
                "manifestation": "未写明直角条件",
                "evidence_ids": [item["id"] for item in source["evidence"]],
            }]}

    fake = OptionAwareClient()
    holder["client"] = fake
    job = client.post(f"/api/sessions/{sid}/class-analysis/regenerate?kind=causes").json()
    manager.wait(job["id"], timeout=10)
    assert manager.get(job["id"]).status == "succeeded"
    # Q1 选项诊断 1 次 + Q2 整题整理 1 次；李四 Q1 选 C。
    assert sorted(fake.calls) == ["option", "v3"]

    from backend.class_analysis import ClassAnalysisStateStore
    state = ClassAnalysisStateStore(reports_dir).load(sid)
    q1 = state["cause_analysis"]["questions"]["Q1"]
    group = q1["result"]["groups"][0]
    assert group["reason"] == "误认梯形为轴对称"
    assert group["category"] == "概念理解"
    assert state["option_analysis"]["Q1"]["analysis"]["C"]["pattern"] == "误认梯形为轴对称"

    # 再次整理：选项分析按题目指纹复用、Q2 结果仍新鲜 → 零新调用。
    job2 = client.post(f"/api/sessions/{sid}/class-analysis/regenerate?kind=causes").json()
    manager.wait(job2["id"], timeout=10)
    assert manager.get(job2["id"]).status == "succeeded"
    assert len(fake.calls) == 2

    # 教师确认入库：触发条件推导为 option/C；重复提交幂等。
    payload = {
        "question_id": "Q1", "kind": "error", "category": "概念理解",
        "reason": "误认梯形为轴对称", "manifestation": "选了 C",
        "operation_token": "op-1",
    }
    resp = client.post(f"/api/sessions/{sid}/class-analysis/causes/confirm", json=payload)
    assert resp.status_code == 200, resp.text
    row = resp.json()["pattern"]
    assert row["question_id"] == bank_id
    assert row["trigger_kind"] == "option" and row["trigger_value"] == "C"
    resp2 = client.post(
        f"/api/sessions/{sid}/class-analysis/causes/confirm",
        json={**payload, "operation_token": "op-2"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["pattern"]["id"] == row["id"]

    # 页面 GET 标注该错法已入库。
    page = client.get(f"/api/sessions/{sid}/class-analysis?view=summary&class_name=").json()
    q1_page = next(q for q in page["data"]["questions"] if q["question_id"] == "Q1")
    cause = next(c for c in q1_page["causes"] if c["reason"] == "误认梯形为轴对称")
    assert cause["bank_confirmed"] is True
