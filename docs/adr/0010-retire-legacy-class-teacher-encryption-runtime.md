---
status: accepted
date: 2026-08-09
supersedes: 0005 (daily class-teacher encryption runtime), 0007 (temporary legacy runtime compatibility)
---

# Retire the daily legacy class-teacher encryption runtime

## Context

The current class-teacher product stores new student-affairs objects as plaintext JSON in SQLite, includes the workspace in ordinary backups, and opens its four work surfaces without a PIN or unlock gate. The repository still retained daily vault endpoints, browser session parameters, password and PIN mutation paths, and code capable of creating new encrypted databases or ciphertext. Those paths were not part of the current interaction, but keeping them made the production boundary ambiguous and allowed obsolete encrypted writes to reappear.

Legacy encrypted databases may still exist outside the inspected workspaces. Treating one as a current plaintext database could mix storage formats and damage the only readable copy. Removing every legacy reader immediately would also remove the last controlled conversion path.

## Decision

Normal class-teacher runtime is plaintext-only. It does not expose PIN initialization, password or PIN unlock, recovery-driven password changes, manual or idle locking, session renewal, PIN or password changes, recovery-key acknowledgement, new encrypted-database creation, or new ciphertext writes. The browser does not carry a vault client, an unlock session parameter, or an `x-class-teacher-session` header. The existing `x-class-teacher-client` marker remains because it belongs to the current local write-request contract rather than authentication.

Before a normal student-affairs read or write, the runtime may identify a legacy encrypted database without modifying it. Once identified, it stops class-teacher data access before schema migration, interrupted-operation recovery, or any other write, and reports that offline conversion is required. Other application modules may continue operating.

Legacy decryption remains available only to one offline converter during the first retirement stage. The converter is not registered as a page, FastAPI route, background Job, workspace module, or ordinary backup action. It accepts an explicitly named legacy source and an existing credential, writes through a temporary candidate, validates every converted record plus SQLite integrity and foreign keys, and publishes only to a new output path. It never overwrites the source or installs the candidate as the active database. Using real data or replacing an active database requires separate authorization.

The retired `.ctbackup` format remains unsupported. Ordinary ZIP backup and restore continue to include the current plaintext class-teacher workspace and do not become a legacy conversion channel.

## Consequences

- Current teachers see the same home, calendar, affairs, and student workflows, without encryption controls or an extra migration page.
- New data cannot silently return to the obsolete encrypted format.
- Detecting a legacy database fails closed for class-teacher reads and writes while preserving its bytes.
- Conversion is explicit, offline, source-preserving, and testable with synthetic databases, but it is not a self-service daily workflow.
- The repository temporarily retains the minimum old key derivation and record-opening code needed by the converter. Removing that reader and its format documentation is a separately authorized second stage after all required legacy databases have been handled.
- The historical threat model remains useful evidence of what the old format attempted to protect, but it no longer defines current production endpoints, page gates, backup policy, or the default future security design.
