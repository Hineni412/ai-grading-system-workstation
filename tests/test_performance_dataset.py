from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from tools.performance.dataset import (
    BenchmarkDataset,
    ScaleDefinition,
    build_benchmark_dataset,
)


MICRO = ScaleDefinition(
    name="micro",
    sessions=1,
    students=2,
    questions_per_session=2,
    grading_details=4,
    question_bank_questions=8,
    backups=2,
)


def test_failed_build_releases_sqlite_handles_for_immediate_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.performance.dataset as dataset_module

    def fail_validation(_db_path: Path, _label: str) -> None:
        raise RuntimeError("generated validation failure")

    monkeypatch.setattr(dataset_module, "_assert_database_valid", fail_validation)
    with pytest.raises(RuntimeError, match="generated validation failure"):
        with tempfile.TemporaryDirectory(
            prefix="generated-dataset-failure-cleanup-"
        ) as temp_dir:
            temporary_root = Path(temp_dir)
            build_benchmark_dataset(temporary_root / "dataset", MICRO)

    assert not temporary_root.exists()
