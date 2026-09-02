from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.teaching_prep.application.question_selection import (
    QuestionSelectionError,
    SelectionRequest,
    fetch_question_content,
    list_volume_sections,
    select_questions,
)


def _bank(path: Path) -> Path:
    """构造最小题库：题目、小节/方法/知识点标签、考频与判重链接。"""
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE questions (
                id INTEGER PRIMARY KEY,
                question_number TEXT,
                question_type TEXT,
                question_text TEXT,
                answer_text TEXT,
                difficulty TEXT,
                has_images INTEGER,
                image_paths TEXT,
                source_file TEXT,
                needs_review INTEGER,
                is_deleted INTEGER,
                updated_at TEXT
            );
            CREATE TABLE question_tags (
                id INTEGER PRIMARY KEY,
                question_id INTEGER,
                tag_type TEXT,
                tag_value TEXT
            );
            CREATE TABLE question_frequency_cache (
                question_id INTEGER PRIMARY KEY,
                score_midterm REAL,
                score_final REAL,
                score_zhongkao REAL
            );
            CREATE TABLE question_duplicate_links (
                id INTEGER PRIMARY KEY,
                question_id INTEGER,
                duplicate_of_question_id INTEGER
            );
            """
        )
    return path


def _add_question(
    path: Path,
    question_id: int,
    *,
    section: str = "s1",
    difficulty: int = 3,
    stem: str | None = None,
    method: str | None = None,
    frequency: tuple[float, float, float] = (0.0, 0.0, 0.0),
    is_deleted: int = 0,
    needs_review: int = 0,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO questions (
                id, question_number, question_type, question_text,
                answer_text, difficulty, has_images, image_paths,
                source_file, needs_review, is_deleted, updated_at
            ) VALUES (?, ?, '计算题', ?, '合成答案', ?, 0, NULL,
                      'synthetic.pdf', ?, ?, '2026-07-02')
            """,
            (
                question_id,
                str(question_id),
                stem if stem is not None else f"合成题干 {question_id}",
                str(difficulty),
                needs_review,
                is_deleted,
            ),
        )
        connection.execute(
            """
            INSERT INTO question_tags (id, question_id, tag_type, tag_value)
            VALUES (?, ?, 'curriculum_section', ?)
            """,
            (question_id * 10, question_id, section),
        )
        if method is not None:
            connection.execute(
                """
                INSERT INTO question_tags (id, question_id, tag_type, tag_value)
                VALUES (?, ?, 'method', ?)
                """,
                (question_id * 10 + 1, question_id, method),
            )
        connection.execute(
            """
            INSERT INTO question_frequency_cache (
                question_id, score_midterm, score_final, score_zhongkao
            ) VALUES (?, ?, ?, ?)
            """,
            (question_id, *frequency),
        )


def _request(**overrides: object) -> SelectionRequest:
    values: dict[str, object] = {
        "volume_id": "bnu24-math-g7-upper",
        "section_ids": ("s1",),
        "difficulty_max": 5,
        "stem_max_chars": 220,
        "limit": 12,
        "max_per_method": 2,
    }
    values.update(overrides)
    return SelectionRequest(**values)  # type: ignore[arg-type]


def test_request_validation_rejects_out_of_range_input() -> None:
    with pytest.raises(QuestionSelectionError):
        _request(section_ids=())
    with pytest.raises(QuestionSelectionError):
        _request(section_ids=tuple(f"s{i}" for i in range(13)))
    with pytest.raises(QuestionSelectionError):
        _request(difficulty_max=0)
    with pytest.raises(QuestionSelectionError):
        _request(difficulty_max=10)
    with pytest.raises(QuestionSelectionError):
        _request(stem_max_chars=0)
    with pytest.raises(QuestionSelectionError):
        _request(stem_max_chars=401)
    with pytest.raises(QuestionSelectionError):
        _request(limit=0)
    with pytest.raises(QuestionSelectionError):
        _request(limit=31)
    with pytest.raises(QuestionSelectionError):
        _request(max_per_method=7)


def test_hard_filters_scope_difficulty_deleted_stem_and_exclusion(
    tmp_path: Path,
) -> None:
    bank = _bank(tmp_path / "questions.db")
    _add_question(bank, 1, difficulty=3, frequency=(1.0, 0.0, 0.0))
    _add_question(bank, 2, difficulty=6, frequency=(9.0, 9.0, 9.0))
    _add_question(bank, 3, is_deleted=1, frequency=(9.0, 9.0, 9.0))
    _add_question(bank, 4, stem="长" * 300, frequency=(9.0, 9.0, 9.0))
    _add_question(bank, 5, section="s2", frequency=(9.0, 9.0, 9.0))
    _add_question(bank, 6, difficulty=2, frequency=(8.0, 0.0, 0.0))

    result = select_questions(
        bank,
        _request(difficulty_max=5, exclude_question_ids=(6,)),
    )

    assert [item["question_id"] for item in result["items"]] == [1]
    assert result["stats"]["candidate_total"] == 3
    assert result["stats"]["dropped_stem_length"] == 1
    assert result["stats"]["dropped_excluded"] == 1
    assert result["stats"]["selected"] == 1
    assert result["request"]["exclude_question_ids"] == [6]


def test_frequency_desc_then_difficulty_asc_then_id_order(
    tmp_path: Path,
) -> None:
    bank = _bank(tmp_path / "questions.db")
    _add_question(bank, 1, difficulty=4, frequency=(1.0, 0.0, 0.0))
    _add_question(bank, 2, difficulty=1, frequency=(1.0, 0.0, 0.0))
    _add_question(bank, 3, difficulty=2, frequency=(2.0, 0.0, 0.0))

    result = select_questions(bank, _request())

    assert [item["question_id"] for item in result["items"]] == [3, 2, 1]
    assert [item["difficulty"] for item in result["items"]] == [2, 1, 4]
    assert result["items"][0]["frequency_score"] == 2.0


def test_needs_review_questions_are_dropped(tmp_path: Path) -> None:
    bank = _bank(tmp_path / "questions.db")
    _add_question(bank, 1, frequency=(5.0, 0.0, 0.0), needs_review=1)
    _add_question(bank, 2, frequency=(2.0, 0.0, 0.0))

    result = select_questions(bank, _request())

    assert [item["question_id"] for item in result["items"]] == [2]
    assert result["stats"]["dropped_needs_review"] == 1


def test_duplicate_partner_is_dropped_after_one_is_selected(
    tmp_path: Path,
) -> None:
    bank = _bank(tmp_path / "questions.db")
    _add_question(bank, 1, frequency=(3.0, 0.0, 0.0))
    _add_question(bank, 2, frequency=(2.0, 0.0, 0.0))
    _add_question(bank, 3, frequency=(1.0, 0.0, 0.0))
    with sqlite3.connect(bank) as connection:
        connection.execute(
            """
            INSERT INTO question_duplicate_links (
                id, question_id, duplicate_of_question_id
            ) VALUES (1, 1, 2)
            """
        )

    result = select_questions(bank, _request())

    assert [item["question_id"] for item in result["items"]] == [1, 3]
    assert result["stats"]["dropped_duplicate"] == 1


def test_method_diversity_cap_and_limit(tmp_path: Path) -> None:
    bank = _bank(tmp_path / "questions.db")
    _add_question(bank, 1, method="配方法", frequency=(5.0, 0.0, 0.0))
    _add_question(bank, 2, method="配方法", frequency=(4.0, 0.0, 0.0))
    _add_question(bank, 3, method="配方法", frequency=(3.0, 0.0, 0.0))
    _add_question(bank, 4, method="换元法", frequency=(2.0, 0.0, 0.0))
    _add_question(bank, 5, frequency=(1.0, 0.0, 0.0))

    result = select_questions(bank, _request(max_per_method=2))

    assert [item["question_id"] for item in result["items"]] == [1, 2, 4, 5]
    assert result["stats"]["dropped_method_balance"] == 1
    assert result["method_distribution"] == {"配方法": 2, "换元法": 1}

    capped = select_questions(bank, _request(limit=2, max_per_method=6))
    assert [item["question_id"] for item in capped["items"]] == [1, 2]
    assert capped["stats"]["selected"] == 2


def test_item_payload_carries_reason_method_and_knowledge(
    tmp_path: Path,
) -> None:
    bank = _bank(tmp_path / "questions.db")
    _add_question(
        bank,
        1,
        method="配方法",
        frequency=(1.5, 0.5, 0.0),
    )
    with sqlite3.connect(bank) as connection:
        connection.execute(
            """
            INSERT INTO question_tags (id, question_id, tag_type, tag_value)
            VALUES (99, 1, 'knowledge_point', '一元二次方程')
            """
        )

    item = select_questions(bank, _request())["items"][0]

    assert item["stem"] == "合成题干 1"
    assert item["method"] == "配方法"
    assert item["knowledge_points"] == ["一元二次方程"]
    assert item["selection_reason"]["method"] == "配方法"
    assert item["frequency_score"] == 2.0
    # 预览负载不携带答案正文、图片绝对路径与来源文件，防止路径与答案泄露
    assert "answer_text" not in item
    assert "image_paths" not in item
    assert "source_file" not in item


def test_fetch_question_content_reads_current_rows_only(
    tmp_path: Path,
) -> None:
    bank = _bank(tmp_path / "questions.db")
    _add_question(bank, 1)
    _add_question(bank, 2, is_deleted=1)
    with sqlite3.connect(bank) as connection:
        connection.execute(
            """
            UPDATE questions
            SET question_text = 'x<sup>2</sup>=4',
                answer_text = 'x=±2',
                has_images = 1,
                image_paths = '["q1-a.png", "q1-b.png"]'
            WHERE id = 1
            """
        )

    content = fetch_question_content(bank, [1, 2, 999])

    assert sorted(content) == [1]
    assert content[1]["stem_html"] == "x<sup>2</sup>=4"
    assert content[1]["answer_html"] == "x=±2"
    assert content[1]["image_paths"] == ["q1-a.png", "q1-b.png"]
    assert fetch_question_content(bank, []) == {}


def test_list_volume_sections_counts_questions_per_section(
    tmp_path: Path,
) -> None:
    from question_bank.taxonomy.curriculum_catalog import (
        load_curriculum_catalog,
    )

    catalog = load_curriculum_catalog()
    volume = catalog["volumes"][0]
    section = volume["chapters"][0]["sections"][0]
    other = volume["chapters"][0]["sections"][1]
    bank = _bank(tmp_path / "questions.db")
    _add_question(bank, 1, section=section["id"])
    _add_question(bank, 2, section=section["id"])
    _add_question(bank, 3, section=section["id"], is_deleted=1)

    sections = list_volume_sections(bank, volume["id"])

    by_id = {item["section_id"]: item for item in sections}
    assert by_id[section["id"]]["question_count"] == 2
    assert by_id[other["id"]]["question_count"] == 0
    assert by_id[section["id"]]["chapter_id"] == volume["chapters"][0]["id"]
    assert sections[0]["section_name"]
    assert list_volume_sections(bank, "unknown-volume") == []
    with pytest.raises(QuestionSelectionError):
        list_volume_sections(bank, "")
