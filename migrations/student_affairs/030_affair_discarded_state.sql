-- migration-policy: rebuild-tables affairs
-- SOP 事务支持弃用：state 增加 'discarded'（终态，不可重开）。

CREATE TABLE affairs_rebuilt (
    affair_id TEXT PRIMARY KEY,
    template_version_id TEXT NOT NULL,
    payload_object_id TEXT NOT NULL UNIQUE,
    plan_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN ('active', 'closed', 'discarded')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT,
    FOREIGN KEY(template_version_id)
        REFERENCES sop_template_versions(template_version_id),
    FOREIGN KEY(payload_object_id) REFERENCES encrypted_objects(object_id),
    FOREIGN KEY(plan_id) REFERENCES work_plans(plan_id)
);

INSERT INTO affairs_rebuilt (
    affair_id, template_version_id, payload_object_id, plan_id,
    state, created_at, updated_at, closed_at
)
SELECT affair_id, template_version_id, payload_object_id, plan_id,
       state, created_at, updated_at, closed_at
FROM affairs;

DROP TABLE affairs;

ALTER TABLE affairs_rebuilt RENAME TO affairs;
