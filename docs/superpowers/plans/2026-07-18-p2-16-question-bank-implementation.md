# P2-16 题库管理即时实现计划

**执行包：** P2-16
**规划状态：** verified_pending_integration
**计划基线：** 89a00416151c7a59d038e7dd6b936c21cd9425a7
**交接基线：** 89a00416151c7a59d038e7dd6b936c21cd9425a7
**用户自测：** quick
**自测清单：** docs/user-testing/checkpoints/P2-16-v1.5.0-question-bank-quick-check.md
**已确认测试缝：** 用户于 2026-07-18 明确启动 P2-16；Phase map、现有 Streamlit 行为、P1-15/P1-16/P1-18 服务与 HTTP 契约及下列 Public Test Seams 共同冻结测试范围，不把私有 SQL、Vue 内部组件层级或旧页面布局作为验收接口。

## Objective

在不修改题库 Schema、活动标签语义、AI prompt/模型策略或生产入口的前提下，交付独立 Vue“题库管理”工作区：教师可以用服务器分页筛选数千道题，查看完整题干、答案、富文本、图片和预览，跨页稳定选择题目，安全提交批量 AI 打标签任务并恢复/取消/重试，下载部分失败清单，以及用 revision 冲突保护确认当前标签。受控 DOCX/PDF 导入形成可恢复 Job；旧 Streamlit 页面继续可用，所有写测试只使用临时题库与假模型。

## Frozen Boundary

- **包含：** 试卷与题目服务器分页；关键词、题号、知识点、题型、试卷、年份、考试类型、年级、考试范围、难度、标签状态和排序筛选；跨页选择；题目详情、答案、富文本块、受控图片与当前预览；受控 DOCX/PDF 多文件导入队列；导入 Job；批量 AI 打标签 Job；进度、取消、刷新恢复、部分失败清单下载与只重试失败项；教师标签整集确认；revision 冲突；单题软删除和当前页面内立即恢复；Vue 路由/导航；五档 Windows 桌面视口；quick 用户短测。
- **明确不包含：** 数据库迁移；真实模型调用；真实题库写入；修改 prompt、模型、API profile、并发或 RPM 配置；旧“知识点整理（高级）”、旧技能目录/概念映射/技能消歧；组卷/试题篮（P2-17）；训练推荐（P2-18）；题目正文/答案编辑；试卷批量删除/恢复；通用本地路径、文件夹扫描或客户端目标路径；生产 UI 切换；删除旧 Streamlit 页面。
- **相邻但不并入：** 旧页面允许逐文件人工改写年份、地区、考试类型等导入元数据，而 P1-18 公开 Job 契约只接收受控文件请求；本包恢复安全文件名的既有自动元数据推断，但不扩展新的元数据写契约。旧页面的题目正文编辑、试卷删除/恢复和高级技能能力同样不在 Phase map 明确目标内，另行记录但不扩大 P2-16。
- **验收条件：** 数千题生成数据下分页与筛选稳定；跨页选择不会因刷新当前页误选/丢选；长公式、图片、富文本和缺失素材均可解释；同范围旧请求不能覆盖新筛选/新详情；标签保存与软删除使用当前 revision，409 不覆盖后来数据且保留教师草稿；导入/打标签任务可刷新恢复、取消和受控重试；打标签只有教师明确确认后才启动，且只提交选中题目；部分失败可下载安全 CSV 并只重试允许的题目；受影响自动测试、匿名真实浏览器、快速冒烟、交接验证和真实两库指纹守卫通过。
- **风险等级：** 高。题库写入会改变批改上下文、知识图谱和训练推荐读取的当前标签；AI 打标签会产生外部费用；导入涉及文件发布、长任务、取消、重试、部分完成和重复来源。形成稳定候选后必须进行需求符合性与代码质量双路复审。

## Root-Cause Inventory

开工集中调查只覆盖 P2-16 及直接影响范围，去重并冻结为六个根因组；修改前问题和相邻需求不自动并入。

| 根因组 | 调查证据与当前缺口 | 本包统一处理 |
|---|---|---|
| A. 公开能力已在后端但 Vue 无入口 | P1-15/P1-16/P1-18 已提供 papers、questions、detail/media、revision 标签写、软删除/恢复、受控上传、导入与 tagging Job；`frontend/` 尚无 question-bank API、store、路由或页面 | 复用现有 HTTP seam，新增严格类型解码、store、页面、导航和受影响测试；不重写题库服务 |
| B. 数千题筛选和跨页选择需要服务器真相 | API 每页最多 100 条并在服务端排序/筛选；旧 Streamlit 以页内卡片和当前页批处理为主 | 使用服务器分页；筛选草稿经“应用筛选”才请求；选择集合以题目 ID 跨页保持，并显式显示隐藏选择数与清空动作 |
| C. 详情与媒体必须保持受控边界 | 详情会返回完整长文本、富文本块、受控 asset/preview URL；媒体可能缺失、过期、类型不支持或源快照繁忙 | 只读取 API 返回的同源受控 URL；详情请求按 generation/AbortController 隔离；素材失败局部恢复，不清空仍可读文字 |
| D. 标签写入会影响多个下游消费者 | `question_tags.knowledge_point` 是活动身份；`PUT tags` 替换整集且以 revision 防并发；409 必须 fail closed | 以当前详情完整标签初始化草稿，保存时提交完整集合；校验类型/非空/长度/置信度；409 保留草稿并要求重新加载，不自动覆盖 |
| E. 导入和打标签是可恢复长任务 | Job Store 只恢复当前浏览器记录的 ID；导入按请求锁与来源指纹幂等，tagging 对重叠题目加锁、只保存 complete、部分失败可重试 | 页面初始化通用 Job Store；持久化本页工作流引用；双击防重复；显示取消的真实语义；导入/打标签分别使用专用 retry 端点 |
| F. 外部费用和部分失败必须可控 | tagging Job 最多 500 题；真实模型调用有费用；结果只公开安全 failure category/message 与题目 ID，当前没有专用失败文件端点 | 启动前显示题数并二次确认，默认不自动打标签；仅失败题重试；浏览器从安全结果生成 UTF-8 CSV 下载，不引入服务器路径或新文件发布语义 |

## Fixed Failure Matrix

| 场景 | 预期结果 |
|---|---|
| 重复操作 | 写按钮在请求中禁用；相同标签重试由后端幂等返回；重复导入仍由来源指纹去重；同一个失败 Job 的重复重试只提交教师本次明确选择的失败题 |
| 同时操作 | 重叠 tagging 由后端稳定锁顺序串行；同题手工标签/删除由 revision 只允许当前版本成功；旧列表、详情和 Job 响应不能覆盖后来范围 |
| 中途退出 | 已提交 Job 留在服务器并由浏览器记录的 Job ID 恢复；未提交筛选和标签草稿只保留当前页面内存，不假装已经保存；已经发布的导入或已保存标签不回滚 |
| 重新启动 | 题库列表、详情和标签从服务器重建；当前浏览器记录的导入/tagging Job 按 ID 恢复；找不到的 Job 显示已无法恢复并移除本地引用 |
| 失败重试 | GET 可按现有 Client 有界重试；所有写请求不自动重放；导入仅在 failed/cancelled/partial retryable 时用专用端点；tagging 只重试服务端允许的失败 ID |
| 取消 | 导入取消不承诺撤销已经完成的文件解析/入库；tagging 保留已安全保存批次，丢弃当前未保存批次并停止后续批次；终态以服务器返回为准 |
| 部分完成 | 导入显示成功题目数和失败文件数；成功题仍可查看；tagging 显示成功/跳过/失败数，失败清单可下载并受控重试，不把 partial 显示为全成功 |
| 数据缺失 | 空题库、题目被删除、详情消失、媒体缺失/过期、导入请求失效和 Job 消失都有稳定提示；不会回退到本机路径或旧详情 |
| 数据冲突 | 标签或删除 409 保留用户输入并明确要求重新加载；重新加载前禁止再次覆盖；筛选返回和请求条件不一致时拒绝写入 store |
| 外部费用 | 页面不自动触发 AI；启动和重试前都显示实际题数并要求确认；取消只停止尚未安全保存的后续工作，不承诺退回已产生费用 |

## Public Test Seams

1. **Question Bank HTTP seam：** 复用临时题库验证 papers、分页 filters、detail/media、标签 revision、软删除/恢复、受控上传、导入/tagging 提交和专用重试；只在需要证明原子/脱敏不变量时检查临时数据库。
2. **Typed frontend API seam：** 通过公开 decoder 与请求函数验证字段白名单、分页 scope、写请求不重放、二进制上传、专用 retry 和路径样字段拒绝；不测试私有 helper。
3. **Pinia question-bank store seam：** 验证筛选 generation、详情隔离、跨页选择、标签草稿冲突、工作流恢复和 Job 投影；loader 使用公开函数签名替换，不 mock 内部组件。
4. **Vue user-flow seam：** 通过真实 DOM 操作筛选、分页、选择、详情、标签确认、导入、AI 费用确认、取消、部分失败下载/重试和软删除/恢复。
5. **匿名真实浏览器 seam：** 固定生成题库、DOCX/PDF、假 tagging 服务与临时数据根，运行真实 FastAPI + Vue，覆盖长公式/图片、数千题、刷新恢复和五档视口；不读取真实数据库或调用真实模型。

## Business Capability and UX Traceability

| 用户任务或业务能力 | 现有业务来源 | 必须保持的语义、结果与安全边界 | 新 Vue 呈现与操作设计 | UX 调整理由 | 业务差异及用户决定 | 验证方式 |
|---|---|---|---|---|---|---|
| 浏览与筛选题目 | `pages/题库管理.py::_question_filter_panel()`、`QuestionBankReadService.list_questions()`、API 组合筛选测试 | 只显示活动题；服务器先排序再分页；tagged 表示四个核心标签齐全；不在浏览器重算查询语义 | 顶部紧凑筛选带 + 可展开更多筛选，点击“应用筛选”后更新连续题目台账 | 数千题时避免卡片堆叠与每次输入都重读快照 | 无业务语义差异；分页替代虚拟滚动是 Phase map 允许的规模选择 | API/store/浏览器 |
| 跨页选择批量题目 | 旧页面当前页批量选择、P1-18 最多 500 ID 契约 | 只向 Job 提交明确题目 ID；去重且上限 500 | 行首复选框、当前页全选、跨页选择计数、隐藏选择提示和清空 | 让 API 调用量和费用在操作前可见 | 能力增强为跨页稳定选择，但不改变所选题的业务结果 | store/DOM/500 上限 |
| 查看完整题目与答案 | 旧教师视角卡片、`QuestionDetailResponse` | 长文本保持原样；答案默认不冒充学生可见内容；缺失内容有说明 | 右侧“题册页”检查器显示题干，答案/解析作为明确分区 | 列表保持高密度，同时为公式和长题提供稳定阅读面 | 纯 UX 重组，无业务差异 | detail 契约/浏览器 |
| 查看图片、富文本和预览 | `QuestionBankReadService` rich/preview/media 契约与安全测试 | 只使用语义 ID 和受控 URL；不泄露路径；缺失/过期局部失败 | 检查器按正文块内嵌素材，并提供“题目预览/答案预览”纸面切换 | 保持内容与素材上下文，不让工具条遮挡试题 | 无业务差异 | media 回归/像素非空 |
| 导入 DOCX/PDF | 旧批量导入弹窗、P1-16 受控上传、P1-18 question_import Job | 浏览器只上传文件字节和安全文件名；服务端请求清单/哈希复核；重复来源不重复入库 | 独立“导入与任务”台，支持多文件排队，每个文件生成独立可恢复任务 | 浏览器不能安全复刻本机路径/文件夹扫描；按文件显示结果更易恢复 | 不迁移本机文件夹扫描；恢复安全文件名自动元数据推断；人工元数据写入无公开契约，列为相邻范围 | HTTP/Job/真实浏览器 |
| 批量 AI 打标签 | 旧页面 AI 批处理、`tagging_sync` Job | 只保存质量状态 `complete`；已有四核心标签跳过；不运行旧技能消歧；真实模型可能收费 | 选中题工具条显示题数，确认后启动；任务面板显示进度、成功、跳过和失败 | 把费用与范围放到动作前，不在页面加载或导入后自动调用 | 默认不自动调用 AI，属于更保守的安全交互；能力仍完整可达，无需改变标签结果 | 假模型、费用确认 DOM |
| 取消与刷新恢复 | 通用 Job Store、P1-18 取消/重试契约 | 取消为协作式且不撤销已发布结果；服务器终态为真相 | 任务按类型和时间连续列出，可取消、重新同步；刷新恢复本浏览器记录的任务 | 长任务不绑死在一次页面会话 | 无业务差异 | Job store/浏览器刷新 |
| 部分失败下载和重试 | P1-18 安全 failures/result 与专用 retry API | 只公开题目 ID、分类和脱敏消息；只重试允许失败项 | 任务行展开失败表；下载 UTF-8 CSV；勾选失败题后重试 | 让教师可以留存和缩小重试范围 | CSV 只由公开安全字段生成，不新增服务端文件或数据语义 | decoder/DOM/CSV 内容 |
| 教师确认当前标签 | 旧标签编辑区、`replace_question_tags()`、活动 tag-only 决策 | 保存替换完整标签集；值裁剪、最长 36、置信度 0–1；写为 manual；不写旧技能表；revision 冲突失败关闭 | 检查器内按“标签类型—标签值—置信度”连续编辑，明确“保存标签” | 避免为每种历史标签制造不同控件，同时保留完整允许集 | 纯 UX 重组；不显示旧技能身份为活动语义 | write API/store/DOM |
| 软删除并恢复当前题 | 旧软删除/恢复、P1-16 revision API | 删除不物理移除标签；恢复使用删除后的 revision；冲突不覆盖 | 危险区二次确认；成功后保留只读检查器和“立即恢复” | 让可逆操作仍有明确影响并提供近场撤销 | 不迁移旧“全部已删除内容”管理页，因公开列表契约不提供删除集；列为相邻范围 | API/DOM |
| 旧入口继续可用 | 当前 Streamlit 题库管理页 | Vue 尚未切为生产 UI；旧页面和服务不删除 | 新导航增加真实“题库管理”，不隐藏旧 Streamlit | 遵守 Phase 2 双入口回退 | 无差异 | 路由/冒烟 |

## Frontend Design Direction

- **主题、受众和单一任务：** 本机 AI 阅卷系统；日常维护数学试题资产的教师；快速找到一道题、读清证据，并安全决定“保存标签、打标签或导入”。
- **色彩：** 只使用现有 Token：纸面白 `#ffffff`、应用灰 `#f6f7f8`、墨色 `#20242a`、次要灰 `#5c6470`、强调蓝 `#2563eb`、AI 紫灰 `#5e628d`、风险红 `#b04444`；无渐变、发光或大面积状态色。
- **字体：** 沿用 Inter / 苹方 / 微软雅黑；题干阅读面可使用现有系统宋体回退增强数学试卷感，但不新增字体依赖；题号、ID、页码和任务计数使用 utility 数字排版。
- **布局：** 页面不是营销首页，也不是卡片大屏。主区像“题册索引 + 摊开的题页”：上方是筛选/选择工具带，中间是连续高密度台账，详情是可收放的纸面检查器；导入与任务使用第二个真实工作区，不与浏览筛选争抢空间。

```text
┌ 题库管理 ─ 题目 n / 已标注 n ───── [导入与任务] ┐
├ 关键词  标签状态  试卷  更多筛选      [应用筛选] ┤
├ 已选 12  当前页全选  清空      [AI 打标签 12题] ┤
├───────────────────────────┬──────────────────┤
│ 题册索引：勾选｜题号｜摘要｜来源｜标签 │ 摊开的题页检查器       │
│ 连续行、服务器分页、选中行有装订标     │ 题干 / 答案 / 素材 / 标签 │
├───────────────────────────┴──────────────────┤
│ 上一页                 第 n / m 页          下一页 │
└──────────────────────────────────────────────┘
```

- **签名元素：** 题目台账最左侧使用窄“题册装订标”，把当前题、已选题和任务失败题以文字缩写 + 细线表达；它来自教师翻题册、夹便签的真实工作方式，不只靠颜色，也不复制 P2-13 登记线。
- **可访问性：** 表头、复选框、按钮和素材有可访问名称；详情打开后标题可聚焦；状态有文字；危险动作无快捷键；`prefers-reduced-motion` 下移除检查器位移动效。
- **自我复核：** 初稿考虑“四块统计卡 + 三栏卡片”，会落入通用后台模板并浪费 1024px 工作空间；已收敛为一条必要覆盖摘要、连续台账和单一检查器。唯一视觉风险用于题册装订标，其余严格服从项目的安静高密度语言。

## Planned Interfaces

现有后端接口保持为主要 seam：

- `GET /api/question-bank/papers`
- `GET /api/question-bank/questions?...`
- `GET /api/question-bank/questions/{id}`
- `GET /api/question-bank/questions/{id}/assets/{index}`
- `GET /api/question-bank/questions/{id}/previews/{type}`
- `PUT /api/question-bank/questions/{id}/tags`
- `DELETE /api/question-bank/questions/{id}`
- `POST /api/question-bank/questions/{id}/restore`
- `POST /api/question-bank/import-uploads?filename=...`
- `POST /api/question-bank/import-requests`
- `POST /api/question-bank/import-requests/{id}/jobs`
- `POST /api/question-bank/question-import-jobs/{id}/retry`
- `POST /api/question-bank/tagging-jobs`
- `POST /api/question-bank/tagging-jobs/{id}/retry`
- `GET/POST /api/jobs/{id}` 与 `/cancel`

仅计划一个向后兼容的后端局部修正：question-import Job 使用受控资源的安全文件名调用既有 `infer_metadata_from_filename()`，恢复旧扫描导入已有的自动年份/地区/考试类型推断；不增加请求字段、不改变重复来源指纹、不接受客户端路径。

前端新增 `question-bank.ts` 类型化 API 和 `question-bank.ts` Pinia store。store 的外部任务级入口为：

```ts
loadPapers()
applyFilters(filters)
loadPage(page)
selectQuestion(id)
loadDetail(id)
toggleSelected(id)
saveTags(id, expectedRevision, tags)
softDelete(id, expectedRevision)
restore(id, expectedRevision)
submitImport(file)
submitTagging(ids)
retryImport(jobId)
retryTagging(jobId, ids)
```

store 隐藏请求 generation、AbortController、严格 decoder、选择去重/500 上限、标签草稿、冲突现场、Job 本地引用和公开失败 CSV 投影。Vue 组件只消费这些任务级 Interface。

## Delivery Slices

### Slice 0：领取、计划与安全基线

- [x] 即时计划成为相对交接基线后的首个 first-parent 提交，且只修改本计划。
- [x] 记录不可变 stash 基线；handoff validator 通过。
- [x] 功能 worktree 源码与 `user_data/` 状态干净、无 reparse point。
- [x] 记录真实两库只读 SHA-256/大小/UTC mtime，不把绝对路径写入文档。
- [x] 第二个文档提交把 Index 的 P2-16/当前动作改为 `in_progress`；首个源码测试前完成。

### Slice 1：类型化 Question Bank API

- [x] RED：papers、组合 filters、questions/detail、rich/media URL、tag write、delete/restore、上传/请求/Job/retry 的严格成功与失败契约。
- [x] GREEN：最小 `frontend/src/api/question-bank.ts`；只允许同源 `/api/`；写请求单次发送；二进制上传使用安全文件名和 200 MiB 前置限制。
- [x] RED/GREEN：question-import 从安全文件名恢复既有自动元数据推断；重复来源和安全结果保持不变。

### Slice 2：题库 Store 与分页选择

- [x] RED：筛选草稿不自动请求；应用后规范化 query；旧列表/详情响应隔离；服务器分页 scope 核对。
- [x] GREEN：papers/list/detail store、请求 generation、AbortController、加载/空/错/保留同范围旧数据。
- [x] RED/GREEN：当前页全选、跨页选择、隐藏选择计数、去重、删除后选择收口和 500 上限。
- [x] RED/GREEN：标签草稿、revision 保存、409 保留输入与重新加载；软删除后立即恢复。

### Slice 3：导入、AI Job 与费用保护

- [x] RED：DOCX/PDF/空文件/超限；多文件独立提交；双击不会重复提交；刷新恢复当前浏览器记录的两类 Job。
- [x] GREEN：导入任务队列和 Job Store 接入；不保存文件字节、路径、题干或密钥到 localStorage。
- [x] RED：AI 未确认时零请求；确认显示实际题数；取消语义；partial 不是成功；失败 CSV 只含安全字段；只重试允许失败 ID。
- [x] GREEN：tagging Job 面板、取消、重新同步、CSV 下载和专用 retry。

### Slice 4：Vue 题册台账与检查器

- [x] RED/GREEN：路由、导航、紧凑覆盖摘要、筛选带、服务器分页、连续题目台账和跨页选择。
- [x] RED/GREEN：题干/答案/富文本/素材/预览检查器；加载、缺失、过期、长公式、长中文、无标签和焦点状态。
- [x] RED/GREEN：标签整集编辑、校验、冲突恢复、软删除确认和立即恢复。
- [x] RED/GREEN：导入与任务工作区、费用确认、取消、partial、失败下载和重试。
- [x] CSS 只使用现有 Token；实现题册装订标；无卡片海洋、渐变或无来源功能。

### Slice 5：匿名浏览器、文档与稳定候选

- [x] 固定生成数千题临时题库、匿名 DOCX/PDF 和假 tagging 服务；所有写入发生在临时数据根。
- [x] 覆盖 1920×1080、1440×900、1366×768、1280×800、1024×768；长公式/图片、100 页、跨页选择、刷新恢复和冲突。
- [x] 更新 `ARCHITECTURE.md` 的 P2-16 已实现事实；页面稳定后生成 quick 自测清单。
- [x] 聚焦测试、受影响 Question Bank/Job 回归、`npm run verify`、真实浏览器、`tools/smoke_check.py --skip-tests`、`git diff --check`、handoff validator 和真实两库指纹守卫通过。

### Slice 6：冻结、双路复审与交接

- [x] 稳定候选提交后冻结 SHA；Spec 与 Standards 两名评审代理检查同一版本。
- [x] 汇总、去重并只阻塞本次修改直接引入或当前任务遗漏的 `Critical`/`Important`。
- [x] 如有阻塞问题，只进行一个统一修复阶段、受影响复测和一次限定最终复审。
- [x] quick 用户短测绑定已复审 SHA；通过后更新交接块为 `verified_pending_integration`。
- [ ] 合入 M2-02 integration，运行 P2-16 逐包受影响验证并更新 Index 精确 SHA。

## File Map

**Create：**

- `frontend/src/api/question-bank.ts`：严格类型、filters、详情、媒体引用、写入、上传和 Job 提交/retry。
- `frontend/src/stores/question-bank.ts`：分页、选择、详情、标签草稿、冲突与本页 Job 工作流。
- `frontend/src/views/QuestionBankView.vue`：题库管理工作区。
- `frontend/src/components/question-bank/QuestionBankFilters.vue`：筛选草稿与应用。
- `frontend/src/components/question-bank/QuestionLedger.vue`：连续台账、选择和分页。
- `frontend/src/components/question-bank/QuestionInspector.vue`：题干/答案/素材/标签/删除恢复。
- `frontend/src/components/question-bank/QuestionImportJobs.vue`：导入、tagging、取消、失败下载和重试。
- `frontend/src/styles/question-bank.css`：高密度台账、题册装订标、检查器与五视口。
- `frontend/src/__tests__/question-bank-api.spec.ts`、`question-bank-store.spec.ts`、`question-bank-view.spec.ts`：公开 seam。
- `frontend/e2e/question-bank-real-api.spec.ts`、`frontend/playwright.p2-16-real.config.ts`：匿名真实 API 与五视口。
- `tools/p2_16_browser_server.py`：临时题库、匿名素材和假 tagging 服务。
- `docs/user-testing/checkpoints/P2-16-v1.5.0-question-bank-quick-check.md`：稳定候选后的版本化短测卡。

**Modify：**

- `backend/jobs/question_import.py` 与聚焦测试：恢复安全文件名自动元数据推断。
- `frontend/src/navigation.ts`、`frontend/src/router/index.ts`、`frontend/src/main.ts` 和相关导航/路由测试：真实“题库管理”入口与样式注册。
- `ARCHITECTURE.md`：P2-16 已实现边界、接口和风险事实。
- `docs/superpowers/packages/EXECUTION_INDEX.md`：领取、稳定候选、integration 和最终证据状态。
- `frontend/package.json`：只增加 P2-16 prepared 浏览器命令，不修改依赖或 lockfile。

## Verification Strategy

- **当前反馈：** 每个 vertical slice 先写一个公开 seam 的失败测试并确认 RED，再做最小实现确认 GREEN；不先批量编写想象中的全部测试。
- **Python：** 元数据 worked example → P1-18 import Job 聚焦回归 → Question Bank routes/write/jobs 受影响回归。
- **Frontend：** API decoder → store → view/components → navigation/router；相关切片完成后运行受影响文件，稳定候选统一运行 `npm run verify`。
- **Browser：** 使用固定匿名临时服务和假模型；同一已构建 SHA 验证数千题、长公式/图片、筛选分页、跨页选择、标签冲突、导入、AI 费用确认、取消、partial、下载/重试、刷新和五档视口。
- **功能分支：** 不运行完整 pytest；交接前运行包内聚焦回归、受影响 API/前端回归和 `tools/smoke_check.py --skip-tests`。
- **里程碑：** P2-16 合入 M2-02 后只运行本包受影响验证。P2-17 进入 integration 后，批次末优先运行一次 2 进程完整门槛；若不稳定立即退回串行，并运行一次组合前端/浏览器门槛和真实两库指纹守卫。

## Review and Fix Policy

稳定候选冻结后使用 `code-review`，让 Spec 与 Standards 两名评审代理检查同一 SHA。全部意见返回后由主代理统一去重、按根因归组并核对来源；只有本次修改直接引入或当前任务遗漏的 `Critical`/`Important` 阻塞。若有阻塞问题，只做一次统一修复和受影响复测，再由原评审者做一次限定最终复审；最终复审仍有 `Critical`/`Important` 时立即停止并向用户汇报，不自动开始第三轮。

## Stage Record

| 阶段 | 耗时 | 测试/复审 | 原始意见 | 去重结果 | 剩余工作 | 当前包验收 | 版本可发布 |
|---|---:|---|---:|---|---|---|---|
| 集中调查与计划 | 约 40 分钟 | 必读资料、P1-15/P1-16/P1-18 契约、旧页面行为、前端基础设施、worktree/数据守卫 | 0 | 6 个根因组 | 实现、自动门槛、复审、用户短测、integration | 否 | 否 |
| 稳定候选前实现 | 约 2 小时 | TDD API/store/view、Question Bank/Job 后端 110 项聚焦回归、前端 613 项验证、2005 题真实浏览器、快速冒烟 | 0 | 0 个阻塞问题 | 双路复审、用户短测、integration | 自动验收通过 | 否 |
| 并行初审 | 约 20 分钟 | Spec 与 Standards 评审同一冻结候选 | 8 | 去重为 6 个 Important、1 个 Suggestion | 统一修复、限定最终复审、用户短测、integration | 否 | 否 |
| 统一修复 | 约 35 分钟 | 20 项受影响前端测试、类型检查、代码规范检查、构建、真实浏览器流程 | 0 | 首轮 7 项全部完成修复 | 限定最终复审、用户短测、integration | 自动验收通过 | 否 |
| 限定最终复审 | 约 10 分钟 | 原 Spec 与 Standards 评审仅复核首轮问题、修复区域及直接回归 | 0 | 0 个 Critical、0 个 Important | 用户 quick 短测、integration | 等待用户验收 | 否 |
| 用户 quick 首轮 | 约 5 分钟 | 用户反馈其他功能完整，步骤 2 的题目预览为纯黑 `1×1` 页面 | 1 | 1 个 Major：匿名验收数据使用不可读占位图 | 修复匿名验收图与回归门槛、用户复查步骤 2、integration | 否 | 否 |
| 用户问题修复 | 约 20 分钟 | 原始 HTTP 图片检查由 RED 变 GREEN；真实浏览器流程 1 项通过；图片生成脚本编译和可视检查通过 | 0 | 0 个未解决问题 | 用户复查步骤 2、integration | 等待用户验收 | 否 |
| 用户 quick 复查 | 约 2 分钟 | 用户确认步骤 2 已通过，并明确回复 P2-16 1–7 通过 | 0 | 0 个 Blocker、0 个 Major | integration | 通过 | 否 |

## Ownership

P2-16 功能分支拥有 question-import 自动元数据局部修正、Question Bank 前端 API/store/components/view/CSS、匿名浏览器工具、即时计划、P2-16 架构增量和 quick 自测清单。integration 负责最终 Index 状态、里程碑精确 SHA、共享入口冲突和 M2-02 逐包/批次门槛；功能分支不宣称自己已进入主线。

## Rollback and Stop Conditions

- 功能回退为整体 revert P2-16 提交并继续使用旧 Streamlit 题库管理页；不删除既有题目、标签、试卷或任务历史。
- 发现必须改变 `question_tags.knowledge_point` 活动身份、标签保存规则、AI prompt/模型策略、题库 Schema 或组卷/训练语义时停止。
- 需要把旧技能目录、概念关系、技能消歧或“知识点整理（高级）”重新作为活动语义时停止。
- 测试需要真实题库写入、真实密钥、真实模型调用、不可逆文件操作或客户端磁盘路径时停止。
- 任务结果包含内部路径、密钥、题干或原始异常，或真实两库指纹/大小/UTC mtime未经授权变化时立即停止。
- 同一包连续两次修复失败，或最终复审仍有 `Critical`/`Important` 时停止。

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P2-16
**交接状态：** verified_pending_integration
**功能提交：** 38e3337260dddb76fb4fa4495f81c808d8744b60
**自动验证：** passed
**独立复审：** passed
**用户验收：** passed
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** independent_candidate_allowed
<!-- HANDOFF_STATUS_END -->
