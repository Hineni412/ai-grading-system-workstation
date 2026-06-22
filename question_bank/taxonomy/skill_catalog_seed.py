from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from question_bank.models.skill_catalog import normalize_display_text, normalize_stable_key


_DATA_PATH = Path(__file__).resolve().parent / "data" / "junior_math_skills_v1.json"
_STABLE_KEY = re.compile(r"^[a-z0-9]+(?:[._][a-z0-9]+)+$")
_BROAD_NAMES = {"性质", "计算", "作图", "综合", "应用", "概念", "方法"}
_NEIGHBOR_KINDS = {"same_topic", "prerequisite", "advanced", "co_assessed"}


@dataclass(frozen=True, slots=True)
class BuiltinSkillTopic:
    stable_key: str
    name: str
    grade_min: int
    grade_max: int


@dataclass(frozen=True, slots=True)
class BuiltinSkill:
    stable_key: str
    topic_key: str
    name: str
    aliases: tuple[str, ...]
    grade_min: int
    grade_max: int
    legacy_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BuiltinSkillNeighbor:
    source_key: str
    target_key: str
    kind: str
    weight: float


@dataclass(frozen=True, slots=True)
class BuiltinSkillCatalog:
    version: str
    subject: str
    topics: tuple[BuiltinSkillTopic, ...]
    skills: tuple[BuiltinSkill, ...]
    neighbors: tuple[BuiltinSkillNeighbor, ...]


def load_builtin_catalog(path: str | Path | None = None) -> BuiltinSkillCatalog:
    payload = json.loads(Path(path or _DATA_PATH).read_text(encoding="utf-8"))
    topics = tuple(
        BuiltinSkillTopic(
            stable_key=normalize_stable_key(row.get("stable_key")),
            name=normalize_display_text(row.get("name")),
            grade_min=int(row.get("grade_min")),
            grade_max=int(row.get("grade_max")),
        )
        for row in payload.get("topics", [])
    )
    skills = tuple(
        BuiltinSkill(
            stable_key=normalize_stable_key(row.get("stable_key")),
            topic_key=normalize_stable_key(row.get("topic_key")),
            name=normalize_display_text(row.get("name")),
            aliases=_unique_text(row.get("aliases", [])),
            grade_min=int(row.get("grade_min")),
            grade_max=int(row.get("grade_max")),
            legacy_keys=tuple(
                normalize_stable_key(value)
                for value in row.get("legacy_keys", [])
                if normalize_stable_key(value)
            ),
        )
        for row in payload.get("skills", [])
    )
    neighbors = tuple(
        BuiltinSkillNeighbor(
            source_key=normalize_stable_key(row.get("source_key")),
            target_key=normalize_stable_key(row.get("target_key")),
            kind=normalize_stable_key(row.get("kind")),
            weight=float(row.get("weight")),
        )
        for row in payload.get("neighbors", [])
    )
    return BuiltinSkillCatalog(
        version=normalize_stable_key(payload.get("version")),
        subject=normalize_stable_key(payload.get("subject")),
        topics=topics,
        skills=skills,
        neighbors=neighbors,
    )


def validate_builtin_catalog(catalog: BuiltinSkillCatalog) -> tuple[str, ...]:
    errors: list[str] = []
    if catalog.version != "junior_math_v1":
        errors.append("catalog version must be junior_math_v1")
    if catalog.subject != "math":
        errors.append("catalog subject must be math")

    topic_keys = [topic.stable_key for topic in catalog.topics]
    skill_keys = [skill.stable_key for skill in catalog.skills]
    _validate_unique(topic_keys, "topic stable key", errors)
    _validate_unique(skill_keys, "skill stable key", errors)
    for key in topic_keys + skill_keys:
        if not _STABLE_KEY.fullmatch(key):
            errors.append(f"invalid stable key: {key}")

    topic_key_set = set(topic_keys)
    normalized_labels: dict[str, str] = {}
    for topic in catalog.topics:
        _validate_grades(topic.grade_min, topic.grade_max, topic.stable_key, errors)
        if not topic.name:
            errors.append(f"topic name missing: {topic.stable_key}")
    for skill in catalog.skills:
        _validate_grades(skill.grade_min, skill.grade_max, skill.stable_key, errors)
        if skill.topic_key not in topic_key_set:
            errors.append(f"unknown topic for skill {skill.stable_key}: {skill.topic_key}")
        if not skill.name or skill.name in _BROAD_NAMES:
            errors.append(f"skill name is not concrete: {skill.name or skill.stable_key}")
        for label in (skill.name, *skill.aliases):
            normalized = normalize_stable_key(label)
            previous = normalized_labels.get(normalized)
            if previous is not None and previous != skill.stable_key:
                errors.append(f"duplicate skill label: {label}")
            else:
                normalized_labels[normalized] = skill.stable_key

    skill_key_set = set(skill_keys)
    neighbor_ids: set[tuple[str, str, str]] = set()
    for neighbor in catalog.neighbors:
        identity = (neighbor.source_key, neighbor.target_key, neighbor.kind)
        if identity in neighbor_ids:
            errors.append(f"duplicate neighbor: {identity}")
        neighbor_ids.add(identity)
        if neighbor.source_key not in skill_key_set or neighbor.target_key not in skill_key_set:
            errors.append(f"neighbor references unknown skill: {identity}")
        if neighbor.source_key == neighbor.target_key:
            errors.append(f"neighbor cannot reference itself: {neighbor.source_key}")
        if neighbor.kind not in _NEIGHBOR_KINDS:
            errors.append(f"invalid neighbor kind: {neighbor.kind}")
        if not 0.0 <= neighbor.weight <= 1.0:
            errors.append(f"invalid neighbor weight: {neighbor.weight}")
    return tuple(errors)


def _validate_unique(values: list[str], label: str, errors: list[str]) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            errors.append(f"duplicate {label}: {value}")
        seen.add(value)


def _validate_grades(grade_min: int, grade_max: int, ref: str, errors: list[str]) -> None:
    if not (1 <= grade_min <= grade_max <= 12):
        errors.append(f"invalid grade range for {ref}: {grade_min}-{grade_max}")


def _unique_text(values: Any) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = normalize_display_text(value)
        key = normalize_stable_key(text)
        if text and key not in seen:
            result.append(text)
            seen.add(key)
    return tuple(result)
