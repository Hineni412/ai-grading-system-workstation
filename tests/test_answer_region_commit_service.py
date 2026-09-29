from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
from queue import Empty

import pytest

import answer_region_commit_service as commit_module
from answer_region_commit_service import AnswerRegionCommitService
from answer_region_draft_service import AnswerRegionDraftService
from answer_region_session_lock import get_answer_region_session_lock
from backend.repositories.access import (
    GradingRepositoryAccess,
    as_grading_repositories,
)
from db_manager import DBManager


IMAGE_SIZES = {"front": (200, 300), "back": (200, 300)}
from backend.repositories.grading_database import open_grading_repositories


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
        real_replace = self.template_repository.replace_answer_regions_atomic

        def reported_replace(
            *args: object,
            **kwargs: object,
        ) -> str:
            self._events.put(("newer_commit", "entered"))
            return real_replace(*args, **kwargs)

        self.template_repository.replace_answer_regions_atomic = reported_replace


def _older_completion_in_process(
    db_path: str,
    session_dir: str,
    session_id: int,
    template_id: int,
    events: object,
    release: object,
) -> None:
    service = AnswerRegionCommitService(
        open_grading_repositories(Path(db_path)),
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
        as_grading_repositories(_ReportingDBManager(Path(db_path), events)),
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


def _setup(
    tmp_path: Path,
) -> tuple[GradingRepositoryAccess, int, int, Path, AnswerRegionDraftService]:
    db = open_grading_repositories(tmp_path / "grading.db")
    db.initialize()
    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "max_score": 10,
                        "parts": [{"part_id": "Q1(P1)", "part_score": 10}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    session_id = db.sessions.create_grading_session(
        "regions",
        str(rubric_path),
        "answer.json",
    )
    template_id = db.templates.upsert_session_template(session_id, "front.png", "back.png")
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
    db: GradingRepositoryAccess,
    session_id: int,
    template_id: int,
    region_uuid: str = "formal-region",
    *,
    pending: bool = False,
) -> str:
    token = db.templates.replace_answer_regions_atomic(
        session_id,
        template_id,
        [_region(region_uuid)],
        confirmed=True,
    )
    if not pending:
        db.templates.mark_region_snapshot_complete(session_id, expected_token=token)
    return token


def test_database_failure_leaves_formal_regions_and_draft_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, session_id, template_id, session_dir, draft_service = _setup(tmp_path)
    _seed_formal(db, session_id, template_id)
    _save_draft(draft_service, session_id)
    formal_before = db.templates.list_answer_regions(session_id)
    draft_before = draft_service.draft_path.read_bytes()
    service = AnswerRegionCommitService(db, session_dir, draft_service)

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        db.templates,
        "replace_answer_regions_atomic",
        fail_replace,
    )

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
    assert db.templates.list_answer_regions(session_id) == formal_before
    assert draft_service.draft_path.read_bytes() == draft_before
    assert list(session_dir.glob("regions_confirmed_*.json")) == []


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
        args=(
            str(db.db_path),
            str(session_dir),
            session_id,
            template_id,
            events,
            release,
        ),
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
