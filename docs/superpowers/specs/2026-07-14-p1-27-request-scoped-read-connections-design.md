# P1-27 请求级只读连接复用设计

**执行包：** P1-27  
**设计日期：** 2026-07-14  
**基线：** `0564d60697d1e6e643491679099ed3e796be32ca`  
**用户自测：** none  
**实施资格：** daytime_only

## 1. 结论

采用“请求内双库候选 + 每库单一只读连接 + 显式借用”的窄范围方案：

- 只优化 P1-26 已证明昂贵的 Training 诊断/计划预览和 Graph 三个只读端点。
- 每个请求分别捕获阅卷库与题库的稳定临时候选，各打开一个 `mode=ro`、`query_only` 的连接，并在请求结束时关闭。
- `DiagnosisProfileService`、`QuestionTagProjectionService`、`PracticePlanService` 及其只读协作者显式接收借用连接；借用连接不被嵌套 `with`、`commit()`、`rollback()` 或 `close()` 提前结束。
- Streamlit 和所有未改造调用方继续使用原构造方式、原初始化、原连接和原事务行为。
- Training 创建任务属于写请求，继续使用既有服务和写事务；本包不把写连接塞进只读请求上下文。
- P1-15 Question Bank GET 保留每请求零源写入快照，不增加跨请求缓存或连接池。

这是用户预先同意的推荐设计。

## 2. 依据

P1-26 的正式生成数据基线包含三档数据、16 个场景、每场景 20 个样本和两轮重复。与本包直接相关的事实是：

- `medium/training.plan.preview` 每请求约 140 秒，SQLite 语句中位数为 1,222,551，SELECT 中位数为 7,854。
- `medium/training.diagnosis` 每请求约 3.3 秒，SQLite 语句中位数为 5,151，SELECT 中位数为 54。
- 三个 `medium/graph.*` 端点约 1.4–1.75 秒，SQLite 语句中位数均为 5,163，SELECT 中位数均为 56。
- `medium/question_bank.questions.default` 约 34–35 ms，SQLite 语句中位数为 11，SELECT 中位数为 6。

Training/Graph 的总语句数远高于实际 SELECT 数，说明主要成本来自服务层反复 `initialize_database()`、打开连接和执行 PRAGMA/DDL 检查，而不是业务查询本身。Question Bank GET 已经在 P1-15 使用稳定候选和只读 URI；其端到端成本处于几十毫秒量级，没有证据支持承担跨请求失效协议的复杂度。

## 3. 备选方案

### 方案 A：请求内显式借用只读连接（采用）

优点：

- 直接消除已测得的重复初始化和重复连接成本。
- 生命周期与 FastAPI 请求一致，异常时也能确定关闭。
- 借用对象只存在于一个请求，不产生跨请求陈旧状态。
- 原构造方式保留，Streamlit 和写路径无需迁移。

代价：

- 需要把连接参数沿 Training/Graph 的只读调用链逐层传递。
- 必须防止旧服务内部的上下文管理或 `close()` 提前结束请求连接。

### 方案 B：让所有 API 的 `DBManager` 统一使用一个请求事务（拒绝）

`DBManager` 同时服务大量读写路径，含显式 `BEGIN IMMEDIATE`、`commit()`、`rollback()` 和 `close()`。一次性改造所有方法会扩大到评分、复核、配置和会话写入，超过 P1-27 的证据范围，并显著增加事务语义回归风险。

### 方案 C：进程级连接池或跨请求快照缓存（拒绝）

这会引入连接所有权、陈旧快照、WAL/sidecar 失效、并发引用计数和进程退出清理问题，也违反“不引入第三方连接池”的包边界。P1-15 普通题库读取仅几十毫秒，当前收益不足。

## 4. 请求生命周期

### 4.1 候选捕获

复用 P1-15 的 `captured_sqlite_snapshot_path()`：

1. 读取源 main/WAL 到系统临时目录。
2. 使用有界 M1-W1-W2-M2 比较确认候选稳定。
3. 验证所需表存在且 `quick_check=ok`。
4. 仅把临时候选路径交给本请求。
5. 请求结束后清理候选目录。

两个数据库仍是依次捕获，不宣称跨数据库原子快照；这与现有 Graph 行为一致。源数据库不通过 SQLite 打开，也不写源 WAL/SHM。

### 4.2 只读连接

从候选打开连接时固定：

- URI `mode=ro`；
- `PRAGMA query_only = ON`；
- `isolation_level=None` 后显式 `BEGIN`，维持整个请求的稳定读视图；
- `row_factory=sqlite3.Row`；
- `check_same_thread=False`，允许 FastAPI 同步 dependency 的进入、路由执行和退出跨 AnyIO worker 线程，但连接不得跨请求共享；
- 请求 teardown 无条件 `close()`。

不改变遗留连接的 WAL 或 busy timeout 配置。候选连接沿用现有只读验证值，不对源数据库执行 PRAGMA。

### 4.3 借用语义

`DBManager` 增加可选 `external_connection`，默认值为 `None`。传入连接时，`_connect()` 返回非拥有型代理：

- `execute()`、`cursor()`、row factory 等转发给底层连接；
- `with` 进入/退出不提交、不回滚、不关闭；
- `close()` 不关闭底层连接；
- SQLite 自身的 `mode=ro` 与 `query_only` 拒绝任何写语句；
- 请求 dependency 是唯一所有者，负责最终关闭。

题库 `connect()` 增加同样的可选外部连接参数。外部连接分支只 `yield`，不执行初始化、提交、回滚或关闭；未传入连接时保持现有实现逐字节语义。

## 5. 服务适配边界

### 5.1 Training/Graph 共享只读上下文

新增请求上下文对象，持有：

- 阅卷库候选路径和连接；
- 题库候选路径和连接；
- 基于借用阅卷连接的 `DBManager`；
- 基于两连接构造的 `DiagnosisProfileService`；
- 基于题库连接构造的 `PracticePlanService`。

FastAPI 通过一个 yield dependency 创建上下文。拆分出的 diagnosis/practice dependencies 都依赖同一个上下文，利用 FastAPI 单请求 dependency cache 保证只构造一次。

适用路由：

- `POST /api/training/diagnosis`
- `POST /api/training/plans/preview`
- `POST /api/graph/profiles`
- `POST /api/graph/rows`
- `POST /api/graph/evidence`

`POST /api/training/tasks` 和其他写路由继续使用旧 dependencies，避免把只读候选与写事务混合。

### 5.2 只读调用链

新增可选外部连接参数并只在只读方法中传播：

- `DiagnosisProfileService`
  - 阅卷读走带外部连接的 `DBManager`。
  - 题库读把同一题库连接传给 `QuestionTagProjectionService`。
- `QuestionTagProjectionService`
  - 外部连接存在时跳过 `initialize_database()`。
  - 把连接传给 `SourceQuestionLinkService.list_links()`。
  - questions/tags 查询复用同一连接。
- `PracticePlanService`
  - question-tag 路径跳过初始化。
  - 候选、标签和关系查询复用同一连接。
  - 把连接传给 `QuestionFrequencyService.metrics_for_questions()` 和 `SourceQuestionLinkService.confirmed_bank_question_ids()`。
- `QuestionFrequencyService` 与 `SourceQuestionLinkService`
  - 只读入口支持借用连接并跳过初始化。
  - 写入口保持原自有连接与事务；若误对只读借用连接执行写入，SQLite 必须拒绝。

legacy/skill 推荐分支不是当前 API 主路径，不为目标架构提前改造 `SkillCatalogService`、`SkillLinkService` 或 `ConceptAlignmentService`。

## 6. 错误与关闭

- 候选捕获失败继续映射为现有脱敏 503，不返回路径。
- 路由业务抛错时，FastAPI dependency teardown 仍关闭两个连接并清理两个候选目录。
- 第二个候选或连接创建失败时，已创建的第一个连接/候选也必须关闭/清理。
- 关闭失败不得覆盖原业务异常；无原异常时转换为稳定的内部错误并不暴露路径。
- 不记录 SQL、请求体、学生正文、真实路径或数据库指纹。

## 7. 并发与事务

- 每个请求创建独立候选和独立连接；两个并发请求的连接对象、事务和临时目录不得相同。
- `check_same_thread=False` 只解决 FastAPI 生命周期跨 worker 的关闭问题，不授权跨请求共享。
- 请求内路由是同步串行执行，不并发使用同一连接。
- 源库写入可与候选读取并发；当前请求继续读取捕获时的一致候选，后续请求重新捕获新状态。
- 只读连接执行 `INSERT/UPDATE/DELETE/DDL` 必须得到 SQLite readonly 错误，候选和源库均无变化。
- 写路由继续使用原 DBManager/题库连接、原 `BEGIN IMMEDIATE` 和原提交/回滚规则。

## 8. 性能验证

新增 P1-27 对比报告，只使用生成数据并复用 P1-26 数据集：

- 对比场景：Training diagnosis、Training plan preview、Graph profiles/rows/evidence。
- 控制场景：Question Bank default list，用于记录 P1-15 每请求快照端到端成本。
- 三档规模、固定 seed、两轮重复；业务返回记录数和状态必须与 P1-26 基线一致。
- 报告同时列 before/after p50、总语句数、SELECT 数、响应记录数和改善比例。

合并门槛：

- 五个目标端点的总 SQLite 语句中位数必须下降。
- `medium/training.plan.preview` 和三个 `medium/graph.*` 的 p50 必须明显下降；机器噪声下以至少 20% 为门槛。
- 响应状态、记录数和公开契约保持一致。
- Question Bank 控制场景只记录，不要求优化；若仍处于几十毫秒量级，不增加跨请求缓存。
- 任一业务不一致、只读失效或性能无改善都阻塞合并。

## 9. 测试

### 单元/服务测试

- `DBManager` 外部连接复用且非拥有型代理不提前提交/回滚/关闭。
- 题库 `connect()` 外部连接分支不初始化、不提交、不关闭。
- Diagnosis/Projection/Practice/Frequency/SourceLink 只读调用链只看到同一连接身份。
- 旧构造方式继续独立开关连接，现有 Streamlit 服务测试不变。
- 借用只读连接执行写入被拒绝。

### API 生命周期测试

- 每个目标请求每库只打开一个工作连接。
- 正常、业务异常和候选失败都关闭连接并清理临时目录。
- 两个并发请求连接身份不同，返回结果不互相污染。
- 源库并发写入不会改变已捕获请求结果；下一请求可见新状态。
- Training 创建任务仍走旧写事务，不注入只读连接。

### 回归

- Training、Graph、Question Bank、DBManager、快照和 OpenAPI 受影响测试。
- 快速冒烟；integration 波次运行完整冒烟。
- 所有写测试只使用临时数据库或隔离副本。

## 10. 文档与回退

完成后在 `ARCHITECTURE.md` 记录请求级只读连接事实和明确范围。版本化性能报告放在 `docs/performance/`，只含生成数据聚合指标。

回退顺序：

1. 路由恢复旧 dependency。
2. 删除请求只读上下文。
3. 删除服务外部连接参数与非拥有型代理。
4. 保留 P1-26 基线与 P1-27 对比报告作为历史证据。

没有 Schema、迁移或真实数据回退。
