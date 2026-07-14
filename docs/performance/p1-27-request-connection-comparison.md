# P1-27 request connection comparison

- P1-26 provenance code SHA: `6bb53c338d23e530ed2890afbdecce0ae6ac9fb9`
- code SHA: `65fde607a2e4fef3874a970b03e4fcb2bcd43d69`
- generated at UTC: `2026-07-14T04:21:59.897665Z`
- seed: `126`
- scales: `small, medium, large_5pct`
- scenarios: `question_bank.questions.default, training.diagnosis, training.plan.preview, graph.profiles, graph.rows, graph.evidence`
- before/after modes: `legacy_per_call/request_scoped`
- data scale factor: `0.1`
- warmups/samples/repetitions: `3/2/2`
- repeatability: `passed`
- overall gate: `passed`

## Runtime

| Windows version | Python version | SQLite version | logical CPU |
|---|---|---|---:|
| Windows-11-10.0.26200 | 3.12.1 | 3.43.1 | 16 |

## Aggregate comparisons

| scale | repetition | scenario | kind | status before/after | response records before/after | before p50 ms | after p50 ms | p50 improvement | DB statements before/after | statement reduction | DB selects before/after | statement gate | medium p50 gate |
|---|---:|---|---|---|---|---:|---:|---:|---|---:|---|---|---|
| small | 1 | question_bank.questions.default | control | 200/200 | 20/20 | 20.9984 | 20.1664 | 3.962207% | 11/11 | 0% | 6/6 | not_required | not_required |
| small | 1 | training.diagnosis | target | 200/200 | 3/3 | 98.2139 | 49.4347 | 49.66629% | 1043/24 | 97.698945% | 14/12 | passed | not_required |
| small | 1 | training.plan.preview | target | 200/200 | 3/3 | 470.0349 | 73.2059 | 84.425433% | 7136/45 | 99.369395% | 59/33 | passed | not_required |
| small | 1 | graph.profiles | target | 200/200 | 3/3 | 89.4947 | 48.4333 | 45.881376% | 1043/24 | 97.698945% | 14/12 | passed | not_required |
| small | 1 | graph.rows | target | 200/200 | 3/3 | 90.301 | 49.9268 | 44.71069% | 1043/24 | 97.698945% | 14/12 | passed | not_required |
| small | 1 | graph.evidence | target | 200/200 | 3/3 | 93.5572 | 49.6998 | 46.877632% | 1043/24 | 97.698945% | 14/12 | passed | not_required |
| small | 2 | question_bank.questions.default | control | 200/200 | 20/20 | 20.2325 | 22.3362 | -10.397627% | 11/11 | 0% | 6/6 | not_required | not_required |
| small | 2 | training.diagnosis | target | 200/200 | 3/3 | 93.2582 | 51.2611 | 45.033145% | 1043/24 | 97.698945% | 14/12 | passed | not_required |
| small | 2 | training.plan.preview | target | 200/200 | 3/3 | 475.474 | 71.5932 | 84.942773% | 7136/45 | 99.369395% | 59/33 | passed | not_required |
| small | 2 | graph.profiles | target | 200/200 | 3/3 | 91.517 | 47.328 | 48.285018% | 1043/24 | 97.698945% | 14/12 | passed | not_required |
| small | 2 | graph.rows | target | 200/200 | 3/3 | 89.5828 | 46.4443 | 48.154891% | 1043/24 | 97.698945% | 14/12 | passed | not_required |
| small | 2 | graph.evidence | target | 200/200 | 3/3 | 88.2077 | 46.9946 | 46.722792% | 1043/24 | 97.698945% | 14/12 | passed | not_required |
| medium | 1 | question_bank.questions.default | control | 200/200 | 20/20 | 24.0211 | 17.2627 | 28.135264% | 11/11 | 0% | 6/6 | not_required | not_required |
| medium | 1 | training.diagnosis | target | 200/200 | 20/20 | 210.5064 | 103.2232 | 50.964341% | 1043/24 | 97.698945% | 14/12 | passed | not_required |
| medium | 1 | training.plan.preview | target | 200/200 | 20/20 | 1323239.7073 | 959.4182 | 99.927495% | 41663/164 | 99.606365% | 314/152 | passed | passed |
| medium | 1 | graph.profiles | target | 200/200 | 20/20 | 203.2853 | 116.0825 | 42.896756% | 1043/24 | 97.698945% | 14/12 | passed | passed |
| medium | 1 | graph.rows | target | 200/200 | 40/40 | 263.9323 | 110.7921 | 58.022531% | 1043/24 | 97.698945% | 14/12 | passed | passed |
| medium | 1 | graph.evidence | target | 200/200 | 20/20 | 269.9335 | 103.4438 | 61.678043% | 1043/24 | 97.698945% | 14/12 | passed | passed |
| medium | 2 | question_bank.questions.default | control | 200/200 | 20/20 | 26.2404 | 16.4173 | 37.435024% | 11/11 | 0% | 6/6 | not_required | not_required |
| medium | 2 | training.diagnosis | target | 200/200 | 20/20 | 265.126 | 101.3542 | 61.771309% | 1043/24 | 97.698945% | 14/12 | passed | not_required |
| medium | 2 | training.plan.preview | target | 200/200 | 20/20 | 2779.3096 | 878.7385 | 68.382849% | 41663/164 | 99.606365% | 314/152 | passed | passed |
| medium | 2 | graph.profiles | target | 200/200 | 20/20 | 122.5878 | 89.3832 | 27.086382% | 1043/24 | 97.698945% | 14/12 | passed | passed |
| medium | 2 | graph.rows | target | 200/200 | 40/40 | 137.5047 | 97.0612 | 29.412449% | 1043/24 | 97.698945% | 14/12 | passed | passed |
| medium | 2 | graph.evidence | target | 200/200 | 20/20 | 133.4016 | 101.7445 | 23.730675% | 1043/24 | 97.698945% | 14/12 | passed | passed |
| large_5pct | 1 | question_bank.questions.default | control | 200/200 | 20/20 | 14.8539 | 14.237 | 4.153118% | 11/11 | 0% | 6/6 | not_required | not_required |
| large_5pct | 1 | training.diagnosis | target | 200/200 | 2/2 | 87.8612 | 60.4348 | 31.215599% | 1043/24 | 97.698945% | 14/12 | passed | not_required |
| large_5pct | 1 | training.plan.preview | target | 200/200 | 2/2 | 276.7228 | 79.6108 | 71.230849% | 5105/38 | 99.255632% | 44/26 | passed | not_required |
| large_5pct | 1 | graph.profiles | target | 200/200 | 2/2 | 83.879 | 61.7205 | 26.41722% | 1043/24 | 97.698945% | 14/12 | passed | not_required |
| large_5pct | 1 | graph.rows | target | 200/200 | 2/2 | 83.8103 | 54.4762 | 35.000591% | 1043/24 | 97.698945% | 14/12 | passed | not_required |
| large_5pct | 1 | graph.evidence | target | 200/200 | 2/2 | 84.6646 | 56.2805 | 33.525346% | 1043/24 | 97.698945% | 14/12 | passed | not_required |
| large_5pct | 2 | question_bank.questions.default | control | 200/200 | 20/20 | 14.9853 | 15.3566 | -2.477762% | 11/11 | 0% | 6/6 | not_required | not_required |
| large_5pct | 2 | training.diagnosis | target | 200/200 | 2/2 | 80.0591 | 58.7332 | 26.637696% | 1043/24 | 97.698945% | 14/12 | passed | not_required |
| large_5pct | 2 | training.plan.preview | target | 200/200 | 2/2 | 271.1389 | 80.267 | 70.396354% | 5105/38 | 99.255632% | 44/26 | passed | not_required |
| large_5pct | 2 | graph.profiles | target | 200/200 | 2/2 | 89.8888 | 56.3555 | 37.305315% | 1043/24 | 97.698945% | 14/12 | passed | not_required |
| large_5pct | 2 | graph.rows | target | 200/200 | 2/2 | 90.4411 | 54.9577 | 39.233711% | 1043/24 | 97.698945% | 14/12 | passed | not_required |
| large_5pct | 2 | graph.evidence | target | 200/200 | 2/2 | 86.4214 | 54.053 | 37.454149% | 1043/24 | 97.698945% | 14/12 | passed | not_required |

## Gates

| gate | required comparisons | passed comparisons | result |
|---|---:|---:|---|
| status equality | 36 | 36 | passed |
| response record equality | 36 | 36 | passed |
| non-zero response records | 36 | 36 | passed |
| target statement reduction | 30 | 30 | passed |
| medium p50 improvement | 8 | 8 | passed |
| legacy two-round repeatability | 3 | 3 | passed |
| request-scoped two-round repeatability | 3 | 3 | passed |

## limitations

- Only aggregate measurements from generated benchmark data are included.
- Before and after measurements use identical generated datasets.
- The committed P1-26 report is provenance only, not numeric performance evidence.
- The reduced workload has lower statistical confidence and capacity coverage.
- Latency is machine-specific and is not a service-level objective.
- Each file is atomic, but sudden termination can leave a mixed old/new pair.
- With only two measured samples per repetition, p50 is sensitive to timing variance and has reduced statistical confidence.
- The first medium legacy plan-preview repetition recorded a p50 of 1323239.7073 ms (about 22.05 minutes), while repetition two recorded 2779.3096 ms; this observed timing outlier shows that the report is not capacity or service-level evidence.
