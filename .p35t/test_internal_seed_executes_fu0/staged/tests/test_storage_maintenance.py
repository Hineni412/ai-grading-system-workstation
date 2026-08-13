from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tools.storage_maintenance import (
    ArchiveAction,
    DedupeAction,
    build_archive_actions,
    build_dedupe_actions,
    is_hardlink_eligible,
)


OLD_TIMESTAMP = 1_700_000_000
NOW_TIMESTAMP = OLD_TIMESTAMP + 3 * 24 * 60 * 60


def _make_old_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (OLD_TIMESTAMP, OLD_TIMESTAMP))


def test_hardlink_eligibility_blocks_database(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    db_path = data_root / "databases" / "grading_system.db"
    _make_old_file(db_path, b"x" * 200_000)

    assert not is_hardlink_eligible(db_path, data_root, now_timestamp=NOW_TIMESTAMP)


def test_hardlink_eligibility_allows_old_large_annotated_jpg(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    image_path = data_root / "annotated" / "session_1" / "a.jpg"
    _make_old_file(image_path, b"x" * 200_000)

    assert is_hardlink_eligible(image_path, data_root, now_timestamp=NOW_TIMESTAMP)


def test_hardlink_eligibility_blocks_recent_files(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    image_path = data_root / "annotated" / "session_1" / "a.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"x" * 200_000)

    assert not is_hardlink_eligible(image_path, data_root, now_timestamp=image_path.stat().st_mtime)


def test_build_dedupe_actions_keeps_first_path_as_canonical(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    first = data_root / "annotated" / "session_1" / "a.jpg"
    second = data_root / "annotated" / "session_1" / "b.jpg"
    _make_old_file(first, b"x" * 200_000)
    _make_old_file(second, b"x" * 200_000)

    actions = build_dedupe_actions(data_root, now_timestamp=NOW_TIMESTAMP)

    assert actions == [DedupeAction(canonical=first, duplicate=second, bytes_saved=200_000)]


def test_build_archive_actions_marks_old_report_page_dir(tmp_path: Path) -> None:
    data_root = tmp_path / "user_data"
    report_dir = data_root / "reports" / "0526_批注原卷页面_20260526_235334"
    _make_old_file(report_dir / "page.jpg", b"x" * 10)
    os.utime(report_dir, (OLD_TIMESTAMP, OLD_TIMESTAMP))

    actions = build_archive_actions(data_root, now_timestamp=OLD_TIMESTAMP + 9 * 24 * 60 * 60)

    assert actions == [
        ArchiveAction(
            source=report_dir,
            archive_path=data_root / "archives" / "old_report_page_dir" / "0526_批注原卷页面_20260526_235334.zip",
            reason="old_report_page_dir",
            bytes_archived=10,
        )
    ]


def test_storage_maintenance_runs_as_script(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    (project_root / "user_data").mkdir(parents=True)

    result = subprocess.run(
        [sys.executable, "tools/storage_maintenance.py", "--root", str(project_root)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 0
    assert "Dry run only" in result.stdout
