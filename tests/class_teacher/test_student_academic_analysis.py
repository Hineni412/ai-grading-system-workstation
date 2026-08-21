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


def test_adjacent_rank_change_reports_rank_numbers(tmp_path: Path) -> None:
    service, token, subject_id = _service(tmp_path)
    assessments = []
    for index, (occurred, score, rank) in enumerate(
        [("2026-03-01", 61, 40), ("2026-04-01", 78, 12)],
        start=1,
    ):
        assessments.append({
            "title": f"合成相邻场次 {index}",
            "subject_name": "数学",
            "occurred_on": occurred,
            "max_score": 100,
            "rank_scope": "class",
            "participant_count": 40,
            "assessment_nature": "unit",
            "rank_origin": "teacher_confirmed",
            "cohort_key": "synthetic-class-1",
            "ranking_rule_version": "school-rule-v1",
            "session": {
                "title": f"合成相邻场次 {index}",
                "academic_year": "2025-2026",
                "term": "下学期",
                "grade": "八年级",
                "exam_type": "单元测验",
                "comparison_series": "数学单元",
                "occurred_on": occurred,
                "source_reference": f"synthetic-adjacent-{index}",
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
        "source_label": "仅内存合成相邻排名数据",
        "assessments": assessments,
    })
    service.evidence.confirm_batch(
        token=token, operation_id="academic-confirm-rank", batch=batch
    )

    analysis = service.academic.read(token=token, subject_id=subject_id)
    pairs = analysis["rank_change_pairs"]
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair["subject_name"] == "数学"
    assert pair["from_rank"] == 40
    assert pair["to_rank"] == 12
    assert pair["rank_delta"] == 28
    assert pair["delta"] == pytest.approx(28 / 39)


def test_rank_pairs_follow_semester_chain_across_grades(tmp_path: Path) -> None:
    service, token, subject_id = _service(tmp_path)
    # 八上期中的考试日期被错填得比七下期末还早，学期链条仍应把七下期末排在前面
    cases = [
        ("2026-06-20", "下学期", "七年级", "期末考试", 90, 100),
        ("2026-01-05", "上学期", "八年级", "期中考试", 95, 80),
    ]
    assessments = []
    for index, (occurred, term, grade, exam_type, score, rank) in enumerate(cases, start=1):
        assessments.append({
            "title": f"合成跨年级场次 {index}",
            "subject_name": "数学",
            "occurred_on": occurred,
            "max_score": 100,
            "rank_scope": "grade",
            "participant_count": 320,
            "assessment_nature": exam_type,
            "rank_origin": "teacher_confirmed",
            "cohort_key": "same-grade-cohort",
            "ranking_rule_version": "school-export-v1",
            "session": {
                "title": f"合成跨年级场次 {index}",
                "academic_year": "2025-2026",
                "term": term,
                "grade": grade,
                "exam_type": exam_type,
                "comparison_series": "class-regular",
                "occurred_on": occurred,
                "source_reference": f"synthetic-chain-{index}",
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
        "source_label": "仅内存合成跨年级数据",
        "assessments": assessments,
    })
    service.evidence.confirm_batch(
        token=token, operation_id="academic-confirm-chain", batch=batch
    )

    analysis = service.academic.read(token=token, subject_id=subject_id)
    pairs = analysis["rank_change_pairs"]
    assert len(pairs) == 1
    pair = pairs[0]
    # 七下期末(100名) → 八上期中(80名)，而不是按日期倒过来
    assert pair["from_rank"] == 100
    assert pair["to_rank"] == 80
    assert pair["rank_delta"] == 20
    assert pair["rank_scope"] == "grade"


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


def _profile_assessments(
    subject_id: str,
    rows: list[tuple[str, str, str, str, str, int, int]],
) -> list[dict[str, object]]:
    """rows: (subject, occurred, term, grade, exam_type, score, rank)."""
    sessions: dict[str, dict[str, object]] = {}
    for subject, occurred, term, grade, exam_type, score, rank in rows:
        key = occurred
        session = sessions.setdefault(key, {
            "title": f"合成画像场次 {occurred}",
            "academic_year": "2025-2026",
            "term": term,
            "grade": grade,
            "exam_type": exam_type,
            "comparison_series": "class-regular",
            "occurred_on": occurred,
            "source_reference": f"synthetic-profile-{occurred}",
        })
        session.setdefault("subjects", []).append((subject, score, rank))
    assessments = []
    for occurred, session in sessions.items():
        for subject, score, rank in session["subjects"]:
            assessments.append({
                "title": session["title"],
                "subject_name": subject,
                "occurred_on": occurred,
                "max_score": 100,
                "rank_scope": "grade",
                "participant_count": 400,
                "measure_role": "total_score" if subject == "总分" else "subject_score",
                "assessment_nature": session["exam_type"],
                "rank_origin": "teacher_confirmed",
                "cohort_key": "same-grade-cohort",
                "ranking_rule_version": "school-export-v1",
                "session": {key: value for key, value in session.items() if key != "subjects"},
                "results": [{
                    "subject_id": subject_id,
                    "result_state": "normal",
                    "score": score,
                    "rank": rank,
                }],
            })
    return assessments


def _confirm_assessments(
    service: VaultService,
    token: str,
    assessments: list[dict[str, object]],
    operation_id: str,
) -> None:
    batch = ConfirmedSpreadsheetAdapter().read({
        "teacher_confirmed": True,
        "source_label": "仅内存合成画像数据",
        "assessments": assessments,
    })
    service.evidence.confirm_batch(token=token, operation_id=operation_id, batch=batch)


def test_profile_computes_positioning_trend_stability_and_skew(tmp_path: Path) -> None:
    service, token, subject_id = _service(tmp_path)
    rows = [
        # 七上期中：总分 200 名
        ("总分", "2025-11-01", "上学期", "七年级", "期中考试", 70, 200),
        ("语文", "2025-11-01", "上学期", "七年级", "期中考试", 80, 100),
        ("数学", "2025-11-01", "上学期", "七年级", "期中考试", 75, 100),
        ("英语", "2025-11-01", "上学期", "七年级", "期中考试", 78, 120),
        # 七上期末：总分 100 名
        ("总分", "2026-01-10", "上学期", "七年级", "期末考试", 78, 100),
        ("语文", "2026-01-10", "上学期", "七年级", "期末考试", 85, 60),
        ("数学", "2026-01-10", "上学期", "七年级", "期末考试", 70, 200),
        ("英语", "2026-01-10", "上学期", "七年级", "期末考试", 82, 90),
        # 七下期中：总分 40 名
        ("总分", "2026-04-20", "下学期", "七年级", "期中考试", 88, 40),
        ("语文", "2026-04-20", "下学期", "七年级", "期中考试", 92, 20),
        ("数学", "2026-04-20", "下学期", "七年级", "期中考试", 65, 300),
        ("英语", "2026-04-20", "下学期", "七年级", "期中考试", 85, 100),
    ]
    _confirm_assessments(
        service, token, _profile_assessments(subject_id, rows), "academic-profile-0001"
    )

    analysis = service.academic.read(token=token, subject_id=subject_id)
    profile = analysis["profile"]

    current = profile["current"]
    assert current["rank"] == 40
    assert current["participant_count"] == 400
    assert current["top_ratio"] == pytest.approx(0.1)
    assert current["term_label"] == "七下"
    assert current["previous"]["rank"] == 100
    assert current["rank_delta"] == 60

    # 相对位次 0.501 → 0.752 → 0.902，两步同向且超过阈值
    assert profile["trend"]["label"] == "improving"
    assert profile["trend"]["session_count"] == 3
    # 振幅约 0.40，属于波动大
    assert profile["stability"]["label"] == "volatile"
    assert profile["stability"]["swing_ratio"] == pytest.approx(
        (1 - 39 / 399) - (1 - 199 / 399)
    )

    # 七下期中：语文前 10%（20/400），数学后 50%（300/400）→ 明显偏科
    assert profile["skew"]["label"] == "skewed"
    assert profile["skew"]["strongest"][0]["subject_name"] == "语文"
    assert profile["skew"]["weakest"][0]["subject_name"] == "数学"

    subjects = {item["subject_name"]: item for item in profile["subjects"]}
    assert subjects["数学"]["latest"]["rank"] == 300
    # 数学 200 名 → 300 名，下滑 100/400 = 0.25，超过关注阈值
    assert subjects["数学"]["rank_delta"] == -100
    assert subjects["数学"]["attention"] is True
    # 最弱两科（数学、英语）也带关注标记
    assert subjects["英语"]["attention"] is True
    assert subjects["语文"]["attention"] is False
    assert [point["rank"] for point in subjects["语文"]["points"]] == [100, 60, 20]


def test_profile_marks_insufficient_and_ignores_cross_grade_ranks(tmp_path: Path) -> None:
    service, token, subject_id = _service(tmp_path)
    rows = [
        ("总分", "2025-11-01", "上学期", "七年级", "期中考试", 70, 210),
        ("语文", "2025-11-01", "上学期", "七年级", "期中考试", 80, 200),
        ("数学", "2025-11-01", "上学期", "七年级", "期中考试", 75, 205),
        ("英语", "2025-11-01", "上学期", "七年级", "期中考试", 78, 195),
        ("总分", "2026-01-10", "上学期", "七年级", "期末考试", 71, 200),
        ("语文", "2026-01-10", "上学期", "七年级", "期末考试", 81, 190),
        ("数学", "2026-01-10", "上学期", "七年级", "期末考试", 76, 210),
        ("英语", "2026-01-10", "上学期", "七年级", "期末考试", 79, 200),
        # 升入八年级后名次口径不同，不得与七年级场次直接比较
        ("总分", "2026-11-01", "上学期", "八年级", "期中考试", 60, 350),
        ("语文", "2026-11-01", "上学期", "八年级", "期中考试", 70, 330),
        ("数学", "2026-11-01", "上学期", "八年级", "期中考试", 55, 360),
        ("英语", "2026-11-01", "上学期", "八年级", "期中考试", 65, 340),
    ]
    _confirm_assessments(
        service, token, _profile_assessments(subject_id, rows), "academic-profile-0002"
    )

    analysis = service.academic.read(token=token, subject_id=subject_id)
    profile = analysis["profile"]

    # 最新一场在八年级，同年级只有一场 → 趋势、进退都不可判定
    assert profile["current"]["rank"] == 350
    assert profile["current"]["previous"] is None
    assert profile["current"]["rank_delta"] is None
    assert profile["trend"]["label"] == "insufficient"
    assert profile["stability"]["label"] == "insufficient"
    subjects = {item["subject_name"]: item for item in profile["subjects"]}
    assert subjects["数学"]["rank_delta"] is None
    # 单科 points 仍保留全部场次（含七年级）
    assert [point["rank"] for point in subjects["数学"]["points"]] == [205, 210, 360]


def test_ai_summary_card_flows_into_model_context(tmp_path: Path) -> None:
    service, token, subject_id = _service(tmp_path)
    rows = [
        ("总分", "2025-11-01", "上学期", "七年级", "期中考试", 70, 200),
        ("语文", "2025-11-01", "上学期", "七年级", "期中考试", 80, 30),
        ("数学", "2025-11-01", "上学期", "七年级", "期中考试", 60, 320),
        ("英语", "2025-11-01", "上学期", "七年级", "期中考试", 78, 150),
    ]
    _confirm_assessments(
        service, token, _profile_assessments(subject_id, rows), "academic-profile-0003"
    )

    context = service.student_cards.model_context(token=token, subject_id=subject_id)
    summary = context["academic_summary"]
    assert summary["contract_version"] == "academic_ai_summary_v1"
    assert summary["total"]["rank"] == 200
    assert summary["total"]["participant_count"] == 400
    assert summary["latest_exam"]["term_label"] == "七上"
    assert summary["trend"] == "insufficient"
    assert summary["skew"]["label"] == "skewed"
    assert summary["skew"]["strongest"] == ["语文"]
    assert summary["skew"]["weakest"] == ["数学"]
    subjects = {item["name"]: item for item in summary["subjects"]}
    assert subjects["数学"]["latest_rank"] == 320
    assert subjects["数学"]["attention"] is True

    # 没有任何成绩证据的学生：摘要卡为 None，不阻塞 AI 流程
    other = service.support.create_subject(
        token=token,
        operation_id="academic-subject-0002",
        source_student_id="academic-student-002",
        display_name="无成绩学生",
        class_label="合成一班",
    )
    empty_context = service.student_cards.model_context(
        token=token, subject_id=str(other["subject_id"])
    )
    assert empty_context["academic_summary"] is None
