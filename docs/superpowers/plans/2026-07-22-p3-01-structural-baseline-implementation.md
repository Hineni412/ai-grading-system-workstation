# P3-01：调用图、覆盖范围与性能证据基线（临时连续候选）

**执行包：** P3-01
**计划日期：** 2026-07-22
**规划状态：** ready_for_execution
**规划模型：** 当前连续作业模型
**允许夜间执行：** yes
**计划基线：** 5fde1b6b534f71e4157eab7c46f04233cffdce24
**交接基线：** 5fde1b6b534f71e4157eab7c46f04233cffdce24
**用户自测：** none
**自测清单：** not_required

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P3-01
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

- **目标：** 为后续 P3 拆分固定一个可重复生成的结构基线：核心模块的静态导入关系、公开符号、测试覆盖映射、迁移文件快照，以及现有 P1-26/P1-27 端点性能证据的可核对索引。
- **包含：** 一个只读 Python 报告命令、其公共命令行测试、两份由该命令生成并提交的 JSON/Markdown 基线；报告仅使用仓库源码、迁移文件和已提交的性能报告。
- **明确不包含：** 不拆分或修改生产业务代码；不改 API、评分、状态、Schema、迁移、启动入口或前端；不运行真实模型；不读取、写入、暂存或提交 `user_data/`；不重新执行耗时性能实验、P2-20 真实流程或用户验收。
- **验收条件：** 从干净工作树可把同一源码生成内容一致且不含绝对路径/真实数据的两种报告；报告列出 P3 后续拆分的核心对象、静态和动态导入线索、公开 API、直接测试模块、两库迁移文件摘要和已提交的性能/真实流程证据入口；缺失受控输入时明确失败且不发布半份报告。
- **风险等级：** 低。改动仅为离线调查工具、测试和文档；输出写入版本库文档目录，且使用临时文件后替换。

## 临时连续作业例外

P2-21 已在 M2-04 临时 integration 的 `5fde1b6b534f71e4157eab7c46f04233cffdce24` 完成逐包自动验证，formal 人工验收仍待完成。`EXECUTION_INDEX.md` 的 2026-07-22 例外允许仅依赖 P2-21、非破坏性的 P3-01 在此临时线独立候选；这不改变 P3 的正式 `planned` 状态、也不满足正式依赖。P3-01 及后续候选在 P2-21 人工验收前不得进入 `main`；P2-22、删除/退役、Schema/迁移、真实数据、真实模型费用和需产品裁决的包不在例外内。

## 已完成的集中调查

1. P3-01 的固定包定义要求 AST/import 调查、公开方法清单、Schema 快照、性能证据和真实流程对照；明确禁止本包重构生产代码。
2. `ARCHITECTURE.md` 已把 `web_app.py`、`session_manager.py`、`db_manager.py` 列为主要大型跨域模块；P3-02 至 P3-07 将首先依赖这份边界证据。
3. 已有 `tools/generate_schema_baseline.py` 与 `tests/test_schema_baseline.py` 守卫两库“运行时初始化/迁移”一致性；本包只记录已提交 migration 文件的摘要，不重新生成或修改迁移。
4. 已有 P1-26/P1-27 生成数据性能报告和 P1-29/P2-20 流程验收记录；本包只建立指向这些证据的版本化索引，不重跑测量或真实流程。
5. 当前没有通用、可复现的 P3 静态调用图/公开 API/测试映射工具。现有少量 AST 断言只服务于单独业务契约，不能作为拆分前基线。

## 故障场景与预期

| 场景 | 预期处理 |
|---|---|
| 重复运行 | 对同一源码和同一输出目录产生字节一致报告。 |
| 并行运行 | 不是支持的工作流；每次使用独立输出目录，避免两次发布互相覆盖。 |
| 运行中断 | 先在输出目录写临时文件，再替换正式文件；不会把未完整的单文件当作完成结果。 |
| 重启/失败重试 | 删除临时文件后可重新运行；正式旧报告保持可读，直到新文件完整生成。 |
| 受控输入缺失或语法损坏 | 以明确错误退出，不发布新报告。 |
| 源码在扫描期间变化 | 用扫描的源文件 SHA-256 清单标识结果；调用方必须在干净工作树生成提交证据。 |
| 真实数据/模型调用 | 不适用：工具只读取版本库内允许的代码和文档路径，拒绝 `user_data/`。 |

## 已确认的测试边界（seams）

1. `tools/build_p3_01_baseline.py --output-dir <临时目录>`：外部调用者可观察到的 JSON/Markdown 报告、失败退出和无绝对路径保证。
2. `docs/architecture/p3-01-structural-baseline.{json,md}`：受控的提交证据入口，可由同一命令以相同源码重建。

## 实施步骤

- [ ] **1. 先写公共命令行的失败测试。** 在临时仓库夹具中放入最小的一组 Python、迁移和性能证据文件；断言报告包含相对路径、静态 import、动态 import 线索、公开符号、测试映射、Schema 与性能证据，并拒绝缺失输入且不留下输出。
- [ ] **2. 以最小实现提供只读报告命令。** 仅用 `ast` 和标准库扫描规定的一方 Python 根；记录导入边、字面量动态导入线索、顶层公开类/函数与类的公开方法；从测试静态导入关联核心对象；为两库迁移和既有性能/验收文件记录 SHA-256、摘要与限制说明。
- [ ] **3. 生成并提交当前候选的证据。** 在功能实现提交后，从干净候选生成 JSON 和 Markdown 到 `docs/architecture/`；报告记录功能提交 SHA 和相对路径，不记录本机绝对路径、`user_data` 或业务正文。
- [ ] **4. 完成分层验证与交接。** 运行 P3-01 测试、Schema/性能相关回归、快速冒烟和 `handoff_status.py`。P3-01 是低风险离线包，按结果决定是否需要限定独立复审；无用户可见操作，不生成用户验收清单。

## 计划验证命令

```powershell
runtime\python\python.exe -m pytest tests\test_p3_01_structural_baseline.py -q
runtime\python\python.exe -m pytest tests\test_schema_baseline.py tests\test_performance_report.py tests\test_request_connection_benchmark.py -q
runtime\python\python.exe tools\build_p3_01_baseline.py --output-dir <临时目录>
runtime\python\python.exe tools\smoke_check.py --skip-tests
runtime\python\python.exe tools\handoff_status.py --plan docs\superpowers\plans\2026-07-22-p3-01-structural-baseline-implementation.md --repo . --expected-handoff-base 5fde1b6b534f71e4157eab7c46f04233cffdce24
```

## 预期提交边界

1. `docs: claim P3-01 structural baseline` — 仅本计划和上方交接块。
2. `test: define P3-01 structural baseline contract` — RED 测试。
3. `feat: add P3-01 structural baseline report` — 工具与 GREEN 测试。
4. `docs: record P3-01 structural baseline evidence` — 只含由工具生成的证据。
5. 交接锚点与最终交接提交仅改本计划。

## 回退

本包所有变化均是单独的工具、测试与报告提交；回退相应提交即可，不影响生产代码、迁移或真实数据。
