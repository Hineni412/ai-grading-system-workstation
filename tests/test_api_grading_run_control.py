from __future__ import annotations
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from backend.repositories.grading_database import open_grading_repositories

from pathlib import Path
from typing import Any
import pytest
from PIL import Image
import backend.scan_grading.grading_service as grading_service
from backend.scan_grading.ai_batch_grading_service import AIBatchRunResult, PaperEntry
from backend.repositories.db_manager import DBManager
from backend.scan_grading.grading_run_store import GradingRunStore
from backend.scan_grading.scanner import ExamPaperGroup


def _configure_preflight_binding(db, tmp_path, session_id: int) -> dict:
    from PIL import Image

    from backend.config_workspace.publish import load_editor_config
    from backend.exam_intake.template_upload_service import TemplateUploadService

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    rubric_path = config_dir / f"rubric-{session_id}.json"
    answer_path = config_dir / f"answer-{session_id}.json"
    rubric_path.write_text(
        json.dumps({"total_score": 0, "questions": []}),
        encoding="utf-8",
    )
    answer_path.write_text(
        json.dumps({"questions": []}),
        encoding="utf-8",
    )
    db.sessions.update_grading_session_config(
        session_id,
        rubric_path=str(rubric_path),
        answer_key_path=str(answer_path),
    )
    session_dir = tmp_path / "templates" / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    front_path = session_dir / "front.png"
    back_path = session_dir / "back.png"
    Image.new("RGB", (16, 16), "white").save(front_path)
    Image.new("RGB", (16, 16), "black").save(back_path)
    template_id = db.templates.upsert_session_template(
        session_id,
        str(front_path),
        str(back_path),
    )
    db.templates.mark_template_confirmed(session_id, True)
    current = TemplateUploadService(tmp_path / "templates").load_current(
        db=open_grading_repositories(db.db_path),
        session_id=session_id,
    )
    return {
        "config_revision": load_editor_config(db, session_id).revision,
        "template_id": template_id,
        "template_fingerprint": current.template_fingerprint,
        "template_first_page_role": current.first_page_role,
        "expected_rubric_path": str(rubric_path),
        "expected_answer_key_path": str(answer_path),
        "expected_template_id": template_id,
        "expected_front_template_path": str(front_path),
        "expected_back_template_path": str(back_path),
    }


def _system(tmp_path, *, config_fingerprint_resolver=None):
    from backend.api.app import create_app
    from backend.api.dependencies import get_exams_dir, get_grading_db, get_job_manager, get_templates_dir
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    

    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    manager = JobManager(JobStore(db.db_path), max_workers=1)
    app = create_app()
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_exams_dir] = lambda: tmp_path / "exams"
    app.dependency_overrides[get_templates_dir] = lambda: tmp_path / "templates"
    if config_fingerprint_resolver is not None:
        from backend.api.dependencies import get_scan_grading_workspace
        from backend.scan_grading.workspace import ScanGradingWorkspace

        grading_workspace = ScanGradingWorkspace(
            exams_root=tmp_path / "exams",
            templates_root=tmp_path / "templates",
            grading_db_path=db.db_path,
            job_manager=manager,
            config_fingerprint_resolver=config_fingerprint_resolver,
        )
        app.dependency_overrides[get_scan_grading_workspace] = lambda: grading_workspace
    return TestClient(app), db, manager


def _prepare_ready_scan_batch(client, db, tmp_path, session_id: int) -> dict:
    from backend.repositories.students import StudentRecord

    if not db.students.list_students():
        db.students.upsert_students([StudentRecord("S001", "学生甲", "测试班")])
    student = db.students.list_students()[0]
    binding = _configure_preflight_binding(db, tmp_path, session_id)
    content = b"\xff\xd8\xffready scan"
    uploaded = client.post(
        f"/api/sessions/{session_id}/scan-uploads",
        content=content,
        headers={
            "content-type": "image/jpeg",
            "x-upload-filename": "ready.jpg",
        },
    )
    assert uploaded.status_code == 201
    frozen = client.post(
        f"/api/sessions/{session_id}/scan-uploads/freeze",
        json={"expected_revision": 1},
    )
    assert frozen.status_code == 200
    scan_file = next((tmp_path / "exams").rglob("*.jpg"))
    analysis_path = (
        tmp_path / "templates" / f"session_{session_id}" / "scan_analysis_latest.json"
    )
    analysis_path.write_text(
        json.dumps(
            {
                "scan_batch_id": frozen.json()["batch_id"],
                "groups": [
                    {
                        "source_label": "001",
                        "front_image": str(scan_file),
                        "back_image": None,
                        "student_id": int(student["id"]),
                        "student_name": student["name"],
                        "match_method": "exact",
                        "match_score": 1.0,
                    }
                ],
                "issues": [],
                "absent_students": [],
                "warnings": [],
                "total_pages": 1,
                "config_revision": binding["config_revision"],
                "template_id": binding["template_id"],
                "template_fingerprint": binding["template_fingerprint"],
                "template_first_page_role": binding["template_first_page_role"],
            }
        ),
        encoding="utf-8",
    )
    result = frozen.json()
    result["_binding"] = binding
    return result


def test_concurrent_start_requests_create_only_one_grading_job(tmp_path) -> None:
    from backend.repositories.students import StudentRecord

    client, db, manager = _system(tmp_path)
    db.students.upsert_students([StudentRecord("S001", "学生甲", "测试班")])
    session_id = db.sessions.create_grading_session("并发启动测试", "rubric.json", "answer.json")
    binding = _configure_preflight_binding(db, tmp_path, session_id)
    content = b"\xff\xd8\xfffront"
    client.post(
        f"/api/sessions/{session_id}/scan-uploads",
        content=content,
        headers={
            "content-type": "image/jpeg",
            "x-upload-filename": "front.jpg",
        },
    )
    frozen = client.post(
        f"/api/sessions/{session_id}/scan-uploads/freeze",
        json={"expected_revision": 1},
    )
    scan_file = next((tmp_path / "exams").rglob("*.jpg"))
    analysis_path = (
        tmp_path / "templates" / f"session_{session_id}" / "scan_analysis_latest.json"
    )
    analysis_path.write_text(
        json.dumps(
            {
                "scan_batch_id": frozen.json()["batch_id"],
                "groups": [
                    {
                        "source_label": "001",
                        "front_image": str(scan_file),
                        "back_image": None,
                        "student_id": 1,
                        "match_method": "exact",
                        "match_score": 1.0,
                    }
                ],
                "issues": [],
                "absent_students": [],
                "warnings": [],
                "total_pages": 1,
                "config_revision": binding["config_revision"],
                "template_id": binding["template_id"],
                "template_fingerprint": binding["template_fingerprint"],
                "template_first_page_role": binding["template_first_page_role"],
            }
        ),
        encoding="utf-8",
    )
    release_handler = threading.Event()

    def wait_for_release(_context):
        release_handler.wait(timeout=5)
        return {"state": "completed"}

    manager.register("grading_run", wait_for_release)
    request = {
        "grading_mode": "ai",
        "upload_revision": 2,
        "decision_revision": 0,
        "confirm_pending_issues": False,
    }
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda _index: client.post(
                        f"/api/sessions/{session_id}/grading/run",
                        json=request,
                    ),
                    range(2),
                )
            )
        assert sorted(response.status_code for response in responses) == [202, 409]
        jobs, total = manager.list(
            session_id=session_id,
            job_types=("grading_run",),
            limit=10,
        )
        assert total == 1
        assert len(jobs) == 1
        assert "max_workers" not in jobs[0].payload
        projected = client.get(f"/api/sessions/{session_id}/grading-workspace")
        assert projected.status_code == 200
        assert projected.json()["grading_run"] is None
        grading_job = projected.json()["grading_job"]
        assert grading_job["id"] == jobs[0].id
        assert grading_job["status"] in {"queued", "running"}
        assert grading_job["scan_batch_id"] == frozen.json()["batch_id"]
        assert grading_job["cancel_requested"] is False
        release_handler.set()
        manager.wait(jobs[0].id, timeout=2)
        completed = client.get(f"/api/sessions/{session_id}/grading-workspace")
        assert completed.status_code == 200
        assert completed.json()["grading_run"] is None
        assert completed.json()["grading_job"]["status"] == "succeeded"
        repeated = client.post(
            f"/api/sessions/{session_id}/grading/run",
            json=request,
        )
        assert repeated.status_code == 409
        _jobs, repeated_total = manager.list(
            session_id=session_id,
            job_types=("grading_run",),
            limit=10,
        )
        assert repeated_total == 1
    finally:
        release_handler.set()
        manager.shutdown()


def test_start_requires_current_frozen_preflight_and_pending_issue_confirmation(
    tmp_path,
) -> None:
    client, db, manager = _system(tmp_path)
    session_id = db.sessions.create_grading_session("匿名期末", "rubric.json", "answer.json")
    binding = _configure_preflight_binding(db, tmp_path, session_id)
    manager.register("grading_run", lambda context: {"state": "completed"})
    content = b"\xff\xd8\xfffront"
    try:
        client.post(
            f"/api/sessions/{session_id}/scan-uploads",
            content=content,
            headers={
                "content-type": "image/jpeg",
                "x-upload-filename": "front.jpg",
                },
        )
        frozen = client.post(
            f"/api/sessions/{session_id}/scan-uploads/freeze",
            json={"expected_revision": 1},
        )
        scan_file = next((tmp_path / "exams").rglob("*.jpg"))
        analysis_path = (
            tmp_path
            / "templates"
            / f"session_{session_id}"
            / "scan_analysis_latest.json"
        )
        analysis_path.write_text(
            json.dumps(
                {
                    "scan_batch_id": frozen.json()["batch_id"],
                    "groups": [],
                    "issues": [
                        {
                            "issue_id": "issue-1",
                            "issue_type": "missing_back",
                            "message": "missing back",
                            "front_image": str(scan_file),
                            "back_image": None,
                            "source_label": "001",
                        }
                    ],
                    "absent_students": [],
                    "warnings": [],
                    "total_pages": 1,
                    "config_revision": binding["config_revision"],
                    "template_id": binding["template_id"],
                    "template_fingerprint": binding["template_fingerprint"],
                    "template_first_page_role": binding["template_first_page_role"],
                }
            ),
            encoding="utf-8",
        )

        rejected_path = client.post(
            f"/api/sessions/{session_id}/grading/run",
            json={"exams_dir": "C:/private", "grading_mode": "ai"},
        )
        assert rejected_path.status_code == 422

        unconfirmed = client.post(
            f"/api/sessions/{session_id}/grading/run",
            json={
                "grading_mode": "ai",
                "upload_revision": 2,
                "decision_revision": 0,
                "confirm_pending_issues": False,
            },
        )
        assert unconfirmed.status_code == 409
        assert (
            unconfirmed.json()["error"]["code"] == "pending_scan_issues_not_confirmed"
        )

        started = client.post(
            f"/api/sessions/{session_id}/grading/run",
            json={
                "grading_mode": "ai",
                "upload_revision": 2,
                "decision_revision": 0,
                "confirm_pending_issues": True,
            },
        )
        assert started.status_code == 202
        assert started.json()["payload"]["grading_mode"] == "ai"
        assert "exams_dir" in started.json()["payload"]

        from backend.scan_grading.grading_run_store import GradingRunStore

        active_run = GradingRunStore(db.db_path).begin(
            session_id, "d" * 64, "ai"
        )
        duplicate = client.post(
            f"/api/sessions/{session_id}/grading/run",
            json={
                "grading_mode": "ai",
                "upload_revision": 2,
                "decision_revision": 0,
                "confirm_pending_issues": True,
            },
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "grading_input_not_ready"
        GradingRunStore(db.db_path).finish(active_run.run_token, "completed")
    finally:
        manager.shutdown()


def test_resume_rejects_changed_grading_configuration_before_submitting_job(
    tmp_path,
) -> None:
    from backend.repositories.students import StudentRecord
    from backend.scan_grading.grading_run_store import GradingRunStore

    client, db, manager = _system(
        tmp_path,
        config_fingerprint_resolver=lambda _session_id, _mode: "b" * 64,
    )
    db.students.upsert_students([StudentRecord("S001", "学生甲", "测试班")])
    student_id = int(db.students.list_students()[0]["id"])
    session_id = db.sessions.create_grading_session("配置变化恢复", "rubric.json", "answer.json")
    store = GradingRunStore(db.db_path)
    run = store.begin(session_id, "a" * 64, "ai")
    store.add_item(
        run.id,
        source_label="001",
        student_id=student_id,
        paper_fingerprint="c" * 64,
        config_fingerprint="a" * 64,
        status="pending",
    )
    store.finish(run.run_token, "paused")
    manager.register("grading_run", lambda _context: {"state": "completed"})
    try:
        response = client.post(
            f"/api/sessions/{session_id}/grading/runs/{run.id}/resume"
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "grading_config_changed"
        jobs, total = manager.list(
            session_id=session_id,
            job_types=("grading_run",),
            limit=10,
        )
        assert total == 0
        assert jobs == []
        assert store.latest(session_id).id == run.id
    finally:
        manager.shutdown()


def test_appended_scan_blocks_supplement_until_preflight_rerun(tmp_path) -> None:
    from backend.repositories.students import StudentRecord
    from backend.scan_grading.grading_run_store import GradingRunStore

    client, db, manager = _system(
        tmp_path,
        config_fingerprint_resolver=lambda _session_id, _mode: "a" * 64,
    )
    session_id = db.sessions.create_grading_session(
        "追加答卷补批", "rubric.json", "answer.json"
    )
    late_content = b"\xff\xd8\xfflate scan"
    headers = {
        "content-type": "image/jpeg",
        "x-upload-filename": "late.jpg",
    }
    try:
        draft_append = client.post(
            f"/api/sessions/{session_id}/scan-uploads?append=true",
            content=late_content,
            headers=headers,
        )
        assert draft_append.status_code == 409
        assert draft_append.json()["error"]["code"] == "scan_append_requires_frozen_batch"

        _prepare_ready_scan_batch(client, db, tmp_path, session_id)
        store = GradingRunStore(db.db_path)
        run = store.begin(session_id, "a" * 64, "ai")
        store.finish(run.run_token, "completed")

        mode_conflict = client.post(
            f"/api/sessions/{session_id}/scan-uploads?append=true&replacement=true",
            content=late_content,
            headers=headers,
        )
        assert mode_conflict.status_code == 422
        assert mode_conflict.json()["error"]["code"] == "scan_upload_mode_conflict"

        appended = client.post(
            f"/api/sessions/{session_id}/scan-uploads?append=true",
            content=late_content,
            headers=headers,
        )
        assert appended.status_code == 201
        assert appended.json()["file"]["appended"] is True

        stale = client.get(f"/api/sessions/{session_id}/scan/preflight")
        assert stale.status_code == 200
        assert stale.json()["input_changed"] is True
        assert stale.json()["appended_file_count"] == 1

        blocked = client.post(
            f"/api/sessions/{session_id}/grading/runs/{run.id}/supplement-new-matches"
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "scan_preflight_outdated"

        # 重新预检让结果版本追上批次后，补批放行、只处理新匹配的答卷。
        db.students.upsert_students([StudentRecord("S002", "学生乙", "测试班")])
        new_student = next(
            item for item in db.students.list_students() if item["name"] == "学生乙"
        )
        session_dir = tmp_path / "templates" / f"session_{session_id}"
        manifest = json.loads(
            (session_dir / "scan_upload_batch.json").read_text(encoding="utf-8")
        )
        late_scan = next(
            (
                tmp_path / "exams" / f"session_{session_id}"
                / "scan_batches" / manifest["batch_id"] / "files"
            ).glob(f"{appended.json()['file']['sha256_prefix']}*")
        )
        analysis_path = session_dir / "scan_analysis_latest.json"
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        analysis["scan_upload_revision"] = manifest["revision"]
        analysis["groups"].append(
            {
                "source_label": "002",
                "front_image": str(late_scan),
                "back_image": None,
                "student_id": int(new_student["id"]),
                "student_name": "学生乙",
                "match_method": "exact",
                "match_score": 1.0,
            }
        )
        analysis_path.write_text(json.dumps(analysis), encoding="utf-8")

        current = client.get(f"/api/sessions/{session_id}/scan/preflight")
        assert current.json()["input_changed"] is False
        manager.register("grading_run", lambda _context: {"state": "completed"})
        submitted = client.post(
            f"/api/sessions/{session_id}/grading/runs/{run.id}/supplement-new-matches"
        )
        assert submitted.status_code == 202
        assert submitted.json()["payload"]["supplement_only"] is True
        assert submitted.json()["payload"]["supplement_run_id"] == run.id
    finally:
        manager.shutdown()


class grading_pause_resume__CountingGrader:
    """假 AIGrader：不调用模型，只承载 rubric/answer_key 供服务读取。"""

    def __init__(self, **kwargs: Any) -> None:
        self.rubric = {
            "questions": [{"question_id": "Q1", "parts": [{"part_id": "P1"}]}]
        }
        self.answer_key = {
            "questions": [{"question_id": "Q1", "parts": [{"part_id": "P1"}]}]
        }
        self.question_tag_context = {}
        self.call_count = 0


def grading_pause_resume__stub_result(student_name: str) -> Any:
    class _Result:
        pass

    result = _Result()
    result.student_name = student_name
    result.student_score = 1.0
    result.total_score = 1.0
    result.needs_human_review = False
    result.raw_json = {"questions": []}
    result.grading_details = []
    return result


def grading_pause_resume__batch_result_for_groups(
    paper_groups: list[ExamPaperGroup],
    *,
    paused: bool = False,
) -> AIBatchRunResult:
    entries = [
        PaperEntry(
            paper_key=f"paper-{group.student_id}",
            student_id=int(group.student_id),
            student_name=group.student_name,
            group=group,
        )
        for group in paper_groups
    ]
    results = {
        entry.paper_key: grading_pause_resume__stub_result(entry.student_name) for entry in entries
    }
    return AIBatchRunResult(
        paper_entries=entries,
        results_by_paper_key=results,
        fallback_items=[],
        usage_records=[],
        usage_summary={},
        paused=paused,
    )


def grading_pause_resume__make_group(
    tmp_path: Path, name: str, student_id: int, content: bytes
) -> ExamPaperGroup:
    front = tmp_path / f"{name}_front.png"
    back = tmp_path / f"{name}_back.png"
    Image.new("RGB", (20, 20), "white").save(front)
    back.write_bytes(content)
    return ExamPaperGroup(
        front_image=front,
        back_image=back,
        student_name=name,
        student_id=student_id,
        detected_name=name,
        source_label=name,
    )


def grading_pause_resume__seed(tmp_path: Path, students: list[tuple[int, str]]) -> tuple[DBManager, int]:
    # db 放在 databases/ 子目录下，data_root 才能推断为 tmp_path；
    # 否则解析相对路径（如 "rubric.json"）会落到会话级共享临时根，全量跑时被其他测试的同名文件干扰。
    databases_dir = tmp_path / "databases"
    databases_dir.mkdir()
    db = open_grading_repositories(databases_dir / "grading.db")
    db.initialize()
    session_id = db.sessions.create_grading_session("测试", "rubric.json", "answer.json")
    import sqlite3

    conn = sqlite3.connect(db.db_path)
    for sid, name in students:
        conn.execute(
            "INSERT INTO students (id, student_code, name) VALUES (?, ?, ?)",
            (sid, f"S{sid}", name),
        )
    conn.commit()
    conn.close()
    return db, session_id


@pytest.fixture
def grading_pause_resume_patched(monkeypatch, tmp_path):
    """打桩 AIGrader、AI 批次运行与扫描/模板重环节，用真实账本但不触碰模型。"""
    grader = grading_pause_resume__CountingGrader()

    def fake_batch(**kwargs: Any) -> AIBatchRunResult:
        grader.call_count += len(kwargs["paper_groups"])
        return grading_pause_resume__batch_result_for_groups(kwargs["paper_groups"])

    monkeypatch.setattr(grading_service, "AIGrader", lambda **kwargs: grader)
    monkeypatch.setattr(grading_service, "run_ai_batch_grading", fake_batch)
    monkeypatch.setattr(
        grading_service, "_load_rubric_for_preflight", lambda path: grader.rubric
    )
    monkeypatch.setattr(
        grading_service, "_validate_session_exam_identity", lambda session, rubric: None
    )
    monkeypatch.setattr(
        grading_service,
        "_target_question_ids_from_regions",
        lambda regions, rubric=None: ["Q1"],
    )
    monkeypatch.setattr(
        grading_service, "student_name_region_from_regions", lambda regions: None
    )
    monkeypatch.setattr(
        grading_service,
        "_session_front_page_parity",
        lambda session_id, scan_analysis=None: "odd",
    )

    import backend.answer_regions.answer_region_geometry as answer_region_geometry

    monkeypatch.setattr(
        answer_region_geometry,
        "answer_regions_with_template_source_sizes",
        lambda db, session_id, data_root=None: [],
    )
    return grader


def grading_pause_resume__service(db) -> Any:
    return grading_service.GradingService(db_manager=db, llm_client=object())


def test_pause_stops_batch_and_marks_run_paused(grading_pause_resume_patched, tmp_path, monkeypatch):
    db, session_id = grading_pause_resume__seed(tmp_path, [(i, f"stu{i}") for i in range(1, 6)])
    groups = [
        grading_pause_resume__make_group(tmp_path, f"stu{i}", i, f"paper-{i}".encode()) for i in range(1, 6)
    ]
    # 让服务把这些 group 当作已匹配答卷
    monkeypatch.setattr(
        grading_service,
        "_apply_manual_decisions",
        lambda analysis, decisions, students: groups,
    )
    monkeypatch.setattr(db.templates, "is_template_ready", lambda session_id: True)
    monkeypatch.setattr(
        db.students,
        "find_student_by_name",
        lambda name: {"id": int(name[3:]), "name": name},
    )

    service = grading_pause_resume__service(db)
    store = GradingRunStore(db.db_path)

    def pausing_batch(**kwargs: Any) -> AIBatchRunResult:
        # 暂停请求在 grading_started 事件后到达；批次遵循 should_pause 返回 paused。
        assert kwargs["should_pause"]()
        return grading_pause_resume__batch_result_for_groups(kwargs["paper_groups"], paused=True)

    monkeypatch.setattr(grading_service, "run_ai_batch_grading", pausing_batch)

    events = []
    gen = service.run_session_grading(
        session_id=session_id,
        exams_dir=tmp_path,
        rubric_path=tmp_path / "rubric.json",
        answer_key_path=tmp_path / "answer.json",
        scan_analysis={"groups": [], "issues": []},
        max_workers=2,
        grading_mode="ai",
        enhance_images=False,
    )
    for event in gen:
        events.append(event)
        if event.get("event") == "grading_started":
            store.request_pause(session_id)

    run = store.latest(session_id)
    assert run.state == "paused"
    counts = store.counts(run.id)
    assert counts["graded"] == 5
    assert any(e.get("event") == "session_paused" for e in events)


def test_cancel_discards_unpublished_batch_results(grading_pause_resume_patched, tmp_path, monkeypatch):
    db, session_id = grading_pause_resume__seed(tmp_path, [(1, "stu1")])
    group = grading_pause_resume__make_group(tmp_path, "stu1", 1, b"paper-1")
    monkeypatch.setattr(
        grading_service,
        "_apply_manual_decisions",
        lambda _analysis, _decisions, _students: [group],
    )
    monkeypatch.setattr(db.templates, "is_template_ready", lambda _session_id: True)
    started = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()

    def blocking_batch(**kwargs: Any) -> AIBatchRunResult:
        started.set()
        assert release.wait(3)
        return grading_pause_resume__batch_result_for_groups(kwargs["paper_groups"], paused=True)

    monkeypatch.setattr(grading_service, "run_ai_batch_grading", blocking_batch)
    events: list[dict[str, Any]] = []
    errors: list[BaseException] = []

    def consume() -> None:
        try:
            events.extend(
                grading_pause_resume__service(db).run_session_grading(
                    session_id=session_id,
                    exams_dir=tmp_path,
                    rubric_path=tmp_path / "rubric.json",
                    answer_key_path=tmp_path / "answer.json",
                    scan_analysis={"groups": [], "issues": []},
                    grading_mode="ai",
                    enhance_images=False,
                    should_cancel=cancelled.is_set,
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    worker = threading.Thread(target=consume)
    worker.start()
    started_in_time = started.wait(3)
    cancelled.set()
    release.set()
    worker.join(5)

    assert started_in_time, errors
    assert not worker.is_alive()
    assert errors == []
    assert any(event.get("event") == "session_cancelled" for event in events)
    with db._connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM session_results WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT processing_status FROM exam_papers WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == "pending"
        )
    run = GradingRunStore(db.db_path).latest(session_id)
    assert run is not None
    assert run.state == "failed"


def test_supplement_grades_only_new_sources_without_clearing_prior_results(
    grading_pause_resume_patched,
    tmp_path,
    monkeypatch,
):
    db, session_id = grading_pause_resume__seed(tmp_path, [(1, "stu1"), (2, "stu2")])
    first = grading_pause_resume__make_group(tmp_path, "stu1", 1, b"paper-one")
    second = grading_pause_resume__make_group(tmp_path, "stu2", 2, b"paper-two")
    visible_groups = [first]
    monkeypatch.setattr(
        grading_service,
        "_apply_manual_decisions",
        lambda _analysis, _decisions, _students: list(visible_groups),
    )
    monkeypatch.setattr(db.templates, "is_template_ready", lambda _session_id: True)

    first_events = list(
        grading_pause_resume__service(db).run_session_grading(
            session_id=session_id,
            exams_dir=tmp_path,
            rubric_path=tmp_path / "rubric.json",
            answer_key_path=tmp_path / "answer.json",
            scan_analysis={"groups": [], "issues": []},
            grading_mode="ai",
            enhance_images=False,
        )
    )
    assert any(event.get("event") == "graded" for event in first_events)
    run = GradingRunStore(db.db_path).latest(session_id)
    assert run is not None and run.state == "completed"
    assert grading_pause_resume_patched.call_count == 1

    visible_groups[:] = [first, second]
    supplement_events = list(
        grading_pause_resume__service(db).run_session_grading(
            session_id=session_id,
            exams_dir=tmp_path,
            rubric_path=tmp_path / "rubric.json",
            answer_key_path=tmp_path / "answer.json",
            scan_analysis={"groups": [], "issues": []},
            grading_mode="ai",
            enhance_images=False,
            supplement_only=True,
            supplement_run_id=run.id,
        )
    )

    assert sum(event.get("event") == "graded" for event in supplement_events) == 1
    assert grading_pause_resume_patched.call_count == 2
    with db._connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM exam_papers WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == 2
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM session_results WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            == 2
        )
    refreshed = GradingRunStore(db.db_path).latest(session_id)
    assert refreshed is not None and refreshed.id == run.id
    assert refreshed.state == "completed"
    assert GradingRunStore(db.db_path).counts(run.id)["graded"] == 2
