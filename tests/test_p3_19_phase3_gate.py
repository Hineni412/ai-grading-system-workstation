from __future__ import annotations

from pathlib import Path

from tools.migration_rehearsal import RehearsalResult
from tools.p3_19_acceptance import (
    assess_rehearsal_result,
    run_historical_version_matrix,
)


def test_phase_gate_upgrades_every_supported_historical_version(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "existing-note.txt"
    existing.write_text("preserve unrelated content", encoding="utf-8")
    matrix = run_historical_version_matrix(work_dir=tmp_path)

    assert matrix["passed"] is True
    assert list(tmp_path.iterdir()) == [existing]
    assert existing.read_text(encoding="utf-8") == "preserve unrelated content"
    by_target = {item["target"]: item for item in matrix["targets"]}
    assert set(by_target) == {"grading", "question_bank"}
    for target in ("grading", "question_bank"):
        migration_dir = Path(__file__).resolve().parents[1] / "migrations" / target
        expected_versions = [
            "empty",
            *(path.stem for path in sorted(migration_dir.glob("*.sql"))),
        ]
        item = by_target[target]
        assert [
            version["start_version"] for version in item["versions"]
        ] == expected_versions
        assert all(
            version["status"]
            in {
                "passed",
                "passed_expected_migration_changes",
                "passed_expected_retirement",
                "passed_expected_seed",
            }
            for version in item["versions"]
        )
