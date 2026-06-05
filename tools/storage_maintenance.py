from __future__ import annotations

import argparse
import os
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.storage_audit import find_retention_candidates, file_sha256, scan_file_records, duplicate_groups


MIN_HARDLINK_SIZE_BYTES = 128 * 1024
SECONDS_PER_DAY = 24 * 60 * 60
ALLOWED_HARDLINK_SUFFIXES = {".jpg", ".jpeg", ".png", ".pdf"}
ALLOWED_HARDLINK_TOP_DIRS = {"annotated", "templates", "reports", "exams"}


@dataclass(frozen=True)
class DedupeAction:
    canonical: Path
    duplicate: Path
    bytes_saved: int


@dataclass(frozen=True)
class ArchiveAction:
    source: Path
    archive_path: Path
    reason: str
    bytes_archived: int


def _top_dir(path: Path, data_root: Path) -> str:
    try:
        relative = path.relative_to(data_root)
    except ValueError:
        return ""
    return relative.parts[0] if relative.parts else ""


def _age_seconds(path: Path, now_timestamp: float) -> float:
    return max(0.0, now_timestamp - path.stat().st_mtime)


def is_hardlink_eligible(path: Path, data_root: Path, *, now_timestamp: float | None = None) -> bool:
    now = now_timestamp if now_timestamp is not None else __import__("datetime").datetime.now().timestamp()
    if not path.exists() or not path.is_file():
        return False
    if _top_dir(path, data_root) not in ALLOWED_HARDLINK_TOP_DIRS:
        return False
    if path.suffix.lower() not in ALLOWED_HARDLINK_SUFFIXES:
        return False
    if path.stat().st_size < MIN_HARDLINK_SIZE_BYTES:
        return False
    if _age_seconds(path, now) < SECONDS_PER_DAY:
        return False
    try:
        parts = set(path.relative_to(data_root).parts)
    except ValueError:
        return False
    if "databases" in parts or "config" in parts:
        return False
    return True


def _same_drive(left: Path, right: Path) -> bool:
    return left.resolve().drive.lower() == right.resolve().drive.lower()


def build_dedupe_actions(data_root: Path, *, now_timestamp: float | None = None) -> list[DedupeAction]:
    records = scan_file_records(data_root)
    groups = duplicate_groups(records)
    actions: list[DedupeAction] = []
    for group in groups:
        paths = [Path(path) for path in group.paths]
        eligible = [path for path in paths if is_hardlink_eligible(path, data_root, now_timestamp=now_timestamp)]
        if len(eligible) < 2:
            continue
        canonical = eligible[0]
        for duplicate in eligible[1:]:
            if not _same_drive(canonical, duplicate):
                continue
            if file_sha256(canonical) != file_sha256(duplicate):
                continue
            actions.append(DedupeAction(canonical=canonical, duplicate=duplicate, bytes_saved=duplicate.stat().st_size))
    return actions


def _safe_archive_name(source: Path) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in source.name) or "archive"


def build_archive_actions(data_root: Path, *, now_timestamp: float | None = None) -> list[ArchiveAction]:
    candidates = find_retention_candidates(data_root, now_timestamp=now_timestamp)
    actions: list[ArchiveAction] = []
    for candidate in candidates:
        source = Path(candidate.path)
        if not source.exists():
            continue
        archive_dir = data_root / "archives" / candidate.reason
        archive_path = archive_dir / f"{_safe_archive_name(source)}.zip"
        actions.append(
            ArchiveAction(
                source=source,
                archive_path=archive_path,
                reason=candidate.reason,
                bytes_archived=candidate.size,
            )
        )
    return actions


def apply_hardlink(action: DedupeAction) -> None:
    temp_path = action.duplicate.with_suffix(action.duplicate.suffix + ".dedupe_tmp")
    if temp_path.exists():
        temp_path.unlink()
    action.duplicate.rename(temp_path)
    try:
        os.link(action.canonical, action.duplicate)
    except Exception:
        temp_path.rename(action.duplicate)
        raise
    temp_path.unlink()


def apply_archive(action: ArchiveAction) -> None:
    action.archive_path.parent.mkdir(parents=True, exist_ok=True)
    if action.archive_path.exists():
        raise FileExistsError(f"Archive already exists: {action.archive_path}")
    with zipfile.ZipFile(action.archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        if action.source.is_file():
            archive.write(action.source, action.source.name)
        else:
            for item in sorted(action.source.rglob("*")):
                if item.is_file():
                    archive.write(item, item.relative_to(action.source.parent).as_posix())


def _print_actions(dedupe_actions: list[DedupeAction], archive_actions: list[ArchiveAction]) -> None:
    print(f"Planned hardlink actions: {len(dedupe_actions)}")
    print(f"Estimated hardlink saved MB: {sum(action.bytes_saved for action in dedupe_actions) / 1024 / 1024:.2f}")
    for action in dedupe_actions[:50]:
        print(f"hardlink: {action.duplicate} -> {action.canonical}")

    print(f"Planned archive actions: {len(archive_actions)}")
    print(f"Estimated archived MB: {sum(action.bytes_archived for action in archive_actions) / 1024 / 1024:.2f}")
    for action in archive_actions[:50]:
        print(f"archive: {action.source} -> {action.archive_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run-first storage maintenance.")
    parser.add_argument("--root", default=".", help="Project root")
    parser.add_argument("--apply-hardlinks", action="store_true", help="Apply safe hardlink dedupe actions")
    parser.add_argument("--apply-archives", action="store_true", help="Create zip archives for retention candidates")
    args = parser.parse_args()

    project_root = Path(args.root).resolve()
    data_root = project_root / "user_data"
    dedupe_actions = build_dedupe_actions(data_root)
    archive_actions = build_archive_actions(data_root)
    _print_actions(dedupe_actions, archive_actions)

    if not args.apply_hardlinks and not args.apply_archives:
        print("Dry run only. Re-run with --apply-hardlinks or --apply-archives to make changes.")
        return 0

    if args.apply_hardlinks:
        for action in dedupe_actions:
            apply_hardlink(action)
        print(f"Applied hardlink actions: {len(dedupe_actions)}")

    if args.apply_archives:
        for action in archive_actions:
            apply_archive(action)
        print(f"Created archives: {len(archive_actions)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
