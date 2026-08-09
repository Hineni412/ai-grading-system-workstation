# 当前前端说明

`frontend/` 是 AI 阅卷系统当前使用的 Web 界面源码。生产构建由同一个本机后端提供，页面与 `/api/*` 使用同一来源；浏览器不保存模型密钥，业务数据由后端和本机数据库管理。

双击根目录 `运行.bat` 时，启动器会根据当前形态处理前端。开发源码目录含有 `frontend/package.json`，因此每次启动前都会构建前端，并要求与其 `engines` 和 `packageManager` 约束兼容的 Node.js 与 npm。便携包只包含已构建的 `frontend/dist`，启动器直接检查该目录，不要求 Node.js 或 npm。两种形态的便携 Python 都由项目自带。

## 当前路由

核心路由元数据位于 `frontend/src/navigation.ts`，页面装配和重定向位于 `frontend/src/router/index.ts`。

| 地址 | 当前用途 |
|---|---|
| `/` | 重定向到 `/workbench` |
| `/workbench` | 当前考试进度、待处理事项和学情分析 |
| `/sessions` | 创建考试并准备评分依据 |
| `/sessions/:sessionId/regions` | 上传样卷并标定作答区域 |
| `/sessions/:sessionId/grading-run` | 上传整班答卷、完成预检并控制批改 |
| `/students` | 导入、核对和维护学生名单 |
| `/question-bank` | 筛选、核对、导入和维护题目 |
| `/question-assembly` | 选题、整理顺序和分节并导出练习试卷 |
| `/training` | 核对知识证据并人工确定训练范围 |
| `/knowledge-graph` | 查看知识结构、章节学情并安排训练 |
| `/results` | 查看成绩、定位待处理题目并导出正式文件 |
| `/files` | 保留查询参数，重定向到 `/results?tab=exports` |
| `/grading` | 执行整班批改并人工评分或复核结果 |
| `/model-profiles` | 保留查询参数，重定向到 `/settings?section=models` |
| `/settings` | 管理模型使用方式、备份和本机维护 |
| `/teaching-prep` | 备课工作台 |
| `/class-teacher` | 班主任工作台 |
| `/design-system` | 开发用组件与状态展示 |

其他页面地址进入 404 页面。后端为业务深路由提供单页应用回退；不存在的 `/api/*` 仍返回 API 错误，不会被当成页面。

## 工作台 Manifest

工作台通过 `frontend/src/workspaces/registry.ts` 自动发现 `frontend/src/workspaces/*/manifest.ts`。注册表先校验模块编号、路由前缀、导航顺序、数据分类、功能标记和子导航，再按启用条件生成路由与导航；Manifest 无效时直接阻止注册，不静默降级。

当前模块：

- `teaching-prep`：路由 `/teaching-prep`，默认启用；只有 `VITE_TEACHING_PREP_ENABLED=0` 时关闭，子导航包含备课首页和资料库。
- `class-teacher`：路由 `/class-teacher`，当前启用。

新增工作台前先复用现有 Manifest 契约和注册表，不在路由文件中再维护一份重复模块清单。

## 目录职责

| 目录 | 职责 |
|---|---|
| `frontend/src/api/` | 同源 API 请求、运行时响应校验和安全错误 |
| `frontend/src/stores/` | 页面状态、请求协调和可恢复引用 |
| `frontend/src/views/` | 核心路由级页面 |
| `frontend/src/workspaces/` | 教师工作台 Manifest、共享契约和模块页面 |
| `frontend/src/components/` | 可复用业务与视觉组件 |
| `frontend/src/layouts/` | 应用布局 |
| `frontend/src/router/`、`frontend/src/navigation.ts` | 路由装配与导航元数据 |
| `frontend/src/styles/` | Token、全局样式和页面样式 |
| `frontend/src/__tests__/`、`frontend/e2e/` | 前端自动化检查 |
| `frontend/dist/` | 生产构建产物，不手工编辑 |

共享答题区几何能力位于根目录 `components/answer_region_editor/`，前端开发服务器已允许读取该目录。

## 当前命令

以下命令都在 `frontend/` 中运行。先按 `frontend/package.json` 安装兼容的 Node.js 与 npm，再安装锁定依赖：

```powershell
npm ci
```

| 命令 | 用途 |
|---|---|
| `npm run dev` | 在 `127.0.0.1` 启动开发服务器，并把 `/api` 代理到本机后端 |
| `npm run build` | 先做类型检查，再生成生产构建并检查 JavaScript 体积预算 |
| `npm run build-only` | 只生成生产构建并检查体积预算 |
| `npm run preview` | 在本机预览已有生产构建 |
| `npm run lint` | 运行代码规范检查 |
| `npm run typecheck` | 运行 TypeScript 与 Vue 类型检查 |
| `npm run test` | 运行前端单元测试 |
| `npm run test:editor` | 运行独立题框编辑器测试 |
| `npm run test:all` | 运行前端单元测试和题框编辑器测试 |
| `npm run verify` | 依次运行规范检查、全部单元测试和生产构建 |
| `npm run e2e:install` | 安装浏览器端到端测试所需的 Chromium |
| `npm run e2e` | 运行当前浏览器端到端测试 |

开发服务器和预览服务器只监听本机地址。需要真实业务响应时，先按项目根说明启动后端；读取真实数据或调用真实模型仍需逐次授权。
