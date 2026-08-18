from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.class_teacher.errors import VaultError
from backend.class_teacher.roster_ref import (
    is_legacy_opaque_ref,
    parse_stable_ref,
    student_stable_ref,
)
from backend.class_teacher.vault_service import VaultService
from backend.workspaces.contracts import WorkspaceContext


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _service(tmp_path: Path) -> VaultService:
    grading = tmp_path / "grading.db"
    with closing(sqlite3.connect(grading)) as connection:
        connection.execute(
            "CREATE TABLE students (id INTEGER PRIMARY KEY, student_code TEXT, name TEXT, class_name TEXT)"
        )
        connection.executemany(
            "INSERT INTO students VALUES (?, ?, ?, ?)",
            [
                (1, "40", "合成学生甲", "9班"),
                (2, None, "合成学生乙", "9班"),
            ],
        )
        connection.commit()
    service = VaultService(
        WorkspaceContext(
            module_id="class-teacher",
            root=tmp_path / "workspaces" / "class-teacher",
            paths=SimpleNamespace(
                project_root=PROJECT_ROOT,
                migration_project_root=PROJECT_ROOT,
                db_path=grading,
            ),
        )
    )
    service.ensure_plaintext_ready()
    return service


def test_stable_ref_prefers_student_code() -> None:
    assert (
        student_stable_ref(class_label="9班", student_code="40", display_name="张三")
        == "9班|40"
    )


def test_stable_ref_falls_back_to_name_when_code_missing() -> None:
    assert (
        student_stable_ref(class_label="9班", student_code="", display_name="张三")
        == "9班|张三"
    )
    assert (
        student_stable_ref(class_label="9班", student_code=None, display_name="张三")
        == "9班|张三"
    )


def test_stable_ref_escapes_separator_and_backslash() -> None:
    ref = student_stable_ref(class_label="一|班", student_code="A\\1", display_name="张三")
    assert ref == "一\\|班|A\\\\1"
    assert parse_stable_ref(ref) == ("一|班", "A\\1")


@pytest.mark.parametrize(
    "value",
    ["", "没有分隔符", "a|b|c", "9班|", "尾部转义\\", None],
)
def test_parse_stable_ref_rejects_malformed(value: object) -> None:
    assert parse_stable_ref(str(value or "")) is None


def test_is_legacy_opaque_ref() -> None:
    assert is_legacy_opaque_ref("a" * 64)
    assert not is_legacy_opaque_ref("9班|40")
    assert not is_legacy_opaque_ref("A" * 64)
    assert not is_legacy_opaque_ref("")


def test_candidates_use_stable_ref_and_resolve_round_trip(tmp_path: Path) -> None:
    service = _service(tmp_path)
    candidates = service.class_roster.ai_candidates(token="", class_label="9班")
    by_name = {item["display_name"]: item for item in candidates}
    assert by_name["合成学生甲"]["id"] == "9班|40"
    # 学号为空时按姓名。
    assert by_name["合成学生乙"]["id"] == "9班|合成学生乙"

    resolved = service.class_roster.resolve_roster_ref(
        roster_ref=by_name["合成学生甲"]["id"],
        expected_revision=by_name["合成学生甲"]["revision"],
    )
    assert resolved.student_code == "40"
    assert resolved.display_name == "合成学生甲"


def test_resolve_roster_ref_conflict_on_changed_revision(tmp_path: Path) -> None:
    service = _service(tmp_path)
    candidate = service.class_roster.ai_candidates(token="", class_label="9班")[0]
    with pytest.raises(VaultError, match="学生资料已变化") as excinfo:
        service.class_roster.resolve_roster_ref(
            roster_ref=candidate["id"],
            expected_revision="0" * 64,
        )
    assert excinfo.value.status_code == 409


@pytest.mark.parametrize("ref", ["9班|不存在", "f" * 64])
def test_resolve_roster_ref_invalid_for_unknown_or_legacy(
    tmp_path: Path, ref: str
) -> None:
    service = _service(tmp_path)
    # 兼容输入：旧格式 64 位十六进制临时编号同样按「引用已失效」处理。
    with pytest.raises(VaultError, match="学生引用已失效") as excinfo:
        service.class_roster.resolve_roster_ref(roster_ref=ref, expected_revision="r")
    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "class_teacher_subject_ref_invalid"


def test_roster_identity_for_ref_preview(tmp_path: Path) -> None:
    service = _service(tmp_path)
    identity = service.class_roster.roster_identity_for_ref(roster_ref="9班|40")
    assert identity is not None
    assert identity["display_name"] == "合成学生甲"
    assert identity["class_label"] == "9班"
    assert identity["subject_id"] is None

    assert service.class_roster.roster_identity_for_ref(roster_ref="9班|不存在") is None
    assert service.class_roster.roster_identity_for_ref(roster_ref="f" * 64) is None
    assert service.class_roster.roster_identity_for_ref(roster_ref="") is None
