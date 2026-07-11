# Phase 3 执行包地图：后端拆分、Schema 收敛与瘦身

> **阶段目标：** 在 Vue 主流程稳定后，解除反向依赖，按领域拆分大模块，使迁移成为 Schema 权威，并分批退役旧实现。
> **阶段状态：** `planned`；Phase 2 切换与真实五流程通过后开始。
> **核心原则：** 先增加兼容边界，再切调用方，最后删除；所有 Schema/删除包均独立回退。
> **历史迁移：** 已发布或已打标的迁移文件保留，不通过删除历史迁移来“清理”旧 Schema。
> **夜间资格：** 所有包级标记以 `NIGHTLY_ELIGIBILITY_MATRIX.md` 为唯一权威，本文件不重复维护。

## 基线与共享模型

### P3-01 Phase 3 调用图、覆盖率与性能基线

- **状态/依赖：** `planned`；P2-21，P1-26 数据可用。
- **目标：** 固定拆分前 import graph、公共 API、测试覆盖、Schema 和端点性能证据。
- **主要范围：** AST/import 调查、公开方法清单、Schema 快照、基准测试和真实流程对照。
- **子任务：** 标记外部调用方；列出动态导入；识别循环依赖；冻结契约测试；保存性能和文件规模报告。
- **不修改：** 本包只调查和加守卫，不重构生产代码。
- **验收：** 每个后续拆分对象都有调用方/测试/回退证据；报告可重复生成。
- **回退/风险：** 只读调查，风险低。
- **模型：** `S-XH / T-H / S-H`。

### P3-02 领域数据模型下沉

- **状态/依赖：** `planned`；P3-01。
- **目标：** 把 `GradingResult`、`QuestionGradingDetail`、`ExamPaperGroup` 等共享类型移到无服务依赖的领域模型模块。
- **主要范围：** 新 `backend/domain_models.py` 或按责任拆分的等价模块；原位置兼容别名。
- **子任务：** 复制类型测试；切换 db_manager 反向导入；逐调用方迁移；循环依赖守卫；弃用说明。
- **不修改：** 不改变字段、序列化、默认值或评分逻辑。
- **验收：** 数据类等价测试；旧 import 一个版本仍可用；db_manager 不再导入 AI/scanner 服务。
- **回退/风险：** 兼容别名使回退简单，风险中等。
- **模型：** `S-XH / T-H / S-H`。

## Repository 拆分

### P3-03 Repository 契约与事务边界

- **状态/依赖：** `planned`；P3-01/P3-02。
- **目标：** 定义 repositories 的连接所有权、事务、返回类型和错误语义，避免把 DBManager 复制成多个大文件。
- **主要范围：** connection factory、repository protocol/base、transaction context、只读/读写契约。
- **子任务：** 从实际调用提取最小接口；定义 row mapping；明确谁 commit/rollback/close；测试嵌套与异常。
- **不修改：** 不引入 ORM，不改变 SQLite/WAL，不提前移动所有 SQL。
- **验收：** 契约测试证明请求级连接、事务回滚和线程隔离；无隐式全局连接。
- **回退/风险：** 新边界增量引入，数据库风险高。
- **模型：** `S-XH / T-H / S-H`。

### P3-04 Students 与 Sessions Repository

- **状态/依赖：** `planned`；P3-03。
- **目标：** 拆出学生、会话、软删/恢复、出勤等聚合，API 和服务不再调用对应 DBManager 方法。
- **主要范围：** students/sessions repositories、映射测试、API dependency wiring。
- **子任务：** 每次迁一个聚合；保持排序/冲突/备份行为；增加兼容 facade；切 API；比较 SQL 结果。
- **不修改：** 不改变学生删除业务规则，不实现 Phase 6 数据。
- **验收：** 现有 API/StudentManager 测试通过；DBManager 兼容方法委托新 repo；事务一致。
- **回退/风险：** 真实学生数据高风险，测试只用临时库。
- **模型：** `S-XH / T-H / S-H`。

### P3-05 Papers、Results 与 Review Repository

- **状态/依赖：** `planned`；P3-03/P3-04。
- **目标：** 拆出答卷、结果、明细、出勤和复核查询/调分事务。
- **主要范围：** papers/results/review repositories、GradingService/ManualReviewService 适配。
- **子任务：** 先只读再写入；批量复核查询；结果保存事务；幂等/冲突；兼容 facade。
- **不修改：** 不改变完整性审计、分数计算和失败卷语义。
- **验收：** grading/review/report 全套回归；多结果回滚；同学生冲突逻辑不变。
- **回退/风险：** 评分数据最高风险。
- **模型：** `S-XH / T-H / S-XH`。

### P3-06 Templates、Regions 与 Settings Repository

- **状态/依赖：** `planned`；P3-03。
- **目标：** 拆出模板/答题区/应用设置，并保留数据库+文件快照补偿协议。
- **主要范围：** template/region/settings repositories、AnswerRegionCommitService 适配。
- **子任务：** 只读映射；草稿/提交事务；snapshot 补偿；设置键值；兼容 facade。
- **不修改：** 不改坐标模型、模板 fingerprint 或 workflow_state 格式。
- **验收：** 现有 region commit/concurrency/snapshot 测试；失败补偿与恢复证据。
- **回退/风险：** 批改前置数据风险高。
- **模型：** `S-XH / T-H / S-H`。

### P3-07 服务、API 与报告调用方切换

- **状态/依赖：** `planned`；P3-04 至 P3-06。
- **目标：** 活跃服务和 API 全部依赖 repositories；DBManager 仅作为一个版本兼容 facade。
- **主要范围：** grading/session/review/report/API dependencies、Streamlit 已退役残留工具。
- **子任务：** 按调用图切换；禁止新 DBManager 直接调用；删除重复 mapping；更新依赖注入；弃用日志。
- **不修改：** 本包不删 DBManager、不做 Schema 迁移。
- **验收：** import guard；API E2E；完整冒烟；DBManager 活跃调用方只剩兼容测试/旧入口。
- **回退/风险：** 跨模块风险高，可按调用域独立提交。
- **模型：** `S-XH / T-H / S-H`。

## SessionManager 拆分

### P3-08 文档解析与本地题块模块

- **状态/依赖：** `planned`；P3-01。
- **目标：** 把 DOCX/PDF 文本、富文本块、本地题目/答案解析移到纯解析模块。
- **主要范围：** `session_manager.py` 解析函数、新 parser modules、现有解析测试。
- **子任务：** 按数据流分层；移动纯函数；显式输入输出；保留兼容 import；增加恶劣文档 fixture。
- **不修改：** 不改变题号规范化、答案推断和生成 fallback 规则。
- **验收：** 解析 fixtures 字节级/结构等价；session_manager 行为不变；无模型客户端依赖。
- **回退/风险：** 纯函数迁移，风险中等。
- **模型：** `S-XH / T-H / S-H`。

### P3-09 Prompt 构建与配置生成编排

- **状态/依赖：** `planned`；P3-08、P1-24/P1-25。
- **目标：** Prompt 构建保持纯函数；配置生成、批次、重试、合并和 progress 进入独立 orchestration service。
- **主要范围：** prompt builders、config generation service、Gateway adapters、兼容 facade。
- **子任务：** 先 snapshot prompt；移纯 builder；提 orchestration；统一 retry；切 config job；保留 API。
- **不修改：** 不借重构改 prompt 文案、模型参数或分数分配规则。
- **验收：** prompt snapshot、假 LLM、部分失败/重试和输出等价测试；无 session_manager 隐式全局路径。
- **回退/风险：** 模型行为高风险。
- **模型：** `S-XH / T-H / S-XH`。

### P3-10 配置归一化、评分约束与质量告警

- **状态/依赖：** `planned`；P3-08/P3-09。
- **目标：** 把 schema 归一、score allocation、objective/solution hard rules 和质量告警拆成可单测模块。
- **主要范围：** normalizer、score policy adapter、quality warnings、session_manager facade。
- **子任务：** 按功能族迁移；固定 golden payload；消除重复 helper；公开最小接口；弃用旧内部函数。
- **不修改：** 不改变 100 分缩放、题型约束和 warning 业务含义。
- **验收：** golden payload 完全等价；所有 config generation/normalization 测试通过；循环依赖为零。
- **回退/风险：** 评分配置高风险。
- **模型：** `S-XH / S-H / S-XH`。

## Schema 与字段治理

### P3-11 迁移版本门槛与运行时 DDL 退役

- **状态/依赖：** `planned`；P3-07，所有活跃表均有迁移覆盖。
- **目标：** 启动只检查/执行受控迁移和版本，不再由各服务散落 `CREATE/ALTER` 修改生产 Schema。
- **主要范围：** DBManager/JobStore/GradingRunStore/question-bank initialize、migration runner、启动提示。
- **子任务：** 盘点所有 runtime DDL；补遗漏迁移；空库 bootstrap；旧库升级；版本过旧/过新拒绝；移除重复字面 DDL。
- **不修改：** 不改变业务数据，不删除历史迁移，不取消自动备份。
- **验收：** 空库、旧快照、当前库副本、重复启动；Schema diff 完全一致；完整迁移预演。
- **回退/风险：** Schema 最高风险。
- **模型：** `S-XH / S-XH / S-XH`。

### P3-12 状态列约束迁移

- **状态/依赖：** `planned`；P3-11。
- **目标：** 依据真实 distinct 值，为 session/paper/region 等稳定状态列补 CHECK 或等价验证。
- **主要范围：** 数据审计工具、SQLite table rebuild migrations、domain enums/validation。
- **子任务：** 副本统计；列出未知值；用户确认业务口径；预清理/映射；迁移；应用层契约。
- **不修改：** 不凭文档猜枚举，不自动改未知真实值。
- **验收：** 多份历史库副本预演；行数/外键/索引/触发器不变；非法新值被拒绝。
- **回退/风险：** 必须备份并可恢复。
- **模型：** `S-XH / S-XH / S-XH`。

### P3-13 `knowledge_id` 到 `knowledge_ids` 读写切换

- **状态/依赖：** `planned`；P3-05/P3-11。
- **目标：** 回填列表字段，活动代码统一读 `knowledge_ids`，旧单值字段进入兼容只读期。
- **主要范围：** migration、row mapping、grading/report/diagnosis readers、兼容写入。
- **子任务：** 数据分布审计；无损回填；双读比较；切主读；停止新写旧列；记录剩余调用。
- **不修改：** 本包不立即删旧列，不改变标签来源或知识点文本。
- **验收：** 旧/新数据 golden 对比；报告/诊断结果一致；回填幂等。
- **回退/风险：** 先兼容后切换，数据风险高。
- **模型：** `S-XH / T-H / S-XH`。

### P3-14 旧列清理与 question-bank sync 状态决策

- **状态/依赖：** `planned`；P3-13 稳定一个版本或用户确认兼容期结束。
- **目标：** 删除已无调用的 `knowledge_id`，并以测量结果决定 sync 四列保留还是迁入状态表。
- **主要范围：** 调用方 guard、forward migration、workflow service、Schema docs。
- **子任务：** grep/运行证据；比较表宽与查询复杂度；记录保留/迁移 ADR；执行最小迁移；预演恢复。
- **不修改：** `session_results.raw_json` 保留；无收益不得为“整洁”迁列。
- **验收：** 调用方为零；迁移副本完整；workflow 状态和 UI 不变。
- **回退/风险：** 删列风险高。
- **模型：** `S-XH / S-H / S-XH`。

## 分批删除

### P3-15 停用向导与断链脚本删除

- **状态/依赖：** `planned`；P3-01、Phase 2 不再调用。
- **目标：** 删除 objective admission UI/runner 和无效入口，保留活跃的 crop calibration。
- **主要范围：** wizard UI、runner、imports、tests/docs/requirements。
- **子任务：** 静态与动态调用调查；移除入口；删死测试；运行 objective recognition 回归；更新清单。
- **不修改：** 保留 `objective_crop_calibration.py` 及识别链使用的代码。
- **验收：** 调用方为空；客观题识别测试和完整冒烟通过。
- **回退/风险：** 独立删除提交。
- **模型：** `S-XH / T-H / S-H`。

### P3-16 旧 CLI、Legacy API 与专属表退役

- **状态/依赖：** `planned`；P3-07、用户确认旧 CLI 无使用需求。
- **目标：** 退役 `main.py`、DBManager Legacy API 和 `exam_results/grading_details` 活跃使用，最后用前向迁移删除表。
- **主要范围：** CLI、legacy methods、调用方、迁移、发布说明。
- **子任务：** 使用调查；导出旧数据工具；兼容警告期；删调用；副本迁移；恢复演练。
- **不修改：** 不删除 `session_results/session_details` 新主路径。
- **验收：** 用户确认；旧数据可导出；活跃调用为零；表删除迁移可回退备份。
- **回退/风险：** 删除业务数据结构。
- **模型：** `S-XH / S-H / S-XH`。

### P3-17 旧技能语义体系退役

- **状态/依赖：** `planned`；Phase 4 前 tag-only 能力完整，用户确认旧数据无需继续查看。
- **目标：** 删除旧技能 UI、service 分支和 11 张旧表的活跃能力，保留 question_tags 主路径和历史迁移记录。
- **主要范围：** legacy/skill services、旧页面、旧训练集、forward drop migration、数据导出/审计报告。
- **子任务：** 真实库副本数据报告；用户确认；导出归档；移调用；删除代码；前向迁移删表；全流程回归。
- **不修改：** 不删除已发布 migrations 006-008；不删除活动 tag projection、diagnosis、training task 表。
- **验收：** tag-only 图谱/推荐/批改上下文完整；旧表行数归档；迁移恢复演练；完整冒烟。
- **回退/风险：** 最高删除风险。
- **模型：** `S-XH / S-XH / S-XH`。

## 性能与阶段门槛

### P3-18 基于证据的性能优化

- **状态/依赖：** `planned`；P1-26/P1-27、P3-07，必须有基线证据。
- **目标：** 只实施能在目标数据规模上证明收益的缓存、批量查询、裁剪缓存和图片预处理优化。
- **主要范围：** review/report crop cache、tag projection request memo、图片压缩 cache、热点 SQL/index。
- **子任务：** 每项一个实验；记录前后 p50/p95、内存和磁盘；失效策略；并发测试；选择性合并。
- **不修改：** 无数字不优化；不改变实时标签语义；不做全局永久缓存。
- **验收：** 每项有环境/数据规模/收益；正确性回归；缓存可清理和重建。
- **回退/风险：** 每项独立提交。
- **模型：** `S-XH / T-H / S-H`。

### P3-19 Phase 3 总门槛

- **状态/依赖：** `planned`；P3-02 至 P3-18 适用包达到 `verified`。
- **目标：** 证明拆分后行为一致、Schema 可升级、删除可恢复、性能不倒退。
- **主要范围：** import guard、API/UI E2E、迁移矩阵、性能报告、ARCHITECTURE/Index。
- **子任务：** 完整 smoke；历史库快照矩阵；真实副本只读验收；删除回退；性能比较；文档同步。
- **不修改：** 只验收，不夹带修复。
- **验收：** 无循环/反向依赖；所有迁移预演绿；五流程通过；已知差异经用户确认。
- **回退/风险：** 阶段检查点。
- **模型：** `S-XH / T-H / S-XH`。
