"""Design E: exam rubric steps reference evidence point ids (§7.1–§7.3)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from question_bank.database.schema import connect, initialize_database
from question_bank.services.source_question_link_service import (
    SourceQuestionLinkService,
)
from question_bank.solution_evidence.evidence_snapshot import (
    freeze_session_evidence_snapshot,
    validate_rubric_evidence_coverage,
)
from integration.question_tag_projection_service import (
    QuestionTagProjectionService,
)
from integration.diagnosis_profile_service import _step_target_contributions
from tests.current_knowledge_support import install_current_knowledge

_SECTION_KEY = "kp_bnu24_math_g8_upper_1_1"
_LEAF_KEY = "kp_bnu24_math_g8_upper_1_1_1"
_SKILL_KEY = "sk_bnu24_math_g8_upper_1_1_01"
_OTHER_SKILL_KEY = "sk_bnu24_math_g8_upper_1_2_01"
_VERSION = "d" * 64
_SESSION = 7


def _point(point_id: str, **overrides) -> dict:
    point = {
        "evidence_point_id": point_id,
        "target": f"目标{point_id}",
        "justification": "",
        "answer_anchor": "",
        "observable_evidence": f"证据{point_id}",
        "equivalent_rules": [],
        "counterexamples": [],
        "depends_on": [],
    }
    point.update(overrides)
    return point


def _seed_bank_question(
    db_path: Path,
    release_id: str,
    *,
    question_id: int = 1,
    points: list[dict] | None = None,
    links: list[tuple[str, str, str, str]] | None = None,
    part_id: str = "part-1",
    part_overrides: dict | None = None,
) -> None:
    """links: (point_id, role, stable_key, resolution_status)."""
    evidence_part = {
        "part_id": part_id,
        "response_mode": "process",
        "allow_alternative_methods": False,
        "proof_obligations": [],
        "evidence_points": points or [],
    }
    if part_overrides:
        evidence_part.update(part_overrides)
    with connect(db_path) as connection:
        connection.execute(
            "INSERT INTO papers(id,title,import_status) VALUES(1,'合成','ready')"
        )
        connection.execute(
            "INSERT INTO questions(id,paper_id,question_number,question_text)"
            " VALUES(?,1,'1','合成题')",
            (question_id,),
        )
        connection.execute(
            """
            INSERT INTO question_solution_evidence_versions(
                evidence_version_id, question_id, source_content_hash,
                schema_version, content_hash, evidence_json, status,
                source_kind, source_reference, created_by, graph_release_id
            ) VALUES (?, ?, ?, 'question-solution-evidence-v2', ?, ?,
                      'approved', 'backfill', 'synthetic', 'test', ?)
            """,
            (
                _VERSION,
                question_id,
                "e" * 64,
                "f" * 64,
                json.dumps(
                    {"parts": [evidence_part]},
                    ensure_ascii=False,
                ),
                release_id,
            ),
        )
        connection.executemany(
            """
            INSERT INTO evidence_point_knowledge_links(
                evidence_version_id, question_id, part_id, evidence_point_id,
                graph_release_id, role, term_id, stable_key,
                resolution_status, weight, source_kind, source_reference
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0,
                      'link_job', 'synthetic')
            """,
            [
                (
                    _VERSION,
                    question_id,
                    part_id,
                    point_id,
                    release_id,
                    role,
                    stable_key,
                    stable_key,
                    status,
                )
                for point_id, role, stable_key, status in links or ()
            ],
        )


def _confirm_link(
    db_path: Path,
    *,
    session_id: int = _SESSION,
    source_ref: str = "Q1",
    bank_question_id: int = 1,
) -> None:
    SourceQuestionLinkService(db_path).confirm_link(
        grading_session_id=session_id,
        source_question_id=source_ref,
        bank_question_id=bank_question_id,
        link_method="synthetic",
    )


def _freeze(tmp_path: Path, db_path: Path, *, session_id: int = _SESSION) -> None:
    freeze_session_evidence_snapshot(
        db_path,
        grading_session_id=session_id,
        upload_config_dir=tmp_path / "config" / "uploaded",
        data_root=tmp_path,
    )


def _projection_service(tmp_path: Path, db_path: Path) -> QuestionTagProjectionService:
    return QuestionTagProjectionService(db_path, data_root=tmp_path)


def _rubric(steps: list[dict], *, part_id: str = "part-1") -> dict:
    return {
        "questions": [
            {
                "question_id": "Q1",
                "parts": [
                    {
                        "part_id": part_id,
                        "steps": steps,
                    }
                ],
            }
        ]
    }


def _step(step_id: str, point_ids: list[str], **overrides) -> dict:
    step = {
        "step_id": step_id,
        "step_score": 3,
        "evidence_point_ids": point_ids,
    }
    step.update(overrides)
    return step


def _setup(tmp_path: Path) -> tuple[Path, str]:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    release_id = install_current_knowledge(db_path, taxonomy_revision=5)
    return db_path, release_id


def test_frozen_snapshot_survives_later_link_changes(tmp_path: Path) -> None:
    """冻结后题库链接重生成不影响历史会话投影。"""
    db_path, release_id = _setup(tmp_path)
    _seed_bank_question(
        db_path,
        release_id,
        points=[_point("p1")],
        links=[("p1", "direct", _SKILL_KEY, "resolved")],
    )
    _confirm_link(db_path)
    _freeze(tmp_path, db_path)

    with connect(db_path) as connection:
        connection.execute(
            """
            UPDATE evidence_point_knowledge_links
            SET stable_key = ?, term_id = ?
            WHERE evidence_version_id = ? AND evidence_point_id = 'p1'
            """,
            (_OTHER_SKILL_KEY, _OTHER_SKILL_KEY, _VERSION),
        )

    # A repeated sync/retry must not silently replace the frozen facts either.
    _freeze(tmp_path, db_path)

    projection = _projection_service(tmp_path, db_path).project_session(
        grading_session_id=_SESSION,
        rubric=_rubric([_step("S1", ["p1"])]),
    )

    (item,) = projection.items
    assert item.tags["knowledge_point"] == (_SKILL_KEY,)


def _projected_with_steps() -> object:
    from integration.question_tag_projection_service import (
        ProjectedQuestionTags,
    )

    return ProjectedQuestionTags(
        item_ref="part-1",
        parent_ref="Q1",
        bank_question_id=1,
        tags={"knowledge_point": (_SKILL_KEY, _LEAF_KEY)},
        steps=(
            {
                "step_id": "S1",
                "part_id": "part-1",
                "step_score": 4,
                "evidence_point_ids": ["p1"],
            },
            {
                "step_id": "S2",
                "part_id": "part-1",
                "step_score": 2,
                "evidence_point_ids": ["p2", "p3"],
            },
        ),
        step_targets={
            "S1": (_SKILL_KEY,),
            "S2": (_LEAF_KEY, _SKILL_KEY),
        },
    )


def test_teacher_final_total_does_not_restore_superseded_ai_step_scores():
    from types import SimpleNamespace
    from integration.diagnosis_profile_service import DiagnosisProfileService

    service = object.__new__(DiagnosisProfileService)
    projected = _projected_with_steps()
    service.db = SimpleNamespace(
        results=SimpleNamespace(
            get_active_assessment_evidence=lambda **kwargs: [
            {
                "session_id": 1,
                "student_id": 1,
                "question_id": projected.item_ref,
                "teacher_final_revision": 1,
                "score_awarded": 6,
                "teacher_final_max_score": 6,
                "assessment_state": {
                    "step_assessments": [
                        {"step_id": "S1", "achievement": "none", "score_awarded": 0}
                    ]
                },
            }
        ])
    )
    (row,) = service._projected_tag_evidence(
        student_ids=["1"],
        session_ids=[1],
        projection_by_session={1: SimpleNamespace(items=[projected])},
    )
    assert row["score_awarded"] == row["full_score"] == 6
    assert "target_contributions" not in row and "point_observations" not in row
    assert row["assessment"]["granularity"] == "whole_question"


def test_mastery_uses_each_point_instead_of_exam_score_allocation():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from question_bank.mastery.current import CurrentMasteryCalculator

    reference = {
        "session_id": 1,
        "question_id": "Q1",
        "score_awarded": 9,
        "full_score": 10,
        "assessment": {
            "granularity": "part",
            "point_observations": [
                {"point_id": "p1", "stable_key": _SKILL_KEY, "weight": 0.5, "achieved": 1},
                {"point_id": "p2", "stable_key": _SKILL_KEY, "weight": 0.5, "achieved": 0},
            ],
        },
    }
    calculator = object.__new__(CurrentMasteryCalculator)
    calculator.resolver = SimpleNamespace(resolve=lambda key: [SimpleNamespace(stable_key=key)])
    observations = calculator.exam_observations({
        "students": [{"student_id": "1", "weak_points": [{
            "knowledge_key": _SKILL_KEY, "source_question_refs": [reference],
        }]}],
        "_mastery_session_times": {1: datetime(2026, 9, 17, tzinfo=timezone.utc)},
    })
    assert [item["y"] for item in observations] == [1, 0]
    assert [item["item"] for item in observations] == [(1, "Q1", "p1"), (1, "Q1", "p2")]
    assert all(item["links"] == {_SKILL_KEY: 1} for item in observations)
