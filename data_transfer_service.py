from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path


EXPORT_SIZE_WARNING_MB = 200.0


@dataclass(frozen=True)
class ExportEntry:
    source_path: Path
    arc_name: str
    size_bytes: int


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
        for path in sorted(source_root.rglob("*")):
            if not should_include_export_path(path, source_root, arc_root, scope):
                continue
            rel = path.relative_to(source_root).as_posix()
            arc_name = f"{arc_root}/{rel}"
            if arc_name in seen_arc_names:
                continue
            seen_arc_names.add(arc_name)
            entries.append(ExportEntry(source_path=path, arc_name=arc_name, size_bytes=path.stat().st_size))

    return sorted(entries, key=lambda entry: entry.arc_name)


def total_size_mb(entries: list[ExportEntry]) -> float:
    return sum(entry.size_bytes for entry in entries) / (1024 * 1024)


def create_export_zip_bytes(entries: list[ExportEntry]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for entry in entries:
            zf.write(entry.source_path, entry.arc_name)
    return buf.getvalue()
