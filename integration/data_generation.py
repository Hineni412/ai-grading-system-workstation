"""Per-database commit generations backed by SQLite's existing metadata.

One long-lived read-only monitor connection is kept per resolved database
path. ``PRAGMA data_version`` detects commits from other connections while
the WAL contains frames. Each distinct observed state bumps a monotonic
counter so caches can use ``commit_generation()`` instead of WAL mtimes.

Some read-only WAL connections repeatedly advance data_version while the
WAL is empty. In that state all committed content is in the main file; use
its existing SQLite header and WAL-index counters to distinguish real
changes from reads. Checkpoint metadata transitions may refresh a cache;
repeated reads of unchanged content keep the same generation.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Union


_PATH_TYPE = Union[str, Path]

# 每类数据一个版本号：读缓存只看自己依赖的那几类，其余写入不再失效缓存。
# 类别归属在迁移 SQL 与运行时覆盖检查中共用这一份映射。
GRADING_VERSION_UNTRACKED_TABLES = frozenset(
    {
        "jobs",
        "workspace_ai_tasks",
        "workspace_ai_handoffs",
        "schema_migrations",
        "data_versions",
    }
)

QUESTION_BANK_VERSION_UNTRACKED_TABLES = frozenset(
    {
        # question_frequency_cache/question_fingerprints 没有任何写入路径；
        # paper_permanent_delete_receipts 只在删除题目的同一事务中写入。
        # question_scope_summary 与 question_content_index 按 materials 跟踪：
        # 读缓存直接消费它们，而存在不伴随 materials 写入的独立重建/补齐路径。
        "question_frequency_cache",
        "question_fingerprints",
        "paper_permanent_delete_receipts",
        "schema_migrations",
        "data_versions",
        # question_versions 由 qv_/qvg_ 触发器维护，自身不再受 dv_ 跟踪。
        "question_versions",
    }
)

QUESTION_BANK_VERSION_DOMAIN_TABLES: dict[str, frozenset[str]] = {
    "training_ops": frozenset(
        {
            "personalized_paper_batch_items",
            "personalized_paper_batches",
            "personalized_paper_events",
            "personalized_paper_instances",
            "personalized_paper_items",
            "personalized_paper_pages",
            "personalized_recommendation_drafts",
            "personalized_recommendation_events",
            "training_exports",
            "training_scan_batches",
            "training_submission_events",
            "training_submission_pages",
            "training_submission_uploads",
            "training_submissions",
            "training_assessment_attempts",
            "training_assessment_events",
            "training_assessment_runs",
            "training_feedback_snapshots",
        }
    ),
    "training_records": frozenset(
        {
            "training_evidence_outbox",
            "training_evidence_records",
            "training_evidence_sync_events",
            "training_point_results",
            "training_question_results",
            "training_point_locks",
            "training_attempts",
            "training_tasks",
            "training_task_items",
            "training_sets",
            "training_set_items",
            "training_variants",
            "variant_students",
            "mastery_v2_evaluation_items",
            "mastery_v2_evaluations",
            "mastery_v2_parameter_versions",
            "mastery_v2_rollout_events",
            "mastery_v2_rollout_state",
            "mastery_v2_spot_checks",
        }
    ),
}

# 未单独列出的表：题库归 materials，阅卷归 results。
DATA_VERSION_UNTRACKED_TABLES = (
    GRADING_VERSION_UNTRACKED_TABLES | QUESTION_BANK_VERSION_UNTRACKED_TABLES
)


# ── per-question 版本跟踪（question_versions / materials_global）──────────────
# 下列常量是迁移 SQL、覆盖校验与测试的唯一来源。materials 域的每张表必须且
# 只能归入一类：按题目列跟踪、JSON 题号列表、试卷扩散、全局。

# 写入这些表时按行内题目列 bump question_versions。UPDATE 同时 bump
# OLD 与 NEW 两侧的题目号。
# 说明：question_document_items.source_question_id 是导入文档侧的 TEXT 键，
# 没有指向 questions(id) 的外键，不按题目跟踪；bank_question_id 才有该外键。
# authoring_works.source_question_id 经外键确认引用 questions(id)。
QUESTION_BANK_VERSION_QUESTION_TABLES: dict[str, tuple[str, ...]] = {
    "questions": ("id",),
    "question_tags": ("question_id",),
    "question_solution_evidence_versions": ("question_id",),
    "evidence_point_knowledge_links": ("question_id",),
    "training_criterion_heads": ("question_id",),
    "training_criterion_versions": ("question_id",),
    "training_criterion_events": ("question_id",),
    "question_analysis_items": ("question_id",),
    "question_content_index": ("question_id",),
    "question_content_revisions": ("question_id",),
    "question_previews": ("question_id",),
    "question_error_patterns": ("question_id",),
    "question_part_difficulty_features": ("question_id",),
    "question_scope_summary": ("question_id",),
    "paper_question_occurrences": ("question_id",),
    "taxonomy_review_applications": ("question_id",),
    "training_criterion_backfill_items": ("question_id",),
    "question_duplicate_links": ("question_id", "duplicate_of_question_id"),
    "grading_question_links": ("bank_question_id",),
    "question_document_items": ("bank_question_id",),
    "authoring_works": ("source_question_id",),
}

# 题号以 JSON 列表存储的表：列表合法时逐个 bump，非法时 bump
# materials_global（写入本身绝不因触发器解析 JSON 而失败）。
QUESTION_BANK_VERSION_JSON_TABLES: dict[str, str] = {
    "question_analysis_requests": "question_ids_json",
    "training_criterion_backfill_runs": "question_ids_json",
    "question_document_publications": "question_ids_json",
}

# 试卷行写入时 bump 其全部题目（paper_id = 该行 id）。
QUESTION_BANK_VERSION_PAPER_TABLES: tuple[str, ...] = ("papers",)

# 这些表的数据是所有题目共享的输入（知识图谱、治理、标签身份等），
# 写入时 bump data_versions('materials_global')。
QUESTION_BANK_VERSION_GLOBAL_TABLES: tuple[str, ...] = (
    "authoring_assets",
    "authoring_reviews",
    "authoring_work_versions",
    "fine_term_core_mapping_events",
    "fine_term_core_mappings",
    "knowledge_graph_fine_term_dispositions",
    "knowledge_graph_node_profiles",
    "knowledge_graph_release_events",
    "knowledge_graph_release_mappings",
    "knowledge_graph_release_relations",
    "knowledge_graph_releases",
    "knowledge_identity_replacements",
    "knowledge_relation_amendment_events",
    "knowledge_relation_audit_events",
    "knowledge_relation_evidence",
    "knowledge_relation_suggestion_items",
    "knowledge_relation_suggestion_requests",
    "knowledge_relation_suggestion_runs",
    "knowledge_relations",
    "knowledge_tag_aliases",
    "knowledge_tag_identities",
    "knowledge_tag_identity_mappings",
    "question_analysis_operations",
    "question_document_publication_outbox",
)

# 最近一次按题增量计算的观测记录，仅内部排查/测试使用，不进 API 响应。
LAST_INCREMENTAL: dict[str, dict[str, object]] = {}


def _database_file_state(path: Path) -> tuple:
    """Database and journal metadata used only to refresh disposable caches."""
    stat = path.stat()
    wal = Path(f"{path}-wal")
    try:
        wal_stat = wal.stat()
        wal_state = (wal_stat.st_size, wal_stat.st_mtime_ns) if wal_stat.st_size else None
    except FileNotFoundError:
        wal_state = None
    try:
        with Path(f"{path}-shm").open("rb") as index:
            header = index.read(96)
        index_state = (header[8:12], header[32:40], header[:48] == header[48:96])
    except FileNotFoundError:
        index_state = None
    return (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns,
            wal_state, index_state)


def database_change_stamp(path: _PATH_TYPE) -> tuple:
    """Read file metadata for a disposable cache, without reading database rows."""
    return _database_file_state(Path(path).resolve(strict=False))


_LOCK = threading.Lock()
_MONITORS: dict[str, _GenerationMonitor] = {}
_MONITOR_LIMIT = 64


def _empty_wal_change_counter(path: Path) -> tuple | None:
    wal = Path(f"{path}-wal")

    def empty() -> bool:
        try:
            return wal.stat().st_size == 0
        except FileNotFoundError:
            return True

    try:
        if not empty():
            return None
        with path.open("rb") as database:
            header = database.read(32)
        # Check again so a writer starting a WAL while the header is read
        # sends us back to SQLite's live connection version.
        if len(header) < 32 or not empty():
            return None
        try:
            with Path(f"{path}-shm").open("rb") as index:
                wal_index = index.read(96)
        except FileNotFoundError:
            wal_index = b""
        if wal_index and (len(wal_index) < 96 or wal_index[:48] != wal_index[48:96]):
            return None
        # The WAL-index commit counter survives TRUNCATE checkpoints even
        # when the main-file counter and timestamp do not change. Its two
        # header copies must agree; otherwise let SQLite handle the writer.
        committed = (wal_index[8:12], wal_index[32:40]) if wal_index else None
        if not empty():
            return None
        state = path.stat()
        return (header[24:28], state.st_mtime_ns, state.st_size, committed)
    except OSError:
        return None


def _file_identity(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return (int(info.st_dev), int(info.st_ino))


class _GenerationMonitor:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._connection: sqlite3.Connection | None = None
        self._identity: tuple[int, int] | None = None
        self._seen_version: tuple | None = None
        self._counter = 0

    def _close(self) -> None:
        connection, self._connection = self._connection, None
        self._identity = None
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass

    def _open(self) -> sqlite3.Connection | None:
        try:
            uri = self._path.resolve(strict=True).as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
            connection.execute("PRAGMA query_only = ON")
            connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        except (OSError, sqlite3.Error):
            return None
        return connection

    def versions_connection(self) -> sqlite3.Connection | None:
        """The long-lived read-only connection, or None when unavailable."""

        identity = _file_identity(self._path)
        if identity is None or identity != self._identity:
            self._close()
            self._seen_version = None
            if identity is None:
                return None
            self._connection = self._open()
            if self._connection is None:
                return None
            self._identity = identity
        return self._connection

    def generation(self) -> int:
        connection = self.versions_connection()
        if connection is None:
            self._counter += 1
            return self._counter
        try:
            row = connection.execute("PRAGMA data_version").fetchone()
            version = int(row[0])
        except sqlite3.Error:
            self._close()
            self._seen_version = None
            self._counter += 1
            return self._counter
        main_counter = _empty_wal_change_counter(self._path)
        observed = ("main", main_counter) if main_counter is not None else ("sqlite", version)
        if observed != self._seen_version:
            self._seen_version = observed
            self._counter += 1
        return self._counter


def commit_generation(db_path: _PATH_TYPE) -> int:
    """Return the monotonic commit generation for ``db_path``.

    The value increases whenever another connection commits a write to the
    database, when the file identity changes, or when the file cannot be
    opened. Repeated read-only opens are stable; checkpoint metadata
    transitions may conservatively refresh a cache.
    """

    resolved = str(Path(db_path).resolve(strict=False))
    with _LOCK:
        monitor = _MONITORS.get(resolved)
        if monitor is None:
            if len(_MONITORS) >= _MONITOR_LIMIT:
                stale = list(_MONITORS.values())
                _MONITORS.clear()
                for item in stale:
                    item._close()
            monitor = _GenerationMonitor(Path(resolved))
            _MONITORS[resolved] = monitor
        return monitor.generation()


def reset_commit_generations() -> None:
    """Close all monitor connections and drop counters (test isolation)."""

    with _LOCK:
        monitors = list(_MONITORS.values())
        _MONITORS.clear()
        _COVERAGE.clear()
        _QV_COVERAGE.clear()
    for monitor in monitors:
        monitor._close()


_COVERAGE: dict[tuple[object, ...], bool] = {}
_COVERAGE_LIMIT = 64
_COVERAGE_DOMAIN_RE = re.compile(r"domain\s*=\s*'([^']+)'")


def _versions_coverage(connection: sqlite3.Connection) -> bool:
    """True when every tracked table carries its three dv_* triggers.

    The set is checked against live sqlite objects, so a future table added
    without triggers disables versions instead of silently missing writes.
    """

    try:
        objects = connection.execute(
            "SELECT name, type, sql FROM sqlite_master "
            "WHERE type IN ('table', 'trigger')"
        ).fetchall()
    except sqlite3.Error:
        return False
    tables = {
        str(name)
        for name, kind, _sql in objects
        if kind == "table" and not str(name).startswith("sqlite_")
    }
    if "data_versions" not in tables:
        return False
    if "questions" in tables:
        untracked = QUESTION_BANK_VERSION_UNTRACKED_TABLES
    elif "jobs" in tables or "students" in tables:
        untracked = GRADING_VERSION_UNTRACKED_TABLES
    else:
        return False
    triggers = {
        str(name): str(sql or "")
        for name, kind, sql in objects
        if kind == "trigger"
    }
    domains: set[str] = set()
    for table in tables - untracked:
        for suffix in ("ins", "upd", "del"):
            sql = triggers.get(f"dv_{table}_{suffix}")
            if sql is None:
                return False
            match = _COVERAGE_DOMAIN_RE.search(sql)
            if match is not None:
                domains.add(match.group(1))
    try:
        have = {
            str(row[0])
            for row in connection.execute("SELECT domain FROM data_versions")
        }
    except sqlite3.Error:
        return False
    return domains <= have


def data_versions(db_path: _PATH_TYPE) -> Mapping[str, int] | None:
    """Per-category data version numbers, or None when coverage is absent.

    Reads through the shared monitor connection. None means callers must
    keep their existing change detection (file stamps, commit generations,
    asset manifests). Coverage is evaluated once per resolved path, file
    identity and ``PRAGMA schema_version``.
    """

    resolved = str(Path(db_path).resolve(strict=False))
    with _LOCK:
        monitor = _MONITORS.get(resolved)
        if monitor is None:
            if len(_MONITORS) >= _MONITOR_LIMIT:
                stale = list(_MONITORS.values())
                _MONITORS.clear()
                for item in stale:
                    item._close()
            monitor = _GenerationMonitor(Path(resolved))
            _MONITORS[resolved] = monitor
        connection = monitor.versions_connection()
        if connection is None:
            return None
        try:
            schema_version = int(
                connection.execute("PRAGMA schema_version").fetchone()[0]
            )
        except (sqlite3.Error, TypeError, ValueError):
            return None
        coverage_key = (resolved, monitor._identity, schema_version)
        covered = _COVERAGE.get(coverage_key)
        if covered is None:
            covered = _versions_coverage(connection)
            while len(_COVERAGE) >= _COVERAGE_LIMIT:
                _COVERAGE.pop(next(iter(_COVERAGE)))
            _COVERAGE[coverage_key] = covered
        if not covered:
            return None
        try:
            return {
                str(domain): int(version)
                for domain, version in connection.execute(
                    "SELECT domain, version FROM data_versions"
                )
            }
        except sqlite3.Error:
            return None


def data_version_token(
    db_path: _PATH_TYPE, *domains: str
) -> tuple[object, ...] | None:
    """Cache-key token for ``domains``, or None without version coverage."""

    versions = data_versions(db_path)
    if versions is None:
        return None
    wanted = domains or tuple(versions)
    return (
        str(Path(db_path).resolve(strict=False)),
        *tuple(f"{domain}:{int(versions.get(domain, 0))}" for domain in wanted),
    )


@dataclass(frozen=True)
class QuestionVersionState:
    """按题版本快照：全局素材版本 + 每题版本。读侧增量计算的输入。"""

    materials_global: int
    versions: Mapping[int, int]

    def __post_init__(self) -> None:
        # 冻结内部映射，Mapping 标注之外也不允许调用方改动共享状态。
        object.__setattr__(self, "versions", MappingProxyType(dict(self.versions)))

    def changed_since(
        self,
        previous: "QuestionVersionState | None",
    ) -> frozenset[int] | None:
        """None => 全量重算（无先前状态或 materials_global 已变）；
        否则返回版本不同的题目号集合（缺失按 0 处理）。"""

        if previous is None or previous.materials_global != self.materials_global:
            return None
        current, earlier = self.versions, previous.versions
        changed = set()
        for qid in current.keys() | earlier.keys():
            if current.get(qid, 0) != earlier.get(qid, 0):
                changed.add(int(qid))
        return frozenset(changed)


_QV_COVERAGE: dict[tuple[object, ...], bool] = {}


def _question_versions_coverage(connection: sqlite3.Connection) -> bool:
    """qv_/qvg_/dvg_ 触发器、question_versions 表与 materials_global 行齐备。"""

    try:
        objects = connection.execute(
            "SELECT type, name FROM sqlite_master WHERE type IN ('table','trigger')"
        ).fetchall()
    except sqlite3.Error:
        return False
    tables = {str(name) for kind, name in objects if kind == "table"}
    triggers = {str(name) for kind, name in objects if kind == "trigger"}
    if "question_versions" not in tables or "data_versions" not in tables:
        return False
    expected: set[str] = set()
    for table in QUESTION_BANK_VERSION_QUESTION_TABLES:
        expected.update(f"qv_{table}_{suffix}" for suffix in ("ins", "upd", "del"))
    for table in QUESTION_BANK_VERSION_JSON_TABLES:
        expected.update(f"qv_{table}_{suffix}" for suffix in ("ins", "upd", "del"))
        expected.update(f"qvg_{table}_{suffix}" for suffix in ("ins", "upd", "del"))
    for table in QUESTION_BANK_VERSION_PAPER_TABLES:
        expected.update(f"qv_{table}_{suffix}" for suffix in ("ins", "upd", "del"))
    expected.update(
        f"dvg_{table}_{suffix}"
        for table in QUESTION_BANK_VERSION_GLOBAL_TABLES
        for suffix in ("ins", "upd", "del")
    )
    if not expected <= triggers:
        return False
    try:
        return (
            connection.execute(
                "SELECT 1 FROM data_versions WHERE domain = 'materials_global'"
            ).fetchone()
            is not None
        )
    except sqlite3.Error:
        return False


def question_version_state(db_path: _PATH_TYPE) -> QuestionVersionState | None:
    """按题版本状态；覆盖不完整（旧库/未迁移）时返回 None，调用方回退全量。

    与 ``data_versions`` 共用同一监听连接；覆盖检查按 resolved path、
    文件身份与 ``PRAGMA schema_version`` 缓存。
    """

    resolved = str(Path(db_path).resolve(strict=False))
    with _LOCK:
        monitor = _MONITORS.get(resolved)
        if monitor is None:
            if len(_MONITORS) >= _MONITOR_LIMIT:
                stale = list(_MONITORS.values())
                _MONITORS.clear()
                for item in stale:
                    item._close()
            monitor = _GenerationMonitor(Path(resolved))
            _MONITORS[resolved] = monitor
        connection = monitor.versions_connection()
        if connection is None:
            return None
        try:
            schema_version = int(
                connection.execute("PRAGMA schema_version").fetchone()[0]
            )
        except (sqlite3.Error, TypeError, ValueError):
            return None
        coverage_key = (resolved, monitor._identity, schema_version)
        covered = _QV_COVERAGE.get(coverage_key)
        if covered is None:
            covered = _question_versions_coverage(connection)
            while len(_QV_COVERAGE) >= _COVERAGE_LIMIT:
                _QV_COVERAGE.pop(next(iter(_QV_COVERAGE)))
            _QV_COVERAGE[coverage_key] = covered
        if not covered:
            return None
        try:
            row = connection.execute(
                "SELECT version FROM data_versions WHERE domain = 'materials_global'"
            ).fetchone()
            if row is None:
                return None
            versions = {
                int(question_id): int(version)
                for question_id, version in connection.execute(
                    "SELECT question_id, version FROM question_versions"
                )
            }
        except (sqlite3.Error, TypeError, ValueError):
            return None
        return QuestionVersionState(int(row[0]), versions)
