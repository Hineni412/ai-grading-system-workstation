from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from question_bank.database.schema import connect, initialize_database
from question_bank.services.question_service import CORE_ANALYSIS_TAG_TYPES


FINGERPRINT_VERSION = 4
FORMAL_EXAM_TYPES = ("期中", "期末", "中考")
PRACTICE_EXAM_MARKERS = ("同步练习", "专题练习", "练习", "作业")
SIMPLE_QUESTION_TYPES = ("选择", "填空", "choice", "blank", "fill")

# 极宽泛的"思想方法"标签——几乎覆盖所有综合题，参与指纹会让大量几何/函数综合题
# 聚到同一个匹配桶里，把"宽泛"误判成"高频"。这些标签从指纹中排除，指纹只保留
# 具体方法（如配方法、待定系数法、面积法）来区分技能。
GENERIC_METHOD_TAGS = frozenset({
    "数形结合",
    "分类讨论",
    "整体思想",
    "转化思想",
    "方程思想",
    "函数思想",
    "建模思想",
    "类比",
    "归纳",
})


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
    # 技能考频：基于 canonical 知识点 + 方法 + 模型 + 能力的加权标签重合度
    skill_frequency: float = 0.0
    skill_matched_count: int = 0
    skill_available: bool = False
    
    # 综合加权考频新属性
    weighted_frequency: float = 0.0
    global_similar_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_all_active_questions(conn) -> dict[int, dict[str, Any]]:
    # Get all active questions with their tags
    rows = conn.execute(
        """
        SELECT q.id FROM questions q
        LEFT JOIN papers p ON p.id = q.paper_id
        WHERE q.is_deleted = 0 AND COALESCE(p.import_status, '') <> 'deleted'
        """
    ).fetchall()
    qids = [int(r["id"]) for r in rows]
    return _batch_load_questions(conn, qids)


def _precompute_parents(questions: dict[int, dict[str, Any]]) -> dict[int, set[str]]:
    from question_bank.taxonomy.registry import get_parent_knowledge_category
    parents_by_qid = {}
    for qid, q in questions.items():
        q_grouped = _group_tags(q.get("tags", []))
        q_kps = _dedup_sorted([
            *_filtered(q_grouped.get("canonical_knowledge_id")),
            *_filtered(q_grouped.get("knowledge_point")),
        ])
        parents_by_qid[qid] = set(get_parent_knowledge_category(kp) for kp in q_kps if kp)
    return parents_by_qid


def _global_similar_match_count(
    target: Mapping[str, Any],
    all_questions: dict[int, dict[str, Any]],
    parents_by_qid: dict[int, set[str]],
) -> int:
    target_grouped = _group_tags(target.get("tags", []))
    target_kps = _dedup_sorted([
        *_filtered(target_grouped.get("canonical_knowledge_id")),
        *_filtered(target_grouped.get("knowledge_point")),
    ])
    from question_bank.taxonomy.registry import get_parent_knowledge_category
    target_parents = set(get_parent_knowledge_category(kp) for kp in target_kps if kp)
    if not target_parents:
        return 0
        
    global_matched = 0
    for qid, candidate in all_questions.items():
        # Coarse filter
        if not (parents_by_qid.get(qid, set()) & target_parents):
            continue
        # Fine calculate
        sim = calculate_question_similarity(target, candidate)
        if sim >= 0.55:
            global_matched += 1
    return global_matched


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
    
    weighted_val = getattr(metrics, "weighted_frequency", 0.0)
    
    if metrics.exam_type == "中考":
        summary = f"中考考频 {weighted_val:.1%} (综合加权)"
        if metrics.shenzhen_fit_available and metrics.shenzhen_fit_score > 0:
            summary += f" · 深圳适配 {metrics.shenzhen_fit_score:.0%}"
        return summary
    else:
        return f"{metrics.exam_type}考频 {weighted_val:.1%} (综合加权)"


# ---------------------------------------------------------------------------
# 技能考频：基于 canonical 知识点 + 方法 + 模型 + 能力的加权标签重合度
# ---------------------------------------------------------------------------

# 技能考频各维度权重（总和 = 1.0）
_SKILL_WEIGHT_KNOWLEDGE = 0.35
_SKILL_WEIGHT_METHOD = 0.25
_SKILL_WEIGHT_MODEL = 0.15
_SKILL_WEIGHT_ABILITY = 0.25

# 技能关联分阈值：低于此分数的候选题不计入"有重合的题数"
_SKILL_MATCH_THRESHOLD = 0.3


def _canonical_overlap(target_grouped: dict, candidate_grouped: dict) -> float:
    # If the first knowledge points are exactly equal, they match
    t_kps = target_grouped.get("knowledge_point") or []
    c_kps = candidate_grouped.get("knowledge_point") or []
    if t_kps and c_kps and t_kps[0] == c_kps[0]:
        return 1.0

    # canonical 知识点重合度：相同 canonical_id → 1.0；不同但父类相同 → 0.4；都不同 → 0.0。
    from question_bank.taxonomy.registry import canonicalize_knowledge, get_parent_knowledge_category

    target_canonical = _first_canonical(target_grouped)
    candidate_canonical = _first_canonical(candidate_grouped)

    # 双方都没有 canonical 时，fallback 到 knowledge_point 父类比对
    if not target_canonical and not candidate_canonical:
        t_kp = _first(target_grouped.get("knowledge_point"))
        c_kp = _first(candidate_grouped.get("knowledge_point"))
        if not t_kp or not c_kp:
            return 0.0
        if t_kp == c_kp:
            return 1.0
        t_parent = get_parent_knowledge_category(t_kp)
        c_parent = get_parent_knowledge_category(c_kp)
        return 1.0 if t_parent and t_parent == c_parent else 0.0

    if not target_canonical or not candidate_canonical:
        return 0.0

    # 同一个 canonical → 完全匹配
    if target_canonical == candidate_canonical:
        return 1.0

    # 不同 canonical → 检查父类是否相同（如"一元二次方程"和"二次函数"共享"二次函数"主题）
    t_parent = get_parent_knowledge_category(target_canonical.canonical_name)
    c_parent = get_parent_knowledge_category(candidate_canonical.canonical_name)
    return 0.4 if t_parent and t_parent == c_parent else 0.0


def _first_canonical(grouped: dict):
    # 从标签组中找到第一个能匹配到 canonical 表的知识点。
    from question_bank.taxonomy.registry import canonicalize_knowledge

    for value in grouped.get("canonical_knowledge_id") or []:
        compacted = _compact(value)
        if compacted:
            result = canonicalize_knowledge(compacted)
            if result is not None:
                return result
    for value in grouped.get("knowledge_point") or []:
        compacted = _compact(value)
        if compacted:
            result = canonicalize_knowledge(compacted)
            if result is not None:
                return result
    return None


def _jaccard(a: set[str], b: set[str]) -> float:
    # Jaccard 系数：交集 / 并集。双方都为空 → 1.0；一方空 → 0.0。
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _skill_overlap_score(
    target_grouped: dict[str, list[str]],
    candidate_grouped: dict[str, list[str]],
) -> float:
    # 四维度加权技能关联分（0~1）。
    knowledge_score = _canonical_overlap(target_grouped, candidate_grouped)

    t_methods = set(_filtered(target_grouped.get("method"), exclude=GENERIC_METHOD_TAGS))
    c_methods = set(_filtered(candidate_grouped.get("method"), exclude=GENERIC_METHOD_TAGS))
    method_score = _jaccard(t_methods, c_methods)

    t_models = set(_filtered(target_grouped.get("model")))
    c_models = set(_filtered(candidate_grouped.get("model")))
    model_score = _jaccard(t_models, c_models)

    t_ability = set(_filtered(target_grouped.get("ability")))
    c_ability = set(_filtered(candidate_grouped.get("ability")))
    ability_score = _jaccard(t_ability, c_ability)

    return round(
        knowledge_score * _SKILL_WEIGHT_KNOWLEDGE
        + method_score * _SKILL_WEIGHT_METHOD
        + model_score * _SKILL_WEIGHT_MODEL
        + ability_score * _SKILL_WEIGHT_ABILITY,
        4,
    )


def calculate_question_similarity(
    target: dict[str, Any] | Mapping[str, Any],
    candidate: dict[str, Any] | Mapping[str, Any],
) -> float:
    # 1. 难度判定 (Difficulty Penalty)
    t_diff = _number(target.get("difficulty"))
    c_diff = _number(candidate.get("difficulty"))
    if t_diff is not None and c_diff is not None:
        diff_diff = abs(t_diff - c_diff)
        if diff_diff >= 3:
            return 0.0
        elif diff_diff == 2:
            difficulty_penalty = 0.5
        else:
            difficulty_penalty = 1.0
    else:
        difficulty_penalty = 1.0

    # 2. 标签加权 Jaccard
    target_grouped = _group_tags(target.get("tags", []))
    candidate_grouped = _group_tags(candidate.get("tags", []))

    # A. 核心知识点相似度
    knowledge_score = _canonical_overlap(target_grouped, candidate_grouped)

    # B. 解题方法相似度
    t_methods = set(_filtered(target_grouped.get("method"), exclude=GENERIC_METHOD_TAGS))
    c_methods = set(_filtered(candidate_grouped.get("method"), exclude=GENERIC_METHOD_TAGS))
    method_score = _jaccard(t_methods, c_methods)

    # C. 数学模型相似度
    t_models = set(_filtered(target_grouped.get("model")))
    c_models = set(_filtered(candidate_grouped.get("model")))
    model_score = _jaccard(t_models, c_models)

    tag_sim = (
        knowledge_score * 0.5
        + method_score * 0.3
        + model_score * 0.2
    )

    return round(tag_sim * difficulty_penalty, 4)


def _skill_frequency_for_papers(
    conn,
    target_grouped: dict[str, list[str]],
    paper_ids: list[int],
    *,
    target_id: int,
) -> tuple[float, int]:
    # 批量计算 eligible 试卷中的技能考频。
    # 返回 (skill_frequency, matched_count)。
    if not paper_ids:
        return 0.0, 0
    candidates = _batch_load_questions(conn, _question_ids_in_papers(conn, paper_ids))
    total_score = 0.0
    matched_count = 0
    for qid, candidate in candidates.items():
        if qid == target_id:
            continue
        candidate_grouped = _group_tags(candidate.get("tags", []))
        score = _skill_overlap_score(target_grouped, candidate_grouped)
        if score >= _SKILL_MATCH_THRESHOLD:
            total_score += score
            matched_count += 1
    paper_count = len(paper_ids)
    return round(total_score / paper_count, 4) if paper_count else 0.0, matched_count


def _question_ids_in_papers(conn, paper_ids: list[int]) -> list[int]:
    # 获取指定试卷中的所有未删除题目 ID。
    if not paper_ids:
        return []
    placeholders = ", ".join("?" for _ in paper_ids)
    rows = conn.execute(
        f"SELECT id FROM questions WHERE paper_id IN ({placeholders}) AND is_deleted = 0",
        paper_ids,
    ).fetchall()
    return [int(row["id"]) for row in rows]


def build_question_fingerprint(question: dict[str, Any] | Mapping[str, Any]) -> str:
    grouped = _group_tags(question.get("tags", []))
    question_type = _normalize_question_type(question.get("question_type"))
    # 知识点：取所有标签去重排序后拼接（消除标签顺序敏感），避免多知识点题漏配。
    knowledge_tags = _dedup_sorted([
        *_filtered(grouped.get("canonical_knowledge_id")),
        *_filtered(grouped.get("knowledge_point")),
    ])
    if not question_type or not knowledge_tags:
        return ""

    from question_bank.taxonomy.registry import get_parent_knowledge_category
    parent_categories = sorted(list(set(
        get_parent_knowledge_category(tag) for tag in knowledge_tags
    )))
    parent_categories = [p for p in parent_categories if p]
    if not parent_categories:
        return ""
    knowledge = "+".join(parent_categories)

    parts = [question_type, knowledge]

    # Combine image and context/exploration style features
    style = _style_features(question)
    has_img = "有图" if style.get("has_images") else "无图"
    parts.append(has_img)

    is_ctx = "情境/探究" if (style.get("is_contextual") or style.get("is_exploratory")) else "普通"
    parts.append(is_ctx)

    # 方法/模型：取所有标签、按集合比对；排除"数形结合/分类讨论"等几乎覆盖所有
    # 综合题的宽泛标签，避免几何综合题过度聚拢到同一指纹桶。
    method_tags = _filtered(grouped.get("method"), exclude=GENERIC_METHOD_TAGS)
    model_tags = _filtered(grouped.get("model"))
    methods = _dedup_sorted([*method_tags, *model_tags])
    if methods:
        parts.append("+".join(methods))

    # 难度：基础/偏基础/偏综合/拔高 四档，避免中档(4-7)把难度4的常规题和难度7的
    # 综合题混进同一桶——这是"难题排在考频前面"的主要成因。
    diff = style.get("difficulty")
    if diff is not None:
        if diff <= 3:
            diff_lvl = "基础"
        elif diff <= 5:
            diff_lvl = "偏基础"
        elif diff <= 7:
            diff_lvl = "偏综合"
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
            deduped = list(dict.fromkeys(int(value) for value in question_ids))
            # 批量加载目标题目，避免逐题 _load_question 的 2N 次查询。
            targets = _batch_load_questions(conn, deduped)
            
            # 批量加载全局题目以计算全局相似度考频，避免N+1次扫描数据库
            all_active = _load_all_active_questions(conn)
            parents_by_qid = _precompute_parents(all_active)
            
            eligible_cache: dict[tuple, list[int]] = {}
            match_cache: dict[tuple, int] = {}
            for question_id in deduped:
                target = targets.get(question_id)
                if target is None:
                    result[question_id] = FrequencyMetrics(available=False)
                else:
                    result[question_id] = _metrics_for_target_cached(
                        conn,
                        target,
                        eligible_cache=eligible_cache,
                        match_cache=match_cache,
                        all_active=all_active,
                        parents_by_qid=parents_by_qid,
                    )
            return result

    def metrics_for_question(self, question_id: int) -> FrequencyMetrics:
        self.initialize_database()
        with connect(self.db_path) as conn:
            target = _load_question(conn, int(question_id))
            if target is None:
                return FrequencyMetrics(available=False)
            all_active = _load_all_active_questions(conn)
            parents_by_qid = _precompute_parents(all_active)
            return _metrics_for_target(conn, target, all_active=all_active, parents_by_qid=parents_by_qid)

    def update_frequency_cache_for_all(self) -> None:
        self.initialize_database()
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id FROM questions WHERE is_deleted = 0"
            ).fetchall()
            qids = [int(r["id"]) for r in rows]
            self._update_frequency_cache_internal(conn, qids)

    def _update_frequency_cache_internal(self, conn, qids: list[int]) -> None:
        if not qids:
            return
        targets = _batch_load_questions(conn, qids)
        
        all_active = _load_all_active_questions(conn)
        parents_by_qid = _precompute_parents(all_active)
        
        candidates_cache: dict[tuple[int, ...], dict[int, dict[str, Any]]] = {}
        for qid, target in targets.items():
            global_matched = _global_similar_match_count(target, all_active, parents_by_qid)
            s_global = min(1.0, global_matched / 50.0)
            
            # 1. 期中
            midterm_papers = _eligible_paper_ids(conn, target, exam_type="期中")
            midterm_matched = _matching_count_by_similarity(conn, target, midterm_papers, cache=candidates_cache)
            p_midterm = min(1.0, midterm_matched / len(midterm_papers)) if midterm_papers else 0.0
            score_midterm = p_midterm * 0.6 + s_global * 0.4

            # 2. 期末
            final_papers = _eligible_paper_ids(conn, target, exam_type="期末")
            final_matched = _matching_count_by_similarity(conn, target, final_papers, cache=candidates_cache)
            p_final = min(1.0, final_matched / len(final_papers)) if final_papers else 0.0
            score_final = p_final * 0.6 + s_global * 0.4

            # 3. 中考
            zhongkao_papers = _eligible_paper_ids(conn, target, exam_type="中考")
            zhongkao_matched = _matching_count_by_similarity(conn, target, zhongkao_papers, cache=candidates_cache)
            p_zhongkao = min(1.0, zhongkao_matched / len(zhongkao_papers)) if zhongkao_papers else 0.0
            score_zhongkao = p_zhongkao * 0.6 + s_global * 0.4

            conn.execute(
                """
                INSERT INTO question_frequency_cache (question_id, score_midterm, score_final, score_zhongkao, updated_at)
                VALUES (?, ?, ?, ?, datetime('now', 'localtime'))
                ON CONFLICT(question_id) DO UPDATE SET
                    score_midterm = excluded.score_midterm,
                    score_final = excluded.score_final,
                    score_zhongkao = excluded.score_zhongkao,
                    updated_at = excluded.updated_at
                """,
                (qid, score_midterm, score_final, score_zhongkao)
            )
        conn.commit()
        conn.commit()

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
            for row in rows:
                qid = int(row["id"])
                target = _load_question(conn, qid)
                if target is not None:
                    fingerprint = build_question_fingerprint(target)
                    if fingerprint:
                        _cache_fingerprint(conn, qid, fingerprint, _style_features(target))
            
            # Backfill missing cache in frequency cache table
            missing_cache = conn.execute(
                """
                SELECT q.id
                FROM questions q
                LEFT JOIN question_frequency_cache qfc ON qfc.question_id = q.id
                WHERE q.is_deleted = 0 AND qfc.question_id IS NULL
                """
            ).fetchall()
            if missing_cache:
                qids = [int(r["id"]) for r in missing_cache]
                self._update_frequency_cache_internal(conn, qids)

    def invalidate_frequency_cache_for_question(self, question_id: int) -> None:
        self.initialize_database()
        with connect(self.db_path) as conn:
            target = _load_question(conn, question_id)
            if target is None:
                return
            exam_type = normalize_exam_type(target.get("exam_type"))
            paper_ids = _eligible_paper_ids(conn, target, exam_type=exam_type)
            if paper_ids:
                placeholders = ", ".join("?" for _ in paper_ids)
                conn.execute(
                    f"""
                    DELETE FROM question_frequency_cache
                    WHERE question_id IN (
                        SELECT id FROM questions WHERE paper_id IN ({placeholders})
                    )
                    """,
                    paper_ids
                )
            conn.execute("DELETE FROM question_frequency_cache WHERE question_id = ?", (question_id,))
            conn.commit()


def _matching_count_by_similarity(conn, target: Mapping[str, Any], paper_ids: list[int], cache: dict | None = None) -> int:
    if not paper_ids:
        return 0
    key = tuple(sorted(paper_ids))
    candidates = None
    if cache is not None:
        candidates = cache.get(key)
    if candidates is None:
        candidate_ids = _question_ids_in_papers(conn, paper_ids)
        candidates = _batch_load_questions(conn, candidate_ids)
        if cache is not None:
            cache[key] = candidates
            
    matched_count = 0
    for qid, candidate in candidates.items():
        sim = calculate_question_similarity(target, candidate)
        if sim >= 0.55:
            matched_count += 1
    return matched_count


def _bayesian_frequency_score(matched_count: int, paper_count: int) -> float:
    K = 5
    C = 0.05
    return (matched_count + K * C) / (paper_count + K)


def _metrics_for_target(
    conn,
    target: Mapping[str, Any],
    *,
    all_active: dict[int, dict[str, Any]] | None = None,
    parents_by_qid: dict[int, set[str]] | None = None,
) -> FrequencyMetrics:
    question_id = int(target["id"])
    exam_type = normalize_exam_type(target.get("exam_type"))
    fingerprint = build_question_fingerprint(target)
    if not exam_type or not fingerprint:
        return FrequencyMetrics(available=False, exam_type=exam_type, fingerprint=fingerprint)
    _cache_fingerprint(conn, question_id, fingerprint, _style_features(target))
    
    if all_active is None:
        all_active = _load_all_active_questions(conn)
    if parents_by_qid is None:
        parents_by_qid = _precompute_parents(all_active)
        
    global_matched = _global_similar_match_count(target, all_active, parents_by_qid)
    s_global = min(1.0, global_matched / 50.0)
    
    eligible_papers = _eligible_paper_ids(conn, target, exam_type=exam_type)
    matched = _matching_count_by_similarity(conn, target, eligible_papers)
    p_local = min(1.0, matched / len(eligible_papers)) if eligible_papers else 0.0
    weighted_freq = p_local * 0.6 + s_global * 0.4

    if exam_type != "中考":
        count = len(eligible_papers)
        return FrequencyMetrics(
            available=True,
            exam_type=exam_type,
            fingerprint=fingerprint,
            matched_question_count=matched,
            eligible_paper_count=count,
            questions_per_paper=_ratio(matched, count),
            confidence_label=_confidence_label(count),
            weighted_frequency=weighted_freq,
            global_similar_count=global_matched,
            skill_frequency=weighted_freq,
            skill_matched_count=global_matched,
            skill_available=True,
        )
    shenzhen_papers = _eligible_paper_ids(conn, target, exam_type=exam_type, city="深圳市")
    shenzhen_matched = _matching_count_by_similarity(conn, target, shenzhen_papers)
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
        available=True,
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
        weighted_frequency=weighted_freq,
        global_similar_count=global_matched,
        skill_frequency=weighted_freq,
        skill_matched_count=global_matched,
        skill_available=True,
    )


def _batch_load_questions(conn, question_ids: list[int]) -> dict[int, dict[str, Any]]:
    # 批量加载多道题目及其标签，用 2 次 SQL 替代逐题 _load_question 的 2N 次查询。
    if not question_ids:
        return {}
    placeholders = ", ".join("?" for _ in question_ids)
    rows = conn.execute(
        f"""
        SELECT q.*, p.title AS paper_title, p.year, p.province, p.city, p.district,
               p.exam_type, p.grade, p.semester
        FROM questions q
        LEFT JOIN papers p ON p.id = q.paper_id
        WHERE q.id IN ({placeholders}) AND q.is_deleted = 0
          AND COALESCE(p.import_status, '') <> 'deleted'
        """,
        question_ids,
    ).fetchall()
    tag_rows = conn.execute(
        f"""
        SELECT question_id, tag_type, tag_value
        FROM question_tags
        WHERE question_id IN ({placeholders})
        ORDER BY id
        """,
        question_ids,
    ).fetchall()
    tags_by_q: dict[int, list[dict[str, Any]]] = {}
    for tag in tag_rows:
        tags_by_q.setdefault(int(tag["question_id"]), []).append(
            {"tag_type": tag["tag_type"], "tag_value": tag["tag_value"]}
        )
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        item["tags"] = tags_by_q.get(int(item["id"]), [])
        result[int(item["id"])] = item
    return result


def _metrics_for_target_cached(
    conn,
    target: Mapping[str, Any],
    *,
    eligible_cache: dict[tuple, list[int]],
    match_cache: dict[tuple, int],
    all_active: dict[int, dict[str, Any]] | None = None,
    parents_by_qid: dict[int, set[str]] | None = None,
) -> FrequencyMetrics:
    # 与 _metrics_for_target 行为完全一致，但复用 eligible_cache / match_cache，
    # 避免在批量计算时对同一组试卷或同一指纹重复查询。
    question_id = int(target["id"])
    exam_type = normalize_exam_type(target.get("exam_type"))
    fingerprint = build_question_fingerprint(target)
    if not exam_type or not fingerprint:
        return FrequencyMetrics(available=False, exam_type=exam_type, fingerprint=fingerprint)
    _cache_fingerprint(conn, question_id, fingerprint, _style_features(target))

    if all_active is None:
        all_active = _load_all_active_questions(conn)
    if parents_by_qid is None:
        parents_by_qid = _precompute_parents(all_active)

    global_matched = _global_similar_match_count(target, all_active, parents_by_qid)
    s_global = min(1.0, global_matched / 50.0)

    eligible_key = (target.get("grade"), target.get("semester"), exam_type, None)
    eligible_papers = eligible_cache.get(eligible_key)
    if eligible_papers is None:
        eligible_papers = _eligible_paper_ids(conn, target, exam_type=exam_type)
        eligible_cache[eligible_key] = eligible_papers

    match_key = (question_id, tuple(eligible_papers))
    matched = match_cache.get(match_key)
    if matched is None:
        matched = _matching_count_by_similarity(conn, target, eligible_papers)
        match_cache[match_key] = matched

    p_local = min(1.0, matched / len(eligible_papers)) if eligible_papers else 0.0
    weighted_freq = p_local * 0.6 + s_global * 0.4

    if exam_type != "中考":
        count = len(eligible_papers)
        return FrequencyMetrics(
            available=True,
            exam_type=exam_type,
            fingerprint=fingerprint,
            matched_question_count=matched,
            eligible_paper_count=count,
            questions_per_paper=_ratio(matched, count),
            confidence_label=_confidence_label(count),
            weighted_frequency=weighted_freq,
            global_similar_count=global_matched,
            skill_frequency=weighted_freq,
            skill_matched_count=global_matched,
            skill_available=True,
        )

    shenzhen_key = (target.get("grade"), target.get("semester"), exam_type, "深圳市")
    shenzhen_papers = eligible_cache.get(shenzhen_key)
    if shenzhen_papers is None:
        shenzhen_papers = _eligible_paper_ids(conn, target, exam_type=exam_type, city="深圳市")
        eligible_cache[shenzhen_key] = shenzhen_papers

    shenzhen_match_key = (question_id, tuple(shenzhen_papers))
    shenzhen_matched = match_cache.get(shenzhen_match_key)
    if shenzhen_matched is None:
        shenzhen_matched = _matching_count_by_similarity(conn, target, shenzhen_papers)
        match_cache[shenzhen_match_key] = shenzhen_matched

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
        available=True,
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
        weighted_frequency=weighted_freq,
        global_similar_count=global_matched,
        skill_frequency=weighted_freq,
        skill_matched_count=global_matched,
        skill_available=True,
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


def _ensure_fingerprints_for_papers(conn, paper_ids: list[int]) -> None:
    # _matching_question_count 与 _shenzhen_fit 都改读 question_fingerprints 缓存表，
    # 因此在统计前必须保证给定试卷中的所有题目都拥有当前版本的指纹。
    # 这里只补齐缺失/过期项，缓存命中时仅一次轻量查询即返回。
    if not paper_ids:
        return
    placeholders = ", ".join("?" for _ in paper_ids)
    stale = conn.execute(
        f"""
        SELECT q.id
        FROM questions q
        LEFT JOIN question_fingerprints qf
          ON qf.question_id = q.id AND qf.fingerprint_version = ?
        WHERE q.paper_id IN ({placeholders})
          AND q.is_deleted = 0
          AND qf.question_id IS NULL
        """,
        (FINGERPRINT_VERSION, *paper_ids),
    ).fetchall()
    if not stale:
        return
    stale_ids = [int(row["id"]) for row in stale]
    for target in _batch_load_questions(conn, stale_ids).values():
        fingerprint = build_question_fingerprint(target)
        if fingerprint:
            _cache_fingerprint(conn, int(target["id"]), fingerprint, _style_features(target))


def _matching_question_count(conn, paper_ids: list[int], fingerprint: str) -> int:
    # 直接复用 question_fingerprints 缓存表，避免逐题加载 + 重建指纹的 N+1 查询。
    # 调用方需先调用 _ensure_fingerprints_for_papers 保证缓存完整。
    if not paper_ids:
        return 0
    placeholders = ", ".join("?" for _ in paper_ids)
    row = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM question_fingerprints qf
        JOIN questions q ON q.id = qf.question_id
        WHERE qf.fingerprint_version = ?
          AND qf.base_fingerprint = ?
          AND q.paper_id IN ({placeholders})
          AND q.is_deleted = 0
        """,
        (FINGERPRINT_VERSION, fingerprint, *paper_ids),
    ).fetchone()
    return int(row[0]) if row else 0


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
    
    candidate_ids = [int(row["id"]) for row in rows]
    candidates = _batch_load_questions(conn, candidate_ids)
    
    for qid, candidate in candidates.items():
        if calculate_question_similarity(target, candidate) < 0.55:
            continue
        score, notes = _style_fit_score(
            target_features,
            target_tags,
            _style_features(candidate),
            _group_tags(candidate.get("tags", [])),
            shenzhen_frequency=shenzhen_frequency,
        )
        scored.append((score, qid, notes))
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


def _filtered(values: list[str] | None, *, exclude: frozenset[str] | None = None) -> list[str]:
    # 清洗标签列表：去空白、去空、可选排除宽泛标签。保持原顺序。
    result: list[str] = []
    for value in values or []:
        compacted = _compact(value)
        if not compacted or compacted in (exclude or frozenset()):
            continue
        if compacted not in result:
            result.append(compacted)
    return result


def _dedup_sorted(values: list[str]) -> list[str]:
    # 去重后按字典序排序，使标签集合的拼接结果与标签顺序无关（消除顺序敏感）。
    return sorted(set(_compact(v) for v in values if _compact(v)))


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
