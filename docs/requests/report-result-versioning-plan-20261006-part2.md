# 报告结果的版本与指纹统一方案（第二部分）

接 [第一部分](report-result-versioning-plan-20261006.md)。本文是待确认方案；**尚未实现**。

## 6. 读取端与界面

- 成绩明细页学生行的报告状态钮：`current` ✓、`stale` ↻、`old_prompt` 显示为 ✓ 并在悬停说明里加"用旧版提示词生成"，`missing` ○。沿用现有四种视觉，不新增按钮。
- 班级分析页：叙述段落标题旁的状态文字按同一词汇显示；"AI 整理"按钮说明里的待补数量 = `stale + missing` 的对象数，不再把 `old_prompt` 算进去。
- 错因整理页：现在的"兼容旧版 / 等待升级"提示合并为 `old_prompt` 一种；按钮"重新整理"只对该题生效。
- "AI 整理"状态接口（`build_report_pipeline_status`）与费用预估（`build_analysis_preflight`、`plan_cause_calls`）改读新存储，命中判定统一用 `resolve_result_state`。
- 个人报告阅读器（`PersonalReportReader`）读新目录；"查看报告（数据版）"路径不变。

## 7. 迁移（一次性、可重复执行）

应用启动时对 `user_data/reports` 做一次迁移，已迁移的场次跳过：

| 旧数据 | 处理 |
|---|---|
| 错因 `questions[题号]` 有 `result` 且 `version ∈ {v4, v3}` | 迁入：`input_digest` = 原 `input_fingerprint`（算法相同），`prompt_version` = 原 `version`，保留 `input`（stale/old_prompt 结果按生成时的证据投影展示），删 `history`、`version` |
| 错因 `version ∈ {v2, v1}` | 迁入为 `input_digest = ""`（读出来即 `stale`）、`prompt_version` = 原 `version`，内容仍可看 |
| 班级 `class_reports[班]` 有叙述 | 若场次级 `score_revision`、`rendition_version` 与当前算法一致，`input_digest` 用新算法现算（视为当前）；否则 `input_digest = ""`（`stale`，显示上次生成）。叙述正文从缓存文件搬入状态文件 |
| 个人：`personal_index` 里有记录的学生 | 读索引里的钥匙对应文件，写入 `.personal_reports/<场次>/<学生>.json`；索引里的 `student_revision` 与当前学生指纹相同则现算 `input_digest`，否则 `""` |
| 个人：无索引的旧文件（9 月 22 日的 89 个） | 无法对回学生，不迁移；文件原地保留，另行授权后再删除 |
| 场次级 `score_revision`、`rendition_version`、`narrative`、`narrative_error` | 删除字段；`status`、`auto_generate`、`generated_at` 保留 |

迁移只写 `user_data/reports` 下的状态与报告文件，不碰成绩数据库。迁移前把 `.class_analysis/` 与 `personal_index/` 复制到 `user_data/reports/backup/<时间>/`。

## 8. 费用与影响

- 迁移本身不调用模型。
- 迁移后第一次"AI 整理"：第四周那场（session 5）的错因、班级、个人今天刚生成且无变化，预计 0 次调用；第二、三周两场的个人叙述本来就读不到，重新生成各约 83–85 次调用，与现状一致，不因本方案增加。
- 以后升级提示词：旧报告继续可看、标"旧版"，不再自动重跑；想用新版由用户逐场点"重新生成"。
- 以后数据库加字段：不影响任何报告状态。
- 删除的概念：8 个版本常量中的 6 个（4 个错因版本、班级版式版本、个人兼容名单），`history` 累积，按钥匙命名的叙述缓存，`personal_index`。保留 `report_narrative_version()` 一个函数作为"当前提示词版本"的唯一来源（各模块各一个字符串）。

## 9. 实施步骤

1. 新建 `backend/report_results.py`：`resolve_result_state`、三个模块的 `input_digest` 计算、新存储的读写；补对应测试。
2. 错因整理改用新存储与判定；删除 v1–v4 分支及 `cause_input_matches` 的兼容路径；`apply_cause_results` 的 `fresh/pre_step/outdated/legacy` 四态合并为三态；更新 `tests/test_class_analysis.py`、`tests/test_typical_error_flow.py`。
3. 班级报告改用新存储；`_class_narrative` 去掉缓存层；`class_report_entry_current` 改为 `resolve_result_state`；更新 `tests/test_report_pipeline*.py`。
4. 个人报告改用新目录；`personal_report_states`、`lookup_personal_narrative`、`publish_personal_index`、`student_report_revisions` 收敛为新模块的函数；更新 `tests/test_analysis_report.py` 与阅读器相关测试。
5. 迁移函数与启动挂载；用本机真实 `user_data/reports` 的副本做一次只读演练，核对三场考试迁移后的状态数（预期：session 5 全部 `current`；session 3、4 错因 `current`、班级 `stale` 或 `missing`、个人 `missing`）。
6. 前端：状态文案与悬停说明；`ClassAnalysisPanel`、`ResultsCenterView`、`PersonalReportReader` 读新接口字段；更新前端测试。
7. 文档：`docs/product/GRADING.md` 写入三条规则与状态词汇，`CONTEXT.md` 增加 `current / stale / old_prompt / missing` 的定义，`ARCHITECTURE.md` 更新报告数据归属（`.personal_reports/`），`docs/maintenance/storage-policy.md` 更新目录清单；本文改为迁移记录或删除。

步骤 1–5 为后端，可在一次改动里完成并验证；步骤 6 依赖接口字段定稿后进行；每一步独立提交。

## 10. 需要用户确认的点

1. `old_prompt` 不自动重跑、由用户逐场手动触发——是否接受？（另一种选择：自动模式下也重跑，费用更高）
2. 删除 `history`（错因结果的历次版本列表）——现在界面没有展示它，是否接受不再保留？
3. 迁移后旧的 89 个叙述文件与按钥匙命名的缓存目录：保留原地，还是迁移成功后授权删除？
