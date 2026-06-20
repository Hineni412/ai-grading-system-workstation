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
