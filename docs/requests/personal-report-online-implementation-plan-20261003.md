# 个人报告在线查看与批量导出实施方案（2026-10-03）

用途：交给实施代理的完整说明。用户已认可原型（`docs/requests/personal-report-center-prototype-20261003/`，浏览器直接打开 `index.html`）。本文写明数据规则、后端与前端改动、测试和文档更新；原型与本文冲突时以本文为准（差异见第 7 节）。

状态：已实施。原型目录按用户要求保留。真实数据验收使用只读查看和数据版报告；真实模型生成未调用。生成、缓存状态变化及导出流程使用合成数据和模拟模型验证。

实施前必读：`AGENTS.md`（授权、真实模型调用需单独授权、收尾三行、单次写入 ≤15KB）、`docs/product/GRADING.md` 第 386–438 行、`docs/maintenance/storage-policy.md` 第 21–33 行、`docs/security/SECURITY.md`、`ARCHITECTURE.md`。

## 1. 已确认的决定

1. 不新开页签。个人报告并入成绩中心现有的“成绩明细”和右上角“导出”。
2. 成绩明细现有行为全部保留：点姓名打开学生详情抽屉；点分数格、点抽屉里的题目进入现有成绩复核页（`navigateToReview`）。这两处代码不改。
3. 姓名后加报告状态图标，点图标一步打开该生本场报告（全屏阅读）。抽屉顶部也有“个人报告”入口。
4. 全屏阅读：可切换该生本学期各场考试；“上一位 / 下一位”按成绩明细当前排序和筛选；Esc 返回并打开最后查看学生的抽屉。报告中可“去复核这题”，复核后返回该报告。
5. 一名学生一场考试一份报告；复用已有 AI 叙述，查看不调用模型。
6. 是否“需重新生成”只看本人变化（用户选定）：只有该生本人的成绩、批改明细、教师最终分锁或批语，以及本场关联的题库资料变化才标记；别的学生改分不影响他。报告中的班级平均、名次等数据部分每次打开都按当前成绩计算，只有 AI 文字保持生成时的内容。
7. 在线查看的作答图直接从本机原卷裁切后返回，不另存副本。
8. 批量导出“方式 A”：导出弹窗里选学生范围（全部 / 按班级 / 指定学生）、考试（可多选，默认本场）；格式为每人一份自包含 HTML（离线可开，图片写入文件）打包 ZIP。合并 PDF 不在本次范围。
9. 导出只使用已生成的叙述，不调用模型；没有叙述的学生不导出，列入清单。

## 2. 现状（已查证）

- 生成与导出：`backend/reporting/analysis_report_exporter.py::AnalysisReportExporter.export_session(session_id, report_type, *, score_revision, student_ids=None, html_only=False)` → `_export_personal`。每生一个模型请求（最多并发 3），先整理错因（`backend/class_analysis.py::run_cause_analysis`，整场逐题、已整理不重复调用），再渲染 `_render_personal_html` 并打包 ZIP。`student_ids` 只在导出器内部可用，API 和任务没有暴露。
- 叙述缓存：`AnalysisNarrativeCache`，文件在 `user_data/reports/.analysis_narrative_cache/<sha256>.json`，键 = `(session_id, score_revision, rendition_version, "personal:{student_id}")` 的哈希。`backend/report_exports.py::score_revision` 是整场哈希（全场成绩、明细、教师锁及关联题库资料），所以任何一人改分都会让全场缓存失配。没有按学生的索引，无法找到“上一次生成”的叙述。
- 图片：`capture_lost_question_shots` 读取原卷正反面并按题框裁切，编码为 base64 写进 HTML（`_render_personal_html` 第 2066、2707 行使用 `shot["data_uri"]`）；题库题图在第 2131 行同样内嵌。公式资源由 `_report_math_assets()` 内嵌。
- 任务与下载：`POST /api/sessions/{id}/reports/export`（`backend/api/routers/reports.py`）→ `submit_report_export`（按报告类型与整场修订号去重）→ `backend/jobs/default_handlers.py` 的 `report_export` 处理；`backend/files/service.py` 中 `personal_analysis_html` 属于 `RETAINED_REPORT_TYPES`，下载后不删除。错题本任务（`backend/jobs/wrong_question_export.py`，`consume_after_download=True`）是“只读生成 → 打包 → 下载后删除”的现成样板。
- 可复用的在线查看样板：班级报告 `GET /api/sessions/{id}/class-analysis/report` 返回 HTML，`ClassReportView.vue` 用 iframe 内嵌。
- 原卷状态：`originals_state(data_root, session_id)` 为 `clearing`/`cleared` 时现有接口不再提供裁切图（如 `backend/api/routers/students.py` 第 374 行）。
- 成绩明细：`ResultsCenterView.vue` 第 1006–1047 行矩阵、第 1068–1142 行学生抽屉；导出弹层是嵌入的 `FileCenterView.vue`（`variant="popover"`），个人报告行目前执行“预估确认 → 生成并打包 ZIP”。

## 3. 规则与口径

### 3.1 报告状态（每名学生 × 每场考试）

| 状态 | 判定 | 界面 |
|---|---|---|
| 已生成 `current` | 存在与该生当前“本人输入修订号”匹配的叙述（新键），或兼容输入：存在与当前整场修订号匹配的旧键叙述 | ✓ 绿 |
| 需重新生成 `stale` | 无当前叙述，但个人索引里有该生本场较早生成的叙述且缓存文件仍在 | ↻ 琥珀；查看时显示上次的 AI 文字并加提示条 |
| 未生成 `missing` | 该生在导出器的学生集合内，但没有任何可用叙述 | ○ 灰 |
| 不可生成 `unavailable` | 该生在本场 `data.skipped` 中（缺考、无有效成绩等），附原因 | “缺考”或原因文字，不可点 |

学生集合必须与导出器一致：取 `assemble_session_analysis(...)` 的 `students`（含纯人工成绩学生）与 `skipped`。

### 3.2 本人输入修订号

新增函数 `student_report_revision(repositories, session_id, student_id) -> str`（放在 3.4 所述新模块），对以下内容做与 `score_revision` 同样的规范化 JSON + sha256：

- 该生本场 `session_results` 行与其 `get_result_details` 全部明细；
- `list_teacher_score_locks(session_id)` 中该生的条目（含修订号与批语）；
- `_question_bank_report_source(...)`（本场关联题库资料，整场共用，变化时全场重新标记，符合现有“题库资料变化使旧叙述失效”规则）；
- `report_narrative_version("personal_analysis_html")`。

不包含其他学生的成绩和班级统计。缓存键改为 `AnalysisNarrativeCache.cache_key(session_id=..., score_revision="student:" + student_revision, rendition_version=..., report_key="personal:{id}")`。查找时先查新键，再查旧整场键（兼容输入，只读，不迁移、不改写旧文件）。

### 3.3 个人叙述索引

新文件 `user_data/reports/.analysis_narrative_cache/personal_index/session_{id}.json`：`{"version": 1, "students": {"<student_id>": {"cache_key", "student_revision", "narrative_version", "generated_at"}}}`，每生只保留最近一次。只在生成任务的调用线程（`_export_personal` 等待结果的循环里，叙述写入缓存之后）用原子替换写入（同目录临时文件 + `replace`，与 `AnalysisNarrativeCache.store` 相同做法，不引用题库模块的工具），查看请求不写。索引缺失或损坏时按“没有较早叙述”处理（已生成判断不依赖索引）。

### 3.4 新模块位置

新建 `backend/personal_reports.py`，承担：本人修订号、索引读写、状态计算、单生在线渲染上下文缓存。不继续加大 `backend/reporting/analysis_report_exporter.py`（已 3555 行）；导出器只做必要的拆分（4.2）。属于考试阅卷模块，不读写训练或题库写入路径。

### 3.5 在线与导出的区别

| 项 | 在线查看 | 导出 HTML |
|---|---|---|
| 作答图 | `<img src="/api/.../shots/{key}">`，请求时从原卷裁切 | base64 写入文件（现状） |
| 题库题图、公式资源 | 沿用现有内嵌方式 | 现状 |
| “去复核这题” | 显示（仅本场为成绩中心当前考试时） | 不显示 |
| 建议核对清单写入 | 不写 | 仅生成任务写（现状） |
| 叙述 | 当前或上次（stale）；`missing` 时可选数据版 | 当前或上次；`missing` 不导出 |

原卷已释放（`clearing`/`cleared`）时在线不输出图片地址，在作答图位置显示“原卷已释放，无法显示作答图；分数与批语不受影响”；导出同样不含图片并显示这句。

## 4. 后端改动

### 4.1 复用与新建

| 需要 | 复用候选 | 结论 |
|---|---|---|
| 在线 HTML | 班级报告 HTML 接口 + iframe | 照此新建个人报告 HTML 接口；渲染复用 `_render_personal_html`，不另写模板 |
| 作答图 | `/results/{rid}/details/{did}/crop`、复核预检裁切 | 不能直接用：报告按“大题题框”裁切且纯人工成绩学生没有 `result_id`；新建只读裁切接口，裁切与区域查找复用导出器现有函数 |
| 生成 | `report_export` 任务 | 复用，增加 `student_ids` 与 `publish` 两个可选参数 |
| 批量导出 | `report_export`（会调模型、整场一包、留存） / 错题本任务 | 新任务类型 `personal_report_bundle`，照错题本样板：只读、多场多人、下载后删除 |
| 状态 | 无 | 新建只读接口 |

### 4.2 导出器拆分（`backend/reporting/analysis_report_exporter.py`）

1. 把 `capture_lost_question_shots` 拆成 `lost_question_shot_specs(...)`（返回 `[{key, parent_question_id, region_question_id, caption, page, region, image_path}]`，不读像素）和现有的编码函数。导出继续调用编码（含 12 MiB 预算）；在线调用规格并生成 URL。键格式沿用 `parent` / `parent:index`。
2. `_render_personal_html` 中作答图改读 `shot.get("src") or shot["data_uri"]`；新增关键字参数 `online: bool = False` 与 `review_links: bool = False`。`online=True` 时 `<img>` 不加懒加载（保证打印时已加载），正文通过同源消息传递 Esc 和左右翻页按键；外层校验消息来源窗口与来源地址。`review_links=True` 时在答题一览展开详情和失分题附录每题旁输出 `<a href="#" class="review-link" data-question-id="Q5">去复核这题 ›</a>`，并附一小段脚本：点击后 `window.parent.postMessage({type: 'personal-report:open-review', questionId}, location.origin)`。
3. `_export_personal` 增加 `narrative_mode: Literal['generate', 'cache_only'] = 'generate'`。`cache_only` 不初始化模型客户端、不整理错因、不写建议核对；只取当前或上次叙述，二者都没有的学生跳过并记入清单。
4. 叙述读写改用 3.2 的新键；写入后在调用线程更新 3.3 索引。`build_analysis_preflight` 同样按新键（含兼容旧键）计算命中，并增加 `student_ids` 过滤。

### 4.3 新接口（`backend/api/routers/reports.py`）

均为只读，除 4.4 外不写任何文件或数据库：

1. `GET /api/sessions/{session_id}/personal-reports` → `{session_id, students: [{student_id, status, generated_at, reason}]}`。
2. `GET /api/sessions/{session_id}/personal-reports/{student_id}/html?narrative=auto|none&review_links=0|1` → `HTMLResponse`。`auto`：当前或上次叙述；`missing` 时返回 409 `personal_report_missing`；`none`：数据版（无 AI 叙述，沿用现有“无 AI 叙述版”渲染）。不可生成学生返回 409 `personal_report_unavailable`（带原因）。响应头 `Cache-Control: no-store`。
3. `GET /api/sessions/{session_id}/personal-reports/{student_id}/shots/{shot_key}` → `image/jpeg`。重新计算该生规格并按键查找，用现有裁切参数（边距 12、最大宽 1600、JPEG 85）。原卷已释放返回 410；键不存在 404；路径必须经 `resolve_stored_file_path` 且位于受控数据根下。
4. `GET /api/students/{student_id}/personal-reports?curriculum_volume_id=` → 该生本学期各场 `{session_id, session_name, graded_at, score, max_score, status}`，按 `graded_at` 排序；场次筛选沿用 `_load_student_histories` 的 `curriculum_volume_id` 规则（含晚于当前考试的场次）。
5. `GET /api/sessions/{id}/reports/analysis-preflight` 增加可选查询参数 `student_ids`（逗号分隔）。

单生渲染需要整场装配（`assemble_session_analysis`、`enrich_personal_questions`、`_load_student_histories`、错因状态）。`backend/personal_reports.py` 用进程内 LRU（最多 2 场）缓存整场上下文。缓存按阅卷与题库数据库提交版本、考试配置、原卷状态、错因文件状态和日期失效；重建时计算整场修订号。掌握与步骤资料的底层读取覆盖整个学期，因此 `enrich_personal_knowledge` 在整场上下文中只准备一次。翻页只复制所看学生，其他学生仅参与已有匿名统计。学生场次列表同样缓存最多两个学期的只读摘要。作答图裁切在内存复用最多两页解码结果，总量不超过 128 MiB；每次请求仍核验路径及原卷状态，不保存裁切副本。

### 4.4 生成与导出任务

- 生成：`ReportExportRequest` 增加 `student_ids: list[int] | None` 与 `publish: bool = True`（只对 `personal_analysis_html` 有效）。`submit_report_export` 把排序后的 `student_ids` 和 `publish` 计入去重指纹。`publish=False` 时处理函数用 `html_only` 输出到暂存目录后删除暂存，任务结果写 `{generated, failed, skipped}` 人数，不带 `file_path`；文件列表和下载入口忽略这类任务。新界面的“生成（N 人）”“生成该生报告”“重新生成”都用 `publish=False`。错因整理仍按现有整场规则执行，预估弹窗如实显示其调用次数。
- 批量导出：新文件 `backend/jobs/personal_report_bundle.py`，任务类型 `personal_report_bundle`，载荷 `{session_ids, student_ids, scope_label}`。逐场调用导出器 `narrative_mode='cache_only'`、`html_only=True`，结构：`班级/学号_姓名_考试名_个人报告.html`，根目录附 `未导出清单.txt`（学生、考试、原因：未生成 / 缺考 / 渲染失败）。只有一人一场时直接给单个 HTML。命名：单场 `考试名_个人报告_{范围}.zip`，多场 `{册名}_个人报告_{N}场_{M}人.zip`，范围为“全部_88人”“9班_45人”“指定12人”。产物放在 `reports_dir/personal_report_bundles/job-N/`，`JOB_FILE_RULES` 新增该类型（`.html`、`.zip`，`consume_after_download=True`）。单人失败不影响其他人；可取消；进度按人次汇报。
- 提交接口：`POST /api/personal-reports/bundles`（body 同载荷，`student_ids` 1–500，`session_ids` 1–20），返回 `JobResponse`；同载荷未完成任务复用。任务中心名称在 `frontend/src/components/shell/task-center-format.ts` 增加“导出学生个人报告”。

### 4.5 不改变

分数、名次、教师最终分锁、复核状态、错因整理规则、训练证据；已导出的历史 ZIP 与旧缓存文件原样保留，旧 ZIP 仍可下载与删除。

## 5. 前端改动

### 5.1 请求与状态

- 新建 `frontend/src/api/personal-reports.ts`：4.3 的状态、学生场次、批量导出提交接口，带与现有 `api/exports.ts` 相同风格的响应校验；HTML 与图片地址只拼字符串，不经 JSON 客户端。生成仍走 `api/exports.ts` 现有提交函数（加 `student_ids`、`publish`）。
- 状态随成绩中心当前考试读取；成绩刷新、相关生成任务结束（沿用 `ResultsOverviewPanel.vue` 第 138 行“跟踪中的导出任务结束时刷新一次”的做法）时重新读取。

### 5.2 成绩明细（`ResultsCenterView.vue`）

- 身份单元格：姓名按钮不变；其后新增独立的状态按钮（✓ / ↻ / ○；不可生成显示“缺考”或原因的短文字，不可点）。点击区域至少 24×24px，`aria-label` 如“查看储雨泽的个人报告（已生成）”，`title` 同文。
- 矩阵上方一行灰字：“点姓名看成绩详情 · 点 ✓ 直接打开个人报告 · 点分数进入成绩复核”。
- 筛选栏在“只看待处理学生”旁新增“报告未生成或需重新生成（N）”，与现有筛选同为单选切换，N 按当前班级范围计数。
- 学生抽屉：在总分与对比行之后插入“个人报告”块：已生成 → “查看个人报告”；需重新生成 → 说明 + “查看个人报告（上次生成）”；未生成 → “本场报告未生成” + “先看数据版”；不可生成 → 原因文字。抽屉原有逐题按钮与 `navigateToReview` 不改。
- 分数格按钮、`navigateToReview`、矩阵排序与现有筛选的行为不改。

### 5.3 全屏阅读（新组件 `components/results-center/PersonalReportReader.vue`）

- `Teleport` 到 body 的全屏层，结构参照 `PaperWalkthrough.vue` 的全屏方式。顶栏：“← 返回成绩明细（Esc）”、姓名 / 学号 / 班级、“第 n / m 人（按当前排序和筛选）”、上一位 / 下一位（←/→，焦点在输入框时不响应）；右侧“导出本场 HTML”“打印”，需重新生成时加“重新生成（1 次调用）”。
- 考试切换条：4.3 第 4 项返回的各场，显示分数与状态；默认成绩中心当前考试。翻到下一位时保持所选考试；该生在这场没有报告时显示对应空状态。
- 正文：iframe，`src` 为 4.3 第 2 项地址；`review_links=1` 只在所看考试等于成绩中心当前考试时传。状态分流：
  - 已生成：正文。
  - 需重新生成：顶栏下黄条“成绩已变化，显示的是上次生成的 AI 分析；分数和班级数据为当前值”。
  - 未生成：空状态“本场报告尚未生成”，按钮“生成该生报告（预估后确认）”与“先看数据版”（`narrative=none`）。
  - 不可生成：显示原因。
- 生成与重新生成：先调预估接口（`student_ids=[该生]`），用现有 `ClassAnalysisGenerateConfirm.vue`/FileCenter 的确认组件显示错因整理与叙述调用次数，确认后提交 `publish=False` 任务；任务结束后刷新状态和 iframe（地址带 `v=generated_at` 强制重载，同班级报告）。
- “去复核这题”：父页面监听 `message`，校验 `event.origin === location.origin` 且 `event.source === iframe.contentWindow`；按 `questionId` 在当前学生的成绩项中找精确匹配，找不到再按大题号取第一项；找到后在 `stores/results-center.ts` 写入 `reportReturn = {sessionId, studentId, reportSessionId}`，再调用现有 `navigateToReview`。成绩中心挂载或从复核页返回时发现 `reportReturn` 且考试一致，就重新打开阅读器并清除该值；有叙述时显示当前或上次叙述，没有叙述时直接恢复数据版正文。
- 打印：`iframe.contentWindow.print()`，使用报告自带打印样式。导出本场 HTML：提交一人一场的 `personal_report_bundle`。
- Esc 或返回：关闭阅读器，打开最后查看学生的抽屉并高亮其行；焦点回到触发按钮或抽屉关闭按钮。

### 5.4 导出弹层（`FileCenterView.vue` 个人报告行）

- 显示“本场：已生成 a · 需重新生成 b · 未生成 c · 不可生成 d（人）”。
- “生成（b + c 人）”：预估（`student_ids` 为这些人）→ 现有确认组件 → `publish=False` 任务；行内显示进度。人数为 0 时不显示按钮。
- “导出…”：打开新组件 `components/results-center/PersonalReportExportDialog.vue`（方式 A，按原型）：学生范围单选（全部 / 按班级多选 / 指定学生：搜索复用成绩明细的姓名、学号、班级与拼音首字母匹配函数，按班分组勾选、全选本班、已选标签）；考试多选（本学期各场，默认本场，各自显示可导出人数）；格式只显示 ZIP（PDF 不显示）；实时汇总“将导出 N 人 × E 场，其中 k 份未生成或不可生成，不导出，会列入清单”及文件名预览；确认后提交批量导出任务并提示“已加入任务中心，完成后在任务中心下载”。各场可导出人数由 4.3 第 1 项按场读取。
- 原“生成并下载 ZIP”入口移除；已有留存 ZIP 的历史记录照旧显示、下载与删除。

## 6. 安全与存储

- 新接口只返回本机受控路径下的内容，不在响应、普通日志或任务结果中写出文件系统路径；任务结果只写人数、文件名和清单条目（学生姓名、考试名、原因），与错题本一致。
- 在线查看不新增任何持久文件；新增的持久文件只有 3.3 索引（每场一份、每生一条，体积很小）和批量导出的临时 ZIP（下载后删除）。在 `docs/maintenance/storage-policy.md` 写明。
- 真实模型调用：开发与测试使用模拟模型客户端；用真实数据只读打开阅读器、查看状态属于允许操作；在真实数据上点“生成”会产生费用，必须先获得用户针对该次的授权。

## 7. 与原型的差异（实施以本文为准）

| 位置 | 原型 | 实施 | 原因 |
|---|---|---|---|
| 阅读器顶栏走势图 | 有 | 去掉 | 现有报告正文已有“历次成绩”走势图，避免重复 |
| 报告正文 | 页面内模拟版式 | iframe 加载真实报告 HTML | 在线与导出共用一个渲染器 |
| “去复核这题” | 任何考试都有 | 只在所看考试为成绩中心当前考试时显示 | 复核与返回都基于当前考试 |
| 合并 PDF 选项 | 灰色“待评估” | 不显示 | 不在本次范围 |
| 状态图标 | 很小 | 点击区域 ≥24px | 便于点击 |
| “只看待处理学生”含义 | 原型自定 | 沿用现有规则 | — |
| 生成确认组件 | 原型自绘 | 复用 `components/file-center/AnalysisConfirmDialog.vue` | 已有费用说明与测试 |

## 8. 测试

按 `AGENTS.md` 顺序优先扩展现有测试；全部使用合成数据与模拟模型客户端，不调用真实模型。

- 后端：
  - `tests/test_analysis_report.py`：扩展第 501 行附近的缓存用例——另一名学生改分后，本人仍命中（状态 `current`）；本人改分、教师锁或题库来源变化后变为 `stale`，在线 HTML 使用上次叙述；旧整场键缓存作为兼容输入命中且不被改写；`cache_only` 不创建模型客户端、缺叙述学生进入清单；在线渲染不含 `data:image/jpeg`、含 shots 地址，导出渲染仍内嵌图片；`review_links` 只在要求时输出；原卷释放时两种渲染都显示提示句。
  - `tests/test_api_report_jobs.py`：`student_ids`、`publish` 进入去重指纹；`publish=False` 任务结果无 `file_path`；预估接口按 `student_ids` 过滤计数。
  - `tests/test_report_export_job.py`：新增批量导出处理函数用例（同文件）：多场多人目录与命名、单人单场直接给 HTML、清单原因、取消不发布、单人渲染失败不影响他人。
  - 新接口：状态、HTML（含 409 两种）、shots（200 / 404 / 410 / 越界路径拒绝）、学生场次，及 `JOB_FILE_RULES` 新类型下载后删除，都放入 `tests/test_api_report_jobs.py`。
  - 索引：并发生成时只在调用线程写；索引损坏按无旧叙述处理。
- 前端（vitest）：
  - `src/__tests__/results-center-view.spec.ts`：状态图标与 `aria-label`；点图标打开阅读器、点姓名仍开抽屉、点分数格仍调用复核跳转（保留现有断言）；新筛选与计数；抽屉“个人报告”块四种状态；阅读器上一位 / 下一位顺序跟随筛选、Esc 返回抽屉；`message` 来源校验与 `reportReturn` 恢复。
  - `src/__tests__/file-center-view.spec.ts`：个人报告行计数、生成（预估 → 确认 → `publish=False`、取消不发请求）、导出弹窗三种范围、多场汇总、文件名预览与提交载荷；移除原“生成并下载 ZIP”断言，保留历史 ZIP 的下载与删除断言。
  - `src/components/shell/__tests__/app-shell.spec.ts`：新任务类型名称。
- 验证命令：`npm run build`；上述 vitest 文件；`runtime/python/python.exe -m pytest tests/test_analysis_report.py tests/test_api_report_jobs.py tests/test_report_export_job.py tests/test_controlled_file_access.py tests/test_personal_report_design.py tests/test_report_print_layout.py`。重建共享 `frontend/dist` 前先确认没有其他任务在用。

## 9. 文档更新（同一提交）

- `docs/product/GRADING.md`：
  - 第 390 行：改写个人报告的入口（成绩明细状态图标、抽屉、全屏阅读）、生成（可按学生）和批量导出（方式 A、只用已生成叙述、下载后删除），删除“系统下载导出为 ZIP”的旧说法；
  - 第 405、420 行：缓存与“需重新生成”改为按本人输入判断，班级数据实时、AI 文字保持生成时内容；兼容读取旧整场缓存；
  - 第 434–437 行附近：在线查看图片按需从原卷读取、不另存；原卷释放后的提示句；“去复核这题”及返回。
- `docs/maintenance/storage-policy.md`：个人叙述索引位置与体积；批量导出 ZIP 下载后删除；旧留存 ZIP 规则不变。
- `ARCHITECTURE.md`：`backend/personal_reports.py` 的职责、在线与导出共用渲染器、整场上下文 LRU。
- `CONTEXT.md`：如“本人输入修订号”“需重新生成”成为固定用语，补词条。
- `docs/testing/README.md`：如测试分组变化，改对应句子。
- 本文件：实施完成后改写开头“状态”一句；原型目录是否保留由用户决定。

## 10. 验收

- 合成环境：生成 3 名学生 → 改其中 1 人分数 → 只有这 1 人显示“需重新生成”，另 2 人仍为已生成；阅读器翻页、切换考试、去复核并返回、打印预览、单人导出、多场批量导出（检查 ZIP 结构与清单）。
- 真实数据只读：打开成绩明细与阅读器，记录首次打开一份报告与翻到下一位的耗时（目标：下一位 ≤1 秒；首次超过 5 秒时先报告），确认响应中没有内嵌作答图、浏览器里图片正常；不点生成。
- 前后对比：同一学生同一场，在线 HTML 与导出 HTML 除图片引用方式、复核链接和在线键盘消息脚本外内容一致（用脚本去掉这些差异后比较）。

## 11. 不在本次范围

- 合并 PDF、按家长发送、手机端专门版式。
- 学期综合报告（跨场 AI 总结）。
- 报告正文内容与提示词的改动。
- 知识与训练页改版（见 `knowledge-training-redesign-implementation-plan-20261003.md`）。
