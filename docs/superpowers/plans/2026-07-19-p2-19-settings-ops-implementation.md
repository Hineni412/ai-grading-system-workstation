# P2-19 设置、系统自检与运维入口即时实现计划

**执行包：** P2-19
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** no
**计划基线：** 3076b43ebf63ba8f2317faf2ea2988c0f5915f7d
**交接基线：** 3076b43ebf63ba8f2317faf2ea2988c0f5915f7d
**用户自测：** quick
**自测清单：** docs/user-testing/checkpoints/P2-19-v1.5.0-settings-ops-quick-check.md

> 用户于 2026-07-19 明确要求启动 P2-19。Phase map、P1-22/P1-23 已实现的 Ops API/Job/File 契约、现有 Streamlit 自检与数据管理任务，以及下列 Public Test Seams 共同冻结范围。用户尚未对最终可见页面给出 quick 验收结论，因此交接前必须停在可运行的已复审版本等待用户实际短测。

## Objective

在不修改数据库 Schema、现有运维语义、API profile 持久化、启动入口或真实业务数据的前提下，交付独立 Vue“设置与运维”工作区：教师可查看脱敏的系统版本、API 配置布尔状态、逻辑目录、数据库完整性/迁移摘要、外部工具和备份清单；可复制不含路径、密钥或业务正文的诊断摘要；可对备份、恢复、数据库迁移、数据包导入和导出执行严格的“预检—二次确认—独立 Job—必要时重启—最终状态/恢复说明”闭环。

## Frozen Boundary

- **目标：** 迁移设置区域的只读系统自检、备份记录与 P1-23 五类受保护 Ops 写入口；让危险动作的影响、确认、进度、是否已经写入、是否需要重启和恢复方式对教师清楚可见。
- **包含：** `GET /api/ops/self-check`；`GET /api/ops/backups`；ZIP 暂存上传；五类严格预检；5 分钟单次确认令牌；Ops Job 提交/刷新恢复/取消；在线备份与数据包导出下载；离线恢复/迁移/导入的 operation 状态查询与准备后撤销；Vue 路由/导航；脱敏诊断复制；五档 Windows 桌面视口；quick 用户短测。
- **明确不包含：** 数据库迁移文件或 Schema 修改；直接读写真实 `user_data/`；真实备份、恢复、迁移、导入或导出；API Key 查看、录入、编辑或健康请求；任意路径、目录、命令、SQL 或迁移文件选择；删除备份；自动重启工作机应用；远程/多用户权限；生产 UI 切换；旧 Streamlit 页面删除；P2-20 正式五流程验收。
- **验收条件：** 只读状态与现有 Ops 服务结果一致；响应和复制文本不含路径、密钥或业务正文；五类动作都必须先预检并在独立安全闸门中二次确认；写请求不自动重放；Job 刷新后可恢复；离线准备成功只显示“等待重启应用”，不能显示为已经应用；失败明确说明是否已写入、是否可重试及恢复方式；在线产物可受控下载；五档视口无意外横向溢出；受影响自动测试、临时数据根真实 API 浏览器、快速冒烟、交接验证和真实两库指纹守卫通过。
- **风险等级：** 高。页面本身主要是前端编排，但它暴露备份、恢复、迁移和数据传输写入口；任何确认降级、状态误报、自动重放或真实数据根误用都可能造成数据错乱、文件丢失或不可恢复覆盖。

## Public Test Seams

测试只允许观察以下公共接口，不绑定 Vue 内部组件层级、私有函数、数据库行或后端实现调用次数：

1. 类型化 Ops API：自检、备份清单、上传、预检、提交、operation 查询/撤销和受控 Job 下载。
2. Ops Pinia Store：同一时间一个运维流程；请求世代隔离；预检、确认、Job、离线 operation 与失败恢复的公开状态和动作。
3. `/settings` 页面：教师看到的系统状态、备份记录、危险动作隔离、确认摘要、进度、重启提示、恢复说明、下载和脱敏诊断复制。
4. 路由与导航：设置入口可达，刷新后恢复当前浏览器已记录的 Ops Job，旧页面与生产入口不受影响。
5. 临时数据根真实 API 浏览器：五类预检、至少一类在线 Job/下载、一类离线准备/重启提示/撤销及五档视口；禁止指向根目录真实数据。

上述 seams 已由正式包定义和现有公开契约确定；用户启动 P2-19 即授权按该冻结边界测试与实现，不需要为内部文件组织另行确认。

## Business Capability and UX Traceability

| 用户任务或业务能力 | 现有业务来源 | 必须保持的语义、结果与安全边界 | 新 Vue 呈现与操作设计 | UX 调整理由 | 业务差异及用户决定 | 验证方式 |
|---|---|---|---|---|---|---|
| 查看系统是否可用 | `OpsSelfCheckService`、Ops schema/API 测试、旧“系统自检”任务 | 只返回版本、布尔 API 配置、逻辑目录状态、数据库候选完整性/迁移摘要、工具布尔状态；不返回路径或密钥 | 页面顶部使用“系统状态账本”，按目录、数据库、工具分组，异常先行且保留完整明细 | 比旧页面的指标块更便于逐项排障，不改变状态含义 | 无业务差异 | API decoder、store、view、真实 API 浏览器 |
| 复制诊断信息 | Phase map“复制脱敏诊断信息”、P1-22 公开安全投影 | 只能由公开字段生成；不得包含路径、密钥、业务正文或原始异常 | 生成可粘贴的中文纯文本摘要并明确标注“已脱敏” | 便于非技术用户求助，避免手工截图泄露 | 无业务差异 | 固定 worked example、剪贴板测试、敏感字符串守卫 |
| 查看已有备份 | `GET /api/ops/backups`、备份 schema/API 测试 | 有界清单；只显示安全文件名、类型、时间、原因和大小 | 与创建/恢复入口相邻的高密度列表；ZIP 可作为恢复候选，DB 自动备份只读展示 | 让“先备份再恢复”的因果关系可见 | 无业务差异 | decoder/store/view |
| 创建备份 | P1-23 backup 预检/Job/File 契约 | 固定原因；API profile 永不包含；预检和确认令牌必需；发布前可取消；成功后受控下载 | 普通操作区选择原因，点击“预检备份”；确认只在右侧安全闸门完成 | 不把危险确认和普通浏览混在一起 | 无业务差异 | API/store/view/真实临时数据根 |
| 恢复备份 | P1-23 restore 契约与已批准设计 | 只接受受控 ZIP 文件名；先安全备份；准备成功仍未应用；下次双服务启动前离线应用；可在 applying 前撤销 | 备份行选择后进入独立危险面板，明确“覆盖包内同名文件、不删除其他文件”，展示重启与回退状态 | 比旧命令行说明更清楚，同时保持更高安全门槛 | 无业务差异 | 409/404/失败/撤销/重启状态测试 |
| 数据库迁移 | P1-23 migration 契约、迁移工具测试 | 只接受 grading/question_bank/all；只在候选副本预演；应用前再备份；all 失败整体回退 | 选择数据库范围后预检，摘要展示待迁移数和候选校验；确认区明确需要重启 | 把 dry-run 与正式应用分开，避免旧页面直接执行 | 无业务差异 | API/store/view；临时双库 |
| 导出数据包 | P1-23 transfer_export 契约、现有 lean/full 语义 | 只接受 lean/full；排除 API profile；在线 Job 原子发布；受控下载 | 轻量/完整并列说明，预检显示文件数、体积和跳过项，再确认并跟踪下载 | 让包范围和代价在提交前可见 | 无业务差异 | API/store/view/download |
| 导入数据包 | P1-23 upload/transfer_import 契约 | 只接受受控 ZIP；大小/成员/路径/数据库候选严格校验；备份失败阻断；准备后重启应用；不删除包外文件 | 先上传暂存，再只读预检，再输入短语确认；上传与确认状态分离 | 取消旧 Streamlit“备份失败仍继续”的危险体验 | 无业务差异；采用共同基线中已批准的 P1-23 安全语义 | 上传、422/413/415、Job、operation、浏览器 |
| 取消与失败恢复 | P1-23 并发/取消/重启设计、Job Store 契约 | queued/running Job 协作式取消；prepared/restart_required 用专用 operation 撤销；applying 不可强行中断；写请求不自动重放 | 根据当前阶段只显示合法动作；模糊写失败显示“结果未知”，要求刷新状态而不是自动再提交 | 避免重复危险操作和错误成功提示 | 无业务差异 | store race/refresh tests、真实浏览器 |

## Failure Scenarios

| 场景 | 预期结果 |
|---|---|
| 重复点击或重复提交 | 页面立即锁定当前提交；同一确认令牌只使用一次；409 显示令牌已用/预检过期并要求重新预检，不自动重放 |
| 两个标签页同时操作 | 后端单一 Ops 锁裁决；失败标签页显示当前已有运维操作，保留自检与备份内容，不伪装排队 |
| 预检后中途退出 | 令牌不持久化；刷新后必须重新预检；暂存上传 ID 仅在当前页面内使用，不推断执行成功 |
| Job 提交响应丢失 | 不自动重试；显示结果未知并刷新当前浏览器已记录任务/自检/备份；若没有 Job ID，要求重新检查后再预检，不能声称未执行 |
| Job 运行时刷新或重启浏览器 | 通用 Job Store 按当前浏览器记录的 Job ID 恢复；终态不会被旧响应倒退 |
| 应用重启 | 从 Job payload/result 恢复 operation ID；查询 Journal 投影，区分 restart_required、applied、rolled_back、failed 和 cancelled |
| 失败重试 | GET 可安全重试；写操作只能由教师重新完成预检和确认；失败 Job 不直接复用旧令牌 |
| 取消 | queued/running Job 使用 Job 取消；离线准备完成使用 operation 撤销；applying 后禁用取消并解释不能强停 |
| 部分完成 | Job `succeeded` 对恢复/迁移/导入只表示准备完成；只有 operation `applied` 才显示已生效；`rolled_back` 显示未生效且已回退 |
| 数据缺失或冲突 | 备份消失、上传无效、迁移集合变化、预检过期或锁冲突均 fail closed；页面保留上一份只读状态并显示可执行下一步 |
| 临时数据根误配为真实根 | 浏览器验收服务器在启动前比对受控临时根；发现仓库真实 `user_data` 路径或真实两库指纹变化立即停止 |

## Frontend Design Plan

### Subject and single job

- **具体对象：** Windows 本机阅卷系统的教师运维工作区。
- **受众：** 不具备命令行经验、但需要判断系统是否安全可用并执行有限维护动作的教师。
- **页面唯一工作：** 先看清系统状态，再通过不会跳步的安全闸门完成一次受保护运维操作。

### Tokens

- **纸面白 `#ffffff`：** 主要账本内容面，对应 `--color-bg-surface`。
- **工作台灰 `#f6f7f8`：** 页面背景，对应 `--color-bg-app`。
- **结构灰 `#d9dde2`：** 账本分隔，对应 `--color-border-default`。
- **操作蓝 `#2563eb`：** 普通选择与刷新，对应 `--color-accent`。
- **警戒褐 `#9a6718`：** 等待、预检和需要重启，对应 `--color-warning`。
- **危险红 `#b04444`：** 最终危险确认与不可继续状态，对应 `--color-danger`。
- 字体严格复用 STYLE 的 Inter/苹方/微软雅黑系统栈；页面标题 22–24px，正文 14px，账本行 13px；版本、大小、迁移数使用 tabular numerals，不引入新字体或依赖。

### Layout and signature

```text
┌────────────────────────────────────────────────────────────┐
│ 设置与运维  [整体状态] [刷新] [复制已脱敏诊断]              │
├──────────────────────────────┬─────────────────────────────┤
│ 系统状态账本                 │ 安全闸门                    │
│ 目录 / 数据库 / 工具 / API   │ 预检 → 确认 → Job → 重启/结果│
├──────────────────────────────┴─────────────────────────────┤
│ 备份与恢复账本                                             │
├──────────────────────────────┬─────────────────────────────┤
│ 数据库迁移                   │ 数据包导入与导出            │
└──────────────────────────────┴─────────────────────────────┘
```

- **记忆点：** 右侧“安全闸门”是唯一有阶段轨道的区域；四个节点严格对应真实业务阶段，不用于装饰。它让教师始终知道当前只是预检、后台准备、等待重启，还是已经应用/回退。
- **克制原则：** 不做指标卡海洋、不做营销 Hero、不做渐变；普通状态使用账本行和细分隔。危险红只花在最终确认和不可继续结果上。
- **紧凑桌面：** 1280px 以上双栏；1024–1279px 安全闸门进入主内容流顶部并保持阶段顺序，业务字段不隐藏。
- **设计自检：** 初始设想若使用彩色健康卡片和五张操作卡，会像通用运维大屏并弱化真实因果；已改为“状态账本 + 单一安全闸门”，颜色只承担真实状态，结构只编码实际操作顺序。

## Implementation Slices

### Task 1: 领取与基线守卫

- [x] 首个 first-parent 提交只包含本即时计划和合法 `in_progress` 交接块。
- [x] `tools/handoff_status.py` 以 integration 现场的 `3076b43...` 可信输入验证领取关系。
- [x] 功能 worktree 源码与 `user_data/` 干净；除内部前端依赖链接外无外部 reparse point。
- [x] 真实两库只读基线已记录，后续只比较不写入。
- [x] 第二个文档提交把功能分支所见 Index 的 P2-19/M2-03 当前动作改为 `in_progress`。

### Task 2: 类型化 Ops API

- [x] RED：自检、备份、上传、五类预检、Job 提交、operation 查询/撤销和下载的合法 worked examples。
- [x] RED：路径样字段、密钥、未知状态、非法 filename/operation、越界数字和畸形摘要 fail closed。
- [x] GREEN：新增 `frontend/src/api/ops.ts`，只使用同源固定 Ops 路径和现有 `apiClient`，写请求单次发送。

### Task 3: Ops Store 状态机

- [x] RED：只读刷新保留最后成功内容；旧响应隔离；预检失效；重复提交锁；Job 刷新恢复；在线下载；离线 operation 查询/撤销。
- [x] RED：模糊写失败、双标签锁冲突、取消竞态、prepared 与 applied 区分、rolled_back/failed 恢复说明。
- [x] GREEN：新增 `frontend/src/stores/ops.ts`，复用通用 Job Store，不创建第二套轮询器，不持久化令牌或上传正文。

### Task 4: 设置与运维页面

- [x] RED：系统状态账本、脱敏诊断复制、备份列表、五类入口、唯一安全闸门、确认短语、阶段合法动作、加载/空白/错误/禁用/焦点。
- [x] RED：危险按钮不与普通主按钮并列；离线 Job succeeded 不显示已应用；1024px 长中文/长文件名不溢出。
- [x] GREEN：新增 `SettingsOpsView.vue` 与专用 Token 化 CSS；接入导航、路由和 main 样式。

### Task 5: 临时数据根真实 API 浏览器

- [x] 使用临时双库、临时备份/输出/本机状态区和固定无密钥配置启动真实 FastAPI + Vue。
- [x] 覆盖只读自检、五类预检、备份确认/Job/下载、恢复准备/重启提示/撤销、失败恢复与脱敏复制。
- [x] 验证 1920×1080、1440×900、1366×768、1280×800、1024×768，无控制台错误和意外横向溢出。

### Task 6: 稳定候选、复审与交接

- [x] 运行包内前端/API 测试、受影响 Ops 后端回归、`npm run verify`、同一构建的真实浏览器流程和 `tools/smoke_check.py --skip-tests`。
- [x] 更新 `ARCHITECTURE.md`、即时计划阶段记录和版本化 quick 清单；记录真实两库指纹未变。
- [x] 冻结候选 SHA，按 Spec/Standards 双路复审；阻塞项统一修补一次，并由原评审者完成限定最终复审。
- [ ] 独立复审通过后生成只绑定已复审 SHA 的 quick 清单，等待用户实际短测并明确给出 `passed` 或问题。
- [ ] 用户通过后完成 evidence/交接提交，验证 `verified_pending_integration`，再合入 M2-03 integration 并运行 P2-19 逐包受影响门槛。

## Expected Files

- `frontend/src/api/ops.ts`
- `frontend/src/api/__tests__/ops.spec.ts`
- `frontend/src/stores/ops.ts`
- `frontend/src/__tests__/ops-store.spec.ts`
- `frontend/src/views/SettingsOpsView.vue`
- `frontend/src/__tests__/settings-ops-view.spec.ts`
- `frontend/src/styles/settings-ops.css`
- `frontend/src/navigation.ts`
- `frontend/src/router/index.ts`
- `frontend/src/__tests__/navigation-router.spec.ts`
- `frontend/src/main.ts`
- `tools/p2_19_browser_server.py`
- `ARCHITECTURE.md`
- `docs/user-testing/checkpoints/P2-19-v1.5.0-settings-ops-quick-check.md`
- `docs/superpowers/packages/EXECUTION_INDEX.md`
- 本即时计划

不修改后端 Ops 契约、数据库迁移、依赖锁、生产启动入口或真实 `user_data/`；若调查证明前端无法在现有公开契约上完成某项冻结验收，先安全停机并把缺口作为 P2-19 当前任务遗漏复审，不凭猜测扩大后端。

## Test Commands

- 单切片：`npm run test -- --run <ops-test-file>`
- 前端门槛：`npm run verify`
- 后端受影响：根目录便携 Python 在功能 worktree 运行 Ops API/服务/Job/File/迁移/数据传输聚焦测试。
- 浏览器：先使用同一源码的 `npm run build`，再以 Playwright CLI 驱动受控临时 FastAPI + Vue，保存五档视口与下载证据到忽略的 `output/playwright/p2-19/`。
- 快速冒烟：根目录便携 Python 在功能 worktree 运行 `tools/smoke_check.py --skip-tests`。
- 功能分支不运行完整 pytest；P2-19 进入 integration 后只跑受影响验证，M2-03 三包完成后才运行一次批次末完整门槛。

## Review Rules

稳定候选冻结后使用 `code-review` skill，让 Spec 与 Standards 两名评审代理检查同一 SHA。全部意见返回后由主代理统一去重、按根因归组并核对来源；只有本次修改直接引入或当前任务遗漏的 `Critical`/`Important` 阻塞。若有阻塞问题，只做一次统一修复和受影响复测，再由原评审者做限定最终复审；最终复审仍有当前范围内 Critical/Important 时停止并向用户汇报，不开始第三轮。

## Stage Record

| 阶段 | 耗时 | 测试/复审 | 原始意见 | 去重结果 | 剩余工作 | 当前包验收 | 版本可发布 |
|---|---:|---|---:|---|---|---|---|
| 集中调查与计划 | 14 分钟 | 必读资料、P1-22/P1-23 规格与 Ops API/Job/File、旧自检/数据管理、前端 API/Job/导航基础设施、worktree/数据守卫 | 0 | 6 个根因组：公开契约、确认与并发、Job/重启恢复、文件下载、脱敏、视觉与可达性 | 已完成 | 否 | 否 |
| TDD 实现 | 34 分钟 | API、Store、页面、路由与导航测试；相关测试 83 passed | 0 | 0 Critical / 0 Important | 自动门槛与真实浏览器 | 否 | 否 |
| 稳定候选验证 | 31 分钟 | 前端 verify 700 passed；Ops 后端 98 passed；五类预检、备份下载、恢复准备/撤销、五档视口；快速冒烟 | 4 个实现期问题 | 4 个已统一修复；另有 1 个 P1-23 修改前导出问题单独登记 | 双路复审、quick；既有导出问题待裁定 | 否 | 否 |

## Initial review and unified fix

- 首轮冻结候选：`3c8a5e73c02b202505410b12bd8498ac849f1119`。
- Spec 与 Standards 两路复审检查同一冻结版本；原始意见 5 条，去重后仍为 5 条：4 条 `Important`、1 条 `Suggestion`。
- 当前任务阻塞项 3 条，已在一个统一修复批次中处理：
  1. 危险提交请求尚未返回时立即锁定全部受保护入口，避免上传、预检或第二流程覆盖在途结果。
  2. 预检区补全备份原因、体积、展开体积、敏感项跳过及恢复覆盖边界。
  3. 离线准备失败时明确说明业务数据尚未应用、可能已经创建安全备份，并引导先检查备份清单。
- 修改前已有的导出失败 1 条单独登记，不在 P2-19 内修改；映射文案去重 1 条为非阻塞建议，不扩大本包。
- 统一修复验证：新增/受影响检查 21 passed；相关回归 115 passed；前端完整门槛 70 files / 702 tests passed，lint、typecheck、production build 均通过。
- 限定最终复审：首轮两位原评审者检查统一修复候选 `df024058a60f138a11bcdc8301c078da7a897963`；原始意见 0 条、去重后 0 条，当前范围 `Critical`/`Important` 均为 0。
- 当前包实现与独立复审已经通过；修改前已有的 `transfer_export` 故障仍是外部验收阻塞。剩余工作是由用户决定是否开启独立 P1-23 修复任务，问题解决后再进行 P2-19 quick 验收。

## Out-of-scope finding

- **修改前已经存在 / Important：** 临时真实 API 中，`transfer_export` 预检后正常提交可因在线 SQLite 相关文件造成资源指纹变化而失败，Job 内部原因为 `preflight resource changed`。P2-19 未修改后端 Ops 实现，98 项既有聚焦回归通过；本包前端已修正为如实显示失败、没有产物且不自动重试。该问题属于 P1-23 既有实现，按范围规则不在本包自动修改；是否阻止 P2-19 验收由复审统一裁定。

## Rollback and Stop Conditions

- 功能回退为整体 revert P2-19 提交并继续使用旧 Streamlit 自检/数据管理入口；不删除已生成的备份、导出或离线 operation Journal。
- 任何浏览器或测试路径指向根目录真实 `user_data`、真实 API profile、真实模型或外部目录时立即停止。
- 任何公开响应/复制文本泄露内部路径、密钥、确认令牌、业务正文或原始异常时立即停止。
- 需要改变 P1-23 操作状态、确认令牌、离线应用或回退语义时停止并复核，不在前端自行发明替代状态。
- 同一包连续两次统一修复仍失败，或最终复审仍有 Critical/Important 时停止并汇报。

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P2-19
**交接状态：** waiting_user
**功能提交：** df024058a60f138a11bcdc8301c078da7a897963
**自动验证：** passed
**独立复审：** passed
**用户验收：** pending
**真实数据指纹：** not_touched
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
