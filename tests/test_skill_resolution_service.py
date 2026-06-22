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


def _request(
    label: str,
    *,
    source_ref: str = "Q1",
    stable_key_hint: str = "",
    grade: str = "八年级",
    topic_hint: str = "",
) -> SkillResolutionRequest:
    return SkillResolutionRequest(
        source_type="assessment_item",
        source_ref=source_ref,
        raw_label=label,
        stable_key_hint=stable_key_hint,
        grade=grade,
        topic_hint=topic_hint,
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


class _StaticRanker:
    def __init__(self, ranking) -> None:
        self.ranking = ranking

    def rank(self, request, candidates):
        return self.ranking


class _FailingRanker:
    def rank(self, request, candidates):
        raise RuntimeError("service unavailable")


def test_context_candidate_accepts_exact_threshold_and_margin(tmp_path: Path) -> None:
    from question_bank.services.skill_context_ranker import ContextRanking
    from question_bank.services.skill_resolution_service import SkillResolutionService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    catalog = SkillCatalogService(db_path)
    first = catalog.find_by_stable_key("math.geometry.line_angle.bisector")
    second = catalog.find_by_stable_key("math.geometry.line_angle.parallel_property")
    ranking = ContextRanking(
        candidates=(
            {"skill_id": first["id"], "confidence": 0.92, "reason": "上下文一致"},
            {"skill_id": second["id"], "confidence": 0.77, "reason": "次选"},
        )
    )

    result = SkillResolutionService(
        db_path,
        context_ranker=_StaticRanker(ranking),
    ).resolve(_request("题干中的具体角关系", topic_hint="线与角"))

    assert result.outcome is ResolutionOutcome.RESOLVED_EXISTING
    assert result.skill_id == first["id"]
    assert result.confidence == 0.92


def test_context_candidate_rejects_below_threshold_or_margin(tmp_path: Path) -> None:
    from question_bank.services.skill_context_ranker import ContextRanking
    from question_bank.services.skill_resolution_service import SkillResolutionService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    catalog = SkillCatalogService(db_path)
    first = catalog.find_by_stable_key("math.geometry.line_angle.bisector")
    second = catalog.find_by_stable_key("math.geometry.line_angle.parallel_property")
    cases = (
        (0.9199, 0.60),
        (0.92, 0.7701),
    )
    for index, (first_score, second_score) in enumerate(cases, start=1):
        ranking = ContextRanking(
            candidates=(
                {"skill_id": first["id"], "confidence": first_score, "reason": "首选"},
                {"skill_id": second["id"], "confidence": second_score, "reason": "次选"},
            )
        )
        result = SkillResolutionService(
            db_path,
            context_ranker=_StaticRanker(ranking),
        ).resolve(_request(f"阈值边界{index}", source_ref=f"B{index}", topic_hint="线与角"))
        assert result.outcome is ResolutionOutcome.CONFLICT


def test_context_candidate_requires_matching_grade_topic_and_no_ambiguity(tmp_path: Path) -> None:
    from question_bank.services.skill_context_ranker import ContextRanking
    from question_bank.services.skill_resolution_service import SkillResolutionService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    catalog = SkillCatalogService(db_path)
    candidate = catalog.find_by_stable_key("math.geometry.construction.circumcenter")
    base_candidate = ({"skill_id": candidate["id"], "confidence": 0.99, "reason": "高分"},)
    cases = (
        (_request("外心分析甲", source_ref="G1", grade="七年级", topic_hint="尺规作图与最短路径"), ContextRanking(candidates=base_candidate)),
        (_request("外心分析乙", source_ref="G2", grade="八年级", topic_hint="一次函数"), ContextRanking(candidates=base_candidate)),
        (_request("外心分析丙", source_ref="G3", grade="八年级", topic_hint="尺规作图与最短路径"), ContextRanking(candidates=base_candidate, ambiguity_flags=("作图对象不明确",))),
    )
    for request, ranking in cases:
        result = SkillResolutionService(
            db_path,
            context_ranker=_StaticRanker(ranking),
        ).resolve(request)
        assert result.outcome is ResolutionOutcome.CONFLICT


def test_archived_context_candidate_cannot_be_resolved(tmp_path: Path) -> None:
    from question_bank.services.skill_context_ranker import ContextRanking
    from question_bank.services.skill_resolution_service import SkillResolutionService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    catalog = SkillCatalogService(db_path)
    candidate = catalog.find_by_stable_key("math.geometry.line_angle.bisector")
    with connect(db_path) as conn:
        conn.execute("UPDATE skills SET status = 'archived' WHERE id = ?", (candidate["id"],))
    ranking = ContextRanking(
        candidates=({"skill_id": candidate["id"], "confidence": 0.99, "reason": "高分"},)
    )

    result = SkillResolutionService(
        db_path,
        context_ranker=_StaticRanker(ranking),
    ).resolve(_request("上下文角关系", topic_hint="线与角"))

    assert result.outcome is ResolutionOutcome.CONFLICT


def test_ranker_outage_becomes_conflict_without_raising(tmp_path: Path) -> None:
    from question_bank.services.skill_resolution_service import SkillResolutionService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)

    result = SkillResolutionService(
        db_path,
        context_ranker=_FailingRanker(),
    ).resolve(_request("需要上下文判断的技能"))

    assert result.outcome is ResolutionOutcome.CONFLICT
    assert "不可用" in result.reason


def test_concrete_local_skill_is_created_once(tmp_path: Path) -> None:
    from question_bank.services.skill_context_ranker import ContextRanking
    from question_bank.services.skill_resolution_service import SkillResolutionService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    ranking = ContextRanking(
        proposed_local_name="旋转手拉手模型",
        proposed_topic_key="math.geometry.transformation",
        proposed_aliases=("手拉手旋转模型",),
        local_confidence=0.95,
    )
    service = SkillResolutionService(db_path, context_ranker=_StaticRanker(ranking))

    first = service.resolve(_request("本校手拉手构造", source_ref="L1", topic_hint="轴对称与图形变换"))
    second = service.resolve(_request("旋转手拉手模型", source_ref="L2", topic_hint="轴对称与图形变换"))

    assert first.outcome is ResolutionOutcome.CREATED_LOCAL
    assert second.outcome is ResolutionOutcome.RESOLVED_EXISTING
    assert second.skill_id == first.skill_id
    with connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM skills WHERE name = '旋转手拉手模型'").fetchone()[0]
    assert count == 1


def test_local_skill_creation_rejects_broad_or_duplicate_name(tmp_path: Path) -> None:
    from question_bank.services.skill_context_ranker import ContextRanking
    from question_bank.services.skill_resolution_service import SkillResolutionService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    rankings = (
        ContextRanking(proposed_local_name="作图", proposed_topic_key="math.geometry.construction", local_confidence=0.99),
        ContextRanking(proposed_local_name="角平分线的性质", proposed_topic_key="math.geometry.line_angle", local_confidence=0.99),
    )
    for index, ranking in enumerate(rankings, start=1):
        result = SkillResolutionService(
            db_path,
            context_ranker=_StaticRanker(ranking),
        ).resolve(_request(f"本地候选{index}", source_ref=f"L{index}"))
        assert result.outcome is ResolutionOutcome.CONFLICT


def test_legacy_canonical_key_resolves_to_its_concrete_skill(tmp_path: Path) -> None:
    from question_bank.services.skill_resolution_service import SkillResolutionService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    target = SkillCatalogService(db_path).find_by_stable_key("math.geometry.congruence.judge")

    result = SkillResolutionService(db_path).resolve(
        _request("KP_GEO_TRIANGLE_CONGRUENCE", source_ref="legacy-key-question")
    )

    assert result.outcome is ResolutionOutcome.RESOLVED_EXISTING
    assert result.skill_id == target["id"]
