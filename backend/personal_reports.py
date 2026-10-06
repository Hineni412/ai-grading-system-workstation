"""个人报告的本人修订、叙述索引与只读在线上下文。"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import uuid
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from backend.report_exports import _report_revision_inputs, report_narrative_version, score_revision
from backend.session_analysis import assemble_session_analysis, infer_data_root, split_session_analysis_by_class

_index_lock = threading.RLock()
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


def student_report_revisions(repositories, session_id: int, student_ids, *, _inputs=None) -> dict[int, str]:
    selected = set(student_ids)
    inputs = (_report_revision_inputs(repositories, session_id, student_ids=selected)
              if _inputs is None else _inputs)
    locks = inputs['locks']
    source = inputs.get('question_bank_source', {})
    by_student = {sid: [] for sid in selected}
    for item in inputs['results']:
        row = item['result']
        sid = int(row.get("student_id") or 0)
        if sid in selected:
            by_student[sid].append(item)
    return {sid: _digest({"results": by_student[sid],
                         "locks": [dict(lock) for lock in locks if int(lock.get("student_id") or 0) == sid],
                         "question_bank_source": source,
                         "narrative_version": report_narrative_version("personal_analysis_html")})
            for sid in selected}


def student_report_revision(repositories, session_id: int, student_id: int) -> str:
    return student_report_revisions(repositories, session_id, [student_id])[student_id]


def personal_cache_key(session_id: int, student_id: int, revision: str) -> str:
    from backend.reporting.analysis_report_exporter import AnalysisNarrativeCache
    return AnalysisNarrativeCache.cache_key(session_id=session_id, score_revision="student:" + revision,
        rendition_version=report_narrative_version("personal_analysis_html"), report_key=f"personal:{student_id}")


def read_personal_index(cache_dir: Path, session_id: int) -> dict:
    try:
        value = json.loads((Path(cache_dir) / "personal_index" / f"session_{session_id}.json").read_text(encoding="utf-8"))
        return value["students"] if value.get("version") == 1 and isinstance(value.get("students"), dict) else {}
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
        return {}


def publish_personal_index(cache_dir: Path, session_id: int, student_id: int, revision: str) -> None:
    """在生成任务的调用线程中执行；查看请求从不调用。"""
    from backend.reporting.analysis_report_exporter import AnalysisNarrativeCache
    key = personal_cache_key(session_id, student_id, revision)
    if AnalysisNarrativeCache(cache_dir).load(key) is None:
        return
    with _index_lock:
        students = read_personal_index(cache_dir, session_id)
        students[str(student_id)] = dict(cache_key=key, student_revision=revision,
            narrative_version=report_narrative_version("personal_analysis_html"), generated_at=datetime.now().isoformat())
        target = Path(cache_dir) / "personal_index" / f"session_{session_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(dict(version=1, students=students), ensure_ascii=False), encoding="utf-8")
            temporary.replace(target)
        except OSError:
            temporary.unlink(missing_ok=True)


def lookup_personal_narrative(cache, session_id: int, student_id: int, revision: str,
                              session_revision: str, *, allow_stale: bool = True, index=None) -> dict:
    from backend.reporting.analysis_report_exporter import AnalysisNarrativeCache
    from backend.report_exports import LEGACY_PERSONAL_NARRATIVE_VERSIONS
    index = read_personal_index(cache.cache_dir, session_id) if index is None else index
    saved = index.get(str(student_id))
    if not isinstance(saved, dict):
        saved = {}
    keys = [personal_cache_key(session_id, student_id, revision)]
    keys.extend(AnalysisNarrativeCache.cache_key(session_id=session_id, score_revision=session_revision,
        rendition_version=version, report_key=f"personal:{student_id}") for version in
        (report_narrative_version("personal_analysis_html"), *LEGACY_PERSONAL_NARRATIVE_VERSIONS))
    for key in keys:
        narrative = cache.load(key)
        if narrative is not None:
            try:
                generated = datetime.fromtimestamp(cache._path(key).stat().st_mtime).isoformat()
            except OSError:
                generated = None
            return dict(status="current", narrative=narrative, generated_at=saved.get("generated_at") or generated, reason=None)
    stale_key = saved.get("cache_key")
    if allow_stale and isinstance(stale_key, str) and re.fullmatch(r"[0-9a-f]{64}", stale_key):
        narrative = cache.load(stale_key)
        if narrative is not None:
            return dict(status="stale", narrative=narrative, generated_at=saved.get("generated_at"), reason=None)
    return dict(status="missing", narrative=None, generated_at=None, reason=None)


def personal_report_states(repositories, session_id: int, reports_dir: Path, *, data=None, revision=None) -> dict:
    from backend.reporting.analysis_report_exporter import AnalysisNarrativeCache
    data = data or assemble_session_analysis(repositories, session_id, data_root=infer_data_root(repositories.db_path), page_only=True)
    inputs = _report_revision_inputs(repositories, session_id)
    revision = revision or score_revision(repositories, session_id, _inputs=inputs)
    revisions = student_report_revisions(repositories, session_id, [s.student_id for s in data.students], _inputs=inputs)
    cache = AnalysisNarrativeCache(Path(reports_dir) / ".analysis_narrative_cache")
    index = read_personal_index(cache.cache_dir, session_id)
    students = []
    for student in data.students:
        value = lookup_personal_narrative(cache, session_id, student.student_id, revisions[student.student_id], revision, index=index)
        students.append(dict(student_id=student.student_id, **{k: v for k, v in value.items() if k != "narrative"}))
    students.extend(dict(student_id=int(s["student_id"]), status="unavailable", generated_at=None,
                         reason=s["reason"]) for s in data.skipped)
    return dict(session_id=session_id, students=students)


def personal_report_summary(repositories, session_id: int, reports_dir: Path) -> dict[str, int]:
    """Use the same read-only status rules as the personal report list."""
    counts = dict(current=0, stale=0, missing=0)
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
    """缓存同学期的轻量成绩投影；叙述状态每次从当前缓存和索引读取。"""
    from backend.reporting.analysis_report_exporter import AnalysisNarrativeCache
    root = infer_data_root(repositories.db_path)
    key = (str(repositories.db_path), volume_id, _read_generation(repositories, root, reports_dir))
    def prepare():
        summaries = []
        for row in repositories.sessions.list_grading_sessions():
            if row.get("is_deleted") or (volume_id is not None and str(row.get("curriculum_volume_id") or "") != volume_id):
                continue
            sid = int(row["id"])
            data = assemble_session_analysis(repositories, sid, data_root=root, page_only=True)
            inputs = _report_revision_inputs(repositories, sid)
            summaries.append(dict(session_id=sid, session_name=data.session_name, graded_at=data.graded_at,
                max_score=data.full_score, scores={s.student_id: s.student_score for s in data.students},
                skipped={int(s["student_id"]): s["reason"] for s in data.skipped},
                revisions=student_report_revisions(repositories, sid, [s.student_id for s in data.students], _inputs=inputs),
                revision=score_revision(repositories, sid, _inputs=inputs)))
        return summaries

    summaries = _cached_context(_semester_summaries, key, prepare)
    cache = AnalysisNarrativeCache(reports_dir / ".analysis_narrative_cache")
    items = []
    for entry in summaries:
        if student_id in entry["scores"]:
            state = lookup_personal_narrative(cache, entry["session_id"], student_id,
                entry["revisions"][student_id], entry["revision"])
            score = entry["scores"][student_id]
        elif student_id in entry["skipped"]:
            state = dict(status="unavailable", generated_at=None, reason=entry["skipped"][student_id])
            score = None
        else:
            continue
        items.append(dict(**{k: entry[k] for k in ("session_id", "session_name", "graded_at", "max_score")},
                          score=score, **{k: state[k] for k in ("status", "generated_at", "reason")}))
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
        revision = score_revision(repositories, session_id)
        data = assemble_session_analysis(repositories, session_id, data_root=root)
        enrich_personal_questions(repositories, data, root)
        # 掌握与步骤资料的底层计算覆盖整学期。每场只准备一次，翻页直接取该生数据。
        enrich_personal_knowledge(repositories, data, root)
        context = dict(data=data, revision=revision, root=root,
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
    from backend.reporting.analysis_report_exporter import (AnalysisNarrativeCache, _render_personal_html, lost_question_shot_specs,
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
    revision = student_report_revision(repositories, session_id, student_id)
    cached = lookup_personal_narrative(AnalysisNarrativeCache(reports_dir / ".analysis_narrative_cache"),
        session_id, student_id, revision, context["revision"])
    if narrative_mode == "auto" and cached["status"] == "missing":
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
    return _render_personal_html(group, student, cached["narrative"] if narrative_mode == "auto" else None, shots,
        history=context["histories"].get(student_id, []), error_map=error_map, error_history=error_history,
        online=online, review_links=review_links, originals_released=released)
