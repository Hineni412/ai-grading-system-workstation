# 题型改版交接（2026-10-07）

用途：把题型改版的当前进度交给后续代理。方案与决定见 [改版方案](question-type-redesign-plan-20261006.md)，本文件只写实施状态、未完成项和接手注意事项。产品行为仍以根目录 README 索引的权威文档为准；代码与文档在本地 main 整理；真实题库已获本次授权并启用 v9。

## 1. 一句话状态

真实题库已启用 `kgr_bnu_math_curriculum_2026_10_v9`（修订 11）。1292 道已标题型题全部纳入有效范围；57 道预存来源失效题已由当前聊天模型逐题核读并恢复，未调用应用模型接口。用户确认后另修正了其中 2 道题的歧义文字。页面已人工验收，正式前端已同步，原应用在 8035 端口运行。新导入、考试报告、完整班级基准与推荐规则已实现并验证。七年级等未改版册保留技能规则。3 道低置信度题型仍属暂定分类；新题真实模型标注效果与学生实际训练收益尚未验证。

## 2. 关键事实

- 题型键格式 `kp_bnu24_math_g8_upper_{章}_{节}_tNN`，7 章共 100 个题型。判断与目标选择集中在 `question_bank/question_types.py`（`is_type_key`、`type_keys_active`、`is_training_target`、`training_keys`、`question_type_key`）。新代码一律经这里判断，不要再写 `startswith("sk_")`。
- 当前活动标准：`kgr_bnu_math_curriculum_2026_10_v9`，taxonomy revision 11，文件 `question_bank/taxonomy/catalogs/knowledge_graph_release_v9.json`、`tag_vocabulary_v10.json`，已在 `question_bank/knowledge_graph_release/loader.py` 登记。
- 构建工具 `tools/build_type_release.py`：默认只打印摘要；`--preview-db` 只写新建副本并拒绝真实题库路径及已有目标；`--activate-and-carry-db` 与 `--db` 必须相同，需 `--backup-dir` 和本次真实数据操作授权。先校验已登记标准、词表及当前资料，再备份、隔离预演、核对数据变化并执行；默认来源失效会阻断，可显式加 `--skip-unavailable` 仅给当前有效题新增题型，失效资料与旧链接保持待处理。链接写入失败回退原活动标准，备份保留。
- 每题 1 个主题型（写成判定点的题型链接），最多 2 个次题型（`question_tags.tag_type='secondary_type'`, `source='taxonomy'`）。1297 题中 5 题为 `NONE`，原因见 `output/question_type_stage1_20261006/labels/issues.md`。
- 过去的考试不重批：读学情时经“考试题 → 已确认题库题 → 当前主题型”投影（`integration/question_tag_projection_service.py`、`integration/diagnosis_profile_service.py`）。

## 3. 已完成内容

### 3.1 阶段 1、2（数据与标准，均在 `output/` 与 catalogs）
- 词表：`output/question_type_stage1_20261006/vocab/vocab_ch1..7.tsv`（用户已审核同意）。
- 标签：`output/question_type_stage1_20261006/labels/`（SWE-2 标注；第 2、4 章参照讲义项目分类，第 5 章为唯一独立检验；正式使用前仍需用户抽查）。
- 副本预演：100 个题型节点，1292 题有主题型，新增 4482 条判定点题型链接，原技能 4688 条、知识点 640 条链接不变，外键与重复标签检查通过。

### 3.2 阶段 3 第一批（后台）
- 掌握度只按题型计算（v9）；配题四层候选：同主题型 → 次题型相关 → 同小节且题面相似度 ≥0.6（理由注明“相近题，不是同一题型”）→ 同小节补充（仅班级组卷供手选）。同题型内优先与失分原题题面相近的题。
- “同技能最多 N 道”在 v9 下变为“同题型最多 N 道”。
- 先补哪些题型：学生 × 题型加权得分率；考 ≥2、错 ≥2、得分率 <60% 为稳定失分优先，其余按“全班 − 本人”差距排序；同小节占比作为排序偏好。
- 主要文件：`question_bank/recommendation/{target_matching,personalized}.py`、`question_bank/services/{assembly_assistant,knowledge_order}.py`、`question_bank/solution_evidence/knowledge_links.py`、`backend/api/routers/assembly.py`，以及上述两个 integration 文件。

### 3.3 阶段 3 第二批（页面，按用户确认的原型）
- 学情 → 知识结构：v9 下只一栏“题型 · 考什么”；隐藏没有证据的题型和整节无证据的小节，并提示“尚未考查的 N 个题型已隐藏”；右侧不列逐个学生，改为定义、四档人数、一道典型题（本班考过的这类题中班级加权得分率最低者，带考试名、题号、本班得分率）和“本班还考过这类题”列表；保留“给这 N 人出训练卷”“看错题”。
- 学情总览、按学生、训练推荐、组卷助手、试题篮、个人补弱草稿：v9 下统一用“题型”字样和题型节点。
- 后端：`integration/mastery_overview.py` 输出 `target_kind`、`kind:'type'` 节点、`typical_question`/`other_questions`、学生 `types` 分档；`backend/api/schemas/{training,assembly}.py` 对应字段。
- 考试报告（跨模块，见 5.3）：`backend/reporting/{report,analysis_report_exporter}.py`、`backend/session_analysis.py`、`backend/report_assets/personal_knowledge.{js,css}` 在 v9 下把“知识点分析”改称“题型分析”并按题型分桶。分数、排名和教师最终分锁不变。

### 3.4 新导入题与报告补测
- 联合分析复用 `question_bank/training_criteria/{analysis,adapters,combined_analysis}.py` 的封闭候选合同与存储。所属小节的题型加入候选；每题 1 个主题型、最多 2 个次题型。主题型挂每个判定点，次题型保存 `secondary_type`；无合适题型进入现有新词待审，不自行创建。
- 提示词为 `combined-v4-question-types`；不自动重打已有题，v8 与 combined-v3 来源哈希保持兼容。支持先入库分析、确认题库关联后入库、判定点单独重试和断点恢复，不新增模型请求步骤。
- 报告补测覆盖同名不同题型、未关联题、无教学学期、教师确认分数与排名。混合标准按当前教材册选题型或技能；无教学学期时，只有本卷可靠题型关联才保留题型模式，无可靠来源沿用旧显示。
- 完整题型诊断通过私有 `_type_class_question_totals` 给出班级、考试、题号三级加权评分基准。复用已读成绩，仅补读同班未选中学生；教师最终分锁与满分优先，未确认复核或涂抹结果排除。单人和小组选题不扩大名单，各人只与本班同批题比较。摘要、技能模式和公共响应不带新字段；缓存、来源签名与持久快照涵盖基准。旧输入缺基准时明确称选中群体；显式缺少同批题基准时保持未知。
- 用户选择小节分散为软偏好：有其他小节合适候选时优先分散，候选不足时允许同小节补齐，不设硬上限。

## 4. 验证记录

| 范围 | 命令或方式 | 结果 |
|---|---|---|
| 后台推荐、统一练习、组卷 | `runtime/python/python.exe -m pytest tests/training/test_personalized_recommendation.py tests/test_unified_practice_rules.py tests/test_assembly_assistant.py tests/question_bank -q` 等 | 119 项通过（第一批） |
| 学情总览与报告 | `pytest tests/test_api_graph_selected_scope.py tests/test_analysis_report.py tests/test_personal_report_design.py -q` | 32 项通过 |
| 前端 | `cd frontend && npx vitest run` knowledge-graph-view / knowledge-overview-view / training-recommendations-view / assembly-assistant / personalized-recommendation-draft / practice-selection / question-bank-view / training-group-recommendations / student-evidence-view 九个 spec；`npm run typecheck` | 115 项通过；类型检查无错误 |
| v8 兼容 | 同输入下 v8 掌握度数字与改动前相同；推荐与组卷在无题型标准时不变 | 通过 |
| 副本端到端 | 用户最初截图题（第三周第 8 题，T2-05 数轴上构造无理数）前 10 候选全同题型；8 名学生个人补弱卷全同题型、分散 4–6 小节 | 通过（需 `--remap-assets`，见 5.4） |

补充验证：新导入题及同步、配置生成、证据恢复相关回归 66 项通过；报告及打印、改分、教师分数比较与缓存回归 43 项通过；标准发布与当前解析回归 31 项通过，启用及来源阻断、显式跳过和同时间版本相关 17 项通过，修订 3–11 文件往返校验 9 项通过。以上分组有重叠，不合计为全项目测试数。混合教材册的知识解析、学情与报告精准用例 23 项通过；组卷助手全模块 19 项通过（扩展既有题型用例检查其他册的目标与响应称谓）。推荐最终合成回归 114 项通过：`runtime/python/python.exe -m pytest tests/training/test_personalized_recommendation.py tests/test_unified_practice_rules.py tests/question_bank/test_topic_skill_matching.py -q --tb=short`。完整班级基准的服务、缓存与快照回归 14 项通过；114 项推荐回归已在该修复后复跑，随后补充原有 2 个用例的单人完整配题断言也通过。启用前 1235 道有效范围只读模拟已完成，见下节；该测量未包含本次恢复的 57 道题。

### 4.1 通过来源范围的推荐只读模拟

范围为同一八上教材册、2 个班、91 名学生、3 次考试；两版本读取原处同一真实资料根。每位学生按其所属完整班级的有效最终成绩取得同题基准，目标学生范围保持原样。读取现有数据库副本，所有原始输入表核对相同，数据库代次前后不变。在内存读取层遮蔽 57 道来源失效题的题型标签与关联，模拟真实启用仅写 1235 道有效题的决定；未写任何数据库或模型请求。补完班级基准后只重算受影响的 v9 三范围，既有 v8 输入未变；四个副本数据库文件前后字节签名相同。匿名材料在 `output/TEST-type-recommendation-rules-20261007/anonymous_full_class_summary.json`。

| 所选章节 | v9 小组数 | v9 入组学生数 | 共用卷保留个人卷主要补弱目标的最低比例 | 低于四分之三的学生数 |
|---|---|---|---|---|
| 第 1 章 | 7 组 | 48 人 | 75% | 0 人 |
| 第 2 章 | 5 组 | 45 人 | 75% | 0 人 |
| 第 1、2 章 | 12 组 | 54 人 | 75% | 0 人 |

三范围互相重叠，人数不相加。比例分母是同一学生在同输入个人卷中可练到的主要补弱目标数；不是全部错题数或题目数。三范围的个人选择与正式草稿选择一致，个人、小组均未发现成员难度不适合的题位。v8 对照仍保留旧技能行为；第 2 章、合并章各有 1 人按此次个人基线核算低于四分之三，属于既有技能规则结果，不扩大本批修复。v8 的技能目标与 v9 的题型目标数量、派生考试引用形状不同，不能直接把两种目标覆盖率之差解释为精准度提升。本轮只证明给定输入的规则与组卷行为，未验证教师独立题面评价或学生训练收益。

页面已由用户人工验收。前端类型检查、独立生产构建和包大小检查通过；首轮构建位于 `output/TEST-type-release-ready-20261007_173805/dist`；正式同步成品位于 `output/TEST-type-release-staged-frontend-20261007/frontend/dist`，它只从本任务提交的前端来源构建，不包含未暂存的扫描改动，类型检查与包大小检查均通过。该成品已同步到共享 `frontend/dist`，原前端已另备份。真实题库启用与回读已验证；真实模型的新题标注效果、学生实际训练结果仍未验证。

## 5. 验收、启用与剩余事项

### 5.1 页面人工验收（已通过）
用户已人工验收页面，认为没有问题。
副本预览服务：`output/question_type_stage3_20261006/run_preview_service.py`，数据根 `output/question_type_stage3_20261006/data_root_v9`（v9 已启用，模型地址已改为不可达，不会产生费用），前端构建 `dist_v9/`，端口 8046。该包装脚本只在进程内放宽路径解析，以读取副本中指向真实 `user_data` 的绝对路径；生产代码未改。共享 `frontend/dist` 已同步正式成品；8046 仍是独立旧副本预览，不能用其数据判断正式题库状态。

### 5.2 新导入题打题型（已完成）
实现与合成验证见 3.4。真实模型标注效果仍未验证，若要调用真实模型需用户针对本次调用单独授权；已有题不自动重打。

### 5.3 考试报告的题型模式
用户已明确选择保留 v9 的“题型分析”；报告专门测试及既有分数回归已通过，见 3.4 和第 4 节。真实启用已执行，原应用读取 v9。

### 5.4 题图绝对路径（独立旧问题，未修）
`_resolve_asset_path` 遇到数据目录外仍存在的旧绝对路径时会先拒绝，不尝试映射到当前数据根，换电脑或搬目录后这些题会被当作缺图排除。是否修由用户决定。

### 5.5 v9 真实启用与来源修复（已完成）
用户本次明确授权当前聊天模型处理失效题、写原数据库并真实启用 v9。57 道题均核读当前文字和图片：52 道修正判定点，5 道核实原逻辑可复用；全部重新评定逐小问难度，覆盖 170 个小问。不是只刷新旧来源指纹。先在副本中预演 55 道，再保存原题库；另 2 道经用户确认题意后同步最小题干文字与对应 Word 富文本，并单独预演、保存新判定点。题 238 澄清第（3）问允许选取起始位置；题 1400 统一第（4）问题干与重复复述的点名。原图、正确解答和冻结考试保持。

当前实际范围为 1292 道题、4366 条判定点题型关联、54 个次题型标签；5 道 NONE 与 37 道标注范围外题未新增题型。启用时保留原技能和知识点关联；修复旧判定点时按重新核读的依据生成当前关联，旧版本完整保留。3 道既有低置信度标签（967、1159、1146）仍是暂定分类，未经用户专项抽查。

原处 `user_data/backups/question_type_v9/` 保留三份关键数据库备份：来源修复前、修复 55 道后且启用前、补改 2 道题干前的 v9。两份原富文本保留在同目录 `source_corrections_20261007/`。复核资料与匿名核对回执保留在原处 `user_data/reports/codex_type_v9_repair_20261007/`，不进入 Git、文档或公共日志。

真实回读检查通过：57 道来源及小问难度有效、1292 道题型关联齐全、题干变更与预演相同、其他原题字段和全部旧依据版本保持、冻结卷保持、考试库字节保持、数据库完整性及引用校验通过。应用启动后的考试记录也核对保持。未调用应用 SDK 或外部模型接口。

正式成品已同步 `frontend/dist`；旧前端在 `output/TEST-type-v9-activation-20261007/frontend_before_activation/`。本次维护启动脚本 `output/TEST-type-v9-activation-20261007/start_verified_app.py` 使用原真实数据根、配置与 8035 端口，只跳过无关的旧报告缓存自动迁移清理；未改安全、评分、路径解析或数据库检查。下次代理启动前仍须检查真实任务及待执行维护操作；不得把 8046 旧预览当成真实库。恢复覆盖仍须针对具体目标另行授权。

### 5.6 文档（阶段 5）
已按“当前教材册在活动标准中是否含题型节点”改写 `docs/product/KNOWLEDGE_AND_TRAINING.md`、`CONTEXT.md`、`ARCHITECTURE.md`、`docs/testing/README.md` 和 `docs/product/GRADING.md` 的报告段落；保留旧标准条件与章节考情原规则。实际启用状态由数据库与交接记录说明，不在权威文档写实施流水。

## 6. 工作区与提交注意

- 本任务在本地 main 提交，扫描阅卷改动保持原样且不纳入。属于本任务的：第 3 节列出的文件，以及 `question_bank/question_types.py`、`tools/build_type_release.py`、两个 catalogs JSON、loader、`README.md` 维护工具表一行、`docs/requests/` 下方案与本交接、相关测试（`tests/test_skill_candidates.py`、`tests/test_current_knowledge_resolver.py`、`tests/question_bank/*`、`tests/training/test_personalized_recommendation.py`、`tests/test_unified_practice_rules.py`、`tests/test_assembly_assistant.py`、`tests/test_api_graph_selected_scope.py`、前端三个 spec）。
- 不属于本任务、勿动：扫描阅卷相关（`backend/api/routers/{grading,scan}.py`、`backend/api/schemas/scan.py`、`backend/jobs/*`、`backend/scan_grading/workspace.py`、`frontend` 下 scan-grading 文件及其测试、`docs/product/GRADING.md` 的扫描批次段落、`CONTEXT.md` 的“新增答卷文件”词条）。本任务仅修改 GRADING 的报告规则，提交时不能混入扫描段落。`frontend/src/styles/scan-grading.css` 第 30 行疑似误粘贴的命令文字，用户尚未处理。
- `cache/`、`exp_local_gemini/` 来源未知，保持原样。
- 实验与盲评材料：`output/test_type_experiment_20261006/`（盲评结果 `review/review_result.json`），阶段 3 验证材料 `output/question_type_stage3_20261006/`。`output/` 不入库。
