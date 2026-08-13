from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from db_manager import DBManager
from question_bank.database.schema import connect, initialize_database
from question_bank.importers.batch_importer import BatchImportResult, PaperImportFileResult
from question_bank.services.grading_paper_intake_service import GradingPaperIntakeResult
from question_bank.services.source_question_link_service import SourceQuestionLinkService


def _rubric() -> dict:
    return {
        "grade": "八年级",
        "questions": [
            {
                "question_id": "Q1",
                "question_text": "利用角平分线性质求角度",
                "knowledge_name": "角平分线性质",
            },
            {
                "question_id": "Q2",
                "question_text": "计算三角形面积",
                "knowledge_name": "三角形面积计算",
            },
        ],
    }


@dataclass
class FakeTagger:
    fail_question_numbers: set[str] = field(default_factory=set)
    call_count: int = 0


def _write_rubric(path: Path) -> None:
    import json

    path.write_text(json.dumps(_rubric(), ensure_ascii=False), encoding="utf-8")


def _build_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, with_source: bool = True):
    from integration import grading_paper_skill_workflow_service as workflow_module
    from integration.grading_paper_skill_workflow_service import GradingPaperSkillWorkflowService

    data_root = tmp_path / "user_data"
    (data_root / "databases").mkdir(parents=True)
    grading_db_path = data_root / "databases" / "grading_system.db"
    question_bank_db_path = data_root / "databases" / "question_bank.db"
    grading_db = DBManager(grading_db_path)
    grading_db.initialize()
    initialize_database(question_bank_db_path)
    rubric_path = data_root / "rubric.json"
    answer_path = data_root / "answer.json"
    source_path = data_root / "source.docx"
    _write_rubric(rubric_path)
    answer_path.write_text("{}", encoding="utf-8")
    source_path.write_bytes(b"source-paper")
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    session_id = grading_db.create_grading_session(
        "期中测试",
        str(rubric_path),
        str(answer_path),
        source_paper_path=str(source_path) if with_source else "",
        source_paper_sha256=digest if with_source else "",
    )
    tagger = FakeTagger()

    def fake_intake_grading_paper_to_question_bank(**kwargs):
        tagger_arg = kwargs["ai_service"]
        tagger_arg.call_count += 1
        db_path = Path(kwargs["db_path"])
        stored_source = str(kwargs["source_file"])
        initialize_database(db_path)
        question_ids: dict[str, int] = {}
        with connect(db_path) as conn:
            for number, text in (("1", "利用角平分线性质求角度"), ("2", "计算三角形面积")):
                row = conn.execute(
                    "SELECT id FROM questions WHERE source_file = ? AND question_number = ?",
                    (stored_source, number),
                ).fetchone()
                if row is None:
                    question_id = int(
                        conn.execute(
                            """
                            INSERT INTO questions (question_number, question_text, source_file)
                            VALUES (?, ?, ?)
                            """,
                            (number, text, stored_source),
                        ).lastrowid
                    )
                else:
                    question_id = int(row["id"])
                question_ids[number] = question_id
            for number in ("1", "2"):
                if number in tagger_arg.fail_question_numbers:
                    continue
                question_id = question_ids[number]
                conn.execute("DELETE FROM question_tags WHERE question_id = ?", (question_id,))
                conn.executemany(
                    "INSERT INTO question_tags (question_id, tag_type, tag_value) VALUES (?, ?, ?)",
                    [
                        (question_id, "knowledge_point", "角平分线性质" if number == "1" else "三角形面积"),
                        (question_id, "ability", "推理能力"),
                        (question_id, "exam_scope", "八年级"),
                        (question_id, "student_level", "基础巩固"),
                    ],
                )
        SourceQuestionLinkService(db_path).confirm_imported_questions_for_session(
            grading_session_id=kwargs["grading_session_id"],
            source_questions=kwargs["grading_source_questions"],
            imported_bank_questions=[
                {
                    "id": question_ids[number],
                    "question_number": number,
                    "question_text": text,
                    "source_file": stored_source,
                }
                for number, text in (("1", "利用角平分线性质求角度"), ("2", "计算三角形面积"))
            ],
        )
        callback = kwargs.get("workflow_progress_callback")
        if callback is not None:
            for completed, stage in enumerate(("import", "tag", "save", "link"), start=1):
                callback(
                    SimpleNamespace(
                        stage=stage,
                        completed=completed,
                        total=5,
                        request_count=1,
                        failed_questions=len(tagger_arg.fail_question_numbers),
                        question_id="",
                    )
                )
        return GradingPaperIntakeResult(
            saved_file=Path(stored_source),
            import_result=BatchImportResult(
                files=[PaperImportFileResult(source_file=stored_source, status="imported", question_count=2)],
                imported_papers=1,
                question_count=2,
                answer_match_count=0,
                review_count=0,
                skipped_duplicate_files=0,
                failed_files=0,
            ),
            tagged_questions=2 - len(tagger_arg.fail_question_numbers),
            failed_tagging=len(tagger_arg.fail_question_numbers),
            confirmed_links=2,
        )

    monkeypatch.setattr(
        workflow_module,
        "intake_grading_paper_to_question_bank",
        fake_intake_grading_paper_to_question_bank,
    )
    service = GradingPaperSkillWorkflowService(
        grading_db_path=grading_db_path,
        question_bank_db_path=question_bank_db_path,
        data_root=data_root,
    )
    return service, session_id, tagger, question_bank_db_path


def test_status_is_not_started_when_source_exists_without_intake_links(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, session_id, _tagger, _db_path = _build_fixture(tmp_path, monkeypatch)

    status = service.status(session_id)

    assert status.state == "not_started"
    assert status.source_available is True
    assert status.source_question_total == 2
    assert status.imported_question_total == 0
    assert status.complete_tag_question_total == 0
    assert status.linked_source_total == 0


def test_run_becomes_ready_and_second_run_reuses_existing_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, session_id, tagger, db_path = _build_fixture(tmp_path, monkeypatch)

    first = service.run(session_id, ai_service=tagger, max_workers=1, requests_per_minute=60)
    second = service.run(session_id, ai_service=tagger, max_workers=1, requests_per_minute=60)

    assert first.state == "ready"
    assert second.state == "ready"
    assert second.linked_source_total == first.linked_source_total == 2
    assert second.imported_question_total == 2
    assert second.complete_tag_question_total == 2
    with connect(db_path) as conn:
        counts = {
            "questions": int(conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]),
            "source_links": int(conn.execute("SELECT COUNT(*) FROM grading_question_links").fetchone()[0]),
        }
    assert counts == {"questions": 2, "source_links": 2}
    assert tagger.call_count == 2


def test_partial_results_survive_tagging_failure_and_are_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, session_id, tagger, _db_path = _build_fixture(tmp_path, monkeypatch)
    tagger.fail_question_numbers = {"2"}

    partial = service.run(session_id, ai_service=tagger, max_workers=1, requests_per_minute=60)

    assert partial.state == "partial"
    assert partial.complete_tag_question_total == 1
    assert partial.failed_questions == 1
    tagger.fail_question_numbers.clear()
    assert service.run(
        session_id,
        ai_service=tagger,
        max_workers=1,
        requests_per_minute=60,
    ).state == "ready"


def test_progress_reaches_complete_only_after_all_five_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, session_id, tagger, _db_path = _build_fixture(tmp_path, monkeypatch)
    events = []

    status = service.run(
        session_id,
        ai_service=tagger,
        max_workers=3,
        requests_per_minute=77,
        progress_callback=events.append,
    )

    assert [event.stage for event in events] == ["import", "tag", "save", "link", "complete"]
    assert all(event.completed < event.total for event in events[:-1])
    assert events[-1].completed == events[-1].total
    assert status.request_count == 1
    assert status.current_stage == "complete"


def test_interrupted_running_state_is_recovered_as_retryable_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, session_id, _tagger, _db_path = _build_fixture(tmp_path, monkeypatch)
    service.grading_db.update_question_bank_sync_state(session_id, state="running")

    status = service.status(session_id)

    assert status.state == "failed"
    assert "中断" in status.error


def test_legacy_session_can_save_source_without_running_ai(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, session_id, tagger, db_path = _build_fixture(tmp_path, monkeypatch, with_source=False)

    status = service.save_source(
        session_id,
        filename="补传试卷.docx",
        content=b"source-bytes",
    )

    assert status.state == "not_started"
    assert status.source_available is True
    assert tagger.call_count == 0
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 0
