from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from types import SimpleNamespace

from update_tools import backup_core


def test_legacy_restore_write_path_is_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    data_root = project_root / "synthetic-data"
    config_root = data_root / "config"
    config_root.mkdir(parents=True)
    current = config_root / "settings.json"
    current.write_text('{"state":"before"}', encoding="utf-8")
    archive = tmp_path / "backup_synthetic_manual.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
        handle.writestr(
            "user_data/config/settings.json",
            '{"state":"restored"}',
        )
    monkeypatch.setattr(
        backup_core,
        "_get_pm",
        lambda: (_ for _ in ()).throw(AssertionError("must remain inert")),
    )
    monkeypatch.setattr(
        backup_core,
        "_get_logger",
        lambda: (_ for _ in ()).throw(AssertionError("must remain inert")),
    )

    result = backup_core.restore_backup(archive)

    assert result["error"] == "legacy_restore_write_disabled"
    assert result["restored_files"] == []
    assert current.read_text(encoding="utf-8") == '{"state":"before"}'


def test_legacy_restore_keeps_read_only_preview_available(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    data_root = project_root / "synthetic-data"
    archive = tmp_path / "backup_synthetic_manual.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
        handle.writestr("user_data/config/settings.json", "{}")
    paths = SimpleNamespace(
        project_root=project_root,
        data_root=data_root,
        logs_dir=project_root / "logs",
    )
    monkeypatch.setattr(backup_core, "_get_pm", lambda: paths)
    monkeypatch.setattr(
        backup_core,
        "_get_logger",
        lambda: logging.getLogger("test.legacy-restore-preview"),
    )

    result = backup_core.restore_backup(archive, dry_run=True)

    assert result["error"] is None
    assert result["restored_files"] == [
        "user_data/config/settings.json -> "
        + str(data_root / "config" / "settings.json")
    ]
    assert not data_root.exists()
