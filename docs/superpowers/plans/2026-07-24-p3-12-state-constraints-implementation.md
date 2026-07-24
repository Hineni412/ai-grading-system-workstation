# P3-12 状态列约束迁移

**执行包：** P3-12
**计划日期：** 2026-07-24
**计划状态：** verified_pending_integration
**计划模型：** 当前连续作业模型
**允许夜间执行：** no
**计划基线：** 86f10c67d47b41ad79eb70e1580f8eb85a6e0b79
**用户自测：** none
**自测清单：** not_required
**授权修正预算：** 1/3

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P3-12
**交接状态：** verified_pending_integration
**功能提交：** af9fead398f900859966dfb65a8071f19a9605aa
**自动验证：** passed
**独立复审：** passed
**用户验收：** not_required
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** independent_candidate_allowed
<!-- HANDOFF_STATUS_END -->

## 任务边界（已冻结）

- **目标：** 依据真实数据库与历史副本中的 distinct 值，为 `grading_sessions.status`、`exam_papers.match_status`、`exam_papers.processing_status` 和 `answer_regions.mapping_status` 增加数据库 CHECK 约束及同源应用层验证，使非法新状态在写入边界被明确拒绝。
- **包含：** 只读状态分布审计工具；四列状态常量与验证；grading forward migration 005；SQLite 三表受控 rebuild；迁移执行器对本次显式声明的 table-rebuild migration 提供最小、可审计的放行；空库、当前库副本和多份历史库副本预演；行数、外键、索引、触发器、自增主键与非法值拒绝守卫；相关架构文档。
- **明确不包含：** 不约束 `jobs`、`grading_runs`、`grading_run_items`、`session_attendance` 或 question-bank 状态列；不修改评分、匹配、暂停/取消或答题区映射业务语义；不把前端展示状态混入数据库枚举；不改写历史 migration；不自动改未知真实值；不执行根目录真实库 migration，不暂存/提交真实 `user_data/`。
- **验收条件：** 当前库副本和至少三类历史副本均能先备份后原子升级；升级前后业务表行数、主键、外键、索引、触发器和既有状态值不变；四列合法值可写、非法值在 Repository/API 间接写入和原始 SQL 两层均被拒绝；未知历史值导致迁移失败且原库零部分重建；重复与并发启动幂等；完整迁移预演、受影响回归、快速冒烟、完整门槛、双路复审和真实两库指纹守卫通过。
- **风险等级：** 最高。SQLite rebuild 会短暂重建三个核心表；任何列、外键、索引、触发器或自增序列遗漏都可能造成数据丢失或应用无法启动。所有实现与测试只写 pytest 临时库和根目录真实库的隔离副本。

## 已确认测试边界

Phase map 和 Master Plan 已把公开边界固定为数据审计、migration、状态写入接口和启动 Schema gate，不需要新增产品选择：

1. `tools/audit_status_values.py`：只输出表、列、状态值与计数，不输出记录内容、绝对路径或个人信息。
2. `run_migrations` / `ensure_schema_current`：观察 rebuild 的备份、原子性、未知值拒绝、重复/并发启动和最终 Schema。
3. `DBManager.update_session_status()`、`PaperRepository`/gateway 的建卷与处理状态写入、`TemplateRegionRepository`/gateway 的答题区写入：观察同源应用层验证与既有合法流程。
4. 原始 SQLite 写入：证明 CHECK 约束独立于 Python 调用方工作。

## 集中调查与冻结问题清单

1. 2026-07-24 对根目录真实阅卷库只读统计：`grading_sessions.status` 为 `completed=2`；`exam_papers.match_status` 为 `matched=160`；`exam_papers.processing_status` 为 `graded=159, failed=1`；`answer_regions.mapping_status` 为 `auto=22, manual=13`；`integrity_check=ok`。
2. 对根目录 `user_data/backups` 下 74 份 `.db` 只读汇总，74 份均可打开且没有未知值：会话出现 `created/running/completed`；匹配出现 `matched`；处理出现 `pending/grading/graded/failed`；映射出现 `auto/manual`。
3. 生产写入点与测试共同补齐当前数据未覆盖但仍合法的状态：会话保守集合为 `created/running/completed/failed`；匹配为 `matched/unmatched/student_deleted`；处理为 `pending/grading/graded/failed/skipped`；映射为 `unbound/auto/manual`。前端/Job/训练流程中同名但不同领域的状态不进入这四列。
4. 当前四列都没有 CHECK；`PaperRepository` 与 `DBManager.update_session_status()` 接受任意字符串，答题区写入会把未知映射状态归一为 `manual/unbound`。本包只在四个已冻结持久化边界统一验证，不把范围扩到其他状态机。
5. 迁移执行器会默认拒绝 `DROP TABLE`，而 SQLite 无法原地给既有列增加 CHECK。实现必须增加一个仅允许声明式 table rebuild 的窄策略，并继续拒绝 `DELETE FROM`、`TRUNCATE`、`DROP COLUMN` 和未声明的 `DROP TABLE/DROP INDEX`。
6. rebuild 必须完整保留：`grading_sessions` 的活动索引；`exam_papers` 的两个索引及其到 `grading_sessions/students` 的外键；`answer_regions` 的唯一索引、两个非空 UUID 触发器及其到 `grading_sessions/session_templates` 的外键。历史 `answer_regions.region_uuid` 可空列继续由既有触发器提供等价非空保护，本包不借机改变该 P3-11 兼容决定。
7. 未发现需要映射或清理的未知真实状态值，因此没有新的业务口径选择；但连续作业授权未覆盖新的 Schema migration，当前只冻结计划并等待实施授权。

## 故障场景与预期

| 场景 | 预期处理 |
|---|---|
| 重复操作 | 005 已登记时零 rebuild、零额外备份；应用层验证保持幂等。 |
| 同时操作 | 复用 P3-11 写锁内重读；只有一个执行者运行 005 并登记一次，另一执行者跳过。 |
| 中途退出 | 三表 rebuild、索引/触发器重建与 005 登记处于同一事务；失败全部回滚，升级前备份保留。 |
| 重新启动 | 未登记 005 时从原 Schema 重试；已登记时只校验 checksum 与 Schema。 |
| 失败重试 | 未知值、复制失败、外键/完整性失败均保持原库；原因解决后可从备份或原库重试。 |
| 取消 | 启动迁移无交互取消点；启动失败即退出。离线运维 Job 的取消与 Journal 规则不变。 |
| 部分完成 | 不允许只完成其中一张表；任一表、索引或触发器失败必须回滚整个 005。 |
| 数据缺失或冲突 | 空表可 rebuild；未知/空白/NULL 状态不自动映射，迁移失败并报告列级安全错误。 |

## 实施步骤

- [x] 获得 P3-12 Schema migration 实施授权；授权前不创建 migration 或改生产代码。
- [x] RED：新增审计工具契约测试，固定四列、稳定排序、安全输出、NULL/空白/未知值分类。
- [x] GREEN：实现只读审计深模块与 CLI；默认只接受显式数据库路径，绝不自动修改或备份源库。
- [x] RED：新增 migration 005 契约测试，覆盖完整合法集合、未知值原子失败、三表 Schema/行/主键/外键/索引/触发器等价和非法原始 SQL 拒绝。
- [x] GREEN：为 migration manifest 增加窄 table-rebuild 策略，编写 005 三表 rebuild 并重建既有索引/触发器；不改写 000—004。
- [x] RED/GREEN：建立四列共享状态常量与持久化边界验证；合法现有调用不变，非法新调用抛出稳定 `ValueError`/API 400 映射。
- [x] 验证：运行聚焦测试、迁移/Schema/Repository/API 受影响回归和快速冒烟；只在系统临时目录对当前真实库与历史库隔离副本做预演并复核根目录两库 SHA-256 不变。
- [ ] 冻结同一功能 SHA，完成需求符合性与代码质量双路复审；阻塞项统一修正，最多 3 次，修后只做一次限定终审。
- [ ] 合入 M3-05 integration，运行单包完整门槛和真实数据指纹守卫，再通过 PR 合入主线并同步正式状态。

## 实现与验证证据（稳定候选前）

- TDD 聚焦测试：`tests/test_p3_12_state_constraints.py` 16 项通过；覆盖只读审计、稳定安全输出、四列应用边界、窄表重建授权、合法数据保持、未知值原子失败、历史运行时缺列补齐和数据库原生 `CHECK`。
- 迁移与版本门槛回归：迁移器、迁移预演、Schema 基线、P3-11 统一版本门槛和 Job Store 共 57 项通过；会话仓储 9 项通过。
- 直接影响业务回归：答题区、模板区、答卷/结果/复核仓储、暂停恢复、失败重试和相关 API 共 130 项通过。
- 当前真实阅卷库只读一致性副本升级成功：`integrity_check=ok`、业务表行数不变、最终 Schema 与干净迁移库等价；三类受支持历史阅卷库副本同样通过。所有源库及根目录真实两库 SHA-256 前后不变。
- 快速冒烟使用根目录当前两库的一致性副本通过文档治理、静态编译、两库重复初始化与完整性检查。工作区自带的旧历史快照另有 `session_details` 结构漂移，属于 P3-11 之前已存在且不在本包三表范围内的问题；不阻塞当前真实库或 P3-12，后续应独立清理该仓库历史快照。
- 冻结候选 `af9fead398f900859966dfb65a8071f19a9605aa` 完成同版本双路初审：需求符合性 0 项；代码质量原始 2 项，去重后 1 Important（交接记录未及时前进）和 1 Suggestion（状态契约键可进一步类型化）。Important 已通过本次仅改计划的最终交接记录统一关闭；Suggestion 不影响功能、数据安全或验收，转为后续独立优化，不扩大本包。需求评审另补跑 23 项无缓存聚焦验证，全部通过且工作区保持干净。

## 计划验证命令

```powershell
runtime\python\python.exe -m pytest tests\test_p3_12_state_constraints.py -q
runtime\python\python.exe -m pytest tests\test_migration_tooling.py tests\test_migration_rehearsal.py tests\test_schema_baseline.py tests\test_p3_04_student_session_repositories.py tests\test_p3_05_papers_results_review_repositories.py tests\test_p3_06_templates_regions_settings_repositories.py -q
runtime\python\python.exe tools\migration_rehearsal.py
runtime\python\python.exe tools\smoke_check.py --skip-tests
runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-24-p3-12-state-constraints-implementation.md --repo .
```

## 规划复核结论

- 包依赖、四列范围和合法状态集合同时得到 Phase map、Master Plan、生产写入点、当前真实库与 74 份历史备份支持；没有未知真实值需要用户决定映射。
- 方案不修改业务状态语义，只把现有合法集合下沉为数据库与应用层共同契约；不同领域的同名状态明确排除。
- 最高风险集中在 SQLite 三表 rebuild 与迁移执行器窄放行，已经冻结为单 migration、单事务、升级前备份、多副本预演和结构完整性守卫。
- 集中调查与规划耗时约 25 分钟；当前完成 1 次只读真实库审计、1 次 74 份历史备份汇总和 1 次源码写入点核对，未进行测试或代码复审。当前剩余工作为获得实施授权后完成 TDD、迁移预演、双路复审、integration 完整门槛和 PR；P3-12 尚未通过验收，版本不因本包允许发布。

## 回退

代码可整体回退 P3-12 提交，但已执行的 005 不删除、不倒退。若 005 升级失败或升级后发现版本级问题，使用该 migration 自动生成的升级前备份恢复整个阅卷库；恢复前保持应用停止，禁止把三张表分别回退或手工改写真实状态值。
