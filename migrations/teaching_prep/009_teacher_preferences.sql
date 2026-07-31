CREATE TABLE teaching_preferences (
    profile_key TEXT PRIMARY KEY
        CHECK(profile_key = 'default'),
    revision INTEGER NOT NULL
        CHECK(revision > 0),
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    )
);

INSERT INTO teaching_preferences (
    profile_key,
    revision,
    payload_json
)
VALUES (
    'default',
    1,
    '{"avoid_direct_homework_copy":true,"avoid_ppt_duplicates":true,"label_textbook_pages":true,"page_label_font_size":28,"practice_trim_level":"moderate","prefer_short_practice":true,"preserve_teaching_examples":true,"prioritize_homework_workbook":true,"schema_version":1,"supplement_as_source_image":true,"supplement_from_references":true,"supplement_question_limit":2,"trim_excess_practice":true}'
);
