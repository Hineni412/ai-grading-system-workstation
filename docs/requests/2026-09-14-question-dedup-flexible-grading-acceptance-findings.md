# 题库提前查重与灵活评分：修复与复验记录

日期：2026-09-14。用途：保留用户要求记录的验收发现，以及授权修复后的实现和验证结果。

状态：**用户已授权修复。以下 F01–F12 已在源码中修复并做定向复验；正式题库升级与 8035 服务启用已在用户逐次授权后完成，真实模型效果尚未验收。** 本文只记录源码及隔离合成数据验证，不包含真实考试、学生或数据库记录。

需求依据：[实施交接文档](2026-09-13-question-dedup-and-flexible-grading-implementation.md)。业务规则以该文档第 2 节的已确认要求及项目 AGENTS.md 为准。

## 一、修复前的问题与验收要求

| 编号 | 需求 | 已确认问题及复现 | 主要入口 | 修复后的验收要求 |
|---|---|---|---|---|
| F01 | R7/R8/R12、C4/G8 | 同一份仅答案正确、无过程的结果，携带 `answer_only_correct=true` 和 `answer_is_blank_or_no_valid_work=true` 时，整卷保留 1 分，混合入口改为 0 分。旧文字防护也没有依据新步骤明细判断有效过程；仅 `x=数字` 等表面形式仍可能被当作过程，绕开明确的答案 1 分声明。 | `hybrid_batch_grading_service.py::_detail_from_ai_item`；`solution_answer_guard.py::extract_observed_text/apply_solution_substance_rules`；`ai_grader.py::_validate_and_convert` | 两入口对同一合成作答得到一致结果；答案正确无过程恰好 1 分，有效过程按块给分；格式不完整时进入失败/待复核，不能把已提供的有效步骤证据当空白判零。 |
| F02 | R10、E2/E3、G7 | 模型 8.5 分虽然标记为待复核，仍能经实际结果仓库写入业务成绩列。 | `ai_grader.py::_validate_and_convert`；`hybrid_batch_grading_service.py::_detail_from_ai_item`；`backend/repositories/results.py::save_session_result` | 非整数、布尔、非有限、负数、越界分不写成有效新成绩；不静默取整；保留错误状态与必要诊断，不伪装为学生零分；历史教师确认值保持。 |
| F03 | R11、D6/E3、G7 | 过程题完全缺失 `step_assessments` 可通过且不要求复核；`achievement=none`、空作答依据却给满分，也可通过。 | `solution_answer_guard.py::validate_step_assessments` | 新过程评分结果每块恰好一项，身份、范围、整数、加总、状态与依据一致；历史没有明细的成绩继续只读展示，不能与新模型漏字段混为一谈。 |
| F04 | R1/R2、B7、D8 | 考试查重已命中旧题，但 `reusable_analysis` 不可用时 `_reused_analysis_items` 跳过该题，后续重新进入模型分析队列；异常也会退回模型。 | `backend/jobs/config_generation.py::_reused_analysis_items`；`question_bank/services/duplicate_analysis_copy_service.py::reusable_analysis` | 区分新题、可复用、缺项/冲突；重复题缺旧分析只显示待处理，不暗中发模型或覆盖旧标签；测试必须走真实查重入口，不仅预先构造 reused_items。 |
| F05 | R3、B2、D1/D2/D3 | 当前正式查重仍使用精确文字、原始公式 XML 和带尺寸的像素键；仅分值前缀、等价减号、根式 `1/on` 等价属性或图片缩放的变化均不能复用。 | `question_bank/services/duplicate_analysis_copy_service.py::_question_content_key`；`question_bank/models/question.py::duplicate_question_key` | 格式变化可复用；运算、数值、条件、根指数、选项、图形点名/长度/阴影变化不得直接合并；不确定的图像只作待核对候选。 |
| F06 | B2、R3 | 内容索引仅补缺失行。合成题保存新的公式富内容后，再执行 ensure_content_index，索引仍是旧题面键；既有内容修订入口没有同步更新索引。 | `question_bank/services/duplicate_analysis_copy_service.py::ensure_content_index/upsert_content_index`；现有富内容保存与回填入口 | 随实际内容修订更新或失效索引；旧题面不能指向已改题意的新内容；未改题无需再次全库解码。 |
| F07 | B6、D7 | 一张卷两次引用同一道已有规范题时，导入器查询占位符按唯一 ID 生成，参数却未去重，报“需要 2 个参数，提供了 4 个”。 | `question_bank/importers/batch_importer.py::_import_scanned_paper`，分析复用计数段 | 同卷多次出现、同批重复和重复导入正常完成，保留每次题号，不重复创建规范题；失败状态与已经保存的关系一致。 |
| F08 | B6/B9、D9/D10 | 合成 A/B 两卷共用题目，删除 A 时将规范题迁到 B 并删除 B 的出现记录；恢复 A 后，A 原来的题目数从 1 变为 0。 | `question_bank/services/question_write_service.py::_rehome_surviving_questions/set_paper_deleted` | 删除/恢复一卷不丢另一卷内容，也不丢被恢复卷的题号与出现关系；保持历史题号及引用，检查预览、图片与题频。 |
| F09 | E6、P3 | 教师最终分生效后，评分检查器因 `score_source=teacher` 隐藏已有 AI 步骤依据，并错误提示没有 AI 初评分数或标注。 | `frontend/src/components/review/ReviewScoringInspector.vue::hasAiAssessment`；对应读取字段 | 重新进入仍可查看 AI 原分与步骤解释，明确它们属于 AI 初评；教师最终分独立显示，不伪造改分后的步骤理由。 |
| F10 | E8、P4 | 旧 AI 结果没有步骤明细时，没有明确显示“暂无步骤评分依据”。 | `frontend/src/components/review/ReviewScoringInspector.vue` | 明确显示暂无依据，不从总分编造步骤、不重发模型。 |
| F11 | R6、S1 | `f(1)、f(2)` 已排除，但 `f (1)、f (2)` 带空格后再次识别为两问。 | `question_bank/parsers/type_detector.py::subq_mark_labels/_is_subq_marker_context` | 结合富文本公式与文本回退判断，排除空白变体的公式编号，同时保留真正的行内/换行小问。 |
| F12 | 评分规则的可理解性；本次前端核对发现 | 评分卡片默认只展示分值、关键步骤和扣分文字；仅答案上限藏在每小问首卡的编辑区。块内整数部分分、等价达成和同错不重复扣等共同规则没有可见说明，通用页脚“按评分依据判定”无法区分不同策略。编辑接口也未提供 `response_mode` 与 `allow_alternative_methods` 的展示字段。教师难以判断卡片分值是最高分、是否允许部分得分，以及各卡片共用哪个小问上限。 | `frontend/src/components/config/RubricEditorTable.vue`；配置编辑行的数据映射 | 在现有卡片与小问结构内清楚展示实际生效规则，标明“本块最高分”和小问答案上限，说明整数部分分与等价解法；避免再加一组独立设置。展示应来自实际配置和共同规则，不能只贴一个“新版”标记。 |

## 二、修复后的行为与验证

| 范围 | 已实现结果 | 验证证据 |
|---|---|---|
| F01–F03 | 整卷与混合入口一致保留正确仅答案的 1 分；已校验的逐块证据优先。过程题缺步骤、状态矛盾、缺依据、错小问、得分不守恒或非法分值拒绝成为有效新成绩。首次保存与选题重批均拦截。 | `tests/test_flexible_grading_acceptance.py`；`tests/test_solution_answer_guard.py`；`tests/test_hybrid_batch_validation.py`；`tests/test_atomic_major_retry.py` |
| F04 | 真正走查重、读取已存分析、组成考试分析结果：排版变体可复用，结果不含本次模型请求。旧分析缺失或不可用保留待处理，不进入普通模型重试。 | `tests/test_question_dedup_acceptance.py` 的实际复用、缺分析案例；`tests/test_config_generation_job.py`；`tests/test_session_question_bank_sync_job.py` |
| F05–F06 | 分值前缀、全半角、等价减号、公式格式及布尔属性写法归一；根指数、运算符与图中内容不同不能直接复用。能证明像素相同的整数倍缩放可复用；压缩或插值等不确定图片进入核对提示，考试入口不自动调用模型。题面、富内容和图片修订会刷新索引，未改图片不重新解码。 | `tests/test_question_dedup_acceptance.py` 的文本、根式、图形、修订及不确定图片案例 |
| F07–F08 | 同一道规范题在同卷出现两次可正常导入并保留两个题号。删除原宿主卷时保留其出现关系，恢复后题目仍在，另一卷也保留自己的题号。 | `tests/test_question_dedup_acceptance.py`；`tests/test_question_import_duplicates.py`；`tests/test_question_bank_paper_trash.py` |
| F09–F10 | 教师最终分独立生效，仍可查看 AI 原分及原步骤依据；没有旧步骤时明确显示暂无依据。历史教师 4.5 分在新 AI 4 分保存后仍为最终 4.5 分，AI 原分与步骤正文回读保持。非法新成绩不会覆盖原结果。 | `tests/test_flexible_grading_acceptance.py`；`tests/test_report_ai_teacher_comparison.py`；独立页面 P3/P4 复验通过 |
| F11 | 带空格的函数、下标和图号括号不再制造小问；真正换行小问仍保留。 | `tests/test_question_dedup_acceptance.py` |
| F12 | 卡片按实际小问归组，显示小问总分和共同规则，各块明确“最高分”、得分依据和等价达成；客观题与直接作答/作图采用对应说明。沿用现有编辑与保存入口，整数编辑、统一分配、分值错误提示和总分检查接通。 | `frontend/src/components/config/__tests__/rubric-editor-table.spec.ts`；配置编辑接口/仓库测试；桌面及 390px 宽度实际浏览器检查 |

浏览器使用实际组件与合成示例，未连接真实考试：修改 6→5 后，块分、小问合计与整卷总分同步变化，并提示总分未达 100；输入 2.5 保留无效草稿、显示错误，原分不变；改回 6 后恢复 100 分。检查了展开、收起、窄屏编辑与横向溢出。浏览器预览只验证页面行为；真实保存和回读由隔离数据库及配置编辑 API 测试覆盖。

前端类型检查、定向 ESLint、组件测试、独立生产构建与包体积检查通过。后端生成任务测试遇到 Windows 沙箱文件句柄错误 5 时，按授权在沙箱外使用同一套隔离合成数据复跑；环境拦截不计为业务缺陷。未执行全项目发布套件。

查重性能复测使用同机 2,000 道 256×256 合成带图题、20 道查询（10 重复、10 新题），各跑 5 次：旧全库解码方式中位耗时 6.2086 秒，修复后 1.5058 秒；图片读取由每次 2,010 次降至 10 次；首次建立索引 12.6581 秒。测量包含索引读取/修订检查及查询，不含文档解析和完整导入。修订检查增加了文件状态读取，因此不沿用修复前未检查内容变化时的耗时数字。

当前可复现入口：

- [评分回归](../../tests/test_flexible_grading_acceptance.py)、[查重回归及迁移演练](../../tests/test_question_dedup_acceptance.py)。全部使用隔离合成数据。
- [性能脚本](../../output/repair-20260914-dedup-grading/benchmark.py)与[性能结果](../../output/repair-20260914-dedup-grading/benchmark.json)。旧加载器只读取自 HEAD，两种方式使用相同内容识别函数；这里比较索引加载策略。
- [实际组件的合成预览](../../output/repair-20260914-dedup-grading/preview/main.js)、[隔离预览配置](../../output/repair-20260914-dedup-grading/preview/vite.config.mts)。预览端口 8037，不连接真实 API，不承担正式保存。
- [历史独立页面验收](../../output/acceptance-20260914-dedup-grading/review-inspector.acceptance.spec.ts)已复跑通过；该目录中的旧 JSON 是修复前快照，不能作为当前结论。

## 三、正式启用情况与尚未验证内容

修订索引复用既有 `question_content_index`，只增加 `source_revision` 字段，没有新增查重服务或第二套题目记录。现有 `updated_at` 只记录索引写入时间，无法判断题面、富内容或图片是否变化，因此不能替代该字段。

[038 迁移](../../migrations/question_bank/038_track_question_content_revision.sql)只为既有索引加字段，默认空值；下一次查询会重建缺修订标识的索引。已使用官方迁移器演练 037→038：升级前生成备份，既有题目、题号与出现关系保持，旧索引可刷新。

用户随后明确授权正式启用。已通过应用既有的维护预检与任务入口，为题库准备单项 038 升级；预检仅有一项待执行且完整性检查通过。维护流程分别生成准备前与应用前的受控备份，确认无正在进行的业务任务后，通过 `运行.bat` 重启并应用升级。维护状态为 `applied`；题库及阅卷库完整性均正常，待执行迁移数为零，未迁移阅卷库。

正式前端经原有完整构建入口生成，类型与包体积检查通过；8035 返回该构建的资源，配置编辑接口也已返回新增的实际作答模式、等价解法及答案上限字段。此轮浏览器控制连接不可用，重连后仍失败，因此正式页面未重新截图；桌面和窄屏布局、编辑行为以此前实际组件的合成预览验证为准。已提供正式考试配置页面入口。
本次未改写真实考试评分依据、未重批历史成绩、未调用真实模型。新提示词和接收校验的生效，不证明模型对任意数学作答都能正确评分；真实模型样例验收仍需单独授权。历史成绩缺少的步骤理由不会被补造。无法证明等价的压缩、插值或水印图像仍需核对，不承诺自动识别所有图像变体。
