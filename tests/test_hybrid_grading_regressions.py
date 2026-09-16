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


def _full_page_setup(tmp_path: Path):
    front = tmp_path / "front.jpg"
    back = tmp_path / "back.jpg"
    Image.new("RGB", (400, 300), "white").save(front)
    Image.new("RGB", (400, 200), "white").save(back)
    group = ExamPaperGroup(front, back, "学生甲", 1, source_label="scan_1")
    entry = PaperEntry("paper-1", 1, "学生甲", group)
    spec = MajorQuestionSpec(
        question_id="Q10",
        detail_question_ids=["10-1", "10-2"],
        rubric={
            "question_id": "Q10",
            "parts": [
                {"part_id": "10-1", "part_score": 4},
                {"part_id": "10-2", "part_score": 4},
            ],
        },
        answer_key={"question_id": "Q10"},
        max_score=8,
    )
    regions = [
        {"page": "front", "mapped_question_id": "Q10(1)", "x": 10, "y": 10, "w": 80, "h": 50},
        {"page": "back", "mapped_question_id": "Q10(2)", "x": 20, "y": 30, "w": 60, "h": 40},
    ]
    return spec, entry, regions


def test_full_page_builder_stacks_front_and_back_with_region_hints(tmp_path: Path) -> None:
    spec, entry, regions = _full_page_setup(tmp_path)

    result = hybrid.FullPageEvidenceBuilder(
        tmp_path / "output",
        include_target_crop=True,
    ).build(
        session_id=7,
        spec=spec,
        paper_entries=[entry],
        answer_regions=regions,
        batch_index=1,
        target_detail_question_ids_by_paper_key={"paper-1": ["10-1", "10-2"]},
    )

    manifest = result["manifest"]
    with Image.open(result["atlas_path"]) as composite:
        assert composite.width == 400
        assert composite.height == 300 + 18 + 200
        width, height = composite.size
    assert manifest["schema_version"] == 3
    assert manifest["mode"] == "full_page_subjective"
    assert manifest["session_id"] == 7
    assert manifest["question_id"] == "Q10"
    assert [page["page"] for page in manifest["pages"]] == ["front", "back"]
    assert manifest["pages"][1]["composite_bbox"]["y"] == 300 + 18
    assert len(manifest["items"]) == 1
    item = manifest["items"][0]
    assert item["paper_key"] == "paper-1"
    assert item["target_detail_question_ids"] == ["10-1", "10-2"]
    sub_items = {si["part_id"]: si for si in item["sub_items"]}
    assert set(sub_items) == {"10-1", "10-2"}
    assert sub_items["10-1"]["page"] == "front"
    assert sub_items["10-2"]["page"] == "back"
    for sub_item in sub_items.values():
        bbox = sub_item["bbox"]
        assert 0 <= bbox["x"] and bbox["x"] + bbox["w"] <= width
        assert 0 <= bbox["y"] and bbox["y"] + bbox["h"] <= height
    assert sub_items["10-2"]["bbox"]["y"] >= 300 + 18
    crop_path = Path(manifest["target_crop_path"])
    assert crop_path.exists()
    assert Path(result["manifest_path"]).exists()


def test_full_page_builder_marks_missing_region_with_null_bbox(tmp_path: Path) -> None:
    spec, entry, _ = _full_page_setup(tmp_path)
    regions = [
        {"page": "front", "mapped_question_id": "Q99", "x": 10, "y": 10, "w": 80, "h": 50},
    ]

    result = hybrid.FullPageEvidenceBuilder(tmp_path / "output").build(
        session_id=7,
        spec=spec,
        paper_entries=[entry],
        answer_regions=regions,
        batch_index=1,
        target_detail_question_ids_by_paper_key={"paper-1": ["10-1", "10-2"]},
    )

    item = result["manifest"]["items"][0]
    assert item["target_detail_question_ids"] == ["10-1", "10-2"]
    sub_items = {si["part_id"]: si for si in item["sub_items"]}
    assert set(sub_items) == {"10-1", "10-2"}
    assert all(si["bbox"] is None for si in sub_items.values())
    # Without usable regions both pages are included so the model can still
    # locate the answers on the full-page image.
    assert [page["page"] for page in result["manifest"]["pages"]] == ["front", "back"]


def test_full_page_builder_survives_missing_back_page(tmp_path: Path) -> None:
    front = tmp_path / "front.jpg"
    Image.new("RGB", (400, 300), "white").save(front)
    group = ExamPaperGroup(
        front, tmp_path / "missing_back.jpg", "学生甲", 1, source_label="scan_1"
    )
    entry = PaperEntry("paper-1", 1, "学生甲", group)
    spec = MajorQuestionSpec(
        question_id="Q10",
        detail_question_ids=["10-1"],
        rubric={"question_id": "Q10", "parts": [{"part_id": "10-1", "part_score": 8}]},
        answer_key={"question_id": "Q10"},
        max_score=8,
    )
    regions = [
        {"page": "back", "mapped_question_id": "Q10(1)", "x": 20, "y": 30, "w": 60, "h": 40},
    ]

    result = hybrid.FullPageEvidenceBuilder(tmp_path / "output").build(
        session_id=7,
        spec=spec,
        paper_entries=[entry],
        answer_regions=regions,
        batch_index=1,
        target_detail_question_ids_by_paper_key={"paper-1": ["10-1"]},
    )

    manifest = result["manifest"]
    assert [page["page"] for page in manifest["pages"]] == ["front"]
    assert manifest["items"][0]["target_detail_question_ids"] == ["10-1"]
    assert manifest["items"][0]["sub_items"][0]["bbox"] is None


def test_full_page_builder_raises_when_no_page_source_exists(tmp_path: Path) -> None:
    group = ExamPaperGroup(
        tmp_path / "missing_front.jpg",
        tmp_path / "missing_back.jpg",
        "学生甲", 1, source_label="scan_1",
    )
    entry = PaperEntry("paper-1", 1, "学生甲", group)
    spec, _, regions = _full_page_setup(tmp_path)

    with pytest.raises(ValueError, match="full_page_source_missing"):
        hybrid.FullPageEvidenceBuilder(tmp_path / "output").build(
            session_id=7,
            spec=spec,
            paper_entries=[entry],
            answer_regions=regions,
            batch_index=1,
        )


def test_full_page_target_crop_uses_source_page_resolution(tmp_path: Path) -> None:
    front = tmp_path / "front.jpg"
    back = tmp_path / "back.jpg"
    # Wide enough that the composite gets downscaled below source resolution.
    Image.new("RGB", (2400, 300), "white").save(front)
    Image.new("RGB", (400, 200), "white").save(back)
    group = ExamPaperGroup(front, back, "学生甲", 1, source_label="scan_1")
    entry = PaperEntry("paper-1", 1, "学生甲", group)
    spec = MajorQuestionSpec(
        question_id="Q10",
        detail_question_ids=["10-1", "10-2"],
        rubric={
            "question_id": "Q10",
            "parts": [
                {"part_id": "10-1", "part_score": 4},
                {"part_id": "10-2", "part_score": 4},
            ],
        },
        answer_key={"question_id": "Q10"},
        max_score=8,
    )
    # Both targets live on the front page.
    regions = [
        {"page": "front", "mapped_question_id": "Q10(1)", "x": 10, "y": 10, "w": 80, "h": 50},
        {"page": "front", "mapped_question_id": "Q10(2)", "x": 100, "y": 80, "w": 60, "h": 40},
    ]

    result = hybrid.FullPageEvidenceBuilder(
        tmp_path / "output", include_target_crop=True, crop_padding=24
    ).build(
        session_id=7,
        spec=spec,
        paper_entries=[entry],
        answer_regions=regions,
        batch_index=1,
        target_detail_question_ids_by_paper_key={"paper-1": ["10-1", "10-2"]},
    )

    with Image.open(result["manifest"]["target_crop_path"]) as crop:
        # Page-coordinate union (10,10)-(160,120) plus 24px padding, clipped
        # to the 2400x300 source page — not the downscaled composite.
        assert crop.size == (184, 144)


def test_full_page_prompt_uses_region_hints_not_tile_map(tmp_path: Path) -> None:
    spec, entry, regions = _full_page_setup(tmp_path)
    built = hybrid.FullPageEvidenceBuilder(tmp_path / "output").build(
        session_id=7,
        spec=spec,
        paper_entries=[entry],
        answer_regions=regions,
        batch_index=1,
    )

    system, static, dynamic = build_hybrid_major_prompt(spec, built["manifest"])

    combined = system + static + dynamic
    assert "QUESTION_REGION_HINTS" in combined
    assert "不是裁切边界" in system
    assert "整页原图" in combined
    assert "TILE_TO_SUBQUESTION_MAP" not in combined
    assert "10-2" in dynamic


def test_collage_prompt_keeps_tile_mapping() -> None:
    spec = MajorQuestionSpec(
        question_id="Q10",
        detail_question_ids=["10-1"],
        rubric={"question_id": "Q10", "max_score": 4},
        answer_key={},
        max_score=4,
    )
    manifest = {
        "mode": "hybrid_major_batch",
        "items": [
            {
                "paper_key": "p",
                "student_id": 1,
                "student_name": "学生甲",
                "target_detail_question_ids": ["10-1"],
                "sub_items": [
                    {
                        "part_id": "10-1",
                        "tile_label": "01. [学生甲] 小问: 10-1",
                        "page": "front",
                        "bbox": {"x": 0, "y": 0, "w": 10, "h": 10},
                    }
                ],
            }
        ],
    }

    system, static, dynamic = build_hybrid_major_prompt(spec, manifest)

    assert "TILE_TO_SUBQUESTION_MAP" in system + dynamic


def test_full_page_run_sends_one_student_per_major_request(tmp_path: Path) -> None:
    groups = []
    for index in (1, 2):
        front = tmp_path / f"front_{index}.jpg"
        back = tmp_path / f"back_{index}.jpg"
        Image.new("RGB", (300, 200), "white").save(front)
        Image.new("RGB", (300, 200), "white").save(back)
        groups.append(
            ExamPaperGroup(front, back, f"学生{index}", index, source_label=f"scan_{index}")
        )
    rubric = {
        "total_score": 8,
        "questions": [
            {
                "question_id": "Q10",
                "question_type": "proof",
                "parts": [
                    {"part_id": "10-1", "part_score": 4},
                    {"part_id": "10-2", "part_score": 4},
                ],
            }
        ],
    }
    answer_key = {"questions": [{"question_id": "Q10", "canonical_answer": "略"}]}
    regions = [
        {"page": "front", "mapped_question_id": "Q10(1)", "x": 10, "y": 10, "w": 80, "h": 50},
        {"page": "back", "mapped_question_id": "Q10(2)", "x": 20, "y": 30, "w": 60, "h": 40},
    ]

    class FullPageClient:
        def __init__(self) -> None:
            self.calls = 0

        def json_from_images_once(self, _static, _images, **kwargs):
            self.calls += 1
            prompt = str(kwargs.get("dynamic_prompt") or "")
            manifest = json.loads(prompt.split("BATCH_MANIFEST_JSON)】：", 1)[1].strip())
            return {
                "question_id": manifest["question_id"],
                "items": [
                    {
                        "paper_key": item["paper_key"],
                        "student_id": item["student_id"],
                        "grading_details": [
                            {
                                "question_id": qid,
                                "score_awarded": 4,
                                "confidence_score": 95,
                                "needs_human_review": False,
                            }
                            for qid in item["target_detail_question_ids"]
                        ],
                    }
                    for item in manifest["items"]
                ],
            }

    client = FullPageClient()
    run = run_hybrid_batch_grading(
        session_id=7,
        paper_groups=groups,
        answer_regions=regions,
        rubric=rubric,
        answer_key=answer_key,
        llm_client=client,
        grading_model="fake",
        output_root=tmp_path / "out",
        include_objective_local=False,
        subjective_evidence="full_page",
        result_mode="ai",
    )

    assert client.calls == 2  # 2 students × 1 major question
    manifests = sorted((tmp_path / "out").rglob("*_manifest.json"))
    assert len(manifests) == 2
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["mode"] == "full_page_subjective"
        assert len(manifest["items"]) == 1
    for result in run.results_by_paper_key.values():
        assert result.raw_json["mode"] == "ai"
        assert len(result.grading_details) == 2
