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


def test_open_running_preview_reuses_the_current_healthy_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        preview,
        "check_preview",
        lambda: {"preview_head": "84c2e183" + "0" * 32},
    )
    monkeypatch.setattr(
        preview,
        "_fetch_preview_health",
        lambda _port: {
            "status": "ok",
            "service": "ai-grading-api",
            "preview_instance_id": "teacher-platform-integration",
            "preview_head": "84c2e183" + "0" * 32,
        },
    )

    assert preview.open_running_preview(
        port=8035,
        browser_open=lambda url: opened.append(url) or True,
    ) is True
    assert opened == ["http://127.0.0.1:8035/teaching-prep?preview=84c2e183"]


def test_open_running_preview_distinguishes_stopped_and_wrong_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preview,
        "check_preview",
        lambda: {"preview_head": "84c2e183" + "0" * 32},
    )
    monkeypatch.setattr(preview, "_fetch_preview_health", lambda _port: None)
    assert preview.open_running_preview(
        port=8035,
        browser_open=lambda _url: pytest.fail("browser should stay closed"),
    ) is False

    monkeypatch.setattr(
        preview,
        "_fetch_preview_health",
        lambda _port: {"status": "ok", "service": "another-service"},
    )
    with pytest.raises(preview.PreviewGuardError, match="不是三合一预览服务"):
        preview.open_running_preview(
            port=8035,
            browser_open=lambda _url: pytest.fail("browser should stay closed"),
        )


def test_open_running_preview_rejects_an_old_preview_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preview,
        "check_preview",
        lambda: {"preview_head": "84c2e183" + "0" * 32},
    )
    monkeypatch.setattr(
        preview,
        "_fetch_preview_health",
        lambda _port: {
            "status": "ok",
            "service": "ai-grading-api",
            "preview_instance_id": "teacher-platform-integration",
            "preview_head": "older-head",
        },
    )

    with pytest.raises(preview.PreviewGuardError, match="旧版三合一预览"):
        preview.open_running_preview(
            port=8035,
            browser_open=lambda _url: pytest.fail("browser should stay closed"),
        )


@pytest.mark.parametrize(
    "variable",
    [
        "AI_GRADING_WORKTREE_DATA_DIR",
        "AI_GRADING_DATA_DIR",
        "AI_GRADING_OPS_STATE_DIR",
        "AI_GRADING_API_PROFILES_PATH",
    ],
)
def test_preview_guard_rejects_external_runtime_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    variable: str,
) -> None:
    project_root = tmp_path / ".worktrees" / "preview"
    project_root.mkdir(parents=True)
    monkeypatch.setattr(preview, "PROJECT_ROOT", project_root)
    for environment_name in (
        "AI_GRADING_WORKTREE_DATA_DIR",
        "AI_GRADING_DATA_DIR",
        "AI_GRADING_OPS_STATE_DIR",
        "AI_GRADING_API_PROFILES_PATH",
    ):
        monkeypatch.delenv(environment_name, raising=False)
    monkeypatch.setenv(variable, str(tmp_path / "user_data"))

    with pytest.raises(preview.PreviewGuardError, match=variable):
        preview._assert_local_data_boundary()


def test_preview_guard_accepts_its_forced_isolated_runtime_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    project_root = tmp_path / ".worktrees" / "preview"
    local_data = project_root / "user_data"
    project_root.mkdir(parents=True)
    monkeypatch.setattr(preview, "PROJECT_ROOT", project_root)
    monkeypatch.setenv("AI_GRADING_WORKTREE_DATA_DIR", str(local_data))
    monkeypatch.setenv("AI_GRADING_DATA_DIR", str(local_data))
    monkeypatch.setenv(
        "AI_GRADING_OPS_STATE_DIR",
        str(local_data / "runtime_state" / "ops"),
    )
    monkeypatch.setenv(
        "AI_GRADING_API_PROFILES_PATH",
        str(local_data / "config" / "api_profiles.json"),
    )

    preview._assert_local_data_boundary()


def test_git_executable_falls_back_to_the_standard_windows_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    git_executable = tmp_path / "Git" / "cmd" / "git.exe"
    git_executable.parent.mkdir(parents=True)
    git_executable.write_bytes(b"")
    monkeypatch.setattr(preview.shutil, "which", lambda _command: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert preview._git_executable() == str(git_executable)


def test_preview_launcher_reuses_a_running_service_before_starting_another() -> None:
    launcher = (preview.PROJECT_ROOT / "运行三合一预览.bat").read_text(
        encoding="utf-8",
    )

    probe = launcher.index("open-running")
    start = launcher.index('call "%~dp0运行.bat"')
    assert probe < start
    assert 'if "%RUNNING_STATUS%"=="0" goto done' in launcher
    assert 'if not "%RUNNING_STATUS%"=="3" goto running_probe_error' in launcher
    assert 'set "AI_GRADING_WORKTREE_DATA_DIR=%~dp0user_data"' in launcher
    assert 'set "AI_GRADING_PREVIEW_INSTANCE_ID=teacher-platform-integration"' in launcher
    assert 'set "AI_GRADING_PREVIEW_HEAD="' in launcher


def test_preview_launcher_does_not_misreport_all_failures_as_stale() -> None:
    launcher = (preview.PROJECT_ROOT / "运行三合一预览.bat").read_text(
        encoding="utf-8",
    )

    ensure = launcher.index("teacher_platform_preview.py\" ensure")
    read_head = launcher.index("teacher_platform_preview.py\" print-head")
    assert ensure < read_head
    assert "git -C" not in launcher
    assert "goto preview_prepare_error" in launcher
    assert "goto running_probe_error" in launcher
    assert "The three-workspace preview is not current." not in launcher


def test_preview_launcher_uses_windows_crlf_line_endings() -> None:
    launcher = (preview.PROJECT_ROOT / "运行三合一预览.bat").read_bytes()

    assert b"\r\n" in launcher
    assert b"\n" not in launcher.replace(b"\r\n", b"")
