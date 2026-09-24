"""Knowledge context and observed skill matching, shared by training and assembly.

Matching never relaxes difficulty, progress, source validity or duplicate checks.
Those eligibility rules stay with each caller. Whole-question topic tags can
describe a single part, but never assert which of several parts tests a topic.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any
import json
import sqlite3
import threading
from pathlib import Path
from functools import lru_cache

from question_bank.current_knowledge import CurrentKnowledgeResolver
from question_bank.taxonomy.curriculum_catalog import load_curriculum_catalog


MATCH_LABELS = {
    1: "同技能、同知识主题",
    2: "同章同技能迁移",
    3: "同知识主题巩固",
    4: "同小节补充",
}


@lru_cache(maxsize=4)
def target_index(resolver: CurrentKnowledgeResolver) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for volume in load_curriculum_catalog()["volumes"]:
        for chapter in volume["chapters"]:
            chapter_key = str(chapter["knowledge_id"])
            index[chapter_key] = {"kind": "chapter", "chapter": chapter_key, "section": ""}
            for section in chapter["sections"]:
                section_key = str(section["knowledge_id"])
                index[section_key] = {"kind": "section", "chapter": chapter_key, "section": section_key}
                for leaf in section["knowledge_points"]:
                    index[str(leaf["id"])] = {"kind": "topic", "chapter": chapter_key, "section": section_key}
    parents = {r.source_key: r.target_key for r in resolver.relations if r.relation_type == "parent"}
    for node in resolver.nodes:
        if not node.stable_key.startswith("sk_"):
            continue
        parent = parents.get(node.stable_key, "")
        anchor = index.get(parent, {})
        index[node.stable_key] = {"kind": "skill", "chapter": anchor.get("chapter", ""),
                                  "section": anchor.get("section", "")}
    return index


def topic_keys(values: Sequence[str], resolver: CurrentKnowledgeResolver,
               index: Mapping[str, Mapping[str, str]]) -> list[str]:
    return sorted({identity.stable_key for value in values for identity in resolver.resolve(value)
                   if index.get(identity.stable_key, {}).get("kind") == "topic"})


def part_facets(evidence: Mapping[str, Any], links: Mapping[str, Sequence[Any]] | None,
                resolver: CurrentKnowledgeResolver, index: Mapping[str, Mapping[str, str]],
                question_topics: Sequence[str] = ()) -> list[dict[str, Any]]:
    parts = evidence.get("parts") or []
    result = []
    for part in parts:
        keys: set[str] = set()
        topics: set[str] = set()
        for point in part.get("evidence_points", []):
            embedded = point.get("fine_term_links") or []
            if links is not None:
                direct_values = [str(link.stable_key) for link in links.get(str(point.get("evidence_point_id") or ""), ())
                                 if link.role == "direct" and link.resolution_status == "resolved" and link.weight > 0]
            else:
                direct_values = [str(link.get("fine_term_id") or "") for link in embedded
                                 if link.get("role") == "direct"]
            keys.update(identity.stable_key for value in direct_values for identity in resolver.resolve(value))
            current_topics = topic_keys(direct_values, resolver, index)
            if current_topics:
                topics.update(current_topics)
            else:
                # Skill-only relinking retains the original small-part topic as
                # compatibility context. Explicit current topics take precedence;
                # whole-question tags never fill in an unknown multipart topic.
                for link in embedded:
                    resolution = link.get("core_resolution") or {}
                    if link.get("role") != "direct" or resolution.get("status", "resolved") != "resolved":
                        continue
                    topics.update(topic_keys(resolution.get("stable_keys") or
                                             [str(link.get("fine_term_id") or "")], resolver, index))
        basis = "part_evidence" if topics else "unknown"
        if not topics and len(parts) == 1:
            topics.update(question_topics)
            if topics:
                basis = "single_part_question_tags"
        # Context comes from this part's topics when known. A reusable skill's
        # home section must not make an unrelated chapter count as same-chapter.
        anchors = [index.get(key, {}) for key in (topics or keys)]
        result.append({"part_id": str(part.get("part_id") or ""), "direct_keys": sorted(keys),
                       "skill_keys": sorted(key for key in keys if key.startswith("sk_")),
                       "topic_keys": sorted(topics), "topic_basis": basis,
                       "chapter_keys": sorted({a["chapter"] for a in anchors if a.get("chapter")}),
                       "section_keys": sorted({a["section"] for a in anchors if a.get("section")})})
    return result


def _point_link_rows(point: Mapping[str, Any],
                     links: Mapping[str, Sequence[Any]] | None) -> list[dict[str, Any]]:
    """Normalise point links to ``{role, term_id, stable_keys, resolved}``.

    ``links`` is the per-point map from ``evidence_point_knowledge_links``
    (``load_point_links``); when it is ``None`` the embedded
    ``fine_term_links`` are read as compatibility input.
    """
    if links is not None:
        point_id = str(point.get("evidence_point_id") or "")
        return [
            {
                "role": str(link.role),
                "term_id": str(link.term_id or ""),
                "stable_keys": [link.stable_key] if link.stable_key else [],
                "resolved": (
                    link.resolution_status == "resolved" and bool(link.stable_key)
                ),
            }
            for link in links.get(point_id, ())
        ]
    rows: list[dict[str, Any]] = []
    for link in point.get("fine_term_links", []):
        resolution = link.get("core_resolution") or {}
        keys = resolution.get("stable_keys") or []
        rows.append({
            "role": link.get("role"),
            "term_id": link.get("fine_term_id"),
            "stable_keys": list(keys),
            "resolved": resolution.get("status") == "resolved" and bool(keys),
        })
    return rows


def _question_evidence_metadata(
    evidence: Mapping[str, Any],
    resolver: CurrentKnowledgeResolver,
    links: Mapping[str, Sequence[Any]] | None = None,
) -> dict[str, Any]:
    """Keep roles and response modes tied to each small part of the printed question."""
    direct: set[str] = set()
    required: set[str] = set()
    supporting: set[str] = set()
    modes: dict[str, set[str]] = {}
    observations: dict[str, list[dict[str, Any]]] = {}
    parts = evidence.get("parts") or []
    complete = bool(parts)
    link_rows_by_point: dict[int, list[dict[str, Any]]] = {}
    for part in parts:
        part_direct: set[str] = set()
        for point_index, point in enumerate(part.get("evidence_points", [])):
            rows = _point_link_rows(point, links)
            link_rows_by_point[id(point)] = rows
            if not any(link["resolved"] and link["role"] == "direct" for link in rows):
                complete = False
            for link in rows:
                if not link["resolved"]:
                    complete = False
                    continue
                for raw_key in link["stable_keys"]:
                    resolved = resolver.resolve(raw_key)
                    if not resolved:
                        complete = False
                    for identity in resolved:
                        required.add(identity.stable_key)
                        if link["role"] == "direct":
                            part_direct.add(identity.stable_key)
                        else:
                            supporting.add(identity.stable_key)
        if not part_direct:
            complete = False
        direct.update(part_direct)
        for key in part_direct:
            modes.setdefault(key, set()).add(str(part.get("response_mode") or "unknown"))
            relevant_points = [point for point in part.get("evidence_points", []) if any(
                link["role"] == "direct" and key in link["stable_keys"]
                for link in link_rows_by_point.get(id(point), _point_link_rows(point, links)))]
            observations.setdefault(key, []).append({
                "part_id": str(part.get("part_id") or ""),
                "response_mode": str(part.get("response_mode") or "unknown"),
                "observable": "；".join(str(point.get(field) or "") for point in relevant_points
                                        for field in ("target", "observable_evidence", "justification")),
                "part_observable": "；".join(str(point.get(field) or "") for point in part.get("evidence_points", [])
                                             for field in ("target", "observable_evidence", "justification")),
                "evidence_points": [{field: deepcopy(point.get(field)) for field in
                                     ("evidence_point_id", "target", "observable_evidence", "justification")}
                                    for point in relevant_points],
                "fine_terms": sorted({str(link["term_id"]) for point in relevant_points
                                      for link in link_rows_by_point.get(id(point), [])
                                      if link["role"] == "direct" and link["term_id"]}),
            })
    return {"stable_keys": sorted(direct), "required_keys": sorted(required), "supporting_keys": sorted(supporting), "scope_complete": complete,
            "response_modes_by_key": {key: sorted(values) for key, values in sorted(modes.items())},
            "practice_observations_by_key": observations}


def match_target(target_key: str, source_parts: Sequence[Mapping[str, Any]],
                 candidate_parts: Sequence[Mapping[str, Any]],
                 index: Mapping[str, Mapping[str, str]]) -> dict[str, Any] | None:
    """Return the strongest justified match, always within one candidate part."""
    target = index.get(target_key, {})
    target_parts = [p for p in source_parts if target_key in p.get("direct_keys", ())]
    if target_key.startswith("sk_") and not target_parts:
        return None
    source_parts = target_parts or list(source_parts)
    matches = []
    for source in source_parts:
        topics = set(source.get("topic_keys", ()))
        sections = set(source.get("section_keys", ())) or {target.get("section", "")}
        chapters = set(source.get("chapter_keys", ())) or {target.get("chapter", "")}
        sections.discard("")
        chapters.discard("")
        for candidate in candidate_parts:
            same_skill = target_key.startswith("sk_") and target_key in candidate.get("skill_keys", ())
            same_topics = bool(topics) and topics.issubset(candidate.get("topic_keys", ()))
            same_chapter = bool(chapters.intersection(candidate.get("chapter_keys", ())))
            if same_skill and same_topics:
                level = 1
            elif same_skill and same_chapter:
                level = 2
            elif same_topics:
                level = 3
            elif sections.intersection(candidate.get("section_keys", ())):
                level = 4
            else:
                continue
            matches.append({"match_level": level, "match_label": MATCH_LABELS[level],
                            "matched_topic_keys": sorted(topics.intersection(candidate.get("topic_keys", ()))),
                            "matched_skill_keys": [target_key] if same_skill else [],
                            "source_part_id": str(source.get("part_id") or ""),
                            "candidate_part_id": str(candidate.get("part_id") or "")})
    return min(matches, key=lambda m: (m["match_level"], m["candidate_part_id"], m["source_part_id"])) if matches else None


_FACETS_CACHE_LOCK = threading.Lock()
# Whole-bank facet loads back every interactive selection change; keep one
# snapshot per facet-source signature instead of re-reading every evidence row.
_FACETS_CACHE: dict[tuple[Any, ...], dict[int, dict[str, Any]]] = {}


def _facets_signature(conn: sqlite3.Connection) -> tuple[Any, ...]:
    """Table-level state that changes exactly when facet inputs change.

    File mtime would also flap on unrelated writes (e.g. persisting computed
    content-index keys), so the cache keys on cheap aggregates of the tables
    that actually feed the facets below.
    """
    return (
        conn.execute("SELECT COUNT(*), MAX(updated_at) FROM questions WHERE is_deleted=0").fetchone(),
        conn.execute("SELECT COUNT(*), MAX(updated_at) FROM question_scope_summary").fetchone(),
        conn.execute("SELECT COUNT(*), MAX(updated_at) FROM question_solution_evidence_versions WHERE status IN ('approved','proposed')").fetchone(),
        conn.execute("SELECT COUNT(*), MAX(id), MAX(created_at) FROM question_tags WHERE tag_type='knowledge_point'").fetchone(),
        conn.execute("SELECT COUNT(*), MAX(created_at), SUM(resolution_status='resolved') FROM evidence_point_knowledge_links").fetchone(),
    )


def load_question_facets(db_path: Path, resolver: CurrentKnowledgeResolver,
                         question_ids: Sequence[int] | None = None) -> dict[int, dict[str, Any]]:
    """Read existing tags and current point links; never create analyses or versions."""
    from question_bank.solution_evidence.knowledge_links import load_point_links
    if question_ids is not None and not question_ids:
        return {}
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cache_key = None
        if question_ids is None:
            # The signature covers every table feeding the facets, so a request
            # snapshot copy of the same database shares one entry instead of
            # re-reading the whole bank under a different path.
            cache_key = (resolver.release_id, _facets_signature(conn))
            with _FACETS_CACHE_LOCK:
                cached = _FACETS_CACHE.get(cache_key)
                if cached is not None:
                    return cached
        index = target_index(resolver)
        ids = set(question_ids) if question_ids is not None else None
        rows = [r for r in conn.execute("""
            SELECT q.id, s.evidence_version_id, v.evidence_json
            FROM questions q JOIN question_scope_summary s ON s.question_id=q.id
            JOIN question_solution_evidence_versions v ON v.evidence_version_id=s.evidence_version_id
            WHERE q.is_deleted=0 AND v.status IN ('approved','proposed')
        """) if ids is None or int(r["id"]) in ids]
        tags: dict[int, list[str]] = {}
        for row in conn.execute("SELECT question_id, tag_value FROM question_tags WHERE tag_type='knowledge_point'"):
            if ids is None or int(row["question_id"]) in ids:
                tags.setdefault(int(row["question_id"]), []).append(str(row["tag_value"]))
        links = load_point_links(db_path, [str(r["evidence_version_id"]) for r in rows],
                                 resolver.release_id, connection=conn)
        result = {}
        for row in rows:
            qid = int(row["id"])
            topics = topic_keys(tags.get(qid, []), resolver, index)
            evidence = json.loads(row["evidence_json"])
            facets = part_facets(evidence, links.get(str(row["evidence_version_id"]), {}), resolver, index, topics)
            practice = _question_evidence_metadata(evidence, resolver, links=links.get(str(row["evidence_version_id"]), {}))
            result[qid] = {"parts": facets, "topic_keys": topics,
                           "evidence_version_id": str(row["evidence_version_id"]),
                           "practice_observations_by_key": practice["practice_observations_by_key"],
                           "skill_keys": sorted({key for part in facets for key in part["skill_keys"]})}
        if cache_key is not None:
            with _FACETS_CACHE_LOCK:
                if len(_FACETS_CACHE) > 4:
                    _FACETS_CACHE.clear()
                _FACETS_CACHE[cache_key] = result
        return result
    finally:
        conn.close()


def knowledge_skill_associations(facets: Mapping[int, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Co-occurrence is labelled separately from known same-part associations."""
    pairs: dict[tuple[str, str], dict[str, set[int]]] = {}
    for qid, question in facets.items():
        for topic in question["topic_keys"]:
            for skill in question["skill_keys"]:
                pairs.setdefault((topic, skill), {"questions": set(), "parts": set()})["questions"].add(qid)
        for part in question["parts"]:
            for topic in part["topic_keys"]:
                for skill in part["skill_keys"]:
                    pair = pairs.setdefault((topic, skill), {"questions": set(), "parts": set()})
                    pair["questions"].add(qid)
                    pair["parts"].add(qid)
    return [{"topic_key": topic, "skill_key": skill, "question_count": len(value["questions"]),
             "same_part_question_count": len(value["parts"]),
             "basis": "same_part" if value["parts"] else "question_cooccurrence"}
            for (topic, skill), value in sorted(pairs.items())]
