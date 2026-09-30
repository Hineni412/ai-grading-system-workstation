# 方案：训练推荐规则面板与讲义模式

用途：交给开发代理实现的已确认方案。本文内容**尚未实现**；实现完成后，改写 `docs/product/KNOWLEDGE_AND_TRAINING.md` 中对应的固定规则（见文末），然后删除本文。

## 目标

教师生成个性化训练推荐前，可以在现有“出卷设置”面板里看到并调整选题规则。新增“讲义”用途：题量最多 50 道，只导出打印，不回收判定。

用户已确认：

- 可调规则：每卷题数、同一技能最多几道、解答题最多几道、难度上限、近期原题排除次数；
- 相似题限制保持现有固定规则，不开放；
- 讲义**不回收**：不扫描、不判定、不更新掌握度；
- 8–12 题的训练卷流程不变，仍可回收判定。

## 现状

规则目前是写死的常量，面板只让教师改题数（8–12）和难度上限（1–8），其余规则只以一行提示文字展示。

| 规则 | 现值 | 代码位置 |
|---|---|---|
| 每卷题数 | 8–12 | `question_bank/recommendation/personalized.py` 的 `PersonalizedRecommendationConfig.__post_init__`；`backend/api/schemas/training.py`（两处 `Field(ge=8, le=12)`）；前端 `TrainingRecommendationsView.vue`、`PersonalizedRecommendationDraft.vue`、`PaperSettingsPanel.vue` 的校验 |
| 难度上限 | 1–8（后端 `min(8., …)` 截断） | 同上；另有 `_difficulty_plan` 的“最高 8 级” |
| 同技能最多 | 1 道 | `MAX_QUESTIONS_PER_SKILL`、`_paper_skill_limit_exceeded` |
| 解答题最多 | 2 道 | `_paper_diversity_allowed`；`_refresh_supplement_warnings` 的 `written_limit` |
| 近期原题排除 | 最近 3 次 | `RECENT_ACTIVITY_COUNT`（冻结最近活动时使用） |
| 冻结训练卷题量 | ≤12 | `question_bank/personalized_papers/module.py` 的 `MAX_QUESTIONS`（一次模型判定的承载上限） |

草稿配置由 `to_dict` / `_config_constructor` 保存和恢复；下一轮补练继承配置在 `backend/training_assessment/evidence.py`。

## 面板设计（改造现有 `PaperSettingsPanel.vue`，不新增页面）

在现有“每卷题数 / 难度上限”网格上方加一个**用途**选择，下方网格扩为五项：

| 项 | 训练卷（默认） | 讲义 | 默认值 |
|---|---|---|---|
| 用途 | 可回收判定，更新掌握度 | 只导出打印，不回收 | 训练卷 |
| 每卷题数 | 8–12 | 8–50 | 10 |
| 同一技能最多 | 1–5 道 | 1–10 道 | 1 |
| 解答题最多 | 0–题数 | 0–题数 | 2 |
| 难度上限 | 1–10 级 | 1–10 级 | 8 |
| 排除最近几次的原题 | 0–10 次（0 = 不排除） | 0–10 次 | 3 |

- 原来那行固定规则提示文字改为按当前取值动态生成；相似题限制仍写为固定说明。
- 讲义用途下显示提示：“讲义不回收判定，也不更新掌握度。题量较大时建议放宽同技能上限，否则可能出现缺题。”
- 所有取值随现有出卷会话记录恢复（沿用 `savePaperSelectionSession` 的做法）。
- 训练卷、讲义之外的入口不受影响，包括**学情组卷助手**：它继续使用固定规则，本次不改 `validate_paper_questions` 与 `assembly_assistant.py`。

## 后端规则改造

1. `PersonalizedRecommendationConfig` 新增字段：
   - `purpose`：`"training" | "handout"`，默认 `"training"`；
   - `max_questions_per_skill`，默认 1；
   - `max_written_questions`，默认 2；
   - `recent_activity_count`，默认 3。

   按上表校验范围：训练卷题数仍为 8–12，讲义为 8–50；难度上限放宽为 1–10，删除 `min(8., …)` 截断。
2. 常量改为读取配置：`_paper_skill_limit_exceeded`、`_paper_diversity_allowed`、`_refresh_supplement_warnings`、最近活动截取、`_difficulty_plan` 的上限。这些函数需要把配置作为参数传入，不能改成可变的全局变量。
3. `to_dict` / `_config_constructor` 保存和恢复新字段。**兼容输入**：旧草稿缺少这些字段时，按现有固定值（1 道、2 道、3 次、训练卷）恢复，不改变旧草稿的行为。
4. 换题、排除、重新核对和下一轮补练都沿用草稿里保存的规则（现有“设置、来源和限制随草稿保存”的机制）。讲义草稿没有下一轮入口。
5. API 请求模型（`backend/api/schemas/training.py` 的推荐请求与分组请求）增加对应字段，放宽范围。
6. **近期原题排除设为 0** 时不排除任何原题；草稿仍冻结最近活动记录（记录为空），保持“草稿生成后不随新批改变化”的语义。
7. **难度上限超过 8**：只影响候选筛选和适合难度的上限。难度计算、1–3 级基础新练习的兜底规则不变。

## 讲义导出

- 用途为讲义的草稿，页面把“生成试卷”换成“导出讲义”；不创建冻结训练卷实例，不生成二维码页面身份，不进入扫描批次。
- 新增一个从草稿导出的接口，参数为草稿编号、预期修订号、请求令牌，提交一个后台任务：
  - 一人一卷：每名学生一份 Word，打包 zip；
  - 多人同卷：一份 Word；
  - 使用现有 `question_bank/exporters/paper_docx_exporter.py` 的 `export_question_paper_docx`，题序与草稿一致，答案解析放末尾；标题写“{姓名或小组} 讲义”。
- 导出前做与冻结相同的草稿来源核对：来源已变化时要求先重新核对。修订号不一致返回冲突，不导出旧内容。
- 下载规则与训练额外导出一致：下载后删除本机副本（`backend/files/service.py` 中新增任务类型的 `consume_after_download=True`）。
- 讲义不写训练证据，不影响掌握度，不计入近期原题排除的活动次数（与现有“仅生成、导出或未批改的不计入”一致）。
- 讲义草稿即使题数 ≤12，也不能转成训练卷。需要回收时重新生成训练卷草稿。

## 必须注意

- 冻结训练卷的 `MAX_QUESTIONS=12` 与判定点、图片、页数上限**保持不变**，它们保护“一次判定只发一次模型请求”。讲义不经过冻结，所以不受这些上限约束。
- 50 题 × 全班一人一卷可能明显变慢。实现后要用同一份合成数据比较“10 题”和“50 题”的生成耗时，并把对比写进交付说明。
- 同技能上限为 1 时，50 题常常凑不满，会按现有规则保留缺口并说明原因，不能放宽其他规则来凑数。

## 验收

- 合成数据：
  - 同一学生分别用默认规则与放宽规则（同技能 3、解答题 5、近期排除 0、难度上限 10）生成推荐，逐项核对结果符合所设规则；
  - 旧草稿（无新字段）换题结果与改动前一致；
  - 讲义 50 题导出 Word，题序与草稿一致、答案在末尾，下载后本机副本删除，且没有产生训练实例或证据。
- 性能：同一合成班级 10 题与 50 题的生成耗时对比。
- 测试优先扩展现有推荐规则与出卷设置测试（如 `frontend/src/__tests__/training-recommendations-view.spec.ts` 和现有个性化推荐后端测试），不新建测试文件，除非确无归属。

## 完成后需要更新的文档

`docs/product/KNOWLEDGE_AND_TRAINING.md` 需要改写：

- “范围与题量”：题量、同技能与解答题上限改为“教师设置，默认……”；
- “适合难度”：上限不再固定为最高 8 级；
- “近期原题排除”：次数可设，默认 3；
- “批量出卷”：新增讲义导出，并说明不回收、不产生证据。

原句直接改写，不在末尾追加。
