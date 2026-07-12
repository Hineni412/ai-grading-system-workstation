# 夜间执行资格矩阵

> **权威范围：** 本文件只负责包级夜间资格；包状态以 `EXECUTION_INDEX.md` 为准，稳定依赖与边界以对应 Phase map 为准。
> **审查信息：** 2026-07-11，按现有包范围、风险和夜间自动化守卫逐项审查。

## 标记

| 标记 | 含义 |
|---|---|
| `eligible_after_plan` | 包的边界允许夜间无人值守实施，但不代表当前可执行。 |
| `daytime_only` | 包必须在白天实施或验收，不得由夜间自动化领取。 |
| `completed_not_applicable` | 包已完成，不得重复领取。 |

`eligible_after_plan` 还必须同时满足以下运行时门槛：

1. `EXECUTION_INDEX.md` 状态为 `ready`，全部依赖已进入最新 `origin/main`。
2. 现场能唯一确认专属、干净、同步共同基线的 worktree/分支，并符合并行手册的长期规则。
3. 即时计划已经过要求的复核，并包含 `ready_for_execution`、`允许夜间执行: yes` 和完整计划基线 SHA。
4. 计划目标没有漂移或通道冲突，测试只使用临时、生成或模拟数据。
5. 不接触真实 `user_data/`，不做数据库迁移、不可逆删除、真实密钥/模型调用、用户视觉验收或业务语义裁决。

任一门槛不满足时，自动化只能报告。当前 P1-16 与 P2-01 均可按用户安排在白天推进；本矩阵不改变白天优先级。

## 汇总

| Phase | 正式包 | `eligible_after_plan` | `daytime_only` | `completed_not_applicable` |
|---|---:|---:|---:|---:|
| Phase 1 | 21 | 8 | 6 | 7 |
| Phase 2 | 22 | 10 | 12 | 0 |
| Phase 3 | 19 | 10 | 9 | 0 |
| Phase 4 | 12 | 1 | 11 | 0 |
| Phase 5 | 13 | 2 | 11 | 0 |
| **合计** | **87** | **31** | **49** | **7** |

## Phase 1

正式范围为 P1-09 至 P1-29。

| 包 | 标题 | 夜间资格 | 理由与额外门槛 |
|---|---|---|---|
| P1-09 | Windows 失效路径与跳过测试清理 | `completed_not_applicable` | 已合并并通过全量验证。 |
| P1-10 | JobManager Schema 与应用生命周期 | `completed_not_applicable` | 已合并；原包含迁移策略和严格执行门槛。 |
| P1-11 | 协作式任务取消 | `completed_not_applicable` | 已合并并验证并发取消状态机。 |
| P1-12 | 复核查询服务与原子写入 | `completed_not_applicable` | 已合并并验证评分写入原子性。 |
| P1-13 | 受控媒体与文件下载 API | `completed_not_applicable` | 已合并并验证路径与媒体安全契约。 |
| P1-14 | Phase 1 稳定化检查点 | `completed_not_applicable` | 已完成并合并阶段检查点。 |
| P1-15 | Question Bank 只读路由 | `completed_not_applicable` | 已合并并验证源题库零写入读取。 |
| P1-16 | Question Bank 轻写与导入准备 | `eligible_after_plan` | 仅用临时题库和上传目录；禁止长导入、真实 AI 和半成品残留。 |
| P1-17 | 配置生成 Job | `eligible_after_plan` | 可用假 LLM 和临时目录验收；禁止真实模型、密钥和 prompt 语义变化。 |
| P1-18 | 题库导入与 AI 打标 Job | `eligible_after_plan` | 仅用临时题库、临时文件和模拟 AI 验证批次、取消及幂等。 |
| P1-19 | Training 诊断与任务只读/轻写 API | `eligible_after_plan` | 仅在临时双库写入；固定现有 scope 与 tag-only 口径。 |
| P1-20 | Training 导出 Job | `eligible_after_plan` | 只写临时输出，不改变选题算法或导出格式。 |
| P1-21 | Graph 查询 API | `eligible_after_plan` | 只读双库；禁止新增关系表或恢复旧 concept/skill 语义。 |
| P1-22 | Ops 只读与自检 API | `eligible_after_plan` | 只读且风险低；禁止执行备份、恢复、迁移或导入。 |
| P1-23 | Ops 受保护写操作 | `daytime_only` | 只允许白天人工监督，且备份、恢复、迁移和导入属于最高数据风险。 |
| P1-24 | LLM Gateway 核心 | `daytime_only` | 只允许白天人工监督；需裁决全局超时、重试、节流和模型协议。 |
| P1-25 | 四处直连迁移与缺失超时清零 | `daytime_only` | 模型行为风险高，完整验收包含受控 API 健康检查。 |
| P1-26 | API/DB 性能测量基线 | `eligible_after_plan` | 只用生成数据做可关闭测量；不实施缓存、索引或连接池优化。 |
| P1-27 | 请求级连接复用与只读连接 | `daytime_only` | 必须人工审阅收益证据，并处理数据库并发实验门。 |
| P1-28 | 五流程 API E2E | `daytime_only` | 跨领域集成验收，需白天检查完整业务链。 |
| P1-29 | Phase 1 总门槛 | `daytime_only` | 阶段总门槛包含真实工作机受控流程和用户确认。 |

## Phase 2

| 包 | 标题 | 夜间资格 | 理由与额外门槛 |
|---|---|---|---|
| P2-01 | 前端工程、依赖锁与质量命令 | `eligible_after_plan` | 纯增量工程基础，不切启动入口且不需要真实数据。 |
| P2-02 | Token、Element 主题与基础控件 | `eligible_after_plan` | 样式可独立回退；用组件、对比度和多视口自动检查验收。 |
| P2-03 | App Shell、路由与全局会话上下文 | `eligible_after_plan` | 新前端内部低风险变更，只复用现有 session 语义。 |
| P2-04 | API Client、错误契约与 Job Store | `eligible_after_plan` | 纯前端网络层，可用 mock API 验证。 |
| P2-05 | 复核队列、筛选与连续导航 | `eligible_after_plan` | 只读页面切片，可用生成数据验证导航和性能。 |
| P2-06 | 答卷证据查看器 | `eligible_after_plan` | 只读媒体且不修改原图；只使用受控测试素材和自动浏览器检查。 |
| P2-07 | 评分检查器、草稿与确认 | `daytime_only` | 会确认教师最终分并写评分结果。 |
| P2-08 | 样板页集成、快捷键与视觉门槛 | `daytime_only` | 属于视觉与完整业务流程门槛，需要用户/白天验收。 |
| P2-09 | 会话、配置与 Rubric 编辑 | `daytime_only` | 写入 rubric 和配置，涉及评分政策边界。 |
| P2-10 | 样卷模板与答题区编辑器 | `daytime_only` | 模板区域是批改前置，需交互与数据库一致性验收。 |
| P2-11 | 扫描预检、批改进度与失败恢复 | `daytime_only` | 涉及长任务暂停、恢复、取消和竞态状态语义。 |
| P2-12 | 报告、原卷与文件下载 | `eligible_after_plan` | 输出可删除重建；只用临时输出根且不改变报告口径。 |
| P2-13 | 学生名单导入与管理 | `daytime_only` | 涉及敏感学生数据批量写入和删除备份契约。 |
| P2-14 | 工作台与分析总览 | `eligible_after_plan` | 只读聚合；禁止臆造 KPI 或新增统计口径。 |
| P2-15 | 知识图谱与证据下钻 v1 | `eligible_after_plan` | 只读图表；仅用生成图数据，不引入 Phase 4 新语义。 |
| P2-16 | 题库管理 | `daytime_only` | 包含题库写入、标签确认和 AI tagging。 |
| P2-17 | 组卷工作台 | `eligible_after_plan` | 草稿和导出可重建；不新增算法或改变导出规则。 |
| P2-18 | 训练推荐工作台 | `daytime_only` | 涉及推荐理由、教师确认和训练任务写入。 |
| P2-19 | 设置、系统自检与运维入口 | `daytime_only` | 包含备份、迁移和数据传输等危险操作。 |
| P2-20 | 新 UI 真实五流程验收 | `daytime_only` | 本包就是授权数据副本上的真实流程、视觉和一致性验收。 |
| P2-21 | FastAPI 静态托管与启动切换 | `daytime_only` | 改变默认启动入口，必须保留并白天验证回退。 |
| P2-22 | Streamlit UI 与旧前端依赖退役 | `daytime_only` | 涉及高风险删除和稳定运行观察期。 |

## Phase 3

| 包 | 标题 | 夜间资格 | 理由与额外门槛 |
|---|---|---|---|
| P3-01 | Phase 3 调用图、覆盖率与性能基线 | `eligible_after_plan` | 只读调查和守卫，不含生产重构或迁移。 |
| P3-02 | 领域数据模型下沉 | `eligible_after_plan` | 兼容别名可回退；不得改变字段、序列化、默认值或评分逻辑。 |
| P3-03 | Repository 契约与事务边界 | `eligible_after_plan` | 只增量引入连接和事务边界；仅用临时库，不迁移 Schema。 |
| P3-04 | Students 与 Sessions Repository | `eligible_after_plan` | 只用临时库；禁止改变学生删除业务规则。 |
| P3-05 | Papers、Results 与 Review Repository | `eligible_after_plan` | 只用临时库且无迁移；评分语义出现歧义必须停机。 |
| P3-06 | Templates、Regions 与 Settings Repository | `eligible_after_plan` | 兼容 facade 可回退；冻结坐标、fingerprint 和 workflow_state。 |
| P3-07 | 服务、API 与报告调用方切换 | `eligible_after_plan` | 不删除 DBManager、不做迁移，可按调用域回退。 |
| P3-08 | 文档解析与本地题块模块 | `eligible_after_plan` | 纯函数迁移，用 fixtures 验证结构等价且不依赖模型。 |
| P3-09 | Prompt 构建与配置生成编排 | `eligible_after_plan` | 仅限 prompt 快照和假 LLM 等价重构；禁止改文案及模型参数。 |
| P3-10 | 配置归一化、评分约束与质量告警 | `daytime_only` | 只允许白天人工监督，且涉及评分约束和 warning 业务含义。 |
| P3-11 | 迁移版本门槛与运行时 DDL 退役 | `daytime_only` | 只允许白天人工监督并涉及最高风险 Schema 迁移。 |
| P3-12 | 状态列约束迁移 | `daytime_only` | 需审计真实 distinct 值、用户确认语义并重建表。 |
| P3-13 | `knowledge_id` 到 `knowledge_ids` 读写切换 | `daytime_only` | 包含数据分布审计、回填 migration 和读写切换。 |
| P3-14 | 旧列清理与 question-bank sync 状态决策 | `daytime_only` | 包含删列迁移和 sync 字段设计裁决，只允许白天人工监督。 |
| P3-15 | 停用向导与断链脚本删除 | `eligible_after_plan` | 仅在静态/动态零调用后删除代码；独立 Git 提交可完整回退。 |
| P3-16 | 旧 CLI、Legacy API 与专属表退役 | `daytime_only` | 需用户确认、旧数据导出和前向删表迁移。 |
| P3-17 | 旧技能语义体系退役 | `daytime_only` | 涉及真实副本审计和 11 张旧表删除，只允许白天人工监督。 |
| P3-18 | 基于证据的性能优化 | `daytime_only` | 需要人工解释实验收益并决定哪些优化合并。 |
| P3-19 | Phase 3 总门槛 | `daytime_only` | 阶段检查点包含迁移矩阵、真实副本和五流程确认。 |

## Phase 4

| 包 | 标题 | 夜间资格 | 理由与额外门槛 |
|---|---|---|---|
| P4-01 | 标签身份、业务口径与评估基线 | `daytime_only` | 需真实数据授权、教师标注和关系语义裁决，只允许白天人工监督。 |
| P4-02 | Tag Relation Schema 与 Repository | `daytime_only` | 涉及题库 Schema 迁移和回滚，只允许白天人工监督。 |
| P4-03 | AI 关系建议 Job 与离线评估 | `daytime_only` | 包含真实模型、gold set 阈值和隐私评估。 |
| P4-04 | 教师关系审核 API 与 UI | `daytime_only` | 教师确认会改变推荐依据，且需流程和视觉验收。 |
| P4-05 | 图谱关系查询服务与 API v2 | `eligible_after_plan` | 只读消费已确认关系；不迁移、不裁决语义、不调用真实模型。 |
| P4-06 | ECharts 图谱 2.0 交互 | `daytime_only` | 涉及复杂视觉、桌面交互和千节点体验验收。 |
| P4-07 | 掌握度 v2 纯函数模型 | `daytime_only` | 时间衰减、收缩和参数含义需要业务裁决与教师样本检查。 |
| P4-08 | 新旧掌握度评估与功能开关 | `daytime_only` | 需要匿名真实样本、教师抽检和生产口径灰度放行。 |
| P4-09 | 真实 Training Attempt 关联与回流 | `daytime_only` | 包含真实训练关联和跨数据库写入补偿，只允许白天人工监督。 |
| P4-10 | Prerequisite 阶段候选与推荐解释 | `daytime_only` | 推荐深度、配额和平衡属于业务语义裁决。 |
| P4-11 | 闭环编排、训练历史与下一轮推荐 | `daytime_only` | 跨服务双库回流、历史 UI 和推荐闭环风险高。 |
| P4-12 | Phase 4 评估与总门槛 | `daytime_only` | 阶段门槛包含真实教师抽检、用户确认和综合评估。 |

## Phase 5

| 包 | 标题 | 夜间资格 | 理由与额外门槛 |
|---|---|---|---|
| P5-01 | 教师命题工作流研究与产品规格 | `daytime_only` | 产品实验需要白天人工监督，需要用户和教师参与裁决业务口径。 |
| P5-02 | 富文本命题编辑与预览技术实验 | `daytime_only` | 技术选型实验需要白天浏览器和视觉候选验证。 |
| P5-03 | 相似题召回评估 | `daytime_only` | 需要教师 gold set、阈值和算法升级决策。 |
| P5-04 | AI 命题评审 Rubric 与评估集 | `daytime_only` | 包含真实模型和教师误报漏报裁决，只允许白天人工监督。 |
| P5-05 | 实验决策与正式设计冻结 | `daytime_only` | 强制设计决策门，必须由用户逐节批准。 |
| P5-06 | Authored Question Schema、状态机与迁移 | `daytime_only` | 涉及高风险 Schema、状态机和迁移回退，只允许白天人工监督。 |
| P5-07 | 草稿 CRUD、自动保存与版本服务 | `daytime_only` | 教师创作数据风险高，需裁决并发覆盖和部分失败恢复。 |
| P5-08 | 相似题检索服务 | `eligible_after_plan` | 只读题库；阈值冻结后仅用匿名测试集，不读取真实 `user_data/`。 |
| P5-09 | AI 评审 Job 与教师逐项决策 | `daytime_only` | 涉及真实 AI、创作数据、教师决策和审计。 |
| P5-10 | 定稿与发布题库事务 | `daytime_only` | 是跨数据库/文件的最高风险发布事务，只允许白天人工监督。 |
| P5-11 | 命题工作台 UI | `daytime_only` | 覆盖危险发布确认、断网恢复和完整教师工作流验收。 |
| P5-12 | 历史、改进记录、预览与导出 | `eligible_after_plan` | 只读历史和可重建导出；语义冻结后仅用 fixtures 自动验收。 |
| P5-13 | 教师试点、评估与 Phase 5 门槛 | `daytime_only` | 阶段门槛要求真实教师内容、模型评估和用户确认。 |

## 历史完成记录

以下 8 条记录形成于正式 87 包体系之前，不参与正式包统计，也不得再次领取。

| 包 | 标题 | 夜间资格 |
|---|---|---|
| P1-01 | FastAPI 骨架、统一错误体、双入口 | `completed_not_applicable` |
| P1-02 | sessions/students 基础只读 API | `completed_not_applicable` |
| P1-03 | session/student 安全写 API | `completed_not_applicable` |
| P1-04 | rubric/answer_key 同步配置 API | `completed_not_applicable` |
| P1-05 | template/regions 草稿与提交 API | `completed_not_applicable` |
| P1-06 | 最小 JobManager、JobStore、Jobs API | `completed_not_applicable` |
| P1-07 | report、scan、grading 三类业务 Job | `completed_not_applicable` |
| P1-08 | review 题目摘要、列表、确认首批 API | `completed_not_applicable` |

## 维护规则

- 普通进度只更新 `EXECUTION_INDEX.md`，不改本矩阵的资格值。
- 包的范围、风险或验收边界实质变化时，必须重新审查对应行。
- 资格变化必须同时更新审查日期、理由、统计和夜间自动化相关文档。
- 夜间自动化不得自行修改本矩阵、提升包状态或生成放行计划。
