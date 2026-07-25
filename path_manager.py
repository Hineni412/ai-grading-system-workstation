"""Unified path manager for AI Grading System.

Provides a single source of truth for all data-related paths so that
program code (replaceable) and user data (persistent) stay separated.

Resolution order:
1. ``AI_GRADING_WORKTREE_DATA_DIR`` for an explicitly isolated worktree
2. ``config/app_config.yaml`` – DATA_DIR / LOGS_DIR keys
3. ``AI_GRADING_DATA_DIR`` environment variable (legacy compat)
4. Default: ``<project_root>/user_data``
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

_PROJECT_ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# YAML loader – tiny built-in parser to avoid adding PyYAML dependency
# ---------------------------------------------------------------------------

def _load_yaml_simple(path: Path) -> dict[str, Any]:
    """Read a *flat* key: value YAML file.  Supports strings only."""
    result: dict[str, Any] = {}
    if not path.exists():
        return result
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip("'\"")
            if value:
                result[key] = value
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# PathManager
# ---------------------------------------------------------------------------

class PathManager:
    """Centralised path resolution for the AI Grading System."""

    def __init__(self) -> None:
        self._project_root = _PROJECT_ROOT
        self._cfg = _load_yaml_simple(self._project_root / "config" / "app_config.yaml")

        # --- resolve data root ---
        worktree_data_override = os.getenv("AI_GRADING_WORKTREE_DATA_DIR")
        configured_data = self._cfg.get("DATA_DIR")
        if worktree_data_override:
            self._data_root = Path(worktree_data_override).expanduser().resolve()
        elif configured_data:
            p = Path(configured_data)
            self._data_root = p if p.is_absolute() else (self._project_root / p).resolve()
        else:
            env = os.getenv("AI_GRADING_DATA_DIR")
            if env:
                self._data_root = Path(env).resolve()
            else:
                self._data_root = self._project_root / "user_data"

        # --- resolve logs root ---
        configured_logs = self._cfg.get("LOGS_DIR")
        if configured_logs:
            p = Path(configured_logs)
            self._logs_root = p if p.is_absolute() else (self._project_root / p).resolve()
        else:
            self._logs_root = self._project_root / "logs"

        # API credentials are machine-local configuration, not project data.
        # Keeping this path outside the repository prevents branch/worktree
        # changes from replacing the saved credentials.
        api_profiles_override = os.getenv("AI_GRADING_API_PROFILES_PATH")
        if api_profiles_override:
            self._api_profiles_path = Path(api_profiles_override).expanduser().resolve()
        else:
            local_appdata = os.getenv("LOCALAPPDATA")
            if local_appdata:
                api_config_root = Path(local_appdata) / "AIGradingSystem" / "config"
            else:
                api_config_root = Path.home() / ".ai_grading_system" / "config"
            self._api_profiles_path = api_config_root / "api_profiles.json"

        ops_state_override = os.getenv("AI_GRADING_OPS_STATE_DIR")
        if ops_state_override:
            self._ops_state_dir = Path(ops_state_override).expanduser().resolve()
        else:
            local_appdata = os.getenv("LOCALAPPDATA")
            if local_appdata:
                ops_state_root = Path(local_appdata) / "AIGradingSystem" / "ops"
            else:
                ops_state_root = Path.home() / ".ai_grading_system" / "ops"
            self._ops_state_dir = ops_state_root.resolve()

        # --- propagate to env so legacy code keeps working ---
        os.environ["AI_GRADING_DATA_DIR"] = str(self._data_root)

    # -- directory properties ------------------------------------------------

    @property
    def project_root(self) -> Path:
        return self._project_root

    @property
    def data_root(self) -> Path:
        """Root of all persistent user data (``user_data/``)."""
        return self._data_root

    @property
    def logs_dir(self) -> Path:
        return self._logs_root

    @property
    def databases_dir(self) -> Path:
        return self._data_root / "databases"

    @property
    def db_path(self) -> Path:
        """Grading system SQLite database."""
        return self.databases_dir / "grading_system.db"

    @property
    def qb_db_path(self) -> Path:
        """Question-bank SQLite database."""
        return self.databases_dir / "question_bank.db"

    @property
    def exams_dir(self) -> Path:
        return self._data_root / "exams"

    @property
    def config_dir(self) -> Path:
        return self._data_root / "config"

    @property
    def upload_config_dir(self) -> Path:
        return self.config_dir / "uploaded"

    @property
    def api_profiles_path(self) -> Path:
        return self._api_profiles_path

    @property
    def ops_state_dir(self) -> Path:
        """Machine-local Ops state excluded from data restore/export roots."""
        return self._ops_state_dir

    @property
    def legacy_api_profiles_paths(self) -> tuple[Path, ...]:
        """Project-relative profile locations accepted only for migration."""
        candidates = (
            self.config_dir / "api_profiles.json",
            self._project_root / "config" / "api_profiles.json",
            self._project_root / "data" / "config" / "api_profiles.json",
        )
        unique: list[Path] = []
        for candidate in candidates:
            if candidate != self.api_profiles_path and candidate not in unique:
                unique.append(candidate)
        return tuple(unique)

    @property
    def templates_dir(self) -> Path:
        return self._data_root / "templates"

    @property
    def annotated_dir(self) -> Path:
        return self._data_root / "annotated"

    @property
    def reports_dir(self) -> Path:
        return self._data_root / "reports"

    @property
    def qb_data_dir(self) -> Path:
        """Question-bank ancillary data (raw papers, images, etc.)."""
        return self._data_root / "question_bank"

    @property
    def outputs_dir(self) -> Path:
        return self._data_root / "outputs"

    @property
    def snapshots_dir(self) -> Path:
        return self._data_root / "snapshots"

    @property
    def backups_dir(self) -> Path:
        return self._data_root / "backups"

    # -- helpers -------------------------------------------------------------

    @property
    def version(self) -> str:
        # VERSION file is the single source of truth
        version_file = self._project_root / "VERSION"
        if version_file.exists():
            try:
                return version_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        return str(self._cfg.get("VERSION", "0.0.0"))

    def ensure_directories(self) -> None:
        """Create every data directory that should exist."""
        for directory in (
            self._data_root,
            self.databases_dir,
            self.exams_dir,
            self.config_dir,
            self.upload_config_dir,
            self.templates_dir,
            self.annotated_dir,
            self.reports_dir,
            self.qb_data_dir,
            self.outputs_dir,
            self.snapshots_dir,
            self.backups_dir,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def summary(self) -> dict[str, str]:
        """Return a human-readable mapping of logical names → actual paths."""
        return {
            "项目根目录": str(self.project_root),
            "数据根目录": str(self.data_root),
            "阅卷数据库": str(self.db_path),
            "题库数据库": str(self.qb_db_path),
            "考试答卷": str(self.exams_dir),
            "上传配置": str(self.upload_config_dir),
            "模板文件": str(self.templates_dir),
            "批注结果": str(self.annotated_dir),
            "报表导出": str(self.reports_dir),
            "题库数据": str(self.qb_data_dir),
            "组卷输出": str(self.outputs_dir),
            "数据库备份": str(self.backups_dir),
            "快照": str(self.snapshots_dir),
            "日志目录": str(self.logs_dir),
            "API 配置": str(self.api_profiles_path),
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_instance: PathManager | None = None


def get_path_manager() -> PathManager:
    """Return the global ``PathManager`` singleton (created on first call)."""
    global _instance
    if _instance is None:
        _instance = PathManager()
        _instance.ensure_directories()
    return _instance


def resolve_stored_file_path(
    path_value: object,
    *,
    data_root: Path | None = None,
    project_root: Path | None = None,
    search_roots: Iterable[Path] | None = None,
) -> Path:
    """Resolve a persisted file path after the app/data directory moved.

    Older records may store absolute paths from another machine or a previous
    project directory. Prefer the stored path when it still exists, then try to
    remap anything under ``user_data`` to the current data root, and finally
    search the common data locations by filename.
    """
    text = str(path_value or "").strip()
    path = Path(text)
    if text and path.exists():
        return path

    pm = get_path_manager()
    root = project_root or pm.project_root
    data = data_root or pm.data_root
    candidates: list[Path] = []

    if text:
        if not path.is_absolute():
            candidates.extend([root / path, data / path])

        parts = path.parts
        lowered = [part.lower() for part in parts]
        for marker in ("user_data", "data"):
            if marker in lowered:
                idx = lowered.index(marker)
                if idx + 1 < len(parts):
                    candidates.append(data.joinpath(*parts[idx + 1:]))

        filename = path.name
        if filename:
            roots = list(search_roots or [])
            roots.extend(
                [
                    data / "config" / "uploaded",
                    data / "templates",
                    data / "config",
                    data,
                    root / "config" / "uploaded",
                    root / "templates",
                    root / "config",
                ]
            )
            for search_root in roots:
                if not search_root.exists():
                    continue
                direct = search_root / filename
                candidates.append(direct)
                try:
                    candidates.extend(search_root.rglob(filename))
                except OSError:
                    continue

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        if candidate.exists() and candidate.is_file():
            return candidate
    return path
