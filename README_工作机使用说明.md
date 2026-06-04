# AI 阅卷系统 — 工作机使用说明 v1.3.0

## 快速开始

### 首次使用

1. 将本文件夹整体拷贝到工作机任意位置
2. 确保工作机已安装 Python 3.10 或更高版本
3. 双击 `run.bat` 启动系统
4. 首次启动会自动创建虚拟环境并安装依赖（需要网络）
5. 浏览器自动打开 http://localhost:8501

### 日常使用

- 双击 `run.bat` 即可启动
- 按 Ctrl+C 停止服务

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

## 技术支持

如遇问题，请提供以下信息：
- `logs/` 目录下的日志文件
- "系统自检"页面截图
- VERSION 文件中的版本号

---
版本: 1.3.0
生成时间: 2026-05-27 18:34:04
