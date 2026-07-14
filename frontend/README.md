# AI 阅卷系统前端工程

本目录是 Vue 3 + TypeScript + Vite 前端。P2-01 提供可重复的安装、检查、测试和构建能力；P2-02 提供设计 Token 和基础控件；P2-03 至 P2-08 已依次实现 App Shell、路由与当前考试上下文、统一 API Client 与 Job Store、单题复核队列样板页、答卷证据查看、评分检查与安全确认、页面级快捷键及固定匿名验收服务。这些都是当前实现事实，Vue 仍未切入生产。

这些实现只能作为待核验能力清单、审计输入和复用候选，不能单独证明业务规则。后续页面先追溯业务任务、数据语义、结果和安全边界，再主动设计导航、页面拆分、布局、控件与操作步骤；不要求复制当前外壳或旧 Streamlit。

## 开发环境

- Node.js：`^22.18.0 || >=24.12.0`
- npm：`11.8.0`（工程会拒绝其他版本，避免锁文件解析行为漂移）

```powershell
npm --version
npm ci
npm run e2e:install
npm run dev
```

`npm --version` 必须输出 `11.8.0`。若本机不是该版本，先运行 `npm install --global npm@11.8.0`，再执行干净安装。`e2e:install` 会下载与锁定 Playwright 版本匹配的 Chromium，仅开发和自动测试机器需要。

开发服务器只监听 `127.0.0.1`，并把 `/api` 代理到本机 FastAPI `http://127.0.0.1:8000`。

## 质量命令

```powershell
npm run lint
npm run typecheck
npm run test
npm run e2e
npm run build
```

生产构建输出到 `frontend/dist/`。最终工作机只携带该构建产物，不需要安装 Node.js。

## 路由与 App Shell

> **来源重校准说明：** 本节记录 P2-03 的路由与 App Shell、P2-04 的共享前端基础设施，以及 P2-05 至 P2-08 在该外壳中形成的单题复核样板页和验收能力。下列路由、三栏外壳和最小 1024px 支持范围都是当前实现事实，也只能作为待核验能力清单、审计输入和复用候选；它们不是业务权威或界面复刻模板。后续页面应先锁定业务能力、数据语义、结果和安全边界，再按任务效率主动设计导航、页面拆分、布局、控件与操作步骤，不要求复制当前外壳或旧 Streamlit。

开发服务器启动后，`/` 会重定向到 `/workbench`。当前路由入口如下：

- `/workbench`、`/grading`、`/exams`、`/students`、`/analytics`、`/question-bank` 和 `/settings`：P2-03 App Shell 内的占位工作区，不是已经迁移的业务页面。
- `/design-system`：保留的 P2-02 组件展示页。
- 其他地址：进入 404 页面，可返回工作台；不会改变当前考试选择或业务数据。

左侧导航以 `src/navigation.ts` 为唯一来源。“智能体与自动化”是可聚焦但不可进入的未来入口；设置固定在导航底部。产品只支持宽度不低于 1024px 的 Windows 桌面浏览器：≥1280px 使用完整三栏，1024–1279px 使用可收起左栏和缩窄右栏。平板和手机不属于实现或验收范围。

## 当前考试上下文

`src/api/sessions.ts` 保留 sessions 公开类型和运行时校验，请求已经由 `src/api/client.ts` 统一发送。`src/stores/session.ts` 继续负责加载考试列表、校验选择并向 App Shell 提供当前考试上下文：

- 已确认的考试 ID 保存在当前浏览器的 `localStorage`，键为 `ai-grading:selected-session:v1`，刷新页面后会尝试恢复。
- 只有服务器成功返回且列表中仍存在的未删除考试才会恢复；失效 ID 会在成功加载后清除。
- 加载失败时不会把已保存候选误当成当前考试，也不会删除它；重新加载成功后再校验和恢复。
- 清空选择会同步移除浏览器保存值。此持久化只属于当前浏览器，不写业务数据库，也不代表跨设备或多用户会话。

## API Client、错误与 Job Store

- `src/api/client.ts` 是唯一直接 `fetch` 边界，只接受以 `/api/` 开头的同源相对路径。每次请求都携带 request ID，并支持超时和显式取消。
- 只有 GET 的临时网络错误和 5xx 会有界退避重试。写请求、422、404、409、超时、取消和契约错误不会自动重放。
- 每个资源适配器拥有自己的受控手写类型和运行时 decoder。响应未通过 decoder 时按契约错误处理，不将原始数据交给 Store 或页面。
- `ApiError` 只保留状态码、安全 code/message/details、request ID、错误类别和可重试提示。通知端口不传递 details、payload 或 result，当前不渲染可见通知 UI。
- `src/stores/jobs.ts` 使用 `ai-grading:tracked-jobs:v1` 保存当前浏览器的 Job ID、类型和首次跟踪时间。刷新后只查询这些 ID，不扫描服务器历史任务。
- 每个 Job 只有一个轮询循环。断网保留上次快照，404 移除不可恢复引用，终态停止轮询。`cancel_requested=true` 只表示服务器收到取消请求，仍需继续查询到真实终态。

P2-04 不新增 Job 列表 API、通用提交契约、鉴权、业务页面或任何可见 UI。后续页面不得绕过 Client 直接调用 `fetch` 或将 API key 写入浏览器。

## 设计系统与外壳样式

`/design-system` 展示 P2-02 的设计系统样张。它用于核对按钮、输入、状态徽章、空/加载/错误态、操作反馈、长中文、键盘焦点和响应式布局，不是已经迁移的业务页面。

- `src/styles/tokens.css` 是产品颜色、字号、间距、圆角、阴影和动效的唯一来源。
- `src/styles/element-theme.css` 只负责把产品 Token 映射到 Element Plus；不得在这里新增独立色板。
- `src/styles/base.css` 保存全局盒模型、字体、焦点和减少动效规则。
- `src/styles/app-shell.css` 保存 P2-03 外壳布局；颜色、间距、边框、圆角、阴影、可复用外壳尺寸和动效继续引用 `tokens.css` 中的 Token。
- `src/components/design-system/` 保存产品语义组件。按钮和输入继续按需使用 Element Plus；字段关联、状态徽章、异步状态和反馈使用 P2-02 组件。
- 除 `tokens.css` 外，不得在新增 Vue/CSS 源码中写十六进制、RGB 或 HSL 色值；常用间距、圆角和阴影必须使用 Token。
- 组件样式按需导入，禁止恢复 `element-plus/dist/index.css` 或全量 `app.use(ElementPlus)`，以免把未使用控件加入启动包。

## 浏览器验收边界

Playwright 浏览器测试拦截 `/api/sessions` 并使用两条合成考试数据，不读取真实数据库。测试覆盖 1920×1080、1440×900、1366×768、1280×800、1024×768 五种桌面视口，检查横向溢出、控制台/页面错误、考试选择和刷新恢复、长名称、桌面栏位收起、失效保存值、失败重试、禁用未来入口、设置页和 404 返回。

P2-04 没有改动 App Shell、路由、样式或业务页面。浏览器回归继续使用合成 sessions 数据并与真实 `user_data/` 隔离。
