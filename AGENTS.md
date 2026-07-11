# AI 阅卷系统 Codex 接手说明

本文件是 Codex 在本仓库工作的优先入口。历史文档里提到的 `CLAUDE.md`，现在等同于阅读本文件；`CLAUDE.md` 只保留兼容指针。

## 项目现状

- 版本：`VERSION` = `v1.5.0`。
- 当前实际形态：Windows 本机单用户应用；生产 UI 仍是 Streamlit，监听 `127.0.0.1:8501`。FastAPI 与 P1-02 至 P1-15 API/JobManager 增量已合并到 `main`；Vue 3 SPA 尚未开始。
- 数据：两个 SQLite 数据库加 `user_data/` 文件树。`user_data/databases/grading_system.db` 和 `user_data/databases/question_bank.db` 是真实业务数据，默认不要改、不要删、不要提交。
- 目标架构是 FastAPI + Vue 3 SPA。迁移期间仍以 Streamlit 行为、现有服务和数据库契约为事实来源，不得凭目标架构臆造业务规则。

## 必读文件

开始任何较大改动前，按顺序读：

1. `AGENTS.md`
2. `ARCHITECTURE.md`
3. `docs/superpowers/packages/README.md`
4. `docs/superpowers/packages/EXECUTION_INDEX.md`
5. `docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md`
6. `docs/superpowers/packages/NIGHTLY_AUTOMATION.md`
7. `docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION_2026-07-11.md`
8. `docs/user-testing/README.md`
9. 与当前包对应的 `docs/superpowers/packages/phase-*-execution-packages.md`
10. `docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md`
11. 当前执行包的即时实现计划、相关代码、测试和迁移文件

前端视觉或交互改造还必须读 `docs/ui/STYLE.md`。样板页通过验收前，不要扩散迁移其他页面。

## 当前进度快照（2026-07-11）

- Phase 0 与 WP1.1 已通过 PR #1 合并到 `main`；代码冻结标签为 `pre-framework-switch-2026-07-09`。
- 框架切换前完整数据备份已创建：`user_data/backups/backup_20260709_142850_before_update.zip`。默认不要提交该备份。
- 当前共同基线：GitHub PR #2 已把 P1-02 至 P1-15 合并到 `main`；PR #3 至 PR #5 已把并行执行手册、入口文档、linked-worktree 测试兼容修复和分支清理红线合并到 `main`。根目录本地 `main` 与三个活动 worktree 必须始终快进到最新 `origin/main`，文档不写死会随下一次 PR 失效的当前提交号。
- 87 个正式执行包已经过 Sol Extra High 夜间资格审查：31 个为 `eligible_after_plan`、49 个为 `daytime_only`、7 个为 `completed_not_applicable`。唯一权威标记见 `docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md`；P1-01 至 P1-08 另列为历史完成记录。
- 正式包首次被任务领取后，源码级即时计划必须维护标记化昼夜交接块；`tools/handoff_status.py` 负责解析并核对 Git 证据。未领取的干净 `ready` 候选仍使用原五个夜间放行字段，不能伪造已完成状态。
- 2026-07-11 分支治理已删除完全并入主线的旧本地分支和已合并的临时远端分支。用户随后明确确认 `fine-grained-graph-training`、`resilient-grading`、`grading-paper-skill-workflow` 三个早期 worktree 及其分支均为放弃内容，并授权删除其中 14/9 个未合并提交和 21 项 worktree 本地 `user_data/`。当前只保留根目录、P1-16、P2-01 与 integration 四个 worktree。
- 删除三个旧 worktree 后发现根目录便携 `runtime/` 内容被同时清空；已从本机同版本 Python 3.12.1 缓存恢复、按锁定依赖补齐，并重新通过完整 smoke（967 passed）。真实两库指纹未变化。后续 Windows worktree 删除必须先审计目录联接、符号链接和其他 reparse point，任何指向 worktree 外部的目标都必须阻塞删除。
- WP1.2 Batch A-D 已本地验证：sessions、students、config、template/answer-region 的只读与安全轻写 API。
- WP1.2 Batch E 已本地验证一部分：Excel 报告导出、扫描分析、启动批改、首批 review 路由和 P1-15 Question Bank 只读 API；training、graph、ops 尚未开始。
- 最小 WP1.3 JobManager 已本地验证：通用创建/查询/取消 API，以及 `report_export`、`scan_analysis`、`grading_run` handler。P1-10 已把 jobs 完整 DDL 收敛到 `003_add_jobs.sql` 并改为 FastAPI lifespan 所有 manager；P1-11 已让 running 取消区分“请求”与“安全停止确认”，并给三类现有 handler 补齐副作用边界。
- 2026-07-10 P1-14 完整基线：聚焦回归 87 项通过；API 回归 85 项通过；完整 `tools/smoke_check.py` 全量测试 889 passed / 0 skipped / 0 failed，编译 338 个第一方文件且两库副本初始化幂等、`integrity_check=ok`。
- P1-09 已验证：不可访问绝对路径不再中断题库素材回退；两个 POSIX fork-only skip 已改为 Windows/POSIX 均真实执行的 spawn 测试，POSIX 原始 fork 回归仅在支持的平台收集。该包已随 PR #2 合并到 `main`。
- P1-10 已验证：`003_add_jobs.sql` 是 jobs 表与索引的唯一完整 DDL；JobStore 保留旧表补列兼容并确定关闭连接；应用启动恢复中断任务、退出关闭线程池，测试 override 仍由测试拥有。该包已随 PR #2 合并到 `main`。
- P1-11 已验证：queued/paused 可立即取消；running 只置 `cancel_requested`，由 `JobContext.raise_if_cancelled()` 在安全边界确认 cancelled；普通失败不伪装成取消，完成竞态可由已发布结果胜出。报告先写 staging、扫描 latest 用临时文件原子替换；整卷/混合批改停止派发并丢弃未发布的在途结果，failed-only 会恢复取消前 paper 状态。该包已随 PR #2 合并到 `main`。
- P1-12 已验证：复核读模型改为单 JOIN，60 个 result 的 API GET 连同会话检查不超过 2 次连接；跨 result 调分、元数据和总分在同一 SQLite 事务中提交或整批回滚；事务后批注失败返回脱敏、可重试的 `retry_required`，router 保持薄壳。该包已随 PR #2 合并到 `main`。
- P1-13 已验证：review item 返回语义化媒体 URL；原卷/批注页和答题区裁剪只通过 session/result/detail/page ID 访问，路径必须落在受控根且扩展名受白名单限制，裁剪只在内存生成。成功的 `report_export` job 只公开文件名与受控下载 URL；公开 job 结果递归移除路径字段，失败信息不回显内部路径。该包已随 PR #2 合并到 `main`。
- P1-14 已验证：OpenAPI 422 统一引用 `ErrorResponse`，二进制下载/媒体 200 契约与运行时一致；通用 Job 提交拒绝敏感键变体，公开 payload/result 与历史 review 元数据使用共享允许列表/路径脱敏；完成 future 自动回收，并发报告发布使用 job-owned 文件名。整包首轮复审发现均已用 TDD 修复，聚焦复审为 0 Critical / 0 Important / 0 Minor。该包已随 PR #2 合并到 `main`。
- P1-15 已验证：Question Bank 试卷列表、题目分页/组合筛选、详情、当前标签、富文本与预览元数据通过专用读服务公开；源题库以 M1-W1-W2-M2 有界临时快照读取，SQLite 只打开系统临时候选，持续变化返回脱敏 503，不创建或改写源 WAL/SHM。题目素材与预览只通过语义化 URL 访问，固定根、路径、扩展名和 `no-store` 均受控；未导入文件、未调用 AI、未写标签或 Schema。组合回归 156 passed，Question Bank/OpenAPI 64 passed，快速冒烟编译 342 个第一方文件且两库副本幂等、`integrity_check=ok`，整包复审 0 Critical / 0 Important / 0 Minor。该包已随 PR #2 合并到 `main`。
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
- 已领取正式包必须在即时计划中维护 `in_progress`、`resumable`、`waiting_review`、`waiting_user` 或 `verified_pending_integration` 交接状态；字段或 Git 证据不一致时一律安全停机。完整矩阵见执行包 README。
- 模型默认分工：Sol Extra High 负责跨域设计、实验门与高风险审查；Terra High/Medium 负责大多数实现；Luna 仅做低风险机械任务。升级条件见执行包 README。

## Git 与 Worktree 强制流程

并行分工、共享文件所有权和启动文本以 `docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION_2026-07-11.md` 为准。后续不需要用户重复强调，所有执行包默认遵守：

1. 一个功能 worktree/分支同一时间只实施一个执行包；下一包必须从最新已集成基线创建新分支。
2. 功能分支完成 TDD、包内回归、快速冒烟和指定独立复核后，提交给 `codex/p1-integration-verification`，不得直接写入 `main`。
3. 集成 worktree 一次合入一个包，解决共享入口/文档冲突；一波完成后运行组合回归和完整 `tools/smoke_check.py`，并核对真实两库指纹不变。
4. 集成分支验证通过后推送并通过 PR 合并 GitHub `main`；不默认直接 push `main`。
5. PR 合并后同步 `origin/main`、本地 `main` 和活动 worktree；删除已合并的远端 integration 临时分支，本地 integration 通道继续保留并跟踪 `origin/main`。下一批功能 worktree 必须基于该新提交。
6. 只有 `git branch --merged origin/main` 能证明已合并且不再承担活动任务的分支才可普通删除；有独有提交的分支必须保留或经用户明确确认后归档/丢弃。分支已合并本身不代表对应 worktree 可以删除。
7. 删除 linked worktree 前必须分别检查源码状态、`user_data/` 状态和 Windows reparse point；任何目录联接或符号链接指向 worktree 外部时必须停止。只有提交已合并、源码干净、`user_data/` 无任何本地项、无外部链接且不再承担任务时，才可从仓库根目录执行 `git worktree remove`。禁止手工递归删除 worktree 目录。
8. 任何阶段都不得暂存、提交或用 stash 打包真实 `user_data/`；真实数据库操作仍需单独明确授权。

用户明确要求启动某个执行包，即视为对该包标准 Git 流程的持续授权：门槛通过后可在功能分支提交，由 integration 分支完成合并验证、push、创建并合并 PR，再同步本地 `main`，无需逐步重复询问。该持续授权不包含直接 push `main`、force push、真实数据写入、不可逆删除，或丢弃 `origin/main` 尚未包含的历史提交；这些操作仍必须单独确认。

## 夜间自动化

每天 4:00 的“阅卷系统夜间4点推进”使用 `docs/superpowers/packages/NIGHTLY_AUTOMATION.md` 作为唯一权威提示词。夜间任务还必须读取资格矩阵、并行手册和候选包即时计划，并用 `tools/handoff_status.py` 验证已经领取的包；一次最多唤醒一个原任务或实施一个已放行包。

夜间自动实施只允许资格矩阵标记为 `eligible_after_plan`、已有专属干净 worktree、状态为 `ready`、执行模型为 Terra，且即时计划包含 `规划状态: ready_for_execution`、`规划模型: S-XH`、`允许夜间执行: yes` 和完整计划基线的包。夜间 Terra 完成后只能创建本地功能提交并留下 `waiting_review`，不得自行宣称独立复审或用户验收通过；不得执行 push、PR、integration/main 合并、worktree/分支清理或真实数据操作。

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
