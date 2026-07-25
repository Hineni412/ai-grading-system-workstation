from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from PIL import Image

from ai_grader import AIGrader
from hybrid_batch_grading_service import (
    MajorQuestionAtlasBuilder,
    MajorQuestionSpec,
    PaperEntry,
    build_hybrid_major_prompt,
    grade_major_question_batch,
    validate_hybrid_major_response,
)
from scanner import ExamPaperGroup


class _FakeLLMClient:
    pass


def _multipart_spec(*, question_image_base64: str | None = None) -> MajorQuestionSpec:
    rubric = {
        "question_id": "Q10",
        "parts": [
            {"part_id": "10-1", "part_score": 5},
            {"part_id": "10-2", "part_score": 5},
        ],
    }
    if question_image_base64 is not None:
        rubric["question_image_base64"] = question_image_base64
    return MajorQuestionSpec(
        question_id="Q10",
        detail_question_ids=["10-1", "10-2"],
        rubric=rubric,
        answer_key={"question_id": "Q10"},
        max_score=10,
    )


def _manifest() -> dict:
    return {
        "items": [
            {
                "paper_key": "paper-1",
                "student_id": 1,
                "student_name": "学生甲",
                "sub_items": [],
            }
        ]
    }


def _detail(question_id: str) -> dict:
    return {
        "question_id": question_id,
        "score_awarded": 2,
        "confidence_score": 95,
        "knowledge_ids": ["K1"],
    }


def test_question_image_base64_is_omitted_from_hybrid_and_full_paper_prompts(tmp_path: Path) -> None:
    encoded = base64.b64encode(b"question-stem-image").decode("ascii")
    spec = _multipart_spec(question_image_base64=encoded)

    prompts = build_hybrid_major_prompt(spec, _manifest(), has_stem_image=True)
    assert encoded not in "\n".join(prompts)

    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps({"questions": [spec.rubric]}, ensure_ascii=False),
        encoding="utf-8",
    )
    grader = AIGrader(rubric_path, _FakeLLMClient())
    assert encoded not in grader._build_system_prompt()


@pytest.mark.parametrize(
    ("details", "expected_reason"),
    [
        ([_detail("Q10")], "unexpected_detail_question_id"),
        ([_detail("10-1")], "missing_detail_question_ids"),
        ([_detail("10-1"), _detail("10-1"), _detail("10-2")], "duplicate_detail_question_id"),
    ],
)
def test_multipart_response_requires_each_part_exactly_once(details: list[dict], expected_reason: str) -> None:
    response = {
        "question_id": "Q10",
        "items": [
            {
                "paper_key": "paper-1",
                "student_id": 1,
                "grading_details": details,
            }
        ],
    }

    accepted, failed = validate_hybrid_major_response(response, _manifest(), _multipart_spec())

    assert accepted == []
    assert [item["reason"] for item in failed] == [expected_reason]


def test_atlas_merges_nested_same_page_regions_into_shared_tile(tmp_path: Path) -> None:
    page_path = tmp_path / "page.jpg"
    Image.new("RGB", (500, 500), "white").save(page_path)
    group = ExamPaperGroup(
        front_image=page_path,
        back_image=page_path,
        student_name="学生甲",
        student_id=1,
    )
    entry = PaperEntry("paper-1", 1, "学生甲", group)
    regions = [
        {"page": "front", "mapped_question_id": "10-1", "x": 10, "y": 10, "w": 100, "h": 100},
        {"page": "front", "mapped_question_id": "10-2", "x": 10, "y": 10, "w": 200, "h": 200},
    ]

    result = MajorQuestionAtlasBuilder(tmp_path / "output", crop_padding=0).build(
        session_id=1,
        spec=_multipart_spec(),
        paper_entries=[entry],
        answer_regions=regions,
        batch_index=1,
    )

    sub_items = result["manifest"]["items"][0]["sub_items"]
    assert [item["part_id"] for item in sub_items] == ["10-1", "10-2"]
    assert [item["bbox"]["w"] for item in sub_items] == [200, 200]
    assert len({item["tile_label"] for item in sub_items}) == 1


def test_atlas_uses_shared_groups_and_cross_page_parent_fallback(tmp_path: Path) -> None:
    page_path = tmp_path / "page.jpg"
    Image.new("RGB", (500, 500), "white").save(page_path)
    group = ExamPaperGroup(
        front_image=page_path,
        back_image=page_path,
        student_name="学生甲",
        student_id=1,
    )
    entry = PaperEntry("paper-1", 1, "学生甲", group)
    spec = MajorQuestionSpec(
        question_id="Q12",
        detail_question_ids=["12-1", "12-2", "12-3"],
        rubric={
            "question_id": "Q12",
            "parts": [
                {"part_id": "12-1", "part_score": 3},
                {"part_id": "12-2", "part_score": 3},
                {"part_id": "12-3", "part_score": 4},
            ],
        },
        answer_key={"question_id": "Q12"},
        max_score=10,
    )
    regions = [
        {"page": "front", "mapped_question_id": "Q12(1)", "x": 10, "y": 10, "w": 80, "h": 80},
        {"page": "back", "mapped_question_id": "Q12", "x": 100, "y": 120, "w": 160, "h": 140},
    ]

    result = MajorQuestionAtlasBuilder(tmp_path / "output", crop_padding=0).build(
        session_id=1,
        spec=spec,
        paper_entries=[entry],
        answer_regions=regions,
        batch_index=1,
    )

    sub_items = result["manifest"]["items"][0]["sub_items"]
    by_part = {item["part_id"]: item for item in sub_items}

    assert len({item["tile_label"] for item in sub_items}) == 2
    assert by_part["12-1"]["page"] == "front"
    assert by_part["12-2"]["page"] == "back"
    assert by_part["12-3"]["page"] == "back"
    assert by_part["12-2"]["tile_label"] == by_part["12-3"]["tile_label"]
    assert by_part["12-2"]["bbox"] == by_part["12-3"]["bbox"]
    assert by_part["12-1"]["tile_label"] != by_part["12-2"]["tile_label"]


def test_hybrid_prompt_describes_shared_tile_scoring_rules() -> None:
    spec = MajorQuestionSpec(
        question_id="Q12",
        detail_question_ids=["12-1", "12-2", "12-3"],
        rubric={
            "question_id": "Q12",
            "parts": [
                {"part_id": "12-1", "part_score": 3},
                {"part_id": "12-2", "part_score": 3},
                {"part_id": "12-3", "part_score": 4},
            ],
        },
        answer_key={"question_id": "Q12"},
        max_score=10,
    )
    manifest = {
        "items": [
            {
                "paper_key": "paper-1",
                "student_id": 1,
                "student_name": "学生甲",
                "sub_items": [
                    {
                        "part_id": "12-2",
                        "tile_label": "01. [student] 鍚堝苟灏忛棶: 12-2,12-3",
                        "page": "back",
                        "bbox": {"x": 100, "y": 120, "w": 160, "h": 140},
                    },
                    {
                        "part_id": "12-3",
                        "tile_label": "01. [student] 鍚堝苟灏忛棶: 12-2,12-3",
                        "page": "back",
                        "bbox": {"x": 100, "y": 120, "w": 160, "h": 140},
                    },
                ],
            }
        ]
    }

    prompts = build_hybrid_major_prompt(spec, manifest)
    joined = "\n".join(prompts)

    assert "Shared tiles may contain vertically, horizontally, or continuously written answers." in joined
    assert "Score each required part exactly once and do not duplicate evidence across parts." in joined
    assert "If boundaries are unclear, return all implicated parts with low confidence and needs_human_review=true." in joined


def test_subjective_retry_acquires_one_request_slot_per_model_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    atlas_path = tmp_path / "atlas.jpg"
    Image.new("RGB", (20, 20), "white").save(atlas_path)
    manifest = _manifest()

    class FakeBuilder:
        def build(self, **kwargs):
            return {"atlas_path": atlas_path, "manifest": manifest}

    class RetryingClient:
        def __init__(self) -> None:
            self.calls = 0

        def json_from_images(self, *args, **kwargs):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("transient")
            return {
                "question_id": "Q10",
                "items": [
                    {
                        "paper_key": "paper-1",
                        "student_id": 1,
                        "grading_details": [_detail("10-1"), _detail("10-2")],
                    }
                ],
            }

    class CountingLimiter:
        def __init__(self) -> None:
            self.calls = 0

        def acquire(self) -> None:
            self.calls += 1

    client = RetryingClient()
    limiter = CountingLimiter()
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    result = grade_major_question_batch(
        session_id=1,
        spec=_multipart_spec(),
        paper_entries=[],
        answer_regions=[],
        llm_client=client,
        grading_model="test-model",
        output_root=tmp_path,
        batch_index=1,
        builder=FakeBuilder(),
        rate_limiter=limiter,
    )

    assert len(result["accepted"]) == 1
    assert client.calls == 3
    assert limiter.calls == 3


def test_subjective_retry_reuses_one_bounded_image_memo_for_all_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    atlas_path = tmp_path / "atlas.jpg"
    Image.new("RGB", (20, 20), "white").save(atlas_path)
    manifest = _manifest()

    class FakeBuilder:
        def build(self, **kwargs):
            return {"atlas_path": atlas_path, "manifest": manifest}

    class RetryingClient:
        def __init__(self) -> None:
            self.memos: list[dict[str, bytes]] = []

        def json_from_images_with_options(self, *args, **kwargs):
            self.memos.append(kwargs["image_compression_memo"])
            if len(self.memos) < 3:
                raise RuntimeError("transient")
            return {
                "question_id": "Q10",
                "items": [
                    {
                        "paper_key": "paper-1",
                        "student_id": 1,
                        "grading_details": [_detail("10-1"), _detail("10-2")],
                    }
                ],
            }

    client = RetryingClient()
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    result = grade_major_question_batch(
        session_id=1,
        spec=_multipart_spec(),
        paper_entries=[],
        answer_regions=[],
        llm_client=client,
        grading_model="test-model",
        output_root=tmp_path,
        batch_index=1,
        builder=FakeBuilder(),
    )

    assert len(result["accepted"]) == 1
    assert len(client.memos) == 3
    assert client.memos[0] is client.memos[1] is client.memos[2]
    assert client.memos[0] == {}
