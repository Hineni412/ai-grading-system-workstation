# P2-22：Streamlit UI 与旧前端依赖退役

**执行包：** P2-22
**计划日期：** 2026-07-23
**规划状态：** ready_for_review
**规划模型：** 当前白天高风险删除模型
**允许夜间执行：** no
**计划基线：** 6305a1442ecd7bd13c6122e43dac7d6d771e2aeb
**用户自测：** formal
**自测清单：** docs/user-testing/checkpoints/P2-22-v1.5.0-streamlit-retirement-formal.md

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P2-22
**交接状态：** waiting_review
**功能提交：** branch_head
**自动验证：** passed
**独立复审：** pending
**用户验收：** pending
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->

## 任务边界（已冻结）

### 目标

在 P2-21 默认 Vue/FastAPI 入口稳定完成一个正式验收周期后，删除已迁移的 Streamlit 日常 UI、页面共享层、旧 Streamlit 答题区包装器和旧桌面启动器；删除已无调用的 `streamlit-drawable-canvas` 依赖。默认启动、便携包和发布说明只保留 Vue/FastAPI。

### 包含

- 删除 `web_app.py`。
- 删除 `pages/` 下组卷、题库管理、系统自检、训练推荐、知识点整理五个旧页面。
- 删除 `pages_shared/` 的 Streamlit 共享组件、样式、旧状态辅助和 sortable 资产。
- 删除 `answer_region_editor_component.py`、`answer_region_focus_page.py` 和 `run_desktop.py`。
- 保留并继续构建 Vue 正在直接复用的 `components/answer_region_editor/` 原生 DOM/SVG 核心。
- `运行.bat` 删除 `USE_STREAMLIT`、`START_API`、8501 和 `web_app.py` 回退分支，只保留启动前离线安全操作与 Vue/FastAPI 前台入口。
- 便携打包不再复制 `pages/`，运行时自检和私人说明不再要求或宣传 Streamlit 回退。
- 删除 `streamlit-drawable-canvas` 的 requirements/constraints 声明。
- 删除只验证退役 Streamlit 页面或包装器的测试；混合测试文件只删除对应源码扫描断言，保留服务、数据库和并发行为测试。
- 新增退役静态守卫、启动/打包契约和版本化正式用户验收清单。
- 更新 `ARCHITECTURE.md`、`AGENTS.md` 常用启动说明和工作机说明中的当前入口事实。

### 明确不包含

- 不删除 Vue 复用的 `components/answer_region_editor/`。
- 不删除或改写任何已下沉业务服务、API、数据库 Schema、迁移、评分规则、失败卷语义、报表格式或真实数据。
- 不删除 `objective_admission_wizard_ui.py`、`objective_crop_calibration.py` 或其校准链；停用向导属于 P3-15。
- 不删除或改写历史 P1-29 双入口验收工具；其历史重放仍需要 Streamlit。
- 因上述两个明确保留项，暂保留 Streamlit 核心依赖；本包只移除已确定零调用的 `streamlit-drawable-canvas`。剩余核心依赖在 P3-15/P3-19 的调用方归零后再处理。
- 不删除 `main.py` 旧 CLI、旧数据库表或其他 Phase 3 退役对象。
- 不读取、复制、修改、删除、暂存或提交根目录真实 `user_data/`。
- 不调用真实模型，不使用真实密钥。

### 验收条件

- 所有列入删除清单的路径均不存在，且 Git 可按独立提交恢复。
- 非历史文档、非明确保留项中，对 `web_app.py`、`pages/`、`pages_shared/`、旧包装器、`USE_STREAMLIT` 和 `streamlit-drawable-canvas` 的运行调用为零。
- `运行.bat` 仍先执行离线待处理操作，再只启动 loopback Vue/FastAPI；缺少 dist、端口占用和服务异常继续安全失败。
- 便携包包含完整后端、`frontend/dist` 与唯一启动器，不复制旧页面，不要求 Node，不宣传或尝试旧回退。
- Vue 复用的答题区核心仍能通过组件测试、类型检查和生产构建。
- 删除 UI 测试后，相关服务/API/数据库行为测试仍保留并通过；完整串行门槛、前端 verify、快速冒烟和便携包验收通过。
- 正式用户清单覆盖默认启动、深路由刷新、考试配置/答题区、学生/题库/组卷、批改复核/文件与设置运维等五类流程；Blocker/Major 清零并由用户明确标记 `passed`。
- 验证前后真实两库 SHA-256、大小和 UTC 修改时间不变，真实 `user_data/` 无新增 Git 变更。

### 风险等级

最高。虽然删除的是 Git 可恢复代码而非业务数据，但错误删除可能让某项已迁移能力不可达、让便携包缺文件、让故障回退失效，或误删 Vue 正在复用的编辑器核心。因此删除清单、调用方守卫、完整测试、便携包和用户正式验收缺一不可。

## 业务能力与 UX 设计溯源表

| 用户任务或业务能力 | 现有业务来源 | 必须保持的语义、结果与安全边界 | 退役后的 Vue 入口 | 退役理由 | 业务差异及用户决定 | 验证方式 |
|---|---|---|---|---|---|---|
| 启动、刷新和恢复当前页面 | P2-21 启动器、FastAPI hosting、Vue Router、正式验收 | loopback、离线操作先执行、API 不被 SPA fallback 吞掉、深路由可刷新 | `运行.bat` → `/` | P2-21 已完成一个正式验收周期，双入口观察期结束 | 用户于 2026-07-23 明确启动 P2-22，接受退役旧回退 | 启动契约、浏览器、便携包 |
| 考试配置、评分依据和样卷题框 | 现有服务/API、P2-09/P2-10 测试与验收 | 配置 revision、来源绑定、题框坐标/指纹、冲突与正式确认不变 | `/sessions`、`/sessions/:id/regions` | 已迁移能力集中在连续 Vue 工作台 | 无业务差异 | API/服务回归、Vue 组件与浏览器 |
| 答卷上传、扫描匹配和批改运行 | P2-11 服务/API、运行账本和测试 | 上传冻结、人工决定、暂停/恢复/取消、失败重试和费用保护不变 | `/sessions/:id/grading-run` | 旧 Streamlit 入口不再承担生产流程 | 无业务差异 | grading/scan 回归、浏览器 |
| 学生、题库、组卷和训练 | P2-13/P2-16/P2-17/P2-18 服务、API 与测试 | 学生删除保护、题库写入、组卷草稿、精确标签训练语义不变 | `/students`、`/question-bank`、`/assembly`、`/training` | 五个旧 pages 已有对应 Vue 工作区 | 无业务差异 | 服务/API/前端回归、浏览器 |
| 复核、报告、文件和运维 | P2-05—P2-08、P2-12、P2-19 服务/API 与验收 | 教师分优先、写请求不自动重放、受控下载、运维确认令牌与离线恢复不变 | `/grading`、`/files`、`/settings` | 新入口已形成统一工作流 | 无业务差异 | review/report/ops 回归、浏览器 |
| 停用客观题向导与历史 P1-29 重放 | P3-15 包定义、`tools/p1_29_acceptance.py` | 不抢先删除后续包对象，不把历史工具误当生产入口 | 不出现在日常导航 | 明确保留到对应后续包 | 无 | 静态允许列表和零生产调用守卫 |

## 集中调查与冻结问题清单

1. 当前生产默认已经是 Vue/FastAPI，但 `运行.bat` 仍保留 `USE_STREAMLIT=1`、`START_API`、8501 与 `web_app.py` 回退分支。
2. 旧 UI 共包含 `web_app.py`、5 个 `pages/` 页面、7 个 `pages_shared/` Python/静态文件、2 个 Streamlit 答题区包装器和 `run_desktop.py`；Vue 仍直接复用 `components/answer_region_editor/`，该目录不能删除。
3. `package_v1.5.0.py` 仍复制 `pages/`，运行时自检强制 import Streamlit，私人说明仍宣传回退。
4. `requirements.txt` 与 `constraints.txt` 仍包含 `streamlit-drawable-canvas`；其唯一生产调用均位于待删除 `web_app.py`，可以移除。
5. `objective_admission_wizard_ui.py` 和 P1-29 历史验收工具仍引用 Streamlit，但不在生产启动/导航调用图中，且分别属于后续包与历史重放；因此 Streamlit 核心依赖不能在本包强删。
6. 现有多批测试通过读取旧页面源码固定 UI 行为。纯 UI 文件应随退役删除；混合文件须保留服务/事务测试，只移除旧源码断言。
7. `ARCHITECTURE.md`、`AGENTS.md` 和工作机说明仍把 Streamlit 记为当前或常用入口，必须同步为 Vue/FastAPI 单入口事实；历史计划与已完成验收证据保持原文。

以上问题去重为四个根因：旧生产回退仍可达、便携发布仍携带旧 UI、测试把退役页面当活动契约、当前架构说明尚未收口。

## 故障场景与预期

| 场景 | 预期处理 |
|---|---|
| 重复启动或端口占用 | 后启动实例明确失败，不自动切回 Streamlit，不执行额外数据操作 |
| 缺少 `frontend/dist` | 在离线数据操作前失败关闭，提示修复便携包；不再提供已退役回退 |
| 删除中途退出 | 每个删除批次为独立 Git 提交；未完成候选不得进入 integration，可按提交恢复 |
| 误删仍有调用的文件 | 静态调用方守卫、Python 编译、前后端构建和完整测试阻断候选 |
| 便携包漏带服务或前端资源 | 打包契约和隔离便携启动验收阻断发布 |
| 用户仍尝试 `USE_STREAMLIT=1` | 环境变量不再改变入口；系统始终启动 Vue/FastAPI，发布说明不再宣传旧开关 |
| 旧测试随 UI 删除造成业务保护丢失 | 纯 UI 测试可删除；服务/事务/并发测试必须保留，混合文件只移除源码断言 |
| 回退 P2-22 | revert 独立删除/依赖/文档提交即可恢复旧入口；不涉及数据库恢复 |
| 真实数据缺失、冲突或正在使用 | 本包不打开或写真实库；只比对文件指纹，所有运行验证用空白/合成隔离数据 |

## 实施步骤

- [x] 新增退役契约 RED：删除路径必须不存在、启动器不得有旧回退、打包不得复制 pages/ 或要求旧依赖、明确保留项以外不得存在生产 Streamlit 调用。
- [x] 第一删除批次：移除 `web_app.py`、旧答题区 Streamlit 包装器、`run_desktop.py` 及其纯 UI 测试；保留原生编辑器核心和对应 Vue 测试。
- [x] 第二删除批次：移除 `pages/`、`pages_shared/` 及纯页面测试；清理混合测试中的旧源码扫描，保留服务、数据库、并发和兼容测试。
- [x] 第三收口批次：简化 `运行.bat`，更新便携打包清单/运行时自检/私人说明，移除 `streamlit-drawable-canvas` 声明。
- [x] 更新当前架构与工作机说明，保留历史计划/验收证据原文；记录暂留 Streamlit 核心依赖的精确允许列表和后续包。
- [x] 运行退役契约、启动/托管/打包聚焦测试、所有受影响后端回归和前端组件测试。
- [x] 运行一次前端 `npm run verify` 与生产 build；生成隔离便携目录并验证无 Node 启动、根/深路由、未知 API、缺失资源和端口占用。
- [x] 运行串行完整后端门槛、快速冒烟和真实两库指纹守卫；冻结稳定候选。
- [x] 对同一冻结候选并行执行需求符合性和代码质量初审；4 个 `Important` 已归并并在唯一一次统一修复中处理，等待原复审代理限定终审。
- [ ] 独立复审通过后创建 `waiting_user` 锚点，生成版本化正式清单并交给用户完成五类流程验收。
- [ ] 用户明确 `passed` 后只提交清单证据和最终交接；合入 `codex/integration-p2-22` 逐包验证，再 push、PR 合入 `main` 并同步状态。

## 预计验证命令

```powershell
runtime\python\python.exe -m pytest tests\test_p2_22_streamlit_retirement.py tests\test_run_bat_api_entry.py tests\test_frontend_portable_packaging.py -q
runtime\python\python.exe -m pytest tests\test_api_frontend_hosting.py tests\test_api_app.py tests\test_api_openapi_contract.py tests\test_portable_path_resolution.py -q
runtime\python\python.exe -m pytest -q
runtime\python\python.exe tools\smoke_check.py --skip-tests
```

前端在源码删除和共享编辑器核验完成后运行一次 `npm run verify` 与 `npm run build`。浏览器和便携验收复用该构建产物，不重复构建。

## 回退

本包按旧入口/页面、测试、依赖与发布说明分批独立提交。任何阻塞可逐批 revert；完整回退 P2-22 的功能提交即可恢复 `USE_STREAMLIT=1`、旧页面和 drawable-canvas 依赖。无数据库 Schema、迁移或真实数据变化，因此不需要数据恢复。

## 稳定候选记录

- 集中调查与边界冻结：完成；问题清单归为旧回退可达、便携发布残留、旧页面测试残留、当前说明漂移四个根因。
- 首轮冻结提交：`acc349e8474b7e5bf7cdcc0fb555dff963c846ab`；初始实现 `0b89cbe` 已通过非破坏性 revert 后，按旧入口、旧页面、启动/打包和当前文档四个可独立回退批次重新落地。
- RED 退役契约：初始 6 failed / 10 passed，失败均对应计划内待退役对象。
- 聚焦与受影响回归：163 passed；首轮完整门槛发现两份遗漏的纯旧页面测试，9 failed / 2111 passed / 2 skipped，归并为一个当前任务遗漏根因。
- 统一修复：删除 `test_question_bank_ai_tagging_ui.py`，把 `test_question_frequency_ui.py` 收敛为服务 Schema 守卫；关联题库 AI、考频服务与 API 复测 86 passed。
- 完整后端最终门槛：2111 passed / 2 skipped / 0 failed，耗时 695.27 秒。
- 前端门槛：70 个测试文件、716 tests passed，lint、typecheck、生产 build 全部通过，耗时 129.2 秒。
- 隔离便携浏览器：唯一监听 `127.0.0.1:8013`；`/sessions`、`/grading`、`/question-bank`、`/training`、`/settings` 五个深路由直接打开，控制台 0 error；服务已关闭。
- 快速冒烟：文档治理、479 个第一方 Python 文件编译、两库副本初始化幂等全部通过，耗时 6.16 秒；启动前端资源检查通过。
- 真实数据守卫：根目录两库 SHA-256 与开工基线一致，功能工作区 `user_data/` 无 Git 变更；真实模型调用 0。
- 独立初审：同一冻结提交并行完成需求符合性与代码质量复审；原始意见 4 条，去重后 4 个 `Important`、0 个 `Critical`、0 个 `Suggestion`，分别为交接块格式、当前架构表述、删除批次不可独立回退和退役守卫扫描过窄。
- 统一修复：仅处理上述四项；采用非破坏性历史重排形成四个可独立 revert 的活动批次，扩大第一方源码退役守卫，纠正当前生产入口事实，并准备版本化 formal 清单。未修改业务接口、数据、模型调用或前端行为。
- 阶段耗时与轮次：自动验证包含一次因 600 秒工具上限中断、一次因对话切入中断；首个自然结束的完整门槛暴露 1 个实现期根因，收敛后仅补跑一次完整门槛。独立初审 1 轮；当前进入唯一一次统一修复后的限定终审。
- 当前剩余：原复审代理限定终审、formal 用户验收、integration/PR/main 收口。
- 当前任务验收：自动门槛通过，用户验收待办。
- 版本发布：尚不允许；需独立复审、formal 用户确认和 integration 门槛完成。
