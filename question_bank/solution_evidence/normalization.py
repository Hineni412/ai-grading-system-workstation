from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Any, Mapping, Sequence

from question_bank.solution_evidence.contracts import _reject_score_fields


_ROOT_FIELDS = {
    "schema_version",
    "question_id",
    "parts",
    "auxiliary_rules",
    "rationale",
    "confidence",
}
_PART_FIELDS = {
    "part_id",
    "label",
    "response_mode",
    "canonical_answer",
    "accepted_forms",
    "full_answer",
    "proof_obligations",
    "visual_requirements",
    "deduction_policy",
    "allow_alternative_methods",
    "evidence_points",
}
_POINT_FIELDS = {
    "evidence_point_id",
    "step_index",
    "target",
    "justification",
    "answer_anchor",
    "observable_evidence",
    "depends_on",
    "fine_term_links",
    "equivalent_rules",
    "counterexamples",
}
_LINK_FIELDS = {"fine_term_id", "fine_term_name", "role"}

_ROOT_ALIASES = {
    "questionparts": "parts",
    "solutionparts": "parts",
    "rules": "auxiliary_rules",
    "reason": "rationale",
    "confidencescore": "confidence",
}
_PART_ALIASES = {
    "answer": "canonical_answer",
    "standardanswer": "canonical_answer",
    "completeanswer": "full_answer",
    "solution": "full_answer",
    "solutiontext": "full_answer",
    "answermode": "response_mode",
    "steps": "evidence_points",
    "scoringpoints": "evidence_points",
    "alternativemethods": "allow_alternative_methods",
}
_POINT_ALIASES = {
    "stepid": "evidence_point_id",
    "pointid": "evidence_point_id",
    "goal": "target",
    "reason": "justification",
    "evidence": "observable_evidence",
    "knowledge": "fine_term_links",
}
_LINK_ALIASES = {
    "id": "fine_term_id",
    "termid": "fine_term_id",
    "name": "fine_term_name",
    "termname": "fine_term_name",
    "linkrole": "role",
}

_MODE_ALIASES = {
    "exact_objective": "exact_objective",
    "objective": "exact_objective",
    "exact": "exact_objective",
    "choice": "exact_objective",
    "single_choice": "exact_objective",
    "short_answer_points": "short_answer_points",
    "short_answer": "short_answer_points",
    "short": "short_answer_points",
    "fill_blank": "short_answer_points",
    "direct_answer": "short_answer_points",
    "process_required": "process_required",
    "process": "process_required",
    "proof": "process_required",
    "calculation": "process_required",
    "solution": "process_required",
    "visual_construction": "visual_construction",
    "construction": "visual_construction",
    "visual": "visual_construction",
    "drawing": "visual_construction",
}

_ROLE_ALIASES = {
    "direct": "direct",
    "primary": "direct",
    "main": "direct",
    "supporting_prerequisite": "supporting_prerequisite",
    "supporting": "supporting_prerequisite",
    "prerequisite": "supporting_prerequisite",
    "secondary": "supporting_prerequisite",
}

_AUXILIARY_PART_TOKENS = {
    "auxiliaryrule",
    "auxiliaryrules",
    "supportingrule",
    "supportingrules",
}


@dataclass(frozen=True, slots=True)
class ModelEvidenceNormalization:
    payload: dict[str, Any]
    notes: tuple[str, ...]
    requires_review: bool = False


def normalize_model_solution_evidence(
    payload: Mapping[str, Any],
    *,
    question_id: int,
    question_type: str,
    taxonomy_contract: Mapping[str, Any],
) -> ModelEvidenceNormalization:
    """Converge model-shaped evidence into the strict internal contract.

    This seam repairs mechanical representation only. It never rewrites a
    submitted mathematical answer, formula, number, sign, or question identity.
    """

    _reject_score_fields(payload)
    notes: list[str] = []
    requires_review = False
    root = _canonicalize_keys(
        payload,
        fields=_ROOT_FIELDS,
        aliases=_ROOT_ALIASES,
        context="解题证据",
        notes=notes,
    )
    schema_version = _schema_version(root.get("schema_version"), notes=notes)

    submitted_question_id = root.get("question_id")
    normalized_question_id = _integer_or_original(submitted_question_id)

    raw_parts = _mapping_list(root.get("parts"))
    candidate_names = _knowledge_candidate_names(taxonomy_contract)
    parts: list[dict[str, Any]] = []
    auxiliary_rules = _text_list(root.get("auxiliary_rules"))
    for part_index, raw_part in enumerate(raw_parts, start=1):
        if not isinstance(raw_part, Mapping):
            notes.append(f"已忽略第 {part_index} 个无法读取的小问条目")
            continue
        part, part_review = _normalize_part_shape(
            raw_part,
            part_index=part_index,
            question_type=question_type,
            candidate_names=candidate_names,
            notes=notes,
        )
        requires_review = requires_review or part_review
        if _is_auxiliary_part(part):
            auxiliary_rules.extend(_auxiliary_text(part))
            notes.append("已把误放在小问列表中的辅助规则移回辅助说明")
            continue
        parts.append(part)

    if not parts and raw_parts:
        # Do not make a genuinely missing answer disappear behind normalization.
        parts = [
            _normalize_part_shape(
                raw_part,
                part_index=index,
                question_type=question_type,
                candidate_names=candidate_names,
                notes=notes,
            )[0]
            for index, raw_part in enumerate(raw_parts, start=1)
            if isinstance(raw_part, Mapping)
        ]

    if str(question_type or "").strip().casefold() == "single_choice":
        parts, collapsed_rules = _collapse_single_choice(parts, notes=notes)
        auxiliary_rules.extend(collapsed_rules)
    elif str(question_type or "").strip().casefold() == "fill_blank":
        parts, collapsed_rules = _collapse_fill_blank(parts, notes=notes)
        auxiliary_rules.extend(collapsed_rules)

    _regenerate_identities_and_dependencies(parts, notes=notes)
    if schema_version == "question-solution-evidence-v1":
        _preserve_v1_point_shape(parts)

    normalized = {
        "schema_version": schema_version,
        "question_id": normalized_question_id,
        "parts": parts,
        "auxiliary_rules": _unique_text(auxiliary_rules),
        "rationale": _text(root.get("rationale")),
        "confidence": _confidence(root.get("confidence")),
    }
    return ModelEvidenceNormalization(
        payload=normalized,
        notes=tuple(dict.fromkeys(notes)),
        requires_review=requires_review,
    )


def _normalize_part_shape(
    raw_part: Mapping[str, Any],
    *,
    part_index: int,
    question_type: str,
    candidate_names: Mapping[str, str],
    notes: list[str],
) -> tuple[dict[str, Any], bool]:
    part = _canonicalize_keys(
        raw_part,
        fields=_PART_FIELDS,
        aliases=_PART_ALIASES,
        context=f"第 {part_index} 个小问",
        notes=notes,
    )
    mode = _response_mode(part.get("response_mode"), question_type=question_type)
    canonical_answer = _text(part.get("canonical_answer"))
    accepted_forms = _text_list(part.get("accepted_forms"))
    if not canonical_answer and mode == "exact_objective" and accepted_forms:
        canonical_answer = accepted_forms[0]
        notes.append(f"第 {part_index} 个客观小问已从可接受答案补齐标准答案")
    if canonical_answer and canonical_answer not in accepted_forms:
        accepted_forms.insert(0, canonical_answer)

    points: list[dict[str, Any]] = []
    requires_review = False
    for point_index, raw_point in enumerate(
        _mapping_list(part.get("evidence_points")),
        start=1,
    ):
        if not isinstance(raw_point, Mapping):
            notes.append(
                f"第 {part_index} 个小问中第 {point_index} 个无法读取的评分点已忽略"
            )
            continue
        point, point_review = _normalize_point_shape(
            raw_point,
            part_index=part_index,
            point_index=point_index,
            fallback_answer=canonical_answer,
            candidate_names=candidate_names,
            notes=notes,
        )
        points.append(point)
        requires_review = requires_review or point_review

    full_answer = _text(part.get("full_answer"))
    if mode != "exact_objective" and not full_answer:
        answer_fragments = [
            canonical_answer,
            *[
                _text(point.get("answer_anchor"))
                or _text(point.get("observable_evidence"))
                for point in points
            ],
        ]
        full_answer = "；".join(_unique_text(answer_fragments))
        if full_answer:
            notes.append(
                f"第 {part_index} 个小问缺少完整解答，已从现有判分证据补齐并标记待检查"
            )
            requires_review = True

    visual_requirements = _text_list(part.get("visual_requirements"))
    if mode == "visual_construction" and not full_answer and visual_requirements:
        full_answer = "；".join(visual_requirements)
        notes.append(f"第 {part_index} 个作图小问已用作图要求补齐答案说明")
        requires_review = True

    deduction_policy = _text_list(part.get("deduction_policy"))
    if not deduction_policy:
        deduction_policy = ["按各评分点是否达到进行判断"]
        notes.append(f"第 {part_index} 个小问已补齐默认判分说明")

    return (
        {
            "part_id": _text(part.get("part_id")),
            "label": _text(part.get("label")),
            "response_mode": mode,
            "canonical_answer": canonical_answer,
            "accepted_forms": _unique_text(accepted_forms),
            "full_answer": full_answer,
            "proof_obligations": _unique_text(
                _text_list(part.get("proof_obligations"))
            ),
            "visual_requirements": _unique_text(visual_requirements),
            "deduction_policy": _unique_text(deduction_policy),
            "allow_alternative_methods": _boolean(
                part.get("allow_alternative_methods"),
                default=mode != "exact_objective",
            ),
            "evidence_points": points,
        },
        requires_review,
    )


def _normalize_point_shape(
    raw_point: Mapping[str, Any],
    *,
    part_index: int,
    point_index: int,
    fallback_answer: str,
    candidate_names: Mapping[str, str],
    notes: list[str],
) -> tuple[dict[str, Any], bool]:
    point = _canonicalize_keys(
        raw_point,
        fields=_POINT_FIELDS,
        aliases=_POINT_ALIASES,
        context=f"第 {part_index} 个小问第 {point_index} 个评分点",
        notes=notes,
    )
    target = _text(point.get("target"))
    observable = _text(point.get("observable_evidence"))
    anchor = _text(point.get("answer_anchor"))
    if not target:
        target = observable or anchor or fallback_answer
    if not observable:
        observable = target
    if not anchor:
        anchor = fallback_answer or target or observable
    justification = _text(point.get("justification"))
    if not justification and (target or observable or anchor):
        justification = "依据题目条件和该步骤所述数学关系"

    links: list[dict[str, str]] = []
    seen_links: set[tuple[str, str]] = set()
    requires_review = False
    for raw_link in _mapping_list(point.get("fine_term_links")):
        if not isinstance(raw_link, Mapping):
            notes.append(
                f"第 {part_index} 个小问第 {point_index} 个评分点的无效知识词链接已忽略"
            )
            requires_review = True
            continue
        link = _canonicalize_keys(
            raw_link,
            fields=_LINK_FIELDS,
            aliases=_LINK_ALIASES,
            context="知识词链接",
            notes=notes,
        )
        term_id = _text(link.get("fine_term_id"))
        term_name = _text(link.get("fine_term_name"))
        role = _ROLE_ALIASES.get(
            _text(link.get("role")).strip().casefold(),
            _text(link.get("role")).strip().casefold(),
        )
        if term_id in candidate_names:
            term_name = candidate_names[term_id]
        if not term_id or not term_name or role not in {
            "direct",
            "supporting_prerequisite",
        }:
            notes.append(
                f"第 {part_index} 个小问第 {point_index} 个评分点的不完整知识词链接已转待处理"
            )
            requires_review = True
            continue
        signature = (term_id, role)
        if signature in seen_links:
            continue
        seen_links.add(signature)
        links.append(
            {
                "fine_term_id": term_id,
                "fine_term_name": term_name,
                "role": role,
            }
        )

    return (
        {
            "evidence_point_id": _text(point.get("evidence_point_id")),
            "step_index": _integer_or_original(point.get("step_index")),
            "target": target,
            "justification": justification,
            "answer_anchor": anchor,
            "observable_evidence": observable,
            "depends_on": _text_list(point.get("depends_on")),
            "fine_term_links": links,
            "equivalent_rules": _unique_text(
                _text_list(point.get("equivalent_rules"))
            ),
            "counterexamples": _unique_text(
                _text_list(point.get("counterexamples"))
            ),
        },
        requires_review,
    )


def _collapse_single_choice(
    parts: Sequence[dict[str, Any]],
    *,
    notes: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    if not parts:
        return [], []
    canonical_answers = _unique_text(
        [_text(part.get("canonical_answer")) for part in parts]
    )
    if len(canonical_answers) > 1:
        raise ValueError("single-choice parts contain conflicting canonical answers")
    primary_source = next(
        (
            part
            for part in parts
            if _text(part.get("canonical_answer"))
        ),
        parts[0],
    )
    explanations = _unique_text(
        [
            _text(part.get("full_answer"))
            for part in parts
            if _text(part.get("full_answer"))
        ]
    )
    primary = dict(primary_source)
    primary["full_answer"] = "；".join(explanations)
    collapsed = _collapse_objective_part(primary, notes=notes)
    auxiliary = [
        value
        for part in parts
        if part is not primary_source
        for value in _text_list(part.get("deduction_policy"))
    ]
    if len(parts) > 1:
        notes.append("已按本地客观题身份合并模型额外生成的非计分小问")
    return [collapsed], auxiliary


def _collapse_fill_blank(
    parts: Sequence[dict[str, Any]],
    *,
    notes: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep a fill-in question as one objective scoring unit.

    Multiple blanks remain represented by one ordered combined answer.  This
    prevents a model explanation or intermediate calculation from becoming a
    separately scored part while retaining every submitted answer fragment.
    """

    if not parts:
        return [], []
    canonical_answers = [
        _text(part.get("canonical_answer"))
        or next(iter(_text_list(part.get("accepted_forms"))), "")
        for part in parts
    ]
    canonical_answers = [value for value in canonical_answers if value]
    primary_source = next(
        (
            part
            for part in parts
            if _text(part.get("canonical_answer"))
        ),
        parts[0],
    )
    combined_answer = "；".join(canonical_answers)
    explanations = _unique_text(
        [
            _text(part.get("full_answer"))
            for part in parts
            if _text(part.get("full_answer"))
        ]
    )
    primary = dict(primary_source)
    primary["canonical_answer"] = combined_answer
    primary["accepted_forms"] = (
        _text_list(primary_source.get("accepted_forms"))
        if len(parts) == 1
        else ([combined_answer] if combined_answer else [])
    )
    primary["full_answer"] = "；".join(explanations)
    collapsed = _collapse_objective_part(primary, notes=notes)
    auxiliary = [
        value
        for part in parts
        if part is not primary_source
        for value in _text_list(part.get("deduction_policy"))
    ]
    if len(parts) > 1:
        notes.append("已把填空题的多个模型小问合并为一个最终答案评分单元")
    return [collapsed], auxiliary


def _preserve_v1_point_shape(parts: Sequence[dict[str, Any]]) -> None:
    """Keep readable v1 checkpoints on their original exact-key contract."""

    v1_fields = {
        "evidence_point_id",
        "target",
        "observable_evidence",
        "fine_term_links",
        "equivalent_rules",
        "counterexamples",
    }
    for part in parts:
        points = part.get("evidence_points") or []
        part["evidence_points"] = [
            {key: value for key, value in point.items() if key in v1_fields}
            for point in points
            if isinstance(point, Mapping)
        ]


def _collapse_objective_part(
    part: dict[str, Any],
    *,
    notes: list[str],
) -> dict[str, Any]:
    canonical_answer = _text(part.get("canonical_answer"))
    accepted_forms = _text_list(part.get("accepted_forms"))
    if not canonical_answer and accepted_forms:
        canonical_answer = accepted_forms[0]
    if canonical_answer and canonical_answer not in accepted_forms:
        accepted_forms.insert(0, canonical_answer)

    links: list[dict[str, str]] = []
    seen_links: set[tuple[str, str]] = set()
    equivalent_rules: list[str] = []
    counterexamples: list[str] = []
    raw_points = [
        point
        for point in part.get("evidence_points") or []
        if isinstance(point, Mapping)
    ]
    for point in raw_points:
        for link in point.get("fine_term_links") or []:
            if not isinstance(link, Mapping):
                continue
            signature = (
                _text(link.get("fine_term_id")),
                _text(link.get("role")),
            )
            if not all(signature) or signature in seen_links:
                continue
            seen_links.add(signature)
            links.append(dict(link))
        equivalent_rules.extend(_text_list(point.get("equivalent_rules")))
        counterexamples.extend(_text_list(point.get("counterexamples")))

    part = dict(part)
    part.update(
        {
            "response_mode": "exact_objective",
            "canonical_answer": canonical_answer,
            "accepted_forms": _unique_text(accepted_forms),
            "allow_alternative_methods": False,
            "evidence_points": [
                {
                    "evidence_point_id": "objective-answer",
                    "step_index": 1,
                    "target": f"作答为{canonical_answer}" if canonical_answer else "",
                    "justification": "与标准答案一致" if canonical_answer else "",
                    "answer_anchor": canonical_answer,
                    "observable_evidence": (
                        f"作答为{canonical_answer}" if canonical_answer else ""
                    ),
                    "depends_on": [],
                    "fine_term_links": links,
                    "equivalent_rules": _unique_text(equivalent_rules),
                    "counterexamples": _unique_text(counterexamples),
                }
            ],
        }
    )
    if len(raw_points) > 1:
        notes.append("已把客观题的过程解析收敛为单个标准答案评分点")
    return part


def _regenerate_identities_and_dependencies(
    parts: Sequence[dict[str, Any]],
    *,
    notes: list[str],
) -> None:
    old_ids_by_part: list[dict[str, str | None]] = []
    old_id_parts: dict[str, set[int]] = {}
    for part_index, part in enumerate(parts, start=1):
        old_part_id = _text(part.get("part_id"))
        new_part_id = f"part-{part_index}"
        if old_part_id and old_part_id.casefold() != new_part_id:
            notes.append(f"已把小问编号 {old_part_id} 统一为 {new_part_id}")
        part["part_id"] = new_part_id
        points = [
            point
            for point in part.get("evidence_points") or []
            if isinstance(point, dict)
        ]
        part["evidence_points"] = points
        local_old_ids: dict[str, str | None] = {}
        for point_index, point in enumerate(points, start=1):
            old_point_id = _text(point.get("evidence_point_id"))
            new_point_id = f"{new_part_id}-step-{point_index}"
            if old_point_id:
                clean_old_id = old_point_id.casefold()
                if clean_old_id in local_old_ids:
                    local_old_ids[clean_old_id] = None
                else:
                    local_old_ids[clean_old_id] = new_point_id
                old_id_parts.setdefault(clean_old_id, set()).add(part_index)
            if old_point_id and old_point_id.casefold() != new_point_id:
                notes.append(
                    f"已把评分点编号 {old_point_id} 统一为 {new_point_id}"
                )
            point["evidence_point_id"] = new_point_id
            point["step_index"] = point_index
        old_ids_by_part.append(local_old_ids)

    for part_index, part in enumerate(parts, start=1):
        local_old_ids = old_ids_by_part[part_index - 1]
        prior_ids: set[str] = set()
        for point in part.get("evidence_points") or []:
            dependencies: list[str] = []
            for raw_dependency in _text_list(point.get("depends_on")):
                clean = raw_dependency.strip().casefold()
                if clean in local_old_ids:
                    candidate = local_old_ids[clean]
                    if candidate is None:
                        notes.append(
                            f"已忽略指向重复旧编号的歧义依赖 {raw_dependency}"
                        )
                        continue
                elif clean in old_id_parts:
                    notes.append(
                        f"已移除跨小问依赖 {raw_dependency}，数学承接仍保留在判分说明中"
                    )
                    continue
                else:
                    candidate = clean
                if candidate not in prior_ids:
                    notes.append(f"已忽略无法对应到更早评分点的依赖 {raw_dependency}")
                    continue
                if candidate not in dependencies:
                    dependencies.append(candidate)
            point["depends_on"] = dependencies
            prior_ids.add(_text(point.get("evidence_point_id")))


def _canonicalize_keys(
    payload: Mapping[str, Any],
    *,
    fields: set[str],
    aliases: Mapping[str, str],
    context: str,
    notes: list[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    priorities: dict[str, int] = {}
    token_fields = {_key_token(field): field for field in fields}
    alias_fields = {**token_fields, **aliases}
    for raw_key, value in payload.items():
        submitted = str(raw_key)
        token = _key_token(submitted)
        canonical: str | None = None
        priority = 0
        if submitted in fields:
            canonical = submitted
            priority = 3
        elif token in token_fields:
            canonical = token_fields[token]
            priority = 2
        elif token in alias_fields:
            canonical = alias_fields[token]
            priority = 1
        else:
            canonical = _fuzzy_field(token, alias_fields)
            priority = 1 if canonical else 0
        if canonical is None:
            notes.append(f"{context}中的额外字段 {submitted} 已忽略")
            continue
        if canonical in result:
            if result[canonical] != value:
                raise ValueError(
                    f"{context}中的冲突字段不能同时匹配为 {canonical}"
                )
            if priorities.get(canonical, -1) >= priority:
                notes.append(f"{context}中的等值重复字段 {submitted} 已忽略")
                continue
        result[canonical] = value
        priorities[canonical] = priority
        if submitted != canonical:
            notes.append(f"{context}字段 {submitted} 已匹配为 {canonical}")
    return result


def _fuzzy_field(token: str, candidates: Mapping[str, str]) -> str | None:
    if len(token) < 5:
        return None
    scored = sorted(
        (
            (SequenceMatcher(None, token, candidate).ratio(), canonical)
            for candidate, canonical in candidates.items()
        ),
        reverse=True,
    )
    if not scored or scored[0][0] < 0.9:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.08:
        return None
    return scored[0][1]


def _is_auxiliary_part(part: Mapping[str, Any]) -> bool:
    identifier = _key_token(_text(part.get("part_id")))
    return identifier in _AUXILIARY_PART_TOKENS


def _auxiliary_text(part: Mapping[str, Any]) -> list[str]:
    result = _text_list(part.get("deduction_policy"))
    result.extend(
        _text(value)
        for value in (
            part.get("canonical_answer"),
            part.get("full_answer"),
        )
        if _text(value)
    )
    result.extend(_text_list(part.get("proof_obligations")))
    result.extend(_text_list(part.get("visual_requirements")))
    for point in part.get("evidence_points") or []:
        if not isinstance(point, Mapping):
            continue
        result.extend(
            _text(value)
            for value in (
                point.get("target"),
                point.get("justification"),
                point.get("observable_evidence"),
                point.get("answer_anchor"),
            )
            if _text(value)
        )
    return _unique_text(result)


def _response_mode(value: object, *, question_type: str) -> str:
    clean = _text(value).strip().casefold().replace("-", "_").replace(" ", "_")
    group = str(question_type or "").strip().casefold()
    if group == "single_choice":
        return "exact_objective"
    if group == "fill_blank":
        return "exact_objective"
    if group == "construction":
        return "visual_construction"
    if group in {"calculation", "proof"} and _MODE_ALIASES.get(clean) == (
        "exact_objective"
    ):
        return "process_required"
    if clean in _MODE_ALIASES:
        return _MODE_ALIASES[clean]
    return clean or "process_required"


def _schema_version(value: object, *, notes: list[str]) -> str:
    clean = _text(value).strip().casefold().replace("_", "-")
    if not clean:
        notes.append("已补齐解题证据版本")
        return "question-solution-evidence-v2"
    if clean in {"v2", "2", "question-solution-evidence-2"}:
        notes.append("已把解题证据版本统一为 question-solution-evidence-v2")
        return "question-solution-evidence-v2"
    return clean


def _knowledge_candidate_names(contract: Mapping[str, Any]) -> dict[str, str]:
    raw_candidates = contract.get("candidates")
    knowledge = (
        raw_candidates.get("knowledge")
        if isinstance(raw_candidates, Mapping)
        else None
    )
    result: dict[str, str] = {}
    for raw in knowledge if isinstance(knowledge, list) else []:
        if not isinstance(raw, Mapping):
            continue
        term_id = _text(raw.get("id"))
        name = _text(raw.get("name"))
        if term_id and name:
            result[term_id] = name
    return result


def _mapping_list(value: object) -> list[object]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, Mapping):
        return [value]
    return []


def _text_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, (list, tuple)):
        return []
    return [text for item in value if (text := _text(item))]


def _unique_text(values: Sequence[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _boolean(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    clean = _text(value).casefold()
    if clean in {"true", "1", "yes", "是", "允许"}:
        return True
    if clean in {"false", "0", "no", "否", "不允许"}:
        return False
    return default


def _confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.8
    return round(min(1.0, max(0.0, confidence)), 4)


def _integer_or_original(value: object) -> object:
    if isinstance(value, bool):
        return value
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return value


def _key_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


__all__ = [
    "ModelEvidenceNormalization",
    "normalize_model_solution_evidence",
]
