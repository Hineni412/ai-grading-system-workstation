---
status: accepted
date: 2026-08-09
supersedes: 0010 (temporary legacy reader and offline converter boundary)
---

# Stop reading and converting legacy class-teacher databases

The first retirement stage removed daily PIN, locking, password changes, and new encrypted writes while temporarily retaining a separate converter. The second stage removes that converter, all legacy credential handling, and all legacy decryption because the current product supports only its plaintext database format and the user has accepted that the current version no longer converts old files.

Normal class-teacher work, current plaintext data, and ordinary backup and restore remain unchanged. Before any class-teacher migration, recovery, read, or write, the runtime keeps only a non-decrypting format check: a legacy, mixed, damaged, or unknown database is rejected without modification or conversion guidance, while other application modules may continue. This decision does not claim that real legacy files were inventoried or converted; no real `user_data` was read for either retirement stage.
