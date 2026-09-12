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
