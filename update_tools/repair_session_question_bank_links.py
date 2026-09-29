"""考试题库关联修复工具 — 为已完成入库的考试补建「考试题 ↔ 题库题」确认关联。

背景:旧流程的「分析并入库」只把试卷导入题库并打标,没有建立
grading_question_links 确认关联,导致知识与结构页覆盖率为 0。
本工具按已发布评分依据的题号与同来源题库题唯一匹配,补建 confirmed 关联,
并把场次题库同步状态修正为 ready(或关联不完整时的 partial)。

用法(默认 dry-run,只报告不写入):
    py update_tools/repair_session_question_bank_links.py --session-id 2
确认报告无误后加 --apply 才真正写入。

注意:
    - --apply 前会自动把两个库文件快照备份到 user_data/backups/(带时间戳)。
    - 不调用任何模型、不产生费用;不动评分数据、不改题库题目与标签。
    - 题号在同一来源试卷内不唯一时不强行关联,列入 unresolved 报告。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 确保项目根目录在 sys.path 中
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.repositories.grading_database import open_grading_repositories  # noqa: E402

from path_manager import resolve_stored_file_path  # noqa: E402
from question_bank.database.schema import connect, initialize_database  # noqa: E402
from question_bank.services.source_question_link_service import (  # noqa: E402
    SourceQuestionLinkService,
)
from integration.question_tag_projection_service import (  # noqa: E402
    QuestionTagProjectionService,
)


def _backup_database(db_path: Path, backup_dir: Path) -> Path:
    """用 sqlite 备份 API 做一致性快照(WAL 安全)。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"{db_path.stem}.pre-link-repair-{stamp}{db_path.suffix}"
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as source:
        with sqlite3.connect(target) as dest:
            source.backup(dest)
    return target


def _load_session_rubric(
    session: dict,
    *,
    data_root: Path,
) -> dict:
    rubric_path = resolve_stored_file_path(
        session.get("rubric_path"),
        data_root=data_root,
    )
    if not rubric_path.is_file():
        raise FileNotFoundError(f"评分依据文件不存在: {session.get('rubric_path')}")
    payload = json.loads(rubric_path.read_text(encoding="utf-8"))
    rubric = payload.get("rubric") if isinstance(payload.get("rubric"), dict) else payload
    if not isinstance(rubric, dict) or not isinstance(rubric.get("questions"), list):
        raise ValueError("评分依据中没有可用的 questions 列表")
    return rubric


def _bank_candidates(bank_db: Path, source_file: str) -> list[dict]:
    with connect(bank_db) as conn:
        rows = conn.execute(
            """
            SELECT id, question_number, question_text, source_file
            FROM questions
            WHERE COALESCE(is_deleted, 0) = 0 AND source_file = ?
            ORDER BY id
            """,
            (source_file,),
        ).fetchall()
    return [dict(row) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", type=int, required=True)
    parser.add_argument(
        "--grading-db",
        type=Path,
        default=_PROJECT_ROOT / "user_data" / "databases" / "grading_system.db",
    )
    parser.add_argument(
        "--question-bank-db",
        type=Path,
        default=_PROJECT_ROOT / "user_data" / "databases" / "question_bank.db",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=_PROJECT_ROOT / "user_data",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=_PROJECT_ROOT / "user_data" / "backups",
    )
    parser.add_argument("--apply", action="store_true", help="真正写入(默认只报告)")
    args = parser.parse_args()

    grading_db = open_grading_repositories(args.grading_db)
    grading_db.initialize()
    session = grading_db.sessions.get_grading_session(args.session_id)
    if session is None:
        print(f"找不到考试场次: {args.session_id}")
        return 1
    print(f"场次 {args.session_id}: {session.get('session_name')}")
    print(f"当前同步状态: {session.get('question_bank_sync_state')}")

    rubric = _load_session_rubric(session, data_root=args.data_root)
    source_questions = [
        item for item in rubric["questions"] if isinstance(item, dict)
    ]
    source_file = str(session.get("source_paper_path") or "").strip()
    if not source_file:
        print("本场考试没有已归档的原始试卷,无法定位题库候选题。")
        return 1
    initialize_database(args.question_bank_db)
    candidates = _bank_candidates(args.question_bank_db, source_file)
    print(f"评分题目: {len(source_questions)} 题;题库同来源候选: {len(candidates)} 题")
    if not candidates:
        print("题库中没有该来源试卷的题目,请先完成试卷入库。")
        return 1

    service = SourceQuestionLinkService(args.question_bank_db)
    preview = service.match_imported_questions(
        source_questions=source_questions,
        imported_bank_questions=candidates,
    )
    print(f"可唯一匹配: {preview['confirmed']} 题;无法匹配: {preview['unresolved']} 题")
    if preview["unresolved_question_ids"]:
        print(f"未匹配题号: {preview['unresolved_question_ids']}")

    if not args.apply:
        print("dry-run 结束;确认无误后加 --apply 写入。")
        return 0

    grading_backup = _backup_database(args.grading_db, args.backup_dir)
    bank_backup = _backup_database(args.question_bank_db, args.backup_dir)
    print(f"已备份: {grading_backup.name}, {bank_backup.name}")

    result = service.confirm_imported_questions_for_session(
        grading_session_id=args.session_id,
        source_questions=source_questions,
        imported_bank_questions=candidates,
        preserve_existing_confirmed=True,
    )
    confirmed = int(result.get("confirmed") or 0)
    unresolved_ids = [
        str(item).strip()
        for item in result.get("unresolved_question_ids", [])
        if str(item).strip()
    ]
    ready = confirmed > 0 and not unresolved_ids
    grading_db.sessions.update_question_bank_sync_state(
        args.session_id,
        state="ready" if ready else "partial",
        details={
            "intake_required": True,
            "intake_category": "complete",
            "repair_tool": "repair_session_question_bank_links",
            "repaired_at": datetime.now().isoformat(timespec="seconds"),
            "linked_count": confirmed,
            "unresolved_question_ids": unresolved_ids,
        },
        error=None if ready else "题库关联未全部建立;知识结构页只显示已关联题目。",
    )
    print(f"已建立确认关联 {confirmed} 条,场次状态写为 {'ready' if ready else 'partial'}。")

    # 设计 E §7.2/§7.4：确认关联后冻结证据快照并对评分依据盖章。
    from question_bank.solution_evidence.evidence_snapshot import (
        freeze_session_evidence_snapshot,
    )
    snapshot_path = freeze_session_evidence_snapshot(
        Path(args.question_bank_db),
        grading_session_id=args.session_id,
        upload_config_dir=Path(args.data_root) / "config" / "uploaded",
        data_root=Path(args.data_root),
        rubric_path=resolve_stored_file_path(
            session.get("rubric_path"), data_root=Path(args.data_root)
        ),
    )
    print(f"证据快照: {snapshot_path or '无可冻结内容'}")

    projection = QuestionTagProjectionService(args.question_bank_db).project_session(
        grading_session_id=args.session_id,
        rubric=rubric,
    )
    print(f"验证:知识图谱覆盖 {projection.covered_items}/{projection.total_items} 个评分题")
    if projection.missing_items:
        print(f"仍缺失: {dict(projection.missing_items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
