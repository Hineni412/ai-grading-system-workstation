# 当前测试与人工验收

自动测试使用合成数据和模型替身，不调用真实模型，不产生真实模型费用。测试保护分数与排名、保存与重新进入、恢复与回退、教师最终分锁、模型调用次数与费用以及数据边界。

## 选择验证范围

- 文档和设置只检查内容、格式与引用，入口为 `tools/check_documentation.py`。
- 文案与布局检查相关页面和受影响操作，不新增自动测试。
- 功能修复先重现原操作，再运行覆盖实际故障的现有用例。新增条件见 `AGENTS.md` 收尾一节。
- 评分、推荐和保存使用独立预期结果；保存后检查重新进入。
- 性能、缓存和内部重构使用相同输入比较输出，测量写在回复和提交说明里，不固化成新测试。
- 通过相关检查后，只有新修改、失败或具体未解决问题才扩大范围。下面的入口按需要选择，不是逐级关卡。

模型替身不代表真实模型的判断质量。合成回归不代替真实旧机升级、完整安装包验收和纸面排版核对。数据库生成定义用 `tools/generate_schema_baseline.py --check` 核对。正式服务和真实数据操作按 `AGENTS.md` 授权。

## 测试数量约束

项目根目录的 `tools/run_test_suite.py --inventory` 检查所有现存测试文件，包含未跟踪的新文件。统计范围为 `tests/`、`frontend/src/`、`frontend/e2e/`、`frontend/demo/` 和 `components/`。临时副本、依赖和运行产物不计入源码测试。

统一入口限制测试文件不超过 24 个，测试函数不超过 140 个；`--inventory` 还收集全部后端测试，限制参数化展开后不超过 132 个用例。CI 执行完整数量检查，统一测试入口在运行前检查文件与函数数量；pytest 收集时也检查展开用例数量。`quick`、`full` 等分组不改变统计范围。

默认新增文件数为零。按业务流程复用现有文件，不按生产模块、页面或修复记录拆文件。需要补充检查时，先扩展已有输入或断言，再考虑同文件中的独立测试函数。提高总量上限需要用户明确授权，代理不能自行提高。

## 测试写什么

- 比较完整输出、保存后的数据或页面状态。预期值独立于被测实现。
- 同一行为只在一处测。上层完整流程已覆盖时，删除底层重复测试及专用准备代码。
- 参数只保留不同业务分支。独立故障保持独立用例，不塞进一条大循环。
- 缓存比较更新后的结果与重新计算的结果，不锁定内部键、命中次数或耗时。
- 调用次数仅在影响模型费用、重复提交或取消后是否发送时断言。
- 兼容输入只为实际仍存在的格式保留。实验工具默认不保留专用回归测试。
- 源码字符串、固定颜色像素、简单转发、阶段完成和内部实现不作为回归条件。
- 精简时实际删除废弃文件和断言；不用跳过、改名、排除收集或搬到测试副本降低数量。

## 自动测试入口

在项目根目录使用 PowerShell 7 运行：

```powershell
& .\runtime\python\python.exe tools\run_test_suite.py --inventory
& .\runtime\python\python.exe tools\run_test_suite.py quick
& .\runtime\python\python.exe tools\run_test_suite.py full
& .\runtime\python\python.exe tools\run_test_suite.py serial
& .\runtime\python\python.exe tools\run_test_suite.py release
& .\runtime\python\python.exe tools\run_test_suite.py review
```

| 入口 | 当前内容 |
|---|---|
| `quick` | 配置保存、复核评分、批改失败恢复、推荐采用、模型请求与报告保存，配合相关页面和题框编辑器测试 |
| `full` | 当前业务后端与隔离组、前端规范检查、全部单元测试、类型检查、独立目录构建和真实浏览器复核保存 |
| `serial` | Windows 文件与进程状态相关的批改流程和备份恢复检查 |
| `release` | `full` 加数据库版本、升级备份和失败回退检查；不代表完整历史升级或安装包验收 |
| `review` | 复核保存与最终分锁、页面草稿和真实浏览器改分、刷新、重新进入、冲突保护与成绩表下载 |

分组以 `tools/test_suite_manifest.py` 为准。后端按文件并行，默认最多 6 个进程，可用 `--workers` 指定；前端最多 4 个进程。`--durations` 默认显示 10 个慢用例，0 关闭明细。`--skip-frontend` 只适用于后端排查；`review` 不能跳过前端。

`quick` 和 `review` 复用完整用例。`release` 包含 `full`；数据库升级检查只在发布入口执行。`tools/smoke_check.py` 的后端检查委托给 `full --skip-frontend`，它还检查文档、静态编译和数据库副本初始化。

GitHub 的 [CI 工作流](../../.github/workflows/ci.yml) 检查测试数量、文档、仓库数据边界及快速后端流程；前端运行规范检查、单元测试、类型检查、构建、演示服务测试及配置保存浏览器流程。仓库数据与密钥路径检查的合成回归并入 `tests/test_test_suite_runner.py`。CI 不配置真实模型密钥，不代替完整功能和真实 API 浏览器验收。

## 浏览器验收

`review` 和 `full` 通过 `tests/api_e2e/harness.py` 建立合成考试，用真实页面、API 和临时数据库保存分数，不拦截保存响应。后端与前端使用独立动态端口，浏览器直接运行当前源码，不重建共享 `frontend/dist`。截图、日志和追踪保存在本次新建的 `output/review_browser_*` 目录。

在 `frontend/` 目录运行其他保留入口：

| 操作 | 命令 | 环境 |
|---|---|---|
| 配置保存、刷新和冲突处理 | `npm run e2e` | 模拟接口，独立临时构建目录，端口 5173 |
| 题库标签保存、重新进入和整卷题目列表 | `npm run e2e:question-bank` | 真实本地 API、合成数据，端口 8016，独立临时构建目录 |
| 复用已有独立前端构建 | `npm run e2e:question-bank:prepared` | 先设置 `QB_BROWSER_FRONTEND_DIST` 指向当前完整构建 |
| 批量复核演示服务的合成行为 | `npm run demo:test` | Node 测试，不需要浏览器 |

浏览器需要已安装的 Playwright Chromium。固定端口的专项运行前确认没有其他任务共用服务。`npm run demo:review-batch` 可单独启动演示服务；正式复核保存验收使用 `review`。

题库专项使用跨过分页边界的小规模合成试卷，不验证大题库的响应时间。布局尺寸、焦点顺序和缓存请求次数不属于保留的浏览器回归条件。

## 隔离规则

- 统一入口移除模型密钥环境变量，把数据、配置、分类状态和运维状态指向本次 `.test-runs/` 临时目录。
- 直接运行 pytest 也隔离路径管理器和临时目录；显式 `--basetemp` 由调用者负责选择。
- 每个用例获得独立数据库副本，测试写入不进入基础库。业务文件可用清单中的当前结构基础库；历史版本、升级、恢复和真实 API 启动沿用生产初始化。
- `SERIAL_TEST_PATHS` 在并行组结束后运行；`PROCESS_ISOLATED_TEST_PATHS` 另用独立 pytest 进程。
- 用例结束释放数据库观察连接；运行结束清理本轮数据、基础库和日志，恢复原有环境。强制终止或文件占用时可能残留本轮临时目录。
- 修改测试分组后，用 `tests/test_test_suite_runner.py` 检查路径和隔离边界。

掌握度和训练卷只读维护实验不属于自动测试，用法见 [训练维护实验](../maintenance/training-experiments.md)。
