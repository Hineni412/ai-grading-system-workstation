from __future__ import annotations

import json
import multiprocessing
import threading
from datetime import datetime
from pathlib import Path
from queue import Empty

import pytest

import backend.answer_regions.answer_region_draft_service as draft_module
from backend.answer_regions.answer_region_draft_service import AnswerRegionDraftService, DraftLoadResult
from backend.answer_regions.answer_region_session_lock import get_answer_region_session_lock


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


@pytest.mark.parametrize("schema", [None, 0, 99, "old-format"])
def test_saved_draft_reads_existing_fields_without_schema_or_template_gate(tmp_path, schema):
    service = AnswerRegionDraftService(tmp_path)
    saved = {"session_id": 1, "revision": 3, "regions": [{"region_uuid": "TEST-region"}],
             "template_fingerprint": "old-template"}
    if schema is not None:
        saved["schema_version"] = schema
    service.draft_path.write_text(json.dumps(saved), encoding="utf-8")
    before = service.draft_path.read_bytes()
    loaded = service.load(expected_template_fingerprint="new-template", expected_session_id=1)
    assert loaded.status == "compatible"
    assert loaded.draft["regions"] == saved["regions"]
    assert loaded.draft["revision"] == 3
    assert service.draft_path.read_bytes() == before
    assert list(tmp_path.glob("region_draft.corrupt-*.json")) == []
    service.save(session_id=1, template_fingerprint="new-template", expected_revision=3,
                 revision=4, regions=saved["regions"])
    assert service.load(expected_session_id=1).draft["revision"] == 4


@pytest.mark.parametrize("content", [b"{broken", b"\xff\xfe", b'{"regions": []}'])
def test_unreadable_draft_remains_in_place_and_explains_failure(tmp_path, content):
    service = AnswerRegionDraftService(tmp_path)
    service.draft_path.write_bytes(content)
    loaded = service.load(expected_session_id=1)
    assert loaded.status == "corrupt"
    assert loaded.reason and "已保留" in loaded.reason
    assert loaded.draft is None
    assert service.draft_path.read_bytes() == content
    assert list(tmp_path.glob("region_draft.corrupt-*.json")) == []
    with pytest.raises(ValueError, match="cannot be read"):
        service.save(session_id=1, template_fingerprint="template", expected_revision=0,
                     revision=1, regions=[])
    assert service.draft_path.read_bytes() == content


def test_reading_draft_keeps_session_identity_guard(tmp_path):
    service = AnswerRegionDraftService(tmp_path)
    service.save(session_id=2, template_fingerprint="template", revision=1, regions=[])
    before = service.draft_path.read_bytes()
    loaded = service.load(expected_session_id=1)
    assert loaded.status == "incompatible"
    assert loaded.draft is None
    assert "另一场考试" in loaded.reason
    assert service.draft_path.read_bytes() == before
