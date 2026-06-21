# Task 5 Report

## Status

Done.

## Scope delivered

- Added `DBManager.list_incomplete_results(session_id)` and shared completeness resolution for:
  - structured `grading_completeness`
  - historical rows audited from current rubric + stored details
  - legacy `hybrid_batch_fallback`
- Kept incomplete graded rows visible through existing failed-only retry discovery.
- Added grading-page incomplete-results panel and one-click hybrid failed-only retry wiring in `web_app.py`.
- Added Excel completeness markers in `report.py`:
  - `批改完整性`
  - `缺失题目`
- Added focused Task 5 tests:
  - `tests/test_grading_completeness_ui.py`
  - `tests/test_report_completeness.py`

## TDD evidence

- RED:
  - `pytest tests/test_grading_completeness_ui.py tests/test_report_completeness.py -q`
  - initial failure: missing new `web_app` Task 5 helpers during test collection
- GREEN:
  - `pytest tests/test_grading_completeness_ui.py tests/test_report_completeness.py -q`
  - `5 passed`

## Verification

- `pytest tests/test_grading_completeness_ui.py tests/test_report_completeness.py tests/test_report_score_adjustment.py -q`
- Result: `11 passed`

## Files changed

- `db_manager.py`
- `web_app.py`
- `report.py`
- `tests/test_grading_completeness_ui.py`
- `tests/test_report_completeness.py`

## Concerns

- `web_app.py` already had unrelated local edits before Task 5; they were preserved and should stay out of the Task 5 commit.

## Review finding fix: safe incomplete-result summaries

- The legacy `list_failed_papers` path now uses a generic message for historically audited incomplete rows instead of exposing `last_failure_reason`.
- `list_incomplete_results` sanitizes retry/fallback errors before returning them:
  - removes embedded `data:image` payloads and long opaque data
  - redacts Authorization, Bearer, API-key, and `sk-` credentials
  - normalizes whitespace and limits summaries to 240 characters
- The grading-page row builder reuses the same sanitizer as a defense-in-depth rendering boundary.

### Review fix TDD evidence

- RED 1:
  - `pytest tests/test_grading_completeness_ui.py::test_incomplete_failure_reason_is_safe_at_db_and_render_boundaries -q`
  - failed because the DB list returned the raw `data:image/jpeg;base64,...` retry reason.
- GREEN 1:
  - same focused command
  - `1 passed`
- RED 2:
  - extended the regression with `Authorization: Basic auth-secret`
  - same focused command failed because `auth-secret` remained visible.
- GREEN 2:
  - same focused command
  - `1 passed`

### Review fix verification

- `pytest tests/test_grading_completeness_ui.py tests/test_report_completeness.py tests/test_retry_failed_grading.py tests/test_atomic_major_retry.py -q`
- Result: `24 passed`
