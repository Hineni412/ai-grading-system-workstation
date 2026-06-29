# AI 阅卷系统 — 工作机使用说明 v1.5.0

## 快速开始

### 首次使用

1. 将本文件夹整体拷贝到工作机任意位置
2. 确保工作机已安装 Python 3.10 或更高版本
3. 双击 `run.bat` 启动系统
4. 首次启动会自动创建虚拟环境并安装依赖（需要网络）
5. 浏览器自动打开 http://localhost:8501

### 日常使用

- 双击 `运行.bat` 即可启动
- 按 Ctrl+C 停止服务

### 原始试卷入库与知识图谱（可选）

1. 上传 DOCX/PDF 并生成评分依据后，点击“确认保存评分依据”。系统会同时把原始试卷按 SHA-256 归档到本机，但此时**不会**导入题库，也不会调用 AI 打标签。
2. 创建考试批改后，可在“考试工作台”“批改进度”或“全局资料 → 跨考试知识图谱”看到“入库并打标签”按钮。这个操作可以在正式批改前、批改进行中或批改完成后执行。
3. 点击按钮后，系统复用题库导入和 AI 打标签流程，并按显式对应或唯一题号把评分来源题关联到题库题。知识图谱和训练推荐直接读取这些题目的当前 `knowledge_point` 标签，不会再次提交 AI 做知识点匹配。
4. 这一步不是强制前置条件。选择稍后处理时，批改、人工复核和报告导出仍可继续；全局知识图谱会分别显示“题库流程完成度”和“知识图谱完整度”，未完成时明确列出缺失题目，只展示已确认关联且带知识点标签的证据。
5. 如果网络或个别题目处理失败，已经成功入库和打标签的题目会保留，状态显示“部分完成”。点击“继续处理缺失项”只补齐缺失步骤；已有完整标签的题不会重新调用 AI。
6. 历史考试如果没有保存原始试卷，可在同一提示卡上传 DOCX/PDF 并点击“保存原始试卷”，再执行“入库并打标签”；不会影响已有批改结果。

## 数据保存位置

所有考试数据和配置保存在 `user_data/` 目录下：

| 目录 | 内容 |
|------|------|
| `user_data/databases/` | 阅卷数据库、题库数据库 |
| `user_data/exams/` | 上传的考试答卷图片 |
| `user_data/config/` | API 配置 |
| `user_data/templates/` | 阅卷模板 |
| `user_data/annotated/` | 批注结果图片 |
| `user_data/reports/` | 导出的报表 |
| `user_data/question_bank/` | 题库附属数据 |
| `user_data/outputs/` | 组卷输出 |
| `user_data/backups/` | 数据库备份 |

> **重要**: 绝不要删除 `user_data/` 目录！所有考试数据都在这里。

## 阅卷前如何备份

```bash
# 手动备份（推荐阅卷前执行）
python update_tools/backup_data.py --reason before_exam

# 查看已有备份
python update_tools/list_backups.py
```

也可以在"系统自检"页面点击"立即备份"按钮。

## 如何恢复

```bash
# 查看备份列表
python update_tools/list_backups.py

# 恢复指定备份（需输入 YES 确认）
python update_tools/restore_backup.py <备份文件名>
```

## 如何更新

1. 从开发机获取更新包（`AI阅卷系统_update_vX.Y.Z/` 文件夹）
2. 将更新包拷贝到工作机
3. 双击更新包中的 `update.bat`
4. 按照提示操作

更新过程会自动：
- 备份当前数据
- 备份当前代码
- 替换代码文件
- 执行数据库迁移
- 显示更新结果

## 注意事项

1. **不要删除 `user_data/`** — 所有考试数据都在这里
2. **不要手动修改 `user_data/databases/` 中的 .db 文件**
3. 更新代码时只替换 .py 文件和 pages/ 等代码目录，不要覆盖 user_data/
4. 如需修改 API Key，在"系统自检"页面或 `user_data/config/api_profiles.json` 中配置
5. 定期备份，尤其在重要考试前

## 系统自检

启动系统后，在左侧导航栏点击"系统自检"，可以查看：
- 系统环境状态
- 数据库状态与 Schema 版本
- API 配置状态
- 备份状态
- 一键备份功能

## 知识点标签与训练推荐

训练推荐直接显示“薄弱知识点、掌握率、证据题数、题库同标签题数”。知识点来自已确认来源题所对应题库题的当前 `question_tags`，普通老师无需逐条做知识点编号映射或 AI 消歧。

- **候选资格**：薄弱证据和候选题必须至少共享一个完全相同的 `knowledge_point` 标签值。
- **排序加分**：`sub_skill`、`method`、`model`、`prerequisite` 标签重合，以及难度、频次和来源多样性只影响排序，不改变候选资格。
- **缺题处理**：精确标签题不足时直接显示缺题；不会用近义词、相邻技能或 AI 语义匹配凑数。
- **跟随修改**：教师修改题库标签后，下一次图谱查询或重新分析推荐会读取新标签，无需重新批改试卷。
- **任务快照**：已经保存的训练任务和导出仍保持当时的题目快照，不随之后的标签修改而改写。

旧技能目录、概念映射和技能链接表只用于一个版本的只读回退，请勿手工删除或直接修改；活动知识图谱和推荐不会读取它们。

### 旧技能目录回退命令（仅维护）

先执行 dry-run；报告未通过覆盖率 95%、100 条金标及 98% 精度门槛时，不得 apply 或切换：

```powershell
runtime\python\python.exe -m update_tools.migrate_skill_catalog dry-run --grading-db user_data/databases/grading_system.db --question-bank-db user_data/databases/question_bank.db --report-dir user_data/reports/skill_migration --gold-file tests/fixtures/skill_migration_gold.json
runtime\python\python.exe -m update_tools.migrate_skill_catalog apply --batch-id skill-v1-20260622 --grading-db user_data/databases/grading_system.db --question-bank-db user_data/databases/question_bank.db --report-dir user_data/reports/skill_migration --gold-file tests/fixtures/skill_migration_gold.json
runtime\python\python.exe -m update_tools.migrate_skill_catalog set-mode --mode shadow --question-bank-db user_data/databases/question_bank.db --reason "compare unified skill recommendations"
runtime\python\python.exe -m update_tools.migrate_skill_catalog set-mode --mode skill --question-bank-db user_data/databases/question_bank.db --batch-id skill-v1-20260622 --reason "acceptance gates passed"
runtime\python\python.exe -m update_tools.migrate_skill_catalog rollback --batch-id skill-v1-20260622 --question-bank-db user_data/databases/question_bank.db
runtime\python\python.exe -m update_tools.migrate_skill_catalog set-mode --mode legacy --question-bank-db user_data/databases/question_bank.db --reason "emergency rollback"
```

`shadow → skill` 还要求系统已保存代表性学生的影子推荐对比记录；如果没有记录或出现精确题差异，`set-mode --mode skill` 会直接拒绝。

迁移备份位于 `user_data/databases/skill_migration_backups/<批次号>/`。回滚前不要移动该目录。

## 技术支持

如遇问题，请提供以下信息：
- `logs/` 目录下的日志文件
- "系统自检"页面截图
- VERSION 文件中的版本号

---
版本: 1.5.0
更新时间: 2026-06-28
