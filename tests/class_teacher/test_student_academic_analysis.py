from __future__ import annotations

from contextlib import closing
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.class_teacher.student_academic_analysis as academic_module
from backend.class_teacher.assessment_evidence_service import ConfirmedSpreadsheetAdapter
from backend.class_teacher.errors import VaultError
from backend.class_teacher.student_academic_analysis import StudentAcademicAnalysis
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]
def _service(tmp_path: Path) -> tuple[VaultService, str, str]:
    service = VaultService(WorkspaceContext(
        module_id="class-teacher",
        root=tmp_path / "workspaces" / "class-teacher",
        paths=SimpleNamespace(project_root=PROJECT_ROOT, migration_project_root=PROJECT_ROOT),
    ))
    service.ensure_plaintext_ready()
    token = ""
    subject = service.support.create_subject(
        token=token,
        operation_id="academic-subject-0001",
        source_student_id="academic-student-001",
        display_name="合成学业学生",
        class_label="合成一班",
    )
    return service, token, str(subject["subject_id"])


def _confirm(service: VaultService, token: str, subject_id: str) -> None:
    states = [
        ("normal", 0, 40, 40),
        ("absent", None, None, 40),
        ("makeup", 72, 12, 40),
        ("normal", 78, 41, 40),
    ]
    assessments = []
    for index, (state, score, rank, participants) in enumerate(states, start=1):
        assessments.append({
            "title": f"合成场次 {index}",
            "subject_name": "数学",
            "occurred_on": f"2026-0{index + 1}-01",
            "max_score": 100,
            "rank_scope": "class",
            "participant_count": participants,
            "assessment_nature": "unit",
            "rank_origin": "teacher_confirmed",
            "cohort_key": "synthetic-class-1",
            "ranking_rule_version": "school-rule-v1",
            "session": {
                "title": f"合成场次 {index}",
                "academic_year": "2025-2026",
                "term": "下学期",
                "grade": "八年级",
                "exam_type": "单元测验",
                "comparison_series": "数学单元",
                "occurred_on": f"2026-0{index + 1}-01",
                "source_reference": f"synthetic-{index}",
            },
            "results": [{
                "subject_id": subject_id,
                "result_state": state,
                "score": score,
                "rank": rank,
            }],
        })
    batch = ConfirmedSpreadsheetAdapter().read({
        "teacher_confirmed": True,
        "source_label": "仅内存合成分析数据",
        "assessments": assessments,
    })
    service.evidence.confirm_batch(
        token=token, operation_id="academic-confirm-0001", batch=batch
    )


def test_analysis_preserves_zero_breaks_special_states_and_rejects_invalid_rank(tmp_path: Path) -> None:
    service, token, subject_id = _service(tmp_path)
    _confirm(service, token, subject_id)

    analysis = service.academic.read(token=token, subject_id=subject_id)
    assert analysis["contract_version"] == "academic_analysis_v1"
    assert analysis["ruleset_version"] == "academic_ruleset_v1"
    assert len(str(analysis["source_version"])) == 64
    points = analysis["series"][0]["points"]
    assert points[0]["score"] == 0
    assert points[1]["result_state"] == "absent"
    assert points[2]["result_state"] == "makeup"
    assert points[3]["relative_position"] is None
    assert points[0]["rank_origin"] == "teacher_confirmed"
    assert points[0]["cohort_key"] == "synthetic-class-1"
    assert points[0]["ranking_rule_version"] == "school-rule-v1"
    assert all(
        segment["overall_status"] != "directly_comparable"
        for segment in analysis["series"][0]["segments"]
    )


def test_legacy_evidence_is_one_to_one_and_source_version_blocks_stale_decision(tmp_path: Path) -> None:
    service, token, subject_id = _service(tmp_path)
    _confirm(service, token, subject_id)
    analysis = service.academic.read(token=token, subject_id=subject_id)
    evidence_id = str(analysis["sessions"][0]["evidence"][0]["evidence_version_id"])
    snapshot = service.academic.snapshot(token=token, evidence_version_id=evidence_id)
    assert snapshot["raw_file_retained"] is False
    assert "raw_rows" not in snapshot

    card = service.attention.create_from_evidence(
        token=token,
        operation_id="academic-card-0001",
        evidence_version_id=evidence_id,
        observed_fact="合成证据",
        comparability="insufficient_information",
        limitations=[],
        verification_question="是否需要核实？",
        low_risk_next_step="先了解情况",
        evidence_sufficiency="单条证据",
        review_suggestion="一周后复查",
    )
    with pytest.raises(VaultError) as error:
        service.academic.decide(
            token=token,
            attention_card_id=str(card["attention_card_id"]),
            operation_id="academic-decide-0001",
            revision=int(card["revision"]),
            decision="no_action",
            reason="证据不足",
            plan_id=None,
            review_at=None,
            source_version="0" * 64,
        )
    assert error.value.code == "academic_analysis_stale"


def test_attention_decision_and_projection_outbox_roll_back_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, token, subject_id = _service(tmp_path)
    _confirm(service, token, subject_id)
    analysis = service.academic.read(token=token, subject_id=subject_id)
    evidence_id = str(analysis["sessions"][0]["evidence"][0]["evidence_version_id"])
    card = service.attention.create_from_evidence(
        token=token,
        operation_id="academic-card-atomic",
        evidence_version_id=evidence_id,
        observed_fact="合成证据",
        comparability="insufficient_information",
        limitations=[],
        verification_question="是否需要人工跟进？",
        low_risk_next_step="先了解情况",
        evidence_sufficiency="单条证据",
        review_suggestion="一周后复查",
    )

    def fail_projection(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("synthetic outbox failure")

    monkeypatch.setattr(service.projections, "enqueue", fail_projection)
    with pytest.raises(RuntimeError, match="outbox failure"):
        service.academic.decide(
            token=token,
            attention_card_id=str(card["attention_card_id"]),
            operation_id="academic-decide-atomic",
            revision=int(card["revision"]),
            decision="observe",
            reason="需要人工复查。",
            plan_id=None,
            review_at="2026-09-01",
            source_version=str(analysis["source_version"]),
        )

    assert service.attention.get(
        token=token,
        attention_card_id=str(card["attention_card_id"]),
    )["state"] == "draft"
    with closing(service.database.connect()) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sensitive_work_groups WHERE source_kind = 'attention_followup' AND source_id = ?",
            (str(card["attention_card_id"]),),
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM work_plans").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM confirmation_claims WHERE entity_kind = 'attention' AND entity_id = ?",
            (str(card["attention_card_id"]),),
        ).fetchone()[0] == 0


def test_complete_same_basis_series_is_directly_comparable_and_reports_continuous_change(tmp_path: Path) -> None:
    service, token, subject_id = _service(tmp_path)
    assessments = []
    for index, (score, rank) in enumerate(((60, 30), (70, 25), (80, 20)), start=1):
        assessments.append({
            "title": f"连续场次 {index}",
            "subject_name": "数学",
            "occurred_on": f"2026-0{index + 1}-15",
            "max_score": 100,
            "rank_scope": "class",
            "participant_count": 40,
            "assessment_nature": "unit",
            "rank_origin": "teacher_confirmed",
            "cohort_key": "synthetic-class-1",
            "ranking_rule_version": "school-rule-v1",
            "session": {
                "title": f"连续场次 {index}",
                "academic_year": "2025-2026",
                "term": "下学期",
                "grade": "八年级",
                "exam_type": "单元测验",
                "comparison_series": "数学单元",
                "occurred_on": f"2026-0{index + 1}-15",
                "source_reference": f"continuous-{index}",
            },
            "results": [{
                "subject_id": subject_id,
                "result_state": "normal",
                "score": score,
                "rank": rank,
            }],
        })
    batch = ConfirmedSpreadsheetAdapter().read({
        "teacher_confirmed": True,
        "source_label": "仅内存连续变化数据",
        "assessments": assessments,
    })
    service.evidence.confirm_batch(
        token=token,
        operation_id="academic-continuous-confirm",
        batch=batch,
    )

    analysis = service.academic.read(token=token, subject_id=subject_id)
    assert all(
        segment["overall_status"] == "directly_comparable"
        for segment in analysis["series"][0]["segments"]
    )
    assert analysis["recent_changes"][0]["continuous_score_direction"] == "improving"
    assert analysis["recent_changes"][0]["continuous_rank_direction"] == "improving"


def test_partial_session_metadata_is_not_treated_as_comparable(tmp_path: Path) -> None:
    service, token, subject_id = _service(tmp_path)
    assessments = []
    for index in (1, 2):
        assessments.append({
            "title": f"不完整场次 {index}",
            "subject_name": "数学",
            "occurred_on": f"2026-0{index + 1}-20",
            "max_score": 100,
            "rank_scope": "class",
            "participant_count": 40,
            "assessment_nature": "unit",
            "rank_origin": "teacher_confirmed",
            "cohort_key": "synthetic-class-1",
            "ranking_rule_version": "school-rule-v1",
            "session": {"title": f"不完整场次 {index}"},
            "results": [{
                "subject_id": subject_id,
                "result_state": "normal",
                "score": 60 + index,
                "rank": 20 - index,
            }],
        })
    service.evidence.confirm_batch(
        token=token,
        operation_id="academic-incomplete-confirm",
        batch=ConfirmedSpreadsheetAdapter().read({
            "teacher_confirmed": True,
            "source_label": "仅内存不完整元数据",
            "assessments": assessments,
        }),
    )

    analysis = service.academic.read(token=token, subject_id=subject_id)
    assert all(session["metadata_complete"] is False for session in analysis["sessions"])
    segment = analysis["series"][0]["segments"][0]
    assert segment["dimensions"]["score"]["status"] == "insufficient_information"
    assert segment["dimensions"]["rank"]["status"] == "insufficient_information"


def test_comparable_only_filter_uses_server_comparison_rules_not_normal_state(
    tmp_path: Path,
) -> None:
    service, token, subject_id = _service(tmp_path)
    assessments = []
    for index, max_score in enumerate((100, 120), start=1):
        assessments.append({
            "title": f"满分变化场次 {index}",
            "subject_name": "数学",
            "occurred_on": f"2026-0{index + 1}-25",
            "max_score": max_score,
            "rank_scope": "class",
            "participant_count": 40,
            "assessment_nature": "unit",
            "rank_origin": "teacher_confirmed",
            "cohort_key": "synthetic-class-1",
            "ranking_rule_version": "school-rule-v1",
            "session": {
                "title": f"满分变化场次 {index}",
                "academic_year": "2025-2026",
                "term": "下学期",
                "grade": "八年级",
                "exam_type": "单元测验",
                "comparison_series": "数学单元",
                "occurred_on": f"2026-0{index + 1}-25",
                "source_reference": f"max-score-{index}",
            },
            "results": [{
                "subject_id": subject_id,
                "result_state": "normal",
                "score": 60 + index,
                "rank": 20 - index,
            }],
        })
    service.evidence.confirm_batch(
        token=token,
        operation_id="academic-max-score-confirm",
        batch=ConfirmedSpreadsheetAdapter().read({
            "teacher_confirmed": True,
            "source_label": "仅内存满分变化数据",
            "assessments": assessments,
        }),
    )

    unfiltered = service.academic.read(token=token, subject_id=subject_id)
    assert len(unfiltered["sessions"]) == 2
    assert unfiltered["series"][0]["segments"][0]["overall_status"] == "reference_only"

    comparable = service.academic.read(
        token=token,
        subject_id=subject_id,
        comparable_only=True,
    )
    assert comparable["sessions"] == []
    assert comparable["series"] == []
    assert comparable["relative_subject_signals"] == []


def test_time_filters_use_academic_year_and_current_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(academic_module, "_today", lambda: date(2026, 8, 2))
    sessions = [
        {
            "session_id": "same-year-autumn",
            "academic_year": "2025-2026",
            "occurred_on": "2025-09-01",
            "comparison_series": "合成系列",
            "evidence": [{"subject_name": "数学"}],
        },
        {
            "session_id": "same-year-spring",
            "academic_year": "2025-2026",
            "occurred_on": "2026-01-15",
            "comparison_series": "合成系列",
            "evidence": [{"subject_name": "数学"}],
        },
        {
            "session_id": "older-school-year",
            "academic_year": "2024-2025",
            "occurred_on": "2025-06-30",
            "comparison_series": "合成系列",
            "evidence": [{"subject_name": "数学"}],
        },
    ]

    school_year = StudentAcademicAnalysis._filter_sessions(
        sessions,
        time_range="year",
        comparison_series=None,
        subject_name=None,
    )
    assert [item["session_id"] for item in school_year] == [
        "same-year-autumn",
        "same-year-spring",
    ]

    recent_sessions = [
        {**sessions[0], "session_id": "outside", "occurred_on": "2026-05-04"},
        {**sessions[0], "session_id": "boundary", "occurred_on": "2026-05-05"},
        {**sessions[0], "session_id": "today", "occurred_on": "2026-08-02"},
    ]
    recent = StudentAcademicAnalysis._filter_sessions(
        recent_sessions,
        time_range="recent_90",
        comparison_series=None,
        subject_name=None,
    )
    assert [item["session_id"] for item in recent] == ["boundary", "today"]
