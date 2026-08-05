from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from tools.performance.report import RuntimeSummary
from tools.performance.runner import NumericSummary, ScaleBenchmarkResult, ScenarioSummary


CONTROL_SCENARIO = "question_bank.questions.default"
TARGET_SCENARIOS = (
    "training.diagnosis",
    "training.plan.preview",
    "graph.evidence",
)
SCENARIO_NAMES = (CONTROL_SCENARIO, *TARGET_SCENARIOS)
MEDIUM_LATENCY_SCENARIOS = (
    "training.plan.preview",
    "graph.evidence",
)

_UNSAFE_TEXT = re.compile(
    r"[A-Za-z]:[\\/]|(?:^|[\\/])user_data(?:[\\/]|$)|\.worktrees|"
    r"request[_ -]?id|\b(?:select|insert|update|delete|pragma|create table)\b",
    re.IGNORECASE,
)


class ComparisonGateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class BaselineMeasurement:
    scale: str
    repetition: int
    scenario: str
    status_code: int
    sample_count: int
    latency_p50_ms: float
    db_statements: NumericSummary
    db_selects: NumericSummary
    response_records: NumericSummary


@dataclass(frozen=True, slots=True, repr=False)
class BaselineReport:
    package: str
    code_sha: str
    seed: int
    repeatability: str
    scales: tuple[str, ...]
    scenarios: tuple[str, ...]
    measurements: tuple[BaselineMeasurement, ...]


@dataclass(frozen=True, slots=True)
class EqualityComparison:
    before: int | float
    after: int | float
    equal: bool


@dataclass(frozen=True, slots=True)
class ResponseRecordSummary:
    minimum: float | None
    median: float
    maximum: float | None


@dataclass(frozen=True, slots=True)
class ResponseRecordComparison:
    before: ResponseRecordSummary
    after: ResponseRecordSummary
    equal: bool


@dataclass(frozen=True, slots=True)
class MetricComparison:
    before: float
    after: float
    improvement_percent: float
    gate_required: bool
    gate_passed: bool


@dataclass(frozen=True, slots=True)
class ScenarioComparison:
    scale: str
    repetition: int
    scenario: str
    kind: str
    status: EqualityComparison
    response_records: ResponseRecordComparison
    latency_p50_ms: MetricComparison
    db_statements: MetricComparison
    db_selects: MetricComparison


@dataclass(frozen=True, slots=True)
class GateSummary:
    name: str
    required_count: int
    passed_count: int | None
    passed: bool | None


@dataclass(frozen=True, slots=True)
class RequestConnectionReport:
    package: str
    p1_26_provenance_code_sha: str
    code_sha: str
    generated_at_utc: str
    runtime: RuntimeSummary
    seed: int
    scales: tuple[str, ...]
    scenarios: tuple[str, ...]
    before_mode: str
    after_mode: str
    data_scale_factor: float
    warmup_count: int
    sample_count: int
    repetition_count: int
    repeatability: str
    comparisons: tuple[ScenarioComparison, ...]
    gates: tuple[GateSummary, ...]
    overall_gate: str
    limitations: tuple[str, ...]


def load_baseline_report(
    source: Path,
    *,
    scales: Iterable[str],
    scenarios: Iterable[str] = SCENARIO_NAMES,
) -> BaselineReport:
    requested_scales = _unique_names(scales, "baseline:scales")
    requested_scenarios = _unique_names(scenarios, "baseline:scenarios")
    if any(name not in SCENARIO_NAMES for name in requested_scenarios):
        raise ValueError("baseline:scenario_not_allowlisted")
    try:
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("baseline:unreadable") from exc
    if not isinstance(payload, dict) or payload.get("package") != "P1-26":
        raise ValueError("baseline:invalid_package")
    code_sha = str(payload.get("code_sha", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", code_sha):
        raise ValueError("baseline:invalid_code_sha")
    seed = _positive_int(payload.get("seed"), "baseline:invalid_seed")
    if payload.get("repeatability") != "passed":
        raise ValueError("baseline:repeatability_failed")
    raw_scales = payload.get("scales")
    if not isinstance(raw_scales, list):
        raise ValueError("baseline:missing_scales")
    by_scale: dict[str, dict[str, Any]] = {}
    for item in raw_scales:
        if not isinstance(item, dict) or not isinstance(item.get("manifest"), dict):
            raise ValueError("baseline:invalid_scale")
        name = str(item["manifest"].get("scale", ""))
        if name in by_scale:
            raise ValueError("baseline:duplicate_scale")
        by_scale[name] = item
    if any(name not in by_scale for name in requested_scales):
        raise ValueError("baseline:missing_scale")

    measurements: list[BaselineMeasurement] = []
    for scale_name in requested_scales:
        scale_payload = by_scale[scale_name]
        manifest = scale_payload["manifest"]
        if _positive_int(manifest.get("seed"), "baseline:invalid_seed") != seed:
            raise ValueError("baseline:seed_mismatch")
        if scale_payload.get("repeatability") != "passed":
            raise ValueError("baseline:repeatability_failed")
        raw_repetitions = scale_payload.get("repetitions")
        if not isinstance(raw_repetitions, list) or len(raw_repetitions) != 2:
            raise ValueError("baseline:requires_two_repetitions")
        repetition_projections: list[tuple[tuple[object, ...], ...]] = []
        for expected_repetition, repetition_payload in enumerate(raw_repetitions, start=1):
            if not isinstance(repetition_payload, dict):
                raise ValueError("baseline:invalid_repetition")
            if repetition_payload.get("repetition") != expected_repetition:
                raise ValueError("baseline:invalid_repetition")
            raw_scenarios = repetition_payload.get("scenarios")
            if not isinstance(raw_scenarios, list):
                raise ValueError("baseline:missing_scenarios")
            scenario_map: dict[str, dict[str, Any]] = {}
            for scenario_payload in raw_scenarios:
                if not isinstance(scenario_payload, dict):
                    raise ValueError("baseline:invalid_scenario")
                name = str(scenario_payload.get("name", ""))
                if name in scenario_map:
                    raise ValueError("baseline:duplicate_scenario")
                scenario_map[name] = scenario_payload
            if any(name not in scenario_map for name in requested_scenarios):
                raise ValueError("baseline:missing_scenario")
            projection: list[tuple[object, ...]] = []
            for scenario_name in requested_scenarios:
                scenario_payload = scenario_map[scenario_name]
                status_code = int(scenario_payload.get("status_code", 0))
                if status_code != 200:
                    raise ValueError("baseline:non_200")
                sample_count = _positive_int(
                    scenario_payload.get("sample_count"),
                    "baseline:invalid_sample_count",
                )
                latency = scenario_payload.get("latency_ms")
                if not isinstance(latency, dict):
                    raise ValueError("baseline:invalid_latency")
                latency_p50_ms = _finite_nonnegative(
                    latency.get("p50"), "baseline:invalid_latency"
                )
                db_statements = _numeric_summary(
                    scenario_payload.get("db_statements"),
                    "baseline:invalid_db_statements",
                )
                db_selects = _numeric_summary(
                    scenario_payload.get("db_selects"),
                    "baseline:invalid_db_selects",
                )
                response_records = _numeric_summary(
                    scenario_payload.get("response_records"),
                    "baseline:invalid_response_records",
                )
                measurement = BaselineMeasurement(
                    scale=scale_name,
                    repetition=expected_repetition,
                    scenario=scenario_name,
                    status_code=status_code,
                    sample_count=sample_count,
                    latency_p50_ms=latency_p50_ms,
                    db_statements=db_statements,
                    db_selects=db_selects,
                    response_records=response_records,
                )
                measurements.append(measurement)
                projection.append(_deterministic_measurement_projection(measurement))
            repetition_projections.append(tuple(projection))
        if repetition_projections[0] != repetition_projections[1]:
            raise ValueError("baseline:repeatability_failed")
    return BaselineReport(
        package="P1-26",
        code_sha=code_sha,
        seed=seed,
        repeatability="passed",
        scales=requested_scales,
        scenarios=requested_scenarios,
        measurements=tuple(measurements),
    )


def build_comparison_report(
    baseline: BaselineReport,
    legacy_scales: Iterable[ScaleBenchmarkResult],
    optimized_scales: Iterable[ScaleBenchmarkResult],
    *,
    code_sha: str,
    generated_at_utc: str,
    runtime: RuntimeSummary,
    data_scale_factor: float,
    warmups: int,
    samples: int,
    repetitions: int,
    limitations: Iterable[str],
) -> RequestConnectionReport:
    legacy_results = tuple(legacy_scales)
    optimized_results = tuple(optimized_scales)
    if not re.fullmatch(r"[0-9a-f]{40}", code_sha):
        raise ValueError("comparison:invalid_code_sha")
    if warmups <= 0 or samples <= 0:
        raise ValueError("comparison:invalid_counts")
    if repetitions != 2:
        raise ValueError("comparison:requires_two_repetitions")
    if runtime.logical_cpu_count <= 0:
        raise ValueError("comparison:invalid_runtime")
    if not math.isfinite(data_scale_factor) or not 0 < data_scale_factor <= 1:
        raise ValueError("comparison:invalid_data_scale_factor")
    legacy_names = tuple(result.manifest.scale_name for result in legacy_results)
    optimized_names = tuple(result.manifest.scale_name for result in optimized_results)
    if legacy_names != baseline.scales or optimized_names != baseline.scales:
        raise ValueError("comparison:scale_mismatch")
    if any(
        result.manifest.seed != baseline.seed
        for result in (*legacy_results, *optimized_results)
    ):
        raise ValueError("comparison:seed_mismatch")
    legacy_repeatability = all(
        result.repeatability == "passed" for result in legacy_results
    )
    optimized_repeatability = all(
        result.repeatability == "passed" for result in optimized_results
    )
    if not legacy_repeatability or not optimized_repeatability:
        raise ValueError("comparison:repeatability_failed")
    if any(
        legacy.manifest != optimized.manifest
        for legacy, optimized in zip(legacy_results, optimized_results, strict=True)
    ):
        raise ValueError("comparison:dataset_mismatch")
    safe_limitations = tuple(str(item).strip() for item in limitations if str(item).strip())
    if not safe_limitations:
        raise ValueError("comparison:missing_limitations")
    for value in (
        generated_at_utc,
        runtime.windows_version,
        runtime.python_version,
        runtime.sqlite_version,
        *safe_limitations,
    ):
        _require_safe_text(value)

    comparisons: list[ScenarioComparison] = []
    for legacy_result, optimized_result in zip(
        legacy_results,
        optimized_results,
        strict=True,
    ):
        if (
            len(legacy_result.repetitions) != repetitions
            or len(optimized_result.repetitions) != repetitions
        ):
            raise ValueError("comparison:requires_two_repetitions")
        for expected_repetition, (legacy_repetition, optimized_repetition) in enumerate(
            zip(
                legacy_result.repetitions,
                optimized_result.repetitions,
                strict=True,
            ),
            start=1,
        ):
            if (
                legacy_repetition.repetition != expected_repetition
                or optimized_repetition.repetition != expected_repetition
            ):
                raise ValueError("comparison:invalid_repetition")
            legacy_map = {
                summary.name: summary for summary in legacy_repetition.scenarios
            }
            optimized_map = {
                summary.name: summary for summary in optimized_repetition.scenarios
            }
            if (
                len(legacy_map) != len(legacy_repetition.scenarios)
                or len(optimized_map) != len(optimized_repetition.scenarios)
            ):
                raise ValueError("comparison:duplicate_scenario")
            if (
                set(legacy_map) != set(baseline.scenarios)
                or set(optimized_map) != set(baseline.scenarios)
            ):
                raise ValueError("comparison:scenario_mismatch")
            for scenario_name in baseline.scenarios:
                before = legacy_map[scenario_name]
                after = optimized_map[scenario_name]
                if before.sample_count != samples or after.sample_count != samples:
                    raise ValueError("comparison:sample_count_mismatch")
                comparisons.append(
                    _compare_scenario(
                        before,
                        after,
                        scale=legacy_result.manifest.scale_name,
                        repetition=expected_repetition,
                    )
                )

    comparison_tuple = tuple(comparisons)
    gates = _build_gates(
        comparison_tuple,
        legacy_repeatability,
        optimized_repeatability,
        len(legacy_results),
    )
    overall_gate = "passed" if all(gate.passed for gate in gates) else "failed"
    return RequestConnectionReport(
        package="P1-27",
        p1_26_provenance_code_sha=baseline.code_sha,
        code_sha=code_sha,
        generated_at_utc=str(generated_at_utc),
        runtime=runtime,
        seed=baseline.seed,
        scales=baseline.scales,
        scenarios=baseline.scenarios,
        before_mode="legacy_per_call",
        after_mode="request_scoped",
        data_scale_factor=data_scale_factor,
        warmup_count=warmups,
        sample_count=samples,
        repetition_count=repetitions,
        repeatability="passed",
        comparisons=comparison_tuple,
        gates=gates,
        overall_gate=overall_gate,
        limitations=safe_limitations,
    )


def ensure_report_passes(report: RequestConnectionReport) -> None:
    failed = [gate.name for gate in report.gates if not gate.passed]
    if report.overall_gate != "passed" or failed:
        raise ComparisonGateError(
            "comparison gates failed: " + ",".join(failed or ["overall_gate"])
        )


def render_json(report: RequestConnectionReport) -> str:
    return json.dumps(
        _report_payload(report),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def render_markdown(report: RequestConnectionReport) -> str:
    lines = [
        "# P1-27 request connection comparison",
        "",
        f"- P1-26 provenance code SHA: `{report.p1_26_provenance_code_sha}`",
        f"- code SHA: `{report.code_sha}`",
        f"- generated at UTC: `{report.generated_at_utc}`",
        f"- seed: `{report.seed}`",
        f"- scales: `{', '.join(report.scales)}`",
        f"- scenarios: `{', '.join(report.scenarios)}`",
        f"- before/after modes: `{report.before_mode}/{report.after_mode}`",
        f"- data scale factor: `{_number(report.data_scale_factor)}`",
        f"- warmups/samples/repetitions: `{report.warmup_count}/{report.sample_count}/{report.repetition_count}`",
        f"- repeatability: `{report.repeatability}`",
        f"- overall gate: `{report.overall_gate}`",
        "",
        "## Runtime",
        "",
        "| Windows version | Python version | SQLite version | logical CPU |",
        "|---|---|---|---:|",
        (
            f"| {report.runtime.windows_version} | {report.runtime.python_version} | "
            f"{report.runtime.sqlite_version} | {report.runtime.logical_cpu_count} |"
        ),
        "",
        "## Aggregate comparisons",
        "",
        "| scale | repetition | scenario | kind | status before/after | response records before min/median/max | response records after min/median/max | before p50 ms | after p50 ms | p50 improvement | DB statements before/after | statement reduction | DB selects before/after | statement gate | medium p50 gate |",
        "|---|---:|---|---|---|---|---|---:|---:|---:|---|---:|---|---|---|",
    ]
    for item in report.comparisons:
        lines.append(
            f"| {item.scale} | {item.repetition} | {item.scenario} | {item.kind} | "
            f"{_number(item.status.before)}/{_number(item.status.after)} | "
            f"{_response_record_display(item.response_records.before)} | "
            f"{_response_record_display(item.response_records.after)} | "
            f"{_number(item.latency_p50_ms.before)} | {_number(item.latency_p50_ms.after)} | "
            f"{_number(item.latency_p50_ms.improvement_percent)}% | "
            f"{_number(item.db_statements.before)}/{_number(item.db_statements.after)} | "
            f"{_number(item.db_statements.improvement_percent)}% | "
            f"{_number(item.db_selects.before)}/{_number(item.db_selects.after)} | "
            f"{_gate_label(item.db_statements)} | {_gate_label(item.latency_p50_ms)} |"
        )
    lines.extend(
        [
            "",
            "## Gates",
            "",
            "| gate | required comparisons | passed comparisons | result |",
            "|---|---:|---:|---|",
        ]
    )
    for gate in report.gates:
        lines.append(
            f"| {_gate_display_name(gate.name)} | {gate.required_count} | "
            f"{'unavailable' if gate.passed_count is None else gate.passed_count} | "
            f"{_gate_result(gate)} |"
        )
    lines.extend(["", "## limitations", ""])
    lines.extend(f"- {item}" for item in report.limitations)
    return "\n".join(lines) + "\n"


def _compare_scenario(
    before: ScenarioSummary,
    after: ScenarioSummary,
    *,
    scale: str,
    repetition: int,
) -> ScenarioComparison:
    status_equal = before.status_code == after.status_code == 200
    records_equal = _numeric_tuple(before.response_records) == _numeric_tuple(
        after.response_records
    )
    target = before.name in TARGET_SCENARIOS
    latency_required = scale == "medium" and before.name in MEDIUM_LATENCY_SCENARIOS
    statement_improvement = _improvement(
        before.db_statements.median, after.db_statements.median
    )
    latency_improvement = _improvement(
        before.latency_ms.p50, after.latency_ms.p50
    )
    return ScenarioComparison(
        scale=scale,
        repetition=repetition,
        scenario=before.name,
        kind="control" if before.name == CONTROL_SCENARIO else "target",
        status=EqualityComparison(before.status_code, after.status_code, status_equal),
        response_records=ResponseRecordComparison(
            before=ResponseRecordSummary(
                before.response_records.minimum,
                before.response_records.median,
                before.response_records.maximum,
            ),
            after=ResponseRecordSummary(
                after.response_records.minimum,
                after.response_records.median,
                after.response_records.maximum,
            ),
            equal=records_equal,
        ),
        latency_p50_ms=MetricComparison(
            before.latency_ms.p50,
            after.latency_ms.p50,
            latency_improvement,
            latency_required,
            not latency_required or latency_improvement >= 20.0,
        ),
        db_statements=MetricComparison(
            before.db_statements.median,
            after.db_statements.median,
            statement_improvement,
            target,
            not target or after.db_statements.median < before.db_statements.median,
        ),
        db_selects=MetricComparison(
            before.db_selects.median,
            after.db_selects.median,
            _improvement(before.db_selects.median, after.db_selects.median),
            False,
            True,
        ),
    )


def _build_gates(
    comparisons: tuple[ScenarioComparison, ...],
    legacy_repeatability_passed: bool,
    optimized_repeatability_passed: bool,
    scale_count: int,
) -> tuple[GateSummary, ...]:
    statuses = [item.status.equal for item in comparisons]
    records = [item.response_records.equal for item in comparisons]
    nonzero_records = [
        item.response_records.before.minimum is not None
        and item.response_records.before.minimum > 0
        and item.response_records.after.minimum is not None
        and item.response_records.after.minimum > 0
        for item in comparisons
    ]
    statements = [
        item.db_statements.gate_passed
        for item in comparisons
        if item.db_statements.gate_required
    ]
    latencies = [
        item.latency_p50_ms.gate_passed
        for item in comparisons
        if item.latency_p50_ms.gate_required
    ]
    return (
        _gate("status_equality", statuses),
        _gate("response_record_equality", records),
        _gate("nonzero_response_records", nonzero_records),
        _gate("target_statement_reduction", statements),
        _gate("medium_p50_improvement", latencies, allow_empty=True),
        GateSummary(
            "legacy_two_round_repeatability",
            scale_count,
            scale_count if legacy_repeatability_passed else 0,
            legacy_repeatability_passed,
        ),
        GateSummary(
            "request_scoped_two_round_repeatability",
            scale_count,
            scale_count if optimized_repeatability_passed else 0,
            optimized_repeatability_passed,
        ),
    )


def _gate(
    name: str,
    values: list[bool],
    *,
    allow_empty: bool = False,
) -> GateSummary:
    passed_count = sum(bool(value) for value in values)
    passed = passed_count == len(values) and (bool(values) or allow_empty)
    return GateSummary(name, len(values), passed_count, passed)


def _report_payload(report: RequestConnectionReport) -> dict[str, Any]:
    return {
        "package": report.package,
        "p1_26_provenance_code_sha": report.p1_26_provenance_code_sha,
        "code_sha": report.code_sha,
        "generated_at_utc": report.generated_at_utc,
        "runtime": {
            "windows_version": report.runtime.windows_version,
            "python_version": report.runtime.python_version,
            "sqlite_version": report.runtime.sqlite_version,
            "logical_cpu_count": report.runtime.logical_cpu_count,
        },
        "seed": report.seed,
        "scales": list(report.scales),
        "scenarios": list(report.scenarios),
        "before_mode": report.before_mode,
        "after_mode": report.after_mode,
        "data_scale_factor": report.data_scale_factor,
        "warmup_count": report.warmup_count,
        "sample_count": report.sample_count,
        "repetition_count": report.repetition_count,
        "repeatability": report.repeatability,
        "comparisons": [_comparison_payload(item) for item in report.comparisons],
        "gates": [
            {
                "name": gate.name,
                "required_count": gate.required_count,
                "passed_count": gate.passed_count,
                "passed": gate.passed,
                "result": _gate_result(gate),
            }
            for gate in report.gates
        ],
        "overall_gate": report.overall_gate,
        "limitations": list(report.limitations),
    }


def _comparison_payload(item: ScenarioComparison) -> dict[str, Any]:
    return {
        "scale": item.scale,
        "repetition": item.repetition,
        "scenario": item.scenario,
        "kind": item.kind,
        "status": _equality_payload(item.status),
        "response_records": _response_record_payload(item.response_records),
        "latency_p50_ms": _metric_payload(item.latency_p50_ms),
        "db_statements": _metric_payload(item.db_statements),
        "db_selects": _metric_payload(item.db_selects),
    }


def _equality_payload(item: EqualityComparison) -> dict[str, int | float | bool]:
    return {"before": item.before, "after": item.after, "equal": item.equal}


def _response_record_payload(item: ResponseRecordComparison) -> dict[str, Any]:
    return {
        "before": _response_record_summary_payload(item.before),
        "after": _response_record_summary_payload(item.after),
        "equal": item.equal,
    }


def _response_record_summary_payload(
    item: ResponseRecordSummary,
) -> dict[str, float | None]:
    return {
        "minimum": item.minimum,
        "median": item.median,
        "maximum": item.maximum,
    }


def _metric_payload(item: MetricComparison) -> dict[str, float | bool]:
    return {
        "before": item.before,
        "after": item.after,
        "improvement_percent": item.improvement_percent,
        "gate_required": item.gate_required,
        "gate_passed": item.gate_passed,
    }


def _numeric_summary(value: object, error: str) -> NumericSummary:
    if not isinstance(value, dict):
        raise ValueError(error)
    result = NumericSummary(
        _finite_nonnegative(value.get("minimum"), error),
        _finite_nonnegative(value.get("median"), error),
        _finite_nonnegative(value.get("maximum"), error),
    )
    if not result.minimum <= result.median <= result.maximum:
        raise ValueError(error)
    return result


def _finite_nonnegative(value: object, error: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(error) from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(error)
    return number


def _positive_int(value: object, error: str) -> int:
    if isinstance(value, bool):
        raise ValueError(error)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(error) from exc
    if number <= 0 or number != value:
        raise ValueError(error)
    return number


def _unique_names(values: Iterable[str], error: str) -> tuple[str, ...]:
    result = tuple(str(value) for value in values)
    if not result or len(set(result)) != len(result) or any(not value for value in result):
        raise ValueError(error)
    return result


def _deterministic_measurement_projection(
    item: BaselineMeasurement,
) -> tuple[object, ...]:
    return (
        item.scenario,
        item.status_code,
        item.sample_count,
        *_numeric_tuple(item.db_statements),
        *_numeric_tuple(item.db_selects),
        *_numeric_tuple(item.response_records),
    )


def _deterministic_scenario_projection(item: ScenarioSummary) -> tuple[object, ...]:
    return (
        item.name,
        item.status_code,
        item.sample_count,
        *_numeric_tuple(item.db_statements),
        *_numeric_tuple(item.db_selects),
        *_numeric_tuple(item.response_records),
    )


def _numeric_tuple(item: NumericSummary) -> tuple[float, float, float]:
    return item.minimum, item.median, item.maximum


def _improvement(before: float, after: float) -> float:
    if before == 0:
        return 0.0 if after == 0 else -100.0
    return (before - after) / before * 100.0


def _require_safe_text(value: object) -> None:
    text = str(value)
    if _UNSAFE_TEXT.search(text):
        raise ValueError("comparison:unsafe_report_text")


def _gate_label(item: MetricComparison) -> str:
    if not item.gate_required:
        return "not_required"
    return "passed" if item.gate_passed else "failed"


def _gate_display_name(name: str) -> str:
    return {
        "status_equality": "status equality",
        "response_record_equality": "response record equality",
        "nonzero_response_records": "non-zero response records",
        "target_statement_reduction": "target statement reduction",
        "medium_p50_improvement": "medium p50 improvement",
        "legacy_two_round_repeatability": "legacy two-round repeatability",
        "request_scoped_two_round_repeatability": "request-scoped two-round repeatability",
    }[name]


def _number(value: int | float) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.6f}".rstrip("0").rstrip(".")


def _gate_result(gate: GateSummary) -> str:
    if gate.passed is None:
        return "not_evaluated"
    return "passed" if gate.passed else "failed"


def _response_record_display(item: ResponseRecordSummary) -> str:
    values = (
        "unknown" if item.minimum is None else _number(item.minimum),
        _number(item.median),
        "unknown" if item.maximum is None else _number(item.maximum),
    )
    return "/".join(values)


__all__ = [
    "CONTROL_SCENARIO",
    "MEDIUM_LATENCY_SCENARIOS",
    "SCENARIO_NAMES",
    "TARGET_SCENARIOS",
    "BaselineMeasurement",
    "BaselineReport",
    "ComparisonGateError",
    "GateSummary",
    "RequestConnectionReport",
    "ResponseRecordComparison",
    "ResponseRecordSummary",
    "build_comparison_report",
    "ensure_report_passes",
    "load_baseline_report",
    "render_json",
    "render_markdown",
]
