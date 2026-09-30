"""班级分析（教师版）内嵌页面：状态存储、生成 job 与阅卷完成后的自动触发。

- 每场次一个状态文件：受控 reports 目录下 `.class_analysis/{session_id}.json`，
  原子写入；AI 叙述本体直接内嵌在状态文件中，另按
  (session_id, score_revision, rendition, report_key="class:{class_name}") 进
  AnalysisNarrativeCache，重新生成命中缓存不再调用模型。
- 页面数据（data）不落盘，GET 时按当前成绩实时装配；状态文件只记叙述与
  score_revision，用于叙述 stale 判定。逐题错因归并同存于该状态文件，保留
  批语映射及评分要求，读取时匹配当前输入并按所选班级去重统计人数。
  归并结果同时物化为学生×题错因记录（error_records），供个人报告按题读取。
- 错因整理由手动 kind=causes 任务触发，并作为个人报告导出 job 的前置阶段
  （retry_failed=False：失败题不重发，报告照常生成）；普通读取、切班与
  自动报告不调用。
- 自动生成挂钩点：阅卷 run 判定为 completed 且该场次无未批完答卷时，
  由 default_handlers 里的 grading_run handler 调用
  maybe_auto_generate_class_analysis。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from analysis_report_prompts import CLASS_MAX_TOKENS, CLASS_SYSTEM_PROMPT
from backend.jobs.manager import (
    ActiveJobExistsError,
    JobContext,
    JobManager,
)
from backend.jobs.store import JobRecord
from backend.repositories.access import GradingRepositoryAccess, as_grading_repositories
from backend.repositories.grading_database import open_grading_repositories

LOGGER = logging.getLogger(__name__)

CLASS_ANALYSIS_JOB_TYPE = "class_analysis_generate"
CLASS_ANALYSIS_STATE_DIRNAME = ".class_analysis"
CLASS_ANALYSIS_RENDITION_VERSION = "class_analysis_page_v3_class_scope"
CLASS_ANALYSIS_REPORT_KEY = "class:session"
NARRATIVE_CACHE_DIRNAME = ".analysis_narrative_cache"
CAUSE_ANALYSIS_VERSION = "class_error_causes_v3"
CAUSE_KINDS = frozenset({"error", "process", "response_state", "carry_forward", "review"})
# 仍可展示的旧版整理结果：v1 只有文本归并，v2 有 kind/manifestation 但没有大类。
CAUSE_OUTDATED_VERSION = "class_error_causes_v2"
CAUSE_LEGACY_VERSION = "class_error_causes_v1"

CAUSE_ANALYSIS_PROMPT = """你是数学教师，依据本场考试的题目、参考解答、已有作答证据与批语整理失分情况，不重新评分。
evidence 的每个 id 代表相同的批语、作答及前问证据组合，不是学生身份。批语是解释来源，不是不可质疑的事实。
先看 question_text、reference_analysis 与 canonical_answer，再对照 rubric 和作答；只用现有证据，缺资料应明确待核对，不重新识别图片。
每组分为 kind：
error：作答支持的数学或单位错误。只描述可观察的错误，不猜粗心、态度、不会某知识。
process：未写依据、关键推导未展示、未完成求解等过程缺项，与数学错误分开。
response_state：未作答、全部作废无有效内容、仅无关作答；不能依据低分或默认字段猜空白。
carry_forward：沿用前问错误量，当前关系在该输入下成立。必须给出该证据 previous_answers 中的 source_question_id。若另有独立新错，可另列 error，不能把每个受影响步骤重复归错。
review：字迹辨认、标准与参考解答冲突、等价形式争议、证据不足等需要核对的具体问题。教师确认的是分数，不能据此消除旧批语中的辨认疑点。
kind 为 error、process、response_state 时必须给 category，只能从下面 7 个固定大类中选一个；carry_forward 与 review 不填 category：
概念理解：概念、定义、公式、定理本身的理解或记忆有误。
计算与化简：运算、符号、通分约分、结果没化到最简等。
审题与条件：读错题意、漏用条件、读图或单位有误、多选漏选。
方法与思路：选错方法、缺辅助线、漏分类讨论、不会建立模型。
过程与依据：关键步骤或理由没写、推理断裂、未完成求解。
书写与规范：答句、单位、格式、书写辨认等规范问题。
未作答：空白、全部作废、只有无关内容。
解答题若能定位到 rubric 中具体判定点，填 step_id（只能取 rubric 里出现的 step_id），定位不了就省略。
known_patterns 列出本题（含同题库的以往考试）或本场其他题已用过的错法名称；同义时必须复用其中的 reason，只有确实不同的错法才允许新命名。
reason 为可复用的规范名称，例如“选错目标量的组成部分”；manifestation 为本题具体表现，例如“求绳长时多加水平边”。同一规范错因的不同表现用相同 reason 分别列组，系统合并人数并保留表现。
每个有分歧的方面单独处理；一份可以同时有过程缺项、确定错误与待核对项。不得用不确定猜测填满数学错因。
判断边界：只写直角结论没证明是 process；先假设待证直角再据此论证才是循环论证。停在12x=28是未完成求解，算出x=2才是计算错。
28/12与7/3等价，是否必须化简属于书写要求；参考解答也未展开平方时，不把未展开直接判为数学错误。已改正并保留的最终答案不能仍算成旧的划去答案。
绳长中多加一段与替错一段可共用规范名称，但 manifestation 必须区分。沿用前问错误绳长后运算自洽，不再推断不会勾股定理。
没有作答过程时，不从选项或错误数字推测具体认知错因；保留可观察的选答表现，并归 review 的“过程原因未明”。
肯定表述不成为错因；全部证据只支持正确、且没有任何待核对方面时放 positive_ids。整条不足以整理的放 uncertain_ids，遗漏项也由系统保留待核对。
仅返回 JSON：{"groups":[{"kind":"error","category":"计算与化简","reason":"规范错因","manifestation":"本题证据支持的具体表现","evidence_ids":["E1"],"source_question_id":null,"step_id":null}],"positive_ids":[],"uncertain_ids":[]}。
覆盖全部输入 id，只用输入 id；同一 id 可在多个组，但 positive_ids、uncertain_ids 与组成员互斥。不要输出人数、姓名、分数或评分调整；人数由系统去重。
"""


def _cause_text(record: Any) -> str:
    from backend.error_causes import clean_cause_text

    parts = []
    seen = set()
    for label, value in (("扣分理由", record.deduction_reason), ("错因", record.error_summary),
                         ("错误类别", record.error_category), ("教师批语", record.teacher_comment)):
        text = clean_cause_text(value)
        if text and text not in seen:
            seen.add(text)
            parts.append(f"{label}：{text}")
    return "\n".join(parts) or "未记录具体错因"


def assemble_cause_data(db: Any, session_id: int, *, data_root: Path | None = None) -> Any:
    """复用成绩快照、作答文字和已绑定题目，跳过报告图片与题库回填。"""
    from backend.session_analysis import (
        assemble_session_analysis,
        enrich_personal_questions,
    )
    repositories = as_grading_repositories(db)
    data = assemble_session_analysis(repositories, session_id, data_root=data_root,
                                     page_only=True, include_answer_evidence=True)
    enrich_personal_questions(repositories, data, data_root, include_images=False)
    return data


def _cause_rubric(data: Any, question_id: str) -> dict[str, Any]:
    from backend.session_analysis import parent_question_id
    allowed = {"question_id", "question_type", "part_id", "max_score", "stem_summary", "question_text",
               "text", "parts", "deduction_policy", "proof_obligations", "steps", "canonical_answer",
               "step_id", "description", "evidence", "evidence_required", "score", "points",
               "deduction_rules", "answer_only", "final_answer_required", "equivalent_answers",
               "part_score", "step_score", "core_goal", "required_elements", "allow_alternative_methods",
               "presentation_rules", "rule_id", "rule", "max_deduction", "require_final_answer",
               "answer_only_max_score", "response_mode", "accepted_forms", "answer"}

    def project(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: project(item) for key, item in value.items() if key in allowed}
        if isinstance(value, list):
            return [project(item) for item in value]
        return value

    return {key: value for q in data.rubric.get("questions", []) if isinstance(q, dict)
            and parent_question_id(str(q.get("question_id") or "")) == parent_question_id(question_id)
            for key, value in project(q).items()}


def _cause_evidence(student: Any, record: Any) -> dict[str, Any]:
    from backend.session_analysis import natural_question_order, parent_question_id
    siblings = {item.question_id: item for item in student.records
                if parent_question_id(item.question_id) == parent_question_id(record.question_id)}
    order = natural_question_order(siblings)
    previous = [siblings[qid] for qid in order[:order.index(record.question_id)]]
    return {
        "text": _cause_text(record), "student_answer": record.student_answer,
        "evidence_steps": record.evidence_steps, "missing_steps": record.missing_steps,
        "teacher_confirmed": record.teacher_confirmed,
        "previous_answers": [{"question_id": item.question_id, "student_answer": item.student_answer,
                              "text": _cause_text(item), "evidence_steps": item.evidence_steps}
                             for item in previous],
    }


def _evidence_key(evidence: dict[str, Any]) -> str:
    return json.dumps({key: value for key, value in evidence.items() if key != "id"},
                      ensure_ascii=False, sort_keys=True)


def _evidence_hash(evidence_key: str) -> str:
    return hashlib.sha256(evidence_key.encode("utf-8")).hexdigest()[:20]


def _cause_input_fingerprint(source: dict[str, Any]) -> str:
    """整理输入指纹：known_patterns 每次生成时可变，不参与新旧判定。"""
    comparable = {key: value for key, value in source.items() if key != "known_patterns"}
    return hashlib.sha256(
        json.dumps(comparable, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def cause_input_matches(saved: dict[str, Any], source: dict[str, Any]) -> bool:
    """Current input, or an older export missing only supplemental bank text.

    Legacy external runs omitted question_text/reference_analysis. Accept their
    original evidence, answers and rubric only when those still match exactly;
    never treat an edited, previously populated field as compatible.
    """
    previous = saved.get("input")
    fingerprint = saved.get("input_fingerprint") or (
        _cause_input_fingerprint(previous) if isinstance(previous, dict) else None
    )
    if fingerprint == _cause_input_fingerprint(source):
        return True
    if not isinstance(previous, dict) or fingerprint != _cause_input_fingerprint(previous):
        return False
    if previous.get("question_text"):
        return False
    comparable = dict(source)
    for key in ("question_text", "reference_analysis"):
        if not previous.get(key):
            if key in previous:
                comparable[key] = previous[key]
            else:
                comparable.pop(key, None)
    return fingerprint == _cause_input_fingerprint(comparable)


def _merge_known_patterns(*groups: Any) -> list[dict[str, Any]]:
    """合并多个来源的已知错法名，按 reason 去重，控制提示词长度。"""
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group or []:
            if not isinstance(item, dict):
                continue
            reason = str(item.get("reason") or "").strip()
            if reason and reason not in merged:
                merged[reason] = {
                    "reason": reason,
                    "category": item.get("category"),
                    "scope": str(item.get("scope") or "以往整理"),
                }
            if len(merged) >= 20:
                return list(merged.values())
    return list(merged.values())


def build_cause_inputs(data: Any, *, known_patterns: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """全场共用逐题输入；只有批语、作答及前问证据都相同才合并，不发送身份。"""
    patterns = known_patterns or {}
    shared = patterns.get("shared") or []
    by_question = patterns.get("questions") or {}
    evidence: dict[str, dict[str, dict[str, Any]]] = {}
    for student in data.students:
        for record in student.records:
            if record.lost:
                item = _cause_evidence(student, record)
                evidence.setdefault(record.question_id, {})[_evidence_key(item)] = item

    from backend.session_analysis import parent_question_id
    return [{
        "question_id": info.question_id, "max_score": info.max_score,
        "stem_summary": info.stem_summary, "canonical_answer": info.canonical_answer,
        "question_text": info.question_text, "reference_analysis": info.reference_analysis,
        "rubric": _cause_rubric(data, info.question_id),
        "evidence": [{"id": f"E{index}", **item} for index, (_key, item) in
                     enumerate(sorted(evidence[info.question_id].items()), start=1)],
        "known_patterns": _merge_known_patterns(
            by_question.get(parent_question_id(info.question_id)), shared,
        ),
    } for info in data.questions if evidence.get(info.question_id)]


def _linked_question_sources(
    question_bank_db_path: Path | None, session_id: int,
) -> dict[str, set[tuple[int, str]]]:
    """{本场父级题号: {(其他场次id, 对方父级题号)}}。

    按题库 grading_question_links 的同题关联，再经 question_duplicate_links
    的判重副本扩展；题库缺失或没有关联时返回空，不报错。查询实现与
    error_patterns.session_bank_context 共用。
    """
    from backend.error_patterns import session_bank_context

    return {
        parent: set(context["linked"])
        for parent, context in session_bank_context(question_bank_db_path, session_id).items()
    }


def known_cause_patterns(
    store: Any, question_bank_db_path: Path | None, session_id: int,
) -> dict[str, Any]:
    """整理时可复用的错法名：本场其他题已整理的（shared）+ 跨场次同题的（questions）。"""
    from backend.error_causes import CAUSE_KIND_CATEGORIES
    from backend.session_analysis import parent_question_id

    def usable_groups(state: Any) -> Iterable[dict[str, Any]]:
        stored = (((state or {}).get("cause_analysis") or {}).get("questions")) or {}
        for entry in stored.values():
            for group in ((entry.get("result") or {}).get("groups") or []):
                if isinstance(group, dict) and group.get("kind") in CAUSE_KIND_CATEGORIES:
                    yield group

    shared: dict[str, dict[str, Any]] = {}
    for group in usable_groups(store.load(session_id)):
        reason = str(group.get("reason") or "").strip()
        if reason:
            shared.setdefault(reason, {
                "reason": reason, "category": group.get("category"), "scope": "本场已整理",
            })
    questions: dict[str, dict[str, dict[str, Any]]] = {}

    def collect(parent: str, reason: Any, category: Any, scope: str) -> None:
        text = str(reason or "").strip()
        if text:
            questions.setdefault(parent, {}).setdefault(text, {
                "reason": text, "category": category, "scope": scope,
            })

    from backend.error_patterns import (
        answer_pattern_map,
        bank_confirmed_triggers,
        option_analysis_entries,
        session_bank_context,
    )

    state = store.load(session_id) or {}
    # 本场候选库（选项诊断 + 填空错误答案）也作为可复用名称进入提示词。
    for parent, bucket in answer_pattern_map(state).items():
        for answer, item in bucket.items():
            if str(answer).startswith("_") or not isinstance(item, dict):
                continue
            collect(parent, item.get("pattern"), item.get("category"), "本题候选")
    for qid, entry in option_analysis_entries(state).items():
        for item in (entry.get("analysis") or {}).values():
            if isinstance(item, dict):
                collect(parent_question_id(str(qid)), item.get("pattern"),
                        item.get("category"), "本题候选")
    bank_context = session_bank_context(question_bank_db_path, session_id)
    confirmed = bank_confirmed_triggers(
        question_bank_path=question_bank_db_path,
        question_ids=sorted({bid for ctx in bank_context.values() for bid in ctx["bank_ids"]}),
    )
    from question_bank.services.error_pattern_service import preferred_active_patterns

    for parent, ctx in bank_context.items():
        for bank_id in ctx["bank_ids"]:
            for row in preferred_active_patterns(confirmed.get(bank_id) or []):
                collect(parent, row.get("pattern"), row.get("category"), "题库已有")
    for parent, links in _linked_question_sources(question_bank_db_path, session_id).items():
        for other_sid, other_parent in links:
            other = store.load(other_sid)
            stored = (((other or {}).get("cause_analysis") or {}).get("questions")) or {}
            for qid, entry in stored.items():
                if parent_question_id(qid) != other_parent:
                    continue
                for group in (entry.get("result") or {}).get("groups") or []:
                    if isinstance(group, dict) and group.get("kind") in CAUSE_KIND_CATEGORIES:
                        collect(parent, group.get("reason"), group.get("category"), "同题以往整理")
            # 关联场次的选项诊断与填空错法同样可复用名称。
            for item in (answer_pattern_map(other).get(other_parent) or {}).values():
                if isinstance(item, dict):
                    collect(parent, item.get("pattern"), item.get("category"), "同题以往整理")
            for qid, entry in option_analysis_entries(other).items():
                if parent_question_id(str(qid)) != other_parent:
                    continue
                for item in (entry.get("analysis") or {}).values():
                    if isinstance(item, dict):
                        collect(parent, item.get("pattern"), item.get("category"), "同题以往整理")
    return {
        "shared": list(shared.values()),
        "questions": {key: list(items.values()) for key, items in questions.items()},
    }


def _rubric_step_ids(rubric: Any) -> set[str]:
    """rubric 投影里允许引用的判定点 id。"""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "step_id" and isinstance(value, str) and value.strip():
                    found.add(value.strip())
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(rubric)
    return found


def normalize_cause_result(payload: Any, source: dict[str, Any]) -> dict[str, Any]:
    """保存类型、大类、规范名称、本题表现与证据映射；人数不采纳模型输出。"""
    from backend.error_causes import CAUSE_KIND_CATEGORIES, normalize_cause_category

    if not isinstance(payload, dict) or not isinstance(payload.get("groups"), list):
        raise ValueError("invalid cause classification")
    known = {item["id"] for item in source["evidence"]}
    step_ids = _rubric_step_ids(source.get("rubric"))
    known_reasons = {
        str(item.get("reason") or "").strip()
        for item in source.get("known_patterns") or []
        if isinstance(item, dict)
    }

    def ids(value: Any) -> set[str]:
        if not isinstance(value, list) or any(not isinstance(item, str) or item not in known for item in value):
            raise ValueError("invalid evidence reference")
        return set(value)

    groups: dict[tuple[str, str], dict[tuple[str, str | None], set[str]]] = {}
    meta: dict[tuple[str, str], dict[str, Any]] = {}
    source_evidence = {item["id"]: item for item in source["evidence"]}
    for group in payload["groups"]:
        if not isinstance(group, dict) or not isinstance(group.get("reason"), str) or not group["reason"].strip():
            raise ValueError("invalid cause label")
        kind, manifestation = group.get("kind"), group.get("manifestation")
        if kind not in CAUSE_KINDS or not isinstance(manifestation, str) or not manifestation.strip():
            raise ValueError("invalid cause kind or manifestation")
        category = None
        if kind in CAUSE_KIND_CATEGORIES:
            category = normalize_cause_category(group.get("category"))
            if category is None or category not in CAUSE_KIND_CATEGORIES[kind]:
                raise ValueError("invalid cause category")
        step_id = group.get("step_id")
        if step_id is not None:
            if not isinstance(step_id, str) or step_id.strip() not in step_ids:
                raise ValueError("invalid step reference")
            step_id = step_id.strip()
        members = ids(group.get("evidence_ids"))
        previous_id = group.get("source_question_id")
        if kind == "carry_forward":
            if not isinstance(previous_id, str) or not members or any(
                previous_id not in {item["question_id"] for item in source_evidence[key].get("previous_answers", [])}
                for key in members
            ):
                raise ValueError("invalid previous question reference")
        elif previous_id is not None:
            raise ValueError("unexpected previous question reference")
        if members:
            key = (kind, group["reason"].strip())
            groups.setdefault(key, {}).setdefault(
                (manifestation.strip(), previous_id), set()).update(members)
            meta.setdefault(key, {
                "category": category,
                "step_id": step_id,
                "pattern_status": "existing" if group["reason"].strip() in known_reasons else "candidate",
            })
    positive = ids(payload.get("positive_ids", []))
    uncertain = ids(payload.get("uncertain_ids", []))
    assigned = {key for variants in groups.values() for members in variants.values() for key in members}
    if assigned & (positive | uncertain) or positive & uncertain:
        raise ValueError("conflicting evidence classification")
    uncertain |= known - assigned - positive - uncertain
    return {
        "groups": [{"kind": kind, "reason": reason,
                    "category": meta[(kind, reason)]["category"],
                    "step_id": meta[(kind, reason)]["step_id"],
                    "pattern_status": meta[(kind, reason)]["pattern_status"],
                    "evidence_ids": sorted({key for members in variants.values() for key in members}),
                    "manifestations": [{"description": description, "source_question_id": previous_id,
                                        "evidence_ids": sorted(members)}
                                       for (description, previous_id), members in variants.items()]}
                   for (kind, reason), variants in groups.items()],
        "positive_ids": sorted(positive), "uncertain_ids": sorted(uncertain),
    }


def student_error_records(data: Any, source: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    """把本题归并结果投影成学生×题错因记录；证据哈希供读取侧校验输入是否变化。"""
    from backend.error_causes import CAUSE_KIND_CATEGORIES

    hash_to_id = {
        _evidence_hash(_evidence_key(item)): item["id"] for item in source["evidence"]
    }
    rows: list[dict[str, Any]] = []
    for student in data.students:
        for record in student.records:
            if record.question_id != source["question_id"] or not record.lost:
                continue
            digest = _evidence_hash(_evidence_key(_cause_evidence(student, record)))
            evidence_id = hash_to_id.get(digest)
            if evidence_id is None:
                continue
            for group in result.get("groups") or []:
                if group.get("kind") not in CAUSE_KIND_CATEGORIES:
                    continue
                descriptions = [
                    variant["description"] for variant in group.get("manifestations") or []
                    if evidence_id in set(variant.get("evidence_ids") or [])
                ]
                if not descriptions:
                    continue
                rows.append({
                    "student_id": student.student_id,
                    "question_id": source["question_id"],
                    "evidence_hash": digest,
                    "kind": group["kind"],
                    "category": group.get("category"),
                    "pattern": group["reason"],
                    "manifestation": "；".join(descriptions),
                    "step_id": group.get("step_id"),
                    "pattern_status": group.get("pattern_status") or "candidate",
                    "score": record.score,
                    "max_score": record.max_score,
                    "lost_points": record.lost_points,
                    "version": CAUSE_ANALYSIS_VERSION,
                })
    return rows


def save_cause_result(
    store: Any, session_id: int, source: dict[str, Any], payload: Any,
    *, origin: str = "model", data: Any = None,
) -> dict[str, Any]:
    """模型生成与本次助手归类共用同一校验、保存入口，逐题保存已完成结果。

    传入 data 时同步把结果物化为学生错因记录（state.error_records），
    供个人报告按题读取；不触发数据库迁移。
    """
    result = normalize_cause_result(payload, source)
    state = store.load(session_id) or {}
    current = state.get("cause_analysis") or {}
    questions = dict(current.get("questions") or {})
    previous = questions.get(source["question_id"]) or {}
    history = list(previous.get("history") or [])
    if previous.get("result") and any((previous.get("version") != CAUSE_ANALYSIS_VERSION,
                                      previous.get("input") != source, previous.get("result") != result)):
        history.append({key: value for key, value in previous.items() if key not in {"history", "failed"}})
    fingerprint = _cause_input_fingerprint(source)
    questions[source["question_id"]] = {
        "version": CAUSE_ANALYSIS_VERSION, "input": source, "input_fingerprint": fingerprint,
        "result": result,
        "generated_at": _now_iso(), "origin": origin, "failed": False,
        "history": history,
    }
    fields: dict[str, Any] = {"cause_analysis": {"questions": questions}}
    if data is not None:
        records = dict(state.get("error_records") or {})
        records[source["question_id"]] = {
            "input_fingerprint": fingerprint,
            "generated_at": _now_iso(),
            "records": student_error_records(data, source, result),
        }
        fields["error_records"] = records
    store.save(session_id, **fields)
    return result


def apply_cause_results(
    page: dict[str, Any] | None, data: Any, all_inputs: list[dict[str, Any]],
    state: Any, *, session_id: int | None = None,
    question_bank_path: Path | None = None,
) -> dict[str, Any]:
    """匹配当前作答证据后按班级投影；兼容的旧归并明确标记，等待手动升级。

    v3 结果按输入指纹判定新鲜；v2 旧结果在证据一致时仍展示（无错误大类，
    标记 causes_outdated 等待重新整理）；v1 文本归并走原 legacy 路径。
    传入 session_id + 题库路径时，额外标注每题题库关联。
    """
    from backend.error_causes import CAUSE_CATEGORIES
    from backend.error_patterns import session_bank_context
    from backend.session_analysis import parent_question_id

    stored = ((state or {}).get("cause_analysis") or {}).get("questions") or {}
    bank_map: dict[str, int] = {}
    if session_id is not None and question_bank_path is not None:
        bank_context = session_bank_context(question_bank_path, int(session_id))
        bank_map = {parent: int(ctx["bank_id"]) for parent, ctx in bank_context.items()}
    sources = {source["question_id"]: source for source in all_inputs}
    ready, failed, legacy_count, outdated_count, stale = 0, 0, 0, 0, False
    times, origins = [], set()
    question_pages = {item["question_id"]: item for item in (page or {}).get("questions", [])}
    for question_id, source in sources.items():
        saved = stored.get(question_id) or {}
        fresh = (saved.get("version") == CAUSE_ANALYSIS_VERSION and cause_input_matches(saved, source)
                 and isinstance(saved.get("result"), dict))
        old_version = saved.get("version")
        old_source = saved.get("input") or {}
        compatible = isinstance(saved.get("result"), dict) and all(
            old_source.get(key) == source.get(key)
            for key in ("question_id", "max_score", "stem_summary", "canonical_answer")
        ) and {item.get("text") for item in old_source.get("evidence", [])} == {
            item["text"] for item in source["evidence"]}
        legacy = compatible and old_version == CAUSE_LEGACY_VERSION
        outdated = compatible and old_version == CAUSE_OUTDATED_VERSION
        text_match = legacy or outdated
        if not fresh:
            stale |= bool(saved.get("result"))
            failed += int(bool(saved.get("failed")))
            if not text_match:
                continue
            if legacy:
                legacy_count += 1
            else:
                outdated_count += 1
        else:
            ready += 1
        times.append(saved.get("generated_at") or "")
        origins.add(saved.get("origin") or "model")
        question = question_pages.get(question_id)
        if question is None:
            continue
        by_evidence: dict[str, set[int]] = {}
        for student in data.students:
            for record in student.records:
                if record.question_id == question_id and record.lost:
                    key = _cause_text(record) if text_match else _evidence_key(_cause_evidence(student, record))
                    by_evidence.setdefault(key, set()).add(student.student_id)
        evidence = {item["id"]: item for item in (old_source if text_match else source)["evidence"]}

        def details(member_ids: list[str]) -> list[dict[str, Any]]:
            result = []
            for key in member_ids:
                item = evidence.get(key)
                if item is None:
                    continue
                lookup = item["text"] if text_match else _evidence_key(item)
                if members := by_evidence.get(lookup):
                    result.append({**{field: value for field, value in item.items() if field != "id"},
                                   "student_ids": sorted(members)})
            return result

        causes = []
        for group in saved["result"]["groups"]:
            items = details(group["evidence_ids"])
            members = {sid for item in items for sid in item["student_ids"]}
            if members:
                cause = {"reason": group["reason"], "count": len(members), "evidence": items}
                if group.get("kind") is not None:
                    cause["kind"] = group["kind"]
                    cause["manifestations"] = [
                        {"description": variant["description"], "source_question_id": variant["source_question_id"],
                         "evidence": visible}
                        for variant in group["manifestations"] if (visible := details(variant["evidence_ids"]))
                    ]
                if group.get("category"):
                    cause["category"] = group["category"]
                if group.get("step_id"):
                    cause["step_id"] = group["step_id"]
                if group.get("pattern_status"):
                    cause["pattern_status"] = group["pattern_status"]
                cause["teacher_edited"] = bool(group.get("teacher_edited"))
                causes.append(cause)
        question["causes"] = sorted(causes, key=lambda item: -item["count"])
        category_members: dict[str, set[int]] = {}
        for cause in question["causes"]:
            category = str(cause.get("category") or "")
            if not category:
                continue
            bucket = category_members.setdefault(category, set())
            for item in cause["evidence"]:
                bucket.update(item.get("student_ids") or [])
        category_order = {name: index for index, name in enumerate(CAUSE_CATEGORIES)}
        question["cause_category_counts"] = [
            {"category": category, "count": len(members)}
            for category, members in sorted(
                category_members.items(),
                key=lambda kv: (-len(kv[1]), category_order.get(kv[0], len(category_order)), kv[0]),
            )
        ]
        question["bank_question_id"] = bank_map.get(parent_question_id(question_id))
        question["cause_review"] = {
            "positive": details(saved["result"]["positive_ids"]),
            "uncertain": details(saved["result"]["uncertain_ids"]),
        }
        question["causes_grouped"] = True
        question["causes_legacy"] = bool(legacy)
        question["causes_outdated"] = bool(outdated)
    total = len(sources)
    return {"status": "ready" if ready == total else "partial" if ready else "not_generated",
            "pending_questions": total - ready, "total_questions": total, "failed_questions": failed,
            "legacy_questions": legacy_count, "outdated_questions": outdated_count,
            "stale": stale, "generated_at": max(times, default="") or None,
            "origin": "assistant" if origins == {"assistant"} else "model" if origins else None}


def _ensure_error_records(store: Any, session_id: int, state: Any,
                          source: dict[str, Any], saved: dict[str, Any], data: Any) -> None:
    """已整理且输入未变的题：补齐早期任务未物化的学生错因记录。"""
    fingerprint = ((saved.get("input_fingerprint") or _cause_input_fingerprint(saved.get("input") or source))
                   if cause_input_matches(saved, source) else _cause_input_fingerprint(source))
    records_state = dict(state.get("error_records") or {})
    envelope = records_state.get(source["question_id"]) or {}
    if envelope.get("input_fingerprint") == fingerprint and isinstance(envelope.get("records"), list):
        return
    records_state[source["question_id"]] = {
        "input_fingerprint": fingerprint,
        "generated_at": _now_iso(),
        "records": student_error_records(data, source, saved["result"]),
    }
    store.save(session_id, error_records=records_state)


def _resolve_option_source(
    source: dict[str, Any], bank_row: dict[str, Any] | None,
) -> tuple[str, list[str], str]:
    """选项分析的题目来源：题干优先用整理输入，解析不出选项时退回题库正文。"""
    from backend.error_patterns import (
        extract_canonical_option,
        normalize_option_answer,
        parse_option_letters,
    )

    text = str(source.get("question_text") or "")
    letters = parse_option_letters(text)
    bank_text = str((bank_row or {}).get("question_text") or "")
    if len(letters) < 2 and bank_text:
        text, letters = bank_text, parse_option_letters(bank_text)
    correct = normalize_option_answer(source.get("canonical_answer")) or extract_canonical_option(
        (bank_row or {}).get("answer_text"))
    return text, letters, correct


def _bank_pattern_rows(ctx: dict[str, Any], confirmed_by_bank: dict[int, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [
        row for bank_id in (ctx.get("bank_ids") or set())
        for row in confirmed_by_bank.get(int(bank_id)) or []
    ]


def plan_cause_question(
    store: Any, session_id: int, source: dict[str, Any], *,
    qtype: str, option_scope: bool, ctx: dict[str, Any],
    confirmed_by_bank: dict[int, list[dict[str, Any]]],
    question_bank_path: Path | None, retry_failed: bool,
) -> dict[str, Any]:
    """整理路径决策，任务执行与报告 preflight 共用同一口径。

    path:
    - "option"：选择题选项映射（option.patterns 已有时零调用）。
    - "fill_covered"：填空错误答案库全覆盖，直接合成（零调用）。
    - "fill_v3" / "v3"：走 v3 整题整理（fill_v3 成功后回写答案库）。
    - "skip"：选项诊断此前失败且输入未变、本次不重试。
    needs_call：本次是否会产生一次模型调用。
    """
    from backend.error_patterns import (
        CHOICE_TYPES,
        FILL_TYPES,
        bank_question_row,
        find_option_analysis,
        merge_bank_triggers_into_patterns,
        question_fingerprint,
        synthesize_answer_result,
    )
    from backend.session_analysis import parent_question_id

    parent = parent_question_id(source["question_id"])
    if qtype in CHOICE_TYPES and option_scope and ctx.get("bank_id"):
        text, letters, correct = _resolve_option_source(
            source, bank_question_row(question_bank_path, int(ctx["bank_id"])),
        )
        if len(letters) < 2 or not correct:
            return {"path": "v3", "needs_call": True}
        fingerprint = question_fingerprint(text, correct)
        option = {"text": text, "letters": letters, "correct": correct,
                  "fingerprint": fingerprint, "patterns": None,
                  "patterns_source": None, "bank_id": int(ctx["bank_id"])}
        bank_rows = _bank_pattern_rows(ctx, confirmed_by_bank)
        blocked = {
            str(row.get("trigger_value") or "").strip()
            for row in bank_rows
            if row.get("trigger_kind") == "option" and row.get("status") == "rejected"
        }
        available = merge_bank_triggers_into_patterns(
            bank_rows, trigger_kind="option")
        available = {key: value for key, value in available.items() if key not in blocked}
        required = set(letters) - {correct} - blocked
        option["required"] = sorted(required)
        option["blocked"] = sorted(blocked)
        entry = find_option_analysis(
            store, session_id, parent, fingerprint, ctx.get("linked") or ())
        if entry and entry.get("analysis") and not entry.get("failed"):
            saved_patterns = {
                key: dict(value) for key, value in entry["analysis"].items()
                if isinstance(value, dict) and key not in blocked
            }
            saved_patterns.update(available)
            option["patterns"] = saved_patterns
            if required.issubset(saved_patterns) or entry.get("source") == "model":
                return {"path": "option", "needs_call": False, "option": option}
            option["base_patterns"] = saved_patterns
            option["patterns"] = None
            return {"path": "option", "needs_call": True, "option": option}
        if entry and entry.get("failed") and not retry_failed:
            return {"path": "skip", "needs_call": False, "option": option}
        if required.issubset(available):
            option["patterns"] = available
            option["patterns_source"] = "bank_confirmed"
            return {"path": "option", "needs_call": False, "option": option}
        option["base_patterns"] = available
        return {"path": "option", "needs_call": True, "option": option}
    if qtype in FILL_TYPES:
        library = _fill_answer_library(
            store=store, session_id=session_id, parent=parent,
            ctx=ctx, confirmed_by_bank=confirmed_by_bank,
        )
        payload = synthesize_answer_result(source, library, source.get("canonical_answer"))
        if payload is not None:
            return {"path": "fill_covered", "needs_call": False, "payload": payload}
        return {"path": "fill_v3", "needs_call": True}
    return {"path": "v3", "needs_call": True}


def _run_option_plan(
    *, store: Any, session_id: int, source: dict[str, Any], data: Any,
    plan: dict[str, Any], get_client: Callable[[], Any],
) -> None:
    """执行选择题选项映射：patterns 缺失时调用一次模型做选项诊断。"""
    from backend.error_patterns import (
        OPTION_ANALYSIS_PROMPT,
        OPTION_ANALYSIS_VERSION,
        build_option_analysis_input,
        normalize_option_analysis,
        save_option_analysis,
        synthesize_option_result,
    )

    patterns = plan.get("patterns")
    if patterns is None:
        client = get_client()
        if client is None:
            raise ValueError("content generation model is not configured")
        call_input = build_option_analysis_input(
            plan["text"], plan["correct"], str(source.get("reference_analysis") or ""))
        call_input["missing_options"] = [
            letter for letter in plan["required"]
            if letter not in plan.get("base_patterns", {})
        ]
        try:
            payload = client.json_from_text(
                OPTION_ANALYSIS_PROMPT + "\n" + json.dumps(call_input, ensure_ascii=False),
                extra_kwargs={"temperature": 0.2, "max_tokens": 8000},
            )
            predicted = normalize_option_analysis(
                payload, option_letters=plan["letters"], correct_option=plan["correct"])
            blocked = set(plan.get("blocked") or ())
            patterns = {
                letter: item
                for letter, item in {**predicted, **plan.get("base_patterns", {})}.items()
                if letter not in blocked
            }
        except Exception:
            save_option_analysis(store, session_id, source["question_id"], {
                "version": OPTION_ANALYSIS_VERSION,
                "input_fingerprint": plan["fingerprint"],
                "bank_question_id": plan["bank_id"], "failed": True,
                "failed_input_fingerprint": plan["fingerprint"],
            })
            raise
        save_option_analysis(store, session_id, source["question_id"], {
            "version": OPTION_ANALYSIS_VERSION,
            "input_fingerprint": plan["fingerprint"],
            "bank_question_id": plan["bank_id"], "analysis": patterns,
            "source": "model", "analyzed_at": _now_iso(), "failed": False,
        })
    elif plan.get("patterns_source") == "bank_confirmed":
        save_option_analysis(store, session_id, source["question_id"], {
            "version": OPTION_ANALYSIS_VERSION,
            "input_fingerprint": plan["fingerprint"],
            "bank_question_id": plan["bank_id"], "analysis": patterns,
            "source": "bank_confirmed", "analyzed_at": _now_iso(), "failed": False,
        })
    save_cause_result(store, session_id, source,
                      synthesize_option_result(source, patterns),
                      origin="option_map", data=data)


def _fill_answer_library(
    *, store: Any, session_id: int, parent: str, ctx: dict[str, Any],
    confirmed_by_bank: dict[int, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """填空错误答案库：本场次 + 关联场次候选 + 题库已确认（确认优先）。"""
    from backend.error_patterns import (
        find_answer_patterns,
        merge_bank_triggers_into_patterns,
    )

    linked_ids = {sid for sid, _ in ctx.get("linked") or set()}
    library = find_answer_patterns(store, session_id, parent, linked_ids)
    rows = _bank_pattern_rows(ctx, confirmed_by_bank)
    blocked = {
        str(row.get("trigger_value") or "").strip()
        for row in rows
        if row.get("trigger_kind") == "wrong_answer" and row.get("status") == "rejected"
    }
    confirmed = merge_bank_triggers_into_patterns(rows, trigger_kind="wrong_answer")
    return {**{key: value for key, value in library.items() if key not in blocked},
            **{key: value for key, value in confirmed.items() if key not in blocked}}


def _organize_fill_question(
    *, store: Any, session_id: int, source: dict[str, Any], data: Any,
    ctx: dict[str, Any], confirmed_by_bank: dict[int, list[dict[str, Any]]],
) -> str:
    """填空题：错误答案库全覆盖时直接映射（0 次调用）；否则退回 v3 整理。"""
    from backend.error_patterns import synthesize_answer_result
    from backend.session_analysis import parent_question_id

    parent = parent_question_id(source["question_id"])
    library = _fill_answer_library(
        store=store, session_id=session_id, parent=parent,
        ctx=ctx, confirmed_by_bank=confirmed_by_bank,
    )
    payload = synthesize_answer_result(source, library, source.get("canonical_answer"))
    if payload is None:
        return "v3"
    save_cause_result(store, session_id, source, payload,
                      origin="pattern_library", data=data)
    return "done"


def run_cause_analysis(
    context: Any, *, db: Any, data_root: Path | None, store: Any,
    llm_client_factory: Callable[[], Any] | None,
    retry_failed: bool = True,
    progress_band: tuple[float, float] = (0.0, 1.0),
    progress_stage: str = "class_analysis",
) -> dict[str, object]:
    """逐题整理错因。

    retry_failed=False 用于个人报告导出的前置阶段：整理失败的题不自动重发，
    报告照常生成、该题不显示错误类型；手动「整理错因」保持默认重发行为。

    已关联题库的选择题先复用题库选项预测，缺项才补做选项诊断；填空题先查错误答案库，全覆盖零调用，
    否则走 v3 整理并把新错法按规范化答案回写候选库。
    """
    from backend.error_causes import CAUSE_KIND_CATEGORIES
    from backend.error_patterns import (
        FILL_TYPES,
        additions_from_v3_result,
        bank_confirmed_triggers,
        record_answer_patterns,
        session_bank_context,
        sync_session_patterns_to_bank,
    )
    from backend.session_analysis import parent_question_id, question_bank_db_path

    session_id = int(context.payload["session_id"])
    option_scope = True
    question_bank_path = question_bank_db_path(Path(db.db_path))
    bank_context = session_bank_context(question_bank_path, session_id)
    confirmed_by_bank = bank_confirmed_triggers(
        question_bank_path,
        sorted({bid for ctx in bank_context.values() for bid in ctx["bank_ids"]}),
    )
    data = assemble_cause_data(db, session_id, data_root=data_root)
    sources = build_cause_inputs(
        data,
        known_patterns=known_cause_patterns(store, question_bank_path, session_id),
    )
    qtypes = {info.question_id: str(info.question_type or "") for info in data.questions}
    client = None

    def get_client() -> Any:
        nonlocal client
        if client is None:
            client = llm_client_factory() if llm_client_factory else None
        return client

    failed = 0
    lo, hi = progress_band
    fresh_patterns: list[dict[str, Any]] = []
    for index, source in enumerate(sources):
        context.raise_if_cancelled()
        state = store.load(session_id) or {}
        old = ((state.get("cause_analysis") or {}).get("questions")) or {}
        saved = old.get(source["question_id"]) or {}
        fingerprint = _cause_input_fingerprint(source)
        if saved.get("version") == CAUSE_ANALYSIS_VERSION and cause_input_matches(saved, source) and saved.get("result"):
            _ensure_error_records(store, session_id, state, source, saved, data)
            continue
        if (not retry_failed and saved.get("failed")
                and saved.get("failed_input_fingerprint") == fingerprint):
            continue
        context.report(
            lo + (hi - lo) * (index + 0.2) / max(1, len(sources)),
            progress_stage, "grouping_error_causes",
        )
        qtype = qtypes.get(source["question_id"], "")
        ctx = bank_context.get(parent_question_id(source["question_id"])) or {}
        try:
            plan = plan_cause_question(
                store, session_id, source, qtype=qtype, option_scope=option_scope,
                ctx=ctx, confirmed_by_bank=confirmed_by_bank,
                question_bank_path=question_bank_path, retry_failed=retry_failed,
            )
            if plan["path"] == "skip":
                continue
            if plan["path"] == "option":
                _run_option_plan(store=store, session_id=session_id, source=source,
                                 data=data, plan=plan["option"], get_client=get_client)
                continue
            if plan["path"] == "fill_covered":
                save_cause_result(store, session_id, source, plan["payload"],
                                  origin="pattern_library", data=data)
                continue
            if get_client() is None:
                raise ValueError("content generation model is not configured")
            # 本次整理新产出的错法名也喂给后续题目，促使跨题复用同一名称；
            # 存储的 input 仍是不含动态项的快照，指纹不受提示词变化影响。
            prompt_source = {
                **source,
                "known_patterns": _merge_known_patterns(source.get("known_patterns"), fresh_patterns),
            }
            prompt = CAUSE_ANALYSIS_PROMPT + "\n" + json.dumps(prompt_source, ensure_ascii=False)
            payload = client.json_from_text(prompt, extra_kwargs={"temperature": 0.2, "max_tokens": 12000})
            context.raise_if_cancelled()
            result = save_cause_result(store, session_id, source, payload, data=data)
            if qtype in FILL_TYPES:
                additions = additions_from_v3_result(
                    source, result, source.get("canonical_answer"))
                if additions:
                    record_answer_patterns(
                        store, session_id,
                        parent_question_id(source["question_id"]), additions,
                        bank_question_id=ctx.get("bank_id"),
                    )
            for group in result["groups"]:
                if group["kind"] in CAUSE_KIND_CATEGORIES:
                    fresh_patterns.append({
                        "reason": group["reason"],
                        "category": group.get("category"),
                        "scope": "本次整理",
                    })
        except Exception:
            context.raise_if_cancelled()
            failed += 1
            # 失败不重发；其他已完成题目继续可用，旧输入的结果仍由读取端判定是否过期。
            old[source["question_id"]] = {
                **saved, "failed": True, "failed_input_fingerprint": fingerprint,
            }
            store.save(session_id, cause_analysis={"questions": old})
    # P5：整理产出自动回挂题库；回挂失败只记日志，不影响本场整理结果。
    try:
        written = sync_session_patterns_to_bank(
            store, session_id, question_bank_path, bank_context, current_sources=sources)
    except Exception:
        LOGGER.warning("sync session %s patterns to bank failed", session_id, exc_info=True)
        written = 0
    return {"session_id": session_id, "kind": "causes", "failed_questions": failed,
            "bank_patterns_written": written,
            "status": "failed" if failed else "ready"}


def student_error_map(
    state: Any, student: Any, sources: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """{question_id: [错因记录]}：输入指纹与证据哈希都一致才返回，过期题不展示。"""
    envelopes = (state or {}).get("error_records") or {}
    saved_questions = ((state or {}).get("cause_analysis") or {}).get("questions") or {}
    fingerprints = {
        source["question_id"]: (
            (saved_questions[source["question_id"]].get("input_fingerprint")
             or _cause_input_fingerprint(saved_questions[source["question_id"]]["input"]))
            if cause_input_matches(saved_questions.get(source["question_id"]) or {}, source)
            else _cause_input_fingerprint(source)
        ) for source in sources
    }
    out: dict[str, list[dict[str, Any]]] = {}
    for record in student.records:
        if not record.lost:
            continue
        envelope = envelopes.get(record.question_id) or {}
        if envelope.get("input_fingerprint") != fingerprints.get(record.question_id):
            continue
        digest = _evidence_hash(_evidence_key(_cause_evidence(student, record)))
        rows = [
            dict(row) for row in envelope.get("records") or []
            if row.get("student_id") == student.student_id and row.get("evidence_hash") == digest
        ]
        if rows:
            out[record.question_id] = rows
    return out


def collect_student_error_index(store: Any, session_ids: Iterable[int], *, db: Any,
                                data_root: Path | None = None) -> dict[int, dict[str, dict[str, set[int]]]]:
    """跨场次历史错因索引：{student_id: {"categories": {大类: {场次id}}, "patterns": {错法名: {场次id}}}}。

    历史场次同样校验当前答卷，避免更正后的旧错因继续进入报告。
    """
    index: dict[int, dict[str, dict[str, set[int]]]] = {}
    for sid in session_ids:
        students = session_error_records(db, sid, store.state_dir.parent, data_root=data_root)
        for questions in students.values():
            for row in (row for rows in questions.values() for row in rows):
                student_id = row.get("student_id")
                if not isinstance(student_id, int):
                    continue
                entry = index.setdefault(student_id, {"categories": {}, "patterns": {}})
                category = row.get("category")
                if category:
                    entry["categories"].setdefault(str(category), set()).add(int(sid))
                pattern = row.get("pattern")
                if pattern:
                    entry["patterns"].setdefault(str(pattern), set()).add(int(sid))
    return index


def session_error_records(
    db: Any, session_id: int, reports_dir: Path,
    *, data_root: Path | None = None,
) -> dict[int, dict[str, list[dict[str, Any]]]]:
    """本场全部学生的物化错因记录 {student_id: {question_id: [记录]}}。

    证据指纹必须基于整场数据构建（按班级子集装配会让全部指纹失配）；
    无状态文件或任何异常一律按无记录返回，报告与导出不受影响。
    """
    try:
        state = ClassAnalysisStateStore(Path(reports_dir)).load(int(session_id))
        if not state:
            return {}
        data = assemble_cause_data(db, int(session_id), data_root=data_root)
        sources = build_cause_inputs(data)
        out: dict[int, dict[str, list[dict[str, Any]]]] = {}
        for student in data.students:
            mapped = student_error_map(state, student, sources)
            if mapped:
                out[int(student.student_id)] = mapped
        return out
    except Exception:
        LOGGER.warning("load session %s error records failed", session_id, exc_info=True)
        return {}


def question_category_counts(
    records_by_student: dict[int, dict[str, list[dict[str, Any]]]],
    student_ids: Iterable[int] | None = None,
) -> dict[str, list[tuple[str, int]]]:
    """{question_id: [(大类, 学生数)]}：每大类按去重学生数统计，降序。"""
    from backend.error_causes import CAUSE_CATEGORIES

    allowed = {int(sid) for sid in student_ids} if student_ids is not None else None
    per_question: dict[str, dict[str, set[int]]] = {}
    for sid, by_question in (records_by_student or {}).items():
        try:
            student_id = int(sid)
        except (TypeError, ValueError):
            continue
        if allowed is not None and student_id not in allowed:
            continue
        for qid, rows in (by_question or {}).items():
            buckets = per_question.setdefault(str(qid), {})
            for row in rows or []:
                category = str(row.get("category") or "").strip()
                if category:
                    buckets.setdefault(category, set()).add(student_id)
    order = {name: index for index, name in enumerate(CAUSE_CATEGORIES)}
    return {
        qid: sorted(
            ((category, len(members)) for category, members in buckets.items()),
            key=lambda item: (-item[1], order.get(item[0], len(order)), item[0]),
        )
        for qid, buckets in per_question.items()
    }


class CausePatternEditError(Exception):
    """错法修改失败；code 供 API 层映射为 409/422。"""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def edit_cause_pattern(
    store: Any, session_id: int, *,
    question_id: str, kind: str, reason: str, new_reason: str,
    category: str | None,
    question_bank_path: Path | None = None,
    bank_context: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """教师修改错法名称/大类（可选操作，非必经步骤）。

    已关联题库时先把本场整理结果回挂，再按选项/错误答案触发位或主观题
    具体错法的改名记录找到当前条目；不能确定旧主观题错法的对应关系时
    提示核对。未关联时仅改会话状态。会话内同步更新错因分组、物化记录、
    选项诊断与填空候选库中的同名条目，并标记 ``teacher_edited``。
    """
    from backend.error_causes import CAUSE_KIND_CATEGORIES, normalize_cause_category
    from backend.error_patterns import (
        answer_pattern_map,
        bank_confirmed_triggers,
        option_analysis_entries,
        sync_session_patterns_to_bank,
    )
    from backend.session_analysis import parent_question_id
    from question_bank.services.error_pattern_service import (
        current_pattern_for_snapshot,
        preferred_active_patterns,
        rename_patterns,
    )

    question_id = str(question_id)
    kind = str(kind or "").strip()
    reason = str(reason or "").strip()
    new_reason = str(new_reason or "").strip()
    if not new_reason:
        raise CausePatternEditError(
            "cause_pattern_name_blank", "错法名称不能为空", status=422)
    state = store.load(session_id) or {}
    questions = dict(((state.get("cause_analysis") or {}).get("questions")) or {})
    saved = questions.get(question_id) or {}
    if saved.get("version") != CAUSE_ANALYSIS_VERSION or not isinstance(saved.get("result"), dict):
        raise CausePatternEditError(
            "cause_pattern_not_ready", "该题尚未完成新版错因整理，不能修改错法")
    group = next(
        (item for item in saved["result"].get("groups") or []
         if isinstance(item, dict)
         and str(item.get("reason") or "").strip() == reason
         and str(item.get("kind") or "") == kind),
        None,
    )
    if group is None:
        raise CausePatternEditError(
            "cause_pattern_group_missing", "未找到对应的错因分组，请先重新整理错因")
    if kind not in ("error", "process"):
        raise CausePatternEditError(
            "cause_pattern_category_invalid", "仅错误与过程类分组支持修改", status=422)
    category = normalize_cause_category(category)
    if category is None or category not in CAUSE_KIND_CATEGORIES[kind]:
        raise CausePatternEditError(
            "cause_pattern_category_invalid", "错误大类与分组类型不匹配", status=422)

    parent = parent_question_id(question_id)
    ctx = (bank_context or {}).get(parent) or {}
    if question_bank_path is not None and ctx.get("bank_ids"):
        sync_session_patterns_to_bank(store, session_id, question_bank_path, bank_context)
        triggers: set[tuple[str, str]] = set()
        for qid, entry in option_analysis_entries(state).items():
            if parent_question_id(str(qid)) != parent or not isinstance(entry, dict):
                continue
            analysis = entry.get("analysis")
            if not isinstance(analysis, dict):
                continue
            for letter, item in analysis.items():
                if isinstance(item, dict) and str(item.get("pattern") or "").strip() == reason:
                    triggers.add(("option", str(letter)))
        for answer, item in (answer_pattern_map(state).get(parent) or {}).items():
            if str(answer).startswith("_") or not isinstance(item, dict):
                continue
            if str(item.get("pattern") or "").strip() == reason[:40]:
                triggers.add(("wrong_answer", str(answer)))
        bank_rows = bank_confirmed_triggers(question_bank_path, ctx["bank_ids"])
        current = preferred_active_patterns(
            row for bank_id in ctx["bank_ids"] for row in bank_rows.get(bank_id) or []
        )
        targets = [
            row for row in current
            if (row["trigger_kind"], row["trigger_value"]) in triggers
        ]
        if not targets and not triggers:
            from question_bank.database.schema import connect

            step_id = str(group.get("step_id") or "").strip()
            trigger_kind = "step" if step_id else "observation"
            with connect(question_bank_path) as conn:
                for bank_id in ctx["bank_ids"]:
                    target = current_pattern_for_snapshot(
                        conn, question_id=int(bank_id), old_pattern=reason,
                        trigger_kind=trigger_kind, trigger_value=step_id,
                    )
                    if target is not None:
                        targets.append(target)
        if targets:
            from question_bank.database.schema import connect

            with connect(question_bank_path) as conn:
                for row in targets:
                    rename_patterns(
                        question_bank_path, question_ids=[row["question_id"]],
                        pattern_id=row["id"], old_pattern=row["pattern"],
                        new_pattern=new_reason, category=category, connection=conn,
                    )
        else:
            changed = rename_patterns(
                question_bank_path, question_ids=ctx["bank_ids"],
                old_pattern=reason, new_pattern=new_reason, category=category,
            )
            if not changed:
                step_id = str(group.get("step_id") or "").strip()
                trigger_kind = "step" if step_id else "observation"
                archived = any(
                    row["status"] == "merged" and row["pattern"] == reason
                    and row["trigger_kind"] == trigger_kind
                    and row["trigger_value"] == step_id
                    for rows in bank_rows.values() for row in rows
                )
                if archived:
                    raise CausePatternEditError(
                        "cause_pattern_bank_changed",
                        "题库错法已被调整，无法确定当前对应条目，请在题目详情核对后重试",
                    )

    group["reason"] = new_reason
    group["category"] = category
    group["teacher_edited"] = True
    store_fields: dict[str, Any] = {"cause_analysis": {"questions": questions}}

    records_state = dict(state.get("error_records") or {})
    envelope = records_state.get(question_id)
    if isinstance(envelope, dict) and isinstance(envelope.get("records"), list):
        for row in envelope["records"]:
            if str(row.get("pattern") or "").strip() == reason:
                row["pattern"] = new_reason
                row["category"] = category
        store_fields["error_records"] = records_state

    option_entries = dict(option_analysis_entries(state))
    touched_option = False
    for qid, entry in option_entries.items():
        if parent_question_id(str(qid)) != parent or not isinstance(entry, dict):
            continue
        analysis = entry.get("analysis")
        if not isinstance(analysis, dict):
            continue
        for item in analysis.values():
            if isinstance(item, dict) and str(item.get("pattern") or "").strip() == reason:
                item["pattern"] = new_reason
                item["category"] = category
                touched_option = True
    if touched_option:
        store_fields["option_analysis"] = option_entries

    library = {key: dict(value) for key, value in answer_pattern_map(state).items()}
    bucket = library.get(parent)
    touched_answers = False
    if isinstance(bucket, dict):
        for answer, item in bucket.items():
            if str(answer).startswith("_") or not isinstance(item, dict):
                continue
            if str(item.get("pattern") or "").strip() == reason[:40]:
                item["pattern"] = new_reason[:40]
                item["category"] = category
                touched_answers = True
    if touched_answers:
        store_fields["answer_patterns"] = library

    store.save(session_id, **store_fields)
    return {"ok": True, "question_id": question_id, "reason": new_reason,
            "category": category, "bank_linked": bool(ctx.get("bank_ids"))}


def class_question_preview(
    db: GradingRepositoryAccess, session_id: int, question_id: str, *, source_service: Any,
) -> dict[str, Any]:
    """按需读取考试绑定的原题，复用配置页图文投影及图片读取入口。"""
    from backend.config_workspace.sources import _project_config_rich_blocks
    from backend.session_analysis import (
        infer_data_root,
        load_rubric,
        parent_question_id,
    )

    repositories = as_grading_repositories(db)
    session = repositories.sessions.get_grading_session(session_id) or {}
    root = infer_data_root(repositories.db_path)
    parent = parent_question_id(question_id)
    bound_source = str(session.get("source_paper_sha256") or "").strip()
    if bound_source:
        try:
            source = source_service.load_active_record(session_id=session_id)
            if source.sha256 == bound_source:
                original = next((item for item in source.public_snapshot()["questions"]
                                 if item["question_id"] == parent), None)
                if original is not None:
                    content = original["rich_content"]
                    return {
                        "question_id": question_id,
                        "parent_question_id": parent,
                        "text": original["question_preview"],
                        "rich_content": {**content, "answer_blocks": [], "answer_block_count": 0,
                                         "available": bool(content["question_blocks"])},
                        "notice": "",
                    }
        except (OSError, ValueError, RuntimeError):
            pass

    rubric = load_rubric(session, root)
    question = next((item for item in rubric.get("questions", []) if isinstance(item, dict)
                     and parent_question_id(str(item.get("question_id") or "")) == parent), {})
    text = next((str(question[key]).strip() for key in ("question_html", "question_text", "text", "stem")
                 if question.get(key)), "")
    full_text = bool(text)
    text = text or str(question.get("stem_summary") or "")
    options = question.get("options")
    if isinstance(options, dict):
        text += "\n" + "\n".join(f"{label}. {value}" for label, value in options.items())
    elif isinstance(options, list):
        text += "\n" + "\n".join(str(value) for value in options)
    blocks = _project_config_rich_blocks(text)
    return {
        "question_id": question_id,
        "parent_question_id": parent,
        "text": text,
        "rich_content": {"available": bool(blocks), "question_block_count": len(blocks),
                         "answer_block_count": 0, "question_blocks": blocks, "answer_blocks": []},
        "notice": "原文件预览不可用，显示考试配置中的题目文字。" if full_text else
                  "未找到可预览的完整原题，以下仅为已保存的题干摘要。",
    }

# 状态文件 status 取值：ready / failed / not_configured；None 表示从未生成。
_STATE_STATUSES = frozenset({"ready", "failed", "not_configured"})

_save_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ClassAnalysisStateStore:
    """每场次一个状态 JSON 文件，原子写入，读取失败按不存在处理。"""

    def __init__(self, reports_dir: Path) -> None:
        self.state_dir = Path(reports_dir) / CLASS_ANALYSIS_STATE_DIRNAME

    def _path(self, session_id: int) -> Path:
        return self.state_dir / f"{int(session_id)}.json"

    @staticmethod
    def _default() -> dict[str, Any]:
        return {
            "version": 1,
            "auto_generate": True,
            "status": None,
            "narrative": None,
            "narrative_error": None,
            "score_revision": "",
            "generated_at": None,
            "small_sample": False,
            "class_reports": {},
            "rendition_version": "",
            "cause_analysis": None,
            "error_records": {},
            "option_analysis": {},
            "answer_patterns": {},
        }

    def load(self, session_id: int) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._path(session_id).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        state = self._default()
        state.update({key: payload[key] for key in state if key in payload})
        if state["status"] not in _STATE_STATUSES:
            state["status"] = None
        if not isinstance(state["narrative"], dict):
            state["narrative"] = None
        state["auto_generate"] = bool(state["auto_generate"])
        return state

    def save(self, session_id: int, **fields: Any) -> dict[str, Any]:
        """合并写入（保留未提及字段，如 auto_generate 开关），原子替换。"""
        with _save_lock:
            state = self.load(session_id) or self._default()
            state.update(fields)
            self.state_dir.mkdir(parents=True, exist_ok=True)
            target = self._path(session_id)
            temp = target.with_name(f".{target.stem}.tmp")
            try:
                temp.write_text(
                    json.dumps(state, ensure_ascii=False),
                    encoding="utf-8",
                )
                os.replace(temp, target)
            except OSError:
                temp.unlink(missing_ok=True)
                raise
            return state

    def set_auto_generate(self, session_id: int, enabled: bool) -> dict[str, Any]:
        return self.save(session_id, auto_generate=bool(enabled))


def submit_class_analysis_generate(
    *,
    manager: JobManager,
    session_id: int,
    revision: str,
    force: bool = False,
    kind: str = "narrative",
) -> JobRecord:
    """提交班级分析生成 job；已有 queued/running 同类 job 时直接复用返回。"""
    payload = {
        "session_id": int(session_id),
        "score_revision": str(revision),
        "force": bool(force),
        "kind": kind,
    }
    try:
        return manager.submit_unique_active(CLASS_ANALYSIS_JOB_TYPE, payload)
    except ActiveJobExistsError:
        jobs, _total = manager.list(
            session_id=int(session_id),
            job_types=(CLASS_ANALYSIS_JOB_TYPE,),
            statuses=("queued", "running"),
            limit=1,
        )
        if jobs:
            return jobs[0]
        raise


def _class_narrative(
    *,
    client: Any,
    cache: Any,
    session_id: int,
    revision: str,
    prompt: str,
    class_name: str | None = None,
) -> dict[str, Any] | None:
    """缓存命中直接返回；否则恰好调用 1 次模型，任何异常只降级不重发。"""
    from analysis_report_exporter import AnalysisNarrativeCache

    key = AnalysisNarrativeCache.cache_key(
        session_id=int(session_id),
        score_revision=revision,
        rendition_version=CLASS_ANALYSIS_RENDITION_VERSION,
        report_key=f"class:{class_name}" if class_name is not None else CLASS_ANALYSIS_REPORT_KEY,
    )
    cached = cache.load(key)
    if cached is not None:
        return cached
    try:
        narrative = client.json_from_text(
            prompt,
            extra_kwargs={"temperature": 0.3, "max_tokens": CLASS_MAX_TOKENS},
        )
    except Exception:
        return None
    if not isinstance(narrative, dict):
        return None
    cache.store(key, narrative)
    return narrative


def run_class_analysis_generate(
    context: JobContext,
    *,
    db_path: Path,
    reports_dir: Path,
    data_root: Path | None,
    llm_client_factory: Callable[[], Any] | None,
) -> dict[str, object]:
    """class_analysis_generate job：装配数据 → 生成 AI 叙述 → 写状态文件。"""
    from analysis_report_exporter import (
        AnalysisNarrativeCache,
        build_class_payload,
        build_report_prompt,
    )
    from backend.session_analysis import (
        assemble_session_analysis,
        split_session_analysis_by_class,
    )

    raw_session_id = context.payload.get("session_id")
    if raw_session_id is None:
        raise ValueError("session_id is required")
    session_id = int(raw_session_id)
    # 延迟导入：backend.report_exports 的导入链会经 jobs/__init__ 回到本模块。
    from backend.report_exports import score_revision

    store = ClassAnalysisStateStore(reports_dir)
    db = open_grading_repositories(Path(db_path))
    if context.payload.get("kind") == "causes":
        return run_cause_analysis(context, db=db, data_root=data_root, store=store,
                                  llm_client_factory=llm_client_factory)
    revision = str(context.payload.get("score_revision") or "").strip() or score_revision(
        db, session_id, include_question_bank=False
    )
    force = bool(context.payload.get("force"))

    # 幂等：同 (session_id, score_revision) 已有 ready 状态且非手动重新生成时跳过。
    existing = store.load(session_id)
    if (
        not force
        and existing is not None
        and existing.get("status") == "ready"
        and existing.get("score_revision") == revision
        and existing.get("rendition_version") == CLASS_ANALYSIS_RENDITION_VERSION
    ):
        return {
            "session_id": session_id,
            "status": "ready",
            "generated_at": existing.get("generated_at"),
            "skipped": True,
        }

    context.raise_if_cancelled()
    context.report(0.1, "class_analysis", "assembling")
    data = assemble_session_analysis(db, session_id, data_root=data_root)
    generated_at = _now_iso()
    if not data.students:
        store.save(
            session_id,
            status="failed",
            narrative=None,
            narrative_error="该场次暂无可分析的成绩数据",
            score_revision=revision,
            generated_at=generated_at,
            small_sample=data.small_sample,
            class_reports={},
            rendition_version=CLASS_ANALYSIS_RENDITION_VERSION,
        )
        return {
            "session_id": session_id,
            "status": "failed",
            "generated_at": generated_at,
        }

    client = llm_client_factory() if llm_client_factory is not None else None
    if client is None:
        # 未配置内容生成模型：不静默换模型，记 not_configured 供页面降级显示。
        store.save(
            session_id,
            status="not_configured",
            narrative=None,
            narrative_error="内容生成模型未配置",
            score_revision=revision,
            generated_at=generated_at,
            small_sample=data.small_sample,
            class_reports={},
            rendition_version=CLASS_ANALYSIS_RENDITION_VERSION,
        )
        return {
            "session_id": session_id,
            "status": "not_configured",
            "generated_at": generated_at,
        }

    context.report(0.4, "class_analysis", "generating_narrative")
    class_reports = {}
    for class_name, group in split_session_analysis_by_class(data).items():
        if not group.students:
            continue
        context.raise_if_cancelled()
        narrative = _class_narrative(
            client=client,
            cache=AnalysisNarrativeCache(Path(reports_dir) / NARRATIVE_CACHE_DIRNAME),
            session_id=session_id, revision=revision, class_name=class_name,
            prompt=build_report_prompt(CLASS_SYSTEM_PROMPT, build_class_payload(group)),
        )
        class_reports[class_name] = {
            "status": "ready" if narrative is not None else "failed",
            "narrative": narrative,
        }
    context.raise_if_cancelled()
    status = "ready" if all(item["status"] == "ready" for item in class_reports.values()) else "failed"
    store.save(
        session_id, status=status,
        narrative=next(iter(class_reports.values()))["narrative"] if len(class_reports) == 1 else None,
        narrative_error=None if status == "ready" else "AI 分析生成失败，可重新生成",
        score_revision=revision, generated_at=generated_at, small_sample=data.small_sample,
        class_reports=class_reports, rendition_version=CLASS_ANALYSIS_RENDITION_VERSION,
    )
    context.report(0.98, "class_analysis", status)
    return {
        "session_id": session_id,
        "status": status,
        "generated_at": generated_at,
    }


def maybe_auto_generate_class_analysis(
    *,
    manager: JobManager,
    db: GradingRepositoryAccess | Any,
    session_id: int,
    reports_dir: Path,
) -> JobRecord | None:
    """阅卷完成后的自动触发：开关开 + 全部答卷批完 + 已配置模型才提交 job。

    同 revision 已 ready 或已有进行中 job 时幂等跳过；未配置模型时记
    not_configured 状态（页面降级显示），不提交 job、不产生费用。
    """
    from backend.model_profiles.content_generation import (
        resolve_content_generation_settings,
    )
    from backend.report_exports import score_revision

    repositories = as_grading_repositories(db)
    store = ClassAnalysisStateStore(reports_dir)
    existing = store.load(session_id)
    if existing is not None and not bool(existing.get("auto_generate", True)):
        return None
    # 仍有未批完答卷时不算「阅卷结束」。
    if repositories.results.list_incomplete_results(int(session_id)):
        return None
    revision = score_revision(repositories, session_id, include_question_bank=False)
    if (
        existing is not None
        and existing.get("status") == "ready"
        and existing.get("score_revision") == revision
        and existing.get("rendition_version") == CLASS_ANALYSIS_RENDITION_VERSION
    ):
        return None
    if resolve_content_generation_settings() is None:
        store.save(
            session_id,
            status="not_configured",
            narrative=None,
            narrative_error="内容生成模型未配置",
            score_revision=revision,
            generated_at=_now_iso(),
            class_reports={},
            rendition_version=CLASS_ANALYSIS_RENDITION_VERSION,
        )
        return None
    return submit_class_analysis_generate(
        manager=manager,
        session_id=int(session_id),
        revision=revision,
    )


def build_class_analysis_auto_trigger(
    *,
    manager: JobManager,
    db_path: Path,
    reports_dir: Path,
) -> Callable[[int], None]:
    """供 grading_run handler 在批改完成后调用的闭包；任何失败不外抛。"""

    def trigger(session_id: int) -> None:
        maybe_auto_generate_class_analysis(
            manager=manager,
            db=open_grading_repositories(Path(db_path)),
            session_id=int(session_id),
            reports_dir=Path(reports_dir),
        )

    return trigger
