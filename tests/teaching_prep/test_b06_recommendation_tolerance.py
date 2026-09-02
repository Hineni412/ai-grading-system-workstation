"""回归：备选/排除的选题建议允许缺省标题与分钟数（模型对非入选题的合理省略）。"""
from __future__ import annotations

from backend.teaching_prep.application.lesson_drafts import (
    build_local_template,
    validate_draft_payload,
)
from backend.teaching_prep.application.preferences import (
    DEFAULT_TEACHING_PREFERENCES,
)
from backend.teaching_prep.domain.models import ResourcePackVersion


def _pack() -> ResourcePackVersion:
    payload = {
        "lesson": {
            "lesson_node_id": "lesson-1",
            "title": "第1课时 勾股定理的应用",
            "lesson_type": "review",
        },
        "materials": [
            {
                "link_id": "link-1",
                "purpose": "reference_ppt",
                "units": [
                    {
                        "unit_id": "u1",
                        "unit_index": 1,
                        "unit_kind": "ppt_slide",
                        "title": "引入",
                        "text": "勾股定理应用引入",
                        "text_status": "embedded",
                        "object_summary": {},
                    },
                ],
            },
        ],
        "evidence": {
            "question": {
                "items": [
                    {
                        "question_id": 7,
                        "question_number": "12",
                        "text_excerpt": "勾股定理应用题",
                        "difficulty": "4",
                        "knowledge_points": [],
                        "updated_at": "2026-09-02T00:00:00Z",
                    },
                ],
            },
        },
        "missing_and_uncertain": [],
        "preparation_preferences": dict(DEFAULT_TEACHING_PREFERENCES),
    }
    return ResourcePackVersion(
        id="pack-1",
        lesson_node_id="lesson-1",
        version_number=1,
        source_state_sha256="0" * 64,
        pack_sha256="0" * 64,
        payload=payload,
        created_at="2026-09-02T00:00:00Z",
    )


def test_backup_and_exclude_recommendations_may_omit_title_and_minutes() -> None:
    pack = _pack()
    payload = build_local_template(pack)
    payload["exercise_recommendations"] = [
        {
            "source_ref": "question:7",
            "action": "backup",
            "reason": "略难，留作课后拓展",
            "citations": ["question:7"],
        },
        {
            "source_ref": "question:7",
            "action": "exclude",
            "title": "",
            "estimated_minutes": 0,
            "reason": "超出本课范围",
            "citations": ["question:7"],
        },
    ]

    result = validate_draft_payload(payload, pack)

    recommendations = result["exercise_recommendations"]
    assert recommendations[0]["title"] == ""
    assert recommendations[0]["estimated_minutes"] == 0
    assert recommendations[1]["title"] == ""


def test_include_recommendation_still_requires_title_and_minutes() -> None:
    import pytest

    from backend.teaching_prep.domain.errors import TeachingPrepValidationError

    pack = _pack()
    payload = build_local_template(pack)
    payload["exercise_recommendations"] = [
        {
            "source_ref": "question:7",
            "action": "include",
            "reason": "高频基础题",
            "citations": ["question:7"],
        },
    ]

    with pytest.raises(TeachingPrepValidationError):
        validate_draft_payload(payload, pack)
