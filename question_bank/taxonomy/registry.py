from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class CanonicalKnowledge:
    canonical_id: str
    canonical_name: str
    aliases: tuple[str, ...]


CANONICAL_KNOWLEDGE: tuple[CanonicalKnowledge, ...] = (
    CanonicalKnowledge(
        canonical_id="KP_GEO_PARALLEL_LINES",
        canonical_name="相交线与平行线",
        aliases=(
            "相交线与平行线",
            "平行线性质",
            "平行线的性质",
            "平行线判定",
            "平行线的判定",
            "平行线角度",
            "两直线平行",
            "C2_04",
            "C2_05",
            "C2_06",
        ),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_TRIANGLE_CONGRUENCE",
        canonical_name="三角形全等",
        aliases=("三角形全等", "全等三角形", "全等三角形的判定", "全等三角形的性质", "C_CONGRUENT_TRIANGLES"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_ISOSCELES_TRIANGLE",
        canonical_name="等腰三角形",
        aliases=("等腰三角形", "等腰三角形性质", "等腰三角形的性质", "等边对等角", "C2_11"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_AXIS_SYMMETRY",
        canonical_name="轴对称",
        aliases=("轴对称", "生活中的轴对称", "折叠", "对称轴"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_QUADRILATERAL",
        canonical_name="四边形",
        aliases=("四边形", "平行四边形", "特殊平行四边形", "矩形", "菱形", "正方形"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_SIMILARITY",
        canonical_name="图形相似",
        aliases=("图形相似", "相似三角形", "相似", "比例线段"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_ALG_EQUATION_SYSTEM",
        canonical_name="二元一次方程组",
        aliases=("二元一次方程组", "二元一次方程", "方程组", "ALG_02"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_ALG_INEQUALITY_SYSTEM",
        canonical_name="一元一次不等式组",
        aliases=("一元一次不等式组", "一元一次不等式", "不等式组"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_ALG_POLYNOMIAL",
        canonical_name="整式运算",
        aliases=("整式运算", "整式的乘除", "完全平方公式", "平方差公式", "因式分解"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_FUN_FUNCTION_GRAPH",
        canonical_name="函数图像",
        aliases=("函数图像", "函数图象", "一次函数图像", "一次函数图象", "反比例函数图像", "二次函数图像", "FUN_01"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_PROBABILITY",
        canonical_name="概率初步",
        aliases=("概率初步", "概率"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_DATA_ANALYSIS",
        canonical_name="数据分析",
        aliases=("数据分析", "统计图", "平均数", "中位数", "众数"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_COMPREHENSIVE",
        canonical_name="几何综合",
        aliases=("几何综合", "几何压轴", "几何探究", "综合几何"),
    ),
)

GRADING_ERROR_TO_BANK_ERROR: dict[str, str] = {
    "概念理解错误": "概念理解不清",
    "计算错误": "运算化简错误",
    "审题错误": "题意阅读偏差",
    "条件遗漏": "条件识别不完整",
    "逻辑断裂": "书写依据不完整",
    "表达不规范": "书写依据不完整",
    "未作答": "题意阅读偏差",
    "多选失分": "题意阅读偏差",
    "作废答案": "书写依据不完整",
    "提示注入": "题意阅读偏差",
    "答案不等价": "概念理解不清",
    "其他": "综合建模困难",
}

ERROR_KEYWORD_MAP: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("条件识别不完整", ("条件", "漏用", "漏找", "已知", "对应关系")),
    ("概念理解不清", ("概念", "定义", "本质", "理解")),
    ("公式/定理误用", ("公式", "定理", "性质", "判定", "误用")),
    ("运算化简错误", ("计算", "运算", "化简", "代入", "符号")),
    ("图形关系识别错误", ("图形", "角关系", "边关系", "位置关系", "读图")),
    ("辅助线思路缺失", ("辅助线", "构造")),
    ("分类讨论遗漏", ("分类", "讨论")),
    ("数形转化困难", ("数形", "坐标", "图像", "图象")),
    ("题意阅读偏差", ("审题", "题意", "阅读")),
    ("书写依据不完整", ("书写", "依据", "证明", "逻辑", "断裂")),
    ("综合建模困难", ("建模", "模型", "综合")),
)

def canonicalize_knowledge(value: object) -> CanonicalKnowledge | None:
    text = _text(value)
    if not text:
        return None
    exact = _ALIAS_INDEX.get(_normalize(text))
    if exact is not None:
        return exact
    normalized = _normalize(text)
    for alias, item in _ALIAS_INDEX.items():
        if alias and alias in normalized:
            return item
    return None


def canonicalize_knowledge_values(values: Iterable[object]) -> CanonicalKnowledge | None:
    for value in values:
        item = canonicalize_knowledge(value)
        if item is not None:
            return item
    return None


def canonicalize_error_type(value: object) -> str:
    text = _text(value)
    if not text:
        return ""
    if text in GRADING_ERROR_TO_BANK_ERROR:
        return GRADING_ERROR_TO_BANK_ERROR[text]
    for category, keywords in ERROR_KEYWORD_MAP:
        if any(keyword in text for keyword in keywords):
            return category
    return text


def canonical_knowledge_options() -> list[str]:
    return [item.canonical_name for item in CANONICAL_KNOWLEDGE]


def canonical_knowledge_seed_rows() -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "canonical_key": item.canonical_id,
            "name": item.canonical_name,
            "aliases": item.aliases,
        }
        for item in CANONICAL_KNOWLEDGE
    )


def _normalize(value: object) -> str:
    return re.sub(r"[\s\W_]+", "", _text(value)).casefold()


def _text(value: object) -> str:
    return str(value or "").strip()


_ALIAS_INDEX = {
    _normalize(alias): item
    for item in CANONICAL_KNOWLEDGE
    for alias in (item.canonical_id, item.canonical_name, *item.aliases)
}


def get_parent_knowledge_category(knowledge_point: str) -> str:
    kp = str(knowledge_point or "").strip()
    if not kp:
        return ""
    
    # 1. First check if it matches existing canonical knowledge mapping
    canonical = canonicalize_knowledge(kp)
    if canonical is not None:
        return canonical.canonical_name
        
    # 2. Keyword-based heuristics to merge child/sub-knowledge points to parent categories
    if any(k in kp for k in ("二次函数", "抛物线")):
        return "二次函数"
    if "反比例" in kp:
        return "反比例函数"
    if any(k in kp for k in ("一次函数", "正比例")):
        return "一次函数"
    if "函数" in kp:
        return "函数初步"
    if any(k in kp for k in ("方程", "方程组", "解方程")):
        return "方程与方程组"
    if "不等式" in kp:
        return "不等式与不等式组"
    if any(k in kp for k in ("整式", "因式分解", "完全平方", "平方差", "幂")):
        return "整式与因式分解"
    if "分式" in kp:
        return "分式"
    if any(k in kp for k in ("有理数", "无理数", "实数", "相反数", "绝对值", "平方根", "算术平方根", "立方根", "估算", "数轴", "代数式", "单项式", "多项式", "科学记数")):
        return "数与式（实数）"
    if any(k in kp for k in ("相似", "位似", "比例线段")):
        return "图形相似"
    if any(k in kp for k in ("全等", "全等三角形")):
        return "三角形全等"
    if any(k in kp for k in ("等腰", "等边", "直角", "勾股", "中线", "角平分线", "线段垂直平分线", "高线", "三角形")):
        return "三角形"
    if any(k in kp for k in ("矩形", "菱形", "正方形", "平行四边形", "四边形")):
        return "四边形"
    if any(k in kp for k in ("圆", "切线", "弦", "弧", "扇形", "圆心角", "圆周角", "垂径定理")):
        return "圆"
    if any(k in kp for k in ("平行线", "相交线", "同位角", "内错角", "同旁内角", "对顶角", "垂直", "垂线")):
        return "相交线与平行线"
    if any(k in kp for k in ("对称", "平移", "旋转", "折叠", "投影", "视图")):
        return "图形与变换"
    if any(k in kp for k in ("统计", "概率", "中位数", "众数", "平均数", "方差", "样本", "频数", "频率", "图")):
        return "统计与概率"
        
    return kp

