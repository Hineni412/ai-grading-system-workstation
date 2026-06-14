from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import datetime
from pathlib import Path

import pytest

from answer_region_draft_service import AnswerRegionDraftService, DraftLoadResult


def test_exposes_frozen_load_result_required_paths_and_public_methods(tmp_path: Path) -> None:
    service = AnswerRegionDraftService(tmp_path / "session")
    result = DraftLoadResult(status="missing")

    assert service.draft_path == tmp_path / "session" / "region_draft.json"
    assert service.temp_path == tmp_path / "session" / "region_draft.json.tmp"
    assert result.draft is None
    assert result.quarantined_path is None
    assert {
        name
        for name, value in AnswerRegionDraftService.__dict__.items()
        if not name.startswith("_") and callable(value)
    } == {"compute_template_fingerprint", "save", "load", "discard"}
    with pytest.raises(FrozenInstanceError):
        result.status = "compatible"  # type: ignore[misc]


def test_template_fingerprint_is_stable_and_changes_with_order_or_content(tmp_path: Path) -> None:
    front_path = tmp_path / "front.png"
    back_path = tmp_path / "back.png"
    front_path.write_bytes(b"front-content")
    back_path.write_bytes(b"back-content")
    service = AnswerRegionDraftService(tmp_path / "session")

    original = service.compute_template_fingerprint(front_path, back_path)

    assert service.compute_template_fingerprint(front_path, back_path) == original
    assert service.compute_template_fingerprint(back_path, front_path) != original

    back_path.write_bytes(b"changed-back-content")
    assert service.compute_template_fingerprint(front_path, back_path) != original


def test_template_fingerprint_frames_page_content_unambiguously(tmp_path: Path) -> None:
    first_front = tmp_path / "first-front.png"
    first_back = tmp_path / "first-back.png"
    second_front = tmp_path / "second-front.png"
    second_back = tmp_path / "second-back.png"
    first_front.write_bytes(b"a")
    first_back.write_bytes(b"bc")
    second_front.write_bytes(b"ab")
    second_back.write_bytes(b"c")
    service = AnswerRegionDraftService(tmp_path / "session")

    assert service.compute_template_fingerprint(
        first_front, first_back
    ) != service.compute_template_fingerprint(second_front, second_back)


def test_load_returns_missing_when_no_draft_exists(tmp_path: Path) -> None:
    service = AnswerRegionDraftService(tmp_path / "session")

    assert service.load(expected_template_fingerprint="fingerprint") == DraftLoadResult(
        status="missing"
    )


def test_atomic_save_preserves_unicode_and_compatible_load_returns_draft(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    service = AnswerRegionDraftService(session_dir)
    regions = [{"region_uuid": "区域一", "mapped_question_id": "第一题"}]

    saved_path = service.save(
        session_id=42,
        template_fingerprint="template-sha256",
        revision=7,
        regions=regions,
    )

    assert saved_path == service.draft_path
    assert saved_path.exists()
    assert not service.temp_path.exists()
    raw_json = saved_path.read_text(encoding="utf-8")
    assert "区域一" in raw_json
    draft = json.loads(raw_json)
    assert draft == {
        "schema_version": 1,
        "session_id": 42,
        "template_fingerprint": "template-sha256",
        "revision": 7,
        "updated_at": draft["updated_at"],
        "regions": regions,
    }
    assert datetime.fromisoformat(draft["updated_at"])
    assert service.load(expected_template_fingerprint="template-sha256") == DraftLoadResult(
        status="compatible",
        draft=draft,
    )


def test_incompatible_load_leaves_draft_untouched(tmp_path: Path) -> None:
    service = AnswerRegionDraftService(tmp_path / "session")
    service.save(
        session_id=8,
        template_fingerprint="original-fingerprint",
        revision=3,
        regions=[{"region_uuid": "region-one"}],
    )
    original_bytes = service.draft_path.read_bytes()

    result = service.load(expected_template_fingerprint="different-fingerprint")

    assert result.status == "incompatible"
    assert result.draft is not None
    assert result.draft["template_fingerprint"] == "original-fingerprint"
    assert result.quarantined_path is None
    assert service.draft_path.read_bytes() == original_bytes
    assert not list(service.draft_path.parent.glob("region_draft.corrupt-*.json"))


@pytest.mark.parametrize("corrupt_bytes", [b"{not-json", b"\xff\xfe\xfa"])
def test_corrupt_or_unreadable_draft_is_quarantined(
    tmp_path: Path, corrupt_bytes: bytes
) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    service = AnswerRegionDraftService(session_dir)
    formal_path = session_dir / "formal-regions.json"
    formal_path.write_bytes(b"formal-data")
    service.draft_path.write_bytes(corrupt_bytes)

    result = service.load(expected_template_fingerprint="fingerprint")

    assert result == DraftLoadResult(status="corrupt", quarantined_path=result.quarantined_path)
    assert result.quarantined_path is not None
    assert result.quarantined_path.parent == session_dir
    assert result.quarantined_path.name.startswith("region_draft.corrupt-")
    assert result.quarantined_path.name.endswith(".json")
    assert result.quarantined_path.read_bytes() == corrupt_bytes
    assert not service.draft_path.exists()
    assert formal_path.read_bytes() == b"formal-data"


@pytest.mark.parametrize(
    "invalid_draft",
    [
        [],
        {},
        {
            "schema_version": 1,
            "session_id": 1,
            "template_fingerprint": "fingerprint",
            "revision": 1,
            "updated_at": "2026-06-14T00:00:00+00:00",
            "regions": ["not-a-dict"],
        },
        {
            "schema_version": True,
            "session_id": 1,
            "template_fingerprint": "fingerprint",
            "revision": 1,
            "updated_at": "2026-06-14T00:00:00+00:00",
            "regions": [],
        },
    ],
)
def test_invalid_shape_draft_is_quarantined(tmp_path: Path, invalid_draft: object) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    service = AnswerRegionDraftService(session_dir)
    service.draft_path.write_text(
        json.dumps(invalid_draft, ensure_ascii=False),
        encoding="utf-8",
    )

    result = service.load(expected_template_fingerprint="fingerprint")

    assert result.status == "corrupt"
    assert result.draft is None
    assert result.quarantined_path is not None
    assert result.quarantined_path.exists()
    assert not service.draft_path.exists()


def test_load_ignores_stale_temp_and_successful_save_removes_it(tmp_path: Path) -> None:
    service = AnswerRegionDraftService(tmp_path / "session")
    service.temp_path.parent.mkdir()
    service.temp_path.write_text("interrupted-save", encoding="utf-8")

    assert service.load(expected_template_fingerprint="fingerprint").status == "missing"
    assert service.temp_path.read_text(encoding="utf-8") == "interrupted-save"

    service.save(
        session_id=2,
        template_fingerprint="fingerprint",
        revision=1,
        regions=[],
    )

    assert service.draft_path.exists()
    assert not service.temp_path.exists()


def test_discard_removes_draft_and_stale_temp_but_preserves_quarantine(tmp_path: Path) -> None:
    service = AnswerRegionDraftService(tmp_path / "session")
    service.draft_path.parent.mkdir()
    quarantined_path = service.draft_path.parent / "region_draft.corrupt-existing.json"
    service.draft_path.write_text("draft", encoding="utf-8")
    service.temp_path.write_text("temp", encoding="utf-8")
    quarantined_path.write_text("quarantined", encoding="utf-8")

    service.discard()

    assert not service.draft_path.exists()
    assert not service.temp_path.exists()
    assert quarantined_path.read_text(encoding="utf-8") == "quarantined"

    service.discard()
