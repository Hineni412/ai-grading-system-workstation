"""Build and validate the local three-workspace preview frontend.

This tool intentionally manages only ignored frontend dependencies and build
artifacts. It never starts the API or reads business data.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"
STAMP_PATH = DIST_DIR / "teacher-platform-preview-build.json"
EXPECTED_BRANCH = "codex/teacher-platform-integration"
SOURCE_BRANCHES = (
    "codex/grading-system-iteration",
    "codex/teaching-prep-iteration",
    "codex/class-teacher-iteration",
)
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


def _git(*args: str) -> str:
    completed = _run(
        [
            "git",
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


def _assert_preview_workspace() -> tuple[str, dict[str, str]]:
    branch = _current_branch()
    if branch != EXPECTED_BRANCH:
        raise PreviewGuardError(
            f"当前分支是 {branch or '未知'}，必须在 {EXPECTED_BRANCH} 中运行。"
        )

    tracked_changes = _git("status", "--porcelain", "--untracked-files=no")
    if tracked_changes:
        raise PreviewGuardError(
            "组合预览区存在尚未形成检查点的代码改动，请先处理后再构建。"
        )

    preview_head = _current_head()
    source_heads = _source_heads()
    for source_branch, source_head in source_heads.items():
        ancestor = subprocess.run(
            [
                "git",
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
        if ancestor.returncode != 0:
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="三合一组合预览页面守卫")
    parser.add_argument("action", choices=("build", "check", "ensure"))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.action == "build":
            stamp = build_preview()
        elif args.action == "check":
            stamp = check_preview()
        else:
            stamp = ensure_preview()
    except (PreviewGuardError, subprocess.CalledProcessError) as exc:
        print(f"组合预览未就绪：{exc}", file=sys.stderr)
        return 2

    print(
        "组合预览页面已对齐："
        f"{stamp['preview_head'][:8]}，三个业务检查点均已包含。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
