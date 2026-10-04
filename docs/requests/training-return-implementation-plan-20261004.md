# 训练卷回收批改页改版：实施方案

- 用途：交给实施代理，把“知识与训练 · 生成试卷”里打开草稿后的页头，以及第三步“回收批改”改成用户已确认的样子。
- 依据：用户已确认原型 `docs/requests/training-return-prototype-20261004/`（直接打开 `index.html`，合成数据，左下角“原型控制”可切换 5 种情况）。原型与本文冲突时以本文为准（差异见第 2 节）。
- 状态：已实施。真实数据只读验收覆盖草稿页头、三个步骤、回收入口和无扫描批次面板；完整扫描判定流程在合成环境验收，限制见第 9、11 节。
- 本方案只改前端，不改后端接口、数据库、判定规则和证据规则。实施与验证都不调用真实模型、不产生费用（见第 9 节）。

实施前必读：`AGENTS.md`（授权、并行协调、单次写入约 15KB 以内、收尾三行）、`docs/ui/STYLE.md`、`docs/product/KNOWLEDGE_AND_TRAINING.md` 第 259 行及“扫描归卷”“整卷四态判定”“教师锁与证据回流”三节、`docs/testing/README.md`。

## 0. 前置条件与并行协调

- 写作本文时，`docs/product/KNOWLEDGE_AND_TRAINING.md`、`docs/testing/README.md`、`frontend/src/components/knowledge-training/TrainingGroupRecommendations.vue` 含另一项任务（章节分组规则）的未提交改动。每个阶段开始前运行 `git status --short -- <本阶段要改的文件>`；涉及文件仍有他人未提交改动时，前端代码可以继续，文档改动等对方提交后再做，不覆盖、不暂存他人修改。
- 本方案不改 `TrainingGroupRecommendations.vue`。
- 共享文件：`frontend/src/components/workbench/workbench-home.ts`（工作台，见 5.13）、`docs/ui/STYLE.md`。改前同样检查未提交改动。
- 重建共享的 `frontend/dist` 前确认没有其他任务在使用。

## 1. 已确认的产品决定

1. **草稿页头**：打开一份训练卷或讲义草稿后，页头左侧为“← 返回 上一页”、草稿标题与信息，右上角只放步骤条；不显示知识与训练五个页签。三个步骤（审核题目、打印试卷、回收批改）通用；讲义只有前两步。没有打开草稿时，“生成试卷”保持现有页签页头。
2. **回收批改布局**：一行汇总（批次、各状态计数、完成细条、唯一主按钮）＋三栏：学生队列｜原始答卷（最大）｜判定点台账。异常页放在队列最前，与学生共用三栏。
3. **批量判定**：可一次确认“判定 N 份（N 次模型请求）”。确认框列出学生、份数、请求次数和费用；逐份发送，每份仍只发 1 次请求，失败不自动重试。单份“判定这一份”保留。
4. **一键锁定**：判定点上点“达成 / 未达成 / 无法辨认”即锁定为教师最终判定；核对依据与原因自动生成，可加备注。“标为不确定”放在“⋯”菜单里。
5. **批量更新掌握度**：“确认并更新 N 份掌握度”只包含全部题目都已判定完成的答卷；还有待复核点的答卷只能逐份确认。不调用模型，不产生费用。
6. **工作台待办**：从工作台训练待办进入时，直接停在回收批改，并选中对应的第一份待处理答卷或异常页。

## 2. 与原型的差异（以本文为准）

- 原型的数字、姓名、题目和掌握度都是合成数据。正式版的所有数字来自第 5.1 节列出的真实接口字段。
- 原型标题“个人训练卷 · 10月3日 创建”：草稿接口没有创建时间，正式版页头不显示日期（见 4.2）。
- 原型“判定中”是静态演示；正式版对运行中的答卷每 3 秒查询一次结果（沿用现有规则，见 5.1）。
- 原型批量判定时主按钮外观未变灰；正式版运行期间该按钮禁用并改为“正在判定 k/N 份”，旁边提供“停止发送剩余”。
- 原型 900px 宽度下汇总行和队列较乱；正式版按 5.4 的窄窗口规则实现。
- 原型的答卷是示意图。正式版显示真实扫描页图片（`preview_url`），不在页面上另画题框或高亮。
- 原型“标注数据来源”只是演示开关，正式版不实现。
- 原型撤回证据后显示“待更新”；正式版按真实反馈状态推导（撤回后同样回到“待更新”）。

## 3. 复用与新建清单

| 需要 | 复用 | 新建／删除及原因 |
|---|---|---|
| 页头 | `PageHeader`（`#back`、`#meta`、`#actions` 插槽）、`BackButton`、`StepProgress`（横排） | 无新组件。步骤条从草稿组件移到页头，见 4.3 |
| 回收批改容器 | 保留 `components/training/TrainingScanBatchPanel.vue` 文件名和对外接口（`instances` 属性、`openDraft`、`progressChange` 事件），内部重写 | 草稿组件不用改引用 |
| 状态推导与计数 | 无现成实现。考试复核的 `review/review-status.ts` 是考试分数状态，含义不同，不能复用 | 新建 `features/training/training-return.ts`：纯函数（5.1、5.3、5.5、5.8） |
| 数据读取与轮询 | `trainingApi` 现有方法，以及 `TrainingAssessmentPanel.vue` 里的轮询、版本号防旧结果、模糊写入后查询逻辑 | 新建 `features/training/use-training-return.ts`（组合式函数）。原逻辑整段迁入，不重复实现 |
| 汇总行 | `StatusBadge`、`AppButton` | 新建 `TrainingReturnSummary.vue` |
| 无批次面板 | 现有 `selectLatestInstances`、建批次与上传逻辑 | 新建 `TrainingReturnStart.vue`，逻辑从旧面板迁入 |
| 学生队列 | `StatusBadge` | 新建 `TrainingReturnQueue.vue` |
| 原始答卷 | 旧 `TrainingAssessmentPanel.vue` 的 `answer-preview` 区块 | 新建 `TrainingAnswerSheet.vue`，原区块迁入后扩展 |
| 判定点台账 | 旧面板的逐题列表、请求记录、重试与接管 | 新建 `TrainingPointLedger.vue`，交互改为一键锁定 |
| 掌握度反馈 | 旧面板的 `skillChanges`、`aggregateChanges`、`masteryTitle`、`masteryValue`、补发、撤回、下一轮入口 | 新建 `TrainingFeedbackSheet.vue`，原逻辑迁入 |
| 异常页处理 | 旧面板的 `resolve`、`issueLabel`、`candidateLabel`、`cancelSubmission` | 新建 `TrainingScanIssueForm.vue` |
| 费用确认框 | 项目里没有通用确认框。`file-center/AnalysisConfirmDialog.vue` 绑定报告预检结构，不能复用 | 新建 `TrainingCostConfirmDialog.vue`：`role="dialog"`、`fx-dialog` 动效、Esc 关闭、关闭后焦点回到触发按钮 |
| 删除 | — | `TrainingAssessmentPanel.vue`（逻辑全部迁出后删除）。删除前全局检索引用，并迁移其测试（第 7 节） |

只有一种真实实现，不建接口层、注册表或策略层。每个新文件单次写入约 15KB 以内，组件超过时继续拆成子组件。

## 4. 草稿页头（`TrainingRecommendationsView.vue`、`PersonalizedRecommendationDraft.vue`）

### 4.1 何时切换

- `draftOpen = trainingMode === 'paper' && draftContext !== null`。`draftContext` 已由草稿组件的 `contextChange` 事件提供，草稿读出后非空，放弃或未生成时为空。
- `draftOpen` 为假：页头保持现状（标题、学期与人数元信息、`KnowledgeTrainingTabs` 页签）。
- `draftOpen` 为真：不渲染 `#navigation`（五个页签），改用 4.2、4.3。

### 4.2 左侧：返回、标题、元信息

- `#back`：`<BackButton :label :to>`。去向与名称按顺序取第一条成立的：
  1. 路由带 `from=workbench`（见 5.13）→ `/workbench`，“工作台”；
  2. 现有 `paperBackTarget` 指向 `mode=chapter` → “按章节训练”；
  3. 其余 → `paperBackTarget`，“按学生训练”。
- 标题：用途为讲义时写“刷题讲义”，否则写“训练卷”。
- `#meta`：`{一人一卷|多人同一套卷} · {n} 名学生 · 每卷 {q} 题 · 难度 ≤ {d} 级 · 自动保存 V{revision}`。前四项取 `draftContext`（与现有 `paper-review-bar` 同一来源）。`revision` 取 4.3 事件里的草稿修订号。
- `draftOpen` 时不渲染 `paper-review-bar`：其中的信息已移到页头，“调整出卷设置”由返回按钮代替；“生成草稿”按钮只在未生成草稿阶段出现，不受影响。
- 下一轮个人草稿打开后（`openNextDraft`），页头按新草稿的人数和设置显示，沿用现有规则。

### 4.3 右上：步骤条

- 草稿组件删除 `draft-workflow-heading` 这一行（其中的步骤条和“自动保存 · V”）。
- 草稿组件新增事件 `workspaceChange: [value: { steps: StepProgressStep[]; current: string; revision: number } | null]`，在 `workspaceSteps`、`viewStep` 或 `draft.revision` 变化时发出；没有草稿时发 `null`。
- 草稿组件的 `defineExpose` 由 `{ generate }` 扩为 `{ generate, selectWorkspaceStep }`。
- 页面在 `#actions` 渲染 `<StepProgress :steps :current @select="paperDraft?.selectWorkspaceStep($event)" aria-label="草稿步骤">`，横排，与考试批改页（`ScanGradingView.vue`）用法相同。
- 窄窗口：宽度 ≤1100px 时页头换行，步骤条移到第二行左对齐（参照 `KnowledgeTrainingTabs.vue` 中 `.knowledge-training-header` 的 1100px 规则，为 `.page-header__actions` 补同样处理）；≤760px 时连接线缩短，沿用草稿组件原有的 760px 规则（移到页面样式）。整页不得出现横向滚动。

## 5. 回收批改页

### 5.1 数据、状态推导与轮询（`use-training-return.ts`、`training-return.ts`）

**读取顺序**（沿用现有恢复规则：进入时恢复最近一次扫描批次，可切换历史批次）：

1. `listTrainingScanBatches(paper_batch_id)`（按 `instances` 中出现的全部 `paper_batch_id`）→ 取最新一批 `getTrainingScanBatch`。
2. 对每份 `status === 'ready'` 的提交读取 `getTrainingAssessment(submission_id, revision)`：返回 404 `training_assessment_not_found` 记为“未判定”；其他错误记为“状态未读出”。
3. 判定存在且 `status !== 'running'` 时，再读 `getTrainingFeedback`：404 记为“没有反馈”。
4. 第 2、3 步最多同时 4 个请求；读完一份就更新这一行，不等全部完成。
5. 每次批次切换或组件卸载，递增版本号，旧请求结果一律丢弃（沿用旧面板的 `loadVersion`、`viewVersion` 做法）。

**轮询**：状态为“判定中”的每一份，每 3 秒 `getTrainingAssessment` 一次。连续 3 次失败就暂停，该行台账显示“暂时无法确认后台结果，请重新查询；不会自动追加模型请求。”和“重新查询”。查询到结束状态后停止轮询，并读取反馈。只查询，从不重新提交判定。

**状态推导** `submissionStage(submission, assessment, feedback)`，按顺序取第一条成立的：

| 阶段 | 条件 | 队列标签 | 色调 |
|---|---|---|---|
| `cancelled` | `submission.status === 'cancelled'` | 已取消 | neutral |
| `scan_issue` | `submission.status === 'manual_review'` | 有缺页时“缺 n 页”（n = `missing_pages.length`），否则“待核对” | warning |
| `unknown` | 判定或反馈读取失败 | 状态未读出 | neutral |
| `unjudged` | 判定不存在（404） | 待判定 | info |
| `running` | `assessment.status === 'running'` | 判定中 | info |
| `failed` | `assessment.status` 为 `failed` 或 `cancelled`，且所有判定点的 `state` 都为空 | 判定失败 | danger |
| `review` | 存在 `review_status !== 'completed'` 的题 | 待复核 k 点 | warning |
| `update` | 全部题已完成，且反馈不是“当前完成” | 待更新 | teacher |
| `done` | 全部题已完成，且 `feedback.status === 'complete' && feedback.source_review_revision === assessment.review_revision` | 已完成 | success |

- 待复核点数 k = 判定点中 `state` 为空，或未被教师锁定且 `state` 为 `uncertain`、`unreadable` 的个数。这与后端 `module.py` 中的 `needs_review` 口径一致。k 为 0 但仍有题未完成时（例如某题没有判定点），标签只写“待复核”。
- “待复核”“待更新”与工作台待办口径一致：工作台“复核 … 判定 n 份”= 存在未完成题；“发布 … 证据 n 份”= 全部题完成且反馈不是当前完成。差异只有一处：判定失败且没有任何点结果的答卷，工作台计入“复核”，本页单列为“判定失败”。这处差异不改后端，在 5.13 的文案中说明。
- 运行失败但已有部分题结果（后端 `workflow_status === 'partial_review'`）属于 `review`；台账同时显示 `action_message` 和“教师确认后重试一次（1 次请求）”（见 5.9）。
- `api/training.ts` 的 `TrainingAssessmentPoint` 类型补可选字段 `candidate_state?: TrainingPointState | null`。解码器已经校验并保留该字段，不改解码逻辑。

**同一份答卷内保持的本地状态**（切换学生不丢失，切换批次清空）：备注草稿（以 `submission_id:revision:task_item_code:point_id` 为键）、“只看待复核”开关、当前页码、查询暂停状态。

### 5.2 无批次：回收答卷（`TrainingReturnStart.vue`）

- 出现条件：历史批次已读完且当前没有选中的批次。批次读取中显示内容骨架；读取失败显示一句话和“重新读取批次”（沿用现有文案）。
- 没有冻结卷时显示“先生成 PDF 训练卷，再回收答卷。”（沿用）。
- 一行“本次回收 {n} 份训练卷（每人最新版本）”加文字按钮“调整 ▾”。默认选中规则沿用 `selectLatestInstances`（每名学生取最大 `series_version` 的冻结卷）；展开后为紧凑复选网格，每格“姓名 · V版本 · 页数”。
- 虚线选择区：点击或拖入文件；`accept` 与现有一致（PDF / JPG / PNG，可多选）。选中后显示文件名列表与“已选 n 个文件”。
- 主按钮“导入并归组（n 个文件）”，未选文件或未选试卷时禁用。点击后依次：`createTrainingScanBatch(selectedIds, token)` → 对每个文件 `uploadTrainingScan`。建批次成功后立即切到有批次视图；上传中途失败时保留已建批次和已归组结果，汇总行下方显示现有错误文案，教师可用“补传答卷”继续。
- 删除旧面板单独的“建立扫描批次”按钮和折叠的上传区。

### 5.3 汇总行（`TrainingReturnSummary.vue`）

单行、白底细边框，左到右：

1. **批次选择** `<select class="app-input">`：选项为历史批次（沿用 `batchLabel`：时间 · n 人 · 归组完成/需要检查/已取消），最后一项“新建批次…”（切到 5.2）。旁边弱化按钮“刷新”（重新读取当前批次及各份状态）。
2. **计数**：可点击的文字按钮，只显示非零项，顺序为异常页 n 页 · 缺页/待核对 n 份 · 待判定 · 判定中 · 判定失败 · 待复核 · 待更新 · 已完成（份）。“异常页”= 当前批次中 `issue_code` 非空、`state` 不是 `replaced`/`dismissed`、且所属提交未取消的页（与工作台 `scan_page_count` 同口径）。点一个计数会把队列筛选为该状态，再点一次取消。
3. **完成细条**：行内细条＋“已完成 a/b 份”，b = 未取消的提交数。
4. **右侧动作组**（不换行，固定在第一行右端）：
   - 弱化按钮“补传答卷”：在汇总行下方展开与 5.2 相同的选择区，上传到当前批次。
   - 唯一主按钮，按顺序取第一条成立的：异常页 > 0 →“处理异常页（n）”（选中第一张异常页）；待判定 > 0 →“判定 n 份（n 次模型请求）”（5.9）；待更新 > 0 →“确认并更新 n 份掌握度”（5.10）。都不成立时不显示主按钮。
   - 主按钮为“判定”且待更新 > 0 时，“确认并更新 n 份掌握度”作为次要按钮放在主按钮左侧。
   - 批量判定进行中：判定按钮禁用，文字改为“正在判定 k/N 份”，左侧出现弱化按钮“停止发送剩余”。
- 宽度不够时计数区内部换行，动作组保持在第一行右端。

### 5.4 布局

- 宽度 ≥1280px：三栏 `240px | minmax(0,1fr) | 400px`，三栏各自滚动，高度占满汇总行以下的可用区域。
- 1024–1279px：左栏 220px；右侧区域上下排列，答卷在上（最大高度 55vh），台账在下。
- <1024px：单栏。队列变为一行横向滚动的紧凑条（每项姓名＋状态标签），其下依次为答卷、台账。汇总行计数区换行，动作组单独一行右对齐。
- 任何宽度都不得出现整页横向滚动。

### 5.5 学生队列（`TrainingReturnQueue.vue`）

- 头部一行：“学生 {n} 人”（小标题字号，人数灰色）＋右侧筛选 `<select>`：全部 / 需要处理 / 已完成。点汇总计数时该下拉框显示“全部”，计数筛选优先。
- 第一组“异常页”：每行“上传文件第 {upload_page_number} 页”＋“异常页”标签，第二行为 `issueLabel(issue_code)`。
- 然后是学生行，排序：`scan_issue`、`failed`、`review`、`unjudged`、`update`、`unknown`、`running`、`done`、`cancelled`；同一阶段按学号，再按姓名。每行：姓名（粗体）、状态标签（文字＋色调）；第二行“{班级} · {学号}”，已有判定时再加“ · 达成 {met}/{total} 点”（met = 各题 `met_count` 之和，total = `expected_point_count`）。判定中的行带细进度动画（减少动态效果设置下关闭）。
- 选中行左侧内阴影标出，`aria-pressed`；每行是按钮，Enter/空格可选。
- 底部“下一份待处理 ›”：在异常页和阶段为 `scan_issue`、`failed`、`review`、`unjudged`、`update` 的行之间循环。没有这类行时隐藏。
- 初始选中：有 5.13 的定位参数时按参数；否则选第一行“需要处理”的行，没有就选第一行。选中的行消失时（例如异常页已处理），改选下一份待处理的行。

### 5.6 原始答卷（`TrainingAnswerSheet.vue`）

- 头部：“{姓名} · V{series_version} · {expected_total_pages} 页”＋页签“第 1 页 …”＋“放大查看”（新标签页打开 `preview_url`，沿用）。
- 页面来源：`batch.pages` 中 `submission_id` 等于该份、`state === 'assigned'` 的页，按 `page_number` 排序；缺少的页码显示占位块“缺第 n 页”。
- 图片按栏宽等比显示，不裁切；图片无法加载时显示一句“扫描页暂时无法显示”加“放大查看”。
- 选中异常页时：头部为“上传文件第 n 页 · 异常页”，图片上方（不覆盖图片）一条警示横条写 `issueLabel(issue_code)`。

### 5.7 判定点台账（`TrainingPointLedger.vue`、`TrainingFeedbackSheet.vue`、`TrainingScanIssueForm.vue`）

台账为三段：头部（固定）｜内容（滚动）｜底栏（固定、不透明、上边框）。内容不得渲染到底栏下方。

| 选中对象 | 头部 | 内容 | 底栏 |
|---|---|---|---|
| 异常页 | “异常页 · 上传文件第 n 页” | 问题文字；“匹配到 [学生训练卷 ▾]”“页码 [数字]”；主按钮“人工匹配”、次要“明确替换该页”、弱化“忽略此页”。逻辑、校验与文案沿用旧面板 `resolve` | 无 |
| `scan_issue` | “缺少第 {页码} 页”或“需要核对” | 缺页与问题列表；“补传扫描件，或在异常页中匹配到这一份”；弱化按钮“取消这份提交”（沿用 `cancelSubmission`） | 无 |
| `unjudged` | “整卷 {题数} 题 · {判定点数} 个判定点”（取该份冻结卷 `question_count`、`criterion_point_count`） | 无 | 主按钮“判定这一份（1 次模型请求）”（5.9） |
| `running` | “判定中” | 三行骨架＋“后台正在判定，页面会自动查询结果”；查询暂停时显示重新查询；弱化按钮“接管中断状态”（沿用 `recover`） | 无 |
| `failed` | “判定失败” | `action_message` | 次要按钮“教师确认后重试一次（1 次请求）”（5.9） |
| `review` / `update` | “达成 {met}/{total} 点 · 待复核 k 点”（k 为 0 时“已全部确定”）；右侧“只看待复核”复选框；折叠“请求记录 · {题数} 题 · 已请求 {request_count} 次 · {tokens} tokens” | 逐题列表（下述） | 见下 |
| `done` | “{姓名} · 达成 {met}/{total} 点 · 已完成” | 页签“掌握度变化 \| 逐点结果”，默认前者 | 无 |
| `unknown` / `cancelled` | 状态文字 | “重新读取”（unknown）或“这一份没有计入本次回收” | 无 |

**逐题列表**：

- 题头“第 {item_order} 题”＋每个判定点一个小圆点（达成、未达成、无法辨认、不确定或待复核各一种颜色，`title` 写状态文字）＋右侧“{met_count}/{total_count} 点”。
- 判定点行：左边为判定点文字（`content`，两行截断，点击展开），下方小字“AI：{evidence}”（无依据时省略）；已锁定且有原因时再加一行“教师：{teacher_reason}”。右边为三段按钮“达成 | 未达成 | 无法辨认”（当前 `state` 对应的一段为按下状态）、来源小标签（`teacher_locked` 为真写“教师已锁定”，否则写“AI”）、“⋯”菜单（“标为不确定”和“加备注”单行输入）。
- 待复核点（k 的计数口径）加警示边框和标签：`candidate_state === 'unreadable'` 写“AI 无法辨认”，候选为空写“AI 未判定”，其余写“AI 拿不准”；三段按钮都不按下。
- “只看待复核”勾选后只显示含待复核点的题和这些点。
- 选中一份 `review`/`update` 答卷时，内容区自动滚动到第一个待复核点并聚焦（`focus({ preventScroll: true })`，只滚动台账内容区，不滚动整页）。

**底栏**（`review`/`update`）：左侧“本份待复核 k 点”或“本份已全部确定”；主按钮在有待复核点时为“确认已完成的 {已完成题数} 题并更新”，否则为“确认并更新掌握度”。可用条件沿用现有 `canPublish`：不在运行中且至少一题完成。右侧弱化按钮“下一份 ›”（同“下一份待处理”）。下面一行小字快捷键提示：“J/K 切换学生 · Tab 下一个待复核点 · 1/2/3 达成/未达成/无法辨认”。

**反馈已过期**：存在反馈、`status !== 'withdrawn'` 且 `source_review_revision !== review_revision` 时，内容区顶部显示“判定已修改，需重新确认并更新掌握度”。

**掌握度变化**（`done`，以及 `update` 中仍有旧反馈时可在“逐点结果”旁查看）：

- 表格“训练目标 | 训练前 | 训练后”，行取现有 `skillChanges`，数值用现有 `masteryValue`（有档位时由 `masteryDetail` 显示档位与百分比）。
- 折叠“章节与小节汇总（n 项）”，取 `aggregateChanges`。
- 一行“下一轮补练：{next_round.message}”；有 `draft_id` 时显示次要按钮“打开下一轮草稿”（发出 `openDraft`）。
- `feedback.status === 'publication_pending'` 时显示“安全补发未完成证据”（沿用 `replayEvidence`）。
- 折叠“更正”，内含危险按钮“撤回本次证据”（沿用 `syncEvidence('withdraw')`）。
- 显示“已保存 {published_question_count}/{total_question_count} 题证据”，与达成点数分开显示（沿用现有规则）。

### 5.8 一键锁定

`lockPoint(assessment, question, point, finalState, note)` 调用现有 `reviewTrainingPoint`，请求体：

- `final_state`：按钮对应 `met`、`not_met`、`unreadable`；菜单“标为不确定”对应 `uncertain`。
- `teacher_reason`：`"{候选} → 教师{最终}"`。候选为空写“AI 未判定”，否则写“AI ”加状态文字（例：“AI 不确定 → 教师达成”）；有备注时追加“；{备注}”。截断到 500 字。
- `teacher_evidence`：有备注时用备注；没有备注且最终状态等于候选状态并且 AI 有依据时，用 AI 依据；其他情况用“教师查看原始训练答卷后判定”。截断到 500 字。
- `operation_token` 每次新生成（沿用 `requestToken`）。

行为：

- 点击当前已按下的那一段不发请求。
- 同一份答卷同一时刻只允许一个锁定请求：请求进行中，这份答卷的所有判定按钮与快捷键暂时禁用，该点显示“正在保存…”。
- 成功后用返回的判定结果整体替换本地数据，清除该点备注草稿，提示“已锁定：{候选} → 教师 {最终}”，焦点移到下一个待复核点（没有就停在原处）。旧反馈自然变为过期，阶段按 5.1 重新推导。
- 失败时，`training_assessment_review_conflict` / `training_assessment_revision_conflict` 先重新读取判定，再提示“判定内容已在另一处变化，已刷新，请重新确认这一点”；其他错误沿用现有 `safeError` 文案。不自动重试。
- `done` 答卷在“逐点结果”页签中也能这样修改；修改后阶段回到 `update`。

### 5.9 判定：单份、重试与批量

后端事实（已核对 `backend/training_assessment/module.py` 的 `assess`）：同一份答卷的同一修订只登记一个运行，运行编号由答卷编号与修订号固定算出；重复提交返回已有结果，不会再发请求。提交是同步请求，最长等待 180 秒。

**单份**：“判定这一份（1 次模型请求）”→ `TrainingCostConfirmDialog`，正文：“将发送本份训练答卷、题目和判定点，调用模型 1 次并产生费用；实际费用以模型服务商计费为准；失败后不会自动重试。”按钮“取消”“确认判定”。取消不发请求。确认后调用 `startTrainingAssessment`，后续处理沿用旧面板 `startAssessment`：模糊写入错误（`isAmbiguousWriteError`）转为轮询查询，不重新提交。

**重试**：判定失败时“教师确认后重试一次（1 次请求）”→ 同一确认框，正文把“调用模型 1 次”改为“追加 1 次模型请求”，其余相同；确认后调用 `controlTrainingAssessment(action: 'retry')`，原因文案沿用。每次重试都要重新确认。

**批量**：汇总行“判定 N 份（N 次模型请求）”：

1. 在点击时确定名单：队列顺序中阶段为 `unjudged` 的答卷，记下 `submission_id` 与 `revision`。
2. 确认框标题“判定 N 份”；列出姓名，超过 10 人时写前 10 人加“等 N 人”；正文：“将逐份发送这 N 份答卷、题目和判定点，共 N 次模型请求，产生费用；实际费用以模型服务商计费为准。失败的不会自动重试。”按钮“取消”“确认判定 N 份”。取消不发任何请求。
3. 确认后逐份进行，同一时刻最多一份在等待提交返回：
   - 发送前重新检查这份的阶段，仍为 `unjudged` 且修订号未变才发送，否则跳过；
   - 成功：写入结果，阶段按 5.1 更新；
   - 模糊写入错误：该份记为“判定中”并按 5.1 轮询，不再提交；继续下一份；
   - 其他错误（修订冲突、输入无效、服务不可用）：该份记录错误文字并保持原阶段，继续下一份，不重试。
4. “停止发送剩余”：当前这一份照常收尾，之后的不再发送。
5. 组件卸载或切换批次时停止发送剩余；已发出的请求由后台照常完成，重新进入时按 5.1 恢复。
6. 结束后在汇总行下方显示一行：“已判定 a 份；b 份未完成，不会自动重试；c 份已跳过”（只列非零项）。

### 5.10 更新掌握度：单份与批量

- **单份**（台账底栏）：调用 `syncTrainingEvidence(assessment, 'publish', token)`，行为沿用旧面板 `syncEvidence`：只保存已完成题目的证据，成功后台账切到“掌握度变化”。
- **批量**（汇总行“确认并更新 N 份掌握度”）：
  - 名单 = 阶段为 `update` 的答卷；阶段为 `review` 的不包含。不弹确认框（不调用模型，可用“撤回本次证据”更正）。
  - 逐份调用 `syncTrainingEvidence`，同一时刻一份。每份在开始前生成一次 `operation_token`，整个过程中不变。
  - 模糊写入错误：用 `getTrainingFeedback` 核对结果，不重新提交。
  - 其他错误：记录错误，继续下一份。
  - 结束后显示一行：“已更新 a 份掌握度；另有 r 份待复核，需逐份确认；f 份未完成，请重试”（只列非零项）。
- 发布后生成下一轮草稿等后续行为与现在单份发布完全相同，批量只是依次执行单份发布。

### 5.11 异常页与缺页

- 异常页的匹配、替换、忽略逻辑与修订保护沿用旧面板 `resolve`，“请先选择准确的学生训练卷和页码。”等文案不变。
- 处理成功后，用返回的批次整体替换本地批次：该异常页从队列消失，相关答卷阶段重新推导（例如缺页补齐后变为“待判定”，并读取其判定状态），选中项移到下一份待处理。
- “匹配到”下拉默认选中第一份 `scan_issue` 中缺页的答卷，页码默认填它的第一个缺页页码；没有缺页答卷时不预选。

### 5.12 快捷键与焦点

- 只在焦点不在输入框、下拉框、多行输入内时生效：
  - `J`/`K`：队列下一行/上一行；
  - `Tab`/`Shift+Tab`：在台账的待复核点之间循环（没有待复核点时恢复浏览器默认行为）；
  - `1`/`2`/`3`：对当前聚焦的判定点锁定为达成/未达成/无法辨认。
- 确认框打开时只有确认框内的按键生效；Esc 关闭并把焦点还给触发按钮。
- 键盘顺序与视觉顺序一致；所有按钮都有可访问名称；状态同时用文字表达。

### 5.13 工作台待办定位（`workbench-home.ts`）

- 三类训练待办的去向在现有 `/training?mode=paper&draft={id}` 上追加 `&from=workbench&focus={scan|review|publish}`（依次对应扫描页、复核、发布三行）。
- 复核行的事实文字改为“不确定、无法辨认、缺失判定点或判定失败，仍需教师处理”，与 5.1 的口径差异一致。
- 页面读取 `focus`：草稿有冻结卷时直接进入“回收批改”（现有行为），队列初始选中：`scan` → 第一张异常页，没有则第一份 `scan_issue`；`review` → 第一份 `failed` 或 `review`；`publish` → 第一份 `update`。找不到时按 5.5 的默认规则。读取后用 `router.replace` 去掉 `focus`，保留 `draft` 与 `from`。
- `TrainingRecommendationsView` 把 `focus` 传给草稿组件，草稿组件再传给回收批改容器。容器新增可选属性 `initialFocus?: 'scan' | 'review' | 'publish'`，对外接口其余不变。

## 6. 保持不变

- 后端接口、数据库结构、请求与响应字段（只在前端类型上补已存在的 `candidate_state`）。
- 每份答卷每个修订只登记一个运行、每次运行最多一次模型请求、不拆卷、不自动重试、不自动追加请求；查询失败只能人工重新查询。
- 教师锁优先于模型结果；锁使用答卷、修订、题号和判定点身份唯一定位，并受复核修订号保护。
- 只发布已完成题目的证据，不补零；训练结果不带分值，不影响考试成绩、排名和报告。
- 扫描异常的修订保护、取消提交、历史批次切换、证据补发与撤回。
- 讲义草稿只有审核与导出两步，不出现回收批改。
- 训练草稿生成、换题、出卷与冻结流程不变。

## 7. 测试

按 `AGENTS.md` 的顺序：先在已有测试里加断言，再在模块现有文件里加函数，现有文件都不合适时才新建。所有接口在测试中模拟，不连接真实服务，不调用模型。

前端单元（vitest，`frontend/src/__tests__/`）：

- **新建 `training-return.spec.ts`**（`training-return.ts` 是新模块，没有对应的测试文件），覆盖纯函数：
  - 5.1 状态表每一行，包括：失败但有部分点结果判为 `review`；k 为 0 但仍有未完成题；已撤回反馈判为 `update`；反馈修订号不等判为 `update`；
  - 待复核点数口径（教师锁定的“不确定”不计入）；
  - 汇总计数：异常页排除 `replaced`、`dismissed` 和已取消提交的页；
  - 队列排序与“下一份待处理”循环；
  - 主按钮优先级，以及主次按钮同时出现的情况；
  - 5.8 锁定请求体：理由与依据的各种组合、500 字截断；
  - 5.13 `focus` 初始选中规则。
- **改写 `training-scan-batch-panel.spec.ts`**（现有 3 个用例按新界面改写），新增：
  - 进入时恢复最近批次，并逐份读取判定与反馈，最多同时 4 个请求；切换批次后，旧批次的结果不写入；
  - 导入一步完成：先建批次，再上传；上传中途失败时保留批次；
  - 异常页排在队列最前；处理后选中下一份；
  - 批量判定：确认框写明人数和请求次数；取消时不发请求；确认后按队列顺序每份只发 1 次提交；发送前阶段已变的跳过；模糊写入错误只查询不重发；一份失败不影响其后各份；“停止发送剩余”生效；结束提示的计数正确；
  - 批量更新：只对 `update` 阶段发布；`review` 阶段排除并计入提示；模糊写入错误只核对不重发；
  - 一键锁定：请求体正确；请求进行中禁用；成功后焦点移到下一待复核点；冲突时重新读取并提示；点击已按下的一段不发请求；
  - 切换学生后备注草稿保留；
  - `initialFocus` 三种取值的初始选中。
- **迁移后删除 `training-assessment-panel.spec.ts`**，其 3 个用例迁入上面的文件：
  - 超时后只查询、不再提交，直到出结果；
  - 失败重试需要重新确认费用；
  - 锁定不确定点后发布证据，只打开下一轮草稿。
  随 `TrainingAssessmentPanel.vue` 一起删除仅供它使用的准备代码。
- **`personalized-recommendation-draft.spec.ts`**：断言 `workspaceChange` 事件的内容；`selectWorkspaceStep` 可从外部调用；组件内不再出现步骤条行。
- **`training-recommendations-view.spec.ts`**：
  - 草稿打开后页头没有五个页签；
  - 返回按钮的名称与去向按 4.2 的三种来源变化；
  - 页头步骤条可切换步骤；
  - 未打开草稿时页签仍在；
  - `focus` 读取后从地址中移除。
- **`workbench-view.spec.ts`**：三类训练待办的去向带 `from=workbench` 与对应 `focus`；复核行事实文字为新文案。

浏览器（Playwright，`frontend/e2e/training-recommendations-real-api.spec.ts`。该文件已拦截全部 `/api/` 请求并返回模拟数据，开发服务在 8018 端口，与真实应用隔离）：

- 新增用例：模拟一份带冻结卷与扫描批次的训练草稿，地址带 `focus=review`。核对：
  - 页头没有页签，步骤条在右上；
  - 初始选中第一份待复核答卷，焦点在第一个待复核点；
  - 1440 宽三栏、1100 宽右侧上下排列、900 宽单栏；
  - 三种宽度都没有整页横向滚动，台账底栏不遮挡内容；
  - 批量判定确认框点“取消”后，没有任何 `POST …/assessment`；
  - 一键锁定发出 1 次 `POST …/assessment/reviews`。

命令（只跑覆盖改动的部分）：

- 在 `frontend` 目录运行 `npm run typecheck`、`npm run lint`；
- `npx vitest run src/__tests__/training-return.spec.ts src/__tests__/training-scan-batch-panel.spec.ts src/__tests__/personalized-recommendation-draft.spec.ts src/__tests__/training-recommendations-view.spec.ts src/__tests__/workbench-view.spec.ts`；
- `npx playwright test --config playwright.p2-18-real.config.ts`（8018 开发服务，使用模拟接口；真实应用由根目录 `运行.bat` 在 8035 启动）。

后端没有改动，不新增后端测试。最终提交前运行一次 `tools/run_test_suite.py quick`。本机首次并行运行出现内存不足，使用 `tools/run_test_suite.py quick --workers 1` 完成后端 88＋4、前端关键流程 38、题框编辑器 7 项检查，全部通过。

## 8. 文档（与改变该规则的代码同一次提交）

- `docs/product/KNOWLEDGE_AND_TRAINING.md`：
  - 第 259 行“工作区按…切换”：补写草稿打开后页头为“返回＋草稿标题与信息＋右上步骤条”，不显示知识与训练页签；删去“页头以实际草稿的模式、人数和设置为准”中与此重复的部分，改写为同一句。
  - “扫描归卷”第 346 行：默认选中每名学生的最新冻结卷，可调整；导入时建立批次并归组，一步完成。
  - 第 349 行：改写为汇总行加三栏（队列、原始答卷、判定点台账），异常页在队列最前，队列按“需要处理”排序；切换学生保留备注草稿与查询状态。
  - 第 350 行：补写工作台入口直接定位到第一份对应答卷，以及“判定失败”在工作台计入复核、页面单列这一口径差异。
  - “整卷四态判定”第 354 行：开始判定可以单份或批量；批量一次确认，写明份数、请求次数与费用，逐份发送，每份仍只发 1 次请求；重试每次单独确认。
  - “教师锁与证据回流”第 362 行：改写为一键锁定、自动生成核对依据与原因、可加备注、待复核点高亮并自动聚焦。
  - 第 366 行：“待确认更新”改为页面实际使用的“待更新”；补写批量更新只包含全部题完成的答卷。
  - 改写原句，不在末尾追加。行号以实施时的文件为准（另一任务可能已改动该文件）。
- `docs/ui/STYLE.md`：
  - 第 51 行知识与训练页头规则：补写打开草稿后的页头形式；
  - 新增一条回收批改布局规则：三栏宽度、两个断点、台账底栏固定，以及汇总行只有一个主要按钮。
- `docs/testing/README.md`：第 18 行把 `training-assessment-panel` 换成 `training-scan-batch-panel` 与 `training-return`，并在训练相关条目中登记本页的单元与浏览器覆盖范围。
- `CONTEXT.md`：核对是否有“待确认更新”等相关词条；目前没有，预计无需修改。如有变化，改写对应词条。
- `ARCHITECTURE.md`：前端内部重组，模块连接与数据归属不变，预计无需修改；实施时核对一遍。
- 本文：实施后把状态改为“已实施”，并写明验证限制。
- `tools/check_documentation.py` 检查通过。

## 9. 验证（不调用真实模型）

- 所有判定、锁定、发布都只在单元测试与 Playwright 模拟接口中验证。不得在真实应用中点击“判定”“重试”或“批量判定”：这些操作会调用真实模型并产生费用，需要用户逐次授权。
- 真实数据只读查看：真实库目前没有任何扫描批次，进入有冻结卷的训练草稿只能看到“回收答卷”面板和新页头。不在真实数据上建批次、上传扫描件。
- 性能：最后一次模拟浏览器测量为 40 份答卷，每份 10 题、25 个判定点；进入页面到全部队列状态显示为 5.319 秒，共 80 次状态读取，同时最多 4 次。测量包含草稿进入过程与模拟接口等待，不代表真实判定耗时；未调用模型。合成记录见 `output/TEST-training-return-20261004/browser-results.json`。
- 视觉：已对照原型核对 1440、1280、1100、900px。真实冻结卷的页头和无批次面板均无横向滚动；1100px 步骤条换行，900px 使用窄窗口布局。合成三栏按 1280px、1024px 断点排列，台账底栏不遮挡内容，减少动态效果时没有运行中的动画。合成截图在 `output/TEST-training-return-20261004/synthetic-{宽度}.png`，未将真实数据截图复制到项目输出。

## 10. 分阶段提交

每个阶段一个本地提交，只含该阶段内容，按 `AGENTS.md` 写收尾三行。不 push。

1. **草稿页头**：第 4 节及对应测试；`STYLE.md` 第 51 行；产品文档第 259 行。
2. **回收批改改版**：第 5.1–5.12 节、删除旧面板及其测试、迁移用例、浏览器用例；产品文档第 346–366 行、`STYLE.md` 新规则、测试文档。
3. **工作台定位**：第 5.13 节及 `workbench-view` 测试；产品文档第 350 行。

## 11. 风险与待核实

- **进入时的请求数**：合成 40 份答卷已验证 80 次读取、并发不超过 4；各行逐份更新。本次没有新增接口。
- **从工作台直接打开**：页面仍等现有诊断完成后渲染草稿。真实冻结卷已验证直接进入回收批改，消费 `focus` 后保留 `draft` 与 `from`；连续刷新到回收面板可见为 2.690 秒（含诊断与草稿读取）。本次未改诊断逻辑。
- **失败后部分结果的锁定**：界面允许锁定并保留错误与重试入口；真实应用中该路径未验证，不能据模拟接口断言后端接受。
- **批量发布与下一轮草稿**：合成 2 份已验证依次调用单份发布，排除 1 份待复核答卷；模糊写入只核对反馈，不重发。单份反馈的下一轮草稿入口已验证只发出打开事件。本次没有在真实后端生成下一轮草稿，不能据模拟回执核实真实生成次数。
- **模糊写入后继续下一份**：前一份结果未知时，继续发送下一份，两次模型请求可能在后台同时进行。每份仍只发 1 次请求，费用不变。
- **真实验收受限**：只读核实真实库有 5 份冻结卷、0 个扫描批次。真实验收没有创建批次、上传、锁定、发布、删除或迁移数据，也没有调用模型。完整流程仍需后续单独授权的真实小批量试用。
- **测试范围**：五组相关单元测试共 48 项通过，浏览器 4 项通过；类型检查、静态检查和文档检查通过。旧判定面板与其 3 个测试已迁移到回收面板并删除。
