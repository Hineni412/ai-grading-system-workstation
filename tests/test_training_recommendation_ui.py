from pathlib import Path


PAGE_SOURCE = Path("pages/训练推荐.py").read_text(encoding="utf-8")


def test_training_page_uses_real_skill_diagnosis_and_task_services() -> None:
    for service in ("DiagnosisProfileService", "PracticePlanService", "TrainingTaskService", "SkillCatalogService"):
        assert service in PAGE_SOURCE
    assert "load_sample_mastery_rows" not in PAGE_SOURCE


def test_training_page_keeps_scope_selection_and_snapshot_safety() -> None:
    for label in ("单个学生", "筛选多个学生", "全部班级", "当前考试", "跨考试", "手动选择考试"):
        assert label in PAGE_SOURCE
    assert "明确选择要训练的学生" in PAGE_SOURCE
    assert "保存训练任务" in PAGE_SOURCE
    assert "请先保存训练任务" in PAGE_SOURCE
    assert "PLAN_SIGNATURE_KEY" in PAGE_SOURCE


def test_diagnosis_uses_plain_concrete_skill_columns() -> None:
    for label in ("薄弱技能", "所属主题", "掌握率", "证据题数", "精确题数"):
        assert label in PAGE_SOURCE
    for removed in (
        "ConceptAlignmentService",
        "AlignmentReviewService",
        "ALIGNMENT_FOCUS_SESSION_KEY",
        "focus_items_from_diagnosis",
        "打开知识点整理（高级）",
        "映射状态",
        "置信度",
        "canonical_knowledge_id",
        "允许仅大类匹配的题目补足",
    ):
        assert removed not in PAGE_SOURCE
    assert "background_gradient" not in PAGE_SOURCE
    assert "matplotlib" not in PAGE_SOURCE


def test_shortage_has_one_teacher_decision_and_neighbors_are_labeled() -> None:
    assert '_generate_selected_plan("ask")' in PAGE_SOURCE
    assert "related_fill_policy=fill_policy" in PAGE_SOURCE
    assert "补入相近题" in PAGE_SOURCE
    assert "保持较少的精确题" in PAGE_SOURCE
    assert "allow_neighbors" in PAGE_SOURCE
    assert "exact_only" in PAGE_SOURCE
    assert "相近补入" in PAGE_SOURCE
    assert "target_skill_name" in PAGE_SOURCE
    assert "matched_skill_name" in PAGE_SOURCE


def test_resolved_evidence_needs_no_confirmation_action() -> None:
    assert "无需逐条确认" in PAGE_SOURCE
    assert "本次跳过" in PAGE_SOURCE
    assert "resolve_conflict" in PAGE_SOURCE
