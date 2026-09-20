"""strict/primary/any scope filtering + link-table reads (redesign §8)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.services.question_read_service import (
    QuestionBankReadService,
    QuestionReadFilters,
)
from question_bank.solution_evidence.knowledge_links import (
    rebuild_question_scope_summaries,
    refresh_question_scope_summary,
    replace_point_links,
)
from question_bank.recommendation.personalized import _question_evidence_metadata
from question_bank.solution_evidence.knowledge_links import KnowledgeLink
from tests.current_knowledge_support import install_current_knowledge

# Section ``bnu24-math-g7-lower-c02-s01`` lives in volume order 2; the g7-upper
# leaf is an earlier volume (order 1).
_SECTION = "kp_bnu24_math_g7_lower_2_1"
_SECTION_ID = "bnu24-math-g7-lower-c02-s01"
_LEAF_IN = "kp_bnu24_math_g7_lower_2_1_1"
_LEAF_SAME_VOL = "kp_bnu24_math_g7_lower_2_2_1"  # sibling section, same volume
_LEAF_EARLIER = "kp_bnu24_math_g7_upper_1_1_1"   # earlier volume


def test_skill_progress_and_strict_scope_use_published_parent_relations():
    from types import SimpleNamespace
    from question_bank.recommendation.personalized import (
        PersonalizedRecommendationConfig, _allowed_keys_for_config, _question_scope_allowed,
    )
    from dataclasses import replace
    skill = 'sk_bnu24_math_g8_upper_1_1_01'
    later = 'sk_bnu24_math_g8_upper_2_1_01'
    relations = [SimpleNamespace(relation_type='parent', source_key=key, target_key=parent)
                 for key, parent in [(skill, 'kp_bnu24_math_g8_upper_1_1'),
                                     (later, 'kp_bnu24_math_g8_upper_2_1'),
                                     ('kp_bnu24_math_g8_upper_1_1', 'kp_bnu24_math_g8_upper_1')]]
    # The production resolver is hashable; use the same immutable identity
    # semantics for the cached progress calculation.
    class Resolver:
        pass
    resolver = Resolver()
    resolver.relations = relations
    config = PersonalizedRecommendationConfig(curriculum_volume_id='bnu24-math-g8-upper', target_keys=(skill,))
    allowed = _allowed_keys_for_config(config, resolver)
    assert skill in allowed and later not in allowed
    candidate = {'stable_keys': [skill], 'required_keys': [skill], 'scope_complete': True}
    assert _question_scope_allowed(candidate, config, allowed, resolver)
    # Learning chapter two does not permit a mixed chapter-one/chapter-two
    # exercise when the teacher explicitly requests chapter one only.
    strict = replace(config, scope_keys=('kp_bnu24_math_g8_upper_1',),
                     teaching_progress_chapter_id='bnu24-math-g8-upper-c02')
    mixed = {**candidate, 'stable_keys': [skill, later], 'required_keys': [skill, later]}
    assert not _question_scope_allowed(mixed, strict, _allowed_keys_for_config(strict, resolver), resolver)


def test_scope_summary_uses_active_links_instead_of_evidence_creation_release(bank):
    db_path = bank['db_path']
    _link(bank, 1, [{'term_id': _LEAF_IN, 'role': 'direct'}])
    with connect(db_path) as conn:
        conn.execute("INSERT INTO knowledge_graph_releases(release_id,schema_version,taxonomy_revision,content_hash,payload_json,status,source_reference,created_by) SELECT 'kgr_test_old_release',schema_version,taxonomy_revision,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',payload_json,'retired',source_reference,created_by FROM knowledge_graph_releases WHERE status='active'")
        conn.execute("UPDATE question_solution_evidence_versions SET graph_release_id='kgr_test_old_release' WHERE question_id=1")
        conn.execute("INSERT INTO evidence_point_knowledge_links SELECT evidence_version_id,question_id,part_id,evidence_point_id,'kgr_test_old_release',role,term_id,?,resolution_status,weight,source_kind,source_reference,'2099-01-01' FROM evidence_point_knowledge_links WHERE question_id=1", (_LEAF_EARLIER,))
        refresh_question_scope_summary(conn, 1, db_path=db_path)
        row = conn.execute('SELECT * FROM question_scope_summary WHERE question_id=1').fetchone()
        assert row['primary_section_id'] == _SECTION
        assert row['graph_release_id'] != 'kgr_test_old_release'


def _evidence(question_id: int) -> dict:
    return {
        "parts": [
            {
                "part_id": "part-1",
                "evidence_points": [
                    {
                        "evidence_point_id": f"q{question_id}-p1",
                        "target": "目标",
                        "observable_evidence": "证据",
                        "fine_term_links": [],
                    }
                ],
            }
        ]
    }


@pytest.fixture()
def bank(tmp_path: Path) -> dict:
    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    release_id = install_current_knowledge(db_path, taxonomy_revision=4)
    with connect(db_path) as connection:
        connection.execute(
            "INSERT INTO papers(id,title,import_status) VALUES(1,'合成','ready')"
        )
        for qid in range(1, 6):
            connection.execute(
                "INSERT INTO questions(id,paper_id,question_number,question_text)"
                " VALUES(?,1,?,'合成题')",
                (qid, f"{qid}"),
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
                    f"v{qid}" + "0" * 62,
                    qid,
                    "b" * 64,
                    "c" * 64,
                    json.dumps(_evidence(qid), ensure_ascii=False),
                    release_id,
                ),
            )
    return {"db_path": db_path, "release_id": release_id}


def _link(
    bank: dict,
    qid: int,
    links: list[dict],
) -> None:
    with connect(bank["db_path"]) as connection:
        replace_point_links(
            connection,
            evidence_version_id=f"v{qid}" + "0" * 62,
            question_id=qid,
            graph_release_id=bank["release_id"],
            points=[
                {
                    "part_id": "part-1",
                    "evidence_point_id": f"q{qid}-p1",
                    "links": links,
                }
            ],
        )


def _tag(connection, qid: int, tag_type: str, value: str) -> None:
    connection.execute(
        "INSERT INTO question_tags(question_id, tag_type, tag_value, source)"
        " VALUES(?, ?, ?, 'derived')",
        (qid, tag_type, value),
    )


def _ids(service: QuestionBankReadService, mode: str) -> set[int]:
    page = service.list_questions(
        QuestionReadFilters(
            curriculum_sections=(_SECTION_ID,),
            scope_mode=mode,
            page_size=100,
        )
    )
    return {int(item["id"]) for item in page.items}


def test_scope_modes_form_ordered_subsets(bank):
    db_path = bank["db_path"]
    # Q1: direct in-scope, supporting from an earlier volume -> all modes.
    _link(bank, 1, [
        {"term_id": _LEAF_IN, "role": "direct"},
        {"term_id": _LEAF_EARLIER, "role": "supporting_prerequisite"},
    ])
    # Q2: primary stays in scope but a second direct link crosses chapters.
    _link(bank, 2, [
        {"term_id": _LEAF_IN, "role": "direct", "weight": 0.9},
        {"term_id": _LEAF_EARLIER, "role": "direct", "weight": 0.1},
    ])
    # Q3: supporting prerequisite from the same volume -> not "earlier volumes".
    _link(bank, 3, [
        {"term_id": _LEAF_IN, "role": "direct"},
        {"term_id": _LEAF_SAME_VOL, "role": "supporting_prerequisite"},
    ])
    # Q4: direct link only to the sibling section -> outside every mode.
    _link(bank, 4, [{"term_id": _LEAF_SAME_VOL, "role": "direct"}])
    with connect(db_path) as connection:
        for qid in (1, 2, 3, 5):
            _tag(connection, qid, "curriculum_section", _SECTION_ID)
        _tag(connection, 4, "curriculum_section", "bnu24-math-g7-lower-c02-s02")

    service = QuestionBankReadService(db_path)
    strict = _ids(service, "strict")
    primary = _ids(service, "primary")
    any_mode = _ids(service, "any")
    assert strict == {1, 5}
    assert primary == {1, 2, 3, 5}
    assert any_mode == {1, 2, 3, 5}
    assert strict <= primary <= any_mode


def test_scope_summary_row_and_idempotent_refresh(bank):
    db_path = bank["db_path"]
    _link(bank, 1, [
        {"term_id": _LEAF_IN, "role": "direct", "weight": 0.9},
        {"term_id": _LEAF_EARLIER, "role": "direct", "weight": 0.1},
        {"term_id": "kp_bnu24_math_g7_upper_1_1_2", "role": "supporting_prerequisite"},
    ])
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM question_scope_summary WHERE question_id = 1"
        ).fetchone()
        assert row["primary_section_id"] == _SECTION
        sections = set(json.loads(row["direct_section_ids_json"]))
        assert sections == {_SECTION, "kp_bnu24_math_g7_upper_1_1"}
        assert row["has_cross_chapter_direct"] == 1
        assert row["supporting_max_volume_order"] == 1
        refresh_question_scope_summary(connection, 1, db_path=db_path)
        again = connection.execute(
            "SELECT * FROM question_scope_summary WHERE question_id = 1"
        ).fetchone()
        assert again["direct_section_ids_json"] == row["direct_section_ids_json"]
        assert again["primary_section_id"] == row["primary_section_id"]


def test_scope_summary_removed_without_usable_version(bank):
    db_path = bank["db_path"]
    _link(bank, 1, [{"term_id": _LEAF_IN, "role": "direct"}])
    with connect(db_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM question_scope_summary WHERE question_id = 1"
        ).fetchone()
        connection.execute(
            "UPDATE question_solution_evidence_versions SET status='rejected'"
            " WHERE question_id = 1"
        )
        refresh_question_scope_summary(connection, 1, db_path=db_path)
        assert connection.execute(
            "SELECT 1 FROM question_scope_summary WHERE question_id = 1"
        ).fetchone() is None


def test_rebuild_covers_all_questions(bank):
    db_path = bank["db_path"]
    _link(bank, 1, [{"term_id": _LEAF_IN, "role": "direct"}])
    with connect(db_path) as connection:
        connection.execute("DELETE FROM question_scope_summary")
    assert rebuild_question_scope_summaries(db_path) == 5
    with connect(db_path) as connection:
        # Only the linked question keeps a row; unlinked questions fall back
        # to tag matching and intentionally have no summary row.
        assert connection.execute(
            "SELECT COUNT(*) FROM question_scope_summary"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT primary_section_id FROM question_scope_summary"
            " WHERE question_id = 1"
        ).fetchone()[0] == _SECTION


def test_metadata_reads_link_table_over_embedded(bank):
    """Table links are authoritative; embedded text stays factual input."""
    db_path = bank["db_path"]
    evidence = {
        "parts": [
            {
                "part_id": "part-1",
                "response_mode": "process_required",
                "evidence_points": [
                    {
                        "evidence_point_id": "p1",
                        "target": "目标",
                        "observable_evidence": "证据",
                        "fine_term_links": [
                            {
                                "fine_term_id": "kp_stale",
                                "role": "direct",
                                "core_resolution": {
                                    "status": "resolved",
                                    "stable_keys": ["kp_stale"],
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    with connect(db_path) as connection:
        resolver = CurrentKnowledgeResolver.from_connection(connection)
    table_links = {
        "p1": (
            KnowledgeLink(
                term_id=_LEAF_IN,
                stable_key=_LEAF_IN,
                role="direct",
                weight=1.0,
                resolution_status="resolved",
                source_kind="link_job",
            ),
        )
    }
    metadata = _question_evidence_metadata(evidence, resolver, links=table_links)
    assert metadata["stable_keys"] == [_LEAF_IN]
    # Without table links the embedded compatibility input is used instead;
    # its unresolved-in-catalog key simply yields nothing.
    embedded = _question_evidence_metadata(evidence, resolver)
    assert metadata["stable_keys"] != embedded["stable_keys"] or embedded[
        "stable_keys"
    ] == []


def test_difficulty_report_math_and_readonly(bank, monkeypatch, tmp_path):
    import tools.difficulty_calibration_report as report_tool

    monkeypatch.setattr(
        report_tool,
        "collect_rows",
        lambda *a, **k: {
            1: {"rates": [0.6] * 40, "classes": {"甲班", "乙班"}, "sessions": {2}},
            2: {"rates": [0.05] * 40, "classes": {"甲班"}, "sessions": {2}},
        },
    )
    with connect(bank["db_path"]) as connection:
        connection.execute("UPDATE questions SET difficulty=5 WHERE id=1")
        connection.execute("UPDATE questions SET difficulty=9 WHERE id=2")
    report, warnings = report_tool.build_report(
        tmp_path / "unused-grading.db",
        bank["db_path"],
        tmp_path,
        min_observations=3,
    )
    assert warnings == []
    by_id = {row["question_id"]: row for row in report}
    assert by_id[1]["class_count"] == 2
    # difficulty 5 expects 0.6 rate: observing exactly 0.6 keeps z at 0.
    assert by_id[1]["z"] == 0 and not by_id[1]["review_candidate"]
    # difficulty 9 expects 0.2 rate: observing 0.05 over 40 rows is |z|>1.5.
    assert by_id[2]["z"] < -1.5 and by_id[2]["review_candidate"]


def test_validity_report_is_readonly(bank, tmp_path):
    import tools.recommendation_validity_report as validity_tool

    report = validity_tool.build_report(bank["db_path"], days=30)
    assert report["days"] == 30
    assert report["paper_count"] == 0
    assert report["details"] == []


def test_validity_compares_same_metric_and_includes_later_papers(tmp_path):
    import sqlite3
    from tools.recommendation_validity_report import build_report
    database = tmp_path / 'synthetic_validity.db'
    conn = sqlite3.connect(database)
    conn.executescript('''
        CREATE TABLE personalized_paper_instances(paper_instance_id,draft_id,student_id,created_at,status);
        CREATE TABLE personalized_recommendation_drafts(draft_id,draft_json);
        CREATE TABLE training_submissions(submission_id,paper_instance_id);
        CREATE TABLE training_evidence_records(submission_id,student_id,stable_key,achieved_points,total_points,evidence_weight,occurred_at,status);
    ''')
    conn.execute("INSERT INTO personalized_paper_instances VALUES('p','d','1','2026-09-10 00:00:00','frozen')")
    conn.execute('INSERT INTO personalized_recommendation_drafts VALUES(?,?)', ('d',json.dumps({'students':[
        {'student_id':'1','targets':[{'stable_key':'sk_a','value':.65},{'stable_key':'sk_b','value':.65}]}]})))
    conn.executemany('INSERT INTO training_submissions VALUES(?,?)', [('s0','old'),('s1','p'),('s2','later')])
    conn.executemany('INSERT INTO training_evidence_records VALUES(?,?,?,?,?,?,?,?)', [
        ('s0','1','sk_a',1,4,1,'2026-09-08 00:00:00','active'),
        ('s1','1','sk_a',1,1,.25,'2026-09-11 00:00:00','active'),
        ('s2','1','sk_a',0,1,.75,'2026-09-12 00:00:00','active'),
        ('s2','1','sk_a',1,1,10,'2026-09-12 00:00:00','superseded'),
        ('s2','1','sk_b',1,1,1,'2026-09-12 00:00:00','active'),
    ])
    conn.commit()
    conn.close()
    report = build_report(database, days=5)
    a,b = report['details']
    assert a['pre_score_rate'] == a['post_score_rate'] == .25
    assert a['delta'] == 0 and a['transfer_score_rate'] == 0
    assert a['post_evidence_count'] == 2
    assert b['delta'] is None and b['post_score_rate'] == 1
