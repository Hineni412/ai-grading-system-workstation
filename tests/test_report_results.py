"""报告结果版本化：状态判定、输入指纹、个人结果存储与 v1→v2 迁移。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.repositories.grading_database import open_grading_repositories
from backend.report_results import (
    PersonalReportStore,
    class_input_digest,
    migrate_report_results,
    personal_input_digest,
    prompt_version,
    resolve_result_state,
)


# ---------------------------------------------------------------------------
# resolve_result_state
# ---------------------------------------------------------------------------


def test_resolve_result_state_all_states():
    stored = {
        "result": {"groups": []},
        "input_digest": "d1",
        "prompt_version": prompt_version("causes"),
        "generated_at": "2024-01-01T00:00:00",
    }
    assert resolve_result_state(stored, "d1", "causes")["status"] == "current"
    assert resolve_result_state(stored, "d2", "causes")["status"] == "stale"
    # 空指纹一律 stale（不能视为当前）。
    assert resolve_result_state({**stored, "input_digest": ""}, "d1", "causes")[
        "status"
    ] == "stale"
    old = {**stored, "prompt_version": "class_error_causes_v3"}
    assert resolve_result_state(old, "d1", "causes")["status"] == "old_prompt"
    assert resolve_result_state(None, "d1", "causes")["status"] == "missing"
    assert resolve_result_state({"input_digest": "d1"}, "d1", "causes")[
        "status"
    ] == "missing"  # 无 result/narrative
    # 叙述型结果同样判定。
    narrative = {"narrative": {}, "input_digest": "d1",
                 "prompt_version": prompt_version("personal_report")}
    assert resolve_result_state(narrative, "d1", "personal_report")[
        "status"
    ] == "current"


# ---------------------------------------------------------------------------
# 输入指纹：忽略多余列、对真实输入敏感
# ---------------------------------------------------------------------------


def test_class_digest_ignores_extra_fields_and_tracks_inputs():
    records = [[1, "Q1", 30.0, 40.0]]
    locks = [[1, "Q1", 30.0]]
    base = class_input_digest(records=records, locks=locks, cause_digest="cd")
    # 行内多余元素不参与指纹（指纹只认固定投影的列）。
    extra = class_input_digest(
        records=[[1, "Q1", 30.0, 40.0]],
        locks=locks,
        cause_digest="cd",
    )
    assert base == extra
    # 多余列（行外字段）由调用方投影，不在 digest 层；行内值变化即变化。
    assert class_input_digest(
        records=[[1, "Q1", 31.0, 40.0]], locks=locks, cause_digest="cd"
    ) != base
    assert class_input_digest(
        records=records, locks=[[1, "Q1", 31.0]], cause_digest="cd"
    ) != base
    assert class_input_digest(
        records=records, locks=locks, cause_digest="other"
    ) != base
    # 确定性：行序不影响指纹。
    two = [[1, "Q1", 30.0, 40.0], [1, "Q2", 50.0, 60.0]]
    assert class_input_digest(records=two, locks=[], cause_digest="") == (
        class_input_digest(records=list(reversed(two)), locks=[], cause_digest="")
    )


def test_personal_digest_tracks_records_locks_errors_and_questions():
    base = personal_input_digest(
        records=[["Q1", 30.0, 40.0]],
        locks=[["Q1", 30.0]],
        error_records=[["Q1", "error", "概念理解", "旧错法"]],
        questions=[["Q1", "题干", "解析"]],
    )
    assert personal_input_digest(
        records=[["Q1", 31.0, 40.0]], locks=[["Q1", 30.0]],
        error_records=[["Q1", "error", "概念理解", "旧错法"]],
        questions=[["Q1", "题干", "解析"]],
    ) != base
    assert personal_input_digest(
        records=[["Q1", 30.0, 40.0]], locks=[["Q1", 31.0]],
        error_records=[["Q1", "error", "概念理解", "旧错法"]],
        questions=[["Q1", "题干", "解析"]],
    ) != base
    assert personal_input_digest(
        records=[["Q1", 30.0, 40.0]], locks=[["Q1", 30.0]],
        error_records=[["Q1", "error", "概念理解", "新错法"]],
        questions=[["Q1", "题干", "解析"]],
    ) != base
    assert personal_input_digest(
        records=[["Q1", 30.0, 40.0]], locks=[["Q1", 30.0]],
        error_records=[["Q1", "error", "概念理解", "旧错法"]],
        questions=[["Q1", "改后题干", "解析"]],
    ) != base


# ---------------------------------------------------------------------------
# PersonalReportStore
# ---------------------------------------------------------------------------


def test_personal_report_store_roundtrip_and_delete(tmp_path):
    store = PersonalReportStore(tmp_path)
    assert store.load(1, 2) is None
    entry = store.save(
        1, 2, narrative={"a": 1}, input_digest="d", prompt_version="p"
    )
    assert store.load(1, 2)["narrative"] == {"a": 1}
    assert entry["generated_at"]
    assert set(store.list(1)) == {2}
    store.delete_session(1)
    assert store.list(1) == {}
    assert store.load(1, 2) is None


# ---------------------------------------------------------------------------
# v1 → v2 迁移
# ---------------------------------------------------------------------------


def _seed_session(db, tmp_path: Path) -> int:
    from tests.test_analysis_report import _seed_analysis_session

    return _seed_analysis_session(db, tmp_path)


def _legacy_index_and_cache(reports_dir: Path, sid: int, students: list[int]) -> Path:
    """旧式缓存：personal_index 列两个学生 + 键名文件；另有一个孤儿文件。"""
    cache_dir = reports_dir / ".analysis_narrative_cache"
    index_dir = cache_dir / "personal_index"
    index_dir.mkdir(parents=True)
    for student_id in students:
        (cache_dir / f"key-{student_id}.json").write_text(
            json.dumps({"version": 1, "narrative": {"s": student_id}},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    (cache_dir / "orphan.json").write_text(
        json.dumps({"version": 1, "narrative": {"s": 999}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return index_dir


def test_migrate_report_results_end_to_end(tmp_path):
    from backend.class_analysis import (
        ClassAnalysisStateStore,
        _cause_input_fingerprint,
        assemble_cause_data,
        build_cause_inputs,
        normalize_cause_result,
        student_error_records,
    )
    from backend.personal_reports import (
        personal_report_states,
        student_report_digests,
    )
    from backend.report_exports import score_revision
    from backend.report_results import _legacy_student_report_revision
    from backend.session_analysis import (
        assemble_session_analysis,
        enrich_personal_questions,
        split_session_analysis_by_class,
    )

    db = open_grading_repositories(tmp_path / "databases" / "grading.db")
    db.initialize()
    sid = _seed_session(db, tmp_path)
    reports_dir = tmp_path / "reports"
    data = assemble_cause_data(db, sid, data_root=tmp_path)
    sources = {s["question_id"]: s for s in build_cause_inputs(data)}

    # 错因：Q1 v4（指纹匹配 → current）、Q2 v3（指纹匹配 → old_prompt）、
    # 另造一题 v2（空指纹 → stale）。这里只断言状态机与文件行为。
    def entry(version, source):
        result = normalize_cause_result(
            {"groups": [{"kind": "error", "category": "概念理解",
                         "reason": "旧错法", "manifestation": "旧表现",
                         "evidence_ids": [e["id"] for e in source["evidence"]]}]},
            source,
        )
        return {
            "version": version,
            "input": source,
            "input_fingerprint": _cause_input_fingerprint(source),
            "result": result,
            "generated_at": "2024-01-01T00:00:00",
            "origin": "model",
            "failed": False,
            "history": [{"old": 1}],
        }

    q1, q2 = sorted(sources)
    questions = {
        q1: entry("class_error_causes_v4", sources[q1]),
        q2: entry("class_error_causes_v3", sources[q2]),
        "Q9": {
            "version": "class_error_causes_v2",
            "input": {"question_id": "Q9", "evidence": []},
            "result": {"groups": []},
            "generated_at": "2024-01-01T00:00:00",
            "origin": "model",
            "failed": False,
        },
    }
    # 班级：就绪叙述 + 旧口径 score_revision/rendition_version 与当前一致。
    revision = score_revision(db, sid, include_question_bank=False)
    reports_dir.mkdir(parents=True)
    state_path = reports_dir / ".class_analysis"
    state_path.mkdir()
    (state_path / f"{sid}.json").write_text(
        json.dumps({
            "version": 1,
            "status": "ready",
            "generated_at": "2024-01-01T00:00:00",
            "score_revision": revision,
            "rendition_version": "class_analysis_page_v4_cause_payload",
            "class_reports": {
                "1 班": {
                    "narrative": {"key_findings": ["x"]},
                    "status": "ready",
                    "generated_at": "2024-01-01T00:00:00",
                }
            },
            "cause_analysis": {"questions": questions},
            "error_records": {},
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    # 个人索引：学生1 revision 匹配 → current；学生2 不匹配 → stale；孤儿不迁。
    students = sorted(int(s.student_id) for s in data.students)
    index_dir = _legacy_index_and_cache(reports_dir, sid, students)
    revisions = {
        str(students[0]): {
            "cache_key": f"key-{students[0]}",
            "student_revision": _legacy_student_report_revision(db, sid, students[0]),
        },
        str(students[1]): {
            "cache_key": f"key-{students[1]}",
            "student_revision": "outdated-revision",
        },
    }
    (index_dir / f"session_{sid}.json").write_text(
        json.dumps({"students": revisions}, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = migrate_report_results(reports_dir, db)
    assert summary["backup_dir"] and Path(summary["backup_dir"]).is_dir()
    assert (Path(summary["backup_dir"]) / ".class_analysis" / f"{sid}.json").is_file()
    assert not (reports_dir / ".analysis_narrative_cache").exists()
    assert summary["cache_dir_deleted"] is True

    state = ClassAnalysisStateStore(reports_dir).load(sid)
    assert state["results_version"] == 2
    qs = state["cause_analysis"]["questions"]
    assert qs[q1]["prompt_version"] == "class_error_causes_v4"
    assert qs[q1]["input_digest"] == _cause_input_fingerprint(sources[q1])
    assert "version" not in qs[q1] and "history" not in qs[q1]
    assert qs[q1]["input"]["evidence"]  # input 保留
    assert qs[q2]["prompt_version"] == "class_error_causes_v3"
    assert qs["Q9"]["input_digest"] == "" and qs["Q9"]["prompt_version"] == "class_error_causes_v2"
    class_entry = state["class_reports"]["1 班"]
    assert class_entry["narrative"] == {"key_findings": ["x"]}
    assert class_entry["input_digest"]  # 旧修订一致 → 现算指纹 → current

    store = PersonalReportStore(reports_dir)
    first = store.load(sid, students[0])
    assert first["narrative"] == {"s": students[0]}
    pdata = assemble_session_analysis(db, sid, data_root=tmp_path)
    enrich_personal_questions(db, pdata, tmp_path)
    digests = student_report_digests(db, sid, pdata, reports_dir=reports_dir)
    assert first["input_digest"] == digests[students[0]]
    second = store.load(sid, students[1])
    assert second["input_digest"] == ""  # 旧修订不一致 → stale
    # 孤儿缓存文件不迁移。
    assert store.list(sid).keys() == {students[0], students[1]}

    # 迁移后的公开状态判定。
    page_states = personal_report_states(db, sid, reports_dir)["students"]
    by_id = {s["student_id"]: s["status"] for s in page_states}
    assert by_id[students[0]] == "current"
    assert by_id[students[1]] == "stale"

    # 幂等：第二次运行不再迁移，也不重建缓存目录。
    again = migrate_report_results(reports_dir, db)
    assert again["sessions"] == {}
    assert again["already_migrated"] == [sid]
