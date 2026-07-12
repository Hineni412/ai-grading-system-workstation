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
    tokens = (SRC / "styles" / "tokens.css").read_text(encoding="utf-8")
    main = (SRC / "main.ts").read_text(encoding="utf-8")
    assert not COLOR_LITERAL.search(css)
    required_tokens = {
        "--shell-topbar-height: 60px": "var(--shell-topbar-height)",
        "--shell-navigation-width: 232px": "var(--shell-navigation-width)",
        "--shell-navigation-collapsed-width: 60px": "var(--shell-navigation-collapsed-width)",
        "--shell-inspector-width: 360px": "var(--shell-inspector-width)",
        "--shell-inspector-compact-width: 320px": "var(--shell-inspector-compact-width)",
        "--shell-overlay-z-index: 30": "var(--shell-overlay-z-index)",
        "--shell-panel-z-index: 31": "var(--shell-panel-z-index)",
        "--shell-topbar-z-index: 32": "var(--shell-topbar-z-index)",
        "--shell-skip-link-z-index: 33": "var(--shell-skip-link-z-index)",
    }
    for definition, reference in required_tokens.items():
        assert definition in tokens
        assert reference in css
    assert "z-index: calc(" not in css
    assert "@media (min-width: 1024px) and (max-width: 1279px)" in css
    assert "@media (max-width: 1023px)" in css
    assert "@media (max-width: 767px)" in css
    assert "import './styles/app-shell.css'" in main
    assert main.index("import './styles/base.css'") < main.index(
        "import './styles/app-shell.css'"
    )
    router = (SRC / "router" / "index.ts").read_text(encoding="utf-8")
    assert "'/design-system'" in router
    assert "ComponentShowcase" in router
