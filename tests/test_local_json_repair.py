from __future__ import annotations


import pytest

from backend.llm.json_repair import parse_json_object_locally


def test_local_json_repair_handles_control_char_invalid_escape_and_trailing_comma() -> (
    None
):
    result = parse_json_object_locally(
        '{"meta":{"note":"line1\nline2 \\(x\\)"},"rubric":{},}'
    )

    assert result.payload["meta"]["note"] == "line1\nline2 \\(x\\)"
    assert set(result.report.operations) == {
        "escape_control_character",
        "escape_invalid_backslash",
        "remove_trailing_comma",
    }


def test_local_json_repair_does_not_close_truncated_content_or_leak_it() -> None:
    private = '{"student_answer":"private answer","rubric":{"questions":['

    with pytest.raises(ValueError) as raised:
        parse_json_object_locally(private)

    assert "private answer" not in str(raised.value)
    assert "响应字符数" in str(raised.value)
