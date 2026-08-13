from __future__ import annotations

import json
import sqlite3
from pathlib import Path


ROOT = Path(r"D:\AI阅卷系统_工作机版_v1.5.0\user_data")
EXPECTED = {
    "databases/grading_system.db": {
        "students": 91,
        "grading_sessions": 1,
        "exam_papers": 78,
        "session_results": 78,
        "session_details": 1404,
        "answer_regions": 19,
        "schema_migrations": 11,
        "session_attendance": 91,
        "annotated_results": 78,
        "session_templates": 1,
    },
    "databases/question_bank.db": {
        "papers": 24,
        "questions": 477,
        "question_tags": 4581,
        "question_fingerprints": 238,
        "knowledge_tag_identities": 77,
        "knowledge_graph_releases": 1,
        "knowledge_graph_release_mappings": 301,
        "knowledge_graph_release_relations": 48,
        "knowledge_graph_node_profiles": 77,
        "schema_migrations": 31,
        "question_frequency_cache": 240,
        "knowledge_identity_replacements": 17,
        "knowledge_graph_fine_term_dispositions": 294,
    },
    "workspaces/teaching-prep/teaching_prep.db": {
        "teaching_preferences": 1,
        "teaching_semesters": 0,
        "material_sources": 0,
        "material_versions": 0,
        "lesson_nodes": 0,
        "lesson_preparations": 0,
        "resource_pack_versions": 0,
        "lesson_draft_versions": 0,
        "pptx_versions": 0,
        "schema_migrations": 17,
    },
    "workspaces/class-teacher/class_teacher_work.db": {
        "class_teacher_preferences": 1,
        "intake_conversations": 1,
        "work_operations": 1,
        "work_nodes": 0,
        "ordinary_home_intake_drafts": 0,
        "intake_drafts": 0,
        "sensitive_protection": 0,
        "schema_migrations": 7,
    },
}


def main() -> int:
    checked_tables = 0
    for relative, tables in EXPECTED.items():
        database = ROOT / relative
        uri = f"{database.resolve(strict=True).as_uri()}?mode=ro&immutable=1"
        with sqlite3.connect(uri, uri=True, timeout=10) as connection:
            for table, expected in tables.items():
                actual = int(
                    connection.execute(
                        f'SELECT COUNT(*) FROM "{table}"'
                    ).fetchone()[0]
                )
                if actual != expected:
                    raise RuntimeError(
                        f"Count mismatch for {relative}:{table}: "
                        f"expected {expected}, got {actual}"
                    )
                checked_tables += 1
    print(
        json.dumps(
            {
                "database_count": len(EXPECTED),
                "checked_table_count": checked_tables,
                "all_counts_match": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
