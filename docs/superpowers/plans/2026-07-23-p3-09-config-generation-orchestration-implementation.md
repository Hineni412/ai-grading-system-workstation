# P3-09 Prompt 构建与配置生成编排

**执行包：** P3-09
**计划日期：** 2026-07-23
**计划状态：** verified_pending_integration
**计划模型：** 当前连续作业模型
**允许夜间执行：** yes
**计划基线：** 7a9aef6a2311308ec7253a588f9585c227e44af6
**交接基线：** 7a9aef6a2311308ec7253a588f9585c227e44af6
**用户自测：** none
**自测清单：** not_required
**授权修正预算：** 0/3

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P3-09
**交接状态：** verified_pending_integration
**功能提交：** 6ee0f97ce51281a19d714b0e162c7bc6283180b3
**自动验证：** passed
**独立复审：** passed
**用户验收：** not_required
**真实数据指纹：** unchanged
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** independent_candidate_allowed
<!-- HANDOFF_STATUS_END -->

## 任务边界（已冻结）

- **目标：** 把配置生成的纯 Prompt、顺序分批、失败批次重试、整卷统一配分、结果合并和进度/检查点编排移入独立深模块；正式配置 Job 使用新服务，`session_manager` 只保留兼容入口。
- **包含：** `backend/config_generation/` 的 Prompt、Gateway 单次请求适配器和 orchestration service；批次划分、每批单请求、失败分类、选择完整失败批次重试、配分单请求、checkpoint/progress 回调；正式 Job 接线；旧函数兼容 facade；Prompt 快照、假 LLM、部分失败/重试/取消检查点和输出等价测试。
- **明确不包含：** 不修改 Prompt 文案、模型名称、超时、请求参数、批次规则、请求次数、自动重试次数、100 分分配规则、题型/答案/Schema 归一化、质量告警、发布/绑定事务、API/Job 公共契约、数据库 Schema 或真实 `user_data/`；不调用真实模型；不提前实施 P3-10。
- **验收条件：** 活动批次与配分 Prompt 字节完全等价；假 Gateway 下请求类型、顺序、次数和参数等价；成功、部分失败、完整失败批次重试、仅配分重试、检查点和进度结果等价；正式 Job 不再从 `session_manager` 导入配置生成编排；新 orchestration 不读取环境路径或模块级可变状态；旧兼容入口继续通过；受影响测试、快速冒烟、双路复审和交接核验通过。
- **风险等级：** 高。模型请求次数、失败草稿和配分结果直接影响费用与评分配置；本包以显式依赖注入隔离 P3-10 规则，并只使用假客户端验证。

## 集中调查与冻结问题清单

1. 活动配置 Job 通过 `session_manager` 的五个函数完成生成、失败批次查询和重试；Job 本身还负责会话 revision、checkpoint、发布和原子绑定，这些持久化边界不属于本包，必须保持原位。
2. 批次编排把选择/填空按顺序最多 3 题成组，其他题固定单题；每批恰好一次 Gateway 请求，全部批次完成后再恰好一次 AI 配分。模型或本地校验失败只记录草稿，不在编排层自动重试。
3. 编排同时调用 Schema 归一、题型/答案本地事实、100 分校验和质量告警；这些属于 P3-10。P3-09 只把它们定义为显式 policy callbacks，不移动或改写实现，避免循环依赖和业务口径漂移。
4. Prompt 分散在 `session_manager.py`，活动批次 Prompt 与配分 Prompt 没有稳定快照；迁移前先固定完整字符串和输入序列化结果。
5. 现有 Job 测试通过模块级兼容名称安装替身。正式调用切换到新 facade 时保留这些接缝，避免测试兼容性变成业务行为变化。
6. 旧并发单题生成仍有历史兼容测试，但生产 Job 已把 `per_question` 归一为 `batched`。本包不删除旧路径，只迁移当前活动批次流程；历史瘦身另记，不借重构扩大范围。

## 故障场景与预期

| 场景 | 预期处理 |
|---|---|
| 重复操作 | 同一输入与假 Gateway 响应得到同序批次、Prompt、合并结果和 checkpoint；已完成 AI 配分的草稿恢复时不再调用模型。 |
| 同时操作 | service 不保存模块级状态；会话锁、revision 复核和原子发布继续由现有 Job 层负责。 |
| 中途退出 | 每批及配分前后继续调用既有 checkpoint；Job 重启仍从已保存草稿恢复。 |
| 重新启动 | 已成功批次不重复生成；完整草稿按既有规则只完成待处理配分或本地发布。 |
| 失败重试 | 只允许完整失败批次；按当前分批规则重新映射，成功批次不重发，配分失败只重发配分请求。 |
| 取消 | service 在每个 checkpoint/progress 边界不吞掉上层取消异常；Job 保留现有取消与草稿清理规则。 |
| 部分完成 | 失败批次及脱敏错误写入 meta，跳过配分并返回可重试草稿；成功题保持原顺序。 |
| 数据缺失或冲突 | 空/重复题号、损坏批次记录、旧失败批次无法映射、返回题号越界均在任何额外请求前或当前单请求后安全失败。 |

## 实施步骤

- [x] 固定活动批次 Prompt、配分 Prompt、请求顺序/次数和部分失败重试快照，取得 RED。
- [x] 建立 `backend/config_generation/` Prompt、Gateway adapter、显式 policy callbacks 与 orchestration service。
- [x] 把正式配置 Job 切到新 facade，并让 `session_manager` 兼容入口委托新实现；保持现有模块级测试接缝。
- [x] 运行 P3-09 聚焦测试、批次/策略/Job/API 受影响回归和快速冒烟。
- [x] 冻结同一功能 SHA，完成需求符合性与代码质量双路复审；阻塞问题统一修正，最多 3 次。

## 计划验证命令

```powershell
runtime\python\python.exe -m pytest tests\test_p3_09_config_generation_orchestration.py -q
runtime\python\python.exe -m pytest tests\test_batched_config_generation.py tests\test_config_generation_job.py tests\test_api_config_generation_jobs.py tests\test_grading_config_generation_policy.py -q
runtime\python\python.exe tools\smoke_check.py --skip-tests
runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-23-p3-09-config-generation-orchestration-implementation.md --repo . --expected-handoff-base 7a9aef6a2311308ec7253a588f9585c227e44af6
```

## 规划复核结论

- Phase map、M3-03 精确基线、正式 Job、批次生成、失败批次重试、Prompt 构建与现有测试接缝已交叉核对。
- 没有需要用户决定的产品方案：采用最保守的“流程迁移、规则回调、字节/请求/结果等价”，真实模型调用保持为零。
- 问题清单已冻结；P3-10 的归一化、评分规则和质量告警实现不进入本包。
- RED 阶段因 `backend.config_generation` 尚不存在而按预期在收集期失败；实现后 6 项新契约测试通过。
- 冻结候选的 Prompt/Gateway/批次、生成策略、正式 Job 和 API 合并受影响回归共 188 项通过；快速冒烟通过文档治理、511 个第一方 Python 文件编译和两库隔离副本初始化幂等。
- 验证未调用真实模型，功能工作区 `user_data/` 无本地改动；根目录真实两库 SHA-256 与开工基线一致。
- 需求符合性与代码质量两路在同一冻结 SHA `6ee0f97ce51281a19d714b0e162c7bc6283180b3` 上完成首轮复审；原始意见 2 条，去重后 1 个 `Suggestion`，0 `Critical`、0 `Important`。
- 唯一非阻塞建议是 `session_manager.py` 仍保留一份已被文件尾 facade 覆盖的活动批次历史实现，未来可能与新服务漂移。正式 Job、公共兼容名称、请求次数、重试、检查点和结果当前均走新服务；按冻结边界不删除仍被历史单题路径共用的辅助函数，本建议转入后续历史代码瘦身，不启动无目标终审。
- RED、分组回归、合并受影响回归与快速冒烟合计约 3.5 分钟；双路复审约 5 分钟。当前剩余工作只有 integration 逐包合入、受影响验证和 Index 收口；P3-09 工程验收通过，M3-03 是否发布仍取决于 P3-10 与批次末门槛。

## 回退

本包不做 Schema、真实数据或模型迁移。回退正式 Job 和 `session_manager` facade 接线即可恢复旧模块内编排；草稿、发布文件和 API 契约不变。
