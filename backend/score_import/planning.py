"""智学网成绩导入预览计划：列映射、名单匹配、检查与差异。

``build_import_plan`` 同时服务 preview 与 confirm——confirm 用相同输入重算
计划并比对 preview_revision，保证两次结果一致。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from backend.score_import.context import read_receipt
from backend.score_import.parsing import (
    ParsedSheet,
    ParsedUpload,
    class_digits,
    normalize_name,
    normalize_option_letters,
    parse_score,
)

OPTION_TYPES = {"choice", "single_choice"}
MULTI_CHOICE_TYPES = {"multi_choice", "multiple_choice"}
OPTION_CHECK_TYPES = OPTION_TYPES | MULTI_CHOICE_TYPES
_FILL_LIKE_TYPES = {
    "fill_blank",
    "judgement",
    "true_false",
    "direct_answer",
}
OBJECTIVE_TYPES = OPTION_TYPES | MULTI_CHOICE_TYPES | _FILL_LIKE_TYPES

# 汇总列在没有成员小题得分列时退化为合并给分组。
_GROUP_SUMMARY_TYPES = {
    "single_choice_total": (OPTION_TYPES, "单选题"),
    "multi_choice_total": (MULTI_CHOICE_TYPES, "多选题"),
    "fill_total": (_FILL_LIKE_TYPES, "填空题"),
}


def _issue(level: str, code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"level": level, "code": code, "message": message, **extra}


def _row_key(upload_id: str, sheet: str, excel_row: int) -> str:
    return f"{upload_id}:{sheet}:{int(excel_row)}"


def _question_number(question_id: str) -> int | None:
    match = re.fullmatch(r"[Qq](\d+)", str(question_id or "").strip())
    return int(match.group(1)) if match else None


def rubric_questions(rubric: dict[str, Any]) -> list[dict[str, Any]]:
    """One entry per rubric question with its scoring items (parts)."""
    result: list[dict[str, Any]] = []
    for question in rubric.get("questions") or []:
        if not isinstance(question, dict):
            continue
        question_id = str(question.get("question_id") or "").strip()
        qno = _question_number(question_id)
        if not question_id or qno is None:
            continue
        parts = [p for p in question.get("parts") or [] if isinstance(p, dict)]
        items = [
            {
                "item_id": str(part.get("part_id") or "").strip(),
                "part_index": index,
                "max_score": float(part.get("part_score") or part.get("max_score") or 0),
                "step_count": len(part.get("steps") or []),
            }
            for index, part in enumerate(parts)
            if str(part.get("part_id") or "").strip()
        ]
        if not items:
            items = [
                {
                    "item_id": question_id,
                    "part_index": 0,
                    "max_score": float(question.get("max_score") or 0),
                    "step_count": 0,
                }
            ]
        result.append(
            {
                "question_id": question_id,
                "qno": qno,
                "question_type": str(question.get("question_type") or ""),
                "max_score": float(question.get("max_score") or 0),
                "parts": items,
                "multi_part": len(items) > 1,
            }
        )
    return sorted(result, key=lambda item: item["qno"])


def _canonical_answer_text(entry: dict[str, Any]) -> str:
    for key in ("canonical_answer", "answer"):
        value = str(entry.get(key) or "").strip()
        if value:
            return value
    return ""


def _normalize_answer_letters(value: str) -> str:
    letters = re.sub(r"[^A-Za-z]", "", str(value or "")).upper()
    return "".join(sorted(set(letters)))


def normalize_params(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    sheet_classes: dict[str, dict[str, str]] = {}
    for upload_id, sheets in (raw.get("sheet_classes") or {}).items():
        if isinstance(sheets, dict):
            sheet_classes[str(upload_id)] = {
                str(name): str(value) for name, value in sheets.items()
            }
    part_choices = {
        str(q): str(v)
        for q, v in (raw.get("part_choices") or {}).items()
        if str(v) in {"parts", "whole"}
    }
    item_max = {
        str(k): float(v)
        for k, v in (raw.get("item_max") or {}).items()
        if isinstance(v, (int, float)) and not isinstance(v, bool) and float(v) > 0
    }
    student_overrides: dict[str, int | None] = {}
    for key, value in (raw.get("student_overrides") or {}).items():
        student_overrides[str(key)] = int(value) if value is not None else None
    return {
        "upload_ids": [str(v) for v in (raw.get("upload_ids") or [])],
        "sheet_classes": sheet_classes,
        "part_choices": part_choices,
        "item_max": item_max,
        "student_overrides": student_overrides,
    }


class _Roster:
    def __init__(self, students: list[dict[str, Any]]) -> None:
        self.by_id: dict[int, dict[str, Any]] = {}
        self.classes: set[str] = set()
        self._by_class_name: dict[tuple[str, str], list[int]] = {}
        self._by_name: dict[str, list[int]] = {}
        for student in students:
            sid = int(student["id"])
            self.by_id[sid] = student
            name = normalize_name(student.get("name"))
            self._by_name.setdefault(name, []).append(sid)
            digits = class_digits(student.get("class_name"))
            if digits is not None:
                self.classes.add(digits)
                self._by_class_name.setdefault((digits, name), []).append(sid)

    def candidates(self, name: str, digits: str | None) -> list[int]:
        if digits is not None:
            return self._by_class_name.get((digits, name), [])
        return self._by_name.get(name, [])

    def describe(self, student_id: int) -> str:
        student = self.by_id.get(student_id, {})
        return f"{student.get('name') or student_id}（{student.get('class_name') or '?'}班）"


def _sheet_identity(sheet: ParsedSheet) -> dict[str, Any]:
    return {
        "name": next((c for c in sheet.columns if c.key == "name"), None),
        "class": next((c for c in sheet.columns if c.key == "class"), None),
        "exam_no": next((c for c in sheet.columns if c.key == "exam_no"), None),
    }


def _row_value(sheet: ParsedSheet, row: Any, col: Any) -> Any:
    return row.cells.get(col.index, "") if col is not None else ""


def _question_columns(sheet: ParsedSheet) -> dict[int, dict[str, Any]]:
    """Score columns of one sheet grouped by question number."""
    result: dict[int, dict[str, Any]] = {}
    for col in sheet.columns:
        if col.role == "question" and col.question_no is not None:
            result.setdefault(col.question_no, {"parent": [], "sub": {}})["parent"].append(col)
        elif col.role == "sub" and col.question_no is not None and col.sub_index is not None:
            result.setdefault(col.question_no, {"parent": [], "sub": {}})["sub"].setdefault(col.sub_index, []).append(col)
    return result


def _column_observed_max(columns: list[Any]) -> float | None:
    values: list[float] = []
    for sheet, col in columns:
        for row in sheet.rows:
            value = parse_score(row.cells.get(col.index))
            if value is not None:
                values.append(value)
    return max(values) if values else None


def _plan_combined_groups(
    *,
    questions: list[dict[str, Any]],
    questions_by_qno: dict[int, dict[str, Any]],
    all_score_columns: dict[int, dict[str, Any]],
    combined_columns: dict[tuple[int, int], list[tuple[Any, Any]]],
    summary_group_columns: dict[str, list[tuple[Any, Any]]],
    option_key_answers: dict[int, str],
    item_max_overrides: dict[str, float],
    issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """合并给分组的两种来源：显式区间列（如 9-13）与题型汇总列。

    成员必须是评分依据里的单一评分项题目、没有任何一张成绩表给它们
    单独的小题得分列，且一题只能属于一个组。
    """

    def _has_own_score_columns(qno: int) -> bool:
        entry = all_score_columns.get(qno) or {}
        return bool(entry.get("parent") or entry.get("sub"))

    claimed: set[str] = set()
    specs: list[dict[str, Any]] = []

    def _register(
        members: list[dict[str, Any]],
        *,
        group_id: str,
        label: str,
        source: str,
        columns: list[tuple[Any, Any]],
        derivable: bool,
    ) -> None:
        spec = {
            "group_id": group_id,
            "label": label,
            "source": source,
            "columns": list(columns),
            "derivable": bool(derivable),
            "questions": list(members),
            "max_score": float(sum(
                float(item_max_overrides.get(
                    question["question_id"], question["max_score"]
                ))
                for question in members
            )),
            "counts": {"full": 0, "zero": 0, "intermediate": 0, "derived": 0},
        }
        specs.append(spec)
        claimed.update(question["question_id"] for question in members)

    # (a) 显式合并给分列「9-13」：成员为区间内每个题号。
    for (start, end), columns in sorted(combined_columns.items()):
        label = f"{start}-{end}"
        members: list[dict[str, Any]] = []
        reason: str | None = None
        for qno in range(int(start), int(end) + 1):
            question = questions_by_qno.get(qno)
            if question is None:
                reason = f"评分依据中没有第 {qno} 题"
                break
            if question["multi_part"]:
                reason = f"第 {qno} 题按多小问评分"
                break
            if question["question_id"] in claimed:
                reason = f"第 {qno} 题已属于其他合并给分组"
                break
            if _has_own_score_columns(qno):
                reason = f"第 {qno} 题已有单独的小题得分列"
                break
            members.append(question)
        if reason is not None:
            issues.append(_issue(
                "block", "combined_group_invalid",
                f"合并给分列「{label}」无法成立：{reason}。"
                "合并给分要求组内每题都是单一评分项且没有单独得分列。",
                column=label,
            ))
            continue
        _register(
            members,
            group_id=f"G{label}",
            label=label,
            source="column",
            columns=columns,
            derivable=False,
        )

    # (b) 题型汇总列（单选题/多选题/填空题）：该题型全部题目都没有
    # 单独得分列时，汇总值按合并给分导入。
    for key, columns in sorted(summary_group_columns.items()):
        types, label = _GROUP_SUMMARY_TYPES.get(key) or ((), "")
        if not types:
            continue
        type_questions = [
            question for question in questions
            if question["question_type"] in types
        ]
        if len(type_questions) < 2:
            continue
        if any(_has_own_score_columns(question["qno"]) for question in type_questions):
            # 部分小题有得分列时按逐题导入处理，汇总列不参与合并给分。
            continue
        if any(question["multi_part"] for question in type_questions):
            # 合并给分不支持多小问题型；未覆盖的题目按缺列照常报错。
            continue
        if any(question["question_id"] in claimed for question in type_questions):
            continue
        _register(
            type_questions,
            group_id=f"G{label}",
            label=label,
            source="summary",
            columns=columns,
            derivable=(
                key == "single_choice_total"
                and all(
                    question["qno"] in option_key_answers
                    for question in type_questions
                )
            ),
        )

    return specs


def build_import_plan(
    *,
    session_id: int,
    db: Any,
    data_root: Path,
    uploads: list[ParsedUpload],
    raw_params: dict[str, Any] | None,
) -> dict[str, Any]:
    params = normalize_params(raw_params)
    roster = _Roster(db.students.list_students())
    sheet_classes_param = params["sheet_classes"]

    session = db.sessions.get_grading_session(int(session_id))
    if session is None:
        raise KeyError(f"grading session not found: {session_id}")
    from path_manager import resolve_stored_file_path

    rubric_path = resolve_stored_file_path(
        session.get("rubric_path"), data_root=data_root
    )
    answer_key_path = resolve_stored_file_path(
        session.get("answer_key_path"), data_root=data_root
    )
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    answer_key = json.loads(answer_key_path.read_text(encoding="utf-8"))
    rubric_sha = hashlib.sha256(rubric_path.read_bytes()).hexdigest()
    questions = rubric_questions(rubric)
    questions_by_qno = {q["qno"]: q for q in questions}
    answers = {
        str(q.get("question_id") or ""): q
        for q in (answer_key.get("questions") or [])
        if isinstance(q, dict)
    }

    issues: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    skipped_by_class: dict[str, int] = {}
    skipped_rows = 0
    resolved_rows: list[dict[str, Any]] = []
    exam_no_to_student: dict[str, int] = {}

    def resolve_row(upload: ParsedUpload, sheet: ParsedSheet, row: Any) -> tuple[int | None, str | None]:
        row_key = _row_key(upload.upload_id, sheet.name, row.excel_row)
        ident = _sheet_identity(sheet)
        if row_key in params["student_overrides"]:
            override = params["student_overrides"][row_key]
            if override is None:
                return None, "override_skip"
            if int(override) in roster.by_id:
                return int(override), None
            unresolved.append({
                "row_key": row_key,
                "sheet": sheet.name,
                "row": row.excel_row,
                "name": str(_row_value(sheet, row, ident["name"]) or ""),
                "class": "",
                "candidates": [],
                "reason": "override_unknown",
            })
            return None, "unresolved"
        name = normalize_name(_row_value(sheet, row, ident["name"]))
        raw_class = _row_value(sheet, row, ident["class"])
        digits = (
            class_digits(raw_class)
            or class_digits(sheet.class_hint)
            or class_digits((sheet_classes_param.get(upload.upload_id) or {}).get(sheet.name))
        )
        exam_no = str(_row_value(sheet, row, ident["exam_no"]) or "").strip()
        if exam_no and exam_no in exam_no_to_student:
            return exam_no_to_student[exam_no], digits
        candidates = roster.candidates(name, digits)
        info = {
            "row_key": row_key,
            "sheet": sheet.name,
            "row": row.excel_row,
            "name": str(_row_value(sheet, row, ident["name"]) or ""),
            "class": digits or "",
            "candidates": [
                {
                    "student_id": sid,
                    "name": str(roster.by_id[sid].get("name") or ""),
                    "class_name": str(roster.by_id[sid].get("class_name") or ""),
                }
                for sid in candidates
            ],
        }
        if len(candidates) == 1:
            return candidates[0], digits
        if digits is not None and digits not in roster.classes:
            return None, f"skipped:{digits}"
        info["reason"] = "student_not_found" if not candidates else "student_ambiguous"
        unresolved.append(info)
        return None, "unresolved"

    # Pass 1: score sheets first so exam_no links exist for the options sheets.
    pairs = [(u, s) for u in uploads for s in u.sheets]
    for upload, sheet in sorted(pairs, key=lambda pair: pair[1].kind != "scores"):
        if sheet.kind == "unrecognized":
            issues.append(_issue(
                "block", "sheet_unrecognized",
                f"工作表「{sheet.name}」在前几行中找不到「姓名」表头，无法识别。",
                sheet=sheet.name,
            ))
            continue
        for row in sheet.rows:
            student_id, class_flag = resolve_row(upload, sheet, row)
            if class_flag == "override_skip":
                skipped_rows += 1
                continue
            if student_id is None and isinstance(class_flag, str) and class_flag.startswith("skipped:"):
                skipped_by_class[class_flag.split(":", 1)[1]] = (
                    skipped_by_class.get(class_flag.split(":", 1)[1], 0) + 1
                )
                continue
            if student_id is None:
                continue
            resolved_rows.append(
                {
                    "row_key": _row_key(upload.upload_id, sheet.name, row.excel_row),
                    "sheet": sheet,
                    "row": row,
                    "student_id": student_id,
                    "class_digits": class_flag or "",
                }
            )
            ident = _sheet_identity(sheet)
            exam_no = str(_row_value(sheet, row, ident["exam_no"]) or "").strip()
            if exam_no:
                exam_no_to_student[exam_no] = student_id

    score_row_keys: dict[int, list[str]] = {}
    for entry in resolved_rows:
        if entry["sheet"].kind == "scores":
            score_row_keys.setdefault(entry["student_id"], []).append(entry["row_key"])
    for sid, keys in score_row_keys.items():
        if len(keys) > 1:
            issues.append(_issue(
                "block", "duplicate_student_row",
                f"{roster.describe(sid)}在得分明细中对应 {len(keys)} 行。",
                student_id=sid,
            ))

    # ---------- options sheets: answer key row + per-student letters ----------
    option_key_answers: dict[int, str] = {}
    observed_options: dict[int, dict[str, str]] = {}
    for upload, sheet in pairs:
        if sheet.kind != "options":
            continue
        for col in sheet.columns:
            if col.role != "question" or col.question_no is None:
                continue
            letters = normalize_option_letters(sheet.answer_key_cells.get(col.index, ""))
            question = questions_by_qno.get(col.question_no)
            if letters is None:
                issues.append(_issue(
                    "block", "answer_key_cell_invalid",
                    f"「{sheet.name}」答案行第 {col.question_no} 题列没有有效选项字母。",
                    sheet=sheet.name, row=sheet.answer_key_row, column=col.header,
                ))
                continue
            option_key_answers[col.question_no] = letters
            if question is not None and question["question_type"] in OPTION_CHECK_TYPES:
                expected = _normalize_answer_letters(
                    _canonical_answer_text(answers.get(question["question_id"]) or {})
                )
                if expected and letters != expected:
                    issues.append(_issue(
                        "block", "answer_key_mismatch",
                        f"「{sheet.name}」答案行第 {col.question_no} 题为 {letters}，"
                        f"与评分依据的标准答案 {expected} 不一致（可能是错版卷）。",
                        sheet=sheet.name, row=sheet.answer_key_row, column=col.header,
                    ))
        for entry in resolved_rows:
            if entry["sheet"] is not sheet:
                continue
            sid = entry["student_id"]
            for col in sheet.columns:
                if col.role != "question" or col.question_no is None:
                    continue
                letters = normalize_option_letters(entry["row"].cells.get(col.index, ""))
                if letters:
                    observed_options.setdefault(sid, {})[f"Q{col.question_no}"] = letters

    # ---------- per-question column mapping (grade-wide decision) ----------
    all_score_columns: dict[int, dict[str, Any]] = {}
    combined_columns: dict[tuple[int, int], list[tuple[Any, Any]]] = {}
    summary_group_columns: dict[str, list[tuple[Any, Any]]] = {}
    unknown_columns: list[str] = []
    for upload, sheet in pairs:
        if sheet.kind != "scores":
            continue
        for col in sheet.columns:
            if col.role in {"question", "sub"} and col.question_no is not None:
                bucket = all_score_columns.setdefault(
                    col.question_no, {"parent": [], "sub": {}}
                )
                if col.role == "question":
                    bucket["parent"].append((sheet, col))
                else:
                    bucket["sub"].setdefault(col.sub_index, []).append((sheet, col))
            elif (
                col.role == "combined"
                and col.question_no is not None
                and col.combined_end is not None
            ):
                combined_columns.setdefault(
                    (int(col.question_no), int(col.combined_end)), []
                ).append((sheet, col))
            elif col.role == "summary" and col.key in _GROUP_SUMMARY_TYPES:
                summary_group_columns.setdefault(str(col.key), []).append(
                    (sheet, col)
                )
            elif col.role == "unknown" and col.header:
                unknown_columns.append(f"{sheet.name}!{col.header}")
    if unknown_columns:
        issues.append(_issue(
            "warn", "unknown_columns",
            "以下列未能识别，已忽略：" + "、".join(sorted(set(unknown_columns))),
        ))

    item_max_overrides = params["item_max"]
    combined_group_specs = _plan_combined_groups(
        questions=questions,
        questions_by_qno=questions_by_qno,
        all_score_columns=all_score_columns,
        combined_columns=combined_columns,
        summary_group_columns=summary_group_columns,
        option_key_answers=option_key_answers,
        item_max_overrides=item_max_overrides,
        issues=issues,
    )
    member_group: dict[str, dict[str, Any]] = {
        question["question_id"]: spec
        for spec in combined_group_specs
        for question in spec["questions"]
    }

    question_plans: list[dict[str, Any]] = []
    collapses: set[str] = set()
    planned_items: list[dict[str, Any]] = []

    for question in questions:
        qno = question["qno"]
        cols = all_score_columns.get(qno, {"parent": [], "sub": {}})
        qtype = question["question_type"]
        plan: dict[str, Any] = {
            "question_id": question["question_id"],
            "question_type": qtype,
            "columns": sorted(
                {col.header for _s, col in cols["parent"]}
                | {col.header for cols_list in cols["sub"].values() for _s, col in cols_list}
            ),
        }
        group_spec = member_group.get(question["question_id"])
        if group_spec is not None:
            choice = "combined"
            plan["group_id"] = group_spec["group_id"]
            plan["group_label"] = group_spec["label"]
        elif question["multi_part"]:
            # 「按小问导入」要求每个覆盖该题的成绩表都提供全部小问列；
            # 任何一张表只有整题列或缺少小问列，整场都退回整题给分。
            covering_sheets = (
                {sheet for sheet, _col in cols["parent"]}
                | {
                    sheet
                    for _index, column_list in cols["sub"].items()
                    for sheet, _col in column_list
                }
            )
            subs_by_sheet: dict[Any, set[int]] = {
                sheet: set() for sheet in covering_sheets
            }
            for sub_index, column_list in cols["sub"].items():
                for sheet, _col in column_list:
                    subs_by_sheet[sheet].add(int(sub_index))
            full_sub_indexes = set(range(1, len(question["parts"]) + 1))
            grade_wide_parts = bool(covering_sheets) and all(
                indexes == full_sub_indexes
                for indexes in subs_by_sheet.values()
            )
            fits_parts = (
                grade_wide_parts
                and all(
                    (v := _column_observed_max(cols["sub"][index + 1])) is None
                    or v <= question["parts"][index]["max_score"]
                    for index in range(len(question["parts"]))
                    if (index + 1) in cols["sub"]
                )
            )
            requested = params["part_choices"].get(question["question_id"])
            if requested == "whole":
                choice = "whole"
            elif fits_parts:
                choice = "parts"
                if requested != "parts":
                    plan["status"] = "needs_confirm"
            else:
                choice = "whole"
                if requested == "parts":
                    issues.append(_issue(
                        "warn", "parts_request_rejected",
                        f"{question['question_id']} 无法按小问导入："
                        f"按小问导入要求每个班的成绩表都包含全部 "
                        f"{len(question['parts'])} 个小问列，至少一张表只有整题分或缺少小问列，"
                        "本场已按整题给分处理。",
                    ))
            if choice == "whole":
                collapses.add(question["question_id"])
        elif cols["parent"] or cols["sub"]:
            choice = "single"
        elif qtype in OPTION_TYPES and qno in option_key_answers:
            choice = "derive_options"
        else:
            choice = "single"
            issues.append(_issue(
                "block", "missing_score_column",
                f"{question['question_id']} 在得分明细表中没有对应的得分列。",
                column=str(qno),
            ))
        plan["choice"] = choice
        plan.setdefault("status", "ok")

        if choice in {"whole", "single", "derive_options", "combined"}:
            effective = [
                {
                    "item_id": question["question_id"],
                    "question_id": question["question_id"],
                    "question_type": qtype,
                    "max_score": float(question["max_score"]),
                    "part_index": 0,
                }
            ]
        else:
            effective = [
                {
                    "item_id": part["item_id"],
                    "question_id": question["question_id"],
                    "question_type": qtype,
                    "max_score": float(part["max_score"]),
                    "part_index": part["part_index"],
                }
                for part in question["parts"]
            ]
        for item in effective:
            if item["item_id"] in item_max_overrides:
                item["max_score"] = float(item_max_overrides[item["item_id"]])
        for item in effective:
            if choice == "parts":
                observed = _column_observed_max(cols["sub"].get(item["part_index"] + 1, []))
            elif cols["parent"]:
                observed = _column_observed_max(cols["parent"])
            elif cols["sub"]:
                observed = sum(
                    _column_observed_max(cols["sub"][k]) or 0 for k in sorted(cols["sub"])
                )
            else:
                observed = None
            item["observed_max"] = observed
        planned_items.extend(effective)
        plan["items"] = [
            {
                "item_id": item["item_id"],
                "max_score": item["max_score"],
                "observed_max": item.get("observed_max"),
            }
            for item in effective
        ]
        if plan.get("status") == "needs_confirm":
            issues.append(_issue(
                "warn", "parts_mapping_needs_confirm",
                f"{question['question_id']} 的小问数与得分列一致，默认按小问导入；"
                "如需整题给分请在确认前改选「整题」。",
            ))
        question_plans.append(plan)

    # ---------- per-student score assignment ----------
    planned_by_question: dict[str, dict[str, Any]] = {}
    for plan in question_plans:
        qno = _question_number(plan["question_id"])
        planned_by_question[plan["question_id"]] = {"qno": qno, **plan}
    item_lookup = {item["item_id"]: item for item in planned_items}
    scores: dict[int, dict[str, float]] = {}
    combined_scores: dict[int, dict[str, dict[str, Any]]] = {}
    absent_rows: list[dict[str, Any]] = []
    imported_classes: set[str] = set()

    for entry in resolved_rows:
        sheet = entry["sheet"]
        if sheet.kind != "scores":
            continue
        sid = entry["student_id"]
        row = entry["row"]
        sheet_qcols = _question_columns(sheet)
        score_cols = [
            c for c in sheet.columns
            if c.role in {"question", "sub", "combined"}
            or (c.role == "summary" and c.key in _GROUP_SUMMARY_TYPES)
        ]
        all_blank = bool(score_cols) and all(
            parse_score(row.cells.get(col.index)) is None for col in score_cols
        )
        if all_blank:
            absent_rows.append(
                {"student_id": sid, "reason": "blank_row", "row_key": entry["row_key"]}
            )
            continue
        if entry["class_digits"]:
            imported_classes.add(str(entry["class_digits"]))
        record = scores.setdefault(sid, {})
        for plan in question_plans:
            qno = _question_number(plan["question_id"])
            local = sheet_qcols.get(qno, {"parent": [], "sub": {}})
            if local["parent"] and local["sub"]:
                parent_value = parse_score(
                    row.cells.get(local["parent"][0].index)
                )
                sub_values = [
                    parse_score(row.cells.get(col_list[0].index))
                    for _key, col_list in sorted(local["sub"].items())
                ]
                if (
                    parent_value is not None
                    and sub_values
                    and all(value is not None for value in sub_values)
                    and abs(sum(sub_values) - parent_value) > 0.01
                ):
                    issues.append(_issue(
                        "block", "parent_sub_sum_mismatch",
                        f"{roster.describe(sid)}的 {plan['question_id']} 整题分 "
                        f"{parent_value:g} 与小问合计 {sum(sub_values):g} 不一致。",
                        student_id=sid, sheet=sheet.name,
                        row=row.excel_row,
                        column=str(local["parent"][0].header or ""),
                    ))
            for item in plan["items"]:
                item_id = item["item_id"]
                if plan["choice"] in {"whole", "single"}:
                    value = None
                    if local["parent"]:
                        value = parse_score(row.cells.get(local["parent"][0].index))
                    if value is None and local["sub"]:
                        subs = [
                            parse_score(row.cells.get(col_list[0].index))
                            for _k, col_list in sorted(local["sub"].items())
                        ]
                        if any(v is not None for v in subs):
                            value = sum(v or 0.0 for v in subs)
                    if value is None:
                        continue
                elif plan["choice"] == "parts":
                    question = questions_by_qno[qno]
                    p_index = next(
                        (
                            p["part_index"]
                            for p in question["parts"]
                            if p["item_id"] == item_id
                        ),
                        0,
                    )
                    col_list = local["sub"].get(p_index + 1)
                    if not col_list:
                        continue
                    value = parse_score(row.cells.get(col_list[0].index))
                    if value is None:
                        continue
                elif plan["choice"] == "derive_options":
                    letters = observed_options.get(sid, {}).get(plan["question_id"])
                    key = option_key_answers.get(qno)
                    value = float(item["max_score"]) if letters is not None and letters == key else 0.0
                else:
                    continue
                record[item_id] = float(value)

        # ---- 合并给分组：组内成员只有一个组总分 ----
        for spec in combined_group_specs:
            group_col = next(
                (col for col_sheet, col in spec["columns"] if col_sheet is sheet),
                None,
            )
            value = (
                parse_score(row.cells.get(group_col.index))
                if group_col is not None
                else None
            )
            if value is None:
                continue
            members = spec["questions"]
            member_ids = [question["question_id"] for question in members]
            group_max = float(spec["max_score"])
            if value < 0 or value > group_max + 1e-6:
                issues.append(_issue(
                    "block", "invalid_score",
                    f"{roster.describe(sid)}的合并给分「{spec['label']}」得分 "
                    f"{value:g} 超出 0～{group_max:g} 的范围。",
                    student_id=sid, sheet=sheet.name, row=row.excel_row,
                    column=str(group_col.header if group_col else spec["label"]),
                ))
                continue
            if spec["derivable"]:
                derived: dict[str, float] | None = {}
                for question in members:
                    letters = observed_options.get(sid, {}).get(
                        question["question_id"]
                    )
                    key = option_key_answers.get(question["qno"])
                    if letters is None or key is None:
                        derived = None
                        break
                    derived[question["question_id"]] = (
                        float(
                            item_max_overrides.get(
                                question["question_id"], question["max_score"]
                            )
                        )
                        if letters == key
                        else 0.0
                    )
                if derived is not None and abs(
                    sum(derived.values()) - value
                ) <= 0.01:
                    record.update(derived)
                    spec["counts"]["derived"] += 1
                    continue
            if value >= group_max - 1e-6:
                for question in members:
                    record[question["question_id"]] = float(
                        item_max_overrides.get(
                            question["question_id"], question["max_score"]
                        )
                    )
                spec["counts"]["full"] += 1
            elif value <= 1e-6:
                for question in members:
                    record[question["question_id"]] = 0.0
                spec["counts"]["zero"] += 1
            else:
                combined_scores.setdefault(sid, {})[spec["group_id"]] = {
                    "score": float(value),
                    "max_score": group_max,
                    "question_ids": member_ids,
                }
                spec["counts"]["intermediate"] += 1

        # ---- numeric checks ----
        for item in planned_items:
            if item["item_id"] not in record:
                continue
            value = record[item["item_id"]]
            if value < 0:
                issues.append(_issue(
                    "block", "invalid_score",
                    f"{roster.describe(sid)}的 {item['item_id']} 得分 {value:g} 不能为负。",
                    student_id=sid, column=item["item_id"],
                ))
            elif value > item["max_score"] + 1e-6:
                issues.append(_issue(
                    "block", "score_above_max",
                    f"{roster.describe(sid)}的 {item['item_id']} 得分 {value:g} 超过满分 {item['max_score']:g}。",
                    student_id=sid, column=item["item_id"],
                ))
        covered_items = set(record)
        for group_entry in (combined_scores.get(sid) or {}).values():
            covered_items.update(group_entry["question_ids"])
        missing = [
            item["item_id"]
            for item in planned_items
            if item["item_id"] not in covered_items
        ]
        if missing:
            issues.append(_issue(
                "block", "missing_item_scores",
                f"{roster.describe(sid)}缺少 {len(missing)} 个评分项的得分："
                + "、".join(missing[:8])
                + ("…" if len(missing) > 8 else ""),
                student_id=sid,
            ))

        # ---- totals check ----
        objective = subjective = total = single_total = None
        for col in sheet.columns:
            if col.role != "summary":
                continue
            value = parse_score(row.cells.get(col.index))
            if value is None:
                continue
            if col.key == "objective":
                objective = value
            elif col.key == "subjective":
                subjective = value
            elif col.key == "total":
                total = value
            elif col.key == "single_choice_total":
                single_total = value
        written_sum = sum(record.values()) + sum(
            float(group_entry["score"])
            for group_entry in (combined_scores.get(sid) or {}).values()
        )
        expected_total = None
        if objective is not None or subjective is not None:
            expected_total = (objective or 0.0) + (subjective or 0.0)
        elif total is not None:
            expected_total = total
        if expected_total is not None and abs(written_sum - expected_total) > 0.01:
            issues.append(_issue(
                "block", "total_mismatch",
                f"{roster.describe(sid)}的逐项合计 {written_sum:g} 与表内合计 {expected_total:g} 不一致。",
                student_id=sid,
            ))
        if single_total is not None:
            derived_items = [
                item for item in planned_items
                if item["question_type"] in OPTION_TYPES
                and planned_by_question[item["question_id"]]["choice"] == "derive_options"
            ]
            if derived_items and abs(
                sum(record.get(item["item_id"], 0.0) for item in derived_items) - single_total
            ) > 0.01:
                issues.append(_issue(
                    "block", "choice_derived_total_mismatch",
                    f"{roster.describe(sid)}由选项推算的单选题得分与「单选题」列 {single_total:g} 不一致。",
                    student_id=sid,
                ))

    # ---- option-vs-column disagreement on choice items that have score columns ----
    for sid, record in scores.items():
        options = observed_options.get(sid, {})
        for item in planned_items:
            if item["question_type"] not in OPTION_TYPES:
                continue
            plan = planned_by_question[item["question_id"]]
            if plan["choice"] != "single" or item["item_id"] not in record:
                continue
            letters = options.get(item["question_id"])
            if letters is None:
                continue
            key = option_key_answers.get(plan["qno"])
            derived = item["max_score"] if letters == key else 0.0
            if abs(derived - record[item["item_id"]]) > 0.01:
                issues.append(_issue(
                    "block", "choice_option_score_conflict",
                    f"{roster.describe(sid)}的 {item['question_id']}：得分列 {record[item['item_id']]:g} "
                    f"与作答选项（{letters}）推算的 {derived:g} 不一致。",
                    student_id=sid, column=item["question_id"],
                ))

    # ---------- absent roster students ----------
    absent: list[dict[str, Any]] = []
    blank_student_ids = {row["student_id"] for row in absent_rows}
    for student in roster.by_id.values():
        sid = int(student["id"])
        digits = class_digits(student.get("class_name"))
        if digits is None or digits not in imported_classes:
            continue
        if sid in scores or sid in blank_student_ids:
            continue
        absent.append(
            {
                "student_id": sid,
                "name": str(student.get("name") or ""),
                "class_name": str(student.get("class_name") or ""),
                "reason": "missing_from_scores",
            }
        )
    for row in absent_rows:
        student = roster.by_id[row["student_id"]]
        absent.append(
            {
                "student_id": row["student_id"],
                "name": str(student.get("name") or ""),
                "class_name": str(student.get("class_name") or ""),
                "reason": "blank_row",
            }
        )

    # ---------- diff against existing results ----------
    has_results = _results_state(db, session_id)["result_count"] > 0
    receipt = read_receipt(data_root, int(session_id))
    diff = (
        _build_diff(db, session_id, scores, receipt, combined_scores)
        if has_results
        else None
    )

    preview_revision = hashlib.sha256(
        json.dumps(
            {
                "session_id": int(session_id),
                "upload_ids": sorted(params["upload_ids"]),
                "params": {
                    "sheet_classes": params["sheet_classes"],
                    "part_choices": params["part_choices"],
                    "item_max": params["item_max"],
                    "student_overrides": params["student_overrides"],
                },
                "rubric_sha256": rubric_sha,
                "results_state": _results_state(db, session_id),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()

    return {
        "questions": question_plans,
        "groups": [
            {
                "group_id": spec["group_id"],
                "label": spec["label"],
                "question_ids": [
                    question["question_id"] for question in spec["questions"]
                ],
                "max_score": float(spec["max_score"]),
                "counts": dict(spec["counts"]),
            }
            for spec in combined_group_specs
        ],
        "students": {
            "matched": len(scores),
            "absent": absent,
            "unresolved": unresolved,
            "skipped_by_class": skipped_by_class,
            "skipped_rows": skipped_rows,
        },
        "issues": issues,
        "diff": diff,
        "proposed_rubric_total": float(sum(item["max_score"] for item in planned_items)),
        "preview_revision": preview_revision,
        "has_results": has_results,
        "_scores": scores,
        "_combined_scores": combined_scores,
        "_combined_groups": [
            {
                "group_id": spec["group_id"],
                "label": spec["label"],
                "question_ids": [
                    question["question_id"] for question in spec["questions"]
                ],
                "max_score": float(spec["max_score"]),
            }
            for spec in combined_group_specs
        ],
        "_options": observed_options,
        "_items": planned_items,
        "_collapses": sorted(collapses),
        "_item_max": item_max_overrides,
        "_absent": absent,
        "_imported_classes": sorted(imported_classes),
    }


def _results_state(db: Any, session_id: int) -> dict[str, Any]:
    with db._connect() as conn:
        results = conn.execute(
            "SELECT COUNT(*) AS count, COALESCE(MAX(id),0) AS max_id FROM session_results WHERE session_id = ?",
            (int(session_id),),
        ).fetchone()
        locks = conn.execute(
            "SELECT COUNT(*) AS count, COALESCE(MAX(revision),0) AS max_rev, "
            "COALESCE(MAX(updated_at),'') AS max_updated "
            "FROM teacher_score_locks WHERE session_id = ?",
            (int(session_id),),
        ).fetchone()
    return {
        "result_count": int(results["count"]),
        "result_max_id": int(results["max_id"]),
        "lock_count": int(locks["count"]),
        "lock_max_revision": int(locks["max_rev"]),
        "lock_max_updated": str(locks["max_updated"] or ""),
    }


def _build_diff(
    db: Any,
    session_id: int,
    scores: dict[int, dict[str, float]],
    receipt: dict[str, Any] | None,
    combined_scores: dict[int, dict[str, dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    with db._connect() as conn:
        rows = conn.execute(
            """
            SELECT sr.student_id, sd.question_id, sd.score_awarded
            FROM session_details sd
            JOIN session_results sr ON sr.id = sd.result_id
            WHERE sr.session_id = ?
            """,
            (int(session_id),),
        ).fetchall()
        raw_rows = conn.execute(
            "SELECT student_id, raw_json FROM session_results WHERE session_id = ?",
            (int(session_id),),
        ).fetchall()
        lock_rows = conn.execute(
            "SELECT student_id, question_id, revision, updated_at "
            "FROM teacher_score_locks WHERE session_id = ?",
            (int(session_id),),
        ).fetchall()
    current: dict[tuple[int, str], float] = {}
    for row in rows:
        current[(int(row["student_id"]), str(row["question_id"]))] = float(
            row["score_awarded"] or 0
        )
    from backend.scan_grading.grading_completeness import raw_combined_scores

    for row in raw_rows:
        for group_id, entry in raw_combined_scores(row["raw_json"]).items():
            current[(int(row["student_id"]), group_id)] = float(
                entry["score"]
            )
    edited: set[tuple[int, str]] = set()
    imported_at = str((receipt or {}).get("imported_at") or "")
    for lock in lock_rows:
        key = (int(lock["student_id"]), str(lock["question_id"]))
        if int(lock["revision"] or 0) > 1 or (
            imported_at and str(lock["updated_at"] or "") > imported_at
        ):
            edited.add(key)
    changes: list[dict[str, Any]] = []
    for sid, record in scores.items():
        for item_id, new_value in record.items():
            key = (sid, item_id)
            old_value = current.get(key)
            if old_value is None or abs(old_value - new_value) > 0.01:
                changes.append(
                    {
                        "student_id": sid,
                        "item_id": item_id,
                        "old_score": old_value,
                        "new_score": new_value,
                        "teacher_edited": key in edited,
                    }
                )
    for sid, groups in (combined_scores or {}).items():
        for group_id, entry in groups.items():
            key = (sid, group_id)
            new_value = float(entry["score"])
            old_value = current.get(key)
            if old_value is None or abs(old_value - new_value) > 0.01:
                changes.append(
                    {
                        "student_id": sid,
                        "item_id": group_id,
                        "old_score": old_value,
                        "new_score": new_value,
                        "teacher_edited": key in edited,
                    }
                )
    return changes





