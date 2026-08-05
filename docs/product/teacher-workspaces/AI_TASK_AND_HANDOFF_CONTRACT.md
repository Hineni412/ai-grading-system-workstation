# 教师工作台 AI 任务与页面交接契约

> 契约版本：`teacher_workspace_ai_task.v1`
>
> 状态：`design_frozen`
>
> 适用模块：备课工作台、班主任工作台；后续工作台只有通过同一合同测试后才能接入。
>
> 物理存储与跨库采用决定见 `docs/adr/0008-use-metadata-only-workspace-ai-tasks.md`。

## 1. 为什么需要独立契约

现有阅卷 Job 已经提供排队、进度、查询、取消和重启清理，是可靠的执行底座；但它的产品语义仍是通用后台任务：

- 状态只有 `queued/running/paused/succeeded/failed/cancelled`；
- 不能区分“请求尚未发出”和“模型可能已收到但结果未知”；
- 通用 Job payload/result 会进入公共 API 投影，不适合承载学生对话、完整 prompt、课件内容或模型原文；
- 前端持久键仍使用 `ai-grading:tracked-jobs:v1`，没有工作台来源、返回页面和教师下一动作。

因此新增一个深 Module：**教师工作台 AI 任务**。它在内部复用 Job 执行器，但对 A/B 只暴露小而稳定的 Interface；Job 是实现细节，不是 A/B 的业务接口。

## 2. 统一词汇

| 词语 | 含义 |
| --- | --- |
| AI Task | 一次可追踪的工作台 AI 操作，包含业务快照引用、目的地指纹、发送尝试、恢复状态和安全投影 |
| operation_id | 客户端为一次明确教师动作生成的随机操作号；重复提交同一动作必须沿用，同一操作内容变化必须冲突 |
| request_fingerprint | 对冻结输入、prompt 版本、来源 revision 和模型目的地计算的摘要，不包含可逆正文 |
| send_attempt_count | 在进入“请求可能已经发出”边界前原子保留的发送尝试数；一项工作台操作固定最多为 1。它是保守防重凭据，不等于可核实的供应商计费次数 |
| dispatch_evidence | 本机对发送边界的证据：`not_started`、`may_have_started` 或 `response_persisted` |
| result_unknown | 请求可能已经发出，但本机没有可以证明成功或失败的持久结果；只能查询或放弃，不能自动重发 |
| proposal | 模型结果通过本业务 Adapter 校验后保存的非正式草稿；不是正式学生记录、日历、教案或课件 |
| adopt | 教师确认后，把 proposal 交给所属业务模块保存；共同模块只协调，不直接写 A/B 正式表 |
| Handoff | 从 AI 结果返回受控业务页面的交接信封；包含业务意图和草稿引用，不包含任意 URL |
| Adoption Receipt | 所属业务库与正式对象同一事务保存的采用收据；用于跨库崩溃后证明某个 Handoff 已经写入且不得重复创建 |
| Runtime Projection | 把 AI Task 投影为现有 Job 的排队、运行、进度和取消信息 |

## 3. Module、Interface、Adapter 与 Seam

### 3.1 Module

建议共同实现目录：

```text
backend/workspaces/ai_tasks/
  models.py
  ports.py
  service.py
  store.py
  job_adapter.py
  public_projection.py
  recovery.py
  registry.py
```

前端共同实现目录：

```text
frontend/src/workspaces/shared/ai-tasks/
  api.ts
  contracts.ts
  store.ts
  taskPresentation.ts
  WorkspaceAITaskDrawer.vue
```

这是公共热点，只能在短期共同地基分支实现。A/B 分支只能添加自己的 Adapter 和页面，不得复制公共状态机。

### 3.2 小 Interface

共同 Module 只暴露五个业务动作：

```text
prepare(operation_id, request) -> PreparedTask
dispatch(operation_id, prepared_task_id, request_fingerprint) -> TaskSnapshot
get(task_id | operation_id) -> TaskSnapshot
cancel(operation_id) -> TaskSnapshot
adopt(handoff_id, draft_revision, target_revision) -> AdoptionReceipt
```

`prepare` 在第一次调用时就绑定 operation_id、请求指纹和 PreparedTask；同 operation、同指纹重放返回同一 PreparedTask，因此 prepare 响应丢失不会产生孤儿快照。它是冻结输入和来源 revision 的技术快照，不是班主任隐私预览，也不增加一次教师确认。班主任教师按“发送”即可以在 B 的深 Module 内完成 `prepare + dispatch`；备课页面可以先让教师检查来源范围，再按“生成建议”触发 dispatch。

`adopt` 以 Handoff 而不是整个 AI Task 为单位。一个 Task 可以产生多个 Handoff，并分别采用、丢弃或失效；页面只能调用所属业务 API，业务 API 内部委托这一共同 adopt 协调器，不能同时维护第二条采用路径。

上述五个动作是后端 Module Interface，不要求前端直接访问一个万能写入 API。前端 dispatch/get/cancel 可使用安全公共投影；正式 adopt 必须经过 A 或 B 的领域 API，以便领域 Adapter 在自己的事务中完成校验、正式写入和 Receipt。

### 3.3 共同 Module 负责

- AI Task 元数据持久化和唯一约束；
- operation claim、请求指纹和目的地指纹校验；
- 发送尝试保留、dispatch evidence 与最多一次发送边界；
- 与现有 JobManager 的排队、进度和取消桥接；
- 重启恢复、`result_unknown`、安全错误和下一动作；
- 任务抽屉安全投影；
- proposal 与一个或多个业务 Handoff 的版本关联和聚合状态；
- 调用 A/B Adapter，但不理解 A/B 正文字段。

### 3.4 A/B Adapter 负责

- 从本业务存储读取输入快照；
- 构造领域 prompt 和允许的上下文；
- 调用统一模型 Gateway 所需的领域参数；
- 校验、归一和持久化模型原始结果与 proposal；
- 产生 allowlist 内的 Handoff；
- 教师 adopt 后以稳定 adoption_id 幂等写本业务正式数据，并在同一领域事务保存 Adoption Receipt；
- 提供按 adoption_id 查询 Receipt 的 Port，供共同 Module 在崩溃后收敛状态；
- 对来源 revision、目标 revision 和业务权限作最终判断。

班主任 Adapter **不得增加匿名化、隐私闸门或逐字发送确认**。教师原文可以按用户决定直接发给已配置模型，但共同任务表、公共 API、普通日志和 localStorage 仍不得复制原文。

## 4. Prepare 请求

建议的逻辑结构如下；具体语言类型由实现确定：

```json
{
  "module": "teaching_prep | class_teacher",
  "task_kind": "string",
  "source_ref": {
    "kind": "lesson | conversation",
    "id": "领域内部不可猜测引用",
    "revision": "冻结版本"
  },
  "context_refs": [
    {"kind": "domain-owned-reference", "id": "opaque-id", "revision": "..."}
  ],
  "prompt_contract_version": "模块 prompt 版本",
  "model_destination_fingerprint": "去凭据目的地摘要",
  "return_target": "allowlist destination key"
}
```

`safe_title`、安全错误说明和下一动作不接受 Adapter 自由文本；共同 Module 根据已注册的 `module + task_kind + error_code` 生成固定投影，未知 task_kind 直接拒绝。共同存储只保留上述安全元数据和摘要。完整输入、消息、prompt、模型原文和领域 proposal 保存在所属模块的数据区，由所属模块决定保留策略。

## 5. 状态模型

### 5.1 产品状态

| 状态 | 发送尝试／证据 | 可否取消 | 教师下一动作 |
| --- | ---: | --- | --- |
| `prepared` | `0 / not_started` | 可放弃 | 发送或返回修改来源 |
| `queued` | `0 / not_started` | 可真正取消 | 等待或取消 |
| `running` | `0 / not_started` 或 `1 / may_have_started` | 可请求取消 | 等待；页面只按证据显示“尚未发送”或“可能已发送” |
| `needs_input` | `1 / response_persisted` | 不适用 | 回来源页面补充信息，创建新会话轮次 |
| `proposal_ready` | `1 / response_persisted` | 可分别处理 Handoff | 审阅 0/N 个 Handoff；Task 本身不代表正式保存 |
| `failed_before_dispatch` | `0 / not_started` | 不适用 | 检查配置后，显式继续同一 operation 的首次发送 |
| `failed` | `1 / response_persisted` | 不适用 | 查看已知失败；显式新建 operation 才可再试 |
| `result_unknown` | `1 / may_have_started` | 不适用 | 只查询原任务或放弃；不自动重发 |
| `invalid_result` | `1 / response_persisted` | 不适用 | 查看校验说明；显式新建 operation 才可再试 |
| `cancelled_before_dispatch` | `0 / not_started` | 不适用 | 返回来源页 |
| `discarded` | `0 或 1` | 不适用 | 保留审计元数据，不写正式业务数据 |

Handoff 另有 `pending/opened/adoption_started/adopted/discarded/stale` 状态。TaskSnapshot 返回 `handoff_total/adopted_count/discarded_count/stale_count/pending_count`；例如一轮两个 work_item 只保存一个时，Task 仍是 `proposal_ready`，聚合显示 `1/2 已处理`，不会把另一项误报为已采用。

### 5.2 内部阶段

`prepared → queued → claimed → send_attempt_reserved → validating → handoff_ready`

分支可能在任一步进入上一节的失败或取消状态。`send_attempt_reserved` 必须在任何网络发送前持久化，并与 `send_attempt_count = 1`、`dispatch_evidence = may_have_started` 同一事务完成。该时点到实际网络发送之间仍有无法消除的崩溃窗口，所以系统保守显示“可能已发送”，不能声称一定调用或一定计费。

### 5.3 与现有 Job 的投影

| AI Task | Job 投影 | 说明 |
| --- | --- | --- |
| `queued` | `queued` | 仍可真正取消 |
| `claimed/send_attempt_reserved/validating` | `running` | Job 只负责执行与进度 |
| `proposal_ready/needs_input` | `succeeded` | 业务 outcome 与 Handoff 聚合由 AI Task API 返回 |
| `failed_before_dispatch/failed/result_unknown/invalid_result` | `failed` | 不能只看 Job failed 判断能否重试 |
| `cancelled_before_dispatch/discarded` | `cancelled` 或终态投影 | 真实语义以 AI Task 为准 |

A/B 页面不得直接从通用 Job `status` 推导“可以再次调用模型”。

## 6. 幂等、并发和发送尝试

1. `prepare` 的 `operation_id + request_fingerprint + model_destination_fingerprint` 完全相同：返回原 PreparedTask；任一指纹不同返回 `operation_conflict`；
2. operation_id 在全部工作台模块中唯一，不能拿同一 operation 创建另一种 task_kind 或另一个 source_ref；
3. 多窗口同时 dispatch：数据库唯一约束和原子 claim 只允许一个获胜；
4. 第一次 dispatch 从 `prepared` 创建一个 Job；对 `queued/running/needs_input/proposal_ready/failed/result_unknown/invalid_result/cancelled_before_dispatch/discarded` 的重复 dispatch 只返回原快照；
5. **唯一重排队例外：** 状态为 `failed_before_dispatch`、`send_attempt_count = 0`、`dispatch_evidence = not_started` 且指纹未变化时，教师显式再次 dispatch 可为同一 operation 创建一个新的活动 Job；原子唯一约束仍保证只有一个活动 Job；
6. `send_attempt_count` 在可能发送前记为 1；任何 Adapter 都不能把它重置；
7. 工作台 AI 每个 operation 的 `max_send_attempts = 1`；Gateway 的业务请求自动重试关闭；
8. 前端轮询因网络错误退避重试只是在查询状态，不属于模型重试，也不能产生新的模型请求；
9. 教师明确选择“重新生成”时必须创建新 operation，并在页面显示这是一次新的发送尝试。

## 7. 取消与竞态

### 7.1 发送前取消

- `prepared/queued/claimed` 且发送尝试数为 0 时可以进入 `cancelled_before_dispatch`；
- Job 不得继续进入 dispatch；
- A/B 不产生 proposal，也不写正式业务数据。

### 7.2 发送后取消

- `send_attempt_reserved` 后只能记录 `cancel_requested` 并停止尚未开始的本地后处理；
- 系统不能向教师声称外部模型请求已经取消；
- 如果结果随后返回，先安全持久化，再标记“结果已返回但未采纳”；
- 教师可以审阅或丢弃，不自动 adopt。

### 7.3 终态竞态

成功结果、取消请求和页面旧轮询响应竞争时，持久化 revision 较新的终态获胜；前端沿用现有“旧响应不能覆盖新状态”的比较规则。

## 8. 重启与恢复

| 重启时持久阶段 | 恢复结果 | 是否允许模型重发 |
| --- | --- | --- |
| `prepared/queued/claimed` 且发送尝试数 0 | `failed_before_dispatch`；教师可按第 6 节唯一例外显式继续同一 operation | 可以，但必须仍是本 operation 的第一次发送尝试 |
| `send_attempt_reserved` 且无持久结果 | `result_unknown`，页面显示“可能已发送” | 不允许自动或同 operation 重发 |
| 模型原始结果已持久化、校验未完成 | 恢复本地校验 | 不重发 |
| proposal 已持久化、Handoff 保存失败 | 恢复 Handoff | 不重发 |
| Handoff 已就绪、业务目标 revision 变化 | 保持 proposal，提示目标冲突 | 不重发；教师选择新目标后重新 adopt |

响应丢失与进程重启使用同一规则。`result_unknown` 的“查询原任务”只能查询本地 task/receipt 状态；除非供应商明确提供同请求查询接口，不得伪装成能从模型服务找回响应。

## 9. 存储与安全投影

### 9.1 物理落点与迁移所有权

- `workspace_ai_tasks`、`workspace_ai_handoffs` 和共同 adoption 投影表放在现有 JobStore 使用的运行数据库 `grading_system.db`，通过公共 `grading` 迁移族管理；不创建新的正文数据库；
- 这里沿用 JobStore 的运行元数据备份与恢复归属，但只保存安全元数据；
- A 的完整输入、结果、proposal 和 Adoption Receipt 留在 `teaching_prep.db`；B 的相应内容留在 B 自有业务数据库；
- 共同数据库与 A/B 数据库之间不假设跨库事务；每个 Handoff 生成稳定 adoption_id；
- A/B Adapter 必须在所属业务库的同一事务中完成“正式对象写入 + Adoption Receipt 写入”；响应丢失或共同库更新失败后，用 adoption_id 查询领域 Receipt 并收敛共同 Handoff，不重复创建正式对象；
- 一个 Handoff 只能采用到一个领域事务。如果一个建议需要同时产生学生记录和日历，应拆成两个 Handoff，不能用一个跨库伪事务包装。

### 9.2 共同任务元数据允许保存

- task_id、operation_id、module、task_kind；
- 安全标题、来源不透明引用、来源 revision；
- prompt 合同版本、请求摘要、模型目的地摘要；
- 状态、阶段、进度、发送尝试数、dispatch evidence、取消标记；
- proposal 引用和 Handoff 引用；
- 时间戳、安全错误码和安全下一动作。

### 9.3 共同任务元数据禁止保存或返回

- 学生姓名、班级可识别组合、教师原话、对话全文；
- 教材/教辅正文、完整 prompt、模型完整输出；
- 密钥、带凭据 URL、绝对路径、真实文件名；
- 可反推出正文的长摘要；
- A/B 正式业务字段的复制品。

### 9.4 Gateway 与 AI 调用诊断

- 工作台任务调用统一 Gateway 时必须使用 `metadata_only` 诊断模式；
- 共享 AI 调用记录只保留 task/operation、固定 task_kind、目的地摘要、时长、dispatch evidence、状态和安全错误码；
- 共享记录、普通日志和公共 API 不保存 prompt、请求正文、响应正文、学生姓名或资料内容；
- 如果 A/B 为草稿恢复确需保存模型原文，由各自 Adapter 写入领域存储并执行领域保留策略，不能借用共享诊断表；
- `metadata_only` 是共同 Gateway 的强制策略，不由 Adapter 通过自由布尔值关闭。

### 9.5 班主任无隐私闸门的准确含义

- 不解锁、不输入 PIN、不做匿名化、不展示逐字匿名发送预览；
- 教师按“发送”后即可按已配置模型目的地调用；
- 原始对话和模型结果由 B 自身存储保存；
- 公共任务列表只显示例如“班主任 · 整理一项学生事务”，不能显示学生姓名或原文；
- 这条存储边界用于避免技术层误泄露，不改变教师的日常操作流程。

## 10. Handoff 契约

### 10.1 信封

```json
{
  "contract_version": "teacher_workspace_handoff.v1",
  "handoff_id": "opaque-id",
  "work_item_id": "opaque-id",
  "module": "class_teacher",
  "intent": "create | append | follow_up | plan | review",
  "handling_mode": "record | plan_calendar | sop",
  "destination_key": "class_teacher.student.record",
  "subject_refs": [
    {"kind": "student", "id": "opaque-id", "revision": "..."}
  ],
  "draft_ref": {"id": "opaque-id", "revision": "..."},
  "adoption_state": "pending",
  "prefill_keys": ["summary", "observed_at"],
  "missing_fields": [],
  "source_task_id": "opaque-id",
  "source_turn_id": "opaque-id",
  "return_context": {"destination_key": "class_teacher.home", "focus_ref": "..."},
  "expires_on_source_change": true
}
```

Handoff API 只返回字段名和不透明引用；页面再向本业务 API 读取草稿正文。共同任务 API 不返回 `prefill` 正文。

### 10.2 允许的目标

首版 allowlist：

- `teaching_prep.overview`
- `teaching_prep.library`
- `teaching_prep.lesson.materials`
- `teaching_prep.lesson.plan`
- `teaching_prep.lesson.exercises`
- `teaching_prep.lesson.slides`
- `teaching_prep.lesson.package`
- `class_teacher.home`
- `class_teacher.student.record`
- `class_teacher.affair.record`
- `class_teacher.plan.calendar`
- `class_teacher.affair.sop`

`handling_mode` 只表示登记、计划／日历或 SOP 的交互样式，不等于固定页面。应用 Adapter 还要根据业务对象选择 destination_key：学生登记进入 `class_teacher.student.record`，活动复盘、学校交接和一般事务登记进入 `class_teacher.affair.record`。模型只能提出业务意图、处理样式和草稿；应用 Adapter 将它映射为 allowlist 中的 destination_key。未知 key、任意 URL、跨模块目标一律拒绝。

### 10.3 交接行为

- `proposal_ready` 后可以自动打开目标草稿页；
- 每个 work_item 产生独立 Handoff；一个 Task 可以有 0—N 个 Handoff；
- 自动打开不等于自动 adopt；
- 页面刷新后依据 handoff_id 恢复同一草稿和焦点；
- 返回对话或备课首页时保留 source_task_id 和上次位置；
- 来源 revision 变化时交接标为 stale，不能把旧草稿静默写进新版本；
- 教师取消交接只丢弃草稿，不撤回已经发生的模型调用。
- adopt 以 handoff_id、draft revision 和 target revision 定位；共同 Module 为该 Handoff 复用稳定 adoption_id，并用领域 Receipt 防止响应丢失后重复写入。

## 11. 统一任务抽屉

任务抽屉每项只显示：

- 安全标题与来源：阅卷、备课或班主任；
- 业务状态、进度、更新时间和发送尝试证据；
- “尚未发送／可能已发送／响应已保存”，不得把保守发送标记写成确定计费次数；
- Handoff 处理汇总，例如“1/2 已处理”；
- 一个明确的下一动作：返回原页、补充信息、审阅草稿、查询原任务、重试本地保存或放弃；
- 在允许时显示“取消”，发送后改成“停止后续处理”。

不显示 prompt、模型原文、学生姓名、真实文件名或绝对路径。

新的 AI Task store 使用中性键，例如 `teacher-platform:tracked-ai-tasks:v1`。旧 `ai-grading:tracked-jobs:v1` 是所有普通 Job 共用的索引，**不得整体删除或改作 AI Task 索引**。如兼容迁移发现旧引用对应 `workspace_ai.*` Job 且服务器能返回 task_id，只迁移该一项；其余阅卷、资料解析、WPS、题库、训练和导出引用原样保留并重新写回旧键。没有可证明映射的旧工作台 Job 继续作为普通 Job 跟踪到终态。

## 12. 各业务 Adapter 的首版输出

### 12.1 备课

- 学期目录建议；
- 课时资料范围建议；
- 课堂方案草稿；
- 候选练习；
- 课件修改 proposal。

所有输出必须引用课时、来源 snapshot 和 revision；教师审批前不写 WPS 副本，来源变化后旧 proposal 失效。

### 12.2 班主任

- 对话补问；
- 六域分类与三样式选择；
- 登记型草稿；
- 计划／日历型草稿；
- SOP 型草稿与安全优先步骤。

模型可完整提出建议，但不能代表系统完成诊断、欺凌认定、惩戒决定、对外发送或结案。高影响内容必须以“AI 建议，待教师判断”呈现。

## 13. 实施迁移策略

1. 在现有 JobStore 运行数据库中新增 AI Task/Handoff 安全元数据表和公共 API，不改变现有通用 Job 状态枚举；
2. 用 Job Adapter 把 AI Task 排队和进度投影到现有 JobManager；
3. 先把备课现有“快照—claim—发送开始—结果未知—proposal”逻辑提炼为合同测试；
4. A 接入共同模块，保留既有业务表和 proposal；
5. B 将同步模型直调改为持久任务，并把内存 preview 改为 B 自有草稿存储；
6. 上线统一抽屉和独立 AI Task 引用键；对旧 Job 混合索引只做逐项兼容，绝不整体迁移或删除；
7. 共同合同稳定后，删除 A/B 各自重复的 AI 轮询、自动重试和旧匿名发送确认代码。

WPS 执行、资料解析和非 AI 文件任务继续使用各自 Job 类型；不要为了统一外观把所有后台任务都改成 AI Task。

## 14. TW-F1 实施包

这些包在共同地基分支连续实现。它们不是七次独立验收；整个共同候选稳定后只集中测试和复审一次。

### F1-00：合同骨架与 Fake Adapter

- 建立 `backend/workspaces/ai_tasks` 的模型、Port、错误码和状态转换；
- 先实现内存 Store、Fake Model Gateway、A Fake Adapter 和 B Fake Adapter；
- 把本文件的 prepare 重放、dispatch 唯一重排队、幂等、取消、重启、Handoff 和 Adoption Receipt 规则写成合同测试；
- 不接现有 Job，不改真实数据库。

完成证据：两种 Fake Adapter 通过同一合同，失败用例能实际触发而不是只断言常量。

### F1-01：元数据持久化与唯一约束

- 通过公共 `grading` 迁移在现有 JobStore 数据库增加 AI Task、Handoff 元数据和 operation/adoption claim；
- 只保存第 9.1 节允许字段；
- 增加 operation/request/destination 唯一约束和 revision；
- 实现 `send_attempt_reserved + send_attempt_count=1 + may_have_started` 原子写入；
- 实现重启扫描和安全恢复。

完成证据：并发数据库测试、发送边界前后进程重启测试、领域 Receipt 收敛测试、共同数据库正文检索为零。

### F1-02：现有 Job 执行 Adapter

- 注册中性 `workspace_ai.*` Job handler；
- 将 queued/running/progress/cancel 投影到 AI Task；
- Job payload 只带 task_id，不带领域正文或 prompt；
- Gateway 为工作台任务强制 `metadata_only` 诊断并关闭业务自动重试；
- 发送后取消、响应丢失和本地 Handoff 恢复按合同落状态。

完成证据：通用 Job 与 AI Task 状态映射测试、每 operation 最多一次发送尝试测试、共享调用记录正文检索为零。

### F1-03：安全 API 与 Handoff allowlist

- 新增独立 AI Task API，而不是让 A/B 解析 JobRecord；
- 在通用 Job API 中为 `workspace_ai.*` 加最小 payload/result/detail/error allowlist，默认不得回落为原始 mapping；
- 实现带 operation 的幂等 prepare、dispatch/get/cancel，以及按 handoff_id 的 adopt；
- 实现 destination_key registry、未知目标拒绝和领域草稿延迟读取；
- 固定安全标题、错误说明和下一动作全部由 registry 生成，不接受 Adapter 自由文本；
- 错误只返回安全错误码、教师说明和下一动作。

完成证据：API schema、路径/正文泄露、任意 URL、旧 revision 和错误投影测试。

### F1-04：前端 Store 与统一任务抽屉

- 建立中性的 AI Task store，复用现有防旧响应覆盖、轮询退避和取消去重；
- 新建 AI Task 引用键；只迁移能由服务器证明属于 `workspace_ai.*` 且带 task_id 的旧引用，原样保留所有普通 Job 引用；
- 实现固定安全标题、来源、状态、发送尝试证据、Handoff 处理数、下一动作和返回来源页；
- 区分“取消模型前任务”和“发送后停止本地处理”；
- 不在 localStorage 保存正文、Handoff prefill 或真实文件名。

完成证据：普通 Job 与工作台 AI 混合索引迁移、刷新恢复、旧响应竞态、not_found、网络退避和可访问性测试。

### F1-05：工作台子导航公共契约

- 扩展 workspace manifest，使模块可以声明稳定 destination_key、标签、匹配规则和顺序；
- `AppTopbar` 读取 manifest 投影，不再硬编码备课旧四工作区事件；
- A 首版公共子导航只声明“备课首页／资料库”，课时阶段留在 A 页面内部；
- B 可用同一契约声明“首页／日历／事务／学生”，不复制顶部栏逻辑；
- 旧 manifest 没有子导航时保持当前行为，避免影响阅卷等模块。

完成证据：manifest 解码、旧模块兼容、destination allowlist、A/B 顶部切换和浏览器刷新测试。该包属于公共热点，只在共同地基分支实现，不留给 A 或集成分支临时补丁。

### F1-06：共同收口

- 接入 A/B Fake Adapter 做跨模块冒烟；
- 确认 WPS、资料解析和普通导出仍走原 Job；
- 更新公共架构、任务索引和开发说明；
- 冻结共同候选，集中运行合同、Job 回归、API、前端 store/抽屉、类型检查、规范检查和构建；
- 对同一冻结版本并行完成一次需求符合性复审和一次代码质量复审。

完成后形成供 A/B 同步的共同检查点；不得在共同分支顺手实现 A/B 业务页面。

## 15. 合同测试与验收

### 15.1 共同合同测试

- prepare 响应丢失后，同 operation 和同指纹返回同一 PreparedTask；
- 同 operation 和同指纹重复/并发 dispatch 只产生一个活动 Job、最多一次发送尝试；
- failed_before_dispatch 只有在尝试数为 0 且证据为 not_started 时可显式重排队；
- 同 operation 异内容或异目的地返回冲突；
- 发送前取消为零次调用；发送后取消不谎报外部请求已取消；
- 发送前与发送后崩溃分别恢复为 `failed_before_dispatch` 和 `result_unknown`；
- HTTP 响应丢失不产生第二次模型调用；
- 模型结果无效进入 `invalid_result`，不写正式业务数据；
- proposal 已保存而 Handoff 失败时，只重做本地交接；
- 一个 Task 的多个 Handoff 可以部分采用、部分丢弃，Task 聚合不会误报全部完成；
- adopt 遇到目标 revision 冲突时不覆盖；领域对象已写入但共同 Receipt 更新前崩溃时，重放 adopt 返回同一对象而不重复创建；
- 刷新和进程重启后，任务抽屉与来源页面状态一致；
- 固定安全标题拒绝 Adapter 注入姓名或正文；
- API、共享 AI 调用记录、普通日志、通用 jobs/AI Task 表和 localStorage 中检索不到测试正文、姓名、prompt 和路径；
- 旧 Job 键含阅卷、WPS、资料解析与 `workspace_ai.*` 混合引用时，只迁移可证明的 AI Task，其他引用完整保留；
- manifest 子导航能从 A 首页切资料库、从 B 首页切日历/事务/学生，旧模块不回归；
- A Fake Adapter 与 B Fake Adapter 通过同一套生命周期合同。
- B 新建任务分别持久化 `class_teacher.intake_triage` 与 `class_teacher.draft_revision`；历史
  `class_teacher.intake` 任务在启动恢复时仍可找到 Adapter，且只做本地恢复、不追加模型请求。
- 教师显式保存新的 B 草稿版本后，共同 Handoff 的 draft revision 与不透明 subject refs 随之更新；两库投影中断时，
  下一次 adopt 先补做安全投影，再使用同一稳定 adoption_id。
- B adopt 在领域 Receipt 不存在的可恢复校验/目标冲突后释放共同和领域的未提交占用；已有 Receipt、未知结果和
  响应丢失仍按收据收敛，不得以“释放”为由重复创建正式对象。
- 模型失败或结果不明后由教师手动选择三种处理样式时，不追加模型请求，也不要求伪造共同 Handoff；B 领域草稿
  仍须经过相同的版本核对、教师采用和领域 Receipt，重复采用只返回同一正式对象。
- 计划／日历草稿采用后，计划页和普通日历使用同一 plan/action id；领域 Receipt 已存在但 WorkGraph 投影缺失时，
  收据恢复必须补齐相同节点和依赖边，不得生成第二套计划身份。

### 15.2 人工冒烟

1. 在备课页面发起建议，离开页面后从抽屉返回同一课时；
2. 在班主任首页发起事务，切换到其他工作台后从抽屉返回同一会话；
3. 发送前取消、发送后请求取消、结果未知、信息不足和 proposal 就绪各走一遍；
4. 确认任务抽屉不展示正文，目标页面能通过领域草稿引用恢复正文；
5. 确认任何 proposal 都必须由教师确认后才进入正式数据。
6. 让模型任务进入结果不明，手动分流后编辑并采用，确认零新增模型请求且正式对象只生成一份；
7. 采用一份含前后依赖的计划草稿，确认计划页与日历使用同一组编号；模拟投影中断后恢复，日历不重复。

## 16. 完成定义

共同地基只有同时满足以下条件才可交给 A/B：

- 小 Interface 与本契约一致，A/B 不直接依赖 JobRecord 内部字段；
- 没有自动模型重试路径；
- `result_unknown`、取消和重启语义可由测试实际触发；
- 共同存储和公共投影不包含领域正文；
- 任务抽屉能准确返回来源页面；
- A/B Fake Adapter 合同通过；
- `ARCHITECTURE.md` 在实现完成时更新为已实现事实；
- 未调用真实模型、未使用真实业务数据。
