from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"

REQUIRED_SCRIPTS = {"dev", "build", "lint", "typecheck", "test", "e2e"}
REQUIRED_RUNTIME = {"vue", "element-plus", "pinia", "vue-router", "echarts"}
EXACT_VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?")


def _package() -> dict[str, object]:
    return json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))


def test_frontend_manifest_locks_dependencies_and_quality_commands() -> None:
    package = _package()

    assert package["private"] is True
    assert package["packageManager"] == "npm@11.8.0"
    assert package["engines"] == {
        "node": "^22.18.0 || >=24.12.0",
        "npm": "11.8.0",
    }
    assert REQUIRED_SCRIPTS <= package["scripts"].keys()
    assert package["scripts"]["e2e:install"] == "playwright install chromium"
    assert REQUIRED_RUNTIME <= package["dependencies"].keys()
    for section in ("dependencies", "devDependencies"):
        assert all(
            EXACT_VERSION.fullmatch(version)
            for version in package[section].values()
        )


def test_frontend_full_gate_typechecks_once_and_reuses_prepared_build() -> None:
    scripts = _package()["scripts"]

    assert scripts["verify"] == "npm run lint && npm run test && npm run build"
    assert scripts["build"] == "npm run typecheck && npm run build-only"
    assert scripts["e2e:p2-08:prepared"] == (
        "playwright test --config playwright.p2-08.config.ts"
    )
    assert scripts["e2e:p2-08"] == "npm run build && npm run e2e:p2-08:prepared"
    assert scripts["e2e:phase2-recalibration:prepared"] == (
        "npm run e2e:p2-08:prepared"
    )
    assert scripts["e2e:phase2-recalibration"] == (
        "npm run build && npm run e2e:phase2-recalibration:prepared"
    )


def test_frontend_lockfile_and_generated_outputs_are_governed() -> None:
    lockfile = FRONTEND / "package-lock.json"
    assert lockfile.is_file()
    assert json.loads(lockfile.read_text(encoding="utf-8"))["lockfileVersion"] == 3
    assert (FRONTEND / ".npmrc").read_text(encoding="utf-8").strip() == "engine-strict=true"

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (
        "node_modules/",
        "frontend/dist/",
        "frontend/coverage/",
        "frontend/playwright-report/",
        "frontend/test-results/",
    ):
        assert pattern in gitignore


def test_frontend_vite_proxy_stays_on_loopback_api() -> None:
    config = (FRONTEND / "vite.config.ts").read_text(encoding="utf-8")

    assert "'/api'" in config
    assert "http://127.0.0.1:8000" in config
    assert "0.0.0.0" not in config
