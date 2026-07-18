# P2-17 组卷工作台即时实现计划

**执行包：** P2-17
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** yes
**计划基线：** 5c1181e4e7f7827779c7a5b4da772c27a156f951
**交接基线：** 5c1181e4e7f7827779c7a5b4da772c27a156f951
**用户自测：** quick
**自测清单：** docs/user-testing/checkpoints/P2-17-v1.5.0-assembly-quick-check.md

> 用户于 2026-07-18 明确开始 P2-17。Phase map、现有 Streamlit 组卷行为、`assembly_basket_state`/记录/导出器、P2-16 Question Bank API 与下列 Public Test Seams 共同冻结测试范围；不把旧页面布局、私有 JSON 文件结构或 Vue 内部组件层级作为验收接口。

## Objective

在不修改题库 Schema、题目/标签语义、现有 Word 格式规则、模型策略或生产入口的前提下，交付独立 Vue“组卷工作台”：教师可从题库把明确选中的题目加入唯一试题篮，刷新或重启后恢复；重复题自动去重；在分节轨道中拖拽排序、改节标题和跨节移动；查看学生/教师预览与题数、题型、可识别分值摘要；提交可取消、可查询、可重试的 Word/Markdown 导出任务；从组卷记录恢复、下载或删除记录。旧 Streamlit 组卷页继续可用，所有写入和导出验证只使用临时题库与临时数据根。

## Frozen Boundary

- **包含：** `assembly_basket_state` 兼容草稿；题目 ID 去重、顺序、手工 section specs、标题、页眉、答案开关与预览模式；P2-16 题库选中项加入试题篮；批量解析篮内题目；题数、题型、知识点、难度和题干开头可识别分值摘要；顺序/按题型/手工分节预览；Word/Markdown 导出 Job；进度、取消、失败重试、刷新恢复、过期文件提示；组卷记录列表、恢复、受控下载和只删除记录；Vue 路由/导航；五档 Windows 桌面视口；quick 用户短测。
- **明确不包含：** 数据库迁移；真实题库写入；真实模型调用；新增或迁移自动组卷/AI 分类算法；修改既有 AI prompt、模型、API profile、并发或 RPM；题目正文、答案、标签或分值编辑；PDF 导出；客户端目标路径/文件夹选择；删除导出的实体文件；生产 UI 切换；删除旧 Streamlit 页面。
- **相邻但不并入：** 旧页“AI 智能一键排序与优化”直接调用模型并在失败时本地排序，Phase map 只要求拖拽排序、分节并明确“不新增自动组卷算法”；本包保留旧入口但不把该费用能力接入 Vue。题目没有独立分数字段，旧页只识别题干开头 `(10分)`/`（10分）`；新摘要沿用该提示性口径，未识别题目明确显示“未标分”，不新建分值数据模型。旧记录只保存 Word 私有路径；新 API 兼容读取旧记录但绝不公开路径。
- **验收条件：** 刷新/进程重启恢复；重复题去重；两个页面/标签页冲突不会静默覆盖；排序、增删分节、改标题和跨节移动后顺序稳定；缺题或缺素材有文字降级且不泄露路径；Word/Markdown 真实生成并通过受控 URL 下载；取消前不发布，失败可重试，发布后取消竞态由正常完成胜出；历史记录可恢复、下载、过期提示和只删记录；受影响自动测试、匿名真实浏览器、快速冒烟、交接验证与真实两库指纹守卫通过。
- **风险等级：** 中。草稿和导出均可重建且不改数据库，但涉及多个状态写入、并发标签页、长任务、取消/重试、文件原子发布和部分完成；形成稳定候选后必须进行需求符合性与代码质量双路复审。

## Root-Cause Inventory

开工集中调查只覆盖 P2-17 及直接影响范围，去重并冻结为六个根因组；修改前问题和相邻需求不自动并入。

| 根因组 | 调查证据与当前缺口 | 本包统一处理 |
|---|---|---|
| A. 草稿只有兼容文件函数，没有公开并发契约 | `assembly_basket_state.py` 读写固定 JSON，写失败被吞掉；只存 basket/order/sections，无 revision、原子替换、标题或预览配置 | 新增深模块 `AssemblyWorkspaceService`；兼容旧 JSON，规范化去重/顺序/分节，内容哈希 revision，临时文件 + `os.replace()` 原子保存，409 保留后来草稿 |
| B. P2-16 能选题但不能进入试题篮 | Question Bank store 已有最多 500 个跨页选择 ID，Vue 没有 assembly API、篮子动作或路由 | 在已选题工具条增加“加入试题篮”；只提交明确 ID，服务端再次去重；成功后显示篮子总数并可进入组卷 |
| C. 篮内题目与摘要没有批量公开 seam | 现 API 只支持分页列表和逐题 detail；旧页逐 ID 直读 `QuestionService`，分值只从题干开头识别 | Question Bank Read Service 增加单快照、最多 500 ID 的稳定批量解析；Assembly API 返回公开摘要、缺失 ID 与提示性分值，不暴露 SQL/路径 |
| D. 编排状态跨字段，容易出现重复、遗漏或冲突 | 旧 basket/order/sections 可彼此不一致；删除 section、跨节移动和缺题恢复缺少统一不变量 | 服务端统一验证：order 恰好覆盖 basket；每题最多一个 section；section 题目合集覆盖当前有效顺序；空节被丢弃；UI 只保存完整快照 |
| E. 导出同步执行且历史依赖私有路径 | 旧页同步 Word 导出，记录 JSON 保存绝对 output path；无 Job、Markdown、受控历史下载、取消或 retry | 新 `assembly_export` Job 使用任务独占暂存目录，取消检查后原子发布；新增安全 Markdown；记录只在成功发布后创建；公开结果只含安全文件名/URL/摘要 |
| F. Vue 缺少针对教师组卷任务的信息结构 | 当前导航有题库但无组卷；旧 1,802 行页面把筛选、AI、拖拽、预览、导出和历史堆在同页 | 新页聚焦“分节轨道 + 纸面预览 + 导出/历史”；选题复用题库，不复制第二套筛选器；加载、空白、冲突、缺素材、失败、取消和过期都有明确下一步 |

## Fixed Failure Matrix

| 场景 | 预期结果 |
|---|---|
| 重复操作 | 加题在客户端和服务端均按 ID 去重；保存/导出按钮请求中禁用；写请求不自动重放；重复主动导出可形成不同记录但不会覆盖文件 |
| 同时操作 | 草稿 PUT 必须携带当前 revision；旧标签页得到 409 并保留本地编排，只有重新加载后才能继续保存；导出只使用提交时已保存的精确 revision |
| 中途退出 | 草稿临时文件不替代有效旧稿；导出只在取消边界后发布；未发布的任务暂存可清理，已发布文件必须有对应记录 |
| 重新启动 | 草稿从兼容 JSON 重建；当前浏览器记录的 Job ID 由通用 Job Store 恢复；服务器记录列表重新读取；中断 Job 按既有 JobStore 规则进入可解释终态 |
| 失败重试 | GET 可用现有 Client 有界重试；所有写请求单次发送；failed/cancelled export 只通过专用 retry 端点复用服务器私有 payload，不接受客户端路径 |
| 取消 | queued 立即取消；running 在生成前/发布前协作检查；一旦发布边界已跨过，正常成功可以胜出；不承诺撤销已生成并发布的文件 |
| 部分完成 | 输出发布后若记录创建失败，任务删除自己本次生成的输出并失败；不会留下可见“成功但无记录”；记录删除不删除输出文件 |
| 数据缺失 | 草稿中的已删除/不存在题目以 missing IDs 显示并阻止导出；素材缺失只降级对应题目并保留文字；空篮、空记录、过期文件有明确恢复动作 |
| 数据冲突 | revision 冲突不覆盖服务器草稿；若导出运行中用户保存了新草稿，成功任务只清空与提交 revision 相同的旧稿，绝不清空后来修改 |
| 外部费用 | 本包不注册、不调用任何自动组卷/AI 分类模型；匿名测试不使用密钥或真实模型 |

## Public Test Seams

1. **Assembly Workspace service seam：** 临时数据根验证兼容旧稿、去重、order/sections 不变量、原子保存、revision 冲突、记录创建/列出/删除/受控文件解析；不测试私有 JSON helper。
2. **Assembly HTTP seam：** 真实 FastAPI + 临时题库验证 draft GET/PUT、批量题目、409、records、download、export submit/retry 与安全错误；只在证明文件发布不变量时检查临时目录。
3. **Assembly Export Job seam：** 真实小型题库生成 DOCX/Markdown，验证顺序/分节/答案、缺素材降级、取消前不发布、记录失败补偿、重试和结果脱敏。
4. **Typed frontend API/store seam：** 严格 decoder、写请求不重放、草稿 generation/revision、拖拽/分节不变量、冲突现场、Job 恢复和历史投影；loader 通过公开函数替换，不 mock 内部组件。
5. **Vue user-flow seam：** 真实 DOM 从 P2-16 选题加入篮子，进入编排，拖拽、分节、预览、导出、取消/失败重试、历史恢复/过期/删除。
6. **匿名真实浏览器 seam：** 固定生成题库与临时数据根，运行真实 FastAPI + Vue，覆盖刷新/重启、重复题、缺素材、Word/Markdown 下载和五档桌面视口；不读取真实数据库或调用模型。

## Business Capability and UX Traceability

| 用户任务或业务能力 | 现有业务来源 | 必须保持的语义、结果与安全边界 | 新 Vue 呈现与操作设计 | UX 调整理由 | 业务差异及用户决定 | 验证方式 |
|---|---|---|---|---|---|---|
| 从题库加入试题篮 | `pages/题库管理.py` 试题篮、P2-16 跨页选择 | 只加入明确题目 ID；重复 ID 去重；不修改题目 | P2-16 已选题工具条增加“加入试题篮”，成功后保留选择并显示进入组卷入口 | 不复制第二套筛选器，选题仍发生在题库真相页 | 纯 UX 重组，无业务结果差异 | HTTP/store/DOM |
| 恢复唯一草稿 | `assembly_basket_state.load/save_basket_draft()` | basket/order/sections 可跨刷新与重启恢复；不读取客户端路径 | 页面加载服务器草稿；顶部显示“已保存/保存中/冲突”；完整快照保存 | 把隐式文件写入变为可见状态，失败不会假装成功 | 增加 revision 冲突保护，属于安全增强 | service/API/双标签页 |
| 重复题去重与稳定顺序 | `normalize_question_ids()`、`order_for_basket()` | 每个题目 ID 最多一次；旧 order 缺项时补齐，越界项移除 | 试题轨道显示连续题号，拖拽后整体保存 | 让题号与导出顺序一致 | 无业务差异 | worked example/DOM |
| 编排与分节 | 旧顺序/按题型/AI sections 编辑、Phase map `section specs` | section 标题与题目唯一归属；段内/段间顺序决定预览与导出 | 支持顺序、按题型和手工分节；可改标题、增删节、段内拖拽、跨节移动 | 不依赖付费 AI 才能获得可编辑 sections | 手工建节是 Phase map 明示的 section specs 操作；不新增算法 | service/store/drag DOM |
| 查看题数与分值摘要 | 旧统计面板、`_process_question_for_drag_card()` | 题数/题型/难度/知识点来自当前题目；分值只识别题干开头括号分值 | 窄摘要条显示题数、题型、平均难度、知识点和“已识别分值/未标分题数” | 把不完整分值明确标成提示，不伪造总分 | 无新分值字段或口径 | API literal cases/DOM |
| 学生/教师预览 | 旧 `assembly_preview_view` 与题目详情/媒体 | 学生视角不显示答案；教师视角明确显示答案/标签；素材只经受控 URL | 中央纸面连续预览，工具条切换视角；缺素材保留题干和文字说明 | 编排与结果同屏，减少来回跳转 | 纯 UX 重组 | detail/media/browser |
| 导出 Word | `export_question_paper_docx()` | 顺序、sections、按题型、标题、页眉、答案开关和现有版式不改；缺素材跳过而不泄露路径 | 先保存草稿，再提交 Job；显示进度、取消、失败和下载 | 长操作可刷新恢复，结果仍由旧导出器生成 | 同一导出规则，执行方式任务化 | exporter/job/下载 |
| 导出 Markdown | Phase map P2-17 验收明确要求 Word/Markdown | 顺序、sections、标题、页眉和答案开关与 Word 语义一致；不写内部路径 | 与 Word 共用导出设置；`.md` 直接下载 | 提供轻量可编辑文本，缺图用文字占位 | Phase map 明确新增格式；不改变 Word 规则 | golden text/job/下载 |
| 组卷记录 | `assembly_record_service.py` 与旧历史面板 | 成功导出才有记录；可恢复题目顺序、下载或删记录；删记录不删文件 | 右侧“导出与记录”按时间列出状态、格式、题数；恢复、下载、过期重建、确认删记录 | 记录与当前导出任务同处，失败更易恢复 | 旧记录兼容读取；API 不公开 output path | service/API/DOM |
| 旧 AI 分类 | `pages/组卷.py::_run_ai_question_sorting()` | 真实调用会产生费用；失败回退本地排序 | Vue 不提供 AI 自动组卷按钮，旧 Streamlit 入口继续保留 | Phase map 不要求算法迁移，且明确不新增自动算法 | 记录为相邻能力；无本包授权，不调用 | 静态断言/零模型调用 |
| 旧入口继续可用 | 当前 Streamlit 组卷页 | Vue 尚未切为生产 UI；旧页面、草稿兼容和导出器不删除 | 新导航增加真实“组卷工作台”，不隐藏旧 Streamlit | 遵守 Phase 2 双入口回退 | 无差异 | 路由/冒烟 |

## Frontend Design Direction

- **主题、受众和单一任务：** 本机 AI 阅卷系统；把数学题编排成可发给学生试卷的教师；把选好的题从“散页”整理成有顺序、有分节、可下载的一份卷。
- **色彩：** 纸面白 `#ffffff`、应用灰 `#f6f7f8`、墨色 `#20242a`、次要灰 `#5c6470`、强调蓝 `#2563eb`、警告棕 `#9a6718`；只使用现有 Token，无渐变、发光或大面积语义色。
- **字体：** 沿用 Inter / 苹方 / 微软雅黑；预览正文使用现有系统宋体回退表达试卷纸面；题号、分值和任务计数使用 utility 数字排版，不增加字体依赖。
- **布局：** 页面不是大屏或卡片海洋。左侧是窄“分节书脊”，中央是可滚动纸面，右侧是克制的导出与记录；题库承担选题，组卷页只承担编排与出件。

```text
┌ 组卷工作台 ─ 已保存 ─ 12题 / 已识别 86分 ─ [继续选题] ┐
├──────────────┬───────────────────────────┬──────────────┤
│ 分节书脊      │ 试卷纸面                   │ 导出与记录    │
│ 一、选择题 6  │ 标题 / 页眉 / 学生或教师视角 │ Word / MD     │
│ 二、填空题 3  │ 1. 题干…      ☰ 拖拽       │ 答案开关      │
│ 三、解答题 3  │ 2. 题干…      素材状态       │ 当前 Job      │
│ + 新增分节     │ …                           │ 历史记录      │
└──────────────┴───────────────────────────┴──────────────┘
```

- **签名元素：** 左侧“分节书脊”用真实章节标题、题数、细装订线和当前落点组成；拖拽时落点沿书脊与纸面同步，体现教师把散题装订成卷的任务，不只靠颜色。
- **可访问性：** 所有拖拽都有“上移/下移/移至分节”的键盘替代；焦点明显；学生/教师视角是文字状态；缺素材与冲突不只用颜色；`prefers-reduced-motion` 下取消位移动效。
- **自我复核：** 初稿若复制题库的“台账 + inspector”会让组卷仍像资产管理；已改为分节书脊与连续纸面，唯一视觉风险用在书脊同步落点，其余保持项目安静、高密度语言。

## Planned Interfaces

后端新增独立 `/api/question-assembly` seam：

- `GET /draft`
- `PUT /draft`：完整快照 + `expected_revision`
- `GET /questions?question_ids=...`：最多 500 个 ID 的单快照公开摘要
- `GET /records?limit=...`
- `DELETE /records/{record_id}`
- `GET /records/{record_id}/download`
- `POST /exports`：以当前 `draft_revision` 提交 `assembly_export`
- `POST /exports/{job_id}/retry`
- 通用 `GET /api/jobs/{id}`、`POST /cancel` 与 `/api/jobs/{id}/download`

后端任务级深模块：

```python
workspace.load_draft()
workspace.save_draft(expected_revision, draft)
workspace.add_questions(expected_revision, question_ids)
workspace.resolve_questions(question_ids)
workspace.clear_if_revision(expected_revision)
workspace.list_records(limit)
workspace.create_record(export_result)
workspace.delete_record(record_id)
workspace.resolve_record_file(record_id)
```

前端新增 `assembly.ts` 类型化 API 与 `assembly.ts` Pinia store。store 外部任务级入口：

```ts
loadDraft()
addSelectedQuestions(ids)
saveDraft(nextDraft)
moveQuestion(questionId, target)
createSection(title)
renameSection(sectionId, title)
deleteSection(sectionId)
loadRecords()
submitExport(format)
retryExport(jobId)
restoreRecord(recordId)
deleteRecord(recordId)
```

store 隐藏 revision、请求 generation、AbortController、完整快照规范化、冲突现场、拖拽不变量、Job 本地引用和历史安全投影。Vue 组件只消费这些任务级接口。

## Delivery Slices

### Slice 0：领取、计划与安全基线

- [ ] 首个 first-parent 提交只包含本即时计划和合法 `in_progress` 交接块。
- [ ] handoff validator 使用 integration 现场可信基线 `5c1181e4e7f7827779c7a5b4da772c27a156f951` 通过。
- [ ] 功能 worktree 源码与 `user_data/` 状态干净；源码区无外部 reparse point。
- [ ] 真实两库只读基线已记录：`grading_system.db` SHA-256 `93fee56e23ea072ac48351b1e6616d7af7f4b35ceb2b4779890e8d059fb841cd`、2,863,104 bytes、UTC mtime `2026-07-10T07:10:41.1221109Z`；`question_bank.db` SHA-256 `e1e5123ad54c9e8af5984bdcc5182a8f7a3038a1707f98ab26f168f4577a88b8`、3,461,120 bytes、UTC mtime `2026-07-08T11:58:06.3320883Z`。
- [ ] 第二个文档提交把功能分支所见 Index 的 P2-17/当前动作改为 `in_progress`；首个源码测试前完成。

### Slice 1：Assembly Workspace 深模块

- [ ] RED：旧 basket/order/sections JSON 兼容；重复/无效 ID；stale order；空/重复 section；标题/页眉/答案/预览字段；损坏 JSON；原子替换失败保留旧稿。
- [ ] GREEN：`AssemblyWorkspaceService`、不可变 draft/section/record DTO、内容 revision 和原子 JSON。
- [ ] RED/GREEN：expected revision 成功/冲突；并发加题；clear-if-revision；旧记录兼容；记录删除不删文件；受控 `.docx/.md` 解析拒绝越界。

### Slice 2：Assembly HTTP 与批量题目

- [ ] RED：draft GET/PUT/add；500 ID 上限；单快照题目顺序、missing IDs、分值识别；路径字段拒绝；错误体脱敏。
- [ ] GREEN：schema/router/dependency；Question Bank Read Service 单快照批量解析；不新增数据库写入或 Schema。
- [ ] RED/GREEN：records 列表/删除/下载，旧绝对路径不公开，过期/越界/类型错误分别为 410/403/415。

### Slice 3：Word/Markdown Export Job

- [ ] RED：当前 revision 提交；顺序/按题型/sections；答案/页眉；Word 复用现有 exporter；Markdown golden text；图片 marker 不泄露本机路径。
- [ ] GREEN：最小 Markdown 组卷导出器与 `assembly_export` runner/handler；任务独占暂存、取消检查、原子发布、成功记录。
- [ ] RED/GREEN：缺题拒绝；缺素材文字降级；记录创建失败删除本任务输出；草稿未变化才清空；failed/cancelled retry；公开 payload/result 白名单与受控下载。

### Slice 4：Frontend API/Store 与 P2-16 选题接入

- [ ] RED/GREEN：严格 decoder、写请求单次发送、draft generation/revision、冲突保留、重复加题、500 上限。
- [ ] RED/GREEN：完整快照 reorder/sections invariant、键盘移动、摘要、学生/教师预览状态。
- [ ] RED/GREEN：Job 引用刷新恢复、取消、retry、过期文件、记录恢复/删除。
- [ ] P2-16 选择工具条增加“加入试题篮”与进入组卷入口；不改变 AI 打标签选择语义。

### Slice 5：Vue 分节书脊、纸面与导出记录

- [ ] RED/GREEN：路由、导航、空篮、加载、保存、冲突、继续选题。
- [ ] RED/GREEN：分节书脊、拖拽与键盘替代、改名/增删/跨节移动、摘要与连续纸面。
- [ ] RED/GREEN：学生/教师预览、长公式/长中文、局部缺素材、标题/页眉/答案设置。
- [ ] RED/GREEN：Word/Markdown Job、取消/失败重试、下载、历史恢复/过期/只删记录。
- [ ] CSS 只使用现有 Token；实现分节书脊；无卡片海洋、渐变、AI 自动组卷或无来源功能。

### Slice 6：匿名浏览器、文档与稳定候选

- [ ] 固定生成题库、可读图片、缺失素材和临时数据根；所有写入发生在临时目录。
- [ ] 真实 FastAPI + Vue 贯通题库选题、重复去重、刷新/服务重启、双标签冲突、编排、Word/Markdown 下载、取消/失败 retry、历史恢复/过期/删除。
- [ ] 覆盖 1920×1080、1440×900、1366×768、1280×800、1024×768；50 题、长分节名、长公式、未标分和焦点无溢出。
- [ ] 更新 `ARCHITECTURE.md` 的 P2-17 已实现边界；页面稳定后生成 quick 自测清单。
- [ ] 聚焦测试、受影响 Question Bank/Job/File 回归、`npm run verify`、真实浏览器、`tools/smoke_check.py --skip-tests`、`git diff --check`、handoff validator 和真实两库指纹守卫通过。

### Slice 7：冻结、双路复审、用户短测与 M2-02 收口

- [ ] 稳定候选提交后冻结 SHA；Spec 与 Standards 两名评审代理检查同一版本。
- [ ] 汇总、去重并只阻塞本次修改直接引入或当前任务遗漏的 `Critical`/`Important`。
- [ ] 如有阻塞问题，只进行一个统一修复阶段、受影响复测和一次限定最终复审。
- [ ] quick 用户短测绑定已复审 SHA；通过后更新交接块为 `verified_pending_integration`。
- [ ] 合入 M2-02 integration，运行 P2-17 逐包受影响验证。
- [ ] 三包齐备后只运行一次 M2-02 批次末组合回归、2 进程完整门槛（不稳定则退回串行）、完整冒烟、组合浏览器与真实两库指纹守卫；随后按标准流程 push integration、创建/合并里程碑 PR 并同步主线。

## File Map

**Create：**

- `question_bank/services/assembly_workspace_service.py`：草稿、revision、sections、记录和受控文件深模块。
- `question_bank/exporters/paper_markdown_exporter.py`：与现有组卷语义一致的安全 Markdown。
- `backend/jobs/assembly_export.py`：暂存、取消、原子发布、记录和条件清空草稿。
- `backend/api/schemas/assembly.py`、`backend/api/routers/assembly.py`：独立公开契约。
- `frontend/src/api/assembly.ts`、`frontend/src/stores/assembly.ts`：严格类型、草稿/编排/Job/记录。
- `frontend/src/views/AssemblyView.vue`：组卷工作台。
- `frontend/src/components/assembly/AssemblySectionRail.vue`：分节书脊、拖拽与键盘替代。
- `frontend/src/components/assembly/AssemblyPaperPreview.vue`：学生/教师连续纸面与素材降级。
- `frontend/src/components/assembly/AssemblyExportPanel.vue`：导出任务和历史记录。
- `frontend/src/styles/assembly.css`：分节书脊、纸面、三段式响应和五视口。
- `tests/test_assembly_workspace_service.py`、`test_api_assembly_routes.py`、`test_assembly_export_job.py`、`test_question_bank_paper_markdown_exporter.py`：后端公开 seam。
- `frontend/src/__tests__/assembly-api.spec.ts`、`assembly-store.spec.ts`、`assembly-view.spec.ts`：前端公开 seam。
- `frontend/e2e/assembly-real-api.spec.ts`、`frontend/playwright.p2-17-real.config.ts`、`tools/p2_17_browser_server.py`：匿名真实流程与五视口。
- `docs/user-testing/checkpoints/P2-17-v1.5.0-assembly-quick-check.md`：稳定候选后的版本化短测卡。

**Modify：**

- `question_bank/services/assembly_basket_state.py`、`assembly_record_service.py`：保留旧调用并委托新模块/读取新兼容字段。
- `question_bank/services/question_read_service.py`、Question Bank schema/tests：批量解析与分值提示。
- `backend/jobs/default_handlers.py`、`backend/api/dependencies.py`、`backend/api/app.py`、router/schema exports：注册 Assembly service/router/Job。
- `backend/api/routers/jobs.py`、`backend/files/service.py` 与聚焦测试：Assembly Job 公开白名单与下载规则。
- `frontend/src/api/question-bank.ts`、`stores/question-bank.ts`、`QuestionBankView.vue` 及测试：选中题加入试题篮。
- `frontend/src/navigation.ts`、`frontend/src/router/index.ts`、`frontend/src/main.ts` 与相关测试：真实“组卷工作台”入口与样式注册。
- `frontend/package.json`：只增加 P2-17 prepared 浏览器命令，不修改依赖或 lockfile。
- `ARCHITECTURE.md`：P2-17 已实现事实、接口和风险边界。
- `docs/superpowers/packages/EXECUTION_INDEX.md`：领取、稳定候选、integration、M2-02 门槛和最终证据。

## Verification Strategy

- **当前反馈：** 每个 vertical slice 先写一个公开 seam 的失败测试并确认 RED，再做最小实现确认 GREEN；不先批量编写想象中的全部测试。
- **Python：** workspace worked example → HTTP → exporter/job/file →现有 assembly/exporter/question-bank/jobs/files 受影响回归。
- **Frontend：** API decoder → store → view/components → Question Bank 接入 → navigation/router；相关切片完成后运行受影响文件，稳定候选统一运行 `npm run verify`。
- **Browser：** 使用固定匿名临时服务；同一已构建 SHA 验证选题、去重、刷新/重启、双标签冲突、拖拽/键盘、分节、缺素材、Word/Markdown、取消/retry、历史和五档视口。
- **功能分支：** 不运行完整 pytest；交接前运行包内聚焦回归、受影响 API/前端回归和 `tools/smoke_check.py --skip-tests`。
- **里程碑：** P2-17 合入 M2-02 后先运行本包受影响验证；随后因本包是固定三包批次末，按 Index 只运行一次组合前端/浏览器和完整门槛，优先 2 进程，不稳定时退回串行。

## Review and Fix Policy

稳定候选冻结后使用 `code-review`，让 Spec 与 Standards 两名评审代理检查同一 SHA。全部意见返回后由主代理统一去重、按根因归组并核对来源；只有本次修改直接引入或当前任务遗漏的 `Critical`/`Important` 阻塞。若有阻塞问题，只做一次统一修复和受影响复测，再由原评审者做一次限定最终复审；最终复审仍有 `Critical`/`Important` 时立即停止并向用户汇报，不自动开始第三轮。

## Stage Record

| 阶段 | 耗时 | 测试/复审 | 原始意见 | 去重结果 | 剩余工作 | 当前包验收 | 版本可发布 |
|---|---:|---|---:|---|---|---|---|
| 集中调查与计划 | 约 45 分钟 | 必读资料、旧组卷页/草稿/记录/导出器、P2-16 API、Job/File/前端基础设施、worktree/数据守卫 | 0 | 6 个根因组 | 实现、自动门槛、复审、用户短测、integration 与 M2-02 批次门槛 | 否 | 否 |

## Ownership

P2-17 功能分支拥有 Assembly workspace/API/Job/Markdown、Question Bank 批量解析与选题接入、Vue Assembly API/store/components/view/CSS、匿名浏览器工具、即时计划、P2-17 架构增量和 quick 自测清单。integration 负责最终 Index 冲突裁定、里程碑精确 SHA、M2-02 逐包/批次门槛、push/PR/合并与主线同步；功能分支不宣称自己已进入主线。

## Rollback and Stop Conditions

- 功能回退为整体 revert P2-17 提交并继续使用旧 Streamlit 题库/组卷页；不删除既有草稿、记录或导出文件。
- 发现必须修改题库 Schema、题目/标签身份、Word 格式规则、自动组卷算法、AI prompt/模型策略或训练语义时停止。
- 需要真实题库写入、真实密钥、真实模型调用、客户端目标路径、删除实体导出文件或不可逆操作时停止。
- 草稿/Job/记录结果公开内部路径、题干、密钥或原始异常，或真实两库指纹/大小/UTC mtime未经授权变化时立即停止。
- 同一包连续两次修复失败，或最终复审仍有 `Critical`/`Important` 时停止。

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P2-17
**交接状态：** in_progress
**功能提交：** none
**自动验证：** pending
**独立复审：** pending
**用户验收：** pending
**真实数据指纹：** not_touched
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
