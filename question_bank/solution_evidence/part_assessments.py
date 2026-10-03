"""Per-part difficulty reads referencing the existing solution evidence.

Per-part difficulty has one source: the formula score in
``question_part_difficulty_features`` (active rows). This module only reads;
it never stores a second tag truth or changes a frozen marking standard.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

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
    from question_bank.training_criteria.analysis import (
        solution_evidence_source_content_hash,
    )
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
    """Latest usable evidence plus active formula difficulty per part.

    A record exists for every question whose newest proposed/approved evidence
    version is present. ``parts[].difficulty`` is the active
    ``question_part_difficulty_features`` formula score; it is ``None`` when the
    part has no active row or the row's content fingerprint no longer matches
    the current question (需重评). ``revision`` is a derived token identifying
    the evidence version plus its current formula difficulties, not a stored
    row number.
    """
    if not ids:
        return {}
    with reading(db_path, connection) as conn:
        marks = ",".join("?" for _ in ids)
        # created_at has second precision; when two usable versions tie, the
        # most recently inserted row (max rowid) wins.
        rows = [
            row
            for row in conn.execute(
                f"""SELECT v.question_id, v.evidence_version_id, v.evidence_json,
                           v.source_content_hash AS evidence_source_hash,
                           v.status AS evidence_status, v.graph_release_id
                    FROM question_solution_evidence_versions v
                    JOIN questions q ON q.id = v.question_id AND q.is_deleted = 0
                    JOIN (
                        SELECT question_id, MAX(created_at) AS max_created
                        FROM question_solution_evidence_versions
                        WHERE status IN ('proposed', 'approved')
                        GROUP BY question_id
                    ) m ON m.question_id = v.question_id AND m.max_created = v.created_at
                    WHERE v.status IN ('proposed', 'approved')
                      AND v.question_id IN ({marks})
                    ORDER BY v.rowid""", list(ids),
            ).fetchall()
        ]
        seen: set[int] = set()
        deduped: list[Any] = []
        for row in reversed(rows):
            question_id = int(row["question_id"])
            if question_id in seen:
                continue
            seen.add(question_id)
            deduped.append(row)
        rows = deduped
        features: dict[int, dict[str, Any]] = {}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_part_difficulty_features'").fetchone() is not None:
            for row in conn.execute(
                f"""SELECT question_id, part_id, features_json, formula_difficulty,
                           formula_version, source_content_hash
                    FROM question_part_difficulty_features
                    WHERE is_active = 1 AND question_id IN ({marks})""", list(ids),
            ).fetchall():
                features.setdefault(int(row["question_id"]), {})[str(row["part_id"])] = row
        result = {}
        inputs = {}
        question_rows: dict[int, Any] = {}
        if rows and verify_source:
            # A caller that already loaded this batch can reuse its exact
            # question inputs. Source comparison still runs for every profile.
            inputs = question_inputs if question_inputs is not None else current_inputs(
                db_path, [int(r["question_id"]) for r in rows], conn, data_root=data_root)
            question_rows = {
                int(row["id"]): dict(row)
                for row in conn.execute(
                    f"SELECT * FROM questions WHERE id IN ({marks}) AND is_deleted = 0", list(ids),
                ).fetchall()
            }
        from question_bank.services.standard_difficulty import (
            question_content_fingerprint,
        )
        from question_bank.solution_evidence.repository import (
            _classification_from_evidence_payload,
        )
        from question_bank.training_criteria.analysis import (
            solution_evidence_source_content_hash,
        )
        for row in rows:
            question_id = int(row["question_id"])
            evidence = json.loads(row["evidence_json"])
            # Match the decorated payload readers get from repository.latest().
            evidence["whole_question_classification"] = (
                _classification_from_evidence_payload(evidence)
            )
            evidence_source_hash = str(row["evidence_source_hash"])
            fingerprint = ""
            alias = ""
            if verify_source:
                question = inputs[question_id]
                current_hash = solution_evidence_source_content_hash(question)
                alias = "" if current_hash == evidence_source_hash else source_alias(question, evidence_source_hash)
                question_row = question_rows.get(question_id)
                fingerprint = (
                    question_content_fingerprint(question_row)
                    if question_row is not None else ""
                )
            else:
                current_hash = evidence_source_hash
            parts = []
            for part in evidence.get("parts", []):
                feature = features.get(question_id, {}).get(str(part.get("part_id")))
                rationale = ""
                formula_version = ""
                difficulty = None
                if feature is not None:
                    stale = (
                        verify_source
                        and fingerprint
                        and str(feature["source_content_hash"]) != fingerprint
                    )
                    if not stale:
                        difficulty = feature["formula_difficulty"]
                    try:
                        rationale = str(
                            (json.loads(str(feature["features_json"] or "{}")) or {}).get("evidence") or ""
                        )
                    except (TypeError, ValueError):
                        rationale = ""
                    formula_version = str(feature["formula_version"])
                parts.append({"part_id": part.get("part_id"), "difficulty": difficulty,
                              "source": "formula", "rationale": rationale,
                              "formula_version": formula_version})
            revision = hashlib.sha256(json.dumps(
                [str(row["evidence_version_id"]),
                 [(part["part_id"], part["difficulty"], part["formula_version"]) for part in parts]],
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()[:16]
            available = alias is not None if verify_source else True
            result[question_id] = {
                "question_id": question_id,
                "evidence_version_id": str(row["evidence_version_id"]),
                "evidence": evidence,
                "evidence_source_hash": evidence_source_hash,
                "evidence_status": str(row["evidence_status"]),
                "graph_release_id": row["graph_release_id"],
                "current_source_content_hash": current_hash,
                "source_type_alias": alias or "",
                "available": available,
                "reason": None if available else "part_assessment_source_changed",
                "revision": revision,
                "parts": parts,
            }
        return result


def direct_targets(part: Mapping[str, Any], links: Mapping[str, Sequence[Any]]) -> tuple[str, ...]:
    """Stable keys of a part's resolved direct links from the links table."""
    from question_bank.solution_evidence.knowledge_links import direct_targets_for_part
    return direct_targets_for_part(part, links)


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
                # Current skeletons keep only observable student work here;
                # older skeletons also included explanation and answer anchors.
                observable = {str(point.get("observable_evidence") or "").strip()} - {""}
                if set(step.get("required_elements") or []) not in (set(required), observable):
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
                from question_bank.training_criteria.combined_analysis import (
                    DeferredCombinedAnalysisItem,
                    UnmappedFineTermResolver,
                )
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
                               final_points: Sequence[Mapping[str, Any]],
                               links: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]] | None:
    """Read frozen, final step facts without changing the publication or its locks."""
    from question_bank.solution_evidence.knowledge_links import direct_links_for_part
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
            targets = direct_links_for_part({"evidence_points": [point]}, links)
            if targets:
                observed.append((point, targets))
        for point, targets in observed:
            for target in targets:
                result.append({"part_id": part["part_id"], "point_id": point["evidence_point_id"],
                               "stable_key": target.stable_key, "achieved": int(states[point["evidence_point_id"]] == "met"),
                               "weight": target.weight / len(observed),
                               "difficulty": estimates.get(part["part_id"], {}).get("difficulty")})
    return result
