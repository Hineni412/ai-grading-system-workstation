# 学情总览与知识结构改版验收（2026-10-03）

用途：记录实施方案的验证方法、代码测试结果与限制。不保存真实数据、派生统计或响应内容。产品规则以 `docs/product/KNOWLEDGE_AND_TRAINING.md` 为准。对照原型为 `knowledge-training-redesign-prototype-20261003/`，原型文件保留。

## 实施

两页共用学情总览接口和前端缓存。总览呈现优先技能、学生关注名单与章节进度；知识结构呈现本册两栏地图、六级热度、关联连线及详情抽屉。训练入口只预填，沿用现有流程。

## 本机视觉与交互检查方法

- 分别选择全部学生和单班范围，对照两页指标及章节进度。区分按人统计的“有证据学生”和按知识点或技能统计的地图“有证据”。
- 对照原型检查总览四个指标、优先技能卡片、四档人数条、学生名牌、关注名单、章节热度条和折叠学生表。
- 展开尚未考查的章节与往届内容，核对页头口径。查看有跨节关联的知识点及其详情抽屉，检查格子、名称角标和连线位置。
- 从总览建议进入按学生训练，核对学生、技能、小节、专项模式和一人一卷设置。仅操作预填入口，不执行草稿生成。
- 从错题入口和学生名牌进入证据页，并通过返回入口返回总览。检查返回后的范围选择。
- 使用 800×900 像素视口检查横向溢出、两栏上下排列、全宽底部抽屉和窄屏不画连线。结束后恢复默认视口。

按用户授权，通过本机应用执行上述检查，未调用真实模型或执行数据库迁移。验收截图留在本机 `user_data/reports/TEST-knowledge-redesign-20261003/`，不进入版本库或普通文档。原型与实施方案的差异按方案第 6 节执行，沿用项目现有导航外壳。

## 性能验证方法与选择

相同输入、每次新建服务对象，在未启用应用启动预热的情况下测量首次请求，再测量同范围第二次请求。未清理真实缓存或复制真实数据。响应大小按 UTF-8 JSON 字节数计算。

已向用户报告测量结果，并得到确认：继续一次加载。两页切换复用同一份结果。真实数据的测量数值不写入本文。

## 代码测试结果

- `runtime/python/python.exe -m pytest tests/test_api_graph_selected_scope.py -q`：3 个测试通过。覆盖本册统计、往届节点、定义、区间、关联筛选、摘要与完整诊断一致、快照恢复及关联读取失败。
- 前端相关单元测试：`knowledge-overview-view.spec.ts`、`knowledge-graph-view.spec.ts`、`training-api.spec.ts`、`workbench-view.spec.ts`，共 26 个测试通过。
- `npm exec playwright test e2e/knowledge-graph.spec.ts -- --reporter=line`：3 个测试通过。覆盖共享请求、实线与虚线、抽屉、Esc 焦点恢复、筛选、窄屏及训练跳转。隔离数据下，连线路径采样未进入知识格内部。
- `npm run build`：类型检查、生产构建及 JavaScript 包体预算通过。
- 本次前端文件的 ESLint 检查通过。项目全量检查存在其他任务测试文件的条件断言错误，见下述限制。
- `runtime/python/python.exe tools/check_documentation.py` 与 `git diff --check` 通过。

测试扩展现有文件，没有新增测试文件。删除随旧图页面失效的 `knowledge-structure-browser.spec.ts`、`knowledge-graph-store.spec.ts`、`knowledge-graph-route.spec.ts`。产品文档、架构说明、术语、样式规范和测试入口同步更新。

## 验证限制

未执行真实模型调用、正式出卷、打印或归卷；这些流程不属于本次改版验收。没有声称完成全项目回归。项目全量 ESLint 在 `personalized-recommendation-draft.spec.ts` 的两处 `vitest/no-conditional-expect` 报错；本次改版文件检查通过，未修改其他任务测试。
