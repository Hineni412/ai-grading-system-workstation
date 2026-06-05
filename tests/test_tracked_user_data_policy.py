from __future__ import annotations

from tools.check_tracked_user_data import is_allowed_tracked_user_data


def test_allows_gitkeep() -> None:
    assert is_allowed_tracked_user_data("user_data/exams/.gitkeep")


def test_blocks_pdf_user_data() -> None:
    assert not is_allowed_tracked_user_data("user_data/exams/02.pdf")


def test_blocks_database_user_data() -> None:
    assert not is_allowed_tracked_user_data("user_data/databases/grading_system.db")
