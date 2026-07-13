from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from tools.performance.dataset import (
    LARGE,
    MEDIUM,
    SMALL,
    BenchmarkDataset,
    DatasetManifest,
    ScaleDefinition,
    build_benchmark_dataset,
)


MICRO = ScaleDefinition("micro", 1, 2, 2, 4, 8, 2, 2)


class _SetupFailingConnection:
    def __init__(self, connection: sqlite3.Connection, *, fail_setup: bool) -> None:
        self._connection = connection
        self._fail_setup = fail_setup
        self.closed = False

    @property
    def row_factory(self) -> object:
        return self._connection.row_factory

    @row_factory.setter
    def row_factory(self, value: object) -> None:
        self._connection.row_factory = value

    def execute(self, statement: str, parameters: object = ()) -> object:
        if self._fail_setup and statement.strip().casefold().startswith(
            "pragma journal_mode"
        ):
            raise sqlite3.OperationalError("generated setup failure")
        return self._connection.execute(statement, parameters)

    def close(self) -> None:
        self.closed = True
        self._connection.close()

    def __enter__(self) -> _SetupFailingConnection:
        self._connection.__enter__()
        return self

    def __exit__(self, *args: object) -> object:
        return self._connection.__exit__(*args)

    def __getattr__(self, name: str) -> object:
        return getattr(self._connection, name)


class _SetupFailureFactory:
    def __init__(
        self,
        real_connect: object,
        *,
        fail_connection_number: int,
    ) -> None:
        self._real_connect = real_connect
        self._fail_connection_number = fail_connection_number
        self.connections: list[_SetupFailingConnection] = []

    def __call__(self, *args: object, **kwargs: object) -> _SetupFailingConnection:
        connection = self._real_connect(*args, **kwargs)  # type: ignore[operator]
        tracked = _SetupFailingConnection(
            connection,
            fail_setup=len(self.connections) + 1 == self._fail_connection_number,
        )
        self.connections.append(tracked)
        return tracked


def _integrity(db_path: Path) -> tuple[list[tuple[object, ...]], str]:
    with sqlite3.connect(db_path) as conn:
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    return foreign_keys, integrity


def _table_counts(db_path: Path, table_names: tuple[str, ...]) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        return {
            table_name: int(
                conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
            )
            for table_name in table_names
        }


def _assert_generated_content(dataset: BenchmarkDataset) -> None:
    with sqlite3.connect(dataset.paths.db_path) as conn:
        students = conn.execute(
            "SELECT student_code, name, class_name FROM students ORDER BY id"
        ).fetchall()
        sessions = conn.execute(
            "SELECT session_name, rubric_path, answer_key_path FROM grading_sessions ORDER BY id"
        ).fetchall()
        details = conn.execute(
            "SELECT knowledge_id, knowledge_ids FROM session_details ORDER BY id"
        ).fetchall()

    assert students
    assert all(code.startswith("GEN-") and name.startswith("GEN-") for code, name, _ in students)
    assert all(class_name.startswith("CLASS-") for _, _, class_name in students)
    assert all(
        name.startswith("generated-")
        and rubric.startswith("generated-")
        and answer.startswith("generated-")
        for name, rubric, answer in sessions
    )
    assert all(
        knowledge_id.startswith("knowledge-")
        and knowledge_ids.startswith('["knowledge-')
        for knowledge_id, knowledge_ids in details
    )

    with sqlite3.connect(dataset.paths.qb_db_path) as conn:
        questions = conn.execute(
            "SELECT question_text, answer_text, source_file FROM questions ORDER BY id"
        ).fetchall()
        tags = conn.execute(
            "SELECT tag_type, tag_value FROM question_tags ORDER BY id"
        ).fetchall()
        tasks = conn.execute(
            "SELECT task_code, created_by FROM training_tasks ORDER BY id"
        ).fetchall()

    assert questions
    assert all(
        text.startswith("generated-")
        and answer.startswith("generated-")
        and source.startswith("generated-")
        for text, answer, source in questions
    )
    assert all(
        tag_type in {"knowledge_point", "method"}
        and tag_value.startswith(("knowledge-", "generated-"))
        for tag_type, tag_value in tags
    )
    assert all(code.startswith("GEN-") and creator.startswith("generated-") for code, creator in tasks)


def test_default_scale_counts_are_the_approved_design_sizes() -> None:
    assert SMALL.counts == (1, 30, 10, 300, 200, 10, 5)
    assert MEDIUM.counts == (5, 200, 20, 20_000, 2_000, 100, 50)
    assert LARGE.name == "large_5pct"
    assert LARGE.counts == (1, 25, 2, 7_500, 500, 25, 5)


def test_manifest_is_frozen_and_contains_only_allowlisted_summary_fields() -> None:
    assert tuple(field.name for field in fields(DatasetManifest)) == (
        "scale_name",
        "seed",
        "table_counts",
        "grading_database_bytes",
        "question_bank_database_bytes",
        "backup_database_bytes",
        "generated_asset_bytes",
    )
    manifest = DatasetManifest("micro", 126, (), 1, 2, 3, 4)
    with pytest.raises(FrozenInstanceError):
        manifest.seed = 999  # type: ignore[misc]


def test_build_uses_explicit_paths_and_restores_the_default_path_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import path_manager

    class ForbiddenDefaultPaths:
        def __init__(self) -> None:
            raise AssertionError("default repository path provider must not be constructed")

    monkeypatch.setattr(path_manager, "_instance", None)
    monkeypatch.setattr(path_manager, "PathManager", ForbiddenDefaultPaths)

    dataset = build_benchmark_dataset(tmp_path / "explicit", MICRO)

    assert dataset.paths.project_root == (tmp_path / "explicit").resolve()
    assert path_manager._instance is None


def test_micro_dataset_is_deterministic_valid_and_confined_to_temp_roots(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "other"

    first = build_benchmark_dataset(first_root, MICRO, seed=126)
    second = build_benchmark_dataset(second_root, MICRO, seed=126)

    assert first.manifest == second.manifest
    assert first.representative_question_id == second.representative_question_id
    assert first.representative_task_id == second.representative_task_id
    assert first.knowledge_key == second.knowledge_key

    expected_counts = dict(first.manifest.table_counts)
    grading_tables = (
        "students",
        "grading_sessions",
        "exam_papers",
        "session_results",
        "session_details",
    )
    question_bank_tables = (
        "questions",
        "question_tags",
        "question_previews",
        "grading_question_links",
        "training_tasks",
        "training_variants",
        "variant_students",
        "training_task_items",
    )
    for dataset in (first, second):
        actual_counts = {
            **_table_counts(dataset.paths.db_path, grading_tables),
            **_table_counts(dataset.paths.qb_db_path, question_bank_tables),
        }
        actual_counts["backup_files"] = len(list(dataset.paths.backups_dir.glob("*.db")))
        assert actual_counts == expected_counts
        assert _integrity(dataset.paths.db_path) == ([], "ok")
        assert _integrity(dataset.paths.qb_db_path) == ([], "ok")
        from question_bank.services.question_read_service import QuestionBankReadService

        read_service = QuestionBankReadService(
            dataset.paths.qb_db_path,
            data_root=dataset.paths.data_root,
        )
        asset = read_service.resolve_asset(dataset.representative_question_id, 0)
        question_preview = read_service.resolve_preview(
            dataset.representative_question_id,
            "question",
        )
        answer_preview = read_service.resolve_preview(
            dataset.representative_question_id,
            "answer",
        )
        assert asset.path.parent == (
            dataset.paths.qb_data_dir / "extracted_images"
        ).resolve()
        assert question_preview.path.parent == (
            dataset.paths.qb_data_dir / "previews"
        ).resolve()
        assert answer_preview.path == question_preview.path
        assert all(
            item.path.is_relative_to(dataset.paths.data_root.resolve())
            for item in (asset, question_preview, answer_preview)
        )
        with sqlite3.connect(dataset.paths.db_path) as conn:
            rubric_values = [
                str(row[0])
                for row in conn.execute(
                    "SELECT rubric_path FROM grading_sessions ORDER BY id"
                )
            ]
            distinct_result_questions = int(
                conn.execute(
                    "SELECT COUNT(DISTINCT CAST(result_id AS TEXT) || ':' || question_id) "
                    "FROM session_details"
                ).fetchone()[0]
            )
        assert distinct_result_questions == MICRO.grading_details
        for rubric_value in rubric_values:
            rubric_path = dataset.paths.data_root / rubric_value
            assert rubric_path.is_file()
            rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
            assert len(rubric["questions"]) == MICRO.questions_per_session
        _assert_generated_content(dataset)

    for root in (first_root, second_root):
        resolved_root = root.resolve()
        generated_files = [path for path in root.rglob("*") if path.is_file()]
        assert generated_files
        assert all(path.resolve().is_relative_to(resolved_root) for path in generated_files)

    repository_user_data = str((Path(__file__).resolve().parents[1] / "user_data").resolve())
    assert repository_user_data not in repr(first)
    assert repository_user_data not in repr(first.manifest)
    assert repository_user_data not in repr(second)
    assert repository_user_data not in repr(second.manifest)


def test_build_releases_sqlite_handles_for_immediate_temporary_tree_cleanup() -> None:
    with tempfile.TemporaryDirectory(prefix="generated-dataset-cleanup-") as temp_dir:
        temporary_root = Path(temp_dir)
        dataset = build_benchmark_dataset(temporary_root / "dataset", MICRO)
        assert dataset.paths.db_path.is_file()
        assert dataset.paths.qb_db_path.is_file()

    assert not temporary_root.exists()


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


@pytest.mark.parametrize("fail_connection_number", [1, 2])
def test_setup_failure_closes_connection_and_restores_temporary_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_connection_number: int,
) -> None:
    import path_manager
    import tools.performance.dataset as dataset_module

    real_connect = sqlite3.connect
    factory = _SetupFailureFactory(
        real_connect,
        fail_connection_number=fail_connection_number,
    )
    sentinel = object()
    monkeypatch.setattr(
        dataset_module,
        "_SQLITE_CONNECT",
        factory,
        raising=False,
    )
    monkeypatch.setattr(path_manager, "_instance", sentinel)
    temporary_root = tmp_path / f"setup-failure-{fail_connection_number}"
    cleanup_error: PermissionError | None = None

    try:
        with pytest.raises(sqlite3.OperationalError, match="generated setup failure"):
            build_benchmark_dataset(temporary_root, MICRO)
        assert path_manager._instance is sentinel
        try:
            shutil.rmtree(temporary_root)
        except PermissionError as exc:
            cleanup_error = exc

        assert [connection.closed for connection in factory.connections] == [
            True
        ] * fail_connection_number
        assert cleanup_error is None
        assert not temporary_root.exists()
    finally:
        for connection in factory.connections:
            if not connection.closed:
                connection.close()
        shutil.rmtree(temporary_root, ignore_errors=True)
