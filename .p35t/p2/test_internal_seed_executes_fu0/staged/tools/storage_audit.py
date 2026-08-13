from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


PROTECTED_EXTENSIONS = {".db", ".sqlite", ".sqlite3", ".json", ".yaml", ".yml"}
SECONDS_PER_DAY = 24 * 60 * 60


@dataclass(frozen=True)
class FileRecord:
    path: str
    size: int
    sha256: str
    category: str


@dataclass(frozen=True)
class DuplicateGroup:
    sha256: str
    count: int
    size: int
    recoverable_bytes: int
    category: str
    paths: list[str]


@dataclass(frozen=True)
class RetentionCandidate:
    path: str
    reason: str
    size: int
    age_days: float


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_user_data_path(path: Path, data_root: Path) -> str:
    try:
        relative = path.relative_to(data_root)
    except ValueError:
        return "outside_user_data"
    return relative.parts[0] if relative.parts else "user_data"


def is_protected_path(path: Path, data_root: Path) -> bool:
    try:
        relative = path.relative_to(data_root)
    except ValueError:
        return True
    if not relative.parts:
        return True
    if relative.parts[0] == "databases":
        return True
    return path.suffix.lower() in PROTECTED_EXTENSIONS


def scan_file_records(data_root: Path) -> list[FileRecord]:
    records: list[FileRecord] = []
    if not data_root.exists():
        return records
    for path in sorted(data_root.rglob("*")):
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size <= 0:
            continue
        records.append(
            FileRecord(
                path=str(path),
                size=size,
                sha256=file_sha256(path),
                category=classify_user_data_path(path, data_root),
            )
        )
    return records


def duplicate_groups(records: list[FileRecord]) -> list[DuplicateGroup]:
    by_hash: dict[str, list[FileRecord]] = {}
    for record in records:
        by_hash.setdefault(record.sha256, []).append(record)

    groups: list[DuplicateGroup] = []
    for sha256, items in by_hash.items():
        if len(items) < 2:
            continue
        size = items[0].size
        categories = sorted({item.category for item in items})
        groups.append(
            DuplicateGroup(
                sha256=sha256,
                count=len(items),
                size=size,
                recoverable_bytes=size * (len(items) - 1),
                category="+".join(categories),
                paths=[item.path for item in items],
            )
        )
    return sorted(groups, key=lambda group: group.recoverable_bytes, reverse=True)


def directory_totals(records: list[FileRecord]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for record in records:
        totals[record.category] = totals.get(record.category, 0) + record.size
    return dict(sorted(totals.items(), key=lambda item: item[1], reverse=True))


def _tree_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def _age_days(path: Path, now_timestamp: float) -> float:
    return max(0.0, (now_timestamp - path.stat().st_mtime) / SECONDS_PER_DAY)


def _older_than(path: Path, days: int, now_timestamp: float) -> bool:
    return _age_days(path, now_timestamp) >= float(days)


def find_retention_candidates(data_root: Path, *, now_timestamp: float | None = None) -> list[RetentionCandidate]:
    now = now_timestamp if now_timestamp is not None else datetime.now().timestamp()
    candidates: list[RetentionCandidate] = []

    reports_dir = data_root / "reports"
    if reports_dir.exists():
        for path in sorted(reports_dir.glob("*_批注原卷页面_*")):
            if path.is_dir() and _older_than(path, 7, now):
                candidates.append(
                    RetentionCandidate(
                        path=str(path),
                        reason="old_report_page_dir",
                        size=_tree_size(path),
                        age_days=round(_age_days(path, now), 2),
                    )
                )

    outputs_dir = data_root / "outputs"
    if outputs_dir.exists():
        for path in sorted(outputs_dir.glob("benchmark_*")):
            if path.is_dir() and _older_than(path, 7, now):
                candidates.append(
                    RetentionCandidate(
                        path=str(path),
                        reason="old_benchmark_output",
                        size=_tree_size(path),
                        age_days=round(_age_days(path, now), 2),
                    )
                )
        for path in sorted(outputs_dir.glob("*comparison*")):
            if path.is_dir() and _older_than(path, 14, now):
                candidates.append(
                    RetentionCandidate(
                        path=str(path),
                        reason="old_comparison_output",
                        size=_tree_size(path),
                        age_days=round(_age_days(path, now), 2),
                    )
                )

    temp_dir = data_root / "temp"
    if temp_dir.exists():
        for path in sorted(temp_dir.iterdir()):
            if _older_than(path, 1, now):
                candidates.append(
                    RetentionCandidate(
                        path=str(path),
                        reason="old_temp_item",
                        size=_tree_size(path),
                        age_days=round(_age_days(path, now), 2),
                    )
                )

    backups_dir = data_root / "backups"
    if backups_dir.exists():
        zip_backups = sorted(backups_dir.glob("backup_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in zip_backups[3:]:
            if _older_than(path, 30, now):
                candidates.append(
                    RetentionCandidate(
                        path=str(path),
                        reason="old_zip_backup_beyond_latest_3",
                        size=path.stat().st_size,
                        age_days=round(_age_days(path, now), 2),
                    )
                )

        db_backups = sorted(backups_dir.glob("grading_before_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in db_backups[20:]:
            if _older_than(path, 14, now):
                candidates.append(
                    RetentionCandidate(
                        path=str(path),
                        reason="old_db_backup_beyond_latest_20",
                        size=path.stat().st_size,
                        age_days=round(_age_days(path, now), 2),
                    )
                )

    return sorted(candidates, key=lambda item: item.size, reverse=True)


def tracked_user_data_files(project_root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false", "ls-files", "user_data"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def write_reports(
    project_root: Path,
    records: list[FileRecord],
    groups: list[DuplicateGroup],
    retention_candidates: list[RetentionCandidate],
) -> Path:
    out_dir = project_root / "user_data" / "reports" / "storage_audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"audit_{timestamp}.json"
    duplicates_csv_path = out_dir / f"duplicates_{timestamp}.csv"
    retention_csv_path = out_dir / f"retention_candidates_{timestamp}.csv"
    summary_path = out_dir / "latest_summary.md"

    totals = directory_totals(records)
    tracked = tracked_user_data_files(project_root)
    payload = {
        "generated_at": timestamp,
        "file_count": len(records),
        "total_bytes": sum(record.size for record in records),
        "directory_totals": totals,
        "duplicate_group_count": len(groups),
        "duplicate_file_count": sum(group.count for group in groups),
        "recoverable_bytes": sum(group.recoverable_bytes for group in groups),
        "tracked_user_data_files": tracked,
        "retention_candidates": [asdict(candidate) for candidate in retention_candidates],
        "duplicates": [asdict(group) for group in groups],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with duplicates_csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sha256", "count", "size_mb", "recoverable_mb", "category", "paths"])
        for group in groups:
            writer.writerow(
                [
                    group.sha256,
                    group.count,
                    round(group.size / 1024 / 1024, 3),
                    round(group.recoverable_bytes / 1024 / 1024, 3),
                    group.category,
                    " | ".join(group.paths),
                ]
            )

    with retention_csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["reason", "size_mb", "age_days", "path"])
        for candidate in retention_candidates:
            writer.writerow(
                [
                    candidate.reason,
                    round(candidate.size / 1024 / 1024, 3),
                    candidate.age_days,
                    candidate.path,
                ]
            )

    summary_lines = [
        "# Storage Audit Summary",
        "",
        f"- Files scanned: {len(records)}",
        f"- Total size MB: {sum(record.size for record in records) / 1024 / 1024:.2f}",
        f"- Duplicate groups: {len(groups)}",
        f"- Duplicate files: {sum(group.count for group in groups)}",
        f"- Estimated recoverable MB: {sum(group.recoverable_bytes for group in groups) / 1024 / 1024:.2f}",
        f"- Retention candidates: {len(retention_candidates)}",
        f"- Tracked user_data files: {len(tracked)}",
        "",
        "## Largest Directories",
        "",
    ]
    for category, size in list(totals.items())[:10]:
        summary_lines.append(f"- `{category}`: {size / 1024 / 1024:.2f} MB")
    summary_lines.extend(
        [
            "",
            f"JSON report: `{json_path.name}`",
            f"Duplicate CSV: `{duplicates_csv_path.name}`",
            f"Retention CSV: `{retention_csv_path.name}`",
            "",
        ]
    )
    summary_path.write_text("\n".join(summary_lines), encoding="utf-8")
    return summary_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only user_data storage audit.")
    parser.add_argument("--root", default=".", help="Project root")
    args = parser.parse_args()
    project_root = Path(args.root).resolve()
    data_root = project_root / "user_data"
    records = scan_file_records(data_root)
    groups = duplicate_groups(records)
    retention = find_retention_candidates(data_root)
    summary = write_reports(project_root, records, groups, retention)
    print(f"Wrote storage audit summary: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
