# 任务中心改版实施方案（方案一：右下角圆环 + 浮层列表）

用途：交给实施代理的已确认方案，现已实施。用户已在原型中选定方案一；参考原型见 `task-center-prototype-20261012/`（只用合成数据，原型与本文冲突时以本文为准）。当前产品规则见 `../product/GRADING.md`，原型保留作取舍依据。

## 1. 要解决的问题

后台任务（主要是调用模型的长任务）运行时，教师应能放心去系统其他地方做事。现状做不到：

- 入口只是侧栏底部一行“任务中心”（`frontend/src/components/shell/TaskCenterPopover.vue`），进行中数量只是一个小数字，侧栏收起为图标栏时更难发现。
- 任务开始和结束都没有任何提示。`stores/jobs.ts` 里的 `latestTrackedJobId`、`jobNoticeRevision` 和 `AppShell.vue` 的 `--shell-sidebar-offset` 是旧提示浮卡的残留，已无使用者。
- 结果说明写“请返回原操作页面”，却没有跳转按钮。
- 已结束任务一直堆在列表里，没有自动清理。

## 2. 已确认的决定

| 项目 | 决定 |
|---|---|
| 常驻入口 | 所有页面右下角一个 40px 圆形按钮，只显示进度圆环，不显示文字；侧栏里的“任务中心”入口移除 |
| 列表 | 点圆环弹出约 360px 浮层，分“进行中 / 需要处理 / 已结束”三组 |
| 完成提醒 | 圆环上方弹出结果卡片，6 秒后自动收起；底部 4px 彩色倒计时条，悬停暂停 |
| 自动清除 | 已查看的完成任务 10 分钟后移出列表；需要处理的任务保留到点“去查看” |
| 开始提示 | 任务开始时底部短暂提示“已在后台开始：任务名，可以去做别的事” |
| 原有进度弹框 | 运行中只留“转到后台”一个按钮，删去作用相同的“关闭” |
| 范围 | 全部后台任务；运行不超过 5 秒的短任务只记入列表，不弹结果卡片 |

不改：后台任务状态、取消规则、评分、训练证据、模型调用次数与费用、任务名称与结果判定文字（`task-center-format.ts` 的 `taskName`、`taskOutcome`、`taskDetail`、`taskScope` 保持现有含义）。移出列表只是不再显示，不删除服务器上的任务记录和任何业务结果。

## 3. 界面规则

### 3.1 圆环按钮

位置固定在视口右下角（距右、下各 20px），挂在 `AppShell.vue`，所有页面可见；层级低于对话框遮罩，高于页面内容。

| 状态 | 显示 |
|---|---|
| 有任务进行中（含等待中、正在取消） | 主题色圆弧，长度为进行中任务的平均进度 |
| 无进行中，有未查看结果 | 满圈绿色加 ✓；未查看结果中有“需要处理”的任务时改为琥珀色加 ! |
| 无进行中，无未查看结果，列表非空 | 灰色圈，仍可点开列表 |
| 列表为空 | 隐藏 |

- 右上角红色数字角标 = 未查看结果数。
- 文字说明（例如“2 个任务进行中，平均 45%；1 项结果待查看”）放在 `aria-label` 和 `title`；鼠标悬停或键盘聚焦时向左展开显示这一行文字，离开收回。没有其他自动展开。
- 实施时检查底部固定操作栏的页面（例如复核页底部按钮、`ReviewFeedbackToast`），圆环和卡片不能遮住可点击控件；有冲突时把圆环上移，不改那些页面。

### 3.2 浮层列表

沿用现有 reka-ui Popover，从圆环向上弹出，宽约 360px，最大高度 80vh 可滚动。标题“任务中心”，右侧“清除已结束”。

分组与条目：

- 进行中：等待中、进行中、已暂停、正在取消。
- 需要处理：结束后 `taskOutcome` 为“部分完成”“等待教师核对”“未完成”，或任务状态为 failed。
- 已结束：其余结束任务（全部完成、已取消、处理已结束、等待重启完成等）。
- 每条显示：任务名、状态标签（沿用 `taskOutcome` 的文字与色调）、所属考试或试卷（沿用 `taskScope` 与 `task-context` 查询）、说明（`taskDetail`）、`已用时 X 分 XX 秒 · 开始于 HH:MM`；进行中加进度条、百分比和“取消任务”；结束后加“去查看”；个人报告打包保留现有“下载个人报告”；已查看的结束任务在时间行后加灰字“已查看 · 稍后自动移出列表”；同步错误照旧显示。
- 底部一行灰字：“已查看的完成任务会在 10 分钟后自动移出列表，不影响已保存的结果。”
- 打开浮层时：刷新任务状态（沿用现有 `refresh` 逻辑），并把当前可见的、非“需要处理”的结束任务标为已查看。

### 3.3 结果卡片

- 触发：本次打开页面期间，任务被观察到从未结束变为结束（见 4.2）。
- 不弹：已取消；运行时长（`finished_at` 减 `started_at`，缺 `started_at` 用 `created_at`）不超过 5 秒；浮层正开着；完成时教师正在该任务的来源页面（见 3.5 的“来源页面”列，考试任务还要求当前所选考试就是任务所属考试）。后两种情况中，非“需要处理”的任务直接标为已查看。
- 内容：任务名、结果标签、`taskOutcome.note`（为空时用 `taskDetail`）、“去查看”、`已用时`、右上角关闭叉号。
- 叠放在圆环上方、右对齐，最多 3 张，新卡片挤掉最旧的。
- 倒计时：底部 4px 条，颜色随结果色调（成功绿、警告琥珀、错误红、中性灰蓝），底轨用浅底色。必须用 CSS `@keyframes`（宽度 100% → 0，6 秒，linear，forwards），悬停用 `animation-play-state: paused`，以 `animationend` 触发收起。不要用对新插入元素做 `width` 过渡，原型里这种写法在部分浏览器里会直接跳到 0。
- 查看状态：自动收起不算查看（圆环保留红点）；点“去查看”算查看；点关闭叉号对非“需要处理”的任务算查看，对“需要处理”的任务不算。
- 卡片区域加 `aria-live="polite"`。

### 3.4 开始提示

- 屏幕底部居中的深色小提示，约 3 秒消失，`aria-live="polite"`。文字：“已在后台开始：{taskName}，可以去做别的事”。
- 触发：`track()` 新登记一个未结束任务，且 `created_at` 距现在不超过 15 秒（避免页面加载时找回的旧任务误报），且调用方没有关闭提示（见 4.1）。
- 在对话框里显示进度的流程（3.6）不在提交时提示，改为对话框关闭时任务仍在运行才提示。

### 3.5 “去查看”跳转

在 `task-center-format.ts` 增加 `taskTarget(job)`，返回目标路由和来源页面路由名。考试任务先切换到任务所属考试（`payload.session_id`，缺失时用 `result.session_id`）。

| 任务类型 | 去查看 | 来源页面 |
|---|---|---|
| `config_generation`、`config_generate`、`question_bank_sync` | 切换考试 → `/sessions` | `sessions` |
| `scan_analysis`、`grading_run`、`grading` | 结果为“等待教师核对”→ `/grading`；其余 → `/sessions/{id}/grading-run` | `grading-run` |
| `class_analysis_generate`、`class_analysis`、`individual_report`、`report_export` | `/results?session={id}` | `results` |
| `personal_report_bundle` | `/results?tab=exports` | `results` |
| `question_import`、`tagging_sync`、`question_bank_repair`、`criterion_backfill`、`knowledge_link`、`answer_draft`、`taxonomy_suggestion`、`chapter_type_organize` | `/question-bank`；`task-context` 只关联一份试卷时加 `?paper={id}` | `question-bank` |
| `assembly_export`、`ai_assembly_spec` | `/question-assembly` | `question-assembly` |
| `wrong_question_export`、`personalized_handout_export` | `/training` | `training` |
| `ops_*` | `/settings?section=data` | `settings` |
| 其他或缺少归属 | 不显示“去查看” | 无 |

- 切换考试走 `sessionStore.selectSession`。`AppShell.vue` 在考试配置有未保存修改时会撤回切换；撤回后不跳转，在浮层或卡片上提示“当前考试有未保存的修改，请先保存后再查看”。`/results` 已支持 `?session=`，不需要先切换。
- 实施时逐个核对上表路由在当前代码中存在，若某页面已改名，按实际路由修正并在提交说明中写明。

### 3.6 原有进度弹框

凡是在 `AppDialog` 内显示任务进度的组件，运行中页脚只留一个主按钮“转到后台”；标题栏关闭叉号保留，效果相同。两者都只关闭弹框，不取消任务；关闭时任务仍在运行则调用 `jobs.announce(id)` 显示开始提示。提交时用 `track(job, { announce: false })`。

已知需要改的：`components/question-bank/QuestionRepairDialog.vue`；`components/question-bank/SkillCandidateRunDialog.vue`（入口在 `QuestionBankTodo.vue`）。当前 `QuestionImportJobs.vue` 位于 `QuestionBankView.vue` 已有的“上传试卷与任务”弹框内，也按相同规则处理；关闭后仍在提交的后续任务照常提示。其他在弹框内显示 `job.progress` 的组件使用同一规则。在页面内显示进度的流程（考试配置、扫描批改等）不加弹框，只靠开始提示。

“已加入任务中心，完成后在任务中心下载。”（`PersonalReportReader.vue`、`FileCenterView.vue`）改为“已加入右下角任务中心，完成后在那里下载。”

## 4. 实现要点

### 4.1 `stores/jobs.ts`

- `PersistedJobReference` 增加可选字段 `readAt?: string`、`dismissedAt?: string`（ISO 时间）。`isPersistedReference` 与 `readReferences` 同步接受这两个字段；旧数据没有这两个字段时按未查看、未移出处理。
- `track(job, options?: { announce?: boolean })`：删除现在无人传入的第二个参数 `next`（依赖注入只保留在 `initialize(next)`/`configure`；先搜索确认没有调用方或测试传入）。新登记时：任务已结束则直接写 `readAt`；未结束且满足 3.4 条件则加入开始提示队列。
- 新增 `announce(id)`、`markRead(id)`、`dismiss(ids: number[])`。`clearCompleted()` 改为对所有已结束任务调用 `dismiss`，不再 `remove`。
- 观察到“未结束 → 结束”：在 `refresh`、`refreshMany`、`cancel` 和对已登记任务的 `track` 中，替换快照前比较旧快照与新快照，变为结束时把 `{ id, live }` 推入完成队列。`initialize()` 期间找回的变化 `live = false`：不弹卡片，只保持未查看。
- 用两个响应式队列交给界面消费：`announcements: number[]`、`completions: { id: number; live: boolean }[]`。删除无使用者的 `latestTrackedJobId`、`jobNoticeRevision`。
- `initialize()`：`dismissedAt` 早于 24 小时的引用直接删除，不再请求；其余照旧刷新。之后请求一次 `GET /api/jobs?status=queued&status=running&page_size=50`，对本地没有引用的任务用批量接口取完整记录后 `track(job, { announce: false })`，使清过浏览器记录后仍能看到正在运行的任务。
- 被移出（`dismissedAt`）的任务仍保留在 `jobs.jobs` 中，只是不在列表显示。`config-workspace.ts`、`QuestionRepairDialog.vue`、`chapterTypeJobSummary` 等直接读 `jobs.jobs` 的页面因此不受影响；不要改成删除。

### 4.2 界面组件

- 用一个新组件替换 `TaskCenterPopover.vue`（例如 `components/shell/TaskCenterDock.vue`，内含圆环、浮层、结果卡片和开始提示），挂到 `AppShell.vue`；从 `AppSidebar.vue` 移除入口；删除 `AppShell.vue` 中 `--shell-sidebar-offset` 相关代码。保留并复用现有下载、取消、`task-context` 读取逻辑。
- 在 `task-center-format.ts` 增加 `needsAttention(job)` 与 `taskTarget(job)`。
- 自动清除在组件内执行：每 30 秒和每次打开浮层时，对“已结束、非需要处理、`readAt` 早于 10 分钟”的任务调用 `dismiss`；“已结束”组按 `finished_at` 倒序只保留 5 条，其余立即 `dismiss`；需要处理的任务不计入上限。
- 可见任务 = 未 `dismissedAt` 的已登记任务；未查看 = 可见、已结束、无 `readAt`。
- 样式使用 `tokens.css` 现有令牌；新样式放在组件内或 `app-shell.css`，不新建全局样式文件。

## 5. 完成标准

1. 任意页面发起一个后台任务，底部出现开始提示；离开到其他页面，右下角圆环持续显示进度。
2. 任务结束时（不在来源页面、浮层未开），圆环上方弹出结果卡片，彩色倒计时条可见并匀速缩短，悬停暂停，6 秒后收起，圆环出现未查看角标。
3. “去查看”跳到 3.5 表中的页面，考试任务切到所属考试；有未保存修改时不跳转并提示。
4. 已查看的完成任务 10 分钟后从列表消失，刷新页面后保持消失；需要处理的任务一直保留到点“去查看”。“已结束”组最多 5 条。
5. 页面刷新后，正在运行的任务仍在圆环和列表中；刷新期间已结束的任务显示为未查看，但不补弹卡片。
6. 进度弹框运行中只有“转到后台”，关闭弹框不取消任务。
7. 侧栏不再有任务中心入口；工作台“正在运行”区与各页面原有进度显示不变。

## 6. 验证、测试与文档

- 验证：`frontend` 下运行 `npm run lint`、`npm run typecheck`、`npm run test`、`npm run build`。浏览器手测只用免费本地任务（数据备份、报告导出、组卷导出、错题本导出），不要触发任何调用模型的任务；启动或重启 8035 前确认没有正在进行的真实批改或模型任务。长任务的卡片与倒计时可以先用参考原型对照外观。
- 测试：按 `AGENTS.md` 收尾规则，本次属于界面与本机显示状态改动，默认不新增测试；现有测试文件数 22、测试函数 110、后端展开用例 113（2026-10-12 `tools/run_test_suite.py --inventory`）。若改动导致现有测试失败，按实际行为修正断言。
- 文档：同一次提交中改写
  - `docs/product/GRADING.md` 中“任务中心”两段：补入口位置、结果卡片、自动移出与开始提示规则；
  - `docs/ui/STYLE.md` 中“任务中心入口位于侧栏底部，任务提示浮卡贴在侧栏右侧底部”一句；
  - `ARCHITECTURE.md` 任务中心一句：补充启动时通过 `GET /api/jobs` 找回运行中任务；
  - `docs/requests/README.md` 本条目状态改为已实现，并注明原型保留作取舍依据。
