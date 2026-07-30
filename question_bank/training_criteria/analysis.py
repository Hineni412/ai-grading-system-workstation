from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping, Protocol, Sequence

from question_bank.models.tag_schema import TagAnalysis, TaggingContext


AnalysisProjection = Literal["both", "tag", "training_criteria"]
ProjectionStatus = Literal[
    "pending",
    "succeeded",
    "failed",
    "cancelled",
    "not_requested",
]
_PROJECTIONS = ("tag", "training_criteria")
_POINT_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_BANNED_SCORE_KEYS = frozenset(
    {
        "score",
        "max_score",
        "min_score",
        "step_score",
        "total_score",
        "points_awarded",
        "score_awarded",
        "full_score",
    }
)


class AnalysisConflictError(RuntimeError):
    pass


class ProjectionValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class QuestionAnalysisImage:
    role: Literal["question", "answer"]
    mime_type: str
    content: bytes
    sha256: str = field(init=False)

    def __post_init__(self) -> None:
        payload = bytes(self.content)
        mime = str(self.mime_type or "").strip().casefold()
        if self.role not in {"question", "answer"}:
            raise ValueError("image role is invalid")
        if mime not in {
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/gif",
        }:
            raise ValueError("image mime type is unsupported")
        if not payload:
            raise ValueError("image content must not be empty")
        if len(payload) > 20 * 1024 * 1024:
            raise ValueError("image content exceeds the per-image limit")
        object.__setattr__(self, "content", payload)
        object.__setattr__(self, "mime_type", mime)
        object.__setattr__(
            self,
            "sha256",
            hashlib.sha256(payload).hexdigest(),
        )

    def data_url(self) -> str:
        encoded = base64.b64encode(self.content).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"


@dataclass(frozen=True, slots=True)
class QuestionAnalysisInput:
    question_id: int
    tagging_context: TaggingContext
    rich_question_blocks: tuple[Mapping[str, Any], ...] = ()
    rich_answer_blocks: tuple[Mapping[str, Any], ...] = ()
    images: tuple[QuestionAnalysisImage, ...] = ()
    taxonomy_contract: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.question_id, bool) or int(self.question_id) <= 0:
            raise ValueError("question_id must be positive")
        if not str(self.tagging_context.question_text or "").strip():
            raise ValueError("question text must not be empty")
        object.__setattr__(self, "question_id", int(self.question_id))
        object.__setattr__(
            self,
            "rich_question_blocks",
            tuple(dict(item) for item in self.rich_question_blocks),
        )
        object.__setattr__(
            self,
            "rich_answer_blocks",
            tuple(dict(item) for item in self.rich_answer_blocks),
        )
        object.__setattr__(self, "images", tuple(self.images))
        object.__setattr__(
            self,
            "taxonomy_contract",
            dict(self.taxonomy_contract),
        )

    @property
    def has_required_images(self) -> bool:
        if not self.tagging_context.has_images:
            return True
        return any(image.role == "question" for image in self.images)

    @property
    def source_content_hash(self) -> str:
        return _hash_payload(
            {
                "question_id": self.question_id,
                "tagging_context": self.tagging_context.to_dict(),
                "rich_question_blocks": self.rich_question_blocks,
                "rich_answer_blocks": self.rich_answer_blocks,
                "image_hashes": [
                    {
                        "role": image.role,
                        "mime_type": image.mime_type,
                        "sha256": image.sha256,
                    }
                    for image in self.images
                ],
                "taxonomy_contract": self.taxonomy_contract,
            }
        )

    @property
    def question_type_group(self) -> str:
        value = str(self.tagging_context.question_type or "").casefold()
        text = str(self.tagging_context.question_text or "")
        if any(token in value for token in ("证明", "proof")):
            return "proof"
        if any(token in value for token in ("作图", "画图", "construction")):
            return "construction"
        if any(token in value for token in ("选择", "choice")):
            return "single_choice"
        if any(token in value for token in ("填空", "fill")):
            return "fill_blank"
        if any(token in value for token in ("计算", "解答", "calculation")):
            return "calculation"
        if "证明" in text:
            return "proof"
        if "作图" in text:
            return "construction"
        return "calculation"


@dataclass(frozen=True, slots=True)
class TrainingCriterionPoint:
    point_id: str
    target: str
    observable_evidence: str
    equivalent_rules: tuple[str, ...] = ()
    counterexamples: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TrainingCriterionPoint":
        _reject_score_fields(payload)
        point_id = str(payload.get("point_id") or "").strip().casefold()
        target = str(payload.get("target") or "").strip()
        evidence = str(payload.get("observable_evidence") or "").strip()
        if not _POINT_ID.fullmatch(point_id):
            raise ProjectionValidationError("point_id is invalid")
        if not target or not evidence:
            raise ProjectionValidationError(
                "criterion target and observable evidence are required"
            )
        return cls(
            point_id=point_id,
            target=target,
            observable_evidence=evidence,
            equivalent_rules=_text_tuple(payload.get("equivalent_rules")),
            counterexamples=_text_tuple(payload.get("counterexamples")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "point_id": self.point_id,
            "target": self.target,
            "observable_evidence": self.observable_evidence,
            "equivalent_rules": list(self.equivalent_rules),
            "counterexamples": list(self.counterexamples),
        }


@dataclass(frozen=True, slots=True)
class TrainingCriteriaDraft:
    schema_version: Literal["training-criteria-draft-v1"]
    question_id: int
    source_content_hash: str
    question_type: str
    points: tuple[TrainingCriterionPoint, ...]
    auxiliary_rules: tuple[str, ...]
    rationale: str
    confidence: float
    source_kind: Literal["combined_model", "confirmed_rubric_adapter"]

    @classmethod
    def from_model_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        question: QuestionAnalysisInput,
    ) -> "TrainingCriteriaDraft":
        _reject_score_fields(payload)
        if payload.get("schema_version") != "training-criteria-draft-v1":
            raise ProjectionValidationError(
                "training criteria schema version is invalid"
            )
        raw_points = payload.get("points")
        if not isinstance(raw_points, list) or not raw_points:
            raise ProjectionValidationError(
                "training criteria must contain at least one point"
            )
        points = tuple(
            TrainingCriterionPoint.from_dict(item)
            for item in raw_points
            if isinstance(item, Mapping)
        )
        if len(points) != len(raw_points):
            raise ProjectionValidationError("criterion point is invalid")
        if len({point.point_id for point in points}) != len(points):
            raise ProjectionValidationError("criterion point_id is duplicated")
        raw_question_id = payload.get("question_id", question.question_id)
        if int(raw_question_id) != question.question_id:
            raise ProjectionValidationError(
                "training criteria question_id does not match"
            )
        confidence = float(payload.get("confidence", 0.0))
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ProjectionValidationError(
                "training criteria confidence is invalid"
            )
        return cls(
            schema_version="training-criteria-draft-v1",
            question_id=question.question_id,
            source_content_hash=question.source_content_hash,
            question_type=question.question_type_group,
            points=points,
            auxiliary_rules=_text_tuple(payload.get("auxiliary_rules")),
            rationale=str(payload.get("rationale") or "").strip(),
            confidence=confidence,
            source_kind="combined_model",
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["points"] = [point.to_dict() for point in self.points]
        payload["auxiliary_rules"] = list(self.auxiliary_rules)
        return payload


@dataclass(frozen=True, slots=True)
class GatewayUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __post_init__(self) -> None:
        for field_name in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
        ):
            value = int(getattr(self, field_name))
            if value < 0:
                raise ValueError("usage values must be nonnegative")
            object.__setattr__(self, field_name, value)


@dataclass(frozen=True, slots=True)
class GatewayBatchResponse:
    payload: Mapping[str, Any]
    model_name: str
    usage: GatewayUsage = GatewayUsage()
    latency_ms: int = 0


@dataclass(frozen=True, slots=True)
class PlannedAnalysisBatch:
    questions: tuple[QuestionAnalysisInput, ...]
    estimated_input_tokens: int
    estimated_output_tokens: int

    @property
    def question_ids(self) -> tuple[int, ...]:
        return tuple(item.question_id for item in self.questions)

    @property
    def batch_hash(self) -> str:
        return _hash_payload(
            {
                "question_ids": self.question_ids,
                "source_hashes": [
                    item.source_content_hash for item in self.questions
                ],
                "input": self.estimated_input_tokens,
                "output": self.estimated_output_tokens,
            }
        )


class QuestionAnalysisGateway(Protocol):
    def analyze(
        self,
        batch: PlannedAnalysisBatch,
        *,
        projection: AnalysisProjection,
        operation_id: str,
        request_id: str,
    ) -> GatewayBatchResponse:
        ...


class TagProjectionWriter(Protocol):
    def write(
        self,
        question: QuestionAnalysisInput,
        payload: Mapping[str, Any],
        *,
        model_name: str,
        operation_id: str,
    ) -> Mapping[str, Any]:
        ...


class AnalysisProjectionRepository(Protocol):
    def begin_operation(
        self,
        *,
        operation_id: str,
        fingerprint: str,
        questions: Sequence[QuestionAnalysisInput],
        requested_projection: AnalysisProjection,
    ) -> bool:
        ...

    def projection_status(
        self,
        operation_id: str,
        question_id: int,
        projection: str,
    ) -> ProjectionStatus:
        ...

    def record_request_started(
        self,
        *,
        operation_id: str,
        request_id: str,
        projection: AnalysisProjection,
        batch: PlannedAnalysisBatch,
    ) -> None:
        ...

    def record_request_finished(
        self,
        *,
        request_id: str,
        status: Literal["succeeded", "failed"],
        response: GatewayBatchResponse | None = None,
        error_category: str = "",
    ) -> None:
        ...

    def save_projection(
        self,
        *,
        operation_id: str,
        question_id: int,
        projection: Literal["tag", "training_criteria"],
        status: Literal["succeeded", "failed", "cancelled"],
        payload: Mapping[str, Any] | None = None,
        error_category: str = "",
    ) -> None:
        ...

    def recover_interrupted(self, operation_id: str) -> None:
        ...

    def operation_summary(self, operation_id: str) -> Mapping[str, Any]:
        ...


class CombinedQuestionAnalysisModule:
    """Deep module for one request, two independently persisted projections."""

    def __init__(
        self,
        *,
        repository: AnalysisProjectionRepository,
        gateway: QuestionAnalysisGateway,
        tag_writer: TagProjectionWriter,
    ) -> None:
        self.repository = repository
        self.gateway = gateway
        self.tag_writer = tag_writer

    def analyze(
        self,
        *,
        operation_id: str,
        questions: Sequence[QuestionAnalysisInput],
        projection: AnalysisProjection = "both",
    ) -> Mapping[str, Any]:
        clean_operation = _required_text(operation_id, "operation_id")
        normalized = _normalize_questions(questions)
        normalized_projection = _projection(projection)
        fingerprint = _operation_fingerprint(
            normalized,
            normalized_projection,
        )
        created = self.repository.begin_operation(
            operation_id=clean_operation,
            fingerprint=fingerprint,
            questions=normalized,
            requested_projection=normalized_projection,
        )
        if not created:
            return self.repository.operation_summary(clean_operation)
        self._execute(
            operation_id=clean_operation,
            questions=normalized,
            projection=normalized_projection,
            retry=False,
        )
        return self.repository.operation_summary(clean_operation)

    def retry_failed_projection(
        self,
        *,
        operation_id: str,
        questions: Sequence[QuestionAnalysisInput],
        projection: Literal["tag", "training_criteria"],
    ) -> Mapping[str, Any]:
        clean_operation = _required_text(operation_id, "operation_id")
        normalized = _normalize_questions(questions)
        selected = _projection(projection)
        summary = self.repository.operation_summary(clean_operation)
        expected = str(summary.get("input_fingerprint") or "")
        actual = _operation_fingerprint(
            normalized,
            str(summary.get("requested_projection") or "both"),
        )
        if expected != actual:
            raise AnalysisConflictError(
                "retry input does not match the original operation"
            )
        self._execute(
            operation_id=clean_operation,
            questions=normalized,
            projection=selected,
            retry=True,
        )
        return self.repository.operation_summary(clean_operation)

    def resume_interrupted(
        self,
        *,
        operation_id: str,
        questions: Sequence[QuestionAnalysisInput],
    ) -> Mapping[str, Any]:
        clean_operation = _required_text(operation_id, "operation_id")
        normalized = _normalize_questions(questions)
        summary = self.repository.operation_summary(clean_operation)
        requested = _projection(
            str(summary.get("requested_projection") or "both")
        )
        expected = str(summary.get("input_fingerprint") or "")
        actual = _operation_fingerprint(normalized, requested)
        if expected != actual:
            raise AnalysisConflictError(
                "resume input does not match the original operation"
            )
        self.repository.recover_interrupted(clean_operation)
        for projection in _selected_projections(requested):
            self._execute(
                operation_id=clean_operation,
                questions=normalized,
                projection=projection,
                retry=True,
            )
        return self.repository.operation_summary(clean_operation)

    def _execute(
        self,
        *,
        operation_id: str,
        questions: tuple[QuestionAnalysisInput, ...],
        projection: AnalysisProjection,
        retry: bool,
    ) -> None:
        selected = _selected_projections(projection)
        ready: list[QuestionAnalysisInput] = []
        for question in questions:
            statuses = {
                item: self.repository.projection_status(
                    operation_id,
                    question.question_id,
                    item,
                )
                for item in selected
            }
            if retry:
                if not any(status in {"failed", "cancelled"} for status in statuses.values()):
                    continue
            elif any(status == "succeeded" for status in statuses.values()):
                continue
            if not question.has_required_images:
                for item, status in statuses.items():
                    if retry and status not in {"failed", "cancelled"}:
                        continue
                    self.repository.save_projection(
                        operation_id=operation_id,
                        question_id=question.question_id,
                        projection=item,
                        status="failed",
                        error_category="missing_image",
                    )
                continue
            ready.append(question)

        batches = plan_analysis_batches(
            tuple(ready),
            projection=projection,
        )
        for batch_index, batch in enumerate(batches):
            request_id = _hash_payload(
                {
                    "operation_id": operation_id,
                    "projection": projection,
                    "batch_hash": batch.batch_hash,
                    "request_number": int(
                        self.repository.operation_summary(operation_id).get(
                            "request_count",
                            0,
                        )
                    )
                    + 1,
                }
            )
            self.repository.record_request_started(
                operation_id=operation_id,
                request_id=request_id,
                projection=projection,
                batch=batch,
            )
            try:
                response = self.gateway.analyze(
                    batch,
                    projection=projection,
                    operation_id=operation_id,
                    request_id=request_id,
                )
                items = _response_items(response.payload, batch)
            except Exception as exc:
                category = _error_category(exc)
                self.repository.record_request_finished(
                    request_id=request_id,
                    status="failed",
                    error_category=category,
                )
                self._fail_batch(
                    operation_id=operation_id,
                    batch=batch,
                    projection=projection,
                    retry=retry,
                    category=category,
                )
                if category == "cancelled":
                    for remaining in batches[batch_index + 1 :]:
                        self._fail_batch(
                            operation_id=operation_id,
                            batch=remaining,
                            projection=projection,
                            retry=retry,
                            category=category,
                        )
                    break
                continue

            self.repository.record_request_finished(
                request_id=request_id,
                status="succeeded",
                response=response,
            )
            for question in batch.questions:
                raw = items.get(question.question_id)
                if raw is None:
                    self._fail_question(
                        operation_id=operation_id,
                        question=question,
                        projection=projection,
                        retry=retry,
                        category="missing_result",
                    )
                    continue
                if "tag" in selected:
                    status = self.repository.projection_status(
                        operation_id,
                        question.question_id,
                        "tag",
                    )
                    if not retry or status in {"failed", "cancelled", "pending"}:
                        self._save_tag(
                            operation_id,
                            question,
                            raw,
                            response.model_name,
                        )
                if "training_criteria" in selected:
                    status = self.repository.projection_status(
                        operation_id,
                        question.question_id,
                        "training_criteria",
                    )
                    if not retry or status in {"failed", "cancelled", "pending"}:
                        self._save_criteria(operation_id, question, raw)

    def _save_tag(
        self,
        operation_id: str,
        question: QuestionAnalysisInput,
        raw: Mapping[str, Any],
        model_name: str,
    ) -> None:
        payload = raw.get("tag_analysis")
        if not isinstance(payload, Mapping):
            self.repository.save_projection(
                operation_id=operation_id,
                question_id=question.question_id,
                projection="tag",
                status="failed",
                error_category="tag_validation",
            )
            return
        try:
            normalized = self.tag_writer.write(
                question,
                payload,
                model_name=model_name,
                operation_id=operation_id,
            )
        except Exception:
            self.repository.save_projection(
                operation_id=operation_id,
                question_id=question.question_id,
                projection="tag",
                status="failed",
                error_category="tag_validation",
            )
            return
        self.repository.save_projection(
            operation_id=operation_id,
            question_id=question.question_id,
            projection="tag",
            status="succeeded",
            payload=normalized,
        )

    def _save_criteria(
        self,
        operation_id: str,
        question: QuestionAnalysisInput,
        raw: Mapping[str, Any],
    ) -> None:
        payload = raw.get("training_criteria")
        if not isinstance(payload, Mapping):
            self.repository.save_projection(
                operation_id=operation_id,
                question_id=question.question_id,
                projection="training_criteria",
                status="failed",
                error_category="criteria_validation",
            )
            return
        try:
            draft = TrainingCriteriaDraft.from_model_dict(
                payload,
                question=question,
            )
        except (TypeError, ValueError):
            self.repository.save_projection(
                operation_id=operation_id,
                question_id=question.question_id,
                projection="training_criteria",
                status="failed",
                error_category="criteria_validation",
            )
            return
        self.repository.save_projection(
            operation_id=operation_id,
            question_id=question.question_id,
            projection="training_criteria",
            status="succeeded",
            payload=draft.to_dict(),
        )

    def _fail_batch(
        self,
        *,
        operation_id: str,
        batch: PlannedAnalysisBatch,
        projection: AnalysisProjection,
        retry: bool,
        category: str,
    ) -> None:
        for question in batch.questions:
            self._fail_question(
                operation_id=operation_id,
                question=question,
                projection=projection,
                retry=retry,
                category=category,
            )

    def _fail_question(
        self,
        *,
        operation_id: str,
        question: QuestionAnalysisInput,
        projection: AnalysisProjection,
        retry: bool,
        category: str,
    ) -> None:
        for item in _selected_projections(projection):
            status = self.repository.projection_status(
                operation_id,
                question.question_id,
                item,
            )
            if retry and status not in {"failed", "cancelled"}:
                continue
            self.repository.save_projection(
                operation_id=operation_id,
                question_id=question.question_id,
                projection=item,
                status=(
                    "cancelled"
                    if category == "cancelled"
                    else "failed"
                ),
                error_category=category,
            )


def plan_analysis_batches(
    questions: Sequence[QuestionAnalysisInput],
    *,
    projection: AnalysisProjection = "both",
    max_input_tokens: int = 12_000,
    max_output_tokens: int = 5_000,
) -> tuple[PlannedAnalysisBatch, ...]:
    selected = _projection(projection)
    batches: list[PlannedAnalysisBatch] = []
    current: list[QuestionAnalysisInput] = []
    current_input = 0
    current_output = 0

    def flush() -> None:
        nonlocal current, current_input, current_output
        if current:
            batches.append(
                PlannedAnalysisBatch(
                    questions=tuple(current),
                    estimated_input_tokens=current_input,
                    estimated_output_tokens=current_output,
                )
            )
        current = []
        current_input = 0
        current_output = 0

    for question in questions:
        estimate_in, estimate_out = _token_estimate(question, selected)
        group_limit = _question_batch_limit(question)
        force_single = group_limit == 1
        if (
            current
            and (
                force_single
                or len(current) >= group_limit
                or current_input + estimate_in > max_input_tokens
                or current_output + estimate_out > max_output_tokens
                or _question_batch_limit(current[0]) != group_limit
            )
        ):
            flush()
        current.append(question)
        current_input += estimate_in
        current_output += estimate_out
        if force_single or len(current) >= group_limit:
            flush()
    flush()
    return tuple(batches)


def criteria_from_confirmed_rubric(
    *,
    question: QuestionAnalysisInput,
    rubric_question: Mapping[str, Any],
    answer_key: Mapping[str, Any] | None = None,
) -> TrainingCriteriaDraft:
    answer = dict(answer_key or {})
    candidates: list[Mapping[str, Any]] = []
    parts = rubric_question.get("parts")
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, Mapping):
                continue
            steps = part.get("steps")
            if isinstance(steps, list):
                candidates.extend(
                    item for item in steps if isinstance(item, Mapping)
                )
    if not candidates:
        steps = rubric_question.get("steps")
        if isinstance(steps, list):
            candidates.extend(
                item for item in steps if isinstance(item, Mapping)
            )
    points: list[TrainingCriterionPoint] = []
    for index, step in enumerate(candidates, start=1):
        target = _first_text(
            step,
            "core_goal",
            "goal",
            "criterion",
            "description",
            "title",
        )
        required = _text_tuple(step.get("required_elements"))
        if not target and required:
            target = required[0]
        if not target:
            continue
        point_id = _safe_point_id(
            str(step.get("step_id") or f"step-{index}")
        )
        points.append(
            TrainingCriterionPoint(
                point_id=point_id,
                target=target,
                observable_evidence=(
                    "；".join(required) if required else target
                ),
                equivalent_rules=_text_tuple(
                    step.get("alternative_methods")
                    or step.get("equivalent_answers")
                ),
                counterexamples=(),
            )
        )
    if not points:
        canonical = _first_text(
            answer,
            "canonical_answer",
            "answer",
            "correct_answer",
        ) or str(question.tagging_context.answer_text or "").strip()
        if not canonical:
            raise ProjectionValidationError(
                "confirmed rubric cannot be converted without an answer"
            )
        points.append(
            TrainingCriterionPoint(
                point_id="p-answer",
                target="给出正确或等价答案",
                observable_evidence=canonical,
                equivalent_rules=_text_tuple(
                    answer.get("accepted_forms")
                    or answer.get("equivalent_answers")
                ),
                counterexamples=(),
            )
        )
    if len({point.point_id for point in points}) != len(points):
        points = [
            TrainingCriterionPoint(
                point_id=f"{point.point_id}-{index}",
                target=point.target,
                observable_evidence=point.observable_evidence,
                equivalent_rules=point.equivalent_rules,
                counterexamples=point.counterexamples,
            )
            for index, point in enumerate(points, start=1)
        ]
    return TrainingCriteriaDraft(
        schema_version="training-criteria-draft-v1",
        question_id=question.question_id,
        source_content_hash=question.source_content_hash,
        question_type=question.question_type_group,
        points=tuple(points),
        auxiliary_rules=(),
        rationale="由教师已确认的正式评分依据本地去分值转换。",
        confidence=1.0,
        source_kind="confirmed_rubric_adapter",
    )


class TagOnlyV1ResultAdapter:
    """Compatibility Adapter that exposes an old tag result at the new seam."""

    @staticmethod
    def adapt(
        question_id: int,
        result: object,
    ) -> Mapping[str, Any]:
        analysis = getattr(result, "analysis", None)
        if not bool(getattr(result, "ok", False)) or not isinstance(
            analysis,
            TagAnalysis,
        ):
            raise ProjectionValidationError("tag-only-v1 result is invalid")
        return {
            "question_id": int(question_id),
            "tag_analysis": analysis.to_dict(),
        }


def combined_response_format(
    projection: AnalysisProjection = "both",
) -> dict[str, Any]:
    selected = _selected_projections(_projection(projection))
    item_properties: dict[str, Any] = {
        "question_id": {"type": "integer"},
    }
    if "tag" in selected:
        item_properties["tag_analysis"] = _tag_schema()
    if "training_criteria" in selected:
        item_properties["training_criteria"] = _criteria_schema()
    return {
        "type": "json_schema",
        "name": (
            "question_bank_combined_analysis_v2"
            if projection == "both"
            else f"question_bank_{projection}_projection_v2"
        ),
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": item_properties,
                        "required": list(item_properties),
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["results"],
            "additionalProperties": False,
        },
    }


def _tag_schema() -> dict[str, Any]:
    array = {"type": "array", "items": {"type": "string"}}
    text = {"type": "string"}
    properties = {
        "knowledge_points": array,
        "method_tags": array,
        "ability_tags": array,
        "math_model_tags": array,
        "special_type_tags": array,
        "difficulty": {"type": "integer", "minimum": 1, "maximum": 10},
        "error_prone_points": array,
        "prerequisite_points": array,
        "textbook_chapters": array,
        "curriculum_sections": array,
        "suitable_student_level": text,
        "canonical_knowledge_id": text,
        "taxonomy_revision": {"type": "integer"},
        "proposed_tags": _proposal_schema(),
        "reason": text,
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _proposal_schema() -> dict[str, Any]:
    text = {"type": "string"}
    properties = {
        "dimension": text,
        "name": text,
        "definition": text,
        "reason": text,
        "nearest_id": text,
        "why_not_reuse": text,
    }
    return {
        "type": "array",
        "maxItems": 2,
        "items": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


def _criteria_schema() -> dict[str, Any]:
    text_array = {"type": "array", "items": {"type": "string"}}
    point = {
        "type": "object",
        "properties": {
            "point_id": {"type": "string"},
            "target": {"type": "string"},
            "observable_evidence": {"type": "string"},
            "equivalent_rules": text_array,
            "counterexamples": text_array,
        },
        "required": [
            "point_id",
            "target",
            "observable_evidence",
            "equivalent_rules",
            "counterexamples",
        ],
        "additionalProperties": False,
    }
    properties = {
        "schema_version": {
            "type": "string",
            "enum": ["training-criteria-draft-v1"],
        },
        "question_id": {"type": "integer"},
        "points": {"type": "array", "minItems": 1, "items": point},
        "auxiliary_rules": text_array,
        "rationale": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _response_items(
    payload: Mapping[str, Any],
    batch: PlannedAnalysisBatch,
) -> dict[int, Mapping[str, Any]]:
    raw = payload.get("results")
    if not isinstance(raw, list):
        raise ProjectionValidationError("combined response has no results")
    expected = set(batch.question_ids)
    items: dict[int, Mapping[str, Any]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise ProjectionValidationError("combined response item is invalid")
        try:
            question_id = int(item["question_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProjectionValidationError(
                "combined response question_id is invalid"
            ) from exc
        if question_id not in expected or question_id in items:
            raise ProjectionValidationError(
                "combined response has unknown or duplicate question_id"
            )
        items[question_id] = item
    return items


def _normalize_questions(
    questions: Sequence[QuestionAnalysisInput],
) -> tuple[QuestionAnalysisInput, ...]:
    normalized = tuple(questions)
    if not normalized:
        raise ValueError("questions must not be empty")
    ids = [item.question_id for item in normalized]
    if len(set(ids)) != len(ids):
        raise ValueError("question_ids must be unique")
    return tuple(sorted(normalized, key=lambda item: item.question_id))


def _operation_fingerprint(
    questions: Sequence[QuestionAnalysisInput],
    projection: str,
) -> str:
    return _hash_payload(
        {
            "contract": "combined-v2",
            "requested_projection": projection,
            "questions": [
                {
                    "question_id": item.question_id,
                    "source_content_hash": item.source_content_hash,
                }
                for item in questions
            ],
        }
    )


def _selected_projections(
    projection: AnalysisProjection,
) -> tuple[Literal["tag", "training_criteria"], ...]:
    if projection == "both":
        return _PROJECTIONS
    return (projection,)


def _projection(value: object) -> AnalysisProjection:
    normalized = str(value or "").strip()
    if normalized not in {"both", "tag", "training_criteria"}:
        raise ValueError("analysis projection is invalid")
    return normalized  # type: ignore[return-value]


def _token_estimate(
    question: QuestionAnalysisInput,
    projection: AnalysisProjection,
) -> tuple[int, int]:
    context = question.tagging_context
    text_chars = len(
        json.dumps(
            {
                "context": context.to_dict(),
                "question_blocks": question.rich_question_blocks,
                "answer_blocks": question.rich_answer_blocks,
                "taxonomy_contract": question.taxonomy_contract,
            },
            ensure_ascii=False,
            default=str,
        )
    )
    image_tokens = sum(
        max(900, math.ceil(len(image.content) / 1024) * 32)
        for image in question.images
    )
    input_tokens = math.ceil(text_chars / 3) + image_tokens + 250
    output_tokens = 0
    if projection in {"both", "tag"}:
        output_tokens += 650
    if projection in {"both", "training_criteria"}:
        output_tokens += (
            500
            if question.question_type_group in {
                "single_choice",
                "fill_blank",
            }
            else 1_250
        )
    return input_tokens, output_tokens


def _question_batch_limit(question: QuestionAnalysisInput) -> int:
    text = str(question.tagging_context.question_text or "")
    if (
        question.images
        or question.question_type_group in {"proof", "construction"}
        or len(text) >= 480
        or any(
            token in text
            for token in ("综合与实践", "【探究】", "【模型", "【定义】")
        )
    ):
        return 1
    if question.question_type_group in {"single_choice", "fill_blank"}:
        return 5
    return 3


def _reject_score_fields(value: object, *, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key).strip().casefold()
            if key in _BANNED_SCORE_KEYS:
                raise ProjectionValidationError(
                    f"score field is forbidden at {path}.{key}"
                )
            _reject_score_fields(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_score_fields(child, path=f"{path}[{index}]")


def _text_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return tuple(result)


def _first_text(value: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        text = str(value.get(key) or "").strip()
        if text:
            return text
    return ""


def _safe_point_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.casefold()).strip("-_")
    if not normalized or not normalized[0].isalpha():
        normalized = f"p-{normalized or 'point'}"
    return normalized[:64]


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _error_category(exc: BaseException) -> str:
    text = f"{type(exc).__name__} {exc}".casefold()
    if "timeout" in text:
        return "timeout"
    if "json" in text or "parse" in text:
        return "parse"
    if "validation" in text or "schema" in text or "result" in text:
        return "validation"
    if "cancel" in text:
        return "cancelled"
    return "model"


def _hash_payload(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "AnalysisConflictError",
    "AnalysisProjection",
    "CombinedQuestionAnalysisModule",
    "GatewayBatchResponse",
    "GatewayUsage",
    "PlannedAnalysisBatch",
    "ProjectionStatus",
    "ProjectionValidationError",
    "QuestionAnalysisGateway",
    "QuestionAnalysisImage",
    "QuestionAnalysisInput",
    "TagOnlyV1ResultAdapter",
    "TagProjectionWriter",
    "TrainingCriteriaDraft",
    "TrainingCriterionPoint",
    "combined_response_format",
    "criteria_from_confirmed_rubric",
    "plan_analysis_batches",
]
