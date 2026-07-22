"""P3-01 structural baseline public-command contract."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOL = PROJECT_ROOT / "tools" / "build_p3_01_baseline.py"


def _run(output_dir: Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--output-dir",
            str(output_dir),
            *extra_args,
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_p3_01_command_emits_reproducible_redacted_structural_baseline(
    tmp_path: Path,
) -> None:
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"

    first = _run(first_output)
    assert first.returncode == 0, first.stderr

    report_path = first_output / "p3-01-structural-baseline.json"
    markdown_path = first_output / "p3-01-structural-baseline.md"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    rendered = markdown_path.read_text(encoding="utf-8")

    assert payload["package"] == "P3-01"
    assert payload["report_version"] == 1
    assert "DBManager" in payload["core_targets"]["db_manager.py"]["public_classes"]
    assert "db_manager.py" in payload["core_targets"]
    assert "session_manager.py" in payload["core_targets"]
    assert "backend/api/app.py" in payload["core_targets"]
    assert any(
        item["kind"] in {"importlib.import_module", "__import__"}
        for item in payload["dynamic_imports"]
    )
    assert any(
        item["target"] == "db_manager.py" for item in payload["test_coverage_map"]
    )
    assert "main.py" in payload["core_callers"]["db_manager.py"]
    assert set(payload["schema_inputs"]) == {"grading", "question_bank"}
    assert payload["performance_evidence"]["p1_26"]["package"] == "P1-26"
    assert payload["performance_evidence"]["p1_27"]["p1_26_provenance_code_sha"]
    assert "P1-29" in payload["real_process_evidence"]
    assert "P2-20" in payload["real_process_evidence"]
    assert str(PROJECT_ROOT) not in report_path.read_text(encoding="utf-8")
    assert str(PROJECT_ROOT) not in rendered
    assert "user_data/" not in report_path.read_text(encoding="utf-8")

    second = _run(second_output)
    assert second.returncode == 0, second.stderr
    assert report_path.read_bytes() == (
        second_output / "p3-01-structural-baseline.json"
    ).read_bytes()
    assert markdown_path.read_bytes() == (
        second_output / "p3-01-structural-baseline.md"
    ).read_bytes()


def test_p3_01_command_rejects_missing_controlled_evidence_without_publication(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output"

    completed = _run(
        output_dir,
        "--p1-26-report",
        "docs/performance/missing-p1-26.json",
    )

    assert completed.returncode != 0
    assert "missing required input" in completed.stderr
    assert not output_dir.exists()
