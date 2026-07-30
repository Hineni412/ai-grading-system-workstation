from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol


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

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["usage"] = asdict(self.usage)
        payload["issue_codes"] = list(self.issue_codes)
        payload["questions"] = [dict(item) for item in self.questions]
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
