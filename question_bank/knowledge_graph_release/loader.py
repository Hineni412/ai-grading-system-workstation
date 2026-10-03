from __future__ import annotations

import json
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from question_bank.knowledge_graph_release.contracts import (
    KnowledgeGraphRelease,
    _register_release,
)

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
    10: DEFAULT_RELEASE_PATH.with_name('knowledge_graph_release_v8.json'),
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
    10: DEFAULT_TAXONOMY_PATH.with_name('tag_vocabulary_v9.json'),
}


_FILE_RELEASE_CACHE_LIMIT = 8
_FILE_RELEASE_CACHE_LOCK = threading.Lock()
_FILE_RELEASE_CACHE: OrderedDict[
    tuple[str, int, int], KnowledgeGraphRelease
] = OrderedDict()


def load_release(path: Path | None = None) -> KnowledgeGraphRelease:
    """Parse a release file once per (path, size, mtime); callers must treat the
    returned object as read-only (``release.to_dict()`` gives a mutable copy)."""
    source = Path(path or DEFAULT_RELEASE_PATH)
    try:
        resolved = source.resolve()
        stat = resolved.stat()
        key = (str(resolved), int(stat.st_size), int(stat.st_mtime_ns))
    except OSError:
        key = None
    if key is not None:
        with _FILE_RELEASE_CACHE_LOCK:
            cached = _FILE_RELEASE_CACHE.get(key)
            if cached is not None:
                _FILE_RELEASE_CACHE.move_to_end(key)
                return cached
    release = KnowledgeGraphRelease.from_path(source)
    _register_release(release)
    if key is not None:
        with _FILE_RELEASE_CACHE_LOCK:
            _FILE_RELEASE_CACHE[key] = release
            _FILE_RELEASE_CACHE.move_to_end(key)
            while len(_FILE_RELEASE_CACHE) > _FILE_RELEASE_CACHE_LIMIT:
                _FILE_RELEASE_CACHE.popitem(last=False)
    return release


def _clear_file_release_cache_for_tests() -> None:
    with _FILE_RELEASE_CACHE_LOCK:
        _FILE_RELEASE_CACHE.clear()


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
