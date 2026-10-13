# 调查与待办索引

本目录保留仍可用于讨论的调查、实验和待办。产品行为以根目录 README 索引的权威文档为准。历史测量只适用于原文注明的输入和日期。

| 主题 | 保留入口 | 用途与未完成范围 |
|---|---|---|
| 待办 | [未完成事项](open-items.md) | 汇总仍未完成或待核实的候选，不充当已实现功能说明 |
| 后端架构与性能 | [四项改进与验证](backend-architecture-performance-review-20261013.html) | 四项已按顺序实现；记录共享刷新、组卷难度、考试分析与掌握度的前后对比及合成验证边界，正式页面耗时未测 |
| 合并后性能与业务审查 | [九项修复与证据](backend-business-performance-audit-20261013.md) | 8 项问题与第 9 项掌握度优化已实现；16 万条合成观测的完整拟合中位耗时减少 18.1%，记录验证范围与限制 |
| 设置布局与本机空间 | [调查与改进建议](settings-space-investigation-20261011.md)、[备份与试验副本清理结果](older-backup-inventory-20261011.md) | 设置布局调整与已授权清理依据；仅保留一份当前数据库备份，完整业务备份限制、快照保留及推荐正文压缩仍待处理 |
| 页面与性能巡检 | [问题与根因记录](ui-performance-audit-20261007.md)、[概览](ui-performance-audit-20261007.html) | 九项修复完成，8035 三种桌面尺寸复测及同输入核心、实际接口核验通过；模型、改分、生成与恢复流程未执行 |
| 首次分组与推荐计算 | [优化与调查记录](training-first-compute-audit-20261009.md)、[测量概览](training-first-compute-audit-20261009.html) | 进一步四步优化已实施；91 人两章同输入各 5 次完整重算，分组与掌握度精确一致，首次重算仍未达到 5 秒 |
| 首次重算剩余空间 | [完整评价与实施交接](training-performance-remaining-handoff-20261009.md)、[实施与自测交接](training-performance-implementation-handoff-20261009.md)、[独立验收](training-performance-independent-acceptance-20261010.md) | 保留原 22 项评价与失败守护；实施 7 项，独立复跑 297 项测试及完整响应、掌握度和三范围组卷对照通过。独立同条件中位数降低 26.39%，当前单版本五次完整重算中位数 73.27 秒，首次 5 秒目标未达成；正式 HTTP、页面和真实改分后刷新未验收 |
| 完整资料与题目素材复用 | [实施试验和取舍](training-two-hotspots-evaluation-20261010.md) | 四个候选均已比较，每个局部步骤各五次；完整匹配、素材快照和检查键一致，未证明稳定净收益，候选全部撤回。记录内存压力、失败样本与未验证范围；首次 5 秒目标未达成 |
| 训练进入与切班性能 | [实施与验证](training-entry-performance-20261008.md)、[概览](training-entry-performance-20261008.html) | 正文复用、指定题读取、有限预取和显示精简已实施；同输入测量、页面检查与真实生成的验证范围分别说明 |
| 题库结构简化 | [简化设计](question-bank-simplification-design-20261007.md) | 统一当前判定依据、内容摘要与升级协调已实现；保留设计取舍、兼容边界，以及未执行的真实转换、物理删表和实际旧机验收 |
| 首次刷新 | [性能调查](backend-first-refresh-performance-investigation-20261003.md) | 合并首次刷新与两秒目标的证据边界；真实更新后的完整页面耗时待验收 |
| 后端性能复审 | [实施与验证报告](backend-performance-reaudit-20261005.html) | 六项基础优化及完整分组三项已落实；首次耗时补充独立进程比较，缩减匹配范围尚未证明大幅提速。轻量计算表、批量试组和现有预热顺序调整为待验证方案。匿名规模与耗时经用户授权保存 |
| 训练推荐 | [实现与测量交接](training-recommendation-backend-handoff-20261004.md) | 定位当前相关入口与比较条件；实现规则仍以架构和产品文档为准 |
| 推荐优化候选 | [方法调查](training-recommendation-performance-options-20261004.md)、[方案图](training-recommendation-performance-options-20261004.html) | 比较可复用方法及未实施的适用条件 |
| 掌握度模型 | [模型调查](mastery-model-v3-plan-20261001.md) | 保留模型与参数选择依据；原型代码不用于当前运行 |
| 排版 | [排版调查](question-bank-latex-layout-evaluation-20261002.md)、[一手来源](latex-question-bank-primary-sources-20261002.md) | 已有合成实排依据，以及高中、断网新工作机和纸面验收的限制 |
| 相似题向量 | [实验](vector-similarity-experiment-20261001.md) | 只读实验依据；正式持久化、增量更新和模型接入尚未实现 |
| 外观审查 | [历史组件审查](frontend-component-audit-20261001.html) | 保留视觉比较材料；历史快照与旧数量不代表当前状态 |
| 页面文字与阅读层次 | [Impeccable 只读检查](impeccable-ui-audit-20261012.md)、[家长报告逐题整合评估](personal-report-question-detail-evaluation-20261012.md) | 学情与题库说明折叠、报告考查点折叠和手机正文字号已实现；报告原有页面顺序保留。逐题直接展示截图与完整内容，屏幕取消重复附录，打印保留全部失分题 |
| 题型改版 | [方案](question-type-redesign-plan-20261006.md)、[交接](question-type-redesign-handoff-20261007.md)、[进度网页](question-type-redesign-status-20261007.html) | 八上按题型诊断与配题；页面已人工验收，新导入、报告和权威文档已补齐。真实 v9 已获授权并启用；57 道预存失效题全部恢复，2 道歧义文字按用户确认修正。1292 道已标题型题均纳入；3 道低置信度题型抽查及实际训练收益仍待验证 |
| 题库标签与新册自动题型 | [方案](question-bank-labels-and-auto-types-plan-20261008.md) | 已确认的标签分层、知识点恢复可写、新册按章自动整理题型、按章切换训练目标与各模块影响；全部尚未实现，模型调用、数据库迁移与真实重打需逐次授权 |
| 关键步骤诊断 | [交接](key-step-diagnosis-handoff-20261009.md) | 技能改作失分步骤诊断的已确认决定、统计口径、只读原型与复用入口；试题诊断热点、学生作答证据与学情肖像的“反复卡点”、题型详情“卡在哪一步”和训练草稿“对应卡点”标签已实现，当前规则见阅卷与知识训练产品文档；跨考试同章复测的卡点追踪尚无数据 |
| 按标签多维筛选 | [重设计方案](question-bank-tag-filter-redesign-plan-20261008.md)、[交互原型](question-bank-tag-filter-prototype-20261008.html) | 已实施章节树与多维标签组合筛选、知识点精确匹配、旧地址兼容和本页技能入口退役；保留设计依据，当前规则见题库产品文档，原型只用合成数据 |
| 教研提升·试卷评价 | [实施方案](paper-review-implementation-plan-20261011.md)、本机原型 `paper-review-prototype-20261011/` | 已实现并通过正式 8035 的只读页面验收；阈值校准尚未实现。方案和本机原型保留取舍依据，当前规则见阅卷产品文档 |
| 教研提升·命题训练 | [设计方案](teacher-item-writing-training-design-20261012.md)、[实施交接](item-writing-implementation-20261012/README.md)、[中考官方要求](teacher-item-writing-research-20261012/zhongkao-standards.md)、[比赛与遴选调研](teacher-item-writing-research-20261012/competitions.md) | 单人按给定材料命一道题：八步分维度作答、上传识别核对、AI 评价与九维量表；本机原型 `item-writing-prototype-20261012/` 只用合成材料与示例评价。已确认复用 authoring 表并由 AI 从题库抽取原生情境作材料；迁移已授权、情境抽取已试运行 10 道；实施交接已写好，代码全部尚未实现，批量抽取与真实 AI 评价需另行授权 |
| 知识与训练页合并 | [合并方案](knowledge-training-merge-plan-20261009.md)、[交互原型](knowledge-training-merge-prototype-20261009.html) | 学情总览与知识结构合并为学情页，按章节与按学生合并为出训练页，去掉综合／专项并增加批量选人；已确认方向，原型只用合成数据，正式页面全部尚未实现 |
| 八下关键步骤种子词表 | [调查候选](g8-lower-key-step-seed-research-20261011.md) | 北师大版（2024）八下 113 条关键步骤候选与跨册前置、排除项清单；仅供老师审阅，系统不读取，判定点覆盖筛选与正式标准发布尚未进行 |
| 八上关键步骤词表审查 | [只读审查](g8-upper-key-step-audit-20261011.md) | 已启用 111 条关键步骤逐条处理意见（保留 81／仅客观题 13／改名 6／合并 5／退役 5／拆分 1）、473 个未挂判定点归类与多挂点主技能规则；仅调查建议，未改任何数据，执行须发布新知识标准并另行授权 |
| 任务中心改版 | [实施方案](task-center-redesign-plan-20261012.md)、本机原型 `task-center-prototype-20261012/` | 已实现方案一：右下角进度圆环、浮层列表、带倒计时条的结果卡片、开始提示、已查看任务 10 分钟后自动移出、进度弹框只留“转到后台”；合成数据原型保留作取舍依据 |
| 关键步骤新标准 v11 | [决定与发布步骤](key-step-release-20261012.md) | 用户对八上审查的决定、七上七下关键步骤词表、逐点判定资料、代码规则与副本演练结果；真实题库尚未发布，须单独授权 |

已被正式页面与权威文档取代的实施方案、验收流水和原型不继续占用当前目录。需要回看时，在 Git 历史中定位原文件；实际操作入口从 [项目 README](../../README.md) 查找。
