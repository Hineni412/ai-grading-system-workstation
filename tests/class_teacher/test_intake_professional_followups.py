from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest

from backend.class_teacher.errors import VaultError
from backend.class_teacher.intake.ai_task_adapter import (
    _EXPLICIT_DATE_PATTERN,
    _merge_open_questions,
    _professional_confirmed_dimension,
    _professional_date_from_text,
    _professional_profile_fallback,
)
from backend.class_teacher.support_record_service import _normalize_datetime
from tests.class_teacher.test_intake_r7 import (
    _client,
    _conversation_with_turn,
    _service,
    _work_item,
)


def _set_homeroom(service, operation_id: str) -> None:
    preference = service.intake.preferences.get()
    service.intake.preferences.set(
        homeroom_class="一班",
        expected_revision=0,
        expected_source_revision=str(preference["source_revision"]),
        operation_id=operation_id,
    )


def _student_ref(service, display_name: str = "合成学生甲") -> dict[str, str]:
    candidate = next(
        item
        for item in service.class_roster.ai_candidates(token="", class_label="一班")
        if item["display_name"] == display_name
    )
    return {
        "kind": "student",
        "id": str(candidate["id"]),
        "revision": str(candidate["revision"]),
    }


def test_explicit_date_pattern_accepts_year_month_forms() -> None:
    assert _EXPLICIT_DATE_PATTERN.search("报告是2026年5月出具的")
    assert _EXPLICIT_DATE_PATTERN.search("结论日期 2026-05")
    assert _EXPLICIT_DATE_PATTERN.search("今年5月做的评估")
    assert _EXPLICIT_DATE_PATTERN.search("去年12月确诊")
    assert _EXPLICIT_DATE_PATTERN.search("2026年5月1日出具")
    assert _EXPLICIT_DATE_PATTERN.search("2026-05-01")
    assert _EXPLICIT_DATE_PATTERN.search("昨天复查")


def test_explicit_date_pattern_stays_conservative_for_plain_numbers() -> None:
    assert not _EXPLICIT_DATE_PATTERN.search("这次考了85分，班里有42人")
    assert not _EXPLICIT_DATE_PATTERN.search("小组活动安排在5周后")
    assert not _EXPLICIT_DATE_PATTERN.search("量表版本 2026.5 不适用")
    assert not _EXPLICIT_DATE_PATTERN.search("编号 20260531 的材料")


def test_professional_date_from_text_extracts_year_month() -> None:
    assert _professional_date_from_text("结论由医院在2026年5月出具") == "2026-05"
    assert _professional_date_from_text("报告日期 2026-05") == "2026-05"
    assert _professional_date_from_text("2026/5的诊断报告") == "2026-05"
    # 带日的写法仍保持完整日期。
    assert _professional_date_from_text("2026年5月3日出具") == "2026-05-03"
    assert _professional_date_from_text("2026-05-03 出具") == "2026-05-03"


def test_professional_date_from_text_resolves_relative_year_month() -> None:
    current_year = datetime.now(ZoneInfo("Asia/Shanghai")).year
    assert _professional_date_from_text("今年5月做的评估") == f"{current_year:04d}-05"
    assert _professional_date_from_text("去年12月确诊") == f"{current_year - 1:04d}-12"


def test_professional_date_from_text_rejects_invalid_month() -> None:
    assert _professional_date_from_text("2026年13月的说法") == ""


def test_merge_open_questions_deduplicates_trailing_punctuation() -> None:
    merged = _merge_open_questions(
        ["具体表现情境？", "  作息是否规律  "],
        ["具体表现情境", "作息是否规律。"],
    )
    assert merged == ["具体表现情境？", "作息是否规律"]


def test_professional_fallback_drops_answered_rule_questions(tmp_path: Path) -> None:
    del tmp_path
    profile = _professional_profile_fallback(
        current_profile={
            "summary": "原有档案。",
            "dimensions": [],
            "open_questions": [
                "这份专业结论的出具日期是什么时候？",
                "专业结论由哪家机构或哪位专业人员出具，是否有可核对的书面材料？",
                "他在哪些课堂上更能坐得住？",
            ],
            "support_focus": [],
        },
        questions=["学生当前在校已采用哪些支持方式，哪些有效，哪些做法需要避免？"],
        source_text="结论由合成市儿童医院出具，有2026年5月书面诊断报告。",
    )
    # 会话全文已提供来源/依据与日期，对应的规则层追问不再带入档案。
    assert "这份专业结论的出具日期是什么时候？" not in profile["open_questions"]
    assert not any("哪家机构" in question for question in profile["open_questions"])
    # 与专业证据无关的旧问题保留；在校支持未提供，对应追问仍保留。
    assert "他在哪些课堂上更能坐得住？" in profile["open_questions"]
    assert any("在校" in question for question in profile["open_questions"])


def test_professional_fallback_keeps_confirmed_dimension_instead_of_downgrading(
    tmp_path: Path,
) -> None:
    del tmp_path
    confirmed = {
        "key": "professional_support_context",
        "label": "专业支持信息",
        "items": ["结论日期：2026-05。"],
    }
    profile = _professional_profile_fallback(
        current_profile={
            "summary": "",
            "dimensions": [confirmed],
            "open_questions": [],
            "support_focus": [],
        },
        questions=[],
        source_text="教师又提到孩子确诊一事，但没有新材料。",
    )
    dimension = next(
        item for item in profile["dimensions"]
        if item["key"] == "professional_support_context"
    )
    assert dimension["label"] == "专业支持信息"
    assert dimension["items"] == ["结论日期：2026-05。"]


def test_professional_fallback_still_writes_placeholder_when_not_confirmed(
    tmp_path: Path,
) -> None:
    del tmp_path
    profile = _professional_profile_fallback(
        current_profile={"summary": "", "dimensions": [], "open_questions": [], "support_focus": []},
        questions=[],
        source_text="教师提到孩子确诊一事。",
    )
    dimension = next(
        item for item in profile["dimensions"]
        if item["key"] == "professional_support_context"
    )
    assert dimension["label"] == "专业支持信息（待核对）"


def test_professional_confirmed_dimension_uses_extracted_fields() -> None:
    dimension = _professional_confirmed_dimension({
        "basis": "合成市儿童医院书面诊断报告",
        "observed_at": "2026-05",
        "current_school_support": "当前在校采用前排座位",
        "professional_recommendations": "固定规则并预告转换",
        "avoidances": "避免公开责备",
    })
    assert dimension["key"] == "professional_support_context"
    assert dimension["label"] == "专业支持信息"
    joined = "\n".join(str(item) for item in dimension["items"])
    assert "书面诊断报告" in joined
    assert "2026-05" in joined
    assert "前排座位" in joined
    assert "避免公开责备" in joined


def test_year_month_professional_material_completes_evidence_and_replaces_placeholder(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    _set_homeroom(service, "year-month-professional-homeroom")
    message = (
        "合成学生甲的结论由合成市儿童医院李医生出具，有2026年5月书面诊断报告。"
        "当前在校采用前排座位和简短提醒；报告建议固定规则，避免公开责备。"
    )
    conversation, turn = _conversation_with_turn(
        service,
        "year-month-professional-turn",
        message=message,
    )
    task = port.prepare_calls[-1]
    ref = _student_ref(service)

    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已补充专业材料草稿。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "year-month-professional-item",
                domain="student_support",
                mode="record",
                intent="append",
                refs=[ref],
                draft={
                    "summary": "教师补充了专业材料和在校支持。",
                    "record_kind": "reported_statement",
                    "source": "教师当前输入",
                    "basis": "合成市儿童医院书面诊断报告",
                    "observed_at": "",
                    "current_school_support": "",
                    "professional_recommendations": "固定规则",
                    "avoidances": "避免公开责备",
                    "profile_update": {
                        "summary": "教师补充了专业材料。",
                        "dimensions": [],
                        "open_questions": [],
                        "support_focus": [],
                    },
                },
            )],
        },
    )

    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))
    saved = service.intake.get_conversation(str(conversation["conversation_id"]))
    content = handoff["content"]

    assert content["record_kind"] == "professional_conclusion"
    assert content["observed_at"] == "2026-05"
    assert saved["turns"][-1]["clarification_questions"] == []
    dimension = next(
        item
        for item in content["profile_update"]["dimensions"]
        if item["key"] == "professional_support_context"
    )
    assert dimension["label"] == "专业支持信息"
    assert any("2026-05" in str(item) for item in dimension["items"])
    open_questions = content["profile_update"]["open_questions"]
    assert not any("出具日期" in question for question in open_questions)
    assert not any("哪家机构" in question for question in open_questions)
    assert not any("在校" in question for question in open_questions)


def test_history_clarification_questions_are_replayed_in_model_request(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    _set_homeroom(service, "history-replay-homeroom")
    conversation, first_turn = _conversation_with_turn(
        service,
        "history-replay-first-turn",
        message="合成学生甲最近上课容易走神。",
    )
    first_task = port.prepare_calls[-1]
    service.intake.ai_task_adapter.persist_model_result(
        task_id=str(first_turn["task_id"]),
        source_ref=first_task["source_ref"],
        context_refs=first_task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已记录，想再了解一点。",
            "clarification_questions": ["孩子最近在家的作息怎样？"],
            "work_items": [],
        },
    )
    ready = service.intake.get_conversation(str(conversation["conversation_id"]))
    continued = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(ready["revision"]),
        message="作息规律，晚上九点前睡觉。",
        operation_id="history-replay-second-turn",
    )
    second_task = port.prepare_calls[-1]
    request = service.intake.ai_task_adapter.build_model_request(
        task_kind="class_teacher.intake_triage",
        source_ref=second_task["source_ref"],
        context_refs=second_task["context_refs"],
    )
    assistant_messages = [
        str(message["content"])
        for message in request.messages
        if message["role"] == "assistant"
    ]
    assert continued["turns"]
    assert assistant_messages == ["已记录，想再了解一点。（当时追问：孩子最近在家的作息怎样？）"]


def test_normalize_datetime_accepts_year_month_as_first_of_month() -> None:
    normalized = _normalize_datetime("2026-07", "观察时间")
    assert normalized is not None
    parsed = datetime.fromisoformat(normalized)
    assert parsed.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat() == "2026-07-01"
    # 完整日期与空值行为不变。
    assert _normalize_datetime(None, "观察时间") is None
    full = _normalize_datetime("2026-07-15", "观察时间")
    assert full is not None
    assert datetime.fromisoformat(full).astimezone(
        ZoneInfo("Asia/Shanghai")
    ).date().isoformat() == "2026-07-15"


def test_normalize_datetime_still_rejects_invalid_year_month() -> None:
    with pytest.raises(VaultError):
        _normalize_datetime("2026-13", "观察时间")
    with pytest.raises(VaultError):
        _normalize_datetime("不是日期", "观察时间")


def test_year_month_professional_conclusion_adopts_into_support_record(
    tmp_path: Path,
) -> None:
    service, port = _service(tmp_path)
    _set_homeroom(service, "year-month-adopt-homeroom")
    message = (
        "合成学生甲的结论由合成市儿童医院李医生出具，有2026年5月书面诊断报告。"
        "当前在校采用前排座位和简短提醒；报告建议固定规则，避免公开责备。"
    )
    _conversation, turn = _conversation_with_turn(
        service,
        "year-month-adopt-turn",
        message=message,
    )
    task = port.prepare_calls[-1]
    ref = _student_ref(service)

    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已补充专业材料草稿。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "year-month-adopt-item",
                domain="student_support",
                mode="record",
                intent="append",
                refs=[ref],
                draft={
                    "summary": "教师补充了专业材料和在校支持。",
                    "record_kind": "reported_statement",
                    "source": "教师当前输入",
                    "basis": "合成市儿童医院书面诊断报告",
                    "observed_at": "",
                    "current_school_support": "",
                    "professional_recommendations": "固定规则",
                    "avoidances": "避免公开责备",
                    "profile_update": {
                        "summary": "教师补充了专业材料。",
                        "dimensions": [],
                        "open_questions": [],
                        "support_focus": [],
                    },
                },
            )],
        },
    )
    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))
    assert handoff["content"]["observed_at"] == "2026-05"

    receipt = service.intake.adopt_handoff(
        token="",
        handoff_id=str(handoff["handoff_id"]),
        draft_revision=int(handoff["draft_revision"]),
        target_revision=str(handoff["subject_refs"][0]["revision"]),
        operation_id="adopt-year-month-professional",
    )
    assert receipt["formal_object_type"] == "student_record"
    with closing(service.database.connect()) as connection:
        row = connection.execute(
            "SELECT record_kind, observed_at FROM support_records LIMIT 1"
        ).fetchone()
    assert row is not None
    assert str(row["record_kind"]) == "professional_conclusion"
    assert datetime.fromisoformat(str(row["observed_at"])).astimezone(
        ZoneInfo("Asia/Shanghai")
    ).date().isoformat() == "2026-05-01"


_COMPLETE_MATERIAL_MESSAGE = (
    "合成学生甲的结论由合成市儿童医院李医生出具，有2026年5月书面诊断报告。"
    "当前在校采用前排座位和简短提醒；报告建议固定规则，避免公开责备。"
)


def _adopt_base_profile(
    service,
    port,
    operation_prefix: str,
    open_questions: list[str],
) -> tuple[dict[str, object], dict[str, str]]:
    """先形成并采纳一条普通记录，让档案里带上指定的 open_questions。"""
    conversation, turn = _conversation_with_turn(
        service,
        f"{operation_prefix}-base-turn",
        message="合成学生甲最近上课容易走神。",
    )
    task = port.prepare_calls[-1]
    ref = _student_ref(service)
    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理到学生档案。",
            "clarification_questions": [],
            "work_items": [_work_item(
                f"{operation_prefix}-base-item",
                domain="student_growth",
                mode="record",
                intent="create",
                refs=[ref],
                draft={
                    "summary": "合成学生甲最近上课容易走神。",
                    "profile_update": {
                        "summary": "合成学生甲最近上课容易走神。",
                        "dimensions": [],
                        "open_questions": open_questions,
                        "support_focus": [],
                    },
                },
            )],
        },
    )
    handoff = service.intake.open_handoff(str(outcome["handoff_ids"][0]))
    receipt = service.intake.adopt_handoff(
        token="",
        handoff_id=str(handoff["handoff_id"]),
        draft_revision=int(handoff["draft_revision"]),
        target_revision=str(handoff["subject_refs"][0]["revision"]),
        operation_id=f"{operation_prefix}-base-adopt",
    )
    assert receipt["formal_object_type"] == "student_record"
    return conversation, ref


def _persist_second_professional_turn(
    service,
    port,
    conversation: dict[str, object],
    ref: dict[str, str],
    operation_prefix: str,
    profile_update: object,
) -> dict[str, object]:
    ready = service.intake.get_conversation(str(conversation["conversation_id"]))
    continued = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(ready["revision"]),
        message=_COMPLETE_MATERIAL_MESSAGE,
        operation_id=f"{operation_prefix}-followup-turn",
    )
    turn = continued["turns"][-1]
    task = port.prepare_calls[-1]
    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已补充专业材料草稿。",
            "clarification_questions": [],
            "work_items": [_work_item(
                f"{operation_prefix}-followup-item",
                domain="student_support",
                mode="record",
                intent="append",
                refs=[ref],
                draft={
                    "summary": "教师补充了专业材料和在校支持。",
                    "record_kind": "reported_statement",
                    "source": "教师当前输入",
                    "basis": "合成市儿童医院书面诊断报告",
                    "observed_at": "",
                    "current_school_support": "",
                    "professional_recommendations": "固定规则",
                    "avoidances": "避免公开责备",
                    "profile_update": profile_update,
                },
            )],
        },
    )
    return service.intake.open_handoff(str(outcome["handoff_ids"][0]))


def test_model_trimmed_open_questions_win_when_evidence_complete(tmp_path: Path) -> None:
    service, port = _service(tmp_path)
    _set_homeroom(service, "model-trimmed-homeroom")
    conversation, ref = _adopt_base_profile(
        service,
        port,
        "model-trimmed",
        ["走神是否集中在特定课程？", "这份专业结论的出具日期是什么时候？"],
    )

    handoff = _persist_second_professional_turn(
        service,
        port,
        conversation,
        ref,
        "model-trimmed",
        {
            "summary": "合并后的当前档案。",
            "dimensions": [],
            "open_questions": ["孩子在家的作业启动是否需要新的约定？"],
            "support_focus": [],
        },
    )

    # 模型返回了精简后的完整列表，旧问题不复活；已由会话回答的更被过滤。
    assert handoff["content"]["profile_update"]["open_questions"] == [
        "孩子在家的作业启动是否需要新的约定？",
    ]


def test_invalid_model_profile_falls_back_to_merging_old_questions(tmp_path: Path) -> None:
    service, port = _service(tmp_path)
    _set_homeroom(service, "invalid-profile-homeroom")
    conversation, ref = _adopt_base_profile(
        service,
        port,
        "invalid-profile",
        ["走神是否集中在特定课程？", "这份专业结论的出具日期是什么时候？"],
    )

    handoff = _persist_second_professional_turn(
        service,
        port,
        conversation,
        ref,
        "invalid-profile",
        "invalid-provider-shape",
    )

    content = handoff["content"]
    open_questions = content["profile_update"]["open_questions"]
    # 模型输出非法时退回旧逻辑：旧列表并入，但已被会话回答的仍被过滤。
    assert "走神是否集中在特定课程？" in open_questions
    assert "这份专业结论的出具日期是什么时候？" not in open_questions
    dimension = next(
        item
        for item in content["profile_update"]["dimensions"]
        if item["key"] == "professional_support_context"
    )
    assert dimension["label"] == "专业支持信息"


def test_pending_student_handoffs_endpoint_lists_subject_drafts(tmp_path: Path) -> None:
    service, port = _service(tmp_path)
    _set_homeroom(service, "pending-endpoint-homeroom")
    _conversation, turn = _conversation_with_turn(
        service,
        "pending-endpoint-turn",
        message="合成学生甲确诊有多动症。",
    )
    task = port.prepare_calls[-1]
    ref = _student_ref(service)
    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理专业信息草稿。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "pending-endpoint-item",
                domain="student_support",
                mode="record",
                intent="create",
                refs=[ref],
                draft={
                    "summary": "教师报告合成学生甲已有相关专业结论。",
                    "profile_update": {
                        "summary": "教师报告合成学生甲已有相关专业结论。",
                        "dimensions": [],
                        "open_questions": [],
                        "support_focus": [],
                    },
                },
            )],
        },
    )
    handoff_id = str(outcome["handoff_ids"][0])

    client = _client(service)
    response = client.get(f"/api/class-teacher/intake/subjects/{ref['id']}/pending-handoffs")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["handoff_id"] for item in items] == [handoff_id]
    assert items[0]["adoption_state"] in {"pending", "opened"}
    assert items[0]["summary"]

    empty = client.get("/api/class-teacher/intake/subjects/subject-unknown/pending-handoffs")
    assert empty.status_code == 200
    assert empty.json()["items"] == []


def test_pending_student_handoffs_matches_hex_subject_id_to_stable_ref(
    tmp_path: Path,
) -> None:
    """handoff ref 是「班级|学号」稳定标识、抽屉 subject_id 是 hex 档案编号时，
    通过 student_subject_links 身份映射仍能命中；其他学生的 handoff 不误命中。"""
    service, port = _service(tmp_path)
    with closing(sqlite3.connect(tmp_path / "grading.db")) as connection:
        connection.execute("UPDATE students SET class_name='一班' WHERE id=2")
        connection.commit()
    _set_homeroom(service, "mixed-id-homeroom")
    conversation, jia_ref = _adopt_base_profile(service, port, "mixed-id", [])
    assert jia_ref["id"] == "一班|A001"
    with closing(service.database.connect()) as connection:
        subject_id = str(
            connection.execute(
                "SELECT subject_id FROM student_subject_links LIMIT 1"
            ).fetchone()[0]
        )
    # 采纳后档案编号（hex）与对话 ref（班级|学号）属于两套体系。
    assert subject_id != jia_ref["id"]

    yi_ref = _student_ref(service, "合成学生乙")
    ready = service.intake.get_conversation(str(conversation["conversation_id"]))
    continued = service.intake.append_turn(
        conversation_id=str(conversation["conversation_id"]),
        expected_revision=int(ready["revision"]),
        message="两名学生最近都有新情况。",
        operation_id="mixed-id-second-turn",
    )
    turn = continued["turns"][-1]
    task = port.prepare_calls[-1]
    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已分别整理到两名学生的档案。",
            "clarification_questions": [],
            "work_items": [
                _work_item(
                    "mixed-id-jia-item",
                    domain="student_growth",
                    mode="record",
                    intent="append",
                    refs=[jia_ref],
                    draft={
                        "summary": "甲的新情况。",
                        "profile_update": {
                            "summary": "甲的新情况。",
                            "dimensions": [],
                            "open_questions": [],
                            "support_focus": [],
                        },
                    },
                ),
                _work_item(
                    "mixed-id-yi-item",
                    domain="student_growth",
                    mode="record",
                    intent="append",
                    refs=[yi_ref],
                    draft={
                        "summary": "乙的新情况。",
                        "profile_update": {
                            "summary": "乙的新情况。",
                            "dimensions": [],
                            "open_questions": [],
                            "support_focus": [],
                        },
                    },
                ),
            ],
        },
    )
    handoff_ref_by_id = {}
    for handoff_id in outcome["handoff_ids"]:
        handoff = service.intake.open_handoff(str(handoff_id))
        refs = handoff.get("subject_refs") or []
        handoff_ref_by_id[str(handoff_id)] = str(refs[0].get("id")) if refs else ""
    jia_handoff_id = next(
        handoff_id
        for handoff_id, ref_id in handoff_ref_by_id.items()
        if ref_id == jia_ref["id"]
    )
    yi_handoff_id = next(
        handoff_id
        for handoff_id, ref_id in handoff_ref_by_id.items()
        if ref_id == yi_ref["id"]
    )

    result = service.intake.pending_student_handoffs(subject_id)
    items = result["items"]
    assert [item["handoff_id"] for item in items] == [jia_handoff_id]
    assert items[0]["subject_id"] == subject_id
    # 兼容输入旧 uuid 时，对外返回归一后的稳定学籍标识。
    assert items[0]["student_ref"] == "一班|A001"
    assert yi_handoff_id not in [item["handoff_id"] for item in items]

    # 主路径：直接以稳定学籍标识查询同样命中。
    by_ref = service.intake.pending_student_handoffs("一班|A001")
    assert [item["handoff_id"] for item in by_ref["items"]] == [jia_handoff_id]
    assert by_ref["items"][0]["student_ref"] == "一班|A001"


def test_support_endpoints_accept_stable_ref_and_legacy_uuid(tmp_path: Path) -> None:
    """对外学生编号统一为「班级|学号」稳定标识；旧 uuid 档案编号按兼容输入接受。"""
    service, port = _service(tmp_path)
    _set_homeroom(service, "stable-ref-api-homeroom")
    _conversation, jia_ref = _adopt_base_profile(service, port, "stable-ref-api", [])
    with closing(service.database.connect()) as connection:
        internal_id = str(
            connection.execute(
                "SELECT subject_id FROM student_subject_links LIMIT 1"
            ).fetchone()[0]
        )
    assert jia_ref["id"] == "一班|A001"

    client = _client(service)
    quoted_ref = quote("一班|A001", safe="")

    by_ref = client.get(f"/api/class-teacher/support/subjects/{quoted_ref}/student-card")
    assert by_ref.status_code == 200
    assert by_ref.json()["subject"]["student_ref"] == "一班|A001"
    assert by_ref.json()["subject"]["subject_id"] == internal_id

    by_uuid = client.get(f"/api/class-teacher/support/subjects/{internal_id}/student-card")
    assert by_uuid.status_code == 200
    assert by_uuid.json()["subject"]["student_ref"] == "一班|A001"

    records_by_ref = client.get(f"/api/class-teacher/support/subjects/{quoted_ref}/records")
    assert records_by_ref.status_code == 200
    assert len(records_by_ref.json()["items"]) == 1
    records_by_uuid = client.get(f"/api/class-teacher/support/subjects/{internal_id}/records")
    assert records_by_uuid.status_code == 200
    assert records_by_uuid.json()["items"] == records_by_ref.json()["items"]

    directory = client.get("/api/class-teacher/support/directory")
    assert directory.status_code == 200
    assert directory.json()["items"][0]["student_ref"] == "一班|A001"


def test_support_endpoints_reject_unknown_or_unlinked_student_ref(tmp_path: Path) -> None:
    service, port = _service(tmp_path)
    del port
    _set_homeroom(service, "stable-ref-error-homeroom")
    client = _client(service)

    unknown = client.get("/api/class-teacher/support/subjects/九班%7Cnobody/records")
    assert unknown.status_code == 409
    assert unknown.json()["error"]["code"] == "class_teacher_subject_ref_invalid"

    # 花名册里真实存在但尚未建档的学生：引用有效，档案不存在。
    unlinked = client.get("/api/class-teacher/support/subjects/二班%7CB001/records")
    assert unlinked.status_code == 404
    assert unlinked.json()["error"]["code"] == "support_subject_not_found"


def test_start_student_conversation_accepts_stable_ref(tmp_path: Path) -> None:
    service, port = _service(tmp_path)
    _set_homeroom(service, "stable-ref-conversation-homeroom")
    _conversation, jia_ref = _adopt_base_profile(service, port, "stable-ref-conversation", [])
    with closing(service.database.connect()) as connection:
        internal_id = str(
            connection.execute(
                "SELECT subject_id FROM student_subject_links LIMIT 1"
            ).fetchone()[0]
        )

    client = _client(service)
    response = client.post(
        "/api/class-teacher/intake/conversations",
        json={"subject_id": jia_ref["id"]},
        headers={"x-class-teacher-client": "class-teacher-browser-v1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["focused_subject_id"] == internal_id


def test_pending_student_handoffs_endpoint_returns_student_ref(tmp_path: Path) -> None:
    service, port = _service(tmp_path)
    _set_homeroom(service, "pending-student-ref-homeroom")
    _conversation, turn = _conversation_with_turn(
        service,
        "pending-student-ref-turn",
        message="合成学生甲确诊有多动症。",
    )
    task = port.prepare_calls[-1]
    ref = _student_ref(service)
    outcome = service.intake.ai_task_adapter.persist_model_result(
        task_id=str(turn["task_id"]),
        source_ref=task["source_ref"],
        context_refs=task["context_refs"],
        result={
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已整理专业信息草稿。",
            "clarification_questions": [],
            "work_items": [_work_item(
                "pending-student-ref-item",
                domain="student_support",
                mode="record",
                intent="create",
                refs=[ref],
                draft={
                    "summary": "教师报告合成学生甲已有相关专业结论。",
                    "profile_update": {
                        "summary": "教师报告合成学生甲已有相关专业结论。",
                        "dimensions": [],
                        "open_questions": [],
                        "support_focus": [],
                    },
                },
            )],
        },
    )
    handoff_id = str(outcome["handoff_ids"][0])

    client = _client(service)
    response = client.get(
        f"/api/class-teacher/intake/subjects/{quote('一班|A001', safe='')}/pending-handoffs"
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["handoff_id"] for item in items] == [handoff_id]
    assert items[0]["student_ref"] == "一班|A001"
