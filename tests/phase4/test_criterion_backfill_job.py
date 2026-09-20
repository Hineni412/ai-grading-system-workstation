from __future__ import annotations

import json
from pathlib import Path

from backend.jobs.criterion_backfill import run_criterion_backfill_job
from backend.jobs.manager import JobContext
from backend.jobs.store import JobStore
from question_bank.database.schema import connect, initialize_database
from question_bank.training_criteria import TrainingCriterionModule
from tests.current_knowledge_support import install_current_knowledge


class FakeResponse:
    def __init__(self, question_id: int) -> None:
        self.output_text = json.dumps(
            {
                "results": [
                    {
                        "question_id": question_id,
                        "training_criteria": {
                            "schema_version": (
                                "training-criteria-draft-v1"
                            ),
                            "question_id": question_id,
                            "points": [
                                {
                                    "point_id": "p-relation",
                                    "target": "建立等量关系",
                                    "observable_evidence": (
                                        "列出正确方程"
                                    ),
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
                            "auxiliary_rules": [],
                            "rationale": "合成回填",
                            "confidence": 0.9,
                        },
                    }
                ]
            },
            ensure_ascii=False,
        )
        self.usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
        }


class FakeProtocol:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def responses(self, **kwargs):
        self.calls.append(kwargs)
        user_text = kwargs["kwargs"]["input"][1]["content"][0]["text"]
        payload = json.loads(user_text)
        return FakeResponse(payload["questions"][0]["question_id"])


_SKILL_KEY = "sk_bnu24_math_g8_upper_1_1_01"


class FakeTaggingService:
    model = "synthetic-criterion-model"

    def __init__(
        self,
        protocol: FakeProtocol,
        contract: dict | None = None,
    ) -> None:
        self.protocol = protocol
        self.contract = contract or {}
        self.contract_calls: list[dict] = []

    def _protocol_adapter(self):
        return self.protocol

    def taxonomy_contracts(self, contexts):
        self.contract_calls.append(dict(contexts))
        return {
            question_id: dict(self.contract) for question_id in contexts
        }


def _seed(database: Path) -> None:
    initialize_database(database)
    install_current_knowledge(database)
    with connect(database) as connection:
        paper_id = connection.execute(
            """
            INSERT INTO papers (title, import_status)
            VALUES ('P4-10 回填任务', 'completed')
            """
        ).lastrowid
        connection.execute(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type,
                question_text, answer_text
            ) VALUES (1, ?, '1', '计算题', '解方程 x+1=2', 'x=1')
            """,
            (paper_id,),
        )


def _context(
    store: JobStore,
    *,
    run_id: str,
    job_id: int,
) -> JobContext:
    return JobContext(
        job_id=job_id,
        job_type="criterion_backfill",
        payload={"run_id": run_id},
        store=store,
    )


def test_backfill_job_creates_draft_once_and_skips_existing_draft(
    tmp_path: Path,
) -> None:
    database = tmp_path / "data" / "databases" / "question_bank.db"
    data_root = tmp_path / "data"
    _seed(database)
    criterion = TrainingCriterionModule(database)
    protocol = FakeProtocol()
    store = JobStore(tmp_path / "jobs.db")
    first_run, _ = criterion.create_backfill_run(
        question_ids=(1,),
        request_token="a" * 32,
    )
    first_job = store.create_job(
        "criterion_backfill",
        {"run_id": first_run["run_id"]},
    )
    assert store.mark_running(first_job.id)

    first = run_criterion_backfill_job(
        context=_context(
            store,
            run_id=first_run["run_id"],
            job_id=first_job.id,
        ),
        question_bank_db_path=database,
        data_root=data_root,
        ai_service_factory=lambda: FakeTaggingService(protocol),
    )

    assert first["status"] == "succeeded"
    assert len(protocol.calls) == 1
    workspace = criterion.get_backfill_run(first_run["run_id"])
    assert workspace["items"][0]["status"] == "succeeded"

    second_run, _ = criterion.create_backfill_run(
        question_ids=(1,),
        request_token="b" * 32,
    )
    second_job = store.create_job(
        "criterion_backfill",
        {"run_id": second_run["run_id"]},
    )
    assert store.mark_running(second_job.id)
    second = run_criterion_backfill_job(
        context=_context(
            store,
            run_id=second_run["run_id"],
            job_id=second_job.id,
        ),
        question_bank_db_path=database,
        data_root=data_root,
        ai_service_factory=lambda: FakeTaggingService(protocol),
    )

    assert second["status"] == "succeeded"
    assert len(protocol.calls) == 1
    assert criterion.get_backfill_run(second_run["run_id"])["items"][0][
        "status"
    ] == "skipped"

    regenerate_run, _ = criterion.create_backfill_run(
        question_ids=(1,),
        request_token="c" * 32,
        mode="regenerate",
    )
    regenerate_job = store.create_job(
        "criterion_backfill",
        {"run_id": regenerate_run["run_id"]},
    )
    assert store.mark_running(regenerate_job.id)
    regenerated = run_criterion_backfill_job(
        context=_context(
            store,
            run_id=regenerate_run["run_id"],
            job_id=regenerate_job.id,
        ),
        question_bank_db_path=database,
        data_root=data_root,
        ai_service_factory=lambda: FakeTaggingService(protocol),
    )

    assert regenerated["status"] == "succeeded"
    assert len(protocol.calls) == 2
    assert criterion.get_backfill_run(regenerate_run["run_id"])["items"][
        0
    ]["status"] == "succeeded"


class LinkedEvidenceResponse:
    def __init__(self, question_id: int, term_id: str) -> None:
        self.output_text = json.dumps(
            {
                "results": [
                    {
                        "question_id": question_id,
                        "solution_evidence": {
                            "schema_version": "question-solution-evidence-v2",
                            "question_id": question_id,
                            "parts": [
                                {
                                    "part_id": "part-1",
                                    "response_mode": "process",
                                    "canonical_answer": "x=1",
                                    "full_answer": "移项得 x=1",
                                    "deduction_policy": ["按评分点判定"],
                                    "evidence_points": [
                                        {
                                            "evidence_point_id": "p1",
                                            "target": "建立等量关系",
                                            "observable_evidence": "列出方程",
                                            "justification": "题意",
                                            "answer_anchor": "x=1",
                                            "fine_term_links": [
                                                {
                                                    "fine_term_id": term_id,
                                                    "role": "direct",
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ],
                            "auxiliary_rules": [],
                            "rationale": "合成回填",
                        },
                    }
                ]
            },
            ensure_ascii=False,
        )
        self.usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
        }


class LinkedEvidenceProtocol:
    def __init__(self, term_id: str) -> None:
        self.term_id = term_id
        self.calls: list[dict[str, object]] = []

    def responses(self, **kwargs):
        self.calls.append(kwargs)
        user_text = kwargs["kwargs"]["input"][1]["content"][0]["text"]
        payload = json.loads(user_text)
        return LinkedEvidenceResponse(
            payload["questions"][0]["question_id"], self.term_id
        )


def test_backfill_supplies_taxonomy_contract_and_projects_links(
    tmp_path: Path,
) -> None:
    """§6 验收：回填两遍加载传 taxonomy_contracts，产出的证据链接入表。"""
    database = tmp_path / "data" / "databases" / "question_bank.db"
    data_root = tmp_path / "data"
    initialize_database(database)
    release_id = install_current_knowledge(
        database, taxonomy_revision=5
    )
    with connect(database) as connection:
        paper_id = connection.execute(
            "INSERT INTO papers (title, import_status)"
            " VALUES ('P6 回填候选表', 'completed')"
        ).lastrowid
        connection.execute(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type,
                question_text, answer_text
            ) VALUES (1, ?, '1', '解答题', '解方程 x+1=2', 'x=1')
            """,
            (paper_id,),
        )

    contract = {
        "taxonomy_revision": 5,
        "candidates": {
            "knowledge": [
                {
                    "id": _SKILL_KEY,
                    "name": "一元一次方程求解",
                    "usage": "direct_core",
                }
            ]
        },
    }
    protocol = LinkedEvidenceProtocol(_SKILL_KEY)
    service = FakeTaggingService(protocol, contract=contract)
    criterion = TrainingCriterionModule(database)
    store = JobStore(tmp_path / "jobs.db")
    run, _ = criterion.create_backfill_run(
        question_ids=(1,),
        request_token="d" * 32,
    )
    job = store.create_job(
        "criterion_backfill", {"run_id": run["run_id"]}
    )
    assert store.mark_running(job.id)

    result = run_criterion_backfill_job(
        context=_context(store, run_id=run["run_id"], job_id=job.id),
        question_bank_db_path=database,
        data_root=data_root,
        ai_service_factory=lambda: service,
    )

    assert result["status"] == "succeeded", result
    # 两遍加载确实先取了每题 tagging_context 再生成候选表。
    assert list(service.contract_calls[0]) == [1]
    with connect(database) as connection:
        rows = connection.execute(
            """
            SELECT stable_key, role, source_kind
            FROM evidence_point_knowledge_links
            WHERE question_id = 1 AND graph_release_id = ?
            """,
            (release_id,),
        ).fetchall()
    assert [
        (row["stable_key"], row["role"]) for row in rows
    ] == [(_SKILL_KEY, "direct")]
