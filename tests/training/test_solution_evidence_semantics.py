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


def test_saved_evidence_is_visible_after_source_change_but_not_adopted(tmp_path):
    from question_bank.database.schema import connect
    from question_bank.solution_evidence.contracts import QuestionSolutionEvidence
    from question_bank.solution_evidence.knowledge_links import load_point_links
    from question_bank.training_criteria.analysis import solution_evidence_source_content_hash
    from tests.current_knowledge_support import install_current_knowledge
    path = tmp_path / "TEST-read-saved-evidence.db"
    initialize_database(path)
    install_current_knowledge(path)
    with connect(path) as conn:
        conn.execute("INSERT INTO papers(id,title,import_status) VALUES(1,'TEST-saved-evidence','ready')")
        conn.execute("INSERT INTO questions(id,paper_id,question_number,question_type,question_text,answer_text) VALUES(1,1,'1','解答题','TEST-current-question','TEST-current-answer')")
    original = _question(1)
    source = solution_evidence_source_content_hash(original)
    evidence = QuestionSolutionEvidence.from_model_dict(_evidence_payload(1), question_id=1,
        source_content_hash=source, resolver=Resolver())
    repository = SolutionEvidenceRepository(path)
    version = repository.save(evidence, source_kind="backfill", source_reference="TEST-history", created_by="test")
    before = path.read_bytes()
    visible = repository.latest(1, current_source_content_hash="f" * 64)
    assert visible["evidence"]["parts"]
    assert visible["status"] == "proposed"
    assert repository.load_current(1, source_content_hash="f" * 64, resolver=Resolver()) is None
    links = load_point_links(path, [version], None)[version]["step-1"]
    assert [(link.stable_key, link.role, link.source_kind) for link in links] == [
        ("kp_alg_linear_equation", "direct", "migrated_from_embedded"),
        ("kp_alg_linear_equation", "supporting_prerequisite", "migrated_from_embedded"),
    ]
    assert path.read_bytes() == before


class TrackedLinkRows(list):
    def __init__(self, rows):
        super().__init__(rows)
        self.visits = 0

    def __iter__(self):
        for row in super().__iter__():
            self.visits += 1
            yield row


@pytest.fixture
def point_metadata_rows():
    evidence_specs = [
        ("own", 1, "rejected", "r-other", "original"),
        ("missing-a", 1, "proposed", "r-current", "original"),
        ("missing-b", 1, "approved", "r-current", "original"),
        ("missing-q2", 2, "approved", "r-current", "original"),
        ("unmatched", 1, "approved", "r-current", "unmatched"),
        ("broken", 1, "approved", "r-current", None),
        ("old-job", 1, "approved", "", "original"),
        ("current-teacher", 1, "proposed", "r-current", "original"),
        ("current-job-older", 1, "approved", "r-current", "original"),
        ("current-job", 1, "approved", "r-current", "original"),
        ("current-job-latest", 1, "approved", "r-current", "original"),
        ("current-job-tie", 1, "approved", "r-current", "original"),
        ("rejected", 1, "rejected", "r-current", "original"),
        ("wrong-release", 1, "approved", "r-other", "original"),
        ("changed", 1, "approved", "r-current", "changed"),
        ("other-job", 2, "approved", "r-current", "original"),
        ("second-point", 1, "approved", "r-current", "original"),
    ]
    versions = {
        name: hashlib.sha256(f"TEST-point-links:{name}".encode()).hexdigest()
        for name in [*(spec[0] for spec in evidence_specs), "unknown"]
    }
    evidence_rows = []
    questions = {}
    for name, question_id, status, release, target in evidence_specs:
        questions[name] = question_id
        payload = _evidence_payload(question_id)
        part = payload["parts"][0]
        part["evidence_points"][0]["target"] = target
        part["evidence_points"][0]["fine_term_links"] = [{"fine_term_id": name}]
        second = copy.deepcopy(part["evidence_points"][0])
        second.update(evidence_point_id="step-2", target=f"second-{target}")
        part["evidence_points"].append(second)
        evidence_rows.append({
            "evidence_version_id": versions[name],
            "question_id": question_id,
            "status": status,
            "graph_release_id": release,
            "evidence_json": json.dumps(payload) if target is not None else "not-json",
        })
    link_specs = [
        ("own", "step-1", "own", "teacher", "r-other", "2035", 900),
        ("old-job", "step-1", "old", "link_job", "r-old", "2030", 400),
        ("current-teacher", "step-1", "teacher", "teacher", "r-current", "2026", 500),
        ("current-job-older", "step-1", "job-older", "link_job", "r-current", "2024", 9),
        ("current-job", "step-1", "job", "link_job", "r-current", "2025", 10),
        ("current-job-latest", "step-1", "job-latest", "link_job", "r-current", "2025", 12),
        ("current-job-tie", "step-1", "job-tie", "link_job", "r-current", "2025", 12),
        ("current-job-latest", "step-1", "job-support", "link_job", "r-current", "2025", 11),
        ("current-job-latest", "step-1", "teacher-in-job-group", "teacher", "r-current", "2025", 10),
        ("rejected", "step-1", "rejected", "link_job", "r-current", "2036", 901),
        ("wrong-release", "step-1", "wrong-release", "link_job", "r-current", "2028", 902),
        ("changed", "step-1", "changed", "link_job", "r-current", "2037", 903),
        ("other-job", "step-1", "other-question", "link_job", "r-current", "2038", 904),
        ("second-point", "step-2", "second", "teacher", "r-current", "2025", 13),
    ]
    link_rows = []
    for name, point_id, term, kind, release, year, order in link_specs:
        supporting = term == "job-support"
        link_rows.append({
            "evidence_version_id": versions[name],
            "question_id": questions[name],
            "evidence_point_id": point_id,
            "graph_release_id": release,
            "role": "supporting_prerequisite" if supporting else "direct",
            "term_id": term,
            "stable_key": f"sk_TEST_{term}",
            "resolution_status": "resolved",
            "weight": 0.25 if supporting else 0.75,
            "source_kind": kind,
            "created_at": f"{year}-01-01T00:00:00",
            "link_order": order,
        })
    return versions, evidence_rows, link_rows


@pytest.mark.parametrize(
    ("wanted", "excluded", "chosen_terms"),
    [
        ("r-current", (), ("job-latest", "job-support", "teacher-in-job-group")),
        ("r-current", ("current-job-latest", "current-job-tie"), ("job",)),
        ("r-current", ("current-job-latest", "current-job-tie", "current-job"), ("job-older",)),
        ("r-current", ("current-job-latest", "current-job-tie", "current-job", "current-job-older"), ("teacher",)),
        ("", (), ("old",)),
    ],
)
def test_adopted_point_metadata_preserves_scope_priority_and_order(
    point_metadata_rows, wanted, excluded, chosen_terms
):
    from question_bank.solution_evidence.knowledge_links import _adopted_point_metadata

    versions, evidence_rows, link_rows = point_metadata_rows
    excluded_ids = {versions[name] for name in excluded}
    rows = TrackedLinkRows([
        row for row in link_rows if row["evidence_version_id"] not in excluded_ids
    ])
    names = ["own", "missing-a", "missing-b", "missing-q2", "unmatched", "broken", "unknown", "missing-a"]
    result = _adopted_point_metadata(
        [versions[name] for name in names], evidence_rows, rows, wanted
    )
    by_term = {row["term_id"]: row for row in link_rows}
    expected = [by_term["own"]]
    for name in ("missing-a", "missing-b"):
        expected.extend({**by_term[term], "evidence_version_id": versions[name]}
                        for term in (*chosen_terms, "second"))
    expected.append({**by_term["other-question"], "evidence_version_id": versions["missing-q2"]})
    expected.extend({**by_term[term], "evidence_version_id": versions["missing-a"]}
                    for term in (*chosen_terms, "second"))
    assert result == expected
    assert result[0] is by_term["own"]
    assert rows.visits <= 2 * len(rows)


def test_adopted_point_metadata_keeps_invalid_order_failure(point_metadata_rows):
    from question_bank.solution_evidence.knowledge_links import _adopted_point_metadata

    versions, evidence_rows, link_rows = point_metadata_rows
    link_rows[5]["link_order"] = "invalid-order"
    with pytest.raises(ValueError, match="invalid-order"):
        _adopted_point_metadata([versions["missing-a"]], evidence_rows, link_rows, "r-current")


def test_load_point_links_keeps_saved_and_fallback_links_without_repeated_row_scans(
    tmp_path, monkeypatch, point_metadata_rows
):
    from question_bank.database.schema import connect
    from question_bank.solution_evidence import knowledge_links
    from question_bank.solution_evidence.knowledge_links import KnowledgeLink

    versions, evidence_rows, link_rows = point_metadata_rows
    path = tmp_path / "TEST-point-link-index.db"
    initialize_database(path)
    releases = {name: f"kgr_TEST_{name}" for name in ("r-current", "r-other", "r-old")}
    current = releases["r-current"]
    evidence_rows = [{**row, "graph_release_id": releases.get(row["graph_release_id"]),
                      "evidence_json": "null" if row["evidence_json"] == "not-json" else row["evidence_json"]}
                     for row in evidence_rows]
    link_rows = [{**row, "graph_release_id": releases[row["graph_release_id"]]} for row in link_rows]
    with connect(path) as conn:
        conn.executemany(
            """INSERT INTO knowledge_graph_releases(
                release_id,schema_version,taxonomy_revision,content_hash,payload_json,status,source_reference,created_by)
               VALUES(?,'knowledge-graph-release-v1',1,?,'{}','retired','TEST-point-links','test')""",
            [(release, hashlib.sha256(release.encode()).hexdigest()) for release in releases.values()],
        )
        conn.execute("INSERT INTO papers(id,title,import_status) VALUES(1,'TEST-point-links','ready')")
        conn.executemany(
            """INSERT INTO questions(id,paper_id,question_number,question_type,question_text,answer_text)
               VALUES(?,1,?,'解答题','TEST-question','TEST-answer')""",
            [(question_id, str(question_id)) for question_id in (1, 2)],
        )
        conn.executemany(
            """INSERT INTO question_solution_evidence_versions(
                evidence_version_id,question_id,source_content_hash,schema_version,
                content_hash,evidence_json,status,source_kind,source_reference,created_by,graph_release_id)
               VALUES(?,? ,?,'question-solution-evidence-v1',?, ?,?,'backfill',?,'test',?)""",
            [(
                row["evidence_version_id"], row["question_id"], "a" * 64, "b" * 64,
                row["evidence_json"], row["status"], f'TEST-{row["evidence_version_id"]}',
                row["graph_release_id"],
            ) for row in evidence_rows],
        )
        conn.executemany(
            """INSERT INTO evidence_point_knowledge_links(
                evidence_version_id,question_id,part_id,evidence_point_id,graph_release_id,
                role,term_id,stable_key,resolution_status,weight,source_kind,created_at)
               VALUES(?,?,'part-1',?,?,?,?,?,?,?,?,?)""",
            [tuple(row[key] for key in (
                "evidence_version_id", "question_id", "evidence_point_id", "graph_release_id",
                "role", "term_id", "stable_key", "resolution_status", "weight", "source_kind", "created_at",
            )) for row in link_rows],
        )
    before = path.read_bytes()
    fetched = []
    original_rows_as_dicts = knowledge_links._rows_as_dicts

    def tracked_rows_as_dicts(cursor):
        rows = TrackedLinkRows(original_rows_as_dicts(cursor))
        fetched.append(rows)
        return rows

    monkeypatch.setattr(knowledge_links, "_rows_as_dicts", tracked_rows_as_dicts)
    names = ["own", "current-teacher", "current-job-older", "current-job",
             "current-job-latest", "current-job-tie", "missing-a", "missing-b",
             "missing-q2", "unmatched", "broken", "unknown", "missing-a"]
    result = knowledge_links.load_point_links(path, [versions[name] for name in names], current)
    assert set(result) == {versions[name] for name in names if name not in {"unmatched", "broken", "unknown"}}
    expected_point = (
        KnowledgeLink("job-latest", "sk_TEST_job-latest", "direct", 0.75,
                      source_kind="link_job", graph_release_id=current),
        KnowledgeLink("job-support", "sk_TEST_job-support", "supporting_prerequisite", 0.25,
                      source_kind="link_job", graph_release_id=current),
    )
    expected_second = (KnowledgeLink(
        "second", "sk_TEST_second", "direct", 0.75,
        source_kind="teacher", graph_release_id=current,
    ),)
    for name in ("missing-a", "missing-b"):
        assert list(result[versions[name]]) == ["step-1", "step-2"]
        assert result[versions[name]] == {"step-1": expected_point, "step-2": expected_second}
    assert result[versions["own"]] == {"step-1": (KnowledgeLink(
        "own", "sk_TEST_own", "direct", 0.75,
        source_kind="teacher", graph_release_id=releases["r-other"],
    ),)}
    assert result[versions["missing-q2"]] == {"step-1": (KnowledgeLink(
        "other-question", "sk_TEST_other-question", "direct", 0.75,
        source_kind="link_job", graph_release_id=current,
    ),)}
    assert len(fetched) == 3
    assert fetched[0].visits <= 2 * len(fetched[0])
    assert fetched[2].visits <= 2 * len(fetched[2])
    without_release = knowledge_links.load_point_links(path, [versions["missing-a"]], None)
    assert without_release[versions["missing-a"]]["step-1"] == (KnowledgeLink(
        "old", "sk_TEST_old", "direct", 0.75,
        source_kind="link_job", graph_release_id=releases["r-old"],
    ),)
    assert path.read_bytes() == before


def test_load_point_links_memo_tracks_materials_version_and_bypasses_without_versions(
    monkeypatch,
):
    """The per-process memo follows the materials data-version token; without
    version coverage every call reads again, as before."""
    from question_bank.solution_evidence import knowledge_links

    calls = []
    monkeypatch.setattr(
        knowledge_links,
        "_load_point_links",
        lambda db_path, ids, release, **kw: calls.append(tuple(ids)) or {"v": {}},
    )
    token = {"value": ("db", "dv", 1)}
    monkeypatch.setattr(
        knowledge_links,
        "data_version_token",
        lambda *args, **kwargs: token["value"],
    )
    knowledge_links._POINT_LINKS_CACHE._entries.clear()

    knowledge_links.load_point_links("db", ["e1"], "r1")
    knowledge_links.load_point_links("db", ["e1"], "r1")
    assert len(calls) == 1
    knowledge_links.load_point_links("db", ["e1"], "r2")
    assert len(calls) == 2

    token["value"] = ("db", "dv", 2)
    knowledge_links.load_point_links("db", ["e1"], "r1")
    assert len(calls) == 3

    token["value"] = None
    knowledge_links.load_point_links("db", ["e1"], "r1")
    knowledge_links.load_point_links("db", ["e1"], "r1")
    assert len(calls) == 5
    knowledge_links._POINT_LINKS_CACHE._entries.clear()
