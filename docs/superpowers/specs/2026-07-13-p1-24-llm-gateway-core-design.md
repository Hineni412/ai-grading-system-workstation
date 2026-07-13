# P1-24 LLM Gateway 核心设计

## 1. 目标与边界

P1-24 建立唯一的模型请求策略层，集中管理显式超时、有界重试、节流、请求 ID、协议兼容和用量事件。它只提供 Gateway 核心与旧 `LLMClient` 的兼容接入，不迁移四处直连 OpenAI 的调用链；直连迁移与 `timeout=None` 清零属于 P1-25。

本包不更换模型供应商或模型名，不修改 prompt、评分规则、题号契约、JSON 业务结构、现有并发参数或活动知识点语义，不迁移历史日志，也不执行真实模型请求。所有验证使用假客户端、假时钟和临时日志。

## 2. 方案选择

采用独立 `backend/llm/` 策略层，并让根目录 `llm_client.py` 继续作为一个版本的兼容门面。

没有选择把所有职责继续叠入 `llm_client.py`，因为该文件已经同时承担消息构建、图片压缩、JSON 解析与参数兼容。也不采用仅抽取零散工具函数的方案，因为那无法形成供 Chat Completions 和 Responses 共用的唯一请求边界。

## 3. 组件与职责

### 3.1 策略模型

`backend/llm/policy.py` 定义：

- 请求类型枚举：`grading`、`recognition`、`config_generation`、`tagging`。
- 协议枚举：`chat_completions`、`responses`。
- 不可变请求策略：超时秒数、最大普通重试次数、每分钟请求数和退避序列。
- 默认策略：批改 300 秒、识别 60 秒、配置生成 120 秒、打标 120 秒。

配置档允许用独立字段覆盖各请求类型的超时、重试次数和 RPM。覆盖值必须是有限数值并处于明确安全范围；缺失值使用默认策略，非法值在策略解析边界被拒绝，不会产生 `None`、无限重试或零间隔忙循环。API profile 的文件格式和原子存储协议保持不变。

### 3.2 错误分类

`backend/llm/errors.py` 把模型异常归为：

- `timeout`
- `connection`
- `rate_limit`
- `server_transient`
- `parameter_incompatible`
- `authentication`
- `invalid_request`
- `unknown`

只有超时、连接失败、限流和服务端临时错误可以消耗普通重试预算。认证、普通 4xx、JSON 业务解析失败和未知错误不自动重试。参数不兼容进入独立兼容降级路径，不占普通重试预算，且每种参数组合只尝试有限次数。

### 3.3 节流注册表

`backend/llm/pacing.py` 复用现有 `RequestPacer` 的均匀起始槽语义，以配置身份和请求类型作为稳定键维护线程安全的 pacer。相同策略共享节流，不同模型用途互不误用速率。Gateway 不改变业务线程池、批次大小或上层并发上限。

### 3.4 用量事件

`backend/llm/usage.py` 定义结构化事件和记录接口。每个物理请求记录：

- 逻辑请求 ID、尝试序号、请求类型和协议；
- 模型、耗时、成功状态、错误分类和是否发生兼容降级；
- prompt/input、completion/output、cached、reasoning 和 total token；
- 可选的上层关联 ID，但不记录 prompt、响应正文、密钥、完整 URL、学生姓名或答卷内容。

现有 `usage_logger.py` 保留兼容函数，并委托新记录器写入现有 JSONL 目的地。日志写入失败不得使模型业务请求失败。

### 3.5 Gateway

`backend/llm/gateway.py` 提供同步 Gateway。调用方提交请求类型、协议、模型和一个只负责发出 SDK 请求的操作函数；Gateway 负责：

1. 生成或接收逻辑请求 ID。
2. 取得已校验策略并等待节流槽。
3. 把显式 timeout 传给 SDK 操作。
4. 执行请求并记录用量事件。
5. 按集中错误分类执行有限退避重试。
6. 在预算耗尽时抛出原异常或带稳定分类的 Gateway 异常。

Gateway 不理解 prompt、评分结果或标签质量，不在策略层修复 JSON。

## 4. 兼容层与协议处理

`llm_client.py` 的公开构造方式和现有 `text_from_images`、`json_from_images`、`json_from_text`、单次请求方法保持可用。内部 Chat Completions 请求经 Gateway 执行，但原有参数兼容顺序、JSON 截断重试和 JSON 修复语义保持不变。

Chat Completions 与 Responses 使用同一策略、错误分类、请求 ID 和用量记录，但各自保留协议适配器。Responses 支持对象和字典两种 usage 形状；Chat Completions 继续支持现有 `max_tokens` → `max_completion_tokens` → 去除 `response_format` 的有限兼容降级。

“单次请求”方法仍只发出一次模型调用：它不运行普通重试、参数兼容降级或 AI JSON 修复。这个契约优先于 Gateway 的默认重试策略。

## 5. 数据流

调用方先从当前 API profile 构造 `LLMSettings` 和策略覆盖，再通过兼容 `LLMClient` 或直接 Gateway 发起请求。Gateway 为逻辑请求分配 ID、选择策略并节流，协议适配器将显式 timeout 注入 SDK。每次返回或异常都生成脱敏用量事件；可重试错误按退避表再次进入节流，最终响应回到 `LLMClient` 执行既有文本提取与 JSON 处理。

参数兼容降级属于同一逻辑请求，沿用请求 ID、递增尝试序号并记录降级原因。JSON 修复属于新的物理尝试，但仍关联原逻辑请求 ID，方便审计一次业务调用实际消耗了多少模型请求。

## 6. 失败与安全行为

- 超时、连接、限流和 5xx 只按有限预算重试；不允许无限循环。
- `Retry-After` 若可安全解析，则在本包定义的最大退避上限内采用；否则使用确定性退避表。
- 参数错误只有命中明确参数标记或受支持状态码时才进入兼容降级，网络异常不会被误判为参数问题。
- 用量记录器只接收脱敏元数据；日志异常被吞并并产生本地警告，不覆盖模型结果。
- 配置覆盖非法时在请求发出前失败；不会静默采用危险的无超时值。
- 测试和冒烟不读取真实 API profile，不调用真实模型，也不写 `user_data/`。

## 7. 测试设计

新增 Gateway 契约测试，使用假客户端和假时钟覆盖：

- 四类默认超时和合法/非法配置覆盖；
- Chat Completions 与 Responses 都收到显式 timeout；
- 超时、连接、限流和 5xx 的有限重试及预算耗尽；
- 认证、普通 4xx 和未知错误不重试；
- 参数兼容降级不消耗普通重试预算且次数有界；
- 多线程调用按共享 pacer 预留不同请求槽；
- 请求 ID 在重试、兼容降级和 JSON 修复过程中保持关联；
- 两种协议的 token 用量归一化；
- 日志内容不含密钥、prompt、响应正文或内部绝对路径；
- `LLMClient` 既有 JSON 修复、截断处理和 single-request 契约不变。

验证层级为新增聚焦测试、现有 LLM/config/tagging/批改相关回归、`git diff --check` 和 `tools/smoke_check.py --skip-tests`。由于这是公共模型请求基础设施的实质代码变化，integration 波次末按仓库风险规则运行完整 `tools/smoke_check.py`。

## 8. 回退与后续包

本包是纯代码增量，没有 Schema 或真实数据迁移。回退时可撤销 P1-24 的独立提交，旧 `llm_client.py` 入口仍在。P1-25 将在本包合并后逐条迁移 choice、fill-blank、objective batch 和 AI tagging 直连；每条调用链必须保持模型参数、prompt、fallback 和评分语义不变。
