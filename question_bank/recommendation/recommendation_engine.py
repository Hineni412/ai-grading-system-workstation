from __future__ import annotations

import re
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from question_bank.current_knowledge import (
    CurrentKnowledgeResolver,
    CurrentKnowledgeUnavailable,
)
from question_bank.recommendation.scoring import (
    difficulty_match_score,
    parse_difficulty,
    recommendation_score,
    tag_match_score,
)
from question_bank.services.question_frequency_service import FrequencyMetrics, QuestionFrequencyService


RECOMMENDATION_TAG_TYPES = ("knowledge_point", "canonical_knowledge_id", "method", "error_type", "model")
TEXT_CLEAN_PATTERN = re.compile(r"[\W_]+", re.UNICODE)


def recommend_for_weak_point(
    db_path: str | Path,
    weak_point: Mapping[str, Any],
    *,
    limit: int = 5,
    similarity_threshold: float = 0.9,
) -> list[dict[str, Any]]:
    database_path = Path(db_path)
    try:
        resolver = CurrentKnowledgeResolver.from_active_database(database_path)
    except CurrentKnowledgeUnavailable:
        return []
    normalized_weak_point = _normalize_weak_point(weak_point, resolver)
    knowledge_point = _text(normalized_weak_point.get("knowledge_point"))
    if not knowledge_point or limit <= 0:
        return []

    candidates = _load_candidates(database_path, normalized_weak_point, resolver)
    frequency_metrics = QuestionFrequencyService(database_path).metrics_for_questions(
        [int(candidate["id"]) for candidate in candidates]
    )
    scored = [
        _score_candidate(candidate, normalized_weak_point, frequency_metrics.get(int(candidate["id"])))
        for candidate in candidates
    ]
    scored.sort(key=lambda item: (-item["recommend_score"], item["question_id"]))
    selected = _select_diverse(
        scored,
        limit=limit,
        similarity_threshold=similarity_threshold,
    )
    for order, item in enumerate(selected, start=1):
        item["suggested_order"] = order
    return selected


def assign_training_stage(candidate: Mapping[str, Any]) -> str:
    difficulty = parse_difficulty(candidate.get("difficulty"))
    if candidate.get("model_tags"):
        return "典型模型"
    if difficulty is not None and difficulty >= 8:
        return "压轴迁移"
    if difficulty is not None and difficulty <= 3:
        return "基础回补"
    if difficulty is not None and 4 <= difficulty <= 6 and candidate.get("method_tags"):
        return "方法形成"
    if difficulty is not None and 6 <= difficulty <= 8:
        return "综合提升"
    return "方法形成" if candidate.get("method_tags") else "基础回补"


def text_similarity(left: object, right: object) -> float:
    normalized_left = normalize_question_text(left)
    normalized_right = normalize_question_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def normalize_question_text(value: object) -> str:
    return TEXT_CLEAN_PATTERN.sub("", _text(value)).casefold()


def practice_gradient_fit(stage: str, difficulty: object) -> float | None:
    normalized = parse_difficulty(difficulty)
    if normalized is None:
        return None
    preferred = {
        "prerequisite": (1, 4),
        "direct": (4, 7),
        "transfer": (7, 10),
    }
    lower, upper = preferred.get(str(stage), (4, 7))
    if lower <= normalized <= upper:
        return 1.0
    if normalized in {lower - 1, upper + 1}:
        return 0.6
    return 0.2


def _load_candidates(
    db_path: Path,
    weak_point: Mapping[str, Any],
    resolver: CurrentKnowledgeResolver,
) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []

    target_keys = set(weak_point.get("_current_core_keys") or [])
    if not target_keys:
        return []
    db_uri = f"{db_path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(db_uri, uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"{_candidate_select_sql()} "
                "LEFT JOIN papers p ON p.id = q.paper_id "
                "WHERE COALESCE(q.is_deleted, 0) = 0 "
                "AND COALESCE(p.import_status, '') <> 'deleted' "
                "ORDER BY q.id ASC"
            ).fetchall()
            candidates = [dict(row) for row in rows]
            _attach_tags(conn, candidates, resolver)
    except sqlite3.Error:
        return []
    return [
        candidate
        for candidate in candidates
        if target_keys.intersection(
            candidate["tags"].get("current_knowledge_key", [])
        )
    ]


def _attach_tags(
    conn: sqlite3.Connection,
    candidates: list[dict[str, Any]],
    resolver: CurrentKnowledgeResolver,
) -> None:
    if not candidates:
        return
    placeholders = ", ".join("?" for _ in candidates)
    tag_type_placeholders = ", ".join("?" for _ in RECOMMENDATION_TAG_TYPES)
    rows = conn.execute(
        f"""
        SELECT question_id, tag_type, tag_value
        FROM question_tags
        WHERE question_id IN ({placeholders})
          AND tag_type IN ({tag_type_placeholders})
        ORDER BY id ASC
        """,
        [*(item["id"] for item in candidates), *RECOMMENDATION_TAG_TYPES],
    ).fetchall()
    tags_by_question: dict[int, dict[str, list[str]]] = {
        int(item["id"]): {
            **{tag_type: [] for tag_type in RECOMMENDATION_TAG_TYPES},
            "current_knowledge_key": [],
        }
        for item in candidates
    }
    for row in rows:
        tag_type = str(row["tag_type"])
        tag_value = _text(row["tag_value"])
        if tag_type in {"knowledge_point", "canonical_knowledge_id"}:
            term = resolver.canonical_term(tag_value)
            resolved = resolver.resolve(tag_value)
            if term is None or not resolved:
                continue
            knowledge = tags_by_question[int(row["question_id"])]
            if term[1] not in knowledge["knowledge_point"]:
                knowledge["knowledge_point"].append(term[1])
            if term[0] not in knowledge["canonical_knowledge_id"]:
                knowledge["canonical_knowledge_id"].append(term[0])
            for target in resolved:
                if target.stable_key not in knowledge["current_knowledge_key"]:
                    knowledge["current_knowledge_key"].append(target.stable_key)
            continue
        values = tags_by_question[int(row["question_id"])][tag_type]
        if tag_value and tag_value not in values:
            values.append(tag_value)
    for item in candidates:
        item["tags"] = tags_by_question[int(item["id"])]


def _score_candidate(
    candidate: Mapping[str, Any],
    weak_point: Mapping[str, Any],
    frequency: FrequencyMetrics | None,
) -> dict[str, Any]:
    tags = candidate["tags"]
    matched_errors = _overlap(weak_point.get("error_types", []), tags.get("error_type", []))
    frequency = frequency or FrequencyMetrics(available=False)
    frequency_rate = min(1.0, frequency.questions_per_paper) if frequency.available else None
    skill_frequency_rate = min(1.0, frequency.skill_frequency) if frequency.skill_available else None
    score = recommendation_score(
        mastery=_rate(weak_point.get("mastery"), default=0.0),
        frequency_rate=frequency_rate,
        tag_score=tag_match_score(weak_point, tags),
        difficulty_score=difficulty_match_score(candidate.get("difficulty"), weak_point),
        shenzhen_fit_score=frequency.shenzhen_fit_score if frequency.shenzhen_fit_available else None,
        shenzhen_frequency_rate=min(1.0, frequency.shenzhen_questions_per_paper),
        national_frequency_rate=min(1.0, frequency.national_questions_per_paper),
        skill_frequency_rate=skill_frequency_rate,
    )
    reason_parts = [f"匹配薄弱知识点“{_text(weak_point.get('knowledge_point'))}”"]
    if matched_errors:
        reason_parts.append(f"匹配错因“{'、'.join(matched_errors)}”")
    if frequency.available:
        reason_parts.append(f"同类题考频 {frequency.matched_question_count}/{frequency.eligible_paper_count} 卷")
    if frequency.shenzhen_fit_available:
        reason_parts.append(f"深圳中考适配度 {frequency.shenzhen_fit_score:.0%}")
    if difficulty_match_score(candidate.get("difficulty"), weak_point) >= 1.0:
        reason_parts.append("难度适配")
    method_tags = list(tags.get("method", []))
    if method_tags:
        reason_parts.append(f"覆盖方法“{method_tags[0]}”")

    payload = {
        "question_id": int(candidate["id"]),
        "source_paper": _source_paper(candidate),
        "question_number": _text(candidate.get("question_number")),
        "knowledge_points": list(tags.get("knowledge_point", [])),
        "difficulty": _text(candidate.get("difficulty")),
        "frequency": frequency.to_dict(),
        "recommend_reason": "，".join(reason_parts),
        "suggested_order": 0,
        "training_stage": "",
        "method_tags": method_tags,
        "recommend_score": score,
    }
    training_candidate = {
        **payload,
        "model_tags": list(tags.get("model", [])),
    }
    payload["training_stage"] = assign_training_stage(training_candidate)
    payload["_question_text"] = candidate.get("question_text")
    payload["_paper_key"] = _paper_key(candidate)
    return payload


def _select_diverse(
    scored: list[dict[str, Any]],
    *,
    limit: int,
    similarity_threshold: float,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    paper_counts: Counter[str] = Counter()
    selected_methods: set[str] = set()

    diverse_candidates = sorted(
        scored,
        key=lambda item: (
            _method_already_seen(item, selected_methods),
            -item["recommend_score"],
            item["question_id"],
        ),
    )
    pending = list(diverse_candidates)
    while pending and len(selected) < limit:
        next_pending: list[dict[str, Any]] = []
        changed = False
        for item in pending:
            if len(selected) >= limit:
                break
            if not _can_select(item, selected, paper_counts, similarity_threshold):
                continue
            unseen_methods = set(item["method_tags"]) - selected_methods
            if item["method_tags"] and not unseen_methods and _has_unseen_methods(pending, selected_methods):
                next_pending.append(item)
                continue
            _accept_item(item, selected, paper_counts, selected_methods)
            changed = True
        if not changed:
            for item in next_pending:
                if len(selected) >= limit:
                    break
                if _can_select(item, selected, paper_counts, similarity_threshold):
                    _accept_item(item, selected, paper_counts, selected_methods)
            break
        pending = next_pending

    return [_public_payload(item) for item in selected]


def _accept_item(
    item: dict[str, Any],
    selected: list[dict[str, Any]],
    paper_counts: Counter[str],
    selected_methods: set[str],
) -> None:
    selected.append(item)
    paper_counts[item["_paper_key"]] += 1
    selected_methods.update(item["method_tags"])


def _can_select(
    item: Mapping[str, Any],
    selected: list[dict[str, Any]],
    paper_counts: Counter[str],
    similarity_threshold: float,
) -> bool:
    if paper_counts[item["_paper_key"]] >= 2:
        return False
    return all(
        text_similarity(item.get("_question_text"), chosen.get("_question_text")) < similarity_threshold
        for chosen in selected
    )


def _has_unseen_methods(items: Iterable[Mapping[str, Any]], selected_methods: set[str]) -> bool:
    return any(set(item.get("method_tags", [])) - selected_methods for item in items)


def _method_already_seen(item: Mapping[str, Any], selected_methods: set[str]) -> bool:
    methods = set(item.get("method_tags", []))
    return bool(methods) and not bool(methods - selected_methods)


def _public_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in item.items()
        if not key.startswith("_")
    }


def _source_paper(candidate: Mapping[str, Any]) -> str:
    return (
        _text(candidate.get("paper_title"))
        or _text(candidate.get("paper_source_file"))
        or _text(candidate.get("question_source_file"))
    )


def _paper_key(candidate: Mapping[str, Any]) -> str:
    return _text(candidate.get("paper_id")) or _source_paper(candidate) or f"question-{candidate['id']}"


def _overlap(left: object, right: object) -> list[str]:
    left_values = {_text(value).casefold() for value in _as_list(left) if _text(value)}
    result: list[str] = []
    for value in _as_list(right):
        text = _text(value)
        if text.casefold() in left_values and text not in result:
            result.append(text)
    return result


def _as_list(value: object) -> list[object]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]


def _rate(value: object, *, default: float) -> float:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return default
    if rate > 1:
        rate /= 100
    return min(max(rate, 0.0), 1.0)


def _text(value: object) -> str:
    return str(value or "").strip()


def _candidate_select_sql() -> str:
    return """
        SELECT DISTINCT
            q.id,
            q.paper_id,
            q.question_number,
            q.question_text,
            q.difficulty,
            q.source_file AS question_source_file,
            p.title AS paper_title,
            p.source_file AS paper_source_file
        FROM questions q
    """


def _normalize_weak_point(
    weak_point: Mapping[str, Any],
    resolver: CurrentKnowledgeResolver,
) -> dict[str, Any]:
    item = dict(weak_point)
    if item.get("knowledge_point") and not item.get("raw_knowledge_point"):
        item["raw_knowledge_point"] = item.get("knowledge_point")
    values = [
        item.get("canonical_knowledge_id"),
        item.get("knowledge_point"),
        *(_as_list(item.get("raw_knowledge_ids"))),
        item.get("raw_knowledge_point"),
    ]
    term = next(
        (
            resolved
            for value in values
            if (resolved := resolver.canonical_term(value))
        ),
        None,
    )
    targets = resolver.resolve_many(values)
    if term is None or not targets:
        item["canonical_knowledge_id"] = ""
        item["knowledge_point"] = ""
        item["_current_core_keys"] = []
        return item
    item["canonical_knowledge_id"] = term[0]
    item["knowledge_point"] = term[1]
    item["_current_core_keys"] = list(
        dict.fromkeys(target.stable_key for target in targets)
    )
    errors = []
    for value in _as_list(item.get("error_types")) + _as_list(item.get("raw_error_types")):
        normalized = _text(value)
        if normalized and normalized not in errors:
            errors.append(normalized)
    if errors:
        item["error_types"] = errors
    return item
