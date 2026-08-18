"""班主任学生身份修复工具 — 旧临时编号改挂稳定学籍标识。

背景：班主任模块旧版用 HMAC 生成的 64 位十六进制「临时加密编号」引用学生，
成绩库重建后学生内部 id 变化导致旧编号全部失效。本工具把旧引用改挂到稳定
学籍标识（班级|学号，学号为空时用班级|姓名）。

用法（默认 dry-run，只报告不写入）：
    py update_tools/repair_class_teacher_student_refs.py \
        --vault-db <班主任工作区库> --work-db <班主任事务库> --grading-db <成绩库>
    确认报告无误后加 --apply 才真正写入。

功能：
    (a) 遍历 vault 库 student_subject_links：旧二进制指纹的行，从对应
        encrypted_objects payload 读出旧 source_student_id/display_name/class_label，
        用「班级+姓名」匹配当前成绩库花名册，把 source_fingerprint 改写为稳定标识
        明文。匹配不到或班级+姓名有歧义的行列出清单，不强行处理。
    (b) 遍历 work 库未收起的 intake_drafts（state='open'）：subject_refs 里的旧
        64 位十六进制编号按 (a) 建立的映射重写为新引用与新 revision，让旧对话草稿
        复活；无法映射的草稿标记 stale 并计数汇报。

注意：
    - 只适用于明文存储格式的班主任工作区；运行前必须先备份两个库文件。
    - 模块独立数据库之间没有原子事务，--apply 分库逐库写入并各自计数。
"""

from __future__ import annotations

import argparse
import json
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
from backend.class_teacher.roster_ref import (  # noqa: E402
    is_legacy_opaque_ref,
    legacy_opaque_ref,
    student_content_revision,
    student_stable_ref,
)

# 必须与 backend/class_teacher/vault_service.py 的 _PLAINTEXT_KEY 保持一致，
# 用于识别旧草稿里的 HMAC 临时编号（兼容输入）。
_PLAINTEXT_VMK = b"class-teacher-plaintext-debug-key"


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


def repair_subject_links(
    vault_db: Path,
    roster: list[ExistingStudent],
    *,
    apply: bool,
) -> dict[str, object]:
    """(a) 把 student_subject_links 的旧指纹改挂到稳定学籍标识。"""
    stats: dict[str, object] = {
        "links_total": 0,
        "links_already_stable": 0,
        "links_rewritten": 0,
        "links_unmatched": [],
        "links_ambiguous": [],
        "links_conflict": [],
    }
    # 旧草稿引用（64 位十六进制）→ (新稳定标识, 新 revision)
    ref_mapping: dict[str, tuple[str, str]] = {}
    index = _roster_by_class_and_name(roster)
    timestamp = _now()
    with closing(sqlite3.connect(vault_db)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT subject_id, source_fingerprint, payload_object_id "
            "FROM student_subject_links"
        ).fetchall()
        stats["links_total"] = len(rows)
        for row in rows:
            fingerprint = row["source_fingerprint"]
            if isinstance(fingerprint, str):
                stats["links_already_stable"] = int(stats["links_already_stable"]) + 1
                continue
            payload_row = connection.execute(
                "SELECT payload_ciphertext FROM encrypted_objects WHERE object_id = ?",
                (row["payload_object_id"],),
            ).fetchone()
            if payload_row is None:
                stats["links_unmatched"].append(  # type: ignore[union-attr]
                    {"subject_id": row["subject_id"], "reason": "payload 缺失"}
                )
                continue
            payload = json.loads(bytes(payload_row["payload_ciphertext"]).decode("utf-8"))
            old_source_id = str(payload.get("source_student_id") or "")
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
                "SELECT subject_id FROM student_subject_links WHERE source_fingerprint = ?",
                (new_ref,),
            ).fetchone()
            if conflict is not None and str(conflict["subject_id"]) != str(row["subject_id"]):
                stats["links_conflict"].append(  # type: ignore[union-attr]
                    {
                        "subject_id": row["subject_id"],
                        "new_ref": new_ref,
                        "reason": "稳定标识已被其他档案使用",
                    }
                )
                continue
            if old_source_id:
                ref_mapping[legacy_opaque_ref(_PLAINTEXT_VMK, old_source_id)] = (
                    new_ref,
                    student_content_revision(
                        source_key=student.source_key,
                        student_code=student.student_code,
                        display_name=student.display_name,
                        class_label=student.class_label,
                    ),
                )
            if apply:
                connection.execute(
                    "UPDATE student_subject_links "
                    "SET source_fingerprint = ?, updated_at = ? WHERE subject_id = ?",
                    (new_ref, timestamp, row["subject_id"]),
                )
            stats["links_rewritten"] = int(stats["links_rewritten"]) + 1
        if apply:
            connection.commit()
    stats["ref_mapping"] = ref_mapping
    return stats


def repair_drafts(
    work_db: Path,
    ref_mapping: dict[str, tuple[str, str]],
    *,
    apply: bool,
) -> dict[str, object]:
    """(b) 重写未收起草稿里的旧临时编号，无法映射的标记 stale。"""
    stats: dict[str, object] = {
        "drafts_scanned": 0,
        "drafts_rewritten": 0,
        "drafts_marked_stale": 0,
        "refs_rewritten": 0,
        "refs_unmapped": [],
    }
    timestamp = _now()
    with closing(sqlite3.connect(work_db)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT draft_id, subject_refs_json FROM intake_drafts WHERE state = 'open'"
        ).fetchall()
        stats["drafts_scanned"] = len(rows)
        for row in rows:
            refs = json.loads(str(row["subject_refs_json"]))
            if not isinstance(refs, list):
                continue
            changed = False
            unmappable: list[str] = []
            new_refs: list[dict[str, str]] = []
            for ref in refs:
                if (
                    isinstance(ref, dict)
                    and is_legacy_opaque_ref(str(ref.get("id") or ""))
                ):
                    legacy_id = str(ref["id"])
                    mapped = ref_mapping.get(legacy_id)
                    if mapped is None:
                        unmappable.append(legacy_id)
                        new_refs.append(dict(ref))
                    else:
                        new_refs.append(
                            {**ref, "id": mapped[0], "revision": mapped[1]}
                        )
                        changed = True
                        stats["refs_rewritten"] = int(stats["refs_rewritten"]) + 1
                else:
                    new_refs.append(ref)
            if unmappable:
                stats["refs_unmapped"].append(  # type: ignore[union-attr]
                    {"draft_id": row["draft_id"], "legacy_refs": unmappable}
                )
                stats["drafts_marked_stale"] = int(stats["drafts_marked_stale"]) + 1
                if apply:
                    connection.execute(
                        "UPDATE intake_drafts SET state = 'stale', updated_at = ? "
                        "WHERE draft_id = ?",
                        (timestamp, row["draft_id"]),
                    )
                    connection.execute(
                        "UPDATE intake_handoffs SET adoption_state = 'stale', "
                        "updated_at = ? WHERE draft_id = ? "
                        "AND adoption_state NOT IN ('adopted', 'discarded')",
                        (timestamp, row["draft_id"]),
                    )
                continue
            if changed:
                stats["drafts_rewritten"] = int(stats["drafts_rewritten"]) + 1
                if apply:
                    connection.execute(
                        "UPDATE intake_drafts SET subject_refs_json = ?, updated_at = ? "
                        "WHERE draft_id = ?",
                        (
                            json.dumps(new_refs, ensure_ascii=False, separators=(",", ":")),
                            timestamp,
                            row["draft_id"],
                        ),
                    )
        if apply:
            connection.commit()
    return stats


def _print_report(link_stats: dict[str, object], draft_stats: dict[str, object], *, apply: bool) -> None:
    mode = "实际写入" if apply else "dry-run（未写入，加 --apply 才生效）"
    print(f"模式: {mode}")
    print("== (a) 学生档案链接 ==")
    print(f"  链接总数: {link_stats['links_total']}")
    print(f"  已是稳定标识，跳过: {link_stats['links_already_stable']}")
    print(f"  可改挂: {link_stats['links_rewritten']}")
    unmatched = link_stats["links_unmatched"]
    ambiguous = link_stats["links_ambiguous"]
    conflict = link_stats["links_conflict"]
    print(f"  匹配不到（不处理）: {len(unmatched)}")
    for item in unmatched:  # type: ignore[union-attr]
        print(f"    - {item}")
    print(f"  班级+姓名歧义（不处理）: {len(ambiguous)}")
    for item in ambiguous:  # type: ignore[union-attr]
        print(f"    - {item}")
    print(f"  稳定标识冲突（不处理）: {len(conflict)}")
    for item in conflict:  # type: ignore[union-attr]
        print(f"    - {item}")
    print("== (b) 对话草稿引用 ==")
    print(f"  未收起草稿数: {draft_stats['drafts_scanned']}")
    print(f"  可复活（重写引用）: {draft_stats['drafts_rewritten']}")
    print(f"  重写引用条数: {draft_stats['refs_rewritten']}")
    print(f"  无法映射、标记 stale: {draft_stats['drafts_marked_stale']}")
    for item in draft_stats["refs_unmapped"]:  # type: ignore[union-attr]
        print(f"    - {item}")


def run(
    *,
    vault_db: Path,
    work_db: Path,
    grading_db: Path,
    apply: bool = False,
) -> tuple[dict[str, object], dict[str, object]]:
    roster = _load_roster(grading_db)
    link_stats = repair_subject_links(vault_db, roster, apply=apply)
    draft_stats = repair_drafts(
        work_db,
        link_stats.pop("ref_mapping"),  # type: ignore[arg-type]
        apply=apply,
    )
    return link_stats, draft_stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="班主任学生身份修复：旧临时编号改挂稳定学籍标识")
    parser.add_argument("--vault-db", required=True, type=Path, help="班主任工作区库（含 student_subject_links）")
    parser.add_argument("--work-db", required=True, type=Path, help="班主任事务库（含 intake_drafts）")
    parser.add_argument("--grading-db", required=True, type=Path, help="成绩库（只读，students 表）")
    parser.add_argument("--apply", action="store_true", help="真正写入；默认 dry-run 只报告")
    args = parser.parse_args(argv)
    link_stats, draft_stats = run(
        vault_db=args.vault_db,
        work_db=args.work_db,
        grading_db=args.grading_db,
        apply=args.apply,
    )
    _print_report(link_stats, draft_stats, apply=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
