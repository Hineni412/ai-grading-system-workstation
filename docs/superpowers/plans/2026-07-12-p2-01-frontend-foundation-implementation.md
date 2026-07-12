# P2-01 Frontend Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**执行包：** P2-01
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** yes
**计划基线：** d1582855ec2273fae1d4b00dfb7b4d54824009e8
**用户自测：** none
**自测清单：** not_required

**Goal:** 建立可重复安装、静态检查、类型检查、单元测试、浏览器测试和生产构建的 `frontend/` 工程，并证明 `frontend/dist` 能由现有便携发布流程携带。

**Architecture:** 使用官方 `create-vue` 当前配置作为基线，保留 Vue 3、TypeScript、Vite、Element Plus、Pinia、Vue Router、ECharts、Vitest、ESLint 与 Playwright 的工程能力，但只实现无视觉迁移的空应用。所有直接依赖在 `package.json` 中使用精确版本并提交 npm 锁文件；开发服务器仅把 `/api` 代理到本机 FastAPI。便携发布脚本只复制已生成的 `frontend/dist`，不携带 Node、源码、依赖目录或测试产物。

**Tech Stack:** Vue 3.5.39, TypeScript 6.0.3, Vite 8.1.4, Element Plus 2.14.3, Pinia 3.0.4, Vue Router 5.1.0, ECharts 6.1.0, Vitest 4.1.10, ESLint 10.7.0, Playwright 1.61.1, npm lockfile v3.

## Global Constraints

- 只新增 `frontend/` 工程、前端守卫测试和便携发布的 `frontend/dist` 复制能力；不做页面视觉迁移，不建立 App Shell，不实现 API client。
- 不修改 `运行.bat`、Streamlit 生产入口、FastAPI 路由、数据库、评分规则或真实 `user_data/`。
- Node 只用于开发和构建；最终工作机只携带 `frontend/dist`，不要求安装 Node。
- `package.json` 的所有直接依赖使用完整版本号，不允许 `^`、`~`、`*`、tag 或 workspace 浮动范围。
- 使用 npm 11 与 Node `^22.18.0 || >=24.12.0`；提交 `package-lock.json`，忽略 `node_modules/`、`frontend/dist/`、coverage 与 Playwright 产物。
- Vite 开发代理仅匹配 `/api` 并指向 `http://127.0.0.1:8000`；不在浏览器配置中保存密钥。
- 遵守 `docs/ui/STYLE.md`，但本包不创建 Token、主题、业务页面或样板页；这些分别属于 P2-02 及后续包。
- 包内自动验证使用生成内容和临时目录；真实两库只做文件大小、UTC 修改时间和 SHA-256 的只读前后比较。

---

### Task 1: Claim the package with a plan-only commit

**Files:**
- Create: `docs/superpowers/plans/2026-07-12-p2-01-frontend-foundation-implementation.md`

**Interfaces:**
- Consumes: P2-01 `ready` state at baseline `d1582855ec2273fae1d4b00dfb7b4d54824009e8`.
- Produces: the unique P2-01 plan and immutable handoff identity used by `tools/handoff_status.py`.

- [ ] **Step 1: Record the initial real-database fingerprints without opening SQLite**

Run a read-only PowerShell `Get-Item` and `Get-FileHash -Algorithm SHA256` for the root checkout's `grading_system.db` and `question_bank.db`. Record size, UTC modification time, and SHA-256 in the execution log, not in repository documents.

- [ ] **Step 2: Verify the package worktree is clean and synchronized**

Run: `git status --short --branch`, `git rev-list --left-right --count HEAD...origin/main`, `git status --short -- user_data`, and `git stash list --format=%H`.

Expected: branch `codex/p2-01-frontend-foundation`, ahead/behind `0 0`, no worktree-local `user_data/` change, and the stash baseline recorded below.

- [ ] **Step 3: Commit only this plan as the claim commit**

```powershell
git add -- docs/superpowers/plans/2026-07-12-p2-01-frontend-foundation-implementation.md
git diff --cached --name-only
git commit -m "docs: claim P2-01 frontend foundation"
```

Expected staged file list: only this plan.

### Task 2: Add exact tooling contracts and the installable frontend skeleton

**Files:**
- Modify: `.gitignore`
- Create: `tests/test_frontend_foundation.py`
- Create: `frontend/package.json`
- Create: `frontend/package-lock.json`
- Create: `frontend/.editorconfig`
- Create: `frontend/.gitattributes`
- Create: `frontend/README.md`
- Create: `frontend/env.d.ts`
- Create: `frontend/eslint.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/playwright.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.app.json`
- Create: `frontend/tsconfig.node.json`
- Create: `frontend/tsconfig.vitest.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/e2e/tsconfig.json`

**Interfaces:**
- Consumes: Node `^22.18.0 || >=24.12.0`, npm lockfile v3, local FastAPI at `127.0.0.1:8000`.
- Produces: scripts named `dev`, `build`, `lint`, `typecheck`, `test`, and `e2e`; Vite `/api` proxy; exact dependency manifest and clean-install lockfile.

- [ ] **Step 1: Write the failing repository contract test**

Add `tests/test_frontend_foundation.py` that loads `frontend/package.json` and asserts:

```python
REQUIRED_SCRIPTS = {"dev", "build", "lint", "typecheck", "test", "e2e"}
REQUIRED_RUNTIME = {"vue", "element-plus", "pinia", "vue-router", "echarts"}

def test_frontend_manifest_locks_dependencies_and_quality_commands() -> None:
    package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert REQUIRED_SCRIPTS <= package["scripts"].keys()
    assert REQUIRED_RUNTIME <= package["dependencies"].keys()
    for section in ("dependencies", "devDependencies"):
        assert all(re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", value)
                   for value in package[section].values())

def test_frontend_vite_proxy_stays_on_loopback_api() -> None:
    config = (ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    assert "'/api'" in config
    assert "http://127.0.0.1:8000" in config
    assert "0.0.0.0" not in config
```

Also assert the lockfile exists, `frontend/package.json` declares the exact Node engine, and root `.gitignore` excludes front-end generated directories.

- [ ] **Step 2: Run the test to verify RED**

Run: `runtime\python\python.exe -m pytest tests\test_frontend_foundation.py -q` from the repository root runtime with the P2-01 worktree as current directory.

Expected: FAIL because `frontend/package.json` does not exist.

- [ ] **Step 3: Add the minimal official-style configuration**

Create `frontend/package.json` with exact versions and the scripts:

```json
{
  "name": "ai-grading-frontend",
  "version": "0.0.0",
  "private": true,
  "type": "module",
  "engines": { "node": "^22.18.0 || >=24.12.0" },
  "scripts": {
    "dev": "vite --host 127.0.0.1",
    "build": "run-p typecheck build-only",
    "build-only": "vite build",
    "preview": "vite preview --host 127.0.0.1",
    "lint": "eslint .",
    "typecheck": "vue-tsc --build",
    "test": "vitest run",
    "e2e": "playwright test"
  }
}
```

Populate exact dependencies from the Tech Stack and compatible official Vue scaffold packages. Configure strict TypeScript project references, ESLint flat config for Vue/TypeScript/Vitest/Playwright, jsdom unit tests, Playwright `webServer` using `npm run dev -- --port 5173`, and Vite proxy:

```ts
server: {
  host: '127.0.0.1',
  proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false } },
}
```

Do not create router, store, theme, API client, or page modules in this task.

- [ ] **Step 4: Generate the committed lockfile with a clean npm install**

Run in `frontend/`: `npm install --package-lock-only --ignore-scripts`, then `npm ci --ignore-scripts`.

Expected: exit 0; `package-lock.json` records all resolved transitive versions and integrity hashes.

- [ ] **Step 5: Run the repository contract test to verify GREEN**

Run: `runtime\python\python.exe -m pytest tests\test_frontend_foundation.py -q`.

Expected: PASS.

### Task 3: Add the empty Vue application through RED → GREEN

**Files:**
- Create: `frontend/src/__tests__/App.spec.ts`
- Create: `frontend/src/App.vue`
- Create: `frontend/src/main.ts`
- Create: `frontend/e2e/app.spec.ts`

**Interfaces:**
- Consumes: Vue/Vitest/Playwright configuration from Task 2.
- Produces: a mountable empty application with one stable readiness marker and a browser smoke test.

- [ ] **Step 1: Write a failing unit test before the application component**

```ts
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import App from '../App.vue'

describe('App', () => {
  it('renders the frontend readiness marker', () => {
    expect(mount(App).get('[data-testid="frontend-ready"]').text()).toBe('前端工程已就绪')
  })
})
```

- [ ] **Step 2: Run the unit test to verify RED**

Run: `npm run test -- --run src/__tests__/App.spec.ts`.

Expected: FAIL because `src/App.vue` is missing.

- [ ] **Step 3: Add the minimal application**

Create `App.vue` containing only a semantic `<main data-testid="frontend-ready">前端工程已就绪</main>` and `main.ts` that mounts it to `#app`. Do not add decorative CSS, router registration, Pinia stores, Element theme, or business content.

- [ ] **Step 4: Run unit, lint, and type checks to verify GREEN**

Run: `npm run test`, `npm run lint`, and `npm run typecheck`.

Expected: all exit 0 with one passing unit test.

- [ ] **Step 5: Add the Playwright smoke contract**

Create `frontend/e2e/app.spec.ts` that opens `/`, asserts the readiness marker, and asserts the page has no horizontal overflow at the default desktop viewport. Validate discovery without a browser download using `npm run e2e -- --list`.

Expected: Playwright lists exactly one smoke test.

### Task 4: Prove portable dist inclusion without touching real data

**Files:**
- Modify: `package_v1.5.0.py`
- Create: `tests/test_frontend_portable_packaging.py`

**Interfaces:**
- Consumes: generated `frontend/dist` directory.
- Produces: `copy_frontend_dist(src_dir, pkg_dir) -> int`, called by `copy_sources`, which copies only built assets to `<package>/frontend/dist`.

- [ ] **Step 1: Write the failing portable-copy test**

Use `tmp_path` to create a synthetic source tree with `frontend/dist/index.html`, `frontend/dist/assets/app.js`, `frontend/src/main.ts`, and `frontend/node_modules/noise.js`. Import `copy_sources`, run it against another temporary directory, and assert the two dist files are copied while `src` and `node_modules` are absent.

- [ ] **Step 2: Run the test to verify RED**

Run: `runtime\python\python.exe -m pytest tests\test_frontend_portable_packaging.py -q`.

Expected: FAIL because the current packager does not copy `frontend/dist`.

- [ ] **Step 3: Implement the narrow dist copier**

Add:

```python
def copy_frontend_dist(src_dir: Path, pkg_dir: Path) -> int:
    src = src_dir / "frontend" / "dist"
    if not src.is_dir():
        return 0
    files = [path for path in src.rglob("*") if path.is_file()]
    _copy_tree_filtered(src, pkg_dir / "frontend" / "dist")
    return len(files)
```

Call it from `copy_sources` and include its count in the returned statistics. Do not invoke `copy_private_user_data` in this test or run the real packaging command.

- [ ] **Step 4: Run packaging and foundation tests to verify GREEN**

Run: `runtime\python\python.exe -m pytest tests\test_frontend_foundation.py tests\test_frontend_portable_packaging.py -q`.

Expected: PASS.

### Task 5: Complete package verification and handoff

**Files:**
- Modify: `docs/superpowers/plans/2026-07-12-p2-01-frontend-foundation-implementation.md`

**Interfaces:**
- Consumes: all P2-01 code and tests.
- Produces: a `waiting_review` feature commit followed, after independent review, by a plan-only `verified_pending_integration` handoff commit.

- [ ] **Step 1: Perform a truly clean install**

Remove only the verified `frontend/node_modules` generated directory, then run `npm ci --ignore-scripts` in `frontend/`.

Expected: exit 0 without modifying `package-lock.json`.

- [ ] **Step 2: Run all front-end quality commands**

Run in `frontend/`: `npm run lint`, `npm run typecheck`, `npm run test`, `npm run e2e -- --list`, and `npm run build`.

Expected: all exit 0; `dist/index.html` and hashed assets exist; the build contains no `.env` file, source map, secret-like environment value, or Node dependency directory.

- [ ] **Step 3: Run affected repository verification**

Run from the P2-01 worktree:

```powershell
runtime\python\python.exe -m pytest tests\test_frontend_foundation.py tests\test_frontend_portable_packaging.py tests\test_smoke_check.py -q
runtime\python\python.exe tools\smoke_check.py --skip-tests
git diff --check
```

Expected: all exit 0. The full repository smoke will be run once after integration because this package changes shared dependency/build infrastructure.

- [ ] **Step 4: Recheck scope and real-data fingerprints**

Confirm `git status --short -- user_data` is empty, no generated directory is staged, and the two root database size/UTC/SHA-256 tuples exactly match Task 1.

- [ ] **Step 5: Commit the verified feature state as waiting_review**

Update the handoff block to `waiting_review`, `branch_head`, `passed`, `pending`, `not_required`, `unchanged`, then stage explicit P2-01 files only and commit `feat: establish Vue frontend foundation`.

- [ ] **Step 6: Request independent code review and resolve findings with TDD**

Review `origin/main..HEAD` against this plan. Fix every Critical/Important issue, rerun the affected tests and front-end commands, and leave no unresolved Critical/Important finding.

- [ ] **Step 7: Create the plan-only final handoff commit**

Set `功能提交` to the direct parent feature commit's full 40-character SHA, set `交接状态: verified_pending_integration`, `自动验证: passed`, `独立复审: passed`, `用户验收: not_required`, `真实数据指纹: unchanged`, and `夜间动作: independent_candidate_allowed`. Commit only this plan, then run:

```powershell
runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-12-p2-01-frontend-foundation-implementation.md --repo .
```

Expected: exit 0, one JSON result with `ok=true` and no issues.

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P2-01
**交接状态：** in_progress
**功能提交：** none
**自动验证：** pending
**独立复审：** pending
**用户验收：** not_required
**真实数据指纹：** not_touched
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
