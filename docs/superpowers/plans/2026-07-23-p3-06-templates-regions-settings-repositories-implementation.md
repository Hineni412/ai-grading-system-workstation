# P3-06：Templates、Regions 与 Settings Repository

**执行包：** P3-06
**计划日期：** 2026-07-23
**规划状态：** verified_pending_integration
**规划模型：** 当前连续作业模型
**允许夜间执行：** yes
**计划基线：** 999928f20eb69afba544f0cde43e300972c21172
**交接基线：** 999928f20eb69afba544f0cde43e300972c21172
**用户自测：** none
**自测清单：** not_required
**授权修正预算：** 0/3

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P3-06
**交接状态：** verified_pending_integration
**功能提交：** 5832c9439c46fde25fc26fc7d12338dd6c3551e2
**自动验证：** passed
**独立复审：** passed
**用户验收：** not_required
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** independent_candidate_allowed
<!-- HANDOFF_STATUS_END -->

## 任务边界（已冻结）

- **目标：** 把 `session_templates`、`answer_regions` 与 `app_settings` 的 SQL 下沉到 session-bound Repository，并让 `AnswerRegionCommitService` 使用新 Repository，同时保留 `DBManager` 一个版本的兼容门面和既有数据库+文件快照补偿协议。
- **包含：** `TemplateRepository`、`RegionRepository`、`SettingsRepository` 及 gateway；模板创建、激活、分析路径、确认和快照代次状态；答题区只读、整批替换、映射更新、单条增删与坐标更新；应用设置键值读写；会话存储路径与永久删除的同事务组合；`AnswerRegionCommitService` 适配；临时数据库映射、所有权、并发、回滚、快照失败与恢复回归。
- **明确不包含：** 不修改 Schema、迁移、坐标归一化、模板 fingerprint、`workflow_state.json` 格式、草稿格式、快照文件命名、API 响应或真实 `user_data/`；不提前切换 P3-07 的全部 API、媒体、Job、报告调用方；不调用真实模型。
- **验收条件：** 现有 region commit/concurrency/snapshot 测试通过；模板与答题区整批提交同事务；跨会话 template_id 被拒绝且零写入；快照写入或清理失败仍保留正确 pending/token 并可重试；过时代次不能清除新代次；应用设置读写语义不变；`DBManager` 对应方法不再含目标表 SQL；`AnswerRegionCommitService` 不通过旧数据库方法绕过 Repository。
- **风险等级：** 高。模板与答题区是正式批改前置数据；数据库已提交而文件快照失败必须保留可恢复状态，并发代次不能互相覆盖。所有写验证只允许使用 pytest 临时数据库和临时文件目录。

## 集中调查与冻结问题清单

1. `DBManager` 当前同时承担模板、答题区、设置 SQL；`collect_session_storage_paths` 与永久删除还跨 P3-05 结果域、P3-06 模板域组合，必须在同一个 `RepositorySession` 中保持删除顺序和回滚。
2. `replace_answer_regions_atomic` 在 `BEGIN IMMEDIATE` 中校验 template/session 所有权、替换全部区域、标记模板确认状态并生成 snapshot token；这些步骤不能拆成多个 gateway 提交。
3. `AnswerRegionCommitService` 的文件补偿协议是既有业务事实：数据库提交成功后写确认快照和 workflow，失败保持 pending；只有匹配当前 token 才能清除 pending，草稿变化和旧代次均失败关闭。
4. 坐标默认值、`region_uuid` 生成、`mapping_status` fallback、页面排序与确认条件属于现有行为，本包只搬持久化位置，不重新解释。
5. P3-07 才切换 API、媒体、Job 与其他活跃调用方；本包只适配包定义明确列出的 `AnswerRegionCommitService`，其余调用继续经 `DBManager` 兼容门面。
6. `app_settings` 当前只由会话配置状态使用；键和值继续按字符串保存，缺失键继续返回调用方传入的 default，不引入类型化设置或新键。
7. `initialize()` 中建表、补列、索引和触发器仍由 P3-11 处理；P3-06 不以迁移 SQL 为由触碰运行时 DDL。

## Module、Interface 与 seam

- `TemplateRepository` 隐藏模板行、确认与 snapshot generation 状态；`RegionRepository` 隐藏答题区映射、批量替换与排序；`SettingsRepository` 隐藏应用键值。
- session-bound Repository 不拥有连接和事务；gateway 打开一次 owned/borrowed session，跨模板/答题区操作在同一事务组合。
- `DBManager` 保留原方法名、返回值与异常，改为委托；兼容层可继续承担文件路径收集与跨 P3-05/P3-06 永久删除编排，但不再内联目标表 SQL。
- `AnswerRegionCommitService` 只依赖模板/答题区高层接口；文件锁、验证、快照原子发布、workflow 补偿与草稿清理仍留在服务。

## 故障场景与预期

| 场景 | 预期处理 |
|---|---|
| 重复操作 | 模板 upsert 保持单行；设置 upsert 覆盖同键；答题区整批替换不追加旧区域；快照同 token 内容相同可安全重试。 |
| 同时操作 | 整批替换使用 immediate 事务；每次生成独立 token；旧代次不能读取后清除新代次 pending，也不能覆盖新 workflow。 |
| 中途退出/重新启动 | 数据库已提交但快照未完成时保留 pending/token；重启后 `retry_pending_snapshot` 读取正式区域继续恢复。 |
| 失败重试 | 文件写入、workflow 写入、草稿清理或完成标记失败均返回稳定错误并保留可恢复证据；不自动重放数据库替换。 |
| 取消 | 本服务没有独立取消状态；调用在数据库提交前失败则整笔回滚，提交后按 pending 补偿协议处理。 |
| 部分完成 | 模板所有权校验、区域删除/插入、确认状态和 token 标记任一步失败全部回滚；永久删除跨域任一步失败全部回滚。 |
| 数据缺失 | 缺失模板继续返回 `None`/not ready；缺失设置返回 default；空区域使模板 not ready。 |
| 数据冲突 | 跨会话 template_id 拒绝；重复或空白 region UUID 继续由既有约束/生成规则处理；snapshot token 必须非空且精确匹配。 |

## 实施步骤

- [x] 新增模板、答题区、设置 Repository 公共契约与服务适配测试并取得 RED。
- [x] 实现 session-bound `TemplateRepository`、`RegionRepository`、`SettingsRepository` 和 gateway，逐个 vertical slice 转 GREEN。
- [x] 把 `DBManager` 对应方法改为兼容委托；存储路径与永久删除在一个 `RepositorySession` 中组合。
- [x] 让 `AnswerRegionCommitService` 使用新 Repository，保留锁、验证、snapshot/workflow/草稿补偿语义。
- [x] 运行包内、region commit/concurrency/snapshot、session config、API 受影响回归与快速冒烟。
- [x] 冻结候选后在同一 SHA 并行完成需求符合性与代码质量复审；仅在存在阻塞问题时使用授权预算统一修正，最多 3 次。

## 计划验证命令

```powershell
runtime\python\python.exe -m pytest tests\test_p3_06_templates_regions_settings_repositories.py -q
runtime\python\python.exe -m pytest tests\test_answer_region_db.py tests\test_answer_region_commit_service.py tests\test_session_config_state.py tests\test_api_template_region_routes.py tests\test_session_cleanup.py -q
runtime\python\python.exe tools\smoke_check.py --skip-tests
runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-23-p3-06-templates-regions-settings-repositories-implementation.md --repo . --expected-handoff-base 999928f20eb69afba544f0cde43e300972c21172
```

## 规划复核结论

- Phase map、最新架构、P3-03 至 P3-05 Repository 事实、模板/答题区数据库方法、`AnswerRegionCommitService` 补偿流程和现有并发测试已交叉核对。
- 未发现需要改变坐标、fingerprint、workflow、API 或 Schema 的未决产品选择；无需中断询问。
- 计划按“数据库事务进入 Repository、文件补偿留在服务、跨域删除在同一 session 编排”限制范围，不提前实施 P3-07。
- 自动验证完成：Repository P3-03 至 P3-06、region commit/concurrency/snapshot、session config、API 与清理共 110 项组合回归通过；扩展调用方回归另有 123 项通过；快速冒烟和 496 个第一方 Python 文件静态编译通过。
- 双路初审完成：原始意见 1 条，去重后 1 条 `Suggestion`；已在交接文档提交中同步 `ARCHITECTURE.md` 当前结构事实，未改代码、未启动修正复审，授权修正预算仍为 0/3。
- 当前剩余工作量仅为 integration 逐包合入与验证、更新 Index；真实模型调用与根目录真实数据写入均为零，真实两库指纹不变。

## 回退

本包不做 Schema 或真实数据迁移。回退 Repository、兼容委托和服务适配提交即可恢复原内联 SQL；临时数据库和临时快照目录随测试清理，真实数据无需恢复。
