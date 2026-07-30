from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping, Sequence

from backend.llm import LLMRequestKind, usage_fields
from question_bank.database.schema import connect
from question_bank.models.tag_schema import TagAnalysis, TaggingContext
from question_bank.services.ai_tagging_service import (
    AITaggingResult,
    AITaggingService,
    _with_quality,
    is_auto_saveable_result,
)
from question_bank.services.asset_path_service import (
    resolve_question_bank_asset_path,
)
from question_bank.services.question_service import QuestionService
from question_bank.services.rich_content_service import (
    load_question_rich_content,
)
from question_bank.training_criteria.analysis import (
    AnalysisProjection,
    GatewayBatchResponse,
    GatewayUsage,
    PlannedAnalysisBatch,
    QuestionAnalysisImage,
    QuestionAnalysisInput,
    combined_response_format,
)


_IMAGE_MARKER = re.compile(r"\[\[IMAGE:(?P<path>[^\]]+)\]\]")


class ExistingTagProjectionWriter:
    """Adapter that keeps combined tags on the established tag seam."""

    def __init__(
        self,
        *,
        question_service: QuestionService,
        tagging_service: AITaggingService,
    ) -> None:
        self.question_service = question_service
        self.tagging_service = tagging_service

    def write(
        self,
        question: QuestionAnalysisInput,
        payload: Mapping[str, Any],
        *,
        model_name: str,
        operation_id: str,
    ) -> Mapping[str, Any]:
        analysis = TagAnalysis.from_dict(dict(payload))
        contract = dict(
            question.taxonomy_contract
            or self.tagging_service.taxonomy_contract(
                question.tagging_context
            )
        )
        checked = _with_quality(
            AITaggingResult(
                ok=True,
                mock_mode=False,
                analysis=analysis,
                model_name=model_name,
            ),
            question.tagging_context,
            governance=self.tagging_service.taxonomy_governance,
            taxonomy_contract=contract,
            question_ref=str(question.question_id),
        )
        if not is_auto_saveable_result(checked):
            raise ValueError("tag projection did not meet the quality gate")
        assert checked.analysis is not None
        if not self.question_service.save_tag_analysis(
            question.question_id,
            checked.analysis,
            model_name=model_name,
            confidence=checked.analysis.confidence,
        ):
            raise RuntimeError("tag projection could not be saved")
        return {
            "schema_version": "tag-only-v1",
            "analysis": checked.analysis.to_dict(),
            "quality_status": checked.quality_status,
            "taxonomy_revision": checked.taxonomy_revision,
            "proposal_count": len(checked.proposals),
            "operation_id": operation_id,
        }


class OpenAICombinedAnalysisGateway:
    """Production Adapter with zero automatic model retries."""

    def __init__(self, *, protocol_adapter: Any, model_name: str) -> None:
        self.protocol_adapter = protocol_adapter
        self.model_name = str(model_name or "").strip()
        if not self.model_name:
            raise ValueError("model_name must not be empty")

    def analyze(
        self,
        batch: PlannedAnalysisBatch,
        *,
        projection: AnalysisProjection,
        operation_id: str,
        request_id: str,
    ) -> GatewayBatchResponse:
        started = perf_counter()
        response = self.protocol_adapter.responses(
            request_kind=LLMRequestKind.TAGGING,
            model=self.model_name,
            request_id=request_id,
            operation_id=operation_id,
            allow_retry=False,
            kwargs={
                "text": {
                    "format": combined_response_format(projection)
                },
                "input": _combined_prompt(batch, projection),
            },
        )
        output_text = str(
            getattr(response, "output_text", "") or ""
        ).strip()
        payload = json.loads(output_text)
        if not isinstance(payload, Mapping):
            raise ValueError("combined model response must be an object")
        normalized_usage = usage_fields(response)
        return GatewayBatchResponse(
            payload=dict(payload),
            model_name=self.model_name,
            usage=GatewayUsage(
                prompt_tokens=int(
                    normalized_usage.get("prompt_tokens") or 0
                ),
                completion_tokens=int(
                    normalized_usage.get("completion_tokens") or 0
                ),
                total_tokens=int(
                    normalized_usage.get("total_tokens") or 0
                ),
            ),
            latency_ms=int(
                round(max(perf_counter() - started, 0.0) * 1000)
            ),
        )


class QuestionAnalysisInputLoader:
    """Load controlled rich text and actual image bodies for the module."""

    def __init__(self, *, db_path: Path, data_root: Path) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root)

    def load(
        self,
        question_ids: Sequence[int],
        *,
        taxonomy_contracts: Mapping[int, Mapping[str, Any]] | None = None,
    ) -> tuple[QuestionAnalysisInput, ...]:
        ids = tuple(dict.fromkeys(int(value) for value in question_ids))
        if not ids or any(value <= 0 for value in ids):
            raise ValueError("question_ids must contain positive integers")
        placeholders = ",".join("?" for _ in ids)
        with connect(self.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT q.id, q.question_text, q.answer_text,
                       q.question_number, q.question_type,
                       q.image_paths, q.has_images, q.is_deleted,
                       p.grade, p.semester, p.textbook_version,
                       p.exam_type, p.district
                FROM questions q
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE q.id IN ({placeholders})
                """,
                ids,
            ).fetchall()
        by_id = {int(row["id"]): row for row in rows}
        result: list[QuestionAnalysisInput] = []
        for question_id in ids:
            row = by_id.get(question_id)
            if row is None or bool(row["is_deleted"]):
                raise KeyError(question_id)
            rich = load_question_rich_content(
                question_id,
                root=self.data_root / "question_bank" / "rich_content",
            ) or {}
            question_blocks = _safe_blocks(rich.get("question_blocks"))
            answer_blocks = _safe_blocks(rich.get("answer_blocks"))
            question_paths = _dedupe_paths(
                [
                    *_stored_paths(row["image_paths"]),
                    *_marker_paths(row["question_text"]),
                    *_rich_paths(question_blocks),
                ]
            )
            answer_paths = _dedupe_paths(
                [
                    *_marker_paths(row["answer_text"]),
                    *_rich_paths(answer_blocks),
                ]
            )
            images = tuple(
                [
                    *self._images(question_paths, role="question"),
                    *self._images(answer_paths, role="answer"),
                ]
            )
            has_images = bool(row["has_images"]) or bool(
                question_paths or answer_paths
            )
            result.append(
                QuestionAnalysisInput(
                    question_id=question_id,
                    tagging_context=TaggingContext(
                        question_text=str(row["question_text"] or ""),
                        answer_text=str(row["answer_text"] or ""),
                        question_number=str(
                            row["question_number"] or ""
                        ),
                        question_type=str(row["question_type"] or ""),
                        grade=str(row["grade"] or ""),
                        semester=str(row["semester"] or ""),
                        textbook_version=str(
                            row["textbook_version"] or ""
                        ),
                        exam_type=str(row["exam_type"] or ""),
                        district=str(row["district"] or ""),
                        has_images=has_images,
                    ),
                    rich_question_blocks=tuple(
                        _public_block(item) for item in question_blocks
                    ),
                    rich_answer_blocks=tuple(
                        _public_block(item) for item in answer_blocks
                    ),
                    images=images,
                    taxonomy_contract=dict(
                        (taxonomy_contracts or {}).get(question_id, {})
                    ),
                )
            )
        return tuple(result)

    def _images(
        self,
        paths: Sequence[str],
        *,
        role: str,
    ) -> list[QuestionAnalysisImage]:
        result: list[QuestionAnalysisImage] = []
        for saved_path in paths:
            try:
                resolved = resolve_question_bank_asset_path(
                    saved_path,
                    data_root=self.data_root,
                    search_subdirs=(
                        "question_bank/extracted_images",
                        "question_bank/previews",
                    ),
                )
            except (OSError, ValueError):
                continue
            if not resolved.is_file():
                continue
            mime = (
                mimetypes.guess_type(resolved.name)[0]
                or "application/octet-stream"
            )
            try:
                result.append(
                    QuestionAnalysisImage(
                        role=role,  # type: ignore[arg-type]
                        mime_type=mime,
                        content=resolved.read_bytes(),
                    )
                )
            except (OSError, ValueError):
                continue
        return result


def _combined_prompt(
    batch: PlannedAnalysisBatch,
    projection: AnalysisProjection,
) -> list[dict[str, Any]]:
    instructions = (
        "Analyze only the listed junior-middle-school math questions. "
        "Keep each question_id isolated. Tag candidates may only come from "
        "that question's candidate_contract. Training criteria are observable "
        "answer/process obligations and must never contain score fields. "
        "Do not invent content hidden by a missing image."
    )
    questions = []
    for item in batch.questions:
        context = item.tagging_context.to_dict()
        context.pop("existing_tags", None)
        context.pop("existing_tags_by_dimension", None)
        questions.append(
            {
                "question_id": item.question_id,
                "question": context,
                "rich_question_blocks": list(
                    item.rich_question_blocks
                ),
                "rich_answer_blocks": list(
                    item.rich_answer_blocks
                ),
                "candidate_contract": dict(item.taxonomy_contract),
                "expected_projection": projection,
            }
        )
    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": json.dumps(
                {
                    "task": "combined-v2 question analysis",
                    "rules": instructions,
                    "questions": questions,
                },
                ensure_ascii=False,
            ),
        }
    ]
    for item in batch.questions:
        for image in item.images:
            content.extend(
                [
                    {
                        "type": "input_text",
                        "text": (
                            f"question_id={item.question_id};"
                            f"image_role={image.role};sha256={image.sha256}"
                        ),
                    },
                    {
                        "type": "input_image",
                        "image_url": image.data_url(),
                    },
                ]
            )
    return [
        {
            "role": "system",
            "content": [
                {
                    "type": "input_text",
                    "text": (
                        "Return strict JSON only. Never return scores, "
                        "ranks, grades, or point values."
                    ),
                }
            ],
        },
        {"role": "user", "content": content},
    ]


def _stored_paths(value: object) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _marker_paths(value: object) -> list[str]:
    return [
        match.group("path").strip()
        for match in _IMAGE_MARKER.finditer(str(value or ""))
        if match.group("path").strip()
    ]


def _safe_blocks(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        dict(item)
        for item in value
        if isinstance(item, Mapping)
        and isinstance(item.get("text"), str)
    ]


def _rich_paths(blocks: Sequence[Mapping[str, Any]]) -> list[str]:
    paths: list[str] = []
    for block in blocks:
        paths.extend(_marker_paths(block.get("text")))
        relationships = block.get("image_relationships")
        if isinstance(relationships, Mapping):
            paths.extend(
                str(item).strip()
                for item in relationships.values()
                if str(item).strip()
            )
    return paths


def _dedupe_paths(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _public_block(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "text": _IMAGE_MARKER.sub("", str(value.get("text") or "")),
    }


__all__ = [
    "ExistingTagProjectionWriter",
    "OpenAICombinedAnalysisGateway",
    "QuestionAnalysisInputLoader",
]
