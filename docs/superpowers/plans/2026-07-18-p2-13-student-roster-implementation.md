# P2-13 学生名单导入与管理即时实现计划

**执行包：** P2-13
**规划状态：** ready_for_execution
**计划基线：** 44228d5f1d8945a35e45f2f20445374c88ebb28e
**用户自测：** quick
**自测清单：** docs/user-testing/checkpoints/P2-13-v1.5.0-students-quick-check.md
**已确认测试缝：** 用户于 2026-07-18 启动正式包；Phase map、现有 Streamlit/服务/数据库契约和下列 Public Test Seams 共同固定测试范围，不另行把私有 SQL 或页面内部实现作为验收接口。

## Objective

在不改变 `students` Schema、学生唯一编码语义、答卷匹配规则或生产入口的前提下，交付独立 Vue“学生名单”工作区：教师可以上传 CSV/XLSX 名单，检查字段映射、无效行、同文件重复和已有学号冲突，再明确确认批量 upsert；也可以分页筛选现有学生、编辑单名学生，并在看清历史成绩/答卷影响且备份成功后硬删除。旧 Streamlit 入口继续可用，所有自动验证只写临时数据库和临时上传。

## Frozen Boundary

- **包含：** 受控 CSV/XLSX 文件读取；自动与手动字段映射；逐行预览和错误说明；同文件重复的现有“后行覆盖前行”规则显式化；已有学号 insert/update/unchanged 分类；名单 revision 冲突保护；原子批量 upsert；分页、搜索、班级筛选；单名编辑；删除影响预检；备份成功后硬删除；Vue 路由/导航/工作台入口；五档桌面视口；quick 用户短测。
- **明确不包含：** 数据库迁移；新增学生字段；账号、身份认证或权限体系；Phase 6 学生画像；批量硬删除；修改 OCR/答卷匹配、评分、报告或分析口径；删除/替换旧 Streamlit 入口；生产 UI 切换；真实学生数据写入；真实模型调用；通用文件浏览或客户端路径。
- **验收条件：** 正常、已有学号更新、同文件重复、缺列、乱码/不可解码、空文件和部分无效行均有稳定结果；确认写入使用名单 revision，重复/并发提交至多一方成功且不会部分提交；5,000 行预览和现有名单分页保持可用；编辑冲突不覆盖后来数据；删除前展示完整影响计数，备份失败时零删除，成功时返回安全备份确认且不泄露路径；刷新或重启后从服务器真相恢复；五档视口无意外溢出；受影响自动测试、匿名真实浏览器、快速冒烟、交接验证和真实两库指纹守卫通过。
- **风险等级：** 高。没有 Schema 变化，但学生主数据会影响扫描匹配、成绩、题目明细、批注、出勤和答卷关联；硬删除不可由页面撤销，必须完成需求符合性与代码质量双路复审、备份失败保护和临时数据库故障验证。

## Root-Cause Inventory

开工集中调查只冻结 P2-13 及直接影响范围，去重为四个根因组；修改前问题或相邻需求不自动并入。

| 根因组 | 调查证据与当前缺口 | 本包统一处理 |
|---|---|---|
| A. 导入规则只有进程内列表 | `StudentManager.load_students()` 解析后由 Streamlit 直接预览，缺行被静默跳过、重复学号静默以后行为准；FastAPI 没有上传/映射/预览契约 | 把解析、映射、逐行诊断、重复选择和数据库冲突分类收口到学生名册 Module；上传只收文件字节和安全文件名，不落客户端路径 |
| B. 写入缺少预览到确认的一致性 | `POST /api/students` 直接 upsert，未绑定预览时名单版本；重复或同时确认无法说明使用了哪个名单快照 | 预览返回不透明 roster revision；确认在同一 `BEGIN IMMEDIATE` 事务内重算并核对 revision，再原子 upsert；写请求不自动重放 |
| C. 管理路由无法支撑长名单和编辑冲突 | `GET /api/students` 返回全部学生供既有筛选消费者使用；PATCH 只按 ID 写入，没有名单 revision；Vue `/students` 仍未迁移 | 保留既有只读列表契约，新增分页 workspace；新编辑携带 roster revision 并在事务内校验；Vue 使用分页、搜索和班级筛选 |
| D. 硬删除只有按钮确认，没有可复核影响契约 | `delete_student_hard()` 会备份并清除结果/明细/批注/出勤、解绑答卷，但 API 直接 DELETE，页面无法在删除前取得准确计数，且备份结果未公开 | 新增删除影响预检；确认删除在写锁内复核 revision 和影响，先完成一致备份再删除；返回计数和安全 `backup_created`，不返回磁盘路径 |

## Fixed Failure Matrix

| 场景 | 预期结果 |
|---|---|
| 重复操作 | 同一预览 revision 的第二次确认返回 409，不重复写；重新预览同一内容只得到 unchanged；删除同一学生第二次返回 404 |
| 同时操作 | 两个相同 revision 的导入/编辑/删除在 SQLite 写事务中串行，第一方成功后第二方因 revision 改变失败关闭；不出现后写覆盖 |
| 中途退出 | 导入和编辑只在单事务提交后可见；删除备份失败或任一删除语句失败时整事务回滚，学生与关联数据仍在 |
| 重新启动 | 预览不依赖服务器内存令牌；未确认文件只需重新选择并预览；列表、影响和 revision 均从数据库重建 |
| 失败重试 | 读取失败可修正映射/文件后重预览；写失败保留浏览器预览和选择，但必须重新读取最新名单；写请求不自动重放 |
| 取消 | 预览请求可由浏览器中止且无副作用；确认前可关闭导入台；同步事务开始后不伪装成可取消任务 |
| 部分完成 | 无效/重复前置行在预览中明确排除，教师可只确认有效 insert/update 行；被确认行在数据库中全部成功或全部失败 |
| 数据缺失 | 空文件、零数据行、缺少学号/姓名映射、学生不存在和删除前对象消失均返回稳定、可执行的提示 |
| 数据冲突 | 同文件重复以后行为准并显式标记；数据库同学号不同内容显示 before/after；确认、编辑或删除时名单变化返回 409 并要求刷新 |

## Public Test Seams

1. **学生名册 Module seam：** 通过临时 `DBManager` 调用 workspace、preview、commit、update、deletion impact 和 delete，验证解析、revision、事务、备份和错误模式；不测试私有辅助函数。
2. **HTTP seam：** 受控二进制 preview、JSON confirm、分页 workspace、revision 编辑、删除影响与确认删除；验证稳定错误码、响应白名单、无路径/堆栈泄露和 OpenAPI。
3. **Vue user-flow seam：** 文件选择、映射、预览分类、选择有效行、冲突刷新、分页筛选、编辑、影响确认、备份失败恢复和刷新恢复。
4. **匿名真实浏览器 seam：** 固定临时数据库与匿名 CSV/XLSX，在真实 FastAPI + Vue 上完成导入、编辑、删除失败/成功和五档视口；所有写入仅发生在测试临时根。

数据库查询只用于测试准备、故障注入和核对原子/备份不变量；页面测试不依赖私有表实现形状。

## Business Capability and UX Traceability

| 用户任务或业务能力 | 现有业务来源 | 必须保持的语义、结果与安全边界 | 新 Vue 呈现与操作设计 | UX 调整理由 | 业务差异及用户决定 | 验证方式 |
|---|---|---|---|---|---|---|
| 选择 CSV/Excel 名单 | `render_student_import_section()`、`StudentManager` | 只读取 CSV/XLSX 文件字节；不接受客户端磁盘路径；学号和姓名必需，班级可空 | 页面上方可收起的三阶段“导入台”：选择文件 → 校验 → 写入 | 把一次按钮拆成真实、可恢复的业务阶段 | 新 Vue 明确支持 CSV/XLSX；修改前 `.xls` 在便携运行时缺少解析依赖，记录为范围外兼容问题，不在本包新增依赖 | Module/HTTP/浏览器 |
| 字段映射 | `REQUIRED_FIELDS` 别名与 `_standardize_columns()` | 自动识别既有中英文字段；缺必需列时不能写入 | 显示检测列和自动映射；缺失或错误时可手动选择学号/姓名/班级列并重新校验 | 避免要求教师修改原表头 | 无业务语义差异；新增的是已有字段的显式映射控件 | 解析契约与浏览器 |
| 行校验和重复 | `load_students()` 必填过滤、`_deduplicate()` 后行覆盖 | 无学号/姓名不写；同学号最后一行作为实际候选 | 连续预览表显示源行号、状态和原因；被后行覆盖的行不可勾选 | 把原静默规则变成可解释结果 | 无业务结果差异 |  worked examples、HTTP |
| 批量 upsert | `DBManager.upsert_students()` | 学号唯一；新学号插入，已有学号内容变化时更新，相同内容不写；整批事务 | 预览摘要分为新增/更新/不变/无效；只有明确点击“写入所选有效行”才提交 | 教师先看清覆盖范围，再执行写入 | 新增 revision 冲突保护，不改变成功后的数据语义 | 并发/原子测试、浏览器 |
| 查看长名单 | `list_students()` 排序、现有学生字段 | 显示 ID、学号、姓名、班级、创建时间；不增加身份字段 | 紧凑名册台账，搜索姓名/学号/班级，班级筛选和服务器分页 | 数千学生时避免整页加载与卡片海洋 | 无业务差异 | 5,000 行查询与五视口 |
| 编辑单名学生 | Streamlit `data_editor`、`update_student()` | 学号/姓名非空；学号唯一；只修改选中学生 | 右侧/下方检查器内明确“保存修改”，失败保留输入 | 集中显示当前对象与冲突，不在每行堆按钮 | 新增 revision 防后来修改被覆盖 | Module/API/Vue |
| 看清删除影响 | Streamlit 硬删除帮助文案、`delete_student_hard()` | 会删除成绩、题目明细、批注和出勤，解绑答卷；不可恢复；必须先备份 | 检查器先列出准确影响计数，再要求勾选不可恢复确认 | 让高风险结果在点击前可见，不靠模糊警告 | 新增公开影响预检，不改变删除范围 | 影响契约、浏览器 |
| 备份后删除 | `create_backup("delete_student")`、`delete_student_hard()` | 备份失败不得删除；公开响应不得泄露备份路径 | 删除成功显示“备份已创建”和各类清理数；失败保持学生与检查器现场 | 将“有备份”从隐含实现变成可验证结果 | 安全边界增强，无需产品取舍 | 故障注入、事务与响应脱敏 |
| 旧入口继续可用 | 当前 Streamlit 全局资料页 | 新 Vue 未切为生产 UI 时旧入口不删除 | 新导航增加真实可用“学生名单”，不隐藏旧 Streamlit | 遵守 Phase 2 双入口回退 | 无差异 | 回归与快速冒烟 |

## Module and Seam Design

新增学生名册 Module，以 `StudentRosterModule` 作为外部 seam。其依赖属于 **local-substitutable**：生产和测试都使用 `DBManager`，测试传入临时 SQLite；不为单一实现增加假想 adapter。

外部 Interface 保持六个任务级入口：

```python
workspace(query) -> StudentWorkspace
preview_import(filename, content, mapping) -> StudentImportPreview
commit_import(expected_revision, items) -> StudentImportResult
update_student(student_id, expected_revision, values) -> StudentMutationResult
deletion_impact(student_id) -> StudentDeletionImpact
delete_student(student_id, expected_revision, confirmed) -> StudentDeleteResult
```

Interface 隐藏文件类型/大小/编码校验、字段别名、值规范化、源行号、重复后行胜出、数据库 before/after 分类、名单 revision、分页 SQL、`BEGIN IMMEDIATE`、备份一致性和错误净化。路由只把 HTTP 输入翻译为该 Interface，并把 Module 的命名错误映射为稳定状态码。删除该 Module 会迫使同一复杂度重新分散到 Streamlit 解析器、路由、`DBManager` 和 Vue，因而具备足够 Depth 与 Locality。

`StudentManager.load_students()` 保留为旧入口兼容门面，内部可复用 Module 的纯解析实现；既有调用者不需要理解 preview/revision。

## Frontend Design Direction

- **主题、受众和单一任务：** 本机 AI 阅卷系统；维护全校/年级名单的教师；把一份电子表可靠地变成可核对、可维护的“在册学生台账”。
- **色彩：** 只使用现有 Token：纸面白 `#ffffff`、应用灰 `#f6f7f8`、墨色 `#20242a`、次要灰 `#5c6470`、强调蓝 `#2563eb`、风险红 `#b04444`；不新增品牌色、渐变或大面积状态色。
- **字体：** 沿用 Inter / 苹方 / 微软雅黑正文；学号、源行号和计数使用同一 utility 数字排版；不引入新字体依赖。
- **布局：** 标题区保持紧凑；真实三步顺序编码在“导入台”细横轨中；主区是连续名册表和学生检查器，不使用卡片网格。

```text
┌ 学生名单 ─ 当前 5,000 名 ───────────── [导入名单] ┐
├ 选择文件 ── 校验与冲突 ── 写入（可收起导入台）    ┤
├ 搜索姓名/学号/班级  班级筛选          结果与分页 ┤
├──────────────────────────────┬───────────────┤
│ 名册台账：学号｜姓名｜班级｜创建时间 │ 学生检查器    │
│ 连续行、轻分隔、选中行有左侧登记线    │ 编辑 / 删除影响 │
├──────────────────────────────┴───────────────┤
│ 上一页                         第 n 页 / 共 n 页 │
└──────────────────────────────────────────────┘
```

- **签名元素：** 选中行与导入预览共享一条窄“登记线”，像纸质点名册的装订侧标；线上的短文字同时表达新增、更新、无效或当前选中状态，绝不只靠颜色。
- **可访问性：** 原生文件输入、表单标签、可见焦点、表格标题和状态文本；危险操作没有快捷键；`prefers-reduced-motion` 下移除阶段切换动效。
- **自我复核：** 初稿曾考虑“导入卡片 + 学生卡片”，但它会成为通用 SaaS 模板并浪费 1024px 空间；改为真实顺序轨、连续台账和单一检查器。视觉风险只花在登记线这一处，其余保持安静、专业和高密度。

## Planned Interfaces

- `GET /api/students`
  - 保持现有 `items/total` 严格只读契约，继续供扫描和知识图谱筛选使用。
- `GET /api/students/workspace?search=&class_name=&page=&page_size=`
  - 返回当前页、总数、班级选项和不透明 `roster_revision`；排序保持班级、姓名、ID 稳定。
- `POST /api/students/import/preview?filename=<safe-name>&student_code_column=&name_column=&class_name_column=`
  - 请求体为受限二进制 CSV/XLSX；返回检测列、映射、逐行状态、before/after、计数和 `roster_revision`；不落盘、不返回文件内容以外的路径。
- `POST /api/students/import/commit`
  - 只接受 `expected_revision` 和已预览的规范化学生项；同事务重校验 revision、唯一性和必填值后原子 upsert。
- `PATCH /api/students/{student_id}`
  - 请求增加必需 `expected_revision`；冲突返回 409，成功返回学生与新 revision。
- `GET /api/students/{student_id}/deletion-impact`
  - 返回学生摘要、名单 revision 和将删除/解绑的精确计数。
- `DELETE /api/students/{student_id}?expected_revision=<sha>&confirmed=true`
  - 未确认拒绝；名单变化返回 409；备份失败返回稳定错误且零删除；成功只返回计数、`backup_created=true` 和新 revision，不返回备份路径。

公开响应和浏览器状态不得出现数据库路径、备份路径、SQL、堆栈、原始 pandas 错误、任意文件路径或真实业务数据。

## Delivery Slices

### Slice 0：领取与计划

- [x] M2-02 与 P2-13 已通过 PR #55 进入 `origin/main`，功能分支从精确合并 SHA 创建。
- [x] 首个 first-parent 功能提交只包含本即时计划和合法 `in_progress` 交接块。
- [x] handoff validator 通过；功能 worktree 源码与 `user_data/` 状态干净、无 reparse point。
- [x] 记录真实两库只读 SHA-256/大小/UTC mtime，不把绝对路径写入文档。

### Slice 1：学生名册 Module 与 import preview

- [x] RED：CSV/XLSX 正常映射；手动映射；空文件、缺列、不可解码、错误文件魔数；无效行；同文件重复以后行为准；数据库 insert/update/unchanged；5,000 行预览。
- [x] GREEN：最小 `StudentRosterModule.preview_import()` 和安全命名错误；旧 `StudentManager` 兼容调用继续通过。
- [x] RED/GREEN：workspace 搜索、班级筛选、稳定分页和 roster revision；保留既有 `GET /api/students` 响应不变。

### Slice 2：原子确认、编辑和删除保护

- [x] RED：重复/并发 import 只有一个 revision 赢家；任一项错误零写入；重新预览同一数据为 unchanged。
- [x] GREEN：`BEGIN IMMEDIATE` 内重算 revision 并批量 upsert；路由写请求不自动重放。
- [x] RED/GREEN：编辑的非空/唯一/revision 保护和失败保留。
- [x] RED：删除影响计数；备份失败零删除；删除语句故障整批回滚；并发 revision；成功备份不泄露路径。
- [x] GREEN：锁内影响复核、备份和硬删除；旧 Streamlit `delete_student_hard()` 继续兼容。

### Slice 3：HTTP 契约与 Vue 数据层

- [x] RED/GREEN：students schema/router/OpenAPI 的 preview、commit、workspace、edit、impact、delete 契约和稳定错误码。
- [x] RED/GREEN：扩展 `frontend/src/api/students.ts` 与新 store；严格解码、请求世代、AbortController、冲突刷新、写请求不重放。
- [x] 保留 Knowledge Graph 与 Scan Grading 的既有 `fetchStudents()` 行为和严格顶层响应。

### Slice 4：Vue 学生名单工作区

- [x] RED/GREEN：路由、导航和工作台受控入口；导入三阶段、映射、预览分类、选择有效行、写入摘要。
- [x] RED/GREEN：名册台账、搜索、班级筛选、分页、选择检查器、编辑、删除影响和确认。
- [x] 加载、空白、部分无效、冲突、备份失败、禁用、长中文、焦点和刷新恢复状态齐全；CSS 只使用现有 Token。

### Slice 5：匿名浏览器、文档与稳定候选

- [x] 固定匿名 CSV/XLSX 和临时数据库贯通导入、编辑、删除备份失败/成功、刷新和重启恢复。
- [x] 覆盖 1920×1080、1440×900、1366×768、1280×800、1024×768；5,000 行、长姓名、映射错误和焦点无溢出。
- [x] 更新 `ARCHITECTURE.md` 的 P2-13 增量边界；页面可运行并冻结后再生成 quick 自测清单。
- [x] 聚焦测试、受影响回归、`npm run verify`、浏览器 prepared 门槛、`tools/smoke_check.py --skip-tests`、`git diff --check`、handoff validator 和真实两库指纹守卫通过。

## File Map

**Create:**

- `backend/students/__init__.py`、`backend/students/service.py`：学生名册 Module、命名结果和错误。
- `tests/test_student_roster_service.py`：Module seam 的解析、revision、并发、事务、备份和长名单测试。
- `frontend/src/stores/students.ts`：分页名单、导入预览、编辑和删除状态。
- `frontend/src/views/StudentsView.vue`：学生名单工作区。
- `frontend/src/components/students/StudentImportDesk.vue`：选择、映射、预览和确认。
- `frontend/src/components/students/StudentRosterTable.vue`：筛选、连续表格和分页。
- `frontend/src/components/students/StudentInspector.vue`：编辑、影响预检和删除确认。
- `frontend/src/styles/students.css`：登记线、台账、导入阶段与五视口样式。
- `frontend/src/__tests__/students-store.spec.ts`、`students-view.spec.ts`、`students-components.spec.ts`：公开用户流。
- `frontend/e2e/students-real-api.spec.ts`、`frontend/playwright.p2-13-real.config.ts`：匿名真实 API 和五视口。
- `tools/p2_13_browser_server.py`：只创建匿名临时数据库/测试文件的受控浏览器服务。
- `docs/user-testing/checkpoints/P2-13-v1.5.0-students-quick-check.md`：稳定候选后的版本化短测卡。

**Modify:**

- `student_manager.py`：保留旧 Interface，复用新的纯解析规则或明确兼容转换。
- `db_manager.py`：只增加 student snapshot/revision、分页、原子 mutation、影响和锁内备份能力；不改 Schema。
- `backend/api/schemas/students.py`、`backend/api/routers/students.py`：HTTP adapter 和公开模型。
- `backend/api/dependencies.py`：可替换的 `StudentRosterModule` 依赖。
- `backend/api/schemas/__init__.py`、`tests/test_api_write_routes.py`、`tests/test_api_read_routes.py`、`tests/test_api_openapi_contract.py`：契约与回归。
- `frontend/src/api/students.ts`、`frontend/src/api/__tests__/students.spec.ts`：保留旧列表 decoder，增加管理契约。
- `frontend/src/navigation.ts`、`frontend/src/router/index.ts`、`frontend/src/layouts/AppShell.vue` 或现有入口组件、相关导航/路由测试：真实“学生名单”入口。
- `frontend/src/styles/main.css`：只注册新 students 样式。
- `frontend/package.json`：只增加 P2-13 prepared 浏览器命令，不修改依赖或锁文件。
- `ARCHITECTURE.md`：P2-13 已实现事实、接口与数据风险。

## Verification Strategy

- **Python 当前反馈：** 单个 Module worked example → 解析/preview 文件 → transaction/revision/delete 文件 → students HTTP 契约 → 受影响 API、扫描匹配和知识图谱学生读取回归。
- **Frontend 当前反馈：** API decoder → store → components/view → navigation/router；稳定候选统一运行 `npm run verify`。
- **Browser：** 使用固定匿名临时服务；同一已构建 SHA 运行 P2-13 prepared 命令，覆盖 CSV/XLSX、部分无效、冲突、编辑、备份失败/成功、刷新和五档视口。
- **功能分支：** 不运行完整 pytest；交接前运行包内聚焦回归、相关 API/前端回归和 `tools/smoke_check.py --skip-tests`。
- **里程碑：** P2-13 合入 M2-02 后只运行本包受影响验证。P2-16、P2-17 均进入 integration 后，批次末优先运行 2 进程完整门槛；不稳定时退回串行，并运行一次组合前端/浏览器门槛和真实两库指纹守卫。

## Review and Fix Policy

稳定候选冻结后使用 `code-review`，让 Spec 与 Standards 两名评审代理检查同一 SHA。全部意见返回后由主代理统一去重、按根因归组并核对来源；只有本次修改直接引入或当前任务遗漏的 `Critical`/`Important` 阻塞。若有阻塞问题，只做一次统一修复和受影响复测，再由原评审者做一次限定最终复审；最终复审仍有 `Critical`/`Important` 时立即停止并向用户汇报，不自动开始第三轮。

## Stage Record

| 阶段 | 耗时 | 测试/复审 | 原始意见 | 去重结果 | 剩余工作 | 当前包验收 | 版本可发布 |
|---|---:|---|---:|---|---|---|---|
| 集中调查与计划 | 约 35 分钟 | 文档治理、依赖/源码/数据守卫调查 | 0 | 4 个根因组 | 自动门槛、复审与用户短测 | 否 | 否 |
| 稳定候选 | 约 2 小时 20 分钟 | Python 聚焦 46 项；前端 591 项、lint/typecheck/build；匿名真实浏览器；快速冒烟；格式、交接与两库指纹守卫 | 0 | 0 | 双路初审与用户短测 | 否 | 否 |
| 初审 | 约 8 分钟 | Spec + Standards 同查 `a98de6a7ff9abe39e7ddaaa7d5a6ceb6cebd07ef` | 3 | 3 个 Important：影响请求串线、表格缺记录号/建立时间、删除成功缺清理数 | 统一修复与限定复审 | 否 | 否 |
| 统一修复 | 约 10 分钟 | 先以 3 个失败断言复现；修复后受影响单元 8 项、lint/typecheck/build 和匿名真实浏览器通过 | 3 | 3 个 Important 已统一修复 | 原评审者限定最终复审 | 否 | 否 |
| 最终复审 | 约 3 分钟 | 原 Spec/Standards 评审者仅核对 3 项首轮问题、修复区与直接回归 | 0 | 0；3 个 Important 均已解决 | 用户短测 | 否 | 否 |

## Ownership

P2-13 功能分支拥有学生名册 Module、students router/schema 的包内增量、临时数据库测试、Vue 学生名单工作区、匿名浏览器工具、即时计划、P2-13 架构增量和 quick 自测清单。integration 负责最终 Index 状态、里程碑精确 SHA、共享入口冲突和 M2-02 完整门槛；功能分支不宣称自己已进入主线。

## Rollback and Stop Conditions

- 功能回退为整体 revert P2-13 提交并继续使用旧 Streamlit 学生名单入口；不删除任何既有学生、备份或结果。
- 发现必须增加/迁移数据库 Schema、改变学号唯一语义、答卷匹配/评分/报告口径、引入账号体系或 Phase 6 学生画像时停止。
- `.xls` 兼容需要新增运行依赖；本包不擅自扩大依赖锁，作为修改前兼容问题单独记录。
- 测试需要真实学生数据、真实数据库写入、真实密钥、真实模型或不可逆文件操作时停止。
- 删除备份无法在写锁内证明一致，或真实两库指纹/大小/UTC mtime未经授权变化时立即停止。
- 同一包连续两次修复失败，或最终复审仍有 `Critical`/`Important` 时停止。

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P2-13
**交接状态：** waiting_user
**功能提交：** 19dedc590041555dcc9f3a269b853be26b846962
**自动验证：** passed
**独立复审：** passed
**用户验收：** pending
**真实数据指纹：** not_touched
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
