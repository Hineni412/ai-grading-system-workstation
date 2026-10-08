# 打包与版本更新

本文件说明当前桌面交付的打包方式、增量更新机制和数据库迁移规则,以及两者当前未覆盖的范围。事实来自打包脚本、更新工具和启动代码。

## 交付形态

- 桌面交付是一个便携文件夹:`运行.bat` 启动、`关闭系统.bat` 停止,服务只监听 `127.0.0.1`;目标机器不需要安装 Python 和 Node。
- 便携 Python 位于 `runtime/python`;前端以 `frontend/dist` 成品随包分发,包内不含前端工程文件,启动时不触发前端构建。
- 业务数据始终位于 `user_data/`;应用代码与用户数据分离是更新安全的前提。

## 完整安装包

- `package_v1.5.0.py` 生成完整安装包:复制代码、文档、`frontend/dist`、启动器、便携 Python 运行时,以及 `runtime/tectonic`(训练卷 PDF 排版引擎)。产物只有 `dist/` 下的文件夹和 `RELEASE_MANIFEST_<版本>.json`,不生成 zip 压缩包。版本号取自 `VERSION`。
- 包内 `user_data` 是打包时刻的全量快照,包含工作区业务数据和 `user_data/config/api_profiles.json` 中的模型密钥。因此包等同于教师本机的整机数据快照:只允许本机私有保存,不得进入 Git、同步盘或对外分发;需要干净分发时,必须从不含真实数据的隔离骨架目录打包。
- 复制 `user_data` 前必须先关闭系统(`关闭系统.bat`);服务运行中复制 SQLite 数据库可能得到不一致的快照。
- 便携运行时首次构建需要联网下载 Python 3.12 嵌入包并安装 `requirements.txt`(配合 `constraints.txt` 锁定版本);之后使用 `.portable_runtime_cache` 缓存,重复打包不再重新下载。`frontend/dist` 不完整时打包直接失败,不产出半成品。
- 同版本重复执行会先删除同名包目录再重建。空白数据库在首次启动时按当前 schema 自动初始化,包不依赖携带预置数据库。

## 增量更新

- 已部署机器的版本升级使用增量更新包,不用新完整包覆盖安装目录。
- 开发机上用 `update_tools/make_update.py` 生成更新包:内容为 `update_manifest.json`、`app/`(代码、`frontend/dist`、`VERSION`、启动器与 `runtime/tectonic`)、全量 `migrations/` 与全量 `update_tools/`。生成规则与完整安装包同源,只产出文件夹。更新包绝不包含 `user_data`。
- 目标机上用 `update_tools/apply_update.py <更新包目录>` 应用更新：先备份 `user_data`（zip，不含 API 密钥）与将被覆盖的代码（`app_backup_v<旧版本>/`，包含 `migrations/` 和 `update_tools/`），再覆盖代码。随后调用目标版本的 `backend.ops.offline`，复用应用维护的预演、迁移前备份、操作日志、锁和待执行操作；没有待迁移时不写业务库。支持 `--dry-run` 与 `--rollback`，更新摘要写入 `logs/backup.log`。
- 代码覆盖阶段不携带或覆盖 user_data，包括本机自动生成的知识标准、题型词表和整理回执；这些资料随普通业务备份恢复。随后确有核心库迁移时才按原受保护流程写数据库；自动题型使用已有表，不新增结构迁移。
- `update_tools/backup_data.py`、`update_tools/backup_core.py`、`update_tools/list_backups.py` 提供独立的数据备份与查询。

## 数据库 schema 迁移

- 当前定义在 `backend/current_schema/{grading,question_bank}.json`，包括 DDL、稳定初始记录、结构签名和固定迁移身份。`tools/generate_schema_baseline.py` 从合成库重建定义，`--check` 核对定义与历史迁移结果一致；发布前必须一起携带定义文件。
- 空库在事务内直接建立当前结构，确认仍为空后才写入；实际签名核对成功后登记迁移记录。完整当前库核对当前定义、实际结构与记录，不读取历史 SQL。旧库前缀、未知或失败记录仍走严格历史清单，不重新盖成功标记。
- 历史升级清单在 `migrations/<目标>/`，覆盖 grading、question_bank 两个库。已应用记录在各库的 `schema_migrations` 表，包含校验和与成功标记；校验通过的迁移不重放。旧 SQL 不能因当前库已采用基线而删除，支持的历史起点仍需逐一验证。
- 题库迁移 `046_add_authoring_practice.sql` 建立的 authoring_works、authoring_work_versions、authoring_reviews、authoring_assets 四张表已无功能使用。当前定义和历史清单均保留这些表；物理删表需单独授权和迁移，不随普通读取发生。
- 核心库(grading、question_bank)启动时执行 schema 闸门:已有库存在待迁移时,普通启动拒绝修改数据并停止,要求通过受保护维护入口确认迁移;受保护操作经操作日志与锁,在下次启动由 `backend.ops.offline --apply-pending` 应用。空白库按当前 schema 直接初始化。
- 迁移失败时由既有维护流程恢复两份核心库并记录失败，不能只把成功的一个库留在新版本。手工更新回退同时恢复配对的数据与代码备份；代码备份也恢复历史迁移和更新工具，避免代码与结构不一致。

## 当前未覆盖范围

- 增量更新不携带便携 Python 运行时(`runtime/python`):依赖清单变化的新版本需要重新完整打包并重装,不能用更新包升级。
- 完整安装包内的 `user_data` 是打包那一刻的快照;已部署机器上的真实数据始终以目标机为准,由增量更新流程保留。
