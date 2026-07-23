# P3-04：Students 与 Sessions Repository

**执行包：** P3-04
**计划日期：** 2026-07-23
**规划状态：** waiting_review
**规划模型：** 当前连续作业模型
**允许夜间执行：** yes
**计划基线：** e502ea9c1056446f2b237e142a99b301c5a1ab7d
**交接基线：** e502ea9c1056446f2b237e142a99b301c5a1ab7d
**用户自测：** none
**自测清单：** not_required

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P3-04
**交接状态：** verified_pending_integration
**功能提交：** 7e3eaf1db673d776602665b8deea72e57bb348be
**自动验证：** passed
**独立复审：** passed
**用户验收：** not_required
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** independent_candidate_allowed
<!-- HANDOFF_STATUS_END -->

## 任务边界（已冻结）

- **目标：** 把学生名单、考试会话核心生命周期和出勤 SQL 下沉到 session-bound Repository；学生与会话主路由、学生名单服务不再直接调用对应 `DBManager` 方法，同时保留旧方法作为兼容 facade。
- **包含：** `StudentRepository` 的名单读取、分页快照、导入提交、编辑、删除影响、安全永久删除和姓名匹配；`SessionRepository` 的创建、列表、详情、改名、软删、恢复、出勤整批替换与读取；请求依赖 wiring；`StudentRosterModule` 改依赖 Repository 协议；`DBManager` 兼容方法委托；临时数据库映射、事务与 API 等价测试。
- **明确不包含：** 不迁移会话来源绑定、题库同步、评分配置发布、模板、答题区、进度、批改运行状态、清空运行数据、结果/明细/复核、考试永久删除或文件清理；不切 P3-07 的跨域分析/报告/Job 调用方；不修改 Schema、删除规则、排序、冲突、备份、评分或真实 `user_data/`。
- **验收条件：** SQL 行映射和排序与现有结果相同；名单 revision 冲突、学号冲突、活动批改阻断和删除前备份语义不变；学生删除和出勤替换任一步失败均整笔回滚；学生/会话 API 返回与错误行为不变；旧 `DBManager` 方法只委托新 Repository；外部只读请求连接继续复用且不新增连接。
- **风险等级：** 高。学生安全删除横跨结果、明细、批注、出勤和答卷解绑；失败可能造成部分删除、答卷残留旧归属或丢失备份保护。会话与出勤写入失败也必须保持原子性。

## 集中调查与冻结问题清单

1. 学生 SQL 目前集中在 `DBManager`，其中名单 revision、分页与班级排序、安全删除影响、活动批改阻断、备份和六类关联数据收口属于同一业务聚合，不能只搬简单 CRUD。
2. 会话主路由直接调用 `DBManager` 的创建、列表、详情、改名、软删和恢复；进度、模板与答题区属于后续 Repository 包，本包只把 `_require_session` 改走核心 Session Repository，其他领域方法暂保留 DBManager。
3. `StudentRosterModule` 直接依赖 `DBManager`；生产依赖应注入 `StudentRepository`，但 DBManager 的结构兼容必须保留，避免现有 Streamlit、Job 和测试一次性全切。
4. `RequestReadContext` 用 `external_connection` 保持请求级快照；Repository provider 必须借用该只读连接且不 commit/rollback/close，也不得为同一请求再开连接。普通连接继续保留 SQL 性能 instrumentation。
5. 新 `RepositoryConnection` 不暴露原始 cursor/transaction 生命周期；迁移 SQL 只能使用受控 `execute`/`executemany`、显式 `RepositorySession.transaction()` 和返回 cursor 的行/计数属性。
6. 学生永久删除在 `BEGIN IMMEDIATE` 后创建备份。备份成功但后续 SQL 失败时允许保留多余安全备份，数据库必须完整回滚；该既有行为不改。
7. 会话永久删除、批改状态、清空运行数据与结果表所有权横跨 P3-05/P3-06，本包不迁移，防止执行包扩散。

## 故障场景与预期

| 场景 | 预期处理 |
|---|---|
| 重复操作 | 名单提交/编辑/删除用 roster revision 阻止旧确认重复写；软删/恢复保持既有幂等 UPDATE 结果；不新增自动重试。 |
| 同时操作 | 名单写和学生删除使用 immediate 事务串行；后到请求重新核对 revision/活动批改状态，冲突时零写入。 |
| 中途退出或取消 | `BaseException` 离开 Repository 事务时回滚；连接由 provider 归还，原异常不被清理错误遮蔽。 |
| 重新启动 | 不保存进程内事务状态；只认 SQLite 已提交结果。姓名匹配缓存只属于 Repository 实例，写入后失效。 |
| 失败重试 | 调用方必须新建请求/session 并重新读取 revision；基础层不自动重放写操作。 |
| 部分完成 | 学生关联数据删除、答卷解绑和名单删除同一事务；出勤先删后插同一事务，任何失败恢复原集合。 |
| 数据缺失 | 学生编辑/影响/删除继续给出既有 not-found；会话详情返回 `None`，API 映射为原 404。 |
| 数据冲突 | 学号唯一冲突映射为既有异常；活动会话或可恢复批改账本阻止永久删除，备份前停止。 |
| 备份失败 | 学生删除在任何 DELETE/UPDATE 前回滚并返回既有备份失败异常；不把“未删除”误报为成功。 |
| 外部只读连接 | 只读 Repository 借用请求快照且不关闭；写 session 明确拒绝，不能在只读请求中隐式落库。 |

## 已确认公共测试 seam

1. `StudentRepository`、`SessionRepository` 与它们公开方法，在新建临时数据库上观察返回映射、排序、revision、提交和回滚结果。
2. `DBManager` 现有同名公共方法作为兼容 seam；行为测试确认它们返回同样结果，静态守卫确认 SQL 不再留在 facade 方法体。
3. `StudentRosterModule` 的现有 workspace/import/update/deletion API；只把依赖类型换成 Repository 协议，不改变公开 dataclass 和异常。
4. FastAPI `/api/students*` 与会话核心 CRUD 路由；继续通过 `get_grading_db` 覆盖派生 Repository，保持现有测试隔离。
5. `RequestReadContext.grading_db` 的外部连接读取；连接计数和关闭所有权由既有请求连接测试验证。

## 实施步骤

- [x] 新增 Repository 公共契约测试并取得 RED：模块缺失、DBManager facade 未委托、服务/主路由仍直连 DBManager。
- [x] 迁移学生只读与 revision 写入，每个行为切片逐个转 GREEN；再迁安全删除并用故障注入证明整笔回滚和备份顺序。
- [x] 迁移会话核心 CRUD 与出勤；用临时库证明倒序/班级排序、软删恢复和出勤先删后插原子性。
- [x] 增加普通/借用连接 provider，切 StudentRosterModule、学生路由和会话核心路由依赖；保留未归属本包的 DBManager 调用。
- [x] 运行包内、API/StudentManager/请求连接受影响回归和快速冒烟；冻结候选后双路复审、必要时一次统一修复、交接核验与真实两库指纹守卫。

## 稳定候选记录

- **冻结实现提交：** `7e3eaf1db673d776602665b8deea72e57bb348be`
- **测试优先切片：** 学生映射/revision、分页搜索与姓名缓存、安全删除、会话 CRUD/来源绑定、出勤原子替换、facade 无 SQL、借用连接不新增连接均先取得预期失败再实现通过。
- **受影响回归：** 91 passed；覆盖阅卷人数限制、暂停/恢复、学生服务与 API、会话 API/草稿/清理、请求只读连接和 SQL 性能日志。
- **快速冒烟：** 文档治理通过；518 个第一方 Python 文件编译通过；两类临时数据库初始化幂等且 integrity check 通过；按分层规则跳过全量测试。
- **数据与费用：** 只写 pytest 临时数据库；真实 `user_data/` 无本地变化；真实模型调用 0。
- **初审记录：** 同一冻结候选 `1dbdd3c2bfa06e28f0919f6d509d920bd461a520` 完成双路初审。需求符合性为 0 Critical / 0 Important / 0 Suggestion；代码质量为 0 Critical / 0 Important / 2 Suggestion。原始 2 条、去重后 2 条，均为防回归覆盖建议，无阻塞问题，因此不启动统一修复或无目标终审。
- **非阻塞建议：** 后续可增加“备份发生在任何删除前”的显式顺序断言，以及借用只读连接零 commit/rollback/close 的 spy 断言；当前实现和既有测试未显示对应行为错误。
- **数据守卫：** 真实两库 SHA-256、大小和 UTC 修改时间与 N3-01 门槛记录一致；stash 基线仍为两条既有提交；功能工作区及 `user_data/` 状态干净。
- **当前剩余工作：** integration 逐包合入与验证、更新 Index；本包实现和独立复审已通过，无用户验收要求。

## 计划验证命令

```powershell
runtime\python\python.exe -m pytest tests\test_p3_04_student_session_repositories.py -q
runtime\python\python.exe -m pytest tests\test_student_roster_service.py tests\test_api_student_routes.py tests\test_api_write_routes.py tests\test_api_read_routes.py tests\test_api_session_drafts.py tests\test_session_cleanup.py tests\test_request_read_connections.py tests\test_db_performance_instrumentation.py -q
runtime\python\python.exe tools\smoke_check.py --skip-tests
runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-23-p3-04-students-sessions-repositories-implementation.md --repo . --expected-handoff-base e502ea9c1056446f2b237e142a99b301c5a1ab7d
```

## 回退

本包不做 Schema 或真实数据迁移。回退 Repository、依赖 wiring 与 DBManager facade 提交即可恢复原内联 SQL；临时测试备份随测试目录清理，真实数据无需恢复。
