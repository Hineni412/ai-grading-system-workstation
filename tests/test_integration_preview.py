from __future__ import annotations

import json
import subprocess

import pytest

from tools import integration_preview as preview


def _git_repo(repo, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_code_changes_ignore_preview_data_but_not_source(
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
    assert preview._code_changes() == ""

    for artifact_root in (
        ".p35t",
        ".codex_artifacts",
        ".codex-review",
        ".cindy-worktrees",
        "论文修订输出",
    ):
        artifact = tmp_path / artifact_root / "local-output.txt"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("local artifact", encoding="utf-8")
    assert preview._code_changes() == ""

    source.write_text("source change", encoding="utf-8")
    assert "app.py" in preview._code_changes()

    source.write_text("baseline", encoding="utf-8")
    untracked_source = tmp_path / "new_source.py"
    untracked_source.write_text("new source", encoding="utf-8")
    assert "new_source.py" in preview._code_changes()


def test_preview_workspace_uses_the_current_branch_and_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preview, "_code_changes", lambda: "")
    monkeypatch.setattr(preview, "_current_branch", lambda: "codex/any-feature")
    monkeypatch.setattr(preview, "_current_head", lambda: "a" * 40)
    monkeypatch.setattr(preview, "_assert_local_data_boundary", lambda: None)

    assert preview._assert_preview_workspace() == (
        "codex/any-feature",
        "a" * 40,
    )


def test_preview_workspace_rejects_dirty_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preview, "_code_changes", lambda: " M app.py")

    with pytest.raises(preview.PreviewGuardError, match="尚未形成检查点"):
        preview._assert_preview_workspace()


def test_check_preview_rejects_a_build_from_an_older_preview_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preview,
        "_assert_preview_workspace",
        lambda: ("codex/current", "current-preview-head"),
    )
    monkeypatch.setattr(preview, "_assert_workspace_labels", lambda: None)
    monkeypatch.setattr(
        preview,
        "_read_stamp",
        lambda: {
            "preview_branch": "codex/current",
            "preview_head": "older-preview-head",
        },
    )

    with pytest.raises(preview.PreviewGuardError, match="页面仍是旧构建"):
        preview.check_preview()


def test_check_preview_rejects_a_build_from_another_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        preview,
        "_assert_preview_workspace",
        lambda: ("codex/current", "a" * 40),
    )
    monkeypatch.setattr(preview, "_assert_workspace_labels", lambda: None)
    monkeypatch.setattr(
        preview,
        "_read_stamp",
        lambda: {
            "preview_branch": "codex/another",
            "preview_head": "a" * 40,
        },
    )

    with pytest.raises(preview.PreviewGuardError, match="其他分支"):
        preview.check_preview()


def test_build_stamp_records_only_the_current_branch_and_head(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    stamp_path = tmp_path / "integration-preview-build.json"
    monkeypatch.setattr(preview, "FRONTEND_DIR", frontend)
    monkeypatch.setattr(preview, "STAMP_PATH", stamp_path)
    monkeypatch.setattr(
        preview,
        "_assert_preview_workspace",
        lambda: ("codex/any-feature", "b" * 40),
    )
    monkeypatch.setattr(preview, "_npm_command", lambda: "npm")
    monkeypatch.setattr(preview, "_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(preview, "_assert_workspace_labels", lambda: None)
    monkeypatch.setattr(
        preview,
        "check_preview",
        lambda: json.loads(stamp_path.read_text(encoding="utf-8")),
    )

    stamp = preview.build_preview()

    assert stamp["preview_branch"] == "codex/any-feature"
    assert stamp["preview_head"] == "b" * 40
    assert set(stamp) == {
        "schema_version",
        "preview_branch",
        "preview_head",
        "built_at_utc",
    }


def test_build_repairs_an_incomplete_node_modules_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    frontend = tmp_path / "frontend"
    (frontend / "node_modules").mkdir(parents=True)
    commands: list[list[str]] = []
    monkeypatch.setattr(preview, "FRONTEND_DIR", frontend)
    monkeypatch.setattr(preview, "STAMP_PATH", tmp_path / "stamp.json")
    monkeypatch.setattr(
        preview,
        "_assert_preview_workspace",
        lambda: ("codex/test", "c" * 40),
    )
    monkeypatch.setattr(preview, "_npm_command", lambda: "npm")
    monkeypatch.setattr(
        preview,
        "_run",
        lambda command, **_kwargs: commands.append(command),
    )
    monkeypatch.setattr(preview, "_assert_workspace_labels", lambda: None)
    monkeypatch.setattr(
        preview,
        "check_preview",
        lambda: {"preview_branch": "codex/test", "preview_head": "c" * 40},
    )

    preview.build_preview()

    assert commands == [
        ["npm", "ci", "--prefer-offline", "--no-audit", "--no-fund"],
        ["npm", "run", "build"],
    ]


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
            "preview_instance_id": "integration-preview",
            "preview_head": "84c2e183" + "0" * 32,
        },
    )

    assert preview.open_running_preview(
        port=8035,
        browser_open=lambda url: opened.append(url) or True,
    ) is True
    assert opened == ["http://127.0.0.1:8035/?preview=84c2e183"]


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
    with pytest.raises(preview.PreviewGuardError, match="不是集成预览服务"):
        preview.open_running_preview(
            port=8035,
            browser_open=lambda _url: pytest.fail("browser should stay closed"),
        )

    monkeypatch.setattr(
        preview,
        "_fetch_preview_health",
        lambda _port: {
            "status": "ok",
            "service": "ai-grading-api",
            "preview_instance_id": "another-preview",
            "preview_head": "84c2e183" + "0" * 32,
        },
    )
    with pytest.raises(preview.PreviewGuardError, match="不是集成预览服务"):
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
            "preview_instance_id": "integration-preview",
            "preview_head": "older-head",
        },
    )

    with pytest.raises(preview.PreviewGuardError, match="旧版集成预览"):
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
    monkeypatch.setattr(preview, "_primary_worktree_root", lambda: tmp_path)
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
    monkeypatch.setattr(preview, "_primary_worktree_root", lambda: tmp_path)
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


def test_preview_guard_accepts_the_primary_worktree_runtime_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    local_data = tmp_path / "user_data"
    monkeypatch.setattr(preview, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(preview, "_primary_worktree_root", lambda: tmp_path)
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


def test_primary_worktree_root_resolves_a_relative_common_git_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(preview, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(preview, "_git", lambda *_args: ".git")

    assert preview._primary_worktree_root() == tmp_path


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
