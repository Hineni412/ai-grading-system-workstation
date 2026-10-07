from __future__ import annotations

import json
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from question_bank.canonical_hash import (
    canonical_hash as _hash_payload,
    canonical_json as _canonical_json,
)
from question_bank.database.schema import connect
from question_bank.knowledge_graph_release.contracts import stable_record_hash
from question_bank.knowledge_graph_release.repository import active_release_id
from question_bank.solution_evidence.contracts import (
    CoreResolution,
    FineTermResolver,
    QuestionSolutionEvidence,
    validate_evidence_fine_terms,
)
from question_bank.solution_evidence.convergence import converge_evidence_terms
from question_bank.solution_evidence.normalization import (
    normalize_model_solution_evidence,
)

if TYPE_CHECKING:
    from question_bank.training_criteria.analysis import QuestionAnalysisInput


MappingSourceKind = Literal["builtin", "synthetic", "model", "teacher", "import"]
MappingActorKind = Literal["system", "teacher", "import"]


class FineTermCoreMappingRepository:
    """Govern fine-term mappings; model proposals are never active by default."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def resolve(self, fine_term_id: str) -> CoreResolution:
        term_id = _required_text(fine_term_id, "fine_term_id")
        with connect(self.db_path) as connection:
            confirmed = connection.execute(
                """
                SELECT mapping.stable_key
                FROM fine_term_core_mappings mapping
                JOIN knowledge_tag_identities identity
                  ON identity.stable_key = mapping.stable_key
                WHERE mapping.fine_term_id = ?
                  AND mapping.status = 'confirmed'
                  AND identity.status = 'active'
                ORDER BY mapping.stable_key
                """,
                (term_id,),
            ).fetchall()
            if confirmed:
                return CoreResolution(
                    status="resolved",
                    stable_keys=tuple(str(row["stable_key"]) for row in confirmed),
                    reason="governed_confirmed_mapping",
                )
            candidates = connection.execute(
                """
                SELECT mapping.stable_key
                FROM fine_term_core_mappings mapping
                JOIN knowledge_tag_identities identity
                  ON identity.stable_key = mapping.stable_key
                WHERE mapping.fine_term_id = ?
                  AND mapping.status = 'suggested'
                  AND identity.status = 'active'
                ORDER BY mapping.stable_key
                """,
                (term_id,),
            ).fetchall()
        if candidates:
            return CoreResolution(
                status="ambiguous",
                stable_keys=tuple(str(row["stable_key"]) for row in candidates),
                reason="unconfirmed_mapping_candidates",
            )
        return CoreResolution(status="unmapped", reason="no_governed_mapping")

    def propose(
        self,
        *,
        fine_term_id: str,
        stable_key: str,
        source_kind: MappingSourceKind,
        source_reference: str,
        rationale: str,
        model_name: str = "",
        model_version: str = "",
    ) -> dict[str, Any]:
        term_id = _required_text(fine_term_id, "fine_term_id")
        key = _stable_key(stable_key)
        source = _source_kind(source_kind)
        reference = _required_text(source_reference, "source_reference")
        reason = _required_text(rationale, "rationale")
        clean_model_name = str(model_name or "").strip()
        clean_model_version = str(model_version or "").strip()
        if source == "model" and (not clean_model_name or not clean_model_version):
            raise ValueError("model mapping proposals require model identity")
        mapping_id = _hash_payload({"fine_term_id": term_id, "stable_key": key})
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            _require_active_identity(connection, key)
            existing = connection.execute(
                "SELECT * FROM fine_term_core_mappings WHERE mapping_id = ?",
                (mapping_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["fine_term_id"]) != term_id
                    or str(existing["stable_key"]) != key
                ):
                    raise RuntimeError("mapping identifier collision")
                return _mapping_row(existing)
            connection.execute(
                """
                INSERT INTO fine_term_core_mappings (
                    mapping_id, fine_term_id, stable_key, status,
                    source_kind, source_reference, rationale,
                    model_name, model_version
                ) VALUES (?, ?, ?, 'suggested', ?, ?, ?, ?, ?)
                """,
                (
                    mapping_id,
                    term_id,
                    key,
                    source,
                    reference,
                    reason,
                    clean_model_name or None,
                    clean_model_version or None,
                ),
            )
            _mapping_event(
                connection,
                mapping_id=mapping_id,
                event_type="suggested",
                from_status=None,
                to_status="suggested",
                actor_kind=("system" if source in {"builtin", "synthetic"} else source),
                actor_ref=reference,
                reason=reason,
                expected_revision=0,
                resulting_revision=1,
            )
            row = connection.execute(
                "SELECT * FROM fine_term_core_mappings WHERE mapping_id = ?",
                (mapping_id,),
            ).fetchone()
        assert row is not None
        return _mapping_row(row)

    def confirm(
        self,
        mapping_id: str,
        *,
        expected_revision: int,
        actor_kind: MappingActorKind,
        actor_ref: str,
        reason: str,
    ) -> dict[str, Any]:
        clean_id = _hash_text(mapping_id, "mapping_id")
        actor = str(actor_kind or "").strip().casefold()
        if actor not in {"system", "teacher", "import"}:
            raise ValueError("only governed actors can confirm a mapping")
        actor_reference = _required_text(actor_ref, "actor_ref")
        decision_reason = _required_text(reason, "reason")
        revision = int(expected_revision)
        if revision < 1:
            raise ValueError("expected_revision must be positive")
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM fine_term_core_mappings WHERE mapping_id = ?",
                (clean_id,),
            ).fetchone()
            if row is None:
                raise KeyError(clean_id)
            current_revision = int(row["revision"])
            if current_revision != revision:
                raise RuntimeError("fine-term mapping revision changed")
            current_status = str(row["status"])
            if current_status == "confirmed":
                return _mapping_row(row)
            if current_status != "suggested":
                raise RuntimeError("only suggested mappings can be confirmed")
            next_revision = current_revision + 1
            connection.execute(
                """
                UPDATE fine_term_core_mappings
                SET status = 'confirmed', decision_by = ?, decision_note = ?,
                    decided_at = datetime('now','localtime'), revision = ?,
                    updated_at = datetime('now','localtime')
                WHERE mapping_id = ? AND revision = ? AND status = 'suggested'
                """,
                (
                    actor_reference,
                    decision_reason,
                    next_revision,
                    clean_id,
                    current_revision,
                ),
            )
            _mapping_event(
                connection,
                mapping_id=clean_id,
                event_type="confirmed",
                from_status="suggested",
                to_status="confirmed",
                actor_kind=actor,
                actor_ref=actor_reference,
                reason=decision_reason,
                expected_revision=current_revision,
                resulting_revision=next_revision,
            )
            updated = connection.execute(
                "SELECT * FROM fine_term_core_mappings WHERE mapping_id = ?",
                (clean_id,),
            ).fetchone()
        assert updated is not None
        return _mapping_row(updated)

    def install_synthetic_mappings(
        self,
        mappings: Mapping[str, Sequence[str]],
        *,
        source_reference: str,
        actor_ref: str,
    ) -> int:
        """Install reviewed deterministic mappings; never upgrades model rows."""

        reference = _required_text(source_reference, "source_reference")
        actor = _required_text(actor_ref, "actor_ref")
        normalized = [
            (_required_text(term_id, "fine_term_id"), _stable_key(stable_key))
            for term_id, stable_keys in mappings.items()
            for stable_key in stable_keys
        ]
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            active_release = connection.execute(
                """
                SELECT 1
                FROM knowledge_graph_releases
                WHERE status = 'active'
                LIMIT 1
                """
            ).fetchone()
            if active_release is not None:
                return 0
            installed = 0
            for term_id, key in normalized:
                _require_active_identity(connection, key)
                mapping_id = _hash_payload(
                    {"fine_term_id": term_id, "stable_key": key}
                )
                existing = connection.execute(
                    """
                    SELECT mapping_id, source_kind, status, revision
                    FROM fine_term_core_mappings
                    WHERE fine_term_id = ? AND stable_key = ?
                    """,
                    (term_id, key),
                ).fetchone()
                if existing is not None:
                    mapping_id = str(existing["mapping_id"])
                    if str(existing["status"]) == "confirmed":
                        continue
                    if str(existing["source_kind"]) not in {"builtin", "synthetic"}:
                        continue
                    if str(existing["status"]) != "suggested":
                        continue
                    current_revision = int(existing["revision"])
                    connection.execute(
                        """
                        UPDATE fine_term_core_mappings
                        SET status = 'confirmed', decision_by = ?,
                            decision_note = 'reviewed synthetic mapping',
                            decided_at = datetime('now','localtime'),
                            revision = revision + 1,
                            updated_at = datetime('now','localtime')
                        WHERE mapping_id = ? AND status = 'suggested'
                          AND revision = ?
                        """,
                        (actor, mapping_id, current_revision),
                    )
                    _mapping_event(
                        connection,
                        mapping_id=mapping_id,
                        event_type="confirmed",
                        from_status="suggested",
                        to_status="confirmed",
                        actor_kind="system",
                        actor_ref=actor,
                        reason="reviewed synthetic mapping",
                        expected_revision=current_revision,
                        resulting_revision=current_revision + 1,
                    )
                    installed += 1
                    continue
                connection.execute(
                    """
                    INSERT INTO fine_term_core_mappings (
                        mapping_id, fine_term_id, stable_key, status,
                        source_kind, source_reference, rationale,
                        decision_by, decision_note, decided_at
                    ) VALUES (?, ?, ?, 'confirmed', 'synthetic', ?, ?, ?, ?,
                              datetime('now','localtime'))
                    """,
                    (
                        mapping_id,
                        term_id,
                        key,
                        reference,
                        "reviewed deterministic fine-term mapping",
                        actor,
                        "reviewed synthetic mapping",
                    ),
                )
                _mapping_event(
                    connection,
                    mapping_id=mapping_id,
                    event_type="confirmed",
                    from_status=None,
                    to_status="confirmed",
                    actor_kind="system",
                    actor_ref=actor,
                    reason="reviewed synthetic mapping",
                    expected_revision=0,
                    resulting_revision=1,
                )
                installed += 1
        return installed


class SolutionEvidenceRepository:
    """Store immutable evidence versions without changing approved criteria."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def save(
        self,
        evidence: QuestionSolutionEvidence,
        *,
        source_kind: Literal["combined_model", "teacher_manual", "import", "backfill"],
        source_reference: str,
        created_by: str,
        graph_release_id: str | None = None,
    ) -> str:
        source = str(source_kind or "").strip().casefold()
        if source not in {"combined_model", "teacher_manual", "import", "backfill"}:
            raise ValueError("solution evidence source_kind is invalid")
        reference = _required_text(source_reference, "source_reference")
        actor = _required_text(created_by, "created_by")
        graph_id = str(graph_release_id or "").strip() or None
        storage_version_id = (
            stable_record_hash(
                "solution-evidence-graph-version",
                evidence.version_id,
                graph_id,
            )
            if graph_id is not None
            else evidence.version_id
        )
        stored_payload = evidence.to_dict()
        # Whole-question classification is a read-only projection, never a
        # separately editable source of truth.
        stored_payload.pop("whole_question_classification", None)
        payload_json = _canonical_json(stored_payload)
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_reference = connection.execute(
                """
                SELECT evidence_version_id, content_hash, source_content_hash,
                       graph_release_id
                FROM question_solution_evidence_versions
                WHERE question_id = ? AND source_kind = ? AND source_reference = ?
                """,
                (evidence.question_id, source, reference),
            ).fetchone()
            if existing_reference is not None:
                if (
                    str(existing_reference["content_hash"]) != evidence.content_hash
                    or str(existing_reference["source_content_hash"])
                    != evidence.source_content_hash
                    or str(existing_reference["graph_release_id"] or "")
                    != str(graph_id or "")
                ):
                    raise RuntimeError("solution evidence source reference was reused")
                return str(existing_reference["evidence_version_id"])
            existing_version = connection.execute(
                """
                SELECT content_hash FROM question_solution_evidence_versions
                WHERE evidence_version_id = ?
                """,
                (storage_version_id,),
            ).fetchone()
            if existing_version is not None:
                if str(existing_version["content_hash"]) != evidence.content_hash:
                    raise RuntimeError("solution evidence version collision")
                return storage_version_id
            connection.execute(
                """
                INSERT INTO question_solution_evidence_versions (
                    evidence_version_id, question_id, source_content_hash,
                    schema_version, content_hash, evidence_json, status,
                    source_kind, source_reference, created_by,
                    graph_release_id
                ) VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?)
                """,
                (
                    storage_version_id,
                    evidence.question_id,
                    evidence.source_content_hash,
                    evidence.schema_version,
                    evidence.content_hash,
                    payload_json,
                    source,
                    reference,
                    actor,
                    graph_id,
                ),
            )
            from question_bank.solution_evidence.knowledge_links import (
                project_embedded_links,
                refresh_question_scope_summary,
            )
            project_embedded_links(
                connection,
                evidence_version_id=storage_version_id,
                question_id=evidence.question_id,
                evidence_payload=stored_payload,
                default_release_id=graph_id,
            )
            refresh_question_scope_summary(connection, evidence.question_id)
        return storage_version_id

    def has_current(self, question_id: int, source_content_hash: str) -> bool:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT 1 FROM question_solution_evidence_versions
                WHERE question_id = ? AND source_content_hash = ?
                  AND status IN ('proposed', 'approved')
                LIMIT 1
                """,
                (int(question_id), str(source_content_hash or "").strip().casefold()),
            ).fetchone()
        return row is not None

    def latest(
        self,
        question_id: int,
        *,
        current_source_content_hash: str | None = None,
        compatible_source_hashes: Sequence[str] = (),
    ) -> dict[str, Any] | None:
        from question_bank.solution_evidence.part_assessments import reading
        with reading(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM question_solution_evidence_versions
                WHERE question_id = ?
                ORDER BY status = 'approved' DESC, created_at DESC, rowid DESC
                """,
                (int(question_id),),
            ).fetchall()
        if not rows:
            return None
        current_hash = str(current_source_content_hash or "").strip().casefold()
        compatible = {current_hash, *compatible_source_hashes}
        row = next((item for item in rows if current_hash
                    and item["status"] in {"proposed", "approved"}
                    and str(item["source_content_hash"]) in compatible), rows[0])
        stored_hash = str(row["source_content_hash"])
        if current_hash and stored_hash not in compatible:
            return {
                "evidence_version_id": str(row["evidence_version_id"]),
                "question_id": int(row["question_id"]),
                "source_content_hash": stored_hash,
                "content_hash": str(row["content_hash"]),
                "status": "stale",
                "graph_release_id": str(row["graph_release_id"] or ""),
                "evidence": None,
            }
        evidence_payload = json.loads(str(row["evidence_json"]))
        evidence_payload["whole_question_classification"] = (
            _classification_from_evidence_payload(evidence_payload)
        )
        return {
            "evidence_version_id": str(row["evidence_version_id"]),
            "question_id": int(row["question_id"]),
            "source_content_hash": str(row["source_content_hash"]),
            "content_hash": str(row["content_hash"]),
            "status": str(row["status"]),
            "graph_release_id": str(row["graph_release_id"] or ""),
            "evidence": evidence_payload,
        }

    def load_current(
        self,
        question_id: int,
        *,
        source_content_hash: str,
        resolver: FineTermResolver,
    ) -> QuestionSolutionEvidence | None:
        """Decode the current saved evidence for a projection-only retry.

        A tag or training-point retry must be able to reuse an already saved
        model result.  The database payload contains a read-only
        ``core_resolution`` decoration on links; remove that decoration before
        passing the payload back through the strict model contract.
        """

        compatible: frozenset[str] = frozenset()
        from question_bank.solution_evidence.part_assessments import current_inputs, reading
        from question_bank.training_criteria.analysis import compatible_source_content_hashes
        with reading(self.db_path) as connection:
            inputs = current_inputs(self.db_path, [int(question_id)], connection, load_failures={})
        current = inputs.get(int(question_id))
        if current is not None and current.criterion_source_content_hash == source_content_hash:
            compatible = compatible_source_content_hashes(current, kind="solution_evidence")
        latest = self.latest(
            question_id,
            current_source_content_hash=source_content_hash,
            compatible_source_hashes=compatible,
        )
        if not isinstance(latest, dict) or latest.get("status") not in {
            "proposed",
            "approved",
        }:
            return None
        payload = latest.get("evidence")
        if not isinstance(payload, dict):
            return None
        clean_payload = _model_evidence_payload(payload)
        from question_bank.solution_evidence.contracts import (
            QuestionSolutionEvidence,
        )

        return QuestionSolutionEvidence.from_model_dict(
            clean_payload,
            question_id=int(question_id),
            source_content_hash=str(source_content_hash),
            resolver=resolver,
        )



class SolutionEvidenceProjectionWriter:
    """Resolve governed cores and persist one combined-model evidence draft."""

    def __init__(
        self,
        *,
        mapping_repository: FineTermResolver,
        evidence_repository: SolutionEvidenceRepository,
        taxonomy_governance: Any | None = None,
    ) -> None:
        self.mapping_repository = mapping_repository
        self.evidence_repository = evidence_repository
        self.taxonomy_governance = taxonomy_governance
        self._audit_lock = threading.Lock()
        self._audits: dict[tuple[str, int], dict[str, Any]] = {}

    def load_current(
        self,
        question: QuestionAnalysisInput,
    ) -> QuestionSolutionEvidence | None:
        """Load the evidence matching this exact analysis input."""

        from question_bank.training_criteria.analysis import (
            solution_evidence_source_content_hash,
        )

        return self.evidence_repository.load_current(
            question.question_id,
            source_content_hash=solution_evidence_source_content_hash(question),
            resolver=self.mapping_repository,
        )

    def write(
        self,
        question: QuestionAnalysisInput,
        payload: Mapping[str, Any],
        *,
        model_name: str,
        operation_id: str,
        objective_response_shape: str | None = None,
    ) -> QuestionSolutionEvidence:
        from question_bank.training_criteria.analysis import (
            solution_evidence_source_content_hash,
        )

        # 题型建议被采纳时由调用方传入真实作答形态，解除本地误判的客观约束。
        response_shape = str(
            objective_response_shape or question.objective_response_shape
        )
        normalization = normalize_model_solution_evidence(
            payload,
            question_id=question.question_id,
            question_type=question.question_type_group,
            taxonomy_contract=question.taxonomy_snapshot,
            question_type_confirmed=question.question_type_confirmed,
            expected_part_count=(
                len(question.explicit_part_labels)
                if question.explicit_part_labels
                else None
            ),
            objective_response_shape=response_shape,
            expected_answer=question.tagging_context.answer_text,
        )
        normalized_payload: Mapping[str, Any] = normalization.payload
        additional_allowed: Sequence[str] = ()
        audit_key = (str(operation_id), question.question_id)
        with self._audit_lock:
            self._audits[audit_key] = {
                "normalization_notes": list(normalization.notes),
                "criteria_review_required": normalization.requires_review,
            }
        if self.taxonomy_governance is not None:
            convergence = converge_evidence_terms(
                normalized_payload,
                taxonomy_contract=question.taxonomy_snapshot,
                governance=self.taxonomy_governance,
                question_ref=str(question.question_id),
                model_name=model_name,
                operation_id=operation_id,
                persist_proposals=True,
            )
            normalized_payload = convergence.payload
            additional_allowed = convergence.canonical_term_ids
            with self._audit_lock:
                audit = dict(self._audits.get(audit_key) or {})
                audit.update(convergence.audit_dict())
                self._audits[audit_key] = audit
        evidence = QuestionSolutionEvidence.from_model_dict(
            normalized_payload,
            question_id=question.question_id,
            source_content_hash=solution_evidence_source_content_hash(question),
            resolver=self.mapping_repository,
        )
        validate_evidence_fine_terms(
            evidence,
            question.taxonomy_snapshot,
            additional_allowed_term_ids=additional_allowed,
        )
        requested_graph_release_id = (
            question.taxonomy_snapshot.knowledge_graph_release_id
        )
        active_graph_release_id = ""
        if requested_graph_release_id:
            db_path = getattr(self.evidence_repository, "db_path", None)
            if db_path is not None:
                active_graph_release_id = active_release_id(Path(db_path)) or ""
        self.evidence_repository.save(
            evidence,
            source_kind="combined_model",
            source_reference=f"analysis:{operation_id}:{question.question_id}",
            created_by=f"model:{str(model_name or 'unknown').strip()}",
            graph_release_id=(
                requested_graph_release_id
                if requested_graph_release_id == active_graph_release_id
                else None
            ),
        )
        return evidence

    def criterion_review_required(
        self,
        operation_id: str,
        question_id: int,
    ) -> bool:
        with self._audit_lock:
            audit = self._audits.get((str(operation_id), int(question_id))) or {}
            return audit.get("criteria_review_required") is True

    def audit_summary(
        self,
        operation_id: str,
        question_ids: Sequence[int],
    ) -> dict[str, Any]:
        retrieval_misses: list[dict[str, Any]] = []
        proposals: list[dict[str, Any]] = []
        secondary_matches: list[dict[str, Any]] = []
        unresolved_links: list[dict[str, Any]] = []
        missing_link_points: list[dict[str, Any]] = []
        retrieval_question_ids: list[int] = []
        proposal_question_ids: list[int] = []
        with self._audit_lock:
            rows = [
                (
                    int(question_id),
                    self._audits.get((str(operation_id), int(question_id))),
                )
                for question_id in question_ids
            ]
        for question_id, audit in rows:
            if not isinstance(audit, Mapping):
                continue
            misses = [
                dict(item)
                for item in audit.get("retrieval_misses", [])
                if isinstance(item, Mapping)
            ]
            observed = [
                dict(item)
                for item in audit.get("proposals", [])
                if isinstance(item, Mapping)
            ]
            retrieval_misses.extend(misses)
            proposals.extend(observed)
            secondary_matches.extend(
                dict(item)
                for item in audit.get("secondary_matches", [])
                if isinstance(item, Mapping)
            )
            unresolved_links.extend(
                dict(item)
                for item in audit.get("unresolved_links", [])
                if isinstance(item, Mapping)
            )
            missing_link_points.extend(
                dict(item)
                for item in audit.get("missing_link_points", [])
                if isinstance(item, Mapping)
            )
            if misses:
                retrieval_question_ids.append(question_id)
            if observed:
                proposal_question_ids.append(question_id)
        return {
            "retrieval_misses": retrieval_misses,
            "proposals": proposals,
            "secondary_matches": secondary_matches,
            "unresolved_links": unresolved_links,
            "missing_link_points": missing_link_points,
            "retrieval_miss_question_ids": retrieval_question_ids,
            "proposal_question_ids": proposal_question_ids,
        }


def load_evidence_parts_for_tagging(
    db_path: Path,
    question_ids: Sequence[int],
    *,
    connection: Any = None,
) -> dict[int, list[dict[str, Any]]]:
    """每题最新可用判定点版本的小问清单（供整题打标签的 part_features 定位）。

    返回 {question_id: [{"part_id", "part_label", "evidence_point_ids"}]}；
    与写侧派生归属使用同一"最新 proposed/approved 版本"口径。
    """

    ids = sorted({int(question_id) for question_id in question_ids if question_id})
    result: dict[int, list[dict[str, Any]]] = {qid: [] for qid in ids}
    if not ids:
        return result
    placeholders = ",".join("?" for _ in ids)
    query = f"""
        SELECT v.question_id, v.evidence_json
        FROM question_solution_evidence_versions v
        JOIN (
            SELECT question_id, MAX(created_at) AS max_created
            FROM question_solution_evidence_versions
            WHERE status IN ('proposed', 'approved')
            GROUP BY question_id
        ) m
          ON m.question_id = v.question_id
         AND m.max_created = v.created_at
        WHERE v.question_id IN ({placeholders})
          AND v.status IN ('proposed', 'approved')
        """
    if connection is not None:
        rows = connection.execute(query, tuple(ids)).fetchall()
    else:
        with connect(Path(db_path)) as conn:
            rows = conn.execute(query, tuple(ids)).fetchall()
    for row in rows:
        try:
            payload = json.loads(str(row["evidence_json"] or "{}"))
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, Mapping):
            continue
        parts: list[dict[str, Any]] = []
        for part in payload.get("parts", []) or []:
            if not isinstance(part, Mapping):
                continue
            part_id = str(part.get("part_id") or "").strip()
            if not part_id:
                continue
            parts.append(
                {
                    "part_id": part_id,
                    "part_label": str(part.get("label") or "").strip(),
                    "evidence_point_ids": [
                        str(point.get("evidence_point_id") or "").strip()
                        for point in part.get("evidence_points", []) or []
                        if isinstance(point, Mapping)
                        and str(point.get("evidence_point_id") or "").strip()
                    ],
                }
            )
        result[int(row["question_id"])] = parts
    return result


def _source_kind(value: object) -> str:
    source = str(value or "").strip().casefold()
    if source not in {"builtin", "synthetic", "model", "teacher", "import"}:
        raise ValueError("mapping source_kind is invalid")
    return source


def _stable_key(value: object) -> str:
    key = _required_text(value, "stable_key").casefold()
    if not key.startswith(("kp_", "ki_", "sk_")):
        raise ValueError("stable_key is invalid")
    return key


def _hash_text(value: object, field_name: str) -> str:
    text = _required_text(value, field_name).casefold()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ValueError(f"{field_name} is invalid")
    return text


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _require_active_identity(connection: Any, stable_key: str) -> None:
    row = connection.execute(
        "SELECT status FROM knowledge_tag_identities WHERE stable_key = ?",
        (stable_key,),
    ).fetchone()
    if row is None or str(row["status"]) != "active":
        raise ValueError("stable_key is not an active governed identity")


def _mapping_row(row: Any) -> dict[str, Any]:
    return {
        "mapping_id": str(row["mapping_id"]),
        "fine_term_id": str(row["fine_term_id"]),
        "stable_key": str(row["stable_key"]),
        "status": str(row["status"]),
        "source_kind": str(row["source_kind"]),
        "source_reference": str(row["source_reference"]),
        "revision": int(row["revision"]),
    }


def _mapping_event(
    connection: Any,
    *,
    mapping_id: str,
    event_type: str,
    from_status: str | None,
    to_status: str,
    actor_kind: str,
    actor_ref: str,
    reason: str,
    expected_revision: int,
    resulting_revision: int,
) -> None:
    connection.execute(
        """
        INSERT INTO fine_term_core_mapping_events (
            mapping_id, event_type, from_status, to_status,
            actor_kind, actor_ref, reason, expected_revision,
            resulting_revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mapping_id,
            event_type,
            from_status,
            to_status,
            actor_kind,
            actor_ref,
            reason,
            int(expected_revision),
            int(resulting_revision),
        ),
    )




def _model_evidence_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Strip repository-only link resolution fields from a stored payload."""

    clean = json.loads(json.dumps(dict(payload), ensure_ascii=False))
    for key in ('whole_question_classification', 'source_content_hash', 'content_hash', 'version_id'):
        clean.pop(key, None)
    parts = clean.get("parts")
    if not isinstance(parts, list):
        return clean
    for part in parts:
        if not isinstance(part, dict):
            continue
        points = part.get("evidence_points")
        if not isinstance(points, list):
            continue
        for point in points:
            if not isinstance(point, dict):
                continue
            links = point.get("fine_term_links")
            if not isinstance(links, list):
                continue
            point["fine_term_links"] = [
                {
                    key: link.get(key)
                    for key in ("fine_term_id", "fine_term_name", "role")
                }
                for link in links
                if isinstance(link, Mapping)
            ]
    return clean


def _classification_from_evidence_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    direct_terms: list[dict[str, str]] = []
    supporting_terms: list[dict[str, str]] = []
    role_resolved = {"direct": [], "supporting_prerequisite": []}
    role_ambiguous = {"direct": [], "supporting_prerequisite": []}
    role_unmapped = {"direct": [], "supporting_prerequisite": []}
    seen_terms = {"direct": set(), "supporting_prerequisite": set()}
    parts = payload.get("parts")
    if isinstance(parts, list):
        for part in parts:
            points = part.get("evidence_points") if isinstance(part, Mapping) else None
            if not isinstance(points, list):
                continue
            for point in points:
                links = point.get("fine_term_links") if isinstance(point, Mapping) else None
                if not isinstance(links, list):
                    continue
                for link in links:
                    if not isinstance(link, Mapping):
                        continue
                    role = str(link.get("role") or "")
                    if role not in seen_terms:
                        continue
                    term_id = str(link.get("fine_term_id") or "")
                    term_name = str(link.get("fine_term_name") or "")
                    signature = (term_id, term_name)
                    if signature not in seen_terms[role]:
                        seen_terms[role].add(signature)
                        (direct_terms if role == "direct" else supporting_terms).append(
                            {"fine_term_id": term_id, "fine_term_name": term_name}
                        )
                    resolution = link.get("core_resolution")
                    if not isinstance(resolution, Mapping):
                        continue
                    status = str(resolution.get("status") or "")
                    stable_keys = resolution.get("stable_keys")
                    keys = [str(item) for item in stable_keys] if isinstance(stable_keys, list) else []
                    if status == "resolved":
                        _append_unique(role_resolved[role], keys)
                    elif status == "ambiguous":
                        _append_unique(role_ambiguous[role], keys)
                    elif status == "unmapped":
                        _append_unique(role_unmapped[role], [term_id])
    resolved = list(role_resolved["direct"])
    _append_unique(resolved, role_resolved["supporting_prerequisite"])
    ambiguous = list(role_ambiguous["direct"])
    _append_unique(ambiguous, role_ambiguous["supporting_prerequisite"])
    unmapped = list(role_unmapped["direct"])
    _append_unique(unmapped, role_unmapped["supporting_prerequisite"])
    return {
        "direct_fine_terms": direct_terms,
        "supporting_prerequisite_fine_terms": supporting_terms,
        "direct_resolved_core_node_ids": role_resolved["direct"],
        "supporting_resolved_core_node_ids": role_resolved["supporting_prerequisite"],
        "direct_ambiguous_core_node_ids": role_ambiguous["direct"],
        "supporting_ambiguous_core_node_ids": role_ambiguous["supporting_prerequisite"],
        "direct_unmapped_fine_term_ids": role_unmapped["direct"],
        "supporting_unmapped_fine_term_ids": role_unmapped["supporting_prerequisite"],
        "resolved_core_node_ids": resolved,
        "ambiguous_core_node_ids": ambiguous,
        "unmapped_fine_term_ids": unmapped,
    }


def _append_unique(target: list[str], values: Sequence[str]) -> None:
    seen = set(target)
    for value in values:
        if value and value not in seen:
            seen.add(value)
            target.append(value)


__all__ = [
    "FineTermCoreMappingRepository",
    "SolutionEvidenceProjectionWriter",
    "SolutionEvidenceRepository",
]
