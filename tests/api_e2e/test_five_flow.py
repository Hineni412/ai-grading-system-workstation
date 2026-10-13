from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
import pytest
import path_manager

from fastapi.testclient import TestClient
from backend.api.app import create_app
from backend.api import dependencies
from backend.api.dependencies import get_job_manager, get_ops_write_service
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore


@pytest.fixture(autouse=True)
def _controlled_synthetic_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # tests/conftest.py isolates the global PathManager into its own temporary
    # directory, so the harness data root (tmp_path / "synthetic_data") falls
    # outside every controlled root and resolve_stored_file_path rejects the
    # session's stored rubric/template paths during region commit. Point the
    # data root at tmp_path so the whole synthetic tree is controlled here.
    monkeypatch.setattr(path_manager.get_path_manager(), "_data_root", tmp_path)


def test_auto_regions_supplement_commit_scan_and_complete_grading_plan(api_e2e, monkeypatch):
    import hashlib
    from PIL import Image, ImageDraw
    from backend.scan_grading.workspace import ScanGradingWorkspace
    from backend.scan_grading.grading_service import _target_question_ids_from_regions
    from backend.reporting.original_paper_exporter import detect_printed_question_anchors
    from tests.api_e2e.test_five_flow import original_paper_score_contract__ocr_row as _ocr_row

    client = api_e2e.client
    created = client.post('/api/sessions', json={
        'name': 'Synthetic Auto Region Exam',
        'rubric_path': str(api_e2e.paths.bootstrap_rubric),
        'answer_key_path': str(api_e2e.paths.bootstrap_answer),
    })
    assert created.status_code == 201
    sid = created.json()['id']
    payload = api_e2e.config_payload()
    assert client.put(f'/api/sessions/{sid}/config', json=payload).status_code == 200
    paths = {'front': api_e2e.paths.template_front, 'back': api_e2e.paths.template_back}
    for marker, (page, path) in enumerate(paths.items(), 1):
        image = Image.new('RGB', (1000, 1400), 'white')
        draw = ImageDraw.Draw(image)
        for number, y in zip(range(1 if marker == 1 else 4, 4 if marker == 1 else 7), (200, 500, 800)):
            draw.text((50, y), f'{number}. synthetic printed question', fill='black')
        image.putpixel((0, 0), (marker, marker, marker))
        image.save(path)
    bound = client.put(f'/api/sessions/{sid}/template', json={
        'front_template_path': str(paths['front']), 'back_template_path': str(paths['back']),
    })
    assert bound.status_code == 200

    def ocr(image):
        marker = int(image[0, 0, 0])
        if image.shape[1] != 1000:
            return [], 0.0
        ids = (1, 2, 3) if marker == 1 else (4, 5)  # Teacher must supplement Q6.
        rows = [_ocr_row(f'{number}. printed', 50, y, w=820)
                for number, y in zip(ids, (200, 500, 800))]
        if marker == 1:
            rows.insert(0, _ocr_row('姓名：', 220, 70, w=50))
        return rows, 0.0

    monkeypatch.setattr('backend.document_parsing.local_ocr.get_local_ocr', lambda: ocr)
    anchors_before = detect_printed_question_anchors(paths, [f'Q{i}' for i in range(1, 7)])
    proposed = client.get(f'/api/sessions/{sid}/regions/auto-proposal')
    assert proposed.status_code == 200
    proposal = proposed.json()
    assert proposal['missing_question_ids'] == ['Q6']
    regions = proposal['regions']
    request = {'revision': 1, 'expected_revision': 0, 'regions': regions,
               'expected_template_fingerprint': proposal['template_fingerprint']}
    assert client.put(f'/api/sessions/{sid}/regions/draft', json=request).status_code == 200
    q5 = next(region for region in regions if region['mapped_question_id'] == 'Q5')
    q5['h'] = 300
    regions.append({**q5, 'region_uuid': 'test-manual-Q6', 'mapped_question_id': 'Q6',
                    'detected_question_id': None, 'confidence': 0.0, 'mapping_status': 'manual',
                    'region_order': 7, 'y': 800, 'h': 580})
    request.update(revision=2, expected_revision=1, regions=regions)
    assert client.put(f'/api/sessions/{sid}/regions/draft', json=request).status_code == 200
    committed = client.post(f'/api/sessions/{sid}/regions/commit', json={
        'regions': regions, 'image_sizes': {'front': [1000, 1400], 'back': [1000, 1400]},
        'expected_template_fingerprint': proposal['template_fingerprint'],
    })
    assert committed.status_code == 200 and committed.json()['committed']
    assert not committed.json()['snapshot_pending']
    formal = api_e2e.db.templates.list_answer_regions(sid)
    assert _target_question_ids_from_regions(formal, rubric=payload['rubric']) == [f'Q{i}' for i in range(1, 7)]
    assert detect_printed_question_anchors(paths, [f'Q{i}' for i in range(1, 7)]) == anchors_before

    uploaded = {}
    for marker, source in enumerate(sorted(api_e2e.paths.exams_dir.glob('SYN-*.png'))):
        with Image.open(source) as image:
            image.putpixel((0, 0), (marker, 0, 0))
            image.save(source)
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        response = client.post(f'/api/sessions/{sid}/scan-uploads', content=content,
                               headers={'content-type': 'image/png', 'x-upload-filename': source.name})
        assert response.status_code == 201, response.json()
        uploaded[source.name] = digest
    frozen = client.post(f'/api/sessions/{sid}/scan-uploads/freeze', json={'expected_revision': len(uploaded)})
    assert frozen.status_code == 200
    workspace = ScanGradingWorkspace(exams_root=api_e2e.paths.exams_dir,
                                     templates_root=api_e2e.paths.templates_dir,
                                     grading_db_path=api_e2e.paths.db_path)
    api_e2e.controls.uploaded_scan_paths = {
        name: workspace.frozen_scan_dir(sid) / f'{digest}.png' for name, digest in uploaded.items()
    }
    api_e2e.scan(sid)
    plan = client.post(f'/api/sessions/{sid}/grading/plan', json={'grading_mode': 'ai'})
    assert plan.status_code == 200, plan.json()
    assert plan.json()['status'] == 'ready'
    assert plan.json()['counts']['total_score_items'] == 12  # Two students, all six questions.
    assert api_e2e.controls.fake_llm_calls == []


def test_api_five_flow_persists_reviewed_score_in_downloaded_report(
    api_e2e,
    monkeypatch,
) -> None:
    session_id = api_e2e.create_configured_session()
    assert api_e2e.controls.fake_llm_calls == []

    config_response = api_e2e.client.get(f"/api/sessions/{session_id}/config")
    assert config_response.status_code == 200
    config = config_response.json()
    config_questions = config["rubric"]["questions"]
    assert [question["question_id"] for question in config_questions] == [
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "Q5",
        "Q6",
    ]
    assert [question["max_score"] for question in config_questions] == [
        17,
        17,
        17,
        16,
        16,
        17,
    ]
    assert Path(config["rubric_path"]).is_file()
    assert Path(config["answer_key_path"]).is_file()
    assert api_e2e.db.templates.is_template_ready(session_id) is True
    regions_response = api_e2e.client.get(f"/api/sessions/{session_id}/regions")
    assert regions_response.status_code == 200
    regions = regions_response.json()
    assert regions["total"] == 1
    assert regions["items"][0]["mapped_question_id"] == "Q1"

    scan_job = api_e2e.scan(session_id)
    assert scan_job["result"]["summary"] == {
        "auto_matched": 2,
        "issues": 0,
        "absent_candidates": 0,
        "total_pages": 4,
    }
    assert api_e2e.paper_statuses(session_id) == []

    grading_job = api_e2e.grade(session_id)
    assert grading_job["result"]["state"] == "completed"
    assert grading_job["result"]["summary"]["graded"] == 1
    assert grading_job["result"]["summary"]["failed"] == 1
    assert api_e2e.paper_statuses(session_id) == ["failed", "graded"]
    assert api_e2e.result_scores(session_id) == {"SYN-001": 85.0}

    recovery_job = api_e2e.grade(session_id, failed_only=True)
    assert recovery_job["result"]["state"] == "completed"
    assert recovery_job["result"]["summary"]["graded"] == 1
    assert recovery_job["result"]["summary"]["failed"] == 0
    assert api_e2e.paper_statuses(session_id) == ["graded", "graded"]
    assert api_e2e.result_scores(session_id) == {
        "SYN-001": 85.0,
        "SYN-002": 70.0,
    }

    questions_response = api_e2e.client.get(
        f"/api/sessions/{session_id}/review/questions"
    )
    assert questions_response.status_code == 200
    q1 = next(
        item
        for item in questions_response.json()["items"]
        if item["question_id"] == "Q1"
    )
    assert q1["needs_review_count"] == 1

    items_response = api_e2e.client.get(
        f"/api/sessions/{session_id}/review/questions/Q1/items"
    )
    assert items_response.status_code == 200
    first = next(
        item
        for item in items_response.json()["items"]
        if item["student_code"] == "SYN-001"
    )
    assert first["score_awarded"] == 12.0
    assert first["max_score"] == 17.0
    assert first["needs_review"] is True

    confirmed = api_e2e.client.post(
        f"/api/sessions/{session_id}/review/questions/Q1/confirm",
        json={
            "items": [
                {
                    "result_id": first["result_id"],
                    "detail_id": first["detail_id"],
                    "score_awarded": 17,
                    "deduction_reason": "synthetic teacher confirmation",
                }
            ]
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["updated_details"] == 1
    assert confirmed.json()["updated_results"] == 1
    assert api_e2e.result_scores(session_id)["SYN-001"] == 90.0

    cleared_questions = api_e2e.client.get(
        f"/api/sessions/{session_id}/review/questions"
    )
    assert cleared_questions.status_code == 200
    cleared_q1 = next(
        item
        for item in cleared_questions.json()["items"]
        if item["question_id"] == "Q1"
    )
    assert cleared_q1["needs_review_count"] == 0
    cleared_items = api_e2e.client.get(
        f"/api/sessions/{session_id}/review/questions/Q1/items"
    )
    assert cleared_items.status_code == 200
    assert cleared_items.json() == {"items": [], "total": 0}

    exported = api_e2e.client.post(
        f"/api/sessions/{session_id}/reports/export"
    )
    assert exported.status_code == 202
    report_job = api_e2e.poll_job(exported.json()["id"], "succeeded")
    assert report_job["result"] == {
        "session_id": session_id,
        "file_path": report_job["result"]["file_path"],
        "filename": report_job["result"]["filename"],
        "download_url": f"/api/jobs/{report_job['id']}/download",
    }
    assert Path(report_job["result"]["filename"]).name == report_job["result"][
        "filename"
    ]
    assert str(api_e2e.paths.data_root) in str(report_job["result"]["file_path"])

    import os
    import time
    from backend.files.service import JobFileService

    files = JobFileService(api_e2e.paths.reports_dir)
    store = api_e2e.manager.store
    export_path = Path(report_job["result"]["file_path"])
    retained_path = api_e2e.paths.reports_dir / "synthetic-retained.html"
    retained_path.write_text("synthetic paid report", encoding="utf-8")
    retained = store.create_job("report_export", {"report_type": "personal_analysis_html"})
    store.finish(retained.id, "succeeded", result={"file_path": str(retained_path)})
    original_path = api_e2e.paths.exams_dir / "synthetic-original.pdf"
    original_path.write_bytes(b"synthetic original paper")
    outside = store.create_job("report_export", {})
    store.finish(outside.id, "succeeded", result={"file_path": str(original_path)})
    cutoff = time.time() - 7 * 24 * 60 * 60
    assert files.reclaim_expired_files(store, cutoff=cutoff) == 0
    assert export_path.is_file()
    with store._connect() as conn:
        conn.execute(
            "UPDATE jobs SET finished_at = datetime('now','localtime','-8 days') WHERE id IN (?, ?, ?)",
            (report_job["id"], retained.id, outside.id),
        )
    assert files.reclaim_expired_files(store, cutoff=cutoff) == 0
    os.utime(export_path, (cutoff - 86400, cutoff - 86400))
    os.utime(retained_path, (cutoff - 86400, cutoff - 86400))
    os.utime(original_path, (cutoff - 86400, cutoff - 86400))
    original_unlink = Path.unlink

    def busy_export(path, *args, **kwargs):
        if path == export_path:
            raise PermissionError("synthetic export is being downloaded")
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as busy:
        busy.setattr(Path, "unlink", busy_export)
        assert files.reclaim_expired_files(store, cutoff=cutoff) == 0
    assert export_path.is_file()
    assert store.get_job(report_job["id"]).result["file_path"] == str(export_path)
    assert files.reclaim_expired_files(store, cutoff=cutoff) == 1
    assert not export_path.exists()
    assert retained_path.read_text(encoding="utf-8") == "synthetic paid report"
    assert original_path.read_bytes() == b"synthetic original paper"
    assert store.get_job(retained.id).result["file_path"] == str(retained_path)
    assert store.get_job(outside.id).result["file_path"] == str(original_path)
    assert api_e2e.client.get(report_job["result"]["download_url"]).status_code == 410

    exported = api_e2e.client.post(f"/api/sessions/{session_id}/reports/export")
    assert exported.status_code == 202
    report_job = api_e2e.poll_job(exported.json()["id"], "succeeded")
    download = api_e2e.client.get(report_job["result"]["download_url"])
    assert download.status_code == 200
    assert download.headers["cache-control"] == "no-store"
    assert download.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert api_e2e.xlsx_score(download.content, "SYN-001") == 90.0
    assert not Path(report_job["result"]["file_path"]).exists()


def original_paper_score_contract__ocr_row(text, x, y, w=250, h=24, confidence=0.95):
    return [[[x, y], [x + w, y], [x + w, y + h], [x, y + h]], text, confidence]


def test_scan_failure_can_be_retried_without_half_published_state(
    api_e2e,
    prepared_session_id,
) -> None:
    api_e2e.controls.scan_failures_remaining = 1
    failed = api_e2e.client.post(
        f"/api/sessions/{prepared_session_id}/scan/analyze",
        json={"enhance_images": False},
    )
    assert failed.status_code == 202
    first_job = api_e2e.poll_job(failed.json()["id"], "failed")
    assert first_job["error"] == "synthetic scan failure"
    assert not (
        api_e2e.paths.templates_dir
        / f"session_{prepared_session_id}"
        / "scan_analysis_latest.json"
    ).exists()
    assert list(
        (
            api_e2e.paths.templates_dir
            / f"session_{prepared_session_id}"
        ).glob(".scan_analysis_latest.*.tmp")
    ) == []
    assert api_e2e.paper_statuses(prepared_session_id) == []

    retried = api_e2e.client.post(
        f"/api/sessions/{prepared_session_id}/scan/analyze",
        json={"enhance_images": False},
    )
    assert retried.status_code == 202
    second_job = api_e2e.poll_job(retried.json()["id"], "succeeded")
    assert second_job["result"]["summary"] == {
        "auto_matched": 2,
        "issues": 0,
        "absent_candidates": 0,
        "total_pages": 4,
    }
    assert api_e2e.controls.scan_runner_calls == 2
    assert api_e2e.controls.scanner_analyze_calls == 2


def test_partial_grading_uses_completed_semantics_and_failed_only_recovery(
    api_e2e,
    scanned_session_id,
) -> None:
    assert api_e2e.paths.qb_db_path.is_file()
    api_e2e.paths.qb_db_path.resolve().relative_to(api_e2e.paths.data_root.resolve())
    submitted = api_e2e.client.post(
        f"/api/sessions/{scanned_session_id}/grading/run",
        json={"grading_mode": "ai", "enhance_images": False},
    )
    assert submitted.status_code == 202
    job = api_e2e.poll_job(submitted.json()["id"], "succeeded")
    assert job["result"]["state"] == "completed"
    assert job["result"]["summary"]["graded"] == 1
    assert job["result"]["summary"]["failed"] == 1
    assert api_e2e.paper_statuses(scanned_session_id) == ["failed", "graded"]
    assert api_e2e.result_scores(scanned_session_id) == {"SYN-001": 85.0}
    assert api_e2e.result_review_flags(scanned_session_id) == {
        "SYN-001": True,
    }
    assert api_e2e.result_details(scanned_session_id) == {
        "SYN-001": [
            ("Q1", 12.0),
            ("Q2", 17.0),
            ("Q3", 17.0),
            ("Q4", 17.0),
            ("Q5", 17.0),
            ("Q6", 5.0),
        ]
    }
    successful_result_ids = api_e2e.result_ids(scanned_session_id)

    retried = api_e2e.client.post(
        f"/api/sessions/{scanned_session_id}/grading/run",
        json={"failed_only": True, "enhance_images": False},
    )
    assert retried.status_code == 202
    recovered = api_e2e.poll_job(retried.json()["id"], "succeeded")
    assert recovered["result"]["state"] == "completed"
    assert recovered["result"]["summary"]["graded"] == 1
    assert recovered["result"]["summary"]["failed"] == 0
    assert api_e2e.paper_statuses(scanned_session_id) == ["graded", "graded"]
    assert api_e2e.result_scores(scanned_session_id) == {
        "SYN-001": 85.0,
        "SYN-002": 70.0,
    }
    assert api_e2e.result_ids(scanned_session_id)["SYN-001"] == (
        successful_result_ids["SYN-001"]
    )
    details = api_e2e.result_details(scanned_session_id)
    assert details["SYN-001"] == [
        ("Q1", 12.0),
        ("Q2", 17.0),
        ("Q3", 17.0),
        ("Q4", 17.0),
        ("Q5", 17.0),
        ("Q6", 5.0),
    ]
    assert [question_id for question_id, _score in details["SYN-002"]] == [
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "Q5",
        "Q6",
    ]
    assert sum(score for _question_id, score in details["SYN-002"]) == 70.0
    assert api_e2e.controls.grading_qbank_paths == [
        api_e2e.paths.qb_db_path,
        api_e2e.paths.qb_db_path,
    ]


def test_restart_marks_inflight_jobs_failed_and_preserves_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ops_factory_calls = 0

    def fail_if_ops_service_is_created():
        nonlocal ops_factory_calls
        ops_factory_calls += 1
        raise AssertionError("restart test must not create a root-bound ops service")

    monkeypatch.setattr(
        dependencies,
        "create_ops_write_service",
        fail_if_ops_service_is_created,
    )
    store = JobStore(tmp_path / "restart.db")
    queued = store.create_job("scan_analysis", {"session_id": 1})
    running = store.create_job("grading_run", {"session_id": 1})
    succeeded = store.create_job("report_export", {"session_id": 1})
    assert store.mark_running(running.id) is True
    store.finish(succeeded.id, "succeeded", result={"session_id": 1})

    restarted = JobManager(
        JobStore(store.db_path),
        max_workers=1,
        cleanup_interrupted=True,
    )
    try:
        app = create_app()
        app.dependency_overrides[get_job_manager] = lambda: restarted
        app.dependency_overrides[get_ops_write_service] = lambda: SimpleNamespace()
        with TestClient(app) as client:
            queued_response = client.get(f"/api/jobs/{queued.id}")
            running_response = client.get(f"/api/jobs/{running.id}")
            succeeded_response = client.get(f"/api/jobs/{succeeded.id}")

        assert queued_response.status_code == 200
        assert running_response.status_code == 200
        assert succeeded_response.status_code == 200
        assert queued_response.json()["status"] == "failed"
        assert running_response.json()["status"] == "failed"
        assert succeeded_response.json()["status"] == "succeeded"

        stored_running = restarted.get(running.id)
        assert stored_running is not None
        assert stored_running.error == "interrupted by process restart"
        for response in (queued_response, running_response):
            assert response.json()["error"] == stored_running.error
        assert ops_factory_calls == 0
    finally:
        restarted.shutdown()


def test_zhixue_score_import_writes_teacher_final_scores_and_combined_groups(
    api_e2e,
) -> None:
    from io import BytesIO
    import json
    from openpyxl import Workbook
    from backend.repositories.students import StudentRecord

    client = api_e2e.client
    # A third student is needed for the three combined-score cases
    # (full, zero and intermediate); put the roster into class 1 so the
    # workbook title's 「1班」 hint participates in matching.
    api_e2e.db.students.upsert_students(
        [
            StudentRecord("SYN-001", "Synthetic Student A", "1"),
            StudentRecord("SYN-002", "Synthetic Student B", "1"),
            StudentRecord("SYN-003", "Synthetic Student C", "1"),
        ]
    )
    created = client.post(
        "/api/sessions",
        json={
            "name": "Synthetic Zhixue Import Exam",
            "rubric_path": str(api_e2e.paths.bootstrap_rubric),
            "answer_key_path": str(api_e2e.paths.bootstrap_answer),
            "kind": "zhixue_import",
            "exam_date": "2026-07-01",
        },
    )
    assert created.status_code == 201, created.json()
    session_id = int(created.json()["id"])
    assert created.json()["kind"] == "zhixue_import"
    assert created.json()["exam_date"] == "2026-07-01"

    # 17+17+18+16+16+16 = 100. Q3 stays multi-part so the workbook's
    # single 「3」 total column forces the grade-wide 「whole」 decision;
    # Q4-Q6 are fill-blank single items covered only by the 「4-6」 column.
    payload = api_e2e.config_payload()
    q3 = payload["rubric"]["questions"][2]
    q3["max_score"] = 18
    q3["parts"] = [
        {
            "part_id": "Q3-1",
            "part_score": 9,
            "response_mode": "short_answer_points",
            "visual_requirements": [],
            "steps": [
                {
                    "step_id": "S1",
                    "step_score": 9,
                    "core_goal": "synthetic answer",
                    "required_elements": ["synthetic evidence"],
                    "allow_alternative_methods": True,
                }
            ],
        },
        {
            "part_id": "Q3-2",
            "part_score": 9,
            "response_mode": "short_answer_points",
            "visual_requirements": [],
            "steps": [
                {
                    "step_id": "S1",
                    "step_score": 9,
                    "core_goal": "synthetic answer",
                    "required_elements": ["synthetic evidence"],
                    "allow_alternative_methods": True,
                }
            ],
        },
    ]
    answer_q3 = payload["answer_key"]["questions"][2]
    answer_q3["parts"] = [
        {
            "part_id": "Q3-1",
            "answer": "synthetic answer Q3",
            "accepted_forms": [],
            "analysis": "synthetic analysis",
            "step_milestones": ["synthetic evidence"],
        },
        {
            "part_id": "Q3-2",
            "answer": "synthetic answer Q3",
            "accepted_forms": [],
            "analysis": "synthetic analysis",
            "step_milestones": ["synthetic evidence"],
        },
    ]
    for question in payload["rubric"]["questions"][3:]:
        question["question_type"] = "fill_blank"
        question["max_score"] = 16
        question["parts"][0]["part_score"] = 16
        question["parts"][0]["steps"][0]["step_score"] = 16
    configured = client.put(f"/api/sessions/{session_id}/config", json=payload)
    assert configured.status_code == 200, configured.json()

    def build_scores_workbook(student_b_q1: float = 17) -> bytes:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "小题得分明细"
        sheet.append(["【测试】八年级1班数学学生小题得分明细"])
        sheet.append(
            ["姓名", "客观分", "主观分", "总分", "1", "2", "3", "4-6"]
        )
        student_b_total = 32.5 + float(student_b_q1)
        sheet.append(
            ["Synthetic Student A", 0, 100, 100, 17, 17, 18, 48]
        )
        sheet.append(
            [
                "Synthetic Student B",
                0,
                student_b_total,
                student_b_total,
                student_b_q1,
                15.5,
                17,
                0,
            ]
        )
        sheet.append(["Synthetic Student C", 0, 71, 71, 15, 14, 12, 30])
        buffer = BytesIO()
        workbook.save(buffer)
        workbook.close()
        return buffer.getvalue()

    upload = client.post(
        f"/api/sessions/{session_id}/score-import/uploads",
        content=build_scores_workbook(),
        headers={"x-upload-filename": "scores.xlsx"},
    )
    assert upload.status_code == 201, upload.json()
    upload_id = upload.json()["upload_id"]

    preview = client.post(
        f"/api/sessions/{session_id}/score-import/preview",
        json={"upload_ids": [upload_id]},
    )
    assert preview.status_code == 200, preview.json()
    plan = preview.json()
    assert [
        issue for issue in plan["issues"] if issue["level"] == "block"
    ] == []
    plan_by_question = {
        question["question_id"]: question
        for question in plan["questions"]
    }
    assert plan_by_question["Q3"]["choice"] == "whole"
    assert plan_by_question["Q4"]["choice"] == "combined"
    group = plan["groups"][0]
    assert group["group_id"] == "G4-6"
    assert group["question_ids"] == ["Q4", "Q5", "Q6"]
    assert group["max_score"] == 48.0
    assert group["counts"] == {
        "full": 1,
        "zero": 1,
        "intermediate": 1,
        "derived": 0,
    }
    assert plan["students"]["matched"] == 3

    confirmed = client.post(
        f"/api/sessions/{session_id}/score-import/confirm",
        json={
            "upload_ids": [upload_id],
            "preview_revision": plan["preview_revision"],
            "replace_existing": False,
        },
    )
    assert confirmed.status_code == 200, confirmed.json()

    assert api_e2e.result_scores(session_id) == {
        "SYN-001": 100.0,
        "SYN-002": 49.5,
        "SYN-003": 71.0,
    }
    details = api_e2e.result_details(session_id)
    assert [question_id for question_id, _ in details["SYN-003"]] == [
        "Q1",
        "Q2",
        "Q3",
    ]
    assert dict(details["SYN-001"])["Q4"] == 16.0
    assert dict(details["SYN-002"])["Q4"] == 0.0

    with api_e2e.db._connect() as connection:
        raw_payloads = {
            str(row["student_code"]): json.loads(row["raw_json"])
            for row in connection.execute(
                "SELECT s.student_code, sr.raw_json FROM session_results sr "
                "JOIN students s ON s.id = sr.student_id "
                "WHERE sr.session_id = ?",
                (session_id,),
            )
        }
        lock_keys = {
            (str(row["student_code"]), str(row["question_id"]))
            for row in connection.execute(
                "SELECT s.student_code, l.question_id "
                "FROM teacher_score_locks l "
                "JOIN students s ON s.id = l.student_id "
                "WHERE l.session_id = ?",
                (session_id,),
            )
        }
        student_ids = {
            str(row["student_code"]): int(row["id"])
            for row in connection.execute(
                "SELECT id, student_code FROM students"
            )
        }
    combined = raw_payloads["SYN-003"].get("combined_scores")
    assert combined is not None
    assert combined["G4-6"]["score"] == 30.0
    assert combined["G4-6"]["max_score"] == 48.0
    assert "combined_scores" not in raw_payloads["SYN-001"]
    # Extreme students keep member details and teacher locks; the
    # intermediate student carries only the combined group score.
    assert ("SYN-001", "Q4") in lock_keys
    assert ("SYN-002", "Q5") in lock_keys
    assert ("SYN-003", "Q4") not in lock_keys
    assert raw_payloads["SYN-003"]["grading_completeness"]["status"] == (
        "complete"
    )

    center = client.get(f"/api/sessions/{session_id}/results-center")
    assert center.status_code == 200
    center_body = center.json()
    center_scores = {
        student["student_code"]: student["current_score"]
        for student in center_body["students"]
    }
    assert center_scores == {
        "SYN-001": 100.0,
        "SYN-002": 49.5,
        "SYN-003": 71.0,
    }
    student_c = next(
        student
        for student in center_body["students"]
        if student["student_code"] == "SYN-003"
    )
    group_cell = next(
        item
        for item in student_c["items"]
        if item["question_id"] == "4-6"
    )
    assert group_cell["score_awarded"] == 30.0
    assert group_cell["max_score"] == 48.0
    assert group_cell["combined_group"] is True
    assert all(
        item["question_id"] not in {"Q4", "Q5", "Q6"}
        for item in student_c["items"]
    )
    student_a = next(
        student
        for student in center_body["students"]
        if student["student_code"] == "SYN-001"
    )
    assert {"Q4", "Q5", "Q6"} <= {
        item["question_id"] for item in student_a["items"]
    }

    assessment_rows = api_e2e.db.results.get_active_assessment_rows(
        student_ids=list(student_ids.values()),
        session_ids=[session_id],
    )
    member_rows = {
        (str(row["student_code"]), str(row["question_id"]))
        for row in assessment_rows
        if str(row["question_id"]) in {"Q4", "Q5", "Q6"}
    }
    assert ("SYN-001", "Q4") in member_rows
    assert ("SYN-002", "Q4") in member_rows
    assert all(student_code != "SYN-003" for student_code, _ in member_rows)
    assert {str(row["exam_created_at"]) for row in assessment_rows} == {
        "2026-07-01 08:00:00"
    }

    changed = client.post(
        f"/api/sessions/{session_id}/score-import/uploads",
        content=build_scores_workbook(student_b_q1=16),
        headers={"x-upload-filename": "scores-v2.xlsx"},
    )
    assert changed.status_code == 201, changed.json()
    second_upload_id = changed.json()["upload_id"]
    second_preview = client.post(
        f"/api/sessions/{session_id}/score-import/preview",
        json={"upload_ids": [second_upload_id]},
    )
    assert second_preview.status_code == 200, second_preview.json()
    diff = second_preview.json()["diff"]
    assert diff == [
        {
            "student_id": student_ids["SYN-002"],
            "item_id": "Q1",
            "old_score": 17.0,
            "new_score": 16.0,
            "teacher_edited": False,
        }
    ]

    rejected = client.post(
        f"/api/sessions/{session_id}/score-import/confirm",
        json={
            "upload_ids": [second_upload_id],
            "preview_revision": second_preview.json()["preview_revision"],
            "replace_existing": False,
        },
    )
    assert rejected.status_code == 422
    replaced = client.post(
        f"/api/sessions/{session_id}/score-import/confirm",
        json={
            "upload_ids": [second_upload_id],
            "preview_revision": second_preview.json()["preview_revision"],
            "replace_existing": True,
        },
    )
    assert replaced.status_code == 200, replaced.json()
    assert api_e2e.result_scores(session_id) == {
        "SYN-001": 100.0,
        "SYN-002": 48.5,
        "SYN-003": 71.0,
    }
    assert api_e2e.controls.fake_llm_calls == []
