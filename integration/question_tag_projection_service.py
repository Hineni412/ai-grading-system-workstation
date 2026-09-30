from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.config_generation.contract import iter_effective_rubric_item_refs
from question_bank.database.schema import connect, initialize_database
from question_bank.services.source_question_link_service import (
    SourceQuestionLinkService,
)


@dataclass(frozen=True, slots=True)
class ProjectedQuestionTags:
    item_ref: str
    parent_ref: str
    bank_question_id: int | None
    tags: Mapping[str, tuple[str, ...]]
    missing_reason: str = ""
    assessment: Mapping[str, Any] = field(default_factory=dict)
    # Frozen-snapshot steps: {"step_id","step_score","evidence_point_ids"} —
    # consumed with per-student step_assessments for §6.3 step-level mastery.
    steps: tuple[Mapping[str, Any], ...] = ()
    # step_id -> direct stable keys of the step's covered evidence points.
    step_targets: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    evidence_points: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    point_links: Mapping[str, tuple[Mapping[str, Any], ...]] = field(default_factory=dict)

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
        feature_difficulties = self._active_feature_difficulties(bank_question_ids)
        from question_bank.solution_evidence.evidence_snapshot import (
            load_snapshot,
            resolve_evidence_part_id,
            resolved_direct_keys,
        )
        snapshot = load_snapshot(
            self.data_root / "config" / "uploaded",
            grading_session_id,
        )
        snapshot_questions = (
            snapshot.get("questions") if isinstance(snapshot, Mapping) else {}
        )
        resolver = self._knowledge_resolver()

        projected: list[ProjectedQuestionTags] = []
        for item_ref, parent_ref, _question, _item in item_refs:
            bank_question_id = confirmed_links.get(parent_ref)
            tags = tags_by_question.get(bank_question_id, {}) if bank_question_id is not None else {}
            assessment: dict[str, Any] = {"granularity": "whole_question", "reason": "part_assessment_not_prepared"}
            step_point_map: dict[str, tuple[str, ...]] = {}
            item_step_payloads: tuple[dict[str, Any], ...] = ()
            evidence_points: dict[str, Mapping[str, Any]] = {}
            point_links: dict[str, tuple[Mapping[str, Any], ...]] = {}
            if bank_question_id is None:
                missing_reason = "missing_link"
            elif not questions.get(bank_question_id, False):
                missing_reason = "bank_question_missing"
                tags = {}
            elif not tags.get("knowledge_point"):
                missing_reason = "missing_knowledge_point"
            else:
                missing_reason = ""
            snapshot_question = (
                snapshot_questions.get(parent_ref)
                if bank_question_id is not None
                else None
            )
            if (
                isinstance(snapshot_question, Mapping)
                and snapshot_question.get("usable")
            ):
                covered_ids, item_steps = _covered_point_ids(_item)
                evidence_points = {
                    str(point.get("evidence_point_id")): point
                    for part in (snapshot_question.get("evidence") or {}).get("parts", ())
                    for point in part.get("evidence_points", ())
                }
                point_links = {str(pid): tuple(links) for pid, links in snapshot_question.get("links", {}).items()}
                targets = resolved_direct_keys(snapshot_question, covered_ids)
                tags = {**tags, "knowledge_point": targets}
                step_point_map = {
                    str(step.get("step_id") or ""): resolved_direct_keys(
                        snapshot_question,
                        [str(pid) for pid in step.get("evidence_point_ids") or ()],
                    )
                    for step in item_steps
                    if str(step.get("step_id") or "")
                }
                item_part_id = str(
                    _item.get("part_id") or _question.get("question_id")
                )
                evidence_part_id = resolve_evidence_part_id(_item, snapshot_question, item_part_id)
                item_step_payloads = tuple(
                    {
                        "step_id": str(step.get("step_id") or ""),
                        "part_id": item_part_id,
                        "step_score": step.get("step_score"),
                        "evidence_point_ids": [
                            str(pid)
                            for pid in step.get("evidence_point_ids") or ()
                        ],
                    }
                    for step in item_steps
                )
                estimate = _snapshot_part_estimate(
                    snapshot_question,
                    evidence_part_id,
                    feature_difficulties.get(bank_question_id) or {},
                )
                missing_reason = (
                    "part_evidence_point_ids_missing"
                    if not covered_ids
                    else ("part_direct_knowledge_missing" if not targets else "")
                )
                assessment = {
                    "granularity": "part",
                    "part_id": item_part_id,
                    "evidence_part_id": evidence_part_id,
                    "evidence_version_id": snapshot_question.get(
                        "source_evidence_version_id"
                    ),
                    "part_difficulty": estimate.get("difficulty"),
                    "difficulty_source": estimate.get("source", "unknown"),
                    "difficulty_rationale": estimate.get("rationale", ""),
                    # Step contributions already allocate the step score per
                    # target; no extra 1/N split (§6.3).
                    "evidence_weight": 1.0,
                    "reason": missing_reason or "snapshot_evidence_point_attribution",
                }
            elif bank_question_id is not None and questions.get(bank_question_id):
                # 无快照的旧会话兜底：整题 knowledge_point 标签上溯到节键。
                fallback = self._section_fallback_keys(
                    tags.get("knowledge_point", ()),
                    resolver,
                )
                if fallback:
                    tags = {**tags, "knowledge_point": fallback}
                    missing_reason = ""
                    assessment = {
                        "granularity": "whole_question",
                        "reason": "legacy_section_fallback",
                    }
            projected.append(
                ProjectedQuestionTags(
                    item_ref=item_ref,
                    parent_ref=parent_ref,
                    bank_question_id=bank_question_id,
                    tags=tags,
                    missing_reason=missing_reason,
                    assessment=assessment,
                    step_targets=step_point_map,
                    steps=item_step_payloads,
                    evidence_points=evidence_points,
                    point_links=point_links,
                )
            )
        return QuestionTagProjection(tuple(projected))

    def _knowledge_resolver(self) -> Any:
        try:
            from question_bank.current_knowledge import CurrentKnowledgeResolver

            return CurrentKnowledgeResolver.from_active_database(self.db_path)
        except Exception:
            return None

    def _section_fallback_keys(
        self,
        tag_values: tuple[str, ...],
        resolver: Any,
    ) -> tuple[str, ...]:
        """整题兜底：把 knowledge_point 标签值解析并上溯到节稳定键。"""
        keys: list[str] = []
        for value in tag_values:
            text = str(value or "").strip()
            if not text:
                continue
            if text.startswith(("sk_", "kp_")):
                keys.append(text)
                continue
            if resolver is None:
                continue
            try:
                resolved = resolver.resolve(text)
            except Exception:
                resolved = ()
            for target in resolved:
                key = str(getattr(target, "stable_key", "") or "")
                if key and key not in keys:
                    keys.append(key)
        if not keys:
            return ()
        from question_bank.solution_evidence.knowledge_links import (
            resolve_anchor_keys,
        )

        with connect(
            self.db_path,
            external_connection=self.external_connection,
        ) as conn:
            return tuple(
                resolve_anchor_keys(conn, keys)["sections"]
            )

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

    def _active_feature_difficulties(
        self,
        question_ids: set[int],
    ) -> dict[int, dict[str, dict[str, Any]]]:
        """当前有效的逐小问公式难度，按 (question_id, part_id) 组织。"""
        if not question_ids:
            return {}
        placeholders = ", ".join("?" for _ in question_ids)
        with connect(
            self.db_path,
            external_connection=self.external_connection,
        ) as conn:
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='question_part_difficulty_features'"
            ).fetchone() is None:
                return {}
            rows = conn.execute(
                f"""
                SELECT question_id, part_id, features_json, formula_difficulty
                FROM question_part_difficulty_features
                WHERE is_active = 1 AND question_id IN ({placeholders})
                """,
                sorted(question_ids),
            ).fetchall()
        result: dict[int, dict[str, dict[str, Any]]] = {}
        for row in rows:
            try:
                payload = json.loads(str(row["features_json"] or "{}"))
            except (TypeError, ValueError):
                payload = {}
            result.setdefault(int(row["question_id"]), {})[str(row["part_id"])] = {
                "difficulty": row["formula_difficulty"],
                "rationale": str(payload.get("evidence") or "")
                if isinstance(payload, Mapping) else "",
            }
        return result


def _covered_point_ids(
    item: Mapping[str, Any],
) -> tuple[list[str], list[Mapping[str, Any]]]:
    """(covered evidence point ids, steps) for one rubric item.

    ``uncovered_evidence_point_ids`` on an ``allow_alternative_methods`` part
    removes points from coverage; a point id may not appear in two steps.
    """
    steps = [
        step
        for step in item.get("steps") or []
        if isinstance(step, Mapping)
    ]
    uncovered = {
        str(pid)
        for pid in item.get("uncovered_evidence_point_ids") or ()
    }
    covered: list[str] = []
    for step in steps:
        for raw in step.get("evidence_point_ids") or ():
            point_id = str(raw)
            if point_id and point_id not in uncovered and point_id not in covered:
                covered.append(point_id)
    return covered, steps


def _snapshot_part_estimate(
    snapshot_question: Mapping[str, Any],
    part_id: str,
    features: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any]:
    """历史考试的小问难度读当前公式分，不写回已冻结的快照。

    只有当该题当前有效的特征 part_id 集合与快照证据的 part_id 集合
    完全一致时才采用公式难度；否则按未知处理。
    """
    evidence_part_ids = {
        str(part.get("part_id") or "")
        for part in (snapshot_question.get("evidence") or {}).get("parts", ())
        if isinstance(part, Mapping)
    }
    if features and set(features) == evidence_part_ids:
        feature = features.get(part_id)
        if feature is not None:
            return {
                "difficulty": feature.get("difficulty"),
                "source": "formula",
                "rationale": str(feature.get("rationale") or ""),
            }
    return {"difficulty": None, "source": "unknown", "rationale": ""}


__all__ = [
    "ProjectedQuestionTags",
    "QuestionTagProjection",
    "QuestionTagProjectionService",
]
