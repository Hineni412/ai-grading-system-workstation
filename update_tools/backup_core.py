"""备份与恢复系统 — 核心逻辑模块。

被 backup_data.py / restore_backup.py / list_backups.py 与当前运维服务共用。
"""

from __future__ import annotations

import logging
import json
import os
import re
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import closing
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
    """返回写入 logs/backup.log 的 logger。"""
    logger = logging.getLogger("backup_system")
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
        # 控制台输出
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter(_LOG_FORMAT))
        logger.addHandler(ch)
    return logger


# ── 常量 ──────────────────────────────────────────────

VALID_REASONS = ("before_exam", "before_update", "before_import", "before_restore", "manual", "after_exam")
VALID_BACKUP_SCOPES = ("grading", "class_teacher")
DEFAULT_BACKUP_SCOPES = ("grading", "class_teacher")

# 不备份的模式
_SKIP_PATTERNS = {
    ".venv",
    "__pycache__",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    "node_modules",
}

_SKIP_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".tmp",
    ".temp",
    ".swp",
    ".swo",
}

# 默认不备份 API key 文件
_SENSITIVE_FILES = {"api_profiles.json"}

_WORKSPACE_DERIVED_DATABASE_DIRS = {
    "backups",
    "cache",
    "work-backups",
}


def _backup_root() -> Path:
    """获取备份 zip 存放目录（项目根/backups/）。"""
    try:
        from path_manager import get_path_manager
        return get_path_manager().backups_dir
    except Exception:
        return _PROJECT_ROOT / "backups"


def _get_pm():
    """获取 PathManager 实例。"""
    from path_manager import get_path_manager
    return get_path_manager()


# ── 备份逻辑 ──────────────────────────────────────────

def _should_skip(rel_path: Path) -> bool:
    """检查是否应跳过该路径。"""
    parts = rel_path.parts
    for part in parts:
        if part in _SKIP_PATTERNS:
            return True
    folded = tuple(part.casefold() for part in parts)
    if (
        len(folded) >= 5
        and folded[:2] == ("user_data", "workspaces")
        and folded[2] in {"class-teacher"}
        and any(
            part in _WORKSPACE_DERIVED_DATABASE_DIRS
            for part in folded[3:-1]
        )
        and folded[-1].endswith((".db", ".sqlite", ".sqlite3"))
    ):
        return True
    if rel_path.suffix.lower() in _SKIP_EXTENSIONS:
        return True
    return False


def _is_sensitive(rel_path: Path) -> bool:
    """检查是否为敏感文件（含 API key）。"""
    sensitive = {name.casefold() for name in _SENSITIVE_FILES}
    return rel_path.name.casefold() in sensitive


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def preview_backup(
    *,
    path_manager: Any,
    include_api_keys: bool = False,
    include_logs: bool = False,
    scopes: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    """Collect the existing backup manifest without creating logs or directories."""
    pm = path_manager
    selected = set(scopes or DEFAULT_BACKUP_SCOPES)
    if not selected or selected - set(VALID_BACKUP_SCOPES):
        raise ValueError("invalid backup scopes")
    backup_sources: list[tuple[Path, str]] = []
    if "grading" in selected:
        backup_sources.extend([
            (pm.data_root / "databases", "user_data/databases"),
            (pm.data_root / "exams", "user_data/exams"),
            (pm.data_root / "question_bank", "user_data/question_bank"),
            (pm.data_root / "outputs", "user_data/outputs"),
            (pm.data_root / "config", "user_data/config"),
            (pm.data_root / "templates", "user_data/templates"),
            (pm.data_root / "annotated", "user_data/annotated"),
            (pm.data_root / "reports", "user_data/reports"),
            (pm.data_root / "snapshots", "user_data/snapshots"),
            (pm.project_root / "config", "config"),
        ])
    if "class_teacher" in selected:
        backup_sources.append((
            pm.data_root / "workspaces" / "class-teacher",
            "user_data/workspaces/class-teacher",
        ))
    if include_logs:
        backup_sources.append((pm.logs_dir, "logs"))

    files: list[str] = []
    skipped: list[str] = []
    skipped_sensitive: list[str] = []
    total_size = 0
    for source_dir, prefix in backup_sources:
        if not source_dir.exists():
            continue
        from data_transfer_service import ensure_controlled_path

        ensure_controlled_path(source_dir, source_dir)
        for item in sorted(source_dir.rglob("*")):
            ensure_controlled_path(item, source_dir)
            if not item.is_file():
                continue
            try:
                rel = item.relative_to(source_dir)
            except ValueError:
                continue
            full_rel = Path(prefix) / rel
            public_name = full_rel.as_posix()
            if public_name.endswith((".db-wal", ".db-shm", ".db-journal")):
                skipped.append(public_name)
                continue
            if prefix == "user_data/databases" and public_name not in {
                "user_data/databases/grading_system.db",
                "user_data/databases/question_bank.db",
            }:
                skipped.append(public_name)
                continue
            if _should_skip(full_rel):
                skipped.append(public_name)
                continue
            if _is_sensitive(full_rel) and not include_api_keys:
                skipped_sensitive.append(public_name)
                continue
            files.append(public_name)
            try:
                total_size += max(0, int(item.stat().st_size))
            except OSError:
                pass
    return {
        "file_count": len(files),
        "total_size": total_size,
        "files": files,
        "skipped": skipped,
        "skipped_sensitive": skipped_sensitive,
    }


def _safe_restore_destination(
    member: str,
    *,
    project_root: Path,
    data_root: Path,
    logs_root: Path,
) -> Path | None:
    normalized = Path(member.replace("\\", "/"))
    parts = normalized.parts
    if not parts or normalized.is_absolute() or ".." in parts:
        return None

    if parts[0] == "user_data":
        if len(parts) > 1 and parts[1].casefold() == "workspaces":
            return None
        dest = data_root.joinpath(*parts[1:])
        return dest if _is_relative_to(dest, data_root) else None
    if parts[0] == "config":
        dest = project_root.joinpath(*parts)
        return dest if _is_relative_to(dest, project_root / "config") else None
    if parts[0] == "logs":
        dest = logs_root.joinpath(*parts[1:])
        return dest if _is_relative_to(dest, logs_root) else None
    return None


def create_backup(
    reason: str = "manual",
    *,
    include_api_keys: bool = False,
    include_logs: bool = False,
    dry_run: bool = False,
    scopes: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    """创建 user_data + config 的 zip 备份。

    Args:
        reason: 备份原因标签 (before_exam/before_update/manual/after_exam)
        include_api_keys: 是否包含 API key 明文文件
        include_logs: 是否包含 logs/ 目录
        dry_run: 只列出会备份的文件，不实际创建 zip

    Returns:
        dict with keys:
          - "zip_path": str | None  (dry_run 时为 None)
          - "file_count": int
          - "total_size": int  (字节)
          - "files": list[str]  (相对路径列表)
          - "skipped": list[str]
          - "skipped_sensitive": list[str]
          - "error": str | None
    """
    logger = _get_logger()
    result: dict[str, Any] = {
        "zip_path": None,
        "file_count": 0,
        "total_size": 0,
        "files": [],
        "skipped": [],
        "skipped_sensitive": [],
        "error": None,
    }

    if reason not in VALID_REASONS:
        result["error"] = f"无效的备份原因: {reason}，可选: {', '.join(VALID_REASONS)}"
        logger.error(result["error"])
        return result

    try:
        pm = _get_pm()
    except Exception as exc:
        result["error"] = f"PathManager 加载失败: {exc}"
        logger.error(result["error"])
        return result

    # 构建备份文件名
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_name = f"backup_{ts}_{reason}.zip"
    backup_dir = _backup_root()
    backup_dir.mkdir(parents=True, exist_ok=True)
    zip_path = backup_dir / zip_name

    # 收集要备份的目录
    selected = set(scopes or DEFAULT_BACKUP_SCOPES)
    if not selected or selected - set(VALID_BACKUP_SCOPES):
        result["error"] = "备份范围无效"
        return result
    backup_sources: list[tuple[Path, str]] = []
    if "grading" in selected:
        backup_sources.extend([
            (pm.data_root / "databases", "user_data/databases"),
            (pm.data_root / "exams", "user_data/exams"),
            (pm.data_root / "question_bank", "user_data/question_bank"),
            (pm.data_root / "outputs", "user_data/outputs"),
            (pm.data_root / "config", "user_data/config"),
            (pm.data_root / "templates", "user_data/templates"),
            (pm.data_root / "annotated", "user_data/annotated"),
            (pm.data_root / "reports", "user_data/reports"),
            (pm.data_root / "snapshots", "user_data/snapshots"),
            (pm.project_root / "config", "config"),
        ])
    if "class_teacher" in selected:
        backup_sources.append((pm.data_root / "workspaces" / "class-teacher", "user_data/workspaces/class-teacher"))
    if include_logs:
        backup_sources.append((pm.logs_dir, "logs"))

    # 收集文件列表
    files_to_add: list[tuple[Path, str]] = []  # (abs_path, arcname)

    for source_dir, prefix in backup_sources:
        if not source_dir.exists():
            continue
        for item in sorted(source_dir.rglob("*")):
            if not item.is_file():
                continue
            try:
                rel = item.relative_to(source_dir)
            except ValueError:
                continue
            full_rel = Path(prefix) / rel

            if full_rel.as_posix().endswith((".db-wal", ".db-shm", ".db-journal")):
                result["skipped"].append(str(full_rel))
                continue

            if _should_skip(full_rel):
                result["skipped"].append(str(full_rel))
                continue
            if _is_sensitive(full_rel) and not include_api_keys:
                result["skipped_sensitive"].append(str(full_rel))
                continue

            arcname = str(full_rel).replace("\\", "/")
            files_to_add.append((item, arcname))
            result["files"].append(arcname)
            try:
                result["total_size"] += item.stat().st_size
            except OSError:
                pass

    result["file_count"] = len(files_to_add)

    if dry_run:
        logger.info(
            "[DRY-RUN] 将备份 %d 个文件，预计大小 %s，跳过 %d 个，敏感文件跳过 %d 个",
            result["file_count"],
            _format_bytes(result["total_size"]),
            len(result["skipped"]),
            len(result["skipped_sensitive"]),
        )
        return result

    # 创建 zip
    logger.info("开始备份: %s (原因: %s)", zip_name, reason)
    try:
        with tempfile.TemporaryDirectory(prefix="ordinary-backup-") as temp_value:
            snapshot_root = Path(temp_value)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for index, (abs_path, arcname) in enumerate(files_to_add):
                    try:
                        source = abs_path
                        if abs_path.suffix.casefold() == ".db":
                            source = snapshot_root / f"{index}.db"
                            with closing(sqlite3.connect(abs_path)) as current, closing(
                                sqlite3.connect(source)
                            ) as snapshot:
                                current.backup(snapshot)
                        zf.write(source, arcname)
                    except Exception as exc:
                        logger.warning("跳过文件 %s: %s", arcname, exc)
                        result["skipped"].append(f"{arcname} (写入失败: {exc})")

        result["zip_path"] = str(zip_path)
        zip_size = zip_path.stat().st_size
        logger.info(
            "备份完成: %s, 包含 %d 个文件, 压缩后 %s",
            zip_name, result["file_count"], _format_bytes(zip_size),
        )
    except Exception as exc:
        result["error"] = f"创建 zip 文件失败: {exc}"
        logger.error(result["error"])
        # 清理失败的 zip 文件
        if zip_path.exists():
            try:
                zip_path.unlink()
            except OSError:
                pass

    return result


# ── 列出备份 ──────────────────────────────────────────

def list_backups(*, backup_dir: Path | None = None) -> list[dict[str, Any]]:
    """列出所有 zip 备份，按时间降序。"""
    resolved_backup_dir = Path(backup_dir) if backup_dir is not None else _backup_root()
    if not resolved_backup_dir.exists():
        return []

    backups = []
    for zp in sorted(resolved_backup_dir.glob("backup_*.zip"), reverse=True):
        try:
            stat = zp.stat()
            # 解析文件名: backup_YYYYMMDD_HHMMSS_reason.zip
            stem = zp.stem  # backup_YYYYMMDD_HHMMSS_reason
            parts = stem.split("_", 3)
            if len(parts) >= 4:
                date_str = parts[1]
                time_str = parts[2]
                reason = parts[3]
                try:
                    dt = datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S")
                    display_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    display_time = "未知"
                    reason = stem
            else:
                display_time = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                reason = stem

            backups.append({
                "filename": zp.name,
                "path": str(zp),
                "size": stat.st_size,
                "size_display": _format_bytes(stat.st_size),
                "time": display_time,
                "reason": reason,
                "mtime": stat.st_mtime,
            })
        except OSError:
            continue

    return backups


# ── 恢复逻辑 ──────────────────────────────────────────

def restore_backup(
    zip_path: str | Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """只读预览旧备份；正式恢复必须使用现代离线 Ops 流程。

    Args:
        zip_path: 备份 zip 文件路径
        dry_run: 只列出会恢复的文件，不实际操作

    Returns:
        dict with keys:
          - "pre_backup_zip": str | None
          - "restored_files": list[str]
          - "skipped_files": list[str]
          - "error": str | None
    """
    result: dict[str, Any] = {
        "pre_backup_zip": None,
        "restored_files": [],
        "skipped_files": [],
        "error": None,
    }

    zip_path = Path(zip_path)
    # Keep the legacy write API inert even before PathManager/log setup, since
    # those helpers can create directories.  Only dry-run may inspect state.
    if not dry_run:
        result["error"] = "legacy_restore_write_disabled"
        return result

    logger = _get_logger()
    if not zip_path.exists():
        result["error"] = f"备份文件不存在: {zip_path}"
        logger.error(result["error"])
        return result

    if not zipfile.is_zipfile(zip_path):
        result["error"] = f"不是有效的 zip 文件: {zip_path}"
        logger.error(result["error"])
        return result

    try:
        pm = _get_pm()
    except Exception as exc:
        result["error"] = f"PathManager 加载失败: {exc}"
        logger.error(result["error"])
        return result

    # 解析 zip 中的文件
    project_root = pm.project_root
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.namelist():
                # 跳过目录
                if member.endswith("/"):
                    continue

                dest = _safe_restore_destination(
                    member,
                    project_root=project_root,
                    data_root=pm.data_root,
                    logs_root=pm.logs_dir,
                )
                if dest is None:
                    result["skipped_files"].append(f"{member} (unsafe_or_unknown_path)")
                    continue

                result["restored_files"].append(f"{member} -> {dest}")

    except Exception as exc:
        result["error"] = f"读取 zip 文件失败: {exc}"
        logger.error(result["error"])
        return result

    logger.info("[DRY-RUN] 将恢复 %d 个文件", len(result["restored_files"]))

    return result


# ── 工具函数 ──────────────────────────────────────────

def _format_bytes(size_bytes: int) -> str:
    """格式化字节数。"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
