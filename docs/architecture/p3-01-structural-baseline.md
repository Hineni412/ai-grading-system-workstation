# P3-01 结构基线

- 源码提交：`5e03980e0c94a74c858f827caff698cb0c29346b`
- 扫描源码文件：289
- 静态导入：2787
- 动态导入线索：4

## 后续拆分对象

| 文件 | 公开类 | 公开函数 | 公开方法数 | 静态导入数 | 直接调用方 | 直接测试文件 |
|---|---:|---:|---:|---:|---:|---:|
| `db_manager.py` | 7 | 3 | 81 | 18 | 55 | 54 |
| `session_manager.py` | 0 | 27 | 0 | 34 | 12 | 6 |
| `grading_service.py` | 1 | 1 | 1 | 35 | 2 | 10 |
| `manual_review_service.py` | 1 | 0 | 4 | 14 | 2 | 3 |
| `web_app.py` | 0 | 14 | 0 | 85 | 1 | 7 |
| `backend/api/app.py` | 4 | 1 | 0 | 19 | 28 | 35 |
| `question_bank/database/schema.py` | 0 | 2 | 0 | 8 | 33 | 41 |

## Schema 与性能证据

- 阅卷库迁移文件：4
- 题库迁移文件：9
- P1-26：`docs/performance/p1-26-api-db-baseline.json`，96 个测量场景。
- P1-27：`docs/performance/p1-27-request-connection-comparison.json`，P1-26 来源提交为 `6bb53c338d23e530ed2890afbdecce0ae6ac9fb9`。

性能数据来自生成数据，机器相关，不能当作服务等级承诺；本包没有重跑性能测量。

## 真实流程证据入口

- P1-29：`docs/user-testing/checkpoints/P1-29-v1.5.0-phase1-formal.md`
- P2-20：`docs/user-testing/checkpoints/P2-20-v1.5.0-new-ui-five-flow-formal.md`

本报告只记录版本库内的结构与证据索引；不包含业务正文、绝对路径或运行数据。
