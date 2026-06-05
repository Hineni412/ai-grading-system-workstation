# Storage Policy

This document describes local data storage for the AI grading system.

## Data Categories

| Category | Path | Role | Safe To Regenerate |
|---|---|---|---|
| Databases | `user_data/databases/` | Grading and question-bank state | No |
| Uploaded exams | `user_data/exams/` | Source scans and PDF renders | Partly |
| Templates | `user_data/templates/` | Session templates and source PDFs | Partly |
| Annotated images | `user_data/annotated/` | Rendered grading annotations | Yes, if database records and source images remain |
| Reports | `user_data/reports/` | Exported Excel/PDF/page images | Page image folders yes; final exported files no |
| Backups | `user_data/backups/` | Manual and automatic backups | No, but old copies can be expired |
| Question bank assets | `user_data/question_bank/` | Raw papers, extracted images, rich content | Partly |
| Development outputs | `user_data/outputs/` | Benchmarks and comparison artifacts | Yes |

## Retention Defaults

- Keep all databases.
- Keep the latest 3 full zip backups.
- Keep the latest 20 database backup snapshots.
- Keep final report files unless a teacher removes them.
- Archive report page image directories older than 7 days.
- Archive benchmark outputs older than 7 days.
- Archive comparison outputs older than 14 days.
- Remove temporary files older than 1 day after a dry-run report.

## Deduplication Defaults

Only immutable binary artifacts may be deduplicated by hardlink:

- Template PDFs and images.
- Report page images.
- Annotated images.
- Enhanced scan images.
- PDF-rendered page images.

Never deduplicate databases, JSON config, YAML config, or files modified in the last 24 hours.

## Required Workflow

1. Run `python tools/storage_audit.py --root .`.
2. Review `user_data/reports/storage_audit/latest_summary.md`.
3. Run `python tools/storage_maintenance.py --root . --dry-run`.
4. Review the planned changes.
5. Run with `--apply` only after confirming the plan.
