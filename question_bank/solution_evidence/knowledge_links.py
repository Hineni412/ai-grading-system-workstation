"""Single read entry for evidence-point knowledge links.

The immutable evidence JSON remains the factual source for point text; link
membership lives in ``evidence_point_knowledge_links`` so reads do not depend
on embedded ``fine_term_links``. Embedded links still arriving with newly
written evidence are projected into the table at write time so this stays the
only read path.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from question_bank.solution_evidence.part_assessments import reading

LINK_JOB_KIND = "link_job"
MIGRATED_KIND = "migrated_from_embedded"
TEACHER_KIND = "teacher"
_LINK_ROLES = ("direct", "supporting_prerequisite")
_RELEASE_REASON_PREFIX = "current_release:"


@dataclass(frozen=True, slots=True)
class KnowledgeLink:
    term_id: str
    stable_key: str
    role: str
    weight: float
    resolution_status: str = "resolved"
    source_kind: str = ""
    graph_release_id: str = ""


def load_point_links(
    db_path: Path,
    evidence_version_ids: Sequence[str],
    graph_release_id: str | None,
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, dict[str, tuple[KnowledgeLink, ...]]]:
    """Load links grouped ``{version_id: {point_id: (KnowledgeLink, ...)}}``.

    Each version resolves to one release's rows: the requested release (or the
    active release when omitted) when it has rows, otherwise the latest rows, so
    links migrated under an older release keep working after activation moves
    on. Within a (version, point), ``link_job`` rows take precedence over any
    other source.
    """
    ids = [
        str(value)
        for value in dict.fromkeys(evidence_version_ids)
        if str(value or "").strip()
    ]
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    with reading(Path(db_path), connection) as conn:
        rows = conn.execute(
            f"""
            SELECT evidence_version_id, evidence_point_id, graph_release_id,
                   role, term_id, stable_key, resolution_status, weight,
                   source_kind, created_at
            FROM evidence_point_knowledge_links
            WHERE evidence_version_id IN ({marks})
            """,
            ids,
        ).fetchall()
        active_release = _active_release_id(conn)
    by_version: dict[str, dict[str, list[sqlite3.Row]]] = {}
    for row in rows:
        by_version.setdefault(str(row["evidence_version_id"]), {}).setdefault(
            str(row["graph_release_id"]), []
        ).append(row)
    wanted = str(graph_release_id or active_release or "").strip()
    result: dict[str, dict[str, tuple[KnowledgeLink, ...]]] = {}
    for version_id, release_groups in by_version.items():
        if wanted and wanted in release_groups:
            chosen = release_groups[wanted]
        else:
            chosen = max(
                release_groups.values(),
                key=lambda group: (
                    max(str(row["created_at"]) for row in group),
                    len(group),
                ),
            )
        raw_points: dict[str, list[sqlite3.Row]] = {}
        for row in chosen:
            raw_points.setdefault(str(row["evidence_point_id"]), []).append(row)
        points: dict[str, tuple[KnowledgeLink, ...]] = {}
        for point_id, point_rows in raw_points.items():
            job_rows = [
                row for row in point_rows if str(row["source_kind"]) == LINK_JOB_KIND
            ]
            points[point_id] = tuple(
                KnowledgeLink(
                    term_id=str(row["term_id"]),
                    stable_key=str(row["stable_key"] or ""),
                    role=str(row["role"]),
                    weight=float(row["weight"]),
                    resolution_status=str(row["resolution_status"]),
                    source_kind=str(row["source_kind"]),
                    graph_release_id=str(row["graph_release_id"]),
                )
                for row in (job_rows or point_rows)
            )
        result[version_id] = points
    return result


def direct_links_for_part(
    part: Mapping[str, Any],
    links: Mapping[str, Sequence[KnowledgeLink]],
) -> tuple[KnowledgeLink, ...]:
    """Resolved ``direct`` links across a part's evidence points, in order."""
    result: list[KnowledgeLink] = []
    seen: set[tuple[str, str]] = set()
    for point in part.get("evidence_points", []) or []:
        point_id = str(point.get("evidence_point_id") or "")
        for link in links.get(point_id, ()):
            if link.role != "direct" or link.resolution_status != "resolved":
                continue
            if not link.stable_key or link.stable_key in seen:
                continue
            seen.add(link.stable_key)
            result.append(link)
    return tuple(result)


def direct_targets_for_part(
    part: Mapping[str, Any],
    links: Mapping[str, Sequence[KnowledgeLink]],
) -> tuple[str, ...]:
    """Stable keys of a part's resolved direct links."""
    return tuple(link.stable_key for link in direct_links_for_part(part, links))


def skill_parent_targets(
    conn: sqlite3.Connection,
    skill_keys: Sequence[str],
    preferred_release_id: str | None = None,
) -> dict[str, str]:
    """Map ``sk_*`` keys to their ``parent`` relation target section keys,
    preferring relations from the given graph release."""
    keys = [key for key in dict.fromkeys(skill_keys) if str(key or "").strip()]
    if not keys:
        return {}
    marks = ",".join("?" for _ in keys)
    rows = conn.execute(
        f"""
        SELECT source_key, target_key, release_id
        FROM knowledge_graph_release_relations
        WHERE relation_type = 'parent'
          AND source_key IN ({marks})
        """,
        keys,
    ).fetchall()
    preferred = str(preferred_release_id or "").strip()
    rows.sort(
        key=lambda row: 0
        if str(row["release_id"] or "") == preferred
        else 1
    )
    result: dict[str, str] = {}
    for row in rows:
        result.setdefault(str(row["source_key"]), str(row["target_key"]))
    return result


def resolve_anchor_keys(
    conn: sqlite3.Connection,
    keys: Sequence[str],
    *,
    preferred_release_id: str | None = None,
) -> dict[str, list[str]]:
    """Climb each stable key to its curriculum section/chapter anchor.

    ``sk_*`` keys resolve through graph-release ``parent`` relations; ``kp_*``
    leaves/sections climb the bundled curriculum catalog.  Returns
    ``{"sections": [...], "chapters": [...]}`` (ordered, deduplicated).
    """
    from question_bank.taxonomy.curriculum_catalog import (
        curriculum_knowledge_ancestors,
        curriculum_knowledge_node,
    )

    ordered = [
        str(key) for key in dict.fromkeys(keys) if str(key or "").strip()
    ]
    skill_map = skill_parent_targets(
        conn,
        [key for key in ordered if key.startswith("sk_")],
        preferred_release_id,
    )
    sections: list[str] = []
    chapters: list[str] = []
    for key in ordered:
        anchor = skill_map.get(key, key)
        node = curriculum_knowledge_node(anchor)
        if node is None:
            continue
        level = int(node.get("level") or 0)
        if level == 1:
            chapters.append(anchor)
            continue
        if level == 2:
            sections.append(anchor)
        ancestors = curriculum_knowledge_ancestors(anchor)
        if level >= 3 and ancestors:
            sections.append(ancestors[0])
        chapters.extend(
            parent
            for parent in ancestors
            if (curriculum_knowledge_node(parent) or {}).get("level") == 1
        )
    return {
        "sections": list(dict.fromkeys(sections)),
        "chapters": list(dict.fromkeys(chapters)),
    }


def _iter_embedded_links(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Flatten resolved embedded ``fine_term_links`` into plain row dicts."""
    parts = payload.get("parts")
    if parts is None:
        parts = [payload] if "evidence_points" in payload else []
    rows: list[dict[str, Any]] = []
    for part in parts or []:
        if not isinstance(part, Mapping):
            continue
        part_id = str(part.get("part_id") or "")
        for point in part.get("evidence_points", []) or []:
            if not isinstance(point, Mapping):
                continue
            resolved = []
            for raw in point.get("fine_term_links", []) or []:
                if not isinstance(raw, Mapping):
                    continue
                resolution = raw.get("core_resolution")
                keys = (
                    resolution.get("stable_keys")
                    if isinstance(resolution, Mapping)
                    else None
                ) or []
                if (
                    not isinstance(resolution, Mapping)
                    or str(resolution.get("status") or "") != "resolved"
                    or not keys
                ):
                    continue
                role = str(raw.get("role") or "").strip()
                if role not in _LINK_ROLES:
                    continue
                resolved.append(
                    {
                        "part_id": part_id,
                        "evidence_point_id": str(
                            point.get("evidence_point_id") or ""
                        ),
                        "role": role,
                        "term_id": str(
                            raw.get("fine_term_id") or keys[0]
                        ).strip(),
                        "stable_key": str(keys[0]),
                        "release_hint": str(
                            resolution.get("reason") or ""
                        ),
                    }
                )
            direct_count = sum(1 for item in resolved if item["role"] == "direct")
            for item in resolved:
                item["weight"] = (
                    1.0 / direct_count
                    if item["role"] == "direct" and direct_count
                    else 1.0
                )
            rows.extend(resolved)
    return rows


@lru_cache(maxsize=1)
def _curriculum_chapter_maps() -> tuple[
    dict[str, str], dict[str, tuple[int, int]], list[tuple[str, str]]
]:
    """term stable key -> chapter key; chapter key -> (volume, chapter) order.

    The third element is a longest-first (section/chapter key, chapter key)
    prefix table for terms that are not leaf ids themselves.
    """
    from question_bank.taxonomy.curriculum_catalog import load_curriculum_catalog

    term_chapter: dict[str, str] = {}
    chapter_order: dict[str, tuple[int, int]] = {}
    prefixes: list[tuple[str, str]] = []
    for volume in load_curriculum_catalog()["volumes"]:
        volume_order = int(volume["order"])
        for chapter in volume["chapters"]:
            chapter_id = str(chapter["knowledge_id"])
            chapter_order[chapter_id] = (volume_order, int(chapter["order"]))
            term_chapter.setdefault(chapter_id, chapter_id)
            prefixes.append((chapter_id, chapter_id))
            for section in chapter["sections"]:
                section_id = str(section["knowledge_id"])
                term_chapter.setdefault(section_id, chapter_id)
                prefixes.append((section_id, chapter_id))
                for point in section.get("knowledge_points", []):
                    term_chapter.setdefault(str(point["id"]), chapter_id)
    prefixes.sort(key=lambda item: len(item[0]), reverse=True)
    return term_chapter, chapter_order, prefixes


def _chapter_of_term(term_key: object, maps: tuple[Any, Any, Any]) -> str:
    term_chapter, _order, prefixes = maps
    key = str(term_key or "").strip()
    if not key:
        return ""
    hit = term_chapter.get(key)
    if hit:
        return hit
    for prefix, chapter_id in prefixes:
        if key.startswith(f"{prefix}_"):
            return chapter_id
    return ""


def drop_later_chapter_supporting_links(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop supporting links from chapters later than the primary chapter.

    ``rows`` are flat link dicts carrying ``role``, ``term_id``/``stable_key``,
    ``evidence_point_id`` and optionally ``weight`` (missing direct weights
    default to ``1 / direct-count`` of their point, matching the write path).
    The primary chapter is the chapter with the largest summed direct-link
    weight; ties resolve to the earliest curriculum order. Terms that cannot
    be placed in the bundled catalog are kept. Returns ``(kept, dropped)``;
    dropped rows gain ``drop_reason`` and ``primary_chapter``.
    """
    term_chapter, chapter_order, prefixes = _curriculum_chapter_maps()
    maps = (term_chapter, chapter_order, prefixes)
    direct_counts = Counter(
        str(row.get("evidence_point_id") or "")
        for row in rows
        if str(row.get("role") or "") == "direct"
    )

    def _weight(row: Mapping[str, Any]) -> float:
        raw = row.get("weight")
        if raw is not None:
            return float(raw)
        point_id = str(row.get("evidence_point_id") or "")
        count = direct_counts.get(point_id, 0)
        return 1.0 / count if count else 1.0

    chapter_weight: dict[str, float] = {}
    for row in rows:
        if str(row.get("role") or "") != "direct":
            continue
        chapter = _chapter_of_term(
            row.get("stable_key") or row.get("term_id"), maps
        )
        if chapter:
            chapter_weight[chapter] = chapter_weight.get(chapter, 0.0) + _weight(row)
    if not chapter_weight:
        return [dict(row) for row in rows], []
    primary = max(
        chapter_weight,
        key=lambda chapter: (
            chapter_weight[chapter],
            -chapter_order[chapter][0],
            -chapter_order[chapter][1],
        ),
    )
    primary_order = chapter_order[primary]
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("role") or "") == "supporting_prerequisite":
            chapter = _chapter_of_term(
                row.get("stable_key") or row.get("term_id"), maps
            )
            if chapter and chapter_order[chapter] > primary_order:
                dropped.append(
                    {
                        **dict(row),
                        "drop_reason": "later_than_primary_chapter",
                        "primary_chapter": primary,
                    }
                )
                continue
        kept.append(dict(row))
    return kept, dropped


def links_from_embedded(
    payload: Mapping[str, Any],
) -> dict[str, tuple[KnowledgeLink, ...]]:
    """Convert embedded links to :class:`KnowledgeLink` rows keyed by point id.

    Used by the write-time projection and by tests; production reads always go
    through :func:`load_point_links`.
    """
    result: dict[str, list[KnowledgeLink]] = {}
    for row in _iter_embedded_links(payload):
        if not row["term_id"]:
            continue
        result.setdefault(row["evidence_point_id"], []).append(
            KnowledgeLink(
                term_id=row["term_id"],
                stable_key=row["stable_key"],
                role=row["role"],
                weight=row["weight"],
                resolution_status="resolved",
                source_kind=MIGRATED_KIND,
            )
        )
    return {point_id: tuple(items) for point_id, items in result.items()}


def refresh_question_scope_summary(
    connection: sqlite3.Connection,
    question_id: int,
    *,
    db_path: Path | None = None,
) -> None:
    """Recompute the §8 ``question_scope_summary`` row for one question.

    The summary always describes the question's current usable evidence
    version's resolved links; questions without a usable version lose their
    row so scope filters fall back to tag matching.
    """
    qid = int(question_id)
    row = connection.execute(
        """
        SELECT v.evidence_version_id, v.graph_release_id
        FROM question_solution_evidence_versions v
        JOIN (
            SELECT question_id, MAX(created_at) AS max_created
            FROM question_solution_evidence_versions
            WHERE status IN ('proposed', 'approved')
            GROUP BY question_id
        ) m
          ON m.question_id = v.question_id
         AND m.max_created = v.created_at
        WHERE v.question_id = ? AND v.status IN ('proposed', 'approved')
        LIMIT 1
        """,
        (qid,),
    ).fetchone()
    if row is None:
        connection.execute(
            "DELETE FROM question_scope_summary WHERE question_id = ?",
            (qid,),
        )
        return
    version_id = str(row["evidence_version_id"])
    grouped = load_point_links(
        Path(db_path) if db_path is not None else Path("."),
        [version_id],
        None,
        connection=connection,
    )
    release_id = next((link.graph_release_id for links in grouped.get(version_id, {}).values()
                       for link in links), "")
    direct_weight: dict[str, float] = {}
    supporting_keys: list[str] = []
    for links in grouped.get(version_id, {}).values():
        for link in links:
            if link.resolution_status != "resolved" or not link.stable_key:
                continue
            if link.role == "direct":
                direct_weight[link.stable_key] = (
                    direct_weight.get(link.stable_key, 0.0) + link.weight
                )
            elif link.role == "supporting_prerequisite":
                supporting_keys.append(link.stable_key)
    if not direct_weight:
        # No resolved direct links: drop the row so scope filters fall back
        # to the question's model-derived ownership tags.
        connection.execute(
            "DELETE FROM question_scope_summary WHERE question_id = ?",
            (qid,),
        )
        return
    from question_bank.taxonomy.curriculum_catalog import (
        curriculum_knowledge_node,
    )

    direct_anchors = resolve_anchor_keys(
        connection,
        list(direct_weight),
        preferred_release_id=release_id or None,
    )
    key_anchors = {
        key: resolve_anchor_keys(
            connection,
            [key],
            preferred_release_id=release_id or None,
        )
        for key in direct_weight
    }
    # 主小节只在能解析出小节的 direct 键中选：跨章节技能词（如只挂到
    # 综合与实践章、没有小节锚点的 sk_*）权重再高也不占主小节位置；
    # 没有任何键能解析出小节时退回原有全量选主逻辑。
    sectioned_keys = [
        key for key in direct_weight if key_anchors[key]["sections"]
    ]
    primary_key = max(
        sectioned_keys or list(direct_weight),
        key=lambda key: (direct_weight[key], key),
    )
    primary_anchors = key_anchors[primary_key]
    supporting_orders = [
        int(node["volume_order"])
        for key in dict.fromkeys(supporting_keys)
        if (
            node := curriculum_knowledge_node(
                (
                    resolve_anchor_keys(
                        connection,
                        [key],
                        preferred_release_id=release_id or None,
                    )["sections"] or [key]
                )[-1]
            )
        )
        is not None
        and node.get("volume_order") is not None
    ]
    connection.execute(
        """
        INSERT INTO question_scope_summary (
            question_id, evidence_version_id, graph_release_id,
            primary_section_id, direct_section_ids_json,
            supporting_max_volume_order, has_cross_chapter_direct,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
        ON CONFLICT(question_id) DO UPDATE SET
            evidence_version_id = excluded.evidence_version_id,
            graph_release_id = excluded.graph_release_id,
            primary_section_id = excluded.primary_section_id,
            direct_section_ids_json = excluded.direct_section_ids_json,
            supporting_max_volume_order = excluded.supporting_max_volume_order,
            has_cross_chapter_direct = excluded.has_cross_chapter_direct,
            updated_at = excluded.updated_at
        """,
        (
            qid,
            version_id,
            release_id,
            primary_anchors["sections"][-1] if primary_anchors["sections"] else "",
            json.dumps(direct_anchors["sections"], ensure_ascii=False),
            max(supporting_orders) if supporting_orders else 0,
            1 if len(set(direct_anchors["chapters"])) > 1 else 0,
        ),
    )


def rebuild_question_scope_summaries(
    db_path: Path,
    *,
    connection: sqlite3.Connection | None = None,
) -> int:
    """Recompute ``question_scope_summary`` for every question with evidence.

    Used once after link migrations/backfills; regular writes refresh rows
    incrementally through :func:`refresh_question_scope_summary`.
    Returns the number of questions processed.
    """
    from question_bank.database.schema import connect

    if connection is not None:
        conn = connection
        ids = [
            int(row["question_id"])
            for row in conn.execute(
                "SELECT DISTINCT question_id "
                "FROM question_solution_evidence_versions"
            )
        ]
        existing = {
            int(row["question_id"])
            for row in conn.execute(
                "SELECT question_id FROM question_scope_summary"
            )
        }
        for qid in sorted(set(ids) | existing):
            refresh_question_scope_summary(conn, qid, db_path=db_path)
        return len(set(ids) | existing)
    with connect(Path(db_path)) as conn:
        return rebuild_question_scope_summaries(db_path, connection=conn)


def _active_release_id(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        """
        SELECT release_id FROM knowledge_graph_releases
        WHERE status = 'active'
        ORDER BY activated_at DESC, created_at DESC
        LIMIT 1
        """
    ).fetchone()
    return str(row["release_id"]) if row is not None else None


def project_embedded_links(
    connection: sqlite3.Connection,
    *,
    evidence_version_id: str,
    question_id: int,
    evidence_payload: Mapping[str, Any],
    default_release_id: str | None = None,
) -> int:
    """Project embedded resolved links of a written evidence version.

    ``INSERT OR IGNORE`` keeps existing ``link_job`` rows authoritative when
    both sources name the same target. Returns the number of inserted rows.
    """
    installed = {
        str(row["release_id"])
        for row in connection.execute(
            "SELECT release_id FROM knowledge_graph_releases"
        )
    }
    fallback = str(default_release_id or "").strip()
    if fallback not in installed:
        fallback = _active_release_id(connection) or ""
    if not fallback:
        return 0
    projected, _dropped = drop_later_chapter_supporting_links(
        _iter_embedded_links(evidence_payload)
    )
    inserted = 0
    for item in projected:
        if not item["term_id"]:
            continue
        release = item["release_hint"]
        release = (
            release[len(_RELEASE_REASON_PREFIX):]
            if release.startswith(_RELEASE_REASON_PREFIX)
            else ""
        ) or fallback
        if release not in installed:
            release = fallback
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO evidence_point_knowledge_links (
                evidence_version_id, question_id, part_id, evidence_point_id,
                graph_release_id, role, term_id, stable_key, resolution_status,
                weight, source_kind, source_reference
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'resolved', ?, ?, ?)
            """,
            (
                str(evidence_version_id),
                int(question_id),
                item["part_id"],
                item["evidence_point_id"],
                release,
                item["role"],
                item["term_id"],
                item["stable_key"],
                float(item["weight"]),
                MIGRATED_KIND,
                f"evidence:{evidence_version_id}",
            ),
        )
        inserted += int(cursor.rowcount > 0)
    refresh_question_scope_summary(connection, int(question_id))
    from question_bank.services.question_write_service import (
        refresh_derived_ownership_tags,
    )
    refresh_derived_ownership_tags(connection, int(question_id))
    return inserted


def replace_point_links(
    connection: sqlite3.Connection,
    *,
    evidence_version_id: str,
    question_id: int,
    graph_release_id: str,
    points: Sequence[Mapping[str, Any]],
    source_kind: str = LINK_JOB_KIND,
    source_reference: str = "",
    replace: bool = True,
) -> int:
    """Write link rows for a version under one release.

    ``points`` items carry ``part_id``, ``evidence_point_id`` and ``links``
    (iterables of ``{term_id, stable_key, role, weight}``). With ``replace``
    the existing rows of the same ``source_kind`` for this (version, release)
    are deleted first; migrated and teacher rows are never touched and stay as
    fallback for points the job did not cover.
    """
    release = str(graph_release_id or "").strip()
    if not release:
        raise ValueError("graph_release_id is required")
    if replace:
        connection.execute(
            """
            DELETE FROM evidence_point_knowledge_links
            WHERE evidence_version_id = ? AND graph_release_id = ?
              AND source_kind = ?
            """,
            (str(evidence_version_id), release, source_kind),
        )
    inserted = 0
    for point in points:
        direct = [
            link for link in point.get("links", [])
            if str(link.get("role") or "") == "direct"
        ]
        for link in point.get("links", []):
            role = str(link.get("role") or "").strip()
            if role not in _LINK_ROLES:
                raise ValueError(f"link role is invalid: {role!r}")
            term_id = str(link.get("term_id") or "").strip()
            if not term_id:
                raise ValueError("link term_id must not be empty")
            stable_key = str(link.get("stable_key") or term_id)
            weight = link.get("weight")
            weight = (
                float(weight)
                if weight is not None
                else (1.0 / len(direct) if role == "direct" and direct else 1.0)
            )
            connection.execute(
                """
                INSERT INTO evidence_point_knowledge_links (
                    evidence_version_id, question_id, part_id, evidence_point_id,
                    graph_release_id, role, term_id, stable_key,
                    resolution_status, weight, source_kind, source_reference
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'resolved', ?, ?, ?)
                """,
                (
                    str(evidence_version_id),
                    int(question_id),
                    str(point.get("part_id") or ""),
                    str(point.get("evidence_point_id") or ""),
                    release,
                    role,
                    term_id,
                    stable_key,
                    weight,
                    source_kind,
                    str(source_reference or ""),
                ),
            )
            inserted += 1
    refresh_question_scope_summary(connection, int(question_id))
    from question_bank.services.question_write_service import (
        refresh_derived_ownership_tags,
    )
    refresh_derived_ownership_tags(connection, int(question_id))
    return inserted


def skill_layer_report(
    connection: sqlite3.Connection,
    graph_release_id: str,
) -> dict[str, Any]:
    """Read-only routine report: underused skills and co-occurring pairs."""
    latest = """
        SELECT v.evidence_version_id, v.question_id
        FROM question_solution_evidence_versions v
        JOIN (
            SELECT question_id, MAX(created_at) AS max_created
            FROM question_solution_evidence_versions
            WHERE status IN ('proposed', 'approved')
            GROUP BY question_id
        ) m
          ON m.question_id = v.question_id AND m.max_created = v.created_at
        WHERE v.status IN ('proposed', 'approved')
    """
    usage_rows = connection.execute(
        f"""
        SELECT l.stable_key, l.question_id
        FROM evidence_point_knowledge_links l
        JOIN ({latest}) m ON m.evidence_version_id = l.evidence_version_id
        WHERE l.graph_release_id = ? AND l.role = 'direct'
          AND l.resolution_status = 'resolved'
        """,
        (str(graph_release_id),),
    ).fetchall()
    questions_by_skill: dict[str, set[int]] = {}
    for row in usage_rows:
        questions_by_skill.setdefault(str(row["stable_key"]), set()).add(
            int(row["question_id"])
        )
    skill_names = {
        str(row["stable_key"]): str(row["display_name"] or "")
        for row in connection.execute(
            """
            SELECT stable_key, display_name FROM knowledge_tag_identities
            WHERE status = 'active' AND stable_key LIKE 'sk\\_%' ESCAPE '\\'
            """
        )
    }
    skills = [
        {
            "stable_key": key,
            "display_name": name,
            "question_count": len(questions_by_skill.get(key, set())),
            "underused": len(questions_by_skill.get(key, set())) < 5,
        }
        for key, name in sorted(skill_names.items())
    ]
    # Co-occurrence: skill pairs linked from the same question.
    skills_by_question: dict[int, set[str]] = {}
    for row in usage_rows:
        key = str(row["stable_key"])
        if key in skill_names:
            skills_by_question.setdefault(int(row["question_id"]), set()).add(key)
    pair_counts: dict[tuple[str, str], int] = {}
    for keys in skills_by_question.values():
        ordered = sorted(keys)
        for index, left in enumerate(ordered):
            for right in ordered[index + 1:]:
                pair_counts[(left, right)] = pair_counts.get((left, right), 0) + 1
    pairs = [
        {
            "left": left,
            "right": right,
            "shared_questions": shared,
            "cooccurrence": round(
                shared
                / min(
                    len(questions_by_skill.get(left, {0})) or 1,
                    len(questions_by_skill.get(right, {0})) or 1,
                ),
                4,
            ),
        }
        for (left, right), shared in sorted(pair_counts.items())
        if shared >= 3
        and shared
        / min(
            len(questions_by_skill.get(left, {0})) or 1,
            len(questions_by_skill.get(right, {0})) or 1,
        )
        > 0.9
    ]
    return {
        "graph_release_id": str(graph_release_id),
        "linked_questions": len(skills_by_question),
        "skills": skills,
        "underused_skills": [item for item in skills if item["underused"]],
        "cooccurring_pairs": pairs,
    }


__all__ = [
    "KnowledgeLink",
    "LINK_JOB_KIND",
    "MIGRATED_KIND",
    "TEACHER_KIND",
    "load_point_links",
    "direct_links_for_part",
    "direct_targets_for_part",
    "drop_later_chapter_supporting_links",
    "links_from_embedded",
    "project_embedded_links",
    "replace_point_links",
    "skill_layer_report",
]
