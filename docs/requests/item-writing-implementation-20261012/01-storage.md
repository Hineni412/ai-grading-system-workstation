# 01 存储：迁移 049 与当前结构定义

## 1. 授权范围

用户于 2026-10-12 授权一次题库迁移，内容只限以下四项（第 4 项是写文档时发现、用户随后确认的）：

1. `authoring_works.kind` 增加 `material`。
2. `authoring_assets.role` 增加 `document`。
3. 新增 `authoring_materials` 表。
4. 全部 authoring 表的数据版本触发器改为只记 `training_ops` 域：删除现有 `materials`、`materials_global` 与 `question_versions` 触发器。

任何其他列、索引或表的改动都不在授权内，需要先停下问用户。

## 2. 现状（2026-10-12 只读核对）

- 真实库 `user_data/databases/question_bank.db`：`authoring_works` 1 行（10 月 3 日的 decompose 作品，`is_deleted=1`，无版本）；`authoring_work_versions`、`authoring_reviews`、`authoring_assets` 均为 0 行。迁移后这 1 行必须原样保留。
- 现有触发器（`backend/current_schema/question_bank.json` 中可见）：
  - `dv_authoring_{works,work_versions,reviews,assets}_{ins,upd,del}`：12 个，记 `materials`（迁移 047）。
  - `dvg_authoring_{work_versions,reviews,assets}_{ins,upd,del}`：9 个，记 `materials_global`（迁移 048）。
  - `qv_authoring_works_{ins,upd,del}`：3 个，按 `source_question_id` 记 `question_versions`（迁移 048）。
- 影响：每保存一次作品，题库技能投影、章节考情等按 materials 版本复用的缓存全部失效。改为 `training_ops` 后，题库缓存不再受作品保存影响。

## 3. 迁移文件 `migrations/question_bank/049_authoring_material_practice.sql`

写法照 036、039 等已有重建迁移：

- 第一行：`-- migration-policy: rebuild-tables authoring_works, authoring_assets`。
- 在 `update_tools/migrate_db.py` 的 `_APPROVED_TABLE_REBUILDS` 登记 `"049_authoring_material_practice": frozenset({"authoring_works", "authoring_assets"})`。
- 不写 `DELETE FROM`、`DROP INDEX`；`DROP TABLE` 只针对这两张被重建的表；`DROP TRIGGER` 可以使用。

步骤（顺序不能变）：

1. 新建 `authoring_works_v049`，列定义与当前完全相同，只把检查约束改为 `kind IN ('decompose', 'adapt', 'material')`。`INSERT INTO … SELECT` 搬回全部列。`DROP TABLE authoring_works`，再 `ALTER TABLE authoring_works_v049 RENAME TO authoring_works`。重建索引 `idx_authoring_works_kind_updated ON authoring_works(kind, is_deleted, updated_at DESC)`。
2. 用同样方式重建 `authoring_assets`，检查约束改为 `role IN ('photo', 'figure', 'document')`；重建 `idx_authoring_assets_work ON authoring_assets(work_id)`。
3. `DROP TRIGGER IF EXISTS` 删掉 `authoring_work_versions`、`authoring_reviews` 上的 `dv_…`、`dvg_…` 共 12 个触发器。两张被重建表上的触发器已随 `DROP TABLE` 消失，同样写 `DROP TRIGGER IF EXISTS` 以防万一。
4. 新建表：

```sql
CREATE TABLE authoring_materials (
    material_id              TEXT PRIMARY KEY,
    kind                     TEXT NOT NULL
        CHECK (kind IN ('situation', 'source_question', 'topic')),
    title                    TEXT NOT NULL CHECK (TRIM(title) <> ''),
    body_json                TEXT NOT NULL CHECK (json_valid(body_json)),
    context_kind             TEXT,
    section_id               TEXT,
    source_question_ids_json TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(source_question_ids_json)),
    needs_figure             INTEGER NOT NULL DEFAULT 0 CHECK (needs_figure IN (0, 1)),
    status                   TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'usable', 'disabled')),
    origin                   TEXT NOT NULL CHECK (origin IN ('ai_extract', 'teacher')),
    extraction_json          TEXT
        CHECK (extraction_json IS NULL OR json_valid(extraction_json)),
    revision                 INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at               TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at               TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX idx_authoring_materials_status ON authoring_materials(status, kind, updated_at DESC);
```

5. 为 `authoring_works`、`authoring_work_versions`、`authoring_reviews`、`authoring_assets`、`authoring_materials` 各建 `dv_<表>_ins`、`dv_<表>_upd`、`dv_<表>_del` 三个触发器，内容只有 `UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';`，写法照 047 第 418 行附近的 training_ops 触发器。不建 `dvg_`、`qv_` 触发器。

字段含义：
- `kind`：situation＝情境素材，source_question＝母题，topic＝主题限定。
- `body_json`：situation 为 `{"situation": 情境描述}`；source_question 为母题冻结快照 `{"question_text", "question_type", "section_label"}`；topic 为 `{"scope", "constraints"}`。
- `context_kind`：无情境、生活情境、科学跨学科、数学文化、新定义五类之一，或 NULL。
- `section_id`：来源小节的 knowledge_id。
- `source_question_ids_json`：来源题号列表；只作记录，不建外键，题目删除后材料保留。
- `extraction_json`：AI 抽取记录 `{"model", "operation_id", "job_id", "raw": 模型对该题的原始条目, "local_issues": [...]}`；教师录入的材料为 NULL。
- `revision`：教师编辑材料时的冲突判断，保存时 `WHERE revision = 期望值`。

作品与材料的关联不新增列：`authoring_works.task_card_json` 保存 `{"material_id", "material_revision", "material_snapshot", "requirement"}`。材料之后被编辑或停用，已有作品仍按快照显示。

## 4. 同步修改的代码

- `integration/data_generation.py`：
  - 五张 authoring 表加入 `QUESTION_BANK_VERSION_DOMAIN_TABLES["training_ops"]`。
  - 从 `QUESTION_BANK_VERSION_QUESTION_TABLES` 删除 `authoring_works`，从 `QUESTION_BANK_VERSION_GLOBAL_TABLES` 删除 `authoring_assets`、`authoring_reviews`、`authoring_work_versions`，并删掉第 119 行那句只针对 authoring_works 的说明。
- `backend/current_schema/question_bank.json`：用 `tools/generate_schema_baseline.py` 从合成库重建，再用 `--check` 核对。不要手改 JSON。
- `docs/maintenance/packaging.md` 第 32 行改写：四张表已恢复使用，并新增材料表；删除“已无功能使用”的说法。

## 5. 上传文件位置

- 上传原件和配图保存在 `user_data/question_bank/authoring_assets/<work_id>/<asset_id>.<扩展名>`；`authoring_assets.relative_path` 存相对 `user_data/question_bank/` 的路径，`sha256` 存文件内容哈希，用于同一作品内去重。
- 读取经题库模块受控根目录解析，拒绝 `..`、绝对路径与符号链接。
- 删除作品（软删除）不删文件；永久清理不在本次范围内。
- `docs/maintenance/storage-policy.md` 增加一条：位置、内容、何时产生、不会自动清理。

## 6. 验证与执行

合成库上：
1. 用当前结构建空库，插入 1 行 decompose 作品（`is_deleted=1`）和 1 行 photo 资产，执行迁移。核对两行逐列不变，`PRAGMA foreign_key_check` 为空，`PRAGMA integrity_check` 为 ok。
2. 插入 `kind='material'` 作品与 `role='document'` 资产成功；`kind='other'` 失败。
3. 写入任一 authoring 表：`training_ops` 版本加 1，`materials`、`materials_global` 与 `question_versions` 不变。
4. `tools/generate_schema_baseline.py --check` 通过；`integration/data_generation.py` 的覆盖检查在新库上返回完整覆盖。

真实库（已授权，按受保护流程）：
1. 先确认 8035 上没有进行中的批改、模型任务或其他共用服务的任务。
2. 走现有维护流程：预演、安全备份，下次启动由 `backend.ops.offline --apply-pending` 执行（入口与行为见 `docs/maintenance/packaging.md`）。不得用脚本直接改真实库。
3. 执行后只读核对：原有 1 行仍在；新表存在；`schema_migrations` 有 049 成功记录；启动后题库页面正常。
4. 失败时由维护流程恢复备份；在回复中写明备份文件名与失败原因。
