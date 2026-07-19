# P2-18 训练推荐工作台即时实现计划

**执行包：** P2-18
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** no
**计划基线：** ce64b1edcccb3d91a8d954848d5d198294cb0bfe
**用户自测：** quick
**自测清单：** docs/user-testing/checkpoints/P2-18-v1.5.0-training-recommendations-quick-check.md

> 用户于 2026-07-19 明确要求继续 P2 的下一个任务，并预先同意推荐方案、最多三次复核问题修补以及完成后先记录 quick 验收通过。Phase map、现有 Streamlit 训练页、现有 Training API/Job/File 契约与下列 Public Test Seams 共同冻结范围；不把旧页面布局、Vue 组件内部层级、真实数据或 Phase 4 设想作为验收接口。

## Objective

在不修改诊断口径、推荐算法、数据库 Schema、导出格式、模型策略或生产入口的前提下，交付独立 Vue“训练推荐工作台”：教师可以选择考试与学生范围，读取只基于当前精确 `knowledge_point` 标签的薄弱点诊断和证据，生成每人独立或自动分组的训练计划预览，核对推荐原因和缺题说明，使用版本校验与幂等确认保存固定任务，再查看历史任务并进入训练材料导出、重试和下载闭环。旧 Streamlit 页面继续可用，所有写入和浏览器验证只使用临时双库与临时数据根。

## Frozen Boundary

- **包含：** 当前/跨考试/手选考试；单个/明确多选/整班学生；tag-only 诊断；学生×知识点掌握情况及文字摘要；评分证据、薄弱原因和覆盖缺口；每人独立/自动分组预览；8—12 题、默认 60/30/10 阶段比例、排除当前原题；推荐理由、精确标签、缺题和警告；计划 revision；幂等教师确认；任务详情与历史；Word/Markdown、整任务包/指定版本、学生版/教师版导出；Job 进度、取消、失败重试、刷新恢复与受控下载；Vue 路由/导航；五档 Windows 桌面视口；quick 用户短测。
- **明确不包含：** 数据库迁移；真实业务数据写入；真实模型调用；Phase 4 时间衰减、先修关系、训练结果回流或推荐评估；宽泛/近义标签补题；手工修改推荐分数或算法权重；新增训练题；学生答题入口；修改题库标签、评分结果、推荐算法、导出格式或 Job 协议；生产 UI 切换；删除旧 Streamlit 页面。
- **相邻但不并入：** 现有 API 支持 `teacher_groups`，旧 Streamlit 主流程未提供手工分组，本包只迁移现有“每人独立/自动分组”；文件中心已具备完整训练出件登记簿，本页提供当前任务出件和最近历史，并保留进入文件中心的深度管理入口，不复制第二套文件管理产品；训练结果回流仍明确未启用。
- **验收条件：** 与现有服务同一固定输入得到一致的学生、薄弱点、候选题、缺口和任务结果；热力图同时有可读文字摘要；无证据或无推荐时说明原因和下一步；选择变化不会保存旧预览；确认丢失后人工重试不会重复建任务；revision 冲突不覆盖新计划；导出提交、Job 恢复、取消、失败重试、下载与历史闭环可用；五档视口无意外溢出；受影响自动测试、匿名真实浏览器、快速冒烟、交接验证和真实两库指纹守卫通过。
- **风险等级：** 中。现有后端已具备幂等确认和可重建导出，但前端涉及多范围状态一致性、旧响应隔离、任务写入、版本冲突、Job 重试和文件下载；稳定候选后必须进行需求符合性与代码质量双路复审。

## Root-Cause Inventory

开工集中调查只覆盖 P2-18 及直接影响范围，去重并冻结为五个根因组；修改前问题和相邻需求不自动并入。

| 根因组 | 调查证据与当前缺口 | 本包统一处理 |
|---|---|---|
| A. Training API 已完整但 Vue 只有出件尾段 | `/api/training/diagnosis`、`plans/preview`、`tasks` 与 exports 已进入共同基线；`frontend/src/api/exports.ts` 只覆盖历史/出件，没有诊断、预览和确认类型 | 新增严格 `training.ts` API，复用既有 tasks/exports/Job seam，不改后端业务契约 |
| B. 范围选择跨考试、学生和班级，旧响应容易污染新现场 | Streamlit 用 session state signature；Vue 已有 sessions/students/current session，但没有 Training generation | Training store 以规范化 scope key 和 generation 隔离 diagnosis/preview/task；选择变化保留控件但废止旧结果 |
| C. 精确标签证据有结构化数据但缺少可访问的主视图 | API 返回 mastery、扣分、考试数、证据引用、原因、覆盖缺口和 warnings | “诊断热力账本”同时提供格点强度、数值、文字摘要和证据下钻；颜色不作为唯一信息 |
| D. 推荐预览与确认之间存在真实数据漂移 | API 以 64 位 `plan_revision` 检测题库或诊断变化，并以 `confirmation_id` 保证重复确认只建一个任务 | 预览冻结 revision；确认使用每份预览稳定 UUID；409 保留旧预览供核对但禁止再次确认，要求重新生成 |
| E. 出件与历史已在文件中心，训练工作流仍缺闭环 | P2-12 已提供 task list/detail、training_export Job、retry/download；独立训练页尚不存在 | 工作台显示最近任务、当前任务版本与快捷出件；Job 由通用 store 恢复；深度登记簿继续由文件中心承担 |

## Fixed Failure Matrix

| 场景 | 预期结果 |
|---|---|
| 重复操作 | 分析/预览/确认/导出请求进行中禁用同一动作；写请求不自动重放；同一预览人工重试沿用同一 confirmation ID，服务端返回同一任务 |
| 同时操作 | 两个窗口确认同一 confirmation ID 只产生一个任务；不同窗口若数据已变，由 plan revision 返回 409，不覆盖或伪装成功 |
| 中途退出 | 尚未确认的诊断/预览不写数据库；已提交导出由 Job 状态记录；页面关闭不取消服务器任务 |
| 重新启动 | 已确认任务从任务历史恢复；浏览器记录的导出 Job 由通用 Job Store 按 ID 恢复；未确认预览需重新生成，避免把过时计划当作当前事实 |
| 失败重试 | GET 继续使用现有有界重试；所有写请求单次发送；确认人工重试复用 confirmation ID；failed/cancelled export 只走专用 retry 端点 |
| 取消 | 仅对 queued/running 导出提供取消；服务器终态胜出；诊断、预览和任务确认是短请求，不伪装为可取消长任务 |
| 部分完成 | 计划变体可带缺题与 warning，但没有任何题的计划不能确认；导出部分结果以 Job/任务公开状态为准，不从客户端猜测 |
| 数据缺失 | 无学生、无考试、题库不可用、无关联标签、覆盖不全、无候选题和文件过期分别显示原因与可执行下一步，不显示内部路径 |
| 数据冲突 | 选择变化使旧 diagnosis/preview 失效；迟到响应按 generation 丢弃；revision 冲突保留核对现场并要求重新分析/生成 |
| 外部费用 | 本包只读取现有 tag-only 服务，不注册或调用模型，不开放宽泛匹配或 AI 补题控制 |

## Public Test Seams

用户已按“采用推荐默认选项”的授权确认以下测试边界：

1. **Training HTTP seam：** 复用现有真实 FastAPI + 临时双库契约测试，补充前端所依赖的 worked examples，不测试服务私有函数。
2. **Typed frontend API seam：** 严格解码 diagnosis/plan/task，拒绝路径字段、旧 skill 身份、非法 revision、异常 mastery 和畸形变体；写请求不自动重放。
3. **Training store seam：** 通过公开任务级方法验证范围规范化、generation 隔离、诊断/预览失效、稳定 confirmation ID、409、历史、出件和 Job 跟踪；只替换网络边界。
4. **Vue user-flow seam：** 真实 DOM 从选择范围、分析、证据、生成、空推荐、确认、历史到出件；覆盖键盘焦点、文字摘要、错误保留和明确恢复。
5. **匿名真实浏览器 seam：** 固定生成临时双库与题库标签，运行真实 FastAPI + Vue，覆盖成功/空证据/缺题/冲突/刷新/导出以及五档桌面视口；不读取真实数据库、不调用模型。

## Business Capability and UX Traceability

| 用户任务或业务能力 | 现有业务来源 | 必须保持的语义、结果与安全边界 | 新 Vue 呈现与操作设计 | UX 调整理由 | 业务差异及用户决定 | 验证方式 |
|---|---|---|---|---|---|---|
| 选择考试范围 | `pages/训练推荐.py`、`TrainingExamScopeRequest`、Diagnosis service | 当前、全部有效考试或明确手选；只传正整数 session ID | 页首范围条以三种文字选项组织；当前考试复用已校验 session store | 让诊断范围在全流程持续可见 | 纯 UX 重组，无业务结果差异 | API/store/DOM |
| 选择学生范围 | 旧训练页、`TrainingScopeRequest`、学生 API | 单人、明确多选、整班；筛选不等于选择；实际使用数据库学生 ID 字符串 | 同一范围条支持模式、班级筛选、搜索和明确勾选；显示最终覆盖人数 | 防止把筛选结果误当成已确认范围 | 纯 UX 重组 | API/store/DOM |
| tag-only 诊断 | `DiagnosisProfileService.build_tag_profiles()`、训练 API/tests | 只读来源题当前精确 `knowledge_point`；不使用旧技能身份或二次 AI 匹配 | “分析薄弱知识点”产生诊断账本，页内明确标注“精确标签口径” | 让教师知道推荐依据而不是算法黑箱 | 无业务差异 | HTTP literal/API decoder |
| 查看掌握情况 | 旧 `_render_diagnosis()`、`TrainingWeakPoint` | mastery、得分/满分、扣分次数、证据数和考试数来自服务 | 学生×知识点热力账本；单元格显示百分比和证据数；顶部有文字摘要 | 图形便于横向发现，文字保证可访问和可核对 | 纯 UX 重组 | DOM/browser |
| 核对证据与原因 | `source_question_refs`、`actionable_reasons`、tag context | 只展示净化后的考试/题号/分数证据；不公开路径或原始异常 | 选择热力格后在同页证据栏显示考试、题号、得分率和行动原因 | 证据与薄弱点保持相邻，减少上下寻找 | 纯 UX 重组 | API/DOM |
| 处理空证据和覆盖缺口 | `coverage`、warnings、训练 API 空态测试 | 无已关联标签时不能生成；覆盖不全必须如实提示 | 账本原位显示缺少哪些题、为什么不能继续和“返回题库核对标签”提示 | 空态直接给下一步，不用猜测 | 无业务差异 | worked examples/DOM |
| 配置训练计划 | 旧训练页、`TrainingPlanRequest` | 独立/自动分组；8—12 题；默认 60/30/10；默认排除原题；只精确标签补题 | 诊断后展开紧凑计划栏；高级比例默认收起；主操作只有“生成计划预览” | 常用默认值优先，保留完整可控项 | 未迁移旧主流程没有的手工 teacher groups；用户默认同意推荐边界 | API/store/DOM |
| 理解推荐原因 | PracticePlan service、plan response/tests | 每题说明知识点、精确匹配原因、训练阶段、缺题和 warning | “推荐路径轨道”按针对训练/基础巩固/提升应用排列，每题显示原因和来源标签 | 把理由与阶段顺序编码为结构，而不是堆标签卡 | 纯 UX 重组 | decoder/DOM/browser |
| 教师确认固定任务 | `POST /api/training/tasks`、revision/idempotency tests | 当前 plan revision 必须匹配；confirmation ID 重复只建一个教师任务；空计划拒绝 | 固定确认栏显示版本数、题数和缺口；明确点击“确认并保存训练任务” | 高影响写动作与预览分开，结果可核对 | 无业务差异 | HTTP/store/concurrency |
| 选择变化与冲突恢复 | 旧 scope signature、API 409 tests | 旧预览不能在新范围保存；数据变更后不能静默确认 | 范围变化立即标记旧结果失效；409 保留预览并引导重新分析，不提供覆盖按钮 | 保留教师现场同时避免过期写入 | 安全增强，不改变成功结果 | store/DOM |
| 任务历史 | 旧 `_render_task_history()`、GET tasks/detail | 任务快照、状态、变体、warning 和时间从服务读取；训练回流未启用 | 右侧最近任务账本，选择后展示固定变体；明确说明“结果回流未启用” | 让已保存任务可复查，不暗示掌握度已更新 | 无业务差异 | exports API/DOM |
| 导出训练材料 | Training export Job、文件中心、P1-20/P2-12 tests | 只从已保存任务导出；整包或指定变体；Word/Markdown；学生/教师版；受控下载 | 当前任务提供快捷出件；活跃 Job 显示进度/取消/重试/下载；完整登记簿链接文件中心 | 保持训练闭环，又不复制全部文件管理 UI | 纯 UX 重组 | Job store/DOM/browser |
| 旧入口继续可用 | 当前 Streamlit 训练页 | Vue 尚未切为生产 UI；旧页面与服务不删除 | 导航新增真实“训练推荐”，旧入口保持不变 | 遵守 Phase 2 双入口回退 | 无差异 | route/smoke |

## Frontend Design Direction

- **主题、受众和单一任务：** 本机 AI 阅卷系统；需要把批改证据转成可发训练材料的教师；把“哪位学生哪里薄弱、为何推荐这些题、是否确认出件”连成一条可核对路径。
- **色彩：** 纸面白 `#ffffff`、工作区灰 `#f6f7f8`、主墨色 `#20242a`、次要灰 `#5c6470`、行动蓝 `#2563eb`、证据琥珀 `#9a6718`；代码只使用现有 CSS Token，不散落新颜色、不使用渐变或发光。
- **字体：** 沿用项目 Inter / 苹方 / 微软雅黑；掌握率、题数和证据计数使用现有 utility 数字排版，不增加字体依赖。
- **布局：** 页面不是数据大屏或卡片海洋。顶部是持续可见的范围条；中央先显示连续“诊断热力账本”，选中单元格后在同一行展开证据；下方是按真实训练阶段排列的推荐路径；右侧为窄任务账本和出件状态。

```text
┌ 训练推荐 ─ 精确标签口径 ─ 当前范围 2 场 / 18 人 ─ [分析] ┐
├───────────────────────────────────────────┬──────────────┤
│ 诊断热力账本                               │ 最近任务      │
│ 学生      一元一次方程  几何证明  数据分析    │ TRN-CFM…      │
│ 张同学      42% · 3证据   —       68% · 2   │ 3 个训练版本  │
│ 李同学      55% · 2证据  31% · 4   —        │ [进入出件]    │
├───────────────────────────────────────────┤ 活跃导出 Job  │
│ 推荐路径：针对训练 → 基础巩固 → 提升应用      │ 进度/重试/下载 │
│ Q12 精确匹配「一元一次方程」 · 推荐原因…       │              │
└───────────────────────────────────────────┴──────────────┘
```

- **签名元素：** “证据—推荐路径”联动。选择一个热力单元格时，其证据摘要和下方精确匹配题沿一条克制的细轨道对齐；轨道表达真实的“薄弱点→证据→推荐题”关系，不是装饰编号。
- **可访问性：** 热力单元格是带学生、知识点、掌握率和证据数名称的原生按钮；所有信息有文字，焦点明显；任务状态不只靠颜色；`prefers-reduced-motion` 下取消联动位移动效。
- **自我复核：** 初稿若做成指标卡+彩色热力图，会像通用 BI 大屏且让证据被颜色吞没；已改为可读账本与单条推荐轨道，唯一视觉风险用于表达真实因果关系，其余保持项目安静、高密度语言。

## Planned Interfaces

后端不新增业务接口，复用：

- `POST /api/training/diagnosis`
- `POST /api/training/plans/preview`
- `POST /api/training/tasks`
- `GET /api/training/tasks`
- `GET /api/training/tasks/{task_id}`
- `POST /api/training/tasks/{task_id}/exports`
- `POST /api/training/exports/jobs/{job_id}/retry`
- 通用 Job 查询、取消与下载接口

前端新增 `training.ts` 类型化 API 与 `training.ts` Pinia store。Store 对外任务级入口：

```ts
loadReferenceData()
setStudentScope(scope)
setExamScope(scope)
analyze()
previewPlan(config)
confirmPlan()
loadTasks()
selectTask(taskId)
submitExport(choice)
retryExport(jobId)
```

Store 隐藏 scope signature、generation、AbortController、confirmation ID、plan revision、旧响应隔离、冲突投影、任务历史和 Job 跟踪。Vue 组件只消费这些任务级接口。

## Delivery Slices

### Slice 0：领取、计划与安全基线

- [x] 首个 first-parent 提交只包含本即时计划和合法 `in_progress` 交接块。
- [x] handoff validator 以最新 `origin/main` 验证领取关系。
- [x] 功能 worktree 源码与 `user_data/` 状态干净；源码区无外部 reparse point。
- [x] 真实两库只读基线已记录：`grading_system.db` SHA-256 `93fee56e23ea072ac48351b1e6616d7af7f4b35ceb2b4779890e8d059fb841cd`、`question_bank.db` SHA-256 `e1e5123ad54c9e8af5984bdcc5182a8f7a3038a1707f98ab26f168f4577a88b8`。
- [x] 第二个文档提交把功能分支所见 Index 的 P2-18/当前动作改为 `in_progress`；首个源码测试前完成。

### Slice 1：Typed Training API

- [x] RED：合法 diagnosis/plan/task worked examples；路径字段、旧 skill identity、非法 revision、越界 mastery、畸形证据/变体。
- [x] GREEN：严格类型、decoder 与 diagnosis/preview/confirm 请求；复用既有 task/export API，不修改网络 client。

### Slice 2：Training Store 范围与诊断

- [x] RED：单人/多选/整班、当前/跨考试/手选范围；选择变化废止旧结果；迟到 response；空证据和覆盖缺口。
- [x] GREEN：规范化 scope、generation/abort、诊断状态、文字摘要和证据选择。

### Slice 3：计划预览与幂等确认

- [x] RED：默认 60/30/10、8—12 题、独立/自动分组、只精确标签、无推荐、缺题；同 revision 稳定 confirmation ID；409 与人工重试。
- [x] GREEN：计划配置、路径投影、preview revision、确认状态和当前任务。

### Slice 4：历史、导出与 Job 恢复

- [x] RED：最近任务、详情、整包/变体导出、学生/教师版、tracked Job、取消、failed/cancelled retry、成功下载和过期提示。
- [x] GREEN：复用 exports API 和通用 Job Store；页面只做训练任务级编排，完整登记簿跳转文件中心。

### Slice 5：Vue 工作台、路由与视觉状态

- [x] RED：范围、诊断热力账本、文字摘要、证据下钻、推荐路径、确认栏、历史/出件、加载/空白/错误/禁用/焦点。
- [x] GREEN：`TrainingRecommendationsView.vue`、克制的专用 CSS、导航/路由/main 注册；五档视口和长中文不溢出。

### Slice 6：匿名真实浏览器、文档与稳定候选

- [x] 固定临时双库启动真实 API + Vue；覆盖成功、空证据、缺题、revision 冲突、确认重试、刷新历史、导出和下载。
- [x] 运行受影响 Python/前端测试、`npm run verify`、同一构建的 prepared Chromium 五视口和 `tools/smoke_check.py --skip-tests`。
- [x] 更新 `ARCHITECTURE.md`、即时计划阶段记录和版本化 quick 清单；记录真实两库指纹未变。
- [x] 冻结候选 SHA，按 Spec/Standards 双路复审；阻塞项统一修补，最多三次授权内完成限定最终复审。

## Expected Files

**Create：**

- `frontend/src/api/training.ts`
- `frontend/src/stores/training.ts`
- `frontend/src/views/TrainingRecommendationsView.vue`
- `frontend/src/styles/training-recommendations.css`
- 对应 API/store/view 单元测试与匿名浏览器检查
- `tools/p2_18_browser_server.py`
- `docs/user-testing/checkpoints/P2-18-v1.5.0-training-recommendations-quick-check.md`

**Modify：**

- `frontend/src/navigation.ts`、`frontend/src/router/index.ts`、`frontend/src/main.ts`：真实训练推荐入口与样式注册。
- `frontend/src/api/exports.ts`：仅在训练工作台需要更严格公开类型时做向后兼容收紧，不改变接口。
- `frontend/package.json`：只增加 P2-18 prepared 浏览器命令，不修改依赖或 lockfile。
- `ARCHITECTURE.md`：P2-18 已实现事实与边界。
- `docs/superpowers/packages/EXECUTION_INDEX.md`：领取、稳定候选与 integration 精确状态。

## Verification Strategy

- **当前反馈：** 每个 vertical slice 先写一个公开 seam 的失败测试并确认 RED，再做最小实现确认 GREEN；不预先堆积全部测试，不测试 Vue 私有实现。
- **Backend：** 现有 Training route/task/export/job 聚焦回归确认契约未被前端假设改变；没有后端源码变化时不重复跑无关测试。
- **Frontend：** API decoder → store → view → navigation/router；每片运行受影响测试，稳定候选统一运行 `npm run verify`。
- **Browser：** 固定匿名临时服务；同一已构建 SHA 验证范围、诊断、证据、空态、预览、冲突、确认、历史、导出和五档桌面视口。
- **功能分支：** 不运行完整 pytest；交接前运行包内聚焦回归、受影响 API/前端回归和 `tools/smoke_check.py --skip-tests`。
- **里程碑：** P2-18 合入 M2-03 后只运行本包受影响验证；M2-03 批次末才运行一次组合前端/浏览器和完整门槛。

## Review and Fix Policy

稳定候选冻结后使用 `code-review`，让 Spec 与 Standards 两名评审代理检查同一 SHA。全部意见返回后由主代理统一去重、按根因归组并核对来源；只有本次修改直接引入或当前任务遗漏的 `Critical`/`Important` 阻塞。若有阻塞问题，只做统一修复和受影响复测，再由原评审者做限定最终复审；用户已预先授权最多三次复核问题修补，超过三次或最终复审仍有 `Critical`/`Important` 时停止并汇报。

## Stage Record

| 阶段 | 耗时 | 测试/复审 | 原始意见 | 去重结果 | 剩余工作 | 当前包验收 | 版本可发布 |
|---|---:|---|---:|---|---|---|---|
| 集中调查与计划 | 约 35 分钟 | 必读资料、Phase map、旧训练页、Diagnosis/Plan/Task/Export API、P2-12 File Center、前端 session/student/Job 基础设施、worktree/数据守卫 | 0 | 5 个根因组 | 实现、自动门槛、复审、quick 证据、integration | 否 | 否 |
| 实现与稳定候选 | 约 2 小时 20 分钟 | TDD 完成 API/store/view/导航；前端完整门槛 645 passed；后端训练相关 58 passed；匿名 Chromium 1 passed；快速冒烟和五档视口通过 | 0 | 0 Critical / 0 Important | 冻结提交、双路复审、quick 证据、integration | 否 | 否 |
| 并行初审 | 约 11 分钟 | 同一冻结 SHA `96580a1f4ce641b6d3a7bac6bdde047d10637102` 的 Spec/Standards 双路复审 | 9 | 7 Important：Job 刷新恢复、任务详情、配置失效/阶段比例、确认切范围、覆盖缺口、指定版本出件、过期下载 | 一次统一修复、受影响复测、限定最终复审 | 否 | 否 |
| 统一修复 | 约 35 分钟 | 7 个根因一次修复；新增/受影响 store+view 13 passed，typecheck/lint/build 通过，匿名 Chromium 覆盖导出后刷新恢复并通过 | 0 | 首轮 7 Important 均已有代码与测试关闭证据 | 原评审者限定最终复审、quick 证据、integration | 否 | 否 |
| 限定最终复审 | 约 5 分钟 | 原 Spec/Standards 评审者只检查首轮问题、修复区域和直接回归；受影响 13 passed 证据复核 | 0 | 0 Critical / 0 Important；1 条“比例默认展开”Suggestion 不阻塞 | quick 证据、integration | 自动与独立复审通过 | 否 |

## Stable Candidate

- **已复审功能提交：** `96f6e463be59308fbbc9885bc4c32d14c8ebf064`
- **实现状态：** 已完成明确考试/学生范围、tag-only 诊断与具体覆盖缺口、可调阶段比例、精确标签计划、幂等确认与 revision 冲突、任务详情、导出 Job 刷新恢复/取消/重试/过期提示和受控下载。
- **不包含：** 未修改诊断或推荐算法、数据库 Schema、导出格式、模型策略、生产 UI；未写入真实 `user_data/`。
- **自动验证：** 前端完整门槛 645 passed；后端训练相关回归 58 passed；统一修复后受影响 13 passed；typecheck/lint/build、匿名 Chromium 五视口与快速冒烟均通过。
- **复审：** 首轮 Spec/Standards 原始 9 条，去重为 7 个 Important；统一修复后由原评审者限定最终复审，剩余 0 Critical / 0 Important。
- **剩余工作：** 写入用户预授权 quick 通过证据，完成最终交接并合入 M2-03 integration。
- **当前包验收：** 自动验证与独立复审通过；用户 quick 结论待证据提交。
- **版本发布：** P2-18 尚未进入 integration，M2-03 尚未完成批次末门槛，当前版本不允许发布。

## Ownership

P2-18 功能分支拥有 Vue Training API/store/view/CSS、导航/路由接入、匿名浏览器工具、即时计划、P2-18 架构增量和 quick 自测清单。后端现有 Training/Export/Job 接口保持原样；integration 负责最终 Index 冲突裁定、里程碑精确 SHA、M2-03 逐包验证以及后续 P2-19 基线。

## Rollback and Stop Conditions

- 功能回退为整体 revert P2-18 提交并继续使用旧 Streamlit 训练页和现有文件中心；不删除已保存任务或导出文件。
- 发现必须修改诊断身份、推荐算法、数据库 Schema、训练回流、评分结果、题库标签、模型策略或导出格式时停止。
- 需要真实业务数据写入、真实密钥、真实模型调用、宽泛标签补题、客户端目标路径或不可逆删除时停止。
- 公开结果泄露内部路径、题干之外的私有存储字段、密钥或原始异常，或真实两库指纹未经授权变化时立即停止。
- 超过用户预授权的三次复核修补，或最终限定复审仍有 `Critical`/`Important` 时停止。

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P2-18
**交接状态：** waiting_user
**功能提交：** `96f6e463be59308fbbc9885bc4c32d14c8ebf064`
**自动验证：** passed
**独立复审：** passed
**用户验收：** pending
**真实数据指纹：** not_touched
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
