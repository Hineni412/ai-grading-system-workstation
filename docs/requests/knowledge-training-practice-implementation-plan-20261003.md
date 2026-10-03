# 按学生／按章节训练改版、批量错题本与刷题讲义实施方案

用途：交给实施代理的完整说明。用户已认可原型方向（`docs/requests/knowledge-training-practice-prototype-20261003/`，本机浏览器直接打开 `index.html`，或在该目录运行 `python -m http.server`）。本文写明原型之外的数据来源、规则、改动位置、测试和文档更新；原型与本文冲突时以本文为准（差异见第 2 节）。

状态：已实施。按学生／按章节三栏、三种出卷用途、讲义巩固上限与知识顺序、批量错题本和证据页入口已实现。相关后端、页面及模拟接口浏览器测试、前端构建和文档检查通过。真实应用已核对 48 人的讲义／训练卷构成、草稿放弃暂存、跨两个班的三人错题本导出和下载回执。项目只有两个班，未覆盖原定三个不同班的组合；Word 已检查分节、来源与技能注记、作答区及答案位置，缺少文档渲染器，逐页打印版式尚未验收。没有调用模型。

实施前必读：`AGENTS.md`（授权、收尾三行、单次写入 ≤15KB）、`docs/product/KNOWLEDGE_AND_TRAINING.md` 第 171–185、205–232、257–279、310–314 行、`docs/ui/STYLE.md`、`docs/testing/README.md`。

本方案不调用模型、不产生费用；训练卷回收批改流程不变。

## 0. 前置条件与并行协调

以下文件在本文写作时含其他任务的未提交改动。每个阶段开始前运行 `git status --short -- <文件>`；涉及的文件仍有他人未提交改动时，停止该阶段并报告，不覆盖、不暂存他人修改：

- 前端：`frontend/src/views/TrainingRecommendationsView.vue`、`components/knowledge-training/ChapterTrainingMatrix.vue`、`components/knowledge-training/TrainingGroupRecommendations.vue`、`stores/training.ts`、`api/training.ts`、`__tests__/training-recommendations-view.spec.ts`、`__tests__/training-api.spec.ts`。
- 后端：`question_bank/recommendation/personalized.py`、`question_bank/exporters/paper_docx_exporter.py`、`backend/api/routers/training.py`、`backend/jobs/default_handlers.py`。
- 文档：`docs/product/KNOWLEDGE_AND_TRAINING.md`、`docs/testing/README.md`、`ARCHITECTURE.md`。

学情总览／知识结构改版（`knowledge-training-redesign-implementation-plan-20261003.md`）已实施：总览与地图的“给这 N 人出训练卷”写入 `mode: 'selected'` 的证据范围并调用 `presetFocusedTraining`，再跳到按学生训练。本方案必须继续支持这条入口（见 5.1）。

## 1. 已确认的产品决定

1. 按学生训练、按章节训练两页统一为三栏：左栏选人（学生名单／章节），中栏选内容（训练范围／推荐小组），右栏为同一个出卷面板。窄于 1200px 时上下排列，出卷面板在最后。“生成试卷”页签（审核 → 打印 → 回收）保持现有流程。
2. 出卷面板顶部三选一：训练卷 | 刷题讲义 | 错题本。只显示当前用途的参数。
3. 错题本可批量选择任意学生（跨班）和本学期考试场次；按“章 → 节”分节，节内同技能相邻、由易到难、相似题相邻；每题可标注来源（考试 · 题号），可在每题后留作答空白，两项默认开启；答案解析集中在末尾。
4. 错题本可按章节过滤：中栏“综合”收所选考试的全部错题；“专项”只收所勾章节的错题。
5. 刷题讲义采用方案 B：先补弱，再按上限加入新练习与巩固题；不用无关题凑数，不足保留缺口。题量无产品上限。
6. 学生名单按班级分组直接列出（不再要求先输入筛选条件），每班可折叠、可“全选本班”。
7. 小组卡片增加难度一行，用来区分目标相同但难度不同的小组。
8. 学生作答证据页的“导出错题本”保留为入口，改为跳到按学生训练并预选该生、用途切到错题本。

## 2. 与原型的差异（以本文为准）

- **训练卷／讲义不做生成前题量估算。** 真实题量要跑完整的选题才知道，等于生成草稿本身。面板只写各项上限；草稿审核页逐人显示真实构成“补弱 a · 巩固 b · 新练习 c ＝ t 题”和缺口原因（见 5.9）。错题本的统计来自真实预览接口，保留。
- **“每个薄弱技能 N 道”改称“同一技能最多 N 道”。** 现有上限 `max_questions_per_skill` 按整题实际直接技能计数，补弱、巩固、新练习合计受限；不另建只管补弱的字段。
- **分节只有一级：章·节。** 标题如“第一章 勾股定理 · 1 探索勾股定理”。技能不单独成标题，而是节内同技能相邻，并在每题前的小字注记中写“技能：xxx”。
- **小组卡片写“目标难度 a–b 级”。** 取值为该组各目标 `target_difficulty` 的最小值到最大值，不是成员的可练难度带（后端不返回成员难度带）。
- 学生抽屉不显示逐场错题数。
- 删除按章节页原有的“学生明细／群体”矩阵视图和“手动选目标同卷”入口。手动多人同卷改由按学生页“多人同卷”完成；推荐小组的增删成员、删目标保留。
- 训练卷一人一卷保持现规则：新练习上限 4、巩固 0，不在面板显示。讲义多人同卷只显示同一技能上限，巩固／新练习上限只用于一人一卷。
- 按学生页默认不勾选学生（原型为全选），避免误生成全体草稿。

## 3. 复用与新建清单

| 需要 | 复用 | 新建／删除及原因 |
|---|---|---|
| 范围条 | 沿用 `knowledge-overview.css` 的 `.knowledge-overview-scope-bar` 样式 | 新建 `TrainingScopeBar.vue`：`OverviewScopeBar.vue` 绑定 `mastery-overview` store，挂载即触发总览读取，不能直接用于训练页 |
| 学生名单 | 诊断结果 `diagnosis.students`（含 `score_rate`、`weak_points[].tier`） | 新建 `StudentPicker.vue`；删除 `EvidenceScopeFilters.vue`（只被本页使用，队列交互被替代） |
| 章节范围／章节列表 | `curriculumScope.selectedVolume` 章节顺序、`diagnosis.knowledge_catalog` 父子关系、`resolvePaperScope` | 新建 `KnowledgeRangeList.vue`，两页共用；删除 `TrainingKnowledgeStructure.vue`、`ChapterTrainingMatrix.vue`（只被本页使用） |
| 推荐小组 | `TrainingGroupRecommendations.vue` 原样复用，只加难度一行 | 无 |
| 出卷面板 | 重写 `PaperSettingsPanel.vue`（保留文件名） | — |
| 错题本导出 | `WrongQuestionBookDialog.vue` 的预览／提交／按编号核对／下载逻辑移入 composable | 新建 `features/training/wrong-question-book-export.ts` 与 `WrongQuestionBookExport.vue`；删除对话框 |
| 知识顺序 | `question_skill_index.py` 的技能锚点解析、`build_skill_snapshot`、`similarity_service.text_similarity` | 新建 `question_bank/services/knowledge_order.py`：错题本与讲义共用唯一排序实现 |
| Word 导出 | `export_question_paper_docx` 的 `sections`、`include_answer_space` | 只加可选参数 `question_notes` |

## 4. 后端

### 4.1 知识顺序（新建 `question_bank/services/knowledge_order.py`）

先把 `question_skill_index.skill_index` 内部的锚点解析（`anchor_key` 闭包与“第 3 级锚点取父级”）提为模块级函数 `skill_anchor_ids(node_row, volume) -> list[str]`，`skill_index` 改为调用它，行为不变（`tests/test_question_bank_read_cache.py` 现有用例须原样通过）。

```python
@dataclass(frozen=True)
class Placement:
    chapter_id: str; chapter_order: int; chapter_label: str
    section_id: str          # 跨小节技能为 ""，归入该章末尾的“本章综合”
    section_order: int; section_label: str
    skill_key: str; skill_name: str   # 无技能时为 ""

@dataclass(frozen=True)
class OrderEntry:
    question_id: int; difficulty: float | None; question_type: str; question_text: str

@dataclass(frozen=True)
class KnowledgeSection:
    title: str; section_id: str | None; question_ids: list[int]

def skill_placements(conn, skill_keys, volume_id) -> dict[str, Placement]
def question_primary_skills(conn, db_path, data_root, question_ids) -> dict[int, str]
def section_placements(conn, question_ids, volume_id) -> dict[int, Placement]   # 无技能时的 curriculum_section 兜底
def knowledge_sections(entries: Sequence[OrderEntry], placements: Mapping[int, Placement | None]) -> list[KnowledgeSection]
```

规则：

- **技能落位**：读取当前 active 知识图谱发布的 `knowledge_graph_node_profiles.curriculum_anchors_json`，取本册第一个第 2 级（节）锚点；只有第 1 级（章）锚点时归入该章“本章综合”（排在该章各节之后）；本册无锚点时不落位。
- **题目主技能**：`build_skill_snapshot(conn, db_path, data_root, question_ids=...)["by_question"][qid]` 中第一个 `sk_` 键，即判定点顺序中第一个直接技能。没有技能时，用 `question_tags` 里 `curriculum_section` 与本册节显示名相同的值落位到节，技能为空。都没有时不落位。
- **分节顺序**：按章序、节序；“本章综合”在该章最后；未落位的题放最后一节，标题“未归入章节”。节标题为 `f"{chapter_label} · {section_label}"`，“本章综合”为 `f"{chapter_label} · 本章综合"`。
- **节内顺序**：先按技能分组，技能组按“组内最低难度”升序、再按技能名排序；无技能的题在节内最后。组内按难度升序（缺难度排最后），再按题型（选择 → 填空 → 解答）、题号。
- **相似题相邻**：在每个技能组内，按上面的顺序单遍处理：对当前题，在后面尚未放置的同组题中找 `text_similarity(question_text) ≥ 0.7` 且最相似的一道，移到当前题之后。只在组内比较，不跨技能。
- 难度统一用题目 `difficulty` 字段，按 `personalized._difficulty` 同样的解析方式处理（讲义直接用草稿项的 `difficulty`）。

### 4.2 错题本

`backend/students/wrong_question_book.py`：

- `build_wrong_question_books(db, qb_path, student_ids, volume_id, session_ids=None, scope_keys=())`。
- 判定错题、教师最终分优先、未知分不算错题、多小问按父题关联、缺题库原题列入 `missing_items` 的规则不变。
- **去重改为“一题一次、来源合并”**：每名学生同一 `bank_question_id` 只收一次，`sources` 按考试时间列出全部失分场次和题号（`question_id_coordinates` 取父题号，显示“第5题”）。
- 对可导出的题调用 `question_primary_skills` 与 `skill_placements`（无技能时用 `section_placements`）。`scope_keys` 非空时只保留 `placement.section_id` 或 `placement.chapter_id` 在范围内的题，其余计入 `out_of_scope_count`；`scope_keys` 为空时不过滤，未落位的题进入“未归入章节”。
- 每本 `book["sections"]` 改为 `knowledge_sections(...)` 的结果；`book["notes"] = {bank_id: "技能：X · 来源：第三周学情反馈 第5题、第四周学情反馈 第7题"}`，无技能时省略“技能”部分。
- 返回值新增：`session_wrong_counts`（按场次统计所选学生的可导出错题数，统计单位为“学生 × 原题”，不受所选场次影响）、`out_of_scope_count`、`empty_students`（`[{student_id, student_name, reason}]`，原因为“没有错题”“错题均缺少题库原题”“错题均不在所选章节”）。

`backend/api/schemas/students.py` 与 `backend/api/routers/students.py`：

- 删除 `POST /api/students/{student_id}/wrong-question-book/preview`（只被将要删除的对话框使用）。
- 新增 `POST /api/students/wrong-question-books/preview`，请求体为 `WrongQuestionBookPreviewRequest`：
  - `curriculum_volume_id`；
  - `student_ids: list[int]`，1–500 个，正整数，去重排序；
  - `session_ids: list[int] | None`；
  - `scope_keys: list[str]`，最多 50 个，须为 `kp_` 或 `ki_` 开头，规范化方式同 `PersonalizedRecommendationCreateRequest.normalize_identity_keys`。

  响应为 `build_wrong_question_books` 去掉 `books` 的部分。学生不存在或考试不属于本学期时，返回 422 `wrong_question_scope_invalid`（沿用现有 ValueError 映射）。
- `WrongQuestionBookSubmitRequest` 新增 `scope_keys`（同上校验）、`include_source_label: bool = True`、`include_answer_space: bool = True`。幂等比较仍用完整 payload，所以同一编号、选项不同会返回 409。

`backend/jobs/wrong_question_export.py`：

- 用 `book["sections"]` 生成 `SectionSpec`。
- 传 `include_answer_space=payload["include_answer_space"]`。
- 传 `question_notes`：`include_source_label` 为真时用完整注记，为假时只保留“技能：X”。
- 页眉、文件名、ZIP、部分失败、取消规则不变。

### 4.3 刷题讲义：巩固题、知识顺序、导出分节

请求与配置：

- `PersonalizedRecommendationCreateRequest` 新增 `max_consolidation_questions: int = Field(default=0, ge=0)`。
- 路由 `create_personalized_draft` 传入配置：
  - `max_consolidation_questions`：只在 `paper_mode == "individual" and purpose == "handout"` 时取请求值，否则为 0；
  - `max_unmeasured_questions`：现有规则不变。
- 路由中“没有 weak_points 的学生不进入草稿”的过滤（约第 445–450 行）：一人一卷且 `max_unmeasured_questions > 0 or max_consolidation_questions > 0` 时保留该生。
- `PersonalizedRecommendationConfig` 新增 `max_consolidation_questions: int = 0`，加入整数校验列表（最小 0）；`_config_constructor` 读取，缺省为 0（兼容输入）。确认 `to_dict` 输出该字段。
- `backend/training_assessment/evidence.py` 约第 1097 行有一份配置键清单。先查明其用途：如果用于下一轮草稿继承设置，就把新键加进去；如果不是，报告后再决定。
- **请求指纹兼容**：`create` 中现有的兼容指纹（去掉 `max_unmeasured_questions`、旧版 legacy 配置）必须原样保留。另外，当 `max_consolidation_questions == 0` 时，对每一种现有指纹再生成一份去掉该键的版本，确保旧请求编号重试仍能对应到原草稿。

选题（`_choose_practice_entries` 的 `personal_remediation` 分支）：

- 原顺序“补弱 → 单题替换优化 → 未测目标新练习”之后，新增第四步“巩固”：
  - 候选：在过滤前的原始分组中，取含 `_is_core(e) and practice_purpose == "consolidation"` 条目、且不含核心补弱条目、尚未入选的题；
  - 循环条件：`added < max_consolidation_questions and len(selected) < question_count`，并且每题都要通过 `_paper_diversity_allowed`；
  - 排序：`(repeated_consolidation_only(group), -len(组内技能键 - 已练技能), _pattern_count, 最低 match_level, 距离中位数, -最大 preference, question_id)`。
- 上限为 0 时，行为与现在完全一致。
- 换题（约第 3487–3491、3504–3507 行两处过滤）：被换的题为巩固题且上限 > 0 时，允许换成核心巩固候选，并计入同卷巩固题数上限。
- 草稿提示（约第 2520–2522 行）：上限 > 0 时补一句“再加入最多 K 道巩固题”。

题序：

- `_order_practice_items(items, placements=None)`：传入 placements 时按 `knowledge_sections` 排序并连续编号，同时给每项写 `knowledge_section = {"id", "title"}` 和 `primary_skill_name`；不传时保持现有的整题难度升序。
- 三处调用（约第 2517、3452、3529 行）：只在 `config.purpose == "handout"` 时，用 `skill_placements` 计算 placements（主技能取该项 `matched_key`；不是 `sk_` 时取题目 `stable_keys` 中第一个 `sk_`），需要时用 `connect(self.db_path)` 打开只读连接。训练卷与班级固定卷（`teacher_fixed_class`）题序不变。

导出（`backend/jobs/training_handout.py`）：

- 题目按 `item_order` 排列；按 `knowledge_section.title` 的连续段生成 `SectionSpec`；`question_notes = {question_id: "技能：X"}`。
- 旧草稿项缺少 `knowledge_section` 时保持现有的不分节导出。

### 4.4 Word 导出注记（`paper_docx_exporter.py`）

`export_question_paper_docx` 新增 `question_notes: Mapping[int, str] | None = None`。分节、按题型、平铺三种渲染分支都传给 `_render_question_body`，在题目正文前插入一个独立段落（9 磅、灰色 `#666666`）。不传时输出与现在逐字节一致，现有调用方不受影响；`save_validated_legacy_export` 的校验须通过。

## 5. 前端

### 5.1 页面骨架与状态（`TrainingRecommendationsView.vue`）

- `mode=student|chapter` 渲染 `.practice-layout`：≥1200px 为三栏（300px / 1fr / 340px，右栏 sticky），以下单栏。`mode=paper` 保持现状。
- **证据范围**改由 `TrainingScopeBar` 决定：全部学生或单个班级，加最低考试得分率。写入 `training.setStudentScope({ mode: 'all' | 'class', classIds, scoreRateMin })` 后调用 `analyze()`；按章节页保留现有“首读带分组”逻辑。班级列表仍来自 `fetchStudents`。
- **选择**：新增 `selectedStudentIds`（持久化）。每次诊断更新后，只保留仍在 `diagnosis.students` 中的 id，并保持原有顺序。
- **一人一卷与多人同卷**：草稿用 `scope = { mode: 'selected', student_ids: selectedStudentIds }`，`paperDiagnosis` 只含所选学生；`resolvePaperScope` 推断已学进度时，也只用所选学生的诊断。
- **按学生页多人同卷**：`paper_mode: 'shared'`，`scope_keys` 取范围键，`target_keys` 为空。后端隔离测试已确认能生成相同题目与题序的公共草稿；请求校验接受非空范围键。
- **兼容入口**：进入页面时，如果保存的证据范围是 `mode: 'selected'` 或 `'student'`（例如来自总览“给这 N 人出训练卷”），把范围条设为这些学生共同所在的班级（班级不止一个时设为全部学生），并把 `selectedStudentIds` 设为这些 id。`presetFocusedTraining` 预填的技能与小节照常生效，进入专项模式，不自动生成草稿。

### 5.2 `TrainingScopeBar.vue`

- props：`volumeLabel`、`sessionCount`、`classes`、`selectedClass`、`scoreFloor`（0–1 或 null）。
- emits：`select-class`、`update-score-floor`。
- 得分率输入框以百分数显示，留空表示不限；失焦或按回车时才生效，不在每次按键时请求。
- 样式沿用 `.knowledge-overview-scope-bar`。

### 5.3 `StudentPicker.vue`（左栏，按学生页）

- **筛选**：姓名／学号检索、“只看有明显薄弱点”。
- **分组**：按 `class_id` 分组，班级名按中文排序；组头显示“9班（22 人）· 全选本班”，可折叠；本班已全选时，再点为取消本班。
- **顶部**：“已选 n 人 · 清空”。
- **每行**：复选框、姓名按钮（点击打开抽屉）、学号、得分率标签、“薄弱 n”。
  - 得分率标签：≥80% 浅绿、60%–<80% 浅黄、<60% 浅红；`score_rate_source !== 'current_exam'` 时显示“无成绩”（中性色）。
  - 薄弱 n：去重后 `tier === 'weak'`、且目录 `node_kind` 为 skill 或 topic 的条目数。
  - 状态同时用文字表达，不只靠颜色。

### 5.4 `StudentQuickView` 抽屉

- 内容：姓名、学号 · 班级、本学期考试得分率、明显薄弱和还不稳的条目（按掌握度升序，最多 10 条，显示百分比和档位文字）。
- 按钮：
  - “只给此人出错题本”：选择改为仅该生，用途切到错题本，关闭抽屉；
  - “查看作答证据”：RouterLink 到 `student-evidence`，`query.from = 'student'`。
- Esc 关闭。
- 先检索设计系统中是否已有抽屉组件；有就复用，没有就写一个带遮罩的简单侧栏。

### 5.5 `KnowledgeRangeList.vue`（中栏，按学生页；左栏，按章节页）

- **结构**：章节结构取 `curriculumScope.selectedVolume.chapters[].sections[]`（按 `order` 排序）；条目归属顺着 `knowledge_catalog` 的父链找到节。
- **每节一行**：
  - 四档条：对每名统计学生取该节条目中最弱的档位（明显薄弱 > 还不稳 > 较稳定 > 证据不足），该节没有观测的学生不计入；
  - 文字“明显薄弱 n 人”，n 为 0 时不显示；
  - 可展开，列出该节各技能／知识点的“明显薄弱 a 人 · 还不稳 b 人”。
- **图例**：顶部一行图例，并注明统计依据——按学生页写“按已选学生统计，每人取本节最弱档位”，按章节页写“按当前范围学生统计，每人取本节最弱档位”。按学生页未选学生时，提示“勾选学生后显示”。
- **两种模式**：
  - `select-one`（按章节页）：点击章或节，写入 `chapterKey` / `sectionKey`。
  - `range`（按学生页）：
    - 综合／专项分段切换；
    - 综合模式显示“已学到”下拉框（原 `progressChapters` 与 `teachingProgressChapterId`），已学到之后的章节灰显并标“未学”；
    - 专项模式在章和节上显示复选框，勾章等于勾全部节，写入 `rangeKeys`。
- 用途为错题本时，顶部显示一行提示：“错题本：综合＝所选考试的全部错题；专项＝只收所勾章节的错题”。错题本的 `scope_keys` 在专项时为 `rangeKeys`，综合时为空（综合也不按“已学到”过滤）。

### 5.6 按章节页

- 左栏：`KnowledgeRangeList`（select-one）。
- 中栏：`TrainingGroupRecommendations`，传入的范围键为 `sectionKey || chapterKey`。
- 小组卡片在统计区增加“目标难度 a–b 级”：取 `targets[].target_difficulty` 非空值的最小值和最大值，四舍五入；两值相同时写“a 级”，没有值时写“难度未知”。
- 采用、编辑、重合确认、暂未推荐原因保持现状。
- 右栏面板标题为“给所采用小组 · m 人出”；尚未采用小组时，面板禁用并提示“先在中间采用一个小组”。

### 5.7 出卷面板（重写 `PaperSettingsPanel.vue`）

- **props**：`context: 'student' | 'group'`、`studentIds`、`volumeId`、`scopeKeys`、`valid`、`blockedReason`、`generating`。
- **v-model**：`purpose: 'training' | 'handout' | 'wrong_book'`、`paperMode`（仅按学生页）、`questionCount`、`difficultyMax`、`maxQuestionsPerSkill`、`maxWrittenQuestions`、`recentActivityCount`、`maxConsolidationQuestions`、`maxUnmeasuredQuestions`。
- **用途说明**：沿用原型文案。训练卷一行写明“批改调用模型、产生费用，批改前另行确认”。
- **训练卷**：每卷题数（8–12）、难度上限（1–10）；“选题细则”内为同一技能最多、解答题最多、近期原题排除次数（0 = 不排除）。
- **讲义**：
  - 每人题数上限（多人同卷为“每卷题数”，≥1）、难度上限；
  - 一人一卷时显示“题量结构”：同一技能最多（默认 3）、新练习最多（默认 6）、巩固题最多（默认 6），附说明“先补弱，再按上限加入新练习和巩固题；不用无关题凑数，不足保留缺口。同一技能上限对三类题合计生效。”；
  - 多人同卷只显示同一技能最多；
  - “细则”内为解答题最多（默认 8）、近期原题排除次数（默认 3，附注“只排除考试原题，可复用历史训练题”）；
  - 一行排版说明：“按章节分节，节内同技能相邻、由易到难；答案解析在末尾。”
- **错题本**：嵌入 `WrongQuestionBookExport`（5.8）。
- **校验**：沿用现有数值校验；巩固、新练习上限为 0 到题数之间的整数。禁用时显示原因（未选学生／未采用小组／数值不合法）。
- **主按钮**：“生成训练卷草稿 →”或“生成讲义草稿 →”，走现有 `goPaper(mode)`。
- **按用途分别记住设置**：训练卷与讲义各存一套，切换用途时换成该用途上次的设置。训练卷默认：10 题、同技能 1、解答 2、近期 3、难度 8。讲义默认：30 题、同技能 3、解答 8、近期 3、难度 8、巩固 6、新练习 6。

### 5.8 错题本导出（`WrongQuestionBookExport.vue` + `features/training/wrong-question-book-export.ts`）

- **逻辑来源**：`WrongQuestionBookDialog.vue` 的提交、按请求编号核对、下载、下载后清除、结果未知时不自动重复提交这些逻辑，原样移入 composable。
- **本地存储**：改为单个键 `ai-grading:wrong-question-book:v2`，保存 `{ token, downloaded, fingerprint }`。指纹由学生、场次、范围键和两个开关组成；当前选择与已保存的指纹不一致时，不恢复旧任务。
- **预览**：学生、场次、范围键任一变化时，防抖 300ms 后调用新预览接口，并中止上一次请求。
- **考试场次**：复选框来自 `preview.sessions`，默认全选，每场旁显示“已选学生错题 n 道”（`session_wrong_counts`）。重新预览后，用户取消勾选的场次如果仍然存在，保持未勾选。
- **开关**：每题标注来源（考试 · 题号）、每题后留作答空白，默认都开启。
- **统计行**：“n 人 · 可导出 x 道错题 · 缺题库原题 y 道 · z 人无错题不生成”；专项模式下加“ · 不在所选章节 w 道”。可展开查看缺原题清单（学生 · 考试 · 题号）和未生成学生及原因。
- **按钮**：“导出错题本（n 份 Word · ZIP）”，只有 1 人时为“导出错题本（Word）”；之后依次显示进度、“下载全部错题本”或“下载错题本”、“重新选择导出”。

### 5.9 草稿审核页（`PersonalizedRecommendationDraft.vue`）

- **新 props**：`maxConsolidationQuestions`、`maxUnmeasuredQuestions`。
- **请求取值**：

  | 场景 | 新练习上限 | 巩固上限 |
  |---|---|---|
  | 一人一卷训练卷 | 4 | 0 |
  | 一人一卷讲义 | 面板值 | 面板值 |
  | 多人同卷 | 0 | 0 |

- **设置指纹**：加入这两个字段，并把指纹版本号加一。
- **一人一卷**：每名学生头部显示“补弱 a · 巩固 b · 新练习 c ＝ t 题”，按 `item.practice_purpose` 计数；有缺口时沿用现有缺口文案。
- **讲义**：在题目列表中，`knowledge_section.title` 变化处插入节标题行，每题显示 `primary_skill_name`。
- 选题设置摘要（约第 899 行）在讲义时增加巩固和新练习上限。

### 5.10 会话存储（`paper-selection-session.ts`）

- **新增字段**：`selectedStudentIds: string[]`；`purpose` 增加 `'wrong_book'`；`trainingRules`、`handoutRules`（5.7 的两套设置）；`wrongBook: { sessionIds: number[] | null; includeSourceLabel: boolean; includeAnswerSpace: boolean }`。
- **版本迁移**：`rulesVersion` 升到 8。从 7 迁移时，原有的平铺字段按当时的 `purpose` 放入对应的规则组，另一组取默认值。
- **新增 `presetWrongQuestionBook({ studentId, classId })`**：选择设为该生，用途设为错题本，范围模式设为综合；证据范围存为该生班级。
- `presetFocusedTraining` 签名和现有测试不变。

### 5.11 学生作答证据页（`StudentEvidenceView.vue`）

“导出错题本”按钮改为调用 `presetWrongQuestionBook`，再执行 `router.push({ name: 'training', query: { mode: 'student' } })`。删除对话框的引用和 `exportDialogOpen`。

### 5.12 删除

- 组件：`EvidenceScopeFilters.vue`、`ChapterTrainingMatrix.vue`、`TrainingKnowledgeStructure.vue`、`WrongQuestionBookDialog.vue`。
- 测试：`evidence-scope-filters.spec.ts`、`knowledge-training-hierarchy.spec.ts`。其中仍适用的断言（例如小问主题归属的显示）先迁移到新组件的测试。
- API：`api/students.ts` 中的 `previewWrongQuestionBook`。
- 样式：`styles/training-recommendations.css`、`styles/knowledge-graph.css` 中因此不再使用的规则。

删除前全局检索，确认没有其他引用。

## 6. 测试

按 `AGENTS.md` 的顺序：优先在已有测试中加断言，其次在模块现有文件中加函数，最后才新建文件。新测试覆盖了旧测试时，删除旧测试。

后端：

- `tests/test_question_bank_read_cache.py`：
  - 抽出锚点函数后，`skill_index` 的现有用例原样通过；
  - 新增 `knowledge_sections` 用例：章节顺序、“本章综合”排在章末、“未归入章节”排最后、节内技能组按最低难度排序、组内难度升序且缺难度排最后、相似度 ≥ 0.7 的题紧跟、不跨技能比较相似。
- `tests/test_api_student_routes.py`，扩展现有错题本用例：
  - 跨班任意学生的批量预览；
  - `scope_keys` 过滤并计入 `out_of_scope_count`；
  - 分节和节内顺序；
  - 同一原题在多场失分时只收一次，`sources` 列出全部场次；
  - `session_wrong_counts` 与 `empty_students` 及其原因；
  - 旧的单生预览路由返回 404；
  - 提交时两个开关和 `scope_keys` 进入 payload，同一编号、选项不同返回 409；
  - 导出任务（使用假 `docx_exporter`）收到正确的 `SectionSpec`、`question_notes` 和 `include_answer_space`。
- `tests/training/test_personalized_recommendation.py`：
  - 讲义一人一卷：巩固题不超过上限，且只在补弱和新练习之后补入；
  - 上限为 0 时选题结果与改动前一致；
  - `max_consolidation_questions == 0` 时，旧请求编号用旧配置重试仍复用原草稿；
  - 巩固题换题后仍为巩固题；
  - 讲义题序按知识顺序，项上写入 `knowledge_section`；
  - 训练卷题序仍按难度；
  - 讲义导出（扩展约第 2456–2497 行的现有用例）按节分段并带技能注记，旧草稿缺少 `knowledge_section` 时不分节。
- `tests/test_api_training_routes.py`：
  - 训练卷或多人同卷请求中的巩固上限被置为 0；
  - 巩固上限 > 0 时，没有薄弱点的学生仍进入草稿；
  - 按学生页多人同卷（只给 `scope_keys`，不给目标键）能生成草稿（对应 5.1 的待验证项）。
- `tests/test_question_document_pipeline.py`：传 `question_notes` 时题前出现注记段落；不传时输出与原来一致。

前端（vitest）：

- `training-recommendations-view.spec.ts`，改写：
  - 范围条请求 all／class 诊断；
  - 勾选的学生子集成为草稿 scope；
  - `mode: 'selected'` 的兼容入口预选学生；
  - 采用小组后右栏可用；
  - 切换用途时换成对应的规则组；
  - 用途为错题本时，`scope_keys` 只在专项模式下传入。
- `training-group-recommendations.spec.ts`：目标难度一行的三种显示。
- `personalized-recommendation-draft.spec.ts`：请求字段取值、指纹、逐人构成摘要、讲义节标题行。
- `student-evidence-view.spec.ts`：按钮写入预设并跳转，页面不再有对话框。
- `training-api.spec.ts`：新预览接口的请求和响应校验。
- 新建 `practice-selection.spec.ts`，覆盖 `StudentPicker` 与 `KnowledgeRangeList`（新模块，没有对应的现有测试文件）：检索、只看薄弱、全选本班、清空、四档条“每人取最弱档位”、无观测学生不计入、综合模式灰显“未学”、专项勾章等于勾全部节。
- `knowledge-overview-view.spec.ts` 中 `presetFocusedTraining` 的用例须原样通过。

浏览器：更新 `npm run e2e:training-recommendations` 对应的 Playwright 用例，接口用 mock 模拟：三栏布局及 1200px 以下的单栏、学生勾选 → 讲义设置 → 进入生成试卷页、错题本预览 → 导出 → 下载。

验证命令（只跑覆盖改动的部分）：

- `npm run build`；
- 上面列出的 vitest 文件；
- `runtime/python/python.exe -m pytest` 运行上面列出的 pytest 文件；
- `npm run e2e:training-recommendations`。

重建共享的 `frontend/dist` 前，先确认没有其他任务在使用。最终提交前跑一次 `tools/run_test_suite.py quick`。

## 7. 文档（与代码同一次提交）

- `docs/product/KNOWLEDGE_AND_TRAINING.md`：
  - “共享证据范围”：改写错题本入口的说明；
  - “学生错题本”：改写为批量选人、按章节过滤、分节和排序、来源合并、注记、留空白、入口位置；
  - “按章节分组”：删除第 208 行“学生明细和群体加权结果保留为核对与手动同卷入口”，补充目标难度；
  - “按学生”：名单按班级直接列出、勾选子集、抽屉、多人同卷；
  - “范围与题量”第 271、275、276 行：讲义的巩固上限、四步选题顺序、讲义按知识顺序排题、两套默认设置；
  - “批量出卷”第 313 行：讲义按章节分节和技能注记。
- `CONTEXT.md`：
  - 修改“补弱优先个人卷”：讲义可另加巩固题；
  - 修改“讲义”“错题本”：错题本改为来源合并，不再只保留最早一场；
  - 新增词条“巩固题”：练习本人已有全对作答、没有失分的技能，不计入失分需要覆盖。
- `docs/testing/README.md`：删除 `knowledge-training-hierarchy` 等已删测试的引用，加入 `practice-selection`。
- `ARCHITECTURE.md`：后台任务表里如果没有错题本导出，补一行（`backend/jobs/wrong_question_export.py`，临时产物位于 `wrong_question_books/`）；有则核对。
- 本文：实施后把状态改为已实施，并写明验证限制。

## 8. 真实数据验收（由主代理执行，不委派）

全部实施并通过测试后，在本机应用中通过应用自身流程操作，不直接写数据库，不调用模型：

1. 选一个班，按学生页全选，生成讲义一人一卷草稿（默认设置）。逐人记录补弱、巩固、新练习题数和缺口原因。用同一批学生生成训练卷草稿作对比。统计单位为“学生 × 题”，并写明学生数。
2. 选三名不同班的学生导出错题本，核对 Word 的分节、顺序、来源注记、留空白和答案位置。
3. 两份草稿都是应用自身流程产生的记录，核对完成后在应用中放弃暂存。

## 9. 分阶段提交

每个阶段一个本地提交，提交只含该阶段的内容，并按 `AGENTS.md` 写收尾三行。

1. 后端：知识顺序与错题本（4.1、4.2、4.4 及对应测试）。
2. 后端：讲义巩固题、题序与导出分节（4.3 及对应测试）。
3. 前端：两页改版、出卷面板、错题本导出、入口与删除（第 5 节及对应测试）。
第 7 节所列文档随改变该规则的阶段一起提交，不单独成为一个阶段。三个阶段完成后，运行 `tools/check_documentation.py`，并逐项核对第 7 节。
