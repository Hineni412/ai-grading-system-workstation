from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tools.benchmark_api_db import build_parser, publish_report
from tools.performance.dataset import DatasetManifest
from tools.performance.report import (
    build_report,
    nearest_rank,
    possible_n_plus_one,
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


FORBIDDEN_KEY = re.compile(
    r"(?:^|_)(?:path|url|sql|body|content|student_name|question_text|request_id|"
    r"hostname|username|environment|command)(?:$|_)",
    re.IGNORECASE,
)


def _summary(*, scale_driver_count: int, select_median: int) -> ScenarioSummary:
    return ScenarioSummary(
        name="graph.rows",
        method="POST",
        route_template="/api/graph/rows",
        status_code=200,
        sample_count=20,
        latency_ms=TimingSummary(1.0, 2.0, 3.0, 4.0),
        db_statements=NumericSummary(
            float(select_median + 2),
            float(select_median + 2),
            float(select_median + 3),
        ),
        db_selects=NumericSummary(
            float(select_median),
            float(select_median),
            float(select_median + 1),
        ),
        response_records=NumericSummary(2.0, 2.0, 2.0),
        response_bytes=NumericSummary(100.0, 100.0, 101.0),
        scale_driver="students",
        scale_driver_count=scale_driver_count,
    )


def _scale(name: str, *, students: int, select_median: int) -> ScaleBenchmarkResult:
    manifest = DatasetManifest(
        scale_name=name,
        seed=126,
        table_counts=(
            ("students", students),
            ("questions", students * 2),
        ),
        grading_database_bytes=1_000,
        question_bank_database_bytes=2_000,
        backup_database_bytes=300,
        generated_asset_bytes=136,
    )
    summary = _summary(
        scale_driver_count=students,
        select_median=select_median,
    )
    return ScaleBenchmarkResult(
        manifest=manifest,
        repetitions=(
            RepetitionBenchmarkResult(1, (summary,)),
            RepetitionBenchmarkResult(2, (summary,)),
        ),
        repeatability="passed",
    )


def _synthetic_report():
    return build_report(
        (_scale("small", students=2, select_median=2),
         _scale("large_5pct", students=52, select_median=8)),
        code_sha="a" * 40,
        generated_at_utc="2026-07-13T04:00:00Z",
        windows_version="Windows-11-generated",
        python_version="3.12.10",
        sqlite_version="3.43.1",
        logical_cpu_count=8,
        limitations=(
            "Only allowlisted SQLite connection boundaries are counted.",
            "Latency is machine-specific and is not a service-level objective.",
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


def test_nearest_rank_uses_finite_nonnegative_values() -> None:
    assert nearest_rank(list(range(1, 21)), 0.50) == 10
    assert nearest_rank(list(range(1, 21)), 0.95) == 19
    assert nearest_rank([3.0, 1.0, 2.0], 1.0) == 3.0

    for values, percentile in (([], 0.5), ([float("nan")], 0.5), ([-1], 0.5)):
        with pytest.raises(ValueError):
            nearest_rank(values, percentile)
    for percentile in (0.0, -0.1, 1.1):
        with pytest.raises(ValueError):
            nearest_rank([1.0], percentile)


def test_renderers_emit_only_aggregate_allowlisted_report_fields() -> None:
    report = _synthetic_report()
    rendered_json = render_json(report)
    rendered_markdown = render_markdown(report)
    payload = json.loads(rendered_json)

    _assert_safe_keys(payload)
    assert payload["package"] == "P1-26"
    assert payload["code_sha"] == "a" * 40
    assert payload["runtime"] == {
        "windows_version": "Windows-11-generated",
        "python_version": "3.12.10",
        "sqlite_version": "3.43.1",
        "logical_cpu_count": 8,
    }
    assert payload["repeatability"] == "passed"
    assert len(payload["scales"]) == 2
    assert len(payload["scales"][0]["repetitions"]) == 2
    scenario = payload["scales"][0]["repetitions"][0]["scenarios"][0]
    assert scenario["latency_ms"] == {
        "minimum": 1.0,
        "p50": 2.0,
        "p95": 3.0,
        "maximum": 4.0,
    }
    assert set(scenario) == {
        "name",
        "method",
        "route_template",
        "status_code",
        "sample_count",
        "latency_ms",
        "db_statements",
        "db_selects",
        "response_records",
        "response_bytes",
        "scale_driver",
        "scale_driver_count",
    }
    assert payload["observations"][0]["label"] == "possible_n_plus_one"
    assert payload["limitations"]

    for expected in (
        "a" * 40,
        "Windows-11-generated",
        "3.12.10",
        "3.43.1",
        "logical CPU",
        "small",
        "large_5pct",
        "p50",
        "p95",
        "minimum",
        "maximum",
        "DB statements",
        "DB selects",
        "response records",
        "response bytes",
        "repeatability",
        "limitations",
        "possible_n_plus_one",
    ):
        assert expected in rendered_markdown

    assert "large_5pct selects" in rendered_markdown
    assert "large selects" not in rendered_markdown

    for rendered in (rendered_json, rendered_markdown):
        assert not re.search(r"[A-Za-z]:[\\/]", rendered)
        assert ".worktrees" not in rendered.casefold()
        assert "user_data" not in rendered.casefold()
        assert "p1-26-small-graph.rows-" not in rendered


def test_possible_n_plus_one_uses_the_required_thresholds() -> None:
    small = _scale("small", students=2, select_median=2)
    large = _scale("large_5pct", students=52, select_median=8)

    observations = possible_n_plus_one((small, large))

    assert len(observations) == 1
    assert observations[0].label == "possible_n_plus_one"
    assert observations[0].small_select_median == 2
    assert observations[0].large_select_median == 8
    assert observations[0].small_driver_count == 2
    assert observations[0].large_driver_count == 52
    assert "recommend" not in repr(observations).casefold()

    below_select_delta = _scale("large_5pct", students=52, select_median=6)
    below_ratio = _scale("large_5pct", students=102, select_median=8)
    assert possible_n_plus_one((small, below_select_delta)) == ()
    assert possible_n_plus_one((small, below_ratio)) == ()


def test_cli_defaults_and_positive_argument_validation() -> None:
    args = build_parser().parse_args([])
    assert args.seed == 126
    assert args.scales == ["small", "medium", "large_5pct"]
    assert args.warmups == 3
    assert args.samples == 20
    assert args.repetitions == 2
    assert args.output_json.as_posix().endswith(
        "docs/performance/p1-26-api-db-baseline.json"
    )
    assert args.output_markdown.as_posix().endswith(
        "docs/performance/p1-26-api-db-baseline.md"
    )

    for option in ("--warmups", "--samples", "--repetitions"):
        with pytest.raises(SystemExit):
            build_parser().parse_args([option, "0"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--scales", "unknown"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--scales", "large"])


def test_cli_supports_direct_script_execution() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "tools/benchmark_api_db.py", "--help"],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "--scales" in completed.stdout


def test_atomic_publication_leaves_previous_pair_untouched_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.benchmark_api_db as cli_module

    json_output = tmp_path / "baseline.json"
    markdown_output = tmp_path / "baseline.md"
    json_output.write_text("old-json", encoding="utf-8")
    markdown_output.write_text("old-markdown", encoding="utf-8")

    def fail_markdown(_report) -> str:
        raise RuntimeError("synthetic render failure")

    monkeypatch.setattr(cli_module, "render_markdown", fail_markdown)

    with pytest.raises(RuntimeError, match="synthetic render failure"):
        publish_report(_synthetic_report(), json_output, markdown_output)

    assert json_output.read_text(encoding="utf-8") == "old-json"
    assert markdown_output.read_text(encoding="utf-8") == "old-markdown"
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "baseline.json",
        "baseline.md",
    ]


@pytest.mark.parametrize("json_exists,markdown_exists", [
    (True, True),
    (False, False),
    (True, False),
    (False, True),
])
@pytest.mark.parametrize("failure_type", [OSError, KeyboardInterrupt, SystemExit])
def test_second_replace_failure_restores_the_exact_previous_pair_and_reraises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    json_exists: bool,
    markdown_exists: bool,
    failure_type: type[BaseException],
) -> None:
    import tools.benchmark_api_db as cli_module

    json_output = tmp_path / "baseline.json"
    markdown_output = tmp_path / "baseline.md"
    if json_exists:
        json_output.write_text("old-json", encoding="utf-8")
    if markdown_exists:
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
        publish_report(_synthetic_report(), json_output, markdown_output)

    assert json_output.exists() is json_exists
    assert markdown_output.exists() is markdown_exists
    if json_exists:
        assert json_output.read_text(encoding="utf-8") == "old-json"
    if markdown_exists:
        assert markdown_output.read_text(encoding="utf-8") == "old-markdown"
    expected_names = sorted(
        name
        for name, exists in (
            ("baseline.json", json_exists),
            ("baseline.md", markdown_exists),
        )
        if exists
    )
    assert sorted(path.name for path in tmp_path.iterdir()) == expected_names
