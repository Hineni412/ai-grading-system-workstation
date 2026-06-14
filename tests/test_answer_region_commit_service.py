from __future__ import annotations

import inspect
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import answer_region_commit_service as commit_module
from answer_region_commit_service import AnswerRegionCommitResult, AnswerRegionCommitService
from answer_region_draft_service import AnswerRegionDraftService
from answer_region_models import RegionValidationResult
from db_manager import DBManager


IMAGE_SIZES = {"front": (200, 300), "back": (200, 300)}


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
) -> None:
    db.replace_answer_regions_atomic(
        session_id,
        template_id,
        [_region(region_uuid)],
        confirmed=True,
    )
    if not pending:
        db.mark_region_snapshot_complete(session_id)


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
    assert result.error == "database unavailable"
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
    assert result.error == "workflow snapshot unavailable"
    assert [region["region_uuid"] for region in db.list_answer_regions(session_id)] == ["replacement"]
    assert draft_service.draft_path.exists()
    assert db.get_session_template(session_id)["regions_snapshot_pending"] == 1
    assert json.loads(workflow_path.read_text(encoding="utf-8")) == previous_workflow
    assert list(session_dir.glob(".workflow_state.json.*.tmp")) == []


def test_snapshot_name_collision_preserves_existing_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    session_dir.mkdir(parents=True)
    existing_path = session_dir / "regions_confirmed_fixed.json"
    existing_path.write_text('{"existing": true}', encoding="utf-8")
    monkeypatch.setattr(commit_module, "_snapshot_timestamp", lambda: "fixed")
    service = AnswerRegionCommitService(db, session_dir, draft_service)

    result = service.commit(
        session_id=session_id,
        template_id=template_id,
        regions=[_region("replacement")],
        image_sizes=IMAGE_SIZES,
        template_matches=True,
    )

    assert result.snapshot_path is not None
    assert result.snapshot_path != existing_path
    assert json.loads(existing_path.read_text(encoding="utf-8")) == {"existing": True}
    assert json.loads(result.snapshot_path.read_text(encoding="utf-8")) == db.list_answer_regions(
        session_id
    )


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
    assert result.error == "snapshot disk full"
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
