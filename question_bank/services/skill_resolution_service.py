from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from question_bank.database.schema import connect, initialize_database
from question_bank.models.skill_catalog import (
    RankedSkillCandidate,
    ResolutionOutcome,
    SkillResolution,
    SkillResolutionRequest,
    normalize_display_text,
    normalize_stable_key,
)


_BROAD_LABELS = {"性质", "计算", "作图", "综合", "应用", "概念", "方法"}


class SkillResolutionService:
    AUTO_ACCEPT_MIN = 0.92
    AUTO_ACCEPT_MARGIN = 0.15

    def __init__(self, db_path: str | Path, *, context_ranker=None) -> None:
        self.db_path = Path(db_path)
        self.context_ranker = context_ranker

    def resolve(
        self,
        request: SkillResolutionRequest,
        *,
        persist_conflict: bool = True,
    ) -> SkillResolution:
        initialize_database(self.db_path)
        normalized_label = normalize_stable_key(request.raw_label)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM skills WHERE status IN ('active', 'merged') ORDER BY id"
            ).fetchall()
            skills = {int(row["id"]): dict(row) for row in rows}

            previous = conn.execute(
                """
                SELECT resolved_skill_id
                FROM skill_resolution_conflicts
                WHERE source_type = ? AND source_ref = ?
                  AND normalized_label = ? AND state = 'resolved'
                ORDER BY resolved_at DESC, id DESC
                LIMIT 1
                """,
                (
                    request.source_type.value,
                    request.source_ref,
                    normalized_label,
                ),
            ).fetchone()
            if previous is not None and previous["resolved_skill_id"] is not None:
                target_id = _active_skill_id(skills, int(previous["resolved_skill_id"]))
                if target_id is not None:
                    return SkillResolution(
                        ResolutionOutcome.RESOLVED_EXISTING,
                        target_id,
                        1.0,
                        "复用已处理的技能选择",
                    )

            if request.stable_key_hint:
                stable_matches = [
                    int(row["id"])
                    for row in skills.values()
                    if normalize_stable_key(row["stable_key"]) == request.stable_key_hint
                ]
                targets = _active_targets(skills, stable_matches)
                if len(targets) == 1:
                    return SkillResolution(
                        ResolutionOutcome.RESOLVED_EXISTING,
                        targets[0],
                        1.0,
                        "稳定技能标识完全一致",
                    )

            label_matches: list[int] = []
            for row in skills.values():
                labels = [row["name"], *_json_text_list(row.get("aliases_json"))]
                if any(normalize_stable_key(label) == normalized_label for label in labels):
                    label_matches.append(int(row["id"]))
            targets = _active_targets(skills, label_matches)
            if len(targets) == 1:
                return SkillResolution(
                    ResolutionOutcome.RESOLVED_EXISTING,
                    targets[0],
                    1.0,
                    "技能名称或别名完全一致",
                )
            if len(targets) > 1:
                return self._conflict(
                    conn,
                    request,
                    normalized_label,
                    "名称对应多个具体技能",
                    targets,
                    persist=persist_conflict,
                )

            if normalize_display_text(request.raw_label) in _BROAD_LABELS:
                return self._conflict(
                    conn,
                    request,
                    normalized_label,
                    "名称过宽，不能作为具体训练技能",
                    (),
                    persist=persist_conflict,
                )
            return self._conflict(
                conn,
                request,
                normalized_label,
                "无法确定唯一的具体技能",
                (),
                persist=persist_conflict,
            )

    def resolve_many(
        self,
        requests: Iterable[SkillResolutionRequest],
        *,
        persist_conflicts: bool = True,
    ) -> list[SkillResolution]:
        return [
            self.resolve(request, persist_conflict=persist_conflicts)
            for request in requests
        ]

    def _conflict(
        self,
        conn,
        request: SkillResolutionRequest,
        normalized_label: str,
        reason: str,
        candidate_ids: Iterable[int],
        *,
        persist: bool,
    ) -> SkillResolution:
        candidates = tuple(
            RankedSkillCandidate(int(skill_id), 0.0, reason)
            for skill_id in candidate_ids
        )
        if persist:
            payload = json.dumps([item.skill_id for item in candidates], ensure_ascii=False)
            existing = conn.execute(
                """
                SELECT id FROM skill_resolution_conflicts
                WHERE source_type = ? AND source_ref = ?
                  AND normalized_label = ? AND state = 'open'
                ORDER BY id DESC LIMIT 1
                """,
                (
                    request.source_type.value,
                    request.source_ref,
                    normalized_label,
                ),
            ).fetchone()
            evidence = json.dumps(
                {
                    "grade": request.grade,
                    "topic_hint": request.topic_hint,
                    "stable_key_hint": request.stable_key_hint,
                    "existing_tags": list(request.existing_tags),
                },
                ensure_ascii=False,
            )
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO skill_resolution_conflicts (
                        source_type, source_ref, raw_label, normalized_label,
                        candidate_skill_ids_json, reason, evidence_json, state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
                    """,
                    (
                        request.source_type.value,
                        request.source_ref,
                        request.raw_label,
                        normalized_label,
                        payload,
                        reason,
                        evidence,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE skill_resolution_conflicts
                    SET raw_label = ?, candidate_skill_ids_json = ?, reason = ?,
                        evidence_json = ?, updated_at = datetime('now','localtime')
                    WHERE id = ?
                    """,
                    (request.raw_label, payload, reason, evidence, int(existing["id"])),
                )
        return SkillResolution(
            ResolutionOutcome.CONFLICT,
            None,
            0.0,
            reason,
            candidates,
        )


def _active_targets(skills: dict[int, dict], skill_ids: Iterable[int]) -> list[int]:
    result: list[int] = []
    for skill_id in skill_ids:
        target = _active_skill_id(skills, int(skill_id))
        if target is not None and target not in result:
            result.append(target)
    return result


def _active_skill_id(skills: dict[int, dict], skill_id: int) -> int | None:
    current_id = int(skill_id)
    visited: set[int] = set()
    while True:
        if current_id in visited:
            return None
        visited.add(current_id)
        row = skills.get(current_id)
        if row is None:
            return None
        if row["status"] == "active":
            return current_id
        if row["status"] != "merged" or row.get("redirect_skill_id") is None:
            return None
        current_id = int(row["redirect_skill_id"])


def _json_text_list(value: object) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [normalize_display_text(item) for item in parsed if normalize_display_text(item)]
