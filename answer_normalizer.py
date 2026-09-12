import re

_MULTI_ANSWER_SEPARATORS = ("或", "、", ",", ";", "；", "和")
_SCORE_BAIT_PATTERNS = (
    r"请\s*(?:判定|判断|给|打)?\s*满分",
    r"(?:给|打|判定|判断)\s*满分",
    r"强制\s*满分",
    r"自动\s*改为?\s*满分",
    r"出题错误.*满分",
    r"满分",
    r"红笔\s*打勾",
    r"打勾",
    r"老师\s*给分",
    r"直接\s*给分",
    r"(?:忽略|忽视).*(?:设置|指令|评分|批改|标准|规则)",
    r"正确",
)


def normalize_answer_text(answer: str | list | None) -> str | list[str] | None:
    if answer is None:
        return None
        
    if isinstance(answer, list):
        return [normalize_answer_text(a) for a in answer if a is not None]
        
    ans = str(answer)
    # 1, 2. 去除首尾空格, 多余空格
    ans = re.sub(r'\s+', '', ans)
    
    # 3. 中文括号转英文括号
    ans = ans.replace('（', '(').replace('）', ')')
    
    # 4. 中文逗号、分号转英文符号
    ans = ans.replace('，', ',').replace('；', ';')
    
    # 5. 统一负号
    ans = ans.replace('−', '-').replace('–', '-').replace('—', '-')
    
    # 6. √ 与 根号 做简单统一
    ans = ans.replace('根号', '√')
    
    # 7. 全角数字转半角数字
    fullwidth_digits = '０１２３４５６７８９'
    halfwidth_digits = '0123456789'
    trans = str.maketrans(fullwidth_digits, halfwidth_digits)
    ans = ans.translate(trans)
    
    return ans


def contains_prompt_injection_or_score_bait(answer: str | None) -> bool:
    text = normalize_answer_text(answer)
    if not isinstance(text, str) or not text:
        return False
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in _SCORE_BAIT_PATTERNS)


def _parse_numeric_value(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        pass
    if '/' in s:
        parts = s.split('/')
        if len(parts) == 2:
            try:
                return float(parts[0]) / float(parts[1])
            except (ValueError, ZeroDivisionError):
                pass
    return None


def _split_answer_values(answer: str) -> list[str]:
    normalized = normalize_answer_text(answer)
    if not isinstance(normalized, str) or not normalized:
        return []
    pattern = "|".join(re.escape(separator) for separator in _MULTI_ANSWER_SEPARATORS)
    return [part for part in re.split(pattern, normalized) if part]


def _answer_value_key(answer: str, tolerance: float) -> tuple[str, object]:
    normalized = normalize_answer_text(answer)
    if not isinstance(normalized, str):
        return ("text", "")
    values = _extract_numeric_values(normalized)
    if len(values) == 1:
        parsed = _parse_numeric_value(values[0])
        if parsed is not None:
            return ("num", round(parsed / tolerance))
    return ("text", normalized)


def _extract_numeric_values(answer: str) -> list[str]:
    # 面积、体积单位的指数不是第二个答案值；保留 x^2、4^2 等数学幂。
    answer = re.sub(
        r"(?<![A-Za-z])((?:mm|cm|dm|km|m))\^(?:\([23]\)|\{[23]\}|[23])",
        r"\1", answer, flags=re.IGNORECASE,
    )
    if "/" in answer:
        return re.findall(r"-?\d+\.?\d*(?:/-?\d+\.?\d*)?", answer)
    return re.findall(r"-?\d+\.?\d*", answer)


def _standard_requires_complete_answer_set(standard_answer: str, tolerance: float) -> bool:
    parts = _split_answer_values(standard_answer)
    if len(parts) <= 1:
        return False
    keys = {_answer_value_key(part, tolerance) for part in parts}
    return len(keys) > 1


def complete_answer_set_values(standard_answer: str, tolerance: float = 1e-6) -> list[str]:
    """Return required values when the standard answer represents one complete set."""
    parts = _split_answer_values(standard_answer)
    if len(parts) <= 1:
        return []
    keys = {_answer_value_key(part, tolerance) for part in parts}
    return parts if len(keys) > 1 else []


def _match_complete_answer_set(student_answer: str, standard_answer: str, tolerance: float) -> dict | None:
    standard_parts = _split_answer_values(standard_answer)
    if len(standard_parts) <= 1:
        return None
    standard_keys = {_answer_value_key(part, tolerance) for part in standard_parts}
    if len(standard_keys) <= 1:
        student_parts = _split_answer_values(student_answer)
        if len(student_parts) <= 1:
            return None
        student_keys = {_answer_value_key(part, tolerance) for part in student_parts}
        if len(student_keys) == 1 and student_keys == standard_keys:
            return {"matched": True, "match_status": "equivalent", "match_reason": "equivalent_answer_forms"}
        return {"matched": False, "match_status": "definite_mismatch", "match_reason": "multiple_values_provided"}
    student_parts = _split_answer_values(student_answer)
    student_keys = {_answer_value_key(part, tolerance) for part in student_parts}
    if len(student_parts) <= 1:
        return {"matched": False, "match_status": "definite_mismatch", "match_reason": "missing_required_answer_values"}
    if student_keys == standard_keys and len(student_keys) == len(student_parts):
        return {"matched": True, "match_status": "equivalent", "match_reason": "complete_answer_set_equivalent"}
    return {"matched": False, "match_status": "definite_mismatch", "match_reason": "answer_set_mismatch"}


def _score_bait_is_valid_answer(student_answer: str, standard_answer: str | list | None) -> bool:
    norm_stu = normalize_answer_text(student_answer)
    norm_std = normalize_answer_text(standard_answer)
    if not isinstance(norm_stu, str) or not norm_stu:
        return False
    if isinstance(norm_std, str):
        return norm_stu == norm_std
    if isinstance(norm_std, list):
        return any(isinstance(value, str) and norm_stu == value for value in norm_std)
    return False

def match_fill_blank_answer(
    student_answer: str,
    standard_answer: str | list,
    tolerance: float = 1e-6
) -> dict:
    # 1. Check empty
    if not student_answer:
        if not standard_answer:
             return {"matched": None, "match_status": "equivalence_uncertain", "match_reason": "empty_answer"}
        else:
             return {"matched": None, "match_status": "equivalence_uncertain", "match_reason": "empty_answer"}
             
    if not standard_answer:
        return {"matched": None, "match_status": "equivalence_uncertain", "match_reason": "standard_answer_missing"}

    if contains_prompt_injection_or_score_bait(student_answer) and not _score_bait_is_valid_answer(student_answer, standard_answer):
        return {"matched": False, "match_status": "definite_mismatch", "match_reason": "prompt_injection_or_score_bait"}

    if isinstance(standard_answer, str):
        set_match = _match_complete_answer_set(student_answer, standard_answer, tolerance)
        if set_match is not None:
            return set_match

    # Pre-process: if standard_answer is a string containing Chinese "或" (meaning "or"),
    # split it into multiple candidate answers so each is checked independently.
    # e.g. "65°或50°或80°" → ["65°", "50°", "80°"]
    if isinstance(standard_answer, str) and '或' in standard_answer:
        candidates = [c.strip() for c in standard_answer.split('或') if c.strip()]
        if len(candidates) > 1:
            standard_answer = candidates

    norm_stu = normalize_answer_text(student_answer)
    norm_std = normalize_answer_text(standard_answer)

    
    if not norm_stu:
        return {"matched": None, "match_status": "equivalence_uncertain", "match_reason": "empty_answer"}
    
    # Handle list of standard answers
    if isinstance(norm_std, list):
        if not norm_std:
            return {"matched": None, "match_status": "equivalence_uncertain", "match_reason": "standard_answer_missing"}
            
        any_uncertain = False
        reasons = []
        for std in norm_std:
            res = match_fill_blank_answer(student_answer, std, tolerance)
            if res["matched"] is True:
                return res
            elif res["matched"] is None:
                any_uncertain = True
                reasons.append(res["match_reason"])
                
        if any_uncertain:
            return {"matched": None, "match_status": "equivalence_uncertain", "match_reason": reasons[0] if reasons else "equivalence_uncertain"}
        else:
            return {"matched": False, "match_status": "definite_mismatch", "match_reason": "definite_numeric_mismatch"}
            
    # 2. Exact match
    if norm_stu == norm_std:
        return {"matched": True, "match_status": "equivalent", "match_reason": "exact_match"}
        
    # Check for text indicating multiple values BEFORE numeric extraction
    if '或' in norm_stu or '和' in norm_stu or ',' in norm_stu or '，' in norm_stu:
        # If the student provided multiple answers (e.g. 65,50) but it didn't match the standard answer
        # then they gave extra wrong answers. We should definitely mismatch instead of going to review.
        return {"matched": False, "match_status": "definite_mismatch", "match_reason": "multiple_values_provided"}
        
    if '?' in norm_stu or '？' in norm_stu:
        return {"matched": None, "match_status": "equivalence_uncertain", "match_reason": "question_mark_uncertain"}
        
    if '看不清' in norm_stu:
        return {"matched": None, "match_status": "equivalence_uncertain", "match_reason": "unclear_answer"}
        
    if 'O' in norm_stu or '约' in norm_stu or '..' in norm_stu:
        return {"matched": None, "match_status": "equivalence_uncertain", "match_reason": "complex_expression_uncertain"}
        
    if '不是' in norm_stu or '不对' in norm_stu or '!= ' in norm_stu or '≠' in norm_stu:
        return {"matched": False, "match_status": "definite_mismatch", "match_reason": "definite_mismatch_negative"}
        
    # Operators check: If there are arithmetic operators that aren't a single negative sign or a single fraction slash
    if re.search(r'[\+*×÷]', norm_stu):
        return {"matched": False, "match_status": "definite_mismatch", "match_reason": "complex_expression_uncertain"}
        
    if norm_stu.count('/') > 1:
        return {"matched": None, "match_status": "equivalence_uncertain", "match_reason": "complex_expression_uncertain"}
        
    stu_vals = _extract_numeric_values(norm_stu)
    std_vals = _extract_numeric_values(norm_std)
    
    if len(stu_vals) > 1:
        return {"matched": None, "match_status": "equivalence_uncertain", "match_reason": "multiple_values_uncertain"}
        
    # If exactly one numeric value in student and standard
    if len(stu_vals) == 1 and len(std_vals) == 1:
        stu_f = _parse_numeric_value(stu_vals[0])
        std_f = _parse_numeric_value(std_vals[0])
        
        if stu_f is not None and std_f is not None:
            if abs(stu_f - std_f) < tolerance:
                return {"matched": True, "match_status": "equivalent", "match_reason": "unit_tolerant_match"}
            else:
                return {"matched": False, "match_status": "definite_mismatch", "match_reason": "definite_numeric_mismatch"}
                
    if len(stu_vals) == 0:
        return {"matched": None, "match_status": "equivalence_uncertain", "match_reason": "no_numeric_value"}
            
    return {"matched": None, "match_status": "equivalence_uncertain", "match_reason": "equivalence_uncertain"}
