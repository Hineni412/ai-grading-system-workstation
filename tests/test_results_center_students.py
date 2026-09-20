"""成绩中心学生字段：拼音搜索字段与共享拼音帮助函数。"""

from __future__ import annotations

from pathlib import Path

from backend.name_pinyin import student_name_initials, student_name_pinyin
from backend.results_center.service import ResultsCenterService
from backend.review.service import ReviewApplicationService

from tests.test_review_results_question_id_compatibility import (
    _seed_review_result,
)


def test_snapshot_students_carry_pinyin_fields_for_search(
    tmp_path: Path,
) -> None:
    db, session_id, session, manual_context = _seed_review_result(
        tmp_path,
        stored_details=[("Q1", 7.0)],
    )

    snapshot = ResultsCenterService(ReviewApplicationService(db)).get_snapshot(
        session_id,
        session,
        manual_context=manual_context,
    )

    student = snapshot.students[0]
    assert student.pinyin_initials == student_name_initials("Student A")
    assert student.pinyin_full == student_name_pinyin("Student A")
    # 拼音字段始终为字符串，前端解码按精确键集校验。
    assert isinstance(student.pinyin_initials, str)
    assert isinstance(student.pinyin_full, str)


def test_name_pinyin_covers_surname_initials_and_full_spelling() -> None:
    assert student_name_initials("张三") == "zs"
    assert student_name_initials("欧阳修") == "oyx"
    assert student_name_pinyin("张三") == "zhangsan"
    assert student_name_pinyin("欧阳修") == "ouyangxiu"
    # 空值不产生异常，退化为空字符串。
    assert student_name_initials("") == ""
    assert student_name_pinyin("") == ""
    assert student_name_initials(None) == ""
    assert student_name_pinyin(None) == ""
