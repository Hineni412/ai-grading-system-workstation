from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from question_bank.knowledge_graph_release.contracts import (
    SCHEMA_VERSION,
    compute_content_hash,
    stable_record_hash,
)
from question_bank.knowledge_graph_release.validation import validate_release
from question_bank.knowledge_graph_release.contracts import KnowledgeGraphRelease
from tools.build_release_v3 import _render_json


TAXONOMY_PATH = (
    ROOT / "question_bank" / "taxonomy" / "catalogs" / "tag_vocabulary_v2.json"
)
OUTPUT_PATH = (
    ROOT
    / "question_bank"
    / "taxonomy"
    / "catalogs"
    / "knowledge_graph_release_v1.json"
)


def _local_key(slug: str) -> str:
    digest = hashlib.md5(
        f"junior-math-knowledge-graph-v1:{slug}".encode("utf-8")
    ).hexdigest()
    return f"ki_{digest}"


NEW_CORE_NODES = {
    _local_key("rational-numbers"): "有理数",
    _local_key("equation-foundations"): "方程基础",
    _local_key("linear-inequality"): "一元一次不等式",
    _local_key("scientific-notation"): "科学记数法",
    _local_key("polygons"): "多边形",
    _local_key("trapezoid"): "梯形",
    _local_key("solid-geometry-foundations"): "立体图形初步",
}

RATIONAL_CORE = next(key for key, name in NEW_CORE_NODES.items() if name == "有理数")
EQUATION_CORE = next(key for key, name in NEW_CORE_NODES.items() if name == "方程基础")
INEQUALITY_CORE = next(
    key for key, name in NEW_CORE_NODES.items() if name == "一元一次不等式"
)
SCIENTIFIC_NOTATION_CORE = next(
    key for key, name in NEW_CORE_NODES.items() if name == "科学记数法"
)
POLYGON_CORE = next(key for key, name in NEW_CORE_NODES.items() if name == "多边形")
TRAPEZOID_CORE = next(key for key, name in NEW_CORE_NODES.items() if name == "梯形")
SOLID_CORE = next(
    key for key, name in NEW_CORE_NODES.items() if name == "立体图形初步"
)


RETIRED_CORE_REPLACEMENTS: dict[str, tuple[str, ...]] = {
    "kp_fun_comprehensive": ("kp_fun_variables", "kp_fun_function_graph"),
    "kp_geo_comprehensive": (
        "kp_geo_line_angle",
        "kp_geo_triangle_properties",
        "kp_geo_quadrilateral",
        "kp_geo_circle_properties",
    ),
    "kp_geo_circle_tangent_appl": (
        "kp_geo_circle_tangent",
        "kp_geo_circle_properties",
    ),
    "kp_fun_dynamic_point": ("kp_fun_variables", "kp_geo_transformation"),
    "kp_syn_modeling": ("kp_fun_variables", "kp_data_analysis"),
    "kp_syn_reading": ("kp_geo_proposition_proof",),
    "kp_geo_quadrilateral_properties": (
        "kp_geo_parallelogram",
        "kp_geo_rectangle",
        "kp_geo_rhombus",
        "kp_geo_square",
    ),
}

RETRIEVAL_ONLY_NAMES = {
    "三元一次方程组",
    "二项方程",
    "四分位数",
    "数学常识",
    "无理方程",
    "离差平方和",
    "代数与函数综合",
    "几何综合",
}

WRONG_DIMENSION_NAMES = {
    "动点与综合函数题",
    "归纳与类比",
    "数学建模与应用",
    "推理与论证",
    "猜想与证明",
    "观察与实验",
    "阅读理解与规律探究",
}

MANUAL_TARGETS: dict[str, tuple[str, ...]] = {
    "一次函数与一元一次不等式": ("kp_fun_linear", INEQUALITY_CORE),
    "一次函数与一元一次方程": ("kp_fun_linear", "kp_alg_linear_equation"),
    "一次函数与二元一次方程（组）": ("kp_fun_linear", "kp_alg_equation_system"),
    "不等式与不等式组": (INEQUALITY_CORE, "kp_alg_inequality_system"),
    "坐标与图形变化——平移": ("kp_fun_coordinate_system", "kp_geo_transformation"),
    "坐标与图形变化——轴对称": ("kp_fun_coordinate_system", "kp_geo_axis_symmetry"),
    "坐标与图形变换——旋转": ("kp_fun_coordinate_system", "kp_geo_transformation"),
    "坐标系与位似图形": ("kp_fun_coordinate_system", "kp_geo_similarity"),
    "用方向角和距离确定物体的位置": (
        "kp_fun_coordinate_system",
        "kp_geo_line_angle",
    ),
    "平行线分线段成比例定理": ("kp_geo_similarity", "kp_geo_parallel_lines"),
    "三角形的外接圆": ("kp_geo_circle_properties", "kp_geo_triangle_properties"),
    "图象法确定一元二次方程的近似根": (
        "kp_alg_quadratic_equation",
        "kp_fun_function_graph",
    ),
    "正多边形和圆": (POLYGON_CORE, "kp_geo_circle_properties"),
    "正多边形的中心角": (POLYGON_CORE, "kp_geo_circle_angle"),
    "点、直线、圆的位置关系": ("kp_geo_circle_tangent", "kp_geo_line_angle"),
    "特殊的平行四边形": (
        "kp_geo_parallelogram",
        "kp_geo_rectangle",
        "kp_geo_rhombus",
        "kp_geo_square",
    ),
}

MANUAL_SINGLE_TARGETS: dict[str, str] = {
    "一元一次方程的定义": "kp_alg_linear_equation",
    "一元二次方程根的判别式": "kp_alg_quadratic_equation",
    "一元二次方程的一般形式": "kp_alg_quadratic_equation",
    "一元二次方程的相关概念": "kp_alg_quadratic_equation",
    "一元二次方程的解": "kp_alg_quadratic_equation",
    "不等式": INEQUALITY_CORE,
    "二元一次方程组定义": "kp_alg_equation_system",
    "二元一次方程（组）的相关概念": "kp_alg_equation_system",
    "从算式到方程": EQUATION_CORE,
    "方程": EQUATION_CORE,
    "方程的解": EQUATION_CORE,
    "三角形": "kp_geo_triangle_properties",
    "三角形的认识": "kp_geo_triangle_properties",
    "三角形的三边关系": "kp_geo_triangle_properties",
    "三角形的中线": "kp_geo_triangle_properties",
    "三角形的稳定性": "kp_geo_triangle_properties",
    "三角形的角平分线": "kp_geo_triangle_properties",
    "三角形的重心": "kp_geo_triangle_properties",
    "三角形的高": "kp_geo_triangle_properties",
    "三角形的内角和定理": "kp_geo_angle_sum_theorem",
    "与三角形有关的线段": "kp_geo_triangle_properties",
    "与三角形有关的角": "kp_geo_triangle_properties",
    "多边形及其内角和": POLYGON_CORE,
    "多边形的内角和": POLYGON_CORE,
    "多边形的外角和": POLYGON_CORE,
    "多边形的对角线": POLYGON_CORE,
    "认识多边形": POLYGON_CORE,
    "几何体的展开图": SOLID_CORE,
    "几何图形初步": SOLID_CORE,
    "截一个几何体": SOLID_CORE,
    "点、线、面、体": SOLID_CORE,
    "立体图形": SOLID_CORE,
    "圆锥的定义及面积": "kp_geo_sector_cone",
    "扇形的定义及面积": "kp_geo_sector_cone",
    "平面图形的认识": "kp_geo_line_angle",
    "有理数加减混合运算": RATIONAL_CORE,
    "有理数比较大小": RATIONAL_CORE,
    "有理数的乘方": RATIONAL_CORE,
    "有理数的乘法法则": RATIONAL_CORE,
    "有理数的乘除": RATIONAL_CORE,
    "有理数的减法法则": RATIONAL_CORE,
    "有理数的初步认识": RATIONAL_CORE,
    "有理数的加减": RATIONAL_CORE,
    "有理数的加法法则": RATIONAL_CORE,
    "有理数的混合运算法则": RATIONAL_CORE,
    "有理数的运算": RATIONAL_CORE,
    "有理数的除法法则": RATIONAL_CORE,
    "正数和负数": RATIONAL_CORE,
    "数轴": RATIONAL_CORE,
    "科学记数法—表示较大的数": SCIENTIFIC_NOTATION_CORE,
    "科学记数法—表示较小的数": SCIENTIFIC_NOTATION_CORE,
    "余角和补角": "kp_geo_line_angle",
    "垂线": "kp_geo_line_angle",
    "方向角": "kp_geo_line_angle",
    "直线、射线、线段": "kp_geo_line_angle",
    "直线、射线、线段的定义": "kp_geo_line_angle",
    "线段的中点": "kp_geo_line_angle",
    "角平分线的性质与判定": "kp_geo_line_angle",
    "角的度量": "kp_geo_line_angle",
    "角的概念": "kp_geo_line_angle",
    "角的运算": "kp_geo_line_angle",
    "乘法公式": "kp_alg_polynomial",
    "完全平方式": "kp_alg_polynomial",
    "单项式乘单项式": "kp_alg_polynomial",
    "单项式乘多项式": "kp_alg_polynomial",
    "单项式除以单项式": "kp_alg_polynomial",
    "多项式乘多项式": "kp_alg_polynomial",
    "整式": "kp_alg_polynomial",
    "整式的加减及运用": "kp_alg_polynomial",
    "实数范围内分解因式": "kp_alg_factorization",
    "分式方程的定义": "kp_alg_fraction_equation",
    "分式方程的应用": "kp_alg_fraction_equation",
    "分式方程的解": "kp_alg_fraction_equation",
    "函数基础知识": "kp_fun_variables",
    "函数的概念": "kp_fun_variables",
    "函数解析式": "kp_fun_variables",
    "坐标方法的简单应用": "kp_fun_coordinate_system",
    "实际问题与二次函数": "kp_fun_quadratic_appl",
    "二次函数与不等式": "kp_fun_quadratic_relation",
    "平行四边形的判定": "kp_geo_parallelogram",
    "平行四边形的性质": "kp_geo_parallelogram",
    "梯形": TRAPEZOID_CORE,
    "图案设计": "kp_geo_transformation",
    "圆的基本认识": "kp_geo_circle_properties",
    "确定圆的条件": "kp_geo_circle_properties",
    "点和圆的位置关系": "kp_geo_circle_tangent",
    "直线和圆的位置关系": "kp_geo_circle_tangent",
    "切线的判定定理": "kp_geo_circle_tangent",
    "垂径定理的实际应用": "kp_geo_circle_angle",
    "垂径定理的推论": "kp_geo_circle_angle",
    "全面调查与抽样调查": "kp_sta_survey",
    "统计调查": "kp_sta_data_collection",
    "算术平均数": "kp_sta_data_representative",
    "数据的波动程度": "kp_sta_data_dispersion",
    "统计量的选择": "kp_data_analysis",
    "直方图": "kp_sta_data_display",
    "可能性的大小": "kp_probability",
    "概率的意义": "kp_probability",
    "随机事件与概率": "kp_probability",
    "等可能事件": "kp_probability",
    "用列举法求概率": "kp_probability_calc",
}


DOMAIN_ROOTS = {
    "数与式": "第四学段/数与代数/数与式",
    "方程与不等式": "第四学段/数与代数/方程与不等式",
    "函数": "第四学段/数与代数/函数",
    "图形的性质": "第四学段/图形与几何/图形的性质",
    "图形的变化": "第四学段/图形与几何/图形的变化",
    "统计与概率": "第四学段/统计与概率",
    "观察、猜想与证明": "第四学段/综合与实践/数学活动",
}


ANCHOR_OVERRIDES_BY_ID: dict[str, tuple[str, ...]] = {
    "kp_alg_number_sense": (
        "第四学段/数与代数/数与式/数感与估算",
    ),
    "kp_geo_construction": (
        "第四学段/图形与几何/图形的性质/尺规作图",
    ),
    "kp_geo_sector_cone": (
        "第四学段/图形与几何/图形的性质/圆/弧长和扇形面积",
        "第四学段/图形与几何/图形的认识/圆锥侧面展开",
    ),
    "kp_geo_similar_appl": (
        "第四学段/图形与几何/图形的变化/图形的相似/相似三角形应用",
    ),
    "kp_geo_triangle_area": (
        "第四学段/图形与几何/图形的性质/三角形/三角形面积",
    ),
    "kp_geo_triangle_properties": (
        "第四学段/图形与几何/图形的性质/三角形",
    ),
    "kp_geo_trigonometry": (
        "第四学段/图形与几何/图形的性质/直角三角形/锐角三角函数",
        "第四学段/图形与几何/图形的性质/直角三角形/解直角三角形",
    ),
    "kp_sta_chart_reading": (
        "第四学段/统计与概率/数据的收集与整理/统计图表",
    ),
    "kp_sta_data_display": (
        "第四学段/统计与概率/数据的收集与整理/数据的表示",
    ),
}

CORE_ANCHOR_OVERRIDES: dict[str, tuple[str, ...]] = {
    SCIENTIFIC_NOTATION_CORE: (
        "第四学段/数与代数/数与式/科学记数法",
    ),
}


@dataclass(frozen=True, slots=True)
class Decision:
    disposition: str
    targets: tuple[str, ...]
    confidence: float
    review_priority: str
    rationale: str


def build_release(catalog: Mapping[str, Any]) -> dict[str, Any]:
    terms = [
        term
        for term in catalog["terms"]
        if term.get("dimension") == "knowledge" and term.get("status") == "approved"
    ]
    core_terms = {term["id"]: term for term in terms if str(term["id"]).startswith("kp_")}
    active_core_terms = {
        key: term
        for key, term in core_terms.items()
        if key not in RETIRED_CORE_REPLACEMENTS
    }
    active_core_names = {
        **{key: term["name"] for key, term in active_core_terms.items()},
        **NEW_CORE_NODES,
    }
    sources = _sources()
    decisions = {
        term["id"]: _decide(term, active_core_terms)
        for term in terms
    }
    anchors_by_core: dict[str, set[str]] = {
        key: set() for key in active_core_names
    }
    for term in terms:
        anchors = set(_curriculum_anchors(term))
        for target in decisions[term["id"]].targets:
            anchors_by_core.setdefault(target, set()).update(anchors)

    core_nodes = []
    for key, term in core_terms.items():
        retired = key in RETIRED_CORE_REPLACEMENTS
        core_nodes.append(
            _core_node(
                stable_key=key,
                display_name=term["name"],
                aliases=term.get("aliases", []),
                anchors=(
                    _curriculum_anchors(term)
                    or sorted(anchors_by_core.get(key, ()))
                    or ["第四学段/跨领域或待核对"]
                ),
                status="retired" if retired else "active",
                node_kind="legacy" if retired else _node_kind(key),
            )
        )
    for key, name in NEW_CORE_NODES.items():
        core_nodes.append(
            _core_node(
                stable_key=key,
                display_name=name,
                aliases=(),
                anchors=(
                    CORE_ANCHOR_OVERRIDES.get(key)
                    or sorted(anchors_by_core.get(key, ()))
                    or ["第四学段/待核对"]
                ),
                status="active",
                node_kind="core",
            )
        )

    dispositions: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    for term in terms:
        decision = decisions[term["id"]]
        dispositions.append(_fine_term_disposition(term, decision))
        for index, target in enumerate(decision.targets):
            mappings.append(
                {
                    "fine_term_id": term["id"],
                    "stable_key": target,
                    "mapping_role": "primary" if index == 0 else "secondary",
                    "rationale": decision.rationale,
                }
            )

    replacements = [
        {
            "retired_key": retired_key,
            "replacement_key": replacement_key,
            "replacement_kind": (
                "split" if len(replacement_keys) > 1 else "broader"
            ),
            "rationale": "旧身份混合了多个知识主题；保留历史身份并显式连接新版核心。",
        }
        for retired_key, replacement_keys in RETIRED_CORE_REPLACEMENTS.items()
        for replacement_key in replacement_keys
    ]
    relations = _relations(active_core_names)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "release_id": "kgr_junior_math_2026_08_v1",
        "taxonomy_revision": int(catalog["revision"]),
        "predecessor_release_id": None,
        "sources": sources,
        "core_nodes": sorted(core_nodes, key=lambda item: item["stable_key"]),
        "fine_term_dispositions": dispositions,
        "mappings": sorted(
            mappings,
            key=lambda item: (
                item["fine_term_id"],
                0 if item["mapping_role"] == "primary" else 1,
                item["stable_key"],
            ),
        ),
        "relations": relations,
        "replacements": sorted(
            replacements,
            key=lambda item: (item["retired_key"], item["replacement_key"]),
        ),
    }
    payload["content_hash"] = compute_content_hash(payload)
    release = KnowledgeGraphRelease.from_mapping(payload)
    report = validate_release(release, catalog)
    report.raise_for_errors()
    return release.to_dict()


def _decide(
    term: Mapping[str, Any],
    active_core_terms: Mapping[str, Mapping[str, Any]],
) -> Decision:
    term_id = str(term["id"])
    name = str(term["name"])
    if name in RETRIEVAL_ONLY_NAMES:
        return Decision(
            "retrieval_only",
            (),
            0.9,
            "high_impact",
            "该词是扩展内容或过宽综合标签，只保留检索价值。",
        )
    if name in WRONG_DIMENSION_NAMES:
        return Decision(
            "wrong_dimension",
            (),
            0.95,
            "high_impact",
            "该词主要描述思想、能力、模型或题型，不作为知识掌握节点。",
        )
    if name in MANUAL_TARGETS:
        return Decision(
            "maps_to_many",
            MANUAL_TARGETS[name],
            0.95,
            "high_impact",
            "该精细词明确联合考查多个可独立评价的核心知识。",
        )
    if term_id in RETIRED_CORE_REPLACEMENTS:
        targets = RETIRED_CORE_REPLACEMENTS[term_id]
        return Decision(
            "maps_to_many" if len(targets) > 1 else "maps_to_core",
            targets,
            0.95,
            "high_impact",
            "旧核心身份已退役，精细词按显式替代关系连接新版核心。",
        )
    if term_id in active_core_terms:
        return Decision(
            "direct_core",
            (term_id,),
            1.0,
            "normal",
            "现有稳定身份与该规范词语义一致，继续作为核心节点。",
        )
    if name in MANUAL_SINGLE_TARGETS:
        return Decision(
            "maps_to_core",
            (MANUAL_SINGLE_TARGETS[name],),
            0.98,
            "normal",
            "根据数学对象和课标主题归入明确的稳定核心节点。",
        )

    ranked = sorted(
        (
            (_core_score(term, core), core_id)
            for core_id, core in active_core_terms.items()
        ),
        key=lambda item: (-item[0], item[1]),
    )
    best_score, best_id = ranked[0]
    second_score = ranked[1][0]
    confidence = min(0.94, 0.68 + max(0, best_score - second_score) / 1000)
    priority = "high_impact" if best_score < 140 or best_score - second_score < 30 else "normal"
    return Decision(
        "maps_to_core",
        (best_id,),
        round(confidence, 3),
        priority,
        "按规范名、已登记别名和来源主题匹配到最接近的稳定核心；低区分项进入关键分歧清单。",
    )


def _core_score(term: Mapping[str, Any], core: Mapping[str, Any]) -> int:
    name = _normalize(term.get("name"))
    path_tokens = {
        _normalize(token)
        for path in term.get("source_paths", [])
        for token in path
        if _normalize(token)
    }
    joined_paths = "".join(sorted(path_tokens))
    core_name = _normalize(core.get("name"))
    score = 0
    if core_name and core_name in name:
        score += 700
    if name and name in core_name:
        score += 260
    if core_name in path_tokens:
        score += 350
    for alias in core.get("aliases", []):
        normalized = _normalize(alias)
        if len(normalized) < 2:
            continue
        if normalized in name:
            score += 180
        elif normalized in joined_paths:
            score += 45
    core_paths = {
        _normalize(token)
        for path in core.get("source_paths", [])
        for token in path
        if _normalize(token)
    }
    score += 35 * len(path_tokens & core_paths)
    term_root = _root(term)
    core_roots = {_normalize(path[0]) for path in core.get("source_paths", []) if path}
    if term_root and term_root in core_roots:
        score += 80
    return score


def _core_node(
    *,
    stable_key: str,
    display_name: str,
    aliases: Sequence[str],
    anchors: Sequence[str],
    status: str,
    node_kind: str,
) -> dict[str, Any]:
    examples = "、".join(str(value) for value in aliases[:4] if str(value).strip())
    include_tail = f"，包括{examples}" if examples else ""
    return {
        "stable_key": stable_key,
        "display_name": display_name,
        "aliases": list(dict.fromkeys(str(value).strip() for value in aliases if str(value).strip())),
        "node_kind": node_kind,
        "status": status,
        "definition": f"以“{display_name}”的概念、性质、规则和直接应用为稳定聚合对象。",
        "include_scope": f"题目或证据点直接要求识别、说明、计算、证明或应用{display_name}{include_tail}。",
        "exclude_scope": "仅作为题目背景、未被评价的辅助前置，或实际考查方法、思想、模型、题型时不计入本节点。",
        "curriculum_anchors": list(dict.fromkeys(anchors)),
        "observable_evidence": f"学生作答中能够观察到对{display_name}的识别、表示、运算、论证或应用结果。",
        "rationale": "依据课程标准主题、审定教材范围和现有精细词语义形成的项目标准节点。",
        "evidence_source_ids": [
            "moe_math_curriculum_2022",
            "moe_approved_textbooks_2024",
            "project_taxonomy_governance_2026",
        ],
    }


def _fine_term_disposition(
    term: Mapping[str, Any],
    decision: Decision,
) -> dict[str, Any]:
    name = str(term["name"])
    anchors = _curriculum_anchors(term) or ["第四学段/扩展或待核对"]
    if decision.disposition == "wrong_dimension":
        include_scope = "仅用于识别原始题库说法和人工治理，不作为知识掌握证据。"
        exclude_scope = "不得进入核心知识掌握度、先修传播或个性化推荐。"
    elif decision.disposition == "retrieval_only":
        include_scope = "可用于检索相应扩展内容或综合题目。"
        exclude_scope = "不能作为稳定的初中核心掌握节点，也不参与掌握度传播。"
    else:
        include_scope = f"题目或解题证据点直接要求学生识别、说明、计算、证明或应用{name}。"
        exclude_scope = "仅在背景中出现、只作未评价前置知识或实际考查其他维度时不标。"
    return {
        "fine_term_id": term["id"],
        "display_name": name,
        "disposition": decision.disposition,
        "definition": f"规范词“{name}”用于描述以{name}为直接考查对象的题目或解题证据。",
        "include_scope": include_scope,
        "exclude_scope": exclude_scope,
        "curriculum_anchors": anchors,
        "rationale": decision.rationale,
        "confidence": decision.confidence,
        "review_priority": decision.review_priority,
        "evidence_source_ids": [
            "moe_math_curriculum_2022",
            "moe_approved_textbooks_2024",
            "project_taxonomy_governance_2026",
        ],
    }


def _relations(active_core_names: Mapping[str, str]) -> list[dict[str, Any]]:
    specs: list[tuple[str, str, str, str, str, str]] = [
        ("kp_alg_real_numbers", RATIONAL_CORE, "parent", "curriculum_structure", "required", "有理数是实数范围内的直接下位数系。"),
        ("kp_alg_number_sense", SCIENTIFIC_NOTATION_CORE, "parent", "curriculum_structure", "required", "科学记数法是数的表示、数量级理解与数感培养中的直接内容。"),
        ("kp_alg_power_rules", SCIENTIFIC_NOTATION_CORE, "prerequisite", "multi_textbook_sequence", "recommended", "表示较小的数会用到负整数指数，教材通常先建立整数指数幂规则。"),
        ("kp_alg_equation_properties", EQUATION_CORE, "prerequisite", "mathematical_logic", "required", "方程变形和求解需要等式基本性质。"),
        (EQUATION_CORE, "kp_alg_linear_equation", "prerequisite", "mathematical_logic", "required", "一元一次方程建立在方程、方程解和等式变形基础上。"),
        ("kp_alg_linear_equation", INEQUALITY_CORE, "prerequisite", "multi_textbook_sequence", "recommended", "一元一次不等式求解通常承接一元一次方程的变形经验，但两者不是数学上的从属关系。"),
        (INEQUALITY_CORE, "kp_alg_inequality_system", "prerequisite", "mathematical_logic", "required", "一元一次不等式组的求解以单个一元一次不等式及其解集为基础。"),
        (INEQUALITY_CORE, "kp_alg_inequality_appl", "prerequisite", "mathematical_logic", "required", "一元一次不等式应用需要能列出并求解相应不等式。"),
        ("kp_alg_linear_equation", "kp_alg_equation_system", "prerequisite", "multi_textbook_sequence", "recommended", "方程组求解通常以一元一次方程的变形和求解为基础。"),
        ("kp_alg_linear_equation", "kp_alg_quadratic_equation", "prerequisite", "multi_textbook_sequence", "recommended", "一元二次方程求解复用一元一次方程与等式变形经验。"),
        ("kp_alg_polynomial", "kp_alg_factorization", "prerequisite", "mathematical_logic", "required", "因式分解建立在整式结构和乘法运算基础上。"),
        ("kp_alg_polynomial", "kp_alg_fraction", "prerequisite", "mathematical_logic", "required", "分式运算需要整式运算与因式结构。"),
        ("kp_alg_fraction", "kp_alg_fraction_equation", "prerequisite", "mathematical_logic", "required", "分式方程的定义域和变形需要分式性质。"),
        ("kp_alg_real_numbers", "kp_alg_radical", "prerequisite", "mathematical_logic", "required", "二次根式的理解和运算以实数与平方根为基础。"),
        ("kp_fun_variables", "kp_fun_linear", "parent", "curriculum_structure", "required", "一次函数是函数的直接类型。"),
        ("kp_fun_variables", "kp_fun_inverse", "parent", "curriculum_structure", "required", "反比例函数是函数的直接类型。"),
        ("kp_fun_variables", "kp_fun_quadratic", "parent", "curriculum_structure", "required", "二次函数是函数的直接类型。"),
        ("kp_fun_coordinate_system", "kp_fun_function_graph", "prerequisite", "mathematical_logic", "required", "函数图像需要坐标系表示点和变量对应关系。"),
        ("kp_fun_function_graph", "kp_fun_linear", "prerequisite", "multi_textbook_sequence", "recommended", "一次函数的图像与性质通常在函数图像基础上学习。"),
        ("kp_fun_linear", "kp_fun_quadratic", "prerequisite", "multi_textbook_sequence", "recommended", "二次函数图像研究通常承接一次函数的表示和性质分析。"),
        ("kp_geo_line_angle", "kp_geo_parallel_lines", "prerequisite", "mathematical_logic", "required", "平行线判定和性质需要直线与角的基本关系。"),
        ("kp_geo_line_angle", "kp_geo_triangle_properties", "prerequisite", "mathematical_logic", "required", "三角形由线段和角组成，其性质建立在基本图形关系上。"),
        ("kp_geo_triangle_properties", "kp_geo_angle_sum_theorem", "parent", "curriculum_structure", "required", "内角和与外角定理是三角形性质的直接内容。"),
        ("kp_geo_triangle_properties", "kp_geo_triangle_area", "parent", "curriculum_structure", "required", "三角形面积是三角形度量的直接内容。"),
        ("kp_geo_triangle_properties", "kp_geo_triangle_congruence", "prerequisite", "mathematical_logic", "required", "全等判定需要三角形边角及基本性质。"),
        ("kp_geo_triangle_properties", "kp_geo_isosceles_triangle", "parent", "curriculum_structure", "required", "等腰三角形是三角形的直接类型。"),
        ("kp_geo_triangle_properties", "kp_geo_right_triangle", "parent", "curriculum_structure", "required", "直角三角形是三角形的直接类型。"),
        (POLYGON_CORE, "kp_geo_quadrilateral", "parent", "curriculum_structure", "required", "四边形是多边形的直接类型。"),
        ("kp_geo_quadrilateral", TRAPEZOID_CORE, "parent", "curriculum_structure", "required", "梯形是四边形的直接类型。"),
        ("kp_geo_quadrilateral", "kp_geo_parallelogram", "parent", "curriculum_structure", "required", "平行四边形是四边形的直接类型。"),
        ("kp_geo_parallelogram", "kp_geo_rectangle", "parent", "curriculum_structure", "required", "矩形是具有直角条件的平行四边形。"),
        ("kp_geo_parallelogram", "kp_geo_rhombus", "parent", "curriculum_structure", "required", "菱形是具有等边条件的平行四边形。"),
        ("kp_geo_rectangle", "kp_geo_square", "parent", "curriculum_structure", "required", "正方形是具有等边条件的矩形。"),
        ("kp_geo_rhombus", "kp_geo_square", "parent", "curriculum_structure", "required", "正方形是具有直角条件的菱形。"),
        ("kp_geo_triangle_properties", "kp_geo_similarity", "prerequisite", "mathematical_logic", "required", "图形相似和相似三角形需要基本三角形与比例关系。"),
        ("kp_geo_similarity", "kp_geo_similar_triangle", "parent", "curriculum_structure", "required", "相似三角形是图形相似的核心类型。"),
        ("kp_geo_similar_triangle", "kp_geo_similar_appl", "prerequisite", "mathematical_logic", "required", "相似三角形应用建立在判定与性质上。"),
        ("kp_geo_right_triangle", "kp_geo_trigonometry", "prerequisite", "mathematical_logic", "required", "锐角三角函数与解直角三角形需要直角三角形边角关系。"),
        ("kp_geo_circle_properties", "kp_geo_circle_angle", "parent", "curriculum_structure", "required", "圆心角、圆周角和垂径关系属于圆的性质。"),
        ("kp_geo_circle_properties", "kp_geo_circle_tangent", "parent", "curriculum_structure", "required", "点、直线与圆的位置关系属于圆的基本性质。"),
        ("kp_geo_circle_properties", "kp_geo_circle_measure", "parent", "curriculum_structure", "required", "弧长和面积计算属于圆的度量。"),
        ("kp_geo_circle_measure", "kp_geo_sector_cone", "prerequisite", "mathematical_logic", "required", "扇形与圆锥侧面计算需要弧长和扇形面积。"),
        (SOLID_CORE, "kp_geo_view_projection", "prerequisite", "multi_textbook_sequence", "recommended", "视图与投影通常以立体图形及其表面、位置认识为基础。"),
        ("kp_sta_data_collection", "kp_sta_data_display", "prerequisite", "multi_textbook_sequence", "recommended", "数据表示通常先经过调查、收集和整理。"),
        ("kp_sta_data_display", "kp_sta_data_representative", "prerequisite", "multi_textbook_sequence", "recommended", "集中趋势的解释需要能够读取和整理数据。"),
        ("kp_sta_data_display", "kp_sta_data_dispersion", "prerequisite", "multi_textbook_sequence", "recommended", "离散程度的解释需要能够读取和整理数据。"),
        ("kp_sta_survey", "kp_sta_estimate", "prerequisite", "mathematical_logic", "required", "用样本估计总体需要理解抽样调查及样本代表性。"),
        ("kp_probability", "kp_probability_calc", "prerequisite", "mathematical_logic", "required", "概率计算与应用需要随机事件和概率意义。"),
    ]
    relations: list[dict[str, Any]] = []
    for prerequisite_or_child, target, relation_type, basis, strength, rationale in specs:
        if prerequisite_or_child not in active_core_names or target not in active_core_names:
            raise ValueError("relation references an unknown active core")
        if relation_type == "parent":
            source_key, target_key = target, prerequisite_or_child
        elif relation_type == "prerequisite":
            source_key, target_key = target, prerequisite_or_child
        else:
            source_key, target_key = sorted((prerequisite_or_child, target))
        relations.append(
            {
                "relation_key": stable_record_hash(
                    "knowledge-relation", source_key, target_key, relation_type
                ),
                "source_key": source_key,
                "target_key": target_key,
                "relation_type": relation_type,
                "basis_kind": basis,
                "strength": strength,
                "rationale": rationale,
                "evidence_source_ids": [
                    "moe_math_curriculum_2022",
                    "moe_approved_textbooks_2024",
                    "knowledge_graph_authority_research_2026",
                ],
                "source_locator": "对应主题的课程内容、审定教材共同结构及本项目权威调查",
            }
        )
    return sorted(relations, key=lambda item: item["relation_key"])


def _sources() -> list[dict[str, str]]:
    # Project paths below are immutable provenance labels in the content-hashed
    # compatibility release. They are not runtime file dependencies.
    return [
        {
            "source_id": "moe_math_curriculum_2022",
            "kind": "curriculum_standard",
            "title": "义务教育数学课程标准（2022年版）",
            "reference": "https://www.moe.gov.cn/srcsite/A26/s8001/202204/W020220510531636118932.pdf",
        },
        {
            "source_id": "moe_approved_textbooks_2024",
            "kind": "approved_textbook_catalog",
            "title": "2024年义务教育国家课程教学用书目录",
            "reference": "https://www.moe.gov.cn/srcsite/A26/s8001/202408/W020240805496325238752.pdf",
        },
        {
            "source_id": "project_taxonomy_governance_2026",
            "kind": "project_governance",
            "title": "初中数学标签词表治理：受控词研究与收敛建议",
            "reference": "docs/architecture/2026-08-02-junior-math-taxonomy-governance-research.md",
        },
        {
            "source_id": "knowledge_graph_authority_research_2026",
            "kind": "project_research",
            "title": "初中数学知识图谱权威来源与长期治理调查",
            "reference": "docs/architecture/2026-08-03-junior-math-knowledge-graph-authority-research.md",
        },
    ]


def _node_kind(stable_key: str) -> str:
    if stable_key in {
        "kp_alg_real_numbers",
        "kp_fun_variables",
        "kp_geo_triangle_properties",
        "kp_geo_quadrilateral",
        "kp_geo_transformation",
        "kp_geo_similarity",
        "kp_geo_circle_properties",
        "kp_data_analysis",
        "kp_sta_data_display",
        "kp_probability",
    }:
        return "structural"
    return "core"


def _curriculum_anchors(term: Mapping[str, Any]) -> list[str]:
    stable_id = str(term.get("id") or "")
    if stable_id in ANCHOR_OVERRIDES_BY_ID:
        return list(ANCHOR_OVERRIDES_BY_ID[stable_id])
    anchors: list[str] = []
    for path in term.get("source_paths", []):
        if not path:
            continue
        root = str(path[0])
        base = DOMAIN_ROOTS.get(root, f"第四学段/{root}")
        detail = "/".join(str(value) for value in path[1:3] if str(value).strip())
        anchor = f"{base}/{detail}" if detail else base
        if anchor not in anchors:
            anchors.append(anchor)
    return anchors


def _root(term: Mapping[str, Any]) -> str:
    for path in term.get("source_paths", []):
        if path:
            return _normalize(path[0])
    return ""


def _normalize(value: object) -> str:
    return "".join(str(value or "").strip().casefold().split())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    catalog = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    payload = build_release(catalog)
    rendered = _render_json(payload)
    if args.check:
        if not OUTPUT_PATH.exists() or OUTPUT_PATH.read_text(encoding="utf-8") != rendered:
            raise SystemExit("knowledge graph release is not up to date")
        return 0
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "release_id": payload["release_id"],
                "core_nodes": len(payload["core_nodes"]),
                "fine_terms": len(payload["fine_term_dispositions"]),
                "mappings": len(payload["mappings"]),
                "relations": len(payload["relations"]),
                "content_hash": payload["content_hash"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
