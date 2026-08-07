from __future__ import annotations

import json

import pytest

from backend.llm.json_repair import parse_json_object_locally


def test_local_json_repair_inserts_unambiguous_missing_colon() -> None:
    result = parse_json_object_locally(
        '{"rubric":{"questions":[]},"answer_key" {"questions":[]},"meta":{}}'
    )

    assert result.payload["answer_key"] == {"questions": []}
    assert result.report.repaired is True
    assert result.report.operations == ("insert_missing_colon",)
    assert result.report.response_chars > 0
    assert len(result.report.response_sha256) == 64


def test_local_json_repair_handles_control_char_invalid_escape_and_trailing_comma() -> None:
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


def test_local_json_repair_replaces_only_malformed_terminal_closer_sequence() -> None:
    expected = {"results": [{"solution": {"parts": [{"points": []}]}}]}
    serialized = json.dumps(expected)
    malformed = serialized[:-3] + serialized[-2:]

    result = parse_json_object_locally(malformed)

    assert result.payload == expected
    assert result.report.operations == ("repair_terminal_closers",)


def test_local_json_repair_does_not_guess_unquoted_business_value() -> None:
    with pytest.raises(ValueError):
        parse_json_object_locally('{"canonical_answer": private_answer}')


def test_local_json_repair_inserts_parser_confirmed_missing_comma() -> None:
    result = parse_json_object_locally(
        '{"rubric":{"questions":[{"question_id":"Q1"} {"question_id":"Q2"}]},'
        '"answer_key":{"questions":[]},"meta":{}}'
    )

    assert [item["question_id"] for item in result.payload["rubric"]["questions"]] == [
        "Q1", "Q2"
    ]
    assert "insert_missing_comma" in result.report.operations


def test_local_json_repair_does_not_treat_unescaped_quote_as_missing_comma() -> None:
    with pytest.raises(ValueError):
        parse_json_object_locally('{"canonical_answer":"the "private" answer"}')
