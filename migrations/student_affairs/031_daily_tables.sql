CREATE TABLE IF NOT EXISTS daily_tables (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_table_columns (
    id TEXT PRIMARY KEY,
    table_id TEXT NOT NULL,
    name TEXT NOT NULL,
    col_type TEXT NOT NULL CHECK (col_type IN ('text', 'number', 'check', 'select', 'date')),
    options_json TEXT,
    position INTEGER NOT NULL CHECK (position >= 0),
    FOREIGN KEY(table_id) REFERENCES daily_tables(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_daily_table_columns_table
    ON daily_table_columns(table_id, position);

CREATE TABLE IF NOT EXISTS daily_table_rows (
    id TEXT PRIMARY KEY,
    table_id TEXT NOT NULL,
    student_ref TEXT NOT NULL,
    display_name TEXT NOT NULL,
    class_label TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    FOREIGN KEY(table_id) REFERENCES daily_tables(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_daily_table_rows_table
    ON daily_table_rows(table_id, position);

CREATE TABLE IF NOT EXISTS daily_table_cells (
    table_id TEXT NOT NULL,
    row_id TEXT NOT NULL,
    column_id TEXT NOT NULL,
    value_text TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (table_id, row_id, column_id),
    FOREIGN KEY(table_id) REFERENCES daily_tables(id) ON DELETE CASCADE,
    FOREIGN KEY(row_id) REFERENCES daily_table_rows(id) ON DELETE CASCADE,
    FOREIGN KEY(column_id) REFERENCES daily_table_columns(id) ON DELETE CASCADE
);
