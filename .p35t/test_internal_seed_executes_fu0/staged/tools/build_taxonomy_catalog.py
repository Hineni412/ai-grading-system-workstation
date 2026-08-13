"""Build the immutable P3.5 junior-math controlled vocabulary.

The source snapshot is retained verbatim. This builder only promotes terms
whose dimension can be determined conservatively; everything else stays in
``reference_candidates`` and is never sent to the model as an approved term.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from question_bank.taxonomy.registry import CANONICAL_KNOWLEDGE


ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = ROOT / "question_bank" / "taxonomy" / "catalogs"
SOURCE_PATH = CATALOG_DIR / "xkw_junior_math_taxonomy_2026-07-26.source.json"
CURRICULUM_PATH = CATALOG_DIR / "bnu_math_2024.json"
OUTPUT_PATH = CATALOG_DIR / "tag_vocabulary_v2.json"

ABILITY_TERMS = (
    "运算能力",
    "几何直观",
    "推理能力",
    "抽象能力",
    "模型观念",
    "空间观念",
    "数据观念",
    "应用意识",
    "创新意识",
    "阅读理解",
)
METHOD_TERMS = (
    "方程思想",
    "数形结合",
    "分类讨论",
    "转化与化归",
    "整体思想",
    "待定系数法",
    "配方法",
    "换元法",
    "构造辅助线",
    "构造全等",
    "构造相似",
    "角度转化",
    "面积法",
    "反证法",
    "函数思想",
    "模型思想",
)
MODEL_TERMS = (
    "平行线角度模型",
    "角平分线模型",
    "中点模型",
    "倍长中线模型",
    "中位线模型",
    "一线三等角模型",
    "一线三垂直模型",
    "手拉手模型",
    "半角模型",
    "倍角模型",
    "旋转模型",
    "折叠模型",
    "将军饮马模型",
    "费马点模型",
    "隐圆模型",
    "胡不归模型",
    "阿氏圆模型",
    "瓜豆模型",
    "弦图模型",
    "相似三角形模型",
    "面积等积模型",
)

CORE_KNOWLEDGE_ROOTS = {
    "数与式",
    "方程与不等式",
    "函数",
    "图形的性质",
    "图形的变化",
    "统计与概率",
    "观察、猜想与证明",
}
EXCLUDED_ROOTS = {"向量的运算", "五四制小学衔接", "数学竞赛"}
KNOWLEDGE_EXCLUDED_TOKENS = (
    "模型",
    "技巧",
    "压轴",
    "竞赛",
    "五四制",
    "小升初",
    "培优",
    "专题",
    "真题",
    "易错",
    "常考",
    "辅助线",
    "构造",
    "作图",
    "猜想规律",
)
METHOD_HINTS = (
    "法",
    "思想",
    "数形结合",
    "分类讨论",
    "转化",
    "化归",
    "整体",
    "换元",
    "特殊值",
    "作平行线",
    "作垂线",
    "倍长中线",
    "截长补短",
    "辅助圆",
    "面积",
)


def _normalized(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s\W_]+", "", text)


def _stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(
        "\0".join(str(part or "") for part in parts).encode("utf-8")
    ).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def _term(
    term_id: str,
    dimension: str,
    name: str,
    *,
    aliases: Iterable[str] = (),
    origin: str,
    source_paths: Iterable[Iterable[str]] = (),
) -> dict[str, Any]:
    return {
        "id": term_id,
        "dimension": dimension,
        "name": name,
        "aliases": list(aliases),
        "status": "approved",
        "origin": origin,
        "source_paths": [list(path) for path in source_paths],
    }


def _dedupe_aliases(terms: list[dict[str, Any]]) -> None:
    owners: dict[tuple[str, str], str] = {}
    for term in terms:
        dimension = term["dimension"]
        term_id = term["id"]
        for value in (term_id, term["name"]):
            key = (dimension, _normalized(value))
            owner = owners.get(key)
            if owner is not None and owner != term_id:
                raise ValueError(f"Duplicate approved term identity: {value}")
            owners[key] = term_id
        kept: list[str] = []
        for alias in term["aliases"]:
            key = (dimension, _normalized(alias))
            owner = owners.get(key)
            if not key[1] or owner not in (None, term_id):
                continue
            owners[key] = term_id
            if _normalized(alias) != _normalized(term["name"]):
                kept.append(alias)
        term["aliases"] = kept


def _curriculum_terms(payload: dict[str, Any]) -> list[dict[str, Any]]:
    terms: list[dict[str, Any]] = []
    for volume in payload.get("volumes", []):
        volume_label = str(volume.get("label") or "").strip()
        for chapter in volume.get("chapters", []):
            if str(chapter.get("kind") or "").strip() != "chapter":
                continue
            chapter_id = str(chapter.get("id") or "").strip()
            chapter_label = str(chapter.get("label") or "").strip()
            if (
                not volume_label
                or not chapter_id
                or not re.match(r"^第[一二三四五六七八九十百0-9]+章(?:\s|$)", chapter_label)
            ):
                continue
            name = f"{volume_label} {chapter_label}"
            terms.append(
                _term(
                    chapter_id,
                    "curriculum",
                    name,
                    aliases=(name.replace(" ", ""),),
                    origin="bnu_2024_catalog",
                    source_paths=((volume_label, chapter_label),),
                )
            )
    return terms


def _seed_terms() -> list[dict[str, Any]]:
    terms = [
        _term(
            item.canonical_id.casefold(),
            "knowledge",
            item.canonical_name,
            aliases=item.aliases,
            origin="p3_registry",
        )
        for item in CANONICAL_KNOWLEDGE
    ]
    for dimension, names in (
        ("ability", ABILITY_TERMS),
        ("method", METHOD_TERMS),
        ("model", MODEL_TERMS),
    ):
        terms.extend(
            _term(
                _stable_id(dimension, name),
                dimension,
                name,
                origin="p3_controlled_options",
            )
            for name in names
        )
    return terms


def _source_terms(
    source: dict[str, Any],
    terms: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    approved_names = {
        (term["dimension"], _normalized(value))
        for term in terms
        for value in (term["id"], term["name"], *term["aliases"])
    }
    promoted: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    def add_candidate(node: dict[str, Any], reason: str) -> None:
        path = [str(item).strip() for item in node.get("path", []) if str(item).strip()]
        name = str(node.get("name") or "").strip()
        if name and path:
            candidates.append(
                {"name": name, "source_path": path, "reason": reason}
            )

    for node in source.get("knowledge", {}).get("nodes", []):
        path = [str(item).strip() for item in node.get("path", []) if str(item).strip()]
        name = str(node.get("name") or "").strip()
        root = path[0] if path else ""
        if root in EXCLUDED_ROOTS:
            add_candidate(node, "超出当前七至九年级常规教学范围")
            continue
        if root not in CORE_KNOWLEDGE_ROOTS or not bool(node.get("leaf")):
            add_candidate(node, "来源层级节点，仅保留作分类参考")
            continue
        if (
            not name
            or len(name) > 36
            or any(token in name for token in KNOWLEDGE_EXCLUDED_TOKENS)
            or name.endswith("法")
        ):
            add_candidate(node, "维度或粒度仍需教师确认")
            continue
        key = ("knowledge", _normalized(name))
        if key in approved_names:
            continue
        term = _term(
            _stable_id("xkw-knowledge", *path),
            "knowledge",
            name,
            origin="xkw_2026_snapshot",
            source_paths=(path,),
        )
        promoted.append(term)
        approved_names.add(key)

    for node in source.get("methods", {}).get("nodes", []):
        path = [str(item).strip() for item in node.get("path", []) if str(item).strip()]
        name = str(node.get("name") or "").strip()
        if not path or not name or not bool(node.get("leaf")):
            add_candidate(node, "来源层级节点，仅保留作分类参考")
            continue
        if path[0] == "压轴题" or "压轴" in name or len(name) > 36:
            add_candidate(node, "题型范围或粒度仍需教师确认")
            continue
        dimension = (
            "method"
            if path[0] == "数学方法" or any(token in name for token in METHOD_HINTS)
            else "model"
        )
        key = (dimension, _normalized(name))
        if key in approved_names:
            continue
        term = _term(
            _stable_id(f"xkw-{dimension}", *path),
            dimension,
            name,
            origin="xkw_2026_snapshot",
            source_paths=(path,),
        )
        promoted.append(term)
        approved_names.add(key)

    unique_candidates: list[dict[str, Any]] = []
    seen_candidates: set[tuple[str, tuple[str, ...], str]] = set()
    for candidate in candidates:
        key = (
            _normalized(candidate["name"]),
            tuple(candidate["source_path"]),
            candidate["reason"],
        )
        if key not in seen_candidates:
            seen_candidates.add(key)
            unique_candidates.append(candidate)
    return promoted, unique_candidates


def build() -> dict[str, Any]:
    source = _read_json(SOURCE_PATH)
    curriculum = _read_json(CURRICULUM_PATH)
    terms = [*_curriculum_terms(curriculum), *_seed_terms()]
    promoted, reference_candidates = _source_terms(source, terms)
    terms.extend(promoted)
    _dedupe_aliases(terms)
    terms.sort(key=lambda item: (item["dimension"], item["name"], item["id"]))
    return {
        "schema_version": 2,
        "catalog_id": "junior-math-controlled-vocabulary-v2",
        "revision": 1,
        "source_snapshot": {
            "file": SOURCE_PATH.name,
            "observed_at": source.get("source", {}).get("observed_at"),
            "knowledge_node_count": len(source.get("knowledge", {}).get("nodes", [])),
            "method_node_count": len(source.get("methods", {}).get("nodes", [])),
            "runtime_refresh": False,
        },
        "classification_policy": {
            "dimensions": ["curriculum", "knowledge", "ability", "method", "model"],
            "excluded_branches": sorted(EXCLUDED_ROOTS),
            "legacy_free_text_dimensions": [
                "sub_skill",
                "measured_skill",
                "supporting_skill",
            ],
            "rule": (
                "只晋升维度明确的末级节点；范围外、层级节点和维度不确定项"
                "保留在 reference_candidates，不能提供给 AI。"
            ),
        },
        "terms": terms,
        "reference_candidates": reference_candidates,
    }


def main() -> None:
    payload = build()
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    counts: dict[str, int] = {}
    for term in payload["terms"]:
        counts[term["dimension"]] = counts.get(term["dimension"], 0) + 1
    print(
        json.dumps(
            {
                "output": OUTPUT_PATH.name,
                "counts": counts,
                "reference_candidates": len(payload["reference_candidates"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
