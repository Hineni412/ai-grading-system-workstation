# 以判定点为事实源的标签结构与掌握度重设计方案

- 文档用途：实施方案。供后续实施代理按阶段执行。
- 状态：原始设计稿，已不能用“全部未实现”描述当前代码。链接独立存储、技能身份、考试冻结证据、归属派生及推荐读取已经有实现；当前行为以 `docs/product/KNOWLEDGE_AND_TRAINING.md` 为准。本稿中的“现状”仅指 2026-09-16 的调查，不能作为今天的运行状态。
- 后续修订边界：用户已要求以可读、可观察的教学操作替换不合格聚类技能，并同意知识主题与技能关系视图、保留原约束的四级匹配。v6 候选标准及副本修复工具已具备；真实题库是否启用以数据库活动发布包为准，不把候选文件存在视为已经上线。
- 用户已确认的决定（2026-09-16）：
  1. 接受"学科网知识叶子降为情境标签、新增技能层、掌握度过渡期按小节汇总"的方向；技能候选清单由聚类自动产生，**用户已声明直接通过、不做人工审定**。
  2. 同意考试侧三条硬规则：评分步骤只引用不重写、考试冻结自带知识链接的证据快照、对位按 id 不按文字。
  3. 补链接走方案 B：知识链接从证据正文中拆出，单独成表，可独立再生成。
  4. 归属冗余字段改为派生写入，并入 B 之后的阶段，不单独提前清理。
- 前提约束：判定点回填任务（`backend/jobs/criterion_backfill.py`）是用户既定的批改改进方向，本方案**不改变判定点正文的生成方式和内容契约**，只修复其候选表遗漏（第 6 阶段），并把知识链接职责移出判定点正文。

---

## 1. 背景与已核实的现状

### 1.1 起因

第三周学情反馈（考试会话 4，涉及八上第二章）批改完成后，知识图谱第二章掌握度未更新。只读复现结论：14 个评分题 0 个进入图谱。原因是图谱投影对有小问评估资料的题改用"评分依据步骤 ↔ 题库证据点"逐字对位（`integration/question_tag_projection_service.py` 第 110–116 行调用 `match_rubric_parts`），对位失败即整题作废，且不回退到整题知识点标签。评分依据与题库证据分别由不同时间、不同模型生成，措辞与步骤数不同，必然对不上。

### 1.2 一道题现有的三层资料（现状）

| 层 | 存储 | 内容 |
|---|---|---|
| A. 整题标签 | `question_tags`（`tag_type` 列区分维度）；`questions.difficulty` | `knowledge_point`（层级路径，平均 3.4 个/题）、`ability`、`method`、`thought`、`model`、`special_type`、`error_type`（1242 个不同值，自由文本）、`prerequisite`、`exam_scope`（章）、`curriculum_section`（节 id）、`canonical_knowledge_id`；整题难度 1–10 |
| B. 判定资料/解题证据 | `question_solution_evidence_versions.evidence_json`；`training_criterion_versions.criteria_json`（含 `solution_evidence` 副本与去分值 `points`） | 小问 → 证据点（`evidence_point_id`、`step_index`、`target`、`justification`、`answer_anchor`、`observable_evidence`、`depends_on`、`equivalent_rules`、`counterexamples`、`fine_term_links[{fine_term_id, fine_term_name, role∈{direct, supporting_prerequisite}, core_resolution{status, stable_keys}}]`） |
| C. 小问评估 | `question_part_assessment_profiles` | 每小问难度 1–10 + 理由 |

数据事实（2026-09-16 只读统计）：
- 题库 1434 题，**全部**有证据版本；最新版本合计 5116 个证据点，其中 2360 个带知识链接，其余为空。
- 今天回填任务产出的 203 个新版本，证据点链接**全部为空**（见 1.4）。
- 知识图谱当前发布 `kgr_bnu_math_curriculum_2026_08_v2`；八上 259 个节点（章 8 / 节 28 / 叶 223）。叶子是学科网爬取的"情境/题型"（如"求旗杆高度""求河宽""求梯子滑落高度"），不是技能。
- 历史链接中 78% 的题所有步骤只指向同一个知识点；步骤级归因在现有叶子上几乎没有分辨率。
- 难度分布：1–3 占 52%，9 分 10 题，10 分 0 题。32 道已关联考题上"估计难度 vs 班级得分率"Pearson r ≈ −0.51。

### 1.3 现有词表治理机制（现状，本方案复用）

- 每题模型候选表由 `question_bank/taxonomy/governance.py` `prompt_contracts()`（第 1683 行起）生成：知识维度候选 = 该册及之前各册全部课程节点（`question_bank/taxonomy/curriculum_catalog.py` `eligible_curriculum_knowledge_nodes`，第 426 行），来源 `xkw_bnu_curriculum_2026_08_05` 或 `teacher`。
- 模型返回受 JSON schema 枚举约束（`question_bank/training_criteria/analysis.py` `_solution_evidence_schema`，第 3043 行起；`fine_term_id` 为候选 id 枚举）。
- 提示词规定链接只能照抄候选 id+name，无匹配返回空数组，新词只进 `tag_analysis.proposed_tags`（`question_bank/training_criteria/adapters.py` 第 772–777 行）。
- 本地复核：`converge_evidence_terms`；对不上的链接剥离进 `unresolved_links` 审计（`question_bank/training_criteria/in_memory.py` `_strip_unverified_evidence_links`，第 3293 行）。新词审核表 `taxonomy_review_applications`。
- 每个词带 `aliases / legacy_names / retrieval_hints`。
- 层级上溯工具：`curriculum_knowledge_ancestors(knowledge_id)`（`curriculum_catalog.py` 第 442 行）。

### 1.4 回填任务链接为空的原因（现状，已定位）

导入入库路径两遍加载：先加载上下文，`ai_service.taxonomy_contracts()` 生成候选表，再带候选表加载（`backend/jobs/question_bank_sync.py` 第 972–986 行）。回填任务只做了第一遍，`loader.load((question_id,))` 未传 `taxonomy_contracts`（`backend/jobs/criterion_backfill.py` 第 65–71 行）。实测 `QuestionAnalysisInputLoader` 对题 1476 返回的 `taxonomy_contract` 为空字典。模型按提示词规则返回空链接。

### 1.5 链接为空的下游后果（现状）

- 个性化推荐：候选题知识身份来自可用判定版本的证据点直接链接（`question_bank/recommendation/personalized.py` `_question_evidence_metadata`，约第 1669–1681 行）。链接为空 → 题目退出候选池。实测题 1476、1432 新版本返回空 `stable_keys`，旧版本分别返回 2 个。
- 考试→图谱：即使对位成功，无直接目标也标记 `part_direct_knowledge_missing`，不计入。
- 训练→图谱：退回整题粗归因。

### 1.6 考试评分依据与题库证据的关系（现状）

- 系统已有从题库证据生成评分骨架的函数 `rubric_skeleton_from_solution_evidence`（`analysis.py` 第 2555–2603 行）：`step_id = evidence_point_id`，`allocation_status: "unassigned"`，记录 `source_evidence_version_id`。
- 会话 4 的评分依据文件（`user_data/config/uploaded/rubric_editor-1c0b64fb….json`）Q13：带 `source_evidence_version_id = 40f3c5aa…`，但步骤 id 已变为 `S1…S4`（题库为 6 个 `part-1-step-N`），且该版本 id **在题库 `question_solution_evidence_versions` 中不存在**（配置考试时的临时分析未落库）。
- 对位函数 `match_rubric_parts` 要求小问数、作答形式、步骤数、目标文字、要求要素、等价规则、反例集合全部一致。

### 1.7 现有掌握度与推荐（现状）

- `question_bank/mastery/current.py`：`MasteryV2Parameters`（公式 `mastery-v2-formula-v2`，先验均值 0.65、强度 2.0；考试权重 1.0 半衰期 180 天，训练权重 0.7 半衰期 90 天；难度非对称加权）。证据键为稳定图谱键（`kp_…`）。
- 训练侧已按证据点计证据：前置点未达成时忽略依赖点的失败；每点直接键按份额分配权重。
- 考试侧止步于小问：小问得分率 × 该小问全部直接目标并集；批改结果 `raw_json` 中实际已有每步达成状态（`S1/S2/S3` full/partial/none），未被使用。
- 推荐实际在用的是 `personalized.py`（`scoring.py` 一套前端无调用，属遗留）。逻辑：只对有实际失分的知识点出题；目标难度 = 失分小问难度 − 0~1，候选取 [目标−1, 目标]，上限 7；候选必须直接考查该知识点；错因靠扣分说明关键词匹配训练任务；不足时用范围内相近题补充并标记"补充"。
- 题库筛选 `QuestionReadFilters`（`question_bank/services/question_read_service.py`）：知识点为 `LIKE` 任一命中；`exam_scopes`、`curriculum_sections` 为面板筛选项；"按教学进度"过滤读 `knowledge_point`/`prerequisite` 前缀与 `exam_scope` 值集合（第 2393–2415 行）。`canonical_knowledge_id` 已在 `_RETIRED_PUBLIC_TAG_TYPES`（第 392 行）退役但模型仍在写。

---

## 2. 目标与非目标

### 目标
1. 判定点（证据点）成为知识归因的唯一事实源；整题知识标签、章/节归属由其派生。
2. 知识标准增加"技能层"，掌握度挂技能与小节，不挂情境叶子。
3. 知识链接与判定点正文解耦，可独立再生成，判定点正文与已有考试引用不受词表变化影响。
4. 考试评分步骤引用题库证据点 id，考试配置冻结自带链接的证据快照，图谱投影按 id 归因到步骤级。
5. 题库筛选支持"严格练某章"与"主要练某章"两种语义。
6. 推荐与掌握度改为读取新链接表与技能键；算法框架不推翻。

### 非目标
- 不改变判定点正文的生成提示词、结构契约、版本/审核流程（回填任务只修候选表遗漏）。
- 不重写任何已完成考试的成绩、排名、报告、教师确认分。
- 不改难度标尺定义与掌握度公式参数（只增加校准报表，见 §9）。
- 不新增安全/权限机制。
- 不为技能层做人工审定流程（用户已声明直接通过自动聚类结果）。

---

## 3. 设计 A：知识标准增加技能层

### 3.1 结构
- 在现有 章(1)/节(2)/叶(3) 之外新增一类节点 **技能（skill）**，父节点为**节**，与情境叶子并列。
- 现有学科网叶子保留，语义降为**情境标签**：只用于筛选、组卷的情境多样性和展示；**不再作为掌握度证据键、不再作为推荐的知识身份**。
- 稳定键格式：`sk_<volume>_<chapter>_<section>_<nn>`，例如 `sk_bnu24_math_g8_upper_1_1_03`。实施前须核对 `question_bank/mastery/v2.py` 稳定键格式校验与 `question_bank/current_knowledge.py` 解析器是否接受 `sk_` 前缀；不接受则扩展校验，不得借用 `kp_` 前缀混用。
- 每个技能字段：`id`、`name`（≤12 字，动词+对象，如"确认直角前提""合并同类二次根式"）、`section_id`（唯一父节）、`aliases[]`、`retrieval_hints[]`（聚类簇的代表步骤原文，≤5 条）、`origin = "skill_cluster_2026_09"`、`status = active`。
- 每章技能数量目标：每节 3–8 个，每章不超过 30 个。客观题"作答结果"点不产生技能（见 3.2 第 3 步）。
- 关系：技能只能属于一个节。跨章使用一律通过链接的 `supporting_prerequisite` 角色引用，不复制节点。

### 3.2 技能候选清单生成（实施代理执行，本地计算，不调模型）

数据源：`question_solution_evidence_versions` 每题最新版本的全部证据点（5116 条），连带题的 `exam_scope`、`curriculum_section`、`question_type`、小问 `response_mode`。

步骤：
1. 归一化 `target` 文本：去除数字、拉丁字母、根号/平方/角度等符号、括号及其内数值、标点；保留中文。
2. 特征：字符 2/3/4 元组集合 + 术语词典命中（词典由实施代理从各章高频 n 元组中整理，需覆盖至少：勾股定理、直角三角形、逆定理、斜边、直角边、面积、最短路径、展开图、折叠、平方根、立方根、算术平方根、无理数、实数、估算、二次根式、最简二次根式、同类二次根式、分母有理化、绝对值、零指数幂、负整数指数幂、坐标、象限、对称点、距离、一次函数、解析式、待定系数、截距、交点、方程组、代入消元、加减消元、平均数、中位数、众数、方差、证明、平行、内角和、外角）。
3. 分桶：`response_mode == exact_objective` 且 `target` 匹配 `^作答为|^给出.*答案|^选择` 的点归入固定桶 `answer_result`，不参与聚类，也不产生技能。
4. 按章分别聚类：两点相似度 = 术语词典命中集合 Jaccard × 0.6 + 三元组 Jaccard × 0.4；凝聚式合并，阈值 0.45；簇大小 < 8 的并入最近簇，仍不足则归 `misc_<chapter>` 桶供后续人工处理。
5. 簇内命名：取簇中出现频次最高的"动词 + 术语"组合为 `name`；动词表：确认/明确/设/表示/列/解/求/化简/合并/代入/判定/证明/比较/估算/作/写出/读出/联立/展开/验证。
6. 节归属：簇内步骤所属题的 `curriculum_section` 众数；若众数占比 < 50%，归入该章"综合应用"节的技能（若该章无此节，挂在章下第一节并在 `aliases` 备注）。
7. 输出 `output/skill_layer_design/skills_<volume>.json`：每技能含 id、name、section_id、count、sample_targets[5]、aliases（簇内次高频动词+术语组合）。同时输出覆盖率报表：进入技能簇的非 answer 点占比（验收 ≥ 85%），`misc` 桶清单。
8. 用户已声明直接通过；实施代理将结果直接写入知识标准（3.3），不设审核门。`misc` 桶的点在链接阶段允许链接到节级键（见 4.3）。

### 3.3 发布
- 以现有知识图谱发布机制新建发布 `kgr_bnu_math_curriculum_2026_09_v3`：包含 v2 全部节点 + 技能节点 + 技能→节的 `parent` 关系。
- 词表目录 `question_bank/taxonomy/catalogs/` 增加技能条目（`dimension = knowledge`，`origin = skill_cluster_2026_09`），使 `prompt_contracts()` 能把技能纳入候选。候选表中的知识维度按 3.4 调整。
- 发布后，v2 键继续可解析（掌握度历史证据不失效）。

### 3.4 候选表调整
- 知识维度候选 = 本题所在章的技能 + 之前各册全部技能（`supporting_prerequisite` 用）+ 本册全部节节点；学科网叶子在链接任务中标记 `usage = retrieval_only`（只用于检索提示，不可作为链接目标）。整题标签分析（`tag_analysis.knowledge_points`）仍可使用叶子，作为情境标签。

### 3.5 防发散规则（固化为代码校验，不靠人工）
- 模型不能新增技能：链接任务的 `fine_term_id` 枚举只含发布内的技能与节键。
- 新技能只能通过 `taxonomy_review_applications` 审核流程进入，来源标记 `teacher`。
- 例行报表（只读，随链接任务结束输出）：挂题 < 5 道的技能、两技能共现率 > 90% 的技能对，列为合并候选。不自动合并。

---

## 4. 设计 B：知识链接独立成表与补链接任务

### 4.1 新表 `evidence_point_knowledge_links`（题库库）

| 列 | 说明 |
|---|---|
| `evidence_version_id` | 引用 `question_solution_evidence_versions.evidence_version_id` |
| `question_id` | 冗余，便于查询 |
| `part_id` | 小问 id |
| `evidence_point_id` | 证据点 id |
| `graph_release_id` | 链接所依据的图谱发布 |
| `role` | `direct` / `supporting_prerequisite` |
| `term_id` | 词表 id（技能或节） |
| `stable_key` | 解析后的稳定键 |
| `resolution_status` | `resolved` / `unresolved` |
| `weight` | 默认 1.0；同一点多个 direct 链接时平分（写入时归一，读侧不再计算） |
| `source_kind` | `link_job` / `migrated_from_embedded` / `teacher` |
| `source_reference` | 任务 id 或来源版本 |
| `created_at` | |

主键：(`evidence_version_id`, `evidence_point_id`, `graph_release_id`, `role`, `term_id`)。

读侧统一入口：新增 `question_bank/solution_evidence/knowledge_links.py`，提供
`load_point_links(db, evidence_version_ids, graph_release_id) -> {version_id: {point_id: [Link]}}`
与 `direct_targets_for_part(part, links)`；`part_assessments.direct_targets()` 改为委托该入口，**不再读证据正文里的 `fine_term_links`**。

### 4.2 迁移已有嵌入链接
- 一次性把 2360 条已嵌入的 `fine_term_links`（`core_resolution.status == resolved`）复制进新表，`source_kind = migrated_from_embedded`，`graph_release_id = 原发布(v2)`。这保证切换读侧后现有可用数据不倒退。
- 证据正文不删改（版本不可变）。后续新生成的证据正文中若仍带 `fine_term_links`，写入时同步投影到新表一份（保持单一读口）。

### 4.3 补链接任务（模型任务，需费用授权）
- 入口：新后台任务 `backend/jobs/knowledge_link_job.py`，参数：`question_ids` 或 `全库`、`graph_release_id`（默认当前发布）、`mode = missing_only | regenerate`。
- 输入：每题最新可用证据版本的证据点（`target`、`observable_evidence`、`justification`、`depends_on`）+ 3.4 的候选表（技能 + 节；叶子仅 retrieval_only）。不发送整题标签、不发送分值。
- 输出 schema：`[{part_id, evidence_point_id, links: [{fine_term_id(枚举), fine_term_name, role}]}]`，`additionalProperties: false`；每点 direct 链接 1–3 条；`answer_result` 桶的点允许链接到节键或该题主技能。
- 本地复核复用 `converge_evidence_terms` 的解析与 `unresolved_links` 审计；`misc` 桶或无法匹配技能的点允许退到**节**键（`kp_…_<chapter>_<section>`），不允许退到章键，不允许为空。
- 幂等键：(`evidence_version_id`, `graph_release_id`)；`missing_only` 模式跳过已有记录。
- 每次调用可打包 5–10 题；全库初次约 1434 题，费用需用户按 AGENTS.md 单独授权。
- 完成后输出 3.5 的例行报表。

### 4.4 与回填任务的关系
- 回填任务继续只负责判定点正文；证据版本变更（新版本成为可用版本）后，由链接任务的 `missing_only` 模式补链接。建议在回填完成回调里排队一次 `missing_only` 链接任务，而不是让回填自己链接。
- 第 6 阶段修复回填候选表遗漏（让正文生成时也能得到候选），其产出的嵌入链接按 4.2 同步投影到新表；两条来源并存时以 `link_job` 为准。

---

## 5. 设计 C：归属字段派生写入

- `exam_scope`（章）、`curriculum_section`（节）在题库写服务中改为由该题当前可用证据版本的 direct 链接上溯（`curriculum_knowledge_ancestors`）得到；写入 `question_tags` 同名 `tag_type`，**筛选、面板、教学进度过滤不改**。
- 模型侧：`tag_analysis` 响应格式中移除 `canonical_knowledge_id`、`textbook_chapters`、`curriculum_sections` 的必填要求，改为可选；服务端忽略其值。`canonical_knowledge_id` 停写（读侧已退役）。
- 无链接的题（链接任务失败/未跑）保留模型原值并加 `tag_status = derived_pending` 标记，报表可见。
- 写入时对同一 (`question_id`, `tag_type`, `tag_value`) 去重；现有 131 处重复在重写时自然消除。
- 整题 `knowledge_point` 标签保留为情境标签，同时追加派生值：该题全部 direct 技能键（便于列表页展示"考查技能"）。

---

## 6. 设计 D：掌握度按技能与步骤计算

### 6.1 证据键
- 新证据一律以技能稳定键（`sk_…`）或节键为键；情境叶子键不再产生新证据。历史 `kp_…` 叶子证据保留，展示时上溯到节。
- 过渡期（技能层发布后、链接任务全库完成前）：图谱页面按**节**汇总展示；节值 = 该节下技能与叶子证据的加权合并（沿用 v2 公式，对键做上溯后再聚合）。

### 6.2 训练侧
- 现有按证据点计证据的逻辑不变，直接键改从 4.1 新表读取；权重使用表中 `weight`。
- 依赖规则不变：`depends_on` 中任一前置点未达成时，本点未达成不计负证据。

### 6.3 考试侧（依赖 §7）
- 评分结果中每个评分步骤的达成状态（full/partial/none）映射到其覆盖的证据点 id（§7.1 一对多），每个证据点得到 achieved ∈ {1, 0.5, 0}；`partial` 仅在步骤覆盖单个点时取 0.5，覆盖多点时按 `awarded/step_score` 比例赋给所有覆盖点。
- 证据点 → 知识键：读考试冻结快照中的链接（§7.2），不读题库当前版本。
- 依赖规则与训练侧相同。
- 来源权重、半衰期、难度非对称权重沿用 `MasteryV2Parameters`；小问难度取快照中的小问评估。
- `supporting_prerequisite` 链接不进入掌握度，只作为推荐上下文（与现状一致，不改）。

### 6.4 图谱投影
- `QuestionTagProjectionService.is_graph_eligible` 改为：有快照且快照中该题至少一个证据点有 resolved 链接。
- 无快照的旧会话（2、3、4）：走 §7.4 迁移；迁移失败的题退回整题 `knowledge_point` 标签上溯到节键（不再整题作废）。这是对旧会话的兜底，不是新流程。

---

## 7. 设计 E：考试评分依据引用题库证据点

### 7.1 评分步骤引用而非重写
- 评分依据 JSON 中每个 step 新增 `evidence_point_ids: [..]`（一对多，允许一个评分步骤合并多个证据点；同一证据点不得出现在多个步骤）。
- `rubric_skeleton_from_solution_evidence` 生成的步骤默认 `evidence_point_ids = [evidence_point_id]`；赋分阶段（模型或教师）可以合并步骤，合并时合并 id 列表，不得删除 id。
- 评分依据编辑器：`step_id` 与 `evidence_point_ids` 只读；允许改 `core_goal / required_elements / step_score / equivalent_rules` 文字。校验：全部证据点 id 恰好被覆盖一次（`allow_alternative_methods` 小问除外，允许未覆盖点，但需显式列在 part 级 `uncovered_evidence_point_ids`）。

### 7.2 考试冻结证据快照
- 配置考试选题关联题库题后，把该题**当前可用证据版本**的完整正文 + 该版本在当前图谱发布下的全部链接（来自 4.1）+ 小问评估，整份写入考试配置目录（`user_data/config/uploaded/evidence_snapshot-<session>.json`），并在评分依据的题级记录 `evidence_snapshot_ref`、`source_evidence_version_id`、`graph_release_id`。
- 若配置考试时题库尚无可用证据版本，配置流程先触发题库分析并落库（不再使用不落库的临时版本），再冻结。
- 题库后续任何版本变更不触及快照。

### 7.3 对位按 id
- 图谱投影与掌握度读取只使用 `evidence_point_ids` 与快照；删除对 `match_rubric_parts` 的调用（函数可保留供质量报表使用）。

### 7.4 旧会话迁移（会话 2、3、4）
- 提供一次性工具 `update_tools/build_legacy_evidence_snapshot.py`：对每个已关联题库题的评分题，读当前可用证据版本与链接生成快照，并用 `match_rubric_parts` 尝试自动建立 `evidence_point_ids`；对位失败的题输出人工对照表（评分步骤 ↔ 证据点），由用户在评分依据编辑器中手工勾选后保存。
- 工具默认只读预演并输出报告；实际写入评分依据文件属于"改写已有真实数据"，须用户单独授权。

---

## 8. 设计 F：题库筛选的范围语义

新增筛选参数 `scope_mode`，配合现有 `exam_scopes / curriculum_sections / knowledge_points`：

| 模式 | 定义（基于 4.1 direct 链接上溯） |
|---|---|
| `strict`（严格练本章） | 该题**全部** direct 链接所属节 ⊆ 所选章（或所选节）；`supporting_prerequisite` 只允许来自之前各册 |
| `primary`（主要练本章） | 该题的主技能所属节 ∈ 所选章；主技能 = direct 链接 `weight` 合计最高的技能；其余 direct 链接可跨章 |
| `any`（现状） | 任一知识标签命中 |

- 列表页与组卷助手（`question_bank/services/assembly_assistant.py`）默认 `primary`；个性化推荐候选范围默认 `strict`（与现有"已学范围"约束叠加）。
- 实现为 SQL：预计算表/视图 `question_scope_summary(question_id, primary_section_id, direct_section_ids_json, has_cross_chapter_direct)` 在链接写入时更新，避免筛选时逐题解析。

---

## 9. 设计 G：推荐与难度

- 推荐候选知识身份改读 4.1 新表（`_question_evidence_metadata` 的输入替换），匹配粒度为技能；情境多样性用叶子标签做卷内去重（同一技能下优先不同情境）。
- 目标难度与窄带逻辑不改。新增只读**难度校准报表**（`tools/difficulty_calibration_report.py`）：按题输出估计难度、参与班级数、得分率、残差；残差 |z| > 1.5 的题列为复核候选。不自动改难度。
- 错因维度暂不重构（超出本方案范围）；建议后续把 `error_type` 收敛为受控词表，另行立项。
- 推荐有效性回看（只读）：训练卷发布后 N 天，比较被推荐技能在后续证据中的掌握度变化；作为报表，不进算法。

---

## 10. 实施阶段、验收与授权

各阶段可独立提交；每阶段完成后更新对应权威文档（`docs/product/KNOWLEDGE_AND_TRAINING.md`、`CONTEXT.md` 词汇、`ARCHITECTURE.md` 数据契约），不新增并行说明文档。

### 阶段 1：技能候选清单（无费用，只读题库）
- 产出：`output/skill_layer_design/skills_*.json` + 覆盖率报表。
- 验收：非 answer 点进入技能簇 ≥ 85%；每章技能 ≤ 30；每技能 `section_id` 唯一。

### 阶段 2：技能层入知识标准与发布 v3（写题库知识图谱表，需授权）
- 产出：新发布、词表目录条目、候选表调整、`sk_` 键校验通过。
- 验收：`CurrentKnowledgeResolver` 能解析 `sk_` 键；v2 键仍可解析；现有掌握度页面在合成数据下按节汇总展示；测试 `tests/` 下新增稳定键与候选表用例。

### 阶段 3：链接表 + 迁移 + 链接任务（建表与迁移需授权；模型任务需费用授权）
- 产出：4.1 表、读侧入口、迁移脚本、后台任务、报表。
- 验收：迁移后 `direct_targets()` 对全部已链接题结果与迁移前一致（合成+真实只读对比）；链接任务在模型替身下对合成题产出合法链接并被 `unresolved_links` 审计捕获非法 id；`missing_only` 幂等。
- 真实全库运行前给出题数与预计调用次数，等待授权。

### 阶段 4：归属字段派生写入（写题库 `question_tags`，需授权）
- 验收：列表页章/节筛选结果在派生前后对比，差异逐题列出并说明（应仅为原模型归属错误的题）；`canonical_knowledge_id` 不再写入；重复值为 0。

### 阶段 5：考试侧三条硬规则（改考试配置生成、编辑器、投影、掌握度考试路径）
- 验收：合成会话配置后评分依据含 `evidence_point_ids` 与快照引用；编辑器改文字后 id 不变；模拟批改结果按步骤落到技能键；题库替换证据版本后该会话掌握度不变；旧会话迁移工具在只读模式对会话 2/3/4 输出对照报告。
- 对旧会话评分依据的实际写入须用户逐次授权。

### 阶段 6：回填任务候选表修复
- 改动：`criterion_backfill.py` 采用两遍加载并传 `taxonomy_contracts`（对齐 `question_bank_sync.py` 第 972–986 行）。
- 验收：合成题在模型替身下回填产出的证据含链接，并同步投影到 4.1 表。不重跑已完成的 203 题（其链接由阶段 3 补）。

### 阶段 7：筛选范围语义与推荐读侧切换
- 验收：`strict/primary/any` 三种模式在合成题库上的集合关系正确（strict ⊆ primary ⊆ any）；推荐候选池中先前因空链接消失的题（如 1476、1432）重新出现；推荐卷内同技能不同情境的去重生效。

### 阶段 8：校准与有效性报表（只读）
- 验收：报表可对真实库运行且不写入。

---

## 11. 费用与授权清单

| 动作 | 类型 | 授权 |
|---|---|---|
| 聚类、报表、只读预演 | 本地只读 | 自动允许 |
| 建新表、迁移嵌入链接、写发布 v3、派生写 `question_tags` | 直接写真实题库 | 每次单独授权 |
| 链接任务全库运行（约 1434 题） | 模型费用 | 单独授权，先报预计次数 |
| 旧会话评分依据写入 `evidence_point_ids` / 快照 | 改写已有真实数据 | 每个会话单独授权 |
| 重启后台服务 | 需确认无真实批改/模型任务 | 自动允许 |

---

## 12. 尚未决定的事项

1. `partial` 达成覆盖多证据点时的分配比例（§6.3 给了默认方案：按分值比例）。
2. `allow_alternative_methods = true` 小问在学生用其他方法时，未覆盖证据点如何计证据（默认：不计，不作负证据）。
3. 旧会话 2/3/4 对位失败的题，是否接受"整题标签上溯到节"作为最终兜底，还是要求手工对照后再计入。
4. 技能层是否覆盖七下各章（题库中七下题约 150 道）：默认覆盖，用同一聚类流程。
5. `error_type` 受控化是否另立方案。

---

## 13. 关键代码位置索引（现状，供实施代理定位）

- 图谱投影与对位：`integration/question_tag_projection_service.py`（第 110–116 行）；`question_bank/solution_evidence/part_assessments.py`（`direct_targets`、`match_rubric_parts`、`load_profiles`）
- 证据存储：`question_bank/solution_evidence/repository.py`、`contracts.py`、`normalization.py`（链接规范化第 390–434 行）
- 评分骨架：`question_bank/training_criteria/analysis.py`（`rubric_skeleton_from_solution_evidence` 第 2555 行；`training_criteria_from_solution_evidence` 第 2401 行；`_solution_evidence_schema` 第 3043 行）
- 组合分析与收敛：`question_bank/training_criteria/in_memory.py`（第 3120–3259 行；`_strip_unverified_evidence_links` 第 3293 行）
- 提示词：`question_bank/training_criteria/adapters.py`（第 738–824 行；`_prompt_candidate_contract` 第 679 行；`QuestionAnalysisInputLoader.load` 第 404 行）
- 候选表：`question_bank/taxonomy/governance.py`（`prompt_contracts` 第 1683 行起）；`question_bank/taxonomy/curriculum_catalog.py`（第 426–454 行）
- 回填任务：`backend/jobs/criterion_backfill.py`（第 65–71 行）；对照 `backend/jobs/question_bank_sync.py`（第 967–986 行）
- 掌握度：`question_bank/mastery/current.py`、`question_bank/mastery/v2.py`
- 推荐：`question_bank/recommendation/personalized.py`（`_question_evidence_metadata`；候选准备约第 1660–1690 行）
- 筛选：`question_bank/services/question_read_service.py`（`QuestionReadFilters` 第 512 行起；教学进度过滤第 2393–2415 行；`_RETIRED_PUBLIC_TAG_TYPES` 第 392 行）
- 标签契约：`question_bank/models/tag_schema.py`；`question_bank/services/ai_tagging_service.py`（`taxonomy_contracts` 第 219 行；受控字段校验第 990–1018 行）
- 产品文档：`docs/product/KNOWLEDGE_AND_TRAINING.md`、`docs/product/GRADING.md`
