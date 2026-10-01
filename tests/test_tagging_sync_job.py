from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import httpx
import openai
import pytest

import backend.jobs.tagging_sync as tagging_sync_module
import question_bank.services.question_write_service as question_write_module
from backend.jobs.manager import JobCancellationRequested, JobContext
from backend.jobs.store import JobStore
from backend.jobs.tagging_sync import run_tagging_sync_job
from question_bank.database.schema import connect
from question_bank.models.question import QuestionCreate
from question_bank.models.tag_schema import TagAnalysis
from question_bank.services.ai_tagging_service import AITaggingResult, AITaggingService
from question_bank.services.question_read_service import (
    QuestionBankReadService,
    QuestionReadFilters,
)
from question_bank.services.question_frequency_service import QuestionFrequencyService
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
            "method_tags": [],
            "thought_tags": ["整体思想"],
            "ability_tags": ["运算能力"],
            "math_model_tags": [],
            "special_type_tags": [],
            "difficulty": 3,
            "predicted_error_patterns": [],
            "part_features": [
                {
                    "part_id": "part-1",
                    "part_label": "整题",
                    "solo": 1,
                    "reasoning": 0,
                    "computation": 1,
                    "context": 0,
                    "context_kind": "无情境",
                    "hidden": 0,
                    "cases": 0,
                    "param_dynamic": 0,
                    "trap": 0,
                    "knowledge": 1,
                    "evidence": "常规运算",
                }
            ],
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


@pytest.mark.parametrize("duplicate", [False, True])
def test_production_tagging_uses_one_combined_call_and_persists_point_evidence(
    tmp_path: Path,
    duplicate: bool,
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
    requested_ids = [question_id]
    if duplicate:
        duplicate_id = QuestionBankTestStore(db_path).add_question(
            QuestionCreate(
                question_number="27",
                question_text="计算整式运算并化简。",
                answer_text="合并同类项后写出化简结果。",
                question_type="解答题",
            )
        )
        requested_ids.append(duplicate_id)
    context, _store = _context(tmp_path, {"question_ids": requested_ids})
    gateway_calls: list[tuple[str, tuple[int, ...]]] = []
    initialize_calls: list[Path] = []
    refresh_calls: list[tuple[int, ...]] = []
    original_initialize = question_write_module.initialize_database
    original_refresh = QuestionFrequencyService.invalidate_frequency_cache_for_questions

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
                item for item in candidates["knowledge"] if item["name"] == "整式运算"
            )
            ability = candidates["ability"][0]
            tag_payload = _analysis().to_dict()
            tag_payload.update(
                {
                    "ability_tags": [ability["name"]],
                    "method_tags": [],
                    "thought_tags": [],
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
                                                        "fine_term_name": knowledge[
                                                            "name"
                                                        ],
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
    assert result["evidence_succeeded_question_ids"] == requested_ids
    assert result["successful_question_ids"] == requested_ids
    assert result["criteria_succeeded_question_ids"] == requested_ids
    assert QuestionBankTestStore(db_path).get_question(question_id)["tags"]
    stored = SolutionEvidenceRepository(db_path).latest(question_id)
    assert stored is not None
    assert stored["evidence"]["parts"][0]["evidence_points"][0]["target"] == (
        "完成整式化简"
    )
    assert stored["evidence"]["whole_question_classification"]["direct_fine_terms"] == [
        {
            "fine_term_id": "kp_alg_polynomial",
            "fine_term_name": "整式运算",
        }
    ]
    assert stored["evidence"]["whole_question_classification"][
        "resolved_core_node_ids"
    ] == ["kp_alg_polynomial"]

    if duplicate:
        duplicate_evidence = SolutionEvidenceRepository(db_path).latest(duplicate_id)
        assert duplicate_evidence is not None
        assert duplicate_evidence["evidence"]["question_id"] == duplicate_id
        assert duplicate_evidence["evidence"]["parts"] == stored["evidence"]["parts"]
        second_context, _ = _context(tmp_path, {"question_ids": requested_ids})

        def no_factory():
            raise AssertionError("complete duplicate must not request a model")

        second = run_tagging_sync_job(
            context=second_context,
            question_bank_db_path=db_path,
            data_root=data_root,
            ai_service_factory=no_factory,
            taxonomy_governance=governance,
        )
        assert second["outcome"] == "complete"
        assert second["successful_question_ids"] == requested_ids


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


def test_tagging_sync_serializes_reversed_partially_overlapping_question_ids(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "qb.db"
    ids = _seed(db_path, 3)
    first, _ = _context(tmp_path / "first", {"question_ids": [ids[1], ids[0]]})
    second, _ = _context(tmp_path / "second", {"question_ids": [ids[2], ids[1]]})
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
    assert all(
        QuestionBankTestStore(db_path).get_question(item)["tags"] for item in ids
    )


_OPENAI_REQUEST = httpx.Request("POST", "https://example.invalid/v1/chat/completions")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            openai.AuthenticationError(
                "Error code: 401 - invalid api key",
                response=httpx.Response(401, request=_OPENAI_REQUEST),
                body=None,
            ),
            "service_config",
        ),
        (
            openai.RateLimitError(
                "Error code: 429 - too many requests",
                response=httpx.Response(429, request=_OPENAI_REQUEST),
                body=None,
            ),
            "rate_limit",
        ),
        (
            openai.BadRequestError(
                "Error code: 400 - unsupported parameter: response_format",
                response=httpx.Response(400, request=_OPENAI_REQUEST),
                body=None,
            ),
            "service_config",
        ),
        (
            openai.InternalServerError(
                "Error code: 503 - service unavailable",
                response=httpx.Response(503, request=_OPENAI_REQUEST),
                body=None,
            ),
            "network",
        ),
        (openai.APITimeoutError(request=_OPENAI_REQUEST), "timeout"),
        (openai.APIConnectionError(request=_OPENAI_REQUEST), "network"),
    ],
    ids=["401", "429", "400-param", "503", "timeout", "connection"],
)
def test_classify_tagging_error_maps_transport_failures(
    error: BaseException,
    expected: str,
) -> None:
    from question_bank.services.ai_tagging_service import classify_tagging_error

    assert classify_tagging_error(error) == expected


def test_classify_tagging_error_keeps_local_error_categories() -> None:
    from question_bank.services.ai_tagging_service import classify_tagging_error

    assert classify_tagging_error(ValueError("validation failed")) == "validation"
    assert classify_tagging_error(RuntimeError("cancelled mid-run")) == "unknown"
