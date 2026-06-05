from __future__ import annotations

from pathlib import Path

from tools.storage_audit import (
    classify_user_data_path,
    duplicate_groups,
    find_retention_candidates,
    is_protected_path,
    scan_file_records,
    write_reports,
)


def test_duplicate_groups_find_same_content(tmp_path: Path) -> None:
    root = tmp_path / "project"
    first = root / "user_data" / "templates" / "session_1" / "a.pdf"
    second = root / "user_data" / "templates" / "session_2" / "b.pdf"
    third = root / "user_data" / "reports" / "c.jpg"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    third.parent.mkdir(parents=True)
    first.write_bytes(b"same-content")
    second.write_bytes(b"same-content")
    third.write_bytes(b"different")

    records = scan_file_records(root / "user_data")
    groups = duplicate_groups(records)

    assert len(groups) == 1
    assert groups[0].count == 2
    assert groups[0].recoverable_bytes == len(b"same-content")


def test_classify_user_data_path_returns_top_category(tmp_path: Path) -> None:
    path = tmp_path / "user_data" / "annotated" / "session_13" / "result.jpg"
    assert classify_user_data_path(path, tmp_path / "user_data") == "annotated"


def test_is_protected_path_blocks_databases_and_json(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    assert is_protected_path(data_root / "databases" / "grading_system.db", data_root)
    assert is_protected_path(data_root / "config" / "api_profiles.json", data_root)
    assert not is_protected_path(data_root / "annotated" / "session_1" / "a.jpg", data_root)


def test_find_retention_candidates_marks_old_report_page_dirs(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    old_dir = data_root / "reports" / "0526_批注原卷页面_20260526_235334"
    old_dir.mkdir(parents=True)
    (old_dir / "page.jpg").write_bytes(b"x" * 10)
    old_timestamp = 1_700_000_000
    for path in (old_dir, old_dir / "page.jpg"):
        path.touch()
        path.chmod(0o666)
    import os

    os.utime(old_dir, (old_timestamp, old_timestamp))
    os.utime(old_dir / "page.jpg", (old_timestamp, old_timestamp))

    candidates = find_retention_candidates(data_root, now_timestamp=old_timestamp + 9 * 24 * 60 * 60)

    assert len(candidates) == 1
    assert candidates[0].path == str(old_dir)
    assert candidates[0].reason == "old_report_page_dir"


def test_write_reports_creates_summary(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    data_root = project_root / "user_data"
    first = data_root / "templates" / "session_1" / "a.pdf"
    second = data_root / "templates" / "session_2" / "b.pdf"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"same-content")
    second.write_bytes(b"same-content")

    records = scan_file_records(data_root)
    groups = duplicate_groups(records)
    summary = write_reports(project_root, records, groups, [])

    assert summary.exists()
    assert "Estimated recoverable MB" in summary.read_text(encoding="utf-8")
