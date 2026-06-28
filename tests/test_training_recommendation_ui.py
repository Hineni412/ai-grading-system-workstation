from pathlib import Path


PAGE_SOURCE = Path("pages/训练推荐.py").read_text(encoding="utf-8")


def test_training_page_uses_current_question_tag_diagnosis_and_task_services() -> None:
    for service in ("DiagnosisProfileService", "PracticePlanService", "TrainingTaskService"):
        assert service in PAGE_SOURCE
    assert ".build_tag_profiles(" in PAGE_SOURCE
    assert "SkillCatalogService" not in PAGE_SOURCE
    assert "question_skill_links" not in PAGE_SOURCE


def test_training_page_keeps_scope_selection_and_snapshot_safety() -> None:
    for label in ("单个学生", "筛选多个学生", "全部班级", "当前考试", "跨考试", "手动选择考试"):
        assert label in PAGE_SOURCE
    assert "明确选择要训练的学生" in PAGE_SOURCE
    assert "保存训练任务" in PAGE_SOURCE
    assert "请先保存训练任务" in PAGE_SOURCE
    assert "PLAN_SIGNATURE_KEY" in PAGE_SOURCE


def test_diagnosis_displays_exact_knowledge_tags_and_current_candidate_counts() -> None:
    for label in ("薄弱知识点", "掌握率", "证据题数", "题库同标签题数"):
        assert label in PAGE_SOURCE
    assert "knowledge_point" in PAGE_SOURCE
    assert "tag_value_counts" in PAGE_SOURCE
    for removed in (
        "skill_id",
        "topic_name",
        "target_skill_name",
        "matched_skill_name",
        "resolve_conflict",
        "allow_neighbors",
        "相近补入",
    ):
        assert removed not in PAGE_SOURCE


def test_training_generation_is_exact_tag_only() -> None:
    assert 'related_fill_policy="exact_only"' in PAGE_SOURCE
    assert "知识点标签完全相同" in PAGE_SOURCE
    assert "精确知识点标签题不足" in PAGE_SOURCE
