# 调查与待办索引

本目录保留仍可用于讨论的调查、实验和待办。产品行为以根目录 README 索引的权威文档为准。历史测量只适用于原文注明的输入和日期。

| 主题 | 保留入口 | 用途与未完成范围 |
|---|---|---|
| 待办 | [未完成事项](open-items.md) | 汇总仍未完成或待核实的候选，不充当已实现功能说明 |
| 首次刷新 | [性能调查](backend-first-refresh-performance-investigation-20261003.md) | 合并首次刷新与两秒目标的证据边界；真实更新后的完整页面耗时待验收 |
| 后端性能复审 | [实施与验证报告](backend-performance-reaudit-20261005.html) | 六项基础优化及完整分组三项已落实；首次耗时补充独立进程比较，缩减匹配范围尚未证明大幅提速。轻量计算表、批量试组和现有预热顺序调整为待验证方案。匿名规模与耗时经用户授权保存 |
| 训练推荐 | [实现与测量交接](training-recommendation-backend-handoff-20261004.md) | 定位当前相关入口与比较条件；实现规则仍以架构和产品文档为准 |
| 推荐优化候选 | [方法调查](training-recommendation-performance-options-20261004.md)、[方案图](training-recommendation-performance-options-20261004.html) | 比较可复用方法及未实施的适用条件 |
| 掌握度模型 | [模型调查](mastery-model-v3-plan-20261001.md) | 保留模型与参数选择依据；原型代码不用于当前运行 |
| 排版 | [排版调查](question-bank-latex-layout-evaluation-20261002.md)、[一手来源](latex-question-bank-primary-sources-20261002.md) | 已有合成实排依据，以及高中、断网新工作机和纸面验收的限制 |
| 相似题向量 | [实验](vector-similarity-experiment-20261001.md) | 只读实验依据；正式持久化、增量更新和模型接入尚未实现 |
| 外观审查 | [历史组件审查](frontend-component-audit-20261001.html) | 保留视觉比较材料；历史快照与旧数量不代表当前状态 |
| 报告结果版本统一 | [方案](report-result-versioning-plan-20261006.md)、[第二部分](report-result-versioning-plan-20261006-part2.md) | 错因、班级、个人报告改为"存一份最新、永远可读、按内容指纹决定重算"的待确认方案；尚未实现 |

已被正式页面与权威文档取代的实施方案、验收流水和原型不继续占用当前目录。需要回看时，在 Git 历史中定位原文件；实际操作入口从 [项目 README](../../README.md) 查找。
