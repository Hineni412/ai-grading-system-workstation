# 当前测试与人工验收

本文件只说明当前可用的测试入口、隔离边界和人工验收方法。测试默认使用合成数据和模型替身，不接触真实业务数据，也不产生真实模型费用。

## 自动测试入口

在项目根目录使用 PowerShell 7 运行：

```powershell
& .\runtime\python\python.exe tools\run_test_suite.py quick
& .\runtime\python\python.exe tools\run_test_suite.py full
& .\runtime\python\python.exe tools\run_test_suite.py serial
& .\runtime\python\python.exe tools\run_test_suite.py release
```

| 入口 | 适用场景 | 当前内容 |
|---|---|---|
| `quick` | 日常修改后的快速反馈 | 关键后端契约和合成主流程，前端单元测试与题框编辑器测试 |
| `full` | 完整功能候选 | 当前后端测试、串行隔离组，以及前端静态检查、单元测试和构建 |
| `serial` | 单独复现隔离问题 | 只运行依赖 Windows 文件锁、固定端口、子进程或进程级状态的测试文件 |
| `release` | 发布候选或发布工具发生变化 | 先运行 `full`，再运行打包、性能和发布治理检查 |

并行进程数按本机处理器数自动选择，最多为 6；需要观察慢用例时可加 `--workers` 和 `--durations`。测试文件分组以 `tools/test_suite_manifest.py` 为准。

## 隔离规则

- 所有自动测试使用合成数据库、合成图片和测试替身。
- 测试入口把成绩数据、工作区数据、模型配置、分类状态、运维状态和本机应用数据目录改到本次运行的临时沙箱。
- 子进程会移除模型密钥等敏感环境变量；测试不得调用真实模型。
- 测试不得读取、写入或迁移真实 `user_data/`，也不得依赖用户电脑中已有的业务文件。
- 后端并行采用按文件分配，同一测试文件始终留在同一进程。
- 只有确实依赖文件锁、固定端口、子进程或全局状态的文件才进入 `SERIAL_TEST_PATHS`；若前一文件留下的进程状态仍会影响结果，再进入 `PROCESS_ISOLATED_TEST_PATHS`。
- 新测试默认进入 `full`。`QUICK_TEST_PATHS` 只保留少量跨模块哨兵，`RELEASE_AUDIT_TEST_PATHS` 只收发布候选需要的治理检查。
- 修改测试分组后，先运行 `tests/test_test_suite_runner.py` 检查路径唯一性和车道边界。

## 通用人工验收

人工验收按以下顺序进行；前一项未通过时先修复，不把后面的检查当作替代证据：

1. 应用能够正常启动。
2. 目标页面能够打开，加载、空白、失败和禁用状态表达清楚。
3. 第一条核心用户流程从入口到结果完整跑通。
4. 保存后重新进入，数据和页面状态仍然正确。
5. 主要操作、布局、长内容、键盘焦点和风险提示正常。
6. 受影响的自动测试通过。
7. 对照本次需求检查结果，并做一次代码质量检查。

涉及真实学生资料、试卷、数据库、模型密钥、模型调用、费用、迁移、覆盖、删除或恢复时，必须针对本次精确操作另行获得用户明确授权。一次授权不自动延续到下一次操作。

## A/B 浏览器验收

`tools/testing/ab_browser_acceptance.py` 提供本机、可复现的浏览器验收环境。它只监听回环地址，使用合成的 A/B 工作台数据和假模型，不需要真实密钥。

先检查环境：

```powershell
& .\runtime\python\python.exe tools\run_project_module.py tools.testing.ab_browser_acceptance self-check --port 8765
```

再启动验收服务；需要同时重建前端时使用：

```powershell
& .\runtime\python\python.exe tools\run_project_module.py tools.testing.ab_browser_acceptance serve --port 8765 --build-frontend
```

浏览器验收至少覆盖：

- A/B 工作台都能从当前导航打开；
- 核心页面、弹层、加载、空白、失败和禁用状态可辨认；
- 关键流程的 `flow_markers` 符合预期；
- 保存或确认后刷新页面，状态仍然一致；
- `/__acceptance__/manifest` 返回当前合成场景清单；
- `/__acceptance__/model-counts` 证明只有预期的假模型调用；
- 截图、网络摘要、刷新结果和模型计数写入 `output/playwright/ab-browser-*`，临时数据保留在 `.test-runs/ab-browser-*`。

这些证据只证明合成环境中的页面与流程，不代表已经获准读取真实数据或调用真实模型。
