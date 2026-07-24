# P3-15 客观题准入向导与断链脚本退役

**执行包：** P3-15  
**计划日期：** 2026-07-25  
**计划状态：** waiting_review
**计划模型：** 当前连续作业模型  
**允许夜间执行：** no  
**计划基线：** 24be1c3e264f50b0e97613aacf8489ca44ecbef5  
**用户自测：** none  
**自测清单：** not_required  
**授权修正预算：** 0/3

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P3-15  
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

- **目标：** 删除 Phase 2 已停用、当前无生产入口且运行必然失败的客观题准入向导 UI 与 runner，移除它们独占的 Streamlit 依赖；保留并回归验证正式客观题识别仍使用的裁剪校准能力。
- **包含：** `objective_admission_wizard_ui.py`、`run_objective_admission_wizard.py`；对应的静态调用与依赖守卫；`requirements.txt`、`constraints.txt` 中仅由该向导需要的 Streamlit；P2-22 延期守卫；P3-15 退役回归；`ARCHITECTURE.md` 与执行状态记录。
- **明确不包含：** 不删除或重写 `objective_crop_calibration.py`、`choice_recognition_chain.py`、客观题批量识别服务、题目注册表或评分逻辑；不恢复向导引用的缺失脚本；不改变 Vue 界面、API、识别阈值、裁剪坐标、成绩或数据；不修改、暂存、提交或运行根目录真实 `user_data/`。
- **验收条件：** 两个退役文件和全部活跃调用消失；第一方运行依赖不再包含 Streamlit；客观题裁剪识别经公开函数入口验证仍使用有效校准框并产出裁剪结果；P2-22 退役回归、客观题识别受影响回归和快速冒烟通过；真实两库 SHA-256 不变。
- **风险等级：** 中。删除范围本身已断链且不触碰数据，但若误删活跃校准模块或依赖，客观题裁剪识别会退化或失败，因此必须以保留行为测试和独立双路复审守卫。

## 集中调查与冻结问题清单

1. 生产入口已经切换到 FastAPI/Vue；全仓第一方 Python 调查只发现 `objective_admission_wizard_ui.py` 自身调用 `run_objective_admission_wizard.py`，其他活跃源码没有导入或调用二者。
2. runner 顺序调用的 5 个辅助脚本均不存在；误触入口不能完成目标流程。恢复这些无需求脚本会扩大产品范围，本包只删除失效入口。
3. `objective_admission_wizard_ui.py` 是当前唯一第一方 Streamlit 导入者；`requirements.txt` 与 `constraints.txt` 仍保留 Streamlit，仅服务于该延期入口。
4. `objective_crop_calibration.py` 不是死代码：`choice_recognition_chain.crop_choice_region()` 在活动识别链中动态导入其有效框选择与质量检查函数。该模块及识别链必须保留。
5. P2-22 回归当前把向导列为唯一允许的 Streamlit 导入；P3-15 需把该延期例外收紧为零，并新增包级退役与活跃裁剪行为测试。
6. 新功能 worktree 基于最新 `origin/main` 精确 SHA 创建，源码状态干净、没有本地 `user_data/` 变更、没有 Windows reparse point；工作树中随主线检出的历史跟踪数据只读保留，本包不访问或修改。

冻结后的根因共两组：一是断链向导及其独占运行依赖仍被保留；二是删除时必须保护活动裁剪校准链。调查发现的其他客观题工具、历史结构快照与 P1-29 验收资料均不纳入当前修改。

## 测试接口

1. **应用退役边界：** 通过仓库发布/启动资产守卫观察退役文件不存在、第一方运行源码无引用、运行依赖无 Streamlit。该边界继承已通过用户授权的 P3-15 验收标准。
2. **客观题识别边界：** 通过公开 `crop_choice_region()` 输入临时合成图片和识别框，验证活动校准模块仍能选择框、检查质量并输出裁剪文件；不直接断言私有实现。

## TDD 步骤

- [x] RED：先扩展退役守卫，使两个旧文件、引用和 Streamlit 依赖成为失败证据。
- [x] GREEN：删除向导 UI 与 runner，移除 Streamlit 依赖，使退役守卫通过。
- [x] 新增并通过活动 `crop_choice_region()` 校准链行为测试，证明保留模块仍可用。
- [x] 运行 P2-22、客观题识别与依赖相关的受影响回归。
- [x] 更新架构事实与执行状态，运行快速冒烟和真实两库指纹守卫。
- [ ] 冻结候选后并行进行需求符合性与代码质量复审；如有阻塞问题，统一修复一次并只做限定终审。
- [ ] 通过独立删除提交进入 M3-08 integration，完成受影响验证、PR、主线同步和状态收口。

## 计划验证命令

```powershell
runtime\python\python.exe -m pytest tests\test_p3_15_objective_admission_retirement.py tests\test_p2_22_streamlit_retirement.py -q
runtime\python\python.exe -m pytest tests\test_objective_batch_recognition_service.py tests\test_objective_escalation.py tests\test_objective_path_manager.py -q
runtime\python\python.exe tools\smoke_check.py --skip-tests
runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-25-p3-15-objective-admission-retirement-implementation.md --repo . --expected-handoff-base 24be1c3e264f50b0e97613aacf8489ca44ecbef5
```

## 阶段记录

- 集中调查约 15 分钟：完成一次全仓静态调用调查、两个退役文件运行链阅读、活动裁剪校准调用链确认、依赖与 P2-22 守卫核对，以及新 worktree 源码、数据和链接安全检查。
- 原始发现 6 条，按根因去重为 2 组；0 个需要用户决定的产品选择。当前剩余工作为 RED→GREEN、受影响验证、双路复审、integration 与主线收口。
- 实现与自动验证约 15 分钟：退役守卫先以 1 项失败确认 RED，最小删除后包级/P2-22/客观题识别受影响回归 42 项通过；快速冒烟通过，包含文档治理、525 个第一方 Python 文件编译及两库隔离副本初始化幂等。
- 静态复核确认活动第一方 Python 调用方为零；客观题公开裁剪入口在系统临时目录生成 30×20 裁剪图并保留 `recognition_box` 来源。根目录真实两库 SHA-256 与 P3-15 开工前一致。
- 当前剩余工作为双路独立复审、必要时一次统一修复、M3-08 integration 与主线收口；自动验收已通过，版本仍须完成复审和主线流程后才允许把 P3-15 记为 merged。

## 回退

本包形成独立删除提交，可整体回退恢复两个旧文件及 Streamlit 依赖。回退只恢复历史入口，不代表其引用的缺失脚本重新受支持；不得借回退修改真实数据或恢复已退役的 Streamlit 生产入口。
