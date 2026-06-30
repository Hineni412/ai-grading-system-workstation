from __future__ import annotations

from pages_shared.question_bank_tagging_config_state import (
    missing_tagging_config_fields,
    replace_tagging_config_state,
    tagging_runtime_limits,
)


def test_saved_profile_replaces_stale_empty_session_values() -> None:
    state = {
        "tagging_api_key_input": "",
        "tagging_base_url_input": "",
        "tagging_model_input": "",
    }
    profile = {
        "tagging_api_key": "saved-key",
        "tagging_base_url": "https://example.test/v1",
        "tagging_model": "tag-model",
        "tagging_max_workers": 20,
        "tagging_requests_per_minute": 1000,
        "tagging_thinking": True,
    }

    replace_tagging_config_state(state, profile)

    assert state["tagging_api_key_input"] == "saved-key"
    assert state["tagging_base_url_input"] == "https://example.test/v1"
    assert state["tagging_model_input"] == "tag-model"
    assert state["tagging_max_workers_input"] == 20
    assert state["tagging_requests_per_minute_input"] == 1000
    assert state["tagging_thinking_input"] is True


def test_missing_fields_and_runtime_limits_come_from_profile() -> None:
    assert missing_tagging_config_fields({"tagging_api_key": "key"}) == (
        "tagging_base_url",
        "tagging_model",
    )
    assert tagging_runtime_limits(
        {"tagging_max_workers": 0, "tagging_requests_per_minute": "25"}
    ) == (1, 25)
