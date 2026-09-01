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
    with TestClient(app) as client:
        try:
            yield client, db, session_id, reports_dir, manager, llm_holder, monkeypatch
        finally:
            manager.shutdown()


def _generate_via_api(client, session_id: int) -> dict:
    response = client.post(f"/api/sessions/{session_id}/class-analysis/regenerate")
    assert response.status_code == 202
    return response.json()


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
    names = {student["student_name"] for student in data["students"]}
    assert names == {"张三", "李四"}  # 教师本人页面出参不脱敏
    zhangsan = next(s for s in data["students"] if s["student_name"] == "张三")
    assert zhangsan["rank"] == 1
    assert zhangsan["lost"] and zhangsan["lost"][0]["question_id"] == "Q2"
    lisi = next(s for s in data["students"] if s["student_name"] == "李四")
    assert lisi["needs_review"] is True
    q2 = next(q for q in data["questions"] if q["question_id"] == "Q2")
    assert q2["max_score"] == 40
    assert q2["class_rate"] == 0.625
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
