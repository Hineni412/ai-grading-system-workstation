# 训练维护实验

本文件说明掌握度与训练推荐的只读维护实验。这些入口读取本机数据，不属于自动测试，也不改变产品常量。

## 掌握度前向检验

维护人员在项目根目录运行：

```powershell
& .\runtime\python\python.exe tools\mastery_validation.py --volume bnu24-math-g8-upper
& .\runtime\python\python.exe tools\mastery_validation.py --volume bnu24-math-g8-upper --initial
& .\runtime\python\python.exe tools\mastery_validation.py --volume bnu24-math-g8-upper --grid
```

默认只读 `user_data`；`--data-root` 可指定已有数据位置，不创建副本或迁移。工具用较早考试及测试考试之前的已发布训练证据预测后一次考试，比较当前模型、仅整体能力与直接做除法，并列出方案记录的旧公式基线。对数损失、Brier 使用全部有效观测，AUC 仅使用完全对与完全错的观测；分档应验率只统计训练、测试中都直接观察到的学生与目标，证据不足不定义应验率。网格以最近考试的对数损失选参数，差异不超过 0.002 时优先保留更大的知识点层差异。输出只含汇总指标、校准、分档覆盖、档位变化与无身份个案计数，不保存逐生结果、不调用模型。参数选择用于提交检验依据，不自动改变产品常量。

## 第一、二章训练卷适配实验

```powershell
& .\runtime\python\python.exe tools\experiment_training_fit.py --output output/test_training_fit_current/aggregate-report.json
& .\runtime\python\python.exe tools\experiment_training_fit.py --evidence-loss --output output/test_training_fit_current/evidence-loss-report.json
& .\runtime\python\python.exe tools\experiment_training_fit.py --match-audit --output output/test_training_fit_current/match-audit-report.json
```

该入口是独立的真实数据只读实验，不属于自动测试。默认核对本学期第一、二章及两章合卷；个人卷在该入口保留只补弱对照设置，未测目标上限为 0，使用原限制内换题，并核对选题与内存草稿构造一致。新建个人卷的补弱优先规则需用下述带补充上限的端点比较验证。小组卷保持原用途；补弱试配只保留能对应实际直接失分的题目，仍需对全体成员适用，不要求每题对全员补弱，不设个人覆盖差距合格线。数据库保持原位置，以只读连接读取，不保存草稿、不准备判定版本、不持久化学情、不复制数据库、不调用模型。报告仅包含匿名计数、按水平汇总与知识标准名称，不包含学生身份或题目正文。知识点与技能按教材归属计数，跨章关联单列；关联覆盖不等于独立教师确认的训练任务覆盖。已有训练资料的只读预览不能代表正式新建时可能补齐的资料，报告不作为学习效果或教师验收结果。

`--evidence-loss` 盘点考试关联、可用逐点记录、有效单判定点结果、教师总分及多判定点题的归因缺口；固定分数、题库、参数、学生与近期实际参加记录，比较现状、可用逐点信息退化为粗粒度小问得分、去掉教师多判定点总分、仅保留有效逐点记录、去掉最早考试及完整有效结果六种条件。退化条件不把多点总分保留为精确补弱来源；完整有效结果同时纳入有效逐点结果和可归属的单判定点题项结果，包含正确作答，不要求补造步骤。逐条件绕过持久化与共享学情缓存，比较个人选题和现有规则形成的小组成员；小组成员变化不代表共用候选已经通过资格检查。条件中的删证据只发生于内存，不是建议删除考试记录；只有逐点输入仍会丢弃有效客观题，不能当成教学金标准。结果是敏感性，不是“补齐后必然改善多少”的因果估计。

`--match-audit` 在内存中放宽难度与近期原题两项条件，再按原规则还原候选和严格补弱选题，分别追查无核心候选与已有候选未入卷。范围、有效资料和作答要求条件保持原状；题量与同技能限制继续按完整题执行，多个入卷限制允许重叠计数。匿名报告保留拒绝原因、标准名称及缺失目标的当前直接关联库存；库存为空不等于题库没有相同技能的题，须另查知识主题与技能关联。原用途规则的水平样本仅作兼容诊断，正式个人结果以严格补弱汇总为准。用于原位置核对的题目定位只即时显示，不写入报告，题面、答卷及学生身份不导出。匹配追查不能替代对学生水平和完整任务的独立教学判断。

推荐算法改动的前后配对比较使用 `tools/compare_training_endpoints.py --output <目录> --baseline-remediation-only --baseline-max-unmeasured-questions 4 --max-unmeasured-questions 4`：目录下 `baseline/`、`latest/` 分别放两版 `personalized.py`、`diagnosis_profile_service.py` 与 `question_tag_projection_service.py`，工具在同一只读输入上重跑第一章、第二章与两章合卷的个人卷和小组卷，核对两端个人选题与各自正式内存草稿一致，并按最新规则复核难度、失分关系与近期原题。仅优化 `personalized.py` 的性能时使用同一工具的 `--performance-only`：诊断与投影两份源文件必须相同，两端共用只读事务、诊断和固定时钟；在内存中按规范化 JSON 完整比较分组成员、目标、卡片、警告和来源版本，以及所有个人和小组草稿的题目、题序、用途、理由、缺口与警告。测量候选缓存清空后的分组、同进程再次分组、91 人个人草稿、5 人逐人草稿和最大组的所选成员复核；`performance.json` 只保存耗时、汇总人数及组数和代码摘要，每个范围通过后保存汇总，仅全部完成及来源不变时标记 `completed: true`。

效果报告使用 `tools/build_training_endpoint_report.py <目录>` 生成匿名 HTML，`tools/serve_training_endpoint_report.py <目录> --port <端口>` 在本机只读显示题面，只含匿名编号、题号和派生判断。当前效果依据为 `output/test_training_skill_model_v4_20261003/`，解释与性能交接见[当前实现与交接说明](../requests/training-recommendation-backend-handoff-20261004.md)。
