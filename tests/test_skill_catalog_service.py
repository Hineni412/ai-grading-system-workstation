from __future__ import annotations

"""Regression coverage for skill catalog service behavior."""

import json
from pathlib import Path

from question_bank.database.schema import connect, initialize_database
from question_bank.services.skill_catalog_service import SkillCatalogService


def _open_question_conflict(db_path: Path) -> tuple[SkillCatalogService, int, int]:
    initialize_database(db_path)
    service = SkillCatalogService(db_path)
    skill = service.find_by_stable_key("math.geometry.line_angle.bisector")
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (id, title, source_file, import_status) VALUES (1, 'P', 'p.docx', 'ready')"
        )
        conn.execute(
            """
            INSERT INTO questions (id, paper_id, question_number, question_text, answer_text)
            VALUES (1, 1, '1', 'question context', 'answer context')
            """
        )
        cursor = conn.execute(
            """
            INSERT INTO skill_resolution_conflicts (
                source_type, source_ref, raw_label, normalized_label,
                candidate_skill_ids_json, reason, evidence_json, state
            ) VALUES ('question_bank_item', '1', 'angle skill', 'angle skill', ?, 'ambiguous', '{}', 'open')
            """,
            (json.dumps([skill["id"]]),),
        )
        conflict_id = int(cursor.lastrowid)
    return service, conflict_id, int(skill["id"])


def test_resolve_conflict_closes_inbox_and_creates_measured_link(tmp_path: Path) -> None:
    service, conflict_id, skill_id = _open_question_conflict(tmp_path / "question_bank.db")

    service.resolve_conflict(conflict_id, skill_id, actor="admin")

    with connect(service.db_path) as conn:
        conflict = conn.execute(
            "SELECT state, resolved_skill_id, resolved_by FROM skill_resolution_conflicts WHERE id = ?",
            (conflict_id,),
        ).fetchone()
        link = conn.execute(
            "SELECT question_id, skill_id, role FROM question_skill_links WHERE question_id = 1"
        ).fetchone()
    assert tuple(conflict) == ("resolved", skill_id, "admin")
    assert tuple(link) == (1, skill_id, "measured")


def test_local_creation_ignore_and_neighbor_toggle_are_auditable(tmp_path: Path) -> None:
    service, conflict_id, _ = _open_question_conflict(tmp_path / "question_bank.db")
    topic_id = service.list_topics()[0]["id"]

    local_id = service.create_local_from_conflict(
        conflict_id,
        "本校角度辅助线判定",
        topic_id,
        actor="admin",
    )
    local = service.get_skill(local_id)
    second_service, second_conflict, _ = _open_question_conflict(tmp_path / "second.db")
    second_service.ignore_conflict(second_conflict, actor="admin")
    neighbor = service.list_neighbors()[0]
    service.set_neighbor_enabled(neighbor["id"], False, actor="admin")

    assert local["origin"] == "local"
    assert local["name"] == "本校角度辅助线判定"
    with connect(second_service.db_path) as conn:
        assert conn.execute(
            "SELECT state, resolved_by FROM skill_resolution_conflicts WHERE id = ?",
            (second_conflict,),
        ).fetchone()[:] == ("ignored", "admin")
    assert next(item for item in service.list_neighbors(include_disabled=True) if item["id"] == neighbor["id"])["enabled"] == 0


def test_coverage_keeps_question_bank_and_rubric_counts_separate(tmp_path: Path) -> None:
    service, _, _ = _open_question_conflict(tmp_path / "question_bank.db")

    coverage = service.coverage_summary()

    assert set(coverage) == {"question_bank", "assessment"}
    assert set(coverage["question_bank"]) >= {"total", "resolved", "conflicts"}
    assert set(coverage["assessment"]) >= {"total", "resolved", "conflicts"}
