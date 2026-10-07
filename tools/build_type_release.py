"""Build the question-type (题型) knowledge-standard candidate and preview it.

Stage 2 of docs/requests/question-type-redesign-plan-20261006.md: the approved
chapter vocabularies under ``output/question_type_stage1_20261006/vocab/``
become one ``core`` node per type hanging under their curriculum section on
top of the DB's active release. Nothing is retired; all 学科网 leaves and
skills stay unchanged so existing resolved links carry forward by identity.
Per-question labels become ``direct`` links on every evidence point of each
labeled question's current evidence version; secondary types are written as
``secondary_type`` question tags and included in a JSON sidecar.

Modes (default is a dry-run that writes nothing):
    python tools/build_type_release.py --vocab-dir DIR --labels-dir DIR
    python tools/build_type_release.py ... --out-dir DIR
    python tools/build_type_release.py ... --write [--catalog-dir DIR]
    python tools/build_type_release.py ... --preview-db COPY.db [--out-dir DIR]
    python tools/build_type_release.py ... --activate-and-carry-db DB --backup-dir DIR

``--write`` writes the release + vocabulary JSONs into the taxonomy catalog
directory and prints the loader map lines; it does not activate anything in
any database. ``--preview-db`` is the destination copy: it is created from
``--db`` with the SQLite backup API and the candidate is applied to the copy
only. The resolved real bank path is refused for preview destinations.
``--activate-and-carry-db`` requires separate authorization for this operation,
a registered candidate, a backup directory and a passing isolated rehearsal.
``--skip-unavailable`` limits new type links/tags to current usable sources;
excluded questions keep their existing evidence and remain unavailable.
Stop the application before activation; no mode calls a model.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import tempfile
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from question_bank.knowledge_graph_release.contracts import (
    KnowledgeGraphRelease,
    compute_content_hash,
    stable_record_hash,
)
from question_bank.knowledge_graph_release.validation import validate_release
from question_bank.relations.contracts import normalize_stable_key
from tools.build_release_v3 import _write_json, build_vocabulary
from tools.build_skill_release import base_release, next_version_numbers, registered_release

CATALOG_DIR = ROOT / "question_bank" / "taxonomy" / "catalogs"
DEFAULT_DB = ROOT / "user_data" / "databases" / "question_bank.db"
DEFAULT_VOCAB_DIR = ROOT / "output" / "question_type_stage1_20261006" / "vocab"
DEFAULT_LABELS_DIR = ROOT / "output" / "question_type_stage1_20261006" / "labels"

SOURCE_ID = "question_type_vocab_2026_10"
SOURCE_LOCATOR = "output/question_type_stage1_20261006/vocab/"
ACTOR = "question_type_vocab_2026_10"
REASON = "发布教师批准的题型词表为题型节点，旧版链接原样结转"
TYPE_SOURCE_KIND_TITLE = "教师批准的章节题型词表（2026-10）"

_TYPE_ID = re.compile(r"T(\d+)-(\d+)")
_TYPE_KEY = re.compile(r"_t\d{2}$")


def load_type_vocabulary(vocab_dir: Path) -> list[dict[str, Any]]:
    """Parse the approved chapter vocab TSVs into type records with stable keys."""
    types: list[dict[str, Any]] = []
    for path in sorted(Path(vocab_dir).glob("vocab_ch*.tsv")):
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                type_id = str(row.get("type_id") or "").strip()
                match = _TYPE_ID.fullmatch(type_id)
                if not match:
                    raise SystemExit(f"{path.name}: 无法识别的题型编号 {type_id!r}")
                section_key = str(row.get("section_key") or "").strip()
                chapter_no = int(match.group(1))
                number = int(match.group(2))
                parts = section_key.split("_")
                if len(parts) != 7 or int(parts[5]) != chapter_no:
                    raise SystemExit(
                        f"{type_id} 的 section_key {section_key!r} 与章号不符。"
                    )
                stable_key = f"{section_key}_t{number:02d}"
                try:
                    stable_key = normalize_stable_key(stable_key)
                except ValueError as exc:
                    raise SystemExit(
                        f"{type_id} 的稳定键 {stable_key!r} 不符合"
                        f" knowledge_tag_identities 规则：{exc}"
                    )
                anchors = [
                    value.strip()
                    for value in str(row.get("anchors") or "").split(";")
                    if value.strip()
                ]
                types.append(
                    {
                        "type_id": type_id,
                        "stable_key": stable_key,
                        "name": str(row.get("name") or "").strip(),
                        "section_key": section_key,
                        "chapter_no": chapter_no,
                        "definition": str(row.get("definition") or "").strip(),
                        "include": str(row.get("include") or "").strip(),
                        "exclude": str(row.get("exclude") or "").strip(),
                        "anchors": anchors,
                        "source_leaves": str(row.get("source_leaves") or "").strip(),
                        "rationale": str(row.get("rationale") or "").strip(),
                    }
                )
    if not types:
        raise SystemExit(f"{vocab_dir} 下没有 vocab_ch*.tsv 题型记录。")
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    for item in types:
        for seen, value, label in (
            (seen_ids, item["type_id"], "题型编号"),
            (seen_keys, item["stable_key"], "稳定键"),
        ):
            if value in seen:
                raise SystemExit(f"{label}重复：{value}")
            seen.add(value)
    return types


def load_labels(labels_dir: Path) -> dict[int, dict[str, Any]]:
    """Parse per-batch label TSVs into ``{question_id: label}``."""
    labels: dict[int, dict[str, Any]] = {}
    for path in sorted(Path(labels_dir).glob("labels_ch*_p*.tsv")):
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                try:
                    qid = int(str(row.get("question_id") or "").strip())
                except ValueError:
                    raise SystemExit(f"{path.name}: 题目编号无法解析 {row!r}")
                secondary = [
                    value.strip()
                    for value in str(row.get("secondary_types") or "").split(";")
                    if value.strip()
                ]
                if qid in labels:
                    raise SystemExit(f"{path.name}: 题目 {qid} 重复标注。")
                primary = str(row.get("primary_type") or "").strip()
                if not primary or (primary != "NONE" and not _TYPE_ID.fullmatch(primary)):
                    raise SystemExit(f"{path.name}: 题目 {qid} 的主题型无效。")
                if len(secondary) > 2 or len(set(secondary)) != len(secondary):
                    raise SystemExit(f"{path.name}: 题目 {qid} 的次题型必须不重复且不超过 2 个。")
                if primary in secondary or (primary == "NONE" and secondary):
                    raise SystemExit(f"{path.name}: 题目 {qid} 的主次题型不一致。")
                labels[qid] = {
                    "primary_type": primary,
                    "secondary_types": secondary,
                    "confidence": str(row.get("confidence") or "").strip(),
                }
    if not labels:
        raise SystemExit(f"{labels_dir} 下没有 labels_ch*_p*.tsv 标注记录。")
    return labels


def build_type_release(
    base_payload: Mapping[str, Any],
    base_vocabulary: Mapping[str, Any],
    types: Sequence[Mapping[str, Any]],
    *,
    release_id: str,
    taxonomy_revision: int,
    source_id: str = SOURCE_ID,
    source_locator: str = SOURCE_LOCATOR,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    """Base release + one core node per type; mirrors build_release_v5.build_v5."""
    nodes = {node["stable_key"]: node for node in base_payload["core_nodes"]}
    taken_names = {
        _normalized(value)
        for term in base_vocabulary.get("terms", [])
        if term.get("dimension") == "knowledge"
        for value in (term.get("id"), term.get("name"), *term.get("aliases", []))
    }
    rows: list[dict[str, Any]] = []
    key_map: dict[str, str] = {}
    for item in types:
        section_key = str(item["section_key"])
        if section_key not in nodes:
            raise SystemExit(f"题型 {item['type_id']} 的小节 {section_key} 不在当前标准中。")
        stable_key = str(item["stable_key"])
        if stable_key in nodes or stable_key in key_map.values():
            raise SystemExit(f"题型 {item['type_id']} 的稳定键 {stable_key} 已被占用。")
        section = nodes[section_key]
        display_name = f"{section['display_name']}｜题型·{item['name']}"
        if _normalized(display_name) in taken_names:
            raise ValueError(f"type term name collides: {display_name}")
        taken_names.add(_normalized(display_name))
        taken_names.add(_normalized(stable_key))
        section_anchor = (section.get("curriculum_anchors") or [""])[0]
        anchor = (
            f"{section_anchor}/题型·{item['name']}"
            if section_anchor
            else f"题型·{item['name']}"
        )
        rows.append(
            {
                "id": stable_key,
                "type_id": str(item["type_id"]),
                "section_key": section_key,
                "chapter_key": section_key.rsplit("_", 1)[0],
                "name": str(item["name"]),
                "display_name": display_name,
                "anchor": anchor,
                "definition": str(item["definition"]),
                "include": str(item["include"]),
                "exclude": str(item["exclude"]),
                "anchors": list(item.get("anchors") or []),
                "source_leaves": str(item.get("source_leaves") or ""),
                "rationale": str(item["rationale"]),
                "aliases": [],
            }
        )
        key_map[str(item["type_id"])] = stable_key

    vocabulary = build_vocabulary(base_vocabulary, rows, nodes)
    definitions = {row["id"]: row for row in rows}
    for term in vocabulary["terms"]:
        if term["id"] in definitions:
            row = definitions[term["id"]]
            term.update(
                name=row["display_name"],
                origin=source_id,
                retrieval_hints=[row["name"], *row["anchors"]],
            )
    vocabulary["revision"] = taxonomy_revision

    payload = dict(base_payload)
    payload["release_id"] = release_id
    payload["taxonomy_revision"] = taxonomy_revision
    payload["predecessor_release_id"] = base_payload["release_id"]
    node_list = [dict(node) for node in base_payload["core_nodes"]]
    dispositions = [dict(item) for item in base_payload["fine_term_dispositions"]]
    mappings = [dict(item) for item in base_payload["mappings"]]
    relations = [dict(item) for item in base_payload["relations"]]

    for row in rows:
        anchors_note = (
            f"锚题 {'；'.join(row['anchors'])}。" if row["anchors"] else ""
        )
        node_list.append(
            {
                "stable_key": row["id"],
                "display_name": row["display_name"],
                "aliases": [],
                "node_kind": "core",
                "status": "active",
                "definition": row["definition"],
                "include_scope": row["include"],
                "exclude_scope": row["exclude"],
                "curriculum_anchors": [row["anchor"]],
                "observable_evidence": f"{row['definition']}{anchors_note}",
                "rationale": f"{row['rationale']}来源叶子：{row['source_leaves']}。",
                "evidence_source_ids": [source_id],
            }
        )
        dispositions.append(
            {
                "fine_term_id": row["id"],
                "display_name": row["display_name"],
                "disposition": "direct_core",
                "definition": row["definition"],
                "include_scope": row["include"],
                "exclude_scope": row["exclude"],
                "curriculum_anchors": [row["anchor"]],
                "rationale": (
                    "题型由教师批准的章节词表逐条定义，一对一映射到同名题型节点。"
                ),
                "confidence": 0.9,
                "review_priority": "normal",
                "evidence_source_ids": [source_id],
            }
        )
        mappings.append(
            {
                "fine_term_id": row["id"],
                "stable_key": row["id"],
                "mapping_role": "primary",
                "rationale": "题型词一对一映射到同名题型节点。",
            }
        )
        relations.append(
            {
                "relation_key": "",
                "source_key": row["id"],
                "target_key": row["section_key"],
                "relation_type": "parent",
                "basis_kind": "empirical_evidence",
                "strength": "required",
                "rationale": (
                    f"题型“{row['name']}”由教师批准的章节词表定义，挂靠本节。"
                ),
                "evidence_source_ids": [source_id],
                "source_locator": source_locator,
            }
        )

    for relation in relations:
        relation["relation_key"] = stable_record_hash(
            "release-relation",
            release_id,
            relation["source_key"],
            relation["target_key"],
            relation["relation_type"],
        )
    payload["core_nodes"] = node_list
    payload["fine_term_dispositions"] = dispositions
    payload["mappings"] = mappings
    payload["relations"] = relations

    seen: set[str] = set()
    deduped = []
    for source in payload.get("sources", []):
        sid = str(source.get("source_id") or "")
        if sid in seen:
            continue
        seen.add(sid)
        deduped.append(source)
    payload["sources"] = deduped + [
        {
            "source_id": source_id,
            "kind": "question_type_vocabulary",
            "title": TYPE_SOURCE_KIND_TITLE,
            "reference": source_locator,
        }
    ]
    payload["content_hash"] = compute_content_hash(payload)
    return payload, vocabulary, key_map


def _normalized(value: object) -> str:
    return "".join(str(value or "").split()).casefold()


def _key_group(stable_key: str) -> str:
    key = str(stable_key or "")
    if key.startswith("sk_"):
        return "sk_"
    if key.startswith("ki_"):
        return "ki_"
    if key.startswith("kp_"):
        return "kp_type" if _TYPE_KEY.search(key) else "kp_"
    return "other"


def apply_type_release(
    db_path: Path,
    payload: Mapping[str, Any],
    vocabulary: Mapping[str, Any],
    labels: Mapping[int, Mapping[str, Any]],
    key_map: Mapping[str, str],
) -> dict[str, Any]:
    """Stage+activate the candidate on ``db_path`` (a copy) and carry links.

    Mirrors build_release_v5.apply_v5: every non-deleted question's current
    evidence version keeps its effective links re-resolved by identity under
    the new release; labeled questions additionally get one ``direct`` link to
    their primary type's stable key on every evidence point, then each point's
    direct weights are renormalized to sum 1.
    """
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.database.schema import connect
    from question_bank.knowledge_graph_release.repository import (
        activate_release,
        active_release_id,
        rollback_release,
        stage_release,
    )
    from question_bank.services.question_write_service import (
        refresh_derived_ownership_tags,
    )
    from question_bank.solution_evidence.knowledge_links import (
        load_point_links,
        refresh_question_scope_summary,
        replace_point_links,
    )

    previous = active_release_id(db_path)
    candidate = KnowledgeGraphRelease.from_mapping(payload)
    if previous != payload["predecessor_release_id"]:
        raise ValueError("活动版本与前置版本不符，请重新预演；未修改数据库。")
    resolver = CurrentKnowledgeResolver(candidate, vocabulary)
    counts: Counter[str] = Counter()
    before_groups: Counter[str] = Counter()
    after_groups: Counter[str] = Counter()
    plans = []
    current_versions: dict[int, str] = {}
    typed_questions: dict[int, str] = {}
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT * FROM (
            SELECT v.*, ROW_NUMBER() OVER(PARTITION BY v.question_id ORDER BY v.created_at DESC, v.rowid DESC) AS seq
            FROM question_solution_evidence_versions v JOIN questions q ON q.id=v.question_id
            WHERE v.status IN ('approved','proposed') AND q.is_deleted=0
        ) WHERE seq=1"""
        ).fetchall()
        grouped = load_point_links(
            db_path, [r["evidence_version_id"] for r in rows], previous, connection=conn
        )
        all_ids = [r[0] for r in conn.execute("SELECT id FROM questions WHERE is_deleted=0")]
        for row in rows:
            qid = int(row["question_id"])
            version = str(row["evidence_version_id"])
            current_versions[qid] = version
            label = labels.get(qid)
            type_key = ""
            if label and str(label["primary_type"]) != "NONE":
                type_key = str(key_map.get(str(label["primary_type"])) or "")
            if type_key:
                typed_questions[qid] = type_key
            points = []
            old = grouped.get(version, {})
            for part in json.loads(row["evidence_json"]).get("parts", []):
                for point in part.get("evidence_points", []):
                    pid = point["evidence_point_id"]
                    link_weights: dict[tuple[str, str], float] = {}
                    for link in old.get(pid, ()):
                        if link.resolution_status != "resolved":
                            continue
                        for identity in resolver.resolve(link.stable_key):
                            key = (link.role, identity.stable_key)
                            link_weights[key] = link_weights.get(key, 0.0) + link.weight
                    for (role, key), weight in link_weights.items():
                        if weight > 0:
                            before_groups[f"{role}/{_key_group(key)}"] += 1
                    if type_key:
                        key = ("direct", type_key)
                        link_weights[key] = link_weights.get(key, 0.0) + 1.0
                    total = sum(
                        weight
                        for (role, _), weight in link_weights.items()
                        if role == "direct"
                    )
                    links = [
                        {
                            "term_id": key,
                            "stable_key": key,
                            "role": role,
                            "weight": weight / total if role == "direct" and total else weight,
                        }
                        for (role, key), weight in link_weights.items()
                        if weight > 0
                    ]
                    for link in links:
                        after_groups[f"{link['role']}/{_key_group(link['stable_key'])}"] += 1
                    points.append(
                        {
                            "part_id": part["part_id"],
                            "evidence_point_id": pid,
                            "links": links,
                        }
                    )
                    counts["points"] += 1
            plans.append((qid, version, points))
    stage_release(
        db_path,
        candidate,
        actor_ref=ACTOR,
        source_reference=str(payload.get("release_id")),
        taxonomy_catalog=vocabulary,
    )
    activate_release(
        db_path,
        candidate.release_id,
        expected_active_release_id=previous,
        actor_ref=ACTOR,
        reason=REASON,
    )
    try:
        with connect(db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            for qid, version, points in plans:
                counts["links_written"] += replace_point_links(
                    conn,
                    evidence_version_id=version,
                    question_id=qid,
                    graph_release_id=candidate.release_id,
                    points=points,
                    source_reference="question_type_vocab_2026_10",
                )
            for qid in all_ids:
                refresh_question_scope_summary(conn, qid)
                refresh_derived_ownership_tags(conn, qid)
            for qid in all_ids:
                label = labels.get(qid)
                if not label:
                    continue
                for type_id in label["secondary_types"]:
                    type_key = str(key_map.get(str(type_id)) or "")
                    if not type_key:
                        continue
                    counts["secondary_tags_written"] += conn.execute(
                        "INSERT INTO question_tags(question_id,tag_type,tag_value,confidence,source) "
                        "SELECT ?,?,?,1.0,'taxonomy' WHERE NOT EXISTS("
                        "SELECT 1 FROM question_tags WHERE question_id=? "
                        "AND tag_type='secondary_type' AND tag_value=?)",
                        (qid, 'secondary_type', type_key, qid, type_key)).rowcount
            counts["duplicate_tag_groups_after"] = conn.execute(
                """SELECT COUNT(*) FROM (
                SELECT question_id,tag_type,tag_value FROM question_tags GROUP BY question_id,tag_type,tag_value HAVING COUNT(*)>1
            )"""
            ).fetchone()[0]
            if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("结转后引用关系校验未通过")
    except Exception:
        rollback_release(
            db_path,
            previous,
            expected_active_release_id=candidate.release_id,
            actor_ref=ACTOR,
            reason="链接结转事务失败，恢复原活动版本",
        )
        raise
    return {
        "previous_release": previous,
        "new_release": candidate.release_id,
        "questions_with_evidence": len(plans),
        "questions_refreshed": len(all_ids),
        "questions_with_type_link": len(typed_questions),
        "type_links_written": sum(after_groups[k] for k in after_groups if k.endswith("/kp_type")),
        "links_written": counts["links_written"],
        "points": counts["points"],
        "duplicate_tag_groups_after": counts["duplicate_tag_groups_after"],
        "secondary_tags_written": counts["secondary_tags_written"],
        "current_versions": current_versions,
        "typed_questions": typed_questions,
        "before_groups": dict(before_groups),
        "after_groups": dict(after_groups),
        "model_calls": 0,
    }


def build_preview_report(
    copy_db: Path,
    payload: Mapping[str, Any],
    vocabulary: Mapping[str, Any],
    labels: Mapping[int, Mapping[str, Any]],
    key_map: Mapping[str, str],
    apply_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Report-only checks on the preview copy after apply."""
    from question_bank.current_knowledge import (
        CurrentKnowledgeResolver,
        CurrentKnowledgeUnavailable,
    )
    from question_bank.database.schema import connect

    type_keys = set(key_map.values())
    type_node_rows = [
        node
        for node in payload["core_nodes"]
        if str(node["stable_key"]) in type_keys
    ]
    type_relation_rows = [
        relation
        for relation in payload["relations"]
        if str(relation["source_key"]) in type_keys
        and relation["relation_type"] == "parent"
    ]
    candidate = KnowledgeGraphRelease.from_mapping(payload)
    validation = validate_release(candidate, vocabulary)

    non_none = {
        qid
        for qid, label in labels.items()
        if str(label["primary_type"]) != "NONE"
    }
    current_versions = apply_result["current_versions"]
    typed_questions = apply_result["typed_questions"]
    missing_current = sorted(qid for qid in labels if qid not in current_versions)

    per_chapter: dict[str, dict[str, int]] = {}
    for qid in non_none:
        chapter = str(labels[qid]["primary_type"]).split("-", 1)[0].lstrip("T")
        bucket = per_chapter.setdefault(
            chapter, {"labeled_non_none": 0, "type_linked": 0}
        )
        bucket["labeled_non_none"] += 1
        if qid in typed_questions:
            bucket["type_linked"] += 1

    before = dict(apply_result["before_groups"])
    after = dict(apply_result["after_groups"])
    carry = {
        key: {"before": before.get(key, 0), "after": after.get(key, 0)}
        for key in sorted(set(before) | set(after))
    }
    lossless = all(
        after.get(key, 0) == before.get(key, 0)
        for key in set(before) | set(after)
        if not key.endswith("/kp_type")
    )

    with connect(copy_db) as conn:
        active = conn.execute(
            "SELECT release_id FROM knowledge_graph_releases WHERE status = 'active'"
        ).fetchone()
        active_release = str(active["release_id"]) if active else ""
        db_type_links = conn.execute(
            f"""SELECT COUNT(DISTINCT question_id) FROM evidence_point_knowledge_links
            WHERE graph_release_id = ? AND role = 'direct'
            AND stable_key IN ({','.join('?' for _ in type_keys)})""",
            (str(payload["release_id"]), *sorted(type_keys)),
        ).fetchone()[0]
        fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        sample_ids = sorted(typed_questions)
        samples = []
        for qid in (sample_ids[0], sample_ids[len(sample_ids) // 2], sample_ids[-1]):
            tags = [
                str(row[0])
                for row in conn.execute(
                    "SELECT tag_value FROM question_tags "
                    "WHERE question_id = ? AND tag_type = 'knowledge_point' "
                    "ORDER BY tag_value",
                    (qid,),
                )
            ]
            samples.append(
                {
                    "question_id": qid,
                    "primary_type": labels[qid]["primary_type"],
                    "knowledge_point_tags": tags,
                }
            )

    resolver_note = "from_active_database"
    resolver_detail = ""
    resolver_missing: list[str] = []
    resolver_ok = False
    try:
        resolver = CurrentKnowledgeResolver.from_active_database(copy_db)
    except CurrentKnowledgeUnavailable as exc:
        resolver_detail = (
            f"bare from_active_database failed ({exc}); "
            "revision not bundled in loader.py, retried with candidate catalog"
        )
        try:
            resolver = CurrentKnowledgeResolver.from_active_database(
                copy_db, taxonomy_catalog=vocabulary
            )
            resolver_note = "from_active_database(taxonomy_catalog=candidate)"
        except CurrentKnowledgeUnavailable as exc2:
            resolver_detail = f"{resolver_detail}; {exc2}"
            resolver = None
    if resolver is not None:
        resolver_missing = [
            key for key in sorted(type_keys) if not resolver.resolve(key)
        ]
        resolver_ok = not resolver_missing

    return {
        "active_release_id": active_release,
        "previous_release_id": apply_result["previous_release"],
        "validate_release": {
            "valid": validation.valid,
            "errors": len(validation.errors),
            "warnings": len(validation.warnings),
        },
        "type_nodes": len(type_node_rows),
        "type_parent_relations": len(type_relation_rows),
        "total_nodes": len(payload["core_nodes"]),
        "total_relations": len(payload["relations"]),
        "questions_with_type_link_db": int(db_type_links),
        "questions_with_type_link_planned": len(typed_questions),
        "labeled_non_none": len(non_none),
        "labeled_non_none_with_current_version": len(
            [qid for qid in non_none if qid in current_versions]
        ),
        "labeled_missing_current_version": missing_current,
        "per_chapter": per_chapter,
        "links_by_role_prefix": carry,
        "carry_forward": {
            "skill_links_before": before.get("direct/sk_", 0)
            + before.get("supporting_prerequisite/sk_", 0),
            "skill_links_after": after.get("direct/sk_", 0)
            + after.get("supporting_prerequisite/sk_", 0),
            "kp_links_before": before.get("direct/kp_", 0)
            + before.get("supporting_prerequisite/kp_", 0),
            "kp_links_after": after.get("direct/kp_", 0)
            + after.get("supporting_prerequisite/kp_", 0),
            "type_links_added": after.get("direct/kp_type", 0),
            "non_type_links_lossless": lossless,
        },
        "resolver_check": {
            "method": resolver_note,
            "ok": resolver_ok,
            "unresolved_type_keys": resolver_missing,
            "detail": resolver_detail,
        },
        "sample_questions": samples,
        "duplicate_tag_groups": apply_result["duplicate_tag_groups_after"],
        "foreign_key_check": "ok" if not fk_rows else f"{len(fk_rows)} violations",
        "links_written": apply_result["links_written"],
        "points": apply_result["points"],
        "questions_refreshed": apply_result["questions_refreshed"],
        "secondary_types_written_to_db": apply_result["secondary_tags_written"],
        "type_link_weight_note": (
            "题型直接链接以初始权重 1.0 加入每个判定点，随后按 apply_v5 规则"
            "把该点全部 direct 权重归一化为总和 1。"
        ),
    }


def copy_database(source_db: Path, target_db: Path) -> None:
    """Copy ``source_db`` into a fresh ``target_db`` via the SQLite backup API."""
    target_db = Path(target_db)
    for suffix in ("", "-wal", "-shm"):
        if Path(str(target_db) + suffix).exists():
            raise ValueError("目标数据库或附属文件已存在，拒绝覆盖。")
    target_db.parent.mkdir(parents=True, exist_ok=True)
    with (
        closing(sqlite3.connect(Path(source_db).resolve().as_uri() + "?mode=ro", uri=True)) as source,
        closing(sqlite3.connect(str(target_db))) as target,
    ):
        source.backup(target)


def _check_type_application(db_path: Path, labels, result) -> dict[str, bool]:
    """Check the same link-preservation and database rules in both modes."""
    from tools.maintain_question_bank import verify_database

    expected = {qid for qid, label in labels.items() if label["primary_type"] != "NONE"}
    if expected != set(result["typed_questions"]):
        raise ValueError("有已标题型的题缺少当前判定版本，预演未通过。")
    before, after = result["before_groups"], result["after_groups"]
    if any(before.get(key, 0) != after.get(key, 0)
           for key in set(before) | set(after) if not key.endswith("/kp_type")):
        raise ValueError("原技能或知识点链接数量变化，预演未通过。")
    if result["duplicate_tag_groups_after"]:
        raise ValueError("题目标签存在重复，预演未通过。")
    return verify_database(db_path)


def activate_type_release(db_path: Path, backup_dir: Path, payload, vocabulary,
                          labels, key_map, *, skip_unavailable: bool = False) -> dict[str, Any]:
    """Back up, rehearse and apply an explicitly authorized registered release.

    A rehearsal failure leaves the source unchanged and keeps the backup.
    Link-transaction failure uses apply_type_release's existing v8 rollback;
    the backup also remains available for a separately reviewed full restore.
    """
    from tools.maintain_question_bank import readonly
    from question_bank.solution_evidence.part_assessments import load_profiles

    backup_dir = Path(backup_dir)
    backup = backup_dir / f"question_bank_before_{payload['release_id']}_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
    with closing(readonly(db_path)) as source:
        generation = source.execute("PRAGMA data_version").fetchone()[0]
        data_root = db_path.parent.parent if db_path.parent.name == "databases" else db_path.parent
        labeled_ids = [qid for qid, row in labels.items() if row["primary_type"] != "NONE"]
        profiles = load_profiles(db_path, labeled_ids, connection=source,
                                 verify_source=True, data_root=data_root)
        missing = set(labeled_ids) - profiles.keys()
        if missing:
            raise ValueError("有已标题型的题缺少当前有效判定资料；未备份或启用。")
        unavailable = {qid for qid in labeled_ids if not profiles[qid]["available"]}
        if unavailable and not skip_unavailable:
            raise ValueError(f"{len(unavailable)} 道已标题型的题缺少当前有效判定资料，请先处理来源变化；未备份或启用。")
        # Exclude invalid sources from new type annotations, preserving their
        # versions and legacy links. This never turns stale material current.
        effective_labels = {qid: label for qid, label in labels.items()
                            if qid not in unavailable}
        backup_dir.mkdir(parents=True, exist_ok=True)
        copy_database(db_path, backup)
        with tempfile.TemporaryDirectory(prefix="TEST-type-release-", dir=backup_dir) as folder:
            rehearsal_db = Path(folder) / "question_bank.db"
            copy_database(backup, rehearsal_db)
            rehearsal = apply_type_release(rehearsal_db, payload, vocabulary, effective_labels, key_map)
            _check_type_application(rehearsal_db, effective_labels, rehearsal)
        if source.execute("PRAGMA data_version").fetchone()[0] != generation:
            raise ValueError("预演期间题库已变化，未启用；请停止应用后重新预演。")
    result = apply_type_release(db_path, payload, vocabulary, effective_labels, key_map)
    result.update(_check_type_application(db_path, effective_labels, result))
    result.update(backup_name=backup.name, rehearsal_passed=True, applied=True,
                  skipped_unavailable_questions=len(unavailable))
    return result


def _release_id_for(base_id: str) -> str:
    trailing = re.search(r"_v(\d+)$", str(base_id))
    return (
        f"kgr_bnu_math_curriculum_{datetime.now():%Y_%m}_v{int(trailing.group(1)) + 1}"
        if trailing
        else f"{base_id}_next"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--vocab-dir", type=Path, default=DEFAULT_VOCAB_DIR)
    parser.add_argument("--labels-dir", type=Path, default=DEFAULT_LABELS_DIR)
    parser.add_argument("--catalog-dir", type=Path, default=CATALOG_DIR)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--write", action="store_true",
                        help="write the release + vocabulary into --catalog-dir "
                             "and print the loader map lines (no DB writes)")
    database_mode = parser.add_mutually_exclusive_group()
    database_mode.add_argument("--preview-db", type=Path)
    database_mode.add_argument("--activate-and-carry-db", type=Path,
                               help="仅供已获本次真实数据操作授权后使用")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--skip-unavailable", action="store_true",
                        help="启用时仅新增当前资料可用题的题型关联，来源失效题保持待处理")
    args = parser.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        parser.error(f"题库数据库不存在：{db}")
    real_db = Path(DEFAULT_DB).resolve()
    out_dir = Path(args.out_dir) if args.out_dir else None
    preview_db = Path(args.preview_db) if args.preview_db else None
    activation_db = Path(args.activate_and_carry_db) if args.activate_and_carry_db else None
    if args.skip_unavailable and activation_db is None:
        parser.error("--skip-unavailable 仅用于已授权的受控启用。")
    if activation_db is not None:
        if args.write or out_dir is not None:
            parser.error("启用数据库与 --write/--out-dir 分开执行。")
        if not args.backup_dir:
            parser.error("--activate-and-carry-db 需要 --backup-dir。")
        if os.path.normcase(str(activation_db.resolve())) != os.path.normcase(str(db.resolve())):
            parser.error("启用目标必须与 --db 是同一个数据库，不能借用其他题库的标注。")
    if preview_db is not None:
        resolved = preview_db.resolve()
        if os.path.normcase(str(resolved)) in {
            os.path.normcase(str(real_db)),
            os.path.normcase(str(db.resolve())),
        }:
            parser.error("--preview-db 必须指向数据库副本，拒绝真实题库路径。")
        if preview_db.exists():
            parser.error(f"预演副本已存在，先删除再运行：{preview_db}")
        if out_dir is None:
            out_dir = preview_db.parent.parent

    types = load_type_vocabulary(Path(args.vocab_dir))
    labels = load_labels(Path(args.labels_dir))
    type_ids = {item["type_id"] for item in types}
    unknown = sorted(
        {
            value
            for label in labels.values()
            for value in [label["primary_type"], *label["secondary_types"]]
            if value != "NONE"
        }
        - type_ids
    )
    if unknown:
        parser.error(f"标注引用了词表外的题型：{', '.join(unknown[:10])}")

    base_payload, base_vocabulary, bundled = base_release(db)
    release_no, vocab_no = next_version_numbers(Path(args.catalog_dir))
    revision = int(bundled.taxonomy_revision) + 1
    release_id = _release_id_for(bundled.release_id)
    payload, vocabulary, key_map = build_type_release(
        base_payload,
        base_vocabulary,
        types,
        release_id=release_id,
        taxonomy_revision=revision,
    )
    report = validate_release(KnowledgeGraphRelease.from_mapping(payload), vocabulary)
    print(
        f"release {release_id}: revision={revision} types={len(types)} "
        f"nodes={len(payload['core_nodes'])} relations={len(payload['relations'])} "
        f"errors={len(report.errors)} warnings={len(report.warnings)}"
    )
    for item in types[:5]:
        print(f"  + {item['type_id']} 「{item['name']}」 → {item['stable_key']}")
    if len(types) > 5:
        print(f"  … 共 {len(types)} 个题型")
    for issue in report.errors[:20]:
        print(f"  ERROR {issue.path}: {issue.message}")
    for issue in report.warnings[:20]:
        print(f"  WARN  {issue.path}: {issue.message}")
    if not report.valid:
        return 1

    if activation_db is not None:
        from question_bank.knowledge_graph_release.loader import load_taxonomy_catalog_for_release

        registered = registered_release(revision)
        if (registered.release_id != release_id
                or registered.content_hash != compute_content_hash(payload)
                or load_taxonomy_catalog_for_release(registered) != vocabulary):
            parser.error("候选标准或词表与已登记文件不一致，请重新预演并登记；未修改数据库。")
        result = activate_type_release(activation_db, args.backup_dir, payload,
                                       vocabulary, labels, key_map,
                                       skip_unavailable=args.skip_unavailable)
        public_result = {key: value for key, value in result.items()
                         if key not in {"current_versions", "typed_questions"}}
        print(json.dumps(public_result, ensure_ascii=False, indent=2))
        return 0

    secondary = {
        str(qid): [key_map[value] for value in label["secondary_types"]]
        for qid, label in sorted(labels.items())
        if label["secondary_types"]
    }

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_json(out_dir / f"knowledge_graph_release_v{release_no}.json", payload)
        _write_json(out_dir / f"tag_vocabulary_v{vocab_no}.json", vocabulary)
        _write_json(out_dir / "type_key_map.json", key_map)
        _write_json(out_dir / "secondary_types.json", secondary)
        print(f"wrote candidate JSONs under {out_dir}")

    if args.write:
        catalog_dir = Path(args.catalog_dir)
        catalog_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(catalog_dir.glob("knowledge_graph_release_v*.json")):
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if str(existing.get("release_id")) == release_id:
                print(f"catalog 已包含 {release_id}：{path.name}（跳过写入）")
                return 0
        release_path = catalog_dir / f"knowledge_graph_release_v{release_no}.json"
        vocab_path = catalog_dir / f"tag_vocabulary_v{vocab_no}.json"
        for path in (release_path, vocab_path):
            if path.exists():
                raise SystemExit(f"拒绝覆盖已有目录文件：{path}")
        _write_json(release_path, payload)
        _write_json(vocab_path, vocabulary)
        print(f"wrote {release_path}")
        print(f"wrote {vocab_path}")
        print("loader mapping:")
        print(f'        "taxonomy_revision": {revision},')
        print(f'        "release": "knowledge_graph_release_v{release_no}.json",')
        print(f'        "vocabulary": "tag_vocabulary_v{vocab_no}.json",')
        if preview_db is None:
            return 0

    if preview_db is not None:
        copy_database(db, preview_db)
        apply_result = apply_type_release(
            preview_db, payload, vocabulary, labels, key_map
        )
        preview_report = build_preview_report(
            preview_db, payload, vocabulary, labels, key_map, apply_result
        )
        preview_report["apply_result"] = {
            key: value
            for key, value in apply_result.items()
            if key not in {"current_versions", "typed_questions"}
        }
        _write_json(out_dir / "preview_report.json", preview_report)
        print(json.dumps(preview_report["apply_result"], ensure_ascii=False, indent=2))
        print(f"wrote {out_dir / 'preview_report.json'}")
        print(f"预览副本保留在 {preview_db}")
        return 0

    if out_dir is None:
        print("dry-run；--out-dir 生成候选 JSON，--preview-db 在副本上预演应用；真实启用需单独授权。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
