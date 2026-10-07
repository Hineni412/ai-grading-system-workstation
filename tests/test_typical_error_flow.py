"""题目预测错法在考试作答、教师调整和重分析间的用户结果。"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from backend.scan_grading.ai_grader import _normalize_grading_errors
from backend.class_analysis import (
    CAUSE_ANALYSIS_VERSION, CausePatternEditError,
    ClassAnalysisStateStore, edit_cause_pattern,
    plan_cause_question,
)

# 旧版提示词结果在会话内仍可修改错法名（迁移后 prompt_version 保留原值）。
CAUSE_PRE_STEP_VERSION = "class_error_causes_v3"
from backend.error_patterns import (
    OPTION_ANALYSIS_VERSION, bank_confirmed_triggers, question_fingerprint, save_option_analysis,
    synthesize_option_result,
)
from question_bank.database.schema import connect, initialize_database
from question_bank.services.error_pattern_service import (
    list_patterns,
    record_auto_patterns,
    reject_pattern,
    rename_patterns,
)
from question_bank.services.predicted_error_patterns import record_predicted_patterns
from question_bank.services.question_read_service import QuestionBankReadService

_SKILL_KEY = "sk_bnu24_math_g7_lower_1_1_01"
_SKILL_KEY_2 = "sk_bnu24_math_g7_lower_1_1_02"
_SKILL_EVIDENCE_ID = "e" * 64


def _choice_question(tmp_path):
    path = tmp_path / "databases" / "question_bank.db"
    initialize_database(path)
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO questions (question_number, question_text, answer_text, question_type)"
            " VALUES ('1', '题干\nA. 1\nB. 2\nC. 3\nD. 4', '【答案】B', '选择题')"
        )
        qid = int(conn.execute("SELECT id FROM questions").fetchone()[0])
    return path, qid


def _predictions():
    return [
        {
            "category": "计算与化简",
            "pattern": f"误选{letter}的计算",
            "explanation": f"选{letter}时可能漏算一步",
            "trigger_kind": "option",
            "trigger_value": letter,
        }
        for letter in "ACD"
    ]


def test_report_preparation_reuses_current_results_without_model(tmp_path, monkeypatch):
    import backend.class_analysis as causes
    path, _qid = _choice_question(tmp_path)
    source = {"question_id": "Q1", "question_text": "题干", "reference_analysis": "解析", "evidence": []}
    fingerprint = causes._cause_input_fingerprint(source)
    store = ClassAnalysisStateStore(tmp_path / "reports")
    store.save(3, cause_analysis={"questions": {"Q1": {"prompt_version": CAUSE_ANALYSIS_VERSION,
               "input": source, "input_digest": fingerprint, "result": {"groups": []}}}},
               error_records={"Q1": {"input_fingerprint": fingerprint, "records": []}})
    monkeypatch.setattr(causes, "assemble_cause_data", lambda *a, **k: SimpleNamespace(questions=[], students=[]))
    monkeypatch.setattr(causes, "build_cause_inputs", lambda *a, **k: [source])
    context = SimpleNamespace(payload={"session_id": 3}, raise_if_cancelled=lambda: None)
    result = causes.run_cause_analysis(context, db=SimpleNamespace(db_path=path.parent / "grading_system.db"),
        data_root=tmp_path, store=store, retry_failed=False,
        llm_client_factory=lambda: pytest.fail("已有有效整理不能重新付费调用"))
    assert result["status"] == "ready"


def test_diagnosis_and_history_ignore_stale_raw_state(tmp_path, monkeypatch):
    from integration.diagnosis_profile_service import DiagnosisProfileService
    from backend.class_analysis import collect_student_error_index
    import backend.class_analysis as causes
    store = ClassAnalysisStateStore(tmp_path / "reports")
    store.save(3, error_records={"Q1": {"records": [{"student_id": 1, "pattern": "过期错因"}]}})
    service = object.__new__(DiagnosisProfileService)
    service.data_root = tmp_path
    service.db = object()
    monkeypatch.setattr(causes, "session_error_records", lambda *args, **kwargs: {})
    assert service._error_cause_index([3]) == {}
    assert collect_student_error_index(store, [3], db=service.db) == {}
    current = {1: {"Q1": [{"student_id": 1, "kind": "error", "category": "计算与化简", "pattern": "计算漏项"}]}}
    monkeypatch.setattr(causes, "session_error_records", lambda *args, **kwargs: current)
    assert service._error_cause_index([3])[(3, 1, "Q1")][0]["pattern"] == "计算漏项"
    assert collect_student_error_index(store, [3], db=service.db)[1]["patterns"] == {"计算漏项": {3}}


def test_backfill_skips_stale_state_and_is_idempotent(tmp_path):
    from backend.class_analysis import _cause_input_fingerprint
    from backend.error_patterns import sync_session_patterns_to_bank
    path, qid = _choice_question(tmp_path)
    source = {"question_id": "Q1", "question_text": "题干\nA. 1\nB. 2\nC. 3\nD. 4", "canonical_answer": "B",
              "evidence": [{"id": "E1", "student_answer": "C"}]}
    store = ClassAnalysisStateStore(tmp_path / "reports")
    store.save(3, cause_analysis={"questions": {"Q1": {"prompt_version": CAUSE_ANALYSIS_VERSION,
               "input": source, "input_digest": _cause_input_fingerprint(source), "origin": "option_map",
               "result": {"groups": [{"evidence_ids": ["E1"]}]}}}},
               option_analysis={"Q1": {"version": OPTION_ANALYSIS_VERSION,
                 "input_fingerprint": question_fingerprint(source["question_text"], "B"), "source": "model",
                 "analysis": {"C": {"category": "计算与化简", "pattern": "误选C"}}}})
    context = {"Q1": {"bank_id": qid}}
    assert sync_session_patterns_to_bank(store, 3, path, context, current_sources=[{**source, "canonical_answer": "A"}]) == 0
    assert sync_session_patterns_to_bank(store, 3, path, context, current_sources=[source]) == 1
    assert sync_session_patterns_to_bank(store, 3, path, context, current_sources=[source]) == 0
    with connect(path) as conn:
        row = list_patterns(conn, [qid])[qid][0]
        assert row["occurrences"] == [{"session_id": 3, "question_id": "Q1"}]


def test_step_organized_result_syncs_rows_per_evidence_point(tmp_path):
    """v4 整理结果按物化记录的证据点写 step 触发行；无点成员补 observation。"""
    from backend.class_analysis import _cause_input_fingerprint
    from backend.error_patterns import sync_session_patterns_to_bank

    path, qid = _choice_question(tmp_path)
    with connect(path) as conn:
        conn.execute("UPDATE questions SET question_type='解答题' WHERE id=?", (qid,))
    source = {
        "question_id": "Q2", "question_text": "解答题题干", "canonical_answer": "AB=BD+DH",
        "evidence": [
            {"id": "E1", "student_answer": "作答一", "failed_steps": [
                {"id": "E1.S1", "step_id": "S2"},
                {"id": "E1.S2", "step_id": "S4"},
            ]},
            {"id": "E2", "student_answer": "作答二"},
        ],
    }
    fingerprint = _cause_input_fingerprint(source)
    store = ClassAnalysisStateStore(tmp_path / "reports")
    store.save(3, cause_analysis={"questions": {"Q2": {
        "prompt_version": CAUSE_ANALYSIS_VERSION, "origin": "model",
        "input": source, "input_digest": fingerprint,
        "result": {"groups": [
            {"kind": "process", "category": "过程与依据", "reason": "缺少关系式",
             "evidence_ids": ["E1.S1"], "step_ids": ["S2"],
             "manifestations": [{"description": "未列关系", "source_question_id": None,
                                 "evidence_ids": ["E1.S1"]}]},
            {"kind": "process", "category": "书写与规范", "reason": "结论不规范",
             "evidence_ids": ["E1.S2"], "step_ids": ["S4"],
             "manifestations": [{"description": "未写结论", "source_question_id": None,
                                 "evidence_ids": ["E1.S2"]}]},
            {"kind": "error", "category": "概念理解", "reason": "概念用错",
             "evidence_ids": ["E2"], "step_ids": [],
             "manifestations": [{"description": "用错性质", "source_question_id": None,
                                 "evidence_ids": ["E2"]}]},
        ]},
    }}}, error_records={"Q2": {
        "input_fingerprint": fingerprint,
        "records": [
            {"student_id": 1, "question_id": "Q2", "kind": "process",
             "category": "过程与依据", "pattern": "缺少关系式",
             "step_id": "S2", "part_id": "Q2", "evidence_point_ids": ["p2"],
             "evidence_version_id": "ev-q2"},
            {"student_id": 1, "question_id": "Q2", "kind": "process",
             "category": "书写与规范", "pattern": "结论不规范",
             "step_id": "S4", "part_id": "Q2", "evidence_point_ids": ["p4a", "p4b"],
             "evidence_version_id": "ev-q2"},
            {"student_id": 1, "question_id": "Q2", "kind": "error",
             "category": "概念理解", "pattern": "概念用错",
             "step_id": None, "part_id": None, "evidence_point_ids": None,
             "evidence_version_id": None},
        ],
    }})
    context = {"Q2": {"bank_id": qid}}
    assert sync_session_patterns_to_bank(
        store, 3, path, context, current_sources=[source]) == 4
    with connect(path) as conn:
        rows = list_patterns(conn, [qid], statuses=("confirmed", "candidate"))[qid]
    by_trigger = {(row["trigger_kind"], row["trigger_value"]): row for row in rows}
    assert set(by_trigger) == {
        ("step", "p2"), ("step", "p4a"), ("step", "p4b"), ("observation", ""),
    }
    assert by_trigger[("step", "p2")]["pattern"] == "缺少关系式"
    assert {by_trigger[("step", "p4a")]["pattern"],
            by_trigger[("step", "p4b")]["pattern"]} == {"结论不规范"}
    assert by_trigger[("observation", "")]["pattern"] == "概念用错"
    # 重复同步不重复建行。
    assert sync_session_patterns_to_bank(
        store, 3, path, context, current_sources=[source]) == 0


def test_failed_new_draft_does_not_reopen_review_when_teacher_version_still_matches(tmp_path):
    from question_bank.training_criteria import QuestionAnalysisInputLoader

    path, qid = _choice_question(tmp_path)
    current_hash = QuestionAnalysisInputLoader(db_path=path, data_root=tmp_path).load([qid])[0].criterion_source_content_hash
    criteria_json = json.dumps({"points": [{"point_id": "TEST-answer", "target": "选择正确答案",
        "observable_evidence": "选择B", "equivalent_rules": [], "counterexamples": []}]})
    with connect(path) as conn:
        for vid, status, quality in (("a"*64, "approved", "passed"), ("b"*64, "proposed", "failed")):
            conn.execute("""INSERT INTO training_criterion_versions
                (version_id,question_id,version_number,source_content_hash,schema_version,status,
                 source_kind,source_reference,criteria_json,criteria_hash,quality_status,created_by)
                VALUES(?,?,? ,?,'judgment-points-v1',?,'teacher_manual',?,?,?,?,'synthetic')""",
                (vid, qid, 1 if status == "approved" else 2, current_hash, status, vid, criteria_json, "d"*64, quality))
        conn.execute("INSERT INTO training_criterion_heads(question_id,current_version_id,approved_version_id,current_source_hash) VALUES(?,?,?,?)",
                     (qid, "b"*64, "a"*64, current_hash))
    reader = QuestionBankReadService(path)
    assert reader.get_question(qid)["criteria_needs_review"] is False
    with connect(path) as conn:
        conn.execute("UPDATE questions SET question_text='TEST-题目正文已改变' WHERE id=?", (qid,))
    assert reader.get_question(qid)["criteria_needs_review"] is True


def test_predicted_choice_maps_selected_c_without_inventing_other_students(tmp_path):
    path, qid = _choice_question(tmp_path)
    with connect(path) as conn:
        assert record_predicted_patterns(conn, qid, _predictions()) == 3

    source = {
        "question_id": "Q1",
        "question_text": "题干\nA. 1\nB. 2\nC. 3\nD. 4",
        "canonical_answer": "B",
        "evidence": [{"id": "e1", "student_answer": "C"}],
    }
    store = ClassAnalysisStateStore(tmp_path / "reports")
    plan = plan_cause_question(
        store, 7, source, qtype="choice", option_scope=True,
        ctx={"bank_id": qid, "bank_ids": {qid}, "linked": set()},
        confirmed_by_bank=bank_confirmed_triggers(path, [qid]),
        question_bank_path=path, retry_failed=True,
    )
    assert plan["path"] == "option" and plan["needs_call"] is False
    result = synthesize_option_result(source, plan["option"]["patterns"])
    assert [(group["reason"], group["evidence_ids"]) for group in result["groups"]] == [
        ("误选C的计算", ["e1"])
    ]

    occurrence = {"session_id": 7, "question_id": "Q1"}
    actual = [{
        "question_id": qid, "category": "计算与化简", "pattern": "误选C的计算",
        "trigger_kind": "option", "trigger_value": "C", "source": "ai_auto",
        "occurrence": occurrence,
    }]
    assert record_auto_patterns(path, actual) == 0
    assert record_auto_patterns(path, actual) == 0
    with connect(path) as conn:
        rows = list_patterns(conn, [qid], statuses=("candidate", "confirmed"))[qid]
    assert len(rows) == 3
    by_option = {row["trigger_value"]: row for row in rows}
    assert by_option["C"]["status"] == "confirmed"
    assert by_option["C"]["source"] == "ai_predicted"
    assert by_option["C"]["occurrences"] == [occurrence]
    assert all(not by_option[letter]["occurrences"] for letter in "AD")


def test_actual_exam_pattern_replaces_different_prediction_for_same_option(tmp_path):
    path, qid = _choice_question(tmp_path)
    with connect(path) as conn:
        record_predicted_patterns(conn, qid, _predictions())

    occurrence = {"session_id": 9, "question_id": "Q1"}
    actual = [{
        "question_id": qid, "category": "审题与条件",
        "pattern": "误把题干中的条件看成结论",
        "explanation": "选 C 的实际作答显示读错条件",
        "trigger_kind": "option", "trigger_value": "C", "source": "ai_auto",
        "occurrence": occurrence,
    }]
    assert record_auto_patterns(path, actual) == 0
    assert record_auto_patterns(path, actual) == 0
    with connect(path) as conn:
        rows = list_patterns(conn, [qid], statuses=("candidate", "confirmed"))[qid]
    assert len(rows) == 3
    by_option = {row["trigger_value"]: row for row in rows}
    assert by_option["C"]["pattern"] == "误把题干中的条件看成结论"
    assert by_option["C"]["category"] == "审题与条件"
    assert by_option["C"]["source"] == "ai_auto"
    assert by_option["C"]["status"] == "confirmed"
    assert by_option["C"]["occurrences"] == [occurrence]


def test_unexplained_option_stays_uncertain_without_repeated_model_call(tmp_path):
    path, qid = _choice_question(tmp_path)
    source = {
        "question_id": "Q1", "question_text": "题干\nA. 1\nB. 2\nC. 3\nD. 4",
        "canonical_answer": "B", "evidence": [{"id": "e1", "student_answer": "C"}],
    }
    store = ClassAnalysisStateStore(tmp_path / "reports")
    save_option_analysis(store, 7, "Q1", {
        "version": OPTION_ANALYSIS_VERSION,
        "input_fingerprint": question_fingerprint(source["question_text"], "B"),
        "bank_question_id": qid,
        "analysis": {"A": {
            "category": "计算与化简", "pattern": "误选A的计算",
            "explanation": "选择A",
        }},
        "source": "model", "failed": False,
    })
    plan = plan_cause_question(
        store, 7, source, qtype="choice", option_scope=True,
        ctx={"bank_id": qid, "bank_ids": {qid}, "linked": set()},
        confirmed_by_bank=bank_confirmed_triggers(path, [qid]),
        question_bank_path=path, retry_failed=True,
    )
    assert plan["path"] == "option" and plan["needs_call"] is False
    result = synthesize_option_result(source, plan["option"]["patterns"])
    assert result["groups"] == []
    assert result["uncertain_ids"] == ["e1"]


def test_teacher_edit_and_rejection_survive_reanalysis(tmp_path):
    path, qid = _choice_question(tmp_path)
    with connect(path) as conn:
        record_predicted_patterns(conn, qid, _predictions())
        rows = list_patterns(conn, [qid], statuses=("candidate",))[qid]
    by_option = {row["trigger_value"]: row for row in rows}
    assert rename_patterns(
        path, question_ids=[qid], pattern_id=by_option["A"]["id"],
        old_pattern="误选A的计算", new_pattern="漏看题干条件",
        category="审题与条件",
    ) == 1
    assert reject_pattern(path, pattern_id=by_option["D"]["id"], question_id=qid)
    with connect(path) as conn:
        assert record_predicted_patterns(conn, qid, _predictions()) == 1
        active = list_patterns(conn, [qid], statuses=("candidate", "confirmed"))[qid]
        rejected = conn.execute(
            "SELECT status FROM question_error_patterns WHERE id=?",
            (by_option["D"]["id"],),
        ).fetchone()[0]
    assert rejected == "rejected"
    assert {row["trigger_value"] for row in active} == {"A", "C"}
    assert next(row for row in active if row["trigger_value"] == "A")["pattern"] == "漏看题干条件"


def test_rejected_option_stays_uncertain_when_model_fills_other_options(monkeypatch):
    import backend.class_analysis as cause_module
    import backend.error_patterns as pattern_module

    captured = {}
    monkeypatch.setattr(pattern_module, "save_option_analysis", lambda *args: None)
    monkeypatch.setattr(
        cause_module, "save_cause_result",
        lambda *args, **kwargs: captured.update(result=args[3]),
    )
    plan = {
        "text": "题干\nA. 1\nB. 2\nC. 3\nD. 4", "correct": "B",
        "letters": list("ABCD"), "required": ["A", "D"], "blocked": ["C"],
        "base_patterns": {"A": {"category": "计算与化简", "pattern": "已有A"}},
        "patterns": None, "fingerprint": "synthetic", "bank_id": 1,
    }
    client = SimpleNamespace(json_from_text=lambda *args, **kwargs: {
        "options": [
            {"option": letter, "category": "计算与化简", "pattern": f"模型错法{letter}"}
            for letter in "ACD"
        ],
    })
    cause_module._run_option_plan(
        store=None, session_id=1,
        source={"question_id": "Q1", "evidence": [{"id": "e1", "student_answer": "C"}]},
        data=None, plan=plan, get_client=lambda: client,
    )
    assert captured["result"]["groups"] == []
    assert captured["result"]["uncertain_ids"] == ["e1"]


def test_teacher_option_is_only_current_detail_row_and_receives_occurrence(tmp_path):
    path, qid = _choice_question(tmp_path)
    with connect(path) as conn:
        record_predicted_patterns(conn, qid, _predictions())
        predicted_c = next(
            row for row in list_patterns(conn, [qid], statuses=("candidate",))[qid]
            if row["trigger_value"] == "C"
        )
        conn.execute(
            "INSERT INTO question_error_patterns"
            " (question_id, category, pattern, trigger_kind, trigger_value, status, source)"
            " VALUES (?, '计算与化简', '旧的选项C预测', 'option', 'C', 'confirmed', 'ai_pre_analysis')",
            (qid,),
        )
    rename_patterns(
        path, question_ids=[qid], pattern_id=predicted_c["id"],
        old_pattern=predicted_c["pattern"], new_pattern="教师指定C", category="审题与条件",
    )
    occurrence = {"session_id": 1, "question_id": "Q1"}
    record_auto_patterns(path, [{
        "question_id": qid, "category": "审题与条件", "pattern": "教师指定C",
        "trigger_kind": "option", "trigger_value": "C", "occurrence": occurrence,
    }])
    detail = QuestionBankReadService(path).get_question(qid)
    current_c = [row for row in detail["error_patterns"] if row["trigger_value"] == "C"]
    assert len(current_c) == 1
    assert current_c[0]["pattern"] == "教师指定C"
    assert current_c[0]["has_evidence"] is True
    with connect(path) as conn:
        teacher = next(
            row for row in list_patterns(conn, [qid])[qid] if row["source"] == "teacher_edit"
        )
    assert teacher["occurrences"] == [occurrence]


def test_reanalysis_after_question_edit_can_reuse_name_and_keep_occurrence(tmp_path):
    path, qid = _choice_question(tmp_path)
    with connect(path) as conn:
        record_predicted_patterns(conn, qid, _predictions())
    occurrence = {"session_id": 1, "question_id": "Q1"}
    record_auto_patterns(path, [{
        "question_id": qid, "category": "计算与化简", "pattern": "误选C的计算",
        "trigger_kind": "option", "trigger_value": "C", "occurrence": occurrence,
    }])
    with connect(path) as conn:
        conn.execute(
            "UPDATE questions SET question_text='新题干\nA. 1\nB. 2\nC. 3\nD. 4' WHERE id=?",
            (qid,),
        )
        record_predicted_patterns(conn, qid, _predictions())
        rows = list_patterns(conn, [qid], statuses=("candidate", "confirmed"))[qid]
    current_c = [row for row in rows if row["trigger_value"] == "C"]
    assert len(current_c) == 1
    assert current_c[0]["pattern"] == "误选C的计算"
    assert current_c[0]["occurrences"] == [occurrence]


def test_teacher_can_restore_previous_name(tmp_path):
    path, qid = _choice_question(tmp_path)
    with connect(path) as conn:
        record_predicted_patterns(conn, qid, _predictions())
        original = next(
            row for row in list_patterns(conn, [qid], statuses=("candidate",))[qid]
            if row["trigger_value"] == "C"
        )
    assert rename_patterns(
        path, question_ids=[qid], pattern_id=original["id"],
        old_pattern=original["pattern"], new_pattern="临时改名", category="计算与化简",
    ) == 1
    assert rename_patterns(
        path, question_ids=[qid], old_pattern="临时改名",
        new_pattern=original["pattern"], category="计算与化简",
    ) == 1
    assert next(
        row for row in QuestionBankReadService(path).get_question(qid)["error_patterns"]
        if row["trigger_value"] == "C"
    )["pattern"] == original["pattern"]


def test_class_edit_uses_current_option_after_detail_edit(tmp_path):
    path, qid = _choice_question(tmp_path)
    with connect(path) as conn:
        record_predicted_patterns(conn, qid, _predictions())
    old = "误选C的计算"
    rename_patterns(
        path, question_ids=[qid], old_pattern=old,
        new_pattern="题目详情改名", category="计算与化简",
    )
    source = {
        "question_id": "Q1", "question_text": "题干\nA. 1\nB. 2\nC. 3\nD. 4",
        "canonical_answer": "B", "evidence": [{"id": "e1", "student_answer": "C"}],
    }
    store = ClassAnalysisStateStore(tmp_path / "reports")
    store.save(7, cause_analysis={"questions": {"Q1": {
        "version": CAUSE_ANALYSIS_VERSION, "origin": "option_map", "input": source,
        "result": {"groups": [{
            "kind": "error", "category": "计算与化简", "reason": old,
            "evidence_ids": ["e1"],
        }]},
    }}}, option_analysis={"Q1": {
        "version": OPTION_ANALYSIS_VERSION,
        "input_fingerprint": question_fingerprint(source["question_text"], "B"),
        "bank_question_id": qid, "source": "bank_confirmed", "failed": False,
        "analysis": {"C": {"category": "计算与化简", "pattern": old}},
    }})
    edit_cause_pattern(
        store, 7, question_id="Q1", kind="error", reason=old,
        new_reason="班级页面再次改名", category="计算与化简", question_bank_path=path,
        bank_context={"Q1": {"bank_id": qid, "bank_ids": {qid}, "linked": set()}},
    )
    detail = QuestionBankReadService(path).get_question(qid)
    assert next(
        row for row in detail["error_patterns"] if row["trigger_value"] == "C"
    )["pattern"] == "班级页面再次改名"


@pytest.mark.parametrize("trigger_kind,trigger_value", [
    ("step", "s1"), ("observation", ""),
])
def test_class_edit_follows_subjective_rename_without_touching_peer_pattern(
    tmp_path, trigger_kind, trigger_value,
):
    path, qid = _choice_question(tmp_path)
    with connect(path) as conn:
        conn.execute("UPDATE questions SET question_type='解答题' WHERE id=?", (qid,))
    old = "漏写必要条件"
    peer = "误用定理"
    occurrence = {"session_id": 7, "question_id": "Q1"}
    record_auto_patterns(path, [
        {
            "question_id": qid, "category": "过程与依据", "pattern": pattern,
            "trigger_kind": trigger_kind, "trigger_value": trigger_value,
            "occurrence": occurrence,
        }
        for pattern in (old, peer)
    ])
    rename_patterns(
        path, question_ids=[qid], old_pattern=old,
        new_pattern="题目详情初次改名", category="过程与依据",
    )
    rename_patterns(
        path, question_ids=[qid], old_pattern="题目详情初次改名",
        new_pattern="题目详情再次改名", category="过程与依据",
    )
    store = ClassAnalysisStateStore(tmp_path / "reports")
    store.save(7, cause_analysis={"questions": {"Q1": {
        "version": CAUSE_PRE_STEP_VERSION, "origin": "model",
        "result": {"groups": [
            {
                "kind": "process", "category": "过程与依据", "reason": old,
                "step_id": trigger_value or None, "evidence_ids": ["e1"],
            },
            {
                "kind": "process", "category": "过程与依据", "reason": peer,
                "step_id": trigger_value or None, "evidence_ids": ["e2"],
            },
        ]},
    }}})
    edit_cause_pattern(
        store, 7, question_id="Q1", kind="process", reason=old,
        new_reason="班级页面改名", category="过程与依据", question_bank_path=path,
        bank_context={"Q1": {"bank_id": qid, "bank_ids": {qid}, "linked": set()}},
    )
    current = QuestionBankReadService(path).get_question(qid)["error_patterns"]
    assert {row["pattern"] for row in current} == {"班级页面改名", peer}


def test_ambiguous_older_subjective_rename_does_not_change_peer(tmp_path):
    path, qid = _choice_question(tmp_path)
    occurrence = {"session_id": 7, "question_id": "Q1"}
    record_auto_patterns(path, [
        {
            "question_id": qid, "category": "过程与依据", "pattern": pattern,
            "trigger_kind": "step", "trigger_value": "s1", "occurrence": occurrence,
        }
        for pattern in ("原有错法", "同判定点另一错法")
    ])
    rename_patterns(
        path, question_ids=[qid], old_pattern="原有错法",
        new_pattern="详情改名", category="过程与依据",
    )
    with connect(path) as conn:
        conn.execute(
            "UPDATE question_error_patterns SET confirm_token=NULL"
            " WHERE question_id=? AND pattern='详情改名'",
            (qid,),
        )
    store = ClassAnalysisStateStore(tmp_path / "reports")
    store.save(7, cause_analysis={"questions": {"Q1": {
        "version": CAUSE_PRE_STEP_VERSION, "origin": "model",
        "result": {"groups": [{
            "kind": "process", "category": "过程与依据", "reason": "原有错法",
            "step_id": "s1", "evidence_ids": ["e1"],
        }]},
    }}})
    with pytest.raises(CausePatternEditError) as exc:
        edit_cause_pattern(
            store, 7, question_id="Q1", kind="process", reason="原有错法",
            new_reason="班级页面改名", category="过程与依据",
            question_bank_path=path,
            bank_context={"Q1": {"bank_id": qid, "bank_ids": {qid}, "linked": set()}},
        )
    assert exc.value.code == "cause_pattern_bank_changed"
    current = QuestionBankReadService(path).get_question(qid)["error_patterns"]
    assert {row["pattern"] for row in current} == {"详情改名", "同判定点另一错法"}


def test_prediction_from_old_question_content_is_not_read_as_current(tmp_path):
    path, qid = _choice_question(tmp_path)
    with connect(path) as conn:
        record_predicted_patterns(conn, qid, _predictions())
        conn.execute("UPDATE questions SET question_text='已修订题干' WHERE id=?", (qid,))
        assert list_patterns(conn, [qid], statuses=("candidate",))[qid] == []


def test_question_detail_edit_reject_and_reopen(tmp_path):
    from fastapi.testclient import TestClient
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_question_bank_db_path, get_question_bank_read_service,
    )

    path, qid = _choice_question(tmp_path)
    with connect(path) as conn:
        record_predicted_patterns(conn, qid, _predictions())
    app = create_app()
    app.dependency_overrides[get_question_bank_db_path] = lambda: path
    app.dependency_overrides[get_question_bank_read_service] = lambda: QuestionBankReadService(path)
    client = TestClient(app)

    detail = client.get(f"/api/question-bank/questions/{qid}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["wrong_option_letters"] == ["A", "C", "D"]
    options = {item["trigger_value"]: item for item in detail.json()["error_patterns"]}
    assert options["C"]["has_evidence"] is False
    response = client.patch(
        f"/api/question-bank/questions/{qid}/error-patterns/{options['C']['id']}",
        json={
            "action": "edit", "expected_pattern": options["C"]["pattern"],
            "pattern": "把三误认作正确值", "category": "概念理解",
        },
    )
    assert response.status_code == 200, response.text
    reopened = client.get(f"/api/question-bank/questions/{qid}").json()
    current = {item["trigger_value"]: item for item in reopened["error_patterns"]}
    assert current["C"]["pattern"] == "把三误认作正确值"
    assert current["C"]["source"] == "teacher_edit"
    assert bank_confirmed_triggers(path, [qid])[qid]

    response = client.patch(
        f"/api/question-bank/questions/{qid}/error-patterns/{options['D']['id']}",
        json={"action": "reject", "expected_pattern": options["D"]["pattern"]},
    )
    assert response.status_code == 200, response.text
    assert {item["trigger_value"] for item in client.get(
        f"/api/question-bank/questions/{qid}"
    ).json()["error_patterns"]} == {"A", "C"}


def test_grading_category_survives_without_legacy_error_type_tag():
    category, summary, secondary = _normalize_grading_errors(
        {
            "error_category": "计算错误",
            "error_summary": "把负号漏掉",
            "secondary_errors": [{"category": "逻辑断裂", "summary": "缺少推导依据"}],
        },
        clear_errors=False,
        error_candidates=[],
    )
    assert (category, summary) == ("计算错误", "把负号漏掉")
    assert [(item.category, item.summary) for item in secondary] == [
        ("逻辑断裂", "缺少推导依据")
    ]


def test_ai_grading_prompt_uses_actual_work_not_legacy_tag_as_constraint():
    from backend.scan_grading.ai_batch_grading_service import MajorQuestionSpec, build_ai_major_prompt

    spec = MajorQuestionSpec(
        question_id="Q1", detail_question_ids=["Q1"],
        rubric={}, answer_key={}, max_score=5,
    )
    system_prompt, _, _ = build_ai_major_prompt(
        spec, {"mode": "full_page_subjective", "items": []},
        question_tag_context={"Q1": {"error_type": ["计算错误"]}},
    )
    assert "根据本次实际作答和扣分证据选择 error_category" in system_prompt
    assert "旧 error_type 标签仅作背景，不能限制本次判断" in system_prompt
    assert "优先从 QUESTION_TAG_CONTEXT 的 error_type 原值中选择" not in system_prompt


def _skill_question(tmp_path):
    """解答题 + 当前知识标准 + 两条技能标签 + 一个可用证据版本。"""
    import json

    from tests.current_knowledge_support import install_current_knowledge

    path, qid = _choice_question(tmp_path)
    release_id = install_current_knowledge(path, taxonomy_revision=9)
    with connect(path) as conn:
        conn.execute("UPDATE questions SET question_type='解答题' WHERE id=?", (qid,))
        for key in (_SKILL_KEY, _SKILL_KEY_2):
            conn.execute(
                "INSERT INTO question_tags(question_id, tag_type, tag_value, source)"
                " VALUES(?, 'knowledge_point', ?, 'derived')",
                (qid, key),
            )
        conn.execute(
            """
            INSERT INTO question_solution_evidence_versions(
                evidence_version_id, question_id, source_content_hash,
                schema_version, content_hash, evidence_json, status,
                source_kind, source_reference, created_by, graph_release_id
            ) VALUES (?, ?, ?, 'question-solution-evidence-v2', ?, ?,
                      'approved', 'backfill', 'synthetic', 'test', ?)
            """,
            (
                _SKILL_EVIDENCE_ID,
                qid,
                "b" * 64,
                "c" * 64,
                json.dumps(
                    {"parts": [{
                        "part_id": "part-1",
                        "evidence_points": [{
                            "evidence_point_id": "ep-1",
                            "target": "目标",
                            "observable_evidence": "证据",
                            "fine_term_links": [],
                        }],
                    }]},
                    ensure_ascii=False,
                ),
                release_id,
            ),
        )
    return path, qid, release_id


def _insert_pattern(conn, qid, *, trigger_kind="step", trigger_value="ep-1"):
    conn.execute(
        "INSERT INTO question_error_patterns"
        " (question_id, category, pattern, trigger_kind, trigger_value, status, source)"
        " VALUES (?, '过程与依据', '漏写必要条件', ?, ?, 'confirmed', 'ai_auto')",
        (qid, trigger_kind, trigger_value),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _link_points(path, qid, release_id, links, extra_points=None):
    from question_bank.solution_evidence.knowledge_links import replace_point_links

    points = [{
        "part_id": "part-1",
        "evidence_point_id": "ep-1",
        "links": links,
    }]
    points.extend(extra_points or [])
    with connect(path) as conn:
        replace_point_links(
            conn,
            evidence_version_id=_SKILL_EVIDENCE_ID,
            question_id=qid,
            graph_release_id=release_id,
            points=points,
        )


def _detail_pattern(detail, pattern_id):
    return next(item for item in detail["error_patterns"] if item["id"] == pattern_id)


def test_step_pattern_derives_unique_skill_from_criterion(tmp_path):
    path, qid, release_id = _skill_question(tmp_path)
    with connect(path) as conn:
        pid = _insert_pattern(conn, qid)
    _link_points(path, qid, release_id, [
        {"term_id": _SKILL_KEY, "stable_key": _SKILL_KEY, "role": "direct"},
    ])
    detail = QuestionBankReadService(path).get_question(qid)
    row = _detail_pattern(detail, pid)
    assert row["skill_key"] == _SKILL_KEY
    assert row["skill_source"] == "criterion"
    assert row["skill_label"]
    assert _SKILL_KEY in [item["key"] for item in detail["selectable_skills"]]


def test_step_pattern_links_all_direct_skills(tmp_path):
    path, qid, release_id = _skill_question(tmp_path)
    with connect(path) as conn:
        pid = _insert_pattern(conn, qid)
    _link_points(path, qid, release_id, [
        {"term_id": _SKILL_KEY, "stable_key": _SKILL_KEY, "role": "direct"},
        {"term_id": _SKILL_KEY_2, "stable_key": _SKILL_KEY_2, "role": "direct"},
    ])
    row = _detail_pattern(QuestionBankReadService(path).get_question(qid), pid)
    assert row["skill_key"] is None
    assert row["skill_keys"] == sorted([_SKILL_KEY, _SKILL_KEY_2])
    assert len(row["skill_labels"]) == 2
    assert row["skill_source"] == "criterion"


def test_non_step_pattern_uses_unique_question_skill(tmp_path):
    """非判定点触发：本题只有一个直达技能时归入该技能，多个则不推断。"""
    path, qid, release_id = _skill_question(tmp_path)
    with connect(path) as conn:
        pid = _insert_pattern(conn, qid, trigger_kind="observation", trigger_value="")
    _link_points(path, qid, release_id, [
        {"term_id": _SKILL_KEY, "stable_key": _SKILL_KEY, "role": "direct"},
    ])
    row = _detail_pattern(QuestionBankReadService(path).get_question(qid), pid)
    assert (row["skill_key"], row["skill_source"]) == (_SKILL_KEY, "question")
    assert row["skill_keys"] == [_SKILL_KEY]

    # 另一判定点再链接一个技能 → 题目直达技能不唯一 → 不推断。
    _link_points(path, qid, release_id, [
        {"term_id": _SKILL_KEY, "stable_key": _SKILL_KEY, "role": "direct"},
    ], extra_points=[{
        "part_id": "part-1", "evidence_point_id": "ep-2",
        "links": [{"term_id": _SKILL_KEY_2, "stable_key": _SKILL_KEY_2, "role": "direct"}],
    }])
    row = _detail_pattern(QuestionBankReadService(path).get_question(qid), pid)
    assert row["skill_key"] is None and row["skill_keys"] == []
    assert row["skill_source"] is None


def test_teacher_skill_overrides_and_clears_to_criterion(tmp_path):
    path, qid, release_id = _skill_question(tmp_path)
    with connect(path) as conn:
        pid = _insert_pattern(conn, qid)
    _link_points(path, qid, release_id, [
        {"term_id": _SKILL_KEY, "stable_key": _SKILL_KEY, "role": "direct"},
    ])
    assert rename_patterns(
        path, question_ids=[qid], pattern_id=pid,
        old_pattern="漏写必要条件", new_pattern="漏写必要条件",
        skill_key=_SKILL_KEY_2, update_skill=True,
    ) == 1
    row = _detail_pattern(QuestionBankReadService(path).get_question(qid), pid)
    assert (row["skill_key"], row["skill_source"]) == (_SKILL_KEY_2, "teacher")
    assert rename_patterns(
        path, question_ids=[qid], pattern_id=pid,
        old_pattern="漏写必要条件", new_pattern="漏写必要条件",
        skill_key=None, update_skill=True,
    ) == 1
    row = _detail_pattern(QuestionBankReadService(path).get_question(qid), pid)
    assert (row["skill_key"], row["skill_source"]) == (_SKILL_KEY, "criterion")


def test_rename_successor_carries_teacher_skill(tmp_path):
    path, qid, _release_id = _skill_question(tmp_path)
    with connect(path) as conn:
        pid = _insert_pattern(conn, qid, trigger_kind="observation", trigger_value="")
    assert rename_patterns(
        path, question_ids=[qid], pattern_id=pid,
        old_pattern="漏写必要条件", new_pattern="漏写必要条件",
        skill_key=_SKILL_KEY, update_skill=True,
    ) == 1
    assert rename_patterns(
        path, question_ids=[qid], pattern_id=pid,
        old_pattern="漏写必要条件", new_pattern="漏写必要前提", category="过程与依据",
    ) == 1
    detail = QuestionBankReadService(path).get_question(qid)
    row = next(item for item in detail["error_patterns"] if item["pattern"] == "漏写必要前提")
    assert row["skill_key"] == _SKILL_KEY and row["skill_source"] == "teacher"


def test_question_detail_skill_edit_endpoint(tmp_path):
    from fastapi.testclient import TestClient

    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_question_bank_db_path, get_question_bank_read_service,
    )

    path, qid, release_id = _skill_question(tmp_path)
    with connect(path) as conn:
        pid = _insert_pattern(conn, qid)
    _link_points(
        path,
        qid,
        release_id,
        [{"term_id": _SKILL_KEY, "stable_key": _SKILL_KEY, "role": "direct"}],
        extra_points=[{
            "part_id": "part-1",
            "evidence_point_id": "ep-2",
            "links": [{"term_id": _SKILL_KEY_2, "stable_key": _SKILL_KEY_2, "role": "direct"}],
        }],
    )
    app = create_app()
    app.dependency_overrides[get_question_bank_db_path] = lambda: path
    app.dependency_overrides[get_question_bank_read_service] = lambda: QuestionBankReadService(path)
    client = TestClient(app)

    detail = client.get(f"/api/question-bank/questions/{qid}").json()
    assert _SKILL_KEY in [item["key"] for item in detail["selectable_skills"]]
    row = _detail_pattern(detail, pid)
    assert row["skill_source"] == "criterion"

    # 仅改关联技能，不改名也不改大类
    response = client.patch(
        f"/api/question-bank/questions/{qid}/error-patterns/{pid}",
        json={"action": "edit", "expected_pattern": "漏写必要条件", "skill_key": _SKILL_KEY_2},
    )
    assert response.status_code == 200, response.text
    row = _detail_pattern(response.json(), pid)
    assert (row["skill_key"], row["skill_source"]) == (_SKILL_KEY_2, "teacher")

    # 题目技能列表之外的键被拒
    response = client.patch(
        f"/api/question-bank/questions/{qid}/error-patterns/{pid}",
        json={"action": "edit", "expected_pattern": "漏写必要条件", "skill_key": "sk_bnu24_math_g8_upper_9_9_99"},
    )
    assert response.status_code == 422

    # 显式 null 清除教师设定，判定点推导恢复生效
    response = client.patch(
        f"/api/question-bank/questions/{qid}/error-patterns/{pid}",
        json={"action": "edit", "expected_pattern": "漏写必要条件", "skill_key": None},
    )
    assert response.status_code == 200, response.text
    row = _detail_pattern(response.json(), pid)
    assert (row["skill_key"], row["skill_source"]) == (_SKILL_KEY, "criterion")


# ── 并行错因整理与班级报告并发 ────────────────────────────────────


def _v3_cause_source(question_id: str) -> dict:
    """不关联题库、非选择非填空的最小输入：plan_cause_question 走 v3 整题整理。"""
    return {
        "question_id": question_id,
        "question_text": "题干",
        "reference_analysis": "",
        "canonical_answer": "x=2",
        "rubric": {"max_score": 10},
        "evidence": [{"id": f"{question_id}.e1", "student_answer": "错答"}],
        "known_patterns": [],
    }


class _ConcurrentCauseClient:
    """按题号返回指定错法名；记录并发峰值；名称合并请求单独计数。"""

    def __init__(self, *, max_in_flight, reasons=None, merge_payload=None,
                 merge_error=None, barrier=None, barrier_calls=0):
        self.config_gateway = SimpleNamespace(
            execution_snapshot=SimpleNamespace(max_in_flight=max_in_flight))
        self._lock = threading.Lock()
        self.inflight = 0
        self.peak = 0
        self.calls: list[dict] = []
        self.merge_calls = 0
        self._reasons = reasons or {}
        self._merge_payload = merge_payload if merge_payload is not None else {"merges": []}
        self._merge_error = merge_error
        self._barrier = barrier
        self._barrier_calls = barrier_calls

    def json_from_text(self, prompt, extra_kwargs=None, **_kwargs):
        body = json.loads(prompt.rsplit("\n", 1)[1])
        is_merge = isinstance(body, dict) and "fresh" in body and "library" in body
        with self._lock:
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
            self.calls.append(body)
            number = len(self.calls)
        try:
            if is_merge:
                self.merge_calls += 1
                if self._merge_error is not None:
                    raise self._merge_error
                return dict(self._merge_payload)
            if self._barrier is not None and number <= self._barrier_calls:
                self._barrier.wait()
            qid = str(body["question_id"])
            reason, category = self._reasons.get(qid, ("规范错法", "计算与化简"))
            return {"groups": [{
                "kind": "error", "category": category, "reason": reason,
                "manifestation": "本题表现",
                "evidence_ids": [item["id"] for item in body["evidence"]],
            }]}
        finally:
            with self._lock:
                self.inflight -= 1


def _run_parallel_causes(tmp_path, monkeypatch, sources, client):
    import backend.class_analysis as causes
    import backend.error_patterns as pattern_module

    path, _qid = _choice_question(tmp_path)
    store = ClassAnalysisStateStore(tmp_path / "reports")
    monkeypatch.setattr(
        causes, "assemble_cause_data",
        lambda *a, **k: SimpleNamespace(questions=[], students=[], rubric=None),
    )
    monkeypatch.setattr(causes, "build_cause_inputs", lambda *a, **k: sources)
    monkeypatch.setattr(
        pattern_module, "sync_session_patterns_to_bank", lambda *a, **k: 0)
    context = SimpleNamespace(
        payload={"session_id": 3},
        raise_if_cancelled=lambda: None,
        report=lambda *a, **k: None,
    )
    return causes.run_cause_analysis(
        context,
        db=SimpleNamespace(db_path=path.parent / "grading_system.db"),
        data_root=tmp_path,
        store=store,
        llm_client_factory=lambda: client,
    ), store


def test_cause_analysis_questions_run_in_parallel(tmp_path, monkeypatch):
    client = _ConcurrentCauseClient(
        max_in_flight=4,
        barrier=threading.Barrier(3, timeout=5),
        barrier_calls=3,
    )
    sources = [_v3_cause_source(f"Q{i}") for i in range(1, 5)]
    outcome, store = _run_parallel_causes(tmp_path, monkeypatch, sources, client)
    # 前 3 个请求互相等待，串行执行会超时；能完成即证明并发 ≥3。
    assert outcome["status"] == "ready" and outcome["failed_questions"] == 0
    assert client.peak >= 3
    assert len(client.calls) == 4  # 只有一个新错法名 → 不发合并请求
    questions = store.load(3)["cause_analysis"]["questions"]
    assert set(questions) == {"Q1", "Q2", "Q3", "Q4"}
    assert all(not entry["failed"] for entry in questions.values())


def test_cause_analysis_respects_configured_single_worker(tmp_path, monkeypatch):
    client = _ConcurrentCauseClient(max_in_flight=1)
    sources = [_v3_cause_source(f"Q{i}") for i in range(1, 5)]
    outcome, _store = _run_parallel_causes(tmp_path, monkeypatch, sources, client)
    assert outcome["status"] == "ready"
    assert client.peak == 1


def test_cause_analysis_parallel_writes_do_not_lose_results(tmp_path, monkeypatch):
    reasons = {f"Q{i}": (f"错法{i}", "计算与化简") for i in range(1, 7)}
    client = _ConcurrentCauseClient(max_in_flight=4, reasons=reasons)
    sources = [_v3_cause_source(qid) for qid in reasons]
    outcome, store = _run_parallel_causes(tmp_path, monkeypatch, sources, client)
    assert outcome["status"] == "ready"
    state = store.load(3)
    assert set(state["cause_analysis"]["questions"]) == set(reasons)
    assert set(state["error_records"]) == set(reasons)
    assert client.merge_calls == 1  # 6 个新错法名 → 发一次合并请求


def test_cause_analysis_merge_unifies_only_same_category_names(tmp_path, monkeypatch):
    client = _ConcurrentCauseClient(
        max_in_flight=4,
        reasons={
            "Q1": ("多加水平边", "审题与条件"),
            "Q2": ("绳长多加一段", "审题与条件"),
            "Q3": ("符号错误", "计算与化简"),
        },
        merge_payload={"merges": [
            {"from": "绳长多加一段", "to": "多加水平边"},
            {"from": "符号错误", "to": "多加水平边"},
        ]},
    )
    sources = [_v3_cause_source(qid) for qid in ("Q1", "Q2", "Q3")]
    outcome, store = _run_parallel_causes(tmp_path, monkeypatch, sources, client)
    assert outcome["status"] == "ready" and outcome["name_merges"] == 1
    questions = store.load(3)["cause_analysis"]["questions"]
    assert {g["reason"] for g in questions["Q1"]["result"]["groups"]} == {"多加水平边"}
    assert {g["reason"] for g in questions["Q2"]["result"]["groups"]} == {"多加水平边"}
    assert {g["reason"] for g in questions["Q3"]["result"]["groups"]} == {"符号错误"}
    merges = store.load(3)["cause_analysis"]["name_merges"]["cause_name_merges"]
    assert merges == [
        {"from": "绳长多加一段", "to": "多加水平边", "question_ids": ["Q2"]}
    ]


def test_cause_analysis_merge_call_failure_is_nonfatal(tmp_path, monkeypatch):
    client = _ConcurrentCauseClient(
        max_in_flight=4,
        reasons={"Q1": ("错法甲", "计算与化简"), "Q2": ("错法乙", "计算与化简")},
        merge_error=RuntimeError("merge unavailable"),
    )
    sources = [_v3_cause_source(qid) for qid in ("Q1", "Q2")]
    outcome, store = _run_parallel_causes(tmp_path, monkeypatch, sources, client)
    assert outcome["status"] == "ready" and outcome["name_merges"] == 0
    questions = store.load(3)["cause_analysis"]["questions"]
    assert {g["reason"] for g in questions["Q1"]["result"]["groups"]} == {"错法甲"}
    assert {g["reason"] for g in questions["Q2"]["result"]["groups"]} == {"错法乙"}


def test_cause_analysis_single_fresh_name_skips_merge_call(tmp_path, monkeypatch):
    client = _ConcurrentCauseClient(max_in_flight=4)
    sources = [_v3_cause_source(qid) for qid in ("Q1", "Q2")]
    outcome, _store = _run_parallel_causes(tmp_path, monkeypatch, sources, client)
    assert outcome["status"] == "ready"
    assert len(client.calls) == 2 and client.merge_calls == 0


def test_class_reports_generate_in_parallel(tmp_path, monkeypatch):
    import backend.report_pipeline as pipeline
    import backend.reporting.analysis_report_exporter as exporter_module
    import backend.session_analysis as session_analysis

    lock = threading.Lock()
    inflight = [0]
    peak = [0]
    barrier = threading.Barrier(2, timeout=5)

    class _Client:
        config_gateway = SimpleNamespace(
            execution_snapshot=SimpleNamespace(max_in_flight=4))

        def json_from_text(self, prompt, extra_kwargs=None, **_kwargs):
            with lock:
                inflight[0] += 1
                peak[0] = max(peak[0], inflight[0])
            try:
                barrier.wait()
                return {"key_findings": [], "common_issues": [], "student_notes": []}
            finally:
                with lock:
                    inflight[0] -= 1

    groups = {
        name: SimpleNamespace(
            students=[SimpleNamespace(student_id=index, records=[])]
        )
        for index, name in enumerate(("1 班", "2 班"), start=1)
    }
    monkeypatch.setattr(
        session_analysis, "assemble_session_analysis",
        lambda *a, **k: SimpleNamespace(students=[object()], small_sample=False),
    )
    monkeypatch.setattr(
        session_analysis, "split_session_analysis_by_class", lambda _data: groups)
    monkeypatch.setattr(pipeline, "session_error_records", lambda *a, **k: {})
    monkeypatch.setattr(exporter_module, "build_class_payload", lambda *a, **k: {})
    monkeypatch.setattr(exporter_module, "build_report_prompt", lambda *a, **k: "prompt")

    context = SimpleNamespace(
        payload={"session_id": 3}, raise_if_cancelled=lambda: None,
        report=lambda *a, **k: None,
    )
    reports_dir = tmp_path / "reports"
    summary = pipeline.generate_session_class_reports(
        context,
        db=SimpleNamespace(
            reviews=SimpleNamespace(list_teacher_score_locks=lambda _sid: [])
        ),
        session_id=3,
        reports_dir=reports_dir, data_root=tmp_path, client=_Client(),
    )
    # 两班叙述请求互相等待：串行执行会超时，完成即证明并发 ≥2。
    assert summary["status"] == "ready" and summary["generated"] == 2
    assert peak[0] >= 2
    state = ClassAnalysisStateStore(reports_dir).load(3)
    assert all(
        entry["status"] == "ready" for entry in state["class_reports"].values())
