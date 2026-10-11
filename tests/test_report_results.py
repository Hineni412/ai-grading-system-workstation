"""保存报告直接读取、单份覆盖与旧目录只读兼容。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.report_results import PersonalReportStore, resolve_result_state


def _tree_contents(root: Path) -> dict:
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
            if path.is_file() else None for path in root.rglob('*')}


@pytest.mark.parametrize('kind,field', [('causes', 'result'), ('personal_report', 'narrative')])
def test_saved_result_ignores_old_analysis_fields(kind, field):
    stored = {field: {'summary': 'TEST-旧内容'}, 'input_digest': 'old-input',
              'input_fingerprint': 'another-input', 'prompt_version': 'TEST-old-method',
              'version': 99, 'generated_at': '2026-01-01T00:00:00'}
    before = dict(stored)
    state = resolve_result_state(stored, kind)
    assert state['status'] == 'current'
    assert state['needs_update'] is False
    assert state['generated_at'] == stored['generated_at']
    assert 'input_digest' not in state and 'prompt_version' not in state
    assert stored == before


@pytest.mark.parametrize('generated,changed,expected', [
    ('2026-01-01T00:00:00', '2026-01-02T00:00:00', 'stale'),
    ('2026-01-01T00:00:00', '2026-01-01T00:00:00', 'current'),
    ('2026-01-02T00:00:00', '2026-01-01T00:00:00', 'current'),
    (None, '2026-01-02T00:00:00', 'current'),
    ('TEST-unknown-time', '2026-01-02T00:00:00', 'current'),
])
def test_update_time_is_only_a_reminder(generated, changed, expected):
    stored = {'narrative': {'summary': 'TEST-可读正文'}, 'generated_at': generated}
    state = resolve_result_state(stored, 'personal_report', current_changed_at=changed)
    assert state['status'] == expected
    assert state['needs_update'] is (expected == 'stale')
    assert stored['narrative'] == {'summary': 'TEST-可读正文'}
    marked = resolve_result_state({**stored, 'needs_update': True}, 'personal_report')
    assert marked['status'] == 'stale'
    assert marked['generated_at'] == generated


def test_missing_body_and_read_errors_do_not_become_saved_results():
    assert resolve_result_state(None, 'personal_report')['status'] == 'missing'
    assert resolve_result_state({'input_digest': 'legacy'}, 'causes')['status'] == 'missing'
    error = {'narrative': None, 'read_error': 'TEST-读取失败'}
    assert resolve_result_state(error, 'personal_report')['read_error'] == error['read_error']
    assert resolve_result_state(error, 'personal_report')['status'] == 'missing'
    with pytest.raises(ValueError):
        resolve_result_state({'narrative': {}}, 'TEST-unknown-kind')


def test_personal_report_store_success_replaces_one_file_and_keeps_other_students(tmp_path):
    store = PersonalReportStore(tmp_path)
    assert store.load(1, 2) is None
    assert store.list(1) == {}
    assert not store.base_dir.exists()
    source = {'student_name': 'TEST-学生'}
    saved = store.save(1, 2, narrative={'summary': 'TEST-第一份'}, source=source,
                       generated_at='2026-01-01T00:00:00')
    store.save(1, 3, narrative={'summary': 'TEST-另一学生'})
    other = store._path(1, 3).read_bytes()
    assert store.load(1, 2) == saved
    assert saved['generated_at'] == '2026-01-01T00:00:00'
    assert saved['source'] == source
    assert 'input_digest' not in saved and 'prompt_version' not in saved and 'version' not in saved
    updated = store.save(1, 2, narrative={'summary': 'TEST-主动更新'}, generated_at='2026-01-02T00:00:00')
    assert store.load(1, 2) == updated
    assert store._path(1, 3).read_bytes() == other
    assert set(store.list(1)) == {2, 3}
    assert {path.name for path in store._session_dir(1).iterdir()} == {'2.json', '3.json'}
    store.delete_session(1)
    assert store.list(1) == {} and store.load(1, 2) is None


def test_concurrent_successful_saves_leave_one_complete_result_and_no_temporary_files(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    store = PersonalReportStore(tmp_path)
    store.save(1, 2, narrative={'summary': 'TEST-previous'})
    barrier = Barrier(2)
    bodies = [{'summary': 'TEST-A', 'evidence': ['A'] * 20},
              {'summary': 'TEST-B', 'evidence': ['B'] * 30}]
    def save_together(body):
        barrier.wait(timeout=5)
        return store.save(1, 2, narrative=body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save_together, body) for body in bodies]
        successes = [future.result(timeout=10) for future in futures]
    assert len(successes) == 2
    assert store.load(1, 2) in successes
    assert store.load(1, 2)['narrative'] in bodies
    assert {path.name for path in store._session_dir(1).iterdir()} == {'2.json'}


@pytest.mark.parametrize('failure', ['no_result', 'replace_failed'])
def test_unsuccessful_save_keeps_previous_success_and_leaves_no_temporary_file(tmp_path, monkeypatch, failure):
    import backend.report_results as results
    store = PersonalReportStore(tmp_path)
    previous = store.save(1, 2, narrative={'summary': 'TEST-上次成功'}, needs_update=True)
    before = _tree_contents(tmp_path)
    if failure == 'no_result':
        with pytest.raises(ValueError):
            store.save(1, 2, narrative=None)
    else:
        original = results.os.replace
        def fail_selected(source, target):
            if Path(target) == store._path(1, 2):
                raise OSError('TEST-atomic-replace-failed')
            return original(source, target)
        monkeypatch.setattr(results.os, 'replace', fail_selected)
        with pytest.raises(OSError):
            store.save(1, 2, narrative={'summary': 'TEST-未采用的新内容'})
    assert store.load(1, 2) == previous
    assert _tree_contents(tmp_path) == before


def _write_legacy_results(root: Path, style: str, session_id: int = 7) -> dict[int, dict]:
    if style == 'class_state':
        cache = root / '.cache'
        index = root / '.class_analysis' / f'{session_id}.json'
        key = 'personal_index'
    else:
        cache = root / '.analysis_narrative_cache'
        index = cache / 'personal_index' / f'session_{session_id}.json'
        key = 'students'
    cache.mkdir(parents=True, exist_ok=True)
    index.parent.mkdir(parents=True, exist_ok=True)
    narratives = {11: {'summary': 'TEST-旧缓存正文'}, 22: {'summary': 'TEST-旧内嵌正文'}}
    (cache / 'TEST-key-11.json').write_text(json.dumps({'version': 99, 'narrative': narratives[11]}), encoding='utf-8')
    (cache / 'TEST-orphan.json').write_text(json.dumps({'version': 1, 'narrative': {'summary': 'TEST-无归属正文'}}), encoding='utf-8')
    index.write_text(json.dumps({'version': 99, key: {
        '11': {'cache_key': 'TEST-key-11', 'student_revision': 'TEST-old-value', 'prompt_version': 'TEST-old-method',
               'generated_at': '2026-01-01T00:00:00'},
        '22': {'narrative': narratives[22], 'generated_at': '2026-01-02T00:00:00'},
        'ignored-entry': {'cache_key': 'TEST-orphan'},
    }}), encoding='utf-8')
    return narratives


@pytest.mark.parametrize('style', ['class_state', 'narrative_cache'])
def test_legacy_reports_are_read_in_place_and_list_keeps_every_known_student(tmp_path, style):
    reports = tmp_path / 'TEST-reports'
    narratives = _write_legacy_results(reports, style)
    store = PersonalReportStore(reports)
    before = _tree_contents(reports)
    for student_id, narrative in narratives.items():
        saved = store.load(7, student_id)
        assert saved['narrative'] == narrative
        assert resolve_result_state(saved, 'personal_report')['status'] == 'current'
    entries = store.list(7)
    assert set(entries) == {11, 22}
    assert {student_id: saved['narrative'] for student_id, saved in entries.items()} == narratives
    assert _tree_contents(reports) == before
    assert not store.base_dir.exists()
    assert not (reports / 'backup').exists()


@pytest.mark.parametrize('style', ['class_state', 'narrative_cache'])
def test_current_success_wins_without_erasing_legacy_sources(tmp_path, style):
    reports = tmp_path / 'TEST-reports'
    narratives = _write_legacy_results(reports, style)
    store = PersonalReportStore(reports)
    legacy_before = _tree_contents(reports)
    saved = store.save(7, 11, narrative={'summary': 'TEST-当前采用正文'})
    current_before = _tree_contents(reports)
    assert store.load(7, 11) == saved
    assert store.list(7)[22]['narrative'] == narratives[22]
    assert set(store.list(7)) == {11, 22}
    assert _tree_contents(reports) == current_before
    assert all(_tree_contents(reports)[name] == value for name, value in legacy_before.items())
    assert len(list(store._session_dir(7).glob('*.json'))) == 1


@pytest.mark.parametrize('content', [b'{invalid', b'\xff', b'[]'])
def test_bad_saved_file_stays_at_original_path_with_a_read_error(tmp_path, content):
    store = PersonalReportStore(tmp_path)
    target = store._path(1, 2)
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    before = _tree_contents(tmp_path)
    saved = store.load(1, 2)
    assert saved['narrative'] is None and saved['read_error']
    state = resolve_result_state(saved, 'personal_report')
    assert state['status'] == 'missing' and state['read_error']
    assert store.list(1)[2]['read_error']
    assert _tree_contents(tmp_path) == before


@pytest.mark.parametrize('style', ['class_state', 'narrative_cache'])
def test_bad_primary_file_can_show_legacy_body_without_repairing_either_file(tmp_path, style):
    reports = tmp_path / 'TEST-reports'
    narratives = _write_legacy_results(reports, style)
    store = PersonalReportStore(reports)
    target = store._path(7, 11)
    target.parent.mkdir(parents=True)
    target.write_text('TEST-bad-json', encoding='utf-8')
    before = _tree_contents(reports)
    saved = store.load(7, 11)
    assert saved['narrative'] == narratives[11] and saved['read_error']
    assert resolve_result_state(saved, 'personal_report')['status'] == 'current'
    assert store.list(7)[11]['narrative'] == narratives[11]
    assert _tree_contents(reports) == before


def test_list_combines_both_legacy_indexes_and_current_files_without_writing(tmp_path):
    reports = tmp_path / 'TEST-reports'
    _write_legacy_results(reports, 'class_state')
    cache = reports / '.analysis_narrative_cache'
    index = cache / 'personal_index' / 'session_7.json'
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({'students': {'44': {'narrative': {'summary': 'TEST-第二种旧目录'}}}}), encoding='utf-8')
    store = PersonalReportStore(reports)
    store.save(7, 33, narrative={'summary': 'TEST-当前目录'})
    before = _tree_contents(reports)
    assert set(store.list(7)) == {11, 22, 33, 44}
    assert store.load(7, 44)['narrative']['summary'] == 'TEST-第二种旧目录'
    assert _tree_contents(reports) == before
