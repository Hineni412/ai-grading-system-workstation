from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from path_manager import PathManager
from question_bank.services.question_read_service import (
    QuestionBankSnapshotError,
    captured_sqlite_snapshot_path,
)
from update_tools.backup_core import list_backups as _list_zip_backups
from update_tools.migrate_db import get_migration_status


_TOOL_KEYS = ("microsoft_word", "libreoffice", "pdflatex")
_SAFE_BACKUP_REASONS = {
    "after_exam",
    "before_exam",
    "before_import",
    "before_restore",
    "before_update",
    "manual",
}


def _default_tool_checker(key: str) -> bool:
    if key == "pdflatex":
        return shutil.which("pdflatex") is not None
    if key == "libreoffice":
        candidates = (
            shutil.which("soffice"),
            shutil.which("libreoffice"),
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        )
    elif key == "microsoft_word":
        candidates = (
            shutil.which("WINWORD.EXE"),
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Microsoft Office"
            / "root"
            / "Office16"
            / "WINWORD.EXE",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
            / "Microsoft Office"
            / "root"
            / "Office16"
            / "WINWORD.EXE",
        )
    else:
        return False
    return any(candidate and Path(candidate).is_file() for candidate in candidates)


class OpsSelfCheckService:
    def __init__(
        self,
        paths: PathManager,
        *,
        tool_checker: Callable[[str], bool] | None = None,
        zip_backup_loader: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.paths = paths
        self._tool_checker = tool_checker or _default_tool_checker
        self._zip_backup_loader = zip_backup_loader or _list_zip_backups

    def build_snapshot(self) -> dict[str, Any]:
        directories = [
            self._directory_check(key, path)
            for key, path in (
                ("data", self.paths.data_root),
                ("databases", self.paths.databases_dir),
                ("backups", self.paths.backups_dir),
                ("logs", self.paths.logs_dir),
                ("reports", self.paths.reports_dir),
                ("outputs", self.paths.outputs_dir),
            )
        ]
        databases = [
            self._database_check("grading", self.paths.db_path, "grading"),
            self._database_check(
                "question_bank",
                self.paths.qb_db_path,
                "question_bank",
            ),
        ]
        tools = []
        warnings: list[str] = []
        for key in _TOOL_KEYS:
            try:
                available = bool(self._tool_checker(key))
            except Exception:
                available = False
            tools.append(
                {
                    "key": key,
                    "available": available,
                    "status": "ok" if available else "warning",
                }
            )
            if not available:
                warnings.append(f"tool_unavailable:{key}")

        api_configured, api_warning = self._api_configured()
        if api_warning:
            warnings.append(api_warning)
        for item in directories:
            if item["status"] != "ok":
                warnings.append(f"directory_{item['status']}:{item['key']}")
        for item in databases:
            if item["status"] != "ok":
                warnings.append(f"database_{item['status']}:{item['key']}")

        statuses = [item["status"] for item in directories + databases]
        statuses.extend(item["status"] for item in tools)
        overall = "error" if "error" in statuses else "warning" if warnings else "ok"
        return {
            "version": str(self.paths.version),
            "status": overall,
            "api_configured": api_configured,
            "directories": directories,
            "databases": databases,
            "tools": tools,
            "warnings": warnings,
        }

    def list_backups(self, limit: int) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 100))
        candidates: list[tuple[float, dict[str, Any]]] = []
        for raw in self._zip_backup_loader():
            filename = Path(str(raw.get("filename") or "")).name
            if not filename.lower().endswith(".zip"):
                continue
            reason = str(raw.get("reason") or "unknown").split(None, 1)[0]
            if reason not in _SAFE_BACKUP_REASONS:
                reason = "unknown"
            mtime = _safe_float(raw.get("mtime"))
            candidates.append(
                (
                    mtime,
                    {
                        "kind": "zip",
                        "filename": filename,
                        "created_at": str(raw.get("time") or _iso_time(mtime)),
                        "reason": reason,
                        "size_bytes": max(0, _safe_int(raw.get("size"))),
                    },
                )
            )
        try:
            database_backups = list(self.paths.backups_dir.glob("*.db"))
        except OSError:
            database_backups = []
        for backup in database_backups:
            try:
                stat = backup.stat()
            except OSError:
                continue
            candidates.append(
                (
                    stat.st_mtime,
                    {
                        "kind": "database",
                        "filename": backup.name,
                        "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(
                            timespec="seconds"
                        ),
                        "reason": "database_automatic",
                        "size_bytes": max(0, int(stat.st_size)),
                    },
                )
            )
        candidates.sort(key=lambda entry: (-entry[0], entry[1]["filename"]))
        items = [item for _mtime, item in candidates[:bounded_limit]]
        return {"items": items, "returned": len(items), "limit": bounded_limit}

    @staticmethod
    def _directory_check(key: str, directory: Path) -> dict[str, Any]:
        path = Path(directory)
        exists = path.is_dir()
        writable = _probe_writable(path) if exists else False
        return {
            "key": key,
            "exists": exists,
            "writable": writable,
            "status": "ok" if exists and writable else "error",
        }

    @staticmethod
    def _database_check(key: str, source: Path, target: str) -> dict[str, Any]:
        source = Path(source)
        if not source.is_file():
            return {
                "key": key,
                "exists": False,
                "size_bytes": 0,
                "integrity": "missing",
                "migration_version": "0",
                "pending_migrations": 0,
                "status": "error",
            }
        size = _safe_file_size(source)
        try:
            with captured_sqlite_snapshot_path(
                source,
                required_tables=frozenset(),
            ) as candidate:
                migration = get_migration_status(
                    target,
                    db_path_override=candidate,
                )
            pending = len(migration.get("pending") or [])
            return {
                "key": key,
                "exists": True,
                "size_bytes": size,
                "integrity": "ok",
                "migration_version": str(migration.get("schema_version") or "0"),
                "pending_migrations": pending,
                "status": "warning" if pending else "ok",
            }
        except (OSError, QuestionBankSnapshotError):
            return {
                "key": key,
                "exists": True,
                "size_bytes": size,
                "integrity": "unavailable",
                "migration_version": "0",
                "pending_migrations": 0,
                "status": "error",
            }

    def _api_configured(self) -> tuple[bool, str | None]:
        path = Path(self.paths.api_profiles_path)
        if not path.is_file():
            return False, "api_not_configured"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False, "api_config_unavailable"
        if not isinstance(payload, list):
            return False, "api_config_unavailable"
        configured = any(
            isinstance(profile, dict) and str(profile.get("api_key") or "").strip()
            for profile in payload
        )
        return configured, None if configured else "api_not_configured"


def _probe_writable(directory: Path) -> bool:
    probe = directory / f".ops-write-probe-{uuid4().hex}"
    try:
        with probe.open("xb"):
            pass
        return True
    except OSError:
        return False
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass


def _safe_file_size(path: Path) -> int:
    try:
        return max(0, int(path.stat().st_size))
    except OSError:
        return 0


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _iso_time(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return "unknown"
