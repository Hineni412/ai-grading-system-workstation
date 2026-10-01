# 本机存储与维护策略

本文件说明当前数据放在哪里、默认保留什么，以及执行审计、归档或去重前必须遵守的授权流程。路径描述来自代码约定；阅读本文件不需要也不得打开真实 `user_data/`。

## 数据类别

| 类别 | 默认位置 | 当前用途 | 默认处理 |
|---|---|---|---|
| 核心数据库 | `user_data/databases/` | 考试、批改、题库等正式状态 | 始终保留，不参与自动归档或去重 |
| 原始业务文件 | `user_data/exams/`、`user_data/templates/`、`user_data/question_bank/` | 试卷、样卷、模板、题库原件及衍生素材 | 原件默认保留；只有明确可再生成的副本才进入维护候选 |
| 批注与导出 | `user_data/annotated/`、`user_data/reports/` | 批注图片、成绩表、报告和报告页面 | 保留门槛见下；可再生成的页面目录可进入归档候选 |
| 本地学情快照 | `user_data/reports/.training_diagnosis/profiles.cache` | 已有考试与训练记录计算出的逐生诊断及群体汇总，供重启后首次读取 | 应用后台保存，单文件最多 12 个当前来源版本的范围；版本不符或文件不可用时重算，不删除原始记录 |
| 临时与工具输出 | `user_data/temp/`、`user_data/outputs/` | 临时文件、基准和比较结果 | 达到保留门槛后只列为候选，不自动删除 |
| 备份与归档 | `user_data/backups/`、`user_data/archives/` | 数据库快照、完整备份和维护工具生成的压缩包 | 按下述数量和时间门槛保留 |
| 配置与密钥 | `user_data/config/`、`%LOCALAPPDATA%\AIGradingSystem\config\` | 上传配置、本机设置和模型 profile；`运行.bat` 默认把 API profile 放在前一个目录 | 视为敏感配置，不进入项目仓库或业务导出 |
| 敏感模型诊断 | `logs/llm_diagnostics.jsonl*` | 本机排错所需的请求与响应正文 | 受控查看、限量轮转，不进入 Git |

## 默认保留规则

保留门槛只用于生成“待确认候选”，不会授予工具自动删除权限：

- 核心数据库、样卷/模板/题库原件默认一直保留。
- 答卷扫描增强成功后只保留工作图，不再另存未处理原图。
- 成绩表、批注原卷 PDF、教师额外导出的训练 zip/docx/md、讲义和错题本 Word/ZIP 下载成功后删除本机副本，需要时再生成；讲义临时文件位于报告目录的 `training_handouts/`，错题本位于 `wrong_question_books/`，批注图片仍按现有目录保留。
- 个性化训练每人保留一份冻结 PDF，供打印和回收扫描，不因下载删除。
- 报告中的 `*_批注原卷页面_*` 目录超过 7 天后可列为归档候选。
- `user_data/outputs/` 中 `benchmark_*` 目录超过 7 天、名称含 `comparison` 的目录超过 14 天后可列为归档候选。
- `user_data/temp/` 中超过 1 天的项目可列为归档候选。
- 完整压缩备份至少保留最新 3 份；只有排在其后且超过 30 天的备份才列为候选。
- `grading_before_*.db` 快照至少保留最新 20 份；只有排在其后且超过 14 天的快照才列为候选。
- 模型诊断日志单文件约 32 MiB 时轮转，保留当前文件和最近 3 个轮转文件。

当前归档操作只在 `user_data/archives/` 创建 ZIP 副本，不删除原文件。是否随后删除原件必须作为另一项精确操作单独设计并另行授权。

## 默认去重规则

去重只使用硬链接：两个路径仍然存在，但在同一磁盘上共用一份相同内容。当前工具只为同时满足以下条件的文件生成候选：

- 位于 `annotated`、`templates`、`reports` 或 `exams` 顶层目录；
- 扩展名为 `.jpg`、`.jpeg`、`.png` 或 `.pdf`；
- 文件大小至少 128 KiB，且最近 24 小时未修改；
- 位于同一磁盘，内容指纹完全一致；
- 不在 `databases` 或 `config` 目录中。

数据库、JSON、YAML、配置文件、最近修改的文件和跨磁盘文件不参与去重。硬链接会改变重复文件的底层存储关系，因此即使内容不变，也属于真实数据写入操作。

## 备份与维护入口

- 普通备份由 `update_tools/backup_core.py` 实现，范围为 grading（阅卷及题库）；排除模型密钥、诊断日志及 SQLite 临时文件；完整安装包包含数据与密钥，不能当作普通备份分发，见 `docs/maintenance/packaging.md`；单条数据删除不会追溯删除已有备份副本。
- 词表状态虽可位于账户配置目录，普通备份仍明确包含状态主文件、其 `.bak`、审核回执及建议记录，归档成员固定为 `config/taxonomy-governance/` 下的四个文件；离线恢复与恢复前安全备份均把这些成员映射到本机 `taxonomy_state_path` 及配套文件，不复制相邻的模型密钥；兼容输入：旧备份没有这些成员时不改变现有词表状态。
- `tools/maintain_question_bank.py` 统一题库定期维护：`errors --sessions 3 4 5` 在副本上预演既有考试错因回挂，只接纳与当前证据匹配的成果，重复执行不重复记录；`standard --release <发布文件>` 预演标准变化和受影响题目。
- 标准修订的 `--links <关联文件>` 是数组，每项含 `question_id`、`evidence_version_id` 和完整 `points`；各点含 `part_id`、`evidence_point_id`、`links`，每个链接含 `term_id`、`stable_key`、`role` 和可选 `weight`；必须覆盖全部受影响题及其当前判定点，未变化题不能夹带进来；生成或人工整理这些关联仍沿用既有版本工具。
- 上述工具默认只读取真实数据库，在临时副本上预演，不调用模型；正式执行需先关闭应用并取得本次批量操作授权，添加 `--apply` 后先在 `user_data/backups/question_maintenance_<时间>/question_bank_before.db` 创建题库快照，再执行、检查数据库完整性和引用；考试库、原答卷、分数及场次分析文件不改写。
- 标准先准备候选关联再启用；执行中断时保留快照及已完成记录，错因回挂可幂等续跑，标准失败需先核对活动版本和候选记录再重试；恢复覆盖快照按 `AGENTS.md` 另行授权；既有一次性脚本不自动归档或删除。

以下工具会读取真实数据：只读审计和预览自动允许；向 `user_data` 写入结果、应用硬链接或归档等改变真实数据状态的操作仍按 `AGENTS.md` 授权；预览不等于批准应用。

| 工具与参数 | 作用 |
|---|---|
| `tools/storage_audit.py`，--root . | 审计并将报告写入 user_data/reports/storage_audit/ |
| `tools/storage_maintenance.py`，--root . | 仅打印维护计划 |
| 同上，加 --apply-hardlinks | 按候选执行硬链接去重 |
| 同上，加 --apply-archives | 创建归档 ZIP，不删除原件 |

使用项目便携 Python 执行。应用前核对精确候选；删除原件属于另一个操作，不包含在归档内。
