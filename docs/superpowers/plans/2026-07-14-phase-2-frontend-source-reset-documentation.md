# Phase 2 前端来源重置：文档与参考资产实施计划

> **工作类型：** 一次性非编号治理工作，不是正式执行包。
> **基线：** `85d7cc664f24313408a01e75127b03fa7620bf12`（P2-08 已合并的 `origin/main`）。
> **本轮范围：** 只修改文档和参考资产，不修改前端源码、后端、测试、数据库或真实 `user_data/`。
> **后续范围：** P2-03 至 P2-08 行为审计和前端修正须在本轮文档进入共同基线后，根据当时源码另写即时实现计划。

## 目标

把 P2-08 之后、P2-09 之前的 Phase 2 前端来源重校准门槛写入当前权威资料；将 `STYLE.md` 收敛为纯视觉规范；删除完整 AI 概念图的活动副本与引用；保留 P2-08 的合并和历史验收事实。

## 安全边界

- 在独立 worktree 和 `codex/p2-frontend-realignment-design` 分支工作；
- 不在根目录脏 `main` 上修改；
- 不读取、打开、修改、暂存、提交或 stash 真实数据库及其他 `user_data/`；
- 不使用 P2-23、P2-08A 或其他正式包身份，不增加交接块或夜间资格；
- 历史 P2-02 至 P2-08 规格、即时计划和用户验收清单保持原样；
- 文档提交不宣称前端行为已经修正或重校准门槛已经通过。

## Task 1：确定门槛身份与历史边界

- [x] 核验 P2-08 已进入 `origin/main`，保留 `merged` 和用户验收记录；
- [x] 核验正式包注册为 Phase 2 共 22 包、全局 87 包；
- [x] 确定采用非编号、白天人工通过的门槛；
- [x] 写入 `docs/superpowers/specs/2026-07-14-phase-2-frontend-realignment-design.md`；
- [x] 明确历史 P2-02 至 P2-08 资料不回写，只失去对未来页面的默认解释权。

## Task 2：重置视觉规范和参考资产

- [x] 重写 `docs/ui/STYLE.md`，只保留视觉 Token、排版、密度、表面、控件外观、可访问性、动效、桌面视口和视觉 QA；
- [x] 从 STYLE 删除固定导航、页面内容、业务状态、操作、快捷键和流程；
- [x] 新建 `docs/ui/references/README.md`，规定局部视觉片段的准入和清单；
- [x] 删除原活动参考集中的 7 张完整 AI PNG，原件只由 Git 历史保留；
- [x] 不在本轮立即生成新的裁剪片段。

## Task 3：同步当前权威资料

- [x] `AGENTS.md`：加入功能溯源硬规则，明确 STYLE 和参考图不能定义业务；
- [x] `docs/superpowers/packages/README.md`：要求可见前端即时计划包含逐项功能溯源表；
- [x] `docs/superpowers/packages/phase-2-execution-packages.md`：在 P2-08 与后续复杂页面之间加入非正式门槛，并阻断所有可绕行入口；
- [x] `docs/superpowers/packages/EXECUTION_INDEX.md`：保持 8/14 和 P2-08 merged，登记门槛未通过和下一动作；
- [x] `docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md`：删除完整概念图映射和 STYLE 功能权威，改为功能溯源；
- [x] `ARCHITECTURE.md`：保留 P2-08 实现事实，登记来源漂移风险和用户新决定；
- [x] `frontend/README.md`：把七项导航和固定外壳明确标成待审计实现事实。
- [x] `docs/user-testing/PHASE2_FRONTEND_RECALIBRATION_TEST_TEMPLATE.md` 与用户测试总则：定义非正式门槛的版本化清单、已复审 SHA、数据与启动信息、Blocker/Major 和用户明确结论，不冒用正式包 ID。
- [x] 夜间提示词与并行手册：P2-09 至 P2-22 在来源重校准门槛通过且证据进入共同基线前一律停机；资格矩阵继续只保存稳定资格，不复制动态状态。

## Task 4：验证和提交

- [x] 用全文检索确认当前权威文档没有完整概念图路径或“按图直接参照”口径；
- [x] 确认 STYLE 不再定义固定导航、业务字段、操作、业务状态、快捷键或流程；
- [x] 运行 `tools/check_documentation.py`；
- [x] 运行 `git diff --check`；
- [x] 核对正式包仍为 87、Phase 2 仍为 22、Index 仍为 8 merged / 14 pending；
- [x] 核对分支的 `git status --short -- user_data` 为空，提交范围没有前端源码或真实数据；
- [x] 完成最终提交范围确认，只包含本计划的文档与参考资产；提交只交给用户审阅，不 push、不合并。

## 下一阶段入口

本轮提交进入共同基线后，重新调查最新 `web_app.py`、Review API、P2-03 至 P2-08 Vue 实现和相关测试，生成新的非编号前端修正即时计划。该计划至少要交付：

1. 业务能力与 UX 设计溯源表；
2. 当前导航的保留、改名、删除或分组决定；
3. 按题号批量复核主视图与单份可选详情的交互设计；
4. 组件复用、重构或替换的理由与范围；复用不得凌驾于已批准的 UX 重设计；
5. TDD、受影响回归、真实浏览器五视口和用户确认步骤；
6. 按专用模板生成版本化验收清单，不复用 P2-08 证据；
7. 不改变评分规则、API、Schema 和真实数据的回退方案。
