from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from tools.performance.dataset import (
    DatasetManifest,
    ScaleDefinition,
)
from tools.performance.report import RuntimeSummary
from tools.performance.request_connection_report import (
    CONTROL_SCENARIO,
    SCENARIO_NAMES,
    ComparisonGateError,
    build_comparison_report,
    ensure_report_passes,
    load_baseline_report,
    render_json,
    render_markdown,
)
from tools.performance.runner import (
    NumericSummary,
    RepetitionBenchmarkResult,
    ScaleBenchmarkResult,
    ScenarioSummary,
    TimingSummary,
)


MICRO = ScaleDefinition("micro", 1, 2, 2, 4, 8, 2)
FORBIDDEN_KEY = re.compile(
    r"(?:^|_)(?:path|url|sql|body|content|student_name|question_text|request_id|"
    r"hostname|username|environment|command|raw_samples?)(?:$|_)",
    re.IGNORECASE,
)


def _metric(value: float) -> dict[str, float]:
    return {"minimum": value, "median": value, "maximum": value}


def _baseline_payload(*, scales: tuple[str, ...] = ("small", "medium")) -> dict:
    scale_payloads = []
    for scale in scales:
        repetitions = []
        for repetition in (1, 2):
            scenarios = []
            for index, name in enumerate(SCENARIO_NAMES, start=1):
                scenarios.append(
                    {
                        "name": name,
                        "status_code": 200,
                        "sample_count": 20,
                        "latency_ms": {
                            "minimum": 80.0,
                            "p50": 100.0 + repetition,
                            "p95": 120.0,
                            "maximum": 130.0,
                        },
                        "db_statements": _metric(
                            11.0 if name == CONTROL_SCENARIO else 100.0
                        ),
                        "db_selects": _metric(float(index)),
                        "response_records": _metric(float(index + 1)),
                        "source_path": r"C:\private\generated.db",
                        "raw_samples": [
                            {"sql": "SELECT private", "request_body": "private"}
                        ],
                    }
                )
            repetitions.append({"repetition": repetition, "scenarios": scenarios})
        scale_payloads.append(
            {
                "manifest": {"scale": scale, "seed": 126},
                "repeatability": "passed",
                "repetitions": repetitions,
                "database_path": r"C:\private\generated.db",
            }
        )
    return {
        "package": "P1-26",
        "code_sha": "a" * 40,
        "seed": 126,
        "repeatability": "passed",
        "scales": scale_payloads,
        "environment": {"hostname": "private-host"},
    }


def _write_baseline(tmp_path: Path, payload: dict | None = None) -> Path:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(payload or _baseline_payload()),
        encoding="utf-8",
    )
    return baseline


def _summary(
    name: str, *, scale: str, statements: float | None = None
) -> ScenarioSummary:
    index = SCENARIO_NAMES.index(name) + 1
    latency = 70.0 if scale == "medium" and name != "training.diagnosis" else 95.0
    return ScenarioSummary(
        name=name,
        method="GET" if name == CONTROL_SCENARIO else "POST",
        route_template="/allowlisted",
        status_code=200,
        sample_count=20,
        latency_ms=TimingSummary(60.0, latency, 80.0, 90.0),
        db_statements=NumericSummary(
            statements
            if statements is not None
            else (11.0 if name == CONTROL_SCENARIO else 10.0),
            statements
            if statements is not None
            else (11.0 if name == CONTROL_SCENARIO else 10.0),
            statements
            if statements is not None
            else (11.0 if name == CONTROL_SCENARIO else 10.0),
        ),
        db_selects=NumericSummary(float(index), float(index), float(index)),
        response_records=NumericSummary(
            float(index + 1),
            float(index + 1),
            float(index + 1),
        ),
        response_bytes=NumericSummary(100.0, 100.0, 100.0),
        scale_driver="students",
        scale_driver_count=2,
    )


def _result(scale: str) -> ScaleBenchmarkResult:
    manifest = DatasetManifest(
        scale_name=scale,
        seed=126,
        table_counts=(("students", 2),),
        grading_database_bytes=1_000,
        question_bank_database_bytes=2_000,
        backup_database_bytes=300,
        generated_asset_bytes=136,
    )
    scenarios = tuple(_summary(name, scale=scale) for name in SCENARIO_NAMES)
    return ScaleBenchmarkResult(
        manifest=manifest,
        repetitions=(
            RepetitionBenchmarkResult(1, scenarios),
            RepetitionBenchmarkResult(2, scenarios),
        ),
        repeatability="passed",
    )


def _legacy_result(scale: str) -> ScaleBenchmarkResult:
    optimized = _result(scale)
    scenarios = tuple(
        replace(
            summary,
            latency_ms=TimingSummary(90.0, 100.0, 110.0, 120.0),
            db_statements=NumericSummary(
                11.0 if summary.name == CONTROL_SCENARIO else 100.0,
                11.0 if summary.name == CONTROL_SCENARIO else 100.0,
                11.0 if summary.name == CONTROL_SCENARIO else 100.0,
            ),
        )
        for summary in optimized.repetitions[0].scenarios
    )
    return replace(
        optimized,
        repetitions=(
            RepetitionBenchmarkResult(1, scenarios),
            RepetitionBenchmarkResult(2, scenarios),
        ),
    )


def _report(
    tmp_path: Path,
    *,
    optimized_results: tuple[ScaleBenchmarkResult, ...] | None = None,
):
    baseline = load_baseline_report(
        _write_baseline(tmp_path),
        scales=("small", "medium"),
        scenarios=SCENARIO_NAMES,
    )
    return build_comparison_report(
        baseline,
        (_legacy_result("small"), _legacy_result("medium")),
        optimized_results or (_result("small"), _result("medium")),
        code_sha="b" * 40,
        generated_at_utc="2026-07-14T04:00:00Z",
        runtime=RuntimeSummary("Windows-11", "3.12.1", "3.43.1", 8),
        data_scale_factor=0.1,
        warmups=3,
        samples=20,
        repetitions=2,
        limitations=(
            "Latency is machine-specific and is not a service-level objective.",
            "Each file is atomic, but sudden termination can leave a mixed old/new pair.",
        ),
    )


def _assert_safe_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            assert not FORBIDDEN_KEY.search(str(key)), key
            _assert_safe_keys(nested)
    elif isinstance(value, list):
        for item in value:
            _assert_safe_keys(item)


def test_report_compares_aggregates_and_emits_explicit_passing_gates(
    tmp_path: Path,
) -> None:
    report = _report(tmp_path)
    ensure_report_passes(report)
    payload = json.loads(render_json(report))
    markdown = render_markdown(report)

    _assert_safe_keys(payload)
    assert set(payload) == {
        "package",
        "p1_26_provenance_code_sha",
        "code_sha",
        "generated_at_utc",
        "runtime",
        "seed",
        "scales",
        "scenarios",
        "before_mode",
        "after_mode",
        "data_scale_factor",
        "warmup_count",
        "sample_count",
        "repetition_count",
        "repeatability",
        "comparisons",
        "gates",
        "overall_gate",
        "limitations",
    }
    assert payload["package"] == "P1-27"
    assert payload["overall_gate"] == "passed"
    assert len(payload["comparisons"]) == 2 * 2 * len(SCENARIO_NAMES)
    assert all(
        item["status"]["before"] == item["status"]["after"] == 200
        for item in payload["comparisons"]
    )
    assert all(item["response_records"]["equal"] for item in payload["comparisons"])
    assert all(gate["passed"] for gate in payload["gates"])

    control = next(
        item
        for item in payload["comparisons"]
        if item["scale"] == "medium"
        and item["repetition"] == 1
        and item["scenario"] == CONTROL_SCENARIO
    )
    assert control["db_statements"]["before"] == control["db_statements"]["after"]
    assert control["db_statements"]["gate_required"] is False
    assert control["latency_p50_ms"]["gate_required"] is False

    for expected in (
        "before p50 ms",
        "after p50 ms",
        "DB statements",
        "DB selects",
        "response records",
        "statement reduction",
        "medium p50 improvement",
        "overall gate",
        "mixed old/new pair",
    ):
        assert expected in markdown
    for rendered in (json.dumps(payload), markdown):
        assert not re.search(r"[A-Za-z]:[\\/]", rendered)
        assert ".worktrees" not in rendered.casefold()
        assert "user_data" not in rendered.casefold()
        assert "select private" not in rendered.casefold()
        assert "raw_samples" not in rendered.casefold()
