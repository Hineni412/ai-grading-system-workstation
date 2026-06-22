from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from question_bank.database.schema import connect, initialize_database
from question_bank.taxonomy.skill_catalog_seed import (
    BuiltinSkillCatalog,
    load_builtin_catalog,
    validate_builtin_catalog,
)


class SkillCatalogService:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def seed_builtin_catalog(self) -> dict[str, int]:
        initialize_database(self.db_path, seed_skills=False)
        catalog = load_builtin_catalog()
        errors = validate_builtin_catalog(catalog)
        if errors:
            raise ValueError("invalid built-in skill catalog: " + "; ".join(errors))
        with connect(self.db_path) as conn:
            return seed_builtin_catalog_connection(conn, catalog)

    def list_topics(self) -> list[dict[str, Any]]:
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM skill_topics WHERE status = 'active' ORDER BY name, id"
                ).fetchall()
            ]

    def list_skills(
        self,
        *,
        topic_id: int | None = None,
        include_merged: bool = False,
    ) -> list[dict[str, Any]]:
        initialize_database(self.db_path)
        where = [] if include_merged else ["s.status = 'active'"]
        params: list[Any] = []
        if topic_id is not None:
            where.append("s.topic_id = ?")
            params.append(int(topic_id))
        sql = """
            SELECT s.*, t.name AS topic_name, t.stable_key AS topic_key
            FROM skills s
            JOIN skill_topics t ON t.id = s.topic_id
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY t.name, s.name, s.id"
        with connect(self.db_path) as conn:
            return [_skill_dict(row) for row in conn.execute(sql, params).fetchall()]

    def get_skill(self, skill_id: int, *, follow_redirect: bool = True) -> dict[str, Any] | None:
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            current_id = int(skill_id)
            visited: set[int] = set()
            while True:
                row = conn.execute(
                    """
                    SELECT s.*, t.name AS topic_name, t.stable_key AS topic_key
                    FROM skills s JOIN skill_topics t ON t.id = s.topic_id
                    WHERE s.id = ?
                    """,
                    (current_id,),
                ).fetchone()
                if row is None:
                    return None
                if not follow_redirect or row["status"] != "merged" or row["redirect_skill_id"] is None:
                    return _skill_dict(row)
                if current_id in visited:
                    raise ValueError(f"skill redirect cycle detected at {current_id}")
                visited.add(current_id)
                current_id = int(row["redirect_skill_id"])

    def find_by_stable_key(self, stable_key: str) -> dict[str, Any] | None:
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id FROM skills WHERE stable_key = ?",
                (str(stable_key).strip().casefold(),),
            ).fetchone()
        return self.get_skill(int(row["id"])) if row is not None else None

    def merge_skill(
        self,
        source_skill_id: int,
        target_skill_id: int,
        *,
        actor: str,
    ) -> None:
        initialize_database(self.db_path)
        source_id = int(source_skill_id)
        target_id = int(target_skill_id)
        if source_id == target_id:
            raise ValueError("skill redirect cycle: source and target are identical")
        if not str(actor or "").strip():
            raise ValueError("actor is required to merge skills")
        with connect(self.db_path) as conn:
            source = conn.execute(
                "SELECT id, status FROM skills WHERE id = ?",
                (source_id,),
            ).fetchone()
            if source is None:
                raise KeyError(f"skill not found: {source_id}")
            if source["status"] == "merged":
                raise ValueError(f"skill is already merged: {source_id}")
            resolved_target = _redirect_target(conn, target_id)
            if resolved_target == source_id:
                raise ValueError("skill redirect cycle detected")
            conn.execute(
                """
                UPDATE skills
                SET status = 'merged', redirect_skill_id = ?,
                    updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                (resolved_target, source_id),
            )
            conn.execute(
                """
                UPDATE skill_neighbors
                SET enabled = 0, updated_at = datetime('now','localtime')
                WHERE (source_skill_id = ? AND target_skill_id = ?)
                   OR (source_skill_id = ? AND target_skill_id = ?)
                """,
                (source_id, resolved_target, resolved_target, source_id),
            )


def seed_builtin_catalog_connection(conn, catalog: BuiltinSkillCatalog) -> dict[str, int]:
    topics_created = 0
    skills_created = 0
    neighbors_created = 0
    for topic in catalog.topics:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO skill_topics (
                stable_key, name, subject, grade_min, grade_max, status
            ) VALUES (?, ?, ?, ?, ?, 'active')
            """,
            (
                topic.stable_key,
                topic.name,
                catalog.subject,
                topic.grade_min,
                topic.grade_max,
            ),
        )
        topics_created += int(cursor.rowcount > 0)
        conn.execute(
            """
            UPDATE skill_topics
            SET name = ?, subject = ?, grade_min = ?, grade_max = ?,
                updated_at = datetime('now','localtime')
            WHERE stable_key = ?
            """,
            (
                topic.name,
                catalog.subject,
                topic.grade_min,
                topic.grade_max,
                topic.stable_key,
            ),
        )
    topic_ids = {
        str(row["stable_key"]): int(row["id"])
        for row in conn.execute("SELECT id, stable_key FROM skill_topics").fetchall()
    }
    for skill in catalog.skills:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO skills (
                stable_key, topic_id, name, aliases_json, grade_min, grade_max, origin
            ) VALUES (?, ?, ?, ?, ?, ?, 'builtin')
            """,
            (
                skill.stable_key,
                topic_ids[skill.topic_key],
                skill.name,
                json.dumps(list(skill.aliases), ensure_ascii=False),
                skill.grade_min,
                skill.grade_max,
            ),
        )
        skills_created += int(cursor.rowcount > 0)
        conn.execute(
            """
            UPDATE skills
            SET topic_id = ?, name = ?, aliases_json = ?, grade_min = ?, grade_max = ?,
                updated_at = datetime('now','localtime')
            WHERE stable_key = ? AND origin = 'builtin' AND status = 'active'
            """,
            (
                topic_ids[skill.topic_key],
                skill.name,
                json.dumps(list(skill.aliases), ensure_ascii=False),
                skill.grade_min,
                skill.grade_max,
                skill.stable_key,
            ),
        )
    skill_ids = {
        str(row["stable_key"]): int(row["id"])
        for row in conn.execute("SELECT id, stable_key FROM skills").fetchall()
    }
    for neighbor in catalog.neighbors:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO skill_neighbors (
                source_skill_id, target_skill_id, kind, weight, source
            ) VALUES (?, ?, ?, ?, 'builtin')
            """,
            (
                skill_ids[neighbor.source_key],
                skill_ids[neighbor.target_key],
                neighbor.kind,
                neighbor.weight,
            ),
        )
        neighbors_created += int(cursor.rowcount > 0)
        conn.execute(
            """
            UPDATE skill_neighbors
            SET weight = ?, source = 'builtin', updated_at = datetime('now','localtime')
            WHERE source_skill_id = ? AND target_skill_id = ? AND kind = ?
            """,
            (
                neighbor.weight,
                skill_ids[neighbor.source_key],
                skill_ids[neighbor.target_key],
                neighbor.kind,
            ),
        )
    conn.execute(
        """
        INSERT INTO skill_system_settings (key, value)
        VALUES ('catalog_version', ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = datetime('now','localtime')
        """,
        (catalog.version,),
    )
    return {
        "topics_created": topics_created,
        "skills_created": skills_created,
        "neighbors_created": neighbors_created,
    }


def _skill_dict(row) -> dict[str, Any]:
    result = dict(row)
    try:
        result["aliases"] = json.loads(result.pop("aliases_json"))
    except (TypeError, ValueError, json.JSONDecodeError):
        result["aliases"] = []
    return result


def _redirect_target(conn, skill_id: int) -> int:
    current_id = int(skill_id)
    visited: set[int] = set()
    while True:
        if current_id in visited:
            raise ValueError("skill redirect cycle detected")
        visited.add(current_id)
        row = conn.execute(
            "SELECT status, redirect_skill_id FROM skills WHERE id = ?",
            (current_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"skill not found: {current_id}")
        if row["status"] != "merged" or row["redirect_skill_id"] is None:
            return current_id
        current_id = int(row["redirect_skill_id"])
