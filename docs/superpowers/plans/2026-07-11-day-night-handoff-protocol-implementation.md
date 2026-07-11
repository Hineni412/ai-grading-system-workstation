# Day-Night Handoff Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为每个活动执行包增加可机械验证的昼夜交接状态，并让夜间自动化安全区分恢复原任务、冻结待复审/待集成分支和领取其他独立包。

**Architecture:** 当前即时计划继续保存人可读交接块，`tools/handoff_status.py` 负责严格提取枚举、校验字段组合并核对 Git 证据。夜间提示词只消费验证器的 JSON 结果，不凭自然语言猜测完成状态；用户测试入口和模板与协议同批建立，但不实现 UAT 运行时。

**Tech Stack:** Python 3.12 标准库、Markdown、Git CLI、PowerShell、Codex Desktop automation。

**工作包：** `DAY-NIGHT-HANDOFF`
**规划状态：** `ready_for_execution`
**规划模型：** `S-XH`
**执行模型：** `T-H`
**复审模型：** `S-XH`
**允许夜间执行：** `no`
**计划基线：** `bbc1b7f7edbd70ba696421eab89ab30d37ee5101`
**包类型：** 一次性治理支撑包；不改变 87 个正式包的数量或状态。

## Global Constraints

- 不修改评分、标签、状态或数据库 Schema。
- 不修改、删除、暂存、提交或 stash 根目录真实 `user_data/`。
- `NIGHTLY_ELIGIBILITY_MATRIX.md` 继续只保存包级先天资格，不保存瞬时交接状态。
- `EXECUTION_INDEX.md` 继续只保存正式包状态和主线进度。
- 缺少、重复、非法或与 Git 不一致的交接证据必须安全停机。
- 夜间仍不得创建 worktree、integration、push 或 PR。
- 当前工作包属于治理基础设施，只能白天实施。

## PowerShell Session Preamble

Linked worktrees do not contain the ignored portable runtime. Before Task 1, and again after opening any new PowerShell session, run:

```powershell
$commonGitDir = (git rev-parse --path-format=absolute --git-common-dir).Trim()
$primaryRepositoryRoot = Split-Path -Parent $commonGitDir
$Python = Join-Path $primaryRepositoryRoot 'runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw "portable Python runtime not found" }
```

All implementation and verification commands below use `$Python`. The automation prompt is the one exception: it continues to use repository-root-relative `runtime\python\python.exe` because automation-2 runs from the primary project root.

---

### Task 1: Strict Handoff Block Parser

**Files:**
- Create: `tools/handoff_status.py`
- Create: `tests/test_handoff_status.py`

**Interfaces:**
- Produces: `parse_handoff_status(text: str) -> HandoffRecord`
- Produces: `HandoffStatusError`, `HandoffRecord`, and enum values consumed by Task 2.

- [ ] **Step 1: Write failing parser tests**

Add tests for one valid block, duplicate markers, a missing field, illegal enum, invalid package ID, invalid full SHA, and an inconsistent field combination:

```python
from __future__ import annotations

import pytest

from tools.handoff_status import HandoffStatusError, parse_handoff_status


VALID = """\
<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P1-16
**交接状态：** verified_pending_integration
**功能提交：** 0123456789abcdef0123456789abcdef01234567
**自动验证：** passed
**独立复审：** passed
**用户验收：** not_required
**真实数据指纹：** unchanged
**夜间动作：** independent_candidate_allowed
<!-- HANDOFF_STATUS_END -->
"""


def test_parse_valid_verified_handoff() -> None:
    record = parse_handoff_status(VALID)

    assert record.package_id == "P1-16"
    assert record.handoff_status == "verified_pending_integration"
    assert record.implementation_commit.startswith("01234567")


@pytest.mark.parametrize(
    "text, message",
    [
        (VALID + VALID, "exactly one handoff block"),
        (VALID.replace("**自动验证：** passed\n", ""), "missing field: automated_validation"),
        (
            VALID.replace(
                "**自动验证：** passed\n",
                "**自动验证：** passed\n**自动验证：** passed\n",
            ),
            "duplicate field: automated_validation",
        ),
        (
            VALID.replace("## 昼夜交接", "## 昼夜交接\n\n**额外字段：** yes"),
            "unknown field: 额外字段",
        ),
        (VALID.replace("P1-16", "package-one"), "invalid package_id"),
        (
            VALID.replace(
                "0123456789abcdef0123456789abcdef01234567",
                "not-a-full-sha",
            ),
            "invalid implementation_commit",
        ),
        (VALID.replace("unchanged", "unknown"), "invalid real_data_fingerprint"),
        (VALID.replace("independent_candidate_allowed", "resume_only"), "invalid field combination"),
    ],
)
def test_invalid_handoff_is_rejected(text: str, message: str) -> None:
    with pytest.raises(HandoffStatusError, match=message):
        parse_handoff_status(text)
```

- [ ] **Step 2: Run parser tests and confirm RED**

Run:

```powershell
& $Python -m pytest tests\test_handoff_status.py -q
```

Expected: collection fails because `tools.handoff_status` does not exist.

- [ ] **Step 3: Implement enums, record, extraction, and combination validation**

Create `tools/handoff_status.py` with these public definitions and strict markers:

```python
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Final


START: Final = "<!-- HANDOFF_STATUS_START -->"
END: Final = "<!-- HANDOFF_STATUS_END -->"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
PACKAGE_ID = re.compile(r"^P[1-9][0-9]*-[0-9]{2}$")

FIELDS: Final = {
    "执行包": "package_id",
    "交接状态": "handoff_status",
    "功能提交": "implementation_commit",
    "自动验证": "automated_validation",
    "独立复审": "independent_review",
    "用户验收": "user_acceptance",
    "真实数据指纹": "real_data_fingerprint",
    "夜间动作": "nightly_action",
}

ALLOWED: Final = {
    "handoff_status": {
        "in_progress",
        "resumable",
        "waiting_review",
        "waiting_user",
        "verified_pending_integration",
    },
    "automated_validation": {"pending", "passed", "failed"},
    "independent_review": {"pending", "passed", "failed"},
    "user_acceptance": {"not_required", "pending", "passed", "failed"},
    "real_data_fingerprint": {"not_touched", "unchanged", "changed"},
    "nightly_action": {"report_only", "resume_only", "independent_candidate_allowed"},
}


class HandoffStatusError(ValueError):
    pass


@dataclass(frozen=True)
class HandoffRecord:
    package_id: str
    handoff_status: str
    implementation_commit: str
    automated_validation: str
    independent_review: str
    user_acceptance: str
    real_data_fingerprint: str
    nightly_action: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _extract_block(text: str) -> str:
    if text.count(START) != 1 or text.count(END) != 1:
        raise HandoffStatusError("expected exactly one handoff block")
    before, rest = text.split(START, 1)
    block, after = rest.split(END, 1)
    if START in before or END in before or START in after or END in after:
        raise HandoffStatusError("expected exactly one handoff block")
    return block


def _read_fields(block: str) -> dict[str, str]:
    labels = re.findall(r"^\*\*([^*：]+)：\*\*", block, flags=re.MULTILINE)
    unknown = sorted(set(labels) - set(FIELDS))
    if unknown:
        raise HandoffStatusError(f"unknown field: {unknown[0]}")
    values: dict[str, str] = {}
    for label, field_name in FIELDS.items():
        matches = re.findall(
            rf"^\*\*{re.escape(label)}：\*\*\s*`?([^`\s]+)`?\s*$",
            block,
            flags=re.MULTILINE,
        )
        if not matches:
            raise HandoffStatusError(f"missing field: {field_name}")
        if len(matches) > 1:
            raise HandoffStatusError(f"duplicate field: {field_name}")
        values[field_name] = matches[0]
    return values


def _validate_record(record: HandoffRecord) -> None:
    if not PACKAGE_ID.fullmatch(record.package_id):
        raise HandoffStatusError("invalid package_id")
    for field_name, choices in ALLOWED.items():
        if getattr(record, field_name) not in choices:
            raise HandoffStatusError(f"invalid {field_name}")
    if record.implementation_commit not in {"none", "branch_head"} and not FULL_SHA.fullmatch(
        record.implementation_commit
    ):
        raise HandoffStatusError("invalid implementation_commit")

    status = record.handoff_status
    valid = (
        status == "in_progress"
        and record.implementation_commit == "none"
        and record.automated_validation == "pending"
        and record.independent_review == "pending"
        and record.user_acceptance in {"pending", "not_required"}
        and record.nightly_action == "report_only"
    ) or (
        status == "resumable"
        and record.implementation_commit == "none"
        and record.automated_validation in {"pending", "failed"}
        and record.independent_review == "pending"
        and record.user_acceptance in {"pending", "not_required"}
        and record.nightly_action == "resume_only"
    ) or (
        status == "waiting_review"
        and record.implementation_commit == "branch_head"
        and record.automated_validation == "passed"
        and record.independent_review == "pending"
        and record.user_acceptance in {"pending", "not_required"}
        and record.nightly_action == "report_only"
    ) or (
        status == "waiting_user"
        and record.implementation_commit in {"none", "branch_head"}
        and record.automated_validation in {"pending", "passed"}
        and record.independent_review in {"pending", "passed"}
        and record.user_acceptance == "pending"
        and record.nightly_action == "report_only"
    ) or (
        status == "verified_pending_integration"
        and bool(FULL_SHA.fullmatch(record.implementation_commit))
        and record.automated_validation == "passed"
        and record.independent_review == "passed"
        and record.user_acceptance in {"passed", "not_required"}
        and record.real_data_fingerprint in {"not_touched", "unchanged"}
        and record.nightly_action == "independent_candidate_allowed"
    )
    if not valid:
        raise HandoffStatusError("invalid field combination")


def parse_handoff_status(text: str) -> HandoffRecord:
    values = _read_fields(_extract_block(text))
    record = HandoffRecord(**values)
    _validate_record(record)
    return record
```

- [ ] **Step 4: Run parser tests and confirm GREEN**

Run the Task 1 test command.

Expected: all tests in `tests/test_handoff_status.py` pass.

- [ ] **Step 5: Commit the parser slice**

```powershell
git add tools\handoff_status.py tests\test_handoff_status.py
git commit -m "test: validate handoff status blocks"
```

### Task 2: Git Evidence Validation and JSON CLI

**Files:**
- Modify: `tools/handoff_status.py`
- Modify: `tests/test_handoff_status.py`

**Interfaces:**
- Consumes: `HandoffRecord` from Task 1.
- Produces: `validate_handoff(plan_path: Path, repo_root: Path) -> HandoffValidation`.
- Produces CLI JSON with `ok`, `record`, `resolved_implementation_commit`, and `issues`.

- [ ] **Step 1: Write failing temporary-repository tests**

Use a helper that initializes a temporary Git repository, creates a functional commit containing `waiting_review`, then creates a handoff-only commit containing `verified_pending_integration`. Assert the valid branch passes and these cases fail: wrong parent SHA, dirty verified worktree, dirty `branch_head` worktree, latest commit changes a second file, `real_data_fingerprint=changed`, tracked `user_data/`, and an ignored local file below `user_data/`.

```python
def test_verified_handoff_requires_plan_only_commit(tmp_path: Path) -> None:
    repo, plan, implementation_sha = make_verified_repo(tmp_path)

    report = validate_handoff(plan, repo)

    assert report.ok is True
    assert report.resolved_implementation_commit == implementation_sha


def test_verified_handoff_rejects_extra_file_in_handoff_commit(tmp_path: Path) -> None:
    repo, plan, _implementation_sha = make_verified_repo(tmp_path, extra_handoff_file=True)

    report = validate_handoff(plan, repo)

    assert report.ok is False
    assert "handoff commit must only change the plan" in report.issues
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```powershell
& $Python -m pytest tests\test_handoff_status.py -q
```

Expected: failures because `validate_handoff` is undefined.

- [ ] **Step 3: Implement Git validation and CLI**

Append these public structures and functions. Use `subprocess.run(..., check=True, text=True, encoding="utf-8")`; never invoke a shell.

```python
import argparse
import json
import subprocess
from pathlib import Path


@dataclass(frozen=True)
class HandoffValidation:
    ok: bool
    record: HandoffRecord | None
    resolved_implementation_commit: str | None
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "record": self.record.to_dict() if self.record else None,
            "resolved_implementation_commit": self.resolved_implementation_commit,
            "issues": list(self.issues),
        }


def _git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.rstrip("\r\n")


def _porcelain_paths(output: str) -> tuple[str, ...]:
    paths: list[str] = []
    expects_rename_source = False
    for token in (item for item in output.split("\0") if item):
        if expects_rename_source:
            paths.append(token.replace("\\", "/"))
            expects_rename_source = False
            continue
        if len(token) < 4 or token[2] != " ":
            paths.append(token.replace("\\", "/"))
            continue
        status = token[:2]
        paths.append(token[3:].replace("\\", "/"))
        expects_rename_source = any(code in status for code in ("R", "C"))
    return tuple(paths)


def validate_handoff(plan_path: Path, repo_root: Path) -> HandoffValidation:
    repo = repo_root.resolve()
    plan = plan_path.resolve()
    issues: list[str] = []
    try:
        plan_relative = plan.relative_to(repo).as_posix()
        record = parse_handoff_status(plan.read_text(encoding="utf-8"))
    except (OSError, ValueError, HandoffStatusError) as exc:
        return HandoffValidation(False, None, None, (str(exc),))

    resolved: str | None = None
    try:
        head = _git(repo, "rev-parse", "HEAD")
        dirty = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        dirty_paths = _porcelain_paths(dirty)
        user_data_status = _git(
            repo,
            "status",
            "--porcelain=v1",
            "-z",
            "--ignored=matching",
            "--untracked-files=all",
            "--",
            "user_data",
        )
        user_data_paths = _porcelain_paths(user_data_status)
        resolved = head if record.implementation_commit == "branch_head" else None
        if record.implementation_commit == "branch_head" and dirty:
            issues.append("committed handoff worktree must be clean")
        if record.handoff_status == "verified_pending_integration":
            parent = _git(repo, "rev-parse", "HEAD^")
            changed = tuple(
                line
                for line in _git(
                    repo,
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "HEAD",
                ).splitlines()
                if line
            )
            resolved = record.implementation_commit
            if record.implementation_commit != parent:
                issues.append("implementation commit must be the direct parent of HEAD")
            if changed != (plan_relative,):
                issues.append("handoff commit must only change the plan")
            if dirty:
                issues.append("verified handoff worktree must be clean")
    except (OSError, subprocess.CalledProcessError) as exc:
        issues.append(f"git validation failed: {exc}")
        dirty_paths = ()
        user_data_paths = ()

    if record.real_data_fingerprint == "changed":
        issues.append("real data fingerprint changed")
    if user_data_paths or any(
        path.casefold() == "user_data" or path.casefold().startswith("user_data/")
        for path in dirty_paths
    ):
        issues.append("user_data changes are not allowed")

    return HandoffValidation(not issues, record, resolved, tuple(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a package handoff block and Git evidence.")
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    args = parser.parse_args()
    report = validate_handoff(args.plan, args.repo)
    print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run parser, Git, and CLI tests**

Run:

```powershell
& $Python -m pytest tests\test_handoff_status.py -q
$output = & $Python tools\handoff_status.py --plan docs\superpowers\plans\2026-07-10-p1-15-question-bank-read-routes-implementation.md --repo .
if ($LASTEXITCODE -ne 2) { throw "historical plan must fail closed with exit code 2" }
$report = $output | ConvertFrom-Json
if ($report.ok -ne $false -or (($report.issues -join " ") -notmatch "handoff block")) {
    throw "historical plan did not report the expected missing-block issue"
}
```

Expected: tests pass; the historical plan exits 2 with JSON explaining that the handoff block is missing. This safe failure is expected evidence, not a test failure.

- [ ] **Step 5: Commit Git evidence validation**

```powershell
git add tools\handoff_status.py tests\test_handoff_status.py
git commit -m "feat: verify package handoff evidence"
```

### Task 3: User Testing Guide and Template

**Files:**
- Create: `docs/user-testing/README.md`
- Create: `docs/user-testing/USER_TEST_TEMPLATE.md`
- Create: `docs/user-testing/checkpoints/.gitkeep`
- Modify: `docs/superpowers/packages/README.md`
- Modify: `docs/superpowers/packages/EXECUTION_INDEX.md`
- Modify: `docs/superpowers/packages/phase-1-execution-packages.md`
- Modify: `docs/superpowers/packages/phase-2-execution-packages.md`

**Interfaces:**
- Produces: stable user-facing entry and a just-in-time checklist template.
- Does not produce concrete P1-29/P2 click steps before those screens are runnable.

- [ ] **Step 1: Create the stable guide**

`docs/user-testing/README.md` must contain these exact sections and decisions:

```markdown
# 用户自测与正式验收

## 你需要做什么
按清单编号操作；异常时回复“步骤编号 + 看到的现象 + 截图”。你不需要运行命令、编辑 Markdown 或判断技术日志。

## Codex 负责什么
Codex 准备隔离数据、启动服务、提供地址、执行自动检查、记录结果并分析失败。

## 测试节奏
- 可见前端批次：5-10 分钟短测。
- P1-29：当前流程黑盒基线。
- P2-08：复核样板页正式验收。
- P2-20：授权副本完整五流程。
- P2-21：启动与回退验收。

## 数据安全
日常和 P2-08 只使用固定演示数据；P2-20 只使用用户明确授权的副本。页面没有“用户验收模式”标识时不要继续。真实数据库、未脱敏截图、导出和密钥不得提交 Git。

## 结果
Blocker/Major 使正式门槛失败；Minor 由用户决定是否阻塞。只有用户明确确认后，正式验收才记为 passed。
```

- [ ] **Step 2: Create the reusable checklist template**

`USER_TEST_TEMPLATE.md` must include metadata, safety, start URL, preflight, numbered steps, expected result, failure action, result record, and reset. Use placeholders only in angle-bracket form because this file is explicitly a template:

```markdown
# <执行包> 用户自测

**提交：** <40 位 SHA>
**类型：** quick | formal
**日期：** <YYYY-MM-DD>
**页面范围：** <页面或流程>
**预计时间：** <分钟>
**状态：** pending

## 安全检查
- [ ] 页面持续显示“用户验收模式”。
- [ ] 数据来源为固定演示数据或本次明确授权的副本。
- [ ] Codex 已记录真实两库测试前指纹。

## 启动
Codex 已执行：<本次启动动作>

访问：`<Codex 提供的本地地址>`

## 测试前检查
- 浏览器与窗口：<浏览器及窗口尺寸>
- 演示会话或账号：<名称或“不需要”>
- 已知限制：<限制或“无”>

## 步骤
| 步骤 | 你的操作 | 应看到的结果 | 失败后动作 |
|---:|---|---|---|
| 1 | <一个动作> | <一个可观察结果> | 停止并回复“步骤 1 + 现象 + 截图” |

## 恢复与重置
仅在 Codex 明确要求时执行：<本清单的重置入口或“不需要”>。关闭本次验收服务后再重新开始。

## 结果记录（由 Codex 更新）
- 结果：pending | passed | failed
- 失败步骤：none
- 用户反馈：none
- 脱敏证据：none
- 真实两库测试后指纹：pending
```

- [ ] **Step 3: Link formal package deliverables**

Update P1-29, P2-08, P2-20, and P2-21 so their `主要模块/范围` and `验收` explicitly require a versioned checklist under `docs/user-testing/checkpoints/`. Add `用户自测: none | quick | formal` and `自测清单` to the package README's immediate-plan requirements. Do not invent future UI steps.

- [ ] **Step 4: Verify documentation structure**

Run:

```powershell
rg -n "P1-29|P2-08|P2-20|P2-21|用户验收模式|步骤编号" docs\user-testing docs\superpowers\packages
git diff --check
```

Expected: all four gates and the feedback protocol are linked; no whitespace errors.

- [ ] **Step 5: Commit user-testing documentation**

```powershell
git add docs\user-testing docs\superpowers\packages\README.md docs\superpowers\packages\EXECUTION_INDEX.md docs\superpowers\packages\phase-1-execution-packages.md docs\superpowers\packages\phase-2-execution-packages.md
git commit -m "docs: define user acceptance workflow"
```

### Task 4: Integrate Handoff Protocol into Project Governance

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/superpowers/packages/README.md`
- Modify: `docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION_2026-07-11.md`
- Modify: `docs/superpowers/packages/NIGHTLY_AUTOMATION.md`
- External managed config: Codex automation-2
- Read-only verification source: `C:\Users\89418\.codex\automations\automation-2\automation.toml`

**Interfaces:**
- Consumes: the single JSON line emitted by `tools/handoff_status.py --plan $resolvedPlanPath --repo $resolvedWorktreePath`.
- Produces: one authoritative automation prompt synchronized to automation-2.

- [ ] **Step 1: Add the canonical handoff block and lifecycle rules**

Add the marker-delimited block from the approved design to the package README. A clean `ready` plan proven never to have been claimed may omit the block and still use the existing five nightly release fields; the worker writes `in_progress` before its first source edit. Once claimed, a missing block defaults that package/channel to `report_only`, and plans are not bulk-edited speculatively. Add the final-tree-anchor/plan-only-handoff-commit rule and full enum matrix: when user results must be versioned, a checklist-only evidence commit may be the final tree anchor, but no source may change after the reviewed functional version and the following handoff commit still changes only the instant plan.

- [ ] **Step 2: Update parallel channel ownership**

Add these channel rules to the parallel manual:

```markdown
- `waiting_review`、`waiting_user` 和 `verified_pending_integration` 仍占用原 worktree，禁止复用或重绑。
- `verified_pending_integration` 不阻止另一个无依赖、无文件或契约冲突的预分配通道。
- 未进入最新 `origin/main` 的功能提交不能满足其他执行包依赖。
- 交接提交只能修改当前即时计划；integration 同时审查功能提交和交接提交。
```

- [ ] **Step 3: Make the automation call the validator**

In the authoritative prompt, apply the validator to every package that has already been claimed by an active, stopped, waiting, completed-unmerged, dirty or otherwise occupied task/worktree. Resolve exactly one current instant plan and exactly one matching worktree for that package, store the two absolute paths as `$resolvedPlanPath` and `$resolvedWorktreePath`, then execute:

```powershell
$resolvedPlanPath = (Resolve-Path -LiteralPath $resolvedPlanPath).Path
$resolvedWorktreePath = (Resolve-Path -LiteralPath $resolvedWorktreePath).Path
runtime\python\python.exe tools\handoff_status.py --plan $resolvedPlanPath --repo $resolvedWorktreePath
```

Parse its single JSON line. Exit code 2, `ok=false`, a missing tool, non-JSON output, multiple matching plans, or any issue makes that claimed package/channel `report_only`; an ambiguous project-wide mapping stops the whole run. Apply these branches before the existing new-candidate logic:

```text
in_progress -> channel occupied; do not message or modify
resumable -> only the uniquely matched stopped task may receive one continuation message after all existing safety gates pass
waiting_review -> freeze branch/worktree; do not message or modify
waiting_user -> freeze branch/worktree; do not infer user approval
verified_pending_integration -> freeze branch/worktree; it does not satisfy dependencies until the functional commit is in latest origin/main
```

Only after no claimed package is resumable may the automation enter the existing new-candidate selection. A candidate plan may omit the handoff block only when task history, branch assignment and a clean worktree prove it has never been claimed; all existing matrix, `ready`, five release-field, dependency, drift, real-data, conflict and one-action gates still apply. Immediately after claiming and before the first source edit, add an `in_progress` block. If the worker stops intentionally, it must leave `resumable`; if it finishes implementation, it commits `waiting_review` with `功能提交: branch_head`. A crash that leaves `in_progress` safely requires daytime inspection.

- [ ] **Step 4: Update the external automation through the app tool**

Extract text between `AUTOMATION_PROMPT_START/END`; update automation-2 without changing name, RRULE, model, reasoning, project, execution environment or ACTIVE status. Do not edit automation TOML directly.

- [ ] **Step 5: Compare prompt and fixed configuration exactly**

Use Python `tomllib` to read `C:\Users\89418\.codex\automations\automation-2\automation.toml`, assert the project prompt equals automation-2 prompt byte-for-byte, and verify:

```text
name = 阅卷系统夜间4点推进
rrule = RRULE:FREQ=WEEKLY;BYHOUR=4;BYMINUTE=0;BYDAY=SU,MO,TU,WE,TH,FR,SA
model = gpt-5.6-terra
reasoning_effort = xhigh
status = ACTIVE
execution_environment = local
```

- [ ] **Step 6: Commit governance integration**

```powershell
git add AGENTS.md docs\superpowers\packages\README.md docs\superpowers\packages\PARALLEL_WORKTREE_EXECUTION_2026-07-11.md docs\superpowers\packages\NIGHTLY_AUTOMATION.md
git commit -m "docs: enforce day-night handoff protocol"
```

### Task 5: Combined Verification and Bootstrap Integration Handoff

**Files:**
- Verify only; update the current plan's checkbox/evidence section before the final handoff commit.

- [ ] **Step 1: Run focused and governance tests**

```powershell
& $Python -m pytest tests\test_handoff_status.py tests\test_tracked_user_data_policy.py tests\test_run_bat_api_entry.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run repository checks**

```powershell
& $Python tools\smoke_check.py --skip-tests
git diff --check
git status --short
```

Expected: first-party compilation, copied-database idempotence and integrity checks pass; only intended source/docs files appear; no `user_data/` is staged.

- [ ] **Step 3: Verify real database fingerprints**

Compare both root databases against the recorded pre-task size, UTC mtime and SHA-256. Expected: exact match.

- [ ] **Step 4: Request independent Sol Extra High review**

Review the full branch against the approved design. Fix every Critical and Important finding and rerun Steps 1-3. Expected final result: 0 Critical / 0 Important; record Minor findings explicitly.

- [ ] **Step 5: Record bootstrap evidence**

This governance package creates the handoff validator and therefore cannot use the new protocol to validate itself. Treat it as the one documented bootstrap exception. After review, append exact test, prompt-match, database-fingerprint and Sol-review evidence to this plan and create a docs-only evidence commit:

```powershell
git add docs\superpowers\plans\2026-07-11-day-night-handoff-protocol-implementation.md
git commit -m "docs: record handoff protocol bootstrap evidence"
```

Verify `git diff-tree --no-commit-id --name-only -r HEAD` lists only this plan. Once this package is merged, all eligible formal execution packages must use the strict protocol; no second bootstrap exception is allowed.

- [ ] **Step 6: Leave integration to the daytime integration workflow**

Push the feature branch, create a ready PR, verify checks, merge through GitHub, fast-forward local `main` and clean activity worktrees, then delete only the merged remote temporary branch. Do not let the nightly automation perform these actions.
