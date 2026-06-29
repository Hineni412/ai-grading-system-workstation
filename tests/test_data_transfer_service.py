from __future__ import annotations

import importlib.util
from pathlib import Path

from data_transfer_service import build_export_manifest, default_export_sources


def _write(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _arc_names(paths) -> set[str]:
    return {entry.arc_name for entry in paths}


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

    entries = build_export_manifest(default_export_sources(project_root, data_root), scope="full")
    arc_names = _arc_names(entries)

    assert "user_data/annotated/session_1/marked.jpg" in arc_names
    assert "user_data/config/api_profiles.json" not in arc_names
    assert "user_data/__pycache__/module.pyc" not in arc_names


def test_private_package_excludes_legacy_api_profiles(tmp_path: Path) -> None:
    module_path = Path(__file__).resolve().parents[1] / "package_v1.5.0.py"
    spec = importlib.util.spec_from_file_location("package_v1_5_0", module_path)
    assert spec is not None and spec.loader is not None
    package_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package_module)

    source = tmp_path / "source"
    package = tmp_path / "package"
    _write(source / "user_data" / "config" / "api_profiles.json")
    _write(source / "user_data" / "databases" / "grading_system.db")

    package_module.copy_private_user_data(source, package)

    assert not (package / "user_data" / "config" / "api_profiles.json").exists()
    assert (package / "user_data" / "databases" / "grading_system.db").exists()
