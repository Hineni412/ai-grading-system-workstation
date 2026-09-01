CREATE TABLE IF NOT EXISTS daily_week_anchor (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    anchor_monday TEXT NOT NULL,
    anchor_week_no INTEGER NOT NULL CHECK (anchor_week_no BETWEEN 1 AND 40),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_custom_slots (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    start_text TEXT,
    end_text TEXT,
    position INTEGER NOT NULL CHECK (position BETWEEN 0 AND 8),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_regular_entries (
    day_of_week INTEGER NOT NULL CHECK (day_of_week BETWEEN 1 AND 5),
    slot_key TEXT NOT NULL,
    course_text TEXT NOT NULL,
    class_label TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (day_of_week, slot_key)
);

CREATE TABLE IF NOT EXISTS daily_overrides (
    id TEXT PRIMARY KEY,
    week_start TEXT NOT NULL,
    day_of_week INTEGER NOT NULL CHECK (day_of_week BETWEEN 1 AND 5),
    slot_key TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('set', 'clear')),
    course_text TEXT,
    class_label TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_daily_overrides_week
    ON daily_overrides(week_start, day_of_week, slot_key);

CREATE TABLE IF NOT EXISTS daily_lesson_notes (
    id TEXT PRIMARY KEY,
    note_date TEXT NOT NULL,
    slot_key TEXT NOT NULL,
    class_label TEXT NOT NULL,
    content_text TEXT NOT NULL,
    homework_text TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_daily_lesson_notes_class_date
    ON daily_lesson_notes(class_label, note_date DESC, created_at DESC);
