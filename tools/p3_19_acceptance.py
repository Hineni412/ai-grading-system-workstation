"""P3-19 phase-gate checks that compose existing strict validators.

The generic migration rehearsal intentionally fails on every business-table row
count change.  P3-19 keeps that validator strict and adds a narrower judgment:
question-bank migration 009 may remove only the exact tables retired by P3-17.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from tools.migration_rehearsal import (
    RehearsalResult,
    _default_migrations_dir,
    _default_source_db,
    rehearse_database,
    run_migrations,
)


RETIRED_QUESTION_BANK_TABLES = frozenset(
    {
        "knowledge_concepts",
        "knowledge_relations",
        "knowledge_source_mappings",
        "skill_topics",
        "skills",
        "assessment_item_skills",
        "question_skill_links",
        "skill_resolution_conflicts",
        "skill_neighbors",
        "skill_system_settings",
        "skill_migration_runs",
    }
)


class _SilentMigrationLogger:
    """Keep generated matrix runs out of product logs and terminal output."""

    def info(self, *_args: object, **_kwargs: object) -> None:
        return None

    def error(self, *_args: object, **_kwargs: object) -> None:
        return None


def assess_rehearsal_result(result: RehearsalResult) -> dict[str, object]:
    """Return a path-free P3-19 judgment for one execute rehearsal."""
    expected_retired: list[str] = []
    unexpected: list[str] = []
    for table, (before, after) in sorted(result.business_row_count_changes.items()):
        if (
            result.target == "question_bank"
            and table in RETIRED_QUESTION_BANK_TABLES
            and before > 0
            and after == 0
        ):
            expected_retired.append(table)
        else:
            unexpected.append(table)

    base_checks_pass = (
        result.mode == "execute"
        and result.migrations_error is None
        and result.integrity_ok
        and result.schema_matches_runtime
        and result.qb_005_changed_paper_rows in {None, 0}
        and not unexpected
    )
    if base_checks_pass and result.ok:
        status = "passed"
    elif base_checks_pass and expected_retired:
        status = "passed_expected_retirement"
    else:
        status = "failed"

    return {
        "target": result.target,
        "status": status,
        "integrity_ok": result.integrity_ok,
        "schema_matches_current": result.schema_matches_runtime,
        "expected_retired_tables": expected_retired,
        "unexpected_changed_tables": unexpected,
    }


def run_current_copy_gate(
    *,
    grading_db: Path,
    question_bank_db: Path,
    work_dir: Path,
) -> dict[str, object]:
    assessments: list[dict[str, object]] = []
    for target, source_db in (
        ("grading", grading_db),
        ("question_bank", question_bank_db),
    ):
        result = rehearse_database(
            target,
            source_db=source_db,
            migrations_dir=_default_migrations_dir(target),
            mode="execute",
            work_dir=work_dir / target,
        )
        assessments.append(assess_rehearsal_result(result))
    return {
        "phase_gate": "P3-19",
        "check": "current_copy_migration",
        "passed": all(item["status"] != "failed" for item in assessments),
        "assessments": assessments,
    }


def _migration_files(target: str) -> list[Path]:
    return sorted(
        _default_migrations_dir(target).glob("*.sql"),
        key=lambda path: (int(path.stem.split("_", 1)[0]), path.name),
    )


def _build_historical_version(
    *,
    target: str,
    version_index: int,
    migration_files: list[Path],
    work_dir: Path,
) -> tuple[Path, str]:
    version_name = (
        "empty" if version_index == 0 else migration_files[version_index - 1].stem
    )
    target_key = "g" if target == "grading" else "q"
    version_dir = work_dir / target_key / f"{version_index:02d}"
    version_dir.mkdir(parents=True, exist_ok=True)
    source_db = version_dir / "source.db"
    if version_index == 0:
        sqlite3.connect(source_db).close()
        return source_db, version_name

    prefix_dir = version_dir / "migration_prefix"
    prefix_dir.mkdir()
    for migration_file in migration_files[:version_index]:
        shutil.copy2(migration_file, prefix_dir / migration_file.name)
    report = run_migrations(
        target,
        db_path=source_db,
        migrations_dir=prefix_dir,
        logger_override=_SilentMigrationLogger(),
        backup_dir_override=version_dir / "seed_backups",
    )
    if report.error:
        raise RuntimeError("historical version seed failed")
    return source_db, version_name


def run_historical_version_matrix(*, work_dir: Path) -> dict[str, object]:
    """Upgrade an empty database and every migration prefix to current.

    Each source version and every migration write live under ``work_dir``.
    Results expose only logical target/version names and path-free statuses.
    """
    targets: list[dict[str, object]] = []
    for target in ("grading", "question_bank"):
        migration_files = _migration_files(target)
        versions: list[dict[str, str]] = []
        for version_index in range(len(migration_files) + 1):
            version_name = (
                "empty"
                if version_index == 0
                else migration_files[version_index - 1].stem
            )
            try:
                source_db, version_name = _build_historical_version(
                    target=target,
                    version_index=version_index,
                    migration_files=migration_files,
                    work_dir=work_dir / "s",
                )
                result = rehearse_database(
                    target,
                    source_db=source_db,
                    migrations_dir=_default_migrations_dir(target),
                    mode="execute",
                    work_dir=work_dir
                    / "r"
                    / ("g" if target == "grading" else "q")
                    / f"{version_index:02d}",
                )
                status = str(assess_rehearsal_result(result)["status"])
            except Exception:
                status = "failed"
            versions.append(
                {
                    "start_version": version_name,
                    "status": status,
                }
            )
        targets.append(
            {
                "target": target,
                "versions": versions,
                "passed": all(item["status"] != "failed" for item in versions),
            }
        )
    return {
        "check": "historical_version_matrix",
        "passed": all(bool(item["passed"]) for item in targets),
        "targets": targets,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the P3-19 current-copy migration acceptance gate"
    )
    parser.add_argument("--grading-db", type=Path, default=None)
    parser.add_argument("--question-bank-db", type=Path, default=None)
    parser.add_argument("--work-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    grading_db = args.grading_db or _default_source_db("grading")
    question_bank_db = args.question_bank_db or _default_source_db("question_bank")
    if args.work_dir is not None:
        current_copy = run_current_copy_gate(
            grading_db=grading_db,
            question_bank_db=question_bank_db,
            work_dir=args.work_dir / "current",
        )
        historical_matrix = run_historical_version_matrix(
            work_dir=args.work_dir / "historical"
        )
    else:
        with tempfile.TemporaryDirectory(prefix="p3_19_acceptance_") as temp_dir:
            work_root = Path(temp_dir)
            current_copy = run_current_copy_gate(
                grading_db=grading_db,
                question_bank_db=question_bank_db,
                work_dir=work_root / "current",
            )
            historical_matrix = run_historical_version_matrix(
                work_dir=work_root / "historical"
            )

    payload = {
        "phase_gate": "P3-19",
        "passed": bool(current_copy["passed"]) and bool(historical_matrix["passed"]),
        "current_copy_migration": current_copy,
        "historical_version_matrix": historical_matrix,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
