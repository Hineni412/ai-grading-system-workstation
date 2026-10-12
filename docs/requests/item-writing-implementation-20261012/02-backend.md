# 02 后端：服务、接口、任务

只有一种真实实现：不建接口层、注册表、策略层或适配层。模型调用、任务、费用确认、受控文件根目录都复用现有能力。防护按 `AGENTS.md`“防护边界”：本地免费保存只用版本号判断冲突；操作令牌只用于付费请求。

## 1. 文件

| 位置 | 内容 |
|---|---|
| `question_bank/authoring/__init__.py` | 对外导出 |
| `question_bank/authoring/rubric.py` | 九维量表与作答选项，原样搬自原型 `rubric-data.js`；前端从接口读取，不在前端另存一份 |
| `question_bank/authoring/store.py` | 材料、作品、版本、资产、评价记录的读写 |
| `question_bank/authoring/selfcheck.py` | 本机自检（第 4 节） |
| `question_bank/authoring/review.py` | AI 评价三步、输出校验、计分（04 第 2、3 节） |
| `question_bank/authoring/extraction.py` | 情境抽取候选、请求、校验、入库（04 第 1 节） |
| `question_bank/authoring/recognition.py` | 上传文件本机识别（第 5 节） |
| `question_bank/authoring/promote.py` | 作品转正（第 7 节） |
| `backend/jobs/authoring.py` | 两种后台任务：`authoring_review`、`authoring_extract` |
| `backend/api/routers/authoring.py`、`backend/api/schemas/authoring.py` | 接口 |

旧实现（`git show fb6a4648^:question_bank/authoring/service.py`）可以参考读写与错误分类写法；旧版对免费保存使用了操作令牌，新版不要沿用。

## 2. 作品内容

`authoring_works`：`kind='material'`，`source_question_id` 为 NULL（母题材料的来源题记在材料里），`source_snapshot_json` 存 `{}`，`task_card_json` 见 01 第 3 节，`title` 默认用材料标题。

`authoring_work_versions.content_json` 字段（与原型 `ITEM_SAMPLE_WORK` 对应，缺省值为空）：

| 字段 | 类型 | 步骤 |
|---|---|---|
| `save_seq` | 整数，每次保存加 1 | — |
| `started_at`、`elapsed_seconds` | 计时 | — |
| `picked` | 材料句子下标列表 | 1 |
| `sections` | 小节 knowledge_id 列表 | 1 |
| `core_idea` | 文本 | 1 |
| `intent`、`competencies`、`requirement_level` | 文本、列表、单值 | 2 |
| `context_kind`、`material_use`、`adapt_methods`、`realism` | 单值、单值、列表、文本 | 3 |
| `question_type`、`openness` | 单值 | 4 |
| `parts` | `[{ask, solo, score, answer, steps: [{text, score}]}]` | 4、6 |
| `stem`、`options`（`{A,B,C,D}`）、`correct_option`、`figure_asset_ids` | | 5 |
| `other_solutions` | 文本 | 6 |
| `difficulty`（1–10）、`predicted_rate`（0–100） | | 7 |
| `errors` | `[{description, error_class, cause}]` | 7 |
| `statement`、`talk_points` | 文本、列表 | 8 |
| `self_eval` | `{维度 key: 1–4}` | 评价 |

单值字段只接受 `rubric.py` 中对应选项；不合法时整次保存返回 422。

版本规则：
- 新建作品时建第 1 版。
- 当前版本还没有评价记录时，保存直接覆盖该版本的 `content_json`；已有评价记录时，保存新建下一版，`current_version` 加 1。
- 保存请求携带 `expected_version` 与 `expected_save_seq`；与库中不一致时返回 409 和当前值，前端保留草稿并提示刷新。

## 3. 接口（前缀 `/api/authoring`）

| 方法与路径 | 作用 |
|---|---|
| `GET /rubric` | 量表、作答选项、教材目录（复用 `curriculum_catalog`，只返回当前册） |
| `GET /materials?status=usable\|pending\|all` | 材料列表 |
| `POST /materials` | 教师录入材料（`kind`、标题、正文、情境类别、小节），`origin='teacher'`、`status='usable'` |
| `PATCH /materials/{id}` | 改标题、正文、状态；带 `expected_revision` |
| `GET /materials/extraction-scope?curriculum_volume_id=…` | 列出该册各章的候选统计：章 id 与名称、符合条件题数、已抽过题数、去重后待抽题数、单独提交该章的请求数。只读，不调用模型 |
| `POST /materials/extraction-preview` | 请求体 `{curriculum_volume_id, chapter_ids: [...], limit}`：按 04 第 1.1 节筛出本次题号（超过 `limit` 时按去重后的排序取前 `limit` 道），返回各章题数、本次题号、批数、计划请求数、`fingerprint`（册、章、题号列表与提示词版本的哈希） |
| `POST /materials/extraction-jobs` | 提交抽取任务；带 `client_request_token`、`fingerprint`、`curriculum_volume_id`、`chapter_ids`、`question_ids`、`authorization: {confirmed: true, request_limit}`；提交时重新计算预估，`fingerprint` 不同返回 409“题库已变化，请重新查看” |
| `GET /works`、`POST /works`（`{material_id}`）、`GET /works/{id}`、`DELETE /works/{id}`（软删除） | 作品 |
| `PUT /works/{id}/draft` | 保存草稿（第 2 节）；响应附最新自检结果 |
| `POST /works/{id}/assets`、`GET /works/{id}/assets/{asset_id}` | 上传、读取图片或文档 |
| `POST /works/{id}/recognize` | 本机识别（第 5 节） |
| `GET /works/{id}/review-preview` | 评价预估：版本号、计划请求数、阻断原因、`fingerprint`（版本内容哈希与量表版本） |
| `POST /works/{id}/review-jobs` | 提交评价任务；字段同抽取任务，另带 `version_no` |
| `GET /works/{id}/reviews` | 该作品全部评价记录 |
| `POST /works/{id}/promote` | 转正（第 7 节） |

费用确认的写法照 `backend/api/routers/question_bank.py::submit_question_repair`：先比对预估 `fingerprint`，再用 `client_request_token` 找已有任务；相同令牌、相同内容时返回原任务，内容不同返回 409。`authorization.request_limit` 必须等于预估的计划请求数，否则返回 422。

## 4. 本机自检（`selfcheck.py`）

保存时与读取作品时计算，返回 `[{code, step, message}]`，不写库。规则（与原型 `app-core.js` 一致）：
- 各步未完成。完成条件：
  1. 至少一句可用信息、至少一个小节、核心想法非空。
  2. 考查意图非空、至少一项核心素养、已选学业要求。
  3. 已选情境类别与材料运用方式、真实性说明非空；母题材料还必须至少选一种改编方法。
  4. 已选题型与开放性，至少一问，每问设问、目标层次、分值齐全。
  5. 题干非空；选择题四个选项和正确项都要有。
  6. 每问有参考答案，且至少一个评分步骤。
  7. 预估得分率已填，至少一行完整的典型错误（描述、错误大类、成因）。
  8. 命题说明非空，四个说题要点都已勾选。
- 小问分值合计不等于材料要求分值。
- 某问评分步骤合计不等于该问分值，提示写明是第几问。
- 选择题没有选正确项。
- 已用时超过限时。
- 与题库相似度：只在评价预估时计算，用 `similarity_service.question_text_profile` 与 `profiled_text_similarity` 比较题干与全部未删除题目，返回最高值及对应题号；材料有来源题时另算与来源题的相似度。不调用模型。

评价预估的阻断原因：没有题干，或任一小问没有参考答案。其余自检问题只提示，不阻断。

## 5. 上传识别（`recognition.py`）

- Word：`backend/document_parsing/text.py::extract_docx_text`。
- 有文字层的 PDF：`extract_pdf_text`；文字为空时按扫描件处理。
- 扫描 PDF：`question_bank/importers/mineru_parse.py::parse_pdf_full`，输出带 LaTeX 的文本；模型文件不全时返回不可用。
- 图片：先试 `mineru.parse` 能否直接处理图片（需实施代理确认，不能确认就不用）；否则用 `backend/document_parsing/local_ocr.py::get_local_ocr()` 逐行识别，把置信度低于 0.8 的行列入 `uncertain`。
- 返回 `{text, uncertain: [片段], engine: 'docx'|'pdf_text'|'mineru'|'local_ocr'|'unavailable'}`。前端只填入对应输入框，等待教师核对，不自动保存。
- 本次不实现模型识别兜底：现有代码没有为此写好的识别提示词。本机识别不可用时，页面提示“请手动录入”。

## 6. 后台任务（`backend/jobs/authoring.py`，在 `default_handlers.py` 注册）

`authoring_extract`：
- 输入：题号列表、`client_request_token`、`fingerprint`。
- 每批 10 道，一批一个请求，按 04 第 1 节执行；某批失败就停止，已完成的批次保留，结果列出已建材料、不适用题号和失败批次。
- 不自动重发；结果不确定（超时、断线）时记为失败，提示到“调用记录”核对。

`authoring_review`：
- 开始时插入 `authoring_reviews`（`status='running'`，`operation_token`＝`client_request_token`）。
- 依次执行：
  1. 题目分析：`AITaggingService.analyze_question(TaggingContext(...))`，题干、各问设问与选项拼成 `question_text`，各问答案拼成 `answer_text`，`curriculum_volume_id` 取材料小节所属册。实施前先确认这一调用实际会发几次请求；如有质量重试或复核请求，关掉它们，或在预估中按真实上限显示。小问难度用 `standard_difficulty.feature_raw` 与 `formula_score` 由返回的难度特征计算。
  2. 独立解题。
  3. 量表评分。
- 成功写 `result_json`：`{analysis, solving, solving_compare, dims, summary, scores, total, cap_violations, similarity, local_checks}`；`model_summary_json` 记模型名、每步 operation_id 与请求数。
- 任一步失败：`status='failed'`，`error_category` 用 `backend/llm/errors.py` 的分类或 `parse`、`validation`，保留已完成步骤的结果。没有收到返回时 `status='unknown'`。都不自动重发；重新评价需要用户再确认一次。
- 同一作品同时只允许一个运行中的评价。

一次性导入试运行结果：迁移完成后，用 `extraction.py` 的同一入库函数读取 `tmp_item_writing_trial/trial-20261012.json`，建 10 行材料（7 行待核对、3 行停用），不调用模型。这一步是脚本写真实库，执行前先告诉用户精确内容并取得确认。

## 7. 作品转正（`promote.py`）

- 只允许已有成功评价的版本；页面二次确认。
- 写入题库：查找标题为“命题训练作品”的试卷，没有就新建（`exam_type='自命题'`、当前册的年级与学期）；用 `QuestionWriteService.add_question(QuestionCreate(...))` 写入题干、答案、题型；题号按该卷已有题数顺延。写入后照常进入题库待补齐清单，判定点与标签由现有补齐流程另行确认费用后生成。
- 在 `task_card_json.promoted` 记录 `{version_no, question_id, promoted_at}`；同一版本重复转正时返回已有题号，不重复写入。
- 转正后的题会进入题库列表、组卷与推荐，这是用户确认的去向。未转正作品不写入任何题库题目表。

## 8. 装配

- `backend/api/dependencies.py`：按现有题库服务的写法提供 authoring 服务（题库库路径与 `user_data` 根）。
- `backend/api/app.py`、`backend/api/routers/__init__.py`：挂载路由。
- 存储不可用（迁移未执行）时接口返回 503 `authoring_storage_unavailable`，页面提示需要先完成程序更新。
