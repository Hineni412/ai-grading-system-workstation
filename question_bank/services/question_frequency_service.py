"""共享的标签相似度与考试类型归一辅助函数。

考频本身已改由章节考情口径计算（见 chapter_exam_profile.build_exam_frequency），
本模块只保留仍被引用的纯函数：experiment_similar_questions 用
calculate_question_similarity / canonical_knowledge_containment 做题间相似度，
chapter_exam_profile 用 normalize_exam_type 划分期中/期末卷。
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

FORMAL_EXAM_TYPES = ("期中", "期末", "中考")
PRACTICE_EXAM_MARKERS = (
    "同步练习",
    "专题练习",
    "练习",
    "作业",
    "小测",
    "小考",
    "测验",
)

# 极宽泛的历史方法/思想标签——几乎覆盖所有综合题，参与相似度会让大量几何/函数
# 综合题聚到同一个匹配桶里。这些标签从比对中排除，只保留具体方法
# （如配方法、待定系数法、面积法）来区分技能。
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


def _canonical_overlap(target_grouped: dict, candidate_grouped: dict) -> float:
    target_keys = set(
        _filtered(target_grouped.get("current_knowledge_key"))
    )
    candidate_keys = set(
        _filtered(candidate_grouped.get("current_knowledge_key"))
    )
    return _jaccard(target_keys, candidate_keys)


def canonical_knowledge_containment(
    target_grouped: dict,
    candidate_grouped: dict,
) -> float:
    # 重叠度：一方知识键被另一方完全覆盖即满分。相似题推荐需要
    # "同点更简/更繁的变式"，Jaccard 会把标签更细的题压到阈值以下。
    target_keys = set(
        _filtered(target_grouped.get("current_knowledge_key"))
    )
    candidate_keys = set(
        _filtered(candidate_grouped.get("current_knowledge_key"))
    )
    if not target_keys or not candidate_keys:
        return 0.0
    return len(target_keys & candidate_keys) / min(len(target_keys), len(candidate_keys))


def _jaccard(a: set[str], b: set[str]) -> float:
    # 缺失标签不是相似证据；双方都空时也必须返回 0。
    if not a and not b:
        return 0.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def calculate_question_similarity(
    target: dict[str, Any] | Mapping[str, Any],
    candidate: dict[str, Any] | Mapping[str, Any],
    *,
    knowledge_overlap=None,
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
    knowledge_score = (knowledge_overlap or _canonical_overlap)(
        target_grouped, candidate_grouped
    )

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


def _filtered(values: Iterable[str] | None, *, exclude: frozenset[str] | None = None) -> list[str]:
    # 清洗标签列表：去空白、去空、可选排除宽泛标签。保持原顺序。
    result: list[str] = []
    for value in values or []:
        compacted = _compact(value)
        if not compacted or compacted in (exclude or frozenset()):
            continue
        if compacted not in result:
            result.append(compacted)
    return result


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _number(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
