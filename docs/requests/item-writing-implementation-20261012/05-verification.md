# 05 验证、测试与文档

## 1. 测试数量（用户 2026-10-12 授权放宽）

2026-10-12 运行 `tools/run_test_suite.py --inventory`：测试文件 22/22，测试函数 110/110，后端展开用例 113/113，均已到上限。

用户授权本功能增加 **1 个测试文件、最多 4 个测试函数**，展开用例相应最多增加 4 个。实施时：
- 上限改为文件 23、函数 114、展开用例 117。改 `tools/run_test_suite.py` 的 `TEST_FILE_LIMIT`、`TEST_FUNCTION_LIMIT`、`BACKEND_CASE_LIMIT`（`tests/conftest.py` 引用后者）和 `docs/testing/README.md`“测试数量约束”一段的数字，保持一致。新测试文件按现有方式登记到 `tools/test_suite_manifest.py` 的分组（旧命题练习的测试曾在这里登记，见 `git show fb6a4648 -- tools/test_suite_manifest.py`）；`tests/test_test_suite_runner.py` 若断言了上限数字，一并更新。
- 不得再提高上限，不得用改名、排除收集或把独立检查合并成大循环来绕过。
- 收尾报告三项数量的前后值。

新文件 `tests/test_authoring_practice.py`，只含以下 4 个函数，全部用合成库与假模型网关，不读真实 `user_data`，不发真实请求：

1. `test_authoring_migration_preserves_rows_and_moves_version_domain`
   - 在迁移前的结构上插入 1 行 decompose 作品（`is_deleted=1`）和 1 行 photo 资产，执行 049。两行逐列相等；`kind='material'`、`role='document'` 可写入，非法值失败。
   - 依次写入五张 authoring 表：每次只有 `training_ops` 加 1，`materials`、`materials_global` 与 `question_versions` 不变。
   - `integration/data_generation.py` 的覆盖检查报告完整覆盖。
2. `test_authoring_work_save_reenter_and_version_conflict`
   - 建材料与作品，保存原型示例作品内容，重新读取后内容逐字段相等。
   - 旧的 `expected_save_seq` 保存返回 409，库中内容不变。
   - 自检结果包含“第（1）问评分步骤合计 3 分，与该问分值 4 分不符”这一条（示例作品故意留的问题）。
   - 插入一条评价记录后再保存，生成第 2 版，第 1 版内容不变。
   - 转正同一版本两次：只新建 1 道题，第二次返回同一题号；未转正作品不出现在 `questions` 表。
3. `test_authoring_review_requests_and_local_scoring`
   - 假网关按顺序返回分析、解题、量表结果，统计调用次数：一次成功评价恰好 3 次（若题目分析经核实不止 1 次，按核实后的真实上限断言，并与预估页显示一致）。
   - 用同一 `client_request_token` 再提交：返回原任务，调用次数不增加；同令牌、不同版本返回 409。
   - 量表返回原型示例的九个水平：本机总分为 64，各维度得分与原型一致；模型返回中夹带的分数被忽略。
   - 某条 `quote` 不在作品原文中：`quote_found=false`。科学性高于上限：记入 `cap_violations`，水平不改。
   - 第 2 步抛出超时：记录为 `unknown` 或 `failed`，调用次数为 2，没有第 3 次和重发。
   - 量表少一个维度：记录为失败，`error_category='validation'`。
4. `test_authoring_extraction_batches_filters_and_validates`
   - 合成题库含两章，含生活情境、数学文化、新定义、题干不足 60 字（含选项时超过 60 字）、同模板重复题与已抽取过的题（含停用行来源）：只选一章时候选只来自该章；不含新定义、短题干和已抽取的题；同模板只留一道。
   - 假网关返回代码块包裹的裸数组（复现试运行的真实格式）：全部条目被解析。
   - 含数字的描述被标出 `local_issues` 但仍建材料；`usable=false` 建为停用材料，再次预估时该题不再出现；描述相似的两条合并为一份材料并记录两个来源题。
   - 23 道候选：计划请求数 3；第 2 批失败时停止，第 1 批材料保留，没有第 3 次调用。

断言写法按 `docs/testing/README.md`“测试写什么”：比较保存后的数据与完整输出，预期值独立写出，不调用被测函数计算预期。

前端不新增单元测试；按测试文档人工检查布局，运行受影响前端文件的代码规范检查、类型检查与构建。

## 2. 验证命令（按需选择最窄的）

```powershell
& .\runtime\python\python.exe -m pytest tests\test_authoring_practice.py -q
& .\runtime\python\python.exe -m pytest tests\test_p3_11_schema_version_gate.py tests\test_api_question_bank_write_routes.py -q
& .\runtime\python\python.exe tools\generate_schema_baseline.py --check
& .\runtime\python\python.exe tools\run_test_suite.py --inventory
& .\runtime\python\python.exe tools\check_documentation.py
cd frontend; npm run build
```

最后一次性跑 `tools\run_test_suite.py quick`。重建共享 `frontend/dist` 或重启 8035 前，确认没有其他任务在用。

## 3. 真实环境验收

- 迁移按 01 第 6 节执行并核对。
- 8035 页面验收按 03 第 5 节，截图三种宽度。
- 导入 10 条试运行结果（02 第 6 节，执行前取得用户确认）：页面上 7 份为待核对、3 份在“已停用”下；“从题库抽取情境”弹窗中这 10 道题计入“已抽过”，不再列入待抽。
- 打开抽取弹窗，选八上任意章，各章题数与只读统计口径一致；只点到确认弹窗为止，不提交（批量抽取需用户在页面上自己确认）。
- 用一份材料完整走完八步，自检正确；不点 AI 评价（真实评价需用户另行授权）。
- 回复中写明：没有发出任何真实模型请求，或列出经授权发出的请求及其 operation_id。

## 4. 需要同步更新的文档

| 文档 | 改动 |
|---|---|
| `docs/product/KNOWLEDGE_AND_TRAINING.md` | 新增“命题训练（教研提升）”一节：材料来源与审核、八步作答、本机自检、AI 评价三请求与费用确认、九维量表与计分、版本、转正、与真卷隔离 |
| `docs/product/GRADING.md` | “试卷评价（教研提升）”一节首句改为教研提升有两个页签，本节只描述试卷评价 |
| `ARCHITECTURE.md` | 领域代码入口加 `question_bank/authoring/`；数据版本一段写明 authoring 表属于 training_ops 域 |
| `CONTEXT.md` | 新增词语：命题训练、材料（情境素材、母题、主题限定）、原生情境、命题量表 |
| `docs/maintenance/packaging.md` | 第 32 行：四张表恢复使用，新增材料表，迁移 049 |
| `docs/maintenance/storage-policy.md` | 上传文件位置与保留规则（01 第 5 节） |
| `docs/testing/README.md` | 上限数字（第 1 节） |
| `docs/requests/README.md` | 本交接的索引行改为实施状态，并写明未实现的部分（模型识别兜底、成长档案统计） |
| `docs/security/SECURITY.md` | 若该文档列出发送给模型的内容类别，补上教师命题作品与题库题干 |

改写或删除过时的句子，不在末尾追加。

## 5. 提交

- 逐个本地提交，只含本任务文件；共用文件中有其他任务的未提交改动时先协调，保留对方修改。
- 提交说明按 `AGENTS.md` 写验证、测试、文档三行。
- 不 push，不建远程 PR。
