from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, replace
from uuid import uuid4

from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepNotFoundError,
    TeachingPrepValidationError,
)
from backend.teaching_prep.domain.models import (
    DuplicateExerciseSuggestion,
    ExerciseCandidate,
    ExerciseRegion,
)
from backend.teaching_prep.infrastructure.database import TeachingPrepDatabase


@dataclass(frozen=True, slots=True)
class ExerciseRegionDraft:
    material_unit_id: str
    crop: dict[str, float]


@dataclass(frozen=True, slots=True)
class ExerciseRegionPreviewRecord:
    region_id: str
    material_unit_id: str
    crop: dict[str, float]
    source_version_sha256: str


class ExerciseCandidateRepository:
    def __init__(self, database: TeachingPrepDatabase) -> None:
        self._database = database

    def create(
        self,
        *,
        request_token: str,
        lesson_node_id: str,
        question_number: str | None,
        content_label: str | None,
        difficulty: str,
        classroom_use: str,
        estimated_minutes: int | None,
        teaching_focus: str | None,
        teacher_note: str | None,
        selection_status: str,
        answer_status: str,
        question_regions: tuple[ExerciseRegionDraft, ...],
        answer_regions: tuple[ExerciseRegionDraft, ...],
    ) -> tuple[ExerciseCandidate, bool]:
        values = _request_values(
            lesson_node_id=lesson_node_id,
            question_number=question_number,
            content_label=content_label,
            difficulty=difficulty,
            classroom_use=classroom_use,
            estimated_minutes=estimated_minutes,
            teaching_focus=teaching_focus,
            teacher_note=teacher_note,
            selection_status=selection_status,
            answer_status=answer_status,
            question_regions=question_regions,
            answer_regions=answer_regions,
        )
        request_hash = _request_hash(values)
        with self._database.connect(immediate=True) as connection:
            existing = connection.execute(
                """
                SELECT id, request_hash
                FROM exercise_candidates
                WHERE request_token = ?
                """,
                (request_token,),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise TeachingPrepConflictError(
                        "request token was reused for a different exercise"
                    )
                candidate_id = str(existing["id"])
                created = False
            else:
                _require_active_lesson(connection, lesson_node_id)
                normalized_answer_status = _validate_answer_status(
                    answer_status,
                    has_answer_regions=bool(answer_regions),
                )
                validated_regions = _validate_regions(
                    connection,
                    lesson_node_id=lesson_node_id,
                    question_regions=question_regions,
                    answer_regions=answer_regions,
                )
                candidate_id = uuid4().hex
                connection.execute(
                    """
                    INSERT INTO exercise_candidates (
                        id,
                        request_token,
                        request_hash,
                        lesson_node_id,
                        question_number,
                        content_label,
                        difficulty,
                        classroom_use,
                        estimated_minutes,
                        teaching_focus,
                        teacher_note,
                        selection_status,
                        answer_status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate_id,
                        request_token,
                        request_hash,
                        lesson_node_id,
                        question_number,
                        content_label,
                        difficulty,
                        classroom_use,
                        estimated_minutes,
                        teaching_focus,
                        teacher_note,
                        selection_status,
                        normalized_answer_status,
                    ),
                )
                _insert_regions(connection, candidate_id, validated_regions)
                created = True
        return self.get(candidate_id), created

    def get(self, candidate_id: str) -> ExerciseCandidate:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM exercise_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise TeachingPrepNotFoundError(
                    "exercise candidate was not found"
                )
            lesson_id = str(row["lesson_node_id"])
            candidates = _load_candidates(
                connection,
                lesson_id,
                condition="candidate.id = ?",
                parameters=(candidate_id,),
            )
            all_rows = connection.execute(
                """
                SELECT *
                FROM exercise_candidates
                WHERE lesson_node_id = ?
                ORDER BY created_at, id
                """,
                (lesson_id,),
            ).fetchall()
            source_ids = _question_source_ids(connection, lesson_id)
        return _with_duplicate_suggestions(
            candidates[0],
            all_rows=all_rows,
            source_ids=source_ids,
        )

    def list_for_lesson(
        self,
        lesson_node_id: str,
    ) -> tuple[ExerciseCandidate, ...]:
        with self._database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM lesson_nodes WHERE id = ?",
                (lesson_node_id,),
            ).fetchone() is None:
                raise TeachingPrepNotFoundError(
                    "lesson node was not found"
                )
            candidates = _load_candidates(connection, lesson_node_id)
            rows = connection.execute(
                """
                SELECT *
                FROM exercise_candidates
                WHERE lesson_node_id = ?
                ORDER BY created_at, id
                """,
                (lesson_node_id,),
            ).fetchall()
            source_ids = _question_source_ids(connection, lesson_node_id)
        return tuple(
            _with_duplicate_suggestions(
                candidate,
                all_rows=rows,
                source_ids=source_ids,
            )
            for candidate in candidates
        )

    def update(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        question_number: str | None,
        content_label: str | None,
        difficulty: str,
        classroom_use: str,
        estimated_minutes: int | None,
        teaching_focus: str | None,
        teacher_note: str | None,
        selection_status: str,
        answer_status: str,
        question_regions: tuple[ExerciseRegionDraft, ...],
        answer_regions: tuple[ExerciseRegionDraft, ...],
        is_active: bool,
    ) -> ExerciseCandidate:
        with self._database.connect(immediate=True) as connection:
            current = connection.execute(
                """
                SELECT lesson_node_id, revision
                FROM exercise_candidates
                WHERE id = ?
                """,
                (candidate_id,),
            ).fetchone()
            if current is None:
                raise TeachingPrepNotFoundError(
                    "exercise candidate was not found"
                )
            if int(current["revision"]) != expected_revision:
                raise TeachingPrepConflictError(
                    "exercise candidate changed; refresh and try again"
                )
            lesson_node_id = str(current["lesson_node_id"])
            validated_regions = _validate_regions(
                connection,
                lesson_node_id=lesson_node_id,
                question_regions=question_regions,
                answer_regions=answer_regions,
            )
            old_fingerprint = _stored_region_fingerprint(
                connection,
                candidate_id,
            )
            new_fingerprint = _validated_region_fingerprint(
                validated_regions
            )
            if old_fingerprint != new_fingerprint:
                normalized_answer_status = (
                    "candidate" if answer_regions else "missing"
                )
            else:
                normalized_answer_status = _validate_answer_status(
                    answer_status,
                    has_answer_regions=bool(answer_regions),
                )
            cursor = connection.execute(
                """
                UPDATE exercise_candidates
                SET question_number = ?,
                    content_label = ?,
                    difficulty = ?,
                    classroom_use = ?,
                    estimated_minutes = ?,
                    teaching_focus = ?,
                    teacher_note = ?,
                    selection_status = ?,
                    answer_status = ?,
                    is_active = ?,
                    revision = revision + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND revision = ?
                """,
                (
                    question_number,
                    content_label,
                    difficulty,
                    classroom_use,
                    estimated_minutes,
                    teaching_focus,
                    teacher_note,
                    selection_status,
                    normalized_answer_status,
                    int(is_active),
                    candidate_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise TeachingPrepConflictError(
                    "exercise candidate changed; refresh and try again"
                )
            connection.execute(
                "DELETE FROM exercise_regions WHERE exercise_candidate_id = ?",
                (candidate_id,),
            )
            _insert_regions(connection, candidate_id, validated_regions)
        return self.get(candidate_id)

    def region_preview_record(
        self,
        region_id: str,
    ) -> ExerciseRegionPreviewRecord:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    region.id,
                    region.material_unit_id,
                    region.crop_json,
                    region.source_version_sha256,
                    version.content_sha256
                FROM exercise_regions AS region
                JOIN material_units AS unit
                    ON unit.id = region.material_unit_id
                JOIN material_versions AS version
                    ON version.id = unit.material_version_id
                WHERE region.id = ?
                """,
                (region_id,),
            ).fetchone()
        if row is None:
            raise TeachingPrepNotFoundError(
                "exercise region was not found"
            )
        if str(row["source_version_sha256"]) != str(row["content_sha256"]):
            raise TeachingPrepConflictError(
                "exercise region source version changed"
            )
        return ExerciseRegionPreviewRecord(
            region_id=str(row["id"]),
            material_unit_id=str(row["material_unit_id"]),
            crop=_load_crop(str(row["crop_json"])),
            source_version_sha256=str(row["source_version_sha256"]),
        )


def _request_values(**values: object) -> dict[str, object]:
    return dict(values)


def _request_hash(values: dict[str, object]) -> str:
    payload = json.dumps(
        values,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=asdict,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require_active_lesson(
    connection: sqlite3.Connection,
    lesson_node_id: str,
) -> None:
    row = connection.execute(
        """
        SELECT node_type
        FROM lesson_nodes
        WHERE id = ? AND is_active = 1
        """,
        (lesson_node_id,),
    ).fetchone()
    if row is None:
        raise TeachingPrepNotFoundError("active lesson node was not found")
    if str(row["node_type"]) != "lesson":
        raise TeachingPrepValidationError(
            "exercise candidates can only attach to lesson nodes"
        )


def _validate_answer_status(
    answer_status: str,
    *,
    has_answer_regions: bool,
) -> str:
    if has_answer_regions and answer_status == "missing":
        raise TeachingPrepValidationError(
            "answer regions cannot use missing answer status"
        )
    if not has_answer_regions and answer_status != "missing":
        raise TeachingPrepValidationError(
            "answer status requires at least one answer region"
        )
    return answer_status


def _validate_regions(
    connection: sqlite3.Connection,
    *,
    lesson_node_id: str,
    question_regions: tuple[ExerciseRegionDraft, ...],
    answer_regions: tuple[ExerciseRegionDraft, ...],
) -> tuple[tuple[str, int, ExerciseRegionDraft, str], ...]:
    if not question_regions:
        raise TeachingPrepValidationError(
            "exercise requires at least one question region"
        )
    result: list[tuple[str, int, ExerciseRegionDraft, str]] = []
    for role, regions in (
        ("question", question_regions),
        ("answer", answer_regions),
    ):
        for sequence, region in enumerate(regions, start=1):
            row = connection.execute(
                """
                SELECT
                    unit.material_version_id,
                    unit.unit_index,
                    unit.source_version_sha256,
                    version.content_sha256
                FROM material_units AS unit
                JOIN material_versions AS version
                    ON version.id = unit.material_version_id
                WHERE unit.id = ?
                  AND EXISTS (
                    SELECT 1
                    FROM lesson_material_links AS link
                    WHERE link.lesson_node_id = ?
                      AND link.material_version_id = unit.material_version_id
                      AND link.is_active = 1
                      AND link.confirmation_status = 'confirmed'
                      AND unit.unit_index BETWEEN
                          link.start_unit AND link.end_unit
                  )
                """,
                (region.material_unit_id, lesson_node_id),
            ).fetchone()
            if row is None:
                raise TeachingPrepValidationError(
                    "exercise region is outside confirmed lesson materials"
                )
            source_sha = str(row["source_version_sha256"])
            if source_sha != str(row["content_sha256"]):
                raise TeachingPrepConflictError(
                    "exercise region source version changed"
                )
            result.append((role, sequence, region, source_sha))
    return tuple(result)


def _insert_regions(
    connection: sqlite3.Connection,
    candidate_id: str,
    regions: tuple[tuple[str, int, ExerciseRegionDraft, str], ...],
) -> None:
    for role, sequence, region, source_sha in regions:
        connection.execute(
            """
            INSERT INTO exercise_regions (
                id,
                exercise_candidate_id,
                region_role,
                material_unit_id,
                sequence,
                crop_json,
                source_version_sha256
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex,
                candidate_id,
                role,
                region.material_unit_id,
                sequence,
                json.dumps(region.crop, sort_keys=True, separators=(",", ":")),
                source_sha,
            ),
        )


def _stored_region_fingerprint(
    connection: sqlite3.Connection,
    candidate_id: str,
) -> tuple[tuple[object, ...], ...]:
    rows = connection.execute(
        """
        SELECT
            region_role,
            sequence,
            material_unit_id,
            crop_json,
            source_version_sha256
        FROM exercise_regions
        WHERE exercise_candidate_id = ?
        ORDER BY region_role DESC, sequence
        """,
        (candidate_id,),
    ).fetchall()
    return tuple(
        (
            str(row["region_role"]),
            int(row["sequence"]),
            str(row["material_unit_id"]),
            str(row["crop_json"]),
            str(row["source_version_sha256"]),
        )
        for row in rows
    )


def _validated_region_fingerprint(
    regions: tuple[tuple[str, int, ExerciseRegionDraft, str], ...],
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            role,
            sequence,
            region.material_unit_id,
            json.dumps(region.crop, sort_keys=True, separators=(",", ":")),
            source_sha,
        )
        for role, sequence, region, source_sha in sorted(
            regions,
            key=lambda item: (
                0 if item[0] == "question" else 1,
                item[1],
            ),
        )
    )


def _load_candidates(
    connection: sqlite3.Connection,
    lesson_node_id: str,
    *,
    condition: str | None = None,
    parameters: tuple[object, ...] = (),
) -> tuple[ExerciseCandidate, ...]:
    where = "candidate.lesson_node_id = ?"
    values: tuple[object, ...] = (lesson_node_id,)
    if condition is not None:
        where += f" AND {condition}"
        values += parameters
    rows = connection.execute(
        f"""
        SELECT candidate.*
        FROM exercise_candidates AS candidate
        WHERE {where}
        ORDER BY candidate.created_at, candidate.id
        """,
        values,
    ).fetchall()
    return tuple(
        _candidate(
            row,
            regions=_load_regions(connection, str(row["id"])),
        )
        for row in rows
    )


def _load_regions(
    connection: sqlite3.Connection,
    candidate_id: str,
) -> tuple[ExerciseRegion, ...]:
    rows = connection.execute(
        """
        SELECT
            region.*,
            unit.material_version_id,
            unit.unit_index,
            source.display_name AS material_name
        FROM exercise_regions AS region
        JOIN material_units AS unit
            ON unit.id = region.material_unit_id
        JOIN material_versions AS version
            ON version.id = unit.material_version_id
        JOIN material_sources AS source
            ON source.id = version.source_id
        WHERE region.exercise_candidate_id = ?
        ORDER BY region.region_role DESC, region.sequence
        """,
        (candidate_id,),
    ).fetchall()
    return tuple(
        ExerciseRegion(
            id=str(row["id"]),
            region_role=str(row["region_role"]),
            material_unit_id=str(row["material_unit_id"]),
            material_version_id=str(row["material_version_id"]),
            material_name=str(row["material_name"]),
            unit_index=int(row["unit_index"]),
            crop=_load_crop(str(row["crop_json"])),
            source_version_sha256=str(row["source_version_sha256"]),
            sequence=int(row["sequence"]),
            preview_url=(
                f"/api/teaching-prep/exercise-regions/{row['id']}/preview"
            ),
        )
        for row in rows
    )


def _load_crop(value: str) -> dict[str, float]:
    candidate = json.loads(value)
    if not isinstance(candidate, dict):
        raise RuntimeError("stored exercise crop is invalid")
    return {
        key: float(candidate[key])
        for key in ("x0", "y0", "x1", "y1")
    }


def _candidate(
    row: sqlite3.Row,
    *,
    regions: tuple[ExerciseRegion, ...],
) -> ExerciseCandidate:
    return ExerciseCandidate(
        id=str(row["id"]),
        lesson_node_id=str(row["lesson_node_id"]),
        question_number=(
            str(row["question_number"])
            if row["question_number"] is not None
            else None
        ),
        content_label=(
            str(row["content_label"])
            if row["content_label"] is not None
            else None
        ),
        difficulty=str(row["difficulty"]),
        classroom_use=str(row["classroom_use"]),
        estimated_minutes=(
            int(row["estimated_minutes"])
            if row["estimated_minutes"] is not None
            else None
        ),
        teaching_focus=(
            str(row["teaching_focus"])
            if row["teaching_focus"] is not None
            else None
        ),
        teacher_note=(
            str(row["teacher_note"])
            if row["teacher_note"] is not None
            else None
        ),
        selection_status=str(row["selection_status"]),
        answer_status=str(row["answer_status"]),
        question_regions=tuple(
            item for item in regions if item.region_role == "question"
        ),
        answer_regions=tuple(
            item for item in regions if item.region_role == "answer"
        ),
        duplicate_suggestions=(),
        is_active=bool(row["is_active"]),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _question_source_ids(
    connection: sqlite3.Connection,
    lesson_node_id: str,
) -> dict[str, set[str]]:
    rows = connection.execute(
        """
        SELECT DISTINCT
            candidate.id AS candidate_id,
            version.source_id
        FROM exercise_candidates AS candidate
        JOIN exercise_regions AS region
            ON region.exercise_candidate_id = candidate.id
           AND region.region_role = 'question'
        JOIN material_units AS unit
            ON unit.id = region.material_unit_id
        JOIN material_versions AS version
            ON version.id = unit.material_version_id
        WHERE candidate.lesson_node_id = ?
        """,
        (lesson_node_id,),
    ).fetchall()
    result: dict[str, set[str]] = {}
    for row in rows:
        result.setdefault(str(row["candidate_id"]), set()).add(
            str(row["source_id"])
        )
    return result


def _with_duplicate_suggestions(
    candidate: ExerciseCandidate,
    *,
    all_rows: list[sqlite3.Row],
    source_ids: dict[str, set[str]],
) -> ExerciseCandidate:
    suggestions: list[DuplicateExerciseSuggestion] = []
    current_label = _similarity_key(candidate.content_label)
    current_number = _similarity_key(candidate.question_number)
    for row in all_rows:
        other_id = str(row["id"])
        if other_id == candidate.id or not bool(row["is_active"]):
            continue
        basis: list[str] = []
        other_label = _similarity_key(
            str(row["content_label"])
            if row["content_label"] is not None
            else None
        )
        if current_label and len(current_label) >= 4 and current_label == other_label:
            basis.append("same_teacher_clue")
        other_number = _similarity_key(
            str(row["question_number"])
            if row["question_number"] is not None
            else None
        )
        if (
            current_number
            and current_number == other_number
            and source_ids.get(candidate.id, set())
            & source_ids.get(other_id, set())
        ):
            basis.append("same_question_number_and_source")
        if basis:
            suggestions.append(
                DuplicateExerciseSuggestion(
                    candidate_id=other_id,
                    question_number=(
                        str(row["question_number"])
                        if row["question_number"] is not None
                        else None
                    ),
                    content_label=(
                        str(row["content_label"])
                        if row["content_label"] is not None
                        else None
                    ),
                    basis=tuple(basis),
                )
            )
    return replace(
        candidate,
        duplicate_suggestions=tuple(suggestions),
    )


def _similarity_key(value: str | None) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").casefold())


__all__ = [
    "ExerciseCandidateRepository",
    "ExerciseRegionDraft",
    "ExerciseRegionPreviewRecord",
]
