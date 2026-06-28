from __future__ import annotations

import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from question_bank.database.schema import connect, initialize_database
from question_bank.taxonomy.skill_catalog_seed import (
    BuiltinSkillCatalog,
    load_builtin_catalog,
    validate_builtin_catalog,
)
from question_bank.models.skill_catalog import normalize_display_text, normalize_stable_key


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

    def list_open_conflicts(self, *, limit: int = 100) -> list[dict[str, Any]]:
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM skill_resolution_conflicts
                WHERE state = 'open'
                ORDER BY created_at, id
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                candidate_ids = _json_int_list(item.pop("candidate_skill_ids_json", "[]"))[:5]
                item["evidence"] = _json_dict(item.pop("evidence_json", "{}"))
                item["candidates"] = []
                if candidate_ids:
                    placeholders = ",".join("?" for _ in candidate_ids)
                    candidates = conn.execute(
                        f"""
                        SELECT s.id, s.name, t.name AS topic_name
                        FROM skills s JOIN skill_topics t ON t.id = s.topic_id
                        WHERE s.id IN ({placeholders}) AND s.status = 'active'
                        """,
                        candidate_ids,
                    ).fetchall()
                    by_id = {int(candidate["id"]): dict(candidate) for candidate in candidates}
                    item["candidates"] = [by_id[value] for value in candidate_ids if value in by_id]
                result.append(item)
            return result

    def resolve_conflict(self, conflict_id: int, skill_id: int, *, actor: str) -> None:
        initialize_database(self.db_path)
        _require_actor(actor)
        with connect(self.db_path) as conn:
            self._resolve_conflict_connection(conn, int(conflict_id), int(skill_id), actor=str(actor))

    def create_local_from_conflict(
        self,
        conflict_id: int,
        name: str,
        topic_id: int,
        *,
        actor: str,
    ) -> int:
        initialize_database(self.db_path)
        actor = _require_actor(actor)
        normalized_name = normalize_display_text(name)
        if len(normalized_name) < 3:
            raise ValueError("local skill name must contain at least 3 characters")
        with connect(self.db_path) as conn:
            topic = conn.execute(
                "SELECT stable_key FROM skill_topics WHERE id = ? AND status = 'active'",
                (int(topic_id),),
            ).fetchone()
            if topic is None:
                raise KeyError(f"active topic not found: {topic_id}")
            existing = conn.execute(
                "SELECT id FROM skills WHERE name = ? AND topic_id = ? AND status = 'active'",
                (normalized_name, int(topic_id)),
            ).fetchone()
            if existing is None:
                digest = hashlib.sha1(
                    f"{topic['stable_key']}:{normalize_stable_key(normalized_name)}".encode("utf-8")
                ).hexdigest()[:12]
                cursor = conn.execute(
                    """
                    INSERT INTO skills (
                        stable_key, topic_id, name, aliases_json, origin, status
                    ) VALUES (?, ?, ?, '[]', 'local', 'active')
                    """,
                    (f"local.{topic['stable_key']}.{digest}", int(topic_id), normalized_name),
                )
                local_id = int(cursor.lastrowid)
            else:
                local_id = int(existing["id"])
            self._resolve_conflict_connection(conn, int(conflict_id), local_id, actor=actor)
            return local_id

    def ignore_conflict(self, conflict_id: int, *, actor: str) -> None:
        initialize_database(self.db_path)
        actor = _require_actor(actor)
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE skill_resolution_conflicts
                SET state = 'ignored', resolved_by = ?, resolved_at = datetime('now','localtime'),
                    updated_at = datetime('now','localtime')
                WHERE id = ? AND state = 'open'
                """,
                (actor, int(conflict_id)),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"open conflict not found: {conflict_id}")

    def list_neighbors(self, *, include_disabled: bool = False) -> list[dict[str, Any]]:
        initialize_database(self.db_path)
        where = "" if include_disabled else "WHERE n.enabled = 1"
        with connect(self.db_path) as conn:
            return [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT n.*, source.name AS source_skill_name, target.name AS target_skill_name
                    FROM skill_neighbors n
                    JOIN skills source ON source.id = n.source_skill_id
                    JOIN skills target ON target.id = n.target_skill_id
                    {where}
                    ORDER BY n.enabled DESC, source.name, target.name, n.id
                    """
                ).fetchall()
            ]

    def set_neighbor_enabled(self, neighbor_id: int, enabled: bool, *, actor: str) -> None:
        initialize_database(self.db_path)
        actor = _require_actor(actor)
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT evidence_json FROM skill_neighbors WHERE id = ?",
                (int(neighbor_id),),
            ).fetchone()
            if row is None:
                raise KeyError(f"skill neighbor not found: {neighbor_id}")
            evidence = _json_dict(row["evidence_json"])
            evidence["last_admin_action"] = {"actor": actor, "enabled": bool(enabled)}
            conn.execute(
                """
                UPDATE skill_neighbors
                SET enabled = ?, evidence_json = ?, updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                (int(bool(enabled)), json.dumps(evidence, ensure_ascii=False), int(neighbor_id)),
            )

    def coverage_summary(self) -> dict[str, dict[str, int]]:
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            question_total = int(
                conn.execute("SELECT COUNT(*) FROM questions WHERE COALESCE(is_deleted, 0) = 0").fetchone()[0]
            )
            question_resolved = int(
                conn.execute(
                    """
                    SELECT COUNT(DISTINCT question_id) FROM question_skill_links
                    WHERE status = 'resolved' AND role = 'measured'
                    """
                ).fetchone()[0]
            )
            question_conflicts = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM skill_resolution_conflicts
                    WHERE state = 'open' AND source_type = 'question_bank_item'
                    """
                ).fetchone()[0]
            )
            assessment_resolved = int(
                conn.execute(
                    """
                    SELECT COUNT(DISTINCT grading_session_id || ':' || source_question_id)
                    FROM assessment_item_skills
                    WHERE status = 'resolved' AND role = 'measured'
                    """
                ).fetchone()[0]
            )
            assessment_conflicts = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM skill_resolution_conflicts
                    WHERE state = 'open' AND source_type = 'assessment_item'
                    """
                ).fetchone()[0]
            )
        return {
            "question_bank": {
                "total": max(question_total, question_resolved + question_conflicts),
                "resolved": question_resolved,
                "conflicts": question_conflicts,
            },
            "assessment": {
                "total": assessment_resolved + assessment_conflicts,
                "resolved": assessment_resolved,
                "conflicts": assessment_conflicts,
            },
        }

    def list_migration_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM skill_migration_runs ORDER BY started_at DESC, id DESC LIMIT ?",
                    (max(1, int(limit)),),
                ).fetchall()
            ]

    def get_read_mode(self) -> str:
        initialize_database(self.db_path)
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT value FROM skill_system_settings WHERE key = 'recommendation_read_mode'"
            ).fetchone()
        return str(row["value"] if row is not None else "legacy")

    def record_shadow_comparison(
        self,
        batch_id: str,
        comparison: dict[str, Any],
        *,
        actor: str,
    ) -> None:
        initialize_database(self.db_path)
        actor = _require_actor(actor)
        with connect(self.db_path) as conn:
            mode_row = conn.execute(
                "SELECT value FROM skill_system_settings WHERE key = 'recommendation_read_mode'"
            ).fetchone()
            if mode_row is None or str(mode_row["value"]) != "shadow":
                raise ValueError("shadow comparison can only be recorded in shadow mode")
            applied = conn.execute(
                """
                SELECT id FROM skill_migration_runs
                WHERE batch_id = ? AND mode = 'apply' AND status = 'succeeded'
                """,
                (str(batch_id),),
            ).fetchone()
            if applied is None:
                raise ValueError("shadow comparison requires a successful applied batch")
            payload = {
                **dict(comparison),
                "batch_id": str(batch_id),
                "actor": actor,
                "recorded_at": datetime.now().isoformat(timespec="seconds"),
            }
            conn.execute(
                """
                INSERT INTO skill_system_settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value, updated_at = datetime('now','localtime')
                """,
                (f"shadow_comparison:{batch_id}", json.dumps(payload, ensure_ascii=False)),
            )

    def set_read_mode(
        self,
        mode: str,
        *,
        actor: str,
        reason: str,
        batch_id: str | None = None,
    ) -> None:
        initialize_database(self.db_path)
        target = str(mode or "").strip()
        actor = _require_actor(actor)
        reason = normalize_display_text(reason)
        if not reason:
            raise ValueError("reason is required")
        if target not in {"legacy", "shadow", "skill"}:
            raise ValueError(f"unsupported recommendation read mode: {target}")
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT value FROM skill_system_settings WHERE key = 'recommendation_read_mode'"
            ).fetchone()
            current = str(row["value"] if row is not None else "legacy")
            if current == target:
                return
            allowed = {("legacy", "shadow"), ("shadow", "skill"), ("skill", "legacy")}
            if (current, target) not in allowed:
                raise ValueError(f"unsupported read-mode transition: {current} -> {target}")
            gate_evidence: dict[str, Any] = {}
            if target == "skill":
                if not batch_id:
                    raise ValueError("skill mode requires an applied migration batch")
                gate_evidence = _skill_cutover_evidence(conn, str(batch_id))
            mode_change = {
                "from": current,
                "to": target,
                "actor": actor,
                "reason": reason,
                "batch_id": str(batch_id or ""),
                "changed_at": datetime.now().isoformat(timespec="seconds"),
                "gates": gate_evidence,
            }
            conn.execute(
                """
                INSERT INTO skill_system_settings (key, value)
                VALUES ('recommendation_read_mode', ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value, updated_at = datetime('now','localtime')
                """,
                (target,),
            )
            conn.execute(
                """
                INSERT INTO skill_migration_runs (
                    batch_id, mode, status, invariants_json, finished_at
                ) VALUES (?, 'set_mode', 'succeeded', ?, datetime('now','localtime'))
                """,
                (
                    f"set-mode-{target}-{datetime.now():%Y%m%d%H%M%S}-{uuid4().hex[:8]}",
                    json.dumps({"mode_change": mode_change}, ensure_ascii=False),
                ),
            )

    def _resolve_conflict_connection(self, conn, conflict_id: int, skill_id: int, *, actor: str) -> None:
        conflict = conn.execute(
            "SELECT * FROM skill_resolution_conflicts WHERE id = ? AND state = 'open'",
            (int(conflict_id),),
        ).fetchone()
        if conflict is None:
            raise KeyError(f"open conflict not found: {conflict_id}")
        active_skill_id = _redirect_target(conn, int(skill_id))
        skill = conn.execute(
            "SELECT id FROM skills WHERE id = ? AND status = 'active'",
            (active_skill_id,),
        ).fetchone()
        if skill is None:
            raise KeyError(f"active skill not found: {skill_id}")
        source_type = str(conflict["source_type"])
        source_ref = str(conflict["source_ref"])
        if source_type == "question_bank_item":
            question_id = _question_id_from_source_ref(source_ref)
            conn.execute(
                """
                INSERT INTO question_skill_links (
                    question_id, skill_id, role, raw_knowledge_label,
                    source, confidence, evidence_json, status
                ) VALUES (?, ?, 'measured', ?, 'admin_resolution', 1.0, ?, 'resolved')
                ON CONFLICT(question_id, skill_id, role) DO UPDATE SET
                    raw_knowledge_label = excluded.raw_knowledge_label,
                    source = excluded.source, confidence = 1.0,
                    evidence_json = excluded.evidence_json, status = 'resolved',
                    updated_at = datetime('now','localtime')
                """,
                (
                    question_id,
                    active_skill_id,
                    str(conflict["raw_label"]),
                    json.dumps({"conflict_id": conflict_id, "actor": actor}, ensure_ascii=False),
                ),
            )
        elif source_type == "assessment_item":
            session_id, item_ref = _assessment_source_ref(source_ref)
            conn.execute(
                """
                INSERT INTO assessment_item_skills (
                    grading_session_id, source_question_id, skill_id, role,
                    raw_knowledge_label, source, confidence, evidence_json, status
                ) VALUES (?, ?, ?, 'measured', ?, 'admin_resolution', 1.0, ?, 'resolved')
                ON CONFLICT(grading_session_id, source_question_id, skill_id, role) DO UPDATE SET
                    raw_knowledge_label = excluded.raw_knowledge_label,
                    source = excluded.source, confidence = 1.0,
                    evidence_json = excluded.evidence_json, status = 'resolved',
                    updated_at = datetime('now','localtime')
                """,
                (
                    session_id,
                    item_ref,
                    active_skill_id,
                    str(conflict["raw_label"]),
                    json.dumps({"conflict_id": conflict_id, "actor": actor}, ensure_ascii=False),
                ),
            )
        conn.execute(
            """
            UPDATE skill_resolution_conflicts
            SET state = 'resolved', resolved_skill_id = ?, resolved_by = ?,
                resolved_at = datetime('now','localtime'), updated_at = datetime('now','localtime')
            WHERE id = ?
            """,
            (active_skill_id, actor, int(conflict_id)),
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
              AND NOT (
                  name IS ? AND subject IS ?
                  AND grade_min IS ? AND grade_max IS ?
              )
            """,
            (
                topic.name,
                catalog.subject,
                topic.grade_min,
                topic.grade_max,
                topic.stable_key,
                topic.name,
                catalog.subject,
                topic.grade_min,
                topic.grade_max,
            ),
        )
    topic_ids = {
        str(row["stable_key"]): int(row["id"])
        for row in conn.execute("SELECT id, stable_key FROM skill_topics").fetchall()
    }
    for skill in catalog.skills:
        aliases_json = json.dumps(
            list(dict.fromkeys((*skill.aliases, *skill.legacy_keys))),
            ensure_ascii=False,
        )
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
                aliases_json,
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
              AND NOT (
                  topic_id IS ? AND name IS ? AND aliases_json IS ?
                  AND grade_min IS ? AND grade_max IS ?
              )
            """,
            (
                topic_ids[skill.topic_key],
                skill.name,
                aliases_json,
                skill.grade_min,
                skill.grade_max,
                skill.stable_key,
                topic_ids[skill.topic_key],
                skill.name,
                aliases_json,
                skill.grade_min,
                skill.grade_max,
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
              AND NOT (weight IS ? AND source = 'builtin')
            """,
            (
                neighbor.weight,
                skill_ids[neighbor.source_key],
                skill_ids[neighbor.target_key],
                neighbor.kind,
                neighbor.weight,
            ),
        )
    conn.execute(
        """
        INSERT INTO skill_system_settings (key, value)
        VALUES ('catalog_version', ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = datetime('now','localtime')
        WHERE skill_system_settings.value IS NOT excluded.value
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


def _require_actor(actor: object) -> str:
    value = normalize_display_text(actor)
    if not value:
        raise ValueError("actor is required")
    return value


def _json_int_list(value: object) -> list[int]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    result: list[int] = []
    for item in parsed if isinstance(parsed, list) else []:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in result:
            result.append(number)
    return result


def _json_dict(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _question_id_from_source_ref(source_ref: str) -> int:
    candidates = [source_ref, *reversed(source_ref.split(":"))]
    for value in candidates:
        try:
            question_id = int(value)
        except (TypeError, ValueError):
            continue
        if question_id > 0:
            return question_id
    raise ValueError(f"question source reference has no numeric id: {source_ref}")


def _assessment_source_ref(source_ref: str) -> tuple[str, str]:
    parts = str(source_ref).split(":", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"invalid assessment source reference: {source_ref}")
    return parts[0], parts[1]


def _skill_cutover_evidence(conn, batch_id: str) -> dict[str, Any]:
    run = conn.execute(
        """
        SELECT report_path FROM skill_migration_runs
        WHERE batch_id = ? AND mode = 'apply' AND status = 'succeeded'
        """,
        (batch_id,),
    ).fetchone()
    if run is None:
        raise ValueError("skill mode requires a successful applied migration batch")
    report_path = Path(str(run["report_path"] or ""))
    if not report_path.is_file():
        raise ValueError("applied migration report is unavailable")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("applied migration report is unreadable") from exc
    failures: list[str] = []
    if (
        str(report.get("batch_id") or "") != batch_id
        or str(report.get("mode") or "") != "apply"
        or str(report.get("status") or "") != "succeeded"
    ):
        failures.append("report identity")
    coverage = float(report.get("coverage", {}).get("value") or 0.0)
    gold = report.get("gold") or {}
    invariants_unchanged = bool(report.get("invariants", {}).get("unchanged"))
    insufficient = int(report.get("result_counts", {}).get("insufficient_evidence") or 0)
    if coverage < 0.95:
        failures.append("coverage")
    if int(gold.get("reviewed_count") or 0) < 100 or float(gold.get("precision") or 0.0) < 0.98:
        failures.append("gold precision")
    if not invariants_unchanged:
        failures.append("count invariants")
    if insufficient:
        failures.append("silent unknowns")
    comparison_row = conn.execute(
        "SELECT value FROM skill_system_settings WHERE key = ?",
        (f"shadow_comparison:{batch_id}",),
    ).fetchone()
    if comparison_row is None:
        raise ValueError("skill mode requires a completed shadow comparison")
    comparison = _json_dict(comparison_row["value"])
    comparison_failures = {
        key: int(comparison.get(key) or 0)
        for key in (
            "unexplained_exact_match_divergence",
            "topic_only_exact_count",
            "supporting_only_exact_count",
            "silent_shortage_fill_count",
        )
    }
    if int(comparison.get("evaluated_profiles") or 0) <= 0 or any(comparison_failures.values()):
        failures.append("shadow comparison")
    if failures:
        raise ValueError("skill mode gates failed: " + ", ".join(failures))
    return {
        "coverage": coverage,
        "gold_reviewed_count": int(gold["reviewed_count"]),
        "gold_precision": float(gold["precision"]),
        "invariants_unchanged": invariants_unchanged,
        "insufficient_evidence": insufficient,
        "shadow_comparison": comparison_failures,
    }
