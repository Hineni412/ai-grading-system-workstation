"""Frozen per-session evidence snapshots (design E §7.2).

Once exam questions are linked to bank questions, the session freezes each
question's current usable evidence version, its knowledge links under that
version's graph release, and the part assessments into
``user_data/config/uploaded/evidence_snapshot-<session>.json``.  Later bank
version changes never touch the snapshot; projection and mastery read only
this file plus the rubric's ``evidence_point_ids``.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from question_bank.solution_evidence.knowledge_links import load_point_links
from question_bank.solution_evidence.part_assessments import (
    load_profiles,
    reading,
)

SNAPSHOT_SCHEMA_VERSION = "evidence-snapshot-v1"


def snapshot_file_name(grading_session_id: str | int) -> str:
    return f"evidence_snapshot-{grading_session_id}.json"


def snapshot_path(
    upload_config_dir: Path,
    grading_session_id: str | int,
) -> Path:
    return Path(upload_config_dir) / snapshot_file_name(grading_session_id)


def load_snapshot(
    upload_config_dir: Path,
    grading_session_id: str | int,
) -> dict[str, Any] | None:
    path = snapshot_path(upload_config_dir, grading_session_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("schema_version")) != SNAPSHOT_SCHEMA_VERSION:
        return None
    if not isinstance(payload.get("questions"), dict):
        return None
    return payload


def build_session_snapshot(
    db_path: Path,
    *,
    source_to_bank: Mapping[str, int],
    connection: sqlite3.Connection | None = None,
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Freeze every confirmed-linked question's usable evidence + links.

    ``source_to_bank`` maps rubric ``question_id`` refs to bank question ids.
    Questions without a usable evidence version are recorded with
    ``usable: false`` so the rubric annotation and the projection can tell
    "no snapshot" apart from "snapshot frozen but unusable".
    """
    bank_ids = sorted({int(bid) for bid in source_to_bank.values()})
    with reading(Path(db_path), connection) as conn:
        version_rows = (
            conn.execute(
                f"""
                SELECT v.question_id, v.evidence_version_id, v.evidence_json,
                       v.graph_release_id
                FROM question_solution_evidence_versions v
                JOIN (
                    SELECT question_id, MAX(created_at) AS max_created
                    FROM question_solution_evidence_versions
                    WHERE status IN ('proposed', 'approved')
                    GROUP BY question_id
                ) m
                  ON m.question_id = v.question_id
                 AND m.max_created = v.created_at
                WHERE v.status IN ('proposed', 'approved')
                  AND v.question_id IN (
                      SELECT value FROM json_each(?))
                """,
                (json.dumps(bank_ids),),
            ).fetchall()
            if bank_ids
            else []
        )
    versions = {
        int(row["question_id"]): row for row in version_rows
    }
    grouped_links = load_point_links(
        Path(db_path),
        [str(row["evidence_version_id"]) for row in version_rows],
        None,
        connection=connection,
    )
    with reading(Path(db_path), connection) as conn:
        link_release_rows = (
            conn.execute(
                f"""
                SELECT evidence_version_id, graph_release_id,
                       MAX(created_at) AS latest
                FROM evidence_point_knowledge_links
                WHERE evidence_version_id IN (
                    SELECT value FROM json_each(?))
                GROUP BY evidence_version_id, graph_release_id
                """,
                (json.dumps([str(row["evidence_version_id"]) for row in version_rows]),),
            ).fetchall()
            if version_rows
            else []
        )
        active_release_row = conn.execute(
            """
            SELECT release_id FROM knowledge_graph_releases
            WHERE status = 'active'
            ORDER BY activated_at DESC, created_at DESC
            LIMIT 1
            """
        ).fetchone()
    link_release: dict[str, tuple[str, str]] = {}
    for row in link_release_rows:
        key = str(row["evidence_version_id"])
        candidate = (str(row["latest"]), str(row["graph_release_id"]))
        if key not in link_release or candidate > link_release[key]:
            link_release[key] = candidate
    active_release_id = (
        str(active_release_row["release_id"])
        if active_release_row is not None
        else ""
    )
    profiles = load_profiles(
        Path(db_path),
        bank_ids,
        connection=connection,
        verify_source=False,
        data_root=data_root,
    )
    release_id = ""
    questions: dict[str, Any] = {}
    for source_ref, bank_id in sorted(
        source_to_bank.items(), key=lambda item: int(item[1])
    ):
        version = versions.get(int(bank_id))
        if version is None:
            questions[str(source_ref)] = {
                "bank_question_id": int(bank_id),
                "usable": False,
            }
            continue
        version_id = str(version["evidence_version_id"])
        # Record the release actually selected by the shared link reader.
        entry_release = (
            next((link.graph_release_id for point_links in grouped_links.get(version_id, {}).values()
                  for link in point_links), "")
            or link_release.get(version_id, ("", ""))[1]
            or active_release_id
        )
        if entry_release:
            release_id = release_id or entry_release
        links: dict[str, list[dict[str, Any]]] = {}
        for point_id, point_links in grouped_links.get(
            version_id, {}
        ).items():
            links[point_id] = [
                {
                    "role": link.role,
                    "term_id": link.term_id,
                    "stable_key": link.stable_key,
                    "resolution_status": link.resolution_status,
                    "weight": link.weight,
                    "source_kind": link.source_kind,
                }
                for link in point_links
            ]
        profile = profiles.get(int(bank_id))
        profile_parts = (
            profile.get("parts")
            if profile is not None
            and str(profile.get("evidence_version_id") or "") == version_id
            else []
        )
        questions[str(source_ref)] = {
            "bank_question_id": int(bank_id),
            "usable": True,
            "source_evidence_version_id": version_id,
            "graph_release_id": entry_release,
            "evidence": json.loads(str(version["evidence_json"])),
            "links": links,
            "parts": [
                {
                    key: part.get(key)
                    for key in (
                        "part_id",
                        "difficulty",
                        "source",
                        "rationale",
                    )
                }
                for part in profile_parts or []
                if isinstance(part, Mapping)
            ],
        }
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "graph_release_id": release_id,
        "questions": questions,
    }


def write_snapshot(
    upload_config_dir: Path,
    grading_session_id: str | int,
    snapshot: Mapping[str, Any],
) -> Path:
    path = snapshot_path(upload_config_dir, grading_session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return path


def freeze_session_evidence_snapshot(
    db_path: Path,
    *,
    grading_session_id: str | int,
    upload_config_dir: Path,
    connection: sqlite3.Connection | None = None,
    data_root: Path | None = None,
    rubric_path: Path | None = None,
    answer_key: Mapping[str, Any] | None = None,
) -> Path | None:
    """Freeze once from confirmed links; preserve an existing session snapshot.

    Call after link confirmation AND after any evidence writes the session
    depends on. A later tag retry never changes an existing snapshot.
    When ``rubric_path`` is given, the published rubric file is
    annotated in place: matched steps gain ``evidence_point_ids`` and each
    linked question records ``evidence_snapshot_ref`` /
    ``source_evidence_version_id`` / ``graph_release_id`` (§7.1/§7.2).

    Returns the written path, or ``None`` when the session has no
    confirmed links.
    """
    existing = load_snapshot(upload_config_dir, grading_session_id)
    if existing is not None:
        if rubric_path is not None:
            annotate_rubric_with_snapshot(
                Path(rubric_path),
                existing,
                grading_session_id,
                answer_key=answer_key,
            )
        return snapshot_path(upload_config_dir, grading_session_id)
    with reading(Path(db_path), connection) as conn:
        rows = conn.execute(
            """
            SELECT source_question_id, bank_question_id
            FROM grading_question_links
            WHERE grading_session_id = ? AND status = 'confirmed'
            """,
            (str(grading_session_id),),
        ).fetchall()
    source_to_bank = {
        str(row["source_question_id"]): int(row["bank_question_id"])
        for row in rows
    }
    if not source_to_bank:
        return None
    snapshot = build_session_snapshot(
        Path(db_path),
        source_to_bank=source_to_bank,
        connection=connection,
        data_root=data_root,
    )
    written = write_snapshot(
        Path(upload_config_dir),
        grading_session_id,
        snapshot,
    )
    if rubric_path is not None:
        annotate_rubric_with_snapshot(
            Path(rubric_path),
            snapshot,
            grading_session_id,
            answer_key=answer_key,
        )
    return written


def annotate_rubric_with_snapshot(
    rubric_path: Path,
    snapshot: Mapping[str, Any],
    grading_session_id: str | int,
    *,
    answer_key: Mapping[str, Any] | None = None,
) -> bool:
    """Stamp ``evidence_point_ids`` and question-level snapshot refs.

    Step-to-point matching reuses ``match_rubric_parts`` (exact obligation
    matching, never count/position). Steps that cannot be matched are left
    untouched so the projection reports them as missing coverage instead of
    guessing by position. Returns whether the file changed.
    """
    try:
        rubric = json.loads(Path(rubric_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(rubric, dict):
        return False
    changed = annotate_rubric_payload(
        rubric,
        snapshot,
        grading_session_id,
        answer_key=answer_key,
    )
    if changed:
        Path(rubric_path).write_text(
            json.dumps(rubric, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
    return changed


def annotate_rubric_payload(
    rubric: dict[str, Any],
    snapshot: Mapping[str, Any],
    grading_session_id: str | int,
    *,
    answer_key: Mapping[str, Any] | None = None,
) -> bool:
    """In-memory variant of :func:`annotate_rubric_with_snapshot`."""
    from question_bank.solution_evidence.part_assessments import (
        match_rubric_parts,
    )

    questions_map = snapshot.get("questions")
    if not isinstance(questions_map, Mapping):
        return False
    questions = rubric.get("questions")
    if not isinstance(questions, list):
        return False
    snapshot_ref = snapshot_file_name(grading_session_id)
    answers = {
        str(answer.get("question_id")): answer
        for answer in (answer_key or {}).get("questions", ())
        if isinstance(answer, Mapping)
    }
    changed = False
    for question_index, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            continue
        source_ref = _rubric_question_ref(question, question_index)
        snap_question = questions_map.get(source_ref)
        if not isinstance(snap_question, Mapping) or not snap_question.get("usable"):
            continue
        evidence = snap_question.get("evidence")
        if isinstance(evidence, Mapping):
            matched = match_rubric_parts(question, evidence)
            rubric_parts = question.get("parts")
            part_list = (
                [part for part in rubric_parts if isinstance(part, dict)]
                if isinstance(rubric_parts, list) and rubric_parts
                else [question]
            )
            # Compatibility for generated objective rubrics whose old composer
            # dropped point ids before replacing the target with a generic goal.
            # Require the frozen version, exact answer and a single whole-question
            # obligation; never infer a multi-part/step mapping by position.
            objective_part = None
            source_parts = evidence.get("parts") or []
            answer = answers.get(source_ref, {})
            if (len(part_list) == len(source_parts) == 1
                    and question.get("source_evidence_version_id") == snap_question.get("source_evidence_version_id")
                    and question.get("question_type") in {"choice", "fill_blank"}):
                source_part = source_parts[0]
                part = part_list[0]
                points = source_part.get("evidence_points") or []
                steps = part.get("steps") or []
                canonical = str(source_part.get("canonical_answer") or "").strip()
                goal = "选择正确的选项" if question.get("question_type") == "choice" else "填写正确或等价的答案"
                if (source_part.get("response_mode") == part.get("response_mode") == "exact_objective"
                        and len(points) == len(steps) == 1 and canonical
                        and str(answer.get("canonical_answer") or "").strip() == canonical
                        and steps[0].get("core_goal") == goal
                        and canonical in (steps[0].get("required_elements") or [])):
                    objective_part = source_part
            for part in part_list:
                part_key = str(part.get("part_id") or source_ref)
                linked_part_id = resolve_evidence_part_id(part, snap_question, source_ref)
                evidence_part = next((p for p in evidence.get("parts", ())
                                      if p.get("part_id") == linked_part_id), None)
                if evidence_part is None:
                    evidence_part = matched.get(part_key) or objective_part
                if not isinstance(evidence_part, Mapping):
                    continue
                evidence_part_id = str(evidence_part.get("part_id") or "")
                if evidence_part_id and part.get("evidence_part_id") != evidence_part_id:
                    part["evidence_part_id"] = evidence_part_id
                    changed = True
                points_by_goal = {
                    str(point.get("target")): point
                    for point in evidence_part.get("evidence_points") or ()
                    if isinstance(point, Mapping)
                }
                for step in part.get("steps") or ():
                    if not isinstance(step, dict):
                        continue
                    if step.get("evidence_point_ids"):
                        continue
                    point = points_by_goal.get(str(step.get("core_goal")))
                    if point is None and evidence_part is objective_part:
                        point = objective_part["evidence_points"][0]
                    if point is None:
                        continue
                    point_id = str(point.get("evidence_point_id") or "")
                    if point_id and step.get("evidence_point_ids") != [point_id]:
                        step["evidence_point_ids"] = [point_id]
                        changed = True
        desired = {
            "evidence_snapshot_ref": snapshot_ref,
            "source_evidence_version_id": snap_question.get(
                "source_evidence_version_id"
            ),
            "graph_release_id": snap_question.get("graph_release_id") or "",
        }
        for key, value in desired.items():
            if question.get(key) != value:
                question[key] = value
                changed = True
    return changed


def resolve_evidence_part_id(part: Mapping[str, Any], snapshot_question: Mapping[str, Any], source_ref: str = "") -> str:
    """Resolve a renamed rubric part by its frozen point ids, never position."""
    parts = (snapshot_question.get("evidence") or {}).get("parts", ())
    explicit = str(part.get("evidence_part_id") or "")
    if explicit:
        return explicit
    ids = {str(pid) for step in part.get("steps", ()) for pid in step.get("evidence_point_ids", ())}
    ids.update(str(pid) for pid in part.get("uncovered_evidence_point_ids", ()))
    matches = [str(p["part_id"]) for p in parts
               if ids and ids.issubset({str(point.get("evidence_point_id")) for point in p.get("evidence_points", ())})]
    return matches[0] if len(matches) == 1 else str(part.get("part_id") or source_ref)


def validate_rubric_evidence_coverage(
    rubric: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> list[str]:
    """§7.1 coverage check for snapshot-linked rubric questions.

    Every frozen evidence point must be covered by exactly one step. For
    ``allow_alternative_methods`` parts, uncovered points are allowed only
    when listed in the part-level ``uncovered_evidence_point_ids``.
    Returns a list of human-readable violations (empty when clean).
    """
    questions_map = snapshot.get("questions")
    if not isinstance(questions_map, Mapping):
        return []
    questions = rubric.get("questions")
    if not isinstance(questions, list):
        return []
    violations: list[str] = []
    for question_index, question in enumerate(questions, start=1):
        if not isinstance(question, Mapping):
            continue
        source_ref = _rubric_question_ref(question, question_index)
        snap_question = questions_map.get(source_ref)
        if not isinstance(snap_question, Mapping) or not snap_question.get("usable"):
            continue
        evidence = snap_question.get("evidence")
        if not isinstance(evidence, Mapping):
            continue
        expected_by_part: dict[str, set[str]] = {}
        for evidence_part in evidence.get("parts") or ():
            if not isinstance(evidence_part, Mapping):
                continue
            part_id = str(evidence_part.get("part_id") or source_ref)
            expected_by_part[part_id] = {
                str(point.get("evidence_point_id") or "")
                for point in evidence_part.get("evidence_points") or ()
                if isinstance(point, Mapping)
                and str(point.get("evidence_point_id") or "")
            }
        rubric_parts = question.get("parts")
        part_list = (
            [part for part in rubric_parts if isinstance(part, Mapping)]
            if isinstance(rubric_parts, list) and rubric_parts
            else [question]
        )
        seen_globally: dict[str, str] = {}
        covered_parts: set[str] = set()
        for part in part_list:
            part_id = resolve_evidence_part_id(part, snap_question, source_ref)
            covered_parts.add(part_id)
            uncovered = {
                str(pid)
                for pid in part.get("uncovered_evidence_point_ids") or ()
            }
            seen_in_part: set[str] = set()
            for step in part.get("steps") or ():
                if not isinstance(step, Mapping):
                    continue
                for raw in step.get("evidence_point_ids") or ():
                    point_id = str(raw)
                    if not point_id:
                        continue
                    if point_id in seen_in_part:
                        violations.append(
                            f"{source_ref}/{part_id}: evidence point "
                            f"{point_id} covered by more than one step"
                        )
                    if point_id in uncovered:
                        violations.append(
                            f"{source_ref}/{part_id}: evidence point "
                            f"{point_id} is both covered and uncovered"
                        )
                    owner = seen_globally.setdefault(point_id, part_id)
                    if owner != part_id:
                        violations.append(
                            f"{source_ref}: evidence point {point_id} "
                            f"appears in both {owner} and {part_id}"
                        )
                    seen_in_part.add(point_id)
            expected = expected_by_part.get(part_id)
            if expected is None:
                violations.append(f"{source_ref}/{part_id}: no matching frozen evidence part")
                continue
            unknown = (seen_in_part | uncovered) - expected
            if unknown:
                violations.append(f"{source_ref}/{part_id}: unknown evidence points {sorted(unknown)}")
            if uncovered and not part.get("allow_alternative_methods"):
                violations.append(f"{source_ref}/{part_id}: uncovered points require alternative methods")
            missing = expected - seen_in_part - uncovered
            if not part.get("allow_alternative_methods") and missing:
                violations.append(
                    f"{source_ref}/{part_id}: uncovered evidence points "
                    f"{sorted(missing)}"
                )
            if part.get("allow_alternative_methods") and (
                expected - seen_in_part - uncovered
            ):
                violations.append(
                    f"{source_ref}/{part_id}: alternative-method part must "
                    f"list uncovered points explicitly; missing "
                    f"{sorted(expected - seen_in_part - uncovered)}"
                )
        missing_parts = set(expected_by_part) - covered_parts
        for part_id in sorted(missing_parts):
            if expected_by_part.get(part_id):
                violations.append(
                    f"{source_ref}: evidence part {part_id} has no rubric part"
                )
    return violations


def _rubric_question_ref(
    question: Mapping[str, Any],
    index: int = 0,
) -> str:
    return str(
        question.get("question_id")
        or question.get("id")
        or question.get("number")
        or (f"Q{index}" if index else "")
    ).strip()


def resolved_direct_keys(
    snapshot_question: Mapping[str, Any],
    evidence_point_ids: list[str],
) -> tuple[str, ...]:
    """Direct stable keys for the frozen covered points of one item."""
    links = snapshot_question.get("links")
    if not isinstance(links, Mapping):
        return ()
    result: list[str] = []
    for point_id in evidence_point_ids:
        for link in links.get(str(point_id), ()) or ():
            if not isinstance(link, Mapping):
                continue
            if (
                str(link.get("role")) != "direct"
                or str(link.get("resolution_status")) != "resolved"
            ):
                continue
            key = str(link.get("stable_key") or link.get("term_id") or "")
            if key and key not in result:
                result.append(key)
    return tuple(result)


def question_point_ids(
    snapshot_question: Mapping[str, Any],
) -> tuple[str, ...]:
    """All evidence point ids of the frozen version, in document order."""
    evidence = snapshot_question.get("evidence")
    if not isinstance(evidence, Mapping):
        return ()
    ids: list[str] = []
    for part in evidence.get("parts") or ():
        if not isinstance(part, Mapping):
            continue
        for point in part.get("evidence_points") or ():
            if not isinstance(point, Mapping):
                continue
            point_id = str(point.get("evidence_point_id") or "")
            if point_id:
                ids.append(point_id)
    return tuple(ids)


def part_point_ids(
    snapshot_question: Mapping[str, Any],
    part_id: str,
) -> tuple[str, ...]:
    """Evidence point ids of one frozen part."""
    evidence = snapshot_question.get("evidence")
    if not isinstance(evidence, Mapping):
        return ()
    for part in evidence.get("parts") or ():
        if not isinstance(part, Mapping):
            continue
        if str(part.get("part_id") or "") != str(part_id):
            continue
        return tuple(
            str(point.get("evidence_point_id") or "")
            for point in part.get("evidence_points") or ()
            if isinstance(point, Mapping)
            and str(point.get("evidence_point_id") or "")
        )
    return ()


__all__ = [
    "SNAPSHOT_SCHEMA_VERSION",
    "annotate_rubric_payload",
    "annotate_rubric_with_snapshot",
    "build_session_snapshot",
    "freeze_session_evidence_snapshot",
    "load_snapshot",
    "part_point_ids",
    "question_point_ids",
    "resolved_direct_keys",
    "snapshot_file_name",
    "snapshot_path",
    "validate_rubric_evidence_coverage",
    "write_snapshot",
]
