# P3-16 旧 CLI、Legacy API 与专属表退役

**执行包：** P3-16
**计划日期：** 2026-07-25
**计划状态：** verified_pending_integration
**计划模型：** 当前连续作业模型
**允许夜间执行：** no
**计划基线：** 39ca9361be4e36889ca13818fbd6815d7a485463
**用户自测：** none
**自测清单：** not_required
**授权修正预算：** 0/5

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P3-16
**交接状态：** verified_pending_integration
**功能提交：** f5091f2fad294befe66b1b58970fb6a1403718b6
**自动验证：** passed
**独立复审：** passed
**用户验收：** not_required
**真实数据指纹：** not_touched
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** independent_candidate_allowed
<!-- HANDOFF_STATUS_END -->

## 任务边界（已冻结）

- **目标：** 在用户于 2026-07-25 明确确认不再使用 `main.py` 旧命令行入口后，退役该入口、`DBManager.save_result()` Legacy API、旧报告导出入口及其专属 `exam_results/grading_details` 表；提供删除前旧数据导出工具，并用前向迁移在隔离副本删除两表。
- **包含：** `main.py`；`DBManager.save_result()` 及仅由它使用的兼容代码；`ReportGenerator.export()` 旧表报告入口；安全只读的旧数据 JSON 导出工具；阅卷库 008 前向迁移及迁移器精确白名单；工作机使用说明、`ARCHITECTURE.md`、结构基线当前行为测试和执行状态记录。
- **明确不包含：** 不删除或修改 `session_results/session_details` 新主路径、`ReportGenerator.export_session()`、FastAPI/Vue、评分规则、报告口径、模型调用、题库 Schema 或 P3-17 旧技能语义体系；不重写历史 P3-01 基线发布物；不执行根目录真实库 migration，不修改、暂存、提交或 stash 真实 `user_data/`。
- **验收条件：** 旧 CLI 与活动源码调用消失；旧数据导出公开入口能从只读数据库完整、原子地产出可校验 JSON；008 迁移只获准删除精确两表、自动备份、事务原子、重复运行幂等且能从备份恢复原数据；新会话结果与报告回归通过；完整测试、快速冒烟、双路复审和真实两库 SHA-256 守卫通过。
- **风险等级：** 高。包含旧数据结构删除和公共迁移基础设施修改；实现及验证只写临时数据库副本，稳定候选必须冻结后进行需求符合性与代码质量双路复审，并在 integration 运行专项完整门槛。

## 集中调查与冻结问题清单

1. 仓库内 `main.py` 是 `DBManager.save_result()` 和 `ReportGenerator.export()` 的唯一活动调用方；两者分别写入和读取 `exam_results/grading_details`。FastAPI、Vue、Job 与现行报告只使用 `session_results/session_details` 和 `export_session()`。
2. 真实阅卷库仅做只读风险核对，具体业务状态不进入文档或 Git；该核对不作为修改真实库的授权，也不允许本包直接迁移真实库。
3. 008 必须覆盖干净空库、当前完整迁移链、带旧行的历史副本、重复执行、结构缺失/冲突和备份恢复，不得假设待升级数据库已经应用任一中间迁移。
4. 迁移器默认拒绝 `DROP TABLE`，只对迁移名、声明表集合和实际删除目标三者精确一致的既有重建开放；P3-16 必须延续这一窄白名单，不得形成通用破坏性开关。
5. `tools/migration_rehearsal.py` 当前把任何业务表行数变化判为失败，不能直接用来证明有意删表；本包以独立临时副本测试 008、自动备份和恢复，不改变通用 rehearsal 对意外数据变化的严格失败语义。
6. P3-01 已发布结构基线是历史证据，不重写；其公共命令当前会重新扫描源码，因此只更新当前行为断言为 `main.py` 不再出现，保留历史发布物。
7. 可复用滚动功能 worktree 已核验干净、无本地 `user_data`、无外部 reparse point，P3-15 分支已由主线完整保留；P3-16 从最新 `origin/main` 精确 SHA 新建独立短分支。

冻结后的根因共三组：旧 CLI 与专属 API/报告仍留在源码；删除前缺少独立导出能力；迁移链尚无对精确两张旧表的安全删除和恢复证据。相邻的 P3-17 旧技能表、P3-18 性能优化及真实库实际迁移均不进入本包。

## 故障场景

| 场景 | 预期结果 |
|---|---|
| 重复导出 | 默认拒绝覆盖已有归档；显式 `--overwrite` 时仍通过同目录临时文件原子替换，不留下半文件 |
| 同时导出与数据库写入 | 导出使用 SQLite 只读事务取得一致快照；锁冲突明确失败，不修改源库 |
| 导出中途退出 | 只可能留下同目录临时文件，公开目标不存在或仍保持旧完整版本；下次可安全重试 |
| 重复迁移/重新启动 | 008 只登记一次；再次运行无待处理项，旧表保持不存在 |
| 迁移失败 | 单事务回滚，不登记成功；自动备份已经存在，可用于恢复 |
| 取消 | 导出和迁移都是短同步命令，无协作式取消入口；进程终止按“中途退出”处理 |
| 部分完成 | 迁移在同一事务先删明细表再删结果表，不能只成功一张；导出原子发布 |
| 表或约束缺失/冲突 | 迁移结构守卫失败并整笔回滚，不把漂移数据库登记为已升级 |
| 旧数据存在 | 导出完整保留两表全部列与关系；迁移前自动整库备份，恢复演练必须证明原行可取回 |
| 真实数据误触 | 所有写验证只接受测试临时路径；真实两库大小、UTC 时间和 SHA-256 前后必须一致 |

## 测试接口

1. **旧数据导出公开 CLI：** 以显式临时数据库和输出路径调用 `tools/export_legacy_cli_data.py`，验证只读、完整关系、确定性摘要、原子发布、拒绝覆盖和缺表失败。
2. **迁移器公开入口：** 通过 `run_migrations(..., db_path=<临时副本>)` 验证 008 的精确白名单、结构守卫、原子删除、自动备份、幂等和备份恢复；不测试私有调用顺序。
3. **现行应用边界：** 通过结构扫描、Schema 基线、会话结果仓储和 `ReportGenerator.export_session()` 回归，证明旧入口归零且新主路径保持。

## TDD 步骤

- [x] RED 1：新增导出 CLI 行为测试，先因工具不存在失败；最小实现只读一致快照与原子 JSON 归档后转 GREEN。
- [x] RED 2：新增 008 迁移、精确破坏性白名单、备份恢复与漂移回滚测试，先因迁移缺失/被拒绝失败；最小实现迁移和白名单后转 GREEN。
- [x] RED 3：新增退役守卫，先证明 `main.py`、Legacy API、旧报告和当前结构基线断言仍存在；最小删除并更新当前行为断言后转 GREEN。
- [x] 运行迁移、结果仓储、报告、启动/打包、P3-01 当前结构扫描和 P3-11 至 P3-14 Schema 受影响回归。
- [x] 更新使用说明、架构事实与 Index，运行功能分支快速冒烟、专项完整测试和真实两库指纹守卫。
- [x] 冻结同一功能 SHA，并行进行一次需求符合性复审和一次代码质量复审；统一处理范围内 `Critical/Important`，最多一次限定终审。
- [ ] 形成 `verified_pending_integration` 交接，进入 M3-09 integration，逐包验证后通过 PR 合入主线并收口状态。

## 计划验证命令

```powershell
runtime\python\python.exe -m pytest tests\test_p3_16_legacy_cli_retirement.py -q
runtime\python\python.exe -m pytest tests\test_migration_tooling.py tests\test_schema_baseline.py tests\test_p3_11_schema_version_gate.py tests\test_p3_12_state_constraints.py tests\test_p3_13_knowledge_ids.py tests\test_p3_14_schema_cleanup.py -q
runtime\python\python.exe -m pytest tests\test_report_score_adjustment.py tests\test_report_completeness.py tests\test_report_export_job.py tests\test_p3_01_structural_baseline.py -q
runtime\python\python.exe tools\smoke_check.py --skip-tests
runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-25-p3-16-legacy-cli-retirement-implementation.md --repo .
```

## 阶段记录

- 集中调查约 20 分钟：完成权威范围、旧 CLI/Legacy API/旧报告静态调用、迁移白名单、备份机制、真实库只读行数、现行报告入口、结构基线和安全 worktree 核验。
- 原始发现 7 条，按根因去重为 3 组；用户已明确确认停用旧 CLI，其他产品选择为零。三个 RED→GREEN 纵切片、受影响回归、一次统一复审修复和限定终审均已完成，当前剩余工作为 integration、完整门槛与主线收口。
- 实现与测试约 45 分钟：三组 RED→GREEN 全部完成；迁移白名单测试额外发现并修复了“只删除声明表集的一部分仍会被放行”的安全缺口。
- 受影响验证：P3-16 与迁移工具 14 项、迁移与 Schema 79 项、报告与结构 27 项、启动与便携 24 项均通过；真实阅卷库只读副本迁移预演通过，结构、完整性与预期行数变化均符合要求。
- 完整测试共运行三次：首轮 2283 项通过、2 项跳过并发现 1 条过时版本断言；修正后第二轮 2284 项通过、2 项跳过；安全白名单加固后的最终候选为 2285 项通过、2 项跳过、2 条既有非阻塞警告，用时 15 分 20 秒。
- 最终快速冒烟 7.06 秒通过：文档治理、526 个第一方 Python 文件编译、两库隔离副本初始化幂等全部正常。
- 真实两库 SHA-256 与开工基线一致，未执行真实数据库迁移。双路初审约 4 分钟，原始意见 3 条，去重为 2 个 `Important` 和 1 个 `Suggestion`：同名并发导出覆盖风险与文档泄露真实业务状态已在一次统一修复中关闭；测试辅助代码重复为非阻塞建议且不扩大范围。
- 修复后受影响测试 8 项通过，快速冒烟 7.73 秒通过；两位原复审者限定终审均为 0 `Critical` / 0 `Important`，并发用例额外连续 20 次通过。当前剩余工作为 integration 门槛、PR 合并和主线收口；功能候选已经通过包内验收，但版本在里程碑门槛和主线合并前仍不允许发布。

## 回退

代码退役可整体回退 P3-16 功能提交，恢复 `main.py`、Legacy API 与旧报告入口。008 迁移不提供向后建表脚本；如果已对某个数据库副本执行，唯一受支持的回退是使用迁移前自动生成的整库备份恢复，恢复后再运行旧版代码。根目录真实数据库不在本包执行范围内。
