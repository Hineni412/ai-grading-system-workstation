from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Timing:
    p50_ms: float
    p95_ms: float


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    name: str
    decision: str
    reason: str
    dataset: dict[str, Any]
    before: Timing | None = None
    after: Timing | None = None
    p50_improvement_percent: float | None = None
    p95_change_percent: float | None = None
    output_equivalent: bool | None = None
    peak_memory_bytes: int = 0
    disk_bytes: int = 0
    invalidation: str = "not_applicable"
    concurrency: str = "not_applicable"


@dataclass(frozen=True, slots=True)
class EvidenceOptimizationReport:
    package: str
    code_sha: str
    generated_at_utc: str
    environment: dict[str, Any]
    thresholds: dict[str, float]
    candidates: tuple[CandidateEvidence, ...]
    limitations: tuple[str, ...]


def qualifies(before: Timing, after: Timing) -> bool:
    if before.p50_ms <= 0 or before.p95_ms <= 0:
        return False
    p50_improvement = (before.p50_ms - after.p50_ms) / before.p50_ms
    p95_change = (after.p95_ms - before.p95_ms) / before.p95_ms
    return p50_improvement >= 0.20 and p95_change <= 0.05


def timing_changes(
    before: Timing,
    after: Timing,
) -> tuple[float, float]:
    p50_improvement = (
        (before.p50_ms - after.p50_ms) / before.p50_ms * 100
        if before.p50_ms > 0
        else 0.0
    )
    p95_change = (
        (after.p95_ms - before.p95_ms) / before.p95_ms * 100
        if before.p95_ms > 0
        else 0.0
    )
    return round(p50_improvement, 3), round(p95_change, 3)


def render_json(report: EvidenceOptimizationReport) -> str:
    return json.dumps(
        asdict(report),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def render_markdown(report: EvidenceOptimizationReport) -> str:
    lines = [
        "# P3-18 evidence-based performance decisions",
        "",
        f"- code SHA: `{report.code_sha}`",
        f"- generated at UTC: `{report.generated_at_utc}`",
        (
            "- gate: p50 improvement >= "
            f"{report.thresholds['minimum_p50_improvement_percent']:.0f}%, "
            "p95 regression <= "
            f"{report.thresholds['maximum_p95_regression_percent']:.0f}%"
        ),
        "",
        "## Environment",
        "",
    ]
    lines.extend(
        f"- {key}: `{value}`" for key, value in sorted(report.environment.items())
    )
    lines.extend(
        [
            "",
            "## Candidate decisions",
            "",
            "| candidate | decision | before p50 ms | before p95 ms | after p50 ms | after p95 ms | p50 improvement | p95 change | peak memory bytes | disk bytes |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in report.candidates:
        before = item.before
        after = item.after
        lines.append(
            f"| {item.name} | {item.decision} | "
            f"{_metric(before, 'p50_ms')} | {_metric(before, 'p95_ms')} | "
            f"{_metric(after, 'p50_ms')} | {_metric(after, 'p95_ms')} | "
            f"{_percent(item.p50_improvement_percent)} | "
            f"{_percent(item.p95_change_percent)} | "
            f"{item.peak_memory_bytes} | {item.disk_bytes} |"
        )
    for item in report.candidates:
        lines.extend(
            [
                "",
                f"### {item.name}",
                "",
                f"- reason: {item.reason}",
                f"- dataset: {json.dumps(item.dataset, ensure_ascii=False, sort_keys=True)}",
                f"- output equivalent: {item.output_equivalent}",
                f"- invalidation: {item.invalidation}",
                f"- concurrency: {item.concurrency}",
            ]
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report.limitations)
    return "\n".join(lines) + "\n"


def _metric(timing: Timing | None, name: str) -> str:
    if timing is None:
        return "n/a"
    return f"{getattr(timing, name):.3f}"


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}%"


__all__ = [
    "CandidateEvidence",
    "EvidenceOptimizationReport",
    "Timing",
    "qualifies",
    "render_json",
    "render_markdown",
    "timing_changes",
]
