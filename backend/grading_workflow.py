"""Planning rules shared by the three P3.5 grading workflows.

The module is intentionally pure: it does not open files, touch SQLite, or call
an AI service.  API and job adapters provide the current frozen scan batch,
rubric, and teacher locks; this module turns them into one user-visible plan.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Literal


GradingWorkflowMode = Literal["full_paper", "manual", "hybrid_batch"]
_OBJECTIVE_TYPES = frozenset({"choice", "fill_blank"})


def build_grading_plan(
    *,
    session_id: int,
    mode: GradingWorkflowMode,
    scan_batch_id: str,
    upload_revision: int,
    preflight: dict[str, Any],
    rubric: dict[str, Any],
    teacher_locks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a compact, deterministic plan without starting paid work."""

    match_status = preflight_match_status(preflight)
    papers = match_status["papers"]
    scoring_groups = _rubric_scoring_groups(rubric)
    current_max_scores = rubric_scoring_item_scores(rubric)
    all_question_ids = {
        question_id
        for group in scoring_groups
        for question_id in group["detail_question_ids"]
    }
    objective_question_ids = {
        question_id
        for group in scoring_groups
        if group["question_type"] in _OBJECTIVE_TYPES
        for question_id in group["detail_question_ids"]
    }
    subjective_groups = [
        group
        for group in scoring_groups
        if group["question_type"] not in _OBJECTIVE_TYPES
    ]

    paper_student_ids = {
        int(paper["student_id"])
        for paper in papers
        if paper.get("student_id") is not None
    }
    locked_pairs = {
        (int(item["student_id"]), str(item["question_id"]).strip())
        for item in teacher_locks
        if item.get("student_id") is not None
        and str(item.get("question_id") or "").strip()
        and int(item["student_id"]) in paper_student_ids
        and str(item["question_id"]).strip() in all_question_ids
    }
    stale_locks = [
        item
        for item in teacher_locks
        if (
            str(item.get("question_id") or "").strip()
            not in current_max_scores
            or
            abs(
                float(item.get("max_score") or 0)
                - current_max_scores[
                    str(item.get("question_id") or "").strip()
                ]
            )
            > 1e-6
            or float(item.get("score_awarded") or 0)
            > current_max_scores[
                str(item.get("question_id") or "").strip()
            ]
            + 1e-6
        )
    ]

    total_score_items = len(papers) * len(all_question_ids)
    teacher_locked_items = len(locked_pairs)
    pending_items = max(0, total_score_items - teacher_locked_items)
    objective_items = sum(
        1
        for paper in papers
        for question_id in objective_question_ids
        if (int(paper["student_id"]), question_id) not in locked_pairs
    )
    subjective_items = sum(
        1
        for paper in papers
        for group in subjective_groups
        for question_id in group["detail_question_ids"]
        if (int(paper["student_id"]), question_id) not in locked_pairs
    )

    objective_students = sum(
        1
        for paper in papers
        if any(
            (int(paper["student_id"]), question_id) not in locked_pairs
            for question_id in objective_question_ids
        )
    )
    subjective_batches = 0
    singleton_subjective_batches = 0
    for group in subjective_groups:
        eligible_students = sum(
            1
            for paper in papers
            if any(
                (int(paper["student_id"]), question_id) not in locked_pairs
                for question_id in group["detail_question_ids"]
            )
        )
        sizes = balanced_subjective_group_sizes(eligible_students)
        subjective_batches += len(sizes)
        singleton_subjective_batches += sizes.count(1)

    if mode == "manual":
        requests = {
            "total": 0,
            "full_paper": 0,
            "objective_sheet": 0,
            "subjective_batches": 0,
        }
        ai_target_items = 0
        manual_target_items = pending_items
    elif mode == "full_paper":
        full_paper_requests = sum(
            1
            for paper in papers
            if any(
                (int(paper["student_id"]), question_id) not in locked_pairs
                for question_id in all_question_ids
            )
        )
        requests = {
            "total": full_paper_requests,
            "full_paper": full_paper_requests,
            "objective_sheet": 0,
            "subjective_batches": 0,
        }
        ai_target_items = pending_items
        manual_target_items = 0
    else:
        requests = {
            "total": objective_students + subjective_batches,
            "full_paper": 0,
            "objective_sheet": objective_students,
            "subjective_batches": subjective_batches,
        }
        ai_target_items = pending_items
        manual_target_items = 0

    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    for conflict in match_status["conflicts"]:
        blockers.append({"code": conflict["code"], "message": conflict["message"]})
    if not papers:
        blockers.append(
            {
                "code": "no_matched_papers",
                "message": "当前预检中没有可处理的学生答卷。",
            }
        )
    if not all_question_ids:
        blockers.append(
            {
                "code": "no_scoring_items",
                "message": "当前评分依据中没有可处理的评分题目。",
            }
        )
    if stale_locks:
        issue = {
            "code": "teacher_score_scale_changed",
            "message": (
                f"有 {len(stale_locks)} 个教师最终分所用满分与当前评分依据不同；"
                "请先重新确认这些分数。"
            ),
        }
        if mode == "manual":
            warnings.append(issue)
        else:
            blockers.append(issue)
    pending_issue_count = int(preflight.get("pending_issue_count") or 0)
    if pending_issue_count:
        warnings.append(
            {
                "code": "pending_scan_issues",
                "message": f"仍有 {pending_issue_count} 份异常答卷未处理，本轮会跳过。",
            }
        )
    if teacher_locked_items:
        warnings.append(
            {
                "code": "teacher_scores_preserved",
                "message": (
                    f"教师已确认 {teacher_locked_items} 个评分项；"
                    "AI 会照常批改所有题目作对照，但最终仍以教师确认的分数为准。"
                ),
            }
        )
    if singleton_subjective_batches and mode == "hybrid_batch":
        warnings.append(
            {
                "code": "subjective_singleton",
                "message": (
                    f"有 {singleton_subjective_batches} 个解答题批次只能包含 1 名学生；"
                    "其余批次按 2～3 人均衡组合。"
                ),
            }
        )

    return {
        "session_id": int(session_id),
        "scan_batch_id": str(scan_batch_id),
        "upload_revision": int(upload_revision),
        "decision_revision": int(preflight.get("revision") or 0),
        "mode": mode,
        "status": "blocked" if blockers else "ready",
        "counts": {
            "matched_papers": len(papers),
            "eligible_papers": len(papers),
            "skipped_papers": pending_issue_count,
            "total_score_items": total_score_items,
            "teacher_locked_items": teacher_locked_items,
            "pending_items": pending_items,
            "ai_target_items": ai_target_items,
            "manual_target_items": manual_target_items,
            "objective_students": objective_students,
            "objective_items": objective_items,
            "subjective_items": subjective_items,
        },
        "requests": requests,
        "batching": {
            "objective_requests_per_student": 1,
            "subjective_group_min": 2,
            "subjective_group_max": 3,
            "singleton_subjective_batches": singleton_subjective_batches,
        },
        "warnings": warnings,
        "blockers": blockers,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def balanced_subjective_group_sizes(student_count: int) -> list[int]:
    """Split into 2–3 where possible: 4 -> 2+2, 7 -> 3+2+2."""

    remaining = max(0, int(student_count))
    if remaining == 0:
        return []
    if remaining <= 3:
        return [remaining]

    groups = [3] * (remaining // 3)
    remainder = remaining % 3
    if remainder == 1:
        groups[-1] = 2
        groups.append(2)
    elif remainder == 2:
        groups.append(2)
    return groups


def effective_preflight_papers(
    preflight: dict[str, Any],
) -> list[dict[str, Any]]:
    decisions = {
        (str(item.get("target_type") or ""), str(item.get("target_id") or "")): item
        for item in preflight.get("decisions", [])
        if isinstance(item, dict)
    }
    papers: list[dict[str, Any]] = []

    for group in preflight.get("groups", []):
        if not isinstance(group, dict):
            continue
        target_id = str(group.get("id") or "")
        decision = decisions.get(("group", target_id))
        if decision and decision.get("action") in {"invalid", "pending"}:
            continue
        student_id = (
            decision.get("student_id")
            if decision and decision.get("action") == "match"
            else group.get("student_id")
        )
        _append_effective_paper(
            papers,
            target_type="group",
            target_id=target_id,
            student_id=student_id,
            student_name=group.get("student_name") or group.get("detected_name"),
            front_media_url=group.get("front_media_url"),
            back_media_url=group.get("back_media_url"),
            evidence=group,
            manual=bool(decision and decision.get("action") == "match"),
        )

    issues_by_id = {
        str(item.get("id") or ""): item
        for item in preflight.get("issues", [])
        if isinstance(item, dict)
    }
    for (target_type, target_id), decision in decisions.items():
        if target_type != "issue" or decision.get("action") != "match":
            continue
        issue = issues_by_id.get(target_id)
        if issue is None or not issue.get("back_media_url"):
            continue
        _append_effective_paper(
            papers,
            target_type="issue",
            target_id=target_id,
            student_id=decision.get("student_id"),
            student_name=issue.get("suggested_student_name")
            or issue.get("detected_name"),
            front_media_url=issue.get("front_media_url"),
            back_media_url=issue.get("back_media_url"),
            evidence=issue,
            manual=True,
        )
    return papers


def _append_effective_paper(
    papers: list[dict[str, Any]],
    *,
    target_type: str,
    target_id: str,
    student_id: Any,
    student_name: Any,
    front_media_url: Any,
    back_media_url: Any,
    evidence: dict[str, Any],
    manual: bool,
) -> None:
    try:
        normalized_student_id = int(student_id)
    except (TypeError, ValueError):
        return
    if normalized_student_id <= 0:
        return
    papers.append(
        {
            "target_type": target_type,
            "target_id": target_id,
            "student_id": normalized_student_id,
            "student_name": str(student_name or ""),
            "front_media_url": str(front_media_url or ""),
            "back_media_url": str(back_media_url or ""),
            "source_label": str(evidence.get("source_label") or ""),
            "detected_name": str(evidence.get("detected_name") or ""),
            "detected_class_name": str(evidence.get("detected_class_name") or ""),
            "manual": manual or evidence.get("match_method") == "manual",
            "match_method": str(evidence.get("match_method") or "exact"),
            "match_score": float(evidence.get("match_score", 1) or 0),
        }
    )


def _identity_text(value: Any) -> str:
    return re.sub(r"[\s()（）·.]+", "", str(value or "")).casefold()


def preflight_match_status(
    preflight: dict[str, Any], students: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Reconcile physical answer groups and stable student IDs without deduping."""
    papers = effective_preflight_papers(preflight)
    roster = {int(s["id"]): s for s in students or [] if s.get("id") is not None}
    names: dict[str, set[int]] = {}
    for student_id, student in roster.items():
        names.setdefault(_identity_text(student.get("name")), set()).add(student_id)
    conflicts: list[dict[str, Any]] = []
    by_student: dict[int, list[dict[str, Any]]] = {}
    blocked: set[tuple[str, str]] = set()

    def add(code: str, message: str, affected: list[dict[str, Any]], student_id: int):
        targets = [{"target_type": p["target_type"], "target_id": p["target_id"]} for p in affected]
        blocked.update((p["target_type"], p["target_id"]) for p in affected)
        conflicts.append({"code": code, "message": message, "student_id": student_id, "targets": targets})

    for paper in papers:
        student_id = paper["student_id"]
        by_student.setdefault(student_id, []).append(paper)
        if students is None:
            continue
        student = roster.get(student_id)
        if student is None:
            add("scan_student_unavailable", "所选学生已不在名单中，请重新选择。", [paper], student_id)
            continue
        detected = _identity_text(paper["detected_name"] or paper["student_name"])
        paper["student_name"] = str(student.get("name") or "")
        if paper["manual"]:
            # Teachers confirm stable IDs; renames or OCR disagreements must not rebind them.
            continue
        if detected and len(names.get(detected, set())) > 1:
            add("scan_name_ambiguous", "名单中有重名学生，请核对学号和班级后确认归属。", [paper], student_id)
        elif (detected and detected != _identity_text(student.get("name"))) or paper["match_score"] < 1 or paper["match_method"] in {"fuzzy", "reduced_fuzzy"}:
            add("scan_name_mismatch", "识别姓名与名单姓名不一致，请看卷后确认归属。", [paper], student_id)
        detected_class = _identity_text(paper["detected_class_name"])
        if detected_class and detected_class != _identity_text(student.get("class_name")):
            add("scan_class_mismatch", "卷面班级与所选学生的班级不一致，请看卷后确认归属。", [paper], student_id)

    for student_id, assigned in by_student.items():
        if len(assigned) > 1:
            student = roster.get(student_id, {})
            label = " · ".join(str(student.get(k) or "") for k in ("name", "student_code", "class_name")).strip(" ·")
            add("scan_student_multiple_papers", f"{label or '同一学生'}对应 {len(assigned)} 份答卷，请更正归属或将重复扫描标为无效。", assigned, student_id)

    # Adapters without roster access still honour the workspace's current checks.
    if students is None:
        for conflict in preflight.get("match_conflicts", []):
            if any(c["code"] == conflict["code"] and c["student_id"] == conflict["student_id"] for c in conflicts):
                continue
            conflicts.append(conflict)
            blocked.update((t["target_type"], t["target_id"]) for t in conflict["targets"])

    all_targets = {
        (kind, str(item.get("id") or ""))
        for kind, key in (("group", "groups"), ("issue", "issues"))
        for item in preflight.get(key, []) if isinstance(item, dict)
    }
    invalid = {
        (str(d.get("target_type")), str(d.get("target_id")))
        for d in preflight.get("decisions", []) if d.get("action") == "invalid"
    } & all_targets
    valid = [p for p in papers if (p["target_type"], p["target_id"]) not in blocked]
    valid_targets = {(p["target_type"], p["target_id"]) for p in valid}
    pending = len(all_targets - invalid - valid_targets)
    return {
        "papers": valid,
        "conflicts": conflicts,
        "pending_issue_count": pending,
        "summary": {
            "scanned_papers": len(all_targets),
            "matched_papers": len(papers),
            "unique_students": len(by_student),
            "ready_to_grade": len(valid),
            "invalid_papers": len(invalid),
            "unresolved_papers": pending,
            "conflicting_papers": len(blocked),
        },
        "absent_students": [s for sid, s in roster.items() if sid not in {p["student_id"] for p in valid}],
    }


def _rubric_scoring_groups(rubric: dict[str, Any]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    questions = rubric.get("questions") if isinstance(rubric, dict) else []
    for question in questions if isinstance(questions, list) else []:
        if not isinstance(question, dict):
            continue
        question_id = str(question.get("question_id") or "").strip()
        if not question_id:
            continue
        detail_ids = [
            str(part.get("part_id") or "").strip()
            for part in question.get("parts", [])
            if isinstance(part, dict) and str(part.get("part_id") or "").strip()
        ]
        if not detail_ids:
            detail_ids = [question_id]
        unique_detail_ids: list[str] = []
        for value in detail_ids:
            if value in seen_ids:
                continue
            seen_ids.add(value)
            unique_detail_ids.append(value)
        if not unique_detail_ids:
            continue
        groups.append(
            {
                "question_id": question_id,
                "question_type": str(
                    question.get("question_type") or "comprehensive"
                ).strip(),
                "detail_question_ids": unique_detail_ids,
            }
        )
    return groups


def rubric_scoring_item_scores(
    rubric: dict[str, Any],
) -> dict[str, float]:
    scores: dict[str, float] = {}
    questions = rubric.get("questions") if isinstance(rubric, dict) else []
    for question in questions if isinstance(questions, list) else []:
        if not isinstance(question, dict):
            continue
        parts = [
            part
            for part in question.get("parts", [])
            if isinstance(part, dict)
            and str(part.get("part_id") or "").strip()
        ]
        if parts:
            for part in parts:
                try:
                    scores[str(part["part_id"]).strip()] = float(
                        part.get("part_score", 0)
                    )
                except (TypeError, ValueError):
                    continue
            continue
        question_id = str(question.get("question_id") or "").strip()
        if not question_id:
            continue
        try:
            scores[question_id] = float(question.get("max_score", 0))
        except (TypeError, ValueError):
            continue
    return scores


__all__ = [
    "GradingWorkflowMode",
    "balanced_subjective_group_sizes",
    "build_grading_plan",
    "effective_preflight_papers",
    "preflight_match_status",
    "rubric_scoring_item_scores",
]
