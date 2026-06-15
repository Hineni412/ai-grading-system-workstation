from __future__ import annotations

import base64
import inspect
import json
import re
from pathlib import Path
from typing import Any

import pytest

import answer_region_editor_component as component_module
from answer_region_editor_component import (
    build_answer_region_editor_data,
    load_template_image_data_url,
    render_answer_region_editor,
)


def test_registered_component_assets_are_nonempty_with_one_default_initializer() -> None:
    assert component_module._EDITOR_HTML.strip()
    assert component_module._EDITOR_CSS.strip()
    assert component_module._EDITOR_JS.strip()
    assert len(re.findall(r"\bexport\s+default\b", component_module._EDITOR_JS)) == 1


@pytest.mark.parametrize(
    ("suffix", "expected_mime"),
    [
        (".png", "image/png"),
        (".jpg", "image/jpeg"),
        (".jpeg", "image/jpeg"),
        (".PNG", "image/png"),
    ],
)
def test_template_image_data_url_uses_supported_mime_and_original_bytes(
    tmp_path: Path,
    suffix: str,
    expected_mime: str,
) -> None:
    path = tmp_path / f"front{suffix}"
    content = b"answer-region-image-bytes"
    path.write_bytes(content)

    value = load_template_image_data_url(str(path), path.stat().st_mtime_ns)

    prefix, encoded = value.split(",", maxsplit=1)
    assert prefix == f"data:{expected_mime};base64"
    assert base64.b64decode(encoded) == content


def test_template_image_data_url_rejects_unsupported_formats(tmp_path: Path) -> None:
    path = tmp_path / "front.gif"
    path.write_bytes(b"not-a-supported-template-image")

    with pytest.raises(ValueError, match=r"Unsupported template image format: \.gif"):
        load_template_image_data_url(str(path), path.stat().st_mtime_ns)


def test_template_image_data_url_cache_signature_includes_mtime(tmp_path: Path) -> None:
    path = tmp_path / "front.png"
    path.write_bytes(b"first")

    first = load_template_image_data_url(str(path), 1)
    path.write_bytes(b"second")
    second = load_template_image_data_url(str(path), 2)

    assert hasattr(load_template_image_data_url, "clear")
    assert first != second


def _build_payload_inputs() -> dict[str, Any]:
    return {
        "session_id": 17,
        "front_image_data_url": "data:image/png;base64,front",
        "back_image_data_url": "data:image/jpeg;base64,back",
        "image_sizes": {"front": (1000, 1400), "back": (1100, 1500)},
        "editor_state": {
            "revision": 4,
            "handled_revision": 3,
            "active_page": "back",
            "regions": [
                {
                    "region_uuid": "region-1",
                    "page": "front",
                    "metadata": {"labels": ["Q1"]},
                }
            ],
            "undo_stack": [[{"region_uuid": "older-region", "page": "front"}]],
            "redo_stack": [[{"region_uuid": "newer-region", "page": "back"}]],
            "drawer_open": True,
        },
        "automatic_candidates": ["Q1", "Q2"],
        "manual_question_options": [
            {"value": "", "label": "Unbound"},
            {"value": "Q1", "label": "Question 1"},
        ],
        "validation_issues": [
            {"code": "unbound_question", "region_uuid": "region-1"}
        ],
        "draft_revision": 4,
        "save_status": "saved",
        "drawer_open_requested": False,
    }


def test_component_payload_is_json_serializable_and_uses_keyword_only_arguments() -> None:
    signature = inspect.signature(build_answer_region_editor_data)
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )

    payload = build_answer_region_editor_data(**_build_payload_inputs())

    assert json.loads(json.dumps(payload)) == payload
    assert payload["image_sizes"] == {
        "front": [1000, 1400],
        "back": [1100, 1500],
    }


def test_component_payload_copies_inputs_and_preserves_complete_editor_history() -> None:
    inputs = _build_payload_inputs()
    payload = build_answer_region_editor_data(**inputs)

    assert payload["editor_state"]["undo_stack"] == inputs["editor_state"]["undo_stack"]
    assert payload["editor_state"]["redo_stack"] == inputs["editor_state"]["redo_stack"]
    assert payload["editor_state"]["handled_revision"] == 3

    payload["image_sizes"]["front"][0] = 1
    payload["editor_state"]["regions"][0]["metadata"]["labels"].append("changed")
    payload["editor_state"]["undo_stack"][0][0]["page"] = "changed"
    payload["automatic_candidates"].append("changed")
    payload["manual_question_options"][0]["label"] = "changed"
    payload["validation_issues"][0]["code"] = "changed"

    assert inputs["image_sizes"]["front"] == (1000, 1400)
    assert inputs["editor_state"]["regions"][0]["metadata"]["labels"] == ["Q1"]
    assert inputs["editor_state"]["undo_stack"][0][0]["page"] == "front"
    assert inputs["automatic_candidates"] == ["Q1", "Q2"]
    assert inputs["manual_question_options"][0]["label"] == "Unbound"
    assert inputs["validation_issues"][0]["code"] == "unbound_question"


def test_render_answer_region_editor_mounts_v2_component_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    expected_result = object()

    def fake_component(**kwargs: Any) -> object:
        calls.append(kwargs)
        return expected_result

    monkeypatch.setattr(component_module, "_answer_region_editor", fake_component)
    data = {"session_id": 17}

    result = render_answer_region_editor(key="answer-region-17", data=data)

    assert result is expected_result
    assert len(calls) == 1
    call = calls[0]
    assert call["key"] == "answer-region-17"
    assert call["data"] is data
    assert call["default"] == {"editor_state": None}
    assert call["height"] == 860
    assert callable(call["on_editor_state_change"])
    assert callable(call["on_finish_requested_change"])
    assert callable(call["on_exit_requested_change"])
    assert set(call) == {
        "key",
        "data",
        "default",
        "height",
        "on_editor_state_change",
        "on_finish_requested_change",
        "on_exit_requested_change",
    }
