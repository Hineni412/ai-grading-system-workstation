from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
COLOR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|hsl)a?\(")


def test_p2_04_keeps_one_navigation_source_and_adds_the_general_client() -> None:
    navigation = (SRC / "navigation.ts").read_text(encoding="utf-8")
    assert navigation.count("futureReason: '") == 1
    assert "智能体与自动化" in navigation
    assert (SRC / "api" / "client.ts").is_file()
    assert (SRC / "stores" / "jobs.ts").is_file()


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
        "--shell-topbar-z-index: 32": "var(--shell-topbar-z-index)",
        "--shell-skip-link-z-index: 33": "var(--shell-skip-link-z-index)",
    }
    for definition, reference in required_tokens.items():
        assert definition in tokens
        assert reference in css
    assert "z-index: calc(" not in css
    assert "@media (min-width: 1024px) and (max-width: 1279px)" in css
    assert "@media (max-width: 1023px)" not in css
    assert "@media (max-width: 767px)" not in css
    assert "--shell-overlay-z-index" not in tokens
    assert "--shell-panel-z-index" not in tokens
    assert "import './styles/app-shell.css'" in main
    assert main.index("import './styles/base.css'") < main.index(
        "import './styles/app-shell.css'"
    )
    router = (SRC / "router" / "index.ts").read_text(encoding="utf-8")
    assert "'/design-system'" in router
    assert "ComponentShowcase" in router


def test_p2_03_supports_only_the_approved_desktop_viewports() -> None:
    shell = (SRC / "layouts" / "AppShell.vue").read_text(encoding="utf-8")
    e2e = (ROOT / "frontend" / "e2e" / "app-shell.spec.ts").read_text(
        encoding="utf-8"
    )
    approved_viewports = [
        ("large desktop", 1920, 1080),
        ("desktop", 1440, 900),
        ("standard workstation", 1366, 768),
        ("compact desktop", 1280, 800),
        ("minimum desktop", 1024, 768),
    ]

    assert "data-overlay" not in shell
    assert "max-width: 1023px" not in shell
    assert "shell-backdrop" not in shell
    assert "focusableSelector" not in shell
    assert "mobile" not in e2e.lower()
    assert "tablet" not in e2e.lower()
    for name, width, height in approved_viewports:
        assert (
            f"{{ name: '{name}', width: {width}, height: {height}" in e2e
        )
