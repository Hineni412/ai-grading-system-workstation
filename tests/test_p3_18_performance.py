from __future__ import annotations

import json
from pathlib import Path
import tempfile

import pytest


def test_p3_18_candidate_selection_requires_both_latency_gates() -> None:
    from tools.performance.evidence_optimization_report import (
        Timing,
        qualifies,
    )

    assert qualifies(Timing(100, 120), Timing(70, 115)) is True
    assert qualifies(Timing(100, 120), Timing(85, 110)) is False
    assert qualifies(Timing(100, 120), Timing(70, 127)) is False


def test_p3_18_crop_experiment_uses_generated_data_and_exact_output(
    tmp_path: Path,
) -> None:
    from tools.benchmark_p3_18 import run_crop_experiment

    evidence = run_crop_experiment(
        tmp_path,
        warmups=1,
        samples=5,
        image_width=1200,
        image_height=1600,
    )

    assert evidence.name == "review_crop_cache"
    assert evidence.before is not None
    assert evidence.after is not None
    assert evidence.output_equivalent is True
    assert evidence.disk_bytes > 0
    assert evidence.peak_memory_bytes > 0
    assert evidence.dataset["source"] == "generated"
    assert evidence.dataset["image_count"] == 1
    assert evidence.dataset["samples"] == 5


def test_p3_18_report_contract_has_four_decisions_and_no_absolute_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.benchmark_p3_18 as benchmark
    from tools.performance.evidence_optimization_report import (
        CandidateEvidence,
        render_json,
        render_markdown,
    )

    original_hot_sql_evidence = benchmark._hot_sql_evidence

    def build_rejected_hot_sql_evidence(
        question_bank_db_path: Path,
        *,
        samples: int,
    ) -> CandidateEvidence:
        with monkeypatch.context() as sql_gate:
            sql_gate.setattr(benchmark, "qualifies", lambda *_args: False)
            return original_hot_sql_evidence(
                question_bank_db_path,
                samples=samples,
            )

    monkeypatch.setattr(
        benchmark,
        "_hot_sql_evidence",
        build_rejected_hot_sql_evidence,
    )

    report = benchmark.build_p3_18_report(
        tmp_path,
        code_sha="a" * 40,
        warmups=1,
        samples=5,
        image_width=1200,
        image_height=1600,
    )
    payload = json.loads(render_json(report))
    markdown = render_markdown(report)

    assert payload["package"] == "P3-18"
    assert {item["name"] for item in payload["candidates"]} == {
        "review_crop_cache",
        "tag_projection_request_memo",
        "image_compression_request_memo",
        "hot_sql_index",
    }
    assert all(item["decision"] in {"selected", "rejected"} for item in payload["candidates"])
    by_name = {item["name"]: item for item in payload["candidates"]}
    tag = by_name["tag_projection_request_memo"]
    assert tag["before"] is not None
    assert tag["dataset"]["source"] == "generated_public_diagnosis_requests"
    assert tag["dataset"]["request_samples"] == 5
    assert tag["dataset"]["maximum_request_local_repeat_count"] == 0
    image = by_name["image_compression_request_memo"]
    assert image["before"] is not None
    assert image["after"] is not None
    assert image["decision"] == "selected"
    assert image["output_equivalent"] is True
    assert image["dataset"]["source"] == "generated_public_hybrid_retry_requests"
    assert image["dataset"]["request_samples"] == 5
    assert image["dataset"]["attempts_per_request"] == [3, 3, 3, 3, 3]
    assert image["dataset"]["maximum_repeated_image_identities"] > 0
    assert image["dataset"]["maximum_memo_entries"] <= 3
    assert image["peak_memory_bytes"] > 0
    assert image["dataset"]["peak_memory_before_bytes"] > 0
    assert image["dataset"]["peak_memory_after_bytes"] > 0
    sql = by_name["hot_sql_index"]
    assert sql["before"] is not None
    assert sql["after"] is not None
    assert sql["dataset"]["source"] == "generated_public_question_list_requests"
    assert sql["dataset"]["query_plan_before"]
    assert sql["dataset"]["query_plan_after"]
    assert sql["dataset"]["candidate_index_create_ms"] >= 0
    assert sql["dataset"]["write_p50_before_ms"] >= 0
    assert sql["dataset"]["write_p50_after_ms"] >= 0
    assert isinstance(sql["dataset"]["candidate_disk_delta_bytes"], int)
    assert "p50" in markdown
    assert "p95" in markdown
    assert str(tmp_path.resolve()) not in render_json(report)
    assert str(tmp_path.resolve()) not in markdown


def test_p3_18_hot_sql_benchmark_executes_each_measured_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import question_bank.services.question_read_service as read_module
    import tools.benchmark_p3_18 as benchmark
    from tools.performance.dataset import (
        ScaleDefinition,
        build_benchmark_dataset,
    )

    micro = ScaleDefinition("p3_18_sql_micro", 1, 2, 2, 4, 8, 2, 2)
    dataset = build_benchmark_dataset(tmp_path / "dataset", micro, seed=318)
    original = read_module.QuestionBankReadService._list_questions
    executed_queries = 0
    samples = 2

    def counted_list_questions(
        service: read_module.QuestionBankReadService,
        filters: read_module.QuestionReadFilters,
    ) -> read_module.QuestionReadPage:
        nonlocal executed_queries
        executed_queries += 1
        return original(service, filters)

    monkeypatch.setattr(
        read_module.QuestionBankReadService,
        "_list_questions",
        counted_list_questions,
    )
    monkeypatch.setattr(benchmark, "qualifies", lambda *_args: False)

    evidence = benchmark._hot_sql_evidence(
        dataset.paths.qb_db_path,
        samples=samples,
    )

    assert executed_queries == (2 * samples) + 1
    assert evidence.dataset["query_plan_before"]
    assert evidence.dataset["query_plan_after"]


def test_p3_18_cli_uses_system_temp_and_cleans_controlled_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.benchmark_p3_18 as benchmark

    system_temp = tmp_path / "system-temp"
    worktree = tmp_path / "worktree"
    system_temp.mkdir()
    worktree.mkdir()
    captured: list[Path] = []

    def fail_after_capture(root: Path, **_kwargs: object) -> object:
        captured.append(Path(root))
        raise RuntimeError("controlled benchmark failure")

    monkeypatch.chdir(worktree)
    monkeypatch.setattr(tempfile, "tempdir", str(system_temp))
    monkeypatch.setattr(benchmark, "_code_sha", lambda: "a" * 40)
    monkeypatch.setattr(benchmark, "build_p3_18_report", fail_after_capture)

    result = benchmark.main(
        [
            "--warmups",
            "1",
            "--samples",
            "1",
            "--image-width",
            "20",
            "--image-height",
            "20",
        ]
    )

    assert result == 1
    assert len(captured) == 1
    assert captured[0].parent == system_temp
    assert not captured[0].exists()
    assert list(worktree.glob("p3-18-benchmark-*")) == []
