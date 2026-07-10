# Phase 1 执行包地图：API、任务与数据访问

> **阶段目标：** 在不改变现有 Streamlit 业务行为的前提下，为 Vue 前端提供完整、可恢复、可测试的本机 API 和任务系统。
> **阶段状态：** `in_progress`
> **阶段门槛：** 五流程 API E2E、完整冒烟、模型请求无 `timeout=None`、关键媒体可安全下载。
> **通用约束：** 不修改评分规则、题号契约和活动知识点语义；后续包不得修改真实 `user_data/`。P1-14 已获用户接受的数据例外单独记录在该包风险说明中，不构成后续授权。

## 已实现证据

| ID | 内容 | 状态 | 证据 |
|---|---|---|---|
| P1-01 | FastAPI 骨架、统一错误体、双入口 | `merged` | PR #1；`pre-framework-switch-2026-07-09` |
| P1-02 | sessions/students 基础只读 API | `verified` | `tests/test_api_read_routes.py` |
| P1-03 | session/student 安全写 API | `verified` | `tests/test_api_write_routes.py` |
| P1-04 | rubric/answer_key 同步配置 API | `verified` | `tests/test_api_config_routes.py` |
| P1-05 | template/regions 草稿与提交 API | `verified` | `tests/test_api_template_region_routes.py` |
| P1-06 | 最小 JobManager、JobStore、Jobs API | `verified` | job/store/API 测试；尚非完整 WP1.3 |
| P1-07 | report、scan、grading 三类业务 Job | `verified` | 三组 handler/API 测试 |
| P1-08 | review 题目摘要、列表、确认首批 API | `verified` | `tests/test_api_review_routes.py` |
| P1-09 | Windows 失效路径与跳过测试清理 | `verified` | 776 passed / 0 skipped / 0 failed；快速冒烟通过 |
| P1-10 | JobManager Schema 与应用生命周期 | `verified` | 聚焦 34 passed；全量 785 passed；迁移预演与快速冒烟通过 |
| P1-11 | 三类真实 Job 协作式取消 | `verified` | 聚焦 51 passed；API 34 passed；全量 797 passed；快速冒烟通过 |
| P1-12 | 复核查询服务与原子写入 | `verified` | 聚焦 35 passed；API 35 passed；全量 823 passed；快速冒烟通过 |
| P1-13 | 受控媒体与文件下载 API | `verified` | 聚焦 73 passed；API 67 passed；全量 871 passed；快速冒烟通过 |
| P1-14 | Phase 1 稳定化检查点 | `verified` | 聚焦 87 passed；API 85 passed；全量 889 passed；完整冒烟通过；复审无遗留 |
| P1-15 | Question Bank 只读路由 | `verified` | 组合 156 passed；Question Bank/OpenAPI 64 passed；快速冒烟编译 342 文件；整包复审无遗留 |

这些包已在当前分支形成 Git 检查点，但尚未合并到主线；因此仍不能把 WP1.2、WP1.3 或整个 Phase 1 标记为 `merged` 或完成。

## 稳定化执行包

### P1-09 Windows 失效路径与跳过测试清理

- **状态/依赖：** `verified`；已纳入当前分支 Git 检查点，尚未合并到 `main`。
- **目标：** 失效本地盘或网络盘被视为不可用候选，题库素材继续按既有规则回退；Windows 不再因 POSIX fork-only 测试产生 skip。
- **主要模块：** `question_bank/services/asset_path_service.py`、`tests/test_question_bank_asset_path_service.py`、`tests/test_answer_region_session_lock.py`。
- **子任务：** 用确定性 `OSError` 复现替代真实 `Z:` 等待；统一安全文件判定；覆盖唯一/歧义回退；用 spawn 验证 fresh registry 与跨进程互斥；保留 POSIX raw-fork 条件覆盖。
- **不修改：** 不改变素材搜索顺序、歧义时拒绝猜测的规则、路径存储格式或生产锁协议。
- **验收：** 素材路径 6 项、锁 8 项、草稿/提交 68 项通过；全量 pytest 776 passed / 0 skipped / 0 failed；快速冒烟通过。
- **回退/数据风险：** 单服务函数可回退；只用临时目录，风险低。
- **模型：** `S-XH / T-M / T-H`；若需要改变路径契约，升级执行为 `S-H`。

### P1-10 JobManager Schema 与应用生命周期

- **状态/依赖：** `verified`；P1-09 已验证，已纳入当前分支 Git 检查点，尚未合并到 `main`。
- **目标：** `jobs` Schema 只有一个权威定义，FastAPI 启停能创建、恢复并关闭唯一 JobManager 线程池。
- **主要模块：** `backend/jobs/store.py`、`migrations/grading/003_add_jobs.sql`、`backend/api/app.py`、`backend/api/dependencies.py`、Schema 工具与测试。
- **子任务：** 以 `003_add_jobs.sql` 作为唯一完整 DDL；000 保持 Phase 0 边界；JobStore 兼容旧表并确定关闭连接；manager shutdown 与 submit 共用锁；FastAPI lifespan 创建、恢复和关闭 app-owned manager；测试 override 保持外部所有权。
- **不修改：** 不在本包增加新 job 类型，不移除 Phase 3 前的兼容初始化能力。
- **验收：** P1-10 聚焦回归 34 passed；阅卷库临时副本执行 003 后 Schema 等价且业务行数无变化；快速冒烟编译 320 文件且两库副本幂等；全量 785 passed / 0 skipped / 0 failed。
- **回退/数据风险：** 数据库结构不删列不删表；只在临时副本预演；风险高但可逆。
- **模型：** `S-XH / S-H / S-H`；涉及迁移策略，禁止降为 Luna。

### P1-11 协作式任务取消

- **状态/依赖：** `verified`；P1-10 已验证，已纳入当前分支 Git 检查点，尚未合并到 `main`。
- **目标：** queued 任务可立即取消；running 任务只在 handler 确认安全停止后进入 cancelled，取消后不继续写报告、扫描结果或评分结果。
- **主要模块：** `backend/jobs/manager.py`、`backend/jobs/store.py`、三个现有 handler、相关领域服务回调。
- **子任务：** 定义状态机；增加 `raise_if_cancelled()` 或等价协议；在安全边界轮询；区分“请求取消”和“已经取消”；覆盖阻塞调用返回后的副作用防护。
- **不修改：** 不承诺强杀 Python 线程或中断正在进行的单次外部 HTTP 请求。
- **验收：** queued/running/race/restart 与时间戳、终态防覆盖、无请求取消信号防孤儿通过；报告 staging、扫描 latest 原子发布、整卷/混合/failed-only 批改取消契约通过；聚焦 51 passed，API 回归 34 passed，快速冒烟通过，全量 797 passed / 0 skipped / 0 failed。
- **回退/数据风险：** 不改评分规则；使用假服务和临时文件；并发风险高。
- **模型：** `S-XH / S-H / S-XH`。

### P1-12 复核查询服务与原子写入

- **状态/依赖：** `verified`；P1-09 已验证，已纳入当前分支 Git 检查点，尚未合并到 `main`。
- **目标：** review router 恢复为薄壳；一次查询取得题目复核数据；多结果确认要么全部成功，要么不写入。
- **主要模块：** `backend/api/routers/review.py`、新复核查询/应用服务、`manual_review_service.py`、`db_manager.py`、review tests。
- **子任务：** 提取复核判定与 score map；消除逐 result 查询；定义事务边界；校验分数范围和 detail 所属关系；增加大班级查询计数测试。
- **不修改：** 不新增复核状态表，不改变“已复核/需复核/低置信度”现有业务口径。
- **验收：** 60 个 result 的应用读模型单 JOIN，API GET 连同会话检查不超过 2 次连接；第二个 result 写入失败时整批 detail 元数据与总分回滚；批注失败返回脱敏、可重试的 `retry_required`；聚焦与 API 回归各 35 passed，快速冒烟编译 324 文件且两库副本幂等，全量 823 passed / 0 skipped / 0 failed。
- **回退/数据风险：** 临时库和临时图片目录；人工调分属于高风险写入。
- **模型：** `S-XH / T-H / S-H`；若需改变 `ManualReviewService` 事务协议，执行升级 `S-H`。

### P1-13 受控媒体与文件下载 API

- **状态/依赖：** `verified`；P1-12 已验证，已纳入当前分支 Git 检查点，尚未合并到 `main`。
- **目标：** Vue 可查看答题区裁剪、原卷页并下载导出文件，客户端不接收或提交任意绝对路径。
- **主要模块：** 新 media/files router 与 schema、裁剪/批注服务、`original_paper_exporter.py`、reports/job result。
- **子任务：** 定义受控资源 ID；限制根目录和扩展名；实现图片/文件流；处理不存在、过期和越界路径；给 job result 返回下载 URL。
- **不修改：** 不开放通用文件浏览器，不允许请求任意磁盘路径，不改变原卷文件。
- **验收：** review item 返回语义化媒体 URL；正常原卷/批注页、内存裁剪与报告下载通过；路径穿越、绝对越界、旧路径 basename 碰撞、错误 session、过期文件和非白名单扩展名均拒绝；公开 job 结果与错误不泄露内部路径；响应 Content-Type/文件名和 `Cache-Control: no-store` 正确。聚焦 73 passed，API 回归 67 passed，快速冒烟编译 336 文件且两库副本幂等，全量 871 passed / 0 skipped / 0 failed。
- **回退/数据风险：** 只读媒体；输出只在临时 reports 目录生成；安全风险高。
- **模型：** `S-XH / T-H / S-H`。

### P1-14 Phase 1 稳定化检查点

- **状态/依赖：** `verified`；P1-09 至 P1-13 已验证，已纳入当前分支 Git 检查点，尚未合并到 `main`。
- **目标：** 把当前 WP1.2/WP1.3 工作整理成可审阅、可回退的 Git 检查点。
- **主要模块：** 当前所有 `backend/`、相关迁移、测试、计划与架构文档。
- **子任务：** 全量 review；核对 OpenAPI；跑完整测试和冒烟；检查 `git diff`/未跟踪文件；显式排除 `user_data/`；准备提交与 PR 摘要。
- **不修改：** 本包不新增业务功能；未经用户确认不 commit/push/建 PR。
- **验收：** OpenAPI 为 25 paths / 33 operations、无重复 operation ID，422 统一 `ErrorResponse`，二进制 200 媒体类型准确；整包复审 0 Critical / 0 Important / 0 Minor；聚焦 87 passed，API 85 passed，完整 smoke 为 889 passed / 0 skipped / 0 failed、编译 338 个第一方文件、两库副本幂等且 `integrity_check=ok`；暂存区为空，提交范围显式排除真实数据。
- **回退/数据风险：** 一次诊断因未完整 override lifespan 在真实 grading DB 创建了空 `jobs` 表与两个索引，用户明确要求保留；接受后两库文件指纹经完整 smoke 前后复核不变。`user_data/` 不纳入候选提交，后续无额外写入授权。
- **模型：** `S-XH / T-H / S-H`。

## 领域 API 与 Job

### P1-15 Question Bank 只读路由

- **状态/依赖：** `verified`；P1-14 已验证，已纳入当前分支 Git 检查点，尚未合并到 `main`。
- **目标：** 通过严格无源写入的读模型提供试卷列表、题目分页/筛选、题目详情、当前标签、富文本与预览元数据，以及受控题目素材/预览图片。
- **主要模块：** `question_bank/services/question_service.py`、`question_read_service.py`、新 question-bank router/schema、OpenAPI 与隔离测试。
- **子任务：** 从现有页面提取筛选契约；绑定同一 PathManager 快照中的题库 DB/数据根；实现分页排序与统一 404；显式投影并清理 marker/路径；以语义 ID 访问素材；源 main/WAL 经有界 M1-W1-W2-M2 捕获到系统临时目录，SQLite 只打开候选，持续变化返回脱敏 503。
- **不修改：** 不导入文件、不调用 AI、不写标签、不迁移旧技能体系。
- **验收：** 空库、分页、组合筛选、详情、缺失/软删题目、长文本、标签/富文本/预览脱敏通过；资产/预览固定根、越界/歧义/类型/过期与 `no-store` 通过；quiescent/active WAL、checkpoint/churn、候选清理和五路 503 通过。组合回归 156 passed，Question Bank/OpenAPI 64 passed，快速冒烟编译 342 文件且两库副本幂等、`integrity_check=ok`；OpenAPI 30 paths / 38 operations / 0 duplicate IDs；整包复审 0 Critical / 0 Important / 0 Minor。
- **回退/数据风险：** 只读源题库与系统临时候选；不打开源 SQLite、不修改源 WAL/SHM。乐观文件快照不等同 SQLite 官方原子备份；每请求 O(main+WAL) 成本留给 P1-26 测量，SQLite 3.43.1 既有 WAL-reset 风险另行升级。
- **模型：** `S-XH / T-M / T-H`。

### P1-16 Question Bank 轻写与导入准备

- **状态/依赖：** `ready`；P1-15 已验证。
- **目标：** 支持教师确认标签、删除/恢复允许的题目元数据和创建导入请求，但耗时导入仍交给 job。
- **主要模块：** `QuestionService`、source/archive/rich-content 服务、question-bank router/schema。
- **子任务：** 逐项核对现有写行为；定义乐观校验；实现精确标签保存；定义上传暂存资源；测试失败不留下半成品。
- **不修改：** 不自动写旧技能表，不把 AI 结果当教师确认，不直接执行长导入。
- **验收：** 标签幂等、冲突、缺失素材、非法题目 ID、失败回滚测试；旧 Streamlit 行为保持。
- **回退/数据风险：** 仅临时题库和上传目录；业务数据风险中等。
- **模型：** `S-XH / T-H / S-H`。

### P1-17 配置生成 Job

- **状态/依赖：** `planned`；P1-11、P1-14。
- **目标：** 上传试卷后以 `config_generation` job 调用现有 `session_manager` 生成 rubric/answer_key，并保存可重试结果。
- **主要模块：** `session_manager.py`、Job handlers、config router/schema、API profile 读取。
- **子任务：** 定义无密钥 payload；接入 progress/cancel；保存临时结果与最终绑定；覆盖部分题失败和单题重试；暴露结果摘要。
- **不修改：** 不重写配置生成算法，不在 payload/database 保存 API key。
- **验收：** 假 LLM 的成功、失败、取消、重试、重启状态测试；生成文件原子写入。
- **回退/数据风险：** 只用临时上传和配置目录；模型调用高风险。
- **模型：** `S-XH / T-H / S-H`。

### P1-18 题库导入与 AI 打标 Job

- **状态/依赖：** `planned`；P1-11、P1-16、P1-24 后可最终切换统一网关。
- **目标：** `question_import` 与 `tagging_sync` 长任务可查询进度、部分失败和重试，保持 complete 才自动保存的现有规则。
- **主要模块：** `grading_paper_intake_service.py`、`ai_tagging_service.py`、QuestionService、Job handlers/API。
- **子任务：** 分离导入和打标 payload；接入批次事件；持久化失败分类；取消停止后续批次；结果返回成功/失败题号。
- **不修改：** 不改变标签质量门槛，不恢复旧技能 AI 消歧，不阻断阅卷主流程。
- **验收：** 批次成功、部分失败、取消、重试、脱敏错误和幂等测试。
- **回退/数据风险：** 临时题库副本；AI 与文件导入风险高。
- **模型：** `S-XH / T-H / S-H`。

### P1-19 Training 诊断与任务只读/轻写 API

- **状态/依赖：** `planned`；P1-15。
- **目标：** 提供 tag profiles、学生薄弱点、推荐草案、训练任务查询及教师确认任务的 API。
- **主要模块：** `DiagnosisProfileService`、`PracticePlanService`、`TrainingTaskService`、training router/schema。
- **子任务：** 固定 scope/exam_scope；适配 tag-only 主路径；分页训练任务；确认任务写入；空证据和缺标签清晰返回。
- **不修改：** 不实现 Phase 4 新掌握度公式，不启用 legacy/skill 推荐分支。
- **验收：** 现有诊断/推荐测试复用；API 契约、权限边界和临时双库测试。
- **回退/数据风险：** 双数据库副本；训练任务写入风险中等。
- **模型：** `S-XH / T-H / S-H`。

### P1-20 Training 导出 Job

- **状态/依赖：** `planned`；P1-11、P1-19。
- **目标：** `training_export` job 生成现有 Word/Markdown 训练材料并返回受控下载 URL。
- **主要模块：** `training_export_service.py`、question-bank exporters、Job handlers、media/download API。
- **子任务：** 定义 task/plan 输入；复用导出配置；接入取消和进度；原子写输出；返回记录 ID 与下载资源。
- **不修改：** 不改变选题算法，不新增导出格式，不要求工作机安装 Node。
- **验收：** 假数据导出、缺素材降级、取消、重试、文件名与下载测试。
- **回退/数据风险：** 只写临时输出目录，风险中等。
- **模型：** `S-XH / T-H / T-H`。

### P1-21 Graph 查询 API

- **状态/依赖：** `planned`；P1-19。
- **目标：** 提供 tag profiles、graph rows、节点证据和下钻数据，为 Phase 2 图表和 Phase 4 关系层预留稳定契约。
- **主要模块：** `DiagnosisProfileService`、`question_tag_projection_service.py`、`skill_graph_projection.py`、graph router/schema。
- **子任务：** 定义节点/边/证据 schema；实现班级/学生/考试过滤；请求内去重；分页证据；空态与缺失来源处理。
- **不修改：** 不创建 `tag_relations`，不返回旧 concept/skill 活动语义。
- **验收：** tag-only 数据测试；节点统计与证据一致；无标签和未知学生契约明确。
- **回退/数据风险：** 只读双库，风险低。
- **模型：** `S-XH / T-H / T-H`。

### P1-22 Ops 只读与自检 API

- **状态/依赖：** `planned`；P1-14。
- **目标：** 暴露版本、目录可写性、数据库状态、迁移状态、外部工具可用性和备份清单，不返回密钥或学生正文。
- **主要模块：** `pages/系统自检.py` 的非 UI 逻辑、`update_tools/list_backups.py`、migration status、ops router/schema。
- **子任务：** 下沉自检服务；定义脱敏结果；限制慢检查；区分 warning/error；增加健康快照测试。
- **不修改：** 不执行备份、恢复、迁移或数据包导入。
- **验收：** 临时环境的正常/缺工具/坏库副本状态；响应不含 API key 和敏感正文。
- **回退/数据风险：** 只读，风险低。
- **模型：** `S-XH / T-M / T-H`。

### P1-23 Ops 受保护写操作

- **状态/依赖：** `planned`；P1-11、P1-22。
- **目标：** 备份、恢复、迁移、数据包导入导出通过独立 job 和预检/确认令牌执行，失败时可恢复。
- **主要模块：** `update_tools/backup_core.py`、backup/restore/migrate、`data_transfer_service.py`、ops jobs/API。
- **子任务：** 每类操作先 dry-run；生成短期确认令牌；执行前备份；限制目标根目录；记录结构化结果和恢复说明。
- **不修改：** 不开放任意命令执行，不自动操作真实库，不默认包含 Phase 6 数据。
- **验收：** 全部在临时数据根；备份失败阻断恢复/导入；路径越界拒绝；迁移预演和完整性检查通过。
- **回退/数据风险：** 数据风险最高；真实操作必须再次获得用户明确授权。
- **模型：** `S-XH / S-XH / S-XH`。

## LLM 与数据访问

### P1-24 LLM Gateway 核心

- **状态/依赖：** `planned`；P1-14。
- **目标：** 建立唯一模型请求策略层，统一超时、重试分类、节流、请求 ID 和用量记录。
- **主要模块：** `llm_client.py` 演进或 `backend/llm/`、`api_profiles.py`、`usage_logger.py`、`request_pacer.py`。
- **子任务：** 盘点请求类型；定义超时预算；集中可重试错误；统一 usage event；兼容 Chat Completions/Responses；契约测试。
- **不修改：** 不更换用户模型供应商，不改变 prompt 内容和评分语义，不迁移历史日志。
- **验收：** 假客户端覆盖超时、限流、参数不兼容、JSON 修复和用量；无无限重试。
- **回退/数据风险：** 兼容层保留旧入口一个版本；模型调用高风险。
- **模型：** `S-XH / S-XH / S-XH`。

### P1-25 四处直连迁移与缺失超时清零

- **状态/依赖：** `planned`；P1-24。
- **目标：** choice、fill-blank、objective batch、AI tagging 全部走 Gateway，第一方代码 `timeout=None` 为零。
- **主要模块：** 四个现有直连模块、hybrid 路径、Gateway adapters 和回归测试。
- **子任务：** 每次只迁一条调用链；保留请求/响应协议；比较重试次数和 fallback；移除重复客户端构造；grep 守卫。
- **不修改：** 不在同包改识别算法、提示词或批改并发参数。
- **验收：** 每条调用链现有测试通过；新增 Gateway 使用断言；`rg timeout=None` 零命中；受控 API 健康检查另需用户授权。
- **回退/数据风险：** 分调用链独立提交可回退；模型行为风险高。
- **模型：** `S-XH / T-H / S-XH`。

### P1-26 API/DB 性能测量基线

- **状态/依赖：** `planned`；P1-15、P1-19、P1-21、P1-22。
- **目标：** 在优化前记录每个 API 的耗时、查询次数、返回行数和测试数据规模。
- **主要模块：** API middleware/dependencies、DB connection hooks、benchmark fixtures、性能报告文档。
- **子任务：** 定义低开销指标；构造小/中/大临时数据集；记录 p50/p95；定位 N+1；保存基线而不承诺优化。
- **不修改：** 不凭感觉加缓存、索引或连接池，不记录学生正文。
- **验收：** 可重复 benchmark；日志脱敏；性能数据包含环境和样本规模。
- **回退/数据风险：** instrumentation 可开关；只用生成数据。
- **模型：** `S-XH / T-H / S-H`。

### P1-27 请求级连接复用与只读连接

- **状态/依赖：** `planned`；P1-26 证明收益后才 `ready`。
- **目标：** API 请求内复用显式连接，并把安全只读连接策略扩展到经 P1-26 证明有收益的端点，同时保持 Streamlit 旧调用兼容。P1-15 已先为题库 GET 落地“源 main/WAL 捕获到临时候选、候选使用 read-only URI”的零源侧车方案；本包不是首次引入题库只读能力。
- **主要模块：** `db_manager.py`、`question_bank/database/schema.py`、API dependencies、repositories 前置适配。
- **子任务：** 先选高收益端点；测量 P1-15 每请求快照成本并评估是否存在不削弱零源写入边界的复用/失效协议；增加外部连接参数；请求结束关闭；写请求事务；比较优化前后数字。
- **不修改：** 不引入第三方连接池，不改变 WAL/busy_timeout，不提前做 Phase 3 仓储拆分。
- **验收：** 并发读写、异常关闭、线程隔离、只读拒写测试；性能数字有改善，否则不合并。
- **回退/数据风险：** 保留旧构造方式；数据库并发风险高。
- **模型：** `S-XH / T-H / S-H`。

## 阶段验收

### P1-28 五流程 API E2E

- **状态/依赖：** `planned`；P1-13、P1-17、P1-20 和核心领域 API。
- **目标：** 在临时数据根通过 API 完成建会话、配置、模板/区域、扫描/批改、复核、导出下载。
- **主要模块：** 新 API E2E fixture/test；假 LLM、临时双库和临时素材。
- **子任务：** 固定最小样例；逐阶段断言持久化；轮询 job；模拟失败恢复；确认最终文件和分数。
- **不修改：** 不调用真实模型，不使用真实学生数据，不替代单元/契约测试。
- **验收：** 成功路径、扫描失败、批改部分失败、复核修改、导出下载和重启后 job 状态全部通过。
- **回退/数据风险：** 纯测试资产，风险低。
- **模型：** `S-XH / T-H / S-H`。

### P1-29 Phase 1 总门槛

- **状态/依赖：** `planned`；P1-15 至 P1-28 全部达到 `verified`。
- **目标：** 证明 API 与 Streamlit 双通道可用，并形成进入 Phase 2 全面迁移的稳定基线。
- **主要模块：** 全仓测试、冒烟、OpenAPI 快照、ARCHITECTURE/AGENTS/Index。
- **子任务：** 完整 smoke；双入口启动；临时数据 API E2E；真实工作机受控基础流程；性能/已知问题报告；用户确认集成。
- **不修改：** 本包只验收和文档同步，不夹带功能修复。
- **验收：** 完整冒烟绿；`timeout=None` 零；数据库预演绿；无 P1 高风险未解决项。
- **回退/数据风险：** 验收使用副本；真实冒烟前另行确认。
- **模型：** `S-XH / T-H / S-XH`。

## Phase 2 样板页提前开工门槛

P1-09 至 P1-13、P1-28 中的 review/media 切片达到 `verified` 后，可以开始 P2-01 至 P2-08。题库、训练、图谱和 ops 页面不得在 Phase 1 总门槛前扩散迁移。
