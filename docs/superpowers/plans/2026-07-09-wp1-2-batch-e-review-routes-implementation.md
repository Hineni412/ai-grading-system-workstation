# WP1.2 Batch E - Review Routes Implementation

## Goal

Expose the existing teacher review workflow through FastAPI routes while preserving the current Streamlit review behavior.

## Scope

- Reuse `DBManager.get_session_results()` and `DBManager.get_result_details()` for review reads.
- Reuse the existing review detection semantics from the Streamlit page:
  - already reviewed categories are not reviewable,
  - explicit review markers are reviewable,
  - confidence below 80 is reviewable.
- Add API routes for:
  - question-level review summary,
  - question-level review items,
  - confirming score adjustments for one question.
- Reuse `ManualReviewService.apply_manual_adjustments()` for writes and annotation regeneration.
- Do not implement global full-score audit, batch class-wide score adjustment, annotated image streaming, or new review status schema in this batch.

## Steps

1. Add failing API tests for summary, item listing, confirmation, and missing-session error shape.
2. Add review Pydantic schemas.
3. Add a review service dependency for `ManualReviewService`.
4. Add `backend/api/routers/review.py` and register it in the app.
5. Run focused review tests, API regression, full tests, and quick smoke check.

## Acceptance Checks

- `GET /api/sessions/{id}/review/questions` returns per-question totals and needs-review counts.
- `GET /api/sessions/{id}/review/questions/{qid}/items` returns review rows sorted by student code/name.
- `POST /api/sessions/{id}/review/questions/{qid}/confirm` groups adjustments by result and calls `ManualReviewService.apply_manual_adjustments()`.
- Missing sessions return the unified `session_not_found` API error.
