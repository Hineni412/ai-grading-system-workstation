from __future__ import annotations

import logging

import base64
import hashlib
import io
import json
import math
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from itertools import count
from typing import Any

from openai import OpenAI
from PIL import Image, ImageOps

from backend.llm import (
    JsonlCallTraceSink,
    JsonlUsageSink,
    LLMGateway,
    LLMRequestKind,
    is_truncation_finish_reason,
    looks_like_truncated_json_object,
    response_diagnostics,
)
from backend.llm.json_repair import parse_json_object_locally
from backend.llm.trace import (
    TRACE_LOG_FILE as LLM_TRACE_LOG_FILE,
)
from backend.llm.trace import (
    safe_endpoint_host,
)
from backend.llm.transport import (
    create_openai_client as _shared_create_openai_client,
)
from backend.llm.transport import (
    gateway_config_key as _shared_gateway_config_key,
)
from backend.llm.transport import (
    normalize_openai_base_url as _shared_normalize_openai_base_url,
)
from backend.llm.usage_logger import LOG_FILE as LLM_USAGE_LOG_FILE


def _default_usage_sink() -> JsonlUsageSink:
    return JsonlUsageSink(LLM_USAGE_LOG_FILE)


def _default_trace_sink() -> JsonlCallTraceSink:
    return JsonlCallTraceSink(LLM_TRACE_LOG_FILE)


@dataclass
class LLMSettings:
    api_key: str
    base_url: str
    ocr_model: str
    grading_model: str
    config_model: str
    config_api_key: str | None = None
    config_base_url: str | None = None
    policy_profile: Mapping[str, object] | None = None


class LLMResponseFormatError(ValueError):
    """The provider returned content that is not a usable JSON object."""


class LLMOutputTruncatedError(ValueError):
    def __init__(
        self,
        *,
        finish_reason: str,
        response_chars: int,
        response_sha256: str,
        provider_reported: bool,
    ) -> None:
        self.finish_reason = str(finish_reason or "")
        self.response_chars = int(response_chars)
        self.response_sha256 = str(response_sha256 or "")
        reason = self.finish_reason or "服务未提供"
        if provider_reported:
            problem = "模型因输出长度上限停止，返回结果不完整"
        else:
            problem = "模型返回的 JSON 结构未闭合，疑似输出被截断"
        super().__init__(
            f"{problem}（停止原因: {reason}；响应字符数: {self.response_chars}；"
            f"响应摘要: {self.response_sha256 or '无'}）。"
            "未发布配置，也未自动重试。请手动重试失败批次；"
            "已经成功的批次不会重复请求。"
        )


class LLMClient:
    def __init__(
        self,
        settings: LLMSettings,
        *,
        gateway_factory=LLMGateway,
        usage_sink_factory=_default_usage_sink,
        trace_sink_factory=_default_trace_sink,
    ) -> None:
        self.settings = settings
        self.client = _create_openai_client(settings.api_key, settings.base_url)
        config_api_key = settings.config_api_key or settings.api_key
        config_base_url = settings.config_base_url or settings.base_url
        gateway_profile = dict(settings.policy_profile or {})
        self.gateway = gateway_factory(
            profile=gateway_profile,
            config_key=_gateway_config_key(settings.api_key, settings.base_url),
            usage_sink=usage_sink_factory(),
            trace_sink=trace_sink_factory(),
            endpoint_host=safe_endpoint_host(settings.base_url),
        )
        if config_api_key == settings.api_key and normalize_openai_base_url(config_base_url) == normalize_openai_base_url(settings.base_url):
            self.config_client = self.client
            self.config_gateway = self.gateway
        else:
            self.config_client = _create_openai_client(config_api_key, config_base_url)
            self.config_gateway = gateway_factory(
                profile=gateway_profile,
                config_key=_gateway_config_key(config_api_key, config_base_url),
                usage_sink=usage_sink_factory(),
                trace_sink=trace_sink_factory(),
                endpoint_host=safe_endpoint_host(config_base_url),
            )

    def text_from_images(self, prompt: str, image_blobs: list[bytes], model: str | None = None, system_prompt: str | None = None) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for blob in image_blobs:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _to_data_url(blob)},
                }
            )

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content})

        completion = self._create_chat_completion(
            self.client,
            model=model or self.settings.ocr_model,
            messages=messages,
            expect_json=False,
            allow_parameter_fallback=False,
            request_kind=LLMRequestKind.RECOGNITION,
            single_request=True,
        )
        return _extract_text_from_completion(completion)

    def json_from_images(
        self,
        prompt: str,
        image_blobs: list[bytes],
        model: str | None = None,
        system_prompt: str | None = None,
        usage_callback=None,
        static_image_blobs: list[bytes] | None = None,
        dynamic_prompt: str | None = None,
        allow_gateway_retry: bool = False,
        request_kind: LLMRequestKind | None = None,
        extra_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.json_from_images_with_options(
            prompt,
            image_blobs,
            model=model,
            system_prompt=system_prompt,
            usage_callback=usage_callback,
            extra_kwargs=extra_kwargs,
            static_image_blobs=static_image_blobs,
            dynamic_prompt=dynamic_prompt,
            allow_gateway_retry=allow_gateway_retry,
            request_kind=request_kind,
        )

    def json_from_images_with_options(
        self,
        prompt: str,
        image_blobs: list[bytes],
        model: str | None = None,
        system_prompt: str | None = None,
        usage_callback=None,
        extra_kwargs: dict[str, Any] | None = None,
        use_config_client: bool = False,
        static_image_blobs: list[bytes] | None = None,
        dynamic_prompt: str | None = None,
        allow_gateway_retry: bool = False,
        request_kind: LLMRequestKind | None = None,
        image_compression_memo: dict[str, bytes] | None = None,
        response_format: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        active_client = self.config_client if use_config_client else self.client
        default_model = self.settings.config_model if use_config_client else self.settings.grading_model
        effective_request_kind = request_kind or (
            LLMRequestKind.CONFIG_GENERATION if use_config_client else LLMRequestKind.GRADING
        )
        active_gateway = None
        allow_retry = allow_gateway_retry
        content: list[dict[str, Any]] = []
        if prompt:
            content.append({"type": "text", "text": prompt})
        if static_image_blobs:
            for blob in static_image_blobs:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": _to_data_url(
                                blob,
                                compression_memo=image_compression_memo,
                            )
                        },
                    }
                )
        if dynamic_prompt:
            content.append({"type": "text", "text": dynamic_prompt})
        if image_blobs:
            for blob in image_blobs:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": _to_data_url(
                                blob,
                                compression_memo=image_compression_memo,
                            )
                        },
                    }
                )

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content})

        completion = self._create_chat_completion(
            active_client,
            model=model or default_model,
            messages=messages,
            expect_json=True,
            usage_callback=usage_callback,
            extra_kwargs=extra_kwargs,
            response_format=response_format,
            allow_parameter_fallback=False,
            request_kind=effective_request_kind,
            single_request=True,
            allow_gateway_retry=allow_retry,
            gateway=active_gateway,
        )
        return _parse_single_request_json(completion)

    def json_from_text(
        self,
        prompt: str,
        model: str | None = None,
        extra_kwargs: dict[str, Any] | None = None,
        response_format: Mapping[str, Any] | None = None,
        *,
        request_kind: LLMRequestKind = LLMRequestKind.CONFIG_GENERATION,
    ) -> dict[str, Any]:
        effective_request_kind = LLMRequestKind(request_kind)
        active_client = self.config_client
        active_gateway = None
        default_model = self.settings.config_model
        allow_retry = False
        completion = self._create_chat_completion(
            active_client,
            model=model or default_model,
            messages=[{"role": "user", "content": prompt}],
            expect_json=True,
            extra_kwargs=extra_kwargs,
            response_format=response_format,
            allow_parameter_fallback=False,
            request_kind=effective_request_kind,
            single_request=True,
            allow_gateway_retry=allow_retry,
            gateway=active_gateway,
        )
        return _parse_single_request_json(completion)

    def json_from_text_once(
        self,
        prompt: str,
        model: str | None = None,
        extra_kwargs: dict[str, Any] | None = None,
        response_format: Mapping[str, Any] | None = None,
        *,
        request_kind: LLMRequestKind = LLMRequestKind.CONFIG_GENERATION,
    ) -> dict[str, Any]:
        """Make exactly one model request and parse JSON locally without AI repair."""
        strict_kwargs = dict(extra_kwargs or {})
        strict_kwargs.pop("omit_token_limit", None)
        effective_request_kind = LLMRequestKind(request_kind)
        active_client = self.config_client
        active_gateway = None
        default_model = self.settings.config_model
        allow_retry = False
        completion = self._create_chat_completion(
            active_client,
            model=model or default_model,
            messages=[{"role": "user", "content": prompt}],
            expect_json=False,
            extra_kwargs=strict_kwargs,
            response_format=response_format,
            allow_parameter_fallback=False,
            request_kind=effective_request_kind,
            single_request=True,
            allow_gateway_retry=allow_retry,
            gateway=active_gateway,
        )
        return _parse_single_request_json(completion)

    def json_from_images_once(
        self,
        prompt: str,
        image_blobs: list[bytes],
        model: str | None = None,
        system_prompt: str | None = None,
        extra_kwargs: dict[str, Any] | None = None,
        use_config_client: bool = False,
        static_image_blobs: list[bytes] | None = None,
        dynamic_prompt: str | None = None,
        usage_callback=None,
    ) -> dict[str, Any]:
        """Make exactly one visual model request and parse JSON locally without AI repair."""
        active_client = self.config_client if use_config_client else self.client
        default_model = self.settings.config_model if use_config_client else self.settings.grading_model
        request_kind = (
            LLMRequestKind.CONFIG_GENERATION
            if use_config_client
            else LLMRequestKind.GRADING
        )
        strict_kwargs = dict(extra_kwargs or {})
        strict_kwargs.pop("omit_token_limit", None)
        active_gateway = None
        allow_retry = False

        content: list[dict[str, Any]] = []
        if prompt:
            content.append({"type": "text", "text": prompt})
        if static_image_blobs:
            for blob in static_image_blobs:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": _to_data_url(blob)},
                    }
                )
        if dynamic_prompt:
            content.append({"type": "text", "text": dynamic_prompt})
        if image_blobs:
            for blob in image_blobs:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": _to_data_url(blob)},
                    }
                )
                
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content})
        completion = self._create_chat_completion(
            active_client,
            model=model or default_model,
            messages=messages,
            expect_json=False,
            usage_callback=usage_callback,
            extra_kwargs=strict_kwargs,
            allow_parameter_fallback=False,
            request_kind=request_kind,
            single_request=True,
            allow_gateway_retry=allow_retry,
            gateway=active_gateway,
        )
        return _parse_single_request_json(completion)

    def _parse_or_repair_json(
        self,
        text: str,
        model: str,
        retry_messages: list[dict[str, Any]] | None = None,
        client: OpenAI | None = None,
        usage_callback = None,
        extra_kwargs: dict[str, Any] | None = None,
        response_format: Mapping[str, Any] | None = None,
        request_kind: LLMRequestKind = LLMRequestKind.GRADING,
        request_id: str | None = None,
        _next_attempt: Callable[[], int] | None = None,
    ) -> dict[str, Any]:
        """Parse a response locally; retained for compatibility with old callers.

        A user action must never trigger an unannounced second model request.  The
        former implementation asked the model to regenerate or repair malformed
        JSON.  Deterministic local repair is now the only permitted recovery.
        """
        try:
            return parse_json_object_locally(text).payload
        except ValueError as exc:
            if _looks_truncated_json(text):
                response_chars, response_sha256 = _safe_output_summary(text)
                raise LLMOutputTruncatedError(
                    finish_reason="",
                    response_chars=response_chars,
                    response_sha256=response_sha256,
                    provider_reported=False,
                ) from exc
            raise LLMResponseFormatError(str(exc)) from exc

    def _create_chat_completion(
        self,
        client: OpenAI,
        model: str,
        messages: list[dict[str, Any]],
        expect_json: bool,
        usage_callback = None,
        extra_kwargs: dict[str, Any] | None = None,
        response_format: Mapping[str, Any] | None = None,
        *,
        allow_parameter_fallback: bool = False,
        request_kind: LLMRequestKind = LLMRequestKind.GRADING,
        request_id: str | None = None,
        single_request: bool = False,
        _next_attempt: Callable[[], int] | None = None,
        allow_gateway_retry: bool = False,
        gateway: Any = None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": model,
            "temperature": 0,
            "messages": messages,
        }
        if not (extra_kwargs and extra_kwargs.get("omit_token_limit")):
            kwargs["max_tokens"] = 32000
        if extra_kwargs and "timeout" in extra_kwargs:
            kwargs["timeout"] = extra_kwargs.get("timeout")
        timeout_override_seconds = (
            extra_kwargs.get("timeout_override_seconds")
            if extra_kwargs
            else None
        )
        if response_format is not None:
            kwargs["response_format"] = dict(response_format)
        elif expect_json:
            kwargs["response_format"] = {"type": "json_object"}
            
        if extra_kwargs and extra_kwargs.get("thinking"):
            model_lower = str(model).lower()
            is_openai_reasoning = any(x in model_lower for x in ["o1", "o3"])
            is_r1_reasoning = any(x in model_lower for x in ["r1", "reasoner"])
            
            if is_openai_reasoning:
                kwargs.pop("temperature", None)
                kwargs.pop("max_tokens", None)
                kwargs["max_completion_tokens"] = 32000
                kwargs["reasoning_effort"] = "medium"
            elif is_r1_reasoning:
                kwargs["temperature"] = 0.6
                
        if gateway is None:
            gateway = (
                self.config_gateway
                if client is self.config_client
                else self.gateway
            )
        logical_request_id = str(
            uuid.uuid4() if request_id is None else request_id
        )
        next_attempt = _next_attempt or count(1).__next__

        def invoke(
            compatibility_fallback: str,
            *,
            allow_retry: bool,
            planned_parameter_fallback: bool,
        ) -> Any:
            res = gateway.chat_completions(
                request_kind=request_kind,
                client=client,
                model=model,
                kwargs=kwargs,
                request_id=logical_request_id,
                allow_retry=allow_retry,
                compatibility_fallback=compatibility_fallback,
                planned_parameter_fallback=planned_parameter_fallback,
                timeout_override_seconds=timeout_override_seconds,
                _next_attempt=next_attempt,
            )
            if usage_callback:
                try:
                    usage_callback(res, kwargs)
                except Exception as exc:
                    # Usage reporting must not invalidate a completed model response.
                    logging.getLogger(__name__).warning(
                        "usage callback failed (%s)", type(exc).__name__,
                    )
            return res

        # Compatibility flags remain in the private signature while older
        # callers migrate, but they can no longer authorize another physical
        # request. Retries are owned by explicit user-level workflows only.
        return invoke(
            "",
            allow_retry=allow_gateway_retry,
            planned_parameter_fallback=False,
        )


def _create_openai_client(api_key: str, base_url: str) -> OpenAI:
    return _shared_create_openai_client(api_key, base_url)


def _gateway_config_key(api_key: str, base_url: str) -> str:
    return _shared_gateway_config_key(api_key, base_url)


def _to_data_url(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    *,
    compression_memo: dict[str, bytes] | None = None,
) -> str:
    if compression_memo is None:
        compressed = _compress_image_for_api(image_bytes)
    else:
        cache_key = hashlib.sha256(image_bytes).hexdigest()
        compressed = compression_memo.get(cache_key)
        if compressed is None:
            compressed = _compress_image_for_api(image_bytes)
            compression_memo[cache_key] = compressed
    encoded = base64.b64encode(compressed).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def normalize_openai_base_url(base_url: str) -> str:
    return _shared_normalize_openai_base_url(base_url)


def _compress_image_for_api(image_bytes: bytes) -> bytes:
    """Resize large local images before sending them to vision APIs."""
    max_pixels = _env_int("AI_GRADING_IMAGE_MAX_PIXELS", 24_000_000)
    max_bytes = _env_int("AI_GRADING_IMAGE_MAX_BYTES", 4_000_000)
    min_quality = _env_int("AI_GRADING_IMAGE_MIN_QUALITY", 62)
    start_quality = _env_int("AI_GRADING_IMAGE_QUALITY", 86)

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGB")
            width, height = image.size
            total_pixels = width * height
            if total_pixels > max_pixels:
                scale = math.sqrt(max_pixels / total_pixels)
                new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
                image = image.resize(new_size, Image.Resampling.LANCZOS)

            quality = max(min(start_quality, 95), min_quality)
            best_bytes: bytes | None = None
            while quality >= min_quality:
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
                compressed = buffer.getvalue()
                best_bytes = compressed
                if len(compressed) <= max_bytes or quality <= min_quality:
                    return compressed
                quality -= 6

            return best_bytes or image_bytes
    except (OSError, ValueError, Image.DecompressionBombError):
        return image_bytes


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _extract_text_from_completion(completion: Any) -> str:
    if isinstance(completion, str):
        return completion.strip()

    # OpenAI SDK typed object
    if hasattr(completion, "choices"):
        choices = getattr(completion, "choices") or []
        if choices:
            message = getattr(choices[0], "message", None)
            if message is not None:
                content = getattr(message, "content", None)
                text = _normalize_message_content(content)
                if text:
                    return text

    # Some providers return dict payload
    if isinstance(completion, dict):
        choices = completion.get("choices") or []
        if choices:
            message = choices[0].get("message", {})
            text = _normalize_message_content(message.get("content"))
            if text:
                return text
        if isinstance(completion.get("output_text"), str):
            return completion["output_text"].strip()

    # Responses API style object fallback
    if hasattr(completion, "output_text") and isinstance(getattr(completion, "output_text"), str):
        return getattr(completion, "output_text").strip()

    if hasattr(completion, "model_dump"):
        dumped = completion.model_dump()
        if isinstance(dumped, dict):
            return _extract_text_from_completion(dumped)

    raise ValueError(f"无法从模型响应中提取文本，响应类型: {type(completion).__name__}")


def _normalize_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()

    # content may be list like [{"type":"text","text":"..."}]
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                    continue
                if isinstance(item.get("content"), str):
                    parts.append(item["content"])
                    continue
            if hasattr(item, "text") and isinstance(getattr(item, "text"), str):
                parts.append(getattr(item, "text"))
        return "\n".join([p for p in parts if p]).strip()

    return str(content).strip()


def _with_compact_json_instruction(content: Any) -> Any:
    instruction = (
        "\n\n输出约束升级：必须只输出一个完整、严格、压缩的 JSON 对象。"
        "不要 markdown；不要解释；不要前后缀文字；不要漂亮打印；不要在字符串中换行；"
        "所有数组和对象必须闭合；如果内容很长，也必须完整输出到最后一个右花括号。"
    )
    if isinstance(content, str):
        return content + instruction
    if isinstance(content, list) and content:
        copied: list[Any] = []
        injected = False
        for item in content:
            if isinstance(item, dict):
                next_item = dict(item)
                if not injected and next_item.get("type") == "text" and isinstance(next_item.get("text"), str):
                    next_item["text"] = next_item["text"] + instruction
                    injected = True
                copied.append(next_item)
            else:
                copied.append(item)
        if not injected:
            copied.insert(0, {"type": "text", "text": instruction.strip()})
        return copied
    return str(content) + instruction


def _parse_json_text(text: str) -> dict[str, Any]:
    cleaned = _clean_json_text(text)
    candidates = [cleaned]
    extracted = _extract_first_json_object(cleaned)
    if extracted and extracted != cleaned:
        candidates.append(extracted)

    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            parsed = json.loads(_remove_trailing_commas(candidate))
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if not isinstance(parsed, dict):
            raise ValueError("模型返回 JSON 顶层必须为对象")
        return parsed

    detail = ""
    if last_error is not None:
        detail = f"解析位置 line {last_error.lineno}, col {last_error.colno}: {last_error.msg}。"
    response_chars, response_sha256 = _safe_output_summary(text)
    raise ValueError(
        f"模型返回非 JSON，无法解析。{detail}"
        f"响应字符数: {response_chars}；响应摘要: {response_sha256 or '无'}。"
    )


def _parse_single_request_json(completion: Any) -> dict[str, Any]:
    diagnostics = response_diagnostics(completion)
    if diagnostics["output_truncated"]:
        raise LLMOutputTruncatedError(
            finish_reason=str(diagnostics["finish_reason"]),
            response_chars=int(diagnostics["response_chars"]),
            response_sha256=str(diagnostics["response_sha256"]),
            provider_reported=is_truncation_finish_reason(
                diagnostics["finish_reason"]
            ),
        )
    text = _extract_text_from_completion(completion)
    try:
        parsed = parse_json_object_locally(text)
    except ValueError as exc:
        if not _looks_truncated_json(text):
            raise LLMResponseFormatError(str(exc)) from exc
        raise LLMOutputTruncatedError(
            finish_reason=str(diagnostics["finish_reason"]),
            response_chars=int(diagnostics["response_chars"]),
            response_sha256=str(diagnostics["response_sha256"]),
            provider_reported=False,
        ) from exc
    if parsed.report.repaired:
        meta = parsed.payload.setdefault("meta", {})
        if not isinstance(meta, dict):
            raise ValueError("模型返回 JSON 的 meta 必须为对象")
        meta["local_json_repair"] = {
            "repaired": True,
            "operations": list(parsed.report.operations),
            "response_chars": parsed.report.response_chars,
            "response_sha256": parsed.report.response_sha256,
        }
    return parsed.payload


def _safe_output_summary(text: str) -> tuple[int, str]:
    normalized = str(text or "")
    return (
        len(normalized),
        hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if normalized
        else "",
    )


def _clean_json_text(text: str) -> str:
    cleaned = str(text or "").strip().lstrip("\ufeff")
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    if cleaned.lower().startswith("json\n"):
        cleaned = cleaned[5:].strip()
    return cleaned


def _extract_first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        char = text[idx]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:idx + 1].strip()
    return None


def _remove_trailing_commas(text: str) -> str:
    result: list[str] = []
    in_string = False
    escape = False
    idx = 0
    while idx < len(text):
        char = text[idx]
        if in_string:
            result.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            idx += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            idx += 1
            continue
        if char == ",":
            lookahead = idx + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "}]":
                idx += 1
                continue
        result.append(char)
        idx += 1
    return "".join(result)


def _looks_truncated_json(text: str) -> bool:
    return looks_like_truncated_json_object(text)
