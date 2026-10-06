# 报告结果的版本与指纹统一方案

用途：按用户 2026-10-06 的要求，把错因整理、班级报告、个人报告三处"版本号 + 指纹"的存取规则统一为三条简单规则。本文是待确认方案；**尚未实现**。确认后按本文实施，实施完成后把规则写入 `docs/product/GRADING.md`，本文删去或改为迁移记录。

## 1. 要解决的问题

2026-09-22 生成的 89 份个人报告叙述现在全部显示"未生成"。原因不是文件丢了（`user_data/reports/.analysis_narrative_cache/` 里仍有 89 个文件），而是找文件用的"钥匙"变了：

- 钥匙 = sha256(考试 id + 成绩指纹 + 提示词版本 + 对象)。钥匙即文件名，对不上就等于内容不存在。
- 10-01 把个人叙述提示词版本从 `v9` 升到 `v12`，同时把兼容旧版本的名单清空。
- 成绩指纹把数据库里每个成绩行、明细行、复核锁和题库关联原样打包计算；后来数据库结构调整新增字段，分数没变指纹也全变。用现在的算法对 3 场考试 × 5 个历史版本号逐一试算，0 命中。
- 当时没有"学生 → 文件"的索引，所以也走不到"成绩已变化、显示上次生成"的降级路径。
- 缓存文件内部只有正文，没有考试和学生编号，事后无法对回学生。

这套机制想解决的两件事值得保留：**没变就不重算（省费用）**、**成绩改了要提醒过期**。多余的是：把指纹当文件名、把数据库原始行塞进指纹、每换一次提示词就手工维护版本常量与兼容名单。

## 2. 现状盘点

| 模块 | 存在哪 | 现在怎么判"当前" | 对不上时 |
|---|---|---|---|
| 错因整理（逐题） | `.class_analysis/<场次>.json` 的 `cause_analysis.questions[题号]`，带 `version`、`input`、`input_fingerprint`、`result`、`history` | `version ∈ {v4, v3}` 且输入指纹相同；v3 另有"按步骤前"的兼容判定；v2/v1 文本相同仍可展示但标过期 | 读取端按 4 个版本常量分支判定；手动整理时重发 |
| 班级报告（逐班） | 同一状态文件的 `class_reports[班级]`，带 `status`、`narrative`、`cause_digest`；正文另存叙述缓存 | 场次级 `score_revision` 与 `rendition_version`（`class_analysis_page_v4_cause_payload`）都相同，且该班错因摘要相同 | 整体视为待生成，但旧叙述对象仍在 |
| 个人报告（逐人） | 叙述缓存 `.analysis_narrative_cache/<钥匙>.json`；索引 `personal_index/session_<场次>.json`（10-01 后才有） | 先按学生级指纹算钥匙；再按场次指纹 × (当前版本 + 兼容名单) 算钥匙；都不中再看索引里上次的钥匙，有则"成绩已变化" | 无索引的旧文件永远找不到 |

涉及的版本常量：`CAUSE_ANALYSIS_VERSION`(v4)、`CAUSE_PRE_STEP_VERSION`(v3)、`CAUSE_OUTDATED_VERSION`(v2)、`CAUSE_LEGACY_VERSION`(v1)、`CLASS_ANALYSIS_RENDITION_VERSION`、`report_narrative_version()`(个人 v12)、`_REPORT_RENDITION_VERSIONS`(导出 v15)、`LEGACY_PERSONAL_NARRATIVE_VERSIONS`(空)。`OPTION_ANALYSIS_VERSION`、`ENHANCER_VERSION`、`_CROP_RENDER_VERSION` 属于题库候选与图片处理，不在本次范围。

## 3. 统一规则

三个模块共用同一套词汇和同一个判定函数：

1. **存**：按"考试 + 对象"固定位置只存**一份最新结果**。对象 = 题号 / 班级名 / 学生 id。每份结果旁记四样东西：`generated_at`、`input_digest`（见规则 3）、`prompt_version`（当时用的提示词版本）、`origin`（模型 / 题库映射 / 答案库）。
2. **读**：永远能读到那份最新结果，内容不会因为版本或指纹变化而消失。读取时只打两个标记：
   - `input_digest` 与现在算出来的不同 → `stale`（界面文案"成绩或错因已变化，显示上次生成"）；
   - `prompt_version` 与现在的不同 → `old_prompt`（界面文案"用旧版提示词生成"）；
   - 两者都相同 → `current`。
   不再有兼容名单，不再按版本常量分支。
3. **生**：手动"AI 整理"或自动模式下，只重算 `stale` 的对象；`old_prompt` 但不 `stale` 的对象**不自动重算**，由用户在界面上点"用新版提示词重新生成"时才花费。`input_digest` 只包含会改变报告内容的字段，按模块定义见第 4 节；不包含数据库原始行、不包含提示词版本。

## 4. 各模块的 `input_digest`

| 模块 | 纳入字段 | 不纳入 |
|---|---|---|
| 错因整理（题） | 题目正文、参考解答、评分标准投影、该题全部证据（批语、作答、步骤、前问作答，去身份后） | `known_patterns`（现状已排除）、提示词版本 |
| 班级报告（班） | 该班学生的逐题最终得分与满分、教师最终分锁（学生 id + 题号 + 分数）、该班错因记录摘要 | 成绩行的其他列、题库关联、提示词版本 |
| 个人报告（人） | 该生逐题最终得分与满分、该生的教师最终分锁、该生各题的错因记录、题目正文与参考解答（报告里会引用） | 其他学生的数据、数据库原始行、提示词版本 |

错因整理的指纹与现状 `_cause_input_fingerprint` 一致，不需要迁移；班级与个人的指纹是新算法，迁移时直接按新算法计算一次写入（第 6 节）。

## 5. 存储形状

- 错因：沿用 `cause_analysis.questions[题号]`，字段改为 `{result, input, input_digest, prompt_version, origin, generated_at, failed, failed_input_digest}`。删除 `version`、`history`（历史留在 Git 和日志，不在状态文件里累积）。`input` 必须保留：stale/old_prompt 结果要按生成时的证据列表（`input["evidence"]` 与 E1… 编号）投影展示并做成员匹配，不能删除。
- 班级：沿用 `class_reports[班级]`，字段改为 `{narrative, input_digest, prompt_version, generated_at, status}`。叙述正文直接放在这里，不再另写叙述缓存；场次级 `score_revision`、`rendition_version` 字段删除。
- 个人：新目录 `.personal_reports/<场次>/<学生id>.json`，内容 `{narrative, input_digest, prompt_version, generated_at}`。`personal_index` 与按钥匙命名的叙述缓存不再使用。

三个模块的"读取并打标记"由一个函数完成：`resolve_result_state(stored, current_digest, current_prompt_version) -> {"status": current|stale|old_prompt|missing, ...}`，放在 `backend/report_results.py`（新模块，仅此一处实现，不建接口层）。

（续见 [第二部分](report-result-versioning-plan-20261006-part2.md)：读取端与界面、迁移、费用与影响、实施步骤。）
