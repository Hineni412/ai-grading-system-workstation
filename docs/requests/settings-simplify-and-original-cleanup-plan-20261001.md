# 设置页简化、学生名单并入与原卷清理：实现方案

用途：用户已确认的需求与实现方案，交给实现代理执行。2026-10-01 编写。
状态：**已实现，并完成合成数据流程验收和本次已授权的真实数据只读界面验收**。界面以原型 `docs/requests/settings-simplify-prototype-20261001.html` 为准（用户已认可文字与布局）。用户追加授权后，已通过 `运行.bat` 启动新版，前端构建与健康检查通过；真实清理、恢复应用和真实模型调用未执行。当前规则以产品、存储和测试文档为准，本文第 2 节保留实施前调查背景。

实现代理开始前必须阅读：`AGENTS.md`、`ARCHITECTURE.md`、`docs/ui/STYLE.md`、`docs/maintenance/storage-policy.md`、`docs/security/SECURITY.md`、`docs/testing/README.md`。本文与原型冲突时以本文为准；本文与代码事实冲突时停下来报告，不自行改变产品规则。

---

## 0. 硬性约束（违反任何一条都要停下来问）

1. **不对真实 `user_data` 执行任何清理、删除、改写**。开发和验证一律用测试沙箱或合成数据（见 `docs/testing/README.md` 隔离规则）。不在真实考试上点“释放扫描文件 / 清除原卷 / 清理旧批注图”。
2. 不做数据库迁移（`migrations/`）。本方案设计为**不需要改表结构**；如果实现中发现必须改表，停下来报告。
3. 不调用真实模型，不重启用户正在使用的服务；重建共享 `frontend/dist` 前确认没有其他任务占用（AGENTS.md「运行环境」）。
4. 本地 `main` 开发，不建分支；仓库里有其他任务的未提交改动（题库相关文件、`docs/requests/*prototype*.html`），不要碰、不要一起提交。
5. 教师最终分锁、评分、成绩、报告口径、训练证据含义一律不变。
6. 已有的确认保护只能按本文第 3.3 节调整，不得额外放宽。

---

## 1. 已确认的需求（用户原话归纳）

| # | 需求 | 结论 |
|---|---|---|
| 1 | 设置页风格与最近改过的页面对齐，术语通俗 | 按原型实现 |
| 2 | 设置页内不要第二条左侧栏 | 改为标题行右侧标签：**学生名单 / AI 服务 / 数据与空间 / 系统状态**（与成绩中心、题库原型一致） |
| 3 | 学生管理并入设置页，不再单独成页 | `/students` 重定向到 `/settings?section=students`；侧栏只保留「任务中心」「设置」 |
| 4 | 清理学生原卷、保留作答数据 | 两档：**释放扫描文件**（只删上传的扫描 PDF）与**清除原卷**（再删页面图与批注图） |
| 5 | 原卷清除后仍允许改分 | 允许；分数、报告照常更新，只是没有原卷可对照 |
| 6 | 确认方式按风险区分 | 备份直接点按钮；恢复备份、清除原卷保留输入确认文字；释放扫描文件用普通确认弹窗 |
| 7 | 批注图改为用时生成 | 不再长期保存批注图；查看时现场画，放入有上限的缓存 |
| 8 | 已有批注图 | 实现后不自动删除；在「数据与空间」提供「清理旧批注图」，由教师自己点 |
| 9 | 清理提醒 | 不做 |

---

## 2. 现状事实（已核对代码与本机只读数据）

### 2.1 一场考试的原卷文件（以 session_3 为例，约 220 MB）

| 文件 | 位置 | 写入方 | 之后谁读 |
|---|---|---|---|
| 上传的扫描 PDF（全班一个或多个） | `user_data/exams/session_N/scan_batches/<batch>/files/<sha256>.pdf`，97.8 MB | `ScanGradingWorkspace.add_upload` | 仅扫描预检 `scanner.render_pdf_to_standard_pages`（重新预检/归卷） |
| 样卷上传时整份全班 PDF 的**副本** | `user_data/templates/session_N/template-versions/<ver>/template_source_full_class.pdf`，52.9 MB；sha256 与上面某个扫描 PDF 完全相同 | `backend/exam_intake/template_upload_service.py` 第 152–156 行 | **无人读取**（`source_path` 赋值后未使用；`backend/files/data_transfer_service.py` 的 lean 导出已跳过它） |
| 每页工作图 | `.../files/_pdf_pages/<sha256>/page_###.jpg` + `source_manifest.json`，33.7 MB | `scanner.render_pdf_to_standard_pages` | `exam_papers.front_image/back_image` 指向它们；复核原卷/裁剪图、AI 批改、批注图生成、批注原卷导出、个人报告截图 |
| 批注图（整页重画的红笔图） | `user_data/annotated/session_N/result_<rid>_<uuid>_{front,back}_annotated.jpg`，35.4 MB；`annotated_results` 表存路径 | `ManualReviewService._render_result_annotation_locked` | 仅复核页 `variant=annotated` 媒体接口 |

其他事实：

- 分数、每题得分、扣分原因、识别出的作答（`session_results.raw_json.detail_metadata.*.recognized_answer`）、错因、知识点都在 `grading_system.db`；题库库不引用任何考试图片（已只读核对）。
- 前端复核保存一律传 `annotation_mode: 'on_demand'`（`frontend/src/api/review.ts` 第 470 行），保存只让旧批注失效；`ReviewMediaService.resolve_result_page` 在路径为空时调用 `ensure_result_annotation` 现场生成。即“按需生成”已经存在，只是生成结果被长期存进 `user_data/annotated/`。
- 批注原卷 PDF 导出 `OriginalPaperExporter.export_session_originals` **自己重画**，不读已存批注图，但需要每页工作图。
- 个人报告（`analysis_report_exporter.capture_lost_question_shots`、`_personal_image_inputs`）从工作图裁剪丢分题截图、并把答卷图发给模型；缺图时不阻断报告，写入“未取得该生可读取的答卷图片…”说明。
- AI 批改 `grading_service.run_session_grading` 在给出 `scan_analysis` 时直接用已存分析与工作图，不调用 `scanner.analyze`；只有无分析时才重新拆 PDF（待验证项见 6.4）。
- 复核裁剪图缓存：`user_data/cache/review_crops/`，256 MB 上限，`ReviewMediaService.clear_detail_crop_cache()` 可整体清空（可再生成）。
- 普通备份（`update_tools/backup_core.py`）包含 `exams/`、`templates/`、`annotated/`、`reports/` 等，不包含 `cache/`。
- 考试永久删除已有一套“暂存 + 清单 + 恢复”的安全删除实现：`backend/files/session_cleanup.py`（`session_lifecycle_guard`、`_resolve_stored_candidate`、`_is_under` 等），以及“先只读预览影响 + 版本号 + 确认文字”的接口模式：`backend/api/routers/sessions.py` 的 `deletion-impact` / `permanent`。
- 设置页现状：`frontend/src/views/SettingsHubView.vue`（内部左侧菜单，section 为 `ai | ai-trace | backup | maintenance`）、`SettingsOpsView.vue`、`ModelProfilesView.vue`、`components/settings/AiDiagnosticsPanel.vue`、`styles/settings-ops.css`。`/model-profiles` 重定向时写的是 `section=models`，这不是有效值（顺手修）。
- 学生名单现状：`views/StudentsView.vue` + `components/students/{StudentRosterTable,StudentInspector,StudentImportDesk}.vue` + `stores/students.ts` + `styles/students.css`；路由 `/students`；侧栏底部 `settingsNavigationItems = [students, aiTrace, settings]`（`navigation.ts` 第 255 行），由 `components/shell/AppSidebar.vue` 渲染。
- 页面级视图标签的现行写法：`ResultsCenterView.vue` 把 `<nav class="results-rail">` 放进 `PageHeader` 的 `#actions` 插槽（标题行右侧），样式在 `styles/base.css` 第 334–369 行的 `:is(.results-rail, .page-tabs)`。

---

## 3. 前端：设置页

### 3.1 外壳 `SettingsHubView.vue`（改写）

- 使用 `design-system/PageHeader`：`title="设置"`；`#actions` 插槽放 `<nav class="page-tabs" aria-label="设置分类">`，四个按钮：学生名单、AI 服务、数据与空间、系统状态。删除现在的 `settings-hub__menu`、眉题、说明句和内容区标题（STYLE.md：标题只出现一次，不写解释性灰字）。
- section 取值改为 `students | ai | data | system`。存储键升级为 `ai-grading:settings-section:v2`；首次默认 `students`。
- 兼容输入（旧链接，写成重定向映射，不保留旧取值）：`ai-trace → system`（并滚动到调用记录）、`backup → data`、`maintenance → system`、`models → ai`。
- 离开 AI 服务时的未保存保护沿用现有 `hasUnsavedChanges` 逻辑。
- 内容区最大宽度沿用 `--content-max-width`，左对齐。
- 各分区组件按需异步加载（沿用 `defineAsyncComponent`）。

### 3.2 学生名单（新分区，复用现有学生代码）

新建 `components/settings/SettingsStudentsPanel.vue` 作为分区入口，复用 `stores/students.ts` 和现有三个学生组件（改造而非重写；store 与 API 契约不变）。

- 工具行：搜索框（输入即搜，300 ms 防抖，调用 `roster.load({ search, class_name, page: 1 })`）、班级下拉（变更即生效）、“共 N 名”、右侧 `AppButton variant="secondary"`「导入名单」+ 小字「CSV / Excel」。去掉「筛选」按钮。
- 表格：学号 / 姓名 / 班级 / 操作（弱化按钮「编辑」）。列宽固定（约 150 / 160 / 140 px），表格面板最大宽度约 780 px，避免中间大片空白。分页沿用现有 50 条/页与上一页/下一页。空结果：一句「没有符合条件的学生」+「清除筛选」。
- **编辑**：`StudentInspector` 改为右侧抽屉（复用 `components/ui/sheet`，参考 `sessions/SessionManagementDrawer.vue` 的写法）。字段学号、姓名、班级（选填），底部取消 / 保存。删除区：弱化危险按钮「删除这名学生…」→ 调用现有 `loadDeletionImpact()` → 显示一句影响：「将删除 {deleted_results} 份成绩、{deleted_details} 条评分明细、{deleted_annotations} 条批注记录和 {deleted_attendance} 条考勤记录，并解除 {unlinked_papers} 份答卷与该生的关联。删除前会自动备份。」→ 输入学号确认 → 危险按钮「备份并删除」。确认规则与后端调用完全不变。
- **导入名单**：`StudentImportDesk` 的预览部分改为宽弹窗（约 760 px，参考 `file-center/ScoreExcelSettingsDialog.vue` 的弹窗结构与 `fx-dialog`）。选文件后打开弹窗：
  - 一行「表格里哪一列是：学号 [▾] 姓名 [▾] 班级 [▾（含“不导入班级”）]」+ 弱化按钮「重新比对」（即原「应用列对应」）。
  - 计数胶囊：新增 / 更新 / 无变化 / **有问题**（原“无效”）/ **重复行**（原“重复”）。
  - 复选框「只看有变化的行」（默认勾选；纯前端过滤，显示 operation ≠ unchanged 的行）。
  - 预览表：☐ / **行号**（原“源行”）/ 学号 / 姓名 / 班级 / 变化 / 说明。可选规则不变。
  - 底部：「已选 N 行，确认后才会写入。」+ 取消 / 主要按钮「写入名单（N 人）」。写入后关闭弹窗并刷新列表。
  - 去掉三步圆点导轨（单一弹窗内已能表达流程）。
- 删除 `views/StudentsView.vue`；`styles/students.css` 中不再使用的选择器一并删除，保留仍被三个组件使用的部分。

### 3.3 AI 服务（改造 `ModelProfilesView.vue`，store 与 API 不变）

- 「用哪个 AI」：两行（沿用 `taskRows`）：
  - 出题与评分标准（小字：题库标注、评分标准、组卷）
  - 批改试卷（小字：含姓名补充识别）
  每行：服务账号下拉 + 模型名输入。主要按钮「保存」仅在有修改时可用；旁边一句「保存不会调用 AI，也不产生费用。」删除“四类工作”等与实际不符的文字。
- 「服务账号」列表：每行名称、地址主机名、`StatusBadge`「密钥已保存」/「未保存密钥」、当前默认服务显示「默认」徽标；有请求时显示「运行中：X 个请求，排队 Y 个」（对每个账号调用现有 `getExecutionStatus`，只在 active+queued > 0 时显示）。行操作：弱化「编辑」、弱化危险「删除」（沿用现有删除确认文案与逻辑）。标题行右侧次要按钮「添加服务」。
- 编辑/添加抽屉（`ui/sheet`）：名称（编辑时只读）、地址、密钥（占位「已保存，留空不改」）。`<details>`「高级」内：请求速度（自动（推荐）/ 保守 / 自定义，自定义显示同时请求数与每分钟请求数）、失败自动重试次数、等待超时（秒，30–1200）、当前运行状态（现有指标）、各任务内置默认（现有折叠列表）、次要按钮「设为默认服务（旧任务使用）」= 现有 `activateProfile`（独立操作，不并入保存）。底部取消 / 保存。字段校验、密钥不回读、`beforeunload` 保护全部沿用。
- 删除 compact/非 compact 两套头部与说明区；`styles/model-profiles.css` 中不再使用的类删除。

### 3.4 数据与空间（改造 `SettingsOpsView.vue` 的备份部分 + 新增空间与原卷）

新建 `components/settings/SettingsDataPanel.vue`，依次四块：

**A. 空间占用**：一行「本机数据共 X GB」、一条细的分段条（`--chart-1..5` + 中性色）、两列图例（类别、大小）。类别与数据来自 6.2 的 `GET /api/ops/storage`：学生原卷、批注图、题库资料、报告与导出、备份、数据库、其他。若 `legacy_annotations.bytes > 0`，图例下方加一行「旧批注图 103 MB · 可随时重新生成」+ 弱化按钮「清理」→ 普通确认弹窗（「清理后，查看批注卷时会重新生成。」）→ `POST /api/ops/storage/legacy-annotations/clear`。

**B. 考试原卷**：表格（带复选框）。列：考试（名称 + 日期）/ 状态徽标 / 扫描文件（大小或「—」）/ 页面与批注图（大小或「—」）/ 原卷（徽标：完整、已释放扫描文件、已清除、清理未完成）/ 操作。
- 操作按钮：次要「释放扫描文件」（无扫描文件时隐藏）、危险描边「清除原卷」（已清除时隐藏）；`can_release_scans` / `can_clear` 为假时按钮禁用并在下方显示 `blocked_reason`（如「复核完成后可清理」）。`state = clearing` 时显示「继续清理」。
- 勾选 ≥1 个可操作行时出现批量条「已选 N 场 · 可腾出 X MB」+ 同样两个按钮；批量时前端**逐场**先取 `GET .../originals`，再逐场提交，任一失败停止并显示该场原因。
- 表上方一句「清除前建议先备份。」+ 链接按钮「立即备份」（滚动到 C）。
- **释放扫描文件弹窗**（普通确认）：标题「释放「{考试名}」的扫描文件？」；正文「可腾出 {release_bytes}。分数、查看原卷、AI 继续批改都不受影响；之后不能再重新扫描归卷。」；按钮 取消 / 释放。若响应 `kept_unrendered > 0`，提示「有 N 个文件还没拆成页面，已保留。」
- **清除原卷弹窗**（输入确认）：标题「清除「{考试名}」的学生原卷」；两列：
  - 会保留：分数和排名、每题得分与扣分原因、识别出的作答内容、成绩报告、掌握度和训练记录、老师改分
  - 之后无法：查看原卷和批注图、AI 重新批改、重新扫描归卷、导出批注原卷、个人报告里的作答截图
  - 「可腾出 {clear_bytes}」
  - 备份提示：`backup_covers_originals` 为假时显示「最近一次备份：{时间}，早于这场考试的批改，备份里没有这些原卷。」+ 次要按钮「先备份」（执行 C 的一键备份，完成后刷新提示）；为真时显示「最近一次备份：{时间}，包含这场考试的原卷。」
  - 「输入「确认清除」继续」+ 危险按钮「清除原卷」（完全匹配才可用）；提交中显示进度，完成后提示「已腾出 X」并刷新 A、B。
- 409 冲突（版本变化/有活动任务）：显示服务端原因，按钮「刷新」。网络错误：不自动重发，提示刷新后再看状态（清除接口幂等，见 6.3）。

**C. 备份**：一行「最近备份：{时间} · {大小}」+ 主要按钮「立即备份」。点击后：`startPreflight({operation:'backup', reason:'manual', scopes:['grading']})` → 成功后**直接** `submitConfirmed()`（不再输入“确认备份”，不再选原因）→ 行内细进度条 → 完成后「备份完成 · {大小}」+ 次要按钮「下载」（沿用 `downloadResult`）。失败显示现有 `actionError`。

**D. 恢复**：备份表（单选）：时间 / 原因（沿用 `BACKUP_REASON_LABELS`）/ 大小。危险按钮「恢复所选备份」→ 预检 → 弹窗：「用这份备份覆盖当前同名数据。重启应用后才生效，重启前可以撤销。」+ 预检摘要（文件数、大小）+ 「输入「确认恢复」继续」→ `submitConfirmed()`。之后显示警示条「恢复已准备好，重启应用后生效。」+「撤销」（现有 `cancelCurrent`）。`operationOutcome` 各状态的文案沿用现有含义，只改成一句话放在警示条里。

迁移、数据包导入/导出：当前设置页没有入口，保持没有。

删除 `SettingsOpsView.vue` 中被拆走的部分；不再使用的 `styles/settings-ops.css` 规则删除（文件可整体删除，若无引用）。

### 3.5 系统状态（改造 `SettingsOpsView` 的维护部分 + `AiDiagnosticsPanel`）

新建 `components/settings/SettingsSystemPanel.vue`：

- 状态行：`selfCheck.status === 'ok'` → 成功徽标「一切正常」；否则警示徽标「有 N 项需要注意」+ 第一项问题的一句话（例：「未找到 Microsoft Word，导出 Word 时会改用 LibreOffice。」只在确实有回退时这样写；没有把握的写成「未找到 Microsoft Word」）。右侧「版本 {version}」+ 次要按钮「复制排查信息」（沿用 `diagnosticText`，提示「已复制（不含密钥和学生信息）」——仅当现有诊断文本确实已脱敏；`ops` store 中写的是“脱敏诊断”，实现时核对）。
- `<details>`「查看全部检查项」：两列：数据文件夹、阅卷数据库、题库数据库（完整/需检查）、数据库版本（`pending_migrations == 0` →「已是最新」，否则「有 N 项待更新」）、Microsoft Word / LibreOffice / PDF 排版工具（可用/未找到）、AI 服务（已配置/未配置）。
- 「AI 调用记录」：`AiDiagnosticsPanel` 原样逻辑，界面改动：筛选标签「来源」→「用途」；结果文字「模型已返回 / 调用失败 / 等待返回」→「成功 / 失败 / 等待中」（只改显示，“成功”仍只表示请求完成）；列表项与详情排版按原型；五个详情页签保留，「原始返回」改名「返回内容」。隐私说明保留为一句「记录只保存在本机。」+ 折叠「了解记录范围」（现有完整说明原文保留，属于安全说明，不删）。

### 3.6 导航、路由、侧栏

- `navigation.ts`：`settingsNavigationItems = [settingsRouteDefinition]`；删除 `aiTraceRouteDefinition` 的侧栏用途；`studentsRouteDefinition.path` 改为 `/settings?section=students`（保留 id/label「学生名单」供快速跳转 `Ctrl+K` 搜索使用），`navigationItems` 中保留它以便搜索到。核对 `CommandPalette` 与 `AppSidebar` 对带 query 的 path 是否正常激活高亮。
- `router/index.ts`：`/students` 改为 redirect 到 `{ path: '/settings', query: { ...to.query, section: 'students' } }`；`/model-profiles` 的 redirect 改为 `section: 'ai'`。`settingsRouteDefinition.description` 改为「学生名单、AI 服务、数据与空间、系统状态」。
- `AppSidebar.vue`：底部只剩任务中心与设置；核对激活指示器计算不依赖被删除的项。

### 3.7 「原卷已清理」在其他页面的表现

- 复核与深查：`backend/api/schemas/review.py` 的 `ReviewMediaLinksResponse` 增加 `originals_available: bool`（默认 true）；`_review_item_response` 在该场 `originals_state` 为 `cleared/clearing` 时设为 false。前端 `ReviewEvidenceViewer.vue`、`ReviewAnswerSheet.vue`、`ReviewDeepWorkspace.vue`：为 false 时不发图片请求，在图片位置显示安静的文字「原卷已清理，分数和作答记录仍保留。」，原卷/标注切换按钮隐藏。改分与确认照常可用。
- 其他使用裁剪图的地方：`backend/api/routers/analytics.py:95`、`graph.py:263`、`students.py:375` 生成的 crop URL，以及 `frontend/src/api/analysis.ts:182` 前端拼接的 URL。后端在原卷已清除时把对应 URL 字段置为 null（字段类型若为 `str` 改为 `str | None`），前端拼接处改为使用后端字段或在已清除时不拼接；显示处同样用文字占位。实现时 `rg "crop"` 全量核对一遍。
- 文件中心：`FileCenterView.vue` 的「批注原卷」生成选项在当前考试原卷已清除时禁用，旁边一句「原卷已清理，不能再导出批注原卷。」（状态来自 `GET /api/sessions/{id}/originals`）。后端导出也要拒绝（见 6.4），前端只是提前提示。
- 考试批改页（扫描预检/重新预检）：释放后提交预检返回 409 `scan_sources_released`，页面显示服务端消息。

---

## 4. 后端：批注图改为用时生成（缓存）

目标：批注图只放在有上限的缓存里，不进普通备份，随时可重画；`annotated_results` 表与“保存使旧批注失效”的语义不变。

1. `path_manager.py` 新增 `annotation_cache_dir = data_root / "cache" / "annotated_pages"`；`backend/api/dependencies.py` 新增 `get_annotation_cache_dir`，`get_media_service` 与 `ManualReviewService` 的 `annotated_dir` 参数改为传它。`get_annotated_dir`（旧目录）仅保留给旧批注图清理与统计使用。
2. `ManualReviewService`：
   - 输出路径改为 `annotation_cache_dir / f"session_{sid}" / f"result_{rid}_{uuid}_{page}_annotated.jpg"`（目录结构不变，只是根换了）。会话锁继续用 `get_answer_region_session_lock(self.annotated_dir / f"session_{sid}")`——`ReviewApplicationService.confirm` 用的是同一个属性，保持一致。
   - `ensure_result_annotation`：除路径为空外，**路径不在当前 `annotated_dir` 下或文件不存在**时也重画（旧行指向 `user_data/annotated/` 时会自动迁到缓存，旧文件不动）。
   - `_cleanup_replaced_annotation_files` 只删除当前 `annotated_dir`（缓存）下的旧文件——现有 `relative_to(annotated_root)` 判断已保证不会删到旧目录，保留这一行为并加测试。
   - 生成后裁剪缓存：新增 `_trim_annotation_cache(max_bytes=256 MiB, keep={本次两张})`，递归统计 `annotation_cache_dir/session_*/*.jpg`，按修改时间从旧到新删除，**跳过 10 分钟内修改过的文件**（避免删掉刚生成、正在发送的图）。被删文件即使仍被 `annotated_results` 引用也没关系，下次查看会重画。
   - 原卷已清除（`originals_state` 为 `cleared/clearing`）时，`ensure_result_annotation` / `render_result_annotation` 不尝试渲染，抛出明确的 `OriginalPagesCleared`（定义在 6 节新模块）。
3. `ReviewMediaService.resolve_result_page`（annotated 分支）：路径为空、不在缓存根下或文件缺失时调用 `ensure_result_annotation`；成功返回前 `os.utime` 刷新文件时间作为最近使用。`OriginalPagesCleared` 映射为 410 `original_pages_cleared`（`backend/api/routers/media.py::_raise_media_api_error` 增加分支）。original 变体与 crop 在原卷清除后同样返回 410 `original_pages_cleared`（先查状态，再解析文件）。
4. 考试永久删除：`session_cleanup._collect_session_dirs` 增加 `data_root / "cache" / "annotated_pages" / f"session_{id}"`。扫描替换（`collect_session_reupload_storage_paths`）沿用 DB 路径删除，无需改。
5. 已有 `user_data/annotated/` 文件：**实现不得删除**；只在 6.2 的接口中由教师触发清理。

---

## 5. 后端：样卷上传不再保存整份全班 PDF

- `backend/exam_intake/template_upload_service.py`：不再写 `template_source_full_class.pdf`（删除 `source_temp` 的写入与 `source_path` 变量；PDF 字节只在内存中打开以渲染两页）。模板指纹、版本目录、激活回执逻辑不变。
- `data_transfer_service.LEAN_SKIP_FILE_NAMES` 中的该文件名保留（兼容输入：旧版本目录里仍可能有）。
- 现有文件由「释放扫描文件」顺带删除（见 6.3）。

---

## 6. 后端：原卷状态、空间统计与清理

### 6.1 新模块 `backend/files/session_originals.py`（项目根目录，与 `backend/files/session_cleanup.py` 同层）

复用候选与理由：`backend/files/session_cleanup.py` 是“整场考试永久删除”，带暂存与回滚，因为它要与数据库删除保持一致；原卷清理的目标就是删除文件、数据库记录保持不变，不需要回滚，只需要可重入。因此新建模块，但**复用** `session_cleanup.session_lifecycle_guard`、`_resolve_stored_candidate`、`_is_under`（直接 import；如嫌私有名，可在 `backend/files/session_cleanup.py` 去掉前导下划线并保留旧名别名，二选一，不要复制实现）。只有一种实现，不建接口/策略层。

**回执文件**：`user_data/exams/session_{id}/originals_receipt.json`（随 `exams/` 进入普通备份；永久删除考试时随目录删除）。用临时文件 + `os.replace` 原子写入。

```json
{
  "version": 1,
  "session_id": 3,
  "state": "scans_released | clearing | cleared",
  "scans_released_at": "2026-10-02T10:00:00",
  "cleared_at": null,
  "freed_bytes": {"scans": 0, "pages": 0, "annotations": 0},
  "deleted_files": 0,
  "kept_unrendered": 0
}
```

状态读取 `originals_state(data_root, session_id) -> "complete" | "scans_released" | "clearing" | "cleared"`：无回执为 `complete`；回执损坏时按 `clearing` 处理（保守：阻止依赖原卷的操作，界面显示「清理未完成 · 继续清理」）。

模块函数（签名为约定，按需调整内部实现）：

```python
class OriginalPagesCleared(RuntimeError): ...
class ScanSourcesReleased(RuntimeError): ...

def originals_state(data_root: Path, session_id: int) -> str
def measure_session_originals(db, data_root: Path, session_id: int) -> dict
    # {"scan_bytes", "page_bytes", "annotation_bytes", "release_bytes", "clear_bytes"}
def release_session_scans(db, data_root: Path, session_id: int) -> dict
def clear_session_originals(db, data_root: Path, session_id: int, *,
                            clear_crop_cache: Callable[[], int]) -> dict
def storage_overview(data_root: Path) -> dict
def clear_legacy_annotations(db, data_root: Path) -> dict
```

**文件归类规则**（只处理 `data_root` 内、解析后仍在 `data_root` 内的普通文件；不跟随指向外部的路径）：

- 扫描文件（第 1 档）：
  - `exams/session_N/scan_batches/*/files/*.pdf`，**且**存在 `files/_pdf_pages/<该 pdf 的 stem>/source_manifest.json`，且该目录下 `page_*.jpg` 数量 = manifest 的 `page_count`；不满足的计入 `kept_unrendered` 并保留。
  - `templates/session_N/template-versions/*/template_source_full_class.pdf`。
  - 被**其他考试** `exam_papers.front_image/back_image` 引用的文件跳过（沿用 `session_cleanup` 的共享引用保护思路）。
- 页面与批注（第 2 档，含第 1 档全部）：
  - `exams/session_N/` 下所有 `.pdf .jpg .jpeg .png .webp .bmp`（包括直接上传的图片、`_pdf_pages/**/page_*.jpg`、任何 `_enhanced/`），**保留所有 `.json`**（批次清单、`source_manifest.json`、回执）。
  - `annotated/session_N/*_annotated.jpg`（旧批注图）。
  - `cache/annotated_pages/session_N/` 整个目录。
  - 不删：`templates/session_N/template-versions/*/template_{front,back}_from_pdf_page.jpg`（样卷两页，题框显示与裁剪坐标换算需要）、`templates/` 下所有 json、评分依据与来源文件。

### 6.2 接口

都放在现有路由文件里，不新建路由文件。

**`GET /api/ops/storage`**（`backend/api/routers/ops.py`）→

```json
{
  "total_bytes": 4500000000,
  "categories": [
    {"key": "originals", "label": "学生原卷", "bytes": 0},
    {"key": "annotations", "label": "批注图", "bytes": 0},
    {"key": "question_bank", "label": "题库资料", "bytes": 0},
    {"key": "reports", "label": "报告与导出", "bytes": 0},
    {"key": "backups", "label": "备份", "bytes": 0},
    {"key": "databases", "label": "数据库", "bytes": 0},
    {"key": "other", "label": "其他", "bytes": 0}
  ],
  "legacy_annotations": {"bytes": 0, "files": 0},
  "sessions": [
    {"session_id": 3, "name": "…", "created_at": "…", "status_label": "已完成|复核中|批改中|未开始",
     "originals_state": "complete", "scan_bytes": 0, "page_bytes": 0,
     "can_release_scans": true, "can_clear": true, "blocked_reason": null}
  ]
}
```

类别口径：originals = `exams/` + 各 `template_source_full_class.pdf`；annotations = `annotated/` + `cache/annotated_pages/`；question_bank = `question_bank/`；reports = `reports/` + `outputs/`；backups = `backups/` + `archives/`；databases = `databases/`；other = 其余。`page_bytes` = 该场页面图 + 旧批注图 + 缓存批注图。用 `os.scandir` 递归求和；只读，不写任何文件。sessions 只列未删除的考试，按创建时间倒序。

**`GET /api/sessions/{id}/originals`**（`sessions.py`）→ 单场：`originals_state`、`revision`（`f"{session_deletion_revision}:{originals_state}"`）、`scan_bytes`、`page_bytes`、`release_bytes`、`clear_bytes`、`can_release_scans`、`can_clear`、`blocked_reason`、`confirmation_phrase: "确认清除"`、`latest_backup_at`、`backup_covers_originals`。
- `backup_covers_originals`：最新 zip 普通备份（`OpsSelfCheckService.list_backups`）的时间晚于该场 `exam_papers.created_at` 最大值且晚于 `scans_released_at`（如有）为真。

**可清理条件**（`can_release_scans` 与 `can_clear` 共用，任一不满足即为假，`blocked_reason` 取第一条）：

1. 考试存在且未删除 → 否则 404。
2. `session_deletion_impact` 的 `active_jobs == 0` 且 `active_grading_runs == 0` →「这场考试还有正在运行的任务」。
3. 没有进行中的替换上传批次（`ScanGradingWorkspace.get_workspace(...)["replacement_batch"] is None`）→「正在替换答卷，完成后再清理」。
4. 有批改结果（`session_results` 行数 > 0）→「还没有批改结果」。
5. 没有未对应学生的答卷（`exam_papers.match_status != 'matched'` 计数为 0）→「还有答卷没有对应到学生」。
6. 复核完成：`ReviewApplicationService.list_questions(session_id, session, scope=None, manual_context=current_manual_context(...))` 汇总 `needs_review_count + ungraded_count + failed_count == 0` →「复核完成后可清理」。
7. `can_release_scans` 还要求 `release_bytes > 0`；`can_clear` 还要求状态不是 `cleared`。

**`POST /api/sessions/{id}/originals/release-scans`** body `{expected_revision}` → 在 `session_lifecycle_guard` 内重新检查条件与版本（不一致 409 `originals_revision_changed` / `originals_not_ready` + 原因），执行 `release_session_scans`。返回 `{originals_state, freed_bytes, deleted_files, kept_unrendered}`。

**`POST /api/sessions/{id}/originals/clear`** body `{expected_revision, confirmation_phrase}` → 短语必须等于「确认清除」（422 `originals_confirmation_mismatch`）。当前状态为 `clearing` 时跳过版本检查（继续未完成的清理），其余同上。返回同上。

**`POST /api/ops/storage/legacy-annotations/clear`** → 执行 `clear_legacy_annotations`，返回 `{freed_bytes, deleted_files}`。

均为同步请求（文件数为几百级）。错误一律走现有 `ApiError`（错误码 + 中文可显示的 message + 有限 details）。

### 6.3 执行步骤

`release_session_scans`：
1. 计算第 1 档候选；逐个 `unlink`（不存在视为已删）。
2. 写回执：`state` 若原为 `complete` 改为 `scans_released`，记录时间、释放字节、`kept_unrendered`；已是 `cleared` 则不降级。
3. 重复调用无副作用（候选为空则只返回 0）。

`clear_session_originals`（顺序固定，保证中断后重跑即可完成）：
1. 写回执 `state = "clearing"`（保留已有字段）。
2. 在该场批注会话锁内，把 `annotated_results` 中该场所有行的两个路径置为 NULL（新增 `ReviewRepository.clear_session_annotation_paths(session_id)`，复用现有 `invalidate_result_annotations` 的写法；这是应用自身流程内的数据库写入，不改分数）。
3. 删除第 2 档全部文件（不存在视为已删）；删除 `cache/annotated_pages/session_N/`；删除后清理空目录（仅限 `exams/session_N/scan_batches/**` 内的空目录，保留含 json 的目录）。
4. 调用 `clear_crop_cache()`（即 `ReviewMediaService.clear_detail_crop_cache`，整体清空可再生成的裁剪缓存）。
5. 写回执 `state = "cleared"`，记录时间与累计释放字节、删除文件数。

任何一步异常：保持 `clearing`，接口返回 409 `originals_clear_incomplete`（details 含 `retryable: true`），界面显示「继续清理」。

`clear_legacy_annotations`：先把 `annotated_results` 中指向 `user_data/annotated/` 的路径置 NULL（按会话加批注锁），再删除 `annotated/session_*/*_annotated.jpg`。`.lock` 文件和目录保留。

### 6.4 依赖原卷的操作要拒绝

| 操作 | 位置 | 何时拒绝 | 错误 |
|---|---|---|---|
| 提交扫描预检/重新预检 | `ScanGradingWorkspace.submit_scan_analysis` | `scans_released`、`clearing`、`cleared` | `ScanGradingWorkspaceError` → 路由 409 `scan_sources_released`「这场考试的扫描文件已释放，不能再重新扫描归卷。」 |
| AI 开始/继续/重试失败项/补批 | `prepare_start/submit_start`、`prepare_resume/submit_resume`、`prepare_failed_retry/submit_failed_retry`、`prepare_supplement/submit_supplement` | `clearing`、`cleared` | 409 `original_pages_cleared`「这场考试的原卷已清理，不能再让 AI 批改。」 |
| 批注原卷导出 | `OriginalPaperExporter.export_session_originals` | `clearing`、`cleared` | 抛出带中文说明的错误，任务失败信息可读 |
| 复核原卷/裁剪/批注图 | `ReviewMediaService` | `clearing`、`cleared` | 410 `original_pages_cleared` |
| 预检阶段裁剪 | `render_preflight_crop` 路由 | 同上 | 同上 |

扫描替换上传（`begin_replacement_upload` / `commit_replacement_upload`）**不拦截**，它是已有的、带自身确认的“换一批答卷重来”流程。替换提交成功后删除该场 `originals_receipt.json`（新答卷就是完整原卷）；在 `_finish_replacement_commit` 成功路径末尾处理，并补测试。

个人报告不拦截：缺图时沿用现有降级（不嵌截图、说明“未取得该生可读取的答卷图片”）。

**待验证假设（必须先验证再写文案）**：释放扫描文件后，「继续批改 / 重试失败项 / 补批」仍可用，因为它们使用已存的 `scan_analysis` 与工作图，不需要 PDF（依据：`grading_service.run_session_grading` 第 441–447 行只在没有分析时调用 `scanner.analyze`）。实现时用测试证明：删除 PDF 后 `prepare_resume`/`submit_resume` 与 `prepare_failed_retry` 正常。如不成立，**停下来报告**，不要自行把这些操作也拦截或改弹窗文案。

---

## 7. 测试（按 AGENTS.md 收尾顺序：先扩展现有测试，再在模块现有文件加函数，最后才新建文件）

后端：

- `tests/test_review_media_service.py`：批注图写入缓存目录；路径指向旧 `annotated/` 时重画到缓存且旧文件仍在；缓存超限时删除最旧且跳过 10 分钟内文件；原卷清除后 original/annotated/crop 都返回 `OriginalPagesCleared`。
- `tests/test_manual_review_atomic.py`（或现有 ManualReviewService 测试）：替换旧批注只删缓存内文件。
- `tests/test_template_upload_service.py`：上传后版本目录不再有 `template_source_full_class.pdf`，其余产物与指纹不变。
- `tests/test_session_cleanup.py`：永久删除考试同时移除 `cache/annotated_pages/session_N`。
- 新建 `tests/test_session_originals.py`（现有文件都不属于该模块）：
  - 释放：只删已完整拆页的 PDF 与样卷副本；未拆页 PDF 保留并计数；其他考试引用的文件不删；重复调用为 0。
  - 清除：删除页面图、旧批注图、缓存批注图，保留所有 json 与样卷两页；`annotated_results` 路径为 NULL；`session_results`、`session_details`、`teacher_score_locks` 行与数值完全不变（逐行比对）；回执为 `cleared`。
  - 中断：在第 3 步模拟 `OSError` → 回执为 `clearing`；再次调用完成为 `cleared`。
  - 回执损坏 → 按 `clearing` 处理。
  - 不出 `data_root`：伪造指向外部的 `front_image` 路径不被删除。
  - 存储统计：各类别字节数与合成目录一致。
- `tests/test_api_media_routes.py`：410 `original_pages_cleared`。
- 现有会话路由 API 测试文件（`deletion-impact` 测试所在文件）中增加：`GET /originals` 的条件判定（有活动任务、未复核、有未对应答卷、无结果各一例）、版本冲突 409、确认文字不符 422、`clearing` 时可继续。
- 扫描/批改工作区现有测试：释放后提交预检返回 409；清除后开始/继续批改返回 409；替换提交后回执被删除；**6.4 的待验证假设**。
- 批注原卷导出现有测试（`tests/test_report_export_job.py` 或 `test_original_paper_score_contract.py`）：清除后导出失败且信息可读。

前端（`frontend/src/__tests__`）：

- `settings-hub-view.spec.ts`：四个标签在标题行；默认学生名单；旧 section 值重定向；离开 AI 服务未保存保护。
- `settings-ops-view.spec.ts`：改为覆盖数据与空间/系统状态：一键备份不需输入文字直接提交；恢复仍需「确认恢复」；清除原卷需「确认清除」；释放为普通确认；不可清理时按钮禁用并显示原因；`clearing` 显示继续清理。如组件改名，测试随之改名，删除只覆盖旧结构的断言。
- `student-roster-view.spec.ts`：入口改为 `/settings?section=students`（并验证 `/students` 重定向）；输入即搜；编辑抽屉保存；删除需学号确认；导入弹窗“只看有变化的行”、写入人数。
- `model-profiles-view.spec.ts`：两行任务绑定保存；抽屉添加/编辑；高级中的设为默认服务；密钥不回读。
- 复核相关现有 spec：`originals_available: false` 时显示文字占位且不请求图片。
- `frontend/e2e/students-real-api.spec.ts`（`npm run e2e:students`）：入口路径更新。

测试分组：新后端测试默认进入 `full`；如加入 `QUICK_*` 需按 `tools/test_suite_manifest.py` 规则，并运行 `tests/test_test_suite_runner.py`。

---

## 8. 验证（全部在隔离环境）

1. 后端：上面涉及的测试文件逐个运行，再跑一次 `tools/run_test_suite.py quick`。
2. 前端：`npm run build`（类型检查 + 构建；先确认共享 `frontend/dist` 无人占用）与相关 vitest。
3. 真实浏览器（合成数据、独立端口、独立数据目录，可复用 `tests/api_e2e/harness.py` 的合成考试）：
   - 1440×900 与 1280×800 下四个标签无横向溢出、标题行标签与成绩中心一致。
   - 学生名单：搜索、编辑、删除（学号确认）、导入弹窗写入。
   - 数据与空间：一键备份→下载；恢复需输入、撤销；对合成考试先“释放扫描文件”再“清除原卷”，之后复核页显示「原卷已清理」、改分仍能保存、成绩中心分数与清理前一致、批注原卷导出被拒绝、重新预检被拒绝。
   - 中断恢复：在合成环境制造 `clearing` 回执后界面显示「继续清理」并能完成。
   - 批注图：复核页查看标注图 → 文件出现在沙箱 `cache/annotated_pages/`，`annotated/` 下无新文件。
4. 截图与日志保存在本次 `output/` 目录，回报路径。

---

## 9. 文档（同一提交内改对应处，改写而非追加）

- `docs/maintenance/storage-policy.md`：数据类别表加 `cache/annotated_pages/`（批注图缓存，256 MiB，可再生成，不进备份），“批注与导出”一行改为旧批注图为兼容数据；默认保留规则加“样卷上传不再保存全班 PDF”“原卷清理两档及回执”“清理只在设置页由教师触发”。
- `docs/product/GRADING.md`：“批量保存与确认”第 255 行改为批注图按需生成并缓存；新增“原卷清理”小节（两档、条件、保留与不可用的功能、确认方式、继续清理）；“成绩与导出 / 原卷 PDF”加“原卷已清理的考试不能导出”。
- `ARCHITECTURE.md`：领域代码入口表加 `backend/files/session_originals.py`（原卷状态、清理与空间统计）；说明批注图缓存位置与上限。
- `README.md`：第 12 行「设置页的“AI 服务”」保持；如有“学生管理”入口描述则改为设置页。
- `CONTEXT.md`：增加术语「释放扫描文件」「清除原卷」「原卷已清理」各一行。
- `docs/testing/README.md`：「学生管理」专项入口说明改为“设置页学生名单”。
- `docs/ui/STYLE.md`：如本次确立“页面级视图标签放标题行右侧”，在“导航与快捷操作”写一句现行规则。
- 运行 `tools/check_documentation.py`。

---

## 10. 不在本次范围

- 题库管理、组卷工作台的标签位置（属于题库改版任务）。
- 旧备份、`databases/` 中的预演数据库副本、`reports/`、`outputs/` 的清理（空间页只展示大小）。
- 自动/定时清理与提醒。
- 数据库迁移、迁移/数据包导入导出在设置页的入口。

## 11. 交付回报格式

按 AGENTS.md「收尾」：三行（验证 / 测试 / 文档）+ 未验证项清单 + 截图路径 + 6.4 待验证假设的结论。提交按任务拆分，不混入他人改动；不 push。
