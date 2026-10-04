# 训练推荐：当前实现、效果评估与后端性能交接

用途：给后续接手的代理说明训练推荐（个人卷、多人同卷、按章节自动分组、班级组卷候选与快速起草）的后端实现、实测效果和性能现状。接手任务只优化后端性能，不改变推荐规则和推荐结果。业务规则的权威说明在[题库、组卷与个性化训练](../product/KNOWLEDGE_AND_TRAINING.md)，本文只描述实现与测量。

记录日期 2026-10-04。代码版本为提交 `0683b73d`，推荐引擎标识 `personalized-recommendation-v27-skill-mastery-model`，分组标识 `chapter-skill-quality-v9-shared-paper`。本功能此前的实验记录、中间对比目录和样本 PDF 已按用户授权清除，只保留 `output/test_training_skill_model_v4_20261003/` 作为当前效果的配对依据。

## 1. 结论

- 推荐规则在当前数据上可用：两章合卷个人卷覆盖 81% 的技能需要，没有"完全找不到题"的需要。所有效果数字都是系统按自身标签和规则自评，还没有真实训练回收、教师盲评或学习收益证据。
- 后端太慢。按章节分组单次计算 40–55 秒，生成 91 人个人草稿约 35 秒，诊断首次构建约 18 秒。页面能用，主要靠本地快照、结果缓存和空闲预热；缓存一失效（每天首次、任何成绩或题库提交、代码文件改动），教师就要等待完整重算。
- 主要耗时来自重复计算：同一批学生在一次请求中被评价两遍，生成小组草稿时又完整重算全部分组，逐生 × 逐目标 × 逐候选做多层字典查找和深拷贝。规则本身不需要这么多计算。

## 2. 数据规模（真实数据，只读）

八年级上册，2 个班，91 名学生，3 场考试（会话 3、4、5），5,582 条掌握度观测，已发布训练证据 0 条。按范围计的有效候选题：第一章 188 道，第二章 275 道，两章合卷 476 道。两章合卷有技能需要的学生 87 人，技能需要 786 项（单位：学生 × 技能）。

## 3. 入口与调用链

| 教师操作 | 接口 | 后端入口 | 主要计算 |
|---|---|---|---|
| 打开或刷新"按章节"分组 | `POST /api/training/diagnosis`（带 `grouping`） | `backend/api/routers/training.py` 的 `_grouped_diagnosis_response_bytes` | `DiagnosisProfileService.build_profiles`，再 `PersonalizedRecommendationModule.chapter_groups` |
| 生成个人卷或多人同卷草稿 | `POST /api/training/personalized-drafts` | `PersonalizedRecommendationModule.create` | 采用小组时先完整调用 `chapter_groups` 复核；再 `_source_snapshot`、`_mastery_snapshot`、`_recent_question_ids`、`_build_draft` |
| 换题、排除、锁定 | `POST /api/training/personalized-drafts/{draft_id}/edits` | `edit` → `_apply_edit` | 换题时对该生重新 `evaluate_candidates` |
| 班级组卷候选列表 | `POST /api/question-assembly/assistant/candidates` | `backend/api/routers/assembly.py` 的 `compute_assistant_candidates` → `question_bank/services/assembly_assistant.py` 的 `shortlist_candidates` | 全班 `evaluate_candidates(core_only=True)`，再统一排序和相似折叠 |
| 班级快速起草 | `POST /api/question-assembly/assistant/quick-draft` | `quick_draft` | 逐道失分题循环：按技能组合取候选（同组合复用），逐个候选调用 `paper_rule_violations` 检查整卷，每加一题再读一次 `_source_snapshot(question_ids=[...])` |

核心实现集中在 `question_bank/recommendation/personalized.py`（约 4,300 行）。掌握度在 `question_bank/mastery/`，诊断与学情快照在 `integration/diagnosis_profile_service.py`，知识语境匹配在 `question_bank/recommendation/target_matching.py`。

## 4. 计算机制

### 4.1 诊断与掌握度

1. `build_profiles` 汇总本学期全部考试和已发布训练证据，形成逐生薄弱点和来源引用。真实数据无本地快照时约 18 秒。
2. 掌握度由 `CurrentMasteryCalculator` 计算：numpy 实现的分层 logistic 模型，L-BFGS 拟合加逐生 Laplace 近似。全体拟合约 1.4 秒；结果按"范围、参数版本、自然周、排除证据"缓存，键标签为 `mastery-v3-population-slope`。
3. 推荐另调用 `_mastery_snapshot`：每次请求都重建一份逐生逐节点快照，并读取训练证据表，约 5–6 秒。
4. 每个节点的掌握度结果带 `logit_mean`、`logit_sd`、`difficulty_slope`，推荐用它们换算适合难度。

### 4.2 候选池

`_source_snapshot` 读取范围内可练题目的判定点、技能链接、小问难度特征、题面与图片身份，并做范围、教学进度、整题最难小问和题图完整性检查。结果按题库提交代次、标准版本和读取条件缓存（`_SOURCE_SNAPSHOT_CACHE`，最多 8 份）。冷启动首次约 20 秒。

### 4.3 逐生评价（三个入口共用）

`evaluate_candidates` 对每名学生调用 `_candidate_entries`，循环为"学生 × 目标技能 × 合格候选"：

- 用 `match_target` 判断匹配等级（第 1、2 级算命中，第 3、4 级只作多人卷补充）；
- 用 `_difficulty_plan` 算该生该技能的适合难度窗口（掌握度模型：预计做对 60%–85%，补弱以本人失分题难度为锚点，上下最多 1 级）；
- 用 `_direct_preference` 算排序偏好（标签重合、题型、作答方式、错因、难度特征）；
- 生成候选条目，含用途（补弱、巩固、新练习、补充）、距离和偏好分。

单次调用内有 `evaluation_memo` 复用来源小问、匹配结果和资格筛选，但条目本身逐生重建，并多处 `deepcopy` 目标和来源引用。

### 4.4 选题

- `_choose_practice_entries`：贪心选题。排序依据 `_group_rank`，依次是新照顾的补弱成员数、未覆盖需要的优先级合计、需要数、是否核心、是否仅重复巩固、练习需要、方法模型重复、匹配等级、难度距离、偏好。
- 每加一题检查整卷限制：同技能上限、解答题上限、相似题（`paper_similarity_allowed`，含 `difflib` 文本相似度）、相近任务（`paper_task_duplicates`）。
- 个人只补弱卷再做 `_improve_personal_selection`：单题替换，不能改善时做两题联合替换，最多 36 个候选、8,000 个组合。
- 之后按上限补未测目标新练习（默认最多 4 道）。

### 4.5 按章节自动分组

`chapter_groups` 的流程：

1. `_group_needs`：只把有可归属直接失分的技能记为需要，并附适合难度窗口。
2. 对全部有需要的学生调用一次 `evaluate_candidates`（`paper_mode="individual"`，目标为所有需要技能的并集）。
3. `_quality_group_members`：
   - 每名学生先用 `_choose_practice_entries` 生成"单独出卷"作为基准；
   - 从水平最低的学生起，逐个尝试加入薄弱技能最接近、且通过相容前置检查（共同需要至少占一半、难度窗口相交、目标难度差不超过 2 级）的同学；
   - 每次尝试都实际生成一份共用卷：共用卷不少于 6 道，且每名成员能练到的薄弱技能不少于单独出卷的四分之三，才接受该同学；
   - 共用题池为成员题池交集减去成员近期原题并集；结果按成员集合缓存，并先用题池大小和可覆盖技能数做廉价排除。
4. 对每个成组调用 `_chapter_group_summary`：在共享模式下**再次**对组员调用 `evaluate_candidates`，生成卡片统计和预览选题，并计算该组来源版本哈希。
5. 未能入组的学生单列原因。教师指定成员时（`member_ids`）另算一份 `selection`。

## 5. 缓存与预热

| 层 | 位置 | 键与失效条件 |
|---|---|---|
| 完整诊断响应（压缩 JSON） | `training.py` 的 `_DIAGNOSIS_RESPONSE_CACHE`，并写入 `user_data` 下的本地学情快照 | 证据范围、两库内容代次、若干源代码文件的修改时间和大小；带分组时另加分组键 |
| 分组结果 | `_GROUPING_RESULT_CACHE` 与 `grouping-public-v2` 响应键 | 分组请求全部参数、成员与目标、题库提交代次、标准版本、**UTC 日期**、题目资料清单 |
| 学期掌握度 | `semester_mastery` 的标签缓存与 `_MASTERY_INPUT_RESULTS` | 范围、参数版本、自然周、排除证据、输入内容哈希 |
| 候选池 | `_SOURCE_SNAPSHOT_CACHE`（8 份） | 题库提交代次、标准版本、读取条件 |
| 题目语境 | `target_matching.py` 的 `_FACETS_CACHE` | 标准版本与题库读取代次 |
| 班级候选 | `assembly.py` 的 `_ASSISTANT_CACHE`（16 份） | 诊断键、请求体、排除题、题库代次 |
| 空闲预热 | `integration/training_prewarm.py` | 后台线程每 15 秒检查。两库代次或日期变化、且没有排队或运行中的任务时，重算最近 2 小时内的至多 8 个请求，以及启动批次：最新学期的全部学生和各班的诊断、总览、知识结构、第一章分组，外加各班组卷默认候选 |

影响：

- 分组只预热第一章；其他章节或其他设置第一次打开时要完整计算。
- 日期变化、任何成绩或题库提交都会让分组缓存整体失效，预热要等后台空闲才开始。
- 生成小组草稿（`create`）不复用页面已算好的分组，会再完整跑一遍 `chapter_groups`（`personalized.py` 中 `if config.group_scope_keys:` 分支）。

## 6. 实测耗时

真实数据只读，同一台工作机，2026-10-04。下表是后端计算时间，不含 HTTP 传输和浏览器绘制。单位为秒。

| 计算 | 首次 | 同进程再次 | 备注 |
|---|---:|---:|---|
| 诊断构建（无本地快照） | 18.2 | — | `build_profiles` |
| 分组，第一章 | 30.2 | 16.5 | 本轮质量分组前为 27.2 / 12.7 |
| 分组，第二章 | 33.6 | 24.0 | 改前 22.9 / 15.7 |
| 分组，两章合卷 | 53.6 | 42.6 | 改前 38.7 / 25.0 |
| 个人草稿，单人（候选与掌握度已备好） | 约 1.3 | — | 5 人平均 |
| 个人草稿，91 人一次构建 | 34.5 | — | `_build_draft` |
| 前后对比工具全量运行 | 760–1,050 | — | 三个范围、两个版本 |

此前另一项性能调查的测量（10 月 3 日，本轮推荐改动之前）：训练诊断接口首次约 10.6 秒，连续约 3.6 秒，响应约 40.1 MiB。见[页面首次刷新性能调查](backend-first-refresh-performance-investigation-20261003.md)和[两秒目标专项](knowledge-training-two-second-performance-20261003.md)。

## 7. 耗时分布与已知低效点

两章合卷分组的一次性能分析（cProfile 会把总时间放大到约 146 秒，下面看比例和调用次数，不看绝对值）：

| 函数 | 调用次数 | 累计秒 | 说明 |
|---|---:|---:|---|
| `evaluate_candidates` | 17 | 61.5 | 1 次全体评价 + 16 次组卡片复核，同一批学生评价两遍 |
| `_choose_practice_entries` | 174 | 50.7 | 单独出卷基准与逐个尝试的共用卷 |
| `_quality_group_members` | 1 | 49.5 | 含上一行中的大部分选卷 |
| `_chapter_group_summary` | 16 | 26.3 | 含组内再次评价 |
| `_source_snapshot` | 1 | 21.1 | 冷启动候选池 |
| `_mastery_snapshot` | 1 | 5.9 | 每次请求重建 |

底层热点：字典 `get` 5,243 万次；`_direct_preference` 24.3 万次（逐生 × 目标 × 候选都算一遍）；`copy.deepcopy` 405 万次；`difflib` 的 `find_longest_match` 9.8 万次（卷内相似题与题型重复判断）；`_group_rank` 8.7 万次；`_matched_key` 28 万次。

已确认的结构性重复：

1. 分组时组员先被全体评价一次，组卡片又在共享模式下再评价一次。两者只差"排除近期原题的并集"。
2. 生成小组草稿时再次完整计算全部分组，只为取出所选成员的复核结果。
3. `_mastery_snapshot` 不跨请求复用，每次都重建并读训练证据表。
4. 候选条目与题目无关的部分（来源小问、任务要求、难度计划）按学生和目标重复构造；与学生无关的部分（题目标签集合、难度特征、规范化题面）按候选重复计算。
5. 快速起草对每个尝试候选都完整重算整卷规则，每加一题都重新读题目资料。
6. 分组缓存按 UTC 日期失效；预热只覆盖第一章分组。

## 8. 优化约束与验收

必须保持：

- 同一输入下推荐结果逐项不变：个人与多人草稿的题目、题序、用途、推荐理由、缺口与警告；分组的成员、目标、卡片统计、来源版本；班级候选的排序、人数统计和相似折叠；快速起草的加题结果。
- 数据规则不变：真实 `user_data` 只读验证；不调用模型；教师最终分锁、请求令牌、修订冲突、冻结快照与审计不变；缓存失效后必须能检测到来源变化。改动缓存键时，旧快照只能被忽略，不能被误读。
- 遵守 `AGENTS.md` 的授权、运行环境和单次写入大小规定。

建议的验收方法：

1. **结果一致：** 用 `tools/compare_training_endpoints.py`，以当前代码为 baseline、优化后代码为 latest，在同一只读输入上比较三个范围。要求两端个人选题与正式内存草稿一致，逐生题目集合和分组成员完全相同。该工具目前只快照 `personalized.py`、`diagnosis_profile_service.py`、`question_tag_projection_service.py` 三个文件；若优化涉及 `target_matching.py`、掌握度或路由，需要先扩展快照范围，或改用同进程内"原函数 / 新函数 / 恢复原函数"三次输出逐字节比较。
2. **自动测试：** `tests/training/test_personalized_recommendation.py`、`tests/test_unified_practice_rules.py`、`tests/test_assembly_assistant.py`、`tests/test_api_training_routes.py`、`tests/training/test_training_assessment.py`、`tests/training/test_training_submissions.py`、`tests/training/test_part_assessment_mastery.py`、`tests/training/test_personalized_papers.py`、`tests/test_api_graph_selected_scope.py`，以及前端 `training-group-recommendations` 用例和类型检查。已知 `test_training_diagnosis_uses_question_tag_identity` 在 2026-10-03 之后必然失败：它的分组缓存键带当天日期，与优化无关。
3. **耗时：** 用同等输入分别记录首次（无缓存）与同进程再次的时间，至少覆盖本文第 6 节各行；另记录缓存失效后的页面首次完整加载。

## 9. 当前效果评估

除注明外均为两章合卷、91 名学生、同一只读输入。配对依据为 `output/test_training_skill_model_v4_20261003/comparison.json` 与同目录 HTML。

个人卷（只补弱，最多补 4 道未测目标新练习）：

| 成绩层 | 已覆盖技能需要 / 需要 |
|---|---:|
| 较低（23 人） | 271 / 368 |
| 中下（22 人） | 202 / 243 |
| 中上（22 人） | 123 / 133 |
| 较高（22 人） | 42 / 42 |
| 合计 | 638 / 786（81%） |

未覆盖的 148 项需要中：99 项是候选题高于学生适合难度，48 项被同技能上限等整卷限制挡住，1 项属于近期原题；没有"完全找不到可用题"的需要。补弱题难度范围 1.3–6.4 级，各层中位数依次为 1.9、2.2、3.6、4.1 级。

小组卷：

| 范围 | 编成组数 | 入组人数 | 组员被共用卷覆盖的需要 |
|---|---:|---:|---:|
| 第一章 | 10 | 23 / 84 | 81 / 95 |
| 第二章 | 7 | 40 / 87 | 263 / 313 |
| 两章合卷 | 16 | 57 / 87 | 479 / 653（73%） |

两章合卷最大组 22 人，其余多为 2–4 人。未入组学生改用一人一卷。

难度依据：掌握度模型用前两次考试预测第三次、只看训练中没出现过的题，平均预测做对率 79.0%，实际 79.2%。全体拟合的难度斜率为每级约 0.32 个对数几率。考试中的小问难度最高约 6.2 级，更高等级是外推。

解释边界：覆盖数是系统按自身标签和规则自评的关联覆盖，不是教师确认的任务覆盖，也不是学习效果；目前没有训练回收证据和教师盲评。

## 10. 尚未处理的事项

- **分组与班级"按技能"会把其他章的关联技能当作本章需要。** 第一章分组的 632 项需要中，226 项属第二章技能、31 项属第五章技能，本章卷子无法出这类题。只按本章技能计后，第一章需要降为 375 项，入组人数基本不变（23 → 25）。修复方式待用户确认。
- **"同技能最多 1 道"是第一章入组少的主要原因。** 第一章仅 14 个技能。只把上限改为 2：第一章入组 25 → 75 人（19 组），第二章 41 → 50 人。分组跟随教师的出卷设置；是否提示教师或给小组单独默认值，待用户决定。去掉最早一场考试（无逐判定点结果）后第一章入组几乎不变，旧考试粒度不是主因。
- **真实训练回收试点已搁置。** 用户说明：答对率本身不能作为训练效果的评判。
- 两章合卷之外的教材章节尚无考试证据，规则在这些章节的效果未经测量。
