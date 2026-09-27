"""Build taxonomy catalog v8 (revision 9) and knowledge-graph release v7.

This revision only changes the governed vocabulary: nine model-side terms are
retired (four thought, one ability, four model dimensions) and one approved
special_type term is added (综合与实践). No knowledge node changes, so the
release payload carries v6 forward unchanged apart from the revision/identity
fields — existing per-point links resolve to the same stable keys.

Usage:
    python tools/build_release_v7.py [--write]
"""
from __future__ import annotations

import argparse
import hashlib
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
from tools.build_release_v3 import _load_json, _write_json

CATALOG_DIR = ROOT / "question_bank" / "taxonomy" / "catalogs"
RELEASE_V6 = CATALOG_DIR / "knowledge_graph_release_v6.json"
VOCAB_V7 = CATALOG_DIR / "tag_vocabulary_v7.json"
RELEASE_V7 = CATALOG_DIR / "knowledge_graph_release_v7.json"
VOCAB_V8 = CATALOG_DIR / "tag_vocabulary_v8.json"

RELEASE_ID = "kgr_bnu_math_curriculum_2026_09_v7"
TAXONOMY_REVISION = 9
REVISION_SOURCE_ID = "tag_scheme_revision_2026_09"
ORIGIN = "teacher_governed_2026_09"

RETIRE_TERMS: dict[str, tuple[str, ...]] = {
    "thought": ("推理与证明思想", "数学建模思想", "统计推断思想", "程序化思想"),
    "ability": ("阅读理解",),
    "model": ("函数模型", "方程模型", "不等式模型", "统计推断模型"),
}

NEW_SPECIAL_TYPE = {
    "dimension": "special_type",
    "name": "综合与实践",
    "aliases": [],
    "status": "approved",
    "origin": ORIGIN,
    "source_paths": [],
    "retrieval_hints": [
        "综合与实践",
        "以真实情境为背景、按“问题背景—研究条件—模型构建—模型应用—总结反思”等环节组织，需要学生自行建立并应用数学模型的探究题，典型为深圳中考第19题",
    ],
}


def _stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(
        "\0".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()[:16]
    return f"{prefix}-{digest}"


def build_vocabulary_v8(vocab: Mapping[str, Any]) -> dict[str, Any]:
    """Return revision-9 vocabulary: retire terms, add 综合与实践."""

    terms = [dict(term) for term in vocab["terms"]]
    wanted = {
        (dimension, name)
        for dimension, names in RETIRE_TERMS.items()
        for name in names
    }
    seen: set[tuple[str, str]] = set()
    for term in terms:
        key = (str(term.get("dimension") or ""), str(term.get("name") or ""))
        if key in wanted:
            if term.get("status") == "retired":
                raise ValueError(f"term already retired: {key}")
            term["status"] = "retired"
            seen.add(key)
    missing = wanted - seen
    if missing:
        raise ValueError(f"retire targets not found in v7 catalog: {sorted(missing)}")

    taken = {
        (str(term.get("dimension") or ""), str(term.get("name") or "").strip())
        for term in terms
    }
    new_key = (NEW_SPECIAL_TYPE["dimension"], NEW_SPECIAL_TYPE["name"])
    if new_key in taken:
        raise ValueError(f"term name already exists: {NEW_SPECIAL_TYPE['name']}")
    terms.append(
        {
            "id": _stable_id("special_type", NEW_SPECIAL_TYPE["name"]),
            **NEW_SPECIAL_TYPE,
        }
    )

    payload = dict(vocab)
    payload["revision"] = TAXONOMY_REVISION
    payload["terms"] = terms
    return payload


def build_release_v7(release: Mapping[str, Any]) -> dict[str, Any]:
    """Return release v7 = v6 content, new identity, revision 9."""

    payload = dict(release)
    payload["release_id"] = RELEASE_ID
    payload["taxonomy_revision"] = TAXONOMY_REVISION
    payload["predecessor_release_id"] = release["release_id"]

    relations = []
    for relation in payload.get("relations", []):
        item = dict(relation)
        item["relation_key"] = stable_record_hash(
            "release-relation",
            RELEASE_ID,
            item["source_key"],
            item["target_key"],
            item["relation_type"],
        )
        relations.append(item)
    payload["relations"] = relations

    sources = [dict(source) for source in payload.get("sources", [])]
    sources.append(
        {
            "source_id": REVISION_SOURCE_ID,
            "kind": "vocabulary_revision",
            "title": "新打标方案词表修订（停用九个宽泛词，新增综合与实践）",
            "reference": "docs/requests/2026-09-25-g8-upper-retagging-plan.md",
        }
    )
    payload["sources"] = sources
    payload.pop("content_hash", None)
    payload["content_hash"] = compute_content_hash(payload)
    return payload


def build() -> tuple[dict[str, Any], dict[str, Any]]:
    release_v6 = _load_json(RELEASE_V6)
    vocab_v7 = _load_json(VOCAB_V7)
    return build_release_v7(release_v6), build_vocabulary_v8(vocab_v7)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    release_payload, vocabulary = build()
    report = validate_release(
        KnowledgeGraphRelease.from_mapping(release_payload), vocabulary
    )
    retired = [
        f"{term['dimension']}/{term['name']}"
        for term in vocabulary["terms"]
        if term["status"] == "retired"
    ]
    print(
        f"release {release_payload['release_id']}: nodes={len(release_payload['core_nodes'])} "
        f"relations={len(release_payload['relations'])} "
        f"retired_terms={len(retired)} "
        f"errors={len(report.errors)} warnings={len(report.warnings)}"
    )
    for name in retired:
        print(f"  retired {name}")
    for issue in report.errors[:20]:
        print(f"  ERROR {issue.path}: {issue.message}")
    for issue in report.warnings[:20]:
        print(f"  WARN  {issue.path}: {issue.message}")
    if not report.valid:
        return 1
    if args.write:
        _write_json(RELEASE_V7, release_payload)
        _write_json(VOCAB_V8, vocabulary)
        print(f"wrote {RELEASE_V7.name}, {VOCAB_V8.name}")
    else:
        print("dry-run; pass --write to emit catalogs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
