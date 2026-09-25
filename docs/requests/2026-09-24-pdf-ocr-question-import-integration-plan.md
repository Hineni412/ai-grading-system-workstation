# 扫描版 PDF 入库与 AI 补答案接通方案

日期：2026-09-24
用途：用户要求的接通方案。
状态：方案一、方案二已于 2026-09-24 实施，公式级识别于 2026-09-25 接通：
MinerU-4 ONNX 模型（版面 PP-DocLayoutV2 + 公式 PP-FormulaNet_plus-M +
表格/印章，约 840MB）已下载到 `runtime/models/mineru/MinerU-4_models_onnx/`，
扫描版 PDF 无文字层时优先走 `mineru.parse(tier="basic")`（`mineru_parse.py`），
输出含 LaTeX 的 Markdown 并剥掉标题符后再切题；缺模型文件时自动回退行级 OCR。
已用含公式合成扫描卷真实验证：公式识别为 `$...$`、答案区正确剥离、全题待复核。

方案二落地为 `answer_draft` 作业 + `POST /api/question-bank/answer-draft-jobs` +
试卷库批量条"AI 补答案"按钮（弹层确认，明示费用与待复核）。
已知未接线：会话归档同步（question_bank_sync）与阅卷归档
（grading_paper_intake）路径的 import_scanned_papers 调用未传管线，
保持原行为；扫描页原图尚未落到题目 image_paths（复核对照暂依赖归档原卷）。

## 背景与现状

用户目标：识别 PDF（带文字层和纯图片扫描版），无论是否带答案都入库，
题目结构化后用于组卷；没答案的可走 AI 生成答案草稿。

现状调查结果：

- 依赖已就位：`requirements.txt` 声明 `mineru==4.0.3`、PyMuPDF；
  `constraints.txt` 已锁定全套依赖。
- 本地 OCR 模型已预置：`runtime/models/mineru/MinerU-4_models_onnx/OCR/paddleocr/`
  下 PP-OCRv6 检测+识别 ONNX，纯 CPU、不联网。
- `QuestionDocumentPipeline`（`question_bank/document_pipeline/pipeline.py`）已实现：
  逐页渲染 PNG、有文字层走 PyMuPDF 块、无文字层走本地 OCR、双栏阅读顺序、
  幂等快照、复核状态机、发布与 Word 导出。**但目前没有任何生产代码实例化它**，
  只有 `tests/test_question_document_pipeline.py` 在用。
- 主导入通道 `import_scanned_papers`（`question_bank/importers/batch_importer.py`）
  调 `import_pdf(path)` 不传管线：无文字层 PDF 只得 `needs_ocr` 结果，不入库
  （`_extract_paper` 约 795 行）。
- AI 补答案不存在：`ai_tagging_service` 只补知识点/难度等标签，不写答案。

## 方案一：扫描版 PDF 入库（接通 OCR）

核心思路：把已有的文档管线接进现有导入作业，**复用现有题目切分、答案区剥离、
去重和 `needs_review` 复核机制**，不新增入库语义、不新增页面。

### 改动点

1. `backend/jobs/question_import.py`：构造
   `QuestionDocumentPipeline(workspace_root=data_root/"question_bank"/"document_pipeline")`，
   传给 `import_scanned_papers`。
2. `question_bank/importers/batch_importer.py`：
   - `import_scanned_papers` 增加可选 `document_pipeline` 参数并透传到 `_extract_paper`。
   - `_extract_paper` 中 PDF 分支改为
     `import_pdf(path, document_pipeline=..., operation_id=f"import-{sha256}", source_id=...)`。
     `operation_id` 用文件内容哈希派生：同一文件重试命中已有快照，天然幂等。
   - 管线返回的 `ExtractedDocument.text` 已是按阅读顺序拼接的块文本，
     继续走现有 `parse_paper_text`：题号切题、答案区剥离、题型识别全部不变。
   - OCR 来源的页（`TextLayerState.LOCAL_OCR`）存在时，给该卷所有题强制
     `needs_review=True`，并把页面渲染 PNG 路径写入 `image_paths`/
     `needs_image_review`，让教师在复核界面能对照原扫描页。
   - OCR 模型缺失或失败时管线产出空文字页 → 维持现有 `needs_ocr` 返回，
     行为与 ARCHITECTURE.md 一致（"缺少依赖或模型时…文档管线保留空文字页和待复核状态"）。
3. 无需改前端：`needs_review` 已是既有字段和筛选概念；
   `PaperLibrary`/`QuestionInspector` 的待复核呈现直接可用。

### 已知限制（如实告知用户）

- 本地只有文字检测+识别模型，无版面分析/公式识别：扫描件里的数学公式会退化为
  普通文字（可能错乱），依赖 `needs_review` + 页面图人工修正。
- PP-OCRv6 tiny/small 在 CPU 上逐页识别，大页数扫描卷导入较慢；
  作业已有取消检查，按文件粒度生效。
- 双线框/图片题无法还原图形，OCR 文字可能残缺——同样落入待复核。

### 验证办法（合成数据）

- 用 PyMuPDF 生成两类测试 PDF：纯文字层卷、纯图片扫描卷（文字渲染成图再导出），
  各含编号题目与"参考答案"区。
- 断言：文字层卷照常入库；扫描卷 OCR 后按题号入库、答案区剥离、
  全部题目 `needs_review`、页面 PNG 已落盘且 `image_paths` 可解析。
- 同一文件重复提交：命中快照幂等，不产生重复卷。
- 临时移走模型文件：行为回退为 `needs_ocr`，不报错。

## 方案二：AI 补答案（新能力）

触发方式：用户在题库中对选中的题（或整卷）显式点"AI 补答案"——
点击即本次调用的授权点，不自动批量跑全库。

### 改动点

1. 新增 `question_bank/services/answer_draft_service.py`：
   - 输入：题目 id 列表；跳过已有 `answer_text` 的题（或用户选"覆盖重生成"时另议）。
   - 调 `llm_client.json_from_text`（复用现有模型档案与用量记录），
     产出 `{answer, analysis, confidence}` 结构。
   - 写入 `questions.answer_text`，同时 `needs_review=1` 标记来源为 AI 草稿，
     等待教师确认；记录生成所用的模型档案与 prompt 版本（沿用
     `ai_tagging_service` 的溯源惯例）。
2. 复用作业体系：新增一个 job type（参照 `tagging_sync` 的幂等提交、
   进度上报、重试模式），入口端点挂在 `question_bank` 路由，
   前端在 `PaperLibrary`/题目列表加操作入口（融入现有批量操作，不新增面板）。
3. 导入后衔接：导入结果中无答案题计数已有（`needs_review` 原因之一即无答案），
   前端可在导入完成提示中给出"对 N 道无答案题补答案"的快捷入口，复用同一作业。

### 明确不做

- 不让 AI 答案直接成为"已确认"：一律落为待复核，教师确认前与普通无答案题
  同等对待。
- 不为公式渲染新增推断；AI 产出的答案以文本/LaTeX 存，展示沿用现有富文本路径。

## 实施顺序

1. 方案一（OCR 接通）：改动集中在 `question_import.py` + `batch_importer.py`，
   合成 PDF 验证。
2. 方案二（AI 补答案）：新服务 + 新 job type + 前端入口，合成题验证
   （模型调用用替身，不真实调用）。

## 涉及共享文件说明

- `batch_importer.py` 是题库导入的热点文件，但本次改动仅在 PDF 抽取分支
  增加可选参数与 OCR 标记透传，不改 docx/文字层路径语义，不影响阅卷模块。
- `question_import.py` 同理：仅新增管线构造与透传。
