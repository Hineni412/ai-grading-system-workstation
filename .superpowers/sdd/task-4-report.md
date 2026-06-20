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
