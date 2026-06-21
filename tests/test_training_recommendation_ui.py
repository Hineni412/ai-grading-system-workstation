from __future__ import annotations

from pathlib import Path


PAGE_SOURCE = Path("pages/训练推荐.py").read_text(encoding="utf-8")


def test_training_page_uses_real_diagnosis_and_task_services() -> None:
    assert "DiagnosisProfileService" in PAGE_SOURCE
    assert "PracticePlanService" in PAGE_SOURCE
    assert "TrainingTaskService" in PAGE_SOURCE
    assert "load_sample_mastery_rows" not in PAGE_SOURCE


def test_training_page_supports_all_student_and_exam_scope_modes() -> None:
    for label in ("单个学生", "筛选多个学生", "全部班级"):
        assert label in PAGE_SOURCE
    for label in ("当前考试", "跨考试", "手动选择考试"):
        assert label in PAGE_SOURCE


def test_training_page_keeps_explicit_selection_and_alignment_entry() -> None:
    assert "明确选择要训练的学生" in PAGE_SOURCE
    assert "selected_student_ids" in PAGE_SOURCE
    assert "ALIGNMENT_FOCUS_SESSION_KEY" in PAGE_SOURCE
    assert "focus_items_from_diagnosis" in PAGE_SOURCE
    assert "AlignmentReviewService" in PAGE_SOURCE
    assert "使用这个匹配" in PAGE_SOURCE
    assert "本次不推荐" in PAGE_SOURCE
    assert "alignment_revision" in PAGE_SOURCE
    assert "前往知识点对齐中心处理" not in PAGE_SOURCE
    assert "知识图谱适配调试.py" in PAGE_SOURCE


def test_training_page_requires_preview_to_be_saved_before_export() -> None:
    assert "保存训练任务" in PAGE_SOURCE
    assert "请先保存训练任务" in PAGE_SOURCE
    assert "exclude_current_exam_originals" in PAGE_SOURCE
    assert "PLAN_SIGNATURE_KEY" in PAGE_SOURCE


def test_training_page_exposes_safety_states() -> None:
    for message in (
        "未选择学生",
        "未选择考试",
        "没有已确认映射的薄弱知识点",
        "题库数据库不可用",
    ):
        assert message in PAGE_SOURCE


def test_training_page_defaults_to_a_simple_teacher_workflow() -> None:
    assert 'st.title("生成错题巩固练习")' in PAGE_SOURCE
    assert 'st.expander("高级设置")' in PAGE_SOURCE
    assert "基础巩固" in PAGE_SOURCE
    assert "针对训练" in PAGE_SOURCE
    assert "提升应用" in PAGE_SOURCE
    assert "允许仅大类匹配的题目补足" in PAGE_SOURCE
    assert "推荐排序权重" not in PAGE_SOURCE
    assert "包含历史错题回流" not in PAGE_SOURCE
    assert "include_historical_wrong_questions" not in PAGE_SOURCE
