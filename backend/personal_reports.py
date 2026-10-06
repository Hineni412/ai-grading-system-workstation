"""个人报告的结果状态、输入指纹与只读在线上下文。

叙述结果存放在 ``reports_dir/.personal_reports/session_<场次>/<学生>.json``
（backend.report_results.PersonalReportStore），每生每场只保留最近一次结果；
新鲜度由 input_digest（成绩、教师锁、错因记录、题面）+ prompt_version 判定，
不再使用按指纹命名的缓存文件与 personal_index。
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from backend.report_results import (
    PersonalReportStore,
    personal_input_digest,
    resolve_result_state,
)
from backend.session_analysis import assemble_session_analysis, infer_data_root, split_session_analysis_by_class

_context_lock = threading.RLock()
_contexts: OrderedDict = OrderedDict()
_semester_summaries: OrderedDict = OrderedDict()
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


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def student_report_digests(
    repositories, session_id: int, data, *, reports_dir: Path,
) -> dict[int, str]:
    """每名学生的个人报告输入指纹：成绩行、教师锁、错因记录与题面。

    data 需已 enrich_personal_questions（question_text/reference_analysis
    参与指纹）。错因记录取状态文件里该生物化行的投影 [question_id, kind,
    category, reason]，不做新鲜度过滤：重新整理后行内容变化即过期，其他学生
    的成绩变化不会连带本场已物化记录。指纹不包含题库关联与提示词版本。
    """
    from backend.class_analysis import ClassAnalysisStateStore

    state = ClassAnalysisStateStore(Path(reports_dir)).load(int(session_id)) or {}
    envelopes = state.get("error_records") or {}
    error_rows_by_student: dict[int, list[list[Any]]] = {}
    for question_id, envelope in envelopes.items():
        for row in (envelope or {}).get("records") or []:
            if not isinstance(row, dict):
                continue
            student_key = row.get("student_id")
            if not isinstance(student_key, int):
                continue
            error_rows_by_student.setdefault(student_key, []).append(
                [question_id, row.get("kind"), row.get("category"), row.get("pattern")]
            )
    locks_all = repositories.reviews.list_teacher_score_locks(int(session_id))
    questions = [
        [info.question_id, info.question_text, info.reference_analysis]
        for info in data.questions
    ]
    digests: dict[int, str] = {}
    for student in data.students:
        records = [
            [record.question_id, record.score, record.max_score]
            for record in student.records
        ]
        locks = [
            [str(lock.get("question_id") or ""), lock.get("score_awarded")]
            for lock in locks_all
            if int(lock.get("student_id") or 0) == student.student_id
        ]
        error_rows = error_rows_by_student.get(int(student.student_id), [])
        digests[int(student.student_id)] = personal_input_digest(
            records=records,
            locks=locks,
            error_records=error_rows,
            questions=questions,
        )
    return digests


def personal_report_states(repositories, session_id: int, reports_dir: Path, *, data=None) -> dict:
    from backend.session_analysis import enrich_personal_questions

    data = data or assemble_session_analysis(repositories, session_id, data_root=infer_data_root(repositories.db_path), page_only=True)
    enrich_personal_questions(repositories, data, None)
    digests = student_report_digests(
        repositories, int(session_id), data, reports_dir=reports_dir
    )
    store = PersonalReportStore(Path(reports_dir))
    students = []
    for student in data.students:
        entry = store.load(int(session_id), student.student_id)
        state = resolve_result_state(
            entry, digests.get(student.student_id, ""), "personal_report"
        )
        students.append(dict(
            student_id=student.student_id,
            status=state["status"],
            generated_at=state["generated_at"],
            reason=None,
        ))
    students.extend(dict(student_id=int(s["student_id"]), status="unavailable", generated_at=None,
                         reason=s["reason"]) for s in data.skipped)
    return dict(session_id=session_id, students=students)


def personal_report_summary(repositories, session_id: int, reports_dir: Path) -> dict[str, int]:
    """Use the same read-only status rules as the personal report list."""
    counts = dict(current=0, stale=0, old_prompt=0, missing=0)
    for student in personal_report_states(repositories, session_id, reports_dir)["students"]:
        status = student["status"]
        if status in counts:
            counts[status] += 1
    return counts


def _read_generation(repositories, root: Path, reports_dir: Path):
    from integration.data_generation import commit_generation
    from integration.diagnosis_profile_service import _dir_generation
    bank = root / "databases" / "question_bank.db"
    return (commit_generation(repositories.db_path), commit_generation(bank) if bank.is_file() else None,
            _dir_generation(reports_dir / ".class_analysis"), datetime.now().date().isoformat())


def student_personal_report_exams(repositories, student_id: int, reports_dir: Path, volume_id=None) -> dict:
    """缓存同学期的轻量成绩投影；叙述状态每次按当前输入指纹判定。"""
    from backend.session_analysis import enrich_personal_questions

    root = infer_data_root(repositories.db_path)
    key = (str(repositories.db_path), volume_id, _read_generation(repositories, root, reports_dir))
    def prepare():
        summaries = []
        for row in repositories.sessions.list_grading_sessions():
            if row.get("is_deleted") or (volume_id is not None and str(row.get("curriculum_volume_id") or "") != volume_id):
                continue
            sid = int(row["id"])
            data = assemble_session_analysis(repositories, sid, data_root=root, page_only=True)
            enrich_personal_questions(repositories, data, None)
            summaries.append(dict(session_id=sid, session_name=data.session_name, graded_at=data.graded_at,
                max_score=data.full_score, scores={s.student_id: s.student_score for s in data.students},
                skipped={int(s["student_id"]): s["reason"] for s in data.skipped},
                digests=student_report_digests(repositories, sid, data, reports_dir=reports_dir)))
        return summaries

    summaries = _cached_context(_semester_summaries, key, prepare)
    store = PersonalReportStore(Path(reports_dir))
    items = []
    for entry in summaries:
        if student_id in entry["scores"]:
            saved = store.load(entry["session_id"], student_id)
            state = resolve_result_state(
                saved, entry["digests"].get(student_id, ""), "personal_report"
            )
            score = entry["scores"][student_id]
        elif student_id in entry["skipped"]:
            state = dict(status="unavailable", generated_at=None)
            score = None
        else:
            continue
        items.append(dict(**{k: entry[k] for k in ("session_id", "session_name", "graded_at", "max_score")},
                          score=score, status=state["status"],
                          generated_at=state.get("generated_at"), reason=state.get("reason")))
    items.sort(key=lambda s: (s["graded_at"] or "", s["session_id"]))
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
    key = (str(repositories.db_path), session_id, mtime, originals_state(root, session_id),
           _digest([dict(s) for s in sessions]), _read_generation(repositories, root, reports_dir))
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
    context = personal_render_context(repositories, session_id, reports_dir)
    matched = next(((g, s) for g in context["groups"].values()
                    for s in g.students if s.student_id == student_id), None)
    if matched is None:
        raise ValueError("personal_report_unavailable")
    group, student = matched
    # 其他学生只参与已经算出的匿名统计；单生渲染不复制全班步骤与历次证据。
    group = copy.deepcopy(group, {id(s): s for s in group.students})
    student = copy.deepcopy(student)
    # 已存叙述（含 stale/old_prompt）照常展示；没有结果才视为未生成。
    saved = PersonalReportStore(Path(reports_dir)).load(session_id, student_id)
    narrative = saved.get("narrative") if isinstance(saved, dict) else None
    if narrative_mode == "auto" and not isinstance(narrative, dict):
        raise ValueError("personal_report_missing")
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
        online=online, review_links=review_links, originals_released=released)
