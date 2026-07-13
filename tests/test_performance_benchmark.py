from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, is_dataclass
import pytest

from tools.performance.dataset import ScaleDefinition, build_benchmark_dataset
from tools.performance.runner import (
    BenchmarkRunError,
    InMemoryPerformanceSink,
    deterministic_projection,
    run_scale,
)
from tools.performance.scenarios import (
    BenchmarkScenario,
    ScenarioRequest,
    build_scenarios,
)


MICRO = ScaleDefinition("micro", 1, 2, 2, 4, 8, 2, 2)
EXPECTED_NAMES = {
    "health",
    "question_bank.papers",
    "question_bank.questions.default",
    "question_bank.questions.filtered",
    "question_bank.question.detail",
    "question_bank.question.asset",
    "question_bank.question.preview",
    "training.diagnosis",
    "training.plan.preview",
    "training.tasks",
    "training.task.detail",
    "graph.profiles",
    "graph.rows",
    "graph.evidence",
    "ops.self_check",
    "ops.backups",
}
SCOPE = {"mode": "class", "class_id": "CLASS-001"}
EXAM_SCOPE = {"mode": "cross_exam"}


@pytest.fixture(scope="module")
def micro_dataset(tmp_path_factory: pytest.TempPathFactory):
    return build_benchmark_dataset(tmp_path_factory.mktemp("benchmark") / "data", MICRO)


def _scenario_map(dataset) -> dict[str, BenchmarkScenario]:
    return {scenario.name: scenario for scenario in build_scenarios(dataset)}


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


def test_build_scenarios_has_exact_real_route_contract(micro_dataset) -> None:
    scenarios = build_scenarios(micro_dataset)

    assert len(scenarios) == 16
    assert {scenario.name for scenario in scenarios} == EXPECTED_NAMES
    assert len({scenario.name for scenario in scenarios}) == 16
    assert all(scenario.method in {"GET", "POST"} for scenario in scenarios)
    assert all(scenario.route_template.startswith("/") for scenario in scenarios)

    by_name = _scenario_map(micro_dataset)
    assert by_name["question_bank.question.detail"].route_template.endswith(
        "/{question_id}"
    )
    assert "{question_id}" in by_name["question_bank.question.asset"].route_template
    assert "{asset_index}" in by_name["question_bank.question.asset"].route_template
    assert "{question_id}" in by_name["question_bank.question.preview"].route_template
    assert "{preview_type}" in by_name["question_bank.question.preview"].route_template
    assert "{task_id}" in by_name["training.task.detail"].route_template


def test_scenario_requests_use_only_deterministic_allowlisted_inputs(
    micro_dataset,
) -> None:
    by_name = _scenario_map(micro_dataset)

    filtered = by_name["question_bank.questions.filtered"].build_request(
        micro_dataset
    )
    assert dict(filtered.params) == {
        "knowledge_point": "knowledge-01",
        "tag_status": "tagged",
        "sort": "difficulty",
        "page_size": "100",
    }

    for name in (
        "training.diagnosis",
        "training.plan.preview",
        "graph.profiles",
        "graph.rows",
        "graph.evidence",
    ):
        request = by_name[name].build_request(micro_dataset)
        assert request.json_body is not None
        assert request.json_body["scope"] == SCOPE
        assert request.json_body["exam_scope"] == EXAM_SCOPE

    evidence = by_name["graph.evidence"].build_request(micro_dataset)
    assert evidence.json_body == {
        "scope": SCOPE,
        "exam_scope": EXAM_SCOPE,
        "knowledge_key": micro_dataset.knowledge_key,
        "page": 1,
        "page_size": 100,
    }


def test_runner_overrides_only_the_brief_allowlist(micro_dataset) -> None:
    import tools.performance.runner as runner_module
    from backend.api.dependencies import (
        get_diagnosis_profile_service,
        get_graph_diagnosis_profile_service,
        get_ops_self_check_service,
        get_practice_plan_service,
        get_question_bank_read_service,
        get_training_task_service,
    )
    from path_manager import get_path_manager

    app = runner_module._build_app(micro_dataset, InMemoryPerformanceSink())

    assert set(app.dependency_overrides) == {
        get_path_manager,
        get_question_bank_read_service,
        get_diagnosis_profile_service,
        get_practice_plan_service,
        get_training_task_service,
    }
    assert get_graph_diagnosis_profile_service not in app.dependency_overrides
    assert get_ops_self_check_service not in app.dependency_overrides


def test_real_runner_collects_two_summaries_of_two_samples_without_ids(
    micro_dataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import path_manager

    sentinel = object()

    class ForbiddenDefaultPaths:
        def __init__(self) -> None:
            raise AssertionError("repository path configuration must not be constructed")

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
        assert all(summary.response_bytes.minimum >= 0 for summary in repetition.scenarios)
    assert deterministic_projection(result.repetitions[0]) == deterministic_projection(
        result.repetitions[1]
    )
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


def test_runner_stops_atomically_on_non_200(
    micro_dataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.performance.runner as runner_module

    scenario = BenchmarkScenario(
        name="fake.non_200",
        method="GET",
        route_template="/missing",
        build_request=lambda _dataset: ScenarioRequest("/missing"),
        count_records=lambda _response: 1,
        scale_driver="constant",
    )
    monkeypatch.setattr(runner_module, "build_scenarios", lambda _dataset: (scenario,))

    with pytest.raises(BenchmarkRunError, match=r"fake\.non_200:non_200"):
        runner_module.run_scale(
            micro_dataset,
            warmups=1,
            samples=1,
            repetitions=2,
        )


def test_runner_stops_atomically_when_sink_record_is_missing(
    micro_dataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.performance.runner as runner_module

    class MissingSink(InMemoryPerformanceSink):
        def pop(self, request_id: str):
            raise KeyError("missing request_id")

    monkeypatch.setattr(runner_module, "InMemoryPerformanceSink", MissingSink)

    with pytest.raises(BenchmarkRunError, match=r"health:missing_record"):
        runner_module.run_scale(
            micro_dataset,
            warmups=1,
            samples=1,
            repetitions=2,
        )


def test_runner_rejects_duplicate_request_ids_before_sending(
    micro_dataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.performance.runner as runner_module

    monkeypatch.setattr(
        runner_module,
        "_request_id",
        lambda **_kwargs: "p1-26-micro-duplicate",
    )

    with pytest.raises(BenchmarkRunError, match=r"health:duplicate_request_id"):
        runner_module.run_scale(
            micro_dataset,
            warmups=1,
            samples=1,
            repetitions=2,
        )
