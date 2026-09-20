from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from question_bank.current_knowledge import CurrentKnowledgeResolver
from question_bank.database.schema import initialize_database
from question_bank.models.question import QuestionCreate, TagCreate
from question_bank.models.tag_schema import TagAnalysis
from question_bank.services.question_frequency_service import QuestionFrequencyService
from question_bank.services.question_read_service import (
    QuestionBankReadService,
    QuestionReadFilters,
)
from question_bank.services.question_write_service import QuestionBankWriteService
from question_bank.taxonomy.curriculum_catalog import load_curriculum_catalog
from tests.current_knowledge_support import install_current_knowledge


def _services(tmp_path: Path):
    db_path = tmp_path / "question_bank.db"
    return (
        db_path,
        QuestionBankReadService(db_path, data_root=tmp_path),
        QuestionBankWriteService(db_path, data_root=tmp_path),
    )


def test_page_tags_reads_taxonomy_once_for_many_teacher_terms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import question_bank.services.question_read_service as read_module
    from question_bank.taxonomy.governance import ALLOWED_DIMENSIONS

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE question_tags (id INTEGER PRIMARY KEY, question_id INTEGER, tag_type TEXT, tag_value TEXT, confidence REAL)"
    )
    connection.executemany(
        "INSERT INTO question_tags (question_id, tag_type, tag_value, confidence) VALUES (?, 'knowledge_point', ?, 1)",
        [(index, f"教师词{index}") for index in range(1, 101)],
    )

    class CurrentKnowledge:
        @staticmethod
        def canonical_term(_value: str):
            return None

        @staticmethod
        def resolve(_value: str):
            return None

    class Governance:
        calls = 0

        def identity_and_teacher_lookup(self):
            self.calls += 1
            return (
                {dimension: {} for dimension in ALLOWED_DIMENSIONS},
                {
                    **{dimension: {} for dimension in ALLOWED_DIMENSIONS},
                    "knowledge": {
                        read_module._taxonomy_value_key(f"教师词{index}"): f"教师词{index}"
                        for index in range(1, 101)
                    },
                },
            )

    governance = Governance()
    monkeypatch.setattr(read_module, "get_taxonomy_governance", lambda: governance)

    tags = read_module._load_page_tags(
        connection,
        list(range(1, 101)),
        current_knowledge=CurrentKnowledge(),
    )

    assert governance.calls == 1
    assert len(tags) == 100


def test_question_schema_has_reason_column_and_migration_file(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(questions)").fetchall()
        }

    assert "reason" in columns
    assert Path("migrations/question_bank/003_add_question_reason.sql").exists()


def test_canonical_write_and_read_services_round_trip_question(tmp_path: Path) -> None:
    db_path, reader, writer = _services(tmp_path)
    question_id = writer.add_question(
        QuestionCreate(
            question_number="1",
            question_text="计算 a²·a³。",
            answer_text="a⁵",
            question_type="计算题",
            tags=[TagCreate("method", "幂的运算", source="manual")],
        )
    )

    saved = reader.get_question(question_id)

    assert saved is not None
    assert saved["question_text"] == "计算 a²·a³。"
    assert saved["answer_text"] == "a⁵"
    assert [
        (tag["tag_type"], tag["tag_value"])
        for tag in saved["tags"]
    ] == [("method", "幂的运算")]
    with sqlite3.connect(db_path) as conn:
        source = conn.execute(
            "SELECT source FROM question_tags WHERE question_id = ?",
            (question_id,),
        ).fetchone()[0]
    assert source == "manual"


def test_analysis_write_preserves_manual_tags_and_records_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, reader, writer = _services(tmp_path)
    question_id = writer.add_question(
        QuestionCreate(
            question_number="1",
            question_text="计算 a²·a³。",
            answer_text="a⁵",
            tags=[TagCreate("ability", "手工保留能力", source="manual")],
        )
    )
    install_current_knowledge(db_path)
    analysis = TagAnalysis.from_dict(
        {
            "knowledge_points": ["整式运算"],
            "method_tags": ["运算法则"],
            "thought_tags": [],
            "ability_tags": ["运算求解"],
            "math_model_tags": [],
            "special_type_tags": [],
            "difficulty": 4,
            "error_prone_points": [],
            "prerequisite_points": [],
            "textbook_chapters": ["七年级上册"],
            "curriculum_sections": [],
            "teaching_stage": "",
            "suitable_student_level": "",
            "reason": "考查幂的运算。",
            "confidence": 0.91,
            "canonical_knowledge_id": "kp_alg_polynomial",
            "sub_skills": [],
            "measured_skills": [],
            "supporting_skills": [],
        }
    )
    resolver_calls: list[Path] = []
    refresh_calls: list[int] = []
    original_resolver = CurrentKnowledgeResolver.from_active_database
    original_refresh = QuestionFrequencyService.invalidate_frequency_cache_for_question

    def tracking_resolver(
        _cls: type[CurrentKnowledgeResolver],
        path: Path,
    ) -> CurrentKnowledgeResolver:
        resolver_calls.append(Path(path))
        return original_resolver(path)

    def tracking_refresh(
        self: QuestionFrequencyService,
        refreshed_question_id: int,
    ) -> None:
        refresh_calls.append(int(refreshed_question_id))
        original_refresh(self, refreshed_question_id)

    monkeypatch.setattr(
        CurrentKnowledgeResolver,
        "from_active_database",
        classmethod(tracking_resolver),
    )
    monkeypatch.setattr(
        QuestionFrequencyService,
        "invalidate_frequency_cache_for_question",
        tracking_refresh,
    )

    assert writer.save_tag_analysis(
        question_id,
        analysis,
        model_name="synthetic-model",
        confidence=0.77,
    )
    saved = reader.get_question(question_id)

    assert saved is not None
    assert any(tag["tag_value"] == "手工保留能力" for tag in saved["tags"])
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        stored_tags = conn.execute(
            """
            SELECT tag_value, source, model_name, confidence
            FROM question_tags
            WHERE question_id = ?
            ORDER BY id
            """,
            (question_id,),
        ).fetchall()
    assert any(
        tag["tag_value"] == "手工保留能力" and tag["source"] == "manual"
        for tag in stored_tags
    )
    ai_tags = [tag for tag in stored_tags if tag["source"] == "ai"]
    assert ai_tags
    assert {tag["model_name"] for tag in ai_tags} == {"synthetic-model"}
    assert {round(float(tag["confidence"]), 2) for tag in ai_tags} == {0.77}
    assert resolver_calls == [db_path]
    assert refresh_calls == [question_id]


def test_read_service_owns_filtering_and_difficulty_sort(tmp_path: Path) -> None:
    _db_path, reader, writer = _services(tmp_path)
    for number, difficulty in (("1", "4"), ("2", "9"), ("3", "6")):
        writer.add_question(
            QuestionCreate(
                question_number=number,
                question_text=f"题目 {number}",
                difficulty=difficulty,
            )
        )

    page = reader.list_questions(
        QuestionReadFilters(sort="difficulty_desc", page_size=10)
    )

    assert [item["question_number"] for item in page.items] == ["2", "3", "1"]


def test_read_service_filters_questions_by_exact_curriculum_volume(tmp_path: Path) -> None:
    db_path, reader, _writer = _services(tmp_path)
    initialize_database(db_path)
    volumes = load_curriculum_catalog()["volumes"][:2]
    with sqlite3.connect(db_path) as conn:
        for index, volume in enumerate(volumes, start=1):
            conn.execute(
                """
                INSERT INTO papers (
                    id, title, grade, semester, textbook_version, import_status
                ) VALUES (?, ?, ?, ?, ?, 'success')
                """,
                (
                    index,
                    volume["label"],
                    volume["grade"],
                    volume["semester"],
                    volume["textbook_version"],
                ),
            )
            conn.execute(
                """
                INSERT INTO questions (
                    id, paper_id, question_number, question_text
                ) VALUES (?, ?, ?, ?)
                """,
                (index, index, str(index), f"{volume['label']}试题"),
            )

    page = reader.list_questions(
        QuestionReadFilters(
            curriculum_volume_ids=(str(volumes[1]["id"]),),
            page_size=10,
        )
    )

    assert [item["question_number"] for item in page.items] == ["2"]


def test_find_similar_questions_matches_shared_canonical_knowledge(
    tmp_path: Path,
) -> None:
    db_path, reader, writer = _services(tmp_path)
    target_id = writer.add_question(
        QuestionCreate(
            question_number="1",
            question_text="求 8 的立方根。",
            question_type="填空题",
            difficulty="3",
            tags=[
                TagCreate(
                    "knowledge_point",
                    "kp_bnu24_math_g8_upper_2_2",
                    source="manual",
                ),
            ],
        )
    )
    variant_id = writer.add_question(
        QuestionCreate(
            question_number="2",
            question_text="已知一个数的立方根是 2，求这个数的相反数。",
            question_type="填空题",
            difficulty="4",
            tags=[
                TagCreate(
                    "knowledge_point",
                    "kp_bnu24_math_g8_upper_2_2",
                    source="manual",
                ),
                TagCreate(
                    "knowledge_point",
                    "sk_bnu24_math_g8_upper_2_2_102",
                    source="manual",
                ),
            ],
        )
    )
    unrelated_id = writer.add_question(
        QuestionCreate(
            question_number="3",
            question_text="观察图形，说出其中立体图形的名称。",
            question_type="填空题",
            difficulty="4",
            tags=[
                TagCreate(
                    "knowledge_point",
                    "kp_bnu24_math_g7_upper_1",
                    source="manual",
                ),
            ],
        )
    )
    install_current_knowledge(db_path, taxonomy_revision=8)

    items = reader.find_similar_questions(target_id, limit=5)
    ids = {int(item["id"]) for item in items}

    assert variant_id in ids
    assert unrelated_id not in ids


def test_detail_splits_skill_nodes_out_of_knowledge_point_tags(
    tmp_path: Path,
) -> None:
    db_path, reader, writer = _services(tmp_path)
    question_id = writer.add_question(
        QuestionCreate(
            question_number="1",
            question_text="求 27 的立方根。",
            question_type="填空题",
            tags=[
                TagCreate(
                    "knowledge_point",
                    "kp_bnu24_math_g8_upper_2_2",
                    source="manual",
                ),
                TagCreate(
                    "knowledge_point",
                    "sk_bnu24_math_g8_upper_2_2_104",
                    source="manual",
                ),
            ],
        )
    )
    install_current_knowledge(db_path, taxonomy_revision=8)

    detail = reader.get_question(question_id)
    pairs = {(tag["tag_type"], tag["tag_value"]) for tag in detail["tags"]}

    assert (
        "knowledge_point",
        "八年级上册｜第二章 实数｜2 平方根与立方根",
    ) in pairs
    assert (
        "skill",
        "八年级上册｜第二章 实数｜2 平方根与立方根｜技能·求立方根",
    ) in pairs
    assert all(
        "技能·" not in value
        for tag_type, value in pairs
        if tag_type == "knowledge_point"
    )


def test_replace_tags_stores_skill_as_knowledge_point(tmp_path: Path) -> None:
    from question_bank.services.question_write_service import ConfirmedQuestionTag

    db_path, _reader, writer = _services(tmp_path)
    question_id = writer.add_question(
        QuestionCreate(
            question_number="1",
            question_text="求 27 的立方根。",
            question_type="填空题",
        )
    )
    revision = writer.get_revision(question_id)

    writer.replace_tags(
        question_id,
        expected_revision=revision,
        tags=[
            ConfirmedQuestionTag(
                "skill",
                "八年级上册｜第二章 实数｜2 平方根与立方根｜技能·求立方根",
                None,
            ),
        ],
    )

    with sqlite3.connect(db_path) as conn:
        stored = conn.execute(
            "SELECT tag_type, tag_value FROM question_tags WHERE question_id = ?",
            (question_id,),
        ).fetchall()
    assert stored == [
        (
            "knowledge_point",
            "八年级上册｜第二章 实数｜2 平方根与立方根｜技能·求立方根",
        )
    ]
