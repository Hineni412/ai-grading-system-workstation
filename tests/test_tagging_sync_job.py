from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

import backend.jobs.tagging_sync as tagging_sync_module
import question_bank.services.question_write_service as question_write_module
from backend.jobs.manager import JobCancellationRequested, JobContext, JobManager
from backend.jobs.store import JobStore
from backend.jobs.tagging_sync import run_tagging_sync_job
from question_bank.current_knowledge import CurrentKnowledgeResolver
from question_bank.database.schema import connect
from question_bank.models.question import QuestionCreate
from question_bank.models.tag_schema import TagAnalysis
from question_bank.services.ai_tagging_service import AITaggingResult, AITaggingService
from question_bank.services.question_read_service import (
    QuestionBankReadService,
    QuestionReadFilters,
)
from question_bank.services.question_frequency_service import QuestionFrequencyService
from question_bank.services.question_write_service import QuestionBankWriteService
from tests.question_bank_support import QuestionBankTestStore
from question_bank.solution_evidence import SolutionEvidenceRepository
from question_bank.taxonomy.governance import TaxonomyGovernance
from question_bank.training_criteria import (
    GatewayBatchResponse,
    QuestionAnalysisInputLoader,
    solution_evidence_source_content_hash,
)
from tests.current_knowledge_support import install_current_knowledge


LEGACY_CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "question_bank"
    / "taxonomy"
    / "catalogs"
    / "tag_vocabulary_v2.json"
)


def _analysis(*, confidence: float = 0.88) -> TagAnalysis:
    return TagAnalysis.from_dict(
        {
            "knowledge_points": ["整式运算"],
            "method_tags": [],
            "thought_tags": ["整体思想"],
            "ability_tags": ["运算能力"],
            "math_model_tags": [],
            "difficulty": 3,
            "error_prone_points": ["符号错误"],
            "prerequisite_points": ["有理数运算"],
            "textbook_chapter": "七年级下册 第一章 整式的乘除",
            "teaching_stage": "期末复习",
            "suitable_student_level": "基础巩固",
            "reason": "考查整式运算。",
            "confidence": confidence,
        }
    )


def _complete() -> AITaggingResult:
    return AITaggingResult(
        ok=True,
        mock_mode=False,
        analysis=_analysis(),
        model_name="fake-tag-model",
        quality_status="complete",
    )


def _partial(error: str = "quality incomplete") -> AITaggingResult:
    return AITaggingResult(
        ok=True,
        mock_mode=False,
        analysis=_analysis(confidence=0.4),
        error=error,
        model_name="fake-tag-model",
        quality_status="partial",
        quality_notes=[error],
    )


class FakeAI:
    def __init__(self, results, *, on_call=None) -> None:
        self.results = dict(results)
        self.on_call = on_call
        self.calls: list[list[int]] = []

    def analyze_questions(self, contexts, **_kwargs):
        ids = sorted(int(item) for item in contexts)
        self.calls.append(ids)
        if self.on_call is not None:
            self.on_call()
        return {question_id: self.results[question_id] for question_id in ids}


def _seed(db_path: Path, count: int) -> list[int]:
    service = QuestionBankTestStore(db_path)
    question_ids = [
        service.add_question(
            QuestionCreate(
                question_number=str(index),
                question_text=f"第{index}题测试题干",
                answer_text=str(index),
            )
        )
        for index in range(1, count + 1)
    ]
    install_current_knowledge(db_path)
    return question_ids


def _context(tmp_path: Path, payload: dict[str, object]) -> tuple[JobContext, JobStore]:
    store = JobStore(tmp_path / "jobs.db")
    job = store.create_job("tagging_sync", payload)
    assert store.mark_running(job.id)
    return JobContext(job.id, job.job_type, job.payload, store), store


def _seed_current_projection_rows(db_path: Path, question) -> None:
    evidence_hash = solution_evidence_source_content_hash(question)
    criterion_hash = question.criterion_source_content_hash
    evidence_version_id = hashlib.sha256(
        f"evidence:{question.question_id}:{evidence_hash}".encode()
    ).hexdigest()
    criterion_version_id = hashlib.sha256(
        f"criterion:{question.question_id}:{criterion_hash}".encode()
    ).hexdigest()
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO question_solution_evidence_versions (
                evidence_version_id, question_id, source_content_hash,
                schema_version, content_hash, evidence_json, status,
                source_kind, source_reference, created_by
            ) VALUES (?, ?, ?, 'question-solution-evidence-v1', ?, '{}',
                      'proposed', 'combined_model', ?, 'model:fake')
            """,
            (
                evidence_version_id,
                question.question_id,
                evidence_hash,
                hashlib.sha256(b"{}").hexdigest(),
                f"test:{evidence_version_id}",
            ),
        )
        connection.execute(
            """
            INSERT INTO training_criterion_versions (
                version_id, question_id, version_number, parent_version_id,
                source_content_hash, schema_version, status, source_kind,
                source_reference, criteria_json, criteria_hash,
                quality_status, quality_codes_json, created_by
            ) VALUES (?, ?, 1, NULL, ?, 'training-criteria-draft-v1',
                      'proposed', 'combined_model', ?, '{}', ?,
                      'passed', '[]', 'model:fake')
            """,
            (
                criterion_version_id,
                question.question_id,
                criterion_hash,
                f"test:{criterion_version_id}",
                hashlib.sha256(b"{}").hexdigest(),
            ),
        )
        connection.execute(
            """
            INSERT INTO training_criterion_heads (
                question_id, current_version_id, approved_version_id,
                current_source_hash, revision
            ) VALUES (?, ?, NULL, ?, 1)
            """,
            (question.question_id, criterion_version_id, criterion_hash),
        )


def _mark_criterion_stale(db_path: Path, question_id: int) -> None:
    """Reproduce a leftover fill: tags and evidence stay current, criteria expired."""

    stale_hash = hashlib.sha256(b"stale-criterion-hash").hexdigest()
    live_hash = hashlib.sha256(b"live-criterion-hash").hexdigest()
    with connect(db_path) as connection:
        connection.execute(
            """
            UPDATE training_criterion_versions
            SET status = 'stale',
                source_content_hash = ?
            WHERE question_id = ?
            """,
            (stale_hash, question_id),
        )
        connection.execute(
            """
            UPDATE training_criterion_heads
            SET current_source_hash = ?
            WHERE question_id = ?
            """,
            (live_hash, question_id),
        )


def _seed_successful_tag_source(
    db_path: Path,
    *,
    question_id: int,
    source_content_hash: str,
    operation_id: str,
) -> None:
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO question_analysis_operations (
                operation_id, input_fingerprint, contract_version,
                requested_projection, status
            ) VALUES (?, ?, 'combined-v2', 'tag', 'succeeded')
            """,
            (
                operation_id,
                hashlib.sha256(operation_id.encode("utf-8")).hexdigest(),
            ),
        )
        connection.execute(
            """
            INSERT INTO question_analysis_items (
                operation_id, question_id, source_content_hash,
                tag_status, criteria_status
            ) VALUES (?, ?, ?, 'succeeded', 'not_requested')
            """,
            (operation_id, question_id, source_content_hash),
        )


def test_tagging_sync_saves_only_complete_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 2)
    fake_ai = FakeAI({ids[0]: _complete(), ids[1]: _partial()})
    context, _store = _context(tmp_path, {"question_ids": ids})
    initialize_calls: list[Path] = []
    resolver_calls: list[Path] = []
    refresh_calls: list[tuple[int, ...]] = []
    original_initialize = question_write_module.initialize_database
    original_resolver = CurrentKnowledgeResolver.from_active_database
    original_refresh = (
        QuestionFrequencyService.invalidate_frequency_cache_for_questions
    )

    def tracking_initialize(path: Path) -> None:
        initialize_calls.append(Path(path))
        original_initialize(path)

    def tracking_resolver(
        _cls: type[CurrentKnowledgeResolver],
        path: Path,
    ) -> CurrentKnowledgeResolver:
        resolver_calls.append(Path(path))
        return original_resolver(path)

    def tracking_refresh(
        self: QuestionFrequencyService,
        question_ids,
    ) -> None:
        captured = tuple(int(item) for item in question_ids)
        refresh_calls.append(captured)
        original_refresh(self, captured)

    monkeypatch.setattr(
        question_write_module,
        "initialize_database",
        tracking_initialize,
    )
    monkeypatch.setattr(
        CurrentKnowledgeResolver,
        "from_active_database",
        classmethod(tracking_resolver),
    )
    monkeypatch.setattr(
        QuestionFrequencyService,
        "invalidate_frequency_cache_for_questions",
        tracking_refresh,
    )

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        ai_service_factory=lambda: fake_ai,
        batch_size=2,
    )

    assert result["outcome"] == "partial"
    assert result["successful_question_ids"] == [ids[0]]
    assert result["failed_question_ids"] == [ids[1]]
    assert result["failures"][0]["category"] == "quality"
    assert QuestionBankTestStore(db_path).get_question(ids[0])["tags"]
    assert QuestionBankTestStore(db_path).get_question(ids[1])["tags"] == []
    assert "fake-tag-model" not in json.dumps(result)
    assert initialize_calls == [db_path]
    assert resolver_calls == [db_path]
    assert refresh_calls == [(ids[0],)]


def test_tagging_sync_proposal_persistence_keeps_the_planned_contract() -> None:
    calls: list[dict[str, object]] = []

    class RecordingGovernance:
        @staticmethod
        def constrain(payload, *, context):
            del payload
            calls.append(dict(context))
            return {
                "proposals": [
                    {
                        "id": "proposal-test",
                        "dimension": "knowledge",
                        "proposed_name": "候选知识点",
                    }
                ]
            }

    result = AITaggingResult(
        ok=True,
        mock_mode=False,
        analysis=_analysis(),
        model_name="fake-tag-model",
        quality_status="needs_review",
        taxonomy_revision=9,
        proposals=[
            {
                "dimension": "knowledge",
                "name": "候选知识点",
                "definition": "候选定义",
                "reason": "候选目录中没有",
                "nearest_id": "",
                "why_not_reuse": "语义边界不同",
            }
        ],
    )
    contract = {
        "candidate_fingerprint": "fingerprint-question-1",
        "knowledge_catalog_revision": 4,
        "allowed_term_ids": {"knowledge": ["kp-question-1"]},
    }

    persisted = tagging_sync_module._persist_proposals(
        RecordingGovernance(),
        result,
        question_id=1,
        job_id="job-contract",
        expected_revision=9,
        knowledge_graph_release_id="release-contract",
        taxonomy_contract=contract,
    )

    assert persisted[0]["id"] == "proposal-test"
    assert calls == [
        {
            "persist_proposals": True,
            "question_ref": "1",
            "model": "fake-tag-model",
            "request_token": (
                "tagging-sync:job-contract:question:1:taxonomy:9:"
                "graph:release-contract:candidates:fingerprint-question-1"
            ),
            "expected_revision": 9,
            "allowed_term_ids": {"knowledge": ["kp-question-1"]},
            "knowledge_catalog_revision": 4,
        }
    ]


def test_production_tagging_uses_one_combined_call_and_persists_point_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "qb.db"
    data_root = tmp_path / "data"
    question_id = QuestionBankTestStore(db_path).add_question(
        QuestionCreate(
            question_number="1",
            question_text="计算整式运算并化简。",
            answer_text="合并同类项后写出化简结果。",
            question_type="解答题",
        )
    )
    install_current_knowledge(db_path)
    context, _store = _context(tmp_path, {"question_ids": [question_id]})
    gateway_calls: list[tuple[str, tuple[int, ...]]] = []
    initialize_calls: list[Path] = []
    refresh_calls: list[tuple[int, ...]] = []
    original_initialize = question_write_module.initialize_database
    original_refresh = (
        QuestionFrequencyService.invalidate_frequency_cache_for_questions
    )

    def tracking_initialize(path: Path) -> None:
        initialize_calls.append(Path(path))
        original_initialize(path)

    def tracking_refresh(
        self: QuestionFrequencyService,
        question_ids,
    ) -> None:
        captured = tuple(int(item) for item in question_ids)
        refresh_calls.append(captured)
        original_refresh(self, captured)

    monkeypatch.setattr(
        question_write_module,
        "initialize_database",
        tracking_initialize,
    )
    monkeypatch.setattr(
        QuestionFrequencyService,
        "invalidate_frequency_cache_for_questions",
        tracking_refresh,
    )

    class FakeCombinedGateway:
        def __init__(self, **_kwargs) -> None:
            pass

        def analyze(self, batch, *, projection, operation_id, request_id):
            del request_id
            gateway_calls.append(
                (projection, tuple(item.question_id for item in batch.questions))
            )
            item = batch.questions[0]
            candidates = item.taxonomy_contract["candidates"]
            knowledge = next(
                item
                for item in candidates["knowledge"]
                if item["name"] == "整式运算"
            )
            ability = candidates["ability"][0]
            curriculum = candidates["curriculum"][0]
            tag_payload = _analysis().to_dict()
            tag_payload.update(
                {
                    "knowledge_points": [knowledge["name"]],
                    "ability_tags": [ability["name"]],
                    "method_tags": [],
                    "thought_tags": [],
                    "textbook_chapter": curriculum["name"],
                    "textbook_chapters": [curriculum["name"]],
                    "canonical_knowledge_id": knowledge["id"],
                    "reason": "按动态候选识别整式运算。",
                    "confidence": 0.92,
                }
            )
            return GatewayBatchResponse(
                payload={
                    "results": [
                        {
                            "question_id": item.question_id,
                            "tag_analysis": tag_payload,
                            "solution_evidence": {
                                "schema_version": "question-solution-evidence-v1",
                                "question_id": item.question_id,
                                "parts": [
                                    {
                                        "part_id": "part-1",
                                        "label": "整题",
                                        "response_mode": "process_required",
                                        "canonical_answer": "",
                                        "accepted_forms": [],
                                        "full_answer": "合并同类项并写出化简结果。",
                                        "proof_obligations": [],
                                        "visual_requirements": [],
                                        "deduction_policy": [
                                            "没有合并同类项过程则该点未达成"
                                        ],
                                        "allow_alternative_methods": True,
                                        "evidence_points": [
                                            {
                                                "evidence_point_id": "point-1",
                                                "target": "完成整式化简",
                                                "observable_evidence": "正确合并同类项。",
                                                "fine_term_links": [
                                                    {
                                                        "fine_term_id": knowledge["id"],
                                                        "fine_term_name": knowledge["name"],
                                                        "role": "direct",
                                                    }
                                                ],
                                                "equivalent_rules": [],
                                                "counterexamples": [],
                                            }
                                        ],
                                    }
                                ],
                                "auxiliary_rules": [],
                                "rationale": "按可观察步骤拆分。",
                                "confidence": 0.93,
                            },
                        }
                    ]
                },
                model_name="synthetic-combined",
            )

    monkeypatch.setattr(
        tagging_sync_module,
        "OpenAICombinedAnalysisGateway",
        FakeCombinedGateway,
    )
    # knowledge_graph_db_path 缺省会指向会话级共享题库库：全量跑时其他测试的应用启动
    # 会往共享库装上 revision 4 的签入知识标准，与本文件词表 revision 冲突，这里改用私有路径。
    governance = TaxonomyGovernance(
        catalog_path=LEGACY_CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=tmp_path / "governance-combined-kg.db",
    )
    service = AITaggingService(
        env={
            "QUESTION_BANK_TAGGING_API_KEY": "synthetic-key",
            "QUESTION_BANK_TAGGING_MODEL": "synthetic-combined",
        },
        protocol_adapter=object(),
        taxonomy_governance=governance,
    )

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        data_root=data_root,
        ai_service_factory=lambda: service,
        taxonomy_governance=governance,
    )

    assert gateway_calls == [("both", (question_id,))]
    assert initialize_calls == [db_path]
    assert refresh_calls == [(question_id,)]
    assert result["outcome"] == "complete"
    assert result["analysis_contract"] == "combined-v3"
    assert result["evidence_succeeded_question_ids"] == [question_id]
    assert QuestionBankTestStore(db_path).get_question(question_id)["tags"]
    stored = SolutionEvidenceRepository(db_path).latest(question_id)
    assert stored is not None
    assert stored["evidence"]["parts"][0]["evidence_points"][0]["target"] == (
        "完成整式化简"
    )
    assert stored["evidence"]["whole_question_classification"][
        "direct_fine_terms"
    ] == [
        {
            "fine_term_id": "kp_alg_polynomial",
            "fine_term_name": "整式运算",
        }
    ]
    assert stored["evidence"]["whole_question_classification"][
        "resolved_core_node_ids"
    ] == ["kp_alg_polynomial"]


def test_fill_twelve_questions_only_analyzes_five_missing_and_refreshes_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "qb.db"
    data_root = tmp_path / "data"
    question_ids = _seed(db_path, 12)
    with connect(db_path) as connection:
        paper_id = int(
            connection.execute(
                """
                INSERT INTO papers (
                    title, source_file, content_fingerprint, import_status,
                    grade, semester, textbook_version
                ) VALUES (
                    '合成十二题试卷', 'synthetic.docx', 'synthetic-paper',
                    'success', '七年级', '下学期', '北师大版（2024）'
                )
                """
            ).lastrowid
        )
        connection.execute(
            """
            UPDATE questions
            SET paper_id = ?,
                question_text = '用 AAS 证明三角形全等',
                answer_text = '写出 AAS 全等证明过程'
            """,
            (paper_id,),
        )

    loaded = QuestionAnalysisInputLoader(
        db_path=db_path,
        data_root=data_root,
    ).load(
        question_ids,
        curriculum_volume_id="bnu24-math-g7-lower",
    )
    bank = QuestionBankTestStore(db_path)
    for question in loaded[:7]:
        assert bank.save_tag_analysis(
            question.question_id,
            _analysis(),
            model_name="existing",
        )
        _seed_current_projection_rows(db_path, question)

    analyzed_ids: list[int] = []

    class PersistingCombinedModule:
        def __init__(self, **_kwargs) -> None:
            pass

        def analyze_work_items(self, *, work_items, **_kwargs):
            analyzed_ids.extend(item.question.question_id for item in work_items)
            for item in work_items:
                if item.analyze_tag:
                    assert bank.save_tag_analysis(
                        item.question.question_id,
                        _analysis(),
                        model_name="synthetic-model",
                    )
                _seed_current_projection_rows(db_path, item.question)
            return {
                "items": [
                    {
                        "question_id": item.question.question_id,
                        "tag_status": (
                            "succeeded" if item.analyze_tag else "not_requested"
                        ),
                        "tag_error_category": "",
                        "criteria_status": "succeeded",
                        "criteria_error_category": "",
                    }
                    for item in work_items
                ],
                "criterion_audit": {
                    "items": [
                        {
                            "question_id": item.question.question_id,
                            "status": "succeeded",
                        }
                        for item in work_items
                    ]
                },
                "projection_audit": {
                    "retrieval_misses": [],
                    "proposals": [],
                    "secondary_matches": [],
                    "relation_hints": [],
                    "retrieval_miss_question_ids": [],
                    "proposal_question_ids": [],
                },
                "question_projection_audits": {},
            }

    monkeypatch.setattr(
        tagging_sync_module,
        "CombinedQuestionAnalysisModule",
        PersistingCombinedModule,
    )
    # knowledge_graph_db_path 缺省会指向会话级共享题库库：全量跑时其他测试的应用启动
    # 会往共享库装上 revision 4 的签入知识标准，与本文件词表 revision 冲突，这里改用私有路径。
    governance = TaxonomyGovernance(
        catalog_path=LEGACY_CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=tmp_path / "governance-fill-kg.db",
    )
    ai_service = AITaggingService(
        env={
            "QUESTION_BANK_TAGGING_API_KEY": "synthetic-key",
            "QUESTION_BANK_TAGGING_MODEL": "synthetic-combined",
        },
        protocol_adapter=object(),
        taxonomy_governance=governance,
    )
    second_context, _store = _context(
        tmp_path,
        {
            "question_ids": question_ids,
            "curriculum_volume_id": "bnu24-math-g7-lower",
        },
    )
    second = run_tagging_sync_job(
        context=second_context,
        question_bank_db_path=db_path,
        data_root=data_root,
        ai_service_factory=lambda: ai_service,
        taxonomy_governance=governance,
    )

    assert analyzed_ids == question_ids[7:]
    assert second["outcome"] == "complete"
    assert second["skipped_complete_count"] == 7
    assert second["tagged_count"] == 5
    assert second["complete_tagged_count"] == 12
    assert second["evidence_count"] == 12
    assert second["criteria_count"] == 12
    assert second["failed_question_ids"] == []

    reopened = QuestionBankReadService(db_path, data_root=data_root)
    paper = reopened.list_papers()[0]
    assert paper["tagged_question_count"] == 12
    assert paper["evidence_question_count"] == 12
    assert paper["criteria_question_count"] == 12
    assert paper["complete_analysis_count"] == 12
    assert reopened.list_questions(
        QuestionReadFilters(
            paper_ids=(paper_id,),
            analysis_status="incomplete",
        )
    ).total == 0


def test_fill_reanalyzes_stale_criteria_without_retagging_complete_neighbors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """补齐 must resend only leftover questions, not the rest of the paper.

    Tags plus current evidence with a stale training criterion used to take a
    local-only republish path. That path never called the model, so retrying
    补齐 finished in seconds with no send record and the same leftover question.
    """

    db_path = tmp_path / "qb.db"
    data_root = tmp_path / "data"
    question_ids = _seed(db_path, 3)
    complete_ids = question_ids[:2]
    leftover_id = question_ids[2]
    loaded = QuestionAnalysisInputLoader(
        db_path=db_path,
        data_root=data_root,
    ).load(
        question_ids,
        curriculum_volume_id="bnu24-math-g7-lower",
    )
    bank = QuestionBankTestStore(db_path)
    for question in loaded:
        assert bank.save_tag_analysis(
            question.question_id,
            _analysis(),
            model_name="existing",
        )
        _seed_current_projection_rows(db_path, question)
    _mark_criterion_stale(db_path, leftover_id)

    analyzed: list[tuple[int, bool, bool, bool]] = []

    class RecordingCombinedModule:
        def __init__(self, **_kwargs) -> None:
            pass

        def analyze_work_items(self, *, work_items, **_kwargs):
            for item in work_items:
                analyzed.append(
                    (
                        item.question.question_id,
                        bool(item.analyze_tag),
                        bool(item.analyze_solution_evidence),
                        bool(item.publish_saved_criterion),
                    )
                )
                current_hash = item.question.criterion_source_content_hash
                with connect(db_path) as connection:
                    connection.execute(
                        """
                        UPDATE training_criterion_versions
                        SET status = 'proposed',
                            source_content_hash = ?
                        WHERE question_id = ?
                        """,
                        (current_hash, item.question.question_id),
                    )
                    connection.execute(
                        """
                        UPDATE training_criterion_heads
                        SET current_source_hash = ?
                        WHERE question_id = ?
                        """,
                        (current_hash, item.question.question_id),
                    )
            return {
                "items": [
                    {
                        "question_id": item.question.question_id,
                        "tag_status": "not_requested",
                        "tag_error_category": "",
                        "criteria_status": "succeeded",
                        "criteria_error_category": "",
                    }
                    for item in work_items
                ],
                "criterion_audit": {
                    "items": [
                        {
                            "question_id": item.question.question_id,
                            "status": "succeeded",
                        }
                        for item in work_items
                    ]
                },
                "projection_audit": {
                    "retrieval_misses": [],
                    "proposals": [],
                    "secondary_matches": [],
                    "relation_hints": [],
                    "retrieval_miss_question_ids": [],
                    "proposal_question_ids": [],
                },
                "question_projection_audits": {},
            }

    monkeypatch.setattr(
        tagging_sync_module,
        "CombinedQuestionAnalysisModule",
        RecordingCombinedModule,
    )
    governance = TaxonomyGovernance(
        catalog_path=LEGACY_CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=tmp_path / "governance-stale-fill-kg.db",
    )
    ai_service = AITaggingService(
        env={
            "QUESTION_BANK_TAGGING_API_KEY": "synthetic-key",
            "QUESTION_BANK_TAGGING_MODEL": "synthetic-combined",
        },
        protocol_adapter=object(),
        taxonomy_governance=governance,
    )
    context, _store = _context(
        tmp_path,
        {
            "question_ids": question_ids,
            "curriculum_volume_id": "bnu24-math-g7-lower",
        },
    )

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        data_root=data_root,
        ai_service_factory=lambda: ai_service,
        taxonomy_governance=governance,
    )

    assert analyzed == [(leftover_id, False, True, False)]
    assert result["outcome"] == "complete"
    assert result["tagged_count"] == 0
    assert result["failed_question_ids"] == []
    assert set(complete_ids).isdisjoint({item[0] for item in analyzed})


def test_fill_retags_when_saved_tags_belong_to_an_old_question_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "qb.db"
    data_root = tmp_path / "data"
    question_id = _seed(db_path, 1)[0]
    bank = QuestionBankTestStore(db_path)
    # knowledge_graph_db_path 缺省会指向会话级共享题库库：全量跑时其他测试的应用启动
    # 会往共享库装上 revision 4 的签入知识标准，与本文件词表 revision 冲突，这里改用私有路径。
    governance = TaxonomyGovernance(
        catalog_path=LEGACY_CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=tmp_path / "governance-retag-kg.db",
    )
    loader = QuestionAnalysisInputLoader(
        db_path=db_path,
        data_root=data_root,
    )
    base = loader.load(
        (question_id,),
        curriculum_volume_id="bnu24-math-g7-lower",
    )[0]
    old_contract = governance.prompt_contract(base.tagging_context.to_dict())
    old_input = loader.load(
        (question_id,),
        taxonomy_contracts={question_id: old_contract},
        curriculum_volume_id="bnu24-math-g7-lower",
    )[0]
    assert bank.save_tag_analysis(
        question_id,
        _analysis(),
        model_name="old-model",
    )
    _seed_successful_tag_source(
        db_path,
        question_id=question_id,
        source_content_hash=old_input.source_content_hash,
        operation_id="old-tag-source",
    )

    with connect(db_path) as connection:
        connection.execute(
            """
            UPDATE questions
            SET question_text = '修改后的全等证明题',
                updated_at = datetime('now', 'localtime', '+1 second')
            WHERE id = ?
            """,
            (question_id,),
        )
    changed = loader.load(
        (question_id,),
        taxonomy_contracts={
            question_id: governance.prompt_contract(
                loader.load((question_id,))[0].tagging_context.to_dict()
            )
        },
        curriculum_volume_id="bnu24-math-g7-lower",
    )[0]
    assert changed.source_content_hash != old_input.source_content_hash
    _seed_current_projection_rows(db_path, changed)

    analyzed: list[tuple[int, bool]] = []

    class PersistingCurrentTagModule:
        def __init__(self, **_kwargs) -> None:
            pass

        def analyze_work_items(self, *, work_items, **_kwargs):
            for item in work_items:
                analyzed.append(
                    (item.question.question_id, bool(item.analyze_tag))
                )
                if item.analyze_tag:
                    assert bank.save_tag_analysis(
                        item.question.question_id,
                        _analysis(),
                        model_name="current-model",
                    )
                    _seed_successful_tag_source(
                        db_path,
                        question_id=item.question.question_id,
                        source_content_hash=item.question.source_content_hash,
                        operation_id="current-tag-source",
                    )
            return {
                "items": [
                    {
                        "question_id": item.question.question_id,
                        "tag_status": (
                            "succeeded" if item.analyze_tag else "not_requested"
                        ),
                        "tag_error_category": "",
                        "criteria_status": "not_requested",
                        "criteria_error_category": "",
                    }
                    for item in work_items
                ],
                "criterion_audit": {"items": []},
                "projection_audit": {
                    "retrieval_misses": [],
                    "proposals": [],
                    "secondary_matches": [],
                    "relation_hints": [],
                    "retrieval_miss_question_ids": [],
                    "proposal_question_ids": [],
                },
                "question_projection_audits": {},
            }

    monkeypatch.setattr(
        tagging_sync_module,
        "CombinedQuestionAnalysisModule",
        PersistingCurrentTagModule,
    )
    ai_service = AITaggingService(
        env={
            "QUESTION_BANK_TAGGING_API_KEY": "synthetic-key",
            "QUESTION_BANK_TAGGING_MODEL": "synthetic-combined",
        },
        protocol_adapter=object(),
        taxonomy_governance=governance,
    )
    context, _store = _context(
        tmp_path,
        {
            "question_ids": [question_id],
            "curriculum_volume_id": "bnu24-math-g7-lower",
        },
    )

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        data_root=data_root,
        ai_service_factory=lambda: ai_service,
        taxonomy_governance=governance,
    )

    assert analyzed == [(question_id, True)]
    assert result["tagged_count"] == 1
    assert result["complete_tagged_count"] == 1
    assert result["failed_question_ids"] == []


def test_source_content_hash_ignores_taxonomy_contract_changes(
    tmp_path: Path,
) -> None:
    # 词表状态绝不参与题目指纹：确认新词、升级词表不能让已保存标签过期，
    # 否则一次全局版本提升会触发全库重打。题目内容变化才改变指纹。
    db_path = tmp_path / "qb.db"
    data_root = tmp_path / "data"
    question_id = _seed(db_path, 1)[0]
    governance = TaxonomyGovernance(
        catalog_path=LEGACY_CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=tmp_path / "governance-fingerprint-kg.db",
    )
    loader = QuestionAnalysisInputLoader(db_path=db_path, data_root=data_root)

    without_contract = loader.load(
        (question_id,),
        curriculum_volume_id="bnu24-math-g7-lower",
    )[0]
    contract = governance.prompt_contract(
        without_contract.tagging_context.to_dict()
    )
    with_contract = loader.load(
        (question_id,),
        taxonomy_contracts={question_id: contract},
        curriculum_volume_id="bnu24-math-g7-lower",
    )[0]
    assert with_contract.source_content_hash == without_contract.source_content_hash

    mutated_contract = dict(contract)
    mutated_contract["candidate_fingerprint"] = "changed-fingerprint"
    mutated_contract["allowed_term_ids"] = {"knowledge": ["kp-changed"]}
    with_mutated_contract = loader.load(
        (question_id,),
        taxonomy_contracts={question_id: mutated_contract},
        curriculum_volume_id="bnu24-math-g7-lower",
    )[0]
    assert (
        with_mutated_contract.source_content_hash
        == without_contract.source_content_hash
    )

    with connect(db_path) as connection:
        connection.execute(
            "UPDATE questions SET question_text = '修改后的题干' WHERE id = ?",
            (question_id,),
        )
    changed = loader.load(
        (question_id,),
        curriculum_volume_id="bnu24-math-g7-lower",
    )[0]
    assert changed.source_content_hash != without_contract.source_content_hash


def test_tagging_sync_reports_local_retrieval_misses_without_retry(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 1)
    result_with_miss = AITaggingResult(
        ok=True,
        mock_mode=False,
        analysis=_analysis(),
        model_name="fake-tag-model",
        quality_status="complete",
        retrieval_misses=[
            {
                "dimension": "knowledge",
                "submitted_name": "整式运算",
                "canonical_id": "kp_alg_polynomial",
                "canonical_name": "整式运算",
                "source_field": "knowledge_points",
            }
        ],
    )
    fake_ai = FakeAI({ids[0]: result_with_miss})
    context, _store = _context(tmp_path, {"question_ids": ids})

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        ai_service_factory=lambda: fake_ai,
    )

    assert fake_ai.calls == [ids]
    assert result["outcome"] == "complete"
    assert result["retrieval_miss_count"] == 1
    assert result["retrieval_miss_question_ids"] == ids


def test_unified_tagging_exposes_evidence_failure_in_retry_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "qb.db"
    data_root = tmp_path / "data"
    ids = _seed(db_path, 1)
    context, _store = _context(tmp_path, {"question_ids": ids})

    class StubCombinedModule:
        def __init__(self, **_kwargs) -> None:
            pass

        def analyze_work_items(self, **_kwargs):
            assert QuestionBankTestStore(db_path).save_tag_analysis(
                ids[0],
                _analysis(),
                model_name="synthetic-model",
            )
            return {
                "items": [
                    {
                        "question_id": ids[0],
                        "tag_status": "succeeded",
                        "tag_error_category": "",
                        "criteria_status": "failed",
                        "criteria_error_category": "evidence_validation",
                    }
                ],
                "criterion_audit": {"items": []},
                "projection_audit": {
                    "retrieval_misses": [],
                    "proposals": [],
                    "secondary_matches": [],
                    "relation_hints": [],
                    "retrieval_miss_question_ids": [],
                    "proposal_question_ids": [],
                },
                "question_projection_audits": {},
            }

    monkeypatch.setattr(
        tagging_sync_module,
        "CombinedQuestionAnalysisModule",
        StubCombinedModule,
    )
    # knowledge_graph_db_path 缺省会指向会话级共享题库库：全量跑时其他测试的应用启动
    # 会往共享库装上 revision 4 的签入知识标准，与本文件词表 revision 冲突，这里改用私有路径。
    governance = TaxonomyGovernance(
        catalog_path=LEGACY_CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=tmp_path / "governance-retry-kg.db",
    )
    service = AITaggingService(
        env={
            "QUESTION_BANK_TAGGING_API_KEY": "synthetic-key",
            "QUESTION_BANK_TAGGING_MODEL": "synthetic-model",
        },
        protocol_adapter=object(),
        taxonomy_governance=governance,
    )

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        data_root=data_root,
        ai_service_factory=lambda: service,
        taxonomy_governance=governance,
    )

    assert result["successful_question_ids"] == ids
    assert result["evidence_failed_question_ids"] == ids
    assert result["failed_question_ids"] == ids
    assert result["failed_count"] == 1
    assert result["retryable"] is True


def test_unified_tagging_rejects_claimed_success_when_no_projection_was_saved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal job may only report what a fresh database read can see."""

    db_path = tmp_path / "qb.db"
    data_root = tmp_path / "data"
    question_id = _seed(db_path, 1)[0]
    context, _store = _context(tmp_path, {"question_ids": [question_id]})

    class ClaimsSuccessWithoutWriting:
        def __init__(self, **_kwargs) -> None:
            pass

        def analyze_work_items(self, **_kwargs):
            return {
                "items": [
                    {
                        "question_id": question_id,
                        "tag_status": "succeeded",
                        "tag_error_category": "",
                        "criteria_status": "succeeded",
                        "criteria_error_category": "",
                    }
                ],
                "criterion_audit": {
                    "items": [
                        {"question_id": question_id, "status": "succeeded"}
                    ]
                },
                "projection_audit": {
                    "retrieval_misses": [],
                    "proposals": [],
                    "secondary_matches": [],
                    "relation_hints": [],
                    "retrieval_miss_question_ids": [],
                    "proposal_question_ids": [],
                },
                "question_projection_audits": {},
            }

    monkeypatch.setattr(
        tagging_sync_module,
        "CombinedQuestionAnalysisModule",
        ClaimsSuccessWithoutWriting,
    )
    # knowledge_graph_db_path 缺省会指向会话级共享题库库：全量跑时其他测试的应用启动
    # 会往共享库装上 revision 4 的签入知识标准，与本文件词表 revision 冲突，这里改用私有路径。
    governance = TaxonomyGovernance(
        catalog_path=LEGACY_CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=tmp_path / "governance-claimed-kg.db",
    )
    service = AITaggingService(
        env={
            "QUESTION_BANK_TAGGING_API_KEY": "synthetic-key",
            "QUESTION_BANK_TAGGING_MODEL": "synthetic-model",
        },
        protocol_adapter=object(),
        taxonomy_governance=governance,
    )

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        data_root=data_root,
        ai_service_factory=lambda: service,
        taxonomy_governance=governance,
    )

    assert result["outcome"] == "failed"
    assert result["successful_question_ids"] == []
    assert result["failed_question_ids"] == [question_id]
    assert result["complete_tagged_count"] == 0
    assert result["evidence_succeeded_question_ids"] == []
    assert result["evidence_failed_question_ids"] == [question_id]
    assert result["criteria_succeeded_question_ids"] == []
    assert result["criteria_failed_question_ids"] == [question_id]
    assert QuestionBankTestStore(db_path).get_question(question_id)["tags"] == []
    assert SolutionEvidenceRepository(db_path).latest(question_id) is None


def test_fill_reanalyzes_evidence_and_criteria_from_an_old_question_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old-version rows must not make the current question look complete."""

    db_path = tmp_path / "qb.db"
    data_root = tmp_path / "data"
    question_id = _seed(db_path, 1)[0]
    bank = QuestionBankTestStore(db_path)
    assert bank.save_tag_analysis(question_id, _analysis(), model_name="existing")
    current = QuestionAnalysisInputLoader(
        db_path=db_path,
        data_root=data_root,
    ).load((question_id,))[0]
    assert current.criterion_source_content_hash != "a" * 64

    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO question_solution_evidence_versions (
                evidence_version_id, question_id, source_content_hash,
                schema_version, content_hash, evidence_json, status,
                source_kind, source_reference, created_by
            ) VALUES (?, ?, ?, 'question-solution-evidence-v1', ?, '{}',
                      'proposed', 'combined_model', 'old-source', 'model:fake')
            """,
            ("b" * 64, question_id, "a" * 64, "c" * 64),
        )
        connection.execute(
            """
            INSERT INTO training_criterion_versions (
                version_id, question_id, version_number, parent_version_id,
                source_content_hash, schema_version, status, source_kind,
                source_reference, criteria_json, criteria_hash,
                quality_status, quality_codes_json, created_by
            ) VALUES (?, ?, 1, NULL, ?, 'training-criteria-draft-v1',
                      'proposed', 'combined_model', 'old-source', '{}', ?,
                      'passed', '[]', 'model:fake')
            """,
            ("d" * 64, question_id, "a" * 64, "e" * 64),
        )
        connection.execute(
            """
            INSERT INTO training_criterion_heads (
                question_id, current_version_id, approved_version_id,
                current_source_hash, revision
            ) VALUES (?, ?, NULL, ?, 1)
            """,
            (question_id, "d" * 64, "a" * 64),
        )

    requested: list[tuple[str | None, int]] = []

    class CapturingCombinedModule:
        def __init__(self, **_kwargs) -> None:
            pass

        def analyze_work_items(self, *, work_items, **_kwargs):
            requested.extend(
                (item.projection, item.question.question_id)
                for item in work_items
            )
            return {
                "items": [
                    {
                        "question_id": question_id,
                        "tag_status": "not_requested",
                        "tag_error_category": "",
                        "criteria_status": "failed",
                        "criteria_error_category": "evidence_validation",
                    }
                ],
                "criterion_audit": {"items": []},
                "projection_audit": {
                    "retrieval_misses": [],
                    "proposals": [],
                    "secondary_matches": [],
                    "relation_hints": [],
                    "retrieval_miss_question_ids": [],
                    "proposal_question_ids": [],
                },
                "question_projection_audits": {},
            }

    monkeypatch.setattr(
        tagging_sync_module,
        "CombinedQuestionAnalysisModule",
        CapturingCombinedModule,
    )
    # knowledge_graph_db_path 缺省会指向会话级共享题库库：全量跑时其他测试的应用启动
    # 会往共享库装上 revision 4 的签入知识标准，与本文件词表 revision 冲突，这里改用私有路径。
    governance = TaxonomyGovernance(
        catalog_path=LEGACY_CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=tmp_path / "governance-reanalyze-kg.db",
    )
    ai_service = AITaggingService(
        env={
            "QUESTION_BANK_TAGGING_API_KEY": "synthetic-key",
            "QUESTION_BANK_TAGGING_MODEL": "synthetic-model",
        },
        protocol_adapter=object(),
        taxonomy_governance=governance,
    )
    context, _store = _context(tmp_path, {"question_ids": [question_id]})

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        data_root=data_root,
        ai_service_factory=lambda: ai_service,
        taxonomy_governance=governance,
    )

    assert requested == [("training_criteria", question_id)]
    assert result["outcome"] == "partial"
    assert result["evidence_failed_question_ids"] == [question_id]
    assert result["criteria_failed_question_ids"] == [question_id]


def test_unified_evidence_retry_preserves_existing_successful_tags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "qb.db"
    data_root = tmp_path / "data"
    question_id = _seed(db_path, 1)[0]
    service = QuestionBankTestStore(db_path)
    assert service.save_tag_analysis(
        question_id,
        _analysis(),
        model_name="existing-model",
    )
    existing_tags = service.get_question(question_id)["tags"]
    context, _store = _context(
        tmp_path,
        {
            "question_ids": [question_id],
            "retry_evidence_question_ids": [question_id],
        },
    )
    calls: list[tuple[str, tuple[int, ...]]] = []

    class StubCombinedModule:
        def __init__(self, **_kwargs) -> None:
            pass

        def analyze_work_items(self, *, work_items, **_kwargs):
            calls.extend(
                (
                    item.projection,
                    (item.question.question_id,),
                )
                for item in work_items
                if item.projection is not None
            )
            for item in work_items:
                _seed_current_projection_rows(db_path, item.question)
            return {
                "items": [
                    {
                        "question_id": question_id,
                        "tag_status": "not_requested",
                        "tag_error_category": "",
                        "criteria_status": "succeeded",
                        "criteria_error_category": "",
                    }
                ],
                "criterion_audit": {"items": []},
                "projection_audit": {
                    "retrieval_misses": [],
                    "proposals": [],
                    "secondary_matches": [],
                    "relation_hints": [],
                    "retrieval_miss_question_ids": [],
                    "proposal_question_ids": [],
                },
                "question_projection_audits": {},
            }

    monkeypatch.setattr(
        tagging_sync_module,
        "CombinedQuestionAnalysisModule",
        StubCombinedModule,
    )
    ai_service = AITaggingService(
        env={
            "QUESTION_BANK_TAGGING_API_KEY": "synthetic-key",
            "QUESTION_BANK_TAGGING_MODEL": "synthetic-model",
        },
        protocol_adapter=object(),
    )

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        data_root=data_root,
        ai_service_factory=lambda: ai_service,
    )

    assert calls == [("training_criteria", (question_id,))]
    assert service.get_question(question_id)["tags"] == existing_tags
    assert result["outcome"] == "complete"
    assert result["tagged_count"] == 0
    assert result["successful_question_ids"] == [question_id]
    assert result["evidence_succeeded_question_ids"] == [question_id]
    assert result["failed_question_ids"] == []


def test_unified_evidence_retry_retags_question_without_saved_tags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 复现 job 138（question 816）：调用方传入 retry_evidence_question_ids，
    # 但该题从未保存过标签。旧逻辑 analyze_tag=False 导致标签永远不被补析，
    # 终审只能报“完整标签未能保存”，用户反复重试永不成功。
    db_path = tmp_path / "qb.db"
    data_root = tmp_path / "data"
    question_id = _seed(db_path, 1)[0]
    bank = QuestionBankTestStore(db_path)
    governance = TaxonomyGovernance(
        catalog_path=LEGACY_CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=tmp_path / "governance-retry-retag-kg.db",
    )
    analyzed: list[tuple[int, bool]] = []

    class RetaggingCombinedModule:
        def __init__(self, **_kwargs) -> None:
            pass

        def analyze_work_items(self, *, work_items, **_kwargs):
            for item in work_items:
                analyzed.append(
                    (item.question.question_id, bool(item.analyze_tag))
                )
                if item.analyze_tag:
                    assert bank.save_tag_analysis(
                        item.question.question_id,
                        _analysis(),
                        model_name="retry-model",
                    )
                _seed_current_projection_rows(db_path, item.question)
            return {
                "items": [
                    {
                        "question_id": item.question.question_id,
                        "tag_status": (
                            "succeeded" if item.analyze_tag else "not_requested"
                        ),
                        "tag_error_category": "",
                        "criteria_status": "succeeded",
                        "criteria_error_category": "",
                    }
                    for item in work_items
                ],
                "criterion_audit": {
                    "items": [
                        {
                            "question_id": item.question.question_id,
                            "status": "succeeded",
                        }
                        for item in work_items
                    ]
                },
                "projection_audit": {
                    "retrieval_misses": [],
                    "proposals": [],
                    "secondary_matches": [],
                    "relation_hints": [],
                    "retrieval_miss_question_ids": [],
                    "proposal_question_ids": [],
                },
                "question_projection_audits": {},
            }

    monkeypatch.setattr(
        tagging_sync_module,
        "CombinedQuestionAnalysisModule",
        RetaggingCombinedModule,
    )
    ai_service = AITaggingService(
        env={
            "QUESTION_BANK_TAGGING_API_KEY": "synthetic-key",
            "QUESTION_BANK_TAGGING_MODEL": "synthetic-combined",
        },
        protocol_adapter=object(),
        taxonomy_governance=governance,
    )
    context, _store = _context(
        tmp_path,
        {
            "question_ids": [question_id],
            "retry_evidence_question_ids": [question_id],
        },
    )

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        data_root=data_root,
        ai_service_factory=lambda: ai_service,
        taxonomy_governance=governance,
    )

    assert analyzed == [(question_id, True)]
    assert result["outcome"] == "complete"
    assert result["tagged_count"] == 1
    assert result["failed_question_ids"] == []
    assert result["failures"] == []
    # 分配与登记覆盖对齐：该题这次确实进入了观测生命周期。
    lifecycle = json.loads(
        (tmp_path / "taxonomy-state.json").read_text(encoding="utf-8")
    )["observation_lifecycle"]
    generation = f"tagging-sync:{context.job_id}"
    assert str(question_id) in lifecycle["allocations"].get(generation, {})
    assert any(
        item["question_id"] == str(question_id)
        and item["generation_id"] == generation
        for item in lifecycle["observations"]
    )


def test_unified_observation_registration_failure_keeps_saved_tag_successful(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 观测登记是审计投影：标签已落库后登记失败只记日志，不再误判为
    # “完整标签未能保存”。
    db_path = tmp_path / "qb.db"
    data_root = tmp_path / "data"
    question_id = _seed(db_path, 1)[0]
    bank = QuestionBankTestStore(db_path)
    governance = TaxonomyGovernance(
        catalog_path=LEGACY_CATALOG_PATH,
        state_path=tmp_path / "taxonomy-state.json",
        knowledge_graph_db_path=tmp_path / "governance-observe-fail-kg.db",
    )

    class PersistingCombinedModule:
        def __init__(self, **_kwargs) -> None:
            pass

        def analyze_work_items(self, *, work_items, **_kwargs):
            for item in work_items:
                if item.analyze_tag:
                    assert bank.save_tag_analysis(
                        item.question.question_id,
                        _analysis(),
                        model_name="synthetic-model",
                    )
                _seed_current_projection_rows(db_path, item.question)
            return {
                "items": [
                    {
                        "question_id": item.question.question_id,
                        "tag_status": (
                            "succeeded" if item.analyze_tag else "not_requested"
                        ),
                        "tag_error_category": "",
                        "criteria_status": "succeeded",
                        "criteria_error_category": "",
                    }
                    for item in work_items
                ],
                "criterion_audit": {
                    "items": [
                        {
                            "question_id": item.question.question_id,
                            "status": "succeeded",
                        }
                        for item in work_items
                    ]
                },
                "projection_audit": {
                    "retrieval_misses": [],
                    "proposals": [],
                    "secondary_matches": [],
                    "relation_hints": [],
                    "retrieval_miss_question_ids": [],
                    "proposal_question_ids": [],
                },
                "question_projection_audits": {},
            }

    def failing_observation(*_args, **_kwargs) -> None:
        raise RuntimeError("synthetic observation registration failure")

    monkeypatch.setattr(
        tagging_sync_module,
        "CombinedQuestionAnalysisModule",
        PersistingCombinedModule,
    )
    monkeypatch.setattr(
        tagging_sync_module,
        "_record_successful_observation",
        failing_observation,
    )
    ai_service = AITaggingService(
        env={
            "QUESTION_BANK_TAGGING_API_KEY": "synthetic-key",
            "QUESTION_BANK_TAGGING_MODEL": "synthetic-combined",
        },
        protocol_adapter=object(),
        taxonomy_governance=governance,
    )
    context, _store = _context(tmp_path, {"question_ids": [question_id]})

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        data_root=data_root,
        ai_service_factory=lambda: ai_service,
        taxonomy_governance=governance,
    )

    assert result["outcome"] == "complete"
    assert result["tagged_count"] == 1
    assert result["successful_question_ids"] == [question_id]
    assert result["failed_question_ids"] == []
    assert result["failures"] == []


def test_tagging_sync_skips_complete_questions_and_retries_only_missing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 2)
    service = QuestionBankTestStore(db_path)
    assert service.save_tag_analysis(ids[0], _analysis(), model_name="existing")
    fake_ai = FakeAI({ids[1]: _complete()})
    context, _store = _context(tmp_path, {"question_ids": ids})

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        ai_service_factory=lambda: fake_ai,
        batch_size=1,
    )

    assert fake_ai.calls == [[ids[1]]]
    assert result["skipped_complete_count"] == 1
    assert result["tagged_count"] == 1
    assert result["outcome"] == "partial"
    assert result["evidence_failed_question_ids"] == [ids[0]]
    assert result["failed_question_ids"] == [ids[0]]
    assert result["retryable"] is True


def test_tagging_sync_classifies_missing_and_deleted_questions(tmp_path: Path) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 1)
    service = QuestionBankTestStore(db_path)
    assert service.delete_question(ids[0])
    fake_ai = FakeAI({})
    context, _store = _context(
        tmp_path, {"question_ids": [ids[0], 99999]}
    )

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        ai_service_factory=lambda: fake_ai,
    )

    assert fake_ai.calls == []
    assert result["failed_question_ids"] == [ids[0], 99999]
    assert {item["category"] for item in result["failures"]} == {"validation"}
    assert result["retryable"] is False


def test_tagging_sync_cancellation_during_batch_discards_batch_and_stops_next(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 2)
    context, store = _context(tmp_path, {"question_ids": ids})
    fake_ai = FakeAI(
        {ids[0]: _complete(), ids[1]: _complete()},
        on_call=lambda: store.request_cancel(context.job_id),
    )

    with pytest.raises(JobCancellationRequested):
        run_tagging_sync_job(
            context=context,
            question_bank_db_path=db_path,
            ai_service_factory=lambda: fake_ai,
            batch_size=1,
        )

    assert fake_ai.calls == [[ids[0]]]
    assert QuestionBankTestStore(db_path).get_question(ids[0])["tags"] == []
    assert QuestionBankTestStore(db_path).get_question(ids[1])["tags"] == []


def test_tagging_sync_cancellation_refreshes_already_saved_questions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 2)
    context, store = _context(tmp_path, {"question_ids": ids})
    refresh_calls: list[tuple[int, ...]] = []
    original_observation = tagging_sync_module._record_successful_observation
    original_refresh = (
        QuestionFrequencyService.invalidate_frequency_cache_for_questions
    )

    def cancel_after_observation(*args, **kwargs) -> None:
        original_observation(*args, **kwargs)
        assert store.request_cancel(context.job_id)

    def tracking_refresh(
        self: QuestionFrequencyService,
        question_ids,
    ) -> None:
        captured = tuple(int(item) for item in question_ids)
        refresh_calls.append(captured)
        original_refresh(self, captured)

    monkeypatch.setattr(
        tagging_sync_module,
        "_record_successful_observation",
        cancel_after_observation,
    )
    monkeypatch.setattr(
        QuestionFrequencyService,
        "invalidate_frequency_cache_for_questions",
        tracking_refresh,
    )

    with pytest.raises(JobCancellationRequested):
        run_tagging_sync_job(
            context=context,
            question_bank_db_path=db_path,
            ai_service_factory=lambda: FakeAI(
                {ids[0]: _complete(), ids[1]: _complete()}
            ),
            batch_size=1,
        )

    assert refresh_calls == [(ids[0],)]
    assert QuestionBankTestStore(db_path).get_question(ids[0])["tags"]
    assert QuestionBankTestStore(db_path).get_question(ids[1])["tags"] == []


def test_tagging_sync_honours_cancellation_before_first_batch(tmp_path: Path) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 1)
    context, store = _context(tmp_path, {"question_ids": ids})
    assert store.request_cancel(context.job_id)

    with pytest.raises(JobCancellationRequested):
        run_tagging_sync_job(
            context=context,
            question_bank_db_path=db_path,
            ai_service_factory=lambda: pytest.fail("AI factory must not run"),
        )


def test_tagging_sync_serializes_reversed_partially_overlapping_question_ids(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 3)
    first, _ = _context(
        tmp_path / "first", {"question_ids": [ids[1], ids[0]]}
    )
    second, _ = _context(
        tmp_path / "second", {"question_ids": [ids[2], ids[1]]}
    )
    entered = threading.Event()
    second_waiting = threading.Event()
    release = threading.Event()
    calls: list[list[int]] = []
    lock_attempts = 0
    guard = threading.Lock()
    real_locks = tagging_sync_module.keyed_execution_locks

    @contextmanager
    def observed_locks(keys, **kwargs):
        nonlocal lock_attempts
        with guard:
            lock_attempts += 1
            if lock_attempts == 2:
                second_waiting.set()
        with real_locks(keys, **kwargs):
            yield

    monkeypatch.setattr(tagging_sync_module, "keyed_execution_locks", observed_locks)

    class BlockingAI(FakeAI):
        def analyze_questions(self, contexts, **kwargs):
            calls.append(sorted(contexts))
            entered.set()
            assert release.wait(timeout=5)
            return super().analyze_questions(contexts, **kwargs)

    fake_ai = BlockingAI({question_id: _complete() for question_id in ids})
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            run_tagging_sync_job,
            context=first,
            question_bank_db_path=db_path,
            ai_service_factory=lambda: fake_ai,
            batch_size=2,
        )
        assert entered.wait(timeout=5)
        second_future = executor.submit(
            run_tagging_sync_job,
            context=second,
            question_bank_db_path=db_path,
            ai_service_factory=lambda: fake_ai,
            batch_size=2,
        )
        assert second_waiting.wait(timeout=5)
        release.set()
        first_result = first_future.result(timeout=5)
        second_result = second_future.result(timeout=5)

    assert calls == [[ids[0], ids[1]], [ids[2]]]
    assert first_result["tagged_count"] == 2
    assert second_result["skipped_complete_count"] == 1
    assert second_result["tagged_count"] == 1
    assert all(QuestionBankTestStore(db_path).get_question(item)["tags"] for item in ids)


def test_tagging_sync_cancels_while_waiting_for_overlapping_question_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 1)
    first, _ = _context(tmp_path / "first", {"question_ids": ids})
    second, second_store = _context(tmp_path / "second", {"question_ids": ids})
    holder_entered = threading.Event()
    second_waiting = threading.Event()
    release = threading.Event()
    lock_attempts = 0
    guard = threading.Lock()
    real_locks = tagging_sync_module.keyed_execution_locks

    @contextmanager
    def observed_locks(keys, **kwargs):
        nonlocal lock_attempts
        with guard:
            lock_attempts += 1
            if lock_attempts == 2:
                second_waiting.set()
        with real_locks(keys, **kwargs):
            yield

    monkeypatch.setattr(tagging_sync_module, "keyed_execution_locks", observed_locks)

    class BlockingAI(FakeAI):
        def analyze_questions(self, contexts, **kwargs):
            holder_entered.set()
            assert release.wait(timeout=5)
            return super().analyze_questions(contexts, **kwargs)

    fake_ai = BlockingAI({ids[0]: _complete()})
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            run_tagging_sync_job,
            context=first,
            question_bank_db_path=db_path,
            ai_service_factory=lambda: fake_ai,
        )
        assert holder_entered.wait(timeout=5)
        second_future = executor.submit(
            run_tagging_sync_job,
            context=second,
            question_bank_db_path=db_path,
            ai_service_factory=lambda: fake_ai,
        )
        assert second_waiting.wait(timeout=5)
        assert second_store.request_cancel(second.job_id)
        try:
            with pytest.raises(JobCancellationRequested):
                second_future.result(timeout=1)
        finally:
            release.set()
        first_future.result(timeout=5)


def test_tagging_sync_reports_monotonic_progress(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 2)
    context, store = _context(tmp_path, {"question_ids": ids})
    fake_ai = FakeAI({question_id: _complete() for question_id in ids})
    progress: list[float] = []
    original = store.update_progress

    def record(job_id, *, progress: float, stage: str, detail: str = ""):
        progress_value = float(progress)
        progress_values.append(progress_value)
        return original(job_id, progress=progress, stage=stage, detail=detail)

    progress_values = progress
    monkeypatch.setattr(store, "update_progress", record)

    run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        ai_service_factory=lambda: fake_ai,
        batch_size=1,
    )

    assert progress == sorted(progress)
    assert progress[0] == 0.05
    assert progress[-1] == 1.0


def test_tagging_sync_classifies_save_failure_without_raising(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 1)
    context, _store = _context(tmp_path, {"question_ids": ids})

    def fail_save(*_args, **_kwargs):
        raise RuntimeError(f"database failed at {tmp_path}")

    monkeypatch.setattr(QuestionBankWriteService, "save_tag_analysis", fail_save)
    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        ai_service_factory=lambda: FakeAI({ids[0]: _complete()}),
    )

    assert result["failures"] == [
        {
            "question_id": ids[0],
            "category": "save",
            "message": "完整标签未能保存。",
        }
    ]


def test_tagging_sync_frequency_refresh_failure_is_not_reported_as_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 1)
    context, _store = _context(tmp_path, {"question_ids": ids})

    def fail_refresh(*_args, **_kwargs) -> None:
        raise RuntimeError("synthetic frequency refresh failed")

    monkeypatch.setattr(
        QuestionFrequencyService,
        "invalidate_frequency_cache_for_questions",
        fail_refresh,
    )

    with pytest.raises(RuntimeError, match="synthetic frequency refresh failed"):
        run_tagging_sync_job(
            context=context,
            question_bank_db_path=db_path,
            ai_service_factory=lambda: FakeAI({ids[0]: _complete()}),
        )

    assert QuestionBankTestStore(db_path).get_question(ids[0])["tags"]


def test_tagging_sync_masks_factory_error_before_job_store_persistence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 1)
    manager = JobManager(JobStore(tmp_path / "manager-jobs.db"), max_workers=1)

    def unsafe_factory():
        raise RuntimeError(f"API key=super-secret at {tmp_path}")

    manager.register(
        "tagging_sync",
        lambda context: run_tagging_sync_job(
            context=context,
            question_bank_db_path=db_path,
            ai_service_factory=unsafe_factory,
        ),
    )
    job = manager.submit("tagging_sync", {"question_ids": ids})
    manager.wait(job.id, timeout=5)
    stored = manager.get(job.id)
    manager.shutdown()

    assert stored is not None
    assert stored.status == "failed"
    assert stored.error == "tagging sync setup failed"
    assert "super-secret" not in str(stored.error)
    assert str(tmp_path) not in str(stored.error)


def test_tagging_sync_sanitizes_ai_failure_details(tmp_path: Path) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 1)
    raw_error = f"Timeout API key=super-secret at {tmp_path}"
    failed = AITaggingResult(
        ok=False,
        mock_mode=False,
        error=raw_error,
        model_name="fake-tag-model",
        quality_status="invalid",
    )
    fake_ai = FakeAI({ids[0]: failed})
    context, _store = _context(tmp_path, {"question_ids": ids})

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        ai_service_factory=lambda: fake_ai,
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["failures"][0]["category"] == "timeout"
    assert "super-secret" not in serialized
    assert str(tmp_path) not in serialized


def test_relation_projection_retry_replays_saved_evidence_without_ai(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "qb.db"
    context, _store = _context(
        tmp_path,
        {
            "question_ids": [11],
            "retry_relation_question_ids": [11],
        },
    )
    monkeypatch.setattr(
        SolutionEvidenceRepository,
        "relation_hints",
        lambda self, question_ids, *, operation_id: [{
            "question_id": 11,
            "evidence_point_id": "point-1",
            "source_keys": ["kp_alg_linear_equation"],
            "target_keys": ["kp_alg_equation_properties"],
            "confidence": 0.99,
            "model_name": "stored-model",
        }],
    )
    monkeypatch.setattr(
        tagging_sync_module.EvidenceRelationGovernanceService,
        "govern",
        lambda self, hints, *, operation_id: {
            "candidate_count": 1,
            "auto_confirmed_count": 0,
            "exception_count": 1,
            "reused_count": 0,
            "failed_count": 0,
            "failed_question_ids": [],
            "outcomes": [],
        },
    )

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        ai_service_factory=lambda: (_ for _ in ()).throw(
            AssertionError("relation replay must not call AI")
        ),
    )

    assert result["outcome"] == "complete"


def test_tagging_sync_explicit_retag_reanalyzes_complete_question(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 1)
    service = QuestionBankTestStore(db_path)
    assert service.save_tag_analysis(ids[0], _analysis(), model_name="existing")
    fake_ai = FakeAI({ids[0]: _complete()})
    context, _store = _context(
        tmp_path,
        {
            "question_ids": ids,
            "force_retag_question_ids": ids,
        },
    )

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        ai_service_factory=lambda: fake_ai,
    )

    assert fake_ai.calls == [ids]
    assert result["tagged_count"] == 1
    assert result["outcome"] == "complete"
    assert result["retryable"] is False


@pytest.mark.parametrize(
    ("raw_error", "expected_category"),
    [
        ("429 rate limit exceeded", "rate_limit"),
        ("JSON decode parse failure", "parse"),
    ],
)
def test_tagging_sync_classifies_retryable_ai_errors(
    tmp_path: Path,
    raw_error: str,
    expected_category: str,
) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 1)
    failed = AITaggingResult(
        ok=False,
        mock_mode=False,
        error=raw_error,
        quality_status="invalid",
    )
    context, _store = _context(tmp_path, {"question_ids": ids})

    result = run_tagging_sync_job(
        context=context,
        question_bank_db_path=db_path,
        ai_service_factory=lambda: FakeAI({ids[0]: failed}),
    )

    assert result["failures"][0]["category"] == expected_category
    assert result["retryable"] is True
