# P3-10 配置归一化、评分约束与质量告警

**执行包：** P3-10
**计划日期：** 2026-07-23
**计划状态：** waiting_review
**计划模型：** 当前连续作业模型
**允许夜间执行：** yes
**计划基线：** 09f2df87c2fbc091281c427edc54126bc8b20bf6
**交接基线：** 09f2df87c2fbc091281c427edc54126bc8b20bf6
**用户自测：** none
**自测清单：** not_required
**授权修正预算：** 1/3

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P3-10
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

- **目标：** 把模型生成配置的 Schema 归一、知识点字段整理、客观题/解答题硬规则、100 分整数约束、配分结果校验/应用和质量告警拆成可独立单测的深模块；`session_manager` 只保留兼容名称，P3-09 orchestration 直接依赖新 policy adapter。
- **包含：** `backend/config_generation/` 下 normalization、score allocation、local facts 和 quality policy；现有 56 个规则函数及其常量/依赖的原样迁移；配置编辑/发布和 P3-09 facade 接线；兼容导出；代表性 golden payload、幂等、严格校验、告警和循环依赖守卫。
- **明确不包含：** 不修改 100 分缩放、单题 18 分上限、同类客观题同分、题型纠偏、答案等价、complete-set、response mode、作图要求、知识点或 warning 文案；不修改 Prompt、模型、超时、请求次数、重试、API/Job、数据库 Schema、持久化事务或真实 `user_data/`；不调用真实模型；不删除历史 per-question 生成路径。
- **验收条件：** 代表性输入每次归一化后的完整 payload 与迁移前同次运行 golden 完全等价，并保持现有函数各自已经承诺的幂等范围；配分结构、严格校验、应用和 100 分结果等价；质量告警列表顺序/文案等价；配置编辑、发布、P3-09 批次/重试和旧 `session_manager` 名称继续通过；新规则模块不导入 `session_manager`，`backend.config_generation` 内部循环依赖为零；受影响测试、快速冒烟、双路复审和交接核验通过。
- **风险等级：** 高。任何微小规则漂移都可能改变正式评分配置；全部验证只用内存 payload、假模型或临时文件，不写真实业务数据。

## 集中调查与冻结问题清单

1. `session_manager.py` 中 56 个相互调用的规则函数约 1,850 行，跨越 Schema/知识点、答案硬规则、100 分缩放、AI 配分校验、质量告警和本地题块事实；当前只能通过巨型模块整体导入。
2. P3-09 已把 orchestration 变成独立服务，但 policy adapter 仍动态导入 `session_manager` 的 12 个回调；P3-10 必须把这些回调接到独立模块，消除新服务对巨型模块的反向依赖。
3. `backend/config_workspace/editor.py` 与 `publish.py` 仍直接从 `session_manager` 取得告警和最终校验；应改用新规则模块，API、revision、发布文件和原子绑定保持不变。
4. 现有策略测试覆盖大量字段级行为，但缺少一个同时包含客观题、主观题、知识点别名、答案别名和告警的完整 golden payload，以及规则模块无循环/无巨型模块依赖守卫。
5. `score_policy.py` 已拥有全局整数分配算法；本包复用它，不复制或改写搜索、18 分上限、题型规范化和同类客观题同分规则。
6. P3-09 复审记录的旧活动批次死实现与部分规则辅助函数共享。P3-10 只删除本包已迁移且不再被历史 per-question 路径独占的规则定义；历史生成流程的整体删除仍留给后续瘦身，避免越界。

## 故障场景与预期

| 场景 | 预期处理 |
|---|---|
| 重复操作 | 保持迁移前逐次输出完全一致；不把历史上第二次才补齐的空兼容字段擅自提前，告警刷新不重复、不改变非质量告警。 |
| 同时操作 | 全部规则只修改调用方传入对象，不保存模块级可变状态；不同 payload 互不串扰。 |
| 中途退出/重新启动 | 规则层不新增持久状态；上层 checkpoint/revision/发布恢复语义不变。 |
| 失败重试 | 同一草稿重新进入规则层得到相同结果，不产生额外模型请求。 |
| 取消 | 规则层无后台任务和吞异常；取消仍由 P3-09 Job/checkpoint 边界处理。 |
| 部分完成 | 缺字段按既有 alias/fallback 补齐；无法满足严格 Schema、题号对应或 100 分约束时继续明确失败，不发布半成品。 |
| 数据缺失或冲突 | 冲突知识点字段、越界/重复配分 ID、缺答案、空步骤和不一致分数继续按现有拒绝或 warning 规则处理。 |

## 实施步骤

- [x] 先固定完整逐次 golden payload、配分、质量告警、既有幂等范围和依赖边界测试，取得 RED。
- [x] 按功能族机械迁移规则函数，复用 `score_policy.py`，建立窄公开接口和兼容导出。
- [x] 切换 P3-09 policy adapter、配置编辑/发布及 `session_manager` facade，消除新模块循环依赖和重复 helper。
- [x] 运行 P3-10 聚焦测试、生成策略、批次/Job/API、配置编辑/发布受影响回归和快速冒烟。
- [ ] 冻结同一功能 SHA，完成需求符合性与代码质量双路复审；阻塞问题统一修正，最多 3 次。

## 计划验证命令

```powershell
runtime\python\python.exe -m pytest tests\test_p3_10_config_normalization.py -q
runtime\python\python.exe -m pytest tests\test_grading_config_generation_policy.py tests\test_batched_config_generation.py tests\test_p3_09_config_generation_orchestration.py tests\test_config_editor_service.py tests\test_config_editor_nullable_policy.py tests\test_api_config_editor.py tests\test_config_workspace_drafts.py tests\test_legacy_config_publish.py tests\test_config_generation_job.py tests\test_api_config_generation_jobs.py -q
runtime\python\python.exe tools\smoke_check.py --skip-tests
runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-23-p3-10-config-normalization-implementation.md --repo . --expected-handoff-base 09f2df87c2fbc091281c427edc54126bc8b20bf6
```

## 规划复核结论

- Phase map、M3-03 精确基线、P3-09 policy adapter、配置编辑/发布、`score_policy.py` 和现有生成策略测试已交叉核对。
- 没有需要用户决定的评分方案：采用“函数体与常量机械迁移、完整 payload/文案/顺序等价”，不改变任何规则或费用边界。
- 问题清单已冻结；Prompt/编排、API/Job、数据库和历史 per-question 流程不进入本包。
- RED 阶段因四个新规则模块尚不存在而按预期在收集期失败；实现后 4 项逐次 golden、告警顺序/文案、配分和依赖边界测试通过。
- 一次性提取脚本按 AST 函数边界机械搬移 56 个规则函数和 3 组常量，成功后已删除且未进入候选；`session_manager.py` 净减少约 1,900 行，目标规则定义只保留在新模块。
- 冻结候选的 P3-10、生成策略、P3-09 编排、配置编辑/发布、Job 和 API 合并受影响回归共 229 项通过；快速冒烟通过文档治理、517 个第一方 Python 文件编译和两库隔离副本初始化幂等。
- 验证未调用真实模型，功能工作区 `user_data/` 无本地改动；根目录真实两库 SHA-256 与开工基线一致。
- 首轮需求符合性与代码质量复审原始意见 2 条，去重后为 1 个 `Important`：`local_facts.py` 搬移了 `merge_equivalent_forms` 调用却遗漏其 import；带本地标准答案的正常批次会在模型请求后触发 `NameError`，属于本次修改直接引入。
- 第 1/3 次统一修正先增加本地可信答案与等价形式合并用例并稳定取得 RED，再补回原依赖 import；P3-10 聚焦 5 项、生成策略/批次/P3-09/Job 147 项和快速冒烟通过，差异检查干净。最终复审只检查该登记问题、修正区域和直接回归。

## 回退

本包不做数据库迁移、真实数据写入或模型调用。回退规则模块接线并恢复 `session_manager` 兼容定义即可；配置文件 Schema 和已保存数据不变。
