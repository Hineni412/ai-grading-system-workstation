from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SOURCE_DIRECTORIES = (
    "backend",
    "components",
    "frontend/src",
    "integration",
    "migrations",
    "question_bank",
    "tools",
    "update_tools",
)
SOURCE_SUFFIXES = {
    ".bat",
    ".css",
    ".html",
    ".js",
    ".json",
    ".ps1",
    ".py",
    ".toml",
    ".ts",
    ".txt",
    ".vue",
    ".yaml",
    ".yml",
}
RETIRED_FILES = (
    "web_app.py",
    "answer_region_editor_component.py",
    "answer_region_focus_page.py",
    "run_desktop.py",
)
RETIRED_DIRECTORIES = ("pages", "pages_shared")
RETIRED_RUNTIME_MARKERS = (
    "web_app.py",
    "pages/知识点整理（高级）.py",
    "pages/系统自检.py",
    "pages/组卷.py",
    "pages/训练推荐.py",
    "pages/题库管理.py",
    "from pages",
    "import pages",
    "pages_shared",
    "answer_region_editor_component",
    "answer_region_focus_page",
    "run_desktop.py",
    "USE_STREAMLIT",
    "streamlit-drawable-canvas",
)
HISTORICAL_RUNTIME_ALLOWLIST = {"tools/p1_29_acceptance.py"}
STREAMLIT_IMPORT_ALLOWLIST = {"objective_admission_wizard_ui.py"}


def _source_files() -> list[Path]:
    files = [
        path
        for path in ROOT.iterdir()
        if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES
    ]
    for directory in SOURCE_DIRECTORIES:
        source_root = ROOT / directory
        files.extend(
            path
            for path in source_root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in SOURCE_SUFFIXES
            and "__pycache__" not in path.parts
        )
    return sorted(set(files))


def test_retired_streamlit_ui_paths_are_absent() -> None:
    remaining = [path for path in RETIRED_FILES if (ROOT / path).exists()]
    remaining.extend(
        str(path.relative_to(ROOT))
        for directory in RETIRED_DIRECTORIES
        for path in (ROOT / directory).rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    remaining.extend(
        path.relative_to(ROOT).as_posix()
        for path in _source_files()
        if path.name in RETIRED_FILES
        or any(part in RETIRED_DIRECTORIES for part in path.relative_to(ROOT).parts)
    )

    assert sorted(set(remaining)) == []


def test_vue_editor_assets_are_preserved() -> None:
    editor = ROOT / "components" / "answer_region_editor"

    assert (editor / "editor.js").is_file()
    assert (editor / "editor.html").is_file()
    assert (editor / "editor.css").is_file()


def test_drawable_canvas_dependency_is_retired() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    constraints = (ROOT / "constraints.txt").read_text(encoding="utf-8")

    assert "streamlit-drawable-canvas" not in requirements
    assert "streamlit-drawable-canvas" not in constraints


def test_remaining_streamlit_entry_is_explicitly_deferred() -> None:
    streamlit_importers: list[str] = []
    for path in _source_files():
        if path.suffix.lower() != ".py":
            continue
        source = path.read_text(encoding="utf-8")
        if "import streamlit" in source or "from streamlit" in source:
            streamlit_importers.append(path.relative_to(ROOT).as_posix())

    assert streamlit_importers == sorted(STREAMLIT_IMPORT_ALLOWLIST)


def test_retired_runtime_markers_are_absent_outside_history_allowlist() -> None:
    violations: list[str] = []
    for path in _source_files():
        relative = path.relative_to(ROOT).as_posix()
        if relative in HISTORICAL_RUNTIME_ALLOWLIST:
            continue
        source = path.read_text(encoding="utf-8")
        for marker in RETIRED_RUNTIME_MARKERS:
            if marker in source:
                violations.append(f"{relative}: {marker}")

    assert violations == []
