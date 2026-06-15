from __future__ import annotations

import inspect
import json
import multiprocessing
import threading
from dataclasses import FrozenInstanceError
from pathlib import Path
from queue import Empty

import pytest

import answer_region_commit_service as commit_module
from answer_region_commit_service import AnswerRegionCommitResult, AnswerRegionCommitService
from answer_region_draft_service import AnswerRegionDraftService
from answer_region_models import RegionValidationResult
from answer_region_session_lock import get_answer_region_session_lock
from db_manager import DBManager


IMAGE_SIZES = {"front": (200, 300), "back": (200, 300)}


class _PausingDraftService(AnswerRegionDraftService):
    def __init__(self, session_dir: Path, events: object, release: object) -> None:
        super().__init__(session_dir)
        self._events = events
        self._release = release

    def discard(self) -> None:
        self._events.put(("older", "completion"))
        if not self._release.wait(10):
            raise TimeoutError("older completion was not released")
        super().discard()


class _ReportingDBManager(DBManager):
    def __init__(self, db_path: Path, events: object) -> None:
        super().__init__(db_path)
        self._events = events

    def replace_answer_regions_atomic(self, *args: object, **kwargs: object) -> str:
        self._events.put(("newer_commit", "entered"))
        return super().replace_answer_regions_atomic(*args, **kwargs)


def _older_completion_in_process(
    db_path: str,
    session_dir: str,
    session_id: int,
    template_id: int,
    events: object,
    release: object,
) -> None:
    service = AnswerRegionCommitService(
        DBManager(Path(db_path)),
        Path(session_dir),
        _PausingDraftService(Path(session_dir), events, release),
    )
    result = service.commit(
        session_id=session_id,
        template_id=template_id,
        regions=[_region("older-commit")],
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )
    events.put(("older", "completed", result.error))


def _reported_commit_in_process(
    db_path: str,
    session_dir: str,
    session_id: int,
    template_id: int,
    events: object,
) -> None:
    events.put(("newer_commit", "started"))
    service = AnswerRegionCommitService(
        _ReportingDBManager(Path(db_path), events),
        Path(session_dir),
        AnswerRegionDraftService(Path(session_dir)),
    )
    result = service.commit(
        session_id=session_id,
        template_id=template_id,
        regions=[_region("newer-commit")],
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )
    events.put(("newer_commit", "completed", result.error))


def _reported_draft_save_in_process(session_dir: str, events: object) -> None:
    events.put(("draft_save", "started"))
    service = AnswerRegionDraftService(Path(session_dir))
    with get_answer_region_session_lock(Path(session_dir)):
        events.put(("draft_save", "entered"))
        service.save(
            session_id=1,
            template_fingerprint="fingerprint",
            revision=3,
            regions=[{"region_uuid": "child-draft"}],
        )
    events.put(("draft_save", "completed"))


def _commit_in_process(
    db_path: str,
    session_dir: str,
    session_id: int,
    template_id: int,
    events: object,
) -> None:
    events.put("started")
    service = AnswerRegionCommitService(
        DBManager(Path(db_path)),
        Path(session_dir),
        AnswerRegionDraftService(Path(session_dir)),
    )
    result = service.commit(
        session_id=session_id,
        template_id=template_id,
        regions=[_region("child-commit")],
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )
    events.put(("completed", result.error))


def _region(
    region_uuid: str,
    question_id: str | None = "Q1",
    *,
    x: int = 10,
    order: int = 1,
) -> dict[str, object]:
    return {
        "region_uuid": region_uuid,
        "page": "front",
        "region_order": order,
        "x": x,
        "y": 20,
        "w": 80,
        "h": 50,
        "detected_question_id": question_id,
        "mapped_question_id": question_id,
        "confidence": 0.9,
        "is_confirmed": True,
        "mapping_status": "manual",
        "multi_region_confirmed": False,
    }


def _setup(tmp_path: Path) -> tuple[DBManager, int, int, Path, AnswerRegionDraftService]:
    db = DBManager(tmp_path / "grading.db")
    db.initialize()
    session_id = db.create_grading_session("regions", "rubric.json", "answer.json")
    template_id = db.upsert_session_template(session_id, "front.png", "back.png")
    session_dir = tmp_path / "session"
    draft_service = AnswerRegionDraftService(session_dir)
    return db, session_id, template_id, session_dir, draft_service


def _save_draft(draft_service: AnswerRegionDraftService, session_id: int) -> None:
    draft_service.save(
        session_id=session_id,
        template_fingerprint="template-fingerprint",
        revision=1,
        regions=[_region("draft-region")],
    )


def _seed_formal(
    db: DBManager,
    session_id: int,
    template_id: int,
    region_uuid: str = "formal-region",
    *,
    pending: bool = False,
) -> str:
    token = db.replace_answer_regions_atomic(
        session_id,
        template_id,
        [_region(region_uuid)],
        confirmed=True,
    )
    if not pending:
        db.mark_region_snapshot_complete(session_id, expected_token=token)
    return token


def test_exposes_frozen_result_and_keyword_only_public_methods(tmp_path: Path) -> None:
    result = AnswerRegionCommitResult(
        committed=False,
        snapshot_pending=False,
        validation=RegionValidationResult(()),
    )

    assert result.snapshot_path is None
    assert result.error is None
    assert {
        name
        for name, value in AnswerRegionCommitService.__dict__.items()
        if not name.startswith("_") and callable(value)
    } == {"commit", "retry_pending_snapshot"}
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in list(inspect.signature(AnswerRegionCommitService.commit).parameters.values())[1:]
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in list(
            inspect.signature(AnswerRegionCommitService.retry_pending_snapshot).parameters.values()
        )[1:]
    )
    with pytest.raises(FrozenInstanceError):
        result.committed = True  # type: ignore[misc]


def test_commit_replaces_formal_regions_writes_both_snapshots_and_finishes_cleanup(
    tmp_path: Path,
) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    _seed_formal(db, session_id, template_id, "old-formal")
    _save_draft(draft_service, session_id)
    service = AnswerRegionCommitService(db, session_dir, draft_service)

    result = service.commit(
        session_id=session_id,
        template_id=template_id,
        regions=[_region("replacement", x=12)],
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )

    formal = db.list_answer_regions(session_id)
    workflow = json.loads((session_dir / "workflow_state.json").read_text(encoding="utf-8"))
    assert result.committed is True
    assert result.snapshot_pending is False
    assert result.validation.can_commit is True
    assert result.error is None
    assert result.snapshot_path is not None
    assert result.snapshot_path.name.startswith("regions_confirmed_")
    assert json.loads(result.snapshot_path.read_text(encoding="utf-8")) == formal
    assert [region["region_uuid"] for region in formal] == ["replacement"]
    assert workflow["session_id"] == session_id
    assert workflow["session_name"] == "regions"
    assert workflow["stage"] == "regions_confirmed"
    assert workflow["snapshot_token"] == result.snapshot_path.stem.removeprefix("regions_confirmed_")
    assert workflow["active_paths"]["regions_path"] is None
    assert workflow["template_ready"] is True
    assert workflow["region_count"] == 1
    assert workflow["progress"] == db.get_session_progress(session_id)
    assert workflow["extra"]["regions_path"] == str(result.snapshot_path)
    assert db.get_session_template(session_id)["regions_snapshot_pending"] == 0
    assert not draft_service.draft_path.exists()


def test_commit_preserves_existing_workflow_extra_fields_including_front_page_parity(
    tmp_path: Path,
) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    session_dir.mkdir(parents=True)
    (session_dir / "workflow_state.json").write_text(
        json.dumps(
            {
                "stage": "template_uploaded",
                "extra": {"front_page_parity": "odd", "keep_me": {"nested": True}},
            }
        ),
        encoding="utf-8",
    )
    service = AnswerRegionCommitService(db, session_dir, draft_service)

    result = service.commit(
        session_id=session_id,
        template_id=template_id,
        regions=[_region("replacement")],
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )

    workflow = json.loads((session_dir / "workflow_state.json").read_text(encoding="utf-8"))
    assert result.snapshot_path is not None
    assert workflow["extra"] == {
        "front_page_parity": "odd",
        "keep_me": {"nested": True},
        "regions_path": str(result.snapshot_path),
    }


def test_validation_failure_leaves_formal_regions_and_draft_untouched(tmp_path: Path) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    _seed_formal(db, session_id, template_id)
    _save_draft(draft_service, session_id)
    formal_before = db.list_answer_regions(session_id)
    draft_before = draft_service.draft_path.read_bytes()
    service = AnswerRegionCommitService(db, session_dir, draft_service)

    result = service.commit(
        session_id=session_id,
        template_id=template_id,
        regions=[_region("invalid", question_id=None)],
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )

    assert result.committed is False
    assert result.snapshot_pending is False
    assert result.validation.can_commit is False
    assert result.snapshot_path is None
    assert result.error is None
    assert db.list_answer_regions(session_id) == formal_before
    assert draft_service.draft_path.read_bytes() == draft_before
    assert not (session_dir / "workflow_state.json").exists()
    assert list(session_dir.glob("regions_confirmed_*.json")) == []


def test_commit_rechecks_current_template_fingerprint_before_formal_write(tmp_path: Path) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    session_dir.mkdir(parents=True)
    original_front = session_dir / "original-front.png"
    original_back = session_dir / "original-back.png"
    replacement_front = session_dir / "replacement-front.png"
    replacement_back = session_dir / "replacement-back.png"
    original_front.write_bytes(b"original-front")
    original_back.write_bytes(b"original-back")
    replacement_front.write_bytes(b"replacement-front")
    replacement_back.write_bytes(b"replacement-back")
    db.upsert_session_template(session_id, str(original_front), str(original_back))
    _seed_formal(db, session_id, template_id)
    _save_draft(draft_service, session_id)
    expected_fingerprint = draft_service.compute_template_fingerprint(original_front, original_back)
    formal_before = db.list_answer_regions(session_id)
    draft_before = draft_service.draft_path.read_bytes()
    db.upsert_session_template(session_id, str(replacement_front), str(replacement_back))
    service = AnswerRegionCommitService(db, session_dir, draft_service)

    result = service.commit(
        session_id=session_id,
        template_id=template_id,
        regions=[_region("stale-template-region")],
        image_sizes=IMAGE_SIZES,
        template_matches=True,
        expected_template_fingerprint=expected_fingerprint,
    )

    assert result.committed is False
    assert [issue.code for issue in result.validation.issues] == ["template_mismatch"]
    assert db.list_answer_regions(session_id) == formal_before
    assert draft_service.draft_path.read_bytes() == draft_before
    assert list(session_dir.glob("regions_confirmed_*.json")) == []


def test_database_failure_leaves_formal_regions_and_draft_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    _seed_formal(db, session_id, template_id)
    _save_draft(draft_service, session_id)
    formal_before = db.list_answer_regions(session_id)
    draft_before = draft_service.draft_path.read_bytes()
    service = AnswerRegionCommitService(db, session_dir, draft_service)

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(db, "replace_answer_regions_atomic", fail_replace)

    result = service.commit(
        session_id=session_id,
        template_id=template_id,
        regions=[_region("replacement")],
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )

    assert result.committed is False
    assert result.snapshot_pending is False
    assert result.validation.can_commit is True
    assert result.snapshot_path is None
    assert result.error == "database_commit_failed"
    assert db.list_answer_regions(session_id) == formal_before
    assert draft_service.draft_path.read_bytes() == draft_before
    assert list(session_dir.glob("regions_confirmed_*.json")) == []


def test_snapshot_failure_keeps_committed_formal_draft_and_pending_and_preserves_valid_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    _seed_formal(db, session_id, template_id, "old-formal")
    _save_draft(draft_service, session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    workflow_path = session_dir / "workflow_state.json"
    previous_workflow = {"stage": "template_uploaded", "extra": {"front_page_parity": "even"}}
    workflow_path.write_text(json.dumps(previous_workflow), encoding="utf-8")
    real_replace = commit_module.os.replace

    def fail_workflow_replace(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == workflow_path:
            raise OSError("workflow snapshot unavailable")
        real_replace(source, destination)

    monkeypatch.setattr(commit_module.os, "replace", fail_workflow_replace)
    service = AnswerRegionCommitService(db, session_dir, draft_service)

    result = service.commit(
        session_id=session_id,
        template_id=template_id,
        regions=[_region("replacement")],
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )

    assert result.committed is True
    assert result.snapshot_pending is True
    assert result.validation.can_commit is True
    assert result.error == "workflow_snapshot_failed"
    assert [region["region_uuid"] for region in db.list_answer_regions(session_id)] == ["replacement"]
    assert draft_service.draft_path.exists()
    assert db.get_session_template(session_id)["regions_snapshot_pending"] == 1
    assert json.loads(workflow_path.read_text(encoding="utf-8")) == previous_workflow
    assert list(session_dir.glob(".workflow_state.json.*.tmp")) == []


def test_matching_deterministic_snapshot_is_reused(tmp_path: Path) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    token = _seed_formal(db, session_id, template_id, pending=True)
    session_dir.mkdir(parents=True)
    existing_path = session_dir / f"regions_confirmed_{token}.json"
    existing_path.write_text(
        json.dumps(db.list_answer_regions(session_id), ensure_ascii=False),
        encoding="utf-8",
    )
    service = AnswerRegionCommitService(db, session_dir, draft_service)

    result = service.retry_pending_snapshot(session_id=session_id)

    assert result.snapshot_path == existing_path
    assert json.loads(existing_path.read_text(encoding="utf-8")) == db.list_answer_regions(session_id)


def test_retry_pending_snapshot_writes_formal_snapshots_then_clears_pending_and_draft(
    tmp_path: Path,
) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    _seed_formal(db, session_id, template_id, pending=True)
    _save_draft(draft_service, session_id)
    service = AnswerRegionCommitService(db, session_dir, draft_service)

    result = service.retry_pending_snapshot(session_id=session_id)

    assert result.committed is True
    assert result.snapshot_pending is False
    assert result.validation.can_commit is True
    assert result.snapshot_path is not None
    assert json.loads(result.snapshot_path.read_text(encoding="utf-8")) == db.list_answer_regions(
        session_id
    )
    assert (session_dir / "workflow_state.json").exists()
    assert db.get_session_template(session_id)["regions_snapshot_pending"] == 0
    assert not draft_service.draft_path.exists()


def test_retry_pending_snapshot_failure_keeps_pending_and_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    _seed_formal(db, session_id, template_id, pending=True)
    _save_draft(draft_service, session_id)
    service = AnswerRegionCommitService(db, session_dir, draft_service)

    def fail_snapshot(path: Path, data: object) -> None:
        raise OSError("snapshot disk full")

    monkeypatch.setattr(commit_module, "_atomic_write_json", fail_snapshot)

    result = service.retry_pending_snapshot(session_id=session_id)

    assert result.committed is True
    assert result.snapshot_pending is True
    assert result.snapshot_path is None
    assert result.error == "snapshot_write_failed"
    assert db.get_session_template(session_id)["regions_snapshot_pending"] == 1
    assert draft_service.draft_path.exists()


def test_retry_pending_snapshot_handles_no_pending_work_gracefully(tmp_path: Path) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    _seed_formal(db, session_id, template_id, pending=False)
    _save_draft(draft_service, session_id)
    service = AnswerRegionCommitService(db, session_dir, draft_service)

    result = service.retry_pending_snapshot(session_id=session_id)

    assert result == AnswerRegionCommitResult(
        committed=True,
        snapshot_pending=False,
        validation=RegionValidationResult(()),
    )
    assert draft_service.draft_path.exists()
    assert list(session_dir.glob("regions_confirmed_*.json")) == []


def test_service_instances_for_same_resolved_session_directory_share_lock(tmp_path: Path) -> None:
    db, _session_id, _template_id, session_dir, draft_service = _setup(tmp_path)

    first = AnswerRegionCommitService(db, session_dir, draft_service)
    second = AnswerRegionCommitService(db, session_dir / ".." / "session", draft_service)

    assert first._lock is second._lock


def test_constructor_rejects_draft_from_different_resolved_session_before_lock_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class DraftService:
        draft_path = tmp_path / "other-session" / "region_draft.json"

    lock_looked_up = False

    def unexpected_lock_lookup(_session_dir: Path) -> object:
        nonlocal lock_looked_up
        lock_looked_up = True
        raise AssertionError("lock lookup must happen after path validation")

    monkeypatch.setattr(commit_module, "get_answer_region_session_lock", unexpected_lock_lookup)

    with pytest.raises(ValueError, match="draft service session directory"):
        AnswerRegionCommitService(object(), tmp_path / "session", DraftService())

    assert lock_looked_up is False


def test_cross_process_newer_commit_waits_for_older_completion_lock(tmp_path: Path) -> None:
    db, session_id, template_id, session_dir, _draft_service = _setup(tmp_path)
    context = multiprocessing.get_context("spawn")
    events = context.Queue()
    process = context.Process(
        target=_commit_in_process,
        args=(str(db.db_path), str(session_dir), session_id, template_id, events),
    )

    with get_answer_region_session_lock(session_dir):
        process.start()
        assert events.get(timeout=5) == "started"
        with pytest.raises(Empty):
            events.get(timeout=0.3)

    assert events.get(timeout=10) == ("completed", None)
    process.join(10)
    assert process.exitcode == 0
    assert [row["region_uuid"] for row in db.list_answer_regions(session_id)] == ["child-commit"]


def test_cross_process_draft_save_and_newer_commit_wait_during_real_older_completion(
    tmp_path: Path,
) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    _save_draft(draft_service, session_id)
    context = multiprocessing.get_context("spawn")
    events = context.Queue()
    release = context.Event()
    older = context.Process(
        target=_older_completion_in_process,
        args=(str(db.db_path), str(session_dir), session_id, template_id, events, release),
    )
    newer = context.Process(
        target=_reported_commit_in_process,
        args=(str(db.db_path), str(session_dir), session_id, template_id, events),
    )
    draft_save = context.Process(
        target=_reported_draft_save_in_process,
        args=(str(session_dir), events),
    )

    older.start()
    assert events.get(timeout=10) == ("older", "completion")
    newer.start()
    draft_save.start()
    starts = {events.get(timeout=10), events.get(timeout=10)}
    assert starts == {("newer_commit", "started"), ("draft_save", "started")}
    with pytest.raises(Empty):
        events.get(timeout=0.5)

    release.set()
    older.join(15)
    newer.join(15)
    draft_save.join(15)
    remaining = []
    while True:
        try:
            remaining.append(events.get_nowait())
        except Empty:
            break

    assert older.exitcode == 0
    assert newer.exitcode == 0
    assert draft_save.exitcode == 0
    assert ("newer_commit", "entered") in remaining
    assert ("draft_save", "entered") in remaining
    assert ("older", "completed", None) in remaining
    assert ("newer_commit", "completed", None) in remaining
    assert ("draft_save", "completed") in remaining


def test_shared_session_lock_serializes_concurrent_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    first = AnswerRegionCommitService(db, session_dir, draft_service)
    second = AnswerRegionCommitService(db, session_dir, draft_service)
    first_in_replace = threading.Event()
    release_first = threading.Event()
    second_entered_replace = threading.Event()
    real_replace = db.replace_answer_regions_atomic
    call_count = 0

    def controlled_replace(*args: object, **kwargs: object) -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_in_replace.set()
            assert release_first.wait(5)
        else:
            second_entered_replace.set()
        return real_replace(*args, **kwargs)

    monkeypatch.setattr(db, "replace_answer_regions_atomic", controlled_replace)
    results: list[AnswerRegionCommitResult] = []
    first_thread = threading.Thread(
        target=lambda: results.append(
            first.commit(
                session_id=session_id,
                template_id=template_id,
                regions=[_region("first")],
                image_sizes=IMAGE_SIZES,
                template_matches=True,
            )
        )
    )
    second_thread = threading.Thread(
        target=lambda: results.append(
            second.commit(
                session_id=session_id,
                template_id=template_id,
                regions=[_region("second")],
                image_sizes=IMAGE_SIZES,
                template_matches=True,
            )
        )
    )

    first_thread.start()
    assert first_in_replace.wait(5)
    second_thread.start()
    assert second_entered_replace.wait(0.2) is False
    release_first.set()
    first_thread.join(5)
    second_thread.join(5)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert second_entered_replace.is_set()
    assert all(result.error is None for result in results)
    assert [row["region_uuid"] for row in db.list_answer_regions(session_id)] == ["second"]


def test_draft_save_waits_for_commit_and_survives_after_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    _save_draft(draft_service, session_id)
    service = AnswerRegionCommitService(db, session_dir, draft_service)
    snapshot_written = threading.Event()
    release_snapshot = threading.Event()
    real_atomic_write = commit_module._atomic_write_json

    def pause_after_snapshot(path: Path, data: object) -> None:
        real_atomic_write(path, data)
        if path.name.startswith("regions_confirmed_"):
            snapshot_written.set()
            assert release_snapshot.wait(5)

    monkeypatch.setattr(commit_module, "_atomic_write_json", pause_after_snapshot)
    results: list[AnswerRegionCommitResult] = []
    worker = threading.Thread(
        target=lambda: results.append(
            service.commit(
                session_id=session_id,
                template_id=template_id,
                regions=[_region("formal")],
                image_sizes=IMAGE_SIZES,
                template_matches=True,
            )
        )
    )

    worker.start()
    assert snapshot_written.wait(5)
    save_finished = threading.Event()

    def save_newer_draft() -> None:
        draft_service.save(
            session_id=session_id,
            template_fingerprint="template-fingerprint",
            revision=2,
            regions=[_region("newer-draft")],
        )
        save_finished.set()

    save_thread = threading.Thread(target=save_newer_draft)
    save_thread.start()
    assert save_finished.wait(0.2) is False
    release_snapshot.set()
    worker.join(5)
    save_thread.join(5)

    assert results[0].error is None
    assert results[0].snapshot_pending is False
    assert save_finished.is_set()
    assert draft_service.load(expected_template_fingerprint="template-fingerprint").draft["revision"] == 2
    assert db.get_session_template(session_id)["regions_snapshot_pending"] == 0


def test_draft_save_cannot_slip_between_final_check_and_discard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    _save_draft(draft_service, session_id)
    service = AnswerRegionCommitService(db, session_dir, draft_service)
    final_check_started = threading.Event()
    release_final_check = threading.Event()
    save_finished = threading.Event()
    real_marker = commit_module._draft_marker
    marker_calls = 0

    def pause_final_marker(path: Path) -> str | None:
        nonlocal marker_calls
        marker_calls += 1
        marker = real_marker(path)
        if marker_calls == 2:
            final_check_started.set()
            assert release_final_check.wait(5)
        return marker

    monkeypatch.setattr(commit_module, "_draft_marker", pause_final_marker)
    commit_thread = threading.Thread(
        target=lambda: service.commit(
            session_id=session_id,
            template_id=template_id,
            regions=[_region("formal")],
            image_sizes=IMAGE_SIZES,
            template_matches=True,
        )
    )

    def save_newer_draft() -> None:
        draft_service.save(
            session_id=session_id,
            template_fingerprint="template-fingerprint",
            revision=2,
            regions=[_region("newer-draft")],
        )
        save_finished.set()

    save_thread = threading.Thread(target=save_newer_draft)
    commit_thread.start()
    assert final_check_started.wait(5)
    save_thread.start()
    assert save_finished.wait(0.2) is False
    release_final_check.set()
    commit_thread.join(5)
    save_thread.join(5)

    assert save_finished.is_set()
    loaded = draft_service.load(expected_template_fingerprint="template-fingerprint")
    assert loaded.draft is not None
    assert loaded.draft["revision"] == 2


def test_discard_failure_keeps_pending_and_retry_reuses_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    _save_draft(draft_service, session_id)
    service = AnswerRegionCommitService(db, session_dir, draft_service)
    real_discard = draft_service.discard

    def fail_discard() -> None:
        raise OSError(f"cannot delete {draft_service.draft_path}")

    monkeypatch.setattr(draft_service, "discard", fail_discard)
    failed = service.commit(
        session_id=session_id,
        template_id=template_id,
        regions=[_region("formal")],
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )
    monkeypatch.setattr(draft_service, "discard", real_discard)
    recovered = service.retry_pending_snapshot(session_id=session_id)

    assert failed.error == "draft_cleanup_failed"
    assert failed.snapshot_pending is True
    assert recovered.error is None
    assert recovered.snapshot_path == failed.snapshot_path
    assert list(session_dir.glob("regions_confirmed_*.json")) == [failed.snapshot_path]
    assert db.get_session_template(session_id)["regions_snapshot_pending"] == 0
    assert not draft_service.draft_path.exists()


def test_repeated_completion_failures_reuse_one_snapshot_and_remain_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    _seed_formal(db, session_id, template_id, pending=True)
    service = AnswerRegionCommitService(db, session_dir, draft_service)
    monkeypatch.setattr(db, "mark_region_snapshot_complete", lambda *args, **kwargs: False)

    first = service.retry_pending_snapshot(session_id=session_id)
    second = service.retry_pending_snapshot(session_id=session_id)

    assert first.error == "snapshot_completion_failed"
    assert second.error == "snapshot_completion_failed"
    assert first.snapshot_path == second.snapshot_path
    assert len(list(session_dir.glob("regions_confirmed_*.json"))) == 1
    assert db.get_session_template(session_id)["regions_snapshot_pending"] == 1


def test_stale_generation_cannot_discard_draft_or_clear_newer_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    _save_draft(draft_service, session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    previous_workflow = {
        "stage": "template_uploaded",
        "extra": {"front_page_parity": "odd", "keep_me": True},
    }
    (session_dir / "workflow_state.json").write_text(
        json.dumps(previous_workflow),
        encoding="utf-8",
    )
    service = AnswerRegionCommitService(db, session_dir, draft_service)
    real_atomic_write = commit_module._atomic_write_json
    newer_token: str | None = None
    replaced = False

    def replace_with_newer_generation(path: Path, data: object) -> None:
        nonlocal newer_token, replaced
        real_atomic_write(path, data)
        if path.name == "workflow_state.json" and not replaced:
            replaced = True
            newer_token = db.replace_answer_regions_atomic(
                session_id,
                template_id,
                [_region("newer-formal")],
                confirmed=True,
            )

    monkeypatch.setattr(commit_module, "_atomic_write_json", replace_with_newer_generation)

    result = service.commit(
        session_id=session_id,
        template_id=template_id,
        regions=[_region("older-formal")],
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )

    template = db.get_session_template(session_id)
    assert result.error == "stale_snapshot_generation"
    assert result.snapshot_pending is True
    assert draft_service.draft_path.exists()
    assert template["regions_snapshot_pending"] == 1
    assert template["regions_snapshot_token"] == newer_token
    assert [row["region_uuid"] for row in db.list_answer_regions(session_id)] == ["newer-formal"]
    assert json.loads((session_dir / "workflow_state.json").read_text(encoding="utf-8")) == (
        previous_workflow
    )


def test_stale_workflow_rollback_never_overwrites_newer_generation_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    _save_draft(draft_service, session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    previous_workflow = {"stage": "template_uploaded", "extra": {"keep_me": True}}
    workflow_path = session_dir / "workflow_state.json"
    workflow_path.write_text(json.dumps(previous_workflow), encoding="utf-8")
    service = AnswerRegionCommitService(db, session_dir, draft_service)
    real_atomic_write = commit_module._atomic_write_json
    newer_workflow: dict[str, object] | None = None

    def publish_newer_after_older(path: Path, data: object) -> None:
        nonlocal newer_workflow
        real_atomic_write(path, data)
        if path == workflow_path:
            newer_token = db.replace_answer_regions_atomic(
                session_id,
                template_id,
                [_region("newer-formal")],
                confirmed=True,
            )
            newer_workflow = {
                "session_id": session_id,
                "stage": "regions_confirmed",
                "snapshot_token": newer_token,
                "extra": {"regions_path": f"regions_confirmed_{newer_token}.json"},
            }
            real_atomic_write(path, newer_workflow)
            assert db.mark_region_snapshot_complete(
                session_id,
                expected_token=newer_token,
            )

    monkeypatch.setattr(commit_module, "_atomic_write_json", publish_newer_after_older)

    result = service.commit(
        session_id=session_id,
        template_id=template_id,
        regions=[_region("older-formal")],
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )

    assert result.error == "stale_snapshot_generation"
    assert newer_workflow is not None
    assert json.loads(workflow_path.read_text(encoding="utf-8")) == newer_workflow
    assert [row["region_uuid"] for row in db.list_answer_regions(session_id)] == ["newer-formal"]
    assert db.get_session_template(session_id)["regions_snapshot_pending"] == 0


def test_stale_workflow_restore_requires_current_thread_to_hold_session_lock(
    tmp_path: Path,
) -> None:
    db, _session_id, _template_id, session_dir, draft_service = _setup(tmp_path)
    service = AnswerRegionCommitService(db, session_dir, draft_service)

    with pytest.raises(AssertionError, match="session lock"):
        service._stale_after_workflow_result(
            RegionValidationResult(()),
            session_dir / "regions_confirmed_token.json",
            session_dir / "workflow_state.json",
            "token",
            {},
            False,
        )


def test_conflicting_deterministic_snapshot_fails_without_overwrite(tmp_path: Path) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    token = _seed_formal(db, session_id, template_id, pending=True)
    session_dir.mkdir(parents=True)
    snapshot_path = session_dir / f"regions_confirmed_{token}.json"
    snapshot_path.write_text('{"belongs_to": "another payload"}', encoding="utf-8")
    service = AnswerRegionCommitService(db, session_dir, draft_service)

    result = service.retry_pending_snapshot(session_id=session_id)

    assert result.error == "snapshot_collision"
    assert result.snapshot_pending is True
    assert json.loads(snapshot_path.read_text(encoding="utf-8")) == {
        "belongs_to": "another payload"
    }


def test_snapshot_publication_race_never_overwrites_raced_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    token = _seed_formal(db, session_id, template_id, pending=True)
    snapshot_path = session_dir / f"regions_confirmed_{token}.json"
    real_link = commit_module.os.link

    def race_link(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == snapshot_path:
            snapshot_path.write_text('{"raced": true}', encoding="utf-8")
            raise FileExistsError
        real_link(source, destination)

    monkeypatch.setattr(commit_module.os, "link", race_link)
    service = AnswerRegionCommitService(db, session_dir, draft_service)

    result = service.retry_pending_snapshot(session_id=session_id)

    assert result.error == "snapshot_collision"
    assert result.snapshot_pending is True
    assert json.loads(snapshot_path.read_text(encoding="utf-8")) == {"raced": True}


def test_errors_are_stable_and_do_not_leak_absolute_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    service = AnswerRegionCommitService(db, session_dir, draft_service)

    def fail_replace(*args: object, **kwargs: object) -> str:
        raise OSError(f"database path leaked: {tmp_path / 'secret.db'}")

    monkeypatch.setattr(db, "replace_answer_regions_atomic", fail_replace)

    result = service.commit(
        session_id=session_id,
        template_id=template_id,
        regions=[_region("formal")],
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )

    assert result.error == "database_commit_failed"
    assert str(tmp_path) not in result.error
