# P3-18 evidence-based performance decisions

- code SHA: `2c27e03c3fba39ad8d859ffce8481a394cb3f963`
- generated at UTC: `2026-07-25T06:29:25.463019Z`
- gate: p50 improvement >= 20%, p95 regression <= 5%

## Environment

- logical_cpu_count: `16`
- operating_system: `Windows-11-10.0.26200-SP0`
- python_version: `3.12.1`
- sqlite_version: `3.43.1`

## Candidate decisions

| candidate | decision | before p50 ms | before p95 ms | after p50 ms | after p95 ms | p50 improvement | p95 change | peak memory bytes | disk bytes |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| review_crop_cache | selected | 57.218 | 64.722 | 3.721 | 4.218 | 93.496% | -93.483% | 2744618 | 354772 |
| tag_projection_request_memo | rejected | 524.112 | 557.681 | n/a | n/a | n/a | n/a | 0 | 0 |
| image_compression_request_memo | selected | 1892.244 | 1963.404 | 693.176 | 719.019 | 63.368% | -63.379% | 23817844 | 0 |
| hot_sql_index | rejected | 16.251 | 21.943 | 33.923 | 38.559 | -108.747% | 75.724% | 0 | 28872 |

### review_crop_cache

- reason: Repeated review crop reads met both latency gates with exact JPEG bytes.
- dataset: {"cache_limit_bytes": 268435456, "image_count": 1, "image_height": 3200, "image_width": 2400, "repetition_results": [{"after_p50_ms": 3.731, "after_p95_ms": 4.2176, "before_p50_ms": 56.0224, "before_p95_ms": 60.4099, "cold_miss_ms": 70.9117, "qualified": true, "repetition": 1}, {"after_p50_ms": 3.69, "after_p95_ms": 4.0332, "before_p50_ms": 57.7255, "before_p95_ms": 64.7738, "cold_miss_ms": 67.6484, "qualified": true, "repetition": 2}], "repetitions": 2, "samples": 20, "source": "generated", "source_bytes": 2280075, "warmups": 3, "workload": "same_detail_repeated_read"}
- output equivalent: True
- invalidation: SHA-256 source bytes, region geometry, page and render version form the key; corrupt entries rebuild and explicit cleanup is supported.
- concurrency: One process-wide lock per cache directory and atomic replacement prevent partial reads.

### tag_projection_request_memo

- reason: Generated public diagnosis requests repeated no session projection identity within a request, so request-local memoization has no evidenced hit.
- dataset: {"maximum_request_local_repeat_count": 0, "projection_calls_per_request": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1], "request_samples": 20, "scale": "large_5pct", "source": "generated_public_diagnosis_requests"}
- output equivalent: True
- invalidation: not_applicable
- concurrency: not_applicable

### image_compression_request_memo

- reason: Generated hybrid grading retries met both latency gates with identical business output and a memo bounded to the request's unique images.
- dataset: {"attempts_per_request": [3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3], "maximum_memo_entries": 3, "maximum_repeated_image_identities": 6, "peak_memory_after_bytes": 23817844, "peak_memory_before_bytes": 23714725, "request_samples": 20, "retry_backoff_excluded": true, "source": "generated_public_hybrid_retry_requests", "unique_images_per_request": 3}
- output equivalent: True
- invalidation: SHA-256 source bytes key a memo discarded when the batch request returns.
- concurrency: Each concurrent batch owns a separate memo; no state is shared across requests.

### hot_sql_index

- reason: The generated public question-list experiment did not meet both frozen latency gates after accounting for write and disk evidence.
- dataset: {"candidate_disk_delta_bytes": 28872, "candidate_index_create_ms": 3.7657, "candidate_index_sql": "CREATE INDEX p3_18_candidate_questions_active_created ON questions(is_deleted, created_at DESC, id DESC)", "query_plan_after": ["SEARCH q USING INDEX p3_18_candidate_questions_active_created (is_deleted=?)", "SEARCH p USING INTEGER PRIMARY KEY (rowid=?) LEFT-JOIN"], "query_plan_before": ["SCAN q USING INDEX idx_questions_type_deleted", "SEARCH p USING INTEGER PRIMARY KEY (rowid=?) LEFT-JOIN", "USE TEMP B-TREE FOR DISTINCT", "USE TEMP B-TREE FOR ORDER BY"], "questions": 500, "request_samples": 20, "scale": "large_5pct", "source": "generated_public_question_list_requests", "write_p50_after_ms": 0.0428, "write_p50_before_ms": 0.0105, "write_p95_after_ms": 0.0534, "write_p95_before_ms": 0.021}
- output equivalent: True
- invalidation: Rejected candidate; no production index or invalidation change is applied.
- concurrency: Generated read snapshots and rolled-back writes use SQLite's normal isolation.

## Limitations

- All writes use generated data under a system temporary directory.
- The benchmark is machine-specific and is not a service-level objective.
- Memo decisions execute public request seams with generated inputs and a no-network fake model gateway.
- The SQL candidate is measured before and after only on a generated large_5pct database.
- Python tracemalloc does not include every native allocation performed inside Pillow.
