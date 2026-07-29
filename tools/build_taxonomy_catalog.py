"""Build the immutable P3.5 junior-math controlled vocabulary.

The source snapshot is retained verbatim. This builder only promotes terms
whose dimension can be determined conservatively; everything else stays in
``reference_candidates`` and is never sent to the model as an approved term.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from question_bank.taxonomy.registry import CANONICAL_KNOWLEDGE  # noqa: E402


CATALOG_DIR = ROOT / "question_bank" / "taxonomy" / "catalogs"
SOURCE_PATH = CATALOG_DIR / "xkw_junior_math_taxonomy_2026-07-26.source.json"
CURRICULUM_PATH = CATALOG_DIR / "bnu_math_2024.json"
OUTPUT_PATH = CATALOG_DIR / "tag_vocabulary_v2.json"

ABILITY_TERMS = (
    "运算能力",
    "几何直观",
    "推理能力",
    "抽象能力",
    "模型观念",
    "空间观念",
    "数据观念",
    "应用意识",
    "创新意识",
    "阅读理解",
)
METHOD_TERMS = (
    "方程思想",
    "数形结合",
    "分类讨论",
    "转化与化归",
    "整体思想",
    "待定系数法",
    "配方法",
    "换元法",
    "构造辅助线",
    "构造全等",
    "构造相似",
    "角度转化",
    "面积法",
    "反证法",
    "函数思想",
    "模型思想",
)
MODEL_TERMS = (
    "平行线角度模型",
    "角平分线模型",
    "中点模型",
    "倍长中线模型",
    "中位线模型",
    "一线三等角模型",
    "一线三垂直模型",
    "手拉手模型",
    "半角模型",
    "倍角模型",
    "旋转模型",
    "折叠模型",
    "将军饮马模型",
    "费马点模型",
    "隐圆模型",
    "胡不归模型",
    "阿氏圆模型",
    "瓜豆模型",
    "弦图模型",
    "相似三角形模型",
    "面积等积模型",
)

TERM_RETRIEVAL_HINTS = {
    "kp_alg_real_numbers": ("数轴",),
    "kp_alg_letter_number": ("科学记数法", "科学记数"),
    "kp_alg_equation_system": ("代入消元法", "加减消元法"),
    "kp_alg_quadratic_equation": ("配方法",),
    "kp_geo_construction": ("尺规作角平分线", "作线段", "作垂线"),
    "kp_syn_reading": ("阅读理解题", "新定义问题"),
}

CURATED_TERM_SPECS = (
    (
        "knowledge",
        "数轴的概念与画法",
        ("数轴的三要素及其画法",),
        (("数与式", "有理数", "数轴", "数轴的三要素及其画法"),),
    ),
    (
        "knowledge",
        "代数式的规范书写",
        ("代数式书写方法",),
        (
            (
                "数与式",
                "代数式",
                "代数式及其应用",
                "代数式的概念及意义",
                "代数式书写方法",
            ),
        ),
    ),
    (
        "knowledge",
        "科学记数法的乘除运算",
        ("用科学记数法表示数的乘法", "用科学记数法表示数的除法"),
        (
            (
                "数与式",
                "代数式",
                "整式的乘除",
                "同底数幂的乘法",
                "用科学记数法表示数的乘法",
            ),
            (
                "数与式",
                "代数式",
                "整式的乘除",
                "单项式除以单项式",
                "用科学记数法表示数的除法",
            ),
        ),
    ),
    (
        "knowledge",
        "(x+p)(x+q)型多项式乘法",
        ("（x+p）（x+q）型多项式乘法",),
        (
            (
                "数与式",
                "代数式",
                "整式的乘除",
                "多项式乘多项式",
                "（x+p）（x+q）型多项式乘法",
            ),
        ),
    ),
    (
        "knowledge",
        "分式的乘法",
        ("分式乘法",),
        (("数与式", "分式", "分式的运算", "分式的乘除", "分式乘法"),),
    ),
    (
        "knowledge",
        "分式的除法",
        ("分式除法",),
        (("数与式", "分式", "分式的运算", "分式的乘除", "分式除法"),),
    ),
    (
        "knowledge",
        "同分母分式加减法",
        (),
        (
            (
                "数与式",
                "分式",
                "分式的运算",
                "分式的加减法则",
                "同分母分式加减法",
            ),
        ),
    ),
    (
        "knowledge",
        "异分母分式加减法",
        (),
        (
            (
                "数与式",
                "分式",
                "分式的运算",
                "分式的加减法则",
                "异分母分式加减法",
            ),
        ),
    ),
    (
        "knowledge",
        "二次根式的乘法",
        (),
        (("数与式", "二次根式", "二次根式的乘除", "二次根式的乘法"),),
    ),
    (
        "knowledge",
        "二次根式的除法",
        (),
        (("数与式", "二次根式", "二次根式的乘除", "二次根式的除法"),),
    ),
    (
        "knowledge",
        "函数的三种表示方法",
        (),
        (("函数", "函数基础知识", "函数的三种表示方法"),),
    ),
    (
        "knowledge",
        "角的表示方法",
        (),
        (("图形的性质", "几何图形初步", "角", "角的概念", "角的表示方法"),),
    ),
    (
        "knowledge",
        "勾股定理的证明",
        ("勾股定理的证明方法",),
        (
            (
                "图形的性质",
                "三角形",
                "勾股定理及逆定理",
                "勾股定理",
                "勾股定理的证明方法",
            ),
        ),
    ),
    (
        "knowledge",
        "尺规确定圆心",
        ("确定圆心(尺规作图)",),
        (
            (
                "图形的性质",
                "圆",
                "点、直线、圆的位置关系",
                "确定圆的条件",
                "确定圆心(尺规作图)",
            ),
        ),
    ),
    (
        "knowledge",
        "尺规作线段",
        ("作线段(尺规作图)", "作线段"),
        (("图形的性质", "限定工具作图", "作线段(尺规作图)"),),
    ),
    (
        "knowledge",
        "尺规作三角形",
        ("尺规作图——作三角形",),
        (("图形的性质", "限定工具作图", "尺规作图——作三角形"),),
    ),
    (
        "knowledge",
        "尺规作角平分线",
        ("作角平分线(尺规作图)",),
        (("图形的性质", "限定工具作图", "作角平分线(尺规作图)"),),
    ),
    (
        "knowledge",
        "尺规作垂线",
        ("作垂线(尺规作图)", "作垂线"),
        (("图形的性质", "限定工具作图", "作垂线(尺规作图)"),),
    ),
    (
        "knowledge",
        "尺规作等腰三角形",
        ("作等腰三角形(尺规作图)",),
        (("图形的性质", "限定工具作图", "作等腰三角形(尺规作图)"),),
    ),
    (
        "knowledge",
        "尺规作圆",
        ("画圆(尺规作图)",),
        (("图形的性质", "限定工具作图", "画圆(尺规作图)"),),
    ),
    (
        "knowledge",
        "尺规作圆的切线",
        ("过圆外一点作圆的切线(尺规作图)",),
        (
            (
                "图形的性质",
                "限定工具作图",
                "过圆外一点作圆的切线(尺规作图)",
            ),
        ),
    ),
    (
        "knowledge",
        "尺规作正多边形",
        ("尺规作图——正多边形",),
        (("图形的性质", "限定工具作图", "尺规作图——正多边形"),),
    ),
    (
        "method",
        "十字相乘法",
        (),
        (("数与式", "因式分解", "十字相乘法"),),
    ),
    (
        "method",
        "分组分解法",
        (),
        (("数与式", "因式分解", "分组分解法"),),
    ),
    (
        "method",
        "代入消元法",
        (),
        (("方程与不等式", "二元一次方程组", "解二元一次方程组", "代入消元法"),),
    ),
    (
        "method",
        "加减消元法",
        (),
        (("方程与不等式", "二元一次方程组", "解二元一次方程组", "加减消元法"),),
    ),
    (
        "method",
        "直接开平方法",
        ("解一元二次方程——直接开平方法",),
        (
            (
                "方程与不等式",
                "一元二次方程",
                "解一元二次方程",
                "解一元二次方程——直接开平方法",
            ),
        ),
    ),
    (
        "method",
        "构造直角三角形法",
        (
            "用勾股定理构造图形解决问题",
            "构造直角三角形求不规则图形的边长或面积",
        ),
        (
            (
                "图形的性质",
                "三角形",
                "勾股定理及逆定理",
                "勾股定理",
                "用勾股定理构造图形解决问题",
            ),
            (
                "图形的变化",
                "锐角三角函数",
                "解直角三角形及其应用",
                "解直角三角形",
                "构造直角三角形求不规则图形的边长或面积",
            ),
        ),
    ),
    (
        "model",
        "圆外切四边形模型",
        (),
        (
            (
                "图形的性质",
                "圆",
                "点、直线、圆的位置关系",
                "三角形内切圆",
                "圆外切四边形模型",
            ),
        ),
    ),
    (
        "special_type",
        "实验操作题",
        ("实验操作类",),
        (("压轴题", "实验操作类"),),
    ),
    (
        "special_type",
        "猜想证明题",
        ("猜想证明",),
        (("压轴题", "猜想证明"),),
    ),
    (
        "special_type",
        "动态几何题",
        ("动态几何",),
        (("压轴题", "动态几何"),),
    ),
    (
        "special_type",
        "数学阅读理解题",
        ("阅读理解", "阅读理解题"),
        (("压轴题", "阅读理解"),),
    ),
    (
        "special_type",
        "开放探究题",
        ("开放探究",),
        (("压轴题", "开放探究"),),
    ),
    (
        "special_type",
        "新定义题",
        ("新定义问题",),
        (("压轴题", "新定义问题"),),
    ),
    (
        "special_type",
        "作图与证明综合题",
        ("结合尺规作图的全等问题（全等三角形的判定综合）",),
        (
            (
                "图形的性质",
                "三角形",
                "全等三角形",
                "三角形全等的判定",
                "结合尺规作图的全等问题（全等三角形的判定综合）",
            ),
        ),
    ),
    (
        "special_type",
        "线段和差证明题",
        ("证一条线段等于两条线段和差（全等三角形的辅助线问题）",),
        (
            (
                "图形的性质",
                "三角形",
                "全等三角形",
                "三角形全等的判定",
                "证一条线段等于两条线段和差（全等三角形的辅助线问题）",
            ),
        ),
    ),
    (
        "special_type",
        "格点作图题",
        (),
        (("图形的性质", "限定工具作图", "格点作图题"),),
    ),
    (
        "special_type",
        "无刻度直尺作图题",
        ("无刻度直尺作图",),
        (("图形的性质", "限定工具作图", "无刻度直尺作图"),),
    ),
    (
        "special_type",
        "立体模型制作实践题",
        ("课题学习制作立体模型",),
        (("图形的变化", "投影与视图", "三视图", "课题学习制作立体模型"),),
    ),
)

REVIEWED_MAPPING_SPECS = (
    {
        "source_path": ("数与式", "分式", "分式的概念及性质", "分式的定义", "按要求构造分式"),
        "targets": (
            {"kind": "taxonomy", "dimension": "knowledge", "name": "分式"},
            {
                "kind": "taxonomy",
                "dimension": "special_type",
                "name": "开放探究题",
            },
        ),
    },
    {
        "source_path": (
            "方程与不等式",
            "二元一次方程组",
            "解二元一次方程组",
            "解二元一次方程组的应用",
            "构造二元一次方程组求解",
        ),
        "targets": (
            {
                "kind": "taxonomy",
                "dimension": "knowledge",
                "name": "二元一次方程组应用",
            },
            {"kind": "taxonomy", "dimension": "method", "name": "方程思想"},
        ),
    },
    {
        "source_path": (
            "方程与不等式",
            "一元二次方程",
            "解一元二次方程",
            "解一元二次方程——配方法",
        ),
        "targets": (
            {"kind": "taxonomy", "dimension": "method", "name": "配方法"},
        ),
    },
    {
        "source_path": (
            "图形的性质",
            "三角形",
            "全等三角形",
            "三角形全等的判定",
            "连接两点构造全等三角形（全等三角形的辅助线问题）",
        ),
        "targets": (
            {"kind": "taxonomy", "dimension": "method", "name": "构造全等"},
        ),
    },
    {
        "source_path": (
            "图形的性质",
            "三角形",
            "全等三角形",
            "三角形全等的判定",
            "倍长中线模型(全等三角形的辅助线问题)",
        ),
        "targets": (
            {"kind": "taxonomy", "dimension": "model", "name": "倍长中线模型"},
        ),
    },
    {
        "source_path": (
            "图形的性质",
            "三角形",
            "全等三角形",
            "三角形全等的判定",
            "旋转模型(全等三角形的辅助线问题)",
        ),
        "targets": (
            {"kind": "taxonomy", "dimension": "model", "name": "旋转模型"},
        ),
    },
    {
        "source_path": (
            "图形的性质",
            "三角形",
            "全等三角形",
            "三角形全等的判定",
            "垂线模型(全等三角形的辅助线问题)",
        ),
        "targets": (
            {"kind": "taxonomy", "dimension": "model", "name": "垂直模型"},
        ),
    },
    {
        "source_path": ("图形的变化", "平移", "平移(作图)"),
        "targets": (
            {"kind": "taxonomy", "dimension": "knowledge", "name": "图形的平移"},
        ),
    },
    {
        "source_path": (
            "统计与概率",
            "数据的收集与整理",
            "统计调查",
            "调查收集数据的过程与方法",
        ),
        "targets": (
            {
                "kind": "taxonomy",
                "dimension": "knowledge",
                "name": "数据的收集与整理",
            },
        ),
    },
    {
        "source_path": ("压轴题", "综合运用"),
        "targets": (
            {
                "kind": "field",
                "field": "question_type",
                "value": "comprehensive",
            },
        ),
    },
)

DEFERRED_SOURCE_REASONS = {
    (
        "方程与不等式",
        "二元一次方程组",
        "解二元一次方程组",
        "二元一次方程组的特殊解法",
    ): "暂缓收录：名称过于笼统，暂不能形成稳定的方法标签",
    (
        "方程与不等式",
        "二元二次方程组及其解法",
    ): "暂缓收录：超出当前七至九年级常规教学范围",
    (
        "图形的性质",
        "三角形",
        "全等三角形",
        "三角形全等的判定",
        "其他模型(全等三角形的辅助线问题)",
    ): "暂缓收录：“其他模型”无法形成稳定、可复用的模型标签",
}

CORE_KNOWLEDGE_ROOTS = {
    "数与式",
    "方程与不等式",
    "函数",
    "图形的性质",
    "图形的变化",
    "统计与概率",
    "观察、猜想与证明",
}
EXCLUDED_ROOTS = {"向量的运算", "五四制小学衔接", "数学竞赛"}
KNOWLEDGE_EXCLUDED_TOKENS = (
    "模型",
    "技巧",
    "压轴",
    "竞赛",
    "五四制",
    "小升初",
    "培优",
    "专题",
    "真题",
    "易错",
    "常考",
    "辅助线",
    "构造",
    "作图",
    "猜想规律",
)
METHOD_HINTS = (
    "法",
    "思想",
    "数形结合",
    "分类讨论",
    "转化",
    "化归",
    "整体",
    "换元",
    "特殊值",
    "作平行线",
    "作垂线",
    "倍长中线",
    "截长补短",
    "辅助圆",
    "面积",
)


def _normalized(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s\W_]+", "", text)


def _stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(
        "\0".join(str(part or "") for part in parts).encode("utf-8")
    ).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def _term(
    term_id: str,
    dimension: str,
    name: str,
    *,
    aliases: Iterable[str] = (),
    retrieval_hints: Iterable[str] = (),
    origin: str,
    source_paths: Iterable[Iterable[str]] = (),
) -> dict[str, Any]:
    term = {
        "id": term_id,
        "dimension": dimension,
        "name": name,
        "aliases": list(aliases),
        "status": "approved",
        "origin": origin,
        "source_paths": [list(path) for path in source_paths],
    }
    hints = list(retrieval_hints)
    if hints:
        term["retrieval_hints"] = hints
    return term


def _dedupe_aliases(terms: list[dict[str, Any]]) -> None:
    owners: dict[tuple[str, str], str] = {}
    for term in terms:
        dimension = term["dimension"]
        term_id = term["id"]
        for value in (term_id, term["name"]):
            key = (dimension, _normalized(value))
            owner = owners.get(key)
            if owner is not None and owner != term_id:
                raise ValueError(f"Duplicate approved term identity: {value}")
            owners[key] = term_id
        kept: list[str] = []
        for alias in term["aliases"]:
            key = (dimension, _normalized(alias))
            owner = owners.get(key)
            if not key[1] or owner not in (None, term_id):
                continue
            owners[key] = term_id
            if _normalized(alias) != _normalized(term["name"]):
                kept.append(alias)
        term["aliases"] = kept


def _curriculum_terms(payload: dict[str, Any]) -> list[dict[str, Any]]:
    terms: list[dict[str, Any]] = []
    for volume in payload.get("volumes", []):
        volume_label = str(volume.get("label") or "").strip()
        for chapter in volume.get("chapters", []):
            if str(chapter.get("kind") or "").strip() != "chapter":
                continue
            chapter_id = str(chapter.get("id") or "").strip()
            chapter_label = str(chapter.get("label") or "").strip()
            if (
                not volume_label
                or not chapter_id
                or not re.match(r"^第[一二三四五六七八九十百0-9]+章(?:\s|$)", chapter_label)
            ):
                continue
            name = f"{volume_label} {chapter_label}"
            terms.append(
                _term(
                    chapter_id,
                    "curriculum",
                    name,
                    aliases=(name.replace(" ", ""),),
                    origin="bnu_2024_catalog",
                    source_paths=((volume_label, chapter_label),),
                )
            )
    return terms


def _seed_terms() -> list[dict[str, Any]]:
    terms = [
        _term(
            item.canonical_id.casefold(),
            "knowledge",
            item.canonical_name,
            aliases=item.aliases,
            retrieval_hints=TERM_RETRIEVAL_HINTS.get(
                item.canonical_id.casefold(),
                (),
            ),
            origin="p3_registry",
        )
        for item in CANONICAL_KNOWLEDGE
    ]
    for dimension, names in (
        ("ability", ABILITY_TERMS),
        ("method", METHOD_TERMS),
        ("model", MODEL_TERMS),
    ):
        terms.extend(
            _term(
                _stable_id(dimension, name),
                dimension,
                name,
                origin="p3_controlled_options",
            )
            for name in names
        )
    return terms


def _curated_terms() -> list[dict[str, Any]]:
    terms = [
        _term(
            _stable_id(dimension, name),
            dimension,
            name,
            aliases=aliases,
            origin="p3_5_teacher_curated",
            source_paths=source_paths,
        )
        for dimension, name, aliases, source_paths in CURATED_TERM_SPECS
    ]
    if len(terms) != 40:
        raise ValueError(f"Expected 40 curated terms, got {len(terms)}")
    return terms


def _reviewed_source_decisions() -> dict[tuple[str, ...], dict[str, str]]:
    decisions: dict[tuple[str, ...], dict[str, str]] = {}

    def add(path: Iterable[str], action: str, reason: str = "") -> None:
        key = tuple(path)
        if key in decisions:
            raise ValueError(f"Duplicate reviewed source path: {' > '.join(key)}")
        decisions[key] = {
            "action": action,
            "source_name": key[-1],
            "reason": reason,
        }

    for _dimension, _name, _aliases, source_paths in CURATED_TERM_SPECS:
        for path in source_paths:
            add(path, "added_term")
    for mapping in REVIEWED_MAPPING_SPECS:
        add(mapping["source_path"], "mapped")
    for path, reason in DEFERRED_SOURCE_REASONS.items():
        add(path, "deferred", reason)

    counts: dict[str, int] = {}
    for decision in decisions.values():
        action = decision["action"]
        counts[action] = counts.get(action, 0) + 1
    expected = {"added_term": 42, "mapped": 10, "deferred": 3}
    if counts != expected:
        raise ValueError(f"Unexpected reviewed source counts: {counts}")
    return decisions


def _apply_reviewed_mappings(
    terms: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    terms_by_name: dict[tuple[str, str], dict[str, Any]] = {}
    for term in terms:
        key = (term["dimension"], _normalized(term["name"]))
        if key in terms_by_name:
            raise ValueError(
                f"Duplicate canonical target before reviewed mappings: {term['name']}"
            )
        terms_by_name[key] = term

    records: list[dict[str, Any]] = []
    for mapping in REVIEWED_MAPPING_SPECS:
        source_path = tuple(mapping["source_path"])
        source_name = source_path[-1]
        resolved_targets: list[dict[str, str]] = []
        for target in mapping["targets"]:
            if target["kind"] == "field":
                resolved_targets.append(
                    {
                        "kind": "field",
                        "field": target["field"],
                        "value": target["value"],
                    }
                )
                continue

            key = (target["dimension"], _normalized(target["name"]))
            term = terms_by_name.get(key)
            if term is None:
                raise ValueError(
                    "Reviewed mapping target was not found: "
                    f"{target['dimension']} / {target['name']}"
                )
            if source_name not in term["aliases"]:
                term["aliases"].append(source_name)
            source_path_list = list(source_path)
            if source_path_list not in term["source_paths"]:
                term["source_paths"].append(source_path_list)
            resolved_targets.append(
                {
                    "kind": "taxonomy",
                    "id": term["id"],
                    "dimension": term["dimension"],
                    "name": term["name"],
                }
            )
        records.append(
            {
                "source_name": source_name,
                "source_path": list(source_path),
                "targets": resolved_targets,
            }
        )
    if len(records) != 10:
        raise ValueError(f"Expected 10 reviewed mappings, got {len(records)}")
    return records


def _source_terms(
    source: dict[str, Any],
    terms: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    approved_names = {
        (term["dimension"], _normalized(value))
        for term in terms
        for value in (term["id"], term["name"], *term["aliases"])
    }
    promoted: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    reviewed_decisions = _reviewed_source_decisions()
    reviewed_seen: dict[tuple[str, ...], int] = {}

    def add_candidate(node: dict[str, Any], reason: str) -> None:
        path = [str(item).strip() for item in node.get("path", []) if str(item).strip()]
        name = str(node.get("name") or "").strip()
        if name and path:
            candidates.append(
                {"name": name, "source_path": path, "reason": reason}
            )

    def handle_reviewed(path: list[str], name: str, node: dict[str, Any]) -> bool:
        key = tuple(path)
        decision = reviewed_decisions.get(key)
        if decision is None:
            return False
        if name != decision["source_name"]:
            raise ValueError(
                f"Reviewed source name changed at {' > '.join(path)}: {name}"
            )
        reviewed_seen[key] = reviewed_seen.get(key, 0) + 1
        if decision["action"] == "deferred":
            add_candidate(node, decision["reason"])
        return True

    for node in source.get("knowledge", {}).get("nodes", []):
        path = [str(item).strip() for item in node.get("path", []) if str(item).strip()]
        name = str(node.get("name") or "").strip()
        if handle_reviewed(path, name, node):
            continue
        root = path[0] if path else ""
        if root in EXCLUDED_ROOTS:
            add_candidate(node, "超出当前七至九年级常规教学范围")
            continue
        if root not in CORE_KNOWLEDGE_ROOTS or not bool(node.get("leaf")):
            add_candidate(node, "来源层级节点，仅保留作分类参考")
            continue
        if (
            not name
            or len(name) > 36
            or any(token in name for token in KNOWLEDGE_EXCLUDED_TOKENS)
            or name.endswith("法")
        ):
            add_candidate(node, "维度或粒度仍需教师确认")
            continue
        key = ("knowledge", _normalized(name))
        if key in approved_names:
            continue
        term = _term(
            _stable_id("xkw-knowledge", *path),
            "knowledge",
            name,
            origin="xkw_2026_snapshot",
            source_paths=(path,),
        )
        promoted.append(term)
        approved_names.add(key)

    for node in source.get("methods", {}).get("nodes", []):
        path = [str(item).strip() for item in node.get("path", []) if str(item).strip()]
        name = str(node.get("name") or "").strip()
        if handle_reviewed(path, name, node):
            continue
        if not path or not name or not bool(node.get("leaf")):
            add_candidate(node, "来源层级节点，仅保留作分类参考")
            continue
        if path[0] == "压轴题" or "压轴" in name or len(name) > 36:
            add_candidate(node, "题型范围或粒度仍需教师确认")
            continue
        dimension = (
            "method"
            if path[0] == "数学方法" or any(token in name for token in METHOD_HINTS)
            else "model"
        )
        key = (dimension, _normalized(name))
        if key in approved_names:
            continue
        term = _term(
            _stable_id(f"xkw-{dimension}", *path),
            dimension,
            name,
            origin="xkw_2026_snapshot",
            source_paths=(path,),
        )
        promoted.append(term)
        approved_names.add(key)

    missing = set(reviewed_decisions) - set(reviewed_seen)
    repeated = {path: count for path, count in reviewed_seen.items() if count != 1}
    if missing or repeated:
        missing_text = [" > ".join(path) for path in sorted(missing)]
        repeated_text = {
            " > ".join(path): count for path, count in sorted(repeated.items())
        }
        raise ValueError(
            "Reviewed source coverage changed: "
            f"missing={missing_text}, repeated={repeated_text}"
        )

    unique_candidates: list[dict[str, Any]] = []
    seen_candidates: set[tuple[str, tuple[str, ...], str]] = set()
    for candidate in candidates:
        key = (
            _normalized(candidate["name"]),
            tuple(candidate["source_path"]),
            candidate["reason"],
        )
        if key not in seen_candidates:
            seen_candidates.add(key)
            unique_candidates.append(candidate)
    return promoted, unique_candidates


def build() -> dict[str, Any]:
    source = _read_json(SOURCE_PATH)
    curriculum = _read_json(CURRICULUM_PATH)
    terms = [*_curriculum_terms(curriculum), *_seed_terms(), *_curated_terms()]
    promoted, reference_candidates = _source_terms(source, terms)
    terms.extend(promoted)
    reviewed_mappings = _apply_reviewed_mappings(terms)
    _dedupe_aliases(terms)
    terms.sort(key=lambda item: (item["dimension"], item["name"], item["id"]))

    counts: dict[str, int] = {}
    for term in terms:
        counts[term["dimension"]] = counts.get(term["dimension"], 0) + 1
    expected_counts = {
        "curriculum": 25,
        "knowledge": 1121,
        "ability": 10,
        "method": 31,
        "model": 90,
        "special_type": 11,
    }
    if counts != expected_counts:
        raise ValueError(f"Unexpected catalog dimension counts: {counts}")
    if len(reference_candidates) != 618:
        raise ValueError(
            "Expected 618 unresolved reference candidates, "
            f"got {len(reference_candidates)}"
        )

    dimensions = [
        "curriculum",
        "knowledge",
        "ability",
        "method",
        "model",
        "special_type",
    ]
    return {
        "schema_version": 2,
        "catalog_id": "junior-math-controlled-vocabulary-v2",
        "revision": 2,
        "source_snapshot": {
            "file": SOURCE_PATH.name,
            "observed_at": source.get("source", {}).get("observed_at"),
            "knowledge_node_count": len(source.get("knowledge", {}).get("nodes", [])),
            "method_node_count": len(source.get("methods", {}).get("nodes", [])),
            "runtime_refresh": False,
        },
        "classification_policy": {
            "dimensions": dimensions,
            "excluded_branches": sorted(EXCLUDED_ROOTS),
            "legacy_free_text_dimensions": [
                "sub_skill",
                "measured_skill",
                "supporting_skill",
            ],
            "rule": (
                "本地先从正式词表筛出相关范围，大模型优先匹配正式标签；"
                "确实无法匹配时允许少量自由标签，再回到完整词表复核，"
                "仍无法匹配的项目进入定期整理。"
            ),
            "reviewed_source_summary": {
                "reviewed": 55,
                "added_term_source_paths": 42,
                "mapped_source_paths": 10,
                "deferred_source_paths": 3,
                "removed_from_reference_candidates": 52,
            },
            "retrieval_hints": {
                "prefilter_dimensions": dimensions,
                "match_fields": [
                    "id",
                    "name",
                    "aliases",
                    "retrieval_hints",
                    "source_paths",
                ],
                "preferred_model_output": "approved_term_id",
                "free_text_fallback": {
                    "allowed": True,
                    "scope": "少量",
                    "local_full_vocabulary_rematch": True,
                    "unmatched_destination": "periodic_review",
                },
                "reviewed_source_mappings": reviewed_mappings,
            },
        },
        "terms": terms,
        "reference_candidates": reference_candidates,
    }


def main() -> None:
    payload = build()
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    counts: dict[str, int] = {}
    for term in payload["terms"]:
        counts[term["dimension"]] = counts.get(term["dimension"], 0) + 1
    print(
        json.dumps(
            {
                "output": OUTPUT_PATH.name,
                "counts": counts,
                "reference_candidates": len(payload["reference_candidates"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
