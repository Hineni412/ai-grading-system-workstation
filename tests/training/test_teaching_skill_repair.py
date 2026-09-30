"""Targeted regression checks for the teaching-skill repair; synthetic only."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.build_release_v3 import (
    build_teaching_release,
    apply_teaching_repair,
    CATALOG_DIR,
)
from question_bank.database.schema import initialize_database, connect
from tests.current_knowledge_support import install_current_knowledge


@pytest.fixture(scope="module")
def standard():
    return json.loads(
        (CATALOG_DIR / "teaching_skills_g8_core.json").read_text(encoding="utf-8")
    )


def _synthetic_bank(tmp_path):
    database = tmp_path / "question_bank.db"
    initialize_database(database)
    release = install_current_knowledge(database, taxonomy_revision=5)
    evidence = {
        "parts": [
            {
                "part_id": "part-1",
                "evidence_points": [
                    {"evidence_point_id": "p1", "target": "计算另一条直角边长"},
                    {"evidence_point_id": "p2", "target": "求结果"},
                ],
            }
        ]
    }
    from question_bank.solution_evidence.knowledge_links import replace_point_links

    with connect(database) as conn:
        conn.execute(
            "INSERT INTO papers(id,title,import_status) VALUES(1,'合成修复验收','ready')"
        )
        conn.execute(
            "INSERT INTO questions(id,paper_id,question_number,question_text) VALUES(1,1,'1','合成题')"
        )
        conn.execute(
            """INSERT INTO question_solution_evidence_versions(
            evidence_version_id,question_id,source_content_hash,schema_version,content_hash,evidence_json,status,
            source_kind,source_reference,created_by,graph_release_id) VALUES(?,1,?,'question-solution-evidence-v2',?,?,'approved','backfill','synthetic','test',?)""",
            ("a" * 64, "b" * 64, "c" * 64, json.dumps(evidence), release),
        )
        replace_point_links(
            conn,
            evidence_version_id="a" * 64,
            question_id=1,
            graph_release_id=release,
            points=[
                {
                    "part_id": "part-1",
                    "evidence_point_id": pid,
                    "links": [
                        {"term_id": "sk_bnu24_math_g8_upper_1_1_01", "role": "direct"}
                    ],
                }
                for pid in ("p1", "p2")
            ],
        )
    return database, release


def test_failed_repair_rolls_back_links_and_active_release(
    tmp_path, standard, monkeypatch
):
    database, old_release = _synthetic_bank(tmp_path)
    import question_bank.solution_evidence.knowledge_links as links

    real_replace = links.replace_point_links

    def fail_after_write(*args, **kwargs):
        real_replace(*args, **kwargs)
        raise ValueError("synthetic failure")

    monkeypatch.setattr(links, "replace_point_links", fail_after_write)
    with pytest.raises(ValueError, match="synthetic failure"):
        apply_teaching_repair(database, standard, *build_teaching_release(standard))
    with connect(database) as conn:
        assert (
            conn.execute(
                "SELECT release_id FROM knowledge_graph_releases WHERE status='active'"
            ).fetchone()[0]
            == old_release
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM evidence_point_knowledge_links"
            ).fetchone()[0]
            == 2
        )
