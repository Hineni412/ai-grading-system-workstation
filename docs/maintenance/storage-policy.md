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

Storage tools can inspect or change real business files. Every run needs authorization for that specific operation; a previous audit or maintenance approval does not carry forward.

1. Obtain authorization to generate the read-only audit report.
2. Run `runtime\python\python.exe tools\storage_audit.py --root .`.
3. Review `user_data/reports/storage_audit/latest_summary.md`.
4. Run `runtime\python\python.exe tools\storage_maintenance.py --root .` to preview the supported maintenance actions.
5. Review the exact planned files and operation type.
6. Only after the user explicitly authorizes this real-data change, run the relevant operation separately with `--apply-hardlinks` or `--apply-archives`.

The tools do not provide generic preview/apply switches beyond the commands listed above. Never infer permission to delete, archive, hardlink, or rewrite data from permission to inspect it.
