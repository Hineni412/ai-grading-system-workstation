"""Build the next knowledge release from teacher-approved skill candidates.

Approved ``new_skill`` suggestions live in the skill-candidate state file
(``<stem>.skill_candidates<suffix>`` next to the taxonomy state) until a
release revision publishes them. This tool builds that release: the DB's
active release plus the approved unpublished skills, nothing retired, so
existing resolved links carry forward unchanged.

Modes (default is a dry-run that writes nothing):
    python tools/build_skill_release.py [--db DB] [--volume ID]
    python tools/build_skill_release.py --write [--catalog-dir DIR]
    python tools/build_skill_release.py --preview-db DB
    python tools/build_skill_release.py --activate-and-carry-db DB --backup-dir DIR

``--activate-and-carry-db`` refuses unless the new release files were written
and registered in ``loader.py`` first (the printed map lines); activation
backs up the database, then applies the release and links each published
skill's still-current gap points. No model calls anywhere.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import tempfile
from collections import Counter
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
from question_bank.knowledge_graph_release.loader import (
    load_release_for_taxonomy_revision,
    load_taxonomy_catalog_for_release,
)
from question_bank.knowledge_graph_release.validation import validate_release
from tools.build_release_v3 import (
    _load_json,
    _write_json,
    build_release,
    build_vocabulary,
)

CATALOG_DIR = ROOT / "question_bank" / "taxonomy" / "catalogs"
DEFAULT_DB = ROOT / "user_data" / "databases" / "question_bank.db"
ACTOR = "skill_release_candidates"
REASON = "发布教师已批准的新技能，旧版链接原样结转"


def default_state_path(db_path: Path) -> Path:
    base = Path(db_path).resolve().parent.parent / "config" / "taxonomy_state_v2.json"
    return base.with_name(f"{base.stem}.skill_candidates{base.suffix or '.json'}")


def approved_unpublished_skills(
    db_path: Path,
    state_path: Path,
    volume: str | None,
) -> list[dict[str, Any]]:
    approved: dict[str, Any] = {}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        raw = state.get("approved_skills")
        if isinstance(raw, dict):
            approved = raw
    if not approved:
        raise SystemExit("没有已批准待发布的新技能；无需发布。")
    read_uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(read_uri, uri=True) as conn:
        active = conn.execute(
            "SELECT release_id FROM knowledge_graph_releases WHERE status = 'active'"
        ).fetchone()
        if active is None:
            raise SystemExit("题库没有已启用的技能标准，无法发布。")
        published = {
            str(row[0])
            for row in conn.execute(
                "SELECT stable_key FROM knowledge_graph_node_profiles "
                "WHERE release_id = ? AND status = 'active'",
                (str(active[0]),),
            )
        }
    skills = [
        dict(skill)
        for skill in approved.values()
        if isinstance(skill, dict)
        and str(skill.get("skill_id") or "") not in published
        and (not volume or str(skill.get("curriculum_volume_id") or "") == volume)
    ]
    skills.sort(key=lambda item: str(item.get("approved_at") or ""))
    if not skills:
        raise SystemExit("没有已批准待发布的新技能；无需发布。")
    return skills


def base_release(db_path: Path) -> tuple[dict[str, Any], dict[str, Any], KnowledgeGraphRelease]:
    """The DB's active release, loaded read-only, plus its bundled vocabulary."""
    from question_bank.knowledge_graph_release.contracts import cached_release_from_json

    read_uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(read_uri, uri=True) as conn:
        row = conn.execute(
            "SELECT release_id, content_hash, payload_json "
            "FROM knowledge_graph_releases WHERE status = 'active'"
        ).fetchone()
    if row is None:
        raise SystemExit("题库没有已启用的技能标准，无法发布。")
    active = cached_release_from_json(
        str(row[2]), release_id=str(row[0]), content_hash=str(row[1])
    )
    bundled = load_release_for_taxonomy_revision(active.taxonomy_revision)
    if bundled.release_id != active.release_id:
        raise SystemExit(
            f"题库活动版本 {active.release_id} 不在随包发布的目录中，请人工核对。"
        )
    return bundled.to_dict(), load_taxonomy_catalog_for_release(bundled), bundled


def next_version_numbers(catalog_dir: Path) -> tuple[int, int]:
    def _max_no(pattern: str) -> int:
        numbers = [
            int(match.group(1))
            for path in catalog_dir.glob(pattern)
            for match in [re.search(r"_v(\d+)\.json$", path.name)]
            if match
        ]
        return max(numbers, default=0)

    return _max_no("knowledge_graph_release_v*.json") + 1, _max_no("tag_vocabulary_v*.json") + 1


def build_candidate_release(
    base_payload: Mapping[str, Any],
    base_vocabulary: Mapping[str, Any],
    skills: Sequence[Mapping[str, Any]],
    *,
    release_id: str,
    taxonomy_revision: int,
    source_id: str,
    standard_ref: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Base release + approved skills; mirrors build_release_v5.build_v5."""
    nodes = {node["stable_key"]: node for node in base_payload["core_nodes"]}
    rows = []
    for item in skills:
        section_key = str(item["section_key"])
        if section_key not in nodes:
            raise SystemExit(f"技能 {item.get('skill_id')} 的小节 {section_key} 不在当前标准中。")
        parent = nodes[section_key]
        chapter_key = (
            section_key
            if parent.get("node_kind") == "core" and section_key.count("_") == 5
            else section_key.rsplit("_", 1)[0]
        )
        rows.append(
            {
                "id": str(item["skill_id"]),
                "section_key": section_key,
                "chapter_key": chapter_key,
                "name": str(item["name"]),
                "include": str(item.get("include") or ""),
                "exclude": str(item.get("exclude") or ""),
                "examples": [str(value) for value in item.get("examples", [])][:3],
                "count": len(item.get("gap_refs", [])),
                "aliases": [],
                "sample_targets": [str(value) for value in item.get("examples", [])][:3],
            }
        )
    names = {
        row["id"]: f"{nodes[row['section_key']]['display_name']}｜技能·{row['name']}"
        for row in rows
    }
    for row in rows:
        row["display_name"] = names[row["id"]]
    vocabulary = build_vocabulary(base_vocabulary, rows, nodes)
    payload = build_release(base_payload, rows, nodes, names)
    payload["release_id"] = release_id
    payload["taxonomy_revision"] = vocabulary["revision"] = taxonomy_revision
    payload["predecessor_release_id"] = base_payload["release_id"]
    definitions = {row["id"]: row for row in rows}
    for term in vocabulary["terms"]:
        if term["id"] in definitions:
            skill = definitions[term["id"]]
            term.update(
                name=names[term["id"]],
                origin=source_id,
                retrieval_hints=skill["examples"],
            )
    for node in payload["core_nodes"]:
        if node["stable_key"] in definitions:
            skill = definitions[node["stable_key"]]
            node.update(
                definition=skill["include"],
                include_scope=skill["include"],
                exclude_scope=skill["exclude"],
                observable_evidence="；".join(skill["examples"]),
                evidence_source_ids=[source_id],
                rationale="技能由教师在技能候选审核中逐条批准定义。",
            )
    for disposition in payload["fine_term_dispositions"]:
        if disposition["fine_term_id"] in definitions:
            skill = definitions[disposition["fine_term_id"]]
            disposition.update(
                display_name=names[disposition["fine_term_id"]],
                definition=skill["include"],
                include_scope=skill["include"],
                exclude_scope=skill["exclude"],
                evidence_source_ids=[source_id],
            )
    for relation in payload["relations"]:
        if relation["source_key"] in definitions:
            relation["evidence_source_ids"] = [source_id]
            relation["source_locator"] = standard_ref
            relation["rationale"] = "按教师批准的技能定义挂入所属小节。"
        relation["relation_key"] = stable_record_hash(
            "release-relation",
            release_id,
            relation["source_key"],
            relation["target_key"],
            relation["relation_type"],
        )
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
            "kind": "teaching_skill_candidates",
            "title": "教师批准的新技能候选定义",
            "reference": standard_ref,
        }
    ]
    payload["content_hash"] = compute_content_hash(payload)
    return payload, vocabulary


def apply_candidate_release(
    db_path: Path,
    payload: Mapping[str, Any],
    vocabulary: Mapping[str, Any],
    skills: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Stage+activate the candidate release, carry links, then link gap refs.

    Mirrors apply_v5's identity carry-forward; afterwards each published
    skill's gap refs that are still current get a direct link via
    ``link_points_to_skill``. No model calls.
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
    from question_bank.services.skill_gaps import teacher_protected_versions
    from question_bank.solution_evidence.knowledge_links import (
        link_points_to_skill,
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
    skipped: list[str] = []
    plans = []
    latest_version: dict[int, str] = {}
    point_parts: dict[str, dict[str, str]] = {}
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
            part_ids: dict[str, str] = {}
            for part in json.loads(row["evidence_json"]).get("parts", []):
                for point in part.get("evidence_points", []):
                    pid = point["evidence_point_id"]
                    part_ids[pid] = str(part["part_id"])
                    link_weights: dict[tuple[str, str], float] = {}
                    for link in old.get(pid, ()):
                        if link.resolution_status != "resolved":
                            continue
                        for identity in resolver.resolve(link.stable_key):
                            key = (link.role, identity.stable_key)
                            link_weights[key] = link_weights.get(key, 0.0) + link.weight
                    total = sum(w for (role, _), w in link_weights.items() if role == "direct")
                    links = [
                        {"term_id": key, "stable_key": key, "role": role,
                         "weight": weight / total if role == "direct" and total else weight}
                        for (role, key), weight in link_weights.items()
                        if weight > 0
                    ]
                    points.append({"part_id": part["part_id"], "evidence_point_id": pid, "links": links})
                    counts["points"] += 1
            plans.append((row["question_id"], row["evidence_version_id"], points))
            latest_version[int(row["question_id"])] = str(row["evidence_version_id"])
            point_parts[str(row["evidence_version_id"])] = part_ids
        protected = teacher_protected_versions(conn)
    stage_release(db_path, candidate, actor_ref=ACTOR, source_reference=str(payload.get("release_id")), taxonomy_catalog=vocabulary)
    activate_release(db_path, candidate.release_id, expected_active_release_id=previous, actor_ref=ACTOR, reason=REASON)
    try:
        with connect(db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            for qid, version, points in plans:
                counts["links_written"] += replace_point_links(
                    conn, evidence_version_id=version, question_id=qid,
                    graph_release_id=candidate.release_id, points=points,
                    source_reference="release_skill_candidates_carry_forward",
                )
            for skill in skills:
                skill_id = str(skill["skill_id"])
                wanted: dict[tuple[int, str], dict[str, str]] = {}
                for ref in skill.get("gap_refs", []):
                    qid = int(ref.get("question_id") or 0)
                    version = str(ref.get("evidence_version_id") or "")
                    point = str(ref.get("point_id") or "")
                    reason = ""
                    if not qid or latest_version.get(qid) != version:
                        reason = "判定点已有更新的证据版本"
                    elif version in protected:
                        reason = "该证据版本含教师人工修改"
                    elif point not in point_parts.get(version, {}):
                        reason = "判定点不在当前证据中"
                    if reason:
                        skipped.append(f"{skill_id}:{ref.get('gap_key')} {reason}")
                        continue
                    wanted.setdefault((qid, version), {})[point] = skill_id
                if not wanted:
                    continue
                current = load_point_links(
                    db_path, sorted({version for _, version in wanted}),
                    candidate.release_id, connection=conn,
                )
                for (qid, version), point_skills in wanted.items():
                    effective = current.get(version, {})
                    part_map = point_parts.get(version, {})
                    eligible = {}
                    for point in point_skills:
                        if any(
                            link.role == "direct" and link.resolution_status == "resolved"
                            and str(link.stable_key).startswith("sk_")
                            for link in effective.get(point, ())
                        ):
                            skipped.append(f"{skill_id}:{point} 已有技能链接")
                            continue
                        eligible[point] = skill_id
                    if eligible:
                        counts["gap_links_written"] += link_points_to_skill(
                            conn, db_path=db_path, question_id=qid,
                            evidence_version_id=version,
                            graph_release_id=candidate.release_id,
                            point_skills=eligible,
                            part_ids={pid: part_map.get(pid, "") for pid in eligible},
                            source_reference=f"skill_candidate_publish:{skill_id}",
                        )
            for qid in all_ids:
                refresh_question_scope_summary(conn, qid)
                refresh_derived_ownership_tags(conn, qid)
            if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("结转后引用关系校验未通过")
    except Exception:
        rollback_release(db_path, previous, expected_active_release_id=candidate.release_id, actor_ref=ACTOR, reason="链接结转事务失败，恢复原活动版本")
        raise
    return {
        "previous_release": previous,
        "new_release": candidate.release_id,
        "questions_with_evidence": len(plans),
        "published_skills": len(skills),
        "gap_refs_linked": counts["gap_links_written"],
        "gap_refs_skipped": len(skipped),
        "skipped": skipped,
        "questions_refreshed": len(all_ids),
        "links_written": counts["links_written"],
        "points": counts["points"],
        "model_calls": 0,
    }


def registered_release(revision: int) -> KnowledgeGraphRelease:
    return load_release_for_taxonomy_revision(revision)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--candidates-state", type=Path)
    parser.add_argument("--catalog-dir", type=Path, default=CATALOG_DIR)
    parser.add_argument("--volume")
    parser.add_argument("--write", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--preview-db", type=Path)
    group.add_argument("--activate-and-carry-db", type=Path)
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        parser.error(f"题库数据库不存在：{db}")
    state_path = Path(args.candidates_state) if args.candidates_state else default_state_path(db)
    skills = approved_unpublished_skills(db, state_path, args.volume)
    base_payload, base_vocabulary, bundled = base_release(db)
    release_no, vocab_no = next_version_numbers(Path(args.catalog_dir))
    revision = int(bundled.taxonomy_revision) + 1
    trailing = re.search(r"_v(\d+)$", bundled.release_id)
    release_id = (
        f"kgr_bnu_math_curriculum_{datetime.now():%Y_%m}_v{int(trailing.group(1)) + 1}"
        if trailing else f"{bundled.release_id}_next"
    )
    source_id = f"teaching_skill_candidates_{release_id.rsplit('_v', 1)[-1]}"
    standard_ref = f"question_bank/taxonomy/catalogs/teaching_skills_candidates_v{release_no}.json"
    payload, vocabulary = build_candidate_release(
        base_payload, base_vocabulary, skills,
        release_id=release_id, taxonomy_revision=revision,
        source_id=source_id, standard_ref=standard_ref,
    )
    report = validate_release(KnowledgeGraphRelease.from_mapping(payload), vocabulary)
    print(
        f"release {release_id}: revision={revision} skills={len(skills)} "
        f"nodes={len(payload['core_nodes'])} relations={len(payload['relations'])} "
        f"errors={len(report.errors)} warnings={len(report.warnings)}"
    )
    for skill in skills:
        print(f"  + {skill['skill_id']} 「{skill['name']}」 → {skill['section_key']} （{len(skill.get('gap_refs', []))} 个判定点）")
    for issue in report.errors[:20]:
        print(f"  ERROR {issue.path}: {issue.message}")
    for issue in report.warnings[:20]:
        print(f"  WARN  {issue.path}: {issue.message}")
    if not report.valid:
        return 1

    teaching_file = Path(args.catalog_dir) / f"teaching_skills_candidates_v{release_no}.json"
    release_file = Path(args.catalog_dir) / f"knowledge_graph_release_v{release_no}.json"
    vocab_file = Path(args.catalog_dir) / f"tag_vocabulary_v{vocab_no}.json"

    if args.preview_db or args.activate_and_carry_db:
        if args.write:
            parser.error("--write 与数据库操作分开执行")
        database = (args.preview_db or args.activate_and_carry_db).resolve(strict=True)
        if args.preview_db:
            with tempfile.TemporaryDirectory(prefix="skill_release_preview_") as folder:
                copy_path = Path(folder) / "question_bank.db"
                with (
                    sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source,
                    sqlite3.connect(str(copy_path)) as target,
                ):
                    source.backup(target)
                result = apply_candidate_release(copy_path, payload, vocabulary, skills)
        else:
            if not args.backup_dir:
                parser.error("--activate-and-carry-db 需要 --backup-dir")
            registered = registered_release(revision)
            if registered.release_id != release_id or registered.content_hash != compute_content_hash(payload):
                parser.error("新版本文件尚未写入目录并登记到 loader.py，或内容与登记不一致；请先 --write 并按打印行登记。")
            args.backup_dir.mkdir(parents=True, exist_ok=True)
            backup = args.backup_dir / f"question_bank_before_{release_id}_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
            with (
                sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source,
                sqlite3.connect(str(backup)) as target,
            ):
                source.backup(target)
            result = apply_candidate_release(database, payload, vocabulary, skills)
            result["backup"] = str(backup)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.write:
        standard = {"skills": [
            {"id": s["skill_id"], "section_key": s["section_key"], "name": s["name"],
             "include": s.get("include", ""), "exclude": s.get("exclude", ""),
             "examples": s.get("examples", [])}
            for s in skills
        ]}
        _write_json(teaching_file, standard)
        _write_json(release_file, payload)
        _write_json(vocab_file, vocabulary)
        print(f"wrote {teaching_file.name}, {release_file.name}, {vocab_file.name}")
        print("把以下两行加入 question_bank/knowledge_graph_release/loader.py：")
        print(f"    {revision}: DEFAULT_RELEASE_PATH.with_name('{release_file.name}'),   # _RELEASE_PATHS_BY_TAXONOMY_REVISION")
        print(f"    {revision}: DEFAULT_TAXONOMY_PATH.with_name('{vocab_file.name}'),   # _TAXONOMY_PATHS_BY_REVISION")
    else:
        print("dry-run；--write 生成目录文件，--preview-db 预演数据库应用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
