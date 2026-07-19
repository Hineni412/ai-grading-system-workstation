from __future__ import annotations

import io
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path


EXPORT_SIZE_WARNING_MB = 200.0


@dataclass(frozen=True)
class ExportEntry:
    source_path: Path
    arc_name: str
    size_bytes: int
    source_root: Path | None = None


COMMON_SKIP_DIR_NAMES = {
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
}

COMMON_SKIP_EXTENSIONS = {
    ".db-journal",
    ".db-shm",
    ".db-wal",
    ".log",
    ".pyc",
    ".pyo",
    ".tmp",
}

SENSITIVE_FILE_NAMES = {
    "api_profiles.json",
}

LEAN_SKIP_USER_DATA_TOP_LEVEL = {
    "annotated",
    "archives",
    "backups",
    "outputs",
    "reports",
    "snapshots",
    "temp",
    "tmp",
}

LEAN_SKIP_ANY_DIR = {
    "_cropped",
    "_enhanced",
    "_pdf_pages",
    "extracted_images",
    "previews",
    "storage_audit",
}

LEAN_SKIP_FILE_NAMES = {
    "template_source_full_class.pdf",
}


def default_export_sources(project_root: Path, data_root: Path | None = None) -> list[tuple[Path, str]]:
    root = Path(project_root)
    user_data = Path(data_root) if data_root is not None else root / "user_data"
    return [(user_data, "user_data"), (root / "config", "config")]


def normalize_export_scope(scope: str) -> str:
    normalized = str(scope or "lean").strip().lower()
    if normalized not in {"lean", "full"}:
        raise ValueError(f"Unknown export scope: {scope}")
    return normalized


def should_include_export_path(path: Path, source_root: Path, arc_root: str, scope: str) -> bool:
    scope = normalize_export_scope(scope)
    if not path.is_file():
        return False

    rel_parts = path.relative_to(source_root).parts
    if any(part in COMMON_SKIP_DIR_NAMES for part in rel_parts[:-1]):
        return False
    if path.suffix.lower() in COMMON_SKIP_EXTENSIONS:
        return False
    if path.name.lower() in SENSITIVE_FILE_NAMES:
        return False

    if scope == "full":
        return True

    if arc_root == "user_data":
        lower_parts = tuple(part.lower() for part in rel_parts)
        if lower_parts and lower_parts[0] in LEAN_SKIP_USER_DATA_TOP_LEVEL:
            return False
        if any(part in LEAN_SKIP_ANY_DIR for part in lower_parts[:-1]):
            return False
        if path.name.lower() in LEAN_SKIP_FILE_NAMES:
            return False

    return True


def build_export_manifest(sources: list[tuple[Path, str]], scope: str = "lean") -> list[ExportEntry]:
    scope = normalize_export_scope(scope)
    entries: list[ExportEntry] = []
    seen_arc_names: set[str] = set()

    for source_root, arc_root in sources:
        source_root = Path(source_root)
        if not source_root.exists():
            continue
        ensure_controlled_path(source_root, source_root)
        for path in sorted(source_root.rglob("*")):
            ensure_controlled_path(path, source_root)
            if not should_include_export_path(path, source_root, arc_root, scope):
                continue
            rel = path.relative_to(source_root).as_posix()
            arc_name = f"{arc_root}/{rel}"
            if arc_name in seen_arc_names:
                continue
            seen_arc_names.add(arc_name)
            entries.append(
                ExportEntry(
                    source_path=path,
                    arc_name=arc_name,
                    size_bytes=path.stat().st_size,
                    source_root=source_root,
                )
            )

    return sorted(entries, key=lambda entry: entry.arc_name)


def total_size_mb(entries: list[ExportEntry]) -> float:
    return sum(entry.size_bytes for entry in entries) / (1024 * 1024)


def create_export_zip_bytes(entries: list[ExportEntry]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for entry in entries:
            validate_export_entry(entry)
            zf.write(entry.source_path, entry.arc_name)
    return buf.getvalue()


def write_export_zip(entries: list[ExportEntry], destination: Path) -> Path:
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for entry in entries:
            validate_export_entry(entry)
            zf.write(entry.source_path, entry.arc_name)
    return output


def ensure_controlled_path(path: Path, source_root: Path) -> None:
    candidate = Path(path)
    root = Path(source_root)
    resolved_root = root.resolve(strict=False)
    current = candidate
    while True:
        if current.exists() or current.is_symlink():
            if _is_reparse_point(current):
                raise ValueError("export source contains a reparse point")
        if current.resolve(strict=False) == resolved_root:
            break
        if current.parent == current:
            raise ValueError("export source is outside controlled root")
        current = current.parent
    try:
        candidate.resolve(strict=False).relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("export source is outside controlled root") from exc


def validate_export_entry(entry: ExportEntry) -> None:
    source = Path(entry.source_path)
    root = Path(entry.source_root) if entry.source_root is not None else source.parent
    ensure_controlled_path(source, root)
    if not source.is_file() or source.is_symlink():
        raise ValueError("export source is not a regular file")
    if int(source.stat().st_size) != int(entry.size_bytes):
        raise ValueError("export source changed after manifest creation")


def _is_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except OSError:
        return False
