# Nightly Single-Package Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已批准的夜间“任务协调器 + 安全执行器”方案写入项目文档，并更新现有每天 4:00 的 Codex 自动化。

**Architecture:** 项目内 `NIGHTLY_AUTOMATION.md` 保存唯一权威提示词和夜间放行协议；`AGENTS.md` 与执行包 README 只保留入口和机器可读计划字段要求。Codex 自动化保留现有调度与模型，只替换 prompt，并通过本机 automation 配置反读验证。

**Tech Stack:** Markdown、Git worktree、Codex Desktop automation、PowerShell、Python 3.12 `tomllib`。

## Global Constraints

- 不修改、删除、暂存或提交根目录 `user_data/`。
- 不直接 push `main`，仓库文档变更通过 integration PR 合并。
- 不改变自动化名称、每天 4:00 频率、GPT-5.6 Terra、极高推理、项目目录或启用状态。
- 夜间自动化不 push、不创建/合并 PR、不创建/删除 worktree、不执行真实数据操作。

---

### Task 1: 固化权威夜间提示词

**Files:**
- Create: `docs/superpowers/packages/NIGHTLY_AUTOMATION.md`
- Modify: `AGENTS.md`
- Modify: `docs/superpowers/packages/README.md`

**Interfaces:**
- Consumes: `EXECUTION_INDEX.md` 状态、并行手册通道、即时计划机器可读字段。
- Produces: 自动化配置可直接使用的唯一 prompt，以及所有后续执行包都能读取的夜间放行规则。

- [x] **Step 1: 写入完整权威提示词和放行协议**

  创建 `NIGHTLY_AUTOMATION.md`，包含任务协调、领取条件、TDD、验证、本地提交、禁止远端操作和交接报告字段。

- [x] **Step 2: 在入口文档加入夜间规则**

  `AGENTS.md` 加入夜间自动化入口；执行包 README 要求夜间计划包含 `执行包`、`规划状态`、`规划模型`、`允许夜间执行`、`计划基线` 五个字段。

- [x] **Step 3: 验证文档一致性**

  Run: `git diff --check`

  Expected: exit 0，无尾随空白或冲突标记。

- [x] **Step 4: 提交仓库文档**

  ```powershell
  git add AGENTS.md docs/superpowers/packages/README.md docs/superpowers/packages/NIGHTLY_AUTOMATION.md docs/superpowers/specs/2026-07-11-nightly-single-package-automation-design.md docs/superpowers/plans/2026-07-11-nightly-automation-implementation.md
  git commit -m "docs: define nightly single-package automation"
  ```

### Task 2: 更新并验证 Codex 自动化

**Files:**
- External managed config: `C:\Users\89418\.codex\automations\automation-2\automation.toml`
- Read: `docs/superpowers/packages/NIGHTLY_AUTOMATION.md`

**Interfaces:**
- Consumes: `NIGHTLY_AUTOMATION.md` 中 `AUTOMATION_PROMPT_START/END` 标记之间的文本。
- Produces: 已启用的“阅卷系统夜间4点推进”自动化 prompt。

- [x] **Step 1: 使用 Codex automation 工具更新现有任务**

  保留现有 name、schedule、model、reasoning effort、execution environment、project、cwd 与 ACTIVE 状态，只替换 prompt。不得手工写 automation TOML。

- [x] **Step 2: 反读配置并校验固定字段**

  使用 `automation_update(mode=view)` 和只读 TOML 解析确认：名称不变、每天 4:00、模型为 `gpt-5.6-terra`、推理为 `xhigh`、状态为 `ACTIVE`、项目路径不变。

- [x] **Step 3: 比较配置 prompt 与权威文档**

  使用 Python `tomllib` 读取 automation TOML，提取 Markdown 标记间文本并进行精确字符串比较。

  Expected: `PROMPT_MATCH=YES`。

- [x] **Step 4: 验证仓库和真实数据守卫**

  Run: `runtime\python\python.exe tools\smoke_check.py --skip-tests`

  Expected: 342 个第一方文件编译通过，两库副本初始化幂等且 `integrity_check=ok`；根目录两库大小、UTC 修改时间与 SHA-256 不变。

## 执行证据

- 权威文档与 automation-2 prompt 精确一致：1944 characters，`PROMPT_MATCH=YES`。
- 名称、每天 4:00、`gpt-5.6-terra`、`xhigh`、本地项目路径和 `ACTIVE` 状态均反读一致。
- `tools/smoke_check.py --skip-tests`：编译 342 个第一方文件；两库副本初始化幂等且 `integrity_check=ok`。
- 真实 `grading_system.db` 与 `question_bank.db` 的大小、UTC 修改时间和 SHA-256 前后完全一致。
