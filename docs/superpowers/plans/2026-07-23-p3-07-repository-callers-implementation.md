# P3-07 服务、API 与报告调用方切换

**执行包：** P3-07  
**计划日期：** 2026-07-23  
**计划状态：** in_progress  
**计划模型：** 当前连续作业模型  
**允许夜间执行：** yes  
**计划基线：** b07560a389b772e03ff5e7758f0d3031cd6a7131  
**交接基线：** b07560a389b772e03ff5e7758f0d3031cd6a7131  
**用户自测：** none  
**自测清单：** not_required  
**授权修正预算：** 0/3

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P3-07  
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

- **目标：** 让正式运行的 grading/session/review/report/API 与后台任务调用方依赖 P3-04 至 P3-06 建立的 Repository 接口；`DBManager` 只保留一个版本的兼容门面，不再作为活跃调用方的依赖类型。
- **包含：** 仓储集合/工厂与 FastAPI 依赖注入；学生、会话、答卷、结果、复核、模板、答题区、设置调用方切换；报告、分析、媒体、工作台、扫描与后台任务接线；只读请求快照的 borrowed Repository；为保持现有业务行为所必需的会话状态、配置发布、跨仓储原子编排接口；import guard、API E2E 和受影响回归。
- **明确不包含：** 不删除 `DBManager`，不删除旧 CLI/Legacy API 或旧表，不修改 Schema/迁移、API 响应、评分计算、配置文案、重试规则、文件格式或真实 `user_data/`；不调用真实模型；不提前实施 P3-08 以后模块拆分。
- **验收条件：** 活跃 `backend/` 代码不直接导入或构造 `DBManager`，唯一允许的兼容组装点有显式白名单；API 与后台任务改用 Repository；只读请求继续复用同一借入连接且拒绝写入；跨仓储写操作保持原子；既有 API E2E、报告/评分/复核回归、完整冒烟和静态 import guard 通过。
- **风险等级：** 高。接线横跨多个正式流程；错误依赖可能造成请求读到不同快照、写操作被拆成部分提交、后台任务重复或错误状态。所有写测试只使用 pytest 临时数据库和临时文件目录。

## 集中调查与冻结问题清单

1. P3-04 至 P3-06 已建立七组 gateway，但正式 API、报告、分析、媒体、工作台、扫描和 Job 仍有二十余个模块导入 `DBManager`；仅改类型名不能满足本包目标，调用必须落到明确的领域 Repository。
2. `backend/api/dependencies.py` 目前以 `get_grading_db()` 为共同依赖；学生和部分会话路由已取子 Repository，其余服务仍接收整个旧门面。应改为仓储集合/窄依赖，同时保持测试依赖覆盖方式。
3. `RequestReadContext` 为跨两库一致读取借入同一 SQLite 连接；切换后必须用 `BorrowedReadOnlySessionProvider` 构建只读仓储，不能另开连接，也不能允许写事务。
4. 会话配置发布、运行状态、清空运行数据和永久删除是跨字段或跨仓储原子操作；它们属于仓储编排接口，不能拆成多个 gateway 独立提交。
5. `DBManager` 仍承担初始化、兼容错误映射、备份和旧入口；本包不删除它。正式 composition root 可为兼容层初始化数据库，但活跃服务、路由和 Job 不得再把它作为业务依赖。
6. 报告与分析中的输出 mapping 是业务投影，保留在服务；数据库行 mapping 只保留 Repository 一份，删除旧门面中的重复投影委托。
7. Streamlit 日常入口已退役；仍受生产批处理复用的残留工具需要切换，纯旧 CLI 留给 P3-16，不在本包删除。

## 故障场景与预期

| 场景 | 预期处理 |
|---|---|
| 重复操作 | 继续由既有条件更新、幂等 upsert 和 Job 状态保护处理；接线不新增自动重试。 |
| 同时操作 | 会话启动、配置发布、结果发布和跨仓储清理继续使用同一 immediate transaction/条件更新。 |
| 中途退出/重新启动 | 已提交数据库状态保持原规则；未提交事务整笔回滚；JobManager 继续接管 interrupted 状态。 |
| 失败重试 | Repository 异常沿既有 API/Job 映射返回；不吞错、不额外调用模型、不重复发布结果。 |
| 取消 | 保持现有取消与会话状态转换；未进入数据库提交的文件/任务操作不制造新状态。 |
| 部分完成 | 配置发布、清空、永久删除、复核批量调整任一步失败都不得留下部分数据库变更。 |
| 数据缺失 | 继续返回既有 `None`、空列表、404/409 或 Job 失败语义，不自动创建业务数据。 |
| 数据冲突 | 快照写请求失败关闭；当前答卷归属、revision、模板所有权和配置期望值冲突继续拒绝。 |

## 实施步骤

- [ ] 先写 import guard、仓储集合/只读快照与关键 API 接线测试，取得 RED。
- [ ] 建立正式 Repository 组装接口，补齐必要的会话/跨仓储高层 gateway，保留 `DBManager` 兼容委托。
- [ ] 按服务、API/报告、Job 三个调用域切换，每个域只运行受影响测试。
- [ ] 运行 API E2E、组合回归、完整冒烟和 import guard，冻结候选。
- [ ] 对同一冻结 SHA 并行完成需求符合性与代码质量复审；仅在存在阻塞问题时使用授权预算统一修正，最多 3 次。

## 计划验证命令

```powershell
runtime\python\python.exe -m pytest tests\test_p3_07_repository_callers.py -q
runtime\python\python.exe -m pytest tests\test_api_app.py tests\test_api_session_routes.py tests\test_api_template_region_routes.py tests\test_api_review_routes.py tests\test_api_report_routes.py -q
runtime\python\python.exe tools\smoke_check.py
runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-23-p3-07-repository-callers-implementation.md --repo . --expected-handoff-base b07560a389b772e03ff5e7758f0d3031cd6a7131
```

## 规划复核结论

- Phase map、P3-01 调用图、P3-04 至 P3-06 Repository 契约、最新 API 依赖与活跃调用方已交叉核对。
- 未发现需要改变产品行为、Schema、评分语义或费用边界的未决选择；按最保守的“只换接线、保持结果等价”实施，无需中断询问。
- 当前问题清单已冻结；修改前既有问题或相邻新需求只记录，不自动扩大本包。

## 回退

本包不做 Schema 或真实数据迁移。按调用域回退接线提交即可恢复通过 `DBManager` 兼容门面运行；临时数据库和临时文件随测试清理，真实数据无需恢复。
