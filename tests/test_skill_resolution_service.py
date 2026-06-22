from __future__ import annotations

import json
from pathlib import Path

from question_bank.database.schema import connect, initialize_database
from question_bank.models.skill_catalog import (
    ResolutionOutcome,
    SkillResolutionRequest,
)
from question_bank.services.skill_catalog_service import SkillCatalogService


FIXTURE = Path(__file__).parent / "fixtures" / "skill_resolution_cases.json"


def _request(label: str, *, source_ref: str = "Q1", stable_key_hint: str = "") -> SkillResolutionRequest:
    return SkillResolutionRequest(
        source_type="assessment_item",
        source_ref=source_ref,
        raw_label=label,
        stable_key_hint=stable_key_hint,
        grade="八年级",
        question_text=f"考查{label}",
    )


def test_stable_key_match_wins_without_ai(tmp_path: Path) -> None:
    from question_bank.services.skill_resolution_service import SkillResolutionService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    service = SkillResolutionService(db_path)

    result = service.resolve(
        _request(
            "评分标准本地名称",
            stable_key_hint="math.geometry.line_angle.bisector",
        )
    )
    skill = SkillCatalogService(db_path).get_skill(result.skill_id)

    assert result.outcome is ResolutionOutcome.RESOLVED_EXISTING
    assert result.confidence == 1.0
    assert skill["name"] == "角平分线性质"


def test_exact_names_and_aliases_resolve_critical_skills(tmp_path: Path) -> None:
    from question_bank.services.skill_resolution_service import SkillResolutionService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    service = SkillResolutionService(db_path)
    catalog = SkillCatalogService(db_path)
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))

    for index, case in enumerate(cases, start=1):
        result = service.resolve(_request(case["raw_label"], source_ref=f"Q{index}"))
        expected = catalog.find_by_stable_key(case["expected_key"])
        assert result.outcome is ResolutionOutcome.RESOLVED_EXISTING
        assert result.skill_id == expected["id"]


def test_redirected_skill_resolves_to_active_target(tmp_path: Path) -> None:
    from question_bank.services.skill_resolution_service import SkillResolutionService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    catalog = SkillCatalogService(db_path)
    source = catalog.find_by_stable_key("math.geometry.construction.fold_path")
    target = catalog.find_by_stable_key("math.geometry.construction.shortest_path")
    catalog.merge_skill(source["id"], target["id"], actor="admin")

    result = SkillResolutionService(db_path).resolve(
        _request("轴对称最短路径", stable_key_hint=source["stable_key"])
    )

    assert result.skill_id == target["id"]


def test_ambiguous_alias_becomes_conflict(tmp_path: Path) -> None:
    from question_bank.services.skill_resolution_service import SkillResolutionService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        topic_id = conn.execute(
            "SELECT id FROM skill_topics WHERE stable_key = 'math.geometry.construction'"
        ).fetchone()[0]
        for suffix, name in (("a", "本校作图甲"), ("b", "本校作图乙")):
            conn.execute(
                """
                INSERT INTO skills (
                    stable_key, topic_id, name, aliases_json, grade_min, grade_max, origin
                ) VALUES (?, ?, ?, '[\"校本作法\"]', 7, 9, 'local')
                """,
                (f"local.math.construction.{suffix}", topic_id, name),
            )

    result = SkillResolutionService(db_path).resolve(_request("校本作法"))

    assert result.outcome is ResolutionOutcome.CONFLICT
    assert len(result.candidates) == 2


def test_broad_and_unknown_labels_become_explicit_conflicts(tmp_path: Path) -> None:
    from question_bank.services.skill_resolution_service import SkillResolutionService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    service = SkillResolutionService(db_path)

    broad = service.resolve(_request("作图", source_ref="Q1"))
    unknown = service.resolve(_request("本校未登记的新技能", source_ref="Q2"))

    assert broad.outcome is ResolutionOutcome.CONFLICT
    assert "过宽" in broad.reason
    assert unknown.outcome is ResolutionOutcome.CONFLICT
    assert "无法确定" in unknown.reason


def test_same_source_conflict_is_upserted_not_duplicated(tmp_path: Path) -> None:
    from question_bank.services.skill_resolution_service import SkillResolutionService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    service = SkillResolutionService(db_path)
    request = _request("未知训练法", source_ref="Q9")

    service.resolve(request)
    service.resolve(request)

    with connect(db_path) as conn:
        count = conn.execute(
            """
            SELECT COUNT(*) FROM skill_resolution_conflicts
            WHERE source_type = 'assessment_item'
              AND source_ref = 'Q9'
              AND raw_label = '未知训练法'
              AND state = 'open'
            """
        ).fetchone()[0]
    assert count == 1


def test_resolved_conflict_decision_is_reused(tmp_path: Path) -> None:
    from question_bank.services.skill_resolution_service import SkillResolutionService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    catalog = SkillCatalogService(db_path)
    target = catalog.find_by_stable_key("math.geometry.construction.shortest_path")
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO skill_resolution_conflicts (
                source_type, source_ref, raw_label, normalized_label, reason,
                state, resolved_skill_id, resolved_by, resolved_at
            ) VALUES ('assessment_item', 'Q8', '校本路径法', '校本路径法',
                      '管理员选择', 'resolved', ?, 'admin', datetime('now','localtime'))
            """,
            (target["id"],),
        )

    result = SkillResolutionService(db_path).resolve(
        _request("校本路径法", source_ref="Q8")
    )

    assert result.outcome is ResolutionOutcome.RESOLVED_EXISTING
    assert result.skill_id == target["id"]
