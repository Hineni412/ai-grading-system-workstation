from __future__ import annotations

import base64
import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from types import FunctionType

from backend.performance.metrics import instrument_sqlite_connection
from db_manager import DBManager
from question_bank.database.schema import initialize_database


_BATCH_SIZE = 1_000
_GENERATED_TIME = "generated-time"
_SQLITE_CONNECT = sqlite3.connect
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


@dataclass(frozen=True, slots=True)
class ScaleDefinition:
    name: str
    sessions: int
    students: int
    questions_per_session: int
    grading_details: int
    question_bank_questions: int
    training_tasks: int
    backups: int

    @property
    def counts(self) -> tuple[int, int, int, int, int, int, int]:
        return (
            self.sessions,
            self.students,
            self.questions_per_session,
            self.grading_details,
            self.question_bank_questions,
            self.training_tasks,
            self.backups,
        )


SMALL = ScaleDefinition("small", 1, 30, 10, 300, 200, 10, 5)
MEDIUM = ScaleDefinition("medium", 5, 200, 20, 20_000, 2_000, 100, 50)
LARGE = ScaleDefinition("large", 10, 500, 30, 150_000, 10_000, 500, 100)
SCALES = (SMALL, MEDIUM, LARGE)


@dataclass(frozen=True, slots=True, repr=False)
class BenchmarkPaths:
    project_root: Path = field(repr=False)

    @classmethod
    def from_root(cls, root: Path) -> BenchmarkPaths:
        return cls(project_root=Path(root).resolve())

    @property
    def data_root(self) -> Path:
        return self.project_root / "generated-data"

    @property
    def databases_dir(self) -> Path:
        return self.data_root / "databases"

    @property
    def db_path(self) -> Path:
        return self.databases_dir / "grading_system.db"

    @property
    def qb_db_path(self) -> Path:
        return self.databases_dir / "question_bank.db"

    @property
    def config_dir(self) -> Path:
        return self.data_root / "config"

    @property
    def upload_config_dir(self) -> Path:
        return self.config_dir / "uploaded"

    @property
    def api_profiles_path(self) -> Path:
        return self.config_dir / "generated-api-profiles.json"

    @property
    def ops_state_dir(self) -> Path:
        return self.data_root / "generated-ops"

    @property
    def templates_dir(self) -> Path:
        return self.data_root / "templates"

    @property
    def annotated_dir(self) -> Path:
        return self.data_root / "annotated"

    @property
    def reports_dir(self) -> Path:
        return self.data_root / "reports"

    @property
    def backups_dir(self) -> Path:
        return self.data_root / "backups"

    @property
    def outputs_dir(self) -> Path:
        return self.data_root / "outputs"

    @property
    def exams_dir(self) -> Path:
        return self.data_root / "exams"

    @property
    def logs_dir(self) -> Path:
        return self.data_root / "logs"

    @property
    def qb_data_dir(self) -> Path:
        return self.data_root / "question_bank"

    @property
    def snapshots_dir(self) -> Path:
        return self.data_root / "snapshots"

    @property
    def version(self) -> str:
        return "v1.5.0-p1-26-generated"

    def ensure_directories(self) -> None:
        for directory in (
            self.data_root,
            self.databases_dir,
            self.config_dir,
            self.upload_config_dir,
            self.api_profiles_path.parent,
            self.ops_state_dir,
            self.templates_dir,
            self.annotated_dir,
            self.reports_dir,
            self.backups_dir,
            self.outputs_dir,
            self.exams_dir,
            self.logs_dir,
            self.qb_data_dir,
            self.snapshots_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def __repr__(self) -> str:
        return "BenchmarkPaths(generated=True)"


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    scale_name: str
    seed: int
    table_counts: tuple[tuple[str, int], ...]
    grading_database_bytes: int
    question_bank_database_bytes: int
    backup_database_bytes: int
    generated_asset_bytes: int

    @property
    def scale(self) -> str:
        return self.scale_name

    @property
    def counts(self) -> tuple[tuple[str, int], ...]:
        return self.table_counts

    @property
    def logical_counts(self) -> tuple[tuple[str, int], ...]:
        return self.table_counts

    @property
    def database_bytes(self) -> tuple[tuple[str, int], ...]:
        return (
            ("grading", self.grading_database_bytes),
            ("question_bank", self.question_bank_database_bytes),
            ("backups", self.backup_database_bytes),
        )

    @property
    def grading_db_bytes(self) -> int:
        return self.grading_database_bytes

    @property
    def question_bank_db_bytes(self) -> int:
        return self.question_bank_database_bytes

    @property
    def asset_bytes(self) -> int:
        return self.generated_asset_bytes


@dataclass(frozen=True, slots=True)
class BenchmarkDataset:
    paths: BenchmarkPaths = field(repr=False)
    manifest: DatasetManifest
    representative_question_id: int
    representative_task_id: int
    knowledge_key: str


def build_benchmark_dataset(
    root: Path,
    scale: ScaleDefinition,
    *,
    seed: int = 126,
) -> BenchmarkDataset:
    _validate_scale(scale)
    paths = BenchmarkPaths.from_root(root)
    paths.ensure_directories()
    if paths.db_path.exists() or paths.qb_db_path.exists():
        raise FileExistsError("generated benchmark databases already exist")

    with _explicit_path_manager(paths):
        _initialize_grading_database(paths.db_path)
    _initialize_question_bank_database(paths.qb_db_path)

    _write_session_rubrics(paths, scale)
    _populate_grading_database(paths.db_path, scale)
    asset_path, preview_path = _write_generated_assets(paths)
    representative_question_id = _populate_question_bank_database(
        paths.qb_db_path,
        scale,
        seed=int(seed),
        asset_path=asset_path.relative_to(paths.data_root).as_posix(),
        preview_path=preview_path.relative_to(paths.data_root).as_posix(),
    )
    representative_task_id = 1
    backup_paths = _write_backup_metadata(paths, scale.backups, int(seed))

    _checkpoint(paths.db_path)
    _checkpoint(paths.qb_db_path)
    actual_counts = _actual_counts(paths)
    expected_counts = _expected_counts(scale)
    if actual_counts != expected_counts:
        raise RuntimeError("generated benchmark table counts do not match the scale")
    _assert_database_valid(paths.db_path, "grading")
    _assert_database_valid(paths.qb_db_path, "question_bank")

    manifest = DatasetManifest(
        scale_name=scale.name,
        seed=int(seed),
        table_counts=tuple(actual_counts.items()),
        grading_database_bytes=paths.db_path.stat().st_size,
        question_bank_database_bytes=paths.qb_db_path.stat().st_size,
        backup_database_bytes=sum(path.stat().st_size for path in backup_paths),
        generated_asset_bytes=asset_path.stat().st_size + preview_path.stat().st_size,
    )
    return BenchmarkDataset(
        paths=paths,
        manifest=manifest,
        representative_question_id=representative_question_id,
        representative_task_id=representative_task_id,
        knowledge_key="knowledge_point:knowledge-01",
    )


def _validate_scale(scale: ScaleDefinition) -> None:
    positive = (
        scale.sessions,
        scale.students,
        scale.questions_per_session,
        scale.question_bank_questions,
        scale.training_tasks,
    )
    if not scale.name or any(value <= 0 for value in positive):
        raise ValueError("benchmark scale requires a name and positive core counts")
    if scale.grading_details < 0 or scale.backups < 0:
        raise ValueError("benchmark detail and backup counts must not be negative")
    if scale.question_bank_questions < scale.questions_per_session:
        raise ValueError("question bank must cover every session question")


@contextmanager
def _explicit_path_manager(paths: BenchmarkPaths) -> Iterator[None]:
    import path_manager

    previous = path_manager._instance
    path_manager._instance = paths
    try:
        yield
    finally:
        path_manager._instance = previous


def _initialize_grading_database(db_path: Path) -> None:
    manager = DBManager(db_path)

    def configured_connection() -> object:
        return _configured_sqlite_connection(db_path)

    manager._connect = configured_connection  # type: ignore[method-assign]
    manager.initialize()


def _initialize_question_bank_database(db_path: Path) -> None:
    isolated_globals = dict(initialize_database.__globals__)
    isolated_globals["connect"] = _configured_sqlite_connection
    isolated_initialize = FunctionType(
        initialize_database.__code__,
        isolated_globals,
        initialize_database.__name__,
        initialize_database.__defaults__,
        initialize_database.__closure__,
    )
    isolated_initialize.__kwdefaults__ = initialize_database.__kwdefaults__
    isolated_initialize(db_path, seed_skills=False)


@contextmanager
def _configured_sqlite_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = _SQLITE_CONNECT(db_path)
    try:
        configured = instrument_sqlite_connection(conn)
        configured.row_factory = sqlite3.Row
        configured.execute("PRAGMA foreign_keys = ON")
        configured.execute("PRAGMA busy_timeout = 5000")
        configured.execute("PRAGMA journal_mode = WAL")
        try:
            yield configured
            configured.commit()
        except Exception:
            configured.rollback()
            raise
    finally:
        conn.close()


def _populate_grading_database(db_path: Path, scale: ScaleDefinition) -> None:
    result_count = scale.sessions * scale.students
    with _sqlite_connection(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        _insert_batches(
            conn,
            "INSERT INTO students (id, student_code, name, class_name, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                (
                    student_id,
                    f"GEN-{student_id:05d}",
                    f"GEN-{student_id:05d}",
                    "CLASS-001",
                    _GENERATED_TIME,
                )
                for student_id in range(1, scale.students + 1)
            ),
        )
        _insert_batches(
            conn,
            "INSERT INTO grading_sessions ("
            "id, session_name, rubric_path, answer_key_path, status, created_at, updated_at, "
            "question_bank_sync_state, question_bank_sync_details_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    session_id,
                    f"generated-session-{session_id:03d}",
                    f"generated-rubric-{session_id:03d}.json",
                    f"generated-answer-{session_id:03d}.json",
                    "completed",
                    _GENERATED_TIME,
                    _GENERATED_TIME,
                    "ready",
                    "{}",
                )
                for session_id in range(1, scale.sessions + 1)
            ),
        )
        _insert_batches(
            conn,
            "INSERT INTO exam_papers ("
            "id, session_id, front_image, back_image, ocr_name, student_id, match_status, "
            "processing_status, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            _paper_rows(scale),
        )
        _insert_batches(
            conn,
            "INSERT INTO session_results ("
            "id, session_id, student_id, paper_id, total_score, student_score, "
            "needs_human_review, raw_json, graded_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            _result_rows(scale),
        )
        _insert_batches(
            conn,
            "INSERT INTO session_details ("
            "id, result_id, question_id, score_awarded, deduction_reason, knowledge_id, "
            "knowledge_ids, error_category, error_summary, confidence_score, secondary_errors_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            _detail_rows(scale, result_count),
        )


def _write_session_rubrics(paths: BenchmarkPaths, scale: ScaleDefinition) -> None:
    questions = [
        {
            "question_id": f"Q{question_index:03d}",
            "max_score": 10.0,
            "knowledge_id": f"knowledge-{question_index % 50:02d}",
            "knowledge_ids": [f"knowledge-{question_index % 50:02d}"],
        }
        for question_index in range(1, scale.questions_per_session + 1)
    ]
    payload = json.dumps(
        {"questions": questions},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    for session_id in range(1, scale.sessions + 1):
        (paths.data_root / f"generated-rubric-{session_id:03d}.json").write_text(
            payload,
            encoding="utf-8",
        )


def _paper_rows(scale: ScaleDefinition) -> Iterator[tuple[object, ...]]:
    paper_id = 0
    for session_id in range(1, scale.sessions + 1):
        for student_id in range(1, scale.students + 1):
            paper_id += 1
            yield (
                paper_id,
                session_id,
                f"generated-front-{paper_id:06d}.png",
                f"generated-back-{paper_id:06d}.png",
                f"GEN-{student_id:05d}",
                student_id,
                "matched",
                "completed",
                _GENERATED_TIME,
            )


def _result_rows(scale: ScaleDefinition) -> Iterator[tuple[object, ...]]:
    result_id = 0
    for session_id in range(1, scale.sessions + 1):
        for student_id in range(1, scale.students + 1):
            result_id += 1
            yield (
                result_id,
                session_id,
                student_id,
                result_id,
                float(scale.questions_per_session * 10),
                float((student_id + session_id) % 101),
                0,
                "{}",
                _GENERATED_TIME,
            )


def _detail_rows(
    scale: ScaleDefinition,
    result_count: int,
) -> Iterator[tuple[object, ...]]:
    for detail_id in range(1, scale.grading_details + 1):
        result_id = (((detail_id - 1) // scale.questions_per_session) % result_count) + 1
        session_id = ((result_id - 1) // scale.students) + 1
        student_id = ((result_id - 1) % scale.students) + 1
        question_index = ((detail_id - 1) % scale.questions_per_session) + 1
        question_id = f"Q{question_index:03d}"
        knowledge_label = f"knowledge-{question_index % 50:02d}"
        score = float((student_id + session_id + question_index) % 11)
        yield (
            detail_id,
            result_id,
            question_id,
            score,
            "generated-deduction",
            knowledge_label,
            json.dumps([knowledge_label]),
            "generated-error",
            "generated-summary",
            1.0,
            "[]",
        )


def _write_generated_assets(paths: BenchmarkPaths) -> tuple[Path, Path]:
    asset_path = paths.qb_data_dir / "extracted_images" / "generated-question.png"
    preview_path = paths.qb_data_dir / "previews" / "generated-preview.png"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(_PNG_BYTES)
    preview_path.write_bytes(_PNG_BYTES)
    return asset_path, preview_path


def _populate_question_bank_database(
    db_path: Path,
    scale: ScaleDefinition,
    *,
    seed: int,
    asset_path: str,
    preview_path: str,
) -> int:
    with _sqlite_connection(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        _insert_batches(
            conn,
            "INSERT INTO questions ("
            "id, question_number, question_type, question_text, answer_text, source_file, "
            "image_paths, difficulty, typicality, reason, needs_review, has_images, "
            "needs_image_review, is_deleted, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    question_index,
                    f"Q{question_index:05d}",
                    "generated-type",
                    f"generated-question-{question_index:05d}",
                    f"generated-answer-{question_index:05d}",
                    f"generated-source-{question_index:05d}",
                    json.dumps([asset_path]) if question_index == 1 else "[]",
                    "generated-difficulty",
                    "generated-typicality",
                    "generated-reason",
                    0,
                    1 if question_index == 1 else 0,
                    0,
                    0,
                    _GENERATED_TIME,
                    _GENERATED_TIME,
                )
                for question_index in range(1, scale.question_bank_questions + 1)
            ),
        )
        _insert_batches(conn, _QUESTION_TAG_INSERT, _question_tag_rows(scale))
        _insert_batches(conn, _QUESTION_PREVIEW_INSERT, _preview_rows(preview_path))
        _insert_batches(conn, _QUESTION_LINK_INSERT, _question_link_rows(scale))
        _insert_batches(conn, _TRAINING_TASK_INSERT, _training_task_rows(scale, seed))
        _insert_batches(conn, _TRAINING_VARIANT_INSERT, _training_variant_rows(scale))
        _insert_batches(conn, _VARIANT_STUDENT_INSERT, _variant_student_rows(scale))
        _insert_batches(conn, _TRAINING_ITEM_INSERT, _training_item_rows(scale))
    return 1


_QUESTION_TAG_INSERT = (
    "INSERT INTO question_tags ("
    "id, question_id, tag_type, tag_value, confidence, source, model_name, created_at"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_QUESTION_PREVIEW_INSERT = (
    "INSERT INTO question_previews ("
    "id, question_id, preview_type, source_file, page_number, image_path, bbox_json, "
    "status, message, created_at, updated_at"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_QUESTION_LINK_INSERT = (
    "INSERT INTO grading_question_links ("
    "id, grading_session_id, source_question_id, bank_question_id, link_method, confidence, "
    "status, evidence_json, reviewed_by, reviewed_at, created_at, updated_at"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_TRAINING_TASK_INSERT = (
    "INSERT INTO training_tasks ("
    "id, task_code, created_by, scope_json, exam_scope_json, diagnosis_snapshot_json, "
    "generation_config_json, warnings_json, status, created_at, updated_at"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_TRAINING_VARIANT_INSERT = (
    "INSERT INTO training_variants ("
    "id, task_id, variant_key, variant_type, grouping_reason_json, diagnosis_snapshot_json, "
    "shortages_json, warnings_json, status, created_at, updated_at"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_VARIANT_STUDENT_INSERT = (
    "INSERT INTO variant_students ("
    "variant_id, student_id, student_name_snapshot, class_id_snapshot, created_at"
    ") VALUES (?, ?, ?, ?, ?)"
)
_TRAINING_ITEM_INSERT = (
    "INSERT INTO training_task_items ("
    "id, variant_id, task_item_code, bank_question_id, bank_question_fingerprint, item_order, "
    "stage, concept_snapshot_json, recommendation_snapshot_json, question_snapshot_json, created_at"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _question_tag_rows(scale: ScaleDefinition) -> Iterator[tuple[object, ...]]:
    tag_id = 0
    for question_index in range(1, scale.question_bank_questions + 1):
        tag_id += 1
        yield (
            tag_id,
            question_index,
            "knowledge_point",
            f"knowledge-{question_index % 50:02d}",
            1.0,
            "generated-source",
            "generated-model",
            _GENERATED_TIME,
        )
        tag_id += 1
        yield (
            tag_id,
            question_index,
            "method",
            f"generated-method-{question_index % 10:02d}",
            1.0,
            "generated-source",
            "generated-model",
            _GENERATED_TIME,
        )


def _preview_rows(asset_path: str) -> Iterator[tuple[object, ...]]:
    for preview_id, preview_type in enumerate(("question", "answer"), start=1):
        yield (
            preview_id,
            1,
            preview_type,
            "generated-source",
            1,
            asset_path,
            "{}",
            "ready",
            "generated-preview",
            _GENERATED_TIME,
            _GENERATED_TIME,
        )


def _question_link_rows(scale: ScaleDefinition) -> Iterator[tuple[object, ...]]:
    link_id = 0
    for session_id in range(1, scale.sessions + 1):
        for question_index in range(1, scale.questions_per_session + 1):
            link_id += 1
            yield (
                link_id,
                str(session_id),
                f"Q{question_index:03d}",
                question_index,
                "generated-method",
                1.0,
                "confirmed",
                "{}",
                "generated-reviewer",
                _GENERATED_TIME,
                _GENERATED_TIME,
                _GENERATED_TIME,
            )


def _training_task_rows(
    scale: ScaleDefinition,
    seed: int,
) -> Iterator[tuple[object, ...]]:
    for task_id in range(1, scale.training_tasks + 1):
        yield (
            task_id,
            f"GEN-TASK-{seed}-{task_id:05d}",
            "generated-benchmark",
            "{}",
            "{}",
            "{}",
            "{}",
            "[]",
            "ready",
            _GENERATED_TIME,
            _GENERATED_TIME,
        )


def _training_variant_rows(scale: ScaleDefinition) -> Iterator[tuple[object, ...]]:
    for task_id in range(1, scale.training_tasks + 1):
        yield (
            task_id,
            task_id,
            f"generated-variant-{task_id:05d}",
            "individual",
            "{}",
            "{}",
            "[]",
            "[]",
            "ready",
            _GENERATED_TIME,
            _GENERATED_TIME,
        )


def _variant_student_rows(scale: ScaleDefinition) -> Iterator[tuple[object, ...]]:
    for task_id in range(1, scale.training_tasks + 1):
        student_id = ((task_id - 1) % scale.students) + 1
        student_code = f"GEN-{student_id:05d}"
        yield (task_id, student_code, student_code, "CLASS-001", _GENERATED_TIME)


def _training_item_rows(scale: ScaleDefinition) -> Iterator[tuple[object, ...]]:
    for task_id in range(1, scale.training_tasks + 1):
        question_id = ((task_id - 1) % scale.question_bank_questions) + 1
        yield (
            task_id,
            task_id,
            f"GEN-ITEM-{task_id:05d}",
            question_id,
            f"generated-fingerprint-{question_id:05d}",
            1,
            "direct",
            "{}",
            "{}",
            "{}",
            _GENERATED_TIME,
        )


def _write_backup_metadata(
    paths: BenchmarkPaths,
    count: int,
    seed: int,
) -> tuple[Path, ...]:
    created: list[Path] = []
    for backup_index in range(1, count + 1):
        backup_path = paths.backups_dir / f"generated-backup-{backup_index:05d}.db"
        if backup_path.exists():
            raise FileExistsError("generated benchmark backup already exists")
        with _sqlite_connection(backup_path) as conn:
            conn.execute(
                "CREATE TABLE generated_metadata (generated_key TEXT, generated_value TEXT)"
            )
            conn.execute(
                "INSERT INTO generated_metadata VALUES (?, ?)",
                ("generated-seed", f"generated-{seed}"),
            )
        created.append(backup_path)
    return tuple(created)


def _insert_batches(
    conn: sqlite3.Connection,
    sql: str,
    rows: Iterable[tuple[object, ...]],
) -> None:
    iterator = iter(rows)
    while batch := list(islice(iterator, _BATCH_SIZE)):
        conn.executemany(sql, batch)


@contextmanager
def _sqlite_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = _SQLITE_CONNECT(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _expected_counts(scale: ScaleDefinition) -> dict[str, int]:
    result_count = scale.sessions * scale.students
    return {
        "students": scale.students,
        "grading_sessions": scale.sessions,
        "exam_papers": result_count,
        "session_results": result_count,
        "session_details": scale.grading_details,
        "questions": scale.question_bank_questions,
        "question_tags": scale.question_bank_questions * 2,
        "question_previews": 2,
        "grading_question_links": scale.sessions * scale.questions_per_session,
        "training_tasks": scale.training_tasks,
        "training_variants": scale.training_tasks,
        "variant_students": scale.training_tasks,
        "training_task_items": scale.training_tasks,
        "backup_files": scale.backups,
    }


def _actual_counts(paths: BenchmarkPaths) -> dict[str, int]:
    expected_names = tuple(_expected_counts(SMALL))
    grading_names = expected_names[:5]
    question_bank_names = expected_names[5:-1]
    counts: dict[str, int] = {}
    with _sqlite_connection(paths.db_path) as conn:
        for table_name in grading_names:
            counts[table_name] = int(
                conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
            )
    with _sqlite_connection(paths.qb_db_path) as conn:
        for table_name in question_bank_names:
            counts[table_name] = int(
                conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
            )
    counts["backup_files"] = len(list(paths.backups_dir.glob("*.db")))
    return counts


def _assert_database_valid(db_path: Path, label: str) -> None:
    with _sqlite_connection(db_path) as conn:
        foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    if foreign_key_errors or integrity != "ok":
        raise RuntimeError(f"generated {label} database failed validation")


def _checkpoint(db_path: Path) -> None:
    with _sqlite_connection(db_path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


__all__ = [
    "LARGE",
    "MEDIUM",
    "SCALES",
    "SMALL",
    "BenchmarkDataset",
    "BenchmarkPaths",
    "DatasetManifest",
    "ScaleDefinition",
    "build_benchmark_dataset",
]
