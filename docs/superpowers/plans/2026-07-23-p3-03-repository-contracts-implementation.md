# P3-03：Repository 契约与事务边界

**执行包：** P3-03
**计划日期：** 2026-07-23
**规划状态：** waiting_review
**规划模型：** 当前连续作业模型
**允许夜间执行：** yes
**计划基线：** dae97291aedf190e6cd1dd40e88f8e410bf9a881
**交接基线：** dae97291aedf190e6cd1dd40e88f8e410bf9a881
**用户自测：** none
**自测清单：** not_required

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P3-03
**交接状态：** verified_pending_integration
**功能提交：** eabf29afd226b6f6000b3e6c09bd3fb650680dbc
**自动验证：** passed
**独立复审：** passed
**用户验收：** not_required
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** independent_candidate_allowed
<!-- HANDOFF_STATUS_END -->

## 任务边界（已冻结）

- **目标：** 提供后续领域 Repository 共用的 SQLite 连接所有权、只读/读写会话、显式事务、嵌套 savepoint、行映射与 Repository 协议，避免把 `DBManager` 复制成多个大文件。
- **包含：** `backend/repositories/` 基础模块；每会话独立连接的 factory；同一会话内可共享的 `RepositorySession`；只读 `query_only`；外层 commit/rollback、嵌套 savepoint；线程归属守卫；明确的连接/事务错误；临时库契约测试。
- **明确不包含：** 不移动任何 students/sessions/papers/results/review SQL；不切 API 或服务调用方；不引入 ORM、连接池、全局连接、跨线程连接或自动重试；不改变 SQLite/WAL、Schema、迁移、业务错误、评分规则或真实数据。
- **验收条件：** 每个会话只拥有并关闭一条请求级连接；异常回滚、嵌套回滚且外层可继续、成功提交、只读拒写和线程隔离均由临时库证明；无隐式模块级连接；基础模块可供 P3-04/P3-05 注入同一 session。
- **风险等级：** 高。虽然尚未切业务调用方，但事务/连接基础若定义错误会在后续包造成部分提交、锁冲突或跨线程误用。

## 集中调查与冻结问题清单

1. 当前没有 `backend/repositories`；`DBManager` 自有 `_connect()`，另有只读请求上下文和多个独立 Store，各自管理连接。
2. `DBManager(external_connection=...)` 使用 `_BorrowedSQLiteConnection` 屏蔽 commit/rollback/close，证明后续需要“连接由请求/session 拥有、Repository 不自行关闭”的公共边界。
3. 现有 API 只读上下文已按请求捕获临时快照连接；P3-03 不替换它，只提供后续领域 Repository 的最小公共契约。
4. SQLite 写路径大量显式 `BEGIN`/commit/rollback；本包只建立可复用的事务原语，不提前迁移这些 SQL。
5. 不建立连接池或线程共享：Windows 本机单用户并不需要额外并发基础，默认 SQLite `check_same_thread` 和显式 session 线程归属更保守。

## 故障场景与预期

| 场景 | 预期处理 |
|---|---|
| 重复操作 | 每次 `factory.session()` 创建、归还并关闭独立连接；不复用隐藏全局状态。 |
| 同时操作 | 不同线程/请求获得不同连接；同一 session 跨线程使用明确拒绝。SQLite 锁错误不自动重试。 |
| 中途退出/取消 | `BaseException` 离开事务时回滚；会话退出时关闭连接。 |
| 重新启动 | 不保存进程状态；数据库提交结果只由 SQLite 事务决定。 |
| 失败重试 | 上层可新建 session 重试；基础层不自动重放写操作。 |
| 部分完成 | 外层异常回滚全部；嵌套异常只回滚 savepoint，调用方捕获后外层仍可继续。 |
| 数据缺失 | 只读打开不存在文件时给稳定连接错误，不创建空库。 |
| 数据冲突/锁占用 | 保留明确事务错误及原始异常链，不吞错、不自动覆盖。 |
| 取消/强制异常 | 捕获 `BaseException` 执行回滚后原样传播；不把取消误报为成功。 |

## 已确认测试 seam

1. 公共 `SQLiteConnectionFactory.session(read_only=...)` 上下文和会话关闭后的行为。
2. 公共 `RepositorySession.transaction(immediate=...)` 的提交、外层回滚和嵌套 savepoint 结果，使用临时 SQLite 文件从新会话读取验证。
3. 公共 `RepositorySession.connection` 供后续 Repository 执行 SQL；跨线程访问通过公共异常观察。
4. `Repository`/`RowMapper` Protocol 的静态公共形状，以及基础模块不存在模块级 `sqlite3.Connection`。

## 实施步骤

- [x] 先写临时库公共契约测试并取得 RED：模块缺失、提交/回滚/嵌套/只读/线程隔离尚不存在。
- [x] 最小实现连接 factory、session、Protocol 和稳定异常，使单会话提交/关闭转 GREEN。
- [x] 逐个补齐外层回滚、嵌套 savepoint、只读拒写、跨线程拒绝和缺失只读库，每条保持行为测试。
- [x] 运行聚焦与 DBManager/请求连接受影响回归、快速冒烟、双路独立复审、交接与真实两库指纹门槛。

## 计划验证命令

```powershell
runtime\python\python.exe -m pytest tests\test_p3_03_repository_contracts.py -q
runtime\python\python.exe -m pytest tests\test_request_read_connections.py tests\test_request_connection_benchmark.py tests\test_db_performance_instrumentation.py -q
runtime\python\python.exe tools\smoke_check.py --skip-tests
runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-23-p3-03-repository-contracts-implementation.md --repo . --expected-handoff-base dae97291aedf190e6cd1dd40e88f8e410bf9a881
```

## 实施记录

- **首轮实现提交：** `2fc377581c3948dceee92f70699eeb740b8dd810`
- **首轮冻结候选：** `1fae8530cbf681bb06905e2eda94f859704d3829`
- **TDD 结果：** 新增 10 项公共契约测试；其中未显式事务写入、回滚失败保留原异常、关闭失败不遮蔽原异常均先出现预期失败，再完成修正。
- **首轮自动验证：** 10 项 P3-03 测试与 45 项连接/性能受影响回归组合运行，共 55 项通过；唯一警告为既有 Starlette/httpx 弃用提示。
- **快速冒烟：** 文档治理、517 个第一方 Python 文件静态编译、两库隔离副本初始化幂等全部通过；按功能分支规则跳过全量测试。
- **真实数据：** 未读写真实业务表；所有写入测试仅使用临时数据库，真实两库指纹保持不变。

## 首轮复审与统一修复

- **首轮复审：** Spec 与 Standards 在同一冻结候选并行完成；原始意见为 6 Important / 2 Suggestion，去重为 4 个 Important 根因与 2 个 Suggestion。
- **统一修复：** `1077d3ac9be743e090453921ede39c930712f9a0`。受限连接视图不再暴露 commit/rollback/close/executescript；嵌套 immediate 只在外层已经取得 immediate 写锁时允许；回滚、关闭和 commit 后回滚的组合失败均保留原错误且继续尝试释放资源；连接初始化 PRAGMA 失败后显式关闭已打开连接。
- **建议处理：** 新增无模块级 SQLite 连接静态守卫，并把示例 Repository 的 session 类型收紧为 `RepositorySession`。
- **修复验证：** 新增 7 项故障与守卫测试，先稳定复现 6 个失败；统一修复后 P3-03 共 17 项，连同 45 项受影响回归共 62 项通过，快速冒烟再次通过。
- **最终复审范围：** 只检查首轮登记问题、上述修复区域及其直接回归，不重新开展开放式审查。

## 用户授权的限定续修

- **授权：** 用户于 2026-07-23 明确回复“继续修复”，批准建立新的限定修复任务。
- **目标：** 仅关闭最终限定复审仍存在的两项 Important：SQL/游标绕过 session 事务所有权；commit 与补救 rollback 同时失败时丢失原始 commit 异常对象与 traceback。
- **包含：** 受控 SQL 与 cursor 公共视图；事务控制语句拒绝；双重失败的两个原始异常对象保留；对应故障注入测试。
- **不包含：** 不迁移任何业务 SQL，不改变 Repository 之外的调用方，不处理 P3-04/P3-05，不做 Schema、真实数据或模型调用。
- **验收：** 事务绕过最小复现由 RED 转 GREEN，外层异常后临时库无部分写入；双重失败的 cause 同时包含原 commit 与 rollback 异常对象；受影响回归与快速冒烟通过。
- **风险：** 高；修复只使用临时库与内存故障对象。
- **首个候选：** `719bc95f58a94fbaf4b67a23aee4493cb523bdb1`；两个最小复现均先 RED 后 GREEN，63 项受影响测试与快速冒烟通过。
- **限定初审：** Spec 与 Standards 各报告 1 条 Important，去重为同一根因：SQLite 接受开头 UTF-8 BOM，但关键词检查未跳过该不可见字符，仍可提交外层事务。
- **统一修复：** `8bdc2aab77f34f72b7fe387c413f178252792a3d`；把 BOM 纳入 SQLite 前导可忽略字符，精确复现转 GREEN。
- **修复验证：** 18 项 P3-03 契约测试与 45 项受影响回归共 63 项通过；快速冒烟再次通过，真实数据未参与。
- **最终复审范围：** 只核对 BOM 绕过、原两项授权修复和直接回归。

## 限定续修最终复审与收口

- 原 Spec 与 Standards 评审者在同一最终候选 `eabf29afd226b6f6000b3e6c09bd3fb650680dbc` 完成限定终审，两路均为 `0 Critical / 0 Important / 0 Suggestion`。
- BOM 前缀事务绕过、SQL/游标事务权限绕过和双失败原异常丢失均已关闭；未发现直接回归。
- 全过程使用临时数据库和内存故障对象，真实两库大小、时间与 SHA-256 不变，Stash 基线不变；未调用真实模型。
- P3-03 通过包级验收，可以进入 N3-01 临时 integration；P2-22 仍为 deferred，整条 P3 候选不得进入 `main`。

## 回退

本包只新增尚未接管生产 SQL 的基础模块和测试；回退对应提交即可，不涉及数据恢复、Schema 或业务调用方。
