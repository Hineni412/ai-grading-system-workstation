# Execution Package 使用说明

本目录把 2026-07-03 Master Plan 拆成稳定的执行包地图。执行包地图负责固定目标、依赖、范围、验收和模型选择；源码级实现计划在每个包开工前依据当时代码生成。

- 方案设计：`docs/superpowers/specs/2026-07-10-roadmap-execution-packages-design.md`
- 计划与代码质量审计：`docs/superpowers/packages/PLAN_AUDIT_2026-07-10.md`
- 当前状态与下一动作：`docs/superpowers/packages/EXECUTION_INDEX.md`
- 夜间执行资格矩阵：`docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md`
- 夜间自动领取与停机规则：`docs/superpowers/packages/NIGHTLY_AUTOMATION.md`

## 权威顺序

发生冲突时按以下顺序处理：

1. 用户当前明确指令
2. 根目录 `AGENTS.md`
3. `ARCHITECTURE.md` 中已经实现并核验的事实
4. 本目录 `EXECUTION_INDEX.md` 与对应 Phase 执行包地图
5. 2026-07-03 Master Plan
6. 历史实现计划和旧行号

`user_data/` 下数据库、答卷、报告、题库素材和备份都是真实业务数据。除非用户对本次操作明确授权，不得修改、删除、暂存或提交。

## 两级资料

### 执行包地图

文件：`docs/superpowers/packages/phase-*-execution-packages.md`

用途：提前看清全部工作量、依赖、质量门槛和推荐模型。执行包地图不写未来可能漂移的函数签名和源码行号。

### 源码级实现计划

文件：`docs/superpowers/plans/YYYY-MM-DD-<package-id>-<slug>-implementation.md`

用途：开工前重新调查当前代码，写出准确文件、接口、失败测试、最小实现、验证命令和提交范围。实现计划必须使用 checkbox，并要求执行模型采用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans`。

包级先天资格以 `NIGHTLY_ELIGIBILITY_MATRIX.md` 为唯一权威来源。只有标记为 `eligible_after_plan` 的包才可以进入夜间候选池；该标记不等于 `ready`，也不替代依赖、worktree 或代码漂移检查。

若允许每天 4:00 的 Terra 自动化执行，即时计划还必须包含：

```markdown
**执行包：** P1-16
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** yes
**计划基线：** <完整 Git 提交 SHA>
```

这些字段只代表该包可以进入夜间候选池；自动化仍须按 `NIGHTLY_AUTOMATION.md` 检查任务占用、文件漂移、风险和数据守卫。没有 `允许夜间执行: yes` 时，自动化只能报告，不能实施。

## 状态

| 状态 | 含义 |
|---|---|
| `planned` | 已定义，但依赖尚未满足 |
| `ready` | 依赖满足，可以生成源码级实现计划 |
| `in_progress` | 当前正在实施 |
| `implemented_uncommitted` | 本地实现存在，但尚无 Git 检查点 |
| `verified` | 本地定向和回归验证通过，尚未代表已合并 |
| `merged` | 已进入主线或用户指定集成分支，才计入阶段完成 |
| `deferred` | 用户明确延后，不计为阻塞 |
| `blocked` | 有明确外部依赖且无法继续 |

## 模型代码

模型建议核验于 2026-07-10。若模型选择器已更新，保持角色不变并选择当时官方等价模型；不要为了旧名称使用已弃用模型。

| 代码 | 模型与强度 | 用途 |
|---|---|---|
| `S-XH` | GPT-5.6 Sol + Extra High | 跨阶段规划、Schema、并发、删除、高风险决策 |
| `S-H` | GPT-5.6 Sol + High | 高风险实现或独立代码复核 |
| `T-H` | GPT-5.6 Terra + High | 跨模块实现、复杂测试、性能工作 |
| `T-M` | GPT-5.6 Terra + Medium | 默认 API、服务适配和页面迁移 |
| `L-M` | GPT-5.6 Luna + Medium | 输入输出明确的机械性清单和文档同步 |

每个包的 `模型` 字段依次为“规划 / 执行 / 复核”。例如 `S-XH / T-M / T-H` 表示用 Sol Extra High 生成源码级计划，用 Terra Medium 实现，用 Terra High 独立复核。

87 个正式包定义的模型分配已经按用户指定策略固化：规划槽全部为 `S-XH`；执行槽为 Terra 69 包（79.3%）和 Sol 18 包（20.7%）。其中 `T-M` 6 包、`T-H` 63 包、`S-H` 6 包、`S-XH` 12 包。完成包仍保留原模型记录；复核槽单独按风险设置，不计入上述执行比例。

官方模型说明：<https://learn.chatgpt.com/docs/models>

## 升级条件

执行模型遇到以下任一情况必须停止猜测，回到调查或升级到 `S-H/S-XH`：

1. 计划中的接口、表或文件与当前代码不一致。
2. 需要改变评分规则、题号口径、知识点语义或状态含义。
3. 涉及数据库迁移、线程取消、不可逆文件操作、敏感学生数据或密钥。
4. 同一个包连续两次修复失败。
5. 实现范围将扩散到另一个执行包。

## 开工流程

1. 从 `EXECUTION_INDEX.md` 选择状态为 `ready` 的包。
2. 读取 `AGENTS.md`、`ARCHITECTURE.md`、本 README、对应 Phase 文档和相关现有测试。
3. 使用包内“规划模型”生成源码级实现计划。
4. 先写失败测试并确认 RED，再做最小实现并确认 GREEN。
5. 跑包内验收、相关回归和 `runtime\python\python.exe tools\smoke_check.py --skip-tests`。
6. 高风险包按文档指定模型做独立复核。
7. 用户明确启动执行包后，门槛通过即可按本文固定流程在功能分支提交，并由 integration 分支 push、创建/合并 PR；默认排除整个 `user_data/`。直接 push `main`、force push、真实数据写入和未合并历史丢弃仍需单独确认。
8. 更新 `EXECUTION_INDEX.md` 的状态和证据。

## 并行、集成与 Main 同步

并行任务必须先读 `PARALLEL_WORKTREE_EXECUTION_2026-07-11.md`。默认采用两个功能 worktree 加一个集成 worktree：

1. 每个功能分支只承载一个执行包，不跨包堆叠提交。
2. 功能分支完成测试和复核后，先合入专用 integration 分支；不得直接合入本地或 GitHub `main`。
3. integration 分支逐包合并并运行受影响回归；一波结束后运行完整 smoke、前端质量命令和真实数据指纹守卫。
4. 集成结果通过后推送 integration 分支，通过 PR 合并 GitHub `main`。
5. PR 合并后 fast-forward 本地 `main` 与活动 worktree，删除已合并的远端 integration 临时分支，并让本地 integration 通道重新跟踪 `origin/main`；再从新 `main` 创建下一批 worktree。
6. 只删除已经被 `origin/main` 包含、源码状态干净、`user_data/` 无本地项且不再承担活动任务的分支/worktree；有独有提交或任何本地数据的历史 worktree 必须保留，除非单独获得用户明确授权并先完成数据处置。

共享 `AGENTS.md`、`ARCHITECTURE.md`、Index、全局 OpenAPI 和入口注册冲突由 integration worktree 统一处理。任何功能 worktree 都不得通过覆盖共享文档来宣称自己已进入主线。

## 通用回退

- 代码包：保持包边界独立，可按单个提交或 PR 回退。
- 数据库包：只在副本预演通过后操作真实库；真实操作前自动备份，回退说明必须写进源码级计划。
- 前端包：在 Phase 2 切换前保留 Streamlit 入口；单页迁移失败可退回旧页面。
- 删除包：每批独立提交，删除前保留调用方清单、完整测试和真实流程证据。
