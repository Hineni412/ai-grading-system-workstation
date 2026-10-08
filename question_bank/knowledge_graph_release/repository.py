from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from question_bank.database.schema import connect
from question_bank.knowledge_graph_release.contracts import (
    KnowledgeGraphRelease,
    KnowledgeGraphReleaseError,
    cached_release_from_json,
    stable_record_hash,
)
from question_bank.knowledge_graph_release.loader import (
    load_release,
    load_taxonomy_catalog_for_release,
)
from question_bank.knowledge_graph_release.validation import validate_release


class KnowledgeGraphReleaseConflict(KnowledgeGraphReleaseError):
    """The database changed or contains teacher decisions needing review."""


class KnowledgeGraphReleaseNotFound(KnowledgeGraphReleaseError):
    pass


@dataclass(frozen=True, slots=True)
class InstallIssue:
    code: str
    message: str
    blocking: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "blocking": self.blocking,
        }


@dataclass(frozen=True, slots=True)
class HighImpactItem:
    fine_term_id: str
    display_name: str
    disposition: str
    target_names: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "fine_term_id": self.fine_term_id,
            "display_name": self.display_name,
            "disposition": self.disposition,
            "target_names": list(self.target_names),
        }


@dataclass(frozen=True, slots=True)
class InstallPreview:
    release_id: str
    content_hash: str
    current_release_id: str | None
    node_count: int
    fine_term_count: int
    mapping_count: int
    relation_count: int
    high_impact_count: int
    high_impact_items: tuple[HighImpactItem, ...]
    issues: tuple[InstallIssue, ...]

    @property
    def can_activate(self) -> bool:
        return not any(issue.blocking for issue in self.issues)

    def to_dict(self) -> dict[str, object]:
        return {
            "release_id": self.release_id,
            "content_hash": self.content_hash,
            "current_release_id": self.current_release_id,
            "node_count": self.node_count,
            "fine_term_count": self.fine_term_count,
            "mapping_count": self.mapping_count,
            "relation_count": self.relation_count,
            "high_impact_count": self.high_impact_count,
            "high_impact_items": [
                item.to_dict() for item in self.high_impact_items
            ],
            "can_activate": self.can_activate,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def preview_install(
    db_path: Path,
    release: KnowledgeGraphRelease | None = None,
    *,
    taxonomy_catalog: Mapping[str, Any] | None = None,
) -> InstallPreview:
    candidate = release or load_release()
    catalog = dict(
        taxonomy_catalog or load_taxonomy_catalog_for_release(candidate)
    )
    validation = validate_release(candidate, catalog)
    issues = [
        InstallIssue(issue.code, f"{issue.path}: {issue.message}")
        for issue in validation.errors
    ]
    current_release_id: str | None = None
    try:
        with connect(Path(db_path)) as connection:
            current_release_id = _active_release_id(connection)
            issues.extend(_database_conflicts(connection, candidate, current_release_id))
    except sqlite3.OperationalError as exc:
        issues.append(
            InstallIssue(
                "schema_not_ready",
                f"知识图谱发布表尚未就绪：{exc}",
            )
        )
    payload = candidate.payload
    core_names = {
        str(item["stable_key"]): str(item["display_name"])
        for item in _objects(payload.get("core_nodes"))
    }
    mapping_targets: dict[str, list[str]] = {}
    for item in _objects(payload.get("mappings")):
        target_name = core_names.get(str(item["stable_key"]), "")
        if target_name:
            mapping_targets.setdefault(
                str(item["fine_term_id"]), []
            ).append(target_name)
    high_impact_items = tuple(
        HighImpactItem(
            fine_term_id=str(item["fine_term_id"]),
            display_name=str(item["display_name"]),
            disposition=str(item["disposition"]),
            target_names=tuple(
                mapping_targets.get(str(item["fine_term_id"]), ())
            ),
        )
        for item in _objects(payload.get("fine_term_dispositions"))
        if str(item.get("review_priority") or "") == "high_impact"
    )
    return InstallPreview(
        release_id=candidate.release_id,
        content_hash=candidate.content_hash,
        current_release_id=current_release_id,
        node_count=len(_objects(payload.get("core_nodes"))),
        fine_term_count=len(_objects(payload.get("fine_term_dispositions"))),
        mapping_count=len(_objects(payload.get("mappings"))),
        relation_count=len(_objects(payload.get("relations"))),
        high_impact_count=len(high_impact_items),
        high_impact_items=high_impact_items,
        issues=tuple(issues),
    )


def stage_release(
    db_path: Path,
    release: KnowledgeGraphRelease | None = None,
    *,
    actor_ref: str,
    source_reference: str,
    reason: str = "知识图谱发布包已完成结构校验并进入候选区",
    taxonomy_catalog: Mapping[str, Any] | None = None,
    external_connection: sqlite3.Connection | None = None,
) -> str:
    candidate = release or load_release()
    validation = validate_release(
        candidate,
        dict(
            taxonomy_catalog
            or load_taxonomy_catalog_for_release(candidate)
        ),
    )
    validation.raise_for_errors()
    actor = _required(actor_ref, "actor_ref")
    source = _required(source_reference, "source_reference")
    stage_reason = _required(reason, "reason")
    payload = candidate.payload
    with connect(Path(db_path), external_connection=external_connection) as connection:
        if external_connection is None:
            connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT content_hash FROM knowledge_graph_releases WHERE release_id = ?",
            (candidate.release_id,),
        ).fetchone()
        if existing is not None:
            if str(existing["content_hash"]) != candidate.content_hash:
                raise KnowledgeGraphReleaseConflict(
                    "同一发布编号对应了不同内容，已拒绝覆盖"
                )
            return candidate.release_id

        _ensure_identities(connection, payload)
        connection.execute(
            """
            INSERT INTO knowledge_graph_releases (
                release_id, schema_version, taxonomy_revision, content_hash,
                payload_json, status, predecessor_release_id,
                source_reference, created_by
            ) VALUES (?, ?, ?, ?, ?, 'candidate', ?, ?, ?)
            """,
            (
                candidate.release_id,
                candidate.schema_version,
                candidate.taxonomy_revision,
                candidate.content_hash,
                candidate.canonical_json(),
                _optional(payload.get("predecessor_release_id")),
                source,
                actor,
            ),
        )
        _insert_release_rows(connection, candidate)
        connection.execute(
            """
            INSERT INTO knowledge_graph_release_events (
                release_id, event_type, actor_ref, reason, resulting_revision
            ) VALUES (?, 'staged', ?, ?, 1)
            """,
            (candidate.release_id, actor, stage_reason),
        )
    return candidate.release_id


def activate_release(
    db_path: Path,
    release_id: str,
    *,
    expected_active_release_id: str | None,
    actor_ref: str,
    reason: str,
    external_connection: sqlite3.Connection | None = None,
) -> str:
    return _activate(
        Path(db_path),
        _required(release_id, "release_id"),
        expected_active_release_id=_optional(expected_active_release_id),
        actor_ref=_required(actor_ref, "actor_ref"),
        reason=_required(reason, "reason"),
        rollback=False,
        external_connection=external_connection,
    )


def bootstrap_release(
    db_path: Path,
    release: KnowledgeGraphRelease | None = None,
    *,
    actor_ref: str,
    source_reference: str,
    reason: str,
    taxonomy_catalog: Mapping[str, Any] | None = None,
) -> str:
    """Atomically install the first immutable current standard.

    Identity statuses are synced to the release payload so relation governance
    accepts the installed vocabulary from the start.  Legacy live mappings and
    relations are deliberately neither read nor changed.  Runtime current-graph
    consumers read the release payload, while the old governance rows remain
    available as historical records.
    """

    candidate = release or load_release()
    catalog = dict(
        taxonomy_catalog or load_taxonomy_catalog_for_release(candidate)
    )
    validation = validate_release(candidate, catalog)
    validation.raise_for_errors()
    actor = _required(actor_ref, "actor_ref")
    source = _required(source_reference, "source_reference")
    activation_reason = _required(reason, "reason")

    with connect(Path(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        active_row = connection.execute(
            """
            SELECT release_id, payload_json
            FROM knowledge_graph_releases
            WHERE status = 'active'
            """
        ).fetchone()
        if active_row is not None:
            active = KnowledgeGraphRelease.from_mapping(
                json.loads(str(active_row["payload_json"]))
            )
            active_validation = validate_release(
                active,
                load_taxonomy_catalog_for_release(active),
            )
            if not active_validation.valid:
                raise KnowledgeGraphReleaseConflict(
                    "当前活动图谱无法通过结构校验"
                )
            return active.release_id

        row = connection.execute(
            "SELECT * FROM knowledge_graph_releases WHERE release_id = ?",
            (candidate.release_id,),
        ).fetchone()
        if row is not None and str(row["content_hash"]) != candidate.content_hash:
            raise KnowledgeGraphReleaseConflict(
                "同一发布编号对应了不同内容，已拒绝覆盖"
            )
        hash_owner = connection.execute(
            "SELECT release_id FROM knowledge_graph_releases WHERE content_hash = ?",
            (candidate.content_hash,),
        ).fetchone()
        if (
            hash_owner is not None
            and str(hash_owner["release_id"]) != candidate.release_id
        ):
            raise KnowledgeGraphReleaseConflict(
                "同一发布内容使用了另一个发布编号"
            )

        if row is None:
            _ensure_identities(connection, candidate.payload)
            connection.execute(
                """
                INSERT INTO knowledge_graph_releases (
                    release_id, schema_version, taxonomy_revision, content_hash,
                    payload_json, status, predecessor_release_id,
                    source_reference, created_by
                ) VALUES (?, ?, ?, ?, ?, 'candidate', NULL, ?, ?)
                """,
                (
                    candidate.release_id,
                    candidate.schema_version,
                    candidate.taxonomy_revision,
                    candidate.content_hash,
                    candidate.canonical_json(),
                    source,
                    actor,
                ),
            )
            _insert_release_rows(connection, candidate)
            _apply_identity_states(connection, candidate)
            connection.execute(
                """
                INSERT INTO knowledge_graph_release_events (
                    release_id, event_type, actor_ref, reason,
                    resulting_revision
                ) VALUES (?, 'staged', ?, ?, 1)
                """,
                (candidate.release_id, actor, activation_reason),
            )
            target_revision = 2
        else:
            if str(row["status"]) != "candidate":
                raise KnowledgeGraphReleaseConflict(
                    "已有同版本记录，但它不是可启用的候选版本"
                )
            persisted = KnowledgeGraphRelease.from_mapping(
                json.loads(str(row["payload_json"]))
            )
            persisted_validation = validate_release(persisted, catalog)
            if (
                not persisted_validation.valid
                or persisted.content_hash != candidate.content_hash
            ):
                raise KnowledgeGraphReleaseConflict(
                    "已有候选版本无法通过结构校验"
                )
            target_revision = int(row["revision"]) + 1

        connection.execute(
            """
            UPDATE knowledge_graph_releases
            SET status = 'active', activated_by = ?, activation_note = ?,
                activated_at = datetime('now','localtime'), retired_at = NULL,
                revision = ?, updated_at = datetime('now','localtime')
            WHERE release_id = ? AND status = 'candidate'
            """,
            (actor, activation_reason, target_revision, candidate.release_id),
        )
        connection.execute(
            """
            INSERT INTO knowledge_graph_release_events (
                release_id, event_type, actor_ref, reason,
                from_release_id, resulting_revision
            ) VALUES (?, 'activated', ?, ?, NULL, ?)
            """,
            (
                candidate.release_id,
                actor,
                activation_reason,
                target_revision,
            ),
        )
    return candidate.release_id


def rollback_release(
    db_path: Path,
    target_release_id: str,
    *,
    expected_active_release_id: str,
    actor_ref: str,
    reason: str,
) -> str:
    return _activate(
        Path(db_path),
        _required(target_release_id, "target_release_id"),
        expected_active_release_id=_required(
            expected_active_release_id, "expected_active_release_id"
        ),
        actor_ref=_required(actor_ref, "actor_ref"),
        reason=_required(reason, "reason"),
        rollback=True,
    )


def active_release_id(db_path: Path) -> str | None:
    with connect(Path(db_path)) as connection:
        return _active_release_id(connection)


def load_active_release(db_path: Path) -> KnowledgeGraphRelease | None:
    """Read the immutable payload for the currently active release, if any."""

    try:
        with connect(Path(db_path)) as connection:
            row = connection.execute(
                """
                SELECT release_id, content_hash, payload_json
                FROM knowledge_graph_releases
                WHERE status = 'active'
                """
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return cached_release_from_json(
        str(row["payload_json"]),
        release_id=str(row["release_id"]),
        content_hash=str(row["content_hash"]),
    )


def _activate(
    db_path: Path,
    release_id: str,
    *,
    expected_active_release_id: str | None,
    actor_ref: str,
    reason: str,
    rollback: bool,
    external_connection: sqlite3.Connection | None = None,
) -> str:
    with connect(db_path, external_connection=external_connection) as connection:
        if external_connection is None:
            connection.execute("BEGIN IMMEDIATE")
        current = _active_release_id(connection)
        if current != expected_active_release_id:
            raise KnowledgeGraphReleaseConflict(
                "活动图谱已变化，请重新预演后再确认"
            )
        row = connection.execute(
            "SELECT * FROM knowledge_graph_releases WHERE release_id = ?",
            (release_id,),
        ).fetchone()
        if row is None:
            raise KnowledgeGraphReleaseNotFound(release_id)
        allowed_statuses = {"candidate", "retired"} if rollback else {"candidate"}
        if str(row["status"]) == "active" and current == release_id:
            return release_id
        if str(row["status"]) not in allowed_statuses:
            raise KnowledgeGraphReleaseConflict("目标发布版本当前不能启用")
        candidate = KnowledgeGraphRelease.from_mapping(
            json.loads(str(row["payload_json"]))
        )
        issues = _database_conflicts(
            connection,
            candidate,
            current,
            check_predecessor=not rollback,
        )
        blocking = [issue for issue in issues if issue.blocking]
        if blocking:
            raise KnowledgeGraphReleaseConflict(
                "；".join(issue.message for issue in blocking[:6])
            )

        _apply_identity_states(connection, candidate)
        _apply_mappings(connection, candidate, actor_ref, reason)
        _apply_relations(connection, candidate, actor_ref, reason)

        if current is not None and current != release_id:
            current_row = connection.execute(
                "SELECT revision FROM knowledge_graph_releases WHERE release_id = ?",
                (current,),
            ).fetchone()
            assert current_row is not None
            current_revision = int(current_row["revision"]) + 1
            connection.execute(
                """
                UPDATE knowledge_graph_releases
                SET status = 'retired', retired_at = datetime('now','localtime'),
                    revision = ?, updated_at = datetime('now','localtime')
                WHERE release_id = ? AND status = 'active'
                """,
                (current_revision, current),
            )
            connection.execute(
                """
                INSERT INTO knowledge_graph_release_events (
                    release_id, event_type, actor_ref, reason,
                    from_release_id, resulting_revision
                ) VALUES (?, 'retired', ?, ?, ?, ?)
                """,
                (current, actor_ref, reason, release_id, current_revision),
            )

        target_revision = int(row["revision"]) + 1
        connection.execute(
            """
            UPDATE knowledge_graph_releases
            SET status = 'active', activated_by = ?, activation_note = ?,
                activated_at = datetime('now','localtime'), retired_at = NULL,
                revision = ?, updated_at = datetime('now','localtime')
            WHERE release_id = ?
            """,
            (actor_ref, reason, target_revision, release_id),
        )
        connection.execute(
            """
            INSERT INTO knowledge_graph_release_events (
                release_id, event_type, actor_ref, reason,
                from_release_id, resulting_revision
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                release_id,
                "rolled_back" if rollback else "activated",
                actor_ref,
                reason,
                current,
                target_revision,
            ),
        )
    return release_id


def _database_conflicts(
    connection: sqlite3.Connection,
    release: KnowledgeGraphRelease,
    current_release_id: str | None,
    *,
    check_predecessor: bool = True,
) -> list[InstallIssue]:
    issues: list[InstallIssue] = []
    row = connection.execute(
        "SELECT release_id, content_hash FROM knowledge_graph_releases WHERE release_id = ?",
        (release.release_id,),
    ).fetchone()
    if row is not None and str(row["content_hash"]) != release.content_hash:
        issues.append(
            InstallIssue("release_id_collision", "同一发布编号已存在，但内容不同")
        )
    hash_owner = connection.execute(
        "SELECT release_id FROM knowledge_graph_releases WHERE content_hash = ?",
        (release.content_hash,),
    ).fetchone()
    if hash_owner is not None and str(hash_owner["release_id"]) != release.release_id:
        issues.append(
            InstallIssue("content_hash_collision", "同一发布内容使用了另一个发布编号")
        )
    predecessor = _optional(release.payload.get("predecessor_release_id"))
    if (
        check_predecessor
        and predecessor is not None
        and predecessor != current_release_id
    ):
        issues.append(
            InstallIssue(
                "predecessor_mismatch",
                "候选版本不是从当前活动图谱继续生成的",
            )
        )

    desired_mappings = {
        (str(item["fine_term_id"]), str(item["stable_key"]))
        for item in _objects(release.payload.get("mappings"))
    }
    teacher_mappings = connection.execute(
        """
        SELECT fine_term_id, stable_key
        FROM fine_term_core_mappings
        WHERE status = 'confirmed' AND source_kind = 'teacher'
        """
    ).fetchall()
    mapping_conflicts = [
        f"{row['fine_term_id']}→{row['stable_key']}"
        for row in teacher_mappings
        if (str(row["fine_term_id"]), str(row["stable_key"]))
        not in desired_mappings
    ]
    if mapping_conflicts:
        issues.append(
            InstallIssue(
                "teacher_mapping_conflict",
                "存在未纳入发布包的教师确认映射："
                + "、".join(mapping_conflicts[:5]),
            )
        )
    blocked_teacher_mapping_states = connection.execute(
        """
        SELECT fine_term_id, stable_key, status
        FROM fine_term_core_mappings
        WHERE source_kind = 'teacher' AND status <> 'confirmed'
        """
    ).fetchall()
    desired_but_blocked = [
        f"{row['fine_term_id']}→{row['stable_key']}({row['status']})"
        for row in blocked_teacher_mapping_states
        if (str(row["fine_term_id"]), str(row["stable_key"]))
        in desired_mappings
    ]
    if desired_but_blocked:
        issues.append(
            InstallIssue(
                "teacher_mapping_state_conflict",
                "发布包需要的映射曾被教师否决或退役："
                + "、".join(desired_but_blocked[:5]),
            )
        )

    desired_relations = {
        (
            str(item["source_key"]),
            str(item["target_key"]),
            str(item["relation_type"]),
        )
        for item in _objects(release.payload.get("relations"))
    }
    teacher_relations = connection.execute(
        """
        SELECT source_key, target_key, relation_type
        FROM knowledge_relations
        WHERE status = 'confirmed' AND source_kind = 'teacher'
        """
    ).fetchall()
    relation_conflicts = [
        f"{row['source_key']}→{row['target_key']}({row['relation_type']})"
        for row in teacher_relations
        if (
            str(row["source_key"]),
            str(row["target_key"]),
            str(row["relation_type"]),
        )
        not in desired_relations
    ]
    if relation_conflicts:
        issues.append(
            InstallIssue(
                "teacher_relation_conflict",
                "存在未纳入发布包的教师确认关系："
                + "、".join(relation_conflicts[:5]),
            )
        )
    blocked_teacher_relation_states = connection.execute(
        """
        SELECT source_key, target_key, relation_type, status
        FROM knowledge_relations
        WHERE source_kind = 'teacher' AND status <> 'confirmed'
        """
    ).fetchall()
    desired_relations_blocked = [
        f"{row['source_key']}→{row['target_key']}({row['relation_type']}:{row['status']})"
        for row in blocked_teacher_relation_states
        if (
            str(row["source_key"]),
            str(row["target_key"]),
            str(row["relation_type"]),
        )
        in desired_relations
    ]
    if desired_relations_blocked:
        issues.append(
            InstallIssue(
                "teacher_relation_state_conflict",
                "发布包需要的关系曾被教师否决或退役："
                + "、".join(desired_relations_blocked[:5]),
            )
        )
    return issues


def _ensure_identities(
    connection: sqlite3.Connection,
    payload: Mapping[str, Any],
) -> None:
    for node in _objects(payload.get("core_nodes")):
        key = str(node["stable_key"])
        display_name = str(node["display_name"])
        row = connection.execute(
            "SELECT display_name FROM knowledge_tag_identities WHERE stable_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO knowledge_tag_identities (
                    stable_key, display_name, origin, status, retired_at
                ) VALUES (?, ?, 'local', 'retired', datetime('now','localtime'))
                """,
                (key, display_name),
            )
        elif str(row["display_name"]) != display_name:
            raise KnowledgeGraphReleaseConflict(
                f"稳定身份 {key} 的名称与发布包不一致"
            )


def _insert_release_rows(
    connection: sqlite3.Connection,
    release: KnowledgeGraphRelease,
) -> None:
    payload = release.payload
    release_id = release.release_id
    for item in _objects(payload.get("core_nodes")):
        connection.execute(
            """
            INSERT INTO knowledge_graph_node_profiles (
                release_id, stable_key, display_name, node_kind, status,
                definition, include_scope, exclude_scope,
                curriculum_anchors_json, observable_evidence, rationale
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                release_id,
                item["stable_key"],
                item["display_name"],
                item["node_kind"],
                item["status"],
                item["definition"],
                item["include_scope"],
                item["exclude_scope"],
                _json(item.get("curriculum_anchors", [])),
                item["observable_evidence"],
                item["rationale"],
            ),
        )
    for item in _objects(payload.get("fine_term_dispositions")):
        connection.execute(
            """
            INSERT INTO knowledge_graph_fine_term_dispositions (
                release_id, fine_term_id, display_name, disposition,
                definition, include_scope, exclude_scope,
                curriculum_anchors_json, rationale, confidence, review_priority
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                release_id,
                item["fine_term_id"],
                item["display_name"],
                item["disposition"],
                item["definition"],
                item["include_scope"],
                item["exclude_scope"],
                _json(item.get("curriculum_anchors", [])),
                item["rationale"],
                item["confidence"],
                item["review_priority"],
            ),
        )
    for item in _objects(payload.get("mappings")):
        connection.execute(
            """
            INSERT INTO knowledge_graph_release_mappings (
                release_id, fine_term_id, stable_key, mapping_role, rationale
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                release_id,
                item["fine_term_id"],
                item["stable_key"],
                item["mapping_role"],
                item["rationale"],
            ),
        )
    for item in _objects(payload.get("relations")):
        connection.execute(
            """
            INSERT INTO knowledge_graph_release_relations (
                release_id, relation_key, source_key, target_key,
                relation_type, basis_kind, strength, rationale,
                evidence_source_ids_json, source_locator
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                release_id,
                item["relation_key"],
                item["source_key"],
                item["target_key"],
                item["relation_type"],
                item["basis_kind"],
                item["strength"],
                item["rationale"],
                _json(item.get("evidence_source_ids", [])),
                item["source_locator"],
            ),
        )
    for item in _objects(payload.get("replacements")):
        connection.execute(
            """
            INSERT INTO knowledge_identity_replacements (
                release_id, retired_key, replacement_key,
                replacement_kind, rationale
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                release_id,
                item["retired_key"],
                item["replacement_key"],
                item["replacement_kind"],
                item["rationale"],
            ),
        )


def _apply_identity_states(
    connection: sqlite3.Connection,
    release: KnowledgeGraphRelease,
) -> None:
    target_keys = {
        str(node["stable_key"])
        for node in _objects(release.payload.get("core_nodes"))
    }
    historical_release_keys = connection.execute(
        "SELECT DISTINCT stable_key FROM knowledge_graph_node_profiles"
    ).fetchall()
    for row in historical_release_keys:
        key = str(row["stable_key"])
        if key in target_keys:
            continue
        connection.execute(
            """
            UPDATE knowledge_tag_identities
            SET status = 'retired',
                retired_at = COALESCE(retired_at, datetime('now','localtime')),
                revision = revision + 1,
                updated_at = datetime('now','localtime')
            WHERE stable_key = ? AND status = 'active'
            """,
            (key,),
        )
    for node in _objects(release.payload.get("core_nodes")):
        retired = str(node["status"]) == "retired"
        connection.execute(
            """
            UPDATE knowledge_tag_identities
            SET status = ?, display_name = ?,
                retired_at = CASE
                    WHEN ? = 'retired'
                    THEN COALESCE(retired_at, datetime('now','localtime'))
                    ELSE NULL
                END,
                revision = revision + 1,
                updated_at = datetime('now','localtime')
            WHERE stable_key = ?
              AND (status <> ? OR display_name <> ?)
            """,
            (
                "retired" if retired else "active",
                node["display_name"],
                "retired" if retired else "active",
                node["stable_key"],
                "retired" if retired else "active",
                node["display_name"],
            ),
        )
        for alias_kind, alias in (
            ("canonical_name", str(node["display_name"])),
            *(
                ("registered_alias", str(value))
                for value in node.get("aliases", [])
            ),
        ):
            connection.execute(
                """
                INSERT OR IGNORE INTO knowledge_tag_aliases (
                    stable_key, alias, normalized_alias, alias_kind
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    node["stable_key"],
                    alias,
                    _normalized(alias),
                    alias_kind,
                ),
            )


def _apply_mappings(
    connection: sqlite3.Connection,
    release: KnowledgeGraphRelease,
    actor_ref: str,
    reason: str,
) -> None:
    desired = {
        (str(item["fine_term_id"]), str(item["stable_key"])): item
        for item in _objects(release.payload.get("mappings"))
    }
    rows = connection.execute(
        """
        SELECT * FROM fine_term_core_mappings
        WHERE status = 'confirmed' AND source_kind <> 'teacher'
        """
    ).fetchall()
    for row in rows:
        pair = (str(row["fine_term_id"]), str(row["stable_key"]))
        if pair in desired:
            continue
        revision = int(row["revision"]) + 1
        connection.execute(
            """
            UPDATE fine_term_core_mappings
            SET status = 'retired', decision_by = ?, decision_note = ?,
                decided_at = datetime('now','localtime'), revision = ?,
                updated_at = datetime('now','localtime')
            WHERE mapping_id = ?
            """,
            (actor_ref, reason, revision, row["mapping_id"]),
        )
        _mapping_event(
            connection,
            str(row["mapping_id"]),
            "retired",
            "confirmed",
            "retired",
            actor_ref,
            reason,
            int(row["revision"]),
            revision,
        )

    dispositions = {
        str(item["fine_term_id"]): str(item["disposition"])
        for item in _objects(release.payload.get("fine_term_dispositions"))
    }
    for pair, item in desired.items():
        fine_term_id, stable_key = pair
        row = connection.execute(
            """
            SELECT * FROM fine_term_core_mappings
            WHERE fine_term_id = ? AND stable_key = ?
            """,
            pair,
        ).fetchone()
        if row is not None and str(row["source_kind"]) == "teacher":
            continue
        if row is None:
            mapping_id = stable_record_hash(
                "knowledge-graph-live-mapping", fine_term_id, stable_key
            )
            connection.execute(
                """
                INSERT INTO fine_term_core_mappings (
                    mapping_id, fine_term_id, stable_key, status, source_kind,
                    source_reference, rationale, decision_by, decision_note,
                    decided_at, graph_release_id, mapping_role, disposition
                ) VALUES (?, ?, ?, 'confirmed', 'builtin', ?, ?, ?, ?,
                    datetime('now','localtime'), ?, ?, ?)
                """,
                (
                    mapping_id,
                    fine_term_id,
                    stable_key,
                    release.release_id,
                    item["rationale"],
                    actor_ref,
                    reason,
                    release.release_id,
                    item["mapping_role"],
                    dispositions[fine_term_id],
                ),
            )
            _mapping_event(
                connection,
                mapping_id,
                "confirmed",
                None,
                "confirmed",
                actor_ref,
                reason,
                0,
                1,
            )
            continue
        previous = str(row["status"])
        revision = int(row["revision"]) + 1
        connection.execute(
            """
            UPDATE fine_term_core_mappings
            SET status = 'confirmed', source_kind = 'builtin',
                source_reference = ?, rationale = ?, decision_by = ?,
                decision_note = ?, decided_at = datetime('now','localtime'),
                graph_release_id = ?, mapping_role = ?, disposition = ?,
                revision = ?, updated_at = datetime('now','localtime')
            WHERE mapping_id = ?
            """,
            (
                release.release_id,
                item["rationale"],
                actor_ref,
                reason,
                release.release_id,
                item["mapping_role"],
                dispositions[fine_term_id],
                revision,
                row["mapping_id"],
            ),
        )
        _mapping_event(
            connection,
            str(row["mapping_id"]),
            "restored" if previous == "retired" else "confirmed",
            previous,
            "confirmed",
            actor_ref,
            reason,
            int(row["revision"]),
            revision,
        )


def _apply_relations(
    connection: sqlite3.Connection,
    release: KnowledgeGraphRelease,
    actor_ref: str,
    reason: str,
) -> None:
    desired = {
        (
            str(item["source_key"]),
            str(item["target_key"]),
            str(item["relation_type"]),
        ): item
        for item in _objects(release.payload.get("relations"))
    }
    rows = connection.execute(
        """
        SELECT * FROM knowledge_relations
        WHERE status = 'confirmed' AND source_kind <> 'teacher'
        """
    ).fetchall()
    for row in rows:
        triple = (
            str(row["source_key"]),
            str(row["target_key"]),
            str(row["relation_type"]),
        )
        if triple in desired:
            continue
        _transition_relation(
            connection,
            row,
            to_status="retired",
            event_type="retired",
            actor_ref=actor_ref,
            reason=reason,
        )

    source_refs = {
        str(item["source_id"]): str(item["reference"])
        for item in _objects(release.payload.get("sources"))
    }
    for triple, item in desired.items():
        row = connection.execute(
            """
            SELECT * FROM knowledge_relations
            WHERE source_key = ? AND target_key = ? AND relation_type = ?
            """,
            triple,
        ).fetchone()
        if row is not None and str(row["source_kind"]) == "teacher":
            relation_id = str(row["relation_id"])
        elif row is None:
            relation_id = "krg_" + str(item["relation_key"])[:32]
            connection.execute(
                """
                INSERT INTO knowledge_relations (
                    relation_id, source_key, target_key, relation_type,
                    status, source_kind, source_reference, rationale,
                    decision_by, decision_note, decided_at, graph_release_id
                ) VALUES (?, ?, ?, ?, 'confirmed', 'system', ?, ?, ?, ?,
                    datetime('now','localtime'), ?)
                """,
                (
                    relation_id,
                    *triple,
                    release.release_id,
                    item["rationale"],
                    actor_ref,
                    reason,
                    release.release_id,
                ),
            )
            _relation_event(
                connection,
                relation_id,
                "confirmed",
                None,
                "confirmed",
                actor_ref,
                reason,
                0,
                1,
            )
        else:
            previous = str(row["status"])
            revision = int(row["revision"]) + 1
            connection.execute(
                """
                UPDATE knowledge_relations
                SET status = 'confirmed', source_kind = 'system',
                    source_reference = ?, rationale = ?, decision_by = ?,
                    decision_note = ?, decided_at = datetime('now','localtime'),
                    graph_release_id = ?, revision = ?,
                    updated_at = datetime('now','localtime')
                WHERE relation_id = ?
                """,
                (
                    release.release_id,
                    item["rationale"],
                    actor_ref,
                    reason,
                    release.release_id,
                    revision,
                    row["relation_id"],
                ),
            )
            relation_id = str(row["relation_id"])
            _relation_event(
                connection,
                relation_id,
                "restored" if previous == "retired" else "confirmed",
                previous,
                "confirmed",
                actor_ref,
                reason,
                int(row["revision"]),
                revision,
            )
        for source_id in item.get("evidence_source_ids", []):
            source_id = str(source_id)
            source_reference = source_refs[source_id]
            evidence_id = stable_record_hash(
                "knowledge-relation-evidence",
                relation_id,
                release.release_id,
                item["basis_kind"],
                source_id,
                item["source_locator"],
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO knowledge_relation_evidence (
                    evidence_id, relation_id, release_id, basis_kind, strength,
                    source_reference, source_locator, rationale
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    relation_id,
                    release.release_id,
                    item["basis_kind"],
                    item["strength"],
                    source_reference,
                    item["source_locator"],
                    item["rationale"],
                ),
            )


def _transition_relation(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    to_status: str,
    event_type: str,
    actor_ref: str,
    reason: str,
) -> None:
    revision = int(row["revision"]) + 1
    connection.execute(
        """
        UPDATE knowledge_relations
        SET status = ?, decision_by = ?, decision_note = ?,
            decided_at = datetime('now','localtime'), revision = ?,
            updated_at = datetime('now','localtime')
        WHERE relation_id = ?
        """,
        (to_status, actor_ref, reason, revision, row["relation_id"]),
    )
    _relation_event(
        connection,
        str(row["relation_id"]),
        event_type,
        str(row["status"]),
        to_status,
        actor_ref,
        reason,
        int(row["revision"]),
        revision,
    )


def _mapping_event(
    connection: sqlite3.Connection,
    mapping_id: str,
    event_type: str,
    from_status: str | None,
    to_status: str,
    actor_ref: str,
    reason: str,
    expected_revision: int,
    resulting_revision: int,
) -> None:
    connection.execute(
        """
        INSERT INTO fine_term_core_mapping_events (
            mapping_id, event_type, from_status, to_status, actor_kind,
            actor_ref, reason, expected_revision, resulting_revision
        ) VALUES (?, ?, ?, ?, 'teacher', ?, ?, ?, ?)
        """,
        (
            mapping_id,
            event_type,
            from_status,
            to_status,
            actor_ref,
            reason,
            expected_revision,
            resulting_revision,
        ),
    )


def _relation_event(
    connection: sqlite3.Connection,
    relation_id: str,
    event_type: str,
    from_status: str | None,
    to_status: str,
    actor_ref: str,
    reason: str,
    expected_revision: int,
    resulting_revision: int,
) -> None:
    connection.execute(
        """
        INSERT INTO knowledge_relation_audit_events (
            relation_id, event_type, from_status, to_status, actor_kind,
            actor_ref, reason, expected_revision, resulting_revision
        ) VALUES (?, ?, ?, ?, 'teacher', ?, ?, ?, ?)
        """,
        (
            relation_id,
            event_type,
            from_status,
            to_status,
            actor_ref,
            reason,
            expected_revision,
            resulting_revision,
        ),
    )


def _active_release_id(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT release_id FROM knowledge_graph_releases WHERE status = 'active'"
    ).fetchone()
    return None if row is None else str(row["release_id"])


def _objects(raw: object) -> list[Mapping[str, Any]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _required(value: object, label: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{label} is required")
    return clean


def _optional(value: object) -> str | None:
    clean = str(value or "").strip()
    return clean or None


def _normalized(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s\W_]+", "", normalized)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "InstallIssue",
    "InstallPreview",
    "KnowledgeGraphReleaseConflict",
    "KnowledgeGraphReleaseNotFound",
    "active_release_id",
    "activate_release",
    "bootstrap_release",
    "preview_install",
    "rollback_release",
    "stage_release",
]
