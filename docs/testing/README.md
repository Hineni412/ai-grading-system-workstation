# 分层验收测试

测试不再要求每次修改都等待十多分钟。现有用例只维护一份，按用途进入不同车道：

| 入口 | 用途 | 运行内容 |
|---|---|---|
| `quick` | 日常修改后快速发现明显回归 | 关键后端契约、一个合成全流程、Vue 与题框编辑器单元测试 |
| `full` | P3.5 功能候选准备合并时 | 当前产品后端测试、串行隔离组、前端检查/测试/构建 |
| `serial` | 排查 Windows 文件锁、子进程和全局状态问题 | 只运行不能安全分给多个 pytest 进程的文件 |
| `release` | 正式发布或改动发布工具时 | `full` 的全部内容，再加历史阶段证据、打包和性能基线 |

## 常用命令

在项目目录用 PowerShell 7 运行：

```powershell
& .\runtime\python\python.exe tools\run_test_suite.py quick
& .\runtime\python\python.exe tools\run_test_suite.py full
& .\runtime\python\python.exe tools\run_test_suite.py serial
& .\runtime\python\python.exe tools\run_test_suite.py release
```

不熟悉命令时，可以直接双击：

- `运行核心测试.bat`：对应 `quick`。
- `运行完整验收.bat`：对应 `full`。
- `运行隔离测试.bat`：对应 `serial`。

并行进程数会按本机处理器数自动选择，最多 6 个。需要复查性能时可临时指定，例如
`--workers 4 --durations 30`。每个测试文件只进入一个后端车道，不会重复执行。

## 为什么发布证据不再进入每次合并验收

Phase 1—3 的冻结结构报告、旧交接流程、便携打包和性能报告生成器仍有保存价值，但它们主要验证
“发布证据能否重新生成”，不直接覆盖教师当前使用的批改、题库、成绩中心或导出行为。它们没有删除，
而是移入 `release`；相关工具发生变化或准备正式发布时仍完整运行。

## 隔离和费用边界

- Python 测试继续使用合成数据库、合成图片和测试替身。
- 前端单元车道同时覆盖 Vue 应用和独立题框编辑器，不再遗漏后者的 16 个 Node 测试。
- 入口会把成绩数据、工作区数据、模型配置、分类状态和运维状态全部改到一次性临时目录；
  即使电脑预先设置过这些目录，也不会沿用，并会移除子进程中的 API Key。
- 不读取或写入正式 `user_data/`，不调用真实模型，也不绑定固定服务端口。
- 前端浏览器端到端脚本不在默认验收中；它们需要独立服务或真实页面时另行明确运行。
- 后端并行使用 `loadfile`：同一个测试文件始终留在同一进程，减少模块级状态互相干扰。
- 隔离组中仍会继承全局状态的少数文件会再放进各自的新进程，避免前一个文件留下的临时覆盖影响结果。

## 新测试如何分组

1. 新测试默认进入 `full` 的并行车道。
2. 只有能复现固定端口、Windows 文件锁、子进程或进程级全局状态冲突时，才把整个文件加入
   `SERIAL_TEST_PATHS`；若即使串行也会受前一个文件的全局状态影响，再加入
   `PROCESS_ISOLATED_TEST_PATHS`。
3. 只有发布包、冻结阶段证据或性能基线生成器测试才加入 `RELEASE_AUDIT_TEST_PATHS`。
4. `QUICK_TEST_PATHS` 只保留少量跨模块哨兵；优先复用现有产品测试，不复制“快速版”测试。
5. 三份清单位于 `tools/test_suite_manifest.py`，修改清单后先运行
   `tests/test_test_suite_runner.py` 检查路径和分组。
