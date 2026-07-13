from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import uuid
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlparse

from backend.llm import LLMGateway, LLMRequestKind
from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError
from PIL import Image, ImageOps


_GATEWAY_CONFIG_SALT = "ai-grading-llm-gateway-config-v1"


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


class LLMClient:
    def __init__(self, settings: LLMSettings, *, gateway_factory=LLMGateway) -> None:
        self.settings = settings
        self.client = _create_openai_client(settings.api_key, settings.base_url)
        config_api_key = settings.config_api_key or settings.api_key
        config_base_url = settings.config_base_url or settings.base_url
        gateway_profile = dict(settings.policy_profile or {})
        self.gateway = gateway_factory(
            profile=gateway_profile,
            config_key=_gateway_config_key(settings.api_key, settings.base_url),
        )
        if config_api_key == settings.api_key and normalize_openai_base_url(config_base_url) == normalize_openai_base_url(settings.base_url):
            self.config_client = self.client
            self.config_gateway = self.gateway
        else:
            self.config_client = _create_openai_client(config_api_key, config_base_url)
            self.config_gateway = gateway_factory(
                profile=gateway_profile,
                config_key=_gateway_config_key(config_api_key, config_base_url),
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
            request_kind=LLMRequestKind.RECOGNITION,
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
    ) -> dict[str, Any]:
        return self.json_from_images_with_options(
            prompt,
            image_blobs,
            model=model,
            system_prompt=system_prompt,
            usage_callback=usage_callback,
            extra_kwargs=None,
            static_image_blobs=static_image_blobs,
            dynamic_prompt=dynamic_prompt,
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
    ) -> dict[str, Any]:
        active_client = self.config_client if use_config_client else self.client
        default_model = self.settings.config_model if use_config_client else self.settings.grading_model
        request_kind = (
            LLMRequestKind.CONFIG_GENERATION
            if use_config_client
            else LLMRequestKind.GRADING
        )
        request_id = str(uuid.uuid4())
        
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
            request_kind=request_kind,
            request_id=request_id,
        )
        text = _extract_text_from_completion(completion)
        
        retry_messages = []
        if system_prompt:
            retry_messages.append({"role": "system", "content": system_prompt})
        retry_messages.append({"role": "user", "content": _with_compact_json_instruction(content)})
        
        return self._parse_or_repair_json(
            text,
            model=model or default_model,
            retry_messages=retry_messages,
            client=active_client,
            usage_callback=usage_callback,
            extra_kwargs=extra_kwargs,
            request_kind=request_kind,
            request_id=request_id,
        )

    def json_from_text(self, prompt: str, model: str | None = None, extra_kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        completion = self._create_chat_completion(
            self.config_client,
            model=model or self.settings.config_model,
            messages=[{"role": "user", "content": prompt}],
            expect_json=True,
            extra_kwargs=extra_kwargs,
            request_kind=LLMRequestKind.CONFIG_GENERATION,
            request_id=request_id,
        )
        text = _extract_text_from_completion(completion)
        retry_messages = [{"role": "user", "content": _with_compact_json_instruction(prompt)}]
        return self._parse_or_repair_json(
            text,
            model=model or self.settings.config_model,
            retry_messages=retry_messages,
            client=self.config_client,
            extra_kwargs=extra_kwargs,
            request_kind=LLMRequestKind.CONFIG_GENERATION,
            request_id=request_id,
        )

    def json_from_text_once(
        self,
        prompt: str,
        model: str | None = None,
        extra_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make exactly one model request and parse JSON locally without AI repair."""
        strict_kwargs = dict(extra_kwargs or {})
        strict_kwargs["omit_token_limit"] = True
        completion = self._create_chat_completion(
            self.config_client,
            model=model or self.settings.config_model,
            messages=[{"role": "user", "content": prompt}],
            expect_json=False,
            extra_kwargs=strict_kwargs,
            allow_parameter_fallback=False,
            request_kind=LLMRequestKind.CONFIG_GENERATION,
            single_request=True,
        )
        return _parse_json_text(_extract_text_from_completion(completion))

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
        strict_kwargs["omit_token_limit"] = True
        
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
            extra_kwargs=strict_kwargs,
            allow_parameter_fallback=False,
            request_kind=request_kind,
            single_request=True,
        )
        return _parse_json_text(_extract_text_from_completion(completion))

    def _parse_or_repair_json(
        self,
        text: str,
        model: str,
        retry_messages: list[dict[str, Any]] | None = None,
        client: OpenAI | None = None,
        usage_callback = None,
        extra_kwargs: dict[str, Any] | None = None,
        request_kind: LLMRequestKind = LLMRequestKind.GRADING,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        active_client = client or self.client
        logical_request_id = str(
            uuid.uuid4() if request_id is None else request_id
        )
        try:
            return _parse_json_text(text)
        except ValueError as first_error:
            if _looks_truncated_json(text):
                if not retry_messages:
                    raise first_error
                completion = self._create_chat_completion(
                    active_client,
                    model=model,
                    messages=retry_messages
                    + [
                        {
                            "role": "user",
                            "content": (
                                "上一轮 JSON 输出被截断，导致无法解析。请重新生成完整结果。"
                                "必须只输出一个完整、严格、压缩的 JSON 对象；不要 markdown；"
                                "不要解释；不要省略任何题目；不要在字符串中换行。"
                            ),
                        }
                    ],
                    expect_json=True,
                    usage_callback=usage_callback,
                    extra_kwargs=extra_kwargs,
                    request_kind=request_kind,
                    request_id=logical_request_id,
                )
                retried_text = _extract_text_from_completion(completion)
                try:
                    return _parse_json_text(retried_text)
                except ValueError as retried_error:
                    if _looks_truncated_json(retried_text):
                        raise ValueError(
                            "模型连续两次输出被截断，无法得到完整 JSON。"
                            "建议换用更大输出上限的模型，或减少单次生成内容。"
                            f"\n首次错误：{first_error}\n重试错误：{retried_error}"
                        ) from retried_error
                    raise ValueError(f"{first_error}\n截断后重试仍失败：{retried_error}") from retried_error
            repair_prompt = (
                "下面是一段模型输出，目标是把它转换为严格 JSON 对象。\n"
                "要求：只输出 JSON；不要 markdown；不要解释；不要新增题目或改写内容；"
                "只修复代码块、前后多余文字、尾逗号、转义等格式问题。\n\n"
                f"原始输出：\n{text}"
            )
            completion = self._create_chat_completion(
                active_client,
                model=model,
                messages=[{"role": "user", "content": repair_prompt}],
                expect_json=True,
                usage_callback=usage_callback,
                extra_kwargs=extra_kwargs,
                request_kind=request_kind,
                request_id=logical_request_id,
            )
            repaired_text = _extract_text_from_completion(completion)
            try:
                return _parse_json_text(repaired_text)
            except ValueError as repaired_error:
                raise ValueError(f"{first_error}\nJSON 修复重试仍失败：{repaired_error}") from repaired_error

    def _create_chat_completion(
        self,
        client: OpenAI,
        model: str,
        messages: list[dict[str, Any]],
        expect_json: bool,
        usage_callback = None,
        extra_kwargs: dict[str, Any] | None = None,
        *,
        allow_parameter_fallback: bool = True,
        request_kind: LLMRequestKind = LLMRequestKind.GRADING,
        request_id: str | None = None,
        single_request: bool = False,
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
        if expect_json:
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
                
        gateway = (
            self.config_gateway
            if client is self.config_client
            else self.gateway
        )

        def invoke(
            compatibility_fallback: str,
            *,
            allow_retry: bool,
        ) -> Any:
            res = gateway.chat_completions(
                request_kind=request_kind,
                client=client,
                model=model,
                kwargs=kwargs,
                request_id=request_id,
                allow_retry=allow_retry,
                compatibility_fallback=compatibility_fallback,
            )
            if usage_callback:
                try: usage_callback(res, kwargs)
                except: pass
            return res

        try:
            return invoke("", allow_retry=not single_request)
        except Exception as exc:
            if not allow_parameter_fallback:
                raise
            if not _is_parameter_fallback_error(exc):
                raise
            if "max_tokens" in kwargs:
                kwargs.pop("max_tokens", None)
                kwargs["max_completion_tokens"] = 32000
                try:
                    return invoke(
                        "max_completion_tokens",
                        allow_retry=False,
                    )
                except Exception as retry_exc:
                    if not _is_parameter_fallback_error(retry_exc):
                        raise
                    kwargs.pop("max_completion_tokens", None)
            if expect_json:
                kwargs.pop("response_format", None)
                return invoke("response_format", allow_retry=False)
            raise exc


def _is_parameter_fallback_error(exc: Exception) -> bool:
    if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError)):
        return False
    message = str(exc).lower()
    parameter_markers = (
        "max_tokens",
        "max_completion_tokens",
        "response_format",
        "json_object",
        "unsupported parameter",
        "unknown parameter",
        "unrecognized request argument",
        "not supported",
        "extra_forbidden",
    )
    if any(marker in message for marker in parameter_markers):
        return True
    status_code = getattr(exc, "status_code", None)
    return status_code in {400, 422}


def _create_openai_client(api_key: str, base_url: str) -> OpenAI:
    normalized_base_url = normalize_openai_base_url(base_url)
    kwargs: dict[str, Any] = {
        "api_key": str(api_key or "").strip(),
        "timeout": 120.0,
        "max_retries": 0,
    }
    if normalized_base_url:
        kwargs["base_url"] = normalized_base_url
    return OpenAI(**kwargs)


def _gateway_config_key(api_key: str, base_url: str) -> str:
    material = "\0".join(
        (
            _GATEWAY_CONFIG_SALT,
            str(api_key or "").strip(),
            normalize_openai_base_url(base_url),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _to_data_url(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    image_bytes = _compress_image_for_api(image_bytes)
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def normalize_openai_base_url(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        return value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return value
    path = parsed.path.strip("/")
    if path:
        return value
    return f"{value}/v1"


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
    except Exception:
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
    raise ValueError(f"模型返回非 JSON，无法解析。{detail}原始输出: {text[:3000]}")


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
    cleaned = _clean_json_text(text)
    if not cleaned:
        return False
    if _extract_first_json_object(cleaned):
        return False
    return "{" in cleaned and not cleaned.rstrip().endswith("}")
