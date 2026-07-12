# P2-03 App Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**执行包：** P2-03
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** yes
**计划基线：** b0d188b9568ff0cc6925825476904648daf282b1
**用户自测：** quick
**自测清单：** docs/user-testing/checkpoints/P2-03-v1.5.0-app-shell-quick-check.md

**Goal:** 在非生产 Vue 前端中建立可响应、可访问的 App Shell、七项路由导航和经 sessions API 验证后可刷新恢复的全局考试会话上下文。

**Architecture:** 路由配置与导航配置分离但共享稳定元数据；`AppShell` 只负责顶部栏、左右栏位和响应式面板状态，业务占位页不进入壳层内部。Pinia session Store 通过仅限 `GET /api/sessions` 的轻量适配器加载活动会话，只持久化 session ID，并在 API 成功验证后才暴露当前会话；P2-04 可替换适配器底层而不改变 Store 公共语义。

**Tech Stack:** Vue 3.5.39, TypeScript 6.0.3, Vite 8.1.4, Vue Router 5.1.0, Pinia 3.0.4, Element Plus 2.14.3, Vitest 4.1.10, Playwright 1.61.1, CSS Custom Properties, Python 3.12 repository guards.

## Global Constraints

- 实现必须符合 `docs/superpowers/specs/2026-07-12-p2-03-app-shell-design.md` 和 `docs/ui/STYLE.md`；设计冲突以数据准确、教师工作流和已批准规格为先。
- 一级导航固定为工作台、阅卷、考试、学生、分析、题库与训练、智能体与自动化；设置固定在侧栏底部。
- “智能体与自动化”只显示禁用未来入口，不实现聊天、任务、规则或执行记录。
- 无有效会话时保持“未选择”；不得自动选择第一场考试。持久化候选必须经 `/api/sessions` 成功响应验证后才能成为当前会话。
- P2-03 只实现 sessions GET 专用适配器；不得实现 P2-04 的通用 API Client、统一错误体、request ID、重试、轮询、取消或 Job Store。
- 不迁移任何 Streamlit 业务页面；占位页不得伪造统计、业务按钮或后端数据。
- 保留 P2-02 展示页并迁至 `/design-system`；继续按需导入 Element Plus 样式，不全量注册组件库。
- 不新增依赖，不修改锁文件、后端、数据库 Schema、`运行.bat`、生产入口、评分规则、会话状态含义或 `user_data/`。
- 所有新样式使用 P2-02 Token；除 `tokens.css` 外不得增加颜色字面量，间距、圆角和阴影不得散落裸值。
- 1440×900、1280×800、1024×768、768×1024、390×844 必须无页面级横向溢出；低于 1024px 使用互斥覆盖面板。
- 自动测试只使用合成 sessions 响应；真实两库只读比较大小、UTC 修改时间和 SHA-256。

---

### Task 1: Claim P2-03 with a plan-only commit

**Files:**
- Create: `docs/superpowers/plans/2026-07-12-p2-03-app-shell-implementation.md`

**Interfaces:**
- Consumes: approved design on merged baseline `b0d188b9568ff0cc6925825476904648daf282b1`.
- Produces: the unique P2-03 plan identity and immutable handoff baseline used by `tools/handoff_status.py`.

- [x] **Step 1: Record real-database fingerprints without opening SQLite**

Use read-only `Get-Item` and `Get-FileHash -Algorithm SHA256` against the root checkout databases. Record size, UTC modification time and SHA-256 in the execution log only.

- [x] **Step 2: Verify the isolated common baseline and stash baseline**

Run:

```powershell
git status --short --branch
git rev-list --left-right --count HEAD...origin/main
git status --short -- user_data
git stash list --format=%H
```

Expected: `codex/p2-03-app-shell`, ahead/behind `0 0`, no worktree-local `user_data/` changes, and exactly the two stash SHAs recorded in the handoff block.

- [x] **Step 3: Reuse the verified front-end baseline only after confirming no target drift**

The pre-plan baseline ran lint, typecheck, build, 27 Vitest tests and 6 Playwright tests successfully. `git diff 239bcd65b621c4497a1c771526ea974e47263a20..b0d188b9568ff0cc6925825476904648daf282b1 -- frontend tests/test_frontend_design_system.py docs/ui/STYLE.md docs/superpowers/packages/phase-2-execution-packages.md` must remain empty; otherwise rerun the complete front-end baseline before claiming.

- [x] **Step 4: Commit only this plan as the claim commit**

```powershell
git add -- docs/superpowers/plans/2026-07-12-p2-03-app-shell-implementation.md
git diff --cached --name-only
git commit -m "docs: claim P2-03 app shell"
```

Expected staged file list: only this plan. This must be the first first-parent commit after the `origin/main` merge base.

### Task 2: Establish navigation and router contracts

**Files:**
- Create: `frontend/src/navigation.ts`
- Create: `frontend/src/router/index.ts`
- Create: `frontend/src/views/RoutePlaceholderView.vue`
- Create: `frontend/src/views/NotFoundView.vue`
- Create: `frontend/src/__tests__/navigation-router.spec.ts`

**Interfaces:**
- Consumes: approved route table and existing `ComponentShowcase.vue`.
- Produces: `navigationItems`, `settingsNavigationItem`, `createAppRouter(history?)`, root redirect, `/design-system`, and catch-all 404.

- [ ] **Step 1: Write the failing navigation and router test**

Create `frontend/src/__tests__/navigation-router.spec.ts` with these assertions:

```ts
import { createMemoryHistory } from 'vue-router'
import { describe, expect, it } from 'vitest'

import { navigationItems, settingsNavigationItem } from '../navigation'
import { createAppRouter } from '../router'

describe('P2-03 navigation', () => {
  it('keeps the approved seven-item order and one disabled future entry', () => {
    expect(navigationItems.map(({ label }) => label)).toEqual([
      '工作台', '阅卷', '考试', '学生', '分析', '题库与训练', '智能体与自动化',
    ])
    expect(navigationItems.filter(({ availability }) => availability === 'future')).toEqual([
      expect.objectContaining({ label: '智能体与自动化', path: undefined }),
    ])
    expect(settingsNavigationItem).toMatchObject({ label: '设置', path: '/settings' })
  })

  it.each([
    ['/', '/workbench'],
    ['/grading', '/grading'],
    ['/design-system', '/design-system'],
    ['/missing/deep/path', '/missing/deep/path'],
  ])('resolves %s safely', async (target, expectedPath) => {
    const router = createAppRouter(createMemoryHistory())
    await router.push(target)
    await router.isReady()
    expect(router.currentRoute.value.fullPath).toBe(expectedPath)
    expect(router.currentRoute.value.meta.title).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run the test and verify RED**

Run `npm run test -- src/__tests__/navigation-router.spec.ts --maxWorkers=1`.

Expected: FAIL because `navigation.ts` and `router/index.ts` do not exist.

- [ ] **Step 3: Implement the typed navigation source**

Create `frontend/src/navigation.ts`:

```ts
export type NavigationAvailability = 'available' | 'future'

export interface NavigationItem {
  id: string
  label: string
  symbol: string
  availability: NavigationAvailability
  path?: string
  futureReason?: string
}

export const navigationItems: readonly NavigationItem[] = [
  { id: 'workbench', label: '工作台', symbol: '工', availability: 'available', path: '/workbench' },
  { id: 'grading', label: '阅卷', symbol: '阅', availability: 'available', path: '/grading' },
  { id: 'exams', label: '考试', symbol: '考', availability: 'available', path: '/exams' },
  { id: 'students', label: '学生', symbol: '生', availability: 'available', path: '/students' },
  { id: 'analytics', label: '分析', symbol: '析', availability: 'available', path: '/analytics' },
  { id: 'question-bank', label: '题库与训练', symbol: '题', availability: 'available', path: '/question-bank' },
  {
    id: 'agents',
    label: '智能体与自动化',
    symbol: '智',
    availability: 'future',
    futureReason: '未来能力，本阶段暂不开放',
  },
] as const

export const settingsNavigationItem: NavigationItem = {
  id: 'settings', label: '设置', symbol: '设', availability: 'available', path: '/settings',
}
```

- [ ] **Step 4: Implement the router and shared views**

Create a route factory in `frontend/src/router/index.ts` using `createRouter`, `createWebHistory`, `RouterHistory`, lazy-loaded placeholder/404 views, and a direct `/design-system` route to `ComponentShowcase.vue`. Each route meta must provide `title`, `description`, and `breadcrumb`; `/` redirects to `/workbench`; the catch-all meta title is `页面未找到`.

`RoutePlaceholderView.vue` reads route meta and renders a `main` landmark, one `h1` with `tabindex="-1"`, the exact explanation `此工作区将在后续执行包中迁移。当前应用外壳和考试上下文已经可用。`, and the current session name from the Store or `未选择当前考试`.

`NotFoundView.vue` renders `StatePanel kind="error"`, explains that no data was changed, and uses `router.push('/workbench')` for `返回工作台`.

- [ ] **Step 5: Run GREEN and commit the route contract**

Run:

```powershell
npm run test -- src/__tests__/navigation-router.spec.ts --maxWorkers=1
npm run typecheck
git add -- frontend/src/navigation.ts frontend/src/router frontend/src/views frontend/src/__tests__/navigation-router.spec.ts
git commit -m "feat: add P2-03 navigation routes"
```

Expected: focused tests and typecheck pass.

### Task 3: Implement the session adapter and persistence Store

**Files:**
- Create: `frontend/src/api/sessions.ts`
- Create: `frontend/src/stores/session.ts`
- Create: `frontend/src/__tests__/session-store.spec.ts`

**Interfaces:**
- Produces: `SessionSummary`, `SessionListResponse`, `SessionLoader`, `fetchSessions()`, `SESSION_STORAGE_KEY`, and `useSessionStore()`.
- Store public API: `sessions`, `selectedSessionId`, `currentSession`, `loadState`, `errorMessage`, `initialize(loader?)`, `selectSession(id)`, `clearSelection()`.

- [ ] **Step 1: Write failing adapter and Store tests**

Create `frontend/src/__tests__/session-store.spec.ts`. Use `setActivePinia(createPinia())`, clear `localStorage` before each test, and cover exactly these cases:

```ts
const sessions = [
  { id: 7, name: '七年级数学期末质量监测', status: 'configured', is_deleted: false,
    deleted_at: null, created_at: null, updated_at: null },
  { id: 9, name: '超长考试名称用于验证顶部栏不会遮挡主要操作与检查器入口', status: 'grading',
    is_deleted: false, deleted_at: null, created_at: null, updated_at: null },
]

it('restores only an API-validated persisted id', async () => {
  localStorage.setItem(SESSION_STORAGE_KEY, '9')
  const store = useSessionStore()
  await store.initialize(async () => sessions)
  expect(store.currentSession?.id).toBe(9)
})

it('clears a stale id and never selects the first session', async () => {
  localStorage.setItem(SESSION_STORAGE_KEY, '99')
  const store = useSessionStore()
  await store.initialize(async () => sessions)
  expect(store.selectedSessionId).toBeNull()
  expect(localStorage.getItem(SESSION_STORAGE_KEY)).toBeNull()
})

it('keeps the candidate for retry without exposing it after failure', async () => {
  localStorage.setItem(SESSION_STORAGE_KEY, '7')
  const store = useSessionStore()
  await store.initialize(async () => { throw new Error('network detail') })
  expect(store.currentSession).toBeNull()
  expect(store.loadState).toBe('error')
  expect(store.errorMessage).not.toContain('network detail')
  expect(localStorage.getItem(SESSION_STORAGE_KEY)).toBe('7')
  await store.initialize(async () => sessions)
  expect(store.currentSession?.id).toBe(7)
})
```

Also mock `globalThis.fetch` and assert `fetchSessions()` requests only `/api/sessions`, accepts the exact public response, and throws `SessionReadError` for non-2xx or malformed items.

- [ ] **Step 2: Run the test and verify RED**

Run `npm run test -- src/__tests__/session-store.spec.ts --maxWorkers=1`.

Expected: FAIL because the adapter and Store do not exist.

- [ ] **Step 3: Implement the narrow sessions adapter**

Create `frontend/src/api/sessions.ts` with the approved interfaces and an `isSessionSummary()` type guard. `fetchSessions()` must call `fetch('/api/sessions', { headers: { accept: 'application/json' } })`, reject non-success responses without including response text, validate `{ items, total }`, and return only items where `is_deleted === false`. Use a stable `SessionReadError('无法读取考试列表')`; do not parse the global FastAPI error body.

- [ ] **Step 4: Implement the Store with validated recovery**

Create `frontend/src/stores/session.ts` with `defineStore('session', () => ...)`. Use:

```ts
export const SESSION_STORAGE_KEY = 'ai-grading:selected-session:v1'
export type SessionLoadState = 'idle' | 'loading' | 'ready' | 'error'

function readPersistedId(): number | null {
  const raw = localStorage.getItem(SESSION_STORAGE_KEY)
  if (!raw || !/^\d+$/.test(raw)) return null
  const value = Number(raw)
  return Number.isSafeInteger(value) && value > 0 ? value : null
}
```

During `initialize`, set `selectedSessionId` to `null` before awaiting the loader. On success, restore only if the candidate exists in the returned list; otherwise remove the key. On failure, keep the key, expose no candidate as current, and set the stable teacher-facing error `考试列表暂时无法读取。已保存的选择没有丢失，可以重新加载。`. `selectSession` rejects IDs absent from the loaded list, persists valid IDs, and removes the key for `null`.

- [ ] **Step 5: Run GREEN and commit the session boundary**

```powershell
npm run test -- src/__tests__/session-store.spec.ts --maxWorkers=1
npm run typecheck
git add -- frontend/src/api/sessions.ts frontend/src/stores/session.ts frontend/src/__tests__/session-store.spec.ts
git commit -m "feat: add P2-03 session context"
```

Expected: focused tests and typecheck pass; no general API client exists.

### Task 4: Build the accessible App Shell and wire the application

**Files:**
- Create: `frontend/src/layouts/AppShell.vue`
- Create: `frontend/src/components/shell/AppTopbar.vue`
- Create: `frontend/src/components/shell/AppNavigation.vue`
- Create: `frontend/src/components/shell/SessionInspector.vue`
- Create: `frontend/src/components/shell/__tests__/app-shell.spec.ts`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/main.ts`

**Interfaces:**
- Consumes: route meta, navigation config, `useSessionStore`, P2-02 `StatePanel` and `FeedbackBanner`.
- Produces: `app-shell`, `app-topbar`, `app-navigation`, `session-inspector`, `main-workspace`, mutually exclusive overlay panels, and application startup initialization.

- [ ] **Step 1: Write the failing shell component test**

Mount `AppShell` with a memory router and active Pinia. Assert:

```ts
expect(host.querySelector('[data-testid="app-shell"]')).not.toBeNull()
expect(host.querySelectorAll('[data-testid="primary-navigation"] a')).toHaveLength(6)
expect(host.textContent).toContain('智能体与自动化')
expect(host.querySelector('[aria-disabled="true"]')?.textContent).toContain('智能体与自动化')
expect(host.querySelector('label[for="current-session"]')?.textContent).toBe('当前考试')
expect(host.querySelector('[data-testid="session-inspector"]')?.textContent).toContain('未选择当前考试')
```

Trigger the navigation and inspector buttons and assert `aria-expanded` changes. In overlay mode, opening one closes the other; dispatch `Escape` and assert the active panel closes and focus returns to its trigger.

- [ ] **Step 2: Run the test and verify RED**

Run `npm run test -- src/components/shell/__tests__/app-shell.spec.ts --maxWorkers=1`.

Expected: FAIL because shell components do not exist.

- [ ] **Step 3: Implement navigation, topbar and inspector components**

`AppNavigation.vue` renders available items as `RouterLink`, the future item as a focusable disabled explanation with `aria-disabled="true"`, and settings in a bottom group. In collapsed mode, every symbol has `aria-hidden="true"` and the interactive item retains the full label as `aria-label` and `title`.

`AppTopbar.vue` renders a skip link to `#main-workspace`, breadcrumb/page title, a permanent `label for="current-session"`, and a native `select` whose first option is `未选择`. It disables the select while loading, calls `sessionStore.selectSession`, and emits navigation/inspector toggle events. Both toggle buttons expose `aria-expanded` and `aria-controls`.

`SessionInspector.vue` uses `StatePanel` for loading/empty/error. Error uses `retryLabel="重新加载考试列表"` and emits retry. Ready state renders only `currentSession.name`, `currentSession.status`, and the explanation `后续业务页面将继续使用这一考试上下文。`.

- [ ] **Step 4: Implement AppShell responsive state and focus safety**

`AppShell.vue` owns `navigationOpen`, `inspectorOpen`, and a `matchMedia('(max-width: 1023px)')` listener. Desktop defaults both open; overlay mode defaults closed. `openNavigation()` and `openInspector()` close the other overlay panel. Save `document.activeElement` before opening; on Escape or backdrop close, use `nextTick()` to return focus. Remove media and key listeners in `onUnmounted`.

The template order must be topbar, navigation, `main#main-workspace` with `RouterView`, inspector, then a single backdrop. The main route heading receives focus after `route.fullPath` changes.

- [ ] **Step 5: Wire Pinia, Router and startup**

Update `main.ts` to create and register Pinia before Router:

```ts
const app = createApp(App)
const pinia = createPinia()
const router = createAppRouter()

app.use(pinia)
app.use(router)
app.mount('#app')
```

Keep the existing Element Plus base/button/input styles and add only styles actually used by the shell (select has native styling; no full Element bundle). Update `App.vue` so `ElConfigProvider` wraps `AppShell`; call `useSessionStore().initialize()` once from the shell startup and expose retry only through the Store action.

- [ ] **Step 6: Run GREEN and commit the shell**

```powershell
npm run test -- src/components/shell/__tests__/app-shell.spec.ts src/__tests__/navigation-router.spec.ts src/__tests__/session-store.spec.ts --maxWorkers=1
npm run typecheck
npm run lint
git add -- frontend/src/layouts frontend/src/components/shell frontend/src/App.vue frontend/src/main.ts
git commit -m "feat: build P2-03 app shell"
```

Expected: focused tests, typecheck and lint pass.

### Task 5: Add Token-governed responsive styling and browser acceptance

**Files:**
- Modify: `frontend/src/styles/tokens.css`
- Create: `frontend/src/styles/app-shell.css`
- Modify: `frontend/src/main.ts`
- Replace: `frontend/e2e/app.spec.ts`
- Create: `frontend/e2e/app-shell.spec.ts`
- Modify: `frontend/src/__tests__/App.spec.ts`
- Create: `tests/test_frontend_app_shell.py`

**Interfaces:**
- Produces: shell sizing tokens, five-viewport behavior, synthetic sessions browser contract, focus/overflow guards, and repository scope guards.

- [ ] **Step 1: Write failing repository guards**

Create `tests/test_frontend_app_shell.py` to assert:

```python
def test_p2_03_has_one_navigation_source_and_no_general_api_client() -> None:
    navigation = (SRC / "navigation.ts").read_text(encoding="utf-8")
    assert navigation.count("availability: 'future'") == 1
    assert "智能体与自动化" in navigation
    assert not (SRC / "api" / "client.ts").exists()
    assert not (SRC / "stores" / "jobs.ts").exists()

def test_shell_uses_tokens_and_keeps_showcase_route() -> None:
    css = (SRC / "styles" / "app-shell.css").read_text(encoding="utf-8")
    assert not COLOR_LITERAL.search(css)
    assert "var(--shell-navigation-width)" in css
    assert "var(--shell-inspector-width)" in css
    router = (SRC / "router" / "index.ts").read_text(encoding="utf-8")
    assert "'/design-system'" in router
    assert "ComponentShowcase" in router
```

Reuse `COLOR_LITERAL` semantics from `test_frontend_design_system.py` rather than importing application code.

- [ ] **Step 2: Run the guard and verify RED**

Run `D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_frontend_app_shell.py -q`.

Expected: FAIL because `app-shell.css` and its tokens do not exist.

- [ ] **Step 3: Add shell tokens and responsive CSS**

Add only these reusable dimensions to `tokens.css`: `--shell-topbar-height: 60px`, `--shell-navigation-width: 232px`, `--shell-navigation-collapsed-width: 60px`, `--shell-inspector-width: 360px`, `--shell-inspector-compact-width: 320px`, and `--shell-overlay-z-index: 30`.

Create `app-shell.css` using existing color, spacing, border, radius, shadow and duration variables. Required layout rules:

```css
.app-shell {
  display: grid;
  min-width: 0;
  min-height: 100vh;
  grid-template: var(--shell-topbar-height) minmax(0, 1fr) /
    var(--shell-navigation-width) minmax(0, 1fr) var(--shell-inspector-width);
}

.main-workspace { min-width: 0; overflow: auto; }

@media (min-width: 1024px) and (max-width: 1279px) {
  .app-shell { grid-template-columns: var(--shell-navigation-collapsed-width) minmax(0, 1fr) var(--shell-inspector-compact-width); }
}

@media (max-width: 1023px) {
  .app-shell { display: block; padding-block-start: var(--shell-topbar-height); }
  .app-navigation, .session-inspector { position: fixed; inset-block: var(--shell-topbar-height) 0; z-index: var(--shell-overlay-z-index); }
}
```

Complete the CSS with Token-only borders/backgrounds, panel transforms, mutually exclusive backdrop, 390px wrapping, independent desktop scroll, ellipsis with full accessible names, visible focus, and reduced-motion compatibility. Import it after `base.css` in `main.ts`.

- [ ] **Step 4: Replace showcase-only browser checks with shell-aware tests**

Keep P2-02 focus and design-system assertions under `/design-system`. Add `app-shell.spec.ts` that intercepts `/api/sessions` with two synthetic sessions, then for all five viewports checks no horizontal overflow, page/console errors empty, current session selection, refresh recovery, and long-name containment.

Desktop checks verify navigation and inspector widths remain in approved ranges. Narrow checks open navigation, open inspector and prove the first closes; press Escape and verify focus returns. Add dedicated tests for stale localStorage cleanup, API failure preserving the saved candidate without presenting it, successful retry, disabled future entry, `/settings`, and 404 return.

- [ ] **Step 5: Update the root App test and confirm GREEN**

Replace the P2-02 root showcase assumption in `App.spec.ts` with a memory-router/Pinia mount that mocks a successful empty sessions loader and asserts App Shell landmarks. Preserve ComponentShowcase unit coverage through a direct component test or `/design-system` router test.

Run:

```powershell
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_frontend_design_system.py tests\test_frontend_app_shell.py -q
npm run test -- --maxWorkers=1
npm run e2e
npm run lint
npm run typecheck
npm run build
```

Expected: all commands exit 0 and all five viewports have no page-level horizontal overflow.

- [ ] **Step 6: Commit responsive and browser acceptance**

```powershell
git add -- frontend/src/styles frontend/src/main.ts frontend/src/__tests__/App.spec.ts frontend/e2e tests/test_frontend_app_shell.py
git commit -m "test: verify P2-03 shell behavior"
```

### Task 6: Document the implemented boundary and prepare independent review

**Files:**
- Modify: `frontend/README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/plans/2026-07-12-p2-03-app-shell-implementation.md`

**Interfaces:**
- Produces: contributor guidance, implemented architecture facts, a verified feature SHA, `waiting_review`, and later the reviewed anchor for quick user testing.

- [ ] **Step 1: Document usage and non-goals**

Update `frontend/README.md` with route entry points, session persistence semantics, the session-only adapter boundary, Token-only shell styling, synthetic browser testing, and explicit P2-04/business-page exclusions. Update `ARCHITECTURE.md` only with the implemented P2-03 facts: App Shell/routes/session context exist, Vue remains non-production, and the general API client does not yet exist.

- [ ] **Step 2: Run the feature-branch verification gate**

```powershell
npm run lint
npm run typecheck
npm run test
npm run build
npm run e2e
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_frontend_foundation.py tests\test_frontend_portable_packaging.py tests\test_frontend_design_system.py tests\test_frontend_app_shell.py -q
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe tools\smoke_check.py --skip-tests
git diff --check
```

Expected: every command exits 0. If default Vitest concurrency exceeds the command timeout, investigate active processes first; the final evidence must still include an unmodified `npm run test` pass.

- [ ] **Step 3: Verify real data and commit the feature state**

Repeat root database size/UTC/SHA-256 checks and require exact equality with Task 1. Confirm `git status --short -- user_data` is empty. Set handoff to `waiting_review`, `功能提交: branch_head`, `自动验证: passed`, `独立复审: pending`, `用户验收: pending`, `真实数据指纹: unchanged`, preserve the immutable stash baseline, and commit all P2-03 source/test/docs changes without `user_data/`.

- [ ] **Step 4: Perform independent review and fix findings**

Review `origin/main..HEAD` against the approved design and this plan. Critical/Important findings must be zero. Any fix must begin with a failing regression test and rerun the focused test plus affected front-end commands.

- [ ] **Step 5: Create the user-test anchor commit**

Record the reviewed feature SHA in a plan-only commit; set `交接状态: waiting_user`, `自动验证: passed`, `独立复审: passed`, `用户验收: pending`. Run `tools/handoff_status.py` and require `ok=true`.

### Task 7: Complete quick user acceptance and final package handoff

**Files:**
- Create: `docs/user-testing/checkpoints/P2-03-v1.5.0-app-shell-quick-check.md`
- Modify: `docs/superpowers/plans/2026-07-12-p2-03-app-shell-implementation.md`

**Interfaces:**
- Consumes: reviewed anchor SHA and synthetic sessions browser environment.
- Produces: explicit user acceptance evidence and `verified_pending_integration` handoff.

- [ ] **Step 1: Generate the versioned checklist only after the shell is verified**

The checklist must include package, reviewed SHA, synthetic data source, exact start/stop commands, URL, visible P2-03 marker, and numbered checks for navigation order, settings location, session selection/refresh, side-panel behavior, long names, disabled future entry, 404, focus and narrow-screen overflow. Include the feedback format and machine result block required by `tools/handoff_status.py`.

- [ ] **Step 2: Run the reviewed version for user inspection**

Start only the verified synthetic sessions environment on loopback, open the shell, and ask the user to complete the 5–10 minute checklist. Do not read real databases or infer a pass from silence.

- [ ] **Step 3: Commit passed user evidence**

After explicit user confirmation, commit only the declared checkpoint file with the reviewed SHA and `passed` machine result.

- [ ] **Step 4: Create the plan-only final handoff commit**

Set `交接状态: verified_pending_integration`, `功能提交` to the direct parent's full SHA, `自动验证: passed`, `独立复审: passed`, `用户验收: passed`, `真实数据指纹: unchanged`, and `夜间动作: independent_candidate_allowed`. Commit only this plan and run:

```powershell
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-12-p2-03-app-shell-implementation.md --repo .
```

Expected: exit 0, single-line JSON, `ok=true`, and no issues.

### Task 8: Integrate P2-03 through GitHub main and synchronize the baseline

**Files:**
- Modify on integration branch: `docs/superpowers/packages/EXECUTION_INDEX.md`
- Modify on integration branch only if needed: shared architecture/evidence files

**Interfaces:**
- Consumes: complete verified P2-03 commit chain.
- Produces: P2-03 merged into GitHub `main`, updated authoritative Index, and synchronized local baseline.

- [ ] **Step 1: Merge the verified feature chain into a fresh integration branch**

Create the integration branch from latest `origin/main`, merge only P2-03, run `tools/handoff_status.py` against the feature worktree, and confirm no `user_data/` path enters the diff.

- [ ] **Step 2: Run integration gates**

Run the focused Python guards, all front-end quality commands, full `tools/smoke_check.py`, `git diff --check`, and before/after root database fingerprint comparison. Because P2-03 changes shared front-end routing and root application composition, do not reuse an older full-smoke result.

- [ ] **Step 3: Update the authoritative package state**

Set P2-03 to `merged`, update Phase 2 counts/current queue/next action in `EXECUTION_INDEX.md`, and do not copy dynamic state into Phase maps or the nightly matrix.

- [ ] **Step 4: Push, create and merge the integration PR**

Push the integration branch, create a PR targeting GitHub `main`, verify its exact head SHA and checks, then merge without directly pushing `main`.

- [ ] **Step 5: Synchronize and safely clean up**

Fetch/prune, fast-forward root `main` and active worktrees to `origin/main`, delete only remote integration branches and local branches proven merged by `git branch --merged origin/main`, and remove linked worktrees only after source status, `user_data/` and reparse-point checks pass.

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P2-03
**交接状态：** in_progress
**功能提交：** none
**自动验证：** pending
**独立复审：** pending
**用户验收：** pending
**真实数据指纹：** not_touched
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
