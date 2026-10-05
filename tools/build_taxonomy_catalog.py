"""Build the governed junior-math controlled vocabulary.

The external snapshot is discovery material, never the runtime vocabulary.
Only stable concept-level knowledge nodes and explicitly curated methods,
thoughts, models, abilities and special question types are promoted.  Fine
source leaves and historical names stay as hidden legacy lookup keys so an old
tag can converge to a new canonical term without being offered to the model.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from question_bank.taxonomy.registry import CANONICAL_KNOWLEDGE  # noqa: E402
from tools.build_release_v3 import _write_json  # noqa: E402


CATALOG_DIR = ROOT / "question_bank" / "taxonomy" / "catalogs"
SOURCE_PATH = CATALOG_DIR / "xkw_junior_math_taxonomy_2026-07-26.source.json"
CURRICULUM_PATH = CATALOG_DIR / "bnu_math_2024.json"
OUTPUT_PATH = CATALOG_DIR / "tag_vocabulary_v2.json"

DIMENSIONS = (
    "curriculum",
    "knowledge",
    "ability",
    "method",
    "thought",
    "model",
    "special_type",
)

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

THOUGHT_SPECS = (
    ("数学建模思想", ("模型思想", "模型化思想")),
    ("方程思想", ("方程建模思想",)),
    ("函数思想", ("函数观点",)),
    ("数形结合思想", ("数形结合",)),
    ("分类讨论思想", ("分类讨论", "分类讨论法", "分情况讨论")),
    ("转化与化归思想", ("转化与化归", "转化思想", "化归思想")),
    ("整体思想", ("整体处理思想",)),
    ("归纳与猜想思想", ("归纳与猜想", "归纳猜想", "观察归纳")),
    ("类比思想", ("类比推理",)),
    ("推理与证明思想", ("推理与证明", "演绎推理")),
    ("特殊与一般思想", ("特殊化思想", "一般化思想", "从特殊到一般")),
    ("程序化思想", ("算法思想", "步骤化思想")),
    ("统计推断思想", ("统计思想", "用样本估计总体")),
)

METHOD_SPECS = (
    ("代入消元法", ("代入法",)),
    ("加减消元法", ("加减法消元",)),
    ("换元法", ("设元代换", "整体换元")),
    ("待定系数法", ("设系数法",)),
    ("配方法", ("完全平方配方",)),
    ("公式法", ("求根公式法",)),
    ("直接开平方法", ("开平方法",)),
    ("因式分解法", ("分解因式法", "十字相乘法", "分组分解法")),
    ("反证法", ("反证",)),
    ("特殊值法", ("取特殊值", "特例法")),
    ("枚举法", ("列举法", "穷举法")),
    ("举反例法", ("举反例", "反例法")),
    (
        "构造辅助线法",
        (
            "构造辅助线",
            "作辅助线",
            "作垂线法",
            "作平行线法",
            "倍长中线",
            "截长补短",
            "补全图形法",
        ),
    ),
    ("构造全等三角形法", ("构造全等",)),
    ("构造相似三角形法", ("构造相似",)),
    ("构造直角三角形法", ()),
    ("几何变换法", ("平移法", "旋转法", "对称法", "平移对角线法")),
    ("面积法", ("面积关系法",)),
    ("角度转化法", ("角度转化",)),
    ("证明切线法", ("证明切线的方法",)),
    ("坐标法", ("坐标系法",)),
    ("图象法", ("图像法",)),
    ("割补法", ("割补图形法",)),
)

MODEL_SPECS = (
    ("方程模型", ()),
    ("不等式模型", ("不等关系模型",)),
    ("函数模型", ()),
    ("统计推断模型", ("抽样估计模型",)),
    ("概率试验模型", ("随机试验模型",)),
    ("行程模型", ("路程速度时间模型", "相遇追及模型")),
    ("工程模型", ("工作效率模型",)),
    ("销售与利润模型", ("利润模型",)),
    ("浓度与配比模型", ("浓度模型",)),
    ("几何测量模型", ("测高模型", "梯子模型", "风吹树折")),
    ("一线三等角模型", ("一线三等角",)),
    ("一线三垂直模型", ("一线三垂直", "与正方形有关的三垂线")),
    ("手拉手模型", ("手拉手",)),
    ("半角模型", ("半角",)),
    ("将军饮马模型", ("将军饮马", "最短路径模型", "蚂蚁爬行")),
    ("胡不归模型", ("胡不归",)),
    ("阿氏圆模型", ("阿氏圆",)),
    ("费马点模型", ("费马点",)),
    ("隐圆模型", ("隐圆",)),
    ("瓜豆模型", ("瓜豆原理", "瓜豆")),
    ("弦图模型", ("弦图",)),
    ("一点一垂线模型", ("一点一垂线",)),
    ("一点两垂线模型", ("一点两垂线",)),
    ("两点一垂线模型", ("两点一垂线",)),
    ("两点两垂线模型", ("两点两垂线",)),
    ("三平行模型", ("三平行",)),
    ("中点模型", ()),
    ("角平分线模型", ()),
    ("倍长中线模型", ()),
    ("对角互补模型", ("对角互补",)),
    ("圆外切四边形模型", ()),
    ("面积等积模型", ("等积模型",)),
    (
        "相似三角形模型",
        (
            "A字型",
            "8字型",
            "K字型相似",
            "母子型相似",
            "（双）A字型相似",
            "（双）8型相似",
            "背靠背型",
            "拥抱型",
        ),
    ),
    ("全等三角形模型", ("公共边模型", "公共角模型", "x模型")),
    (
        "旋转模型",
        ("等腰旋转", "双等腰旋转", "互补型旋转", "旋转相似"),
    ),
    ("折叠模型", ("与三角形有关的折叠", "线段的（折叠，动点）模型")),
    ("中点四边形模型", ("中点四边形",)),
    ("垂美四边形模型", ("垂美四边形",)),
    ("线段分点模型", ()),
    ("角等分模型", ("角n等分模型",)),
    ("定弦定角模型", ("定弦定角",)),
)

SPECIAL_TYPE_SPECS = (
    ("实验操作题", ("实验操作类",)),
    ("猜想证明题", ("猜想证明",)),
    ("动态几何题", ("动态几何",)),
    ("数学阅读理解题", ("阅读理解", "阅读理解题")),
    ("开放探究题", ("开放探究",)),
    ("新定义题", ("新定义问题",)),
    ("作图与证明综合题", ()),
    ("线段和差证明题", ()),
    ("格点作图题", ()),
    ("无刻度直尺作图题", ("无刻度直尺作图",)),
    ("立体模型制作实践题", ()),
    # 解答题子类标签（题型归一后的一等子类词）。
    ("画图", ("作图",)),
    ("计算", ()),
    ("证明", ("求证",)),
)

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
    "根据",
    "利用",
    "进行",
    "解决",
    "求解",
    "计算",
    "判断",
    "探究",
    "综合",
    "问题情境",
    "应用题",
)

OLD_METHOD_HINTS = (
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

MODEL_LEGACY_MAP = {
    '"骨折"型': "相似三角形模型",
    '"鸡翅"型': "相似三角形模型",
    "(叠合式)子母型": "相似三角形模型",
    "12345型": "几何测量模型",
    "378和578模型": "几何测量模型",
    "M型(含锯齿型)": "三平行模型",
    "笔尖型": "三平行模型",
    "燕尾角": "角平分线模型",
    "双角平分线型": "角平分线模型",
    "边边角模型(胖瘦模型)": "全等三角形模型",
    "十字架模型": "中点模型",
    "正方形与45°角的基本图": "一线三垂直模型",
    "周期型": "函数模型",
    "递推型": "函数模型",
    "固定累加型": "函数模型",
    "渐变累加型": "函数模型",
    "婆罗摩笈多": "圆外切四边形模型",
}

KNOWLEDGE_RETRIEVAL_HINTS = {
    "kp_alg_real_numbers": ("数轴",),
    "kp_alg_letter_number": ("科学记数法", "科学记数"),
    "kp_alg_equation_system": ("代入消元法", "加减消元法"),
    "kp_alg_quadratic_equation": ("配方法",),
    "kp_geo_construction": ("尺规作角平分线", "作线段", "作垂线"),
    "kp_syn_reading": ("阅读理解题", "新定义问题"),
}

# 来源目录里这些名称表达同一知识身份。左侧仍保留为隐藏旧词，
# 右侧才是新任务可见的规范词；其中优先复用现有核心知识稳定身份。
KNOWLEDGE_CANONICAL_NAME_MAP = {
    "一元一次不等式的应用": "一元一次不等式应用",
    "一元一次不等式组的应用": "一元一次不等式应用",
    "实际问题与一元一次方程": "一元一次方程应用",
    "一次函数的实际应用": "一次函数应用",
    "实际问题与二元一次方程组": "二元一次方程组应用",
    "二元一次方程组的应用": "二元一次方程组应用",
    "解二元一次方程组的应用": "二元一次方程组应用",
    "函数的图象": "函数图像",
    "一次函数的图象": "函数图像",
    "图形的相似": "图形相似",
    "图形的变换": "图形变换",
    "三角形全等的判定": "三角形全等",
    "全等三角形的概念及性质": "三角形全等",
    "分式方程的实际应用": "分式方程的应用",
    "解直角三角形的应用": "解直角三角形及其应用",
}

# 这些来源节点描述“做一个动作”，应由知识父节点与方法/思想组合表达，
# 不再单独成为知识规范词。旧名称和旧 ID 仍会映射到安全的知识父节点。
KNOWLEDGE_PROCEDURE_NAMES = {
    "举反例",
    "分析图案的形成过程",
    "列二元一次方程组",
    "求一次函数解析式",
    "求反比例函数解析式",
    "画三视图",
    "画旋转图形",
    "画轴对称图形",
    "解一元一次不等式",
    "解一元一次不等式组",
    "解二元一次方程组",
    "解二元一次方程组的应用",
    "解分式方程（化为一元一次）",
    "解分式方程（化为一元二次）",
    "设计轴对称图案",
}


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


def _unique(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = _normalized(text)
        if text and key and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _term(
    term_id: str,
    dimension: str,
    name: str,
    *,
    aliases: Iterable[str] = (),
    retrieval_hints: Iterable[str] = (),
    legacy_names: Iterable[str] = (),
    legacy_ids: Iterable[str] = (),
    origin: str,
    source_paths: Iterable[Iterable[str]] = (),
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": term_id,
        "dimension": dimension,
        "name": name,
        "aliases": _unique(aliases),
        "status": "approved",
        "origin": origin,
        "source_paths": [list(path) for path in source_paths],
    }
    for field, values in (
        ("retrieval_hints", retrieval_hints),
        ("legacy_names", legacy_names),
        ("legacy_ids", legacy_ids),
    ):
        items = _unique(values)
        if items:
            result[field] = items
    return result


def _add_metadata(
    term: dict[str, Any],
    *,
    source_path: Iterable[str] = (),
    legacy_name: str = "",
    legacy_id: str = "",
) -> None:
    path = [str(item).strip() for item in source_path if str(item).strip()]
    if path and path not in term["source_paths"]:
        term["source_paths"].append(path)
    if legacy_name and _normalized(legacy_name) not in {
        _normalized(term["name"]),
        *(_normalized(value) for value in term["aliases"]),
    }:
        term.setdefault("legacy_names", [])
        term["legacy_names"] = _unique([*term["legacy_names"], legacy_name])
    if legacy_id and legacy_id != term["id"]:
        term.setdefault("legacy_ids", [])
        term["legacy_ids"] = _unique([*term["legacy_ids"], legacy_id])


def _curriculum_terms(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for volume in payload.get("volumes", []):
        volume_label = str(volume.get("label") or "").strip()
        for chapter in volume.get("chapters", []):
            chapter_id = str(chapter.get("id") or "").strip()
            chapter_label = str(chapter.get("label") or "").strip()
            if (
                str(chapter.get("kind") or "").strip() != "chapter"
                or not volume_label
                or not chapter_id
                or not re.match(
                    r"^第[一二三四五六七八九十百0-9]+章(?:\s|$)", chapter_label
                )
            ):
                continue
            name = f"{volume_label} {chapter_label}"
            result.append(
                _term(
                    chapter_id,
                    "curriculum",
                    name,
                    aliases=(name.replace(" ", ""),),
                    origin="bnu_2024_catalog",
                    source_paths=((volume_label, chapter_label),),
                )
            )
    return result


def _controlled_terms() -> list[dict[str, Any]]:
    result = [
        _term(
            item.canonical_id.casefold(),
            "knowledge",
            item.canonical_name,
            aliases=item.aliases,
            retrieval_hints=KNOWLEDGE_RETRIEVAL_HINTS.get(
                item.canonical_id.casefold(), ()
            ),
            origin="p3_registry",
        )
        for item in CANONICAL_KNOWLEDGE
    ]
    for dimension, specs in (
        ("ability", ((name, ()) for name in ABILITY_TERMS)),
        ("method", METHOD_SPECS),
        ("thought", THOUGHT_SPECS),
        ("model", MODEL_SPECS),
        ("special_type", SPECIAL_TYPE_SPECS),
    ):
        result.extend(
            _term(
                _stable_id(dimension, name),
                dimension,
                name,
                aliases=aliases,
                origin="teacher_governed_2026_08",
            )
            for name, aliases in specs
        )
    return result


def _knowledge_node_is_canonical(node: dict[str, Any]) -> bool:
    path = [str(item).strip() for item in node.get("path", []) if str(item).strip()]
    name = str(node.get("name") or "").strip()
    level = int(node.get("level") or len(path))
    return bool(
        path
        and path[0] in CORE_KNOWLEDGE_ROOTS
        and (level in (2, 3) or (level == 4 and not bool(node.get("leaf"))))
        and name
        and len(name) <= 24
        and name not in KNOWLEDGE_PROCEDURE_NAMES
        and not name.endswith("法")
        and not any(token in name for token in KNOWLEDGE_EXCLUDED_TOKENS)
    )


def _identity_index(
    terms: Iterable[dict[str, Any]], *, dimension: str | None = None
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for term in terms:
        if dimension is not None and term["dimension"] != dimension:
            continue
        for value in (term["name"], *term.get("aliases", [])):
            key = _normalized(value)
            owner = result.get(key)
            if key and owner is not None and owner["id"] != term["id"]:
                raise ValueError(f"Ambiguous canonical identity: {value}")
            if key:
                result[key] = term
    return result


def _govern_knowledge(
    source: dict[str, Any], terms: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    nodes = [
        node
        for node in source.get("knowledge", {}).get("nodes", [])
        if isinstance(node, dict)
    ]
    selected = sorted(
        (node for node in nodes if _knowledge_node_is_canonical(node)),
        key=lambda item: tuple(item.get("path", [])),
    )
    knowledge_index = _identity_index(terms, dimension="knowledge")
    path_targets: dict[tuple[str, ...], dict[str, Any]] = {}
    for node in selected:
        path = tuple(str(item).strip() for item in node.get("path", []))
        source_name = str(node.get("name") or "").strip()
        name = KNOWLEDGE_CANONICAL_NAME_MAP.get(source_name, source_name)
        term = knowledge_index.get(_normalized(name))
        if term is None:
            term = _term(
                (
                    _stable_id("xkw-knowledge-canonical", name)
                    if name != source_name
                    else _stable_id("xkw-knowledge", *path)
                ),
                "knowledge",
                name,
                origin="xkw_2026_governed_concept",
                source_paths=(path,),
            )
            terms.append(term)
            knowledge_index[_normalized(name)] = term
        else:
            _add_metadata(term, source_path=path)
        path_targets[path] = term

    reference_candidates: list[dict[str, Any]] = []
    for node in nodes:
        path = tuple(str(item).strip() for item in node.get("path", []) if str(item).strip())
        name = str(node.get("name") or "").strip()
        if not path or not name:
            continue
        if path[0] in EXCLUDED_ROOTS or path[0] not in CORE_KNOWLEDGE_ROOTS:
            reference_candidates.append(
                {
                    "name": name,
                    "source_path": list(path),
                    "reason": "超出当前七至九年级正式词表范围",
                }
            )
            continue
        canonical_name = KNOWLEDGE_CANONICAL_NAME_MAP.get(name, name)
        term = knowledge_index.get(_normalized(canonical_name))
        if term is None:
            for length in range(len(path) - 1, 0, -1):
                term = path_targets.get(path[:length])
                if term is not None:
                    break
        if term is None:
            reference_candidates.append(
                {
                    "name": name,
                    "source_path": list(path),
                    "reason": "没有可安全归并的规范知识点",
                }
            )
            continue
        _add_metadata(
            term,
            source_path=path if path in path_targets else (),
            legacy_name=name,
            legacy_id=_stable_id("xkw-knowledge", *path),
        )
        if path not in path_targets:
            reference_candidates.append(
                {
                    "name": name,
                    "source_path": list(path),
                    "reason": f"已归并至规范知识点：{term['name']}",
                }
            )
    return reference_candidates


def _legacy_target(
    terms: list[dict[str, Any]], name: str, root: str
) -> dict[str, Any] | None:
    by_dimension = {
        dimension: _identity_index(terms, dimension=dimension)
        for dimension in ("method", "thought", "model", "special_type", "knowledge")
    }
    if root == "压轴题":
        return by_dimension["special_type"].get(_normalized(name))
    for dimension in ("method", "thought", "model", "knowledge"):
        term = by_dimension[dimension].get(_normalized(name))
        if term is not None:
            return term
    mapped_name = MODEL_LEGACY_MAP.get(name)
    if mapped_name:
        return by_dimension["model"].get(_normalized(mapped_name))
    return None


def _govern_methods_source(
    source: dict[str, Any], terms: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for node in source.get("methods", {}).get("nodes", []):
        if not isinstance(node, dict) or not bool(node.get("leaf")):
            continue
        path = tuple(str(item).strip() for item in node.get("path", []) if str(item).strip())
        name = str(node.get("name") or "").strip()
        if not path or not name:
            continue
        target = _legacy_target(terms, name, path[0])
        old_dimension = (
            "method"
            if path[0] == "数学方法" or any(token in name for token in OLD_METHOD_HINTS)
            else "model"
        )
        if target is None:
            candidates.append(
                {
                    "name": name,
                    "source_path": list(path),
                    "reason": "网络叫法缺少稳定定义，未进入正式词表",
                }
            )
            continue
        _add_metadata(
            target,
            source_path=path,
            legacy_name=name,
            legacy_id=_stable_id(f"xkw-{old_dimension}", *path),
        )
    return candidates


def _validate_terms(terms: list[dict[str, Any]]) -> None:
    ids: set[str] = set()
    identities: dict[tuple[str, str], str] = {}
    for term in terms:
        if term["id"] in ids:
            raise ValueError(f"Duplicate term id: {term['id']}")
        ids.add(term["id"])
        term["aliases"] = _unique(term.get("aliases", []))
        for field in ("retrieval_hints", "legacy_names", "legacy_ids"):
            if field in term:
                term[field] = _unique(term[field])
                if not term[field]:
                    term.pop(field)
        term["source_paths"] = sorted(
            {tuple(path) for path in term.get("source_paths", [])}
        )
        term["source_paths"] = [list(path) for path in term["source_paths"]]
        for value in (term["id"], term["name"], *term["aliases"]):
            key = (term["dimension"], _normalized(value))
            owner = identities.get(key)
            if owner is not None and owner != term["id"]:
                raise ValueError(f"Ambiguous term identity: {value}")
            identities[key] = term["id"]

    separated: dict[str, set[str]] = {}
    for dimension in ("method", "thought", "model"):
        separated[dimension] = {
            _normalized(value)
            for term in terms
            if term["dimension"] == dimension
            for value in (term["name"], *term["aliases"])
        }
    for left, right in (("method", "thought"), ("method", "model"), ("thought", "model")):
        overlap = separated[left] & separated[right]
        if overlap:
            raise ValueError(f"Cross-dimension identities overlap: {left}/{right}: {overlap}")


def build() -> dict[str, Any]:
    source = _read_json(SOURCE_PATH)
    curriculum = _read_json(CURRICULUM_PATH)
    terms = [*_curriculum_terms(curriculum), *_controlled_terms()]
    reference_candidates = [
        *_govern_knowledge(source, terms),
        *_govern_methods_source(source, terms),
    ]
    _validate_terms(terms)
    terms.sort(key=lambda item: (DIMENSIONS.index(item["dimension"]), item["name"], item["id"]))
    reference_candidates.sort(
        key=lambda item: (tuple(item["source_path"]), item["name"], item["reason"])
    )

    counts = {dimension: 0 for dimension in DIMENSIONS}
    for term in terms:
        counts[term["dimension"]] += 1
    if counts["curriculum"] != 25 or counts["ability"] != 10 or counts["special_type"] != 14:
        raise ValueError(f"Protected dimensions changed unexpectedly: {counts}")
    if not 250 <= counts["knowledge"] <= 450:
        raise ValueError(f"Knowledge governance produced an unsafe count: {counts}")
    if not 15 <= counts["method"] <= 35 or not 10 <= counts["thought"] <= 20:
        raise ValueError(f"Method/thought governance produced an unsafe count: {counts}")
    if not 25 <= counts["model"] <= 50:
        raise ValueError(f"Model governance produced an unsafe count: {counts}")

    return {
        "schema_version": 2,
        "catalog_id": "junior-math-controlled-vocabulary-v2",
        "revision": 3,
        "source_snapshot": {
            "file": SOURCE_PATH.name,
            "observed_at": source.get("source", {}).get("observed_at"),
            "knowledge_node_count": len(source.get("knowledge", {}).get("nodes", [])),
            "method_node_count": len(source.get("methods", {}).get("nodes", [])),
            "runtime_refresh": False,
        },
        "classification_policy": {
            "dimensions": list(DIMENSIONS),
            "protected_dimensions": ["curriculum", "ability", "special_type"],
            "governed_dimensions": ["knowledge", "method", "thought", "model"],
            "excluded_branches": sorted(EXCLUDED_ROOTS),
            "rule": (
                "来源快照只提供检索素材；正式知识点只保留稳定概念层级，"
                "细粒度旧词归并为隐藏检索键。解题方法、数学思想和数学模型"
                "必须分类明确，模型只选择本题候选中的正式名称。"
            ),
            "dimension_definitions": {
                "knowledge": "题目直接考查的概念、性质、定理、公式或运算规则。",
                "method": "可重复执行的具体解题程序或构造办法。",
                "thought": "跨主题复用的通用思考策略。",
                "model": "具有明确条件和关系、可重复识别的稳定结构。",
            },
            "retrieval_hints": {
                "match_fields": [
                    "id",
                    "name",
                    "aliases",
                    "retrieval_hints",
                    "legacy_names",
                    "legacy_ids",
                    "source_paths",
                ],
                "preferred_model_output": "approved_term_id",
                "legacy_values_are_model_candidates": False,
                "free_text_fallback": {
                    "allowed": True,
                    "maximum_per_question": 1,
                    "local_full_vocabulary_rematch": True,
                    "unmatched_destination": "periodic_review",
                },
            },
        },
        "terms": terms,
        "reference_candidates": reference_candidates,
    }


def main() -> None:
    payload = build()
    _write_json(OUTPUT_PATH, payload)
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
