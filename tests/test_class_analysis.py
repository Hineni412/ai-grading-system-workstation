"""班级分析内嵌页：GET 三态、settings 持久化、「AI 整理」管线与自动触发。"""

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
    PERSONAL_NARRATIVE,
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


class PipelineClient(FakeLLMClient):
    """「AI 整理」管线用 fake：按请求内容分派错因分组、班级叙述与个人叙述。"""

    def __init__(self) -> None:
        super().__init__(narrative=CLASS_NARRATIVE)
        self.kinds: list[str] = []

    def _respond(self, prompt, images, extra_kwargs, kwargs):
        self.calls += 1
        self.requests.append(
            {"prompt": prompt, "images": images, "options": extra_kwargs, **kwargs}
        )
        if self._error is not None:
            raise self._error
        kind = self._classify(prompt)
        self.kinds.append(kind)
        if kind == "cause":
            source = json.loads(prompt.rsplit("\n", 1)[1])
            return {
                "groups": [
                    {
                        "kind": "error",
                        "category": "概念理解",
                        "reason": "垂直关系用错",
                        "manifestation": "未证垂直就用性质",
                        "evidence_ids": [
                            item["id"] for item in source["evidence"]
                        ],
                    }
                ],
                "positive_ids": [],
                "uncertain_ids": [],
            }
        return dict(CLASS_NARRATIVE if kind == "class" else PERSONAL_NARRATIVE)

    @staticmethod
    def _classify(prompt: str) -> str:
        try:
            payload = json.loads(prompt.rsplit("\n", 1)[1])
        except (IndexError, TypeError, ValueError):
            return "other"
        if "evidence" in payload:
            return "cause"
        if "student" in payload:
            return "personal"
        if "students" in payload:
            return "class"
        return "other"


class CauseFailClient(PipelineClient):
    """只有错因整理失败的管线 fake：班级与个人叙述照常返回。"""

    def _respond(self, prompt, images, extra_kwargs, kwargs):
        if self._classify(prompt) == "cause":
            self.calls += 1
            self.kinds.append("cause")
            self.requests.append(
                {
                    "prompt": prompt,
                    "images": images,
                    "options": extra_kwargs,
                    **kwargs,
                }
            )
            raise TimeoutError("synthetic cause failure")
        return super()._respond(prompt, images, extra_kwargs, kwargs)


@pytest.fixture
def class_analysis_api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_grading_db,
        get_job_manager,
        get_reports_dir,
        get_scan_grading_workspace,
        get_upload_config_dir,
    )
    from backend.jobs.default_handlers import _build_class_analysis_generate_handler
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from backend.scan_grading.workspace import ScanGradingWorkspace

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
    workspace = ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
        grading_db_path=db.db_path,
        data_root=tmp_path,
    )
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_reports_dir] = lambda: reports_dir
    app.dependency_overrides[get_upload_config_dir] = lambda: (
        tmp_path / "config" / "uploaded"
    )
    app.dependency_overrides[get_scan_grading_workspace] = lambda: workspace
    with TestClient(app) as client:
        try:
            yield client, db, session_id, reports_dir, manager, llm_holder, monkeypatch
        finally:
            manager.shutdown()


def _pipeline_via_api(client, session_id: int) -> dict:
    response = client.post(f"/api/sessions/{session_id}/report-pipeline")
    assert response.status_code == 202
    return response.json()


def _run_causes(db, session_id: int, data_root: Path, reports_dir: Path, client, **kwargs) -> dict:
    """直接跑「AI 整理」第一阶段错因整理（与管线同一实现），不经任务队列。"""
    from types import SimpleNamespace

    from backend.class_analysis import ClassAnalysisStateStore, run_cause_analysis

    context = SimpleNamespace(
        payload={"session_id": session_id},
        raise_if_cancelled=lambda: None,
        report=lambda *args, **kw: None,
    )
    return run_cause_analysis(
        context,
        db=db,
        data_root=data_root,
        store=ClassAnalysisStateStore(reports_dir),
        llm_client_factory=lambda: client,
        **kwargs,
    )


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
    _run_causes(db, sid, reports_dir.parent, reports_dir, fake)
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
    # 普通读取、切班、再次整理都复用；改批语只重整受影响题目。
    _run_causes(db, sid, reports_dir.parent, reports_dir, fake)
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
    _run_causes(db, sid, reports_dir.parent, reports_dir, fake)
    assert len(fake.calls) == 3
    # 图像编码不进入文本归并请求；实际评分要求变化会使该题归并过期。
    rubric_path = Path(db.sessions.get_grading_session(sid)["rubric_path"])
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric["questions"][1]["question_image_base64"] = "synthetic-image-payload"
    rubric["questions"][1]["proof_obligations"] = ["必须说明直角条件"]
    rubric_path.write_text(json.dumps(rubric, ensure_ascii=False), encoding="utf-8")
    changed = client.get(f"/api/sessions/{sid}/class-analysis?view=summary").json()
    assert changed["cause_analysis"]["pending_questions"] == 1
    _run_causes(db, sid, reports_dir.parent, reports_dir, fake)
    assert len(fake.calls) == 4
    assert "synthetic-image-payload" not in json.dumps(fake.calls[-1])


def test_cause_model_failure_does_not_retry_or_replace_original_reasons(
    class_analysis_api_client,
):
    client, db, sid, reports_dir, manager, holder, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, True)
    fake = FakeLLMClient(error=TimeoutError("synthetic timeout"))
    holder["client"] = fake
    _run_causes(db, sid, reports_dir.parent, reports_dir, fake)
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
        _run_causes(db, sid, reports_dir.parent, reports_dir, holder["client"])

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
    llm["client"] = PipelineClient()
    job = _pipeline_via_api(client, session_id)
    manager.wait(job["id"], timeout=10)
    # 管线顺序：错因整理（2 题）→ 班级叙述（2 班）→ 个人叙述（2 人）。
    assert llm["client"].kinds == [
        "cause", "cause", "class", "class", "personal", "personal"
    ]
    assert llm["client"].calls == 6
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


def test_report_pipeline_second_run_without_changes_makes_zero_model_calls(
    class_analysis_api_client,
) -> None:
    client, db, session_id, _reports_dir, manager, llm_holder, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, True)
    fake = PipelineClient()
    llm_holder["client"] = fake

    first = _pipeline_via_api(client, session_id)
    manager.wait(first["id"], timeout=10)
    assert manager.get(first["id"]).status == "succeeded"
    assert fake.calls == 5  # 错因 2 + 班级 1 + 个人 2。

    # 无成绩/错因变化时再次触发：三个阶段全部幂等跳过，零模型调用。
    from backend.class_analysis import submit_class_analysis_generate
    from backend.report_exports import score_revision

    second = submit_class_analysis_generate(
        manager=manager,
        session_id=session_id,
        revision=score_revision(db, session_id, include_question_bank=False),
        mode="auto",
    )
    manager.wait(second.id, timeout=10)
    result = manager.get(second.id).result
    assert result["kind"] == "pipeline" and result["status"] == "ready"
    assert fake.calls == 5


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
    mapped = student_error_map(state, lisi, sources, data)
    assert [row["pattern"] for row in mapped["Q2"]] == ["垂直关系用错"]
    # 批语变化 → 输入指纹变化 → 物化记录不再匹配，报告不展示旧归类。
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE session_details SET deduction_reason='复核后改判' WHERE question_id='Q2'"
        )
    changed = assemble_cause_data(db, sid, data_root=tmp_path)
    assert student_error_map(state, lisi, build_cause_inputs(changed), changed) == {}


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
    _run_causes(db, sid, reports_dir.parent, reports_dir, fake)
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
    _run_causes(db, sid, reports_dir.parent, reports_dir, fake)
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
    _run_causes(db, sid, reports_dir.parent, reports_dir, fake)
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
    _run_causes(db, sid, reports_dir.parent, reports_dir, holder["client"])
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


def test_session_error_records_and_category_counts(tmp_path: Path, monkeypatch) -> None:
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
    from backend.reporting.analysis_report_exporter import build_class_page_data
    

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
    # 多出没有已保存错因的学生时，不重新计算每份全班来源指纹，
    # 也不能把原学生的错因归给新增学生。
    from dataclasses import replace
    import backend.class_analysis as analysis
    expanded = replace(data, students=[*data.students, *[
        replace(data.students[0], student_id=9000 + index) for index in range(20)
    ]])
    fingerprints = []
    original_fingerprint = analysis._cause_input_fingerprint
    def counted_fingerprint(*args, **kwargs):
        fingerprints.append(True)
        return original_fingerprint(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(analysis, "assemble_cause_data", lambda *args, **kwargs: expanded)
        patch.setattr(analysis, "_cause_input_fingerprint", counted_fingerprint)
        assert session_error_records(db, sid, reports_dir) == records
    assert len(fingerprints) <= 8 * len(build_cause_inputs(expanded))
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
    job = _pipeline_via_api(client, sid)
    manager.wait(job["id"], timeout=10)
    fake = holder["client"]
    # Q1 错因 1 次 + 班级 1 次 + 个人 2 次；Q2 已有整理结果不再调用。
    assert fake.calls == 4
    class_payloads = [
        json.loads(request["prompt"].split("输入 JSON：\n", 1)[1])
        for request in fake.requests
        if "输入 JSON：\n" in request["prompt"]
    ]
    payload = next(p for p in class_payloads if "students" in p)
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
    fake = PipelineClient()
    holder["client"] = fake
    job = _pipeline_via_api(client, sid)
    manager.wait(job["id"], timeout=10)
    assert fake.calls == 5

    store = ClassAnalysisStateStore(reports_dir)
    state = store.load(sid)
    assert state["rendition_version"] == CLASS_ANALYSIS_RENDITION_VERSION
    # 模拟版本升级前的线上状态：就绪叙述、旧 rendition_version。
    store.save(sid, rendition_version="class_analysis_page_v3_class_scope")

    page = client.get(f"/api/sessions/{sid}/class-analysis").json()
    assert page["stale"] is True
    assert page["narrative"] is None
    # 页面读取只标记过期，不提交生成任务、不调用模型。
    assert fake.calls == 5
    _jobs, total = manager.list(
        session_id=sid, job_types=(CLASS_ANALYSIS_JOB_TYPE,), limit=10
    )
    assert total == 1

    # 手动「AI 整理」仍走缓存：同 (场次, 成绩版本, 叙述版本, 错因摘要) 命中，
    # 不重复调用模型。
    job = _pipeline_via_api(client, sid)
    manager.wait(job["id"], timeout=10)
    assert fake.calls == 5
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
    from backend.reporting.analysis_report_exporter import _record_brief_text
    from backend.reporting.report import _loss_entry_label

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


# ---------------------------------------------------------------------------
# 按步骤整理（v4）：独立扣分步骤单元、教师锁步骤、v3 兼容升级
# ---------------------------------------------------------------------------


def _seed_stepped_session(db, tmp_path: Path) -> int:
    """基础场次 + Q2 评分步骤；张三的批改证据带逐步评估（S3 沿用前步错误）。"""
    sid = _seed_analysis_session(db, tmp_path)
    _make_stepped(db, sid)
    return sid


def _make_stepped(db, sid: int) -> None:
    """把已播种场次的 Q2 改为按步骤评分，并给张三写入逐步评估证据。"""
    session = db.sessions.get_grading_session(sid)
    rubric_path = Path(session["rubric_path"])
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric["questions"][1].update({
        "source_evidence_version_id": "e" * 64,
        "parts": [{
            "part_id": "Q2",
            "part_score": 40,
            "steps": [
                {"step_id": "S1", "step_score": 10, "core_goal": "设未知数",
                 "evidence_point_ids": ["p1"]},
                {"step_id": "S2", "step_score": 10, "core_goal": "列等量关系",
                 "evidence_point_ids": ["p2"]},
                {"step_id": "S3", "step_score": 10, "core_goal": "求解方程",
                 "evidence_point_ids": ["p3"]},
                {"step_id": "S4", "step_score": 10, "core_goal": "写出结论",
                 "evidence_point_ids": ["p4a", "p4b"]},
            ],
        }],
    })
    rubric_path.write_text(json.dumps(rubric, ensure_ascii=False), encoding="utf-8")
    with sqlite3.connect(db.db_path) as conn:
        result_id = conn.execute(
            "SELECT r.id FROM session_results r JOIN students s ON s.id = r.student_id"
            " WHERE r.session_id = ? AND s.name = '张三'",
            (sid,),
        ).fetchone()[0]
        raw = {
            "grading_completeness": {"status": "complete"},
            "detail_metadata": {"Q2": {
                "observed_answer": "张三的证明作答",
                "step_assessments": [
                    {"step_id": "S1", "part_id": "Q2", "achievement": "full",
                     "score_awarded": 10},
                    {"step_id": "S2", "part_id": "Q2", "achievement": "none",
                     "score_awarded": 0, "reason": "未列出等量关系"},
                    {"step_id": "S3", "part_id": "Q2", "achievement": "none",
                     "score_awarded": 0, "carried_error_from": "S2",
                     "reason": "沿用前步错误方程"},
                    {"step_id": "S4", "part_id": "Q2", "achievement": "partial",
                     "score_awarded": 0, "missing_or_error": "未写结论"},
                ],
            }},
        }
        conn.execute(
            "UPDATE session_results SET raw_json = ? WHERE id = ?",
            (json.dumps(raw, ensure_ascii=False), result_id),
        )


def test_cause_inputs_split_independently_failed_steps(tmp_path: Path) -> None:
    """by_step 输入把独立扣分的步骤拆为单元；沿用前步错误的步骤不单列。"""
    import backend.jobs
    from backend.class_analysis import assemble_cause_data, build_cause_inputs

    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    sid = _seed_stepped_session(db, tmp_path)
    data = assemble_cause_data(db, sid, data_root=tmp_path)

    source = _cause_source(data, "Q2")
    stepped = next(item for item in source["evidence"] if item.get("failed_steps"))
    assert [unit["step_id"] for unit in stepped["failed_steps"]] == ["S2", "S4"]
    assert [unit["id"] for unit in stepped["failed_steps"]] == [
        f"{stepped['id']}.S1",
        f"{stepped['id']}.S2",
    ]
    first = stepped["failed_steps"][0]
    assert first["core_goal"] == "列等量关系" and first["step_score"] == 10
    assert first["part_id"] == "Q2" and first["reason"] == "未列出等量关系"

    # by_step=False 复现旧输入形状；无步骤证据在两种口径下完全一致。
    old_source = next(
        item for item in build_cause_inputs(data, by_step=False)
        if item["question_id"] == "Q2"
    )
    assert all("failed_steps" not in item for item in old_source["evidence"])
    assert len(old_source["evidence"]) == len(source["evidence"])
    # 无步骤证据在两种口径下的条目内容完全一致（id 是位置序号，不计内容）。
    plain = next(item for item in source["evidence"] if not item.get("failed_steps"))
    assert {k: v for k, v in plain.items() if k != "id"} in [
        {k: v for k, v in item.items() if k != "id"}
        for item in old_source["evidence"]
    ]
    stripped = {
        key: [{k: v for k, v in item.items() if k != "failed_steps"}
              for item in source["evidence"]]
        if key == "evidence" else value
        for key, value in source.items()
    }
    assert sorted(
        json.dumps({k: v for k, v in item.items() if k != "id"},
                   ensure_ascii=False, sort_keys=True)
        for item in old_source["evidence"]
    ) == sorted(
        json.dumps({k: v for k, v in item.items() if k != "id"},
                   ensure_ascii=False, sort_keys=True)
        for item in stripped["evidence"]
    )
    assert {k: v for k, v in old_source.items() if k != "evidence"} == {
        k: v for k, v in stripped.items() if k != "evidence"
    }


def test_teacher_locked_answer_uses_review_steps_only(tmp_path: Path) -> None:
    """教师锁定题只用校验通过的教师逐步记录；校验失败按整题处理。"""
    import backend.jobs
    from backend.class_analysis import assemble_cause_data, build_cause_inputs

    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    sid = _seed_stepped_session(db, tmp_path)
    with sqlite3.connect(db.db_path) as conn:
        student_id, result_id = conn.execute(
            "SELECT s.id, r.id FROM session_results r JOIN students s ON s.id = r.student_id"
            " WHERE r.session_id = ? AND s.name = '李四'",
            (sid,),
        ).fetchone()
        conn.execute(
            "INSERT INTO teacher_score_locks (session_id, scan_batch_id, student_id,"
            " question_id, score_awarded, max_score, deduction_reason,"
            " source_target_type, source_target_id, revision)"
            " VALUES (?, 'batch-1', ?, 'Q2', 20, 40, '教师扣分', 'manual', 1, 1)",
            (sid, student_id),
        )
        review = {
            "revision": 1,
            "scan_batch_id": "batch-1",
            "score_awarded": 20,
            "steps": [
                {"step_id": "S1", "part_id": "Q2", "score_awarded": 10,
                 "max_score": 10, "evidence_point_ids": ["p1"]},
                {"step_id": "S2", "part_id": "Q2", "score_awarded": 0,
                 "max_score": 10, "evidence_point_ids": ["p2"], "reason": "关系缺失"},
                {"step_id": "S3", "part_id": "Q2", "score_awarded": 10,
                 "max_score": 10, "evidence_point_ids": ["p3"]},
                {"step_id": "S4", "part_id": "Q2", "score_awarded": 0,
                 "max_score": 10, "evidence_point_ids": ["p4a", "p4b"]},
            ],
        }
        raw = {
            "grading_completeness": {"status": "complete"},
            "detail_metadata": {"Q2": {
                "observed_answer": "李四的证明作答",
                "step_assessments": [
                    {"step_id": "S1", "part_id": "Q2", "achievement": "none",
                     "score_awarded": 0, "reason": "AI 认为 S1 失败"},
                ],
            }},
            "teacher_reviews": {"Q2": review},
        }
        conn.execute(
            "UPDATE session_results SET raw_json = ? WHERE id = ?",
            (json.dumps(raw, ensure_ascii=False), result_id),
        )

    def stepped_units() -> list[dict]:
        data = assemble_cause_data(db, sid, data_root=tmp_path)
        source = _cause_source(data, "Q2")
        lisi = next(
            item for item in source["evidence"]
            if item["student_answer"] == "李四的证明作答"
        )
        return lisi.get("failed_steps") or []

    # 教师步骤校验通过：AI 评估（S1）被忽略，只保留教师判定的失分步。
    assert [unit["step_id"] for unit in stepped_units()] == ["S2", "S4"]

    for source, note, expected_ids in [
        ("teacher", None, ["S2"]), ("teacher", "教师核实的错误", ["S2", "S4"]),
        ("ai", None, ["S2", "S4"]),
    ]:
        updated = json.loads(json.dumps(raw))
        target = updated["teacher_reviews"]["Q2"]["steps"][3]
        target.update(deduction_source=source, teacher_note=note, reason="AI 原理由", missing_or_error="AI 缺漏")
        with sqlite3.connect(db.db_path) as conn:
            conn.execute("UPDATE session_results SET raw_json=? WHERE id=?", (json.dumps(updated), result_id))
        units = stepped_units()
        assert [unit["step_id"] for unit in units] == expected_ids
        if note:
            assert units[-1]["reason"] == note
        if source == "ai":
            assert units[-1]["missing_or_error"] == "AI 缺漏"
    updated["teacher_reviews"]["Q2"]["steps"][3]["carried_error_from"] = "S2"
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE session_results SET raw_json=? WHERE id=?", (json.dumps(updated), result_id))
    assert [unit["step_id"] for unit in stepped_units()] == ["S2"]
    for step in updated["teacher_reviews"]["Q2"]["steps"]:
        step["deduction_source"] = "teacher" if step["score_awarded"] == 0 else "none"
        step.pop("teacher_note", None)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE session_results SET raw_json=? WHERE id=?", (json.dumps(updated), result_id))
    data = assemble_cause_data(db, sid, data_root=tmp_path)
    source = _cause_source(data, "Q2")
    assert all(entry["student_answer"] != "李四的证明作答" for entry in source["evidence"])
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE session_results SET raw_json=? WHERE id=?", (json.dumps(raw), result_id))

    # 修订号对不上当前最终分锁：教师记录不可用，AI 步骤也不得回退使用。
    with sqlite3.connect(db.db_path) as conn:
        row = conn.execute(
            "SELECT raw_json FROM session_results WHERE id = ?", (result_id,)
        ).fetchone()
        stale = json.loads(row[0])
        stale["teacher_reviews"]["Q2"]["revision"] = 999
        conn.execute(
            "UPDATE session_results SET raw_json = ? WHERE id = ?",
            (json.dumps(stale, ensure_ascii=False), result_id),
        )
    assert stepped_units() == []


def test_normalize_cause_result_assigns_step_units() -> None:
    """步骤单元按组引用归属；未分配单元自动待核对；模型 step_id 被忽略。"""
    from backend.class_analysis import normalize_cause_result

    source = {
        "question_id": "Q2",
        "evidence": [
            {"id": "E1", "text": "批语", "student_answer": "作答",
             "failed_steps": [
                 {"id": "E1.S1", "step_id": "S2", "part_id": "Q2"},
                 {"id": "E1.S2", "step_id": "S4", "part_id": "Q2"},
             ]},
            {"id": "E2", "text": "另一批语", "student_answer": "另一作答"},
        ],
        "known_patterns": [],
    }
    result = normalize_cause_result(
        {
            "groups": [{
                "kind": "process", "category": "过程与依据",
                "reason": "缺少等量关系", "manifestation": "未列关系式",
                "evidence_ids": ["E1.S1", "E2"],
                "step_id": "S9",
            }],
        },
        source,
    )
    group = result["groups"][0]
    assert group["evidence_ids"] == ["E1.S1", "E2"]
    assert group["step_ids"] == ["S2"]
    assert "step_id" not in group
    # 未分配的步骤单元自动进待核对；整条 E1 因其单元被引用视为已覆盖。
    assert result["uncertain_ids"] == ["E1.S2"]
    assert result["positive_ids"] == []

    bare = normalize_cause_result(
        {
            "groups": [{
                "kind": "error", "category": "概念理解",
                "reason": "概念用错", "manifestation": "用错性质",
                "evidence_ids": ["E1"],
            }],
            "uncertain_ids": ["E2"],
        },
        source,
    )
    assert bare["groups"][0]["evidence_ids"] == ["E1"]
    assert bare["groups"][0]["step_ids"] == []
    assert bare["uncertain_ids"] == ["E1.S1", "E1.S2", "E2"]


def test_step_aware_error_records_and_counts(tmp_path: Path) -> None:
    """步骤单元各物化一行；大类计数仍按学生去重。"""
    import backend.jobs
    from backend.reporting.analysis_report_exporter import build_class_page_data
    from backend.class_analysis import (
        ClassAnalysisStateStore,
        apply_cause_results,
        assemble_cause_data,
        build_cause_inputs,
        question_category_counts,
        save_cause_result,
        session_error_records,
    )

    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    sid = _seed_stepped_session(db, tmp_path)
    reports_dir = tmp_path / "reports"
    store = ClassAnalysisStateStore(reports_dir)
    data = assemble_cause_data(db, sid, data_root=tmp_path)
    source = _cause_source(data, "Q2")
    stepped = next(item for item in source["evidence"] if item.get("failed_steps"))
    plain = next(item for item in source["evidence"] if not item.get("failed_steps"))
    unit_ids = [unit["id"] for unit in stepped["failed_steps"]]
    save_cause_result(
        store,
        sid,
        source,
        {
            "groups": [
                {
                    "kind": "process", "category": "过程与依据",
                    "reason": "缺少等量关系", "manifestation": "未列关系式",
                    "evidence_ids": [unit_ids[0], plain["id"]],
                },
                {
                    "kind": "process", "category": "书写与规范",
                    "reason": "结论不规范", "manifestation": "未写结论",
                    "evidence_ids": [unit_ids[1]],
                },
            ],
        },
        data=data,
    )
    records = session_error_records(db, sid, reports_dir)
    zhangsan = next(s for s in data.students if s.student_name == "张三")
    lisi = next(s for s in data.students if s.student_name == "李四")
    rows = records[zhangsan.student_id]["Q2"]
    assert {(row["step_id"], row["part_id"]) for row in rows} == {
        ("S2", "Q2"), ("S4", "Q2"),
    }
    by_step = {row["step_id"]: row for row in rows}
    assert by_step["S2"]["evidence_point_ids"] == ["p2"]
    assert by_step["S4"]["evidence_point_ids"] == ["p4a", "p4b"]
    assert all(row["evidence_version_id"] == "e" * 64 for row in rows)
    assert {row["pattern"] for row in records[lisi.student_id]["Q2"]} == {"缺少等量关系"}
    # 张三在“过程与依据”下有两条步骤行，按学生只计一次。
    counts = question_category_counts(records)
    assert counts["Q2"] == [("过程与依据", 2), ("书写与规范", 1)]
    state = store.load(sid)
    page = build_class_page_data(data, compact=True)
    status = apply_cause_results(page, data, build_cause_inputs(data), state)
    q2 = next(q for q in page["questions"] if q["question_id"] == "Q2")
    assert status["pending_questions"] == 1 and status["pre_step_questions"] == 0
    assert q2["causes_by_step"] is True
    assert {
        (cause["reason"], tuple(cause["step_ids"])) for cause in q2["causes"]
    } == {("缺少等量关系", ("S2",)), ("结论不规范", ("S4",))}


def test_pre_step_v3_results_display_then_upgrade(tmp_path: Path) -> None:
    """v3 旧结果继续显示但不计入就绪；手动整理升级，报告前置不调用模型。"""
    import backend.jobs
    from types import SimpleNamespace

    from backend.reporting.analysis_report_exporter import build_class_page_data
    from backend.class_analysis import (
        CAUSE_PRE_STEP_VERSION,
        ClassAnalysisStateStore,
        _cause_input_fingerprint,
        apply_cause_results,
        assemble_cause_data,
        build_cause_inputs,
        normalize_cause_result,
        run_cause_analysis,
        student_error_map,
        student_error_records,
    )

    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    sid = _seed_stepped_session(db, tmp_path)
    store = ClassAnalysisStateStore(tmp_path / "reports")
    data = assemble_cause_data(db, sid, data_root=tmp_path)
    sources = build_cause_inputs(data)
    old_sources = build_cause_inputs(data, by_step=False)
    questions: dict[str, dict] = {}
    error_records: dict[str, dict] = {}
    for old in old_sources:
        result = normalize_cause_result(
            {
                "groups": [{
                    "kind": "error", "category": "概念理解",
                    "reason": "旧错法", "manifestation": "旧表现",
                    "evidence_ids": [item["id"] for item in old["evidence"]],
                }],
            },
            old,
        )
        fingerprint = _cause_input_fingerprint(old)
        questions[old["question_id"]] = {
            "version": CAUSE_PRE_STEP_VERSION, "input": old,
            "input_fingerprint": fingerprint, "result": result,
            "generated_at": "2024-01-01T00:00:00", "origin": "model",
            "failed": False,
        }
        error_records[old["question_id"]] = {
            "input_fingerprint": fingerprint,
            "generated_at": "2024-01-01T00:00:00",
            "records": student_error_records(data, old, result, by_step=False),
        }
    store.save(sid, cause_analysis={"questions": questions},
               error_records=error_records)

    # Q1 无评分步骤：v3 输入与 v4 相同，旧结果直接视为新鲜。
    # Q2 有失败步骤：v3 结果按兼容输入展示，标记按步骤整理前的结果。
    page = build_class_page_data(data, compact=True)
    status = apply_cause_results(page, data, sources, store.load(sid))
    assert status["status"] == "partial"
    assert status["pre_step_questions"] == 1
    by_id = {q["question_id"]: q for q in page["questions"]}
    # Q1 无评分步骤：v3 结果直接视为新鲜的按步骤口径结果。
    assert by_id["Q1"]["causes_by_step"] is True
    assert by_id["Q1"]["causes"]  # v3 结果照常显示
    assert by_id["Q2"]["causes_by_step"] is False
    assert by_id["Q2"]["causes"]
    lisi = next(s for s in data.students if s.student_name == "李四")
    mapped = student_error_map(store.load(sid), lisi, sources, data)
    assert mapped["Q2"] and mapped["Q2"][0]["pattern"] == "旧错法"
    # 按 v3 口径物化的记录保留其版本标记。
    assert mapped["Q2"][0]["version"] == CAUSE_PRE_STEP_VERSION

    context = SimpleNamespace(
        payload={"session_id": sid},
        raise_if_cancelled=lambda: None,
        report=lambda *args, **kwargs: None,
    )
    calls: list[str] = []
    client = SimpleNamespace(
        json_from_text=lambda *args, **kwargs: calls.append(args[0]) or {
            "groups": [], "positive_ids": [], "uncertain_ids": [],
        }
    )
    # 报告前置阶段：兼容的 v3 结果不为升级而调用模型。
    outcome = run_cause_analysis(
        context, db=db, data_root=tmp_path, store=store,
        llm_client_factory=lambda: client,
        retry_failed=False, upgrade_pre_step=False,
    )
    assert outcome["status"] == "ready" and calls == []
    # 手动整理：v3 兼容结果重发并升级为 v4。
    outcome = run_cause_analysis(
        context, db=db, data_root=tmp_path, store=store,
        llm_client_factory=lambda: client,
        retry_failed=False,
    )
    assert outcome["status"] == "ready" and len(calls) == 1
    saved = store.load(sid)["cause_analysis"]["questions"]
    assert saved["Q2"]["version"] == "class_error_causes_v4"
    assert saved["Q1"]["version"] == CAUSE_PRE_STEP_VERSION  # 无步骤题无需重发


def test_pre_step_source_dedupes_evidence_collapsed_by_step_split(
    tmp_path: Path,
) -> None:
    """两名学生作答与批语相同但失败步骤不同：v4 证据分两条、v3 并一条，
    旧 v3 结果仍按兼容输入识别、展示，并为两人返回错因记录。"""
    import backend.jobs

    from backend.reporting.analysis_report_exporter import build_class_page_data
    from backend.class_analysis import (
        CAUSE_PRE_STEP_VERSION,
        ClassAnalysisStateStore,
        _cause_input_fingerprint,
        apply_cause_results,
        assemble_cause_data,
        build_cause_inputs,
        normalize_cause_result,
        student_error_map,
        student_error_records,
    )

    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    sid = _seed_stepped_session(db, tmp_path)
    with sqlite3.connect(db.db_path) as conn:
        result_id = conn.execute(
            "SELECT r.id FROM session_results r JOIN students s ON s.id = r.student_id"
            " WHERE r.session_id = ? AND s.name = '李四'",
            (sid,),
        ).fetchone()[0]
        # 前一题不留错因痕迹、作答文字与张三相同，只有失败步骤不同。
        conn.execute(
            "UPDATE session_details SET score_awarded = 60, deduction_reason = NULL,"
            " error_category = NULL, error_summary = NULL"
            " WHERE result_id = ? AND question_id = 'Q1'",
            (result_id,),
        )
        raw = {
            "grading_completeness": {"status": "complete"},
            "detail_metadata": {"Q2": {
                "observed_answer": "张三的证明作答",
                "step_assessments": [
                    {"step_id": "S1", "part_id": "Q2", "achievement": "full",
                     "score_awarded": 10},
                    {"step_id": "S2", "part_id": "Q2", "achievement": "full",
                     "score_awarded": 10},
                    {"step_id": "S3", "part_id": "Q2", "achievement": "full",
                     "score_awarded": 10},
                    {"step_id": "S4", "part_id": "Q2", "achievement": "none",
                     "score_awarded": 0, "reason": "未写结论"},
                ],
            }},
        }
        conn.execute(
            "UPDATE session_results SET raw_json = ? WHERE id = ?",
            (json.dumps(raw, ensure_ascii=False), result_id),
        )

    data = assemble_cause_data(db, sid, data_root=tmp_path)
    source = next(item for item in build_cause_inputs(data)
                  if item["question_id"] == "Q2")
    assert len(source["evidence"]) == 2
    old = next(item for item in build_cause_inputs(data, by_step=False)
               if item["question_id"] == "Q2")
    assert len(old["evidence"]) == 1

    result = normalize_cause_result(
        {"groups": [{
            "kind": "error", "category": "概念理解",
            "reason": "旧错法", "manifestation": "旧表现",
            "evidence_ids": [old["evidence"][0]["id"]],
        }]},
        old,
    )
    fingerprint = _cause_input_fingerprint(old)
    store = ClassAnalysisStateStore(tmp_path / "reports")
    store.save(
        sid,
        cause_analysis={"questions": {"Q2": {
            "version": CAUSE_PRE_STEP_VERSION, "input": old,
            "input_fingerprint": fingerprint, "result": result,
            "generated_at": "2024-01-01T00:00:00", "origin": "model",
            "failed": False,
        }}},
        error_records={"Q2": {
            "input_fingerprint": fingerprint,
            "generated_at": "2024-01-01T00:00:00",
            "records": student_error_records(data, old, result, by_step=False),
        }},
    )

    page = build_class_page_data(data, compact=True)
    status = apply_cause_results(page, data, [source], store.load(sid))
    assert status["pre_step_questions"] == 1
    q2 = next(q for q in page["questions"] if q["question_id"] == "Q2")
    assert q2["causes_by_step"] is False and q2["causes"]

    state = store.load(sid)
    for name in ("张三", "李四"):
        student = next(s for s in data.students if s.student_name == name)
        mapped = student_error_map(state, student, [source], data)
        assert mapped["Q2"] and mapped["Q2"][0]["pattern"] == "旧错法"
        assert mapped["Q2"][0]["version"] == CAUSE_PRE_STEP_VERSION


def test_session_error_record_skill_enrichment_is_nonfatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """物化记录补充技能链接；补充失败时记录完整保留。"""
    import backend.jobs
    from backend.class_analysis import (
        ClassAnalysisStateStore,
        assemble_cause_data,
        normalize_cause_result,
        save_cause_result,
        session_error_records,
    )
    from question_bank.database.schema import connect, initialize_database

    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    sid = _seed_stepped_session(db, tmp_path)
    store = ClassAnalysisStateStore(tmp_path / "reports")
    data = assemble_cause_data(db, sid, data_root=tmp_path)
    source = _cause_source(data, "Q2")
    stepped = next(item for item in source["evidence"] if item.get("failed_steps"))
    plain = next(item for item in source["evidence"] if not item.get("failed_steps"))
    unit_ids = [unit["id"] for unit in stepped["failed_steps"]]
    save_cause_result(
        store, sid, source,
        {"groups": [{
            "kind": "process", "category": "过程与依据",
            "reason": "缺少等量关系", "manifestation": "未列关系式",
            "evidence_ids": [*unit_ids, plain["id"]],
        }]},
        data=data,
    )

    bank_path = tmp_path / "databases" / "question_bank.db"
    initialize_database(bank_path)
    from tests.current_knowledge_support import install_current_knowledge
    from question_bank.solution_evidence.knowledge_links import replace_point_links

    release_id = install_current_knowledge(bank_path, taxonomy_revision=9)
    with connect(bank_path) as conn:
        conn.execute(
            "INSERT INTO questions (question_number, question_text, answer_text,"
            " question_type) VALUES ('2', '题干', '答案', '解答题')"
        )
        conn.execute(
            """
            INSERT INTO question_solution_evidence_versions(
                evidence_version_id, question_id, source_content_hash,
                schema_version, content_hash, evidence_json, status,
                source_kind, source_reference, created_by, graph_release_id
            ) VALUES ('eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', 1, ?, 'question-solution-evidence-v2', ?, '{}',
                      'approved', 'backfill', 'synthetic', 'test', ?)
            """,
            ("b" * 64, "c" * 64, release_id),
        )
        replace_point_links(
            conn, evidence_version_id="e" * 64, question_id=1,
            graph_release_id=release_id,
            points=[{
                "part_id": "Q2", "evidence_point_id": "p2",
                "links": [{"term_id": "sk_bnu24_math_g7_lower_1_1_01",
                           "stable_key": "sk_bnu24_math_g7_lower_1_1_01",
                           "role": "direct"}],
            }],
        )

    records = session_error_records(db, sid, tmp_path / "reports")
    zhangsan = next(s for s in data.students if s.student_name == "张三")
    rows = {row["step_id"]: row for row in records[zhangsan.student_id]["Q2"]}
    assert rows["S2"]["skill_keys"] == ["sk_bnu24_math_g7_lower_1_1_01"]
    assert rows["S2"]["skill_basis"] == "step"
    # S4 的判定点无直达技能 → step 行的技能列表为空。
    assert rows["S4"]["skill_keys"] == [] and rows["S4"]["skill_basis"] == "step"
    lisi = next(s for s in data.students if s.student_name == "李四")
    lisi_row = records[lisi.student_id]["Q2"][0]
    # 本题全部判定点只解析出唯一技能 → 题级记录归入该技能。
    assert lisi_row["skill_keys"] == ["sk_bnu24_math_g7_lower_1_1_01"]
    assert lisi_row["skill_basis"] == "question"

    import question_bank.solution_evidence.knowledge_links as links_module
    monkeypatch.setattr(
        links_module, "load_point_links",
        lambda *args, **kwargs: (_ for _ in ()).throw(sqlite3.Error("boom")),
    )
    fallback = session_error_records(db, sid, tmp_path / "reports")
    assert {
        sid_: {qid: len(rows) for qid, rows in by_q.items()}
        for sid_, by_q in fallback.items()
    } == {
        sid_: {qid: len(rows) for qid, rows in by_q.items()}
        for sid_, by_q in records.items()
    }
    assert all("skill_keys" not in row for rows_ in fallback.values()
               for rs in rows_.values() for row in rs)


# ---------------------------------------------------------------------------
# 「AI 整理」统一管线：状态预检、手动/自动口径、占位渲染与自动触发
# ---------------------------------------------------------------------------


def test_report_pipeline_status_counts_then_complete(
    class_analysis_api_client,
) -> None:
    """状态预检：错因待整理时全部班级记为待生成；管线跑完后 complete=true。"""
    client, db, sid, _reports_dir, manager, holder, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, True)
    holder["client"] = PipelineClient()

    status = client.get(f"/api/sessions/{sid}/report-pipeline").json()
    assert status["configured"] is True
    assert status["active_job_id"] is None
    assert status["causes"]["pending_questions"] == 2
    assert status["causes"]["total_questions"] == 2
    assert status["causes"]["call_count"] == 2
    assert status["causes"]["estimated_tokens"] > 0
    # 错因摘要随整理结果变化：有错因待整理时全部有学生的班记为待做。
    assert status["class_reports"] == {"pending": 1, "total": 1}
    assert status["personal_reports"]["pending"] == 2
    assert status["personal_reports"]["total"] == 2
    assert status["personal_reports"]["call_count"] == 2
    assert status["complete"] is False

    job = _pipeline_via_api(client, sid)
    manager.wait(job["id"], timeout=10)
    assert manager.get(job["id"]).status == "succeeded"

    done = client.get(f"/api/sessions/{sid}/report-pipeline").json()
    assert done["complete"] is True
    assert done["causes"]["pending_questions"] == 0
    assert done["class_reports"]["pending"] == 0
    assert done["personal_reports"]["pending"] == 0
    assert done["personal_reports"]["call_count"] == 0


def test_report_pipeline_post_validates_and_reuses_active_job(
    class_analysis_api_client,
) -> None:
    """POST：无成绩 409、未配置模型 422；进行中任务直接复用同一 job。"""
    client, db, sid, _reports_dir, manager, holder, monkeypatch = (
        class_analysis_api_client
    )
    empty = db.sessions.create_grading_session("空场", "r.json", "a.json")
    empty_response = client.post(f"/api/sessions/{empty}/report-pipeline")
    assert empty_response.status_code == 409
    assert empty_response.json()["error"]["code"] == "report_results_missing"

    _patch_configured(monkeypatch, False)
    unconfigured = client.post(f"/api/sessions/{sid}/report-pipeline")
    assert unconfigured.status_code == 422
    assert (
        unconfigured.json()["error"]["code"]
        == "content_generation_model_not_configured"
    )

    _patch_configured(monkeypatch, True)
    blocker = BlockingLLMClient()
    holder["client"] = blocker
    first = _pipeline_via_api(client, sid)
    assert blocker.started.wait(3)
    second = client.post(f"/api/sessions/{sid}/report-pipeline")
    assert second.status_code == 202
    assert second.json()["id"] == first["id"]  # 进行中 job 直接复用
    manager.cancel(first["id"])
    blocker.release.set()
    manager.wait(first["id"], timeout=5)


def test_report_pipeline_manual_retries_failed_causes_but_auto_does_not(
    class_analysis_api_client,
) -> None:
    """手动口径重发失败题并升级为就绪；自动口径跳过失败题、整体记 partial。"""
    client, db, sid, reports_dir, manager, holder, monkeypatch = (
        class_analysis_api_client
    )
    from backend.class_analysis import (
        ClassAnalysisStateStore,
        submit_class_analysis_generate,
    )
    from backend.report_exports import score_revision

    _patch_configured(monkeypatch, True)
    holder["client"] = FakeLLMClient(error=TimeoutError("synthetic cause failure"))
    _run_causes(db, sid, reports_dir.parent, reports_dir, holder["client"])
    store = ClassAnalysisStateStore(reports_dir)
    state = store.load(sid)
    assert all(
        q["failed"] for q in state["cause_analysis"]["questions"].values()
    )

    good = PipelineClient()
    holder["client"] = good
    revision = score_revision(db, sid, include_question_bank=False)
    auto = submit_class_analysis_generate(
        manager=manager, session_id=sid, revision=revision, mode="auto",
    )
    manager.wait(auto.id, timeout=10)
    # 自动口径不重发失败题：错因未就绪 → 班级无摘要 → 叙述用无错因键生成。
    assert good.kinds == ["class", "personal", "personal"]
    saved = store.load(sid)["cause_analysis"]["questions"]
    assert all(q["failed"] for q in saved.values())
    # 本轮未产生失败调用，自动口径下任务自身记 ready；失败标记留给手动重试。
    assert manager.get(auto.id).result["status"] == "ready"

    good.kinds.clear()
    manual = client.post(f"/api/sessions/{sid}/report-pipeline")
    assert manual.status_code == 202
    manager.wait(manual.json()["id"], timeout=10)
    assert good.kinds[:2] == ["cause", "cause"]  # 手动口径重发失败题
    saved = store.load(sid)["cause_analysis"]["questions"]
    assert not any(q["failed"] for q in saved.values())
    assert manager.get(manual.json()["id"]).result["status"] == "ready"


def test_report_pipeline_manual_upgrades_pre_step_but_auto_keeps(
    class_analysis_api_client, tmp_path: Path,
) -> None:
    """v3 兼容结果：自动保留不调用，手动升级重发。"""
    from backend.class_analysis import (
        CAUSE_PRE_STEP_VERSION,
        ClassAnalysisStateStore,
        _cause_input_fingerprint,
        _pre_step_source,
        assemble_cause_data,
        build_cause_inputs,
        normalize_cause_result,
        save_cause_result,
        student_error_records,
    )

    client, db, sid, reports_dir, manager, holder, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, True)
    _make_stepped(db, sid)  # 只有带评分步骤的题才有 v3→v4 口径差异
    store = ClassAnalysisStateStore(reports_dir)
    data = assemble_cause_data(db, sid, data_root=reports_dir.parent)
    source = next(
        s for s in build_cause_inputs(data) if s["question_id"] == "Q2"
    )
    old = _pre_step_source(source)
    result = normalize_cause_result(
        {
            "groups": [{
                "kind": "error", "category": "概念理解",
                "reason": "旧错法", "manifestation": "旧表现",
                "evidence_ids": [item["id"] for item in old["evidence"]],
            }],
        },
        old,
    )
    fingerprint = _cause_input_fingerprint(old)
    store.save(
        sid,
        cause_analysis={
            "questions": {
                "Q2": {
                    "version": CAUSE_PRE_STEP_VERSION,
                    "input": old,
                    "input_fingerprint": fingerprint,
                    "result": result,
                    "generated_at": "2024-01-01T00:00:00",
                    "origin": "model",
                    "failed": False,
                }
            }
        },
        error_records={
            "Q2": {
                "input_fingerprint": fingerprint,
                "generated_at": "2024-01-01T00:00:00",
                "records": student_error_records(data, old, result, by_step=False),
            }
        },
    )

    fake = PipelineClient()
    holder["client"] = fake
    # 自动口径：v3 兼容结果不升级，只补缺失的 Q1。
    from backend.class_analysis import submit_class_analysis_generate
    from backend.report_exports import score_revision

    auto = submit_class_analysis_generate(
        manager=manager,
        session_id=sid,
        revision=score_revision(db, sid, include_question_bank=False),
        mode="auto",
    )
    manager.wait(auto.id, timeout=10)
    saved = store.load(sid)["cause_analysis"]["questions"]
    assert saved["Q2"]["version"] == CAUSE_PRE_STEP_VERSION
    assert fake.kinds.count("cause") == 1  # 只有缺失的 Q1

    # 手动口径：v3 兼容结果重发并升级为 v4；错因变化顺带重生成班级叙述。
    fake.kinds.clear()
    manual = _pipeline_via_api(client, sid)
    manager.wait(manual["id"], timeout=10)
    saved = store.load(sid)["cause_analysis"]["questions"]
    assert saved["Q2"]["version"] == "class_error_causes_v4"
    assert fake.kinds == ["cause", "class"]


def test_report_pipeline_personal_targets_only_missing_and_stale(
    class_analysis_api_client,
) -> None:
    """成绩变化只让受影响学生的个人报告过期；已当前的报告不再生成。"""
    client, db, sid, reports_dir, manager, holder, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, True)
    fake = PipelineClient()
    holder["client"] = fake
    job = _pipeline_via_api(client, sid)
    manager.wait(job["id"], timeout=10)
    calls_before = list(fake.kinds)

    lisi_id = next(
        s["student_id"]
        for s in client.get(f"/api/sessions/{sid}/personal-reports").json()["students"]
        if s["status"] == "current"
    )
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE session_results SET total_score=total_score+5"
            " WHERE session_id=? AND student_id=?",
            (sid, lisi_id),
        )
    states = client.get(f"/api/sessions/{sid}/personal-reports").json()["students"]
    by_status = {
        s["student_id"]: s["status"] for s in states if s["status"] != "unavailable"
    }
    assert by_status[lisi_id] == "stale"
    other = next(sid_ for sid_, st in by_status.items() if sid_ != lisi_id)
    assert by_status[other] == "current"

    from backend.reporting.analysis_report_exporter import AnalysisReportGenerator

    captured: dict[str, object] = {}
    original_export = AnalysisReportGenerator.export_session

    def spy_export(self, session_id, report_type, **kwargs):
        captured["student_ids"] = set(kwargs.get("student_ids") or set())
        return original_export(self, session_id, report_type, **kwargs)

    monkeypatch.setattr(
        AnalysisReportGenerator, "export_session", spy_export
    )
    fake.kinds.clear()
    second = _pipeline_via_api(client, sid)
    manager.wait(second["id"], timeout=10)
    # 只有过期学生重新生成；已当前的学生保留原叙述。
    assert captured["student_ids"] == {lisi_id}
    assert fake.kinds.count("personal") == 1


def test_report_pipeline_not_configured_makes_zero_calls(
    class_analysis_api_client,
) -> None:
    """任务内的未配置兜底：写 not_configured 状态，返回不产生调用。"""
    client, db, sid, reports_dir, manager, holder, monkeypatch = (
        class_analysis_api_client
    )
    from backend.class_analysis import ClassAnalysisStateStore

    _patch_configured(monkeypatch, True)  # 预检放行，任务内工厂返回 None
    holder["client"] = None
    job = _pipeline_via_api(client, sid)
    manager.wait(job["id"], timeout=5)
    record = manager.get(job["id"])
    assert record.status == "succeeded"
    assert record.result["status"] == "not_configured"
    state = ClassAnalysisStateStore(reports_dir).load(sid)
    assert state["status"] == "not_configured"
    # 无既有叙述的班级仍走 narrative_failed → 页面显示未配置横幅。
    page = client.get(f"/api/sessions/{sid}/class-analysis").json()
    assert page["narrative"] is None
    assert page["narrative_failed"] is True


def test_report_pipeline_not_configured_preserves_existing_narrative(
    class_analysis_api_client,
) -> None:
    """未配置兜底只写状态：已生成的班级叙述与班级条目原样保留可展示。"""
    client, db, sid, reports_dir, manager, holder, monkeypatch = (
        class_analysis_api_client
    )
    from backend.class_analysis import ClassAnalysisStateStore

    _patch_configured(monkeypatch, True)
    fake = PipelineClient()
    holder["client"] = fake
    job = _pipeline_via_api(client, sid)
    manager.wait(job["id"], timeout=10)
    assert manager.get(job["id"]).result["status"] == "ready"

    # 任务内工厂返回 None → not_configured；已就绪的班级叙述不清空。
    holder["client"] = None
    second = _pipeline_via_api(client, sid)
    manager.wait(second["id"], timeout=10)
    assert manager.get(second["id"]).result["status"] == "not_configured"
    state = ClassAnalysisStateStore(reports_dir).load(sid)
    assert state["status"] == "not_configured"
    assert state["class_reports"]["1 班"]["status"] == "ready"
    assert state["class_reports"]["1 班"]["narrative"]["key_findings"]

    page = client.get(f"/api/sessions/{sid}/class-analysis").json()
    assert page["narrative"] is not None
    assert page["narrative"]["student_notes"][0]["student_name"]
    assert page["narrative_failed"] is False


def test_report_pipeline_cancel_stops_between_units(
    class_analysis_api_client,
) -> None:
    """取消在第一处检查点生效：job 记 cancelled，不继续跑后续阶段。"""
    client, db, sid, _reports_dir, manager, holder, monkeypatch = (
        class_analysis_api_client
    )
    _patch_configured(monkeypatch, True)
    blocker = BlockingLLMClient()
    holder["client"] = blocker
    job = _pipeline_via_api(client, sid)
    assert blocker.started.wait(3)
    assert manager.cancel(job["id"])
    blocker.release.set()
    manager.wait(job["id"], timeout=5)
    assert manager.get(job["id"]).status == "cancelled"


def test_class_cause_digest_invalidates_class_narrative(
    class_analysis_api_client,
) -> None:
    """错因变化 → 班级摘要变化 → 页面 stale，重整理重新生成叙述。"""
    client, db, sid, reports_dir, manager, holder, monkeypatch = (
        class_analysis_api_client
    )
    from backend.class_analysis import ClassAnalysisStateStore

    _patch_configured(monkeypatch, True)
    fake = PipelineClient()
    holder["client"] = fake
    job = _pipeline_via_api(client, sid)
    manager.wait(job["id"], timeout=10)
    entry = ClassAnalysisStateStore(reports_dir).load(sid)["class_reports"]["1 班"]
    assert entry["status"] == "ready" and entry["cause_digest"]
    page = client.get(f"/api/sessions/{sid}/class-analysis").json()
    assert page["stale"] is False

    # 教师修改错法名 → 物化记录变化 → 该班摘要不再匹配。
    edit = client.post(
        f"/api/sessions/{sid}/class-analysis/causes/edit",
        json={
            "question_id": "Q2",
            "kind": "error",
            "reason": "垂直关系用错",
            "new_reason": "垂直条件误用",
            "category": "概念理解",
        },
    )
    assert edit.status_code == 200, edit.text
    page = client.get(f"/api/sessions/{sid}/class-analysis").json()
    assert page["stale"] is True

    fake.kinds.clear()
    second = _pipeline_via_api(client, sid)
    manager.wait(second["id"], timeout=10)
    assert fake.kinds == ["class"]  # 只有该班叙述重生成
    page = client.get(f"/api/sessions/{sid}/class-analysis").json()
    assert page["stale"] is False


def test_class_narrative_cache_without_causes_still_hits(
    class_analysis_api_client,
) -> None:
    """无错因摘要的班级叙述沿用旧缓存键：已生成的缓存继续命中。"""
    client, db, sid, reports_dir, manager, holder, monkeypatch = (
        class_analysis_api_client
    )
    from backend.class_analysis import (
        CLASS_ANALYSIS_RENDITION_VERSION,
        ClassAnalysisStateStore,
    )
    from backend.report_exports import score_revision
    from backend.reporting.analysis_report_exporter import AnalysisNarrativeCache

    _patch_configured(monkeypatch, True)
    revision = score_revision(db, sid, include_question_bank=False)
    cache = AnalysisNarrativeCache(reports_dir / ".analysis_narrative_cache")
    key = AnalysisNarrativeCache.cache_key(
        session_id=sid,
        score_revision=revision,
        rendition_version=CLASS_ANALYSIS_RENDITION_VERSION,
        report_key="class:1 班",
    )
    cache.store(key, dict(CLASS_NARRATIVE))

    fake = CauseFailClient()  # 错因始终失败 → 无摘要 → 走兼容缓存键
    holder["client"] = fake
    job = _pipeline_via_api(client, sid)
    manager.wait(job["id"], timeout=10)
    assert fake.kinds == ["cause", "cause", "personal", "personal"]
    entry = ClassAnalysisStateStore(reports_dir).load(sid)["class_reports"]["1 班"]
    assert entry["status"] == "ready"
    assert entry["cause_digest"] == ""
    assert entry["narrative"]["key_findings"] == CLASS_NARRATIVE["key_findings"]


def test_class_report_renders_data_with_placeholder_before_pipeline(
    class_analysis_api_client,
) -> None:
    """报告页先出数据段：叙述未整理时 AI 段显示占位文案而不是 409。"""
    client, db, sid, _reports_dir, manager, holder, monkeypatch = (
        class_analysis_api_client
    )
    response = client.get(f"/api/sessions/{sid}/class-analysis/report")
    assert response.status_code == 200
    assert "AI 整理后在此显示" in response.text
    assert "成绩分布" in response.text or "参考人数" in response.text


def _auto_workspace(db, tmp_path: Path):
    from backend.scan_grading.workspace import ScanGradingWorkspace

    return ScanGradingWorkspace(
        exams_root=tmp_path / "exams",
        templates_root=tmp_path / "templates",
        grading_db_path=db.db_path,
        data_root=tmp_path,
    )


def _mark_review_pending(db) -> None:
    """制造一条待复核条目：批注含「需复核」字样。"""
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE session_details SET deduction_reason='需复核：字迹不清'"
            " WHERE question_id='Q2'"
        )


def _clear_review_pending(db) -> None:
    """等价于教师复核确认：清掉「需复核」批注与低置信度。"""
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE session_results SET needs_human_review=0")
        conn.execute(
            "UPDATE session_details SET deduction_reason='批注',"
            " confidence_score=100"
        )


def test_auto_pipeline_skips_while_review_pending_and_fires_after_clear(
    auto_generate_env, tmp_path: Path,
) -> None:
    from backend.report_pipeline import maybe_auto_generate_report_pipeline

    db, sid, reports_dir, manager, fake, monkeypatch = auto_generate_env
    _patch_configured(monkeypatch, True)
    workspace = _auto_workspace(db, tmp_path)

    _mark_review_pending(db)
    # 有待复核条目：复核未完成不触发、不产生调用。
    assert maybe_auto_generate_report_pipeline(
        manager=manager, db=db, session_id=sid,
        reports_dir=reports_dir, workspace=workspace,
    ) is None
    _clear_review_pending(db)
    job = maybe_auto_generate_report_pipeline(
        manager=manager, db=db, session_id=sid,
        reports_dir=reports_dir, workspace=workspace,
    )
    assert job is not None
    assert job.payload["kind"] == "pipeline"
    assert job.payload["mode"] == "auto"
    manager.wait(job.id, timeout=10)
    assert manager.get(job.id).status == "succeeded"
    # 内容已齐备：再次触发幂等返回 None，无新增模型调用。
    calls = fake.calls
    assert maybe_auto_generate_report_pipeline(
        manager=manager, db=db, session_id=sid,
        reports_dir=reports_dir, workspace=workspace,
    ) is None
    assert fake.calls == calls


def test_auto_pipeline_never_fires_when_switch_off(
    auto_generate_env, tmp_path: Path,
) -> None:
    from backend.class_analysis import ClassAnalysisStateStore
    from backend.report_pipeline import maybe_auto_generate_report_pipeline

    db, sid, reports_dir, manager, fake, monkeypatch = auto_generate_env
    _patch_configured(monkeypatch, True)
    ClassAnalysisStateStore(reports_dir).set_auto_generate(sid, False)
    _clear_review_pending(db)
    assert maybe_auto_generate_report_pipeline(
        manager=manager, db=db, session_id=sid,
        reports_dir=reports_dir, workspace=_auto_workspace(db, tmp_path),
    ) is None
    assert fake.calls == 0


def test_auto_pipeline_skips_while_grading_incomplete(
    auto_generate_env, tmp_path: Path,
) -> None:
    from backend.report_pipeline import maybe_auto_generate_report_pipeline

    db, sid, reports_dir, manager, fake, monkeypatch = auto_generate_env
    _patch_configured(monkeypatch, True)
    _clear_review_pending(db)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            "UPDATE session_results SET raw_json=?",
            (json.dumps({"grading_completeness": {"status": "incomplete",
                         "missing_question_ids": ["Q9"]}}),),
        )
    assert maybe_auto_generate_report_pipeline(
        manager=manager, db=db, session_id=sid,
        reports_dir=reports_dir, workspace=_auto_workspace(db, tmp_path),
    ) is None
    assert fake.calls == 0


def test_auto_pipeline_saves_not_configured_without_calls(
    auto_generate_env, tmp_path: Path,
) -> None:
    from backend.class_analysis import ClassAnalysisStateStore
    from backend.report_pipeline import maybe_auto_generate_report_pipeline

    db, sid, reports_dir, manager, fake, monkeypatch = auto_generate_env
    _patch_configured(monkeypatch, False)
    _clear_review_pending(db)
    assert maybe_auto_generate_report_pipeline(
        manager=manager, db=db, session_id=sid,
        reports_dir=reports_dir, workspace=_auto_workspace(db, tmp_path),
    ) is None
    assert fake.calls == 0
    assert (
        ClassAnalysisStateStore(reports_dir).load(sid)["status"]
        == "not_configured"
    )


def test_auto_pipeline_trigger_closure_used_by_grading_run(
    auto_generate_env, tmp_path: Path,
) -> None:
    from backend.report_pipeline import build_report_pipeline_auto_trigger

    db, sid, reports_dir, manager, fake, monkeypatch = auto_generate_env
    _patch_configured(monkeypatch, True)
    _clear_review_pending(db)
    trigger = build_report_pipeline_auto_trigger(
        manager=manager,
        db_path=db.db_path,
        reports_dir=reports_dir,
        exams_dir=tmp_path / "exams",
        templates_dir=tmp_path / "templates",
        data_root=tmp_path,
    )
    trigger(sid)
    jobs, total = manager.list(
        session_id=sid, job_types=("class_analysis_generate",), limit=5
    )
    assert total == 1
    assert jobs[0].payload["kind"] == "pipeline"
    assert jobs[0].payload["mode"] == "auto"
    manager.wait(jobs[0].id, timeout=10)
    assert manager.get(jobs[0].id).status == "succeeded"

