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
from backend.repositories.grading_database import open_grading_repositories


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
    

    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
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
    app.dependency_overrides[get_upload_config_dir] = lambda: (
        tmp_path / "config" / "uploaded"
    )
    with TestClient(app) as client:
        try:
            yield client, db, session_id, reports_dir, manager, llm_holder, monkeypatch
        finally:
            manager.shutdown()


def _generate_via_api(client, session_id: int) -> dict:
    response = client.post(f"/api/sessions/{session_id}/class-analysis/regenerate")
    assert response.status_code == 202
    return response.json()


def test_cause_groups_persist_count_students_per_class_and_update_only_changed_questions(
    class_analysis_api_client,
):
    from backend.class_analysis import ClassAnalysisStateStore

    client, db, sid, reports_dir, manager, holder, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, True)

    class CauseClient:
        def __init__(self):
            self.calls = []

        def json_from_text(self, prompt, **kwargs):
            source = json.loads(prompt.rsplit("\n", 1)[1])
            self.calls.append(source)
            return {
                "groups": [
                    {
                        "kind": "process",
                        "category": "过程与依据",
                        "reason": "缺少直角依据",
                        "manifestation": "未写明直角条件就使用勾股定理",
                        "evidence_ids": [item["id"] for item in source["evidence"]],
                    }
                ]
            }

    fake = CauseClient()
    holder["client"] = fake
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE students SET class_name='2 班' WHERE name='李四'")
    job = client.post(
        f"/api/sessions/{sid}/class-analysis/regenerate?kind=causes"
    ).json()
    manager.wait(job["id"], timeout=5)
    assert manager.get(job["id"]).status == "succeeded"
    assert len(fake.calls) == 2
    assert all("student_name" not in json.dumps(source) for source in fake.calls)
    assert all(source["rubric"] for source in fake.calls)
    merged = client.get(
        f"/api/sessions/{sid}/class-analysis?view=summary&class_name="
    ).json()
    assert merged["cause_analysis"]["status"] == "ready"
    q2 = next(q for q in merged["data"]["questions"] if q["question_id"] == "Q2")
    assert q2["causes"][0]["count"] == 2
    assert (
        len(
            {sid for item in q2["causes"][0]["evidence"] for sid in item["student_ids"]}
        )
        == 2
    )
    for name in ("1 班", "2 班"):
        payload = client.get(
            f"/api/sessions/{sid}/class-analysis",
            params={"view": "summary", "class_name": name},
        ).json()
        scoped = next(
            q for q in payload["data"]["questions"] if q["question_id"] == "Q2"
        )
        assert scoped["causes"][0]["reason"] == q2["causes"][0]["reason"]
        assert scoped["causes"][0]["count"] == 1
    assert ClassAnalysisStateStore(reports_dir).load(sid)["cause_analysis"][
        "questions"
    ]["Q2"]["result"]
    assert ClassAnalysisStateStore(reports_dir).load(sid)["narrative"] is None
    # 普通读取、切班、再次点击整理都复用；改批语只重整受影响题目。
    job = client.post(
        f"/api/sessions/{sid}/class-analysis/regenerate?kind=causes"
    ).json()
    manager.wait(job["id"], timeout=5)
    assert len(fake.calls) == 2
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE session_details SET deduction_reason='复核后：计算错误' WHERE question_id='Q2'"
        )
    changed = client.get(
        f"/api/sessions/{sid}/class-analysis?view=summary&class_name="
    ).json()
    assert changed["cause_analysis"]["stale"] is True
    assert changed["cause_analysis"]["pending_questions"] == 1
    assert not next(
        q for q in changed["data"]["questions"] if q["question_id"] == "Q2"
    ).get("causes_grouped")
    job = client.post(
        f"/api/sessions/{sid}/class-analysis/regenerate?kind=causes"
    ).json()
    manager.wait(job["id"], timeout=5)
    assert len(fake.calls) == 3
    # 图像编码不进入文本归并请求；实际评分要求变化会使该题归并过期。
    rubric_path = Path(db.sessions.get_grading_session(sid)["rubric_path"])
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric["questions"][1]["question_image_base64"] = "synthetic-image-payload"
    rubric["questions"][1]["proof_obligations"] = ["必须说明直角条件"]
    rubric_path.write_text(json.dumps(rubric, ensure_ascii=False), encoding="utf-8")
    changed = client.get(f"/api/sessions/{sid}/class-analysis?view=summary").json()
    assert changed["cause_analysis"]["pending_questions"] == 1
    job = client.post(
        f"/api/sessions/{sid}/class-analysis/regenerate?kind=causes"
    ).json()
    manager.wait(job["id"], timeout=5)
    assert len(fake.calls) == 4
    assert "synthetic-image-payload" not in json.dumps(fake.calls[-1])


def test_cause_model_failure_does_not_retry_or_replace_original_reasons(
    class_analysis_api_client,
):
    client, _db, sid, _reports, manager, holder, monkeypatch = class_analysis_api_client
    _patch_configured(monkeypatch, True)
    fake = FakeLLMClient(error=TimeoutError("synthetic timeout"))
    holder["client"] = fake
    job = client.post(
        f"/api/sessions/{sid}/class-analysis/regenerate?kind=causes"
    ).json()
    manager.wait(job["id"], timeout=5)
    assert fake.calls == 2  # 两题各一次，失败不重发。
    payload = client.get(f"/api/sessions/{sid}/class-analysis?view=summary").json()
    assert payload["cause_analysis"]["failed_questions"] == 2
    assert payload["cause_analysis"]["pending_questions"] == 2
    assert payload["data"]["questions"][0]["causes"]


def test_cause_answer_context_survives_reentry_and_invalidates_without_feedback_change(
    class_analysis_api_client,
):
    from backend.class_analysis import ClassAnalysisStateStore

    client, db, sid, reports_dir, manager, holder, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, True)
    with sqlite3.connect(db.db_path) as conn:
        result_ids = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM session_results WHERE session_id=? ORDER BY id", (sid,)
            )
        ]
        for result_id, answer in zip(
            result_ids, ("设边长x，化到12x=28，未继续", "设边长x，算得x=2")
        ):
            raw = {
                "grading_completeness": {"status": "complete"},
                "detail_metadata": {
                    "Q2": {
                        "observed_answer": answer,
                        "evidence_steps": ["已列方程"],
                        "missing_steps": [],
                    }
                },
            }
            conn.execute(
                "UPDATE session_results SET raw_json=? WHERE id=?",
                (json.dumps(raw, ensure_ascii=False), result_id),
            )
        conn.execute(
            "UPDATE session_details SET deduction_reason='求解有误', error_summary='', error_category='' WHERE question_id='Q2'"
        )
    session = db.sessions.get_grading_session(sid)
    answer_path = Path(session["answer_key_path"])
    answer_key = json.loads(answer_path.read_text(encoding="utf-8"))
    answer_key["questions"][1]["analysis"] = "列出12x=28，解得x=7/3。"
    answer_path.write_text(json.dumps(answer_key, ensure_ascii=False), encoding="utf-8")
    rubric_path = Path(session["rubric_path"])
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric["questions"][1].update(
        {
            "question_text": "合成题：求线段长度。",
            "parts": [
                {
                    "part_id": "Q2",
                    "part_score": 40,
                    "steps": [
                        {
                            "step_id": "S1",
                            "step_score": 40,
                            "core_goal": "列式求解",
                            "required_elements": ["12x=28"],
                            "question_image_base64": "not-for-text-request",
                        }
                    ],
                }
            ],
        }
    )
    rubric_path.write_text(json.dumps(rubric, ensure_ascii=False), encoding="utf-8")

    class CauseClient:
        calls = []

        def json_from_text(self, prompt, **kwargs):
            source = json.loads(prompt.rsplit("\n", 1)[1])
            self.calls.append(source)
            groups = []
            for item in source["evidence"]:
                unfinished = "未继续" in item["student_answer"]
                groups.append(
                    {
                        "kind": "process" if unfinished else "error",
                        "category": "过程与依据" if unfinished else "计算与化简",
                        "reason": "未完成求解" if unfinished else "计算错误",
                        "manifestation": "停在方程" if unfinished else "求值错误",
                        "evidence_ids": [item["id"]],
                    }
                )
            return {"groups": groups}

    fake = CauseClient()
    holder["client"] = fake

    def generate():
        job = client.post(
            f"/api/sessions/{sid}/class-analysis/regenerate?kind=causes"
        ).json()
        manager.wait(job["id"], timeout=5)
        assert manager.get(job["id"]).status == "succeeded"

    generate()
    source = next(s for s in fake.calls if s["question_id"] == "Q2")
    assert len(source["evidence"]) == 2  # 相同批语、不同作答，不提前合并。
    assert source["reference_analysis"] == "列出12x=28，解得x=7/3。"
    assert "合成题" in source["question_text"]
    assert source["rubric"]["parts"][0]["steps"][0]["required_elements"] == ["12x=28"]
    assert "not-for-text-request" not in json.dumps(source)
    first = client.get(
        f"/api/sessions/{sid}/class-analysis?view=summary&class_name="
    ).json()
    q2 = next(q for q in first["data"]["questions"] if q["question_id"] == "Q2")
    assert {(c["kind"], c["count"]) for c in q2["causes"]} == {
        ("process", 1),
        ("error", 1),
    }
    again = client.get(
        f"/api/sessions/{sid}/class-analysis?view=summary&class_name="
    ).json()
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
        raw = {
            "grading_completeness": {"status": "complete"},
            "grading_details": [
                {"question_id": "Q2", "observed_answer": "已算出x=7/3"}
            ],
        }
        conn.execute(
            "UPDATE session_results SET raw_json=? WHERE id=?",
            (json.dumps(raw), result_ids[0]),
        )
    changed = client.get(f"/api/sessions/{sid}/class-analysis?view=summary").json()
    assert changed["cause_analysis"]["pending_questions"] == 1
    holder["client"] = FakeLLMClient(error=TimeoutError("test"))
    generate()
    assert (
        ClassAnalysisStateStore(reports_dir).load(sid)["cause_analysis"]["questions"][
            "Q2"
        ]["input"]
        == stored_input
    )
    holder["client"] = fake
    generate()
    current = ClassAnalysisStateStore(reports_dir).load(sid)["cause_analysis"][
        "questions"
    ]["Q2"]
    assert current["history"][0]["input"] == stored_input
    assert current["input"] != stored_input
    # 参考解答改变也需要重新核对。
    answer_key["questions"][1]["analysis"] = "可接受等价分数，过程需完整。"
    answer_path.write_text(json.dumps(answer_key, ensure_ascii=False), encoding="utf-8")
    assert (
        client.get(f"/api/sessions/{sid}/class-analysis?view=summary").json()[
            "cause_analysis"
        ]["pending_questions"]
        == 1
    )


def test_class_analysis_switches_classes_and_persists_separate_narratives(
    class_analysis_api_client,
) -> None:
    client, db, session_id, reports_dir, manager, llm, monkeypatch = (
        class_analysis_api_client
    )
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE students SET class_name='2 班' WHERE name='李四'")
    _patch_configured(monkeypatch, True)
    job = _generate_via_api(client, session_id)
    manager.wait(job["id"], timeout=5)
    assert llm["client"].calls == 2
    for name, score, student in (("1 班", 90, "张三"), ("2 班", 50, "李四")):
        page = client.get(
            f"/api/sessions/{session_id}/class-analysis", params={"class_name": name}
        ).json()
        assert page["class_names"] == ["1 班", "2 班"]
        assert page["selected_class"] == name
        assert page["data"]["score_distribution"]["avg"] == score
        assert len(page["data"]["students"]) == 1
        assert page["data"]["students"][0]["rank"] == 1
        assert page["narrative"]["student_notes"][0]["student_name"] == student
    state = json.loads(
        (reports_dir / ".class_analysis" / f"{session_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(state["class_reports"]) == {"1 班", "2 班"}
    assert state["narrative"] is None


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
    

    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
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


# ---------------------------------------------------------------------------
# P1 错因体系：7 大类归一、学生错因记录物化、整理编排
# ---------------------------------------------------------------------------


def _cause_source(data, question_id: str) -> dict:
    from backend.class_analysis import build_cause_inputs

    return next(
        source
        for source in build_cause_inputs(data)
        if source["question_id"] == question_id
    )


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
    

    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    sid = _seed_analysis_session(db, tmp_path)
    store = ClassAnalysisStateStore(tmp_path / "reports")
    data = assemble_cause_data(db, sid, data_root=tmp_path)
    sources = build_cause_inputs(data)
    source = _cause_source(data, "Q2")
    ids = [item["id"] for item in source["evidence"]]
    save_cause_result(
        store,
        sid,
        source,
        {
            "groups": [
                {
                    "kind": "error",
                    "category": "概念理解",
                    "reason": "垂直关系用错",
                    "manifestation": "未证垂直就用性质",
                    "evidence_ids": ids,
                },
            ]
        },
        data=data,
    )
    state = store.load(sid)
    envelope = state["error_records"]["Q2"]
    assert envelope["input_fingerprint"]
    assert {row["category"] for row in envelope["records"]} == {"概念理解"}
    assert {row["student_id"] for row in envelope["records"]} == {
        student.student_id
        for student in data.students
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


def test_choice_question_option_path_auto_bank_write_and_edit(
    class_analysis_api_client,
    tmp_path: Path,
) -> None:
    """P2+P5 端到端：选项诊断直映射；整理产出自动回挂题库；教师可修改。"""
    from question_bank.database.schema import connect, initialize_database

    client, db, sid, reports_dir, manager, holder, monkeypatch = (
        class_analysis_api_client
    )
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
            "INSERT INTO questions (question_number, question_text, answer_text, question_type)"
            " VALUES ('2', '证明题题干', 'AB=BD+DH', '解答题')"
        )
        bank_id2 = int(
            conn.execute(
                "SELECT id FROM questions WHERE question_number='2'"
            ).fetchone()[0]
        )
        conn.execute(
            "INSERT INTO grading_question_links (grading_session_id, source_question_id,"
            " bank_question_id, link_method, status) VALUES (?, 'Q1', ?, 'manual', 'confirmed'),"
            " (?, 'Q2', ?, 'manual', 'confirmed')",
            (str(sid), bank_id, str(sid), bank_id2),
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
                return {
                    "options": [
                        {
                            "option": "A",
                            "category": "概念理解",
                            "pattern": "误认平行四边形为轴对称",
                            "explanation": "混淆轴对称与中心对称",
                        },
                        {
                            "option": "C",
                            "category": "概念理解",
                            "pattern": "误认梯形为轴对称",
                            "explanation": "未区分等腰梯形",
                        },
                        {
                            "option": "D",
                            "category": "审题与条件",
                            "pattern": "忽略三角形限定",
                            "explanation": "一般三角形不一定轴对称",
                        },
                    ]
                }
            self.calls.append("v3")
            source = json.loads(prompt.rsplit("\n", 1)[1])
            return {
                "groups": [
                    {
                        "kind": "process",
                        "category": "过程与依据",
                        "reason": "缺少直角依据",
                        "manifestation": "未写明直角条件",
                        "evidence_ids": [item["id"] for item in source["evidence"]],
                    }
                ]
            }

    fake = OptionAwareClient()
    holder["client"] = fake
    job = client.post(
        f"/api/sessions/{sid}/class-analysis/regenerate?kind=causes"
    ).json()
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
    assert (
        state["option_analysis"]["Q1"]["analysis"]["C"]["pattern"] == "误认梯形为轴对称"
    )

    # P5：整理结束自动回挂题库 —— 选项行 ai_auto + v3 观察行。
    from question_bank.services.error_pattern_service import list_patterns

    with connect(qb_path) as conn:
        q1_rows = list_patterns(conn, [bank_id])[bank_id]
        q2_rows = list_patterns(conn, [bank_id2])[bank_id2]
        all_option_rows = list_patterns(
            conn, [bank_id], statuses=("confirmed", "candidate")
        )[bank_id]
        total_before = conn.execute(
            "SELECT COUNT(*) FROM question_error_patterns"
        ).fetchone()[0]
    options = {
        row["trigger_value"]: row for row in q1_rows if row["trigger_kind"] == "option"
    }
    assert options["C"]["pattern"] == "误认梯形为轴对称"
    assert options["C"]["source"] == "ai_auto"
    assert options["C"]["occurrences"] == [{"session_id": sid, "question_id": "Q1"}]
    assert all(
        not row["occurrences"]
        for row in all_option_rows
        if row["trigger_kind"] == "option" and row["trigger_value"] != "C"
    )
    assert {row["pattern"] for row in q2_rows} == {"缺少直角依据"}
    assert q2_rows[0]["trigger_kind"] == "observation"
    assert q2_rows[0]["source"] == "ai_auto"

    # 再次整理：选项分析按题目指纹复用、Q2 结果仍新鲜 → 零新调用、零新增题库行。
    job2 = client.post(
        f"/api/sessions/{sid}/class-analysis/regenerate?kind=causes"
    ).json()
    manager.wait(job2["id"], timeout=10)
    assert manager.get(job2["id"]).status == "succeeded"
    assert len(fake.calls) == 2
    with connect(qb_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM question_error_patterns").fetchone()[
            0
        ]
    assert total == total_before

    # 教师可选修改错法名/大类：旧行 merged，新名 teacher_edit；会话内同步改名。
    resp = client.post(
        f"/api/sessions/{sid}/class-analysis/causes/edit",
        json={
            "question_id": "Q1",
            "kind": "error",
            "reason": "误认梯形为轴对称",
            "new_reason": "误认等腰梯形",
            "category": "审题与条件",
            "operation_token": "op-1",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
    with connect(qb_path) as conn:
        rows = conn.execute(
            "SELECT pattern, status, source, trigger_kind, trigger_value, category"
            " FROM question_error_patterns WHERE question_id=? ORDER BY id",
            (bank_id,),
        ).fetchall()
    old = next(row for row in rows if row[0] == "误认梯形为轴对称")
    assert old[1] == "merged"
    edited = next(row for row in rows if row[0] == "误认等腰梯形")
    assert edited[1:] == ("confirmed", "teacher_edit", "option", "C", "审题与条件")

    state = ClassAnalysisStateStore(reports_dir).load(sid)
    q1 = state["cause_analysis"]["questions"]["Q1"]
    group = next(g for g in q1["result"]["groups"] if g["reason"] == "误认等腰梯形")
    assert group["category"] == "审题与条件" and group["teacher_edited"] is True
    assert state["option_analysis"]["Q1"]["analysis"]["C"]["pattern"] == "误认等腰梯形"
    assert state["error_records"]["Q1"]["records"]
    assert all(
        row["pattern"] == "误认等腰梯形" and row["category"] == "审题与条件"
        for row in state["error_records"]["Q1"]["records"]
    )

    # 页面 GET：错法名已更新，带 teacher_edited 标记与错误大类人数。
    page = client.get(
        f"/api/sessions/{sid}/class-analysis?view=summary&class_name="
    ).json()
    q1_page = next(q for q in page["data"]["questions"] if q["question_id"] == "Q1")
    cause = next(c for c in q1_page["causes"] if c["reason"] == "误认等腰梯形")
    assert cause["teacher_edited"] is True
    assert {
        item["category"]: item["count"] for item in q1_page["cause_category_counts"]
    } == {
        "审题与条件": 1,
    }

    # 题库侧教师修改（其他场次/途径改名）：重新合成时教师改过的名称覆盖
    # 会话内保存的同名选项诊断。删除已保存结果迫使重新合成。
    from question_bank.services.error_pattern_service import rename_patterns

    rename_patterns(
        qb_path,
        question_ids=[bank_id],
        old_pattern="误认等腰梯形",
        new_pattern="图形限定误用",
        category="审题与条件",
    )
    store = ClassAnalysisStateStore(reports_dir)
    state = store.load(sid)
    questions = dict(state["cause_analysis"]["questions"])
    questions.pop("Q1")
    store.save(sid, cause_analysis={"questions": questions})
    job3 = client.post(
        f"/api/sessions/{sid}/class-analysis/regenerate?kind=causes"
    ).json()
    manager.wait(job3["id"], timeout=10)
    assert manager.get(job3["id"]).status == "succeeded"
    assert len(fake.calls) == 2  # 选项诊断与会话结果均复用，无新调用
    state = store.load(sid)
    reasons = {
        g["reason"]
        for g in state["cause_analysis"]["questions"]["Q1"]["result"]["groups"]
    }
    assert "图形限定误用" in reasons and "误认等腰梯形" not in reasons


def test_cause_edit_api_validation_and_unlinked_state_only(
    class_analysis_api_client,
    tmp_path: Path,
) -> None:
    """未关联题库的题目：修改只更新会话状态；校验失败返回 409/422。"""
    from backend.class_analysis import ClassAnalysisStateStore

    client, db, sid, reports_dir, manager, holder, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, True)

    class CauseClient:
        def json_from_text(self, prompt, **kwargs):
            source = json.loads(prompt.rsplit("\n", 1)[1])
            ids = [item["id"] for item in source["evidence"]]
            return {
                "groups": [
                    {
                        "kind": "process",
                        "category": "过程与依据",
                        "reason": "缺少直角依据",
                        "manifestation": "未写明直角条件",
                        "evidence_ids": ids,
                    },
                    {
                        "kind": "review",
                        "reason": "过程原因待核",
                        "manifestation": "依据不足",
                        "evidence_ids": ids[:1],
                    },
                ]
            }

    holder["client"] = CauseClient()
    job = client.post(
        f"/api/sessions/{sid}/class-analysis/regenerate?kind=causes"
    ).json()
    manager.wait(job["id"], timeout=10)
    assert manager.get(job["id"]).status == "succeeded"
    url = f"/api/sessions/{sid}/class-analysis/causes/edit"

    resp = client.post(
        url,
        json={
            "question_id": "Q9",
            "kind": "error",
            "reason": "任意",
            "new_reason": "新名",
            "category": "概念理解",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "cause_pattern_not_ready"

    resp = client.post(
        url,
        json={
            "question_id": "Q2",
            "kind": "process",
            "reason": "不存在的错法",
            "new_reason": "新名",
            "category": "过程与依据",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "cause_pattern_group_missing"

    resp = client.post(
        url,
        json={
            "question_id": "Q2",
            "kind": "process",
            "reason": "缺少直角依据",
            "new_reason": "新名",
            "category": "概念理解",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "cause_pattern_category_invalid"

    # review / response_state 等结构分组不支持修改。
    resp = client.post(
        url,
        json={
            "question_id": "Q2",
            "kind": "review",
            "reason": "过程原因待核",
            "new_reason": "新名",
            "category": None,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "cause_pattern_category_invalid"

    # 未关联题库（本题库文件不存在）：仅更新会话状态，不落库也不报错。
    resp = client.post(
        url,
        json={
            "question_id": "Q2",
            "kind": "process",
            "reason": "缺少直角依据",
            "new_reason": "直角条件未写",
            "category": "书写与规范",
        },
    )
    assert resp.status_code == 200, resp.text
    state = ClassAnalysisStateStore(reports_dir).load(sid)
    group = next(
        g
        for g in state["cause_analysis"]["questions"]["Q2"]["result"]["groups"]
        if g["reason"] == "直角条件未写"
    )
    assert group["category"] == "书写与规范" and group["teacher_edited"] is True
    assert state["error_records"]["Q2"]["records"]
    assert all(
        row["pattern"] == "直角条件未写"
        for row in state["error_records"]["Q2"]["records"]
        if row["kind"] == "process"
    )
    assert not (tmp_path / "databases" / "question_bank.db").exists()


def test_session_error_records_and_category_counts(tmp_path: Path) -> None:
    """物化错因记录：按学生×题读取、按大类去重计数、班级过滤；无状态返回空。"""
    import backend.jobs
    from backend.class_analysis import (
        ClassAnalysisStateStore,
        apply_cause_results,
        assemble_cause_data,
        build_cause_inputs,
        question_category_counts,
        save_cause_result,
        session_error_records,
    )
    from analysis_report_exporter import build_class_page_data
    

    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    sid = _seed_analysis_session(db, tmp_path)
    reports_dir = tmp_path / "reports"
    store = ClassAnalysisStateStore(reports_dir)
    assert session_error_records(db, sid, reports_dir) == {}

    data = assemble_cause_data(db, sid, data_root=tmp_path)
    source = _cause_source(data, "Q2")
    ids = [item["id"] for item in source["evidence"]]
    save_cause_result(
        store,
        sid,
        source,
        {
            "groups": [
                {
                    "kind": "error",
                    "category": "概念理解",
                    "reason": "垂直关系用错",
                    "manifestation": "未证垂直",
                    "evidence_ids": ids,
                },
            ]
        },
        data=data,
    )
    state = store.load(sid)
    # 教师修改标记透传到页面输出。
    state["cause_analysis"]["questions"]["Q2"]["result"]["groups"][0][
        "teacher_edited"
    ] = True
    store.save(sid, cause_analysis=state["cause_analysis"])

    records = session_error_records(db, sid, reports_dir)
    student_ids = {
        student.student_id
        for student in data.students
        if any(r.question_id == "Q2" and r.lost for r in student.records)
    }
    assert set(records) == student_ids
    assert all("Q2" in by_question for by_question in records.values())
    counts = question_category_counts(records)
    assert counts["Q2"] == [("概念理解", len(student_ids))]
    one = sorted(student_ids)[:1]
    assert question_category_counts(records, student_ids=one)["Q2"] == [("概念理解", 1)]

    page = build_class_page_data(data, compact=True)
    apply_cause_results(page, data, build_cause_inputs(data), store.load(sid))
    q2 = next(q for q in page["questions"] if q["question_id"] == "Q2")
    assert q2["causes"][0]["teacher_edited"] is True
    assert q2["cause_category_counts"] == [
        {"category": "概念理解", "count": len(student_ids)}
    ]


def test_class_narrative_payload_carries_organized_causes(
    class_analysis_api_client, tmp_path: Path
) -> None:
    """已归类错因进入班级叙述入参：按大类/错法聚类，不再带旧 error_category。"""
    import backend.jobs
    from backend.class_analysis import (
        ClassAnalysisStateStore,
        assemble_cause_data,
        save_cause_result,
    )

    client, db, sid, reports_dir, manager, holder, monkeypatch = (
        class_analysis_api_client
    )
    store = ClassAnalysisStateStore(reports_dir)
    data = assemble_cause_data(db, sid, data_root=tmp_path)
    source = _cause_source(data, "Q2")
    ids = [item["id"] for item in source["evidence"]]
    save_cause_result(
        store,
        sid,
        source,
        {
            "groups": [
                {
                    "kind": "error",
                    "category": "概念理解",
                    "reason": "垂直关系用错",
                    "manifestation": "未证垂直",
                    "evidence_ids": ids,
                },
            ]
        },
        data=data,
    )
    _patch_configured(monkeypatch, True)
    job = _generate_via_api(client, sid)
    manager.wait(job["id"], timeout=5)
    fake = holder["client"]
    assert fake.calls == 1
    prompt = fake.requests[0]["prompt"]
    payload = json.loads(prompt.split("输入 JSON：\n", 1)[1])
    questions = {q["question_id"]: q for q in payload["questions"]}
    q2 = questions["Q2"]
    expected = {"category": "概念理解", "pattern": "垂直关系用错"}
    assert q2["causes"] == [
        {**expected, "students": sorted(r["alias"] for r in q2["records"])}
    ]
    assert all("error_category" not in r for r in q2["records"])
    assert all(r["cause"] == [expected] for r in q2["records"])
    # 未归类错因的题保留原有字段。
    q1_records = questions["Q1"]["records"]
    assert q1_records
    assert all("error_category" in r for r in q1_records)
    assert all("cause" not in r for r in q1_records)


def test_old_rendition_marks_stale_without_model_call(
    class_analysis_api_client,
) -> None:
    """叙述版本升级后：页面仅提示需重新生成，不因版本变化自动调用模型。"""
    from backend.class_analysis import (
        CLASS_ANALYSIS_JOB_TYPE,
        CLASS_ANALYSIS_RENDITION_VERSION,
        ClassAnalysisStateStore,
    )

    client, _db, sid, reports_dir, manager, holder, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, True)
    job = _generate_via_api(client, sid)
    manager.wait(job["id"], timeout=5)
    fake = holder["client"]
    assert fake.calls == 1

    store = ClassAnalysisStateStore(reports_dir)
    state = store.load(sid)
    assert state["rendition_version"] == CLASS_ANALYSIS_RENDITION_VERSION
    # 模拟版本升级前的线上状态：就绪叙述、旧 rendition_version。
    store.save(sid, rendition_version="class_analysis_page_v3_class_scope")

    page = client.get(f"/api/sessions/{sid}/class-analysis").json()
    assert page["stale"] is True
    assert page["narrative"] is None
    # 页面读取只标记过期，不提交生成任务、不调用模型。
    assert fake.calls == 1
    _jobs, total = manager.list(
        session_id=sid, job_types=(CLASS_ANALYSIS_JOB_TYPE,), limit=10
    )
    assert total == 1

    # 手动重新生成仍走缓存：同 (场次, 成绩版本, 叙述版本) 命中，不重复调用模型。
    job = _generate_via_api(client, sid)
    manager.wait(job["id"], timeout=5)
    assert fake.calls == 1
    assert (
        store.load(sid)["rendition_version"] == CLASS_ANALYSIS_RENDITION_VERSION
    )
    page = client.get(f"/api/sessions/{sid}/class-analysis").json()
    assert page["stale"] is False
    assert page["narrative"] is not None


# ---------------------------------------------------------------------------
# 复核确认占位词不得作为错因类别外显
# ---------------------------------------------------------------------------


def test_review_confirmed_marker_never_surfaces_as_error_type() -> None:
    """已复核/教师已确认等确认占位词在所有错因类别出口一律剔除。"""
    from types import SimpleNamespace

    from backend.error_causes import display_error_category
    from backend.repositories.results import _normalize_error_category
    from integration.mastery_adapter import _normalize_error_types
    from analysis_report_exporter import _record_brief_text
    from report import _loss_entry_label

    for marker in (
        "已复核",
        "人工复核",
        "人工复核已确认",
        "教师已确认",
        "教师已确认最终分",
        "manual_review_confirmed",
        "teacher_score_locked",
    ):
        assert display_error_category(marker) == "", marker
    assert display_error_category("概念理解错误") == "概念理解错误"
    assert display_error_category(None) == ""

    # 薄弱点聚合：占位词不当类别，退回按理由推断。
    assert _normalize_error_category("已复核", "漏写计算过程") == "计算错误"
    assert _normalize_error_category("教师已确认", "人工复核已确认") == "其他"
    # 掌握度错因列表：占位词剔除，真实类别保留。
    assert _normalize_error_types(["已复核", "答错"]) == ["答错"]
    # 班级叙述记录摘要与成绩表「主要错因」回退。
    record = SimpleNamespace(
        deduction_reason="", error_category="已复核", error_summary=""
    )
    assert _record_brief_text(record) == "未作答或无批改记录"
    assert _loss_entry_label({"error_category": "已复核"}) == "原因未记录"
