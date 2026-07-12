# P2-04 API Client、错误契约与 Job Store 设计

## 1. 目标

在尚未切入生产的 Vue 前端中建立唯一类型化网络入口、统一错误契约和可恢复的 Pinia Job Store。后续页面将通过这些边界访问 FastAPI，而不再直接调用 `fetch`、拼接 API URL 或各自实现轮询。

P2-04 只建立前端网络层和任务状态层；不迁移业务页面，不新增后端路由，不改变 JobManager 状态语义，不存储 API key、完整任务 payload 或结果正文。

## 2. 权威依据与已确认决定

- `docs/superpowers/packages/phase-2-execution-packages.md` 定义 P2-04 的稳定目标、范围、验收和不修改边界。
- `ARCHITECTURE.md` 和已合并的 P1-10/P1-11 实现是当前错误体、request ID、Job 状态和取消语义的事实来源。
- `docs/ui/STYLE.md` 规定错误必须说明影响、重试和安全返回，并要求失败时尽量保留已有内容。
- 用户于 2026-07-12 确认：刷新后只恢复当前浏览器曾发起并记录的 Job，不扫描或认领其他历史任务。
- 用户于 2026-07-12 批准采用原生 `fetch` 封装和受控手写类型，不在本包引入 Axios 或 OpenAPI 生成工具。
- 用户于 2026-07-12 批准本文的请求/错误契约、Job Store 恢复与取消语义、测试和回退方案。

## 3. 方案比较与选择

### 方案 A：原生 `fetch` + 受控手写类型（采用）

在一个小型 Client 中集中 base URL、request ID、JSON 解析、超时/中止、安全重试和错误归一化；每个资源适配器保留自己的运行时校验器和公开类型。

该方案延续 P2-03 已使用的浏览器 API，不增加生产依赖，且能在不改变 Session Store 公共语义的前提下替换 sessions 窄适配器的底层请求。

### 方案 B：Axios + 手写类型（不采用）

拦截器和取消封装更现成，但会增加生产依赖和锁文件变化；当前需求使用原生 `fetch` 已能清晰满足，收益不足以抵消额外依赖和复审面。

### 方案 C：OpenAPI 自动生成全部类型（不采用）

长期契约一致性较好，但会引入生成工具、大量生成文件、CI 漂移守卫和后端 OpenAPI 完整性要求。P2-04 尚未迁移业务页面，当前不承担全契约生成治理。

## 4. 范围

### 4.1 包含

- 统一 API Client：相对 base URL、request ID、请求体序列化、成功体解析、超时/中止和有界重试；
- 统一 `ApiError`：HTTP 状态、后端 code/message/details、request ID、错误类别和可重试提示；
- sessions 适配器迁移到统一 Client，保留现有 Session Store 公共行为；
- Job 公开响应的受控手写类型和运行时校验；
- Pinia Job Store：跟踪、恢复、轮询、退避、取消、停止、移除和清理已完成任务；
- 当前浏览器 Job 最小索引持久化；
- 可注入的通知端口，供后续页面将失败转换为统一反馈；
- mock API 单元测试、Session 回归、Job 恢复/取消竞态和工程守卫。

### 4.2 不包含

- 任何阅卷、考试、学生、分析、题库、训练或设置业务页面迁移；
- Job 列表页、全局通知面板、新的可见 UI 或用户验收流程；
- 后端新路由、Job 列表 API、JobManager/JobStore 状态机修改或数据库 Schema 变更；
- 扫描、恢复或认领当前浏览器未记录的历史 Job；
- Axios、OpenAPI 代码生成工具、第二套状态库或新的主要前端依赖；
- 认证、远程 base URL 配置、API key 保存或任意文件访问；
- 真实 `user_data/`、真实密钥、真实模型调用或生产入口切换。

## 5. API Client 边界

### 5.1 请求流程

1. 资源适配器传入相对 API 路径、HTTP method、可选 body、超时和成功体校验器。
2. Client 只接受以 `/api/` 开头的同源相对路径，拒绝绝对 URL 和非 API 路径。
3. 每次请求生成新的不含业务数据的 request ID，通过 `x-request-id` 发送。
4. Client 合并超时中止和调用方 `AbortSignal`；任意一方中止都立即停止当前尝试。
5. HTTP 成功后先安全解析 JSON，再由资源校验器验证；响应不符合契约时不向 Store/页面暴露未知数据。
6. 响应 `x-request-id` 优先作为最终请求标识；后端错误体中的 request ID 必须与之一致，不一致时按契约错误处理。

### 5.2 安全重试

- 默认只对 `GET` 请求中的临时网络失败和可明确归类的 5xx 使用有上限的指数退避。
- 422、404、409、响应契约错误、显式取消和超时不自动重试。
- `POST`、`PUT`、`PATCH` 和 `DELETE` 默认不自动重放，避免重复提交。后续资源如需重试，必须由业务适配器依据后端幂等契约显式发起新请求。
- 调用方中止、页面卸载或 Job 停止后不再进入下一次退避尝试。

## 6. 统一错误契约

`ApiError` 至少保留：

- `kind`：`validation | not_found | conflict | server | network | timeout | cancelled | contract`；
- `status`：HTTP 状态码或网络层错误时的 `null`；
- `code`：经校验的后端错误代码或稳定的前端错误代码；
- `message`：后端标准错误体的安全消息，或前端为网络/契约错误提供的稳定消息；
- `details`：仅当顶层为普通对象时保留，不序列化到本地存储或通知日志；
- `requestId`：响应头/标准错误体中经验证的标识，网络未达服务器时使用客户端生成值；
- `retryable`：只表示可向用户提供安全重试动作，不等同于 Client 已自动重试。

后端标准错误体必须符合 `{ error: { code, message, details, request_id } }`。非 2xx 响应如果不符合该格式，Client 仍保留 HTTP 状态和 request ID，但按 `contract` 错误处理，不显示原始响应文本。

通知端口只接收脱敏后的用户向消息、影响说明、是否可重试和 request ID。页面/Store 决定何时发出通知；Client 不对同一失败自动弹出多次消息。

## 7. Job Store

### 7.1 公开 Job 契约

Store 只接受后端当前公开字段：`id`、`job_type`、`payload`、`result`、`status`、`progress`、`stage`、`detail`、`error`、`cancel_requested`、`created_at`、`started_at`、`updated_at` 和 `finished_at`。

`status` 严格校验为 `queued | running | paused | succeeded | failed | cancelled`。终态只有 `succeeded | failed | cancelled`。`cancel_requested=true` 是服务器已收到协作式取消请求，不代表任务已进入 `cancelled`。

### 7.2 内存状态与持久化

Store 内存中按 Job ID 保存最后一次验证成功的 Job 快照、同步状态、最近错误、下次轮询时间和每任务一个的轮询控制器。

带版本的 `localStorage` 索引只保存：

```ts
interface PersistedJobReference {
  id: number
  jobType: string
  trackedAt: string
}
```

不持久化 payload、result、error/details、页面草稿、下载路径或任何密钥。本地索引格式非法时安全清空；单条非法引用只丢弃该条，不影响其他可验证记录。

### 7.3 跟踪与刷新恢复

- 业务适配器提交 Job 成功后，将服务器返回的完整 Job 快照交给 Store 跟踪；Store 写入最小引用并依状态决定是否轮询。
- 启动/刷新时，Store 只读取本地索引，逐个调用 `GET /api/jobs/{id}`；不请求或推断其他任务。
- `queued | running | paused` 继续轮询；终态保留快照但停止轮询。
- 404 表示该引用不可恢复：从活动轮询中移除并删除该本地引用，同时在当前运行期保留一个可读错误状态供调用方显示。
- 临时断网或 5xx 不覆盖最后一次成功快照；Store 在有上限退避后再查询。
- 显式移除或清理已完成记录只修改当前浏览器 Store/索引，不删除服务器 Job。

### 7.4 轮询和竞态保护

- 同一 Job ID 同时只能有一个轮询循环和一个在途查询。重复 `track/startPolling` 调用复用现有控制器。
- 每次轮询使用单调更新时间或世代标识，旧响应不得覆盖更新的快照。
- 任务进入终态、用户显式停止、任务被移除或 Store 销毁时，同时清理定时器和中止在途请求。
- 轮询间隔只属于前端反馈节奏，不推断后端剩余时间或伪造进度。

### 7.5 取消

- 只有已跟踪、非终态任务可发起 `POST /api/jobs/{id}/cancel`。
- 取消请求期间保留已有快照，并以服务器返回的 Job 为唯一新状态。
- 服务器返回 `running + cancel_requested=true` 时继续轮询，直到真正终态；前端不提前标记 `cancelled`。
- 取消请求和轮询响应竞态时，使用响应世代/更新时间规则保证较新服务器快照胜出。
- 取消请求失败不改写 Job 状态；保留旧快照并暴露可重试错误。

## 8. Sessions 迁移和文件职责

- `frontend/src/api/client.ts`：统一请求管道、request ID、超时/取消、安全重试和错误归一化。
- `frontend/src/api/errors.ts`：统一错误类型、后端错误体校验和用户向脱敏映射。
- `frontend/src/api/validation.ts`：可复用的小型运行时校验帮助函数，不引入大型 Schema 依赖。
- `frontend/src/api/sessions.ts`：保留 Session 公开类型和响应校验，底层改用 Client；`SessionLoader` 可注入契约和 Session Store 公开行为不变。
- `frontend/src/api/jobs.ts`：Job 公开类型、运行时校验、查询和取消资源适配器。
- `frontend/src/stores/jobs.ts`：Pinia Job Store、最小本地索引、轮询和竞态控制。
- `frontend/src/notifications.ts`：可注入的用户向通知端口；默认实现可为无可见 UI 的内存接口。

实现计划可在不改变职责边界的前提下合并过小文件。不得将轮询、错误体解析或本地 Job 索引重新塞进业务 Store、Vue 组件或页面。

## 9. 错误处理和保留旧内容

- Client 只把失败转换为稳定错误，不自动清空 Store 或页面数据。
- Session Store 继续保持 P2-03 行为：读取失败时不把未验证候选暴露为当前会话，也不删除可供重试的本地候选。
- Job Store 在查询失败时保留最后成功快照，将同步错误与服务器 Job 终态分开表达。
- 契约错误、原始响应文本、内部路径和异常堆栈不进入用户通知或本地持久化。

## 10. 测试与验证

### 10.1 API Client 单元测试

- 每次请求生成并发送 request ID，成功/错误响应回传正确；
- 成功 JSON 经校验后返回，非 JSON、空体或畸形响应不向上暴露；
- 422、404、409、500 和非标准错误体被稳定分类；
- 临时网络/5xx 的 GET 使用有界退避，写请求不自动重放；
- 超时和显式取消可区分，中止后没有额外尝试；
- 绝对 URL、非 `/api/` 路径和不安全响应内容被拒绝。

### 10.2 Job Store 单元测试

- 跟踪 Job 只持久化 ID、类型和记录时间；敏感字段、payload/result 不进入本地存储；
- 刷新后只恢复本地引用的 Job，活动状态继续轮询，终态停止；
- 同 ID 重复跟踪不产生第二个轮询循环；
- 404 清理引用并停止轮询，断网/5xx 保留旧快照并退避恢复；
- 取消返回 `running + cancel_requested=true` 时不提前终止轮询；
- 取消与轮询、停止与在途响应的竞态不回写旧状态；
- 移除和清理只修改浏览器索引，不发起服务器删除。

### 10.3 回归和工程门槛

- sessions 适配器迁移后的合法/畸形/失败响应和 Session Store 刷新恢复行为不变；
- App Shell 现有单元和浏览器回归保持通过，不新增业务页面或可见行为；
- 运行 lint、typecheck、unit、build、受影响的 Chromium e2e、相关 Python 静态守卫、`git diff --check` 和 `tools/smoke_check.py --skip-tests`；
- 功能分支默认不运行全量 pytest；公共基础设施/依赖锁变化、测试失败不稳定或其他仓库规则触发时再按影响扩大验证；
- 验证前后只读比较根工作区两库大小、UTC 修改时间和 SHA-256，必须完全不变。

P2-04 是不可见的纯工程包，用户自测为 `none`，不生成短测或正式验收清单。所有 mock API 测试使用合成数据，不读取真实 `user_data/`。

## 11. 风险与回退

- 主要风险是错误分类过度简化、读取重试失控、取消/轮询旧响应覆盖新状态，以及把完整 Job 数据误存入浏览器。这些都通过严格校验、写请求不自动重放、单轮询控制器、世代/时间规则和本地存储精确测试守卫。
- 若实现调查发现后端错误体、Job 字段或状态语义与本设计不一致，立即停止并回到当前后端实现和测试核对，不在前端臆造兼容语义。
- 回退时可整体回退 P2-04 的纯前端提交，sessions 适配器恢复 P2-03 原窄请求实现。Streamlit 生产入口、FastAPI、数据库和真实 `user_data/` 不受影响。
