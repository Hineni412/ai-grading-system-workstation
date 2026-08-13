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
    # --- 数与代数 ---
    CanonicalKnowledge(
        canonical_id="KP_ALG_REAL_NUMBERS",
        canonical_name="实数",
        aliases=("有理数", "有理数运算", "无理数", "相反数", "绝对值", "平方根", "算术平方根", "立方根", "数轴", "整数的概念", "整数"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_ALG_LETTER_NUMBER",
        canonical_name="字母表示数",
        aliases=("字母表示数", "用字母表示数", "代数式", "代数式化简", "单项式", "多项式", "科学记数法", "科学记数"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_ALG_POLYNOMIAL",
        canonical_name="整式运算",
        aliases=("整式加减", "整式乘除", "幂的运算", "完全平方公式", "平方差公式", "整式的加减", "整式的乘除", "整式的基本概念", "合并同类项", "同类项"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_ALG_POWER_RULES",
        canonical_name="整数指数幂",
        aliases=("整数指数幂", "零指数幂", "负整数指数幂", "乘方运算", "乘方运算法则", "幂的基本概念", "幂的运算性质", "幂的乘方", "积的乘方", "同底数幂", "幂的运算法则"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_ALG_EQUATION_PROPERTIES",
        canonical_name="等式的性质",
        aliases=("等式性质", "等式的性质", "方程基础", "方程思想"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_ALG_FACTORIZATION",
        canonical_name="因式分解",
        aliases=("因式分解", "提公因式法", "公式法因式分解"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_ALG_FRACTION",
        canonical_name="分式",
        aliases=("分式的基本性质", "分式运算", "分式的运算", "分数的意义", "分数"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_ALG_RADICAL",
        canonical_name="二次根式",
        aliases=("二次根式", "最简二次根式", "二次根式化简", "二次根式的运算", "根式计算"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_ALG_LINEAR_EQUATION",
        canonical_name="一元一次方程",
        aliases=("一元一次方程", "解一元一次方程", "一元一次方程的解法"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_ALG_EQUATION_SYSTEM",
        canonical_name="二元一次方程组",
        aliases=("二元一次方程组", "二元一次方程", "方程组", "代入消元法", "加减消元法", "ALG_02"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_ALG_QUADRATIC_EQUATION",
        canonical_name="一元二次方程",
        aliases=("一元二次方程", "解一元二次方程", "配方法", "求根公式", "根的判别式", "韦达定理", "一元二次方程的解法"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_ALG_INEQUALITY_SYSTEM",
        canonical_name="一元一次不等式组",
        aliases=("一元一次不等式组", "一元一次不等式", "不等式组", "不等式的性质", "一元一次不等式解法"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_ALG_LINEAR_EQUATION_APPL",
        canonical_name="一元一次方程应用",
        aliases=("方程应用题", "行程问题", "工程问题", "利润问题", "分配问题", "路程速度时间", "路程、速度、时间的数量关系", "路程速度时间关系", "盈亏问题", "打折销售"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_ALG_SYSTEM_APPL",
        canonical_name="二元一次方程组应用",
        aliases=("方程组应用题", "鸡兔同笼", "配套问题", "方案选择", "二元一次方程组的应用"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_ALG_INEQUALITY_APPL",
        canonical_name="一元一次不等式应用",
        aliases=("不等式应用题", "方案设计", "最优方案", "不等式与方案", "最省方案"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_ALG_FRACTION_EQUATION",
        canonical_name="分式方程及其应用",
        aliases=("分式方程", "分式方程应用", "解分式方程", "增根", "分式应用题", "分式方程的解法"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_ALG_NUMBER_SENSE",
        canonical_name="数感与估算",
        aliases=("近似数", "有效数字", "估算", "精确度", "小数点移动规律", "整数解取值应用"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_FUN_COORDINATE_SYSTEM",
        canonical_name="平面直角坐标系",
        aliases=("平面直角坐标系", "直角坐标系", "象限", "点与坐标", "坐标表示位置"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_FUN_VARIABLES",
        canonical_name="变量与函数",
        aliases=("变量", "自变量", "因变量", "变量基本概念", "变量的基本概念", "函数概念", "函数的基本概念"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_FUN_LINEAR",
        canonical_name="一次函数",
        aliases=("一次函数", "正比例函数", "一次函数图像与性质", "待定系数法"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_FUN_INVERSE",
        canonical_name="反比例函数",
        aliases=("反比例函数", "反比例函数图像与性质", "反比例函数应用", "双曲线"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_FUN_QUADRATIC",
        canonical_name="二次函数",
        aliases=("二次函数", "二次函数图像与性质", "抛物线", "二次函数顶点式"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_FUN_FUNCTION_GRAPH",
        canonical_name="函数图像",
        aliases=("函数图像", "函数图象", "一次函数图像", "一次函数图象", "反比例函数图像", "二次函数图像", "FUN_01"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_FUN_LINEAR_APPL",
        canonical_name="一次函数应用",
        aliases=("一次函数实际应用", "行程函数", "分段函数", "函数应用题", "一次函数的应用", "一次函数图像应用"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_FUN_QUADRATIC_APPL",
        canonical_name="二次函数应用",
        aliases=("二次函数应用题", "利润最大化", "抛物线应用", "面积最大化", "二次函数的实际应用"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_FUN_QUADRATIC_RELATION",
        canonical_name="二次函数与方程不等式",
        aliases=("二次函数与一元二次方程", "抛物线与x轴交点", "函数与不等式", "二次函数与方程", "抛物线与坐标轴交点"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_FUN_DYNAMIC_POINT",
        canonical_name="动点与综合函数题",
        aliases=("动点问题", "动点与函数", "存在性问题", "分类讨论动点", "动点综合题", "二次函数动点"),
    ),
    # --- 图形与几何 ---
    CanonicalKnowledge(
        canonical_id="KP_GEO_LINE_ANGLE",
        canonical_name="线段与角",
        aliases=("线段", "角", "两点之间距离", "角平分线", "度分秒换算", "余角与补角", "基本平面图形", "直线的基本性质", "三角板", "邻补角", "对顶角的性质", "垂直", "线段垂直平分线"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_CONSTRUCTION",
        canonical_name="尺规作图",
        aliases=("尺规作图", "基本尺规作图", "尺规作角平分线", "作一个角等于已知角", "作线段", "作角", "作垂线", "作线段垂直平分线"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_PARALLEL_LINES",
        canonical_name="相交线与平行线",
        aliases=("相交线与平行线", "平行线性质", "平行线的性质", "平行线判定", "平行线的判定", "平行线角度", "两直线平行", "对顶角", "C2_04", "C2_05", "C2_06"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_PROPOSITION_PROOF",
        canonical_name="命题与证明",
        aliases=("命题", "定理", "证明", "逆命题", "互逆命题", "定义", "公理", "推论", "几何证明"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_TRIANGLE_PROPERTIES",
        canonical_name="三角形性质",
        aliases=("三角形内角和", "外角性质", "三角形三边关系", "三角形中位线", "三角形高线", "三角形中线", "中线性质", "三角形的基本概念", "三角形基本概念"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_ANGLE_SUM_THEOREM",
        canonical_name="三角形内角和与外角定理",
        aliases=("三角形内角和定理", "三角形外角定理", "三角形外角的性质", "内角和", "外角和", "三角形外角性质", "三角形内角"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_TRIANGLE_AREA",
        canonical_name="三角形面积",
        aliases=("三角形面积", "三角形面积计算", "三角形面积公式", "三角形面积计算公式", "三角形面积计算方法", "面积和差计算", "图形面积计算"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_TRIANGLE_CONGRUENCE",
        canonical_name="三角形全等",
        aliases=("三角形全等", "三角形的全等", "全等三角形", "全等三角形的判定", "全等三角形的性质", "C_CONGRUENT_TRIANGLES"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_ISOSCELES_TRIANGLE",
        canonical_name="等腰三角形",
        aliases=("等腰三角形", "等腰三角形性质", "等腰三角形的性质", "等边对等角", "等角对等边", "三线合一", "等边三角形", "C2_11"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_RIGHT_TRIANGLE",
        canonical_name="直角三角形与勾股定理",
        aliases=("直角三角形", "勾股定理", "直角三角形性质", "勾股定理逆定理", "勾股数"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_QUADRILATERAL",
        canonical_name="四边形",
        aliases=("四边形", "特殊平行四边形"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_PARALLELOGRAM",
        canonical_name="平行四边形",
        aliases=("平行四边形性质", "平行四边形判定", "平行四边形的定义"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_RECTANGLE",
        canonical_name="矩形",
        aliases=("矩形性质", "矩形判定", "矩形判定定理"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_RHOMBUS",
        canonical_name="菱形",
        aliases=("菱形性质", "菱形判定", "菱形判定定理"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_SQUARE",
        canonical_name="正方形",
        aliases=("正方形性质", "正方形判定"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_QUADRILATERAL_PROPERTIES",
        canonical_name="特殊四边形性质综合",
        aliases=("中点四边形", "四边形综合", "特殊四边形综合", "四边形性质", "四边形的判定"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_AXIS_SYMMETRY",
        canonical_name="轴对称",
        aliases=("轴对称图形", "轴对称性质", "生活中的轴对称", "折叠", "对称轴", "垂直平分线性质", "折叠问题"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_TRANSFORMATION",
        canonical_name="图形变换",
        aliases=("图形变换", "图形的平移与旋转", "平移", "旋转", "中心对称", "平移的性质", "旋转的性质"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_SIMILARITY",
        canonical_name="图形相似",
        aliases=("图形相似", "相似", "比例线段", "位似"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_SIMILAR_TRIANGLE",
        canonical_name="相似三角形",
        aliases=("相似判定", "相似比", "A字型", "8字型", "相似三角形判定", "相似三角形性质", "相似三角形", "平行线截相似"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_SIMILAR_APPL",
        canonical_name="相似三角形应用",
        aliases=("相似应用", "测高问题", "相似与坐标", "位似图形", "相似的实际应用", "物高测量"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_TRIGONOMETRY",
        canonical_name="锐角三角函数与解直角三角形",
        aliases=("锐角三角函数", "正弦", "余弦", "正切", "特殊角三角函数值", "解直角三角形", "解直角三角形应用"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_CIRCLE_PROPERTIES",
        canonical_name="圆的基本性质",
        aliases=("圆的定义", "垂径定理", "圆心角与圆周角", "弧、弦、圆心角的关系", "圆周角定理", "圆", "圆的性质"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_CIRCLE_ANGLE",
        canonical_name="圆心角圆周角与垂径定理",
        aliases=("圆心角", "圆周角", "圆内接四边形", "圆内接四边形性质", "圆心角定理", "等弧", "弦与弧"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_CIRCLE_TANGENT",
        canonical_name="与圆有关的位置关系",
        aliases=("点与圆的位置关系", "直线与圆的位置关系", "切线的判定", "切线的性质", "切线长定理", "三角形内切圆"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_CIRCLE_TANGENT_APPL",
        canonical_name="切线与圆的综合",
        aliases=("切线证明", "切线综合题", "内切圆", "外接圆", "三角形内心", "三角形外心", "三角形外心作图", "切线长", "切线应用"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_CIRCLE_MEASURE",
        canonical_name="弧长与面积计算",
        aliases=("弧长公式", "扇形面积", "扇形面积公式", "圆锥侧面积"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_SECTOR_CONE",
        canonical_name="扇形弧长与圆锥",
        aliases=("弧长计算", "圆锥展开图", "扇形弧长公式", "圆锥计算", "不规则图形面积", "弓形面积", "组合图形面积"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_GEO_VIEW_PROJECTION",
        canonical_name="视图与投影",
        aliases=("三视图", "投影", "中心投影", "平行投影", "几何体的三视图", "由三视图还原几何体", "盲区"),
    ),
    # --- 统计与概率 ---
    CanonicalKnowledge(
        canonical_id="KP_STA_DATA_COLLECTION",
        canonical_name="数据的收集与整理",
        aliases=("总体与样本", "频数与频率"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_STA_DATA_DISPLAY",
        canonical_name="数据的描述",
        aliases=("条形统计图", "折线统计图", "扇形统计图", "频数分布直方图", "统计图的选择"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_STA_DATA_REPRESENTATIVE",
        canonical_name="数据的集中趋势",
        aliases=("平均数", "加权平均数", "中位数", "众数"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_STA_DATA_DISPERSION",
        canonical_name="数据的离散程度",
        aliases=("极差", "方差", "方差的计算", "标准差"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_DATA_ANALYSIS",
        canonical_name="数据分析",
        aliases=("数据分析", "统计图"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_STA_SURVEY",
        canonical_name="普查与抽样调查",
        aliases=("普查", "抽样调查", "总体", "个体", "样本容量", "调查方式", "全面调查"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_STA_ESTIMATE",
        canonical_name="用样本估计总体",
        aliases=("用样本估计总体", "样本估计", "样本估计总体", "用样本推断总体"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_STA_CHART_READING",
        canonical_name="统计图表分析",
        aliases=("统计图表", "图表分析", "圆心角计算", "补全统计图", "频数分布直方图应用", "统计图数据读取", "条形统计图数据读取"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_PROBABILITY",
        canonical_name="概率初步",
        aliases=("概率初步", "概率", "必然事件", "随机事件", "不可能事件", "列表法求概率", "树状图法"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_PROBABILITY_CALC",
        canonical_name="概率计算与应用",
        aliases=("概率计算", "列表法", "树状图", "频率与概率", "游戏公平性", "概率的应用", "用频率估计概率"),
    ),
    # --- 综合与实践 ---
    CanonicalKnowledge(
        canonical_id="KP_GEO_COMPREHENSIVE",
        canonical_name="几何综合",
        aliases=("几何综合", "几何压轴", "几何探究", "综合几何", "几何动态问题"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_FUN_COMPREHENSIVE",
        canonical_name="代数与函数综合",
        aliases=("二次函数压轴题", "代数综合", "函数几何综合", "最值问题"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_SYN_MODELING",
        canonical_name="数学建模与应用",
        aliases=("数学建模", "数学应用", "实际应用题", "跨学科应用", "综合应用题"),
    ),
    CanonicalKnowledge(
        canonical_id="KP_SYN_READING",
        canonical_name="阅读理解与规律探究",
        aliases=("阅读理解题", "规律探究", "定义新运算", "数式规律", "图形规律", "新定义问题"),
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


def canonicalize_knowledge_exact(value: object) -> CanonicalKnowledge | None:
    """只接受词表中的规范名、编码或已登记别名。

    AI 新标签入库前使用严格匹配，避免 substring 启发式把未登记的新词
    静默归到错误的筛选标签。旧调用继续使用 canonicalize_knowledge 的
    兼容性回退，不改写历史行为或历史数据。
    """
    text = _text(value)
    if not text:
        return None
    return _ALIAS_INDEX.get(_normalize(text))


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

