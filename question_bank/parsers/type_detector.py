from __future__ import annotations

import re

# 题干开头的分值标记，如“（12分）”。高分值是解答题的可靠信号。
_LEADING_SCORE_RE = re.compile(r"^\s*[（(]\s*(\d+)\s*分\s*[)）]")
# 规范小问标记：（1）（2）成对出现才算小问；括号数字候选由
# _is_subq_marker_context 按上下文排除公式下标、指数、分数、函数参数
# 和图表/编号引用。
_SUBQ_CANDIDATE_RE = re.compile(r"[（(]\s*(\d{1,2})\s*[)）]")

# 括号数字前紧邻这些字符时属于编号或引用，不是小问：
# 图(1) 表(1) 式(1) 项(1) 空(1) 例(1) 号(1) 题(1) 问(1) 组(1) 班(1) 章(1) 节(1) 卷(1) 款(1)
_CJK_REF_PREFIX_CHARS = "图表式项空例号题问班组章节卷款"


def _is_subq_marker_context(text: str, start: int, end: int) -> bool:
    """区分真实小问编号与公式下标、指数、分数、函数参数和图表引用。"""
    # 只跨越行内空白，不把上一行末尾的字母吞成下一小问的公式前缀。
    prefix = text[:start].rstrip(" \t\u3000")
    suffix = text[end:].lstrip(" \t\u3000")
    prev = prefix[-1:] if prefix else ""
    nxt = suffix[:1]
    if (prev and prev in "/0123456789") or nxt == "/":
        # (1)/(2) 分数写法，以及 2(1) 这类系数紧邻。
        return False
    if prev == "_":
        # S_(1) 是下标；____（2）这类 2 个及以上下划线是填空后的真小问。
        run = 0
        cursor = len(prefix) - 1
        while cursor >= 0 and text[cursor] == "_":
            run += 1
            cursor -= 1
        if run < 2:
            return False
    elif (prev and prev in "^{") or (prev.isascii() and prev.isalpha()):
        # x^(1)、x^{(1)} 上标，f(1)、sin(1) 函数参数。
        return False
    if prev and prev in _CJK_REF_PREFIX_CHARS:
        # 图(1)、式(1)、题(1) 等图表或编号引用。
        return False
    if (nxt and nxt in "班组题式问") or text[end : end + 2] == "年级":
        # (1)班、(1)组、(1)年级、(1)题 是编号引用；(1)式、(1)问 是公式/小问引用。
        return False
    return True


def subq_mark_labels(text: object, *, max_label: int = 19) -> tuple[str, ...]:
    """按出现顺序返回去重后的真实小问编号（已过上下文过滤）。"""
    value = str(text or "")
    labels: list[str] = []
    for match in _SUBQ_CANDIDATE_RE.finditer(value):
        if not _is_subq_marker_context(value, match.start(), match.end()):
            continue
        number = int(match.group(1))
        if 1 <= number <= max_label:
            label = str(number)
            if label not in labels:
                labels.append(label)
    return tuple(labels)

# 分值达到该阈值时，即使题干含填空信号也按解答题处理。
_FULL_SOLUTION_SCORE_THRESHOLD = 6

# 题型封闭枚举：本检测器的全部合法输出，也是联合分析题型建议的唯一合法取值。
# 解答题的"画图/计算/证明"子类不再是题型枚举，由 detect_essay_subtype
# 产出并写入 special_type 标签。
QUESTION_TYPES = (
    "选择题",
    "多选题",
    "填空题",
    "解答题",
)

# 解答题子类标签的封闭取值（special_type 标签维度）。
ESSAY_SUBTYPES = ("画图", "计算", "证明")

# 兼容输入：归一前的旧六值子类题型 → （归一大类，子类标签值）。
_ESSAY_SUBTYPE_BY_LEGACY_TYPE = {
    "解答题（画图）": "画图",
    "解答题（计算）": "计算",
    "解答题（证明）": "证明",
}

# 子类识别规则（宁缺毋滥、猜就猜准）：
# 画图=强作图信号，优先级最高（带尺规作图的证明也算画图）；
# 证明=强说理信号；计算=开头直接计算指令；均不命中则不标注。
_DRAW_SUBTYPE_RE = re.compile(r"作图|画出|尺规|网格作图|绘制图形|在图中标出")
_PROOF_SUBTYPE_RE = re.compile(
    r"证明|求证|试说明|说明理由|理由如下|判定[^。；！？?\n]{0,24}是否"
)
# 计算只认开头指令式（允许题号、分值、小问标记等排版前缀），
# 题面中段的“计算/求解”等叙述不算信号，避免误伤。
_CALC_SUBTYPE_RE = re.compile(
    r"^\s*(?:\d+\s*[.．、]\s*)?"
    r"(?:[（(]\s*\d+\s*分\s*[)）]\s*)?"
    r"(?:[（(]\d+[)）]\s*)*"
    r"(?:计算|化简|求值|解方程|解不等式|解下列)"
)


def split_legacy_question_type(value: object) -> tuple[str | None, str | None]:
    """兼容输入：旧六值题型拆成归一后的四类题型与子类标签值。

    非旧子类值原样返回（子类为 None）；空输入返回 (None, None)。
    """

    text = str(value or "").strip()
    subtype = _ESSAY_SUBTYPE_BY_LEGACY_TYPE.get(text)
    if subtype is not None:
        return "解答题", subtype
    return (text or None), None


def detect_essay_subtype(question_text: object) -> str | None:
    """识别解答题子类：'画图' | '计算' | '证明' | None（未标注）。

    宁缺毋滥：只有强信号才打标，拿不准一律 None，留待人工或 AI 打标订正。
    """

    text = str(question_text or "")
    if not text.strip():
        return None
    if _DRAW_SUBTYPE_RE.search(text):
        return "画图"
    if _PROOF_SUBTYPE_RE.search(text):
        return "证明"
    if _CALC_SUBTYPE_RE.search(text):
        return "计算"
    return None


def _looks_like_full_solution(text: str) -> bool:
    """判断一道题是否明显是解答大题（用于压制填空误判）。

    docx 解析后解答题的作答下划线常变成 <u>　　</u>，全角空格被保留，
    会误触填空分支；分值与小问是更可靠的反向信号。
    """
    score_match = _LEADING_SCORE_RE.match(text)
    if score_match and int(score_match.group(1)) >= _FULL_SOLUTION_SCORE_THRESHOLD:
        return True
    marks = set(subq_mark_labels(text))
    return {"1", "2"} <= marks


def detect_question_type(question_text: str, current_type: str | None = None) -> str:
    """
    Robust junior high school math question type detector.
    Classifies a question into exactly one of:
      - 选择题 (Choice Questions)
      - 多选题 (Multi-Choice Questions)
      - 填空题 (Fill in the Blank)
      - 解答题 (Solution; 子类"画图/计算/证明"见 detect_essay_subtype)
    """
    # 0. If current type is already specified and is highly granular, keep it!
    val = str(current_type or "").strip()
    if val in QUESTION_TYPES and val != "解答题":
        return val
    # 兼容输入：归一前的旧子类题型按大类"解答题"保留，子类由标签表达。
    if val in _ESSAY_SUBTYPE_BY_LEGACY_TYPE:
        return "解答题"

    text = str(question_text or "")

    # 1. Check choice questions (选择题 / 多选题)
    # Check for option prefixes (e.g., A. B. C. D. or A、 B、 C、 D、 or (A) (B) (C) (D))
    found_options = set()
    for opt in ("A", "B", "C", "D"):
        # Match option character:
        # - Vertex or label check: must be preceded by start of line, space, or punctuation,
        #   and followed by a dot (., ．), backslash/comma (、), parenthesis, or at least two spaces.
        if re.search(rf"(?:^|[\s\u3000,\(\)（）【】])(?:{opt})(?:[\.．、\)]|）|\s{{2,}})", text):
            found_options.add(opt)

    is_choice = len(found_options) >= 3

    if not is_choice:
        # Fallback to simple matching if option patterns are very standard
        has_a = any(x in text for x in ("A．", "A.", "A、", "(A)", "（A）"))
        has_b = any(x in text for x in ("B．", "B.", "B、", "(B)", "（B）"))
        has_c = any(x in text for x in ("C．", "C.", "C、", "(C)", "（C）"))
        has_d = any(x in text for x in ("D．", "D.", "D、", "(D)", "（D）"))
        if sum([has_a, has_b, has_c, has_d]) >= 3:
            is_choice = True

    if is_choice:
        if "多选" in text or "双选" in text:
            return "多选题"
        return "选择题"

    # 2. Check fill in the blank (填空题)
    is_blank = False
    # Match standard underscore blanks: e.g. ___ or ______
    if re.search(r"_{2,}", text):
        is_blank = True
    # Match empty parentheses: e.g. (  ) or （  ） or （ ）
    elif re.search(r"\(\s*\)", text) or re.search(r"（\s*[　\s]*\s*）", text):
        is_blank = True
    # Match keyword "填空" or Chinese full-width space "　"
    elif "填空" in text or "　" in text:
        is_blank = True

    if is_blank:
        if _looks_like_full_solution(text):
            # 高分值或带 (1)(2) 小问的题是解答题，填空信号来自排版残留，跳过填空分支。
            is_blank = False
        else:
            return "填空题"

    # 3. 其余一律归"解答题"；画图/计算/证明子类由 detect_essay_subtype 打标签。
    return "解答题"


# Grading-rubric (LLM) question types mapped onto the question-bank enum.
# The rubric enum has no drawing/multi-choice granularity, so calculation,
# proof and comprehensive all fall back to the generic 解答题；解答题子类
# 由导入链路的 detect_essay_subtype 依据题干另行打标。
_RUBRIC_QUESTION_TYPE_MAP = {
    "choice": "选择题",
    "fill_blank": "填空题",
    "calculation": "解答题",
    "proof": "解答题",
    "comprehensive": "解答题",
}


def question_type_from_rubric(value: object) -> str | None:
    """Map a grading-rubric question type to the question-bank type enum."""
    return _RUBRIC_QUESTION_TYPE_MAP.get(str(value or "").strip().casefold())
