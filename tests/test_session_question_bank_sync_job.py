from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any
import pytest
from backend.config_workspace.publish import load_editor_config
from backend.config_workspace.deferred_analysis import DeferredAnalysisArtifact, DeferredAnalysisArtifactStore
from backend.jobs.manager import JobContext
from backend.jobs.question_bank_sync import _adopt_deferred_analysis_with_links, run_session_question_bank_sync_job
from backend.jobs.store import JobStore
from backend.repositories.db_manager import DBManager
from question_bank.database.schema import connect, initialize_database
from question_bank.models.question import QuestionCreate
from tests.current_knowledge_support import install_current_knowledge
from question_bank.services.question_write_service import QuestionBankWriteService
from tests.question_bank_support import QuestionBankTestStore
from question_bank.solution_evidence import SolutionEvidenceRepository
from question_bank.training_criteria import ConfigQuestionAnalysisSource, GatewayBatchResponse, DeferredCombinedQuestionAnalysisModule, question_analysis_input_from_config_source
from backend.repositories.grading_database import open_grading_repositories


def test_stale_job_cannot_overwrite_a_newer_sync_owner(
    tmp_path: Path,
) -> None:
    db, session_id, _source, source_sha256, _revision = _configured_session(tmp_path)
    store = JobStore(db.db_path)
    newer_revision = "d" * 64
    db.sessions.update_question_bank_sync_state(
        session_id,
        state="running",
        details={
            "config_revision": newer_revision,
            "job_id": 99,
            "source_paper_sha256": source_sha256,
            "stage": "tagging",
        },
    )

    assert (
        store.transition_question_bank_sync_state_if_owned(
            session_id=session_id,
            job_id=98,
            source_paper_sha256=source_sha256,
            config_revision="e" * 64,
            state="not_started",
            details={"stage": "stale"},
        )
        is False
    )

    unchanged = db.sessions.get_grading_session(session_id)
    assert unchanged is not None
    assert unchanged["question_bank_sync_state"] == "running"
    assert json.loads(unchanged["question_bank_sync_details_json"]) == {
        "config_revision": newer_revision,
        "job_id": 99,
        "source_paper_sha256": source_sha256,
        "stage": "tagging",
    }


def _configured_session(
    tmp_path: Path,
) -> tuple[DBManager, int, Path, str, str]:
    data_root = tmp_path / "data"
    databases = data_root / "databases"
    databases.mkdir(parents=True)
    db = open_grading_repositories(databases / "grading.db")
    db.initialize()
    rubric_path = data_root / "config" / "rubric.json"
    answer_path = data_root / "config" / "answer.json"
    rubric_path.parent.mkdir(parents=True)
    rubric_path.write_text(
        json.dumps(
            {
                "total_score": 100,
                "questions": [
                    {
                        "question_id": "Q1",
                        "question_type": "choice",
                        "max_score": 100,
                        "parts": [
                            {
                                "part_id": "Q1",
                                "part_score": 100,
                                "response_mode": "exact_objective",
                                "steps": [
                                    {
                                        "step_id": "S1",
                                        "step_score": 100,
                                        "core_goal": "选择正确选项",
                                        "required_elements": ["B"],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    answer_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "canonical_answer": "B",
                        "parts": [{"part_id": "Q1", "answer": "B"}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    source = data_root / "question_bank" / "raw_papers" / "paper.docx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"controlled source paper")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    session_id = db.sessions.create_grading_session(
        "同步测试",
        str(rubric_path),
        str(answer_path),
        source_paper_path=str(source),
        source_paper_sha256=source_sha256,
    )
    revision = load_editor_config(db, session_id).revision
    return db, session_id, source, source_sha256, revision


def _deferred_sync_contract() -> dict[str, Any]:
    return {
        "taxonomy_revision": 2,
        "allowed_dimensions": ["knowledge", "ability"],
        "candidates": {
            "knowledge": [
                {
                    "id": "kp_alg_linear_equation",
                    "name": "一元一次方程",
                    "aliases": ["一次方程"],
                }
            ],
            "ability": [
                {
                    "id": "ability-calculation",
                    "name": "运算能力",
                    "aliases": [],
                }
            ],
        },
    }


def _deferred_sync_result(question_id: int) -> dict[str, Any]:
    return {
        "results": [
            {
                "question_id": question_id,
                "tag_analysis": {
                    "method_tags": [],
                    "thought_tags": ["方程思想"],
                    "ability_tags": ["运算能力"],
                    "math_model_tags": [],
                    "special_type_tags": [],
                    "difficulty": 3,
                    "predicted_error_patterns": [],
                    "part_features": [
                        {
                            "part_id": "part-1",
                            "part_label": "第1题",
                            "solo": 1,
                            "reasoning": 0,
                            "computation": 1,
                            "context": 0,
                            "hidden": 0,
                            "cases": 0,
                            "param_dynamic": 0,
                            "trap": 0,
                            "knowledge": 0,
                            "context_kind": "无情境",
                            "evidence": "合成逐小问特征。",
                        }
                    ],
                    "taxonomy_revision": 2,
                    "proposed_tags": [],
                    "reason": "合成延期标签。",
                    "confidence": 0.95,
                },
                "solution_evidence": {
                    "schema_version": "question-solution-evidence-v1",
                    "question_id": question_id,
                    "parts": [
                        {
                            "part_id": "part-1",
                            "label": "第1题",
                            "response_mode": "exact_objective",
                            "canonical_answer": "B",
                            "accepted_forms": ["B"],
                            "full_answer": "B",
                            "proof_obligations": [],
                            "visual_requirements": [],
                            "deduction_policy": ["答案不等价则未达成"],
                            "allow_alternative_methods": False,
                            "evidence_points": [
                                {
                                    "evidence_point_id": "answer-1",
                                    "target": "选出正确答案",
                                    "observable_evidence": "作答为 B",
                                    "fine_term_links": [
                                        {
                                            "fine_term_id": "kp_alg_linear_equation",
                                            "fine_term_name": "一元一次方程",
                                            "role": "direct",
                                        }
                                    ],
                                    "equivalent_rules": [],
                                    "counterexamples": ["作答为 A"],
                                }
                            ],
                        }
                    ],
                    "auxiliary_rules": [],
                    "rationale": "合成延期证据。",
                    "confidence": 0.95,
                },
            }
        ]
    }


class _DeferredSyncGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[int, ...]] = []

    def analyze(self, batch: Any, **_kwargs: Any) -> GatewayBatchResponse:
        self.calls.append(batch.question_ids)
        payload = {
            "results": [
                item
                for qid in batch.question_ids
                for item in _deferred_sync_result(qid)["results"]
            ]
        }
        return GatewayBatchResponse(payload=payload, model_name="synthetic-combined-v3")


class _PassThroughTaxonomyGovernance:
    def snapshot(self) -> dict[str, Any]:
        return {
            "terms_by_dimension": {
                "knowledge": [
                    {
                        "id": "kp_alg_linear_equation",
                        "name": "一元一次方程",
                        "aliases": ["一次方程"],
                    }
                ]
            }
        }

    def resolve_term(
        self,
        dimension: str,
        name: str,
    ) -> dict[str, Any] | None:
        if dimension != "knowledge" or name not in {
            "一元一次方程",
            "一次方程",
        }:
            return None
        return {
            "id": "kp_alg_linear_equation",
            "name": "一元一次方程",
            "aliases": ["一次方程"],
        }

    def constrain(
        self,
        raw_analysis: dict[str, Any],
        context: object = None,
    ) -> dict[str, Any]:
        del context
        accepted_fields = {
            field: list(raw_analysis.get(field) or [])
            for field in (
                "knowledge_points",
                "method_tags",
                "thought_tags",
                "ability_tags",
                "math_model_tags",
                "special_type_tags",
                "textbook_chapters",
            )
        }
        return {
            "accepted_fields": accepted_fields,
            "accepted_terms": {
                "knowledge": [
                    {
                        "id": "kp_alg_linear_equation",
                        "name": "一元一次方程",
                    }
                ]
            },
            "proposals": [],
            "retrieval_misses": [],
            "taxonomy_revision": 2,
            "status": "complete",
            "notes": [],
        }


class _DeferredAdoptionTaggingService:
    model = "synthetic-combined-v3"

    def __init__(self, taxonomy_governance: Any | None = None) -> None:
        self.taxonomy_governance = (
            taxonomy_governance or _PassThroughTaxonomyGovernance()
        )

    def taxonomy_contracts(
        self,
        contexts: dict[int, object],
    ) -> dict[int, dict[str, Any]]:
        return {question_id: _deferred_sync_contract() for question_id in contexts}


def _run_deferred_adoption(
    tmp_path: Path,
) -> tuple[dict[str, object], _DeferredSyncGateway, list[int], Path, Path]:
    db, session_id, _source, source_sha256, revision = _configured_session(tmp_path)
    question_bank_db = tmp_path / "data" / "databases" / "question_bank.db"
    initialize_database(question_bank_db)
    install_current_knowledge(question_bank_db)
    source_question = question_analysis_input_from_config_source(
        {
            "question_id": "Q1",
            "question_text": "1 + 1 = ?",
            "answer_text": "B",
            "question_type": "choice",
        },
        question_id=1,
        curriculum_volume_id="bnu24-math-g7-upper",
        taxonomy_contract=_deferred_sync_contract(),
    )
    gateway = _DeferredSyncGateway()
    bundle = DeferredCombinedQuestionAnalysisModule(
        gateway=gateway, taxonomy_governance=None
    ).analyze(
        operation_id="config:synthetic:deferred-adoption",
        curriculum_volume_id="bnu24-math-g7-upper",
        sources=(ConfigQuestionAnalysisSource("Q1", source_question),),
    )
    artifact_id = "a" * 32
    source_id = "b" * 32
    source_revision = "c" * 64
    artifact_root = tmp_path / "analysis-artifacts"
    artifact = DeferredAnalysisArtifactStore(artifact_root).save(
        artifact_id=artifact_id,
        session_id=session_id,
        source_id=source_id,
        source_revision=source_revision,
        curriculum_volume_id="bnu24-math-g7-upper",
        bundle=bundle,
    )
    store = JobStore(db.db_path)
    job = store.create_job(
        "question_bank_sync",
        {
            "session_id": session_id,
            "mode": "sync",
            "config_revision": revision,
            "source_paper_sha256": source_sha256,
            "curriculum_volume_id": "bnu24-math-g7-upper",
            "analysis_artifact_id": artifact.artifact_id,
            "analysis_artifact_hash": artifact.content_hash,
            "analysis_source_id": source_id,
            "analysis_source_revision": source_revision,
        },
    )
    assert store.mark_running(job.id)
    context = JobContext(
        job_id=job.id, job_type=job.job_type, payload=job.payload, store=store
    )
    imported_ids: list[int] = []

    def import_runner(**_kwargs: Any) -> dict[str, object]:
        if imported_ids:
            return {
                "outcome": "complete",
                "successful_question_ids": [imported_ids[0]],
                "failed_question_ids": [],
                "failed_count": 0,
                "retryable": False,
            }
        with connect(question_bank_db) as connection:
            numbers = ("1",)
            question_ids = [
                int(
                    connection.execute(
                        "\n                        INSERT INTO questions (\n                            question_number, question_type, question_text,\n                            answer_text, source_file\n                        ) VALUES (?, 'choice', ?, 'B', 'paper.docx')\n                        ",
                        (number, f"{number} + 1 = ?"),
                    ).lastrowid
                )
                for number in numbers
            ]
        imported_ids.extend(question_ids)
        return {
            "outcome": "complete",
            "successful_question_ids": question_ids,
            "failed_question_ids": [],
            "failed_count": 0,
            "retryable": False,
        }

    def unexpected_tagging_runner(**_kwargs: Any) -> dict[str, object]:
        pytest.fail("deferred adoption must not call the tagging model runner")

    governance = _PassThroughTaxonomyGovernance()
    result = run_session_question_bank_sync_job(
        context=context,
        grading_db=db,
        question_bank_db_path=question_bank_db,
        data_root=tmp_path / "data",
        write_service=QuestionBankWriteService(
            question_bank_db, data_root=tmp_path / "data"
        ),
        question_import_runner=import_runner,
        tagging_sync_runner=unexpected_tagging_runner,
        ai_service_factory=lambda: _DeferredAdoptionTaggingService(governance),
        taxonomy_governance=governance,
        analysis_artifact_root=artifact_root,
    )
    return (
        result,
        gateway,
        imported_ids,
        question_bank_db,
        artifact_root / f"deferred_question_analysis_{artifact.artifact_id}.json",
    )


def test_sync_adopts_deferred_tags_and_evidence_without_tagging_model(
    tmp_path: Path,
) -> None:
    result, gateway, imported_ids, question_bank_db, artifact_path = (
        _run_deferred_adoption(tmp_path)
    )

    assert result["outcome"] == "complete", result
    assert result["tagged_count"] == 1
    assert result["evidence_count"] == 1
    assert result["criteria_count"] == 1
    assert result["taxonomy_review_count"] == 0
    assert result["taxonomy_review_question_ids"] == []
    assert result["retryable"] is False
    assert result["analysis_artifact_consumed"] is True
    assert gateway.calls == [(1,)]
    saved = QuestionBankTestStore(question_bank_db).get_question(imported_ids[0])
    assert saved is not None
    # 整题知识点改由判定点关联派生，模型标签只带能力等维度。
    assert any(
        tag["tag_type"] == "ability" and tag["tag_value"] == "运算能力"
        for tag in saved["tags"]
    )
    assert not any(
        tag["tag_type"] == "knowledge_point" and tag["tag_value"] == "一元一次方程"
        for tag in saved["tags"]
    )
    evidence = SolutionEvidenceRepository(question_bank_db).latest(imported_ids[0])
    assert evidence is not None
    assert (
        evidence["evidence"]["parts"][0]["evidence_points"][0]["fine_term_links"][0][
            "role"
        ]
        == "direct"
    )
    with connect(question_bank_db) as connection:
        criterion = connection.execute(
            """
            SELECT source_kind, criteria_json
            FROM training_criterion_versions
            WHERE question_id = ?
            """,
            (imported_ids[0],),
        ).fetchone()
    assert criterion is not None
    assert criterion["source_kind"] == "confirmed_rubric_adapter"
    criterion_payload = json.loads(str(criterion["criteria_json"]))
    assert criterion_payload["points"][0]["target"] == "给出正确或等价答案"
    assert criterion_payload["points"][0]["observable_evidence"] == "B"
    assert not artifact_path.exists()


def test_deferred_adoption_isolates_unreadable_questions_without_new_model_calls(tmp_path):
    database = tmp_path / 'TEST-deferred-isolation.db'
    initialize_database(database)
    install_current_knowledge(database)
    question_ids = [QuestionBankTestStore(database).add_question(
        QuestionCreate(question_number=str(index), question_text=f'{index} + 1 = ?',
            answer_text='B', question_type='choice')) for index in (1, 2)]
    sources = tuple(ConfigQuestionAnalysisSource(f'Q{index}',
        question_analysis_input_from_config_source({
            'question_id': f'Q{index}', 'question_text': f'{index} + 1 = ?',
            'answer_text': 'B', 'question_type': 'choice',
        }, question_id=index, curriculum_volume_id='bnu24-math-g7-upper',
        taxonomy_contract=_deferred_sync_contract())) for index in (1, 2))
    gateway = _DeferredSyncGateway()
    bundle = DeferredCombinedQuestionAnalysisModule(gateway=gateway).analyze(
        operation_id='config:TEST-deferred-isolation',
        curriculum_volume_id='bnu24-math-g7-upper', sources=sources)
    calls_before_adoption = list(gateway.calls)
    artifact = DeferredAnalysisArtifact(artifact_id='a' * 32, session_id=1,
        source_id='b' * 32, source_revision='c' * 64,
        curriculum_volume_id='bnu24-math-g7-upper', bundle=bundle, content_hash='d' * 64)
    damaged_id = question_ids[1]
    sidecar = tmp_path / f'question_bank/rich_content/question_{damaged_id}.json'
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text('{TEST-corrupt', encoding='utf-8')
    result = _adopt_deferred_analysis_with_links(artifact=artifact, session_id=1,
        question_bank_db_path=database, data_root=tmp_path,
        links={f'Q{index}': {'bank_question_id': qid}
            for index, qid in enumerate(question_ids, 1)},
        ai_service_factory=_DeferredAdoptionTaggingService,
        taxonomy_governance=_PassThroughTaxonomyGovernance())
    assert result['outcome'] == 'partial'
    assert result['requested_count'] == 2
    assert result['successful_question_ids'] == question_ids[:1]
    assert result['failed_question_ids'] == [damaged_id]
    assert result['failed_count'] == 1
    assert result['tagged_count'] == result['evidence_count'] == result['criteria_count'] == 1
    assert result['failures'][0]['reason_code'] == 'rich_content_invalid_json'
    assert result['adoption_results'][1]['input_error_code'] == 'rich_content_invalid_json'
    assert gateway.calls == calls_before_adoption
    assert SolutionEvidenceRepository(database).latest(damaged_id) is None
    assert QuestionBankTestStore(database).get_question(damaged_id)['tags'] == []
