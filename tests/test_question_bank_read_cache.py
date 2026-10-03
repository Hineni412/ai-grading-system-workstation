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
    from question_bank.services.knowledge_order import question_primary_skills, skill_placements
    with connect(db) as conn:
        assert question_primary_skills(conn, db, tmp_path, [1])[1] == keys[0]
        placement = skill_placements(conn, keys, 'bnu24-math-g8-upper')[keys[0]]
        assert placement.section_id and placement.chapter_id and placement.skill_name
    assert [skills[key]['question_count'] for key in keys] == [2, 1, 0]
    assert skills[keys[0]]['difficulty'] == {'min': 2.0, 'median': 2.5, 'max': 3.0}
    page = service.list_questions(QuestionReadFilters(skill_keys=(keys[1],), include_skills=True))
    assert [item['id'] for item in page.items] == [1]
    assert len(page.items[0]['skills']) == 2
    assert page.items[0]['evidence_point_count'] == 2
    assert all('/' not in row['display_name'] for row in skills.values())
    assert page.items[0]['skill_hits'] == [{'point_id': 'p2', 'point_label': '判定点 2：求解'}]
    assert service.list_facets(QuestionReadFilters(skill_keys=(keys[1],)))['question_types'] == [{'value': '解答题', 'count': 1}]
    assert {item['id'] for item in service.list_questions(QuestionReadFilters(skill_unlinked=True)).items} == {3, 4, 5}
    assert 'skills' not in service.list_questions(QuestionReadFilters()).items[0]
    assert service.list_papers()[0]['skill_unlinked_question_count'] == 3
    with connect(db) as conn:
        conn.executemany(
            "INSERT INTO question_error_patterns(question_id,category,pattern,status,source) VALUES(?,?,?,?,?)",
            [(1, '方法与思路', 'TEST-错因-1', 'confirmed', 'TEST'),
             (2, '方法与思路', 'TEST-错因-2', 'candidate', 'TEST'),
             (4, '计算与化简', 'TEST-错因-4', 'confirmed', 'TEST')],
        )
    assert [item['id'] for item in service.list_questions(QuestionReadFilters(error_pattern_categories=('方法与思路',))).items] == [1]
    assert service.list_facets(QuestionReadFilters())['error_pattern_categories'] == [
        {'value': '方法与思路', 'count': 1}, {'value': '计算与化简', 'count': 1}]
    with connect(db) as conn:
        conn.execute("UPDATE questions SET is_deleted=1 WHERE id=2")
    assert service.skill_index('bnu24-math-g8-upper')['question_count'] == 4
    assert service.list_questions(QuestionReadFilters(skill_keys=(keys[0],))).total == 1


def test_knowledge_sections_order_groups_missing_difficulty_and_similar_neighbors(monkeypatch):
    from question_bank.services import knowledge_order as order
    from dataclasses import replace
    first = order.Placement('kp_ch1', 1, '第一章', 'ki_sec1', 1, '1 第一节', 'sk_a', '甲技能')
    placements = {1: first, 2: first, 3: first, 9: first,
                  5: replace(first, skill_key='sk_b', skill_name='乙技能'),
                  6: replace(first, skill_key='', skill_name=''),
                  7: replace(first, section_id='', section_order=10**9, section_label='本章综合'),
                  10: replace(first, chapter_id='kp_ch2', chapter_order=2, chapter_label='第二章')}
    compared = []
    def similarity(left, right):
        compared.append((left, right))
        return .8 if {left, right} == {'q1', 'q3'} else .1
    monkeypatch.setattr(order, 'text_similarity', similarity)
    entries = [order.OrderEntry(qid, difficulty, kind, f'q{qid}') for qid, difficulty, kind in
               [(10, 1, '选择题'), (7, 1, '选择题'), (8, 1, '选择题'), (6, 1, '选择题'),
                (9, None, '选择题'), (3, 4, '解答题'), (2, 3, '填空题'), (1, 2, '选择题'), (5, 1, '解答题')]]
    sections = order.knowledge_sections(entries, placements)
    assert [section.title for section in sections] == ['第一章 · 1 第一节', '第一章 · 本章综合',
                                                     '第二章 · 1 第一节', '未归入章节']
    assert sections[0].question_ids == [5, 1, 3, 2, 9, 6]
    assert all(left not in {'q5', 'q6'} and right not in {'q5', 'q6'} for left, right in compared)
    assert sections[-1].section_id is None
    assert order.parsed_difficulty('3.5') == 3.5
    assert all(order.parsed_difficulty(value) is None for value in ('?', 'nan', 'inf', 0, 11, None))
    tied = order.knowledge_sections([order.OrderEntry(qid, 2, kind, '') for qid, kind in
                                    [(3, '解答题'), (2, '填空题'), (1, '选择题')]], placements)
    assert tied[0].question_ids == [1, 2, 3]


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
    members = service.list_questions(QuestionReadFilters(collapse_duplicates=True, include_skills=True, page_size=1, page=2))
    assert members.items[0]['duplicate_members'] == [{'id': 2, 'paper_id': 1, 'question_number': '2', 'paper_title': 'Paper'}]
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


def test_skill_repair_only_fills_requested_current_points_and_keeps_teacher_links(tmp_path):
    from backend.jobs.knowledge_link_job import run_knowledge_link_job
    from backend.jobs.manager import JobContext
    from backend.jobs.store import JobStore
    from question_bank.database.schema import connect
    service, db, keys = _seed_skill_bank(tmp_path)
    release = service.skill_index('bnu24-math-g8-upper')['graph_release_id']
    with connect(db) as conn:
        # Existing knowledge-topic links used to make the old missing-only mode skip this question.
        conn.execute("INSERT INTO evidence_point_knowledge_links(evidence_version_id,question_id,part_id,evidence_point_id,graph_release_id,role,term_id,stable_key,resolution_status,source_kind) VALUES(?,4,'part-1','p1',?,'direct','kp_bnu24_math_g8_upper_1_1','kp_bnu24_math_g8_upper_1_1','resolved','link_job')", (f'{4:064x}', release))
        before = [tuple(row) for row in conn.execute('SELECT * FROM evidence_point_knowledge_links WHERE question_id=1')]
    store = JobStore(tmp_path / 'TEST-repair-jobs.db')
    calls = []
    def gateway(request):
        calls.append(request)
        return {q['question_id']: [{'evidence_point_id': p['evidence_point_id'], 'links': [{'fine_term_id': keys[0], 'role': 'direct'}]}
            for part in q['parts'] for p in part['points']] for q in request['questions']}
    def run(ids):
        record = store.create_job('knowledge_link', {'mode': 'missing_skills', 'question_ids': ids})
        return run_knowledge_link_job(context=JobContext(record.id, record.job_type, record.payload, store),
                                     question_bank_db_path=db, data_root=tmp_path, link_gateway=gateway)
    run([1, 3, 4, 5])
    assert [[q['question_id'] for q in request['questions']] for request in calls] == [[4]]
    assert service.skill_index('bnu24-math-g8-upper')['unlinked']['no_skill_link'] == 0
    with connect(db) as conn:
        assert [tuple(row) for row in conn.execute('SELECT * FROM evidence_point_knowledge_links WHERE question_id=1')] == before
        conn.execute("DELETE FROM evidence_point_knowledge_links WHERE question_id=4")
        conn.execute("INSERT INTO evidence_point_knowledge_links(evidence_version_id,question_id,part_id,evidence_point_id,graph_release_id,role,term_id,stable_key,resolution_status,source_kind) VALUES(?,4,'part-1','p1',?,'direct','kp_bnu24_math_g8_upper_1_1','kp_bnu24_math_g8_upper_1_1','resolved','teacher')", (f'{4:064x}', release))
    run([4])
    assert len(calls) == 1


def test_skill_repair_does_not_save_a_response_after_the_source_changes(tmp_path):
    from backend.jobs.knowledge_link_job import run_knowledge_link_job
    from backend.jobs.manager import JobContext
    from backend.jobs.store import JobStore
    from question_bank.database.schema import connect
    service, db, keys = _seed_skill_bank(tmp_path)
    store = JobStore(tmp_path / 'TEST-repair-jobs.db')
    record = store.create_job('knowledge_link', {'mode': 'missing_skills', 'question_ids': [4]})
    def gateway(request):
        with connect(db) as conn:
            conn.execute("UPDATE questions SET question_text='TEST-new source' WHERE id=4")
        return {4: [{'evidence_point_id': 'p1', 'links': [{'fine_term_id': keys[0], 'role': 'direct'}]}]}
    run_knowledge_link_job(context=JobContext(record.id, record.job_type, record.payload, store),
                          question_bank_db_path=db, data_root=tmp_path, link_gateway=gateway)
    with connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM evidence_point_knowledge_links WHERE question_id=4').fetchone()[0] == 0


def test_repair_preview_lists_each_missing_product_without_model_calls(tmp_path):
    from backend.jobs.question_bank_repair import repair_preview
    service, db, _ = _seed_skill_bank(tmp_path)
    preview = repair_preview(service, 'bnu24-math-g8-upper', 'skills')
    assert [item['id'] for item in preview['items']] == [3, 4, 5]
    assert preview['model_calls'] == 0
    assert preview['counts']['skills'] == 3
    assert preview['counts']['evidence'] == 2
    assert 'evidence' not in preview['items'][1]['missing']
    assert len(preview['fingerprint']) == 64
    assert repair_preview(service, 'bnu24-math-g8-upper', 'skills')['fingerprint'] == preview['fingerprint']
    with pytest.raises(ValueError, match='当前教学学期'):
        repair_preview(service, 'bnu24-math-g8-upper', 'skills', [6])


@pytest.mark.parametrize('analysis_fails', [False, True])
@pytest.mark.parametrize('kind', ['skills', 'analysis', 'all'])
def test_one_click_repair_routes_only_previewed_gaps_and_stops_failed_dependencies(tmp_path, monkeypatch, analysis_fails, kind):
    from backend.jobs import question_bank_repair as repair
    from backend.jobs.manager import JobContext
    from backend.jobs.store import JobStore
    _, db, _ = _seed_skill_bank(tmp_path)
    # Three previewed questions: one lacks points, one only needs a link, one changed since preview.
    versions = [
        [{'id': 3, 'missing': ['evidence', 'criteria', 'skills'], 'revision': 'r3', 'blocked_reason': ''},
         {'id': 4, 'missing': ['skills'], 'revision': 'r4', 'blocked_reason': ''},
         {'id': 5, 'missing': ['skills'], 'revision': 'changed', 'blocked_reason': ''}],
        [{'id': 3, 'missing': ['evidence', 'skills'] if analysis_fails else ['skills'], 'blocked_reason': ''},
         {'id': 4, 'missing': ['skills'], 'blocked_reason': ''}],
        [{'id': 5, 'question_number': '5', 'missing': ['skills'], 'blocked_reason': ''}] +
        ([{'id': 3, 'question_number': '3', 'missing': ['evidence', 'skills'], 'blocked_reason': ''}] if analysis_fails else []),
    ]
    preview_kinds = []
    def preview(*args, **kwargs):
        preview_kinds.append(args[2])
        return {'items': versions.pop(0)}
    monkeypatch.setattr(repair, 'repair_preview', preview)
    store = JobStore(tmp_path / 'TEST-one-click.db')
    record = store.create_job('question_bank_repair', {'question_ids': [3, 4, 5], 'kind': kind,
        'curriculum_volume_id': 'bnu24-math-g8-upper', 'revisions': {'3': 'r3', '4': 'r4', '5': 'r5'}})
    calls = []
    def tagging(**kw):
        calls.append(('analysis', kw['context'].payload))
    def linking(**kw):
        calls.append(('links', kw['context'].payload))
    result = repair.run_question_bank_repair_job(context=JobContext(record.id, record.job_type, record.payload, store),
        question_bank_db_path=db, data_root=tmp_path, tagging_runner=tagging, link_runner=linking,
        ai_service_factory=lambda: None, link_gateway_factory=lambda: None)
    assert calls[0][1]['question_ids'] == [3]
    assert calls[0][1]['repair_missing_only'] is True
    assert calls[1][1]['question_ids'] == ([4] if analysis_fails else [3, 4])
    assert calls[1][1]['mode'] == 'missing_skills'
    assert result['outcome'] == 'partial'
    assert result['completed_count'] == (1 if analysis_fails else 2)
    assert result['remaining'][0]['reason'].startswith('题目已变化')
    # The entry filter must not hide a skill gap after the analysis stage finishes.
    assert preview_kinds == [kind, 'all', 'all']
