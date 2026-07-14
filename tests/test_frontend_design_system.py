from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
TOKENS = SRC / "styles" / "tokens.css"
COLOR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|hsl)a?\(")
DESIGN_PROPERTY = (
    r"(?:margin|padding)(?:-[a-z]+)*"
    r"|(?:row-|column-)?gap"
    r"|border(?:-[a-z]+)*-radius"
    r"|box-shadow"
)
NONZERO_DESIGN_NUMBER = (
    r"(?<![\w.-])-?"
    r"(?:0*\.[0-9]*[1-9][0-9]*|0*[1-9][0-9]*(?:\.[0-9]+)?)"
    r"(?:px|rem|em|%)?"
)
RAW_DESIGN_VALUE = re.compile(
    rf"(?<![\w-])(?:{DESIGN_PROPERTY})\s*:\s*[^;{{}}]*?"
    rf"{NONZERO_DESIGN_NUMBER}",
    re.IGNORECASE,
)


def test_design_tokens_define_the_approved_visual_contract() -> None:
    css = TOKENS.read_text(encoding="utf-8")
    required = {
        "--color-accent: #2563eb",
        "--color-accent-hover: #1d4ed8",
        "--space-1: 4px",
        "--space-9: 48px",
        "--radius-control: 6px",
        "--radius-overlay: 12px",
        "--shadow-overlay:",
        "--duration-fast: 100ms",
        "--duration-base: 160ms",
    }
    lowered = css.lower()
    assert all(token in lowered for token in required)


def test_colors_and_design_values_do_not_escape_the_token_file() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*"):
        if path.suffix not in {".css", ".vue"} or path == TOKENS:
            continue
        text = path.read_text(encoding="utf-8")
        if COLOR_LITERAL.search(text) or RAW_DESIGN_VALUE.search(text):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_question_selection_uses_a_border_token_not_a_document_flow_shadow() -> None:
    tokens = TOKENS.read_text(encoding="utf-8")
    queue_css = (SRC / "styles" / "review-queue.css").read_text(encoding="utf-8")

    assert "--border-selected-width: 4px" in tokens
    assert "border-block-end: var(--border-selected-width) solid" in queue_css
    assert "--shadow-selected" not in tokens
    assert "box-shadow: var(--shadow-selected" not in queue_css


@pytest.mark.parametrize(
    "declaration",
    (
        "padding-inline: 13px;",
        "margin: var(--space-2) 19px;",
        "row-gap: 0.5rem;",
        "box-shadow: 0 2px 8px var(--color-border-default);",
    ),
)
def test_design_value_guard_rejects_logical_multi_value_and_shadow_bypasses(
    declaration: str,
) -> None:
    assert RAW_DESIGN_VALUE.search(declaration)


def test_element_theme_maps_product_tokens() -> None:
    css = (SRC / "styles" / "element-theme.css").read_text(encoding="utf-8")
    for mapping in (
        "--el-color-primary: var(--color-accent)",
        "--el-color-success: var(--color-success)",
        "--el-color-warning: var(--color-warning)",
        "--el-color-danger: var(--color-danger)",
        "--el-border-radius-base: var(--radius-control)",
        "--el-text-color-secondary: var(--color-text-secondary)",
        "--el-text-color-placeholder: var(--color-text-secondary)",
    ):
        assert mapping in css

    derived = {
        "primary": ("accent", "accent-active"),
        "success": ("success", "success"),
        "warning": ("warning", "warning"),
        "danger": ("danger", "danger"),
        "info": ("info", "info"),
    }
    for role, (product_role, dark_role) in derived.items():
        for level in (3, 5, 8, 9):
            assert (
                f"--el-color-{role}-light-{level}: "
                f"var(--color-{product_role}-subtle)"
            ) in css
        assert (
            f"--el-color-{role}-dark-2: var(--color-{dark_role})"
        ) in css
    assert "--el-color-primary-light-7: var(--color-accent-subtle)" in css


def test_element_plus_stays_scoped_to_the_design_system_components() -> None:
    main = (SRC / "main.ts").read_text(encoding="utf-8")
    app = (SRC / "App.vue").read_text(encoding="utf-8")

    assert "element-plus/dist/index.css" not in main
    assert ".use(ElementPlus" not in main
    for stylesheet in (
        "element-plus/theme-chalk/base.css",
        "element-plus/theme-chalk/el-icon.css",
        "element-plus/theme-chalk/el-button.css",
        "element-plus/theme-chalk/el-input.css",
    ):
        assert stylesheet in main
    assert "ElConfigProvider" in app
    assert "element-plus/es/locale/lang/zh-cn" in app
