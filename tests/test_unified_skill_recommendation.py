from __future__ import annotations

import json
from pathlib import Path

from question_bank.database.schema import connect, initialize_database
from question_bank.models.skill_catalog import ResolvedSkillLink
from question_bank.recommendation.practice_plan_service import PracticePlanService
from question_bank.services.skill_catalog_service import SkillCatalogService
from question_bank.services.skill_link_service import SkillLinkService
from question_bank.services.training_task_service import TrainingTaskService


def _insert_question(conn, question_id: int, name: str, difficulty: int) -> None:
    question_bodies = (
        "calculate an unknown angle from two equal rays",
        "prove triangle congruence with a bisecting segment",
        "find a ratio after drawing an auxiliary diagonal",
        "judge a geometric statement from parallel lines",
        "construct a ray and explain every compass step",
        "compare distances from one point to two sides",
        "complete a coordinate argument about a moving point",
        "derive an area formula from a folded paper diagram",
        "select the valid condition and reject the distractors",
        "write a counterexample for the proposed conclusion",
        "use symmetry to locate the shortest route",
    )
    question_text = question_bodies[question_id % len(question_bodies)]
    conn.execute(
        """
        INSERT INTO papers (id, title, source_file, grade, import_status)
        VALUES (?, ?, ?, 'grade-8', 'ready')
        """,
        (question_id, f"source-{name}", f"{name}.docx"),
    )
    conn.execute(
        """
        INSERT INTO questions (
            id, paper_id, question_number, question_type, question_text,
            answer_text, difficulty
        ) VALUES (?, ?, '1', 'solution', ?, 'reference-answer', ?)
        """,
        (question_id, question_id, f"{name}: {question_text}", str(difficulty)),
    )
    conn.execute(
        """
        INSERT INTO question_tags (question_id, tag_type, tag_value, source)
        VALUES (?, 'method', ?, 'manual')
        """,
        (question_id, f"method-{question_id}"),
    )


def _skill_system(tmp_path: Path) -> tuple[PracticePlanService, dict, dict[str, int]]:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    catalog = SkillCatalogService(db_path)
    target = catalog.find_by_stable_key("math.geometry.line_angle.bisector")
    neighbor = catalog.find_by_stable_key("math.geometry.construction.bisector")
    same_topic = catalog.find_by_stable_key("math.geometry.line_angle.parallel_property")
    ids = {
        "target": target["id"],
        "neighbor": neighbor["id"],
        "same_topic": same_topic["id"],
    }
    with connect(db_path) as conn:
        for index, question_id in enumerate(range(101, 106), start=1):
            _insert_question(conn, question_id, f"exact-{index}", 2 + index)
        _insert_question(conn, 106, "supporting-only", 5)
        _insert_question(conn, 107, "same-topic-only", 5)
        for index, question_id in enumerate(range(201, 205), start=1):
            _insert_question(conn, question_id, f"neighbor-{index}", 3 + index)
    links = SkillLinkService(db_path)
    for question_id in range(101, 106):
        links.replace_question_links(
            question_id,
            [ResolvedSkillLink(ids["target"], "measured")],
        )
    links.replace_question_links(
        106,
        [
            ResolvedSkillLink(ids["same_topic"], "measured"),
            ResolvedSkillLink(ids["target"], "supporting"),
        ],
    )
    links.replace_question_links(
        107,
        [ResolvedSkillLink(ids["same_topic"], "measured")],
    )
    for question_id in range(201, 205):
        links.replace_question_links(
            question_id,
            [ResolvedSkillLink(ids["neighbor"], "measured")],
        )
    profile = {
        "diagnosis_identity": "skill",
        "scope": {"mode": "student", "student_ids": ["12"]},
        "exam_scope": {"mode": "current", "session_ids": []},
        "confirmed_skill_ids": [ids["target"]],
        "students": [
            {
                "student_id": "12",
                "student_name": "Test Student",
                "class_id": "grade-8-class-1",
                "score_rate": 0.5,
                "weak_points": [
                    {
                        "skill_id": ids["target"],
                        "skill_name": "Angle bisector properties",
                        "topic_name": "Lines and angles",
                        "mastery": 0.3,
                        "eligible_for_recommendation": True,
                    }
                ],
            }
        ],
    }
    return PracticePlanService(db_path), profile, ids


def test_skill_mode_exact_recommendations_require_same_measured_skill_id(tmp_path: Path) -> None:
    service, profile, ids = _skill_system(tmp_path)

    plan = service.generate(
        profile,
        question_count=8,
        related_fill_policy="ask",
        exclude_current_exam_originals=False,
    )
    variant = plan["variants"][0]

    assert {item["question_id"] for item in variant["items"]} == set(range(101, 106))
    assert all(item["match_kind"] == "exact" for item in variant["items"])
    assert all(item["target_skill_id"] == ids["target"] for item in variant["items"])
    assert all(item["matched_skill_id"] == ids["target"] for item in variant["items"])
    assert 106 not in {item["question_id"] for item in variant["items"]}
    assert 107 not in {item["question_id"] for item in variant["items"]}
    assert variant["shortages"]
    assert all(shortage["decision_required"] for shortage in variant["shortages"])


def test_exact_only_keeps_short_plan_and_neighbor_policy_labels_fill(tmp_path: Path) -> None:
    service, profile, ids = _skill_system(tmp_path)

    exact_only = service.generate(
        profile,
        question_count=8,
        related_fill_policy="exact_only",
        exclude_current_exam_originals=False,
    )["variants"][0]
    filled = service.generate(
        profile,
        question_count=8,
        related_fill_policy="allow_neighbors",
        exclude_current_exam_originals=False,
    )["variants"][0]

    assert len(exact_only["items"]) == 5
    assert all(not shortage["decision_required"] for shortage in exact_only["shortages"])
    assert len(filled["items"]) == 8
    neighbor_items = [item for item in filled["items"] if item["match_kind"] == "neighbor"]
    assert len(neighbor_items) == 3
    assert all(item["matched_skill_id"] == ids["neighbor"] for item in neighbor_items)
    assert all(item["neighbor_kind"] == "same_topic" for item in neighbor_items)
    assert all("related fill" in item["reason"] for item in neighbor_items)
    assert 106 not in {item["question_id"] for item in filled["items"]}
    assert 107 not in {item["question_id"] for item in filled["items"]}


def test_training_task_snapshot_persists_fill_policy_and_skill_trace(tmp_path: Path) -> None:
    service, profile, ids = _skill_system(tmp_path)
    plan = service.generate(
        profile,
        question_count=8,
        related_fill_policy="allow_neighbors",
        exclude_current_exam_originals=False,
    )

    task = TrainingTaskService(service.db_path).create_task(plan, created_by="teacher")
    loaded = TrainingTaskService(service.db_path).get_task(task.id)

    assert loaded["generation_config"]["related_fill_policy"] == "allow_neighbors"
    snapshots = [item["recommendation_snapshot"] for item in loaded["variants"][0]["items"]]
    assert all(item["target_skill_id"] == ids["target"] for item in snapshots)
    assert {item["match_kind"] for item in snapshots} == {"exact", "neighbor"}
    with connect(service.db_path) as conn:
        raw = conn.execute(
            "SELECT concept_snapshot_json FROM training_task_items ORDER BY item_order"
        ).fetchall()
    concept_snapshots = [json.loads(row["concept_snapshot_json"]) for row in raw]
    assert all("target_skill_id" in item for item in concept_snapshots)
