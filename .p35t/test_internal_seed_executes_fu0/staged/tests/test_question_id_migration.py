import hashlib
import json
from pathlib import Path

import pytest

from question_id_contract import QuestionIdContractError
from question_id_migration import migrate_question_documents


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_mismatched_ids_leave_sources_and_backup_dir_untouched(tmp_path: Path) -> None:
    rubric_path = tmp_path / "rubric.json"
    answer_key_path = tmp_path / "answer_key.json"
    backup_dir = tmp_path / "backups"
    _write(rubric_path, {"questions": [{"question_id": "Q1", "parts": [{"part_id": "P1"}, {"part_id": "P2"}]}]})
    _write(answer_key_path, {"questions": [{"question_id": "Q1", "parts": [{"part_id": "P1"}]}]})
    rubric_before = _digest(rubric_path)
    answer_before = _digest(answer_key_path)

    with pytest.raises(QuestionIdContractError):
        migrate_question_documents(rubric_path, answer_key_path, backup_dir)

    assert _digest(rubric_path) == rubric_before
    assert _digest(answer_key_path) == answer_before
    assert not backup_dir.exists()


def test_matching_ids_backup_then_rewrite_to_canonical(tmp_path: Path) -> None:
    rubric_path = tmp_path / "rubric.json"
    answer_key_path = tmp_path / "answer_key.json"
    backup_dir = tmp_path / "backups"
    _write(
        rubric_path,
        {"questions": [{"question_id": "Q10", "parts": [{"part_id": "Q10(1)"}, {"part_id": "Q10(2)"}]}]},
    )
    _write(
        answer_key_path,
        {"questions": [{"question_id": "Q10", "parts": [{"part_id": "P1"}, {"part_id": "P2"}]}]},
    )
    rubric_before = _digest(rubric_path)
    answer_before = _digest(answer_key_path)

    report = migrate_question_documents(rubric_path, answer_key_path, backup_dir)

    # 备份保留迁移前内容
    assert _digest(report.rubric_backup) == rubric_before
    assert _digest(report.answer_key_backup) == answer_before
    # 源文件被改写为规范号
    rubric_after = json.loads(rubric_path.read_text(encoding="utf-8"))
    answer_after = json.loads(answer_key_path.read_text(encoding="utf-8"))
    rubric_parts = [p["part_id"] for p in rubric_after["questions"][0]["parts"]]
    answer_parts = [p["part_id"] for p in answer_after["questions"][0]["parts"]]
    assert rubric_parts == ["Q10(P1)", "Q10(P2)"]
    assert answer_parts == ["Q10(P1)", "Q10(P2)"]
    assert set(report.changed_files) == {rubric_path, answer_key_path}
