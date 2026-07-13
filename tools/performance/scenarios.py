from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from httpx import Response

from tools.performance.dataset import BenchmarkDataset


@dataclass(frozen=True, slots=True, repr=False)
class ScenarioRequest:
    path: str
    params: tuple[tuple[str, str], ...] = ()
    json_body: dict[str, object] | None = None


@dataclass(frozen=True, slots=True, repr=False)
class BenchmarkScenario:
    name: str
    method: str
    route_template: str
    build_request: Callable[[BenchmarkDataset], ScenarioRequest]
    count_records: Callable[[Response], int]
    scale_driver: str


def _request(path: str) -> Callable[[BenchmarkDataset], ScenarioRequest]:
    return lambda _dataset: ScenarioRequest(path)


def _single(_response: Response) -> int:
    return 1


def _collection(key: str) -> Callable[[Response], int]:
    def count(response: Response) -> int:
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
            raise ValueError("response collection is missing")
        return len(payload[key])

    return count


def _training_body() -> dict[str, object]:
    return {
        "scope": {"mode": "class", "class_id": "CLASS-001"},
        "exam_scope": {"mode": "cross_exam"},
    }


def build_scenarios(
    dataset: BenchmarkDataset,
) -> tuple[BenchmarkScenario, ...]:
    question_id = dataset.representative_question_id
    task_id = dataset.representative_task_id
    training_body = _training_body()
    scenarios = (
        BenchmarkScenario(
            "health",
            "GET",
            "/api/healthz",
            _request("/api/healthz"),
            _single,
            "constant",
        ),
        BenchmarkScenario(
            "question_bank.papers",
            "GET",
            "/api/question-bank/papers",
            _request("/api/question-bank/papers"),
            _collection("items"),
            "papers",
        ),
        BenchmarkScenario(
            "question_bank.questions.default",
            "GET",
            "/api/question-bank/questions",
            _request("/api/question-bank/questions"),
            _collection("items"),
            "questions",
        ),
        BenchmarkScenario(
            "question_bank.questions.filtered",
            "GET",
            "/api/question-bank/questions",
            lambda _dataset: ScenarioRequest(
                "/api/question-bank/questions",
                params=(
                    ("knowledge_point", "knowledge-01"),
                    ("tag_status", "tagged"),
                    ("sort", "difficulty"),
                    ("page_size", "100"),
                ),
            ),
            _collection("items"),
            "questions",
        ),
        BenchmarkScenario(
            "question_bank.question.detail",
            "GET",
            "/api/question-bank/questions/{question_id}",
            lambda _dataset: ScenarioRequest(
                f"/api/question-bank/questions/{question_id}"
            ),
            _single,
            "questions",
        ),
        BenchmarkScenario(
            "question_bank.question.asset",
            "GET",
            "/api/question-bank/questions/{question_id}/assets/{asset_index}",
            lambda _dataset: ScenarioRequest(
                f"/api/question-bank/questions/{question_id}/assets/0"
            ),
            _single,
            "generated_asset_bytes",
        ),
        BenchmarkScenario(
            "question_bank.question.preview",
            "GET",
            "/api/question-bank/questions/{question_id}/previews/{preview_type}",
            lambda _dataset: ScenarioRequest(
                f"/api/question-bank/questions/{question_id}/previews/question"
            ),
            _single,
            "generated_asset_bytes",
        ),
        BenchmarkScenario(
            "training.diagnosis",
            "POST",
            "/api/training/diagnosis",
            lambda _dataset: ScenarioRequest(
                "/api/training/diagnosis",
                json_body=dict(training_body),
            ),
            _collection("students"),
            "students",
        ),
        BenchmarkScenario(
            "training.plan.preview",
            "POST",
            "/api/training/plans/preview",
            lambda _dataset: ScenarioRequest(
                "/api/training/plans/preview",
                json_body=dict(training_body),
            ),
            lambda response: _nested_collection(response, "plan", "variants"),
            "students",
        ),
        BenchmarkScenario(
            "training.tasks",
            "GET",
            "/api/training/tasks",
            lambda _dataset: ScenarioRequest(
                "/api/training/tasks",
                params=(("page_size", "100"),),
            ),
            _collection("items"),
            "training_tasks",
        ),
        BenchmarkScenario(
            "training.task.detail",
            "GET",
            "/api/training/tasks/{task_id}",
            lambda _dataset: ScenarioRequest(f"/api/training/tasks/{task_id}"),
            _single,
            "training_tasks",
        ),
        BenchmarkScenario(
            "graph.profiles",
            "POST",
            "/api/graph/profiles",
            lambda _dataset: ScenarioRequest(
                "/api/graph/profiles",
                json_body=dict(training_body),
            ),
            _collection("students"),
            "students",
        ),
        BenchmarkScenario(
            "graph.rows",
            "POST",
            "/api/graph/rows",
            lambda _dataset: ScenarioRequest(
                "/api/graph/rows",
                json_body=dict(training_body),
            ),
            _collection("rows"),
            "session_details",
        ),
        BenchmarkScenario(
            "graph.evidence",
            "POST",
            "/api/graph/evidence",
            lambda _dataset: ScenarioRequest(
                "/api/graph/evidence",
                json_body={
                    **training_body,
                    "knowledge_key": dataset.knowledge_key,
                    "page": 1,
                    "page_size": 100,
                },
            ),
            _collection("items"),
            "session_details",
        ),
        BenchmarkScenario(
            "ops.self_check",
            "GET",
            "/api/ops/self-check",
            _request("/api/ops/self-check"),
            _single,
            "constant",
        ),
        BenchmarkScenario(
            "ops.backups",
            "GET",
            "/api/ops/backups",
            lambda _dataset: ScenarioRequest(
                "/api/ops/backups",
                params=(("limit", "100"),),
            ),
            _collection("items"),
            "backup_files",
        ),
    )
    return scenarios


def _nested_collection(response: Response, parent: str, child: str) -> int:
    payload: Any = response.json()
    if not isinstance(payload, dict):
        raise ValueError("response object is missing")
    nested = payload.get(parent)
    if not isinstance(nested, dict) or not isinstance(nested.get(child), list):
        raise ValueError("response collection is missing")
    return len(nested[child])


__all__ = [
    "BenchmarkScenario",
    "ScenarioRequest",
    "build_scenarios",
]
