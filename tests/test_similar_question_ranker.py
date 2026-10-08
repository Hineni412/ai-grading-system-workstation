"""Original-B adoption: frozen ranking parity and the real read/API contract."""
from __future__ import annotations

import random
from unittest.mock import patch

import pytest

from backend.api.routers.question_bank import list_similar_questions
from question_bank.database.schema import connect
from question_bank.services import question_read_service as reads
from question_bank.services.similar_question_ranker import (
    SimilarityPart, SimilarityQuestion, SimilarQuestionIndex,
)
from tools.experiment_similar_questions import Experiment, Part, Question


@pytest.fixture(autouse=True)
def clean_similarity_caches():
    reads._READ_RESULT_CACHE.clear()
    reads._SIMILAR_INDEX_CACHE.clear()
    yield
    reads._READ_RESULT_CACHE.clear()
    reads._SIMILAR_INDEX_CACHE.clear()


def test_adopted_ranking_matches_frozen_b_including_window_missing_tags_and_ties():
    rng = random.Random(20260928)
    original = []
    for qid in range(1, 141):
        tags = {dim: frozenset(rng.sample([f"{dim}{n}" for n in range(4)], rng.randrange(3)))
                for dim in ("skill", "topic", "section", "chapter", "method", "model", "thought", "ability", "special_type")}
        parts = tuple(Part(tags["skill"], tags["topic"], rng.choice(["exact_objective", "process_required", ""]),
                           frozenset(rng.sample(["calculation", "proof", "parameter"], rng.randrange(3))))
                      for _ in range(rng.randrange(4)))
        original.append(Question(qid, rng.choice(["计算：√12-√3", "求一元一次方程 x+3=9 的解", "证明两组三角形全等", "判断实数的取值范围"]),
                                 difficulty=rng.choice([None, 2., 4.8, 7.2]), tags=tags, parts=parts))
    frozen = Experiment(original)
    adopted = SimilarQuestionIndex([
        SimilarityQuestion(q.qid, q.text, q.difficulty, q.tags,
                           tuple(SimilarityPart(p.skills, p.topics, p.mode, p.operations) for p in q.parts))
        for q in original
    ])
    for target in original[::5]:
        for limit in (6, 20):
            result = adopted.rank(target.qid, limit)
            assert [qid for qid, _ in result] == frozen.rank(target.qid, "rules_bm25_rrf", limit)
            assert all(0 <= score <= 1 for _, score in result)
            assert [score for _, score in result] == sorted((score for _, score in result), reverse=True)


@pytest.fixture
def service(tmp_path, question_bank_database):
    db = question_bank_database(tmp_path / "question_bank.db")
    with connect(db) as conn:
        conn.executemany("INSERT INTO papers(id,title,import_status) VALUES(?,?,?)", [
            (1, "合成相似题验收", "success"), (2, "合成已删除试卷", "deleted"),
        ])
        conn.executemany("""INSERT INTO questions(id,paper_id,question_number,question_type,question_text,difficulty,is_deleted)
                            VALUES(?,?,?,?,?,?,?)""", [
            (1, 1, "1", "解答题", "求一元一次方程 x+1=5 的解，并写出计算过程", 3, 0),
            (2, 1, "2", "填空题", "求一元一次方程 x+2=7 的解，并写出计算过程", 3, 0),
            (3, 1, "3", "选择题", "合成统计样本中位数的含义是什么", 3, 0),
            (4, 1, "4", "解答题", "求一元一次方程 x+1=5 的解，并写出计算过程", 3, 1),
            (5, 2, "5", "解答题", "求一元一次方程 x+1=5 的解，并写出计算过程", 3, 0),
            (6, 1, "6", "选择题", "求一元一次方程 y-3=4 的解，并写出计算过程", 3, 0),
        ])
    return reads.QuestionBankReadService(db, data_root=tmp_path)


def test_service_and_api_preserve_cross_type_results_and_hide_deleted_questions(service):
    response = list_similar_questions(1, limit=6, service=service)
    assert {item.id for item in response.items} == {2, 6}
    assert all(item.similarity_reasons and item.revision for item in response.items)
    assert {item.question_type for item in response.items} == {"填空题", "选择题"}
    assert len(service.find_similar_questions(1, limit=1)) == 1
    assert service.find_similar_questions(3, limit=6) == []
    assert service.find_similar_questions(4, limit=6) is None
    assert service.find_similar_questions(999, limit=6) is None


def test_index_reused_across_targets_and_rebuilt_after_question_changes(service):
    with patch.object(reads, "build_similar_question_index", wraps=reads.build_similar_question_index) as build:
        first = service.find_similar_questions(1, limit=6)
        service.find_similar_questions(2, limit=6)
        assert build.call_count == 1
        first[0]["similarity_reasons"].clear()
        assert service.find_similar_questions(1, limit=6)[0]["similarity_reasons"]
        with connect(service.db_path) as conn:
            conn.execute("UPDATE questions SET question_text='合成统计样本说明' WHERE id=2")
            conn.execute("UPDATE questions SET is_deleted=1 WHERE id=6")
        assert service.find_similar_questions(1, limit=6) == []
        assert build.call_count == 2


def test_taxonomy_change_invalidates_result_and_prepared_index(service, monkeypatch):
    state = [1]
    monkeypatch.setattr(reads, "_taxonomy_generation_token", lambda: tuple(state))
    with patch.object(reads, "build_similar_question_index", wraps=reads.build_similar_question_index) as build:
        before = service.find_similar_questions(1, limit=6)
        state[0] = 2
        assert service.find_similar_questions(1, limit=6) == before
        assert build.call_count == 2


def test_active_read_transaction_does_not_reuse_a_later_index(service):
    with reads._read_connection(service.db_path):
        before = service.find_similar_questions(1, limit=6)
        with connect(service.db_path) as writer:
            writer.execute("UPDATE questions SET is_deleted=1 WHERE id=2")
        assert service.find_similar_questions(1, limit=6) == before
    assert {row["id"] for row in service.find_similar_questions(1, limit=6)} == {6}


def test_current_question_types_score_independently_from_knowledge_topics():
    primary = 'kp_bnu24_math_g8_lower_1_1_t01'
    other = 'kp_bnu24_math_g8_lower_1_1_t02'
    questions = [
        SimilarityQuestion(1, 'TEST-判断条件甲', 3, {'type': frozenset({primary}), 'topic': frozenset({'shared'})}),
        SimilarityQuestion(2, 'TEST-判断条件乙', 3, {'type': frozenset({primary}), 'topic': frozenset({'different'})}),
        SimilarityQuestion(3, 'TEST-判断条件丙', 3, {'type': frozenset({other}), 'topic': frozenset({'shared'})})]
    index = SimilarQuestionIndex(questions)
    same_type = index.components(questions[0], questions[1])
    same_topic = index.components(questions[0], questions[2])
    assert same_type['type'] == 1 and same_type['knowledge'] == 0
    assert same_topic['type'] == 0 and same_topic['knowledge'] == 1
    assert same_type['semantic'] > same_topic['semantic']
    assert [qid for qid, score in index.rank(1)] == [2, 3]
