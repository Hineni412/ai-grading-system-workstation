"""Current training targets, resolved per textbook chapter."""
from __future__ import annotations

import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from question_bank.taxonomy.curriculum_catalog import (
    curriculum_knowledge_node,
    curriculum_volume,
)

TYPE_KEY_PATTERN = r"^kp_[a-z0-9_]+_t\d{2}$"
_TYPE_KEY = re.compile(TYPE_KEY_PATTERN)
_TYPE_RELEASE_CACHE_LIMIT = 16
_LOOKUP_MEMO_LIMIT = 8192
_lookup_memo_lock = threading.Lock()


@dataclass
class _ReleaseTypeScope:
    nodes: dict[str, tuple[str, str]]
    typed_chapters: frozenset[str]
    typed_volumes: frozenset[str]
    chapter_keys: dict[str, dict[str, str]] = field(default_factory=dict)
    chapter_kinds: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)


_TYPE_RELEASE_CACHE: dict[tuple[str, str], _ReleaseTypeScope] = {}
# Per-release lookup answers: release identity fully determines every input
# (resolver scope and the bundled curriculum catalog are both immutable).
_kind_memo: OrderedDict[tuple, str] = OrderedDict()
_target_memo: OrderedDict[tuple, bool] = OrderedDict()


def _release_identity(resolver: Any) -> tuple | None:
    release_id = str(getattr(resolver, "release_id", "") or "")
    if not release_id:
        return None
    # The knowledge-target fallback reads resolver policy attributes beyond
    # the release payload; both belong to the memo identity.
    return (
        release_id,
        str(getattr(resolver, "content_hash", "") or ""),
        getattr(resolver, "taxonomy_revision", None),
        tuple(getattr(resolver, "knowledge_target_volumes", ()) or ()),
    )


def _memoized(memo: OrderedDict, memo_key: tuple, compute) -> Any:
    with _lookup_memo_lock:
        cached = memo.get(memo_key)
        if cached is not None:
            memo.move_to_end(memo_key)
    if cached is not None:
        return cached
    value = compute()
    with _lookup_memo_lock:
        memo[memo_key] = value
        memo.move_to_end(memo_key)
        while len(memo) > _LOOKUP_MEMO_LIMIT:
            memo.popitem(last=False)
    return value


def is_type_key(key: object) -> bool:
    return bool(_TYPE_KEY.fullmatch(str(key or "").strip()))


def _scope_for_key(key: str, parents: dict[str, str]) -> tuple[str, str]:
    seen: set[str] = set()
    current = key
    while current and current not in seen:
        seen.add(current)
        anchor = curriculum_knowledge_node(current)
        if anchor is not None:
            volume = str(anchor["volume_id"])
            while int(anchor["level"]) > 1:
                anchor = curriculum_knowledge_node(anchor["parent_id"])
                if anchor is None:
                    return volume, ""
            return volume, str(anchor["id"])
        current = parents.get(current, "")
    current = "kp_" + key[3:] if key.startswith("sk_") else key
    while current.startswith("kp_"):
        if curriculum_knowledge_node(current) is not None:
            return _scope_for_key(current, {})
        current = current.rpartition("_")[0]
    return "", ""


def _release_type_scope(resolver: Any) -> _ReleaseTypeScope:
    identity = (str(getattr(resolver, "release_id", "") or ""),
                str(getattr(resolver, "content_hash", "") or ""))
    if identity[0] and identity in _TYPE_RELEASE_CACHE:
        return _TYPE_RELEASE_CACHE[identity]
    nodes = tuple(getattr(resolver, "nodes", ()) or ())
    parents = {str(relation.source_key): str(relation.target_key)
               for relation in getattr(resolver, "relations", ()) or ()
               if relation.relation_type == "parent"}
    scopes = {str(node.stable_key): _scope_for_key(str(node.stable_key), parents)
              for node in nodes}
    typed = frozenset(scopes[str(node.stable_key)][1] for node in nodes
                      if is_type_key(node.stable_key) and scopes[str(node.stable_key)][1])
    result = _ReleaseTypeScope(
        nodes=scopes,
        typed_chapters=typed,
        typed_volumes=frozenset(volume for volume, chapter in scopes.values() if chapter in typed),
    )
    if identity[0]:
        _TYPE_RELEASE_CACHE[identity] = result
        while len(_TYPE_RELEASE_CACHE) > _TYPE_RELEASE_CACHE_LIMIT:
            _TYPE_RELEASE_CACHE.pop(next(iter(_TYPE_RELEASE_CACHE)))
    return result


def _chapter_keys(scope: _ReleaseTypeScope, volume_id: str) -> dict[str, str]:
    keys = scope.chapter_keys.get(volume_id)
    if keys is None:
        volume = curriculum_volume(volume_id=volume_id)
        if volume is None:
            return {}
        keys = {}
        for chapter in volume["chapters"]:
            knowledge_id = str(chapter["knowledge_id"])
            keys.setdefault(str(chapter["id"]), knowledge_id)
            keys.setdefault(knowledge_id, knowledge_id)
        scope.chapter_keys[volume_id] = keys
    return keys


def _chapter_key(scope: _ReleaseTypeScope, volume_id: str, chapter_id: object) -> str:
    selected = str(chapter_id or "").strip()
    return _chapter_keys(scope, volume_id).get(selected, selected)


def _knowledge_fallback(resolver: Any, volume_id: str) -> bool:
    if resolver is None:
        return False
    revision = getattr(resolver, "taxonomy_revision", None)
    if revision is not None and int(revision) < 11:
        return False
    explicit = getattr(resolver, "knowledge_target_volumes", ()) or ()
    return volume_id == "bnu24-math-g8-lower" or volume_id in explicit


def _chapter_target_kinds(
    resolver: Any, scope: _ReleaseTypeScope, selected: str,
) -> dict[str, str]:
    keys = _chapter_keys(scope, selected)
    if not keys:
        return {}
    fallback = ("type" if selected == "bnu24-math-g8-upper" and selected in scope.typed_volumes
                else "knowledge" if _knowledge_fallback(resolver, selected) else "skill")
    cache_key = selected, fallback
    kinds = scope.chapter_kinds.get(cache_key)
    if kinds is None:
        kinds = {chapter: ("type" if chapter in scope.typed_chapters else fallback)
                 for chapter in dict.fromkeys(keys.values())}
        scope.chapter_kinds[cache_key] = kinds
    return kinds


def chapter_target_kinds(resolver: Any, volume_id: object) -> dict[str, str]:
    selected = str(volume_id or "").strip()
    return dict(_chapter_target_kinds(resolver, _release_type_scope(resolver), selected))


def chapter_target_kind(resolver: Any, volume_id: object, chapter_id: object = "") -> str:
    """A chapter has one target kind; a mixed volume reports ``mixed``."""
    selected = str(volume_id or "").strip()
    scope = _release_type_scope(resolver)
    kinds = _chapter_target_kinds(resolver, scope, selected)
    if chapter_id:
        return kinds.get(_chapter_key(scope, selected, chapter_id),
                         "knowledge" if _knowledge_fallback(resolver, selected) else "skill")
    values = set(kinds.values())
    if len(values) == 1:
        return next(iter(values))
    return "mixed" if values else "skill"


def target_kind_for_key(key: object, resolver: Any, volume_id: object = "") -> str:
    clean_key = str(key or "").strip()
    selected_arg = str(volume_id or "").strip()

    def compute() -> str:
        scope = _release_type_scope(resolver)
        volume, chapter = scope.nodes.get(clean_key) or _scope_for_key(clean_key, {})
        selected = selected_arg or volume
        kinds = _chapter_target_kinds(resolver, scope, selected)
        if selected and chapter:
            return kinds.get(chapter,
                             "knowledge" if _knowledge_fallback(resolver, selected) else "skill")
        values = set(kinds.values())
        if len(values) == 1:
            return next(iter(values))
        return "mixed" if values else "skill"

    identity = _release_identity(resolver)
    if identity is None:
        return compute()
    return _memoized(_kind_memo, (clean_key, identity, selected_arg), compute)


def type_keys_active(resolver: Any, volume_id: object = "", chapter_id: object = "") -> bool:
    """Whether this release, volume or chapter contains active type nodes."""
    scope = _release_type_scope(resolver)
    selected = str(volume_id or "").strip()
    if chapter_id:
        return _chapter_key(scope, selected, chapter_id) in scope.typed_chapters
    if selected:
        return selected in scope.typed_volumes
    return bool(scope.typed_chapters)


def is_training_target(key: object, resolver: Any, volume_id: object = "", chapter_id: object = "") -> bool:
    clean_key = str(key or "").strip()

    def compute() -> bool:
        kind = (chapter_target_kind(resolver, volume_id, chapter_id) if chapter_id
                else target_kind_for_key(clean_key, resolver, volume_id))
        if kind == "type":
            return is_type_key(clean_key)
        if kind == "knowledge":
            node = curriculum_knowledge_node(clean_key)
            return bool(node and int(node["level"]) == 3
                        and (not volume_id or node["volume_id"] == str(volume_id)))
        return clean_key.startswith("sk_")

    identity = _release_identity(resolver)
    if identity is None:
        return compute()
    return _memoized(_target_memo, (clean_key, identity, str(volume_id or "").strip(),
                                    str(chapter_id or "").strip()), compute)


def training_keys(keys: object, resolver: Any = None, volume_id: object = "") -> frozenset[str]:
    values = {str(key) for key in keys or () if str(key).strip()}
    if resolver is not None:
        return frozenset(key for key in values if is_training_target(key, resolver, volume_id))
    typed = {key for key in values if is_type_key(key)}
    if typed:
        return frozenset(typed)
    return frozenset(key for key in values if key.startswith("sk_"))


def question_type_key(knowledge_point_tags: object) -> str:
    for value in knowledge_point_tags or ():
        if is_type_key(value):
            return str(value)
    return ""


def _clear_type_cache_for_tests() -> None:
    _TYPE_RELEASE_CACHE.clear()
    _kind_memo.clear()
    _target_memo.clear()


__all__ = ["TYPE_KEY_PATTERN", "chapter_target_kind", "chapter_target_kinds", "is_training_target",
           "is_type_key", "question_type_key", "target_kind_for_key", "training_keys", "type_keys_active"]
