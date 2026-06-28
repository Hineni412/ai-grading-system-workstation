from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.models.knowledge_alignment import normalize_source_value
from question_bank.services.concept_alignment_service import ConceptAlignmentService


@dataclass(frozen=True, slots=True)
class AlignmentFocusItem:
    source_namespace: str
    source_value: str
    display_value: str
    evidence_count: int
    student_ids: tuple[str, ...]
    session_ids: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AlignmentReviewService:
    def __init__(
        self,
        grading_db_path: str | Path,
        question_bank_db_path: str | Path,
    ) -> None:
        self.diagnosis = DiagnosisProfileService(
            grading_db_path,
            question_bank_db_path,
        )
        self.alignment = ConceptAlignmentService(question_bank_db_path)

    def confirm_and_rebuild(
        self,
        *,
        scope: Mapping[str, Any],
        exam_scope: Mapping[str, Any],
        source_value: str,
        concept_id: int,
    ) -> dict[str, Any]:
        self.alignment.confirm_mapping(
            "grading_weak_point",
            source_value,
            concept_id,
            sub_skill_tags=[source_value],
            reviewed_by="teacher",
        )
        return self.diagnosis.build_legacy_profiles(
            scope=scope,
            exam_scope=exam_scope,
        )


def focus_items_from_diagnosis(
    diagnosis: Mapping[str, Any],
) -> list[AlignmentFocusItem]:
    exam_scope = diagnosis.get("exam_scope") or {}
    session_ids = tuple(
        sorted({int(value) for value in exam_scope.get("session_ids", [])})
    )
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for student in diagnosis.get("students", []):
        student_id = str(student.get("student_id") or "")
        for weak in student.get("weak_points", []):
            if str(weak.get("review_state") or "") == "本次不推荐":
                continue
            if str(weak.get("mapping_status") or "") in {
                "confirmed",
                "rejected",
            }:
                continue
            source_value = str(weak.get("source_term") or "").strip()
            if not source_value:
                continue
            key = (
                "grading_weak_point",
                normalize_source_value(source_value),
            )
            row = grouped.setdefault(
                key,
                {
                    "source_value": source_value,
                    "display_value": str(
                        weak.get("source_display") or source_value
                    ),
                    "evidence_count": 0,
                    "student_ids": set(),
                },
            )
            row["evidence_count"] += int(weak.get("evidence_count") or 0)
            if student_id:
                row["student_ids"].add(student_id)
    return [
        AlignmentFocusItem(
            source_namespace=namespace,
            source_value=row["source_value"],
            display_value=row["display_value"],
            evidence_count=row["evidence_count"],
            student_ids=tuple(sorted(row["student_ids"])),
            session_ids=session_ids,
        )
        for (namespace, _), row in sorted(grouped.items())
    ]


def _focus_key(namespace: object, value: object) -> tuple[str, str]:
    return normalize_source_value(namespace), normalize_source_value(value)


def merge_focus_sources(
    source_terms: Iterable[Mapping[str, Any]],
    focus_items: Iterable[AlignmentFocusItem],
) -> list[dict[str, Any]]:
    result = [dict(item) for item in source_terms]
    existing = {
        _focus_key(item.get("source_namespace"), item.get("source_value"))
        for item in result
    }
    for item in focus_items:
        key = _focus_key(item.source_namespace, item.source_value)
        if key in existing:
            continue
        result.append(
            {
                "source_namespace": item.source_namespace,
                "source_value": item.source_value,
                "display_value": item.display_value,
                "evidence_count": item.evidence_count,
            }
        )
        existing.add(key)
    return result


def filter_focus_sources(
    source_terms: Iterable[Mapping[str, Any]],
    focus_items: Iterable[AlignmentFocusItem],
) -> list[dict[str, Any]]:
    allowed = {
        _focus_key(item.source_namespace, item.source_value)
        for item in focus_items
    }
    return [
        dict(item)
        for item in source_terms
        if _focus_key(
            item.get("source_namespace"),
            item.get("source_value"),
        )
        in allowed
    ]


def apply_scope_exclusions(
    diagnosis: Mapping[str, Any],
    excluded_terms: set[str],
) -> dict[str, Any]:
    result = deepcopy(dict(diagnosis))
    for student in result.get("students", []):
        for weak in student.get("weak_points", []):
            if str(weak.get("source_term") or "") in excluded_terms:
                weak["eligible_for_recommendation"] = False
                weak["review_state"] = "本次不推荐"
    result["confirmed_concept_ids"] = sorted(
        {
            int(weak["concept_id"])
            for student in result.get("students", [])
            for weak in student.get("weak_points", [])
            if weak.get("eligible_for_recommendation")
            and weak.get("concept_id") is not None
        }
    )
    return result


__all__ = [
    "AlignmentFocusItem",
    "AlignmentReviewService",
    "apply_scope_exclusions",
    "filter_focus_sources",
    "focus_items_from_diagnosis",
    "merge_focus_sources",
]
