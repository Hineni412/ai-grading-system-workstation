from __future__ import annotations

from pathlib import Path

from tools.migration_rehearsal import RehearsalResult
from tools.p3_19_acceptance import (
    EXPECTED_QUESTION_BANK_SEEDS,
    RETIRED_QUESTION_BANK_TABLES,
    assess_rehearsal_result,
    run_historical_version_matrix,
)


def _result(**overrides: object) -> RehearsalResult:
    values: dict[str, object] = {
        "target": "question_bank",
        "mode": "execute",
        "source_db": "private/source.db",
        "copy_db": "private/copy.db",
        "ok": False,
        "integrity_ok": True,
        "schema_matches_runtime": True,
        "business_row_count_changes": {},
        "qb_005_changed_paper_rows": 0,
        "migrations_error": None,
        "messages": [],
    }
    values.update(overrides)
    return RehearsalResult(**values)  # type: ignore[arg-type]


def test_phase_gate_accepts_an_ordinary_green_rehearsal() -> None:
    assessment = assess_rehearsal_result(_result(target="grading", ok=True))

    assert assessment == {
        "target": "grading",
        "status": "passed",
        "integrity_ok": True,
        "schema_matches_current": True,
        "expected_retired_tables": [],
        "expected_seeded_tables": [],
        "unexpected_changed_tables": [],
    }


def test_phase_gate_accepts_only_exact_retired_question_bank_table_removal() -> None:
    retired = sorted(RETIRED_QUESTION_BANK_TABLES)[:2]
    assessment = assess_rehearsal_result(
        _result(
            business_row_count_changes={
                table: (index + 1, 0) for index, table in enumerate(retired)
            }
        )
    )

    assert assessment["status"] == "passed_expected_retirement"
    assert assessment["expected_retired_tables"] == retired
    assert assessment["expected_seeded_tables"] == []
    assert assessment["unexpected_changed_tables"] == []
    assert "source_db" not in assessment
    assert "copy_db" not in assessment


def test_phase_gate_rejects_unapproved_or_nonempty_table_changes() -> None:
    unapproved = assess_rehearsal_result(
        _result(business_row_count_changes={"questions": (5, 4)})
    )
    nonempty_retired = assess_rehearsal_result(
        _result(business_row_count_changes={"knowledge_concepts": (5, 1)})
    )
    invalid_seed = assess_rehearsal_result(
        _result(business_row_count_changes={"mastery_v2_rollout_state": (1, 2)})
    )

    assert unapproved["status"] == "failed"
    assert unapproved["unexpected_changed_tables"] == ["questions"]
    assert nonempty_retired["status"] == "failed"
    assert nonempty_retired["unexpected_changed_tables"] == ["knowledge_concepts"]
    assert invalid_seed["status"] == "failed"
    assert invalid_seed["unexpected_changed_tables"] == [
        "mastery_v2_rollout_state"
    ]


def test_phase_gate_accepts_only_the_exact_question_bank_system_seed() -> None:
    assessment = assess_rehearsal_result(
        _result(business_row_count_changes=dict(EXPECTED_QUESTION_BANK_SEEDS))
    )

    assert assessment["status"] == "passed_expected_seed"
    assert assessment["expected_retired_tables"] == []
    assert assessment["expected_seeded_tables"] == [
        "mastery_v2_rollout_state"
    ]
    assert assessment["unexpected_changed_tables"] == []


def test_phase_gate_rejects_failed_integrity_schema_or_migration() -> None:
    for result in (
        _result(integrity_ok=False),
        _result(schema_matches_runtime=False),
        _result(migrations_error="migration failed"),
        _result(qb_005_changed_paper_rows=1),
        _result(mode="stamp-only"),
    ):
        assert assess_rehearsal_result(result)["status"] == "failed"


def test_phase_gate_upgrades_every_supported_historical_version(
    tmp_path: Path,
) -> None:
    matrix = run_historical_version_matrix(work_dir=tmp_path)

    assert matrix["passed"] is True
    by_target = {
        item["target"]: item for item in matrix["targets"]
    }
    assert set(by_target) == {"grading", "question_bank"}
    for target, expected_count, latest_version in (
        ("grading", 12, "010_workspace_ai_tasks"),
        (
            "question_bank",
            33,
            "031_add_paper_folder",
        ),
    ):
        item = by_target[target]
        assert len(item["versions"]) == expected_count
        assert item["versions"][0]["start_version"] == "empty"
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
        assert item["versions"][-1]["start_version"] == latest_version
