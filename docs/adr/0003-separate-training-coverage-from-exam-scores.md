---
status: accepted
---

# Separate training coverage evidence from formal exam scores

Personalized training uses versioned, non-numeric training criteria and per-point outcomes rather than reusing the formal exam rubric and score result model. The visible result is each question's achieved-point count over its frozen total-point count. It is training evidence, not a 100-point score, grade, rank, or replacement for a teacher-confirmed exam result.

Question-bank analysis may return controlled tags and training criteria in one physical model response when the context and output budget allow. The two projections are validated, stored, reviewed, retried, and versioned independently. A unit value of `1` may be compiled inside a legacy model adapter, but it is never persisted or exposed as a score.

Each personalized paper instance freezes its question snapshots, `task_item_code` values, criterion version IDs, criterion snapshots, and content hashes. Each printed page carries a signed machine-readable paper-instance identity and page number. Mixed scans are routed and checked for missing, duplicate, conflicting, or foreign pages before any model request.

Training assessment is a separate module from formal exam grading. One complete student submission produces at most one physical whole-paper request. The model returns one of `met`, `not_met`, `uncertain`, or `unreadable` for every expected point; the application validates point identity and computes counts locally. Uncertain or unreadable points require teacher review, and teacher final decisions cannot be overwritten by later AI runs.

Only finalized per-question evidence is published to mastery aggregation. Formal exam scoring, reports, ranking, teacher score locks, and current `AIGrader` semantics remain unchanged. Cross-database publication uses stable idempotency identities and reconciliation rather than pretending to have a distributed transaction.

Reusing the existing numeric rubric was rejected because its required score fields, 100-point validation, and report semantics would make training evidence look like formal achievement. Storing permanent one-point placeholder rubrics was rejected because it would weaken quality checks and make criterion counts accidentally behave like scores. Making the model compute achieved totals was rejected because identity validation and counting are deterministic local responsibilities.
