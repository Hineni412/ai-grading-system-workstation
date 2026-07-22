# P3-02：领域数据模型下沉

**执行包：** P3-02
**计划日期：** 2026-07-23
**规划状态：** ready_for_execution
**规划模型：** 当前连续作业模型
**允许夜间执行：** yes
**计划基线：** 5cd050a5b22b97f508aa78d84be7492335ec1710
**交接基线：** 5cd050a5b22b97f508aa78d84be7492335ec1710
**用户自测：** none
**自测清单：** not_required

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P3-02
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

- **目标：** 把 `QuestionGradingDetail`、`SecondaryError`、`GradingResult` 和 `ExamPaperGroup` 四个跨模块数据类型移到不依赖评分器、扫描器或数据库服务的 `backend/domain_models.py`，解除 `db_manager.py` 对 AI/扫描服务的反向导入。
- **包含：** 新领域模型模块；`ai_grader.py` 与 `scanner.py` 保留一个版本的兼容导入；生产调用方按类型依赖迁到新模块；字段、默认值、可变/冻结属性、构造参数、相等性、repr 与 `dataclasses.asdict` 结果的等价测试；静态循环/反向依赖守卫。
- **明确不包含：** 不改变字段、类型注解、默认值、序列化、评分、扫描、匹配或数据库逻辑；不移动 `AIGrader`、`Scanner`、`ScanAnalysis` 等行为类；不改 API、Schema、迁移、前端、真实数据或模型调用。
- **验收条件：** 新旧 import 指向同一类对象；已知构造样例和序列化完全等价；`db_manager.py` 不再导入 `ai_grader` 或 `scanner`；领域模型模块只依赖标准库；现有 grading/scanner/db_manager 受影响测试通过。
- **风险等级：** 中等。运行行为不应变化，但共享类型的 import 身份和默认值一旦漂移会影响大量调用方。

## 集中调查与冻结问题清单

1. 三个主要类型当前分别定义在 `ai_grader.py` 和 `scanner.py`；`QuestionGradingDetail` 还直接引用冻结的 `SecondaryError`，因此四个类型必须作为同一根因批次移动。
2. `db_manager.py` 为类型标注和结果保存直接导入 AI/扫描模块，形成 P3-02 明确要求解除的反向依赖；业务 SQL 本身不需要这些服务。
3. `grading_service.py`、`hybrid_batch_grading_service.py`、`objective_batch_recognition_service.py` 和 `evidence_atlas.py` 混合导入类型与行为；只切换其中的类型导入，私有评分 helper 和扫描常量继续留在原模块。
4. 大量既有测试和外部调用仍从 `ai_grader`/`scanner` 导入这些类型；兼容别名必须保留，测试无需批量改写，借此证明旧入口仍可用。
5. 当前没有 `backend/domain_models.py` 或等价无服务依赖模块，也没有守卫阻止 `db_manager.py` 再次导入 AI/扫描服务。

## 故障场景与预期

| 场景 | 预期处理 |
|---|---|
| 重复导入/不同导入顺序 | 新旧入口始终返回同一类对象，不生成两套 dataclass 身份。 |
| 同时操作、重试、取消、中途退出、重启 | 不适用：本包只调整静态模块边界，不增加运行状态或持久化操作。 |
| 部分迁移 | 静态守卫与身份测试阻止 `db_manager` 保留反向导入或兼容入口指向不同类。 |
| 数据缺失/冲突 | 构造与序列化 golden 测试固定字段顺序、默认值和嵌套 `SecondaryError` 结果。 |
| 循环依赖 | `backend/domain_models.py` 只允许标准库 import，`db_manager.py` 不得导入 AI/扫描服务。 |

## 已确认测试 seam

1. 公共 Python import：`backend.domain_models`、`ai_grader` 和 `scanner` 暴露的四个类型身份。
2. 四个 dataclass 的公开构造、`dataclasses.fields` 与 `dataclasses.asdict` 结果。
3. 通过 AST 检查的模块依赖边界：领域模块仅标准库，`db_manager.py` 无 AI/扫描反向导入。

## 实施步骤

- [x] 新增 P3-02 契约测试并取得 RED：新模块缺失、旧新身份尚不存在、反向导入仍存在。
- [x] 最小创建领域模型模块，并把原定义改为兼容导入，使身份/字段/序列化测试 GREEN。
- [x] 逐个切换生产类型调用方，保持行为类与 helper 原导入不动；运行 import/编译和聚焦回归。
- [ ] 运行受影响回归、快速冒烟、双路独立复审、交接核验和真实两库指纹复核。

## 计划验证命令

```powershell
runtime\python\python.exe -m pytest tests\test_p3_02_domain_models.py -q
runtime\python\python.exe -m pytest tests\test_atomic_major_retry.py tests\test_grading_completeness.py tests\test_scan_manual_decisions.py tests\test_secondary_error_persistence.py -q
runtime\python\python.exe tools\smoke_check.py --skip-tests
runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-23-p3-02-domain-models-implementation.md --repo . --expected-handoff-base 5cd050a5b22b97f508aa78d84be7492335ec1710
```

## 回退

本包不改数据和行为；回退新领域模块与对应 import 提交即可恢复原定义位置。兼容旧 import 在本版本不会删除。

## 实施记录（等待独立复审）

- 实现提交 `e9534a9778ba01179cb5574e7087381fb98b4ce7` 新增无服务依赖的 `backend/domain_models.py`，四个 dataclass 的字段、默认值、冻结属性和嵌套结构保持原样；`ai_grader`/`scanner` 旧入口直接导入同一类对象。
- 第一条公共 import 测试先因新模块缺失得到 RED；反向依赖守卫随后先确认 `db_manager.py` 仍导入 `ai_grader` 得到 RED，再迁为领域模型 import 后转绿。
- P3-02 新契约 3 项通过；grading、scanner、hybrid、objective、retry 和 secondary error 受影响回归 70 项通过；变更模块编译通过，快速冒烟通过（文档治理、514 个第一方 Python 文件编译、两库临时副本初始化幂等）。
- 未运行全量 pytest；没有 Schema、数据库写入、真实模型或 `user_data/` 操作。当前剩余工作为同一冻结候选的 Spec/Standards 双路复审、必要时一次统一修复和交接核验。
