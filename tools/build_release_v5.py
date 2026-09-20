"""Build taxonomy catalog v6 and knowledge-graph release v5.

Release v5 = release v4 + reasoning/proof and context-dependent teaching
skills defined in ``teaching_skills_reasoning_v5.json``. Unlike the v4
repair, nothing is retired: every v4 term stays active, so existing links
carry forward unchanged under the new release.

The skill definitions themselves were written by semantic analysis of the
actual section-only evidence points, not by local text clustering — the
standard carries no regex patterns and is not machine-matched.

Usage:
    python tools/build_release_v5.py --teaching-standard PATH [--write]
    python tools/build_release_v5.py --teaching-standard PATH --preview-db DB
    python tools/build_release_v5.py --teaching-standard PATH --activate-and-carry-db DB --backup-dir DIR
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from question_bank.knowledge_graph_release.contracts import (
    KnowledgeGraphRelease,
    compute_content_hash,
    stable_record_hash,
)
from question_bank.knowledge_graph_release.validation import validate_release
from tools.build_release_v3 import build_release, build_vocabulary, _load_json, _write_json

CATALOG_DIR = ROOT / "question_bank" / "taxonomy" / "catalogs"
RELEASE_V4 = CATALOG_DIR / "knowledge_graph_release_v4.json"
VOCAB_V5 = CATALOG_DIR / "tag_vocabulary_v5.json"
RELEASE_V5 = CATALOG_DIR / "knowledge_graph_release_v5.json"
VOCAB_V6 = CATALOG_DIR / "tag_vocabulary_v6.json"

RELEASE_ID = "kgr_bnu_math_curriculum_2026_09_v5"
TAXONOMY_REVISION = 7
TEACHING_SOURCE = "teaching_skill_semantic_analysis_2026_09"
STANDARD_REF = "question_bank/taxonomy/catalogs/teaching_skills_reasoning_v5.json"


def build_v5(standard: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (release_v5 payload, vocabulary v6) = v4 + new skills, nothing retired."""
    old_release = _load_json(RELEASE_V4)
    old_vocabulary = _load_json(VOCAB_V5)
    nodes = {node["stable_key"]: node for node in old_release["core_nodes"]}
    skills = []
    for item in standard["skills"]:
        section_key = item["section_key"]
        if section_key not in nodes:
            raise ValueError(f"unknown parent node: {section_key}")
        parent_node = nodes[section_key]
        chapter_key = (
            section_key
            if parent_node.get("node_kind") == "core" and section_key.count("_") == 5
            else section_key.rsplit("_", 1)[0]
        )
        skills.append(
            {
                **item,
                "chapter_key": chapter_key,
                "count": 0,
                "aliases": [],
                "sample_targets": item["examples"],
            }
        )
    names = {
        s["id"]: f"{nodes[s['section_key']]['display_name']}｜技能·{s['name']}"
        for s in skills
    }
    for skill in skills:
        skill["display_name"] = names[skill["id"]]

    vocabulary = build_vocabulary(old_vocabulary, skills, nodes)
    payload = build_release(old_release, skills, nodes, names)
    payload["release_id"] = RELEASE_ID
    payload["taxonomy_revision"] = vocabulary["revision"] = TAXONOMY_REVISION
    payload["predecessor_release_id"] = old_release["release_id"]

    definitions = {s["id"]: s for s in skills}
    for term in vocabulary["terms"]:
        if term["id"] in definitions:
            skill = definitions[term["id"]]
            term.update(
                name=names[term["id"]],
                origin=TEACHING_SOURCE,
                retrieval_hints=skill["examples"],
            )
    for node in payload["core_nodes"]:
        key = node["stable_key"]
        if key in definitions:
            skill = definitions[key]
            node.update(
                definition=skill["include"],
                include_scope=skill["include"],
                exclude_scope=skill["exclude"],
                observable_evidence="；".join(skill["examples"]),
                evidence_source_ids=[TEACHING_SOURCE],
                rationale=(
                    "技能由对章节级判定点证据的逐条语义分析定义；"
                    "模糊证据仅归小节。"
                ),
            )
    for disposition in payload["fine_term_dispositions"]:
        key = disposition["fine_term_id"]
        if key in definitions:
            skill = definitions[key]
            disposition.update(
                display_name=names[key],
                definition=skill["include"],
                include_scope=skill["include"],
                exclude_scope=skill["exclude"],
                evidence_source_ids=[TEACHING_SOURCE],
            )
    for relation in payload["relations"]:
        if relation["source_key"] in definitions:
            relation["evidence_source_ids"] = [TEACHING_SOURCE]
            relation["source_locator"] = STANDARD_REF
            relation["rationale"] = "按可观察数学操作及教材节归属定义。"
        relation["relation_key"] = stable_record_hash(
            "release-relation",
            RELEASE_ID,
            relation["source_key"],
            relation["target_key"],
            relation["relation_type"],
        )
    # build_release re-appends the v3 clustering source; keep first occurrence only.
    seen_source_ids: set[str] = set()
    deduped = []
    for source in payload.get("sources", []):
        sid = str(source.get("source_id") or "")
        if sid in seen_source_ids:
            continue
        seen_source_ids.add(sid)
        deduped.append(source)
    payload["sources"] = deduped + [
        {
            "source_id": TEACHING_SOURCE,
            "kind": "teaching_skill_semantic_analysis",
            "title": "证明/推理与情境依赖技能定义（模型语义分析）",
            "reference": STANDARD_REF,
        }
    ]
    payload["content_hash"] = compute_content_hash(payload)
    return payload, vocabulary


def apply_v5(db_path: Path, standard: Mapping[str, Any], payload: Mapping[str, Any], vocabulary: Mapping[str, Any]) -> dict[str, Any]:
    """Stage+activate v5 and carry forward every resolved v4 link.

    No link is re-decided here and no local matcher runs: each resolved v4
    link resolves to the same stable key in v5 (nothing was retired), so the
    carry-forward is a pure identity migration. Per-point re-decisions are
    produced separately by the offline link job after activation.
    """
    import sqlite3
    from collections import Counter
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.database.schema import connect
    from question_bank.knowledge_graph_release.repository import (
        stage_release,
        activate_release,
        active_release_id,
        rollback_release,
    )
    from question_bank.solution_evidence.knowledge_links import (
        load_point_links,
        replace_point_links,
        refresh_question_scope_summary,
    )
    from question_bank.services.question_write_service import (
        refresh_derived_ownership_tags,
    )

    previous = active_release_id(db_path)
    candidate = KnowledgeGraphRelease.from_mapping(payload)
    if previous != payload["predecessor_release_id"]:
        raise ValueError("活动版本与前置版本不符，请重新预演；未修改数据库。")
    resolver = CurrentKnowledgeResolver(candidate, vocabulary)
    plans = []
    counts: Counter[str] = Counter()
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT * FROM (
            SELECT v.*, ROW_NUMBER() OVER(PARTITION BY v.question_id ORDER BY v.created_at DESC, v.evidence_version_id DESC) AS seq
            FROM question_solution_evidence_versions v JOIN questions q ON q.id=v.question_id
            WHERE v.status IN ('approved','proposed') AND q.is_deleted=0
        ) WHERE seq=1"""
        ).fetchall()
        grouped = load_point_links(
            db_path, [r["evidence_version_id"] for r in rows], previous, connection=conn
        )
        all_ids = [r[0] for r in conn.execute("SELECT id FROM questions WHERE is_deleted=0")]
        for row in rows:
            points = []
            old = grouped.get(row["evidence_version_id"], {})
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
                    points.append(
                        {
                            "part_id": part["part_id"],
                            "evidence_point_id": pid,
                            "links": links,
                        }
                    )
                    counts["points"] += 1
            plans.append((row["question_id"], row["evidence_version_id"], points))
    stage_release(
        db_path,
        candidate,
        actor_ref="skill_reasoning_v5",
        source_reference=STANDARD_REF,
        taxonomy_catalog=vocabulary,
    )
    activate_release(
        db_path,
        candidate.release_id,
        expected_active_release_id=previous,
        actor_ref="skill_reasoning_v5",
        reason="启用语义分析定义的新技能，v4 链接原样结转",
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
                    source_reference="release_v5_carry_forward",
                )
            for qid in all_ids:
                refresh_question_scope_summary(conn, qid)
                refresh_derived_ownership_tags(conn, qid)
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
            actor_ref="skill_reasoning_v5",
            reason="链接结转事务失败，恢复 v4 活动版本",
        )
        raise
    return {
        "previous_release": previous,
        "new_release": candidate.release_id,
        "questions_with_evidence": len(plans),
        "questions_refreshed": len(all_ids),
        **dict(counts),
        "snapshots_changed": 0,
        "model_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--teaching-standard", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--preview-db", type=Path)
    group.add_argument("--activate-and-carry-db", type=Path)
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()

    release_payload, vocabulary = build_v5(_load_json(args.teaching_standard))
    report = validate_release(
        KnowledgeGraphRelease.from_mapping(release_payload), vocabulary
    )
    print(
        f"release {release_payload['release_id']}: nodes={len(release_payload['core_nodes'])} "
        f"relations={len(release_payload['relations'])} "
        f"errors={len(report.errors)} warnings={len(report.warnings)}"
    )
    for issue in report.errors[:20]:
        print(f"  ERROR {issue.path}: {issue.message}")
    for issue in report.warnings[:20]:
        print(f"  WARN  {issue.path}: {issue.message}")
    if not report.valid:
        return 1

    if args.preview_db or args.activate_and_carry_db:
        if args.write:
            parser.error("catalog --write and database apply are separate operations")
        import sqlite3
        import tempfile
        from datetime import datetime

        database = (args.preview_db or args.activate_and_carry_db).resolve(strict=True)
        if args.preview_db:
            with tempfile.TemporaryDirectory(prefix="release_v5_preview_") as folder:
                copy_path = Path(folder) / "question_bank.db"
                with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source, sqlite3.connect(copy_path) as target:
                    source.backup(target)
                source.close()
                target.close()
                result = apply_v5(copy_path, _load_json(args.teaching_standard), release_payload, vocabulary)
        else:
            if not args.backup_dir:
                parser.error("--activate-and-carry-db requires --backup-dir")
            args.backup_dir.mkdir(parents=True, exist_ok=True)
            backup = args.backup_dir / f"question_bank_before_release_v5_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
            with backup.open("xb"):
                pass
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source, sqlite3.connect(backup) as target:
                source.backup(target)
            source.close()
            target.close()
            result = apply_v5(database, _load_json(args.teaching_standard), release_payload, vocabulary)
            result["backup"] = str(backup)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.write:
        _write_json(RELEASE_V5, release_payload)
        _write_json(VOCAB_V6, vocabulary)
        print(f"wrote {RELEASE_V5.name}, {VOCAB_V6.name}")
    else:
        print("dry-run; pass --write to emit catalogs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
