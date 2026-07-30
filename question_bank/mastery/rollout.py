from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from question_bank.database.schema import connect
from question_bank.mastery.comparison import MasteryComparisonReport
from question_bank.mastery.v2 import MasteryV2Parameters


class MasteryRolloutError(RuntimeError):
    pass


class MasteryEvaluationNotFound(MasteryRolloutError):
    pass


class MasteryEvaluationItemNotFound(MasteryRolloutError):
    pass


class MasteryRolloutRevisionConflict(MasteryRolloutError):
    def __init__(self, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__("mastery rollout revision is stale")


class MasterySpotCheckConflict(MasteryRolloutError):
    pass


class MasteryRolloutGateBlocked(MasteryRolloutError):
    pass


@dataclass(frozen=True, slots=True)
class MasteryRolloutState:
    enabled: bool
    active_mode: Literal["v1", "v2"]
    active_parameter_version: str | None
    approved_evaluation_id: str | None
    revision: int
    updated_by: str | None
    reason: str | None
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MasteryEvaluationGate:
    evaluation_id: str
    parameter_version: str
    revision: int
    required_review_count: int
    accepted_count: int
    rejected_count: int
    pending_count: int
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MasteryRolloutRepository:
    """Persist immutable parameters, anonymized checks and a fail-closed flag."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def register_parameters(
        self,
        parameters: MasteryV2Parameters,
    ) -> str:
        payload = json.dumps(
            asdict(parameters),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO mastery_v2_parameter_versions (
                    parameter_version,
                    schema_version,
                    formula_version,
                    parameters_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    parameters.version,
                    parameters.schema_version,
                    parameters.formula_version,
                    payload,
                ),
            )
            row = connection.execute(
                """
                SELECT parameters_json
                FROM mastery_v2_parameter_versions
                WHERE parameter_version = ?
                """,
                (parameters.version,),
            ).fetchone()
            if row is None or str(row["parameters_json"]) != payload:
                raise MasteryRolloutError(
                    "parameter version content does not match"
                )
        return parameters.version

    def get_parameters(self, parameter_version: str) -> MasteryV2Parameters:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT parameters_json
                FROM mastery_v2_parameter_versions
                WHERE parameter_version = ?
                """,
                (str(parameter_version or "").strip(),),
            ).fetchone()
        if row is None:
            raise KeyError(parameter_version)
        raw = json.loads(str(row["parameters_json"]))
        return MasteryV2Parameters(**raw)

    def list_parameters(self) -> tuple[dict[str, Any], ...]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT parameter_version, parameters_json, created_at
                FROM mastery_v2_parameter_versions
                ORDER BY created_at, parameter_version
                """
            ).fetchall()
        return tuple(
            {
                "parameter_version": str(row["parameter_version"]),
                "parameters": json.loads(str(row["parameters_json"])),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        )

    def record_evaluation(
        self,
        report: MasteryComparisonReport,
    ) -> MasteryEvaluationGate:
        self.register_parameters(
            self.get_parameters(report.parameter_version)
        )
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT parameter_version, as_of, item_count,
                       required_review_count, max_absolute_delta
                FROM mastery_v2_evaluations
                WHERE evaluation_id = ?
                """,
                (report.evaluation_id,),
            ).fetchone()
            expected = (
                report.parameter_version,
                report.as_of,
                len(report.items),
                report.required_review_count,
                report.maximum_absolute_delta,
            )
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO mastery_v2_evaluations (
                        evaluation_id,
                        parameter_version,
                        as_of,
                        item_count,
                        required_review_count,
                        max_absolute_delta
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (report.evaluation_id, *expected),
                )
                connection.executemany(
                    """
                    INSERT INTO mastery_v2_evaluation_items (
                        evaluation_id,
                        item_hash,
                        requires_review
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        (
                            report.evaluation_id,
                            item.item_hash,
                            int(item.requires_review),
                        )
                        for item in report.items
                    ),
                )
            else:
                actual = (
                    str(existing["parameter_version"]),
                    str(existing["as_of"]),
                    int(existing["item_count"]),
                    int(existing["required_review_count"]),
                    (
                        None
                        if existing["max_absolute_delta"] is None
                        else float(existing["max_absolute_delta"])
                    ),
                )
                if actual != expected:
                    raise MasteryRolloutError(
                        "evaluation identity content does not match"
                    )
        return self.evaluation_gate(report.evaluation_id)

    def review_item(
        self,
        *,
        evaluation_id: str,
        item_hash: str,
        decision: Literal["accepted", "rejected"],
        teacher_ref: str,
        reason: str,
        expected_revision: int,
    ) -> MasteryEvaluationGate:
        clean_teacher = _required_text(teacher_ref, "teacher_ref")
        clean_reason = _required_text(reason, "reason")
        normalized_decision = str(decision or "").strip().casefold()
        if normalized_decision not in {"accepted", "rejected"}:
            raise ValueError("decision is invalid")
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            evaluation = connection.execute(
                """
                SELECT revision
                FROM mastery_v2_evaluations
                WHERE evaluation_id = ?
                """,
                (evaluation_id,),
            ).fetchone()
            if evaluation is None:
                raise MasteryEvaluationNotFound(evaluation_id)
            current_revision = int(evaluation["revision"])
            item = connection.execute(
                """
                SELECT item_hash
                FROM mastery_v2_evaluation_items
                WHERE evaluation_id = ? AND item_hash = ?
                """,
                (evaluation_id, item_hash),
            ).fetchone()
            if item is None:
                raise MasteryEvaluationItemNotFound(item_hash)
            existing = connection.execute(
                """
                SELECT decision, teacher_ref, reason
                FROM mastery_v2_spot_checks
                WHERE evaluation_id = ? AND item_hash = ?
                """,
                (evaluation_id, item_hash),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["decision"]) == normalized_decision
                    and str(existing["teacher_ref"]) == clean_teacher
                    and str(existing["reason"]) == clean_reason
                ):
                    return self._evaluation_gate(connection, evaluation_id)
                raise MasterySpotCheckConflict(
                    "spot check is immutable; create a new evaluation"
                )
            if int(expected_revision) != current_revision:
                raise MasteryRolloutRevisionConflict(
                    int(expected_revision),
                    current_revision,
                )
            connection.execute(
                """
                INSERT INTO mastery_v2_spot_checks (
                    evaluation_id,
                    item_hash,
                    decision,
                    teacher_ref,
                    reason
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    evaluation_id,
                    item_hash,
                    normalized_decision,
                    clean_teacher,
                    clean_reason,
                ),
            )
            connection.execute(
                """
                UPDATE mastery_v2_evaluations
                SET revision = revision + 1
                WHERE evaluation_id = ? AND revision = ?
                """,
                (evaluation_id, current_revision),
            )
            return self._evaluation_gate(connection, evaluation_id)

    def evaluation_gate(
        self,
        evaluation_id: str,
    ) -> MasteryEvaluationGate:
        with connect(self.db_path) as connection:
            return self._evaluation_gate(connection, evaluation_id)

    def get_state(self) -> MasteryRolloutState:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM mastery_v2_rollout_state
                WHERE singleton_id = 1
                """
            ).fetchone()
        if row is None:
            raise MasteryRolloutError("mastery rollout state is missing")
        return _state(row)

    def select_parameters(self) -> MasteryV2Parameters | None:
        state = self.get_state()
        if not state.enabled or state.active_parameter_version is None:
            return None
        return self.get_parameters(state.active_parameter_version)

    def update_state(
        self,
        *,
        enabled: bool,
        expected_revision: int,
        actor_ref: str,
        reason: str,
        evaluation_id: str | None = None,
    ) -> MasteryRolloutState:
        clean_actor = _required_text(actor_ref, "actor_ref")
        clean_reason = _required_text(reason, "reason")
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT *
                FROM mastery_v2_rollout_state
                WHERE singleton_id = 1
                """
            ).fetchone()
            if row is None:
                raise MasteryRolloutError("mastery rollout state is missing")
            current = _state(row)
            if int(expected_revision) != current.revision:
                raise MasteryRolloutRevisionConflict(
                    int(expected_revision),
                    current.revision,
                )
            parameter_version: str | None = None
            approved_evaluation_id: str | None = None
            if enabled:
                clean_evaluation_id = _required_text(
                    evaluation_id,
                    "evaluation_id",
                )
                gate = self._evaluation_gate(
                    connection,
                    clean_evaluation_id,
                )
                if not gate.passed:
                    raise MasteryRolloutGateBlocked(
                        "required spot checks are not all accepted"
                    )
                parameter_version = gate.parameter_version
                approved_evaluation_id = clean_evaluation_id
            next_revision = current.revision + 1
            connection.execute(
                """
                UPDATE mastery_v2_rollout_state
                SET enabled = ?,
                    active_parameter_version = ?,
                    approved_evaluation_id = ?,
                    revision = ?,
                    updated_by = ?,
                    reason = ?,
                    updated_at = datetime('now','localtime')
                WHERE singleton_id = 1 AND revision = ?
                """,
                (
                    int(enabled),
                    parameter_version,
                    approved_evaluation_id,
                    next_revision,
                    clean_actor,
                    clean_reason,
                    current.revision,
                ),
            )
            connection.execute(
                """
                INSERT INTO mastery_v2_rollout_events (
                    enabled,
                    parameter_version,
                    evaluation_id,
                    actor_ref,
                    reason,
                    expected_revision,
                    resulting_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(enabled),
                    parameter_version,
                    approved_evaluation_id,
                    clean_actor,
                    clean_reason,
                    current.revision,
                    next_revision,
                ),
            )
            updated = connection.execute(
                """
                SELECT *
                FROM mastery_v2_rollout_state
                WHERE singleton_id = 1
                """
            ).fetchone()
            if updated is None:
                raise MasteryRolloutError("mastery rollout state disappeared")
            return _state(updated)

    @staticmethod
    def _evaluation_gate(
        connection: sqlite3.Connection,
        evaluation_id: str,
    ) -> MasteryEvaluationGate:
        row = connection.execute(
            """
            SELECT
                e.evaluation_id,
                e.parameter_version,
                e.revision,
                e.required_review_count,
                SUM(
                    CASE
                        WHEN i.requires_review = 1
                         AND c.decision = 'accepted' THEN 1
                        ELSE 0
                    END
                ) AS accepted_count,
                SUM(
                    CASE
                        WHEN i.requires_review = 1
                         AND c.decision = 'rejected' THEN 1
                        ELSE 0
                    END
                ) AS rejected_count
            FROM mastery_v2_evaluations e
            JOIN mastery_v2_evaluation_items i
              ON i.evaluation_id = e.evaluation_id
            LEFT JOIN mastery_v2_spot_checks c
              ON c.evaluation_id = i.evaluation_id
             AND c.item_hash = i.item_hash
            WHERE e.evaluation_id = ?
            GROUP BY
                e.evaluation_id,
                e.parameter_version,
                e.revision,
                e.required_review_count
            """,
            (evaluation_id,),
        ).fetchone()
        if row is None:
            raise MasteryEvaluationNotFound(evaluation_id)
        required = int(row["required_review_count"])
        accepted = int(row["accepted_count"] or 0)
        rejected = int(row["rejected_count"] or 0)
        pending = max(required - accepted - rejected, 0)
        return MasteryEvaluationGate(
            evaluation_id=str(row["evaluation_id"]),
            parameter_version=str(row["parameter_version"]),
            revision=int(row["revision"]),
            required_review_count=required,
            accepted_count=accepted,
            rejected_count=rejected,
            pending_count=pending,
            passed=required > 0 and accepted == required and rejected == 0,
        )


def _state(row: sqlite3.Row) -> MasteryRolloutState:
    enabled = bool(row["enabled"])
    return MasteryRolloutState(
        enabled=enabled,
        active_mode="v2" if enabled else "v1",
        active_parameter_version=(
            None
            if row["active_parameter_version"] is None
            else str(row["active_parameter_version"])
        ),
        approved_evaluation_id=(
            None
            if row["approved_evaluation_id"] is None
            else str(row["approved_evaluation_id"])
        ),
        revision=int(row["revision"]),
        updated_by=(
            None if row["updated_by"] is None else str(row["updated_by"])
        ),
        reason=None if row["reason"] is None else str(row["reason"]),
        updated_at=str(row["updated_at"]),
    )


def _required_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must be nonblank")
    if len(text) > 500:
        raise ValueError(f"{field} is too long")
    return text


__all__ = [
    "MasteryEvaluationGate",
    "MasteryEvaluationItemNotFound",
    "MasteryEvaluationNotFound",
    "MasteryRolloutError",
    "MasteryRolloutGateBlocked",
    "MasteryRolloutRepository",
    "MasteryRolloutRevisionConflict",
    "MasteryRolloutState",
    "MasterySpotCheckConflict",
]
