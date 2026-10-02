# AI 阅卷系统

面向教师 Windows 工作机的本地应用，主要流程是 AI 阅卷、题库、组卷和个性化训练。班主任工作台已移出为独立应用（`D:\班主任工作台`，使用说明见其 README），本应用不提供其接口，学生名单、成绩和题库也不与其自动同步。

## 启动与停止

双击 `运行.bat`，在浏览器打开 http://127.0.0.1:8035 。停止服务使用 `关闭系统.bat`。

- 完整便携包自带 Python 与前端成品，目标电脑不需要另装 Python、Node.js。
- 源码目录包含 `frontend/package.json` 时，每次启动会先构建前端，需要兼容的 Node.js、npm 和已安装依赖；构建失败不会启动后端。
- 业务数据保存在本目录的 `user_data/`。更新、备份和打包的处理方式不同，操作前阅读下方对应说明。
- 模型配置在设置页的“AI 服务”中维护；调用费用取决于所选服务。人工批改不需要模型。

## 功能入口

| 要完成的事 | 操作流程与规则 |
|---|---|
| 阅卷 | 配置考试 → 核对题目和评分依据 → 确认样卷题框 → 扫描归卷 → 批改与复核 → 成绩和报告；见 [考试阅卷](docs/product/GRADING.md) |
| 题库、组卷与训练 | 导入与标注题目、手动选题与班级组卷、按掌握情况推荐、出卷、回收判定；见 [题库与训练](docs/product/KNOWLEDGE_AND_TRAINING.md) |

## 开发与维护文档

只按当前任务查阅，不要求每次通读。

| 文档 | 何时阅读 |
|---|---|
| [AGENTS.md](AGENTS.md) | 代理开始开发：范围、协作、授权、验证规则 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 查运行方式、模块连接、数据归属、模型通道与代码入口 |
| [CONTEXT.md](CONTEXT.md) | 评分、判定点、掌握度等业务术语不明确时 |
| [安全与诊断](docs/security/SECURITY.md) | 涉及模型发送、本机数据、日志或部署边界时 |
| [存储与备份](docs/maintenance/storage-policy.md) | 查文件保留、备份范围、归档与去重时 |
| [打包与更新](docs/maintenance/packaging.md) | 制作便携包、升级、回退或数据库迁移时 |
| [测试](docs/testing/README.md) | 选择针对性测试或合成环境验收入口时 |
| [样式](docs/ui/STYLE.md) | 修改页面视觉与通用交互时 |
| [Word 与 LaTeX 排版调查](docs/requests/question-bank-latex-layout-evaluation-20261002.md)、[一手项目来源](docs/requests/latex-question-bank-primary-sources-20261002.md) | 查 PDF 导出方向、题型实排证据、外部参考与尚未验证的范围 |

产品文档记录业务规则，架构记录连接方式，代码与配置提供具体实现。发现不一致时核对实现和用户需求，修正文档；不能为迁就过期文字修改正常功能。文档检查入口为 `tools/check_documentation.py`。

## 独立维护工具

以下工具由维护人员按需运行，不由日常页面自动调用。没有代码引用不代表可以删除；运行前按工具参数和项目授权规则区分只读检查、生成文件与正式数据写入。

| 用途 | 保留入口 | 使用边界 |
|---|---|---|
| 题库维护与标准修订预演 | `tools/maintain_question_bank.py` | 参数与正式执行条件见存储与备份文档 |
| 知识标准初始发布文件重建 | `tools/build_knowledge_graph_release.py` | `--check` 只核对现有文件；不带参数会重写初始发布文件 |
| 教学技能标准发布文件重建 | `tools/build_release_v5.py` | 必须提供 `--teaching-standard` JSON；默认预演，`--write` 生成发布文件；数据库应用是另行授权的操作 |
| 词表修订发布文件重建 | `tools/build_release_v7.py` | 默认读取已有输入并验证，`--write` 生成对应发布包和词表；不会自动切换数据库中的活动标准 |
| 难度校准与推荐有效性回看 | `tools/difficulty_calibration_report.py`、`tools/recommendation_validity_report.py` | 按显式数据库路径读取统计结果，不改写评分、难度或推荐规则 |
| 掌握度前向检验与参数选择 | `tools/mastery_validation.py` | `--volume` 指定教学学期；`--initial` 复现原型参数，`--grid` 选择参数；只读数据库，只向终端输出汇总数字，不调用模型、不落盘学生结果 |
| 相似题向量检索实验 | `tools/experiment_vector_similarity.py` | `--baseline-only` 只读核对现有排序；本地真实模型实验须单独授权，方案与限制见 [实验说明](docs/requests/vector-similarity-experiment-20261001.md) |
| 历史数据导出 | `tools/export_legacy_cli_data.py`、`tools/export_legacy_skill_data.py` | 保留退役数据的读取与导出能力，源数据库不改写 |
| 本机空间盘点、维护预览 | `tools/storage_audit.py`、`tools/storage_maintenance.py` | 产物位置、保留范围及执行授权见存储与备份文档 |
| 代理后台启动 | `tools/start_service.py` | 按项目运行约束使用；教师日常入口仍为 `运行.bat` |

知识标准构建脚本依赖的辅助脚本也应保留；生成候选发布文件与启用数据库中的活动标准是两个操作。前端专项浏览器测试的业务命令与构建要求见测试文档。
