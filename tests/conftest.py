from __future__ import annotations

import gc
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import path_manager as path_manager_module
import pytest

from tools.test_suite_manifest import DATABASE_BASELINE_TEST_PATHS, categories_for_path


_TEMPORARY_ROOT: tempfile.TemporaryDirectory[str] | None = None
_RUN_TEMPORARY_ROOT: tempfile.TemporaryDirectory[str] | None = None
_ORIGINAL_PATH_MANAGER: Any | None = None
_ORIGINAL_DATA_DIR: str | None = None
_ORIGINAL_TEMP_ENV: dict[str, str | None] = {}
_ORIGINAL_TEMP_CACHE: str | None = None


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    global _ORIGINAL_DATA_DIR
    global _ORIGINAL_PATH_MANAGER
    global _TEMPORARY_ROOT
    global _RUN_TEMPORARY_ROOT
    global _ORIGINAL_TEMP_CACHE

    _ORIGINAL_TEMP_CACHE = tempfile.tempdir
    _ORIGINAL_TEMP_ENV.update(
        {key: os.environ.get(key) for key in ("TEMP", "TMP", "TMPDIR")}
    )
    if not hasattr(config, "workerinput"):
        if config.option.basetemp is None:
            sandbox_parent = _PROJECT_ROOT / ".test-runs"
            sandbox_parent.mkdir(exist_ok=True)
            _RUN_TEMPORARY_ROOT = tempfile.TemporaryDirectory(
                prefix="py_",
                dir=sandbox_parent,
                ignore_cleanup_errors=True,
            )
            temporary_root = Path(_RUN_TEMPORARY_ROOT.name)
            config.option.basetemp = str(temporary_root / "p")
        else:
            # The suite runner owns explicit basetemp directories and their cleanup.
            temporary_root = Path(config.option.basetemp).resolve().parent
            temporary_root.mkdir(parents=True, exist_ok=True)
        for key in _ORIGINAL_TEMP_ENV:
            os.environ[key] = str(temporary_root)
        tempfile.tempdir = str(temporary_root)
    else:
        # Workers share the parent's TEMP root, but have separate pytest basetemps.
        tempfile.tempdir = None

    _ORIGINAL_PATH_MANAGER = path_manager_module._instance
    _ORIGINAL_DATA_DIR = os.environ.get("AI_GRADING_DATA_DIR")

    _TEMPORARY_ROOT = tempfile.TemporaryDirectory(
        prefix="ai_grading_pytest_",
        ignore_cleanup_errors=True,
    )
    temporary_root = Path(_TEMPORARY_ROOT.name)
    isolated_paths = path_manager_module.PathManager()
    isolated_paths._data_root = temporary_root / "user_data"
    isolated_paths._logs_root = temporary_root / "logs"
    isolated_paths._api_profiles_path = temporary_root / "config" / "api_profiles.json"
    isolated_paths._ops_state_dir = temporary_root / "ops"
    isolated_paths.ensure_directories()

    path_manager_module._instance = isolated_paths
    os.environ["AI_GRADING_DATA_DIR"] = str(isolated_paths.data_root)


def pytest_unconfigure() -> None:
    global _TEMPORARY_ROOT
    global _RUN_TEMPORARY_ROOT

    from integration.data_generation import reset_commit_generations

    reset_commit_generations()
    path_manager_module._instance = _ORIGINAL_PATH_MANAGER
    if _ORIGINAL_DATA_DIR is None:
        os.environ.pop("AI_GRADING_DATA_DIR", None)
    else:
        os.environ["AI_GRADING_DATA_DIR"] = _ORIGINAL_DATA_DIR
    # SQLite connections in reference cycles can outlive fixture teardown.
    # Release their Windows file handles before removing this run's directories.
    gc.collect()
    if _TEMPORARY_ROOT is not None:
        # Windows cannot remove a log file while a test-created handler owns it.
        owned_root = Path(_TEMPORARY_ROOT.name).resolve()
        loggers = [logging.getLogger(), *logging.Logger.manager.loggerDict.values()]
        for logger in loggers:
            if not isinstance(logger, logging.Logger):
                continue
            for handler in logger.handlers[:]:
                filename = getattr(handler, "baseFilename", None)
                if filename and Path(filename).resolve().is_relative_to(owned_root):
                    handler.close()
                    logger.removeHandler(handler)
        _TEMPORARY_ROOT.cleanup()
        _TEMPORARY_ROOT = None
    if _RUN_TEMPORARY_ROOT is not None:
        _RUN_TEMPORARY_ROOT.cleanup()
        _RUN_TEMPORARY_ROOT = None
    for key, value in _ORIGINAL_TEMP_ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    _ORIGINAL_TEMP_ENV.clear()
    tempfile.tempdir = _ORIGINAL_TEMP_CACHE


@pytest.fixture
def tmp_path(tmp_path: Path):
    """Release database monitors before pytest removes this test's directory."""
    from integration.data_generation import reset_commit_generations

    yield tmp_path
    reset_commit_generations()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        try:
            relative_path = Path(item.path).resolve().relative_to(_PROJECT_ROOT)
        except ValueError:
            continue
        for category in categories_for_path(relative_path):
            item.add_marker(category)


@pytest.fixture(scope="session")
def question_bank_database(tmp_path_factory: pytest.TempPathFactory):
    """Copy a per-worker synthetic baseline for explicitly opted-in tests.

    Schema/upgrade tests keep calling the real initializer on their own inputs.
    Each consumer receives a new database, never a shared writable connection.
    """
    import sqlite3
    from contextlib import closing

    from question_bank.database.schema import initialize_database
    from tests.current_knowledge_support import install_current_knowledge

    template_root = tmp_path_factory.mktemp("qb")
    templates: dict[int | None, Path] = {}

    def create_database(db_path: Path, *, taxonomy_revision: int | None = None) -> Path:
        db_path = Path(db_path)
        if db_path.exists():
            raise FileExistsError(f"test database already exists: {db_path}")
        if taxonomy_revision not in templates:
            template = template_root / f"v_{taxonomy_revision}" / "q.db"
            initialize_database(template)
            if taxonomy_revision is not None:
                install_current_knowledge(template, taxonomy_revision=taxonomy_revision)
            templates[taxonomy_revision] = template

        template = templates[taxonomy_revision]
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(
            sqlite3.connect(template.as_uri() + "?mode=ro", uri=True)
        ) as source:
            with closing(sqlite3.connect(db_path)) as destination:
                source.backup(destination)
        # Preserve the production initializer's schema checks and connection setup.
        initialize_database(db_path)
        return db_path

    return create_database


@pytest.fixture(scope="session")
def current_schema_database(tmp_path_factory: pytest.TempPathFactory):
    """Build each current schema once; give business tests independent empty copies."""
    import sqlite3
    from contextlib import closing

    from backend.schema_migrations import ensure_schema_current

    template_root = tmp_path_factory.mktemp("schemas")
    templates: dict[str, Path] = {}

    def copy_database(target: str, destination: Path) -> None:
        if target not in {"grading", "question_bank"}:
            raise ValueError(target)
        destination = Path(destination)
        if destination.exists():
            raise FileExistsError(destination)
        if target not in templates:
            template = template_root / target[0] / "b.db"
            ensure_schema_current(target, template)
            templates[target] = template
        destination.parent.mkdir(parents=True, exist_ok=True)
        with closing(
            sqlite3.connect(templates[target].as_uri() + "?mode=ro", uri=True)
        ) as source:
            with closing(sqlite3.connect(destination)) as database:
                source.backup(database)

    return copy_database


@pytest.fixture(autouse=True)
def _business_database_baselines(request, monkeypatch, current_schema_database):
    """Only the explicit business-file allowlist reuses current-schema preparation.

    Historical schemas, existing files and custom migration roots always take the
    original path. Production initializers and their post-migration work still run.
    """
    try:
        relative = Path(request.node.path).resolve().relative_to(_PROJECT_ROOT)
    except ValueError:
        return
    if relative not in DATABASE_BASELINE_TEST_PATHS:
        return

    import db_manager
    import grading_run_store
    from backend import schema_migrations
    from backend.jobs import store as job_store
    from question_bank.database import schema as question_bank_schema

    original_gate = schema_migrations.ensure_schema_current
    temporary_root = Path(tempfile.gettempdir()).resolve()

    def prepare_current_schema(target, db_path, **kwargs):
        database = Path(db_path)
        if (
            target in {"grading", "question_bank"}
            and set(kwargs) <= {"allow_existing_migrations"}
            and database.resolve().is_relative_to(temporary_root)
            and not database.exists()
        ):
            current_schema_database(target, database)
        return original_gate(target, db_path, **kwargs)

    for module in (db_manager, grading_run_store, job_store, question_bank_schema):
        monkeypatch.setattr(module, "ensure_schema_current", prepare_current_schema)
