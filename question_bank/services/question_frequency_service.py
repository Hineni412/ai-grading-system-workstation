from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from question_bank.database.schema import connect, initialize_database
from question_bank.services.question_service import CORE_ANALYSIS_TAG_TYPES


FINGERPRINT_VERSION = 2
FORMAL_EXAM_TYPES = ("期中", "期末", "中考")
PRACTICE_EXAM_MARKERS = ("同步练习", "专题练习", "练习", "作业")
SIMPLE_QUESTION_TYPES = ("选择", "填空", "choice", "blank", "fill")


@dataclass(frozen=True)
class FrequencyMetrics:
    available: bool
    exam_type: str = ""
    fingerprint: str = ""
    matched_question_count: int = 0
    eligible_paper_count: int = 0
    questions_per_paper: float = 0.0
    national_matched_question_count: int = 0
    national_eligible_paper_count: int = 0
    national_questions_per_paper: float = 0.0
    shenzhen_matched_question_count: int = 0
    shenzhen_eligible_paper_count: int = 0
    shenzhen_questions_per_paper: float = 0.0
    shenzhen_fit_available: bool = False
    shenzhen_fit_score: float = 0.0
    shenzhen_fit_notes: tuple[str, ...] = ()
    shenzhen_similar_question_ids: tuple[int, ...] = ()
    confidence_label: str = "暂无数据"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_exam_type(value: object) -> str:
    text = str(value or "").strip()
    if any(marker in text for marker in PRACTICE_EXAM_MARKERS):
        return ""
    for exam_type in FORMAL_EXAM_TYPES:
        if exam_type in text:
            return exam_type
    return ""


def is_frequency_exam_type(value: object) -> bool:
    return bool(normalize_exam_type(value))


def frequency_summary(metrics: FrequencyMetrics | None) -> str:
    if metrics is None or not metrics.available:
        return ""
    if metrics.exam_type == "中考":
        parts = [
            f"全国考频 {metrics.national_matched_question_count}题/{metrics.national_eligible_paper_count}卷",
            f"深圳考频 {metrics.shenzhen_matched_question_count}题/{metrics.shenzhen_eligible_paper_count}卷",
        ]
        if metrics.shenzhen_fit_available:
            parts.append(f"深圳适配 {metrics.shenzhen_fit_score:.0%}")
        return " · ".join(parts)
    return (
        f"{metrics.exam_type}考频 {metrics.matched_question_count}题/"
        f"{metrics.eligible_paper_count}卷（平均 {metrics.questions_per_paper:.2f}题/卷）"
    )


def build_question_fingerprint(question: Mapping[str, Any]) -> str:
    grouped = _group_tags(question.get("tags", []))
    question_type = _normalize_question_type(question.get("question_type"))
    knowledge_raw = _first(grouped.get("canonical_knowledge_id")) or _first(grouped.get("knowledge_point"))
    if not question_type or not knowledge_raw:
        return ""
        
    from question_bank.taxonomy.registry import get_parent_knowledge_category
    knowledge = get_parent_knowledge_category(knowledge_raw)
    
    parts = [question_type, knowledge]
    
    # Combine image and context/exploration style features
    style = _style_features(question)
    has_img = "有图" if style.get("has_images") else "无图"
    parts.append(has_img)
    
    is_ctx = "情境/探究" if (style.get("is_contextual") or style.get("is_exploratory")) else "普通"
    parts.append(is_ctx)
    
    if not _is_simple_question_type(question_type):
        method_or_model = _first(grouped.get("method")) or _first(grouped.get("model"))
        if method_or_model:
            parts.append(method_or_model)
            
        diff = style.get("difficulty")
        if diff is not None:
            if diff <= 3:
                diff_lvl = "基础"
            elif diff <= 7:
                diff_lvl = "中档"
            else:
                diff_lvl = "拔高"
            parts.append(diff_lvl)
            
    return "|".join(_compact(part) for part in parts if _compact(part))


class QuestionFrequencyService:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def initialize_database(self) -> None:
        initialize_database(self.db_path)

    def metrics_for_questions(self, question_ids: list[int] | tuple[int, ...]) -> dict[int, FrequencyMetrics]:
        self.initialize_database()
        with connect(self.db_path) as conn:
            result: dict[int, FrequencyMetrics] = {}
            for question_id in dict.fromkeys(int(value) for value in question_ids):
                target = _load_question(conn, question_id)
                result[question_id] = (
                    _metrics_for_target(conn, target)
                    if target is not None
                    else FrequencyMetrics(available=False)
                )
            return result

    def metrics_for_question(self, question_id: int) -> FrequencyMetrics:
        self.initialize_database()
        with connect(self.db_path) as conn:
            target = _load_question(conn, int(question_id))
            if target is None:
                return FrequencyMetrics(available=False)
            return _metrics_for_target(conn, target)

    def backfill_all_fingerprints(self) -> None:
        self.initialize_database()
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT q.id
                FROM questions q
                LEFT JOIN question_fingerprints qf ON qf.question_id = q.id
                WHERE q.is_deleted = 0 AND (qf.question_id IS NULL OR qf.fingerprint_version <> ?)
                """,
                (FINGERPRINT_VERSION,)
            ).fetchall()
            if not rows:
                return
            for row in rows:
                qid = int(row["id"])
                target = _load_question(conn, qid)
                if target is not None:
                    fingerprint = build_question_fingerprint(target)
                    if fingerprint:
                        _cache_fingerprint(conn, qid, fingerprint, _style_features(target))


def _metrics_for_target(conn, target: Mapping[str, Any]) -> FrequencyMetrics:
    question_id = int(target["id"])
    exam_type = normalize_exam_type(target.get("exam_type"))
    fingerprint = build_question_fingerprint(target)
    if not exam_type or not fingerprint:
        return FrequencyMetrics(available=False, exam_type=exam_type, fingerprint=fingerprint)
    _cache_fingerprint(conn, question_id, fingerprint, _style_features(target))
    eligible_papers = _eligible_paper_ids(conn, target, exam_type=exam_type)
    matched = _matching_question_count(conn, eligible_papers, fingerprint)
    if exam_type != "中考":
        count = len(eligible_papers)
        return FrequencyMetrics(
            available=bool(count),
            exam_type=exam_type,
            fingerprint=fingerprint,
            matched_question_count=matched,
            eligible_paper_count=count,
            questions_per_paper=_ratio(matched, count),
            confidence_label=_confidence_label(count),
        )
    shenzhen_papers = _eligible_paper_ids(conn, target, exam_type=exam_type, city="深圳市")
    shenzhen_matched = _matching_question_count(conn, shenzhen_papers, fingerprint)
    national_count = len(eligible_papers)
    shenzhen_count = len(shenzhen_papers)
    fit_score, fit_notes, similar_ids = _shenzhen_fit(
        conn,
        target,
        shenzhen_papers,
        fingerprint,
        shenzhen_frequency=_ratio(shenzhen_matched, shenzhen_count),
    )
    is_external = not _is_shenzhen(target)
    return FrequencyMetrics(
        available=bool(national_count),
        exam_type=exam_type,
        fingerprint=fingerprint,
        matched_question_count=matched,
        eligible_paper_count=national_count,
        questions_per_paper=_ratio(matched, national_count),
        national_matched_question_count=matched,
        national_eligible_paper_count=national_count,
        national_questions_per_paper=_ratio(matched, national_count),
        shenzhen_matched_question_count=shenzhen_matched,
        shenzhen_eligible_paper_count=shenzhen_count,
        shenzhen_questions_per_paper=_ratio(shenzhen_matched, shenzhen_count),
        shenzhen_fit_available=bool(is_external and similar_ids),
        shenzhen_fit_score=fit_score if is_external else 0.0,
        shenzhen_fit_notes=tuple(fit_notes) if is_external else (),
        shenzhen_similar_question_ids=tuple(similar_ids) if is_external else (),
        confidence_label=_confidence_label(national_count),
    )


def _load_question(conn, question_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT q.*, p.title AS paper_title, p.year, p.province, p.city, p.district,
               p.exam_type, p.grade, p.semester
        FROM questions q
        LEFT JOIN papers p ON p.id = q.paper_id
        WHERE q.id = ? AND q.is_deleted = 0
          AND COALESCE(p.import_status, '') <> 'deleted'
        """,
        (question_id,),
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    tags = conn.execute(
        "SELECT tag_type, tag_value FROM question_tags WHERE question_id = ? ORDER BY id",
        (question_id,),
    ).fetchall()
    item["tags"] = [dict(tag) for tag in tags]
    return item


def _eligible_paper_ids(conn, target: Mapping[str, Any], *, exam_type: str, city: str | None = None) -> list[int]:
    clauses = [
        "COALESCE(p.import_status, '') <> 'deleted'",
        "p.grade = ?",
    ]
    params: list[Any] = [target.get("grade")]
    if exam_type == "中考":
        clauses.append("p.exam_type LIKE '%中考%'")
    else:
        clauses.extend(["p.exam_type LIKE ?", "COALESCE(p.semester, '') = COALESCE(?, '')"])
        params.extend([f"%{exam_type}%", target.get("semester")])
    if city:
        clauses.append("(p.city = ? OR (COALESCE(p.city, '') = '' AND p.district LIKE ?))")
        params.extend([city, f"%{city.removesuffix('市')}%"])
    rows = conn.execute(
        f"""
        SELECT p.id
        FROM papers p
        WHERE {' AND '.join(clauses)}
          AND (
              SELECT COUNT(*)
              FROM questions q
              WHERE q.paper_id = p.id AND q.is_deleted = 0
          ) > 0
          AND (
              SELECT COUNT(DISTINCT q.id)
              FROM questions q
              WHERE q.paper_id = p.id AND q.is_deleted = 0
                AND {_complete_tag_sql('q.id')}
          ) * 1.0 / (
              SELECT COUNT(*)
              FROM questions q
              WHERE q.paper_id = p.id AND q.is_deleted = 0
          ) >= 0.9
        ORDER BY p.id
        """,
        params,
    ).fetchall()
    return [int(row["id"]) for row in rows]


def _matching_question_count(conn, paper_ids: list[int], fingerprint: str) -> int:
    if not paper_ids:
        return 0
    placeholders = ", ".join("?" for _ in paper_ids)
    rows = conn.execute(
        f"""
        SELECT q.id
        FROM questions q
        WHERE q.paper_id IN ({placeholders}) AND q.is_deleted = 0
        ORDER BY q.id
        """,
        paper_ids,
    ).fetchall()
    count = 0
    for row in rows:
        item = _load_question(conn, int(row["id"]))
        if item is not None and build_question_fingerprint(item) == fingerprint:
            count += 1
    return count


def _shenzhen_fit(
    conn,
    target: Mapping[str, Any],
    paper_ids: list[int],
    fingerprint: str,
    *,
    shenzhen_frequency: float,
) -> tuple[float, list[str], list[int]]:
    if not paper_ids or _is_shenzhen(target):
        return 0.0, [], []
    placeholders = ", ".join("?" for _ in paper_ids)
    rows = conn.execute(
        f"SELECT id FROM questions WHERE paper_id IN ({placeholders}) AND is_deleted = 0 ORDER BY id",
        paper_ids,
    ).fetchall()
    target_features = _style_features(target)
    target_tags = _group_tags(target.get("tags", []))
    scored: list[tuple[float, int, list[str]]] = []
    for row in rows:
        candidate = _load_question(conn, int(row["id"]))
        if candidate is None or build_question_fingerprint(candidate) != fingerprint:
            continue
        score, notes = _style_fit_score(
            target_features,
            target_tags,
            _style_features(candidate),
            _group_tags(candidate.get("tags", [])),
            shenzhen_frequency=shenzhen_frequency,
        )
        scored.append((score, int(row["id"]), notes))
    scored.sort(key=lambda item: (-item[0], item[1]))
    top = scored[:3]
    if not top:
        return 0.0, [], []
    average = round(sum(item[0] for item in top) / len(top), 4)
    notes: list[str] = []
    for _, _, item_notes in top:
        for note in item_notes:
            if note not in notes:
                notes.append(note)
    return average, notes[:4], [item[1] for item in top]


def _style_fit_score(
    target_features: Mapping[str, Any],
    target_tags: Mapping[str, list[str]],
    candidate_features: Mapping[str, Any],
    candidate_tags: Mapping[str, list[str]],
    *,
    shenzhen_frequency: float,
) -> tuple[float, list[str]]:
    notes: list[str] = []
    method_model = _overlap_rate(
        [*target_tags.get("method", []), *target_tags.get("model", [])],
        [*candidate_tags.get("method", []), *candidate_tags.get("model", [])],
    )
    if method_model >= 0.5:
        notes.append("主要方法与模型接近")
    ability = _overlap_rate(target_tags.get("ability", []), candidate_tags.get("ability", []))
    if ability >= 0.5:
        notes.append("核心能力要求接近")
    structure_keys = ("question_type", "has_images", "subquestion_count", "is_contextual", "is_exploratory")
    structure = sum(target_features.get(key) == candidate_features.get(key) for key in structure_keys) / len(structure_keys)
    if structure >= 0.8:
        notes.append("题目结构接近深圳真题")
    target_difficulty = target_features.get("difficulty")
    candidate_difficulty = candidate_features.get("difficulty")
    if target_difficulty is None or candidate_difficulty is None:
        difficulty = 0.5
    else:
        difficulty = max(0.0, 1 - abs(float(target_difficulty) - float(candidate_difficulty)) / 5)
    if difficulty >= 0.8:
        notes.append("难度接近")
    position = 1.0 if _position_bucket(target_features.get("question_number")) == _position_bucket(candidate_features.get("question_number")) else 0.3
    if position >= 1.0:
        notes.append("整卷位置接近")
    score = (
        method_model * 0.30
        + structure * 0.25
        + ability * 0.10
        + difficulty * 0.15
        + position * 0.10
        + min(1.0, shenzhen_frequency) * 0.10
    )
    return round(score, 4), notes


def _complete_tag_sql(question_id_expr: str) -> str:
    return " AND ".join(
        f"""EXISTS (
            SELECT 1 FROM question_tags ct
            WHERE ct.question_id = {question_id_expr}
              AND ct.tag_type = '{tag_type}'
              AND COALESCE(ct.tag_value, '') <> ''
        )"""
        for tag_type in CORE_ANALYSIS_TAG_TYPES
    )


def _cache_fingerprint(conn, question_id: int, fingerprint: str, style_features: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO question_fingerprints (
            question_id, fingerprint_version, base_fingerprint, style_features_json, updated_at
        ) VALUES (?, ?, ?, ?, datetime('now','localtime'))
        ON CONFLICT(question_id) DO UPDATE SET
            fingerprint_version = excluded.fingerprint_version,
            base_fingerprint = excluded.base_fingerprint,
            style_features_json = excluded.style_features_json,
            updated_at = datetime('now','localtime')
        """,
        (question_id, FINGERPRINT_VERSION, fingerprint, json.dumps(style_features, ensure_ascii=False)),
    )


def _style_features(question: Mapping[str, Any]) -> dict[str, Any]:
    text = str(question.get("question_text") or "")
    return {
        "question_type": _normalize_question_type(question.get("question_type")),
        "difficulty": _number(question.get("difficulty")),
        "question_number": str(question.get("question_number") or ""),
        "has_images": bool(question.get("has_images")),
        "subquestion_count": len(re.findall(r"(?:^|\s)[（(]\d+[)）]", text)),
        "is_contextual": any(token in text for token in ("实际", "情境", "生活", "方案")),
        "is_exploratory": any(token in text for token in ("探究", "发现", "猜想", "开放")),
    }


def _is_shenzhen(question: Mapping[str, Any]) -> bool:
    city = str(question.get("city") or "").strip()
    district = str(question.get("district") or "").strip()
    return city == "深圳市" or (not city and "深圳" in district)


def _overlap_rate(left: list[str], right: list[str]) -> float:
    left_set = {_compact(value) for value in left if _compact(value)}
    right_set = {_compact(value) for value in right if _compact(value)}
    if not left_set and not right_set:
        return 0.0
    if not left_set or not right_set:
        return 0.0
    return len(left_set.intersection(right_set)) / len(left_set.union(right_set))


def _position_bucket(value: object) -> str:
    try:
        number = int(re.search(r"\d+", str(value or "")).group())
    except (AttributeError, ValueError):
        return "unknown"
    if number <= 10:
        return "early"
    if number <= 18:
        return "middle"
    return "late"


def _group_tags(tags: object) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for tag in tags if isinstance(tags, list) else []:
        if not isinstance(tag, Mapping):
            continue
        tag_type = str(tag.get("tag_type") or "").strip()
        tag_value = str(tag.get("tag_value") or "").strip()
        if tag_type and tag_value and tag_value not in grouped.setdefault(tag_type, []):
            grouped[tag_type].append(tag_value)
    return grouped


def _normalize_question_type(value: object) -> str:
    text = str(value or "").strip()
    lowered = text.casefold()
    if "选择" in text or "choice" in lowered:
        return "选择题"
    if "填空" in text or "blank" in lowered or "fill" in lowered:
        return "填空题"
    if "证明" in text or "proof" in lowered:
        return "证明题"
    if "解答" in text or "solution" in lowered or "综合" in text:
        return "解答题"
    return text


def _is_simple_question_type(question_type: str) -> bool:
    lowered = question_type.casefold()
    return any(token in lowered for token in SIMPLE_QUESTION_TYPES)


def _first(values: list[str] | None) -> str:
    return str((values or [""])[0] or "").strip()


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _number(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _confidence_label(sample_count: int) -> str:
    if sample_count >= 10:
        return "高"
    if sample_count >= 5:
        return "中"
    if sample_count > 0:
        return "低"
    return "暂无数据"
