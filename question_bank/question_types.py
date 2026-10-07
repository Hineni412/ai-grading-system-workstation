"""Question-type (题型) identity helpers — the single source of truth.

Question types are ``core`` nodes whose stable keys end in ``_tNN``
(``kp_bnu24_math_g8_upper_{章}_{节}_t{NN}``), introduced by release
``kgr_bnu_math_curriculum_2026_10_v9``. Volumes without type nodes keep the
previous skill (``sk_``) behaviour, including unconverted volumes in v9;
every call site that
distinguishes training targets must gate on :func:`type_keys_active` instead
of the key shape alone.
"""
from __future__ import annotations

import re
from typing import Any

from question_bank.taxonomy.curriculum_catalog import curriculum_knowledge_node

TYPE_KEY_PATTERN = r"^kp_[a-z0-9_]+_t\d{2}$"
_TYPE_KEY = re.compile(TYPE_KEY_PATTERN)

# release_id -> (any types, typed volumes, node-to-volume map). Small and
# bounded: release ids are immutable, so the cache never needs invalidation.
_TYPE_RELEASE_CACHE_LIMIT = 16
_TYPE_RELEASE_CACHE: dict[str, tuple[bool, frozenset[str], dict[str, str]]] = {}


def is_type_key(key: object) -> bool:
    """True when ``key`` has the question-type stable-key shape."""
    return bool(_TYPE_KEY.fullmatch(str(key or "").strip()))


def _volume_for_key(key: str, parents: dict[str, str]) -> str:
    """Find a bundled curriculum anchor through parents or its namespace."""
    seen: set[str] = set()
    current = key
    while current and current not in seen:
        seen.add(current)
        anchor = curriculum_knowledge_node(current)
        if anchor is not None:
            return str(anchor["volume_id"])
        current = parents.get(current, "")
    # Stable type/skill keys also encode the section's curriculum namespace.
    # This covers callers with a key but no resolver node (e.g. frozen input).
    current = "kp_" + key[3:] if key.startswith("sk_") else key
    while current.startswith("kp_"):
        anchor = curriculum_knowledge_node(current)
        if anchor is not None:
            return str(anchor["volume_id"])
        current = current.rpartition("_")[0]
    return ""


def _release_type_scope(resolver: Any) -> tuple[bool, frozenset[str], dict[str, str]]:
    release_id = str(getattr(resolver, "release_id", "") or "").strip()
    if release_id and release_id in _TYPE_RELEASE_CACHE:
        return _TYPE_RELEASE_CACHE[release_id]
    nodes = tuple(getattr(resolver, "nodes", ()) or ())
    parents = {
        str(relation.source_key): str(relation.target_key)
        for relation in getattr(resolver, "relations", ()) or ()
        if relation.relation_type == "parent"
    }
    volumes = {
        str(node.stable_key): _volume_for_key(str(node.stable_key), parents)
        for node in nodes
    }
    type_keys = {str(node.stable_key) for node in nodes if is_type_key(node.stable_key)}
    result = (bool(type_keys), frozenset(volumes[key] for key in type_keys if volumes[key]), volumes)
    if release_id:
        _TYPE_RELEASE_CACHE[release_id] = result
        while len(_TYPE_RELEASE_CACHE) > _TYPE_RELEASE_CACHE_LIMIT:
            _TYPE_RELEASE_CACHE.pop(next(iter(_TYPE_RELEASE_CACHE)))
    return result


def type_keys_active(resolver: Any, volume_id: object = "") -> bool:
    """True iff the selected volume contains an active type node.

    An omitted volume checks the whole release for projection capabilities.
    Presentation and recommendation callers pass their current volume.
    Cached per immutable release; ``None`` preserves skill behaviour.
    """
    if resolver is None:
        return False
    any_types, typed_volumes, _volumes = _release_type_scope(resolver)
    selected = str(volume_id or "").strip()
    return selected in typed_volumes if selected else any_types


def is_training_target(key: object, resolver: Any, volume_id: object = "") -> bool:
    """The key kind that carries mastery/recommendation targets.

    The selected volume uses types only if converted. Without a selected
    volume, infer the key's volume so unconverted skills remain targets.
    """
    selected = str(volume_id or "").strip()
    if not selected and resolver is not None:
        _any_types, _typed_volumes, volumes = _release_type_scope(resolver)
        clean_key = str(key or "").strip()
        selected = volumes.get(clean_key) or _volume_for_key(clean_key, {})
    if type_keys_active(resolver, selected):
        return is_type_key(key)
    return str(key or "").startswith("sk_")


def training_keys(keys: object) -> frozenset[str]:
    """Training-target keys inside ``keys``, without a resolver.

    Returns the type keys when any are present, otherwise the ``sk_`` keys.
    For pure helpers (paper quotas, dedup) that never see a resolver: on a
    release without type nodes no type keys exist, so this is exactly the
    historical ``sk_`` behaviour; on a typed release a typed question counts
    by its type while untyped questions fall back to their skills.
    """
    values = {str(key) for key in keys or () if str(key).strip()}
    typed = {key for key in values if is_type_key(key)}
    if typed:
        return frozenset(typed)
    return frozenset(key for key in values if key.startswith("sk_"))


def question_type_key(knowledge_point_tags: object) -> str:
    """The first type-keyed ``knowledge_point`` tag value, or ``""``.

    A bank question carries at most one primary type link, so the first match
    is the primary type.
    """
    for value in knowledge_point_tags or ():
        if is_type_key(value):
            return str(value)
    return ""


def _clear_type_cache_for_tests() -> None:
    _TYPE_RELEASE_CACHE.clear()


__all__ = [
    "TYPE_KEY_PATTERN",
    "is_training_target",
    "is_type_key",
    "question_type_key",
    "training_keys",
    "type_keys_active",
]
