from __future__ import annotations

import json
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

    assert workspace["available"] is True
    assert workspace["current_version"]["status"] == "approved"
    assert module.get_version(version_id)["status"] == "approved"
    confirmed = module.get_version(version_id)
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
    assert module.get_version(version_id) == confirmed
    assert module.read(changed)["approved_version"] == confirmed
    assert module.freeze((changed,))[0]["version_id"] != version_id


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


def test_saved_state_read_skips_profile_derivation_but_verify_source_keeps_it(tmp_path, monkeypatch):
    """verify_source=False 且已有可用保存版本时不再从证据推导 current_version。"""
    import question_bank.training_criteria.versioning as versioning

    database = tmp_path / "TEST-saved-read.db"
    _seed(database, 1)
    question, _, _ = _current_evidence(database)
    module = TrainingCriterionModule(database)
    proposed = _propose(module, question)

    derived: list[int] = []
    original = versioning._version_from_profile

    def spy(question_arg, profile):
        derived.append(question_arg.question_id)
        return original(question_arg, profile)

    monkeypatch.setattr(versioning, "_version_from_profile", spy)

    workspace = module.read_many((question,), verify_source=False)[1]
    assert workspace["available"] is True
    assert workspace["current_version"]["version_id"] == proposed["current_version"]["version_id"]
    assert derived == []

    # Without a usable saved criterion the profile still derives the view.
    with connect(database) as connection:
        connection.execute(
            "UPDATE training_criterion_versions SET status='superseded' WHERE question_id=1")
    workspace = module.read_many((question,), verify_source=False)[1]
    assert derived == [1]
    assert workspace["available"] is True

    # verify_source=True keeps deriving the current source for adoption.
    derived.clear()
    _propose(module, question, reference="analysis:synthetic:2")
    workspace = module.read_many((question,), verify_source=True)[1]
    assert derived == [1]
    assert workspace["available"] is True


def _changed_saved_draft(question, label):
    payload = _draft(question).to_dict()
    payload["points"][0]["target"] = label
    return TrainingCriteriaDraft.from_model_dict({k: payload[k] for k in ("schema_version", "question_id", "points", "auxiliary_rules", "rationale", "confidence")}, question=question)


def _saved_criterion_rows(path):
    with connect(path) as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM training_criterion_versions ORDER BY version_number")]


def test_successful_model_redos_replace_one_unadopted_result_and_failed_redos_preserve_it(tmp_path, monkeypatch):
    import question_bank.training_criteria.versioning as versioning
    path = tmp_path / "TEST-one-criterion.db"; _seed(path)
    module = TrainingCriterionModule(path, data_root=tmp_path)
    question = _question(1)
    first = _propose(module, question)
    identifier = first["current_version"]["version_id"]
    second = module.propose(question=question, draft=_changed_saved_draft(question, "TEST-second-goal"),
        source_kind="backfill", source_reference="TEST-redo-2", actor_ref="TEST-model", reason="TEST-redo",
        expected_revision=first["revision"])
    assert second["current_version"]["version_id"] == identifier
    assert second["current_version"]["criteria"]["points"][0]["target"] == "TEST-second-goal"
    assert len(_saved_criterion_rows(path)) == 1
    before = _saved_criterion_rows(path)
    invalid_question = _question(1, question_type="选择题", answer="A")
    with pytest.raises(versioning.CriterionQualityError):
        module.propose(question=invalid_question, draft=_draft(invalid_question), source_kind="backfill",
            source_reference="TEST-invalid-redo", actor_ref="TEST-model", reason="TEST-invalid")
    assert _saved_criterion_rows(path) == before
    assert module.read(question)["revision"] == second["revision"]
    monkeypatch.setattr(versioning, "_event", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("TEST-event-failure")))
    with pytest.raises(OSError, match="TEST-event-failure"):
        module.propose(question=question, draft=_changed_saved_draft(question, "TEST-third-goal"),
            source_kind="backfill", source_reference="TEST-redo-failed-save", actor_ref="TEST-model", reason="TEST-failure")
    assert _saved_criterion_rows(path) == before
    assert module.read(question)["revision"] == second["revision"]


def test_confirmed_criterion_and_one_pending_draft_have_distinct_purposes(tmp_path):
    path = tmp_path / "TEST-confirmed-criterion.db"; _seed(path)
    module = TrainingCriterionModule(path, data_root=tmp_path); question = _question(1)
    first = _propose(module, question); identifier = first["current_version"]["version_id"]
    module.review(CriterionReviewCommand(1, identifier, first["revision"], "approve", "TEST-teacher", "TEST-approved"), question=question)
    adopted = module.get_version(identifier)
    second = module.propose(question=question, draft=_changed_saved_draft(question, "TEST-pending-2"),
        source_kind="backfill", source_reference="TEST-pending-2", actor_ref="TEST-model", reason="TEST-pending")
    pending_id = second["current_version"]["version_id"]
    third = module.propose(question=question, draft=_changed_saved_draft(question, "TEST-pending-3"),
        source_kind="backfill", source_reference="TEST-pending-3", actor_ref="TEST-model", reason="TEST-pending")
    assert pending_id != identifier
    assert third["current_version"]["version_id"] == pending_id
    assert third["approved_version"]["version_id"] == identifier
    assert module.get_version(identifier) == adopted
    assert len(_saved_criterion_rows(path)) == 2
    with connect(path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("reference_kind", ["foreign_key", "exam_snapshot"])
def test_historical_criterion_reference_remains_unchanged_when_model_redoes(tmp_path, reference_kind):
    path = tmp_path / "TEST-frozen-criterion.db"; _seed(path)
    module = TrainingCriterionModule(path, data_root=tmp_path); question = _question(1)
    first = _propose(module, question); identifier = first["current_version"]["version_id"]
    frozen = module.get_version(identifier)
    snapshot = tmp_path / "config" / "uploaded" / "evidence_snapshot-TEST-exam.json"
    if reference_kind == "foreign_key":
        with connect(path) as connection:
            connection.execute("CREATE TABLE TEST_frozen_criteria(reference TEXT REFERENCES training_criterion_versions(version_id),snapshot_json TEXT)")
            connection.execute("INSERT INTO TEST_frozen_criteria VALUES(?,?)", (identifier, json.dumps(frozen)))
    else:
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text(json.dumps({"questions": {"Q1": {"source_evidence_version_id": identifier, "criteria": frozen}}}), encoding="utf-8")
        original_snapshot = snapshot.read_bytes()
    second = module.propose(question=question, draft=_changed_saved_draft(question, "TEST-new-after-history"),
        source_kind="backfill", source_reference="TEST-after-history", actor_ref="TEST-model", reason="TEST-redo")
    assert second["current_version"]["version_id"] != identifier
    assert module.get_version(identifier) == frozen
    assert len(_saved_criterion_rows(path)) == 2
    if reference_kind == "exam_snapshot":
        assert snapshot.read_bytes() == original_snapshot
    with connect(path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def _next_model_evidence(evidence, label):
    from dataclasses import replace
    part = evidence.parts[0]
    return replace(evidence, parts=(replace(part, evidence_points=(replace(part.evidence_points[0], target=label),)),))


def test_unadopted_model_evidence_is_one_current_result_and_save_failure_rolls_back(tmp_path, monkeypatch):
    from question_bank.solution_evidence.repository import SolutionEvidenceRepository
    from question_bank.solution_evidence import knowledge_links
    path = tmp_path / "TEST-one-model-evidence.db"; _seed(path, 1)
    _question_input, original, identifier = _current_evidence(path)
    repository = SolutionEvidenceRepository(path, data_root=tmp_path)
    second = _next_model_evidence(original, "TEST-second-model-result")
    updated_id = repository.save(second, source_kind="combined_model", source_reference="TEST-second-model", created_by="TEST-model")
    assert updated_id == second.version_id
    assert updated_id != identifier
    with connect(path) as connection:
        before = [dict(row) for row in connection.execute("SELECT * FROM question_solution_evidence_versions")]
        assert len(before) == 1
        assert before[0]["evidence_version_id"] == updated_id
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    monkeypatch.setattr(knowledge_links, "project_embedded_links", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("TEST-projection-failure")))
    with pytest.raises(OSError, match="TEST-projection-failure"):
        repository.save(_next_model_evidence(original, "TEST-failed-third"), source_kind="combined_model",
            source_reference="TEST-failed-model-save", created_by="TEST-model")
    with connect(path) as connection:
        assert [dict(row) for row in connection.execute("SELECT * FROM question_solution_evidence_versions")] == before
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("reference_kind", ["foreign_key", "exam_snapshot", "checkpoint", "request_receipt"])
def test_model_evidence_used_by_an_independent_task_is_preserved(tmp_path, reference_kind):
    from question_bank.solution_evidence.repository import SolutionEvidenceRepository
    path = tmp_path / "TEST-retained-model-evidence.db"; _seed(path, 1)
    _question_input, evidence, identifier = _current_evidence(path)
    if reference_kind == "foreign_key":
        with connect(path) as connection:
            connection.execute("CREATE TABLE TEST_frozen_evidence(reference TEXT REFERENCES question_solution_evidence_versions(evidence_version_id))")
            connection.execute("INSERT INTO TEST_frozen_evidence VALUES(?)", (identifier,))
    elif reference_kind == "request_receipt":
        with connect(path) as connection:
            connection.execute("INSERT INTO question_analysis_operations(operation_id,input_fingerprint,contract_version,requested_projection,status) VALUES('TEST-request-operation',?,'combined-v2','both','succeeded')", ("a" * 64,))
            connection.execute("UPDATE question_solution_evidence_versions SET source_reference='analysis:TEST-request-operation:1' WHERE evidence_version_id=?", (identifier,))
    else:
        filename = "evidence_snapshot-TEST-exam.json" if reference_kind == "exam_snapshot" else "deferred_question_analysis_TEST-checkpoint.json"
        snapshot = tmp_path / "config" / "uploaded" / filename
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text(json.dumps({"evidence_version_id": identifier, "evidence": evidence.to_dict()}), encoding="utf-8")
        original_snapshot = snapshot.read_bytes()
    with connect(path) as connection:
        before = dict(connection.execute("SELECT * FROM question_solution_evidence_versions WHERE evidence_version_id=?", (identifier,)).fetchone())
    repository = SolutionEvidenceRepository(path, data_root=tmp_path)
    changed = _next_model_evidence(evidence, "TEST-new-model-result")
    new_id = repository.save(changed, source_kind="combined_model", source_reference="TEST-next-model", created_by="TEST-model")
    assert new_id != identifier
    with connect(path) as connection:
        assert dict(connection.execute("SELECT * FROM question_solution_evidence_versions WHERE evidence_version_id=?", (identifier,)).fetchone()) == before
        assert connection.execute("SELECT count(*) FROM question_solution_evidence_versions").fetchone()[0] == 2
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    if reference_kind in {"exam_snapshot", "checkpoint"}:
        assert snapshot.read_bytes() == original_snapshot


@pytest.mark.parametrize("change_source", [False, True])
def test_embedded_model_redos_keep_one_pending_result_and_rollback_both_stores(tmp_path, monkeypatch, change_source):
    from dataclasses import replace
    import question_bank.training_criteria.versioning as versioning
    from question_bank.training_criteria.analysis import training_criteria_from_solution_evidence
    path = tmp_path / "TEST-embedded-pending.db"; _seed(path, 1)
    question, original, adopted_id = _current_evidence(path)
    module = TrainingCriterionModule(path, data_root=tmp_path)
    initial = module.read(question)
    module.review(CriterionReviewCommand(1, adopted_id, initial["revision"], "approve", "TEST-teacher", "TEST-approved"), question=question)
    adopted = module.get_version(adopted_id)
    def propose(label, reference):
        evidence = replace(_next_model_evidence(original, label), source_content_hash=question.criterion_source_content_hash)
        return module.propose(question=question, draft=training_criteria_from_solution_evidence(evidence, question=question),
            source_kind="combined_model", source_reference=reference, actor_ref="TEST-model", reason="TEST-redo")
    second = propose("TEST-pending-second", "TEST-embedded-2")
    pending_id = second["current_version"]["version_id"]
    if change_source:
        question = replace(question, tagging_context=replace(question.tagging_context, question_text="TEST-new-question-material"))
    third = propose("TEST-pending-third", "TEST-embedded-3")
    assert third["current_version"]["version_id"] == pending_id
    assert third["approved_version"]["version_id"] == adopted_id
    assert module.get_version(adopted_id) == adopted
    with connect(path) as connection:
        before_evidence = [dict(row) for row in connection.execute("SELECT * FROM question_solution_evidence_versions ORDER BY rowid")]
        before_criteria = [dict(row) for row in connection.execute("SELECT * FROM training_criterion_versions ORDER BY rowid")]
        before_head = dict(connection.execute("SELECT * FROM training_criterion_heads WHERE question_id=1").fetchone())
        assert len(before_evidence) == len(before_criteria) == 2
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    monkeypatch.setattr(versioning, "_event", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("TEST-coupled-save-failure")))
    with pytest.raises(OSError, match="TEST-coupled-save-failure"):
        propose("TEST-failed-fourth", "TEST-embedded-failed")
    with connect(path) as connection:
        assert [dict(row) for row in connection.execute("SELECT * FROM question_solution_evidence_versions ORDER BY rowid")] == before_evidence
        assert [dict(row) for row in connection.execute("SELECT * FROM training_criterion_versions ORDER BY rowid")] == before_criteria
        assert dict(connection.execute("SELECT * FROM training_criterion_heads WHERE question_id=1").fetchone()) == before_head
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
