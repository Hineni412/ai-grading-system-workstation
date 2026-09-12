from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from question_bank.database.schema import connect, initialize_database
from question_bank.services.source_question_link_service import SourceQuestionLinkService
from session_manager import iter_effective_rubric_item_refs


@dataclass(frozen=True, slots=True)
class ProjectedQuestionTags:
    item_ref: str
    parent_ref: str
    bank_question_id: int | None
    tags: Mapping[str, tuple[str, ...]]
    missing_reason: str = ""
    assessment: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_graph_eligible(self) -> bool:
        return bool(self.bank_question_id and self.tags.get("knowledge_point"))


@dataclass(frozen=True, slots=True)
class QuestionTagProjection:
    items: tuple[ProjectedQuestionTags, ...]

    @property
    def total_items(self) -> int:
        return len(self.items)

    @property
    def covered_items(self) -> int:
        return sum(item.is_graph_eligible for item in self.items)

    @property
    def missing_items(self) -> dict[str, str]:
        return {
            item.item_ref: item.missing_reason
            for item in self.items
            if item.missing_reason
        }

    def context_by_item(self) -> dict[str, dict[str, list[str]]]:
        return {
            item.item_ref: {
                tag_type: list(values)
                for tag_type, values in item.tags.items()
            }
            for item in self.items
            if item.bank_question_id is not None and item.tags
        }


class QuestionTagProjectionService:
    def __init__(
        self,
        question_bank_db_path: str | Path,
        *,
        external_connection: sqlite3.Connection | None = None,
        data_root: Path | None = None,
    ) -> None:
        self.db_path = Path(question_bank_db_path)
        self.external_connection = external_connection
        self.data_root = Path(data_root) if data_root is not None else self.db_path.parent.parent

    def project_session(
        self,
        *,
        grading_session_id: str | int,
        rubric: Mapping[str, Any],
    ) -> QuestionTagProjection:
        if self.external_connection is None:
            initialize_database(self.db_path)
        confirmed_links = {
            str(item["source_question_id"]): int(item["bank_question_id"])
            for item in SourceQuestionLinkService(
                self.db_path,
                external_connection=self.external_connection,
            ).list_links(grading_session_id)
            if item.get("status") == "confirmed"
        }
        item_refs = list(iter_effective_rubric_item_refs(rubric))
        bank_question_ids = {
            confirmed_links[parent_ref]
            for _item_ref, parent_ref, _question, _item in item_refs
            if parent_ref in confirmed_links
        }
        questions, tags_by_question = self._load_questions_and_tags(bank_question_ids)
        from question_bank.solution_evidence.part_assessments import load_profiles, match_rubric_parts, direct_targets, historical_source_matches
        profiles = load_profiles(self.db_path, sorted(bank_question_ids), connection=self.external_connection, data_root=self.data_root)

        projected: list[ProjectedQuestionTags] = []
        for item_ref, parent_ref, _question, _item in item_refs:
            bank_question_id = confirmed_links.get(parent_ref)
            tags = tags_by_question.get(bank_question_id, {}) if bank_question_id is not None else {}
            assessment: dict[str, Any] = {"granularity": "whole_question", "reason": "part_assessment_not_prepared"}
            if bank_question_id is None:
                missing_reason = "missing_link"
            elif not questions.get(bank_question_id, False):
                missing_reason = "bank_question_missing"
                tags = {}
            elif not tags.get("knowledge_point"):
                missing_reason = "missing_knowledge_point"
            else:
                missing_reason = ""
            profile = profiles.get(bank_question_id)
            if profile is not None:
                matched = match_rubric_parts(_question, profile["evidence"]) if profile["available"] and historical_source_matches(self.db_path, profile, _question, self.external_connection, session_id=grading_session_id, data_root=self.data_root) else {}
                part = matched.get(str(_item.get("part_id") or _question.get("question_id")))
                targets = direct_targets(part) if part else ()
                tags = {**tags, "knowledge_point": targets}
                missing_reason = (profile.get("reason") or "part_source_not_matched") if not part else ("part_direct_knowledge_missing" if not targets else "")
                estimate = next((value for value in profile["parts"] if part and value["part_id"] == part["part_id"]), {})
                assessment = {"granularity": "part", "part_id": part["part_id"] if part else None,
                              "profile_id": profile["profile_id"], "revision": profile["revision"],
                              "evidence_version_id": profile["evidence_version_id"],
                              "part_difficulty": estimate.get("difficulty"),
                              "difficulty_source": estimate.get("source", "unknown"),
                              "difficulty_rationale": estimate.get("rationale", ""),
                              "evidence_weight": 1.0 / len(targets) if targets else 1.0,
                              "reason": missing_reason or "part_composite_attribution_limited"}
            projected.append(
                ProjectedQuestionTags(
                    item_ref=item_ref,
                    parent_ref=parent_ref,
                    bank_question_id=bank_question_id,
                    tags=tags,
                    missing_reason=missing_reason,
                    assessment=assessment,
                )
            )
        return QuestionTagProjection(tuple(projected))

    def _load_questions_and_tags(
        self,
        question_ids: set[int],
    ) -> tuple[dict[int, bool], dict[int, dict[str, tuple[str, ...]]]]:
        if not question_ids:
            return {}, {}
        placeholders = ", ".join("?" for _ in question_ids)
        ordered_ids = sorted(question_ids)
        with connect(
            self.db_path,
            external_connection=self.external_connection,
        ) as conn:
            question_rows = conn.execute(
                f"SELECT id, is_deleted FROM questions WHERE id IN ({placeholders})",
                ordered_ids,
            ).fetchall()
            tag_rows = conn.execute(
                f"""
                SELECT question_id, tag_type, tag_value
                FROM question_tags
                WHERE question_id IN ({placeholders})
                ORDER BY id
                """,
                ordered_ids,
            ).fetchall()

        questions = {
            int(row["id"]): not bool(row["is_deleted"])
            for row in question_rows
        }
        mutable_tags: dict[int, dict[str, list[str]]] = {}
        for row in tag_rows:
            question_id = int(row["question_id"])
            tag_type = str(row["tag_type"] or "").strip()
            tag_value = str(row["tag_value"] or "").strip()
            if not tag_type or not tag_value:
                continue
            values = mutable_tags.setdefault(question_id, {}).setdefault(tag_type, [])
            if tag_value not in values:
                values.append(tag_value)
        tags_by_question = {
            question_id: {
                tag_type: tuple(values)
                for tag_type, values in tags.items()
            }
            for question_id, tags in mutable_tags.items()
        }
        return questions, tags_by_question


__all__ = [
    "ProjectedQuestionTags",
    "QuestionTagProjection",
    "QuestionTagProjectionService",
]
