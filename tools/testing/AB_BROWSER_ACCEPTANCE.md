# A/B 隔离浏览器验收环境

这个工具只创建合成资料和合成学生。每次启动都会分配新的目录，不读取或覆盖
`user_data`、真实 API 配置或真实本机标签状态。

## 启动前自检

从仓库根目录运行：

```powershell
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe tools\run_project_module.py tools.testing.ab_browser_acceptance self-check --port 8765
```

自检会初始化迁移、合成 PDF/PPTX、两名合成学生和 A/B 服务，再通过应用生命周期
读取健康状态。它不会发送模型请求，也不会启动长期后台服务。

## 前台启动

首次或前端代码变更后：

```powershell
D:\AI阅卷系统_工作机版_v1.5.0\runtime\python\python.exe tools\run_project_module.py tools.testing.ab_browser_acceptance serve --port 8765 --build-frontend
```

已经构建当前前端时可省略 `--build-frontend`。服务只绑定
`127.0.0.1:8765`，按 Ctrl+C 停止。启动摘要会显示本次 run ID、数据目录、日志目录、
证据目录、学期/课时 ID 和两个不透明合成学生 ID。

## 证据和模型计数

- 数据、`LOCALAPPDATA`、正文诊断日志：`.test-runs/ab-browser-*/`
- 截图、网络记录、保存重进状态、模型计数：`output/playwright/ab-browser-*/`
- 只读 seed 合同：`GET /__acceptance__/manifest`
- 只读物理模型请求计数：`GET /__acceptance__/model-counts`

假模型只在内存中保留各用途的整数计数，不保留请求或响应正文，也没有重置接口。
请求/响应正文只会按产品规则进入本次临时目录中的
`logs/llm_diagnostics.jsonl`，不会复制到服务输出或浏览器存储。

## 三类班主任合成输入

从 manifest 读取并逐条使用 `flow_markers`。记录事务会故意返回“学生 ID 正确但版本
错误”的引用，用来验证页面保留草稿、清空失效引用并提示教师重新选择；日程和 SOP
分别形成可审核的计划、流程草稿。每条操作前后都读取模型计数，确认一次明确点击只增加
一次物理请求。

## 浏览器证据约定

每个核心流程至少保留：操作前截图、提交后的网络响应和 `x-request-id`、保存后截图、
重新进入后的截图，以及操作前后两份模型计数 JSON。HAR 或筛选后的网络 JSON 放在
`network/`；不要把模型请求/响应正文另存到 HAR 或测试报告。
