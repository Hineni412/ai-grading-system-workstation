from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from update_tools.migrate_db import run_migrations


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GRADING_MIGRATIONS = PROJECT_ROOT / "migrations" / "grading"


def test_pytest_data_isolation_restores_an_unset_environment() -> None:
    script = """
import importlib.util
import logging
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.pop("AI_GRADING_DATA_DIR", None)
original_temp = {key: os.environ.get(key) for key in ("TEMP", "TMP", "TMPDIR")}
original_cache = tempfile.tempdir
module_path = Path("tests/conftest.py").resolve()
spec = importlib.util.spec_from_file_location("p3_12_conftest_probe", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
config = SimpleNamespace(option=SimpleNamespace(basetemp=None))
module.pytest_configure(config)
temporary_root = Path(tempfile.gettempdir())
assert temporary_root.parent == Path.cwd() / ".test-runs"
assert Path(config.option.basetemp).parent == temporary_root
assert all(os.environ[key] == str(temporary_root) for key in original_temp)
handler = logging.FileHandler(Path(module._TEMPORARY_ROOT.name) / "logs" / "synthetic.log")
logger = logging.getLogger("synthetic_temp_cleanup")
logger.addHandler(handler)
logger.warning("synthetic log keeps a Windows file handle open")
module.pytest_unconfigure()
assert "AI_GRADING_DATA_DIR" not in os.environ
assert not temporary_root.exists()
assert {key: os.environ.get(key) for key in original_temp} == original_temp
assert tempfile.tempdir == original_cache
assert handler not in logger.handlers
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("fail_last", [False, True])
def test_direct_pytest_reclaims_passed_copies_and_cleans_run_on_exit(
    tmp_path: Path,
    fail_last: bool,
) -> None:
    record = tmp_path / "run.json"
    probe = tmp_path / "test_temp_lifecycle.py"
    probe.write_text(
        f"""
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from integration.data_generation import commit_generation

previous = None

def test_first_copy(tmp_path):
    global previous
    previous = tmp_path
    (tmp_path / "synthetic.bin").write_bytes(b"x" * 1024 * 1024)
    database = tmp_path / "synthetic.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE synthetic (value INTEGER)")
        connection.commit()
    assert commit_generation(database) > 0
    root = Path(tempfile.gettempdir())
    assert root.parent == Path.cwd() / ".test-runs"
    assert tmp_path.is_relative_to(root)
    Path({str(record)!r}).write_text(json.dumps({{"root": str(root)}}))

def test_next_copy(tmp_path):
    assert not previous.exists(), "passed test data accumulated until session end"
    (tmp_path / "synthetic.bin").write_bytes(b"x" * 1024 * 1024)
    assert {not fail_last!r}, "intentional synthetic failure"
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "tests.conftest",
            "-c",
            "pytest.ini",
            "-q",
            str(probe),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == int(fail_last), completed.stdout + completed.stderr
    assert ("1 passed" if fail_last else "2 passed") in completed.stdout
    assert not Path(json.loads(record.read_text())["root"]).exists()
