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
    annotate_rubric_with_snapshot,
    build_session_snapshot,
    freeze_session_evidence_snapshot,
    validate_rubric_evidence_coverage,
    write_snapshot,
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


def test_snapshot_projection_resolves_direct_keys_by_point_ids(
    tmp_path: Path,
) -> None:
    db_path, release_id = _setup(tmp_path)
    _seed_bank_question(
        db_path,
        release_id,
        points=[_point("p1"), _point("p2")],
        links=[
            ("p1", "direct", _SKILL_KEY, "resolved"),
            ("p2", "direct", _LEAF_KEY, "resolved"),
        ],
    )
    _confirm_link(db_path)
    _freeze(tmp_path, db_path)

    projection = _projection_service(tmp_path, db_path).project_session(
        grading_session_id=_SESSION,
        rubric=_rubric(
            [_step("S1", ["p1"]), _step("S2", ["p2"])], part_id="Q1(P1)"
        ),
    )

    (item,) = projection.items
    assert item.is_graph_eligible
    assert set(item.tags["knowledge_point"]) == {_SKILL_KEY, _LEAF_KEY}
    assert item.assessment["granularity"] == "part"
    assert item.assessment["part_id"] == "Q1(P1)"
    assert item.assessment["evidence_part_id"] == "part-1"
    assert item.assessment["reason"] == "snapshot_evidence_point_attribution"
    assert item.step_targets["S1"] == (_SKILL_KEY,)
    assert item.step_targets["S2"] == (_LEAF_KEY,)
    assert [step["step_id"] for step in item.steps] == ["S1", "S2"]


def test_projection_matches_ids_not_step_wording(tmp_path: Path) -> None:
    """步骤文案与证据点文本不同（模型重写）仍按 id 归因。"""
    db_path, release_id = _setup(tmp_path)
    _seed_bank_question(
        db_path,
        release_id,
        points=[_point("p1", target="由勾股定理列方程")],
        links=[("p1", "direct", _SKILL_KEY, "resolved")],
    )
    _confirm_link(db_path)
    _freeze(tmp_path, db_path)

    projection = _projection_service(tmp_path, db_path).project_session(
        grading_session_id=_SESSION,
        rubric=_rubric(
            [
                _step(
                    "S1",
                    ["p1"],
                    core_goal="重新措辞的完全不同表述",
                    required_elements=["评分者写的文字"],
                )
            ]
        ),
    )

    (item,) = projection.items
    assert item.is_graph_eligible
    assert item.tags["knowledge_point"] == (_SKILL_KEY,)


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


def test_legacy_session_without_snapshot_uses_section_fallback(
    tmp_path: Path,
) -> None:
    """无快照旧会话：整题 knowledge_point 标签上溯到节键。"""
    db_path, _release_id = _setup(tmp_path)
    _seed_bank_question(db_path, _release_id, points=None, links=None)
    _confirm_link(db_path)
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO question_tags(question_id, tag_type, tag_value, source)
            VALUES (1, 'knowledge_point', ?, 'ai')
            """,
            (_LEAF_KEY,),
        )

    projection = _projection_service(tmp_path, db_path).project_session(
        grading_session_id=_SESSION,
        rubric=_rubric([_step("S1", [])]),
    )

    (item,) = projection.items
    assert item.is_graph_eligible
    assert item.tags["knowledge_point"] == (_SECTION_KEY,)
    assert item.assessment["granularity"] == "whole_question"
    assert item.assessment["reason"] == "legacy_section_fallback"


def test_missing_step_point_ids_report_missing_reason(tmp_path: Path) -> None:
    db_path, release_id = _setup(tmp_path)
    _seed_bank_question(
        db_path,
        release_id,
        points=[_point("p1")],
        links=[("p1", "direct", _SKILL_KEY, "resolved")],
    )
    _confirm_link(db_path)
    _freeze(tmp_path, db_path)

    projection = _projection_service(tmp_path, db_path).project_session(
        grading_session_id=_SESSION,
        rubric=_rubric([_step("S1", [])]),
    )

    (item,) = projection.items
    assert item.missing_reason == "part_evidence_point_ids_missing"
    assert not item.is_graph_eligible


def test_uncovered_points_removed_from_coverage(tmp_path: Path) -> None:
    db_path, release_id = _setup(tmp_path)
    _seed_bank_question(
        db_path,
        release_id,
        points=[_point("p1"), _point("p2")],
        links=[
            ("p1", "direct", _SKILL_KEY, "resolved"),
            ("p2", "direct", _LEAF_KEY, "resolved"),
        ],
    )
    _confirm_link(db_path)
    _freeze(tmp_path, db_path)

    rubric = _rubric([_step("S1", ["p1", "p2"])])
    rubric["questions"][0]["parts"][0][
        "allow_alternative_methods"
    ] = True
    rubric["questions"][0]["parts"][0][
        "uncovered_evidence_point_ids"
    ] = ["p2"]

    projection = _projection_service(tmp_path, db_path).project_session(
        grading_session_id=_SESSION,
        rubric=rubric,
    )

    (item,) = projection.items
    assert item.tags["knowledge_point"] == (_SKILL_KEY,)


def test_annotate_rubric_stamps_step_ids_and_question_refs(
    tmp_path: Path,
) -> None:
    """冻结时按 match_rubric_parts 精确对位盖章；措辞不同的步骤不猜。"""
    db_path, release_id = _setup(tmp_path)
    _seed_bank_question(
        db_path,
        release_id,
        points=[
            _point(
                "p1",
                target="证明全等",
                justification="判定依据",
                answer_anchor="结论",
                observable_evidence="全等判定过程",
            )
        ],
        links=[("p1", "direct", _SKILL_KEY, "resolved")],
    )
    _confirm_link(db_path)

    rubric_path = tmp_path / "config" / "uploaded" / "rubric_job-1.json"
    rubric_path.parent.mkdir(parents=True, exist_ok=True)
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "parts": [
                            {
                                "part_id": "part-1",
                                "response_mode": "process",
                                "allow_alternative_methods": False,
                                "proof_obligations": [],
                                "steps": [
                                    {
                                        "step_id": "S1",
                                        "core_goal": "证明全等",
                                        "required_elements": [
                                            "判定依据",
                                            "结论",
                                            "全等判定过程",
                                        ],
                                        "equivalent_rules": [],
                                        "counterexamples": [],
                                        "step_score": 3,
                                    }
                                ],
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    _freeze(
        tmp_path,
        db_path,
    )
    snapshot = build_session_snapshot(
        db_path, source_to_bank={"Q1": 1}
    )
    assert annotate_rubric_with_snapshot(rubric_path, snapshot, _SESSION)

    saved = json.loads(rubric_path.read_text(encoding="utf-8"))
    question = saved["questions"][0]
    assert question["evidence_snapshot_ref"] == f"evidence_snapshot-{_SESSION}.json"
    assert question["source_evidence_version_id"] == _VERSION
    assert question["graph_release_id"] == release_id
    step = question["parts"][0]["steps"][0]
    assert step["evidence_point_ids"] == ["p1"]


def test_freeze_with_answer_key_stamps_objective_generic_goal_steps(
    tmp_path: Path,
) -> None:
    """自动发布路径没有编辑器答案键；冻结时必须转发 answer_key，
    客观题兼容分支才能给通用目标步骤盖章。"""
    db_path, release_id = _setup(tmp_path)
    _seed_bank_question(
        db_path,
        release_id,
        points=[_point("p1", target="作答为C")],
        links=[("p1", "direct", _SKILL_KEY, "resolved")],
        part_overrides={
            "response_mode": "exact_objective",
            "canonical_answer": "C",
        },
    )
    _confirm_link(db_path)

    rubric_path = tmp_path / "config" / "uploaded" / "rubric_job-1.json"
    rubric_path.parent.mkdir(parents=True, exist_ok=True)
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "Q1",
                        "question_type": "choice",
                        "source_evidence_version_id": _VERSION,
                        "parts": [
                            {
                                "part_id": "Q1",
                                "response_mode": "exact_objective",
                                "steps": [
                                    {
                                        "step_id": "S1",
                                        "core_goal": "选择正确的选项",
                                        "required_elements": ["C"],
                                        "step_score": 3,
                                    }
                                ],
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    answer_key = {
        "questions": [{"question_id": "Q1", "canonical_answer": "C"}]
    }

    # 不传 answer_key 时兼容分支无法核对冻结答案，步骤保持未盖章——
    # 这正是调用方必须转发 answer_key 的原因。
    freeze_session_evidence_snapshot(
        db_path,
        grading_session_id=_SESSION,
        upload_config_dir=tmp_path / "config" / "uploaded",
        data_root=tmp_path,
        rubric_path=rubric_path,
    )
    saved = json.loads(rubric_path.read_text(encoding="utf-8"))
    step = saved["questions"][0]["parts"][0]["steps"][0]
    assert not step.get("evidence_point_ids")

    # 已有快照分支同样转发 answer_key，重跑后完成盖章。
    freeze_session_evidence_snapshot(
        db_path,
        grading_session_id=_SESSION,
        upload_config_dir=tmp_path / "config" / "uploaded",
        data_root=tmp_path,
        rubric_path=rubric_path,
        answer_key=answer_key,
    )
    saved = json.loads(rubric_path.read_text(encoding="utf-8"))
    step = saved["questions"][0]["parts"][0]["steps"][0]
    assert step["evidence_point_ids"] == ["p1"]


def _coverage_rubric(
    *,
    steps: list[dict],
    allow_alternative: bool = False,
    uncovered: list[str] | None = None,
) -> dict:
    part = {
        "part_id": "part-1",
        "allow_alternative_methods": allow_alternative,
        "steps": steps,
    }
    if uncovered is not None:
        part["uncovered_evidence_point_ids"] = uncovered
    return {"questions": [{"question_id": "Q1", "parts": [part]}]}


def _coverage_snapshot(point_ids: list[str]) -> dict:
    return {
        "questions": {
            "Q1": {
                "usable": True,
                "source_evidence_version_id": _VERSION,
                "graph_release_id": "rel",
                "evidence": {
                    "parts": [
                        {
                            "part_id": "part-1",
                            "evidence_points": [
                                {"evidence_point_id": pid}
                                for pid in point_ids
                            ],
                        }
                    ]
                },
            }
        }
    }


def test_coverage_validation_requires_each_point_once(tmp_path: Path) -> None:
    snapshot = _coverage_snapshot(["p1", "p2"])

    clean = _coverage_rubric(
        steps=[_step("S1", ["p1"]), _step("S2", ["p2"])]
    )
    assert validate_rubric_evidence_coverage(clean, snapshot) == []

    missing = _coverage_rubric(steps=[_step("S1", ["p1"])])
    violations = validate_rubric_evidence_coverage(missing, snapshot)
    assert violations and "p2" in violations[0]

    duplicated = _coverage_rubric(
        steps=[_step("S1", ["p1"]), _step("S2", ["p1", "p2"])]
    )
    violations = validate_rubric_evidence_coverage(duplicated, snapshot)
    assert any("more than one step" in message for message in violations)


def test_coverage_validation_alternative_methods_need_uncovered_list(
    tmp_path: Path,
) -> None:
    snapshot = _coverage_snapshot(["p1", "p2"])

    ok = _coverage_rubric(
        steps=[_step("S1", ["p1"])],
        allow_alternative=True,
        uncovered=["p2"],
    )
    assert validate_rubric_evidence_coverage(ok, snapshot) == []

    bad = _coverage_rubric(
        steps=[_step("S1", ["p1"])],
        allow_alternative=True,
    )
    violations = validate_rubric_evidence_coverage(bad, snapshot)
    assert violations and "uncovered" in violations[0]


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


def test_step_target_contributions_map_achievement() -> None:
    projected = _projected_with_steps()
    contributions = _step_target_contributions(
        projected,
        [
            {"step_id": "S1", "part_id": "part-1", "achievement": "full", "score_awarded": 4},
            {"step_id": "S2", "part_id": "part-1", "achievement": "none", "score_awarded": 0},
        ],
    )
    assert contributions[_SKILL_KEY] == (4.0, 6.0)
    assert contributions[_LEAF_KEY] == (0.0, 2.0)


def test_step_target_contributions_partial_rules() -> None:
    projected = _projected_with_steps()
    # 单点 partial → 0.5；多点 partial → awarded/step_score 比例。
    single = _step_target_contributions(
        projected,
        [{"step_id": "S1", "achievement": "partial", "score_awarded": 2}],
    )
    assert single[_SKILL_KEY] == (2.0, 4.0)

    multi = _step_target_contributions(
        projected,
        [{"step_id": "S2", "achievement": "partial", "score_awarded": 1}],
    )
    assert multi[_LEAF_KEY] == (1.0, 2.0)
    assert multi[_SKILL_KEY] == (1.0, 2.0)


def test_step_target_contributions_empty_without_assessments() -> None:
    projected = _projected_with_steps()
    assert _step_target_contributions(projected, None) == {}
    assert _step_target_contributions(projected, []) == {}


def test_exam_dependencies_and_point_weights_are_frozen_facts():
    from dataclasses import replace
    from integration.diagnosis_profile_service import _step_point_observations
    projected = replace(_projected_with_steps(), evidence_points={
        'p1': {'depends_on': []}, 'p2': {'depends_on': ['p1']}, 'p3': {'depends_on': []},
    }, point_links={
        'p1': ({'role':'direct','resolution_status':'resolved','stable_key':_SKILL_KEY,'weight':1.0},),
        'p2': ({'role':'direct','resolution_status':'resolved','stable_key':_OTHER_SKILL_KEY,'weight':1.0},),
        'p3': ({'role':'direct','resolution_status':'resolved','stable_key':_LEAF_KEY,'weight':0.25},
               {'role':'direct','resolution_status':'resolved','stable_key':_SKILL_KEY,'weight':0.75}),
    })
    records = [{'step_id':'S1','achievement':'none'}, {'step_id':'S2','achievement':'none'}]
    observations = _step_point_observations(projected, records)
    assert {o['point_id'] for o in observations} == {'p1', 'p3'}
    assert sum(o['weight'] for o in observations) == 1.0
    leaf = next(o for o in observations if o['stable_key'] == _LEAF_KEY)
    assert leaf['weight'] == 0.125
    assert _OTHER_SKILL_KEY not in _step_target_contributions(projected, records)
    # Missing prerequisites also cannot establish dependent failure.
    assert {o['point_id'] for o in _step_point_observations(projected, records[1:])} == {'p3'}


def test_coverage_uses_point_identity_when_rubric_part_was_renamed():
    snapshot = {'questions': {'Q1': {'usable': True, 'evidence': {'parts': [
        {'part_id':'part-1', 'evidence_points':[{'evidence_point_id':'p1'}]},
    ]}}}}
    rubric = {'questions':[{'question_id':'Q1','parts':[
        {'part_id':'Q1(P1)','steps':[{'step_id':'S1','evidence_point_ids':['p1']}]},
    ]}]}
    assert validate_rubric_evidence_coverage(rubric, snapshot) == []
    rubric['questions'][0]['parts'][0]['steps'][0]['evidence_point_ids'] = ['unknown']
    assert validate_rubric_evidence_coverage(rubric, snapshot)


def test_teacher_final_total_does_not_restore_superseded_ai_step_scores():
    from types import SimpleNamespace
    from integration.diagnosis_profile_service import DiagnosisProfileService
    service = object.__new__(DiagnosisProfileService)
    projected = _projected_with_steps()
    service.db = SimpleNamespace(get_active_assessment_evidence=lambda **kwargs: [{
        'session_id': 1, 'student_id': 1, 'question_id': projected.item_ref,
        'teacher_final_revision': 1, 'score_awarded': 6, 'teacher_final_max_score': 6,
        'assessment_state': {'step_assessments': [{'step_id':'S1','achievement':'none','score_awarded':0}]},
    }])
    row, = service._projected_tag_evidence(student_ids=['1'], session_ids=[1],
        projection_by_session={1: SimpleNamespace(items=[projected])})
    assert row['score_awarded'] == row['full_score'] == 6
    assert 'target_contributions' not in row and 'point_observations' not in row
    assert row['assessment']['granularity'] == 'whole_question'


def test_teacher_partial_step_is_not_achieved_and_stale_revision_is_not_used():
    from types import SimpleNamespace
    from integration.diagnosis_profile_service import DiagnosisProfileService
    projected = _projected_with_steps()
    steps = [{**step, 'max_score': step['step_score'], 'score_awarded': awarded,
              'achievement': 'partial'} for step, awarded in zip(projected.steps, (3, 2))]
    review = {'revision': 2, 'scan_batch_id': 'test-batch', 'score_awarded': 5, 'steps': steps}
    source = {'session_id': 1, 'student_id': 1, 'question_id': projected.item_ref,
              'teacher_final_revision': 2, 'teacher_final_scan_batch_id': 'test-batch',
              'score_awarded': 5, 'teacher_final_max_score': 6,
              'assessment_state': {'teacher_review': review}}
    service = object.__new__(DiagnosisProfileService)
    service.db = SimpleNamespace(get_active_assessment_evidence=lambda **_: [source])
    def projected_row():
        return service._projected_tag_evidence(student_ids=['1'], session_ids=[1],
            projection_by_session={1: SimpleNamespace(items=[projected])})[0]
    row = projected_row()
    assert row['assessment']['granularity'] == 'step'
    assert row['target_contributions'][_SKILL_KEY] == (2, 6)
    assert row['target_contributions'][_LEAF_KEY] == (2, 2)
    review['revision'] = 1
    row = projected_row()
    assert row['assessment']['granularity'] == 'whole_question'
    assert 'target_contributions' not in row


def test_mastery_uses_equal_point_mass_instead_of_exam_score_allocation():
    from datetime import datetime, timezone
    from question_bank.mastery.current import _exam_evidence
    reference = {'session_id':1, 'score_awarded':9, 'full_score':10,
                 'assessment':{'granularity':'part','point_observations':[
                     {'weight':.5,'achieved':1}, {'weight':.5,'achieved':0},
                 ]}}
    evidence = _exam_evidence(reference, student_id='1', stable_key=_SKILL_KEY,
                              session_times={1:datetime(2026,9,17,tzinfo=timezone.utc)})
    assert evidence.score_awarded == .5 and evidence.full_score == 1
    assert evidence.evidence_weight == 1
