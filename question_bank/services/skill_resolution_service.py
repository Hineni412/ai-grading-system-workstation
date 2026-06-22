from __future__ import annotations

import json
import hashlib
import re
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
                """
                SELECT s.*, t.name AS topic_name, t.stable_key AS topic_key,
                       t.status AS topic_status
                FROM skills s
                JOIN skill_topics t ON t.id = s.topic_id
                WHERE s.status IN ('active', 'merged')
                ORDER BY s.id
                """
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
            if self.context_ranker is not None:
                pool = _context_candidate_pool(skills, request)
                try:
                    ranking = self.context_ranker.rank(request, pool)
                except Exception as exc:
                    return self._conflict(
                        conn,
                        request,
                        normalized_label,
                        "上下文识别服务不可用，已保留为待处理问题",
                        (),
                        persist=persist_conflict,
                        extra_evidence={"ranker_error": type(exc).__name__},
                    )
                pool_ids = {int(item["id"]) for item in pool}
                ranked = tuple(
                    item
                    for item in ranking.candidates
                    if item.skill_id in pool_ids
                    and _active_skill_id(skills, item.skill_id) == item.skill_id
                )
                if ranking.ambiguity_flags:
                    return self._conflict(
                        conn,
                        request,
                        normalized_label,
                        "上下文仍有歧义，不能自动绑定",
                        ranked,
                        persist=persist_conflict,
                        extra_evidence={"ambiguity_flags": list(ranking.ambiguity_flags)},
                    )
                if ranked:
                    first = ranked[0]
                    second_score = ranked[1].confidence if len(ranked) > 1 else 0.0
                    margin = round(first.confidence - second_score, 4)
                    if (
                        first.confidence >= self.AUTO_ACCEPT_MIN
                        and margin >= self.AUTO_ACCEPT_MARGIN
                    ):
                        return SkillResolution(
                            ResolutionOutcome.RESOLVED_EXISTING,
                            first.skill_id,
                            first.confidence,
                            "上下文候选达到自动通过条件",
                            ranked,
                        )
                    return self._conflict(
                        conn,
                        request,
                        normalized_label,
                        "上下文候选未达到自动通过阈值",
                        ranked,
                        persist=persist_conflict,
                        extra_evidence={
                            "first_score": first.confidence,
                            "second_score": second_score,
                            "margin": margin,
                        },
                    )
                local = self._create_local_if_allowed(conn, request, skills, ranking)
                if local is not None:
                    return local
                return self._conflict(
                    conn,
                    request,
                    normalized_label,
                    "本校技能建议未达到自动创建条件",
                    (),
                    persist=persist_conflict,
                    extra_evidence={
                        "proposed_local_name": ranking.proposed_local_name,
                        "proposed_topic_key": ranking.proposed_topic_key,
                        "local_confidence": ranking.local_confidence,
                    },
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
        extra_evidence: dict | None = None,
    ) -> SkillResolution:
        candidates = tuple(
            item
            if isinstance(item, RankedSkillCandidate)
            else RankedSkillCandidate(int(item), 0.0, reason)
            for item in candidate_ids
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
            evidence_payload = {
                "grade": request.grade,
                "topic_hint": request.topic_hint,
                "stable_key_hint": request.stable_key_hint,
                "existing_tags": list(request.existing_tags),
                "candidate_scores": [
                    {"skill_id": item.skill_id, "confidence": item.confidence}
                    for item in candidates
                ],
                "auto_accept_min": self.AUTO_ACCEPT_MIN,
                "auto_accept_margin": self.AUTO_ACCEPT_MARGIN,
            }
            evidence_payload.update(extra_evidence or {})
            evidence = json.dumps(evidence_payload, ensure_ascii=False)
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

    def _create_local_if_allowed(self, conn, request, skills, ranking) -> SkillResolution | None:
        name = normalize_display_text(ranking.proposed_local_name)
        if (
            not name
            or name in _BROAD_LABELS
            or len(name) < 3
            or ranking.local_confidence < self.AUTO_ACCEPT_MIN
            or not ranking.proposed_topic_key
            or not (request.question_text or request.rubric_text or request.answer_text)
        ):
            return None
        topic = conn.execute(
            """
            SELECT * FROM skill_topics
            WHERE stable_key = ? AND status = 'active'
            """,
            (ranking.proposed_topic_key,),
        ).fetchone()
        if topic is None or not _topic_matches_request(dict(topic), request):
            return None
        proposed_labels = {
            normalize_stable_key(value)
            for value in (name, *ranking.proposed_aliases)
            if normalize_stable_key(value)
        }
        for row in skills.values():
            existing_labels = {
                normalize_stable_key(value)
                for value in (row["name"], *_json_text_list(row.get("aliases_json")))
            }
            if proposed_labels.intersection(existing_labels):
                return None
        digest = hashlib.sha256(normalize_stable_key(name).encode("utf-8")).hexdigest()[:12]
        topic_slug = str(topic["stable_key"]).split("math.", 1)[-1].replace(".", "_")
        stable_key = f"local.math.{topic_slug}.{digest}"
        grade = _grade_number(request.grade)
        grade_min = grade or int(topic["grade_min"] or 7)
        grade_max = grade or int(topic["grade_max"] or 9)
        conn.execute(
            """
            INSERT OR IGNORE INTO skills (
                stable_key, topic_id, name, aliases_json, grade_min, grade_max, origin
            ) VALUES (?, ?, ?, ?, ?, ?, 'local')
            """,
            (
                stable_key,
                int(topic["id"]),
                name,
                json.dumps(list(ranking.proposed_aliases), ensure_ascii=False),
                grade_min,
                grade_max,
            ),
        )
        row = conn.execute(
            "SELECT id FROM skills WHERE stable_key = ?",
            (stable_key,),
        ).fetchone()
        return SkillResolution(
            ResolutionOutcome.CREATED_LOCAL,
            int(row["id"]),
            ranking.local_confidence,
            "创建并绑定具体本校技能",
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


def _context_candidate_pool(
    skills: dict[int, dict],
    request: SkillResolutionRequest,
) -> list[dict]:
    grade = _grade_number(request.grade)
    topic_hint = normalize_stable_key(request.topic_hint)
    result: list[dict] = []
    for row in skills.values():
        if row["status"] != "active" or row.get("topic_status") != "active":
            continue
        if grade is not None:
            grade_min = int(row.get("grade_min") or 1)
            grade_max = int(row.get("grade_max") or 12)
            if not grade_min <= grade <= grade_max:
                continue
        if topic_hint:
            topic_values = {
                normalize_stable_key(row.get("topic_key")),
                normalize_stable_key(row.get("topic_name")),
            }
            if topic_hint not in topic_values:
                continue
        result.append(row)
    return result


def _topic_matches_request(topic: dict, request: SkillResolutionRequest) -> bool:
    if not request.topic_hint:
        return True
    hint = normalize_stable_key(request.topic_hint)
    return hint in {
        normalize_stable_key(topic.get("stable_key")),
        normalize_stable_key(topic.get("name")),
    }


def _grade_number(value: object) -> int | None:
    text = normalize_display_text(value)
    digit = re.search(r"(?:^|\D)([7-9])(?:\D|$)", text)
    if digit:
        return int(digit.group(1))
    for token, grade in (("七", 7), ("八", 8), ("九", 9)):
        if token in text:
            return grade
    return None
