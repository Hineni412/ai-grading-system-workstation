# Execution Package 使用说明

本目录把 2026-07-03 Master Plan 拆成稳定的执行包地图。执行包地图负责固定目标、依赖、范围、验收和模型选择；源码级实现计划在每个包开工前依据当时代码生成。

- 当前状态与下一动作：`docs/superpowers/packages/EXECUTION_INDEX.md`
- 夜间执行资格矩阵：`docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md`
- 夜间自动领取与停机规则：`docs/superpowers/packages/NIGHTLY_AUTOMATION.md`
- 用户自测与正式验收：`docs/user-testing/README.md`

## 权威顺序

发生冲突时按以下顺序处理：

1. 用户当前明确指令
2. 根目录 `AGENTS.md`
3. `ARCHITECTURE.md` 中已经实现并核验的事实
4. 本目录 `EXECUTION_INDEX.md` 的动态状态，以及对应 Phase map 的稳定包定义
5. 2026-07-03 Master Plan
6. 历史实现计划和旧行号

`user_data/` 下数据库、答卷、报告、题库素材和备份都是真实业务数据。除非用户对本次操作明确授权，不得修改、删除、暂存或提交。

## 两级资料

### 执行包地图

文件：`docs/superpowers/packages/phase-*-execution-packages.md`

用途：提前看清全部工作量、依赖、质量门槛和风险边界。Phase maps 不保存动态状态；其中“依赖”只描述启动条件，不能证明前置包已经完成。执行包地图也不写未来可能漂移的函数签名和源码行号。

`EXECUTION_INDEX.md` 是包状态的唯一来源；`NIGHTLY_ELIGIBILITY_MATRIX.md` 只保存稳定资格。状态变更不得复制到 Phase maps 或资格矩阵。

### 源码级实现计划

文件：`docs/superpowers/plans/YYYY-MM-DD-<package-id>-<slug>-implementation.md`

用途：开工前重新调查当前代码，写出准确文件、接口、失败测试、最小实现、验证命令和提交范围。实现计划必须使用 checkbox。执行采用 Matt Pocock skills：不确定的需求或设计先使用 `grilling`，行为修改优先使用 `tdd`，按已确认计划实施时使用 `implement`，稳定候选使用 `code-review`。技能的通用测试和提交建议必须服从本仓库的分层测试、worktree、integration 和真实数据保护规则。

可见前端包还必须在即时计划中建立“业务能力与 UX 设计溯源表”：

| 用户任务或业务能力 | 现有业务来源 | 必须保持的语义、结果与安全边界 | 新 Vue 呈现与操作设计 | UX 调整理由 | 业务差异及用户决定 | 验证方式 |
|---|---|---|---|---|---|---|
| 开工时逐项填写 | Streamlit、服务、数据库契约或测试 | 明确不可丢失或改义的内容 | 允许重新设计 | 说明效率、清晰度或可访问性依据 | 仅业务边界变化时必填 | 自动检查、浏览器证据与用户验收 |

纯 UX 差异不需要逐控件专项批准；改变业务能力、数据语义、结果、状态、权限、安全、持久化、危险操作保护、失败恢复、幂等性或审计时才填写带日期和范围的用户决定。纯 UX 差异仍必须经过设计复审、留存浏览器证据并完成最终用户验收。`docs/ui/STYLE.md`、AI 概念图、演示数据、Master Plan 和历史计划只能说明视觉或历史背景，不能单独填入“现有业务来源”列。

协议合并后新建或首次领取的正式包即时计划，文件名必须包含小写包号（例如 `2026-07-11-p1-16-...-implementation.md`），并在交接块之外恰好声明一次 `**执行包：** P1-16`。文件名、顶部字段和交接块包号必须一致；复制计划后只改其中一处会安全停机。

### 实现计划的生命周期与精简

即时实现计划是执行期间的临时导航，不是永久状态账本。正式包合并后，只有同时满足以下条件，才可从当前工作树删除该计划：

1. 退休批次之外的执行 Index、Phase map、工具、测试、代码和其他活动文档都不再引用它。
2. 已实现的长期系统事实已进入 `ARCHITECTURE.md`，动态状态已进入执行 Index，用户验收结果已进入对应版本化清单；需要长期保留的架构决定已进入规格或 ADR。
3. 删除不会丢失尚未交接的决定、风险、回退方法或验收证据；一次性命令、旧源码行号和已完成 checkbox 不迁移到长期文档。
4. 文件名加入 `tools/check_documentation.py` 的已退休引用清单，并通过文档治理检查，防止活动文档重新指向它。

Git 历史与已合并 PR 保留原始过程。规格、Master Plan、用户验收清单和仍被活动资料引用的实施计划不随包完成自动删除；它们需要单独审查后才能调整。每次退休以一批“已合并且批外零引用”的计划为单位，批内交叉引用随整批一同退出，避免反复逐文件清理。

每份即时计划还必须明确用户是否需要观察结果：

```markdown
**用户自测：** none | quick | formal
**自测清单：** not_required | docs/user-testing/checkpoints/<版本化文件名>.md
```

纯工程或不可见包使用 `none/not_required`；明显可见的前端批次使用 `quick`；P1-29、P2-08、P2-20 和 P2-21 使用 `formal`。具体清单只能在对应页面和流程已经实现并运行验证后生成，不得根据未来设计猜测操作步骤。

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

## 昼夜交接协议

尚未被任何任务领取的干净 `ready` 候选继续只使用上述五个夜间放行字段。任务首次领取后，必须在首次源码修改前把下面的块写入该包唯一的源码级即时计划；此后该计划必须且只能有一对标记：

```markdown
<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P1-16
**交接状态：** in_progress
**功能提交：** none
**自动验证：** pending
**独立复审：** pending
**用户验收：** not_required
**真实数据指纹：** not_touched
**Stash 基线：** none
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->
```

字段组合只能使用下表。`真实数据指纹: changed` 可以用于前四种状态记录异常，但会覆盖夜间动作并使验证失败；最终状态只允许 `not_touched` 或 `unchanged`。

`Stash 基线` 不随状态变化：首次领取时把 `git stash list --format=%H` 的全部现有 SHA 用英文逗号连接；没有则写 `none`。后续不得把新 SHA 补进基线。验证器允许这些历史共享 stash 保留，但任何基线之后新增且包含 `user_data/` 的 stash 都会失败。

| 交接状态 | 功能提交 | 自动验证 | 独立复审 | 用户验收 | 夜间动作 |
|---|---|---|---|---|---|
| `in_progress` | `none` | `pending` | `pending` | `pending` 或 `not_required` | `report_only` |
| `resumable` | `none` | `pending` 或 `failed` | `pending` | `pending` 或 `not_required` | `resume_only` |
| `waiting_review` | `branch_head` | `passed` | `pending` | `pending` 或 `not_required` | `report_only` |
| `waiting_user` | 复审前为 `none`/`branch_head`；复审通过后为完整 40 位已复审 SHA | 复审前 `pending`/`passed`；复审通过后 `passed` | `pending`，或与完整 SHA 同时为 `passed` | `pending` | `report_only` |
| `verified_pending_integration` | 完整 40 位 SHA | `passed` | `passed` | `passed` 或 `not_required` | `independent_candidate_allowed` |

生命周期规则：

1. 首次领取先记录不可变的 `Stash 基线`，再使用 `in_progress`；只有任务主动停下、下一步明确且不等待用户时才使用 `resumable`。
2. 实现和自动验证通过后，功能提交内写 `waiting_review` 与 `功能提交: branch_head`。夜间 Terra 到此停止，不能自行写复审通过。
3. 等待用户选择或验收时使用 `waiting_user`，夜间不得推断用户结论。独立复审通过后，先创建一个只改即时计划的锚点提交：它记录直接父提交的完整已复审 SHA，并把自动验证/独立复审写为 `passed`；用户只测试该锚定版本。
4. 独立复审和必要用户验收通过后，创建最终交接提交。它只能修改当前即时计划，块内记录直接父提交的完整 SHA，并改为 `verified_pending_integration`。
5. 通常最终交接的直接父提交就是已复审功能提交。`用户验收: passed` 时，必须紧接锚点提交创建一个证据提交；它只能修改 `docs/user-testing/checkpoints/` 下唯一一份以本包号开头的 Markdown 清单，不得改源码或其他文档。清单机器结果块必须记录同一包号、锚点中的已复审 SHA 和 `passed`，随后仍只允许一个仅改即时计划的交接提交。
6. `merged` 不由计划自报，必须由最新 `origin/main` 的包含关系证明。未合并提交不能满足其他包依赖。

已经领取的包必须用 `tools/handoff_status.py --plan <即时计划> --repo <worktree>` 验证。缺块、重复块、非法字段、包号身份不一致、脏 `branch_head`、错误父 SHA、验收证据夹带文件或交接提交夹带文件都按失败处理。领取提交必须是当前分支相对 `origin/main` 合并基线后的第一个 first-parent 提交且只能修改即时计划；`用户自测` 类型自领取起不可改变，P1-29、P2-08、P2-20、P2-21 必须为 `formal`，需要验收时交接结果不能写成 `not_required`，最终证据提交还必须精确修改计划声明的那一份清单。验证器还会检查 `origin/main..HEAD` 整段包提交历史和领取后的新增 stash；任何新增、修改、删除后保留、随后恢复或藏入新 stash 的 `user_data/` 都会失败，但主线已有且本包从未触及的历史跟踪文件及领取前已记录的共享 stash 不阻塞。一次性治理或支撑工作不得冒用正式包 ID、改变 87 包计数或进入夜间候选。

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

## 安全停机条件

执行任务遇到以下任一情况必须停止猜测，回到调查或请求更严格的规划与复核：

1. 计划中的接口、表或文件与当前代码不一致。
2. 需要改变评分规则、题号口径、知识点语义或状态含义。
3. 涉及数据库迁移、线程取消、不可逆文件操作、敏感学生数据或密钥。
4. 同一个包连续两次修复失败。
5. 实现范围将扩散到另一个执行包。

## 开工流程

1. 从 `EXECUTION_INDEX.md` 选择状态为 `ready` 的包。
2. 读取 `AGENTS.md`、`ARCHITECTURE.md`、本 README、对应 Phase 文档和相关现有测试。
3. 根据包边界、最新源码和风险生成源码级实现计划。
4. 先写失败测试并确认 RED，再做最小实现并确认 GREEN。
5. 按并行手册的分层门禁完成当前测试、包内验收、受影响回归和交接快速冒烟。
6. 高风险包按文档要求做独立复核。
7. 用户明确启动执行包后，按 `AGENTS.md` 的授权边界和并行手册走完标准功能分支、integration 与 PR 流程。
8. 更新 `EXECUTION_INDEX.md` 的状态和证据。

## 并行、集成与 Main 同步

具体流程统一见 `PARALLEL_WORKTREE_EXECUTION.md`；该文件是通道上限、文件所有权、集成顺序、测试证据复用、主线同步和安全清理的唯一详细操作手册。本 README 只维护执行包状态与交接规则。

## 通用回退

- 代码包：保持包边界独立，可按单个提交或 PR 回退。
- 数据库包：只在副本预演通过后操作真实库；真实操作前自动备份，回退说明必须写进源码级计划。
- 前端包：在 Phase 2 切换前保留 Streamlit 入口；单页迁移失败可退回旧页面。
- 删除包：每批独立提交，删除前保留调用方清单、完整测试和真实流程证据。
