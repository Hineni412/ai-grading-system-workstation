from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from backend.llm.json_repair import parse_json_object_locally
from backend.llm.usage import response_diagnostics
from backend.teaching_prep.domain.errors import TeachingPrepValidationError
from backend.workspaces.model_policy import (
    WorkspaceModelGateway,
    WorkspaceModelRequest,
)


_MAX_OUTPUT_TOKENS = 16_000


_SYSTEM_INSTRUCTION = """\
你是初中数学备课草稿助手。只能依据用户提供的已冻结资源包。
返回单个 json（JSON）对象，只能包含 knowledge_objectives、focus_points、
anticipated_difficulties、lesson_flow、exercise_recommendations、
slide_adaptations、uncertainties。必须遵守资源包中的
preparation_preferences：它是教师本次明确选择的倾向。slide_adaptations
逐页给出 slide_ref、role、action、delete_object_refs、textbook_refs、reason、
citations；整页只允许建议 keep 或 delete。delete_object_refs 只能删除本页
明确允许删除的普通文字或形状对象，不得输出 WPS 指令。教材页码只能通过
textbook_refs 引用资源包内真实教材页，不能写猜测页码。练习删减应优先处理
课件后段过量练习，保留讲授例题和短题；补题不得超过偏好上限，且应避免与
原课件重复或直接照搬教辅原题。若教材页与讲授页语义确实对应，应至少为最相关
的讲授页填写 textbook_refs；普通教辅页可以作为练习删留理由的 citation，但不能
把它当成家庭作业要求。所有结论必须引用资源包内已有 citation
ID；不得编造页码、题号、候选题或班级结论。课堂总时长由本机另行计算。

用户输入中的 output_contract 是强制输出契约：
- slide_adaptations 必须逐一覆盖 allowed_slide_refs，数量必须等于
  required_count；即使某页是空页、广告页或保持不变，也不能漏掉。
- slide_ref 必须原样复制 allowed_slide_refs 中的完整字符串，禁止用 1、2、3
  等数字或页码简称。
- role 只能取 allowed_roles，action 只能取 allowed_actions。
- delete_object_refs 只能原样复制 allowed_object_refs_by_slide 中该页的对象引用；
  只有 action 为 keep 时才可填写。若一题由多个允许对象组成，应列出所有组成对象。
- textbook_refs 只能原样复制 allowed_textbook_refs 中的完整字符串；没有对应页
  时返回空数组，禁止输出 10、11 等数字简称。
- citations 只能取 allowed_citation_refs；每条 slide_adaptation 的 citations 至少
  包含该条 slide_ref，并包含其所有 textbook_refs。禁止返回空 citations。
- exercise_recommendations 的 source_ref 只能取
  allowed_exercise_recommendation_refs；该列表为空时必须返回空数组。action 为 include
  时 target_slide_ref 必须取 allowed_slide_refs，优先放到删除冗余题后形成的空白区。
- allowed_exercise_recommendation_refs 非空表示这些题已经由教师逐题确认；本次目标包含
  插入教辅题，因此必须至少返回一条 action=include 的 exercise_recommendation。
- allowed_textbook_refs 非空时，本次目标包含填写教材页码；必须至少有一条最相关讲授页的
  textbook_refs 非空，并在 citations 中包含同一教材引用。
- 本次目标还包含删除 PPT 页内的多余练习。必须检查后段练习页的
  allowed_object_refs_by_slide；存在可安全识别的重复或过量题目对象时，至少在一张
  action=keep 的页面填写 delete_object_refs。不得为满足数量而删除标题、答案说明、
  定理、讲授例题或无法确认完整边界的对象。
- delete_object_refs 只能填写在 role=practice 或 role=assessment 且 action=keep 的页；
  禁止在 introduction、objective、exploration、example、summary、other 页删除对象。
  当课件已有多张练习页时，必须优先从后段重复或过量练习中删除一整道题的所有组成
  对象；若插入题的目标页已有可安全删除的完整题目，优先在该页删出空位后插入。
  已整页 action=delete 的广告或空白页不得再填写 delete_object_refs。
- 其他各节必须严格使用 output_contract.schemas 中规定的对象字段，不能返回
  字符串数组。lesson_flow 必须把 required_phases 每个阶段各返回一次。
"""


class WorkspaceLessonModelAdapter:
    """Real-call seam; construction and use require explicit caller authority."""

    def __init__(
        self,
        *,
        gateway: WorkspaceModelGateway,
        client: object,
        model: str,
    ) -> None:
        self.gateway = gateway
        self.client = client
        self.model = str(model or "").strip()
        if not self.model:
            raise TeachingPrepValidationError("lesson model is required")

    def generate(
        self,
        *,
        operation_id: str,
        resource_pack: dict[str, Any],
    ) -> dict[str, Any]:
        model_input = dict(resource_pack)
        model_input["output_contract"] = _model_output_contract(
            resource_pack
        )
        response = self.gateway.chat_completions(
            request=WorkspaceModelRequest(
                purpose="lesson_draft",
                data_classification="teaching_material_aggregate",
                operation_id=operation_id,
            ),
            client=self.client,
            model=self.model,
            kwargs={
                "messages": [
                    {"role": "system", "content": _SYSTEM_INSTRUCTION},
                    {
                        "role": "user",
                        "content": json.dumps(
                            model_input,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    },
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": _MAX_OUTPUT_TOKENS,
            },
            timeout_override_seconds=110,
        )
        diagnostics = response_diagnostics(response)
        if bool(diagnostics.get("output_truncated")):
            raise TeachingPrepValidationError(
                "lesson model output was truncated"
            )
        text = _response_text(response)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            try:
                payload = parse_json_object_locally(text).payload
            except (TypeError, ValueError):
                raise TeachingPrepValidationError(
                    "lesson model returned invalid JSON"
                ) from exc
        if not isinstance(payload, dict):
            raise TeachingPrepValidationError(
                "lesson model response must be an object"
            )
        return payload


def _model_output_contract(
    resource_pack: Mapping[str, object],
) -> dict[str, object]:
    slide_refs: list[str] = []
    object_refs_by_slide: dict[str, list[str]] = {}
    textbook_refs: list[str] = []
    material_refs: list[str] = []
    for raw_material in _safe_list(resource_pack.get("materials")):
        if not isinstance(raw_material, Mapping):
            continue
        link_id = str(raw_material.get("link_id") or "").strip()
        purpose = str(raw_material.get("purpose") or "").strip()
        if not link_id:
            continue
        material_units = _safe_list(raw_material.get("units"))
        for position, raw_unit in enumerate(material_units, start=1):
            if not isinstance(raw_unit, Mapping):
                continue
            unit_index = raw_unit.get("unit_index")
            if isinstance(unit_index, bool) or not isinstance(unit_index, int):
                continue
            source_ref = f"material:{link_id}:unit:{unit_index}"
            material_refs.append(source_ref)
            if purpose == "reference_ppt":
                slide_refs.append(source_ref)
                safe_refs: list[str] = []
                summary = raw_unit.get("object_summary")
                objects = (
                    summary.get("objects")
                    if isinstance(summary, Mapping)
                    else None
                )
                for raw_object in _safe_list(objects):
                    if not isinstance(raw_object, Mapping):
                        continue
                    object_ref = str(raw_object.get("object_ref") or "").strip()
                    if (
                        object_ref
                        and raw_object.get("safe_to_delete") is True
                        and _is_practice_slide(
                            raw_unit,
                            tail_candidate=(
                                position > max(1, int(len(material_units) * 0.55))
                            ),
                        )
                    ):
                        safe_refs.append(f"{source_ref}:object:{object_ref}")
                object_refs_by_slide[source_ref] = safe_refs
            elif purpose == "textbook":
                textbook_refs.append(source_ref)

    exercise_refs = [
        f"exercise:{candidate_id}"
        for item in _safe_list(resource_pack.get("exercises"))
        if isinstance(item, Mapping)
        and item.get("selection_status") != "excluded"
        and (candidate_id := str(item.get("candidate_id") or "").strip())
    ]
    evidence = resource_pack.get("evidence")
    question = evidence.get("question") if isinstance(evidence, Mapping) else None
    question_refs = [
        f"question:{question_id}"
        for item in _safe_list(
            question.get("items") if isinstance(question, Mapping) else None
        )
        if isinstance(item, Mapping)
        and (question_id := item.get("question_id")) is not None
    ]
    assessment = (
        evidence.get("assessment") if isinstance(evidence, Mapping) else None
    )
    assessment_refs = [
        f"assessment:{assessment_id}"
        for item in _safe_list(
            assessment.get("assessments")
            if isinstance(assessment, Mapping)
            else None
        )
        if isinstance(item, Mapping)
        and (assessment_id := item.get("assessment_id")) is not None
    ]
    review_refs = [
        f"post_review:{review_id}"
        for item in _safe_list(resource_pack.get("prior_reviews"))
        if isinstance(item, Mapping)
        and (review_id := item.get("review_id")) is not None
    ]
    lesson = resource_pack.get("lesson")
    lesson_id = (
        str(lesson.get("lesson_node_id") or "").strip()
        if isinstance(lesson, Mapping)
        else ""
    )
    other_refs = [f"lesson:{lesson_id}"] if lesson_id else []
    classroom = resource_pack.get("classroom")
    if (
        isinstance(classroom, Mapping)
        and classroom.get("teacher_context")
    ):
        other_refs.append("teacher_context")
    allowed_citations = list(
        dict.fromkeys(
            [
                *other_refs,
                *material_refs,
                *exercise_refs,
                *question_refs,
                *assessment_refs,
                *review_refs,
            ]
        )
    )
    return {
        "schemas": {
            "knowledge_objectives": {
                "type": "array",
                "item": {
                    "text": "string",
                    "citations": "non-empty array of allowed citation IDs",
                },
            },
            "focus_points": {
                "type": "array",
                "item": {
                    "kind": "key or difficulty",
                    "title": "string",
                    "rationale": "string",
                    "citations": "non-empty array of allowed citation IDs",
                },
            },
            "anticipated_difficulties": {
                "type": "array",
                "item": {
                    "text": "string",
                    "citations": "non-empty array of allowed citation IDs",
                },
            },
            "lesson_flow": {
                "type": "array",
                "required_phases": [
                    "introduction",
                    "exploration",
                    "example",
                    "practice",
                    "summary",
                ],
                "item": {
                    "phase": "one required phase",
                    "title": "string",
                    "purpose": "string",
                    "suggested_minutes": "integer from 0 through 120",
                    "citations": "non-empty array of allowed citation IDs",
                },
            },
            "exercise_recommendations": {
                "type": "array",
                "item": {
                    "source_ref": (
                        "one allowed_exercise_recommendation_ref"
                    ),
                    "action": (
                        "include, backup, move_after_class, exclude, "
                        "or replace_shorter"
                    ),
                    "target_slide_ref": (
                        "one allowed slide ref when action is include"
                    ),
                    "title": "string",
                    "reason": "string",
                    "estimated_minutes": "integer from 1 through 60",
                    "citations": "non-empty array of allowed citation IDs",
                },
            },
            "slide_adaptations": {
                "type": "array",
                "item": {
                    "slide_ref": "one allowed_slide_ref",
                    "role": "one allowed_role",
                    "action": "keep or delete",
                    "delete_object_refs": (
                        "array of allowed object refs for this slide"
                    ),
                    "textbook_refs": "array of allowed textbook refs",
                    "reason": "string",
                    "citations": "non-empty array of allowed citation IDs",
                },
            },
            "uncertainties": {"type": "array of strings"},
        },
        "slide_adaptations": {
            "required_count": len(slide_refs),
            "allowed_slide_refs": slide_refs,
            "allowed_roles": [
                "introduction",
                "exploration",
                "explanation",
                "example",
                "practice",
                "summary",
                "other",
            ],
            "allowed_actions": ["keep", "delete"],
            "allowed_textbook_refs": textbook_refs,
            "allowed_object_refs_by_slide": object_refs_by_slide,
            "allowed_citation_refs": allowed_citations,
        },
        "allowed_exercise_recommendation_refs": [
            *exercise_refs,
            *question_refs,
        ],
        "all_allowed_citation_refs": allowed_citations,
    }


def _is_practice_slide(
    unit: Mapping[str, object], *, tail_candidate: bool = True
) -> bool:
    value = f"{unit.get('title') or ''}\n{unit.get('text') or ''}"
    if re.search(
        r"学习用优翼|产品与服务|选购指南|公益教研|扫码获取|教学工具书",
        value,
    ):
        return False
    if re.search(
        r"典型例题|(?:^|[\s：:（(])例"
        r"(?:题|\s*\d{1,3}(?=\s|[：:、.．)）]|$))",
        value,
    ):
        return False
    if re.search(r"练习|练一练|巩固|检测|训练|做一做|试一试", value):
        return True
    title = str(unit.get("title") or "").strip()
    return tail_candidate and bool(
        re.match(r"^(?:\d{1,2}|[（(]\d{1,2}[）)]?)\s*(?:[.．、\[]|$)", title)
    )


def _safe_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _response_text(response: object) -> str:
    if isinstance(response, Mapping):
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, Mapping):
                message = choice.get("message")
                if isinstance(message, Mapping):
                    content = message.get("content")
                    if isinstance(content, str):
                        return content
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
    raise TeachingPrepValidationError(
        "lesson model response text is unavailable"
    )


__all__ = ["WorkspaceLessonModelAdapter"]
