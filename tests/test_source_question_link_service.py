from __future__ import annotations

from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.importers.batch_importer import BatchImportResult
from question_bank.services import grading_paper_intake_service
from question_bank.services.source_question_link_service import SourceQuestionLinkService


@pytest.fixture
def link_service(tmp_path: Path) -> SourceQuestionLinkService:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO questions (id, question_number, question_text)
            VALUES (?, ?, ?)
            """,
            [
                (201, "17", "已知二次函数 y=x²-2x-3，求其顶点坐标。"),
                (202, "18", "如图，在三角形 ABC 中，证明角平分线的性质。"),
            ],
        )
    return SourceQuestionLinkService(db_path)


def test_confirmed_link_can_exclude_current_exam_original(
    link_service: SourceQuestionLinkService,
) -> None:
    link_service.confirm_link(
        grading_session_id=14,
        source_question_id="17",
        bank_question_id=201,
        link_method="fingerprint",
    )

    assert link_service.confirmed_bank_question_ids(14) == {201}


def test_suggested_link_is_not_used_for_exclusion(
    link_service: SourceQuestionLinkService,
) -> None:
    link_service.suggest_link(
        grading_session_id=14,
        source_question_id="17",
        bank_question_id=201,
        confidence=0.88,
        link_method="text_similarity",
    )

    assert link_service.confirmed_bank_question_ids(14) == set()
    assert link_service.exclusion_warning(14)


def test_exact_text_match_confirms_but_similarity_only_suggests(
    link_service: SourceQuestionLinkService,
) -> None:
    result = link_service.link_questions_for_session(
        grading_session_id=14,
        source_questions=[
            {
                "question_id": "Q17",
                "question_text": " 已知二次函数 y=x²-2x-3，求其顶点坐标。 ",
            },
            {
                "question_id": "Q18",
                "question_text": "如图，在三角形 ABC 中，请证明角平分线的性质。",
            },
        ],
    )

    assert result == {"confirmed": 1, "suggested": 1, "unresolved": 0}
    links = {item["source_question_id"]: item for item in link_service.list_links(14)}
    assert links["Q17"]["status"] == "confirmed"
    assert links["Q17"]["link_method"] == "exact_text"
    assert links["Q18"]["status"] == "suggested"
    assert links["Q18"]["link_method"] == "text_similarity"


def test_suggestion_never_overwrites_teacher_confirmed_link(
    link_service: SourceQuestionLinkService,
) -> None:
    link_service.confirm_link(
        grading_session_id=14,
        source_question_id="17",
        bank_question_id=201,
        link_method="manual",
        reviewed_by="teacher",
    )

    link_service.suggest_link(
        grading_session_id=14,
        source_question_id="17",
        bank_question_id=202,
        confidence=0.95,
        link_method="text_similarity",
    )

    link = link_service.list_links(14)[0]
    assert link["bank_question_id"] == 201
    assert link["status"] == "confirmed"


def test_grading_paper_intake_creates_source_links(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "question_bank.db"
    source_file = tmp_path / "grading-paper.docx"
    source_file.write_bytes(b"placeholder")

    def fake_import(_papers, database_path, **_kwargs):
        initialize_database(database_path)
        with connect(database_path) as conn:
            conn.execute(
                """
                INSERT INTO questions (question_number, question_text, source_file)
                VALUES ('17', '已知二次函数 y=x²-2x-3，求其顶点坐标。', ?)
                """,
                (str(source_file),),
            )
        return BatchImportResult([], 1, 1, 0, 0, 0, 0)

    monkeypatch.setattr(grading_paper_intake_service, "import_scanned_papers", fake_import)

    result = grading_paper_intake_service.intake_grading_paper_to_question_bank(
        source_file=source_file,
        db_path=db_path,
        run_ai_tagging=False,
        grading_session_id=14,
        grading_source_questions=[
            {
                "question_id": "Q17",
                "question_text": "已知二次函数 y=x²-2x-3，求其顶点坐标。",
            }
        ],
    )

    assert result.confirmed_links == 1
    assert SourceQuestionLinkService(db_path).confirmed_bank_question_ids(14)
