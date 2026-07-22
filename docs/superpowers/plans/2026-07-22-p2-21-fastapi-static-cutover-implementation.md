# FastAPI 静态托管与启动切换即时实现计划

**执行包：** P2-21
**规划状态：** ready_for_execution
**规划模型：** S-XH
**允许夜间执行：** no
**计划基线：** bb01d532a2de2409780c27741a79c161f7610853
**用户自测：** formal
**自测清单：** docs/user-testing/checkpoints/P2-21-v1.5.0-fastapi-startup-cutover-formal.md

> `daytime_only` 仍是定时夜间自动化的稳定资格；本次实施来自用户在当前任务中明确批准的连续作业，只允许形成自动验证和独立复审通过的候选。正式用户验收前不得合入 `main`。

## 冻结边界

### 目标

让双击 `运行.bat` 默认在同一个 FastAPI 地址打开已构建的 Vue SPA 与 `/api/*`，并保留一个版本的显式 Streamlit 回退。便携包在没有 Node.js 的工作机上也必须包含同一启动器、完整后端和 `frontend/dist`。

### 包含

- FastAPI 托管 `frontend/dist/assets`，根路由与非 API 深路由回退到 `index.html`。
- HTML 使用禁止缓存策略，带哈希的构建资源使用长期不可变缓存；API 和下载路由保持现有缓存与错误语义。
- 缺少或不完整 `frontend/dist` 时返回安全提示，默认启动在执行离线数据动作前先失败关闭。
- 新的 FastAPI 前台启动器只绑定 `127.0.0.1`，在服务真正开始监听后打开浏览器。
- `运行.bat` 默认启动 Vue/FastAPI；`USE_STREAMLIT=1` 显式恢复旧入口，并继续支持回退模式下的 `START_API=0`。
- 便携打包复制 `backend/`、唯一真实启动器和构建产物，缺少构建产物时拒绝生成不完整包。
- 使用合成或隔离数据完成自动启动、刷新、重启、深路由、缺失资源和 Streamlit 回退候选验证；实现后才生成版本化 formal 清单。

### 明确不包含

- 不删除或改写 `web_app.py`、`pages/`、Streamlit 依赖或旧页面；这些属于 P2-22。
- 不改变 Vue 页面、导航、API 字段、评分规则、任务状态、数据库 Schema 或真实业务数据。
- 不调用真实模型，不使用真实密钥，不验证 Issue #67 的延期内容。
- 不在用户 formal 验收前合入 `main`，也不把 P2-22 记为可开始。

### 验收条件

- 默认启动只打开 `http://127.0.0.1:<API_PORT>/`，同源 `/api/healthz` 正常；浏览器打开不早于服务监听。
- 根路由和至少一个 Vue 深路由刷新返回 SPA；未知 `/api/*` 不返回 HTML；真实 API 路由与 OpenAPI 不被 fallback 吞掉。
- `index.html`/SPA fallback 为 `no-store`；`/assets/*` 为长期 immutable；现有敏感下载仍保持 `no-store`。
- 缺少 `index.html` 或 `assets` 时默认启动在离线数据动作和服务器启动前停止，并只显示安全可操作提示。
- `USE_STREAMLIT=1` 能进入旧入口，旧 UI 和 `START_API=0` 回退语义保留。
- 便携布局包含 `backend/`、`frontend/dist`、同版 `运行.bat`，排除 `frontend/src` 和 `node_modules`，启动不需要 Node.js。
- 包内聚焦与受影响回归、前端 verify、快速冒烟、独立 Spec/Standards 复审通过；正式清单 Blocker/Major 清零且只能由用户明确写为 `passed`。

### 风险等级

高。原因是默认生产启动入口发生变化；回退是单环境变量且旧 UI 完整保留，因此代码层面可直接回退本包提交。

## 业务能力与 UX 设计溯源表

| 用户任务或业务能力 | 现有业务来源 | 必须保持的语义、结果与安全边界 | 新 Vue 呈现与操作设计 | UX 调整理由 | 业务差异及用户决定 | 验证方式 |
|---|---|---|---|---|---|---|
| 双击启动系统 | `运行.bat`、`backend.ops.offline`、启动契约测试 | 便携 Python、loopback、离线待处理动作先于服务器、异常可见 | 默认打开 FastAPI 同源 Vue 地址 | 让已迁移 Vue 成为单一日常入口，避免两个端口和两个 UI 并存 | Phase map 已冻结；无新增业务差异 | 启动契约、进程/HTTP 浏览器验证 |
| 刷新与直接进入深路由 | Vue Router、既有 P2 浏览器服务器 | API 不能被 SPA fallback 吞掉；路径不能读取任意文件 | 非 API GET 深路由统一返回 `index.html` | 用户可收藏、刷新和恢复当前页面 | 无 | TestClient、浏览器刷新 |
| 使用旧 Streamlit 应急 | 当前 `运行.bat` 与 Streamlit 生产入口 | 旧页面、数据根、端口和可选 API 行为保持一个版本 | `USE_STREAMLIT=1` 显式切换 | 默认入口清晰，同时保留可理解回退 | Phase map 明确要求保留一版 | 批处理契约、隔离启动验证、formal 清单 |
| 在新电脑使用便携包 | `package_v1.5.0.py`、现有 dist-only 打包测试 | 不要求系统 Python/Node，不携带 API 密钥，真实数据规则不降低 | 包含完整后端、构建产物和同一启动器 | 避免源码入口与便携入口漂移 | 无 | 临时布局测试、无 Node 启动候选 |

## 故障场景与预期结果

| 场景 | 预期结果 |
|---|---|
| 重复双击或端口已占用 | 后启动实例明确失败并保留控制台信息，不打开未确认的新页面；不触碰数据之外的启动前既有安全动作 |
| Vue 与回退入口同时启动 | 默认模式只运行 FastAPI；只有显式回退才运行 Streamlit，回退模式是否附带 API 继续由 `START_API` 控制 |
| 启动中途关闭 | 前台 FastAPI/Streamlit 随窗口退出；没有后台业务 Job 伪装为继续运行，既有 Job 重启恢复规则不变 |
| 重启 | 相同地址恢复；离线待处理动作仍在任何服务器前执行；Vue 深路由刷新仍可达 |
| 启动失败后重试 | 端口或 dist 修复后可直接再次运行，不写临时配置或永久切换标记 |
| 用户取消/关闭窗口 | 不新增取消协议；沿用前台进程关闭行为，后台回退 API 窗口保持当前旧模式行为 |
| API 已启动但浏览器打开失败 | 服务继续可用并在控制台显示 URL，浏览器失败不终止服务器 |
| `dist` 缺失或部分缺失 | 默认模式在离线数据动作前失败；回退模式仍可显式启动旧 UI |
| 环境变量冲突 | `USE_STREAMLIT=1` 优先决定回退；`API_PORT` 与 `PORT` 分别只控制对应入口；所有监听仍固定 loopback |

## 集中调查冻结问题

1. `backend/api/app.py` 当前只有 API 路由，没有生产静态资源和 SPA fallback。
2. `运行.bat` 当前默认后台启动 API、浏览器打开 8501，并以前台 Streamlit 作为主进程。
3. `package_v1.5.0.py` 会生成另一份旧 Streamlit 启动器，导致源码与便携包行为漂移。
4. 便携打包的生产目录未包含 `backend/`；即使切换启动器，当前包也无法启动 FastAPI。
5. 打包在 `frontend/dist` 缺失时静默复制 0 个文件，能产出不可启动的包。
6. 已有 P2 浏览器工具证明 `/assets` 加最后一个 GET fallback 可工作，但没有生产缓存头、缺失 dist 和启动顺序契约。

以上问题按 3 个根因冻结：生产托管缺口、启动入口漂移、便携布局不完整。相邻范围和 P2-22 删除不纳入本包。

## 实施步骤

- [ ] 新增静态托管 RED 测试：根/深路由、hashed assets 缓存、未知 API、OpenAPI、缺失 dist、安全路径。
- [ ] 实现独立的前端托管深模块并在所有 API router 注册后挂载，保持 API 契约不变。
- [ ] 新增启动器 RED 测试：dist 预检、监听后开浏览器、浏览器失败不杀服务、loopback/端口边界。
- [ ] 实现 FastAPI 前台启动器和 `运行.bat` 默认/回退分支，保持离线动作顺序。
- [ ] 扩展便携打包 RED 测试：复制 `backend/`、唯一启动器、dist 必需项与无 Node 布局。
- [ ] 修正打包清单并让便携启动器复用源码唯一入口；更新私人版启动说明。
- [ ] 在临时目录和合成数据上执行默认启动、根/深路由刷新、重启、dist 缺失和 Streamlit 回退自动候选验证。
- [ ] 生成版本化 formal 清单，写明数据源、地址、可见标识、关闭和回退方式，机器结果保持 `pending`。
- [ ] 运行包内聚焦测试、受影响 API/打包/启动回归、前端 `npm run verify` 和 `tools/smoke_check.py --skip-tests`；核对真实 `user_data` 未触碰。
- [ ] 冻结同一候选并并行执行 Spec 与 Standards 复审；统一修复至多一次，必要时由原评审者限定终审。
- [ ] 独立复审通过后创建只改即时计划的 `waiting_user` 锚点提交，并合入 `codex/integration-m2-04` 做逐包验证；等待用户 formal 验收，不进入 `main`。
- [ ] 若只剩用户验收，按已批准例外从该候选临时领取低风险 P3-01；P2-22 继续停机。

## 预计验证命令

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_api_frontend_hosting.py tests\test_run_bat_api_entry.py tests\test_frontend_portable_packaging.py -q
..\..\runtime\python\python.exe -m pytest tests\test_api_app.py tests\test_api_openapi_contract.py tests\test_portable_path_resolution.py -q
..\..\runtime\python\python.exe tools\smoke_check.py --skip-tests
```

前端在工作区依赖重新探测后运行一次 `npm run verify`；浏览器验证只复用该次构建产物，不重复构建。

## 回退

回退本包的静态托管、启动器和打包提交即可恢复当前 Streamlit 默认入口。回退不涉及数据库迁移、数据转换、真实业务文件或 API Schema。

## 自动验证与复审记录

- 自动验证：静态托管、启动器、批处理契约与便携打包 20 项通过；受影响 API/OpenAPI/便携路径回归 24 项通过；快速冒烟通过（文档治理、510 个第一方 Python 文件编译、两库副本初始化幂等）。前端 `npm run verify` 的 716 项通过证据可从未改动前端源码的同一构建基线复用。
- 首轮复审：冻结候选 `f3c03c67846aec8fcd2c52fc9a5ac4587372b7a0` 发现 3 项 Important，已统一修复为 `9866401f4c13149351b9977161375374afd951be`；其中空资源、监听失败与 formal 清单缺失均已关闭。
- 首次终审：候选 `f5b5f4995623352b8cca9d243c484f28478b5032` 仍发现便携打包接受空 `assets/` 的 Important。用户于 2026-07-22 明确授权建立限定后续修复，不扩大 P2-21 范围。
- 后续修复：`030d58fffbda2c8d548075d8ce1284bc8e746d3d` 在复制前拒绝空资源目录；20 项关联测试和快速冒烟通过，限定 Spec/Standards 复审均为 Critical 0 / Important 0 / Suggestion 0。
- 用户于 2026-07-23 在只含空白隔离双库的干净便携副本中完成 formal 清单：默认 Vue/FastAPI、同源 API、根/深路由、未知 API、重启、端口占用、缺失 `dist`、Streamlit 回退与关闭清理均通过，并明确回复“P2-21 正式验收通过”。验收辅助批处理首次因非 Windows 换行闪退，不属于候选文件；修正辅助文件后回退入口约 26 秒开始监听，用户复验通过。最终 8000、8501 均释放，隔离副本相关进程为 0，真实模型调用为 0，真实 `user_data/` 未读取、复制或写入。
- P2-21 自动验证、独立复审和 formal 用户验收均已通过，可以完成 integration 收口并进入单包里程碑 PR；P2-22 仍须等待 P2-21 稳定运行一个人工验收周期，不因本次交接自动开始删除或退役。

<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** P2-21
**交接状态：** verified_pending_integration
**功能提交：** 60b3c931627f350f9b8d1bbbdf8c74c47bee6754
**自动验证：** passed
**独立复审：** passed
**用户验收：** passed
**真实数据指纹：** not_touched
**Stash 基线：** 85726b3b9863575c9aebe4ff12916e96d4bb08ba,67edf9783a70b42878c44ae05eea25528b51ddf2
**夜间动作：** independent_candidate_allowed
<!-- HANDOFF_STATUS_END -->
