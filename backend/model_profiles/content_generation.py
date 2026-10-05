"""「内容生成」任务模型配置的解析入口。

设置页按任务绑定模型档案；本模块把档案字段解析成 LLMSettings，
供 AI 组卷/答案草拟/分析报告等所有内容生成调用方共用。
"""

from __future__ import annotations

from backend.llm.api_profiles import get_api_profile_store, resolve_profile_for_task
from backend.llm.policy import policy_overrides_from_profile
from backend.llm.trace import safe_endpoint_host
from backend.llm.llm_client import LLMSettings, normalize_openai_base_url


def resolve_content_generation_settings() -> LLMSettings | None:
    """解析设置页「内容生成」任务当前绑定的模型；api_key/model 缺失即未配置。"""
    profile = resolve_profile_for_task(get_api_profile_store(), "content_generation")
    api_key = str(profile.get("api_key") or "").strip()
    config_api_key = str(profile.get("config_api_key") or api_key).strip()
    config_model = str(profile.get("config_model") or "").strip()
    if not api_key or not config_api_key or not config_model:
        return None
    grading_model = str(profile.get("grading_model") or config_model)
    return LLMSettings(
        api_key=api_key,
        base_url=normalize_openai_base_url(
            str(profile.get("base_url") or "https://api.openai.com/v1")
        ),
        ocr_model=str(profile.get("ocr_model") or grading_model),
        grading_model=grading_model,
        config_model=config_model,
        config_api_key=config_api_key,
        config_base_url=normalize_openai_base_url(
            str(
                profile.get("config_base_url")
                or profile.get("base_url")
                or "https://api.openai.com/v1"
            )
        ),
        policy_profile=policy_overrides_from_profile(profile),
    )


def content_generation_public_info() -> tuple[str | None, str | None]:
    """返回可对外展示的（服务名, 模型名），不含密钥与完整路径。"""
    profile = resolve_profile_for_task(get_api_profile_store(), "content_generation")
    model = str(profile.get("config_model") or "").strip() or None
    profile_name = str(profile.get("name") or "").strip()
    host = safe_endpoint_host(
        profile.get("config_base_url") or profile.get("base_url")
    )
    service = " @ ".join(part for part in (profile_name, host) if part) or None
    return service, model
