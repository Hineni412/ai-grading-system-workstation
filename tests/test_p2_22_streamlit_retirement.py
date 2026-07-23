from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RETIRED_FILES = (
    "web_app.py",
    "answer_region_editor_component.py",
    "answer_region_focus_page.py",
    "run_desktop.py",
)
RETIRED_DIRECTORIES = ("pages", "pages_shared")


def test_retired_streamlit_ui_paths_are_absent() -> None:
    remaining = [path for path in RETIRED_FILES if (ROOT / path).exists()]
    remaining.extend(
        str(path.relative_to(ROOT))
        for directory in RETIRED_DIRECTORIES
        for path in (ROOT / directory).rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )

    assert remaining == []


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
    for path in ROOT.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "import streamlit" in source or "from streamlit" in source:
            streamlit_importers.append(path.name)

    assert streamlit_importers == ["objective_admission_wizard_ui.py"]
