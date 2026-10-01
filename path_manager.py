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
import re
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent
_WORKSPACE_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


class UncontrolledStoredFilePathError(ValueError):
    """A persisted path points outside every explicitly controlled root."""


class AmbiguousStoredFilePathError(ValueError):
    """A filename-only legacy lookup matched more than one controlled file."""

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
    except (OSError, UnicodeError):
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

        taxonomy_state_override = os.getenv("AI_GRADING_TAXONOMY_STATE_PATH")
        if taxonomy_state_override:
            self._taxonomy_state_path = (
                Path(taxonomy_state_override).expanduser().resolve()
            )
        else:
            local_appdata = os.getenv("LOCALAPPDATA")
            if local_appdata:
                taxonomy_config_root = (
                    Path(local_appdata) / "AIGradingSystem" / "config"
                )
            else:
                taxonomy_config_root = (
                    Path.home() / ".ai_grading_system" / "config"
                )
            self._taxonomy_state_path = (
                taxonomy_config_root / "taxonomy_state_v2.json"
            )

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
    def taxonomy_state_path(self) -> Path:
        """Machine-local teacher-approved taxonomy overlay and review queue."""
        return self._taxonomy_state_path

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

    def workspace_dir(self, workspace_id: str, *, create: bool = False) -> Path:
        """Return one validated workspace root without creating it by default."""
        clean_id = str(workspace_id or "").strip()
        if not _WORKSPACE_ID.fullmatch(clean_id):
            raise ValueError("workspace ID is invalid")

        data_root = self._data_root.resolve(strict=False)
        workspace_root = self._data_root / "workspaces"
        resolved_workspace_root = workspace_root.resolve(strict=False)
        try:
            resolved_workspace_root.relative_to(data_root)
        except ValueError as exc:
            raise ValueError("workspace root is outside the controlled data root") from exc
        if workspace_root.exists() and _is_reparse_point(workspace_root):
            raise ValueError("workspace root cannot be a reparse point")

        candidate = workspace_root / clean_id
        resolved_candidate = candidate.resolve(strict=False)
        try:
            resolved_candidate.relative_to(resolved_workspace_root)
        except ValueError as exc:
            raise ValueError("workspace path is outside the controlled root") from exc
        if candidate.exists() and _is_reparse_point(candidate):
            raise ValueError("workspace path cannot be a reparse point")

        if create:
            candidate.mkdir(parents=True, exist_ok=True)
            if _is_reparse_point(workspace_root) or _is_reparse_point(candidate):
                raise ValueError("workspace path cannot be a reparse point")
            resolved_candidate = candidate.resolve(strict=True)
            try:
                resolved_candidate.relative_to(
                    self._data_root.resolve(strict=True)
                )
            except ValueError as exc:
                raise ValueError(
                    "workspace path is outside the controlled data root"
                ) from exc
        return candidate

    @property
    def version(self) -> str:
        # VERSION file is the single source of truth
        version_file = self._project_root / "VERSION"
        if version_file.exists():
            try:
                return version_file.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeError):
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
            "标签词表状态": str(self.taxonomy_state_path),
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_instance: PathManager | None = None


def _is_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(
            attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
    except OSError:
        return False


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
    project directory. Existing paths are accepted only beneath the current
    data/config roots or an explicitly supplied search root. Legacy paths are
    remapped inside those roots; a filename fallback is used only when unique.
    """
    text = str(path_value or "").strip()
    path = Path(text)
    if not text:
        return path
    pm = get_path_manager() if project_root is None or data_root is None else None
    root = Path(
        project_root if project_root is not None else pm.project_root
    ).resolve(strict=False)
    data = Path(
        data_root if data_root is not None else pm.data_root
    ).resolve(strict=False)
    explicit_roots = [
        Path(candidate).expanduser().resolve(strict=False)
        for candidate in (search_roots or ())
    ]
    project_subroots = [
        resolved
        for resolved in (
            (root / "config" / "uploaded").resolve(strict=False),
            (root / "templates").resolve(strict=False),
            (root / "config").resolve(strict=False),
        )
        if resolved == root or resolved.is_relative_to(root)
    ]
    controlled_roots = _unique_paths(
        [
            data,
            *project_subroots,
            *explicit_roots,
        ]
    )

    if ".." in path.parts:
        raise UncontrolledStoredFilePathError(
            "stored file path cannot traverse outside its controlled root"
        )
    if path.exists():
        return _require_controlled_file(path, controlled_roots)

    candidates: list[Path] = []

    if not path.is_absolute():
        candidates.extend([root / path, data / path])

    parts = path.parts
    lowered = [part.lower() for part in parts]
    for marker in ("user_data", "data"):
        if marker in lowered:
            idx = lowered.index(marker)
            if idx + 1 < len(parts):
                candidates.append(data.joinpath(*parts[idx + 1:]))

    exact = _unique_controlled_existing_files(candidates, controlled_roots)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise AmbiguousStoredFilePathError(text)

    filename = path.name
    fallback: list[Path] = []
    if filename:
        fallback_roots = _unique_paths(
            [
                *explicit_roots,
                data / "config" / "uploaded",
                data / "templates",
                data / "config",
                data,
                *project_subroots,
            ]
        )
        for search_root in fallback_roots:
            if not search_root.exists():
                continue
            fallback.append(search_root / filename)
            try:
                fallback.extend(search_root.rglob(filename))
            except OSError:
                continue
    matches = _unique_controlled_existing_files(fallback, controlled_roots)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AmbiguousStoredFilePathError(text)

    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if _is_below_any_root(resolved, controlled_roots):
            return resolved
    raise UncontrolledStoredFilePathError(
        "stored file path is outside every controlled root"
    )


def _unique_paths(values: Iterable[Path]) -> list[Path]:
    unique: list[Path] = []
    for value in values:
        resolved = Path(value).resolve(strict=False)
        if resolved not in unique:
            unique.append(resolved)
    return unique


def _is_below_any_root(path: Path, roots: Iterable[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def _require_controlled_file(path: Path, roots: Iterable[Path]) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or not _is_below_any_root(resolved, roots):
        raise UncontrolledStoredFilePathError(
            "stored file path is outside every controlled root"
        )
    return resolved


def _unique_controlled_existing_files(
    values: Iterable[Path],
    roots: Iterable[Path],
) -> list[Path]:
    unique: set[Path] = set()
    for value in values:
        try:
            if not value.is_file():
                continue
            resolved = value.resolve(strict=True)
        except OSError:
            continue
        if _is_below_any_root(resolved, roots):
            unique.add(resolved)
    return sorted(unique, key=lambda item: str(item).casefold())
