"""Chapter type planning, immutable input snapshots and automatic release validation."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from question_bank.atomic_files import write_json_atomic
from question_bank.current_knowledge import CurrentKnowledgeResolver
from question_bank.knowledge_graph_release.repository import load_active_release
from question_bank.knowledge_graph_release.loader import load_taxonomy_catalog_for_release
from question_bank.question_types import is_type_key
from question_bank.solution_evidence.part_assessments import load_profiles, source_input_key
from question_bank.taxonomy.curriculum_catalog import curriculum_volume_contract

AUTOMATIC_VOLUME_ID = "bnu24-math-g8-lower"
MIN_QUESTIONS = 30
MIN_PAPERS = 3
MAX_CHAPTER_INPUT_CHARS = 180_000


class ChapterTypeInvalid(ValueError):
    pass


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


@contextmanager
def _reading(db_path: Path, external_connection: sqlite3.Connection | None = None):
    if external_connection is not None:
        yield external_connection
        return
    connection = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.close()


def _question_source_rows(connection):
    rows = [dict(row) for row in connection.execute("SELECT * FROM questions WHERE is_deleted=0 ORDER BY id")]
    paper_sources = defaultdict(set)
    for member in connection.execute("SELECT q.id,p.id FROM questions q JOIN papers p ON p.id=q.paper_id "
        "WHERE p.deleted_at IS NULL AND COALESCE(p.import_status,'')<>'deleted' "
        "UNION SELECT o.question_id,p.id FROM paper_question_occurrences o JOIN papers p ON p.id=o.paper_id "
        "WHERE p.deleted_at IS NULL AND COALESCE(p.import_status,'')<>'deleted'"):
        paper_sources[int(member[0])].add(int(member[1]))
    for row in rows:
        row.update(paper_ids=sorted(paper_sources[int(row["id"])]), source_hash=source_input_key(row))
    return rows


def _chapter_inputs(db_path: Path, data_root: Path, volume_id: str, *, verify_source=True,
                    external_connection: sqlite3.Connection | None = None):
    volume = curriculum_volume_contract(volume_id)
    if volume is None:
        raise ChapterTypeInvalid("教材册不存在")
    sections = {row["knowledge_id"]: row for row in volume["sections"]}
    chapter_keys = {row["id"]: row["knowledge_id"] for row in volume["chapters"]}
    section_aliases = {str(value): key for key, row in sections.items()
        for value in (row["id"], key, row["name"])}
    chapter_aliases = {str(value): row["knowledge_id"] for row in volume["chapters"]
        for value in (row["id"], row["knowledge_id"], row["name"])}
    with _reading(db_path, external_connection=external_connection) as conn:
        rows = _question_source_rows(conn)
        tags = defaultdict(list)
        for row in conn.execute("SELECT question_id,tag_type,tag_value,source FROM question_tags"):
            tags[int(row[0])].append(dict(row))
        scopes = {int(row["question_id"]): str(row["primary_section_id"])
                  for row in conn.execute("SELECT question_id,primary_section_id FROM question_scope_summary")}
        from question_bank.services.question_revision import question_revision
        located = []
        for row in rows:
            qid = int(row["id"])
            section = scopes.get(qid, "")
            if section not in sections:
                section = next((section_aliases[str(tag["tag_value"])] for tag in tags[qid]
                    if tag["tag_type"] == "curriculum_section" and str(tag["tag_value"]) in section_aliases), "")
            chapter = chapter_keys[sections[section]["chapter_id"]] if section in sections else next((
                chapter_aliases[str(tag["tag_value"])] for tag in tags[qid]
                if tag["tag_type"] == "exam_scope" and str(tag["tag_value"]) in chapter_aliases), "")
            if not chapter:
                continue
            row.update(chapter_id=chapter, section_id=section,
                       question_revision=question_revision(conn, qid),
                       primary_type=next((str(tag["tag_value"]) for tag in tags[qid]
                           if tag["tag_type"] == "knowledge_point" and is_type_key(tag["tag_value"])), ""),
                       teacher_type=any(tag["source"] == "manual" and is_type_key(tag["tag_value"])
                                        for tag in tags[qid]))
            located.append(row)
        profiles = load_profiles(db_path, [int(row["id"]) for row in located],
            connection=conn, data_root=data_root, verify_source=verify_source)
        from question_bank.solution_evidence.knowledge_links import load_point_links
        from question_bank.taxonomy.curriculum_catalog import curriculum_knowledge_node
        active_row = conn.execute("SELECT release_id FROM knowledge_graph_releases WHERE status='active'").fetchone()
        release_id = str(active_row[0]) if active_row else ""
        linked = load_point_links(db_path, [str(profile["evidence_version_id"]) for profile in profiles.values()],
                                 release_id, connection=conn) if release_id else {}
        groups = defaultdict(list)
        for row in located:
            profile = profiles.get(int(row["id"]), {})
            row.update(available=bool(profile.get("available")),
                       evidence_version_id=str(profile.get("evidence_version_id") or ""),
                       evidence=profile.get("evidence") or {},
                       evidence_hash=_hash(profile.get("evidence") or {}))
            point_knowledge = {}
            for point_id, links in linked.get(row["evidence_version_id"], {}).items():
                point_knowledge[point_id] = [{"stable_key": link.stable_key, "role": link.role,
                    "name": node["label"], "volume_id": node["volume_id"]}
                    for link in links if link.resolution_status == "resolved"
                    and (node := curriculum_knowledge_node(link.stable_key)) is not None and node["level"] == 3]
            row["point_knowledge"] = point_knowledge
            groups[row["chapter_id"]].append(row)
    return volume, groups


def preview_chapter_type_plan(*, db_path: Path, data_root: Path, volume_id: str,
                             question_ids: Sequence[int] = (), pending_question_count: int = 0) -> dict[str, Any]:
    if type(pending_question_count) is not int or pending_question_count < 0:
        raise ChapterTypeInvalid("待导入题数必须为非负整数")
    pending = sorted({int(value) for value in question_ids if int(value) > 0})
    release = load_active_release(db_path)
    base_id = release.release_id if release else ""
    if volume_id != AUTOMATIC_VOLUME_ID or release is None or release.taxonomy_revision < 11:
        plan = {"base_release_id": base_id, "volume_id": volume_id,
                "chapters": [], "planned_requests": 0, "model_calls": 0,
                "pending_question_ids": pending, "pending_question_count": pending_question_count,
                "source_question_hashes": {}, "policy_version": "chapter-types-v1-30q-3p"}
        plan["input_fingerprint"] = _hash(plan)
        return plan
    volume, groups = _chapter_inputs(db_path, data_root, volume_id)
    resolver = CurrentKnowledgeResolver(release, load_taxonomy_catalog_for_release(release))
    from question_bank.question_types import chapter_target_kind
    chapters = []
    located_ids = {int(row["id"]) for rows in groups.values() for row in rows}
    with _reading(db_path) as connection:
        unlocated_pending = [row for row in _question_source_rows(connection)
            if int(row["id"]) in pending and int(row["id"]) not in located_ids]
    pending_papers = {pid for row in unlocated_pending for pid in row["paper_ids"]}
    possible_new_count = len(unlocated_pending) + pending_question_count
    source_hashes = {str(row["id"]): row["source_hash"] for row in unlocated_pending}
    for chapter in volume["chapters"]:
        key = str(chapter["knowledge_id"])
        rows = groups.get(key, [])
        source_hashes.update({str(row["id"]): row["source_hash"] for row in rows})
        usable = [row for row in rows if row["available"]]
        expected = [row for row in rows if row["available"] or int(row["id"]) in pending]
        papers = {pid for row in expected for pid in row["paper_ids"]}
        unclassified = sum(not row["primary_type"] for row in expected)
        question_count = len(expected) + possible_new_count
        paper_count = len(papers | pending_papers) + int(pending_question_count > 0)
        typed = chapter_target_kind(resolver, volume_id, key) == "type"
        ready = question_count >= MIN_QUESTIONS and paper_count >= MIN_PAPERS
        ready = ready and (not typed or unclassified + possible_new_count >= MIN_QUESTIONS)
        chapters.append({"chapter_id": key, "label": str(chapter["name"]),
            "question_count": len(usable), "paper_count": len({pid for row in usable for pid in row["paper_ids"]}),
            "unclassified_count": sum(not row["primary_type"] for row in usable),
            "possible_question_count": question_count, "possible_paper_count": paper_count,
            "planned_requests": int(ready), "target_kind": "type" if typed else "knowledge"})
    plan = {"base_release_id": base_id, "volume_id": volume_id, "chapters": chapters,
        "planned_requests": sum(row["planned_requests"] for row in chapters), "model_calls": 0,
        "pending_question_ids": pending, "pending_question_count": pending_question_count,
        "source_question_hashes": source_hashes, "policy_version": "chapter-types-v1-30q-3p"}
    plan["input_fingerprint"] = _hash(plan)
    return plan


def authorized_chapter_inputs(*, db_path: Path, data_root: Path, authorization: Mapping[str, Any]):
    if authorization.get("confirmed") is not True:
        raise ChapterTypeInvalid("题型整理没有本次费用授权")
    if authorization.get("volume_id") != AUTOMATIC_VOLUME_ID:
        raise ChapterTypeInvalid("本次自动题型只适用于八下")
    plan_fields = ("base_release_id", "volume_id", "chapters", "planned_requests", "model_calls",
                     "pending_question_ids", "pending_question_count", "source_question_hashes", "policy_version")
    if _hash({key: authorization.get(key) for key in plan_fields}) != authorization.get("input_fingerprint"):
        raise ChapterTypeInvalid("题型整理授权快照与费用预估不一致")
    chapters = [row for row in authorization.get("chapters", []) if row.get("planned_requests") == 1]
    limit = authorization.get("request_limit")
    if type(limit) is not int or limit != len(chapters) or limit != authorization.get("planned_requests"):
        raise ChapterTypeInvalid("题型整理请求上限与已确认预估不一致")
    release = load_active_release(db_path)
    if release is not None and release.taxonomy_revision < 11:
        raise ChapterTypeInvalid("历史技能标准不自动整理题型，未发起请求")
    if release is None or release.release_id != authorization.get("base_release_id"):
        raise ChapterTypeInvalid("活动标准已变化，题型整理未发起请求")
    volume, groups = _chapter_inputs(db_path, data_root, AUTOMATIC_VOLUME_ID)
    current = {str(row["id"]): row for rows in groups.values() for row in rows}
    source_hashes = authorization.get("source_question_hashes") or {}
    with _reading(db_path) as connection:
        current_sources = {str(row["id"]): row for row in _question_source_rows(connection)}
    for qid, expected in source_hashes.items():
        if qid not in current_sources or current_sources[qid]["source_hash"] != expected:
            raise ChapterTypeInvalid("已确认题目内容已变化，题型整理未发起请求")
        if qid in current and current[qid]["source_hash"] != expected:
            raise ChapterTypeInvalid("已确认题目内容已变化，题型整理未发起请求")
    pending_ids = {str(qid) for qid in authorization.get("pending_question_ids") or []}
    pending_count = authorization.get("pending_question_count", 0)
    if type(pending_count) is not int or pending_count < 0:
        raise ChapterTypeInvalid("题型整理授权的待导入题数无效，未发起请求")
    unknown_ids = set(current) - set(source_hashes) - pending_ids
    if len(unknown_ids) > pending_count:
        raise ChapterTypeInvalid("当前题目超出已确认待导入题数，题型整理未发起请求")
    ready = []
    for chapter in chapters:
        rows = [row for row in groups.get(str(chapter["chapter_id"]), []) if row["available"]]
        question_count = len(rows)
        papers = {pid for row in rows for pid in row["paper_ids"]}
        if question_count < MIN_QUESTIONS or len(papers) < MIN_PAPERS:
            continue
        if chapter.get("target_kind") == "type":
            rows = [row for row in rows if not row["primary_type"]]
            if len(rows) < MIN_QUESTIONS:
                continue
        question_limit = chapter.get("possible_question_count", chapter.get("question_count"))
        paper_limit = chapter.get("possible_paper_count", chapter.get("paper_count"))
        if any(type(value) is not int or value < 0 for value in (question_limit, paper_limit)):
            raise ChapterTypeInvalid("题型整理授权缺少本章资料上限，请重新确认费用")
        if question_count > question_limit or len(papers) > paper_limit:
            raise ChapterTypeInvalid("本章可用题目或来源试卷超出已确认上限，题型整理未发起请求")
        ready.append((dict(chapter), rows))
    return release, volume, ready


def chapter_model_payload(release, volume, chapter, rows):
    section_ids = {row["knowledge_id"] for row in volume["sections"]
                   if row["chapter_id"] == next(item["id"] for item in volume["chapters"]
                   if item["knowledge_id"] == chapter["chapter_id"])}
    parent_of = {edge["source_key"]: edge["target_key"] for edge in release.payload["relations"] if edge["relation_type"] == "parent"}
    existing = [{key: node.get(key) for key in ("stable_key", "display_name", "definition", "include_scope", "exclude_scope")}
        for node in release.payload["core_nodes"] if is_type_key(node["stable_key"])
        and node.get("status") == "active" and parent_of.get(node["stable_key"]) in section_ids]
    payload = {"task": "organize_chapter_question_types", "chapter": chapter,
        "sections": [{"section_id": row["knowledge_id"], "name": row["name"]} for row in volume["sections"] if row["knowledge_id"] in section_ids],
        "existing_types": existing, "questions": [{"question_ref": f"q{row['id']}",
            "question_text": row["question_text"], "answer_text": row.get("answer_text") or "",
            "section_id": row["section_id"], "solution_evidence": row["evidence"], "point_knowledge": row["point_knowledge"]} for row in rows]}
    if len(json.dumps(payload, ensure_ascii=False)) > MAX_CHAPTER_INPUT_CHARS:
        raise ChapterTypeInvalid("本章资料超过单次已确认请求容量，未发送也不追加请求")
    return payload

def validate_chapter_result(response: object, payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(response, Mapping) or set(response) != {"types", "assignments"}:
        raise ChapterTypeInvalid("本章题型返回字段不完整")
    raw_types, assignments = response["types"], response["assignments"]
    if not isinstance(raw_types, list) or not isinstance(assignments, list):
        raise ChapterTypeInvalid("本章题型清单与分类必须为数组")
    wanted = {row["question_ref"] for row in payload["questions"]}
    sections = {row["section_id"] for row in payload["sections"]}
    existing = {row["stable_key"] for row in payload["existing_types"]}
    names = {re.sub(r"\s+", "", row["display_name"].rsplit("题型·", 1)[-1]) for row in payload["existing_types"]}
    types = []
    ids = set(existing)
    for row in raw_types:
        if not isinstance(row, Mapping) or set(row) != {"type_id", "name", "definition", "include_scope", "exclude_scope", "section_id", "anchors"}:
            raise ChapterTypeInvalid("新题型缺少定义或边界")
        strings = {key: str(row[key]).strip() for key in ("type_id", "name", "definition", "include_scope", "exclude_scope", "section_id")}
        if any(not value for value in strings.values()) or any(not isinstance(row[key], str) for key in strings):
            raise ChapterTypeInvalid("新题型文字或标识为空")
        if strings["section_id"] not in sections or strings["type_id"] in ids or not re.fullmatch(r"new_[a-z0-9_]{1,60}", strings["type_id"]):
            raise ChapterTypeInvalid("新题型标识重复或小节不属于本章")
        name = re.sub(r"\s+", "", strings["name"])
        if name in names:
            raise ChapterTypeInvalid("本章题型名称重复，保留原有标准")
        anchors = row["anchors"]
        if not isinstance(anchors, list) or not 1 <= len(anchors) <= 2 or len(set(anchors)) != len(anchors) or any(value not in wanted for value in anchors):
            raise ChapterTypeInvalid("新题型锚题不属于本次本章资料")
        types.append({**strings, "anchors": list(anchors)})
        names.add(name)
        ids.add(strings["type_id"])
    seen = set()
    counts = Counter()
    cleaned = []
    primary_for = {}
    for row in assignments:
        if not isinstance(row, Mapping) or set(row) != {"question_ref", "primary_type_id", "secondary_type_ids"}:
            raise ChapterTypeInvalid("题目题型分类字段不完整")
        ref, primary, secondary = row["question_ref"], row["primary_type_id"], row["secondary_type_ids"]
        if ref not in wanted or ref in seen or primary not in ids:
            raise ChapterTypeInvalid("题目重复、遗漏或主题型未定义")
        if not isinstance(secondary, list) or len(secondary) > 2 or len(set(secondary)) != len(secondary) or primary in secondary or any(key not in ids for key in secondary):
            raise ChapterTypeInvalid("次题型超过上限或与主题型冲突")
        seen.add(ref)
        counts[primary] += 1
        primary_for[ref] = primary
        cleaned.append(dict(row))
    if seen != wanted:
        raise ChapterTypeInvalid("本章可用题目没有全部分配主题型")
    for row in types:
        if counts[row["type_id"]] < 3 or any(primary_for[ref] != row["type_id"] for ref in row["anchors"]):
            raise ChapterTypeInvalid("新题型少于 3 道主题型题目或锚题与分类不一致")
    return {"types": types, "assignments": cleaned}


def build_chapter_release(base, chapter_results, *, taxonomy_revision: int | None = None):
    from question_bank.knowledge_graph_release.contracts import KnowledgeGraphRelease, compute_content_hash
    from question_bank.knowledge_graph_release.validation import validate_release
    from question_bank.taxonomy.attribute_definitions import enrich_attribute_definitions
    from tools.build_type_release import build_type_release
    vocabulary = enrich_attribute_definitions(load_taxonomy_catalog_for_release(base))
    keys = {node["stable_key"] for node in base.payload["core_nodes"]}
    new_types = []
    label_map = {}
    for chapter, rows, response in chapter_results:
        by_ref = {f"q{row['id']}": row for row in rows}
        alias_map = {key: key for key in keys if is_type_key(key)}
        for row in response["types"]:
            section = row["section_id"]
            number = next((value for value in range(1, 100) if f"{section}_t{value:02d}" not in keys), None)
            if number is None:
                raise ChapterTypeInvalid("本节题型稳定编号已用完，未改变原编号")
            stable_key = f"{section}_t{number:02d}"
            keys.add(stable_key)
            alias = f"{chapter['chapter_id']}:{row['type_id']}"
            alias_map[row["type_id"]] = stable_key
            new_types.append({"type_id": alias, "stable_key": stable_key, "section_key": section,
                "name": row["name"], "definition": row["definition"], "include": row["include_scope"],
                "exclude": row["exclude_scope"], "anchors": [
                    f"题干：{by_ref[ref]['question_text']}\n答案：{by_ref[ref].get('answer_text') or ''}"
                    for ref in row["anchors"]], "rationale": "本章题目自动整理并通过本地检查。"})
        for assignment in response["assignments"]:
            source = by_ref[assignment["question_ref"]]
            if source["primary_type"] or source["teacher_type"]:
                continue
            label_map[int(source["id"])] = {"chapter_id": source["chapter_id"], "primary_type": alias_map[assignment["primary_type_id"]],
                "secondary_types": [alias_map[key] for key in assignment["secondary_type_ids"]],
                "evidence_version_id": source["evidence_version_id"], "source_hash": source["source_hash"],
                "evidence_hash": source["evidence_hash"], "question_revision": source["question_revision"]}
    revision = max(13, int(base.taxonomy_revision) + 1, int(taxonomy_revision or 0))
    release_id = "kgr_auto_types_" + _hash([base.release_id, revision, chapter_results])[:24]
    payload, catalog, _ = build_type_release(base.to_dict(), vocabulary, new_types,
        release_id=release_id, taxonomy_revision=revision,
        source_id="automatic_chapter_question_types", source_locator="local:automatic-chapter-types")
    for source in payload["sources"]:
        if source["source_id"] == "automatic_chapter_question_types":
            source["title"] = "本章题型自动整理与本地检查"
    anchors_by_key = {row["stable_key"]: row["anchors"] for row in new_types}
    for term in catalog["terms"]:
        if term["id"] in anchors_by_key:
            term["anchors"] = list(anchors_by_key[term["id"]])
    new_keys = set(anchors_by_key)
    for node in payload["core_nodes"]:
        if node["stable_key"] in new_keys:
            node["rationale"] = "本章题目自动整理并通过本地检查。"
    for row in payload["fine_term_dispositions"]:
        if row["fine_term_id"] in new_keys:
            row["rationale"] = "自动整理题型一对一映射到整题任务节点。"
    for edge in payload["relations"]:
        if edge["source_key"] in new_keys:
            edge["rationale"] = "自动整理题型依据本章资料挂靠当前小节。"
    payload["taxonomy_catalog"] = catalog
    policy = dict(base.payload.get("training_target_policy") or {})
    policy["knowledge_fallback_volumes"] = sorted({AUTOMATIC_VOLUME_ID, *policy.get("knowledge_fallback_volumes", [])})
    payload["training_target_policy"] = policy
    payload["content_hash"] = compute_content_hash(payload)
    candidate = KnowledgeGraphRelease.from_mapping(payload)
    validate_release(candidate, catalog).raise_for_errors()
    return candidate, catalog, label_map


def publish_chapter_release(*, db_path: Path, data_root: Path, base_release_id: str,
                            candidate, catalog, labels: Mapping[int, Mapping[str, Any]]):
    from question_bank.database.schema import connect
    from question_bank.knowledge_graph_release.repository import stage_release, activate_release
    from question_bank.services.question_write_service import sync_question_type_labels, refresh_derived_ownership_tags
    from question_bank.solution_evidence.knowledge_links import carry_forward_effective_links, refresh_question_scope_summary
    current = load_active_release(db_path)
    if current is not None and current.release_id == candidate.release_id:
        return
    directory = Path(data_root) / "question_bank" / "knowledge_releases" / candidate.release_id
    from backend.files.data_transfer_service import ensure_controlled_path
    ensure_controlled_path(directory, Path(data_root) / "question_bank")
    directory.mkdir(parents=True, exist_ok=True)
    write_json_atomic(directory / "release.json", candidate.to_dict())
    write_json_atomic(directory / "taxonomy.json", dict(catalog))
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute("SELECT release_id FROM knowledge_graph_releases WHERE status='active'").fetchone()
        if active is None or str(active[0]) != base_release_id:
            raise ChapterTypeInvalid("发布前活动标准已变化，保留原活动版")
        _volume, current_groups = _chapter_inputs(db_path, data_root, AUTOMATIC_VOLUME_ID,
                                                  external_connection=conn)
        for chapter_id in {str(label['chapter_id']) for label in labels.values()}:
            current_unclassified = {int(row['id']) for row in current_groups.get(chapter_id, [])
                                    if row['available'] and not row['primary_type']}
            covered = {int(qid) for qid, label in labels.items() if label['chapter_id'] == chapter_id}
            if current_unclassified != covered:
                raise ChapterTypeInvalid('发布前本章可用待归类题已变化，保留原活动版')
        profiles = load_profiles(db_path, list(labels), connection=conn, data_root=data_root)
        from question_bank.services.question_revision import question_revision
        for qid, label in labels.items():
            if question_revision(conn, qid) != label["question_revision"]:
                raise ChapterTypeInvalid("发布前题目修订已变化，保留原活动版")
            row = conn.execute("SELECT * FROM questions WHERE id=? AND is_deleted=0", (qid,)).fetchone()
            profile = profiles.get(qid, {})
            if row is None or source_input_key(dict(row)) != label["source_hash"] or not profile.get("available") or profile.get("evidence_version_id") != label["evidence_version_id"] or _hash(profile["evidence"]) != label["evidence_hash"]:
                raise ChapterTypeInvalid("发布前题目或当前判定版本已变化，保留原活动版")
        stage_release(db_path, candidate, actor_ref="automatic-chapter-types", source_reference=candidate.release_id,
            taxonomy_catalog=catalog, external_connection=conn)
        activate_release(db_path, candidate.release_id, expected_active_release_id=base_release_id,
            actor_ref="automatic-chapter-types", reason="本章题型检查通过，自动发布。", external_connection=conn)
        resolver = CurrentKnowledgeResolver(candidate, catalog)
        versions = conn.execute("SELECT v.question_id,v.evidence_version_id FROM question_solution_evidence_versions v "
            "JOIN questions q ON q.id=v.question_id WHERE q.is_deleted=0 AND v.status IN ('approved','proposed')").fetchall()
        for row in versions:
            carry_forward_effective_links(conn, db_path=db_path, question_id=int(row[0]),
                evidence_version_id=str(row[1]), graph_release_id=candidate.release_id, skip_points=())
        for qid, label in labels.items():
            sync_question_type_labels(conn, qid, label["primary_type"], label["secondary_types"], resolver,
                expected_evidence_version_id=label["evidence_version_id"], source="taxonomy", data_root=data_root)
        for qid in {int(row[0]) for row in versions}:
            refresh_question_scope_summary(conn, qid, db_path=db_path, data_root=data_root)
            refresh_derived_ownership_tags(conn, qid, data_root=data_root)
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ChapterTypeInvalid("发布引用校验失败，事务已回退")
CHAPTER_TYPE_PROMPT = (
    "按本章整题核心任务整理题型，不以知识点组合定义题型。只使用给定题目资料。"
    "保留 existing_types 的 stable_key、名称、定义与边界，不拆分、合并或改名。"
    "可新增类型，新增 type_id 使用 new_ 加小写英文数字下划线；section_id 只能照抄本章小节。"
    "每个新增类型至少有 3 道主题型题目，anchors 选 1 到 2 个归入该类型的 question_ref。"
    "每道给定题必须恰好有一个 primary_type_id，secondary_type_ids 最多 2 个且不同于主题型。"
    "知识点是逐点事实，point_knowledge 按 role 区分直接知识点与前置知识。"
    "新增类型提供简体中文 name、definition、include_scope、exclude_scope。拿不准的边界不能猜造。"
    "只返回 types 与 assignments 两个数组，不返回学生信息或额外说明。"
)


def chapter_type_response_format():
    text = {"type": "string"}
    def object_schema(properties):
        return {"type": "object", "additionalProperties": False, "properties": properties,
                "required": list(properties)}
    type_schema = object_schema({key: text for key in
        ("type_id", "name", "definition", "include_scope", "exclude_scope", "section_id")})
    type_schema["properties"]["anchors"] = {"type": "array", "items": text, "minItems": 1, "maxItems": 2}
    type_schema["required"].append("anchors")
    assignment = object_schema({"question_ref": text, "primary_type_id": text,
        "secondary_type_ids": {"type": "array", "items": text, "maxItems": 2}})
    return {"type": "json_schema", "name": "chapter_question_types_v1", "strict": True,
        "schema": object_schema({"types": {"type": "array", "items": type_schema},
                                  "assignments": {"type": "array", "items": assignment}})}