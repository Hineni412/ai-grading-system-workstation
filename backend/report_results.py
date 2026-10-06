"""报告结果版本化：状态判定、输入指纹与个人报告存储。

三类对象（错因整理、班级报告、个人报告）各自保存最近一次生成结果，
以 input_digest（输入内容指纹）+ prompt_version（提示词版本）判定状态：

- current    输入指纹与提示词版本都与当前一致；
- stale      输入指纹不同（成绩、复核锁、错因或题面有变化），旧结果仍可读；
- old_prompt 输入一致但提示词版本旧，旧结果仍可读、不自动重算；
- missing    没有已存结果。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

RESULT_STATES = ("current", "stale", "old_prompt", "missing")

# 三类报告结果的提示词版本；只有这里保存这些字符串。
_PROMPT_VERSIONS = {
    "causes": "class_error_causes_v4",
    "class_report": "class_analysis_page_v4_cause_payload",
    "personal_report": "personal_analysis_html_v12_knowledge_focus",
}

# 个人报告结果目录：reports_dir/.personal_reports/session_<sid>/<student_id>.json
PERSONAL_REPORTS_DIRNAME = ".personal_reports"
_MIGRATED_MARKER = ".migrated.json"

# 状态文件的报告结果格式版本；2 = 已迁移到 input_digest/prompt_version。
RESULTS_VERSION = 2


def prompt_version(kind: str) -> str:
    """kind ∈ {"causes", "class_report", "personal_report"}。"""
    try:
        return _PROMPT_VERSIONS[kind]
    except KeyError:
        raise ValueError(f"unknown report result kind: {kind!r}") from None


def resolve_result_state(
    stored: Mapping[str, Any] | None,
    current_digest: str,
    kind: str,
) -> dict[str, Any]:
    """按已存结果与当前输入指纹判定状态；old_prompt 可读但不自动重算。"""
    if not isinstance(stored, Mapping) or (
        stored.get("result") is None and stored.get("narrative") is None
    ):
        return {
            "status": "missing",
            "generated_at": None,
            "prompt_version": None,
            "input_digest": None,
        }
    stored_digest = str(stored.get("input_digest") or "")
    stored_prompt = str(stored.get("prompt_version") or "")
    if not stored_digest or stored_digest != str(current_digest):
        status = "stale"
    elif stored_prompt != prompt_version(kind):
        status = "old_prompt"
    else:
        status = "current"
    return {
        "status": status,
        "generated_at": stored.get("generated_at"),
        "prompt_version": stored_prompt,
        "input_digest": stored_digest,
    }


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _rows(rows: Any) -> list[list[Any]]:
    """行级内容排序成确定顺序；元素类型不一致时按 JSON 文本排序。"""
    items = [list(row) for row in (rows or [])]
    return sorted(
        items,
        key=lambda row: json.dumps(row, ensure_ascii=False, default=str),
    )


def cause_input_digest(source: Mapping[str, Any]) -> str:
    """错因整理输入指纹：除 known_patterns 外的全部输入（沿用旧指纹算法）。"""
    payload = {
        key: value
        for key, value in dict(source or {}).items()
        if key != "known_patterns"
    }
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, default=str
        ).encode("utf-8")
    ).hexdigest()


def class_input_digest(
    *,
    records: Any,
    locks: Any,
    cause_digest: str,
) -> str:
    """班级报告输入指纹：本班成绩行、教师锁行与班级错因摘要指纹。"""
    return _canonical_digest(
        {
            "records": _rows(records),
            "locks": _rows(locks),
            "cause_digest": str(cause_digest or ""),
        }
    )


def personal_input_digest(
    *,
    records: Any,
    locks: Any,
    error_records: Any,
    questions: Any,
) -> str:
    """个人报告输入指纹：该生成绩行、教师锁行、错因记录与题面。"""
    return _canonical_digest(
        {
            "records": _rows(records),
            "locks": _rows(locks),
            "error_records": _rows(error_records),
            "questions": _rows(questions),
        }
    )


class PersonalReportStore:
    """每个学生一个结果文件，原子写入；不再使用按指纹命名的缓存文件。"""

    def __init__(self, reports_dir: Path) -> None:
        self.base_dir = Path(reports_dir) / PERSONAL_REPORTS_DIRNAME

    def _session_dir(self, session_id: int) -> Path:
        return self.base_dir / f"session_{int(session_id)}"

    def _path(self, session_id: int, student_id: int) -> Path:
        return self._session_dir(session_id) / f"{int(student_id)}.json"

    def load(self, session_id: int, student_id: int) -> dict[str, Any] | None:
        try:
            payload = json.loads(
                self._path(session_id, student_id).read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def save(
        self,
        session_id: int,
        student_id: int,
        *,
        narrative: Any,
        input_digest: str,
        prompt_version: str,
    ) -> dict[str, Any]:
        entry = {
            "narrative": narrative,
            "input_digest": str(input_digest or ""),
            "prompt_version": str(prompt_version or ""),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        target = self._path(session_id, student_id)
        temp = target.with_name(f".{target.stem}.tmp")
        try:
            temp.write_text(
                json.dumps(entry, ensure_ascii=False, indent=1) + "\n",
                encoding="utf-8",
            )
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        return entry

    def list(self, session_id: int) -> dict[int, dict[str, Any]]:
        entries: dict[int, dict[str, Any]] = {}
        session_dir = self._session_dir(session_id)
        try:
            files = sorted(session_dir.glob("*.json"))
        except OSError:
            return entries
        for file in files:
            try:
                student_id = int(file.stem)
            except ValueError:
                continue
            entry = self.load(session_id, student_id)
            if entry is not None:
                entries[student_id] = entry
        return entries

    def delete_session(self, session_id: int) -> None:
        shutil.rmtree(self._session_dir(session_id), ignore_errors=True)


# ---------------------------------------------------------------------------
# v1 → v2 迁移（仅启动时执行一次；幂等）

# 仅供迁移辨认旧叙述文件；新版提示词版本只经 prompt_version() 读取。
_LEGACY_PERSONAL_NARRATIVE_VERSIONS = [
    "personal_analysis_html_v12_knowledge_focus",
    "personal_analysis_html_v11_error_causes",
    "personal_analysis_html_v9_problem_refs",
    "personal_analysis_html_v8_parts",
]

# 仅供迁移：旧版按 key 命名叙述缓存的键算法（与旧 AnalysisNarrativeCache 一致）。
def _legacy_cache_key(
    *,
    session_id: int,
    score_revision: str,
    rendition_version: str,
    report_key: str,
) -> str:
    payload = {
        "session_id": int(session_id),
        "score_revision": str(score_revision),
        "rendition_version": str(rendition_version),
        "report_key": str(report_key),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _legacy_cache_load(cache_dir: Path, key: str) -> Any:
    path = Path(cache_dir) / f"{key}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or "narrative" not in payload:
        return None
    return payload.get("narrative")


def _legacy_student_report_revision(repositories, session_id: int, student_id: int) -> str:
    """仅供迁移：旧版学生输入修订指纹算法，用于核对 personal_index 是否过期。"""
    from backend.report_exports import _report_revision_inputs, report_narrative_version

    inputs = _report_revision_inputs(repositories, session_id, student_ids={student_id})
    results = [
        item
        for item in inputs["results"]
        if int(item["result"].get("student_id") or 0) == int(student_id)
    ]
    return hashlib.sha256(
        json.dumps(
            {
                "results": results,
                "locks": [
                    dict(lock)
                    for lock in inputs["locks"]
                    if int(lock.get("student_id") or 0) == int(student_id)
                ],
                "question_bank_source": inputs.get("question_bank_source", {}),
                "narrative_version": report_narrative_version("personal_analysis_html"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _read_migrated_marker(base_dir: Path) -> set[int]:
    try:
        payload = json.loads(
            (Path(base_dir) / _MIGRATED_MARKER).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return set()
    sessions = payload.get("sessions") if isinstance(payload, dict) else None
    if not isinstance(sessions, list):
        return set()
    return {int(sid) for sid in sessions if str(sid).isdigit() or isinstance(sid, int)}


def _write_migrated_marker(base_dir: Path, sessions: set[int]) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    target = Path(base_dir) / _MIGRATED_MARKER
    temp = target.with_name(f".{target.stem}.tmp")
    temp.write_text(
        json.dumps(
            {"version": 1, "sessions": sorted(int(sid) for sid in sessions)},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.replace(temp, target)


def migrate_report_results(reports_dir: Path, db) -> dict[str, Any]:
    """把 v1 键名缓存/修订字段迁移为 input_digest + prompt_version。

    幂等：状态文件 results_version==2 且 .migrated.json 已记录的场次跳过；
    全部场次迁移成功后删除根级 .analysis_narrative_cache（inspection_* 内不动）。
    返回每场迁移数量与汇总；出错时抛异常，不写完成标记。
    """
    from backend.class_analysis import (
        CLASS_ANALYSIS_STATE_DIRNAME,
        NARRATIVE_CACHE_DIRNAME,
        ClassAnalysisStateStore,
        assemble_cause_data,
        session_error_records,
    )
    from backend.personal_reports import student_report_digests
    from backend.report_exports import score_revision  # 仅供迁移核对的旧口径
    from backend.report_pipeline import (
        class_cause_digest,
        class_report_input_digest,
    )
    from backend.session_analysis import (
        assemble_session_analysis,
        enrich_personal_questions,
        infer_data_root,
        split_session_analysis_by_class,
    )

    reports_dir = Path(reports_dir)
    state_dir = reports_dir / CLASS_ANALYSIS_STATE_DIRNAME
    cache_dir = reports_dir / NARRATIVE_CACHE_DIRNAME
    personal_store = PersonalReportStore(reports_dir)
    migrated = _read_migrated_marker(personal_store.base_dir)
    session_ids = []
    if state_dir.is_dir():
        for file in sorted(state_dir.glob("*.json")):
            if file.stem.isdigit():
                session_ids.append(int(file.stem))
    pending = [sid for sid in session_ids if sid not in migrated]
    summary: dict[str, Any] = {
        "backup_dir": None,
        "sessions": {},
        "already_migrated": sorted(migrated),
        "cache_dir_deleted": False,
    }
    if pending:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = reports_dir / "backup" / f"report-results-{stamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        if state_dir.is_dir():
            shutil.copytree(
                state_dir,
                backup_dir / CLASS_ANALYSIS_STATE_DIRNAME,
                dirs_exist_ok=True,
            )
        if cache_dir.is_dir():
            shutil.copytree(
                cache_dir,
                backup_dir / NARRATIVE_CACHE_DIRNAME,
                dirs_exist_ok=True,
            )
        summary["backup_dir"] = str(backup_dir)
        store = ClassAnalysisStateStore(reports_dir)
        for sid in pending:
            summary["sessions"][sid] = _migrate_one_session(
                sid,
                store=store,
                cache_dir=cache_dir,
                personal_store=personal_store,
                db=db,
                score_revision=score_revision,
                assemble_cause_data=assemble_cause_data,
                session_error_records=session_error_records,
                class_cause_digest=class_cause_digest,
                class_report_input_digest=class_report_input_digest,
                student_report_digests=student_report_digests,
                assemble_session_analysis=assemble_session_analysis,
                enrich_personal_questions=enrich_personal_questions,
                infer_data_root=infer_data_root,
                split_session_analysis_by_class=split_session_analysis_by_class,
            )
            migrated.add(sid)
            _write_migrated_marker(personal_store.base_dir, migrated)
    if all(sid in migrated for sid in session_ids) and cache_dir.is_dir():
        shutil.rmtree(cache_dir)
        summary["cache_dir_deleted"] = True
    return summary


def _migrate_one_session(sid, **deps) -> dict[str, Any]:
    store = deps["store"]
    cache_dir = deps["cache_dir"]
    personal_store = deps["personal_store"]
    db = deps["db"]
    counts = {"causes": 0, "class_reports": 0, "personal_reports": 0}
    try:
        raw = json.loads(store._path(sid).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    # --- 错因：v4/v3 保留旧指纹作为 input_digest（算法相同）；v2/v1 写空指纹，
    # 读出来即 stale（输入口径已变，下次整理按当前输入重算）。 ---
    cause_state = raw.get("cause_analysis")
    if isinstance(cause_state, dict):
        questions = {}
        for qid, saved in (cause_state.get("questions") or {}).items():
            if not isinstance(saved, dict):
                continue
            version = str(saved.get("version") or "")
            digest = (
                str(saved.get("input_fingerprint") or "")
                if version in ("class_error_causes_v4", "class_error_causes_v3")
                else ""
            )
            questions[str(qid)] = {
                "result": saved.get("result"),
                "input": saved.get("input"),
                "input_digest": digest,
                "prompt_version": version,
                "origin": saved.get("origin"),
                "generated_at": saved.get("generated_at"),
                "failed": bool(saved.get("failed")),
                "failed_input_digest": (
                    saved.get("failed_input_fingerprint")
                    if saved.get("failed")
                    else None
                ),
            }
            counts["causes"] += 1
        cause_state = {**cause_state, "questions": questions}

    # --- 班级报告：叙述并入班级条目；旧修订一致才写真实指纹，否则留空 → stale ---
    old_revision = deps["score_revision"](
        db, sid, include_question_bank=False
    )
    class_current = (
        str(raw.get("score_revision") or "") == old_revision
        and str(raw.get("rendition_version") or "")
        == prompt_version("class_report")
    )
    groups = deps["split_session_analysis_by_class"](
        deps["assemble_cause_data"](db, sid)
    )
    error_records = deps["session_error_records"](
        db, sid, store.state_dir.parent
    )
    class_reports = {}
    for name, entry in (raw.get("class_reports") or {}).items():
        if not isinstance(entry, dict):
            continue
        narrative = entry.get("narrative")
        group = groups.get(name)
        digest = ""
        if narrative is not None and class_current and group is not None:
            digest = deps["class_report_input_digest"](
                db,
                sid,
                group,
                cause_digest=deps["class_cause_digest"](
                    error_records,
                    [s.student_id for s in group.students],
                ),
            )
        class_reports[name] = {
            "narrative": narrative,
            "input_digest": digest,
            "prompt_version": prompt_version("class_report"),
            "generated_at": entry.get("generated_at"),
            "status": entry.get("status"),
        }
        if narrative is not None:
            counts["class_reports"] += 1

    # --- 个人报告：personal_index 的旧修订与新算法不同，改用旧算法核对；
    # 修订一致 → 写入新 input_digest（current），否则留空（stale）。 ---
    index_path = cache_dir / "personal_index" / f"session_{int(sid)}.json"
    try:
        index_payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        index_payload = {}
    students_index = (
        index_payload.get("students")
        if isinstance(index_payload, dict)
        else None
    ) or {}
    if students_index:
        data = deps["assemble_session_analysis"](
            db, sid, data_root=deps["infer_data_root"](db.db_path)
        )
        deps["enrich_personal_questions"](db, data, None)
        digests = deps["student_report_digests"](
            db, sid, data, reports_dir=personal_store.base_dir.parent
        )
        session_revision = deps["score_revision"](db, sid)
        for student_key, saved in students_index.items():
            if not str(student_key).isdigit() or not isinstance(saved, dict):
                continue
            student_id = int(student_key)
            student_revision = str(saved.get("student_revision") or "")
            keys = [
                str(saved.get("cache_key") or ""),
                *(
                    _legacy_cache_key(
                        session_id=sid,
                        score_revision=session_revision,
                        rendition_version=version,
                        report_key=f"personal:{student_id}",
                    )
                    for version in _LEGACY_PERSONAL_NARRATIVE_VERSIONS
                ),
                *(
                    _legacy_cache_key(
                        session_id=sid,
                        score_revision=f"student:{student_revision}",
                        rendition_version=version,
                        report_key=f"personal:{student_id}",
                    )
                    for version in _LEGACY_PERSONAL_NARRATIVE_VERSIONS
                ),
            ]
            narrative = next(
                (
                    item
                    for key in dict.fromkeys(keys)
                    if key
                    for item in [_legacy_cache_load(cache_dir, key)]
                    if item is not None
                ),
                None,
            )
            if narrative is None:
                continue
            up_to_date = student_revision == _legacy_student_report_revision(
                db, sid, student_id
            )
            personal_store.save(
                sid,
                student_id,
                narrative=narrative,
                input_digest=digests.get(student_id, "") if up_to_date else "",
                prompt_version=str(saved.get("narrative_version") or "")
                or prompt_version("personal_report"),
            )
            counts["personal_reports"] += 1

    store.save(
        sid,
        cause_analysis=cause_state,
        class_reports=class_reports,
        results_version=RESULTS_VERSION,
        generated_at=raw.get("generated_at"),
        status=raw.get("status"),
    )
    return counts
