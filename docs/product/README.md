# 产品设计与实施入口

## 当前实现节奏

- 第一波可同时启动 F0、P4-00/P4-01、A00、B00；
- F0 是一次性公共底座，整体完成后集中测试、复审和验收一次，合入 M1 后结束；
- 此后 P4、A、B 三条业务线长期并行；
- 三条业务线不在每个小任务或实施包后测试、复审，持续实现到整条线进入用户最终验收阶段后再集中复测和双复审；
- 影响结果且无法从项目查明的问题及时询问用户，优先汇总当前已知问题一次问完；存在真实依赖时可以分步骤再问。

## 三路总控

- [Phase 4 与两大教师工作台三路并行实施总索引](./TEACHER_WORKSPACES_PARALLEL_IMPLEMENTATION.md)

## P4：知识图谱与个性化训练

- [个性化训练闭环产品与实现设计](./PERSONALIZED_TRAINING_LOOP.md)
- [Phase 4 详细实施计划](../superpowers/packages/phase-4-execution-packages.md)

## A：初中数学备课工作台

- [初中数学备课工作台设计](./TEACHING_PREP_WORKBENCH.md)
- [初中数学备课工作台详细实施计划](./TEACHING_PREP_WORKBENCH_IMPLEMENTATION_PLAN.md)

## B：班主任工作台

- [班主任工作台总体设计](./CLASS_TEACHER_WORKBENCH.md)
- [班主任工作台详细实施计划](./CLASS_TEACHER_WORKBENCH_IMPLEMENTATION_PLAN.md)
- [班主任德育事务国家规范基线与 SOP 初稿](./CLASS_TEACHER_AFFAIRS_SOP_BASELINE.md)
- [B00/B01 治理关口](../superpowers/packages/phase-6-deferred.md)

动态状态和下一动作只看 [执行索引](../superpowers/packages/EXECUTION_INDEX.md)。当前实现事实只看根目录 `ARCHITECTURE.md`。
