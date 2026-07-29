from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from question_bank.database.schema import connect
from question_bank.services.question_frequency_service import (
    QuestionFrequencyService,
    build_question_fingerprint,
    is_frequency_exam_type,
)


def _insert_paper(
    db_path: Path,
    *,
    title: str,
    exam_type: str,
    grade: str = "七年级",
    semester: str = "下学期",
    province: str = "广东省",
    city: str = "深圳市",
) -> int:
    with connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO papers (
                title, exam_type, grade, semester, province, city, import_status
            ) VALUES (?, ?, ?, ?, ?, ?, 'ready')
            """,
            (title, exam_type, grade, semester, province, city),
        )
        return int(cursor.lastrowid)


def _insert_question(
    db_path: Path,
    paper_id: int,
    *,
    number: str,
    question_type: str = "选择题",
    knowledge: str = "科学记数法",
    method: str = "数形结合",
    ability: str = "运算能力",
    model: str = "",
    difficulty: str = "4",
) -> int:
    with connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO questions (
                paper_id, question_number, question_type, question_text, difficulty
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (paper_id, number, question_type, f"{knowledge}-{number}", difficulty),
        )
        question_id = int(cursor.lastrowid)
        rows = [
            (question_id, "knowledge_point", knowledge),
            (question_id, "ability", ability),
            (question_id, "exam_scope", "七年级下册"),
            (question_id, "student_level", "基础巩固"),
        ]
        if method:
            rows.append((question_id, "method", method))
        if model:
            rows.append((question_id, "model", model))
        conn.executemany(
            "INSERT INTO question_tags (question_id, tag_type, tag_value) VALUES (?, ?, ?)",
            rows,
        )
        return question_id


def test_only_formal_exams_have_frequency() -> None:
    assert is_frequency_exam_type("期中考试")
    assert is_frequency_exam_type("期末")
    assert is_frequency_exam_type("中考")
    assert not is_frequency_exam_type("同步练习")
    assert not is_frequency_exam_type("专题练习")
    assert not is_frequency_exam_type("期末同步练习")


def test_frequency_counts_at_most_one_best_match_per_paper(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    service = QuestionFrequencyService(db_path)
    service.initialize_database()
    first_paper = _insert_paper(db_path, title="A", exam_type="期末")
    second_paper = _insert_paper(db_path, title="B", exam_type="期末")
    target_id = _insert_question(db_path, first_paper, number="1")
    _insert_question(db_path, first_paper, number="2")
    _insert_question(db_path, second_paper, number="1")

    metrics = service.metrics_for_question(target_id)

    assert metrics.available
    assert metrics.matched_question_count == 2
    assert metrics.eligible_paper_count == 2
    assert metrics.questions_per_paper == 1.0


def test_practice_invalidation_does_not_clear_formal_exam_cache(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    service = QuestionFrequencyService(db_path)
    service.initialize_database()
    formal_paper = _insert_paper(db_path, title="期末卷", exam_type="期末")
    practice_paper = _insert_paper(db_path, title="阶段小测", exam_type="阶段练习")
    formal_question = _insert_question(db_path, formal_paper, number="1")
    practice_question = _insert_question(db_path, practice_paper, number="1")
    with connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO question_frequency_cache (
                question_id, score_midterm, score_final, score_zhongkao
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (formal_question, 0.1, 0.8, 0.2),
                (practice_question, 0.0, 0.0, 0.0),
            ],
        )

    service.invalidate_frequency_cache_for_question(practice_question)

    with connect(db_path) as conn:
        remaining = conn.execute(
            """
            SELECT question_id, score_midterm, score_final, score_zhongkao
            FROM question_frequency_cache
            ORDER BY question_id
            """
        ).fetchall()
    assert [int(row["question_id"]) for row in remaining] == [
        formal_question,
        practice_question,
    ]
    formal = remaining[0]
    practice = remaining[1]
    assert (
        float(formal["score_midterm"]),
        float(formal["score_final"]),
        float(formal["score_zhongkao"]),
    ) == (0.1, 0.8, 0.2)
    assert all(
        0.0 <= float(practice[column]) <= 1.0
        for column in ("score_midterm", "score_final", "score_zhongkao")
    )


def test_frequency_excludes_practice_and_other_semesters(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    service = QuestionFrequencyService(db_path)
    service.initialize_database()
    target_paper = _insert_paper(db_path, title="期末A", exam_type="期末")
    same_scope = _insert_paper(db_path, title="期末B", exam_type="期末")
    other_semester = _insert_paper(db_path, title="上学期期末", exam_type="期末", semester="上学期")
    other_grade = _insert_paper(db_path, title="八年级期末", exam_type="期末", grade="八年级")
    other_exam = _insert_paper(db_path, title="七年级期中", exam_type="期中")
    practice = _insert_paper(db_path, title="同步练习", exam_type="同步练习")
    target_id = _insert_question(db_path, target_paper, number="1")
    _insert_question(db_path, same_scope, number="1")
    _insert_question(db_path, other_semester, number="1")
    _insert_question(db_path, other_grade, number="1")
    _insert_question(db_path, other_exam, number="1")
    _insert_question(db_path, practice, number="1")

    metrics = service.metrics_for_question(target_id)
    practice_metrics = service.metrics_for_question(
        _insert_question(db_path, practice, number="2")
    )

    assert metrics.matched_question_count == 2
    assert metrics.eligible_paper_count == 2
    assert not practice_metrics.available


def test_zhongkao_has_national_and_shenzhen_frequency_without_semester_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    service = QuestionFrequencyService(db_path)
    service.initialize_database()
    shenzhen = _insert_paper(
        db_path, title="深圳中考", exam_type="中考", grade="九年级", semester="", city="深圳市"
    )
    shenzhen_other_semester = _insert_paper(
        db_path, title="深圳中考2", exam_type="中考", grade="九年级", semester="下学期", city="深圳市"
    )
    hangzhou = _insert_paper(
        db_path, title="杭州中考", exam_type="中考", grade="九年级", semester="", province="浙江省", city="杭州市"
    )
    target_id = _insert_question(db_path, hangzhou, number="20", question_type="解答题", method="分类讨论")
    _insert_question(db_path, shenzhen, number="20", question_type="解答题", method="分类讨论")
    _insert_question(db_path, shenzhen_other_semester, number="20", question_type="解答题", method="分类讨论")

    metrics = service.metrics_for_question(target_id)

    assert metrics.national_matched_question_count == 3
    assert metrics.national_eligible_paper_count == 3
    assert metrics.shenzhen_matched_question_count == 2
    assert metrics.shenzhen_eligible_paper_count == 2


def test_fingerprint_uses_method_for_both_simple_and_solution_questions() -> None:
    # 用具体方法验证"方法参与指纹"；宽泛方法（数形结合/分类讨论）已被排除，
    # 不再用于区分指纹（见 GENERIC_METHOD_TAGS）。
    simple_a = build_question_fingerprint(
        {"question_type": "选择题", "tags": [{"tag_type": "knowledge_point", "tag_value": "概率"}]}
    )
    simple_b = build_question_fingerprint(
        {
            "question_type": "选择题",
            "tags": [
                {"tag_type": "knowledge_point", "tag_value": "概率"},
                {"tag_type": "method", "tag_value": "列举法"},
            ],
        }
    )
    solution_a = build_question_fingerprint(
        {
            "question_type": "解答题",
            "tags": [
                {"tag_type": "knowledge_point", "tag_value": "概率"},
                {"tag_type": "method", "tag_value": "列举法"},
            ],
        }
    )
    solution_b = build_question_fingerprint(
        {
            "question_type": "解答题",
            "tags": [
                {"tag_type": "knowledge_point", "tag_value": "概率"},
                {"tag_type": "method", "tag_value": "树状图法"},
            ],
        }
    )

    assert simple_a != simple_b
    assert solution_a != solution_b


def test_fingerprint_excludes_generic_method_tags() -> None:
    # 数形结合/分类讨论等宽泛方法不进指纹，避免几何综合题过度聚拢。
    without_generic = build_question_fingerprint(
        {"question_type": "解答题", "tags": [{"tag_type": "knowledge_point", "tag_value": "二次函数"}]}
    )
    with_generic_only = build_question_fingerprint(
        {
            "question_type": "解答题",
            "tags": [
                {"tag_type": "knowledge_point", "tag_value": "二次函数"},
                {"tag_type": "method", "tag_value": "数形结合"},
                {"tag_type": "method", "tag_value": "分类讨论"},
            ],
        }
    )
    assert without_generic == with_generic_only


def test_fingerprint_is_invariant_to_tag_order() -> None:
    # 标签顺序不应影响指纹（消除顺序敏感）。
    first_order = build_question_fingerprint(
        {
            "question_type": "解答题",
            "tags": [
                {"tag_type": "knowledge_point", "tag_value": "二次函数"},
                {"tag_type": "method", "tag_value": "待定系数法"},
                {"tag_type": "method", "tag_value": "配方法"},
            ],
        }
    )
    reversed_order = build_question_fingerprint(
        {
            "question_type": "解答题",
            "tags": [
                {"tag_type": "knowledge_point", "tag_value": "二次函数"},
                {"tag_type": "method", "tag_value": "配方法"},
                {"tag_type": "method", "tag_value": "待定系数法"},
            ],
        }
    )
    assert first_order == reversed_order


def test_fingerprint_splits_mid_difficulty_into_two_buckets() -> None:
    # 难度4-7拆为偏基础(4-5)/偏综合(6-7)，避免常规题与综合题混桶。
    base_mid = build_question_fingerprint(
        {
            "question_type": "解答题",
            "tags": [{"tag_type": "knowledge_point", "tag_value": "一元二次方程"}],
            "difficulty": "4",
        }
    )
    advanced_mid = build_question_fingerprint(
        {
            "question_type": "解答题",
            "tags": [{"tag_type": "knowledge_point", "tag_value": "一元二次方程"}],
            "difficulty": "7",
        }
    )
    assert base_mid != advanced_mid


def test_external_zhongkao_gets_explainable_shenzhen_fit_score(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    service = QuestionFrequencyService(db_path)
    service.initialize_database()
    shenzhen = _insert_paper(
        db_path, title="深圳中考", exam_type="中考", grade="九年级", semester="", city="深圳市"
    )
    hangzhou = _insert_paper(
        db_path, title="杭州中考", exam_type="中考", grade="九年级", semester="", province="浙江省", city="杭州市"
    )
    _insert_question(
        db_path,
        shenzhen,
        number="20",
        question_type="解答题",
        knowledge="二次函数",
        method="分类讨论",
        ability="推理能力",
        difficulty="8",
    )
    target_id = _insert_question(
        db_path,
        hangzhou,
        number="20",
        question_type="解答题",
        knowledge="二次函数",
        method="分类讨论",
        ability="推理能力",
        difficulty="8",
    )

    metrics = service.metrics_for_question(target_id)

    assert metrics.shenzhen_fit_available
    assert metrics.shenzhen_fit_score >= 0.8
    assert metrics.shenzhen_similar_question_ids
    assert any("方法" in note or "结构" in note for note in metrics.shenzhen_fit_notes)


def test_shenzhen_zhongkao_question_does_not_get_external_fit_score(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    service = QuestionFrequencyService(db_path)
    service.initialize_database()
    shenzhen = _insert_paper(
        db_path, title="深圳中考", exam_type="中考", grade="九年级", semester="", city="深圳市"
    )
    target_id = _insert_question(
        db_path, shenzhen, number="20", question_type="解答题", knowledge="二次函数", method="分类讨论"
    )

    metrics = service.metrics_for_question(target_id)

    assert not metrics.shenzhen_fit_available


def test_frequency_batch_read_uses_borrowed_readonly_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from question_bank.services import question_frequency_service as frequency_module

    db_path = tmp_path / "question_bank.db"
    legacy_service = QuestionFrequencyService(db_path)
    legacy_service.initialize_database()
    paper_id = _insert_paper(db_path, title="借用连接期末", exam_type="期末")
    question_id = _insert_question(db_path, paper_id, number="1", method="列举法")
    borrowed = sqlite3.connect(db_path)
    borrowed.row_factory = sqlite3.Row
    borrowed.execute("PRAGMA query_only = ON")
    try:
        monkeypatch.setattr(
            frequency_module,
            "initialize_database",
            lambda _path: pytest.fail("borrowed frequency read must not initialize the database"),
        )
        metrics = QuestionFrequencyService(
            db_path,
            external_connection=borrowed,
        ).metrics_for_questions([question_id])

        assert metrics[question_id].available
        assert borrowed.execute("SELECT 1").fetchone()[0] == 1
    finally:
        borrowed.close()


def test_frequency_batch_legacy_path_still_initializes_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from question_bank.services import question_frequency_service as frequency_module

    db_path = tmp_path / "question_bank.db"
    service = QuestionFrequencyService(db_path)
    service.initialize_database()
    paper_id = _insert_paper(db_path, title="legacy 期末", exam_type="期末")
    question_id = _insert_question(db_path, paper_id, number="1", method="列举法")
    initialize_calls: list[Path] = []
    original_initialize = frequency_module.initialize_database

    def tracking_initialize(path: Path) -> None:
        initialize_calls.append(path)
        original_initialize(path)

    monkeypatch.setattr(frequency_module, "initialize_database", tracking_initialize)

    metrics = service.metrics_for_questions([question_id])

    assert metrics[question_id].available
    assert initialize_calls == [db_path]
