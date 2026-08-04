"""Build and validate the local three-workspace preview frontend.

This tool intentionally manages only ignored frontend dependencies and build
artifacts. It never starts the API or reads business data.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"
STAMP_PATH = DIST_DIR / "teacher-platform-preview-build.json"
SOURCE_CHECKPOINT_RECEIPTS_PATH = (
    PROJECT_ROOT / "integration" / "teacher-platform-source-checkpoints.json"
)
EXPECTED_BRANCH = "codex/teacher-platform-integration"
EXPECTED_PREVIEW_INSTANCE_ID = "teacher-platform-integration"
SOURCE_BRANCHES = (
    "codex/grading-system-iteration",
    "codex/teaching-prep-iteration",
    "codex/class-teacher-iteration",
)
SOURCE_PATH_SCOPES = {
    "codex/teaching-prep-iteration": (
        "backend/teaching_prep",
        "frontend/src/workspaces/teaching-prep",
        "tests/teaching_prep",
        "migrations/teaching_prep",
        "docs/product/TEACHING_PREP_WORKBENCH_IMPLEMENTATION_PLAN.md",
    ),
}
EXPECTED_LABELS = ("备课工作台", "班主任工作台")


class PreviewGuardError(RuntimeError):
    """Raised when the preview would be incomplete, stale, or unsafe."""


def _run(
    command: list[str],
    *,
    cwd: Path = PROJECT_ROOT,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=capture,
    )


def _git_executable() -> str:
    located = shutil.which("git")
    if located:
        return located

    candidates: list[Path] = []
    for environment_name in ("ProgramFiles", "ProgramFiles(x86)"):
        install_root = os.environ.get(environment_name)
        if install_root:
            candidates.append(Path(install_root) / "Git" / "cmd" / "git.exe")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            Path(local_app_data) / "Programs" / "Git" / "cmd" / "git.exe"
        )

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise PreviewGuardError(
        "未找到 Git。请重新打开资源管理器后再试，或重新安装 Git for Windows。"
    )


def _git(*args: str) -> str:
    completed = _run(
        [
            _git_executable(),
            "-c",
            f"safe.directory={PROJECT_ROOT.as_posix()}",
            *args,
        ],
        capture=True,
    )
    return completed.stdout.strip()


def _current_branch() -> str:
    return _git("branch", "--show-current")


def _current_head() -> str:
    return _git("rev-parse", "HEAD")


def _source_heads() -> dict[str, str]:
    return {branch: _git("rev-parse", branch) for branch in SOURCE_BRANCHES}


def _source_checkpoint_receipts() -> dict[str, dict[str, str]]:
    if not SOURCE_CHECKPOINT_RECEIPTS_PATH.is_file():
        return {}
    try:
        payload = json.loads(
            SOURCE_CHECKPOINT_RECEIPTS_PATH.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise PreviewGuardError("组合预览来源检查点记录无法读取。") from exc
    if not isinstance(payload, dict):
        raise PreviewGuardError("组合预览来源检查点记录格式无效。")
    receipts = payload.get("source_checkpoints")
    if not isinstance(receipts, dict):
        raise PreviewGuardError("组合预览来源检查点记录格式无效。")
    result: dict[str, dict[str, str]] = {}
    for branch, receipt in receipts.items():
        if (
            branch not in SOURCE_PATH_SCOPES
            or not isinstance(receipt, dict)
            or set(receipt) != {"source_head", "integration_commit"}
        ):
            raise PreviewGuardError("组合预览来源检查点记录包含无效条目。")
        clean_receipt: dict[str, str] = {}
        for field in ("source_head", "integration_commit"):
            value = receipt.get(field)
            if not isinstance(value, str):
                raise PreviewGuardError(
                    "组合预览来源检查点记录包含无效提交号。"
                )
            clean_value = value.strip().lower()
            if len(clean_value) not in (40, 64) or any(
                character not in "0123456789abcdef"
                for character in clean_value
            ):
                raise PreviewGuardError(
                    "组合预览来源检查点记录包含无效提交号。"
                )
            clean_receipt[field] = clean_value
        result[branch] = clean_receipt
    return result


def _source_is_ancestor(source_head: str, preview_head: str) -> bool:
    completed = subprocess.run(
        [
            _git_executable(),
            "-c",
            f"safe.directory={PROJECT_ROOT.as_posix()}",
            "merge-base",
            "--is-ancestor",
            source_head,
            preview_head,
        ],
        cwd=PROJECT_ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def _source_matches_integration(
    source_head: str,
    integration_commit: str,
    paths: tuple[str, ...],
) -> bool:
    completed = subprocess.run(
        [
            _git_executable(),
            "-c",
            f"safe.directory={PROJECT_ROOT.as_posix()}",
            "diff",
            "--quiet",
            source_head,
            integration_commit,
            "--",
            *paths,
        ],
        cwd=PROJECT_ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def _receipt_proves_integration(
    *,
    source_branch: str,
    source_head: str,
    preview_head: str,
    receipts: dict[str, dict[str, str]],
) -> bool:
    receipt = receipts.get(source_branch)
    paths = SOURCE_PATH_SCOPES.get(source_branch)
    if receipt is None or paths is None:
        return False
    integration_commit = receipt["integration_commit"]
    return (
        receipt["source_head"] == source_head
        and _source_is_ancestor(integration_commit, preview_head)
        and _source_matches_integration(source_head, integration_commit, paths)
    )


def _tracked_code_changes() -> str:
    return _git(
        "status",
        "--porcelain",
        "--untracked-files=no",
        "--",
        ".",
        ":(exclude)user_data/**",
    )


def _assert_preview_workspace() -> tuple[str, dict[str, str]]:
    branch = _current_branch()
    if branch != EXPECTED_BRANCH:
        raise PreviewGuardError(
            f"当前分支是 {branch or '未知'}，必须在 {EXPECTED_BRANCH} 中运行。"
        )

    tracked_changes = _tracked_code_changes()
    if tracked_changes:
        raise PreviewGuardError(
            "组合预览区存在尚未形成检查点的代码改动，请先处理后再构建。"
        )

    preview_head = _current_head()
    source_heads = _source_heads()
    checkpoint_receipts = _source_checkpoint_receipts()
    for source_branch, source_head in source_heads.items():
        if (
            not _source_is_ancestor(source_head, preview_head)
            and not _receipt_proves_integration(
                source_branch=source_branch,
                source_head=source_head,
                preview_head=preview_head,
                receipts=checkpoint_receipts,
            )
        ):
            raise PreviewGuardError(
                f"{source_branch} 已有新检查点但尚未进入组合预览；请先刷新合并。"
            )

    _assert_local_data_boundary()
    return preview_head, source_heads


def _assert_local_data_boundary() -> None:
    local_data = PROJECT_ROOT / "user_data"
    repository_root = PROJECT_ROOT.parents[1]
    real_data = repository_root / "user_data"
    if local_data.exists() and local_data.resolve() == real_data.resolve():
        raise PreviewGuardError("组合预览不能连接根目录的真实 user_data。")

    expected_paths = {
        "AI_GRADING_WORKTREE_DATA_DIR": local_data,
        "AI_GRADING_DATA_DIR": local_data,
        "AI_GRADING_OPS_STATE_DIR": local_data / "runtime_state" / "ops",
        "AI_GRADING_API_PROFILES_PATH": local_data / "config" / "api_profiles.json",
    }
    for variable, expected in expected_paths.items():
        raw_value = os.environ.get(variable)
        if not raw_value:
            continue
        candidate = Path(raw_value).expanduser()
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        if candidate.resolve() != expected.resolve():
            raise PreviewGuardError(
                f"{variable} 指向组合预览之外，已拒绝继续。"
            )


def _assert_workspace_labels() -> None:
    assets = DIST_DIR / "assets"
    if not (DIST_DIR / "index.html").is_file() or not assets.is_dir():
        raise PreviewGuardError("组合预览页面尚未构建或构建不完整。")

    javascript = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in assets.glob("*.js")
        if path.is_file()
    )
    missing = [label for label in EXPECTED_LABELS if label not in javascript]
    if missing:
        raise PreviewGuardError(
            "页面构建缺少工作台入口：" + "、".join(missing)
        )


def _read_stamp() -> dict[str, Any]:
    try:
        payload = json.loads(STAMP_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise PreviewGuardError("页面缺少有效的组合预览版本标记。") from exc
    if not isinstance(payload, dict):
        raise PreviewGuardError("组合预览版本标记格式无效。")
    return payload


def check_preview() -> dict[str, Any]:
    preview_head, source_heads = _assert_preview_workspace()
    _assert_workspace_labels()
    stamp = _read_stamp()
    if stamp.get("preview_head") != preview_head:
        raise PreviewGuardError("代码已经更新，但页面仍是旧构建；必须重新构建。")
    if stamp.get("source_checkpoints") != source_heads:
        raise PreviewGuardError("三条业务线检查点已变化，当前页面不是最新组合。")
    return stamp


def _npm_command() -> str:
    command = shutil.which("npm.cmd") or shutil.which("npm")
    if command is None:
        raise PreviewGuardError("未找到 Node.js/npm，无法构建组合预览页面。")
    return command


def build_preview() -> dict[str, Any]:
    preview_head, source_heads = _assert_preview_workspace()
    npm = _npm_command()
    if not (FRONTEND_DIR / "node_modules").is_dir():
        _run(
            [npm, "ci", "--prefer-offline", "--no-audit", "--no-fund"],
            cwd=FRONTEND_DIR,
        )
    _run([npm, "run", "build"], cwd=FRONTEND_DIR)
    _assert_workspace_labels()

    stamp: dict[str, Any] = {
        "schema_version": 1,
        "preview_branch": EXPECTED_BRANCH,
        "preview_head": preview_head,
        "source_checkpoints": source_heads,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    STAMP_PATH.write_text(
        json.dumps(stamp, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return check_preview()


def ensure_preview() -> dict[str, Any]:
    try:
        return check_preview()
    except PreviewGuardError as exc:
        print(f"组合预览需要刷新：{exc}")
        return build_preview()


def _api_port() -> int:
    raw_port = os.environ.get("API_PORT", "8035")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise PreviewGuardError(f"API_PORT 不是有效端口：{raw_port}") from exc
    if not 1 <= port <= 65535:
        raise PreviewGuardError(f"API_PORT 超出有效范围：{raw_port}")
    return port


def _fetch_preview_health(port: int) -> dict[str, Any] | None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/healthz",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=0.75) as response:
            payload = json.loads(response.read(4096).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise PreviewGuardError(
            f"端口 {port} 已被其他服务占用，健康检查返回 {exc.code}。"
        ) from exc
    except urllib.error.URLError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PreviewGuardError(
            f"端口 {port} 返回的健康状态无法识别。"
        ) from exc
    if not isinstance(payload, dict):
        raise PreviewGuardError(f"端口 {port} 返回的健康状态格式无效。")
    return payload


def open_running_preview(
    *,
    port: int | None = None,
    browser_open: Callable[[str], bool] = webbrowser.open,
) -> bool:
    stamp = check_preview()
    selected_port = _api_port() if port is None else port
    health = _fetch_preview_health(selected_port)
    if health is None:
        return False
    if health.get("status") != "ok" or health.get("service") != "ai-grading-api":
        raise PreviewGuardError(
            f"端口 {selected_port} 上的程序不是三合一预览服务。"
        )
    if health.get("preview_instance_id") != EXPECTED_PREVIEW_INSTANCE_ID:
        raise PreviewGuardError(
            f"端口 {selected_port} 上的程序不是三合一预览服务。"
        )
    expected_head = str(stamp["preview_head"])
    if health.get("preview_head") != expected_head:
        raise PreviewGuardError(
            f"端口 {selected_port} 上运行的是旧版三合一预览；"
            "请先关闭旧服务窗口，再重新打开预览。"
        )

    version = expected_head[:8]
    url = (
        f"http://127.0.0.1:{selected_port}/teaching-prep"
        f"?preview={version}"
    )
    if not browser_open(url):
        raise PreviewGuardError(
            "三合一预览正在运行，但未能打开浏览器；请手动访问 " + url
        )
    print(f"已复用正在运行的三合一预览：{url}")
    return True


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="三合一组合预览页面守卫")
    parser.add_argument(
        "action",
        choices=("build", "check", "ensure", "open-running", "print-head"),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.action == "print-head":
            print(check_preview()["preview_head"])
            return 0
        if args.action == "open-running":
            if open_running_preview():
                return 0
            print("当前没有正在运行的三合一预览服务。")
            return 3
        if args.action == "build":
            stamp = build_preview()
        elif args.action == "check":
            stamp = check_preview()
        else:
            stamp = ensure_preview()
    except (PreviewGuardError, subprocess.CalledProcessError, OSError) as exc:
        print(f"组合预览未就绪：{exc}", file=sys.stderr)
        return 2

    print(
        "组合预览页面已对齐："
        f"{stamp['preview_head'][:8]}，三个业务检查点均已包含。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
