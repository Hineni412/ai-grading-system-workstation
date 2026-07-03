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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path 中
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── 日志 ──────────────────────────────────────────────

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("db_migration")
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

# "duplicate column" 错误消息模式
_DUPLICATE_COLUMN_RE = re.compile(r"duplicate column name", re.IGNORECASE)


def _check_destructive(sql: str) -> list[str]:
    """检查 SQL 中的破坏性操作，返回警告列表。"""
    warnings = []
    # 去掉注释后检查
    clean = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
    clean = re.sub(r"/\*.*?\*/", "", clean, flags=re.DOTALL)
    for pattern in _DESTRUCTIVE_PATTERNS:
        matches = pattern.findall(clean)
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
    order: int      # 从文件名解析的序号

    @classmethod
    def from_path(cls, path: Path) -> MigrationFile:
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.md5(sql.encode("utf-8")).hexdigest()
        stem = path.stem
        # 解析序号
        match = re.match(r"^(\d+)", stem)
        order = int(match.group(1)) if match else 0
        return cls(name=stem, path=path, sql=sql, checksum=checksum, order=order)


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

def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
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
    conn.commit()


def _get_applied_migrations(conn: sqlite3.Connection) -> set[str]:
    """获取已执行的迁移名称集合。"""
    try:
        rows = conn.execute(
            "SELECT migration_name FROM schema_migrations WHERE success = 1"
        ).fetchall()
        return {row[0] for row in rows}
    except sqlite3.OperationalError:
        return set()


def _record_migration(conn: sqlite3.Connection, name: str, checksum: str, success: bool) -> None:
    """记录迁移结果。"""
    conn.execute(
        """
        INSERT OR REPLACE INTO schema_migrations (migration_name, checksum, success, applied_at)
        VALUES (?, ?, ?, datetime('now','localtime'))
        """,
        (name, checksum, 1 if success else 0),
    )
    conn.commit()


def _backup_database(db_path: Path, reason: str) -> Path | None:
    """迁移前备份数据库。"""
    if not db_path.exists():
        return None
    try:
        from path_manager import get_path_manager
        backup_dir = get_path_manager().backups_dir
    except Exception:
        backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    db_stem = db_path.stem
    backup_path = backup_dir / f"{db_stem}_before_{reason}_{ts}.db"
    shutil.copy2(db_path, backup_path)
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

    conn.commit()
    return None


def run_migrations(
    target_name: str,
    *,
    dry_run: bool = False,
    stamp_only: bool = False,
    db_path: Path | None = None,
    migrations_dir: Path | None = None,
) -> MigrationReport:
    """对指定数据库执行所有未执行的迁移。

    - ``db_path``/``migrations_dir``：显式覆盖目标路径（预演/测试用副本库）；缺省用真实目标。
    - ``stamp_only``：把待执行迁移记录为已应用但不执行 SQL（基线打标用，
      适用于 Schema 已由运行时初始化建成、且旧迁移含不可盲目重放的数据语句的库）。
    """
    logger = _get_logger()
    targets = _get_targets()

    if target_name not in targets:
        return MigrationReport(
            target=target_name, db_path="",
            error=f"未知目标: {target_name}，可选: {', '.join(targets)}"
        )

    config = targets[target_name]
    db_path = Path(db_path) if db_path is not None else config["db_path"]
    migrations_dir = (
        Path(migrations_dir) if migrations_dir is not None else config["migrations_dir"]
    )

    report = MigrationReport(target=target_name, db_path=str(db_path))

    if not migrations_dir.exists():
        report.error = f"迁移目录不存在: {migrations_dir}"
        return report

    # 收集迁移文件
    sql_files = sorted(migrations_dir.glob("*.sql"))
    if not sql_files:
        logger.info("[%s] 没有迁移文件", target_name)
        return report

    migrations = [MigrationFile.from_path(f) for f in sql_files]
    migrations.sort(key=lambda m: m.order)

    # 连接数据库
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        _ensure_migrations_table(conn)
        applied = _get_applied_migrations(conn)

        pending = [m for m in migrations if m.name not in applied]
        if not pending:
            logger.info("[%s] 所有 %d 个迁移已是最新", target_name, len(migrations))
            return report

        logger.info("[%s] 待执行 %d 个迁移 (共 %d 个)", target_name, len(pending), len(migrations))

        if stamp_only and not dry_run:
            # 打标不执行 SQL，但仍在动作前做一次整体备份（只会新增 schema_migrations 行）。
            stamp_backup = _backup_database(db_path, "stamp_only")
            logger.info("打标前备份: %s", stamp_backup)

        for mig in pending:
            if stamp_only and not dry_run:
                # 仅记录，不执行：破坏性检查针对"将被执行的 SQL"，此处不适用。
                _record_migration(conn, mig.name, mig.checksum, success=True)
                logger.info("打标（未执行）: %s", mig.name)
                report.results.append(MigrationResult(
                    name=mig.name, status="stamped", message="记录为已应用，未执行 SQL"
                ))
                continue

            # 检查破坏性操作
            warnings = _check_destructive(mig.sql)
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

            # 备份数据库
            backup_path = _backup_database(db_path, f"migration_{mig.name}")
            logger.info("迁移前备份: %s", backup_path)

            # 执行迁移
            logger.info("执行迁移: %s", mig.name)
            error = _execute_sql_safe(conn, mig.sql)

            if error:
                logger.error("迁移失败 %s: %s", mig.name, error)
                _record_migration(conn, mig.name, mig.checksum, success=False)
                report.results.append(MigrationResult(
                    name=mig.name, status="failed", message=error,
                    backup_path=str(backup_path) if backup_path else None,
                ))
                report.error = (
                    f"迁移 {mig.name} 执行失败。数据库已在执行前备份。\n"
                    f"备份文件: {backup_path}\n"
                    f"错误: {error}"
                )
                break
            else:
                _record_migration(conn, mig.name, mig.checksum, success=True)
                logger.info("迁移成功: %s", mig.name)
                report.results.append(MigrationResult(
                    name=mig.name, status="applied",
                    backup_path=str(backup_path) if backup_path else None,
                ))
    finally:
        conn.close()

    return report


def get_migration_status(target_name: str) -> dict[str, Any]:
    """获取指定数据库的迁移状态。"""
    targets = _get_targets()
    if target_name not in targets:
        return {"error": f"未知目标: {target_name}"}

    config = targets[target_name]
    db_path: Path = config["db_path"]
    migrations_dir: Path = config["migrations_dir"]

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

    conn = sqlite3.connect(db_path)
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
