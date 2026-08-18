from __future__ import annotations

import base64
import io
import json
import re
from collections.abc import Callable, Mapping
from typing import Any

from PIL import Image

from backend.llm.errors import classify_llm_error, is_retryable_error
from backend.llm.json_repair import parse_json_object_locally
from backend.llm.usage import response_diagnostics
from backend.teaching_prep.application.lesson_drafts import (
    validate_review_findings_payload,
)
from backend.teaching_prep.domain.errors import TeachingPrepValidationError
from backend.workspaces.model_policy import (
    WorkspaceModelGateway,
    WorkspaceModelRequest,
)


_MAX_OUTPUT_TOKENS = 16_000
_MAX_MODEL_ROUNDS = 6
_MAX_PAGES_PER_ROUND = 4
_MAX_ATTEMPTS_PER_ROUND = 2
_MAX_FIRST_ROUND_FINDINGS_CALLS = 2
_ROUND_TIMEOUT_SECONDS = 300
_MAX_FIRST_ROUND_SLIDES = 40
_THUMBNAIL_MAX_EDGE = 480
_THUMBNAIL_JPEG_QUALITY = 70
_UNIT_TEXT_LIMIT = 400
_OBJECT_TEXT_LIMIT = 80
_THINKING_EXCERPT_LIMIT = 500
_FINDINGS_TRACE_LIMIT = 20
_FINDING_TEXT_LIMIT = 240
_PAGE_SOURCE_REF = re.compile(r"^material:([0-9a-f]{32}):unit:(\d+)$")
_WINDOWS_PATH = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\)[^\r\n\t<>|\"']+")
_FILE_URL = re.compile(r"(?i)\bfile://[^\s<>\"']+")
_HTTP_URL = re.compile(r"(?i)\bhttps?://[^\s<>\"']+")
_GET_FROZEN_PAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "get_frozen_page",
        "description": (
            "Fetch one frozen local page image by an exact page_catalog "
            "source_ref. At most 4 pages per round. Files stay on this computer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_ref": {
                    "type": "string",
                    "description": "Exact page_catalog source_ref",
                }
            },
            "required": ["source_ref"],
        },
    },
}


_SYSTEM_INSTRUCTION = """\
你是初中数学备课草稿助手。只能依据用户提供的已冻结资源包。
返回单个 json（JSON）对象，只能包含 knowledge_objectives、focus_points、
anticipated_difficulties、lesson_flow、exercise_recommendations、
slide_adaptations、review_findings、uncertainties。必须遵守资源包中的
preparation_preferences：它是教师本次明确选择的倾向。

本次任务分两步：先审课，再改动。
第一步逐页审视主课件：内容是否合理、顺序是否得当、练习量是否合适、
与教材和新课标是否对应，结论写入 review_findings。每条 review_finding
包含 slide_refs（涉及的页，取 allowed_citation_refs 中的页引用，
整体判断可为空数组）、
finding、category（content、sequence、practice_load、alignment、other
之一）、suggested_action、citations。review_findings 至少一条，且必须
包含对整份课件的总体判断。
第二步给出 slide_adaptations：只为有明确价值的页面提出改动，没有价值
就 keep，允许整份课件零改动；零改动时 review_findings 必须说明这节课
为什么不需要改。slide_adaptations 逐页给出 slide_ref、role、action、
delete_object_refs、textbook_refs、target_position、suggested_text、
reason、citations；action 可取 keep、delete、reorder、hide、add、
modify_text：
- reorder 表示调整该页次序，只给出目标位置 target_position（从 1 开始的
  整数）；
- hide 表示建议隐藏该页，需教师人工执行；
- add 表示在该页之后复制其版式新增一页，suggested_text 简述新页内容，
  只能引用资源包内素材，不得编造题目；
- modify_text 表示建议修改本页文字，只给出建议新文本 suggested_text，
  需教师人工修改。
delete_object_refs 只能删除本页明确允许删除的普通文字或形状对象，且只有
action 为 keep 时才可填写，不得输出 WPS 指令。教材页码只能通过
textbook_refs 引用资源包内真实教材页，不能写猜测页码；教材页与讲授页
有语义对应时才填写，没有对应就返回空数组，不强行填写。练习删减只针对
确实重复或过量的题目，保留讲授例题和短题；补题不得超过偏好上限，且应
避免与原课件重复或直接照搬教辅原题。普通教辅页可以作为练习删留理由的
citation，但不能把它当成家庭作业要求。所有结论必须引用资源包内已有
citation ID；不得编造页码、题号、候选题或班级结论。课堂总时长由本机
另行计算。

用户输入中的 output_contract 是强制输出契约：
- slide_adaptations 必须逐一覆盖 allowed_slide_refs，数量必须等于
  required_count；即使某页是空页、广告页或保持不变，也不能漏掉。
- slide_ref 必须原样复制 allowed_slide_refs 中的完整字符串，禁止用 1、2、3
  等数字或页码简称。
- role 只能取 allowed_roles，action 只能取 allowed_actions。
- delete_object_refs 只能原样复制 allowed_object_refs_by_slide 中该页的对象引用；
  只有 action 为 keep 时才可填写。若一题由多个允许对象组成，应列出所有组成对象。
- target_position 只在 action 为 reorder 时填写，取 1 到 required_count
  之间的整数；其他 action 一律填 null。
- suggested_text 只在 action 为 add 或 modify_text 时填写；其他 action
  一律填 null。
- textbook_refs 只能原样复制 allowed_textbook_refs 中的完整字符串；没有对应页
  时返回空数组，禁止输出 10、11 等数字简称。
- citations 只能取 allowed_citation_refs；每条 slide_adaptation 的 citations 至少
  包含该条 slide_ref，并包含其所有 textbook_refs。禁止返回空 citations。
- review_findings 的 slide_refs 与 citations 只能取 allowed_citation_refs，
  每条 citations 禁止为空。
- exercise_recommendations 的 source_ref 只能取
  allowed_exercise_recommendation_refs；该列表为空时必须返回空数组。action 为 include
  时 target_slide_ref 必须取 allowed_slide_refs，优先放到删除冗余题后形成的空白区。
- allowed_exercise_recommendation_refs 非空表示系统已从教师勾选的教材或教辅原页自动
  选出候选题，不是教师逐题勾选；只在候选题对本课有明确价值时才返回
  action=include，没有价值时全部返回 backup 或 exclude。
- 整次最多 6 轮。第 1 轮消息已附主课件每页的小图（超过 40 页时只附前
  40 页）；第 1 轮先只交初步审课发现：只返回 {"review_findings": [...]}
  对象，每条字段与最终版相同，不能调用工具，不要给出改编方案。第 2 轮
  起可取页细看：调用 get_frozen_page，source_ref 必须原样复制
  page_catalog，细看取图每轮最多 4 页。最后一轮必须返回规定完整 json
  对象，发现清单可相对初步版修正，不要再调用工具。不得引用目录和图中
  都没有的页码或题号。
- 检查后段练习页的 allowed_object_refs_by_slide；只在确认存在重复或过量题目、
  且能安全识别完整边界时，才在 action=keep 的页面填写 delete_object_refs，
  否则全部留空。不得为凑数量删除标题、答案说明、定理、讲授例题或无法确认
  完整边界的对象。
- delete_object_refs 只能填写在 role=practice 且 action=keep 的页；
  禁止在 introduction、objective、exploration、example、summary、other 页删除对象。
  当课件已有多张练习页时，优先从后段重复或过量练习中删除一整道题的所有组成
  对象；若插入题的目标页已有可安全删除的完整题目，可在该页删出空位后插入。
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
        page_loader: Callable[[str], Mapping[str, object]] | None = None,
        observer: Callable[[Mapping[str, object]], None] | None = None,
    ) -> dict[str, Any]:
        catalog = compact_resource_pack_for_model(resource_pack)
        slide_images = _reference_slide_thumbnails(resource_pack, page_loader)
        if slide_images:
            catalog["first_round_slide_images"] = {
                "attached": len(slide_images),
                "limit": _MAX_FIRST_ROUND_SLIDES,
                "note": (
                    "主课件每页的小图已随本条消息附上，按 unit_index 对应 "
                    "page_catalog；超出上限或未附上的页从第 2 轮起用 "
                    "get_frozen_page 取图。"
                ),
            }
        messages: list[dict[str, object]] = [
            {"role": "system", "content": _SYSTEM_INSTRUCTION},
            {
                "role": "user",
                "content": vision_user_content(
                    catalog,
                    slide_images,
                    max_images=_MAX_FIRST_ROUND_SLIDES,
                ),
            },
        ]
        _emit(
            observer,
            {
                "round": 1,
                "phase": "started",
                "summary": (
                    f"已发送本课目录与 {len(slide_images)} 页课件小图，"
                    "等待模型审课。"
                    if slide_images
                    else "已发送本课目录，等待模型按页取图。"
                ),
                "thinking_excerpt": None,
                "tool": None,
                "result": None,
                "model_calls_used": 0,
                "model_calls_max": _MAX_MODEL_ROUNDS,
            },
        )
        self._initial_review_findings(
            messages=messages,
            operation_id=operation_id,
            resource_pack=resource_pack,
            observer=observer,
        )
        last_error: TeachingPrepValidationError | None = None
        for round_number in range(2, _MAX_MODEL_ROUNDS + 1):
            final_round = round_number == _MAX_MODEL_ROUNDS
            kwargs: dict[str, object] = {
                "messages": messages,
                "max_tokens": _MAX_OUTPUT_TOKENS,
            }
            if final_round:
                kwargs["response_format"] = {"type": "json_object"}
            else:
                kwargs["tools"] = [_GET_FROZEN_PAGE_TOOL]
                kwargs["tool_choice"] = "auto"
            response = self._call_round(
                round_number=round_number,
                operation_id=operation_id,
                kwargs=kwargs,
                observer=observer,
            )
            thinking = _teacher_safe_excerpt(_response_thinking(response))
            if thinking:
                _emit(
                    observer,
                    {
                        "round": round_number,
                        "phase": "thinking",
                        "summary": "模型正在分析本课目录和已取原页。",
                        "thinking_excerpt": thinking,
                        "tool": None,
                        "result": None,
                        "model_calls_used": round_number,
                        "model_calls_max": _MAX_MODEL_ROUNDS,
                    },
                )
            tool_calls = _response_tool_calls(response)
            if tool_calls and not final_round:
                assistant = _assistant_message_for_history(response)
                if assistant is not None:
                    messages.append(assistant)
                images: list[dict[str, object]] = []
                for index, call in enumerate(tool_calls):
                    accepted = index < _MAX_PAGES_PER_ROUND
                    events, image, tool_message = _execute_page_tool(
                        call,
                        page_loader=page_loader,
                        accepted=accepted,
                    )
                    for event in events:
                        event["round"] = round_number
                        event["model_calls_used"] = round_number
                        event["model_calls_max"] = _MAX_MODEL_ROUNDS
                        _emit(observer, event)
                    messages.append(tool_message)
                    if image is not None:
                        images.append(image)
                if images:
                    messages.append(
                        {
                            "role": "user",
                            "content": vision_user_content(
                                {
                                    "returned_pages": [
                                        str(item.get("label") or "")
                                        for item in images
                                    ]
                                },
                                images,
                                max_images=_MAX_PAGES_PER_ROUND,
                            ),
                        }
                    )
                _emit(
                    observer,
                    {
                        "round": round_number,
                        "phase": "round_done",
                        "summary": f"第 {round_number} 轮已取回 {len(images)} 页。",
                        "thinking_excerpt": None,
                        "tool": None,
                        "result": None,
                        "model_calls_used": round_number,
                        "model_calls_max": _MAX_MODEL_ROUNDS,
                    },
                )
                continue
            diagnostics = response_diagnostics(response)
            if bool(diagnostics.get("output_truncated")):
                last_error = TeachingPrepValidationError(
                    "lesson model output was truncated"
                )
                if final_round:
                    break
                messages.append(
                    {
                        "role": "user",
                        "content": "上一轮输出被截断。请返回完整规定 json 对象，或调用 get_frozen_page。",
                    }
                )
                continue
            try:
                payload = _parse_model_object(_response_text(response))
            except TeachingPrepValidationError as exc:
                last_error = exc
                if final_round:
                    break
                messages.append(
                    {
                        "role": "user",
                        "content": "请返回规定 json 对象，或调用 get_frozen_page 查看原页。",
                    }
                )
                continue
            _emit(
                observer,
                {
                    "round": round_number,
                    "phase": "final_accepted",
                    "summary": "已收到改编清单。",
                    "thinking_excerpt": None,
                    "tool": None,
                    "result": None,
                    "model_calls_used": round_number,
                    "model_calls_max": _MAX_MODEL_ROUNDS,
                },
            )
            return payload
        _emit(
            observer,
            {
                "round": _MAX_MODEL_ROUNDS,
                "phase": "failed",
                "summary": "本次没有完成（未改原 PPT）。",
                "thinking_excerpt": None,
                "tool": None,
                "result": None,
                "model_calls_used": _MAX_MODEL_ROUNDS,
                "model_calls_max": _MAX_MODEL_ROUNDS,
            },
        )
        raise last_error or TeachingPrepValidationError(
            "lesson model did not return a valid JSON object"
        )

    def _initial_review_findings(
        self,
        *,
        messages: list[dict[str, object]],
        operation_id: str,
        resource_pack: Mapping[str, object],
        observer: Callable[[Mapping[str, object]], None] | None,
    ) -> list[dict[str, object]]:
        """Round 1: preliminary review findings only; page tools come later."""
        last_error: TeachingPrepValidationError | None = None
        for attempt in range(1, _MAX_FIRST_ROUND_FINDINGS_CALLS + 1):
            response = self._call_round(
                round_number=1,
                operation_id=operation_id,
                kwargs={
                    "messages": messages,
                    "max_tokens": _MAX_OUTPUT_TOKENS,
                    "response_format": {"type": "json_object"},
                },
                observer=observer,
            )
            thinking = _teacher_safe_excerpt(_response_thinking(response))
            if thinking:
                _emit(
                    observer,
                    {
                        "round": 1,
                        "phase": "thinking",
                        "summary": "模型正在逐页审课。",
                        "thinking_excerpt": thinking,
                        "tool": None,
                        "result": None,
                        "model_calls_used": 1,
                        "model_calls_max": _MAX_MODEL_ROUNDS,
                    },
                )
            try:
                diagnostics = response_diagnostics(response)
                if bool(diagnostics.get("output_truncated")):
                    raise TeachingPrepValidationError(
                        "lesson model output was truncated"
                    )
                payload = _parse_model_object(_response_text(response))
                findings = validate_review_findings_payload(
                    payload.get("review_findings"),
                    resource_pack,
                )
            except TeachingPrepValidationError as exc:
                last_error = exc
                if attempt < _MAX_FIRST_ROUND_FINDINGS_CALLS:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "第 1 轮只接受初步审课发现：请只返回 "
                                '{"review_findings": [...]} 对象，每条包含 '
                                "slide_refs、finding、category、"
                                "suggested_action、citations；不要调用工具，"
                                "不要给出改编方案。"
                            ),
                        }
                    )
                continue
            assistant = _assistant_message_for_history(response)
            if assistant is not None:
                # Round 1 offers no tools; never echo dangling tool_calls.
                assistant.pop("tool_calls", None)
                messages.append(assistant)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "已收到初步审课发现。从本轮起可调用 get_frozen_page "
                        "细看原页（每轮最多 4 页）；最后一轮返回规定完整 "
                        "json 对象，发现清单可相对初步版修正。"
                    ),
                }
            )
            _emit(
                observer,
                {
                    "round": 1,
                    "phase": "findings_ready",
                    "summary": (
                        f"已给出初步审课发现 {len(findings)} 条"
                        "（细看后可能修正）"
                    ),
                    "thinking_excerpt": None,
                    "tool": None,
                    "result": None,
                    "findings": _findings_trace_items(findings),
                    "model_calls_used": 1,
                    "model_calls_max": _MAX_MODEL_ROUNDS,
                },
            )
            return findings
        _emit(
            observer,
            {
                "round": 1,
                "phase": "failed",
                "summary": "本次没有完成（未改原 PPT）。",
                "thinking_excerpt": None,
                "tool": None,
                "result": None,
                "model_calls_used": 1,
                "model_calls_max": _MAX_MODEL_ROUNDS,
            },
        )
        raise last_error or TeachingPrepValidationError(
            "lesson model did not return valid review findings"
        )

    def _call_round(
        self,
        *,
        round_number: int,
        operation_id: str,
        kwargs: Mapping[str, object],
        observer: Callable[[Mapping[str, object]], None] | None,
    ) -> object:
        for attempt in range(1, _MAX_ATTEMPTS_PER_ROUND + 1):
            try:
                return self.gateway.chat_completions(
                    request=WorkspaceModelRequest(
                        purpose="lesson_draft",
                        data_classification="teaching_material_aggregate",
                        operation_id=operation_id,
                        max_physical_calls=(
                            _MAX_MODEL_ROUNDS * _MAX_ATTEMPTS_PER_ROUND
                        ),
                    ),
                    client=self.client,
                    model=self.model,
                    kwargs=kwargs,
                    timeout_override_seconds=_ROUND_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                if attempt >= _MAX_ATTEMPTS_PER_ROUND or not is_retryable_error(
                    exc
                ):
                    raise
                _emit(
                    observer,
                    {
                        "round": round_number,
                        "phase": "round_retry",
                        "summary": (
                            f"第 {round_number} 轮遇到可恢复的网络或服务异常"
                            f"（{classify_llm_error(exc).value}），"
                            "已自动重试一次。"
                        ),
                        "thinking_excerpt": None,
                        "tool": None,
                        "result": None,
                        "model_calls_used": round_number,
                        "model_calls_max": _MAX_MODEL_ROUNDS,
                    },
                )
        raise TeachingPrepValidationError("lesson model round did not run")


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
                    "action": (
                        "keep, delete, reorder, hide, add, or modify_text"
                    ),
                    "delete_object_refs": (
                        "array of allowed object refs for this slide"
                    ),
                    "textbook_refs": "array of allowed textbook refs",
                    "target_position": (
                        "integer from 1 through required_count when action "
                        "is reorder, otherwise null"
                    ),
                    "suggested_text": (
                        "suggested new text when action is add or "
                        "modify_text, otherwise null"
                    ),
                    "reason": "string",
                    "citations": "non-empty array of allowed citation IDs",
                },
            },
            "review_findings": {
                "type": "array",
                "item": {
                    "slide_refs": "array of allowed citation IDs, may be empty",
                    "finding": "string",
                    "category": (
                        "content, sequence, practice_load, alignment, "
                        "or other"
                    ),
                    "suggested_action": "string",
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
            "allowed_actions": [
                "keep",
                "delete",
                "reorder",
                "hide",
                "add",
                "modify_text",
            ],
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


_IMAGE_PAGE_LABELS = {
    "exercise": "教辅原页",
    "textbook": "教材原页",
    "reference_ppt": "主课件原页",
}


def _reference_slide_thumbnails(
    resource_pack: Mapping[str, object],
    page_loader: Callable[[str], Mapping[str, object]] | None,
    *,
    max_slides: int = _MAX_FIRST_ROUND_SLIDES,
) -> list[dict[str, object]]:
    if page_loader is None:
        return []
    images: list[dict[str, object]] = []
    for raw_material in _safe_list(resource_pack.get("materials")):
        if not isinstance(raw_material, Mapping):
            continue
        if str(raw_material.get("purpose") or "") != "reference_ppt":
            continue
        link_id = str(raw_material.get("link_id") or "").strip()
        if not link_id:
            continue
        version_id = str(raw_material.get("material_version_id") or "")
        for raw_unit in _safe_list(raw_material.get("units")):
            if len(images) >= max_slides:
                return images
            if not isinstance(raw_unit, Mapping):
                continue
            unit_index = raw_unit.get("unit_index")
            if isinstance(unit_index, bool) or not isinstance(unit_index, int):
                continue
            source_ref = f"material:{link_id}:unit:{unit_index}"
            try:
                loaded = page_loader(source_ref)
            except Exception:
                continue
            if not isinstance(loaded, Mapping) or loaded.get("ok") is not True:
                continue
            content = loaded.get("content")
            if not isinstance(content, (bytes, bytearray)) or not content:
                continue
            mime_type, data = _thumbnail_bytes(
                bytes(content),
                str(loaded.get("mime_type") or "image/png"),
            )
            images.append(
                {
                    "purpose": "reference_ppt",
                    "material_version_id": version_id,
                    "material_unit_id": (
                        loaded.get("unit_id") or raw_unit.get("unit_id")
                    ),
                    "unit_index": unit_index,
                    "mime_type": mime_type,
                    "content": data,
                    "label": (
                        loaded.get("label")
                        or page_label("reference_ppt", unit_index)
                    ),
                }
            )
    return images


def _thumbnail_bytes(content: bytes, mime_type: str) -> tuple[str, bytes]:
    try:
        with Image.open(io.BytesIO(content)) as image:
            rgb = image.convert("RGB")
            rgb.thumbnail((_THUMBNAIL_MAX_EDGE, _THUMBNAIL_MAX_EDGE))
            buffer = io.BytesIO()
            rgb.save(
                buffer,
                format="JPEG",
                quality=_THUMBNAIL_JPEG_QUALITY,
                optimize=True,
            )
            data = buffer.getvalue()
        if data and len(data) < len(content):
            return "image/jpeg", data
    except Exception:
        pass
    if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
        mime_type = "image/png"
    return mime_type, content


def vision_user_content(
    payload: Mapping[str, object],
    raw_images: object,
    *,
    max_images: int = _MAX_PAGES_PER_ROUND,
) -> object:
    payload_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if not isinstance(raw_images, list) or not raw_images:
        return payload_json
    content: list[dict[str, object]] = [{"type": "text", "text": payload_json}]
    for raw in raw_images[:max_images]:
        if not isinstance(raw, Mapping):
            continue
        data = raw.get("content")
        if not isinstance(data, bytes) or not data:
            continue
        mime_type = str(raw.get("mime_type") or "image/png")
        if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
            continue
        purpose = str(raw.get("purpose") or "")
        label = _IMAGE_PAGE_LABELS.get(purpose, "资料原页")
        encoded = base64.b64encode(data).decode("ascii")
        content.extend(
            [
                {
                    "type": "text",
                    "text": (
                        f"{label}；"
                        f"material_version_id={raw.get('material_version_id')};"
                        f"material_unit_id={raw.get('material_unit_id')};"
                        f"unit_index={raw.get('unit_index')}"
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{encoded}"
                    },
                },
            ]
        )
    return content


def compact_resource_pack_for_model(
    resource_pack: Mapping[str, object],
) -> dict[str, object]:
    materials: list[dict[str, object]] = []
    page_catalog: list[dict[str, object]] = []
    for raw_material in _safe_list(resource_pack.get("materials")):
        if not isinstance(raw_material, Mapping):
            continue
        link_id = str(raw_material.get("link_id") or "").strip()
        purpose = str(raw_material.get("purpose") or "").strip()
        if not link_id:
            continue
        units: list[dict[str, object]] = []
        for raw_unit in _safe_list(raw_material.get("units")):
            if not isinstance(raw_unit, Mapping):
                continue
            unit_index = raw_unit.get("unit_index")
            if isinstance(unit_index, bool) or not isinstance(unit_index, int):
                continue
            source_ref = f"material:{link_id}:unit:{unit_index}"
            unit_id = str(raw_unit.get("unit_id") or "").strip()
            title = str(raw_unit.get("title") or "").strip()
            compact_unit = {
                "source_ref": source_ref,
                "unit_index": unit_index,
                "title": title,
                "text": _truncate_text(raw_unit.get("text"), _UNIT_TEXT_LIMIT),
                "objects": _compact_objects(raw_unit.get("object_summary")),
            }
            units.append(compact_unit)
            page_catalog.append(
                {
                    "source_ref": source_ref,
                    "purpose": purpose,
                    "unit_index": unit_index,
                    "title": title,
                    "unit_id": unit_id,
                }
            )
        materials.append(
            {
                "link_id": link_id,
                "purpose": purpose,
                "material_name": str(raw_material.get("material_name") or ""),
                "units": units,
            }
        )
    lesson = resource_pack.get("lesson")
    classroom = resource_pack.get("classroom")
    exercises = []
    for item in _safe_list(resource_pack.get("exercises")):
        if not isinstance(item, Mapping):
            continue
        exercises.append(
            {
                "candidate_id": str(item.get("candidate_id") or ""),
                "question_number": item.get("question_number"),
                "content_label": _truncate_text(
                    item.get("content_label"), 160
                ),
                "selection_status": item.get("selection_status"),
            }
        )
    return {
        "lesson": (
            {
                "lesson_node_id": lesson.get("lesson_node_id"),
                "title": lesson.get("title"),
            }
            if isinstance(lesson, Mapping)
            else {}
        ),
        "classroom": (
            {
                "teacher_context": _truncate_text(
                    classroom.get("teacher_context"), 1_000
                )
            }
            if isinstance(classroom, Mapping)
            else {}
        ),
        "preparation_preferences": resource_pack.get("preparation_preferences"),
        "materials": materials,
        "exercises": exercises,
        "page_catalog": page_catalog,
        "output_contract": _model_output_contract(resource_pack),
        "tool_limits": {
            "tool": "get_frozen_page",
            "max_pages_per_round": _MAX_PAGES_PER_ROUND,
            "max_model_rounds": _MAX_MODEL_ROUNDS,
        },
    }


def page_catalog_entry(
    resource_pack: Mapping[str, object],
    source_ref: str,
) -> dict[str, object] | None:
    match = _PAGE_SOURCE_REF.fullmatch(str(source_ref or "").strip())
    if match is None:
        return None
    link_id = match.group(1)
    unit_index = int(match.group(2))
    for raw_material in _safe_list(resource_pack.get("materials")):
        if not isinstance(raw_material, Mapping):
            continue
        if str(raw_material.get("link_id") or "").strip() != link_id:
            continue
        purpose = str(raw_material.get("purpose") or "").strip()
        for raw_unit in _safe_list(raw_material.get("units")):
            if not isinstance(raw_unit, Mapping):
                continue
            if raw_unit.get("unit_index") != unit_index:
                continue
            return {
                "source_ref": f"material:{link_id}:unit:{unit_index}",
                "purpose": purpose,
                "unit_index": unit_index,
                "title": str(raw_unit.get("title") or ""),
                "unit_id": str(raw_unit.get("unit_id") or ""),
            }
    return None


def page_label(purpose: object, unit_index: object) -> str:
    labels = {
        "exercise": "教辅",
        "textbook": "教材",
        "reference_ppt": "主课件",
        "answer": "答案",
        "supplement": "补充资料",
    }
    role = labels.get(str(purpose or ""), "资料")
    try:
        page = int(unit_index)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return role
    return f"{role} 第 {page} 页"


def _compact_objects(summary: object) -> list[dict[str, object]]:
    objects = summary.get("objects") if isinstance(summary, Mapping) else None
    result: list[dict[str, object]] = []
    for raw in _safe_list(objects):
        if not isinstance(raw, Mapping):
            continue
        object_ref = str(raw.get("object_ref") or "").strip()
        if not object_ref:
            continue
        result.append(
            {
                "object_ref": object_ref,
                "text": _truncate_text(raw.get("text"), _OBJECT_TEXT_LIMIT),
                "safe_to_delete": raw.get("safe_to_delete") is True,
            }
        )
    return result


def _truncate_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _findings_trace_items(
    findings: list[Mapping[str, object]],
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for item in findings[:_FINDINGS_TRACE_LIMIT]:
        pages: list[int] = []
        for raw_ref in _safe_list(item.get("slide_refs")):
            match = _PAGE_SOURCE_REF.fullmatch(str(raw_ref or "").strip())
            if match is not None:
                pages.append(int(match.group(2)))
        items.append(
            {
                "finding": _truncate_text(
                    item.get("finding"), _FINDING_TEXT_LIMIT
                ),
                "category": str(item.get("category") or ""),
                "pages": pages,
            }
        )
    return items


def _parse_model_object(text: str) -> dict[str, Any]:
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


def _execute_page_tool(
    call: Mapping[str, object],
    *,
    page_loader: Callable[[str], Mapping[str, object]] | None,
    accepted: bool,
) -> tuple[
    list[dict[str, object]],
    dict[str, object] | None,
    dict[str, object],
]:
    call_id = str(call.get("id") or "tool-call")
    source_ref = str(call.get("source_ref") or "").strip()
    safe_ref = source_ref if _PAGE_SOURCE_REF.fullmatch(source_ref) else ""
    tool = {
        "name": "get_frozen_page",
        "purpose": None,
        "page": None,
        "source_ref": safe_ref,
    }
    if not accepted:
        payload = {
            "ok": False,
            "error": "this round already used 4 pages",
            "source_ref": source_ref,
        }
        event = {
            "phase": "tool_result",
            "summary": "本轮已达 4 页上限，其余取页已拒绝。",
            "thinking_excerpt": None,
            "tool": tool,
            "result": {"ok": False, "label": "本轮超出 4 页", "preview_url": None},
        }
        return [event], None, _tool_message(call_id, payload)
    if not safe_ref:
        payload = {
            "ok": False,
            "error": "source_ref is not an allowed page_catalog value",
            "source_ref": source_ref,
        }
        event = {
            "phase": "tool_call",
            "summary": "取页参数无效，已拒绝且未读盘。",
            "thinking_excerpt": None,
            "tool": tool,
            "result": {"ok": False, "label": "无效页引用", "preview_url": None},
        }
        return [event], None, _tool_message(call_id, payload)
    if page_loader is None:
        payload = {"ok": False, "error": "page lookup is unavailable"}
        event = {
            "phase": "tool_result",
            "summary": "本机取页不可用。",
            "thinking_excerpt": None,
            "tool": tool,
            "result": {"ok": False, "label": "取页不可用", "preview_url": None},
        }
        return [event], None, _tool_message(call_id, payload)
    loaded = dict(page_loader(source_ref))
    purpose = loaded.get("purpose")
    unit_index = loaded.get("unit_index")
    label = str(loaded.get("label") or page_label(purpose, unit_index))
    tool = {
        "name": "get_frozen_page",
        "purpose": str(purpose or "") or None,
        "page": unit_index if isinstance(unit_index, int) else None,
        "source_ref": safe_ref,
    }
    ok = loaded.get("ok") is True
    preview_url = loaded.get("preview_url")
    call_event = {
        "phase": "tool_call",
        "summary": f"取页 · {label}",
        "thinking_excerpt": None,
        "tool": tool,
        "result": None,
    }
    result_event = {
        "phase": "tool_result",
        "summary": f"已返回{label}" if ok else f"未能返回{label}",
        "thinking_excerpt": None,
        "tool": tool,
        "result": {
            "ok": ok,
            "label": label if ok else str(loaded.get("error") or label),
            "preview_url": preview_url if isinstance(preview_url, str) else None,
        },
    }
    image = None
    content = loaded.get("content")
    if ok and isinstance(content, (bytes, bytearray)) and content:
        image = {
            "purpose": purpose,
            "material_unit_id": loaded.get("unit_id"),
            "unit_index": unit_index,
            "mime_type": loaded.get("mime_type") or "image/png",
            "content": bytes(content),
            "label": label,
        }
    tool_payload = {
        "ok": ok,
        "source_ref": source_ref,
        "label": label,
        "error": loaded.get("error"),
    }
    return [call_event, result_event], image, _tool_message(call_id, tool_payload)


def _tool_message(call_id: str, payload: Mapping[str, object]) -> dict[str, object]:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(
            {key: value for key, value in payload.items() if value is not None},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    }


def _emit(
    observer: Callable[[Mapping[str, object]], None] | None,
    event: Mapping[str, object],
) -> None:
    if observer is None:
        return
    try:
        observer(dict(event))
    except Exception:
        return


def _response_message(response: object) -> Mapping[str, object]:
    if isinstance(response, Mapping):
        choices = response.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
            message = choices[0].get("message")
            if isinstance(message, Mapping):
                return message
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        message = getattr(choices[0], "message", None)
        if isinstance(message, Mapping):
            return message
        values = {
            "content": getattr(message, "content", None),
            "tool_calls": getattr(message, "tool_calls", None),
            "function_call": getattr(message, "function_call", None),
            "reasoning_content": getattr(message, "reasoning_content", None),
            "reasoning": getattr(message, "reasoning", None),
        }
        return {key: value for key, value in values.items() if value is not None}
    return {}


def _response_text(response: object) -> str:
    message = _response_message(response)
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts = [
            str(item.get("text") or "")
            for item in content
            if isinstance(item, Mapping) and item.get("type") in {None, "text"}
        ]
        text = "\n".join(part for part in parts if part).strip()
        if text:
            return text
    raise TeachingPrepValidationError(
        "lesson model response text is unavailable"
    )


def _response_thinking(response: object) -> str:
    message = _response_message(response)
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    content = message.get("content")
    if isinstance(content, list):
        parts = [
            str(item.get("text") or item.get("reasoning") or "")
            for item in content
            if isinstance(item, Mapping)
            and str(item.get("type") or "") in {"reasoning", "thinking"}
        ]
        text = "\n".join(part for part in parts if part).strip()
        if text:
            return text
    return ""


def _response_tool_calls(response: object) -> list[dict[str, object]]:
    message = _response_message(response)
    raw_calls = message.get("tool_calls")
    calls: list[dict[str, object]] = []
    if isinstance(raw_calls, list):
        for index, raw in enumerate(raw_calls):
            parsed = _parse_tool_call(raw, index)
            if parsed is not None:
                calls.append(parsed)
    function_call = message.get("function_call")
    if not calls and function_call is not None:
        parsed = _parse_tool_call(
            {"id": "call-0", "function": function_call},
            0,
        )
        if parsed is not None:
            calls.append(parsed)
    return calls


def _parse_tool_call(raw: object, index: int) -> dict[str, object] | None:
    if not isinstance(raw, Mapping):
        name = getattr(raw, "function", None)
        call_id = str(getattr(raw, "id", "") or f"call-{index}")
        function = name if isinstance(name, Mapping) else {
            "name": getattr(name, "name", None),
            "arguments": getattr(name, "arguments", None),
        }
        raw = {"id": call_id, "function": function}
    if not isinstance(raw, Mapping):
        return None
    function = raw.get("function")
    if not isinstance(function, Mapping):
        function = {
            "name": raw.get("name"),
            "arguments": raw.get("arguments"),
        }
    name = str(function.get("name") or "").strip()
    if name != "get_frozen_page":
        return None
    arguments = function.get("arguments")
    parsed: Mapping[str, object]
    if isinstance(arguments, Mapping):
        parsed = arguments
    else:
        try:
            loaded = json.loads(str(arguments or "{}"))
        except json.JSONDecodeError:
            loaded = {}
        parsed = loaded if isinstance(loaded, Mapping) else {}
    source_ref = str(parsed.get("source_ref") or "").strip()
    return {
        "id": str(raw.get("id") or f"call-{index}"),
        "name": name,
        "source_ref": source_ref,
        "raw": dict(raw),
    }


def _assistant_message_for_history(response: object) -> dict[str, object] | None:
    message = _response_message(response)
    if not message:
        return None
    payload: dict[str, object] = {"role": "assistant"}
    content = message.get("content")
    if isinstance(content, str):
        payload["content"] = content
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        payload["tool_calls"] = tool_calls
    return payload


def _teacher_safe_excerpt(value: object, *, limit: int = _THINKING_EXCERPT_LIMIT) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = _FILE_URL.sub("[LOCAL_PATH_REDACTED]", text)
    text = _HTTP_URL.sub("[URL_REDACTED]", text)
    text = _WINDOWS_PATH.sub("[LOCAL_PATH_REDACTED]", text)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


__all__ = [
    "WorkspaceLessonModelAdapter",
    "compact_resource_pack_for_model",
    "page_catalog_entry",
    "page_label",
    "vision_user_content",
]
