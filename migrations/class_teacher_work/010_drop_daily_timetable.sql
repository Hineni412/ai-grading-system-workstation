-- migration-policy: drop-tables daily_lesson_notes, daily_overrides, daily_regular_entries, daily_custom_slots, daily_week_anchor
DROP TABLE IF EXISTS daily_lesson_notes;
DROP TABLE IF EXISTS daily_overrides;
DROP TABLE IF EXISTS daily_regular_entries;
DROP TABLE IF EXISTS daily_custom_slots;
DROP TABLE IF EXISTS daily_week_anchor;
