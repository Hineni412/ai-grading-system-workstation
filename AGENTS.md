# AI 阅卷系统 Codex 接手说明

本文件是 Codex 在本仓库工作的优先入口。历史文档里提到的 `CLAUDE.md`，现在等同于阅读本文件；`CLAUDE.md` 只保留兼容指针。

## 项目现状

- 版本：`VERSION` = `v1.5.0`。
- 当前实际形态：Windows 本机单用户 Streamlit 单体应用，主入口是 `运行.bat -> streamlit run web_app.py`，监听 `127.0.0.1:8501`。
- 数据：两个 SQLite 数据库加 `user_data/` 文件树。`user_data/databases/grading_system.db` 和 `user_data/databases/question_bank.db` 是真实业务数据，默认不要改、不要删、不要提交。
- 目标架构只是后续计划：FastAPI + Vue 3 SPA 还没有开始落地。现阶段仍以 Streamlit 和现有服务为事实来源。

## 必读文件

开始任何较大改动前，按顺序读：

1. `AGENTS.md`
2. `ARCHITECTURE.md`
3. `docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md`
4. 与当前工作包对应的 `docs/superpowers/plans/2026-07-03-wp*-*.md`
5. 相关代码、测试和迁移文件

前端视觉或交互改造还必须读 `docs/ui/STYLE.md`。样板页通过验收前，不要扩散迁移其他页面。

## 当前进度快照（2026-07-08）

分支状态：`main` 比 `origin/main` 多 4 个本地提交。

已提交：

- `9fa372b`：写入 Phase 0 的 WP0.2-WP0.5 实现计划。
- `da83da9`：WP0.2 仓库卫生已做，根目录杂物归档，`.gitignore` 补齐。
- `301ce41`：WP0.3 依赖锁定已做，新增 `constraints.txt`，拆分 `requirements-build.txt`，运行依赖清单去掉 `google-generativeai` 和构建期 `pyinstaller`。
- `42a4b79`：WP0.4 Task 1 已做，`update_tools/migrate_db.py` 支持触发器 SQL 切分、路径覆盖和 `stamp_only`，对应测试已提交。

已在当前工作区继续完成但尚未提交：

- WP0.4 Task 2/3 已补完：`tools/generate_schema_baseline.py`、两份 `migrations/*/000_baseline_schema.sql`、`tests/test_schema_baseline.py`、`tools/migration_rehearsal.py`、`tests/test_migration_rehearsal.py`。
- `migrations/question_bank/002_add_teacher_review_status.sql` 已改为 no-op；该迁移原本是未被运行时 schema 或代码使用的示例列，会导致空库迁移多出 `questions.teacher_review_status`。
- `update_tools/migrate_db.py` 额外修正了路径覆盖时的备份目录：临时库迁移备份留在临时库旁边，不再污染真实 `user_data/backups`。
- 真实库已落地 WP0.4：阅卷库执行 3 个迁移；题库库以 `--stamp-only` 打标 9 个迁移。两库 `PRAGMA integrity_check` 均为 `ok`，`migrate_db.py --status` 显示无待执行迁移。
- WP0.5 已补完：`tools/smoke_check.py`、`冒烟检查.bat`、`tests/test_smoke_check.py` 已新增；冒烟入口执行“静态编译 + 全量测试 + 两库副本初始化幂等检查”，支持 `--skip-tests` 快速检查。
- 最新完整冒烟验证：`runtime\python\python.exe tools\smoke_check.py` 通过，静态编译 OK，全量测试 724 passed / 2 skipped，两库副本初始化幂等 OK。
- WP1.1 已在当前工作区实现但尚未提交：新增 FastAPI API 骨架 `backend/api/app.py`，`运行.bat` 默认同时启动 Streamlit 8501 与 API 8000；API 当前只有 `/healthz`、`/api/healthz` 与统一错误体，业务路由留给 WP1.2。

未完成：

- WP1.2 及后续 Phase 1/2/3/4/5/6 尚未开始实现。

当前工作区还有一个用户已确认的脏状态：`docs/superpowers/plans/` 下 15 份旧计划文件被删除但未提交，其中包括 `2026-07-01-unified-question-ids-resumable-grading-tag-retry.md`。用户已在 2026-07-08 明确表示“旧计划文件就删除掉”。

## 工作原则

- 先调查再判断。不要凭文件名猜字段、接口、状态或业务规则。
- 不臆造评分规则、知识点口径、状态流转、权限或数据含义。
- 优先复用现有服务和测试，不为了“新架构”提前重写。
- 数据库变更必须有迁移、预演、备份和测试。
- 涉及真实 `user_data/` 的操作必须极其保守。除非用户明确要求，不要把数据库、答卷图片、导出文件或备份纳入提交。
- 结构性变化要同步更新 `ARCHITECTURE.md`；普通局部修复不做无意义文档改写。

## 常用命令

使用项目自带运行时优先：

```powershell
runtime\python\python.exe -m pytest -q
runtime\python\python.exe -m pytest tests\test_migration_tooling.py tests\test_schema_baseline.py -q
runtime\python\python.exe -m pytest tests\test_api_app.py tests\test_run_bat_api_entry.py -q
runtime\python\python.exe tools\smoke_check.py
runtime\python\python.exe tools\smoke_check.py --skip-tests
runtime\python\python.exe -m streamlit run web_app.py --server.address 127.0.0.1 --server.port 8501
```

日常启动也可双击 `运行.bat`。

## 下一步建议

1. 提交或整理 WP0.4/WP0.5/WP1.1 当前工作区改动（包含真实库变更是否纳入提交需由用户决定；默认不要提交 `user_data/`）。
2. 后续每个 WP 结束前运行 `runtime\python\python.exe tools\smoke_check.py`。
3. 下一步进入 WP1.2：按 sessions → students → config → templates/regions 的顺序增量落 API 路由。
