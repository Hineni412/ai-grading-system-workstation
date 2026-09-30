from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import pytest
from docx import Document

from backend.config_generation.targeted import (
    merge_targeted_failure_draft,
    merge_targeted_regeneration,
)
from backend.config_workspace.publish import (
    load_editor_config,
    publish_generated_config,
)
from backend.config_workspace.sources import (
    ConfigSourceRecord,
    ConfigSourceService,
)
from backend.jobs.config_generation import (
    run_config_generation_job,
    stage_config_source_generation_input,
)
from backend.jobs.manager import JobCancellationRequested
from backend.repositories.grading_database import open_grading_repositories
from question_bank.database.schema import initialize_database

from question_id_contract import canonical_parent_id

from tests.test_config_generation_job import (
    _deferred_combined_item,
    _DeferredProtocol,
    _DeferredProtocolResponse,
    _DeferredTaggingService,
    _job_context,
    _valid_config_payload,
)


def _qnum(question_ref: str) -> int:
    canonical = canonical_parent_id(question_ref)
    assert canonical is not None
    return int(canonical[1:])


def _regenerated_question(
    question_id: str,
    *,
    part_ids: tuple[str, ...],
    steps_per_part: int = 1,
) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "question_type": "comprehensive",
        "max_score": 0,
        "parts": [
            {
                "part_id": part_id,
                "part_score": 0,
                "response_mode": "short_answer_points",
                "steps": [
                    {
                        "step_id": f"{part_id}-S{index + 1}",
                        "step_score": 0,
                        "core_goal": "answer",
                        "required_elements": ["x"],
                        "allow_alternative_methods": True,
                    }
                    for index in range(steps_per_part)
                ],
            }
            for part_id in part_ids
        ],
    }


def _regenerated_structure(
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "rubric": {"exam_title": "Atomic Exam", "questions": questions},
        "answer_key": {
            "questions": [
                {
                    "question_id": question["question_id"],
                    "canonical_answer": "x",
                    "accepted_forms": ["x"],
                    "method_variants": [],
                    "parts": [
                        {
                            "part_id": part["part_id"],
                            "answer": "x",
                            "analysis": "",
                            "step_milestones": [],
                        }
                        for part in question["parts"]
                    ],
                }
                for question in questions
            ]
        },
        "meta": {},
    }


def _questions_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(question["question_id"]): question
        for question in payload["rubric"]["questions"]
    }


def _answers_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(question["question_id"]): question
        for question in payload["answer_key"]["questions"]
    }


def test_merge_replaces_only_targeted_question_and_preserves_scores() -> None:
    existing = _valid_config_payload()
    regenerated = _regenerated_structure(
        [
            _regenerated_question(
                "Q1",
                part_ids=("Q1-P1",),
                steps_per_part=2,
            )
        ]
    )
    merged = merge_targeted_regeneration(
        existing_payload=existing,
        regenerated_structure=regenerated,
        targeted_question_ids=["Q1"],
        targeted_source_refs=["Q1"],
    )

    merged_questions = _questions_by_id(merged)
    assert set(merged_questions) == set(_questions_by_id(existing))
    untouched = {qid for qid in merged_questions if qid != "Q1"}
    assert untouched == {"Q2", "Q3", "Q4", "Q5", "Q6"}
    for qid in untouched:
        assert merged_questions[qid] == _questions_by_id(existing)[qid]
        assert _answers_by_id(merged)[qid] == _answers_by_id(existing)[qid]

    targeted = merged_questions["Q1"]
    assert targeted["max_score"] == 17
    assert targeted["parts"][0]["part_score"] == 17
    step_total = sum(
        float(step["step_score"]) for step in targeted["parts"][0]["steps"]
    )
    assert step_total == pytest.approx(17.0)
    assert merged["rubric"]["total_score"] == 100
    assert _answers_by_id(merged)["Q1"]["canonical_answer"] == "x"


def test_merge_reallocates_question_total_when_part_set_changed() -> None:
    existing = _valid_config_payload()
    regenerated = _regenerated_structure(
        [
            _regenerated_question(
                "Q1",
                part_ids=("Q1-A", "Q1-B"),
                steps_per_part=1,
            )
        ]
    )
    merged = merge_targeted_regeneration(
        existing_payload=existing,
        regenerated_structure=regenerated,
        targeted_question_ids=["Q1"],
        targeted_source_refs=["Q1"],
    )

    targeted = _questions_by_id(merged)["Q1"]
    assert targeted["max_score"] == 17
    part_scores = [
        float(part["part_score"]) for part in targeted["parts"]
    ]
    assert sum(part_scores) == pytest.approx(17.0)
    for part in targeted["parts"]:
        step_total = sum(
            float(step["step_score"]) for step in part["steps"]
        )
        assert step_total == pytest.approx(float(part["part_score"]))
    assert merged["rubric"]["total_score"] == 100


def test_merge_rejects_unaligned_question_ids() -> None:
    existing = _valid_config_payload()
    with pytest.raises(ValueError, match="无法解析"):
        merge_targeted_regeneration(
            existing_payload=existing,
            regenerated_structure=_regenerated_structure([]),
            targeted_question_ids=["不是题号"],
            targeted_source_refs=["不是题号"],
        )
    with pytest.raises(ValueError, match="缺少题目"):
        merge_targeted_regeneration(
            existing_payload=existing,
            regenerated_structure=_regenerated_structure([]),
            targeted_question_ids=["Q1"],
            targeted_source_refs=["Q1"],
        )
    with pytest.raises(ValueError, match="无法对齐"):
        merge_targeted_regeneration(
            existing_payload=existing,
            regenerated_structure=_regenerated_structure(
                [_regenerated_question("Q9", part_ids=("Q9-P1",))]
            ),
            targeted_question_ids=["Q1"],
            targeted_source_refs=["Q1"],
        )
    with pytest.raises(ValueError, match="缺少题目"):
        merge_targeted_regeneration(
            existing_payload=existing,
            regenerated_structure=_regenerated_structure(
                [_regenerated_question("Q9", part_ids=("Q9-P1",))]
            ),
            targeted_question_ids=["Q9"],
            targeted_source_refs=["Q9"],
        )


def test_merge_failure_draft_never_touches_published_questions() -> None:
    existing = _valid_config_payload()
    failure_draft = {
        "rubric": {"questions": [{"question_id": "Q1", "bogus": True}]},
        "answer_key": {"questions": []},
        "meta": {
            "question_states": [{"question_id": "Q1", "state": "failed"}],
            "failed_question_ids": ["Q1"],
            "failed_batches": [
                {"batch_id": "b1", "question_ids": ["Q1"], "status": "failed"}
            ],
            "uncertain_question_ids": [],
            "needs_teacher_resolution": True,
        },
    }
    merged = merge_targeted_failure_draft(
        existing_payload=existing,
        failure_draft=failure_draft,
        targeted_source_refs=["Q1"],
    )
    assert merged["rubric"] == existing["rubric"]
    assert merged["answer_key"] == existing["answer_key"]
    assert merged["meta"]["failed_question_ids"] == ["Q1"]
    assert merged["meta"]["needs_teacher_resolution"] is True


def _docx_bytes(lines: list[str]) -> bytes:
    document = Document()
    for line in lines:
        document.add_paragraph(line)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


async def _chunks(content: bytes):
    yield content


def _two_question_source(
    tmp_path: Path,
    session_id: int,
) -> tuple[ConfigSourceService, ConfigSourceRecord]:
    service = ConfigSourceService(tmp_path / "uploaded")
    content = _docx_bytes(
        [
            "1. 解方程 x+1=2。",
            "2. 解方程 2x=4。",
            "参考答案",
            "1. x=1",
            "2. x=2",
        ]
    )
    record = asyncio.run(
        service.stage_and_parse(
            session_id=session_id,
            filename="七年级下册测试卷.docx",
            chunks=_chunks(content),
        )
    )
    assert len(record.questions) == 2
    return service, record


def _published_db(tmp_path: Path) -> tuple[Any, int, tuple[str, str]]:
    databases_dir = tmp_path / "databases"
    databases_dir.mkdir(parents=True, exist_ok=True)
    db = open_grading_repositories(databases_dir / "grading.db")
    db.initialize()
    initial_dir = tmp_path / "initial"
    initial_dir.mkdir(parents=True, exist_ok=True)
    publication = publish_generated_config(
        initial_dir,
        _valid_config_payload(),
        token="0" * 32,
    )
    session_id = db.sessions.create_grading_session(
        "Targeted Exam",
        str(publication.rubric_path),
        str(publication.answer_key_path),
    )
    return (
        db,
        session_id,
        (str(publication.rubric_path), str(publication.answer_key_path)),
    )


def _bank_path(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "databases" / "question_bank.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    initialize_database(path)
    return path


def _decisions(source: ConfigSourceRecord) -> list[dict[str, Any]]:
    return [
        {
            "question_id": question.question_id,
            "question_type": "calculation",
            "excluded": False,
        }
        for question in source.questions
    ]


def _stage_targeted_input(
    upload_dir: Path,
    source: ConfigSourceRecord,
    session_id: int,
    expected_paths: tuple[str, str],
    existing_payload: dict[str, Any],
    expected_revision: str,
    targeted_refs: list[str],
) -> str:
    return stage_config_source_generation_input(
        upload_dir,
        session_id=session_id,
        expected_rubric_path=expected_paths[0],
        expected_answer_key_path=expected_paths[1],
        generation_mode="batched",
        source_id=source.source_id,
        source_revision=source.source_revision,
        decisions=_decisions(source),
        sync_to_question_bank=True,
        curriculum_volume_id="bnu24-math-g7-upper",
        existing_payload=existing_payload,
        regenerate_question_ids=targeted_refs,
        expected_revision=expected_revision,
    )


def _targeted_payload(
    session_id: int,
    input_id: str,
    source: ConfigSourceRecord,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "mode": "regenerate_questions",
        "generation_mode": "batched",
        "input_id": input_id,
        "source_id": source.source_id,
        "source_revision": source.source_revision,
        "sync_to_question_bank": True,
    }


class _FailingDeferredProtocol(_DeferredProtocol):
    def __init__(self, fail_question_id: int) -> None:
        super().__init__()
        self.fail_question_id = fail_question_id

    def responses(self, **kwargs: Any) -> _DeferredProtocolResponse:
        self.calls.append(kwargs)
        prompt = kwargs["kwargs"]["input"][1]["content"][0]["text"]
        questions = json.loads(prompt)["questions"]
        results = [
            {"question_id": int(item["question_id"]), "broken": True}
            if int(item["question_id"]) == self.fail_question_id
            else _deferred_combined_item(int(item["question_id"]))
            for item in questions
        ]
        response = _DeferredProtocolResponse([])
        response.output_text = json.dumps(
            {"results": results}, ensure_ascii=False
        )
        return response


def _seed_prior_analysis(
    tmp_path: Path,
    db: Any,
    session_id: int,
    source: ConfigSourceRecord,
    expected_paths: tuple[str, str],
) -> None:
    """Run a failed first generation so a union-scope artifact exists."""

    input_id = stage_config_source_generation_input(
        tmp_path / "uploaded",
        session_id=session_id,
        expected_rubric_path=expected_paths[0],
        expected_answer_key_path=expected_paths[1],
        generation_mode="batched",
        source_id=source.source_id,
        source_revision=source.source_revision,
        decisions=_decisions(source),
        sync_to_question_bank=True,
        curriculum_volume_id="bnu24-math-g7-upper",
    )
    context, store = _job_context(
        db.db_path,
        {
            "session_id": session_id,
            "mode": "generate",
            "generation_mode": "batched",
            "input_id": input_id,
            "source_id": source.source_id,
            "source_revision": source.source_revision,
            "sync_to_question_bank": True,
        },
    )

    second_qid = _qnum(source.questions[1].question_id)
    protocol = _FailingDeferredProtocol(second_qid)
    result = run_config_generation_job(
        context=context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        data_root=tmp_path / "data",
        question_bank_db_path=_bank_path(tmp_path),
        tagging_ai_service_factory=lambda: _DeferredTaggingService(protocol),
        taxonomy_governance=object(),
        question_bank_intake_runner=lambda **kwargs: {
            "outcome": "complete",
            "imported_count": 1,
            "tagged_count": 1,
            "criteria_count": 1,
            "failed_count": 0,
        },
    )
    assert result["outcome"] == "partial"
    store.finish(context.job_id, "succeeded", result=result)


def test_targeted_regeneration_publishes_only_selected_question(
    tmp_path: Path,
) -> None:
    db, session_id, old_paths = _published_db(tmp_path)
    _source_service, source = _two_question_source(tmp_path, session_id)
    _seed_prior_analysis(tmp_path, db, session_id, source, old_paths)

    before = load_editor_config(db, session_id)
    staged = _stage_targeted_input(
        tmp_path / "uploaded",
        source,
        session_id,
        old_paths,
        before.payload,
        before.revision,
        [source.questions[0].question_id],
    )
    context, _store = _job_context(
        db.db_path,
        _targeted_payload(session_id, staged, source),
    )
    protocol = _DeferredProtocol()
    intake_calls: list[dict[str, Any]] = []

    def intake_runner(**kwargs: Any) -> dict[str, object]:
        intake_calls.append(kwargs)
        return {
            "outcome": "complete",
            "imported_count": 1,
            "tagged_count": 1,
            "criteria_count": 1,
            "failed_count": 0,
        }

    result = run_config_generation_job(
        context=context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        data_root=tmp_path / "data",
        question_bank_db_path=_bank_path(tmp_path),
        tagging_ai_service_factory=lambda: _DeferredTaggingService(protocol),
        taxonomy_governance=object(),
        question_bank_intake_runner=intake_runner,
    )

    assert result["outcome"] == "complete"
    # Joint analysis was limited to the targeted question.
    assert len(protocol.calls) == 1
    prompt = protocol.calls[0]["kwargs"]["input"][1]["content"][0]["text"]
    analyzed_ids = [
        int(item["question_id"])
        for item in json.loads(prompt)["questions"]
    ]
    assert analyzed_ids == [_qnum(source.questions[0].question_id)]
    # Intake ran against the filtered targeted artifact.
    assert len(intake_calls) == 1
    intake_refs = {
        reference
        for reference, _fingerprint in (
            intake_calls[0]["artifact"].bundle.source_fingerprints
        )
    }
    assert intake_refs == {source.questions[0].question_id}

    session = db.sessions.get_grading_session(session_id)
    assert str(session["rubric_path"]) != old_paths[0]
    after = load_editor_config(db, session_id)
    before_questions = _questions_by_id(before.payload)
    after_questions = _questions_by_id(after.payload)
    assert set(after_questions) == set(before_questions)
    targeted_parent = canonical_parent_id(source.questions[0].question_id)
    for qid in before_questions:
        if qid == targeted_parent:
            continue
        assert after_questions[qid] == before_questions[qid]
        assert _answers_by_id(after.payload)[qid] == _answers_by_id(
            before.payload
        )[qid]
    targeted = after_questions[targeted_parent]
    assert targeted != before_questions[targeted_parent]
    assert targeted["max_score"] == before_questions[targeted_parent][
        "max_score"
    ]
    assert after.payload["rubric"]["total_score"] == (
        before.payload["rubric"]["total_score"]
    )


def test_targeted_analysis_failure_keeps_published_rubric(
    tmp_path: Path,
) -> None:
    db, session_id, old_paths = _published_db(tmp_path)
    _source_service, source = _two_question_source(tmp_path, session_id)
    _seed_prior_analysis(tmp_path, db, session_id, source, old_paths)

    before = load_editor_config(db, session_id)
    staged = _stage_targeted_input(
        tmp_path / "uploaded",
        source,
        session_id,
        old_paths,
        before.payload,
        before.revision,
        [source.questions[0].question_id],
    )
    context, _store = _job_context(
        db.db_path,
        _targeted_payload(session_id, staged, source),
    )
    first_qid = _qnum(source.questions[0].question_id)
    protocol = _FailingDeferredProtocol(first_qid)

    result = run_config_generation_job(
        context=context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        data_root=tmp_path / "data",
        question_bank_db_path=_bank_path(tmp_path),
        tagging_ai_service_factory=lambda: _DeferredTaggingService(protocol),
        taxonomy_governance=object(),
        question_bank_intake_runner=lambda **kwargs: {
            "outcome": "complete",
            "imported_count": 1,
            "tagged_count": 1,
            "criteria_count": 1,
            "failed_count": 0,
        },
    )

    assert result["outcome"] == "partial"
    session = db.sessions.get_grading_session(session_id)
    assert str(session["rubric_path"]) == old_paths[0]
    assert str(session["answer_key_path"]) == old_paths[1]
    after = load_editor_config(db, session_id)
    assert after.payload["rubric"] == before.payload["rubric"]
    assert after.payload["answer_key"] == before.payload["answer_key"]


def test_targeted_intake_incomplete_keeps_published_rubric(
    tmp_path: Path,
) -> None:
    db, session_id, old_paths = _published_db(tmp_path)
    _source_service, source = _two_question_source(tmp_path, session_id)
    _seed_prior_analysis(tmp_path, db, session_id, source, old_paths)

    before = load_editor_config(db, session_id)
    staged = _stage_targeted_input(
        tmp_path / "uploaded",
        source,
        session_id,
        old_paths,
        before.payload,
        before.revision,
        [source.questions[0].question_id],
    )
    context, _store = _job_context(
        db.db_path,
        _targeted_payload(session_id, staged, source),
    )
    protocol = _DeferredProtocol()

    result = run_config_generation_job(
        context=context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        data_root=tmp_path / "data",
        question_bank_db_path=_bank_path(tmp_path),
        tagging_ai_service_factory=lambda: _DeferredTaggingService(protocol),
        taxonomy_governance=object(),
        question_bank_intake_runner=lambda **kwargs: {
            "outcome": "partial",
            "imported_count": 1,
            "tagged_count": 0,
            "evidence_count": 0,
            "failed_count": 1,
        },
    )

    assert result["outcome"] == "partial"
    session = db.sessions.get_grading_session(session_id)
    assert str(session["rubric_path"]) == old_paths[0]
    after = load_editor_config(db, session_id)
    assert after.payload["rubric"] == before.payload["rubric"]


def test_targeted_cancel_keeps_published_rubric(
    tmp_path: Path,
) -> None:
    db, session_id, old_paths = _published_db(tmp_path)
    _source_service, source = _two_question_source(tmp_path, session_id)

    before = load_editor_config(db, session_id)
    staged = _stage_targeted_input(
        tmp_path / "uploaded",
        source,
        session_id,
        old_paths,
        before.payload,
        before.revision,
        [source.questions[0].question_id],
    )
    context, store = _job_context(
        db.db_path,
        _targeted_payload(session_id, staged, source),
    )
    assert store.request_cancel(context.job_id)

    with pytest.raises(JobCancellationRequested):
        run_config_generation_job(
            context=context,
            db=db,
            upload_config_dir=tmp_path / "uploaded",
            data_root=tmp_path / "data",
            question_bank_db_path=_bank_path(tmp_path),
            tagging_ai_service_factory=lambda: _DeferredTaggingService(
                _DeferredProtocol()
            ),
            taxonomy_governance=object(),
        )

    session = db.sessions.get_grading_session(session_id)
    assert str(session["rubric_path"]) == old_paths[0]
    after = load_editor_config(db, session_id)
    assert after.payload["rubric"] == before.payload["rubric"]


def test_targeted_regeneration_without_prior_artifact_analyzes_selected(
    tmp_path: Path,
) -> None:
    db, session_id, old_paths = _published_db(tmp_path)
    _source_service, source = _two_question_source(tmp_path, session_id)

    before = load_editor_config(db, session_id)
    staged = _stage_targeted_input(
        tmp_path / "uploaded",
        source,
        session_id,
        old_paths,
        before.payload,
        before.revision,
        [source.questions[0].question_id],
    )
    context, _store = _job_context(
        db.db_path,
        _targeted_payload(session_id, staged, source),
    )
    protocol = _DeferredProtocol()

    result = run_config_generation_job(
        context=context,
        db=db,
        upload_config_dir=tmp_path / "uploaded",
        data_root=tmp_path / "data",
        question_bank_db_path=_bank_path(tmp_path),
        tagging_ai_service_factory=lambda: _DeferredTaggingService(protocol),
        taxonomy_governance=object(),
        question_bank_intake_runner=lambda **kwargs: {
            "outcome": "complete",
            "imported_count": 1,
            "tagged_count": 1,
            "criteria_count": 1,
            "failed_count": 0,
        },
    )

    assert result["outcome"] == "complete"
    assert len(protocol.calls) == 1
    prompt = protocol.calls[0]["kwargs"]["input"][1]["content"][0]["text"]
    analyzed_ids = [
        int(item["question_id"])
        for item in json.loads(prompt)["questions"]
    ]
    assert analyzed_ids == [_qnum(source.questions[0].question_id)]
    after = load_editor_config(db, session_id)
    before_questions = _questions_by_id(before.payload)
    targeted_parent = canonical_parent_id(source.questions[0].question_id)
    for qid, question in _questions_by_id(after.payload).items():
        if qid != targeted_parent:
            assert question == before_questions[qid]
