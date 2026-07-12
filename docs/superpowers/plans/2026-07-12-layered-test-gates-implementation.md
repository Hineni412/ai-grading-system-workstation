# Layered Test Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让所有后续执行包默认采用分层测试，并把耗时全量测试收敛到集成波次的最终门禁。

**Architecture:** `AGENTS.md` 和包 README 保存长期原则，`PARALLEL_WORKTREE_EXECUTION.md` 保存可执行的判断规则，Master Plan 只保留目标级约束并服从更新的执行包流程。P1-16 历史证据不改，本次不修改代码、测试或真实数据。

**Tech Stack:** Markdown、Git、PowerShell、ripgrep

## Global Constraints

- 功能分支默认运行当前测试、包内聚焦测试、受影响回归和 `tools/smoke_check.py --skip-tests`，不默认运行全量 pytest。
- integration 逐包运行受影响验证，当前波次全部合入后只运行一次完整 `tools/smoke_check.py`。
- 只有代码 SHA、依赖基线和测试配置相同且其后无实质代码变化时，完整测试证据才可复用。
- 复审、纯文档提交、交接状态更新和主线同步本身不触发全量测试。
- 删除、破坏性迁移、Schema、依赖锁、公共基础设施、核心状态机、冲突后代码修改和测试异常保留额外全量门禁。
- 不修改 P1-16 历史记录，不修改或暂存 `user_data/`，不运行全量 pytest。

---

### Task 1: 更新长期与包级测试原则

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/superpowers/packages/README.md`

**Interfaces:**
- Consumes: `docs/superpowers/specs/2026-07-12-layered-test-gates-design.md` 的七条规则。
- Produces: 后续所有任务首先读取的分层测试默认值和完整测试证据复用约束。

- [x] **Step 1: 在 `AGENTS.md` 的执行包工作原则中加入分层测试原则**

加入以下含义完整的规则：功能分支使用聚焦测试、受影响回归和跳过 pytest 的快速冒烟；完整 smoke 由 integration 在波次结束后运行一次；相同 SHA、基线和配置且无后续实质代码变化时复用证据；复审、文档和同步不单独触发全量；明确列出额外全量例外。

- [x] **Step 2: 更新包 README 的标准工作流**

把现有第 5 步扩展为分层测试门禁，并在并行流程中明确：逐包受影响回归后，波次结束只运行一次完整 smoke。保留删除、迁移和包定义专项门禁。

- [x] **Step 3: 检查长期入口不存在相反指令**

Run:

```powershell
rg -n -S "每次提交.*全量|每个.*包.*全量|每个 WP.*全量|聚焦测试|受影响回归|完整.*smoke|相同.*SHA" AGENTS.md docs\superpowers\packages\README.md
```

Expected: 新分层规则可检索；两份文件中不存在要求普通功能分支每次提交或每个包重复全量测试的表述。

- [x] **Step 4: 提交长期与包级规则**

```powershell
git add -- AGENTS.md docs\superpowers\packages\README.md
git commit -m "docs: define layered test gates"
```

### Task 2: 细化并行集成判断并消除 Master Plan 冲突

**Files:**
- Modify: `docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md`
- Modify: `docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md`

**Interfaces:**
- Consumes: Task 1 产生的长期分层测试原则。
- Produces: 功能分支、逐包集成、波次全量、证据复用和额外全量的唯一可执行判断流程。

- [x] **Step 1: 在并行执行规则中增加“分层测试与证据复用”小节**

小节必须明确：开发阶段按当前测试到包内回归逐层扩大；复审修复按影响面重跑；功能交接使用 `--skip-tests`；逐包集成跑受影响命令；波次结束完整 smoke 一次；相同 SHA、基线、配置的证据可复用；额外全量的七类触发条件；失败时先修复并跑失败/受影响范围，最终候选再通过完整门禁。

- [x] **Step 2: 修正 Master Plan 第 7 节与附录 C**

把“每个 WP 必跑完整 smoke”和“每次提交保持全量测试通过”改为：每个执行包必须保持其聚焦和受影响回归通过，完整 smoke 按当前执行包/integration 规则在波次末运行一次。保留删除类变更的独立全量测试要求，并说明更新的执行包规则优先于早期 WP 表述。

- [x] **Step 3: 验证四份规则的一致性和历史边界**

Run:

```powershell
rg -n -S "每次提交.*全量|每个 WP.*全量|每 WP 必跑|聚焦测试|受影响回归|完整.*smoke|相同.*SHA|删除.*全量" AGENTS.md docs\superpowers\packages\README.md docs\superpowers\packages\PARALLEL_WORKTREE_EXECUTION.md docs\superpowers\plans\2026-07-03-frontend-backend-modernization-master-plan.md
rg -n -S "第二轮修复后全量测试：`1073 passed in 239.92s`" docs\superpowers\plans\2026-07-11-p1-16-question-bank-write-implementation.md
git diff --check
```

Expected: 普通包没有逐提交/逐包重复全量指令；完整 smoke 位于波次末；额外全量例外和删除门禁存在；P1-16 历史行仍存在；`git diff --check` 无输出且退出码为 0。

- [x] **Step 4: 核对提交范围并提交**

Run:

```powershell
git status --short
git diff --stat HEAD
```

Expected: 只包含两份待提交规则文档；不包含代码、测试、数据库或 `user_data/`。

```powershell
git add -- docs\superpowers\packages\PARALLEL_WORKTREE_EXECUTION.md docs\superpowers\plans\2026-07-03-frontend-backend-modernization-master-plan.md
git commit -m "docs: avoid redundant full-suite runs"
```

### Task 3: 最终文档验证

**Files:**
- Verify: `AGENTS.md`
- Verify: `docs/superpowers/packages/README.md`
- Verify: `docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md`
- Verify: `docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md`

**Interfaces:**
- Consumes: Tasks 1–2 的两个文档提交。
- Produces: 可供后续执行包直接遵循的已验证规则变更。

- [x] **Step 1: 执行最终静态验证**

```powershell
git diff origin/main...HEAD --check
git status --short
rg -n -S "T[B]D|T[O]DO|待[定]|占[位]" docs\superpowers\specs\2026-07-12-layered-test-gates-design.md docs\superpowers\plans\2026-07-12-layered-test-gates-implementation.md
```

Expected: diff 检查通过；工作区干净；设计和实施计划没有未完成标记。

- [x] **Step 2: 检查真实数据未进入分支**

```powershell
git diff --name-only origin/main...HEAD | rg "^user_data/"
```

Expected: 无输出，退出码为 1；分支中没有真实数据变化。

- [x] **Step 3: 汇总结果**

记录四份生效文档、完整测试默认次数、例外条件和验证结果。明确本次只改文档，按新规则没有运行全量 pytest。
