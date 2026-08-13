# 领域文档

本项目采用 single-context（单一业务上下文）结构。

## 阅读顺序

1. 先遵守根目录 `AGENTS.md`。
2. 阅读 `ARCHITECTURE.md` 中的当前系统事实。
3. 需要统一业务词语时，读取根目录 `CONTEXT.md`（如果已建立）。
4. 涉及难以逆转的架构决定时，读取 `docs/adr/` 中的相关记录。
5. 当前阶段和下一动作只看 `docs/superpowers/packages/EXECUTION_INDEX.md`。

P3.5 不使用 Phase map、即时实现计划或执行包交接块。未来恢复正式 Phase 时，再按当时的 Index 和对应 Phase map 执行。

## 建立原则

- `CONTEXT.md` 只在首次真正需要统一领域术语时建立，不保存实现细节。
- ADR 只记录难以逆转、仅看代码不容易理解、且确实存在取舍的决定。
- 不为了“文档齐全”创建空文件。
- 任何文档都不得包含真实学生数据、密钥、内部绝对路径或未脱敏日志。
