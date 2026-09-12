from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from backend.grading_workflow import build_grading_plan, preflight_match_status
from backend.scan_grading.workspace import ScanGradingWorkspace, ScanMatchConflictError, UploadBatchRevisionError
from grading_service import apply_scan_manual_decisions
from scanner import ExamPaperGroup, ScanAnalysis, Scanner, _build_student_lookup, _match_student


ROSTER = [
    {"id": 1, "name": "林青", "student_code": "A01", "class_name": "七年级1班"},
    {"id": 2, "name": "林清", "student_code": "A02", "class_name": "七年级1班"},
    {"id": 3, "name": "陈晨", "student_code": "A03", "class_name": "七年级1班"},
    {"id": 4, "name": "陈晨", "student_code": "B01", "class_name": "七年级2班"},
]


def _raw_group(index, student_id=1, **extra):
    return {"front_image": f"front-{index}.jpg", "back_image": f"back-{index}.jpg",
            "source_label": "same-source", "student_id": student_id, "student_name": "林青",
            "detected_name": "林青", "match_method": "exact", "match_score": 1, **extra}


@pytest.fixture
def scan_workspace(tmp_path):
    workspace = ScanGradingWorkspace(exams_root=tmp_path / "exams", templates_root=tmp_path / "templates")
    content = b"\xff\xd8\xffsynthetic"
    workspace.add_upload(7, filename="scan.jpg", media_type="image/jpeg",
                         content_sha256=hashlib.sha256(content).hexdigest(), source=io.BytesIO(content))
    frozen = workspace.freeze_uploads(7, expected_revision=1)
    path = tmp_path / "templates/session_7/scan_analysis_latest.json"
    payload = {
        "scan_batch_id": frozen["batch_id"], "total_pages": 7, "students": ROSTER,
        "groups": [_raw_group(1), _raw_group(2, detected_name="林清")],
        "issues": [
            {"issue_id": "homonym", "front_image": "front-3.jpg", "back_image": "back-3.jpg",
             "detected_name": "陈晨", "issue_type": "ambiguous_name"},
            {"issue_id": "orphan", "front_image": "page-7.jpg", "issue_type": "orphan_page"},
        ],
        "absent_students": [], "warnings": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return workspace, path, payload


def test_duplicate_assignment_blocks_all_plans_and_reentry_keeps_each_paper(scan_workspace):
    workspace, path, original = scan_workspace
    check = workspace.get_preflight(7)
    assert check["summary"]["scanned_papers"] == 4  # front/back form one answer, orphan is one unresolved item
    assert check["summary"]["matched_papers"] == 2
    assert check["summary"]["unique_students"] == 1
    assert check["summary"]["ready_to_grade"] == 0
    assert check["pending_issue_count"] == 4
    assert any(c["code"] == "scan_student_multiple_papers" for c in check["match_conflicts"])
    with pytest.raises(ScanMatchConflictError):
        workspace.require_resolved_matches(check)
    for mode in ("full_paper", "manual", "hybrid_batch"):
        plan = build_grading_plan(session_id=7, mode=mode, scan_batch_id="synthetic", upload_revision=2,
                                 preflight=check, rubric={"questions": [{"question_id": "Q1", "max_score": 5}]}, teacher_locks=[])
        assert plan["status"] == "blocked"
        assert any(c["code"] == "scan_student_multiple_papers" for c in plan["blockers"])

    changes = [
        {"target_type": "group", "target_id": check["groups"][1]["id"], "action": "match", "student_id": 2},
        {"target_type": "issue", "target_id": "homonym", "action": "match", "student_id": 3},
    ]
    saved = workspace.save_decisions(7, expected_revision=0, valid_student_ids={1, 2, 3, 4}, decisions=changes)
    assert saved["ready_to_grade"] == 3 and saved["pending_issue_count"] == 1
    assert saved["summary"]["unique_students"] == 3
    assert [s["id"] for s in saved["absent_students"]] == [4]
    assert saved["match_conflicts"] == []
    reentered = ScanGradingWorkspace(exams_root=workspace.exams_root, templates_root=workspace.templates_root).get_preflight(7)
    assert reentered["decisions"] == changes and reentered["summary"] == saved["summary"]
    assert json.loads(path.read_text(encoding="utf-8")) == original
    internal = json.loads((path.parent / "scan_manual_decisions_latest.json").read_text(encoding="utf-8"))
    analysis = ScanAnalysis.from_dict(original)
    groups = apply_scan_manual_decisions(analysis, internal, ROSTER)
    assert [(g.student_id, g.front_image.name) for g in groups] == [(1, "front-1.jpg"), (2, "front-2.jpg"), (3, "front-3.jpg")]
    assert analysis.groups[1].student_id == 1  # original scan snapshot is never changed in-place


def test_conflicting_batch_is_rejected_atomically_and_revision_stays_current(scan_workspace):
    workspace, path, _ = scan_workspace
    check = workspace.get_preflight(7)
    fixed = [{"target_type": "group", "target_id": check["groups"][1]["id"], "action": "match", "student_id": 2}]
    workspace.save_decisions(7, expected_revision=0, valid_student_ids={1, 2, 3, 4}, decisions=fixed)
    before = (path.parent / "scan_decisions_state.json").read_bytes()
    with pytest.raises(ScanMatchConflictError, match="本次匹配未保存"):
        workspace.save_decisions(7, expected_revision=1, valid_student_ids={1, 2, 3, 4}, decisions=[
            *fixed, {"target_type": "issue", "target_id": "homonym", "action": "match", "student_id": 1},
        ])
    assert (path.parent / "scan_decisions_state.json").read_bytes() == before
    with pytest.raises(UploadBatchRevisionError):
        workspace.save_decisions(7, expected_revision=0, valid_student_ids={1, 2, 3, 4}, decisions=[])
    assert workspace.get_preflight(7)["revision"] == 1


def test_duplicate_scan_can_be_marked_invalid_without_losing_the_original(scan_workspace):
    workspace, path, original = scan_workspace
    check = workspace.get_preflight(7)
    decision = {"target_type": "group", "target_id": check["groups"][1]["id"], "action": "invalid"}
    saved = workspace.save_decisions(7, expected_revision=0, valid_student_ids={1, 2, 3, 4}, decisions=[decision])
    assert saved["ready_to_grade"] == 1
    assert saved["summary"]["invalid_papers"] == 1
    assert saved["summary"]["scanned_papers"] == saved["ready_to_grade"] + saved["summary"]["invalid_papers"] + saved["pending_issue_count"]
    internal = json.loads((path.parent / "scan_manual_decisions_latest.json").read_text(encoding="utf-8"))
    groups = apply_scan_manual_decisions(ScanAnalysis.from_dict(original), internal, ROSTER)
    assert [(g.student_id, g.front_image.name) for g in groups] == [(1, "front-1.jpg")]


@pytest.mark.parametrize("extra,code", [
    ({"detected_name": "林清"}, "scan_name_mismatch"),
    ({"student_id": 3, "detected_name": "陈晨"}, "scan_name_ambiguous"),
    ({"student_id": 3, "detected_name": "", "student_name": "陈晨"}, "scan_name_ambiguous"),
    ({"detected_class_name": "七年级2班"}, "scan_class_mismatch"),
])
def test_ocr_disagreement_homonym_and_cross_class_require_explicit_identity_confirmation(extra, code):
    group = {"id": "g1", "student_id": 1, "student_name": "林青", "detected_name": "林青",
             "front_media_url": "front", "back_media_url": "back", **extra}
    preflight = {"groups": [group]}
    status = preflight_match_status(preflight, ROSTER)
    assert any(c["code"] == code for c in status["conflicts"])
    assert status["summary"]["ready_to_grade"] == 0
    preflight["decisions"] = [{"target_type": "group", "target_id": "g1", "action": "match", "student_id": group["student_id"]}]
    status = preflight_match_status(preflight, ROSTER)
    assert status["conflicts"] == [] and status["summary"]["ready_to_grade"] == 1


def test_actual_grading_refuses_duplicate_groups_before_attendance_or_score_registration():
    analysis = ScanAnalysis.from_dict({"groups": [_raw_group(1), _raw_group(2)]})
    with pytest.raises(ValueError, match="归属存在冲突"):
        apply_scan_manual_decisions(analysis, [], ROSTER)


def test_start_rechecks_duplicate_matches_even_when_pending_skip_is_confirmed(scan_workspace, monkeypatch):
    workspace, _, _ = scan_workspace
    monkeypatch.setattr(workspace, "_require_current_preflight_template", lambda *a, **kw: None)
    with pytest.raises(ScanMatchConflictError):
        workspace.prepare_start(7, grading_mode="full_paper", upload_revision=2, decision_revision=0,
                                confirm_pending_issues=True, enhance_images=False,
                                max_workers=None, requests_per_minute=None)


def test_legacy_name_only_groups_resolve_unique_ids_but_refuse_homonyms():
    unique = ScanAnalysis.from_dict({"groups": [_raw_group(1, student_id=None)]})
    assert apply_scan_manual_decisions(unique, [], ROSTER)[0].student_id == 1
    ambiguous = ScanAnalysis.from_dict({"groups": [_raw_group(1, student_id=None, student_name="陈晨", detected_name="陈晨")]})
    with pytest.raises(ValueError, match="按学号和班级"):
        apply_scan_manual_decisions(ambiguous, [], ROSTER)


def test_name_lookup_does_not_overwrite_homonyms_or_promote_fuzzy_ocr_to_exact(tmp_path):
    from PIL import Image
    lookup = _build_student_lookup(ROSTER)
    match = _match_student("陈晨", lookup)
    assert match.method == "ambiguous" and match.student is None
    path = tmp_path / "front.jpg"
    Image.new("RGB", (100, 100), "white").save(path)
    scanner = Scanner(tmp_path, object(), enhance_images=False)
    scanner._do_local_ocr = lambda _image: "姓名：王小朋 班级：七年级2班"
    fuzzy_roster = _build_student_lookup([{"id": 1, "name": "王小明", "class_name": "七年级1班"}])
    assert scanner._extract_student_names_batch([path], fuzzy_roster) == ["王小朋"]
    assert _match_student("王小朋", fuzzy_roster).method == "fuzzy"
    assert scanner._detected_classes[str(path)] == "七年级2班"
