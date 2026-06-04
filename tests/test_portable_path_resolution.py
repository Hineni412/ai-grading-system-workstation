from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from path_manager import resolve_stored_file_path
from report import ReportGenerator


class PortablePathResolutionTests(unittest.TestCase):
    def test_resolves_old_user_data_absolute_path_to_current_data_root(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            data_root = root / "user_data"
            actual = data_root / "config" / "uploaded" / "rubric_legacy.json"
            actual.parent.mkdir(parents=True)
            actual.write_text("{}", encoding="utf-8")

            resolved = resolve_stored_file_path(
                r"D:\old_machine\AI_Grading_System\user_data\config\uploaded\rubric_legacy.json",
                data_root=data_root,
                project_root=root,
            )

        self.assertEqual(resolved, actual)

    def test_report_loads_score_map_when_session_rubric_path_was_moved(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            data_root = root / "user_data"
            rubric_path = data_root / "config" / "uploaded" / "rubric_legacy.json"
            rubric_path.parent.mkdir(parents=True)
            rubric_path.write_text(
                json.dumps(
                    {
                        "questions": [
                            {
                                "question_id": "Q1",
                                "question_type": "calculation",
                                "max_score": 10,
                                "knowledge_id": "C02",
                                "knowledge_name": "整式运算",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            db_path = data_root / "databases" / "grading_system.db"
            db_path.parent.mkdir(parents=True)
            with sqlite3.connect(db_path) as conn:
                conn.execute("CREATE TABLE grading_sessions (id INTEGER PRIMARY KEY, rubric_path TEXT)")
                conn.execute(
                    "INSERT INTO grading_sessions VALUES (1, ?)",
                    (r"D:\old_machine\AI_Grading_System\user_data\config\uploaded\rubric_legacy.json",),
                )

            score_map, type_map = ReportGenerator(db_path, data_root / "reports")._load_session_question_maps(1)

        self.assertEqual(score_map["Q1"], 10)
        self.assertEqual(type_map["Q1"], "calculation")


if __name__ == "__main__":
    unittest.main()
