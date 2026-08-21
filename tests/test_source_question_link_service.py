from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.importers.batch_importer import BatchImportResult, PaperImportFileResult
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
                (301, "17", "新版试卷中的第 17 题。"),
            ],
        )
    return SourceQuestionLinkService(db_path)


def test_confirmed_link_can_exclude_current_exam_original(
    link_service: SourceQuestionLinkService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from question_bank.services import source_question_link_service as source_link_module

    link_service.confirm_link(
        grading_session_id=14,
        source_question_id="17",
        bank_question_id=201,
        link_method="fingerprint",
    )
    initialize_calls: list[Path] = []
    original_initialize = source_link_module.initialize_database

    def tracking_initialize(path: Path) -> None:
        initialize_calls.append(path)
        original_initialize(path)

    monkeypatch.setattr(source_link_module, "initialize_database", tracking_initialize)

    assert link_service.confirmed_bank_question_ids(14) == {201}
    assert initialize_calls == [link_service.db_path]


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


def test_prepublish_match_requires_explicit_id_or_unique_question_number(
    link_service: SourceQuestionLinkService,
) -> None:
    result = link_service.match_imported_questions(
        source_questions=[
            {"question_id": "Q17"},
            {"question_id": "Q18"},
            {"question_id": "Q19"},
            {"question_id": "Q20", "bank_question_id": 301},
        ],
        imported_bank_questions=[
            {"id": 201, "question_number": "17"},
            {"id": 202, "question_number": "18"},
            {"id": 301, "question_number": "17"},
        ],
    )

    assert result["matches"] == {"Q18": 202, "Q20": 301}
    assert result["confirmed"] == 2
    assert result["unresolved"] == 2
    assert result["unresolved_question_ids"] == ["Q17", "Q19"]


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


def test_unique_question_number_in_imported_paper_confirms_before_global_text(
    link_service: SourceQuestionLinkService,
) -> None:
    result = link_service.link_questions_for_session(
        grading_session_id=16,
        source_questions=[{"question_id": "Q17", "stem_summary": "摘要不等于完整题干"}],
        candidate_bank_questions=[
            {
                "id": 201,
                "question_number": "17",
                "question_text": "完整题干",
                "source_file": "paper-a.docx",
            }
        ],
    )

    assert result == {"confirmed": 1, "suggested": 0, "unresolved": 0}
    link = link_service.list_links(16)[0]
    assert link["bank_question_id"] == 201
    assert link["link_method"] == "paper_question_number"


def test_duplicate_number_inside_candidate_paper_does_not_auto_confirm(
    link_service: SourceQuestionLinkService,
) -> None:
    result = link_service.link_questions_for_session(
        grading_session_id=17,
        source_questions=[{"question_id": "Q17", "stem_summary": "没有可比较的完整题干"}],
        candidate_bank_questions=[
            {"id": 201, "question_number": "17", "question_text": "A"},
            {"id": 202, "question_number": "Q17", "question_text": "B"},
        ],
    )

    assert result == {"confirmed": 0, "suggested": 0, "unresolved": 1}


def test_imported_question_linking_uses_only_explicit_id_or_unique_number(
    link_service: SourceQuestionLinkService,
) -> None:
    result = link_service.confirm_imported_questions_for_session(
        grading_session_id=18,
        source_questions=[
            {"question_id": "Q17"},
            {"question_id": "Q18", "bank_question_id": 202},
        ],
        imported_bank_questions=[
            {"id": 201, "question_number": "17", "question_text": "A"},
            {"id": 202, "question_number": "Q17", "question_text": "B"},
        ],
    )

    assert result == {
        "confirmed": 1,
        "unresolved": 1,
        "unresolved_question_ids": ["Q17"],
    }
    links = link_service.list_links(18)
    assert [(item["source_question_id"], item["bank_question_id"]) for item in links] == [
        ("Q18", 202)
    ]
    assert links[0]["link_method"] == "source_metadata"


def test_new_sync_replaces_stale_automatic_link_and_does_not_restore_it_on_rollback(
    link_service: SourceQuestionLinkService,
) -> None:
    first = link_service.confirm_imported_questions_for_session(
        grading_session_id=18,
        source_questions=[{"question_id": "Q17", "bank_question_id": 201}],
        imported_bank_questions=[{"id": 201, "question_number": "17"}],
        sync_job_id=41,
        sync_config_revision="a" * 64,
    )
    second = link_service.confirm_imported_questions_for_session(
        grading_session_id=18,
        source_questions=[{"question_id": "Q17", "bank_question_id": 301}],
        imported_bank_questions=[{"id": 301, "question_number": "17"}],
        sync_job_id=42,
        sync_config_revision="b" * 64,
    )

    link_service.rollback_imported_question_links(
        grading_session_id=18,
        sync_job_id=41,
        changes=first["_rollback_changes"],
    )

    links = link_service.list_links(18)
    assert len(links) == 1
    assert links[0]["bank_question_id"] == 301
    assert links[0]["evidence"]["sync_job_id"] == 42
    assert second["confirmed"] == 1

    stale_first = link_service.confirm_imported_questions_for_session(
        grading_session_id=18,
        source_questions=[{"question_id": "Q17", "bank_question_id": 201}],
        imported_bank_questions=[{"id": 201, "question_number": "17"}],
        sync_job_id=41,
        sync_config_revision="a" * 64,
    )

    links = link_service.list_links(18)
    assert len(links) == 1
    assert links[0]["bank_question_id"] == 301
    assert links[0]["evidence"]["sync_job_id"] == 42
    assert stale_first["_rollback_changes"] == []

    link_service.rollback_imported_question_links(
        grading_session_id=18,
        sync_job_id=42,
        changes=second["_rollback_changes"],
    )

    assert link_service.list_links(18) == []


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


def test_borrowed_link_reads_reuse_connection_and_leave_it_open_on_error(
    link_service: SourceQuestionLinkService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from question_bank.services import source_question_link_service as source_link_module

    link_service.confirm_link(
        grading_session_id=14,
        source_question_id="17",
        bank_question_id=201,
        link_method="manual",
    )
    borrowed = sqlite3.connect(link_service.db_path)
    borrowed.row_factory = sqlite3.Row
    service = SourceQuestionLinkService(
        link_service.db_path,
        external_connection=borrowed,
    )
    try:
        monkeypatch.setattr(
            source_link_module,
            "initialize_database",
            lambda _path: pytest.fail("borrowed source-link reads must not initialize the database"),
        )
        assert service.confirmed_bank_question_ids(14) == {201}
        assert service.list_links(14)[0]["bank_question_id"] == 201

        borrowed.execute("DROP TABLE grading_question_links")
        with pytest.raises(sqlite3.OperationalError):
            service.list_links(14)
        assert borrowed.execute("SELECT 1").fetchone()[0] == 1
    finally:
        borrowed.rollback()
        borrowed.close()


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
                VALUES ('99', '已知二次函数 y=x²-2x-3，求其顶点坐标。', 'unrelated.docx')
                """
            )
            conn.execute(
                """
                INSERT INTO questions (question_number, question_text, source_file)
                VALUES ('17', '已知二次函数 y=x²-2x-3，求其顶点坐标。', ?)
                """,
                (str(source_file),),
            )
        return BatchImportResult(
            [PaperImportFileResult(source_file=str(source_file), status="imported", question_count=1)],
            1,
            1,
            0,
            0,
            0,
            0,
        )

    monkeypatch.setattr(grading_paper_intake_service, "import_scanned_papers", fake_import)

    result = grading_paper_intake_service.intake_grading_paper_to_question_bank(
        source_file=source_file,
        db_path=db_path,
        run_ai_tagging=False,
        data_root=tmp_path,
        grading_session_id=14,
        grading_source_questions=[
            {
                "question_id": "Q17",
                "question_text": "已知二次函数 y=x²-2x-3，求其顶点坐标。",
            }
        ],
    )

    assert result.confirmed_links == 1
    linked_ids = SourceQuestionLinkService(db_path).confirmed_bank_question_ids(14)
    with connect(db_path) as conn:
        imported_id = conn.execute(
            "SELECT id FROM questions WHERE source_file = ?",
            (str(source_file),),
        ).fetchone()[0]
    assert linked_ids == {imported_id}


def test_duplicate_intake_retries_only_questions_without_complete_tags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "question_bank.db"
    source_file = tmp_path / "grading-paper.docx"
    source_file.write_bytes(b"placeholder")
    initialize_database(db_path)
    with connect(db_path) as conn:
        first_id = int(
            conn.execute(
                "INSERT INTO questions (question_number, question_text, source_file) VALUES ('1', '第一题题干', ?)",
                (str(source_file),),
            ).lastrowid
        )
        second_id = int(
            conn.execute(
                "INSERT INTO questions (question_number, question_text, source_file) VALUES ('2', '第二题题干', ?)",
                (str(source_file),),
            ).lastrowid
        )
        conn.executemany(
            "INSERT INTO question_tags (question_id, tag_type, tag_value) VALUES (?, ?, ?)",
            [
                (first_id, "knowledge_point", "整式运算"),
                (first_id, "ability", "运算能力"),
                (first_id, "exam_scope", "七年级下册"),
                (first_id, "student_level", "基础巩固"),
            ],
        )

    monkeypatch.setattr(
        grading_paper_intake_service,
        "import_scanned_papers",
        lambda *_args, **_kwargs: BatchImportResult(
            [PaperImportFileResult(source_file=str(source_file), status="duplicate")],
            0,
            0,
            0,
            0,
            1,
            0,
        ),
    )

    class CapturingTagger:
        def __init__(self) -> None:
            self.question_ids: list[int] = []

        @staticmethod
        def taxonomy_contracts(contexts):
            return {question_id: {} for question_id in contexts}

        def analyze_questions(self, contexts, **_kwargs):
            self.question_ids = sorted(contexts)
            return {}

    tagger = CapturingTagger()
    grading_paper_intake_service.intake_grading_paper_to_question_bank(
        source_file=source_file,
        db_path=db_path,
        run_ai_tagging=True,
        ai_service=tagger,
        data_root=tmp_path,
    )

    assert tagger.question_ids == [second_id]
