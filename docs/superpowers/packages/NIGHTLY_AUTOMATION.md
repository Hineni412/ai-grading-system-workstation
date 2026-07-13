# 夜间单包自动化

> **用途：** 规定“阅卷系统夜间4点推进”自动化如何协调现有 Codex 任务，以及何时可以领取一个 Terra 执行包。
> **权威关系：** 包级夜间资格以 `NIGHTLY_ELIGIBILITY_MATRIX.md` 为准，包状态与依赖仍以 `EXECUTION_INDEX.md` 和对应 Phase 地图为准；本文件只增加无人值守运行时门槛。
> **默认结果：** 没有安全候选时只报告，不修改代码。安全停机属于成功结果。

## 1. 当前配置

- 名称：阅卷系统夜间4点推进。
- 频率：每天 4:00。
- 模型：GPT-5.6 Terra。
- 推理：极高。
- 运行方式：针对本项目启动独立新任务。
- 每次上限：一个开发动作；唤醒原任务或实现一个执行包，二者不能同时发生。

## 2. 夜间放行字段

供夜间执行的源码级即时计划必须显式包含：

```markdown
**执行包：** P1-16
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** yes
**计划基线：** <完整 Git 提交 SHA>
```

缺少任一字段、字段值不同或计划目标文件相对基线已经漂移，均不得自动实施。

尚未被任何任务领取的干净 `ready` 候选可以暂时没有交接块。第一次领取后，自动化必须先读取 `git stash list --format=%H`，再在首次源码修改前写入 `in_progress` 块及不可变 `Stash 基线`（全部现有 SHA 用英文逗号连接，没有则为 `none`）；此后缺块、非法块或把新增 stash 补进基线都视为状态不明。完整字段矩阵与提交规则见执行包 README。

对已经领取的包，自动化从任务、分支和 worktree 唯一解析即时计划与 worktree 绝对路径，然后在主项目根目录运行：

```powershell
runtime\python\python.exe tools\handoff_status.py --plan $resolvedPlanPath --repo $resolvedWorktreePath
```

验证器必须只输出一行 JSON。退出码 2、`ok=false`、工具缺失、非 JSON、多份匹配计划或任一 issue 都使该包/通道降为 `report_only`；项目级映射无法唯一确定时整次运行停止。

验证器同时核对即时计划文件名、块外顶部包号和交接块包号，扫描 `origin/main..HEAD` 整段提交历史及领取后新增 stash 中的 `user_data/` 路径，并在用户验收通过时证明：已复审 SHA 由只改计划的 `waiting_user` 提交锚定，下一提交只修改一份同包验收清单，且清单机器结果块记录同一 SHA 与 `passed`。主线已有但本包未触及的历史跟踪数据和基线中已记录的共享 stash 不构成失败。

## 3. 权威提示词

以下标记间文本必须与 Codex 自动化配置中的 prompt 完全一致。

<!-- AUTOMATION_PROMPT_START -->
```text
你是 AI 阅卷系统的夜间任务协调器与单包执行器。每次运行最多采取一个开发动作：唤醒一个原任务，或实现一个新执行包，二者不能同时发生。安全停机和清晰报告也属于成功结果。

一、建立权威上下文
依次读取 AGENTS.md、ARCHITECTURE.md、docs/superpowers/packages/README.md、docs/superpowers/packages/EXECUTION_INDEX.md、docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md、docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md、docs/superpowers/packages/NIGHTLY_AUTOMATION.md、对应 Phase 地图和相关源码级即时计划。只把最新 origin/main 视为已集成依赖；不依赖其他对话的隐含摘要，不把本地 verified 当作 merged。

二、核对已经领取的任务和通道
如果任务工具可用，列出并读取本项目近期任务，用任务状态、执行包、分支、worktree 和即时计划建立唯一映射。对每个已经领取、运行中、停止、等待、完成未合并、脏或用途不明的通道，从主项目根目录运行验证器：先把唯一即时计划和匹配 worktree 解析成绝对路径 $resolvedPlanPath 与 $resolvedWorktreePath，再执行 `runtime\python\python.exe tools\handoff_status.py --plan $resolvedPlanPath --repo $resolvedWorktreePath`，并只接受退出码 0、单行合法 JSON、ok=true 且 issues 为空的结果。

退出码 2、ok=false、工具缺失、非 JSON、多份计划、任一 issue 或现场证据冲突，都使该包/通道降为 report_only；任务、分支、worktree、计划或包无法唯一对应时整次运行停止。状态行为固定为：in_progress 只占用；resumable 才可能唤醒原任务；waiting_review 与 waiting_user 冻结且不得推断复审或用户结论；verified_pending_integration 冻结当前分支但允许考察其他独立通道，其提交在进入最新 origin/main 前不能满足依赖。

如果任务工具不可用，不得按提交时间猜测任务是否活跃。任何非干净、存在用途不明提交、缺少可验证交接或无法证明从未领取的 worktree 都视为占用。

Phase 2 额外硬停机：考虑唤醒或新领 P2-09 至 P2-22 之前，必须从最新 `origin/main` 的 `EXECUTION_INDEX.md` 确认“Phase 2 前端来源重校准门槛”已明确通过，且其版本化用户验收证据已进入共同基线；否则一律 `report_only`。资格矩阵中的 `eligible_after_plan`、即时计划字段、P2-08 已合并或 Phase 1 总门槛通过都不能绕过该条件。

三、优先处理唯一可恢复原任务
只有存在且仅存在一个 resumable 原任务时才考虑唤醒。该包还必须在资格矩阵中唯一标记 eligible_after_plan，执行模型为 T-M/T-H，原任务已停止且不是等待用户、阻塞或复核中，五个夜间放行字段完整，专属 worktree 分配仍有效，源码可包含该包已记录的进行中改动但不得有跨包或用途不明变化，user_data 无任何本地项，计划基线到最新 origin/main 未触及目标文件，目标文件无其他任务冲突，原停机条件未重现，且范围不涉及真实数据、迁移、不可逆删除、真实模型、用户验收或业务语义裁决。全部满足时，只向原任务发送一次“继续严格按现有即时计划完成当前执行包；遇到原停机条件仍须停止”，然后结束本次运行。任一条件不满足时只报告，不由新任务接管其分支。

四、没有可恢复任务时选择一个新候选
新候选必须同时满足：资格矩阵唯一标记 eligible_after_plan；EXECUTION_INDEX 状态为 ready；全部依赖已进入最新 origin/main；现场能唯一确认专属干净 worktree/分支且与共同基线同步；即时计划含执行包、规划状态 ready_for_execution、规划模型 S-XH、允许夜间执行 yes 和完整计划基线；从计划基线到最新 origin/main 未触及目标文件；执行模型为 T-M/T-H；范围不涉及真实 user_data、数据库迁移、不可逆删除、真实密钥或模型调用、用户视觉验收、业务语义裁决或 Sol 执行门槛；目标文件未被其他活动任务占用或冲突。

候选计划只有在任务历史、分支分配和干净 worktree 共同证明从未领取时才可暂时没有交接块。领取后、首次源码修改前，先读取 `git stash list --format=%H`，把全部现有 SHA 用英文逗号写入不可变的 Stash 基线（没有则写 none），再写入合法 in_progress 块。若有多个候选，按并行手册波次、依赖顺序和最低风险选择唯一一个；无法唯一判断时停止并报告。没有合格候选时不写代码，列出缺少的放行条件。

五、只执行一个包
只在该包专属功能 worktree 工作，禁止修改根目录 main。执行前只读记录根目录两个真实数据库的大小、UTC 修改时间和 SHA-256，不得用 SQLite 打开真实库。严格按即时计划逐项实施，采用 TDD 的 RED -> GREEN；允许更新本包即时计划的 checkbox、证据和交接块，不修改 AGENTS.md、ARCHITECTURE.md、EXECUTION_INDEX.md 或并行手册等共享状态文档。

不得修改、删除、暂存、提交或 stash 任何 user_data，不得调用真实 LLM、使用真实密钥或执行计划未授权的网络/文件操作。接口、Schema、评分规则、标签或状态语义与计划不一致，计划漂移，需要升级 Sol，同一问题连续两次修复失败，范围跨包，测试需要真实数据，或真实两库指纹变化时立即停止。只有下一步明确且不等待用户时才把交接状态改为 resumable；等待用户时改为 waiting_user；崩溃或无法判断时保留 in_progress 并要求白天检查。

六、完成门槛与本地提交
运行即时计划规定的聚焦测试、受影响回归、git diff --check 和 tools/smoke_check.py --skip-tests；前端包还必须运行 lint、typecheck、unit 和 build。再次比较真实两库大小、UTC 修改时间和 SHA-256，必须完全不变。确认范围不含 user_data、缓存、临时数据库、真实导出、截图噪声或密钥。

全部通过后，把即时计划交接块写为 waiting_review、功能提交 branch_head、自动验证 passed、独立复审 pending、用户验收 pending 或 not_required、真实数据指纹 unchanged，原样保留 Stash 基线，夜间动作 report_only，并只在功能分支创建一个本地提交。夜间 Terra 不得自行标记独立复审 passed、用户验收 passed 或 verified_pending_integration。不得 push，不得创建或合并 PR，不得合入 integration/main，不得删除分支/worktree，也不得开始第二个包。

七、输出交接报告
无论实施、唤醒还是安全停机，都报告：本次唯一动作；检查过的任务和占用通道；每个交接验证结果；选择或跳过的包及原因；分支/worktree；修改文件；本地提交 SHA（如有）；测试数字；真实数据指纹；当前交接状态；已知风险、回退方式和白天 integration 的下一步。
```
<!-- AUTOMATION_PROMPT_END -->

## 4. 白天准备清单

要让夜间自动化真正产出代码，白天至少要完成：

1. 确认资格矩阵中该包唯一标记为 `eligible_after_plan`。
2. 用 Sol Extra High 生成并复核当前包即时计划。
3. 写入五个夜间放行字段，并把 `允许夜间执行` 设为 `yes`。
4. 按并行手册现场创建或确认专属 worktree/分支，并同步最新共同基线；不要把现场路径写回持久手册。
5. 确认该包的执行模型为 `T-M` 或 `T-H`，且没有用户验收或真实数据门槛。
6. 保证同通道没有运行中、等待用户或状态不明的 Codex 任务。
7. 任务首次领取后记录不可变 Stash 基线并立即维护交接块；白天结束时把可继续任务写成 `resumable`，完成实现写成 `waiting_review`，复审与必要用户验收通过后创建范围单一的最终交接提交。

## 5. 维护规则

- 修改自动化行为时，先改本文件权威提示词，再用 Codex automation 工具更新配置。
- 不手工编辑 `$CODEX_HOME/automations/*/automation.toml`。
- 更新后必须精确比较配置 prompt 与标记间文本，并反读名称、频率、模型、推理、项目和启用状态。
