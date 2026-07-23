# P3-05：Papers、Results 与 Review Repository

**执行包：** P3-05
**计划日期：** 2026-07-23
**规划状态：** ready_for_execution
**规划模型：** 当前连续作业模型
**允许夜间执行：** yes
**计划基线：** 99d106c54ae1ea1e2beaf8cadddfec82ef56b9fe
**交接基线：** 99d106c54ae1ea1e2beaf8cadddfec82ef56b9fe
**用户自测：** none
**自测清单：** not_required
**授权修正预算：** 0/3

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P3-05
**交接状态：** in_progress
**功能提交：** none
**自动验证：** pending
**独立复审：** pending
**用户验收：** not_required
**真实数据指纹：** not_touched
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->

## 任务边界（已冻结）

- **目标：** 把答卷、评分结果、评分明细、复核查询/调分与批注索引 SQL 下沉到 session-bound Repository，使评分与人工复核主服务不再直接执行这些表的 SQL，同时保留 `DBManager` 一个版本的兼容门面。
- **包含：** `PaperRepository`、`ResultRepository`、`ReviewRepository` 及 gateway；答卷创建/状态/失败与进度读取；结果保存、同学生旧结果替换、当前答卷归属校验、明细整批替换与失败重试记录；复核批量读取、所有权校验调分、多结果重算和批注索引；`DBManager` 兼容委托；`GradingService`、`ManualReviewService` 适配；临时数据库映射、事务、冲突和回滚测试。
- **明确不包含：** 不修改 Schema、迁移、评分计算、Rubric 解释、完整性审计、失败卷/冲突语义、路径格式、API 响应或真实 `user_data/`；不迁移 P3-04 已完成的出勤 Repository；不提前完成 P3-06 的模板/答题区/设置或 P3-07 的全部 API、分析、报告调用方切换；不调用真实模型。
- **验收条件：** grading/review/report 受影响回归通过；结果发布与答卷状态同事务；同学生结果替换不遗留旧明细/批注；明细替换和多结果复核任一失败全部回滚；当前答卷归属变化时拒绝迟到结果；同学生多份答卷冲突逻辑不变；`DBManager` 对应方法不再含业务 SQL；服务不再通过 `_connect()` 绕过 Repository。
- **风险等级：** 高。评分结果是最高风险业务数据；错误事务可能造成部分分数、答卷状态与明细不一致。所有写验证只允许使用 pytest 临时数据库。

## 集中调查与冻结问题清单

1. P3-04 已把出勤替换与读取迁入 `SessionRepository`；P3-05 只在进度/异常投影中读取该表，不再创建第二份出勤写接口。
2. `DBManager` 从答卷创建到结果/复核/批注共包含多组 SQL；`clear_session_run_data`、会话永久删除和运行状态更新还跨越 session、paper、result、template/region 聚合，需要在同一 `RepositorySession` 中组合，不能拆成多个独立提交。
3. `GradingService` 除调用 `DBManager` 外还有三处 `_connect()` 直查答卷/结果；这些绕过必须由 Repository 的窄查询替代。
4. `ManualReviewService` 同时依赖评分数据、模板区域、备份和文件批注；本包只切评分/复核持久化，模板、路径和备份依赖保留到 P3-06/P3-07。
5. 完整性审计、Rubric 文件读取、知识点/错因聚合和分数边界属于业务计算，不进入 Repository；Repository 只返回稳定行映射或在调用方提供的既有审计结果下完成原子持久化。
6. `report.py`、分析服务和部分 API 仍直接依赖 `DBManager` 或原始连接；本包用兼容门面保持行为，P3-07 再切全部活跃调用方。
7. `DBManager.initialize()` 的建表/补列/索引仍由 P3-11 处理；P3-05 不以“移 SQL”为由触碰运行时 DDL。旧 `exam_results/grading_details` 也保留给 P3-16。

## Module、Interface 与 seam

- **Module：** `PaperRepository` 隐藏答卷状态、失败/进度投影和答卷侧清理；`ResultRepository` 隐藏结果/明细保存、查询和批量证据行；`ReviewRepository` 隐藏所有权校验调分与批注索引。
- **Interface：** 只暴露当前调用方实际需要的高层方法；不公开通用 SQL、原始连接、commit/rollback 或游标生命周期。
- **seam：** 生产与测试共同通过 session-bound Repository/Gateway；生产使用 instrumented SQLite provider，测试使用临时 SQLite provider。
- **兼容 Adapter：** `DBManager` 保留原方法名并委托新 Module；业务计算仍在兼容层/服务，不复制到 Repository。
- **深度约束：** 跨表保存、清理和复核事务隐藏在 Module 内，调用方只接收结果或稳定冲突；不为每条 SQL 建立浅方法。

## 故障场景与预期

| 场景 | 预期处理 |
|---|---|
| 重复操作 | 同一学生同一考试保存新结果时原子替换旧结果、明细和批注；批注 upsert 返回旧路径供事务后补偿；不新增自动重试。 |
| 同时操作 | 结果发布和复核调分使用 immediate 事务；迟到结果必须重新核对答卷仍属于同一考试/学生且状态为 matched，否则返回未发布且零写入。 |
| 中途退出/取消 | `BaseException` 离开 Repository 事务时整笔回滚；取消恢复答卷状态仍使用保存的原状态，不把取消误报为完成。 |
| 重新启动 | 不保存进程内事务；只认 SQLite 已提交的答卷、结果和明细。 |
| 失败重试 | 失败记录只追加既有 `grading_retry_attempts` 并保留完整性状态；基础层不自动重放任何写操作。 |
| 部分完成 | 结果+明细+答卷状态、明细替换+父结果、多结果复核+总分重算、清空运行数据均为单事务；任一步失败恢复原集合。 |
| 数据缺失 | 未知结果、明细或答卷继续使用既有 `None`、空列表、no-op 或稳定异常语义，不擅自统一。 |
| 数据冲突 | 复核明细必须同时匹配 session/result/question/detail；重复 detail ID 或跨考试明细拒绝并零写入；同学生多卷冲突分类不变。 |
| 备份失败 | 清空运行数据仍在任何 DELETE 前创建备份；失败时不进入事务。会话永久删除的跨域保护与删除顺序不变。 |
| 借用只读连接 | 只读 Repository 复用请求快照，不新开连接、不 commit/rollback/close；写方法明确拒绝。 |

## 已确认公共测试 seam

1. `PaperRepositoryGateway` 的答卷创建、状态条件更新、失败/进度投影和补批身份查询。
2. `ResultRepositoryGateway` 的结果保存、当前归属发布、明细替换、重试记录、结果/明细读取和原始证据行。
3. `ReviewRepositoryGateway` 的批量复核读取、所有权校验调分、批量总分重算与批注索引。
4. `DBManager` 现有公共方法作为兼容 seam；返回值与异常保持不变，静态守卫确认对应方法不再包含目标表 SQL。
5. `GradingService` 与 `ManualReviewService` 的现有公共行为；只替换持久化 Adapter，不修改模型、评分、文件补偿或用户结果。
6. grading/review/report 的现有 API 与导出测试；验证 observable 行为，不断言 Repository 私有实现。

## 实施步骤

- [ ] 新增 Repository 公共契约测试并取得 RED：模块/属性缺失，服务仍通过 `_connect()` 或兼容方法访问评分表。
- [ ] 实现 `PaperRepository` 的答卷写入、条件状态、进度/失败读取、存储路径和运行清理切片，逐项转 GREEN。
- [ ] 实现 `ResultRepository` 的结果/明细保存、当前归属发布、同学生替换、完整性源行与证据读取，故障注入证明整笔回滚。
- [ ] 实现 `ReviewRepository` 的复核行、所有权调分、多结果总分与批注索引；跨结果故障注入证明零部分更新。
- [ ] 把 `DBManager` 对应方法改为兼容委托；跨域方法在一个 `RepositorySession` 中组合，保留备份、Rubric/完整性和路径解释。
- [ ] 让 `GradingService`、`ManualReviewService` 使用新 Repository；移除评分主服务三处 `_connect()` 直查，出勤复用既有 `SessionRepository`。
- [ ] 运行包内聚焦、grading/review/report/API 受影响回归、快速冒烟、真实两库指纹与交接核验。
- [ ] 冻结候选后在同一 SHA 并行完成需求符合性与代码质量复审；仅在存在阻塞问题时使用授权预算统一修正，最多 3 次。

## 计划验证命令

```powershell
runtime\python\python.exe -m pytest tests\test_p3_05_papers_results_review_repositories.py -q
runtime\python\python.exe -m pytest tests\test_atomic_major_retry.py tests\test_grading_completeness_db_regressions.py tests\test_retry_failed_grading.py tests\test_grading_pause_resume.py tests\test_manual_review_atomic.py tests\test_review_application_service.py tests\test_report_score_adjustment.py tests\test_report_completeness.py tests\test_api_review_routes.py tests\test_api_report_jobs.py -q
runtime\python\python.exe tools\smoke_check.py --skip-tests
runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-23-p3-05-papers-results-review-repositories-implementation.md --repo . --expected-handoff-base 99d106c54ae1ea1e2beaf8cadddfec82ef56b9fe
```

## 规划复核结论

- Phase map、最新 `ARCHITECTURE.md`、P3-03/P3-04 Repository 事实、当前 SQL 调用方和现有回归均已交叉核对。
- 未发现需要改变评分含义、失败卷状态、API 契约或 Schema 的未决产品选择；无需中断询问。
- 计划按“持久化事务进入 Repository、业务计算留在服务/兼容层”的 seam 限制范围，避免复制 `DBManager` 或提前实施 P3-07。
- 当前剩余工作量为实现、受影响验证、双路复审、交接和 integration 逐包验证；真实模型调用与真实数据写入均为零。

## 回退

本包不做 Schema 或真实数据迁移。回退 Repository、兼容委托和服务适配提交即可恢复原内联 SQL；临时数据库随测试目录清理，真实数据无需恢复。
