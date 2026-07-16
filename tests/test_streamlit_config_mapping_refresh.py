from __future__ import annotations

from typing import Literal

import pytest

import web_app


MappingStatus = Literal["not_present", "refreshed", "reconfirm_required"]


@pytest.mark.parametrize(
    ("service_status", "expected_message"),
    (
        ("not_present", "评分依据已保存，并已同步到当前考试批改。"),
        (
            "refreshed",
            "评分依据已保存，并已同步到当前考试批改；样卷映射表已按新评分标准刷新，请重新确认题框映射。",
        ),
        (
            "reconfirm_required",
            "评分依据已保存，并已同步到当前考试批改；已有样卷映射需要重新确认后才能继续批改。",
        ),
    ),
)
def test_streamlit_mapping_refresh_keeps_three_state_contract_and_truthful_save_message(
    monkeypatch: pytest.MonkeyPatch,
    service_status: MappingStatus,
    expected_message: str,
) -> None:
    def fake_service(*_args: object, **_kwargs: object) -> MappingStatus:
        return service_status

    monkeypatch.setattr(web_app, "_refresh_template_mapping_service", fake_service)

    status = web_app._refresh_template_mapping_from_session(object(), 17)

    assert status == service_status
    assert web_app._template_mapping_save_confirmation(status) == expected_message


def test_streamlit_mapping_refresh_audit_keeps_bool_and_status() -> None:
    source = web_app.Path("web_app.py").read_text(encoding="utf-8")

    assert '"template_mapping_refreshed": mapping_status == "refreshed"' in source
    assert '"template_mapping_status": mapping_status' in source


def test_streamlit_save_uses_partial_success_wrapper() -> None:
    source = web_app.Path("web_app.py").read_text(encoding="utf-8")

    assert "mapping_result = refresh_mapping_after_config_save(" in source
    assert "mapping_status = mapping_result.mapping_status" in source
