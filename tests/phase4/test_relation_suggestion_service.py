from __future__ import annotations

import json
from pathlib import Path

import pytest

from question_bank.database.schema import initialize_database
from question_bank.relations.contracts import (
    KnowledgeRelation,
    RelationStatus,
    RelationType,
)
from question_bank.relations.repository import KnowledgeRelationRepository
from question_bank.relations.suggestion_service import (
    RelationPairCandidate,
    RelationSuggestionInvalid,
    RelationSuggestionOperationConflict,
    RelationSuggestionService,
    evaluate_relation_predictions,
)
from tests.current_knowledge_support import install_current_knowledge


GOLD_SET = (
    Path(__file__).resolve().parent / "fixtures" / "p4_00_gold_set.json"
)


class FakeRelationGateway:
    model_name = "fake-relation-model"
    model_version = "fake-v1"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def suggest_relations(
        self,
        batch,
        *,
        operation_id: str,
        prompt_version: str,
    ):
        self.calls.append(
            {
                "batch": batch,
                "operation_id": operation_id,
                "prompt_version": prompt_version,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


@pytest.fixture
def suggestion_store(
    tmp_path: Path,
) -> tuple[
    Path,
    RelationSuggestionService,
    KnowledgeRelationRepository,
]:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    install_current_knowledge(database)
    return (
        database,
        RelationSuggestionService(database),
        KnowledgeRelationRepository(database),
    )


def _candidate(
    candidate_id: str,
    source_key: str = "kp_alg_real_numbers",
    target_key: str = "kp_alg_equation_properties",
    *,
    allowed_types: tuple[RelationType, ...] = (RelationType.PARENT,),
) -> RelationPairCandidate:
    return RelationPairCandidate(
        candidate_id=candidate_id,
        source_key=source_key,
        target_key=target_key,
        allowed_types=allowed_types,
        evidence_summary="合成题目共现摘要，不含学生信息。",
    )


def _suggestion_response(
    candidates: list[RelationPairCandidate],
    *,
    confidence: float = 0.95,
    relation_type: RelationType = RelationType.PARENT,
    usage: tuple[int, int] = (120, 40),
):
    return {
        "items": [
            {
                "candidate_id": candidate.candidate_id,
                "decision": "suggest",
                "source_key": candidate.source_key,
                "target_key": candidate.target_key,
                "relation_type": relation_type.value,
                "reason": "合成模型理由",
                "confidence": confidence,
            }
            for candidate in candidates
        ],
        "usage": {
            "input_tokens": usage[0],
            "output_tokens": usage[1],
        },
    }


def test_candidate_scope_rejects_unknown_stable_key_before_any_request(
    suggestion_store,
) -> None:
    _database, service, _repository = suggestion_store

    with pytest.raises(
        RelationSuggestionInvalid,
        match="unknown or retired stable key",
    ):
        service.create_run(
            [_candidate("unknown", target_key="kp_not_governed")],
            operation_id="relation-operation-unknown",
            prompt_version="relation-prompt-v1",
        )


def test_operation_is_idempotent_and_changed_scope_is_rejected(
    suggestion_store,
) -> None:
    _database, service, _repository = suggestion_store
    candidates = [_candidate("pair-1")]

    first = service.create_run(
        candidates,
        operation_id="relation-operation-1",
        prompt_version="relation-prompt-v1",
    )
    repeated = service.create_run(
        candidates,
        operation_id="relation-operation-1",
        prompt_version="relation-prompt-v1",
    )
    assert repeated["run_id"] == first["run_id"]

    with pytest.raises(RelationSuggestionOperationConflict):
        service.create_run(
            [
                _candidate(
                    "pair-2",
                    target_key="kp_alg_linear_equation",
                )
            ],
            operation_id="relation-operation-1",
            prompt_version="relation-prompt-v1",
        )


def test_small_batches_store_only_suggestions_and_usage_metadata(
    suggestion_store,
) -> None:
    _database, service, repository = suggestion_store
    candidates = [
        _candidate("pair-1"),
        _candidate(
            "pair-2",
            "kp_alg_equation_properties",
            "kp_alg_linear_equation",
        ),
        _candidate(
            "pair-3",
            "kp_fun_linear",
            "kp_fun_coordinate_system",
        ),
    ]
    gateway = FakeRelationGateway(
        [
            _suggestion_response(candidates[:2], usage=(100, 30)),
            _suggestion_response(candidates[2:], usage=(60, 20)),
        ]
    )
    run = service.create_run(
        candidates,
        operation_id="relation-operation-batches",
        prompt_version="relation-prompt-v1",
    )

    finished = service.process_run(
        str(run["run_id"]),
        gateway,
        batch_size=2,
    )

    assert finished["status"] == "completed"
    assert finished["request_count"] == 2
    assert finished["usage"] == {"input_tokens": 160, "output_tokens": 50}
    assert [item["status"] for item in finished["items"]] == [
        "suggested",
        "suggested",
        "suggested",
    ]
    assert len(repository.list_relations(status=RelationStatus.SUGGESTED)) == 3
    assert repository.list_active_relations() == ()
    assert len(gateway.calls) == 2
    assert all(
        set(call["batch"][0])
        == {
            "candidate_id",
            "source_key",
            "target_key",
            "allowed_relation_types",
            "aggregate_evidence",
        }
        for call in gateway.calls
    )
    requests = service.request_records(str(run["run_id"]))
    assert [request["status"] for request in requests] == [
        "succeeded",
        "succeeded",
    ]
    assert all("api_key" not in json.dumps(request) for request in requests)


def test_low_confidence_is_not_saved_to_raise_coverage(
    suggestion_store,
) -> None:
    _database, service, repository = suggestion_store
    candidate = _candidate("low-confidence")
    gateway = FakeRelationGateway(
        [_suggestion_response([candidate], confidence=0.79)]
    )
    run = service.create_run(
        [candidate],
        operation_id="relation-operation-low-confidence",
        prompt_version="relation-prompt-v1",
    )

    finished = service.process_run(str(run["run_id"]), gateway)

    assert finished["items"][0]["status"] == "not_suggested"
    assert finished["items"][0]["outcome_code"] == "low_confidence"
    assert repository.list_relations() == ()


def test_active_conflict_is_flagged_but_never_confirmed(
    suggestion_store,
) -> None:
    _database, service, repository = suggestion_store
    active = repository.create_suggestion(
        KnowledgeRelation(
            "kp_alg_real_numbers",
            "kp_alg_equation_properties",
            RelationType.PARENT,
        ),
        source_kind="teacher",
        rationale="合成既有关系",
    )
    repository.transition(
        active.relation_id,
        expected_revision=active.revision,
        to_status=RelationStatus.CONFIRMED,
        actor_ref="teacher-synthetic",
        reason="确认合成既有关系",
    )
    candidate = _candidate(
        "reverse-conflict",
        "kp_alg_equation_properties",
        "kp_alg_real_numbers",
    )
    gateway = FakeRelationGateway([_suggestion_response([candidate])])
    run = service.create_run(
        [candidate],
        operation_id="relation-operation-conflict",
        prompt_version="relation-prompt-v1",
    )

    finished = service.process_run(str(run["run_id"]), gateway)

    assert finished["items"][0]["outcome_code"] == "conflict_flagged"
    saved = repository.get_relation(
        str(finished["items"][0]["relation_id"])
    )
    assert saved.status is RelationStatus.SUGGESTED
    assert saved.conflict_codes == ("reverse_conflict",)
    assert len(repository.list_active_relations()) == 1


def test_duplicate_output_from_another_operation_does_not_create_another_edge(
    suggestion_store,
) -> None:
    _database, service, repository = suggestion_store
    candidate = _candidate("same-pair")
    first_gateway = FakeRelationGateway([_suggestion_response([candidate])])
    first = service.create_run(
        [candidate],
        operation_id="relation-operation-first",
        prompt_version="relation-prompt-v1",
    )
    service.process_run(str(first["run_id"]), first_gateway)

    second_gateway = FakeRelationGateway([_suggestion_response([candidate])])
    second = service.create_run(
        [candidate],
        operation_id="relation-operation-second",
        prompt_version="relation-prompt-v1",
    )
    finished = service.process_run(str(second["run_id"]), second_gateway)

    assert finished["items"][0]["status"] == "not_suggested"
    assert finished["items"][0]["outcome_code"] == "duplicate"
    assert len(repository.list_relations()) == 1


@pytest.mark.parametrize(
    "response",
    [
        "bad-json",
        {
            "items": [
                {
                    "candidate_id": "unknown-candidate",
                    "decision": "none",
                    "reason": "未知候选",
                    "confidence": 0.2,
                }
            ]
        },
        {
            "items": [
                {
                    "candidate_id": "bad-response",
                    "decision": "suggest",
                    "source_key": "kp_not_governed",
                    "target_key": "kp_alg_equation_properties",
                    "relation_type": "parent",
                    "reason": "未知身份",
                    "confidence": 0.9,
                }
            ]
        },
    ],
)
def test_bad_structured_output_fails_once_without_automatic_retry(
    suggestion_store,
    response,
) -> None:
    _database, service, repository = suggestion_store
    candidate = _candidate("bad-response")
    gateway = FakeRelationGateway([response])
    run = service.create_run(
        [candidate],
        operation_id=f"relation-operation-bad-{type(response).__name__}",
        prompt_version="relation-prompt-v1",
    )

    failed = service.process_run(str(run["run_id"]), gateway)
    repeated = service.process_run(str(run["run_id"]), gateway)

    assert failed["status"] == "failed"
    assert failed["retryable"] is True
    assert failed["request_count"] == 1
    assert repeated["request_count"] == 1
    assert len(gateway.calls) == 1
    assert repository.list_relations() == ()


def test_gateway_failure_requires_explicit_retry_and_never_persists_secret(
    suggestion_store,
    caplog,
) -> None:
    _database, service, repository = suggestion_store
    candidate = _candidate("retryable")
    gateway = FakeRelationGateway(
        [
            RuntimeError("provider failed with sk-secret-value"),
            _suggestion_response([candidate]),
        ]
    )
    run = service.create_run(
        [candidate],
        operation_id="relation-operation-retry",
        prompt_version="relation-prompt-v1",
    )

    failed = service.process_run(str(run["run_id"]), gateway)
    assert failed["status"] == "failed"
    assert len(gateway.calls) == 1
    assert "sk-secret-value" not in caplog.text
    assert "sk-secret-value" not in json.dumps(
        service.get_run(str(run["run_id"])),
        ensure_ascii=False,
    )

    completed = service.retry_failed(str(run["run_id"]), gateway)
    assert completed["status"] == "completed"
    assert completed["request_count"] == 2
    assert len(gateway.calls) == 2
    assert len(repository.list_relations()) == 1


def test_cancel_and_interrupted_recovery_preserve_state_for_explicit_resume(
    suggestion_store,
) -> None:
    _database, service, _repository = suggestion_store
    candidate = _candidate("recoverable")
    run = service.create_run(
        [candidate],
        operation_id="relation-operation-recovery",
        prompt_version="relation-prompt-v1",
    )

    cancelled = service.cancel_run(str(run["run_id"]))
    assert cancelled["status"] == "cancelled"
    assert cancelled["items"][0]["status"] == "cancelled"

    gateway = FakeRelationGateway([_suggestion_response([candidate])])
    completed = service.retry_failed(str(run["run_id"]), gateway)
    assert completed["status"] == "completed"

    interrupted_candidate = _candidate(
        "interrupted",
        "kp_alg_equation_properties",
        "kp_alg_linear_equation",
    )
    interrupted_run = service.create_run(
        [interrupted_candidate],
        operation_id="relation-operation-interrupted",
        prompt_version="relation-prompt-v1",
    )
    claimed = service._claim_batch(
        str(interrupted_run["run_id"]),
        batch_size=1,
        model_name=gateway.model_name,
        model_version=gateway.model_version,
    )
    assert claimed is not None
    recovered = service.recover_interrupted(
        str(interrupted_run["run_id"])
    )
    assert recovered["status"] == "failed"
    assert recovered["items"][0]["outcome_code"] == "interrupted"
    assert recovered["request_count"] == 1
    assert service.request_records(
        str(interrupted_run["run_id"])
    )[0]["error_category"] == "interrupted"


def test_offline_gold_evaluation_meets_frozen_precision_and_coverage() -> None:
    gold = json.loads(GOLD_SET.read_text(encoding="utf-8"))
    samples = gold["relation_samples"]
    predictions = {
        str(sample["id"]): (
            {
                "decision": "suggest",
                "relation_type": sample["relation_type"],
            }
            if sample["kind"] == "positive"
            else {"decision": "none"}
        )
        for sample in samples
    }

    evaluation = evaluate_relation_predictions(samples, predictions)

    assert evaluation.precision == 1.0
    assert evaluation.coverage == 1.0
    assert evaluation.error_counts == {}
    assert evaluation.passed is True
