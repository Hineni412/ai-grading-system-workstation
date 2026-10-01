from __future__ import annotations

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from question_bank.database.schema import initialize_database
from question_bank.services import question_read_service as read_module
from question_bank.services.question_read_service import (
    QuestionBankReadService,
    QuestionReadFilters,
)


@pytest.fixture(autouse=True)
def _clear_read_result_cache():
    read_module._READ_RESULT_CACHE.clear()
    yield
    read_module._READ_RESULT_CACHE.clear()


def _seed_paper(db_path: Path, paper_id: int = 1, title: str = "Paper") -> None:
    initialize_database(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (?, ?, 'success')",
            (paper_id, title),
        )
        connection.commit()
        # Fold the WAL into the main file up front so a later read-only
        # connection close cannot checkpoint and shift the generation token
        # mid-test.
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()


def _seed_skill_bank(tmp_path: Path, count: int = 6):
    from question_bank.database.schema import connect
    from question_bank.training_criteria import QuestionAnalysisInputLoader, solution_evidence_source_content_hash

    db = tmp_path / "TEST-skill-index.db"
    _seed_paper(db)
    from tests.current_knowledge_support import install_current_knowledge
    install_current_knowledge(db, taxonomy_revision=7)
    with connect(db) as conn:
        # Use catalog metadata exactly as the production volume filter does.
        from question_bank.taxonomy.curriculum_catalog import curriculum_volume
        volume = curriculum_volume(volume_id="bnu24-math-g8-upper")
        conn.execute("UPDATE papers SET grade=?,semester=?,textbook_version=? WHERE id=1",
                     (volume["grade"], volume["semester"], volume["textbook_version"]))
        release = conn.execute("SELECT release_id FROM knowledge_graph_releases WHERE status='active'").fetchone()[0]
        keys = [row[0] for row in conn.execute(
            "SELECT stable_key FROM knowledge_graph_node_profiles WHERE release_id=? "
            "AND status='active' AND stable_key LIKE 'sk_bnu24_math_g8_upper_1_1_%' ORDER BY stable_key LIMIT 3", (release,))]
        assert len(keys) == 3
        conn.executemany("INSERT INTO questions(id,paper_id,question_number,question_text,question_type,difficulty) VALUES(?,1,?,?,'解答题',?)",
                         [(qid, str(qid), f'TEST-合成技能题 {qid}', str(qid % 10 + 1)) for qid in range(1, count + 1)])
    inputs = QuestionAnalysisInputLoader(db_path=db, data_root=tmp_path).load(list(range(1, count + 1)))
    with connect(db) as conn:
        conn.execute("INSERT INTO knowledge_graph_releases(release_id,schema_version,taxonomy_revision,content_hash,payload_json,status,source_reference,created_by) "
                     "VALUES('kgr_TEST_old','knowledge-graph-release-v1',1,?,'{}','retired','TEST','TEST')", ('f' * 64,))
        for question in inputs:
            qid = question.question_id
            if qid == 3:
                continue  # No usable evidence.
            version = f'{qid:064x}'
            payload = {"parts": [{"part_id": "part-1", "evidence_points": [
                {"evidence_point_id": "p1", "target": "列式"}, {"evidence_point_id": "p2", "target": "求解"}]}]}
            conn.execute("INSERT INTO question_solution_evidence_versions(evidence_version_id,question_id,source_content_hash,schema_version,content_hash,evidence_json,status,source_kind,source_reference,created_by,graph_release_id) "
                         "VALUES(?,?,?,'question-solution-evidence-v2',?,?,'approved','combined_model','TEST','TEST',?)",
                         (version, qid, solution_evidence_source_content_hash(question), version, json.dumps(payload), release))
            if qid == 4:
                continue  # Usable evidence without a skill.
            for point, key in [('p1', keys[0]), ('p2', keys[1] if qid == 1 else keys[0])]:
                conn.execute("INSERT INTO evidence_point_knowledge_links(evidence_version_id,question_id,part_id,evidence_point_id,graph_release_id,role,term_id,stable_key,resolution_status,source_kind) "
                             "VALUES(?,?,'part-1',?,?,'direct',?,?,'resolved','link_job')",
                             (version, qid, point, 'kgr_TEST_old' if qid == 2 else release, key, key))
        if count == 6:
            conn.execute("UPDATE questions SET is_deleted=1 WHERE id=6")
            # Source-changed evidence cannot supply a skill.
            conn.execute("UPDATE questions SET question_text='TEST-题面已经改变' WHERE id=5")
            # An older superseded version must never add a third skill.
            conn.execute("INSERT INTO question_solution_evidence_versions(evidence_version_id,question_id,source_content_hash,schema_version,content_hash,evidence_json,status,source_kind,source_reference,created_by,graph_release_id) "
                         "SELECT ?,question_id,source_content_hash,schema_version,?,evidence_json,'superseded',source_kind,'TEST-old',created_by,graph_release_id "
                         "FROM question_solution_evidence_versions WHERE question_id=1", ('e' * 64, 'e' * 64))
            conn.execute("INSERT INTO evidence_point_knowledge_links(evidence_version_id,question_id,part_id,evidence_point_id,graph_release_id,role,term_id,stable_key,resolution_status,source_kind) "
                         "VALUES(?,1,'part-1','p1',?,'direct',?,?,'resolved','link_job')", ('e' * 64, release, keys[2], keys[2]))
    return QuestionBankReadService(db, data_root=tmp_path), db, keys


def test_skill_index_current_versions_legacy_links_filters_and_cache(tmp_path):
    from question_bank.database.schema import connect

    service, db, keys = _seed_skill_bank(tmp_path)
    index = service.skill_index('bnu24-math-g8-upper')
    skills = {row['stable_key']: row for chapter in index['chapters']
              for section in chapter['sections'] for row in section['skills']}
    assert index['question_count'] == 5
    assert index['unlinked'] == {'no_usable_evidence': 2, 'no_skill_link': 1}
    assert [skills[key]['question_count'] for key in keys] == [2, 1, 0]
    assert skills[keys[0]]['difficulty'] == {'min': 2.0, 'median': 2.5, 'max': 3.0}
    page = service.list_questions(QuestionReadFilters(skill_keys=(keys[1],), include_skills=True))
    assert [item['id'] for item in page.items] == [1]
    assert len(page.items[0]['skills']) == 2
    assert page.items[0]['skill_hits'] == [{'point_id': 'p2', 'point_label': '判定点 2：求解'}]
    assert service.list_facets(QuestionReadFilters(skill_keys=(keys[1],)))['question_types'] == [{'value': '解答题', 'count': 1}]
    assert {item['id'] for item in service.list_questions(QuestionReadFilters(skill_unlinked=True)).items} == {3, 4, 5}
    assert 'skills' not in service.list_questions(QuestionReadFilters()).items[0]
    assert service.list_papers()[0]['skill_unlinked_question_count'] == 3
    with connect(db) as conn:
        conn.execute("UPDATE questions SET is_deleted=1 WHERE id=2")
    assert service.skill_index('bnu24-math-g8-upper')['question_count'] == 4
    assert service.list_questions(QuestionReadFilters(skill_keys=(keys[0],))).total == 1


def test_skill_index_1500_question_cold_and_hot_requests(tmp_path):
    from time import perf_counter

    service, _, keys = _seed_skill_bank(tmp_path, count=1500)
    def measure():
        started = perf_counter()
        index = service.skill_index('bnu24-math-g8-upper')
        page = service.list_questions(QuestionReadFilters(skill_keys=(keys[0],), include_skills=True))
        facets = service.list_facets(QuestionReadFilters(skill_keys=(keys[0],)))
        return (perf_counter() - started) * 1000, index, page, facets
    cold, index, page, facets = measure()
    hot, same_index, same_page, same_facets = measure()
    assert index['question_count'] == 1500
    assert same_index == index and same_page == page and same_facets == facets
    assert page.total == 1498
    print(f'TEST-1500 skill index + list + facets: cold={cold:.1f}ms hot={hot:.1f}ms')
    assert hot < 300


def test_paged_duplicate_groups_refresh_after_relabelling_and_keep_occurrence_numbers(tmp_path):
    from question_bank.database.schema import connect
    from question_bank.services.duplicate_analysis_copy_service import ensure_content_index

    db = tmp_path / "question_bank.db"
    _seed_paper(db)
    with connect(db) as conn:
        conn.execute("INSERT INTO papers(id,title) VALUES(2,'Reused paper')")
        conn.executemany("INSERT INTO questions(id,paper_id,question_number,question_text) VALUES(?,1,?,?)", [
            (1, "1", "计算 3+2"), (2, "2", "计算 3+2"), (3, "3", "计算 8+9"),
        ])
        ensure_content_index(conn, data_root=tmp_path)
        conn.execute("INSERT INTO paper_question_occurrences(paper_id,question_id,question_number) VALUES(2,1,'9')")
    service = QuestionBankReadService(db, data_root=tmp_path)
    first = service.list_questions(QuestionReadFilters(collapse_duplicates=True, page_size=1))
    second = service.list_questions(QuestionReadFilters(collapse_duplicates=True, page_size=1, page=2))
    assert first.total == second.total == 2
    assert [first.items[0]["id"], second.items[0]["id"]] == [3, 1]
    with connect(db) as conn:
        conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value,source) VALUES(2,'method','合成标注','manual')")
    refreshed = service.list_questions(QuestionReadFilters(collapse_duplicates=True, page_size=1, page=2))
    assert refreshed.items[0]["id"] == 2
    reused = service.list_questions(QuestionReadFilters(paper_ids=(2,), sort="paper_order"))
    assert [(item["id"], item["question_number"]) for item in reused.items] == [(1, "9")]
    counts = {paper["id"]: paper["question_count"] for paper in service.list_papers()}
    assert counts == {1: 3, 2: 1}


def test_uncommitted_writer_does_not_block_or_leak_into_reads(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "question_bank.db"
    _seed_paper(db_path)
    writer = sqlite3.connect(db_path, timeout=5.0)
    try:
        writer.execute("PRAGMA busy_timeout = 5000")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (9, 'Uncommitted', 'success')"
        )

        def read_titles() -> list[str]:
            return [
                paper["title"]
                for paper in QuestionBankReadService(db_path).list_papers()
            ]

        with ThreadPoolExecutor(max_workers=2) as executor:
            reader = executor.submit(read_titles)
            assert set(reader.result(timeout=10)) == {"Paper"}

        writer.commit()
        assert set(read_titles()) == {"Paper", "Uncommitted"}
    finally:
        writer.close()


def test_empty_wal_read_version_noise_keeps_cache_and_real_commits_refresh(tmp_path, monkeypatch):
    from integration import data_generation

    database = tmp_path / "TEST-cache-generation.db"
    _seed_paper(database)
    original_open = data_generation._GenerationMonitor._open

    class ReadVersionNoise:
        """Reproduce repeated read-only data_version bumps seen on this PC."""
        def __init__(self, connection):
            self.connection = connection
            self.reads = 0

        def execute(self, statement, *args):
            result = self.connection.execute(statement, *args)
            wal = Path(f"{database}-wal")
            if statement == "PRAGMA data_version" and (not wal.exists() or wal.stat().st_size == 0):
                value = result.fetchone()[0]
                self.reads += 1
                return SimpleNamespace(fetchone=lambda: (value + self.reads,))
            return result

        def close(self):
            self.connection.close()

    monkeypatch.setattr(data_generation._GenerationMonitor, "_open",
                        lambda monitor: ReadVersionNoise(original_open(monitor)))
    first = data_generation.commit_generation(database)
    assert [data_generation.commit_generation(database) for _ in range(6)] == [first] * 6
    service = QuestionBankReadService(database)
    assert [paper["title"] for paper in service.list_papers()] == ["Paper"]
    cache_keys = tuple(read_module._READ_RESULT_CACHE)
    assert service.list_papers()[0]["title"] == "Paper"
    assert tuple(read_module._READ_RESULT_CACHE) == cache_keys

    writer = sqlite3.connect(database)
    try:
        writer.execute("UPDATE papers SET title='TEST-committed' WHERE id=1")
        assert service.list_papers()[0]["title"] == "Paper"
        writer.commit()
        assert data_generation.commit_generation(database) > first
        assert service.list_papers()[0]["title"] == "TEST-committed"
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        settled = data_generation.commit_generation(database)
        assert [data_generation.commit_generation(database) for _ in range(6)] == [settled] * 6

        # A whole commit/checkpoint cycle between polls must also refresh,
        # including when the filesystem timestamp is kept unchanged.
        state = database.stat()
        writer.execute("UPDATE papers SET title='TEST-second' WHERE id=1")
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        os.utime(database, ns=(state.st_atime_ns, state.st_mtime_ns))
        assert data_generation.commit_generation(database) > settled
        assert service.list_papers()[0]["title"] == "TEST-second"
    finally:
        writer.close()
