from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from question_bank.database.schema import connect, initialize_database
from question_bank.models.skill_catalog import ResolvedSkillLink, SkillRole


class SkillLinkService:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def replace_assessment_links(
        self,
        grading_session_id: str,
        source_question_id: str,
        links: Sequence[ResolvedSkillLink],
    ) -> None:
        normalized = _validated_links(links)
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            _require_skills(conn, normalized)
            conn.execute(
                """
                DELETE FROM assessment_item_skills
                WHERE grading_session_id = ? AND source_question_id = ?
                """,
                (str(grading_session_id), str(source_question_id)),
            )
            conn.executemany(
                """
                INSERT INTO assessment_item_skills (
                    grading_session_id, source_question_id, skill_id, role,
                    raw_knowledge_id, raw_knowledge_label, source, confidence,
                    evidence_json, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'resolved')
                """,
                [
                    (
                        str(grading_session_id),
                        str(source_question_id),
                        link.skill_id,
                        link.role.value,
                        link.raw_knowledge_id or None,
                        link.raw_knowledge_label or None,
                        link.source,
                        link.confidence,
                        json.dumps(dict(link.evidence), ensure_ascii=False),
                    )
                    for link in normalized
                ],
            )

    def clear_assessment_links(
        self,
        grading_session_id: str,
        source_question_id: str,
    ) -> None:
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            conn.execute(
                """
                DELETE FROM assessment_item_skills
                WHERE grading_session_id = ? AND source_question_id = ?
                """,
                (str(grading_session_id), str(source_question_id)),
            )

    def resolve_rubric(
        self,
        grading_session_id: str,
        payload,
        *,
        resolver=None,
        preserve_existing_measured: bool = False,
    ) -> dict[str, int]:
        from question_bank.models.skill_catalog import (
            ResolvedSkillLink,
            ResolutionOutcome,
            SkillRole,
        )
        from question_bank.services.skill_resolution_service import SkillResolutionService
        from session_manager import iter_rubric_skill_requests

        active_resolver = resolver or SkillResolutionService(self.db_path)
        grouped: dict[str, list[tuple[SkillRole, Any]]] = {}
        for item_ref, role, request in iter_rubric_skill_requests(
            payload,
            grading_session_id=str(grading_session_id),
        ):
            grouped.setdefault(item_ref, []).append((role, request))
        resolved_items = 0
        conflicts = 0
        skill_count = 0
        for item_ref, requests in grouped.items():
            if preserve_existing_measured:
                with connect(self.db_path) as conn:
                    existing_count = int(
                        conn.execute(
                            """
                            SELECT COUNT(*) FROM assessment_item_skills
                            WHERE grading_session_id = ? AND source_question_id = ?
                              AND role = 'measured' AND status = 'resolved'
                            """,
                            (str(grading_session_id), item_ref),
                        ).fetchone()[0]
                    )
                if existing_count:
                    resolved_items += 1
                    skill_count += existing_count
                    continue
            links: list[ResolvedSkillLink] = []
            identities: set[tuple[int, str]] = set()
            for role, request in requests:
                resolution = active_resolver.resolve(request)
                if resolution.outcome is ResolutionOutcome.CONFLICT or resolution.skill_id is None:
                    conflicts += 1
                    continue
                identity = (int(resolution.skill_id), role.value)
                if identity in identities:
                    continue
                identities.add(identity)
                links.append(
                    ResolvedSkillLink(
                        skill_id=int(resolution.skill_id),
                        role=role,
                        raw_knowledge_id=request.stable_key_hint,
                        raw_knowledge_label=request.raw_label,
                        source="rubric",
                        confidence=resolution.confidence,
                        evidence={
                            "grading_session_id": str(grading_session_id),
                            "source_question_id": item_ref,
                            "resolution_reason": resolution.reason,
                            "raw_knowledge_id": request.stable_key_hint,
                        },
                    )
                )
            if any(link.role is SkillRole.MEASURED for link in links):
                self.replace_assessment_links(str(grading_session_id), item_ref, links)
                resolved_items += 1
                skill_count += len(links)
            else:
                self.clear_assessment_links(str(grading_session_id), item_ref)
        return {
            "items": len(grouped),
            "resolved_items": resolved_items,
            "conflicts": conflicts,
            "skills": skill_count,
        }

    def replace_question_links(
        self,
        question_id: int,
        links: Sequence[ResolvedSkillLink],
    ) -> None:
        normalized = _validated_links(links)
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            _require_skills(conn, normalized)
            conn.execute(
                "DELETE FROM question_skill_links WHERE question_id = ?",
                (int(question_id),),
            )
            conn.executemany(
                """
                INSERT INTO question_skill_links (
                    question_id, skill_id, role, raw_knowledge_id,
                    raw_knowledge_label, source, confidence, evidence_json, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'resolved')
                """,
                [
                    (
                        int(question_id),
                        link.skill_id,
                        link.role.value,
                        link.raw_knowledge_id or None,
                        link.raw_knowledge_label or None,
                        link.source,
                        link.confidence,
                        json.dumps(dict(link.evidence), ensure_ascii=False),
                    )
                    for link in normalized
                ],
            )

    def clear_question_links(self, question_id: int) -> None:
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM question_skill_links WHERE question_id = ?",
                (int(question_id),),
            )

    def assessment_links_for_sessions(
        self,
        session_ids: Sequence[str],
    ) -> list[dict[str, Any]]:
        if not session_ids:
            return []
        initialize_database(self.db_path)
        placeholders = ",".join("?" for _ in session_ids)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM assessment_item_skills
                WHERE grading_session_id IN ({placeholders}) AND status = 'resolved'
                ORDER BY grading_session_id, source_question_id, role, id
                """,
                [str(value) for value in session_ids],
            ).fetchall()
            return [_resolved_link_dict(conn, row) for row in rows]

    def question_links_for_skills(
        self,
        skill_ids: Sequence[int],
        *,
        role: SkillRole | None = None,
    ) -> list[dict[str, Any]]:
        if not skill_ids:
            return []
        initialize_database(self.db_path)
        normalized_role = role if isinstance(role, SkillRole) or role is None else SkillRole(str(role))
        placeholders = ",".join("?" for _ in skill_ids)
        params: list[Any] = [int(value) for value in skill_ids]
        sql = f"""
            SELECT * FROM question_skill_links
            WHERE skill_id IN ({placeholders}) AND status = 'resolved'
        """
        if normalized_role is not None:
            sql += " AND role = ?"
            params.append(normalized_role.value)
        sql += " ORDER BY question_id, role, id"
        with connect(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
            return [_resolved_link_dict(conn, row) for row in rows]


def _validated_links(links: Sequence[ResolvedSkillLink]) -> tuple[ResolvedSkillLink, ...]:
    normalized = tuple(
        link if isinstance(link, ResolvedSkillLink) else ResolvedSkillLink(**dict(link))
        for link in links
    )
    if not normalized or not any(link.role is SkillRole.MEASURED for link in normalized):
        raise ValueError("resolved skill links require at least one measured skill")
    identities = {(link.skill_id, link.role.value) for link in normalized}
    if len(identities) != len(normalized):
        raise ValueError("duplicate skill link identity")
    return normalized


def _require_skills(conn, links: Sequence[ResolvedSkillLink]) -> None:
    requested = {link.skill_id for link in links}
    placeholders = ",".join("?" for _ in requested)
    rows = conn.execute(
        f"SELECT id FROM skills WHERE id IN ({placeholders}) AND status IN ('active', 'merged')",
        sorted(requested),
    ).fetchall()
    found = {int(row["id"]) for row in rows}
    missing = sorted(requested - found)
    if missing:
        raise KeyError(f"skill not found or unavailable: {missing}")


def _resolved_link_dict(conn, row) -> dict[str, Any]:
    result = dict(row)
    stored_skill_id = int(result["skill_id"])
    active = _active_skill_row(conn, stored_skill_id)
    result["stored_skill_id"] = stored_skill_id
    result["skill_id"] = int(active["id"])
    result["skill_name"] = str(active["name"])
    result["topic_id"] = int(active["topic_id"])
    result["topic_name"] = str(active["topic_name"])
    try:
        result["evidence"] = json.loads(result.pop("evidence_json"))
    except (TypeError, ValueError, json.JSONDecodeError):
        result["evidence"] = {}
    return result


def _active_skill_row(conn, skill_id: int):
    current_id = int(skill_id)
    visited: set[int] = set()
    while True:
        if current_id in visited:
            raise ValueError(f"skill redirect cycle detected at {current_id}")
        visited.add(current_id)
        row = conn.execute(
            """
            SELECT s.*, t.name AS topic_name
            FROM skills s JOIN skill_topics t ON t.id = s.topic_id
            WHERE s.id = ?
            """,
            (current_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"skill not found: {current_id}")
        if row["status"] == "active":
            return row
        if row["status"] != "merged" or row["redirect_skill_id"] is None:
            raise KeyError(f"skill is not active: {current_id}")
        current_id = int(row["redirect_skill_id"])
