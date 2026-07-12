from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
TOKENS = SRC / "styles" / "tokens.css"
COLOR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|hsl)a?\(")
RAW_DESIGN_VALUE = re.compile(
    r"(?:margin|padding|gap|border-radius|box-shadow)\s*:\s*"
    r"(?!0(?:[;\s]|$))\d"
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


def test_element_theme_maps_product_tokens() -> None:
    css = (SRC / "styles" / "element-theme.css").read_text(encoding="utf-8")
    for mapping in (
        "--el-color-primary: var(--color-accent)",
        "--el-color-success: var(--color-success)",
        "--el-color-warning: var(--color-warning)",
        "--el-color-danger: var(--color-danger)",
        "--el-border-radius-base: var(--radius-control)",
    ):
        assert mapping in css


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
