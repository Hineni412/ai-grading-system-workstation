CREATE TABLE exercise_candidates (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    request_token TEXT NOT NULL UNIQUE
        CHECK(length(request_token) BETWEEN 8 AND 96),
    request_hash TEXT NOT NULL
        CHECK(length(request_hash) = 64),
    lesson_node_id TEXT NOT NULL,
    question_number TEXT,
    content_label TEXT,
    difficulty TEXT NOT NULL DEFAULT 'unrated'
        CHECK(difficulty IN ('unrated', 'easy', 'medium', 'hard')),
    classroom_use TEXT NOT NULL DEFAULT 'guided_practice'
        CHECK(classroom_use IN (
            'introduction',
            'example',
            'guided_practice',
            'independent_practice',
            'diagnostic',
            'challenge',
            'summary'
        )),
    estimated_minutes INTEGER
        CHECK(estimated_minutes IS NULL OR estimated_minutes BETWEEN 1 AND 60),
    teaching_focus TEXT,
    teacher_note TEXT,
    selection_status TEXT NOT NULL DEFAULT 'classroom_candidate'
        CHECK(selection_status IN (
            'classroom_candidate',
            'backup',
            'excluded'
        )),
    answer_status TEXT NOT NULL DEFAULT 'missing'
        CHECK(answer_status IN (
            'candidate',
            'teacher_verified',
            'rejected',
            'missing'
        )),
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK(is_active IN (0, 1)),
    revision INTEGER NOT NULL DEFAULT 1
        CHECK(revision > 0),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    updated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    FOREIGN KEY(lesson_node_id)
        REFERENCES lesson_nodes(id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_exercise_candidates_lesson
ON exercise_candidates(lesson_node_id, is_active, selection_status, updated_at);

CREATE TABLE exercise_regions (
    id TEXT PRIMARY KEY
        CHECK(length(id) = 32),
    exercise_candidate_id TEXT NOT NULL,
    region_role TEXT NOT NULL
        CHECK(region_role IN ('question', 'answer')),
    material_unit_id TEXT NOT NULL,
    sequence INTEGER NOT NULL
        CHECK(sequence > 0),
    crop_json TEXT NOT NULL,
    source_version_sha256 TEXT NOT NULL
        CHECK(length(source_version_sha256) = 64),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    UNIQUE(exercise_candidate_id, region_role, sequence),
    FOREIGN KEY(exercise_candidate_id)
        REFERENCES exercise_candidates(id)
        ON DELETE RESTRICT,
    FOREIGN KEY(material_unit_id)
        REFERENCES material_units(id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_exercise_regions_candidate
ON exercise_regions(exercise_candidate_id, region_role, sequence);

CREATE INDEX idx_exercise_regions_material_unit
ON exercise_regions(material_unit_id);
