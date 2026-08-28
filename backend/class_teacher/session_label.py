"""场次短标签与学期链排序的纯函数。

短标签统一各端的场次展示口径：年级（七年级→七）+ 学期（上/下）
+ 考试性质（期中/期末）。考试性质先看 exam_type，缺省时从标题推断；
都推不出时只显示「七上」。occurred_on 只作登记参考，不参与排序主键。
"""

from __future__ import annotations

GRADE_ORDER = {"七年级": 7, "八年级": 8, "九年级": 9}
TERM_ORDER = {"上学期": 0, "下学期": 1}
# 同学期内期中排在期末前；其他性质（月考、模拟、未识别）排在最后。
_PHASE_ORDER = {"期中": 0, "期末": 1}


def exam_phase(exam_type: object, title: object) -> str:
    """考试性质短名：期中/期末；推不出返回空串。"""
    for source in (exam_type, title):
        text = str(source or "")
        if "期中" in text:
            return "期中"
        if "期末" in text:
            return "期末"
    return ""


def short_label(
    *,
    grade: object = None,
    term: object = None,
    exam_type: object = None,
    title: object = None,
) -> str:
    """「七上期中」式短标签；缺考试性质时退化为「七上」，缺年级/学期时返回空串。"""
    grade_short = str(grade or "").replace("年级", "")
    term_short = "上" if term == "上学期" else ("下" if term == "下学期" else "")
    if not grade_short or not term_short:
        return ""
    return f"{grade_short}{term_short}{exam_phase(exam_type, title)}"


def session_order(session: dict[str, object]) -> tuple[int, int, int] | None:
    """学期链排序键：年级 → 学期 → 期中<期末；元数据不足返回 None 由日期兜底。"""
    grade = GRADE_ORDER.get(str(session.get("grade") or ""))
    term = TERM_ORDER.get(str(session.get("term") or ""))
    if grade is None or term is None:
        return None
    phase = _PHASE_ORDER.get(
        exam_phase(session.get("exam_type"), session.get("title")), 2
    )
    return (grade, term, phase)
