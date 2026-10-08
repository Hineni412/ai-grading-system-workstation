"""Build the paired definition-only vocabulary and knowledge-standard revision."""
from __future__ import annotations
import argparse
from pathlib import Path
from question_bank.taxonomy.attribute_definitions import enrich_attribute_definitions
from question_bank.knowledge_graph_release.contracts import KnowledgeGraphRelease, compute_content_hash
from question_bank.knowledge_graph_release.loader import load_release_for_taxonomy_revision, load_taxonomy_catalog_for_release
from question_bank.knowledge_graph_release.validation import validate_release
from tools.build_release_v3 import _write_json


def build_attribute_release():
    base = load_release_for_taxonomy_revision(11)
    vocabulary = enrich_attribute_definitions(load_taxonomy_catalog_for_release(base))
    vocabulary["revision"] = 12
    payload = base.to_dict()
    payload.update(release_id="kgr_bnu_math_curriculum_2026_10_v10", taxonomy_revision=12,
                   predecessor_release_id=base.release_id)
    payload["content_hash"] = compute_content_hash(payload)
    release = KnowledgeGraphRelease.from_mapping(payload)
    validate_release(release, vocabulary).raise_for_errors()
    return payload, vocabulary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    payload, vocabulary = build_attribute_release()
    if args.write:
        root = Path(__file__).resolve().parents[1] / "question_bank/taxonomy/catalogs"
        _write_json(root / "knowledge_graph_release_v10.json", payload)
        _write_json(root / "tag_vocabulary_v11.json", vocabulary)
    print("validated definition-only taxonomy revision 12; no database writes")


if __name__ == "__main__":
    main()