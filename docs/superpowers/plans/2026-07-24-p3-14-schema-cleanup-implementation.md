# P3-14 旧知识点列清理与题库同步状态决策

**执行包：** P3-14
**计划日期：** 2026-07-24
**计划状态：** verified_pending_integration
**计划模型：** 当前连续作业模型
**允许夜间执行：** no
**计划基线：** fd9edcab4acbcd2b270f9da2004bca7239cf4540
**用户自测：** none
**自测清单：** not_required
**授权修正预算：** 0/3

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P3-14
**交接状态：** verified_pending_integration
**功能提交：** d22d0bdb75467a46438d01d6f5c7176547e321b2
**自动验证：** passed
**独立复审：** passed
**用户验收：** not_required
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** independent_candidate_allowed
<!-- HANDOFF_STATUS_END -->

## 任务边界（已冻结）

- **目标：** 在用户确认 P3-13 兼容期结束后，删除活动 `session_details` 的旧生成列 `knowledge_id`；以实际容量和查询测量决定题库同步四列的归属，并保持题库同步工作流、会话接口和界面结果不变。
- **包含：** 活动 SQL 调用方 guard；grading forward migration 007；`session_details` 单事务重建；主键、自增序号、外键、索引、列表约束和触发器恢复；题库同步四列保留/迁移 ADR；迁移与工作流回归测试；架构文档。
- **明确不包含：** 不改 `grading_details` 旧表及 `DBManager.save_result()` Legacy API（P3-16 范围）；不删除领域模型、Rubric、配置、报告或接口中的兼容输出字段 `knowledge_id`；不改知识点列表内容、顺序、去重或标签语义；不改题库同步状态、详情、错误、时间戳含义；不改 API/UI；不迁移题库同步四列；不删除 `session_results.raw_json`；不执行根目录真实库 migration，不改、暂存或提交真实 `user_data/`。
- **验收条件：** 当前库副本、至少三类历史副本和干净空库可先备份后原子升级；升级后 `session_details` 不再含 `knowledge_id`，全部 `knowledge_ids`、行号、主外键、索引、触发器和自增序号保持；活动运行 SQL 不引用已删列；重复/并发启动幂等；非法列表或结构漂移整笔失败；题库同步状态和 UI 契约不变；完整门槛、双路复审、快速冒烟和真实两库指纹守卫通过。
- **风险等级：** 高。SQLite 需要再次重建 3042 行活动明细；列遗漏、触发器恢复错误或非原子失败可能导致评分明细损坏。题库同步状态若错误迁表，会增加每次状态读取的关联查询并扩大工作流写入面。

## 集中调查与冻结问题清单

1. P3-13 后活动 Repository、报告、掌握度、诊断、浏览器测试服务器和性能数据 SQL 只读写 `knowledge_ids`；对 Python SQL 字符串的静态提取未发现 `session_details.knowledge_id` 运行调用。剩余单值名称属于领域/API 兼容输出、历史 migration 006 或旧 `grading_details`，均不是本次要删除的数据库列调用。
2. 根目录真实阅卷库只读审计：`session_details` 3042 行，全部列表合法，旧值与列表冲突 0；74 份历史 `.db` 均可只读打开，其中 42 份含目标双列，共 43890 行，非法列表 0、冲突 0。P3-13 已证明 006 可把这些受支持副本升级为生成列结构。
3. 真实阅卷库只有 2 个 `grading_sessions`，同步状态为 1 个 `ready`、1 个 `partial`，四列共 1438 个字符且均非默认；74 份历史备份中只有 11 份含四列，共 19 行、12363 个字符，18 行非默认。该状态与会话一对一且随源试卷绑定变更原子重置。
4. 使用相同 4096 字节页和等价合成负载测量：四列原位与拆分状态表相比，2 行和 19 行时拆表均多占 4096 字节，1000 行时多占 114688 字节；单会话详情由一次主键查找变为会话表与状态表两次主键查找及一次关联。会话摘要扫描计划不因拆表改变。
5. 由测量冻结题库同步四列继续保留在 `grading_sessions`：它们属于会话绑定工作流状态，拆表没有容量或查询收益，反而扩大接口和事务面。只新增 ADR 记录该有意决定，不修改工作流实现。
6. migration 007 必须采用 005/006 已验证的固定清单式 table rebuild；直接 `DROP COLUMN` 继续由迁移器拒绝。007 只复制存储真值 `knowledge_ids`，恢复 006 的全列表校验、两个索引、外键和自增序号，不再创建单值生成列。
7. 当前真实两库调查前 SHA-256 为阅卷库 `93fee56e23ea072ac48351b1e6616d7af7f4b35ceb2b4779890e8d059fb841cd`、题库 `e1e5123ad54c9e8af5984bdcc5182a8f7a3038a1707f98ab26f168f4577a88b8`；调查只读，未执行迁移。

## 测试接口

1. **数据库升级接口：** 通过正式 `run_migrations()` 从 006 结构升级，观察最终 Schema、数据、序号、主外键、索引、触发器、失败回滚和重复执行；不测试迁移器私有实现。
2. **会话与题库同步工作流接口：** 通过 `DBManager`、`GradingPaperSkillWorkflowService` 和现有 sessions 响应验证源试卷重绑、运行中断、部分完成、完成与错误状态不变。
3. **活动 SQL guard：** 对第一方运行源码中的 SQL 字符串做限定静态检查，只禁止 `session_details` 查询显式引用单值列，不禁止领域/API 兼容字段或历史 migration。

## 故障场景与预期

| 场景 | 预期处理 |
|---|---|
| 重复操作 | 007 已登记时不 rebuild、不新增备份；题库同步状态保持原值。 |
| 同时操作 | 复用迁移写锁和同连接重试；只有一个启动者执行 007，其他启动者随后校验并跳过。 |
| 中途退出 | 新表、复制、旧表替换、索引/触发器恢复和 007 登记处于同一事务；失败全部回滚，升级前备份保留。 |
| 重新启动 | 未登记 007 时从完整 006 Schema 重试；已登记时只校验 checksum 与最终 Schema。 |
| 失败重试 | 非法列表、缺列或意外 Schema 漂移不自动修正；修复隔离副本输入后从备份或原库重试。 |
| 取消 | 启动迁移无交互取消点；失败即停止服务。题库同步 Job 的取消语义不变。 |
| 部分完成 | 不允许只删列而未恢复索引、触发器或序号；任一步失败都回滚整个 007。 |
| 数据缺失或冲突 | 007 不再读取旧单值；`knowledge_ids` 缺失、非法、为空或含非文本/空白项时安全失败，不猜测回填。 |

## 实施步骤

- [x] 获得 P3-14 启动授权，并确认 P3-13 兼容期结束；授权不包含真实库 migration。
- [x] 完成活动 SQL、真实库与历史备份只读审计；完成题库同步四列容量、查询和事务复杂度测量，冻结“原位保留”决定。
- [x] RED：新增 migration 007 契约测试，覆盖旧列消失、列表/序号/外键/索引/触发器保持、非法数据原子失败、重复/并发启动和活动 SQL guard。
- [x] GREEN：新增 `007_drop_legacy_knowledge_id.sql`，只重建 `session_details`；迁移器只对 007 声明的该表窄放行。
- [x] 新增 ADR 记录题库同步四列继续归属 `grading_sessions`，并更新 Schema/架构事实；不改工作流生产代码。
- [x] 在系统临时目录对干净库、当前真实库只读副本和至少三类历史副本预演；验证行数、主外键、索引、触发器、序号、列表 JSON、工作流状态和源库指纹。
- [x] 运行聚焦测试、受影响回归和快速冒烟；冻结同一功能 SHA 后进行需求符合性与代码质量双路复审，统一修复最多 3 次，必要时只做一次限定终审。
- [ ] 合入 M3-07 integration，运行一次删除包专项完整门槛、真实两库指纹守卫，通过 PR 合入主线并同步正式状态。

## 计划验证命令

```powershell
runtime\python\python.exe -m pytest tests\test_p3_14_schema_cleanup.py -q
runtime\python\python.exe -m pytest tests\test_p3_13_knowledge_ids.py tests\test_grading_paper_source_state.py tests\test_grading_paper_skill_workflow.py tests\test_api_read_routes.py -q
runtime\python\python.exe tools\migration_rehearsal.py --target grading
runtime\python\python.exe tools\smoke_check.py --skip-tests
runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-24-p3-14-schema-cleanup-implementation.md --repo .
```

## 阶段记录

- 集中调查约 25 分钟；完成 1 次活动 SQL 字符串提取、1 次当前真实阅卷库只读审计、1 次 74 份历史备份只读汇总和 3 档合成容量/查询计划测量。未运行迁移或测试，未修改生产代码或真实数据。
- 原始调查问题 6 组，按根因冻结为 3 组：旧列安全删除、`session_details` 原子重建、题库同步状态归属。未发现需要用户决定的数据映射；用户已明确确认兼容期结束。
- 实现与验证约 45 分钟：完成 1 次 RED→GREEN、1 个实现批次；迁移/Schema 聚焦测试 91 项、工作流/API 回归 102 项、相邻结果链路回归 82 项全部通过。首次工作流回归在受限环境出现 Windows 错误 5，正常权限原样重跑后 102 项全过，不属于代码失败。
- 完成 4 类隔离副本专项预演（当前真实库只读副本、缺少次要错误列的历史库、已有次要错误列的历史库、当前数据量历史库）以及正式迁移演练；全部保持行数、主外键、索引、触发器、序号与列表数据，源库未修改。快速冒烟通过。
- 初审约 10 分钟：需求符合性与代码质量两路原始意见各 1 条，去重后为同一根因 1 个 `Critical`、0 个 `Important`、0 个 `Suggestion`。问题属于“当前任务原本遗漏”：意外附加列会在固定清单复制后静默丢失。
- 统一修复 1 次：007 在删表前精确核对 006 的列属性、外键、索引和触发器集合；新增回归先复现“迁移成功但数据丢失”，修复后改为整笔失败并保留附加列和值。修复后聚焦迁移/Schema 测试 92 项通过，正式迁移演练和快速冒烟再次通过。
- 限定终审约 5 分钟：由首轮原两名复审代理只检查登记问题、修复区域及直接回归，结果 0 `Critical`、0 `Important`、0 `Suggestion`；功能候选通过需求与质量复审。
- 当前剩余工作量：integration 专项完整门槛、PR 与主线同步。功能分支候选已经通过验收，P3-14 尚未完成 integration/主线收口；整个版本暂不因该候选单独允许发布。

## 回退

代码可整体回退 P3-14 提交，但已执行的 007 不倒退。若 007 失败或升级后发现版本级问题，停止应用并使用 migration 自动生成的升级前完整备份恢复阅卷库；禁止手工向 007 后数据库补回 `knowledge_id`、绕开 `knowledge_ids` 校验或在运行中修改真实知识点 JSON。题库同步四列未迁移，无额外状态表回退步骤。
