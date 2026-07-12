# AI 阅卷系统前端工程

本目录是 Vue 3 + TypeScript + Vite 前端的工程基础。P2-01 只提供可重复的安装、检查、测试和构建能力；视觉 Token、应用外壳、API Client 与业务页面由后续执行包实现。

## 开发环境

- Node.js：`^22.18.0 || >=24.12.0`
- 包管理器：Node 自带的 npm

```powershell
npm ci
npm run dev
```

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
