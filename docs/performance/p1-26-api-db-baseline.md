# P1-26 API/DB performance baseline

- code SHA: `a7e6de8476ece27ed87a67072756ba87ec5eb0dd`
- generated at UTC: `2026-07-13T20:17:51.753513Z`
- seed: `126`
- repeatability: `passed`

## Runtime

| Windows version | Python version | SQLite version | logical CPU |
|---|---|---|---:|
| Windows-11-10.0.26200 | 3.12.1 | 3.43.1 | 16 |

## Manifests

| scale | logical counts | grading bytes | question-bank bytes | backup bytes | generated asset bytes |
|---|---|---:|---:|---:|---:|
| small | students=30, grading_sessions=1, exam_papers=30, session_results=30, session_details=300, questions=200, question_tags=400, question_previews=2, grading_question_links=10, training_tasks=10, training_variants=10, variant_students=10, training_task_items=10, backup_files=5 | 176128 | 495616 | 40960 | 136 |
| medium | students=200, grading_sessions=5, exam_papers=1000, session_results=1000, session_details=20000, questions=2000, question_tags=4000, question_previews=2, grading_question_links=100, training_tasks=100, training_variants=100, variant_students=100, training_task_items=100, backup_files=50 | 2990080 | 1576960 | 409600 | 136 |
| large_5pct | students=25, grading_sessions=1, exam_papers=25, session_results=25, session_details=7500, questions=500, question_tags=1000, question_previews=2, grading_question_links=2, training_tasks=25, training_variants=25, variant_students=25, training_task_items=25, backup_files=5 | 1105920 | 667648 | 40960 | 136 |

## Aggregate measurements

| scale | repetition | scenario | status | samples | minimum ms | p50 ms | p95 ms | maximum ms | DB statements min/median/max | DB selects min/median/max | response records min/median/max | response bytes min/median/max | scale driver |
|---|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|---|---|
| small | 1 | health | 200 | 20 | 0.4637 | 0.556 | 0.7755 | 0.782 | 0/0/0 | 0/0/0 | 1/1/1 | 77/77/77 | constant=1 |
| small | 1 | question_bank.papers | 200 | 20 | 10.7076 | 11.3291 | 14.2617 | 14.3969 | 7/7/7 | 2/2/2 | 0/0/0 | 22/22/22 | questions=200 |
| small | 1 | question_bank.questions.default | 200 | 20 | 13.6672 | 16.0523 | 17.3282 | 17.7404 | 11/11/11 | 6/6/6 | 20/20/20 | 15904/15904/15904 | questions=200 |
| small | 1 | question_bank.questions.filtered | 200 | 20 | 12.7834 | 14.8067 | 16.8281 | 18.0681 | 8/8/8 | 3/3/3 | 0/0/0 | 63/63/63 | questions=200 |
| small | 1 | question_bank.question.detail | 200 | 20 | 11.987 | 14.4682 | 16.0829 | 19.7696 | 11/11/11 | 6/6/6 | 1/1/1 | 1370/1370/1370 | questions=200 |
| small | 1 | question_bank.question.asset | 200 | 20 | 12.899 | 14.0985 | 17.196 | 17.4903 | 7/7/7 | 2/2/2 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| small | 1 | question_bank.question.preview | 200 | 20 | 14.1171 | 16.0191 | 17.4906 | 18.2951 | 8/8/8 | 3/3/3 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| small | 1 | training.diagnosis | 200 | 20 | 138.6756 | 155.1004 | 249.5062 | 275.6352 | 1043/1043/1043 | 14/14/14 | 30/30/30 | 168331/168331/168331 | students=30 |
| small | 1 | training.plan.preview | 200 | 20 | 3898.5682 | 4083.5695 | 4282.2356 | 4341.8964 | 61973/61973/61973 | 464/464/464 | 30/30/30 | 402018/402018/402018 | students=30 |
| small | 1 | training.tasks | 200 | 20 | 15.7506 | 18.8556 | 24.685 | 26.4672 | 2/2/2 | 2/2/2 | 10/10/10 | 2334/2334/2334 | training_tasks=10 |
| small | 1 | training.task.detail | 200 | 20 | 15.8163 | 17.3282 | 19.8122 | 20.9538 | 5/5/5 | 5/5/5 | 1/1/1 | 1021/1021/1021 | training_tasks=10 |
| small | 1 | graph.profiles | 200 | 20 | 125.319 | 133.3089 | 147.9647 | 227.1377 | 1055/1055/1055 | 16/16/16 | 30/30/30 | 168263/168263/168263 | students=30 |
| small | 1 | graph.rows | 200 | 20 | 127.7292 | 143.8234 | 164.3444 | 240.5409 | 1055/1055/1055 | 16/16/16 | 300/300/300 | 173015/173015/173015 | session_details=300 |
| small | 1 | graph.evidence | 200 | 20 | 119.8237 | 133.6167 | 151.0056 | 238.3642 | 1055/1055/1055 | 16/16/16 | 30/30/30 | 15762/15762/15762 | session_details=300 |
| small | 1 | ops.self_check | 200 | 20 | 68.4662 | 76.217 | 85.4089 | 86.1655 | 12/12/12 | 2/2/2 | 1/1/1 | 1126/1126/1126 | constant=1 |
| small | 1 | ops.backups | 200 | 20 | 1.814 | 2.19 | 2.526 | 2.9432 | 0/0/0 | 0/0/0 | 5/5/5 | 746/746/746 | backup_files=5 |
| small | 2 | health | 200 | 20 | 0.5067 | 0.6156 | 0.8788 | 1.1413 | 0/0/0 | 0/0/0 | 1/1/1 | 77/77/77 | constant=1 |
| small | 2 | question_bank.papers | 200 | 20 | 12.2597 | 13.4574 | 14.8751 | 16.422 | 7/7/7 | 2/2/2 | 0/0/0 | 22/22/22 | questions=200 |
| small | 2 | question_bank.questions.default | 200 | 20 | 13.8662 | 16.2124 | 18.4255 | 18.625 | 11/11/11 | 6/6/6 | 20/20/20 | 15904/15904/15904 | questions=200 |
| small | 2 | question_bank.questions.filtered | 200 | 20 | 13.3417 | 14.3779 | 16.8499 | 17.4394 | 8/8/8 | 3/3/3 | 0/0/0 | 63/63/63 | questions=200 |
| small | 2 | question_bank.question.detail | 200 | 20 | 12.8779 | 14.0182 | 17.8417 | 19.1919 | 11/11/11 | 6/6/6 | 1/1/1 | 1370/1370/1370 | questions=200 |
| small | 2 | question_bank.question.asset | 200 | 20 | 13.1649 | 15.0387 | 17.8382 | 18.9691 | 7/7/7 | 2/2/2 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| small | 2 | question_bank.question.preview | 200 | 20 | 13.4771 | 15.2313 | 18.6461 | 20.6649 | 8/8/8 | 3/3/3 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| small | 2 | training.diagnosis | 200 | 20 | 135.8187 | 151.4476 | 183.7232 | 234.5034 | 1043/1043/1043 | 14/14/14 | 30/30/30 | 168331/168331/168331 | students=30 |
| small | 2 | training.plan.preview | 200 | 20 | 3869.2094 | 3974.7587 | 4036.4833 | 4160.5904 | 61973/61973/61973 | 464/464/464 | 30/30/30 | 402018/402018/402018 | students=30 |
| small | 2 | training.tasks | 200 | 20 | 16.1323 | 19.0265 | 24.9839 | 25.2561 | 2/2/2 | 2/2/2 | 10/10/10 | 2334/2334/2334 | training_tasks=10 |
| small | 2 | training.task.detail | 200 | 20 | 15.4929 | 17.2366 | 22.3151 | 25.841 | 5/5/5 | 5/5/5 | 1/1/1 | 1021/1021/1021 | training_tasks=10 |
| small | 2 | graph.profiles | 200 | 20 | 126.6645 | 134.5996 | 156.6816 | 222.8215 | 1055/1055/1055 | 16/16/16 | 30/30/30 | 168263/168263/168263 | students=30 |
| small | 2 | graph.rows | 200 | 20 | 124.5693 | 139.9208 | 153.7774 | 232.5457 | 1055/1055/1055 | 16/16/16 | 300/300/300 | 173015/173015/173015 | session_details=300 |
| small | 2 | graph.evidence | 200 | 20 | 121.0528 | 130.9197 | 144.5518 | 148.5978 | 1055/1055/1055 | 16/16/16 | 30/30/30 | 15762/15762/15762 | session_details=300 |
| small | 2 | ops.self_check | 200 | 20 | 68.2559 | 75.1658 | 81.3403 | 85.9015 | 12/12/12 | 2/2/2 | 1/1/1 | 1126/1126/1126 | constant=1 |
| small | 2 | ops.backups | 200 | 20 | 1.7235 | 2.085 | 2.702 | 3.481 | 0/0/0 | 0/0/0 | 5/5/5 | 746/746/746 | backup_files=5 |
| medium | 1 | health | 200 | 20 | 0.5039 | 0.6097 | 0.8022 | 1.2263 | 0/0/0 | 0/0/0 | 1/1/1 | 77/77/77 | constant=1 |
| medium | 1 | question_bank.papers | 200 | 20 | 14.7019 | 15.6576 | 19.0794 | 19.9684 | 7/7/7 | 2/2/2 | 0/0/0 | 22/22/22 | questions=2000 |
| medium | 1 | question_bank.questions.default | 200 | 20 | 22.8086 | 26.6865 | 31.8635 | 32.0404 | 11/11/11 | 6/6/6 | 20/20/20 | 15926/15926/15926 | questions=2000 |
| medium | 1 | question_bank.questions.filtered | 200 | 20 | 16.4123 | 18.2198 | 20.4081 | 21.6408 | 8/8/8 | 3/3/3 | 0/0/0 | 63/63/63 | questions=2000 |
| medium | 1 | question_bank.question.detail | 200 | 20 | 15.8013 | 17.9299 | 20.6548 | 21.1237 | 11/11/11 | 6/6/6 | 1/1/1 | 1370/1370/1370 | questions=2000 |
| medium | 1 | question_bank.question.asset | 200 | 20 | 17.3124 | 18.7001 | 21.1231 | 22.7513 | 7/7/7 | 2/2/2 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| medium | 1 | question_bank.question.preview | 200 | 20 | 16.8863 | 18.7448 | 21.5405 | 21.8797 | 8/8/8 | 3/3/3 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| medium | 1 | training.diagnosis | 200 | 20 | 3201.9007 | 3361.6546 | 3537.9949 | 3570.7735 | 5151/5151/5151 | 54/54/54 | 200/200/200 | 4678375/4678375/4678375 | students=200 |
| medium | 1 | training.plan.preview | 200 | 20 | 139745.5201 | 140407.5264 | 141604.9062 | 142271.2511 | 1222551/1222551/1222551 | 7854/7854/7854 | 200/200/200 | 9955557/9955557/9955557 | students=200 |
| medium | 1 | training.tasks | 200 | 20 | 28.1169 | 30.2279 | 36.5882 | 37.0857 | 2/2/2 | 2/2/2 | 100/100/100 | 22856/22856/22856 | training_tasks=100 |
| medium | 1 | training.task.detail | 200 | 20 | 15.5822 | 17.1196 | 19.7529 | 23.0161 | 5/5/5 | 5/5/5 | 1/1/1 | 1021/1021/1021 | training_tasks=100 |
| medium | 1 | graph.profiles | 200 | 20 | 1437.9583 | 1569.4652 | 1665.5837 | 1677.4899 | 5163/5163/5163 | 56/56/56 | 200/200/200 | 4678307/4678307/4678307 | students=200 |
| medium | 1 | graph.rows | 200 | 20 | 1578.8833 | 1725.2232 | 1799.4149 | 1799.666 | 5163/5163/5163 | 56/56/56 | 4000/4000/4000 | 4734015/4734015/4734015 | session_details=20000 |
| medium | 1 | graph.evidence | 200 | 20 | 1287.7992 | 1404.838 | 1506.2524 | 1571.6867 | 5163/5163/5163 | 56/56/56 | 100/100/100 | 52377/52377/52377 | session_details=20000 |
| medium | 1 | ops.self_check | 200 | 20 | 82.198 | 90.5571 | 98.9968 | 100.159 | 12/12/12 | 2/2/2 | 1/1/1 | 1128/1128/1128 | constant=1 |
| medium | 1 | ops.backups | 200 | 20 | 3.2363 | 4.0325 | 4.6742 | 5.2056 | 0/0/0 | 0/0/0 | 50/50/50 | 7137/7137/7137 | backup_files=50 |
| medium | 2 | health | 200 | 20 | 0.4515 | 0.5538 | 0.7517 | 0.9449 | 0/0/0 | 0/0/0 | 1/1/1 | 77/77/77 | constant=1 |
| medium | 2 | question_bank.papers | 200 | 20 | 15.7633 | 17.4803 | 19.8776 | 21.52 | 7/7/7 | 2/2/2 | 0/0/0 | 22/22/22 | questions=2000 |
| medium | 2 | question_bank.questions.default | 200 | 20 | 23.404 | 25.1787 | 30.4368 | 31.3481 | 11/11/11 | 6/6/6 | 20/20/20 | 15926/15926/15926 | questions=2000 |
| medium | 2 | question_bank.questions.filtered | 200 | 20 | 17.5031 | 19.0141 | 22.892 | 23.4263 | 8/8/8 | 3/3/3 | 0/0/0 | 63/63/63 | questions=2000 |
| medium | 2 | question_bank.question.detail | 200 | 20 | 17.2166 | 19.4876 | 22.2735 | 22.7902 | 11/11/11 | 6/6/6 | 1/1/1 | 1370/1370/1370 | questions=2000 |
| medium | 2 | question_bank.question.asset | 200 | 20 | 17.4176 | 19.8807 | 22.3282 | 30.5849 | 7/7/7 | 2/2/2 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| medium | 2 | question_bank.question.preview | 200 | 20 | 17.6081 | 19.6104 | 21.1601 | 21.3234 | 8/8/8 | 3/3/3 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| medium | 2 | training.diagnosis | 200 | 20 | 3263.8131 | 3371.6874 | 3545.0518 | 3567.2574 | 5151/5151/5151 | 54/54/54 | 200/200/200 | 4678375/4678375/4678375 | students=200 |
| medium | 2 | training.plan.preview | 200 | 20 | 139567.0275 | 140313.0435 | 145406.2769 | 146123.1824 | 1222551/1222551/1222551 | 7854/7854/7854 | 200/200/200 | 9955557/9955557/9955557 | students=200 |
| medium | 2 | training.tasks | 200 | 20 | 28.0377 | 33.3943 | 40.2067 | 41.838 | 2/2/2 | 2/2/2 | 100/100/100 | 22856/22856/22856 | training_tasks=100 |
| medium | 2 | training.task.detail | 200 | 20 | 18.267 | 20.9953 | 24.0894 | 24.1415 | 5/5/5 | 5/5/5 | 1/1/1 | 1021/1021/1021 | training_tasks=100 |
| medium | 2 | graph.profiles | 200 | 20 | 1461.8061 | 1579.805 | 1673.8226 | 1681.6023 | 5163/5163/5163 | 56/56/56 | 200/200/200 | 4678307/4678307/4678307 | students=200 |
| medium | 2 | graph.rows | 200 | 20 | 1640.2841 | 1750.795 | 1850.0917 | 1891.2526 | 5163/5163/5163 | 56/56/56 | 4000/4000/4000 | 4734015/4734015/4734015 | session_details=20000 |
| medium | 2 | graph.evidence | 200 | 20 | 1316.9963 | 1442.2328 | 1533.6546 | 1557.3963 | 5163/5163/5163 | 56/56/56 | 100/100/100 | 52377/52377/52377 | session_details=20000 |
| medium | 2 | ops.self_check | 200 | 20 | 85.1005 | 91.5223 | 101.4696 | 105.6754 | 12/12/12 | 2/2/2 | 1/1/1 | 1128/1128/1128 | constant=1 |
| medium | 2 | ops.backups | 200 | 20 | 3.2888 | 4.2494 | 5.9618 | 6.3102 | 0/0/0 | 0/0/0 | 50/50/50 | 7137/7137/7137 | backup_files=50 |
| large_5pct | 1 | health | 200 | 20 | 0.4638 | 0.6109 | 0.8283 | 0.8756 | 0/0/0 | 0/0/0 | 1/1/1 | 77/77/77 | constant=1 |
| large_5pct | 1 | question_bank.papers | 200 | 20 | 10.8044 | 12.2733 | 14.6025 | 17.262 | 7/7/7 | 2/2/2 | 0/0/0 | 22/22/22 | questions=500 |
| large_5pct | 1 | question_bank.questions.default | 200 | 20 | 14.4733 | 16.1681 | 19.9534 | 21.0385 | 11/11/11 | 6/6/6 | 20/20/20 | 15904/15904/15904 | questions=500 |
| large_5pct | 1 | question_bank.questions.filtered | 200 | 20 | 12.5221 | 14.3012 | 16.6047 | 18.5249 | 8/8/8 | 3/3/3 | 0/0/0 | 63/63/63 | questions=500 |
| large_5pct | 1 | question_bank.question.detail | 200 | 20 | 12.6773 | 14.7351 | 16.9397 | 17.4615 | 11/11/11 | 6/6/6 | 1/1/1 | 1370/1370/1370 | questions=500 |
| large_5pct | 1 | question_bank.question.asset | 200 | 20 | 14.0415 | 16.3448 | 17.118 | 18.0573 | 7/7/7 | 2/2/2 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| large_5pct | 1 | question_bank.question.preview | 200 | 20 | 13.6362 | 14.814 | 16.4669 | 17.354 | 8/8/8 | 3/3/3 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| large_5pct | 1 | training.diagnosis | 200 | 20 | 324.6107 | 406.0369 | 461.2191 | 465.6505 | 1043/1043/1043 | 14/14/14 | 25/25/25 | 31457/31457/31457 | students=25 |
| large_5pct | 1 | training.plan.preview | 200 | 20 | 4648.9395 | 4869.8953 | 5004.5411 | 5024.4949 | 51818/51818/51818 | 389/389/389 | 25/25/25 | 105472/105472/105472 | students=25 |
| large_5pct | 1 | training.tasks | 200 | 20 | 18.321 | 22.6597 | 26.1577 | 26.3038 | 2/2/2 | 2/2/2 | 25/25/25 | 5754/5754/5754 | training_tasks=25 |
| large_5pct | 1 | training.task.detail | 200 | 20 | 15.4135 | 17.6127 | 22.6754 | 23.4826 | 5/5/5 | 5/5/5 | 1/1/1 | 1021/1021/1021 | training_tasks=25 |
| large_5pct | 1 | graph.profiles | 200 | 20 | 342.2334 | 451.3719 | 499.2802 | 503.9629 | 1055/1055/1055 | 16/16/16 | 25/25/25 | 31389/31389/31389 | students=25 |
| large_5pct | 1 | graph.rows | 200 | 20 | 355.8004 | 445.2324 | 518.3439 | 524.4294 | 1055/1055/1055 | 16/16/16 | 50/50/50 | 29489/29489/29489 | session_details=7500 |
| large_5pct | 1 | graph.evidence | 200 | 20 | 360.3091 | 471.1576 | 525.0555 | 541.1659 | 1055/1055/1055 | 16/16/16 | 25/25/25 | 13254/13254/13254 | session_details=7500 |
| large_5pct | 1 | ops.self_check | 200 | 20 | 71.7967 | 78.6011 | 87.4857 | 89.4365 | 12/12/12 | 2/2/2 | 1/1/1 | 1127/1127/1127 | constant=1 |
| large_5pct | 1 | ops.backups | 200 | 20 | 1.648 | 2.1237 | 2.3893 | 2.41 | 0/0/0 | 0/0/0 | 5/5/5 | 746/746/746 | backup_files=5 |
| large_5pct | 2 | health | 200 | 20 | 0.4944 | 0.6849 | 0.8364 | 0.8755 | 0/0/0 | 0/0/0 | 1/1/1 | 77/77/77 | constant=1 |
| large_5pct | 2 | question_bank.papers | 200 | 20 | 11.8142 | 12.8229 | 17.2868 | 17.8624 | 7/7/7 | 2/2/2 | 0/0/0 | 22/22/22 | questions=500 |
| large_5pct | 2 | question_bank.questions.default | 200 | 20 | 16.286 | 17.4191 | 20.9345 | 22.0809 | 11/11/11 | 6/6/6 | 20/20/20 | 15904/15904/15904 | questions=500 |
| large_5pct | 2 | question_bank.questions.filtered | 200 | 20 | 12.8494 | 14.8225 | 16.9954 | 17.1226 | 8/8/8 | 3/3/3 | 0/0/0 | 63/63/63 | questions=500 |
| large_5pct | 2 | question_bank.question.detail | 200 | 20 | 13.6035 | 14.6653 | 18.1845 | 20.3233 | 11/11/11 | 6/6/6 | 1/1/1 | 1370/1370/1370 | questions=500 |
| large_5pct | 2 | question_bank.question.asset | 200 | 20 | 14.2875 | 16.144 | 19.418 | 20.0787 | 7/7/7 | 2/2/2 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| large_5pct | 2 | question_bank.question.preview | 200 | 20 | 14.8274 | 16.6421 | 21.0615 | 21.1215 | 8/8/8 | 3/3/3 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| large_5pct | 2 | training.diagnosis | 200 | 20 | 306.4326 | 417.9329 | 462.4106 | 537.8955 | 1043/1043/1043 | 14/14/14 | 25/25/25 | 31457/31457/31457 | students=25 |
| large_5pct | 2 | training.plan.preview | 200 | 20 | 4728.9721 | 4907.4155 | 5043.8592 | 5129.3904 | 51818/51818/51818 | 389/389/389 | 25/25/25 | 105472/105472/105472 | students=25 |
| large_5pct | 2 | training.tasks | 200 | 20 | 18.3418 | 21.884 | 23.2234 | 24.5344 | 2/2/2 | 2/2/2 | 25/25/25 | 5754/5754/5754 | training_tasks=25 |
| large_5pct | 2 | training.task.detail | 200 | 20 | 15.214 | 17.2821 | 22.9383 | 25.4415 | 5/5/5 | 5/5/5 | 1/1/1 | 1021/1021/1021 | training_tasks=25 |
| large_5pct | 2 | graph.profiles | 200 | 20 | 343.9875 | 458.5596 | 501.1227 | 529.3657 | 1055/1055/1055 | 16/16/16 | 25/25/25 | 31389/31389/31389 | students=25 |
| large_5pct | 2 | graph.rows | 200 | 20 | 373.9358 | 465.2228 | 519.4844 | 522.6939 | 1055/1055/1055 | 16/16/16 | 50/50/50 | 29489/29489/29489 | session_details=7500 |
| large_5pct | 2 | graph.evidence | 200 | 20 | 334.8334 | 462.0799 | 501.5476 | 564.9485 | 1055/1055/1055 | 16/16/16 | 25/25/25 | 13254/13254/13254 | session_details=7500 |
| large_5pct | 2 | ops.self_check | 200 | 20 | 72.5437 | 79.9439 | 88.6965 | 92.9504 | 12/12/12 | 2/2/2 | 1/1/1 | 1127/1127/1127 | constant=1 |
| large_5pct | 2 | ops.backups | 200 | 20 | 1.5803 | 2.0219 | 2.8661 | 3.283 | 0/0/0 | 0/0/0 | 5/5/5 | 746/746/746 | backup_files=5 |

## Observations

No possible_n_plus_one candidates met the fixed threshold.

## limitations

- Only allowlisted SQLite connection boundaries are counted.
- Latency is machine-specific and is not a service-level objective.
- The benchmark uses generated data and performs no optimization.
