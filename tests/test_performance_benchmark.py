from __future__ import annotations

from dataclasses import fields, is_dataclass
from concurrent.futures import ThreadPoolExecutor
import pytest

from tools.performance.dataset import ScaleDefinition, build_benchmark_dataset
from tools.performance.runner import (
    BenchmarkRunError,
    deterministic_projection,
    run_scale,
)
from tools.performance.scenarios import (
    BenchmarkScenario,
    ScenarioRequest,
    build_scenarios,
)


MICRO = ScaleDefinition("micro", 1, 2, 2, 4, 8, 2)
EXPECTED_NAMES = {
    "health",
    "question_bank.papers",
    "question_bank.questions.default",
    "question_bank.questions.filtered",
    "question_bank.question.detail",
    "question_bank.question.asset",
    "question_bank.question.preview",
    "training.diagnosis",
    "graph.query",
    "graph.evidence",
    "ops.self_check",
    "ops.backups",
}
SCOPE = {"mode": "class", "class_id": "CLASS-001"}
EXAM_SCOPE = {"mode": "cross_exam"}


@pytest.fixture(scope="module")
def micro_dataset(tmp_path_factory: pytest.TempPathFactory):
    return build_benchmark_dataset(tmp_path_factory.mktemp("benchmark") / "data", MICRO)


def _contains_field_named(value: object, forbidden: str) -> bool:
    if is_dataclass(value):
        if any(field.name == forbidden for field in fields(value)):
            return True
        return any(
            _contains_field_named(getattr(value, field.name), forbidden)
            for field in fields(value)
        )
    if isinstance(value, (tuple, list)):
        return any(_contains_field_named(item, forbidden) for item in value)
    if isinstance(value, dict):
        return forbidden in value or any(
            _contains_field_named(item, forbidden) for item in value.values()
        )
    return False


def test_real_runner_collects_two_summaries_of_two_samples_without_ids(
    micro_dataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import path_manager

    sentinel = object()

    class ForbiddenDefaultPaths:
        def __init__(self) -> None:
            raise AssertionError(
                "repository path configuration must not be constructed"
            )

    monkeypatch.setattr(path_manager, "_instance", sentinel)
    monkeypatch.setattr(path_manager, "PathManager", ForbiddenDefaultPaths)

    result = run_scale(micro_dataset, warmups=1, samples=2, repetitions=2)

    assert result.repeatability == "passed"
    assert len(result.repetitions) == 2
    for repetition in result.repetitions:
        assert {summary.name for summary in repetition.scenarios} == EXPECTED_NAMES
        assert all(summary.status_code == 200 for summary in repetition.scenarios)
        assert all(summary.sample_count == 2 for summary in repetition.scenarios)
        assert all(summary.latency_ms.minimum >= 0 for summary in repetition.scenarios)
        assert all(
            summary.response_bytes.minimum >= 0 for summary in repetition.scenarios
        )
    assert deterministic_projection(result.repetitions[0]) == deterministic_projection(
        result.repetitions[1]
    )
    for scenario_name in (
        "question_bank.papers",
        "question_bank.questions.filtered",
    ):
        response_counts = [
            next(
                summary.response_records
                for summary in repetition.scenarios
                if summary.name == scenario_name
            )
            for repetition in result.repetitions
        ]
        assert all(summary.minimum > 0 for summary in response_counts)
        assert response_counts[0] == response_counts[1]
    scale_projection = deterministic_projection(result)
    assert scale_projection["manifest"]["scale"] == "micro"
    assert scale_projection["manifest"]["seed"] == 126
    assert len(scale_projection["repetitions"]) == 2
    assert "latency" not in repr(scale_projection).casefold()
    assert not _contains_field_named(result, "request_id")
    assert "p1-26-" not in repr(result)
    assert path_manager._instance is sentinel


def test_temporary_path_provider_does_not_leak_to_unrelated_threads(
    micro_dataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import path_manager
    import tools.performance.runner as runner_module

    sentinel = object()
    monkeypatch.setattr(path_manager, "get_path_manager", lambda: sentinel)
    original = path_manager.get_path_manager

    with runner_module._temporary_path_provider(micro_dataset.paths):
        assert path_manager.get_path_manager() is micro_dataset.paths
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(path_manager.get_path_manager).result() is sentinel

    assert path_manager.get_path_manager is original
