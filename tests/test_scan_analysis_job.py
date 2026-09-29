from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


def test_template_change_during_scan_analysis_preserves_previous_preflight(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.jobs.scan_analysis import run_scan_analysis
    from scanner import ScanAnalysis

    template_changed = False

    class FakeDb:
        @property
        def sessions(self):
            return self

        @property
        def templates(self):
            return self

        @property
        def students(self):
            return self

        def get_grading_session(self, session_id: int) -> dict[str, int]:
            return {"id": session_id}

        def is_template_ready(self, _session_id: int) -> bool:
            return True

        def list_students(self) -> list[dict[str, object]]:
            return [{"id": 1, "name": "Alice"}]

    class ChangingScanner:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def analyze(self, _students: list[dict[str, Any]]) -> ScanAnalysis:
            nonlocal template_changed
            template_changed = True
            return ScanAnalysis(total_pages=2)

    def current_template(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            template_id=1,
            template_fingerprint=("b" if template_changed else "a") * 64,
            first_page_role="front",
            is_confirmed=True,
            regions_snapshot_pending=False,
        )

    monkeypatch.setattr(
        "backend.jobs.scan_analysis.answer_regions_with_template_source_sizes",
        lambda _db, _session_id, data_root: [],
    )
    monkeypatch.setattr(
        "backend.jobs.scan_analysis.load_editor_config",
        lambda _db, _session_id: SimpleNamespace(revision="config-revision"),
    )
    monkeypatch.setattr(
        "backend.jobs.scan_analysis.TemplateUploadService.load_current",
        current_template,
    )
    scan_dir = tmp_path / "exams"
    scan_dir.mkdir()
    (scan_dir / "front.jpg").write_bytes(b"scan")
    work_dir = tmp_path / "templates" / "session_1"
    work_dir.mkdir(parents=True)
    latest = work_dir / "scan_analysis_latest.json"
    latest.write_text('{"version":"previous"}', encoding="utf-8")

    with pytest.raises(ValueError, match="template changed"):
        run_scan_analysis(
            db=FakeDb(),
            session_id=1,
            exams_dir=scan_dir,
            session_work_dir=work_dir,
            data_root=tmp_path,
            llm_client_factory=lambda: object(),
            scanner_factory=ChangingScanner,
            front_page_parity="odd",
            template_id=1,
            template_fingerprint="a" * 64,
            template_first_page_role="front",
            config_revision="config-revision",
        )

    assert latest.read_text(encoding="utf-8") == '{"version":"previous"}'
    assert list(work_dir.glob(".scan_analysis_latest.*.tmp")) == []
