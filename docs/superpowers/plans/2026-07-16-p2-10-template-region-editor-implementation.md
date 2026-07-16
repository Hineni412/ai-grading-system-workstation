# P2-10 样卷模板与答题区编辑器 Implementation Plan

**Goal:** 在 Vue 考试配置流程中增加可恢复的样卷上传与专注答题区编辑页，复用现有原生 JS 几何核心和后端提交服务，使教师能安全完成正反面选择、画框、题号绑定、校验、草稿保存和正式确认。

**Architecture:** P2-09 的 `/sessions` 保留考试、来源和评分依据工作台，并增加第五阶段入口；P2-10 使用考试专属 `/sessions/:sessionId/regions` 路由承载大画布。后端新增受控样卷上传服务、安全图片/工作区读取和 revision 化草稿契约，继续以 `AnswerRegionSessionLock`、`AnswerRegionDraftService`、`AnswerRegionCommitService` 和 `DBManager` 为唯一写入边界。Vue 通过适配器复用 `components/answer_region_editor/editor.js`，不复制几何算法。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、PyMuPDF/Pillow、SQLite、Vue 3、TypeScript、Pinia、Vue Router、Vitest、Playwright、现有原生 SVG 编辑器核心。

**执行包：** P2-10
**规划状态：** ready_for_execution
**计划基线：** cdf74012faf1decb60b2d76b6bd5f6714d7734c3
**设计来源：** `docs/superpowers/specs/2026-07-16-p2-10-template-region-editor-design.md`
**用户自测：** quick
**自测清单：** `docs/user-testing/checkpoints/P2-10-v1.5.0-template-regions-quick-check.md`
**已确认测试缝：** 2026-07-16 用户确认 HTTP API、编辑器组件、Vue 页面工作流、匿名真实浏览器流程和持久化一致性五个公开边界。

## Global Constraints

- P2-10 包含样卷 PDF 上传、第一页正反面选择、template viewer、草稿、画框/移动/缩放、题号绑定、冲突、校验、正式提交和 confirmed snapshot；整班答卷、扫描预检、批改 Job 属于 P2-11。
- 评分标准、答案、分值和题号来源继续使用 P2-09 已保存配置；样卷阶段只负责版面和题框，不改变评分语义。
- 必须复用 `components/answer_region_editor/` 原生 JS 核心、`answer_region_models.py` 和现有 draft/lock/commit 服务；不得复制或重写几何算法。
- 新模板、草稿和正式区域以模板指纹绑定；旧请求、旧考试、旧模板或旧 revision 不能覆盖当前上下文。
- 写请求不自动重放；样卷上传响应丢失时复用 P2-09 的请求令牌保留/查询/放弃模式。
- Vue 和公开 API 不返回本机绝对路径、草稿/快照路径、rubric 路径、SQL、堆栈或原始内部 JSON。
- 不新增数据库迁移、不修改依赖锁、不切换生产入口、不退役旧 Streamlit 编辑器。
- 测试只使用匿名合成 PDF、图片、临时目录和临时数据库；不得读取、写入、暂存、提交或 stash 根工作区真实 `user_data/`。
- 支持视口固定为 1920×1080、1440×900、1366×768、1280×800、1024×768；低于 1024px 不扩展产品范围。
- 视觉只使用 `docs/ui/STYLE.md` Token 和字体；画布为唯一视觉重点，状态不能只靠颜色。
- 开发与修复只跑当前切片和受影响范围；稳定候选后才并行需求复审与代码质量复审。第一轮意见全部去重汇总后统一修复，再只复测影响面并进行一次最终复审；第二轮仍有 Critical/Important 时停止报告。
- 功能分支不修改 `EXECUTION_INDEX.md`；最终状态与共享事实由 integration 统一更新。

## File Map

**Create:**

- `docs/superpowers/plans/2026-07-16-p2-10-template-region-editor-implementation.md`：领取、RED/GREEN、验证、复审与交接证据。
- `docs/superpowers/specs/2026-07-16-p2-10-template-region-editor-design.md`：已确认溯源设计；领取后单独引入。
- `template_upload_service.py`：受控 PDF 暂存、前两页转换、映射包生成、请求令牌恢复和原子激活。
- `frontend/src/api/template-regions.ts`：样卷上传、令牌查询、工作区、草稿、提交、快照重试严格契约。
- `frontend/src/stores/template-regions.ts`：考试/模板世代、内存编辑、草稿保存、冲突和确认态。
- `frontend/src/components/template-regions/TemplateUploadPanel.vue`：PDF、第一页角色、上传未知状态与当前模板摘要。
- `frontend/src/components/template-regions/TemplateRegionEditor.vue`：Vue 到原生 JS 编辑器核心的适配器。
- `frontend/src/components/template-regions/RegionValidationPanel.vue`：题框列表、题号绑定、同题多框和 issue 定位。
- `frontend/src/views/TemplateRegionView.vue`：专注路由、加载/恢复/提交/只读确认编排。
- `frontend/src/styles/template-regions.css`：画布、装订边状态尺、题框抽屉和五视口布局。
- `frontend/src/api/__tests__/template-regions.spec.ts`：严格公开契约、路径拒绝和写请求守卫。
- `frontend/src/__tests__/template-region-store.spec.ts`：世代隔离、草稿、冲突、未知上传与确认态。
- `frontend/src/__tests__/template-region-editor.spec.ts`：组件输入、用户事件、原图坐标和只读态。
- `frontend/src/__tests__/template-region-view.spec.ts`：第五阶段入口、路由、恢复和错误状态。
- `frontend/e2e/template-region-editor.spec.ts`：匿名样卷的五视口绘制、绑定、提交和刷新流程。
- `tests/test_template_upload_service.py`：受控 PDF、锁、令牌和失败不覆盖测试。
- `docs/user-testing/checkpoints/P2-10-v1.5.0-template-regions-quick-check.md`：稳定候选后的版本化用户短测。

**Modify:**

- `backend/api/routers/templates.py`：安全上传、图片、工作区、draft conflict、commit 与 snapshot retry 路由。
- `backend/api/schemas/templates.py`：安全公开模型和严格 request/response。
- `backend/api/schemas/sessions.py`、`backend/api/routers/sessions.py`：模板公开响应移除绝对路径并增加受控 URL/尺寸/指纹。
- `backend/api/dependencies.py`：隔离可替换的模板上传服务依赖。
- `tests/test_api_template_region_routes.py`：HTTP 公开行为、失败恢复、路径脱敏和一致性测试。
- `tests/test_api_openapi_contract.py`：新契约与错误模型。
- `components/answer_region_editor/editor.js`：只做 Vue 适配所需的公开控制器/事件边界，不改变几何规则。
- `components/answer_region_editor/editor_core.test.mjs`：不同尺寸、缩放和适配事件回归。
- `frontend/src/components/config/ConfigStageRail.vue`、`frontend/src/views/SessionConfigView.vue`：第五阶段状态与受控入口。
- `frontend/src/router/index.ts`、`frontend/src/navigation.ts`：考试专属专注路由，继续归属考试配置导航。
- `frontend/src/main.ts`：导入 P2-10 样式。
- `frontend/src/__tests__/session-config-view.spec.ts`、`frontend/src/__tests__/navigation-router.spec.ts`：P2-09 回归与入口测试。
- `ARCHITECTURE.md`：实现后记录当前 P2-10 事实和不变边界。

---

### Task 0: 领取 P2-10、引入设计并冻结基线

**Interfaces:** Git 领取提交、交接 validator、真实数据只读指纹。

- [x] **Step 1: 提交唯一领取计划**

  首个 first-parent 提交只包含本计划，交接为 `in_progress / none / pending / pending / pending / not_touched / report_only`。运行 `git diff --cached --check` 和 `tools/handoff_status.py`，要求 `ok=true`。

- [x] **Step 2: 单独引入已确认设计**

  cherry-pick 设计提交 `9861849`；确认只新增设计说明，不改变领取提交顺序。

- [x] **Step 3: 冻结代码前基线**

  运行既有 answer-region Python 聚焦测试、template API 测试、editor core Node 测试、P2-09 前端相关单元、lint/typecheck/build 和 `tools/smoke_check.py --skip-tests`。若基线失败先调查，不写 P2-10 代码规避。

---

### Task 1: 安全样卷上传与请求恢复

**Public seam:** `POST /api/sessions/{id}/template`、请求令牌查询/放弃、随后模板 GET。

- [x] **RED 1:** HTTP 测试证明浏览器可上传匿名双页 PDF并选择第一页角色；少于两页、超限、错误格式和不存在考试均不改变旧模板。
- [x] **GREEN 1:** 实现受控流式暂存、两页转换和最小安全响应。
- [x] **RED 2:** 服务测试证明同考试并发上传只允许一个激活；响应丢失可按令牌查询；重复/冲突令牌拒绝；失败激活不覆盖旧模板/区域 ready 状态。
- [x] **GREEN 2:** 复用 P2-09 提交标记模式与 `AnswerRegionSessionLock`，在临时目录生成映射包并成功后激活。
- [x] **RED 3:** 测试证明响应、日志和错误不包含上传路径、模板绝对路径或临时根。
- [x] **GREEN 3:** 收敛安全公开模型和错误映射。
- [x] **Verify/commit:** 仅运行 `test_template_upload_service.py`、template route 对应 `-k` 和 OpenAPI 受影响用例，记录耗时与去重问题后提交。

---

### Task 2: 安全工作区、媒体、草稿冲突与确认快照

**Public seam:** workspace GET、受控 page GET、draft PUT/GET/discard、commit、snapshot retry；持久化通过公开服务/DBManager 读取。

- [x] **RED 1:** workspace 读取只返回图片 URL/尺寸/指纹、正式区域、草稿状态、题号目录、issues 与 ready 状态；任何绝对路径均被契约拒绝。
- [x] **GREEN 1:** 组合现有 template、rubric catalog、formal regions、draft 和 validation 服务，增加受控图片路由。
- [x] **RED 2:** 相同模板和 revision 可保存草稿；模板变化或 revision 前进返回 409；损坏/不兼容草稿只返回安全状态并需显式丢弃。
- [x] **GREEN 2:** 扩展 draft schema 与 lock 内比较写入，不改变现有草稿文件版本之外的业务含义。
- [x] **RED 3:** 提交校验一次返回全部 issues；成功后 workspace 为只读 confirmed；快照失败仍显示 committed，并可安全重试到一致。
- [x] **GREEN 3:** 复用 `AnswerRegionCommitService`/retry，并把 timeout、fingerprint 和 issues 映射为稳定 API。
- [x] **Verify/commit:** 运行 answer-region models/draft/lock/commit、template routes 与 OpenAPI 受影响测试；不跑全量。

---

### Task 3: 提取可复用编辑器控制器并建立 Vue 适配器

**Public seam:** editor core 的公开输入、用户事件和输出；`TemplateRegionEditor` props/emits。

- [x] **RED 1:** Node 公共测试以 1000×1400、2480×3508 等固定尺寸证明 client→image 坐标、适应宽度、100% 和移动/缩放保存原图像素。
- [x] **GREEN 1:** 仅暴露控制器/事件适配点，保留现有纯函数与 Streamlit 适配。
- [x] **RED 2:** Vue 组件测试通过公开画框、撤销、题号绑定和正反面查看，输出 revision 与完整区域；只读态不产生写事件；其余移动/缩放/删除/重做/键盘行为继续由既有 editor core 回归约束。
- [x] **GREEN 2:** 复用现有 DOM/SVG 核心并把 `setStateValue/setTriggerValue` 映射为 Vue emits。
- [x] **RED 3:** issues 能定位对应题框，同题多框需确认，状态不只靠颜色；1024px 下题框清单可访问。
- [x] **GREEN 3:** 复用 editor core 已有题框抽屉、校验解释、同题多框确认和状态尺；P2-10 页面仅增加 Token 化响应布局，不复制校验控件。
- [x] **Verify/commit:** 运行 editor core 与单个 Vue 组件测试、typecheck；提交前 `git diff --check`。

---

### Task 4: 接入 API client、Pinia store、第五阶段和专注路由

**Public seam:** Vue 页面路由与教师可见流程。

- [x] **RED 1:** API client 严格解码安全字段，拒绝路径/错误 shape；所有写请求单次发送，GET 才使用既有有界重试。
- [x] **GREEN 1:** 实现 `template-regions.ts` SDK 式接口。
- [x] **RED 2:** store 测试覆盖旧 session/template 响应隔离、上传未知状态、草稿延迟保存、409 停止自动保存、失败保留内存编辑、提交后只读。
- [x] **GREEN 2:** 实现上下文 token、保存队列和安全恢复状态机。
- [x] **RED 3:** `/sessions` 在 P2-09 editor configured 后出现第五阶段及正确按钮；专注路由可刷新/返回；无模板、兼容/不兼容草稿、snapshot pending 使用不同状态。
- [x] **GREEN 3:** 接入 stage rail、route、view 和入口，不改变 P2-09 保存语义。
- [x] **Verify/commit:** 运行 P2-10 API/store/view 与 P2-09 session config/navigation 受影响单元、lint/typecheck/build。

---

### Task 5: 浏览器流程与不同模板尺寸

**Public seam:** Chromium 中教师看到并操作的完整流程。

- [x] **RED 1:** 匿名固定数据流程覆盖上传双页 PDF、选择反面在前、预览、画框、绑定、提交和刷新只读态；同题多框继续由 editor core 公共交互回归约束。
- [x] **GREEN 1:** 补齐最小页面行为，使端到端流程通过。
- [x] **RED 2:** 草稿恢复、409、上传结果未知、路径脱敏、快照待补写和重试分别由 HTTP、store 与 view 公共缝测试覆盖，不在浏览器层复制后端状态机。
- [x] **GREEN 2:** 补齐恢复和错误边界，不复制后端状态机。
- [x] **RED 3:** 五档视口、两种模板尺寸、鼠标/键盘与焦点公共行为、无横向溢出和画布像素证据。
- [x] **GREEN 3:** 只调整 P2-10 响应式样式和可访问性。
- [x] **Verify/commit:** 运行 P2-10 Chromium；P2-09 session-config 受影响页面已在 Task 4 单元回归通过，稳定候选再运行其浏览器回归；临时输出已忽略且不提交。

---

### Task 6: 稳定候选、双重复审、短测和交接

- [x] **Step 1: 包内稳定候选验证**

  运行 P2-10 聚焦 Python、现有 answer-region 回归、OpenAPI、全部前端 unit、lint/typecheck/build、P2-10/P2-09 Chromium 和 `tools/smoke_check.py --skip-tests`。记录轮次、耗时、去重问题和剩余工作；功能分支默认不跑全量 pytest。

- [x] **Step 2: 更新架构事实与生成短测卡**

  `ARCHITECTURE.md` 只记录已实现的安全上传、专注路由、核心复用、草稿冲突、confirmed snapshot 和无 Schema/评分语义变化。短测卡只在匿名运行环境和启动方式实际验证后生成。

- [ ] **Step 3: 冻结候选并并行复审**

  使用 `code-review` 技能并行进行 Spec 与 Standards 两路复审，固定比较点为计划基线。汇总全部 Critical/Important，按根因去重后一次性修复；只复测影响范围并进行一次最终复审。第二轮仍有 Critical/Important 时停止并向用户汇报。

- [ ] **Step 4: 用户短测与最终功能交接**

  复审通过后按 handoff 协议冻结已复审完整 SHA，等待用户只对该候选短测；通过后形成 `verified_pending_integration`。功能分支交接运行 `tools/smoke_check.py --skip-tests`、`git diff --check`、handoff validator，并确认不含 `user_data`、临时数据库、构建/浏览器噪声或真实导出。

- [ ] **Step 5: integration、PR 与主线同步**

  从当时最新 `origin/main` 建立 integration，一次只合入 P2-10 完整提交链；运行包内聚焦回归后，在波次末仅运行一次完整 `tools/smoke_check.py`、前端 lint/typecheck/unit/build/Chromium 和真实两库指纹守卫。推送 integration、创建 PR、检查通过后合并，再 fetch 并安全同步本地 main；不得直接 push main、force push 或清理未被 `origin/main` 包含的历史。

## Test and Review Ledger

| 轮次 | 阶段 | 状态 | 耗时 | 去重问题 | 剩余工作 |
|---:|---|---|---:|---:|---|
| 1 | 调查与根因分组 | completed | 记录于会话 | 5 组 | 设计、计划、实现、验证、复审、集成 |
| 2 | 溯源与 UX 设计 | completed | 记录于会话 | 0 新增 | 计划、实现、验证、复审、集成 |
| 3 | 测试缝确认 | completed | 记录于会话 | 0 | 计划、实现、验证、复审、集成 |
| 4 | 代码前聚焦基线 | passed | 约 77 秒有效检查；另有 184 秒并行编排超时 | 1 个执行编排问题，产品问题 0 | 实现、验证、复审、集成 |
| 5 | Task 1 样卷上传 RED/GREEN | passed | 约 55 秒 | 4 组：缺上传入口、令牌不可恢复、数据库失败覆盖旧文件、缺映射包 | 工作区/草稿/提交、Vue、浏览器、复审、集成 |
| 6 | Task 2 工作区、草稿冲突与快照恢复 | passed | 约 90 秒开发验证；38.56 秒聚焦回归 | 5 组：工作区缺口、草稿静默覆盖、内部路径泄露、不兼容草稿误返回内容、快照无安全重试 | 编辑器适配、Vue、浏览器、复审、集成 |
| 7 | Task 3 原生编辑器 Vue 适配 | passed | 约 35 秒开发验证；18.5 秒聚焦验证 | 3 组：缺 Vue 桥接、确认态仍可编辑、前端测试无法读取工作区级复用资源 | API/store/页面、浏览器、复审、集成 |
| 8 | Task 4 API/store/第五阶段与专注页面 | passed | 约 4 分钟开发；46.1 秒首轮门槛、25.5 秒受影响复测 | 5 组：安全解码缺口、旧上下文晚到覆盖、409 后继续保存、无模板无入口、确认态/快照态不清 | 浏览器、稳定候选、复审、集成 |
| 9 | Task 5 匿名浏览器流程与视口 | passed | 约 3 分钟开发；最终 10.3 秒 | 2 组：编辑输出绕过保存队列、浏览器断言命中两个同文案状态 | 稳定候选、复审、短测、集成 |
| 10 | 公开模板响应脱敏、架构与短测卡 | passed | 约 2 分钟；Python 25.28 秒、前端 16 秒 | 1 组：旧模板 GET/PUT 仍返回内部路径与快照令牌 | 稳定候选、复审、短测、集成 |
| 11 | 稳定候选门槛 | passed after scoped fix | 并行总墙钟 62.4 秒；修复复测 43.8 秒 | 1 组：共享编辑器开发目录新增后，旧 Vite 精确配置测试失配 | 双重复审、短测、集成 |
| 12 | 首轮 Spec/Standards 并行复审 | changes requested | 约 9 分钟 | 7 组：6 组 Important 流程缺口，1 组样式 token；另有 2 项非阻断设计建议 | 批量修复、最终复审、短测、集成 |
| 13 | 首轮复审问题批量修复与受影响复测 | passed | 约 18 分钟；最终受影响门槛 约 95 秒 | 7 组全部修复；首次真实链路因运行时优先载入主工作区代码失败，修正隔离启动入口后通过 | 冻结候选、最终复审、短测、集成 |
| 14 | 最终 Spec/Standards 复审 | stopped per policy | 约 7 分钟 | Standards Important 0；Spec Important 3：锁超时契约、第五阶段真实状态、提交前摘要 | 等待用户决定 |
| 15 | 用户授权的新修复轮次与受影响复测 | passed | 约 20 分钟；受影响门槛约 102 秒 | 3 组 Important 全部修复；1 组 P2-09 浏览器测试桩缺新状态接口 | 重新冻结、独立复审、短测、集成 |
| 16 | 授权轮次后的 Spec/Standards 复审 | stopped per policy | 约 8 分钟 | Standards Important 0；Spec 新发现 1 组 Important：模板已激活但成功回执失败时误报失败 | 等待用户决定 |
| 17 | 用户再次授权的上传一致性修复 | passed | 约 12 分钟；受影响门槛约 83 秒 | 1 组 Important 已修复；1 个既有令牌测试复用了两次重新生成 PDF，改为复用同一请求体以消除非业务波动 | 重新冻结、独立复审、短测、集成 |
| 18 | 上传一致性候选 Spec/Standards 复审 | stopped per policy | 约 9 分钟 | 2 组 Important：恢复成功仍可放弃；不同令牌并发上传没有单赢家；同根因含查询锁快照与 processing 解锁 | 等待用户决定 |
| 19 | 用户授权继续完善并发上传协议 | passed | 约 15 分钟；受影响门槛约 102 秒 | 2 组 Important 及同根因恢复边界全部修复 | 重新冻结、独立复审、短测、集成 |

基线证据：Python answer-region/template API `106 passed`（25.20 秒）；editor core `14 passed`（0.15 秒）；P2-09 navigation `20 passed`（6.75 秒）、session config `6 passed`（2.78 秒）；lint/typecheck/build 通过（22.5 秒）；快速冒烟通过（6.31 秒）。首次把这些命令与静态检查并行汇总时，Vitest 子进程未在父级 180 秒上限内退出；拆成单文件顺序反馈后稳定通过，未修改产品代码。

Task 1 证据：四条垂直切片分别从 `405`、submission `404`、abandon `404`、数据库失败覆盖旧图片和映射路径为空转绿；聚焦 service/API `11 passed`（7.03 秒），OpenAPI `15 passed`（14.61 秒），受影响 Python 编译与 `git diff --check` 通过。全部写入只发生在 pytest 临时目录。

Task 2 证据：workspace、revision 冲突、显式丢弃、响应路径脱敏和快照重试从缺失或失败转绿；发现新增公开方法会破坏既有服务接口后，改为在原 `save` 契约内增加可选 revision 守卫。answer-region models/draft/lock/commit、template service/routes 与 OpenAPI 聚焦回归 `128 passed`（38.56 秒）。全部写入只发生在 pytest 临时目录。

Task 3 证据：editor core 从 `15 passed / 1 failed` 转为 `16 passed`（约 0.17 秒），Vue 适配器从资源访问失败转为 `3 passed`（2.54 秒），`vue-tsc` 通过（15.8 秒）。适配器直接载入既有 `editor.html` 与 `editor.js`，没有复制几何算法；确认态只读守卫同时覆盖按钮、题号选择、鼠标和键盘写操作。

Task 4 证据：P2-10 API/store/editor/view 与 P2-09 navigation/session-config 聚焦单元 `37 passed`（5.93 秒），lint 与 typecheck 通过；正式前端构建通过（1.15 秒）。首次门槛仅发现测试替身两个未使用参数，归入同一静态规范问题并一次修正；页面使用装订边状态尺区分草稿、正式只读和快照待补写，状态均同时有文字。

Task 5 证据：Chromium 匿名流程从“草稿已保存”超时暴露出 Vue `v-model` 绕过 store 保存动作，改为显式 `update:modelValue → updateEditor` 后通过；随后只收紧重复文案定位。最终 `2 passed`（7.8 秒测试、10.3 秒总耗时），覆盖上传、反面在前、画框、绑定、自动保存、正式确认、刷新只读及 1920×1080、1440×900、1366×768、1280×800、1024×768 五档视口与 1000×1400/2480×3508 两种页面尺寸。

公开响应与文档证据：既有模板 GET/PUT 响应改为只返回受控正反面 URL、确认状态和时间，不再返回模板、映射、区域路径或快照令牌；template/read/OpenAPI 聚焦回归 `32 passed`（25.28 秒）。架构事实与 P2-10 匿名短测卡已生成；lint 与 P2-10 Chromium `2 passed`（7.9 秒）再次通过。

稳定候选证据：Python P2-10/answer-region/read/OpenAPI `132 passed`（54.18 秒）；P2-10 + P2-09 Chromium `17 passed`（36.2 秒）；快速冒烟通过（23.40 秒）；前端 lint/build 通过。全部前端单元为 `511 passed / 1 failed`，唯一失败是 Vite 配置对象新增共享编辑器目录后旧精确断言未同步；修复时把此前 `..` 允许范围收窄为 `frontend/` 与 `components/answer_region_editor/` 两个精确目录，受影响单元 `4 passed`、typecheck、lint、build 通过。按分层规则未重复运行未受影响的 511 项与浏览器/Python 门槛。

首轮复审与批量修复证据：Spec 复审提出评分依据前置门槛、草稿选择、离开/冲突/保存恢复、校验问题抽屉、正式版重新编辑与 P2-11 就绪提示、真实临时数据库浏览器缝共 6 组 Important；Standards 复审提出交接生命周期 1 组 Important 与样式 token 1 组 Minor。已一次性补齐并只复测受影响范围：后端 answer-region/template/OpenAPI `97 passed`（33.84 秒），最终 template/OpenAPI `29 passed`（22.75 秒）；前端 P2-10 单元 `13 passed`，typecheck/lint/build 通过；模拟 Chromium 覆盖键盘、409 冲突与快照重试 `4 passed`（11.6 秒），真实匿名 PDF + 临时数据库上传至刷新只读 `1 passed`（9.9 秒）；快速冒烟 `--skip-tests` 通过（6.07 秒）。真实两库 SHA256 与开工基线一致，临时数据库只写入 `frontend/test-results/p2-10-real/` 忽略目录。

最终复审停止与用户授权证据：最终 Standards 复审 Important 0；最终 Spec 复审仍发现锁超时未映射、配置页第五阶段未读取真实状态、提交前无数量/绑定/异常摘要共 3 组 Important，因此按规则停止。用户于 2026-07-16 明确确认开启一次新的人工授权修复轮次。新轮次增加 `answer_region_lock_timeout` 稳定 503/重试契约与页面安全提示；readiness 返回真实 `template_ready`，配置页区分“准备样卷/继续标定/查看已确认版本”；正式提交确认展示正反面、绑定、待处理和异常摘要，并补齐每面状态与选中态。受影响后端 `30 passed`（33.26 秒）；前端 `21 passed`、typecheck/lint/build 通过；P2-10 与 P2-09 浏览器首轮 `14 passed / 5 failed`，5 项失败同源于 P2-09 模拟环境缺新增 readiness 路由，补齐后只复测对应五视口 `5 passed`（7.1 秒）；P2-10 真实临时数据库 `1 passed`（9.8 秒）；快速冒烟 `--skip-tests` 通过（11.54 秒）。真实两库 SHA256 再次与开工基线一致。

上传一致性复审与再次授权证据：重新冻结候选的 Standards 复审 Important 0，Spec 确认前述 3 组均关闭，但发现模板激活成功后若成功回执文件单独写入失败，路由会误写失败并让页面宣称旧模板未改变。用户再次明确授权修复。新实现把请求令牌写入与模板同步切换的 manifest；成功回执改为可恢复的尽力写入，失败时不再覆盖真实激活结果；令牌查询可从当前 manifest 与数据库恢复完整成功响应；前端对服务端写异常保留令牌并进入“先核对”状态。受影响后端 template service/routes/OpenAPI `34 passed`（23.56 秒）；前端 `22 passed`、typecheck/lint/build 通过；模拟 P2-10 Chromium `4 passed`（13.3 秒），真实临时数据库 `1 passed`（10.8 秒）。所有写入仍只发生在 pytest/Playwright 临时目录。

并发上传协议复审与继续证据：上一候选复审确认单次回执恢复有效，但指出成功事实仍可被放弃、不同令牌可依次覆盖、查询与模板读取不在同一锁快照、停滞 processing 无法安全解除。用户明确要求继续。新实现让 FastAPI 应用内复用同一上传协调器；同考试存在处理中令牌时拒绝第二令牌；查询在同一会话锁内核对 manifest、数据库和文件并持久修复 `succeeded`；已激活令牌永远不能 abandon；新模板激活后旧成功令牌返回 `replaced`；前端对 `processing` 尝试安全放弃，活动请求被服务拒绝后继续等待，非活动停滞请求才解除。受影响后端 template service/routes/OpenAPI/API app `41 passed`（28.69 秒）；前端 `14 passed`、typecheck/lint/build 通过；模拟 P2-10 Chromium `4 passed`（12.2 秒），真实临时数据库 `1 passed`（12.3 秒）。

后续每个 RED/GREEN、复审、修复和 integration 门槛均追加一行；同一根因的多条失败只计一个去重问题。

## Rollback and Stop Conditions

- 功能回退为撤销 P2-10 提交并继续使用旧 Streamlit 编辑器；不删除既有模板、正式区域或草稿。
- 模板激活失败必须保留旧模板；正式提交失败必须保留草稿；快照失败不得回滚已提交正式区域。
- 发现需要改变评分规则、题号语义、几何算法、数据库 Schema、P2-11 状态机或生产入口时停止。
- 测试需要真实数据、真实密钥、真实模型或不可逆文件操作时停止。
- 同一包连续两次修复失败，或最终复审仍有 Critical/Important 时停止。
- 真实两库指纹、大小或 UTC mtime 未经授权发生变化时立即停止。

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P2-10
**交接状态：** waiting_review
**功能提交：** branch_head
**自动验证：** passed
**独立复审：** pending
**用户验收：** pending
**真实数据指纹：** not_touched
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
