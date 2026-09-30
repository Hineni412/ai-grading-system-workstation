from __future__ import annotations

import copy
import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Mapping

import pytest

from question_bank.database.schema import initialize_database
from question_bank.models.question import QuestionCreate
from question_bank.models.tag_schema import TaggingContext
from tests.question_bank_support import QuestionBankTestStore
from question_bank.taxonomy.governance import TaxonomyGovernance
from question_bank.solution_evidence import (
    CoreResolution,
    FineTermCoreMappingRepository,
    SolutionEvidenceProjectionWriter,
    SolutionEvidenceRepository,
    build_fine_term_mapping_baseline,
    install_fine_term_mapping_baseline,
)
from question_bank.training_criteria import (
    ConfigQuestionAnalysisSource,
    DeferredCombinedAnalysisBundle,
    GatewayBatchResponse,
    DeferredCombinedQuestionAnalysisModule,
    QuestionAnalysisInput,
    TaxonomyProjectionReviewRequired,
    question_analysis_input_from_config_source,
)


VOLUME_ID = "pep-7-up"
LEGACY_CATALOG_PATH = (
    Path(__file__).resolve().parents[2]
    / "question_bank"
    / "taxonomy"
    / "catalogs"
    / "tag_vocabulary_v2.json"
)


class Resolver:
    def resolve(self, fine_term_id: str) -> CoreResolution:
        if fine_term_id in {"kp_alg_linear_equation", "knowledge-fine-linear"}:
            return CoreResolution(
                status="resolved",
                stable_keys=("kp_alg_linear_equation",),
                reason="test",
            )
        return CoreResolution(status="unmapped", reason="test")


def _taxonomy_contract() -> dict[str, Any]:
    return {
        "taxonomy_revision": 2,
        "allowed_dimensions": ["knowledge"],
        "candidates": {
            "knowledge": [
                {
                    "id": "kp_alg_linear_equation",
                    "name": "一元一次方程",
                    "aliases": ["一次方程"],
                },
                {
                    "id": "knowledge-fine-linear",
                    "name": "移项解方程",
                    "aliases": [],
                },
            ]
        },
    }


def _question(
    question_id: int,
    *,
    source_ref: str = "Q1",
    question_type: str = "证明题",
    text: str = "证明等式成立。",
) -> QuestionAnalysisInput:
    return question_analysis_input_from_config_source(
        {
            "question_id": source_ref,
            "question_text": text,
            "answer_text": "由等式性质移项，结论成立。",
            "question_type": question_type,
        },
        question_id=question_id,
        curriculum_volume_id=VOLUME_ID,
        taxonomy_contract=_taxonomy_contract(),
    )


def _evidence_payload(
    question_id: int,
    *,
    invented_term: bool = False,
) -> dict[str, Any]:
    direct_id = "invented-term" if invented_term else "kp_alg_linear_equation"
    direct_name = "模型新造词" if invented_term else "一元一次方程"
    return {
        "schema_version": "question-solution-evidence-v1",
        "question_id": question_id,
        "parts": [
            {
                "part_id": "part-1",
                "label": "第1问",
                "response_mode": "process_required",
                "canonical_answer": "",
                "accepted_forms": [],
                "full_answer": "由等式性质移项并化简，得到结论。",
                "proof_obligations": ["说明所用等式性质", "写出结论"],
                "visual_requirements": [],
                "deduction_policy": ["缺少关键变形依据时该步骤未达成"],
                "allow_alternative_methods": True,
                "evidence_points": [
                    {
                        "evidence_point_id": "step-1",
                        "target": "正确移项",
                        "observable_evidence": "写出等价变形后的方程",
                        "fine_term_links": [
                            {
                                "fine_term_id": direct_id,
                                "fine_term_name": direct_name,
                                "role": "direct",
                            },
                            {
                                "fine_term_id": "knowledge-fine-linear",
                                "fine_term_name": "移项解方程",
                                "role": "supporting_prerequisite",
                            },
                        ],
                        "equivalent_rules": ["等价变形顺序可不同"],
                        "counterexamples": ["改变等号一侧符号但另一侧不变"],
                    }
                ],
            }
        ],
        "auxiliary_rules": ["符号等价即可"],
        "rationale": "按可观察解题过程拆分。",
        "confidence": 0.92,
    }


def _tag_payload() -> dict[str, Any]:
    return {
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
                "part_label": "第1问",
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
                "evidence": "一步方程即可求出未知数",
            }
        ],
        "taxonomy_revision": 2,
        "proposed_tags": [],
        "reason": "考查解方程。",
        "confidence": 0.9,
    }


def _combined_payload(
    question_id: int, *, invented_term: bool = False
) -> dict[str, Any]:
    return {
        "results": [
            {
                "question_id": question_id,
                "tag_analysis": _tag_payload(),
                "solution_evidence": _evidence_payload(
                    question_id,
                    invented_term=invented_term,
                ),
            }
        ]
    }


def _repairable_identity_mismatch_payload(
    question_id: int,
    *,
    repaired: bool = False,
) -> dict[str, Any]:
    payload = _combined_payload(question_id)
    payload["results"][0]["solution_evidence"] = {
        "schema_version": "question-solution-evidence-v2",
        "question_id": question_id if repaired else question_id + 100,
        "parts": [
            {
                "part_id": "part-1",
                "label": "第(1)问",
                "response_mode": "process_required",
                "canonical_answer": "",
                "accepted_forms": [],
                "full_answer": "先得到∠B=90°-x。",
                "proof_obligations": [],
                "visual_requirements": [],
                "deduction_policy": ["缺少该关系则本步未达成"],
                "allow_alternative_methods": True,
                "evidence_points": [
                    {
                        "evidence_point_id": "part-1-step-1",
                        "step_index": 1,
                        "target": "得到∠B的表达式",
                        "justification": "直角三角形两锐角互余",
                        "answer_anchor": "∠B=90°-x",
                        "observable_evidence": "写出∠B=90°-x",
                        "depends_on": [],
                        "fine_term_links": [],
                        "equivalent_rules": [],
                        "counterexamples": [],
                    }
                ],
            },
            {
                "part_id": "part-2",
                "label": "第(2)问",
                "response_mode": "process_required",
                "canonical_answer": "",
                "accepted_forms": [],
                "full_answer": "利用上一问结论，得到y=x/2。",
                "proof_obligations": [],
                "visual_requirements": [],
                "deduction_policy": ["缺少该关系则本步未达成"],
                "allow_alternative_methods": True,
                "evidence_points": [
                    {
                        "evidence_point_id": "part-2-step-1",
                        "step_index": 1,
                        "target": "推出y与x的关系",
                        "justification": "利用上一问结论和角的和差关系",
                        "answer_anchor": "y=x/2",
                        "observable_evidence": "写出y=x/2",
                        "depends_on": [] if repaired else ["part-1-step-1"],
                        "fine_term_links": [],
                        "equivalent_rules": ["x=2y"],
                        "counterexamples": [],
                    }
                ],
            },
        ],
        "auxiliary_rules": ["第(2)问可使用第(1)问结论"],
        "rationale": "按小问拆分独立判分证据。",
        "confidence": 0.9,
    }
    return payload


class QueueGateway:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[int, ...]] = []

    def analyze(self, batch, **_kwargs):
        self.calls.append(batch.question_ids)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return GatewayBatchResponse(
            payload=response,
            model_name="synthetic-combined-model",
        )


class ConcurrentGateway:
    max_parallel_requests = 2

    def __init__(self) -> None:
        self.calls: list[tuple[int, ...]] = []
        self.max_active = 0
        self._active = 0
        self._lock = threading.Lock()
        self._overlap = threading.Event()

    def analyze(self, batch, **_kwargs):
        with self._lock:
            self.calls.append(batch.question_ids)
            self._active += 1
            self.max_active = max(self.max_active, self._active)
            if self._active >= 2:
                self._overlap.set()
        try:
            # A serial implementation times out here and records a peak of one;
            # a bounded parallel implementation releases both calls immediately.
            self._overlap.wait(timeout=0.2)
            question_id = batch.question_ids[0]
            return GatewayBatchResponse(
                payload=_combined_payload(question_id),
                model_name="synthetic-concurrent-model",
            )
        finally:
            with self._lock:
                self._active -= 1


class DecreasingLimitGateway:
    def __init__(self) -> None:
        self.limit = 2
        self.second_started = threading.Event()
        self.release_second = threading.Event()
        self.third_started = threading.Event()

    @property
    def max_parallel_requests(self) -> int:
        return self.limit

    def analyze(self, batch, **_kwargs):
        question_id = batch.question_ids[0]
        if question_id == 1:
            assert self.second_started.wait(timeout=1)
            self.limit = 1
        elif question_id == 2:
            self.second_started.set()
            assert self.release_second.wait(timeout=2)
        elif question_id == 3:
            self.third_started.set()
        return GatewayBatchResponse(
            payload=_combined_payload(question_id),
            model_name="synthetic-decreasing-limit-model",
        )


def test_targeted_repair_context_survives_uncertain_retry_and_restart() -> None:
    source = ConfigQuestionAnalysisSource(
        "Q11",
        _question(11, source_ref="Q11"),
    )
    rejected = _repairable_identity_mismatch_payload(11)
    initial = DeferredCombinedQuestionAnalysisModule(
        gateway=QueueGateway([rejected]),
        resolver=Resolver(),
    ).analyze(
        operation_id="config-source-analysis:repair-timeout",
        curriculum_volume_id=VOLUME_ID,
        sources=(source,),
    )
    original_failure = initial.failures[0]

    uncertain = DeferredCombinedQuestionAnalysisModule(
        gateway=QueueGateway([TimeoutError("synthetic timeout after send")]),
        resolver=Resolver(),
    ).retry_failed(
        initial,
        sources=(source,),
        curriculum_volume_id=VOLUME_ID,
    )

    assert uncertain.status == "needs_resolution"
    assert uncertain.failed_source_refs == ()
    assert uncertain.uncertain_source_refs == ("Q11",)
    assert uncertain.failures == (original_failure,)
    restored = DeferredCombinedAnalysisBundle.from_dict(
        uncertain.to_dict(),
        resolver=Resolver(),
    )

    class ConfirmedRepairGateway(QueueGateway):
        def __init__(self) -> None:
            super().__init__([_repairable_identity_mismatch_payload(11, repaired=True)])
            self.repair_contexts: list[Mapping[str, Any]] = []

        def analyze(self, batch, **kwargs):
            self.repair_contexts.append(batch.questions[0].repair_context)
            return super().analyze(batch, **kwargs)

    gateway = ConfirmedRepairGateway()
    completed = DeferredCombinedQuestionAnalysisModule(
        gateway=gateway,
        resolver=Resolver(),
    ).retry_failed(
        restored,
        sources=(source,),
        curriculum_volume_id=VOLUME_ID,
        retry_source_refs=("Q11",),
        retry_uncertain=True,
    )

    assert completed.status == "succeeded"
    assert completed.failures == ()
    assert gateway.calls == [(11,)]
    assert gateway.repair_contexts[0]["previous_result"] == (rejected["results"][0])
    assert gateway.repair_contexts[0]["validation_error"] == (
        original_failure.validation_error
    )


def test_deferred_unknown_request_outcome_is_durable_and_never_normally_retried() -> (
    None
):
    source = ConfigQuestionAnalysisSource("Q1", _question(1, source_ref="Q1"))
    timeout_gateway = QueueGateway([TimeoutError("synthetic timeout after send")])

    uncertain = DeferredCombinedQuestionAnalysisModule(
        gateway=timeout_gateway,
        resolver=Resolver(),
    ).analyze(
        operation_id="config-source-analysis:unknown-timeout",
        curriculum_volume_id=VOLUME_ID,
        sources=(source,),
    )

    assert uncertain.status == "needs_resolution"
    assert uncertain.failed_source_refs == ()
    assert uncertain.uncertain_source_refs == ("Q1",)
    assert uncertain.requests[-1].status == "outcome_unknown"

    retry_gateway = QueueGateway([_combined_payload(1)])
    unchanged = DeferredCombinedQuestionAnalysisModule(
        gateway=retry_gateway,
        resolver=Resolver(),
    ).retry_failed(
        DeferredCombinedAnalysisBundle.from_dict(
            uncertain.to_dict(),
            resolver=Resolver(),
        ),
        sources=(source,),
        curriculum_volume_id=VOLUME_ID,
    )
    assert unchanged.to_dict() == uncertain.to_dict()
    assert retry_gateway.calls == []

    confirmed_gateway = QueueGateway([_combined_payload(1)])
    confirmed = DeferredCombinedQuestionAnalysisModule(
        gateway=confirmed_gateway,
        resolver=Resolver(),
    ).retry_failed(
        DeferredCombinedAnalysisBundle.from_dict(
            uncertain.to_dict(),
            resolver=Resolver(),
        ),
        sources=(source,),
        curriculum_volume_id=VOLUME_ID,
        retry_source_refs=("Q1",),
        retry_uncertain=True,
    )

    assert confirmed.status == "succeeded"
    assert confirmed.uncertain_source_refs == ()
    assert confirmed_gateway.calls == [(1,)]
    assert any(
        request.status == "failed"
        and request.request_id == uncertain.requests[-1].request_id
        for request in confirmed.requests
    )
    assert confirmed.requests[-1].status == "succeeded"
    assert confirmed.requests[-1].request_id != uncertain.requests[-1].request_id
    assert (
        DeferredCombinedAnalysisBundle.from_dict(
            confirmed.to_dict(),
            resolver=Resolver(),
        ).to_dict()
        == confirmed.to_dict()
    )


class SuccessfulTagWriter:
    def write(self, *_args, **_kwargs) -> Mapping[str, Any]:
        return {"saved": True}


class ReviewTagWriter:
    def write(self, *_args, **_kwargs) -> Mapping[str, Any]:
        raise TaxonomyProjectionReviewRequired("synthetic taxonomy review")
