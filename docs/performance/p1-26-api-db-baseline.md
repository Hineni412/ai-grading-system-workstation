# P1-26 API/DB performance baseline

- code SHA: `6bb53c338d23e530ed2890afbdecce0ae6ac9fb9`
- generated at UTC: `2026-07-13T23:23:17.453962Z`
- seed: `126`
- repeatability: `passed`

## Runtime

| Windows version | Python version | SQLite version | logical CPU |
|---|---|---|---:|
| Windows-11-10.0.26200 | 3.12.1 | 3.43.1 | 16 |

## Manifests

| scale | logical counts | grading bytes | question-bank bytes | backup bytes | generated asset bytes |
|---|---|---:|---:|---:|---:|
| small | students=30, grading_sessions=1, exam_papers=30, session_results=30, session_details=300, papers=2, questions=200, question_tags=1000, question_previews=2, grading_question_links=10, training_tasks=10, training_variants=10, variant_students=10, training_task_items=10, backup_files=5 | 176128 | 593920 | 40960 | 136 |
| medium | students=200, grading_sessions=5, exam_papers=1000, session_results=1000, session_details=20000, papers=20, questions=2000, question_tags=10000, question_previews=2, grading_question_links=100, training_tasks=100, training_variants=100, variant_students=100, training_task_items=100, backup_files=50 | 2990080 | 2527232 | 409600 | 136 |
| large_5pct | students=25, grading_sessions=1, exam_papers=25, session_results=25, session_details=7500, papers=5, questions=500, question_tags=2500, question_previews=2, grading_question_links=2, training_tasks=25, training_variants=25, variant_students=25, training_task_items=25, backup_files=5 | 1105920 | 901120 | 40960 | 136 |

## Aggregate measurements

| scale | repetition | scenario | status | samples | minimum ms | p50 ms | p95 ms | maximum ms | DB statements min/median/max | DB selects min/median/max | response records min/median/max | response bytes min/median/max | scale driver |
|---|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|---|---|
| small | 1 | health | 200 | 20 | 0.5263 | 0.7041 | 0.9972 | 1.1802 | 0/0/0 | 0/0/0 | 1/1/1 | 77/77/77 | constant=1 |
| small | 1 | question_bank.papers | 200 | 20 | 12.2383 | 14.0671 | 15.2614 | 16.1464 | 7/7/7 | 2/2/2 | 2/2/2 | 859/859/859 | papers=2 |
| small | 1 | question_bank.questions.default | 200 | 20 | 16.5879 | 19.0871 | 22.7875 | 23.6408 | 11/11/11 | 6/6/6 | 20/20/20 | 23404/23404/23404 | questions=200 |
| small | 1 | question_bank.questions.filtered | 200 | 20 | 14.4155 | 15.6456 | 19.7488 | 20.2094 | 11/11/11 | 6/6/6 | 4/4/4 | 4767/4767/4767 | questions=200 |
| small | 1 | question_bank.question.detail | 200 | 20 | 12.7981 | 14.2938 | 16.0621 | 16.1175 | 11/11/11 | 6/6/6 | 1/1/1 | 1745/1745/1745 | questions=200 |
| small | 1 | question_bank.question.asset | 200 | 20 | 12.749 | 14.6343 | 16.8616 | 17.7051 | 7/7/7 | 2/2/2 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| small | 1 | question_bank.question.preview | 200 | 20 | 13.1428 | 14.4296 | 16.7151 | 17.6917 | 8/8/8 | 3/3/3 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| small | 1 | training.diagnosis | 200 | 20 | 137.6877 | 151.2445 | 250.2823 | 253.9035 | 1043/1043/1043 | 14/14/14 | 30/30/30 | 178831/178831/178831 | students=30 |
| small | 1 | training.plan.preview | 200 | 20 | 4099.8144 | 4245.3334 | 4475.6481 | 4679.7379 | 61973/61973/61973 | 464/464/464 | 30/30/30 | 423018/423018/423018 | students=30 |
| small | 1 | training.tasks | 200 | 20 | 16.1444 | 19.7103 | 21.941 | 24.0642 | 2/2/2 | 2/2/2 | 10/10/10 | 2334/2334/2334 | training_tasks=10 |
| small | 1 | training.task.detail | 200 | 20 | 15.0619 | 17.201 | 20.5869 | 22.5999 | 5/5/5 | 5/5/5 | 1/1/1 | 1021/1021/1021 | training_tasks=10 |
| small | 1 | graph.profiles | 200 | 20 | 129.7137 | 143.6829 | 159.5564 | 216.6455 | 1055/1055/1055 | 16/16/16 | 30/30/30 | 178763/178763/178763 | students=30 |
| small | 1 | graph.rows | 200 | 20 | 136.1281 | 145.4834 | 250.3298 | 265.8876 | 1055/1055/1055 | 16/16/16 | 300/300/300 | 183865/183865/183865 | session_details=300 |
| small | 1 | graph.evidence | 200 | 20 | 128.3127 | 136.092 | 148.7431 | 218.1011 | 1055/1055/1055 | 16/16/16 | 30/30/30 | 16812/16812/16812 | session_details=300 |
| small | 1 | ops.self_check | 200 | 20 | 69.4354 | 77.0439 | 83.1715 | 90.3561 | 12/12/12 | 2/2/2 | 1/1/1 | 1126/1126/1126 | constant=1 |
| small | 1 | ops.backups | 200 | 20 | 1.7063 | 1.9278 | 2.6486 | 2.7315 | 0/0/0 | 0/0/0 | 5/5/5 | 746/746/746 | backup_files=5 |
| small | 2 | health | 200 | 20 | 0.4878 | 0.6395 | 1.0812 | 1.4003 | 0/0/0 | 0/0/0 | 1/1/1 | 77/77/77 | constant=1 |
| small | 2 | question_bank.papers | 200 | 20 | 12.3382 | 13.9828 | 15.7256 | 16.8427 | 7/7/7 | 2/2/2 | 2/2/2 | 859/859/859 | papers=2 |
| small | 2 | question_bank.questions.default | 200 | 20 | 17.0013 | 18.463 | 21.2507 | 24.0944 | 11/11/11 | 6/6/6 | 20/20/20 | 23404/23404/23404 | questions=200 |
| small | 2 | question_bank.questions.filtered | 200 | 20 | 14.7217 | 16.9327 | 19.6148 | 20.915 | 11/11/11 | 6/6/6 | 4/4/4 | 4767/4767/4767 | questions=200 |
| small | 2 | question_bank.question.detail | 200 | 20 | 14.3568 | 15.7588 | 22.3068 | 24.2081 | 11/11/11 | 6/6/6 | 1/1/1 | 1745/1745/1745 | questions=200 |
| small | 2 | question_bank.question.asset | 200 | 20 | 14.0926 | 15.5959 | 18.0169 | 18.1776 | 7/7/7 | 2/2/2 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| small | 2 | question_bank.question.preview | 200 | 20 | 13.0026 | 14.7085 | 15.7983 | 16.0857 | 8/8/8 | 3/3/3 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| small | 2 | training.diagnosis | 200 | 20 | 139.7834 | 157.6678 | 249.2438 | 260.284 | 1043/1043/1043 | 14/14/14 | 30/30/30 | 178831/178831/178831 | students=30 |
| small | 2 | training.plan.preview | 200 | 20 | 4048.8786 | 4237.4679 | 4517.4687 | 4563.186 | 61973/61973/61973 | 464/464/464 | 30/30/30 | 423018/423018/423018 | students=30 |
| small | 2 | training.tasks | 200 | 20 | 16.3701 | 18.6567 | 21.1668 | 23.7732 | 2/2/2 | 2/2/2 | 10/10/10 | 2334/2334/2334 | training_tasks=10 |
| small | 2 | training.task.detail | 200 | 20 | 14.9131 | 17.3135 | 23.2968 | 24.2622 | 5/5/5 | 5/5/5 | 1/1/1 | 1021/1021/1021 | training_tasks=10 |
| small | 2 | graph.profiles | 200 | 20 | 122.8543 | 131.3581 | 229.2433 | 233.9039 | 1055/1055/1055 | 16/16/16 | 30/30/30 | 178763/178763/178763 | students=30 |
| small | 2 | graph.rows | 200 | 20 | 126.1042 | 138.0912 | 223.7158 | 228.7346 | 1055/1055/1055 | 16/16/16 | 300/300/300 | 183865/183865/183865 | session_details=300 |
| small | 2 | graph.evidence | 200 | 20 | 120.5894 | 131.4595 | 157.6273 | 228.8504 | 1055/1055/1055 | 16/16/16 | 30/30/30 | 16812/16812/16812 | session_details=300 |
| small | 2 | ops.self_check | 200 | 20 | 67.4272 | 75.3777 | 81.7605 | 85.8484 | 12/12/12 | 2/2/2 | 1/1/1 | 1126/1126/1126 | constant=1 |
| small | 2 | ops.backups | 200 | 20 | 1.7065 | 2.1387 | 2.8419 | 3.0437 | 0/0/0 | 0/0/0 | 5/5/5 | 746/746/746 | backup_files=5 |
| medium | 1 | health | 200 | 20 | 0.5004 | 0.5569 | 0.8256 | 0.8485 | 0/0/0 | 0/0/0 | 1/1/1 | 77/77/77 | constant=1 |
| medium | 1 | question_bank.papers | 200 | 20 | 24.9593 | 27.4043 | 31.5567 | 34.4911 | 7/7/7 | 2/2/2 | 20/20/20 | 8413/8413/8413 | papers=20 |
| medium | 1 | question_bank.questions.default | 200 | 20 | 31.5281 | 34.0294 | 37.7409 | 41.7824 | 11/11/11 | 6/6/6 | 20/20/20 | 23446/23446/23446 | questions=2000 |
| medium | 1 | question_bank.questions.filtered | 200 | 20 | 31.0585 | 34.2835 | 39.176 | 44.3862 | 11/11/11 | 6/6/6 | 40/40/40 | 46822/46822/46822 | questions=2000 |
| medium | 1 | question_bank.question.detail | 200 | 20 | 20.7196 | 23.5186 | 26.7491 | 30.5069 | 11/11/11 | 6/6/6 | 1/1/1 | 1745/1745/1745 | questions=2000 |
| medium | 1 | question_bank.question.asset | 200 | 20 | 21.8777 | 23.1999 | 25.2016 | 25.2482 | 7/7/7 | 2/2/2 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| medium | 1 | question_bank.question.preview | 200 | 20 | 21.1713 | 23.3813 | 26.6592 | 34.8464 | 8/8/8 | 3/3/3 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| medium | 1 | training.diagnosis | 200 | 20 | 3580.1759 | 3704.0619 | 3950.9139 | 3958.9905 | 5151/5151/5151 | 54/54/54 | 200/200/200 | 4818375/4818375/4818375 | students=200 |
| medium | 1 | training.plan.preview | 200 | 20 | 158847.202 | 161000.0934 | 165069.8241 | 170580.5761 | 1222551/1222551/1222551 | 7854/7854/7854 | 200/200/200 | 10235557/10235557/10235557 | students=200 |
| medium | 1 | training.tasks | 200 | 20 | 29.2721 | 31.3432 | 37.8502 | 40.481 | 2/2/2 | 2/2/2 | 100/100/100 | 22856/22856/22856 | training_tasks=100 |
| medium | 1 | training.task.detail | 200 | 20 | 15.8353 | 17.2689 | 20.0267 | 21.8905 | 5/5/5 | 5/5/5 | 1/1/1 | 1021/1021/1021 | training_tasks=100 |
| medium | 1 | graph.profiles | 200 | 20 | 1492.3733 | 1636.7198 | 1692.0101 | 1707.3374 | 5163/5163/5163 | 56/56/56 | 200/200/200 | 4818307/4818307/4818307 | students=200 |
| medium | 1 | graph.rows | 200 | 20 | 1720.4835 | 1773.3595 | 1884.5675 | 1885.7352 | 5163/5163/5163 | 56/56/56 | 4000/4000/4000 | 4874715/4874715/4874715 | session_details=20000 |
| medium | 1 | graph.evidence | 200 | 20 | 1479.5681 | 1509.3271 | 1568.6798 | 1603.9694 | 5163/5163/5163 | 56/56/56 | 100/100/100 | 55877/55877/55877 | session_details=20000 |
| medium | 1 | ops.self_check | 200 | 20 | 87.8423 | 99.6955 | 105.852 | 106.3472 | 12/12/12 | 2/2/2 | 1/1/1 | 1128/1128/1128 | constant=1 |
| medium | 1 | ops.backups | 200 | 20 | 3.1232 | 3.504 | 4.5103 | 5.015 | 0/0/0 | 0/0/0 | 50/50/50 | 7137/7137/7137 | backup_files=50 |
| medium | 2 | health | 200 | 20 | 0.4595 | 0.6084 | 0.701 | 0.7922 | 0/0/0 | 0/0/0 | 1/1/1 | 77/77/77 | constant=1 |
| medium | 2 | question_bank.papers | 200 | 20 | 26.2836 | 29.1526 | 31.7246 | 32.4 | 7/7/7 | 2/2/2 | 20/20/20 | 8413/8413/8413 | papers=20 |
| medium | 2 | question_bank.questions.default | 200 | 20 | 32.4462 | 35.4672 | 40.307 | 42.6194 | 11/11/11 | 6/6/6 | 20/20/20 | 23446/23446/23446 | questions=2000 |
| medium | 2 | question_bank.questions.filtered | 200 | 20 | 31.1068 | 35.7395 | 43.8089 | 44.8346 | 11/11/11 | 6/6/6 | 40/40/40 | 46822/46822/46822 | questions=2000 |
| medium | 2 | question_bank.question.detail | 200 | 20 | 23.3916 | 24.7975 | 28.9143 | 30.2025 | 11/11/11 | 6/6/6 | 1/1/1 | 1745/1745/1745 | questions=2000 |
| medium | 2 | question_bank.question.asset | 200 | 20 | 22.0611 | 23.9791 | 28.6162 | 28.8189 | 7/7/7 | 2/2/2 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| medium | 2 | question_bank.question.preview | 200 | 20 | 20.6345 | 24.9066 | 28.6012 | 29.6868 | 8/8/8 | 3/3/3 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| medium | 2 | training.diagnosis | 200 | 20 | 3418.6247 | 3604.2863 | 3739.1422 | 3757.8969 | 5151/5151/5151 | 54/54/54 | 200/200/200 | 4818375/4818375/4818375 | students=200 |
| medium | 2 | training.plan.preview | 200 | 20 | 156818.8214 | 157457.3122 | 158378.6584 | 159992.6424 | 1222551/1222551/1222551 | 7854/7854/7854 | 200/200/200 | 10235557/10235557/10235557 | students=200 |
| medium | 2 | training.tasks | 200 | 20 | 27.4567 | 30.6283 | 34.2429 | 35.999 | 2/2/2 | 2/2/2 | 100/100/100 | 22856/22856/22856 | training_tasks=100 |
| medium | 2 | training.task.detail | 200 | 20 | 15.3684 | 17.165 | 20.4834 | 21.0733 | 5/5/5 | 5/5/5 | 1/1/1 | 1021/1021/1021 | training_tasks=100 |
| medium | 2 | graph.profiles | 200 | 20 | 1540.8805 | 1656.0521 | 1735.7792 | 1740.838 | 5163/5163/5163 | 56/56/56 | 200/200/200 | 4818307/4818307/4818307 | students=200 |
| medium | 2 | graph.rows | 200 | 20 | 1727.9798 | 1762.0941 | 1815.6529 | 1847.1819 | 5163/5163/5163 | 56/56/56 | 4000/4000/4000 | 4874715/4874715/4874715 | session_details=20000 |
| medium | 2 | graph.evidence | 200 | 20 | 1467.2843 | 1514.6516 | 1561.7593 | 1588.9544 | 5163/5163/5163 | 56/56/56 | 100/100/100 | 55877/55877/55877 | session_details=20000 |
| medium | 2 | ops.self_check | 200 | 20 | 86.1049 | 97.4357 | 104.8634 | 105.6212 | 12/12/12 | 2/2/2 | 1/1/1 | 1128/1128/1128 | constant=1 |
| medium | 2 | ops.backups | 200 | 20 | 3.2917 | 5.2613 | 6.2728 | 6.3434 | 0/0/0 | 0/0/0 | 50/50/50 | 7137/7137/7137 | backup_files=50 |
| large_5pct | 1 | health | 200 | 20 | 0.5162 | 0.7187 | 1.0178 | 1.1722 | 0/0/0 | 0/0/0 | 1/1/1 | 77/77/77 | constant=1 |
| large_5pct | 1 | question_bank.papers | 200 | 20 | 13.1214 | 14.0353 | 15.4085 | 15.8995 | 7/7/7 | 2/2/2 | 5/5/5 | 2116/2116/2116 | papers=5 |
| large_5pct | 1 | question_bank.questions.default | 200 | 20 | 18.2188 | 20.188 | 24.3597 | 28.3403 | 11/11/11 | 6/6/6 | 20/20/20 | 23404/23404/23404 | questions=500 |
| large_5pct | 1 | question_bank.questions.filtered | 200 | 20 | 15.2124 | 16.8848 | 19.3674 | 21.0921 | 11/11/11 | 6/6/6 | 10/10/10 | 11770/11770/11770 | questions=500 |
| large_5pct | 1 | question_bank.question.detail | 200 | 20 | 13.5109 | 14.4876 | 16.1337 | 18.9834 | 11/11/11 | 6/6/6 | 1/1/1 | 1745/1745/1745 | questions=500 |
| large_5pct | 1 | question_bank.question.asset | 200 | 20 | 14.0257 | 15.4447 | 16.9376 | 19.2149 | 7/7/7 | 2/2/2 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| large_5pct | 1 | question_bank.question.preview | 200 | 20 | 13.8134 | 15.687 | 17.5551 | 17.8268 | 8/8/8 | 3/3/3 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| large_5pct | 1 | training.diagnosis | 200 | 20 | 343.4712 | 461.5635 | 503.7896 | 510.8543 | 1043/1043/1043 | 14/14/14 | 25/25/25 | 33207/33207/33207 | students=25 |
| large_5pct | 1 | training.plan.preview | 200 | 20 | 4991.5632 | 5267.4086 | 5479.4791 | 5502.2503 | 51818/51818/51818 | 389/389/389 | 25/25/25 | 108972/108972/108972 | students=25 |
| large_5pct | 1 | training.tasks | 200 | 20 | 17.9691 | 20.7444 | 22.8126 | 29.2044 | 2/2/2 | 2/2/2 | 25/25/25 | 5754/5754/5754 | training_tasks=25 |
| large_5pct | 1 | training.task.detail | 200 | 20 | 15.5686 | 17.8398 | 22.7926 | 23.2392 | 5/5/5 | 5/5/5 | 1/1/1 | 1021/1021/1021 | training_tasks=25 |
| large_5pct | 1 | graph.profiles | 200 | 20 | 369.1616 | 476.5607 | 511.0015 | 525.3541 | 1055/1055/1055 | 16/16/16 | 25/25/25 | 33139/33139/33139 | students=25 |
| large_5pct | 1 | graph.rows | 200 | 20 | 377.0843 | 467.6039 | 499.457 | 502.6406 | 1055/1055/1055 | 16/16/16 | 50/50/50 | 31309/31309/31309 | session_details=7500 |
| large_5pct | 1 | graph.evidence | 200 | 20 | 410.8562 | 472.3349 | 508.3468 | 509.2134 | 1055/1055/1055 | 16/16/16 | 25/25/25 | 14129/14129/14129 | session_details=7500 |
| large_5pct | 1 | ops.self_check | 200 | 20 | 73.071 | 79.1706 | 85.8631 | 90.2725 | 12/12/12 | 2/2/2 | 1/1/1 | 1127/1127/1127 | constant=1 |
| large_5pct | 1 | ops.backups | 200 | 20 | 2.1632 | 2.6421 | 3.3132 | 3.5114 | 0/0/0 | 0/0/0 | 5/5/5 | 746/746/746 | backup_files=5 |
| large_5pct | 2 | health | 200 | 20 | 0.578 | 0.7004 | 0.8642 | 0.876 | 0/0/0 | 0/0/0 | 1/1/1 | 77/77/77 | constant=1 |
| large_5pct | 2 | question_bank.papers | 200 | 20 | 14.899 | 18.2612 | 19.537 | 21.0109 | 7/7/7 | 2/2/2 | 5/5/5 | 2116/2116/2116 | papers=5 |
| large_5pct | 2 | question_bank.questions.default | 200 | 20 | 18.2881 | 20.3352 | 24.5505 | 24.8016 | 11/11/11 | 6/6/6 | 20/20/20 | 23404/23404/23404 | questions=500 |
| large_5pct | 2 | question_bank.questions.filtered | 200 | 20 | 15.7127 | 17.391 | 21.4393 | 22.7081 | 11/11/11 | 6/6/6 | 10/10/10 | 11770/11770/11770 | questions=500 |
| large_5pct | 2 | question_bank.question.detail | 200 | 20 | 14.1754 | 15.5437 | 18.2506 | 19.9507 | 11/11/11 | 6/6/6 | 1/1/1 | 1745/1745/1745 | questions=500 |
| large_5pct | 2 | question_bank.question.asset | 200 | 20 | 14.8844 | 17.2726 | 18.9251 | 19.3934 | 7/7/7 | 2/2/2 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| large_5pct | 2 | question_bank.question.preview | 200 | 20 | 15.0205 | 15.9286 | 18.4779 | 18.7865 | 8/8/8 | 3/3/3 | 1/1/1 | 68/68/68 | generated_asset_bytes=136 |
| large_5pct | 2 | training.diagnosis | 200 | 20 | 291.9502 | 437.4473 | 475.3975 | 487.0539 | 1043/1043/1043 | 14/14/14 | 25/25/25 | 33207/33207/33207 | students=25 |
| large_5pct | 2 | training.plan.preview | 200 | 20 | 5186.8667 | 5266.5768 | 5370.5173 | 5418.8005 | 51818/51818/51818 | 389/389/389 | 25/25/25 | 108972/108972/108972 | students=25 |
| large_5pct | 2 | training.tasks | 200 | 20 | 16.1422 | 20.2973 | 24.8579 | 28.2231 | 2/2/2 | 2/2/2 | 25/25/25 | 5754/5754/5754 | training_tasks=25 |
| large_5pct | 2 | training.task.detail | 200 | 20 | 16.8354 | 18.5667 | 22.7243 | 25.104 | 5/5/5 | 5/5/5 | 1/1/1 | 1021/1021/1021 | training_tasks=25 |
| large_5pct | 2 | graph.profiles | 200 | 20 | 379.2532 | 477.7603 | 505.6572 | 517.891 | 1055/1055/1055 | 16/16/16 | 25/25/25 | 33139/33139/33139 | students=25 |
| large_5pct | 2 | graph.rows | 200 | 20 | 379.8767 | 463.9352 | 516.9202 | 517.2038 | 1055/1055/1055 | 16/16/16 | 50/50/50 | 31309/31309/31309 | session_details=7500 |
| large_5pct | 2 | graph.evidence | 200 | 20 | 365.2458 | 473.5012 | 517.5918 | 521.5261 | 1055/1055/1055 | 16/16/16 | 25/25/25 | 14129/14129/14129 | session_details=7500 |
| large_5pct | 2 | ops.self_check | 200 | 20 | 74.9625 | 79.3496 | 86.9875 | 88.8291 | 12/12/12 | 2/2/2 | 1/1/1 | 1127/1127/1127 | constant=1 |
| large_5pct | 2 | ops.backups | 200 | 20 | 1.7295 | 2.224 | 2.5867 | 2.6135 | 0/0/0 | 0/0/0 | 5/5/5 | 746/746/746 | backup_files=5 |

## Observations

No possible_n_plus_one candidates met the fixed threshold.

## limitations

- Only allowlisted SQLite connection boundaries are counted.
- Latency is machine-specific and is not a service-level objective.
- The benchmark uses generated data and performs no optimization.
