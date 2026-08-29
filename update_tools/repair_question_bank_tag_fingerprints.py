"""题库标签指纹一次性修复工具 — 把"题目指纹"从「内容+词表」迁移为「纯内容」。

背景：question_analysis_items.source_content_hash 曾把词表契约（版本号、
知识图谱发布、候选词表）嵌入逐题指纹。词表版本是全局的：教师确认一个
新词就会让全库存量标签整体"过期"，补齐任务因此把完整题目整卷重打。
代码已改为纯内容指纹（词表升级不再触发重打）；本工具把库内存量成功
记录迁移到同一口径：

  1) 内容未变的题目：把最近一次成功打标行的指纹重写为当前纯内容指纹，
     恢复"正常入库"状态——补齐核对时会被跳过（幂等，二次运行无写入）；
  2) 内容已变的题目（例如题型从填空被修正为解答）：保持标签过期，并调用
     判定点模块自身的 _sync_source 把判定点版本标记 stale（事件
     "question content changed"），试卷卡片的"判定点/完整度"计数会如实
     下降，教师在前端即可看到哪张卷、几道题需要补齐重做。

"内容未变"的双重见证（两者都满足才重写指纹）：
  A) 当前 criterion 指纹 == 该题最近一次成功判定点载荷里保存的指纹；
  B) questions.updated_at 与 papers.updated_at 都不晚于该次成功打标时间。
  没有判定点载荷的题目只要求见证 B。

用法（默认 dry-run，只报告不写入）：
    runtime/python/python.exe update_tools/repair_question_bank_tag_fingerprints.py
    确认报告无误后加 --apply 才真正写入：
    runtime/python/python.exe update_tools/repair_question_bank_tag_fingerprints.py --apply

--apply 会先用 SQLite backup API 把题库库文件备份为同目录 .bak-时间戳，
并且拒绝在应用（8035 端口）运行中执行——运行中的旧代码仍按旧口径比对
指纹，会把刚修复的状态再次判为过期。请先停止应用再 --apply，完成后重新
启动应用加载新代码。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from question_bank.database.schema import connect  # noqa: E402
from question_bank.taxonomy.curriculum_catalog import curriculum_volume  # noqa: E402
from question_bank.training_criteria import (  # noqa: E402
    QuestionAnalysisInputLoader,
    TrainingCriterionModule,
)

DEFAULT_DB = Path("user_data/databases/question_bank.db")
HEALTHZ_URL = "http://127.0.0.1:8035/api/healthz"
LOAD_CHUNK = 50


def _readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _load_tag_rows(db_path: Path) -> dict[int, sqlite3.Row]:
    """每题最近一次成功打标行（指纹重写目标）。"""

    with _readonly(db_path) as conn:
        rows = conn.execute(
            """
            SELECT i.rowid AS tag_rowid, i.question_id, i.source_content_hash,
                   i.updated_at AS tag_updated_at
            FROM question_analysis_items i
            JOIN (
                SELECT question_id, MAX(rowid) AS max_rowid
                FROM question_analysis_items
                WHERE tag_status = 'succeeded'
                GROUP BY question_id
            ) latest ON latest.max_rowid = i.rowid
            JOIN questions q ON q.id = i.question_id
                 AND COALESCE(q.is_deleted, 0) = 0
            """
        ).fetchall()
    return {int(row["question_id"]): row for row in rows}


def _load_criteria_hashes(db_path: Path) -> dict[int, str]:
    """每题最近一次成功判定点载荷里的纯内容指纹（见证 A 的对照值）。"""

    result: dict[int, str] = {}
    with _readonly(db_path) as conn:
        rows = conn.execute(
            """
            SELECT i.question_id, i.criteria_payload_json
            FROM question_analysis_items i
            JOIN (
                SELECT question_id, MAX(rowid) AS max_rowid
                FROM question_analysis_items
                WHERE criteria_status = 'succeeded'
                  AND COALESCE(criteria_payload_json, '') <> ''
                GROUP BY question_id
            ) latest ON latest.max_rowid = i.rowid
            """
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["criteria_payload_json"])
        except (TypeError, ValueError):
            continue
        value = str(payload.get("source_content_hash") or "") if isinstance(
            payload, dict
        ) else ""
        if value:
            result[int(row["question_id"])] = value
    return result


def _load_papers(
    db_path: Path,
    question_ids: list[int],
) -> dict[int, sqlite3.Row]:
    placeholders = ",".join("?" for _ in question_ids)
    with _readonly(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT q.id AS question_id, q.paper_id, q.updated_at AS question_updated_at,
                   q.question_number, p.title, p.updated_at AS paper_updated_at,
                   p.grade, p.semester, p.textbook_version
            FROM questions q
            JOIN papers p ON p.id = q.paper_id
            WHERE q.id IN ({placeholders})
            """,
            question_ids,
        ).fetchall()
    return {int(row["question_id"]): row for row in rows}


def _app_is_running() -> bool:
    try:
        with urlopen(HEALTHZ_URL, timeout=2) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def _backup(db_path: Path) -> Path:
    backup = db_path.with_name(
        f"{db_path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}"
    )
    source = sqlite3.connect(db_path)
    try:
        target = sqlite3.connect(backup)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="题库库路径")
    parser.add_argument("--apply", action="store_true", help="真正写入（默认只报告）")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    if not db_path.is_file():
        print(f"找不到题库库文件: {db_path}")
        return 1
    data_root = db_path.parent.parent

    tag_rows = _load_tag_rows(db_path)
    criteria_hashes = _load_criteria_hashes(db_path)
    if not tag_rows:
        print("题库中没有成功打标记录，无需修复。")
        return 0
    paper_rows = _load_papers(db_path, sorted(tag_rows))

    by_paper: dict[int, list[int]] = {}
    for question_id in sorted(tag_rows):
        paper_id = int(paper_rows[question_id]["paper_id"])
        by_paper.setdefault(paper_id, []).append(question_id)

    loader = QuestionAnalysisInputLoader(db_path=db_path, data_root=data_root)
    restore_rows: list[tuple[int, str]] = []
    rework_inputs: dict[int, object] = {}
    failures: list[tuple[int, str]] = []
    no_volume_papers: list[str] = []
    stats = {"already_current": 0, "restore": 0, "rework": 0}
    rework_by_paper: dict[int, list[str]] = {}

    for paper_id, question_ids in sorted(by_paper.items()):
        sample = paper_rows[question_ids[0]]
        volume = curriculum_volume(
            grade=sample["grade"],
            semester=sample["semester"],
            textbook_version=sample["textbook_version"],
        )
        if volume is None:
            no_volume_papers.append(
                f"试卷 #{paper_id} {str(sample['title'] or '')[:40]}（{len(question_ids)} 道已打标题）"
            )
            continue
        for start in range(0, len(question_ids), LOAD_CHUNK):
            chunk = question_ids[start : start + LOAD_CHUNK]
            try:
                inputs = loader.load(
                    chunk, curriculum_volume_id=str(volume["id"])
                )
            except (KeyError, OSError, TypeError, ValueError):
                inputs = []
                for question_id in chunk:
                    try:
                        inputs.extend(
                            loader.load(
                                (question_id,),
                                curriculum_volume_id=str(volume["id"]),
                            )
                        )
                    except (KeyError, OSError, TypeError, ValueError) as exc:
                        failures.append((question_id, type(exc).__name__))
            by_question = {
                int(item.question_id): item for item in inputs
            }
            for question_id in chunk:
                question = by_question.get(question_id)
                if question is None:
                    continue
                tag_row = tag_rows[question_id]
                meta = paper_rows[question_id]
                current_hash = str(question.source_content_hash)
                if str(tag_row["source_content_hash"]) == current_hash:
                    stats["already_current"] += 1
                    continue
                payload_hash = criteria_hashes.get(question_id)
                witness_a = payload_hash is None or (
                    payload_hash
                    == str(question.criterion_source_content_hash)
                )
                witness_b = (
                    str(meta["question_updated_at"] or "")
                    <= str(tag_row["tag_updated_at"] or "")
                    and str(meta["paper_updated_at"] or "")
                    <= str(tag_row["tag_updated_at"] or "")
                )
                if witness_a and witness_b:
                    restore_rows.append(
                        (int(tag_row["tag_rowid"]), current_hash)
                    )
                    stats["restore"] += 1
                else:
                    rework_inputs[question_id] = question
                    stats["rework"] += 1
                    numbers = rework_by_paper.setdefault(paper_id, [])
                    numbers.append(str(meta["question_number"] or question_id))

    print(
        f"指纹口径迁移报告（{len(tag_rows)} 道已打标题）:",
        stats,
    )
    if no_volume_papers:
        print("以下试卷缺少年级/学期/教材版本，无法计算指纹，已跳过：")
        for line in no_volume_papers:
            print(f"  - {line}")
    if failures:
        print(f"{len(failures)} 道题加载失败（保持现状，补齐时会按题处理）：")
        for question_id, error in failures[:10]:
            print(f"  - 题 #{question_id}: {error}")
    if rework_by_paper:
        print("内容已变、需要补齐重做的题目（判定点将标记 stale，前端可见）：")
        for paper_id, numbers in sorted(rework_by_paper.items()):
            sample = paper_rows[
                next(q for q in by_paper[paper_id] if q in rework_inputs)
            ]
            print(
                f"  - 试卷 #{paper_id} {str(sample['title'] or '')[:40]}: "
                f"{len(numbers)} 道（第 {','.join(numbers)} 题）"
            )

    if not args.apply:
        print("dry-run：未写入任何数据。确认后加 --apply 执行。")
        return 0

    if _app_is_running():
        print("应用正在运行（8035 端口可达）。--apply 会与运行中的旧代码口径冲突，")
        print("请先停止应用再执行 --apply，完成后重新启动应用。")
        return 1

    backup = _backup(db_path)
    print(f"已备份: {backup}")

    with connect(db_path) as conn:
        conn.executemany(
            "UPDATE question_analysis_items SET source_content_hash = ? "
            "WHERE rowid = ?",
            [(value, rowid) for rowid, value in restore_rows],
        )
    print(f"已重写 {len(restore_rows)} 条成功打标行的指纹。")

    if rework_inputs:
        module = TrainingCriterionModule(db_path)
        for question_id in sorted(rework_inputs):
            module._sync_source(rework_inputs[question_id])
    print(f"已把 {len(rework_inputs)} 道内容已变题目的判定点标记为 stale。")
    print("修复完成。请启动应用加载新代码后，在前端核对试卷卡片计数。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
