# Phase 2 前端业务基线与 UX 重设计权威同步 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已批准的“业务能力与结果等价、UX 主动重设计”边界同步到全部当前权威文档，并在不干扰 P1-27、不改前端源码和真实数据的前提下安全进入最新主线。

**Architecture:** 以 `docs/superpowers/specs/2026-07-14-phase-2-frontend-realignment-design.md` 为唯一批准规格，分成长期协作规则、Phase 2 稳定边界、当前实现事实、用户验收和动态 Index 五层同步。文档功能分支只形成可复审提交；最终由从最新 `origin/main` 创建的专用 integration 分支接收，动态状态文件始终以主线事实为底再叠加门槛内容。

**Tech Stack:** Markdown、Git worktree/integration 流程、PowerShell、`tools/check_documentation.py`、`pytest` 文档治理测试。

## Global Constraints

- 业务事实来源只包括现有 Streamlit 行为、服务实现、数据库契约、测试，以及带日期和范围的用户明确决定。
- 必须继承的是用户任务、业务能力、数据语义、业务结果、状态语义、权限与安全边界、持久化、危险操作保护、失败恢复、幂等和审计；旧页面结构、控件位置和点击顺序不是业务事实。
- P2-03 至 P2-08 已完成 Vue 页面只能作为待核验能力清单、审计输入和复用候选，不能单独证明业务事实。
- 新 Vue SPA 可主动重组导航、合并或拆分页面、改变布局、控件形式与位置、呈现顺序和操作步骤；前提是业务能力可达、结果与数据语义等价、安全边界不降低。
- 组件复用只是实现偏好。若旧组件妨碍信息架构、清晰度、可访问性、上下文连续性、错误恢复或批量效率，可以有理由地重构或替换。
- 只有新增或删除业务能力，或改变数据含义、业务结果、状态、权限、安全、持久化和危险操作保护时，才需要用户专项决定；纯 UX 调整记录理由并接受设计复审、浏览器验证和最终用户验收。
- `docs/ui/STYLE.md` 与 `docs/ui/references/README.md` 只负责视觉属性；不得重新加入固定导航、页面、字段、动作、状态、快捷键或流程。
- P2-08 保持 `merged`，历史规格、即时计划和验收证据不改写；Phase 2 仍为 22 个正式包，全局仍为 87 个正式包。
- 来源重校准门槛仍是一次性、非编号、白天人工门槛，不进入夜间资格矩阵，不使用正式包交接块。
- 不修改前端或后端源码、测试代码、数据库、Schema、评分规则、模型配置或真实 `user_data/`；不读取或打开真实数据库。
- 当前功能分支只提交文档；不直接 push `main`，不 force push，不丢弃未合并历史。

---

## File Map

**Create:**

- `docs/superpowers/plans/2026-07-14-phase-2-frontend-ux-authority-sync-implementation.md`：本实施计划。

**Modify:**

- `AGENTS.md`：长期业务事实、UX 重设计和用户决定边界。
- `docs/superpowers/packages/README.md`：所有可见前端包共用的七列溯源表及复审规则。
- `docs/ui/STYLE.md`：仅把“功能溯源表”术语校正为“业务能力与 UX 设计溯源表”，视觉正文与 Token 不变。
- `docs/superpowers/packages/phase-2-execution-packages.md`：Phase 2 稳定业务/UX 权威、门槛和验收边界。
- `docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md`：长期迁移策略、风险、验证和用户决定。
- `docs/superpowers/plans/2026-07-14-phase-2-frontend-source-reset-documentation.md`：纠正下一阶段入口中“最小代码修改优先”的旧倾向。
- `ARCHITECTURE.md`：当前已实现事实、P2-03 至 P2-08 身份、风险和用户决定。
- `frontend/README.md`：把现有 Vue 外壳和页面明确为待核验原型，同时说明允许主动重设计。
- `docs/user-testing/PHASE2_FRONTEND_RECALIBRATION_TEST_TEMPLATE.md`：同时验收业务等价与 UX 改善，不按旧屏幕或旧点击序列判定。
- `docs/user-testing/README.md`：向用户解释该门槛的验收目标。
- `docs/superpowers/packages/EXECUTION_INDEX.md`：只登记当前门槛事实、阻断与下一动作；集成时保留最新主线的全部 P1 动态状态。
- `docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md`：把白天停机证明标准补齐为“Index 与验收证据均在最新 `origin/main`”。

**Read and validate only:**

- `docs/superpowers/specs/2026-07-14-phase-2-frontend-realignment-design.md`：已批准规格，不在实施时重新改写。
- `docs/ui/references/README.md`：当前已符合局部纯视觉参考边界。
- `docs/superpowers/packages/NIGHTLY_AUTOMATION.md`：当前仓库提示词已包含 P2-09 至 P2-22 硬停机，只核对不重复改写。
- `docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md`：保留 87/22 和所有资格行；只允许既有“不得复制动态状态”的文字清理，不改变任何资格。
- 历史 P2-02 至 P2-08 规格、即时计划和验收记录：全部保持原样。

---

### Task 0: 锚定已批准的实施计划

**Files:**

- Create: `docs/superpowers/plans/2026-07-14-phase-2-frontend-ux-authority-sync-implementation.md`

**Interfaces:**

- Consumes: 已批准规格和两组只读文档审计结论。
- Produces: Task 1 至 Task 6 及最终 integration 都能读取的已提交计划。

- [x] **Step 1: 保存并完整自检本计划**

已逐节核对批准规格，扫描占位符与内部绝对路径，并验证计划中的正式包计数命令实际输出 87/22。

- [x] **Step 2: 只暂存本计划并检查差异**

```powershell
git add -- docs/superpowers/plans/2026-07-14-phase-2-frontend-ux-authority-sync-implementation.md
git diff --cached --name-only
git diff --cached --check
```

Expected: 暂存区只有本计划，差异检查无输出。

- [x] **Step 3: 创建计划专用提交**

```powershell
git commit -m "docs: plan frontend UX authority sync"
```

Expected: Task 1 开始前，计划已位于 `codex/p2-frontend-realignment-design` 的独立提交中；Task 6 的 `$remaining` 提交列表会包含它。

---

### Task 1: 同步长期协作规则与溯源模型

**Files:**

- Modify: `AGENTS.md:8-29`
- Modify: `docs/superpowers/packages/README.md:33-40`
- Modify: `docs/ui/STYLE.md:36-47`

**Interfaces:**

- Consumes: 已批准规格第 3.1 至 3.4 节。
- Produces: 后续 Phase map、Master Plan、即时计划和验收统一使用的业务/UX 词汇与七列表。

- [ ] **Step 1: 改写 AGENTS 的业务事实边界**

将 `AGENTS.md` 第 10 行和第 29 行所在规则整理为以下三层含义，正文必须明确出现这些句子：

```markdown
- 迁移期间，以现有 Streamlit 行为、服务实现、数据库契约和测试为业务事实来源；它们约束用户任务、业务能力、数据语义、业务结果、状态、权限、安全、持久化与失败恢复，不要求新前端复刻旧页面结构或点击顺序。

可见前端必须先建立“业务能力与 UX 设计溯源表”。P2-03 至 P2-08 已实现 Vue 行为只能作为审计输入和复用候选；只有回溯到业务事实来源或用户明确决定的部分才能进入业务基线。

在业务能力可达、数据语义和结果等价、安全边界不降低的前提下，新 Vue SPA 可以主动重组导航、合并或拆分页面、调整布局、控件、呈现顺序和操作步骤。无来源的业务能力、数据语义或业务结果不得实现；服务于已有能力的新布局和控件不要求旧 UI 存在同形元素。
```

保留 `STYLE.md` 纯视觉、参考图无功能权威、门槛未通过不得扩散 P2-09 及复杂页面的现有规则。

- [ ] **Step 2: 用批准的七列表替换 packages README 旧溯源说明**

在 `docs/superpowers/packages/README.md` 的“源码级实现计划”中使用以下表头，并把用户决定阈值写在表后：

```markdown
| 用户任务或业务能力 | 现有业务来源 | 必须保持的语义、结果与安全边界 | 新 Vue 呈现与操作设计 | UX 调整理由 | 业务差异及用户决定 | 验证方式 |
|---|---|---|---|---|---|---|
| 开工时逐项填写 | Streamlit、服务、数据库契约或测试 | 明确不可丢失或改义的内容 | 允许重新设计 | 说明效率、清晰度或可访问性依据 | 仅业务边界变化时必填 | 自动检查、浏览器证据与用户验收 |
```

表后必须说明：纯 UX 差异不需要逐控件专项批准；改变业务能力、数据语义、结果、状态、权限、安全、持久化或危险操作保护时才填写带日期和范围的用户决定。

- [ ] **Step 3: 只同步 STYLE 中的溯源表术语**

将 `docs/ui/STYLE.md` 第 42 行的“当前页面经批准的功能溯源表”改为“当前页面经批准的业务能力与 UX 设计溯源表”。不得改动视觉 Token、色值、字体、间距、密度、控件外观、视口或视觉 QA 规则。

- [ ] **Step 4: 检查长期规则没有再次限制 UX**

Run:

```powershell
rg -n --encoding utf-8 "业务能力与 UX 设计溯源表|不要求新前端复刻|主动重组导航|无来源的业务能力" AGENTS.md docs/superpowers/packages/README.md docs/ui/STYLE.md
rg -n --encoding utf-8 "前端交互与业务行为必须逐项追溯|没有来源的功能元素不得实现" AGENTS.md docs/superpowers/packages/README.md
```

Expected: 第一条在相应权威文件命中；第二条零命中。

- [ ] **Step 5: 提交长期规则同步**

```powershell
git add -- AGENTS.md docs/superpowers/packages/README.md docs/ui/STYLE.md
git diff --cached --check
git commit -m "docs: separate frontend business facts from UX design"
```

Expected: 提交只包含上述三个文件。

---

### Task 2: 同步 Phase 2 稳定边界与 Master Plan

**Files:**

- Modify: `docs/superpowers/packages/phase-2-execution-packages.md:3-108`
- Modify: `docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md:59-215`
- Modify: `docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md:304-320`
- Modify: `docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md:352-356`
- Modify: `docs/superpowers/plans/2026-07-14-phase-2-frontend-source-reset-documentation.md:59-69`

**Interfaces:**

- Consumes: Task 1 的业务/UX 词汇与七列表。
- Produces: 所有后续 Phase 2 包必须遵守的稳定设计边界和下一阶段入口。

- [ ] **Step 1: 在 Phase 2 map 顶部拆开三类权威**

将顶部“业务权威”改为业务任务、能力、数据、结果、状态和安全边界；新增“UX 设计”规则，明确允许重新组织导航、页面、布局、控件和步骤；保留“视觉权威”只属于 `STYLE.md`。

P2-03 与 P2-08 的“来源重校准说明”必须同时说明：

- P2-03 至 P2-08 是待核验能力清单和复用候选，不是业务事实来源；
- 复用只是偏好，不能高于已批准的 UX 重设计；
- 可以因信息架构、清晰度、可访问性、上下文连续性、错误恢复或批量效率而重构或替换。

- [ ] **Step 2: 升级 Phase 2 门槛的溯源和验收口径**

在非正式门槛中使用 Task 1 的七列表字段。把“按题号批量复核”写成必须可达的默认业务能力，不固定 Streamlit 的布局和点击顺序；把“单份三栏页”改为“单份详情能力，现有三栏仅是可复用原型”。

验收必须同时检查：

1. 业务能力、数据语义、结果和安全边界没有丢失；
2. 新信息架构和操作比旧原型更清楚、高效且可恢复；
3. 不按逐屏、逐控件或逐点击一致判定；
4. 只有业务边界变化才要求专项用户决定。

- [ ] **Step 3: 替换 Master Plan 的旧溯源表和旧 UI 对照口径**

在 `设计依据`、`信息架构与功能溯源`、`WP2.2`、`WP2.3`、`风险与回退`、`验证策略` 和附录 C 中统一使用“业务能力与 UX 设计溯源表”。

必须完成以下具体替换：

- `信息架构、业务状态、字段、操作、快捷键和流程只以旧来源为依据` → 业务任务、数据、动作结果、状态和安全来自业务事实；信息架构与呈现操作允许重设计。
- Master Plan 的六列旧表 → Task 1 的七列表。
- `无来源推断必须删除` → `无来源的业务能力、数据语义或业务结果不得实现`。
- P2-03、P2-04、P2-05 至 P2-08 全部标为审计输入和复用候选，补齐当前遗漏的 P2-04。
- `以现有 UI 行为为准` → `以业务能力、结果、数据语义和安全边界等价为准，不要求屏幕或点击等同`。
- `单份三栏页作为可选详情` → `单份详情能力作为可选入口，现有三栏只是一种原型`。
- 风险缓解从“行为对照清单”改为“业务能力与 UX 设计溯源表”。

- [ ] **Step 4: 修正已完成文档重置计划的下一阶段入口**

在 `docs/superpowers/plans/2026-07-14-phase-2-frontend-source-reset-documentation.md` 的“下一阶段入口”中：

- `逐项功能溯源表` 改为 `业务能力与 UX 设计溯源表`；
- `组件复用和最小代码修改范围` 改为 `组件复用、重构或替换的理由与范围；复用不得凌驾于已批准的 UX 重设计`；
- 保留 TDD、五视口、专用验收清单和不改评分/API/Schema/真实数据的边界。

- [ ] **Step 5: 检查稳定计划中的旧口径已清零**

Run:

```powershell
rg -n --encoding utf-8 "待实现元素或行为|有意差异与用户确认|以现有 UI 行为为准|行为对照清单|组件复用和最小代码修改范围|单份三栏页作为可选详情" docs/superpowers/packages/phase-2-execution-packages.md docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md docs/superpowers/plans/2026-07-14-phase-2-frontend-source-reset-documentation.md
rg -n --encoding utf-8 "业务能力与 UX 设计溯源表|UX 主动重设计|不要求逐屏|复用不得凌驾" docs/superpowers/packages/phase-2-execution-packages.md docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md docs/superpowers/plans/2026-07-14-phase-2-frontend-source-reset-documentation.md
```

Expected: 第一条零命中；第二条在 Phase map、Master Plan 和下一阶段入口均有命中。

- [ ] **Step 6: 提交 Phase 2 稳定边界同步**

```powershell
git add -- docs/superpowers/packages/phase-2-execution-packages.md docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md docs/superpowers/plans/2026-07-14-phase-2-frontend-source-reset-documentation.md
git diff --cached --check
git commit -m "docs: authorize Phase 2 UX redesign"
```

Expected: 提交只包含上述三个文件。

---

### Task 3: 同步当前架构事实与 Vue 工程说明

**Files:**

- Modify: `ARCHITECTURE.md:14-24`
- Modify: `ARCHITECTURE.md:528-556`
- Modify: `frontend/README.md:1-43`

**Interfaces:**

- Consumes: Task 2 的稳定 Phase 2 边界。
- Produces: 不把已完成 Vue 原型误读为业务权威的当前事实说明。

- [ ] **Step 1: 更新 ARCHITECTURE 的来源重校准事实**

保留 P2-03 至 P2-08 每个已实现增量的客观事实，不删除已通过的快捷键、验收服务或视口证据。重写“Phase 2 前端来源重校准边界”、风险表和用户决定第 7 行，明确：

- Streamlit、服务、数据库契约和测试提供业务能力与结果基线；
- P2-03 至 P2-08 Vue 只提供待核验清单与复用原型；
- 旧 Streamlit 和既有 Vue 都不是逐屏、逐控件、逐点击模板；
- 组件复用只是偏好，合理 UX 理由可以支持重构或替换；
- 按题号批量复核是默认业务能力，但新页面布局和操作顺序由任务效率重新设计。

- [ ] **Step 2: 更新 frontend README 的原型身份**

在开头与“路由与 App Shell”说明中覆盖 P2-03 至 P2-08，而不是只突出 P2-03。保留当前路由、三栏外壳和 1024px 支持范围作为“当前实现事实”，同时明确：

```markdown
这些实现只能作为待核验能力清单、审计输入和复用候选，不能单独证明业务规则。后续页面先追溯业务任务、数据语义、结果和安全边界，再主动设计导航、页面拆分、布局、控件与操作步骤；不要求复制当前外壳或旧 Streamlit。
```

- [ ] **Step 3: 核对事实与设计权威没有混写**

Run:

```powershell
rg -n --encoding utf-8 "待核验能力清单|复用只是实现偏好|不要求复制当前外壳|主动设计导航" ARCHITECTURE.md frontend/README.md
rg -n --encoding utf-8 "导航和交互必须重新追溯现有生产功能" frontend/README.md
```

Expected: 第一条在两个文件命中；第二条零命中。

- [ ] **Step 4: 提交当前事实同步**

```powershell
git add -- ARCHITECTURE.md frontend/README.md
git diff --cached --check
git commit -m "docs: clarify existing Vue pages are prototypes"
```

Expected: 提交只包含 `ARCHITECTURE.md` 和 `frontend/README.md`。

---

### Task 4: 同步验收、动态门槛与并行停机证明

**Files:**

- Modify: `docs/user-testing/PHASE2_FRONTEND_RECALIBRATION_TEST_TEMPLATE.md:1-77`
- Modify: `docs/user-testing/README.md:18-25`
- Modify: `docs/superpowers/packages/EXECUTION_INDEX.md:8-97`
- Modify: `docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md:82-93`

**Interfaces:**

- Consumes: Tasks 1-3 的业务等价和 UX 重设计口径。
- Produces: 可观察的验收标准、当前门槛状态和白天/夜间一致停机证明。

- [ ] **Step 1: 将专用验收模板改为双目标验收**

保留现有 SHA、数据来源、启动/关闭方式、真实两库指纹、Blocker/Major 和 `NON_PACKAGE_GATE_ACCEPTANCE` 结果块。将步骤覆盖要求改为：

```markdown
- 当前导航和入口覆盖已追溯的真实业务任务，但不要求与旧导航一一对应；
- 按题号批量比较和复核是默认可达的业务能力，新布局和操作顺序不要求复刻 Streamlit；
- 单份详情是可选深查能力，现有三栏只是一种原型，并能安全返回批量上下文；
- 必须保持的数据语义、评分结果、安全确认和失败恢复与批准的溯源表一致；
- 新信息架构、布局、控件和操作步骤比旧原型更清楚、高效且容易恢复；
- 不从完整概念图或既有 Vue 原型带入无来源的业务能力、数据语义或业务结果；
- 验收不按逐屏、逐控件或逐点击一致判定。
```

在结果记录中增加 `业务等价结论` 和 `UX 改善结论`，两项均未明确通过时，用户明确结论不得写为 `passed`。

- [ ] **Step 2: 更新用户测试总则**

在 Phase 2 来源重校准门槛的测试节奏中说明：该门槛同时确认“业务能力、结果和安全边界未丢失”以及“新设计更清楚、高效、可恢复”，不要求用户对照旧页面逐屏或逐点击判断。

- [ ] **Step 3: 更新 Index 的当前门槛措辞但不篡改正式包状态**

在当前功能分支中只改 Phase 2 和门槛相关文字：

- Phase 2 保持 8 merged / 14 pending，P2-08 保持 `merged`；
- 门槛结果写为“规格已获用户批准；文档权威同步中，业务能力盘点与前端修正待执行”；
- P2-09、P2-13、P2-14、P2-16、P2-19 及后续依赖继续阻断；
- 下一动作改为建立“业务能力与 UX 设计溯源表”，先设计按题号批量复核能力和可选详情，再另写前端修正即时计划；
- 明确新设计不复刻旧页面，组件可以复用、重构或替换。

不得在功能分支把门槛写为 `passed`，不得改变 87/22 计数或 P1 正式包结论。

- [ ] **Step 4: 补齐并行手册的主线证明标准**

将“门槛未通过则暂停”补为：最新 `origin/main` 的 `EXECUTION_INDEX.md` 必须明确记录门槛通过，且匹配的版本化用户验收证据必须已经进入同一共同基线；任一缺失都不得开始 P2-09 至 P2-22。

- [ ] **Step 5: 核对夜间与白天守卫一致**

Run:

```powershell
rg -n --encoding utf-8 "最新 .*origin/main|版本化用户验收证据|P2-09 至 P2-22|report_only" docs/superpowers/packages/NIGHTLY_AUTOMATION.md docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md
rg -n --encoding utf-8 "规格已获用户批准|业务能力与 UX 设计溯源表|前端修正待执行" docs/superpowers/packages/EXECUTION_INDEX.md
```

Expected: 夜间和并行手册都要求 Index 与证据进入最新共同基线；Index 仍是未通过状态。

- [ ] **Step 6: 提交验收与动态门槛同步**

```powershell
git add -- docs/user-testing/PHASE2_FRONTEND_RECALIBRATION_TEST_TEMPLATE.md docs/user-testing/README.md docs/superpowers/packages/EXECUTION_INDEX.md docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md
git diff --cached --check
git commit -m "docs: align frontend recalibration acceptance"
```

Expected: 提交只包含上述四个文件。

---

### Task 5: 完整自检、独立复审与功能分支交接

**Files:**

- Verify: 本计划 File Map 中的全部文件。
- Do not modify: 前后端源码、测试代码、历史 P2-02 至 P2-08 资料、真实 `user_data/`。

**Interfaces:**

- Consumes: Tasks 1-4 的所有文档提交。
- Produces: 可安全交给 integration 的纯文档提交链。

- [ ] **Step 1: 做批准规格逐项覆盖检查**

逐节核对批准规格：

| 规格要求 | 必须由本计划证明 |
|---|---|
| 业务能力与结果基线 | AGENTS、packages README、Phase map、Master Plan、ARCHITECTURE |
| UX 主动重设计 | AGENTS、Phase map、Master Plan、frontend README |
| 视觉权威独立 | STYLE、references README 保持纯视觉 |
| 用户专项决定边界 | packages README 七列表与 Phase map |
| P2-03 至 P2-08 仅为候选 | Phase map、Master Plan、ARCHITECTURE、frontend README |
| 批量复核能力与可选详情 | Phase map、Master Plan、UAT 模板 |
| 不按旧 UI 判定一致 | Master Plan 与 UAT 模板 |
| 门槛阻断 | Index、Nightly、Parallel |

任何一行无法指向实际文本时，返回相应 Task 修正，不得只在交接说明中补充。

- [ ] **Step 2: 扫描占位符、冲突标记和旧口径**

Run:

```powershell
rg -n --encoding utf-8 "T[B]D|T[O]DO|<{7}|={7}|>{7}" AGENTS.md ARCHITECTURE.md docs/superpowers/packages/README.md docs/superpowers/packages/EXECUTION_INDEX.md docs/superpowers/packages/NIGHTLY_AUTOMATION.md docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md docs/superpowers/packages/phase-2-execution-packages.md docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md docs/superpowers/plans/2026-07-14-phase-2-frontend-source-reset-documentation.md docs/superpowers/plans/2026-07-14-phase-2-frontend-ux-authority-sync-implementation.md docs/ui/STYLE.md docs/ui/references/README.md frontend/README.md docs/user-testing/README.md docs/user-testing/PHASE2_FRONTEND_RECALIBRATION_TEST_TEMPLATE.md
rg -n --encoding utf-8 "待实现元素或行为|以现有 UI 行为为准|行为对照清单|组件复用和最小代码修改范围|前端交互与业务行为必须逐项追溯" AGENTS.md ARCHITECTURE.md docs/superpowers/packages/README.md docs/superpowers/packages/EXECUTION_INDEX.md docs/superpowers/packages/NIGHTLY_AUTOMATION.md docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md docs/superpowers/packages/phase-2-execution-packages.md docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md docs/superpowers/plans/2026-07-14-phase-2-frontend-source-reset-documentation.md docs/ui/STYLE.md docs/ui/references/README.md frontend/README.md docs/user-testing/README.md docs/user-testing/PHASE2_FRONTEND_RECALIBRATION_TEST_TEMPLATE.md
```

Expected: 两条命令均零命中。版本化模板中的 `<YYYY-MM-DD>`、SHA、地址和步骤占位符是模板字段，不属于本扫描模式，也不得提前填写。

- [ ] **Step 3: 运行文档验证**

Run:

```powershell
$commonDir = (git rev-parse --path-format=absolute --git-common-dir).Trim()
$repoRoot = Split-Path -Parent $commonDir
$python = Join-Path $repoRoot 'runtime\python\python.exe'
& $python tools\check_documentation.py --root .
& $python -m pytest tests\test_documentation_governance.py -q
git diff --check
```

Expected: `Documentation check passed.`；`14 passed`；`git diff --check` 无输出。若主线新增了文档治理测试，以实际总数全绿为准，不把 14 当固定上限。

- [ ] **Step 4: 核对计数、范围与真实数据红线**

Run:

```powershell
$ids = @(rg --only-matching "P[1-5]-[0-9]{2}" docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md | Sort-Object -Unique)
$formalIds = @($ids | Where-Object { if ($_ -match '^P1-(\d{2})$') { [int]$Matches[1] -ge 9 } else { $true } })
$formalIds.Count
(rg -n "^### P2-[0-9]{2} " docs/superpowers/packages/phase-2-execution-packages.md | Measure-Object).Count
git status --short -- user_data
git diff --name-only 85d7cc664f24313408a01e75127b03fa7620bf12..HEAD
```

Expected: 正式包总数 `87`；Phase 2 为 `22`；`user_data` 无输出；差异中没有前端/后端源码、测试代码、数据库或历史 P2-02 至 P2-08 文档。

- [ ] **Step 5: 请求独立文档复审**

复审者必须逐项判断：

1. 是否仍有文字暗示新 Vue 必须复制旧 Streamlit 或 P2-08；
2. 是否有文字允许无来源地新增业务能力、数据语义或结果；
3. 七列表与用户决定阈值是否在权威文件一致；
4. UAT 是否同时验证业务等价与 UX 改善；
5. Index、Nightly、Parallel 是否都保持门槛未通过即停机；
6. 是否保持 P2-08 merged、87/22 和真实数据红线。

只有 Critical/Important 为零才进入 integration；修复复审问题后重跑 Steps 2-4，并用独立提交记录修正。

---

### Task 6: 从最新主线安全集成，不干扰 P1-27

**Files:**

- Resolve during integration: `docs/superpowers/packages/EXECUTION_INDEX.md`
- Spot-check during integration: `ARCHITECTURE.md`
- Verify only: 其余文档提交。

**Interfaces:**

- Consumes: Task 5 已复审的纯文档提交链。
- Produces: 基于最新 `origin/main`、保留全部 P1 动态事实的 integration 候选。

- [ ] **Step 1: 等待并核对 P1-27 的共同基线状态**

P1-27 功能 worktree 可以继续独立开发，本计划不得修改其分支或文件。准备集成本文档链时先运行：

```powershell
git fetch --prune origin
git log origin/main -1 --oneline
git status --short
git status --short -- user_data
```

如果 P1-27 正在等待 integration，优先让 P1-27 按其既定流程进入 `origin/main`，再从更新后的主线创建本文档的专用 integration 分支，从而只解决一次动态 Index。若用户决定文档先行，也必须在独立 integration 分支解决，不能写根目录 `main`。

- [ ] **Step 2: 使用独立 worktree 创建 integration 分支**

调用 `superpowers:using-git-worktrees`，从当时最新 `origin/main` 创建 `.worktrees/p2-frontend-realignment-integration` 和 `codex/integration-p2-frontend-realignment`。若路径或分支已存在，先停止并核验其用途，不覆盖或复用未知现场。该 worktree 不得复用 P1-27 的功能 worktree；创建后再次确认源码状态和 `user_data` 状态均为空。

- [ ] **Step 3: 按提交顺序接收文档链**

先接收：

```powershell
git cherry-pick 5cec41f7dc167c417796fc214b7b5648c406d49e
```

当前基线预期 `EXECUTION_INDEX.md` 发生内容冲突；`ARCHITECTURE.md` 当前可自动合并，但主线继续前进后也可能冲突。每次 cherry-pick 后先运行：

```powershell
$unmerged = @(git diff --name-only --diff-filter=U)
$unmerged
if ($unmerged | Where-Object { $_ -like 'user_data/*' }) { throw 'user_data conflict is out of scope' }
```

对全部未解决文件逐一处理，不得只暂存 Index 或整文件选择一侧。`EXECUTION_INDEX.md` 必须以最新主线的 Phase 1 总数、P1-26/P1-27/P1-28/P1-29 状态、当前队列和下一动作作为底稿，只叠加以下 Phase 2 内容：

- Phase 2 保持 8/14，P2-08 保持 merged；
- 增加非编号来源重校准门槛及“未通过”结果；
- 增加 P2-09/13/14/16/19 和传递依赖阻断；
- 保留门槛不计入 87/22、不进入夜间矩阵的说明。

`ARCHITECTURE.md` 必须同时保留最新主线新增的 P1 架构事实与本分支的 Phase 2 来源重校准事实；其他冲突文档也必须以最新主线事实为底，只叠加本计划批准的业务/UX 口径。解决后：

```powershell
$unmerged = @(git diff --name-only --diff-filter=U)
git add -- $unmerged
if (@(git diff --name-only --diff-filter=U).Count -ne 0) { throw 'unresolved conflicts remain' }
git cherry-pick --continue
git cherry-pick b5ce6a5d867a8b93c1ef8534c34823f1245b0113
$remaining = @(git rev-list --reverse b5ce6a5d867a8b93c1ef8534c34823f1245b0113..codex/p2-frontend-realignment-design)
foreach ($commit in $remaining) {
    git cherry-pick $commit
    if ($LASTEXITCODE -ne 0) {
        $unmerged = @(git diff --name-only --diff-filter=U)
        throw "cherry-pick stopped at $commit; resolve every listed file against latest main before continuing: $($unmerged -join ', ')"
    }
}
```

如果后续提交停止，重复本步骤的“列出全部未解决文件 → 逐文件保留最新主线事实并叠加批准口径 → 暂存全部已解决文件 → `git cherry-pick --continue`”，然后从尚未接收的下一个提交继续。`ARCHITECTURE.md` 即使自动合并也要人工确认：最新主线新增的 P1-26/P1-27/P1-28 架构事实与本分支的 Phase 2 来源重校准事实必须同时保留。

- [ ] **Step 4: 复核动态 Index**

Run:

```powershell
rg -n --encoding utf-8 "P1-26|P1-27|P1-28|P1-29|Phase 2 前端来源重校准门槛|P2-09 至 P2-22" docs/superpowers/packages/EXECUTION_INDEX.md
rg -n --encoding utf-8 "25.*4|P1-26 API/DB 性能测量基线.*ready" docs/superpowers/packages/EXECUTION_INDEX.md
```

Expected: 第一条保留最新主线的全部 P1 动态事实并含 Phase 2 门槛；第二条零命中。若集成时主线已继续前进，以当时最新 Index 为准，不把本计划中的 2026-07-14 快照覆盖回去。

- [ ] **Step 5: 运行纯文档 integration 门槛**

Run:

```powershell
$commonDir = (git rev-parse --path-format=absolute --git-common-dir).Trim()
$repoRoot = Split-Path -Parent $commonDir
$python = Join-Path $repoRoot 'runtime\python\python.exe'
& $python tools\check_documentation.py --root .
& $python -m pytest tests\test_documentation_governance.py -q
& $python tools\smoke_check.py --skip-tests
git diff --check
git status --short -- user_data
```

Expected: 文档检查、治理测试和快速冒烟通过；无冲突标记；`user_data` 无输出。纯文档集成不默认运行全量 pytest；若手工冲突解决触及源码或测试，按 `AGENTS.md` 的风险触发条件升级验证。

- [ ] **Step 6: 通过 PR 合并并同步实际夜间自动化**

按仓库标准流程推送 integration 分支、创建以 GitHub `main` 为目标的 PR、检查通过后合并；禁止直接 push `main`。合并后重新 fetch，确认来源重校准规格、Index 硬停机、Nightly prompt 和验收模板都已进入 `origin/main`。

随后使用 Codex 自动化管理能力，把“阅卷系统夜间4点推进”的 prompt 更新为 `docs/superpowers/packages/NIGHTLY_AUTOMATION.md` 中 `AUTOMATION_PROMPT_START/END` 之间的精确文本，并反读核对名称、频率、模型、项目、启用状态和 prompt。配置未成功反读前，必须报告夜间外部配置仍未受新门槛保护，不得宣称门槛 enforcement 已完整生效。

---

## Explicitly Out of Scope

- 本计划不实现 P2-03 至 P2-08 的前端修正，不设计最终批量阅卷页面，不恢复或生成新的 AI 全页概念图。
- 本计划不添加新的正式执行包、数据库迁移、API、状态、快捷键或业务功能。
- 本计划不新增“Index passed 必须自动校验 checkpoint 内容”的代码守卫；如需把人工治理升级为程序校验，应另写独立设计与实施计划，不在本次最小权威文档同步中顺手扩展。
- 本计划完成只表示文档权威统一并进入共同基线，不表示来源重校准门槛已经通过。门槛仍需业务能力盘点、前端设计与修正、自动验证、独立复审和用户版本化验收。
