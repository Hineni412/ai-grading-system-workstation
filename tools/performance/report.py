from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable

from tools.performance.runner import ScaleBenchmarkResult, ScenarioSummary


@dataclass(frozen=True, slots=True)
class RuntimeSummary:
    windows_version: str
    python_version: str
    sqlite_version: str
    logical_cpu_count: int


@dataclass(frozen=True, slots=True)
class PossibleNPlusOneObservation:
    label: str
    scenario: str
    small_select_median: float
    comparison_scale: str
    comparison_select_median: float
    small_driver_count: int
    comparison_driver_count: int


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    package: str
    code_sha: str
    generated_at_utc: str
    runtime: RuntimeSummary
    seed: int
    scales: tuple[ScaleBenchmarkResult, ...]
    repeatability: str
    observations: tuple[PossibleNPlusOneObservation, ...]
    limitations: tuple[str, ...]


def nearest_rank(values: Iterable[int | float], percentile: float) -> float:
    percentile = float(percentile)
    if not math.isfinite(percentile) or percentile <= 0 or percentile > 1:
        raise ValueError("percentile must be finite and in (0, 1]")
    ordered = sorted(float(value) for value in values)
    if not ordered or any(not math.isfinite(value) or value < 0 for value in ordered):
        raise ValueError("values must be finite and nonnegative")
    return ordered[math.ceil(percentile * len(ordered)) - 1]


def build_report(
    scales: Iterable[ScaleBenchmarkResult],
    *,
    code_sha: str,
    generated_at_utc: str,
    windows_version: str,
    python_version: str,
    sqlite_version: str,
    logical_cpu_count: int,
    limitations: Iterable[str],
) -> BenchmarkReport:
    scale_results = tuple(scales)
    if not scale_results:
        raise ValueError("at least one scale result is required")
    if not re.fullmatch(r"[0-9a-f]{40}", code_sha):
        raise ValueError("code SHA must be 40 lowercase hexadecimal characters")
    if logical_cpu_count <= 0:
        raise ValueError("logical CPU count must be positive")
    seeds = {result.manifest.seed for result in scale_results}
    if len(seeds) != 1:
        raise ValueError("all scale results must use one seed")
    if any(result.repeatability != "passed" for result in scale_results):
        raise ValueError("all scale results must pass repeatability")
    safe_limitations = tuple(str(item).strip() for item in limitations if str(item).strip())
    if not safe_limitations:
        raise ValueError("at least one limitation is required")
    return BenchmarkReport(
        package="P1-26",
        code_sha=code_sha,
        generated_at_utc=str(generated_at_utc),
        runtime=RuntimeSummary(
            windows_version=str(windows_version),
            python_version=str(python_version),
            sqlite_version=str(sqlite_version),
            logical_cpu_count=int(logical_cpu_count),
        ),
        seed=next(iter(seeds)),
        scales=scale_results,
        repeatability="passed",
        observations=possible_n_plus_one(scale_results),
        limitations=safe_limitations,
    )


def possible_n_plus_one(
    scales: Iterable[ScaleBenchmarkResult],
) -> tuple[PossibleNPlusOneObservation, ...]:
    by_scale = {result.manifest.scale_name: result for result in scales}
    if "small" not in by_scale or "large_5pct" not in by_scale:
        return ()
    small = _scenario_map(by_scale["small"])
    comparison = _scenario_map(by_scale["large_5pct"])
    observations: list[PossibleNPlusOneObservation] = []
    for name in sorted(small.keys() & comparison.keys()):
        small_summary = small[name]
        comparison_summary = comparison[name]
        select_delta = (
            comparison_summary.select_median - small_summary.select_median
        )
        driver_delta = (
            comparison_summary.scale_driver_count - small_summary.scale_driver_count
        )
        candidate = (
            select_delta >= 5
            and driver_delta > 0
            and select_delta / driver_delta >= 0.10
        )
        if candidate:
            observations.append(
                PossibleNPlusOneObservation(
                    label="possible_n_plus_one",
                    scenario=name,
                    small_select_median=small_summary.select_median,
                    comparison_scale="large_5pct",
                    comparison_select_median=comparison_summary.select_median,
                    small_driver_count=small_summary.scale_driver_count,
                    comparison_driver_count=comparison_summary.scale_driver_count,
                )
            )
    return tuple(observations)


def _scenario_map(result: ScaleBenchmarkResult) -> dict[str, ScenarioSummary]:
    if not result.repetitions:
        return {}
    return {summary.name: summary for summary in result.repetitions[0].scenarios}


def render_json(report: BenchmarkReport) -> str:
    return json.dumps(
        _report_payload(report),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def render_markdown(report: BenchmarkReport) -> str:
    lines = [
        "# P1-26 API/DB performance baseline",
        "",
        f"- code SHA: `{report.code_sha}`",
        f"- generated at UTC: `{report.generated_at_utc}`",
        f"- seed: `{report.seed}`",
        f"- repeatability: `{report.repeatability}`",
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
        "## Manifests",
        "",
        "| scale | logical counts | grading bytes | question-bank bytes | backup bytes | generated asset bytes |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for result in report.scales:
        manifest = result.manifest
        counts = ", ".join(f"{key}={value}" for key, value in manifest.table_counts)
        lines.append(
            f"| {manifest.scale_name} | {counts} | "
            f"{manifest.grading_database_bytes} | "
            f"{manifest.question_bank_database_bytes} | "
            f"{manifest.backup_database_bytes} | "
            f"{manifest.generated_asset_bytes} |"
        )
    lines.extend(
        [
            "",
            "## Aggregate measurements",
            "",
            "| scale | repetition | scenario | status | samples | minimum ms | p50 ms | p95 ms | maximum ms | DB statements min/median/max | DB selects min/median/max | response records min/median/max | response bytes min/median/max | scale driver |",
            "|---|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|---|---|",
        ]
    )
    for result in report.scales:
        for repetition in result.repetitions:
            for summary in repetition.scenarios:
                lines.append(
                    f"| {result.manifest.scale_name} | {repetition.repetition} | "
                    f"{summary.name} | {summary.status_code} | {summary.sample_count} | "
                    f"{_number(summary.latency_ms.minimum)} | "
                    f"{_number(summary.latency_ms.p50)} | "
                    f"{_number(summary.latency_ms.p95)} | "
                    f"{_number(summary.latency_ms.maximum)} | "
                    f"{_triple(summary.db_statements)} | "
                    f"{_triple(summary.db_selects)} | "
                    f"{_triple(summary.response_records)} | "
                    f"{_triple(summary.response_bytes)} | "
                    f"{summary.scale_driver}={summary.scale_driver_count} |"
                )
    lines.extend(["", "## Observations", ""])
    if report.observations:
        lines.extend(
            [
                "| label | scenario | small selects | comparison scale | comparison selects | small driver | comparison driver |",
                "|---|---|---:|---|---:|---:|---:|",
            ]
        )
        for item in report.observations:
            lines.append(
                f"| {item.label} | {item.scenario} | "
                f"{_number(item.small_select_median)} | "
                f"{item.comparison_scale} | "
                f"{_number(item.comparison_select_median)} | "
                f"{item.small_driver_count} | {item.comparison_driver_count} |"
            )
    else:
        lines.append("No possible_n_plus_one candidates met the fixed threshold.")
    lines.extend(["", "## limitations", ""])
    lines.extend(f"- {item}" for item in report.limitations)
    return "\n".join(lines) + "\n"


def _report_payload(report: BenchmarkReport) -> dict[str, Any]:
    return {
        "package": report.package,
        "code_sha": report.code_sha,
        "generated_at_utc": report.generated_at_utc,
        "runtime": {
            "windows_version": report.runtime.windows_version,
            "python_version": report.runtime.python_version,
            "sqlite_version": report.runtime.sqlite_version,
            "logical_cpu_count": report.runtime.logical_cpu_count,
        },
        "seed": report.seed,
        "scales": [_scale_payload(result) for result in report.scales],
        "repeatability": report.repeatability,
        "observations": [
            {
                "label": item.label,
                "scenario": item.scenario,
                "small_select_median": item.small_select_median,
                "comparison_scale": item.comparison_scale,
                "comparison_select_median": item.comparison_select_median,
                "small_driver_count": item.small_driver_count,
                "comparison_driver_count": item.comparison_driver_count,
            }
            for item in report.observations
        ],
        "limitations": list(report.limitations),
    }


def _scale_payload(result: ScaleBenchmarkResult) -> dict[str, Any]:
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
        "repeatability": result.repeatability,
        "repetitions": [
            {
                "repetition": repetition.repetition,
                "scenarios": [
                    _scenario_payload(summary) for summary in repetition.scenarios
                ],
            }
            for repetition in result.repetitions
        ],
    }


def _scenario_payload(summary: ScenarioSummary) -> dict[str, Any]:
    return {
        "name": summary.name,
        "method": summary.method,
        "route_template": summary.route_template,
        "status_code": summary.status_code,
        "sample_count": summary.sample_count,
        "latency_ms": {
            "minimum": summary.latency_ms.minimum,
            "p50": summary.latency_ms.p50,
            "p95": summary.latency_ms.p95,
            "maximum": summary.latency_ms.maximum,
        },
        "db_statements": _numeric_payload(summary.db_statements),
        "db_selects": _numeric_payload(summary.db_selects),
        "response_records": _numeric_payload(summary.response_records),
        "response_bytes": _numeric_payload(summary.response_bytes),
        "scale_driver": summary.scale_driver,
        "scale_driver_count": summary.scale_driver_count,
    }


def _numeric_payload(summary: Any) -> dict[str, float]:
    return {
        "minimum": summary.minimum,
        "median": summary.median,
        "maximum": summary.maximum,
    }


def _triple(summary: Any) -> str:
    return "/".join(
        (_number(summary.minimum), _number(summary.median), _number(summary.maximum))
    )


def _number(value: int | float) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.6f}".rstrip("0").rstrip(".")


__all__ = [
    "BenchmarkReport",
    "PossibleNPlusOneObservation",
    "RuntimeSummary",
    "build_report",
    "nearest_rank",
    "possible_n_plus_one",
    "render_json",
    "render_markdown",
]
