from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from backend.grading_workflow import build_grading_plan, preflight_match_status
from backend.scan_grading.workspace import ScanGradingWorkspace, ScanMatchConflictError
from grading_service import apply_scan_manual_decisions
from scanner import ScanAnalysis


ROSTER = [
    {"id": 1, "name": "林青", "student_code": "A01", "class_name": "七年级1班"},
    {"id": 2, "name": "林清", "student_code": "A02", "class_name": "七年级1班"},
    {"id": 3, "name": "陈晨", "student_code": "A03", "class_name": "七年级1班"},
    {"id": 4, "name": "陈晨", "student_code": "B01", "class_name": "七年级2班"},
]


def _raw_group(index, student_id=1, **extra):
    return {
        "front_image": f"front-{index}.jpg",
        "back_image": f"back-{index}.jpg",
        "source_label": "same-source",
        "student_id": student_id,
        "student_name": "林青",
        "detected_name": "林青",
        "match_method": "exact",
        "match_score": 1,
        **extra,
    }


@pytest.fixture
def scan_workspace(tmp_path):
    workspace = ScanGradingWorkspace(
        exams_root=tmp_path / "exams", templates_root=tmp_path / "templates"
    )
    content = b"\xff\xd8\xffsynthetic"
    workspace.add_upload(
        7,
        filename="scan.jpg",
        media_type="image/jpeg",
        content_sha256=hashlib.sha256(content).hexdigest(),
        source=io.BytesIO(content),
    )
    frozen = workspace.freeze_uploads(7, expected_revision=1)
    path = tmp_path / "templates/session_7/scan_analysis_latest.json"
    payload = {
        "scan_batch_id": frozen["batch_id"],
        "total_pages": 7,
        "students": ROSTER,
        "groups": [_raw_group(1), _raw_group(2, detected_name="林清")],
        "issues": [
            {
                "issue_id": "homonym",
                "front_image": "front-3.jpg",
                "back_image": "back-3.jpg",
                "detected_name": "陈晨",
                "issue_type": "ambiguous_name",
            },
            {
                "issue_id": "orphan",
                "front_image": "page-7.jpg",
                "issue_type": "orphan_page",
            },
        ],
        "absent_students": [],
        "warnings": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return workspace, path, payload


def test_partial_batch_saves_valid_matches_and_returns_every_conflicting_paper(
    scan_workspace,
):
    workspace, _, _ = scan_workspace
    check = workspace.get_preflight(7)
    first, second = check["groups"]
    good = {
        "target_type": "group",
        "target_id": second["id"],
        "action": "match",
        "student_id": 2,
    }
    bad = {
        "target_type": "issue",
        "target_id": "homonym",
        "action": "match",
        "student_id": 1,
    }
    saved = workspace.save_decisions(
        7,
        expected_revision=0,
        valid_student_ids={1, 2, 3, 4},
        decisions=[good, bad],
        allow_partial_matches=True,
    )
    assert saved["decisions"] == [good]
    assert saved["summary"]["ready_to_grade"] == 2
    assert saved["match_conflicts"] == []
    conflict = saved["rejected_conflicts"][0]
    assert conflict["student_id"] == 1
    assert {(t["target_type"], t["target_id"]) for t in conflict["targets"]} == {
        ("group", first["id"]),
        ("issue", "homonym"),
    }
    reentered = ScanGradingWorkspace(
        exams_root=workspace.exams_root, templates_root=workspace.templates_root
    ).get_preflight(7)
    assert (
        reentered["decisions"] == [good] and reentered["summary"]["ready_to_grade"] == 2
    )
    corrected = workspace.save_decisions(
        7,
        expected_revision=1,
        valid_student_ids={1, 2, 3, 4},
        decisions=[good, {**bad, "student_id": 3}],
        allow_partial_matches=True,
    )
    assert corrected["ready_to_grade"] == 3 and corrected["match_conflicts"] == []


@pytest.mark.parametrize(
    "extra,code",
    [
        ({"student_id": 3, "detected_name": "陈晨"}, "scan_name_ambiguous"),
        (
            {"student_id": 3, "detected_name": "", "student_name": "陈晨"},
            "scan_name_ambiguous",
        ),
        ({"detected_class_name": "七年级2班"}, "scan_class_mismatch"),
    ],
)
def test_homonym_and_cross_class_require_explicit_identity_confirmation(extra, code):
    group = {
        "id": "g1",
        "student_id": 1,
        "student_name": "林青",
        "detected_name": "林青",
        "front_media_url": "front",
        "back_media_url": "back",
        **extra,
    }
    preflight = {"groups": [group]}
    status = preflight_match_status(preflight, ROSTER)
    assert any(c["code"] == code for c in status["conflicts"])
    assert status["summary"]["ready_to_grade"] == 0
    preflight["decisions"] = [
        {
            "target_type": "group",
            "target_id": "g1",
            "action": "match",
            "student_id": group["student_id"],
        }
    ]
    status = preflight_match_status(preflight, ROSTER)
    assert status["conflicts"] == [] and status["summary"]["ready_to_grade"] == 1


def test_actual_grading_refuses_duplicate_groups_before_attendance_or_score_registration():
    analysis = ScanAnalysis.from_dict({"groups": [_raw_group(1), _raw_group(2)]})
    with pytest.raises(ValueError, match="归属存在冲突"):
        apply_scan_manual_decisions(analysis, [], ROSTER)
