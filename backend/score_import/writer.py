"""智学网成绩导入确认写入：评分结构变更、冻结批次上下文与单事务成绩写入。

配置变更走与赋分编辑器一致的发布链路（快照校验 + 覆盖率校验 + 原子发布 +
空闲时绑定），成绩写入复用 ``save_session_result`` 与教师最终分锁。
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.config_workspace.locks import session_config_lock
from backend.config_workspace.publish import (
    ConfigRevisionConflict,
    load_editor_config,
    publish_generated_config,
    remove_published_config,
)
from backend.domain_models import (
    GradingResult,
    QuestionGradingDetail,
    TEACHER_LOCKED_ERROR_SUMMARY,
)
from backend.repositories.base import SQLiteConnectionFactory
from backend.repositories.papers import PaperRepository
from backend.repositories.results import ResultRepository
from backend.repositories.review import ReviewRepository
from backend.repositories.sessions import SessionRepository
from backend.scan_grading.grading_completeness import audit_grading_details
from backend.score_import.context import (
    _atomic_write_json,
    receipt_path,
    session_template_dir,
)

_IMPORT_KIND_OBJECTIVE = {"choice", "single_choice", "multi_choice", "multiple_choice",
                          "fill_blank", "judgement", "true_false", "direct_answer"}


class ScoreImportWriteError(RuntimeError):
    """写入阶段失败；消息面向教师。"""


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def apply_import_structure(
    payload: dict[str, Any],
    *,
    collapses: list[str],
    item_max: dict[str, float],
    combined_groups: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply question collapse + item full-mark changes, preserving evidence ids."""
    candidate = copy.deepcopy(payload)
    rubric_questions = {
        str(q.get("question_id") or ""): q
        for q in (candidate["rubric"].get("questions") or [])
        if isinstance(q, dict)
    }
    answer_questions = {
        str(q.get("question_id") or ""): q
        for q in (candidate["answer_key"].get("questions") or [])
        if isinstance(q, dict)
    }
    for question_id in collapses:
        question = rubric_questions.get(question_id)
        if question is None:
            continue
        parts = [p for p in question.get("parts") or [] if isinstance(p, dict)]
        if len(parts) <= 1:
            continue
        steps: list[dict[str, Any]] = []
        for part in parts:
            for step in part.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                step = dict(step)
                step["step_id"] = f"S{len(steps) + 1}"
                steps.append(step)
        merged_score = float(item_max.get(question_id) or sum(
            _number(part.get("part_score")) for part in parts
        ))
        merged = {
            "part_id": question_id,
            "part_score": merged_score,
            "response_mode": str(parts[0].get("response_mode") or "process_required"),
            "require_final_answer": any(
                bool(part.get("require_final_answer")) for part in parts
            ),
            "allow_alternative_methods": bool(parts[0].get("allow_alternative_methods", True)),
            "deduction_policy": [
                item for part in parts for item in (part.get("deduction_policy") or [])
            ],
            "proof_obligations": [
                item for part in parts for item in (part.get("proof_obligations") or [])
            ],
            "visual_requirements": copy.deepcopy(parts[0].get("visual_requirements") or []),
            "presentation_rules": copy.deepcopy(parts[0].get("presentation_rules") or []),
            "steps": steps,
            "answer_only_max_score": question.get("answer_only_max_score"),
            "evidence_part_id": question_id,
        }
        question["parts"] = [merged]
        question["max_score"] = merged_score
        answer_question = answer_questions.get(question_id)
        if answer_question is not None:
            answer_parts = [
                p for p in answer_question.get("parts") or [] if isinstance(p, dict)
            ]
            answer_question["parts"] = [
                {
                    "part_id": question_id,
                    "answer": "；".join(
                        str(part.get("answer") or "") for part in answer_parts
                    ),
                    "canonical_answer": str(answer_question.get("canonical_answer") or ""),
                    "accepted_forms": [
                        item for part in answer_parts for item in (part.get("accepted_forms") or [])
                    ],
                    "analysis": "；".join(
                        str(part.get("analysis") or "") for part in answer_parts
                    ),
                    "step_milestones": [
                        item for part in answer_parts for item in (part.get("step_milestones") or [])
                    ],
                    "proof_obligations": [
                        item for part in answer_parts for item in (part.get("proof_obligations") or [])
                    ],
                    "visual_requirements": [
                        item for part in answer_parts for item in (part.get("visual_requirements") or [])
                    ],
                    "answer_values": [
                        item for part in answer_parts for item in (part.get("answer_values") or [])
                    ],
                    "part_score": merged_score,
                }
            ]
            answer_question["max_score"] = merged_score

    for question in rubric_questions.values():
        if str(question.get("question_id")) in collapses:
            continue
        parts = [p for p in question.get("parts") or [] if isinstance(p, dict)]
        changed = False
        for part in parts:
            part_id = str(part.get("part_id") or "")
            if part_id not in item_max:
                continue
            target = float(item_max[part_id])
            steps = [s for s in part.get("steps") or [] if isinstance(s, dict)]
            current = _number(part.get("part_score") or part.get("max_score"))
            if abs(target - current) <= 1e-6:
                continue
            if steps:
                _scale_steps(steps, target)
                part["part_score"] = sum(_number(s.get("step_score")) for s in steps)
            else:
                part["part_score"] = target
            if "max_score" in part:
                part["max_score"] = part["part_score"]
            changed = True
            answer_question = answer_questions.get(str(question.get("question_id")))
            if answer_question is not None:
                for answer_part in answer_question.get("parts") or []:
                    if isinstance(answer_part, dict) and str(answer_part.get("part_id")) == part_id:
                        answer_part["part_score"] = part["part_score"]
        if changed:
            question["max_score"] = sum(_number(p.get("part_score")) for p in parts)
            answer_question = answer_questions.get(str(question.get("question_id")))
            if answer_question is not None:
                answer_question["max_score"] = question["max_score"]
    # question-level item_max (questions without parts)
    for question in rubric_questions.values():
        question_id = str(question.get("question_id"))
        if question_id in collapses or (question.get("parts") or []):
            continue
        if question_id in item_max:
            question["max_score"] = float(item_max[question_id])
    candidate["rubric"]["total_score"] = sum(
        _number(q.get("max_score")) for q in rubric_questions.values()
    )
    if combined_groups:
        # 合并给分组是评分结构的一部分；成员仍是评分项，组总分按
        # item_max 之后的成员满分写入。
        member_max = {
            str(question.get("question_id") or ""): _number(
                question.get("max_score")
            )
            for question in rubric_questions.values()
        }
        candidate["rubric"]["combined_scoring_groups"] = [
            {
                "group_id": str(group["group_id"]),
                "label": str(group.get("label") or group["group_id"]),
                "question_ids": [
                    str(qid) for qid in (group.get("question_ids") or [])
                ],
                "max_score": float(sum(
                    member_max.get(str(qid), _number(None))
                    for qid in (group.get("question_ids") or [])
                )),
            }
            for group in combined_groups
        ]
    return candidate


def _scale_steps(steps: list[dict[str, Any]], target: float) -> None:
    current = sum(_number(step.get("step_score")) for step in steps)
    if current <= 0:
        steps[0]["step_score"] = target
        return
    factor = target / current
    assigned = 0.0
    for index, step in enumerate(steps):
        if index == len(steps) - 1:
            step["step_score"] = round(target - assigned, 2)
        else:
            value = round(_number(step.get("step_score")) * factor * 2) / 2
            step["step_score"] = value
            assigned += value


def save_import_config(
    db: Any,
    upload_config_dir: Path,
    session_id: int,
    *,
    collapses: list[str],
    item_max: dict[str, float],
    combined_groups: list[dict[str, Any]] | None = None,
    job_store: Any = None,
) -> bool:
    """Publish the import-adjusted config through the editor publication path.

    Same guarantees as ``_save_editor_config_locked``: session config lock,
    frozen-snapshot annotation, evidence coverage validation, atomic publish
    and an idle-only session binding. Failure leaves the old config intact.
    """
    if not collapses and not item_max and not combined_groups:
        return False
    with session_config_lock(Path(upload_config_dir), int(session_id)):
        if job_store is not None:
            job_store.assert_config_session_idle(session_id)
        current = load_editor_config(db, session_id)
        if not current.configured:
            raise ScoreImportWriteError("评分依据尚未配置，无法写入导入结构。")
        from question_bank.solution_evidence.evidence_snapshot import (
            annotate_rubric_payload,
            load_snapshot,
            validate_rubric_evidence_coverage,
        )

        snapshot = load_snapshot(Path(upload_config_dir), int(session_id))
        base_payload = copy.deepcopy(current.payload)
        if snapshot is not None:
            annotate_rubric_payload(
                base_payload["rubric"],
                snapshot,
                int(session_id),
                answer_key=base_payload.get("answer_key"),
            )
        candidate = apply_import_structure(
            base_payload,
            collapses=list(collapses),
            item_max=dict(item_max),
            combined_groups=list(combined_groups or []),
        )
        if snapshot is not None:
            violations = validate_rubric_evidence_coverage(
                candidate.get("rubric", {}),
                snapshot,
            )
            # 合并小问的题目主动放弃逐小问判定点（整题给分），快照覆盖检查
            # 对这类题目必然报告 missing part——掌握度按整题粒度照常投影。
            # 本次发布未改动的题目（含此前已合并的整题）同样放行：其违规
            # 来自已接受的现行配置，不是本次发布引入的破坏。
            # 其余题目的违规仍然阻止导入。
            exempt_refs = {str(q) for q in collapses}
            candidate_questions = {
                str(
                    q.get("question_id") or q.get("id") or q.get("number") or ""
                ): q
                for q in (candidate.get("rubric", {}).get("questions") or [])
                if isinstance(q, dict)
            }
            for question in base_payload["rubric"].get("questions") or ():
                if not isinstance(question, dict):
                    continue
                ref = str(
                    question.get("question_id") or question.get("id")
                    or question.get("number") or ""
                )
                if ref and candidate_questions.get(ref) == question:
                    exempt_refs.add(ref)
            violations = [
                v for v in violations
                if str(v).split("/", 1)[0].split(":", 1)[0] not in exempt_refs
            ]
            if violations:
                raise ScoreImportWriteError(
                    "合并小问后评分步骤与题库判定点的关联不完整："
                    + "；".join(str(v) for v in violations[:3])
                )
        from backend.config_workspace.editor import validate_config_editor_candidate

        publication = None
        try:
            validate_config_editor_candidate(candidate)
            publication = publish_generated_config(
                Path(upload_config_dir),
                candidate,
                token=uuid.uuid4().hex,
                preserve_scores=True,
            )
            before_bind = load_editor_config(db, session_id)
            if (
                str(before_bind.session.get("rubric_path") or "")
                != str(current.session.get("rubric_path") or "")
                or str(before_bind.session.get("answer_key_path") or "")
                != str(current.session.get("answer_key_path") or "")
            ):
                raise ConfigRevisionConflict("config paths changed")
            if job_store is not None:
                bound = job_store.update_session_config_if_idle(
                    int(session_id),
                    rubric_path=str(publication.rubric_path),
                    answer_key_path=str(publication.answer_key_path),
                    expected_rubric_path=str(current.session.get("rubric_path") or ""),
                    expected_answer_key_path=str(current.session.get("answer_key_path") or ""),
                    preserve_question_bank_sync=True,
                )
            else:
                bound = db.sessions.publish_grading_session_config(
                    int(session_id),
                    rubric_path=str(publication.rubric_path),
                    answer_key_path=str(publication.answer_key_path),
                    expected_rubric_path=str(current.session.get("rubric_path") or ""),
                    expected_answer_key_path=str(current.session.get("answer_key_path") or ""),
                    preserve_question_bank_sync=True,
                )
            if not bound:
                raise ConfigRevisionConflict("config binding changed")
        except BaseException:
            if publication is not None:
                remove_published_config(Path(upload_config_dir), publication.created_paths)
            raise
    return True


def write_frozen_scan_context(
    *,
    data_root: Path,
    templates_dir: Path,
    session_id: int,
    students: list[dict[str, Any]],
    absent: list[dict[str, Any]],
) -> str:
    """Create the frozen batch context imported scores attach to (V1b shape)."""
    session_dir = session_template_dir(templates_dir, session_id)
    for stale in (
        "scan_upload_batch.json",
        "scan_upload_replacement.json",
        "scan_upload_replacement_commit.json",
        "scan_batch_transition.json",
        "scan_analysis_latest.json",
        "scan_decisions_state.json",
        "scan_manual_decisions_latest.json",
    ):
        try:
            (session_dir / stale).unlink(missing_ok=True)
        except OSError:
            pass
    batch_id = uuid.uuid4().hex
    now = datetime.now().isoformat(timespec="seconds")
    manifest = {
        "batch_id": batch_id,
        "revision": 0,
        "state": "frozen",
        "files": [
            {
                "id": f"file-{uuid.uuid4().hex[:12]}",
                "name": "智学网成绩导入",
                "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "storage_name": "score-import.placeholder",
                "size_bytes": 0,
                "sha256": hashlib.sha256(b"score-import").hexdigest(),
                "added_at": now,
                "appended": False,
            }
        ],
        "frozen_at": now,
        "run_floor_id": 0,
    }
    groups = [
        {
            "source_label": f"score-import:{int(student['id'])}",
            "front_image": "",
            "back_image": "",
            "detected_name": str(student.get("name") or ""),
            "detected_class_name": str(student.get("class_name") or ""),
            "student_id": int(student["id"]),
            "student_name": str(student.get("name") or ""),
            "match_method": "manual",
            "match_score": 1.0,
        }
        for student in students
    ]
    analysis = {
        "scan_batch_id": batch_id,
        "generated_at": now,
        "source": "score_import",
        "groups": groups,
        "issues": [],
        "warnings": [],
        "total_pages": 0,
        "absent_students": [
            {
                "id": int(item["student_id"]),
                "name": item.get("name"),
                "class_name": item.get("class_name"),
            }
            for item in absent
        ],
        "front_page_parity": "odd",
    }
    _atomic_write_json(session_dir / "scan_upload_batch.json", manifest)
    analysis_path = session_dir / "scan_analysis_latest.json"
    _atomic_write_json(analysis_path, analysis)
    analysis_identity = hashlib.sha256(analysis_path.read_bytes()).hexdigest()
    _atomic_write_json(
        session_dir / "scan_decisions_state.json",
        {
            "analysis_identity": analysis_identity,
            "revision": 0,
            "public_decisions": [],
            "internal_decisions": [],
            "updated_at": now,
        },
    )
    _atomic_write_json(session_dir / "scan_manual_decisions_latest.json", [])
    batch_files = (
        Path(data_root) / "exams" / f"session_{int(session_id)}"
        / "scan_batches" / batch_id / "files"
    )
    batch_files.mkdir(parents=True, exist_ok=True)
    _write_cleared_receipt(data_root, session_id, now)
    return batch_id


def _write_cleared_receipt(data_root: Path, session_id: int, now: str) -> None:
    from backend.files.session_originals import _write_receipt

    _write_receipt(
        data_root,
        session_id,
        {
            "version": 1,
            "session_id": int(session_id),
            "state": "cleared",
            "scans_released_at": now,
            "cleared_at": now,
            "freed_bytes": {"scans": 0, "pages": 0, "annotations": 0},
            "deleted_files": 0,
            "kept_unrendered": 0,
        },
    )


def confirm_import(
    *,
    db: Any,
    job_store: Any,
    upload_config_dir: Path,
    templates_dir: Path,
    data_root: Path,
    session_id: int,
    plan: dict[str, Any],
    params: dict[str, Any],
    roster: list[dict[str, Any]],
    replace_existing: bool,
) -> dict[str, Any]:
    """Write imported scores as teacher-final results under a frozen batch.

    Order: blocking checks → config structure save (atomic, aborts everything)
    → replace prior import → frozen scan context files → one DB transaction
    (papers + results + teacher locks + attendance + status) → receipt.
    """
    blocking = [i for i in plan["issues"] if i.get("level") == "block"]
    if blocking:
        raise ScoreImportWriteError(
            "还有未解决的问题：" + "；".join(str(i["message"]) for i in blocking[:3])
        )
    if plan["students"].get("unresolved"):
        raise ScoreImportWriteError("还有未确认归属的成绩行，请先在预览中处理。")
    if plan.get("has_results") and not replace_existing:
        raise ScoreImportWriteError(
            "这场考试已有导入成绩；如需重新导入请确认覆盖已有结果。"
        )
    if not plan["_scores"]:
        raise ScoreImportWriteError("没有任何可写入的成绩。")

    save_import_config(
        db,
        upload_config_dir,
        int(session_id),
        collapses=list(plan.get("_collapses") or []),
        item_max=dict(plan.get("_item_max") or {}),
        combined_groups=list(plan.get("_combined_groups") or []),
        job_store=job_store,
    )

    if plan.get("has_results"):
        db.sessions.reset_session_for_scan_replacement(int(session_id))

    roster_by_id = {int(s["id"]): s for s in roster}
    imported_students = [
        roster_by_id[sid]
        for sid in sorted(plan["_scores"])
        if sid in roster_by_id
    ]
    batch_id = write_frozen_scan_context(
        data_root=data_root,
        templates_dir=templates_dir,
        session_id=int(session_id),
        students=imported_students,
        absent=list(plan.get("_absent") or []),
    )

    session = db.sessions.get_grading_session(int(session_id))
    from path_manager import resolve_stored_file_path

    rubric = json.loads(
        resolve_stored_file_path(session["rubric_path"], data_root=data_root)
        .read_text(encoding="utf-8")
    )
    items = plan["_items"]
    total_score = float(sum(item["max_score"] for item in items))
    item_types = {item["item_id"]: item["question_type"] for item in items}
    options = plan.get("_options") or {}
    imported_classes = set(plan.get("_imported_classes") or [])
    absent_ids = {int(item["student_id"]) for item in plan.get("_absent") or []}

    factory = SQLiteConnectionFactory(Path(db.db_path))
    imported_count = 0
    lock_count = 0
    with factory.session() as repository_session:
        with repository_session.transaction(immediate=True):
            papers = PaperRepository(repository_session)
            results = ResultRepository(repository_session)
            reviews = ReviewRepository(repository_session)
            sessions_repo = SessionRepository(repository_session)
            connection = repository_session.connection
            combined_by_student = plan.get("_combined_scores") or {}
            for student in imported_students:
                sid = int(student["id"])
                record = plan["_scores"][sid]
                combined = dict(combined_by_student.get(sid) or {})
                paper_id = papers.create_exam_paper(
                    int(session_id),
                    "",
                    "",
                    str(student.get("name") or ""),
                    sid,
                    "matched",
                    "graded",
                )
                details = [
                    QuestionGradingDetail(
                        question_id=item["item_id"],
                        score_awarded=float(record[item["item_id"]]),
                        deduction_reason=None,
                        knowledge_id=(
                            "OBJECTIVE"
                            if item_types[item["item_id"]] in _IMPORT_KIND_OBJECTIVE
                            else "UNKNOWN"
                        ),
                        knowledge_ids=(
                            ["OBJECTIVE"]
                            if item_types[item["item_id"]] in _IMPORT_KIND_OBJECTIVE
                            else []
                        ),
                        error_summary=TEACHER_LOCKED_ERROR_SUMMARY,
                    )
                    for item in items
                    if item["item_id"] in record
                ]
                metadata = {
                    item_id: {"observed_answer": letters}
                    for item_id, letters in (options.get(sid) or {}).items()
                    if item_id in {item["item_id"] for item in items}
                }
                raw_json = {
                    "source": "score_import",
                    "detail_metadata": metadata,
                    "grading_completeness": audit_grading_details(
                        rubric, details, combined_scores=combined
                    ),
                }
                if combined:
                    raw_json["combined_scores"] = combined
                grading_result = GradingResult(
                    student_name=str(student.get("name") or ""),
                    total_score=total_score,
                    student_score=float(
                        sum(record.values())
                        + sum(
                            float(entry["score"])
                            for entry in combined.values()
                        )
                    ),
                    needs_human_review=False,
                    grading_details=details,
                    raw_json=raw_json,
                )
                result_id = results.save_session_result(
                    int(session_id),
                    sid,
                    int(paper_id),
                    grading_result,
                    scan_batch_id=batch_id,
                )
                detail_rows = connection.execute(
                    "SELECT id, question_id FROM session_details WHERE result_id = ?",
                    (int(result_id),),
                ).fetchall()
                detail_ids = {
                    str(row["question_id"]): int(row["id"]) for row in detail_rows
                }
                confirmations = [
                    {
                        "student_id": sid,
                        "question_id": item["item_id"],
                        "score_awarded": float(record[item["item_id"]]),
                        "max_score": float(item["max_score"]),
                        # 导入只有分数没有批改理由；扣分原因留空，不写占位文案。
                        "deduction_reason": None,
                        "source_target_type": "session_detail",
                        "source_target_id": detail_ids[item["item_id"]],
                        "expected_revision": 0,
                        "result_id": int(result_id),
                        "detail_id": detail_ids[item["item_id"]],
                    }
                    for item in items
                    if item["item_id"] in record and item["item_id"] in detail_ids
                ]
                outcome = reviews.confirm_teacher_score_locks(
                    int(session_id),
                    batch_id,
                    confirmations,
                    # 智学网原始成绩可以带半分，教师最终分锁按原值写入。
                    allow_fractional_scores=True,
                )
                lock_count += int(outcome.get("inserted") or 0)
                imported_count += 1
            attendance = []
            for sid, student in roster_by_id.items():
                digits = "".join(
                    ch for ch in str(student.get("class_name") or "") if ch.isdigit()
                )
                if digits not in imported_classes:
                    continue
                attendance.append(
                    {
                        "student_id": sid,
                        "attendance_status": (
                            "present" if sid in plan["_scores"] else "absent"
                        ),
                        "source_reason": (
                            "成绩导入已写入"
                            if sid in plan["_scores"]
                            else "成绩表中无有效成绩（智学网导入）"
                        ),
                    }
                )
            if attendance:
                sessions_repo.replace_session_attendance(int(session_id), attendance)
            connection.execute(
                """
                UPDATE grading_sessions
                SET status = 'completed', updated_at = datetime('now','localtime')
                WHERE id = ?
                """,
                (int(session_id),),
            )

    receipt = {
        "imported_at": datetime.now().isoformat(timespec="seconds"),
        "upload_ids": sorted(str(v) for v in params.get("upload_ids") or []),
        "parameters": {
            "sheet_classes": params.get("sheet_classes") or {},
            "part_choices": params.get("part_choices") or {},
            "item_max": params.get("item_max") or {},
            "student_overrides": params.get("student_overrides") or {},
        },
        "counts": {
            "students": imported_count,
            "items": lock_count,
            "absent": len(plan.get("_absent") or []),
        },
        "scan_batch_id": batch_id,
    }
    _atomic_write_json(receipt_path(data_root, int(session_id)), receipt)
    return {
        "batch_id": batch_id,
        "imported_students": imported_count,
        "teacher_final_items": lock_count,
        "absent_students": len(plan.get("_absent") or []),
        "total_score": total_score,
    }

