# P3-08 文档解析与本地题块模块

**执行包：** P3-08
**计划日期：** 2026-07-23
**计划状态：** waiting_review
**计划模型：** 当前连续作业模型
**允许夜间执行：** yes
**计划基线：** 7c4f7968be90e4304eba14b44945adf5d67d5747
**交接基线：** 7c4f7968be90e4304eba14b44945adf5d67d5747
**用户自测：** none
**自测清单：** not_required
**授权修正预算：** 0/3

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P3-08
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

- **目标：** 把 `session_manager.py` 中的 DOCX/PDF 文字提取、富文本题块装配和本地题目/答案解析移入无模型依赖的文档解析深模块；正式配置来源服务依赖新模块，旧 `session_manager` 与 `rubric_auto_cropper` import 保持兼容。
- **包含：** `backend/document_parsing/` 的公开窄接口；DOCX 段落/表格/XML 文本提取；PDF 文字提取；纯文本与富文本题块拆分；内联答案、卷末答案、题型和本地标准答案的现有推断；受控富文本资产回调；配置来源服务接线；兼容导出；恶劣文档、结构等价、无模型依赖和 import guard 测试。
- **明确不包含：** 不修改题号规范化、题型判断、答案推断、答案等价形式、生成 fallback、prompt 文案、模型参数/重试/费用、100 分分配、质量告警、PDF 页面渲染/题图裁切、API/Schema、真实 `user_data/`；不提前实施 P3-09/P3-10。
- **验收条件：** 代表性 DOCX/PDF、内联题号、卷末答案、富文本公式/图片和损坏富文本回退 fixture 的文字或题块结构与基线完全等价；正式配置来源不再从 `session_manager` 取得解析实现；兼容 import 和并发受控目录行为不变；新模块静态及运行时不依赖模型客户端；受影响测试、快速冒烟和交接核验通过。
- **风险等级：** 中。主要风险是移动时改变文本去重/顺序、内联题号切分、答案与题号对应或富文本资产归属；不涉及业务写入、数据库迁移或真实模型费用。

## 集中调查与冻结问题清单

1. `session_manager.py` 同时容纳配置编排和约 1,000 行解析逻辑；解析公共入口只有 DOCX 文字、DOCX 富文本题块和纯文本题块三类，但内部混入生成模块，使边界难以单测。
2. `rubric_auto_cropper.extract_pdf_text()` 是文字解析，却与 PDF 页面渲染和题图裁切放在一起；本包只迁移文字函数，图片能力原样保留并通过兼容委托调用新实现。
3. 正式 `backend/config_workspace/sources.py` 的 DOCX/PDF 路径分别临时导入 `session_manager` 和 `rubric_auto_cropper`；应改为依赖新解析模块，同时保持公开返回和受控文件登记不变。
4. 富文本 DOCX 解析可写提取资产，但正式服务已经显式传入临时根、资产根、登记与受控写回调；新深模块必须要求这些依赖显式传入。只有旧兼容入口可以继续解析默认目录。
5. 现有测试覆盖连续题号、答案详解回退、并发目录隔离和 Windows junction 防护；仍缺少新模块导入边界、兼容身份和 PDF 文字入口的专门守卫。

## 故障场景与预期

| 场景 | 预期处理 |
|---|---|
| 重复操作 | 同一字节输入产生同序、同字段题块；不保存模块级状态。 |
| 同时操作 | 每次富文本解析只使用调用方传入的目录和回调，不修改全局路径、不串用资产。 |
| 中途退出/重新启动 | 已登记临时资产继续由既有来源服务清理/恢复；解析模块不新增持久状态。 |
| 失败重试 | 普通富文本解析异常继续回退纯文本；受控写回调失败必须原样上抛。 |
| 取消/部分完成 | 上层取消语义不变；题块完整后才返回，已写资产仍进入既有登记账本。 |
| 数据缺失/冲突 | 继续返回现有空结果/待复核字段；题号回退或非连续数字按既有规则截断或不拆分。 |

## 实施步骤

- [x] 先写新模块接口、结构等价、兼容 import、PDF 文字和无模型依赖守卫，取得 RED。
- [x] 建立 `backend/document_parsing/` 深模块并切换正式调用方，保留兼容导出。
- [x] 运行解析、配置来源、生成策略与受影响 API 回归，再运行快速冒烟。
- [ ] 对同一冻结 SHA 完成需求符合性与代码质量复审；阻塞问题统一修正，最多 3 次。

## 计划验证命令

```powershell
runtime\python\python.exe -m pytest tests\test_p3_08_document_parsing.py -q
runtime\python\python.exe -m pytest tests\test_grading_config_generation_policy.py tests\test_config_source_service.py tests\test_config_generation_job.py -q
runtime\python\python.exe tools\smoke_check.py --skip-tests
runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-23-p3-08-document-parsing-implementation.md --repo . --expected-handoff-base 7c4f7968be90e4304eba14b44945adf5d67d5747
```

## 规划复核结论

- Phase map、资格矩阵、结构基线、配置来源服务、解析调用图、PDF 工具和现有回归已交叉核对。
- 未发现需要改变题号、答案、题型、生成 fallback、文件生命周期或费用边界的产品选择；按“只移动实现、保持字节/结构等价”实施。
- 问题清单已冻结；P3-09/P3-10、旧功能工作区历史数据副本及范围外问题不进入本包。
- RED 阶段 5 项新守卫全部按预期失败；GREEN 后解析/生成策略 87 项、正式来源/配置生成 95 项、批次与 API 81 项全部通过。唯一中间失败来自旧测试仍把替身安装到 `session_manager`，测试接缝迁到新模块后目标用例与整组回归均通过，生产行为未改变。
- 快速冒烟通过：文档治理、505 个第一方 Python 文件编译、两库隔离副本初始化幂等及 `integrity_check=ok`；未调用真实模型，`user_data/` 无本地改动。

## 回退

本包不做 Schema、真实数据或模型迁移。回退正式来源接线并恢复兼容实现即可；测试临时文件由 pytest 与既有来源服务清理。
