# P3-11 迁移版本门槛与运行时 DDL 退役

**执行包：** P3-11
**计划日期：** 2026-07-23
**计划状态：** waiting_review
**计划模型：** 当前连续作业模型
**允许夜间执行：** no
**计划基线：** efc1739f12d6619fb5fc6aa7316dab79adaad7b9
**用户自测：** none
**自测清单：** not_required
**授权修正预算：** 0/3

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P3-11
**交接状态：** waiting_review
**功能提交：** branch_head
**自动验证：** passed
**独立复审：** pending
**用户验收：** not_required
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->

## 任务边界（已冻结）

- **目标：** 让两库 Schema 只由版本化 migration 管理；生产启动、`DBManager`、`JobStore`、`GradingRunStore` 和题库兼容初始化接口只通过统一迁移版本门检查或执行受控迁移，不再各自保存 `CREATE/ALTER/INDEX/TRIGGER` 字面 DDL。
- **包含：** 迁移清单与已登记版本/校验和核验；未知未来版本、历史缺口和迁移文件漂移拒绝；单个 migration 原子执行和并发重复启动保护；空库 bootstrap、旧库升级、当前库幂等启动；FastAPI lifespan 与 Windows 启动器的安全提示；四个兼容初始化接口接线；迁移预演/Schema 守卫去除“运行时 DDL 自证”；真实两库隔离副本预演、自动备份与完整性检查。
- **明确不包含：** 不删除或改写历史 migration；除补齐本包调查直接发现的旧 `jobs.result_json` runtime 兜底外，不新增业务字段或其他 migration；不为 P3-12 猜测或增加状态 CHECK；不切换/删除 `knowledge_id`；不改变业务数据、Repository/API/Job 状态语义、评分规则或模型调用；不运行真实库 migration，不暂存/提交真实 `user_data/`；不清理工作区、分支或历史副本。
- **验收条件：** 空库经 migration 得到当前完整 Schema；grading 旧快照从 002 升到 004、question-bank 旧快照升级到 008 后与干净当前库 Schema 完全等价且业务行数不变；当前库和重复/并发启动幂等；未知未来 migration、已登记 checksum 漂移、历史登记缺口明确拒绝并给出不含绝对路径的启动提示；故障 migration 不留下本 migration 的部分 DDL；四个兼容初始化入口与 FastAPI lifespan 不含散落 DDL；聚焦测试、迁移/Schema/Job/API 受影响回归、快速冒烟、完整门槛、双路复审和交接核验通过。
- **风险等级：** 最高。错误会导致应用无法启动、Schema 部分升级或真实库额外写入；实现与测试只写临时库和真实库的隔离副本，根目录真实两库只做指纹核对。

## 已确认测试边界

Phase map 已把公开边界固定为 migration runner、四个兼容初始化接口和生产启动；现有调用方与测试进一步确认以下三个 seam，不需要新增产品选择：

1. `run_migrations`/统一 Schema gate：观察版本拒绝、备份、原子升级和最终 migration 状态。
2. `DBManager.initialize()`、`JobStore`、`GradingRunStore`、`initialize_database()`：观察旧兼容入口能否得到同一个 migration 权威 Schema，并保留既有非 DDL 数据维护/题库种子行为。
3. FastAPI lifespan 与 `backend.api.launcher.main()`：观察两库在创建 JobManager 前完成门槛检查，失败时拒绝启动并输出安全提示。

## 集中调查与冻结问题清单

1. `migrations/grading/000`—`003` 与 `migrations/question_bank/000`—`008` 覆盖主要运行时 DDL；受影响回归进一步证明旧 `jobs` 表缺少 `result_json` 时，003 的 `CREATE TABLE IF NOT EXISTS` 不会补列，过去依赖 `JobStore` runtime `ALTER`。本包新增 forward migration 004 承接该遗漏，不改写历史 SQL。
2. `db_manager.py`、`backend/jobs/store.py`、`grading_run_store.py` 和 `question_bank/database/schema.py` 仍分别保存建表、补列、索引和触发器 DDL；同一 Schema 有两套权威来源。
3. `run_migrations` 只按 migration 名称判断已应用，不拒绝未知未来版本、已登记 checksum 漂移或有缺口的历史；单个 migration 失败后，失败登记的 commit 可能连同此前已执行语句一起提交，存在部分完成风险。
4. FastAPI lifespan 当前先构造 `JobStore`，没有先检查 grading/question-bank 两库的整体版本；Windows 启动器也没有面向普通用户的数据库版本失败提示。
5. `tools/generate_schema_baseline.py` 和 `tests/test_schema_baseline.py` 仍以运行时 DDL为对照。运行时 DDL 退役后该对照会变成 migration 自证，基线生成器若继续运行还可能错误重写历史 `000`。
6. 只读临时副本核对确认：当前 grading 库已登记 `000`—`002` 且 checksum 与仓库一致，`003_add_jobs` 尚未登记但其表由旧运行时初始化创建；下一次受控升级会先备份，再幂等登记 003，并由 004 对已存在的 `result_json` 安全跳过后登记，不能改变业务行数。当前 question-bank 已登记 `000`—`008` 且 checksum 全部一致。
7. `DBManager.initialize()` 与题库初始化除 DDL 外仍有既有数据补齐、答题区 UUID 修复和内置技能种子逻辑。本包只退役 DDL，保留这些已存在的非 DDL 行为，避免借 Schema 收敛改变业务数据语义。

## 故障场景与预期

| 场景 | 预期处理 |
|---|---|
| 重复操作 | 已是当前版本时零 migration、零额外备份；四个初始化入口和 FastAPI 重复启动得到相同 Schema。 |
| 同时操作 | 同一进程按数据库路径串行；SQLite 写锁内重新核对 migration，跨入口竞争只允许一个执行者登记，每个 migration 只出现一条成功记录。 |
| 中途退出 | 当前 migration 在同一事务/保存点内回滚，不留下该 migration 的部分表、列、索引或成功登记；此前完整成功的 migration 保留。 |
| 重新启动 | 从最后一条连续成功 migration 继续；已成功项校验 checksum 后跳过。 |
| 失败重试 | 已知失败项允许在原因修复后重试；重试前仍自动备份，未知未来版本、checksum 冲突或历史缺口不得自动重试。 |
| 取消 | 启动 Schema gate 没有可交互取消阶段；启动失败即退出，离线运维 Job 的既有取消/Journal 规则不变。 |
| 部分完成 | runner 必须明确失败并拒绝启动；不得用 runtime `CREATE/ALTER` 偷补成“看似可用”的半升级库。 |
| 数据缺失或冲突 | 空库允许 bootstrap；无登记表的受支持旧库走现有幂等 migration；已登记未来名称、非连续历史或非空 checksum 不一致视为不兼容并拒绝。 |

## 实施步骤

- [x] RED：在新 P3-11 契约测试中固定空库/旧库/当前库、重复与并发、未来版本/checksum/缺口拒绝、失败 migration 原子回滚和安全启动提示。
- [x] GREEN：增强 `update_tools/migrate_db.py` 的清单校验、事务、并发重读和稳定错误分类；建立统一的 `backend/schema_migrations.py` 深模块。
- [x] GREEN：把四个兼容初始化入口与 FastAPI/launcher 接到统一门槛，删除散落 DDL但保留既有非 DDL维护和题库种子；让启动错误不泄露绝对路径。
- [x] GREEN：把迁移预演的参考库改为“干净空库 + 当前 migrations”，退役会重写历史 000 的基线生成路径，更新 Schema/无 runtime DDL 守卫和 `ARCHITECTURE.md`。
- [x] 验证：运行聚焦测试、迁移/Schema/Job/API/启动受影响回归、快速冒烟；只在临时目录对真实两库副本执行完整迁移预演，并复核根目录两库 SHA-256 不变。
- [ ] 冻结同一功能 SHA，完成需求符合性与代码质量双路复审；阻塞项统一修正，最多 3 次，修后只做一次限定终审。
- [ ] 合入 M3-04 integration，运行单包完整门槛和真实数据指纹守卫，再通过 PR 合入主线并同步正式状态。

## 计划验证命令

```powershell
runtime\python\python.exe -m pytest tests\test_p3_11_schema_version_gate.py -q
runtime\python\python.exe -m pytest tests\test_migration_tooling.py tests\test_migration_rehearsal.py tests\test_schema_baseline.py tests\test_job_store.py tests\test_api_app.py tests\test_run_bat_api_entry.py -q
runtime\python\python.exe tools\migration_rehearsal.py
runtime\python\python.exe tools\smoke_check.py --skip-tests
runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-23-p3-11-schema-version-gate-implementation.md --repo .
```

## 规划复核结论

- 计划与 P3-11 Phase map、Master Plan 的“migration 唯一权威 + 版本检查/拒绝启动”、M3-04 单包边界、现有 offline Ops 自动备份/Journal 和四处初始化调用方一致。
- 没有需要用户决定的 Schema 或业务口径；P3-12 状态枚举、P3-13 知识点字段切换和真实库实际迁移动作均明确留在包外。
- 新版本门只接受仓库已知、连续且 checksum 一致的历史；无 migration 表的旧库仍可按现有幂等 migration 升级，避免把“未登记”误判为未来版本。
- 现有 grading 真库在未来首次启动新版本时将对 003/004 做自动备份和幂等登记；本包只在副本证明业务行数和 Schema 不变，不在开发期间执行真实迁移。
- 问题清单和测试 seam 已冻结。实现阶段只处理上述同一根因，不扫描或修复 P3-12/P3-13、旧死代码和其他数据库问题。
- TDD 依次固定并通过空库 bootstrap、未来版本/checksum/历史缺口拒绝、单迁移原子回滚、并发启动、四个兼容入口、FastAPI/launcher 安全失败和无 runtime DDL 守卫；旧 `jobs` 表缺列用例直接证明需要新增 forward migration 004。
- 并发测试进一步固定“每个 migration 仅一份备份”，WAL 用例固定“备份与演练副本必须包含已提交 WAL 数据且不触碰源库”；runner 改为写锁内重读并使用 SQLite 在线备份，演练工具复用既有稳定 M1-W1-W2-M2 快照。
- 冻结候选的迁移/Schema/Job/API/启动与 Ops 受影响回归先后 142 项、139 项题库兼容回归及最终 47 项聚焦复核通过；快速冒烟通过文档治理、519 个第一方 Python 文件编译和两库隔离副本初始化幂等。
- 根目录真实两库只作为稳定快照源在系统临时目录预演：grading execute、question-bank execute 与 stamp-only 均通过 integrity、当前 migration Schema 等价和业务表行数不变检查，题库 005 改动行数为 0；真实两库 SHA-256 与开工基线一致。
- 初始实现、验证与候选收口约 70 分钟；尚余同一 SHA 双路复审、必要的一次统一修正/限定终审、integration 完整门槛和 PR 收口。当前任务尚未通过最终验收，版本暂不因本包允许发布。

## 回退

代码可整体回退到四个兼容初始化入口的旧 runtime DDL。任何已由现有 forward migration 成功登记的版本不删除、不倒退；若受控升级失败，使用该 migration 自动生成的升级前备份恢复，并在版本冲突原因修复前保持拒绝启动。
