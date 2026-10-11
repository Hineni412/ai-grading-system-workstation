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


def test_cache_values_preserves_nested_shapes_and_mixed_type_order():
    from integration.result_cache import cache_values

    opaque = object()
    mutable_atom = bytearray(b"TEST")
    mixed_set = {None, "TEST", 3, (1, 2)}
    value = {
        None: (b"TEST", 1 + 2j),
        3: {2: mixed_set, "TEST-inner": [1, ("TEST",)]},
        ("TEST-key",): frozenset(mixed_set),
        "TEST-z": [True, None, 2.5, opaque, mutable_atom],
    }
    expected = (
        ("TEST-z", (True, None, 2.5, opaque, mutable_atom)),
        (("TEST-key",), ("TEST", (1, 2), 3, None)),
        (3, (("TEST-inner", (1, ("TEST",))), (2, ("TEST", (1, 2), 3, None)))),
        (None, (b"TEST", 1 + 2j)),
    )
    assert cache_values(value) == expected
    assert cache_values(dict(reversed(list(value.items())))) == expected
    assert cache_values(opaque) is opaque
    assert cache_values(mutable_atom) is mutable_atom
    assert cache_values([1, (2, 3)]) == cache_values((1, [2, 3])) == (1, (2, 3))
    assert cache_values({"TEST": True}) == cache_values({"TEST": 1})


def test_cache_values_shared_children_are_rechecked_on_each_ordinary_call():
    from integration.result_cache import cache_values

    child = {"TEST-points": [1, {"TEST-level": 2}], "TEST-set": {2, 1}}
    value = [child, child, (child,)]
    child_key = (("TEST-points", (1, (("TEST-level", 2),))), ("TEST-set", (1, 2)))
    original = cache_values(value)
    assert original == (child_key, child_key, (child_key,))
    child["TEST-points"][1]["TEST-level"] = 4
    child["TEST-points"].append(3)
    child["TEST-set"].add(3)
    changed_key = (("TEST-points", (1, (("TEST-level", 4),), 3)), ("TEST-set", (1, 2, 3)))
    assert cache_values(value) == (changed_key, changed_key, (changed_key,))
    assert original == (child_key, child_key, (child_key,))


def test_cache_values_external_memo_caches_only_the_requested_outer_object():
    from integration.result_cache import cache_values

    child = [1]
    value = {"TEST": child}
    memo = {}
    original = cache_values(value, _memo=memo)
    assert original == (("TEST", (1,)),)
    assert set(memo) == {id(value)}
    assert memo[id(value)][0] is value and memo[id(value)][1] is original
    child.append(2)
    assert cache_values(value, _memo=memo) is original
    assert cache_values(value) == (("TEST", (1, 2)),)
    next_value = [child]
    assert cache_values(next_value, _memo=memo) == ((1, 2),)
    assert set(memo) == {id(value), id(next_value)}
    memo[id(child)] = (child, ("TEST-stale-child",))
    another_value = {"TEST-next": child}
    assert cache_values(another_value, _memo=memo) == (("TEST-next", (1, 2)),)
    memo[id(another_value)] = (value, ("TEST-wrong-object",))
    assert cache_values(another_value, _memo=memo) == (("TEST-next", (1, 2)),)


def test_cache_values_keeps_custom_iteration_and_opaque_types():
    from integration.result_cache import cache_values

    class CustomMapping(dict):
        def items(self):
            return [(3, [3]), ("TEST-first", [1])]

    class CustomSequence(list):
        def __iter__(self):
            return iter((3, {"TEST": [1]}))

    class CustomTuple(tuple):
        def __iter__(self):
            return iter((3, {"TEST": [1]}))

    class CustomSet(set):
        def __iter__(self):
            return iter((3, "TEST", (1, 2)))

    class CustomFrozenSet(frozenset):
        def __iter__(self):
            return iter((3, "TEST", (1, 2)))

    class UnhashableType(type):
        __hash__ = None

        def __eq__(self, other):
            raise AssertionError("TEST-type-equality-must-not-be-used")

    class Opaque(metaclass=UnhashableType):
        pass

    class CustomInt(int):
        pass

    assert cache_values(CustomMapping()) == (("TEST-first", (1,)), (3, (3,)))
    for value in (CustomSequence(), CustomTuple()):
        assert cache_values(value) == (3, (("TEST", (1,)),))
    for value in (CustomSet(), CustomFrozenSet()):
        assert cache_values(value) == ("TEST", (1, 2), 3)
    value = CustomInt(7)
    assert cache_values(value) is value
    with pytest.raises(TypeError, match="unhashable type"):
        cache_values(Opaque())


@pytest.mark.parametrize("custom_kind", ["mapping", "sequence"])
def test_cache_values_custom_iteration_can_change_an_earlier_shared_child(custom_kind):
    from collections import UserDict
    from integration.result_cache import cache_values

    child = [1]

    class MutatingMapping(UserDict):
        def items(self):
            child.append(2)
            return super().items()

    class MutatingSequence(list):
        def __iter__(self):
            child.append(2)
            return super().__iter__()

    if custom_kind == "mapping":
        custom = MutatingMapping({"TEST": 0})
        custom_key = (("TEST", 0),)
    else:
        custom = MutatingSequence(["TEST"])
        custom_key = ("TEST",)
    assert cache_values([child, custom, child, custom, child]) == (
        (1,), custom_key, (1, 2), custom_key, (1, 2, 2),
    )


@pytest.mark.parametrize("container_kind", ["mapping", "set", "frozenset"])
@pytest.mark.parametrize("tuple_key", [False, True])
def test_cache_values_sorting_repr_can_change_an_earlier_shared_child(container_kind, tuple_key):
    from integration.result_cache import cache_values

    child = [1]

    class MutatingKey:
        def __repr__(self):
            child.append(2)
            return "TEST-key"

    key = MutatingKey()
    if tuple_key:
        key = (key,)
    if container_kind == "mapping":
        container = {key: "TEST"}
        container_key = ((key, "TEST"),)
    else:
        container = {key} if container_kind == "set" else frozenset({key})
        container_key = (key,)
    assert cache_values([child, container, child]) == ((1,), container_key, (1, 2))


@pytest.mark.parametrize("container_kind", ["mapping", "sequence"])
def test_cache_values_retains_recursive_input_errors_without_external_memo_entries(container_kind):
    from integration.result_cache import cache_values

    if container_kind == "mapping":
        value = {}
        value["TEST"] = value
    else:
        value = []
        value.append(value)
    memo = {}
    with pytest.raises(RecursionError):
        cache_values(value, _memo=memo)
    assert memo == {}


def test_cache_values_retains_custom_mapping_errors():
    from integration.result_cache import cache_values

    class BrokenMapping(dict):
        def items(self):
            raise ValueError("TEST-items-failed")

    memo = {}
    with pytest.raises(ValueError, match="TEST-items-failed"):
        cache_values([{"TEST": [1]}, BrokenMapping()], _memo=memo)
    assert memo == {}


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


def _seed_skill_bank(tmp_path: Path, count: int = 6, *, taxonomy_revision: int = 7):
    from question_bank.database.schema import connect
    from question_bank.training_criteria import QuestionAnalysisInputLoader, solution_evidence_source_content_hash

    db = tmp_path / "TEST-skill-index.db"
    _seed_paper(db)
    from tests.current_knowledge_support import install_current_knowledge
    install_current_knowledge(db, taxonomy_revision=taxonomy_revision)
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
            # 查看与统计沿用已有证据，题面变化不清空已存技能关联。
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
    assert status["unlinked_skill_count"] == sum(index["unlinked"].values()) == 2
    with connect(db) as conn:
        conn.execute("UPDATE grading_question_links SET status='suggested' WHERE bank_question_id=3")
    assert service.session_analysis_status(7)["unlinked_skill_count"] == 1
    assert service.session_analysis_status(999)["unlinked_skill_count"] == 0


def test_skill_index_current_versions_legacy_links_filters_and_cache(tmp_path, monkeypatch):
    from question_bank.database.schema import connect

    service, db, keys = _seed_skill_bank(tmp_path)
    index = service.skill_index('bnu24-math-g8-upper')
    skills = {row['stable_key']: row for chapter in index['chapters']
              for section in chapter['sections'] for row in section['skills']}
    assert index['question_count'] == 5
    assert index['unlinked'] == {'no_usable_evidence': 1, 'no_skill_link': 1}
    from question_bank.services.knowledge_order import question_primary_skills, skill_placements
    with connect(db) as conn:
        assert question_primary_skills(conn, db, tmp_path, [1])[1] == keys[0]
        placement = skill_placements(conn, keys, 'bnu24-math-g8-upper')[keys[0]]
        assert placement.section_id and placement.chapter_id and placement.skill_name
    assert [skills[key]['question_count'] for key in keys] == [3, 1, 0]
    assert skills[keys[0]]['difficulty'] == {'min': 2.0, 'median': 3.0, 'max': 6.0}
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
    assert {item['id'] for item in service.list_questions(QuestionReadFilters(skill_unlinked=True)).items} == {3, 4}
    assert 'skills' not in service.list_questions(QuestionReadFilters()).items[0]
    assert service.list_papers()[0]['skill_unlinked_question_count'] == 2
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
    assert service.list_questions(QuestionReadFilters(skill_keys=(keys[0],))).total == 2
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
        # 归档后的落盘快照按 materials 版本校验仍然有效，直接恢复。
        assert restarted.skill_index('bnu24-math-g8-upper') == index
        assert len(builds) == before_checkpoint
    process = subprocess.run([sys.executable, '-c', script, str(service.db_path), str(tmp_path)],
                             capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == expected_digest
    # 版本覆盖下，资产文件只能随题库表写入变化；单独增删改文件不再失效快照。
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
        assert len(builds) == count_before
        assert actual == index
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
        current_source_content_hash=question.source_content_hash)['evidence_version_id'] == 'd' * 64
    from question_bank.solution_evidence.part_assessments import load_profiles
    assert load_profiles(db, [4], data_root=tmp_path, verify_source=True)[4]['evidence_version_id'] == version
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
    assert [item['id'] for item in preview['items']] == [3, 4]
    # Question 5 still has saved usable evidence and links after its text changes.
    assert preview['model_calls'] == 0
    assert preview['counts']['skills'] == 2
    assert preview['counts']['evidence'] == 1
    if damaged_question:
        assert 'evidence' not in preview['items'][1]['missing']  # Saved evidence is readable.
        assert preview['items'][1]['blocked_reason_code'] == 'rich_content_invalid_json'
        assert '文件损坏' in preview['items'][1]['blocked_reason']
        assert preview['repairable_count'] == 1
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
        assert calls == [[3]]
        assert result['requested_count'] == 2
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


def test_repair_preview_builds_one_taxonomy_governance_for_multi_batch_gaps(tmp_path, monkeypatch):
    """Gaps spanning several 64-question batches share one governance and one
    taxonomy plan; per-question planning produces the identical preview."""
    from backend.jobs import question_bank_repair as repair
    service, db, _ = _seed_skill_bank(tmp_path, count=70)
    governances, plans = [], []
    real_governance, real_plan = repair.TaxonomyGovernance, repair._plan_taxonomy

    def counted_governance(*args, **kwargs):
        governances.append(True)
        return real_governance(*args, **kwargs)

    def counted_plan(runner, governance, *, contexts):
        plans.append(contexts)
        return real_plan(runner, governance, contexts=contexts)

    monkeypatch.setattr(repair, 'TaxonomyGovernance', counted_governance)
    monkeypatch.setattr(repair, '_plan_taxonomy', counted_plan)
    preview = repair._compute_repair_preview(service, 'bnu24-math-g8-upper', 'all')
    pending = {item['id'] for item in preview['items']}
    assert len(pending) > 64  # Classification spans several 64-question batches.
    assert governances == [True]
    assert len(plans) == 1
    assert set(plans[0]) == {item['id'] for item in preview['items'] if 'types' in item['missing']}

    def split_plan(runner, governance, *, contexts):
        merged, revision = {}, None
        for qid, context in contexts.items():
            contracts, revision = real_plan(runner, governance, contexts={qid: context})
            merged.update(contracts)
        return merged, revision

    monkeypatch.setattr(repair, '_plan_taxonomy', split_plan)
    assert repair._compute_repair_preview(service, 'bnu24-math-g8-upper', 'all') == preview


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

    # Legacy contract + content change: the write-boundary currentness check
    # still reports the drift, while the preview keeps reading saved tags.
    with connect(db) as conn:
        conn.execute("UPDATE questions SET question_text='TEST-题面已变' WHERE id=1")
    changed = load_input()
    assert _load_tag_source_currentness(db, current_inputs=[changed]) == {1: False}
    item = next(i for i in repair_preview(service, 'bnu24-math-g8-upper', 'all')['items'] if i['id'] == 1)
    assert 'tags' not in item['missing']


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
    assert 'tags' not in item['missing']


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
    service._skill_snapshot()
    read_module._READ_RESULT_CACHE.clear()
    restored = []
    original_restore = service._read_local_skill_snapshot

    def restore(*args, **kwargs):
        result = original_restore(*args, **kwargs)
        restored.append(result is not None)
        return result

    monkeypatch.setattr(service, '_read_local_skill_snapshot', restore)
    calls = []
    original = repair._compute_repair_preview

    def tracked(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(repair, '_compute_repair_preview', tracked)
    first = repair.repair_preview(service, 'bnu24-math-g8-upper', 'all')
    second = repair.repair_preview(service, 'bnu24-math-g8-upper', 'all')
    assert len(calls) == 1 and second == first
    assert restored == [True]
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
    # File edits can change readiness without a database write.
    # 版本覆盖下，资产文件只能随题库表写入变化；文件本身的改动不再使预览失效。
    sidecar = tmp_path / 'question_bank/rich_content/question_1.json'
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text('{TEST-corrupt', encoding='utf-8')
    changed = repair.repair_preview(service, 'bnu24-math-g8-upper', 'all')
    assert len(calls) == 3 and changed == fourth


def test_analysis_material_cache_reuses_pages_and_rechecks_database_and_assets(tmp_path, monkeypatch):
    from question_bank.database.schema import connect
    from question_bank.solution_evidence import part_assessments

    service, db, _ = _seed_skill_bank(tmp_path)
    loaded = []
    original = part_assessments.load_profiles

    def track(db_path, ids, *args, **kwargs):
        loaded.append(tuple(ids))
        return original(db_path, ids, *args, **kwargs)

    monkeypatch.setattr(part_assessments, 'load_profiles', track)

    def material(ids):
        with read_module._read_connection(db) as conn:
            return service._analysis_materials(conn, ids)

    first = material([1, 2])
    assert first[1]['evidence_ready'] and first[2]['evidence_ready']
    assert material([1, 2, 4])[1] == first[1]
    assert loaded == [(1, 2), (4,)]
    first[1]['evidence_ready'] = False
    assert material([1])[1]['evidence_ready']
    with connect(db) as conn:
        conn.execute("UPDATE questions SET question_text='TEST-source-edited' WHERE id=1")
    assert material([1])[1]['evidence_ready']  # Saved evidence remains readable.
    sidecar = tmp_path / 'question_bank/rich_content/question_2.json'
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text('{TEST-corrupt', encoding='utf-8')
    assert material([2])[2]['evidence_ready']  # A damaged current stem does not hide saved evidence.
    assert loaded == [(1, 2), (4,), (1,), (2,)]


def test_saved_criterion_stays_ready_after_later_content_change(tmp_path):
    """题面后来变化不再让已通过的保存判定点回到缺失或需复核。"""
    from backend.jobs.question_bank_repair import repair_preview
    from question_bank.database.schema import connect
    from question_bank.training_criteria import QuestionAnalysisInputLoader, TrainingCriterionModule
    from tests.training.test_training_criterion_versioning import _draft

    service, db, _ = _seed_skill_bank(tmp_path)
    question = QuestionAnalysisInputLoader(db_path=db, data_root=tmp_path).load(
        [1], curriculum_volume_id='bnu24-math-g8-upper')[0]
    module = TrainingCriterionModule(db, data_root=tmp_path)
    proposed = module.propose(question=question, draft=_draft(question),
        source_kind='backfill', source_reference='TEST-criterion',
        actor_ref='TEST', reason='TEST')
    assert proposed['current_version']['status'] == 'proposed'
    # Direct writes keep the head's old source hash; only write-boundary
    # marking (covered by the write services) updates criterion state.
    with connect(db) as conn:
        conn.execute("UPDATE questions SET question_text='TEST-题面与已存判定点不同' WHERE id=1")
    preview = repair_preview(service, 'bnu24-math-g8-upper', 'all')
    item = next(i for i in preview['items'] if i['id'] == 1)
    assert 'criteria' not in item['missing']
    with read_module._read_connection(db) as conn:
        assert service._analysis_materials(conn, [1])[1] == {
            'evidence_ready': True, 'criteria_ready': True, 'criteria_needs_review': False}
        assert 1 not in service._criteria_needs_review_ids(conn, [1])


def test_stale_saved_criterion_counts_as_needs_review(tmp_path):
    """被写入边界标为失效的判定版本计入需复核，不再算就绪。"""
    from backend.jobs.question_bank_repair import repair_preview
    from question_bank.database.schema import connect
    from question_bank.training_criteria import QuestionAnalysisInputLoader, TrainingCriterionModule
    from tests.training.test_training_criterion_versioning import _draft

    service, db, _ = _seed_skill_bank(tmp_path)
    question = QuestionAnalysisInputLoader(db_path=db, data_root=tmp_path).load(
        [1], curriculum_volume_id='bnu24-math-g8-upper')[0]
    module = TrainingCriterionModule(db, data_root=tmp_path)
    module.propose(question=question, draft=_draft(question),
        source_kind='backfill', source_reference='TEST-criterion',
        actor_ref='TEST', reason='TEST')
    with connect(db) as conn:
        conn.execute("UPDATE training_criterion_versions SET status='stale' WHERE question_id=1")
    with read_module._read_connection(db) as conn:
        material = service._analysis_materials(conn, [1])[1]
        assert material['criteria_ready'] is False
        assert material['criteria_needs_review'] is True
        assert 1 in service._criteria_needs_review_ids(conn, [1])
    preview = repair_preview(service, 'bnu24-math-g8-upper', 'all')
    assert 'criteria' in next(i for i in preview['items'] if i['id'] == 1)['missing']


def test_skill_index_and_analysis_materials_never_hash_question_content(tmp_path, monkeypatch):
    """普通读取不装载题目输入，也不计算内容摘要。"""
    from question_bank.training_criteria import adapters, analysis

    service, db, _ = _seed_skill_bank(tmp_path)

    def forbid(*args, **kwargs):
        raise AssertionError('TEST-ordinary-read-touched-question-content')

    monkeypatch.setattr(analysis, 'question_content_hash', forbid)
    monkeypatch.setattr(adapters.QuestionAnalysisInputLoader, 'load', forbid)

    assert service.skill_index('bnu24-math-g8-upper')['question_count'] == 5
    with read_module._read_connection(db) as conn:
        materials = service._analysis_materials(conn, [1, 2, 3])
        assert service._criteria_needs_review_ids(conn, [1]) == set()
    assert materials[1]['evidence_ready'] and not materials[3]['evidence_ready']


@pytest.mark.parametrize('first_kind', ['all', 'analysis'])
def test_repair_preview_shares_scan_but_keeps_analysis_question_scope(tmp_path, monkeypatch, first_kind):
    from backend.jobs import question_bank_repair as repair

    service, _, _ = _seed_skill_bank(tmp_path)
    missing = [['types'], ['knowledge_points'], ['skills'],
               ['tags', 'types'], ['evidence', 'skills'], ['criteria']]
    items = [{'id': index, 'missing': parts, 'blocked_reason': ''}
             for index, parts in enumerate(missing, 1)]
    calls = []

    def compute(service, volume, kind, ids):
        calls.append(kind)
        return repair._preview(volume, kind, items, 20)

    monkeypatch.setattr(repair, '_compute_repair_preview', compute)
    repair.repair_preview(service, 'bnu24-math-g8-upper', first_kind)
    all_gaps = repair.repair_preview(service, 'bnu24-math-g8-upper', 'all')
    analysis = repair.repair_preview(service, 'bnu24-math-g8-upper', 'analysis')
    assert calls == ['all']
    assert all_gaps['question_count'] == 6 and analysis['question_count'] == 3
    assert analysis['scanned_count'] == 20
    assert [item['id'] for item in analysis['items']] == [4, 5, 6]
    assert analysis['items'][0]['missing'] == ['tags', 'types']
    assert analysis['fingerprint'] != all_gaps['fingerprint']
    analysis['items'][0]['missing'].clear()
    assert repair.repair_preview(service, 'bnu24-math-g8-upper', 'all') == all_gaps


@pytest.mark.parametrize('outcome', ['success', 'failure', 'source_changed'])
def test_repair_preview_concurrent_entry_points_share_one_scan(tmp_path, monkeypatch, outcome):
    import threading
    import integration.result_cache as cache_module
    from backend.jobs import question_bank_repair as repair
    from question_bank.database.schema import connect

    service, db, _ = _seed_skill_bank(tmp_path)
    started, waiting, release = (threading.Event() for _ in range(3))
    original_flight = cache_module._Flight

    class ObservedEvent:
        def __init__(self):
            self.event = threading.Event()

        def wait(self):
            waiting.set()
            assert self.event.wait(5)

        def set(self):
            self.event.set()

    def flight():
        result = original_flight()
        result.event = ObservedEvent()
        return result

    monkeypatch.setattr(cache_module, '_Flight', flight)
    calls = []

    def compute(service, volume, kind, ids):
        calls.append(kind)
        started.set()
        assert release.wait(5)
        if len(calls) == 1 and outcome == 'failure':
            raise ValueError('TEST-preview-failed')
        if len(calls) == 1 and outcome == 'source_changed':
            with connect(db) as conn:
                conn.execute("UPDATE questions SET answer_text='TEST-concurrent-edit' WHERE id=1")
        return repair._preview(volume, kind, [
            {'id': 1, 'missing': ['tags'], 'blocked_reason': ''},
            {'id': 2, 'missing': ['types'], 'blocked_reason': ''},
        ], 5)

    monkeypatch.setattr(repair, '_compute_repair_preview', compute)
    with ThreadPoolExecutor(max_workers=2) as pool:
        all_result = pool.submit(repair.repair_preview, service, 'bnu24-math-g8-upper', 'all')
        assert started.wait(5)
        analysis_result = pool.submit(repair.repair_preview, service, 'bnu24-math-g8-upper', 'analysis')
        try:
            assert waiting.wait(5)
        finally:
            release.set()
        if outcome == 'failure':
            for result in (all_result, analysis_result):
                with pytest.raises(ValueError, match='TEST-preview-failed'):
                    result.result(timeout=5)
        else:
            assert all_result.result(timeout=5)['question_count'] == 2
            assert analysis_result.result(timeout=5)['question_count'] == 1
    assert calls == ['all']
    assert not repair._REPAIR_PREVIEW_FLIGHTS._flights
    assert not repair._REPAIR_PREVIEW_FLIGHTS._entries
    if outcome != 'success':
        assert not any(key[0] == 'repair_preview' for key in read_module._READ_RESULT_CACHE)
    repair.repair_preview(service, 'bnu24-math-g8-upper', 'analysis')
    assert len(calls) == (1 if outcome == 'success' else 2)


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
    from integration.data_generation import commit_generation, data_version_token
    from integration.training_prewarm import clear_recent_requests, record_request

    def qb_token(path: Path):
        return (
            data_version_token(path, "materials", "training_records", "training_ops")
            or commit_generation(path)
        )

    _service, db, _keys = _seed_skill_bank(tmp_path)
    worker = _prewarm_worker(db, tmp_path)
    monkeypatch.setattr(worker, '_latest_volume_id', lambda: 'bnu24-math-g8-upper')
    monkeypatch.setattr(worker, '_startup_tasks', lambda seen: [])
    warmed = []
    monkeypatch.setattr(worker, '_warm_question_bank', warmed.append)
    plan = worker._refresh_plan()
    assert plan
    plan[0]()
    assert warmed == [qb_token(db)]
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
    assert warmed == [qb_token(db)]
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
                       ('browse', qb_token(db)), ('diagnosis', {'mode': 'all'})]
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
    assert warmed == [qb_token(db)]


@pytest.mark.parametrize(('revision', 'volume'), [
    (7, 'bnu24-math-g8-upper'),
    (11, 'bnu24-math-g8-upper'),
    (11, 'bnu24-math-g8-lower'),
])
def test_question_bank_browse_warm_uses_unfiltered_tag_page_filters(tmp_path, monkeypatch, revision, volume):
    from tests.current_knowledge_support import install_current_knowledge
    db = tmp_path / 'TEST-prewarm-tags.db'
    _seed_paper(db)
    install_current_knowledge(db, taxonomy_revision=revision)
    worker = _prewarm_worker(db, tmp_path)
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
    assert calls[1][1] == QuestionReadFilters(
        include_skills=True, page=1, page_size=20,
        difficulty_min=1, difficulty_max=10, curriculum_volume_ids=(volume,),
        collapse_duplicates=True, scope_mode='primary', sort='newest',
        tag_status='all', analysis_status='all',
    )
    assert calls[2][1] == QuestionReadFilters(
        difficulty_min=1, difficulty_max=10,
        curriculum_volume_ids=(volume,), collapse_duplicates=True,
        scope_mode='primary', tag_status='all', analysis_status='all',
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


def test_skill_snapshot_refreshes_saved_data_without_reading_current_inputs(tmp_path, monkeypatch):
    """统计随真实写入刷新，读取已有资料不校验当前题面与答案正文。"""
    from question_bank.database.schema import connect
    from question_bank.services.file_cache import clear_file_caches

    service, db, _keys = _seed_skill_bank(tmp_path)
    loaded = _spy_current_inputs(monkeypatch)
    with connect(db) as conn:
        release = conn.execute(
            "SELECT release_id FROM knowledge_graph_releases WHERE status='active'").fetchone()[0]

    def insert_evidence(question_id):
        payload = {"parts": [{"part_id": "part-1", "evidence_points": [
            {"evidence_point_id": "p1", "target": "列式"}]}]}
        identifier = f'TEST-v{question_id}'.ljust(64, '0')
        with connect(db) as conn:
            conn.execute("INSERT INTO question_solution_evidence_versions(evidence_version_id,question_id,source_content_hash,schema_version,content_hash,evidence_json,status,source_kind,source_reference,created_by,graph_release_id) "
                         "VALUES(?,?,?,'question-solution-evidence-v2',?,?,'approved','combined_model','TEST','TEST',?)",
                         (identifier, question_id, 'TEST-legacy-input'.ljust(64, '0'),
                          identifier, json.dumps(payload), release))

    def assert_refreshed():
        loaded.clear()
        expected = _fresh_skill_snapshot(db, tmp_path)
        snapshot = service._skill_snapshot()
        assert snapshot == expected and loaded == []
        return snapshot

    initial = assert_refreshed()
    assert 3 in initial['no_usable'] and 6 not in initial['by_question']
    assert service._skill_snapshot() == initial and loaded == []

    # Changed wording keeps the saved evidence and its knowledge links visible.
    with connect(db) as conn:
        conn.execute("UPDATE questions SET question_text='TEST-改写题面' WHERE id=1")
    changed = assert_refreshed()
    assert 1 in changed['by_question'] and 1 not in changed['no_usable']

    # New saved evidence changes the aggregate without checking current input.
    insert_evidence(3)
    added = assert_refreshed()
    assert 3 not in added['no_usable'] and added != changed

    with connect(db) as conn:
        conn.execute("UPDATE evidence_point_knowledge_links SET role='supporting_prerequisite' "
                     "WHERE question_id=1 AND role='direct'")
    relinked = assert_refreshed()
    assert relinked != added

    with connect(db) as conn:
        conn.execute("UPDATE questions SET is_deleted=1 WHERE id=2")
    deleted = assert_refreshed()
    assert 2 not in deleted['by_question'] and deleted != relinked

    with connect(db) as conn:
        conn.execute("INSERT INTO questions(id,paper_id,question_number,question_text,question_type) "
                     "VALUES(7,1,'7','TEST-新增题','解答题')")
    insert_evidence(7)
    assert 7 in assert_refreshed()['by_question']

    sidecar = tmp_path / 'question_bank/rich_content/question_1.json'
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text('{TEST-corrupt-current-content', encoding='utf-8')
    read_module._READ_RESULT_CACHE.clear()
    clear_file_caches()
    assert 1 not in assert_refreshed()['no_usable']

    with connect(db) as conn:
        conn.execute("UPDATE question_solution_evidence_versions SET source_content_hash=? "
                     "WHERE question_id=4", ('TEST-older-source'.ljust(64, '0'),))
    assert 4 not in assert_refreshed()['no_usable']

    read_module._READ_RESULT_CACHE.clear()
    clear_file_caches()
    service = QuestionBankReadService(db, data_root=tmp_path)
    with connect(db) as conn:
        conn.execute("UPDATE questions SET difficulty=9 WHERE id=5")
    assert float(assert_refreshed()['questions'][5]['difficulty']) == 9


def test_missing_knowledge_adds_only_fine_links_and_keeps_original_evidence_and_skills(tmp_path):
    from backend.jobs.knowledge_link_job import run_knowledge_link_job
    from backend.jobs.manager import JobContext
    from backend.jobs.store import JobStore
    from question_bank.database.schema import connect
    from question_bank.solution_evidence.knowledge_links import load_point_links
    from question_bank.solution_evidence.part_assessments import load_profiles, training_part_observations
    from question_bank.taxonomy.curriculum_catalog import curriculum_volume
    service, db, keys = _seed_skill_bank(tmp_path)
    release = service.skill_index('bnu24-math-g8-upper')['graph_release_id']
    fine = curriculum_volume(volume_id='bnu24-math-g8-upper')['chapters'][0]['sections'][0]['knowledge_points'][0]['id']
    with connect(db) as conn:
        row = conn.execute('SELECT evidence_version_id,evidence_json FROM question_solution_evidence_versions WHERE question_id=1').fetchone()
        evidence = json.loads(row['evidence_json'])
        for point in evidence['parts'][0]['evidence_points']:
            point['observable_evidence'] = 'TEST-可观察作答'
        conn.execute('UPDATE question_solution_evidence_versions SET evidence_json=? WHERE evidence_version_id=?', (json.dumps(evidence), row['evidence_version_id']))
        old_evidence = [tuple(row) for row in conn.execute('SELECT * FROM question_solution_evidence_versions WHERE question_id=1')]
        old_links = [tuple(row) for row in conn.execute('SELECT * FROM evidence_point_knowledge_links WHERE question_id=1')]
    profile = load_profiles(db, [1], data_root=tmp_path)[1]
    criteria = {'solution_evidence': {'source_content_hash': profile['evidence_source_hash']},
        'points': [{'point_id': point['evidence_point_id'], 'target': point['target'],
            'observable_evidence': point['observable_evidence']} for point in profile['evidence']['parts'][0]['evidence_points']]}
    facts = [{'point_id': 'p1', 'state': 'met'}, {'point_id': 'p2', 'state': 'not_met'}]
    before = training_part_observations(profile, criteria, facts, load_point_links(db, [f'{1:064x}'], release)[f'{1:064x}'])
    assert before is not None and len(before) == 2
    calls = []
    def gateway(request):
        calls.append(request)
        assert request['mode'] == 'missing_knowledge'
        from question_bank.taxonomy.curriculum_catalog import curriculum_knowledge_node
        assert all((curriculum_knowledge_node(candidate['id']) or {}).get('level') == 3 for candidate in request['questions'][0]['candidates'])
        return {1: [{'evidence_point_id': 'p1', 'links': [{'fine_term_id': fine, 'role': 'direct'}]}]}
    store = JobStore(tmp_path / 'TEST-fine-repair.db')
    record = store.create_job('knowledge_link', {'mode': 'missing_knowledge', 'question_ids': [1]})
    result = run_knowledge_link_job(context=JobContext(record.id, record.job_type, record.payload, store),
        question_bank_db_path=db, data_root=tmp_path, link_gateway=gateway)
    assert result['questions_linked'] == 1
    links = load_point_links(db, [f'{1:064x}'], release)[f'{1:064x}']
    assert {link.stable_key for link in links['p1']} == {keys[0], fine}
    assert {link.stable_key for link in links['p2']} == {keys[1]}
    after = training_part_observations(load_profiles(db, [1], data_root=tmp_path)[1], criteria, facts, links)
    assert after is not None
    assert [item for item in after if item['stable_key'] in keys] == before
    with connect(db) as conn:
        assert [tuple(row) for row in conn.execute('SELECT * FROM question_solution_evidence_versions WHERE question_id=1')] == old_evidence
        rows = [tuple(row) for row in conn.execute('SELECT * FROM evidence_point_knowledge_links WHERE question_id=1')]
        assert all(row in rows for row in old_links)
    again = store.create_job('knowledge_link', {'mode': 'missing_knowledge', 'question_ids': [1]})
    run_knowledge_link_job(context=JobContext(again.id, again.job_type, again.payload, store),
        question_bank_db_path=db, data_root=tmp_path, link_gateway=lambda request: {1: []})
    assert service.skill_index('bnu24-math-g8-upper')['coverage']['knowledge_points'] == 1


def test_repair_preview_uses_current_type_candidates_and_stops_repeating_checked_unclassified(tmp_path):
    import json
    from backend.jobs.question_bank_repair import repair_preview, _with_type_contracts, _type_candidate_fingerprint
    from question_bank.training_criteria import QuestionAnalysisInputLoader
    from question_bank.database.schema import connect

    service, db, _keys = _seed_skill_bank(tmp_path, count=1, taxonomy_revision=11)
    first = repair_preview(service, 'bnu24-math-g8-upper', 'all')
    item = first['items'][0]
    assert 'types' in item['missing']
    assert not item['type_pending_reason']
    question = _with_type_contracts(QuestionAnalysisInputLoader(db_path=db, data_root=tmp_path).load(
        [1], curriculum_volume_id='bnu24-math-g8-upper'), db)[0]
    assert question.taxonomy_contract['question_type_mode'] is True
    with connect(db) as conn:
        release = conn.execute("SELECT release_id FROM knowledge_graph_releases WHERE status='active'").fetchone()[0]
        marker = {'status': 'type_unclassified', 'source_hash': question.source_content_hash,
                  'release_id': release, 'type_candidate_fingerprint': _type_candidate_fingerprint(question)}
        conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value,source) "
                     "VALUES(1,'tag_status',?,'taxonomy')", (json.dumps(marker),))
    checked = repair_preview(service, 'bnu24-math-g8-upper', 'all')['items'][0]
    assert 'types' in checked['missing']
    assert checked['type_pending_reason'] == '已核对当前候选，暂无适合题型，待标准整理'
    with connect(db) as conn:
        conn.execute("UPDATE questions SET question_text='TEST-changed-type-task' WHERE id=1")
    changed = repair_preview(service, 'bnu24-math-g8-upper', 'all')['items'][0]
    assert not changed['type_pending_reason']


def test_type_index_section_ids_follow_curriculum_anchors(tmp_path):
    from copy import deepcopy
    from question_bank.services.question_skill_index import skill_index
    from question_bank.taxonomy.curriculum_catalog import curriculum_volume

    service, _db, _keys = _seed_skill_bank(tmp_path, count=1, taxonomy_revision=11)
    volume_id = 'bnu24-math-g8-upper'
    rows = service.skill_index(volume_id)['types']
    assert rows and all(row['section_id'] for row in rows)
    volume = curriculum_volume(volume_id=volume_id)
    chapter = volume['chapters'][0]
    section = chapter['sections'][1]
    snapshot = deepcopy(service._skill_snapshot())
    key = rows[0]['value']
    node = snapshot['nodes'][key]
    node['curriculum_anchors_json'] = json.dumps([
        chapter['knowledge_id'], section['knowledge_points'][0]['id'],
    ])
    rows = {row['value']: row for row in skill_index(snapshot, volume_id, set())['types']}
    assert rows[key]['section_id'] == section['knowledge_id']
    node['curriculum_anchors_json'] = json.dumps([chapter['knowledge_id']])
    rows = {row['value']: row for row in skill_index(snapshot, volume_id, set())['types']}
    assert rows[key]['section_id'] == chapter['knowledge_id']


def test_skill_index_versioned_cache_survives_training_ops_and_monitor_reset(
    tmp_path, monkeypatch,
):
    from integration.data_generation import data_versions, reset_commit_generations
    from question_bank.services import question_skill_index

    service, db, _keys = _seed_skill_bank(tmp_path)
    (tmp_path / 'question_bank').mkdir(exist_ok=True)
    reset_commit_generations()
    assert data_versions(db) is not None
    builds = []
    original_build = question_skill_index.build_skill_snapshot

    def build(*args, **kwargs):
        builds.append(1)
        return original_build(*args, **kwargs)

    monkeypatch.setattr(question_skill_index, 'build_skill_snapshot', build)
    index = service.skill_index('bnu24-math-g8-upper')
    assert len(builds) == 1

    # 训练作业写入只 bump training_ops：题库读缓存与落盘快照都不失效。
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO personalized_recommendation_drafts("
            "draft_id, request_token, input_fingerprint, result_version,"
            "engine_version, source_version, request_json, draft_json,"
            "status, revision, created_by) "
            "VALUES (?, ?, ?, ?, 'e', ?, '{}', '{}', 'draft', 1, 'TEST')",
            ("d" * 64, "r" * 32, "f" * 64, "v" * 64, "s" * 64),
        )
    reset_commit_generations()
    read_module._READ_RESULT_CACHE.clear()
    assert service.skill_index('bnu24-math-g8-upper') == index
    assert len(builds) == 1

    # 换一个服务实例并复位监视器：未变的 materials 版本让落盘快照直接复用。
    read_module._READ_RESULT_CACHE.clear()
    reset_commit_generations()
    restarted = QuestionBankReadService(db, data_root=tmp_path)
    assert restarted.skill_index('bnu24-math-g8-upper') == index
    assert len(builds) == 1

    # materials 域写入（题目标签）按预期失效并重建。
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO question_tags(question_id, tag_type, tag_value) "
            "VALUES (1, 'probe', 'TEST')"
        )
    reset_commit_generations()
    read_module._READ_RESULT_CACHE.clear()
    assert restarted.skill_index('bnu24-math-g8-upper')['question_count'] == index['question_count']
    assert len(builds) == 2


def test_skill_index_versioned_cache_falls_back_when_coverage_broken(
    tmp_path, monkeypatch,
):
    from integration.data_generation import data_versions, reset_commit_generations
    from question_bank.services import question_skill_index

    service, db, _keys = _seed_skill_bank(tmp_path)
    (tmp_path / 'question_bank').mkdir(exist_ok=True)
    reset_commit_generations()
    assert data_versions(db) is not None
    first = service.skill_index('bnu24-math-g8-upper')
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TRIGGER dv_question_tags_ins")
    reset_commit_generations()
    assert data_versions(db) is None
    # 覆盖不完整时回到既有文件态/清单行为，读取仍然可用。
    read_module._READ_RESULT_CACHE.clear()
    assert service.skill_index('bnu24-math-g8-upper') == first
