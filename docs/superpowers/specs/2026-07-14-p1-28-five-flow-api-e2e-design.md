# P1-28 五流程 API E2E 设计

## 1. 目标与边界

P1-28 用一套可重复、可隔离的 API 端到端测试，证明当前 FastAPI 基线可以在同一临时数据根完成五段业务链：会话与配置、模板与答题区、扫描与批改、教师复核、报表导出与受控下载。测试逐阶段核对 HTTP 契约、Job 轮询终态、SQLite 持久化、最终教师分和下载文件内容。

本包只增加测试资产和测试辅助代码。它不新增或修改生产 API，不改变评分规则、题号、Job 状态、报告格式或活动 `knowledge_point` 语义；如果测试揭示现有契约缺口，必须先用失败测试证明缺口，并只在 P1-28 边界内做最小修复。所有数据库、答卷图片、配置和导出文件都位于 pytest 临时目录，不读取、复制、打开或写入根目录真实 `user_data/`，也不调用真实模型或读取真实密钥。

P1-28 是不可见工程验收包，用户自测为 `none`。它不替代既有路由、领域或契约测试，也不承担 P1-29 的真实工作机黑盒验收。

## 2. 方案选择

采用“真实 API/Job/临时持久化 + 确定性外部边界替身”的有状态 E2E 测试台。

- FastAPI 路由、Pydantic 契约、JobManager/JobStore、配置发布、扫描/批改 Job 编排、复核应用服务、报表生成器和文件下载服务使用当前真实实现。
- 模型、OCR/扫描识别和批改模型调用使用确定性假实现；它们只返回固定合成结果，并通过现有注入点进入真实 Job 编排。
- 测试只通过 HTTP 提交和查询业务动作；允许直接读取临时 SQLite 或临时文件作持久化证据，但不能绕过 API 触发业务阶段。

没有选择真实 uvicorn 子进程的纯黑盒方案，因为它会把端口、进程清理和模型配置引入测试，并削弱失败场景的确定性。也没有选择让所有 Job handler 直接返回固定字典，因为那只能证明路由连通，不能证明配置发布、扫描结果文件、评分记录、复核事务和下载边界共同工作。

## 3. 测试台组件

新增 `tests/api_e2e/`，把 P1-28 的资产与现有单路由测试分开：

- `conftest.py`：创建临时数据根、临时双库、PathManager 形状对象、真实 `DBManager`、真实 `JobManager`、依赖覆盖、固定模板/答卷图片和 TestClient；退出时关闭 manager 并检查临时资源。
- `harness.py`：保存 `ApiE2EHarness`、Job HTTP 轮询、固定配置 payload、假 LLM、假扫描器、假批改服务、失败开关和只读持久化查询。辅助方法使用业务 ID，不向断言暴露临时绝对路径。
- `test_five_flow.py`：验证完整成功链和最终下载。
- `test_failure_recovery.py`：验证扫描失败重试、批改部分失败恢复和进程重启后的 Job 状态。

测试台使用同一个临时阅卷库承载业务表与 `jobs` 表，和生产默认形态一致。题库库只初始化为独立空临时库，证明批改 Job 不会退回真实题库。路径依赖全部指向同一个临时根，文件下载服务只允许该根下的 reports 目录。

## 4. 固定合成样例

样例包含一个 100 分制考试、六道符合现行单题分值上限的题、两名合成学生和两份小型生成图片：

- 配置生成返回 Q1-Q6，分值依次为 17、17、17、17、17、15，标准答案为固定占位文本；生成结果原子替换会话的 bootstrap rubric/answer 文件。
- 模板正反面各为一张小型 PNG；答题区只有一个已确认 Q1 区域，提交后模板必须 ready，快照不得 pending。
- 第一次成功扫描把两份答卷映射到两名合成学生，并发布 `scan_analysis_latest.json`。
- 第一次完整批改对第一份答卷写入六题明细，总分 85，其中 Q1 为 12/17 且需复核；对第二份答卷产生一份 `failed` 结果。Job 业务状态仍遵循现有规则：运行结束可以 `completed`，但必须同时显示 `graded=1`、`failed=1`。
- `failed_only` 恢复把失败答卷批改为 70 分，最终两份答卷均为 `graded`。
- 教师通过 Review API 把第一份 Q1 从 12 调整为 17；应用服务必须在同一事务把总分从 85 重算为 90，并清除相应的待复核状态。
- 真实报表生成器导出 XLSX，Job 结果只公开安全文件名和下载 URL；下载响应必须为 XLSX、`Cache-Control: no-store`，工作簿 `成绩与小题明细` 中第一名合成学生的 `总分` 必须为 90。

固定文本不得使用真实姓名、学校、题干或答卷内容。假 LLM 对象记录逻辑调用类型但不具备网络客户端，不读取环境变量或 API profile。

## 5. 成功路径与阶段证据

完整路径按以下顺序执行，每一阶段只在上一阶段的持久化证据成立后继续：

1. `POST /api/sessions` 创建会话；GET 详情与临时数据库行一致。
2. `POST /api/sessions/{id}/config/generate` 提交配置 Job；只通过 `GET /api/jobs/{id}` 轮询到 `succeeded`，再由 config GET 和磁盘 JSON 证明配置已经绑定。
3. template PUT、regions draft PUT/GET 和 regions commit POST 完成模板与区域；regions GET、数据库和快照文件一致。
4. scan POST 与 grading POST 分别提交真实 Job；轮询终态，并核对扫描结果文件、答卷状态、结果和逐题明细。
5. review questions/items GET 找到待复核 Q1；confirm POST 后再次 GET 与数据库均显示教师最终分。
6. report export POST 后轮询，读取公开 download URL；下载文件并用 openpyxl 检查工作簿最终分。

公开 Job payload/result 和错误响应还必须证明没有临时绝对路径、密钥或合成试卷正文。

## 6. 失败恢复与重启

### 6.1 扫描失败

假扫描器第一次在发布前抛出固定内部异常。Job 必须为 `failed`，公开错误保持脱敏，数据库不得新增答卷，且不存在半成品 latest 文件。通过同一 scan API 再次提交后成功，证明失败不破坏重试入口。

### 6.2 批改部分失败

第一次完整批改运行正常结束，但一份答卷为 `failed`。会话进度必须同时返回一个成功和一个失败，不能把 session/job 的完成误读成全部成功。随后用 `failed_only=true` 再次提交，只恢复失败答卷；已有成功结果不得被重复写入或改变。

### 6.3 重启状态

在独立临时 JobStore 中准备 `queued`、`running`、`succeeded` 三类记录，关闭旧 manager 后用同一存储创建新 manager，模拟 API 进程重启。通过新 TestClient 的 jobs GET 验证 queued/running 被标为 `failed`，succeeded 保持终态，公开错误脱敏；不承诺进程重启后续跑任务。

## 7. 测试和完成门槛

按 TDD 逐个建立以下行为：

1. 测试台隔离、HTTP Job 轮询和脱敏守卫。
2. 会话、配置、模板/区域成功链及逐阶段持久化。
3. 扫描失败后重试成功且无半成品。
4. 批改部分失败、进度统计和 `failed_only` 恢复。
5. 复核改分事务与最终分。
6. 报表导出、受控下载和 XLSX 最终分。
7. 重启后 Job 状态恢复。

功能分支门槛为 P1-28 聚焦测试、受影响的 sessions/config/template/scan/grading/review/report/files/jobs 回归、`git diff --check`、`tools/smoke_check.py --skip-tests`、功能 worktree 无 `user_data/` 改动，以及根目录真实两库大小、UTC 修改时间和 SHA-256 不变。P1-28 跨越多个核心 API 和 Job 状态，integration 波次必须运行完整 `tools/smoke_check.py`；功能分支仍按仓库分层规则不默认重复全量 pytest。

## 8. 回退与后续

若本包只增加测试资产，回退时删除 `tests/api_e2e/` 即可；若测试揭示并最小修复生产缺口，则修复必须独立提交并附对应 RED/GREEN 证据，便于单独回退。

P1-28 通过只证明当前合成临时数据根上的 API 五流程和失败恢复基线。P1-29 仍需独立执行 Phase 1 总门槛、双入口、完整冒烟和用户明确确认；P2-20 的新 UI 真实五流程也不能复用本包结论替代。
