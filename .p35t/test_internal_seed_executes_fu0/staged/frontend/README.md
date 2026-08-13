# AI 阅卷系统前端

`frontend/` 是当前生产界面源码。它使用 Vue 3、TypeScript、Vite、Pinia 和 Vue Router；生产构建由 FastAPI 同源托管，默认只监听本机 `127.0.0.1:8000`。

日常使用者不需要安装 Node.js。双击根目录 `运行.bat` 后，浏览器访问：

```text
http://127.0.0.1:8000/
```

页面与 `/api/*` 使用同一个本机来源。浏览器不保存 API Key，业务数据继续由后端和本地数据库管理。

## 当前路由

路由定义以 `src/navigation.ts` 和 `src/router/index.ts` 为准。

| 地址 | 用途 |
|---|---|
| `/` | 转到工作台 |
| `/workbench` | 当前考试进度、待处理事项与学情分析 |
| `/sessions` | 创建考试、准备来源与评分依据 |
| `/sessions/:sessionId/regions` | 上传样卷并标定答题区域 |
| `/sessions/:sessionId/grading-run` | 上传整班答卷并控制批改运行 |
| `/students` | 导入、核对和维护学生名单 |
| `/question-bank` | 筛选、核对、导入和维护题目 |
| `/question-assembly` | 选择题目、整理分节并导出试卷 |
| `/training` | 核对薄弱证据、确认训练计划并生成材料 |
| `/knowledge-graph` | 按考试、班级和学生查看知识标签证据 |
| `/files` | 生成、查看和下载成绩表、批注原卷与训练材料 |
| `/grading` | 按题号比较并复核评分结果 |
| `/settings` | 查看系统状态并执行受保护的运维操作 |
| `/design-system` | 开发用视觉与基础控件展示 |

其他地址进入 404 页面。FastAPI 负责业务深路由刷新时的 SPA 回退；不存在的 `/api/*` 仍返回 API 错误，不会被当成前端页面。

## 目录

| 目录 | 职责 |
|---|---|
| `src/api/` | 同源 API 请求、运行时响应校验和安全错误 |
| `src/stores/` | 页面状态、请求协调和可恢复引用 |
| `src/views/` | 路由级工作区 |
| `src/components/` | 可复用业务与视觉组件 |
| `src/layouts/` | 应用布局 |
| `src/router/`、`src/navigation.ts` | 路由和导航定义 |
| `src/styles/` | Token、全局样式和页面样式 |
| `src/__tests__/`、`e2e/` | 前端自动化检查 |
| `dist/` | 生产构建产物；不要手工编辑 |

共享答题区几何能力位于根目录 `components/answer_region_editor/`，Vite 开发服务器已显式允许读取该目录。

## 本地开发

要求：

- Node.js：`^22.18.0 || >=24.12.0`
- npm：`11.8.0`

安装并启动前端开发服务器：

```powershell
npm --version
npm ci
npm run dev
```

开发服务器只监听 `127.0.0.1`，并把 `/api` 代理到 `http://127.0.0.1:8000`。需要真实业务响应时，应先在仓库根目录启动 FastAPI。

## 构建

```powershell
npm run build
```

构建结果写入 `frontend/dist/`，由 FastAPI 在生产环境直接提供。发布到工作机时携带构建产物即可，不需要携带 Node.js 开发环境。

开发时还可按当前任务需要使用：

```powershell
npm run lint
npm run typecheck
npm run test
npm run e2e
npm run verify
```

这些命令用于开发反馈或准备可运行页面；具体运行范围由当前任务决定，不代替用户对实际页面的体验与判断。
