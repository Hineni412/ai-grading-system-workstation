# P2-12 报告、原卷与文件下载即时实现计划

**执行包：** P2-12
**用户自测：** quick
**自测清单：** docs/user-testing/checkpoints/P2-12-v1.5.0-files-quick-check.md
**交接基线：** 56851637a82da115dd557d2dae60c237f8962655

## Objective

在不改变成绩报表口径、批注原卷内容、训练材料内容、评分语义或生产入口的前提下，交付独立的 Vue“文件中心”：教师可以针对当前考试生成或复用最新成绩版本的 Excel 成绩报表和批注原卷 PDF，也可以从既有训练任务生成 DOCX、Markdown 或整任务 ZIP；所有任务在刷新后可恢复，成功文件可安全下载，过期、缺失、失败和取消状态都有明确恢复动作。

## Frozen Boundary

- **包含：** 报告导出上下文与任务历史；Excel/批注原卷 PDF 格式选择；当前成绩版本缓存复用；显式重新生成；Training task/variant 选择与既有 DOCX/Markdown/ZIP 导出；任务轮询、取消、失败重试、下载、过期文件恢复；中文文件名；Vue 路由、工作台入口、五档桌面视口和 quick 用户短测。
- **明确不包含：** 增量 Excel 导出；新增或修改报表字段、统计公式、评分规则、批注内容、训练材料内容或训练推荐语义；学生名单管理；报告页评分调整；通用文件浏览；生产入口切换；数据库迁移；真实模型调用；真实业务数据写入。
- **验收条件：** Excel、批注原卷 PDF、训练材料三类文件形成“发起任务→刷新恢复→成功下载”闭环；同一成绩版本默认复用有效缓存，显式重导才产生新文件；成绩变化后旧报告在页面标记为过期且引导重导；缺失/删除文件返回稳定恢复提示；中文文件名可下载；公开响应、错误和前端持久化均不包含绝对路径；五档桌面视口无意外溢出；受影响自动测试、真实匿名浏览器、快速冒烟、交接验证和真实两库指纹守卫通过。
- **风险等级：** 中高。范围不改变数据库 Schema 或报告口径，但涉及后台任务、并发缓存、文件原子发布、受控下载、失败重试和多个前后端模块，必须完成需求符合性与代码质量双路复审。

## Root-Cause Inventory

开工集中调查把当前缺口冻结为四个根因组；后续不扩大为整个项目的开放式扫描。

| 根因组 | 调查证据与当前缺口 | 本包统一处理 |
|---|---|---|
| A. 报告任务只覆盖 Excel | `POST /api/sessions/{id}/reports/export` 固定提交 `report_export`；`OriginalPaperExporter` 仅由 Streamlit 同步调用；下载规则只允许报告 `.xlsx` | 保留同一专用报告入口并增加受控报告类型，Job handler 在独立暂存目录生成 Excel 或 PDF，发布前复核取消状态，下载规则仅增加 PDF |
| B. 缓存仍是 Streamlit 页面内存 | 旧 UI 以 `session + export_type + score_revision` 复用缓存；刷新、浏览器关闭和 API 路径无法恢复；当前报告 API每次都新建任务 | 把成绩 revision 计算下沉为共享公开服务；专用提交在同一 session/type/revision 下默认复用有效成功任务，显式重导才新建；页面把旧 revision 标为需重导 |
| C. 导出历史只有通用 Job 摘要 | 通用 `/api/jobs` 可按 session/type 分页，但摘要没有安全 payload/result；前端 Job Store 只恢复浏览器主动跟踪的 ID | 文件中心使用通用列表发现任务，再按 ID 读取已脱敏详情；当前页面请求世代隔离跨考试旧响应；创建或重试后的任务继续交给既有 Job Store 轮询 |
| D. Training 导出已有后端闭环但无 Vue 入口 | Training task 分页/详情、专用导出、失败重试和受控下载均已存在；Vue 尚无任务选择、格式选择、任务历史或下载入口 | 仅包装既有契约，不新增训练语义；支持整任务 ZIP，或指定 variant 的 DOCX/Markdown 与学生/教师版本；失败仅沿用既有 retry API |

## Fixed Failure Matrix

| 场景 | 预期结果 |
|---|---|
| 重复操作 | 同一 session、报告类型和成绩 revision 的普通生成复用一个仍有效的成功或进行中任务；只有“重新生成”显式创建新任务；Training 重试使用既有 `retry_of_job_id` |
| 同时操作 | 报告缓存查找与提交在进程内按 session/type 串行，普通双击只有一个赢家；两个显式重导可各自产生 job-owned 文件且互不覆盖 |
| 中途退出 | 文件只从 Job 暂存目录原子发布；退出前的半成品由暂存目录清理，不出现在下载历史 |
| 重新启动 | 已发布成功任务从 jobs 表和受控目录恢复；运行中任务按既有规则转 failed；页面重新查询服务端历史，不依赖单个浏览器 |
| 失败重试 | Excel/PDF 失败重新提交原报告类型并重新读取当前成绩 revision；Training 只允许既有 failed job 专用重试；写请求不自动重放 |
| 取消 | queued 可立即取消；running 在最终发布前协作式确认取消；发布边界后的正常成功允许赢得竞态；已发布文件不因迟到取消被删除 |
| 部分完成 | 多页批注 PDF 的中间图片和未完成 PDF 都留在 job 暂存目录，不公开；Training bundle 沿用现有整包原子发布 |
| 数据缺失 | 无考试、无批改结果、训练任务/variant 不存在、输出文件被清理分别返回稳定 404/409/410/422；页面保留已有历史并显示可执行恢复动作 |
| 数据冲突 | 当前成绩 revision 与任务 revision 不一致时旧报告标记“成绩已变化，需重新生成”，不作为最新下载入口；跨 session/任务旧响应不得覆盖当前页面 |

## Public Test Seams

用户启动正式执行包已确认采用包定义、现有业务事实和以下公开 seam；测试不耦合私有实现：

1. **HTTP/API seam：** 报告上下文、Excel/PDF 专用提交、缓存复用与显式重导、通用 Job 列表/详情/取消、Training task/variant 导出与失败重试、受控文件下载；验证错误码、中文文件名、无路径泄露和原子发布。
2. **Vue user-flow seam：** 当前考试报告类型选择、训练任务选择、任务登记簿、刷新恢复、跨考试旧响应隔离、缓存/过期/失败/取消/缺失状态及下载动作。
3. **真实浏览器 + 临时服务 seam：** 固定匿名考试、生成 XLSX/PDF/Markdown/ZIP、删除一个临时输出模拟过期、刷新与五档 Windows 桌面视口；所有写入只发生在临时数据库和临时数据根。

数据库仅用于测试准备和核对成绩 revision/文件安全不变量；不把私有 SQL 形状写成页面行为断言。

## Business Capability and UX Traceability

| 用户任务或业务能力 | 现有业务来源 | 必须保持的语义、结果与安全边界 | 新 Vue 呈现与操作设计 | UX 调整理由 | 业务差异及用户决定 | 验证方式 |
|---|---|---|---|---|---|---|
| 生成 Excel 成绩报表 | `ReportGenerator.export_session()`、旧 Streamlit `_render_report_export_controls()`、report Job/API 与测试 | 每次生成读取已保存成绩；文件为 XLSX；不改变字段和统计口径；客户端不提交路径 | 当前考试“成绩报表”出件行，显示当前/过期状态、普通生成和显式重导 | 把同步按钮改成可恢复任务，且明确当前成绩版本 | 无业务差异 | API 结果对照、XLSX E2E、浏览器 |
| 生成全体批注原卷 PDF | `OriginalPaperExporter.export_session_originals()`、旧 Streamlit 同名按钮与测试 | 使用现有批注渲染和已保存结果；无结果时拒绝；只发布 PDF | 与 Excel 并列但明确标记“批注原卷 PDF”，不混称成绩报表 | 教师可在同一出件台选择用途，避免文件类型含混 | 无业务差异 | PDF 页/文件检查、错误契约、浏览器 |
| 缓存与重新导出 | 旧 Streamlit `session + export_type + score_revision` 缓存键逻辑 | 同一成绩版本可复用；评分调整后旧缓存不能冒充最新 | 最新有效文件直接下载；成绩变化显示“需重新生成”；“重新生成”是独立次要动作 | 让刷新后仍能理解文件是否最新，减少重复生成 | 无业务差异 | revision 契约、重复/并发测试 |
| 查看任务与恢复 | JobManager/JobStore、`GET /api/jobs`、Job 公开脱敏规则 | 服务端状态为真相；重启中断为 failed；写请求不自动重放；路径和内部错误不公开 | 连续“出件登记簿”按新到旧显示类型、范围、状态、时间和唯一当前动作 | 登记簿符合教师查找已出文件的真实任务，避免卡片海洋 | 无业务差异 | API 分页/解码、store 与刷新测试 |
| 下载或处理过期文件 | `GET /api/jobs/{id}/download`、`JobFileService` 与 403/409/410/415 测试 | 只允许受控根和白名单扩展名；缺失返回 410；`no-store`；不接受任意路径 | 可用时“下载”；过期时说明文件已清理并提供重新生成/重试 | 把技术错误翻译为可执行恢复动作 | 无业务差异 | 下载安全回归、浏览器过期场景 |
| 生成训练材料 | Training task list/detail、P1-20 专用 export/retry Job、TrainingExportService | 只消费服务器 task/variant ID；格式、audience 和 bundle 规则不变；不暴露题库路径 | “训练材料”出件行先选任务，再选整包或 variant、格式和版本；沿用既有失败重试 | 让已有后端能力在统一文件中心可达，不新增训练规划 | 无业务差异 | 既有 Training API 回归、前端契约与浏览器 |

## Frontend Design Direction

- **主题、受众和页面单一任务：** 本地 AI 阅卷系统；使用 Windows 工作机的教师；把已经完成的业务结果可靠地“出件并取走”。
- **色彩：** 只复用现有 Token：纸面白 `#ffffff`、应用灰 `#f6f7f8`、墨色 `#20242a`、强调蓝 `#2563eb`、成功绿 `#2f7a55`、警示褐 `#9a6718`；不新增渐变或品牌色。
- **字体：** 沿用项目 Inter / 苹方 / 微软雅黑正文体系；文件类型、大小和时间使用同一 utility 数字排版，不引入新字体依赖。
- **布局：** 页面上方是克制的当前考试与刷新信息；中段三条真实“出件行”分别对应成绩表、批注原卷和训练材料；下方是贯穿页面的任务登记簿。

```text
┌ 文件中心 ─ 当前考试 ─────────────────── 刷新 ┐
├ 成绩报表 XLSX      [最新状态/文件]      [生成] ┤
├ 批注原卷 PDF       [最新状态/文件]      [生成] ┤
├ 训练材料           [任务/版本/格式选择] [生成] ┤
├──────────────── 出件登记簿 ────────────────┤
│ 类型 │ 范围/文件 │ 状态与进度 │ 时间 │ 当前动作 │
└────────────────────────────────────────────┘
```

- **签名元素：** “出件登记簿”的细横线、文件类型窄签和稳定列对齐，像教务室的出件记录，而不是通用下载卡片。
- **自我复核：** 初稿曾考虑三张大卡片，但那会落入通用 SaaS“功能卡片”模板并浪费 1024px 工作空间；改为三条连续出件行和一张登记簿，只在文件类型窄签上保留辨识度。页面不使用营销 Hero、超大数字、渐变、强阴影或无业务含义动效。

## Planned Interface

- `GET /api/sessions/{session_id}/reports/context`
  - 返回不透明 `score_revision`、是否存在可导出结果，以及当前 session 的安全报告任务历史。
- `POST /api/sessions/{session_id}/reports/export`
  - 可选 JSON：`report_type = score_excel | annotated_original_pdf`、`force_regenerate = false`；无 body 时继续兼容现有 Excel 行为。
  - 普通提交复用同一 report type/revision 的有效 queued/running/succeeded Job；显式重导创建新 Job。
- `report_export` Job payload/result
  - 公开字段仅含 `session_id`、`report_type`、不透明 `score_revision`、安全文件名和受控下载 URL。
- `GET /api/jobs`、`GET /api/jobs/{id}`、`POST /api/jobs/{id}/cancel`
  - 保持通用契约；前端只筛 `report_export`/`training_export`。
- 既有 `GET /api/training/tasks`、`GET /api/training/tasks/{id}`、`POST /api/training/tasks/{id}/exports`、`POST /api/training/exports/jobs/{id}/retry`
  - 不改变业务语义，仅由 Vue 新入口调用。
- `GET /api/jobs/{id}/download`
  - 报告白名单增加 `.pdf`；其他类型和受控根保持不变。

前端和公开响应不得出现 `file_path`、`output_path`、数据库路径、报告暂存目录、题库路径、答卷正文、内部异常或密钥。

## Delivery Slices

### Slice 0：领取与计划

- [x] 首个 first-parent 提交只包含本即时计划和合法 `in_progress` 交接块。
- [x] handoff validator 使用独立可信基线 `56851637a82da115dd557d2dae60c237f8962655` 通过。
- [x] 记录真实两库只读指纹，功能 worktree 的 `user_data/` 状态干净。

### Slice 1：报告类型、revision 缓存与 PDF Job

- [x] RED：Excel 默认兼容；PDF 类型；同 revision 普通重复/并发复用；显式重导；成绩变化不复用；无结果拒绝；取消前不发布。
- [x] GREEN：最小报告上下文/协调服务、请求/响应 schema、路由和 handler 分派；复用 `ReportGenerator` 与 `OriginalPaperExporter`，不改两类导出器内容。
- [x] 下载白名单只增加 PDF；中文文件名与公开结果脱敏测试转绿。

### Slice 2：任务发现、过期恢复与 Training 契约包装

- [x] RED：报告历史按当前 session；Training 历史为全局任务；列表摘要后按 ID 获取安全详情；删除输出后 410；failed/cancelled/重启中断显示正确恢复动作。
- [x] GREEN：前端 `exports` API 解码器、二进制下载与文件名解析；只调用既有 Training task/export/retry 契约。
- [x] 验证旧响应不覆盖新 session，失败刷新保留同一范围最后成功历史。

### Slice 3：Vue 文件中心

- [x] RED：路由/导航/工作台入口、三条出件行、任务登记簿、缓存/过期/失败/取消/空白/加载/禁用/焦点状态。
- [x] GREEN：新增文件中心 view/store/components 与局部 Token 样式；同一操作区只有一个主按钮，显式重导为次要动作。
- [x] 训练材料选择必须来自真实 task/detail；没有任务时只说明如何先建立训练任务，不创建未来占位能力。

### Slice 4：匿名浏览器、文档与交接候选

- [x] 固定匿名临时数据库/输出根贯通 XLSX、PDF、Markdown/ZIP、刷新、取消和过期文件重导。
- [x] 覆盖 1920×1080、1440×900、1366×768、1280×800、1024×768；长中文文件名、100 条历史、加载/空白/错误/禁用/焦点无溢出。
- [x] 更新 `ARCHITECTURE.md` 的 P2-12 增量边界；页面可运行并复审后再生成 quick 自测清单。
- [x] 聚焦测试、受影响回归、`npm run verify`、浏览器 prepared 门槛、`tools/smoke_check.py --skip-tests`、`git diff --check`、handoff validator 和真实两库指纹守卫通过。

## Verification Strategy

- **Python 当前反馈：** 新增 report context/coordinator/router/handler/file tests；既有 `test_api_report_jobs.py`、`test_report_export_job.py`、`test_api_file_downloads.py`、OriginalPaperExporter 和 Training export 聚焦回归。
- **Frontend 当前反馈：** exports API/store/view/component 单元；受影响 navigation/router/session/workbench/job-store 回归；稳定候选统一运行 `npm run verify`。
- **Browser：** 使用固定匿名临时服务；同一已构建 SHA 运行 P2-12 prepared 命令，覆盖三类文件、刷新、过期和五档视口，不读取真实 `user_data/`。
- **里程碑：** 功能分支不运行完整 pytest。P2-12 合入 M2-01 后，在同一稳定 SHA 运行一次串行完整门槛和一次 2 进程对照，再运行组合前端/浏览器门槛和真实两库指纹守卫。

## Review and Fix Policy

稳定候选冻结后使用 `code-review`，让 Spec 与 Standards 两名评审代理检查同一个 SHA。全部意见返回后由主代理统一去重和归因；仅本次修改直接引入或当前任务遗漏的 `Critical`/`Important` 阻塞。若有阻塞问题，只做一次统一修复和受影响复测，再由原评审者进行一次限定最终复审；第二轮仍有 `Critical`/`Important` 时停止并向用户汇报。

## Ownership

P2-12 功能分支预期拥有：

- 报告上下文/协调服务、report schema/router/Job handler 的 P2-12 增量和聚焦测试；
- `JobFileService` 的 PDF 白名单增量；
- 新增 frontend exports API/store/view/components/tests/e2e；
- 最小 navigation/router/workbench 入口；
- 本即时计划、P2-12 架构增量和版本化 quick 自测清单。

integration 负责最终 Index 状态、里程碑组合证据、共享入口冲突和 M2-01 完整门槛；功能分支不宣称自己已进入主线。

## Rollback

功能提交保持单包边界，可整体 revert。新增 Vue 路由未成为生产入口，失败时仍使用 Streamlit 导出页。报告/PDF/Training 输出均可删除重建；回退代码不删除任何既有输出或真实业务数据，不修改数据库 Schema。

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P2-12
**交接状态：** waiting_review
**功能提交：** branch_head
**自动验证：** passed
**独立复审：** pending
**用户验收：** pending
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
