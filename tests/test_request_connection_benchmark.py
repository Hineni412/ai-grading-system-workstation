from __future__ import annotations

import inspect
import json
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tools.benchmark_api_db import (
    _best_effort_unlink as p1_26_best_effort_unlink,
)
from tools.benchmark_api_db import _restore_output as p1_26_restore_output
from tools.benchmark_request_connections import build_parser, publish_report
from tools.performance.dataset import (
    DatasetManifest,
    ScaleDefinition,
    build_benchmark_dataset,
)
from tools.performance.report import RuntimeSummary
from tools.performance.request_connection_report import (
    CONTROL_SCENARIO,
    SCENARIO_NAMES,
    TARGET_SCENARIOS,
    ComparisonGateError,
    GateSummary,
    build_comparison_report,
    ensure_report_passes,
    load_baseline_report,
    render_json,
    render_markdown,
)
from tools.performance.runner import (
    BenchmarkRunError,
    NumericSummary,
    RepetitionBenchmarkResult,
    ScaleBenchmarkResult,
    ScenarioSummary,
    TimingSummary,
    run_scale,
)


MICRO = ScaleDefinition("micro", 1, 2, 2, 4, 8, 2, 2)
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
                        "db_statements": _metric(11.0 if name == CONTROL_SCENARIO else 100.0),
                        "db_selects": _metric(float(index)),
                        "response_records": _metric(float(index + 1)),
                        "source_path": r"C:\private\generated.db",
                        "raw_samples": [{"sql": "SELECT private", "request_body": "private"}],
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


def _summary(name: str, *, scale: str, statements: float | None = None) -> ScenarioSummary:
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
            statements if statements is not None else (11.0 if name == CONTROL_SCENARIO else 10.0),
            statements if statements is not None else (11.0 if name == CONTROL_SCENARIO else 10.0),
            statements if statements is not None else (11.0 if name == CONTROL_SCENARIO else 10.0),
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


def test_cli_defaults_match_p1_26_and_exact_task_5_scope() -> None:
    args = build_parser().parse_args([])

    assert args.seed == 126
    assert args.scales == ["small", "medium", "large_5pct"]
    assert args.warmups == 3
    assert args.data_scale_factor == 0.1
    assert args.samples == 2
    assert args.repetitions == 2
    assert args.baseline.as_posix().endswith(
        "tools/performance/baselines/p1-26-api-db-baseline.json"
    )
    assert args.output_json.as_posix().endswith(
        "output/performance/p1-27-request-connection-comparison.json"
    )
    assert args.output_markdown.as_posix().endswith(
        "output/performance/p1-27-request-connection-comparison.md"
    )

    for option in ("--warmups", "--samples", "--repetitions"):
        with pytest.raises(SystemExit):
            build_parser().parse_args([option, "0"])


def test_amended_cli_defaults_use_ten_percent_two_sample_workload() -> None:
    import tools.benchmark_request_connections as cli_module

    args = build_parser().parse_args([])

    assert args.scales == ["small", "medium", "large_5pct"]
    assert args.data_scale_factor == 0.1
    assert args.warmups == 3
    assert args.samples == 2
    assert args.repetitions == 2

    scaled = tuple(cli_module._scaled_scale(scale, args.data_scale_factor) for scale in cli_module.SCALES)
    assert tuple(scale.name for scale in scaled) == ("small", "medium", "large_5pct")
    assert tuple(scale.counts for scale in scaled) == (
        (1, 3, 1, 30, 20, 1, 1),
        (1, 20, 2, 2_000, 200, 10, 5),
        (1, 2, 1, 750, 50, 2, 1),
    )


def test_legacy_mode_is_benchmark_only_wiring_to_production_legacy_dependencies(
    tmp_path: Path,
) -> None:
    import tools.performance.runner as runner_module
    from backend.api.dependencies import (
        get_diagnosis_profile_service,
        get_practice_plan_service,
        get_request_diagnosis_profile_service,
        get_request_practice_plan_service,
    )
    from backend.performance.metrics import InMemoryPerformanceSink

    dataset = build_benchmark_dataset(tmp_path / "dataset", MICRO, seed=126)
    app = runner_module._build_app(
        dataset,
        InMemoryPerformanceSink(),
        connection_mode="legacy_per_call",
    )

    assert (
        app.dependency_overrides[get_request_diagnosis_profile_service]
        is app.dependency_overrides[get_diagnosis_profile_service]
    )
    assert (
        app.dependency_overrides[get_request_practice_plan_service]
        is app.dependency_overrides[get_practice_plan_service]
    )
    assert "legacy_per_call" not in inspect.getsource(
        __import__("backend.api.dependencies", fromlist=["*"])
    )


def test_legacy_mode_uses_generated_paths_before_testclient_lifespan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import path_manager

    dataset = build_benchmark_dataset(tmp_path / "dataset", MICRO, seed=126)
    sentinel = object()

    class ForbiddenDefaultPaths:
        def __init__(self) -> None:
            raise AssertionError("repository path configuration must not be constructed")

    monkeypatch.setattr(path_manager, "_instance", sentinel)
    monkeypatch.setattr(path_manager, "PathManager", ForbiddenDefaultPaths)

    result = run_scale(
        dataset,
        warmups=1,
        samples=1,
        repetitions=2,
        scenario_names=("training.diagnosis", "training.plan.preview"),
        connection_mode="legacy_per_call",
    )

    assert result.repeatability == "passed"
    assert all(
        summary.status_code == 200
        for repetition in result.repetitions
        for summary in repetition.scenarios
    )
    assert path_manager._instance is sentinel


def test_report_uses_same_dataset_legacy_measurements_not_p1_26_numbers(
    tmp_path: Path,
) -> None:
    baseline_payload = _baseline_payload(scales=("medium",))
    for repetition in baseline_payload["scales"][0]["repetitions"]:
        for scenario in repetition["scenarios"]:
            scenario["latency_ms"]["p50"] = 1.0
            scenario["db_statements"] = _metric(1.0)
    baseline = load_baseline_report(
        _write_baseline(tmp_path, baseline_payload),
        scales=("medium",),
        scenarios=SCENARIO_NAMES,
    )
    optimized = _result("medium")
    legacy_scenarios = tuple(
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
    legacy = replace(
        optimized,
        repetitions=(
            RepetitionBenchmarkResult(1, legacy_scenarios),
            RepetitionBenchmarkResult(2, legacy_scenarios),
        ),
    )

    report = build_comparison_report(
        baseline,
        (legacy,),
        (optimized,),
        code_sha="b" * 40,
        generated_at_utc="2026-07-14T04:00:00Z",
        runtime=RuntimeSummary("Windows-11", "3.12.1", "3.43.1", 8),
        data_scale_factor=0.1,
        warmups=3,
        samples=20,
        repetitions=2,
        limitations=("Reduced statistical confidence and capacity coverage.",),
    )

    ensure_report_passes(report)
    payload = json.loads(render_json(report))
    target = next(
        item
        for item in payload["comparisons"]
        if item["repetition"] == 1 and item["scenario"] == "training.plan.preview"
    )
    assert target["latency_p50_ms"]["before"] == 100.0
    assert target["db_statements"]["before"] == 100.0
    assert payload["p1_26_provenance_code_sha"] == "a" * 40
    assert payload["before_mode"] == "legacy_per_call"
    assert payload["after_mode"] == "request_scoped"
    assert payload["data_scale_factor"] == 0.1


def test_report_rejects_legacy_and_optimized_dataset_mismatch(tmp_path: Path) -> None:
    baseline = load_baseline_report(
        _write_baseline(tmp_path, _baseline_payload(scales=("medium",))),
        scales=("medium",),
        scenarios=SCENARIO_NAMES,
    )
    legacy = _legacy_result("medium")
    optimized = _result("medium")
    optimized = replace(
        optimized,
        manifest=replace(
            optimized.manifest,
            table_counts=(("students", 3),),
        ),
    )

    with pytest.raises(ValueError, match="comparison:dataset_mismatch"):
        build_comparison_report(
            baseline,
            (legacy,),
            (optimized,),
            code_sha="b" * 40,
            generated_at_utc="2026-07-14T04:00:00Z",
            runtime=RuntimeSummary("Windows-11", "3.12.1", "3.43.1", 8),
            data_scale_factor=0.1,
            warmups=3,
            samples=20,
            repetitions=2,
            limitations=("Reduced statistical confidence and capacity coverage.",),
        )


def test_cli_supports_direct_script_execution() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "tools/benchmark_request_connections.py", "--help"],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "--baseline" in completed.stdout


def test_committed_baseline_loader_selects_only_active_comparable_scenarios() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    baseline = load_baseline_report(
        repository_root
        / "tools/performance/baselines/p1-26-api-db-baseline.json",
        scales=("small", "medium", "large_5pct"),
        scenarios=SCENARIO_NAMES,
    )

    assert baseline.package == "P1-26"
    assert baseline.code_sha == "6bb53c338d23e530ed2890afbdecce0ae6ac9fb9"
    assert baseline.seed == 126
    assert baseline.repeatability == "passed"
    assert len(baseline.measurements) == 3 * 2 * len(SCENARIO_NAMES)
    assert {item.scenario for item in baseline.measurements} == set(SCENARIO_NAMES)
    assert all(item.status_code == 200 and item.sample_count == 20 for item in baseline.measurements)
    assert "private" not in repr(baseline).casefold()


def test_baseline_loader_ignores_non_allowlisted_fields_and_rejects_bad_repeatability(
    tmp_path: Path,
) -> None:
    baseline = load_baseline_report(
        _write_baseline(tmp_path),
        scales=("small", "medium"),
        scenarios=SCENARIO_NAMES,
    )

    assert len(baseline.measurements) == 2 * 2 * len(SCENARIO_NAMES)
    assert "private" not in repr(baseline).casefold()
    assert "select private" not in repr(baseline).casefold()

    payload = _baseline_payload()
    payload["scales"][0]["repetitions"][1]["scenarios"][0]["db_selects"] = _metric(999.0)
    with pytest.raises(ValueError, match="baseline:repeatability_failed"):
        load_baseline_report(
            _write_baseline(tmp_path, payload),
            scales=("small", "medium"),
            scenarios=SCENARIO_NAMES,
        )


def test_runner_filters_scenarios_without_changing_default_behavior(tmp_path: Path) -> None:
    dataset = build_benchmark_dataset(tmp_path / "dataset", MICRO, seed=126)

    result = run_scale(
        dataset,
        warmups=1,
        samples=1,
        repetitions=2,
        scenario_names=SCENARIO_NAMES,
    )

    assert result.repeatability == "passed"
    assert len(result.repetitions) == 2
    assert tuple(item.name for item in result.repetitions[0].scenarios) == SCENARIO_NAMES
    assert all(item.status_code == 200 for item in result.repetitions[0].scenarios)
    assert all(item.response_records.median > 0 for item in result.repetitions[0].scenarios)


def test_runner_rejects_unknown_or_duplicate_filters_before_app_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.performance.runner as runner_module

    dataset = build_benchmark_dataset(tmp_path / "dataset", MICRO, seed=126)
    monkeypatch.setattr(
        runner_module,
        "_build_app",
        lambda *_args, **_kwargs: pytest.fail("app must not start for invalid filter"),
    )

    with pytest.raises(BenchmarkRunError, match="scenario_filter:unknown"):
        runner_module.run_scale(dataset, scenario_names=("not.allowlisted",))
    with pytest.raises(BenchmarkRunError, match="scenario_filter:duplicate"):
        runner_module.run_scale(
            dataset,
            scenario_names=(SCENARIO_NAMES[0], SCENARIO_NAMES[0]),
        )


def test_report_compares_aggregates_and_emits_explicit_passing_gates(tmp_path: Path) -> None:
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
    assert all(item["status"]["before"] == item["status"]["after"] == 200 for item in payload["comparisons"])
    assert all(item["response_records"]["equal"] for item in payload["comparisons"])
    assert all(gate["passed"] for gate in payload["gates"])

    control = next(
        item
        for item in payload["comparisons"]
        if item["scale"] == "medium" and item["repetition"] == 1 and item["scenario"] == CONTROL_SCENARIO
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


def test_small_only_micro_has_no_medium_latency_requirement(tmp_path: Path) -> None:
    baseline = load_baseline_report(
        _write_baseline(tmp_path),
        scales=("small",),
        scenarios=SCENARIO_NAMES,
    )
    report = build_comparison_report(
        baseline,
        (_legacy_result("small"),),
        (_result("small"),),
        code_sha="b" * 40,
        generated_at_utc="2026-07-14T04:00:00Z",
        runtime=RuntimeSummary("Windows-11", "3.12.1", "3.43.1", 8),
        data_scale_factor=0.1,
        warmups=1,
        samples=20,
        repetitions=2,
        limitations=("Latency is machine-specific.",),
    )

    ensure_report_passes(report)
    latency_gate = next(
        gate for gate in report.gates if gate.name == "medium_p50_improvement"
    )
    assert latency_gate.required_count == 0
    assert latency_gate.passed_count == 0
    assert latency_gate.passed is True


def test_required_statement_latency_status_and_record_gates_block_publication(
    tmp_path: Path,
) -> None:
    passing = _result("medium")
    scenarios = list(passing.repetitions[0].scenarios)

    statement_index = SCENARIO_NAMES.index("training.diagnosis")
    scenarios[statement_index] = _summary(
        "training.diagnosis",
        scale="medium",
        statements=100.0,
    )
    statement_failure = replace(
        passing,
        repetitions=(
            replace(passing.repetitions[0], scenarios=tuple(scenarios)),
            replace(passing.repetitions[1], scenarios=tuple(scenarios)),
        ),
    )
    with pytest.raises(ComparisonGateError, match="target_statement_reduction"):
        ensure_report_passes(
            _report(
                tmp_path,
                optimized_results=(_result("small"), statement_failure),
            )
        )

    scenarios = list(passing.repetitions[0].scenarios)
    graph_index = SCENARIO_NAMES.index("graph.evidence")
    scenarios[graph_index] = replace(
        scenarios[graph_index],
        latency_ms=TimingSummary(80.0, 90.0, 100.0, 110.0),
    )
    latency_failure = replace(
        passing,
        repetitions=(
            replace(passing.repetitions[0], scenarios=tuple(scenarios)),
            replace(passing.repetitions[1], scenarios=tuple(scenarios)),
        ),
    )
    with pytest.raises(ComparisonGateError, match="medium_p50_improvement"):
        ensure_report_passes(
            _report(
                tmp_path,
                optimized_results=(_result("small"), latency_failure),
            )
        )

    scenarios = list(passing.repetitions[0].scenarios)
    scenarios[0] = replace(scenarios[0], status_code=503)
    status_failure = replace(
        passing,
        repetitions=(
            replace(passing.repetitions[0], scenarios=tuple(scenarios)),
            replace(passing.repetitions[1], scenarios=tuple(scenarios)),
        ),
    )
    with pytest.raises(ComparisonGateError, match="status_equality"):
        ensure_report_passes(
            _report(
                tmp_path,
                optimized_results=(_result("small"), status_failure),
            )
        )

    scenarios = list(passing.repetitions[0].scenarios)
    scenarios[0] = replace(
        scenarios[0],
        response_records=NumericSummary(999.0, 999.0, 999.0),
    )
    record_failure = replace(
        passing,
        repetitions=(
            replace(passing.repetitions[0], scenarios=tuple(scenarios)),
            replace(passing.repetitions[1], scenarios=tuple(scenarios)),
        ),
    )
    with pytest.raises(ComparisonGateError, match="response_record_equality"):
        ensure_report_passes(
            _report(
                tmp_path,
                optimized_results=(_result("small"), record_failure),
            )
        )


def test_equal_zero_response_records_block_publication_with_explicit_gate(
    tmp_path: Path,
) -> None:
    baseline = load_baseline_report(
        _write_baseline(tmp_path, _baseline_payload(scales=("small",))),
        scales=("small",),
        scenarios=SCENARIO_NAMES,
    )

    def with_zero_records(result: ScaleBenchmarkResult) -> ScaleBenchmarkResult:
        return replace(
            result,
            repetitions=tuple(
                replace(
                    repetition,
                    scenarios=tuple(
                        replace(
                            scenario,
                            response_records=NumericSummary(0.0, 0.0, 0.0),
                        )
                        for scenario in repetition.scenarios
                    ),
                )
                for repetition in result.repetitions
            ),
        )

    report = build_comparison_report(
        baseline,
        (with_zero_records(_legacy_result("small")),),
        (with_zero_records(_result("small")),),
        code_sha="b" * 40,
        generated_at_utc="2026-07-14T04:00:00Z",
        runtime=RuntimeSummary("Windows-11", "3.12.1", "3.43.1", 8),
        data_scale_factor=0.1,
        warmups=3,
        samples=20,
        repetitions=2,
        limitations=("Reduced statistical confidence and capacity coverage.",),
    )

    equality_gate = next(
        gate for gate in report.gates if gate.name == "response_record_equality"
    )
    expected_count = 2 * len(SCENARIO_NAMES)
    assert equality_gate.required_count == equality_gate.passed_count == expected_count
    with pytest.raises(ComparisonGateError, match="nonzero_response_records"):
        ensure_report_passes(report)
    nonzero_gate = next(
        gate for gate in report.gates if gate.name == "nonzero_response_records"
    )
    assert nonzero_gate.required_count == expected_count
    assert nonzero_gate.passed_count == 0
    assert nonzero_gate.passed is False


def test_zero_minimum_response_sample_is_preserved_and_blocks_publication(
    tmp_path: Path,
) -> None:
    baseline = load_baseline_report(
        _write_baseline(tmp_path, _baseline_payload(scales=("small",))),
        scales=("small",),
        scenarios=SCENARIO_NAMES,
    )

    def with_mixed_records(result: ScaleBenchmarkResult) -> ScaleBenchmarkResult:
        return replace(
            result,
            repetitions=tuple(
                replace(
                    repetition,
                    scenarios=tuple(
                        replace(
                            scenario,
                            response_records=NumericSummary(0.0, 1.0, 1.0),
                        )
                        for scenario in repetition.scenarios
                    ),
                )
                for repetition in result.repetitions
            ),
        )

    report = build_comparison_report(
        baseline,
        (with_mixed_records(_legacy_result("small")),),
        (with_mixed_records(_result("small")),),
        code_sha="b" * 40,
        generated_at_utc="2026-07-14T04:00:00Z",
        runtime=RuntimeSummary("Windows-11", "3.12.1", "3.43.1", 8),
        data_scale_factor=0.1,
        warmups=3,
        samples=20,
        repetitions=2,
        limitations=("Reduced statistical confidence and capacity coverage.",),
    )

    with pytest.raises(ComparisonGateError, match="nonzero_response_records"):
        ensure_report_passes(report)
    nonzero_gate = next(
        gate for gate in report.gates if gate.name == "nonzero_response_records"
    )
    assert (
        nonzero_gate.required_count,
        nonzero_gate.passed_count,
    ) == (2 * len(SCENARIO_NAMES), 0)
    payload = json.loads(render_json(report))
    assert payload["comparisons"][0]["response_records"] == {
        "before": {"minimum": 0.0, "median": 1.0, "maximum": 1.0},
        "after": {"minimum": 0.0, "median": 1.0, "maximum": 1.0},
        "equal": True,
    }


def test_unavailable_nonzero_sample_gate_is_not_reported_as_passed(
    tmp_path: Path,
) -> None:
    report = _report(tmp_path)
    gates = tuple(
        GateSummary(gate.name, gate.required_count, None, None)
        if gate.name == "nonzero_response_records"
        else gate
        for gate in report.gates
    )
    diagnostic = replace(report, gates=gates, overall_gate="quick_diagnostic")

    with pytest.raises(ComparisonGateError, match="nonzero_response_records"):
        ensure_report_passes(diagnostic)
    payload = json.loads(render_json(diagnostic))
    unavailable_gate = next(
        gate for gate in payload["gates"] if gate["name"] == "nonzero_response_records"
    )
    expected_count = 2 * 2 * len(SCENARIO_NAMES)
    assert unavailable_gate == {
        "name": "nonzero_response_records",
        "required_count": expected_count,
        "passed_count": None,
        "passed": None,
        "result": "not_evaluated",
    }
    markdown = render_markdown(diagnostic)
    assert (
        f"| non-zero response records | {expected_count} | unavailable | "
        "not_evaluated |"
    ) in markdown
    assert "overall gate: `quick_diagnostic`" in markdown


def test_publish_report_reuses_p1_26_recovery_primitives_and_contract() -> None:
    import tools.benchmark_request_connections as cli_module

    assert cli_module._restore_output is p1_26_restore_output
    assert cli_module._best_effort_unlink is p1_26_best_effort_unlink
    contract = inspect.getdoc(publish_report)
    assert contract is not None
    assert "Each destination is replaced atomically" in contract
    assert "caught BaseException" in contract
    assert "termination or power loss" in contract
    assert "mixed old/new pair" in contract


@pytest.mark.parametrize("failure_type", [OSError, KeyboardInterrupt])
def test_publish_report_restores_both_outputs_after_catchable_second_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_type: type[BaseException],
) -> None:
    import tools.benchmark_request_connections as cli_module

    report = _report(tmp_path)
    json_output = tmp_path / "comparison.json"
    markdown_output = tmp_path / "comparison.md"
    json_output.write_text("old-json", encoding="utf-8")
    markdown_output.write_text("old-markdown", encoding="utf-8")
    real_replace = cli_module.os.replace
    injected = False

    def interrupt_second_final_replace(source: object, destination: object) -> None:
        nonlocal injected
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            not injected
            and destination_path == markdown_output
            and source_path.name.endswith(".tmp")
            and not source_path.name.endswith(".restore.tmp")
        ):
            injected = True
            raise failure_type("injected second replace failure")
        real_replace(source, destination)

    monkeypatch.setattr(cli_module.os, "replace", interrupt_second_final_replace)

    with pytest.raises(failure_type, match="injected second replace failure"):
        publish_report(report, json_output, markdown_output)

    assert json_output.read_text(encoding="utf-8") == "old-json"
    assert markdown_output.read_text(encoding="utf-8") == "old-markdown"
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "baseline.json",
        "comparison.json",
        "comparison.md",
    ]
