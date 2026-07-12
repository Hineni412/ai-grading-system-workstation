# AI 阅卷系统前端工程

本目录是 Vue 3 + TypeScript + Vite 前端。P2-01 提供可重复的安装、检查、测试和构建能力；P2-02 提供设计 Token、Element Plus 主题、基础状态控件和组件展示页。应用外壳、API Client 与业务页面仍由后续执行包实现。

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

## 设计系统

开发服务器启动后，根页面展示 P2-02 的设计系统样张。它用于核对按钮、输入、状态徽章、空/加载/错误态、操作反馈、长中文、键盘焦点和响应式布局，不是已经迁移的业务页面。

- `src/styles/tokens.css` 是产品颜色、字号、间距、圆角、阴影和动效的唯一来源。
- `src/styles/element-theme.css` 只负责把产品 Token 映射到 Element Plus；不得在这里新增独立色板。
- `src/styles/base.css` 保存全局盒模型、字体、焦点和减少动效规则。
- `src/components/design-system/` 保存产品语义组件。按钮和输入继续按需使用 Element Plus；字段关联、状态徽章、异步状态和反馈使用 P2-02 组件。
- 除 `tokens.css` 外，不得在新增 Vue/CSS 源码中写十六进制、RGB 或 HSL 色值；常用间距、圆角和阴影必须使用 Token。
- 组件样式按需导入，禁止恢复 `element-plus/dist/index.css` 或全量 `app.use(ElementPlus)`，以免把未使用控件加入启动包。

P2-02 不包含 App Shell、路由、全局会话、API Client、Job Store、深色模式或业务页面。修改展示页后必须运行全部质量命令，并检查 1440×900、1280×800、1024×768、768×1024、390×844 五种视口。
