# 关键步骤诊断交接（2026-10-09）

用途：把“技能改作失分步骤诊断”的已确认范围、口径、只读原型和实现入口交给后续代理。本文件是调查与交接，**全部功能尚未实现**；产品行为仍以根目录 README 索引的权威文档为准。开始实现前先读 `AGENTS.md`。

## 1. 状态

- 已完成：现状调查、只读原型（真实数据，学生匿名）、用户对定位与口径的确认。
- 未开始：正式后端、正式页面、测试、权威文档修改。用户要求原型后先停，由后续代理实现。
- 没有调用模型，没有写入任何数据库或已有数据文件。

## 2. 用户已确认的决定

| 问题 | 决定 |
|---|---|
| 技能的定位 | 只做“失分步骤诊断”，回答“这类题卡在哪一步”；不回到掌握度、配题或推荐排序 |
| 第一批功能 | ① 成绩中心“试题诊断”的失分步骤热点；② 学生作答证据页与个人报告显示“卡在哪一步”；③ 典型错法按关键步骤归类，并入①的展开详情 |
| 页面称呼 | 新显示与题目详情称“关键步骤”；未改版册的旧技能掌握度页面暂保留“技能”，避免与旧规则混淆。稳定标识仍是 `sk_` |
| 范围 | 只做八上。八下、九上没有技能词表，新册单题内只显示判定点原文，跨题汇总以后再议；不为新册生成词表 |
| 教师锁分与 AI 同分 | 教师锁定分数与 AI 给分相同、但没有教师逐步记录时，热点与“卡在哪一步”**使用** AI 步骤判断，并标注“教师确认总分，步骤为 AI 判断”；**掌握度规则不动** |
| 常见写法来源 | 用现有错因整理得到的典型错法名称按人数合并，阅卷说明原文作为可展开的例子；题库典型错法折叠在后 |
| 不做 | 同题型内按失分步骤挑题（需先盲评实验）、题型标错提示、新册自动整理技能词表 |

背景依据：题型改版实验中，按技能找同类题的“同类合适”比例为 80%，按题型为 99%；题型加技能的掌握度预测只好 0.004 对数损失（见 [题型改版方案](question-type-redesign-plan-20261006.md)）。所以技能不回主轴；该方案 3.8 节已列“讲评时定位同一题型中全班失分集中的步骤”为后续用途。

## 3. 统计口径（正式实现须保持）

- **范围**：一场考试；八上；有逐步评分记录（`step_assessments`）的解答题小问。客观题和旧格式考试（如第二周）没有逐步记录，不进入。
- **统计单位**：学生。同一学生在同一关键步骤多次失分只算 1 人。分母为该关键步骤有“独立失分或完成”状态的学生数。逐题表按“学生 × 步骤”计人。
- **独立失分**：该步判定点未达成，且它依赖（`depends_on`）的前置判定点均达成。复用 `integration/diagnosis_profile_service.py::_step_point_observations` 的判断，不要用 `backend/session_analysis.py::_independently_failed_steps`（后者不看 `depends_on`）。
- **受前一步影响**：该步未完成，但依赖的前置点未达成或未观测；不计入独立失分。AI 标 `carried_error_from` 且本步方法正确的计为完成（与掌握度一致）。
- **整题未作答**：`answer_is_blank_or_no_valid_work` 为真时单独计数，不计入独立失分与分母。空白不反推错因。
- **AI 判不准**（`uncertain`）：单列，不计失分。
- **教师最终分优先**：有对应当前锁的有效教师逐步记录（`_teacher_step_records` / `_validated_teacher_steps` 校验）时用教师结果；锁定分与 AI 分相同、无教师逐步记录时按第 2 节决定使用 AI 步骤并标注；锁定分与 AI 分不同且无逐步记录时不进入步骤统计，页面写明排除的“学生 × 题”记录数。比较锁定分与 `session_details.ai_score_awarded` 时按小问行逐条核对。
- **关键步骤名称**：来自考试冻结快照（`user_data/config/uploaded/evidence_snapshot-<考试编号>.json`）中判定点的 direct、resolved、`sk_` 链接。注意 `QuestionTagProjectionService` 在题型章会用主题型覆盖 direct 链接（`_type_overlaid_question`），取技能名须读原始快照，不能读投影后的 `point_links`。名称取活动标准的 `display_name` 末段并去掉“技能·”。
- **题库典型错法**：触发方式为判定点的已确认错法，按 `error_pattern_service.pattern_skill_index` 的既有优先顺序归到关键步骤（教师设定 → 触发判定点的直接技能 → 本题唯一技能）。只有实际作答记录（`occurrences_json`）才算学生已发生的错误，预测错法另行标注。

## 4. 只读原型

- 位置：`output/test_step_hotspot_prototype_20261009/`（`output/` 不入库，只在本机）。`index.html` + `app.js` 为页面，`data.js` 为生成的数据，`build_data.py` 为生成脚本。
- 复现：在项目根目录执行 `$env:PYTHONIOENCODING='utf-8'; .\runtime\python\python.exe output\test_step_hotspot_prototype_20261009\build_data.py`。脚本借用 `tools/experiment_training_fit.py::readonly_runtime`，以只读方式打开原位置数据库并拒绝写入；不调用模型。查看时直接打开 `index.html`，或临时用 `python -m http.server` 在非 8035 端口提供。
- `data.js` 含阅卷说明原文（学生作答片段），学生只以“学生甲／乙”出现。不要复制到 `docs/`、提交或写入日志。
- 原型尚未包含第 2 节两项后定决定：锁分同 AI 分时使用 AI 步骤，以及按错因整理的典型错法名称合并常见写法。正式实现以第 2、3 节为准。

## 5. 只读测量（2026-10-09，八上两场有逐步记录的考试）

单位：除特别说明外为学生人数；“排除”为“学生 × 题”记录数。

| 项目 | 第三周 | 第四周 |
|---|---|---|
| 有逐步记录的学生 | 80 | 88 |
| 教师锁分、无逐步记录而被排除的记录 | 119（其中锁定分与 AI 分相同 53） | 245（其中相同 173） |

第四周热点（独立失分 / 分母；另列受前一步影响、整题未作答）：分母有理化 36/71，4，16；化简二次根式 37/82，1，6；实数与二次根式的混合运算 21/66，18，21；二次根式乘除 21/72，0，16；用乘法公式化简根式 15/69，0，13。第三周前三项：实景转为边长关系 21/50；确认直角与斜边 21/50；求整数与小数部分 23/79。

口径对结论影响很大：若把所有未完成步骤记录都算失分，“混合运算”为 352 条“学生 × 步骤”记录中失分 173 条（49%），按第 3 节口径只有 21 人独立失分。

题库现状（活动标准 `kgr_bnu_math_curriculum_2026_10_v9`）：活动技能 143 个，实际挂到判定点 124 个，19 个未用，5 个只用在 1 道题；66 个技能出现在 6 个以上主题型中，适合做跨题共性诊断。判定点触发的典型错法已确认 117 条、候选 516 条，`skill_key` 全为空。原型中只有 7 个关键步骤有已确认步骤错法，全部来自这两场考试自身的题，每条 1 条实际作答记录，所以题库错法应折叠在后。第四周第 17 题 S1、S2、S4 在冻结快照中没有关键步骤，显示“未挂关键步骤”。

## 6. 复用入口（不新建页面）

| 功能 | 现有位置 | 说明 |
|---|---|---|
| 热点后端 | `GET /api/sessions/{id}/class-analysis`：`backend/api/routers/reports.py::get_class_analysis` → `backend/reporting/analysis_report_exporter.py::build_class_page_data`，错因经 `backend/class_analysis.py::apply_cause_results` 叠加 | 在同一响应中增加关键步骤汇总与逐题步骤计数；需要冻结快照与依赖时复用 `QuestionTagProjectionService.project_session`（取 `steps`、`evidence_points`）和原始快照 |
| 热点页面 | `frontend/src/components/results-center/ClassAnalysisPanel.vue`（成绩中心“试题诊断”）；类型 `frontend/src/api/class-analysis.ts`（`failed_steps` 已有字段，页面未显示） | 顶部摘要 + 逐题展开，融入现有面板 |
| 常见写法 | 已保存错因组 `question["causes"]`：`reason`（典型错法名称）、`step_ids`、`evidence[].student_ids`（`apply_cause_results` 第 716–739 行附近） | 按关键步骤合并人数；阅卷说明原文作为例子。注意错因输入步骤来自 `_independently_failed_steps`，不看 `depends_on`，且锁分题不用 AI 步骤，与第 3 节口径不完全一致，合并时须说明或对齐 |
| 学生作答证据 | `frontend/src/views/StudentEvidenceView.vue`；接口 `GET /api/students/{id}/exam-results`（`backend/api/routers/students.py`） | 当前不返回逐步结果，需增加失分步骤（步骤要求、状态、关键步骤、阅卷说明） |
| 个人报告 | `analysis_report_exporter.py::_render_personal_html`，失分题附录 `<dl class="kv">` | 新增显示不得让已有报告全部变成“需重新生成”；先核对 `personal_report_states` 的输入指纹是否会因此变化（尚未查证） |
| 典型错法归类 | `question_bank/services/error_pattern_service.py::pattern_skill_index`、`_version_point_skills` | 只读复用；跨模块读取题库须经现有只读能力 |
| 改称 | `QuestionAnnotationPanel.vue`（判定点编辑与错法“关联技能”文案）、`TrainingCriterionReview.vue` | 只改显示文字，稳定标识与数据不变 |

## 7. 边界与注意

- 跨模块：热点在考试阅卷模块的结果页读取冻结快照与题库典型错法，属于只读协作；不写题库，不改分数、排名、报告分数、教师最终分锁或掌握度。修改 `class_analysis.py`、`analysis_report_exporter.py` 等共享热点文件前，按 `AGENTS.md` 说明影响范围。
- 第 2 节的锁分同分规则只用于显示，因此热点人数会与掌握度证据不同；页面须标注来源，不要顺手改掌握度。
- 统计须随教师改分、错因重整而更新，沿用试题诊断现有的缓存与失效机制，不新建持久存储。
- 不调用模型；真实数据只读；需要启动应用验证时按 `AGENTS.md` 运行环境规定使用 `运行.bat` 与 8035，先确认没有进行中的批改。

## 8. 完成标准

- 用第三、四周两场真实考试只读核对：热点人数与第 5 节一致；如因锁分同分规则而变化，单独说明变化量与原因。
- 改动前后考试分数、排名、报告分数、教师最终分锁与掌握度结果不变（同输入对比）。
- 在现有试题诊断、错因、学生证据和报告测试中补断言：独立失分、连带、空白、锁分同分／不同分、未挂关键步骤。
- 同次提交更新权威文档：`CONTEXT.md` 的“技能”词条（关键步骤的含义与适用范围）、`docs/product/GRADING.md` 的试题诊断与个人报告、`docs/product/KNOWLEDGE_AND_TRAINING.md` 中技能的说明；数据流若有变化同步 `ARCHITECTURE.md`。完成后从本目录索引中移除或改写本文件状态。
