from __future__ import annotations

import re

def detect_question_type(question_text: str, current_type: str | None = None) -> str:
    """
    Robust junior high school math question type detector.
    Classifies a question into:
      - 选择题 (Choice Questions)
      - 多选题 (Multi-Choice Questions)
      - 填空题 (Fill in the Blank)
      - 解答题（计算）(Calculation solution)
      - 解答题（证明）(Proof solution)
      - 解答题（画图）(Drawing solution)
      - 解答题 (Generic solution)
    """
    # 0. If current type is already specified and is highly granular, keep it!
    val = str(current_type or "").strip()
    if val and val not in ("未知", "解答题", ""):
        return val

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
        return "填空题"

    # 3. Check subjective subcategories (解答题 - 计算、证明、画图)
    # Drawing check:
    draw_keywords = ("画", "作图", "画出", "尺规作图", "平移", "旋转", "投影", "对称", "网格", "绘制", "直角坐标系")
    if any(x in text for x in draw_keywords):
        return "解答题（画图）"

    # Proof check:
    proof_keywords = ("证明", "求证", "说明理由", "是否全等", "判定理由", "理由如下", "垂直", "平行", "判定")
    if any(x in text for x in proof_keywords):
        return "解答题（证明）"

    # Calculation check:
    calc_keywords = ("计算", "化简", "求值", "解方程", "解不等式", "求下列各值", "代数式", "计算题", "求得", "求解")
    if any(x in text for x in calc_keywords):
        return "解答题（计算）"

    # Default fallback
    return "解答题"


# Grading-rubric (LLM) question types mapped onto the question-bank enum.
# The rubric enum has no drawing/multi-choice granularity, so comprehensive
# falls back to the generic 解答题.
_RUBRIC_QUESTION_TYPE_MAP = {
    "choice": "选择题",
    "fill_blank": "填空题",
    "calculation": "解答题（计算）",
    "proof": "解答题（证明）",
    "comprehensive": "解答题",
}


def question_type_from_rubric(value: object) -> str | None:
    """Map a grading-rubric question type to the question-bank type enum."""
    return _RUBRIC_QUESTION_TYPE_MAP.get(str(value or "").strip().casefold())
