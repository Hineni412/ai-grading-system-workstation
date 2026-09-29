from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from ai_batch_grading_service import run_ai_batch_grading
from scanner import ExamPaperGroup


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


def test_full_page_run_sends_one_student_per_major_request(tmp_path: Path) -> None:
    groups = []
    for index in (1, 2):
        front = tmp_path / f"front_{index}.jpg"
        back = tmp_path / f"back_{index}.jpg"
        Image.new("RGB", (300, 200), "white").save(front)
        Image.new("RGB", (300, 200), "white").save(back)
        groups.append(
            ExamPaperGroup(
                front, back, f"学生{index}", index, source_label=f"scan_{index}"
            )
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
        {
            "page": "front",
            "mapped_question_id": "Q10(1)",
            "x": 10,
            "y": 10,
            "w": 80,
            "h": 50,
        },
        {
            "page": "back",
            "mapped_question_id": "Q10(2)",
            "x": 20,
            "y": 30,
            "w": 60,
            "h": 40,
        },
    ]

    class FullPageClient:
        def __init__(self) -> None:
            self.calls = 0

        def json_from_images_once(self, _static, _images, **kwargs):
            self.calls += 1
            prompt = str(kwargs.get("dynamic_prompt") or "")
            manifest = json.loads(
                prompt.split("BATCH_MANIFEST_JSON)】：", 1)[1].strip()
            )
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
    run = run_ai_batch_grading(
        session_id=7,
        paper_groups=groups,
        answer_regions=regions,
        rubric=rubric,
        answer_key=answer_key,
        llm_client=client,
        grading_model="fake",
        output_root=tmp_path / "out",
        include_objective_local=False,
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
