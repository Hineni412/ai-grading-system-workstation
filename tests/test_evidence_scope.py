from __future__ import annotations

from integration.evidence_scope import EvidenceScopeResolver


class _GradingData:
    def list_grading_sessions(self):
        return [
            {"id": 1, "session_name": "旧考试", "created_at": "2026-01-01", "is_deleted": False},
            {"id": 2, "session_name": "当前考试", "created_at": "2026-02-01", "is_deleted": False},
            {"id": 3, "session_name": "未来考试", "created_at": "2026-03-01", "is_deleted": False},
        ]

    def list_students(self):
        return [
            {"id": 1, "student_code": "A", "name": "甲", "class_name": "1班"},
            {"id": 2, "student_code": "B", "name": "乙", "class_name": "1班"},
            {"id": 3, "student_code": "C", "name": "丙", "class_name": "2班"},
        ]

    def get_session_results(self, session_id: int):
        return {
            1: [{"student_code": "B", "student_score": 60, "total_score": 100}],
            2: [{"student_code": "A", "student_score": 80, "total_score": 100}],
            3: [{"student_code": "B", "student_score": 100, "total_score": 100}],
        }[session_id]


def test_semester_uses_all_attached_exams_and_never_other_term_history():
    class SemesterData(_GradingData):
        def list_grading_sessions(self):
            return [{**row, "curriculum_volume_id": "this-term" if row["id"] in (2, 3) else "old-term"}
                    for row in super().list_grading_sessions()]

    resolved = EvidenceScopeResolver(SemesterData()).resolve(
        scope={"mode": "all", "use_historical_fallback": True},
        exam_scope={"mode": "semester", "curriculum_volume_id": "this-term", "session_ids": [1, 2]},
    )
    assert [row["id"] for row in resolved.sessions] == [2, 3]
    assert resolved.score_profiles["2"]["score_rate"] == 1
    assert all(not ids for ids in resolved.historical_session_ids_by_student.values())
    assert all(row["historical_exam_count"] == 0 for row in resolved.score_profiles.values())
    for volume in ("", "term-without-exams"):
        empty = EvidenceScopeResolver(SemesterData()).resolve(
            scope={"mode": "all", "score_rate_min": .2},
            exam_scope={"mode": "semester", "curriculum_volume_id": volume},
        )
        assert not empty.sessions and not empty.students
        assert all(row["score_rate"] is None for row in empty.score_profiles.values())


def test_scope_uses_only_prior_history_and_applies_manual_union_last() -> None:
    resolved = EvidenceScopeResolver(_GradingData()).resolve(
        scope={
            "mode": "all",
            "score_rate_min": 0.5,
            "score_rate_max": 0.85,
            "exclude_student_ids": ["1"],
            "include_student_ids": ["3"],
            "use_historical_fallback": True,
        },
        exam_scope={"mode": "current", "session_ids": [2]},
    )

    assert [str(item["id"]) for item in resolved.students] == ["2", "3"]
    assert resolved.score_profiles["2"] == {
        "score_rate": 0.6,
        "score_rate_source": "historical_fallback",
        "historical_exam_count": 1,
        "historical_latest_exam_at": "2026-01-01",
    }
    assert resolved.historical_session_ids_by_student["2"] == (1,)
    assert resolved.score_profiles["3"]["score_rate_source"] == "none"


def test_scope_can_disable_historical_score_fallback() -> None:
    resolved = EvidenceScopeResolver(_GradingData()).resolve(
        scope={
            "mode": "all",
            "score_rate_min": 0.5,
            "use_historical_fallback": False,
        },
        exam_scope={"mode": "current", "session_ids": [2]},
    )

    assert [str(item["id"]) for item in resolved.students] == ["1"]
    assert resolved.score_profiles["2"]["score_rate_source"] == "none"
    assert resolved.historical_session_ids_by_student["2"] == (1,)


def test_scope_supports_multiple_classes_and_rejects_cross_class_manual_include() -> None:
    resolved = EvidenceScopeResolver(_GradingData()).resolve(
        scope={
            "mode": "class",
            "class_ids": ["2班"],
            "include_student_ids": ["1", "3"],
        },
        exam_scope={"mode": "current", "session_ids": [2]},
    )

    assert [str(item["id"]) for item in resolved.students] == ["3"]
    assert any("不属于当前班级" in warning for warning in resolved.warnings)
    normalized = resolved.normalized_scope({"mode": "class", "class_ids": ["2班"]})
    assert normalized["class_ids"] == ["2班"]
    assert len(normalized["scope_revision"]) == 64


def test_selected_scope_preserves_requested_student_order() -> None:
    """Selected 队列的学生顺序必须与请求一致。

    前端把队列（勾选顺序）作为 student_ids 发出，并用有序子序列校验响应
    scope.student_ids 是否与请求一致；按花名册顺序回包会让该校验失败，
    前端随即提示“知识图谱暂时无法更新”。
    """
    resolved = EvidenceScopeResolver(_GradingData()).resolve(
        scope={"mode": "selected", "student_ids": ["3", "1"]},
        exam_scope={"mode": "current", "session_ids": [2]},
    )

    assert [str(item["id"]) for item in resolved.students] == ["3", "1"]
    normalized = resolved.normalized_scope(
        {"mode": "selected", "student_ids": ["3", "1"]}
    )
    assert normalized["student_ids"] == ["3", "1"]


def test_selected_scope_orders_manual_include_after_requested() -> None:
    resolved = EvidenceScopeResolver(_GradingData()).resolve(
        scope={
            "mode": "selected",
            "student_ids": ["3", "1"],
            "include_student_ids": ["2"],
        },
        exam_scope={"mode": "current", "session_ids": [2]},
    )

    assert [str(item["id"]) for item in resolved.students] == ["3", "1", "2"]
