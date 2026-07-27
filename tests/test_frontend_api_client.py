from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"


def _application_sources() -> list[Path]:
    return sorted(
        path
        for suffix in ("*.ts", "*.vue")
        for path in SRC.rglob(suffix)
        if "__tests__" not in path.parts
    )


def test_p2_04_has_one_fetch_boundary_and_no_absolute_api_urls() -> None:
    sources = _application_sources()
    direct_fetch = [
        path for path in sources if "fetch(" in path.read_text(encoding="utf-8")
    ]
    assert direct_fetch == [SRC / "api" / "client.ts"]
    for path in sources:
        source = path.read_text(encoding="utf-8")
        assert "http://127.0.0.1:8000/api" not in source
        assert re.search(r"https?://[^\"']+/api(?:/|[\"'])", source) is None


def test_p2_04_job_storage_is_minimal_and_no_visible_ui_was_added() -> None:
    store = (SRC / "stores" / "jobs.ts").read_text(encoding="utf-8")
    persistence = store.split("function persistReferences", 1)[1].split(
        "function readReferences", 1
    )[0]
    assert "JSON.stringify" in persistence
    assert "payload" not in persistence
    assert "result" not in persistence
    assert "api_key" not in persistence
    assert not (SRC / "components" / "JobNotifications.vue").exists()
    assert not (SRC / "styles" / "job-notifications.css").exists()
