from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
COLOR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|hsl)a?\(")


def test_p2_03_has_one_navigation_source_and_no_general_api_client() -> None:
    navigation = (SRC / "navigation.ts").read_text(encoding="utf-8")
    assert navigation.count("availability: 'future'") == 1
    assert "智能体与自动化" in navigation
    assert not (SRC / "api" / "client.ts").exists()
    assert not (SRC / "stores" / "jobs.ts").exists()


def test_shell_uses_tokens_and_keeps_showcase_route() -> None:
    css = (SRC / "styles" / "app-shell.css").read_text(encoding="utf-8")
    assert not COLOR_LITERAL.search(css)
    assert "var(--shell-navigation-width)" in css
    assert "var(--shell-inspector-width)" in css
    router = (SRC / "router" / "index.ts").read_text(encoding="utf-8")
    assert "'/design-system'" in router
    assert "ComponentShowcase" in router
