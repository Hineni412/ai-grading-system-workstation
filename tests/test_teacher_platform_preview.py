from __future__ import annotations

import subprocess

import pytest

from tools import teacher_platform_preview as preview


def _git_repo(repo, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_tracked_code_changes_ignore_preview_data_but_not_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    database = tmp_path / "user_data" / "databases" / "preview.db"
    database.parent.mkdir(parents=True)
    database.write_text("baseline", encoding="utf-8")
    source = tmp_path / "app.py"
    source.write_text("baseline", encoding="utf-8")
    _git_repo(tmp_path, "init", "--quiet")
    _git_repo(tmp_path, "config", "user.email", "preview-test@example.invalid")
    _git_repo(tmp_path, "config", "user.name", "Preview Test")
    _git_repo(tmp_path, "add", "--", "app.py", "user_data/databases/preview.db")
    _git_repo(tmp_path, "commit", "--quiet", "-m", "baseline")
    monkeypatch.setattr(preview, "PROJECT_ROOT", tmp_path)

    def preview_git(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
        return completed.stdout.strip()

    monkeypatch.setattr(preview, "_git", preview_git)

    database.write_text("runtime change", encoding="utf-8")
    assert preview._tracked_code_changes() == ""

    source.write_text("source change", encoding="utf-8")
    assert "app.py" in preview._tracked_code_changes()


def test_check_preview_rejects_a_build_from_an_older_preview_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_heads = {
        branch: f"head-{index}"
        for index, branch in enumerate(preview.SOURCE_BRANCHES)
    }
    monkeypatch.setattr(
        preview,
        "_assert_preview_workspace",
        lambda: ("current-preview-head", source_heads),
    )
    monkeypatch.setattr(preview, "_assert_workspace_labels", lambda: None)
    monkeypatch.setattr(
        preview,
        "_read_stamp",
        lambda: {
            "preview_head": "older-preview-head",
            "source_checkpoints": source_heads,
        },
    )

    with pytest.raises(preview.PreviewGuardError, match="页面仍是旧构建"):
        preview.check_preview()


def test_workspace_label_check_requires_both_new_workspaces(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    bundle = assets / "index.js"
    bundle.write_text("备课工作台", encoding="utf-8")
    monkeypatch.setattr(preview, "DIST_DIR", dist)

    with pytest.raises(preview.PreviewGuardError, match="班主任工作台"):
        preview._assert_workspace_labels()

    bundle.write_text("备课工作台 班主任工作台", encoding="utf-8")
    preview._assert_workspace_labels()
