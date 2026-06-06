from __future__ import annotations

import sqlite3
from pathlib import Path

from question_bank.models.question import QuestionCreate, TagCreate
from question_bank.models.tag_schema import TagAnalysis, TaggingContext
from question_bank.services.ai_tagging_service import AITaggingResult, AITaggingService, _adaptive_batches
from question_bank.services.question_service import QuestionService, has_complete_analysis_tags


def _analysis(**overrides) -> TagAnalysis:
    payload = {
        "knowledge_points": ["整式运算"],
        "method_tags": ["整体思想"],
        "ability_tags": ["运算能力"],
        "math_model_tags": [],
        "difficulty": 4,
        "error_prone_points": ["运算化简错误"],
        "prerequisite_points": ["幂的运算"],
        "textbook_chapter": "七年级下册 第一章 整式的乘除",
        "teaching_stage": "期末复习",
        "suitable_student_level": "基础巩固",
        "reason": "考查幂运算和整式化简。",
        "confidence": 0.86,
    }
    payload.update(overrides)
    return TagAnalysis.from_dict(payload)


def test_tag_analysis_normalizes_confidence_and_string_list_fields() -> None:
    analysis = TagAnalysis.from_dict(
        {
            "knowledge_points": "科学记数法",
            "method_tags": "数形结合",
            "ability_tags": "运算能力",
            "math_model_tags": "",
            "difficulty": 4,
            "error_prone_points": "运算化简错误",
            "prerequisite_points": "有理数运算",
            "textbook_chapter": ["七年级上册 第二章 有理数及其运算"],
            "teaching_stage": "期末复习",
            "suitable_student_level": "基础巩固",
            "reason": "可直接判断。",
            "confidence": 1.8,
        }
    )

    assert analysis.knowledge_points == ["科学记数法"]
    assert analysis.method_tags == ["数形结合"]
    assert analysis.math_model_tags == []
    assert analysis.error_prone_points == ["运算化简错误"]
    assert analysis.textbook_chapter == "七年级上册 第二章 有理数及其运算"
    assert analysis.confidence == 1.0


def test_save_tag_analysis_persists_model_name_and_confidence(tmp_path: Path) -> None:
    db_path = tmp_path / "question_bank.db"
    service = QuestionService(db_path)
    question_id = service.add_question(
        QuestionCreate(question_number="1", question_text="计算 a^2 · a^3。", answer_text="a^5")
    )

    assert service.save_tag_analysis(
        question_id,
        _analysis(confidence=0.91),
        model_name="doubao-tag",
        confidence=0.77,
    )

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT tag_type, confidence, model_name FROM question_tags WHERE question_id = ?",
            (question_id,),
        ).fetchall()

    assert rows
    assert {row["model_name"] for row in rows} == {"doubao-tag"}
    assert {round(float(row["confidence"]), 2) for row in rows} == {0.77}


def test_only_scope_and_student_level_is_not_complete_analysis_tags(tmp_path: Path) -> None:
    service = QuestionService(tmp_path / "question_bank.db")
    question_id = service.add_question(
        QuestionCreate(
            question_number="1",
            question_text="如图，添加条件使两直线平行。",
            tags=[
                TagCreate("exam_scope", "七年级下册 第二章 相交线与平行线", source="ai"),
                TagCreate("student_level", "基础巩固", source="ai"),
            ],
        )
    )

    saved = service.get_question(question_id)

    assert saved is not None
    assert not has_complete_analysis_tags(saved)


def test_exact_duplicate_complete_tags_can_be_reused_with_confidence_cap(tmp_path: Path) -> None:
    service = QuestionService(tmp_path / "question_bank.db")
    source_id = service.add_question(
        QuestionCreate(question_number="1", question_text="计算 a^2 · a^3。", answer_text="a^5")
    )
    target_id = service.add_question(
        QuestionCreate(question_number="2", question_text=" 计算 a^2 · a^3。 ", answer_text=" a^5 ")
    )
    assert service.save_tag_analysis(source_id, _analysis(confidence=0.97), model_name="doubao-main", confidence=0.97)

    duplicate = service.find_exact_duplicate_tag_analysis(target_id)

    assert duplicate is not None
    analysis, model_name = duplicate
    assert analysis.knowledge_points == ["整式运算"]
    assert analysis.confidence == 0.9
    assert model_name == "doubao-main"


class _FakeTaggingService(AITaggingService):
    def __init__(
        self,
        primary: dict[int, list[TagAnalysis]],
        review: dict[int, TagAnalysis] | None = None,
    ) -> None:
        super().__init__(env={}, llm_client=None)
        self.primary = {qid: list(items) for qid, items in primary.items()}
        self.review = dict(review or {})
        self.primary_calls: list[int] = []
        self.review_calls: list[int] = []
        self.model = "doubao-main"
        self.review_model = "deepseek-review" if review else ""

    @property
    def mock_mode(self) -> bool:
        return False

    def analyze_question(self, context: TaggingContext) -> AITaggingResult:
        qid = int(context.question_number or 0)
        self.primary_calls.append(qid)
        items = self.primary[qid]
        analysis = items.pop(0) if len(items) > 1 else items[0]
        return AITaggingResult(ok=True, mock_mode=False, analysis=analysis, model_name=self.model)

    def analyze_review_question(self, context: TaggingContext) -> AITaggingResult:
        qid = int(context.question_number or 0)
        self.review_calls.append(qid)
        analysis = self.review[qid]
        return AITaggingResult(ok=True, mock_mode=False, analysis=analysis, model_name=self.review_model)


def test_batch_incomplete_single_question_retries_without_affecting_neighbors() -> None:
    service = _FakeTaggingService(
        primary={
            1: [_analysis()],
            2: [
                _analysis(knowledge_points=[], ability_tags=[], confidence=0.91),
                _analysis(knowledge_points=["概率初步"], ability_tags=["数据观念"], confidence=0.88),
            ],
        }
    )
    contexts = {
        1: TaggingContext(question_text="计算 a^2 · a^3。", question_number="1", question_type="选择题"),
        2: TaggingContext(question_text="随机掷骰子，求概率。", question_number="2", question_type="选择题"),
    }

    results = service.analyze_questions(contexts, max_workers=1)

    assert results[1].quality_status == "complete"
    assert results[2].quality_status == "complete"
    assert service.primary_calls == [1, 2, 2]


def test_low_confidence_uses_review_model_when_agreement_is_found() -> None:
    primary = _analysis(knowledge_points=["概率初步"], confidence=0.55)
    review = _analysis(knowledge_points=["概率初步"], confidence=0.9)
    service = _FakeTaggingService(primary={1: [primary]}, review={1: review})
    contexts = {
        1: TaggingContext(question_text="随机抽取一个球，求概率。", question_number="1", question_type="选择题")
    }

    results = service.analyze_questions(contexts, max_workers=1)

    assert results[1].quality_status == "complete"
    assert results[1].model_name == "doubao-main+deepseek-review"
    assert results[1].analysis is not None
    assert results[1].analysis.confidence >= 0.72
    assert service.review_calls == [1]


def test_low_confidence_without_review_stays_pending() -> None:
    service = _FakeTaggingService(primary={1: [_analysis(confidence=0.55)]})
    contexts = {
        1: TaggingContext(question_text="随机抽取一个球，求概率。", question_number="1", question_type="选择题")
    }

    results = service.analyze_questions(contexts, max_workers=1)

    assert results[1].quality_status == "low_confidence"
    assert service.review_calls == []


def test_adaptive_batches_keep_simple_questions_together_and_complex_questions_single() -> None:
    items = [
        (1, TaggingContext(question_text="计算 a^2 · a^3。", question_number="1", question_type="选择题")),
        (2, TaggingContext(question_text="随机事件概率。", question_number="2", question_type="选择题")),
        (3, TaggingContext(question_text="如图证明三角形全等。", question_number="3", question_type="解答题（证明）", has_images=True)),
        (4, TaggingContext(question_text="先化简，再求值。", question_number="4", question_type="解答题（计算）")),
        (5, TaggingContext(question_text="综合与实践：" + "阅读材料。" * 80, question_number="5", question_type="填空题")),
    ]

    batches = _adaptive_batches(items)

    assert [qid for qid, _ in batches[0]] == [1, 2]
    assert [qid for qid, _ in batches[1]] == [3]
    assert [qid for qid, _ in batches[2]] == [4]
    assert [qid for qid, _ in batches[3]] == [5]
