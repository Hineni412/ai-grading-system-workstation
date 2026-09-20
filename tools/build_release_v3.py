"""Build taxonomy catalog v4 and knowledge-graph release v3 with the skill layer.

Read-only against source catalogs and the generated skill candidates. Implements
the release-construction stage of
docs/requests/2026-09-16-criterion-based-tags-and-mastery-redesign.md section
3.3: append the clustered skill nodes to release v2, add skill->section parent
relations, emit the paired taxonomy revision 5, and validate the result with
the governed release validator.

Usage:
    python tools/build_release_v3.py [--skills-dir DIR] [--write]
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
from question_bank.taxonomy.curriculum_catalog import curriculum_knowledge_node

CATALOG_DIR = ROOT / "question_bank" / "taxonomy" / "catalogs"
RELEASE_V2 = CATALOG_DIR / "knowledge_graph_release_v2.json"
VOCAB_V3 = CATALOG_DIR / "tag_vocabulary_v3.json"
RELEASE_V3 = CATALOG_DIR / "knowledge_graph_release_v3.json"
VOCAB_V4 = CATALOG_DIR / "tag_vocabulary_v4.json"
DEFAULT_SKILLS_DIR = ROOT / "output" / "skill_layer_design"

RELEASE_ID = "kgr_bnu_math_curriculum_2026_09_v3"
TAXONOMY_REVISION = 5
SKILL_ORIGIN = "skill_cluster_2026_09"
SKILL_SOURCE_ID = "skill_layer_evidence_cluster_2026_09"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_skills(skills_dir: Path) -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    for path in sorted(skills_dir.glob("skills_*.json")):
        payload = _load_json(path)
        skills.extend(payload.get("skills", []))
    return skills


def _normalized(value: object) -> str:
    return "".join(str(value or "").split()).casefold()


def _term_name(
    nodes_by_key: Mapping[str, Mapping[str, Any]],
    skill: Mapping[str, Any],
) -> str:
    if skill.get('display_name'):
        return str(skill['display_name'])
    chapter = nodes_by_key.get(str(skill["chapter_key"])) or {}
    chapter_name = str(chapter.get("display_name") or "").strip()
    if chapter_name:
        return f"{chapter_name}｜{skill['name']}"
    return str(skill["name"])


def build_vocabulary(
    vocab: Mapping[str, Any],
    skills: list[dict[str, Any]],
    nodes_by_key: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Return the revision-5 catalog with one knowledge term per skill."""

    terms = [dict(term) for term in vocab["terms"]]
    taken: set[str] = set()
    for term in terms:
        if term.get("dimension") != "knowledge":
            continue
        for value in (term.get("id"), term.get("name"), *term.get("aliases", [])):
            taken.add(_normalized(value))
    new_names: list[str] = []
    for skill in skills:
        name = _term_name(nodes_by_key, skill)
        if _normalized(name) in taken:
            raise ValueError(f"skill term name collides: {name}")
        new_names.append(name)
    for skill, name in zip(skills, new_names):
        node = curriculum_knowledge_node(skill["section_key"]) or {}
        chapter_node = curriculum_knowledge_node(skill["chapter_key"]) or {}
        aliases = []
        for alias in skill.get("aliases", []):
            key = _normalized(alias)
            if key and key not in taken and key != _normalized(name):
                aliases.append(str(alias))
                taken.add(key)
        terms.append(
            {
                "id": str(skill["id"]),
                "dimension": "knowledge",
                "name": name,
                "aliases": aliases,
                "status": "approved",
                "origin": SKILL_ORIGIN,
                "source_paths": [
                    [
                        str(chapter_node.get("label") or ""),
                        str(node.get("label") or ""),
                        str(skill["name"]),
                    ]
                ],
                "retrieval_hints": [
                    str(skill["name"]),
                    str(node.get("label") or ""),
                ],
            }
        )
        taken.add(_normalized(name))
        taken.add(str(skill["id"]))
    knowledge_count = sum(
        1
        for term in terms
        if term.get("dimension") == "knowledge" and term.get("status") == "approved"
    )
    payload = dict(vocab)
    payload["revision"] = TAXONOMY_REVISION
    payload["expected_approved_knowledge_count"] = knowledge_count
    payload["terms"] = terms
    return payload


def build_release(
    release: Mapping[str, Any],
    skills: list[dict[str, Any]],
    nodes_by_key: Mapping[str, Mapping[str, Any]],
    term_names: Mapping[str, str],
) -> dict[str, Any]:
    """Return release v3 = v2 + skill nodes + skill->section parent relations."""

    payload = dict(release)
    payload["release_id"] = RELEASE_ID
    payload["taxonomy_revision"] = TAXONOMY_REVISION
    payload["predecessor_release_id"] = release["release_id"]
    sources = list(payload.get("sources", []))
    sources.append(
        {
            "source_id": SKILL_SOURCE_ID,
            "kind": "evidence_point_clustering",
            "title": "题库判定点证据本地聚类生成的技能层（2026-09）",
            "reference": "tools/build_skill_layer.py + output/skill_layer_design/skills_*.json",
        }
    )
    payload["sources"] = sources

    nodes = [dict(node) for node in payload.get("core_nodes", [])]
    dispositions = [dict(item) for item in payload.get("fine_term_dispositions", [])]
    mappings = [dict(item) for item in payload.get("mappings", [])]
    relations = [dict(item) for item in payload.get("relations", [])]

    seen_node_keys = {str(node["stable_key"]) for node in nodes}
    seen_relation_keys = {str(item["relation_key"]) for item in relations}
    for skill in skills:
        skill_id = str(skill["id"])
        if skill_id in seen_node_keys:
            raise ValueError(f"duplicate skill node: {skill_id}")
        section_key = str(skill["section_key"])
        section = nodes_by_key.get(section_key) or {}
        section_anchor = (section.get("curriculum_anchors") or [""])[0]
        name = term_names[skill_id]
        anchor = (
            f"{section_anchor}/技能·{skill['name']}"
            if section_anchor
            else f"技能·{skill['name']}"
        )
        sample = "；".join(str(item) for item in skill.get("sample_targets", [])[:5])
        nodes.append(
            {
                "stable_key": skill_id,
                "display_name": name,
                "aliases": [],
                "node_kind": "skill",
                "status": "active",
                "definition": (
                    f"由题库判定点证据本地聚类得到的可观察数学技能“{skill['name']}”，"
                    f"挂靠教材节“{section.get('display_name', section_key)}”，"
                    f"覆盖 {skill['count']} 个判定点。"
                ),
                "include_scope": (
                    f"判定点直接要求学生展示“{skill['name']}”相关操作时链接本技能。"
                ),
                "exclude_scope": (
                    "仅作为情境背景或情境标签出现时不计入；"
                    "存在更贴切的同章技能时选择更贴切者。"
                ),
                "curriculum_anchors": [anchor],
                "observable_evidence": sample or str(skill["name"]),
                "rationale": (
                    f"判定点文本聚类 n={skill['count']}；"
                    "生成过程见 tools/build_skill_layer.py 覆盖率报表。"
                ),
                "evidence_source_ids": [SKILL_SOURCE_ID],
            }
        )
        seen_node_keys.add(skill_id)
        dispositions.append(
            {
                "fine_term_id": skill_id,
                "display_name": name,
                "disposition": "direct_core",
                "definition": (
                    f"判定点证据聚类得到的技能词“{skill['name']}”。"
                ),
                "include_scope": (
                    f"判定点直接要求展示“{skill['name']}”时使用。"
                ),
                "exclude_scope": (
                    "仅作为情境标签出现时不作为链接目标；"
                    "拿不准技能归属时回退到所挂节键。"
                ),
                "curriculum_anchors": [anchor],
                "rationale": "技能层由判定点证据聚类产生，一对一映射到同名技能节点。",
                "confidence": 0.8,
                "review_priority": "normal",
                "evidence_source_ids": [SKILL_SOURCE_ID],
            }
        )
        mappings.append(
            {
                "fine_term_id": skill_id,
                "stable_key": skill_id,
                "mapping_role": "primary",
                "rationale": "技能词一对一映射到同名技能节点。",
            }
        )
        relation_key = stable_record_hash(
            "release-relation",
            RELEASE_ID,
            skill_id,
            section_key,
            "parent",
        )
        if relation_key in seen_relation_keys:
            raise ValueError(f"duplicate relation key: {relation_key}")
        relations.append(
            {
                "relation_key": relation_key,
                "source_key": skill_id,
                "target_key": section_key,
                "relation_type": "parent",
                "basis_kind": "empirical_evidence",
                "strength": "required",
                "rationale": (
                    f"技能“{skill['name']}”由该节判定点证据聚类产生，挂靠本节。"
                ),
                "evidence_source_ids": [SKILL_SOURCE_ID],
                "source_locator": "output/skill_layer_design/",
            }
        )
        seen_relation_keys.add(relation_key)

    payload["core_nodes"] = nodes
    payload["fine_term_dispositions"] = dispositions
    payload["mappings"] = mappings
    payload["relations"] = relations
    payload.pop("content_hash", None)
    payload["content_hash"] = compute_content_hash(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skills-dir", type=Path, default=DEFAULT_SKILLS_DIR)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--teaching-standard", type=Path, help="Build the explicit chapter-one/two repair as a new immutable release")
    repair = parser.add_mutually_exclusive_group()
    repair.add_argument('--preview-db', type=Path, help='Run the repair on a new temporary SQLite copy; original remains read-only')
    repair.add_argument('--activate-and-repair-db', type=Path, help='Explicitly activate and repair this database, after authorization')
    parser.add_argument('--backup-dir', type=Path, help='Required for --activate-and-repair-db; creates a new backup file')
    args = parser.parse_args()

    if args.teaching_standard:
        release_payload, vocabulary = build_teaching_release(_load_json(args.teaching_standard))
        report = validate_release(KnowledgeGraphRelease.from_mapping(release_payload), vocabulary)
        report.raise_for_errors()
        if args.preview_db or args.activate_and_repair_db:
            if args.write:
                parser.error('repair and catalog --write are separate operations')
            import sqlite3
            import tempfile
            from datetime import datetime
            database = (args.preview_db or args.activate_and_repair_db).resolve(strict=True)
            if args.preview_db:
                with tempfile.TemporaryDirectory(prefix='criterion_repair_preview_') as folder:
                    copy_path = Path(folder) / 'question_bank.db'
                    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(copy_path) as target:
                        source.backup(target)
                    source.close()
                    target.close()
                    result = apply_teaching_repair(copy_path, _load_json(args.teaching_standard), release_payload, vocabulary)
            else:
                if not args.backup_dir:
                    parser.error('--activate-and-repair-db requires --backup-dir')
                args.backup_dir.mkdir(parents=True, exist_ok=True)
                backup = args.backup_dir / f'question_bank_before_skill_repair_{datetime.now():%Y%m%d_%H%M%S_%f}.db'
                with backup.open('xb'):
                    pass
                with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(backup) as target:
                    source.backup(target)
                source.close()
                target.close()
                result = apply_teaching_repair(database, _load_json(args.teaching_standard), release_payload, vocabulary)
                result['backup'] = str(backup)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.write:
            _write_json(CATALOG_DIR / 'knowledge_graph_release_v4.json', release_payload)
            _write_json(CATALOG_DIR / 'tag_vocabulary_v5.json', vocabulary)
        print(f"teaching skills={len(_load_json(args.teaching_standard)['skills'])}; release validated; written={args.write}")
        return 0

    release_v2 = _load_json(RELEASE_V2)
    vocab_v3 = _load_json(VOCAB_V3)
    skills = _load_skills(args.skills_dir)
    if not skills:
        print("no skills found; run tools/build_skill_layer.py first")
        return 1
    nodes_by_key = {
        str(node["stable_key"]): node for node in release_v2["core_nodes"]
    }
    term_names = {str(s["id"]): _term_name(nodes_by_key, s) for s in skills}

    vocab_v4 = build_vocabulary(vocab_v3, skills, nodes_by_key)
    release_v3 = build_release(release_v2, skills, nodes_by_key, term_names)

    release = KnowledgeGraphRelease.from_mapping(release_v3)
    report = validate_release(release, vocab_v4)
    print(
        f"release {release.release_id}: nodes={len(release_v3['core_nodes'])} "
        f"relations={len(release_v3['relations'])} "
        f"terms={vocab_v4['expected_approved_knowledge_count']} "
        f"errors={len(report.errors)} warnings={len(report.warnings)}"
    )
    for issue in report.errors[:20]:
        print(f"  ERROR {issue.path}: {issue.message}")
    for issue in report.warnings[:20]:
        print(f"  WARN  {issue.path}: {issue.message}")
    if not report.valid:
        return 1
    if args.write:
        _write_json(VOCAB_V4, vocab_v4)
        _write_json(RELEASE_V3, release_v3)
        print(f"wrote {VOCAB_V4.name}, {RELEASE_V3.name}")
    else:
        print("dry-run; pass --write to emit catalogs")
    return 0


def build_teaching_release(standard: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reuse the release builder, retaining broad old skills as section aliases."""
    import copy
    old_release = _load_json(RELEASE_V3)
    old_vocabulary = _load_json(VOCAB_V4)
    nodes = {node['stable_key']: node for node in old_release['core_nodes']}
    skills = [{**item, 'chapter_key': item['section_key'].rsplit('_', 1)[0],
               'count': 0, 'aliases': [], 'sample_targets': item['examples']}
              for item in standard['skills']]
    names = {s['id']: f"{nodes[s['section_key']]['display_name']}｜技能·{s['name']}" for s in skills}
    for skill in skills:
        skill['display_name'] = names[skill['id']]
    # The legacy builder names terms by chapter; replace only the generated
    # entries with full section paths before validating the paired release.
    vocabulary = build_vocabulary(old_vocabulary, skills, nodes)
    payload = build_release(old_release, skills, nodes, names)
    payload['release_id'] = 'kgr_bnu_math_curriculum_2026_09_v4'
    payload['taxonomy_revision'] = vocabulary['revision'] = 6
    parents = {r['source_key']: r['target_key'] for r in old_release['relations'] if r['relation_type'] == 'parent'}
    prefixes = tuple(f"sk_bnu24_math_g8_upper_{chapter}_" for chapter in standard['chapters'])
    retired = {key for key in nodes if key.startswith(prefixes)}
    definitions = {s['id']: s for s in skills}
    teaching_source = 'teaching_skill_definition_2026_09'
    for term in vocabulary['terms']:
        if term['id'] in definitions:
            skill = definitions[term['id']]
            term.update(name=names[term['id']], origin='teaching_skill_standard_2026_09',
                        retrieval_hints=skill['examples'])
    for node in payload['core_nodes']:
        key = node['stable_key']
        if key in retired:
            node['status'] = 'retired'
        if key in definitions:
            skill = definitions[key]
            node.update(definition=skill['include'], include_scope=skill['include'],
                        exclude_scope=skill['exclude'], observable_evidence='；'.join(skill['examples']),
                        evidence_source_ids=[teaching_source],
                        rationale='以独立可观察的数学操作定义技能；模糊证据仅归小节。')
    for disposition in payload['fine_term_dispositions']:
        key = disposition['fine_term_id']
        if key in retired:
            disposition.update(disposition='maps_to_core', rationale='兼容旧的混合技能，只能说明小节证据，不能推断任一新技能。')
        elif key in definitions:
            skill = definitions[key]
            disposition.update(display_name=names[key], definition=skill['include'],
                               include_scope=skill['include'], exclude_scope=skill['exclude'], evidence_source_ids=[teaching_source])
    for mapping in payload['mappings']:
        if mapping['fine_term_id'] in retired:
            mapping.update(stable_key=parents[mapping['fine_term_id']], rationale='旧混合技能兼容上溯到原小节。')
    payload['relations'] = [r for r in payload['relations'] if r['source_key'] not in retired and r['target_key'] not in retired]
    for relation in payload['relations']:
        if relation['source_key'] in definitions:
            relation['evidence_source_ids'] = [teaching_source]
            relation['source_locator'] = 'question_bank/taxonomy/catalogs/teaching_skills_g8_core.json'
            relation['rationale'] = '按可观察数学操作及教材小节归属定义。'
        relation['relation_key'] = stable_record_hash('release-relation', payload['release_id'],
                                                     relation['source_key'], relation['target_key'], relation['relation_type'])
    payload['replacements'] = copy.deepcopy(payload.get('replacements', [])) + [
        {'retired_key': key, 'replacement_key': parents[key], 'replacement_kind':'broader',
         'rationale':'旧技能含义混杂，保留为小节级兼容证据。'} for key in sorted(retired)]
    payload['sources'][-1] = {'source_id': teaching_source, 'kind':'teaching_skill_definition',
                             'title':'第一、二章可观察数学技能定义',
                             'reference':'question_bank/taxonomy/catalogs/teaching_skills_g8_core.json'}
    payload['content_hash'] = compute_content_hash(payload)
    return payload, vocabulary


def apply_teaching_repair(db_path: Path, standard: Mapping[str, Any], payload: Mapping[str, Any], vocabulary: Mapping[str, Any]) -> dict[str, Any]:
    """Use existing release/link/derived-tag boundaries. No models or snapshots.

    Links and derived ownership are committed together. If that transaction
    fails, reactivate the previous immutable release; its old links remain.
    The CLI backs up before invoking this function on an existing database.
    """
    import sqlite3
    from collections import Counter
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.database.schema import connect
    from question_bank.knowledge_graph_release.repository import stage_release, activate_release, active_release_id, rollback_release
    from question_bank.solution_evidence.knowledge_links import load_point_links, replace_point_links, resolve_anchor_keys, refresh_question_scope_summary
    from question_bank.services.question_write_service import refresh_derived_ownership_tags
    from tools.build_skill_layer import match_teaching_skill

    previous = active_release_id(db_path)
    candidate = KnowledgeGraphRelease.from_mapping(payload)
    if previous != payload['predecessor_release_id']:
        raise ValueError('活动版本与修复前置版本不符，请重新预演；未修改数据库。')
    resolver = CurrentKnowledgeResolver(candidate, vocabulary)
    plans = []
    counts: Counter[str] = Counter()
    usage: Counter[str] = Counter()
    with connect(db_path) as conn:
        rows = conn.execute('''SELECT * FROM (
            SELECT v.*, ROW_NUMBER() OVER(PARTITION BY v.question_id ORDER BY v.created_at DESC, v.evidence_version_id DESC) AS seq
            FROM question_solution_evidence_versions v JOIN questions q ON q.id=v.question_id
            WHERE v.status IN ('approved','proposed') AND q.is_deleted=0
        ) WHERE seq=1''').fetchall()
        grouped = load_point_links(db_path, [r['evidence_version_id'] for r in rows], previous, connection=conn)
        all_ids = [r[0] for r in conn.execute('SELECT id FROM questions WHERE is_deleted=0')]
        for row in rows:
            points = []
            old = grouped.get(row['evidence_version_id'], {})
            for part in json.loads(row['evidence_json']).get('parts', []):
                for point in part.get('evidence_points', []):
                    pid = point['evidence_point_id']
                    direct_old = [link.stable_key for link in old.get(pid, ()) if link.role == 'direct' and link.resolution_status == 'resolved']
                    anchors = resolve_anchor_keys(conn, direct_old, preferred_release_id=previous)
                    chapters = [key for key in anchors['chapters'] if key in
                                {f'kp_bnu24_math_g8_upper_{chapter}' for chapter in standard['chapters']}]
                    skill = match_teaching_skill(point.get('target', ''), chapters, standard) if chapters else None
                    link_weights: dict[tuple[str, str], float] = {}
                    for link in old.get(pid, ()):
                        if link.resolution_status != 'resolved':
                            continue
                        for identity in resolver.resolve(link.stable_key):
                            key = (link.role, identity.stable_key)
                            link_weights[key] = link_weights.get(key, 0.0) + link.weight
                    if skill and len(chapters) == len(anchors['chapters']):
                        link_weights = {key: value for key, value in link_weights.items() if key[0] != 'direct'}
                        link_weights[('direct', skill)] = 1.0
                        counts['new_skill_points'] += 1
                        usage[skill] += 1
                    elif chapters:
                        counts['section_fallback_points'] += 1
                    total = sum(weight for (role, _), weight in link_weights.items() if role == 'direct')
                    links = [{'term_id': key, 'stable_key': key, 'role': role,
                              'weight': weight / total if role == 'direct' and total else weight}
                             for (role, key), weight in link_weights.items() if weight > 0]
                    points.append({'part_id': part['part_id'], 'evidence_point_id': pid, 'links': links})
                    counts['points'] += 1
            plans.append((row['question_id'], row['evidence_version_id'], points))
    stage_release(db_path, candidate, actor_ref='criterion_skill_repair',
                  source_reference='question_bank/taxonomy/catalogs/teaching_skills_g8_core.json', taxonomy_catalog=vocabulary)
    activate_release(db_path, candidate.release_id, expected_active_release_id=previous,
                     actor_ref='criterion_skill_repair', reason='启用可观察技能定义，旧混合技能上溯到小节')
    try:
        with connect(db_path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            for qid, version, points in plans:
                counts['links_written'] += replace_point_links(conn, evidence_version_id=version, question_id=qid,
                    graph_release_id=candidate.release_id, points=points, source_reference='teaching_skill_repair_v1')
            for qid in all_ids:
                refresh_question_scope_summary(conn, qid)
                refresh_derived_ownership_tags(conn, qid)
            counts['duplicate_tag_groups_after'] = conn.execute('''SELECT COUNT(*) FROM (
                SELECT question_id,tag_type,tag_value FROM question_tags GROUP BY question_id,tag_type,tag_value HAVING COUNT(*)>1
            )''').fetchone()[0]
            if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('修复后引用关系校验未通过')
    except Exception:
        rollback_release(db_path, previous, expected_active_release_id=candidate.release_id,
                         actor_ref='criterion_skill_repair', reason='标签或链接事务失败，恢复旧活动版本')
        raise
    return {'previous_release': previous, 'new_release': candidate.release_id,
            'questions_with_evidence': len(plans), 'questions_refreshed': len(all_ids),
            **dict(counts), 'new_skills_with_direct_evidence': len(usage),
            'skill_point_counts': {skill['name']: usage[skill['id']] for skill in standard['skills']},
            'snapshots_changed': 0, 'model_calls': 0}


if __name__ == "__main__":
    raise SystemExit(main())
