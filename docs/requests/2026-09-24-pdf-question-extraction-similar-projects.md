# 试卷 PDF/扫描件 → OCR → 题目结构化 → 题库：同类项目调研

核验日期：2026-09-24。用途：为题库模块「扫描版试卷导入」方向提供选型与参照；本文是调研笔记，不代表本项目已实现其中任何能力。star 数为查询当日 GitHub 页面数据，会随时间变化。

## 调研结论

- 文档解析/OCR 这一层已经非常成熟：MinerU、PaddleOCR-VL、Docling、Marker、olmOCR 等都能把扫描 PDF 转成带 LaTeX 公式的结构化 Markdown，多个引擎可本地离线跑。
- 「试卷 → 逐题结构化（题号/题干/选项/答案/解析）」没有公认标杆，业界做法分两类：规则解析（题号正则 + 题型启发式）或交给 LLM/VLM 抽取，代表项目 star 都很低（0~200），属于探索期。
- 「导入 → 结构化 → 组卷 → AI 补答案」全链路开源项目里，与本项目形态最接近的是 JudgePeach/math-question-bank（本地运行、数学题库、PDF 双策略拆题、AI 解题、组卷排版导出），但体量小、AGPL、未经大规模验证。这一全链路基本是市场空白，可作为本项目差异点。
- 商业产品（菁优网、组卷网）的「拍照组卷/搜整页」证明了「整页多题切分 + 识别入库」是成熟产品形态，但它们是云端题库匹配，不是本地离线导入。

## 1. 通用文档解析 / OCR 引擎

这些项目解决「扫描件 → 结构化文本（含公式）」，可直接作为本项目 OCR 层的参照或候选。

### 1.1 MinerU（OpenDataLab）⭐ ~78k

- 地址：https://github.com/opendatalab/MinerU
- 定位：高精度文档解析引擎，PDF/DOCX/PPTX/图片 → Markdown/JSON，面向 LLM/RAG/Agent 场景。
- 关键能力：公式自动转 LaTeX、表格转 HTML、自动检测扫描 PDF 并启用 OCR、OCR 支持 109 种语言、去页眉页脚、按阅读顺序输出；含 pipeline 与 VLM 双引擎（[README](https://github.com/opendatalab/MinerU)，[arXiv 论文](https://arxiv.org/pdf/2409.18839)）。
- 与本项目差距：输出是「整页结构化文档」，不识别「这是一道题 / 这是答案区」——题目切分需自己接下游规则或 LLM；license 为 GitHub 标注的自定义协议（"Other"），打包分发前需复核条款。本项目已部署其 PP-OCRv6 ONNX 文字模型（`local_ocr.py`），但 MinerU 完整管线（版面分析 + 公式识别）比单文字 OCR 多一层，扫描卷里的数学公式仅靠文字 OCR 拿不到 LaTeX。

### 1.2 PaddleOCR / PaddleOCR-VL（百度）⭐ ~88k

- 地址：https://github.com/PaddlePaddle/PaddleOCR
- 定位：OCR 全家桶；PaddleOCR-VL-1.6 为 0.9B 轻量文档 VLM，OmniDocBench v1.6 达 96.3%，文本/公式/表格识别开源 SOTA（[官方文档](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.en.md)，[arXiv](https://arxiv.org/html/2510.14528v4)）。
- 与本项目差距：中文场景最强候选之一，Apache-2.0 友好；但 VL 模型需 GPU/较多资源，纯文字 ONNX 路线（本项目现状）更轻。同样只管「识别」，不管「切题」。

### 1.3 Docling（IBM）⭐ ~66k

- 地址：https://github.com/docling-project/docling
- 定位：多格式文档 → 统一结构表示（DoclingDocument），MIT 协议，可本地跑；版面分析用 DocLayNet、表格用 TableFormer（[IBM 论文](https://research.ibm.com/publications/docling-an-efficient-open-source-toolkit-for-ai-driven-document-conversion)）。
- 与本项目差距：英文/科研文档起家，中文试卷与数学公式不是其主场；下游题库项目 DocumentLynx 用它做过渡层（见 2.1）。

### 1.4 Marker（datalab.to / VikParuchuri）⭐ ~39k

- 地址：https://github.com/VikParuchuri/marker
- 定位：PDF → Markdown/JSON 的高速转换器；「有文字层就直接抽取、不好才 OCR」的策略与本项目「嵌入文本优先、缺失才本地 OCR」完全一致（[作者说明](https://threadreaderapp.com/thread/1955355127818358929)，[Mozilla Builders](https://builders.mozilla.org/project/marker/)）。
- 与本项目差距：同样不识别题目边界；公式转 LaTeX 依赖其 texify 模型。GitHub 当前标注 Apache-2.0，历史版本曾有限制性条款，分发前建议复核。

### 1.5 olmOCR（Allen AI）⭐ ~19k

- 地址：https://github.com/allenai/olmOCR
- 定位：用 VLM 把 PDF 线性化为干净文本，偏英文训练语料生产（[Ai2 博客](https://allenai.org/blog/olmocr)）。需要 GPU，单人维护为主。
- 与本项目差距：中文试卷非目标场景，参考价值在「VLM 直出结构化文本」路线本身。

### 1.6 dots.ocr（小红书 rednote-hilab）⭐ ~9k

- 地址：https://github.com/rednote-hilab/dots.ocr
- 定位：1.7B 单模型统一版面检测 + 识别（公式/表格/图片分区），多语言，可转 SVG 图形（[README](https://github.com/rednote-hilab/dots.ocr)）。
- 与本项目差距：推荐 vLLM 部署，对 Windows 本地教师机偏重；它的「按区域类型输出 bbox + 文本」恰好是切题需要的前置信号，可作分区标注参照。

### 1.7 GOT-OCR2.0（中科院/阶跃）⭐ ~8k

- 地址：https://github.com/Ucas-HaoranWei/GOT-OCR2.0
- 定位：580M 端到端 OCR-2.0 模型，公式/表格/图表/乐谱统一识别，支持区域级交互 OCR（[arXiv](https://arxiv.org/abs/2409.01704v1)）。
- 与本项目差距：仓库 2025-02 后基本停更（232 个 open issue）；适合作「对单个公式/区域补识别」的备选，不宜当主管线。

## 2. 试卷/题目抽取方向的开源项目

这个细分方向整体处于探索期，没有高 star 标杆。

### 2.1 DocumentLynx ⭐ 0（但架构最对口）

- 地址：https://github.com/sajadreshi/documentlynx
- 定位：多智能体试卷 → 题库流水线，FastAPI + React，与本项目栈一致。
- 做法：Docling 把 PDF 转 Markdown（保留公式/表格）→ LLM 校验结构（失败重试 3 次）→ LLM 逐题抽取存 PostgreSQL → LLM 按题型/主题/难度/认知层级分类 → 向量入库支持语义搜索；前端有 KaTeX 实时渲染的分栏编辑器供人工修订（[README](https://github.com/sajadreshi/documentlynx)）。
- 借鉴价值：「解析器出文本 → LLM 出题目结构 → 人工编辑确认」三段式是本项目可直接采用的最小闭环；差距是依赖 GCS/Groq 云服务，无本地离线设计。

### 2.2 JudgePeach/math-question-bank（MathBank）⭐ ~200 —— 与本项目最像

- 地址：https://github.com/JudgePeach/math-question-bank
- 定位：面向中学数学教师的**完全本地运行**题库 + 组卷排版工作台，Windows 便携包解压即用。
- 与本项目重叠度极高的设计（[README](https://github.com/JudgePeach/math-question-bank)）：
  - PDF 拆解双策略：优先「原生文字层提取」（0 视觉成本、毫秒级），公式被转成图片时自动降级触发 VLM 视觉 OCR——即本项目「文字层优先、扫描降级」思路的完整实现；
  - Word 导入解析 OMML / MathType（OLE `Equation Native` 流），转换不出的公式保留预览图并标记人工核对；
  - DeepSeek AI 解题补答案、LaTeX 实时预览、一键组卷 + A4 仿真排版 + 高考级 PDF 导出。
- 差距：AGPL-3.0（只能借鉴思路不能搬代码）；个人项目、star 低、未大规模验证；专注数学单科。

### 2.3 shiroha-quiz ⭐ ~183

- 地址：https://github.com/reiqr/shiroha-quiz
- 定位：轻量刷题应用，主打「散乱题库文件自动识别导入」。
- 值得借鉴的细节（[README](https://github.com/reiqr/shiroha-quiz)）：题目文件与答案文件分开上传按题号自动匹配；「一、单选题」分区标题继承题型；识别结果逐题预览 + 异常标记 + 核对筛选器；AI 辅助分三类（整理原文/核对导入结果/补解析）且「涉及答案、题型的写入都应经过用户确认」；扫描 PDF OCR 走「先转成文本/DOCX、人工核对后再入库」的保守路径。
- 差距：面向学生刷题而非教师题库生产；题型集中在选择/判断/填空；OCR 入口是测试性功能。

### 2.4 gygy-open/question-bank ⭐ ~26

- 地址：https://github.com/gygy-open/question-bank
- 定位：「AI 原生题库系统」，FastAPI + Vue/Nuxt + MySQL + ChromaDB。
- 借鉴点：三步导入流程「上传 → 审核 → 入库」；题目走「草稿 → 待审 → 发布 → 归档」审核工作流 + 审核日志 + 软删除；知识点 RAG 把 AI 推荐映射到标准体系；架构图明确标注发给 AI 的是「脱敏题目文本」（[README](https://github.com/gygy-open/question-bank)）。
- 差距：导入只支持 Word/Markdown/图片，没有扫描 PDF 管线；依赖外部大模型，无本地离线兜底。

### 2.5 6wa1t/408-ai-tutor ⭐ ~11

- 地址：https://github.com/6wa1t/408-ai-tutor
- 定位：考研刷题 + AI 助教，FastAPI + Streamlit + SQLite。
- 借鉴点：正是用 **MinerU 做扫描 PDF 本地提取**再经自研 MarkdownParser 转题库包，附 QC 报告脚本（Markdown/CSV/JSON）；「答案候选」与题目分离存储，缺失答案首次作答时由 DeepSeek 生成；扫描 PDF 识别走 Qwen-VL 视觉降级（[README](https://github.com/6wa1t/408-ai-tutor)）。
- 差距：题库垂直 408 考研；视觉识别依赖云端 API，非纯离线。

### 2.6 其他小规模项目（仅列线索）

| 项目 | 地址 | star | 要点 |
|---|---|---|---|
| Competitive-exam-pipeline | github.com/namandhakad712/Competitive-exam-pipeline | 小规模 | Mistral OCR + MinerU 双通道，3 个 AI 供应商并行抽取按字段多数投票 + 32 项自动校验 + 自动修复；「多模型共识」思路可借鉴但成本高 |
| prepzy-pyq | github.com/SQADIRKVM/prepzy-pyq | 2 | Tesseract.js + pdfjs 前端解析，Gemini/DeepSeek 做题目分类与考点分析；纯前端 OCR 质量有限 |
| Gradence-QP-Analyzer | github.com/devika-nair-s/Gradence-QP-Analyzer-Tool | 小规模 | 扫描卷直接交给 Gemini 视觉模型读，跳过 OCR——「VLM 直读试卷」低成本路线的代表 |
| ocr-qa-segmentation | github.com/Abhigyan-Shekhar/ocr-qa-segmentation | 小规模 | 手写试卷题目/答案切分用 CRF + TrOCR，不用 LLM；证明「题目区/答案区切分」也可以是小模型序列标注问题 |
| examino / ExamGenerator | github.com/chitniskedar/examino 等 | 0~5 | PDF → LLM 生成 MCQ 入题库，FastAPI + SQLite 本地优先；代表「AI 出题」而非「试卷还原」 |

## 3. 题库/考试系统类（无 PDF 入库能力，作边界参照）

- **学之思 xzs**（github.com/mindskip/xzs，~4k★，Java+Vue）：国内最知名开源考试系统之一；题目支持文本/图片/表格/数学公式（kityformula），导入仅支持 Excel，**没有 PDF/图片入库**（[README](https://github.com/mindskip/xzs)，[官网功能表](https://www.mindskip.net/xzs.html)）。说明传统题库系统的「文件导入」止步于表格，扫描件入库是它们没做的部分。

## 4. 商业产品（简览）

- **菁优网**：APP「拍照组卷」可整页拍摄、自动分割多道试题并匹配题库出解析，宣称识别搜达率 97%；另有「AI 试卷擦除」（去手写笔迹还原空白卷）（[官方功能页](https://www.jyeoo.com/introduce/appintro/)，[拍照搜题教程](https://www.guofenmi.net/wz/434061.html)）。形态上是「识别 → 匹配自有题库」，不是「识别 → 进用户自己的题库」。
- **组卷网（学科网系）**：拍照组卷、AI 小博士答疑、知识点/章节/细目表选题，组卷-布置-批改-错题全流程；题目均带答案解析与知识点标签（[产品说明](https://zujuan.xkw.com/)，第三方整理见其新手教程）。同样依赖云端海量题库，教师自有试卷「入库再用」不是主线功能。

## 5. 对本项目的启示

**市面上已成熟、可直接借鉴的：**

1. **扫描件 → 带公式文本**：MinerU（已部署其文字模型，可补齐版面/公式模型）、PaddleOCR-VL、Marker 的「文字层优先、坏了才 OCR」策略——与本项目 `document_pipeline` 现有的 `_embedded_blocks` → `_ocr_blocks` 降级结构（`question_bank/document_pipeline/pipeline.py`）思路一致，只需把识别深度从「文字行」升到「公式 LaTeX」。
2. **文本 → 题目结构**：没有专用模型，通行做法是「规则解析（题号/题型区标题/答案区约定）→ 逐题预览 → 人工确认入库」，shiroha-quiz 的答案文件分离匹配、分区题型继承、异常标记筛选器都是成熟交互；条件够时可用 LLM 抽取 + 校验重试（DocumentLynx 模式）。
3. **AI 补答案/解析**：多个项目共识是「AI 结果仅作候选，写入前必须经教师确认」（shiroha-quiz 明示；408-ai-tutor 把答案存为 answer_candidates 独立表）——与本项目教师复核定位一致。
4. **审核流**：gygy-open 的「草稿 → 待审 → 发布 → 归档」+ 审核日志是题库题目生命周期的成熟模板。

**市场上的空白（本项目潜在差异点）：**

1. **全链路**：「扫描试卷 → 题目结构化 → 本地题库 → 组卷 → AI 补答案 + 教师复核」没有成熟开源实现；最接近的 MathBank 是单人小项目且只做数学。本项目若打通即构成完整差异化。
2. **本地离线**：上述链路项目几乎全部依赖云端 LLM/VLM 或云存储；纯本地（ONNX 文字 OCR 已有，公式与切题待补）+ 可选云端 AI 的双层设计在开源圈稀缺。
3. **题目区/答案区切分**：连成熟引擎都不做；要么规则 + 人工（主流），要么小模型序列标注（ocr-qa-segmentation），要么 LLM/VLM 直读（Gradence、Competitive-exam-pipeline 的多模型投票）。对本项目务实的路径是：扫描页先按现有 MinerU OCR 出文字行 → 沿用文字层 PDF 已有的题号/题型规则切分 → 公式区域用 VLM 或公式模型补识别 → 一律进「待教师复核」状态。

**风险提示**：MinerU 与 MathBank 的 license 分别为自定义协议与 AGPL-3.0，借鉴时只取思路与自研代码，打包分发前复核条款。
