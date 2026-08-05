from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from statistics import median
from threading import RLock

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import (
    get_diagnosis_profile_service,
    get_practice_plan_service,
    get_question_bank_read_service,
    get_request_diagnosis_profile_service,
    get_request_practice_plan_service,
    get_training_task_service,
)
from backend.api.routers.graph import get_current_graph_query_service
from backend.performance.metrics import (
    InMemoryPerformanceSink,
    RequestPerformanceRecord,
)
from integration.diagnosis_profile_service import DiagnosisProfileService
from path_manager import get_path_manager
from question_bank.recommendation.practice_plan_service import PracticePlanService
from question_bank.relations.query_service import CurrentKnowledgeGraphQueryService
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.services.training_task_service import TrainingTaskService
from tools.performance.dataset import BenchmarkDataset, BenchmarkPaths, DatasetManifest
from tools.performance.scenarios import BenchmarkScenario, build_scenarios


_PATH_CONTEXT_LOCK = RLock()
_EXPLICIT_PATHS: ContextVar[BenchmarkPaths | None] = ContextVar(
    "benchmark_explicit_paths",
    default=None,
)
REQUEST_SCOPED_MODE = "request_scoped"
LEGACY_PER_CALL_MODE = "legacy_per_call"
CONNECTION_MODES = (REQUEST_SCOPED_MODE, LEGACY_PER_CALL_MODE)


class BenchmarkRunError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class NumericSummary:
    minimum: float
    median: float
    maximum: float


@dataclass(frozen=True, slots=True)
class TimingSummary:
    minimum: float
    p50: float
    p95: float
    maximum: float


@dataclass(frozen=True, slots=True)
class ScenarioSummary:
    name: str
    method: str
    route_template: str
    status_code: int
    sample_count: int
    latency_ms: TimingSummary
    db_statements: NumericSummary
    db_selects: NumericSummary
    response_records: NumericSummary
    response_bytes: NumericSummary
    scale_driver: str
    scale_driver_count: int

    @property
    def select_median(self) -> float:
        return self.db_selects.median


@dataclass(frozen=True, slots=True)
class RepetitionBenchmarkResult:
    repetition: int
    scenarios: tuple[ScenarioSummary, ...]


@dataclass(frozen=True, slots=True)
class ScaleBenchmarkResult:
    manifest: DatasetManifest
    repetitions: tuple[RepetitionBenchmarkResult, ...]
    repeatability: str


@dataclass(frozen=True, slots=True, repr=False)
class _Sample:
    status_code: int
    elapsed_ms: float
    db_statements_total: int
    db_select_statements: int
    response_records: int
    response_bytes: int


def run_scale(
    dataset: BenchmarkDataset,
    *,
    warmups: int = 3,
    samples: int = 20,
    repetitions: int = 2,
    scenario_names: Iterable[str] | None = None,
    connection_mode: str = REQUEST_SCOPED_MODE,
) -> ScaleBenchmarkResult:
    if warmups <= 0 or samples <= 0 or repetitions <= 0:
        raise ValueError("benchmark counts must be positive")
    scenarios = _select_scenarios(build_scenarios(dataset), scenario_names)
    if connection_mode not in CONNECTION_MODES:
        raise BenchmarkRunError("connection_mode:unknown")
    sink = InMemoryPerformanceSink()
    app = _build_app(dataset, sink, connection_mode=connection_mode)
    used_request_ids: set[str] = set()
    completed: list[RepetitionBenchmarkResult] = []

    with _temporary_path_provider(dataset.paths):
        with TestClient(app) as client:
            for repetition in range(1, repetitions + 1):
                summaries: list[ScenarioSummary] = []
                for scenario in scenarios:
                    for warmup in range(1, warmups + 1):
                        request_id = _request_id(
                            scale=dataset.manifest.scale_name,
                            scenario=scenario.name,
                            repetition=repetition,
                            sample=f"warmup-{warmup}",
                        )
                        _claim_request_id(used_request_ids, request_id, scenario.name)
                        _send(
                            client,
                            sink,
                            dataset,
                            scenario,
                            request_id,
                            keep_sample=False,
                        )

                    formal: list[_Sample] = []
                    for sample_number in range(1, samples + 1):
                        request_id = _request_id(
                            scale=dataset.manifest.scale_name,
                            scenario=scenario.name,
                            repetition=repetition,
                            sample=str(sample_number),
                        )
                        _claim_request_id(used_request_ids, request_id, scenario.name)
                        sample = _send(
                            client,
                            sink,
                            dataset,
                            scenario,
                            request_id,
                            keep_sample=True,
                        )
                        if sample is None:
                            raise BenchmarkRunError(f"{scenario.name}:missing_sample")
                        formal.append(sample)
                    summaries.append(_summarize(dataset, scenario, formal))
                completed.append(
                    RepetitionBenchmarkResult(
                        repetition=repetition,
                        scenarios=tuple(summaries),
                    )
                )

    if len(completed) != repetitions:
        raise BenchmarkRunError("scale:incomplete_repetitions")
    first = deterministic_projection(completed[0])
    if any(deterministic_projection(item) != first for item in completed[1:]):
        raise BenchmarkRunError("scale:repeatability_failed")
    return ScaleBenchmarkResult(
        manifest=dataset.manifest,
        repetitions=tuple(completed),
        repeatability="passed",
    )


def _select_scenarios(
    available: tuple[BenchmarkScenario, ...],
    requested_names: Iterable[str] | None,
) -> tuple[BenchmarkScenario, ...]:
    if requested_names is None:
        return available
    requested = tuple(str(name) for name in requested_names)
    if not requested:
        raise BenchmarkRunError("scenario_filter:empty")
    if len(set(requested)) != len(requested):
        raise BenchmarkRunError("scenario_filter:duplicate")
    available_names = {scenario.name for scenario in available}
    if any(name not in available_names for name in requested):
        raise BenchmarkRunError("scenario_filter:unknown")
    requested_set = set(requested)
    return tuple(
        scenario for scenario in available if scenario.name in requested_set
    )


def _build_app(
    dataset: BenchmarkDataset,
    sink: InMemoryPerformanceSink,
    *,
    connection_mode: str = REQUEST_SCOPED_MODE,
):
    if connection_mode not in CONNECTION_MODES:
        raise BenchmarkRunError("connection_mode:unknown")
    app = create_app(performance_sink=sink, path_manager=dataset.paths)
    app.dependency_overrides[get_path_manager] = lambda: dataset.paths
    app.dependency_overrides[get_question_bank_read_service] = lambda: (
        QuestionBankReadService(
            dataset.paths.qb_db_path,
            data_root=dataset.paths.data_root,
        )
    )
    app.dependency_overrides[get_diagnosis_profile_service] = lambda: (
        DiagnosisProfileService(
            dataset.paths.db_path,
            dataset.paths.qb_db_path,
        )
    )
    app.dependency_overrides[get_practice_plan_service] = lambda: PracticePlanService(
        dataset.paths.qb_db_path
    )
    app.dependency_overrides[get_training_task_service] = lambda: TrainingTaskService(
        dataset.paths.qb_db_path
    )
    graph_service = CurrentKnowledgeGraphQueryService(dataset.paths.qb_db_path)
    app.dependency_overrides[get_current_graph_query_service] = lambda: graph_service
    if connection_mode == LEGACY_PER_CALL_MODE:
        app.dependency_overrides[
            get_request_diagnosis_profile_service
        ] = app.dependency_overrides[get_diagnosis_profile_service]
        app.dependency_overrides[
            get_request_practice_plan_service
        ] = app.dependency_overrides[get_practice_plan_service]
    return app


@contextmanager
def _temporary_path_provider(paths: BenchmarkPaths) -> Iterator[None]:
    import path_manager

    with _PATH_CONTEXT_LOCK:
        original = path_manager.get_path_manager

        def scoped_path_provider():
            explicit = _EXPLICIT_PATHS.get()
            return explicit if explicit is not None else original()

        path_manager.get_path_manager = scoped_path_provider
        token = _EXPLICIT_PATHS.set(paths)
        try:
            yield
        finally:
            _EXPLICIT_PATHS.reset(token)
            path_manager.get_path_manager = original


def _request_id(
    *,
    scale: str,
    scenario: str,
    repetition: int,
    sample: str,
) -> str:
    return f"p1-26-{scale}-{scenario}-{repetition}-{sample}"


def _claim_request_id(
    used: set[str],
    request_id: str,
    scenario_name: str,
) -> None:
    if request_id in used:
        raise BenchmarkRunError(f"{scenario_name}:duplicate_request_id")
    used.add(request_id)


def _send(
    client: TestClient,
    sink: InMemoryPerformanceSink,
    dataset: BenchmarkDataset,
    scenario: BenchmarkScenario,
    request_id: str,
    *,
    keep_sample: bool,
) -> _Sample | None:
    request = scenario.build_request(dataset)
    response = client.request(
        scenario.method,
        request.path,
        params=list(request.params),
        json=request.json_body,
        headers={"x-request-id": request_id},
    )
    if response.status_code != 200:
        _discard_record(sink, request_id)
        raise BenchmarkRunError(f"{scenario.name}:non_200")
    record = _pop_record(sink, request_id, scenario.name)
    if record.route_template != scenario.route_template:
        raise BenchmarkRunError(f"{scenario.name}:route_mismatch")
    if not keep_sample:
        return None
    try:
        response_records = int(scenario.count_records(response))
    except Exception as exc:
        raise BenchmarkRunError(f"{scenario.name}:invalid_response") from exc
    if response_records < 0:
        raise BenchmarkRunError(f"{scenario.name}:invalid_response")
    return _Sample(
        status_code=response.status_code,
        elapsed_ms=record.elapsed_ms,
        db_statements_total=record.db_statements_total,
        db_select_statements=record.db_select_statements,
        response_records=response_records,
        response_bytes=len(response.content),
    )


def _pop_record(
    sink: InMemoryPerformanceSink,
    request_id: str,
    scenario_name: str,
) -> RequestPerformanceRecord:
    try:
        return sink.pop(request_id)
    except KeyError as exc:
        raise BenchmarkRunError(f"{scenario_name}:missing_record") from exc


def _discard_record(sink: InMemoryPerformanceSink, request_id: str) -> None:
    try:
        sink.pop(request_id)
    except KeyError:
        pass


def _summarize(
    dataset: BenchmarkDataset,
    scenario: BenchmarkScenario,
    samples: list[_Sample],
) -> ScenarioSummary:
    if not samples:
        raise BenchmarkRunError(f"{scenario.name}:missing_sample")
    return ScenarioSummary(
        name=scenario.name,
        method=scenario.method,
        route_template=scenario.route_template,
        status_code=200,
        sample_count=len(samples),
        latency_ms=_timing([sample.elapsed_ms for sample in samples]),
        db_statements=_numeric(
            [sample.db_statements_total for sample in samples]
        ),
        db_selects=_numeric(
            [sample.db_select_statements for sample in samples]
        ),
        response_records=_numeric(
            [sample.response_records for sample in samples]
        ),
        response_bytes=_numeric([sample.response_bytes for sample in samples]),
        scale_driver=scenario.scale_driver,
        scale_driver_count=_driver_count(dataset, scenario.scale_driver),
    )


def _numeric(values: list[int | float]) -> NumericSummary:
    ordered = sorted(float(value) for value in values)
    if any(not math.isfinite(value) or value < 0 for value in ordered):
        raise BenchmarkRunError("scale:invalid_metric")
    return NumericSummary(ordered[0], float(median(ordered)), ordered[-1])


def _timing(values: list[float]) -> TimingSummary:
    ordered = sorted(float(value) for value in values)
    if not ordered or any(not math.isfinite(value) or value < 0 for value in ordered):
        raise BenchmarkRunError("scale:invalid_timing")
    return TimingSummary(
        minimum=ordered[0],
        p50=_nearest_rank(ordered, 0.50),
        p95=_nearest_rank(ordered, 0.95),
        maximum=ordered[-1],
    )


def _nearest_rank(ordered: list[float], percentile: float) -> float:
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _driver_count(dataset: BenchmarkDataset, driver: str) -> int:
    if driver == "constant":
        return 1
    if driver == "generated_asset_bytes":
        return dataset.manifest.generated_asset_bytes
    counts = dict(dataset.manifest.table_counts)
    try:
        return int(counts[driver])
    except KeyError as exc:
        raise BenchmarkRunError("scale:unknown_scale_driver") from exc


def deterministic_projection(
    result: ScaleBenchmarkResult | RepetitionBenchmarkResult,
) -> dict[str, object] | tuple[tuple[object, ...], ...]:
    if isinstance(result, ScaleBenchmarkResult):
        manifest = result.manifest
        return {
            "manifest": {
                "scale": manifest.scale_name,
                "seed": manifest.seed,
                "logical_counts": dict(manifest.table_counts),
                "grading_database_bytes": manifest.grading_database_bytes,
                "question_bank_database_bytes": manifest.question_bank_database_bytes,
                "backup_database_bytes": manifest.backup_database_bytes,
                "generated_asset_bytes": manifest.generated_asset_bytes,
            },
            "repetitions": tuple(
                _repetition_projection(repetition)
                for repetition in result.repetitions
            ),
        }
    return _repetition_projection(result)


def _repetition_projection(
    repetition: RepetitionBenchmarkResult,
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            summary.name,
            summary.status_code,
            summary.sample_count,
            summary.db_statements.minimum,
            summary.db_statements.median,
            summary.db_statements.maximum,
            summary.db_selects.minimum,
            summary.db_selects.median,
            summary.db_selects.maximum,
            summary.response_records.minimum,
            summary.response_records.median,
            summary.response_records.maximum,
        )
        for summary in repetition.scenarios
    )


__all__ = [
    "BenchmarkRunError",
    "CONNECTION_MODES",
    "LEGACY_PER_CALL_MODE",
    "NumericSummary",
    "RepetitionBenchmarkResult",
    "ScaleBenchmarkResult",
    "ScenarioSummary",
    "TimingSummary",
    "REQUEST_SCOPED_MODE",
    "deterministic_projection",
    "run_scale",
]
