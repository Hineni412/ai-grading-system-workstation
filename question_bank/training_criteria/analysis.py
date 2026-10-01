from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import re
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from typing import Any, Literal, Protocol

from question_bank.models.tag_schema import (
    MAX_ABILITY_TAGS,
    PART_CONTEXT_KINDS,
    PART_FEATURE_ORDER,
    PART_FEATURE_RANGES,
    PART_FEATURES_MAX_PARTS,
    PREDICTED_PATTERN_MAX,
    PREDICTED_TRIGGER_KINDS,
    TagAnalysis,
    TaggingContext,
    predicted_pattern_categories,
)
from question_bank.parsers.type_detector import (
    ESSAY_SUBTYPES,
    QUESTION_TYPES,
    subq_mark_labels,
)
from question_bank.solution_evidence.contracts import QuestionSolutionEvidence
from question_bank.taxonomy.snapshot import QuestionTaxonomySnapshot

AnalysisProjection = Literal["both", "tag", "training_criteria"]
CriterionSchemaVersion = Literal[
    "training-criteria-draft-v1",
    "judgment-points-v1",
]
JUDGMENT_POINTS_SCHEMA: CriterionSchemaVersion = "judgment-points-v1"
LEGACY_CRITERIA_SCHEMA: CriterionSchemaVersion = "training-criteria-draft-v1"
ObjectiveResponseShape = Literal[
    "single_choice",
    "single_blank",
    "multiple_blank",
    "unknown",
]
AnalysisProgressCallback = Callable[[Mapping[str, Any]], None]
ProjectionStatus = Literal[
    "pending",
    "succeeded",
    "failed",
    "cancelled",
    "not_requested",
]
_PROJECTIONS = ("tag", "training_criteria")
_POINT_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
# 题型封闭枚举到本地题组的映射，与 QuestionAnalysisInput.question_type_group
# 的判词规则保持一致（多选题按单选客观形态处理）。
_QUESTION_TYPE_GROUPS = {
    "选择题": "single_choice",
    "多选题": "single_choice",
    "填空题": "fill_blank",
    "解答题": "calculation",
}
# 解答题子类标签值到本地题组的映射（证明/画图保持单题成批分析）。
_ESSAY_SUBTYPE_GROUPS = {
    "画图": "construction",
    "计算": "calculation",
    "证明": "proof",
}
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


class GatewayResponseParseError(ValueError):
    """A physical model response arrived but its payload was unusable."""

    def __init__(self, message: str, *, raw_text: str = "") -> None:
        super().__init__(message)
        # Raw model output kept for repair-feedback retries; the caller must
        # treat it as untrusted model text and never as local instructions.
        self.raw_text = str(raw_text or "")


class TaxonomyProjectionReviewRequired(ValueError):
    """The scoring payload is usable but its tag projection needs a teacher."""

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
    question_type_confirmed: bool = False
    rich_question_blocks: tuple[Mapping[str, Any], ...] = ()
    rich_answer_blocks: tuple[Mapping[str, Any], ...] = ()
    word_question_blocks: tuple[Mapping[str, Any], ...] = ()
    word_answer_blocks: tuple[Mapping[str, Any], ...] = ()
    images: tuple[QuestionAnalysisImage, ...] = ()
    taxonomy_contract: QuestionTaxonomySnapshot | Mapping[str, Any] = field(
        default_factory=dict
    )
    reference_solution: Mapping[str, Any] = field(default_factory=dict)
    repair_context: Mapping[str, Any] = field(default_factory=dict)
    semantic_source: str = "text"

    def __post_init__(self) -> None:
        if self.semantic_source not in {"text", "images"}:
            raise ValueError("question semantic source is invalid")
        if self.semantic_source == "images" and not any(image.role == "question" for image in self.images):
            raise ValueError("PDF 题目缺少题干裁图，不能用识别文字代替完整题面分析")
        if isinstance(self.question_id, bool) or int(self.question_id) <= 0:
            raise ValueError("question_id must be positive")
        if not str(self.tagging_context.question_text or "").strip():
            raise ValueError("question text must not be empty")
        object.__setattr__(self, "question_id", int(self.question_id))
        object.__setattr__(
            self,
            "question_type_confirmed",
            self.question_type_confirmed is True,
        )
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
        object.__setattr__(
            self,
            "word_question_blocks",
            tuple(dict(item) for item in self.word_question_blocks),
        )
        object.__setattr__(
            self,
            "word_answer_blocks",
            tuple(dict(item) for item in self.word_answer_blocks),
        )
        object.__setattr__(self, "images", tuple(self.images))
        object.__setattr__(
            self,
            "taxonomy_contract",
            QuestionTaxonomySnapshot.capture(
                self.question_id,
                self.taxonomy_contract,
            ),
        )
        reference_solution = dict(self.reference_solution)
        if reference_solution:
            required = {
                "text", "source_segments", "rich_blocks", "trust_level", "source_kind"
            }
            if set(reference_solution) != required or reference_solution.get(
                "trust_level"
            ) not in {"teacher_confirmed", "source_extracted", "absent"}:
                raise ValueError("reference_solution fields are invalid")
        object.__setattr__(self, "reference_solution", reference_solution)
        repair_context = dict(self.repair_context)
        if repair_context:
            required_fields = {
                "mode",
                "validation_error",
                "previous_result",
            }
            optional_fields = {
                "attempt",
                "failure_signature",
                "validation_issues",
                "immutable_fields",
                "allowed_changes",
            }
            if not required_fields.issubset(repair_context) or not set(
                repair_context
            ).issubset(required_fields | optional_fields):
                raise ValueError("repair_context fields are invalid")
            if repair_context.get("mode") != "repair_previous_rejected_result":
                raise ValueError("repair_context mode is invalid")
            validation_error = str(
                repair_context.get("validation_error") or ""
            ).strip()
            previous_result = repair_context.get("previous_result")
            if not validation_error or not isinstance(previous_result, Mapping):
                raise ValueError("repair_context is incomplete")
            issues = repair_context.get("validation_issues", [])
            if not isinstance(issues, list) or len(issues) > 20 or any(
                not isinstance(item, Mapping) for item in issues
            ):
                raise ValueError("repair_context validation_issues are invalid")
            if "attempt" in repair_context:
                attempt = repair_context.get("attempt")
                if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= 3:
                    raise ValueError("repair_context attempt is invalid")
            if "failure_signature" in repair_context:
                signature = str(repair_context.get("failure_signature") or "")
                if not re.fullmatch(r"[0-9a-f]{64}", signature):
                    raise ValueError("repair_context failure_signature is invalid")
            for field_name in ("immutable_fields", "allowed_changes"):
                values = repair_context.get(field_name, [])
                if not isinstance(values, list) or len(values) > 20 or any(
                    not str(item or "").strip() for item in values
                ):
                    raise ValueError(f"repair_context {field_name} is invalid")
            serialized = json.dumps(
                repair_context,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if len(serialized) > 60_000:
                raise ValueError("repair_context exceeds the size limit")
            repair_context = json.loads(serialized)
        object.__setattr__(self, "repair_context", repair_context)

    @property
    def has_required_images(self) -> bool:
        if not self.tagging_context.has_images:
            return True
        return any(image.role == "question" for image in self.images)

    @property
    def taxonomy_snapshot(self) -> QuestionTaxonomySnapshot:
        snapshot = self.taxonomy_contract
        if not isinstance(snapshot, QuestionTaxonomySnapshot):
            raise RuntimeError("question taxonomy snapshot was not captured")
        return snapshot

    @property
    def source_content_hash(self) -> str:
        # 题目指纹只描述"这道题现在的样子"：题干、答案、题型、配图与册别。
        # 词表/知识标准状态绝不进入指纹——它是逐题的"题变了才重做"开关，
        # 词表版本是全局的；一旦嵌入，教师确认一个新词就会让全库已保存的
        # 标签整体过期。需要按新词表刷新时，用显式的"重新打标签"提交。
        # evidence_parts 是判定点保存后的派生状态而非题目内容：纳入会让
        # 每次保存判定点后全题被视为"题变了"而反复重打。
        context = {
            key: value
            for key, value in self.tagging_context.to_dict().items()
            if key != "evidence_parts"
        }
        return _hash_payload(
            {
                "question_id": self.question_id,
                "tagging_context": context,
                "question_type_confirmed": self.question_type_confirmed,
                **({"semantic_source": "images"} if self.semantic_source == "images" else {}),
                "explicit_part_labels": list(self.explicit_part_labels),
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
                "reference_solution": self.reference_solution,
            }
        )

    @property
    def criterion_source_content_hash(self) -> str:
        context = self.tagging_context
        return _hash_payload(
            {
                "question_id": self.question_id,
                "question_text": context.question_text,
                "answer_text": context.answer_text,
                "question_type": context.question_type,
                "question_type_confirmed": self.question_type_confirmed,
                **({"semantic_source": "images"} if self.semantic_source == "images" else {}),
                "explicit_part_labels": list(self.explicit_part_labels),
                "has_images": context.has_images,
                "rich_question_blocks": self.rich_question_blocks,
                "rich_answer_blocks": self.rich_answer_blocks,
                "reference_solution": self.reference_solution,
                "image_hashes": [
                    {
                        "role": image.role,
                        "mime_type": image.mime_type,
                        "sha256": image.sha256,
                    }
                    for image in self.images
                ],
            }
        )

    @property
    def explicit_part_labels(self) -> tuple[str, ...]:
        """Return only an objective, sequential (1)(2)... structure fact."""

        # subq_mark_labels 与 parsers/type_detector 的排除语义一致：
        # 公式下标 S_(1)、指数 x^(1)、分数 (1)/(2)、函数参数 f(1) 和
        # 图(1)/式(1) 等编号引用都不算小问；真实小问标记不受影响。
        ordered = subq_mark_labels(self.tagging_context.question_text)
        if len(ordered) < 2:
            return ()
        expected = tuple(str(index) for index in range(1, len(ordered) + 1))
        return ordered if ordered == expected else ()

    @property
    def question_type_group(self) -> str:
        # 子类标签优先：special_type 标签决定解答题题组（证明/画图保持
        # 单题成批）；无标签时回退题型字符串与题干关键词兜底。
        special_types = (
            self.tagging_context.existing_tags_by_dimension.get("special_type")
            or ()
        )
        for subtype in ESSAY_SUBTYPES:
            if subtype in special_types:
                return _ESSAY_SUBTYPE_GROUPS[subtype]
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

    @property
    def objective_response_shape(self) -> ObjectiveResponseShape:
        """Return only objective response facts that are safe to enforce locally."""

        return self.objective_response_shape_for(self.question_type_group)

    def objective_response_shape_for(
        self,
        question_type_group: str,
    ) -> ObjectiveResponseShape:
        """按给定题组推导客观作答形态（题型建议被采纳时使用真实题组）。"""

        if self.explicit_part_labels:
            return "unknown"
        if not str(self.tagging_context.answer_text or "").strip():
            return "unknown"
        if question_type_group == "single_choice":
            return "single_choice"
        if question_type_group != "fill_blank":
            return "unknown"
        text = str(self.tagging_context.question_text or "")
        named = re.findall(r"第[一二三四五六七八九十\d]+空", text)
        underscores = re.findall(r"_{2,}|＿{2,}|　{2,}", text)
        empty_brackets = re.findall(r"[（(]\s*[）)]", text)
        blank_count = max(len(named), len(underscores) + len(empty_brackets), 1)
        return "single_blank" if blank_count == 1 else "multiple_blank"


@dataclass(frozen=True, slots=True)
class QuestionTypeSuggestion:
    """联合分析返回的题型建议；只是候选，教师确认题型始终优先。

    essay_subtype 是解答题子类（画图/计算/证明）建议，落库时写
    special_type 标签而不是改动题型枚举。
    """

    question_type: str
    reason: str = ""
    essay_subtype: str | None = None

    @classmethod
    def from_dict(cls, payload: object) -> QuestionTypeSuggestion:
        if not isinstance(payload, Mapping):
            raise ProjectionValidationError("question type suggestion is invalid")
        value = str(payload.get("question_type") or "").strip()
        if value not in QUESTION_TYPES:
            raise ProjectionValidationError(
                "question type suggestion is not a supported type"
            )
        subtype = str(payload.get("essay_subtype") or "").strip() or None
        if subtype is not None and (
            subtype not in ESSAY_SUBTYPES or value != "解答题"
        ):
            raise ProjectionValidationError(
                "essay subtype suggestion is not a supported subtype"
            )
        return cls(
            question_type=value,
            reason=str(payload.get("reason") or "").strip(),
            essay_subtype=subtype,
        )

    @property
    def question_type_group(self) -> str:
        group = _QUESTION_TYPE_GROUPS[self.question_type]
        if self.essay_subtype is not None:
            return _ESSAY_SUBTYPE_GROUPS[self.essay_subtype]
        return group

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_type": self.question_type,
            "reason": self.reason,
            "essay_subtype": self.essay_subtype,
        }


@dataclass(frozen=True, slots=True)
class QuestionAnalysisWorkItem:
    """One question and the projections still required by its caller.

    Callers describe the missing outcomes; the analysis module owns grouping,
    operation identities, progress aggregation, persistence, and audit merging.
    """

    question: QuestionAnalysisInput
    analyze_tag: bool = True
    analyze_solution_evidence: bool = True
    publish_saved_criterion: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.question, QuestionAnalysisInput):
            raise TypeError("question must be a QuestionAnalysisInput")
        if not (
            self.analyze_tag
            or self.analyze_solution_evidence
            or self.publish_saved_criterion
        ):
            raise ValueError("analysis work item must request at least one outcome")

    @property
    def projection(self) -> AnalysisProjection | None:
        if self.analyze_tag and self.analyze_solution_evidence:
            return "both"
        if self.analyze_tag:
            return "tag"
        if self.analyze_solution_evidence:
            return "training_criteria"
        return None


@dataclass(frozen=True, slots=True)
class TrainingCriterionPoint:
    point_id: str
    target: str
    observable_evidence: str
    equivalent_rules: tuple[str, ...] = ()
    counterexamples: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TrainingCriterionPoint:
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
        raw_depends = payload.get("depends_on")
        if raw_depends is None:
            depends_on: tuple[str, ...] = ()
        elif not isinstance(raw_depends, list) or not all(
            isinstance(item, str) for item in raw_depends
        ):
            raise ProjectionValidationError("depends_on must be an array of identifiers")
        else:
            depends_on = _text_tuple(
                [str(item).strip().casefold() for item in raw_depends]
            )
        if any(not _POINT_ID.fullmatch(item) for item in depends_on):
            raise ProjectionValidationError("depends_on contains an invalid point_id")
        if len(depends_on) != len(set(depends_on)):
            raise ProjectionValidationError("depends_on contains a duplicated point_id")
        return cls(
            point_id=point_id,
            target=target,
            observable_evidence=evidence,
            equivalent_rules=_text_tuple(payload.get("equivalent_rules")),
            counterexamples=_text_tuple(payload.get("counterexamples")),
            depends_on=depends_on,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "point_id": self.point_id,
            "target": self.target,
            "observable_evidence": self.observable_evidence,
            "equivalent_rules": list(self.equivalent_rules),
            "counterexamples": list(self.counterexamples),
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True, slots=True)
class TrainingCriteriaDraft:
    schema_version: CriterionSchemaVersion
    question_id: int
    source_content_hash: str
    question_type: str
    points: tuple[TrainingCriterionPoint, ...]
    auxiliary_rules: tuple[str, ...]
    rationale: str
    confidence: float
    source_kind: Literal["combined_model", "confirmed_rubric_adapter"]
    embedded_evidence_json: str = ""

    @classmethod
    def from_model_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        question: QuestionAnalysisInput,
    ) -> TrainingCriteriaDraft:
        _reject_score_fields(payload)
        schema = str(payload.get("schema_version") or "")
        if schema not in {LEGACY_CRITERIA_SCHEMA, JUDGMENT_POINTS_SCHEMA}:
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
        known_ids = {point.point_id for point in points}
        for point in points:
            if any(item not in known_ids for item in point.depends_on):
                raise ProjectionValidationError(
                    "depends_on references an unknown point_id"
                )
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
        embedded = payload.get("solution_evidence")
        embedded_json = ""
        if schema == JUDGMENT_POINTS_SCHEMA and isinstance(embedded, Mapping):
            _reject_score_fields(embedded)
            embedded_json = json.dumps(
                dict(embedded),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        return cls(
            schema_version=schema,  # type: ignore[arg-type]
            question_id=question.question_id,
            source_content_hash=question.criterion_source_content_hash,
            question_type=question.question_type_group,
            points=points,
            auxiliary_rules=_text_tuple(payload.get("auxiliary_rules")),
            rationale=str(payload.get("rationale") or "").strip(),
            confidence=confidence,
            source_kind="combined_model",
            embedded_evidence_json=embedded_json,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "question_id": self.question_id,
            "source_content_hash": self.source_content_hash,
            "question_type": self.question_type,
            "points": [point.to_dict() for point in self.points],
            "auxiliary_rules": list(self.auxiliary_rules),
            "rationale": self.rationale,
            "confidence": self.confidence,
            "source_kind": self.source_kind,
        }
        if self.schema_version == JUDGMENT_POINTS_SCHEMA and self.embedded_evidence_json:
            payload["solution_evidence"] = json.loads(self.embedded_evidence_json)
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
                "repair_context_hashes": [
                    _hash_payload(item.repair_context)
                    if item.repair_context
                    else ""
                    for item in self.questions
                ],
                "input": self.estimated_input_tokens,
                "output": self.estimated_output_tokens,
            }
        )


class QuestionAnalysisGateway(Protocol):
    @property
    def max_parallel_requests(self) -> int:
        ...

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


class SolutionEvidenceWriter(Protocol):
    def write(
        self,
        question: QuestionAnalysisInput,
        payload: Mapping[str, Any],
        *,
        model_name: str,
        operation_id: str,
        objective_response_shape: str | None = None,
    ) -> QuestionSolutionEvidence:
        ...

    def load_current(
        self,
        question: QuestionAnalysisInput,
    ) -> QuestionSolutionEvidence | None:
        ...


class QuestionTypeSuggestionWriter(Protocol):
    """把题型建议落到题库写边界；教师确认的题型只登记冲突不改数据。"""

    def apply(
        self,
        question: QuestionAnalysisInput,
        suggestion: QuestionTypeSuggestion,
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
    """Deep module for one request: tags plus one detailed unscored 判定点."""

    def __init__(
        self,
        *,
        repository: AnalysisProjectionRepository,
        gateway: QuestionAnalysisGateway,
        tag_writer: TagProjectionWriter,
        evidence_writer: SolutionEvidenceWriter | None = None,
        criterion_module: Any | None = None,
        question_type_writer: QuestionTypeSuggestionWriter | None = None,
    ) -> None:
        self.repository = repository
        self.gateway = gateway
        self.tag_writer = tag_writer
        self.evidence_writer = evidence_writer
        self.criterion_module = criterion_module
        self.question_type_writer = question_type_writer
        self._criterion_audits: dict[tuple[str, int], dict[str, Any]] = {}
        self._criterion_audit_lock = threading.Lock()
        self._projection_notes: dict[tuple[str, int], dict[str, str]] = {}
        self._projection_notes_lock = threading.Lock()

    def analyze_work_items(
        self,
        *,
        operation_id: str,
        work_items: Sequence[QuestionAnalysisWorkItem],
        progress_callback: AnalysisProgressCallback | None = None,
    ) -> Mapping[str, Any]:
        """Run mixed missing projections behind one stable module boundary.

        The persistence repository still stores one projection shape per
        operation.  This method deliberately hides those internal child
        operations and returns one question-oriented outcome to the caller.
        """

        clean_operation = _required_text(operation_id, "operation_id")
        normalized = tuple(work_items)
        if not normalized:
            return {
                "operation_id": clean_operation,
                "items": [],
                "projection_audit": _merge_projection_audits(),
                "question_projection_audits": {},
                "criterion_audit": self.criterion_audit_summary(
                    clean_operation,
                    (),
                ),
                "operation_ids": {},
            }
        question_ids = [item.question.question_id for item in normalized]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("analysis work items must have unique question ids")

        groups: dict[AnalysisProjection, list[QuestionAnalysisInput]] = {
            "both": [],
            "tag": [],
            "training_criteria": [],
        }
        for item in normalized:
            if item.publish_saved_criterion:
                audit = self._publish_saved_criterion(item.question)
                with self._criterion_audit_lock:
                    self._criterion_audits[
                        (clean_operation, item.question.question_id)
                    ] = audit
            projection = item.projection
            if projection is not None:
                groups[projection].append(item.question)

        operation_ids: dict[str, str] = {}
        summaries: list[Mapping[str, Any]] = []
        projection_audits: list[Mapping[str, Any]] = []
        question_projection_audits: dict[int, list[Mapping[str, Any]]] = {
            question_id: [] for question_id in question_ids
        }
        total_questions = sum(len(group) for group in groups.values())
        completed_questions = 0

        for projection in ("both", "tag", "training_criteria"):
            questions = tuple(groups[projection])
            if not questions:
                continue
            child_operation = _child_operation_id(clean_operation, projection)
            operation_ids[projection] = child_operation
            group_start = completed_questions

            def report(
                update: Mapping[str, Any],
                *,
                start: int = group_start,
                size: int = len(questions),
            ) -> None:
                if progress_callback is None:
                    return
                processed = min(
                    size,
                    max(0, int(update.get("processed_questions") or 0)),
                )
                progress_callback(
                    {
                        **dict(update),
                        "operation_id": clean_operation,
                        "processed_questions": start + processed,
                        "total_questions": total_questions,
                    }
                )

            summary = self.analyze(
                operation_id=child_operation,
                questions=questions,
                projection=projection,
                progress_callback=report,
            )
            summaries.append(summary)
            ids = [question.question_id for question in questions]
            if projection in {"both", "tag"}:
                projection_audits.append(
                    _writer_audit_summary(self.tag_writer, child_operation, ids)
                )
                for question_id in ids:
                    question_projection_audits[question_id].append(
                        _writer_audit_summary(
                            self.tag_writer,
                            child_operation,
                            [question_id],
                        )
                    )
            if projection in {"both", "training_criteria"}:
                projection_audits.append(
                    _writer_audit_summary(
                        self.evidence_writer,
                        child_operation,
                        ids,
                    )
                )
                for question_id in ids:
                    question_projection_audits[question_id].append(
                        _writer_audit_summary(
                            self.evidence_writer,
                            child_operation,
                            [question_id],
                        )
                    )
                child_criteria = self.criterion_audit_summary(
                    child_operation,
                    ids,
                )
                with self._criterion_audit_lock:
                    for row in child_criteria.get("items", []):
                        if not isinstance(row, Mapping):
                            continue
                        question_id = int(row.get("question_id") or 0)
                        audit = {
                            key: value
                            for key, value in row.items()
                            if key != "question_id"
                        }
                        self._criterion_audits[(clean_operation, question_id)] = audit
            completed_questions += len(questions)

        items_by_id: dict[int, dict[str, Any]] = {}
        for summary in summaries:
            for row in summary.get("items", []):
                if isinstance(row, Mapping):
                    items_by_id[int(row["question_id"])] = dict(row)
        for item in normalized:
            question_id = item.question.question_id
            row = items_by_id.setdefault(
                question_id,
                {
                    "question_id": question_id,
                    "tag_status": "not_requested",
                    "tag_error_category": "",
                    "criteria_status": "not_requested",
                    "criteria_error_category": "",
                },
            )
            if item.publish_saved_criterion and not item.analyze_solution_evidence:
                # Stored evidence remains a successful evidence projection even
                # when publishing its independent criterion version fails.
                row["criteria_status"] = "succeeded"
                row["criteria_error_category"] = ""

        return {
            "operation_id": clean_operation,
            "items": [
                items_by_id[question_id]
                for question_id in question_ids
            ],
            "projection_audit": _merge_projection_audits(*projection_audits),
            "question_projection_audits": {
                question_id: _merge_projection_audits(*audits)
                for question_id, audits in question_projection_audits.items()
            },
            "criterion_audit": self.criterion_audit_summary(
                clean_operation,
                question_ids,
            ),
            "operation_ids": operation_ids,
        }

    def criterion_audit_summary(
        self,
        operation_id: str,
        question_ids: Sequence[int],
    ) -> dict[str, Any]:
        """Return the independent training-version save result.

        The combined analysis projection is intentionally allowed to remain
        successful when evidence was saved but publishing its training-point
        version failed.  Jobs use this small audit seam to retry only the
        missing training projection on a later run.
        """

        rows: list[dict[str, Any]] = []
        succeeded: list[int] = []
        failed: list[int] = []
        needs_review: list[int] = []
        for question_id in question_ids:
            with self._criterion_audit_lock:
                audit = dict(
                    self._criterion_audits.get(
                        (str(operation_id), int(question_id)),
                    )
                    or {"status": "not_requested"},
                )
            row = {"question_id": int(question_id), **audit}
            rows.append(row)
            if audit.get("status") == "succeeded":
                succeeded.append(int(question_id))
            elif audit.get("status") == "failed":
                failed.append(int(question_id))
            elif audit.get("status") == "needs_review":
                needs_review.append(int(question_id))
        return {
            "items": rows,
            "succeeded_question_ids": succeeded,
            "failed_question_ids": failed,
            "needs_review_question_ids": needs_review,
        }

    def analyze(
        self,
        *,
        operation_id: str,
        questions: Sequence[QuestionAnalysisInput],
        projection: AnalysisProjection = "both",
        progress_callback: AnalysisProgressCallback | None = None,
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
            return self._summary_with_annotations(clean_operation)
        self._execute(
            operation_id=clean_operation,
            questions=normalized,
            projection=normalized_projection,
            retry=False,
            progress_callback=progress_callback,
        )
        return self._summary_with_annotations(clean_operation)

    def retry_failed_projection(
        self,
        *,
        operation_id: str,
        questions: Sequence[QuestionAnalysisInput],
        projection: Literal["tag", "training_criteria"],
        progress_callback: AnalysisProgressCallback | None = None,
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
            progress_callback=progress_callback,
        )
        return self._summary_with_annotations(clean_operation)

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
        return self._summary_with_annotations(clean_operation)

    def _execute(
        self,
        *,
        operation_id: str,
        questions: tuple[QuestionAnalysisInput, ...],
        projection: AnalysisProjection,
        retry: bool,
        progress_callback: AnalysisProgressCallback | None = None,
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
            ready.append(question)

        batches = plan_analysis_batches(
            tuple(ready),
            projection=projection,
        )
        total_questions = sum(len(batch.questions) for batch in batches)
        if not batches:
            self._report_progress(
                progress_callback,
                operation_id=operation_id,
                projection=projection,
                processed_batches=0,
                total_batches=0,
                processed_questions=0,
                total_questions=0,
            )
            return

        worker_count = min(
            len(batches),
            gateway_parallel_limit(self.gateway),
        )
        base_request_count = int(
            self.repository.operation_summary(operation_id).get(
                "request_count",
                0,
            )
        )
        future_map: dict[Future[GatewayBatchResponse], _BatchAttempt] = {}
        next_batch_index = 0
        repair_request_count = 0
        processed_batches = 0
        processed_questions = 0
        stop_scheduling = False
        stop_category = "cancelled"
        canary_succeeded = False
        feedback_budget = _gateway_feedback_limit(self.gateway)

        def issue_request(
            executor: ThreadPoolExecutor,
            *,
            request_batch: PlannedAnalysisBatch,
            feedback_left: int,
            question_count: int,
            request_number: int,
            repair_scopes: Mapping[int, frozenset[str]] | None = None,
        ) -> None:
            request_id = _hash_payload(
                {
                    "operation_id": operation_id,
                    "projection": projection,
                    "batch_hash": request_batch.batch_hash,
                    "request_number": request_number,
                }
            )
            self.repository.record_request_started(
                operation_id=operation_id,
                request_id=request_id,
                projection=projection,
                batch=request_batch,
            )
            future = executor.submit(
                self.gateway.analyze,
                request_batch,
                projection=projection,
                operation_id=operation_id,
                request_id=request_id,
            )
            future_map[future] = _BatchAttempt(
                batch=request_batch,
                request_id=request_id,
                feedback_left=feedback_left,
                question_count=question_count,
                repair_scopes=repair_scopes,
            )

        def submit_next(
            executor: ThreadPoolExecutor,
        ) -> None:
            nonlocal next_batch_index
            batch_index = next_batch_index
            next_batch_index += 1
            issue_request(
                executor,
                request_batch=batches[batch_index],
                feedback_left=feedback_budget,
                question_count=len(batches[batch_index].questions),
                request_number=base_request_count + batch_index + 1,
            )

        def submit_repair(
            executor: ThreadPoolExecutor,
            *,
            questions: tuple[QuestionAnalysisInput, ...],
            source_attempt: _BatchAttempt,
            repair_scopes: Mapping[int, frozenset[str]] | None = None,
        ) -> None:
            nonlocal repair_request_count
            repair_request_count += 1
            issue_request(
                executor,
                request_batch=PlannedAnalysisBatch(
                    questions=questions,
                    estimated_input_tokens=(
                        source_attempt.batch.estimated_input_tokens
                    ),
                    estimated_output_tokens=(
                        source_attempt.batch.estimated_output_tokens
                    ),
                ),
                feedback_left=source_attempt.feedback_left - 1,
                # Repair attempts carry the original chain's question count so
                # the progress totals stay stable across retries.
                question_count=source_attempt.question_count,
                repair_scopes=repair_scopes,
                request_number=(
                    base_request_count + len(batches) + repair_request_count
                ),
            )

        def fill_available_slots(
            executor: ThreadPoolExecutor,
        ) -> None:
            active_limit = worker_count if canary_succeeded else 1
            while (
                not stop_scheduling
                and next_batch_index < len(batches)
                and len(future_map) < active_limit
            ):
                submit_next(executor)

        def finalize(question_count: int) -> None:
            nonlocal processed_batches, processed_questions
            processed_batches += 1
            processed_questions += question_count
            self._report_progress(
                progress_callback,
                operation_id=operation_id,
                projection=projection,
                processed_batches=processed_batches,
                total_batches=len(batches),
                processed_questions=processed_questions,
                total_questions=total_questions,
            )

        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="question-analysis",
        ) as executor:
            fill_available_slots(executor)
            while future_map:
                future = next(as_completed(tuple(future_map)))
                attempt = future_map.pop(future)
                batch = attempt.batch

                response: GatewayBatchResponse | None = None
                try:
                    response = future.result()
                except GatewayResponseParseError as exc:
                    # The response arrived but was not usable JSON; hand the
                    # sanitized error and raw output back to the model once per
                    # remaining budget unit.
                    category = _error_category(exc)
                    self.repository.record_request_finished(
                        request_id=attempt.request_id,
                        status="failed",
                        error_category=category,
                    )
                    if attempt.feedback_left > 0 and not stop_scheduling:
                        submit_repair(
                            executor,
                            questions=tuple(
                                _question_with_repair_context(
                                    question,
                                    validation_error=_projection_error_detail(
                                        exc
                                    ),
                                    previous_result=_raw_text_previous_result(
                                        exc.raw_text
                                    ),
                                )
                                for question in batch.questions
                            ),
                            source_attempt=attempt,
                        )
                        continue
                    self._fail_batch(
                        operation_id=operation_id,
                        batch=batch,
                        projection=projection,
                        retry=retry,
                        category=category,
                    )
                    if _stops_batch_scheduling(category):
                        stop_scheduling = True
                        stop_category = category
                    finalize(attempt.question_count)
                    fill_available_slots(executor)
                    continue
                except Exception as exc:
                    category = _error_category(exc)
                    self.repository.record_request_finished(
                        request_id=attempt.request_id,
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
                    if _stops_batch_scheduling(category):
                        stop_scheduling = True
                        stop_category = category
                    finalize(attempt.question_count)
                    fill_available_slots(executor)
                    continue

                try:
                    items, merge_notes = _response_items(
                        response.payload,
                        batch,
                    )
                except ProjectionValidationError as exc:
                    category = _error_category(exc)
                    self.repository.record_request_finished(
                        request_id=attempt.request_id,
                        status="failed",
                        error_category=category,
                    )
                    if attempt.feedback_left > 0 and not stop_scheduling:
                        submit_repair(
                            executor,
                            questions=tuple(
                                _question_with_repair_context(
                                    question,
                                    validation_error=_projection_error_detail(
                                        exc
                                    ),
                                    previous_result=_repair_previous_result(
                                        response.payload
                                    ),
                                )
                                for question in batch.questions
                            ),
                            source_attempt=attempt,
                        )
                        continue
                    self._fail_batch(
                        operation_id=operation_id,
                        batch=batch,
                        projection=projection,
                        retry=retry,
                        category=category,
                    )
                    if _stops_batch_scheduling(category):
                        stop_scheduling = True
                        stop_category = category
                    finalize(attempt.question_count)
                    fill_available_slots(executor)
                    continue
                except Exception as exc:
                    category = _error_category(exc)
                    self.repository.record_request_finished(
                        request_id=attempt.request_id,
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
                    if _stops_batch_scheduling(category):
                        stop_scheduling = True
                        stop_category = category
                    finalize(attempt.question_count)
                    fill_available_slots(executor)
                    continue

                canary_succeeded = True
                self.repository.record_request_finished(
                    request_id=attempt.request_id,
                    status="succeeded",
                    response=response,
                )
                for merged_question_id, merge_note in merge_notes.items():
                    self._record_projection_note(
                        operation_id,
                        merged_question_id,
                        "merge_note",
                        merge_note,
                    )
                failed_feedback: dict[
                    int,
                    tuple[str, Mapping[str, Any], frozenset[str]],
                ] = {}
                repair_scopes = attempt.repair_scopes
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
                        failed_feedback[question.question_id] = (
                            "响应缺少该题的分析结果。",
                            {},
                            frozenset(selected),
                        )
                        continue
                    scopes = (
                        repair_scopes.get(question.question_id)
                        if repair_scopes is not None
                        else None
                    )
                    detail: str | None = None
                    failed_here: set[str] = set()
                    if "tag" in selected and (
                        scopes is None or "tag" in scopes
                    ):
                        status = self.repository.projection_status(
                            operation_id,
                            question.question_id,
                            "tag",
                        )
                        if not retry or status in {
                            "failed",
                            "cancelled",
                            "pending",
                        }:
                            detail = self._save_tag(
                                operation_id,
                                question,
                                raw,
                                response.model_name,
                            )
                            if detail:
                                failed_here.add("tag")
                    if "training_criteria" in selected and (
                        scopes is None or "training_criteria" in scopes
                    ):
                        status = self.repository.projection_status(
                            operation_id,
                            question.question_id,
                            "training_criteria",
                        )
                        if not retry or status in {
                            "failed",
                            "cancelled",
                            "pending",
                        }:
                            criteria_detail = self._save_criteria(
                                operation_id,
                                question,
                                raw,
                                response.model_name,
                            )
                            if criteria_detail:
                                failed_here.add("training_criteria")
                                if detail is None:
                                    detail = criteria_detail
                    if failed_here:
                        failed_feedback[question.question_id] = (
                            detail or "分析结果未通过本地校验。",
                            raw,
                            frozenset(failed_here),
                        )
                if (
                    failed_feedback
                    and attempt.feedback_left > 0
                    and not stop_scheduling
                ):
                    submit_repair(
                        executor,
                        questions=tuple(
                            _question_with_repair_context(
                                question,
                                validation_error=failed_feedback[
                                    question.question_id
                                ][0],
                                previous_result=_repair_previous_result(
                                    failed_feedback[question.question_id][1]
                                ),
                            )
                            for question in batch.questions
                            if question.question_id in failed_feedback
                        ),
                        source_attempt=attempt,
                        repair_scopes={
                            question_id: scopes
                            for question_id, (_, _, scopes) in
                            failed_feedback.items()
                        },
                    )
                    continue
                finalize(attempt.question_count)
                fill_available_slots(executor)

        if stop_scheduling and next_batch_index < len(batches):
            for remaining in batches[next_batch_index:]:
                self._fail_batch(
                    operation_id=operation_id,
                    batch=remaining,
                    projection=projection,
                    retry=retry,
                    category=stop_category,
                )
                processed_batches += 1
                processed_questions += len(remaining.questions)
            self._report_progress(
                progress_callback,
                operation_id=operation_id,
                projection=projection,
                processed_batches=processed_batches,
                total_batches=len(batches),
                processed_questions=processed_questions,
                total_questions=total_questions,
            )

    def _report_progress(
        self,
        callback: AnalysisProgressCallback | None,
        *,
        operation_id: str,
        projection: AnalysisProjection,
        processed_batches: int,
        total_batches: int,
        processed_questions: int,
        total_questions: int,
    ) -> None:
        if callback is None:
            return
        try:
            callback(
                {
                    "operation_id": operation_id,
                    "projection": projection,
                    "processed_batches": int(processed_batches),
                    "total_batches": int(total_batches),
                    "processed_questions": int(processed_questions),
                    "total_questions": int(total_questions),
                    "summary": dict(
                        self.repository.operation_summary(operation_id)
                    ),
                }
            )
        except Exception as exc:
            # Progress reporting is observational. A UI/store failure must not
            # turn a saved model result into a failed analysis request.
            logging.getLogger(__name__).warning(
                "analysis progress callback failed (%s)", type(exc).__name__,
            )
            return

    def _record_projection_note(
        self,
        operation_id: str,
        question_id: int,
        key: str,
        value: str,
    ) -> None:
        text = str(value or "").strip()
        if not text:
            return
        with self._projection_notes_lock:
            notes = self._projection_notes.setdefault(
                (str(operation_id), int(question_id)),
                {},
            )
            notes[key] = text

    def _summary_with_annotations(
        self,
        operation_id: str,
    ) -> Mapping[str, Any]:
        """Attach in-flight notes (merge audit, sanitized failure detail).

        台账本身保持原有列不变；这些说明只叠加在返回给调用方的任务结果上。
        criteria 投影失败时写入 criteria_payload_json 的 validation_error
        也会在进程重启后继续随摘要带出。
        """

        summary = self.repository.operation_summary(operation_id)
        with self._projection_notes_lock:
            notes_by_question = {
                question_id: dict(notes)
                for (note_operation, question_id), notes in
                self._projection_notes.items()
                if note_operation == str(operation_id)
            }
        items: list[dict[str, Any]] = []
        for row in summary.get("items", []):
            if not isinstance(row, Mapping):
                items.append(row)
                continue
            item = dict(row)
            notes = notes_by_question.get(int(item.get("question_id") or 0))
            if notes:
                item.update(notes)
            if "criteria_error_detail" not in item and str(
                item.get("criteria_status") or ""
            ) == "failed":
                persisted = item.get("training_criteria")
                if isinstance(persisted, Mapping):
                    detail = str(
                        persisted.get("validation_error") or ""
                    ).strip()
                    if detail:
                        item["criteria_error_detail"] = detail
            items.append(item)
        return {**dict(summary), "items": items}

    def _save_tag(
        self,
        operation_id: str,
        question: QuestionAnalysisInput,
        raw: Mapping[str, Any],
        model_name: str,
    ) -> str | None:
        """Persist the tag projection; return a sanitized failure detail."""
        payload = raw.get("tag_analysis")
        if not isinstance(payload, Mapping):
            self.repository.save_projection(
                operation_id=operation_id,
                question_id=question.question_id,
                projection="tag",
                status="failed",
                error_category="tag_validation",
            )
            return "响应缺少 tag_analysis 对象。"
        try:
            tag_question = question
            evidence_payload = raw.get("solution_evidence")
            if isinstance(evidence_payload, Mapping) and isinstance(evidence_payload.get("parts"), list):
                tag_question = dataclass_replace(question, tagging_context=dataclass_replace(
                    question.tagging_context,
                    evidence_parts=[{"part_id": str(part.get("part_id") or "")} for part in evidence_payload["parts"] if isinstance(part, Mapping)],
                ))
            normalized = self.tag_writer.write(
                tag_question,
                payload,
                model_name=model_name,
                operation_id=operation_id,
            )
        except Exception as exc:
            detail = _projection_error_detail(exc)
            self._record_projection_note(
                operation_id,
                question.question_id,
                "tag_error_detail",
                detail,
            )
            self.repository.save_projection(
                operation_id=operation_id,
                question_id=question.question_id,
                projection="tag",
                status="failed",
                error_category="tag_validation",
            )
            return detail
        # 仅打标签流程：题型建议只有在建议题型与本地题型一致时才落库
        # （此时只会写 special_type 子类标签，不会改 question_type，
        # 因为判定点未重新生成）；题型不一致只登记说明。
        suggestion = self._question_type_suggestion(
            operation_id,
            question,
            raw,
        )
        type_audit: dict[str, Any] | None = None
        if suggestion is not None:
            local_type = str(
                question.tagging_context.question_type or ""
            ).strip()
            if suggestion.question_type == local_type:
                type_audit = self._apply_question_type_suggestion(
                    operation_id,
                    question,
                    suggestion,
                    model_name=model_name,
                )
            else:
                self._record_projection_note(
                    operation_id,
                    question.question_id,
                    "question_type_suggestion_note",
                    f"模型建议题型“{suggestion.question_type}”与本地题型"
                    f"“{local_type}”不一致；仅打标签流程不改动题型。",
                )
        tag_payload: Any = (
            {**dict(normalized)} if isinstance(normalized, Mapping)
            else normalized
        )
        if type_audit is not None and isinstance(tag_payload, dict):
            tag_payload = {
                **tag_payload,
                "question_type_suggestion": type_audit,
            }
        self.repository.save_projection(
            operation_id=operation_id,
            question_id=question.question_id,
            projection="tag",
            status="succeeded",
            payload=tag_payload,
        )
        return None

    def _save_criteria(
        self,
        operation_id: str,
        question: QuestionAnalysisInput,
        raw: Mapping[str, Any],
        model_name: str,
    ) -> str | None:
        """Persist the criteria/evidence projection; return failure detail."""
        evidence_payload = raw.get("solution_evidence")
        legacy_payload = raw.get("training_criteria")
        if not isinstance(evidence_payload, Mapping) and not isinstance(
            legacy_payload,
            Mapping,
        ):
            self.repository.save_projection(
                operation_id=operation_id,
                question_id=question.question_id,
                projection="training_criteria",
                status="failed",
                error_category="criteria_validation",
            )
            return "响应缺少 solution_evidence 对象。"
        suggestion = self._question_type_suggestion(
            operation_id,
            question,
            raw,
        )
        effective_group = question.question_type_group
        effective_shape = question.objective_response_shape
        if (
            suggestion is not None
            and not question.question_type_confirmed
            and suggestion.question_type_group != effective_group
        ):
            # 教师未确认题型时采纳模型的真实题型，解除本地误判造成的
            # 客观形态硬约束，让解题证据与判定点按过程题生成。
            effective_group = suggestion.question_type_group
            effective_shape = question.objective_response_shape_for(
                effective_group
            )
        try:
            if isinstance(evidence_payload, Mapping):
                if self.evidence_writer is None:
                    raise ProjectionValidationError(
                        "solution evidence writer is unavailable"
                    )
                evidence = self.evidence_writer.write(
                    question,
                    evidence_payload,
                    model_name=model_name,
                    operation_id=operation_id,
                    objective_response_shape=effective_shape,
                )
                draft = training_criteria_from_solution_evidence(
                    evidence,
                    question=question,
                    objective_response_shape=effective_shape,
                    question_type_group=effective_group,
                )
            else:
                assert isinstance(legacy_payload, Mapping)
                draft = TrainingCriteriaDraft.from_model_dict(
                    legacy_payload,
                    question=question,
                )
        except Exception as exc:
            detail = _projection_error_detail(exc)
            self._record_projection_note(
                operation_id,
                question.question_id,
                "criteria_error_detail",
                detail,
            )
            self.repository.save_projection(
                operation_id=operation_id,
                question_id=question.question_id,
                projection="training_criteria",
                status="failed",
                payload={"validation_error": detail},
                error_category=(
                    "evidence_validation"
                    if isinstance(evidence_payload, Mapping)
                    else "criteria_validation"
                ),
            )
            return detail
        criterion_audit = self._publish_criterion_draft(
            question=question,
            draft=draft,
            actor_ref=f"model:{str(model_name or 'combined-analysis')}",
            reason="联合题目解析自动发布训练判定点",
            evidence_review_required=bool(
                self.evidence_writer is not None
                and getattr(
                    self.evidence_writer,
                    "criterion_review_required",
                    lambda *_args: False,
                )(operation_id, question.question_id)
            ),
            reference_conflict=(
                str(raw.get("reference_assessment") or "").strip().casefold()
                == "conflict"
            ),
        )
        with self._criterion_audit_lock:
            self._criterion_audits[(str(operation_id), question.question_id)] = (
                criterion_audit
            )
        payload = draft.to_dict()
        type_audit = self._apply_question_type_suggestion(
            operation_id,
            question,
            suggestion,
            model_name=model_name,
        )
        if type_audit is not None:
            payload["question_type_suggestion"] = type_audit
        self.repository.save_projection(
            operation_id=operation_id,
            question_id=question.question_id,
            projection="training_criteria",
            status="succeeded",
            payload=payload,
        )
        return None

    def _question_type_suggestion(
        self,
        operation_id: str,
        question: QuestionAnalysisInput,
        raw: Mapping[str, Any],
    ) -> QuestionTypeSuggestion | None:
        """解析模型的题型建议；非法枚举只登记说明，不让可用分析失败。"""

        if "question_type_suggestion" not in raw:
            return None
        try:
            return QuestionTypeSuggestion.from_dict(
                raw.get("question_type_suggestion")
            )
        except ProjectionValidationError:
            self._record_projection_note(
                operation_id,
                question.question_id,
                "question_type_suggestion_note",
                "模型题型建议不是受支持的题型枚举，已按本地题型继续。",
            )
            return None

    def _apply_question_type_suggestion(
        self,
        operation_id: str,
        question: QuestionAnalysisInput,
        suggestion: QuestionTypeSuggestion | None,
        *,
        model_name: str,
    ) -> dict[str, Any] | None:
        """落库题型建议：未确认自动订正，已确认只登记冲突建议。"""

        if suggestion is None:
            return None
        local_type = str(question.tagging_context.question_type or "").strip()
        # 注意：save_tag_analysis 已先清掉非手工的 special_type 行。若模型给出
        # 与旧值相同的子类，这里不能按旧快照提前返回——否则刚被清掉的子类
        # 不会重新写入。子类已确定时一律交给写服务幂等落库（手工子类在写服务
        # 内按 existing_kept 保留）。
        if (
            suggestion.question_type == local_type
            and suggestion.essay_subtype is None
        ):
            return None
        audit: dict[str, Any] = {
            "local_type": local_type,
            "suggested_type": suggestion.question_type,
            "suggested_subtype": suggestion.essay_subtype,
            "reason": suggestion.reason,
            "model_name": str(model_name or ""),
            "action": "not_applied",
        }
        if question.question_type_confirmed:
            # AI 输出只是候选：教师已确认的题型绝不自动改，只登记冲突。
            audit["action"] = "conflict_only"
            return audit
        if self.question_type_writer is None:
            return audit
        try:
            applied = self.question_type_writer.apply(
                question,
                suggestion,
                model_name=model_name,
                operation_id=operation_id,
            )
        except Exception as exc:
            detail = _projection_error_detail(exc)
            self._record_projection_note(
                operation_id,
                question.question_id,
                "question_type_suggestion_note",
                f"题型建议订正写入失败：{detail}",
            )
            audit["action"] = "write_failed"
            return audit
        if isinstance(applied, Mapping):
            audit.update(dict(applied))
        return audit

    def _publish_saved_criterion(
        self,
        question: QuestionAnalysisInput,
    ) -> dict[str, Any]:
        """Publish from the current evidence version without another model call."""

        if self.criterion_module is None or self.evidence_writer is None:
            return {"status": "not_available"}
        load_current = getattr(self.evidence_writer, "load_current", None)
        if not callable(load_current):
            return {"status": "not_available"}
        try:
            evidence = load_current(question)
            if evidence is None:
                return {"status": "not_available"}
            draft = training_criteria_from_solution_evidence(
                evidence,
                question=question,
            )
            return self._publish_criterion_draft(
                question=question,
                draft=draft,
                actor_ref="model:stored-analysis",
                reason="从已保存解题证据发布训练判定点",
            )
        except Exception as exc:
            return {
                "status": "failed",
                "error_category": type(exc).__name__,
            }

    def _publish_criterion_draft(
        self,
        *,
        question: QuestionAnalysisInput,
        draft: TrainingCriteriaDraft,
        actor_ref: str,
        reason: str,
        evidence_review_required: bool = False,
        reference_conflict: bool = False,
    ) -> dict[str, Any]:
        if self.criterion_module is None:
            return {"status": "not_requested"}
        try:
            workspace = self.criterion_module.propose(
                question=question,
                draft=draft,
                source_kind="combined_model",
                source_reference=training_criterion_source_reference(
                    question.question_id,
                    draft,
                ),
                actor_ref=actor_ref,
                reason=reason,
            )
            current = (
                workspace.get("current_version")
                if isinstance(workspace, Mapping)
                else None
            )
            quality_status = (
                str(current.get("quality_status") or "")
                if isinstance(current, Mapping)
                else ""
            )
            quality_codes = (
                list(current.get("quality_codes") or [])
                if isinstance(current, Mapping)
                else []
            )
            needs_review = (
                quality_status != "passed"
                or evidence_review_required
                or reference_conflict
            )
            return {
                "status": "needs_review" if needs_review else "succeeded",
                "version_id": (
                    str(current.get("version_id") or "")
                    if isinstance(current, Mapping)
                    else ""
                ),
                "quality_codes": quality_codes,
                "review_reasons": [
                    review_reason
                    for review_reason, active in (
                        ("quality_gate", quality_status != "passed"),
                        ("objective_answer_conflict", evidence_review_required),
                        ("reference_conflict", reference_conflict),
                    )
                    if active
                ],
            }
        except Exception as exc:
            return {
                "status": "failed",
                "error_category": type(exc).__name__,
            }

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


def _append_answer_unit(
    units: list[tuple[str, tuple[str, ...]]],
    canonical: object,
    equivalents: object,
) -> None:
    value = str(canonical or "").strip()
    if not value:
        return
    accepted = tuple(
        item for item in _text_tuple(equivalents) if item != value
    )
    units.append((value, accepted))


def _objective_answer_units_from_mapping(
    answer: Mapping[str, Any],
    *,
    fallback: str,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    units: list[tuple[str, tuple[str, ...]]] = []
    raw_parts = answer.get("parts")
    if isinstance(raw_parts, list):
        for raw_part in raw_parts:
            if not isinstance(raw_part, Mapping):
                continue
            values = _text_tuple(raw_part.get("answer_values"))
            if values:
                for value in values:
                    _append_answer_unit(units, value, ())
                continue
            _append_answer_unit(
                units,
                _first_text(
                    raw_part,
                    "canonical_answer",
                    "answer",
                    "correct_answer",
                ),
                raw_part.get("accepted_forms")
                or raw_part.get("equivalent_answers"),
            )
    if not units:
        values = _text_tuple(answer.get("answer_values"))
        if values:
            for value in values:
                _append_answer_unit(units, value, ())
    if not units:
        _append_answer_unit(
            units,
            _first_text(
                answer,
                "canonical_answer",
                "answer",
                "correct_answer",
            ),
            answer.get("accepted_forms")
            or answer.get("equivalent_answers"),
        )
    if not units:
        _append_answer_unit(units, fallback, ())
    return tuple(units)


def _answer_only_training_draft(
    *,
    question: QuestionAnalysisInput,
    answer_units: Sequence[tuple[str, tuple[str, ...]]],
    rationale: str,
    confidence: float,
    source_kind: Literal["combined_model", "confirmed_rubric_adapter"],
    question_type_group: str | None = None,
) -> TrainingCriteriaDraft:
    multiple = len(answer_units) > 1
    points = tuple(
        TrainingCriterionPoint(
            point_id=(
                f"answer-unit-{index}"
                if multiple
                else "objective-answer"
            ),
            target=(
                f"给出第 {index} 个正确或等价答案"
                if multiple
                else "给出正确或等价答案"
            ),
            observable_evidence=canonical,
            equivalent_rules=equivalents,
            counterexamples=(),
        )
        for index, (canonical, equivalents) in enumerate(
            answer_units,
            start=1,
        )
    )
    return TrainingCriteriaDraft(
        schema_version="training-criteria-draft-v1",
        question_id=question.question_id,
        source_content_hash=question.criterion_source_content_hash,
        question_type=question_type_group or question.question_type_group,
        points=points,
        auxiliary_rules=(),
        rationale=rationale,
        confidence=confidence,
        source_kind=source_kind,
    )


def _judgment_points_draft(
    *,
    training_criteria: TrainingCriteriaDraft,
    evidence: QuestionSolutionEvidence,
) -> TrainingCriteriaDraft:
    _reject_score_fields(evidence.to_dict())
    return TrainingCriteriaDraft(
        schema_version=JUDGMENT_POINTS_SCHEMA,
        question_id=training_criteria.question_id,
        source_content_hash=training_criteria.source_content_hash,
        question_type=training_criteria.question_type,
        points=training_criteria.points,
        auxiliary_rules=training_criteria.auxiliary_rules,
        rationale=training_criteria.rationale,
        confidence=training_criteria.confidence,
        source_kind=training_criteria.source_kind,
        embedded_evidence_json=json.dumps(
            evidence.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def criteria_from_confirmed_rubric(
    *,
    question: QuestionAnalysisInput,
    rubric_question: Mapping[str, Any],
    answer_key: Mapping[str, Any] | None = None,
) -> TrainingCriteriaDraft:
    answer = dict(answer_key or {})
    if question.objective_response_shape in {
        "single_choice",
        "single_blank",
        "multiple_blank",
    }:
        answer_units = _objective_answer_units_from_mapping(
            answer,
            fallback=str(question.tagging_context.answer_text or "").strip(),
        )
        if question.objective_response_shape == "single_choice":
            answer_units = answer_units[:1]
        if not answer_units:
            raise ProjectionValidationError(
                "confirmed rubric cannot be converted without an answer"
            )
        return _answer_only_training_draft(
            question=question,
            answer_units=answer_units,
            rationale="由教师已确认的正式评分依据本地去分值转换。",
            confidence=1.0,
            source_kind="confirmed_rubric_adapter",
        )
    candidates: list[tuple[int | None, Mapping[str, Any]]] = []
    parts = rubric_question.get("parts")
    multi_part = isinstance(parts, list) and len(parts) > 1
    if isinstance(parts, list):
        for part_index, part in enumerate(parts, start=1):
            if not isinstance(part, Mapping):
                continue
            steps = part.get("steps")
            if isinstance(steps, list):
                candidates.extend(
                    (part_index, item)
                    for item in steps
                    if isinstance(item, Mapping)
                )
    if not candidates:
        steps = rubric_question.get("steps")
        if isinstance(steps, list):
            candidates.extend(
                (None, item) for item in steps if isinstance(item, Mapping)
            )
    points: list[TrainingCriterionPoint] = []
    for index, (part_index, step) in enumerate(candidates, start=1):
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
        step_id = str(step.get("step_id") or f"step-{index}")
        if multi_part and part_index is not None:
            # 多小问的 rubric 每个小问内 step_id 从 S1 重新编号，平铺后会撞号；
            # 带上小问序号使判定点编号与评分标准的小问结构一一对应且天然唯一。
            point_id = _safe_point_id(f"p{part_index}-{step_id}")
        else:
            point_id = _safe_point_id(step_id)
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
        source_content_hash=question.criterion_source_content_hash,
        question_type=question.question_type_group,
        points=tuple(points),
        auxiliary_rules=(),
        rationale="由教师已确认的正式评分依据本地去分值转换。",
        confidence=1.0,
        source_kind="confirmed_rubric_adapter",
    )


def training_criteria_from_solution_evidence(
    evidence: QuestionSolutionEvidence,
    *,
    question: QuestionAnalysisInput,
    objective_response_shape: ObjectiveResponseShape | None = None,
    question_type_group: str | None = None,
) -> TrainingCriteriaDraft:
    """Project rich evidence to the existing score-free criterion contract."""

    if evidence.question_id != question.question_id:
        raise ProjectionValidationError(
            "solution evidence belongs to another question"
        )
    if evidence.source_content_hash != solution_evidence_source_content_hash(
        question
    ):
        raise ProjectionValidationError("solution evidence source is stale")
    # 题型建议被采纳时用真实题组与作答形态投影，否则保持本地推导结果。
    response_shape = objective_response_shape or question.objective_response_shape
    type_group = question_type_group or question.question_type_group
    if response_shape in {"single_choice", "single_blank"}:
        canonical = next(
            (
                part.canonical_answer
                for part in evidence.parts
                if str(part.canonical_answer or "").strip()
            ),
            str(question.tagging_context.answer_text or "").strip(),
        )
        if not canonical:
            raise ProjectionValidationError(
                "objective evidence cannot be converted without an answer"
            )
        accepted_forms = tuple(
            dict.fromkeys(
                form
                for part in evidence.parts
                for form in part.accepted_forms
                if form != canonical
            )
        )
        return _judgment_points_draft(
            training_criteria=_answer_only_training_draft(
                question=question,
                answer_units=((canonical, accepted_forms),),
                rationale="客观题仅依据答案生成训练判定点。",
                confidence=evidence.confidence,
                source_kind="combined_model",
                question_type_group=type_group,
            ),
            evidence=evidence,
        )
    if response_shape == "multiple_blank":
        answer_units: list[tuple[str, tuple[str, ...]]] = []
        for part in evidence.parts:
            if part.response_mode == "exact_objective":
                _append_answer_unit(
                    answer_units,
                    part.canonical_answer,
                    part.accepted_forms,
                )
                continue
            anchors = [
                point.answer_anchor
                for point in part.evidence_points
                if str(point.answer_anchor or "").strip()
            ]
            if anchors:
                for anchor in anchors:
                    _append_answer_unit(answer_units, anchor, ())
            else:
                _append_answer_unit(answer_units, part.full_answer, ())
        if not answer_units:
            raise ProjectionValidationError(
                "fill-blank evidence cannot be converted without answers"
            )
        return _judgment_points_draft(
            training_criteria=_answer_only_training_draft(
                question=question,
                answer_units=tuple(answer_units),
                rationale="填空题仅依据各独立答案生成训练判定点。",
                confidence=evidence.confidence,
                source_kind="combined_model",
                question_type_group=type_group,
            ),
            evidence=evidence,
        )
    points = tuple(
        TrainingCriterionPoint(
            point_id=point.evidence_point_id,
            target=point.target,
            observable_evidence=point.observable_evidence,
            equivalent_rules=point.equivalent_rules,
            counterexamples=point.counterexamples,
            depends_on=point.depends_on,
        )
        for part in evidence.parts
        for point in part.evidence_points
    )
    return _judgment_points_draft(
        training_criteria=TrainingCriteriaDraft(
            schema_version=LEGACY_CRITERIA_SCHEMA,
            question_id=question.question_id,
            source_content_hash=question.criterion_source_content_hash,
            question_type=type_group,
            points=points,
            auxiliary_rules=evidence.auxiliary_rules,
            rationale=evidence.rationale,
            confidence=evidence.confidence,
            source_kind="combined_model",
        ),
        evidence=evidence,
    )


def training_criterion_source_reference(
    question_id: int,
    draft: TrainingCriteriaDraft,
) -> str:
    """Build the idempotency key shared by every criterion publisher."""

    return (
        "combined-analysis:"
        f"{int(question_id)}:{draft.source_content_hash}:"
        f"{_hash_payload(draft.to_dict())}"
    )


def solution_evidence_source_content_hash(
    question: QuestionAnalysisInput,
) -> str:
    """Hash portable question content without a database-local question id."""

    context = question.tagging_context
    return _hash_payload(
        {
            "question_text": context.question_text,
            "answer_text": context.answer_text,
            "question_type": context.question_type,
            "has_images": context.has_images,
            "rich_question_blocks": question.rich_question_blocks,
            "rich_answer_blocks": question.rich_answer_blocks,
            "image_hashes": [
                {
                    "role": image.role,
                    "mime_type": image.mime_type,
                    "sha256": image.sha256,
                }
                for image in question.images
            ],
        }
    )


def rubric_skeleton_from_solution_evidence(
    evidence: QuestionSolutionEvidence,
    *,
    question_ref: str | None = None,
) -> dict[str, Any]:
    """Build a rubric-shaped, deliberately unallocated scoring skeleton."""

    return {
        "schema_version": "solution-evidence-rubric-skeleton-v1",
        "question_id": str(question_ref or evidence.question_id),
        "source_evidence_version_id": evidence.version_id,
        "source_content_hash": evidence.source_content_hash,
        "parts": [
            {
                "part_id": part.part_id,
                "part_label": part.label,
                "response_mode": part.response_mode,
                "require_final_answer": bool(
                    part.canonical_answer or part.full_answer
                ),
                "allow_alternative_methods": part.allow_alternative_methods,
                "deduction_policy": list(part.deduction_policy),
                "proof_obligations": list(part.proof_obligations),
                "visual_requirements": list(part.visual_requirements),
                "steps": [
                    {
                        "step_id": point.evidence_point_id,
                        # evidence_point_ids 是该步覆盖的题库证据点；赋分合并步骤时
                        # 合并 id 列表，不得删除（§7.1 引用而非重写）。
                        "evidence_point_ids": [point.evidence_point_id],
                        # core_goal 是学生必须达成的数学目标；
                        # required_elements 只收可核验的作答成果，
                        # 参考解法说明与定位锚点留在答案侧 step_milestones，
                        # 不作为学生必写清单。
                        "core_goal": point.target,
                        "required_elements": list(
                            _text_tuple((point.observable_evidence,))
                        ),
                        "depends_on": list(point.depends_on),
                        "equivalent_rules": list(point.equivalent_rules),
                        "counterexamples": list(point.counterexamples),
                        # 步骤扣分规则只含该步反例；小问级通用扣分政策
                        # 保留在 part.deduction_policy，不复制到每一步。
                        "deduction_rules": list(point.counterexamples),
                    }
                    for point in part.evidence_points
                ],
            }
            for part in evidence.parts
        ],
        "allocation_status": "unassigned",
    }


def answer_key_skeleton_from_solution_evidence(
    evidence: QuestionSolutionEvidence,
    *,
    question_ref: str | None = None,
) -> dict[str, Any]:
    """Project the same evidence into a complete, score-free answer key."""

    return {
        "schema_version": "solution-evidence-answer-key-v1",
        "question_id": str(question_ref or evidence.question_id),
        "source_evidence_version_id": evidence.version_id,
        "source_content_hash": evidence.source_content_hash,
        "parts": [
            {
                "part_id": part.part_id,
                "answer": part.full_answer or part.canonical_answer,
                "canonical_answer": part.canonical_answer,
                "accepted_forms": list(part.accepted_forms),
                "analysis": part.full_answer,
                "step_milestones": [
                    {
                        "step_id": point.evidence_point_id,
                        "step_index": point.step_index,
                        "target": point.target,
                        "justification": point.justification,
                        "answer_anchor": point.answer_anchor,
                        "observable_evidence": point.observable_evidence,
                        "depends_on": list(point.depends_on),
                        "equivalent_rules": list(point.equivalent_rules),
                    }
                    for point in part.evidence_points
                ],
                "proof_obligations": list(part.proof_obligations),
                "visual_requirements": list(part.visual_requirements),
            }
            for part in evidence.parts
        ],
    }


def grading_config_skeleton_from_solution_evidence(
    evidence: QuestionSolutionEvidence,
    *,
    question_ref: str | None = None,
) -> dict[str, Any]:
    """Return the paired rubric/answer-key source for later whole-paper allocation."""

    return {
        "rubric_question": rubric_skeleton_from_solution_evidence(
            evidence,
            question_ref=question_ref,
        ),
        "answer_key_question": answer_key_skeleton_from_solution_evidence(
            evidence,
            question_ref=question_ref,
        ),
    }


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


_MAX_COMBINED_ENUM_IDS = 800
_CONTROLLED_DIMENSIONS = (
    "knowledge",
    "method",
    "thought",
    "ability",
    "model",
    "special_type",
    "curriculum",
)


def _ordered_unique_strings(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _candidate_ids_from_contract(
    contract: Mapping[str, Any],
    dimension: str,
) -> list[str]:
    candidates = contract.get("candidates")
    if not isinstance(candidates, Mapping):
        return []
    raw_items = candidates.get(dimension)
    if not isinstance(raw_items, Sequence) or isinstance(
        raw_items, (str, bytes, bytearray)
    ):
        return []
    ids: list[str] = []
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            continue
        usage = str(raw.get("usage") or "").strip()
        if usage == "do_not_use_as_knowledge":
            continue
        item_id = str(raw.get("id") or "").strip()
        if item_id:
            ids.append(item_id)
    return ids


def _section_ids_from_contract(contract: Mapping[str, Any]) -> list[str]:
    volume = contract.get("curriculum_volume")
    if not isinstance(volume, Mapping):
        return []
    sections = volume.get("sections")
    if not isinstance(sections, Sequence) or isinstance(
        sections, (str, bytes, bytearray)
    ):
        return []
    return _ordered_unique_strings(
        item.get("id")
        for item in sections
        if isinstance(item, Mapping)
    )


def controlled_term_ids_from_questions(
    questions: Sequence[QuestionAnalysisInput],
) -> dict[str, tuple[str, ...]]:
    buckets: dict[str, list[str]] = {
        dimension: [] for dimension in _CONTROLLED_DIMENSIONS
    }
    buckets["curriculum_sections"] = []
    seen = {key: set() for key in buckets}
    for question in questions:
        contract = question.taxonomy_contract
        if not isinstance(contract, Mapping):
            continue
        for dimension in _CONTROLLED_DIMENSIONS:
            for item_id in _candidate_ids_from_contract(contract, dimension):
                if item_id in seen[dimension]:
                    continue
                seen[dimension].add(item_id)
                buckets[dimension].append(item_id)
        for item_id in _section_ids_from_contract(contract):
            if item_id in seen["curriculum_sections"]:
                continue
            seen["curriculum_sections"].add(item_id)
            buckets["curriculum_sections"].append(item_id)
    return {key: tuple(values) for key, values in buckets.items()}


def _enum_string_schema(
    ids: Sequence[str],
    *,
    include_empty: bool = False,
) -> dict[str, Any]:
    values = _ordered_unique_strings(ids)
    bounded = len(values)
    if include_empty:
        values = ["", *values]
    if bounded == 0 or bounded > _MAX_COMBINED_ENUM_IDS:
        return {"type": "string"}
    return {"type": "string", "enum": values}


def _enum_ref(
    defs: dict[str, Any],
    name: str,
    ids: Sequence[str],
    *,
    include_empty: bool = False,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    schema = _enum_string_schema(ids, include_empty=include_empty)
    if "enum" not in schema:
        return dict(fallback or schema)
    defs.setdefault(name, schema)
    return {"$ref": f"#/$defs/{name}"}


def _enum_array_schema(
    ids: Sequence[str],
    *,
    defs: dict[str, Any] | None = None,
    ref_name: str = "",
) -> dict[str, Any]:
    if defs is not None and ref_name:
        item = _enum_ref(defs, ref_name, ids)
        if "$ref" not in item:
            return {"type": "array", "items": {"type": "string"}}
        return {"type": "array", "items": item}
    item = _enum_string_schema(ids)
    if "enum" not in item:
        return {"type": "array", "items": {"type": "string"}}
    return {"type": "array", "items": item}


def combined_response_format(
    projection: AnalysisProjection = "both",
    *,
    allowed_term_ids: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    selected = _selected_projections(_projection(projection))
    ids = {
        str(key): tuple(str(item or "").strip() for item in values if str(item or "").strip())
        for key, values in dict(allowed_term_ids or {}).items()
    }
    defs: dict[str, Any] = {}
    item_properties: dict[str, Any] = {
        "question_id": {"type": "integer"},
        "reference_assessment": {
            "type": "string",
            "enum": ["consistent", "conflict", "insufficient"],
        },
        "reference_assessment_reason": {"type": "string"},
        "question_type_suggestion": {
            "type": "object",
            "properties": {
                "question_type": {
                    "type": "string",
                    "enum": list(QUESTION_TYPES),
                },
                "reason": {"type": "string"},
                "essay_subtype": {
                    "type": ["string", "null"],
                    "enum": [*ESSAY_SUBTYPES, None],
                },
            },
            "required": ["question_type", "reason"],
            "additionalProperties": False,
        },
    }
    if "tag" in selected:
        item_properties["tag_analysis"] = _tag_schema(ids, defs=defs)
    if "training_criteria" in selected:
        item_properties["solution_evidence"] = _solution_evidence_schema(
            knowledge_ids=ids.get("knowledge") or (),
            defs=defs,
        )
    schema: dict[str, Any] = {"type": "object"}
    if defs:
        schema["$defs"] = defs
    schema.update(
        {
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
        }
    )
    return {
        "type": "json_schema",
        "name": (
            "question_bank_combined_analysis_v3"
            if projection == "both"
            else f"question_bank_{projection}_projection_v3"
        ),
        "strict": True,
        "schema": schema,
    }


def _tag_schema(
    allowed_term_ids: Mapping[str, Sequence[str]] | None = None,
    *,
    defs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ids = dict(allowed_term_ids or {})
    shared_defs = defs if defs is not None else {}
    text = {"type": "string"}
    ability_schema = _enum_array_schema(
        ids.get("ability") or (),
        defs=shared_defs,
        ref_name="ability_id",
    )
    if ability_schema.get("type") == "array":
        ability_schema["maxItems"] = MAX_ABILITY_TAGS
    properties: dict[str, Any] = {
        "method_tags": _enum_array_schema(
            ids.get("method") or (),
            defs=shared_defs,
            ref_name="method_id",
        ),
        "thought_tags": _enum_array_schema(
            ids.get("thought") or (),
            defs=shared_defs,
            ref_name="thought_id",
        ),
        "ability_tags": ability_schema,
        "math_model_tags": _enum_array_schema(
            ids.get("model") or (),
            defs=shared_defs,
            ref_name="model_id",
        ),
        "special_type_tags": _enum_array_schema(
            ids.get("special_type") or (),
            defs=shared_defs,
            ref_name="special_type_id",
        ),
        "difficulty": {"type": "integer", "minimum": 1, "maximum": 10},
        "predicted_error_patterns": _predicted_error_pattern_schema(),
        "part_features": _part_features_schema(),
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


def _predicted_error_pattern_schema() -> dict[str, Any]:
    categories = list(predicted_pattern_categories())
    properties = {
        "category": (
            {"type": "string", "enum": categories}
            if categories
            else {"type": "string"}
        ),
        "pattern": {"type": "string"},
        "explanation": {"type": "string"},
        "trigger_kind": {
            "type": "string",
            "enum": list(PREDICTED_TRIGGER_KINDS),
        },
        "trigger_value": {"type": "string"},
    }
    return {
        "type": "array",
        "maxItems": PREDICTED_PATTERN_MAX,
        "items": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


def _part_features_schema() -> dict[str, Any]:
    def ranged(name: str) -> dict[str, Any]:
        minimum, maximum = PART_FEATURE_RANGES[name]
        return {"type": "integer", "minimum": minimum, "maximum": maximum}

    properties: dict[str, Any] = {
        "part_id": {"type": "string"},
        "part_label": {"type": "string"},
        **{name: ranged(name) for name in PART_FEATURE_ORDER},
        "context_kind": {
            "type": "string",
            "enum": list(PART_CONTEXT_KINDS),
        },
        "evidence": {"type": "string"},
    }
    return {
        "type": "array",
        "minItems": 1,
        "maxItems": PART_FEATURES_MAX_PARTS,
        "items": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
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
        "maxItems": 1,
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


def _solution_evidence_schema(
    *,
    knowledge_ids: Sequence[str] = (),
    defs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text_array = {"type": "array", "items": {"type": "string"}}
    non_empty_text = {"type": "string", "minLength": 1}
    machine_identifier = {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_-]{1,127}$",
    }
    shared_defs = defs if defs is not None else {}
    fine_term_id = _enum_ref(
        shared_defs,
        "knowledge_id",
        knowledge_ids,
        fallback=non_empty_text,
    )
    fine_term_properties = {
        "fine_term_id": fine_term_id,
        "fine_term_name": non_empty_text,
        "role": {
            "type": "string",
            "enum": ["direct", "supporting_prerequisite"],
        },
    }
    fine_term_link = {
        "type": "object",
        "properties": fine_term_properties,
        "required": list(fine_term_properties),
        "additionalProperties": False,
    }
    evidence_point_properties = {
        "evidence_point_id": machine_identifier,
        "step_index": {"type": "integer", "minimum": 1},
        "target": non_empty_text,
        "justification": non_empty_text,
        "answer_anchor": non_empty_text,
        "observable_evidence": non_empty_text,
        "depends_on": {
            # OpenAI 兼容的 json_schema 不允许 uniqueItems；去重在本机写入时校验。
            "type": "array",
            "items": machine_identifier,
        },
        "fine_term_links": {
            "type": "array",
            "items": fine_term_link,
        },
        "equivalent_rules": text_array,
        "counterexamples": text_array,
    }
    evidence_point = {
        "type": "object",
        "properties": evidence_point_properties,
        "required": list(evidence_point_properties),
        "additionalProperties": False,
    }
    part_properties = {
        "part_id": machine_identifier,
        "label": {"type": "string"},
        "response_mode": {
            "type": "string",
            "enum": [
                "exact_objective",
                "short_answer_points",
                "process_required",
                "visual_construction",
            ],
        },
        "canonical_answer": {"type": "string"},
        "accepted_forms": text_array,
        "full_answer": {"type": "string"},
        "proof_obligations": text_array,
        "visual_requirements": text_array,
        "deduction_policy": {
            "type": "array",
            "minItems": 1,
            "items": non_empty_text,
        },
        "allow_alternative_methods": {"type": "boolean"},
        "evidence_points": {
            "type": "array",
            "minItems": 1,
            "items": evidence_point,
        },
    }
    part = {
        "type": "object",
        "properties": part_properties,
        "required": list(part_properties),
        "additionalProperties": False,
    }
    properties = {
        "schema_version": {
            "type": "string",
            "enum": ["question-solution-evidence-v2"],
        },
        "question_id": {"type": "integer"},
        "parts": {"type": "array", "minItems": 1, "items": part},
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
) -> tuple[dict[int, Mapping[str, Any]], dict[int, str]]:
    raw = payload.get("results")
    if not isinstance(raw, list):
        raise ProjectionValidationError("combined response has no results")
    expected = set(batch.question_ids)
    grouped: dict[int, list[Mapping[str, Any]]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise ProjectionValidationError("combined response item is invalid")
        try:
            question_id = int(item["question_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProjectionValidationError(
                "combined response question_id is invalid"
            ) from exc
        if question_id not in expected:
            raise ProjectionValidationError(
                "combined response has unknown or duplicate question_id"
            )
        grouped.setdefault(question_id, []).append(item)
    items: dict[int, Mapping[str, Any]] = {}
    merge_notes: dict[int, str] = {}
    for question_id, group in grouped.items():
        if len(group) == 1:
            items[question_id] = group[0]
            continue
        items[question_id], merge_notes[question_id] = (
            _merge_duplicate_results(group)
        )
    return items, merge_notes


class _DuplicateMergeUnsafeError(Exception):
    """A duplicate result copy cannot be merged deterministically."""


_TAG_ANALYSIS_LIST_FIELDS = (
    "method_tags",
    "thought_tags",
    "ability_tags",
    "math_model_tags",
    "special_type_tags",
    "predicted_error_patterns",
    "part_features",
    "proposed_tags",
)
_REFERENCE_ASSESSMENT_SEVERITY = {
    "consistent": 0,
    "insufficient": 1,
    "conflict": 2,
}


def _ordered_unique_values(values: Iterable[Any]) -> list[Any]:
    seen: set[str] = set()
    unique: list[Any] = []
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def _merge_duplicate_results(
    group: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], str]:
    """Merge duplicate per-question results into one deterministic result.

    多问大题最常见的模型误返回是按小问各给一份 result。能确定性合并时
    合并为一条；任一合并前提不满足时回退为保留第一份，都不再整批作废。
    """

    first = group[0]
    try:
        merged = _try_merge_duplicate_results(group)
    except _DuplicateMergeUnsafeError:
        note = (
            f"模型返回 {len(group)} 份重复结果，结构不满足合并前提，"
            "已保留第一份。"
        )
        return first, note
    note = f"模型返回 {len(group)} 份重复结果，已按返回顺序合并为一条。"
    return merged, note


def _try_merge_duplicate_results(
    group: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    merged = dict(group[0])
    analyses = [
        item["tag_analysis"]
        for item in group
        if isinstance(item.get("tag_analysis"), Mapping)
    ]
    if analyses:
        merged["tag_analysis"] = _merge_tag_analyses(analyses)
    evidence_copies = [
        item["solution_evidence"]
        for item in group
        if isinstance(item.get("solution_evidence"), Mapping)
    ]
    if evidence_copies:
        merged["solution_evidence"] = _merge_solution_evidences(evidence_copies)
    assessments = [
        str(item.get("reference_assessment") or "").strip()
        for item in group
    ]
    if any(assessments):
        merged["reference_assessment"] = max(
            assessments,
            key=lambda value: _REFERENCE_ASSESSMENT_SEVERITY.get(
                value.casefold(),
                len(_REFERENCE_ASSESSMENT_SEVERITY),
            ),
        )
    reasons = _ordered_unique_values(
        [
            str(item.get("reference_assessment_reason") or "").strip()
            for item in group
            if str(item.get("reference_assessment_reason") or "").strip()
        ]
    )
    if reasons:
        merged["reference_assessment_reason"] = "；".join(
            str(reason) for reason in reasons
        )
    return merged


def _merge_tag_analyses(
    analyses: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """列表字段保序去重并集，标量字段以第一份为准。"""

    merged = dict(analyses[0])
    for field_name in _TAG_ANALYSIS_LIST_FIELDS:
        values: list[Any] = []
        present = False
        for analysis in analyses:
            raw_values = analysis.get(field_name)
            if isinstance(raw_values, list):
                present = True
                values.extend(raw_values)
        if present:
            merged[field_name] = _ordered_unique_values(values)
    return merged


def _merge_solution_evidences(
    copies: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """按返回顺序拼接 parts，并把 part/evidence point 编号重编为全题唯一。"""

    merged = dict(copies[0])
    merged_parts: list[dict[str, Any]] = []
    for copy in copies:
        parts = copy.get("parts")
        if not isinstance(parts, list) or not parts:
            raise _DuplicateMergeUnsafeError("solution evidence parts are empty")
        for part in parts:
            merged_parts.append(
                _renumber_evidence_part(part, f"part-{len(merged_parts) + 1}")
            )
    auxiliary_rules: list[Any] = []
    rules_present = False
    for copy in copies:
        raw_rules = copy.get("auxiliary_rules")
        if isinstance(raw_rules, list):
            rules_present = True
            auxiliary_rules.extend(raw_rules)
    if rules_present:
        merged["auxiliary_rules"] = _ordered_unique_values(auxiliary_rules)
    merged["parts"] = merged_parts
    return merged


def _renumber_evidence_part(
    part: object,
    new_part_id: str,
) -> dict[str, Any]:
    if not isinstance(part, Mapping):
        raise _DuplicateMergeUnsafeError("evidence part is invalid")
    points = part.get("evidence_points")
    if not isinstance(points, list) or not points:
        raise _DuplicateMergeUnsafeError("evidence part has no evidence_points")
    id_map: dict[str, str] = {}
    for index, point in enumerate(points, start=1):
        if not isinstance(point, Mapping):
            raise _DuplicateMergeUnsafeError("evidence point is invalid")
        old_id = str(point.get("evidence_point_id") or "").strip()
        if not old_id or old_id in id_map:
            raise _DuplicateMergeUnsafeError("evidence point id is not unique")
        id_map[old_id] = f"{new_part_id}-step-{index}"
    renumbered_points: list[dict[str, Any]] = []
    for point in points:
        assert isinstance(point, Mapping)
        renumbered = dict(point)
        old_id = str(point.get("evidence_point_id") or "").strip()
        renumbered["evidence_point_id"] = id_map[old_id]
        raw_depends = point.get("depends_on")
        depends = raw_depends if isinstance(raw_depends, list) else []
        renumbered_depends: list[str] = []
        for dependency in depends:
            dependency_id = str(dependency or "").strip()
            if not dependency_id:
                continue
            # depends_on 是 part 内命名空间；引用不到本 part 的 id 就无法安全重编。
            target = id_map.get(dependency_id)
            if target is None:
                raise _DuplicateMergeUnsafeError(
                    "depends_on reference cannot be renumbered"
                )
            renumbered_depends.append(target)
        renumbered["depends_on"] = renumbered_depends
        renumbered_points.append(renumbered)
    renumbered_part = dict(part)
    renumbered_part["part_id"] = new_part_id
    renumbered_part["evidence_points"] = renumbered_points
    return renumbered_part


def _projection_error_detail(exc: BaseException) -> str:
    """脱敏后的异常类型+短消息，供台账与任务结果展示。"""

    from question_bank.services.ai_tagging_service import (
        sanitize_tagging_error,
    )

    return sanitize_tagging_error(f"{type(exc).__name__}: {exc}", limit=240)


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
            "contract": "combined-v3",
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


def gateway_parallel_limit(gateway: QuestionAnalysisGateway) -> int:
    """Use the channel parallelism, bounded to 1..100 for every analysis entry."""
    value = getattr(gateway, "max_parallel_requests", 1)
    try:
        return max(1, min(100, int(value)))
    except (TypeError, ValueError, OverflowError):
        return 1


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
                "repair_context": question.repair_context,
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
    status_code = getattr(exc, "status_code", None)
    if not isinstance(status_code, int):
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
    if status_code in {401, 403}:
        return "authentication"
    if status_code == 429:
        return "rate_limit"
    if isinstance(status_code, int) and 400 <= status_code < 500:
        return "invalid_request"
    text = f"{type(exc).__name__} {exc}".casefold()
    if "rate limit" in text or "ratelimit" in text or "requestbursttoofast" in text:
        return "rate_limit"
    if "timeout" in text:
        return "timeout"
    if "json" in text or "parse" in text:
        return "parse"
    if "validation" in text or "schema" in text or "result" in text:
        return "validation"
    if "cancel" in text:
        return "cancelled"
    return "model"


def _stops_batch_scheduling(category: str) -> bool:
    return category in {
        "authentication",
        "cancelled",
        "invalid_request",
        "parameter_incompatible",
    }


def _gateway_feedback_limit(gateway: object) -> int:
    """Local repair-retry budget carried by the gateway (0 = never re-ask)."""
    value = getattr(gateway, "max_auto_retries", 0)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, min(5, value))


def _repair_previous_result(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Bound the previous model payload handed back as repair feedback."""
    try:
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {}
    if len(serialized) <= 40_000:
        return dict(payload)
    return {"raw_output": serialized[:8_000]}


def _raw_text_previous_result(raw_text: str) -> Mapping[str, Any]:
    text = str(raw_text or "").strip()
    if not text:
        return {}
    return {"raw_output": text[:8_000]}


def _question_with_repair_context(
    question: QuestionAnalysisInput,
    *,
    validation_error: str,
    previous_result: Mapping[str, Any],
) -> QuestionAnalysisInput:
    return dataclass_replace(
        question,
        repair_context={
            "mode": "repair_previous_rejected_result",
            "validation_error": validation_error,
            "previous_result": previous_result,
        },
    )


@dataclass(frozen=True, slots=True)
class _BatchAttempt:
    """One in-flight batch plus its remaining local repair budget."""

    batch: PlannedAnalysisBatch
    request_id: str
    feedback_left: int
    question_count: int
    # None = initial/whole-shape attempt (save every selected projection);
    # otherwise only the listed projections are (re)saved per question, so a
    # repair response that omits sibling projections cannot overwrite
    # already-succeeded work.
    repair_scopes: Mapping[int, frozenset[str]] | None = None


def _hash_payload(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _child_operation_id(
    operation_id: str,
    projection: AnalysisProjection,
) -> str:
    if projection == "both":
        return operation_id
    if projection == "tag":
        return f"{operation_id}:tag"
    return f"{operation_id}:evidence"


def _writer_audit_summary(
    writer: object,
    operation_id: str,
    question_ids: Sequence[int],
) -> Mapping[str, Any]:
    method = getattr(writer, "audit_summary", None)
    if not callable(method):
        return {}
    result = method(operation_id, question_ids)
    return dict(result) if isinstance(result, Mapping) else {}


def _merge_projection_audits(
    *audits: Mapping[str, Any],
) -> dict[str, Any]:
    list_fields = (
        "retrieval_misses",
        "proposals",
        "secondary_matches",
    )
    merged: dict[str, list[Any]] = {field_name: [] for field_name in list_fields}
    seen: dict[str, set[str]] = {field_name: set() for field_name in list_fields}
    for audit in audits:
        for field_name in list_fields:
            for item in audit.get(field_name, []):
                if not isinstance(item, Mapping):
                    continue
                signature = _hash_payload(dict(item))
                if signature in seen[field_name]:
                    continue
                seen[field_name].add(signature)
                merged[field_name].append(dict(item))
    merged["retrieval_miss_question_ids"] = sorted(
        {
            int(question_id)
            for audit in audits
            for question_id in audit.get("retrieval_miss_question_ids", [])
            if int(question_id) > 0
        }
    )
    merged["proposal_question_ids"] = sorted(
        {
            int(question_id)
            for audit in audits
            for question_id in audit.get("proposal_question_ids", [])
            if int(question_id) > 0
        }
    )
    return merged


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
    "QuestionAnalysisWorkItem",
    "QuestionTypeSuggestion",
    "QuestionTypeSuggestionWriter",
    "TagOnlyV1ResultAdapter",
    "TagProjectionWriter",
    "TrainingCriteriaDraft",
    "TrainingCriterionPoint",
    "combined_response_format",
    "criteria_from_confirmed_rubric",
    "answer_key_skeleton_from_solution_evidence",
    "grading_config_skeleton_from_solution_evidence",
    "rubric_skeleton_from_solution_evidence",
    "solution_evidence_source_content_hash",
    "training_criteria_from_solution_evidence",
    "training_criterion_source_reference",
    "plan_analysis_batches",
    "gateway_parallel_limit",
]
