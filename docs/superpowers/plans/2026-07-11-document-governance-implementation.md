# Project Documentation Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把项目文档收敛为单一权威分层，删除已经完成、被取代或放弃的历史文档，修正用户操作口径，并用只读检查阻止状态再次漂移。

**Architecture:** PR A 先独立完成或无损迁出当前 integration worktree 的昼夜交接/用户验收支撑工作，并恢复干净集成通道；本计划只实施 PR B。PR B 用一个只读 Python 检查器固定文档契约，再分层重写权威文档、修正用户说明、删除历史文件、同步夜间自动化提示词，最后经 integration 完整验证后进入主线。

**Tech Stack:** Markdown、Python 3.12 标准库、pytest、PowerShell、Git worktree、Codex Desktop automation。

**治理类型：** `non_formal`
**规划状态：** `blocked_until_pr_a`
**设计基线：** `56e59d88b589426939a4000b756b17d4aac256fb`
**设计规格：** `docs/superpowers/specs/2026-07-11-document-governance-design.md`

## Global Constraints

- PR A 未进入最新 `origin/main`、integration 未恢复干净前，不得开始 Task 1 及后续文件修改。
- Task 0 同步 PR A 后必须重新核对本计划全部目标文件；出现范围、业务语义或文件所有权漂移时停止并回到 S-XH 更新计划。
- 不修改评分规则、题号契约、标签语义、状态含义、数据库 Schema 或生产启动行为。
- 不打开、修改、删除、暂存、提交或用 stash 打包根目录真实 `user_data/`。
- 所有数据库检查只使用工具创建的副本；开始和结束时比较根目录两库大小、UTC 修改时间和 SHA-256。
- 文档删除只通过 `apply_patch`，不使用递归删除、资源管理器删除或跨 shell 文件操作。
- 本计划不实施 P1-16、P2-01 或其他正式执行包。
- 不直接 push `main`；功能提交交给恢复后的 integration 通道逐个复核、完整验证并通过 PR 合并。
- `tools/check_documentation.py` 必须只读，不打开 SQLite、不写报告、不创建缓存、不修改任何文件。
- 当前规格和本计划在 PR B 技术验证、独立复审和最终交接完成前保留；最终提交删除二者，历史由分支提交和 PR 保存。

---

### Task 0: PR A 与执行基线硬门

**Files:**
- Read: `.worktrees/p1-integration-verification/`
- Read: `docs/superpowers/specs/2026-07-11-document-governance-design.md`
- Modify after successful sync: `docs/superpowers/plans/2026-07-11-document-governance-implementation.md`

**Interfaces:**
- Consumes: 已完成或无损迁出的昼夜交接/用户验收支撑工作，以及最新 `origin/main`。
- Produces: 干净 integration 通道、同步后的治理 worktree、实际执行基线 SHA 和真实数据只读基线。

- [ ] **Step 1: 只读确认 PR A 已进入主线**

```powershell
git fetch --prune
git -C ..\p1-integration-verification status --short --branch
git -C ..\p1-integration-verification log --oneline origin/main..HEAD
git -C ..\p1-integration-verification diff --name-status origin/main...HEAD
git -C ..\p1-integration-verification status --short -- user_data
```

Expected: integration 工作树无未提交项，`origin/main..HEAD` 无独有提交，`user_data` 状态为空。任一条件不满足时停止；不得在本计划中接管或整理 PR A。

- [ ] **Step 2: 确认 integration 与主线一致**

```powershell
$IntegrationHead = git -C ..\p1-integration-verification rev-parse HEAD
$MainHead = git rev-parse origin/main
if ($IntegrationHead -ne $MainHead) { throw "integration 尚未恢复到 origin/main" }
```

Expected: 两个完整 SHA 完全一致。

- [ ] **Step 3: 把治理分支同步到 PR A 后主线**

```powershell
git status --short
git rebase origin/main
git status --short --branch
```

Expected: rebase 无冲突，治理 worktree 仅包含本规格和本计划的已提交历史，工作树干净。若 AGENTS、Index、夜间提示词、并行手册或 Phase maps 冲突，停止并重新规划，不机械选择一侧。

- [ ] **Step 4: 重新定位全部目标文件**

```powershell
rg -n "核验基线|当前进度|状态/依赖|当前 Git 与 Worktree 快照|run\.bat|相近补入|set-mode --mode skill|--apply" AGENTS.md ARCHITECTURE.md README_*.md docs tools
git diff 56e59d88b589426939a4000b756b17d4aac256fb..origin/main -- AGENTS.md ARCHITECTURE.md README_工作机使用说明.md docs/superpowers/packages docs/superpowers/plans docs/superpowers/specs docs/ui/STYLE.md docs/maintenance
```

Expected: 所有新增 PR A 规则都能归入已批准规格；若出现新的业务决定、Schema、真实数据操作或未包含的用户验收语义，停止并更新规格/计划。

- [ ] **Step 5: 记录执行基线与真实数据只读指纹**

```powershell
$CommonDir = Resolve-Path (git rev-parse --git-common-dir)
$RepoRoot = Split-Path $CommonDir -Parent
$Python = Join-Path $RepoRoot "runtime\python\python.exe"
$ExecutionBase = git rev-parse origin/main
Get-Item "$RepoRoot\user_data\databases\grading_system.db", "$RepoRoot\user_data\databases\question_bank.db" |
  Select-Object FullName,Length,LastWriteTimeUtc
Get-FileHash -Algorithm SHA256 "$RepoRoot\user_data\databases\grading_system.db", "$RepoRoot\user_data\databases\question_bank.db"
```

Expected: 输出两个数据库的路径、大小、UTC 修改时间和 SHA-256；不使用 SQLite 打开源文件。

- [ ] **Step 6: 用 apply_patch 更新本计划的执行证据**

在本文件头部追加两行，值取 Step 5 的真实输出：

```markdown
**执行基线：** `Step 5 输出的完整 origin/main SHA`
**PR A 门槛：** `passed`
```

同时把 `规划状态` 从 `blocked_until_pr_a` 改为 `ready_for_execution`。不得写短 SHA 或推测值。

- [ ] **Step 7: 提交计划基线刷新**

```powershell
git add docs\superpowers\plans\2026-07-11-document-governance-implementation.md
git diff --cached --check
git commit -m "docs: refresh documentation governance baseline"
```

---

### Task 1: 只读文档治理检查器

**Files:**
- Create: `tools/check_documentation.py`
- Create: `tests/test_documentation_governance.py`

**Interfaces:**
- Produces: `DocumentationIssue(code: str, path: str, line: int, message: str)`。
- Produces: `run_checks(project_root: Path) -> list[DocumentationIssue]`，只读返回稳定排序问题列表。
- Produces: CLI `python tools/check_documentation.py --root .`，无问题返回 0，有问题逐行输出并返回 1。
- Consumes later: `tools/smoke_check.py` 的文档检查步骤。

- [ ] **Step 1: 写检查器单元测试**

创建以下测试；每个测试只使用 `tmp_path`，不得读取真实数据库：

```python
from __future__ import annotations

from pathlib import Path

from tools.check_documentation import (
    check_markdown_links,
    check_package_registry,
    check_removed_references,
    check_status_ownership,
    check_user_documents,
    run_checks,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_missing_relative_markdown_link_has_stable_location(tmp_path: Path) -> None:
    _write(tmp_path / "docs" / "guide.md", "[missing](other.md)\n")
    issues = check_markdown_links(tmp_path)
    assert [(item.code, item.path, item.line) for item in issues] == [
        ("DOC001", "docs/guide.md", 1)
    ]


def test_user_documents_reject_obsolete_runtime_and_skill_instructions(tmp_path: Path) -> None:
    _write(tmp_path / "README_工作机使用说明.md", "运行 run.bat\n")
    _write(tmp_path / "README_私人便携版_v1.5.0.md", "允许相近补入\n")
    issues = check_user_documents(tmp_path)
    assert {item.code for item in issues} == {"DOC101", "DOC102"}


def test_user_guide_requires_existing_portable_launcher(tmp_path: Path) -> None:
    _write(tmp_path / "README_工作机使用说明.md", "双击 `运行.bat`。\n")
    issues = check_user_documents(tmp_path)
    assert [(item.code, item.path) for item in issues] == [
        ("DOC104", "README_工作机使用说明.md")
    ]


def test_phase_and_matrix_must_not_copy_dynamic_status(tmp_path: Path) -> None:
    _write(
        tmp_path / "docs/superpowers/packages/phase-1-execution-packages.md",
        "### P1-09 示例\n- **状态/依赖：** `planned`；无。\n",
    )
    _write(
        tmp_path / "docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md",
        "| 包 | 标题 | 状态 | 执行模型 | 夜间资格 |\n",
    )
    issues = check_status_ownership(tmp_path)
    assert {item.code for item in issues} == {"DOC201", "DOC202"}


def test_entry_documents_reject_volatile_snapshots(tmp_path: Path) -> None:
    _write(tmp_path / "AGENTS.md", "## 当前进度快照（2026-07-11）\n")
    _write(tmp_path / "ARCHITECTURE.md", "## 13. 更新记录\n")
    _write(
        tmp_path / "docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md",
        "### 0.4 当前进度（2026-07-11）\n",
    )
    issues = check_status_ownership(tmp_path)
    assert {item.code for item in issues} == {"DOC205", "DOC207", "DOC208"}


def test_removed_document_name_is_rejected_outside_governance_plan(tmp_path: Path) -> None:
    _write(tmp_path / "docs/current.md", "读取 `PLAN_AUDIT_2026-07-10.md`。\n")
    issues = check_removed_references(tmp_path)
    assert [(item.code, item.path) for item in issues] == [
        ("DOC401", "docs/current.md")
    ]


def test_package_registry_compares_title_model_and_formal_ids(tmp_path: Path) -> None:
    phase = tmp_path / "docs/superpowers/packages/phase-1-execution-packages.md"
    matrix = tmp_path / "docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md"
    _write(phase, "### P1-09 示例包\n- **模型：** `S-XH / T-M / T-H`。\n")
    _write(
        matrix,
        "| 包 | 标题 | 执行模型 | 夜间资格 | 理由 |\n"
        "|---|---|---|---|---|\n"
        "| P1-09 | 错误标题 | `T-H` | `eligible_after_plan` | test |\n",
    )
    issues = check_package_registry(tmp_path, expected_ids={"P1-09"})
    assert {item.code for item in issues} == {"DOC301", "DOC302"}


def test_run_checks_is_read_only(tmp_path: Path) -> None:
    _write(tmp_path / "README_工作机使用说明.md", "双击 `运行.bat`。\n")
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    run_checks(tmp_path)
    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before
```

- [ ] **Step 2: 运行测试并确认 RED**

```powershell
& $Python -m pytest tests\test_documentation_governance.py -q
```

Expected: collection 失败，因为 `tools.check_documentation` 尚不存在。

- [ ] **Step 3: 实现检查器核心**

实现以下结构和稳定错误码；不得引入第三方库：

```python
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
PHASE_PACKAGE_RE = re.compile(r"^###\s+(P[1-5]-\d{2})\s+(.+?)\s*$")
MODEL_RE = re.compile(r"^- \*\*模型：\*\*\s+`([^`]+)`")
MATRIX_ROW_RE = re.compile(
    r"^\|\s*(P[1-5]-\d{2})\s*\|\s*(.*?)\s*\|\s*`(T-M|T-H|S-H|S-XH)`\s*\|\s*`?"
    r"(eligible_after_plan|daytime_only|completed_not_applicable)`?\s*\|"
)

PHASE_PATHS = tuple(
    f"docs/superpowers/packages/phase-{phase}-execution-packages.md"
    for phase in range(1, 6)
)
FORMAL_IDS = frozenset(
    [*(f"P1-{value:02d}" for value in range(9, 30))]
    + [*(f"P2-{value:02d}" for value in range(1, 23))]
    + [*(f"P3-{value:02d}" for value in range(1, 20))]
    + [*(f"P4-{value:02d}" for value in range(1, 13))]
    + [*(f"P5-{value:02d}" for value in range(1, 14))]
)

GOVERNANCE_PLANNING_PATHS = frozenset(
    {
        "docs/superpowers/specs/2026-07-11-document-governance-design.md",
        "docs/superpowers/plans/2026-07-11-document-governance-implementation.md",
    }
)
REMOVED_REFERENCE_NAMES = frozenset(
    {
        "PLAN_AUDIT_2026-07-10.md",
        "code-ownership-map.md",
        "knowledge-practice-operations.md",
        "2026-07-03-wp0-2-repo-hygiene-implementation.md",
        "2026-07-03-wp0-3-dependency-lock-implementation.md",
        "2026-07-03-wp0-4-schema-baseline-implementation.md",
        "2026-07-03-wp0-5-smoke-script-implementation.md",
        "2026-07-08-wp1-1-fastapi-skeleton-implementation.md",
        "2026-07-09-wp1-2-api-routes-batch-a-implementation.md",
        "2026-07-09-wp1-2-api-routes-batch-b-implementation.md",
        "2026-07-09-wp1-2-api-routes-batch-c-implementation.md",
        "2026-07-09-wp1-2-api-routes-batch-d-implementation.md",
        "2026-07-09-wp1-2-batch-e-grading-run-job-implementation.md",
        "2026-07-09-wp1-2-batch-e-report-export-job-implementation.md",
        "2026-07-09-wp1-2-batch-e-review-routes-implementation.md",
        "2026-07-09-wp1-2-batch-e-scan-analysis-job-implementation.md",
        "2026-07-09-wp1-3-job-manager-minimal-implementation.md",
        "2026-07-10-p1-09-windows-path-and-skip-cleanup-implementation.md",
        "2026-07-10-p1-10-job-manager-schema-lifecycle-implementation.md",
        "2026-07-10-p1-11-cooperative-job-cancellation-implementation.md",
        "2026-07-10-p1-12-review-service-atomic-write-implementation.md",
        "2026-07-10-p1-13-controlled-media-download-implementation.md",
        "2026-07-10-p1-14-phase-1-stabilization-checkpoint-implementation.md",
        "2026-07-10-p1-15-question-bank-read-routes-implementation.md",
        "2026-06-30-fine-grained-knowledge-graph-design.md",
        "2026-06-28-grading-paper-skill-workflow-design.md",
        "2026-06-13-answer-region-calibration-redesign.md",
        "2026-06-13-knowledge-graph-question-bank-practice-design.md",
        "2026-06-19-grading-completeness-and-shared-regions-design.md",
        "2026-06-19-simplified-knowledge-alignment-training-recommendation-design.md",
        "2026-06-21-question-bank-source-paper-archive-design.md",
        "2026-06-22-unified-skill-catalog-design.md",
        "2026-06-28-question-tags-knowledge-graph-design.md",
        "2026-06-29-import-dialog-direct-tag-save-design.md",
        "2026-06-30-question-bank-tagging-config-readonly-design.md",
        "2026-07-01-unified-question-ids-resumable-grading-tag-retry-design.md",
        "2026-07-10-roadmap-execution-packages-design.md",
        "2026-07-11-nightly-eligibility-matrix-design.md",
        "2026-07-11-nightly-single-package-automation-design.md",
        "2026-07-11-nightly-automation-implementation.md",
        "2026-07-11-nightly-eligibility-matrix-implementation.md",
        "2026-07-11-day-night-handoff-user-acceptance-design.md",
        "2026-07-11-day-night-handoff-protocol-implementation.md",
        "2026-07-11-user-acceptance-runtime-foundation-implementation.md",
    }
)


@dataclass(frozen=True, order=True)
class DocumentationIssue:
    code: str
    path: str
    line: int
    message: str


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _markdown_files(root: Path) -> list[Path]:
    paths = [*root.glob("*.md"), *(root / "docs").rglob("*.md")]
    return sorted(path for path in paths if ".worktrees" not in path.parts)


def check_markdown_links(project_root: Path) -> list[DocumentationIssue]:
    root = Path(project_root).resolve()
    issues: list[DocumentationIssue] = []
    for path in _markdown_files(root):
        text = path.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK_RE.finditer(text):
            raw = match.group(1).strip().strip("<>")
            target = raw.split("#", 1)[0]
            if not target or "://" in target or target.startswith("#"):
                continue
            if not (path.parent / target).resolve().exists():
                issues.append(
                    DocumentationIssue(
                        "DOC001",
                        _relative(root, path),
                        _line_number(text, match.start()),
                        f"Markdown target does not exist: {raw}",
                    )
                )
    return sorted(issues)


def _text_issues(
    root: Path,
    relative_path: str,
    checks: tuple[tuple[str, str, str], ...],
) -> list[DocumentationIssue]:
    path = root / relative_path
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    issues: list[DocumentationIssue] = []
    for code, needle, message in checks:
        offset = text.find(needle)
        if offset >= 0:
            issues.append(
                DocumentationIssue(code, relative_path, _line_number(text, offset), message)
            )
    return issues


def check_user_documents(project_root: Path) -> list[DocumentationIssue]:
    root = Path(project_root).resolve()
    issues = _text_issues(
        root,
        "README_工作机使用说明.md",
        (
            ("DOC101", "run.bat", "Use the portable 运行.bat launcher."),
            ("DOC103", "set-mode --mode skill", "Remove the retired skill-mode operation."),
        ),
    )
    issues.extend(
        _text_issues(
            root,
            "README_私人便携版_v1.5.0.md",
            (("DOC102", "相近补入", "Remove the retired approximate-skill recommendation."),),
        )
    )
    guide = root / "README_工作机使用说明.md"
    if guide.exists() and "运行.bat" in guide.read_text(encoding="utf-8"):
        launcher = root / "运行.bat"
        if not launcher.exists():
            issues.append(
                DocumentationIssue(
                    "DOC104",
                    "README_工作机使用说明.md",
                    1,
                    "Referenced portable launcher is missing: 运行.bat",
                )
            )
    return sorted(issues)


def check_status_ownership(project_root: Path) -> list[DocumentationIssue]:
    root = Path(project_root).resolve()
    issues: list[DocumentationIssue] = []
    for relative_path in PHASE_PATHS:
        issues.extend(
            _text_issues(
                root,
                relative_path,
                (("DOC201", "**状态/依赖：**", "Phase maps must store dependencies, not dynamic status."),),
            )
        )
    issues.extend(
        _text_issues(
            root,
            "docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md",
            (("DOC202", "| 状态 |", "Eligibility matrix must not copy package status."),),
        )
    )
    issues.extend(
        _text_issues(
            root,
            "docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md",
            (
                ("DOC203", "当前 Git 与 Worktree 快照", "Persistent workflow must not contain a live snapshot."),
                ("DOC204", ".worktrees/p1-16-question-bank-write", "Persistent workflow must not hard-code active worktrees."),
            ),
        )
    )
    issues.extend(
        _text_issues(
            root,
            "AGENTS.md",
            (("DOC205", "## 当前进度快照", "AGENTS must not copy volatile project status."),),
        )
    )
    issues.extend(
        _text_issues(
            root,
            "ARCHITECTURE.md",
            (
                ("DOC206", "尚未合并到 `main`", "Architecture must not describe branch merge status."),
                ("DOC207", "## 13. 更新记录", "Move implementation history out of Architecture."),
            ),
        )
    )
    issues.extend(
        _text_issues(
            root,
            "docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md",
            (("DOC208", "### 0.4 当前进度", "Master plan must not copy current progress."),),
        )
    )
    return sorted(issues)


def check_removed_references(project_root: Path) -> list[DocumentationIssue]:
    root = Path(project_root).resolve()
    issues: list[DocumentationIssue] = []
    for path in _markdown_files(root):
        relative = _relative(root, path)
        if relative in GOVERNANCE_PLANNING_PATHS:
            continue
        text = path.read_text(encoding="utf-8")
        for name in sorted(REMOVED_REFERENCE_NAMES):
            offset = text.find(name)
            if offset >= 0:
                issues.append(
                    DocumentationIssue(
                        "DOC401",
                        relative,
                        _line_number(text, offset),
                        f"Reference to removed document: {name}",
                    )
                )
    return sorted(issues)


def _phase_packages(root: Path) -> dict[str, tuple[str, str, str, int]]:
    packages: dict[str, tuple[str, str, str, int]] = {}
    for relative_path in PHASE_PATHS:
        path = root / relative_path
        if not path.exists():
            continue
        current: tuple[str, str, int] | None = None
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            heading = PHASE_PACKAGE_RE.match(line)
            if heading:
                current = (heading.group(1), heading.group(2).strip(), line_number)
                continue
            model = MODEL_RE.match(line)
            if current and model:
                parts = [part.strip() for part in model.group(1).split("/")]
                if len(parts) == 3:
                    package_id, title, heading_line = current
                    packages[package_id] = (title, parts[1], relative_path, heading_line)
                current = None
    return packages


def _matrix_packages(root: Path) -> dict[str, tuple[str, str, str, int]]:
    relative_path = "docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md"
    path = root / relative_path
    packages: dict[str, tuple[str, str, str, int]] = {}
    if not path.exists():
        return packages
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = MATRIX_ROW_RE.match(line)
        if match:
            packages[match.group(1)] = (
                match.group(2).strip(),
                match.group(3),
                match.group(4),
                line_number,
            )
    return packages


def check_package_registry(
    project_root: Path,
    *,
    expected_ids: set[str] | frozenset[str] = FORMAL_IDS,
) -> list[DocumentationIssue]:
    root = Path(project_root).resolve()
    phase = _phase_packages(root)
    matrix = _matrix_packages(root)
    issues: list[DocumentationIssue] = []
    for package_id in sorted(set(expected_ids) - set(phase)):
        issues.append(DocumentationIssue("DOC303", "docs/superpowers/packages", 1, f"Phase map missing {package_id}"))
    for package_id in sorted(set(expected_ids) - set(matrix)):
        issues.append(DocumentationIssue("DOC304", "docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md", 1, f"Matrix missing {package_id}"))
    for package_id in sorted(set(expected_ids) & set(phase) & set(matrix)):
        phase_title, phase_model, phase_path, phase_line = phase[package_id]
        matrix_title, matrix_model, _, matrix_line = matrix[package_id]
        if phase_title != matrix_title:
            issues.append(DocumentationIssue("DOC301", phase_path, phase_line, f"Title mismatch for {package_id}"))
        if phase_model != matrix_model:
            issues.append(DocumentationIssue("DOC302", "docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md", matrix_line, f"Execution model mismatch for {package_id}"))
    return sorted(issues)


def run_checks(project_root: Path) -> list[DocumentationIssue]:
    root = Path(project_root).resolve()
    return sorted(
        [
            *check_markdown_links(root),
            *check_user_documents(root),
            *check_status_ownership(root),
            *check_package_registry(root),
            *check_removed_references(root),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check project documentation governance")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    issues = run_checks(args.root)
    for issue in issues:
        print(f"[{issue.code}] {issue.path}:{issue.line} {issue.message}")
    if issues:
        print(f"Documentation check failed: {len(issues)} issue(s).")
        return 1
    print("Documentation check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行单元测试并确认 GREEN**

```powershell
& $Python -m pytest tests\test_documentation_governance.py -q
```

Expected: 8 passed。

- [ ] **Step 5: 对当前仓库运行检查并记录预期 RED**

```powershell
& $Python tools\check_documentation.py --root .
```

Expected: 返回 1，并至少报告 `DOC101`、`DOC102`、`DOC201`、`DOC202`、`DOC205`、`DOC206`、`DOC207`、`DOC208`、`DOC401`；这证明已知漂移能被守卫发现。不得为了立即全绿删除检查项。

- [ ] **Step 6: 提交只读检查器**

```powershell
git add tools\check_documentation.py tests\test_documentation_governance.py
git diff --cached --check
git commit -m "test: add documentation governance checks"
```

---

### Task 2: 收口 AGENTS、Architecture 与 Master

**Files:**
- Modify: `AGENTS.md`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md`

**Interfaces:**
- Produces: `AGENTS.md` 只承载长期协作规则。
- Produces: `ARCHITECTURE.md` 只承载当前已实现事实。
- Produces: Master 只承载战略、阶段依赖和 D1-D9 决策。

- [ ] **Step 1: 重写 AGENTS 当前快照**

使用 `apply_patch` 删除以下内容：P1-09 至 P1-15 的逐包摘要、具体测试数字、PR #3 至 #8 流水、旧 worktree 删除和 runtime 恢复事故、`下一步建议` 中重复的 P1-16 状态。

保留并按以下顺序组织：

```markdown
# AI 阅卷系统 Codex 接手说明

## 当前产品边界
## 必读顺序
## 权威顺序
## 数据与安全红线
## 执行包工作原则
## Git 与 Worktree 强制流程
## 夜间自动化
## 常用命令
## 当前工作入口
```

“当前工作入口”只写：当前阶段、队列和下一动作必须读取 `EXECUTION_INDEX.md`，不得在 AGENTS 复制包状态。

PR A 新增并已经进入主线的昼夜交接字段、用户验收入口和数据隔离红线属于当前长期规则，必须归入“执行包工作原则”或对应专项入口，不得随历史流水删除。

- [ ] **Step 2: 修正 Architecture 顶部证据和运行视图**

把“核验基线”改为最新 `origin/main`，不写分支名、短 SHA 或“尚未合并”。更新系统上下文图，使本机浏览器可分别访问 Streamlit 与增量 FastAPI；明确 Vue 尚未成为生产 UI。

删除 `## 13. 更新记录`；把其中仍影响当前实现的事实合并到对应运行、数据、安全或风险章节，逐包测试数字不迁移。

- [ ] **Step 3: 移除 Architecture 的实时计划信息**

删除下一包、当前 worktree、当前分支和具体 PR 状态；保留用户确认的长期约束、当前真实数据例外和结构性风险。将“文档和版本元数据漂移”风险更新为本治理包的修正目标，不再引用已知错误的旧 README 版本号。

- [ ] **Step 4: 精简 Master 的易失内容**

删除 `### 0.4 当前进度`、具体合并 SHA、当前测试数字、具体 worktree 和“状态：未开始/进行中”实时句子。Phase 段改用稳定“启动条件/目标/验收”；D1-D9 决策保留。

将 D8 改为：主强调色及所有 Token 只以 `docs/ui/STYLE.md` 为准，不在 Master 保存颜色值或区间。

- [ ] **Step 5: 运行聚焦检查**

```powershell
rg -n "尚未合并到 `main`|合并提交 `[0-9a-f]+`|### 0\.4 当前进度|P1-09 已验证|本次提交前|当前只保留根目录" AGENTS.md ARCHITECTURE.md docs\superpowers\plans\2026-07-03-frontend-backend-modernization-master-plan.md
git diff --check
```

Expected: 零命中，`git diff --check` 退出 0。

- [ ] **Step 6: 提交权威入口收口**

```powershell
git add AGENTS.md ARCHITECTURE.md docs\superpowers\plans\2026-07-03-frontend-backend-modernization-master-plan.md
git commit -m "docs: separate current facts from roadmap history"
```

---

### Task 3: 让 Index 成为唯一包状态源

**Files:**
- Modify: `docs/superpowers/packages/README.md`
- Modify: `docs/superpowers/packages/EXECUTION_INDEX.md`
- Modify: `docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md`
- Modify: `docs/superpowers/packages/phase-1-execution-packages.md`
- Modify: `docs/superpowers/packages/phase-2-execution-packages.md`
- Modify: `docs/superpowers/packages/phase-3-execution-packages.md`
- Modify: `docs/superpowers/packages/phase-4-execution-packages.md`
- Modify: `docs/superpowers/packages/phase-5-execution-packages.md`
- Modify: `docs/superpowers/packages/phase-6-deferred.md`

**Interfaces:**
- Produces: Index 唯一保存动态状态。
- Produces: Phase maps 保存依赖、包定义和历史完成摘要。
- Produces: Matrix 保存稳定夜间资格，不保存动态状态。

- [ ] **Step 1: 更新 packages README 的权威规则**

明确：Index 是状态唯一来源；Phase maps 的“依赖”不是当前状态；Matrix 只负责资格。删除对 `PLAN_AUDIT_2026-07-10.md` 和旧 roadmap spec 的入口。

- [ ] **Step 2: 重写 Index 当前队列**

保留阶段总表和已合并总数；删除易失测试流水和旧文档入口。当前执行队列必须包含：

```markdown
| 顺序 | 包 | 状态 | 目标 | 模型（规划/执行/复核） |
|---:|---|---|---|---|
| 1 | P1-16 Question Bank 轻写与导入准备 | `ready` | 教师确认标签与安全导入请求，耗时导入仍交给 Job | `S-XH / T-H / S-H` |
| 2 | P2-01 前端工程、依赖锁与质量命令 | `ready` | 只建立前端工程与质量命令，不开始页面视觉 | `S-XH / T-M / T-H` |
```

下一动作仍是两包并行，但加上“必须先生成各自即时计划并现场核验 worktree”的门槛。

- [ ] **Step 3: 删除五份 Phase map 的动态状态字段**

对每个正式包把：

```markdown
- **状态/依赖：** `planned`；P2-01。
```

改为：

```markdown
- **依赖：** P2-01。
```

已完成 P1-09 至 P1-15 的详细状态段压缩为 Phase 1 顶部历史完成表；正式包定义只保留稳定目标、边界、验收和模型。

PR A 已加入 Phase 1/2 的用户验收依赖或交接门槛必须保留为稳定验收条件；只删除其动态进度值。

- [ ] **Step 4: 删除 Matrix 的状态列**

所有正式包表统一为：

```markdown
| 包 | 标题 | 执行模型 | 夜间资格 | Sol 理由与额外门槛 |
|---|---|---|---|---|
```

逐行移除 `merged/ready/planned` 单元格，不改变 ID、标题、执行模型、资格或理由。历史 P1-01 至 P1-08 表只保留 ID、标题和 `completed_not_applicable`。

- [ ] **Step 5: 运行包注册表检查**

```powershell
& $Python -m pytest tests\test_documentation_governance.py -q
& $Python tools\check_documentation.py --root .
```

Expected: 单元测试通过；`DOC201`、`DOC202`、`DOC301`、`DOC302`、`DOC303`、`DOC304` 零出现。用户文档和历史文件问题此时仍可存在。

- [ ] **Step 6: 提交状态所有权收口**

```powershell
git add docs\superpowers\packages
git diff --cached --check
git commit -m "docs: make execution index the sole status source"
```

---

### Task 4: 修正用户说明、存储策略与视觉 Token

**Files:**
- Modify: `README_工作机使用说明.md`
- Modify: `README_私人便携版_v1.5.0.md`
- Modify: `docs/maintenance/storage-policy.md`
- Modify: `docs/ui/STYLE.md`
- Read before removal: `docs/knowledge-practice-operations.md`

**Interfaces:**
- Produces: 唯一有效的工作机启动和训练推荐操作说明。
- Produces: 与实际 CLI 一致且需要单独授权的存储维护流程。
- Produces: 唯一视觉 Token 来源。

- [ ] **Step 1: 修正工作机启动说明**

快速开始必须明确：完整便携包无需预装 Python、无需首次联网安装，双击 `运行.bat`；开发命令优先使用 `runtime\python\python.exe`。删除 `run.bat`、创建 venv 和首次安装依赖说明。

- [ ] **Step 2: 合并有效训练说明**

从 `docs/knowledge-practice-operations.md` 迁移以下当前规则：来源题必须确认关联；候选必须共享精确裁剪后的 `knowledge_point`；不足直接缺题；标签修改影响下一次查询但不改写旧任务快照；训练结果自动回流尚未启用。

删除旧技能目录 `dry-run/apply/shadow/skill/legacy` 操作命令和“普通教师维护技能目录”说明。

- [ ] **Step 3: 精简私人便携 README**

只保留便携启动、包含的数据、不包含 API 密钥、新电脑重新配置密钥和可用 Codex 继续开发。删除“具体训练技能”“相近补入”“技能目录与待处理问题”和旧迁移流程。

- [ ] **Step 4: 修正存储维护参数和授权边界**

把维护流程改为：

```markdown
1. 只读审计前先获得本次生成审计报告的授权。
2. 运行 `runtime\python\python.exe tools\storage_audit.py --root .`。
3. 运行 `runtime\python\python.exe tools\storage_maintenance.py --root .` 预览。
4. 只有用户对本次真实数据操作明确授权后，才可单独使用 `--apply-hardlinks` 或 `--apply-archives`。
```

不得记录不存在的 `--dry-run` 或 `--apply` 参数。

- [ ] **Step 5: 固化 STYLE Token**

将 STYLE 的三项 Token 改为：

```css
--color-accent: #2563EB;
--color-accent-hover: #1D4ED8;
--color-accent-subtle: #EFF6FF;
```

其他中性色、AI 色、教师色和反面清单保持不变。

- [ ] **Step 6: 运行用户文档检查**

```powershell
rg -n -F "run.bat" README_*.md docs\user-testing
rg -n "相近补入|set-mode --mode skill|首次启动.*安装依赖|--dry-run|`--apply`" README_*.md docs\maintenance\storage-policy.md
& $Python -m pytest tests\test_documentation_governance.py -q
```

Expected: `rg` 零命中，测试通过。

- [ ] **Step 7: 提交用户和专项文档修正**

```powershell
git add README_工作机使用说明.md README_私人便携版_v1.5.0.md docs\maintenance\storage-policy.md docs\ui\STYLE.md
git commit -m "docs: correct user operations and design tokens"
```

---

### Task 5: 持久并行手册与夜间自动化同步

**Files:**
- Create: `docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md`
- Delete: `docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION_2026-07-11.md`
- Modify: `AGENTS.md`
- Modify: `docs/superpowers/packages/README.md`
- Modify: `docs/superpowers/packages/EXECUTION_INDEX.md`
- Modify: `docs/superpowers/packages/NIGHTLY_AUTOMATION.md`
- External managed config: existing “阅卷系统夜间4点推进” automation

**Interfaces:**
- Produces: 无日期、无当前快照的持久并行规则入口。
- Produces: 项目权威 prompt 与 Codex 自动化实际 prompt 精确一致。

- [ ] **Step 1: 创建持久并行手册**

新文件只保留以下章节：

```markdown
# 并行 Worktree 执行手册

## 1. 现场核验与数据红线
## 2. 两实现通道加一集成通道
## 3. 一包一分支与依赖基线
## 4. 共享文件和冲突所有权
## 5. 功能包完成门槛
## 6. 集成顺序和验证
## 7. 必须暂停的情况
## 8. Main 同步与安全清理
```

删除当前 worktree 路径/HEAD、分支删除历史、runtime 恢复事件、固定 Wave 1 快照和绝对 Node 路径。保留 P1/P2/LLM/迁移等共享文件冲突规则，但写成按包现场匹配，不写当前占用结论。

- [ ] **Step 2: 更新所有仓库入口**

把活动文档对 `PARALLEL_WORKTREE_EXECUTION_2026-07-11.md` 的引用统一改为 `PARALLEL_WORKTREE_EXECUTION.md`。Index 只说明用途，不再称其为“当前 worktree 清单”。

- [ ] **Step 3: 更新夜间权威 prompt**

只把 prompt 中的旧并行手册路径替换为新路径；任务选择、资格、TDD、数据守卫、禁止 push/PR 等语义不变。

- [ ] **Step 4: 使用 automation 工具同步现有任务**

先只读找到并查看名称为“阅卷系统夜间4点推进”的现有 automation；调用 `codex_app__automation_update` 时保留原 name、schedule、model、reasoning effort、execution environment、project、cwd 和 ACTIVE 状态，只替换 prompt。禁止手工编辑 automation TOML。

- [ ] **Step 5: 精确核验 prompt**

反读 automation 配置，比较 `NIGHTLY_AUTOMATION.md` 的 `AUTOMATION_PROMPT_START/END` 标记间文本与实际 prompt；要求 `PROMPT_MATCH=YES`。同时确认名称、每天 4:00、模型、推理、项目和启用状态没有变化。

- [ ] **Step 6: 运行持久规则检查**

```powershell
rg -n "当前 Git 与 Worktree 快照|\.worktrees/p1-16-question-bank-write|\.worktrees/p2-01-frontend-foundation|[0-9a-f]{40}|codex-runtimes.*node" docs\superpowers\packages\PARALLEL_WORKTREE_EXECUTION.md
& $Python -m pytest tests\test_documentation_governance.py -q
```

Expected: `rg` 零命中，测试通过。

- [ ] **Step 7: 提交持久并行规则**

```powershell
git add AGENTS.md docs\superpowers\packages\README.md docs\superpowers\packages\EXECUTION_INDEX.md docs\superpowers\packages\NIGHTLY_AUTOMATION.md docs\superpowers\packages\PARALLEL_WORKTREE_EXECUTION.md docs\superpowers\packages\PARALLEL_WORKTREE_EXECUTION_2026-07-11.md
git diff --cached --check
git commit -m "docs: make parallel worktree rules durable"
```

---

### Task 6: 删除历史 plans、specs、审计和重复说明

**Files:**
- Delete: `docs/knowledge-practice-operations.md`
- Delete: `docs/maintenance/code-ownership-map.md`
- Delete: `docs/superpowers/packages/PLAN_AUDIT_2026-07-10.md`
- Delete the 23 completed plan files listed below.
- Delete the 15 pre-governance spec files listed below.
- Delete after PR A completion if present: `docs/superpowers/specs/2026-07-11-day-night-handoff-user-acceptance-design.md`
- Delete after PR A completion if present: `docs/superpowers/plans/2026-07-11-day-night-handoff-protocol-implementation.md`
- Delete after PR A completion if present: `docs/superpowers/plans/2026-07-11-user-acceptance-runtime-foundation-implementation.md`

**Interfaces:**
- Consumes: 已迁入权威文档的当前规则。
- Produces: 无 archive、无 redirect stub 的精简活动文档树。

**23 个完成计划：**

```text
docs/superpowers/plans/2026-07-03-wp0-2-repo-hygiene-implementation.md
docs/superpowers/plans/2026-07-03-wp0-3-dependency-lock-implementation.md
docs/superpowers/plans/2026-07-03-wp0-4-schema-baseline-implementation.md
docs/superpowers/plans/2026-07-03-wp0-5-smoke-script-implementation.md
docs/superpowers/plans/2026-07-08-wp1-1-fastapi-skeleton-implementation.md
docs/superpowers/plans/2026-07-09-wp1-2-api-routes-batch-a-implementation.md
docs/superpowers/plans/2026-07-09-wp1-2-api-routes-batch-b-implementation.md
docs/superpowers/plans/2026-07-09-wp1-2-api-routes-batch-c-implementation.md
docs/superpowers/plans/2026-07-09-wp1-2-api-routes-batch-d-implementation.md
docs/superpowers/plans/2026-07-09-wp1-2-batch-e-grading-run-job-implementation.md
docs/superpowers/plans/2026-07-09-wp1-2-batch-e-report-export-job-implementation.md
docs/superpowers/plans/2026-07-09-wp1-2-batch-e-review-routes-implementation.md
docs/superpowers/plans/2026-07-09-wp1-2-batch-e-scan-analysis-job-implementation.md
docs/superpowers/plans/2026-07-09-wp1-3-job-manager-minimal-implementation.md
docs/superpowers/plans/2026-07-10-p1-09-windows-path-and-skip-cleanup-implementation.md
docs/superpowers/plans/2026-07-10-p1-10-job-manager-schema-lifecycle-implementation.md
docs/superpowers/plans/2026-07-10-p1-11-cooperative-job-cancellation-implementation.md
docs/superpowers/plans/2026-07-10-p1-12-review-service-atomic-write-implementation.md
docs/superpowers/plans/2026-07-10-p1-13-controlled-media-download-implementation.md
docs/superpowers/plans/2026-07-10-p1-14-phase-1-stabilization-checkpoint-implementation.md
docs/superpowers/plans/2026-07-10-p1-15-question-bank-read-routes-implementation.md
docs/superpowers/plans/2026-07-11-nightly-automation-implementation.md
docs/superpowers/plans/2026-07-11-nightly-eligibility-matrix-implementation.md
```

**15 个旧规格：**

```text
docs/superpowers/specs/2026-06-13-answer-region-calibration-redesign.md
docs/superpowers/specs/2026-06-13-knowledge-graph-question-bank-practice-design.md
docs/superpowers/specs/2026-06-19-grading-completeness-and-shared-regions-design.md
docs/superpowers/specs/2026-06-19-simplified-knowledge-alignment-training-recommendation-design.md
docs/superpowers/specs/2026-06-21-question-bank-source-paper-archive-design.md
docs/superpowers/specs/2026-06-22-unified-skill-catalog-design.md
docs/superpowers/specs/2026-06-28-grading-paper-skill-workflow-design.md
docs/superpowers/specs/2026-06-28-question-tags-knowledge-graph-design.md
docs/superpowers/specs/2026-06-29-import-dialog-direct-tag-save-design.md
docs/superpowers/specs/2026-06-30-fine-grained-knowledge-graph-design.md
docs/superpowers/specs/2026-06-30-question-bank-tagging-config-readonly-design.md
docs/superpowers/specs/2026-07-01-unified-question-ids-resumable-grading-tag-retry-design.md
docs/superpowers/specs/2026-07-10-roadmap-execution-packages-design.md
docs/superpowers/specs/2026-07-11-nightly-eligibility-matrix-design.md
docs/superpowers/specs/2026-07-11-nightly-single-package-automation-design.md
```

- [ ] **Step 1: 删除清单中的文件**

使用单个 `apply_patch` 的 `*** Delete File` 条目删除现存文件。PR A 三个支撑设计/计划只有在其状态已经完成且当前规则已迁入 AGENTS、夜间自动化、并行手册和 `docs/user-testing/` 后才删除；否则停止并更新本计划。

- [ ] **Step 2: 检查活动引用**

```powershell
rg -n "PLAN_AUDIT_2026-07-10|roadmap-execution-packages-design|wp0-[2345]-|wp1-[123]-|p1-0[9]-|p1-1[0-5]-|nightly-.*-implementation|fine-grained-knowledge-graph-design|grading-paper-skill-workflow-design|knowledge-practice-operations|code-ownership-map" --glob "*.md" .
```

Expected: 除当前治理规格和本计划中的删除清单说明外零命中；最终 Task 8 删除当前规格/计划后要求全仓零命中。

- [ ] **Step 3: 核对文档清单**

```powershell
$Docs = @(rg --files -g "*.md" -g "!user_data/**" -g "!.worktrees/**")
$Docs | Sort-Object
"MARKDOWN_COUNT=$($Docs.Count)"
```

Expected: 活动职责均有唯一入口；数量预计约 24 至 27（包含当前规格/计划），不以数量强行删除仍活动的 user-testing 文档。

- [ ] **Step 4: 运行链接和包检查**

```powershell
& $Python tools\check_documentation.py --root .
& $Python -m pytest tests\test_documentation_governance.py -q
```

Expected: 文档检查通过，测试通过。

- [ ] **Step 5: 提交历史删除**

```powershell
git add -A docs
git diff --cached --check
git diff --cached --name-status
git commit -m "docs: remove superseded planning history"
```

Expected: 删除范围只包含清单文件和为清理引用所需的活动文档更新，不含 `user_data`。

---

### Task 7: 把文档检查接入统一冒烟

**Files:**
- Modify: `tools/smoke_check.py`
- Modify: `tests/test_smoke_check.py`
- Modify: `tests/test_documentation_governance.py`

**Interfaces:**
- Produces: `run_documentation_check(project_root: Path) -> StepResult`。
- Changes: `run_smoke()` 顺序为文档治理、静态编译、pytest、两库副本幂等。

- [ ] **Step 1: 写冒烟集成失败测试**

在 `tests/test_smoke_check.py` 增加：

```python
def test_documentation_check_maps_issues_to_failed_step(tmp_path: Path) -> None:
    from tools.smoke_check import run_documentation_check

    _write(tmp_path / "README_工作机使用说明.md", "运行 run.bat\n")
    result = run_documentation_check(tmp_path)
    assert not result.ok
    assert result.return_code == 1
    assert any("DOC101" in message for message in result.messages)
```

并把 `test_run_smoke_skip_tests_omits_pytest_step` 的期望步骤改为：

```python
monkeypatch.setattr(
    smoke_check,
    "run_documentation_check",
    lambda project_root: smoke_check.StepResult("文档治理", True, 0, 0.0, ["ok"]),
)

["文档治理", "静态编译", "全量测试", "两库初始化幂等"]
```

- [ ] **Step 2: 运行测试并确认 RED**

```powershell
& $Python -m pytest tests\test_smoke_check.py::test_documentation_check_maps_issues_to_failed_step tests\test_smoke_check.py::test_run_smoke_skip_tests_omits_pytest_step -q
```

Expected: 因 `run_documentation_check` 不存在和步骤列表未更新而失败。

- [ ] **Step 3: 实现冒烟步骤**

在 `tools/smoke_check.py` 增加：

```python
def run_documentation_check(project_root: Path = PROJECT_ROOT) -> StepResult:
    from tools.check_documentation import run_checks

    started = time.perf_counter()
    issues = run_checks(Path(project_root).resolve())
    elapsed = time.perf_counter() - started
    if issues:
        messages = [
            f"[{issue.code}] {issue.path}:{issue.line} {issue.message}"
            for issue in issues
        ]
        return StepResult("文档治理", False, 1, elapsed, messages)
    return StepResult("文档治理", True, 0, elapsed, ["项目文档治理检查通过。"])
```

把 `run_smoke()` 的结果初始化改为：

```python
results = [run_documentation_check(root), run_static_compile(root)]
```

- [ ] **Step 4: 增加真实仓库契约测试**

在 `tests/test_documentation_governance.py` 增加：

```python
def test_repository_documentation_contract() -> None:
    project_root = Path(__file__).resolve().parents[1]
    issues = run_checks(project_root)
    assert not issues, "\n".join(
        f"[{item.code}] {item.path}:{item.line} {item.message}" for item in issues
    )
```

- [ ] **Step 5: 运行聚焦测试并确认 GREEN**

```powershell
& $Python -m pytest tests\test_documentation_governance.py tests\test_smoke_check.py -q
& $Python tools\smoke_check.py --skip-tests
```

Expected: 全部测试通过；快速冒烟显示 `[OK] 文档治理`、静态编译通过、全量测试 SKIP、两库副本幂等通过。

- [ ] **Step 6: 提交冒烟集成**

```powershell
git add tools\smoke_check.py tests\test_smoke_check.py tests\test_documentation_governance.py
git diff --cached --check
git commit -m "test: gate smoke checks on documentation consistency"
```

---

### Task 8: 完整验证、独立复审与治理计划退役

**Files:**
- Delete after all gates pass: `docs/superpowers/specs/2026-07-11-document-governance-design.md`
- Delete after all gates pass: `docs/superpowers/plans/2026-07-11-document-governance-implementation.md`
- Read: all changed files

**Interfaces:**
- Produces: 可交给 integration 的最终治理提交链。
- Produces: 不含当前完成规格/计划的活动文档树。

- [ ] **Step 1: 运行完整聚焦与全量验证**

```powershell
& $Python -m pytest tests\test_documentation_governance.py tests\test_smoke_check.py tests\test_handoff_status.py -q
& $Python tools\smoke_check.py
git diff --check
```

Expected: 聚焦测试全绿；完整 smoke 为 0 failed / 0 unexpected skipped；文档治理、编译和两库副本检查均通过；`git diff --check` 退出 0。

- [ ] **Step 2: 核对真实两库指纹**

```powershell
Get-Item "$RepoRoot\user_data\databases\grading_system.db", "$RepoRoot\user_data\databases\question_bank.db" |
  Select-Object FullName,Length,LastWriteTimeUtc
Get-FileHash -Algorithm SHA256 "$RepoRoot\user_data\databases\grading_system.db", "$RepoRoot\user_data\databases\question_bank.db"
```

Expected: 与 Task 0 Step 5 的大小、UTC 修改时间和 SHA-256 完全一致；任一变化立即停止。

- [ ] **Step 3: 审计提交范围与 worktree**

```powershell
git status --short --branch
git status --short -- user_data
git diff --name-status origin/main...HEAD
git log --oneline --decorate origin/main..HEAD
Get-ChildItem -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } |
  Select-Object FullName,LinkType,Target
```

Expected: `user_data` 无本地项；提交范围只有批准的文档、检查工具和测试；任何 reparse point 指向 worktree 外部时记录并阻止后续 worktree 删除。

- [ ] **Step 4: 请求独立 S-H/S-XH 复审**

复审必须检查：权威职责是否单一、当前业务事实是否丢失、用户操作是否准确、87/8 包口径、Matrix/Phase 模型一致性、自动化 prompt 同步、检查器只读性、删除清单、真实数据守卫和回退。最终要求 0 Critical / 0 Important；所有修复后重跑 Steps 1-3。

- [ ] **Step 5: 删除已完成的本规格和计划**

使用 `apply_patch` 删除：

```text
docs/superpowers/specs/2026-07-11-document-governance-design.md
docs/superpowers/plans/2026-07-11-document-governance-implementation.md
```

删除前确认二者的创建提交和计划提交仍在 `origin/main..HEAD` 历史中，供 PR 审阅和 Git 追溯。

- [ ] **Step 6: 运行最终零引用检查**

```powershell
rg -n "document-governance-design|document-governance-implementation|PLAN_AUDIT_2026-07-10|roadmap-execution-packages-design|fine-grained-knowledge-graph-design|knowledge-practice-operations|code-ownership-map" --glob "*.md" .
& $Python -m pytest tests\test_documentation_governance.py tests\test_smoke_check.py -q
& $Python tools\smoke_check.py --skip-tests
git diff --check
```

Expected: `rg` 零命中；测试和快速冒烟通过。

- [ ] **Step 7: 提交计划退役与最终证据**

```powershell
git add -A docs\superpowers\specs docs\superpowers\plans
git diff --cached --check
git commit -m "docs: retire completed governance planning artifacts"
```

- [ ] **Step 8: 交给 integration 而不直接发布**

交接报告必须包含：提交列表、删除文件数、最终 Markdown 数量、测试数字、完整 smoke 摘要、自动化 prompt 匹配、真实两库指纹、独立复审结果、回退命令和 P1-16/P2-01 并行开工门状态。功能分支不得直接 push `main`；由 integration 一次接收一个提交、复核组合差异、重新运行完整 smoke 后创建 PR。
