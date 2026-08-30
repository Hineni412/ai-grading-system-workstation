from __future__ import annotations

import pytest

from api_profiles import ApiProfileStore
from backend.llm.execution import LLMExecutionGovernorRegistry
from backend.model_profiles import (
    ModelProfileInvalid,
    ModelProfileNotFound,
    ModelProfileService,
)


def test_model_profile_publishes_safe_default_request_speed_settings(
    tmp_path,
) -> None:
    service = ModelProfileService(ApiProfileStore(tmp_path / "profiles.json"))

    state = service.upsert(
        "校内模型",
        {
            "api_key": "secret",
            "base_url": "https://example.test/v1",
            "ocr_model": "ocr",
            "grading_model": "grading",
        },
    )

    profile = state["profiles"][0]
    assert profile["request_speed_mode"] == "automatic"
    assert profile["max_concurrent_requests"] == 20
    assert profile["requests_per_minute"] == 1000
    assert "api_key" not in profile


def test_model_profile_saves_custom_request_speed_settings(tmp_path) -> None:
    service = ModelProfileService(ApiProfileStore(tmp_path / "profiles.json"))

    state = service.upsert(
        "高并发模型",
        {
            "api_key": "secret",
            "base_url": "https://example.test/v1",
            "ocr_model": "ocr",
            "grading_model": "grading",
            "request_speed_mode": "custom",
            "max_concurrent_requests": 37,
            "requests_per_minute": 10_000,
        },
    )

    assert state["active_profile"] == {
        "name": "高并发模型",
        "base_url": "https://example.test/v1",
        "has_api_key": True,
        "ocr_model": "ocr",
        "grading_model": "grading",
        "config_base_url": "",
        "has_config_api_key": False,
        "config_model": "",
        "teaching_prep_model": "",
        "class_teacher_model": "",
        "request_speed_mode": "custom",
        "max_concurrent_requests": 37,
        "requests_per_minute": 10_000,
        "max_auto_retries": None,
        "request_timeout_seconds": None,
        "batch_enabled": False,
        "batch_model": "",
        "batch_base_url": "",
        "has_batch_api_key": False,
    }


def test_four_work_types_can_use_different_saved_api_sites(tmp_path) -> None:
    store = ApiProfileStore(tmp_path / "profiles.json")
    service = ModelProfileService(store)
    service.upsert("站点甲", {
        "api_key": "key-a", "base_url": "https://a.test/v1",
        "grading_model": "legacy-a",
    })
    service.upsert("站点乙", {
        "api_key": "key-b", "base_url": "https://b.test/v1",
        "grading_model": "legacy-b",
    })

    state = service.update_task_bindings({
        "content_generation": {"profile_name": "站点甲", "model": "content-a"},
        "grading": {"profile_name": "站点乙", "model": "grading-b"},
        "teaching_prep": {"profile_name": "站点甲", "model": "prep-a"},
        "class_teacher": {"profile_name": "站点乙", "model": "teacher-b"},
    })

    assert state["task_bindings"]["grading"] == {
        "profile_name": "站点乙", "model": "grading-b",
    }
    grading = store.profile_for_task("grading")
    assert grading["base_url"] == "https://b.test/v1"
    assert grading["api_key"] == "key-b"
    assert grading["ocr_model"] == grading["grading_model"] == "grading-b"
    content = store.profile_for_task("content_generation")
    assert content["config_base_url"] == "https://a.test/v1"
    assert content["config_api_key"] == "key-a"
    assert content["config_model"] == "content-a"


def test_delete_profile_removes_its_saved_task_bindings(tmp_path) -> None:
    store = ApiProfileStore(tmp_path / "profiles.json")
    service = ModelProfileService(store)
    service.upsert("站点甲", {
        "api_key": "key-a", "base_url": "https://a.test/v1",
        "grading_model": "model-a",
    })
    service.upsert("站点乙", {
        "api_key": "key-b", "base_url": "https://b.test/v1",
        "grading_model": "model-b",
    })
    service.update_task_bindings({
        "content_generation": {"profile_name": "站点甲", "model": "content-a"},
        "grading": {"profile_name": "站点乙", "model": "grading-b"},
        "teaching_prep": {"profile_name": "站点甲", "model": "prep-a"},
        "class_teacher": {"profile_name": "站点乙", "model": "teacher-b"},
    })

    state = service.delete("站点甲")

    assert [profile["name"] for profile in state["profiles"]] == ["站点乙"]
    assert state["task_bindings"]["grading"]["model"] == "grading-b"
    assert state["task_bindings"]["teaching_prep"] == {
        "profile_name": "站点乙",
        "model": "model-b",
    }
    assert "teaching_prep" not in store.load_task_bindings()
    with pytest.raises(ModelProfileNotFound):
        service.delete("站点甲")


def test_model_profile_reports_shared_runtime_execution_status(tmp_path) -> None:
    governors = LLMExecutionGovernorRegistry()
    service = ModelProfileService(
        ApiProfileStore(tmp_path / "profiles.json"),
        execution_governors=governors,
    )
    service.upsert(
        "高并发模型",
        {
            "api_key": "secret",
            "base_url": "https://example.test/v1",
            "ocr_model": "ocr",
            "grading_model": "grading",
            "request_speed_mode": "custom",
            "max_concurrent_requests": 37,
            "requests_per_minute": 10_000,
        },
    )

    assert service.execution_status("高并发模型") == {
        "mode": "custom",
        "configured_max_in_flight": 37,
        "effective_max_in_flight": 37,
        "requests_per_minute": 10_000,
        "active": 0,
        "queued": 0,
        "peak_active": 0,
        "physical_request_count": 0,
        "limiting_reason": "configured",
    }


@pytest.mark.parametrize(
    "updates",
    [
        {"request_speed_mode": "turbo"},
        {"request_speed_mode": "custom", "max_concurrent_requests": 0},
        {"request_speed_mode": "custom", "max_concurrent_requests": 101},
        {"request_speed_mode": "custom", "requests_per_minute": 0},
        {"request_speed_mode": "custom", "requests_per_minute": 10_001},
    ],
)
def test_model_profile_rejects_invalid_request_speed_settings(
    tmp_path,
    updates,
) -> None:
    service = ModelProfileService(ApiProfileStore(tmp_path / "profiles.json"))

    with pytest.raises(ModelProfileInvalid):
        service.upsert(
            "校内模型",
            {
                "api_key": "secret",
                "base_url": "https://example.test/v1",
                "ocr_model": "ocr",
                "grading_model": "grading",
                **updates,
            },
        )


def test_model_profile_saves_batch_inference_settings(tmp_path) -> None:
    store = ApiProfileStore(tmp_path / "profiles.json")
    service = ModelProfileService(store)

    state = service.upsert(
        "校内模型",
        {
            "api_key": "secret",
            "base_url": "https://example.test/v1",
            "grading_model": "grading",
            "batch_enabled": True,
            "batch_model": "ep-bi-abc123",
            "batch_base_url": "https://ark.example.test/api/v3/batch",
            "batch_api_key": "batch-secret",
        },
    )

    profile = state["active_profile"]
    assert profile["batch_enabled"] is True
    assert profile["batch_model"] == "ep-bi-abc123"
    assert profile["batch_base_url"] == "https://ark.example.test/api/v3/batch"
    assert profile["has_batch_api_key"] is True
    assert "batch_api_key" not in profile

    saved = store.load()[0]
    assert saved["batch_enabled"] is True
    assert saved["batch_model"] == "ep-bi-abc123"
    assert saved["batch_api_key"] == "batch-secret"


def test_model_profile_blank_batch_api_key_keeps_the_saved_one(tmp_path) -> None:
    store = ApiProfileStore(tmp_path / "profiles.json")
    service = ModelProfileService(store)
    service.upsert(
        "校内模型",
        {
            "api_key": "secret",
            "base_url": "https://example.test/v1",
            "batch_api_key": "batch-secret",
        },
    )

    state = service.upsert("校内模型", {"batch_api_key": ""})

    assert state["active_profile"]["has_batch_api_key"] is True
    assert store.load()[0]["batch_api_key"] == "batch-secret"


def test_model_profile_batch_inference_defaults_to_off(tmp_path) -> None:
    service = ModelProfileService(ApiProfileStore(tmp_path / "profiles.json"))

    state = service.upsert(
        "校内模型",
        {
            "api_key": "secret",
            "base_url": "https://example.test/v1",
        },
    )

    profile = state["active_profile"]
    assert profile["batch_enabled"] is False
    assert profile["batch_model"] == ""
    assert profile["batch_base_url"] == ""
    assert profile["has_batch_api_key"] is False


@pytest.mark.parametrize(
    "updates",
    [
        {"batch_base_url": "ftp://ark.example.test"},
        {"batch_base_url": "https://user:pass@ark.example.test/api/v3/batch"},
        {"batch_model": "x" * 201},
    ],
)
def test_model_profile_rejects_invalid_batch_inference_settings(
    tmp_path,
    updates,
) -> None:
    service = ModelProfileService(ApiProfileStore(tmp_path / "profiles.json"))

    with pytest.raises(ModelProfileInvalid):
        service.upsert(
            "校内模型",
            {
                "api_key": "secret",
                "base_url": "https://example.test/v1",
                **updates,
            },
        )
