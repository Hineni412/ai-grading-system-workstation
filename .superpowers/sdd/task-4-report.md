# Task 4 Report: Atomic whole-major retry and repeatable failures

## Status

Implemented and verified.

## Changes

- Added transactional `DBManager.replace_result_details_atomic(...)` persistence that preserves the parent result row and annotation linkage.
- Included graded `incomplete` and `invalid` completeness states in both failed-paper queries while preserving legacy `hybrid_batch_fallback` matching.
- Changed failed-only hybrid retry selection to retry every part of each affected parent major question while preserving and skipping unaffected questions.
- Re-audited merged details before accepting a retry; incomplete, duplicate, unexpected, out-of-range, fallback, and persistence failures preserve old score data.
- Added uncapped structured `grading_retry_attempts` records for per-paper and whole-batch failures so subsequent one-click retries remain eligible.
- Preserved the legacy independent Q1/Q2 incremental retry path when no completeness-affected major is available.

## TDD and verification

- RED: `pytest tests/test_atomic_major_retry.py tests/test_retry_failed_grading.py -q` failed on whole-major selection, repeatable attempt records, and incomplete/invalid eligibility.
- Additional RED: the whole-batch exception regression failed because no retry attempt was recorded.
- GREEN: `pytest tests/test_atomic_major_retry.py tests/test_retry_failed_grading.py -q` passed with 10 tests.
- Related regression: `pytest tests/test_grading_completeness.py tests/test_atomic_major_retry.py tests/test_retry_failed_grading.py -q` passed before the final exception-path addition; the final verification reruns this combined suite.
- Python compilation and `git diff --check` are included in final verification.

## Self-review

- Transaction rollback is exercised using an insertion constraint failure after deletion begins.
- Successful whole-major replacement verifies unchanged result ID, preserved Q1, replaced Q10 parts, unchanged annotation linkage, recalculated score, and complete re-audit.
- Missing only Q10(2) verifies Q10(1) is not skipped and both Q10 parts are replaced.
- Two consecutive incomplete retries verify uncapped failure attempts and renewed failed-paper eligibility.
- Whole-batch failure verifies old details, score, result ID, and incomplete marker remain unchanged.
- No `user_data` files were read, modified, staged, or committed.

## Concerns

- Retry timestamps use local process time with numeric timezone offset; they are structured audit metadata only and do not affect eligibility.
- Legacy results without a usable completeness audit continue through the prior incremental merge path for compatibility.

## Review fixes

### Behavior corrected

- Structured completeness rows can no longer enter destructive legacy `save_session_result` persistence. If an invalid/incomplete audit cannot map an issue such as unexpected `Q99` to a parent, the retry targets every rubric parent major and atomically replaces all old details.
- The all-major fallback preserves the existing result ID and annotation linkage while removing unmappable stale details.
- `DBManager.replace_result_details_atomic(...)` now reads the post-replacement rows inside its transaction, recomputes the score from those rows, runs `audit_grading_details(...)`, overwrites caller completeness with that fresh audit, and accepts only `complete`.
- Caller-provided `student_score` and `grading_completeness` are not trusted. Duplicate, out-of-range, incomplete, insertion, and audit failures raise inside the transaction and restore old details, score, and raw audit.
- Legacy incremental merge remains available only when the existing row has no structured completeness audit and no mappable affected parent major.

### Fix TDD evidence

- RED: `pytest tests/test_atomic_major_retry.py -q` produced `6 failed, 4 passed`. The Q99 execution test showed one call to `save_session_result`; transaction tests failed because the database interface did not accept a rubric and did not perform stored-row re-audit.
- GREEN: `pytest tests/test_atomic_major_retry.py -q` produced `10 passed` after the safe all-major path and transaction-local score/audit gate were implemented.
- GREEN regression: `pytest tests/test_atomic_major_retry.py tests/test_retry_failed_grading.py tests/test_grading_completeness.py -q` produced `27 passed`.

### Added review regressions

- Unexpected structured `Q99` retries all rubric majors without calling legacy persistence and preserves result ID plus annotation.
- A stale caller score of `-999` is replaced with the stored-detail sum, and a stale invalid caller audit is replaced with a fresh complete audit.
- Duplicate, out-of-range, and incomplete replacements each roll back all detail and result changes.
- A fallback-marked retry preserves the previous incomplete major, score, result ID, and retry eligibility.

## Third review fix wave

### Behavior corrected

- A structured invalid audit containing any unexpected ID that cannot map to a rubric parent now forces an all-major retry even when another issue already maps to a major such as Q10.
- All-major atomic replacement removes every prior detail question ID, including unmappable `Q99`, before inserting the full replacement. The parent result ID and annotation linkage remain unchanged.
- The original `replace_result_details_atomic(result_id, remove_question_ids, replacement_details, *, student_score, needs_human_review, raw_json)` call shape is compatible again. `rubric` is an optional trailing keyword.
- Calls without rubric retain atomic delete/insert/update behavior and recompute the final score from stored rows. The grading-service production path passes rubric and therefore retains transaction-local completeness audit and rollback gating.
- Unmappable unexpected escalation is limited to rows with structured completeness metadata, preserving the genuinely legacy no-audit Q1/Q2 incremental path.

### Third-wave TDD evidence

- RED: the two new targeted tests produced `2 failed`. The compatibility call raised `TypeError: missing 1 required keyword-only argument: 'rubric'`; the mixed Q10+Q99 retry incorrectly skipped `Q1` and `Q99` instead of targeting all majors.
- Initial focused regression exposed one legacy compatibility failure because an audit synthesized from a no-audit legacy row was also treated as structured. Root-cause tracing showed `has_unmapped_unexpected` needed the existing structured-audit guard.
- GREEN targeted: compatibility, mixed invalid, and legacy incremental tests produced `3 passed`.
- GREEN focused suite: `pytest tests/test_atomic_major_retry.py tests/test_retry_failed_grading.py tests/test_grading_completeness.py -q` produced `29 passed`.

### Added regressions

- Mixed missing Q10 part plus unexpected Q99 retries Q1 and all Q10 parts, removes Q99, preserves result ID and annotation, and finishes complete.
- The original database signature without rubric atomically replaces details and corrects a stale caller score from stored values without raising `TypeError`.
