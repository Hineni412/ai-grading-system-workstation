from __future__ import annotations

import json
from pathlib import Path

from backend.jobs.criterion_backfill import run_criterion_backfill_job
from backend.jobs.manager import JobContext
from backend.jobs.store import JobStore
from question_bank.database.schema import connect, initialize_database
from question_bank.training_criteria import TrainingCriterionModule


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


class FakeTaggingService:
    model = "synthetic-criterion-model"

    def __init__(self, protocol: FakeProtocol) -> None:
        self.protocol = protocol

    def _protocol_adapter(self):
        return self.protocol


def _seed(database: Path) -> None:
    initialize_database(database)
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
