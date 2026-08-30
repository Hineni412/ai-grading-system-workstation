from __future__ import annotations

import pytest

from api_profiles import ApiProfileStore, PROFILE_FIELD_REMOVE
from backend.llm.policy import (
    LLMPolicyError,
    LLMRequestKind,
    policy_from_profile,
    policy_overrides_from_profile,
)
from backend.model_profiles import ModelProfileInvalid, ModelProfileService


def test_channel_retry_budget_defaults_to_kind_policy() -> None:
    policy = policy_from_profile(LLMRequestKind.TAGGING, {"name": "x"})

    assert policy.max_retries == 2


def test_channel_retry_budget_replaces_kind_default() -> None:
    policy = policy_from_profile(
        LLMRequestKind.TAGGING,
        {"name": "x", "max_auto_retries": 4},
    )

    assert policy.max_retries == 4
    # The delay table is rebuilt for the effective retry count.
    assert len(policy.retry_delays) == 4


def test_per_kind_override_wins_over_channel_budget() -> None:
    policy = policy_from_profile(
        LLMRequestKind.TAGGING,
        {
            "name": "x",
            "max_auto_retries": 1,
            "llm_tagging_max_retries": 3,
        },
    )

    assert policy.max_retries == 3


def test_channel_retry_budget_zero_disables_retries() -> None:
    policy = policy_from_profile(
        LLMRequestKind.TAGGING,
        {"name": "x", "max_auto_retries": 0},
    )

    assert policy.max_retries == 0


def test_channel_retry_budget_rejects_out_of_range_values() -> None:
    with pytest.raises(LLMPolicyError):
        policy_from_profile(
            LLMRequestKind.TAGGING,
            {"name": "x", "max_auto_retries": 6},
        )
    with pytest.raises(LLMPolicyError):
        policy_from_profile(
            LLMRequestKind.TAGGING,
            {"name": "x", "max_auto_retries": True},
        )


def test_request_timeout_replaces_online_kind_default() -> None:
    profile = {"name": "x", "request_timeout_seconds": 45}

    grading = policy_from_profile(LLMRequestKind.GRADING, profile)
    recognition = policy_from_profile(LLMRequestKind.RECOGNITION, profile)
    tagging = policy_from_profile(LLMRequestKind.TAGGING, profile)
    workspace = policy_from_profile(LLMRequestKind.WORKSPACE, profile)

    assert grading.timeout_seconds == 45.0
    assert recognition.timeout_seconds == 45.0
    assert tagging.timeout_seconds == 45.0
    assert workspace.timeout_seconds == 45.0


def test_request_timeout_per_kind_override_wins() -> None:
    policy = policy_from_profile(
        LLMRequestKind.GRADING,
        {
            "name": "x",
            "request_timeout_seconds": 45,
            "llm_grading_timeout_seconds": 120,
        },
    )

    assert policy.timeout_seconds == 120.0


def test_request_timeout_ignored_by_batch_channels() -> None:
    profile = {"name": "x", "request_timeout_seconds": 45}

    grading_batch = policy_from_profile(LLMRequestKind.GRADING_BATCH, profile)
    tagging_batch = policy_from_profile(LLMRequestKind.TAGGING_BATCH, profile)

    assert grading_batch.timeout_seconds == 3600.0
    assert tagging_batch.timeout_seconds == 3600.0


def test_request_timeout_accepts_online_maximum() -> None:
    policy = policy_from_profile(
        LLMRequestKind.TAGGING,
        {"name": "x", "request_timeout_seconds": 1200},
    )

    assert policy.timeout_seconds == 1200.0


def test_request_timeout_rejects_out_of_range_values() -> None:
    for invalid in (29, 1201, 29.5, "45"):
        with pytest.raises(LLMPolicyError):
            policy_from_profile(
                LLMRequestKind.GRADING,
                {"name": "x", "request_timeout_seconds": invalid},
            )


def test_request_timeout_overrides_pass_through() -> None:
    overrides = policy_overrides_from_profile(
        {
            "name": "x",
            "api_key": "secret",
            "request_timeout_seconds": 90,
        }
    )

    assert overrides["request_timeout_seconds"] == 90
    assert "api_key" not in overrides


def test_model_profile_service_round_trips_request_timeout(tmp_path) -> None:
    service = ModelProfileService(ApiProfileStore(tmp_path / "profiles.json"))

    saved = service.upsert(
        "智谱",
        {
            "api_key": "secret",
            "base_url": "https://example.test/v1",
            "request_timeout_seconds": 45,
        },
    )

    assert saved["profiles"][0]["request_timeout_seconds"] == 45

    cleared = service.upsert("智谱", {"request_timeout_seconds": None})

    assert cleared["profiles"][0]["request_timeout_seconds"] is None
    stored = ApiProfileStore(tmp_path / "profiles.json").load()
    assert "request_timeout_seconds" not in stored[0]


def test_model_profile_service_accepts_timeout_boundaries(tmp_path) -> None:
    service = ModelProfileService(ApiProfileStore(tmp_path / "profiles.json"))
    service.upsert(
        "智谱",
        {"api_key": "secret", "base_url": "https://example.test/v1"},
    )

    lowered = service.upsert("智谱", {"request_timeout_seconds": 30})
    assert lowered["profiles"][0]["request_timeout_seconds"] == 30

    mid = service.upsert("智谱", {"request_timeout_seconds": 600})
    assert mid["profiles"][0]["request_timeout_seconds"] == 600

    raised = service.upsert("智谱", {"request_timeout_seconds": 1200})
    assert raised["profiles"][0]["request_timeout_seconds"] == 1200


def test_model_profile_service_rejects_invalid_request_timeout(
    tmp_path,
) -> None:
    service = ModelProfileService(ApiProfileStore(tmp_path / "profiles.json"))
    service.upsert(
        "智谱",
        {"api_key": "secret", "base_url": "https://example.test/v1"},
    )

    with pytest.raises(ModelProfileInvalid):
        service.upsert("智谱", {"request_timeout_seconds": 29})
    with pytest.raises(ModelProfileInvalid):
        service.upsert("智谱", {"request_timeout_seconds": 1201})
    with pytest.raises(ModelProfileInvalid):
        service.upsert("智谱", {"request_timeout_seconds": "45"})
    with pytest.raises(ModelProfileInvalid):
        service.upsert("智谱", {"request_timeout_seconds": 45.5})


def test_policy_overrides_keep_channel_budget_and_drop_secrets() -> None:
    overrides = policy_overrides_from_profile(
        {
            "name": "x",
            "api_key": "secret",
            "max_auto_retries": 2,
            "base_url": "https://example.test/v1",
        }
    )

    assert overrides["max_auto_retries"] == 2
    assert "api_key" not in overrides
    assert "base_url" not in overrides


def test_model_profile_service_round_trips_max_auto_retries(tmp_path) -> None:
    service = ModelProfileService(ApiProfileStore(tmp_path / "profiles.json"))

    saved = service.upsert(
        "智谱",
        {
            "api_key": "secret",
            "base_url": "https://example.test/v1",
            "max_auto_retries": 3,
        },
    )

    assert saved["profiles"][0]["max_auto_retries"] == 3

    cleared = service.upsert("智谱", {"max_auto_retries": None})

    assert cleared["profiles"][0]["max_auto_retries"] is None
    stored = ApiProfileStore(tmp_path / "profiles.json").load()
    assert "max_auto_retries" not in stored[0]


def test_model_profile_service_rejects_invalid_max_auto_retries(
    tmp_path,
) -> None:
    service = ModelProfileService(ApiProfileStore(tmp_path / "profiles.json"))
    service.upsert(
        "智谱",
        {"api_key": "secret", "base_url": "https://example.test/v1"},
    )

    with pytest.raises(ModelProfileInvalid):
        service.upsert("智谱", {"max_auto_retries": 6})
    with pytest.raises(ModelProfileInvalid):
        service.upsert("智谱", {"max_auto_retries": -1})
    with pytest.raises(ModelProfileInvalid):
        service.upsert("智谱", {"max_auto_retries": "2"})


def test_profile_field_remove_sentinel_pops_key(tmp_path) -> None:
    store = ApiProfileStore(tmp_path / "profiles.json")
    store.upsert(
        {
            "name": "站点",
            "api_key": "secret",
            "max_auto_retries": 2,
        }
    )

    store.update_named("站点", {"max_auto_retries": PROFILE_FIELD_REMOVE})

    profile = store.load()[0]
    assert "max_auto_retries" not in profile
