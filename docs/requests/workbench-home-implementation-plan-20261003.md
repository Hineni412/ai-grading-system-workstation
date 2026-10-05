# 工作台首页改版：实施方案

- 用途：交给实施方按此方案把 `/workbench` 首页改成“待处理清单 + 当前考试 + 本学期学情 + 最近考试”的结构。
- 依据：用户已确认原型 `docs/requests/workbench-home-prototype-20261003/`（合成数据，原型控制可切换 5 种情况）。
- 状态：尚未实现。本文分两期：第一期只用现有数据和现有接口，外加两处小的只读字段调整；第二期新增三个只读汇总。第二期开始前再与用户确认。

## 1. 已确认的决定

1. 首页主体是“待处理”清单，按“正在运行 / 需要处理 / 可以继续”分组；整张清单只有第一条可操作事项用主要按钮。
2. 删除旧首页的问候大标题与“今天先完成这两件事”、写死的“2 项优先工作”、英文眉题（TEACHING FOCUS、CURRENT EXAM、ONE CONTINUOUS LOOP）、“教学脉搏”深色卡片（圆环 + 三个数字）和底部“考试 → 批改 → 学情 → 训练”四步条。
3. 保留“本学期学情”面板，但它在考试数据之后异步读取，不阻塞首页其余内容。
4. 第一期不显示需要新增统计的事项（训练回收待办、个人报告需重新生成、本场未挂技能）；这三项放在第二期。
5. 页面标题只出现一次（STYLE.md：桌面端没有顶栏）。

### 1.1 与原型的差异（按现有数据如实调整）

- 当前考试与最近考试不显示班级和参考人数，改为显示答卷份数；日期是考试创建日期。工作台概况目前没有这两项数据。
- 最近考试去掉“复核 N 项”列，只用“有待复核”标签：其他考试只有成绩记录上的复核标记，与当前考试的复核项数口径不同。
- 学情面板按明显薄弱人数排序（与学情总览一致），原型写的是按占比；底部改为“本册 N 项，M 项有证据”。
- “补齐题库资料”指本场题目的难度、标签、解题证据或判定点未完成，不是原型里的“未挂技能”；“未挂技能”放第二期。
- “看卷 10 分钟”“按失分题组讲义”第一期不显示数量；训练回收、个人报告两行放第二期。
- 正在运行的任务不放“查看”按钮，任务中心入口已在侧栏。

## 2. 页面结构

```
PageHeader：工作台 ｜ 元信息：{问候} · {M月D日 星期X} · {教学学期名}      [新建考试]（次要按钮）
（考试概况读取失败时）FeedbackBanner：考试概况暂时无法读取，其他入口仍可使用。[重新加载]
┌──────────── 左栏 minmax(0,1fr) ─────────────┐ ┌── 右栏 340px，sticky ──┐
│ 待处理  N 项                                 │ │ 当前考试               │
│   正在运行 / 需要处理 / 可以继续              │ │   5 个步骤竖排         │
├──────────────────────────────────────────────┤ ├────────────────────────┤
│ 最近考试  近 5 场（紧凑表格）                │ │ 本学期学情（前 3 项技能）│
└──────────────────────────────────────────────┘ └────────────────────────┘
```

- 视口宽度不小于 1200px 时两栏，右栏 `position: sticky; top: var(--space-4)`；较窄时单栏，顺序为 待处理 → 当前考试 → 本学期学情 → 最近考试。
- 面板使用现有内容面外观（白底、细边框、`--shadow-raised`、面板圆角）；小标题中文，计数灰色紧跟标题。
- 页面进入使用 `fx-enter`，面板 `fx-stagger`；不加新的关键帧。进行中任务的细条可沿用现有进度条运行动画，减少动态效果设置下关闭。
- “新建考试”跳转 `/sessions`（与侧栏考试切换卡的“新建考试”相同）。
- 元信息中的问候沿用现有按小时规则（<11 早上好，<14 中午好，<18 下午好，其余晚上好）；学期名沿用 `curriculumScope.selectedVolume?.label`，未选择时写“未限定教学学期”。

## 3. 待处理清单（左栏第一块）

### 3.1 通用规则

- 每行：模块标签（`StatusBadge`）｜标题（对象 + 动作 + 数量）与一行事实｜一个按钮。
- 模块标签：考试（info）、题库（ai 色调或现有最接近色调）、训练（success）、任务（neutral）。标签同时有文字，不只靠颜色。
- 按钮：整张清单第一条“需要处理”事项用 `AppButton variant="primary"`；其余“需要处理”用 `secondary`；“可以继续”用 `ghost` 并带“→”。若“需要处理”为空，第一条“可以继续”也不升级为主要按钮。
- 标题旁计数“N 项”= 需要处理与可以继续两组的行数之和，不含正在运行；单位是事项条数，不是学生数或作答记录数。
- 某一数据来源读取失败时，只隐藏该来源的行，并在清单末尾加一行灰色提示与“重试”（例：“题库资料状态暂时无法读取 [重试]”）。所有行都为空且无失败时显示一句“今天没有待处理的事项”。
- 只有已选当前考试时才生成考试类与题库类事项；未选考试时清单只可能有“正在运行”。

### 3.2 正在运行

- 来源：`useJobStore().jobs` 中未结束的任务，与侧栏任务中心同一来源（本机浏览器跟踪的任务），不另读接口；名称与阶段用 `components/shell/task-center-format.ts` 的 `taskName`、`taskDetail`。
- 每行：任务标签 ｜ 名称 ｜ 行内细条 + `Math.round(progress*100)%` ｜ 阶段文字。不放按钮（任务中心入口已在侧栏底部）。
- 最多 3 行，超出写一行“还有 N 项，见任务中心”。

### 3.3 需要处理（按下列顺序，满足条件才出现）

以下 `p` 指 `overview.progress`，`r` 指 `overview.review`，`a` 指 `overview.anomalies`，`s` 指现有 `GET /api/sessions/{id}/regions/readiness` 的 `scoring_configured`（评分依据）与 `template_ready`（样卷题框），“就绪”指两项都为真，`id` 为当前考试。

| 顺序 | 出现条件 | 标题 | 事实行 | 按钮 → 去向 |
|---|---|---|---|---|
| 1 | `s` 未全部就绪 | 完成考试配置 | 未就绪项按“评分依据未完成”“样卷题框未确认”列出，用“，”连接 | 继续配置 → 评分依据未完成时 `/sessions`；仅题框未确认时 `/sessions/{id}/regions` |
| 2 | `s` 就绪且 `p.total_papers = 0` | 上传答卷 | 评分依据与样卷题框已就绪 | 上传答卷 → `/sessions/{id}/grading-run` |
| 3 | `a.unmatched_papers + a.scan_issue_students + a.failed_papers > 0` | 处理答卷异常 | 只列非零项：“{unmatched} 份答卷未匹配学生”“{scan_issue} 名学生扫描页有问题”“{failed} 份批改失败” | 处理异常 → `/sessions/{id}/grading-run` |
| 4 | `p.matched_papers > 0`，`p.graded_papers + p.failed_papers < p.matched_papers`，且正在运行组中没有本场批改任务 | 继续批改 | 已批改 {graded}/{matched} 份 | 继续批改 → `/sessions/{id}/grading-run` |
| 5 | `r.item_count > 0` | 复核 {item_count} 项评分 | 涉及 {question_count} 道题 | 开始复核 → `/grading` |
| 6 | 题库资料状态读取成功，`incomplete_question_ids.length > 0` | 补齐本场 {n} 道题的题库资料 | 难度、标签、解题证据或判定点未完成 | 去补齐 → `/question-bank?tab=todo` |

说明：

- 第 3 行不写合计数：三项分别以答卷份数和学生人数计，单位不同，直接相加会误导。
- 第 4 行的分母用已匹配答卷数，与后端 `progress_percent = (已批改 + 批改失败) / 已匹配` 一致；批改失败已在第 3 行单独列出。
- 第 5 行沿用现有工作台对 `review.item_count` 的含义（复核服务逐题 `needs_review_count` 之和），单位写“项”，与考试批改页一致。
- 第 6 行 `n` = 本场已确认关联、未删除的题库题目中，难度、标签、解题证据、判定点四项不全的题数（`question_read_service.session_analysis_status` 的口径）。它不是“未挂技能”，文字不得写“未挂技能”或“不计入学情”。`incomplete_source_refs` 是题库题目自身的来源题号（缺失时为 `Q{题库编号}`），可能与本场考试题号不同，首页不显示。

### 3.4 可以继续

| 出现条件 | 模块 | 标题 | 事实行 | 按钮 → 去向 |
|---|---|---|---|---|
| `p.graded_papers > 0` | 考试 | 看卷 10 分钟 | {考试名} · 已批改 {graded}/{matched} 份 | 去看卷 → `/results?tab=overview&open=walkthrough` |
| `p.matched_papers > 0`，`p.graded_papers + p.failed_papers = p.matched_papers`，`r.item_count = 0` | 组卷 | 按失分题组讲义 | 班级组卷默认依据最近两场考试，列出得分率低于 70% 的题 | 去组卷 → `/question-assembly?mode=assistant` |

- 模块标签在 3.1 的基础上增加“组卷”，色调与“题库”相同。
- “看卷 10 分钟”需要成绩中心配合（见 6.3）：带 `open=walkthrough` 进入时，在考情总览自身的 `walkthroughAvailable` 与 `walkthroughReady` 都为真后自动打开一次，随后用 `router.replace` 去掉该参数；条件不满足时停在考情总览，不报错。
- “按失分题组讲义”不预选本场考试：班级组卷当前没有考试预选参数，事实行如实写它的默认依据。第一期不显示题数或技能数（需要候选计算，见第二期）。

## 4. 右栏

### 4.1 当前考试

- 标题“当前考试”；考试名最多两行；元信息“{formatSessionDate(created_at)} · {total_papers} 份答卷”。第一期没有班级名和参考人数（工作台概况不含这两项）。
- 步骤使用 `design-system/StepProgress` 新增的竖排模式（见 6.4），5 步：

| id | 标签 | done | in_progress | todo | 点击去向 |
|---|---|---|---|---|---|
| setup | 考试配置 | `s` 两项都就绪，提示“已完成” | 其余情况，提示未就绪项 | — | 同 3.3 第 1 行 |
| prepare | 答卷与预检 | `total>0` 且 `unmatched=0` 且 `scan_issue=0`，提示“{matched} 份已匹配” | `total>0` 且有未匹配或扫描问题，提示“{matched}/{total} 份已匹配” | `total=0`，提示“待上传” | `/sessions/{id}/grading-run` |
| grade | 批改 | `matched>0`，`graded+failed=matched` 且 `failed=0`，提示“{graded}/{matched}” | `matched>0` 且未全部完成，或 `failed>0`；提示“{graded}/{matched}”，有失败时加“ · 失败 {failed}” | `matched=0`，提示“—” | 同上 |
| review | 复核 | grade 为 done 且 `r.item_count=0`，提示“0 项” | `r.item_count>0`，提示“{n} 项待复核” | grade 未完成，提示“等待批改” | `/grading` |
| results | 成绩与讲评 | grade 与 review 都为 done，提示“可查看” | — | 其余，提示“复核后可查看” | `/results` |

- `current` 为第一个非 done 的步骤；全部 done 时为 results。未选考试时所有步骤不可用。
- 底部链接“成绩中心 →”去 `/results`。
- 状态：加载中用内容骨架；未选考试显示“尚未选择考试”、主要按钮“新建考试”（`/sessions`）和一句“可在左侧“当前考试”中切换”；读取失败显示“考试概况暂时无法读取”和“重新加载”；`stale-error` 保留已有数据，顶部提示沿用现有“考试数据可能不是最新 · 上次更新 …”。

### 4.2 本学期学情

- 数据来源：`useMasteryOverviewStore`，与学情总览共用同一请求和结果。首页先渲染其余内容，再读取；读取完成前显示 3 行骨架。
- 首页读取不得改写证据范围：现有 `load()` 会调用 `saveEvidenceScope`，`activate()` 还会读取学生名册。新增 `peek(volumeId)`：按 `activate` 的同一规则从已保存范围推出 `scopeSelection`，然后读取总览，但不保存证据范围、不读取名册。`load` 增加第三个参数 `persistScope = true`，`peek` 传 `false`；其他调用方不变。
- 元信息：“{学期名} · {全部学生 或 班级名}”，班级名沿用学情总览范围条的显示方式。
- 行：取 `volumeItems(nodes).filter(n => n.kind === 'skill' && n.distribution.weak > 0).sort(compareFocusNodes)` 的前 3 项，与学情总览“本周建议优先处理”的前 3 项完全相同。每行显示 `shortNodeName(node)`、第二行 `nodeLocation(node, nodes)`、行内细条（宽度 = `weakRate(node)`），右侧“{weak}/{evidence_student_count} 人明显薄弱”。分子是明显薄弱学生数，分母是该技能有作答证据的学生数；排序按明显薄弱人数，不按占比。
- 底部一行：“本册 {total} 项，{evidence} 项有证据”（`overviewMetrics`，项 = 本册知识点与技能），链接“学情总览 →”去 `/knowledge-overview`。点击技能行也去 `/knowledge-overview`。
- 状态：未选教学学期“选择教学学期后显示学情”；读取失败“学情暂时无法读取”加“重试”（`load(volumeId, true, false)`）；本册没有任何有证据项“本学期还没有学情证据”；有证据但没有明显薄弱技能，沿用学情总览文字“当前范围没有明显薄弱的技能”。

## 5. 最近考试（左栏第二块）

- 数据来源：`overview.recent_sessions`；后端按教学学期筛选（见 6.2），规则与侧栏考试切换卡相同：属于当前教学学期或就是当前考试；未限定学期时不筛选。最多 5 场，按考试编号倒序。
- 列：考试（名称按钮 + 当前考试后加“当前”标签）｜创建日期（`formatSessionDate(created_at)`）｜答卷（`{total_papers} 份`）｜批改（行内细条 + `{graded}/{matched}`）｜状态（`sessionStatusLabel` + `StatusBadge`；`progress.needs_human_review > 0` 时另加“有待复核”标签，不写数字；当前考试行改用 `r.item_count > 0` 判断，避免与右栏“{n} 项待复核”不一致）｜操作。
- 操作：当前考试行不放操作；其他行“看成绩”。点击“看成绩”或考试名都先用 `useSessionSwitch().switchSession(id)` 切换当前考试（沿用未保存修改与待核对保护，守卫拒绝时停留原处），成功后点“看成绩”的再进入 `/results`，点名称的留在首页。
- 当前考试行用浅色主色底标出，同时有“当前”文字。
- 空状态：“还没有考试”加次要按钮“新建考试”。读取失败时整块显示“考试概况暂时无法读取”。
- 不再提供快速归档按钮；归档沿用考试管理。`components/workbench/RecentSessions.vue` 目前没有被引用，随本次删除。

## 6. 第一期改动清单

### 6.1 前端：首页本身

- `frontend/src/views/WorkbenchView.vue`：按第 2–5 节重写模板；保留“所选考试变化 → `resetForSession` + `loadOverview`”的监听。加载顺序：考试概况与样卷就绪状态并行；概况成功且已选考试后读题库资料状态；首屏渲染后调用学情 `peek`。停止引用 `AnimatedCircularProgressBar`、`NumberTicker`（组件本身不删，其他页面可能使用）。
- 新建 `frontend/src/components/workbench/` 下四个展示组件：`WorkbenchTodoList.vue`、`WorkbenchCurrentExam.vue`、`WorkbenchSemesterInsight.vue`、`WorkbenchRecentExams.vue`；以及纯函数文件 `workbench-home.ts`，导出 `buildTodoRows(inputs)` 与 `buildExamSteps(inputs)`，把第 3、4.1 节的条件表写成可单测的规则。不建注册表或策略层。删除未被引用的 `RecentSessions.vue`。
- `frontend/src/styles/workbench.css`：整体重写为新布局；`ui-effects.css` 中只服务旧首页的 `.workbench-primary-button`、`.workbench-secondary-button`、`.workbench-link-button` 选择器，确认无其他引用后一并删除。按钮改用 `AppButton`，状态用 `StatusBadge`，空白/加载/失败用 `StatePanel` 或一句话加按钮，顶部提示用 `FeedbackBanner`。
- `frontend/src/api/workbench.ts`：`fetchWorkbenchOverview(sessionId, curriculumVolumeId, signal)` 在有学期时加 `curriculum_volume_id` 参数；`frontend/src/stores/workbench.ts` 的 `loadOverview` 同步加参数，学期切换时重新读取。解码器字段不变（`curriculum_volume_id` 已在严格键中）。
- `frontend/src/api/template-regions.ts` 的 `fetchRegionReadiness` 与 `frontend/src/api/session-question-bank-sync.ts` 的 `getSessionQuestionBankAnalysisStatus` 各加可选 `signal`，切换考试时取消旧请求，旧结果不得写入新考试。
- `frontend/src/stores/mastery-overview.ts`：按 4.2 增加 `peek` 与 `persistScope` 参数。

### 6.2 后端：工作台概况两处只读调整

- `backend/workbench/service.py` 的 `_session_summary` 输出 `curriculum_volume_id`。现状是该字段始终为 `null`（模式默认值），前端拿不到考试所属学期。
- `GET /api/workbench/overview` 增加可选 `curriculum_volume_id`：有值时最近考试只保留该学期考试或当前考试，再取前 `recent_limit` 场；无值时行为不变。不新增其他字段，不改变 `progress`、`review`、`anomalies` 的计算。

### 6.3 成绩中心：看卷自动打开

- `frontend/src/components/results-center/ResultsOverviewPanel.vue`：路由带 `open=walkthrough` 时，等 `walkthroughAvailable && walkthroughReady` 首次为真后调用现有 `openWalkthrough()` 一次，并用 `router.replace` 去掉 `open`；用户关闭后不再自动打开。不改抽卡规则和续看记录。

### 6.4 设计系统：StepProgress 竖排

- `frontend/src/components/design-system/StepProgress.vue` 增加 `orientation?: 'horizontal' | 'vertical'`，默认 `horizontal`，现有页面外观与行为不变。竖排时各步纵向排列，连接线改为节点之间 2px 竖线，`hint` 作为可见文字右对齐显示（横排仍只作 `title` 与 `aria-description`）。
- 这是跨页面共享文件：只有首页传 `vertical`；修改后核对考试批改页的横排步骤外观不变。

## 7. 第二期：新增只读汇总（开始前再确认）

| 事项 | 建议位置 | 输出 | 首页行 |
|---|---|---|---|
| 训练回收待办 | 训练模块新增 `GET /api/training/pending-summary` | 按训练卷草稿分组：扫描页待人工匹配页数、判定点待教师复核的提交份数、已复核未发布证据的提交份数，各带草稿名称与 `/training?mode=paper&draft=…` 去向 | “核对 {n} 页训练卷扫描”“复核 {草稿名} 训练卷判定 {n} 份”“发布 {草稿名} 训练证据 {n} 份”，归入“需要处理” |
| 个人报告状态 | 工作台概况为当前考试增加 `personal_reports: {current, stale, missing}`（单位：学生数） | 读取个人报告索引，不生成、不调用模型 | `stale > 0` 时“个人报告：{stale} 人需重新生成”，事实行“{current} 人已是最新”，去 `/results?tab=details`，归入“可以继续” |
| 本场未挂技能 | 题库资料状态增加 `unlinked_skill_count` | 复用 `question_bank/services/question_skill_index.py` 的未挂技能口径，只统计本场已确认关联题目 | “本场 {n} 道题未挂技能”，事实行“这些题的判定点暂不进入技能掌握度”（实施时按 `CONTEXT.md` 的“未挂技能”定义核对措辞） |

- 训练状态字段（扫描批次 `manual_review`、提交复核与证据发布状态）必须先在训练提交模块中核对实际状态名，再写查询；本文未核实这些状态与计数口径。
- 个人报告相关代码（`backend/personal_reports.py` 等）当前由另一项任务开发且未提交，第二期须等它进入主线。
- 不建议给“看卷 10 分钟”和“按失分题组讲义”加数量：前者的抽卡规则是前端纯函数（`paper-walkthrough.ts`），后者需要完整候选计算，移到后端会形成两份规则。

## 8. 保持不变

- 首页只读：除切换当前考试（本机状态）外不发起写入；不调用模型，不产生费用。学情读取使用现有 `POST /api/training/overview`，这是只读计算。
- 评分、复核、教师最终分锁、成绩、报告、训练证据和掌握度的含义与计算不变。首页学情数字与学情总览来自同一请求和同一排序。
- 首页不改写证据范围，不改变训练页、学情总览已保存的班级或学生选择。
- 切换考试沿用 `useSessionSwitch` 的未保存修改与待核对保护。
- 各页面原有地址和侧栏导航不变；旧首页指向 `/knowledge-graph`、`/question-assembly` 的入口按新清单的去向替换。

## 9. 测试与验证

- 前端单元：重写 `frontend/src/__tests__/workbench-view.spec.ts` 中两条旧用例（断言的是已删除的四步条和脉搏卡），覆盖：批改中、待复核、已完成、未选考试、概况失败五种情况的行、顺序与唯一主要按钮；各按钮去向（含 `open=walkthrough`）；异常行不写合计；题库资料状态失败只隐藏该行并出现重试；`buildExamSteps` 的边界（`total=0`、`failed>0`、复核未读到）；学情前 3 项与学情总览排序一致、显示“{weak}/{evidence} 人明显薄弱”、`peek` 不调用 `saveEvidenceScope`；切换考试时旧请求结果被丢弃。
- `knowledge-overview-view.spec.ts`：补断言，`activate` 仍会保存证据范围。
- `frontend/src/components/results-center/__tests__/results-overview.spec.ts`：补 `open=walkthrough` 自动打开一次、条件不满足不打开。
- StepProgress 目前没有专属测试：在 `workbench-view.spec.ts` 中断言竖排可见提示；考试批改页现有用例保持通过即证明横排未变。
- 后端：仓库没有工作台概况接口的专属测试，新建 `tests/test_api_workbench_overview.py`，覆盖 `curriculum_volume_id` 输出、按学期筛选、当前考试不在该学期时仍保留、无参数时行为不变。
- 浏览器：`frontend/e2e/workbench-overview.spec.ts` 已与现有首页不符（仍断言“今天先完成这三件事”和 3 条焦点），按新布局重写；模拟接口补齐样卷就绪、题库资料状态和学情总览；只读断言放行 `POST /api/training/overview`；在 1024、1280、1440、1920 宽度检查无横向溢出、右栏吸顶、长考试名不遮挡。
- 命令：`npm run typecheck`、`npm run lint`、`npx vitest run` 跑上述文件、`npx playwright test e2e/workbench-overview.spec.ts`；后端 `pytest tests/test_api_workbench_overview.py`。真实数据只读查看首页，记录首次打开与刷新时间。

## 10. 文档更新（随实现同一次提交）

- `docs/ui/STYLE.md`：增加一条首页布局规则（两栏宽度、断点、清单唯一主要按钮、竖排步骤）。
- `ARCHITECTURE.md`“运行与前端开发”：写明首页只读组合考试概况、样卷就绪、题库资料状态和学情总览，且不保存证据范围。
- `docs/testing/README.md`：登记工作台单元与浏览器测试入口。
- `CONTEXT.md`：无新词，无需修改。

## 11. 风险与待核实

- 题库资料状态接口还会读取待审标签提议数量，耗时未测；第一期上线前测一次，若明显拖慢首页，再考虑只读更轻的版本。
- 学情总览在合成数据上首次无缓存约 4.7 秒、缓存命中约 0.15 秒（见 `knowledge-training-two-second-performance-20261003.md`）；首页不等待它，但面板可能在首次打开时较晚出现。
- 正在运行组与任务中心一样只显示本机浏览器跟踪的任务。
- 第一期依赖未提交的 `frontend/src/components/shell/task-center-format.ts`（另一项任务）；实施前确认它已进入主线，否则先协调。
- `StepProgress.vue`、`ui-effects.css`、`ResultsOverviewPanel.vue` 是共享或他模块文件，修改前查看是否有并行未提交改动。
