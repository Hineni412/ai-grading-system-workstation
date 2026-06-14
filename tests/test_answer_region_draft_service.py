from __future__ import annotations

import gc
import json
import shutil
import threading
from dataclasses import FrozenInstanceError
from datetime import datetime
from pathlib import Path

import pytest

import answer_region_draft_service as draft_module
from answer_region_draft_service import AnswerRegionDraftService, DraftLoadResult


def _valid_draft(**overrides: object) -> dict[str, object]:
    draft: dict[str, object] = {
        "schema_version": 1,
        "session_id": 1,
        "template_fingerprint": "fingerprint",
        "revision": 1,
        "updated_at": "2026-06-14T00:00:00+00:00",
        "regions": [{"region_uuid": "region-one"}],
    }
    draft.update(overrides)
    return draft


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


@pytest.mark.parametrize(
    ("session_id", "template_fingerprint", "revision", "regions"),
    [
        (0, "fingerprint", 2, [{"region_uuid": "replacement-region"}]),
        (1, " ", 2, [{"region_uuid": "replacement-region"}]),
        (1, "fingerprint", -1, [{"region_uuid": "replacement-region"}]),
        (1, "fingerprint", 2, [{}]),
    ],
)
def test_save_rejects_invalid_draft_before_serialization_and_preserves_good_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    session_id: int,
    template_fingerprint: str,
    revision: int,
    regions: list[dict[str, object]],
) -> None:
    service = AnswerRegionDraftService(tmp_path / "session")
    service.save(
        session_id=1,
        template_fingerprint="fingerprint",
        revision=1,
        regions=[{"region_uuid": "good-region"}],
    )
    original_draft = service.draft_path.read_bytes()

    def fail_serialization(*args: object, **kwargs: object) -> str:
        raise AssertionError("invalid draft reached serialization")

    monkeypatch.setattr(draft_module.json, "dumps", fail_serialization)

    with pytest.raises(ValueError, match="invalid answer region draft"):
        service.save(
            session_id=session_id,
            template_fingerprint=template_fingerprint,
            revision=revision,
            regions=regions,
        )

    assert service.draft_path.read_bytes() == original_draft
    assert not service.temp_path.exists()


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


def test_quarantine_collision_never_overwrites_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AnswerRegionDraftService(tmp_path / "session")
    service.draft_path.parent.mkdir()
    existing_quarantine = service.draft_path.with_name(
        "region_draft.corrupt-controlled-collision.json"
    )
    unique_quarantine = service.draft_path.with_name(
        "region_draft.corrupt-controlled-unique.json"
    )
    existing_quarantine.write_bytes(b"existing-quarantine")
    service.draft_path.write_bytes(b"{new-corrupt-draft")
    suffixes = iter(["controlled-collision", "controlled-unique"])
    monkeypatch.setattr(
        draft_module,
        "_new_quarantine_suffix",
        lambda: next(suffixes),
        raising=False,
    )

    result = service.load(expected_template_fingerprint="fingerprint")

    assert result == DraftLoadResult(status="corrupt", quarantined_path=unique_quarantine)
    assert existing_quarantine.read_bytes() == b"existing-quarantine"
    assert unique_quarantine.read_bytes() == b"{new-corrupt-draft"
    assert not service.draft_path.exists()


def test_quarantine_falls_back_to_copy_when_hard_links_are_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AnswerRegionDraftService(tmp_path / "session")
    service.draft_path.parent.mkdir()
    existing_quarantine = service.draft_path.with_name(
        "region_draft.corrupt-fallback-collision.json"
    )
    unique_quarantine = service.draft_path.with_name(
        "region_draft.corrupt-fallback-unique.json"
    )
    existing_quarantine.write_bytes(b"existing-quarantine")
    corrupt_bytes = b"{corrupt-draft"
    service.draft_path.write_bytes(corrupt_bytes)
    suffixes = iter(["fallback-collision", "fallback-unique"])

    def fail_hard_link(*args: object, **kwargs: object) -> None:
        raise OSError("hard links unsupported")

    monkeypatch.setattr(draft_module.os, "link", fail_hard_link)
    monkeypatch.setattr(draft_module, "_new_quarantine_suffix", lambda: next(suffixes))

    result = service.load(expected_template_fingerprint="fingerprint")

    assert result.status == "corrupt"
    assert result.quarantined_path == unique_quarantine
    assert existing_quarantine.read_bytes() == b"existing-quarantine"
    assert result.quarantined_path.read_bytes() == corrupt_bytes
    assert not service.draft_path.exists()


def test_quarantine_copy_failure_preserves_original_and_cleans_partial_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AnswerRegionDraftService(tmp_path / "session")
    service.draft_path.parent.mkdir()
    corrupt_bytes = b"{corrupt-draft"
    service.draft_path.write_bytes(corrupt_bytes)

    def fail_hard_link(*args: object, **kwargs: object) -> None:
        raise OSError("hard links unsupported")

    def fail_after_partial_copy(source: object, destination: object) -> None:
        destination.write(b"partial quarantine")  # type: ignore[attr-defined]
        raise OSError("simulated copy failure")

    monkeypatch.setattr(draft_module.os, "link", fail_hard_link)
    monkeypatch.setattr(shutil, "copyfileobj", fail_after_partial_copy)

    with pytest.raises(OSError, match="simulated copy failure"):
        service.load(expected_template_fingerprint="fingerprint")

    assert service.draft_path.read_bytes() == corrupt_bytes
    assert not list(service.draft_path.parent.glob("region_draft.corrupt-*.json"))


def test_transient_read_oserror_propagates_without_quarantining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AnswerRegionDraftService(tmp_path / "session")
    service.save(
        session_id=1,
        template_fingerprint="fingerprint",
        revision=1,
        regions=[{"region_uuid": "region-one"}],
    )
    original_bytes = service.draft_path.read_bytes()

    def fail_read_text(_path: Path, *args: object, **kwargs: object) -> str:
        raise PermissionError("temporarily locked")

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    with pytest.raises(PermissionError, match="temporarily locked"):
        service.load(expected_template_fingerprint="fingerprint")

    assert service.draft_path.read_bytes() == original_bytes
    assert not list(service.draft_path.parent.glob("region_draft.corrupt-*.json"))


@pytest.mark.parametrize(
    "invalid_draft",
    [
        [],
        {},
        _valid_draft(schema_version=True),
        _valid_draft(schema_version=0),
        _valid_draft(schema_version=2),
        _valid_draft(session_id=True),
        _valid_draft(session_id=0),
        _valid_draft(template_fingerprint=" "),
        _valid_draft(revision=True),
        _valid_draft(revision=-1),
        _valid_draft(updated_at=" "),
        _valid_draft(regions=["not-a-dict"]),
        _valid_draft(regions=[{}]),
        _valid_draft(regions=[{"region_uuid": " "}]),
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


def test_invalid_iso_timestamp_draft_is_quarantined(tmp_path: Path) -> None:
    service = AnswerRegionDraftService(tmp_path / "session")
    service.draft_path.parent.mkdir()
    service.draft_path.write_text(
        json.dumps(_valid_draft(updated_at="not-an-iso-timestamp")),
        encoding="utf-8",
    )

    result = service.load(expected_template_fingerprint="fingerprint")

    assert result.status == "corrupt"
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


def test_serialization_failure_preserves_good_draft_and_existing_temp(tmp_path: Path) -> None:
    service = AnswerRegionDraftService(tmp_path / "session")
    service.save(
        session_id=1,
        template_fingerprint="fingerprint",
        revision=1,
        regions=[{"region_uuid": "good-region"}],
    )
    original_draft = service.draft_path.read_bytes()
    service.temp_path.write_bytes(b"recoverable-stale-temp")

    with pytest.raises(TypeError):
        service.save(
            session_id=1,
            template_fingerprint="fingerprint",
            revision=2,
            regions=[{"region_uuid": "bad-region", "not_json": {1, 2, 3}}],
        )

    assert service.draft_path.read_bytes() == original_draft
    assert service.temp_path.read_bytes() == b"recoverable-stale-temp"


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


def test_replace_failure_preserves_good_draft_and_discard_removes_valid_temp(
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
    original_replace = Path.replace

    def fail_replace(path: Path, target: Path) -> Path:
        if path == service.temp_path:
            raise OSError("simulated replace failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        service.save(
            session_id=1,
            template_fingerprint="fingerprint",
            revision=2,
            regions=[{"region_uuid": "replacement-region"}],
        )

    assert service.draft_path.read_bytes() == original_draft
    stale_temp = json.loads(service.temp_path.read_text(encoding="utf-8"))
    assert stale_temp["revision"] == 2
    assert stale_temp["regions"] == [{"region_uuid": "replacement-region"}]

    service.discard()
    assert not service.draft_path.exists()
    assert not service.temp_path.exists()


def test_concurrent_service_instances_leave_one_valid_draft_and_no_temp(tmp_path: Path) -> None:
    services = [AnswerRegionDraftService(tmp_path / "session") for _ in range(8)]
    start = threading.Barrier(len(services))
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def save_repeatedly(worker: int, service: AnswerRegionDraftService) -> None:
        try:
            start.wait()
            for revision in range(20):
                service.save(
                    session_id=1,
                    template_fingerprint="fingerprint",
                    revision=worker * 100 + revision,
                    regions=[{"region_uuid": f"region-{worker}-{revision}"}],
                )
        except BaseException as exc:
            with errors_lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=save_repeatedly, args=(worker, service))
        for worker, service in enumerate(services)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    result = services[0].load(expected_template_fingerprint="fingerprint")
    assert result.status == "compatible"
    assert result.draft is not None
    assert result.draft["regions"][0]["region_uuid"].startswith("region-")
    assert not services[0].temp_path.exists()


def test_lock_registry_shares_active_lock_and_releases_inactive_path(tmp_path: Path) -> None:
    first_service = AnswerRegionDraftService(tmp_path / "session")
    second_service = AnswerRegionDraftService(tmp_path / "session")
    resolved_path = first_service.draft_path.resolve(strict=False)

    assert first_service._lock is second_service._lock
    assert draft_module._DRAFT_LOCKS[resolved_path] is first_service._lock

    del first_service
    gc.collect()
    assert resolved_path in draft_module._DRAFT_LOCKS

    del second_service
    gc.collect()
    assert resolved_path not in draft_module._DRAFT_LOCKS


def test_save_cannot_race_between_corrupt_read_and_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class TrackingLock:
        def __init__(self) -> None:
            self._lock = threading.RLock()
            self.save_checked = threading.Event()
            self.save_was_blocked: bool | None = None

        def __enter__(self) -> TrackingLock:
            if threading.current_thread().name == "save-draft":
                acquired = self._lock.acquire(blocking=False)
                self.save_was_blocked = not acquired
                self.save_checked.set()
                if not acquired:
                    self._lock.acquire()
            else:
                self._lock.acquire()
            return self

        def __exit__(self, *args: object) -> None:
            self._lock.release()

    tracking_lock = TrackingLock()
    monkeypatch.setattr(draft_module, "_lock_for", lambda _path: tracking_lock)
    service_to_load = AnswerRegionDraftService(tmp_path / "session")
    service_to_save = AnswerRegionDraftService(tmp_path / "session")
    service_to_load.draft_path.parent.mkdir()
    service_to_load.draft_path.write_bytes(b"{corrupt-original")
    parsing_started = threading.Event()
    allow_parsing = threading.Event()
    original_loads = draft_module.json.loads

    def controlled_loads(value: str, *args: object, **kwargs: object) -> object:
        if value == "{corrupt-original":
            parsing_started.set()
            assert allow_parsing.wait(timeout=5)
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(draft_module.json, "loads", controlled_loads)
    load_results: list[DraftLoadResult] = []
    save_errors: list[BaseException] = []

    def load_corrupt() -> None:
        load_results.append(service_to_load.load(expected_template_fingerprint="fingerprint"))

    def save_good() -> None:
        try:
            service_to_save.save(
                session_id=1,
                template_fingerprint="fingerprint",
                revision=2,
                regions=[{"region_uuid": "saved-after-quarantine"}],
            )
        except BaseException as exc:
            save_errors.append(exc)

    load_thread = threading.Thread(target=load_corrupt, name="load-draft")
    save_thread = threading.Thread(target=save_good, name="save-draft")
    load_thread.start()
    assert parsing_started.wait(timeout=5)
    save_thread.start()
    assert tracking_lock.save_checked.wait(timeout=5)
    assert tracking_lock.save_was_blocked is True
    allow_parsing.set()
    load_thread.join()
    save_thread.join()

    assert save_errors == []
    assert load_results[0].status == "corrupt"
    assert load_results[0].quarantined_path is not None
    assert load_results[0].quarantined_path.read_bytes() == b"{corrupt-original"
    result = service_to_load.load(expected_template_fingerprint="fingerprint")
    assert result.status == "compatible"
    assert result.draft is not None
    assert result.draft["regions"] == [{"region_uuid": "saved-after-quarantine"}]


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
