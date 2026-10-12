"""Build and publish the 2026-10 key-step knowledge release (one-off tool).

Builds ``kgr_bnu_math_curriculum_2026_10_v11`` on top of the v9 vocabulary:
key-step renames/merges/retirements, the 3_3_201 split, new g8/g7 key steps,
single-primary-key-step point decisions and carry-forward of existing links.

``--rehearse`` copies the question-bank database into a temporary ``test_``
directory and runs the full flow on the copy only. ``--publish`` performs the
same flow on the real database after writing timestamped backups under
``backups/``; run it only with explicit authorization.
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from question_bank.atomic_files import write_json_atomic
from question_bank.current_knowledge import CurrentKnowledgeResolver
from question_bank.knowledge_graph_release.contracts import (
    KnowledgeGraphRelease,
    compute_content_hash,
)
from question_bank.knowledge_graph_release.repository import (
    activate_release,
    load_active_release,
    stage_release,
)
from question_bank.knowledge_graph_release.loader import load_taxonomy_catalog_for_release
from question_bank.knowledge_graph_release.validation import validate_release
from question_bank.knowledge_graph_release.contracts import stable_record_hash
from question_bank.services.question_write_service import refresh_derived_ownership_tags
from question_bank.solution_evidence.knowledge_links import (
    load_point_links,
    refresh_question_scope_summary,
)
from question_bank.taxonomy.attribute_definitions import enrich_attribute_definitions

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "question_bank" / "taxonomy" / "catalogs" / "key_steps_2026_10"
BASE_RELEASE_ID = "kgr_bnu_math_curriculum_2026_10_v9"
RELEASE_ID = "kgr_bnu_math_curriculum_2026_10_v11"
PREDECESSOR = BASE_RELEASE_ID
SOURCE_ID = "key_step_vocabulary_2026_10"
SOURCE_REFERENCE = "key_step_release_2026_10"
SK_PREFIX = "sk_bnu24_math_"
KP_PREFIX = "kp_bnu24_math_"
LINK_JOB = "link_job"
DECISION_VOLUME_CODES = {"g7l_": "g7_lower_", "g7u_": "g7_upper_"}
G7_FILES = (
    ("g7_upper_a.json", "g7_upper"),
    ("g7_upper_b.json", "g7_upper"),
    ("g7_lower.json", "g7_lower"),
)


class KeyStepReleaseError(RuntimeError):
    pass


def _sk(code: str) -> str:
    return SK_PREFIX + code


def _full_code(code: str) -> str | None:
    code = code.strip()
    if code in {"KEEP", "NONE", ""}:
        return None
    for prefix, volume in DECISION_VOLUME_CODES.items():
        if code.startswith(prefix):
            return SK_PREFIX + volume + code[len(prefix):]
    return SK_PREFIX + "g8_upper_" + code


def _load_json(name: str, catalog_dir: Path) -> Any:
    return json.loads((catalog_dir / name).read_text(encoding="utf-8"))


def _decision_rows(text: str) -> dict[tuple[int, str], str]:
    rows: dict[tuple[int, str], str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        q, pid, code = line.split()
        key = (int(q), str(pid))
        if key in rows:
            raise KeyStepReleaseError(f"duplicate decision row {key}")
        rows[key] = code
    return rows


def _load_inputs(catalog_dir: Path) -> dict[str, Any]:
    changes = _load_json("changes.json", catalog_dir)
    new_skills: list[dict[str, Any]] = []
    for entry in changes.get("new_g8_upper", []):
        new_skills.append({"volume": "g8_upper", "key": entry["key"],
                           "name": entry["name"], "def": entry["def"],
                           "inc": entry["inc"], "exc": entry["exc"], "obs": entry["obs"]})
    for filename, _volume in G7_FILES:
        doc = _load_json(filename, catalog_dir)
        for entry in doc.get("entries", []):
            new_skills.append({"volume": doc["volume"], **entry})
    decisions: dict[tuple[int, str], str] = {}
    for name in ("primary_multi.txt", "gap_decisions_1.txt", "gap_decisions_2.txt",
                 "gap_decisions_3.txt", "split_3_3_201.txt"):
        for key, code in _decision_rows(
                (catalog_dir / name).read_text(encoding="utf-8")).items():
            if key in decisions:
                raise KeyStepReleaseError(f"decision conflict at {key}")
            decisions[key] = code
    multi = _decision_rows((catalog_dir / "primary_multi.txt").read_text(encoding="utf-8"))
    gaps: dict[tuple[int, str], str] = {}
    for name in ("gap_decisions_1.txt", "gap_decisions_2.txt", "gap_decisions_3.txt"):
        for key, code in _decision_rows(
                (catalog_dir / name).read_text(encoding="utf-8")).items():
            gaps[key] = code
    split = _decision_rows((catalog_dir / "split_3_3_201.txt").read_text(encoding="utf-8"))
    versions = {
        int(qid): str(ver)
        for qid, ver in _load_json("evidence_versions.json", catalog_dir)["versions"].items()
    }
    rename_map = {_sk(e["old"]): _sk(e["new"]) for e in changes["renames"]}
    merge_map = {_sk(e["old"]): _sk(e["into"]) for e in changes["merges"]}
    retire_set = {_sk(e) for e in changes["retire_to_section"]}
    merge_kinds = {_sk(e["old"]): e["kind"] for e in changes["merges"]}
    key_map = {**rename_map, **merge_map}
    return {
        "changes": changes,
        "new_skills": new_skills,
        "decisions": decisions,
        "multi": multi,
        "gaps": gaps,
        "split": split,
        "versions": versions,
        "rename_map": rename_map,
        "merge_map": merge_map,
        "key_map": key_map,
        "retire_set": retire_set,
        "merge_kinds": merge_kinds,
    }


def _map_sk_keys(keys: list[str], inputs: dict[str, Any]) -> list[str]:
    """Rename/merge-map a key list and drop retired keys, deduplicated."""
    out: list[str] = []
    for key in keys:
        mapped = inputs["key_map"].get(key, key)
        if mapped in inputs["retire_set"] or mapped in out:
            continue
        out.append(mapped)
    return out


def _question_points(
    conn: sqlite3.Connection,
    db_path: Path,
    versions: dict[int, str],
    release_id: str,
) -> dict[tuple[int, str], dict[str, Any]]:
    """Collect per-point context for the versions in ``versions``.

    Returns ``{(question_id, point_id): {part_id, mode, sk}}`` where ``sk`` is
    the currently visible direct ``sk_`` link list (base-release view).
    """
    versions_list = sorted(set(versions.values()))
    link_map = load_point_links(db_path, versions_list, release_id, connection=conn)
    points: dict[tuple[int, str], dict[str, Any]] = {}
    by_version: dict[str, list[int]] = defaultdict(list)
    for qid, version in versions.items():
        by_version[version].append(qid)
    for version, qids in by_version.items():
        rows = conn.execute(
            """
            SELECT question_id, evidence_json
            FROM question_solution_evidence_versions
            WHERE evidence_version_id = ?
            """,
            (version,),
        ).fetchall()
        if len(rows) != len(set(qids)):
            raise KeyStepReleaseError(f"evidence version {version} row mismatch")
        for row in rows:
            qid = int(row["question_id"])
            payload = json.loads(row["evidence_json"])
            for part in payload.get("parts", []):
                part_id = str(part.get("part_id") or "")
                mode = str(part.get("response_mode") or "")
                for point in part.get("evidence_points", []):
                    pid = str(point.get("evidence_point_id") or "")
                    visible = link_map.get(version, {}).get(pid, ())
                    sk = [
                        str(link.stable_key) for link in visible
                        if link.role == "direct"
                        and link.resolution_status == "resolved"
                        and str(link.stable_key).startswith(SK_PREFIX)
                    ]
                    points[(qid, pid)] = {
                        "part_id": part_id,
                        "mode": mode,
                        "sk": sk,
                    }
    return points


def synthesize_final_links(
    conn: sqlite3.Connection,
    db_path: Path,
    inputs: dict[str, Any],
    active_keys: set[str],
) -> dict[tuple[int, str], str | None]:
    """Recompute the final direct key step per evidence point.

    Same rules as ``tmp_skill_audit_20261011/check_decisions.py``: objective
    points get none; mapped keys are renamed/merged and retired keys dropped;
    >=2 keys need a primary_multi decision, 0 need a gap decision, 1 keeps or
    follows the split file. Missing decisions or unknown keys fail the build.
    """
    points = _question_points(conn, db_path, inputs["versions"], BASE_RELEASE_ID)
    final: dict[tuple[int, str], str | None] = {}
    missing: list[tuple[int, str]] = []
    unknown: list[str] = []
    for (qid, pid), point in sorted(points.items()):
        if point["mode"] == "exact_objective":
            final[(qid, pid)] = None
            continue
        mapped = _map_sk_keys(point["sk"], inputs)
        key = (qid, pid)
        if len(mapped) >= 2:
            code = inputs["multi"].get(key)
            if code is None:
                missing.append(key)
                continue
            target = _full_code(code)
        elif not mapped:
            code = inputs["gaps"].get(key)
            if code is None:
                missing.append(key)
                continue
            target = _full_code(code)
        else:
            code = inputs["split"].get(key)
            target = _full_code(code) if code is not None else mapped[0]
        if target is not None and target not in active_keys:
            unknown.append(f"{qid}|{pid}|{target}")
        final[key] = target
    problems = []
    if missing:
        problems.append(
            "missing decisions: " + "; ".join(f"{q}|{p}" for q, p in missing[:50]))
    if unknown:
        problems.append("inactive decision keys: " + "; ".join(unknown[:50]))
    if problems:
        raise KeyStepReleaseError(" | ".join(problems))
    return final


def _node_index(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(n["stable_key"]): n for n in nodes}


def _section_display(nodes: dict[str, dict[str, Any]], section_key: str) -> str:
    node = nodes.get(section_key)
    if node is None:
        raise KeyStepReleaseError(f"section node missing: {section_key}")
    return str(node["display_name"])


def _section_paths(section_display: str) -> list[str]:
    parts = [p.strip() for p in section_display.split("｜")]
    return parts[-2:] if len(parts) >= 2 else parts


def build_key_step_release(
    base: KnowledgeGraphRelease,
    catalog_dir: Path = DATA_DIR,
) -> tuple[KnowledgeGraphRelease, dict[str, Any], dict[str, Any]]:
    """Build the v11 release payload and governed vocabulary on top of v9."""
    inputs = _load_inputs(catalog_dir)
    vocabulary = enrich_attribute_definitions(load_taxonomy_catalog_for_release(base))
    taxonomy_revision = max(13, int(base.taxonomy_revision) + 1)

    payload = copy.deepcopy(base.payload)
    payload.pop("taxonomy_catalog", None)
    nodes = payload["core_nodes"]
    dispositions = payload["fine_term_dispositions"]
    mappings = payload["mappings"]
    relations = payload["relations"]
    replacements = payload["replacements"]
    node_idx = _node_index(nodes)
    disp_idx = {d["fine_term_id"]: d for d in dispositions}
    map_idx = {m["fine_term_id"]: m for m in mappings}
    parent_target = {
        r["source_key"]: r["target_key"]
        for r in relations if r["relation_type"] == "parent"
    }

    new_source = {
        "source_id": SOURCE_ID,
        "kind": "key_step_vocabulary",
        "title": "关键步骤词表 2026-10 审定版",
        "reference": "question_bank/taxonomy/catalogs/key_steps_2026_10/",
    }
    if SOURCE_ID not in {s["source_id"] for s in payload["sources"]}:
        payload["sources"].append(new_source)

    def _retire(old_key: str, target_key: str, kind: str, rationale: str) -> None:
        node = node_idx[old_key]
        node["status"] = "retired"
        disp = disp_idx[old_key]
        disp["disposition"] = "maps_to_core"
        mapping = map_idx[old_key]
        mapping["stable_key"] = target_key
        mapping["rationale"] = "关键步骤词表 2026-10 审定：旧键兼容上溯。"
        replacements.append({
            "retired_key": old_key,
            "replacement_key": target_key,
            "replacement_kind": kind,
            "rationale": rationale,
        })
        relations[:] = [r for r in relations if r["source_key"] != old_key]

    def _add_skill(full_key: str, name: str, definition: str, include: str,
                   exclude: str, observable: str, section_key: str,
                   source_ref: str) -> None:
        if full_key in node_idx:
            raise KeyStepReleaseError(f"new key already exists: {full_key}")
        if section_key not in node_idx:
            raise KeyStepReleaseError(f"section node missing: {section_key}")
        display = f"{_section_display(node_idx, section_key)}｜技能·{name}"
        anchors = [
            "/".join([*_section_paths(node_idx[section_key]["display_name"]), f"技能·{name}"])
        ]
        node = {
            "stable_key": full_key,
            "display_name": display,
            "node_kind": "skill",
            "status": "active",
            "definition": definition,
            "include_scope": include,
            "exclude_scope": exclude,
            "curriculum_anchors": anchors,
            "observable_evidence": observable,
            "rationale": "关键步骤词表 2026-10 审定新增。",
            "evidence_source_ids": [SOURCE_ID],
        }
        nodes.append(node)
        node_idx[full_key] = node
        dispositions.append({
            "fine_term_id": full_key,
            "display_name": display,
            "disposition": "direct_core",
            "definition": definition,
            "include_scope": include,
            "exclude_scope": exclude,
            "curriculum_anchors": anchors,
            "rationale": "关键步骤词表 2026-10 审定新增。",
            "confidence": 0.8,
            "review_priority": "normal",
            "evidence_source_ids": [SOURCE_ID],
        })
        mappings.append({
            "fine_term_id": full_key,
            "stable_key": full_key,
            "mapping_role": "primary",
            "rationale": "技能词一对一映射到同名技能节点。",
        })
        relations.append({
            "relation_key": stable_record_hash(
                "release-relation", RELEASE_ID, full_key, section_key, "parent"),
            "relation_type": "parent",
            "source_key": full_key,
            "target_key": section_key,
            "strength": "required",
            "basis_kind": "empirical_evidence",
            "rationale": "按可观察数学操作及教材小节归属定义。",
            "source_locator": source_ref,
            "evidence_source_ids": [SOURCE_ID],
        })
        vocabulary["terms"].append({
            "id": full_key,
            "dimension": "knowledge",
            "name": display,
            "aliases": [],
            "status": "approved",
            "origin": SOURCE_ID,
            "source_paths": [
                [*_section_paths(node_idx[section_key]["display_name"]), name]],
            "retrieval_hints": [
                name,
                *[s.strip() for s in str(observable).replace("；", ";").split(";")
                  if s.strip()][:1],
            ],
        })

    # definition-only updates (unchanged keys)
    for entry in inputs["changes"].get("definition_updates", []):
        full_key = _sk(entry["key"])
        node = node_idx.get(full_key)
        if node is None or node["status"] != "active":
            raise KeyStepReleaseError(f"definition update on inactive key: {full_key}")
        for src, dst in (("def", "definition"), ("inc", "include_scope"),
                         ("exc", "exclude_scope"), ("obs", "observable_evidence")):
            if entry.get(src):
                node[dst] = entry[src]
        disp = disp_idx[full_key]
        for src, dst in (("def", "definition"), ("inc", "include_scope"),
                         ("exc", "exclude_scope")):
            if entry.get(src):
                disp[dst] = entry[src]

    # renames: new key mirrors the old node, old key becomes retired + maps_to_core
    for entry in inputs["changes"]["renames"]:
        old_key, new_key = _sk(entry["old"]), _sk(entry["new"])
        node = node_idx.get(old_key)
        if node is None or node["status"] != "active":
            raise KeyStepReleaseError(f"rename source not active: {old_key}")
        section_key = parent_target.get(old_key)
        if not section_key:
            raise KeyStepReleaseError(f"rename source has no section parent: {old_key}")
        _add_skill(new_key, entry["name"], entry["def"], entry["inc"],
                   entry["exc"], entry["obs"], section_key,
                   "key_steps_2026_10/changes.json")
        _retire(old_key, new_key, "exact",
                "关键步骤词表 2026-10 审定改名：同义替换。")

    # merges
    for entry in inputs["changes"]["merges"]:
        old_key, target = _sk(entry["old"]), _sk(entry["into"])
        node = node_idx.get(old_key)
        if node is None or node["status"] != "active":
            raise KeyStepReleaseError(f"merge source not active: {old_key}")
        if node_idx.get(target, {}).get("status") != "active":
            raise KeyStepReleaseError(f"merge target not active: {target}")
        _retire(old_key, target, inputs["merge_kinds"][old_key],
                "关键步骤词表 2026-10 审定合并。")

    # retire_to_section
    for code in inputs["changes"]["retire_to_section"]:
        old_key = _sk(code)
        node = node_idx.get(old_key)
        if node is None or node["status"] != "active":
            raise KeyStepReleaseError(f"retire source not active: {old_key}")
        target = parent_target.get(old_key)
        if not target or node_idx.get(target, {}).get("status") != "active":
            raise KeyStepReleaseError(f"retire target not active: {old_key}")
        _retire(old_key, target, "broader",
                "关键步骤词表 2026-10 审定退役：判定点留在小节层。")

    # new skills
    for entry in inputs["new_skills"]:
        c, s, _n = entry["key"].split("_")
        section_key = f"{KP_PREFIX}{entry['volume']}_{c}_{s}"
        _add_skill(_sk(f"{entry['volume']}_{entry['key']}"), entry["name"],
                   entry["def"], entry["inc"], entry["exc"], entry["obs"],
                   section_key, "key_steps_2026_10/")

    vocabulary["revision"] = taxonomy_revision
    vocabulary["expected_approved_knowledge_count"] = sum(
        1 for t in vocabulary["terms"]
        if t.get("dimension") == "knowledge" and t.get("status") == "approved")
    vocabulary["taxonomy_catalog_note"] = None  # keep dict shape; removed below
    vocabulary.pop("taxonomy_catalog_note")

    payload["release_id"] = RELEASE_ID
    payload["taxonomy_revision"] = taxonomy_revision
    payload["predecessor_release_id"] = PREDECESSOR
    payload["taxonomy_catalog"] = vocabulary
    payload.pop("content_hash", None)
    candidate = KnowledgeGraphRelease.from_mapping(payload)
    return candidate, vocabulary, inputs


def _usable_version_map(
    conn: sqlite3.Connection, question_ids: set[int]
) -> dict[int, str]:
    rows = conn.execute(
        """
        SELECT question_id, evidence_version_id
        FROM question_solution_evidence_versions
        WHERE status IN ('proposed', 'approved')
        ORDER BY (status = 'approved') DESC, created_at DESC, rowid DESC
        """
    ).fetchall()
    usable: dict[int, str] = {}
    for row in rows:
        qid = int(row["question_id"])
        if qid in question_ids and qid not in usable:
            usable[qid] = str(row["evidence_version_id"])
    return usable


def _effective_group_rows(
    conn: sqlite3.Connection, version: str, wanted_release: str
) -> list[sqlite3.Row]:
    """Raw link rows of the release group a reader would select (v9 rules)."""
    rows = conn.execute(
        """
        SELECT * FROM evidence_point_knowledge_links
        WHERE evidence_version_id = ?
        """,
        (version,),
    ).fetchall()
    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        groups[str(row["graph_release_id"])].append(row)
    if not groups:
        return []
    if wanted_release in groups:
        return groups[wanted_release]
    best = max(groups, key=lambda rel: (
        max(str(r["created_at"] or "") for r in groups[rel]),
        len(groups[rel])))
    return groups[best]


def _point_contexts(
    conn: sqlite3.Connection, version: str
) -> dict[str, dict[str, Any]]:
    row = conn.execute(
        "SELECT evidence_json FROM question_solution_evidence_versions "
        "WHERE evidence_version_id = ?",
        (version,),
    ).fetchone()
    contexts: dict[str, dict[str, Any]] = {}
    if row is None:
        return contexts
    payload = json.loads(row["evidence_json"])
    for part in payload.get("parts", []):
        part_id = str(part.get("part_id") or "")
        mode = str(part.get("response_mode") or "")
        for point in part.get("evidence_points", []):
            contexts[str(point.get("evidence_point_id") or "")] = {
                "part_id": part_id,
                "mode": mode,
            }
    return contexts


def _rewrite_version_links(
    conn: sqlite3.Connection,
    qid: int,
    version: str,
    rows: list[sqlite3.Row],
    *,
    decision_version: bool,
    final_links: dict[tuple[int, str], str | None],
    inputs: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Transform one effective link group for the new release.

    Returns the rows to insert under the new release and whether any row
    content changed beyond the release id.
    """
    by_point: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_point[str(row["evidence_point_id"])].append(row)
    contexts = _point_contexts(conn, version) if decision_version else {}
    out: list[dict[str, Any]] = []
    changed = False

    for pid, point_rows in by_point.items():
        nonsk = [r for r in point_rows
                 if not str(r["stable_key"]).startswith(SK_PREFIX)]
        sk_rows = [r for r in point_rows
                   if str(r["stable_key"]).startswith(SK_PREFIX)]
        for row in nonsk:
            out.append(dict(row))
        ctx = contexts.get(pid, {})
        objective = decision_version and ctx.get("mode") == "exact_objective"
        if objective:
            changed = changed or bool(sk_rows)
            continue
        # supporting sk rows: map, drop retired, dedupe by key
        seen_sup: set[str] = set()
        for row in sk_rows:
            if row["role"] != "supporting_prerequisite":
                continue
            mapped = _map_sk_keys([str(row["stable_key"])], inputs)
            if not mapped:
                changed = True
                continue
            key = mapped[0]
            if key in seen_sup:
                changed = True
                continue
            seen_sup.add(key)
            item = dict(row)
            if key != row["stable_key"]:
                item["stable_key"] = key
                item["term_id"] = key
                changed = True
            out.append(item)
        direct_rows = [r for r in sk_rows if r["role"] == "direct"]
        if not decision_version:
            seen_dir: set[str] = set()
            for row in direct_rows:
                mapped = _map_sk_keys([str(row["stable_key"])], inputs)
                if not mapped or mapped[0] in seen_dir:
                    changed = True
                    continue
                seen_dir.add(mapped[0])
                item = dict(row)
                if mapped[0] != row["stable_key"]:
                    item["stable_key"] = mapped[0]
                    item["term_id"] = mapped[0]
                    changed = True
                out.append(item)
            continue
        final_key = final_links.get((qid, pid))
        if len(direct_rows) != (1 if final_key else 0):
            changed = True
        elif final_key and str(direct_rows[0]["stable_key"]) != final_key:
            changed = True
        if final_key is None:
            continue
        # keep exactly one direct sk row for the decided key
        same_key = [r for r in direct_rows if str(r["stable_key"]) == final_key]
        weights = [float(r["weight"]) for r in direct_rows
                   if r["weight"] is not None]
        kinds = {str(r["source_kind"]) for r in point_rows}
        source_kind = LINK_JOB if LINK_JOB in kinds else (
            str(point_rows[0]["source_kind"]) if point_rows else LINK_JOB)
        out.append({
            "evidence_version_id": version,
            "question_id": qid,
            "part_id": str(
                (direct_rows[0] if direct_rows else point_rows[0])["part_id"]
                if point_rows else ctx.get("part_id") or ""),
            "evidence_point_id": pid,
            "graph_release_id": RELEASE_ID,
            "role": "direct",
            "term_id": final_key,
            "stable_key": final_key,
            "resolution_status": "resolved",
            "weight": (float(same_key[0]["weight"]) if same_key
                       else (max(weights) if weights else 0.5)),
            "source_kind": source_kind,
            "source_reference": SOURCE_REFERENCE,
            "created_at": direct_rows[0]["created_at"] if direct_rows else None,
        })
        if not same_key:
            changed = True
    # decided links for points that never had any link rows
    if decision_version:
        for pid, ctx in contexts.items():
            if pid in by_point:
                continue
            final_key = final_links.get((qid, pid))
            if final_key is None:
                continue
            out.append({
                "evidence_version_id": version,
                "question_id": qid,
                "part_id": ctx["part_id"],
                "evidence_point_id": pid,
                "graph_release_id": RELEASE_ID,
                "role": "direct",
                "term_id": final_key,
                "stable_key": final_key,
                "resolution_status": "resolved",
                "weight": 0.5,
                "source_kind": LINK_JOB,
                "source_reference": SOURCE_REFERENCE,
                "created_at": None,
            })
            changed = True
    return out, changed


def publish_key_step_release(
    db_path: Path,
    *,
    data_root: Path,
    candidate: KnowledgeGraphRelease,
    catalog: dict[str, Any],
    inputs: dict[str, Any],
    candidate_state_path: Path | None = None,
) -> dict[str, Any]:
    """Stage + activate the release and rewrite point links in one transaction."""
    db_path = Path(db_path)
    versions_map: dict[int, str] = inputs["versions"]
    report: dict[str, Any] = {"release_id": RELEASE_ID}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            "SELECT release_id FROM knowledge_graph_releases WHERE status='active'"
        ).fetchall()
        if [r["release_id"] for r in active] != [BASE_RELEASE_ID]:
            raise KeyStepReleaseError(
                f"active release is not {BASE_RELEASE_ID}: {[r['release_id'] for r in active]}")

        current = _usable_version_map(conn, set(versions_map))
        drift = sorted(
            qid for qid, ver in versions_map.items()
            if current.get(qid) != ver)
        if drift:
            raise KeyStepReleaseError(
                f"evidence versions drifted for {len(drift)} questions: {drift[:20]}")

        teacher = conn.execute(
            "SELECT COUNT(*) AS c FROM evidence_point_knowledge_links "
            "WHERE source_kind='teacher'"
        ).fetchone()["c"]
        if teacher:
            raise KeyStepReleaseError(
                f"teacher-authored links exist ({teacher}); refusing to publish")

        # active keys of the new standard for decision validation
        active_keys = {
            str(n["stable_key"]) for n in candidate.payload["core_nodes"]
            if n.get("status") == "active"
            and str(n["stable_key"]).startswith(SK_PREFIX)
        }
        final_links = synthesize_final_links(conn, db_path, inputs, active_keys)
        report["final_links"] = sum(1 for v in final_links.values() if v)
        report["decision_points"] = len(final_links)

        stage_release(
            db_path, candidate, actor_ref=SOURCE_REFERENCE,
            source_reference=SOURCE_REFERENCE,
            reason="关键步骤词表 2026-10 审定发布",
            taxonomy_catalog=catalog, external_connection=conn)
        activate_release(
            db_path, candidate.release_id,
            expected_active_release_id=BASE_RELEASE_ID,
            actor_ref=SOURCE_REFERENCE,
            reason="关键步骤词表 2026-10 审定发布",
            external_connection=conn)

        # copy/transform link groups for every usable+approved/proposed version
        version_rows = conn.execute(
            """
            SELECT v.question_id, v.evidence_version_id
            FROM question_solution_evidence_versions v
            JOIN questions q ON q.id = v.question_id AND q.is_deleted = 0
            WHERE v.status IN ('proposed', 'approved')
            """
        ).fetchall()
        insert_sql = (
            "INSERT INTO evidence_point_knowledge_links ("
            "evidence_version_id, question_id, part_id, evidence_point_id, "
            "graph_release_id, role, term_id, stable_key, resolution_status, "
            "weight, source_kind, source_reference, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,"
            "?, COALESCE(?, datetime('now','localtime')))"
        )
        involved_questions: set[int] = set()
        copied_rows = 0
        for row in version_rows:
            qid = int(row["question_id"])
            version = str(row["evidence_version_id"])
            rows = _effective_group_rows(conn, version, BASE_RELEASE_ID)
            new_rows, changed = _rewrite_version_links(
                conn, qid, version, rows,
                decision_version=versions_map.get(qid) == version,
                final_links=final_links,
                inputs=inputs,
            )
            for item in new_rows:
                item = dict(item)
                item["graph_release_id"] = RELEASE_ID
                conn.execute(insert_sql, (
                    item["evidence_version_id"], item["question_id"],
                    item["part_id"], item["evidence_point_id"],
                    item["graph_release_id"], item["role"], item["term_id"],
                    item["stable_key"], item["resolution_status"],
                    item["weight"], item["source_kind"],
                    item["source_reference"], item["created_at"],
                ))
                copied_rows += 1
            if changed or versions_map.get(qid) == version:
                involved_questions.add(qid)
        report["copied_link_rows"] = copied_rows
        report["involved_questions"] = len(involved_questions)

        # B 口径下 v11 与 v9 的非 sk 链接完全相同，scope summary 无需重算；
        # 现有 134 行陈旧值保持原样，不随本次发布落地。
        for qid in sorted(involved_questions):
            refresh_derived_ownership_tags(conn, qid)
        fk_issues = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_issues:
            raise KeyStepReleaseError(
                f"foreign_key_check failed: {[tuple(r) for r in fk_issues[:10]]}")

        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()

    # record KEEP/NONE decisions as dismissed gap candidates
    kept = [
        f"{versions_map[qid]}:{pid}"
        for (qid, pid), key in final_links.items()
        if key is None
        and inputs["decisions"].get((qid, pid)) in {"KEEP", "NONE"}
    ]
    if candidate_state_path is not None and kept:
        from question_bank.services.skill_candidates import (
            _empty_state,
            _write_json_atomic,
        )
        path = Path(candidate_state_path)
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            state = _empty_state()
        now = datetime.now().isoformat(timespec="seconds")
        for gap_key in kept:
            version = gap_key.split(":", 1)[0]
            state["dismissed"].setdefault(gap_key, {
                "suggestion_id": SOURCE_REFERENCE,
                "dismissed_at": now,
                "evidence_version_id": version,
            })
        _write_json_atomic(path, state)
        report["dismissed_gap_keys"] = len(kept)

    directory = Path(data_root) / "question_bank" / "knowledge_releases" / RELEASE_ID
    directory.mkdir(parents=True, exist_ok=True)
    write_json_atomic(directory / "release.json", candidate.to_dict())
    write_json_atomic(directory / "taxonomy.json", dict(catalog))
    report["release_files"] = str(directory)
    return report


def _point_link_view(
    conn: sqlite3.Connection, db_path: Path, versions: list[str], release_id: str
) -> dict[tuple[str, str], list[tuple]]:
    link_map = load_point_links(db_path, versions, release_id, connection=conn)
    view: dict[tuple[str, str], list[tuple]] = {}
    for version, points in link_map.items():
        for pid, links in points.items():
            view[(version, pid)] = sorted(
                (l.role, l.stable_key, round(float(l.weight or 0), 4),
                 l.source_kind, l.resolution_status)
                for l in links)
    return view


def _question_tags_snapshot(conn: sqlite3.Connection) -> dict[int, list[tuple]]:
    tags: dict[int, list[tuple]] = defaultdict(list)
    for row in conn.execute(
        "SELECT question_id, tag_type, tag_value, confidence, source "
        "FROM question_tags ORDER BY id"
    ):
        tags[int(row["question_id"])].append(
            (str(row["tag_type"]), str(row["tag_value"]),
             str(row["confidence"]), str(row["source"])))
    return dict(tags)


def _scope_snapshot(conn: sqlite3.Connection) -> dict[int, tuple]:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='question_scope_summary'"
    ).fetchone():
        return {}
    out = {}
    for row in conn.execute("SELECT * FROM question_scope_summary"):
        d = dict(row)
        d.pop("updated_at", None)
        out[int(d["question_id"])] = d
    return out


def _active_skill_counts(candidate: KnowledgeGraphRelease) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for node in candidate.payload["core_nodes"]:
        key = str(node.get("stable_key") or "")
        if node.get("status") != "active" or not key.startswith(SK_PREFIX):
            continue
        suffix = key[len(SK_PREFIX):]
        volume = "_".join(suffix.split("_")[:2])
        counts[volume] += 1
    return dict(counts)


def _verify_links(
    conn: sqlite3.Connection,
    db_path: Path,
    inputs: dict[str, Any],
    final_links: dict[tuple[int, str], str | None],
    before_links: dict[tuple[str, str], list[tuple]],
) -> dict[str, Any]:
    """Check per-point sk results and non-sk invariance under the new release."""
    versions = sorted(set(inputs["versions"].values()))
    after = _point_link_view(conn, db_path, versions, RELEASE_ID)
    point_ctx = _question_points(conn, db_path, inputs["versions"], RELEASE_ID)
    mismatches: list[str] = []
    multi_direct: list[str] = []
    objective_sk: list[str] = []
    nonsk_diff: list[str] = []
    for (qid, pid), final_key in final_links.items():
        version = inputs["versions"][qid]
        rows = after.get((version, pid), [])
        direct = [r for r in rows if r[0] == "direct" and r[1].startswith(SK_PREFIX)
                  and r[4] == "resolved"]
        expected = [final_key] if final_key else []
        if sorted(r[1] for r in direct) != sorted(expected):
            mismatches.append(f"{qid}|{pid} expected {expected} got {[r[1] for r in direct]}")
        if len(direct) > 1:
            multi_direct.append(f"{qid}|{pid}")
        if point_ctx[(qid, pid)]["mode"] == "exact_objective":
            sk_any = [r for r in rows if r[1].startswith(SK_PREFIX)]
            if sk_any:
                objective_sk.append(f"{qid}|{pid}")
        # non-sk rows must equal the pre-publish v9 view
        before_nonsk = [r for r in before_links.get((version, pid), [])
                        if not r[1].startswith(SK_PREFIX)]
        after_nonsk = [r for r in rows if not r[1].startswith(SK_PREFIX)]
        if before_nonsk != after_nonsk:
            nonsk_diff.append(f"{qid}|{pid}")
    return {
        "checked_points": len(final_links),
        "final_link_mismatches": mismatches[:50],
        "final_link_mismatch_count": len(mismatches),
        "multi_direct_points": multi_direct[:50],
        "objective_points_with_sk": objective_sk[:50],
        "nonsk_diff_points": nonsk_diff[:50],
        "nonsk_diff_count": len(nonsk_diff),
    }


def _verify_diagnosis(tmp_root: Path, bank_copy: Path, grading_db: Path,
                      sessions: tuple[int, ...] = (4, 5)) -> dict[str, Any]:
    from backend.repositories.grading_database import open_grading_repositories
    from backend.step_diagnosis import build_session_step_diagnosis

    ro = sqlite3.connect(f"file:{Path(grading_db).as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    try:
        gdb = open_grading_repositories(Path(grading_db), external_connection=ro)
        out = {}
        for sid in sessions:
            ids = [r[0] for r in ro.execute(
                "SELECT DISTINCT student_id FROM session_results WHERE session_id=?",
                (sid,))]
            d = build_session_step_diagnosis(
                gdb, sid, student_ids=ids, question_bank_path=bank_copy)
            out[str(sid)] = {
                "students": d.summary["students"],
                "hotspots": [
                    {"key": h["key"], "name": h["name"],
                     "independent": h["independent"], "observed": h["observed"]}
                    for h in d.summary["hotspots"]
                ],
                "raw_sk_names": [
                    h["name"] for h in d.summary["hotspots"]
                    if str(h["name"]).startswith(SK_PREFIX)],
            }
        return out
    finally:
        ro.close()


def rehearse(
    db_path: Path,
    *,
    data_root: Path,
    grading_db: Path,
    output_dir: Path,
    catalog_dir: Path = DATA_DIR,
    keep_copy: bool = False,
) -> dict[str, Any]:
    """Copy the bank database into a test dir, publish there, verify, report."""
    tmp = Path(tempfile.mkdtemp(prefix="test_key_step_release_"))
    report: dict[str, Any] = {"mode": "rehearse", "tmp": str(tmp)}
    try:
        tmp_db_dir = tmp / "databases"
        tmp_db_dir.mkdir(parents=True)
        bank_copy = tmp_db_dir / "question_bank.db"
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(db_path) + suffix)
            if src.is_file():
                shutil.copy2(src, Path(str(bank_copy) + suffix))
        # frozen exam snapshots needed by step diagnosis (root = tmp)
        snap_dir = tmp / "config" / "uploaded"
        snap_dir.mkdir(parents=True)
        for snap in (data_root / "config" / "uploaded").glob("evidence_snapshot-*.json"):
            shutil.copy2(snap, snap_dir / snap.name)
        candidate_state = tmp / "taxonomy_state.skill_candidates.json"

        base = load_active_release(bank_copy)
        if base is None or base.release_id != BASE_RELEASE_ID:
            raise KeyStepReleaseError(
                f"copy active release is {base.release_id}, expected {BASE_RELEASE_ID}")
        candidate, catalog, inputs = build_key_step_release(base, catalog_dir)
        validation = validate_release(candidate, catalog)
        if not validation.valid:
            raise KeyStepReleaseError(
                "validation failed: " + "; ".join(
                    f"{i.code}:{i.path}:{i.message}" for i in validation.issues[:20]))
        resolver = CurrentKnowledgeResolver(candidate, catalog)
        report["resolver_release"] = resolver.release_id

        conn = sqlite3.connect(str(bank_copy))
        conn.row_factory = sqlite3.Row
        try:
            before_links = _point_link_view(
                conn, bank_copy, sorted(set(inputs["versions"].values())),
                BASE_RELEASE_ID)
            before_tags = _question_tags_snapshot(conn)
            before_scope = _scope_snapshot(conn)
        finally:
            conn.close()

        active_keys = {
            str(n["stable_key"]) for n in candidate.payload["core_nodes"]
            if n.get("status") == "active"
            and str(n["stable_key"]).startswith(SK_PREFIX)
        }
        conn = sqlite3.connect(str(bank_copy))
        conn.row_factory = sqlite3.Row
        try:
            final_links = synthesize_final_links(conn, bank_copy, inputs, active_keys)
        finally:
            conn.close()

        pub = publish_key_step_release(
            bank_copy, data_root=tmp, candidate=candidate, catalog=catalog,
            inputs=inputs, candidate_state_path=candidate_state)
        report["publish"] = pub
        report["active_skill_counts"] = _active_skill_counts(candidate)

        conn = sqlite3.connect(str(bank_copy))
        conn.row_factory = sqlite3.Row
        try:
            post = conn.execute(
                "SELECT release_id, status FROM knowledge_graph_releases"
            ).fetchall()
            report["releases"] = {r["release_id"]: r["status"] for r in post}
            report["verify_links"] = _verify_links(
                conn, bank_copy, inputs, final_links, before_links)
            after_tags = _question_tags_snapshot(conn)
            after_scope = _scope_snapshot(conn)
            report["tag_diff"] = {
                "questions_changed": sorted(
                    q for q in set(before_tags) | set(after_tags)
                    if before_tags.get(q) != after_tags.get(q))[:200],
            }
            report["tag_diff"]["changed_count"] = len([
                q for q in set(before_tags) | set(after_tags)
                if before_tags.get(q) != after_tags.get(q)])
            scope_field_counts: dict[str, int] = defaultdict(int)
            scope_samples: list[dict[str, Any]] = []
            for q in set(before_scope) | set(after_scope):
                before_row = dict(before_scope.get(q) or ())
                after_row = dict(after_scope.get(q) or ())
                if before_row == after_row:
                    continue
                for field in set(before_row) | set(after_row):
                    if before_row.get(field) != after_row.get(field):
                        scope_field_counts[str(field)] += 1
                structural = (
                    not before_row
                    or not after_row
                    or before_row.get("evidence_version_id")
                    != after_row.get("evidence_version_id")
                )
                if len(scope_samples) < 30 or structural:
                    scope_samples.append({
                        "question_id": q,
                        "structural": structural,
                        "before": before_row,
                        "after": after_row,
                    })
            report["scope_diff"] = {
                "questions_changed": sorted(
                    q for q in set(before_scope) | set(after_scope)
                    if before_scope.get(q) != after_scope.get(q))[:200],
                "field_change_counts": scope_field_counts,
                "samples": scope_samples,
            }
            report["scope_diff"]["changed_count"] = len([
                q for q in set(before_scope) | set(after_scope)
                if before_scope.get(q) != after_scope.get(q)])
            report["fk_check"] = conn.execute(
                "PRAGMA foreign_key_check").fetchall() != []
        finally:
            conn.close()

        # 手动重算 8 道代表题（只报告，不作为发布的一部分）：
        # 3 道换锚题（924/1142/1152）+ 5 道纯客观题丢行题。
        manual_ids = (924, 1142, 1152, 633, 1333, 1371, 1401, 1408)
        manual: dict[str, Any] = {}
        conn = sqlite3.connect(str(bank_copy))
        conn.row_factory = sqlite3.Row
        try:
            for qid in manual_ids:
                before_row = dict(conn.execute(
                    "SELECT * FROM question_scope_summary WHERE question_id=?",
                    (qid,)).fetchone() or {})
                refresh_question_scope_summary(conn, qid)
                after_row = dict(conn.execute(
                    "SELECT * FROM question_scope_summary WHERE question_id=?",
                    (qid,)).fetchone() or {})
                manual[str(qid)] = {"before": before_row, "after": after_row}
            conn.commit()
        finally:
            conn.close()
        report["manual_scope_refresh"] = manual

        report["skill_snapshot"] = _verify_skill_snapshot(
            bank_copy, tmp, candidate_state)
        report["diagnosis"] = _verify_diagnosis(tmp, bank_copy, grading_db)
        report["repair_preview"] = _verify_repair_preview(bank_copy, tmp)
        report["dismissed_count"] = len(json.loads(
            candidate_state.read_text(encoding="utf-8"))["dismissed"]) \
            if candidate_state.exists() else 0

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(output_dir / "report.json", report)
        _write_report_md(output_dir / "report.md", report)
        return report
    finally:
        if not keep_copy:
            shutil.rmtree(tmp, ignore_errors=True)


def _verify_skill_snapshot(
    bank_copy: Path, data_root: Path, candidate_state: Path
) -> dict[str, Any]:
    from question_bank.services.question_skill_index import build_skill_snapshot

    conn = sqlite3.connect(str(bank_copy))
    conn.row_factory = sqlite3.Row
    try:
        snapshot = build_skill_snapshot(conn, bank_copy, data_root)
    finally:
        conn.close()
    members = snapshot.get("by_skill", {})
    top = sorted(
        ((key, len(ids)) for key, ids in members.items()),
        key=lambda item: -item[1])[:20]
    wanted = [
        "sk_bnu24_math_g8_upper_3_3_201", "sk_bnu24_math_g8_upper_3_3_206",
        "sk_bnu24_math_g8_upper_2_3_110", "sk_bnu24_math_g8_upper_5_2_207",
        "sk_bnu24_math_g7_lower_4_1_203", "sk_bnu24_math_g7_upper_3_1_203",
    ]
    dismissed = set()
    if candidate_state.exists():
        dismissed = set(json.loads(
            candidate_state.read_text(encoding="utf-8")).get("dismissed", ()))
    gaps = snapshot.get("gap_points") or {}
    versions = snapshot.get("evidence_versions") or {}
    gap_total = sum(len(rows) for rows in gaps.values())
    undismissed = [
        f"{versions.get(qid, '')}:{row['point_id']}"
        for qid, rows in gaps.items() for row in rows
        if f"{versions.get(qid, '')}:{row['point_id']}" not in dismissed
    ]
    return {
        "unlinked": len(snapshot.get("unlinked") or ()),
        "gap_questions": len(gaps),
        "gap_points": gap_total,
        "undismissed_gap_points": undismissed,
        "top_skills": top,
        "wanted": {key: len(members.get(key, ())) for key in wanted},
    }


def _verify_repair_preview(bank_copy: Path, data_root: Path) -> dict[str, Any]:
    from backend.jobs.question_bank_repair import _compute_repair_preview
    from question_bank.services.question_read_service import QuestionBankReadService

    service = QuestionBankReadService(bank_copy, data_root=data_root)
    preview = _compute_repair_preview(service, "bnu24-math-g8-upper", "skills", None)
    return {
        "skills_required": preview.get("skills_required"),
        "pending_items": len(preview.get("items") or []),
        "scanned": preview.get("scanned_count"),
    }


def _write_report_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 关键步骤新标准 v11 演练报告",
        "",
        f"- 演练模式：副本库（{report.get('tmp', '')}）",
        f"- 发布：{report.get('publish', {}).get('release_id')}",
        f"- 活动标准：{report.get('releases')}",
        f"- 各册活动技能数：{report.get('active_skill_counts')}",
        f"- 判定重写点数：{report.get('publish', {}).get('decision_points')}，"
        f"最终直接关键步骤：{report.get('publish', {}).get('final_links')}",
        f"- 复制链接行：{report.get('publish', {}).get('copied_link_rows')}，"
        f"涉及题目：{report.get('publish', {}).get('involved_questions')}",
        "",
        "## 读取侧核对",
        f"- 逐点比对：{report.get('verify_links', {})}",
        "",
        "## 标签/范围差异",
        f"- question_tags 变化题数：{report.get('tag_diff', {}).get('changed_count')}",
        f"- scope summary 变化题数：{report.get('scope_diff', {}).get('changed_count')}",
        f"- scope summary 行消失的题目（结构性）："
        f"{sorted(s['question_id'] for s in report.get('scope_diff', {}).get('samples', []) if s.get('structural') and not s.get('after'))}",
        f"- 手动重算 8 题（不属发布内容）："
        + "; ".join(
            f"{qid} 主节 {r['before'].get('primary_section_id')}→"
            f"{(r['after'] or {}).get('primary_section_id')}"
            + (" [行消失]" if r['before'] and not r['after'] else "")
            + (" [行新增]" if r['after'] and not r['before'] else "")
            for qid, r in (report.get('manual_scope_refresh') or {}).items()),
        "",
        "## 技能快照",
        f"- {report.get('skill_snapshot')}",
        "",
        "## 考试诊断",
        f"- {json.dumps(report.get('diagnosis'), ensure_ascii=False, indent=2)}",
        "",
        "## 补齐预览",
        f"- {report.get('repair_preview')}",
        "",
        f"- dismissed 缺口数：{report.get('dismissed_count')}",
        f"- foreign_key_check 异常：{report.get('fk_check')}",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rehearse", action="store_true",
                        help="run the full flow on a copied database (default)")
    parser.add_argument("--publish", action="store_true",
                        help="backup then publish to the real database")
    parser.add_argument("--db", type=Path,
                        default=ROOT / "user_data" / "databases" / "question_bank.db")
    parser.add_argument("--grading-db", type=Path,
                        default=ROOT / "user_data" / "databases" / "grading_system.db")
    parser.add_argument("--data-root", type=Path, default=ROOT / "user_data")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "output" / "key-step-release-rehearsal-20261012")
    parser.add_argument("--catalog-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--keep-copy", action="store_true")
    args = parser.parse_args(argv)

    if args.publish:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backups = ROOT / "backups"
        backups.mkdir(exist_ok=True)
        shutil.copy2(args.db, backups / f"question_bank_{stamp}.db")
        for suffix in ("-wal", "-shm"):
            src = Path(str(args.db) + suffix)
            if src.is_file():
                shutil.copy2(src, backups / f"question_bank_{stamp}.db{suffix}")
        print(f"backup written under {backups}")
        base = load_active_release(args.db)
        candidate, catalog, inputs = build_key_step_release(base, args.catalog_dir)
        validation = validate_release(candidate, catalog)
        if not validation.valid:
            raise KeyStepReleaseError("validation failed; refusing to publish")
        report = publish_key_step_release(
            args.db, data_root=args.data_root, candidate=candidate,
            catalog=catalog, inputs=inputs,
            candidate_state_path=None)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    report = rehearse(
        args.db, data_root=args.data_root, grading_db=args.grading_db,
        output_dir=args.output_dir, catalog_dir=args.catalog_dir,
        keep_copy=args.keep_copy)
    print(json.dumps({k: v for k, v in report.items() if k != "diagnosis"},
                     ensure_ascii=False, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
