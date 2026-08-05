from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = ROOT / "question_bank" / "taxonomy" / "catalogs"
SOURCE_PATH = (
    CATALOG_DIR / "xkw_bnu_math_2024_curriculum_2026-08-05.source.json"
)
LEGACY_TAXONOMY_PATH = CATALOG_DIR / "tag_vocabulary_v2.json"
LEGACY_RELEASE_PATH = CATALOG_DIR / "knowledge_graph_release_v1.json"

CURRICULUM_OUTPUT = CATALOG_DIR / "bnu_math_2024_v2.json"
TAXONOMY_OUTPUT = CATALOG_DIR / "tag_vocabulary_v3.json"
RELEASE_OUTPUT = CATALOG_DIR / "knowledge_graph_release_v2.json"
LEGACY_MAPPING_OUTPUT = CATALOG_DIR / "knowledge_standard_v2_legacy_mapping.json"

EXPECTED_VOLUME_IDS = (
    "bnu24-math-g7-upper",
    "bnu24-math-g7-lower",
    "bnu24-math-g8-upper",
    "bnu24-math-g8-lower",
    "bnu24-math-g9-upper",
)
EXPECTED_RETAINED_COUNTS = (243, 185, 259, 229, 208)
EXPECTED_NODE_COUNT = 1124
EXPECTED_CHAPTER_COUNT = 36
EXPECTED_SECTION_COUNT = 126
EXPECTED_LEAF_COUNT = 962
EXPECTED_PARENT_RELATION_COUNT = 1088
EXPECTED_LEGACY_TERM_COUNT = 294
SOURCE_ID = "xkw_bnu_curriculum_snapshot_2026_08_05"
RELEASE_ID = "kgr_bnu_math_curriculum_2026_08_v2"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _normalized(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s\W_]+", "", text)


def _unique_text(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        normalized = _normalized(text)
        if not text or normalized in seen:
            continue
        seen.add(normalized)
        result.append(text)
    return result


def _stable_key(volume_id: str, node_id: str) -> str:
    volume_token = volume_id.removeprefix("bnu24-math-").replace("-", "_")
    node_token = re.sub(r"[^0-9a-z]+", "_", node_id.casefold()).strip("_")
    key = f"kp_bnu24_math_{volume_token}_{node_token}"
    if not re.fullmatch(r"kp_[a-z0-9]+(?:_[a-z0-9]+)*", key):
        raise ValueError(f"invalid stable key: {key}")
    return key


def _curriculum_chapter_id(volume_id: str, order: int) -> str:
    return f"{volume_id}-c{order:02d}"


def _curriculum_section_id(volume_id: str, chapter_order: int, order: int) -> str:
    return f"{volume_id}-c{chapter_order:02d}-s{order:02d}"


def _display_name(volume_label: str, path: list[str]) -> str:
    return "｜".join((volume_label, *path))


def _source_ref(raw: dict[str, Any]) -> dict[str, str]:
    value = raw.get("source_ref")
    if not isinstance(value, dict):
        raise ValueError("source node is missing source_ref")
    node_id = str(value.get("node_id") or "").strip()
    relative_url = str(value.get("relative_url") or "").strip()
    if not node_id or not relative_url:
        raise ValueError("source node has an invalid source_ref")
    return {"node_id": node_id, "relative_url": relative_url}


def _node_kind(label: str, *, chapter: bool = False) -> str:
    if "综合与实践" in label:
        return "activity" if chapter else "activity_group"
    if "问题解决策略" in label:
        return "activity"
    return "chapter" if chapter else "lesson"


def _term(
    *,
    term_id: str,
    dimension: str,
    name: str,
    aliases: Iterable[object] = (),
    origin: str,
    source_paths: list[list[str]],
    retrieval_hints: Iterable[object] = (),
    legacy_names: Iterable[object] = (),
    legacy_ids: Iterable[object] = (),
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": term_id,
        "dimension": dimension,
        "name": name,
        "aliases": _unique_text(aliases),
        "status": "approved",
        "origin": origin,
        "source_paths": source_paths,
    }
    hints = _unique_text(retrieval_hints)
    if hints:
        result["retrieval_hints"] = hints
    old_names = _unique_text(legacy_names)
    if old_names:
        result["legacy_names"] = old_names
    old_ids = _unique_text(legacy_ids)
    if old_ids:
        result["legacy_ids"] = old_ids
    return result


def _stable_record_hash(namespace: str, *parts: object) -> str:
    encoded = json.dumps(
        [str(namespace or "").strip(), *(str(part or "").strip() for part in parts)],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _content_hash(payload: dict[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("content_hash", None)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _build_curriculum_and_nodes(
    source: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw_volumes = source.get("volumes")
    if not isinstance(raw_volumes, list) or len(raw_volumes) != 5:
        raise ValueError("source snapshot must contain five volumes")

    built_volumes: list[dict[str, Any]] = []
    flat_nodes: list[dict[str, Any]] = []
    observed_counts: list[int] = []
    for volume_order, raw_volume in enumerate(raw_volumes, start=1):
        if not isinstance(raw_volume, dict):
            raise ValueError("source volume must be an object")
        volume_id = str(raw_volume.get("id") or "").strip()
        expected_id = EXPECTED_VOLUME_IDS[volume_order - 1]
        if volume_id != expected_id:
            raise ValueError(f"unexpected volume order: {volume_id}")
        volume_label = str(raw_volume.get("label") or "").strip()
        raw_chapters = raw_volume.get("nodes")
        if not isinstance(raw_chapters, list) or not raw_chapters:
            raise ValueError(f"{volume_id} has no chapters")

        chapters: list[dict[str, Any]] = []
        volume_node_count = 0
        for chapter_order, raw_chapter in enumerate(raw_chapters, start=1):
            if not isinstance(raw_chapter, dict):
                raise ValueError("chapter must be an object")
            chapter_label = str(raw_chapter.get("label") or "").strip()
            chapter_path = [str(item).strip() for item in raw_chapter.get("path", [])]
            chapter_key = _stable_key(volume_id, str(raw_chapter["node_id"]))
            chapter_id = _curriculum_chapter_id(volume_id, chapter_order)
            chapter_display = _display_name(volume_label, chapter_path)
            flat_nodes.append(
                {
                    "id": chapter_key,
                    "volume_id": volume_id,
                    "volume_order": volume_order,
                    "level": 1,
                    "order": chapter_order,
                    "label": chapter_label,
                    "display_name": chapter_display,
                    "path": [volume_label, *chapter_path],
                    "parent_id": None,
                    "source_ref": _source_ref(raw_chapter),
                }
            )
            volume_node_count += 1

            raw_sections = raw_chapter.get("children")
            if not isinstance(raw_sections, list):
                raise ValueError("chapter children must be a list")
            sections: list[dict[str, Any]] = []
            for section_order, raw_section in enumerate(raw_sections, start=1):
                if not isinstance(raw_section, dict):
                    raise ValueError("section must be an object")
                section_label = str(raw_section.get("label") or "").strip()
                section_path = [str(item).strip() for item in raw_section.get("path", [])]
                section_key = _stable_key(volume_id, str(raw_section["node_id"]))
                section_id = _curriculum_section_id(
                    volume_id, chapter_order, section_order
                )
                section_display = _display_name(volume_label, section_path)
                flat_nodes.append(
                    {
                        "id": section_key,
                        "volume_id": volume_id,
                        "volume_order": volume_order,
                        "level": 2,
                        "order": section_order,
                        "label": section_label,
                        "display_name": section_display,
                        "path": [volume_label, *section_path],
                        "parent_id": chapter_key,
                        "source_ref": _source_ref(raw_section),
                    }
                )
                volume_node_count += 1

                raw_points = raw_section.get("children")
                if not isinstance(raw_points, list):
                    raise ValueError("section children must be a list")
                points: list[dict[str, Any]] = []
                for point_order, raw_point in enumerate(raw_points, start=1):
                    if not isinstance(raw_point, dict):
                        raise ValueError("knowledge point must be an object")
                    point_label = str(raw_point.get("label") or "").strip()
                    point_path = [str(item).strip() for item in raw_point.get("path", [])]
                    point_key = _stable_key(volume_id, str(raw_point["node_id"]))
                    point_display = _display_name(volume_label, point_path)
                    point_ref = _source_ref(raw_point)
                    flat_nodes.append(
                        {
                            "id": point_key,
                            "volume_id": volume_id,
                            "volume_order": volume_order,
                            "level": 3,
                            "order": point_order,
                            "label": point_label,
                            "display_name": point_display,
                            "path": [volume_label, *point_path],
                            "parent_id": section_key,
                            "source_ref": point_ref,
                        }
                    )
                    volume_node_count += 1
                    points.append(
                        {
                            "id": point_key,
                            "order": point_order,
                            "label": point_label,
                            "display_name": point_display,
                            "parent_knowledge_id": section_key,
                            "source_ref": point_ref,
                        }
                    )

                sections.append(
                    {
                        "id": section_id,
                        "knowledge_id": section_key,
                        "order": section_order,
                        "number": None,
                        "title": section_label,
                        "label": section_label,
                        "kind": _node_kind(section_label),
                        "display_name": section_display,
                        "source_ref": _source_ref(raw_section),
                        "knowledge_points": points,
                    }
                )

            chapters.append(
                {
                    "id": chapter_id,
                    "knowledge_id": chapter_key,
                    "order": chapter_order,
                    "number": None,
                    "title": chapter_label,
                    "label": chapter_label,
                    "kind": _node_kind(chapter_label, chapter=True),
                    "display_name": chapter_display,
                    "source_ref": _source_ref(raw_chapter),
                    "sections": sections,
                }
            )

        observed_counts.append(volume_node_count)
        source_statistics = raw_volume.get("statistics")
        if not isinstance(source_statistics, dict):
            raise ValueError("volume statistics are missing")
        retained_nodes = int(source_statistics.get("retained_nodes") or 0)
        if retained_nodes != volume_node_count:
            raise ValueError(f"{volume_id} retained count does not match its tree")
        built_volumes.append(
            {
                "id": volume_id,
                "order": volume_order,
                "label": volume_label,
                "grade": str(raw_volume.get("grade") or "").strip(),
                "semester": str(raw_volume.get("semester") or "").strip(),
                "textbook_version": str(
                    raw_volume.get("textbook_version") or ""
                ).strip(),
                "source": dict(raw_volume.get("source") or {}),
                "statistics": dict(source_statistics),
                "chapters": chapters,
            }
        )

    if tuple(observed_counts) != EXPECTED_RETAINED_COUNTS:
        raise ValueError(f"unexpected retained volume counts: {observed_counts}")
    level_counts = Counter(int(node["level"]) for node in flat_nodes)
    if len(flat_nodes) != EXPECTED_NODE_COUNT or level_counts != Counter(
        {1: EXPECTED_CHAPTER_COUNT, 2: EXPECTED_SECTION_COUNT, 3: EXPECTED_LEAF_COUNT}
    ):
        raise ValueError(f"unexpected node counts: {level_counts}")
    if len({node["id"] for node in flat_nodes}) != len(flat_nodes):
        raise ValueError("generated knowledge IDs are not unique")
    if len({_normalized(node["display_name"]) for node in flat_nodes}) != len(flat_nodes):
        raise ValueError("generated full display paths are not unique")
    if max(len(str(node["display_name"])) for node in flat_nodes) > 160:
        raise ValueError("a generated display path exceeds 160 characters")

    source_stats = source.get("statistics")
    if not isinstance(source_stats, dict):
        raise ValueError("source snapshot statistics are missing")
    curriculum = {
        "schema_version": 2,
        "catalog_id": "bnu-math-2024-curriculum-v2",
        "knowledge_standard_id": "bnu-math-2024-curriculum-knowledge-v2",
        "publisher": "北京师范大学出版社",
        "subject": "初中数学",
        "edition": "2024",
        "capture": {
            "captured_at": "2026-08-05",
            "mode": "one_time_public_catalog_capture",
            "scope": "北师大版七上至九上五册章、小节和子知识点",
            "runtime_refresh": False,
            "source_provider": "组卷网公开章节选题目录",
            "source_snapshot": SOURCE_PATH.name,
            "retention_rule": "只排除精确标题回顾与思考、复习题",
        },
        "statistics": {
            "raw_nodes": int(source_stats.get("raw_nodes") or 0),
            "excluded_nodes": int(source_stats.get("excluded_nodes") or 0),
            "retained_nodes": len(flat_nodes),
            "chapters": level_counts[1],
            "sections": level_counts[2],
            "knowledge_points": level_counts[3],
        },
        "volumes": built_volumes,
    }
    return curriculum, flat_nodes


def _legacy_mapping(
    legacy_terms: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    node_by_id = {str(node["id"]): node for node in nodes}
    label_index: dict[str, list[str]] = defaultdict(list)
    display_index: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        label_index[_normalized(node["label"])].append(str(node["id"]))
        display_index[_normalized(node["display_name"])].append(str(node["id"]))

    rows: list[dict[str, Any]] = []
    compatibility: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for term in legacy_terms:
        term_id = str(term["id"])
        name = str(term["name"])
        scored: dict[str, int] = {}

        def add_matches(values: Iterable[object], score: int) -> None:
            for value in values:
                key = _normalized(value)
                if not key:
                    continue
                for target in (*label_index.get(key, ()), *display_index.get(key, ())):
                    scored[target] = max(score, scored.get(target, 0))

        add_matches((name,), 100)
        add_matches(term.get("aliases", []), 90)
        add_matches(term.get("legacy_names", []), 80)
        source_tails = [
            path[-1]
            for path in term.get("source_paths", [])
            if isinstance(path, list) and path
        ]
        add_matches(source_tails, 70)

        if scored:
            best = max(scored.values())
            target_ids = sorted(
                (target for target, score in scored.items() if score == best),
                key=lambda target: tuple(node_by_id[target]["path"]),
            )
        else:
            target_ids = []
        if len(target_ids) == 1:
            disposition = "exact" if scored[target_ids[0]] == 100 else "unique_alias"
            reason = "旧词通过精确且唯一的名称匹配到一个教材路径节点。"
        elif target_ids:
            disposition = "split"
            reason = "旧词精确命中多个教材路径，必须保留歧义，不能自动迁移。"
        else:
            disposition = "retired"
            reason = "未找到精确且唯一的教材路径对应项，不进行猜测迁移。"
        rows.append(
            {
                "legacy_id": term_id,
                "legacy_name": name,
                "disposition": disposition,
                "target_ids": target_ids,
                "reason": reason,
            }
        )
        for target_id in target_ids:
            compatibility[target_id].append(term)

    if len(rows) != EXPECTED_LEGACY_TERM_COUNT:
        raise ValueError(f"expected 294 legacy terms, found {len(rows)}")
    return rows, compatibility


def _build_taxonomy(
    legacy_catalog: dict[str, Any],
    curriculum: dict[str, Any],
    nodes: list[dict[str, Any]],
    compatibility: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    short_name_counts = Counter(_normalized(node["label"]) for node in nodes)
    knowledge_terms: list[dict[str, Any]] = []
    for node in nodes:
        old_terms = compatibility.get(str(node["id"]), [])
        legacy_names = []
        legacy_ids = []
        for old in old_terms:
            legacy_names.extend(
                [old.get("name"), *old.get("aliases", []), *old.get("legacy_names", [])]
            )
            legacy_ids.extend([old.get("id"), *old.get("legacy_ids", [])])
        aliases = (
            [node["label"]]
            if short_name_counts[_normalized(node["label"])] == 1
            else []
        )
        knowledge_terms.append(
            _term(
                term_id=str(node["id"]),
                dimension="knowledge",
                name=str(node["display_name"]),
                aliases=aliases,
                origin="xkw_bnu_curriculum_2026_08_05",
                source_paths=[list(node["path"])],
                retrieval_hints=[node["label"], *node["path"][1:]],
                legacy_names=legacy_names,
                legacy_ids=legacy_ids,
            )
        )

    curriculum_terms: list[dict[str, Any]] = []
    for volume in curriculum["volumes"]:
        for chapter in volume["chapters"]:
            curriculum_terms.append(
                _term(
                    term_id=str(chapter["id"]),
                    dimension="curriculum",
                    name=f"{volume['label']} {chapter['label']}",
                    aliases=(),
                    origin="xkw_bnu_curriculum_2026_08_05",
                    source_paths=[[volume["label"], chapter["label"]]],
                    retrieval_hints=[chapter["label"]],
                )
            )

    retained_dimensions = [
        dict(term)
        for term in legacy_catalog.get("terms", [])
        if isinstance(term, dict)
        and term.get("dimension") not in {"knowledge", "curriculum"}
    ]
    policy = dict(legacy_catalog.get("classification_policy") or {})
    policy["rule"] = (
        "知识维度采用北师大版七上至九上五册教材路径节点；章、小节和子知识点均可独立选择，"
        "优先最深可靠节点并允许最近父级回退。方法、思想、模型、能力和特殊题型仍按原维度治理。"
    )
    policy["knowledge_selection"] = {
        "volume_order": list(EXPECTED_VOLUME_IDS),
        "scope": "selected_volume_and_prior",
        "leaf_first_parent_fallback": True,
        "free_text_fallback_maximum": 1,
    }
    return {
        "schema_version": 2,
        "catalog_id": "junior-math-controlled-vocabulary-v2",
        "revision": 4,
        "expected_approved_knowledge_count": EXPECTED_NODE_COUNT,
        "source_snapshot": {
            "file": SOURCE_PATH.name,
            "observed_at": "2026-08-05",
            "knowledge_node_count": EXPECTED_NODE_COUNT,
            "curriculum_chapter_count": EXPECTED_CHAPTER_COUNT,
            "runtime_refresh": False,
        },
        "classification_policy": policy,
        "terms": [*curriculum_terms, *knowledge_terms, *retained_dimensions],
        "reference_candidates": [],
    }


def _build_release(
    legacy_release: dict[str, Any],
    nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    node_by_id = {str(node["id"]): node for node in nodes}
    core_nodes: list[dict[str, Any]] = []
    dispositions: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    for node in nodes:
        node_id = str(node["id"])
        display_name = str(node["display_name"])
        anchor = "/".join(str(item) for item in node["path"])
        source_ref = node["source_ref"]
        core_nodes.append(
            {
                "stable_key": node_id,
                "display_name": display_name,
                "aliases": [str(node["label"])],
                "node_kind": "core",
                "status": "active",
                "definition": f"北师大版（2024）教材知识路径“{display_name}”对应的知识节点。",
                "include_scope": "题目或解题证据点直接考查该教材路径所表达的概念、性质、定理、公式、运算或直接应用。",
                "exclude_scope": "仅作为未被评价的辅助前置，或实际属于解题方法、数学思想、数学模型、能力和特殊题型时不计入。",
                "curriculum_anchors": [anchor],
                "observable_evidence": "学生作答中能够观察到对该节点知识的识别、表示、运算、论证或直接应用。",
                "rationale": "依据用户确认的北师大版五册教材目录结构建立稳定知识身份。",
                "evidence_source_ids": [SOURCE_ID],
            }
        )
        dispositions.append(
            {
                "fine_term_id": node_id,
                "display_name": display_name,
                "disposition": "direct_core",
                "definition": f"教材知识路径“{display_name}”的规范知识词。",
                "include_scope": "直接考查该节点时使用；能够确定更细子节点时应选择子节点。",
                "exclude_scope": "拿不准子节点时回退到本节点；不得从本节点证据猜测分摊到子节点。",
                "curriculum_anchors": [anchor],
                "rationale": "教材路径节点同时作为精细知识词和独立图谱节点。",
                "confidence": 1.0,
                "review_priority": "normal",
                "evidence_source_ids": [SOURCE_ID],
            }
        )
        mappings.append(
            {
                "fine_term_id": node_id,
                "stable_key": node_id,
                "mapping_role": "primary",
                "rationale": "教材知识路径词一对一映射到同一稳定图谱节点。",
            }
        )
        parent_id = node.get("parent_id")
        if parent_id:
            parent = node_by_id[str(parent_id)]
            relations.append(
                {
                    "relation_key": _stable_record_hash(
                        "knowledge-graph-release-relation",
                        node_id,
                        parent_id,
                        "parent",
                    ),
                    "source_key": node_id,
                    "target_key": str(parent_id),
                    "relation_type": "parent",
                    "basis_kind": "curriculum_structure",
                    "strength": "required",
                    "rationale": f"“{node['label']}”在教材目录中直接隶属于“{parent['label']}”。",
                    "evidence_source_ids": [SOURCE_ID],
                    "source_locator": str(source_ref["relative_url"]),
                }
            )
    if len(relations) != EXPECTED_PARENT_RELATION_COUNT:
        raise ValueError(f"unexpected parent relation count: {len(relations)}")

    payload = {
        "schema_version": "knowledge-graph-release-v1",
        "release_id": RELEASE_ID,
        "taxonomy_revision": 4,
        "predecessor_release_id": str(legacy_release.get("release_id") or ""),
        "sources": [
            {
                "source_id": SOURCE_ID,
                "kind": "approved_textbook_catalog_snapshot",
                "title": "组卷网北师大版（2024）七上至九上教材目录一次性来源快照",
                "reference": SOURCE_PATH.name,
            }
        ],
        "core_nodes": core_nodes,
        "fine_term_dispositions": dispositions,
        "mappings": mappings,
        "relations": relations,
        "replacements": [],
    }
    payload["content_hash"] = _content_hash(payload)
    return payload


def main() -> None:
    source = _read_json(SOURCE_PATH)
    legacy_catalog = _read_json(LEGACY_TAXONOMY_PATH)
    legacy_release = _read_json(LEGACY_RELEASE_PATH)
    legacy_terms = [
        dict(term)
        for term in legacy_catalog.get("terms", [])
        if isinstance(term, dict)
        and term.get("dimension") == "knowledge"
        and term.get("status") == "approved"
    ]
    if len(legacy_terms) != EXPECTED_LEGACY_TERM_COUNT:
        raise ValueError(f"legacy catalog must contain 294 approved knowledge terms")

    curriculum, nodes = _build_curriculum_and_nodes(source)
    migration_rows, compatibility = _legacy_mapping(legacy_terms, nodes)
    taxonomy = _build_taxonomy(
        legacy_catalog,
        curriculum,
        nodes,
        compatibility,
    )
    release = _build_release(legacy_release, nodes)
    migration = {
        "schema_version": 1,
        "mapping_id": "bnu-math-curriculum-knowledge-v2-legacy-mapping",
        "source_taxonomy_revision": 3,
        "target_taxonomy_revision": 4,
        "policy": "只采用精确且唯一的名称或来源末级名称匹配；多目标和无目标不自动迁移。",
        "statistics": dict(Counter(row["disposition"] for row in migration_rows)),
        "items": migration_rows,
    }
    migration["content_hash"] = _content_hash(migration)

    _write_json(CURRICULUM_OUTPUT, curriculum)
    _write_json(TAXONOMY_OUTPUT, taxonomy)
    _write_json(RELEASE_OUTPUT, release)
    _write_json(LEGACY_MAPPING_OUTPUT, migration)

    print(
        json.dumps(
            {
                "curriculum": CURRICULUM_OUTPUT.name,
                "taxonomy": TAXONOMY_OUTPUT.name,
                "release": RELEASE_OUTPUT.name,
                "legacy_mapping": LEGACY_MAPPING_OUTPUT.name,
                "nodes": len(nodes),
                "relations": len(release["relations"]),
                "legacy_statistics": migration["statistics"],
                "release_hash": release["content_hash"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
