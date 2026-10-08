"""Current training targets, resolved per textbook chapter."""
from __future__ import annotations

import re
from typing import Any

from question_bank.taxonomy.curriculum_catalog import (
    curriculum_knowledge_node,
    curriculum_volume,
)

TYPE_KEY_PATTERN = r"^kp_[a-z0-9_]+_t\d{2}$"
_TYPE_KEY = re.compile(TYPE_KEY_PATTERN)
_TYPE_RELEASE_CACHE_LIMIT = 16
_TYPE_RELEASE_CACHE: dict[tuple[str, str], tuple[dict[str, tuple[str, str]], frozenset[str]]] = {}


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


def _release_type_scope(resolver: Any) -> tuple[dict[str, tuple[str, str]], frozenset[str]]:
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
    result = scopes, typed
    if identity[0]:
        _TYPE_RELEASE_CACHE[identity] = result
        while len(_TYPE_RELEASE_CACHE) > _TYPE_RELEASE_CACHE_LIMIT:
            _TYPE_RELEASE_CACHE.pop(next(iter(_TYPE_RELEASE_CACHE)))
    return result


def _chapter_key(volume_id: str, chapter_id: object) -> str:
    selected = str(chapter_id or "").strip()
    volume = curriculum_volume(volume_id=volume_id)
    if volume:
        for chapter in volume["chapters"]:
            if selected in {str(chapter["id"]), str(chapter["knowledge_id"])}:
                return str(chapter["knowledge_id"])
    return selected


def _knowledge_fallback(resolver: Any, volume_id: str) -> bool:
    if resolver is None:
        return False
    revision = getattr(resolver, "taxonomy_revision", None)
    if revision is not None and int(revision) < 11:
        return False
    explicit = getattr(resolver, "knowledge_target_volumes", ()) or ()
    return volume_id == "bnu24-math-g8-lower" or volume_id in explicit


def chapter_target_kinds(resolver: Any, volume_id: object) -> dict[str, str]:
    selected = str(volume_id or "").strip()
    volume = curriculum_volume(volume_id=selected)
    if volume is None:
        return {}
    scopes, typed = _release_type_scope(resolver)
    if selected == "bnu24-math-g8-upper" and any(v == selected and c in typed for v, c in scopes.values()):
        return {str(chapter["knowledge_id"]): "type" for chapter in volume["chapters"]}
    fallback = "knowledge" if _knowledge_fallback(resolver, selected) else "skill"
    return {str(chapter["knowledge_id"]): ("type" if str(chapter["knowledge_id"]) in typed else fallback)
            for chapter in volume["chapters"]}


def chapter_target_kind(resolver: Any, volume_id: object, chapter_id: object = "") -> str:
    """A chapter has one target kind; a mixed volume reports ``mixed``."""
    selected = str(volume_id or "").strip()
    kinds = chapter_target_kinds(resolver, selected)
    if chapter_id:
        return kinds.get(_chapter_key(selected, chapter_id),
                         "knowledge" if _knowledge_fallback(resolver, selected) else "skill")
    values = set(kinds.values())
    if len(values) == 1:
        return next(iter(values))
    return "mixed" if values else "skill"


def target_kind_for_key(key: object, resolver: Any, volume_id: object = "") -> str:
    clean_key = str(key or "").strip()
    scopes, _typed = _release_type_scope(resolver)
    volume, chapter = scopes.get(clean_key) or _scope_for_key(clean_key, {})
    selected = str(volume_id or "").strip() or volume
    if selected and chapter:
        return chapter_target_kind(resolver, selected, chapter)
    return chapter_target_kind(resolver, selected)


def type_keys_active(resolver: Any, volume_id: object = "", chapter_id: object = "") -> bool:
    """Whether this release, volume or chapter contains active type nodes."""
    scopes, typed = _release_type_scope(resolver)
    selected = str(volume_id or "").strip()
    if chapter_id:
        return _chapter_key(selected, chapter_id) in typed
    if selected:
        return any(volume == selected and chapter in typed for volume, chapter in scopes.values())
    return bool(typed)


def is_training_target(key: object, resolver: Any, volume_id: object = "", chapter_id: object = "") -> bool:
    clean_key = str(key or "").strip()
    kind = (chapter_target_kind(resolver, volume_id, chapter_id) if chapter_id
            else target_kind_for_key(clean_key, resolver, volume_id))
    if kind == "type":
        return is_type_key(clean_key)
    if kind == "knowledge":
        node = curriculum_knowledge_node(clean_key)
        return bool(node and int(node["level"]) == 3
                    and (not volume_id or node["volume_id"] == str(volume_id)))
    return clean_key.startswith("sk_")


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


__all__ = ["TYPE_KEY_PATTERN", "chapter_target_kind", "chapter_target_kinds", "is_training_target",
           "is_type_key", "question_type_key", "target_kind_for_key", "training_keys", "type_keys_active"]