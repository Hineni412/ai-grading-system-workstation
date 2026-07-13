# P1-24 Task 6 实施报告

## 范围

- 仅在后端默认 Job 构造、Streamlit 当前配置构造、题库规范配置档案构造中传播 LLM Gateway 策略覆盖。
- 所有覆盖均通过 `policy_overrides_from_profile()`，只保留十二个 `llm_*` 白名单字段。
- 环境变量构造、题库专用打标客户端和专用复核客户端继续使用 `policy_profile=None` 的安全默认策略。
- 未新增控件、环境变量导出、配置档案迁移、真实模型调用、P1-25 调用迁移或 `user_data/` 变更。

## RED

命令：

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_api_profile_store.py tests\test_llm_gateway_policy.py -q
```

结果：`3 failed, 44 passed`。三个预期失败分别证明后端默认入口、Web 已加载配置入口和题库规范配置档案入口尚未把白名单策略传入 `LLMSettings`。同轮中，白名单复制以及环境/专用客户端默认策略断言通过。

## GREEN

同一命令在最小实现后结果：`47 passed`。

指定受影响回归：

```powershell
..\..\runtime\python\python.exe -m pytest tests\test_api_profile_store.py tests\test_grading_limits.py tests\test_question_bank_ai_tagging_quality.py tests\test_question_bank_tagging_config_state.py tests\test_tagging_sync_job.py tests\test_config_generation_job.py -q
```

结果：`63 passed`。

## 自审

- 三处生产入口都调用既有白名单函数，没有手工复制字段或扩大允许范围。
- 测试配置同时包含密钥、完整私有 Base URL、模型名和已弃用字段；最终 `policy_profile` 只包含两个示例白名单字段。
- Web 测试确认策略字段没有被转存到环境变量。
- 环境变量构造、专用打标客户端和专用复核客户端均保持 `policy_profile=None`。
- `web_app.py` 仍不包含已弃用的 `objective_timeout`，既有防回归守卫保留。
- `tests/test_llm_gateway_policy.py` 已由前序任务包含十二字段白名单和秘密/无关字段排除测试，本任务未重复改写。
- `git diff --check` 通过；`git status --short -- user_data` 无输出。

## 关注事项

无已知阻断或范围外泄漏。未执行真实模型请求；P1-25 的四处直连迁移保持未动。
