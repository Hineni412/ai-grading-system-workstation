# 并行 Worktree 执行手册（2026-07-11）

> **用途：** 给后续 Codex 任务提供并行开发、复核和集成的共同操作说明。
> **适用范围：** 当前 Phase 1 后端余项与 Phase 2 单题复核样板页。
> **进度权威：** 包状态和依赖仍以 `EXECUTION_INDEX.md` 与对应 Phase 执行包地图为准；本文只规定并行分工和集成纪律。
> **数据红线：** 所有 worktree 都不得修改、删除、暂存或提交 `user_data/`。P1-14 已接受的数据例外不构成后续授权。

## 1. 当前 Git 与 Worktree 快照

检查时间：2026-07-11。GitHub 已确认 PR #2 于 2026-07-10 合并到 `main`。

- 本地远端跟踪基线：`origin/main` = `4e8e6aa`（合并提交 `feat(p1): consolidate verified API and job increments`）。
- 初始审计时根目录分支为 `codex/wp1-2-api-routes` = `74f1789`，本地 `main` 为 `42a4b79`；治理完成后根目录必须切换到与 `origin/main` 同步的本地 `main`，旧功能分支普通删除。
- 三个新并行 worktree 均从 `4e8e6aa` 创建、状态干净，并跟踪 `origin/main`。
- 根目录长期承载真实 `user_data/`，即使源码状态干净也不作为并行功能实现区；只用于本地 `main` 同步、日常运行和真实数据人工操作。
- 最新已记录完整基线为 967 passed、编译 342 个第一方文件、两库副本幂等且 `integrity_check=ok`；本文编写过程没有重新运行该完整基线。
- 进度文案已按 PR #2 同步；后续任何 `merged` 状态必须有 GitHub PR 或 `origin/main` 包含关系作为证据，不能只凭本地提交声明。

### 1.1 Worktree 清单与用途

| Worktree | 分支 / HEAD | 状态 | 允许用途 |
|---|---|---|---|
| 仓库根目录 | `main` / 与最新 `origin/main` 同步 | 源码保持干净；真实数据长期脏 | 日常运行、人工数据操作和 main 同步；不分配并行功能包 |
| `.worktrees/p1-16-question-bank-write` | `codex/p1-16-question-bank-write` / `4e8e6aa` | 干净 | 当前后端领域实现：仅 P1-16 |
| `.worktrees/p2-01-frontend-foundation` | `codex/p2-01-frontend-foundation` / `4e8e6aa` | 干净 | 当前前端实现：仅 P2-01 |
| `.worktrees/p1-integration-verification` | `codex/p1-integration-verification` / 基于最新 `main` | 当前集成通道 | 集成、冲突处理、共享文档和完整验证；不实现业务功能 |
| `.worktrees/fine-grained-graph-training` | `codex/fine-grained-graph-training` / `8e089d4` | 干净但历史分支分叉 | 保留历史工作，禁止复用；相对 `origin/main` 14 ahead / 18 behind |
| `.worktrees/grading-paper-skill-workflow` | `codex/import-dialog-direct-tags` / `8be9820` | 已并入 `origin/main` | 本次治理安全移除 worktree 和分支 |
| `.worktrees/resilient-grading` | `codex/resilient-grading` / `24fc3b8` | 干净但历史分支分叉 | 保留历史工作，禁止复用；相对 `origin/main` 9 ahead / 17 behind |

旧 worktree 不得因为“目录空闲”而直接承担新包。是否归档、合并或删除必须另做调用/提交审计，并获得用户确认。

## 2. 默认并行模型

默认最多同时保持三个活跃对话：

1. **后端实现对话**：一次只负责一个 P1 包。
2. **前端实现对话**：一次只负责一个 P2 包。
3. **集成与复核对话**：不写业务功能，只合并、复核、更新共享文档和运行组合验证。

这意味着默认只有两个并行编码通道。若某一波确需三个实现包并行，应暂停集成写入、为第三包新建独立 worktree，并在三个包完成后恢复独立集成波；不得让三个对话同时写同一工作目录。

### 2.1 一包一分支

- 一个功能 worktree 同时只能有一个 `in_progress` 执行包。
- 包完成并集成后，下一包从最新集成提交创建新分支；不要在旧包分支上连续堆叠多包。
- 同一路线可以复用物理 worktree 目录，但必须先移除旧 worktree/分支绑定，再从最新基线创建新分支；不得用 reset 覆盖未集成工作。
- 每个包仍建议使用独立对话；同一路线不是一个长期不关闭的超长对话。

## 3. 当前第一波固定分工

### 3.1 后端领域 Worktree：P1-16

路径：`.worktrees/p1-16-question-bank-write`
分支：`codex/p1-16-question-bank-write`

只执行 P1-16 Question Bank 轻写与导入准备：

- 教师确认标签、允许的题目元数据删除/恢复和导入请求准备。
- 继续复用 P1-15 的快照读取、受控媒体 URL 和脱敏契约。
- 使用临时题库、上传目录和失败回滚测试。
- 不直接执行耗时导入，不调用 AI，不恢复旧技能写入，不接手 P1-18。

建议主要所有权：

- `question_bank/services/` 中与精确写入、源文件暂存和恢复直接相关的服务。
- `backend/api/routers/question_bank.py`、`backend/api/schemas/question_bank.py` 的 P1-16 增量。
- P1-16 专属测试与即时实现计划。

### 3.2 前端 Worktree：P2-01

路径：`.worktrees/p2-01-frontend-foundation`
分支：`codex/p2-01-frontend-foundation`

只执行 P2-01 前端工程、依赖锁与质量命令：

- 创建 Vue 3 + TypeScript + Vite 工程，锁定 Element Plus、Pinia、Vue Router、ECharts、测试和 Playwright 依赖。
- 建立 dev/build/lint/typecheck/unit/e2e 命令和 `/api` 开发代理。
- 验证空应用构建及 `dist` 发布边界。
- 不开始页面视觉、不修改默认启动入口、不实现 P2-02/P2-03/P2-04。

建议主要所有权：

- 新增的 `frontend/` 目录、锁文件和 P2-01 专属测试/计划。
- 只有在验收确实需要时，才最小修改发布清单；`运行.bat` 留给 P2-21。

### 3.3 集成 Worktree：只做集成

路径：`.worktrees/p1-integration-verification`
分支：`codex/p1-integration-verification`

职责：

- 维护本文和共享进度文档。
- 逐个接收已完成、已复核的包提交，不接受半成品工作树。
- 解决共享入口和共享文档冲突。
- 每合入一个包先跑该包聚焦回归；一波全部合入后跑 API/前端组合验证和完整 smoke。
- 检查 staged/commit 范围，确保无 `user_data/`、临时数据库、构建缓存、截图噪声或本机密钥。
- 生成面向用户的非技术摘要；门槛通过后按 standing workflow push integration、创建/合并 PR 和同步本地 `main`，不直接 push `main`。

禁止事项：

- 不在集成分支顺手修业务 bug。
- 不代替功能分支补测试后直接合入；发现问题应退回原包修复。
- 不把旧历史 worktree 的提交顺便带入当前波次。

## 4. 后续任务池与依赖

### 4.1 后端领域路线

| 包 | 可开始条件 | 分配原则 |
|---|---|---|
| P1-16 | 当前可开始 | 当前 `p1-16-question-bank-write` 独占 |
| P1-19 | P1-15 已验证；建议等 P1-16 集成后使用最新题库契约 | 新分支，训练 API 与 P1-16 不同包 |
| P1-20 | P1-19 已验证 | 与 P1-21 可以拆成两个 worktree，但都必须基于同一份已集成 P1-19 |
| P1-21 | P1-19 已验证 | 只读 graph API；不得提前加入 Phase 4 relation schema |
| P1-18 | P1-16 与 P1-24 均已验证 | 单独高风险包；会同时触及题库、Job 和 LLM 边界，不与 P1-20/P1-25 并行修改共享模块 |

### 4.2 后端基础设施路线

| 包 | 可开始条件 | 分配原则 |
|---|---|---|
| P1-24 | P1-14 已验证 | 下一批优先基础设施包；单独 worktree，Sol 高风险实施/复核 |
| P1-25 | P1-24 已验证 | 四条调用链逐条迁移；不得与 P1-18 并行修改 AI tagging 链 |
| P1-17 | P1-11/P1-14 已验证 | 可穿插，但若 P1-24 已开工，优先等 Gateway 契约稳定后再接配置生成 Job |
| P1-22 | P1-14 已验证 | 只读 ops，可与题库或前端包并行；不得执行真实备份/迁移 |
| P1-23 | P1-22 已验证 | 最高数据风险包，必须单独 worktree、临时数据根和独立 Sol 复核 |
| P1-26 | P1-15/P1-19/P1-21/P1-22 已验证 | 只测量不优化；生成可重复基线 |
| P1-27 | P1-26 证明收益 | 串行执行；没有收益则不实施 |
| P1-28 | P1-17/P1-20 和核心领域 API 完成 | 阶段 E2E，停止并行功能扩散 |
| P1-29 | P1-15 至 P1-28 全部 verified | Phase 1 总门槛，单独执行 |

### 4.3 前端样板路线

| 包 | 可开始条件 | 分配原则 |
|---|---|---|
| P2-01 | 当前可开始 | 当前 `p2-01-frontend-foundation` 独占 |
| P2-02 / P2-04 | P2-01 已集成 | 可以拆成两个新 worktree 并行：主题控件与 API client 文件所有权分离 |
| P2-03 | P2-02 已验证 | App Shell；可等待 P2-04 一并集成，减少 store/client 返工 |
| P2-05 | P2-03/P2-04 与 P1-12 已验证 | 复核队列切片 |
| P2-06 | P2-05 与 P1-13 已验证 | 答卷证据查看器，必须浏览器和像素检查 |
| P2-07 | P2-05/P2-06 已验证 | 评分写入高风险；保留 Streamlit 回退 |
| P2-08 | P2-05 至 P2-07 已验证 | 样板页总门槛，单独集成并由用户做早期视觉/流程确认 |

P2-09 及以后复杂页面仍受 Phase 1 总门槛约束，不得因为 P2-01 至 P2-08 并行启动而提前扩散。

## 5. 推荐波次

| 波次 | 实现 Worktree A | 实现 Worktree B | 集成 Worktree |
|---|---|---|---|
| 当前 Wave 1 | P1-16 | P2-01 | 只读跟踪，完成后逐个合入并跑完整门槛 |
| Wave 2 | P2-02 | P2-04 | 先形成前端设计系统与 API client 共同基线 |
| Wave 3 | P1-24 | P2-03 | 合入后验证 Gateway 与前端 client 无契约漂移 |
| Wave 4 | P1-17 | P2-05 | 配置 Job 与复核只读切片并行 |
| Wave 5 | P1-19 | P2-06 | Training API 与媒体查看器并行 |
| Wave 6 | P1-22 | P2-07 | Ops 只读与评分检查器并行，分别独立复核 |
| 样板门 | 暂停新增并行包 | P2-08 | 组合回归、浏览器多视口和用户验收 |

该表是推荐调度，不覆盖执行包依赖。每一波开始前必须重新读取 `EXECUTION_INDEX.md`；若上一波未集成、接口发生变化或出现高风险缺陷，应暂停排队而不是机械启动下一波。

## 6. 共享文件与冲突所有权

| 共享范围 | 规则 |
|---|---|
| `AGENTS.md`、`ARCHITECTURE.md`、`EXECUTION_INDEX.md` | 功能分支记录包专属事实即可；最终状态、数量和下一动作由集成 worktree 统一整理 |
| `backend/api/app.py`、router/schema `__init__.py` | 功能分支只做最小注册；冲突由集成 worktree 按两包契约同时保留 |
| OpenAPI/全局测试清单 | 包内可新增契约测试；全局快照和组合数字由集成 worktree 更新 |
| `backend/jobs/default_handlers.py`、Job schemas | P1-17/P1-18/P1-20 之间默认串行；不得在不同 worktree 同时重排注册逻辑 |
| `llm_client.py`、`backend/llm/`、AI tagging 调用链 | P1-24/P1-25/P1-18 默认串行或明确拆分文件所有权后再并行 |
| `question_bank/services/` | P1-16/P1-18/P1-19 开工前重新检查重叠文件；重叠即串行 |
| `requirements*`、前端锁文件、发布清单 | P2-01 独占前端依赖与锁文件；后续升级必须单独包处理 |
| 数据库迁移 | 永不并行创建相邻迁移号；必须由集成 worktree 核对顺序和副本预演 |

## 7. 每个功能 Worktree 的完成门槛

功能包只有同时满足以下条件才可交给集成 worktree：

1. 已读取当前 worktree 的 `AGENTS.md`、Phase 文档和包即时实现计划。
2. 工作树起点干净，且依赖包已经进入共同基线。
3. 新行为有 RED → GREEN 证据；没有为了通过测试改写业务口径。
4. 包内聚焦测试、相关 API/前端测试和 `smoke_check.py --skip-tests` 通过。
5. 高风险包完成指定模型的独立复核，阻塞 findings 为零。
6. `git diff --check` 通过；提交范围不含 `user_data/`、`.superpowers/`、缓存、临时 DB、真实导出或密钥。
7. 交付说明包含修改文件、测试数字、已知风险、回退方式和建议合并顺序。
8. 用户已明确启动该执行包时，功能分支可以提交本地包结果；push、PR 和主线合并统一由 integration 流程执行，功能分支不得直接 push `main`。

## 8. 集成顺序与验证

1. 集成 worktree 从最新共同基线开始，确认自身除集成文档外无未知改动。
2. 一次只接收一个功能包提交；先合入依赖更基础、共享面更小的包。
3. 每次合入立即运行该包聚焦回归和受影响的 API/前端命令。
4. 两个实现包均合入后运行完整 `tools/smoke_check.py`；前端存在后还要运行 lint、typecheck、unit 和 build。
5. 比较完整验证前后的真实两库大小、修改时间和 SHA-256；任何变化都视为阻塞，除非用户对本次操作另行明确授权。
6. 最后统一更新 `AGENTS.md`、`ARCHITECTURE.md`、Index、包状态和实际测试数字。
7. 只有集成结果通过后，下一波 worktree 才能从该提交创建。

当前各 linked worktree 不包含根目录被忽略的便携 `runtime/`。后端验证应在目标 worktree 作为工作目录时，调用仓库根目录的 `runtime\python\python.exe`；不要复制 runtime 到每个 worktree。P2-01 不假设系统 Node，开工时先调用 Codex workspace dependencies 探测 Node/pnpm。2026-07-11 当前可用的 Codex Node 位于 `C:\Users\89418\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe`，但后续任务必须重新探测，不能把该绝对路径写入项目配置。

## 9. 后续对话启动文本

### P1-16 实现对话

```text
在 C:\Users\89418\Desktop\AI阅卷系统_工作机版_v1.5.0\.worktrees\p1-16-question-bank-write 中只执行 P1-16。先读取 AGENTS.md、ARCHITECTURE.md、packages README/Index、Phase 1 包定义、P1-15 现有契约，以及 C:\Users\89418\Desktop\AI阅卷系统_工作机版_v1.5.0\.worktrees\p1-integration-verification\docs\superpowers\packages\PARALLEL_WORKTREE_EXECUTION_2026-07-11.md，重新生成即时实现计划并按 TDD 实施。不得访问或修改真实 user_data，不得扩散到 P1-18；完成门槛后提交功能分支并交给 integration，不直接 push `main`。
```

### P2-01 实现对话

```text
在 C:\Users\89418\Desktop\AI阅卷系统_工作机版_v1.5.0\.worktrees\p2-01-frontend-foundation 中只执行 P2-01。先读取 AGENTS.md、ARCHITECTURE.md、packages README/Index、Phase 2 包定义、docs/ui/STYLE.md，以及 C:\Users\89418\Desktop\AI阅卷系统_工作机版_v1.5.0\.worktrees\p1-integration-verification\docs\superpowers\packages\PARALLEL_WORKTREE_EXECUTION_2026-07-11.md；使用 Codex workspace dependencies 探测 Node/pnpm，生成即时实现计划后建立前端工程、锁文件和质量命令。不得开始页面视觉、P2-02/P2-04 或修改默认启动入口；完成门槛后提交功能分支并交给 integration，不直接 push `main`。
```

### 集成对话

```text
在 C:\Users\89418\Desktop\AI阅卷系统_工作机版_v1.5.0\.worktrees\p1-integration-verification 中只做当前波次集成。读取并行 Worktree 执行手册和两个功能包报告，逐个审查/合入，解决共享入口与文档冲突，运行聚焦回归、前端质量命令和完整 smoke，核对真实 user_data 指纹不变。不要新增业务功能；门槛通过后按 standing workflow push integration、创建/合并 PR 并同步本地 main，禁止直接 push main。
```

## 10. 必须暂停并回到集成判断的情况

- 功能 worktree 发现依赖包尚未进入共同基线。
- 两个实现包需要同时修改同一业务服务、迁移或状态机。
- 测试需要真实 `user_data/`、真实模型密钥或不可逆文件操作。
- API 字段、评分规则、标签语义或状态含义与包定义不一致。
- 同一包连续两次修复失败，或独立复核仍有 Critical/Important。
- 合入后完整 smoke、前端 build 或数据库指纹守卫失败。
- P2-08 样板页尚未获得用户确认，却准备扩散其他复杂页面。

出现上述任一项时，不得让并行速度优先于可回退性和数据安全。

## 11. Main 同步与分支清理标准流程

每一波并行开发结束后固定执行：

1. 两个功能分支分别完成提交、聚焦测试、快速冒烟和独立复核。
2. integration worktree 从最新 `origin/main` 更新后，一次合入一个功能分支；每次合入立即运行受影响回归。
3. 全部包进入 integration 后运行完整 smoke、前端质量命令（若适用）和真实两库指纹守卫。
4. 推送 integration 分支并创建 PR，目标固定为 GitHub `main`；等待检查通过后合并 PR。
5. `git fetch --prune` 后，把本地 `main` fast-forward 到 `origin/main`；根目录保持检出本地 `main`。
6. 活动功能 worktree 更新到新主线，或在旧包合并后删除并从新主线创建下一包分支。
7. 使用 `git branch --merged origin/main` 审计候选，只删除已被主线包含、工作树干净且不再承担活动任务的本地/远端分支。
8. `git branch --no-merged origin/main` 中的分支视为仍有独有历史：默认保留，不得用 `-D` 或远端强删；是否归档/丢弃必须单独获得用户明确确认。
9. linked worktree 只能通过 `git worktree remove <verified-path>` 从仓库根目录移除，随后运行 `git worktree prune`；禁止资源管理器或递归删除命令直接删除目录。
10. 清理前后比较 `user_data` 状态行数和两库大小、修改时间、SHA-256；任何变化都立即停止。

本流程是后续所有执行包的默认完成动作，不需要用户逐包重复提醒。用户启动执行包即授权标准功能提交和 integration PR 流程；直接 push `main`、force push、真实数据操作和未合并历史分支丢弃仍需单独明确授权。
