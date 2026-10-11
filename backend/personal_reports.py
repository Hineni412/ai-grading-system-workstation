"""按考试和学生读取一份已保存个人报告，页面准备只保留临时内存结果。"""
from __future__ import annotations

import copy
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from backend.report_results import (
    PersonalReportStore,
    resolve_result_state,
)
from backend.session_analysis import assemble_session_analysis, infer_data_root, split_session_analysis_by_class

_context_lock = threading.RLock()
_contexts: OrderedDict = OrderedDict()
_semester_summaries: OrderedDict = OrderedDict()
_status_inputs: OrderedDict = OrderedDict()
_context_flights: dict = {}
_page_images: OrderedDict = OrderedDict()
_page_lock = threading.Lock()
RELEASED_SHOT_NOTE = "原卷已释放，无法显示作答图；分数与批语不受影响"


def _cached_context(cache, key, compute):
    """Share one preparation per key without blocking unrelated exams."""
    flight_key = (id(cache), key)
    while True:
        with _context_lock:
            if key in cache:
                cache.move_to_end(key)
                return cache[key]
            flight = _context_flights.get(flight_key)
            owner = flight is None
            if owner:
                flight = {'event': threading.Event(), 'error': None}
                _context_flights[flight_key] = flight
        if not owner:
            flight['event'].wait()
            if flight['error'] is not None:
                raise flight['error']
            continue
        try:
            result = compute()
        except BaseException as error:
            with _context_lock:
                _context_flights.pop(flight_key, None)
                flight['error'] = error
                flight['event'].set()
            raise
        with _context_lock:
            cache[key] = result
            cache.move_to_end(key)
            while len(cache) > 2:
                cache.popitem(last=False)
            _context_flights.pop(flight_key, None)
            flight['event'].set()
        return result


def _student_change_times(repositories, session_id: int) -> dict[int, str]:
    """只读取本场评分和教师确认时间，提醒不限制旧报告使用。"""
    changed = {}
    for row in [*repositories.results.get_session_results(int(session_id)),
                *repositories.reviews.list_teacher_score_locks(int(session_id))]:
        student_id = int(row.get('student_id') or 0)
        timestamp = str(row.get('updated_at') or row.get('graded_at') or '').replace('T', ' ')
        if student_id and timestamp:
            changed[student_id] = max(changed.get(student_id, ''), timestamp)
    return changed


def personal_report_states(repositories, session_id: int, reports_dir: Path, *, data=None) -> dict:
    """已存正文始终可读；没有正文时才按当前成绩说明能否生成。"""
    from integration.data_generation import commit_generation, data_version_token

    def prepare():
        source = assemble_session_analysis(
            repositories, int(session_id),
            data_root=infer_data_root(repositories.db_path), page_only=True,
        )
        return dict(student_ids=[student.student_id for student in source.students],
                    skipped=source.skipped, changed_at=_student_change_times(repositories, session_id))

    if data is not None:
        inputs = dict(student_ids=[student.student_id for student in data.students], skipped=data.skipped,
                      changed_at=_student_change_times(repositories, session_id))
    else:
        key = (str(repositories.db_path), int(session_id),
               data_version_token(repositories.db_path, "results")
               or commit_generation(repositories.db_path))
        inputs = _cached_context(_status_inputs, key, prepare)
    saved = PersonalReportStore(Path(reports_dir)).list(int(session_id))
    skipped = {int(item['student_id']): item for item in inputs['skipped']}
    student_ids = dict.fromkeys([*inputs['student_ids'], *skipped, *saved])
    students = []
    for student_id in student_ids:
        state = resolve_result_state(saved.get(student_id), 'personal_report',
                                     current_changed_at=inputs['changed_at'].get(student_id))
        read_error = (saved.get(student_id) or {}).get('read_error')
        reason = read_error or None
        if state['status'] == 'missing' and student_id in skipped:
            state = dict(status='unavailable', generated_at=None)
            reason = skipped[student_id]['reason']
        students.append(dict(student_id=student_id, status=state['status'],
                             generated_at=state['generated_at'], reason=reason, read_error=read_error))
    return dict(session_id=session_id, students=students)


def personal_report_summary(repositories, session_id: int, reports_dir: Path) -> dict[str, int]:
    """Use the same read-only status rules as the personal report list."""
    counts = dict(current=0, stale=0, old_prompt=0, missing=0)
    for student in personal_report_states(repositories, session_id, reports_dir)["students"]:
        status = student["status"]
        if status == 'missing' and student.get('read_error'):
            continue
        if status in counts:
            counts[status] += 1
    return counts


def _read_generation(repositories, root: Path, reports_dir: Path):
    from integration.data_generation import commit_generation, data_version_token
    from integration.diagnosis_profile_service import _dir_generation
    bank = root / "databases" / "question_bank.db"
    return (
        data_version_token(repositories.db_path, "results")
        or commit_generation(repositories.db_path),
        (
            data_version_token(bank, "materials", "training_records", "training_ops")
            or commit_generation(bank)
        )
        if bank.is_file()
        else None,
        _dir_generation(reports_dir / ".class_analysis"), datetime.now().date().isoformat())


def student_personal_report_exams(repositories, student_id: int, reports_dir: Path, volume_id=None) -> dict:
    """历次考试按保存归属读取，当前成绩不可用也保留已存正文入口。"""
    root = infer_data_root(repositories.db_path)
    key = (str(repositories.db_path), volume_id, _read_generation(repositories, root, reports_dir))
    def prepare():
        summaries = []
        for row in repositories.sessions.list_grading_sessions():
            if row.get('is_deleted') or (volume_id is not None and str(row.get('curriculum_volume_id') or '') != volume_id):
                continue
            sid = int(row['id'])
            data = assemble_session_analysis(repositories, sid, data_root=root, page_only=True)
            summaries.append(dict(session_id=sid, session_name=data.session_name, graded_at=data.graded_at,
                max_score=data.full_score, scores={student.student_id: student.student_score for student in data.students},
                skipped={int(item['student_id']): item['reason'] for item in data.skipped},
                changed_at=_student_change_times(repositories, sid)))
        return summaries

    store = PersonalReportStore(Path(reports_dir))
    items = []
    for entry in _cached_context(_semester_summaries, key, prepare):
        saved = store.load(entry['session_id'], student_id)
        state = resolve_result_state(saved, 'personal_report',
                                     current_changed_at=entry['changed_at'].get(student_id))
        score = entry['scores'].get(student_id)
        if state['status'] == 'missing':
            if student_id in entry['skipped']:
                state = dict(status='unavailable', generated_at=None, reason=entry['skipped'][student_id])
            elif student_id not in entry['scores']:
                continue
        items.append(dict(**{key: entry[key] for key in ('session_id', 'session_name', 'graded_at', 'max_score')},
                          score=score, status=state['status'],
                          generated_at=state.get('generated_at'), reason=(saved or {}).get('read_error') or state.get('reason'),
                          read_error=(saved or {}).get('read_error')))
    items.sort(key=lambda item: (item['graded_at'] or '', item['session_id']))
    return dict(student_id=student_id, sessions=items)


def personal_render_context(repositories, session_id: int, reports_dir: Path) -> dict:
    from backend.reporting.analysis_report_exporter import _load_student_histories, load_session_regions, _load_personal_error_histories
    from backend.class_analysis import ClassAnalysisStateStore, build_cause_inputs
    from backend.session_analysis import enrich_personal_questions, enrich_personal_knowledge
    root = infer_data_root(repositories.db_path)
    store = ClassAnalysisStateStore(reports_dir)
    try:
        mtime = store._path(session_id).stat().st_mtime_ns
    except OSError:
        mtime = 0
    # 原卷状态与其他场次成绩同样影响页面；读取不写缓存文件。
    from backend.files.session_originals import originals_state
    sessions = repositories.sessions.list_grading_sessions()
    snapshot_stamp = None
    if root is not None:
        snapshot = root / "config" / "uploaded" / f"evidence_snapshot-{session_id}.json"
        try:
            snapshot_stat = snapshot.stat()
            snapshot_stamp = (snapshot_stat.st_mtime_ns, snapshot_stat.st_size)
        except OSError:
            pass
    key = (str(repositories.db_path), session_id, mtime, originals_state(root, session_id),
           tuple((s.get('id'), s.get('name'), s.get('graded_at'), s.get('is_deleted')) for s in sessions), _read_generation(repositories, root, reports_dir), snapshot_stamp)
    def prepare():
        data = assemble_session_analysis(repositories, session_id, data_root=root)
        enrich_personal_questions(repositories, data, root)
        # 掌握与步骤资料的底层计算覆盖整学期。每场只准备一次，翻页直接取该生数据。
        enrich_personal_knowledge(repositories, data, root)
        context = dict(data=data, root=root,
            groups=split_session_analysis_by_class(data), histories=_load_student_histories(repositories, data, root),
            regions=load_session_regions(repositories, session_id, data_root=root),
            error_state=store.load(session_id) or {}, error_sources=build_cause_inputs(data))
        context["error_histories"] = _load_personal_error_histories(repositories, context["histories"], session_id, reports_dir, root)
        from backend.session_analysis import question_bank_db_path
        bank_path = question_bank_db_path(Path(repositories.db_path))
        context['step_diagnoses'] = {}
        if bank_path is not None:
            from backend.step_diagnosis import build_session_step_diagnosis
            context['step_diagnoses'] = build_session_step_diagnosis(
                repositories, session_id, student_ids=[student.student_id for student in data.students],
                question_bank_path=bank_path,
            ).students
        return context

    return _cached_context(_contexts, key, prepare)


def crop_personal_report_shot(image_path: Path, region) -> str | None:
    """同一答卷的多个题框复用解码结果；仅在内存保留最多两页、128 MiB。"""
    from PIL import Image
    from backend.reporting.analysis_report_exporter import _crop_region_data_uri
    state = image_path.stat()
    key = (str(image_path), state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns)
    with _page_lock:
        image = _page_images.get(key)
        if image is None:
            with Image.open(image_path) as source:
                image = source.copy()
            size = image.width * image.height * len(image.getbands())
            if size > 128 * 1024 * 1024:
                try:
                    return _crop_region_data_uri(image_path, region, decoded_image=image)
                finally:
                    image.close()
            _page_images[key] = image
            while len(_page_images) > 2 or sum(i.width * i.height * len(i.getbands()) for i in _page_images.values()) > 128 * 1024 * 1024:
                _page_images.popitem(last=False)[1].close()
        else:
            _page_images.move_to_end(key)
        return _crop_region_data_uri(image_path, region, decoded_image=image)


def render_personal_report(repositories, session_id: int, student_id: int, reports_dir: Path,
                           *, narrative_mode="auto", review_links=False, online=True) -> str:
    from backend.reporting.analysis_report_exporter import (_render_personal_html, lost_question_shot_specs,
        capture_lost_question_shots)
    from backend.class_analysis import student_error_map
    from backend.files.session_originals import originals_state
    saved = PersonalReportStore(Path(reports_dir)).load(session_id, student_id)
    narrative = saved.get('narrative') if isinstance(saved, dict) else None
    if narrative_mode == 'auto' and not isinstance(narrative, dict):
        if isinstance(saved, dict) and saved.get('read_error'):
            raise ValueError('personal_report_read_error')
        raise ValueError('personal_report_missing')
    context = personal_render_context(repositories, session_id, reports_dir)
    matched = next(((g, s) for g in context["groups"].values()
                    for s in g.students if s.student_id == student_id), None)
    if matched is None:
        if isinstance(narrative, dict) and narrative_mode == 'auto':
            from backend.reporting.analysis_report_exporter import render_saved_narrative
            source = saved.get('source') if isinstance(saved.get('source'), dict) else {}
            session = repositories.sessions.get_grading_session(session_id) or {}
            return render_saved_narrative(narrative, title='个人报告',
                owner=source.get('student_name') or f'学生编号 {student_id}',
                session_name=source.get('session_name') or session.get('name') or f'考试编号 {session_id}',
                generated_at=saved.get('generated_at'))
        raise ValueError("personal_report_unavailable")
    group, student = matched
    # 其他学生只参与已经算出的匿名统计；单生渲染不复制全班步骤与历次证据。
    group = copy.deepcopy(group, {id(s): s for s in group.students})
    student = copy.deepcopy(student)
    released = originals_state(context["root"], session_id) in {"clearing", "cleared"}
    kwargs = dict(regions=context["regions"], data_root=context["root"])
    if released:
        shots = {}
    elif online:
        from urllib.parse import quote
        shots = {spec["key"]: dict(spec, src=f"/api/sessions/{session_id}/personal-reports/{student_id}/shots/{quote(spec['key'])}")
                 for spec in lost_question_shot_specs(repositories, group, student, **kwargs)}
    else:
        shots = capture_lost_question_shots(repositories, group, student, **kwargs)
    error_map = student_error_map(context["error_state"], student, context["error_sources"], context["data"])
    error_history = context["error_histories"].get(student_id)
    return _render_personal_html(group, student, narrative if narrative_mode == "auto" else None, shots,
        history=context["histories"].get(student_id, []), error_map=error_map, error_history=error_history,
        online=online, review_links=review_links, originals_released=released,
        step_diagnoses=context["step_diagnoses"].get(student_id),
        narrative_saved_at=saved.get("generated_at") if isinstance(saved, dict) else None)
