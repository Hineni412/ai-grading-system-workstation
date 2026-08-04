from __future__ import annotations

from pathlib import Path
import json
from typing import Any

from question_bank.knowledge_graph_release.contracts import KnowledgeGraphRelease


DEFAULT_RELEASE_PATH = (
    Path(__file__).resolve().parents[1]
    / "taxonomy"
    / "catalogs"
    / "knowledge_graph_release_v1.json"
)
DEFAULT_TAXONOMY_PATH = (
    Path(__file__).resolve().parents[1]
    / "taxonomy"
    / "catalogs"
    / "tag_vocabulary_v2.json"
)


def load_release(path: Path | None = None) -> KnowledgeGraphRelease:
    return KnowledgeGraphRelease.from_path(path or DEFAULT_RELEASE_PATH)


def load_taxonomy_catalog(path: Path | None = None) -> dict[str, Any]:
    source = path or DEFAULT_TAXONOMY_PATH
    return json.loads(Path(source).read_text(encoding="utf-8"))


__all__ = [
    "DEFAULT_RELEASE_PATH",
    "DEFAULT_TAXONOMY_PATH",
    "load_release",
    "load_taxonomy_catalog",
]
