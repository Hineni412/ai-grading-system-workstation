"""错因体系公共层（方案见 docs/requests/错因体系改造方案.md）。

- 7 个固定大类是全系统唯一的家长可读错因层；批改旧词、题库旧词与
  错因整理输出都换算到这一层再展示。
- 换算只在读取/整理入口进行，不回写已有数据；占位词与复核标记
  不强行归类，返回 None。
- 批改写出的内部英文码在成为证据前先翻译或剔除，避免进入提示词
  和家长报告。
"""

from __future__ import annotations

import re
from typing import Any

CAUSE_CATEGORIES: tuple[str, ...] = (
    "概念理解",
    "计算与化简",
    "审题与条件",
    "方法与思路",
    "过程与依据",
    "书写与规范",
    "未作答",
)
CAUSE_CATEGORY_SET = frozenset(CAUSE_CATEGORIES)

# 归并 kind → 允许的大类。carry_forward / review 是结构性标记，
# 不面向家长展示，不允许挂大类。
CAUSE_KIND_CATEGORIES: dict[str, frozenset[str]] = {
    "error": frozenset({"概念理解", "计算与化简", "审题与条件", "方法与思路"}),
    "process": frozenset({"过程与依据", "书写与规范"}),
    "response_state": frozenset({"未作答"}),
}

# 批改 error_category 词表（ai_grader 提示词 + 系统补写值）→ 大类。
# 提示注入/其他/需复核/教师已确认 不携带学情错因，未列入即返回 None。
GRADING_CATEGORY_MAP: dict[str, str] = {
    "概念理解错误": "概念理解",
    "计算错误": "计算与化简",
    "审题错误": "审题与条件",
    "条件遗漏": "审题与条件",
    "多选失分": "审题与条件",
    "逻辑断裂": "过程与依据",
    "表达不规范": "书写与规范",
    "未作答": "未作答",
    "作废答案": "未作答",
    "答案不等价": "计算与化简",
    "答案未化简": "计算与化简",
}

# 题库 error_type 词表（question_bank tag_schema.ERROR_PRONE_CATEGORIES）→ 大类。
BANK_ERROR_TYPE_MAP: dict[str, str] = {
    "概念理解不清": "概念理解",
    "公式/定理误用": "概念理解",
    "运算化简错误": "计算与化简",
    "单位/符号错误": "计算与化简",
    "条件识别不完整": "审题与条件",
    "题意阅读偏差": "审题与条件",
    "图形关系识别错误": "审题与条件",
    "辅助线思路缺失": "方法与思路",
    "分类讨论遗漏": "方法与思路",
    "数形转化困难": "方法与思路",
    "综合建模困难": "方法与思路",
    "书写依据不完整": "过程与依据",
}


def normalize_cause_category(value: Any) -> str | None:
    """任意来源词 → 7 类之一；占位、复核标记与未知词返回 None，不猜。"""
    text = str(value or "").strip()
    if not text:
        return None
    if text in CAUSE_CATEGORY_SET:
        return text
    return GRADING_CATEGORY_MAP.get(text) or BANK_ERROR_TYPE_MAP.get(text)


# 批改流程写入 error_summary/deduction_reason 的内部英文码 → 中文含义；
# None 表示该值不携带错因信息（确认占位、分数锁等）。
_INTERNAL_SUMMARY_LABELS: dict[str, str | None] = {
    "blank": "空白未作答",
    "blank_or_no_valid_work": "空白或无有效作答内容",
    "no_valid_work": "无有效作答内容",
    "answer_only": "只写答案、无过程",
    "answer_discarded": "作答被涂抹作废",
    "answer_discarded_by_smudge": "作答被涂抹作废",
    "smudge": "作答涂抹，待核对",
    "uncertain_step_points": "部分判定点无法确定，待核对",
    "alternative_method_review": "使用参考答案之外的方法，待核对",
    "equivalence_uncertain": "答案等价性暂不能确定，待核对",
    "low_confidence": "识别置信度低，待核对",
    "need_review": "待复核",
    "manual_review_confirmed": None,
    "teacher_score_locked": None,
    "final_answer_wrong": "最终答案错误",
    "computation_error": "计算错误",
    "not_fully_simplified": "结果未化到最简",
    "wrong_method": "解题方法错误",
    "wrong_formula": "公式用错",
    "wrong_sign": "符号错误",
    "division_error": "除法运算错误",
}

# 教师确认流程写入的占位文案与英文码：不是真实批语，不作为错因证据。
_PLACEHOLDER_REASONS = frozenset(
    {"人工复核已确认", "教师已确认", "教师已确认最终分", "已复核"}
)

_ASCII_ONLY_RE = re.compile(r"^[\x20-\x7e]+$")
_OBJECTIVE_ANSWER_RE = re.compile(r"^objective_answer\s*=\s*(\S+)$")


def clean_cause_text(value: Any) -> str:
    """错因证据文本清理：内部英文码转中文，占位文案与未登记英文码剔除。

    含中文或数字的原文（如具体错因、算式）原样保留；纯英文且不在受控表
    里的值视为内部码，返回空串。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if text in _INTERNAL_SUMMARY_LABELS:
        return _INTERNAL_SUMMARY_LABELS[text] or ""
    if text in _PLACEHOLDER_REASONS:
        return ""
    objective = _OBJECTIVE_ANSWER_RE.match(text)
    if objective is not None:
        return f"客观题作答：{objective.group(1)}"
    if _ASCII_ONLY_RE.match(text) and not re.search(r"\d", text):
        # 纯英文且不含数字（算式、选项等数据会带数字）：未登记的内部码不进入提示词与报告。
        return ""
    return text
