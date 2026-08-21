"""班主任学生身份修复工具 — 成绩导入临时身份改挂花名册稳定标识。

背景：多学科成绩整表导入时，花名册里查不到已建档档案的学生会被自动建成
「evidence-upload|班级|准考证号」临时身份的档案，没有关联阅卷花名册学号。
之后按花名册建档会再生成一条空档案，形成重复。本工具把临时身份档案更正为
花名册稳定标识（班级|学号），并把档案展示用的来源编号改为花名册来源编号，
保证「一个学生自始至终只有一条档案」。

用法（默认 dry-run，只报告不写入）：
    py update_tools/repair_class_teacher_evidence_upload_identities.py \
        --vault-db user_data/workspaces/class-teacher/student_affairs.db \
        --grading-db user_data/databases/grading_system.db
    确认报告无误后加 --apply 才真正写入。

功能：
    遍历 vault 库 student_subject_links 的 active 档案：payload 中
    source_student_id 以「evidence-upload|」开头的行，用「班级+姓名」匹配
    当前成绩库花名册，把 source_fingerprint 改写为花名册稳定标识，
    同时用 EncryptedObjectRepository.put 产生新 revision，把 payload 的
    source_student_id 更正为花名册来源编号、identity_snapshot_at 刷新为
    修复时间。匹配不到、班级+姓名有歧义、或目标稳定标识已被其他档案占用
    （重复空档案需先删除）的行列出清单，不强行处理。

注意：
    - 只适用于明文存储格式的班主任工作区；运行前必须先备份库文件。
    - 只改档案身份链接与身份展示字段，不触碰任何成绩证据行。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

# 确保项目根目录在 sys.path 中
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.class_teacher.existing_student_roster import (  # noqa: E402
    ExistingStudent,
    SqliteExistingStudentRosterSource,
)
from backend.class_teacher.roster_ref import student_stable_ref  # noqa: E402
from backend.class_teacher.secure_repository import EncryptedObjectRepository  # noqa: E402

# 明文兼容库的 payload 读写不依赖 VMK，值仅用于满足接口签名。
_PLAINTEXT_VMK = b"class-teacher-plaintext-debug-key"

_EVIDENCE_UPLOAD_PREFIX = "evidence-upload|"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _load_roster(grading_db: Path) -> list[ExistingStudent]:
    items, _revision = SqliteExistingStudentRosterSource(grading_db).snapshot()
    return items


def _roster_by_class_and_name(
    roster: list[ExistingStudent],
) -> dict[tuple[str, str], list[ExistingStudent]]:
    index: dict[tuple[str, str], list[ExistingStudent]] = {}
    for item in roster:
        index.setdefault((item.class_label, item.display_name), []).append(item)
    return index


def repair_evidence_upload_identities(
    vault_db: Path,
    roster: list[ExistingStudent],
    *,
    apply: bool,
) -> dict[str, object]:
    """把 evidence-upload 临时身份档案改挂到花名册稳定标识。"""
    stats: dict[str, object] = {
        "links_total": 0,
        "links_not_evidence_upload": 0,
        "links_rewritten": 0,
        "links_unmatched": [],
        "links_ambiguous": [],
        "links_conflict": [],
    }
    index = _roster_by_class_and_name(roster)
    timestamp = _now()
    repository = EncryptedObjectRepository()
    with closing(sqlite3.connect(vault_db)) as connection:
        connection.row_factory = sqlite3.Row
        if EncryptedObjectRepository.has_unsupported_storage_format(connection):
            raise SystemExit("数据库不是明文兼容格式，本工具不适用，未做任何写入")
        rows = connection.execute(
            "SELECT subject_id, source_fingerprint, payload_object_id "
            "FROM student_subject_links WHERE state = 'active'"
        ).fetchall()
        stats["links_total"] = len(rows)

        def _work() -> None:
            for row in rows:
                payload, revision = repository.get(
                    connection,
                    vmk=_PLAINTEXT_VMK,
                    object_id=str(row["payload_object_id"]),
                )
                source_id = str(payload.get("source_student_id") or "")
                if not source_id.startswith(_EVIDENCE_UPLOAD_PREFIX):
                    stats["links_not_evidence_upload"] = int(
                        stats["links_not_evidence_upload"]
                    ) + 1
                    continue
                display_name = str(payload.get("display_name") or "").strip()
                class_label = str(payload.get("class_label") or "").strip()
                matches = index.get((class_label, display_name), [])
                if not matches:
                    stats["links_unmatched"].append(  # type: ignore[union-attr]
                        {
                            "subject_id": row["subject_id"],
                            "display_name": display_name,
                            "class_label": class_label,
                            "reason": "不在当前花名册",
                        }
                    )
                    continue
                if len(matches) > 1:
                    stats["links_ambiguous"].append(  # type: ignore[union-attr]
                        {
                            "subject_id": row["subject_id"],
                            "display_name": display_name,
                            "class_label": class_label,
                            "reason": "当前花名册班级+姓名不唯一",
                        }
                    )
                    continue
                student = matches[0]
                new_ref = student_stable_ref(
                    class_label=student.class_label,
                    student_code=student.student_code,
                    display_name=student.display_name,
                )
                conflict = connection.execute(
                    "SELECT subject_id FROM student_subject_links "
                    "WHERE source_fingerprint = ?",
                    (new_ref,),
                ).fetchone()
                if conflict is not None and str(conflict["subject_id"]) != str(
                    row["subject_id"]
                ):
                    stats["links_conflict"].append(  # type: ignore[union-attr]
                        {
                            "subject_id": row["subject_id"],
                            "display_name": display_name,
                            "new_ref": new_ref,
                            "reason": "花名册稳定标识已被其他档案占用，需先删除重复空档案",
                        }
                    )
                    continue
                if apply:
                    connection.execute(
                        "UPDATE student_subject_links "
                        "SET source_fingerprint = ?, updated_at = ? "
                        "WHERE subject_id = ?",
                        (new_ref, timestamp, row["subject_id"]),
                    )
                    repository.put(
                        connection,
                        vmk=_PLAINTEXT_VMK,
                        object_id=str(row["payload_object_id"]),
                        object_type="student_subject",
                        payload={
                            **payload,
                            "source_student_id": student.source_key,
                            "identity_snapshot_at": timestamp,
                        },
                        expected_revision=revision,
                    )
                stats["links_rewritten"] = int(stats["links_rewritten"]) + 1

        if apply:
            with connection:
                _work()
        else:
            _work()
    return stats


def _print_report(stats: dict[str, object], *, apply: bool) -> None:
    mode = "实际写入" if apply else "dry-run（未写入，加 --apply 才生效）"
    print(f"模式: {mode}")
    print("== 成绩导入临时身份档案 ==")
    print(f"  active 档案总数: {stats['links_total']}")
    print(f"  非临时身份，跳过: {stats['links_not_evidence_upload']}")
    print(f"  可改挂花名册身份: {stats['links_rewritten']}")
    unmatched = stats["links_unmatched"]
    ambiguous = stats["links_ambiguous"]
    conflict = stats["links_conflict"]
    print(f"  匹配不到（不处理）: {len(unmatched)}")
    for item in unmatched:  # type: ignore[union-attr]
        print(f"    - {item}")
    print(f"  班级+姓名歧义（不处理）: {len(ambiguous)}")
    for item in ambiguous:  # type: ignore[union-attr]
        print(f"    - {item}")
    print(f"  稳定标识冲突（不处理）: {len(conflict)}")
    for item in conflict:  # type: ignore[union-attr]
        print(f"    - {item}")


def run(
    *,
    vault_db: Path,
    grading_db: Path,
    apply: bool = False,
) -> dict[str, object]:
    roster = _load_roster(grading_db)
    return repair_evidence_upload_identities(vault_db, roster, apply=apply)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="班主任学生身份修复：成绩导入临时身份改挂花名册稳定标识"
    )
    parser.add_argument(
        "--vault-db", required=True, type=Path,
        help="班主任工作区库（含 student_subject_links）",
    )
    parser.add_argument(
        "--grading-db", required=True, type=Path,
        help="成绩库（只读，students 表）",
    )
    parser.add_argument(
        "--apply", action="store_true", help="真正写入；默认 dry-run 只报告"
    )
    args = parser.parse_args(argv)
    stats = run(
        vault_db=args.vault_db,
        grading_db=args.grading_db,
        apply=args.apply,
    )
    _print_report(stats, apply=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
