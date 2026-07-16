# P2-15 知识图谱与证据下钻 v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付独立的只读 Vue 知识图谱页面，让教师按考试、班级和学生查看精确知识标签得分率、分组树、节点事实和分页证据，同时明确禁止把展示分组线解释为知识父子或先修关系。

**Architecture:** 保持 P1-21 FastAPI Graph 契约与数据库不变，前端新增通用 Graph scope 客户端、独立 Pinia store、纯函数视图模型和 ECharts Canvas 页面。图谱模式把无关系标签节点放入四条得分率证据带；分组树只从 rows 临时构造“当前范围 → 学生 → 精确标签”归属线，节点 inspector 的证据请求始终复用同一规范化范围。工作台保留摘要并传递受控当前考试/班级入口。

**Tech Stack:** Vue 3、TypeScript 6、Pinia 3、Vue Router 5、ECharts 6 Canvas、Element Plus、Vitest、Playwright、现有 FastAPI/Pydantic Graph API、pytest。

**执行包：** P2-15
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** yes
**计划基线：** 9f47ececdd6be6eff68831d74da85d706cca7e48
**用户自测：** quick
**自测清单：** `docs/user-testing/checkpoints/P2-15-v1.5.0-knowledge-graph-quick-check.md`

## Global Constraints

- 设计权威为 `docs/superpowers/specs/2026-07-15-p2-15-knowledge-graph-evidence-drilldown-design.md`；用户于 2026-07-15 批准独立页面、默认“当前考试 + 当前班级”和仅表示归属的分组线。
- 活动身份只能是 `knowledge_point:<精确裁剪后的 question_tags 标签值>`；不合并近义词，不读取 legacy concept/skill 关系。
- P1-21 `GraphRowsResponse.edges` 必须继续为 `[]`；前端收到非空 edges 必须拒绝响应，不得渲染为知识父子、先修或相关关系。
- 分组树虚线只允许表达“当前筛选范围 → 学生 → 精确标签”，只在浏览器内生成，不写入 API、数据库、导出或领域模型。
- `tag_context.prerequisite` 只是题目支持标签，不是经确认的知识关系边。
- 掌握度继续使用现有 tag-only 加权得分率和旧生产阈值：`>=90%` 稳定、`>=75%` 轻微欠缺、`>=60%` 需要讲评、`<60%` 重点薄弱；不实现 Phase 4 掌握度 v2。
- 默认范围沿用有效当前考试和从工作台传入的当前班级；无有效班级时要求用户选择，不擅自选择第一个班级。
- 只使用现有 `/api/sessions`、`/api/students`、`/api/graph/rows` 和 `/api/graph/evidence`；不新增生产依赖，不修改 `package.json` 或锁文件。
- 页面只读；不提供评分、标签确认、关系编辑、训练生成、Job 提交、原卷路径或未来空入口。
- ECharts 使用 Canvas；1000 节点基准必须记录非空渲染、模式切换、缩放/拖动和选择结果，不凭空声明跨机器通用毫秒阈值。
- 图表必须有真实 DOM 文字目录、文字等级、覆盖率和 warning；颜色、位置、尺寸都不能成为唯一信息。
- 固定验证 1920×1080、1440×900、1366×768、1280×800、1024×768；低于 1024px 不扩展产品支持范围。
- 视觉只使用现有 Token 和字体；禁止渐变、发光、强阴影、营销 Hero、大圆角卡片海洋或装饰性动画。
- 测试、截图、性能数据和用户短测只使用匿名固定合成数据或临时数据库，不读取、写入、暂存、提交或 stash 真实 `user_data/`。
- 功能分支不更新 `EXECUTION_INDEX.md`；动态状态、共享冲突和最终架构事实由 integration 按验证结果统一处理。

---

## File Map

**Create:**

- `docs/superpowers/specs/2026-07-15-p2-15-knowledge-graph-evidence-drilldown-design.md`：已批准设计与业务/UX 溯源表；领取提交之后单独引入。
- `frontend/src/api/students.ts`：只读学生列表严格解码，供班级/学生筛选使用。
- `frontend/src/features/knowledge-graph/model.ts`：四档阈值、节点尺寸、稳定证据带和临时分组树纯函数。
- `frontend/src/stores/knowledge-graph.ts`：完整范围、rows、选择、evidence 分页、请求世代与 stale 状态。
- `frontend/src/components/knowledge-graph/GraphScopeFilters.vue`：考试、班级、学生范围和应用按钮。
- `frontend/src/components/knowledge-graph/KnowledgeGraphCanvas.vue`：ECharts 生命周期、图/树 option、缩放、恢复与节点事件。
- `frontend/src/components/knowledge-graph/GraphNodeInspector.vue`：节点事实、支持标签、错因和证据分页。
- `frontend/src/components/knowledge-graph/GraphTextDirectory.vue`：可搜索、分页和键盘选择的文字替代。
- `frontend/src/views/KnowledgeGraphView.vue`：受控 URL、默认上下文、页面状态和响应式编排。
- `frontend/src/styles/knowledge-graph.css`：证据带、inspector、文字目录和五视口样式。
- `frontend/src/api/__tests__/graph.spec.ts`：通用 scope、严格响应和非空 edges 拒绝测试。
- `frontend/src/api/__tests__/students.spec.ts`：学生列表严格解码和脱敏失败测试。
- `frontend/src/__tests__/knowledge-graph-model.spec.ts`：阈值边界、稳定布局、尺寸和分组树测试。
- `frontend/src/__tests__/knowledge-graph-store.spec.ts`：旧请求隔离、范围一致、证据分页和 stale 测试。
- `frontend/src/__tests__/knowledge-graph-components.spec.ts`：ECharts 生命周期、文字目录键盘和 inspector 测试。
- `frontend/src/__tests__/knowledge-graph-view.spec.ts`：默认范围、筛选、空错态、路由和工作台入口测试。
- `frontend/e2e/knowledge-graph.spec.ts`：五视口、像素、缩放、图/树、键盘、证据和 1000 节点基准。
- `docs/user-testing/checkpoints/P2-15-v1.5.0-knowledge-graph-quick-check.md`：实现与浏览器验证完成后生成的版本化短测卡。

**Modify:**

- `frontend/src/api/graph.ts`：新增严格通用 scope 请求，保留 P2-14 当前考试/班级兼容入口。
- `frontend/src/navigation.ts`：增加真实“知识图谱”路由定义。
- `frontend/src/router/index.ts`：注册 `/knowledge-graph` 懒加载页面。
- `frontend/src/components/analysis/TagCoverageSummary.vue`：保留摘要和证据行为，增加受控完整图谱入口。
- `frontend/src/views/WorkbenchView.vue`：把当前 session/class 传给图谱入口，不改变分析公式。
- `frontend/src/main.ts`：导入 `knowledge-graph.css`。
- `frontend/src/__tests__/navigation-router.spec.ts`：冻结真实导航与路由元数据。
- `frontend/src/__tests__/workbench-view.spec.ts`：冻结工作台进入图谱的受控上下文。
- `frontend/e2e/workbench-overview.spec.ts`：保留 P2-14 行为并断言完整图谱入口。
- `ARCHITECTURE.md`：实现后记录只读 Vue 图谱、空关系边和无 Schema 变化。
- `docs/superpowers/plans/2026-07-15-p2-15-knowledge-graph-evidence-drilldown-implementation.md`：领取、RED/GREEN、验证、复审与交接证据。

---

### Task 0: 领取 P2-15、引入批准设计并冻结基线

**Files:**

- Create: `docs/superpowers/plans/2026-07-15-p2-15-knowledge-graph-evidence-drilldown-implementation.md`
- Create after claim: `docs/superpowers/specs/2026-07-15-p2-15-knowledge-graph-evidence-drilldown-design.md`

**Interfaces:**

- Consumes: `origin/main` at `9f47ececdd6be6eff68831d74da85d706cca7e48`, approved design commit `ca7ef627d86ad25a5d4cf03fe6fd448cd1536965`.
- Produces: valid `in_progress` handoff, immutable stash baseline, clean P2-15 worktree and reproducible frontend/backend baseline.

- [x] **Step 1: 建立专属功能 worktree 并记录领取证据**

```powershell
git worktree add .worktrees/p2-15-knowledge-graph -b codex/p2-15-knowledge-graph origin/main
git -C .worktrees/p2-15-knowledge-graph rev-parse HEAD
git -C .worktrees/p2-15-knowledge-graph stash list --format='%H'
git -C .worktrees/p2-15-knowledge-graph status --short -- user_data
```

Expected: HEAD 为计划基线；stash 依次为 `85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2`；`user_data` 无输出。

- [x] **Step 2: 提交唯一领取计划并验证交接块**

```powershell
git add -- docs/superpowers/plans/2026-07-15-p2-15-knowledge-graph-evidence-drilldown-implementation.md
git diff --cached --check
git commit -m "docs: claim P2-15 knowledge graph"
& '..\..\runtime\python\python.exe' tools\handoff_status.py --plan docs\superpowers\plans\2026-07-15-p2-15-knowledge-graph-evidence-drilldown-implementation.md --repo .
```

Expected: `origin/main..HEAD` 的第一个 first-parent 提交只修改本计划，validator 输出 `ok=true` 与 `in_progress`。

- [x] **Step 3: 单独引入已批准设计**

```powershell
git cherry-pick ca7ef627d86ad25a5d4cf03fe6fd448cd1536965
git show --stat --oneline HEAD
```

Expected: 该提交只新增设计说明；计划领取提交仍是首个包提交。

- [x] **Step 4: 安装冻结依赖并运行代码前基线**

```powershell
Push-Location frontend
npm ci
npm run lint
npm run typecheck
npm run test
npm run build
Pop-Location
& '..\..\runtime\python\python.exe' -m pytest tests\test_api_graph_routes.py tests\test_skill_graph_projection.py tests\test_api_openapi_contract.py -q
& '..\..\runtime\python\python.exe' tools\smoke_check.py --skip-tests
```

Expected: 当前 P2-14/P1-21 基线全绿；任何失败先调查，不写 P2-15 代码规避。

---

### Task 1: 泛化严格 Graph scope 与学生筛选客户端

**Files:**

- Create: `frontend/src/api/students.ts`
- Create: `frontend/src/api/__tests__/graph.spec.ts`
- Create: `frontend/src/api/__tests__/students.spec.ts`
- Modify: `frontend/src/api/graph.ts`
- Modify: `frontend/src/api/__tests__/analysis.spec.ts`

**Interfaces:**

- Consumes: P1-21 `GraphQueryRequest`、`GraphEvidenceRequest`、`GraphRowsResponse`、`GraphEvidenceResponse` 与现有 `apiClient`。
- Produces: `GraphQueryInput`, `fetchScopedGraphRows(query, signal)`, `fetchScopedGraphEvidence(query, knowledgeKey, signal, page)`, `fetchStudents(signal)`；保留 `fetchGraphRows(sessionId, className)` 和 `fetchGraphEvidence(...)`。

- [x] **Step 1: 写 scope、非空 edges 和学生列表 RED 测试**

```typescript
const query: GraphQueryInput = {
  scope: { mode: 'selected', student_ids: ['12', '15'] },
  exam_scope: { mode: 'manual', session_ids: [7, 8] },
}

it('posts a selected/manual graph scope without legacy controls', async () => {
  await fetchScopedGraphRows(query)
  expect(fetchMock).toHaveBeenCalledWith('/api/graph/rows', expect.objectContaining({
    method: 'POST',
    body: query,
  }))
})

it('rejects relationship edges in the P2-15 response', () => {
  expect(() => decodeGraphRowsResponse({
    ...rows,
    edges: [{ source_key: 'a', target_key: 'b', relation_type: 'parent', weight: 1 }],
  })).toThrow('Invalid graph rows')
})

it('rejects extra student fields', () => {
  expect(() => decodeStudentList({ items: [{ ...student, private_path: 'hidden' }], total: 1 }))
    .toThrow('Invalid student list')
})
```

- [x] **Step 2: 运行目标测试确认 RED**

```powershell
Push-Location frontend
npm run test -- src/api/__tests__/graph.spec.ts src/api/__tests__/students.spec.ts src/api/__tests__/analysis.spec.ts
Pop-Location
```

Expected: 新模块/函数不存在且当前 decoder 接受合法非空 edges。

- [x] **Step 3: 实现最小严格请求与解码**

```typescript
export type GraphStudentScopeInput =
  | { mode: 'class'; class_id: string; student_ids?: string[] }
  | { mode: 'student' | 'selected'; student_ids: string[] }

export type GraphExamScopeInput =
  | { mode: 'current'; session_ids: [number] }
  | { mode: 'manual'; session_ids: number[] }
  | { mode: 'cross_exam'; session_ids?: never }

export interface GraphQueryInput {
  scope: GraphStudentScopeInput
  exam_scope: GraphExamScopeInput
}

export function fetchScopedGraphRows(query: GraphQueryInput, signal?: AbortSignal) {
  const expected = normalizeGraphQuery(query)
  return apiClient.request('/api/graph/rows', {
    method: 'POST', body: expected, signal,
    decode: (value) => decodeGraphRowsResponse(value, expected),
  })
}
```

`decodeGraphRowsResponse(value, expected?)` 必须额外要求 `edges.length === 0`；normalized response 的 mode、class、活动 session 和 evidence item 必须属于返回范围。不存在/删除对象允许服务过滤并用 warnings 说明，所以 selected/manual 响应 ID 只能是请求 ID 的有序子集，不要求把无效 ID 原样回显。

`students.ts` 只接受精确字段 `id/student_code/name/class_name/created_at` 和顶层 `items/total`，捕获失败后抛出固定 `StudentReadError('无法读取学生列表')`。

- [x] **Step 4: 运行 GREEN 与兼容回归并提交**

```powershell
Push-Location frontend
npm run test -- src/api/__tests__/graph.spec.ts src/api/__tests__/students.spec.ts src/api/__tests__/analysis.spec.ts src/__tests__/analysis-store.spec.ts src/__tests__/workbench-view.spec.ts
npm run typecheck
Pop-Location
git add -- frontend/src/api/graph.ts frontend/src/api/students.ts frontend/src/api/__tests__/graph.spec.ts frontend/src/api/__tests__/students.spec.ts frontend/src/api/__tests__/analysis.spec.ts
git diff --cached --check
git commit -m "feat: support strict graph query scopes"
```

Expected: 新 scope 全绿，P2-14 当前班级兼容函数和工作台测试不变。

---

### Task 2: 建立确定性证据带与临时分组树模型

**Files:**

- Create: `frontend/src/features/knowledge-graph/model.ts`
- Create: `frontend/src/__tests__/knowledge-graph-model.spec.ts`

**Interfaces:**

- Consumes: `GraphNode[]`, `GraphRow[]`。
- Produces: `masteryBand(rate)`, `buildEvidenceLaneModel(nodes)`, `buildGroupingTree(rows, scopeLabel)`, `nodeEvidenceSize(itemCount)`, `summarizeGraph(nodes)`。

- [x] **Step 1: 写阈值、稳定排序、尺寸和归属树 RED 测试**

```typescript
expect([0.9, 0.8999, 0.75, 0.7499, 0.6, 0.5999].map(masteryBand)).toEqual([
  'stable', 'slight', 'slight', 'review', 'review', 'weak',
])
expect(buildEvidenceLaneModel(nodes).map((item) => item.knowledgeKey)).toEqual([
  'knowledge_point:重点薄弱', 'knowledge_point:需要讲评',
  'knowledge_point:轻微欠缺', 'knowledge_point:稳定',
])
expect(buildGroupingTree(rows, '当前考试 · 七年级一班')).toMatchObject({
  name: '当前考试 · 七年级一班',
  children: [{ name: 'S001 学生甲', children: [{ knowledgeKey: 'knowledge_point:三角形全等' }] }],
})
```

另断言重复 row 不重复学生下标签、名称按 `student_code/student_name/knowledge_key` 稳定排序、`nodeEvidenceSize` 单调但上下限受控、1000 节点输入不产生 `NaN/Infinity`。

- [x] **Step 2: 运行测试确认 RED**

```powershell
Push-Location frontend
npm run test -- src/__tests__/knowledge-graph-model.spec.ts
Pop-Location
```

Expected: `features/knowledge-graph/model.ts` 不存在。

- [x] **Step 3: 实现最小纯函数模型**

```typescript
export type MasteryBand = 'stable' | 'slight' | 'review' | 'weak'

export function masteryBand(rate: number): MasteryBand {
  if (rate >= 0.9) return 'stable'
  if (rate >= 0.75) return 'slight'
  if (rate >= 0.6) return 'review'
  return 'weak'
}

export function nodeEvidenceSize(itemCount: number): number {
  return Math.min(72, Math.max(28, 24 + Math.sqrt(Math.max(0, itemCount)) * 6))
}
```

`buildEvidenceLaneModel` 输出稳定的 band index、百分比、文字等级、受控尺寸和 ECharts 坐标槽位，不创建 links。`buildGroupingTree` 只输出 scope/student/tag 三层展示节点，并为 tag 节点保留 `knowledgeKey`；不得读取 `edges` 或 `tag_context.prerequisite`。

- [x] **Step 4: 运行 GREEN、类型检查并提交**

```powershell
Push-Location frontend
npm run test -- src/__tests__/knowledge-graph-model.spec.ts
npm run typecheck
Pop-Location
git add -- frontend/src/features/knowledge-graph/model.ts frontend/src/__tests__/knowledge-graph-model.spec.ts
git diff --cached --check
git commit -m "feat: model graph evidence lanes"
```

---

### Task 3: 建立独立 Graph store 与严格证据下钻

**Files:**

- Create: `frontend/src/stores/knowledge-graph.ts`
- Create: `frontend/src/__tests__/knowledge-graph-store.spec.ts`

**Interfaces:**

- Consumes: Task 1 scoped fetchers、`GraphQueryInput`、`GraphRowsResponse`、`GraphEvidenceResponse`。
- Produces: `useKnowledgeGraphStore()` with `loadGraph`, `selectNode`, `loadMoreEvidence`, `retryGraph`, `retryEvidence`, `clearScope`；独立 `graphState/evidenceState`。

- [x] **Step 1: 写请求世代、范围匹配和分页 RED 测试**

```typescript
it('does not let an older scope overwrite the current graph', async () => {
  const first = deferred<GraphRowsResponse>()
  const second = deferred<GraphRowsResponse>()
  void store.loadGraph(queryA, () => first.promise)
  void store.loadGraph(queryB, () => second.promise)
  second.resolve(rowsB)
  first.resolve(rowsA)
  await settle()
  expect(store.appliedQuery).toEqual(queryB)
  expect(store.graph).toEqual(rowsB)
})

it('drops old evidence when the selected node changes', async () => {
  await store.selectNode(nodeA, slowEvidenceA)
  await store.selectNode(nodeB, evidenceB)
  expect(store.selectedNodeKey).toBe(nodeB.knowledge_key)
  expect(store.evidence?.knowledge_key).toBe(nodeB.knowledge_key)
})
```

另测首次失败、最后成功内容 `stale-error`、load more 去重、scope 切换清空选择、空节点状态和 AbortError 不显示错误。

- [x] **Step 2: 运行测试确认 RED**

```powershell
Push-Location frontend
npm run test -- src/__tests__/knowledge-graph-store.spec.ts
Pop-Location
```

Expected: store 不存在。

- [x] **Step 3: 实现最小 store**

```typescript
export const useKnowledgeGraphStore = defineStore('knowledge-graph', () => {
  const graph = ref<GraphRowsResponse | null>(null)
  const appliedQuery = ref<GraphQueryInput | null>(null)
  const selectedNodeKey = ref<string | null>(null)
  const evidence = ref<GraphEvidenceResponse | null>(null)
  const graphState = ref<ResourceState>('idle')
  const evidenceState = ref<ResourceState>('idle')
  let graphGeneration = 0
  let evidenceGeneration = 0
  let graphController: AbortController | null = null
  let evidenceController: AbortController | null = null
  // loadGraph/selectNode/loadMoreEvidence compare generation + normalized scope before assignment.
  return { graph, appliedQuery, selectedNodeKey, evidence, graphState, evidenceState,
    loadGraph, selectNode, loadMoreEvidence, retryGraph, retryEvidence, clearScope }
})
```

实现不得缓存到 localStorage，不得保留学生正文；graph scope 变化必须终止 evidence 请求。证据追加按 `(session_id, student_id, question_id, bank_question_id)` 去重。

- [x] **Step 4: 运行 GREEN 与 store 回归并提交**

```powershell
Push-Location frontend
npm run test -- src/__tests__/knowledge-graph-store.spec.ts src/__tests__/analysis-store.spec.ts src/__tests__/workbench-store.spec.ts
npm run typecheck
Pop-Location
git add -- frontend/src/stores/knowledge-graph.ts frontend/src/__tests__/knowledge-graph-store.spec.ts
git diff --cached --check
git commit -m "feat: manage graph scope and evidence state"
```

---

### Task 4: 实现 ECharts 画布、文字目录和节点 inspector

**Files:**

- Create: `frontend/src/components/knowledge-graph/KnowledgeGraphCanvas.vue`
- Create: `frontend/src/components/knowledge-graph/GraphTextDirectory.vue`
- Create: `frontend/src/components/knowledge-graph/GraphNodeInspector.vue`
- Create: `frontend/src/__tests__/knowledge-graph-components.spec.ts`

**Interfaces:**

- Consumes: Task 2 view model、Task 3 node/evidence state。
- Produces: `select-node`, `change-mode`, `load-more-evidence`, `retry-evidence` component events；ECharts instance lifecycle and accessible text alternative.

- [x] **Step 1: 写组件 RED 测试**

使用可注入 `chartFactory` fake，断言 mount 一次、数据变化 `setOption`、容器变化 `resize`、unmount `dispose`、点击 tag 发 `knowledgeKey`、图谱 option `links=[]`、分组树虚线常驻说明。文字目录断言搜索、50 条分页、ArrowDown/ArrowUp/Enter 选择和可见焦点；inspector 断言文字等级、计数、支持标签、错因、证据 loading/empty/error/stale/pagination。

```typescript
expect(fakeChart.setOption).toHaveBeenCalledWith(expect.objectContaining({
  series: [expect.objectContaining({ type: 'graph', links: [] })],
}), true)
directory.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
expect(selected).toEqual(['knowledge_point:三角形全等'])
```

- [x] **Step 2: 运行测试确认 RED**

```powershell
Push-Location frontend
npm run test -- src/__tests__/knowledge-graph-components.spec.ts
Pop-Location
```

Expected: 三个组件不存在。

- [x] **Step 3: 实现画布生命周期与两种 option**

```typescript
type ChartLike = Pick<ECharts, 'setOption' | 'on' | 'off' | 'resize' | 'dispose' | 'dispatchAction'>
const props = withDefaults(defineProps<{
  nodes: GraphNode[]
  rows: GraphRow[]
  mode: 'graph' | 'tree'
  selectedKey: string | null
  scopeLabel: string
  chartFactory?: (element: HTMLElement) => ChartLike
}>(), { chartFactory: undefined })
```

图谱 option 使用 `type:'graph'`、`layout:'none'`、Canvas 坐标、`links:[]` 和 roam；树 option 使用 `type:'tree'`、Task 2 临时 hierarchy、浅灰虚线，并在组件外可见文字声明归属含义。ResizeObserver 回调合并到 `requestAnimationFrame`；reduced motion 时 `animation:false`。

- [x] **Step 4: 实现文字目录与 inspector**

目录按钮的可访问名称为“标签名，得分率，文字等级，证据数”；键盘选择复用按钮 click 结果。inspector 只渲染 Graph 契约允许字段，得分显示使用有限数值格式化，空原因显示“未记录扣分原因”，不生成建议或关系。

- [x] **Step 5: 运行 GREEN、lint/typecheck 并提交**

```powershell
Push-Location frontend
npm run test -- src/__tests__/knowledge-graph-components.spec.ts src/__tests__/knowledge-graph-model.spec.ts
npm run lint
npm run typecheck
Pop-Location
git add -- frontend/src/components/knowledge-graph frontend/src/__tests__/knowledge-graph-components.spec.ts
git diff --cached --check
git commit -m "feat: render accessible knowledge graph"
```

---

### Task 5: 接入独立页面、筛选、导航和工作台上下文

**Files:**

- Create: `frontend/src/components/knowledge-graph/GraphScopeFilters.vue`
- Create: `frontend/src/views/KnowledgeGraphView.vue`
- Create: `frontend/src/styles/knowledge-graph.css`
- Create: `frontend/src/__tests__/knowledge-graph-view.spec.ts`
- Modify: `frontend/src/navigation.ts`
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/components/analysis/TagCoverageSummary.vue`
- Modify: `frontend/src/views/WorkbenchView.vue`
- Modify: `frontend/src/main.ts`
- Modify: `frontend/src/__tests__/navigation-router.spec.ts`
- Modify: `frontend/src/__tests__/workbench-view.spec.ts`

**Interfaces:**

- Consumes: Tasks 1—4、session store、route query `session/class`。
- Produces: `/knowledge-graph` 页面、主导航入口、工作台带上下文入口和五视口布局。

- [x] **Step 1: 写页面、路由和工作台入口 RED 测试**

```typescript
expect(navigationItems.map(({ id, label, path }) => [id, label, path])).toEqual([
  ['workbench', '工作台', '/workbench'],
  ['knowledge-graph', '知识图谱', '/knowledge-graph'],
  ['grading', '评分复核', '/grading'],
])
expect(router.resolve('/knowledge-graph?session=7&class=七年级一班').name).toBe('knowledge-graph')
expect(openGraphLink.attributes('href')).toContain('session=7')
expect(openGraphLink.attributes('href')).toContain('class=')
```

页面测试覆盖：无班级不发 Graph；有效 route 默认当前考试/班级；无效 session/class 显示安全提示；手动考试/跨考试、班级/单学生/已选学生应用后请求精确 scope；切换图/树不重取；空、error、stale、warning 和 selected inspector。

- [x] **Step 2: 运行目标测试确认 RED**

```powershell
Push-Location frontend
npm run test -- src/__tests__/knowledge-graph-view.spec.ts src/__tests__/navigation-router.spec.ts src/__tests__/workbench-view.spec.ts
Pop-Location
```

Expected: 页面、路由定义和工作台入口不存在。

- [x] **Step 3: 实现筛选器和页面编排**

```typescript
export const knowledgeGraphRouteDefinition = {
  id: 'knowledge-graph',
  label: '知识图谱',
  path: '/knowledge-graph',
  title: '知识图谱',
  description: '按考试、班级和学生查看知识标签证据',
  breadcrumb: '知识图谱',
} as const satisfies WorkspaceRouteDefinition
```

筛选草稿与已应用范围分开；“应用范围”才调用 store。标签搜索只过滤/聚焦当前 nodes。工作台入口通过命名路由传 `sessionId` 和 `className`；直接导航没有有效班级时显示选择提示。URL 只保留验证后的 ID、枚举和班级值，不保存 evidence。

- [x] **Step 4: 实现克制视觉与响应式布局**

`knowledge-graph.css` 只使用现有 CSS Variables。1280px 以上 70/30 双栏；1024px 把 inspector 放到画布下方。画布最小高度稳定，筛选器换行但不隐藏；文字目录、warning、覆盖率、焦点和错误恢复始终可见。图例文字同时给出四档阈值，不使用渐变或发光。

- [x] **Step 5: 运行页面与既有工作台回归并提交**

```powershell
Push-Location frontend
npm run test -- src/__tests__/knowledge-graph-view.spec.ts src/__tests__/navigation-router.spec.ts src/__tests__/workbench-view.spec.ts src/components/shell/__tests__/app-shell.spec.ts
npm run lint
npm run typecheck
npm run build
Pop-Location
git add -- frontend/src/components/knowledge-graph/GraphScopeFilters.vue frontend/src/views/KnowledgeGraphView.vue frontend/src/styles/knowledge-graph.css frontend/src/__tests__/knowledge-graph-view.spec.ts frontend/src/navigation.ts frontend/src/router/index.ts frontend/src/components/analysis/TagCoverageSummary.vue frontend/src/views/WorkbenchView.vue frontend/src/main.ts frontend/src/__tests__/navigation-router.spec.ts frontend/src/__tests__/workbench-view.spec.ts
git diff --cached --check
git commit -m "feat: route the knowledge graph workspace"
```

---

### Task 6: 冻结五视口、像素、交互和 1000 节点浏览器基线

**Files:**

- Create: `frontend/e2e/knowledge-graph.spec.ts`
- Modify: `frontend/e2e/workbench-overview.spec.ts`

**Interfaces:**

- Consumes: Task 5 完整页面。
- Produces: 匿名合成 API、五视口、Canvas 非空、缩放/拖动、图/树、键盘、证据、只读守卫和 1000 节点性能证据。

- [x] **Step 1: 写匿名 Graph 路由与核心 E2E**

```typescript
const viewports = [
  { width: 1024, height: 768 },
  { width: 1280, height: 800 },
  { width: 1366, height: 768 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
]

test('keeps graph, tree and evidence on one exact scope', async ({ page }) => {
  await installAnonymousGraphApi(page)
  await page.goto('/knowledge-graph?session=7&class=七年级一班')
  await page.getByRole('button', { name: /三角形全等.*62%/ }).click()
  await expect(page.getByRole('heading', { name: '节点详情' })).toBeVisible()
  await expect(page.getByRole('list', { name: '证据列表' })).toContainText('匿名学生甲')
  await page.getByRole('button', { name: '分组树' }).click()
  await expect(page.getByText(/虚线仅表示当前范围内的归属/)).toBeVisible()
})
```

拦截 sessions、students、graph rows/evidence；断言所有请求只有 GET 或两个既有 Graph POST，body 不含 legacy/relations/path 字段。

- [x] **Step 2: 增加五视口、Canvas 像素和键盘测试**

每档断言 `document.documentElement.scrollWidth <= innerWidth`、主要筛选/覆盖率/目录/inspector 可达。对 canvas bounding box 截图，统计与背景不同像素超过固定最低面积；模拟 wheel/drag 后 ECharts data URL 或截图发生变化，再用“适应画布”恢复。Tab 进入文字目录，ArrowDown + Enter 选择与 canvas click 得到同一 inspector。

- [x] **Step 3: 增加 1000 节点可复现基线**

合成 `knowledge_point:匿名标签-0001` 至 `-1000`，固定 seed、得分率和证据数。记录页面从 rows fulfill 到 canvas 非空、图/树切换、目录搜索和节点选择的 `performance.now()`；断言无 pageerror、无超过测试超时的冻结、选择结果正确、截图非空，并把测得值作为测试附件与最终验证记录，不提交机器绝对路径。

- [x] **Step 4: 运行 Chromium 与既有工作台回归并提交**

```powershell
Push-Location frontend
npm run build
npx playwright test e2e/knowledge-graph.spec.ts e2e/workbench-overview.spec.ts --project=chromium
Pop-Location
git add -- frontend/e2e/knowledge-graph.spec.ts frontend/e2e/workbench-overview.spec.ts
git diff --cached --check
git commit -m "test: verify P2-15 graph interactions"
```

Expected: 五视口、图/树、像素、缩放/拖动、键盘、证据与 1000 节点场景全绿；截图只含匿名数据。

---

### Task 7: 完成架构、短测、包级验证与交接

**Files:**

- Create after verified runtime: `docs/user-testing/checkpoints/P2-15-v1.5.0-knowledge-graph-quick-check.md`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/plans/2026-07-15-p2-15-knowledge-graph-evidence-drilldown-implementation.md`

**Interfaces:**

- Consumes: Tasks 0—6 完整候选。
- Produces: 当前架构事实、版本化 quick 清单、自动验证、独立复审、用户验收和合法 handoff。

- [x] **Step 1: 运行完整包内与受影响验证**

```powershell
& '..\..\runtime\python\python.exe' -m pytest tests\test_api_graph_routes.py tests\test_skill_graph_projection.py tests\test_api_openapi_contract.py tests\test_request_read_connections.py tests\test_frontend_api_client.py tests\test_frontend_app_shell.py tests\test_documentation_governance.py -q
Push-Location frontend
npm run lint
npm run typecheck
npm run test
npm run build
npx playwright test e2e/knowledge-graph.spec.ts e2e/workbench-overview.spec.ts --project=chromium
Pop-Location
& '..\..\runtime\python\python.exe' tools\smoke_check.py --skip-tests
git diff --check
git status --short -- user_data
```

Expected: 聚焦 pytest、全部前端 unit、lint/typecheck/build、两份 Chromium E2E 和快速冒烟通过；`user_data` 无输出。功能分支默认不跑全量 pytest。

- [x] **Step 2: 更新架构事实并创建已验证短测卡**

`ARCHITECTURE.md` 只记录已实现事实：独立 Vue 图谱路由、严格通用 scope、ECharts Canvas 证据带/临时归属树、文字目录、Graph evidence 和 `edges=[]` 防线；明确无 API/Schema/关系语义变化。

短测卡只有在 Chromium 启动方式实际验证后生成，绑定候选 SHA、匿名合成数据、URL `/knowledge-graph?session=7&class=七年级一班`、可见标识和关闭方式。步骤覆盖默认范围、考试/班级/学生切换、图/树说明、节点证据、文字目录键盘、缩放/拖动、warning/空态和 1024px；用户无需运行命令或打开真实系统。

- [x] **Step 3: 记录 `waiting_review` 并创建功能提交**

把交接块改为 `waiting_review / branch_head / passed / pending / pending / unchanged / report_only`。暂存明确文件，确认不含 `user_data`、node_modules、dist、playwright-report、test-results、截图缓存或真实导出后提交：

```powershell
git add -- ARCHITECTURE.md docs/superpowers/plans/2026-07-15-p2-15-knowledge-graph-evidence-drilldown-implementation.md docs/user-testing/checkpoints/P2-15-v1.5.0-knowledge-graph-quick-check.md
git diff --cached --check
git commit -m "docs: prepare P2-15 review evidence"
& '..\..\runtime\python\python.exe' tools\handoff_status.py --plan docs\superpowers\plans\2026-07-15-p2-15-knowledge-graph-evidence-drilldown-implementation.md --repo .
```

Expected: validator `ok=true`，停在 `waiting_review`；不得自报独立复审或用户验收通过。

- [ ] **Step 4: 完成独立复审、用户短测和最终交接**

独立复审范围为 `origin/main..HEAD`，重点检查 Graph scope 子集语义、非空 edges 拒绝、归属虚线文案、阈值边界、ECharts dispose/resize、旧请求隔离、证据串线、千节点标签密度、导航共享冲突和只读请求守卫。Critical/Important 必须为 0；修复先新增 RED 测试并重跑影响面。

复审通过后创建只改本计划的 `waiting_user` 锚点，记录直接父提交的完整已复审 SHA。用户只检查该 SHA；通过后创建只改唯一 P2-15 短测卡的证据提交，再创建只改本计划的最终交接提交。最终字段为 `verified_pending_integration / <完整功能 SHA> / passed / passed / passed / unchanged / independent_candidate_allowed`，运行 validator 要求 `ok=true`。

---

## Integration Order and Conflict Rules

1. integration 从当时最新 `origin/main` 创建；若 P2-09 已进入主线，P2-15 必须在该基线重放并逐项解决共享导航冲突。
2. `frontend/src/navigation.ts`、router、`main.ts`、App Shell 测试、`ARCHITECTURE.md` 和 Index 不得整文件选择一侧；同时保留所有已合并真实入口。
3. 先合入 P2-15 功能提交并运行 Graph/导航/工作台聚焦回归，再由 integration 更新动态 Index；不得在功能分支宣称 `merged`。
4. 一波完成后运行完整 `tools/smoke_check.py`、前端 lint/typecheck/unit/build/browser 和真实两库文件指纹守卫。
5. 推送 integration 并通过 PR 合并 GitHub `main`；禁止直接 push main、force push、写真实数据或丢弃未合并历史。

## Rollback and Stop Conditions

- 任一实现要求数据库迁移、非空知识关系 edges、新掌握度公式、关系推断/编辑、写 API、真实模型调用或真实数据测试时立即停止并重新评审边界。
- scope/证据一致性、非空 edges 拒绝、ECharts 生命周期、浏览器像素、1000 节点、构建、快速冒烟、handoff validator 或真实两库指纹任一失败时保留 worktree，不进入 integration。
- 同一问题连续两次修复失败时停止局部试错，重新执行系统化调试并复核设计来源。
- 回退移除知识图谱路由/组件/store/样式、通用 scope 增量、工作台入口和测试；保留 P2-14 标签摘要及 P1-21 Graph API。无 Schema 或业务写入，不需要恢复数据。

## Plan Self-Review

- 规格覆盖：独立页面、默认当前考试/班级、全部范围类型、图/树、临时归属线、节点 inspector、证据分页、无标签空态、warning、键盘/文字目录、非颜色信息、五视口、Canvas 像素、缩放/拖动和 1000 节点均有任务。
- 语义边界：Graph API edges 保持空并由 decoder 再次防守；`prerequisite` 支持标签不转关系；前端不复制聚合公式。
- 类型一致：Task 1 的 `GraphQueryInput` 被 Task 3 store 和 Task 5 page 直接使用；Task 2 `knowledgeKey` 被 Task 4/5 的选择事件复用；evidence 始终使用 store 的 `appliedQuery`。
- 依赖顺序：先契约，再纯模型，再状态，再组件，再页面/路由，最后浏览器与交接；每个任务都有独立 RED/GREEN 和提交。
- 占位扫描：计划不包含待填字段或无测试的“适当处理”；实现步骤给出具体文件、接口、命令、失败预期和边界。
- 数据安全：计划不创建后端写路径、不打开真实 SQLite、不暂存/stash `user_data`，用户短测只使用匿名合成接口。

## 实施与验证证据（2026-07-16）

- 功能候选：`ab47fe33a26966a93321a098970e485b9e52986a`；后续仅允许文档、复审修复或验收证据提交改变分支头。
- Graph/前端治理聚焦 pytest：76 passed；导航守卫按 P2-15 真实入口更新后聚焦复核 3 passed。
- 前端：lint 无告警，typecheck 通过，36 个测试文件 / 314 tests passed，生产 build 通过。
- Chromium：知识图谱 7 个场景覆盖五视口、Canvas 像素、缩放、图/树、键盘、证据分页和 1000 节点；工作台 10 个既有场景通过。并发复跑暴露旧“快速切换考试”测试未先确认两个旧请求在途，修正测试前置条件后单 worker 聚焦通过，生产切换保护未改。
- 快速冒烟 `--skip-tests` 通过；功能工作区 `user_data` 无变更；验证前后真实两库 SHA-256、大小和 UTC mtime 指纹一致。
- 主实施者已按 `origin/main..ab47fe3` 检查 scope 子集、非空 edges 拒绝、分组虚线声明、阈值、ECharts 生命周期、旧请求隔离、证据分页、导航和只读请求；未发现 Critical/Important。独立复审仍按交接字段保持 pending，不以主实施者自审替代。

### 独立复审与修复记录

- 第一轮双路独立复审约 7 分钟：需求复审与代码质量复审共报告 10 条，按根因去重为 6 组；`0 Critical / 4 Important / 2 Minor`。4 组 Important 分别是证据可能越出返回学生范围、密集节点跨越掌握度分带、非默认考试/学生范围刷新后丢失、交互与 1000 节点验收证据不完整；2 组 Minor 是分组树不可折叠，以及详情加权说明/完整图表文字摘要/主题色令牌不完整。
- 修复按 6 个根因一次完成：Graph rows/evidence 同时拒绝范围外学生；地址可验证并恢复 3×3 范围组合且清理未知参数；四个分带使用累计行高并保持纵向范围互不重叠；分组树允许折叠；Canvas 从 CSS 主题令牌取色并提供节点总数、四档数量、覆盖率和当前选择文字摘要；详情明确“按当前标签证据加权”。
- RED 轮：5 个文件失败，`7 failed / 23 passed`，逐项复现上述缺口。GREEN 轮：受影响单元检查 `49 passed / 0 failed`（约 9 秒），typecheck 通过（约 16 秒），lint 通过（约 8 秒）。未重复整套前端或后端大测试。
- 浏览器受影响复测：第一次 7 个知识图谱场景中 5 个通过，2 个验收脚本断言暴露“画布扫描可能连续命中”和“全景恢复后拖动截图可能不变”；调整为稳定的请求下限和缩放态拖动后，定向复测 `2 passed`（约 9 秒），合并证据为本轮 7 个场景全部通过。1000 节点附件现记录 `performance.now()` 的首次渲染、模式切换、缩放、拖动和选择耗时及固定上限。
- 第一轮去重问题 6 组已全部关闭；剩余工作为一次最终双路独立复审、用户短测，以及通过后的 integration/PR/主线同步。若最终复审仍有 Important，按项目规则停止并汇报，不再自动进入第三轮修复。
- 最终双路复审约 6 分钟，去重后仍有 `0 Critical / 4 Important / 4 Minor`，按项目规则停止并向用户汇报。4 组 Important 是：同范围刷新成功后旧 evidence 未清除、Graph row 的题目引用未校验考试范围、浏览器缺少无标签/部分失败及候选 SHA/测试机信息、ECharts 首次骨架与局部失败隔离不足。用户在 2026-07-16 明确要求“继续修复”，因此启动一轮新的、经用户决定授权的定向修复，不把它解释为自动无限循环。
- 用户授权后的定向修复约 36 分钟：API RED 为 `1 failed / 14 passed`，复现范围外考试引用；store RED 为 `1 failed / 6 passed`，复现同范围成功刷新保留旧证据；route RED 为 `1 failed / 12 passed`，复现混合旧参数被静默忽略；view RED 分别复现深链接首次失败后无法重试恢复、首次加载无画布/inspector 骨架；component RED 复现 ECharts 初始化异常逃出组件边界。各垂直切片均在最小实现后转绿。
- 修复后的受影响验证：5 个单元文件 `49 passed / 0 failed`（约 17 秒），随后补齐 setOption 局部失败证据，组件文件 `7 passed / 0 failed`（约 7 秒）；typecheck 通过（约 21 秒），lint 通过（约 14 秒）。知识图谱 Chromium 现为 `8 passed / 0 failed`（约 17 秒），新增无标签与局部失败恢复场景；1000 节点附件增加候选 SHA、操作系统/架构/CPU/内存、浏览器与视口，Canvas 点击网格缩小到 16px 以覆盖最小 28px 节点。未重复整套前端、后端或大范围 smoke。
- 定向修复去重后的 4 组 Important 已全部关闭；3 个代码/测试 Minor 同批关闭。验收卡候选 SHA 与列表格式按交接协议留到独立复审通过后的用户验收证据提交统一更新，避免在已复审 SHA 形成前伪造验收锚点。剩余工作为冻结新候选、一次用户授权后的独立复审、用户短测和 integration/PR/主线同步。
- 用户授权后的冻结差异双路复审确认上轮 4 组 Important 主路径均已关闭，但 Standards 报告 `0 Critical / 3 Important / 1 Minor`，Spec 报告 `0 Critical / 1 Important / 1 Minor`；去重为 3 组 Important：resize/ResizeObserver/teardown 的局部失败仍不完整、`ARCHITECTURE.md` 遗留旧 URL 事实和重复编号、未提交工作树不能把旧 HEAD 写成实际受测 SHA。Git 元数据当时由桌面环境设为只读，已停止并向用户说明；用户随后明确授权当前 P2-15 写 `.git`，同时继续禁止 `user_data`，文档治理任务继续运行。
- Git 授权后的最终定向修复：resize 重试先以 `1 failed / 7 passed` 复现只重绘不 resize；observe 重建以 `1 failed / 8 passed` 复现失效 observer 残留；异常 off 后 dispose 以 `1 failed / 9 passed` 复现清理短路，三条分别转绿到组件 `10 passed`。旧 workbench 地址混入额外受控参数先以 `1 failed / 13 passed` 复现，修复后 route `14 passed`。架构事实改为完整 3×3 受控 URL 与成功刷新清证据，并修正序号。冻结前受影响回归为 5 文件 `54 passed / 0 failed`（约 10 秒），typecheck 通过（约 16 秒），lint 通过（约 11 秒）；未运行大范围测试。
- 用户于 2026-07-16 明确决定“定向修复，不再复审，然后合并”。本轮不再启动独立复审，按用户决定关闭最后 `1 Important / 2 Minor` 后直接进入 integration；该决定不扩大真实数据、直接 push main、force push 或破坏性操作权限。
- 最后定向修复 RED/GREEN：节点选择先以组件 `1 failed / 9 passed` 复现整图 `setOption`，随后改为 ECharts highlight/downplay 局部选择并转为 `10 passed`；错误候选 SHA 先令 1000 节点基准按预期失败，再改为附件始终读取真实 `git rev-parse HEAD`，环境值只做一致性校验；四处相同图表降级赋值收敛为统一边界函数。受影响验证为组件 `10 passed`（约 7 秒）、typecheck（约 17 秒）、lint（约 11 秒）、知识图谱 Chromium `8 passed`（约 15 秒）。提交后的真实 HEAD 千节点附件仍须作为 integration 前最终证据重跑。

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P2-15
**交接状态：** waiting_review
**功能提交：** branch_head
**自动验证：** passed
**独立复审：** pending
**用户验收：** pending
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
