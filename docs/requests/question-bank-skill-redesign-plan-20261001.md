# 题库页按技能重构与组卷工作台瘦身：实施方案

用途：交给实施代理的完整方案。视觉与交互以同目录原型 `question-bank-skill-prototype-20261001.html` 为准（合成数据，仅作外观与流程参考，不是代码来源）。

状态：**已实施**。界面已按原型校正并完成两种桌面宽度及本地真实数据的视觉、交互验收；真实数据验收只读，不调用模型。本文保留为已确认范围参考，当前产品规则见 `docs/product/KNOWLEDGE_AND_TRAINING.md`。

## 1. 已确认的决定

1. 采用“找题统一到题库页，组卷工作台瘦身”方案：
   - 题库管理页负责浏览、找题、维护标注，并直接加入试卷篮；
   - 组卷工作台只保留「试卷篮与导出」和「学情组卷助手」两个页签，默认打开「试卷篮与导出」；
   - 删除组卷工作台的「题库选题」模式（`AssemblyQuestionBrowser`），其筛选能力迁入题库页。
2. 题库页三个页签：按技能（默认）/ 按试卷 / 待处理。按技能页签内保留“按技能 / 按知识主题”切换。
3. 题目详情不再用右侧抽屉，改为题卡原地展开；展开后卡头吸顶，“收起”始终可见。
4. 允许新增**只读**后端接口；读取不调用模型、不写数据库。
5. 页面名称不变：侧栏仍为“题库管理”“组卷工作台”。

## 2. 现状（实施前必读）

| 位置 | 现在做什么 |
|---|---|
| `frontend/src/views/QuestionBankView.vue` | 试卷库首页（`PaperLibrary`）→ 点卷进入题目列表（`QuestionBankFilters` + `QuestionLedger`）→ 右侧抽屉 `QuestionInspector` |
| `components/question-bank/PaperLibrary.vue` | 试卷卡片、文件夹分组、24 份分页、跨页勾选、编辑元数据、回收站与永久删除、重新打标签、答案草稿、维护菜单（标准版本与缺口等）、任务进度行 |
| `components/question-bank/QuestionInspector.vue` | 标签核对与保存（修订保护）、精确标定教材小节、`TrainingCriterionReview`（含 `SolutionEvidenceReview`）、典型错法调整/驳回、题干/解析、原卷预览、移出题库 |
| `components/question-bank/QuestionLedger.vue` | 题卡列表、选择本页、加入试卷篮、相似题抽屉 |
| `views/QuestionAssemblyView.vue` | 三模式：`browse`（`AssemblyQuestionBrowser`，1460 行）/ `assistant`（`AssemblyAssistantPanel`）/ `edit`（`AssemblyEditorWorkspace`）；`?mode=ai` 兼容为 assistant |
| `AssemblyQuestionBrowser.vue` | 教材章节树（`curriculum_sections` + `scope_mode`）、七维标签筛选（facets 计数）、`collapseDuplicates: true`、严格教学进度 `teachingProgressChapter`、搜索排序、查看解析、相似题、试卷篮侧栏 |
| `stores/assembly.ts` | 试卷篮草稿，题库页和组卷页已共用；`addQuestions` 负责学情卷规则校验，拒绝时保留原草稿 |
| `question_bank/services/question_read_service.py` | `list_questions`、`list_facets`、`list_papers`（按 `_source_generation_token` 缓存）、`standard_summary` |
| `question_bank/solution_evidence/knowledge_links.py` | `load_point_links`：判定点→知识/技能链接（选活动发布或最新行，`link_job` 优先）；`skill_layer_report`：题量少于 5 的技能视为“题量少” |
| `question_bank/solution_evidence/part_assessments.py` | `load_profiles`：每题当前可用的详细判定版本 |

关键事实：

- 技能（`sk_` 稳定键）不是 `question_tags` 里的标签，只通过 `evidence_point_knowledge_links` 由判定点直接链接（`role='direct'`，`resolution_status='resolved'`）。现有 `/questions` 接口**不能**按技能筛题。
- “当前链接”的权威读法见 `standard_summary`：`load_profiles` 取可用判定版本，再用 `load_point_links(..., 活动发布)` 取链接。新接口必须沿用这条读法，不要直接写 SQL 按 `graph_release_id='active'` 计数（会漏掉兼容的旧发布行，也会把过期判定版本算进去）。
- 技能定义在 `knowledge_graph_node_profiles`（`definition`、`include_scope`、`exclude_scope`、`observable_evidence`、`curriculum_anchors_json`），图谱接口 `CurrentGraphNode` 已有同名字段。
- 前端解码器对题目列表做**精确键检查**（`QUESTION_LIST_KEYS` + `hasExactQuestionKeys`）。给列表项加字段时必须同步更新解码器和 `api` 测试。
- 学情数据：`POST /api/training/overview` 返回 `nodes[]`（`kind` 为 topic/skill、`section_key`、`group_mastery`、`evidence_student_count`）；请求较重（前端超时 120s），只能非阻塞加载。

## 3. 目标信息架构

### 3.1 题库管理

- 页头复用 `design-system/PageHeader`：标题“题库管理”，meta 为“教学学期 · N 题 · M 项技能”。
- 页签使用成绩中心的下划线页签样式（`results-rail`）。如果要在两处共用，把这段样式提取为公共类，不要复制一份。
- 页头操作区依次是：「试卷篮 · N」（次要）、「维护」（次要，原 `PaperLibrary` 维护菜单项全部迁入）、「上传试卷」（主要，沿用 `QuestionImportJobs` 对话框）。
- 地址栏状态：`?tab=skill|paper|todo`，并可带 `section`、`skill`（稳定键）、`topic`、`paper`、`question`。待处理跳转和返回页面时据此恢复。只放公开 id，不放题面内容。
- 册别跟随侧栏教学学期（`curriculum-scope` store），页面内不再放册别下拉。

### 3.2 按技能页签（默认）

三栏：教材章节 240px | 技能/知识主题列表 320px | 题目列表（弹性宽度）。有题卡展开时，章节栏收为 44px 细栏，可临时展开；所有题卡收起后恢复原宽。

- **左栏（教材章节）**：章 → 节，各显示题数；底部有“未挂技能 · N 题”警告行，点击后右栏列出这些题。
- **中栏**：顶部是“按技能 / 按知识主题”切换和排序（教材顺序 / 题数多→少 / 掌握度低→高）。行内容：
  - 名称；
  - “题数 · 选a·填b·解c · 难度区间小标尺（含中位点）”；
  - 可选的学情灰字“N 人有证据 · 均值 x%”；
  - 状态徽章：题数 < 5 显示“题量少”，有待审核判定点显示“k 待审”，0 题显示“暂无题”。
  - 跨小节技能（锚点只到章，例如 `*_8_0`）放在所属章下单独的“跨小节”组里。
- **右栏头部**：技能名、徽章、题数，链接“按班级学情挑这个技能的题 →”（跳到组卷工作台学情助手并预选该技能），以及默认收起的“技能定义”（可观察操作 / 纳入 / 不纳入）。
- **右栏筛选条**：
  - 搜题干、题型 chips、难度区间（复用 `DifficultyRangeFilter`）、来源；
  - 「折叠重复题」，默认开，对应 `collapse_duplicates=true`；
  - 「教学进度」，不限或已学到第 X 章，对应 `teaching_progress_chapter`；
  - 「标签筛选」浮层，维度为能力 / 方法 / 模型 / 思想 / 特殊考法，带 facets 计数，可多选，已选项在筛选条上以可移除 chip 显示；
  - “更多”里放标签完整度、判定点待审核。
- **知识主题模式**：中栏行改为知识主题；右栏改用现有 `knowledge_points` 过滤值，其余与技能模式相同。

### 3.3 题卡（三个视图共用，替代 `QuestionLedger` 卡片与 `QuestionInspector` 抽屉）

折叠态：

- 卡头：题号、来源（按试卷视图不重复卷名）、选入勾选框；
- 题干 2 行截断；
- 按技能视图显示“命中判定点：判定点 2、4 → 本技能 · 直接｜还关联：技能A、技能B”，按试卷视图显示“技能：A、B、C”。技能 chip 可点击，跳到按技能页签的对应技能；
- 关键标签：题型子类、难度、知识主题 1–2 个；
- 卡底：状态摘要“判定点 n · 技能 m · x 处待审”，链接“展开标注”“相似题”，按钮“加入试卷篮 / 已在试卷篮”；
- 开启折叠重复题时，有重复或高度相似成员的卡片显示“另有 k 道相同或高度相似题 ▸”，点开后在卡内列出成员。成员数据来源与组卷现有折叠一致。

展开态（同一列表同时只展开一张，点另一张的卡头或按 Esc 收起当前卡；收起时该卡卡头保持在可视区）：

- **卡头吸顶**：`position: sticky; top: 0`。右侧依次放「加入试卷篮」、「⋯」菜单（相似题、用这道题练习、打开原卷、移出题库）和带文字的「收起 ▲」。卡片底部不再放操作行。
- **两栏**：卡宽 ≥ 760px 时左右并排，否则上下排列。
  - 左栏：完整题干（试卷宋体，两栏时 sticky），折叠的“答案与解析”，以及“打开原卷 · 第 p 页”“用这道题练习”“相似题”链接。
  - 右栏：标注区，分两块：
    1. **判定点 × 技能**（主块）。三列：编号 | 判定点内容 | 状态。
       - 内容文字下面一行放技能胶囊（实心青色，当前技能加深，可点击跳转）和知识主题胶囊（青色描边）。胶囊文字完整显示、可换行，不截断。
       - 状态列只显示问题项（待审核、未挂技能）。全部正常时在块标题后写“n 个判定点 · 全部质检通过”。
       - 典型错法挂在对应判定点下作为缩进子行；选项触发的错法放在单独的“选项”子块。
       - 块标题行右端放「编辑标注」，进入编辑态后变为「保存 / 取消」，旁边显示“修改不会改写已有考试成绩与报告”。
       - 块下方保留现有“核对判定点 / 重新生成”入口。
    2. **题目属性**。两列定义网格：
       - 题型 | 难度（1–10 小标尺 + 数字 + 小问难度）；
       - 教材：面包屑，册 › 章 › 小节 › 知识点。未精确标定时显示警告色“小节待标定”和“选择小节”，复用原小节选择器；
       - 能力：圆点列表；
       - 解法：方法、模型、思想三组浅色方角标签，组间用“｜”分隔，各组色调不同；
       - 特殊考法：暖色标签；
       - 先修：“需先会：…”。
       - 空值整行不显示。

展开态的数据组合现有接口：`GET /questions/{id}`（标签、错法、`selectable_skills`、预览）、`GET /questions/{id}/solution-evidence`（判定点及链接）、`GET /criteria/questions/{id}`（判定点版本与审核状态）。三个请求并行发出。只有实测性能不够时才考虑合并接口。

### 3.4 按试卷页签

主从布局：左侧试卷列表 320px，右侧整卷。

- **左栏**：
  - 搜索；chips“全部 / 真卷 / 校本 / 练习”；“只看有问题”；
  - 文件夹分组规则沿用 `PaperLibrary.paperFolders`（手动文件夹、年级学期自动归类、其他学期、未归类）；
  - 卷行两行：卷名；“n 题 · Word · 日期 + 完整度细条”。行尾显示“k 待审”“k 未挂”徽章，没问题时显示“✓ 完整”。
- **右栏**：
  - 卷头：卷名、meta、「整卷加入试卷篮」、「⋯」菜单（编辑信息、继续分析、重新打标签、生成答案草稿、移入回收站）；
  - 题号导航条：每题一个方块，用颜色加图标表达状态（完整 / 判定点待审 / 未挂技能 / 分析未完成）。点题号滚动到该题并展开；
  - 题卡连续列表：按 `paper_order` 排序，不折叠重复题，保持原卷题号。
- 原 `PaperLibrary` 的全部能力必须保留：跨页勾选、批量操作、编辑元数据、回收站与恢复、永久删除影响确认、重新打标签、答案草稿、任务进度与“未完成题号”提示、删除后的“立即恢复”。这些放进左栏的选择条和各卷 ⋯ 菜单。

### 3.5 待处理页签

- 一行统计：判定点待审核题数、未挂技能题数、分析未完成题数、新词例外数（`taxonomyReview.pendingCount`）、题量少技能数（< 5，与 `skill_layer_report.underused` 同一阈值）。
- 分组列表，每行有一个“去处理”：
  - 题目类：跳到按试卷页签，选中该卷并展开该题；
  - 新词例外：打开现有 `TaxonomyCandidateReview`；
  - 判定点批量审核：打开现有 `CriteriaReviewPanel`；
  - 题量少技能：跳到按技能页签的该技能。
- 页底一句：“以上处理不会调用模型产生费用”。重新生成判定点等确实会调用模型的动作，仍走原有确认流程。

### 3.6 试卷篮抽屉（题库页）

- 右侧 360px 抽屉，内容如下：
  - 摘要：“n 题 · 选择 a · 填空 b · 解答 c”；
  - 篮中有学情助手选入的题时，显示学情卷规则说明“同技能最多 1 道、解答题最多 2 道”；
  - 列表行：序号、题号 · 来源卷、题型、移出；
  - 底部：清空、「编辑与导出 →」（跳到组卷工作台的试卷篮页签）。
- 所有加入和移出都走 `useAssemblyStore`（`addQuestions` / `removeQuestion`），规则拒绝时显示 store 消息并保留原草稿。

### 3.7 组卷工作台

- 页头改用 `PageHeader` + 下划线页签：试卷篮与导出（默认）/ 学情组卷助手；右侧放次要按钮「去题库选题」，跳到 `/question-bank?tab=skill`。
- 「试卷篮与导出」即现 `AssemblyEditorWorkspace`，内容不变。它的“← 返回选题”改为去题库。
- 「学情组卷助手」即现 `AssemblyAssistantPanel`，规则不变。它的“试卷篮 · N →”改为切换到试卷篮页签。从题库链接进入时，按传入技能稳定键预选左侧目标；目标不在当前结果中时提示，不自动改范围。
- 路由兼容：
  - `?mode=browse` 或无 mode 但带旧选题书签时，进入试卷篮页签，不再有选题模式；
  - `?mode=ai` 和 `?mode=assistant` 打开学情助手；
  - `?mode=edit` 打开试卷篮。
  - 工作台首页的入口卡（`WorkbenchView.vue` 第 120 行附近）路径不变。
- 删除 `AssemblyQuestionBrowser.vue` 及其专属样式和测试。删除前把相似题抽屉、查看解析、facets 计数、严格进度等仍需要的逻辑迁到题库页。题库页已有相似题抽屉（`QuestionLedger`），只保留一份。

## 4. 新增或扩展的只读接口

所有接口只读：不调用模型、不写库。结果按 `_source_generation_token` 缓存，与 `list_papers` 相同；数据库在请求期间变化时不写缓存。

### 4.1 `GET /api/question-bank/skill-index`

参数：`curriculum_volume_id`（必填，册 id）。响应：

```json
{
  "graph_release_id": "kgr_…",
  "curriculum_volume_id": "bnu24-math-g8-upper",
  "model_calls": 0,
  "question_count": 1334,
  "unlinked": { "no_usable_evidence": 3, "no_skill_link": 12 },
  "chapters": [{
    "id": "…", "label": "第一章 勾股定理", "question_count": 250,
    "cross_section_skills": [ /* SkillEntry，锚点只到章的技能 */ ],
    "sections": [{
      "id": "kp_bnu24_math_g8_upper_1_1", "label": "1 探索勾股定理", "question_count": 180,
      "skills": [ /* SkillEntry */ ],
      "topics": [ /* TopicEntry */ ]
    }]
  }]
}
```

- `SkillEntry`：`stable_key`、`display_name`（去掉“册｜章｜节｜技能·”前缀的短名，同时返回 `full_name`）、`question_count`、`type_counts`（选择题/多选题/填空题/解答题）、`difficulty`（`{min, median, max}` 或 null，取题目有效难度）、`criteria_needs_review_count`（复用 `_CRITERIA_NEEDS_REVIEW_SQL` 口径）、`definition`（`observable_evidence`、`include_scope`、`exclude_scope`，来自活动发布的节点档案）。
- `TopicEntry`：`filter_value`（必须是现有 `knowledge_points` 筛选参数能直接接受的标签值）、`display_name`，以及与 SkillEntry 相同的计数字段。
- 计数口径：
  - 只统计未删除题；按规范题 id 去重。
  - 一道题算入某技能，条件是它**当前可用判定版本**中任一判定点对该 `sk_` 键有已解析的直接链接。读法同 `standard_summary`。
  - 一道题挂多个技能时分别计入。
  - 技能归属小节取节点档案的 `curriculum_anchors`，只取当前册。
  - 活动发布中当前册的技能即使 0 题也要列出，前端据此显示“暂无题”。
- 未挂技能：`no_usable_evidence` 为没有可用判定版本的题；`no_skill_link` 为有可用判定版本但没有任何已解析直接 `sk_` 链接的题。

### 4.2 扩展 `GET /api/question-bank/questions`

- 新参数：
  - `skill_keys: list[str]`：命中任一即可；
  - `skill_unlinked: bool`：只看未挂技能题，覆盖上面两类；
  - `include_skills: bool`：默认 false，为 true 时每项附带下面两个字段：
    - `skills: [{stable_key, display_name}]`：该题全部直接技能；
    - `skill_hits: [{point_id, point_label}]`：仅在传了 `skill_keys` 时返回，列出命中所选技能的判定点。`point_label` 用与 `SolutionEvidenceReview` 相同的判定点显示文字。
- 实现方式：由 4.1 的同一份缓存派生“技能 → 题 id 集合”和“题 → 技能 / 判定点”映射，在 SQL 条件里追加 `q.id IN (...)`。不要另写一套链接查询。
- 前端 `QUESTION_LIST_KEYS` 解码器支持这两个可选字段；`include_skills=false` 时响应与现在完全一致。

### 4.3 扩展 `GET /api/question-bank/papers`

- 每份卷增加 `skill_unlinked_question_count`，由同一份缓存计算。
- 同步更新前端 `QuestionBankPaper` 解码器。

### 4.4 `GET /api/question-bank/facets`

- 增加 `skill_keys` / `skill_unlinked` 参数，语义同 4.2。标签筛选浮层的计数因此可以限定在当前技能范围内。

## 5. 必须保持不变的规则

- 题库页的标签保存、小节标定、错法调整与驳回、判定点审核与重新生成、移出与恢复、试卷回收站与永久删除：行为、确认、修订冲突保护和失败提示全部沿用现有 store 与 API，只改界面位置。
- 加入试卷篮只走 `useAssemblyStore`。学情卷规则（同技能最多 1 道、解答题最多 2 道、相似题受限、最高 8 级）的校验和拒绝时保留原草稿行为不变。
- 组卷候选的折叠重复题与严格教学进度沿用原接口参数和语义，只在按技能 / 按知识主题视图生效；按试卷视图保持原卷题号和出现记录。
- 技能计数与学情灰字只做展示：不改变掌握度、推荐、考试成绩、教师最终分锁或报告。
- 读取不调用模型；会调用模型的按钮（重新打标签、重新生成判定点、答案草稿）沿用原有确认与任务流程。
- 地址栏只放公开 id；题面、学生信息不进地址栏和普通日志。
- 学情灰字的加载：拿到 `/api/training/overview` 节点后按 `stable_key` 匹配并显示。该数据非阻塞：请求失败或尚未选择教学学期时，隐藏学情灰字和“掌握度低→高”排序，不影响题库浏览。不要为题库页单独发起重复诊断；有现成缓存或 store 时优先复用。

## 6. 分阶段实施

每阶段单独提交，提交前按 `AGENTS.md` 收尾（验证、测试、文档）。

1. **只读接口**：实现 4.1–4.4。
   - 后端测试优先放进已有的题库读取或 API 测试文件，例如 `tests/test_question_bank_read_cache.py` 及题库 API 测试。覆盖：计数口径（多技能、过期判定版本不计、旧发布兼容行、删除题不计）、`skill_keys` 筛选、`include_skills=false` 时响应不变、缓存随数据变化失效。
   - 用约 1500 题的合成库测冷、热请求耗时，热请求目标 < 300ms，并记录在提交说明中。
2. **题卡组件**：新建共用题卡（折叠态和展开态），把 `QuestionInspector` 的逻辑迁入展开态的标注区。`TrainingCriterionReview` 和 `SolutionEvidenceReview` 优先复用，只调整外层布局。迁移完成后删除抽屉，并改造 `question-inspector-error-patterns.spec.ts`、`question-bank-view.spec.ts` 中的对应用例。
3. **按技能页签**：三栏、章节栏收窄、知识主题切换、筛选条（含折叠重复、教学进度、标签浮层）、地址栏状态与返回恢复。
4. **按试卷与待处理**：迁移 `PaperLibrary` 全部能力到主从布局，加题号导航条和待处理页签。迁完后删除旧的卡片布局与 `QuestionBankFilters` 等已无入口的组件。
5. **试卷篮抽屉与组卷工作台瘦身**：页头与页签、路由兼容、删除 `AssemblyQuestionBrowser`，更新 `question-assembly-view.spec.ts`。
6. **收尾**：更新文档（第 7 节），视情况跑 `npm run e2e:question-bank`，最后跑一次 `tools\run_test_suite.py full` 作为最终关口。

每个前端阶段都需要人工页面验收：1440 与 1280 两种宽度；无整页横向滚动；主要动作键盘可达；Esc 与焦点返回正确；尊重减少动态效果设置（见 `docs/ui/STYLE.md`）。合成环境的测试服务与真实数据、端口隔离；重建共享 `frontend/dist` 前确认不影响其他任务。

## 7. 需要同步更新的文档

- `docs/product/KNOWLEDGE_AND_TRAINING.md`：
  - “试卷库与判定点管理”改写为三页签结构，加入未挂技能与题量少技能的口径；
  - “题库管理用于维护题目，组卷工作台用于把现有题目组成试卷…三个入口不能混用筛选含义”改写为：题库页负责找题与维护；折叠重复与教学进度只在按技能 / 按知识主题生效；组卷工作台负责试卷篮整理导出与学情组卷助手；
  - “组卷工作台提供题库选题、学情组卷助手、试卷篮与导出三个入口”改为两个页签；
  - 兼容输入：`mode=browse` 进入试卷篮。
- `docs/testing/README.md`：只有测试入口或分组变化时才更新。
- `ARCHITECTURE.md`：新增的只读接口若引入新的缓存或读取路径，在读取说明处补一句；否则不改。
- `CONTEXT.md`：本次不引入新词，不改。
- 本文件：实施完成后删除，并检查引用（`tools/check_documentation.py`）。

## 8. 不在本次范围

- 不改掌握度计算、推荐规则、组卷导出版式与学情助手的候选算法。
- 不新增题库数据字段，不做数据迁移，不批量重算判定点或链接。
- 不改命题练习、知识与训练页面。

## 9. 实施时需要先核实的点

- 典型错法挂到判定点下：错法的 `trigger_kind='step'` 时，`trigger_value` 来自批改评分步骤 id（见 `backend/error_patterns.py`），不一定等于详细判定点的 `evidence_point_id`。先核实映射关系。能唯一对上时挂到对应判定点下，对不上时放进“其他错法”子块，不猜测。
- `display_name` 短名：节点档案的 `display_name` 是全路径（“册｜章｜节｜技能·名”）。短名用最后一段并去掉“技能·”前缀，与 `QuestionLedger.similarReasonText` 同一规则，建议提取为一个公共函数。
- 跨小节技能：若某技能锚点跨多个小节，计入每个锚点小节，并在行内标“跨小节”。
- `/api/training/overview` 若没有可复用的前端缓存，题库页只在用户选择“掌握度低→高”排序或首次进入按技能页签空闲时再请求，避免拖慢首屏。
