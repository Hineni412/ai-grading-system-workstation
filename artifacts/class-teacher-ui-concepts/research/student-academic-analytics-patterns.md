# 单个学生学业证据与趋势页：成熟产品模式调研

> 调研日期：2026-08-01  
> 用途：为班主任工作台第 6 页“学业证据与关注行动”改版提供界面与交互依据。  
> 范围：只研究单个学生的历次考试、各科位次进退、相对长项与待支持学科、考试可比性、异常状态及原始证据下钻。不涉及全班风险排名或自动作出教育结论。

## 结论先行

成熟方案的共同点不是“多放几张图”，而是把分析做成一条可追溯链路：

1. 先限定可比的考试群，再计算趋势；
2. 总览以图表为主，原始记录收进二级证据视图；
3. 每一个结论都能点开，看到它基于哪几次考试、哪一行数据；
4. 分数、名次、名次/参考人数、班级相对位置是不同口径，不混成一个“进步值”；
5. `0 分`、`缺考`、`免考`、`未录入`、`待核对`、`补测` 必须是不同状态，不能都画成 0 分点；
6. “长项/薄弱”必须附带证据数量和计算口径，教师可标记“仍需核对”；
7. 界面可提供关注建议，但“跟进/观察/暂不行动”仍由教师决定。

对本项目最合适的主视觉组合是：

- **历次名次趋势图**：默认只连接同类、已确认、可比的考试；
- **各科进退哑铃图**：每科一行，对比上次与本次的名次/相对位置；
- **长项与待支持横条**：以班级中位位置为中线，表达各科相对表现，并显示证据数；
- **考试证据时间带**：用小卡和图形状态取代大表格；
- **右侧证据抽屉**：点图上任一数据点即显示原始行、来源、校对状态和可比依据。

## 一手来源与可借鉴模式

### 1. 智学网：名次要与人数、高低平均分和多次数据一起解读

智学网官方 FAQ 用“所处百分比”表达学生在班级中的位置，并建议将最高分、最低分、平均分和名次结合起来判断考试难度；官方也明确提醒，单次成绩不能作为学习好坏的唯一尺度，需看多次数据与丢分位置。

- 官方来源：[智学网——如何知道成绩排名](https://www.zhixue.com/appcommon/wechat/question?type=report_2)
- 直接借鉴：点位标签显示 `7 / 48`，同时补充“班级前 15%”；不只显示“上升 5 名”。
- 不应照搬：不把班级名次扩大成全班红黄榜。本项目是单学生的私密教师视图，不使用羞辱性文案。

### 2. Microsoft Teams for Education Insights：“分布/趋势”切换与学生对班级参照线

Microsoft 官方的 Grades report 可按学生、作业和时间筛选，在“分布”与“趋势”间切换；选中一个学生后，趋势图同时显示学生和班级平均。它只把已评分作业加入图表，并在比较不同满分的作业时归一到 100 分。

- 官方来源：[Microsoft Support——Grades activity data in Insights](https://support.microsoft.com/en-US/teams/education/grades-activity-data-in-insights)
- 直接借鉴：趋势图上提供可显示/隐藏的“班级平均”参照线；用户可切到“本次各科分布”而不离开页面。
- 不应照搬：归一到 100 分只解决满分不同，不能证明两场考试的范围、难度与参考群体可比。本项目不能因为换算了百分制就自动连线。

### 3. Canvas Course Analytics：多条件过滤、图形编码和点击下钻

Canvas 官方分析支持按班级、学生和作业同时筛选，图表会随条件动态更新。数据点除了颜色还可启用不同形状；点击学生数据点可看到当前成绩、作业名称与日期、学生分数、课程平均分、提交状态和日期。官方也会提示数据可能有刷新延迟。

- 官方来源：[Instructure——Compare the course average chart with a student, section or assignment](https://community.instructure.com/en/kb/articles/660629-how-do-i-compare-the-course-average-chart-graph-with-an-assignment-section-or-student-filter-in-course-analytics)
- 直接借鉴：缺考、免考、补测、待核对使用不同点形和文字；颜色只作辅助。点击数据点在右侧抽屉显示完整证据。
- 不应照搬：Canvas 的作业百分比属于同一课程语境，不能换成“跨考试能力趋势”。本项目也不提供包含敏感学生资料的普通明文 CSV 导出。

### 4. Otus：尝试次数趋势、目标区间分解与个人细节下钻

Otus 官方分析把“考试尝试/时间”放在横轴、分数放在纵轴，支持折线和柱形切换；用户可按日期、测评和目标区间筛选。它的水平条形分解可点击到某一尝试的学生明细，再点中学生进入个人表现。学生个人视图包含所有尝试成绩，也能切换折线/柱形。

- 官方来源：[Otus Help——Analyze 3rd Party Data](https://help.otus.com/en/articles/2755238-analyze-3rd-party-data-in-otus)、[View an Individual Student's 3rd Party Data](https://help.otus.com/en/articles/2508369-view-an-individual-student-s-3rd-party-data)
- 直接借鉴：点“英语”学科行后，主图、证据时间带与右侧关注卡同步过滤；点一个考试点再下钻到原始行。
- 不应照搬：官方允许不同地区自定义上传字段，因此“有图”不等于“口径一致”。本项目必须在画图前显式检查可比字段。

### 5. NWEA MAP Growth：纵向数据、误差范围与“不确定就不贴标签”

NWEA 的 Student Profile Report 按学科展示学生纵向数据，并把成绩与班级/常模参照、实际成长和预测成长分开。它会显示测量误差范围、答题时长与快速作答比例；如果一科的“最近成绩”实际来自更早学期，会加星号提醒。在教学领域层面，只有当领域分数与学科整体分数的差异超出误差后，才标记“相对优势”或“建议关注”；落在误差范围内时不贴标签。无数据时使用灰底，无有效成长事件的测试不进入成长统计。

- 官方来源：[NWEA——Student Profile Report](https://teach.mapnwea.org/assist/help_map/Content/Data/SampleReports/StudentProfile.htm)、[Student Progress Report Description](https://teach.mapnwea.org/impl/maphelp/Content/Data/SampleReports/StudentProgressReport.htm)
- 直接借鉴：“长项/待支持”不凭单次排名下结论；卡片显示“基于 3 次同类考试”，证据不足时显示“暂不判定”。最近数据不在本期时明示作旧日期。
- 不应照搬：RIT、美国常模百分位和预测分是 NWEA 的专用测量体系，本项目没有相应常模时不得伪造。

### 6. PowerSchool：原始点、计算线与教师专业判断分开

PowerSchool 的 Student Standards Progress 会显示形成一个结论的全部作业成绩；如果口径是“最近 3 次”，这 3 个原始点会被单独标出，系统计算值以虚线表示。它允许查看平均、中位数、最近若干次、加权平均等口径，并提供“专业判断提示”，说明计算成绩可能与教师对学生掌握程度的判断不一致。Test Results 则按学年、考试类别过滤，区分州/地区/学校/课堂考试，并支持从考试或标准名称下钻。

- 官方来源：[PowerSchool——Student Standards Progress](https://ps.powerschool-docs.com/powerteacher-pro/latest/student-standards-progress)、[PowerSchool——Test Results](https://ps.powerschool-docs.com/pssis-student-parent/latest/test-results)
- 直接借鉴：任何“长项/待支持”结论都显示口径，例如“最近 3 次已确认的同类月考”；图上的原始成绩点和系统摘要线视觉上分开。
- 不应照搬：PowerSchool 的课程标准掌握图不等于本项目已拥有知识点级证据；如果只有考试总分和学科分，不得虚构更细的掌握结论。

### 补充参考：i-Ready 如何将“当前水平”与“成长”分开

i-Ready 官方资料介绍了高中阶段的纵向视图、分领域结果，以及基于学生起点差异的 Typical Growth/Stretch Growth。它同时区分“相对年级标准的当前水平”和“相对同类学生的位置”。

- 官方来源：[Curriculum Associates——i-Ready Diagnostic resources](https://www.curriculumassociates.com/programs/i-ready-assessment/admin-resources/diagnostic)
- 对本项目的启发：摘要条分别写“本次位置”和“相比上次可比考试”，不把当前高分等同于近期进步，也不把低起点下的明显增长忽略。

## 状态与可比性：官方数据规范的底线

### 1EdTech OneRoster 1.2

OneRoster 明确把“分数是否已最终完成”和“学生作品的提交状态”分开。`scoreStatus` 可表示 `exempt`、`fully graded`、`not submitted`、`partially graded`、`submitted`；另有 `inProgress`、`incomplete`、`late`、`missing` 布尔字段表达更细的提交情况。官方特别说明，这些字段可独立存在，“迟交”与“缺失”是否互斥需由实现方决定。

- 官方来源：[OneRoster 1.2 Gradebook Service](https://www.imsglobal.org/sites/default/files/spec/oneroster/v1p2/gradebook-informationmodel/OneRosterv1p2GradebookService_InfoModelv1p0.html)、[Implementation and Best Practices Guide](https://www.imsglobal.org/spec/oneroster/v1p2/impl)
- 对本项目的直接规则：“是否有数字”不能代替状态；状态必须有文字和图形编码。

### Ed-Fi Assessment Domain

Ed-Fi 把测试元数据、学生成绩、参考年级、缺考原因和首测/补测次数分开表达，也保留测试版本和实施日期等信息。

- 官方来源：[Ed-Fi——Assessment Domain entities and descriptors](https://docs.ed-fi.org/reference/data-exchange/data-standard/3/model-reference/assessment-domain/entities-references-and-descriptors)
- 对本项目的直接规则：补测不覆盖原考试；两条证据都保留，再根据学校规则决定哪一条进入趋势。

## 推荐的第 6 页布局

页面不再放“导入成绩”入口。名单和成绩证据由系统其他现有入口准备；此页只做查看、核对与决定。

```text
┌ 学生 A · 九年级 1 班 ─ 学业证据与关注 ──────────────┐
│ [本学期] [同类月考] [全科]   7 次证据 · 5 次可比 · 1 条待核对 │
├── 摘要条：本次位置 | 较上次进退 | 进步最明显 | 数据质量 ──┤
│                                                        │
│  历次总分/位次趋势（8 列）           长项与待支持（4 列） │
│  ●─●   ○缺考   ●─◆补测   ●            数学  +12 · 4 次证据   │
│  可切换：班级位次 / 百分制 / 班级平均     英语   -9 · 待支持     │
│                                                        │
├── 各科进退哑铃图：上次○──●本次，每科一行 ──────┤
│ 数学  18/48  ○─────●  9/48   ↑ 9     右侧关注卡       │
│ 物理  14/48     ○───●  8/48   ↑ 6     [跟进][观察]       │
│ 英语  10/48  ●────○ 17/48   ↓ 7     [暂不行动]         │
├── 考试证据时间带：[五月月考][期中·不可比][七月月考][补测] ┤
│ 点任意图形点或小卡 → 右侧滑出“原始证据”抽屉                 │
└──────────────────────────────────────────────────────┘
```

### 1. 顶部筛选和摘要条

- 筛选：时间范围、考试家族（月考/期中/期末/平时任务）、学科、只看可比证据。
- 摘要不用巨大数字卡，用 4 个紧凑摘要块：
  - 最近一次已确认的总分位次；
  - 较上次同类考试的位置变化；
  - 进步最明显学科；
  - 待核对/不可比证据数。
- 明示“数据更新到 08-01 10:14”，避免用户误以为实时。

### 2. 历次考试趋势图

- 默认显示“班级相对位置”；名次越靠前，图上位置越高。
- 每个点仍显示教师熟悉的 `名次 / 参考人数`，例如 `7 / 48`。
- 可切换到“百分制分数”或显示“班级平均”参照线，但不使用双 Y 轴把分数和名次强行画在一起。
- 只有可比点用实线连接。期中与月考、满分或范围显著不同、参考人群变化较大时，使用灰色隔断和“不参与趋势”文字。
- 悬停/键盘聚焦某点时显示：考试名称、日期、分数/满分、班平均、最高/最低分、名次/参考人数、较上次变化、状态、可比依据。

### 3. 各科进退哑铃图

用横向“旧点—新点”取代表格：

- 每科一行，空心点表示上次可比考试，实心点表示本次；
- 右侧同时写 `18/48 → 9/48，进步 9 名`，不让用户只猜线段含义；
- 点击“英语”整行，页面上方趋势图和下方证据带同步只显示英语；
- 参考人数变化时，主要使用“班级相对位置”定位，点标签仍保留原名次和人数。

### 4. 长项与待支持

- 不使用雷达图。各科满分、难度、数量不一致时，多轴多边形很容易制造虚假的面积印象。
- 用以班级中位位置为中线的水平条或圆点条，展示每科相对位置。
- 标签分为“稳定长项”、“近期进步”、“待支持”、“证据不足”，不使用“差科”。
- 每个标签附带证据口径，例如“最近 4 次同类考试均高于班级中位位置”。
- 点标签打开“为什么这样判断”，显示证据数、计算口径与不确定性；教师可标记“仍需核对”。

### 5. 考试证据时间带

- 每场考试是一张紧凑的圆角矩形小卡，显示考试类型、日期、结果状态和是否可比。
- 月考、期中、期末、平时任务和补测使用不同小图标。
- 不可比考试卡继续可查看，但淡化并显示“不参与趋势”，不直接隐藏证据。
- 补测卡与原考试卡用一条细虚线关联，但不覆盖原“缺考”事件。

### 6. 右侧原始证据抽屉

点击图上的点、科目行或考试卡时，在当前页面打开抽屉，不跳到另一个模块。抽屉包含：

- 考试名称、层级/类型、日期、学科；
- 分数、满分、班平均、最高/最低分；
- 名次/参考人数和班级相对位置；
- 成绩状态、是否最终、是否补测；
- 可比/不可比结论和每项依据；
- 来源文件、工作表/行号、导入时间、修订版本、教师校对状态；
- “查看原始行”和“标记待核对”。

原始证据表格仍可保留在“查看全部证据”的二级视图中，但不再是主页下半部分的默认形态。

## 状态的视觉编码

| 状态 | 图形 | 文字 | 是否进入趋势 |
|---|---|---|---|
| 已确认、可比 | 实心圆 `●` | `已确认` | 是，实线连接 |
| 缺考 | 空心圆加斜线 `⊘` | `缺考` | 否，在该处断线 |
| 免考 | 盾牌/短横线 | `免考` | 否 |
| 未录入 | 虚线方框 `□` | `未录入` | 否 |
| 待核对 | 空心菱形 `◇` | `待核对` | 默认否 |
| 补测 | 实心菱形 `◆` | `补测` | 依学校规则决定，不覆盖原事件 |
| 考试类型不同 | 灰色点+隔断 | `不可直接比较` | 否 |

颜色仅作第二通道：不能只用红、绿区分。对色觉异常用户，图形和文字仍能完整表达状态。

## 本项目的可比性门槛

以下是结合官方产品做法与本项目数据边界得出的**设计推论**，不是某一产品现成算法。

两条考试证据只有在下列核心条件一致或已有明确换算规则时，才用实线连接：

- 同学科；
- 同考试家族/用途，例如都是同年级月考；
- 覆盖范围、满分或可信换算口径一致；
- 参考班级/人群口径一致，或名次趋势已换成可比的相对位置；
- 成绩已最终、已由教师确认；
- 不是缺考、免考、未录入或待核对；
- 补测是否取代原考试，必须有学校明确规则。

当参考人数不同时，原始名次不宜直接连线。例如 `5/20` 与 `5/50` 虽然都是第 5 名，含义完全不同。建议图上坐标使用班级相对位置，数据点标签仍保留 `名次/参考人数`。

## 从总览下钻到原始证据的交互链

1. 页面打开时，默认为“本学期 + 已确认 + 同类可比考试”。
2. 教师在各科进退图中点击“英语”。
3. 历次趋势图只保留英语，考试证据带同步过滤。
4. 教师点击七月月考的数据点。
5. 右侧抽屉显示该点的分数、名次/人数、班平均、状态、来源行与可比依据。
6. 教师点“查看原始行”核对，或标记“待核对”。
7. 被标记待核对后，该点立即变为空心菱形，并暂时退出趋势计算；既有原始证据不被删除。
8. 如果某科连续多次位于待支持区间，右侧关注卡显示“证据和口径”，教师再选择“跟进/观察/暂不行动”并记录理由。

## 不应照搬的风险

| 风险 | 可实际发生的错误 | 本项目应对 |
|---|---|---|
| 排名羞辱 | 用红色大字显示“退步 12 名”，并和全班名单并列 | 只在单学生加密页中显示；文案使用“较上次同类考试”，不做全班风险榜 |
| 不可比强行连线 | 月考、期末或不同满分的数据被画成平滑趋势 | 先分考试家族；不可比点保留但断线，显示原因 |
| 把缺考当 0 分 | 折线突然跌至 0，系统误判为严重退步 | 缺考用独立状态点，断开趋势，不进入计算 |
| 补测覆盖历史 | 用补测成绩直接改写原缺考/原成绩，无法追溯 | 原事件与补测事件均保留，虚线关联，进入趋势的规则单独显示 |
| 颜色代替状态 | 红/绿点对色觉异常用户无法辨认 | 颜色 + 点形 + 文字三重编码 |
| 单次就贴强弱标签 | 一次英语分数低就被定义为“英语薄弱” | 展示证据数和口径；证据不足时只写“暂不判定” |
| 用雷达图制造精确感 | 各科满分不一、考试难度不一，多边形面积被误读为综合能力 | 使用可读取原值的横向圆点条/哑铃图 |
| 把相关当原因 | 成绩下降就自动归因于态度、心理或家庭 | 只陈述可核对学业证据；关注卡写“还需核对的问题”，不诊断 |
| 图表无法追溯 | 用户看到“退步”却不知道源自哪个文件的哪一行 | 所有点、线、标签可下钻到原始证据和校对状态 |

## 用于下一轮概念图的关键画面

概念图建议选择如下明确状态，避免画成泛化数据大屏：

- 学生：`学生 A · 九年级 1 班`；
- 筛选：`本学期 / 同类月考 / 全科`；
- 数据摘要：`7 次证据 · 5 次可比 · 1 条待核对`；
- 主图：名次趋势有 4 个实心点，中间一个“缺考”断点，后面一个“补测”菱形；
- 各科进退：
  - `数学 18/48 → 9/48，进步 9 名`；
  - `物理 14/48 → 8/48，进步 6 名`；
  - `英语 10/48 → 17/48，后退 7 名`；
  - `语文 13/48 → 12/48，基本稳定`；
- 长项/待支持：`数学·稳定长项·基于4次同类考试`，`英语·待支持·基于3次同类考试`；
- 右侧关注卡：`英语阅读表现连续3次低于学生本人其他学科的相对位置`，附 `跟进 / 观察 / 暂不行动`；
- 底部是考试小卡时间带，而不是大表格；
- 打开一个右侧“七月月考 · 英语”证据抽屉，表现从图表下钻到原始数据的交互。

视觉上继续使用项目现有的浅灰工作面、白色内容面、深青绿主操作色与细分隔线。图表是页面的信息主体，不做渐变大屏、不做彩色仪表盘、不用全班头像和排名墙。

## 来源索引

- [智学网官方 FAQ：如何知道成绩排名](https://www.zhixue.com/appcommon/wechat/question?type=report_2)
- [Microsoft Teams for Education：Grades activity data in Insights](https://support.microsoft.com/en-US/teams/education/grades-activity-data-in-insights)
- [Canvas Course Analytics：按学生/作业/班级比较课程平均](https://community.instructure.com/en/kb/articles/660629-how-do-i-compare-the-course-average-chart-graph-with-an-assignment-section-or-student-filter-in-course-analytics)
- [Otus：Analyze 3rd Party Data](https://help.otus.com/en/articles/2755238-analyze-3rd-party-data-in-otus)
- [Otus：View an Individual Student's 3rd Party Data](https://help.otus.com/en/articles/2508369-view-an-individual-student-s-3rd-party-data)
- [NWEA：Student Profile Report](https://teach.mapnwea.org/assist/help_map/Content/Data/SampleReports/StudentProfile.htm)
- [NWEA：Student Progress Report Description](https://teach.mapnwea.org/impl/maphelp/Content/Data/SampleReports/StudentProgressReport.htm)
- [PowerSchool：Student Standards Progress](https://ps.powerschool-docs.com/powerteacher-pro/latest/student-standards-progress)
- [PowerSchool：Test Results](https://ps.powerschool-docs.com/pssis-student-parent/latest/test-results)
- [Curriculum Associates：i-Ready Diagnostic resources](https://www.curriculumassociates.com/programs/i-ready-assessment/admin-resources/diagnostic)
- [1EdTech：OneRoster 1.2 Gradebook Service](https://www.imsglobal.org/sites/default/files/spec/oneroster/v1p2/gradebook-informationmodel/OneRosterv1p2GradebookService_InfoModelv1p0.html)
- [1EdTech：OneRoster 1.2 Implementation and Best Practices](https://www.imsglobal.org/spec/oneroster/v1p2/impl)
- [Ed-Fi：Assessment Domain entities and descriptors](https://docs.ed-fi.org/reference/data-exchange/data-standard/3/model-reference/assessment-domain/entities-references-and-descriptors)
