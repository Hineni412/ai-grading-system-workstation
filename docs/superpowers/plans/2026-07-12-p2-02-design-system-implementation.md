# P2-02 Design System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**执行包：** P2-02
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** yes
**计划基线：** 13be413d8f5b306741640415e14d1bb841e050cb
**用户自测：** quick
**自测清单：** docs/user-testing/checkpoints/P2-02-v1.5.0-design-system-quick-check.md

**Goal:** 将 `docs/ui/STYLE.md` 固化为唯一设计 Token、Element Plus 主题和可复用基础状态控件，并用组件展示页完成可访问性与五视口验证。

**Architecture:** 产品色彩、字体、间距、圆角、阴影和动效只在 `tokens.css` 定义，`element-theme.css` 负责把产品 Token 映射到 Element Plus，`base.css` 提供全局可访问性基础。按钮和输入继续复用 Element Plus；`AppField`、`StatusBadge`、`StatePanel` 和 `FeedbackBanner` 只封装产品语义。`ComponentShowcase` 作为 P2-02 唯一可见页面，不建立 App Shell、API Client 或业务路由。

**Tech Stack:** Vue 3.5.39, TypeScript 6.0.3, Vite 8.1.4, Element Plus 2.14.3, Vitest 4.1.10, Playwright 1.61.1, CSS Custom Properties, Python 3.12 repository guards.

## Global Constraints

- `docs/ui/STYLE.md` 是颜色、字号、间距、圆角、阴影、状态和响应式的唯一视觉权威；主强调色固定为 `#2563EB`。
- 只实现 Token、Element Plus 主题、基础状态控件和展示页；不实现 P2-03 App Shell、P2-04 API Client 或任何业务页面。
- 不复制 `pages_shared/shared_styles.py` 的旧蓝紫色板，不使用渐变、营销 Hero、大圆角或卡片堆叠。
- 除 `frontend/src/styles/tokens.css` 外，P2-02 新增源码不得声明十六进制、RGB 或 HSL 色值；常用间距、圆角和阴影必须引用 Token。
- 状态必须有文字；键盘焦点可见；普通文字对比度至少 4.5:1，大文字至少 3:1；错误字段必须用 ARIA 关联。
- 展示页必须在 1440×900、1280×800、1024×768、768×1024 和 390×844 无页面级横向溢出。
- 不修改 `运行.bat`、Streamlit、FastAPI、数据库、评分规则、依赖版本或锁文件，不读取或写入真实业务数据。
- 前端验证使用生成展示内容；真实两库只比较大小、UTC 修改时间和 SHA-256。

---

### Task 1: Claim P2-02 with a plan-only commit

**Files:**
- Create: `docs/superpowers/plans/2026-07-12-p2-02-design-system-implementation.md`

**Interfaces:**
- Consumes: P2-02 `ready` state, approved design `docs/superpowers/specs/2026-07-12-p2-02-design-system-design.md`, baseline `13be413d8f5b306741640415e14d1bb841e050cb`.
- Produces: the unique P2-02 plan and immutable handoff identity used by `tools/handoff_status.py`.

- [x] **Step 1: Record the real-database fingerprints without opening SQLite**

Use read-only `Get-Item` and `Get-FileHash -Algorithm SHA256` against the root checkout's two databases. Keep size, UTC modification time and SHA-256 in the execution log only.

- [x] **Step 2: Establish and verify the isolated baseline**

Run `git status --short --branch`, `git rev-list --left-right --count HEAD...origin/main`, `git status --short -- user_data`, and `git stash list --format=%H`.

Expected: branch `codex/p2-02-design-system`, ahead/behind `0 0`, no worktree-local `user_data/` changes, and the two stash SHAs recorded in the handoff block.

- [x] **Step 3: Verify the existing front-end baseline**

Run `npm ci`, `npm run lint`, `npm run typecheck`, `npm run test`, `npm run build`, `npm run e2e`, and the two existing Python front-end guard files.

Expected: lint/typecheck/build exit 0; 2 Vitest tests pass; 1 Chromium test passes; 4 Python tests pass.

- [x] **Step 4: Commit only this plan as the claim commit**

```powershell
git add -- docs/superpowers/plans/2026-07-12-p2-02-design-system-implementation.md
git diff --cached --name-only
git commit -m "docs: claim P2-02 design system"
```

Expected staged file list: only this plan.

### Task 2: Establish canonical Token and Element Plus theme contracts

**Files:**
- Create: `tests/test_frontend_design_system.py`
- Create: `frontend/src/styles/tokens.css`
- Create: `frontend/src/styles/element-theme.css`
- Create: `frontend/src/styles/base.css`
- Modify: `frontend/src/main.ts`

**Interfaces:**
- Consumes: `STYLE.md` section 9 values and Element Plus `--el-*` CSS variables.
- Produces: canonical product variables such as `--color-accent`, `--space-4`, `--radius-control`, `--shadow-overlay`, and global Element Plus mappings.

- [x] **Step 1: Write the failing repository Token guard**

Create `tests/test_frontend_design_system.py` with these exact checks:

```python
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
TOKENS = SRC / "styles" / "tokens.css"
COLOR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|hsl)a?\(")
RAW_DESIGN_VALUE = re.compile(
    r"(?:margin|padding|gap|border-radius|box-shadow)\s*:\s*(?!0(?:[;\s]|$))\d"
)


def test_design_tokens_define_the_approved_visual_contract() -> None:
    css = TOKENS.read_text(encoding="utf-8")
    required = {
        "--color-accent: #2563eb",
        "--color-accent-hover: #1d4ed8",
        "--space-1: 4px",
        "--space-9: 48px",
        "--radius-control: 6px",
        "--radius-overlay: 12px",
        "--shadow-overlay:",
        "--duration-fast: 100ms",
        "--duration-base: 160ms",
    }
    lowered = css.lower()
    assert all(token in lowered for token in required)


def test_colors_and_design_values_do_not_escape_the_token_file() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*"):
        if path.suffix not in {".css", ".vue"} or path == TOKENS:
            continue
        text = path.read_text(encoding="utf-8")
        if COLOR_LITERAL.search(text) or RAW_DESIGN_VALUE.search(text):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_element_theme_maps_product_tokens() -> None:
    css = (SRC / "styles" / "element-theme.css").read_text(encoding="utf-8")
    for mapping in (
        "--el-color-primary: var(--color-accent)",
        "--el-color-success: var(--color-success)",
        "--el-color-warning: var(--color-warning)",
        "--el-color-danger: var(--color-danger)",
        "--el-border-radius-base: var(--radius-control)",
    ):
        assert mapping in css
```

- [x] **Step 2: Run the guard to verify RED**

Run: `D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_frontend_design_system.py -q`

Expected: FAIL because the three style files do not exist.

- [x] **Step 3: Add the canonical product Token file**

Create `frontend/src/styles/tokens.css`. Define `:root` variables for every color in `STYLE.md` section 9.1; the system font stack; font sizes 12/13/14/16/20/24/32; weights 400/500/600; line heights 1.35/1.5/1.7; spaces 4/8/12/16/20/24/32/40/48; radii 4/6/8/12; 1px border width; control heights 32/36/40; `--shadow-overlay: 0 8px 24px rgba(32, 36, 42, 0.14)`; durations 100/160/220ms; and `--focus-ring: 0 0 0 3px var(--color-accent-subtle)`.

- [x] **Step 4: Map tokens into Element Plus and global base styles**

Create `element-theme.css` with `:root` mappings for primary/success/warning/danger/info, page/surface/fill/text/border, base radius, base font size, component height and transition duration. Add focused overrides for primary button hover/active, disabled opacity, input focus/invalid borders, tag radii, alert borders and overlay shadows. Every value must be `var(...)`, `currentColor`, `transparent`, `inherit`, `none`, `0` or a percentage.

Create `base.css` with box sizing, body margin 0, product font/background/text, `min-width: 0`, `overflow-wrap: anywhere`, visible `:focus-visible`, and a `prefers-reduced-motion: reduce` rule that sets transition/animation duration to 0.

Update `main.ts` to import only Element Plus base, icon, button and input CSS before the product style layers. Import `ElConfigProvider` and Chinese locale in `App.vue`, wrap the showcase with the provider, and do not call `app.use(ElementPlus)` so unused components stay out of the production bundle. The scoped import contract is guarded by `test_element_plus_stays_scoped_to_the_design_system_components`; a full `element-plus/dist/index.css` import or global plugin registration is a test failure.

- [x] **Step 5: Verify GREEN and commit**

Run the Python guard, `npm run lint`, `npm run typecheck`, and `npm run build`.

Expected: all exit 0.

Commit: `feat: add P2-02 design tokens and Element theme`.

### Task 3: Implement accessible field and status primitives

**Files:**
- Create: `frontend/src/components/design-system/AppField.vue`
- Create: `frontend/src/components/design-system/StatusBadge.vue`
- Create: `frontend/src/components/design-system/__tests__/field-status.spec.ts`

**Interfaces:**
- Produces: `AppField` slot props `inputId`, `ariaDescribedby`, `ariaInvalid`; `StatusBadge` prop `tone: 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'ai' | 'teacher'`.

- [x] **Step 1: Write failing component tests**

Mount small test hosts with `createApp`. Assert that `AppField` renders a permanent label, joins hint/error IDs in `aria-describedby`, sets `aria-invalid="true"` when an error exists, and marks required labels. For every `StatusBadge` tone, assert visible text, `data-tone`, and `aria-label="状态：<label>"`.

- [x] **Step 2: Run tests to verify RED**

Run from `frontend/`: `npm run test -- --run src/components/design-system/__tests__/field-status.spec.ts`

Expected: FAIL because both components are missing.

- [x] **Step 3: Implement `AppField`**

Use this public shape:

```vue
<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  id: string
  label: string
  hint?: string
  error?: string
  required?: boolean
}>()

const describedBy = computed(() =>
  [props.hint ? `${props.id}-hint` : '', props.error ? `${props.id}-error` : '']
    .filter(Boolean)
    .join(' ') || undefined,
)
</script>

<template>
  <div class="app-field" :data-invalid="Boolean(error)">
    <label class="app-field__label" :for="id">
      {{ label }}<span v-if="required" aria-hidden="true"> *</span>
    </label>
    <slot
      :input-id="id"
      :aria-describedby="describedBy"
      :aria-invalid="error ? 'true' : undefined"
    />
    <p v-if="hint" :id="`${id}-hint`" class="app-field__hint">{{ hint }}</p>
    <p v-if="error" :id="`${id}-error`" class="app-field__error">{{ error }}</p>
  </div>
</template>
```

Style only with product tokens.

- [x] **Step 4: Implement `StatusBadge`**

Define the exact tone union, require non-empty `label`, render a text-bearing `<span class="status-badge" :data-tone="tone" :aria-label="`状态：${label}`">`, and map tone classes only to product semantic foreground/background tokens.

- [x] **Step 5: Verify GREEN and commit**

Run the new component test, full `npm run test`, lint and typecheck.

Expected: all pass.

Commit: `feat: add accessible field and status primitives`.

### Task 4: Implement empty, loading, error and feedback primitives

**Files:**
- Create: `frontend/src/components/design-system/StatePanel.vue`
- Create: `frontend/src/components/design-system/FeedbackBanner.vue`
- Create: `frontend/src/components/design-system/__tests__/state-feedback.spec.ts`

**Interfaces:**
- Produces: `StatePanel.kind: 'empty' | 'loading' | 'error'`, `retry` event; `FeedbackBanner.tone: 'info' | 'success' | 'warning' | 'error'`, `action` and `dismiss` events.

- [x] **Step 1: Write failing state and feedback tests**

Assert loading exposes `aria-busy="true"` and three skeleton lines; empty state explains the next action; error state displays impact/saved text and emits `retry`; warning/error feedback uses `role="alert"`; info/success uses `role="status"`; optional action and dismiss buttons emit their events.

- [x] **Step 2: Run tests to verify RED**

Run from `frontend/`: `npm run test -- --run src/components/design-system/__tests__/state-feedback.spec.ts`

Expected: FAIL because both components are missing.

- [x] **Step 3: Implement `StatePanel`**

Use props `kind`, `title`, `description`, optional `detail`, optional `retryLabel`; emit `retry`. Loading renders three `.state-panel__skeleton` children and `aria-busy`; error renders the detail and retry button when supplied; empty renders explanation without decorative illustration. Use text labels and Token-only styles.

- [x] **Step 4: Implement `FeedbackBanner`**

Use props `tone`, `title`, `description`, optional `actionLabel`, optional `dismissible`; emit `action` and `dismiss`. Derive role from tone, keep title and description visible, and render text buttons with accessible names. Use semantic Token pairs and no shadows.

- [x] **Step 5: Verify GREEN and commit**

Run the new test, full unit suite, lint, typecheck and Python Token guard.

Expected: all pass.

Commit: `feat: add reusable state and feedback components`.

### Task 5: Build and verify the component showcase

**Files:**
- Create: `frontend/src/components/design-system/ComponentShowcase.vue`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/__tests__/App.spec.ts`
- Modify: `frontend/e2e/app.spec.ts`
- Create: `frontend/src/__tests__/contrast.spec.ts`

**Interfaces:**
- Consumes: all Task 2-4 tokens and components plus Element Plus button/input.
- Produces: visible P2-02 showcase at `/`, deterministic `data-testid` anchors, automated WCAG and viewport evidence.

- [x] **Step 1: Replace the old readiness tests with failing showcase tests**

Unit assertions must require `data-testid="design-system-showcase"`, the six section names `基础 Token / 按钮 / 输入 / 状态徽章 / 空、加载与错误 / 操作反馈`, long Chinese text, one invalid field, one disabled field, all seven badge tones and all feedback/state variants.

In `contrast.spec.ts`, read `tokens.css`, parse the required color variables, calculate relative luminance using the WCAG formula, and assert these normal-text pairs are at least 4.5:1: primary text/surface, secondary text/surface, accent/surface, danger/danger-subtle, primary text/warning-subtle, success/success-subtle, teacher/teacher-subtle, AI/AI-subtle. The warning hue remains available for border/icon emphasis, while warning copy uses primary text because the approved warning foreground is 4.44:1 on its subtle background and therefore cannot be used for normal-size text.

- [x] **Step 2: Run unit tests to verify RED**

Run: `npm run test`

Expected: FAIL because the old App only renders the readiness marker.

- [x] **Step 3: Implement `ComponentShowcase` and wire `App.vue`**

The showcase must:

- use a restrained document header, not a marketing Hero;
- render Token swatches with names and roles;
- show primary/default/text/danger/loading/disabled Element Plus buttons with result-oriented Chinese labels;
- use `AppField` slot props on Element Plus inputs for default, required, disabled and invalid states;
- show all badge, state and feedback variants;
- include an overlong exam name, student name, knowledge-point label, decimal score and multi-sentence failure impact;
- use sections and dividers rather than decorative card wrappers;
- use only Token values in scoped CSS.

Replace `App.vue` with:

```vue
<script setup lang="ts">
import { ElConfigProvider } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

import ComponentShowcase from './components/design-system/ComponentShowcase.vue'
</script>

<template>
  <ElConfigProvider :locale="zhCn">
    <ComponentShowcase />
  </ElConfigProvider>
</template>
```

- [x] **Step 4: Add exact multi-viewport browser checks**

Update `e2e/app.spec.ts` to iterate these named viewports: desktop 1440×900, compact desktop 1280×800, tablet landscape 1024×768, tablet portrait 768×1024, mobile 390×844. For each, assert the showcase is visible, `scrollWidth <= clientWidth`, section headings are visible, a long label is not clipped, Tab reaches an interactive element with non-`none` outline style, the invalid field has `aria-invalid="true"`, and no browser `pageerror` occurs.

- [x] **Step 5: Verify GREEN and commit**

Run unit tests, contrast test, Python Token guard, lint, typecheck, build and Chromium e2e.

Expected: all pass at all five viewports.

Commit: `feat: add P2-02 component showcase`.

### Task 6: Document usage, verify the feature commit and obtain independent review

**Files:**
- Modify: `frontend/README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/superpowers/plans/2026-07-12-p2-02-design-system-implementation.md`

**Interfaces:**
- Produces: contributor rules, implemented architecture facts, feature commit in `waiting_review`, and reviewed SHA for user testing.

- [x] **Step 1: Add usage and non-goal documentation**

Document import order, Token-only rules, component responsibilities, showcase commands and the explicit P2-03/P2-04 exclusions. Update `ARCHITECTURE.md` only with the implemented P2-02 boundary: design tokens, Element theme, primitives and showcase exist but Vue remains non-production.

- [x] **Step 2: Run the feature-branch verification gate**

Run:

```powershell
npm run lint
npm run typecheck
npm run test
npm run build
npm run e2e
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe -m pytest tests\test_frontend_foundation.py tests\test_frontend_portable_packaging.py tests\test_frontend_design_system.py -q
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe tools\smoke_check.py --skip-tests
git diff --check
```

Expected: every command exits 0.

- [x] **Step 3: Verify real data and commit the feature state**

Repeat read-only size/UTC/SHA-256 checks and require both root databases to match Task 1. Confirm `git status --short -- user_data` is empty. Set handoff to `waiting_review`, `功能提交: branch_head`, `自动验证: passed`, `独立复审: pending`, `用户验收: pending`, `真实数据指纹: unchanged`, then commit all P2-02 source/test/docs changes without `user_data/`.

- [x] **Step 4: Perform independent review and fix findings**

Review `origin/main..HEAD` against the approved spec and this plan. Critical/Important findings must be zero. Any fix reruns its focused test and affected front-end commands before amending or adding a feature commit.

- [ ] **Step 5: Create the user-test anchor commit**

Record the reviewed feature SHA in a plan-only commit; set `交接状态: waiting_user`, `自动验证: passed`, `独立复审: passed`, `用户验收: pending`. Run `tools/handoff_status.py` and require `ok=true`.

### Task 7: Complete quick user acceptance and final package handoff

**Files:**
- Create: `docs/user-testing/checkpoints/P2-02-v1.5.0-design-system-quick-check.md`
- Modify: `docs/superpowers/plans/2026-07-12-p2-02-design-system-implementation.md`

**Interfaces:**
- Consumes: reviewed anchor SHA and the running showcase.
- Produces: user acceptance evidence and `verified_pending_integration` handoff.

- [ ] **Step 1: Generate the short test checklist only after the showcase is verified**

The checklist must state the package, reviewed SHA, local start/stop commands, URL, visible version marker, five checks (overall restraint, readability, state distinction, keyboard focus, narrow-screen overflow), feedback format and machine result block required by `tools/handoff_status.py`.

- [ ] **Step 2: Run the reviewed version for user inspection**

Start Vite on `127.0.0.1`, open the showcase, and ask the user to complete the quick checklist. Do not infer a pass from silence.

- [ ] **Step 3: Commit passed user evidence**

After explicit user confirmation, commit only the declared checkpoint file with the same reviewed SHA and `passed` machine result.

- [ ] **Step 4: Create the plan-only final handoff commit**

Set `交接状态: verified_pending_integration`, `功能提交` to the direct parent's full SHA, `自动验证: passed`, `独立复审: passed`, `用户验收: passed`, `真实数据指纹: unchanged`, and `夜间动作: independent_candidate_allowed`. Commit only this plan and run:

```powershell
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-12-p2-02-design-system-implementation.md --repo .
```

Expected: exit 0 and `ok=true` with no issues.

### Task 8: Integrate P2-02 through GitHub main and synchronize the baseline

**Files:**
- Modify on integration branch: `docs/superpowers/packages/EXECUTION_INDEX.md`
- Modify on integration branch only if needed: shared architecture/evidence files

**Interfaces:**
- Consumes: complete verified P2-02 commit chain.
- Produces: P2-02 merged into GitHub `main`, updated Index, synchronized local baseline.

- [ ] **Step 1: Merge the verified feature chain into a fresh integration branch**

Create the integration branch from latest `origin/main`, merge one package only, and confirm no `user_data/` path enters the diff.

- [ ] **Step 2: Run integration gates**

Run the focused Python guard, all front-end quality commands, full `tools/smoke_check.py`, `git diff --check`, and before/after root database fingerprint comparison. Because P2-02 changes shared front-end styling infrastructure, do not reuse an older full-smoke result.

- [ ] **Step 3: Update the authoritative package state**

Set P2-02 to `merged`, update Phase 2 counts/current queue/next action in `EXECUTION_INDEX.md`, and do not copy dynamic state into Phase maps or the nightly matrix.

- [ ] **Step 4: Push, create and merge the integration PR**

Push the integration branch, create a PR targeting GitHub `main`, verify its head SHA and checks, then merge without directly pushing `main`.

- [ ] **Step 5: Synchronize and safely clean up**

Fetch/prune, fast-forward root `main` and active worktrees to `origin/main`, delete only remote integration branches and local branches proven merged by `git branch --merged origin/main`, and remove linked worktrees only after source status, `user_data/` and reparse-point checks pass.

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P2-02
**交接状态：** waiting_review
**功能提交：** branch_head
**自动验证：** passed
**独立复审：** pending
**用户验收：** pending
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
