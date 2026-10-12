from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from types import SimpleNamespace
from pathlib import Path
from typing import Any

from backend.config_generation.score_policy import OBJECTIVE_TYPES
from integration.diagnosis_profile_service import _step_point_observations, _teacher_step_records
from integration.question_tag_projection_service import QuestionTagProjectionService
from question_bank.current_knowledge import CurrentKnowledgeResolver, CurrentKnowledgeUnavailable
from question_bank.services.question_read_service import _open_direct_read_connection
from question_bank.solution_evidence.evidence_snapshot import load_snapshot

STEP_STATE_LABELS = {
    "independent": "独立失分", "carried": "受前一步影响", "met": "已完成",
    "uncertain": "AI 判不准", "blank": "整题未作答", "unknown": "无逐步记录",
}
STEP_SOURCE_LABELS = {
    "teacher": "教师逐步确认", "ai": "AI 步骤判断",
    "teacher_confirmed_ai": "教师确认总分，步骤为 AI 判断",
}
# 3_3_206 是 3_3_201 在 2026-10 词表中拆出的子键；冻结快照里只挂 3_3_201
# 而题库当前挂 3_3_206 时，视为同一主关键步骤。
_SPLIT_CHILD_OF = {
    "sk_bnu24_math_g8_upper_3_3_206": "sk_bnu24_math_g8_upper_3_3_201",
}


def _release_skill_key_rules(bank) -> tuple[set[str], dict[str, str], str]:
    """Active sk keys plus the replacement chain of the current release."""
    try:
        row = bank.execute(
            "SELECT release_id, payload_json FROM knowledge_graph_releases "
            "WHERE status = 'active'"
        ).fetchone()
    except Exception:
        return set(), {}, ""
    if row is None:
        return set(), {}, ""
    payload = json.loads(row["payload_json"])
    active = {
        str(node["stable_key"])
        for node in payload.get("core_nodes") or ()
        if node.get("status") == "active"
    }
    replacements = {
        str(item["retired_key"]): str(item["replacement_key"])
        for item in payload.get("replacements") or ()
    }
    return active, replacements, str(row["release_id"] or "")


def _resolve_snapshot_skill_key(
    key: str, active: set[str], replacements: dict[str, str]
) -> str | None:
    """Follow replacements until an active sk node; None when it lands on kp/nowhere."""
    seen = set()
    while key and key not in seen:
        if key in active and key.startswith("sk_"):
            return key
        seen.add(key)
        key = replacements.get(key, "")
        if key.startswith("kp_"):
            return None
        if not key:
            return None
    return None


@dataclass
class SessionStepDiagnosis:
    summary: dict[str, Any]
    questions: dict[str, list[dict[str, Any]]]
    students: dict[int, dict[str, dict[str, Any]]]


def build_session_step_diagnosis(
    db, session_id: int, *, student_ids: Sequence[int],
    question_bank_path: Path | None = None, include_patterns: bool = False,
) -> SessionStepDiagnosis:
    """Current exam display only. No mastery updates, model calls or saved state."""
    from backend.session_analysis import question_bank_db_path

    result = SessionStepDiagnosis(
        {"students": 0, "confirmed_ai_rows": 0, "excluded_teacher_total_only": 0, "hotspots": []},
        {}, {},
    )
    if not student_ids:
        return result
    bank_source = question_bank_path or question_bank_db_path(Path(db.db_path))
    if bank_source is None:
        return result
    bank_path = Path(bank_source).resolve()
    if not bank_path.is_file():
        return result
    rubric = db.results._load_session_rubric(session_id) or {}
    session = db.sessions.get_grading_session(session_id) or {}
    key_step_scope = session.get("curriculum_volume_id") in {None, "", "bnu24-math-g8-upper"}
    root = bank_path.parent.parent
    snapshot = load_snapshot(root / "config" / "uploaded", session_id) or {}
    with closing(_open_direct_read_connection(bank_path)) as bank:
        projection = QuestionTagProjectionService(
            bank_path, external_connection=bank, data_root=root,
        ).project_session(grading_session_id=session_id, rubric=rubric)
        try:
            knowledge = CurrentKnowledgeResolver.from_connection(bank)
        except CurrentKnowledgeUnavailable:
            knowledge = None
        goals = {}
        for question in rubric.get("questions") or []:
            for part in question.get("parts") or [{**question, "part_id": question["question_id"]}]:
                for step in part.get("steps") or []:
                    goals[(str(part.get("part_id") or question["question_id"]), str(step["step_id"]))] = step.get("core_goal") or ""
        question_types = {str(question["question_id"]): str(question.get("question_type") or "").strip()
                          for question in rubric.get("questions") or []}
        items = {item.item_ref: item for item in projection.items
                 if question_types.get(item.parent_ref) not in OBJECTIVE_TYPES}
        active_sk, replacements, active_release_id = (
            _release_skill_key_rules(bank) if key_step_scope else (set(), {}, "")
        )

        def _current_point_sk(version_id: str, pid: str) -> set[str]:
            if not version_id or not active_release_id:
                return set()
            return {
                str(row[0]) for row in bank.execute(
                    "SELECT stable_key FROM evidence_point_knowledge_links "
                    "WHERE evidence_version_id = ? AND evidence_point_id = ? "
                    "AND graph_release_id = ? AND role = 'direct' "
                    "AND resolution_status = 'resolved' AND stable_key LIKE 'sk_%'",
                    (version_id, pid, active_release_id))
            }

        def _point_keys(frozen: dict[str, Any], pid: str) -> list[str]:
            raw = []
            for link in (frozen.get("links") or {}).get(str(pid), ()):
                key = str(link.get("stable_key") or link.get("term_id") or "")
                if (link.get("role") == "direct"
                        and link.get("resolution_status") == "resolved"
                        and key.startswith("sk_") and key not in raw):
                    raw.append(key)
            if not active_sk:
                return raw
            resolved: list[str] = []
            for key in raw:
                mapped = _resolve_snapshot_skill_key(key, active_sk, replacements)
                if mapped and mapped not in resolved:
                    resolved.append(mapped)
            if len(resolved) >= 2:
                version_id = str(frozen.get("source_evidence_version_id") or "")
                current = _current_point_sk(version_id, str(pid))
                picked = [key for key in resolved if key in current]
                picked += [
                    c for c in current
                    if _SPLIT_CHILD_OF.get(c) in resolved and c not in picked
                ]
                if len(picked) == 1:
                    return picked
            return resolved

        info = {}
        for item in items.values():
            frozen = (snapshot.get("questions") or {}).get(item.parent_ref) or {}
            for step in item.steps:
                sid = str(step["step_id"])
                keys = []
                for pid in step.get("evidence_point_ids") or []:
                    for key in _point_keys(frozen, str(pid)):
                        if key not in keys:
                            keys.append(key)
                if not key_step_scope:
                    keys = []
                names = []
                for key in keys:
                    node = knowledge.node(key) if knowledge else None
                    name = node.display_name if node else key
                    names.append({"key": key, "name": str(name).split("｜")[-1].removeprefix("技能·")})
                point_text = "；".join(
                    str(point.get("target") or "")
                    for part in (frozen.get("evidence") or {}).get("parts", ())
                    for point in part.get("evidence_points", ())
                    if point.get("evidence_point_id") in step.get("evidence_point_ids", ()))
                rubric_goal = goals.get((str(step.get("part_id")), sid), "")
                entry = {
                    "step_id": sid, "part_id": str(step.get("part_id") or ""),
                    "goal": (rubric_goal or point_text) if key_step_scope else (point_text or rubric_goal),
                    "score": step.get("step_score") or 0, "key_steps": names,
                    "counts": Counter(),
                }
                result.questions.setdefault(item.item_ref, []).append(entry)
                info[(item.item_ref, sid)] = entry
        rows = db.results.get_active_assessment_evidence(student_ids=student_ids, session_ids=[session_id])
        with db._connect() as grading:
            ai_scores = {int(row[0]): row[1] for row in grading.execute(
                "SELECT d.id, d.ai_score_awarded FROM session_details d "
                "JOIN session_results r ON r.id=d.result_id WHERE r.session_id=?", (session_id,),
            )}
        buckets = defaultdict(lambda: defaultdict(set))
        observed_students = set()
        for row in rows:
            qid, student_id = str(row["question_id"]), int(row["student_id"])
            item = items.get(qid)
            if item is None or not item.steps:
                continue
            state = row.get("assessment_state") or {}
            teacher = _teacher_step_records(item, row)
            source = "teacher" if teacher is not None else "ai"
            records = teacher if teacher is not None else state.get("step_assessments")
            student_items = result.students.setdefault(student_id, {})
            if teacher is None and row.get("teacher_final_revision") is not None:
                ai = ai_scores.get(int(row["detail_id"]))
                if ai is None or abs(float(ai) - float(row["score_awarded"])) > 1e-9:
                    result.summary["excluded_teacher_total_only"] += 1
                    student_items[qid] = {"availability": "teacher_total_only", "source": None, "steps": []}
                    continue
                source = "teacher_confirmed_ai"
            if not records:
                student_items[qid] = {"availability": "no_records", "source": None, "steps": []}
                continue
            if source == "teacher_confirmed_ai":
                result.summary["confirmed_ai_rows"] += 1
            observed_students.add(student_id)
            states = step_states(item, records)
            if teacher is None and state.get("answer_is_blank_or_no_valid_work"):
                states = {str(step["step_id"]): "blank" for step in item.steps}
            by_step = {str(record.get("step_id")): record for record in records if isinstance(record, Mapping)}
            entries = []
            for sid, status in states.items():
                step_info = info[(qid, sid)]
                step_info["counts"][status] += 1
                if source == "teacher_confirmed_ai" and status == "independent":
                    step_info["counts"]["independent_confirmed_ai"] += 1
                entries.append({"step_id": sid, "goal": step_info["goal"], "state": status,
                    "key_steps": step_info["key_steps"],
                    "reason": "" if status == "blank" else _step_reason(by_step.get(sid) or {}, source)})
                for target in step_info["key_steps"]:
                    bucket = buckets[target["key"]]
                    bucket[status].add(student_id)
                    if status in {"met", "independent"}:
                        bucket["observed"].add(student_id)
                    if source == "teacher_confirmed_ai" and status == "independent":
                        bucket["confirmed_ai"].add(student_id)
            student_items[qid] = {"availability": "available", "scope": "key_steps" if key_step_scope else "point_text", "source": source, "steps": entries}
        result.summary["students"] = len(observed_students)
        for key, bucket in buckets.items():
            if not bucket["observed"]:
                continue
            related = [(qid, step) for qid, steps in result.questions.items() for step in steps
                       if any(target["key"] == key for target in step["key_steps"])]
            name = next(target["name"] for _qid, step in related for target in step["key_steps"] if target["key"] == key)
            result.summary["hotspots"].append({
                "key": key, "name": name, "independent": len(bucket["independent"]),
                "observed": len(bucket["observed"]),
                "carried": len(bucket["carried"] - bucket["independent"]),
                "uncertain": len(bucket["uncertain"] - bucket["independent"]),
                "blank": len(bucket["blank"] - bucket["observed"]),
                "confirmed_ai": len(bucket["confirmed_ai"]),
                "questions": sorted({qid for qid, _step in related}),
                "causes_status": "missing", "causes": [], "patterns": [],
            })
        result.summary["hotspots"].sort(key=lambda h: (-h["independent"] / h["observed"], -h["independent"], h["key"]))
        if include_patterns:
            _add_patterns(result.summary["hotspots"], bank, bank_path, knowledge)
    if not key_step_scope:
        result.questions = {}
    return result


def step_states(projected, records) -> dict[str, str]:
    # The observation helper filters unlinked points for mastery. Display still
    # needs their dependency status, so give only that calculation a local link.
    links = {pid: tuple(projected.point_links.get(pid) or ()) for pid in projected.evidence_points}
    for pid, values in links.items():
        if not any(link.get("role") == "direct" and link.get("resolution_status") == "resolved"
                   and link.get("stable_key") and float(link.get("weight") or 0) > 0 for link in values):
            links[pid] = ({"role": "direct", "resolution_status": "resolved", "stable_key": pid, "weight": 1},)
    view = SimpleNamespace(steps=projected.steps, evidence_points=projected.evidence_points, point_links=links)
    observed = _step_point_observations(view, records) or []
    achieved = {entry["point_id"]: entry["achieved"] for entry in observed}
    states = {}
    for step in projected.steps:
        sid = str(step["step_id"])
        matches = [record for record in records if isinstance(record, Mapping)
                   and str(record.get("step_id") or "") == sid
                   and (not record.get("part_id") or not step.get("part_id") or record["part_id"] == step["part_id"])]
        if len(matches) != 1:
            states[sid] = "unknown"
            continue
        raw = str(matches[0].get("achievement") or "").lower()
        values = [achieved[pid] for pid in step.get("evidence_point_ids") or () if pid in achieved]
        if raw == "uncertain":
            states[sid] = "uncertain"
        elif values and min(values) < 1:
            states[sid] = "independent"
        elif values:
            states[sid] = "met"
        elif raw in {"none", "partial"}:
            states[sid] = "carried"
        else:
            states[sid] = "unknown"
    return states


def _add_patterns(hotspots, bank, bank_path, knowledge) -> None:
    from question_bank.services.error_pattern_service import pattern_skill_index

    if not hotspots or not bank.execute("SELECT 1 FROM sqlite_master WHERE name='question_error_patterns'").fetchone():
        return
    by_key = {hotspot["key"]: hotspot for hotspot in hotspots}
    by_question = defaultdict(list)
    for row in bank.execute("SELECT * FROM question_error_patterns WHERE trigger_kind='step' AND status='confirmed'"):
        by_question[int(row["question_id"])].append(dict(row))
    for qid, rows in by_question.items():
        index = pattern_skill_index(bank, bank_path, qid, rows, knowledge=knowledge)
        for row in rows:
            try:
                occurrences = json.loads(row.get("occurrences_json") or "[]")
            except (ValueError, TypeError):
                occurrences = []
            actual = len(occurrences) if isinstance(occurrences, list) else 0
            for key in index.get(int(row["id"]), {}).get("skill_keys", ()):
                if key in by_key:
                    by_key[key]["patterns"].append({"pattern": str(row.get("pattern") or ""),
                        "actual": actual, "question_id": qid})
    for hotspot in hotspots:
        hotspot["patterns"].sort(key=lambda pattern: (-pattern["actual"], pattern["pattern"]))


def apply_step_diagnosis(page_data: dict, diagnosis: SessionStepDiagnosis) -> None:
    """Merge saved cause members with current independent failures, by student."""
    page_data["step_diagnosis"] = diagnosis.summary
    questions = {question["question_id"]: question for question in page_data.get("questions") or []}
    for qid, question in questions.items():
        question["key_steps"] = diagnosis.questions.get(qid, [])
    for hotspot in diagnosis.summary["hotspots"]:
        merged = {}
        mapped, unclassified = False, False
        for qid in hotspot["questions"]:
            question = questions.get(qid) or {}
            for cause in question.get("causes") or []:
                if cause.get("category") == "未作答" or cause.get("kind") in {"response_state", "carry_forward", "review"}:
                    continue
                ids = cause.get("step_ids") or ([cause["step_id"]] if cause.get("step_id") else [])
                if not ids:
                    unclassified = True
                    continue
                mapped = True
                members = {int(sid) for evidence in cause.get("evidence") or [] for sid in evidence.get("student_ids") or []}
                eligible, examples = set(), set()
                for student_id in members:
                    student_item = diagnosis.students.get(student_id, {}).get(qid) or {}
                    for step in student_item.get("steps") or []:
                        if step["state"] == "independent" and step["step_id"] in ids and any(target["key"] == hotspot["key"] for target in step["key_steps"]):
                            eligible.add(student_id)
                            if step["reason"]:
                                examples.add(step["reason"])
                if not eligible:
                    continue
                reason = str(cause.get("reason") or "")
                entry = merged.setdefault(reason, {"reason": reason, "category": str(cause.get("category") or ""),
                    "members": set(), "questions": set(), "examples": set()})
                entry["members"].update(eligible)
                entry["questions"].add(qid)
                entry["examples"].update(examples)
        hotspot["causes_status"] = "partial" if mapped and unclassified else "ready" if mapped else "unclassified" if unclassified else "missing"
        hotspot["causes"] = sorted(({"reason": entry["reason"], "category": entry["category"],
            "count": len(entry["members"]), "questions": sorted(entry["questions"]),
            "examples": sorted(entry["examples"])[:3]} for entry in merged.values()),
            key=lambda entry: (-entry["count"], entry["reason"]))


def _step_reason(record, source: str) -> str:
    if source == "teacher" and record.get("deduction_source") == "teacher":
        return str(record.get("teacher_note") or "教师逐步扣分，未记录具体原因").strip()
    return str(record.get("missing_or_error") or record.get("reason") or "").strip()
