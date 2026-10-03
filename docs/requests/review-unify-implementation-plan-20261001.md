# 人工复核页统一与按步骤复核：实现文档

- 日期：2026-10-01
- 用途：交给实现代理的方案说明。产品决定已经由用户确认，实现时不要重新设计。
- 状态：**已按确认范围实现并验证**。学生个人报告与成绩明细保持现有结构，逐步扣分说明仅在复核页展示（用户已确认）。真实数据验收检查布局、导航与未确认草稿；保存、冲突保护和成绩一致性使用隔离测试数据验证，未调用真实模型。
- 视觉参考：`docs/requests/review-unify-prototype-20261001.html`（合成数据，一次性原型）。其中"统一风格、按题页改进"部分完整可看；"按步骤给分、红框、前后步骤联动"部分在中途停下，可能不完整。**两者冲突时以本文档为准。**

实现前先读 `AGENTS.md`、`ARCHITECTURE.md`、`docs/product/GRADING.md`（"复核与改分""人工干预"两节）、`docs/product/KNOWLEDGE_AND_TRAINING.md`（"教师按步骤复核"相关两条）、`docs/security/SECURITY.md`、`docs/testing/README.md`。

---

## 0. 背景与已核实的事实

人工干预页 `frontend/src/views/ReviewQueueView.vue` 有两种状态：

- **按题批量**：`ReviewBatchWorkspace.vue` + `ReviewAnswerSheet.vue`。
- **单份深查**：`ReviewDeepWorkspace.vue` + `ReviewScoringInspector.vue`。从成绩明细进入并带学生参数时，额外显示 `ReviewStudentStrip.vue`，即"按学生复核"。

两种状态共用 `stores/review-queue.ts`、`stores/review-drafts.ts`，提交都走 `confirmReviewItem(s)`。

已核实的问题（代码位置供定位）：

1. **批量页确认解答题不保存分步。**
   - 草稿只在深查打开时才由 `ReviewScoringInspector.beginStepReview()` 写入 `stepScores`；`ensureDraft` 只会恢复已有的教师分步记录。
   - 批量页改总分时，`review-drafts.ts` 的 `updateScore()` 会把 `stepScores` 清空。
   - 后果：
     - 后端 `backend/repositories/review.py`（约 606 行）在没有 `teacher_steps` 时删除该题的 `teacher_reviews`。
     - 掌握度：`integration/diagnosis_profile_service.py`（约 1219–1225 行）对"有教师最终分、无教师分步"的题，按整题证据处理（`teacher_final_without_step_attribution`）。
     - 错因：`backend/session_analysis.py` 的 `_teacher_failed_steps` 返回空，没有分步错因。
2. **"沿用前步错误"标记会过期。**
   - `backend/review/service.py` 的 `_normalize_teacher_steps` 在教师给某步 0 分时，无条件复制 AI 的 `carried_error_from`，不检查被引用的前一步在教师版本里是否仍未达成。
   - 有这个标记的步骤：掌握度按"方法正确"计（`diagnosis_profile_service.py` 约 1473、1545 行），错因整理跳过（`session_analysis.py` 约 805 行）。
   - 所以教师把前一步改对后，后一步的真实错误会被漏记。
3. **教师分步记录不带 AI 的扣分理由，也不能按步骤写批注。**
   - `_normalize_teacher_steps` 只存 `part_id/step_id/score_awarded/max_score/achievement/core_goal/evidence_point_ids/carried_error_from`。
   - 错因整理读取的字段是 `session_analysis._STEP_UNIT_FIELDS` 里的 `reason/missing_or_error/student_evidence`，教师记录里没有这几项。
4. **AI 已经对每一步给出"拿不准"标记。**
   - `solution_answer_guard.py` 的步骤完成状态允许 `uncertain`；`uncertain_step_ids()` 能取出这些步骤。
   - 复核列表接口默认带 `step_assessments`（`review/service.py` 的 `include_evidence=True`）。
   - AI 没有百分比形式的分步置信度。用户已决定**不新增**分步置信度，也不改提示词。
5. 掌握度规则已经符合用户要求，**知识训练模块不需要改**：
   - 有效教师分步记录优先；
   - 只有步骤满分才算达成；
   - 结构依赖 `depends_on` 在计算时按最终达成状态处理（`diagnosis_profile_service.py` 约 1484 行）。
6. 深查确认时会弹两个提示框：`ReviewScoringInspector` 和 `ReviewQueueView` 各有一个 `ReviewFeedbackToast`，位置相同，会叠在一起。
7. `ReviewScoringInspector` 空状态文案写着"从左侧队列选择一名学生"，但页面已经没有左侧队列。

---

## 1. 范围与不变项

### 范围

- 第一部分：两种状态统一风格；按题页改进（纯前端）。
- 第二部分：按步骤复核、红框、Tab 规则、每步记录规则、前后步骤联动（前端 + 考试阅卷模块后端）。

### 不变项（不得改动）

- 考试分数、排名、教师最终分锁、修订号冲突保护、整题批量保存的原子性（要么全部存上、要么都不存）、AI 原分独立保留、标注图延后生成。
- 高置信 AI 结果（`ai_ready`）不修改就不进入保存范围。
- 深查页快捷键：方向键换题、换学生；快捷给分条里 Tab 逐步前进、Enter 提交；Esc 失焦。
- 批量页 `J/K`、`/` 的行为。
- 知识训练模块代码不改。`integration/diagnosis_profile_service.py` 原则上不改；如果确实需要改，先说明原因和影响的模块。
- 不新增模型请求、不改批改提示词、不改 AI 输出契约。
- 历史数据不自动补救：以前在批量页确认、没有分步的答卷保持原状。
- 不新增数据库迁移。教师分步记录仍存在结果 `raw_json.teacher_reviews` 里，只增加字段。

### 建议提交顺序

每次提交只含一件事：

1. 第一部分（纯前端风格统一）；
2. 后端分步记录规则；
3. 前端按步骤复核。

---

## 2. 第一部分：统一风格与按题页改进（纯前端）

### 2.1 统一状态词与颜色

新建一个共享映射，由所有复核组件引用，替换各组件里自写的标签表：`ReviewAnswerSheet`、`ReviewScoringInspector`、`ReviewStudentStrip`、`ReviewBatchWorkspace`。建议放在 `frontend/src/components/review/review-status.ts`。

新建前先搜索 `frontend/src` 里是否已有可复用的状态映射（`'教师已确认'` 出现约 8 处），能复用就复用，不能复用时在提交说明里写原因。

| score_status | 文字 | 颜色变量 |
|---|---|---|
| ungraded | 待人工 | `--color-info` |
| failed | 处理失败 | `--color-danger` |
| ai_review | 待复核 | `--color-warning` |
| ai_ready | AI 已评 | `--color-ai` |
| teacher_final | 教师已确认 | `--color-teacher` |

视觉规则统一为：

- 卡片、题号格、状态标签都用 4px 左边框表示状态颜色；
- 只有"待复核""处理失败"加浅色底；
- 得分率的红→绿热度只在学生题号条（`ReviewStudentStrip`）使用。

### 2.2 题号条

- 按题页的题号条改成和 `ReviewStudentStrip` 同样的单行紧凑格（提取公共样式）。
- 按题号自然顺序排列。**删除** `ReviewBatchWorkspace.prioritizedQuestions` 的风险排序。
- 每格内容：
  - 题号加"剩 N"（N = 待人工 + 处理失败 + 待复核），N 为 0 时显示 ✓；
  - 底部 2px 进度条，表示教师已确认数 / 总数；
  - 左边框按最紧急的状态着色，优先级：处理失败 > 待人工 > 待复核；
  - 选中时用 2px 强调色描边；
  - `title` 显示全部计数和满分；
  - `aria-label` 保持可读。
- 题号条右端加按钮"下一道待处理 ›"：从当前题往后找第一道 N > 0 的题，到末尾后从头接着找。

### 2.3 工具栏与"题目与答案"面板

- 搜索、显示范围、排序三项保持不变。
- 右侧加：
  - 本题进度"本题已确认 x/y · 待处理 n"，配细进度条；
  - "题目与答案"切换按钮，样式与深查头部一致。
- 面板复用 `ReviewAnswerPanel.vue`：在批量页显示为右侧固定栏，宽度沿用 `ai-grading:review-answer-panel-width:v1`，开关状态沿用 `ai-grading:review-answer-panel:v1`，和深查共用。
- 面板打开时，卡片网格减少列数。

### 2.4 卡片（`ReviewAnswerSheet.vue`）

内容顺序：学生名 + 学号·班级 + 状态标签 → 答卷图 → "AI 判断"块 → 教师给分区。

- **AI 判断块**：
  - 第一行"AI x/满分 · 置信度 n%"；
  - 第二行扣分原因，单行省略，`title` 显示全文；
  - 原来放在图片上方的风险原因也并入这一块。
- **"深查"按钮**：去掉现在 52px 方块的"深查/答卷"，改成文字按钮"深查 ›"，放在给分区右侧。
- **选择、填空题**：保持单个分数框，即紧凑布局。
- **解答题**：给分区按第二部分改成多个步骤框。

### 2.5 统一底部操作条

- 批量页底部和深查评分区底部用同一种样式：教师浅绿底，4px 教师绿左边框（参考现在的 `.review-scoring-inspector__footer`）。
- **批量页底部操作条**：
  - 左侧："本题需要教师处理 N 份"，下面一行"其中 X 份分数需要修正"或"M 条草稿未确认"；
  - 中间：分页"‹ 上一批 第 a / b 批 下一批 ›"，从原来的位置移进来；
  - 下方：快捷键提示，用 `<kbd>` 样式；
  - 右侧：主按钮"确认本题处理结果（N）"。
- 删除批量页顶部独立的 `ReviewShortcutGuide` 灰条，或把这个组件改成底部提示复用。
- **快捷键提示文字**：
  - 批量页：`Tab / Shift+Tab 下一个 / 上一个红框 · Enter 下一个，最后一个确认本题 · J / K 切换选中 · / 搜索`。如果第二部分还没合入，第一个短语暂写"下一份 / 上一份"。
  - 深查页：`数字给分 · Tab 下一步 · Enter 确认 · ← / → 换题 · ↑ / ↓ 换人`，必须在一行内放下。

### 2.6 小修

- 深查确认只弹一个提示框：保留页面级 `ReviewQueueView` 的提示，删除 `ReviewScoringInspector` 里重复的成功提示。错误提示仍要能显示，可以改成由页面统一显示。
- `ReviewScoringInspector` 空状态文案改为"请选择一份答卷"。
- 按钮动词统一用"确认"：
  - "保存本题处理结果"改为"确认本题处理结果（N）"；
  - 深查保留"确认此份 / 确认此份并返回"。
- 从成绩明细进入时的"学生作答"标题保持不变。

---

## 3. 第二部分：按步骤复核

### 3.1 规则（用户已确认）

**A. 解答题一开始就是多个步骤框，不再有整题总分框。**

- 适用条件：同现有 `canReviewSteps`。也就是：
  - 有 `result_id/detail_id`；
  - 不是 `legacy:` 开头的记录；
  - 评分标准步骤齐全，每步都有整数分值；
  - 各步满分之和等于题目满分。
- 不满足条件的题，保持现在的单个总分框。
- 步骤框初始值：
  - 优先用已有的教师分步记录（`metadata.teacher_review`，修订号要对得上）；
  - 否则用 AI 步骤分（`step_assessments` 里的 `score_awarded`，按 `part_id/step_id` 唯一对位）；
  - 都没有时留空。
- AI 步骤分和 AI 总分对不上时，沿用现有做法：清空所有步骤框，并提示"AI 步骤分与总分不一致，请逐步给分"。
- 总分只读显示，等于各步之和。

**B. 红框（需要教师核对的框）。** 同一套规则同时用于批量卡片、深查评分卡和深查快捷给分条：

| 答卷情况 | 标红的框 |
|---|---|
| 处理失败、待人工 | 全部步骤框 |
| 待复核，且至少一步 AI 标了 `uncertain` | 只标这些步骤 |
| 待复核，但没有任何一步 `uncertain`（因整体置信度低、涂改、提示注入、单字样作答等原因转复核） | 全部步骤框 |
| 选择、填空题 | 状态为待复核、处理失败、待人工时，标唯一的分数框 |
| AI 已评、教师已确认 | 不标 |

- 前后步骤联动产生的"需重核"也算红框，见 3.4。
- 步骤标签上加小标签，优先级：需重核 > 拿不准 > 需核对（全卡兜底时） / 待评分（没有 AI 结果时）。
- 工具栏放一个图例："红框：AI 拿不准或需要人工评分，Tab 只在红框间跳"。

**C. 批量页键盘（用户明确要求修改，仅限批量页）。**

- Tab / Shift+Tab：在**当前页所有卡片**的红框之间，按页面顺序跳到下一个 / 上一个，跳过非红框和已锁定的框。在非红框里按 Tab，跳到它后面的第一个红框。
- Enter：跳到下一个红框；如果已经是最后一个红框（后面没有红框），执行批量确认。原有的"有错误时先聚焦第一处错误"逻辑保留。
- 非红框仍然可以用鼠标点进去修改。
- 选择、填空题同样只在红框之间跳。
- `J/K`、`/` 不变。
- 深查页的键盘完全不变：快捷给分条逐步前进，所有步骤都会经过。

**D. 每一步保存后记录什么**（AI 步骤分只有 0 或满分两种；教师可以给整数部分分，只有满分才算达成）：

| AI 这一步 | 教师这一步 | 达成状态（掌握度） | 错因 |
|---|---|---|---|
| 未满分 | 满分 | 达成 | 不计错因 |
| 未满分 | 未满分（扣多扣少都一样） | 未达成 | 沿用 AI：复制 AI 这一步的 `reason / missing_or_error / student_evidence` |
| 满分，或没有 AI 结果 | 未满分 | 未达成 | 教师批注；没写时报告显示"人工复核扣分"，但**不计入错因统计、不送去错因整理** |
| 满分 | 满分 | 达成 | 无 |

- 步骤被标为"沿用前步错误"（3.4）时，覆盖上表：不单独记错，掌握度按方法正确计。
- 每步实时显示一行"保存后记录"，对应上表：
  - "改为达成 · 不计错因"；
  - "错因沿用 AI：…"；
  - 扣分原因输入框；
  - "沿用①的错误 · 不单独记错 · 方法按正确计"。
- 扣分原因输入框：`tabindex="-1"`，不进入 Tab 路线，占位文字"人工复核扣分"。

### 3.2 后端改动（只在考试阅卷模块内）

**1. 接口契约** `backend/api/schemas/review.py`：`ReviewStepScore` 增加两个可选字段。

```python
class ReviewStepScore(BaseModel):
    part_id: str = ""
    step_id: str
    score_awarded: float
    teacher_note: str | None = None          # 教师对本步的扣分批注，只在本步未满分时有效
    carried_error_from: str | None = None    # 教师确认"沿用前步错误"，值为同小问更早步骤的 step_id
```

- `backend/api/routers/review.py`（约 183 行）的 `model_dump()` 原样把字段传给服务层。
- 前端 `api/review.ts` 的 `ReviewConfirmInput.step_scores` 类型同步增加这两个字段。

**2. 规范化** `backend/review/service.py` 的 `_normalize_teacher_steps`：现有校验（步骤齐全、整数、范围、合计等于总分）保留，增加以下逻辑。

- 取 AI 本步（沿用现有的 `part_id/step_id` 对位方式，只认唯一匹配）。
- 记录里新增字段：
  - `ai_score_awarded`：AI 本步分；没有时为 null。
  - `deduction_source`：
    - `"none"`：教师给满分；
    - `"ai"`：教师未满分，且 AI 本步也未满分；
    - `"teacher"`：教师未满分，且 AI 本步满分或没有 AI 结果。
- `deduction_source == "ai"` 时，把 AI 本步的 `reason`、`missing_or_error`、`student_evidence` 复制进记录。
- `deduction_source == "teacher"` 时：
  - `teacher_note` 去掉首尾空白后，非空就存进记录，空就不存；
  - **不复制** AI 的理由（AI 认为满分时写的是肯定性描述）；
  - 不要把"人工复核扣分"写进数据库，这是展示用的默认文字。
- `teacher_note` 出现在满分步骤上时忽略。
- **`carried_error_from` 的确定方式**（取代现在无条件复制 AI 标记的做法）：
  - 候选：教师提交的值优先；只有当前端没有提交该字段（字段缺失，而不是显式给空值）时，才回退到 AI 本步标记。前端每次都会显式提交（见 3.3），回退只为兼容旧前端。
  - 只有同时满足以下条件才保留：
    1. 本步教师分为 0（与 AI 契约一致，部分分不算沿用）；
    2. 被引用的步骤在同一小问内（`part_id` 相同）；
    3. 被引用步骤排在本步之前（按评分标准顺序）；
    4. 被引用步骤的**教师分**未满分。
  - 不满足就丢弃，不报错。
  - 保留时，记录的 `deduction_source` 仍按上表计算，`carried_error_from` 单独存。
- 现有限制保持：只有一个 `teacher_reviews` 键对应当前修订号和扫描批次。

**3. 错因读取** `backend/session_analysis.py` 的 `_teacher_failed_steps` / `_independently_failed_steps`：

- 带 `carried_error_from` 的步骤继续跳过（现有行为）。
- `deduction_source == "teacher"` 的步骤：
  - 有 `teacher_note` 时，用它作为送去整理的 `reason`；
  - 没有时，**整步不放进 `failed_steps`**，即不送错因整理、不计错因统计。
- `deduction_source == "ai"` 的步骤：使用复制进来的 AI 字段，和没有教师记录时的行为一致。
- 兼容输入：没有 `deduction_source` 字段的旧教师记录，保持现在的处理方式。
- `class_analysis.py` 通过 `record.failed_steps` 取输入，原则上不需要改。改动会让部分已存的错因整理结果的输入指纹变成"过期"（`cause_source_state` 返回 stale），这是预期行为；在提交说明里写清楚。

**4. 展示"人工复核扣分"**：

- 显示教师分步记录的地方，对 `deduction_source == "teacher"` 且没有 `teacher_note` 的步骤，显示"人工复核扣分"。至少包括：
  - 复核页（前端读 `metadata.teacher_review`）；
  - 学生个人报告和成绩明细里展示步骤或扣分原因的位置。
- 实现前先搜索 `teacher_review`、`failed_steps`、`StudentQuestionRecord` 的使用位置，确认报告实际从哪里取步骤级原因。
- **如果报告目前根本不展示步骤级原因，不要为此新增报告版块。** 停下来把现状报告给用户决定。
- 不要把默认文字拼进整题的 `deduction_reason`：这个字段会进入错因整理输入，`clean_cause_text` 只过滤完全相同的占位词。

**5. 掌握度**：不改。现有的 `_teacher_step_records` 只依赖 `score_awarded/max_score/evidence_point_ids`，新增字段不影响校验；修正后的 `carried_error_from` 自动生效。实现后用测试确认这一点（见 3.5）。

### 3.3 前端改动

**草稿** `stores/review-drafts.ts`：

- `ReviewStepDraft` 增加 `note: string` 和 `carriedFrom: string | null`（null 表示不沿用）。
- 新增"是否由教师显式设置过沿用"的状态，用于联动规则 3.4。
- `refreshDirty` 把 note 和沿用状态纳入比较。
- 解答题在步骤模式下不再提供 `updateScore()` 的入口；该函数只给不满足 `canReviewSteps` 的题使用。
- 步骤初始化（现在在 `ReviewScoringInspector.beginStepReview()` 里）要移到草稿层或共享函数中，让批量卡片不打开深查也能得到同样的步骤草稿。
  - 初始化需要评分标准：批量页按当前题读一次，复用 `review-rubric-cache.ts`。
  - 评分标准读取失败时，解答题退回单个总分框，并显示现有的"评分标准暂时无法读取"提示。

**提交**：

- 批量（`ReviewBatchWorkspace.submitBatch`）和单份（`ReviewScoringInspector.submitCurrent`）两处，对满足步骤条件的题，**始终**提交完整的 `step_scores`。
- 每一步都显式带 `carried_error_from`：不沿用时传 null，这样后端不会回退到 AI 标记。
- 每一步都显式带 `teacher_note`（为空时省略或传 null 均可）。
- `score_awarded` 等于各步之和。
- 校验：可提交的答卷，每个步骤框都必须是 0 到本步满分之间的整数，否则聚焦第一个错误框，并在卡片上显示"第②步请填 0–3 的整数"。

**卡片步骤框（批量）**：

- 每格内容依次为：步骤标签（"① 求导正确 /2"及红框小标签）、AI 行（"AI 2/2 ✓"或"AI 0/3 ✗"，`title` 显示 AI 理由）、数字输入框、"保存后记录"一行。
- 4 步在两列布局下应能一行放下，步骤多时自动换行。
- 右侧显示只读的"合计 x / 满分"和"深查 ›"按钮。

**深查**：

- 评分步骤卡和快捷给分条使用同一套红框规则、小标签、"保存后记录"行和扣分原因输入框。
- 键盘不变。

### 3.4 前后步骤联动（前端实时提示 + 后端最终校验）

依赖只指向**同一小问内更早的步骤**。

1. **把前一步 A 改为满分**，而后面某步 B 的沿用来源是 A：
   - B 的沿用标记立即去掉；
   - B 变成红框（小标签"需重核"），提示"①已改为达成，本步原先因沿用①而扣分，请重新核对"；
   - B 的分数不自动改。
   - 如果 A 又改回未满分，而教师没有手动设置过 B 的沿用状态，就恢复 B 的 AI 沿用标记和原来的红框状态。
2. **把前一步 A 从满分改为未满分**：
   - 同小问中排在 A 后面、当前为满分的步骤都变成红框（"需重核"），提示"①已改为未达成，若本步沿用了①的结果，也可能要扣分"；
   - 分数不自动改。
   - A 改回满分后，教师没有手动改过的步骤去掉这条提示。
   - 判断范围用"同小问后续步骤"，**不读题库的判定点依赖**（`depends_on`）：那属于题库模块，而这里只是提示，宁可多提示。
3. **沿用勾选**：
   - 显示条件：某步 B 为 0 分，且同小问中更早有未满分的步骤。显示内容：复选框"沿用前步错误（方法对，不单独记错）"，加一个可选的前序步骤下拉框，默认选最近的未满分步骤。复选框 `tabindex="-1"`。
   - 默认勾选的条件：AI 对 B 有沿用标记，且被引用的步骤仍未满分。否则默认不勾。
   - B 大于 0 分，或前面没有未满分步骤时，隐藏这个复选框，并清除沿用状态。
4. 后端按 3.2 第 2 点做最终校验，前端状态不一致时以后端为准，不报错。

### 3.5 测试

按 `docs/testing/README.md` 的顺序：优先在已有测试里补断言或补一组输入，确实不属于已有文件时才新建。

**后端**：

- 主要放在 `tests/test_review_step_confirmation.py`，覆盖：
  - 表 D 四种情况，各自记录的 `deduction_source`、AI 字段是否复制、`teacher_note` 是否存储（满分时忽略，空白不存）；
  - `carried_error_from`：
    - AI 标记，前一步仍未满分 → 保留；
    - 教师把前一步改为满分 → 丢弃；
    - 教师显式传 null → 丢弃（不回退到 AI）；
    - 教师传入跨小问、引用后面步骤、或本步部分分 → 丢弃；
    - 前端没有提交该字段 → 回退到 AI 标记，且仍经过上述校验；
  - 原有的合计、整数、范围校验不回退。
- `session_analysis` 相关测试（搜索 `_teacher_failed_steps` 的现有用例），覆盖：
  - `teacher` 来源且没有批注 → 不进入 `failed_steps`；
  - 有批注 → 以批注作为 reason；
  - `ai` 来源 → 带 AI 字段；
  - 有沿用标记 → 跳过；
  - 没有 `deduction_source` 的旧记录 → 结果不变。
- 掌握度：在 `tests/question_bank/test_exam_evidence_projection.py` 的现有用例上补一组输入，确认带新字段的教师记录仍被 `_teacher_step_records` 接受，而且被丢弃了沿用标记的步骤按未达成计。

**前端**：

- 已有的 `review-drafts-store.spec.ts`、`review-batch-workspace.spec.ts`、`review-scoring-inspector.spec.ts`、`review-queue-view.spec.ts`，覆盖：
  - 红框五种情况；
  - 批量页 Tab / Shift+Tab 只在红框间跳（跨卡片）；
  - 最后一个红框按 Enter 触发确认；
  - 扣分原因框和沿用勾选不在 Tab 路线上；
  - 批量确认始终带 `step_scores`，且显式带 `carried_error_from`；
  - 联动规则 1、2、3；
  - 深查只弹一个提示框；
  - 深查键盘不变；
  - 题号条按自然顺序，"下一道待处理"能从末尾回到开头；
  - "题目与答案"面板在批量页和深查共用开关。
- 被新断言覆盖的旧断言删除，例如批量页风险排序、顶部快捷键灰条。

### 3.6 文档（在对应的提交里一起改，改写原句，不在末尾追加）

- `docs/product/GRADING.md`：
  - "人工干预"一节：题号顺序、下一道待处理、题目与答案面板、解答题按步骤给分、红框、批量页 Tab 规则、底部操作条；
  - "复核与改分"一节：每步记录规则、扣分原因默认文字的口径、沿用前步错误的校验。
- `docs/product/KNOWLEDGE_AND_TRAINING.md` 约 163–164 行：说明批量页确认也会保存完整的步骤记录；"只改总分时回到小问整体证据"这句仍适用于不满足步骤条件的题。
- `CONTEXT.md`：只有出现新的业务词时才加。"红框"是界面用语，不算业务词。
- 注意：`ARCHITECTURE.md`、`CONTEXT.md`、`docs/testing/README.md`、`docs/product/KNOWLEDGE_AND_TRAINING.md` 目前有其他任务未提交的修改。按 `AGENTS.md` 错开处理，保留对方的修改，只提交本任务的内容。
- 运行 `tools/check_documentation.py`。

### 3.7 验收

- 用测试数据（新建带测试名称的隔离考试，不动真实 `user_data`）走一遍：
  - 批量页确认一道解答题，结果里有完整的 `teacher_reviews` 分步记录；
  - 分析时这道题按步骤计入掌握度，而不是整题；
  - 表 D 四种情况的错因输入符合预期。
- 不调用真实模型。错因整理只检查送出的输入，不实际发出请求。
- 回复用户时按 `AGENTS.md` 写明"验证 / 测试 / 文档"三行，没验证到的部分（例如报告展示位置）如实列出。

---

## 4. 实现前需要停下来问用户的情况

- 学生报告或成绩明细目前不展示步骤级扣分原因，"人工复核扣分"没有现成的位置可以显示（见 3.2 第 4 点）。
- 发现必须修改 `integration/diagnosis_profile_service.py` 或知识训练模块代码。
- 发现某类题的 AI 步骤结果无法和评分标准步骤唯一对位，而且这种情况在真实数据里大量存在。可以只读统计，不要修改数据。
