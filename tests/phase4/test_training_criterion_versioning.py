from __future__ import annotations

import json
from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.models.tag_schema import TaggingContext
from question_bank.training_criteria import (
    ApprovedCriterionMissing,
    CriterionRequestConflict,
    CriterionReviewCommand,
    CriterionRevisionConflict,
    QuestionAnalysisInput,
    TrainingCriteriaDraft,
    TrainingCriterionModule,
    evaluate_criterion_quality,
)


FIXTURE = Path(__file__).parent / "fixtures" / "p4_00_gold_set.json"


def _seed(database: Path, count: int = 8) -> None:
    initialize_database(database)
    with connect(database) as connection:
        paper_id = connection.execute(
            """
            INSERT INTO papers (title, import_status)
            VALUES ('P4-10 合成试卷', 'completed')
            """
        ).lastrowid
        connection.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type,
                question_text, answer_text
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    index,
                    paper_id,
                    str(index),
                    "计算题",
                    f"合成题目 {index}",
                    f"合成答案 {index}",
                )
                for index in range(1, count + 1)
            ),
        ),


def _question(
    question_id: int,
    *,
    question_type: str = "计算题",
    text: str = "解方程 x+1=2。",
    answer: str = "x=1",
    has_images: bool = False,
) -> QuestionAnalysisInput:
    return QuestionAnalysisInput(
        question_id=question_id,
        tagging_context=TaggingContext(
            question_text=text,
            answer_text=answer,
            question_type=question_type,
            question_number=str(question_id),
            has_images=has_images,
        ),
    )


def _draft(
    question: QuestionAnalysisInput,
    *,
    points: list[dict[str, object]] | None = None,
) -> TrainingCriteriaDraft:
    return TrainingCriteriaDraft.from_model_dict(
        {
            "schema_version": "training-criteria-draft-v1",
            "question_id": question.question_id,
            "points": points
            or [
                {
                    "point_id": "p-relation",
                    "target": "建立等量关系",
                    "observable_evidence": "列出正确方程",
                    "equivalent_rules": [],
                    "counterexamples": [],
                },
                {
                    "point_id": "p-answer",
                    "target": "得到结论",
                    "observable_evidence": "写出 x=1",
                    "equivalent_rules": ["1=x"],
                    "counterexamples": [],
                },
            ],
            "auxiliary_rules": ["书写清楚但不计入分母"],
            "rationale": "P4-10 合成判定点",
            "confidence": 0.9,
        },
        question=question,
    )


def _propose(
    module: TrainingCriterionModule,
    question: QuestionAnalysisInput,
    *,
    reference: str = "analysis:synthetic:1",
    expected_revision: int | None = None,
) -> dict[str, object]:
    return module.propose(
        question=question,
        draft=_draft(question),
        source_kind="backfill",
        source_reference=reference,
        actor_ref="synthetic-job",
        reason="合成回填",
        expected_revision=expected_revision,
    )


def test_five_question_types_pass_the_frozen_quality_gate() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    labels = {
        "single_choice": ("选择题", "选择正确选项。", "C"),
        "fill_blank": (
            "填空题",
            "第一空____，第二空____。",
            "第一空-2，第二空2",
        ),
        "calculation": ("计算题", "解方程。", "x=1"),
        "proof": ("证明题", "证明两三角形全等。", "见证明"),
        "construction": ("作图题", "按要求作图。", "见图"),
    }

    for index, sample in enumerate(
        fixture["criterion_samples"],
        start=1,
    ):
        question_type, text, answer = labels[sample["question_type"]]
        question = _question(
            index,
            question_type=question_type,
            text=text,
            answer=answer,
        )
        draft = _draft(question, points=sample["points"])
        result = evaluate_criterion_quality(question, draft)
        assert result.passed, (sample["id"], result.codes)


def test_quality_gate_trusts_model_granularity_but_requires_actual_image() -> None:
    calculation = _question(1)
    proof = _question(2, question_type="证明题")
    image = _question(3, question_type="作图题", has_images=True)

    calculation_result = evaluate_criterion_quality(
        calculation,
        _draft(
            calculation,
            points=[
                {
                    "point_id": "p-answer",
                    "target": "完成解答",
                    "observable_evidence": "答案正确",
                    "equivalent_rules": [],
                    "counterexamples": [],
                }
            ],
        ),
    )
    proof_result = evaluate_criterion_quality(
        proof,
        _draft(
            proof,
            points=[
                {
                    "point_id": "p-proof",
                    "target": "证明结论",
                    "observable_evidence": "写出证明",
                    "equivalent_rules": [],
                    "counterexamples": [],
                }
            ],
        ),
    )
    image_result = evaluate_criterion_quality(
        image,
        _draft(
            image,
            points=[
                {
                    "point_id": "p-line",
                    "target": "作出垂线",
                    "observable_evidence": "图中有垂线",
                    "equivalent_rules": [],
                    "counterexamples": [],
                },
                {
                    "point_id": "p-mark",
                    "target": "标记关系",
                    "observable_evidence": "标记垂直",
                    "equivalent_rules": [],
                    "counterexamples": [],
                },
            ],
        ),
    )

    assert "calculation_process_missing" not in calculation_result.codes
    assert "proof_obligations_incomplete" not in proof_result.codes
    assert "missing_actual_image" in image_result.codes
    assert image_result.passed is True


def test_missing_actual_image_does_not_block_teacher_approval(tmp_path: Path) -> None:
    database = tmp_path / "question-bank.db"
    _seed(database)
    module = TrainingCriterionModule(database)
    question = _question(1, question_type="作图题", has_images=True)

    proposed = _propose(module, question)
    version = proposed["current_version"]
    assert isinstance(version, dict)
    assert version["quality_status"] == "passed"
    assert "missing_actual_image" in version["quality_codes"]

    with connect(database) as connection:
        connection.execute(
            """
            UPDATE training_criterion_versions
            SET quality_status = 'failed'
            WHERE version_id = ?
            """,
            (version["version_id"],),
        )

    approved = module.review(
        CriterionReviewCommand(
            question_id=1,
            version_id=version["version_id"],
            expected_revision=proposed["revision"],
            action="approve",
            actor_ref="local_teacher",
            reason="判定点够用，缺图只提醒",
        ),
        question=question,
    )
    current = approved["current_version"]
    assert isinstance(current, dict)
    assert approved["available"] is True
    assert current["status"] == "approved"


def test_teacher_review_creates_immutable_versions_and_moves_only_head(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed(database)
    module = TrainingCriterionModule(database)
    question = _question(1)

    proposed = _propose(module, question)
    version_one = proposed["current_version"]
    assert isinstance(version_one, dict)
    approved = module.review(
        CriterionReviewCommand(
            question_id=1,
            version_id=version_one["version_id"],
            expected_revision=proposed["revision"],
            action="approve",
            actor_ref="local_teacher",
            reason="核对通过",
        ),
        question=question,
    )
    edited = module.edit(
        question=question,
        criteria={
            **_draft(question).to_dict(),
            "points": [
                *_draft(question).to_dict()["points"],
                {
                    "point_id": "p-check",
                    "target": "核对答案",
                    "observable_evidence": "代回原方程成立",
                    "equivalent_rules": [],
                    "counterexamples": [],
                },
            ],
        },
        expected_revision=approved["revision"],
        parent_version_id=version_one["version_id"],
        request_token="1" * 32,
        actor_ref="local_teacher",
        reason="补充核对义务",
    )

    assert approved["available"] is True
    assert edited["available"] is True
    assert edited["approved_version"]["version_id"] == version_one["version_id"]
    assert edited["current_version"]["version_id"] != version_one["version_id"]
    original = module.get_version(version_one["version_id"])
    assert original["criteria"] == version_one["criteria"]
    assert original["status"] == "approved"


def test_approving_new_version_supersedes_old_but_keeps_old_readable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed(database)
    module = TrainingCriterionModule(database)
    question = _question(1)
    first = _propose(module, question)
    first_id = first["current_version"]["version_id"]
    approved = module.review(
        CriterionReviewCommand(
            1,
            first_id,
            first["revision"],
            "approve",
            "local_teacher",
            "首版通过",
        ),
        question=question,
    )
    second = module.edit(
        question=question,
        criteria=_draft(question).to_dict(),
        expected_revision=approved["revision"],
        parent_version_id=first_id,
        request_token="2" * 32,
        actor_ref="local_teacher",
        reason="形成新版",
    )
    second_id = second["current_version"]["version_id"]
    final = module.review(
        CriterionReviewCommand(
            1,
            second_id,
            second["revision"],
            "approve",
            "local_teacher",
            "新版通过",
        ),
        question=question,
    )

    assert final["approved_version"]["version_id"] == second_id
    assert module.get_version(first_id)["status"] == "superseded"
    assert module.get_version(first_id)["criteria"] == first[
        "current_version"
    ]["criteria"]


def test_content_change_marks_approved_head_stale_and_blocks_freeze(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed(database)
    module = TrainingCriterionModule(database)
    original = _question(1)
    proposed = _propose(module, original)
    version_id = proposed["current_version"]["version_id"]
    module.review(
        CriterionReviewCommand(
            1,
            version_id,
            proposed["revision"],
            "approve",
            "local_teacher",
            "通过",
        ),
        question=original,
    )

    changed = _question(1, text="题干已经改变：解方程 x+2=3。")
    workspace = module.read(changed)

    assert workspace["available"] is False
    assert workspace["current_version"]["status"] == "stale"
    assert module.get_version(version_id)["status"] == "stale"
    with pytest.raises(ApprovedCriterionMissing) as exc:
        module.freeze((changed,))
    assert exc.value.question_ids == (1,)


def test_revision_and_request_tokens_prevent_duplicate_or_stale_writes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed(database)
    module = TrainingCriterionModule(database)
    question = _question(1)
    first = _propose(module, question)
    duplicate = _propose(module, question)

    assert duplicate == first
    with pytest.raises(CriterionRequestConflict):
        module.propose(
            question=question,
            draft=_draft(
                question,
                points=[
                    {
                        "point_id": "p-other",
                        "target": "建立方程",
                        "observable_evidence": "列出等量关系",
                        "equivalent_rules": [],
                        "counterexamples": [],
                    },
                    {
                        "point_id": "p-answer",
                        "target": "得到答案",
                        "observable_evidence": "x=1",
                        "equivalent_rules": [],
                        "counterexamples": [],
                    },
                ],
            ),
            source_kind="backfill",
            source_reference="analysis:synthetic:1",
            actor_ref="synthetic-job",
            reason="冲突内容",
        )
    with pytest.raises(CriterionRevisionConflict):
        module.edit(
            question=question,
            criteria=_draft(question).to_dict(),
            expected_revision=0,
            parent_version_id=first["current_version"]["version_id"],
            request_token="3" * 32,
            actor_ref="local_teacher",
            reason="过期页面修改",
        )


def test_structurally_valid_atomic_calculation_can_be_approved(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed(database)
    module = TrainingCriterionModule(database)
    question = _question(1)
    workspace = module.propose(
        question=question,
        draft=_draft(
            question,
            points=[
                {
                    "point_id": "p-answer",
                    "target": "完成解答",
                    "observable_evidence": "答案正确",
                    "equivalent_rules": [],
                    "counterexamples": [],
                }
            ],
        ),
        source_kind="backfill",
        source_reference="bad-quality",
        actor_ref="synthetic-job",
        reason="质量门样本",
    )
    version_id = workspace["current_version"]["version_id"]
    command = CriterionReviewCommand(
        1,
        version_id,
        workspace["revision"],
        "approve",
        "local_teacher",
        "错误批准",
    )

    approved = module.review(command, question=question)

    assert approved["current_version"]["status"] == "approved"
    assert approved["available"] is True


def test_backfill_run_is_explicit_idempotent_and_recovers_interruption(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed(database)
    module = TrainingCriterionModule(database)

    run, created = module.create_backfill_run(
        question_ids=(1, 2),
        request_token="a" * 32,
    )
    duplicate, duplicate_created = module.create_backfill_run(
        question_ids=(1, 2),
        request_token="a" * 32,
    )
    claimed = module.claim_backfill(run["run_id"])
    recovered_count = module.recover_interrupted_backfills()
    recovered = module.get_backfill_run(run["run_id"])

    assert created is True
    assert duplicate_created is False
    assert duplicate == run
    assert claimed == (1, 2)
    assert recovered_count == 1
    assert recovered["status"] == "failed"
    assert {
        item["error_category"] for item in recovered["items"]
    } == {"interrupted"}
    with pytest.raises(CriterionRequestConflict):
        module.create_backfill_run(
            question_ids=(1, 3),
            request_token="a" * 32,
        )


def test_backfill_partial_completion_preserves_each_item_state(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed(database)
    module = TrainingCriterionModule(database)
    run, _ = module.create_backfill_run(
        question_ids=(1, 2, 3),
        request_token="b" * 32,
    )
    module.claim_backfill(run["run_id"])
    module.finish_backfill_item(
        run_id=run["run_id"],
        question_id=1,
        status="succeeded",
        version_id=None,
    )
    module.finish_backfill_item(
        run_id=run["run_id"],
        question_id=2,
        status="failed",
        error_category="quality",
    )
    module.finish_backfill_item(
        run_id=run["run_id"],
        question_id=3,
        status="cancelled",
        error_category="cancelled",
    )

    completed = module.complete_backfill(run["run_id"])

    assert completed["status"] == "partial"
    assert [item["status"] for item in completed["items"]] == [
        "succeeded",
        "failed",
        "cancelled",
    ]
