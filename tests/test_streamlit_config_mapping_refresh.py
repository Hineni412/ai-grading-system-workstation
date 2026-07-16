from __future__ import annotations

from typing import Literal

import pytest

import web_app


MappingStatus = Literal["not_present", "refreshed", "reconfirm_required"]


@pytest.mark.parametrize(
    ("service_status", "expected_refreshed", "expected_message"),
    (
        ("not_present", False, "评分依据已保存，并已同步到当前考试批改。"),
        (
            "refreshed",
            True,
            "评分依据已保存，并已同步到当前考试批改；样卷映射表已按新评分标准刷新，请重新确认题框映射。",
        ),
        ("reconfirm_required", False, "评分依据已保存，并已同步到当前考试批改。"),
    ),
)
def test_streamlit_mapping_refresh_keeps_bool_contract_and_truthful_save_message(
    monkeypatch: pytest.MonkeyPatch,
    service_status: MappingStatus,
    expected_refreshed: bool,
    expected_message: str,
) -> None:
    def fake_service(*_args: object, **_kwargs: object) -> MappingStatus:
        return service_status

    monkeypatch.setattr(web_app, "_refresh_template_mapping_service", fake_service)

    refreshed = web_app._refresh_template_mapping_from_session(object(), 17)

    assert refreshed is expected_refreshed
    assert type(refreshed) is bool
    assert web_app._template_mapping_save_confirmation(refreshed) == expected_message


def test_streamlit_mapping_refresh_audit_value_is_explicitly_boolean() -> None:
    source = web_app.Path("web_app.py").read_text(encoding="utf-8")

    assert '"template_mapping_refreshed": bool(refreshed)' in source
