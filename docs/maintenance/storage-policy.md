# 本机存储与维护策略

本文件说明当前数据放在哪里、默认保留什么，以及执行审计、归档或去重前必须遵守的授权流程。路径描述来自代码约定；阅读本文件不需要也不得打开真实 `user_data/`。

## 数据类别

| 类别 | 默认位置 | 当前用途 | 默认处理 |
|---|---|---|---|
| 核心数据库 | `user_data/databases/` | 考试、批改、题库等正式状态 | 始终保留，不参与自动归档或去重 |
| 教师工作区 | `user_data/workspaces/` | 班主任等工作区数据库和附件 | 视为正式业务数据，默认保留 |
| 原始业务文件 | `user_data/exams/`、`user_data/templates/`、`user_data/question_bank/` | 试卷、样卷、模板、题库原件及衍生素材 | 答卷扫描增强后只保留工作图；样卷、模板和题库原件默认保留；只有明确可再生成的副本才进入维护候选 |
| 批注与导出 | `user_data/annotated/`、`user_data/reports/` | 批注图片、成绩表、报告和报告页面 | 成绩表和批注原卷 PDF 下载成功后删除，可重新生成；批注图片仍按现有目录保留；可再生成的页面目录可进入归档候选 |
| 临时与工具输出 | `user_data/temp/`、`user_data/outputs/` | 临时文件、基准和比较结果 | 达到保留门槛后只列为候选，不自动删除 |
| 备份与归档 | `user_data/backups/`、`user_data/archives/` | 数据库快照、完整备份和维护工具生成的压缩包 | 按下述数量和时间门槛保留 |
| 配置与密钥 | `user_data/config/`、`%LOCALAPPDATA%\AIGradingSystem\config\` | 上传配置、本机设置和模型 profile；`运行.bat` 默认把 API profile 放在前一个目录 | 视为敏感配置，不进入项目仓库或业务导出 |
| 敏感模型诊断 | `logs/llm_diagnostics.jsonl*` | 本机排错所需的请求与响应正文 | 受控查看、限量轮转，不进入 Git |

## 默认保留规则

保留门槛只用于生成“待确认候选”，不会授予工具自动删除权限：

- 核心数据库、工作区数据、样卷/模板/题库原件默认一直保留。
- 答卷扫描增强成功后只保留工作图，不再另存未处理原图。
- 成绩表、批注原卷 PDF 以及教师额外导出的训练 zip/docx/md 下载成功后删除本机副本，需要时再生成。
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

## 逐次授权流程

存储工具会检查或修改真实业务文件。每次运行都必须针对本次精确目标重新获得用户明确授权；查看授权、预览授权、归档授权和去重授权互不替代。

1. 先说明要检查的目录、输出报告位置、是否会写入文件以及失败影响。
2. 获得本次审计授权后，才能运行：

   ```powershell
   & .\runtime\python\python.exe tools\storage_audit.py --root .
   ```

   审计会读取真实数据并把报告写入 `user_data/reports/storage_audit/`，因此它不是“无需授权的纯读取”。

3. 与用户核对报告中的精确路径、原因、大小和时间门槛。
4. 获得本次维护预览授权后，才能运行：

   ```powershell
   & .\runtime\python\python.exe tools\storage_maintenance.py --root .
   ```

   不带应用参数时只打印计划，不执行硬链接或归档。

5. 用户明确选择具体操作并再次授权后，才分别运行其中一个命令：

   ```powershell
   & .\runtime\python\python.exe tools\storage_maintenance.py --root . --apply-hardlinks
   & .\runtime\python\python.exe tools\storage_maintenance.py --root . --apply-archives
   ```

6. 操作前确认精确目标仍存在、备份与回退方式可用；操作后只核对本次目标，不顺带处理其他候选。

任何一次授权都不得推断为允许迁移、覆盖、永久删除、恢复覆盖或处理下一批真实数据。
