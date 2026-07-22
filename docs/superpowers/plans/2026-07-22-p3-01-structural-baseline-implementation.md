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
**交接状态：** waiting_review
**功能提交：** branch_head
**自动验证：** passed
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
2. `docs/architecture/p3-01-structural-baseline/manifest.json`：受控的单一提交证据入口；它只会指向同一不可变 release 内、摘要校验一致的 JSON/Markdown 文件对。

## 实施步骤

- [x] **1. 先写公共命令行的失败测试。** 在临时仓库夹具中放入最小的一组 Python、迁移和性能证据文件；断言报告包含相对路径、静态 import、动态 import 线索、公开符号、测试映射、Schema 与性能证据，并拒绝缺失输入且不留下输出。
- [x] **2. 以最小实现提供只读报告命令。** 仅用 `ast` 和标准库扫描规定的一方 Python 根；记录导入边、字面量动态导入线索、顶层公开类/函数与类的公开方法；从测试静态导入关联核心对象；为两库迁移和既有性能/验收文件记录 SHA-256、摘要与限制说明。
- [x] **3. 生成并提交当前候选的证据。** 在功能实现提交后，从干净候选生成 JSON 和 Markdown 到 `docs/architecture/`；报告记录功能提交 SHA 和相对路径，不记录本机绝对路径、`user_data` 或业务正文。
- [x] **4. 完成分层验证与交接。** 运行 P3-01 测试、Schema/性能相关回归和快速冒烟；独立发布恢复按中风险要求进入限定复审。无用户可见操作，不生成用户验收清单。

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

## 实施记录（等待限定复审）

- 功能提交 `cae3be997ef7d19ca061535146e3eba6b4d0cded` 新增只读报告命令和两条公共命令行契约测试；先得到“工具不存在”的 RED（2 项失败），再转为 GREEN。
- 证据提交 `59de8c8dad9160a33865e12849772c8bde390eff` 由该命令生成当前结构基线：289 个第一方 Python 文件、2,787 条静态导入、3 条动态导入线索；核心拆分对象的公开符号、直接调用方、直接测试文件、两库 migration 摘要及 P1-26/P1-27/P1-29/P2-20 证据入口均已固定。
- 自动验证：P3-01、Schema 与性能证据关联回归共 46 项通过（40.88 秒，1 条既有 Starlette/httpx 弃用警告）；快速冒烟通过（文档治理、512 个第一方 Python 文件编译、两库临时副本初始化幂等）。未跑全量 pytest；没有真实数据、真实模型或真实流程调用。
- 本阶段原始问题数 0；等待一次限定独立复审。候选在 P2-21 formal 人工验收前仅保留在临时分支，不进入 integration 的主线合并路径或 `main`。

## 复审记录（当前停止）

- 首轮并行复审共登记 4 条意见，按根因去重为 3 条 `Important`：漏记 `from importlib import import_module` 的动态导入、允许把输出/输入指向 `user_data`、以及双报告发布失败时可能只留下其中一份；另有 1 条已删除的无用变量 `Suggestion`。三项 Important 均属于本次修改直接引入或当前任务原本遗漏。
- 已统一修复并仅复测受影响范围：动态导入现在包含 `pages/题库管理.py` 的实际调用；输入和输出均拒绝 `user_data`；常规发布前先完整生成两份内容。P3-01 契约测试 4 项通过（23.46 秒），快速冒烟通过（文档治理、512 个第一方 Python 文件编译、两库临时副本初始化幂等）。
- 原两名复审者的最终限定复审仍发现 1 条 `Important`：当前替换旧报告的过程中若发生 `KeyboardInterrupt`、强制结束或断电，临时备份可随临时目录清理而消失，留下旧 Markdown 与缺失/新 JSON 的不一致状态。这违反本计划“中断后旧报告保持可读、不发布半份报告”的承诺。
- 按项目最多“一轮初审 + 一次统一修复 + 一轮最终复审”的规则，**不再自动开启第三轮修复**。P3-01 当前未通过验收、不得进入 integration 或 `main`；需用户决定是否另建一个明确边界的报告发布恢复设计任务。P2-21 的 formal 人工验收也仍是上游独立待办。

## 回退

本包所有变化均是单独的工具、测试与报告提交；回退相应提交即可，不影响生产代码、迁移或真实数据。

## 2026-07-23 独立发布恢复修复（边界已冻结）

- **授权：** 用户已明确批准继续，并授权同类失败修复最多自动执行两次；本次记为第 1 次。
- **目标：** 只解决最终限定复审登记的 1 条 `Important`：进程中断、强制结束或断电发生在双报告替换期间时，下一次启动仍能解析到一对完整、相互匹配的 JSON/Markdown 报告。
- **包含：** 不可变 release 目录、单一原子 manifest 激活点、公开的已发布报告解析函数、旧版直出报告到首个 release 的安全迁移、只覆盖该恢复协议的测试与证据更新。
- **明确不包含：** 不改变扫描内容、报告字段或业务代码；不引入数据库、Schema、模型、网络、前端或真实 `user_data/` 操作；不处理多个进程同时发布，调用方仍需串行运行。
- **验收条件：** 新 release 的两份文件完整写入并校验后才切换 manifest；激活前中断继续解析旧 release，激活后只解析新 release；失败或陈旧 staging 不会成为活动结果；相同源码重复生成得到相同 release 与 manifest；原有输入/输出安全守卫继续通过。
- **风险等级：** 中等。原因是修改文件发布与中断恢复协议，但仅作用于离线结构报告。
- **测试 seam：** 公开命令行、`resolve_published_report(output_dir)` 和 manifest 指向的不可变文件对；只在文件系统原子替换边界注入失败，不断言私有调用顺序。

### 故障场景与预期

| 场景 | 预期处理 |
|---|---|
| 重复操作 | 内容相同则复用确定性 release ID，活动 manifest 字节一致。 |
| 同时操作 | 不支持；调用方保持串行，未承诺多发布者互斥。 |
| 中途退出/取消 | manifest 激活前旧 release 仍为活动结果；未完成 release 不可解析。 |
| 重新启动 | 只依据 manifest 解析活动 release，忽略陈旧 staging 和未引用 release。 |
| 失败重试 | 可重新生成并激活同一确定性 release，不需要猜测旧临时文件状态。 |
| 部分完成 | 两份报告及摘要全部校验通过前不写新 manifest。 |
| 数据缺失或冲突 | release ID、相对文件名或 SHA-256 不一致时明确失败，不回退到猜测路径。 |

### 修复步骤

- [x] 先新增一个公开行为测试，证明发布入口必须通过 manifest 解析完整文件对（RED）。
- [x] 最小实现不可变 release + 原子 manifest，并使公开解析测试与中断恢复测试转绿（GREEN）。
- [x] 补齐重复生成、陈旧 staging、摘要和路径校验；旧格式直出文件由新的 manifest 入口替代，不作为活动结果继续保留。
- [ ] 重新生成版本化证据，运行 P3-01 聚焦测试、受影响回归、快速冒烟、双路限定复审和交接核验。

### 修复候选记录（等待限定复审）

- 修复提交 `36825741f995a94f54c7fba14062bc7073f59a2b` 把直接覆盖两个文件改为“先完整写入不可变 release，再以单一 manifest 原子激活”；解析端校验 release ID、固定文件名和两份 SHA-256，不会猜测未引用或残缺目录。
- 公共测试先确认旧实现没有 manifest 解析入口（1 项 RED），随后覆盖正常发布、中断前保留旧文件对、重复生成、陈旧 staging、路径越界、摘要篡改和被阻塞的发布目录，共 7 项通过。
- 受影响回归合计 51 项通过（53.90 秒，1 条既有 Starlette/httpx 弃用警告）；快速冒烟通过（文档治理、512 个第一方 Python 文件编译、两库临时副本初始化幂等），未运行全量 pytest。
- 新证据 release `67e2fc5671dc39bace5672b7eae02d867c1f8760ffb15648025b728970655f3c` 绑定功能提交，记录 289 个第一方 Python 文件、2,789 条静态导入和 4 条动态导入线索；绝对工作路径与 `user_data/` 命中均为 0。
- 当前剩余工作：同一冻结候选的 Spec/Standards 限定复审、必要时一次统一修复、交接核验和进入 N3-01 integration。

### 独立修复初审结果与统一修正范围

- 同一冻结候选 `65266b4af8f10fe7cc051d1374664d55a5c553d4` 的 Spec/Standards 初审原始 5 条意见，按根因去重为 4 条 `Important`、0 条 `Critical`、0 条 `Suggestion`。
- `Important 1｜当前任务原本遗漏`：旧直出 JSON/Markdown 尚未先转换成可恢复的活动 release，首次 manifest 激活前中断时重启无法通过新解析入口取得旧文件对。
- `Important 2｜当前任务原本遗漏`：解析端只校验 release ID 格式与文件摘要，没有以实际文件内容重算 release ID，无法发现 manifest 指向“内容相同但目录 ID 伪造”的冲突。
- `Important 3｜本次修改直接引入`：只检查外层 output dir；预先存在的 publication/release junction、symlink 或其他 reparse point 仍可能把写入引到受控目录之外。
- `Important 4｜当前任务原本遗漏`：文件内容做了 flush/fsync，但 release 与 manifest 原子改名后没有同步父目录；突然掉电后的目录项持久化承诺不足。
- 本轮只统一修正以上四点，并增加 Git 换行转换后的文本摘要稳定性守卫；不扩展扫描内容、报告字段或生产业务范围。修复后只允许对这四点及直接修改区域进行一次最终限定复审。

### 统一修正结果（等待最终限定复审）

- 统一修正提交 `be621b6b7aaa285bf8777252427c99ba2d78215f`：旧直出文件对会先成为可解析的活动 release，再尝试激活新报告；无 manifest 时解析器仅为完整、普通文件组成的旧文件对提供迁移回退。
- 解析器现在以两份实际文本重算 release ID，同时验证固定文件名与摘要；publication、releases、release 和报告文件任一层存在 symlink、junction 或其他 reparse point 都会拒绝写入/解析。
- release 目录和 manifest 原子改名后分别同步父目录；Windows 使用带目录语义的句柄执行 `FlushFileBuffers`，其他平台使用目录 `fsync`。命令只有在内容和目录项同步成功后才报告成功。
- 新增 4 个直接修复场景及 1 个 Git 换行转换场景；P3-01 与 Schema/性能证据受影响回归共 55 项通过（72.71 秒，1 条既有 Starlette/httpx 弃用警告），快速冒烟再次通过（文档治理、512 个第一方 Python 文件编译、两库临时副本初始化幂等）。
- 更新后的活动 evidence release 为 `e3e751e8dcee0cbfa3a238987a0198f2927b0415a2dfc9aa6c0f5855295a5cd2`，绑定统一修正提交，记录 289 个第一方 Python 文件、2,792 条静态导入和 4 条动态导入线索；旧的非活动 evidence release 从版本化证据中移除。
- 当前剩余工作：由首轮原 Spec/Standards 复审者仅核对四条登记问题及直接修改区域；若仍有当前范围 `Critical`/`Important`，按规则立即停止，不再自动修复。

### 第 1 次独立修复最终复审与第 2 次授权

- 最终限定复审关闭了 release ID 内容复核和嵌套 reparse 写入绕过，但仍登记 2 条 `Important`，因此第 1 次独立修复按规则停止，未宣称通过。
- `Important 1｜本次修改直接引入`：旧格式迁移只确认 JSON 可解析、Markdown 可读取，没有证明 Markdown 正是由同一 JSON 渲染；旧故障留下“新 JSON + 旧 Markdown”时会把不匹配文件对固化为活动 release。
- `Important 2｜当前任务原本遗漏`：两份文件已 fsync，release 改名后也同步了 `releases` 父目录，但改名前没有同步 staging 目录本身；掉电时文件名目录项仍可能未持久化。
- 用户此前授予同类问题最多两次自动授权，本任务现启用第 2 次、也是最后一次授权。新修复只允许：用公开渲染结果证明旧 JSON/Markdown 匹配；在 release 改名前同步 staging 目录。不得修改其他行为或扩大范围。
- 第 2 次修复仍执行一次 Spec/Standards 初审、必要时一次统一修正和一次最终限定复审；如果最终仍有当前范围 `Critical`/`Important`，立即停止 P3-01 和后续依赖包，不再自动建立第三个修复任务。

### 第 2 次独立修复候选（等待复审）

- 修复提交 `1eec63b88b7c7b755c1b1ef55a47230f39d4f3f9` 只处理两条登记问题：旧格式回退会重新从 JSON 渲染 Markdown 并要求完全一致；两份文件 fsync 后进一步同步 staging 目录，再执行 release 原子改名。
- 两个公开/文件系统边界测试分别先得到 RED，再转为 GREEN；同时把结构报告表格顺序固定为 `CORE_TARGETS` 权威顺序，使从排序后的 JSON 重建 Markdown 仍与原报告一致，不改变报告字段或扫描内容。
- P3-01、Schema 与性能证据受影响回归共 57 项通过（82.11 秒，1 条既有 Starlette/httpx 弃用警告）；快速冒烟通过（文档治理、512 个第一方 Python 文件编译、两库临时副本初始化幂等）。
- 更新后的活动 evidence release 为 `66bcc9e80ea3fb9bb2ed61bc7b9579c40ece39db86ecda99c482286b0ce37a77`，绑定修复提交，记录 289 个第一方 Python 文件、2,792 条静态导入和 4 条动态导入线索。
- 当前进入第 2 次独立修复的一轮 Spec/Standards 初审；若需要统一修正仍只限本节两条问题及直接回归，随后最多一次最终限定复审。
