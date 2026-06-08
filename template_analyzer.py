from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from llm_client import LLMClient
from llm_client import _compress_image_for_api
from score_policy import enforce_integer_scores_by_type
from question_bank.services.ai_tagging_service import KNOWLEDGE_POINT_OPTIONS


def analyze_template_package(
    front_path: Path,
    back_path: Path,
    *,
    rubric: dict[str, Any],
    answer_key: dict[str, Any],
    llm_client: LLMClient,
    model_name: str | None,
    output_dir: Path,
) -> dict[str, Any]:
    """Analyze answer template pages and persist the full debug package locally."""
    output_dir.mkdir(parents=True, exist_ok=True)
    front_blob, front_size = _image_blob_and_size(front_path)
    back_blob, back_size = _image_blob_and_size(back_path)

    prompt = _build_template_analysis_prompt(
        rubric=rubric,
        answer_key=answer_key,
        front_size=front_size,
        back_size=back_size,
    )
    prompt = (
        "重要更新：不要识别、推测或输出学生作答区域坐标；每道题的 regions 必须为 []。"
        "你只需要解析题目、题型、答案、分值、步骤分、等价答案预案和证明义务。\n\n"
        + prompt
    )
    raw_payload = llm_client.json_from_images(prompt, [front_blob, back_blob], model=model_name)
    config = normalize_template_analysis(raw_payload)
    # Answer-region detection from vision models is too unstable for real marking.
    # Keep AI focused on questions/answers/scores and let the user draw regions manually.
    for question in config.get("questions", []):
        if isinstance(question, dict):
            question["regions"] = []
    force_template_total_score(config, target_total=100.0)

    rubric_candidate, answer_key_candidate = build_grading_files_from_template_config(config)
    regions: list[dict[str, Any]] = []

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_path = output_dir / f"template_analysis_raw_{ts}.json"
    config_path = output_dir / f"template_config_{ts}.json"
    rubric_path = output_dir / f"rubric_template_{ts}.json"
    answer_path = output_dir / f"answer_key_template_{ts}.json"
    regions_path = output_dir / f"regions_{ts}.json"

    _write_json(raw_path, raw_payload)
    _write_json(config_path, config)
    _write_json(rubric_path, rubric_candidate)
    _write_json(answer_path, answer_key_candidate)
    _write_json(regions_path, regions)

    return {
        "raw": raw_payload,
        "config": config,
        "rubric": rubric_candidate,
        "answer_key": answer_key_candidate,
        "regions": regions,
        "paths": {
            "raw_path": str(raw_path),
            "config_path": str(config_path),
            "rubric_path": str(rubric_path),
            "answer_key_path": str(answer_path),
            "regions_path": str(regions_path),
        },
    }


def normalize_template_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    questions_raw = payload.get("questions")
    if not isinstance(questions_raw, list):
        raise ValueError("样卷分析结果缺少 questions 数组")

    questions: list[dict[str, Any]] = []
    for idx, question in enumerate(questions_raw, start=1):
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or f"Q{idx}").strip()
        qtype = _normalize_question_type(question.get("question_type") or question.get("type"))
        max_score = _safe_float(question.get("max_score"), 0.0)
        parts = _normalize_parts(question, qid, max_score)
        regions = _normalize_regions(question.get("regions"), qid)
        obligations = _proof_obligations(question.get("proof_obligations"))
        if not obligations and qtype in {"proof", "calculation", "comprehensive"}:
            obligations = _obligations_from_parts(parts)
        deduction_policy = _deduction_policy(question.get("deduction_policy"))
        if not deduction_policy and qtype in {"proof", "calculation", "comprehensive"}:
            deduction_policy = _default_deduction_policy(max_score)

        questions.append(
            {
                "question_id": qid,
                "question_type": qtype,
                "stem_summary": str(question.get("stem_summary") or "").strip(),
                "max_score": max_score,
                **_normalize_question_knowledge_fields(question),
                "grading_mode": str(
                    question.get("grading_mode")
                    or ("deductive_obligation" if qtype in {"proof", "calculation", "comprehensive"} else "direct_answer")
                ).strip(),
                "proof_obligations": obligations,
                "deduction_policy": deduction_policy,
                "canonical_answer": str(question.get("canonical_answer") or "").strip(),
                "accepted_forms": _string_list(question.get("accepted_forms")),
                "method_variants": _method_variants(question.get("method_variants")),
                "parts": parts,
                "regions": regions,
                "is_confirmed": bool(question.get("is_confirmed", False)),
                "warnings": _string_list(question.get("warnings")),
            }
        )

    if not questions:
        raise ValueError("样卷分析未识别到任何题目")

    return {
        "meta": {
            "source": "template_ai_analysis",
            "warnings": _string_list(payload.get("warnings") or payload.get("meta", {}).get("warnings")),
            "schema_version": 1,
        },
        "questions": questions,
    }


def build_grading_files_from_template_config(config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    questions = config.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("template_config.questions 必须是非空数组")

    rubric_questions: list[dict[str, Any]] = []
    answer_questions: list[dict[str, Any]] = []
    total_score = 0.0

    for question in questions:
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or "").strip()
        if not qid:
            continue
        max_score = _safe_float(question.get("max_score"), 0.0)
        total_score += max_score
        parts = question.get("parts") if isinstance(question.get("parts"), list) else []
        if not parts:
            parts = [_default_part(qid, max_score)]

        rubric_parts: list[dict[str, Any]] = []
        answer_parts: list[dict[str, Any]] = []
        for part_idx, part in enumerate(parts, start=1):
            if not isinstance(part, dict):
                continue
            part_id = str(part.get("part_id") or f"{qid}({part_idx})").strip()
            part_score = _safe_float(part.get("part_score"), max_score)
            steps = _normalize_steps(part.get("steps"), part_score)
            rubric_parts.append(
                {
                    "part_id": part_id,
                    "part_score": part_score,
                    "steps": steps,
                    "presentation_rules": part.get("presentation_rules")
                    if isinstance(part.get("presentation_rules"), list)
                    else [{"rule": "过程表达规范、结论完整", "max_deduction": min(1.0, part_score)}],
                }
            )
            answer_parts.append(
                {
                    "part_id": part_id,
                    "answer": str(part.get("answer") or question.get("canonical_answer") or "").strip(),
                    "analysis": str(part.get("analysis") or "").strip(),
                    "step_milestones": _string_list(part.get("step_milestones")),
                }
            )

        _force_rubric_part_totals(rubric_parts, max_score)

        rubric_questions.append(
            {
                "question_id": qid,
                "question_type": str(question.get("question_type") or "comprehensive"),
                "max_score": max_score,
                **_normalize_question_knowledge_fields(question),
                "knowledge_name": str(
                    question.get("knowledge_name")
                    or question.get("knowledge_text")
                    or question.get("stem_summary")
                    or ""
                ).strip(),
                "stem_summary": str(question.get("stem_summary") or "").strip(),
                "grading_mode": str(question.get("grading_mode") or "direct_answer"),
                "proof_obligations": _proof_obligations(question.get("proof_obligations")),
                "deduction_policy": _deduction_policy(question.get("deduction_policy")),
                "parts": rubric_parts,
            }
        )
        answer_questions.append(
            {
                "question_id": qid,
                "canonical_answer": str(question.get("canonical_answer") or "").strip(),
                "accepted_forms": _string_list(question.get("accepted_forms")),
                "method_variants": _method_variants(question.get("method_variants")),
                "proof_obligations": _proof_obligations(question.get("proof_obligations")),
                "parts": answer_parts,
            }
        )

    return (
        {
            "exam_title": "template_generated_exam",
            "total_score": 100.0,
            "questions": rubric_questions,
        },
        {
            "questions": answer_questions,
        },
    )


def _normalize_question_knowledge_fields(question: dict[str, Any]) -> dict[str, Any]:
    raw_points = question.get("knowledge_points")
    points: list[dict[str, str]] = []
    if isinstance(raw_points, list):
        for item in raw_points:
            if isinstance(item, dict):
                kid = str(item.get("knowledge_id") or item.get("id") or "").strip()
                name = str(item.get("knowledge_name") or item.get("name") or item.get("label") or "").strip()
            else:
                kid = str(item or "").strip()
                name = ""
            if kid:
                points.append({"knowledge_id": kid, "knowledge_name": name})

    raw_ids = question.get("knowledge_ids")
    if isinstance(raw_ids, list):
        for raw_id in raw_ids:
            kid = str(raw_id or "").strip()
            if kid:
                points.append({"knowledge_id": kid, "knowledge_name": ""})

    primary_id = str(question.get("knowledge_id") or "UNKNOWN").strip()
    primary_name = str(
        question.get("knowledge_name")
        or question.get("knowledge_text")
        or question.get("stem_summary")
        or ""
    ).strip()
    if primary_id:
        points.append({"knowledge_id": primary_id, "knowledge_name": primary_name})

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for point in points:
        kid = point["knowledge_id"]
        if not kid or kid in seen:
            continue
        seen.add(kid)
        normalized.append(point)

    if not normalized:
        normalized = [{"knowledge_id": "UNKNOWN", "knowledge_name": ""}]

    return {
        "knowledge_id": normalized[0]["knowledge_id"],
        "knowledge_ids": [point["knowledge_id"] for point in normalized],
        "knowledge_points": normalized,
        "knowledge_name": normalized[0].get("knowledge_name", ""),
    }


def force_template_total_score(config: dict[str, Any], target_total: float = 100.0) -> None:
    questions = config.get("questions") if isinstance(config, dict) else None
    if not isinstance(questions, list) or not questions:
        return
    current_total = sum(_safe_float(q.get("max_score"), 0.0) for q in questions if isinstance(q, dict))
    if current_total <= 0:
        each = round(target_total / len(questions), 2)
        for question in questions:
            if isinstance(question, dict):
                _set_template_question_score(question, each)
    elif abs(current_total - target_total) > 0.01:
        ratio = target_total / current_total
        for question in questions:
            if isinstance(question, dict):
                _set_template_question_score(question, round(_safe_float(question.get("max_score"), 0.0) * ratio, 2))

    valid_questions = [q for q in questions if isinstance(q, dict)]
    total = sum(_safe_float(q.get("max_score"), 0.0) for q in valid_questions)
    diff = round(target_total - total, 2)
    if valid_questions and abs(diff) > 0.001:
        last = valid_questions[-1]
        _set_template_question_score(last, round(_safe_float(last.get("max_score"), 0.0) + diff, 2))
    enforce_integer_scores_by_type(questions, target_total=int(target_total), max_question_score=12)


def _set_template_question_score(question: dict[str, Any], new_score: float) -> None:
    old_score = _safe_float(question.get("max_score"), 0.0)
    question["max_score"] = round(float(new_score), 2)
    parts = question.get("parts")
    if not isinstance(parts, list) or not parts:
        question["parts"] = [_default_part(str(question.get("question_id") or "Q"), float(new_score))]
        return
    ratio = (float(new_score) / old_score) if old_score > 0 else (1.0 / len(parts))
    for part in parts:
        if not isinstance(part, dict):
            continue
        part["part_score"] = round(_safe_float(part.get("part_score"), 0.0) * ratio, 2)
        steps = part.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict):
                    step["step_score"] = round(_safe_float(step.get("step_score"), 0.0) * ratio, 2)
        _force_step_sum(part)
    _force_part_sum(question)
    policies = question.get("deduction_policy")
    if isinstance(policies, list):
        for policy in policies:
            if isinstance(policy, dict) and "max_deduction" in policy:
                policy["max_deduction"] = round(_safe_float(policy.get("max_deduction"), 0.0) * ratio, 2)


def _force_step_sum(part: dict[str, Any]) -> None:
    steps = part.get("steps")
    if not isinstance(steps, list) or not steps:
        return
    part_score = _safe_float(part.get("part_score"), 0.0)
    step_total = sum(_safe_float(step.get("step_score"), 0.0) for step in steps if isinstance(step, dict))
    diff = round(part_score - step_total, 2)
    if abs(diff) > 0.001 and isinstance(steps[-1], dict):
        steps[-1]["step_score"] = round(_safe_float(steps[-1].get("step_score"), 0.0) + diff, 2)


def _force_part_sum(question: dict[str, Any]) -> None:
    parts = question.get("parts")
    if not isinstance(parts, list) or not parts:
        return
    max_score = _safe_float(question.get("max_score"), 0.0)
    part_total = sum(_safe_float(part.get("part_score"), 0.0) for part in parts if isinstance(part, dict))
    diff = round(max_score - part_total, 2)
    if abs(diff) > 0.001 and isinstance(parts[-1], dict):
        parts[-1]["part_score"] = round(_safe_float(parts[-1].get("part_score"), 0.0) + diff, 2)
        _force_step_sum(parts[-1])


def build_regions_from_template_config(config: dict[str, Any]) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    order = 1
    for question in config.get("questions", []):
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or "").strip()
        for region in question.get("regions", []):
            if not isinstance(region, dict):
                continue
            try:
                x = int(region.get("x", 0))
                y = int(region.get("y", 0))
                w = int(region.get("w", 0))
                h = int(region.get("h", 0))
            except (TypeError, ValueError):
                continue
            if w < 10 or h < 10:
                continue
            regions.append(
                {
                    "page": str(region.get("page") or "front"),
                    "region_order": order,
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                    "detected_question_id": qid or None,
                    "mapped_question_id": qid or None,
                    "confidence": _safe_float(region.get("confidence"), 0.75),
                    "is_confirmed": False,
                }
            )
            order += 1
    return regions


def create_template_mapping_package(
    front_path: Path,
    back_path: Path,
    *,
    rubric: dict[str, Any],
    answer_key: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    """Create a local template package from existing grading files without re-generating scoring rules."""
    output_dir.mkdir(parents=True, exist_ok=True)
    config = build_template_config_from_grading_files(rubric, answer_key)
    regions: list[dict[str, Any]] = []

    with Image.open(front_path) as front_img:
        front_size = front_img.size
    with Image.open(back_path) as back_img:
        back_size = back_img.size

    raw_payload = {
        "source": "rubric_layout_mapping",
        "note": "Template upload only stores page images and question candidates. Scoring rules remain from Word-generated rubric/answer_key.",
        "front_size": {"width": front_size[0], "height": front_size[1]},
        "back_size": {"width": back_size[0], "height": back_size[1]},
        "question_count": len(config.get("questions", [])),
    }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_path = output_dir / f"template_mapping_raw_{ts}.json"
    config_path = output_dir / f"template_mapping_config_{ts}.json"
    regions_path = output_dir / f"regions_manual_{ts}.json"
    _write_json(raw_path, raw_payload)
    _write_json(config_path, config)
    _write_json(regions_path, regions)

    return {
        "raw": raw_payload,
        "config": config,
        "regions": regions,
        "paths": {
            "raw_path": str(raw_path),
            "config_path": str(config_path),
            "regions_path": str(regions_path),
        },
    }


def build_template_config_from_grading_files(rubric: dict[str, Any], answer_key: dict[str, Any]) -> dict[str, Any]:
    questions_raw = rubric.get("questions") if isinstance(rubric, dict) else []
    if not isinstance(questions_raw, list) or not questions_raw:
        raise ValueError("当前考试缺少 rubric.questions，无法建立样卷题号映射。")

    answer_map: dict[str, dict[str, Any]] = {}
    answer_questions = answer_key.get("questions") if isinstance(answer_key, dict) else []
    if isinstance(answer_questions, list):
        for item in answer_questions:
            if isinstance(item, dict):
                qid = str(item.get("question_id") or "").strip()
                if qid:
                    answer_map[qid] = item

    questions: list[dict[str, Any]] = []
    for idx, question in enumerate(questions_raw, start=1):
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or f"Q{idx}").strip()
        answer_item = answer_map.get(qid, {})
        questions.append(
            {
                "question_id": qid,
                "question_type": _normalize_question_type(question.get("question_type") or question.get("type")),
                "stem_summary": str(question.get("stem_summary") or "").strip(),
                "max_score": _safe_float(question.get("max_score"), 0.0),
                **_normalize_question_knowledge_fields(question),
                "grading_mode": str(question.get("grading_mode") or "direct_answer").strip(),
                "proof_obligations": _proof_obligations(question.get("proof_obligations")),
                "deduction_policy": _deduction_policy(question.get("deduction_policy")),
                "canonical_answer": str(answer_item.get("canonical_answer") or question.get("canonical_answer") or "").strip(),
                "accepted_forms": _string_list(answer_item.get("accepted_forms") or question.get("accepted_forms")),
                "method_variants": _method_variants(answer_item.get("method_variants") or question.get("method_variants")),
                "parts": _template_parts_from_rubric_and_answer(question, answer_item, qid),
                "regions": [],
                "is_confirmed": True,
                "warnings": [],
            }
        )

    if not questions:
        raise ValueError("当前评分标准没有可映射的题目。")

    return {
        "meta": {
            "source": "rubric_layout_mapping",
            "schema_version": 2,
            "warnings": ["样卷阶段不再重新生成评分标准；请在下方手动画框并绑定题号/小问。"],
        },
        "questions": questions,
    }


def _template_parts_from_rubric_and_answer(
    question: dict[str, Any],
    answer_item: dict[str, Any],
    qid: str,
) -> list[dict[str, Any]]:
    rubric_parts = question.get("parts") if isinstance(question.get("parts"), list) else []
    answer_parts_raw = answer_item.get("parts") if isinstance(answer_item.get("parts"), list) else []
    answer_parts = {
        str(part.get("part_id") or "").strip(): part
        for part in answer_parts_raw
        if isinstance(part, dict) and str(part.get("part_id") or "").strip()
    }
    if not rubric_parts:
        rubric_parts = [_default_part(qid, _safe_float(question.get("max_score"), 0.0))]

    parts: list[dict[str, Any]] = []
    for idx, part in enumerate(rubric_parts, start=1):
        if not isinstance(part, dict):
            continue
        part_id = str(part.get("part_id") or (qid if len(rubric_parts) == 1 else f"{qid}({idx})")).strip()
        answer_part = answer_parts.get(part_id, {})
        parts.append(
            {
                "part_id": part_id,
                "part_score": _safe_float(part.get("part_score"), _safe_float(question.get("max_score"), 0.0)),
                "steps": _normalize_steps(part.get("steps"), _safe_float(part.get("part_score"), 0.0)),
                "presentation_rules": part.get("presentation_rules") if isinstance(part.get("presentation_rules"), list) else [],
                "answer": str(answer_part.get("answer") or "").strip(),
                "analysis": str(answer_part.get("analysis") or "").strip(),
                "step_milestones": _string_list(answer_part.get("step_milestones")),
            }
        )
    return parts


def _force_rubric_part_totals(rubric_parts: list[dict[str, Any]], max_score: float) -> None:
    if not rubric_parts:
        return
    for part in rubric_parts:
        part_score = _safe_float(part.get("part_score"), 0.0)
        steps = part.get("steps")
        if not isinstance(steps, list) or not steps:
            continue
        step_sum = sum(_safe_float(step.get("step_score"), 0.0) for step in steps if isinstance(step, dict))
        diff = round(part_score - step_sum, 2)
        if abs(diff) > 0.01 and isinstance(steps[-1], dict):
            steps[-1]["step_score"] = round(_safe_float(steps[-1].get("step_score"), 0.0) + diff, 2)

    part_sum = sum(_safe_float(part.get("part_score"), 0.0) for part in rubric_parts)
    diff = round(max_score - part_sum, 2)
    if abs(diff) <= 0.01:
        return

    last_part = rubric_parts[-1]
    last_part["part_score"] = round(_safe_float(last_part.get("part_score"), 0.0) + diff, 2)
    steps = last_part.get("steps")
    if isinstance(steps, list) and steps and isinstance(steps[-1], dict):
        steps[-1]["step_score"] = round(_safe_float(steps[-1].get("step_score"), 0.0) + diff, 2)


def save_template_config_package(
    output_dir: Path,
    config: dict[str, Any],
    *,
    prefix: str = "template_config_edited",
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    force_template_total_score(config, target_total=100.0)
    rubric, answer_key = build_grading_files_from_template_config(config)
    # Region mapping is intentionally managed by the interactive editor, not by AI.
    regions: list[dict[str, Any]] = []
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    config_path = output_dir / f"{prefix}_{ts}.json"
    rubric_path = output_dir / f"rubric_template_edited_{ts}.json"
    answer_path = output_dir / f"answer_key_template_edited_{ts}.json"
    regions_path = output_dir / f"regions_edited_{ts}.json"
    _write_json(config_path, config)
    _write_json(rubric_path, rubric)
    _write_json(answer_path, answer_key)
    _write_json(regions_path, regions)
    return {
        "config_path": str(config_path),
        "rubric_path": str(rubric_path),
        "answer_key_path": str(answer_path),
        "regions_path": str(regions_path),
    }


def _normalize_parts(question: dict[str, Any], qid: str, max_score: float) -> list[dict[str, Any]]:
    parts_raw = question.get("parts")
    if not isinstance(parts_raw, list) or not parts_raw:
        return [_default_part(qid, max_score)]

    parts: list[dict[str, Any]] = []
    for idx, part in enumerate(parts_raw, start=1):
        if not isinstance(part, dict):
            continue
        part_id = str(part.get("part_id") or f"{qid}({idx})").strip()
        part_score = _safe_float(part.get("part_score"), max_score)
        parts.append(
            {
                "part_id": part_id,
                "part_score": part_score,
                "answer": str(part.get("answer") or question.get("canonical_answer") or "").strip(),
                "analysis": str(part.get("analysis") or "").strip(),
                "steps": _normalize_steps(part.get("steps"), part_score),
                "step_milestones": _string_list(part.get("step_milestones")),
                "presentation_rules": part.get("presentation_rules")
                if isinstance(part.get("presentation_rules"), list)
                else [{"rule": "过程表达规范、结论完整", "max_deduction": min(1.0, part_score)}],
            }
        )
    return parts or [_default_part(qid, max_score)]


def _default_part(qid: str, max_score: float) -> dict[str, Any]:
    return {
        "part_id": qid,
        "part_score": max_score,
        "answer": "",
        "analysis": "",
        "steps": _normalize_steps([], max_score),
        "step_milestones": [],
        "presentation_rules": [{"rule": "过程表达规范、结论完整", "max_deduction": min(1.0, max_score)}],
    }


def _normalize_steps(steps_raw: Any, total_score: float) -> list[dict[str, Any]]:
    if not isinstance(steps_raw, list) or not steps_raw:
        return [
            {
                "step_id": "S1",
                "step_score": total_score,
                "core_goal": "得到正确答案或完成核心证明逻辑",
                "required_elements": ["答案正确", "关键过程合理"],
                "allow_alternative_methods": True,
            }
        ]

    steps: list[dict[str, Any]] = []
    for idx, step in enumerate(steps_raw, start=1):
        if not isinstance(step, dict):
            continue
        steps.append(
            {
                "step_id": str(step.get("step_id") or f"S{idx}"),
                "step_score": _safe_float(step.get("step_score"), 0.0),
                "core_goal": str(step.get("core_goal") or "").strip(),
                "required_elements": _string_list(step.get("required_elements")),
                "allow_alternative_methods": bool(step.get("allow_alternative_methods", True)),
            }
        )
    return steps or _normalize_steps([], total_score)


def _normalize_regions(regions_raw: Any, qid: str) -> list[dict[str, Any]]:
    if not isinstance(regions_raw, list):
        return []
    regions: list[dict[str, Any]] = []
    for region in regions_raw:
        if not isinstance(region, dict):
            continue
        try:
            x = int(region.get("x", 0))
            y = int(region.get("y", 0))
            w = int(region.get("w", 0))
            h = int(region.get("h", 0))
        except (TypeError, ValueError):
            continue
        if w < 10 or h < 10:
            continue
        regions.append(
            {
                "page": str(region.get("page") or "front"),
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "question_id": qid,
                "confidence": _safe_float(region.get("confidence"), 0.75),
            }
        )
    return regions


def _method_variants(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    variants: list[dict[str, str]] = []
    for idx, item in enumerate(value, start=1):
        if isinstance(item, dict):
            variants.append(
                {
                    "name": str(item.get("name") or f"方法{idx}"),
                    "outline": str(item.get("outline") or item.get("description") or "").strip(),
                }
            )
        elif isinstance(item, str):
            variants.append({"name": f"方法{idx}", "outline": item.strip()})
    return variants


def _normalize_question_type(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"fill_blank", "fill-in", "fill_in_blank", "blank", "short_answer"}:
        return "fill_blank"
    if raw in {"choice", "single_choice", "multiple_choice", "select"}:
        return "choice"
    if raw in {"calculation", "solve", "solution", "解答题", "计算题"}:
        return "calculation"
    if raw in {"proof", "证明", "证明题"}:
        return "proof"
    if raw in {"comprehensive", "综合题"}:
        return "comprehensive"
    if any(token in raw for token in ["填空", "空题", "填"]):
        return "fill_blank"
    if any(token in raw for token in ["选择", "choice"]):
        return "choice"
    if any(token in raw for token in ["证明", "proof"]):
        return "proof"
    if any(token in raw for token in ["计算", "解答", "求解"]):
        return "calculation"
    return "comprehensive"


def _proof_obligations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    obligations: list[dict[str, Any]] = []
    for idx, item in enumerate(value, start=1):
        if isinstance(item, dict):
            obligations.append(
                {
                    "obligation_id": str(item.get("obligation_id") or f"O{idx}"),
                    "description": str(item.get("description") or item.get("core_goal") or "").strip(),
                    "weight": _safe_float(item.get("weight"), 0.0),
                    "acceptable_evidence": _string_list(item.get("acceptable_evidence")),
                }
            )
        elif isinstance(item, str):
            obligations.append(
                {
                    "obligation_id": f"O{idx}",
                    "description": item.strip(),
                    "weight": 0.0,
                    "acceptable_evidence": [],
                }
            )
    return obligations


def _deduction_policy(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    policies: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            policies.append(
                {
                    "issue": str(item.get("issue") or item.get("rule") or "").strip(),
                    "max_deduction": _safe_float(item.get("max_deduction"), 0.0),
                    "severity": str(item.get("severity") or "major").strip(),
                }
            )
        elif isinstance(item, str):
            policies.append({"issue": item.strip(), "max_deduction": 0.0, "severity": "major"})
    return policies


def _obligations_from_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    obligations: list[dict[str, Any]] = []
    idx = 1
    for part in parts:
        if not isinstance(part, dict):
            continue
        for step in part.get("steps", []):
            if not isinstance(step, dict):
                continue
            description = str(step.get("core_goal") or "").strip()
            if not description:
                continue
            obligations.append(
                {
                    "obligation_id": f"O{idx}",
                    "description": description,
                    "weight": _safe_float(step.get("step_score"), 0.0),
                    "acceptable_evidence": _string_list(step.get("required_elements")),
                }
            )
            idx += 1
    return obligations


def _default_deduction_policy(max_score: float) -> list[dict[str, Any]]:
    return [
        {"issue": "关键证明义务缺失", "max_deduction": max_score, "severity": "fatal"},
        {"issue": "关键逻辑断裂或结论无法由过程推出", "max_deduction": min(max_score, max(2.0, max_score * 0.5)), "severity": "major"},
        {"issue": "使用定理但未说明必要前提条件", "max_deduction": min(max_score, 2.0), "severity": "major"},
        {"issue": "过程表述不规范但核心逻辑成立", "max_deduction": min(max_score, 1.0), "severity": "minor"},
    ]


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        if not value.strip():
            return []
        return [item.strip() for item in value.replace("；", ";").split(";") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _image_blob_and_size(path: Path) -> tuple[bytes, tuple[int, int]]:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        size = rgb.size
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=90)
    return _compress_image_for_api(buffer.getvalue()), size


def _write_json(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _build_template_analysis_prompt(
    *,
    rubric: dict[str, Any],
    answer_key: dict[str, Any],
    front_size: tuple[int, int],
    back_size: tuple[int, int],
) -> str:
    knowledge_options_str = ", ".join(KNOWLEDGE_POINT_OPTIONS)
    return (
        "你是中学试卷样卷解析与评分标准设计专家。请同时分析两张样卷图片：第1张为正面，第2张为反面。\n"
        "目标：生成可用于自动批改的样卷配置，必须覆盖题目、答案、分值、步骤分、等价答案预案。\n"
        "硬性总分：本系统所有考试批改统一按 100 分制设计。所有 question.max_score 之和必须等于 100；若原卷不是 100 分制，请按原始分值比例换算。\n"
        "单题上限：每一道题 question.max_score 不能超过 12 分，即不能超过总分的 12%。解答题可以拆成多个小问 parts 分别赋分，但整道题 max_score 仍不得超过 12。\n"
        "硬性赋分：所有 max_score、part_score、step_score、proof_obligations.weight、deduction_policy.max_deduction 都必须是整数，不能出现 2.5、3.33 这类小数。\n"
        "同类同分（仅客观题）：相同 question_type 的客观题必须分值完全相同。例如所有 choice 题同分，所有 fill_blank 题同分，不能出现有的选择题3分、有的选择题4分。解答类大题（calculation/proof/comprehensive）不要求同类同分，可按题目难度与工作量赋予不同分值。\n"
        "分值层级：选择题(choice)单题分值必须小于或等于填空题(fill_blank)，且两者差距不要超过50%（即 choice_score >= fill_blank_score * 0.5）；choice/fill_blank 的单题分值必须小于或等于解答类题目的单题分值。\n"
        "题型细分：解答类题目不要全部写成一种类型，可按实际任务分为 calculation（计算/求解）、proof（证明）、comprehensive（一般综合解答）等多种 question_type。\n"
        "题型纠偏：只有题目明确要求“证明、求证、说明某结论成立、补全证明过程”时才标为 proof；如果题目主要要求求角度、求长度、求周长、求面积、求值、计算、化简或解方程，即使用到几何性质/全等/平行/垂直判定，也应标为 calculation。comprehensive 用于同时包含证明、计算或开放论述的混合型题。\n"
        "若图片/文档中的原始分值与“100分制、整数、客观题同类同分、选择题≤填空题且差距不超过50%、客观题不高于解答题”冲突，请优先按这些规则重新设计赋分。\n"
        "不要输出作答区域坐标，regions 必须为空数组，作答区域由用户后续人工标定。\n"
        "题型严格区分(客观题)：题干或选项中明确包含 A、B、C、D 供选的题目是 choice (选择题)；如果只是要求填入一个最终结果而没有任何候选项，必须标记为 fill_blank (填空题)。坚决不要把没有选项的填空题标记为 choice！\n"
        "语言要求：所有的 core_goal、description、outline、issue 等描述性字段必须全部使用中文，严禁使用英文！\n\n"
        f"正面图片尺寸: {front_size[0]} x {front_size[1]} 像素；反面图片尺寸: {back_size[0]} x {back_size[1]} 像素。\n"
        "若图片中有答案、解析或分值，请优先读取图片信息；若不完整，可参考已有 rubric/answer_key。\n"
        "若某道大题只有总分，请根据答案过程和题型合理拆分 steps，步骤分之和必须等于题目总分。\n"
        "对填空题/选择题/计算题，请尽可能生成 accepted_forms：等价表达、化简前后形式、单位/符号等合理变体。\n"
        "对证明题/解答题，请生成 method_variants、proof_obligations 和 deduction_policy，避免后续批改只认标准答案。\n"
        "证明题/解答题的参考答案只能帮助你抽象证明义务，不能作为唯一阅卷路径。\n\n"
        "仅输出严格 JSON 对象，不要 markdown。输出必须尽量压缩为单行 JSON，不要漂亮打印，不要添加解释性文字，避免输出被截断。结构必须是：\n"
        "{\n"
        "  \"questions\": [\n"
        "    {\n"
        "      \"question_id\": \"Q1\",\n"
        "      \"question_type\": \"fill_blank|choice|calculation|proof|comprehensive\",\n"
        "      \"stem_summary\": \"题干摘要\",\n"
        "      \"max_score\": 6,\n"
        "      \"knowledge_id\": \"C2_01或者UNKNOWN\",\n"
        "      \"knowledge_ids\": [\"C2_01\", \"C2_03\"],\n"
        "      \"knowledge_points\": [{\"knowledge_id\": \"C2_01\", \"knowledge_name\": \"字典中的标准知识点\"}, {\"knowledge_id\": \"C2_03\", \"knowledge_name\": \"字典中的标准知识点\"}],\n"
        "      \"knowledge_name\": \"必须从给定参考字典中选择的主知识点名称，严禁照抄题干\",\n"
        "      \"grading_mode\": \"direct_answer|deductive_obligation\",\n"
        "      \"require_final_answer\": false,\n"
        "      \"answer_only_max_score\": 1,\n"
        "      \"proof_obligations\": [\n"
        "        {\"obligation_id\": \"O1\", \"description\": \"学生必须完成的证明义务\", \"weight\": 3, \"acceptable_evidence\": [\"等价完成方式\"]}\n"
        "      ],\n"
        "      \"deduction_policy\": [\n"
        "        {\"issue\": \"关键逻辑断裂/条件缺失/循环论证/定理前提未说明\", \"max_deduction\": 2, \"severity\": \"minor|major|fatal\"}\n"
        "      ],\n"
        "      \"canonical_answer\": \"标准答案\",\n"
        "      \"accepted_forms\": [\"等价答案1\", \"等价答案2\"],\n"
        "      \"method_variants\": [{\"name\": \"方法A\", \"outline\": \"关键路径\"}],\n"
        "      \"parts\": [\n"
        "        {\n"
        "          \"part_id\": \"Q1或Q1(1)\",\n"
        "          \"part_score\": 6,\n"
        "          \"answer\": \"该小题答案\",\n"
        "          \"analysis\": \"解析或证明过程\",\n"
        "          \"steps\": [\n"
        "            {\n"
        "              \"step_id\": \"S1\",\n"
        "              \"step_score\": 2,\n"
        "              \"core_goal\": \"本步骤评分目标\",\n"
        "              \"required_elements\": [\"必须出现的关键点\"],\n"
        "              \"allow_alternative_methods\": true\n"
        "            }\n"
        "          ],\n"
        "          \"step_milestones\": [\"关键中间结论\"],\n"
        "          \"presentation_rules\": [{\"rule\": \"过程规范/结论完整\", \"max_deduction\": 1}]\n"
        "        }\n"
        "      ],\n"
        "      \"regions\": [\n"
        "        {\"page\": \"front\", \"x\": 120, \"y\": 300, \"w\": 800, \"h\": 260, \"confidence\": 0.86}\n"
        "      ],\n"
        "      \"warnings\": []\n"
        "    }\n"
        "  ],\n"
        "  \"warnings\": []\n"
        "}\n\n"
        "硬性要求：\n"
        "1) 坐标必须是原图绝对像素，page 只能是 front 或 back。\n"
        "1.1) question_type 从 fill_blank、choice、calculation、proof、comprehensive 中选择；不要空\n"
        f"1.2) knowledge_name 必须是精炼、标准的数学知识点名词（如“全等三角形的判定定理”、“实数的混合运算”）。严禁摘抄题干文本！字数控制在 15 个字以内。\n"
        "1.3) 每道题必须提取 knowledge_id 和 knowledge_points。保持高度的一致性：对于考查相同知识点的不同题目，必须输出完全相同的 knowledge_name，避免图谱碎片化！\n"
        "2) 每题 max_score 必须为整数；每题 parts.part_score 之和必须等于 max_score；每个 part 的 steps.step_score 之和必须等于 part_score，且不得出现小数。\n"
        "2.1) 相同 question_type 的客观题（choice/fill_blank）max_score 必须完全一致；解答类大题（calculation/proof/comprehensive）允许不同分值。\n"
        "2.2) choice 的 max_score 必须小于或等于 fill_blank，且不得低于 fill_blank 的 50%；choice/fill_blank 的 max_score 必须小于或等于 calculation/proof/comprehensive 的 max_score。\n"
        "2.3) 任意 question.max_score 必须小于或等于 12；若大题有多问，请在 parts 中拆分小问分值，不要让整题超过 12。\n"
        "3) 若无法确定分值，max_score 可填 0，并在 warnings 写明，方便用户后续补填。\n"
        "4) regions 必须输出空数组 []，不要识别或推测学生作答区域坐标。\n"
        "5) question_id 尽量沿用已有 rubric/answer_key；若图片题号不同，使用图片题号并写 warnings。\n\n"
        "证明题/解答题扣分制准则：\n"
        "- grading_mode 必须使用 deductive_obligation。\n"
        "- proof 与 calculation 默认 require_final_answer=false，不因未额外写“答”单独扣分；comprehensive 默认 require_final_answer=true，未写最终答/结论完整性只可小扣分。\n"
        "- 对 proof/calculation/comprehensive 必须给出 answer_only_max_score：学生只写最终答案但没有有效过程时最多得分，，最多只能给 1 分。\n"
        "- 只写最终答案不能获得主要过程分；过程合理但未单独写“答”时，proof/calculation 不应因此扣分。\n"
        "- proof_obligations 写“必须证明什么/必须建立什么条件”，不是写参考答案原句。\n"
        "- deduction_policy 写可扣分问题，例如缺少关键前提、只写结论无过程、证明方向错误、逻辑断裂、引用定理条件不足。\n"
        "- answer/canonical_answer 只记录最终结论或参考答案摘要，不应主导过程分。\n"
        "- 若学生用不同方法完成同一义务，后续批改应给分，因此 acceptable_evidence 和 method_variants 要覆盖等价方法。\n\n"
        f"已有 rubric(JSON):\n{json.dumps(rubric, ensure_ascii=False)}\n\n"
        f"已有 answer_key(JSON):\n{json.dumps(answer_key, ensure_ascii=False)}"
    )
