from __future__ import annotations

import base64
import json
import sqlite3
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import islice
from pathlib import Path
from types import FunctionType

from backend.performance.metrics import instrument_sqlite_connection
from backend.schema_migrations import ensure_schema_current
from backend.repositories.db_manager import DBManager
from question_bank.current_knowledge import (
    CurrentKnowledgeResolver,
    ensure_checked_in_current_standard,
)
from question_bank.database.schema import initialize_database
from question_bank.knowledge_graph_release.loader import (
    load_release,
    load_taxonomy_catalog_for_release,
)
from question_bank.taxonomy.curriculum_catalog import (
    eligible_curriculum_knowledge_nodes,
)


_BATCH_SIZE = 1_000
_BENCHMARK_KNOWLEDGE_TERM_COUNT = 50
_BENCHMARK_CURRICULUM_VOLUME_ID = "bnu24-math-g9-upper"
_GENERATED_TIME = "generated-time"
_SQLITE_CONNECT = sqlite3.connect
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


class _QuietMigrationLogger:
    def info(self, *_args: object, **_kwargs: object) -> None:
        return None

    def error(self, *_args: object, **_kwargs: object) -> None:
        return None


@dataclass(frozen=True, slots=True)
class ScaleDefinition:
    name: str
    sessions: int
    students: int
    questions_per_session: int
    grading_details: int
    question_bank_questions: int
    backups: int

    @property
    def counts(self) -> tuple[int, int, int, int, int, int]:
        return (
            self.sessions,
            self.students,
            self.questions_per_session,
            self.grading_details,
            self.question_bank_questions,
            self.backups,
        )


SMALL = ScaleDefinition("small", 1, 30, 10, 300, 200, 5)
MEDIUM = ScaleDefinition("medium", 5, 200, 20, 20_000, 2_000, 50)
LARGE = ScaleDefinition("large_5pct", 1, 25, 2, 7_500, 500, 5)
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
    def migration_project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

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
    def taxonomy_state_path(self) -> Path:
        return self.config_dir / "generated-taxonomy-state.json"

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

    def workspace_dir(self, module_id: str, *, create: bool = False) -> Path:
        path = self.data_root / "workspaces" / str(module_id)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

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
    representative_knowledge_term_id: str
    representative_stable_key: str


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
    ensure_checked_in_current_standard(paths.qb_db_path)

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
        representative_knowledge_term_id=_benchmark_knowledge_term_ids()[0],
        representative_stable_key=_benchmark_representative_stable_key(),
    )


@lru_cache(maxsize=1)
def _benchmark_knowledge_term_ids() -> tuple[str, ...]:
    term_ids = tuple(
        str(node["id"])
        for node in eligible_curriculum_knowledge_nodes(
            _BENCHMARK_CURRICULUM_VOLUME_ID
        )[:_BENCHMARK_KNOWLEDGE_TERM_COUNT]
    )
    if len(term_ids) != _BENCHMARK_KNOWLEDGE_TERM_COUNT:
        raise RuntimeError("bundled benchmark knowledge terms are unavailable")
    return term_ids


@lru_cache(maxsize=1)
def _benchmark_representative_stable_key() -> str:
    release = load_release()
    resolver = CurrentKnowledgeResolver(
        release,
        load_taxonomy_catalog_for_release(release),
    )
    stable_keys = tuple(
        dict.fromkeys(
            match.stable_key
            for match in resolver.resolve(_benchmark_knowledge_term_ids()[0])
        )
    )
    if len(stable_keys) != 1:
        raise RuntimeError("benchmark knowledge term has no unique stable key")
    return stable_keys[0]


def _validate_scale(scale: ScaleDefinition) -> None:
    positive = (
        scale.sessions,
        scale.students,
        scale.questions_per_session,
        scale.question_bank_questions,
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
    _prepare_generated_schema("grading", db_path)
    manager = DBManager(db_path)

    def configured_connection() -> object:
        return _configured_sqlite_connection(db_path)

    manager._connect = configured_connection  # type: ignore[method-assign]
    manager.initialize()


def _initialize_question_bank_database(db_path: Path) -> None:
    _prepare_generated_schema("question_bank", db_path)
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
    isolated_initialize(db_path)


def _prepare_generated_schema(target: str, db_path: Path) -> None:
    with tempfile.TemporaryDirectory(
        prefix=f"benchmark_{target}_migration_"
    ) as raw:
        ensure_schema_current(
            target,
            db_path,
            backup_dir=Path(raw),
            logger_override=_QuietMigrationLogger(),
        )


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
            "id, result_id, question_id, score_awarded, deduction_reason, "
            "knowledge_ids, error_category, error_summary, confidence_score, secondary_errors_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            _detail_rows(scale, result_count),
        )


def _write_session_rubrics(paths: BenchmarkPaths, scale: ScaleDefinition) -> None:
    knowledge_term_ids = _benchmark_knowledge_term_ids()
    questions = [
        {
            "question_id": f"Q{question_index:03d}",
            "max_score": 10.0,
            "knowledge_id": knowledge_term_ids[
                (question_index - 1) % len(knowledge_term_ids)
            ],
            "knowledge_ids": [
                knowledge_term_ids[
                    (question_index - 1) % len(knowledge_term_ids)
                ]
            ],
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
                "graded",
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
    knowledge_term_ids = _benchmark_knowledge_term_ids()
    for detail_id in range(1, scale.grading_details + 1):
        result_id = (((detail_id - 1) // scale.questions_per_session) % result_count) + 1
        session_id = ((result_id - 1) // scale.students) + 1
        student_id = ((result_id - 1) % scale.students) + 1
        question_index = ((detail_id - 1) % scale.questions_per_session) + 1
        question_id = f"Q{question_index:03d}"
        knowledge_label = knowledge_term_ids[
            (question_index - 1) % len(knowledge_term_ids)
        ]
        score = float((student_id + session_id + question_index) % 11)
        yield (
            detail_id,
            result_id,
            question_id,
            score,
            "generated-deduction",
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
        _insert_batches(conn, _PAPER_INSERT, _question_bank_paper_rows(scale))
        _insert_batches(
            conn,
            "INSERT INTO questions ("
            "id, paper_id, question_number, question_type, question_text, answer_text, source_file, "
            "image_paths, difficulty, typicality, reason, needs_review, has_images, "
            "needs_image_review, is_deleted, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    question_index,
                    ((question_index - 1) // 100) + 1,
                    f"Q{question_index:05d}",
                    "generated-type",
                    f"generated-question-{question_index:05d}",
                    f"generated-answer-{question_index:05d}",
                    f"generated-source-{question_index:05d}",
                    json.dumps([asset_path]) if question_index == 1 else "[]",
                    f"{1 + ((question_index - 1) % 10):.1f}",
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
    return 1


_PAPER_INSERT = (
    "INSERT INTO papers ("
    "id, title, source_file, year, province, city, district, exam_type, grade, "
    "semester, textbook_version, import_status, content_fingerprint, created_at, updated_at"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
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
def _question_bank_paper_count(scale: ScaleDefinition) -> int:
    return max(1, (scale.question_bank_questions + 99) // 100)


def _question_bank_paper_rows(
    scale: ScaleDefinition,
) -> Iterator[tuple[object, ...]]:
    for paper_id in range(1, _question_bank_paper_count(scale) + 1):
        yield (
            paper_id,
            f"generated-paper-{paper_id:03d}",
            f"generated-paper-{paper_id:03d}.json",
            "generated-year",
            "generated-province",
            "generated-city",
            "generated-district",
            "generated-exam-type",
            "generated-grade",
            "generated-semester",
            "generated-textbook",
            "complete",
            f"generated-fingerprint-{paper_id:03d}",
            _GENERATED_TIME,
            _GENERATED_TIME,
        )


def _question_tag_rows(scale: ScaleDefinition) -> Iterator[tuple[object, ...]]:
    tag_id = 0
    knowledge_term_ids = _benchmark_knowledge_term_ids()
    for question_index in range(1, scale.question_bank_questions + 1):
        tag_values = (
            (
                "knowledge_point",
                knowledge_term_ids[
                    (question_index - 1) % len(knowledge_term_ids)
                ],
            ),
            ("ability", f"generated-ability-{question_index % 10:02d}"),
            ("exam_scope", f"generated-exam-scope-{question_index % 10:02d}"),
            ("student_level", f"generated-student-level-{question_index % 3:02d}"),
            ("method", f"generated-method-{question_index % 10:02d}"),
        )
        for tag_type, tag_value in tag_values:
            tag_id += 1
            yield (
                tag_id,
                question_index,
                tag_type,
                tag_value,
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
        "papers": _question_bank_paper_count(scale),
        "questions": scale.question_bank_questions,
        "question_tags": scale.question_bank_questions * 5,
        "question_previews": 2,
        "grading_question_links": scale.sessions * scale.questions_per_session,
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
