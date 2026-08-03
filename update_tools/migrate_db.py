"""数据库迁移引擎 — 按顺序执行 SQL 迁移脚本，跟踪已执行状态。

用法:
    python update_tools/migrate_db.py                       # 迁移全部数据库
    python update_tools/migrate_db.py --target grading       # 只迁移阅卷数据库
    python update_tools/migrate_db.py --target question_bank # 只迁移题库数据库
    python update_tools/migrate_db.py --dry-run              # 只检查，不执行
    python update_tools/migrate_db.py --status               # 显示迁移状态

安全规则:
    - 每次迁移前自动备份数据库
    - ALTER TABLE ADD COLUMN 重复时自动跳过（不报错）
    - 检测 DROP TABLE / DELETE FROM / TRUNCATE 等破坏性操作并拒绝执行
    - 迁移失败时停止后续迁移
    - 所有操作写入 logs/backup.log
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path 中
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.performance.metrics import instrument_sqlite_connection

# ── 日志 ──────────────────────────────────────────────

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_LOGGER_INIT_LOCK = threading.Lock()


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("db_migration")
    with _LOGGER_INIT_LOCK:
        if not logger.handlers:
            logger.setLevel(logging.DEBUG)
            try:
                from path_manager import get_path_manager
                log_dir = get_path_manager().logs_dir
            except Exception:
                log_dir = _PROJECT_ROOT / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_dir / "backup.log", encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(_LOG_FORMAT))
            logger.addHandler(fh)
            ch = logging.StreamHandler()
            ch.setLevel(logging.INFO)
            ch.setFormatter(logging.Formatter(_LOG_FORMAT))
            logger.addHandler(ch)
    return logger


# ── 破坏性检测 ────────────────────────────────────────

_DESTRUCTIVE_PATTERNS = [
    re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE),
    re.compile(r"\bDROP\s+INDEX\b", re.IGNORECASE),
    re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE),
    re.compile(r"\bTRUNCATE\b", re.IGNORECASE),
    re.compile(r"\bDROP\s+COLUMN\b", re.IGNORECASE),
]

_REBUILD_POLICY_RE = re.compile(
    r"^\s*--\s*migration-policy:\s*rebuild-tables\s+"
    r"([A-Za-z0-9_, ]+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_DROP_POLICY_RE = re.compile(
    r"^\s*--\s*migration-policy:\s*drop-tables\s+"
    r"([A-Za-z0-9_, ]+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_DROP_TABLE_TARGET_RE = re.compile(
    r"\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_APPROVED_TABLE_REBUILDS = {
    "005_add_status_constraints": frozenset(
        {"grading_sessions", "exam_papers", "answer_regions"}
    ),
    "006_knowledge_ids_primary": frozenset({"session_details"}),
    "007_drop_legacy_knowledge_id": frozenset({"session_details"}),
    "028_scope_knowledge_relations_to_release": frozenset(
        {"knowledge_relations"}
    ),
}
_APPROVED_TABLE_DROPS = {
    "008_drop_legacy_cli_tables": frozenset(
        {"exam_results", "grading_details"}
    ),
    "009_drop_legacy_skill_semantics": frozenset(
        {
            "knowledge_concepts",
            "knowledge_relations",
            "knowledge_source_mappings",
            "skill_topics",
            "skills",
            "assessment_item_skills",
            "question_skill_links",
            "skill_resolution_conflicts",
            "skill_neighbors",
            "skill_system_settings",
            "skill_migration_runs",
        }
    ),
}

# "duplicate column" 错误消息模式
_DUPLICATE_COLUMN_RE = re.compile(r"duplicate column name", re.IGNORECASE)


def _check_destructive(migration: "MigrationFile") -> list[str]:
    """检查 SQL 中的破坏性操作，返回警告列表。"""
    warnings = []
    # 去掉注释后检查
    clean = re.sub(r"--.*$", "", migration.sql, flags=re.MULTILINE)
    clean = re.sub(r"/\*.*?\*/", "", clean, flags=re.DOTALL)
    approved_rebuild_targets = _APPROVED_TABLE_REBUILDS.get(migration.name)
    approved_drop_targets = _APPROVED_TABLE_DROPS.get(migration.name)
    approved_targets = approved_rebuild_targets or approved_drop_targets
    table_drop_is_approved = (
        (
            approved_rebuild_targets is not None
            and migration.rebuild_tables == approved_rebuild_targets
            and not migration.drop_tables
        )
        or (
            approved_drop_targets is not None
            and migration.drop_tables == approved_drop_targets
            and not migration.rebuild_tables
        )
    )
    for pattern in _DESTRUCTIVE_PATTERNS:
        matches = pattern.findall(clean)
        if (
            matches
            and pattern.pattern == r"\bDROP\s+TABLE\b"
            and table_drop_is_approved
        ):
            dropped_tables = {
                match.lower()
                for match in _DROP_TABLE_TARGET_RE.findall(clean)
            }
            targets_match_policy = (
                dropped_tables == approved_targets
                if migration.drop_tables
                else dropped_tables <= approved_targets
            )
            if (
                len(dropped_tables) == len(matches)
                and targets_match_policy
            ):
                continue
        if matches:
            warnings.append(f"检测到破坏性操作: {matches[0]}")
    return warnings


# ── 数据模型 ──────────────────────────────────────────

@dataclass
class MigrationFile:
    """一个迁移 SQL 文件。"""
    name: str       # 如 "001_init_migration_tracking"
    path: Path
    sql: str
    checksum: str
    rebuild_tables: frozenset[str]
    drop_tables: frozenset[str]
    order: int      # 从文件名解析的序号

    @classmethod
    def from_path(cls, path: Path) -> MigrationFile:
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.md5(sql.encode("utf-8")).hexdigest()
        stem = path.stem
        # 解析序号
        match = re.match(r"^(\d+)", stem)
        order = int(match.group(1)) if match else 0
        policy_match = _REBUILD_POLICY_RE.search(sql)
        drop_policy_match = _DROP_POLICY_RE.search(sql)
        rebuild_tables = (
            frozenset(
                item.strip().lower()
                for item in policy_match.group(1).split(",")
                if item.strip()
            )
            if policy_match
            else frozenset()
        )
        drop_tables = (
            frozenset(
                item.strip().lower()
                for item in drop_policy_match.group(1).split(",")
                if item.strip()
            )
            if drop_policy_match
            else frozenset()
        )
        return cls(
            name=stem,
            path=path,
            sql=sql,
            checksum=checksum,
            rebuild_tables=rebuild_tables,
            drop_tables=drop_tables,
            order=order,
        )


@dataclass
class MigrationResult:
    """单次迁移的执行结果。"""
    name: str
    status: str  # "applied", "skipped", "failed", "destructive_blocked"
    message: str = ""
    backup_path: str | None = None


@dataclass
class MigrationReport:
    """整体迁移报告。"""
    target: str
    db_path: str
    results: list[MigrationResult] = field(default_factory=list)
    error: str | None = None


class MigrationVersionError(RuntimeError):
    """The recorded migration history cannot be handled by this application."""


# ── 迁移目标配置 ──────────────────────────────────────

def _get_targets() -> dict[str, dict[str, Any]]:
    """返回可迁移的数据库目标配置。"""
    try:
        from path_manager import get_path_manager
        pm = get_path_manager()
        return {
            "grading": {
                "db_path": pm.db_path,
                "migrations_dir": _PROJECT_ROOT / "migrations" / "grading",
            },
            "question_bank": {
                "db_path": pm.qb_db_path,
                "migrations_dir": _PROJECT_ROOT / "migrations" / "question_bank",
            },
        }
    except Exception:
        return {
            "grading": {
                "db_path": _PROJECT_ROOT / "user_data" / "databases" / "grading_system.db",
                "migrations_dir": _PROJECT_ROOT / "migrations" / "grading",
            },
            "question_bank": {
                "db_path": _PROJECT_ROOT / "user_data" / "databases" / "question_bank.db",
                "migrations_dir": _PROJECT_ROOT / "migrations" / "question_bank",
            },
        }


# ── 核心迁移逻辑 ──────────────────────────────────────

def _ensure_migrations_table(
    conn: sqlite3.Connection,
    *,
    commit: bool = True,
) -> None:
    """确保 schema_migrations 表存在。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            migration_name TEXT NOT NULL UNIQUE,
            applied_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            checksum    TEXT,
            success     INTEGER NOT NULL DEFAULT 1
        )
    """)
    if commit:
        conn.commit()


def _migrations_table_exists(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        is not None
    )


def _get_applied_migrations(conn: sqlite3.Connection) -> set[str]:
    """获取已执行的迁移名称集合。"""
    try:
        rows = conn.execute(
            "SELECT migration_name FROM schema_migrations WHERE success = 1"
        ).fetchall()
        return {row[0] for row in rows}
    except sqlite3.OperationalError:
        return set()


def _validate_migration_history(
    conn: sqlite3.Connection,
    migrations: list[MigrationFile],
) -> None:
    known = {migration.name: migration for migration in migrations}
    try:
        rows = conn.execute(
            """
            SELECT migration_name, checksum
            FROM schema_migrations
            WHERE success = 1
            ORDER BY id
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return

    for raw_name, raw_checksum in rows:
        name = str(raw_name)
        migration = known.get(name)
        if migration is None:
            raise MigrationVersionError(
                "database schema is newer than this application"
            )
        checksum = str(raw_checksum or "").strip()
        if checksum and checksum != migration.checksum:
            raise MigrationVersionError(
                f"recorded migration checksum does not match: {name}"
            )
    recorded_names = [str(row[0]) for row in rows]
    expected_prefix = [migration.name for migration in migrations[: len(rows)]]
    if recorded_names != expected_prefix:
        raise MigrationVersionError("recorded migration history has a gap")


def _record_migration(
    conn: sqlite3.Connection,
    name: str,
    checksum: str,
    success: bool,
    *,
    commit: bool = True,
) -> None:
    """记录迁移结果。"""
    conn.execute(
        """
        INSERT OR REPLACE INTO schema_migrations (migration_name, checksum, success, applied_at)
        VALUES (?, ?, ?, datetime('now','localtime'))
        """,
        (name, checksum, 1 if success else 0),
    )
    if commit:
        conn.commit()


def _backup_database(db_path: Path, reason: str, *, backup_dir: Path | None = None) -> Path | None:
    """迁移前备份数据库。"""
    if not db_path.exists():
        return None
    if backup_dir is None:
        try:
            from path_manager import get_path_manager
            backup_dir = get_path_manager().backups_dir
        except Exception:
            backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    db_stem = db_path.stem
    backup_path = backup_dir / f"{db_stem}_before_{reason}_{ts}.db"
    source_uri = db_path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as source:
        with closing(sqlite3.connect(backup_path)) as destination:
            source.backup(destination)
    return backup_path


def _split_sql_statements(sql: str) -> list[str]:
    """把迁移 SQL 拆成独立语句，正确保留 CREATE TRIGGER 的 BEGIN…END 块。

    触发器体内含分号，裸分号切分会破坏语句；这里跟踪 BEGIN/END 深度，
    只在深度为 0 时把分号视为语句边界。行注释在判定关键字前剥离。
    """
    statements: list[str] = []
    buffer: list[str] = []
    depth = 0
    for raw_line in sql.splitlines():
        code = re.sub(r"--.*$", "", raw_line)
        buffer.append(raw_line)
        for match in re.finditer(r"\b(?:BEGIN|CASE|END)\b|;", code, flags=re.IGNORECASE):
            token = match.group(0)
            upper = token.upper()
            if upper in {"BEGIN", "CASE"}:
                depth += 1
            elif upper == "END":
                depth = max(0, depth - 1)
            elif token == ";" and depth == 0:
                statement = "\n".join(buffer).strip()
                # 边界分号可能不在行尾；按最后一个深度 0 分号截断足够安全：
                # 迁移文件约定一行内不混排两条语句。
                statements.append(statement)
                buffer = []
    tail = "\n".join(buffer).strip()
    if tail:
        statements.append(tail)
    # 清掉纯注释/空语句
    result = []
    for stmt in statements:
        cleaned = re.sub(r"--.*$", "", stmt, flags=re.MULTILINE).strip().rstrip(";").strip()
        if cleaned:
            result.append(stmt.rstrip().rstrip(";"))
    return result


def _execute_sql_safe(conn: sqlite3.Connection, sql: str) -> str | None:
    """执行 SQL，处理 ADD COLUMN 重复等安全情况。返回错误信息或 None。"""
    statements = _split_sql_statements(sql)

    for stmt in statements:
        # 跳过纯注释
        clean = re.sub(r"--.*$", "", stmt, flags=re.MULTILINE).strip()
        if not clean:
            continue
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as exc:
            error_msg = str(exc)
            # ALTER TABLE ADD COLUMN 重复 → 安全跳过
            if _DUPLICATE_COLUMN_RE.search(error_msg):
                continue
            # "table already exists" → 安全跳过 (CREATE TABLE IF NOT EXISTS 的变体)
            if "already exists" in error_msg.lower():
                continue
            return f"SQL 执行失败: {error_msg}\n语句: {stmt[:200]}"
        except Exception as exc:
            return f"SQL 执行失败: {exc}\n语句: {stmt[:200]}"

    return None


def run_migrations(
    target_name: str,
    *,
    dry_run: bool = False,
    stamp_only: bool = False,
    db_path: Path | None = None,
    migrations_dir: Path | None = None,
    logger_override: Any | None = None,
    backup_dir_override: Path | None = None,
) -> MigrationReport:
    """对指定数据库执行所有未执行的迁移。

    - ``db_path``/``migrations_dir``：显式覆盖目标路径（预演/测试用副本库）；缺省用真实目标。
    - ``stamp_only``：把待执行迁移记录为已应用但不执行 SQL（基线打标用，
      适用于 Schema 已由运行时初始化建成、且旧迁移含不可盲目重放的数据语句的库）。
    """
    targets = _get_targets()

    if (
        target_name not in targets
        and (db_path is None or migrations_dir is None)
    ):
        return MigrationReport(
            target=target_name, db_path="",
            error=f"未知目标: {target_name}，可选: {', '.join(targets)}"
        )

    config = (
        targets[target_name]
        if target_name in targets
        else {
            "db_path": Path(db_path),
            "migrations_dir": Path(migrations_dir),
        }
    )
    has_path_override = db_path is not None
    db_path = Path(db_path) if db_path is not None else config["db_path"]
    migrations_dir = (
        Path(migrations_dir) if migrations_dir is not None else config["migrations_dir"]
    )
    effective_backup_dir = (
        Path(backup_dir_override)
        if backup_dir_override is not None
        else (db_path.parent / "backups" if has_path_override else None)
    )

    report = MigrationReport(target=target_name, db_path=str(db_path))

    if not migrations_dir.exists():
        report.error = "migration directory is unavailable"
        return report

    # 收集迁移文件
    sql_files = sorted(migrations_dir.glob("*.sql"))
    if not sql_files:
        report.error = "migration manifest is empty"
        return report

    migrations = [MigrationFile.from_path(f) for f in sql_files]
    migrations.sort(key=lambda m: m.order)

    # 连接数据库
    if dry_run and not db_path.exists():
        conn = sqlite3.connect(":memory:")
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        try:
            _validate_migration_history(conn, migrations)
        except MigrationVersionError as exc:
            report.error = str(exc)
            return report
        applied = _get_applied_migrations(conn)

        pending = [m for m in migrations if m.name not in applied]
        if not pending:
            if logger_override is not None:
                logger_override.info(
                    "[%s] 所有 %d 个迁移已是最新",
                    target_name,
                    len(migrations),
                )
            return report

        logger = logger_override or _get_logger()
        logger.info("[%s] 待执行 %d 个迁移 (共 %d 个)", target_name, len(pending), len(migrations))

        if stamp_only and not dry_run:
            # 打标不执行 SQL。写锁内重读历史，避免并发启动重复备份或重复登记。
            conn.execute("BEGIN IMMEDIATE")
            try:
                _validate_migration_history(conn, migrations)
                current_applied = _get_applied_migrations(conn)
                current_pending = [
                    migration
                    for migration in migrations
                    if migration.name not in current_applied
                ]
                if not current_pending:
                    conn.commit()
                    return report
                stamp_backup = _backup_database(
                    db_path,
                    "stamp_only",
                    backup_dir=effective_backup_dir,
                )
                logger.info(
                    "打标前备份已创建: %s",
                    stamp_backup.name if stamp_backup else "none",
                )
                _ensure_migrations_table(conn, commit=False)
                for migration in current_pending:
                    _record_migration(
                        conn,
                        migration.name,
                        migration.checksum,
                        success=True,
                        commit=False,
                    )
                    logger.info("打标（未执行）: %s", migration.name)
                    report.results.append(
                        MigrationResult(
                            name=migration.name,
                            status="stamped",
                            message="记录为已应用，未执行 SQL",
                            backup_path=(
                                str(stamp_backup) if stamp_backup else None
                            ),
                        )
                    )
                conn.commit()
                return report
            except Exception as exc:
                conn.rollback()
                logger.error(
                    "迁移打标前备份或登记失败: %s",
                    type(exc).__name__,
                )
                report.error = "migration backup failed; database unchanged"
                return report

        for mig in pending:
            # 检查破坏性操作
            warnings = _check_destructive(mig)
            if warnings:
                msg = f"迁移 {mig.name} 包含破坏性操作，已拒绝执行: {'; '.join(warnings)}"
                logger.error(msg)
                report.results.append(MigrationResult(
                    name=mig.name, status="destructive_blocked", message=msg
                ))
                report.error = f"检测到破坏性迁移 {mig.name}，已停止。请检查该迁移文件。"
                break

            if dry_run:
                logger.info("[DRY-RUN] 将执行: %s (checksum=%s)", mig.name, mig.checksum[:8])
                report.results.append(MigrationResult(
                    name=mig.name, status="pending", message="将被执行"
                ))
                continue

            conn.execute("BEGIN IMMEDIATE")
            try:
                _validate_migration_history(conn, migrations)
            except MigrationVersionError as exc:
                conn.rollback()
                report.error = str(exc)
                break
            if mig.name in _get_applied_migrations(conn):
                conn.commit()
                report.results.append(
                    MigrationResult(name=mig.name, status="skipped")
                )
                continue
            try:
                backup_path = _backup_database(
                    db_path,
                    f"migration_{mig.name}",
                    backup_dir=effective_backup_dir,
                )
            except Exception as exc:
                conn.rollback()
                logger.error(
                    "迁移 %s 的执行前备份失败: %s",
                    mig.name,
                    type(exc).__name__,
                )
                report.error = "migration backup failed; database unchanged"
                break
            logger.info(
                "迁移前备份已创建: %s",
                backup_path.name if backup_path else "none",
            )
            tracking_existed_before = _migrations_table_exists(conn)
            _ensure_migrations_table(conn, commit=False)

            # 执行迁移
            logger.info("执行迁移: %s", mig.name)
            error = _execute_sql_safe(conn, mig.sql)

            if error is None and (mig.rebuild_tables or mig.drop_tables):
                integrity_rows = conn.execute(
                    "PRAGMA integrity_check"
                ).fetchall()
                if integrity_rows != [("ok",)]:
                    error = "database integrity check failed after migration"
                else:
                    foreign_key_rows = conn.execute(
                        "PRAGMA foreign_key_check"
                    ).fetchall()
                    if foreign_key_rows:
                        error = (
                            "foreign key check failed after migration"
                        )

            if error:
                conn.rollback()
                logger.error("迁移失败 %s: %s", mig.name, error)
                if tracking_existed_before:
                    _record_migration(
                        conn,
                        mig.name,
                        mig.checksum,
                        success=False,
                    )
                report.results.append(MigrationResult(
                    name=mig.name, status="failed", message=error,
                    backup_path=str(backup_path) if backup_path else None,
                ))
                report.error = (
                    f"migration {mig.name} failed after backup"
                )
                break
            else:
                _record_migration(
                    conn,
                    mig.name,
                    mig.checksum,
                    success=True,
                    commit=False,
                )
                conn.commit()
                logger.info("迁移成功: %s", mig.name)
                report.results.append(MigrationResult(
                    name=mig.name, status="applied",
                    backup_path=str(backup_path) if backup_path else None,
                ))
    finally:
        conn.close()

    return report


class _PreviewLogger:
    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def error(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def preview_migrations(
    target_name: str,
    *,
    db_path: Path,
    migrations_dir: Path,
) -> dict[str, Any]:
    """Execute pending migrations on a disposable copy and verify integrity."""
    source = Path(db_path)
    if not source.is_file():
        raise FileNotFoundError("migration preview database is missing")
    with tempfile.TemporaryDirectory(prefix="ai-grading-migration-preview-") as temp_value:
        temp_root = Path(temp_value)
        candidate = temp_root / source.name
        shutil.copy2(source, candidate)
        before = get_migration_status(
            target_name,
            db_path_override=candidate,
            migrations_dir_override=Path(migrations_dir),
        )
        report = run_migrations(
            target_name,
            db_path=candidate,
            migrations_dir=Path(migrations_dir),
            logger_override=_PreviewLogger(),
        )
        if report.error:
            raise RuntimeError("migration preview failed")
        with closing(sqlite3.connect(candidate)) as connection:
            integrity_row = connection.execute("PRAGMA integrity_check").fetchone()
        integrity = str(integrity_row[0] if integrity_row else "unavailable")
        if integrity.casefold() != "ok":
            raise RuntimeError("migration preview integrity check failed")
        applied = [
            item.name
            for item in report.results
            if item.status in {"applied", "stamped"}
        ]
        return {
            "target": target_name,
            "pending_migrations": len(before.get("pending") or []),
            "applied": applied,
            "integrity": "ok",
        }


def get_migration_status(
    target_name: str,
    *,
    db_path_override: Path | None = None,
    migrations_dir_override: Path | None = None,
) -> dict[str, Any]:
    """获取指定数据库的迁移状态。"""
    targets = _get_targets()
    if (
        target_name not in targets
        and (
            db_path_override is None
            or migrations_dir_override is None
        )
    ):
        return {"error": f"未知目标: {target_name}"}

    config = (
        targets[target_name]
        if target_name in targets
        else {
            "db_path": Path(db_path_override),
            "migrations_dir": Path(migrations_dir_override),
        }
    )
    db_path = (
        Path(db_path_override)
        if db_path_override is not None
        else Path(config["db_path"])
    )
    migrations_dir = (
        Path(migrations_dir_override)
        if migrations_dir_override is not None
        else Path(config["migrations_dir"])
    )

    status: dict[str, Any] = {
        "target": target_name,
        "db_path": str(db_path),
        "db_exists": db_path.exists(),
        "applied": [],
        "pending": [],
        "total_migrations": 0,
        "schema_version": "0",
        "last_migration_time": None,
    }

    # 收集迁移文件
    if migrations_dir.exists():
        sql_files = sorted(migrations_dir.glob("*.sql"))
        migrations = [MigrationFile.from_path(f) for f in sql_files]
        migrations.sort(key=lambda m: m.order)
        status["total_migrations"] = len(migrations)
    else:
        migrations = []

    if not db_path.exists():
        status["pending"] = [m.name for m in migrations]
        return status

    conn = instrument_sqlite_connection(sqlite3.connect(db_path))
    try:
        applied = set()
        last_time = None
        try:
            rows = conn.execute(
                "SELECT migration_name, applied_at FROM schema_migrations WHERE success = 1 ORDER BY id"
            ).fetchall()
            applied = {row[0] for row in rows}
            if rows:
                last_time = rows[-1][1]
        except sqlite3.OperationalError:
            pass

        status["applied"] = [m.name for m in migrations if m.name in applied]
        status["pending"] = [m.name for m in migrations if m.name not in applied]
        status["schema_version"] = str(len(status["applied"]))
        status["last_migration_time"] = last_time
    finally:
        conn.close()

    return status


# ── CLI ───────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="AI 阅卷系统 — 数据库迁移工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python update_tools/migrate_db.py                        # 迁移全部\n"
            "  python update_tools/migrate_db.py --target grading       # 只迁移阅卷库\n"
            "  python update_tools/migrate_db.py --target question_bank # 只迁移题库\n"
            "  python update_tools/migrate_db.py --dry-run              # 预览\n"
            "  python update_tools/migrate_db.py --status               # 查看状态\n"
        ),
    )
    parser.add_argument(
        "--target",
        choices=["grading", "question_bank"],
        default=None,
        help="指定数据库目标（默认全部）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只检查，不执行迁移")
    parser.add_argument("--status", action="store_true", help="显示迁移状态")
    parser.add_argument(
        "--stamp-only",
        action="store_true",
        help="把待执行迁移记录为已应用但不执行 SQL（基线打标；Schema 需已由运行时建成）",
    )
    args = parser.parse_args()

    targets = [args.target] if args.target else ["grading", "question_bank"]

    print("=" * 60)
    print("  AI 阅卷系统 — 数据库迁移")
    print("=" * 60)

    if args.status:
        for target in targets:
            status = get_migration_status(target)
            print(f"\n  [{target}]")
            print(f"    数据库:       {status['db_path']}")
            print(f"    数据库存在:   {'是' if status['db_exists'] else '否'}")
            print(f"    Schema 版本:  {status['schema_version']}")
            print(f"    已执行迁移:   {len(status['applied'])} 个")
            print(f"    待执行迁移:   {len(status['pending'])} 个")
            print(f"    最后迁移时间: {status['last_migration_time'] or '无'}")
            if status["pending"]:
                print(f"    待执行列表:")
                for name in status["pending"]:
                    print(f"      - {name}")
        print("\n" + "=" * 60)
        return 0

    if args.dry_run:
        print("\n  [DRY-RUN] 模式：不会修改数据库\n")

    has_error = False
    for target in targets:
        report = run_migrations(target, dry_run=args.dry_run, stamp_only=args.stamp_only)
        print(f"\n  [{target}] {report.db_path}")

        if report.error:
            print(f"    [ERROR] {report.error}")
            has_error = True

        for r in report.results:
            status_label = {
                "applied": "[OK]",
                "pending": "[PENDING]",
                "skipped": "[SKIP]",
                "stamped": "[STAMP]",
                "failed": "[FAIL]",
                "destructive_blocked": "[BLOCKED]",
            }.get(r.status, r.status)
            print(f"    {status_label} {r.name}")
            if r.message and r.status in ("failed", "destructive_blocked"):
                print(f"           {r.message}")

        if not report.results and not report.error:
            print("    所有迁移已是最新。")

    print("\n" + "=" * 60)
    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main())
