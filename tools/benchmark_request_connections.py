from __future__ import annotations

import argparse
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.benchmark_api_db import _best_effort_unlink, _restore_output
from tools.performance.dataset import SCALES, build_benchmark_dataset
from tools.performance.report import RuntimeSummary
from tools.performance.request_connection_report import (
    SCENARIO_NAMES,
    ComparisonGateError,
    RequestConnectionReport,
    build_comparison_report,
    ensure_report_passes,
    load_baseline_report,
    render_json,
    render_markdown,
)
from tools.performance.runner import BenchmarkRunError, run_scale


DEFAULT_BASELINE = Path("docs/performance/p1-26-api-db-baseline.json")
DEFAULT_JSON = Path("docs/performance/p1-27-request-connection-comparison.json")
DEFAULT_MARKDOWN = Path("docs/performance/p1-27-request-connection-comparison.md")


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare P1-27 request connections with the P1-26 baseline"
    )
    parser.add_argument("--seed", type=int, default=126)
    parser.add_argument(
        "--scales",
        nargs="+",
        choices=[scale.name for scale in SCALES],
        default=[scale.name for scale in SCALES],
    )
    parser.add_argument("--warmups", type=_positive, default=3)
    parser.add_argument("--samples", type=_positive, default=20)
    parser.add_argument("--repetitions", type=_positive, default=2)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-markdown", type=Path, default=DEFAULT_MARKDOWN)
    return parser


def publish_report(
    report: RequestConnectionReport,
    json_output: Path,
    markdown_output: Path,
) -> None:
    """Publish two reports with the P1-26 recovery contract.

    Each destination is replaced atomically from a temporary file in the same
    directory. After a caught BaseException, the function uses the P1-26
    recovery primitives to restore both previous outputs and remove temporary
    files. Even a sudden termination or power loss between the two replacements
    can still leave a mixed old/new pair; regenerate or inspect both files
    before the next use. This is not a cross-file transaction.
    """
    json_text = render_json(report)
    markdown_text = render_markdown(report)
    json_output = Path(json_output)
    markdown_output = Path(markdown_output)
    if json_output.resolve() == markdown_output.resolve():
        raise ValueError("report destinations must be distinct")
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    json_temp = json_output.with_name(f".{json_output.name}.{token}.tmp")
    markdown_temp = markdown_output.with_name(f".{markdown_output.name}.{token}.tmp")
    previous_json = json_output.read_bytes() if json_output.exists() else None
    previous_markdown = markdown_output.read_bytes() if markdown_output.exists() else None
    try:
        json_temp.write_text(json_text, encoding="utf-8", newline="\n")
        markdown_temp.write_text(markdown_text, encoding="utf-8", newline="\n")
        os.replace(json_temp, json_output)
        os.replace(markdown_temp, markdown_output)
    except BaseException as publish_error:
        restore_errors: list[BaseException] = []
        for output, previous in (
            (json_output, previous_json),
            (markdown_output, previous_markdown),
        ):
            try:
                _restore_output(output, previous, token)
            except BaseException as restore_error:
                restore_errors.append(restore_error)
        if restore_errors:
            raise publish_error from BaseExceptionGroup(
                "report recovery failed",
                restore_errors,
            )
        raise
    finally:
        _best_effort_unlink(json_temp, markdown_temp)


def _code_sha() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    value = completed.stdout.strip().lower()
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise RuntimeError("invalid code revision")
    return value


def _windows_version() -> str:
    release, version, _service_pack, _product_type = platform.win32_ver()
    values = [value for value in (release, version) if value]
    return "Windows-" + "-".join(values or ["unknown"])


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repetitions != 2:
        print("comparison_failed comparison:requires_two_repetitions", file=sys.stderr)
        return 1
    scale_by_name = {scale.name: scale for scale in SCALES}
    try:
        baseline = load_baseline_report(
            args.baseline,
            scales=args.scales,
            scenarios=SCENARIO_NAMES,
        )
        if args.seed != baseline.seed:
            raise ValueError("comparison:seed_mismatch")
        with tempfile.TemporaryDirectory(prefix="p1-27-connection-benchmark-") as temp_dir:
            temp_root = Path(temp_dir)
            results = []
            for scale_name in args.scales:
                dataset = build_benchmark_dataset(
                    temp_root / scale_name,
                    scale_by_name[scale_name],
                    seed=args.seed,
                )
                results.append(
                    run_scale(
                        dataset,
                        warmups=args.warmups,
                        samples=args.samples,
                        repetitions=args.repetitions,
                        scenario_names=SCENARIO_NAMES,
                    )
                )
            report = build_comparison_report(
                baseline,
                results,
                code_sha=_code_sha(),
                generated_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                runtime=RuntimeSummary(
                    windows_version=_windows_version(),
                    python_version=platform.python_version(),
                    sqlite_version=sqlite3.sqlite_version,
                    logical_cpu_count=os.cpu_count() or 1,
                ),
                warmups=args.warmups,
                samples=args.samples,
                repetitions=args.repetitions,
                limitations=(
                    "Only aggregate measurements from generated benchmark data are included.",
                    "Before measurements come from the committed P1-26 baseline.",
                    "Latency is machine-specific and is not a service-level objective.",
                    "Each file is atomic, but sudden termination can leave a mixed old/new pair.",
                ),
            )
            ensure_report_passes(report)
            publish_report(report, args.output_json, args.output_markdown)
    except (BenchmarkRunError, ComparisonGateError, ValueError) as exc:
        print(f"comparison_failed {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("comparison_failed comparison:unexpected_error", file=sys.stderr)
        return 1

    scenario_count = len(results) * len(SCENARIO_NAMES)
    print(
        f"comparison_passed scenarios={scenario_count}/{scenario_count} "
        "repeatability=passed gates=passed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main", "publish_report"]
