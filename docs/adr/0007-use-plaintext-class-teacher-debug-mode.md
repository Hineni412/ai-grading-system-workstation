---
status: partially_superseded
date: 2026-08-02
supersedes: 0005 (only for the current class-teacher debug mode)
superseded_by: 0008 (privacy-preview behavior), 0010 (daily encryption runtime and legacy conversion boundary)
---

# Use plaintext class-teacher debug mode

> Historical decision record. ADR-0008 supersedes the outbound-preview behavior below, and ADR-0010 supersedes the temporary retention of daily legacy-encryption operations. The plaintext storage and ordinary-backup decision remains current.

For the current debugging stage, the production class-teacher feature opens the affairs and student surfaces without a PIN, password, session timeout, automatic lock, or sensitive-component mount gate. New student-affairs objects are stored as UTF-8 JSON in the existing SQLite compatibility schema. The class-teacher workspace is included in ordinary backups. Its SQLite files are integrity-checked, restored through SQLite backup semantics, and have stale WAL/SHM/journal companions removed during an offline restore. Class-teacher model calls use the shared diagnostics and retry policy without a durable one-call claim.

Ordinary work text is sent when the teacher clicks “生成 AI 草案”; it does not show a separate outbound preview or require a second send confirmation. Invalid, unavailable, changed-destination, and unknown results must be visible, including their error category and the physical request count reported by the shared retry gateway, and the teacher may generate again. A valid plan still remains a draft until the teacher confirms persistence.

The only retained privacy gate is for real student content sent to an external model: local minimization and anonymization, an exact outbound preview, and one explicit teacher confirmation remain mandatory. AI still cannot diagnose, determine bullying, decide discipline, send communications, close a case, or persist its draft without the existing business confirmation.

The two inspected class-teacher workspaces contained no `student_affairs.db`, so this change performs no real-data decryption migration. If a legacy encrypted database appears later, plaintext mode reports `legacy_migration_required` and stops all student reads and writes; it must never mix encrypted and plaintext rows. Migration remains a separately verified data operation. Re-enabling protection after debugging is a new decision and implementation task.
