from __future__ import annotations

import re
from typing import Any

from backend.scan_grading.answer_normalizer import normalize_answer_text

SOLUTION_TYPES = {"proof", "calculation", "comprehensive"}
NON_PROCESS_RESPONSE_MODES = {"exact_objective", "short_answer_points", "visual_construction"}

_STEM_ECHO_PATTERNS = (
    r"是不是定值",
    r"是否为定值",
    r"是否定值",
    r"是不是常量",
    r"是否为常量",
    r"^求证",
    r"^试证明",
    r"^证明.+成立",
    r"^说明.+理由",
    r"^判断.+是否",
)

_ACK_ONLY_RE = re.compile(r"^[是对勾√✓✔]+$")

_PROCESS_EVIDENCE_PATTERNS = (
    r"∵|∴|因为|所以|推出|证得|则得|可得|故有|则有|假设|设",
    r"∠|△|⊥|∥|≌|∽|°|sin|cos|tan",
    r"证明|全等|相似|平行|垂直|辅助线",
    r"[=<>≤≥].*\d|\d.*[=<>≤≥+-]",
    r"\d+\s*[°度]",
)


def extract_observed_text(detail: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("observed_answer", "student_answer", "answer_observed"):
        value = detail.get(key)
        if value:
            parts.append(str(value))
    evidence_steps = detail.get("evidence_steps")
    if isinstance(evidence_steps, list):
        parts.extend(str(step) for step in evidence_steps if step)
    assessments = detail.get("step_assessments")
    if isinstance(assessments, list):
        parts.extend(str(step.get("student_evidence") or "") for step in assessments
                     if isinstance(step, dict))
    summary = detail.get("alternative_solution_summary")
    if summary:
        parts.append(str(summary))
    return " ".join(parts).strip()


def _strip_answer_artifacts(text: str) -> str:
    normalized = normalize_answer_text(text)
    if not isinstance(normalized, str):
        return ""
    cleaned = re.sub(r"[√✓✔vV勾]", "", normalized)
    cleaned = re.sub(r"^[（(]?\d+[）)]", "", cleaned)
    cleaned = re.sub(r"^第?\d+问", "", cleaned)
    cleaned = re.sub(r"^[（(]?[一二三123][）)]?", "", cleaned)
    return cleaned.strip(".,，;；:：、。 ")


def classify_non_substantive_solution_answer(answer: str | None) -> str | None:
    text = str(answer or "").strip()
    if not text:
        return "未见有效作答"

    cleaned = _strip_answer_artifacts(text)
    if not cleaned:
        return "仅勾选或涂改痕迹，无有效作答"

    for pattern in _STEM_ECHO_PATTERNS:
        if re.search(pattern, cleaned, flags=re.IGNORECASE):
            remainder = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip(".,，;；:：、。 ")
            if not remainder or len(cleaned) <= 30:
                return "仅复述题干，无有效作答"

    if len(cleaned) <= 12 and re.fullmatch(r"(是不是|是否为|是否).+", cleaned):
        return "仅复述题干，无有效作答"

    if len(cleaned) <= 2 and _ACK_ONLY_RE.fullmatch(cleaned):
        return "仅表态或勾选，无证明过程"

    return None


def has_solution_process_evidence(answer: str | None) -> bool:
    text = str(answer or "").strip()
    if len(text) < 3:
        return False
    normalized = normalize_answer_text(text)
    if not isinstance(normalized, str) or not normalized:
        return False
    if any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _PROCESS_EVIDENCE_PATTERNS):
        return True
    if len(normalized) >= 20 and re.search(r"\d", normalized) and re.search(r"[=+\-×÷*/]", normalized):
        return True
    return False


def _rubric_questions(rubric: dict[str, Any]) -> list[Any]:
    """Accept either a whole-paper rubric or a single question node.

    混合批改入口按大题传单个 question 节点；整卷入口传整份 rubric。
    两种形态都应得到同一套评分单元语义。
    """
    if not isinstance(rubric, dict):
        return []
    questions = rubric.get("questions")
    if isinstance(questions, list):
        return questions
    if "question_id" in rubric or isinstance(rubric.get("parts"), list):
        return [rubric]
    return []


def rubric_question_meta(rubric: dict[str, Any], question_id: str) -> tuple[str, float, int]:
    for question in _rubric_questions(rubric):
        if not isinstance(question, dict):
            continue
        qtype = str(question.get("question_type") or "")
        qid = str(question.get("question_id") or "")
        if qid == question_id:
            return _meta_from_question_node(question, qtype)
        parts = question.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            if str(part.get("part_id") or "") == question_id:
                try:
                    part_score = float(part.get("part_score") or question.get("max_score") or 0)
                except (TypeError, ValueError):
                    part_score = 0.0
                if "answer_only_max_score" in part:
                    answer_only = _answer_only_max_from_node(part, part_score)
                else:
                    answer_only = _answer_only_max_from_node(question, part_score)
                return qtype, part_score, answer_only
    return "", 0.0, 1


def rubric_response_mode(rubric: dict[str, Any], question_id: str) -> str:
    for question in _rubric_questions(rubric):
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or "")
        parts = question.get("parts")
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict) and str(part.get("part_id") or "") == question_id:
                    return str(part.get("response_mode") or "").strip().lower()
        if qid == question_id:
            explicit = str(question.get("response_mode") or "").strip().lower()
            if explicit:
                return explicit
            part_modes = {
                str(part.get("response_mode") or "").strip().lower()
                for part in parts
                if isinstance(part, dict) and str(part.get("response_mode") or "").strip()
            } if isinstance(parts, list) else set()
            if part_modes and part_modes <= NON_PROCESS_RESPONSE_MODES:
                return "short_answer_points"
            return "process_required"
    return ""


def response_mode_requires_process(response_mode: str | None) -> bool:
    return str(response_mode or "").strip().lower() not in NON_PROCESS_RESPONSE_MODES


def _meta_from_question_node(question: dict[str, Any], qtype: str) -> tuple[str, float, int]:
    try:
        max_score = float(question.get("max_score") or 0)
    except (TypeError, ValueError):
        max_score = 0.0
    return qtype, max_score, _answer_only_max_from_node(question, max_score)


def _answer_only_max_from_node(question: dict[str, Any], max_score: float) -> int:
    default_answer_only = 1
    raw = question.get("answer_only_max_score")
    try:
        answer_only = int(round(float(raw)))
    except (TypeError, ValueError):
        answer_only = default_answer_only
    if max_score > 0:
        return max(0, min(answer_only, int(round(max_score))))
    return max(0, answer_only)


def rubric_scoring_unit_steps(
    rubric: dict[str, Any],
    question_id: str,
) -> list[dict[str, Any]]:
    """Return the rubric steps of the scoring unit addressed by ``question_id``.

    question_id 可以是整题 ID（单元含该题全部步骤），也可以是 part_id
    （单元只含该小问的步骤）。找不到对应评分单元时返回空列表。
    """
    target = str(question_id or "").strip()
    if not target:
        return []
    for question in _rubric_questions(rubric):
        if not isinstance(question, dict):
            continue
        parts = question.get("parts")
        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict):
                    continue
                if str(part.get("part_id") or "").strip() == target:
                    return [
                        {**step, "part_id": str(part.get("part_id") or "")}
                        for step in part.get("steps") or []
                        if isinstance(step, dict)
                    ]
        if str(question.get("question_id") or "").strip() == target:
            steps: list[dict[str, Any]] = []
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict):
                        steps.extend(
                            {**step, "part_id": str(part.get("part_id") or "")}
                            for step in part.get("steps") or []
                            if isinstance(step, dict)
                        )
            return steps
    return []


_STEP_ACHIEVEMENTS = {"full", "equivalent", "none", "uncertain"}


def _integer_score_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not number.is_integer() or number < 0:
        return None
    return int(number)


def integer_business_score(value: Any) -> int | None:
    """Normalize a business score (question/step/unit score) to an int.

    业务得分契约：整数或数值上为整数的 ``3.0`` 归一为 ``3``；
    布尔、非有限、非整数或负值返回 ``None``，调用方不得静默
    四舍五入后保存为有效成绩。
    """
    return _integer_score_or_none(value)


def final_simplification_deduction(detail: dict[str, Any], score: float) -> int:
    """A separate, capped presentation deduction; never invent step failures."""
    assessment = detail.get("final_answer_simplification")
    if not isinstance(assessment, dict) or score < 1:
        return 0
    if not (assessment.get("required") is True and assessment.get("equivalent") is True
            and assessment.get("simplified") is False):
        return 0
    if not str(assessment.get("student_evidence") or "").strip() or not str(assessment.get("requirement_evidence") or "").strip():
        return 0
    return 1


def normalize_candidate_scores(value: Any) -> list[dict[str, Any]]:
    """Normalize the model's candidate_scores entries.

    候选分同属业务得分：数值上为整数的 ``8.0`` 归一为 ``8``；
    非整数候选分不进入候选列表，但保留理由与置信度条目。
    """
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        entry = {
            key: item[key]
            for key in ("score", "confidence", "reason")
            if key in item
        }
        if "score" in entry:
            integer = _integer_score_or_none(entry["score"])
            if integer is None:
                entry.pop("score", None)
            else:
                entry["score"] = integer
        if entry:
            normalized.append(entry)
    return normalized


def validate_step_assessments(
    value: Any,
    *,
    rubric: dict[str, Any],
    question_id: str,
    score_awarded: float,
    answer_only_correct: bool = False,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Validate the model's step_assessments against this rubric scoring unit.

    契约：每个有效步骤恰好一项、step_id 来自本单元 rubric、完成状态在
    full/equivalent/none/uncertain，每个步骤是整点有无的判定点、
    各步得分之和等于该单元 score_awarded。
    返回 (规范化明细, None)；契约不满足时返回 (None, 原因)。
    """
    steps = rubric_scoring_unit_steps(rubric, question_id)
    if value is None:
        if steps and response_mode_requires_process(rubric_response_mode(rubric, question_id)):
            return None, "过程评分缺少 step_assessments"
        return None, None
    if not isinstance(value, list):
        return None, "step_assessments 不是数组"
    if not steps:
        if value:
            return None, "step_assessments 对应的评分单元不存在"
        return None, None
    step_ids = [str(step.get("step_id") or "").strip() for step in steps]
    ambiguous = {step_id for step_id in step_ids if step_ids.count(step_id) > 1}
    step_by_id = {
        (str(step.get("part_id") or "") if step_ids[index] in ambiguous else "", step_ids[index]): step
        for index, step in enumerate(steps)
    }
    seen: set[tuple[str, str]] = set()
    normalized: list[dict[str, Any]] = []
    total = 0
    for item in value:
        if not isinstance(item, dict):
            return None, "step_assessments 元素不是对象"
        step_id = str(item.get("step_id") or "").strip()
        part_id = str(item.get("part_id") or "").strip()
        identity = (part_id if step_id in ambiguous else "", step_id)
        step = step_by_id.get(identity)
        if step is None:
            return None, f"step_assessments 含未知步骤 {step_id or '<空>'}"
        if part_id and step.get("part_id") and part_id != step["part_id"]:
            return None, f"步骤 {step_id} 的小问身份不匹配"
        if identity in seen:
            return None, f"step_assessments 步骤 {step_id} 重复"
        seen.add(identity)
        achievement = str(item.get("achievement") or "").strip().lower()
        if achievement == "partial":
            return None, f"步骤 {step_id} 使用了已停用的部分分 partial；判定点只能整点有无"
        if achievement not in _STEP_ACHIEVEMENTS:
            return None, f"步骤 {step_id} 完成状态无效"
        awarded = _integer_score_or_none(item.get("score_awarded"))
        if awarded is None:
            return None, f"步骤 {step_id} 得分不是有效整数"
        step_max = _integer_score_or_none(step.get("step_score"))
        if step_max is not None and awarded > step_max:
            return None, f"步骤 {step_id} 得分超过该步满分"
        if answer_only_correct and (achievement != "none" or awarded != 0):
            return None, f"answer_only_correct 与步骤判定矛盾：步骤 {step_id}"
        evidence = str(item.get("student_evidence") or "").strip()
        missing = str(item.get("missing_or_error") or "").strip()
        if achievement == "none" and awarded != 0:
            return None, f"步骤 {step_id} 未完成却有得分"
        if achievement == "none" and not missing:
            return None, f"步骤 {step_id} 未达成须写明缺失或错误内容"
        if achievement in {"full", "equivalent"} and (awarded != step_max or not evidence):
            return None, f"步骤 {step_id} 完成状态、满分与作答依据不一致"
        if achievement == "uncertain" and (
            awarded not in {0, step_max} or (not evidence and not missing)
        ):
            return None, f"步骤 {step_id} 无法确定时须按最优判断给 0 或该步满分，并给出依据或缺漏"
        reason = str(item.get("reason") or "").strip()
        if not reason:
            return None, f"步骤 {step_id} 缺少评分理由"
        normalized.append(
            {
                "step_id": step_id,
                **({"part_id": part_id} if part_id else {}),
                "achievement": achievement,
                "score_awarded": awarded,
                "student_evidence": str(item.get("student_evidence") or "").strip(),
                "missing_or_error": str(item.get("missing_or_error") or "").strip(),
                "reason": reason,
            }
        )
        total += awarded
    missing = [identity for identity in step_by_id if identity not in seen]
    if missing:
        return None, f"step_assessments 漏评步骤 {missing}"
    if not answer_only_correct and abs(total - float(score_awarded or 0)) > 1e-6:
        return None, "step_assessments 得分之和不等于该单元得分"
    # carried_error_from 只服务掌握度统计：仅保留“本步方法正确、只因沿用
    # 更早的 none/uncertain 步骤结果而判 none”的标记；其余情况静默丢弃。
    step_index_by_identity = {
        (str(step.get("part_id") or "") if step_ids[index] in ambiguous else "", step_ids[index]): index
        for index, step in enumerate(steps)
    }
    normalized_by_identity = {
        (
            str(record.get("part_id") or "") if str(record.get("step_id") or "") in ambiguous else "",
            str(record.get("step_id") or ""),
        ): record
        for record in normalized
    }
    for item, record in zip(value, normalized):
        carried = item.get("carried_error_from")
        if not isinstance(carried, str) or not carried.strip():
            continue
        carried = carried.strip()
        if record.get("achievement") != "none" or record.get("score_awarded") != 0:
            continue
        identity = (
            str(record.get("part_id") or "") if str(record.get("step_id") or "") in ambiguous else "",
            str(record.get("step_id") or ""),
        )
        ref_identity = (
            str(record.get("part_id") or "") if carried in ambiguous else "",
            carried,
        )
        ref_step = step_by_id.get(ref_identity)
        ref_record = normalized_by_identity.get(ref_identity)
        if (
            ref_step is None
            or ref_record is None
            or str(ref_step.get("part_id") or "") != str(step_by_id[identity].get("part_id") or "")
            or step_index_by_identity[ref_identity] >= step_index_by_identity[identity]
            or ref_record.get("achievement") not in {"none", "uncertain"}
        ):
            continue
        record["carried_error_from"] = carried
    return normalized, None


def uncertain_step_ids(normalized: list[dict[str, Any]] | None) -> list[str]:
    """返回 achievement 为 uncertain 的 step_id 列表，供调用方决定复核。"""
    return [
        str(step.get("step_id") or "")
        for step in normalized or []
        if isinstance(step, dict)
        and str(step.get("achievement") or "").strip().lower() == "uncertain"
    ]


def answer_only_correct_flag(value: Any) -> bool | None:
    """Normalize the model's ``answer_only_correct`` declaration.

    只有模型明确核对过最终答案正确性才返回布尔值；缺省或不可识别时
    返回 None，本地不凭 ``x=数字`` 之类的表面形式猜测对错。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def apply_solution_substance_rules(
    *,
    observed_answer: str | None,
    question_type: str,
    full_score: float,
    answer_only_max_score: int | None,
    current_score: float,
    answer_only_correct: bool | None = None,
    has_valid_step_evidence: bool = False,
) -> tuple[float, str | None, str | None, str | None]:
    if question_type not in SOLUTION_TYPES or full_score <= 0:
        return current_score, None, None, None

    # 明确的无过程正确答案不依赖 x=数值 等表面文字形式。
    if answer_only_correct is True:
        cap = min(float(answer_only_max_score if answer_only_max_score is not None else 1), full_score)
        return cap, "逻辑断裂", "缺少有效过程", f"只有正确最终答案且没有有效过程，本评分单元得 {cap:g} 分。"
    # 已校验的逐块作答证据优先于旧的文字启发式；不以关键词判数学完成度。
    if has_valid_step_evidence:
        return current_score, None, None, None

    zero_reason = classify_non_substantive_solution_answer(observed_answer)
    if zero_reason:
        if current_score > 1e-6:
            return 0.0, "未作答", zero_reason, f"{zero_reason}，按硬规则判 0 分。"
        return current_score, None, None, None

    answer_only = answer_only_max_score
    if answer_only is None:
        # 配分后的过程评分单元恒为 1：正确最终答案且无有效过程得 1 分。
        answer_only = 1
    answer_only = max(0, min(int(answer_only), int(round(full_score))))

    if not has_solution_process_evidence(observed_answer):
        if answer_only_correct is False:
            # 模型明确最终答案错误且无有效过程：不能赠送答案分。
            if current_score > 1e-6:
                return (
                    0.0,
                    "逻辑断裂",
                    "答案错误且无有效过程",
                    "最终答案错误且未见有效过程，按硬规则判 0 分。",
                )
            return current_score, None, None, None
        if current_score > answer_only + 1e-6:
            if current_score >= full_score - 1e-6:
                summary = "缺少有效过程"
                reason = f"未见有效证明或推导过程，最多给 {answer_only} 分。"
            else:
                summary = "缺少有效过程"
                reason = f"仅有结论或无效作答痕迹，最多给 {answer_only} 分。"
            return float(answer_only), "逻辑断裂", summary, reason

    return current_score, None, None, None
