from __future__ import annotations

from pathlib import Path

import pytest

import path_manager


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
    from tests.test_original_paper_score_contract import _ocr_row

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
                               headers={'content-type': 'image/png', 'x-upload-filename': source.name,
                                        'x-content-sha256': digest})
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
        "filename": report_job["result"]["filename"],
        "download_url": f"/api/jobs/{report_job['id']}/download",
    }
    assert Path(report_job["result"]["filename"]).name == report_job["result"][
        "filename"
    ]
    assert str(api_e2e.paths.data_root) not in str(report_job["result"])

    download = api_e2e.client.get(report_job["result"]["download_url"])
    assert download.status_code == 200
    assert download.headers["cache-control"] == "no-store"
    assert download.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert api_e2e.xlsx_score(download.content, "SYN-001") == 90.0
