from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from data_transfer_service import (
    ExportEntry,
    build_export_manifest,
    default_export_sources,
    write_export_zip,
)


def _write(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _arc_names(paths) -> set[str]:
    return {entry.arc_name for entry in paths}


@contextmanager
def _redirected_file(source: Path, target: Path):
    """Use a real link, including Windows without the file-symlink privilege."""
    try:
        os.symlink(target, source)
    except OSError as error:
        if os.name != "nt" or error.winerror != 1314:
            raise
        assert source.name == target.name
        source.parent.rmdir()  # The dedicated link directory must be empty.
        pwsh = shutil.which("pwsh") or r"C:\Program Files\PowerShell\7\pwsh.exe"
        subprocess.run(
            [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
             "New-Item -ItemType Junction -Path $env:TEST_LINK_PATH "
             "-Target $env:TEST_LINK_TARGET -ErrorAction Stop | Out-Null"],
            env={**os.environ, "TEST_LINK_PATH": str(source.parent),
                 "TEST_LINK_TARGET": str(target.parent)},
            check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            assert source.resolve() == target.resolve()
            yield
        finally:
            os.rmdir(source.parent)  # Remove the junction itself, never its target.
    else:
        try:
            yield
        finally:
            source.unlink()


def test_lean_export_skips_regenerable_and_historical_heavy_data(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    data_root = project_root / "user_data"

    _write(project_root / "config" / "app.json")
    _write(data_root / "databases" / "grading_system.db")
    _write(data_root / "config" / "api_profiles.json")
    _write(data_root / "templates" / "session_1" / "front_template.png")
    _write(data_root / "templates" / "session_1" / "template_source_full_class.pdf")
    _write(data_root / "question_bank" / "raw_papers" / "paper.docx")
    _write(data_root / "question_bank" / "rich_content" / "question.json")
    _write(data_root / "question_bank" / "extracted_images" / "page.png")
    _write(data_root / "annotated" / "session_1" / "marked.jpg")
    _write(data_root / "reports" / "report.pdf")
    _write(data_root / "backups" / "backup.zip")
    _write(data_root / "outputs" / "comparison" / "image.jpg")

    entries = build_export_manifest(default_export_sources(project_root, data_root), scope="lean")
    arc_names = _arc_names(entries)

    assert "config/app.json" in arc_names
    assert "user_data/databases/grading_system.db" in arc_names
    assert "user_data/config/api_profiles.json" not in arc_names
    assert "user_data/templates/session_1/front_template.png" in arc_names
    assert "user_data/question_bank/raw_papers/paper.docx" in arc_names
    assert "user_data/question_bank/rich_content/question.json" in arc_names
    assert "user_data/templates/session_1/template_source_full_class.pdf" not in arc_names
    assert "user_data/question_bank/extracted_images/page.png" not in arc_names
    assert "user_data/annotated/session_1/marked.jpg" not in arc_names
    assert "user_data/reports/report.pdf" not in arc_names
    assert "user_data/backups/backup.zip" not in arc_names
    assert "user_data/outputs/comparison/image.jpg" not in arc_names


def test_full_export_keeps_user_data_but_still_skips_runtime_cache(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    data_root = project_root / "user_data"
    _write(data_root / "annotated" / "session_1" / "marked.jpg")
    _write(data_root / "config" / "api_profiles.json")
    _write(data_root / "__pycache__" / "module.pyc")
    _write(data_root / "cache" / "review_crops" / "derived.jpg")
    _write(data_root / "workspaces" / "class-teacher" / "private.db")

    entries = build_export_manifest(default_export_sources(project_root, data_root), scope="full")
    arc_names = _arc_names(entries)

    assert "user_data/annotated/session_1/marked.jpg" in arc_names
    assert "user_data/config/api_profiles.json" not in arc_names
    assert "user_data/__pycache__/module.pyc" not in arc_names
    assert "user_data/cache/review_crops/derived.jpg" not in arc_names
    assert "user_data/workspaces/class-teacher/private.db" not in arc_names


def test_export_skips_case_variant_sensitive_filename(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    data_root = project_root / "user_data"
    _write(data_root / "config" / "API_PROFILES.JSON")

    entries = build_export_manifest(default_export_sources(project_root, data_root), scope="full")

    assert "user_data/config/API_PROFILES.JSON" not in _arc_names(entries)


def test_export_skips_sqlite_runtime_sidecars(tmp_path: Path) -> None:
    data_root = tmp_path / "project" / "user_data"
    databases = data_root / "databases"
    for name in (
        "grading_system.db",
        "grading_system.db-wal",
        "grading_system.db-shm",
        "grading_system.db-journal",
    ):
        _write(databases / name)

    entries = build_export_manifest([(data_root, "user_data")], scope="full")

    assert _arc_names(entries) == {"user_data/databases/grading_system.db"}


def test_export_rejects_redirected_file_below_controlled_root(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    outside = _write(tmp_path / "outside" / "innocent.txt", "synthetic-outside")
    link = source_root / "linked" / "innocent.txt"
    link.parent.mkdir(parents=True)

    with _redirected_file(link, outside):
        with pytest.raises(ValueError):
            build_export_manifest([(source_root, "user_data")], scope="full")
    assert outside.read_text(encoding="utf-8") == "synthetic-outside"


def test_export_rejects_detected_reparse_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    suspect = _write(source_root / "suspect.txt")
    from data_transfer_service import _is_reparse_point

    monkeypatch.setattr(
        "data_transfer_service._is_reparse_point",
        lambda path: Path(path) == suspect or _is_reparse_point(Path(path)),
    )

    with pytest.raises(ValueError):
        build_export_manifest([(source_root, "user_data")], scope="full")


def test_zip_writer_rechecks_source_after_manifest_creation(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source = _write(source_root / "linked" / "safe.txt", "safe")
    outside = _write(tmp_path / "outside" / "safe.txt", "fake")
    entry = ExportEntry(source, "user_data/linked/safe.txt", 4, source_root)
    source.unlink()

    with _redirected_file(source, outside):
        with pytest.raises(ValueError):
            write_export_zip([entry], tmp_path / "out.zip")
    assert outside.read_text(encoding="utf-8") == "fake"


def test_private_package_preserves_full_snapshot_and_skips_runtime_caches(tmp_path: Path) -> None:
    module_path = Path(__file__).resolve().parents[1] / "package_v1.5.0.py"
    spec = importlib.util.spec_from_file_location("package_v1_5_0", module_path)
    assert spec is not None and spec.loader is not None
    package_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package_module)

    source = tmp_path / "source"
    package = tmp_path / "package"
    _write(source / "user_data" / "config" / "api_profiles.json", '{"api_key":"synthetic-test-key"}')
    _write(source / "user_data" / "databases" / "grading_system.db")
    _write(source / "user_data" / "workspaces" / "class-teacher" / "private.db")
    _write(source / "user_data" / "__pycache__" / "cached.pyc")
    _write(source / "user_data" / "temp" / "cached.pyo")

    package_module.copy_private_user_data(source, package)

    assert (package / "user_data" / "config" / "api_profiles.json").read_text(
        encoding="utf-8"
    ) == '{"api_key":"synthetic-test-key"}'
    assert (package / "user_data" / "databases" / "grading_system.db").exists()
    assert (package / "user_data" / "workspaces" / "class-teacher" / "private.db").exists()
    assert not (package / "user_data" / "__pycache__").exists()
    assert not (package / "user_data" / "temp" / "cached.pyo").exists()
