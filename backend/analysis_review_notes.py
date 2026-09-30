"""个人分析报告"建议核对"提示的集中清单。

报告导出时把每个学生的 review_note 汇总到受控 reports 目录下的
``.analysis_review_notes/session_<id>.json``，供成绩中心集中展示并按
教师复核锁对账；与导出的 HTML 无关，重新导出按学生范围合并。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

REVIEW_NOTES_DIRNAME = ".analysis_review_notes"
REVIEW_NOTES_VERSION = 1


def session_notes_path(reports_dir: Path, session_id: int) -> Path:
    return Path(reports_dir) / REVIEW_NOTES_DIRNAME / f"session_{int(session_id)}.json"


def load_session_notes(reports_dir: Path, session_id: int) -> dict[str, Any] | None:
    try:
        payload = json.loads(
            session_notes_path(reports_dir, session_id).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != REVIEW_NOTES_VERSION:
        return None
    items = payload.get("items")
    if not isinstance(items, list):
        return None
    return {
        "session_id": int(payload.get("session_id") or session_id),
        "score_revision": str(payload.get("score_revision") or ""),
        "generated_at": str(payload.get("generated_at") or ""),
        "items": [dict(item) for item in items if isinstance(item, dict)],
    }


def merge_session_notes(
    reports_dir: Path,
    session_id: int,
    *,
    score_revision: str,
    items: list[dict[str, Any]],
    scoped_student_ids: set[int],
) -> int:
    """替换本次导出范围内学生的条目，保留范围外学生的旧条目；返回合并后总数。"""
    existing = load_session_notes(reports_dir, session_id)
    kept = [
        item
        for item in (existing["items"] if existing is not None else [])
        if int(item.get("student_id") or 0) not in scoped_student_ids
    ]
    merged = kept + [dict(item) for item in items]
    target = session_notes_path(reports_dir, session_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.stem}.tmp")
    payload = {
        "version": REVIEW_NOTES_VERSION,
        "session_id": int(session_id),
        "score_revision": str(score_revision or ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": merged,
    }
    temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temp.replace(target)
    return len(merged)
