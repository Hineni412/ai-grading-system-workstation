# WP0.4 Schema 基线与迁移落地 Implementation Plan

> 所属：总体方案 Phase 0。这是"以迁移文件为 Schema 权威来源"（用户 2026-06-28 已确认的目标）的第一步：不缩减运行时 DDL（那是 Phase 3 WP3.4），只补基线、落地 schema_migrations、建立漂移守卫与预演工具。

**Goal:** 两库各获得一份与当前运行时 DDL 等价的幂等基线迁移；真实库建立 schema_migrations 并打标；提供"空库+迁移 ≡ 空库+运行时初始化"的漂移守卫测试和真实库副本预演工具。

**已核验事实（2026-07-03）：**

1. `update_tools/migrate_db.py`（484 行）：目标 `grading`/`question_bank` 指向真实库；按文件名序号排序执行；逐文件迁移前自动备份；`_execute_sql_safe` **按分号切分语句**——含 `BEGIN…END` 的触发器会被切坏（阅卷库运行时 DDL 有 2 个 `answer_regions` 触发器，基线必须包含它们）。
2. `run_migrations(target_name, *, dry_run)` 只接受目标名，无法指向副本库 → 预演/测试需要路径覆盖参数。
3. 旧迁移数据语句审计：`question_bank/005` 含两条 `UPDATE papers` 回填（重放可能覆盖教师手工修正的 semester 等字段）；`008` 为 `INSERT OR IGNORE`（幂等安全）；`grading/001、002` 纯幂等 DDL。→ **真实 QB 库不得盲目重放 005**。
4. 真实阅卷库尚缺 `grading_runs`/`grading_run_items`（002 从未对真实库执行；上一轮只在副本上演练过）；两真实库均无 `schema_migrations`。
5. 阅卷库"运行时 Schema"由两个初始化器构成：`DBManager.initialize()` + `GradingRunStore.initialize()`（惰性）。基线必须两者都覆盖。

## 设计决策

- **基线命名 `000_baseline_schema.sql`**：序号排在现有迁移之前；纯幂等 DDL（`IF NOT EXISTS`，含表/索引/触发器，不含任何数据语句）。空库上先建全量 Schema，之后的旧迁移靠工具的"重复列/已存在"容忍机制自然跳过。
- **基线由生成器导出，不手写**：`tools/generate_schema_baseline.py` 在临时目录新建空库 → 运行真实运行时初始化 → 导出 `sqlite_master` 原文（排除 `sqlite_%` 内部对象与 `schema_migrations`）改写为 `IF NOT EXISTS` → 写入迁移文件。保证"与运行时 DDL 等价"是构造性成立而非人工比对。
- **真实库打标策略（防重放数据语句）**：
  - `grading`：正常 `run_migrations` 执行（000/001/002 全为幂等 DDL，顺带补建缺失的账本表）；
  - `question_bank`：新增 `--stamp-only` 模式——把全部待执行迁移**记录为已应用但不执行 SQL**（schema 已由运行时建成，预演先证明无漂移）。
- **migrate_db.py 三处增强（均配测试）**：
  - (a) `_split_sql_statements()`：正确处理 `CREATE TRIGGER … BEGIN …; END` 块，替换裸分号切分；
  - (b) `run_migrations` 增加 keyword-only `db_path`/`migrations_dir` 覆盖参数（默认行为不变）；
  - (c) `stamp_only: bool` 参数 + CLI `--stamp-only`。

## File Structure

Create:

- `migrations/grading/000_baseline_schema.sql`（生成）
- `migrations/question_bank/000_baseline_schema.sql`（生成）
- `tools/generate_schema_baseline.py`
- `tools/migration_rehearsal.py`
- `tests/test_migration_tooling.py`
- `tests/test_schema_baseline.py`

Modify:

- `update_tools/migrate_db.py`

## Tasks

### Task 1: 迁移工具增强（TDD）

- [ ] Step 1: 写失败测试 `tests/test_migration_tooling.py`：
  - 含触发器的迁移文件经 `run_migrations`（db_path 覆盖到临时库）执行后，触发器存在且可拦截非法写入；
  - `db_path`/`migrations_dir` 覆盖生效（不触真实库）；
  - `stamp_only=True`：迁移记录进 `schema_migrations` 但其 `CREATE TABLE` 未被执行。
- [ ] Step 2: 运行确认失败（当前切分器坏触发器 / 无覆盖参数 / 无 stamp_only）。
- [ ] Step 3: 实现三处增强；`_split_sql_statements` 需处理：行注释、`BEGIN…END;` 触发器体、末尾无分号语句。
- [ ] Step 4: 测试通过；提交 `feat: migrate_db 支持触发器、路径覆盖与 stamp-only`。

### Task 2: 基线生成器与两份基线

- [ ] Step 1: 实现 `tools/generate_schema_baseline.py`：
  - grading：空库 → `DBManager.initialize()` + `GradingRunStore.initialize()` → 导出；
  - question_bank：空库 → `initialize_database()` → 导出；
  - 导出规则：`sqlite_master` 中 type ∈ {table,index,trigger}、名字不以 `sqlite_` 开头、非 `schema_migrations`；`CREATE X` → `CREATE X IF NOT EXISTS`（已含则原样）；每条以 `;` 结尾；文件头注释生成时间与生成器路径。
- [ ] Step 2: 运行生成器产出两份 `000_baseline_schema.sql`；人工抽查：阅卷库含 2 个触发器与账本表；QB 库含 26 张业务表。
- [ ] Step 3: 写漂移守卫 `tests/test_schema_baseline.py`：对两库各验证——空库 A 走运行时初始化、空库 B 走 `run_migrations`（全部迁移，路径覆盖），比较归一化后的 {name → sql}（表/索引/触发器）完全一致。
- [ ] Step 4: 测试通过；提交 `feat: 生成两库 Schema 基线迁移与漂移守卫`。

### Task 3: 预演工具与真实库落地

- [ ] Step 1: 实现 `tools/migration_rehearsal.py`：对每个目标——复制真实库到临时目录 → `run_migrations`（路径覆盖，grading 执行 / QB 分别演练"执行"与"stamp-only"两种模式）→ `PRAGMA integrity_check` → 业务表行数前后对比 → 与"空库+运行时初始化"参照库做 Schema 对比 → 打印报告，异常退出码非 0。特别输出：QB 副本上执行 005 的 `UPDATE papers` 实际影响行数（决策依据留档）。
- [ ] Step 2: 对两真实库副本运行预演，确认：integrity ok、行数不变（grading 允许新增空账本表）、无 Schema 漂移。
- [ ] Step 3: 真实库落地：`migrate_db.py --target grading`（执行）；`migrate_db.py --target question_bank --stamp-only`（打标）。工具自动备份到 `user_data/backups/`。
- [ ] Step 4: 落地后核查：两库 `schema_migrations` 行数正确、`integrity_check ok`、阅卷库账本表存在。
- [ ] Step 5: 全量 `pytest -q` 回归；提交 `feat: 真实库落地 schema_migrations 基线打标`（提交不含 user_data 数据库文件——库文件变更由用户决定是否入库）。

## 验收

- 漂移守卫测试常驻（此后任何"只改运行时 DDL 不改迁移"的行为都会被测试抓住）；两真实库有 schema_migrations 且与迁移目录一致；预演工具可重复运行。

## 回退

- 真实库操作前有工具自动备份；`schema_migrations` 表与账本表可整体 DROP 回退（记录在预演报告中）；代码提交逐个可 revert。
