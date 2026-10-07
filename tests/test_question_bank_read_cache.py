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
    read_module._SKILL_SOURCE_MEMO.clear()
    yield
    read_module._READ_RESULT_CACHE.clear()
    read_module._SKILL_SOURCE_MEMO.clear()


def test_result_cache_byte_budget_replacement_eviction_and_private_values():
    import pickle
    from integration.result_cache import ResultCache

    value = {"TEST": [1, 2, 3]}
    size = len(pickle.dumps(value, pickle.HIGHEST_PROTOCOL))
    cache = ResultCache(limit=3, max_bytes=size * 2)
    cache.put("A", value)
    value["TEST"].append(4)
    cached = cache.get_or_compute("A", lambda: pytest.fail("missed stored value"))
    assert cached == {"TEST": [1, 2, 3]}
    cached["TEST"].clear()
    cache.put("B", {"TEST": [1, 2, 3]})
    cache.get_or_compute("A", lambda: pytest.fail("missed unmodified value"))
    cache.put("C", {"TEST": [1, 2, 3]})
    assert list(cache._entries) == ["A", "C"]
    assert cache._bytes == sum(map(len, cache._entries.values())) == size * 2
    # An oversized replacement must not leave the old value under its key.
    cache.put("A", "TEST" * size)
    assert list(cache._entries) == ["C"]
    assert cache.get_or_compute("A", lambda: "TEST-new") == "TEST-new"
    assert cache._bytes <= size * 2
    cache.clear()
    assert not cache._entries and cache._bytes == 0


@pytest.mark.parametrize("max_bytes,fail", [(None, False), (1, False), (1, True)])
def test_result_cache_concurrent_flight_shares_private_result_and_failure(
    monkeypatch, max_bytes, fail,
):
    import threading
    import integration.result_cache as cache_module

    started, waiting, release = (threading.Event() for _ in range(3))
    original_flight = cache_module._Flight

    class ObservedEvent:
        def __init__(self):
            self.event = threading.Event()

        def wait(self):
            waiting.set()
            return self.event.wait(5)

        def set(self):
            self.event.set()

    def flight():
        result = original_flight()
        result.event = ObservedEvent()
        return result

    monkeypatch.setattr(cache_module, "_Flight", flight)
    cache = cache_module.ResultCache(limit=1, max_bytes=max_bytes)
    computations = []

    def compute():
        computations.append(1)
        started.set()
        assert release.wait(5)
        if fail:
            raise ValueError("TEST-compute-failed")
        return {"TEST": [1]}

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(cache.get_or_compute, "TEST", compute)
        assert started.wait(5)
        second = executor.submit(cache.get_or_compute, "TEST", compute)
        try:
            assert waiting.wait(5)
        finally:
            release.set()
        if fail:
            for result in (first, second):
                with pytest.raises(ValueError, match="TEST-compute-failed"):
                    result.result(timeout=5)
        else:
            owner, waiter = first.result(timeout=5), second.result(timeout=5)
            assert owner == waiter == {"TEST": [1]}
            owner["TEST"].clear()
            assert waiter == {"TEST": [1]}
    assert len(computations) == 1 and not cache._flights
    if max_bytes == 1:
        assert not cache._entries and cache._bytes == 0
        assert cache.get_or_compute("TEST", lambda: "TEST-retry") == "TEST-retry"
    else:
        assert cache.get_or_compute("TEST", lambda: pytest.fail("miss")) == {"TEST": [1]}


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


def test_list_questions_frequency_sort_uses_exam_frequency_shares(tmp_path, monkeypatch):
    """frequency_desc 排序取读服务的章节考情考频（期中/期末卷组覆盖率），
    无考频值的题排在最后；JSON 读连接不再触碰 question_frequency_cache。"""
    db = tmp_path / "TEST-frequency-sort.db"
    _seed_paper(db)
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO questions(id,paper_id,question_number,question_text,"
            "question_type,difficulty) VALUES(?,1,?,?,'解答题',3)",
            [
                (1, "1", "TEST-考频排序甲"),
                (2, "2", "TEST-考频排序乙"),
                (3, "3", "TEST-考频排序丙"),
                (4, "4", "TEST-考频排序丁"),
            ],
        )
    service = QuestionBankReadService(db, data_root=tmp_path)
    calls: list[bool] = []

    def fake_frequency() -> dict[int, tuple[float, float]]:
        calls.append(True)
        return {2: (0.9, 0.1), 1: (0.5, 0.4), 4: (0.2, 0.6)}

    monkeypatch.setattr(service, "exam_frequency", fake_frequency)
    page = service.list_questions(QuestionReadFilters(sort="frequency_desc"))
    # 取 max(期中, 期末) 降序：0.9 / 0.6 / 0.5，无值的 3 号垫底。
    assert [item["id"] for item in page.items] == [2, 4, 1, 3]
    assert page.total == 4
    assert calls == [True]
    page_asc = service.list_questions(QuestionReadFilters(sort="frequency_asc"))
    assert calls == [True, True]
    # 升序按同一 max 指标：0.5 / 0.6 / 0.9，无值的 3 号仍垫底。
    assert [item["id"] for item in page_asc.items] == [1, 4, 2, 3]


def test_filter_query_join_params_precede_where_params(tmp_path):
    """带参 JOIN 的参数必须先于 WHERE 参数进入列表：SQL 文本按书写顺序
    消费 ?，JOIN 子句在 WHERE 之前。knowledge_point JOIN 与考频 JSON
    JOIN 共用同一约定，否则 JOIN 的 ? 会吞掉 WHERE 的首个参数。"""
    db = tmp_path / "TEST-join-params.db"
    _seed_paper(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO questions(id,paper_id,question_number,question_text,"
            "question_type,difficulty) VALUES(1,1,'1','TEST-参数顺序题','解答题',5)"
        )
        conn.execute(
            "INSERT INTO question_tags(question_id,tag_type,tag_value) "
            "VALUES(1,'knowledge_point','TEST-参数顺序')"
        )

    joins, where, params = read_module.build_question_filter_query(
        knowledge_point="TEST-参数顺序",
        question_types=["解答题"],
    )
    where_sql = "WHERE " + " AND ".join(where)
    with sqlite3.connect(db) as conn:
        # 与 _list_questions 的 count 查询同构。
        count = conn.execute(
            " ".join(
                [
                    "SELECT COUNT(DISTINCT q.id) FROM questions q",
                    *joins,
                    where_sql,
                ]
            ),
            params,
        ).fetchone()[0]
    assert count == 1

    # 考频排序在 JOIN 之后、WHERE 之前插入 JSON 参数：插入位置为既有 JOIN
    # 的 ? 数，组合后参数顺序须与文本 ? 顺序一致。
    join_markers = sum(sql.count("?") for sql in joins)
    list_joins = [
        *joins,
        "LEFT JOIN (SELECT CAST(json_extract(value, '$[0]') AS INTEGER) "
        "AS question_id, json_extract(value, '$[1]') AS score_midterm, "
        "json_extract(value, '$[2]') AS score_final, 0.0 AS score_zhongkao "
        "FROM json_each(?)) qfc ON qfc.question_id = q.id",
    ]
    list_params = list(params)
    list_params.insert(join_markers, json.dumps([[1, 0.5, 0.25]]))
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            " ".join(
                [
                    "SELECT DISTINCT q.id FROM questions q",
                    *list_joins,
                    where_sql,
                    "ORDER BY qfc.score_midterm DESC",
                ]
            ),
            list_params,
        ).fetchall()
    assert [int(row[0]) for row in rows] == [1]


def test_session_unlinked_count_reuses_skill_index_for_confirmed_live_questions(tmp_path):
    from question_bank.database.schema import connect
    service, db, _keys = _seed_skill_bank(tmp_path)
    with connect(db) as conn:
        conn.executemany("INSERT INTO grading_question_links "
            "(grading_session_id,source_question_id,bank_question_id,link_method,status) "
            "VALUES ('7',?,?, 'TEST', 'confirmed')", [(str(qid), qid) for qid in range(1, 7)])
        conn.execute("INSERT INTO grading_question_links "
            "(grading_session_id,source_question_id,bank_question_id,link_method,status) "
            "VALUES ('7','duplicate',1,'TEST','confirmed')")
    status = service.session_analysis_status(7)
    assert status["question_count"] == 5  # Unique canonical questions; deleted question excluded.
    index = service.skill_index('bnu24-math-g8-upper')
    assert status["unlinked_skill_count"] == sum(index["unlinked"].values()) == 3
    with connect(db) as conn:
        conn.execute("UPDATE grading_question_links SET status='suggested' WHERE bank_question_id=3")
    assert service.session_analysis_status(7)["unlinked_skill_count"] == 2
    assert service.session_analysis_status(999)["unlinked_skill_count"] == 0


def test_skill_index_current_versions_legacy_links_filters_and_cache(tmp_path, monkeypatch):
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
    page.items[0]['skill_hits'][0]['point_label'] = 'TEST-caller-edit'
    other_page = service.list_questions(QuestionReadFilters(skill_keys=(keys[1],), include_skills=True, page_size=1))
    assert other_page.items[0]['skill_hits'] == [{'point_id': 'p2', 'point_label': '判定点 2：求解'}]
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
    from question_bank.services import question_skill_index
    from question_bank.taxonomy.curriculum_catalog import curriculum_volume
    other_volume = curriculum_volume(volume_id='bnu24-math-g9-upper')
    with connect(db) as conn:
        conn.execute("INSERT INTO papers(id,title,grade,semester,textbook_version,import_status) "
                     "VALUES(2,'TEST-other-volume',?,?,?,'success')",
                     (other_volume['grade'], other_volume['semester'], other_volume['textbook_version']))
        conn.execute("INSERT INTO questions(id,paper_id,question_number,question_text,question_type) "
                     "VALUES(7,2,'7','TEST-other-volume-question','解答题')")
    loaded = []
    original_load = question_skill_index.load_profiles
    def tracked_profiles(db_path, ids, **kwargs):
        loaded.append(set(ids))
        return original_load(db_path, ids, **kwargs)
    monkeypatch.setattr(question_skill_index, 'load_profiles', tracked_profiles)
    assert service.skill_index('bnu24-math-g8-upper')['question_count'] == 4
    assert loaded == [{1, 3, 4, 5}]
    newest = service.list_questions(QuestionReadFilters(page_size=1, include_skills=True))
    assert newest.items[0]['id'] == 7
    assert loaded[-1] == {7}
    papers = {paper['id']: paper for paper in service.list_papers()}
    assert papers[1]['question_count'] == 4 and papers[2]['question_count'] == 1
    assert loaded[-1] == {1, 3, 4, 5, 7}


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


def test_skill_index_1500_question_cold_and_hot_requests(tmp_path, monkeypatch):
    from time import perf_counter
    from question_bank.services import question_skill_index

    service, _, keys = _seed_skill_bank(tmp_path, count=1500)
    (tmp_path / 'question_bank').mkdir(exist_ok=True)
    builds = []
    original_build = question_skill_index.build_skill_snapshot
    def build(*args, **kwargs):
        builds.append(1)
        return original_build(*args, **kwargs)
    monkeypatch.setattr(question_skill_index, 'build_skill_snapshot', build)
    # The initial paper/count requests share the same expensive source read.
    started = perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_index = pool.submit(service.skill_index, 'bnu24-math-g8-upper')
        first_papers = pool.submit(service.list_papers)
        assert first_index.result()['question_count'] == 1500
        assert first_papers.result()[0]['question_count'] == 1500
    assert len(builds) == 1
    cold_counts = (perf_counter() - started) * 1000
    def measure():
        started = perf_counter()
        index = service.skill_index('bnu24-math-g8-upper')
        page = service.list_questions(QuestionReadFilters(skill_keys=(keys[0],), include_skills=True))
        facets = service.list_facets(QuestionReadFilters(skill_keys=(keys[0],)))
        return (perf_counter() - started) * 1000, index, page, facets
    first_list, index, page, facets = measure()
    hot, same_index, same_page, same_facets = measure()
    assert index['question_count'] == 1500
    assert same_index == index and same_page == page and same_facets == facets
    assert page.total == 1498
    print(f'TEST-1500 counts+papers={cold_counts:.1f}ms first-list+facets={first_list:.1f}ms hot={hot:.1f}ms')
    assert hot < 300
    # More filters than the public-result LRU limit must not trigger another
    # full-bank source read when the teacher refreshes the initial selection.
    for page_number in range(1, read_module._READ_RESULT_CACHE_LIMIT + 10):
        service.list_questions(QuestionReadFilters(keyword=f'TEST-empty-filter-{page_number}'))
    refreshed, same_index, same_page, same_facets = measure()
    assert same_index == index and same_page == page and same_facets == facets
    assert len(builds) == 1
    print(f'TEST-1500 refresh after browsing: {refreshed:.1f}ms, source builds={len(builds)}')
    # 模拟进程重启：原缓存文件恢复同一份投影，不再打开全题库输入。
    from question_bank.services.file_cache import clear_file_caches
    from question_bank.database.schema import connect
    cache_path = service._local_skill_path()
    assert cache_path.is_file()
    read_module._READ_RESULT_CACHE.clear()
    clear_file_caches()
    restarted = QuestionBankReadService(service.db_path, data_root=tmp_path)
    assert restarted.skill_index('bnu24-math-g8-upper') == index
    assert len(builds) == 1
    import hashlib
    import subprocess
    import sys
    expected_digest = hashlib.sha256(json.dumps(index, sort_keys=True).encode()).hexdigest()
    script = """
import hashlib,json,sys
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.services import question_skill_index
def fail(*args,**kwargs): raise AssertionError('TEST restarted skill cache recomputed')
question_skill_index.build_skill_snapshot=fail
result=QuestionBankReadService(sys.argv[1],data_root=sys.argv[2]).skill_index('bnu24-math-g8-upper')
print(hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest())
"""
    process = subprocess.run([sys.executable, '-c', script, str(service.db_path), str(tmp_path)],
                             capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == expected_digest
    (tmp_path / 'question_bank/TEST-unrelated-export.txt').write_text('TEST', encoding='utf-8')
    read_module._READ_RESULT_CACHE.clear()
    clear_file_caches()
    assert restarted.skill_index('bnu24-math-g8-upper') == index
    assert len(builds) == 1
    # 活跃 WAL 归档只改变物理文件；来源内容相同，仍能恢复投影。
    with sqlite3.connect(service.db_path) as writer:
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute("UPDATE questions SET reason='TEST-checkpoint-cache' WHERE id=2")
        writer.commit()
        assert restarted.skill_index('bnu24-math-g8-upper') == index
        before_checkpoint = len(builds)
        writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        read_module._READ_RESULT_CACHE.clear()
        clear_file_caches()
        assert restarted.skill_index('bnu24-math-g8-upper') == index
        assert len(builds) == before_checkpoint
    process = subprocess.run([sys.executable, '-c', script, str(service.db_path), str(tmp_path)],
                             capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == expected_digest
    # 新增、改写、删除正文，即使没有改数据库，也不能恢复旧投影。
    sidecar = tmp_path / 'question_bank/rich_content/question_1.json'
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    for body in ('TEST-new-sidecar', 'TEST-edited-sidecar', None):
        if body is None:
            sidecar.unlink()
        else:
            sidecar.write_text(json.dumps({'version': 3, 'question_blocks': [
                {'type': 'text', 'text': body}]}), encoding='utf-8')
        read_module._READ_RESULT_CACHE.clear()
        clear_file_caches()
        count_before = len(builds)
        actual = restarted.skill_index('bnu24-math-g8-upper')
        assert len(builds) == count_before + 1
        assert actual['unlinked']['no_usable_evidence'] == (1 if body is None else 2)
        read_module._READ_RESULT_CACHE.clear()
        fresh = QuestionBankReadService(service.db_path, data_root=tmp_path, persist_skill_snapshots=False)
        assert fresh.skill_index('bnu24-math-g8-upper') == actual
    read_module._READ_RESULT_CACHE.clear()
    count_before = len(builds)
    cache_path.write_bytes(b'TEST-corrupt-skill-cache')
    assert restarted.skill_index('bnu24-math-g8-upper') == index
    assert len(builds) == count_before + 1
    # 超出容量上限时完整读取，保留旧文件，不能失败或输出半份投影。
    read_module._READ_RESULT_CACHE.clear()
    before_cap = cache_path.read_bytes()
    count_before = len(builds)
    with monkeypatch.context() as patch:
        patch.setattr(read_module, '_SKILL_LOCAL_CACHE_MAX_BYTES', 1)
        assert restarted.skill_index('bnu24-math-g8-upper') == index
    assert len(builds) == count_before + 1
    assert cache_path.read_bytes() == before_cap
    read_module._READ_RESULT_CACHE.clear()
    count_before = len(builds)
    with monkeypatch.context() as patch:
        patch.setattr(read_module, '_skill_cache_calculation_revision', lambda: 'TEST-changed-calculation')
        assert restarted.skill_index('bnu24-math-g8-upper') == index
    assert len(builds) == count_before + 1
    with connect(service.db_path) as writer:
        writer.execute('UPDATE questions SET is_deleted=1 WHERE id=1')
    count_before = len(builds)
    assert restarted.skill_index('bnu24-math-g8-upper')['question_count'] == 1499
    assert len(builds) == count_before + 1
    # 保存失败仍返回当前结果，临时文件自行清理，原缓存保持原样。
    from question_bank import atomic_files
    saved_bytes = cache_path.read_bytes()
    with connect(service.db_path) as writer:
        writer.execute("UPDATE questions SET difficulty='9' WHERE id=2")
    def unavailable(*args):
        raise PermissionError('TEST-skill-cache-disk-unavailable')
    monkeypatch.setattr(atomic_files, 'replace_with_retry', unavailable)
    assert restarted.skill_index('bnu24-math-g8-upper')['question_count'] == 1499
    assert cache_path.read_bytes() == saved_bytes
    assert not list(cache_path.parent.glob('skill-read-*.tmp'))
    # 内容版本读取暂不可用时，派生缓存仍不能阻断正常题目读取。
    from integration import data_generation
    for error_type in (AttributeError, ImportError):
        with connect(service.db_path) as writer:
            writer.execute('UPDATE questions SET reason=? WHERE id=2',
                           (f'TEST-version-unavailable-{error_type.__name__}',))
        def unavailable_version(*args, **kwargs):
            raise error_type('TEST-cache-content-version-unavailable')
        with monkeypatch.context() as patch:
            patch.setattr(data_generation, 'database_content_revision', unavailable_version)
            assert restarted.skill_index('bnu24-math-g8-upper')['question_count'] == 1499
        assert cache_path.read_bytes() == saved_bytes


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


@pytest.mark.parametrize('damaged_question', [False, True])
def test_skill_repair_only_fills_requested_current_points_and_keeps_teacher_links(tmp_path, damaged_question):
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
    if damaged_question:
        sidecar = tmp_path / 'question_bank/rich_content/question_5.json'
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text('{TEST-corrupt', encoding='utf-8')
    calls = []
    def gateway(request):
        calls.append(request)
        return {q['question_id']: [{'evidence_point_id': p['evidence_point_id'], 'links': [{'fine_term_id': keys[0], 'role': 'direct'}]}
            for part in q['parts'] for p in part['points']] for q in request['questions']}
    def run(ids):
        record = store.create_job('knowledge_link', {'mode': 'missing_skills', 'question_ids': ids})
        return run_knowledge_link_job(context=JobContext(record.id, record.job_type, record.payload, store),
                                     question_bank_db_path=db, data_root=tmp_path, link_gateway=gateway)
    first = run([1, 3, 4, 5])
    assert first['questions_failed'] == int(damaged_question)
    if damaged_question:
        assert first['audit'][0] == {'question_id': 5, 'action': 'input_failed',
            'reason_code': 'rich_content_invalid_json'}
    assert [[q['question_id'] for q in request['questions']] for request in calls] == [[4]]
    assert service.skill_index('bnu24-math-g8-upper')['unlinked']['no_skill_link'] == 0
    with connect(db) as conn:
        assert [tuple(row) for row in conn.execute('SELECT * FROM evidence_point_knowledge_links WHERE question_id=1')] == before
        conn.execute("DELETE FROM evidence_point_knowledge_links WHERE question_id=4")
        conn.execute("INSERT INTO evidence_point_knowledge_links(evidence_version_id,question_id,part_id,evidence_point_id,graph_release_id,role,term_id,stable_key,resolution_status,source_kind) VALUES(?,4,'part-1','p1',?,'direct','kp_bnu24_math_g8_upper_1_1','kp_bnu24_math_g8_upper_1_1','resolved','teacher')", (f'{4:064x}', release))
    run([4])
    assert len(calls) == 1
    if damaged_question:
        last = run([5])
        assert last['questions_failed'] == 1
        assert last['questions_linked'] == 0
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


def _seed_old_release_links(db, service):
    """Give question 4's two points links that live only under an old release."""
    from question_bank.database.schema import connect
    release = service.skill_index('bnu24-math-g8-upper')['graph_release_id']
    version = f'{4:064x}'
    section = 'kp_bnu24_math_g8_upper_1_1'
    with connect(db) as conn:
        for point in ('p1', 'p2'):
            conn.execute(
                "INSERT INTO evidence_point_knowledge_links(evidence_version_id,"
                "question_id,part_id,evidence_point_id,graph_release_id,role,"
                "term_id,stable_key,resolution_status,source_kind) "
                "VALUES(?,4,'part-1',?,'kgr_TEST_old','direct',?,?,'resolved',"
                "'migrated_from_embedded')",
                (version, point, section, section),
            )
    return release, version, section


def _assert_carried_links(db, data_root, release, version, section, keys):
    from question_bank.database.schema import connect
    from question_bank.services.question_skill_index import build_skill_snapshot
    from question_bank.solution_evidence.knowledge_links import load_point_links
    links = load_point_links(db, [version], release)
    p1 = links[version]['p1']
    assert any(
        link.role == 'direct' and link.stable_key == keys[0]
        and link.resolution_status == 'resolved' for link in p1)
    p2 = links[version]['p2']
    assert any(
        link.role == 'direct' and link.stable_key == section
        and link.resolution_status == 'resolved' for link in p2)
    assert not any(link.stable_key.startswith('sk_') for link in p2)
    with connect(db) as conn:
        snapshot = build_skill_snapshot(conn, db, data_root)
    gaps = snapshot['gap_points']
    assert [gap['point_id'] for gap in gaps.get(4, [])] == ['p2']


def test_skill_repair_carries_forward_points_left_in_older_release(tmp_path):
    from backend.jobs.knowledge_link_job import run_knowledge_link_job
    from backend.jobs.manager import JobContext
    from backend.jobs.store import JobStore
    service, db, keys = _seed_skill_bank(tmp_path)
    release, version, section = _seed_old_release_links(db, service)
    store = JobStore(tmp_path / 'TEST-repair-jobs.db')
    record = store.create_job('knowledge_link', {'mode': 'missing_skills', 'question_ids': [4]})
    summary = run_knowledge_link_job(
        context=JobContext(record.id, record.job_type, record.payload, store),
        question_bank_db_path=db, data_root=tmp_path,
        link_gateway=lambda request: {4: [
            {'evidence_point_id': 'p1', 'links': [{'fine_term_id': keys[0], 'role': 'direct'}]},
            {'evidence_point_id': 'p2', 'links': []}]})
    assert summary['links_carried_forward'] >= 1
    _assert_carried_links(db, tmp_path, release, version, section, keys)


@pytest.mark.parametrize('damage', [None, 'rich', 'evidence', 'stale'])
def test_missing_only_repair_carries_forward_points_left_in_older_release(tmp_path, damage):
    from backend.jobs.knowledge_link_job import run_knowledge_link_job
    from backend.jobs.manager import JobContext
    from backend.jobs.store import JobStore
    service, db, keys = _seed_skill_bank(tmp_path)
    release, version, section = _seed_old_release_links(db, service)
    from question_bank.database.schema import connect
    with connect(db) as conn:
        conn.execute("UPDATE question_solution_evidence_versions SET status='proposed' WHERE evidence_version_id=?", (version,))
        conn.execute("""INSERT INTO question_solution_evidence_versions(
            evidence_version_id,question_id,source_content_hash,schema_version,content_hash,evidence_json,status,
            source_kind,source_reference,created_by,graph_release_id,created_at)
            SELECT ?,question_id,?,schema_version,content_hash,evidence_json,'approved',
            source_kind,'TEST-stale-approved',created_by,graph_release_id,created_at
            FROM question_solution_evidence_versions WHERE evidence_version_id=?""",
            ('d' * 64, 'f' * 64, version))
    from question_bank.training_criteria.adapters import QuestionAnalysisInputLoader
    from question_bank.solution_evidence.repository import SolutionEvidenceRepository
    question = QuestionAnalysisInputLoader(db_path=db, data_root=tmp_path).load([4])[0]
    assert SolutionEvidenceRepository(db).latest(4,
        current_source_content_hash=question.source_content_hash)['evidence_version_id'] == version
    store = JobStore(tmp_path / 'TEST-repair-jobs.db')
    ids = [1, 4] if damage else [4]
    if damage == 'rich':
        sidecar = tmp_path / 'question_bank/rich_content/question_1.json'
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text('{TEST-corrupt', encoding='utf-8')
    elif damage:
        from question_bank.database.schema import connect
        with connect(db) as conn:
            if damage == 'evidence':
                conn.execute('UPDATE question_solution_evidence_versions SET evidence_json=? WHERE question_id=1',
                             ('{"parts":[1]}',))
            else:
                conn.execute("UPDATE questions SET answer_text='TEST-changed-answer' WHERE id=1")
    record = store.create_job('knowledge_link', {'mode': 'missing_only', 'question_ids': ids})
    calls = []
    def gateway(request):
        calls.append([item['question_id'] for item in request['questions']])
        return {4: [
            {'evidence_point_id': 'p1', 'links': [{'fine_term_id': keys[0], 'role': 'direct'}]},
            {'evidence_point_id': 'p2', 'links': []}]}
    summary = run_knowledge_link_job(
        context=JobContext(record.id, record.job_type, record.payload, store),
        question_bank_db_path=db, data_root=tmp_path, link_gateway=gateway)
    assert calls == [[4]]
    assert summary['questions_failed'] == int(bool(damage))
    if damage:
        assert summary['questions_total'] == summary['questions_pending'] == 2
        assert summary['questions_linked'] == 1
        assert summary['audit'][0] == {'question_id': 1, 'action': 'input_failed',
            'reason_code': {'rich': 'rich_content_invalid_json', 'evidence': 'evidence_unreadable',
                            'stale': 'part_assessment_source_changed'}[damage]}
    assert summary['links_carried_forward'] >= 1
    _assert_carried_links(db, tmp_path, release, version, section, keys)


@pytest.mark.parametrize('damaged_question', [False, True])
def test_repair_preview_lists_each_missing_product_without_model_calls(tmp_path, damaged_question):
    from backend.jobs.question_bank_repair import repair_preview
    service, db, _ = _seed_skill_bank(tmp_path)
    if damaged_question:
        sidecar = tmp_path / 'question_bank/rich_content/question_4.json'
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text('{TEST-corrupt', encoding='utf-8')
    preview = repair_preview(service, 'bnu24-math-g8-upper', 'skills')
    assert [item['id'] for item in preview['items']] == [3, 4, 5]
    assert preview['model_calls'] == 0
    assert preview['counts']['skills'] == 3
    assert preview['counts']['evidence'] == (3 if damaged_question else 2)
    if damaged_question:
        assert 'evidence' in preview['items'][1]['missing']
        assert preview['items'][1]['blocked_reason_code'] == 'rich_content_invalid_json'
        assert '文件损坏' in preview['items'][1]['blocked_reason']
        assert preview['repairable_count'] == 2
    else:
        assert 'evidence' not in preview['items'][1]['missing']
    assert len(preview['fingerprint']) == 64
    assert repair_preview(service, 'bnu24-math-g8-upper', 'skills')['fingerprint'] == preview['fingerprint']
    with pytest.raises(ValueError, match='当前教学学期'):
        repair_preview(service, 'bnu24-math-g8-upper', 'skills', [6])
    if damaged_question:
        from backend.jobs.manager import JobContext
        from backend.jobs.store import JobStore
        from backend.jobs.question_bank_repair import run_question_bank_repair_job
        store = JobStore(tmp_path / 'TEST-damaged-repair.db')
        record = store.create_job('question_bank_repair', {
            'question_ids': [item['id'] for item in preview['items']], 'kind': 'all',
            'curriculum_volume_id': 'bnu24-math-g8-upper',
            'revisions': {str(item['id']): item['revision'] for item in preview['items']},
        })
        calls = []
        def tagging(**kwargs):
            calls.append(kwargs['context'].payload['question_ids'])
        def no_links(**kwargs):
            pytest.fail('unreadable or incomplete evidence must not request skill links')
        result = run_question_bank_repair_job(
            context=JobContext(record.id, record.job_type, record.payload, store),
            question_bank_db_path=db, data_root=tmp_path, tagging_runner=tagging,
            link_runner=no_links, ai_service_factory=lambda: None, link_gateway_factory=lambda: None)
        assert calls == [[3, 5]]
        assert result['requested_count'] == 3
        assert result['completed_count'] == 0
        assert '文件损坏' in next(item for item in result['remaining'] if item['id'] == 4)['reason']


def _seed_tagged_question(db):
    """Give question 1 a complete tag set and pin its evidence created_at
    before the tag-item timestamps used by the currentness tests."""
    from question_bank.database.schema import connect
    with connect(db) as conn:
        conn.executemany(
            "INSERT INTO question_tags(question_id,tag_type,tag_value) VALUES(1,?,?)",
            [('ability', 'TEST-能力'), ('knowledge_point', 'TEST-知识点'),
             ('exam_scope', 'TEST-范围'), ('special_type', 'TEST-旧类型')],
        )
        conn.execute(
            "UPDATE question_solution_evidence_versions "
            "SET created_at='2026-01-01 00:00:00' WHERE question_id=1"
        )


def _insert_tag_run(db, stored_hash, *, contract,
                    item_updated_at='2026-01-02 00:00:00'):
    """Record one succeeded tag item for question 1 under ``contract``."""
    from question_bank.database.schema import connect
    operation_id = f'TEST-op-{contract}'
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO question_analysis_operations "
            "(operation_id,input_fingerprint,contract_version,"
            "requested_projection,status) VALUES(?,?,?,'both','succeeded')",
            (operation_id, 'a' * 64, contract),
        )
        conn.execute(
            "INSERT INTO question_analysis_items "
            "(operation_id,question_id,source_content_hash,tag_status,"
            "criteria_status,updated_at) VALUES(?,1,?,'succeeded','succeeded',?)",
            (operation_id, stored_hash, item_updated_at),
        )


def test_tag_source_currentness_tolerates_legacy_fingerprint_tag_writes(tmp_path):
    """combined-v2/tag-only-v1 rows mixed model-written special_type into the
    stored hash; a retag must not invalidate its own record."""
    from backend.jobs.question_bank_repair import repair_preview
    from backend.jobs.tagging_sync import _load_tag_source_currentness
    from question_bank.database.schema import connect
    from question_bank.training_criteria import (
        QuestionAnalysisInputLoader,
        legacy_tag_source_content_hash,
    )

    service, db, _ = _seed_skill_bank(tmp_path)
    loader = QuestionAnalysisInputLoader(db_path=db, data_root=tmp_path)

    def load_input():
        return loader.load([1], curriculum_volume_id='bnu24-math-g8-upper')[0]

    # Legacy contract: the stored hash was taken before a retag rewrote
    # special_type; the matching evidence version predates the tag item.
    _seed_tagged_question(db)
    before = load_input()
    legacy_hash = legacy_tag_source_content_hash(before)
    _insert_tag_run(db, legacy_hash, contract='combined-v2')
    with connect(db) as conn:
        conn.execute(
            "UPDATE question_tags SET tag_value='TEST-新类型' "
            "WHERE question_id=1 AND tag_type='special_type'"
        )
    current = load_input()
    assert legacy_tag_source_content_hash(current) != legacy_hash
    assert str(current.source_content_hash) != legacy_hash
    assert _load_tag_source_currentness(db, current_inputs=[current]) == {1: True}
    item = next(i for i in repair_preview(service, 'bnu24-math-g8-upper', 'all')['items'] if i['id'] == 1)
    assert 'tags' not in item['missing']

    # Legacy contract + content change: the evidence witness no longer
    # matches, so the stale tag run must be reported again.
    with connect(db) as conn:
        conn.execute("UPDATE questions SET question_text='TEST-题面已变' WHERE id=1")
    changed = load_input()
    assert _load_tag_source_currentness(db, current_inputs=[changed]) == {1: False}
    item = next(i for i in repair_preview(service, 'bnu24-math-g8-upper', 'all')['items'] if i['id'] == 1)
    assert 'tags' in item['missing']


@pytest.mark.parametrize('stored_hash_kind', ['current', 'legacy_without_tags'])
def test_tag_source_currentness_new_hash_ignores_model_tag_writes(tmp_path, stored_hash_kind):
    from backend.jobs.question_bank_repair import repair_preview
    from backend.jobs.tagging_sync import _load_tag_source_currentness
    from question_bank.database.schema import connect
    from question_bank.training_criteria import QuestionAnalysisInputLoader
    from question_bank.training_criteria.analysis import _legacy_tag_source_content_hash

    service, db, _ = _seed_skill_bank(tmp_path)
    loader = QuestionAnalysisInputLoader(db_path=db, data_root=tmp_path)

    def load_input():
        return loader.load([1], curriculum_volume_id='bnu24-math-g8-upper')[0]

    _seed_tagged_question(db)
    question = load_input()
    stored_hash = (question.source_content_hash if stored_hash_kind == 'current' else
        _legacy_tag_source_content_hash(question, include_tags=False))
    _insert_tag_run(db, str(stored_hash),
                    contract='combined-v2')
    with connect(db) as conn:
        conn.execute('UPDATE question_solution_evidence_versions SET source_content_hash=? WHERE question_id=1',
            ('0' * 64,))
        conn.execute(
            "UPDATE question_tags SET tag_value='TEST-新类型' "
            "WHERE question_id=1 AND tag_type='special_type'"
        )
    assert _load_tag_source_currentness(db, current_inputs=[load_input()]) == {1: True}

    with connect(db) as conn:
        conn.execute("UPDATE questions SET question_text='TEST-题面再变' WHERE id=1")
    assert _load_tag_source_currentness(db, current_inputs=[load_input()]) == {1: False}
    item = next(i for i in repair_preview(service, 'bnu24-math-g8-upper', 'all')['items'] if i['id'] == 1)
    assert 'tags' in item['missing']


@pytest.mark.parametrize("question_count", [3, 9])
def test_analysis_loader_isolates_damaged_content_and_keeps_legacy_images(tmp_path, question_count):
    from base64 import b64decode
    from docx import Document
    from question_bank.database.schema import connect
    from question_bank.services.rich_content_service import RichContentReadError
    from question_bank.training_criteria import QuestionAnalysisInputLoader

    _, db, _ = _seed_skill_bank(tmp_path, count=question_count)
    figure = tmp_path / 'question_bank/extracted_images/TEST-legacy.png'
    figure.parent.mkdir(parents=True)
    figure.write_bytes(b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6rCEAAAAASUVORK5CYII='))
    xml = Document().add_paragraph('TEST-历史公式正文')._p.xml
    rich_root = tmp_path / 'question_bank/rich_content'
    rich_root.mkdir(parents=True)
    payload = {'version': 'legacy', 'question_id': 1, 'question_blocks': [{
        'text': 'TEST-历史公式正文', 'xml': xml,
        'image_relationships': {'rId1': str(figure)},
    }], 'answer_blocks': []}
    sidecar = rich_root / 'question_1.json'
    sidecar.write_text(json.dumps(payload), encoding='utf-8')
    original = sidecar.read_bytes()
    (rich_root / 'question_2.json').write_text('{TEST-corrupt', encoding='utf-8')
    with connect(db) as connection:
        connection.execute("UPDATE questions SET question_text='' WHERE id=3")

    loader = QuestionAnalysisInputLoader(db_path=db, data_root=tmp_path)
    requested = tuple(reversed(range(1, question_count + 1)))
    failures = {}
    actual = loader.load(requested, load_failures=failures)
    assert [item.question_id for item in actual] == [qid for qid in requested if qid not in (2, 3)]
    assert failures == {2: 'rich_content_invalid_json', 3: 'question_text_unavailable'}
    legacy = next(item for item in actual if item.question_id == 1)
    assert legacy.rich_question_blocks == ({'text': 'TEST-历史公式正文'},)
    assert legacy.word_question_blocks[0]['xml'] == xml.strip()
    assert legacy.images[0].content == figure.read_bytes()
    assert legacy.word_question_blocks[0]['image_relationships'] == {
        'rId1': f'sha256:{legacy.images[0].sha256}',
    }
    assert sidecar.read_bytes() == original
    with pytest.raises(RichContentReadError):
        loader.load((1, 2))
    missing_failures = {}
    assert loader.load((99,), load_failures=missing_failures) == ()
    assert missing_failures == {99: 'question_not_found'}
    with pytest.raises(KeyError):
        loader.load((99,))


def test_analysis_loader_uses_external_read_transaction_for_evidence(tmp_path, monkeypatch):
    from question_bank.solution_evidence import repository
    from question_bank.training_criteria import QuestionAnalysisInputLoader

    _, db, _ = _seed_skill_bank(tmp_path, count=2)

    def disallow_implicit_connection(*args, **kwargs):
        pytest.fail('TEST evidence reader opened a separate writable connection')

    monkeypatch.setattr(repository, 'connect', disallow_implicit_connection)
    connection = sqlite3.connect(f'{db.as_uri()}?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('BEGIN')
        loaded = QuestionAnalysisInputLoader(db_path=db, data_root=tmp_path,
            external_connection=connection).load((1, 2))
        assert loaded[0].tagging_context.evidence_parts == ({
            'part_id': 'part-1', 'part_label': '', 'evidence_point_ids': ['p1', 'p2'],
        },)
        assert connection.in_transaction
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            connection.execute("UPDATE questions SET question_text='TEST' WHERE id=1")
    finally:
        connection.close()


def test_repair_preview_whole_volume_result_is_cached_until_a_write(tmp_path, monkeypatch):
    from backend.jobs import question_bank_repair as repair
    from question_bank.database.schema import connect

    service, db, _ = _seed_skill_bank(tmp_path)
    calls = []
    original = repair._load_analysis_gaps

    def tracked(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(repair, '_load_analysis_gaps', tracked)
    first = repair.repair_preview(service, 'bnu24-math-g8-upper', 'all')
    second = repair.repair_preview(service, 'bnu24-math-g8-upper', 'all')
    assert len(calls) == 1 and second == first
    # The returned copy must not alias the cached payload.
    second['items'].clear()
    second['counts']['skills'] = 999
    third = repair.repair_preview(service, 'bnu24-math-g8-upper', 'all')
    assert len(calls) == 1 and third == first
    # An explicit subset bypasses the whole-volume cache.
    repair.repair_preview(service, 'bnu24-math-g8-upper', 'all', [3, 4])
    assert len(calls) == 2
    with connect(db) as conn:
        conn.execute("UPDATE questions SET answer_text='TEST-新答案' WHERE id=4")
    fourth = repair.repair_preview(service, 'bnu24-math-g8-upper', 'all')
    assert len(calls) == 3 and fourth['fingerprint'] != first['fingerprint']


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


def test_is_within_matches_pathlib_root_containment(tmp_path):
    from question_bank.services.file_cache import is_within

    root = (tmp_path / "TEST-root").resolve()
    cases = {
        root: True,
        root / "child": True,
        root / "child" / "leaf.png": True,
        root.parent / f"{root.name}2": False,
        root.parent / f"{root.name}2" / "leaf.png": False,
        root.parent / "other": False,
        tmp_path.parent.resolve() / f"{tmp_path.name}-outside": False,
    }
    for path, expected in cases.items():
        assert is_within(path, root) is expected
        assert is_within(path, root) == (path == root or path.is_relative_to(root))
    # Case differences follow the platform rule: normcase treats them like
    # WindowsPath's own comparison does.
    swapped = root / "child".swapcase()
    assert is_within(swapped, root) == (swapped == root or swapped.is_relative_to(root))
    # A drive root ends in a separator already; children still match without
    # a doubled separator in the prefix.
    volume_root = Path(os.path.abspath(os.sep))
    volume_child = volume_root / "TEST-volume-child"
    assert is_within(volume_root, volume_root)
    assert is_within(volume_child, volume_root) is True
    assert volume_child.is_relative_to(volume_root)


def _prewarm_worker(db: Path, tmp_path: Path):
    from integration.training_prewarm import TrainingPrewarmWorker, clear_recent_requests
    clear_recent_requests()
    grading_db = tmp_path / "TEST-grading.db"
    _seed_paper(grading_db)
    paths = SimpleNamespace(db_path=grading_db, qb_db_path=db, data_root=tmp_path)
    jobs = SimpleNamespace(list=lambda **kwargs: ([], 0), is_shutdown=False)
    return TrainingPrewarmWorker(paths, jobs, foreground_quiet_seconds=0)


def test_question_bank_browse_prewarm_follows_current_scope_and_replans_on_write(tmp_path, monkeypatch):
    from integration.data_generation import commit_generation
    from integration.training_prewarm import clear_recent_requests, record_request

    _service, db, _keys = _seed_skill_bank(tmp_path)
    worker = _prewarm_worker(db, tmp_path)
    monkeypatch.setattr(worker, '_latest_volume_id', lambda: 'bnu24-math-g8-upper')
    monkeypatch.setattr(worker, '_startup_tasks', lambda seen: [])
    warmed = []
    monkeypatch.setattr(worker, '_warm_question_bank', warmed.append)
    plan = worker._refresh_plan()
    assert plan
    plan[0]()
    assert warmed == [commit_generation(db)]
    for task in plan[1:]:
        task()
    # An unchanged generation does not plan the browse task again.
    worker._seen_qb_browse_generation = warmed[0]
    warmed.clear()
    for task in worker._refresh_plan():
        task()
    assert warmed == []
    # With no recent scope, a question-bank write still plans browse first.
    with sqlite3.connect(db) as writer:
        writer.execute("UPDATE papers SET title='TEST-replan' WHERE id=1")
        writer.commit()
    plan = worker._refresh_plan()
    plan[0]()
    assert warmed == [commit_generation(db)]
    # When a teacher has used a scope, finish its full grouping first, then
    # browse, and only then spend time on older scopes.
    planned = []
    monkeypatch.setattr(worker, '_compute', lambda kind, scope, exams, params:
                        planned.append((kind, scope)))
    monkeypatch.setattr(worker, '_warm_question_bank', lambda generation:
                        planned.append(('browse', generation)))
    exams = {'mode': 'semester', 'curriculum_volume_id': 'bnu24-math-g8-upper'}
    record_request('diagnosis', scope={'mode': 'all'}, exam_scope=exams, params={})
    current_scope = {'mode': 'class', 'class_ids': ['TEST-current']}
    record_request('grouped_diagnosis', scope=current_scope, exam_scope=exams,
                   params={'grouping': {'scope_keys': ['kp_TEST-chapter']}})
    for task in worker._refresh_plan():
        task()
    assert planned == [('diagnosis', current_scope), ('grouped_diagnosis', current_scope),
                       ('browse', commit_generation(db)), ('diagnosis', {'mode': 'all'})]
    clear_recent_requests()
    # A failed run leaves the generation unseen, so the next plan built for
    # any trigger re-plans the browse task.
    worker._seen_qb_browse_generation = None
    warmed.clear()
    def fail(_generation):
        raise RuntimeError('TEST-prewarm-failed')
    monkeypatch.setattr(worker, '_warm_question_bank', fail)
    plan = worker._refresh_plan()
    with pytest.raises(RuntimeError, match='TEST-prewarm-failed'):
        plan[0]()
    assert worker._seen_qb_browse_generation is None
    monkeypatch.setattr(worker, '_warm_question_bank', warmed.append)
    plan = worker._refresh_plan()
    plan[0]()
    assert warmed == [commit_generation(db)]


def test_question_bank_browse_warm_uses_the_first_skill_page_filters(tmp_path, monkeypatch):
    _service, db, _keys = _seed_skill_bank(tmp_path)
    worker = _prewarm_worker(db, tmp_path)
    volume = 'bnu24-math-g8-upper'
    monkeypatch.setattr(worker, '_latest_volume_id', lambda: volume)
    calls = []
    index = {'chapters': [{'id': 'c1', 'sections': [
        {'id': 's0', 'skills': []},
        {'id': 's1', 'skills': [{'stable_key': 'sk_TEST_first'}]},
    ]}]}
    def recording_index(self, volume_id):
        calls.append(('skill_index', volume_id))
        return index
    def recording_list(self, filters):
        calls.append(('list_questions', filters))
    def recording_facets(self, filters):
        calls.append(('list_facets', filters))
    monkeypatch.setattr(QuestionBankReadService, 'skill_index', recording_index)
    monkeypatch.setattr(QuestionBankReadService, 'list_questions', recording_list)
    monkeypatch.setattr(QuestionBankReadService, 'list_facets', recording_facets)
    worker._warm_question_bank(7)
    assert worker._seen_qb_browse_generation == 7
    assert [call[0] for call in calls] == ['skill_index', 'list_questions', 'list_facets']
    assert calls[0][1] == volume
    # Same filters the /questions route builds for the page's first request.
    assert calls[1][1] == QuestionReadFilters(
        skill_keys=('sk_TEST_first',), include_skills=True, page=1, page_size=20,
        difficulty_min=1, difficulty_max=10, curriculum_volume_ids=(volume,),
        collapse_duplicates=True, scope_mode='primary', sort='newest',
    )
    # Same filters the /facets route builds for it.
    assert calls[2][1] == QuestionReadFilters(
        skill_keys=('sk_TEST_first',), difficulty_min=1, difficulty_max=10,
        curriculum_volume_ids=(volume,), collapse_duplicates=True,
        scope_mode='primary',
    )
    # Without a resolvable volume there is nothing to warm; the generation is
    # still marked so an empty bank is not re-scanned every tick.
    calls.clear()
    monkeypatch.setattr(worker, '_latest_volume_id', lambda: None)
    worker._warm_question_bank(9)
    assert worker._seen_qb_browse_generation == 9
    assert calls == []


def _spy_current_inputs(monkeypatch):
    """Record the question ids whose content files are actually read."""
    from question_bank.solution_evidence import part_assessments
    loaded: list[int] = []
    original = part_assessments.current_inputs

    def spy(db_path, ids, connection, *, data_root=None, load_failures=None):
        loaded.extend(int(value) for value in ids)
        return original(db_path, ids, connection, data_root=data_root, load_failures=load_failures)

    monkeypatch.setattr(part_assessments, 'current_inputs', spy)
    return loaded


def _fresh_skill_snapshot(db: Path, root: Path):
    """A full build without any memo — the reference result for each case."""
    from question_bank.database.schema import connect
    from question_bank.services.question_skill_index import build_skill_snapshot
    with connect(db) as conn:
        return build_skill_snapshot(conn, db, root)


def test_skill_snapshot_rebuild_reloads_only_changed_question_inputs(tmp_path, monkeypatch):
    """数据库写入触发完整重建，但只有内容字段变化的题目重新读取文件。"""
    from question_bank.database.schema import connect
    from question_bank.services.file_cache import clear_file_caches
    from question_bank.training_criteria import (
        QuestionAnalysisInputLoader, solution_evidence_source_content_hash,
    )

    service, db, _keys = _seed_skill_bank(tmp_path)
    loaded = _spy_current_inputs(monkeypatch)
    # 3 号题暂无可用证据，6 号题已删除；其余四题都有内容校验存项。
    usable = {1, 2, 4, 5}
    release = None
    with connect(db) as conn:
        release = conn.execute(
            "SELECT release_id FROM knowledge_graph_releases WHERE status='active'").fetchone()[0]

    def insert_evidence(question_id: int, input_obj) -> None:
        payload = {"parts": [{"part_id": "part-1", "evidence_points": [
            {"evidence_point_id": "p1", "target": "列式"}]}]}
        version = f'TEST-v{question_id}'.ljust(64, '0')
        with connect(db) as conn:
            conn.execute("INSERT INTO question_solution_evidence_versions(evidence_version_id,question_id,source_content_hash,schema_version,content_hash,evidence_json,status,source_kind,source_reference,created_by,graph_release_id) "
                         "VALUES(?,?,?,'question-solution-evidence-v2',?,?,'approved','combined_model','TEST','TEST',?)",
                         (version, question_id, solution_evidence_source_content_hash(input_obj),
                          version, json.dumps(payload), release))

    def rebuild():
        loaded.clear()
        return service._skill_snapshot(), list(loaded)

    # 首次构建没有任何存项，全部读取并写入备忘录。
    expected = _fresh_skill_snapshot(db, tmp_path)
    snapshot, ids = rebuild()
    assert snapshot == expected
    assert set(ids) == usable

    # 改写题面：只有该题重新读取。
    with connect(db) as conn:
        conn.execute("UPDATE questions SET question_text='TEST-改写题面' WHERE id=1")
    expected = _fresh_skill_snapshot(db, tmp_path)
    snapshot, ids = rebuild()
    assert snapshot == expected and set(ids) == {1}

    # 新增可用证据版本：该题没有已存校验项，只读取它。
    q3 = QuestionAnalysisInputLoader(db_path=db, data_root=tmp_path).load([3])[0]
    insert_evidence(3, q3)
    usable.add(3)
    expected = _fresh_skill_snapshot(db, tmp_path)
    snapshot, ids = rebuild()
    assert snapshot == expected and set(ids) == {3}

    # 链接行变化不属于题面输入：不读任何文件，结果仍与全新构建一致。
    with connect(db) as conn:
        conn.execute("UPDATE evidence_point_knowledge_links SET role='supporting_prerequisite' "
                     "WHERE question_id=1 AND role='direct'")
    expected = _fresh_skill_snapshot(db, tmp_path)
    snapshot, ids = rebuild()
    assert snapshot == expected and ids == []

    # 删除的题不再读取，其存项也被裁掉。
    with connect(db) as conn:
        conn.execute("UPDATE questions SET is_deleted=1 WHERE id=2")
    usable.discard(2)
    expected = _fresh_skill_snapshot(db, tmp_path)
    snapshot, ids = rebuild()
    assert snapshot == expected and ids == []
    memo = read_module._SKILL_SOURCE_MEMO[service._cache_data_root]
    assert 2 not in memo["entries"]
    assert set(memo["entries"]) <= usable

    # 新题没有存项，只读取它。
    with connect(db) as conn:
        conn.execute("INSERT INTO questions(id,paper_id,question_number,question_text,question_type) "
                     "VALUES(7,1,'7','TEST-新增题','解答题')")
    insert_evidence(7, QuestionAnalysisInputLoader(db_path=db, data_root=tmp_path).load([7])[0])
    usable.add(7)
    expected = _fresh_skill_snapshot(db, tmp_path)
    snapshot, ids = rebuild()
    assert snapshot == expected and set(ids) == {7}

    # 素材清单变化使整份校验作废：全部重新读取。文件侧改动不改变数据库
    # 世代号，与重启后的首次读取一样先清掉进程内缓存再比对。
    sidecar = tmp_path / 'question_bank/rich_content/question_1.json'
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps({'version': 3, 'question_blocks': [
        {'type': 'text', 'text': 'TEST-改写富文本'}]}), encoding='utf-8')
    read_module._READ_RESULT_CACHE.clear()
    clear_file_caches()
    expected = _fresh_skill_snapshot(db, tmp_path)
    snapshot, ids = rebuild()
    assert snapshot == expected and set(ids) == usable

    # 历史题型别名只依赖已存散列：匹配旧类型的证据仍能复用，无需重读。
    from dataclasses import replace
    q4 = QuestionAnalysisInputLoader(db_path=db, data_root=tmp_path).load([4])[0]
    legacy_hash = solution_evidence_source_content_hash(
        replace(q4, tagging_context=replace(q4.tagging_context, question_type='解答题（计算）')))
    with connect(db) as conn:
        conn.execute("UPDATE question_solution_evidence_versions SET source_content_hash=? "
                     "WHERE question_id=4", (legacy_hash,))
    expected = _fresh_skill_snapshot(db, tmp_path)
    snapshot, ids = rebuild()
    assert snapshot == expected and ids == []
    assert 4 in snapshot['by_question'] and 4 not in snapshot['no_usable']

    # 重启（内存备忘录与读缓存清空）后，持久化校验项让改动题之外的输入不重读。
    read_module._SKILL_SOURCE_MEMO.clear()
    read_module._READ_RESULT_CACHE.clear()
    clear_file_caches()
    restarted = QuestionBankReadService(db, data_root=tmp_path)
    with connect(db) as conn:
        conn.execute("UPDATE questions SET question_text='TEST-重启后改写' WHERE id=5")
    expected = _fresh_skill_snapshot(db, tmp_path)
    loaded.clear()
    snapshot = restarted._skill_snapshot()
    assert snapshot == expected and set(loaded) == {5}
