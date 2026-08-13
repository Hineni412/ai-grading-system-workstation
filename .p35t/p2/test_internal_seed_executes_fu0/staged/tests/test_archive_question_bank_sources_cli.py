from __future__ import annotations

import sys
from pathlib import Path

from question_bank.database.schema import connect, initialize_database
from update_tools import archive_question_bank_sources


def test_cli_defaults_to_dry_run(tmp_path: Path, monkeypatch, capsys) -> None:
    root = tmp_path / "project"
    data_root = root / "user_data"
    db_path = data_root / "databases" / "question_bank.db"
    source = tmp_path / "outside.docx"
    source.write_bytes(b"paper")
    initialize_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO questions (question_number, question_text, source_file) VALUES ('1', '题目', ?)",
            (str(source),),
        )

    monkeypatch.setattr(sys, "argv", ["archive_question_bank_sources.py", "--root", str(root)])

    assert archive_question_bank_sources.main() == 0
    output = capsys.readouterr().out
    assert "mode=dry-run" in output
    assert "scanned_sources=1" in output
    assert not (data_root / "question_bank" / "raw_papers").exists()
    with connect(db_path) as conn:
        assert conn.execute("SELECT source_file FROM questions").fetchone()[0] == str(source)
