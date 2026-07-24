# P3-13 `knowledge_id` 到 `knowledge_ids` 读写切换

**执行包：** P3-13  
**计划日期：** 2026-07-24  
**计划状态：** in_progress  
**计划模型：** 当前连续作业模型  
**允许夜间执行：** no  
**计划基线：** 8cc462623ac840286ae3dc8a7534dc6bc504f87f  
**用户自测：** none  
**自测清单：** not_required  
**授权修正预算：** 0/3

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P3-13
**交接状态：** in_progress
**功能提交：** none
**自动验证：** pending
**独立复审：** pending
**用户验收：** not_required
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** report_only
<!-- HANDOFF_STATUS_END -->

## 任务边界（已冻结）

- **目标：** 把活动 `session_details` 明细的列表字段 `knowledge_ids` 设为唯一写入与主读取真相；旧单值 `knowledge_id` 在一个兼容期内改为由列表首项生成的只读列，留给 P3-14 决定最终删除。
- **包含：** 安全聚合审计；grading forward migration 006；`session_details` 原子 rebuild 与幂等回填；结果 Repository、报告快照、掌握度/诊断读取链路切主读；所有活动写入停止显式写旧列；兼容输出继续从列表首项提供 `knowledge_id`；直接 SQL 测试夹具和性能合成数据同步；架构文档。
- **明确不包含：** 不删除 `knowledge_id`；不改 `grading_details` 旧表及 `DBManager.save_result()` Legacy API（由 P3-16 退役）；不改题库 `canonical_knowledge_id`、配置/Rubric 中的兼容单值、标签来源、知识点文本、顺序或去重业务语义；不处理 P3-14 的 question-bank sync 四列；不执行根目录真实库 migration，不改、暂存或提交真实 `user_data/`。
- **验收条件：** 当前库副本、至少三类历史副本和干净空库可先备份后原子升级；缺失列表按旧单值无损回填；已有合法列表原样保留；旧值与列表冲突、非法 JSON、空列表、非文本或空白元素均失败且原库零部分重建；新写入只提交列表；旧兼容读值严格等于列表首项；grading、retry、review、report、analytics、diagnosis、training 和 graph golden 结果等价；重复/并发启动幂等；完整门槛、双路复审、快速冒烟和真实两库指纹守卫通过。
- **风险等级：** 高。SQLite 需要重建 `session_details`，同时影响评分写入、报告和诊断读链；列遗漏、列表顺序变化或错误回填可能改变教师看到的知识点归组。

## 集中调查与冻结问题清单

1. 当前 Schema 的 `session_details.knowledge_id` 是 `TEXT NOT NULL` 普通列，`knowledge_ids` 是可空 JSON 文本；活动 Repository 每次同时写两列，多处查询仍显式选择或排序旧列。`grading_details` 具有相同双列，但只属于仍保留的 Legacy API，本包不扩大到该表。
2. 根目录真实阅卷库只读一致性副本：`session_details` 3042 行全部有合法非空列表，旧单值均包含在列表中；2751 行是与旧值相同的单项列表，291 行是多项列表。`grading_details` 当前 0 行。`integrity_check=ok`。
3. 根目录 `user_data/backups` 的 74 份 `.db` 只读汇总：74 份均可打开，84 张目标明细表均已有 `knowledge_ids`；共 43890 行全部为合法非空列表，0 行需回填、0 个非法值、0 个旧值/列表冲突，8592 行为多项列表。输出只含计数，不输出知识点正文或路径。
4. 当前真实两库审计前后 SHA-256 不变：阅卷库 `93fee56e23ea072ac48351b1e6616d7af7f4b35ceb2b4779890e8d059fb841cd`；题库 `e1e5123ad54c9e8af5984bdcc5182a8f7a3038a1707f98ab26f168f4577a88b8`。
5. 现有业务事实已足以冻结映射：列表是主真相且顺序有意义，旧兼容值只能取列表首项；缺失列表时可唯一回填 `[knowledge_id]`。真实与 74 份历史副本没有冲突，因此不需要用户裁决数据映射；遇到未知冲突一律拒绝迁移，不自动改值。
6. Master Plan 明确旧单值应降级为生成列或删除；P3-13 明确进入兼容只读期且 P3-14 才评估删除，因此本包采用数据库生成列，而不是继续双写或提前删除。
7. 直接影响面冻结为：迁移清单/窄表重建放行、`backend/repositories/results.py`、`backend/repositories/reporting.py`、`grading_service.py`、`integration/mastery_adapter.py`、活动报告/诊断消费与对应测试夹具。旧 `grading_details`、题库标签和配置/Rubric 兼容字段是相邻范围，不随本包修改。

## 故障场景与预期

| 场景 | 预期处理 |
|---|---|
| 重复操作 | 006 已登记时不 rebuild、不额外备份；应用写入列表归一化保持幂等。 |
| 同时操作 | 复用迁移写锁和同连接重试；只有一个启动者执行 006，其他启动者随后校验并跳过。 |
| 中途退出 | `session_details` 新表、复制、旧表替换、索引恢复和 006 登记处于同一事务；失败全部回滚，升级前备份保留。 |
| 重新启动 | 未登记 006 时从原 Schema 重试；已登记时只校验 checksum 与最终 Schema。 |
| 失败重试 | 旧/新冲突、非法 JSON、空列表或非文本元素不自动修正；修复隔离副本输入后从备份或原库重试。 |
| 取消 | 启动迁移无交互取消点；失败即停止服务。评分 Job 的取消语义不变。 |
| 部分完成 | 不允许只完成回填或只切一部分读写点；任一结构/数据步骤失败都回滚整个 006。 |
| 数据缺失或冲突 | `knowledge_ids` 缺失且旧值有效时回填单项列表；旧值空白、已有列表非法或旧值不在列表中时安全失败。 |

## 实施步骤

- [ ] 获得 P3-13 Schema migration 实施授权；授权前不创建 migration 或修改生产代码。
- [ ] RED：新增安全聚合审计与 migration 006 契约测试，覆盖缺失列表回填、已有多项列表保持、冲突/非法值原子失败、生成列只读和重复/并发启动。
- [ ] GREEN：新增 `006_knowledge_ids_primary.sql`，仅重建 `session_details`；`knowledge_ids` 成为非空合法列表，`knowledge_id` 成为列表首项的只读生成列；迁移器只对 006 声明的该表窄放行。
- [ ] RED/GREEN：活动结果写入只提交 `knowledge_ids`；Repository row mapping、报告快照、掌握度/诊断读取以列表为主，并仅为兼容输出派生首项。
- [ ] 更新直接 SQL 测试夹具、浏览器测试服务器和性能合成数据，不改变知识点列表顺序、标签文本、报告统计或诊断口径。
- [ ] 在系统临时目录对干净库、当前真实库只读副本和至少三类历史副本预演；验证行数、主外键、索引、Schema、列表 JSON、报告/诊断 golden 和源库指纹。
- [ ] 运行聚焦测试、受影响回归和快速冒烟；冻结同一功能 SHA 后进行需求符合性与代码质量双路复审，统一修复最多 3 次，必要时只做一次限定终审。
- [ ] 合入 M3-06 integration，运行一次完整门槛、真实两库指纹守卫，通过 PR 合入主线并同步正式状态。

## 计划验证命令

```powershell
runtime\python\python.exe -m pytest tests\test_p3_13_knowledge_ids.py -q
runtime\python\python.exe -m pytest tests\test_p3_05_papers_results_review_repositories.py tests\test_retry_failed_grading.py tests\test_atomic_major_retry.py tests\test_report_completeness.py tests\test_diagnosis_profile_service.py tests\test_api_graph_routes.py tests\test_api_training_routes.py -q
runtime\python\python.exe tools\migration_rehearsal.py --target grading
runtime\python\python.exe tools\smoke_check.py --skip-tests
runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-24-p3-13-knowledge-ids-implementation.md --repo .
```

## 阶段记录

- 集中调查约 30 分钟；完成 1 次当前真实阅卷库只读一致性副本审计、1 次 74 份历史备份只读汇总、1 次源码写入/读取点调查。未运行测试或代码复审，未修改生产代码或真实数据。
- 原始调查问题 7 组，按根因冻结为 3 组：Schema 双列契约、活动双写/旧列读取、迁移失败恢复。没有未知真实值或产品映射问题。
- 当前剩余工作量：Schema migration、活动读写切换、测试夹具、隔离副本矩阵、受影响回归、双路复审、integration 完整门槛与 PR。P3-13 尚未通过验收，版本不因本计划允许发布。

## 回退

代码可整体回退 P3-13 提交，但已执行的 006 不删除、不倒退。若 006 失败或升级后发现版本级问题，停止应用并使用该 migration 自动生成的升级前完整备份恢复阅卷库；禁止手工把生成列改回普通列、拆分回写旧/新字段或在运行中修改真实知识点 JSON。
