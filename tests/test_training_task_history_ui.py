from __future__ import annotations

from pathlib import Path


PAGE_SOURCE = Path("pages/训练推荐.py").read_text(encoding="utf-8")


def test_training_page_exposes_task_history_and_failed_export_retry() -> None:
    assert "历史训练任务" in PAGE_SOURCE
    assert "重试失败导出" in PAGE_SOURCE
    assert "训练结果回流尚未启用" in PAGE_SOURCE


def test_training_page_exports_only_from_saved_tasks() -> None:
    assert "TrainingExportService" in PAGE_SOURCE
    assert "导出学生卷和教师卷" in PAGE_SOURCE
    assert "导出完整任务包" in PAGE_SOURCE
    assert "get_task" in PAGE_SOURCE
