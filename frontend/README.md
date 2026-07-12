# AI 阅卷系统前端工程

本目录是 Vue 3 + TypeScript + Vite 前端的工程基础。P2-01 只提供可重复的安装、检查、测试和构建能力；视觉 Token、应用外壳、API Client 与业务页面由后续执行包实现。

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
