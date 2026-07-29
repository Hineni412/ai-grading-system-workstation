from __future__ import annotations

import json
from pathlib import Path

import backend.config_workspace.publish as publish_module


class _LocalTestFilesystem:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def atomic_write_bytes(self, path: Path, content: bytes) -> None:
        Path(path).write_bytes(content)

    def unlink_many(self, paths) -> None:
        for path in paths:
            Path(path).unlink(missing_ok=True)


def _legacy_single_part_payload() -> dict:
    rubric_questions = []
    answer_questions = []
    for index, score in enumerate((17, 17, 17, 17, 17, 15), start=1):
        parent_id = f"Q{index}"
        legacy_part_id = f"{parent_id}-1"
        rubric_questions.append(
            {
                "question_id": str(index),
                "question_type": "comprehensive",
                "max_score": score,
                "parts": [
                    {
                        "part_id": legacy_part_id,
                        "part_score": score,
                        "steps": [
                            {
                                "step_id": "S1",
                                "step_score": score,
                                "core_goal": "完成解答",
                                "required_elements": ["有效过程"],
                                "allow_alternative_methods": True,
                            }
                        ],
                    }
                ],
            }
        )
        answer_questions.append(
            {
                "question_id": str(index),
                "canonical_answer": f"答案 {index}",
                "accepted_forms": [f"答案 {index}"],
                "method_variants": [],
                "parts": [
                    {
                        "part_id": legacy_part_id,
                        "answer": f"答案 {index}",
                        "analysis": "",
                        "step_milestones": [],
                    }
                ],
            }
        )
    return {
        "rubric": {
            "exam_title": "题号发布测试",
            "total_score": 100,
            "questions": rubric_questions,
        },
        "answer_key": {"questions": answer_questions},
        "meta": {"warnings": []},
    }


def test_config_publication_writes_only_canonical_question_identities(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        publish_module,
        "SecureRootFilesystem",
        _LocalTestFilesystem,
    )

    publication = publish_module.publish_generated_config(
        tmp_path,
        _legacy_single_part_payload(),
        token="a" * 32,
    )

    rubric = json.loads(publication.rubric_path.read_text(encoding="utf-8"))
    answer_key = json.loads(
        publication.answer_key_path.read_text(encoding="utf-8")
    )
    expected_ids = [f"Q{index}" for index in range(1, 7)]
    assert [item["question_id"] for item in rubric["questions"]] == expected_ids
    assert [
        item["parts"][0]["part_id"] for item in rubric["questions"]
    ] == expected_ids
    assert [
        item["question_id"] for item in answer_key["questions"]
    ] == expected_ids
    assert [
        item["parts"][0]["part_id"] for item in answer_key["questions"]
    ] == expected_ids
