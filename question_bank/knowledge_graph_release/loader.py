from __future__ import annotations

from pathlib import Path
import json
from typing import Any

from question_bank.knowledge_graph_release.contracts import KnowledgeGraphRelease


DEFAULT_RELEASE_PATH = (
    Path(__file__).resolve().parents[1]
    / "taxonomy"
    / "catalogs"
    / "knowledge_graph_release_v3.json"
)
DEFAULT_TAXONOMY_PATH = (
    Path(__file__).resolve().parents[1]
    / "taxonomy"
    / "catalogs"
    / "tag_vocabulary_v4.json"
)
_RELEASE_PATHS_BY_TAXONOMY_REVISION = {
    3: (
        Path(__file__).resolve().parents[1]
        / "taxonomy"
        / "catalogs"
        / "knowledge_graph_release_v1.json"
    ),
    4: (
        Path(__file__).resolve().parents[1]
        / "taxonomy"
        / "catalogs"
        / "knowledge_graph_release_v2.json"
    ),
    5: DEFAULT_RELEASE_PATH,
    6: DEFAULT_RELEASE_PATH.with_name('knowledge_graph_release_v4.json'),
    7: DEFAULT_RELEASE_PATH.with_name('knowledge_graph_release_v5.json'),
    8: DEFAULT_RELEASE_PATH.with_name('knowledge_graph_release_v6.json'),
    9: DEFAULT_RELEASE_PATH.with_name('knowledge_graph_release_v7.json'),
}
_TAXONOMY_PATHS_BY_REVISION = {
    3: (
        Path(__file__).resolve().parents[1]
        / "taxonomy"
        / "catalogs"
        / "tag_vocabulary_v2.json"
    ),
    4: (
        Path(__file__).resolve().parents[1]
        / "taxonomy"
        / "catalogs"
        / "tag_vocabulary_v3.json"
    ),
    5: DEFAULT_TAXONOMY_PATH,
    6: DEFAULT_TAXONOMY_PATH.with_name('tag_vocabulary_v5.json'),
    7: DEFAULT_TAXONOMY_PATH.with_name('tag_vocabulary_v6.json'),
    8: DEFAULT_TAXONOMY_PATH.with_name('tag_vocabulary_v7.json'),
    9: DEFAULT_TAXONOMY_PATH.with_name('tag_vocabulary_v8.json'),
}


def load_release(path: Path | None = None) -> KnowledgeGraphRelease:
    return KnowledgeGraphRelease.from_path(path or DEFAULT_RELEASE_PATH)


def load_release_for_taxonomy_revision(revision: int) -> KnowledgeGraphRelease:
    source = _RELEASE_PATHS_BY_TAXONOMY_REVISION.get(int(revision))
    if source is None:
        raise ValueError(f"no knowledge release is bundled for revision {revision}")
    return load_release(source)


def load_taxonomy_catalog(path: Path | None = None) -> dict[str, Any]:
    source = path or DEFAULT_TAXONOMY_PATH
    return json.loads(Path(source).read_text(encoding="utf-8"))


def load_taxonomy_catalog_for_release(
    release: KnowledgeGraphRelease,
) -> dict[str, Any]:
    """Load the immutable governed vocabulary paired with one release."""

    source = _TAXONOMY_PATHS_BY_REVISION.get(release.taxonomy_revision)
    if source is None:
        raise ValueError(
            f"no governed taxonomy is bundled for revision {release.taxonomy_revision}"
        )
    return load_taxonomy_catalog(source)


__all__ = [
    "DEFAULT_RELEASE_PATH",
    "DEFAULT_TAXONOMY_PATH",
    "load_release",
    "load_release_for_taxonomy_revision",
    "load_taxonomy_catalog",
    "load_taxonomy_catalog_for_release",
]
