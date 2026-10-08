from __future__ import annotations

from pathlib import Path
import threading

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.models.tag_schema import TaggingContext
from question_bank.training_criteria import (
    ApprovedCriterionMissing,
    CriterionRequestConflict,
    CriterionReviewCommand,
    QuestionAnalysisInput,
    TrainingCriteriaDraft,
    TrainingCriterionModule,
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
        (
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
        )


def test_asset_loading_keeps_order_and_external_sqlite_on_calling_thread(tmp_path, monkeypatch):
    from question_bank.training_criteria import adapters
    from question_bank.services import file_cache
    from question_bank.services.rich_content_service import save_question_rich_content
    import json

    database = tmp_path / "bank.db"
    _seed(database)
    image_root = tmp_path / 'question_bank' / 'extracted_images'
    image_root.mkdir(parents=True)
    rich_root = tmp_path / 'question_bank' / 'rich_content'
    for qid in range(1, 9):
        (image_root / f'TEST-{qid}.png').write_bytes(f'TEST-image-{qid}'.encode())
        save_question_rich_content(qid, question_blocks=[{'kind': 'text', 'text': f'TEST-rich-{qid}'}], root=rich_root)
    with connect(database) as connection:
        connection.executemany('UPDATE questions SET has_images=1,image_paths=? WHERE id=?',
            [(json.dumps([f'question_bank/extracted_images/TEST-{qid}.png']), qid) for qid in range(1, 9)])
    scans = []
    original_scan = file_cache.os.scandir
    def scan(path):
        scans.append(Path(path))
        return original_scan(path)
    monkeypatch.setattr(file_cache.os, 'scandir', scan)
    caller = threading.get_ident()
    worker_threads = set()
    barrier = threading.Barrier(4, timeout=10)
    original_rich = adapters.load_question_rich_content

    def read_rich(*args, **kwargs):
        worker_threads.add(threading.get_ident())
        barrier.wait()
        return original_rich(*args, **kwargs)

    monkeypatch.setattr(adapters, "load_question_rich_content", read_rich)
    requested = (8, 2, 7, 3, 6, 4, 5, 1, 8)
    with connect(database) as connection:
        inputs = adapters.QuestionAnalysisInputLoader(
            db_path=database, data_root=tmp_path, external_connection=connection,
        ).load(requested, curriculum_volume_id="bnu24-math-g8-upper")
        assert connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 8
    assert [item.question_id for item in inputs] == list(requested[:-1])
    assert [item.tagging_context.question_text for item in inputs] == [f"合成题目 {qid}" for qid in requested[:-1]]
    assert all(item.tagging_context.curriculum_volume_id == "bnu24-math-g8-upper" for item in inputs)
    assert caller not in worker_threads and len(worker_threads) == 4
    assert all(len(item.images) == 1 for item in inputs)
    assert scans.count(image_root) == 1 and scans.count(rich_root) == 1
    # A later edit must be read on the next request, independently of workers.
    monkeypatch.setattr(adapters, "load_question_rich_content", original_rich)
    with connect(database) as connection:
        connection.execute("UPDATE questions SET question_text='更新后的题目' WHERE id=8")
    (image_root / 'TEST-8.png').write_bytes(b'TEST-updated-image-body')
    save_question_rich_content(8, question_blocks=[{'kind': 'text', 'text': 'TEST-updated-rich'}], root=rich_root)
    refreshed = adapters.QuestionAnalysisInputLoader(db_path=database, data_root=tmp_path).load((8,))
    assert refreshed[0].tagging_context.question_text == "更新后的题目"
    assert refreshed[0].images[0].content == b'TEST-updated-image-body'
    assert refreshed[0].rich_question_blocks[0]['text'] == 'TEST-updated-rich'
    (image_root / 'TEST-8.png').unlink()
    assert not adapters.QuestionAnalysisInputLoader(db_path=database, data_root=tmp_path).load((8,))[0].images


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


def test_content_change_blocks_freeze_without_stale_marking_on_read(
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
    assert workspace["current_version"]["status"] == "approved"
    assert module.get_version(version_id)["status"] == "approved"
    with pytest.raises(ApprovedCriterionMissing) as exc:
        module.freeze((changed,))
    assert exc.value.question_ids == (1,)

    module.propose(
        question=changed,
        draft=_draft(changed),
        source_kind="backfill",
        source_reference="analysis:synthetic:2",
        actor_ref="synthetic-job",
        reason="内容变化后重新生成",
    )
    assert module.get_version(version_id)["status"] == "stale"


def _current_evidence(database: Path):
    from question_bank.training_criteria.adapters import QuestionAnalysisInputLoader
    from question_bank.solution_evidence.contracts import QuestionSolutionEvidence
    from question_bank.solution_evidence.repository import SolutionEvidenceRepository
    from tests.training.test_solution_evidence_semantics import _evidence_payload, Resolver

    question = QuestionAnalysisInputLoader(db_path=database, data_root=database.parent).load((1,))[0]
    evidence = QuestionSolutionEvidence.from_model_dict(
        _evidence_payload(1), question_id=1,
        source_content_hash=question.criterion_source_content_hash, resolver=Resolver(),
    )
    identifier = SolutionEvidenceRepository(database).save(
        evidence, source_kind="combined_model", source_reference="TEST-current-evidence", created_by="TEST-model",
    )
    return question, evidence, identifier


def test_current_evidence_trains_without_a_parallel_model_version(tmp_path):
    from question_bank.training_criteria.analysis import training_criteria_from_solution_evidence
    from question_bank.solution_evidence.part_assessments import reading

    database = tmp_path / "TEST-current.db"
    _seed(database, 1)
    question, evidence, identifier = _current_evidence(database)
    module = TrainingCriterionModule(database)
    workspace = module.read(question)
    assert workspace["available"] is True
    assert workspace["current_version"]["version_id"] == identifier
    assert module.freeze((question,))[0]["criteria"]["points"][0]["target"] == "正确移项"
    module.propose(
        question=question, draft=training_criteria_from_solution_evidence(evidence, question=question),
        source_kind="combined_model", source_reference="TEST-model-publication", actor_ref="TEST-model", reason="TEST",
    )
    with reading(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM training_criterion_versions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM training_criterion_heads").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM question_solution_evidence_versions").fetchone()[0] == 1


def test_teacher_can_confirm_current_evidence_and_later_models_preserve_decision(tmp_path):
    from dataclasses import replace
    from question_bank.solution_evidence.repository import SolutionEvidenceRepository

    database = tmp_path / "TEST-teacher.db"
    _seed(database, 1)
    question, evidence, identifier = _current_evidence(database)
    module = TrainingCriterionModule(database)
    current = module.read(question)
    frozen = module.freeze((question,))[0]
    approved = module.review(
        CriterionReviewCommand(1, identifier, current["revision"], "approve", "TEST-teacher", "TEST-confirm"),
        question=question,
    )
    assert approved["approved_version"]["version_id"] == identifier
    first = evidence.parts[0]
    changed = replace(evidence, parts=(replace(first, evidence_points=(
        replace(first.evidence_points[0], target="模型后续建议"),)),))
    SolutionEvidenceRepository(database).save(
        changed, source_kind="combined_model", source_reference="TEST-later-model", created_by="TEST-model",
    )
    assert module.freeze((question,))[0]["criteria"]["points"][0]["target"] == "正确移项"
    assert frozen["criteria"]["points"][0]["target"] == "正确移项"


def test_teacher_edits_from_current_evidence_without_losing_frozen_parent(tmp_path):
    import copy

    database = tmp_path / "TEST-edit.db"
    _seed(database, 1)
    question, _, identifier = _current_evidence(database)
    module = TrainingCriterionModule(database)
    current = module.read(question)
    payload = copy.deepcopy(current["current_version"]["criteria"])
    payload["points"][0]["target"] = "教师修改的判定要求"
    edited = module.edit(
        question=question, criteria=payload, expected_revision=current["revision"],
        parent_version_id=identifier, request_token="a" * 32, actor_ref="TEST-teacher", reason="TEST-edit",
    )
    assert edited["current_version"]["criteria"]["points"][0]["target"] == "教师修改的判定要求"
    assert module.read(question)["available"] is True
    assert module.freeze((question,))[0]["criteria"]["points"][0]["target"] == "教师修改的判定要求"
    assert module.get_version(identifier)["criteria"]["points"][0]["target"] == "正确移项"


def test_reject_current_legacy_evidence_stays_unavailable(tmp_path):
    from dataclasses import replace
    from question_bank.solution_evidence.repository import SolutionEvidenceRepository
    from question_bank.training_criteria import solution_evidence_source_content_hash

    database = tmp_path / "TEST-reject-legacy-evidence.db"
    _seed(database, 1)
    with connect(database) as connection:
        connection.execute("UPDATE questions SET question_type='解答题' WHERE id=1")
    question, evidence, _ = _current_evidence(database)
    legacy_question = replace(
        question,
        tagging_context=replace(question.tagging_context, question_type="解答题（计算）"),
    )
    legacy_evidence = replace(
        evidence, source_content_hash=solution_evidence_source_content_hash(legacy_question),
    )
    identifier = SolutionEvidenceRepository(database).save(
        legacy_evidence, source_kind="combined_model",
        source_reference="TEST-legacy-evidence", created_by="TEST-model",
    )
    module = TrainingCriterionModule(database)
    workspace = module.read(question)
    assert workspace["current_version"]["version_id"] == identifier
    assert workspace["current_version"]["criteria"]["solution_evidence"]["version_id"] != legacy_evidence.version_id
    module.review(
        CriterionReviewCommand(1, identifier, workspace["revision"], "reject", "TEST-teacher", "invalid judgment"),
        question=question,
    )
    assert module.read(question)["available"] is False
    assert module.read(question)["state"] == "rejected"
    with pytest.raises(ApprovedCriterionMissing):
        module.freeze((question,))

    corrected = replace(evidence, parts=(replace(evidence.parts[0], evidence_points=(
        replace(evidence.parts[0].evidence_points[0], target="修正后的判定要求"),
    )),))
    SolutionEvidenceRepository(database).save(
        corrected, source_kind="combined_model",
        source_reference="TEST-corrected-evidence", created_by="TEST-model",
    )
    assert module.read(question)["available"] is True
    assert module.freeze((question,))[0]["criteria"]["points"][0]["target"] == "修正后的判定要求"
