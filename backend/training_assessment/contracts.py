from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping, Protocol


POINT_STATES = frozenset({"met", "not_met", "uncertain", "unreadable"})


@dataclass(frozen=True, slots=True)
class AssessmentUsage:
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
class AssessmentPage:
    page_number: int
    sha256: str
    mime_type: str
    content: bytes

    def __post_init__(self) -> None:
        if int(self.page_number) < 1:
            raise ValueError("page_number must be positive")
        expected = str(self.sha256 or "").strip().casefold()
        if hashlib.sha256(bytes(self.content)).hexdigest() != expected:
            raise ValueError("assessment page hash mismatch")
        if self.mime_type not in {"image/png", "image/jpeg"}:
            raise ValueError("unsupported assessment page type")
        object.__setattr__(self, "page_number", int(self.page_number))
        object.__setattr__(self, "sha256", expected)
        object.__setattr__(self, "content", bytes(self.content))

    def data_url(self) -> str:
        encoded = base64.b64encode(self.content).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"


@dataclass(frozen=True, slots=True)
class AssessmentItem:
    item_order: int
    task_item_code: str
    criterion_version_id: str
    criterion_hash: str
    question: Mapping[str, Any]
    answer: Mapping[str, Any]
    points: tuple[Mapping[str, Any], ...]
    auxiliary_rules: tuple[str, ...] = ()

    @property
    def point_ids(self) -> tuple[str, ...]:
        return tuple(str(point["point_id"]) for point in self.points)


@dataclass(frozen=True, slots=True)
class TrainingAssessmentRequest:
    submission_id: str
    submission_revision: int
    paper_instance_id: str
    items: tuple[AssessmentItem, ...]
    pages: tuple[AssessmentPage, ...]

    @property
    def expected_point_count(self) -> int:
        return sum(len(item.points) for item in self.items)


@dataclass(frozen=True, slots=True)
class ModelPointResult:
    task_item_code: str
    point_id: str
    state: str
    evidence: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ModelPointResult":
        return cls(
            task_item_code=str(payload.get("task_item_code") or "").strip(),
            point_id=str(payload.get("point_id") or "").strip(),
            state=str(payload.get("state") or "").strip().casefold(),
            evidence=str(payload.get("evidence") or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class AssessmentGatewayResponse:
    results: tuple[ModelPointResult, ...]
    model_name: str
    usage: AssessmentUsage = AssessmentUsage()
    latency_ms: int = 0

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        model_name: str,
        usage: AssessmentUsage | None = None,
        latency_ms: int = 0,
    ) -> "AssessmentGatewayResponse":
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise ValueError("training assessment results must be an array")
        results: list[ModelPointResult] = []
        for raw in raw_results:
            if not isinstance(raw, Mapping):
                raise ValueError(
                    "training assessment result must be an object"
                )
            results.append(ModelPointResult.from_mapping(raw))
        clean_model = str(model_name or "").strip()
        if not clean_model:
            raise ValueError("model_name must not be empty")
        clean_latency = int(latency_ms)
        if clean_latency < 0:
            raise ValueError("latency_ms must be nonnegative")
        return cls(
            results=tuple(results),
            model_name=clean_model,
            usage=usage or AssessmentUsage(),
            latency_ms=clean_latency,
        )


class TrainingAssessmentGateway(Protocol):
    model_name: str

    def assess(
        self,
        request: TrainingAssessmentRequest,
        *,
        operation_id: str,
        request_id: str,
    ) -> AssessmentGatewayResponse:
        ...


@dataclass(frozen=True, slots=True)
class ReviewPointCommand:
    operation_token: str
    expected_review_revision: int
    task_item_code: str
    point_id: str
    final_state: str
    teacher_evidence: str
    teacher_reason: str
    actor_ref: str

    def __post_init__(self) -> None:
        token = _operation_token(self.operation_token)
        revision = int(self.expected_review_revision)
        if revision < 1:
            raise ValueError("expected_review_revision must be positive")
        task_item_code = str(self.task_item_code or "").strip()
        point_id = str(self.point_id or "").strip()
        state = str(self.final_state or "").strip().casefold()
        evidence = str(self.teacher_evidence or "").strip()
        reason = str(self.teacher_reason or "").strip()
        actor_ref = str(self.actor_ref or "").strip()
        if not task_item_code or not point_id:
            raise ValueError("task item and point identity must not be empty")
        if state not in POINT_STATES:
            raise ValueError("unsupported final point state")
        if not evidence or len(evidence) > 500:
            raise ValueError("teacher evidence must be 1-500 characters")
        if not reason or len(reason) > 500:
            raise ValueError("teacher reason must be 1-500 characters")
        if not actor_ref:
            raise ValueError("actor_ref must not be empty")
        object.__setattr__(self, "operation_token", token)
        object.__setattr__(self, "expected_review_revision", revision)
        object.__setattr__(self, "task_item_code", task_item_code)
        object.__setattr__(self, "point_id", point_id)
        object.__setattr__(self, "final_state", state)
        object.__setattr__(self, "teacher_evidence", evidence)
        object.__setattr__(self, "teacher_reason", reason)
        object.__setattr__(self, "actor_ref", actor_ref)


@dataclass(frozen=True, slots=True)
class AssessmentActionCommand:
    operation_token: str
    expected_review_revision: int
    actor_ref: str
    reason: str

    def __post_init__(self) -> None:
        token = _operation_token(self.operation_token)
        revision = int(self.expected_review_revision)
        actor_ref = str(self.actor_ref or "").strip()
        reason = str(self.reason or "").strip()
        if revision < 1:
            raise ValueError("expected_review_revision must be positive")
        if not actor_ref:
            raise ValueError("actor_ref must not be empty")
        if not reason or len(reason) > 500:
            raise ValueError("reason must be 1-500 characters")
        object.__setattr__(self, "operation_token", token)
        object.__setattr__(self, "expected_review_revision", revision)
        object.__setattr__(self, "actor_ref", actor_ref)
        object.__setattr__(self, "reason", reason)


@dataclass(frozen=True, slots=True)
class EvidenceSyncCommand:
    operation_token: str
    expected_review_revision: int
    action: Literal["publish", "withdraw"]
    actor_ref: str
    reason: str

    def __post_init__(self) -> None:
        token = _operation_token(self.operation_token)
        revision = int(self.expected_review_revision)
        action = str(self.action or "").strip().casefold()
        actor_ref = str(self.actor_ref or "").strip()
        reason = str(self.reason or "").strip()
        if revision < 1:
            raise ValueError("expected_review_revision must be positive")
        if action not in {"publish", "withdraw"}:
            raise ValueError("evidence action must be publish or withdraw")
        if not actor_ref:
            raise ValueError("actor_ref must not be empty")
        if not reason or len(reason) > 500:
            raise ValueError("reason must be 1-500 characters")
        object.__setattr__(self, "operation_token", token)
        object.__setattr__(self, "expected_review_revision", revision)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "actor_ref", actor_ref)
        object.__setattr__(self, "reason", reason)


class TrainingEvidenceSink(Protocol):
    def deliver(self, payload: Mapping[str, Any]) -> None:
        ...


@dataclass(frozen=True, slots=True)
class TrainingPaperOutcome:
    run_id: str
    submission_id: str
    submission_revision: int
    status: str
    request_count: int
    expected_question_count: int
    expected_point_count: int
    model_name: str | None
    usage: AssessmentUsage
    latency_ms: int
    issue_codes: tuple[str, ...]
    error_code: str | None
    questions: tuple[Mapping[str, Any], ...]
    review_revision: int = 1
    control_state: str = "active"
    workflow_status: str = "pending"
    action_message: str = ""
    attempts: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["usage"] = asdict(self.usage)
        payload["issue_codes"] = list(self.issue_codes)
        payload["questions"] = [dict(item) for item in self.questions]
        payload["attempts"] = [dict(item) for item in self.attempts]
        return payload


def assessment_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": "training_assessment_v1",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["results"],
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "task_item_code",
                            "point_id",
                            "state",
                            "evidence",
                        ],
                        "properties": {
                            "task_item_code": {"type": "string"},
                            "point_id": {"type": "string"},
                            "state": {
                                "type": "string",
                                "enum": sorted(POINT_STATES),
                            },
                            "evidence": {
                                "type": "string",
                                "maxLength": 500,
                            },
                        },
                    },
                }
            },
        },
    }


def stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _operation_token(value: object) -> str:
    clean = str(value or "").strip().casefold()
    if len(clean) != 32 or any(
        char not in "0123456789abcdef" for char in clean
    ):
        raise ValueError(
            "operation_token must be a 32-character hex digest"
        )
    return clean
