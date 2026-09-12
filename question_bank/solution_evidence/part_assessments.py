"""Versioned small-part estimates referencing the existing solution evidence.

Knowledge links remain in solution-evidence versions; this module never stores a
second tag truth or changes a frozen marking standard.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

LEGACY_ESSAY_TYPES = ("解答题（画图）", "解答题（计算）", "解答题（证明）")


@contextmanager
def reading(db_path: Path, connection: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
    if connection is not None:
        yield connection
        return
    borrowed = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    borrowed.row_factory = sqlite3.Row
    try:
        borrowed.execute("PRAGMA query_only=ON")
        borrowed.execute("BEGIN")
        yield borrowed
    finally:
        borrowed.close()


def source_alias(question: Any, expected: str) -> str | None:
    from question_bank.training_criteria.analysis import solution_evidence_source_content_hash
    if solution_evidence_source_content_hash(question) == expected:
        return ""
    if question.tagging_context.question_type != "解答题":
        return None
    matches = [kind for kind in LEGACY_ESSAY_TYPES if solution_evidence_source_content_hash(
        replace(question, tagging_context=replace(question.tagging_context, question_type=kind))
    ) == expected]
    return matches[0] if len(matches) == 1 else None


def current_inputs(db_path: Path, ids: Sequence[int], connection: sqlite3.Connection, *,
                   data_root: Path | None = None) -> dict[int, Any]:
    from question_bank.training_criteria.adapters import QuestionAnalysisInputLoader
    root = Path(data_root) if data_root is not None else Path(db_path).parent.parent
    return {q.question_id: q for q in QuestionAnalysisInputLoader(
        db_path=db_path, data_root=root, external_connection=connection,
    ).load(ids)}


def load_profiles(db_path: Path, ids: Sequence[int], *, connection: sqlite3.Connection | None = None,
                  verify_source: bool = True, data_root: Path | None = None,
                  question_inputs: Mapping[int, Any] | None = None) -> dict[int, dict[str, Any]]:
    if not ids:
        return {}
    with reading(db_path, connection) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_part_assessment_profiles'").fetchone() is None:
            return {}
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"""SELECT p.*,e.evidence_json,e.source_content_hash evidence_source_hash,e.status evidence_status,
                       e.graph_release_id
                FROM question_part_assessment_profiles p
                JOIN question_solution_evidence_versions e ON e.evidence_version_id=p.evidence_version_id
                JOIN questions q ON q.id=p.question_id
                WHERE p.status='active' AND q.is_deleted=0 AND p.question_id IN ({marks})""", list(ids),
        ).fetchall()
        result = {}
        inputs = {}
        if rows and verify_source:
            # A caller that already loaded this batch can reuse its exact
            # question inputs. Source comparison still runs for every profile.
            inputs = question_inputs if question_inputs is not None else current_inputs(
                db_path, [int(r["question_id"]) for r in rows], conn, data_root=data_root)
        from question_bank.training_criteria.analysis import solution_evidence_source_content_hash
        for row in rows:
            record = dict(row)
            record["parts"] = json.loads(record.pop("parts_json"))
            record["evidence"] = json.loads(record.pop("evidence_json"))
            record["available"] = record["evidence_status"] in {"proposed", "approved"}
            if verify_source:
                question = inputs[int(row["question_id"])]
                record["available"] = record["available"] and (
                    solution_evidence_source_content_hash(question) == row["current_source_content_hash"]
                    and source_alias(question, str(row["evidence_source_hash"])) is not None
                )
            record["reason"] = None if record["available"] else "part_assessment_source_changed"
            result[int(row["question_id"])] = record
        return result


def save_profile(db_path: Path, *, question_id: int, evidence_version_id: str,
                 parts: Sequence[Mapping[str, Any]], created_by: str) -> dict[str, Any]:
    from question_bank.database.schema import connect
    from question_bank.training_criteria.analysis import solution_evidence_source_content_hash
    if not str(created_by).strip():
        raise ValueError("Estimate author is required")
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM question_solution_evidence_versions WHERE question_id=? AND evidence_version_id=?",
                           (question_id, evidence_version_id)).fetchone()
        if row is None or row["status"] not in {"proposed", "approved"}:
            raise ValueError("Usable source evidence is required")
        question = current_inputs(db_path, [question_id], conn)[question_id]
        alias = source_alias(question, str(row["source_content_hash"]))
        if alias is None:
            raise ValueError("Source content changed; estimates cannot be attached")
        expected_parts = json.loads(row["evidence_json"])["parts"]
        expected_ids = {p["part_id"] for p in expected_parts}
        if len(parts) != len(expected_ids) or {p.get("part_id") for p in parts} != expected_ids:
            raise ValueError("Every source part must be supplied exactly once")
        clean = []
        for part in parts:
            difficulty = part.get("difficulty")
            if difficulty is not None and (isinstance(difficulty, bool) or not isinstance(difficulty, (int, float))
                                          or not math.isfinite(difficulty) or not 1 <= difficulty <= 10):
                raise ValueError("Small-part difficulty must be between 1 and 10, or unknown")
            if part.get("source") not in {"codex_self", "model", "whole_question", "teacher", "unknown"}:
                raise ValueError("Difficulty estimate source is invalid")
            if part["source"] == "whole_question" and len(expected_ids) != 1:
                raise ValueError("Whole-question difficulty cannot be copied to multiple parts")
            if not str(part.get("rationale") or "").strip():
                raise ValueError("Every estimate needs a rationale")
            clean.append({"part_id": part["part_id"], "difficulty": difficulty, "source": part["source"],
                          "rationale": str(part["rationale"]).strip(),
                          "review_note": str(part.get("review_note") or "").strip(),
                          **({"difficulty_scale_version": str(part["difficulty_scale_version"])}
                             if part.get("difficulty_scale_version") else {})})
        encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True)
        current_hash = solution_evidence_source_content_hash(question)
        previous = conn.execute("SELECT * FROM question_part_assessment_profiles WHERE question_id=? AND status='active'", (question_id,)).fetchone()
        if previous and previous["parts_json"] == encoded and previous["evidence_version_id"] == evidence_version_id and previous["current_source_content_hash"] == current_hash:
            return {"question_id": question_id, "revision": previous["revision"], "unchanged": True}
        revision = conn.execute("SELECT COALESCE(MAX(revision),0)+1 FROM question_part_assessment_profiles WHERE question_id=?", (question_id,)).fetchone()[0]
        conn.execute("UPDATE question_part_assessment_profiles SET status='inactive' WHERE question_id=? AND status='active'", (question_id,))
        conn.execute("""INSERT INTO question_part_assessment_profiles(question_id,evidence_version_id,
                     current_source_content_hash,source_type_alias,parts_json,revision,created_by)
                     VALUES(?,?,?,?,?,?,?)""", (question_id, evidence_version_id, current_hash, alias, encoded, revision, created_by))
        return {"question_id": question_id, "revision": revision, "unchanged": False}


def model_part_estimates(raw: Any, evidence: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Keep old responses readable; new estimates must cover the exact source parts."""
    if raw is None:
        return ()
    expected = {p["part_id"] for p in evidence["parts"]}
    if not isinstance(raw, (list, tuple)) or len(raw) != len(expected):
        raise ValueError("Small-part estimates must cover every source part")
    clean = []
    for value in raw:
        if not isinstance(value, Mapping) or value.get("part_id") not in expected:
            raise ValueError("Small-part estimate has an unknown part identity")
        difficulty = value.get("difficulty")
        rationale = str(value.get("rationale") or "").strip()
        if (isinstance(difficulty, bool) or not isinstance(difficulty, (int, float))
                or not math.isfinite(difficulty) or not 1 <= difficulty <= 10 or not rationale):
            raise ValueError("Small-part estimate requires difficulty 1–10 and a rationale")
        clean.append({"part_id": value["part_id"], "difficulty": difficulty,
                      "rationale": rationale, "source": "model",
                      **({"difficulty_scale_version": str(value["difficulty_scale_version"])}
                         if value.get("difficulty_scale_version") else {})})
    if len({p["part_id"] for p in clean}) != len(expected):
        raise ValueError("Small-part estimate identities must be unique")
    return tuple(clean)


def direct_targets(part: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(key) for step in part.get("evidence_points", [])
                              for link in step.get("fine_term_links", [])
                              if link.get("role") == "direct" and link.get("core_resolution", {}).get("status") == "resolved"
                              for key in link["core_resolution"].get("stable_keys", [])))


def exam_assessment_state(assessment: Mapping[str, Any], metadata: Mapping[str, Any], *, teacher_final: bool,
                          teacher_score: float | None = None) -> dict[str, Any]:
    result = dict(assessment)
    if result.get("granularity") not in {"part", "step"}:
        return result
    if metadata.get("answer_is_blank_or_no_valid_work") is True and not (teacher_final and teacher_score is not None and teacher_score > 0):
        result.update(eligible=False, reason="blank_or_no_valid_work")
    elif not teacher_final and (metadata.get("need_review") is True or metadata.get("answer_discarded_by_smudge") is True):
        result.update(eligible=False, reason="assessment_needs_review")
    else:
        result["eligible"] = True
    return result


def match_rubric_parts(rubric_question: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Match complete step obligations uniquely, never by count, label or array position."""
    parts = evidence.get("parts", [])
    matched: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    rubric_parts = rubric_question.get("parts") or [rubric_question]
    if len(rubric_parts) != len(parts):
        return {}
    for rubric_part in rubric_parts:
        steps = rubric_part.get("steps", [])
        candidates = []
        for part in parts:
            if part.get("response_mode") != rubric_part.get("response_mode"):
                continue
            if any(key in rubric_part and rubric_part[key] != part.get(key, [] if key == "proof_obligations" else False)
                   for key in ("proof_obligations", "allow_alternative_methods")):
                continue
            points = part.get("evidence_points", [])
            if not steps or len(steps) != len(points):
                continue
            by_goal = {str(point.get("target")): point for point in points}
            if len(by_goal) != len(points):
                continue
            valid = True
            for step in steps:
                point = by_goal.get(str(step.get("core_goal")))
                if point is None:
                    valid = False
                    break
                required = list(dict.fromkeys(str(point[k]).strip() for k in ("justification", "answer_anchor", "observable_evidence") if str(point.get(k) or "").strip()))
                if set(step.get("required_elements") or []) != set(required):
                    valid = False
                    break
                if set(step.get("counterexamples") or []) != set(point.get("counterexamples") or []) or set(step.get("equivalent_rules") or []) != set(point.get("equivalent_rules") or []):
                    valid = False
                    break
            if valid:
                candidates.append(part)
        if len(candidates) != 1 or candidates[0]["part_id"] in used:
            return {}
        part = candidates[0]
        used.add(part["part_id"])
        matched[str(rubric_part.get("part_id") or rubric_question.get("question_id"))] = part
    return matched


def historical_source_matches(db_path: Path, profile: Mapping[str, Any], rubric: Mapping[str, Any],
                              connection: sqlite3.Connection | None = None, *, session_id: str | int | None = None,
                              data_root: Path | None = None) -> bool:
    version_id = rubric.get("source_evidence_version_id")
    if not version_id:
        return False
    with reading(db_path, connection) as conn:
        row = conn.execute("SELECT question_id,source_content_hash FROM question_solution_evidence_versions WHERE evidence_version_id=?", (version_id,)).fetchone()
        source_hashes = {profile["current_source_content_hash"], profile["evidence_source_hash"]}
        if row is not None:
            return bool(int(row["question_id"]) == profile["question_id"] and row["source_content_hash"] in source_hashes)
        # Config analysis has a temporary question identity before bank adoption.
        # Follow the existing immutable adoption receipt and original checkpoint;
        # an equal number of parts or a matching stem summary is not sufficient.
        if session_id is None:
            return False
        source_rows = conn.execute("SELECT source_reference,source_content_hash FROM question_solution_evidence_versions WHERE question_id=? AND source_kind='combined_model'", (profile["question_id"],)).fetchall()
        for source_row in source_rows:
            match = re.fullmatch(r"deferred-linked:config:(\d+):([0-9a-f]{32}):([^:]+):[0-9a-f]{64}", str(source_row["source_reference"]))
            if not match or match[1] != str(session_id) or match[3] != rubric.get("question_id") or source_row["source_content_hash"] not in source_hashes:
                continue
            root = Path(data_root) if data_root is not None else Path(db_path).parent.parent
            artifact = root / "config" / "uploaded" / f"deferred_question_analysis_{match[2]}.json"
            try:
                payload = json.loads(artifact.read_text(encoding="utf-8"))
                if str(payload.get("session_id")) != str(session_id):
                    continue
                operation = f"config:{session_id}:{match[2]}"
                if payload.get("bundle", {}).get("operation_id") != operation:
                    continue
                items = [i for i in payload["bundle"]["items"] if i.get("source_question_ref") == match[3]]
                if len(items) != 1:
                    continue
                from question_bank.training_criteria.in_memory import DeferredCombinedAnalysisItem, UnmappedFineTermResolver
                original = DeferredCombinedAnalysisItem.from_checkpoint_dict(items[0], resolver=UnmappedFineTermResolver())
                if original.operation_id != operation or original.solution_evidence.version_id != version_id:
                    continue
                def obligations(evidence):
                    parts = json.loads(json.dumps(evidence["parts"]))
                    for part in parts:
                        for point in part["evidence_points"]:
                            point.pop("fine_term_links", None)
                    return parts
                if obligations(original.solution_evidence.to_dict()) == obligations(profile["evidence"]):
                    return True
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return False


def training_part_observations(profile: Mapping[str, Any], criteria: Mapping[str, Any],
                               final_points: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]] | None:
    """Read frozen, final step facts without changing the publication or its locks."""
    from question_bank.training_criteria.analysis import TrainingCriterionPoint
    if not profile.get("available"):
        return []
    try:
        frozen = tuple(TrainingCriterionPoint.from_dict(p) for p in criteria.get("points", []))
    except (ValueError, TypeError, KeyError):
        return None
    source_points = {p["evidence_point_id"]: p for part in profile["evidence"]["parts"] for p in part["evidence_points"]}
    embedded = criteria.get("solution_evidence") or {}
    if embedded.get("source_content_hash") not in {profile.get("current_source_content_hash"), profile.get("evidence_source_hash")}:
        return None
    if not frozen or len(frozen) != len(source_points) or set(source_points) != {p.point_id for p in frozen}:
        return None
    for point in frozen:
        original = source_points[point.point_id]
        if any(getattr(point, key) != (tuple(original.get(key, [])) if key in {"equivalent_rules", "counterexamples", "depends_on"} else original.get(key, ""))
               for key in ("target", "observable_evidence", "equivalent_rules", "counterexamples", "depends_on")):
            return None
    states = {str(p.get("point_id")): p.get("state") for p in final_points}
    if len(states) != len(final_points) or set(states) != set(source_points) or any(s not in {"met", "not_met"} for s in states.values()):
        return None
    estimates = {p["part_id"]: p for p in profile["parts"]}
    result = []
    for part in profile["evidence"]["parts"]:
        observed = []
        for point in part["evidence_points"]:
            # An unobserved dependent failure does not establish another deficit.
            if states[point["evidence_point_id"]] == "not_met" and any(states.get(dep) != "met" for dep in point.get("depends_on", [])):
                continue
            targets = direct_targets({"evidence_points": [point]})
            if targets:
                observed.append((point, targets))
        for point, targets in observed:
            for target in targets:
                result.append({"part_id": part["part_id"], "point_id": point["evidence_point_id"],
                               "stable_key": target, "achieved": int(states[point["evidence_point_id"]] == "met"),
                               "weight": 1.0 / len(observed) / len(targets),
                               "difficulty": estimates.get(part["part_id"], {}).get("difficulty")})
    return result
