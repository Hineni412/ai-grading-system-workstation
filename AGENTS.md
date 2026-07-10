# AI 阅卷系统 Codex 接手说明

本文件是 Codex 在本仓库工作的优先入口。历史文档里提到的 `CLAUDE.md`，现在等同于阅读本文件；`CLAUDE.md` 只保留兼容指针。

## 项目现状

- 版本：`VERSION` = `v1.5.0`。
- 当前实际形态：Windows 本机单用户应用；生产 UI 仍是 Streamlit，监听 `127.0.0.1:8501`。FastAPI 骨架已合并，更多 API/JobManager 增量已在当前分支本地验证；Vue 3 SPA 尚未开始。
- 数据：两个 SQLite 数据库加 `user_data/` 文件树。`user_data/databases/grading_system.db` 和 `user_data/databases/question_bank.db` 是真实业务数据，默认不要改、不要删、不要提交。
- 目标架构是 FastAPI + Vue 3 SPA。迁移期间仍以 Streamlit 行为、现有服务和数据库契约为事实来源，不得凭目标架构臆造业务规则。

## 必读文件

开始任何较大改动前，按顺序读：

1. `AGENTS.md`
2. `ARCHITECTURE.md`
3. `docs/superpowers/packages/README.md`
4. `docs/superpowers/packages/EXECUTION_INDEX.md`
5. 与当前包对应的 `docs/superpowers/packages/phase-*-execution-packages.md`
6. `docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md`
7. 当前执行包的即时实现计划、相关代码、测试和迁移文件

前端视觉或交互改造还必须读 `docs/ui/STYLE.md`。样板页通过验收前，不要扩散迁移其他页面。

## 当前进度快照（2026-07-10）

- Phase 0 与 WP1.1 已通过 PR #1 合并到 `main`；代码冻结标签为 `pre-framework-switch-2026-07-09`。
- 框架切换前完整数据备份已创建：`user_data/backups/backup_20260709_142850_before_update.zip`。默认不要提交该备份。
- 当前开发分支：`codex/wp1-2-api-routes`，基于 `origin/main`。除 PR #1 外，当前 Phase 1 API/JobManager 增量已在本分支形成可审阅的本地 Git 检查点，尚未合并到 `main`。
- WP1.2 Batch A-D 已本地验证：sessions、students、config、template/answer-region 的只读与安全轻写 API。
- WP1.2 Batch E 已本地验证一部分：Excel 报告导出、扫描分析、启动批改、首批 review 路由和 P1-15 Question Bank 只读 API；training、graph、ops 尚未开始。
- 最小 WP1.3 JobManager 已本地验证：通用创建/查询/取消 API，以及 `report_export`、`scan_analysis`、`grading_run` handler。P1-10 已把 jobs 完整 DDL 收敛到 `003_add_jobs.sql` 并改为 FastAPI lifespan 所有 manager；P1-11 已让 running 取消区分“请求”与“安全停止确认”，并给三类现有 handler 补齐副作用边界。
- 2026-07-10 P1-14 完整基线：聚焦回归 87 项通过；API 回归 85 项通过；完整 `tools/smoke_check.py` 全量测试 889 passed / 0 skipped / 0 failed，编译 338 个第一方文件且两库副本初始化幂等、`integrity_check=ok`。
- P1-09 已本地验证：不可访问绝对路径不再中断题库素材回退；两个 POSIX fork-only skip 已改为 Windows/POSIX 均真实执行的 spawn 测试，POSIX 原始 fork 回归仅在支持的平台收集。该包已纳入当前分支的本地 Git 检查点，尚未合并到 `main`。
- P1-10 已本地验证：`003_add_jobs.sql` 是 jobs 表与索引的唯一完整 DDL；JobStore 保留旧表补列兼容并确定关闭连接；应用启动恢复中断任务、退出关闭线程池，测试 override 仍由测试拥有。该包已纳入当前分支的本地 Git 检查点，尚未合并到 `main`。
- P1-11 已本地验证：queued/paused 可立即取消；running 只置 `cancel_requested`，由 `JobContext.raise_if_cancelled()` 在安全边界确认 cancelled；普通失败不伪装成取消，完成竞态可由已发布结果胜出。报告先写 staging、扫描 latest 用临时文件原子替换；整卷/混合批改停止派发并丢弃未发布的在途结果，failed-only 会恢复取消前 paper 状态。该包已纳入当前分支的本地 Git 检查点，尚未合并到 `main`。
- P1-12 已本地验证：复核读模型改为单 JOIN，60 个 result 的 API GET 连同会话检查不超过 2 次连接；跨 result 调分、元数据和总分在同一 SQLite 事务中提交或整批回滚；事务后批注失败返回脱敏、可重试的 `retry_required`，router 保持薄壳。该包已纳入当前分支的本地 Git 检查点，尚未合并到 `main`。
- P1-13 已本地验证：review item 返回语义化媒体 URL；原卷/批注页和答题区裁剪只通过 session/result/detail/page ID 访问，路径必须落在受控根且扩展名受白名单限制，裁剪只在内存生成。成功的 `report_export` job 只公开文件名与受控下载 URL；公开 job 结果递归移除路径字段，失败信息不回显内部路径。该包已纳入当前分支的本地 Git 检查点，尚未合并到 `main`。
- P1-14 已本地验证：OpenAPI 422 统一引用 `ErrorResponse`，二进制下载/媒体 200 契约与运行时一致；通用 Job 提交拒绝敏感键变体，公开 payload/result 与历史 review 元数据使用共享允许列表/路径脱敏；完成 future 自动回收，并发报告发布使用 job-owned 文件名。整包首轮复审发现均已用 TDD 修复，聚焦复审为 0 Critical / 0 Important / 0 Minor。该包已纳入当前分支的本地 Git 检查点，尚未合并到 `main`。
- P1-15 已本地验证：Question Bank 试卷列表、题目分页/组合筛选、详情、当前标签、富文本与预览元数据通过专用读服务公开；源题库以 M1-W1-W2-M2 有界临时快照读取，SQLite 只打开系统临时候选，持续变化返回脱敏 503，不创建或改写源 WAL/SHM。题目素材与预览只通过语义化 URL 访问，固定根、路径、扩展名和 `no-store` 均受控；未导入文件、未调用 AI、未写标签或 Schema。组合回归 156 passed，Question Bank/OpenAPI 64 passed，快速冒烟编译 342 个第一方文件且两库副本幂等、`integrity_check=ok`，整包复审 0 Critical / 0 Important / 0 Minor。该包已纳入当前分支的本地 Git 检查点，尚未合并到 `main`。
- 本次提交前在隔离副本运行完整 `tools/smoke_check.py`：967 passed，编译 342 个第一方文件，两库副本初始化幂等且 `integrity_check=ok`；真实 `user_data/` 基线保持不变。
- P1-14 期间一次未完整 override lifespan 的诊断曾在真实 `grading_system.db` 创建空 `jobs` 表及两个索引，用户于 2026-07-10 明确要求保留；之后完整 smoke 前后两库大小、修改时间和 SHA-256 均未变化。除该已接受例外外，不得修改或提交 `user_data/`。
- 正式执行包定义覆盖 Phase 1-5，共 87 个；当前待执行 80 个。Phase 5 确定实施但先实验和设计，Phase 6 暂缓。数量、依赖和建议模型以 `docs/superpowers/packages/EXECUTION_INDEX.md` 为准。

未完成：

- Phase 1 尚有 14 个正式待执行包（P1-16 至 P1-29）；下一优先级是 Question Bank 轻写与导入准备。
- Phase 2/3/4/5 尚未开始实现；Phase 6 不在活动队列。

当前工作区仍有用户数据脏状态：`user_data/databases/*.db`、新备份 zip、历史备份、答卷图片、题库素材等均属于真实业务数据；除非用户明确要求，不要提交 `user_data/`。

## 工作原则

- 先调查再判断。不要凭文件名猜字段、接口、状态或业务规则。
- 不臆造评分规则、知识点口径、状态流转、权限或数据含义。
- 优先复用现有服务和测试，不为了“新架构”提前重写。
- 数据库变更必须有迁移、预演、备份和测试。
- 涉及真实 `user_data/` 的操作必须极其保守。除非用户明确要求，不要把数据库、答卷图片、导出文件或备份纳入提交。
- 结构性变化要同步更新 `ARCHITECTURE.md`；普通局部修复不做无意义文档改写。
- 执行包是稳定边界，不是永不过期的代码级步骤。每个包开工前必须根据当时源码生成即时实现计划；完成后更新执行索引和证据。
- 模型默认分工：Sol Extra High 负责跨域设计、实验门与高风险审查；Terra High/Medium 负责大多数实现；Luna 仅做低风险机械任务。升级条件见执行包 README。

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

1. 执行 P1-16：在保持耗时导入任务化边界的前提下，补齐 Question Bank 安全轻写与导入准备。
2. 每个包开工前生成即时实现计划；包结束前按其验收表验证，并更新 `EXECUTION_INDEX.md`。小批次至少运行 `runtime\python\python.exe tools\smoke_check.py --skip-tests`，阶段门运行全量冒烟。
