from __future__ import annotations

import json
import multiprocessing
import threading
from datetime import datetime
from pathlib import Path
from queue import Empty

import pytest

import answer_region_draft_service as draft_module
from answer_region_draft_service import AnswerRegionDraftService, DraftLoadResult
from answer_region_session_lock import get_answer_region_session_lock


def test_write_failure_preserves_good_draft_and_removes_partial_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AnswerRegionDraftService(tmp_path / "session")
    service.save(
        session_id=1,
        template_fingerprint="fingerprint",
        revision=1,
        regions=[{"region_uuid": "good-region"}],
    )
    original_draft = service.draft_path.read_bytes()
    original_write_text = Path.write_text

    def fail_after_partial_write(path: Path, *args: object, **kwargs: object) -> int:
        if path == service.temp_path:
            path.write_bytes(b"partial-temp")
            raise OSError("simulated write failure")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_after_partial_write)

    with pytest.raises(OSError, match="simulated write failure"):
        service.save(
            session_id=1,
            template_fingerprint="fingerprint",
            revision=2,
            regions=[{"region_uuid": "replacement-region"}],
        )

    assert service.draft_path.read_bytes() == original_draft
    assert not service.temp_path.exists()
