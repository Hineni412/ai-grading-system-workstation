from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import hybrid_batch_grading_service as hybrid
from ai_grader import AIGrader
from hybrid_batch_grading_service import (
    MajorQuestionAtlasBuilder,
    MajorQuestionSpec,
    PaperEntry,
    build_hybrid_major_prompt,
    grade_major_question_batch,
    run_hybrid_batch_grading,
    validate_hybrid_major_response,
)
from scanner import ExamPaperGroup


class _FakeLLMClient:
    pass


def test_hybrid_objective_recognition_reuses_active_llm_client_and_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    llm_client = _FakeLLMClient()
    captured: dict[str, object] = {}

    def fake_objective_run(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            details_by_paper_key={},
            metadata_by_paper_key={},
            usage_records=[],
        )

    monkeypatch.setattr(hybrid, "run_objective_batch_recognition", fake_objective_run)

    run_hybrid_batch_grading(
        session_id=1,
        paper_groups=[],
        answer_regions=[],
        rubric={"questions": []},
        answer_key={"questions": []},
        llm_client=llm_client,
        grading_model="active-grading-model",
        output_root=tmp_path,
    )

    assert captured["recognition_client"] is llm_client
    assert captured["recognition_model"] == "active-grading-model"


def test_detail_full_score_uses_canonical_part_score_over_legacy_max_score() -> None:
    spec = MajorQuestionSpec(
        question_id="Q11",
        detail_question_ids=["Q11(P1)"],
        rubric={
            "question_id": "Q11",
            "max_score": 6,
            "parts": [
                {
                    "part_id": "Q11(P1)",
                    "part_score": 6,
                    "max_score": 1,
                }
            ],
        },
        answer_key={"question_id": "Q11"},
        max_score=6,
    )

    assert hybrid._detail_full_score(spec, "Q11(P1)") == 6


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
    ("details", "accepted_qids", "failed_reason", "failed_targets"),
    [
        # An out-of-scope part is ignored; with nothing valid left the item fails whole.
        ([_detail("Q10")], [], "missing_grading_details", ["10-1", "10-2"]),
        # A single missing part only fails that part; valid parts are kept.
        ([_detail("10-1")], ["10-1"], "missing_detail_question_ids", ["10-2"]),
        # A duplicated part keeps its first result instead of voiding the item.
        ([_detail("10-1"), _detail("10-1"), _detail("10-2")], ["10-1", "10-2"], None, []),
    ],
)
def test_multipart_response_tolerates_partial_format_slips(
    details: list[dict],
    accepted_qids: list[str],
    failed_reason: str | None,
    failed_targets: list[str],
) -> None:
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

    assert [
        detail.question_id
        for item in accepted
        for detail in item["details"]
    ] == accepted_qids
    if failed_reason is None:
        assert failed == []
    else:
        assert [item["reason"] for item in failed] == [failed_reason]
        assert failed[0]["target_detail_question_ids"] == failed_targets


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


def test_atlas_labels_use_a_font_that_can_render_chinese(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drawn_fonts: list[object | None] = []
    original_text = hybrid.ImageDraw.ImageDraw.text

    def capture_text(self, xy, text, *args, **kwargs):
        drawn_fonts.append(kwargs.get("font"))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(hybrid.ImageDraw.ImageDraw, "text", capture_text)
    tile = Image.new("RGB", (120, 80), "white")
    try:
        hybrid._save_atlas_with_labels(
            ["01. 姓名=样卷A · Q10(1)"],
            [tile],
            tmp_path / "atlas.jpg",
            max_width=400,
            jpeg_quality=90,
        )
    finally:
        tile.close()

    assert len(drawn_fonts) == 1
    font = drawn_fonts[0]
    assert font is not None
    assert bytes(font.getmask("样卷A")) != bytes(font.getmask("□□A"))


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

    assert "同一切片中可能存在纵向、横向或连续书写的多个答案。" in joined
    assert "每个目标题只能评分一次，不得在不同小问之间重复使用同一份作答证据。" in joined
    assert "若小问边界不清，必须返回所有可能受影响的目标题" in joined
    assert "needs_human_review=true" in joined


def test_hybrid_prompt_keeps_unique_grading_constraints_in_consistent_chinese() -> None:
    system_prompt, _static_prompt, _dynamic_prompt = build_hybrid_major_prompt(
        _multipart_spec(),
        _manifest(),
    )

    for required_rule in (
        "只返回该学生的目标题",
        "不得因解题路径不同而扣分",
        "按 deduction_policy 或 presentation_rules 扣分",
        "prompt_injection_detected=true",
        "observed_answer 只能填写未被涂抹/作废区域中的有效答案",
        "不要输出知识点或技能字段",
        "proof_obligations 是必须完成的证明责任",
        "必须判 0 分或受 answer_only_max_score 限制",
    ):
        assert required_rule in system_prompt

    for mixed_fragment in (
        "and 学生试卷",
        "of strings",
        "of students' work",
        "Shared tiles may contain",
        "Score each targeted part",
        "If boundaries are unclear",
    ):
        assert mixed_fragment not in system_prompt


def test_subjective_failure_uses_one_request_slot_without_hidden_retry(
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

    with pytest.raises(RuntimeError, match="transient"):
        grade_major_question_batch(
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

    assert client.calls == 1
    assert limiter.calls == 1


def test_subjective_failure_uses_one_bounded_image_memo_without_hidden_retry(
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

    with pytest.raises(RuntimeError, match="transient"):
        grade_major_question_batch(
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

    assert len(client.memos) == 1
    assert client.memos[0] == {}
