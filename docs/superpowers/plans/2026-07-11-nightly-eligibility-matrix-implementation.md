# Nightly Eligibility Matrix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Sol 对 87 个正式执行包的夜间资格审查固化为权威矩阵，并让每天 4:00 的自动化把该矩阵作为领取新包的第一道硬门槛。

**Architecture:** `NIGHTLY_ELIGIBILITY_MATRIX.md` 唯一保存包级资格；Phase 地图和入口文档只链接它。`NIGHTLY_AUTOMATION.md` 保存唯一权威 prompt，Codex automation-2 保留原调度配置并同步该 prompt。

**Tech Stack:** Markdown、PowerShell、Git worktree、Codex Desktop automation、项目自带 Python。

## Global Constraints

- 不修改、删除、暂存或提交任何 `user_data/`。
- 不改变 87 个正式包的目标、依赖、模型分配或当前执行状态。
- 不把 `eligible_after_plan` 解释为自动 `ready`；夜间运行仍须满足即时计划和 worktree 门槛。
- 仓库变更经 integration 分支提交、PR、合并后再同步本地 `main`。

---

### Task 1: 记录设计与审查口径

**Files:**
- Create: `docs/superpowers/specs/2026-07-11-nightly-eligibility-matrix-design.md`
- Create: `docs/superpowers/plans/2026-07-11-nightly-eligibility-matrix-implementation.md`

- [x] **Step 1: 记录用户批准的单一权威矩阵方案**
- [x] **Step 2: 固化 87 正式包与 8 历史记录的边界**
- [x] **Step 3: 自审设计与计划是否完整覆盖用户要求**

### Task 2: 建立 87 包权威矩阵

**Files:**
- Create: `docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md`

- [x] **Step 1: 写入分类定义、共同运行时门槛与阶段统计**
- [x] **Step 2: 逐行写入 87 个正式包的 ID、模型、当前状态、资格、理由与额外门槛**
- [x] **Step 3: 在独立附录写入 P1-01 至 P1-08 历史完成记录**
- [x] **Step 4: 用脚本校验 ID 集合、唯一性和 31/49/7 分类总数**

### Task 3: 接入项目文档入口

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/superpowers/packages/README.md`
- Modify: `docs/superpowers/packages/EXECUTION_INDEX.md`
- Modify: `docs/superpowers/packages/phase-1-execution-packages.md`
- Modify: `docs/superpowers/packages/phase-2-execution-packages.md`
- Modify: `docs/superpowers/packages/phase-3-execution-packages.md`
- Modify: `docs/superpowers/packages/phase-4-execution-packages.md`
- Modify: `docs/superpowers/packages/phase-5-execution-packages.md`

- [x] **Step 1: 在入口文档加入权威矩阵和标记语义**
- [x] **Step 2: 在执行索引加入阶段统计与自动化选择约束**
- [x] **Step 3: 在每个 Phase 地图头部加入单一权威来源链接**
- [x] **Step 4: 确认没有在 Phase 地图复制资格值**

### Task 4: 强化并同步夜间自动化

**Files:**
- Modify: `docs/superpowers/packages/NIGHTLY_AUTOMATION.md`
- External managed config: Codex automation-2

- [x] **Step 1: 让权威 prompt 读取资格矩阵**
- [x] **Step 2: 在候选条件第一项要求 `eligible_after_plan`**
- [x] **Step 3: 使用 automation 工具保留原配置并只同步 prompt**
- [x] **Step 4: 反读并精确比较配置 prompt 与文档标记间文本**

### Task 5: 验证、提交与集成

- [x] **Step 1: 运行矩阵结构校验和 Markdown 链接/关键字段检查**
- [x] **Step 2: 运行 `git diff --check`，确认变更范围不含 `user_data/`**
- [ ] **Step 3: 创建 integration 文档提交并推送**
- [ ] **Step 4: 创建 PR，复核后合并 GitHub `main`**
- [ ] **Step 5: 同步本地 `main`，确认功能 worktree 未被重绑或覆盖**

## Expected Evidence

- `FORMAL_IDS=87`、`ELIGIBLE=31`、`DAYTIME_ONLY=49`、`COMPLETED_NA=7`、`HISTORICAL=8`。
- `PROMPT_MATCH=YES`，automation-2 的名称、频率、模型、推理、项目和启用状态不变。
- `git diff --check` 退出 0，提交文件列表中不存在 `user_data/`。

## Execution Evidence

- 矩阵结构校验：`FORMAL_IDS=87`、`UNIQUE=87`、`SOURCE_FIELDS_MATCH=YES`、`ELIGIBLE=31`、`DAYTIME_ONLY=49`、`COMPLETED_NA=7`、`HISTORICAL=8`。
- 五份 Phase 地图均链接权威矩阵，未复制包级资格值；矩阵标题、状态和执行模型与源地图逐项一致。
- automation-2 反读：`PROMPT_MATCH=YES`，正文 2517 字符；每天 4:00、`gpt-5.6-terra`、`xhigh`、本地项目和 `ACTIVE` 均保持不变。
- 独立 Sol Extra High 最终复审：0 Critical / 0 Important / 0 Minor；旧任务唤醒与新包领取均受矩阵和运行时门槛约束。
- 根目录真实两库的大小、UTC 修改时间和 SHA-256 与任务前基线完全一致。
