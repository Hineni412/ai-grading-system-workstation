"""题目预测错法在考试作答、教师调整和重分析间的用户结果。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_grader import _normalize_grading_errors
from backend.class_analysis import (
    CAUSE_ANALYSIS_VERSION, CausePatternEditError, ClassAnalysisStateStore, edit_cause_pattern,
    plan_cause_question,
)
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


def test_legacy_external_causes_accept_only_missing_supplemental_text():
    from copy import deepcopy
    from backend.class_analysis import cause_input_matches, _cause_input_fingerprint
    old = {"question_id": "Q1", "question_text": "", "reference_analysis": "",
           "canonical_answer": "B", "rubric": {"max_score": 3},
           "evidence": [{"id": "E1", "student_answer": "C", "text": "误选 C"}]}
    saved = {"input": old, "input_fingerprint": _cause_input_fingerprint(old)}
    current = {**old, "question_text": "后补的题干", "reference_analysis": "后补的解析"}
    assert cause_input_matches(saved, current)
    for key, value in (("canonical_answer", "D"), ("rubric", {"max_score": 5}),
                       ("evidence", [{"id": "E1", "student_answer": "B"}])):
        assert not cause_input_matches(saved, {**current, key: value})
    populated = deepcopy(current)
    populated["question_text"] = "原有题干"
    assert not cause_input_matches({"input": populated, "input_fingerprint": _cause_input_fingerprint(populated)}, current)


def test_report_preparation_reuses_legacy_external_results_without_model(tmp_path, monkeypatch):
    import backend.class_analysis as causes
    path, _qid = _choice_question(tmp_path)
    old = {"question_id": "Q1", "question_text": "", "reference_analysis": "", "evidence": []}
    source = {**old, "question_text": "后补题干", "reference_analysis": "后补解析"}
    fingerprint = causes._cause_input_fingerprint(old)
    store = ClassAnalysisStateStore(tmp_path / "reports")
    store.save(3, cause_analysis={"questions": {"Q1": {"version": CAUSE_ANALYSIS_VERSION,
               "input": old, "input_fingerprint": fingerprint, "result": {"groups": []}}}},
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
    store.save(3, cause_analysis={"questions": {"Q1": {"version": CAUSE_ANALYSIS_VERSION,
               "input": source, "input_fingerprint": _cause_input_fingerprint(source), "origin": "option_map",
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


def test_failed_new_draft_does_not_reopen_review_when_teacher_version_still_matches(tmp_path):
    path, qid = _choice_question(tmp_path)
    with connect(path) as conn:
        for vid, status, quality in (("a"*64, "approved", "passed"), ("b"*64, "proposed", "failed")):
            conn.execute("""INSERT INTO training_criterion_versions
                (version_id,question_id,version_number,source_content_hash,schema_version,status,
                 source_kind,source_reference,criteria_json,criteria_hash,quality_status,created_by)
                VALUES(?,?,? ,?,'judgment-points-v1',?,'teacher_manual',?,'{}',?,?,'synthetic')""",
                (vid, qid, 1 if status == "approved" else 2, "c"*64, status, vid, "d"*64, quality))
        conn.execute("INSERT INTO training_criterion_heads(question_id,current_version_id,approved_version_id,current_source_hash) VALUES(?,?,?,?)",
                     (qid, "b"*64, "a"*64, "c"*64))
    reader = QuestionBankReadService(path)
    assert reader.get_question(qid)["criteria_needs_review"] is False
    with connect(path) as conn:
        conn.execute("UPDATE training_criterion_heads SET current_source_hash=? WHERE question_id=?", ("e"*64, qid))
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
        "version": CAUSE_ANALYSIS_VERSION, "origin": "model",
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
        "version": CAUSE_ANALYSIS_VERSION, "origin": "model",
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


def test_hybrid_grading_prompt_uses_actual_work_not_legacy_tag_as_constraint():
    from hybrid_batch_grading_service import MajorQuestionSpec, build_hybrid_major_prompt

    spec = MajorQuestionSpec(
        question_id="Q1", detail_question_ids=["Q1"],
        rubric={}, answer_key={}, max_score=5,
    )
    system_prompt, _, _ = build_hybrid_major_prompt(
        spec, {"mode": "tiles", "items": []},
        question_tag_context={"Q1": {"error_type": ["计算错误"]}},
    )
    assert "根据本次实际作答和扣分证据选择 error_category" in system_prompt
    assert "旧 error_type 标签仅作背景，不能限制本次判断" in system_prompt
    assert "优先从 QUESTION_TAG_CONTEXT 的 error_type 原值中选择" not in system_prompt
