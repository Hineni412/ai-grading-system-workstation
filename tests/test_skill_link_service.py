from __future__ import annotations

from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.models.skill_catalog import ResolvedSkillLink, SkillRole
from question_bank.services.skill_catalog_service import SkillCatalogService


def _skills(db_path: Path) -> tuple[int, int]:
    catalog = SkillCatalogService(db_path)
    measured = catalog.find_by_stable_key("math.geometry.line_angle.bisector")
    supporting = catalog.find_by_stable_key("math.geometry.construction.bisector")
    return int(measured["id"]), int(supporting["id"])


def test_replace_assessment_links_requires_measured_skill_and_is_atomic(tmp_path: Path) -> None:
    from question_bank.services.skill_link_service import SkillLinkService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    measured_id, supporting_id = _skills(db_path)
    service = SkillLinkService(db_path)
    service.replace_assessment_links(
        "12",
        "Q1",
        [ResolvedSkillLink(measured_id, "measured", raw_knowledge_label="角平分线性质")],
    )

    with pytest.raises(ValueError, match="measured"):
        service.replace_assessment_links(
            "12",
            "Q1",
            [ResolvedSkillLink(supporting_id, "supporting")],
        )

    rows = service.assessment_links_for_sessions(["12"])
    assert len(rows) == 1
    assert rows[0]["skill_id"] == measured_id
    assert rows[0]["role"] == "measured"


def test_replace_question_links_and_filter_by_role(tmp_path: Path) -> None:
    from question_bank.services.skill_link_service import SkillLinkService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    measured_id, supporting_id = _skills(db_path)
    with connect(db_path) as conn:
        question_id = int(
            conn.execute(
                "INSERT INTO questions (question_number, question_text) VALUES ('1', '角平分线训练')"
            ).lastrowid
        )
    service = SkillLinkService(db_path)
    service.replace_question_links(
        question_id,
        [
            ResolvedSkillLink(measured_id, SkillRole.MEASURED),
            ResolvedSkillLink(supporting_id, SkillRole.SUPPORTING),
        ],
    )

    measured_rows = service.question_links_for_skills(
        [measured_id, supporting_id],
        role=SkillRole.MEASURED,
    )
    all_rows = service.question_links_for_skills([measured_id, supporting_id])

    assert [(row["question_id"], row["skill_id"]) for row in measured_rows] == [
        (question_id, measured_id)
    ]
    assert {row["role"] for row in all_rows} == {"measured", "supporting"}


def test_link_reads_follow_merged_skill_redirect(tmp_path: Path) -> None:
    from question_bank.services.skill_link_service import SkillLinkService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    catalog = SkillCatalogService(db_path)
    source = catalog.find_by_stable_key("math.geometry.construction.fold_path")
    target = catalog.find_by_stable_key("math.geometry.construction.shortest_path")
    service = SkillLinkService(db_path)
    service.replace_assessment_links(
        "15",
        "Q2",
        [ResolvedSkillLink(source["id"], "measured")],
    )
    catalog.merge_skill(source["id"], target["id"], actor="admin")

    row = service.assessment_links_for_sessions(["15"])[0]

    assert row["stored_skill_id"] == source["id"]
    assert row["skill_id"] == target["id"]
    assert row["skill_name"] == target["name"]
