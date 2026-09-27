# 八上题库标签维度调整与重打标签方案

用途：交给独立实施窗口执行八上批量重打的方案。第 5 节的入库程序修改**已在工作区完成（未提交）**，并已在题库副本上试打 3 道题；批量重打**尚未执行**，真实题库**尚未改动**。批量完成后按 `AGENTS.md` 更新对应权威文档（`docs/product/KNOWLEDGE_AND_TRAINING.md` 等），本文不作为已实现事实。

前置阅读：`AGENTS.md`、`ARCHITECTURE.md`、`docs/product/KNOWLEDGE_AND_TRAINING.md`（七维标签治理、详细判定点底座）、`docs/requests/2026-09-16-criterion-based-tags-and-mastery-redesign.md`（判定点关联表与补链接任务的来历）、`docs/requests/错因体系改造方案.md`（典型错法，与 3.10 重叠）。

## 1. 目标与范围

- 目标：按用户已确认的维度调整，对**八年级上册**的题用同一个模型统一重打标签，消除多模型混打造成的口径不一。
- 范围：只做八上。实施开始时冻结题目清单：`questions.is_deleted=0` 且存在 `question_tags.tag_type='exam_scope'` 且值以“八年级上册”开头的题（2026-09-25 只读统计为 1276 道，其中选择 523、填空 311、解答 442，带图 621）。清单写入实施产物，之后范围标签即使因重挂而变化也不改变本次清单。程序上线前新入库的八上题也补进清单。
- 非目标：不重做判定点正文；不改考试评分、教师最终分锁、已冻结的会话与训练快照；七年级题本期不重打。

## 2. 已确认的维度调整

| 编号 | 维度 | 决定 |
|---|---|---|
| 2.1 | 知识点 | **挂法 B（判定点关联）为唯一来源**；整题打标签不再输出知识点。题目知识点、章、小节由各判定点的 direct 关联汇总派生。词表不变 |
| 2.2 | 判定点关联规则（收紧） | 只挂该步骤解题**必须**用到的知识；第一章即可完成的操作（如完全平方数开方）不挂到后续章节；supporting_prerequisite 不得来自比该题主考章节更晚的章节；更早册别只能作 supporting_prerequisite |
| 2.3 | 教材归属 | 主章节只在能落到小节的 direct 关联中选；只属于章的技能（如综合与实践章“运用题设新定义”）不决定考试范围 |
| 2.4 | 前置知识 | **由判定点关联的 supporting_prerequisite 汇总得出**；关联候选已补入七上、七下各小节（往届精确到小节，本册仍细到技能）。整题打标签请求不再附带知识点目录 |
| 2.5 | 能力 | 只标主要考查的 1–2 项；删除“阅读理解”（由难度特征中的情境类型表达） |
| 2.6 | 数学思想 | 删除“推理与证明思想”“数学建模思想”“统计推断思想”“程序化思想” |
| 2.7 | 数学模型 | 只留具体可识别的模型；删除“函数模型”“方程模型”“不等式模型”“统计推断模型”。其余是否属于宽泛项，由实施方列清单请用户确认 |
| 2.8 | 解题方法 | 保留 |
| 2.9 | 特殊题型 | 解答题子类（计算/画图/证明）由模型重判，替代早期迁移来源 `question_type_migration`；教师确认过的题型和手动子类不改；考法新增“综合与实践” |
| 2.10 | 易错点 `error_type` | 不再生成；改为“预测典型错法”（7 大类 + 具体错法，最多 3 条），`source='ai_predicted'`，仅预测，不作学生证据 |
| 2.11 | `canonical_knowledge_id`、`typicality`、适合学生层次、教学阶段 | 删除或清理残留 |
| 2.12 | 人工复核 | **取消置信度门槛**，模型一次判定直接写入，置信度只记录。返回缺项自动重问一次，仍失败记为失败待重跑。唯一需要人看的是“词表缺口清单”：判定点找不到合适知识点、或模型提出新词 |

## 3. 难度（已确认）

- 模型按小问给特征：solo 1–4、reasoning 0–2、computation 0–2、context 0–2（另存情境类别）、hidden 0–2、cases 0–2、param_dynamic 0–1、trap 0–1、knowledge 0–2，再给一句依据 evidence。直接打分 direct 已于 2026-09-26 取消，难度只按公式；数据库 direct_difficulty 列保留，新记录留空，旧值不读取。
- 小问公式难度 `= 1 + 9 × raw ÷ 13`，`raw = (solo−1)×1.0 + reasoning×1.0 + computation×0.75 + context×0.5 + hidden×1.0 + cases×0.75 + param_dynamic×0.5 + trap×0.5 + knowledge×0.5`，结果 1.0–10.0，保留一位小数。
- **整题难度 = 最难小问的公式分，保留一位小数（如 `8.8`），不取整、不换算，覆盖 `questions.difficulty`。** 特征与公式版本另存 `question_part_difficulty_features` 表。
- 比较（筛选区间、难度上限、组卷“目标 ±1”、远近排序）直接用小数，两端包含；筛选滑块按 0.5 调节。
- 分类（推荐阶段、起步/巩固/拓展、组卷难度分布、滑块分段标签）按半档归入最近整数档：`floor(d + 0.5)`，即 7 级 = 6.5–7.4。后端 `standard_difficulty.difficulty_level`，前端 `lib/utils.ts` 的 `difficultyLevel`。原有整数难度结果不变。
- 标尺说明已改为 `junior-full-range-2026-09-v2`（`question_bank/models/tag_schema.py`），9–10 用于真正的压轴题。
- 已决定（2026-09-26）：维持分母 13，不“拉长顶端”。分母 13 恰好对应全部特征取满时的 10 分；调小分母会整体上调所有题，只拉顶端则属于为区分而区分。

## 4. 当前程序状态（2026-09-25，未提交）

- 所有入库方式（题库上传 Word/PDF、考试配置同步、补判定点与重新分析、补挂知识点）共用的核心已改：`question_bank/training_criteria/analysis.py`、`adapters.py`、`question_bank/services/ai_tagging_service.py`、`question_write_service.py`、`standard_difficulty.py`、`predicted_error_patterns.py`、`question_bank/solution_evidence/knowledge_links.py`、`backend/jobs/knowledge_link_job.py`、`question_bank/taxonomy/curriculum_catalog.py`、`question_bank/knowledge_graph_release/loader.py`（修订 9 → `knowledge_graph_release_v7.json`、`tag_vocabulary_v8.json`）；难度小数的读取方见 `question_read_service.py`、`recommendation/personalized.py`、`ai_assembly_service.py` 与对应接口和前端解码。
- 请求大小：整题打标签每题约 1.5–2.7 万字符（原约 16 万）；判定点关联请求（3 题合发）约 48 万字符（原约 37 万，因补入七年级小节）。
- 新表建表脚本暂放 `question_bank/services/schema_part_difficulty_features.sql`，**不在** `migrations/question_bank/`，重启不会自动建表。正式建表需作为迁移 `043_...` 放入迁移目录并执行，属于需单独授权的数据库迁移。
- 试打：`tmp_retag_trial/`（`run_trial.py prepare|apply`，隔离副本 `tmp_retag_trial/data/`），答案为代理按真实请求人工作答，未调用付费模型。第 151、172、263 题全部写入成功，难度 6.0、8.8、4.6，报告见 `tmp_retag_trial/report.md`。该目录及复制的配图是临时产物，用户确认后删除，不提交。

## 5. 批量重打流程

1. 确认所用模型（重打与日常入库用同一个模型绑定）。
2. 冻结第 1 节题目清单。
3. **真实模型试跑 20 道**（覆盖各章、各题型、带图与不带图；需用户授权调用）：在副本上执行，报告每题实际 token 与费用、新旧标签对比、判定点关联变化、难度结果、词表缺口清单，请用户审阅。
4. 用户确认后：备份 `question_bank.db` 到现有备份目录 → 执行建表迁移（授权）→ 全量执行（授权）。写入走应用自身服务与写入流程，不直接改库；只重打标签与特征、只重挂判定点关联，**不生成新判定点版本**。失败项如实列出，结果不确定时只查询不自动重发。
5. 完成后报告：各维度新旧分布、难度分布（供第 3 节“拉长顶端”决定）、词表缺口清单、教学进度筛选可选题数量的前后对比、失败项。

## 6. 验证

- 相关测试：`tests/test_retag_contract.py`、`tests/phase4/`（derived_ownership、combined_question_analysis、solution_evidence_semantics、evidence_point_knowledge_links）、`tests/phase7/`、`tests/test_question_bank_ai_tagging_quality.py`、`tests/test_tagging_batch_attempts.py`、`tests/test_tagging_sync_job.py`、`tests/test_taxonomy_governance.py`、`tests/test_session_question_bank_sync_job.py`、`tests/test_api_question_bank_routes.py`、个性化推荐与组卷相关测试。
- 已知与本方案无关的既有失败：`tests/phase4/test_part_assessment_mastery.py::test_real_diagnosis_dependency_path_keeps_original_assets_and_forms_groups`；`tests/test_question_bank_paper_trash.py::test_permanent_delete_handles_every_current_fk_child_of_a_complete_analysis`（迁移 042 新增的外键子表未列入该测试白名单，属错因改造一侧）。

## 7. 并行与授权提醒

- 工作区另有未提交改动：命题练习（`question_bank/authoring/` 等）、PDF/MinerU 导入、错因、班级分析、前端等。提交时只暂存本方案相关文件，不修改、不暂存、不提交其他内容。
- 需逐次授权：真实模型调用（试跑、全量）、数据库迁移、写入真实题库、提交与推送。
