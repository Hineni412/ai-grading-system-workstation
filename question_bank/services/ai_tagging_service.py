from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from api_profiles import get_api_profile_store, resolve_profile_for_task
from backend.llm import (
    LLMProtocolAdapter,
    LLMRequestKind,
    execution_snapshot_from_profile,
    policy_overrides_from_profile,
)
from llm_client import (
    LLMClient,
    LLMOutputTruncatedError,
    LLMResponseFormatError,
    LLMSettings,
    normalize_openai_base_url,
)
from question_bank.models.tag_schema import (
    DIFFICULTY_SCALE_GUIDANCE,
    MAX_ABILITY_TAGS,
    PART_CONTEXT_KINDS,
    PART_FEATURE_ORDER,
    PART_FEATURE_RANGES,
    PREDICTED_PATTERN_MAX,
    PREDICTED_TRIGGER_KINDS,
    TagAnalysis,
    TaggingContext,
    predicted_pattern_categories,
)
from question_bank.services.taxonomy_review_suggestions import (
    TaxonomySuggestionModelResponseError,
)
from question_bank.taxonomy.governance import get_taxonomy_governance
from question_bank.taxonomy.snapshot import QuestionTaxonomySnapshot


DEFAULT_TAGGING_MODEL = "gpt-4o"
_TAGGING_REASONING_INSTRUCTION = (
    "Use deliberate reasoning before selecting tags. First read the question and "
    "reference answer carefully. Analyze the underlying junior-middle-school "
    "mathematics knowledge being assessed, the key auxiliary construction and "
    "solution steps, any classic mathematical model involved (for example, "
    "midpoint or hand-in-hand models), and the typical errors students are likely "
    "to make, such as omitted conditions or confused concepts. After completing "
    "this mathematical reasoning and diagnostic analysis, select the final tags "
    "according to the schema. Every tag must agree with that analysis; do not "
    "invent, omit, or misclassify tags."
)
@dataclass(frozen=True)
class AITaggingResult:
    ok: bool
    mock_mode: bool
    analysis: TagAnalysis | None = None
    error: str | None = None
    model_name: str | None = None
    quality_status: str = "complete"
    quality_notes: list[str] = field(default_factory=list)
    proposals: list[dict[str, Any]] = field(default_factory=list)
    retrieval_misses: list[dict[str, Any]] = field(default_factory=list)
    taxonomy_revision: int = 0


@dataclass(frozen=True, slots=True)
class TaggingRequestEvent:
    request_number: int
    request_kind: str
    question_ids: tuple[int, ...]
    phase: str = "started"  # started | succeeded | failed
    error_category: str = ""
    error_message: str = ""
    model_name: str = ""


# 打标请求错误分类，供页面按类别展示与决定是否重试。
_TAGGING_ERROR_CATEGORIES = (
    "rate_limit",
    "timeout",
    "network",
    "parse",
    "validation",
    "quality",
    "save",
    "unknown",
)

_SENSITIVE_TOKEN_RE = re.compile(
    r"(api[_-]?key|authorization|bearer|token)\s*[:=]?\s*[^\s,;'\"]+",
    re.IGNORECASE,
)


def classify_tagging_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__} {exc}".lower()
    if "rate limit" in text or "429" in text or "too many requests" in text:
        return "rate_limit"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "connection" in text or "network" in text or "unreachable" in text or "dns" in text:
        return "network"
    if "json" in text or "parse" in text or "decode" in text:
        return "parse"
    if "validation" in text or "schema" in text or "did not include" in text or "missing" in text:
        return "validation"
    if "quality" in text or "low confidence" in text or "invalid" in text:
        return "quality"
    if "save" in text or "database" in text or "sqlite" in text or "constraint" in text:
        return "save"
    return "unknown"


def sanitize_tagging_error(message: object, *, limit: int = 500) -> str:
    text = str(message or "")
    text = _SENSITIVE_TOKEN_RE.sub(lambda m: f"{m.group(1)}=***", text)
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


COMPLETE_CONFIDENCE_THRESHOLD = 0.72
REVIEW_CONFIDENCE_THRESHOLD = 0.45


class AITaggingService:
    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        client: Any | None = None,
        llm_client: Any | None = None,
        protocol_adapter: Any | None = None,
        taxonomy_governance: Any | None = None,
    ) -> None:
        self.env = dict(_dotenv_values())
        self.env.update(dict(os.environ if env is None else env))
        self.api_key = str(self.env.get("QUESTION_BANK_TAGGING_API_KEY") or self.env.get("OPENAI_API_KEY") or "").strip()
        self.model = str(self.env.get("QUESTION_BANK_TAGGING_MODEL") or DEFAULT_TAGGING_MODEL).strip()
        self.client = client
        self._protocol_adapter_instance = protocol_adapter
        self._protocol_adapter_lock = threading.Lock()
        self._tagging_base_url = str(
            self.env.get("QUESTION_BANK_TAGGING_BASE_URL")
            or self.env.get("LLM_BASE_URL")
            or "https://api.openai.com/v1"
        ).strip()
        self._tagging_policy_profile: dict[str, object] = {}
        self._taxonomy_governance = taxonomy_governance
        self._frozen_taxonomy_contract: dict[str, Any] | None = None
        self.review_model = str(self.env.get("QUESTION_BANK_TAGGING_REVIEW_MODEL") or "").strip()
        self.review_llm_client: LLMClient | None = None

        tagging_api_key = str(self.env.get("QUESTION_BANK_TAGGING_API_KEY") or "").strip()
        direct_injection = client is not None or protocol_adapter is not None
        if direct_injection and env is None and not self.api_key:
            profile = _active_saved_profile()
            self.api_key = str(
                profile.get("config_api_key") or profile.get("api_key") or ""
            ).strip()
            self.model = str(profile.get("config_model") or self.model).strip()
            self._tagging_base_url = str(
                profile.get("config_base_url")
                or profile.get("base_url")
                or self._tagging_base_url
            ).strip()
            self._tagging_policy_profile = policy_overrides_from_profile(profile)

        if direct_injection:
            self.llm_client = None
        elif tagging_api_key:
            dedicated_base_url = str(
                self.env.get("QUESTION_BANK_TAGGING_BASE_URL")
                or "https://api.openai.com/v1"
            ).strip()
            settings = LLMSettings(
                api_key=tagging_api_key,
                base_url=dedicated_base_url,
                ocr_model=self.model,
                grading_model=self.model,
                config_model=self.model,
                config_api_key=tagging_api_key,
                config_base_url=dedicated_base_url,
            )
            self.llm_client = LLMClient(settings)
        elif llm_client is not None:
            self.llm_client = llm_client
        else:
            self.llm_client = _llm_client_from_saved_profile(self.env) if env is None else None
        if self.llm_client is not None:
            saved_settings = getattr(self.llm_client, "settings", None)
            saved_model = str(getattr(saved_settings, "config_model", "") or "").strip()
            if saved_model:
                self.model = saved_model
            saved_policy_profile = getattr(saved_settings, "policy_profile", None)
            if isinstance(saved_policy_profile, Mapping):
                self._tagging_policy_profile = dict(saved_policy_profile)
        self._configure_review_client(tagging_api_key=str(self.env.get("QUESTION_BANK_TAGGING_API_KEY") or "").strip())

    @property
    def taxonomy_governance(self) -> Any:
        if self._taxonomy_governance is None:
            self._taxonomy_governance = get_taxonomy_governance()
        return self._taxonomy_governance

    @property
    def taxonomy_revision(self) -> int:
        contract = self._frozen_taxonomy_contract or self.taxonomy_contract()
        return _taxonomy_revision(contract)

    def freeze_taxonomy(self, contract: Mapping[str, Any] | None = None) -> int:
        resolved = dict(contract or self.taxonomy_governance.prompt_contract())
        self._frozen_taxonomy_contract = resolved
        return _taxonomy_revision(resolved)

    def taxonomy_contract(self, context: TaggingContext | None = None) -> dict[str, Any]:
        if self._frozen_taxonomy_contract is not None:
            return self._frozen_taxonomy_contract
        prompt_context = _taxonomy_context(context) if context is not None else None
        return dict(self.taxonomy_governance.prompt_contract(prompt_context))

    def taxonomy_contracts(
        self,
        contexts: Mapping[int, TaggingContext],
    ) -> dict[int, dict[str, Any]]:
        if self._frozen_taxonomy_contract is not None:
            return {
                question_id: dict(self._frozen_taxonomy_contract)
                for question_id in contexts
            }
        prompt_contexts = {
            question_id: _taxonomy_context(context)
            for question_id, context in contexts.items()
        }
        planner = getattr(self.taxonomy_governance, "prompt_contracts", None)
        if callable(planner):
            planned = planner(prompt_contexts)
            return {
                int(question_id): dict(contract)
                for question_id, contract in planned.items()
                if isinstance(contract, Mapping)
            }
        return {
            question_id: self.taxonomy_contract(context)
            for question_id, context in contexts.items()
        }

    @property
    def mock_mode(self) -> bool:
        return not bool(
            self.api_key
            or self.llm_client
            or self.client
            or self._protocol_adapter_instance
        )

    def analyze_question(
        self,
        context: TaggingContext,
        *,
        taxonomy_contract: Mapping[str, Any] | None = None,
        images: Sequence[Any] = (),
    ) -> AITaggingResult:
        taxonomy_contract = dict(
            taxonomy_contract or self.taxonomy_contract(context)
        )
        images = _normalize_question_images(images)
        if self.mock_mode:
            return _with_quality(
                AITaggingResult(
                    ok=True,
                    mock_mode=True,
                    analysis=_mock_analysis(context),
                ),
                context,
                governance=self.taxonomy_governance,
                taxonomy_contract=taxonomy_contract,
            )
        try:
            if self.llm_client is not None:
                thinking_enabled = os.getenv("QUESTION_BANK_TAGGING_THINKING") == "1"
                extra_kwargs = {"thinking": True} if thinking_enabled else None
                response_format = _chat_response_format(
                    _tag_analysis_response_format(taxonomy_contract)
                )
                if images:
                    payload = _json_from_images_compat(
                        self.llm_client,
                        _prompt_text(context, taxonomy_contract),
                        [image.content for image in images],
                        model=_model_for_llm_client(
                            self.llm_client, self.model
                        ),
                        extra_kwargs=extra_kwargs,
                        response_format=response_format,
                    )
                else:
                    payload = _json_from_text_compat(
                        self.llm_client,
                        _prompt_text(context, taxonomy_contract),
                        model=_model_for_llm_client(
                            self.llm_client, self.model
                        ),
                        extra_kwargs=extra_kwargs,
                        response_format=response_format,
                    )
                result = AITaggingResult(
                    ok=True,
                    mock_mode=False,
                    analysis=TagAnalysis.from_dict(payload),
                    model_name=_model_for_llm_client(self.llm_client, self.model),
                )
                return _with_quality(
                    result,
                    context,
                    governance=self.taxonomy_governance,
                    taxonomy_contract=taxonomy_contract,
                )
            response = self._protocol_adapter().responses(
                request_kind=LLMRequestKind.TAGGING,
                model=self.model,
                kwargs={
                    "text": {
                        "format": _tag_analysis_response_format(
                            taxonomy_contract
                        )
                    },
                    "input": _prompt_input(
                        context, taxonomy_contract, images=images
                    ),
                },
            )
            output_text = str(getattr(response, "output_text", "") or "").strip()
            result = AITaggingResult(
                ok=True,
                mock_mode=False,
                analysis=TagAnalysis.from_dict(_loads_model_json(output_text)),
                model_name=self.model,
            )
            return _with_quality(
                result,
                context,
                governance=self.taxonomy_governance,
                taxonomy_contract=taxonomy_contract,
            )
        except Exception as exc:  # noqa: BLE001
            return AITaggingResult(ok=False, mock_mode=False, error=str(exc), model_name=self.model, quality_status="invalid", quality_notes=[str(exc)])

    def suggest_taxonomy_reviews(
        self,
        batch: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Ask for review suggestions without applying any taxonomy decision."""

        if self.mock_mode:
            raise RuntimeError(
                "未配置可用于词表归并建议的大模型，未生成模拟判断。"
            )
        prompt_input = _taxonomy_suggestion_prompt_input(batch)
        if self.llm_client is not None and not (
            self.api_key
            or self.client
            or self._protocol_adapter_instance is not None
        ):
            prompt = "\n\n".join(
                item["content"] for item in prompt_input
            )
            try:
                payload = _json_from_text_once_compat(
                    self.llm_client,
                    prompt,
                    model=_model_for_llm_client(
                        self.llm_client, self.model
                    ),
                    response_format=_chat_response_format(
                        _taxonomy_suggestion_response_format()
                    ),
                )
            except (
                LLMOutputTruncatedError,
                LLMResponseFormatError,
            ) as exc:
                raise TaxonomySuggestionModelResponseError(
                    "AI 归并建议返回格式无效"
                ) from exc
        else:
            response = self._protocol_adapter().responses(
                request_kind=LLMRequestKind.TAGGING,
                model=self.model,
                allow_retry=False,
                kwargs={
                    "text": {
                        "format": _taxonomy_suggestion_response_format()
                    },
                    "input": prompt_input,
                },
            )
            output_text = str(
                getattr(response, "output_text", "") or ""
            ).strip()
            try:
                payload = json.loads(output_text)
            except json.JSONDecodeError as exc:
                raise TaxonomySuggestionModelResponseError(
                    "AI 归并建议返回格式无效"
                ) from exc
        if not isinstance(payload, Mapping) or not isinstance(
            payload.get("results"), list
        ):
            raise TaxonomySuggestionModelResponseError(
                "AI 归并建议返回缺少 results 列表"
            )
        return [
            dict(item)
            for item in payload["results"]
            if isinstance(item, Mapping)
        ]

    def analyze_review_question(
        self,
        context: TaggingContext,
        *,
        taxonomy_contract: Mapping[str, Any] | None = None,
        images: Sequence[Any] = (),
    ) -> AITaggingResult:
        if not self.review_configured:
            return AITaggingResult(ok=False, mock_mode=False, error="未配置低置信度复核模型", model_name=self.review_model or None, quality_status="invalid")
        try:
            assert self.review_llm_client is not None
            taxonomy_contract = dict(
                taxonomy_contract or self.taxonomy_contract(context)
            )
            images = _normalize_question_images(images)
            if images:
                payload = _json_from_images_compat(
                    self.review_llm_client,
                    _prompt_text(context, taxonomy_contract),
                    [image.content for image in images],
                    model=_model_for_llm_client(
                        self.review_llm_client, self.review_model
                    ),
                    response_format=_chat_response_format(
                        _tag_analysis_response_format(taxonomy_contract)
                    ),
                )
            else:
                payload = _json_from_text_compat(
                    self.review_llm_client,
                    _prompt_text(context, taxonomy_contract),
                    model=_model_for_llm_client(
                        self.review_llm_client, self.review_model
                    ),
                    response_format=_chat_response_format(
                        _tag_analysis_response_format(taxonomy_contract)
                    ),
                )
            result = AITaggingResult(
                ok=True,
                mock_mode=False,
                analysis=TagAnalysis.from_dict(payload),
                model_name=_model_for_llm_client(self.review_llm_client, self.review_model),
            )
            return _with_quality(
                result,
                context,
                governance=self.taxonomy_governance,
                taxonomy_contract=taxonomy_contract,
            )
        except Exception as exc:  # noqa: BLE001
            return AITaggingResult(ok=False, mock_mode=False, error=str(exc), model_name=self.review_model or None, quality_status="invalid", quality_notes=[str(exc)])

    @property
    def review_configured(self) -> bool:
        overridden_review = type(self).analyze_review_question is not AITaggingService.analyze_review_question
        return bool(self.review_model and (self.review_llm_client is not None or overridden_review))

    def _configure_review_client(self, *, tagging_api_key: str) -> None:
        if not self.review_model:
            return
        review_api_key = str(self.env.get("QUESTION_BANK_TAGGING_REVIEW_API_KEY") or tagging_api_key).strip()
        if not review_api_key:
            return
        review_base_url = str(
            self.env.get("QUESTION_BANK_TAGGING_REVIEW_BASE_URL")
            or self.env.get("QUESTION_BANK_TAGGING_BASE_URL")
            or "https://api.openai.com/v1"
        ).strip()
        settings = LLMSettings(
            api_key=review_api_key,
            base_url=review_base_url,
            ocr_model=self.review_model,
            grading_model=self.review_model,
            config_model=self.review_model,
            config_api_key=review_api_key,
            config_base_url=review_base_url,
        )
        self.review_llm_client = LLMClient(settings)

    def analyze_questions(
        self,
        contexts: Mapping[int, TaggingContext],
        *,
        taxonomy_contracts: Mapping[int, Mapping[str, Any]] | None = None,
        max_workers: int | None = None,
        requests_per_minute: int | None = None,
        progress_callback: Callable[[int, int, int, AITaggingResult], None] | None = None,
        request_callback: Callable[[TaggingRequestEvent], None] | None = None,
        allow_batch_fallback: bool = True,
        quality_retry_limit: int = 1,
        enable_review: bool = True,
        images: Mapping[int, Sequence[Any]] | None = None,
    ) -> dict[int, AITaggingResult]:
        items = list(contexts.items())
        if not items:
            return {}
        question_contracts = (
            {
                int(question_id): dict(contract)
                for question_id, contract in taxonomy_contracts.items()
                if question_id in contexts and isinstance(contract, Mapping)
            }
            if taxonomy_contracts is not None
            else self.taxonomy_contracts(contexts)
        )
        for question_id, tagging_context in items:
            if question_id not in question_contracts:
                question_contracts[question_id] = self.taxonomy_contract(
                    tagging_context
                )

        batches = _adaptive_batches(items)
        execution_profile = (
            getattr(
                getattr(self.llm_client, "settings", None),
                "policy_profile",
                None,
            )
            if self.llm_client is not None
            else self._tagging_policy_profile
        )
        execution_snapshot = execution_snapshot_from_profile(execution_profile)
        
        worker_count = _bounded_int(
            max_workers,
            execution_snapshot.max_in_flight,
            1,
            min(
                max(len(batches), 1),
                100,
                execution_snapshot.max_in_flight,
            ),
        )
        rpm_limit = _bounded_int(
            requests_per_minute,
            execution_snapshot.requests_per_minute,
            1,
            10000,
        )
        request_controller = _TaggingRequestController(rpm_limit, request_callback)
        results: dict[int, AITaggingResult] = {}
        completed = 0
        total = len(items)
        
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="qb-tagging") as executor:
            future_map = {
                executor.submit(
                    _analyze_one_batch,
                    self,
                    batch,
                    request_controller,
                    allow_batch_fallback,
                    max(0, int(quality_retry_limit)),
                    enable_review,
                    {
                        question_id: question_contracts[question_id]
                        for question_id, _context in batch
                    },
                    images,
                ): batch
                for batch in batches
            }
            for future in as_completed(future_map):
                batch = future_map[future]
                try:
                    batch_results = future.result()
                except Exception as exc:
                    batch_results = {
                        qid: AITaggingResult(ok=False, mock_mode=self.mock_mode, error=str(exc), model_name=self.model, quality_status="invalid", quality_notes=[str(exc)])
                        for qid, _ in batch
                    }
                
                for qid, result in batch_results.items():
                    results[qid] = result
                    completed += 1
                    if progress_callback is not None:
                        progress_callback(completed, total, qid, result)
                        
        return results

    def _protocol_adapter(self):
        adapter = self._protocol_adapter_instance
        if adapter is not None:
            return adapter
        with self._protocol_adapter_lock:
            adapter = self._protocol_adapter_instance
            if adapter is None:
                settings = getattr(self.llm_client, "settings", None)
                configured_key = str(
                    getattr(settings, "config_api_key", "")
                    or getattr(settings, "api_key", "")
                    or self.api_key
                ).strip()
                configured_base_url = str(
                    getattr(settings, "config_base_url", "")
                    or getattr(settings, "base_url", "")
                    or self._tagging_base_url
                ).strip()
                configured_client = (
                    getattr(self.llm_client, "config_client", None)
                    if self.llm_client is not None
                    else self.client
                )
                adapter = LLMProtocolAdapter(
                    configured_key,
                    configured_base_url,
                    policy_profile=self._tagging_policy_profile,
                    client=configured_client or self.client,
                )
                self._protocol_adapter_instance = adapter
        return adapter


def _mock_analysis(context: TaggingContext) -> TagAnalysis:
    payload = {
        "method_tags": ["角度转化法"] if "角" in context.question_text else [],
        "thought_tags": [] if "角" in context.question_text else ["方程思想"],
        "ability_tags": ["推理能力"] if context.has_answer else ["运算能力"],
        "math_model_tags": ["三平行模型"] if "平行" in context.question_text else [],
        "special_type_tags": [],
        "difficulty": 3 if context.has_answer else 2,
        "predicted_error_patterns": [
            {
                "category": "审题与条件",
                "pattern": "漏用题干给出的条件",
                "trigger_kind": "observation",
                "trigger_value": "",
            }
        ],
        "part_features": [
            {
                "part_id": "p1",
                "part_label": "",
                "solo": 2,
                "reasoning": 1,
                "computation": 1,
                "context": 0,
                "context_kind": "无情境",
                "hidden": 0,
                "cases": 0,
                "param_dynamic": 0,
                "trap": 0,
                "knowledge": 1,
                "evidence": "模拟评估：常规单问解答。",
            }
        ],
        "taxonomy_revision": 0,
        "proposed_tags": [],
        "reason": "Mock mode uses stable middle-school math tags for page testing.",
        "confidence": 0.8,
    }
    return TagAnalysis.from_dict(payload)


def _resolve_prompt_contract(
    taxonomy_contract: Mapping[str, Any] | None,
    context: TaggingContext | None = None,
) -> dict[str, Any]:
    if taxonomy_contract is not None:
        if isinstance(taxonomy_contract, QuestionTaxonomySnapshot):
            return taxonomy_contract.to_dict()
        return dict(taxonomy_contract)
    prompt_context = _taxonomy_context(context) if context is not None else None
    return dict(get_taxonomy_governance().prompt_contract(prompt_context))


def _taxonomy_revision(contract: Mapping[str, Any]) -> int:
    if isinstance(contract, QuestionTaxonomySnapshot):
        return contract.taxonomy_revision
    return _coerce_nonnegative_int(
        contract.get("taxonomy_revision", contract.get("revision")),
        fallback=0,
    )


def _taxonomy_context(context: TaggingContext | None) -> dict[str, Any]:
    if context is None:
        return {}
    return {
        "question_text": str(context.question_text or "").strip(),
        "answer_text": str(context.answer_text or "").strip(),
        "question_type": str(context.question_type or "").strip(),
        "grade": str(context.grade or "").strip(),
        "semester": str(context.semester or "").strip(),
        "textbook_version": str(context.textbook_version or "").strip(),
        "curriculum_volume_id": str(
            context.curriculum_volume_id or ""
        ).strip(),
        "exam_type": str(context.exam_type or "").strip(),
    }


def _prompt_question_input(context: TaggingContext) -> dict[str, Any]:
    payload = context.to_dict()
    # The v2 taxonomy starts from a clean governance epoch. Historical tags
    # (including values previously marked as teacher-confirmed) are deliberately
    # excluded so they cannot bias the new candidate retrieval or model output.
    payload.pop("existing_tags", None)
    payload.pop("existing_tags_by_dimension", None)
    return payload


def _coerce_nonnegative_int(value: object, *, fallback: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(fallback))


def _normalize_question_images(images: Sequence[Any] | None) -> list[Any]:
    """只接受带有字节内容与 data_url() 的题图对象（QuestionAnalysisImage）。

    大小上限与 MIME 白名单由 QuestionAnalysisImage 构造时保证；这里
    仅做调用方防错，最多携带 8 张。
    """

    result: list[Any] = []
    for image in images or ():
        content = getattr(image, "content", None)
        data_url = getattr(image, "data_url", None)
        if not isinstance(content, (bytes, bytearray)) or not content:
            continue
        if not callable(data_url):
            continue
        result.append(image)
        if len(result) >= 8:
            break
    return result


def _image_content_parts(images: Sequence[Any]) -> list[dict[str, Any]]:
    return [
        {"type": "input_image", "image_url": image.data_url()}
        for image in images
    ]


def _prompt_text(
    context: TaggingContext,
    taxonomy_contract: Mapping[str, Any] | None = None,
) -> str:
    contract = _resolve_prompt_contract(taxonomy_contract, context)
    user_payload = {
        "task": "Tag this question for a local junior math question bank.",
        "input": _prompt_question_input(context),
        "output_schema": _plain_output_schema(_taxonomy_revision(contract)),
    }
    if os.getenv("QUESTION_BANK_TAGGING_THINKING") == "1":
        user_payload["reasoning_instruction"] = _TAGGING_REASONING_INSTRUCTION
    return f"{_system_prompt(contract)}\n\n{json.dumps(user_payload, ensure_ascii=False)}"


def _prompt_input(
    context: TaggingContext,
    taxonomy_contract: Mapping[str, Any] | None = None,
    *,
    images: Sequence[Any] = (),
) -> list[dict[str, Any]]:
    contract = _resolve_prompt_contract(taxonomy_contract, context)
    user_payload = {
        "task": "Tag this question for a local junior math question bank.",
        "input": _prompt_question_input(context),
        "output_schema": _plain_output_schema(_taxonomy_revision(contract)),
    }
    if os.getenv("QUESTION_BANK_TAGGING_THINKING") == "1":
        user_payload["reasoning_instruction"] = _TAGGING_REASONING_INSTRUCTION
    text = json.dumps(user_payload, ensure_ascii=False)
    if not images:
        return [
            {"role": "system", "content": _system_prompt(contract)},
            {"role": "user", "content": text},
        ]
    return [
        {"role": "system", "content": _system_prompt(contract)},
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": text},
                {
                    "type": "input_text",
                    "text": f"本题附带 {len(images)} 张题图，随后按顺序给出。",
                },
                *_image_content_parts(images),
            ],
        },
    ]


def _system_prompt(
    taxonomy_contract: Mapping[str, Any] | None = None,
) -> str:
    contract = _resolve_prompt_contract(taxonomy_contract)
    contract_json = json.dumps(contract, ensure_ascii=False, separators=(",", ":"))
    pattern_categories = ", ".join(predicted_pattern_categories())
    return f"""
    你负责分析初中数学题。只返回符合指定结构的一个 JSON 对象。
    除公式、数学变量、选项字母、机器标识和原答案片段外，所有可供教师阅读的自由文本字段必须使用简体中文，不得返回英文说明。
    当知识候选包含教材目录树时，该列表已经是所选册别及以前册别的完整“章—小节—细分知识点”树；必须先在整棵树中按稳定 ID 选择最准确节点，不能因为题干用词不同就新造近义标签。
    The local taxonomy contract below is the only source for ability, method,
    thought, model and special-type tags:
    {contract_json}

    Controlled output rules:
    - taxonomy_revision must exactly echo {int(_taxonomy_revision(contract))}.
    - method_tags, thought_tags, ability_tags, math_model_tags and
      special_type_tags may contain only exact approved names from their
      matching contract dimension. Never place a newly coined or approximate
      term there. 整题知识点与前置知识不再由本结构输出，全部由判定点关联派生。
    - ability_tags 只标主要考查的 1–2 项，最多 2 项。
    - method_tags answers "what concrete procedure was used"; thought_tags
      answers "what reusable reasoning strategy guided the solution";
      math_model_tags names a stable structure whose defining relations are
      actually present. Never copy a term across these three fields.
    - 解答题（input.question_type 为“解答题”）若在候选中存在子类词
      （如“计算”“画图”“证明”），special_type_tags 必须重判并给出最准确
      的一个子类；依据是本题要求学生产出的形式，不要因为题干出现
      “求证”等字样就标“证明”。
    - Only when no approved knowledge term accurately fits may you add one
      knowledge item to proposed_tags. Method, thought, model, ability,
      curriculum and special-type fields are closed vocabularies: leave an
      optional field empty instead of inventing a label. Each knowledge
      proposal must state dimension (knowledge),
      name, definition, reason, nearest_id, and why_not_reuse. Also leave that
      unapproved value out of every normal field.
    - proposed_tags is always present, contains at most 1 item per question,
      and uses [] when every value is approved.

    predicted_error_patterns：选择题逐一分析题干真实出现的每个错误选项，
    每个错误选项各写一条 option 触发，正确选项不写；非选择题保留 0–3
    种最可能的具体错法。每项字段：
    - category：必须是这 7 个大类之一（{pattern_categories}）；
    - pattern：本题具体的错误做法名称（如“64 的平方根只写 8”），不要写
      “运算错误”这类泛词，也不要复述正确的完成步骤；
    - explanation：一句话说明该选项或做法为何可能出错；这是预测，不代表
      学生真实心理过程，也不计学生人数；
    - trigger_kind 与 trigger_value：错误最可能在什么位置被观察到。
      trigger_kind 只能是 {"、".join(PREDICTED_TRIGGER_KINDS)} 之一：
      option 表示选错某个选项（trigger_value 填选项字母），wrong_answer
      表示给出某个具体错误答案（trigger_value 填该答案），step 表示在解答
      某个判定点处出错（trigger_value 填该判定点 id，见 input.evidence_parts
      内的判定点），observation 表示无确定位置（trigger_value 留空）。
      拿不准就留空，不要硬凑。

    part_features 对每个小问分别评估，part_id 必须取 input.evidence_parts 中
    给出的小问标识；未提供小问列表时只有一问，part_id 填 "p1"。
    每个小问字段含义：
    - solo：1 单点 / 2 多点 / 3 关联 / 4 拓展抽象；
    - reasoning：0 直接识别或代公式 / 1 三步以内推理或转化 / 2 超过三步或
      多次连续转化；
    - computation：0 无或口算 / 1 常规数值或简单符号运算 / 2 复杂符号运算
      （根式分式综合、方程组、含参、多次平方开方）；
    - context：0 无情境 / 1 熟悉生活情境 / 2 陌生、科学跨学科、数学文化或
      新定义情境；
    - context_kind：情境类别，只能是 {"、".join(PART_CONTEXT_KINDS)} 之一；
    - hidden：0 条件直接给出 / 1 一次转化或一个隐含条件 / 2 需辅助构造或
      多个隐含条件；
    - cases：0 不分类 / 1 两种情况 / 2 三种及以上或需判断存在性；
    - param_dynamic：0 否 / 1 含参数或动点；
    - trap：0 / 1；
    - knowledge：0 一个知识点 / 1 两个 / 2 三个及以上；
    - evidence：一句依据。
    多小问逐问评估，不把一问难度复制给其余问；评估不得使用考试分值、学生
    得分率、小问数量、题干长短或是否含根式。

    Difficulty must be an integer from 1 to 10.
    {DIFFICULTY_SCALE_GUIDANCE}
    input.has_images indicates the stored question has images; when present,
    the image bodies are attached to this request — always use them before
    judging a figure-dependent question.
    Historical saved tags are intentionally absent from the input and must not
    be inferred or preserved. Judge this question from its current content and
    the current taxonomy contract only.
    confidence must be a number from 0 to 1 for your overall confidence in the tag set. Lower it when the image is essential, the answer is missing, or the key solution step is uncertain.
    """.strip()


def _plain_output_schema(taxonomy_revision: int = 0) -> dict[str, object]:
    return {
        "method_tags": [],
        "thought_tags": [],
        "ability_tags": [],
        "math_model_tags": [],
        "special_type_tags": [],
        "difficulty": 1,
        "predicted_error_patterns": [
            {
                "category": "",
                "pattern": "",
                "explanation": "",
                "trigger_kind": "",
                "trigger_value": "",
            }
        ],
        "part_features": [
            {
                "part_id": "",
                "part_label": "",
                "solo": 1,
                "reasoning": 0,
                "computation": 0,
                "context": 0,
                "context_kind": "",
                "hidden": 0,
                "cases": 0,
                "param_dynamic": 0,
                "trap": 0,
                "knowledge": 1,
                "evidence": "",
            }
        ],
        "taxonomy_revision": int(taxonomy_revision),
        "proposed_tags": [
            {
                "dimension": "",
                "name": "",
                "definition": "",
                "reason": "",
                "nearest_id": "",
                "why_not_reuse": "",
            }
        ],
        "reason": "",
        "confidence": 0.8,
    }


def _proposal_response_schema() -> dict[str, object]:
    text_field = {"type": "string"}
    properties = {
        "dimension": text_field,
        "name": text_field,
        "definition": text_field,
        "reason": text_field,
        "nearest_id": text_field,
        "why_not_reuse": text_field,
    }
    return {
        "type": "array",
        "maxItems": 1,
        "items": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


def _contract_candidates(
    contract: Mapping[str, Any] | None,
    dimension: str,
) -> list[dict[str, str]]:
    if contract is None:
        return []
    candidates = contract.get("candidates")
    if not isinstance(candidates, Mapping):
        return []
    raw_items = candidates.get(dimension)
    if not isinstance(raw_items, Sequence) or isinstance(
        raw_items, (str, bytes, bytearray)
    ):
        return []
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            continue
        item = {
            "id": str(raw.get("id") or "").strip(),
            "name": str(raw.get("name") or "").strip(),
        }
        usage = str(raw.get("usage") or "").strip()
        if usage:
            item["usage"] = usage
        key = (item["id"], item["name"])
        if not item["name"] or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _controlled_array_schema(
    contract: Mapping[str, Any] | None,
    dimension: str,
) -> dict[str, Any]:
    if contract is None:
        return {"type": "array", "items": {"type": "string"}}
    names = [
        item["name"]
        for item in _contract_candidates(contract, dimension)
        if item.get("usage") != "do_not_use_as_knowledge"
    ]
    if not names:
        return {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 0,
        }
    return {
        "type": "array",
        "items": {"type": "string", "enum": names},
    }


_CONTROLLED_ID_FIELD_DIMENSIONS = {
    "method_tags": "method",
    "thought_tags": "thought",
    "ability_tags": "ability",
    "math_model_tags": "model",
    "special_type_tags": "special_type",
}


def _resolve_controlled_ids(
    analysis: TagAnalysis,
    contract: Mapping[str, Any],
) -> TagAnalysis:
    """Translate exact candidate IDs in controlled fields to approved names.

    The combined-v3 tagging contract asks the model to answer with stable
    candidate IDs. Violation reporting, constraint, and persistence all work
    with approved names, so map each exact candidate ID to its name first.
    Values that are already approved names, or unknown, pass through
    unchanged and keep the established violation and proposal behavior.
    """
    if not isinstance(contract.get("candidates"), Mapping):
        return analysis
    payload = analysis.to_dict()
    changed = False
    for field_name, dimension in _CONTROLLED_ID_FIELD_DIMENSIONS.items():
        id_to_name = {
            item["id"]: item["name"]
            for item in _contract_candidates(contract, dimension)
            if item["id"]
        }
        if not id_to_name:
            continue
        values = payload.get(field_name)
        if not isinstance(values, list) or not values:
            continue
        resolved = [
            id_to_name.get(str(value or "").strip(), value) for value in values
        ]
        if resolved != values:
            payload[field_name] = resolved
            changed = True
    if not changed:
        return analysis
    return TagAnalysis.from_dict(payload)


def _controlled_field_violation_notes(
    analysis: TagAnalysis,
    contract: Mapping[str, Any],
) -> list[str]:
    if not isinstance(contract.get("candidates"), Mapping):
        return []
    field_dimensions = {
        "method_tags": "method",
        "ability_tags": "ability",
        "math_model_tags": "model",
        "special_type_tags": "special_type",
    }
    notes: list[str] = []
    for field_name, dimension in field_dimensions.items():
        allowed = {
            item["name"]
            for item in _contract_candidates(contract, dimension)
            if item.get("usage") != "do_not_use_as_knowledge"
        }
        values = getattr(analysis, field_name, [])
        if any(str(value or "").strip() not in allowed for value in values):
            notes.append(f"controlled_field_violation:{field_name}")
    return notes


def _predicted_pattern_response_schema() -> dict[str, Any]:
    categories = list(predicted_pattern_categories())
    properties = {
        "category": (
            {"type": "string", "enum": categories}
            if categories
            else {"type": "string"}
        ),
        "pattern": {"type": "string"},
        "explanation": {"type": "string"},
        "trigger_kind": {
            "type": "string",
            "enum": list(PREDICTED_TRIGGER_KINDS),
        },
        "trigger_value": {"type": "string"},
    }
    return {
        "type": "array",
        "maxItems": PREDICTED_PATTERN_MAX,
        "items": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


def _part_features_response_schema() -> dict[str, Any]:
    def ranged(name: str) -> dict[str, Any]:
        minimum, maximum = PART_FEATURE_RANGES[name]
        return {"type": "integer", "minimum": minimum, "maximum": maximum}

    properties: dict[str, Any] = {
        "part_id": {"type": "string"},
        "part_label": {"type": "string"},
        **{name: ranged(name) for name in PART_FEATURE_ORDER},
        "context_kind": {
            "type": "string",
            "enum": list(PART_CONTEXT_KINDS),
        },
        "evidence": {"type": "string"},
    }
    return {
        "type": "array",
        "minItems": 1,
        "maxItems": 8,
        "items": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


def _tag_analysis_response_format(
    taxonomy_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    score_field = {"type": "integer", "minimum": 1, "maximum": 10}
    text_field = {"type": "string"}
    ability_schema = dict(
        _controlled_array_schema(taxonomy_contract, "ability")
    )
    ability_schema["maxItems"] = MAX_ABILITY_TAGS
    properties = {
        "method_tags": _controlled_array_schema(
            taxonomy_contract, "method"
        ),
        "thought_tags": _controlled_array_schema(
            taxonomy_contract, "thought"
        ),
        "ability_tags": ability_schema,
        "math_model_tags": _controlled_array_schema(
            taxonomy_contract, "model"
        ),
        "special_type_tags": _controlled_array_schema(
            taxonomy_contract, "special_type"
        ),
        "difficulty": score_field,
        "predicted_error_patterns": _predicted_pattern_response_schema(),
        "part_features": _part_features_response_schema(),
        "taxonomy_revision": {"type": "integer"},
        "proposed_tags": _proposal_response_schema(),
        "reason": text_field,
        "confidence": {"type": "number"},
    }
    return {
        "type": "json_schema",
        "name": "question_bank_tag_analysis",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


def _taxonomy_suggestion_prompt_input(
    batch: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    safe_batch: list[dict[str, Any]] = []
    shared_catalogs: list[dict[str, Any]] = []
    catalog_keys: dict[str, str] = {}
    for raw in list(batch)[:20]:
        if not isinstance(raw, Mapping):
            continue
        candidates = []
        for candidate in list(raw.get("candidates") or [])[:1500]:
            if not isinstance(candidate, Mapping):
                continue
            item = {
                "id": _limited_text(candidate.get("id"), 100),
                "name": _limited_text(candidate.get("name"), 160),
            }
            for key, limit in (
                ("label", 160),
                ("parent_id", 100),
                ("volume_id", 100),
            ):
                value = _limited_text(candidate.get(key), limit)
                if value:
                    item[key] = value
            if candidate.get("level") is not None:
                try:
                    item["level"] = int(candidate.get("level"))
                except (TypeError, ValueError):
                    pass
            candidates.append(item)
        catalog_fingerprint = json.dumps(
            candidates,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        catalog_key = catalog_keys.get(catalog_fingerprint)
        if catalog_key is None:
            catalog_key = f"catalog-{len(shared_catalogs) + 1}"
            catalog_keys[catalog_fingerprint] = catalog_key
            shared_catalogs.append(
                {"catalog_key": catalog_key, "terms": candidates}
            )
        summaries = []
        for summary in list(raw.get("question_summaries") or [])[:6]:
            if not isinstance(summary, Mapping):
                continue
            try:
                question_id = int(summary.get("id"))
            except (TypeError, ValueError):
                continue
            summaries.append(
                {
                    "id": question_id,
                    "question_number": _limited_text(
                        summary.get("question_number"), 40
                    ),
                    "question_type": _limited_text(
                        summary.get("question_type"), 40
                    ),
                    "question_text": _limited_text(
                        summary.get("question_text"), 600
                    ),
                    "answer_text": _limited_text(
                        summary.get("answer_text"), 400
                    ),
                }
            )
        safe_batch.append(
            {
                "proposal_id": _limited_text(
                    raw.get("proposal_id"), 100
                ),
                "dimension": _limited_text(raw.get("dimension"), 40),
                "proposed_name": _limited_text(
                    raw.get("proposed_name"), 160
                ),
                "reason": _limited_text(raw.get("reason"), 300),
                "nearest_id": _limited_text(
                    raw.get("nearest_id"), 100
                ),
                "candidate_catalog_key": catalog_key,
                "allowed_target_ids": [item["id"] for item in candidates],
                "question_summaries": summaries,
            }
        )
    system_prompt = """
    你协助初中数学教师审核题库中新出现的标签词。你只能给出归并建议，
    不能声称已经批准候选词，也不能写入任何数据。所有 reason 必须只写一句中文，
    不超过 40 个汉字；直接说明与现有词的关系和是否需要教师确认，不要复述候选词。

    semantic relation 只能是以下一个值：exact、broader、narrower、related、
    new_core_candidate、wrong_dimension、reject、uncertain。exact 只表示严格同义，
    不能把相近、上下位或部分重叠当成同义。只有 exact 可以指定一个目标词供系统
    自动归并；broader、narrower、related 只能列出相关目标供教师核对。目标 ID 必须
    来自该待审词 candidate_catalog_key 对应的完整词表，并且必须出现在
    allowed_target_ids 中。shared_candidate_catalogs 中 level 1/2/3 分别代表章、
    小节、细分知识点，parent_id 表示树状父节点。每个 proposal_id 必须返回且只
    返回一项。置信度不能代替审核，不要返回英文说明。
    """.strip()
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "用中文为这些待审核词给出归并建议。",
                    "shared_candidate_catalogs": shared_catalogs,
                    "proposals": safe_batch,
                },
                ensure_ascii=False,
            ),
        },
    ]


def _taxonomy_suggestion_response_format() -> dict[str, Any]:
    properties = {
        "proposal_id": {"type": "string"},
        "relation_kind": {
            "type": "string",
            "enum": [
                "exact",
                "broader",
                "narrower",
                "related",
                "new_core_candidate",
                "wrong_dimension",
                "reject",
                "uncertain",
            ],
        },
        "target_term_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "reason": {"type": "string"},
        "confidence": {"type": "number"},
    }
    return {
        "type": "json_schema",
        "name": "question_bank_taxonomy_review_suggestions",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": properties,
                        "required": list(properties),
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["results"],
            "additionalProperties": False,
        },
    }


def _chat_response_format(
    responses_format: Mapping[str, Any],
) -> dict[str, Any]:
    """Convert a Responses API text format to Chat Completions format."""

    return {
        "type": "json_schema",
        "json_schema": {
            "name": str(responses_format["name"]),
            "strict": bool(responses_format.get("strict", False)),
            "schema": dict(responses_format["schema"]),
        },
    }


def _limited_text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _dotenv_values() -> dict[str, str]:
    env_file = _find_env_file()
    if env_file is None:
        return {}
    values: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _find_env_file() -> Path | None:
    for base in (Path.cwd(), Path(__file__).resolve().parents[2]):
        candidate = base / ".env"
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _llm_client_from_saved_profile(env: Mapping[str, str]) -> LLMClient | None:
    settings = _llm_settings_from_env(env) or _llm_settings_from_profile()
    if settings is None:
        return None
    return LLMClient(settings)


def _llm_settings_from_env(env: Mapping[str, str]) -> LLMSettings | None:
    api_key = str(env.get("LLM_API_KEY") or "").strip()
    config_api_key = str(env.get("LLM_CONFIG_API_KEY") or api_key).strip()
    if not api_key or not config_api_key:
        return None
    return LLMSettings(
        api_key=api_key,
        base_url=normalize_openai_base_url(str(env.get("LLM_BASE_URL") or "https://api.openai.com/v1")),
        ocr_model=str(env.get("LLM_OCR_MODEL") or env.get("LLM_GRADING_MODEL") or DEFAULT_TAGGING_MODEL),
        grading_model=str(env.get("LLM_GRADING_MODEL") or DEFAULT_TAGGING_MODEL),
        config_model=str(env.get("QUESTION_BANK_TAGGING_MODEL") or env.get("LLM_CONFIG_MODEL") or DEFAULT_TAGGING_MODEL),
        config_api_key=config_api_key,
        config_base_url=normalize_openai_base_url(str(env.get("LLM_CONFIG_BASE_URL") or env.get("LLM_BASE_URL") or "https://api.openai.com/v1")),
    )


def _llm_settings_from_profile() -> LLMSettings | None:
    profile = _active_saved_profile()
    if not profile:
        return None
    api_key = str(profile.get("api_key") or "").strip()
    config_api_key = str(profile.get("config_api_key") or api_key).strip()
    if not api_key or not config_api_key:
        return None
    grading_model = str(profile.get("grading_model") or DEFAULT_TAGGING_MODEL)
    return LLMSettings(
        api_key=api_key,
        base_url=normalize_openai_base_url(str(profile.get("base_url") or "https://api.openai.com/v1")),
        ocr_model=str(profile.get("ocr_model") or grading_model),
        grading_model=grading_model,
        config_model=str(profile.get("config_model") or DEFAULT_TAGGING_MODEL),
        config_api_key=config_api_key,
        config_base_url=normalize_openai_base_url(str(profile.get("config_base_url") or profile.get("base_url") or "https://api.openai.com/v1")),
        policy_profile=policy_overrides_from_profile(profile),
    )


def _active_saved_profile() -> dict[str, Any]:
    return resolve_profile_for_task(get_api_profile_store(), "content_generation")


def _model_for_llm_client(llm_client: Any, fallback: str) -> str:
    settings = getattr(llm_client, "settings", None)
    return str(getattr(settings, "config_model", "") or fallback)


def _json_from_text_compat(
    llm_client: Any,
    prompt: str,
    *,
    model: str | None = None,
    extra_kwargs: dict[str, Any] | None = None,
    response_format: Mapping[str, Any] | None = None,
) -> Any:
    call_kwargs: dict[str, Any] = {
        "model": model,
        "request_kind": LLMRequestKind.TAGGING,
        "response_format": response_format,
    }
    if extra_kwargs is not None:
        call_kwargs["extra_kwargs"] = extra_kwargs
    try:
        return llm_client.json_from_text(prompt, **call_kwargs)
    except TypeError as exc:
        message = str(exc)
        if "request_kind" in message:
            call_kwargs.pop("request_kind", None)
        elif "extra_kwargs" in message:
            call_kwargs.pop("extra_kwargs", None)
        else:
            raise
    try:
        return llm_client.json_from_text(prompt, **call_kwargs)
    except TypeError as exc:
        if "extra_kwargs" not in str(exc):
            raise
        call_kwargs.pop("extra_kwargs", None)
        return llm_client.json_from_text(prompt, **call_kwargs)


def _json_from_images_compat(
    llm_client: Any,
    prompt: str,
    image_blobs: list[bytes],
    *,
    model: str | None = None,
    extra_kwargs: dict[str, Any] | None = None,
    response_format: Mapping[str, Any] | None = None,
) -> Any:
    """题图走 chat 图像接口；兼容没有 response_format 形参的替身实现。"""

    method = getattr(llm_client, "json_from_images_with_options", None)
    if not callable(method):
        raise RuntimeError("题图打标需要支持图像的模型客户端")
    call_kwargs: dict[str, Any] = {
        "model": model,
        "request_kind": LLMRequestKind.TAGGING,
        "response_format": response_format,
    }
    if extra_kwargs is not None:
        call_kwargs["extra_kwargs"] = extra_kwargs
    try:
        return method(prompt, image_blobs, **call_kwargs)
    except TypeError as exc:
        message = str(exc)
        for name in ("request_kind", "response_format", "extra_kwargs"):
            if name in message:
                call_kwargs.pop(name, None)
                break
        else:
            raise
    for _ in range(len(call_kwargs)):
        try:
            return method(prompt, image_blobs, **call_kwargs)
        except TypeError as exc:
            matched = False
            for name in ("request_kind", "response_format", "extra_kwargs"):
                if name in str(exc) and name in call_kwargs:
                    call_kwargs.pop(name, None)
                    matched = True
                    break
            if not matched:
                raise
    return method(prompt, image_blobs, **call_kwargs)


def _json_from_text_once_compat(
    llm_client: Any,
    prompt: str,
    *,
    model: str | None = None,
    response_format: Mapping[str, Any] | None = None,
    extra_kwargs: dict[str, Any] | None = None,
) -> Any:
    """Use the strict one-request interface; never fall back to AI repair."""

    method = getattr(llm_client, "json_from_text_once", None)
    if not callable(method):
        raise RuntimeError("AI 归并建议需要单次请求 JSON 接口")
    call_kwargs: dict[str, Any] = {
        "model": model,
        "request_kind": LLMRequestKind.TAGGING,
        "response_format": response_format,
    }
    if extra_kwargs is not None:
        call_kwargs["extra_kwargs"] = extra_kwargs
    try:
        return method(prompt, **call_kwargs)
    except TypeError as exc:
        if "request_kind" not in str(exc):
            raise
        call_kwargs.pop("request_kind", None)
        return method(prompt, **call_kwargs)


def _analyze_one_question(service: AITaggingService, context: TaggingContext, rate_limiter: "_RateLimiter") -> AITaggingResult:
    rate_limiter.acquire()
    return service.analyze_question(context)


def _analyze_question_with_contract(
    service: AITaggingService,
    context: TaggingContext,
    taxonomy_contract: Mapping[str, Any],
    *,
    images: Sequence[Any] = (),
) -> AITaggingResult:
    try:
        return service.analyze_question(
            context,
            taxonomy_contract=taxonomy_contract,
            images=images,
        )
    except TypeError as exc:
        if "taxonomy_contract" not in str(exc):
            raise
        # Compatibility for local test/demonstration subclasses that still
        # implement the earlier one-argument hook. The fixed contract is still
        # applied during local convergence below.
        return service.analyze_question(context)


def _analyze_review_with_contract(
    service: AITaggingService,
    context: TaggingContext,
    taxonomy_contract: Mapping[str, Any],
    *,
    images: Sequence[Any] = (),
) -> AITaggingResult:
    try:
        return service.analyze_review_question(
            context,
            taxonomy_contract=taxonomy_contract,
            images=images,
        )
    except TypeError as exc:
        if "taxonomy_contract" not in str(exc):
            raise
        return service.analyze_review_question(context)


def _batch_system_prompt(
    taxonomy_contract: Mapping[str, Any] | None = None,
) -> str:
    base = _system_prompt(taxonomy_contract)
    return f"""
    {base}
    
    这是一个批量任务。必须逐题分析，并把结果列表放在 JSON 的 results 字段中。
    每个结果必须原样带回输入中的 question_id，不得漏题、串题或互相借用标签。
    shared_knowledge_catalog 在整批中只发送一次，它是本批各题所选教材册别完整知识树的并集。
    每题仍只能选择该题 allowed_term_ids.knowledge 中允许的稳定 ID；其他维度也必须留在该题自己的候选契约内。
    所有教师可见的自由文本必须使用简体中文。
    """.strip()


def _batch_shared_contract(
    batch_contexts: list[tuple[int, TaggingContext]],
    taxonomy_contracts: Mapping[int, Mapping[str, Any]] | Mapping[str, Any] | None,
) -> dict[str, Any]:
    taxonomy_contracts = _normalize_batch_contracts(
        batch_contexts,
        taxonomy_contracts,
    )
    contracts = [
        dict(taxonomy_contracts[question_id])
        for question_id, _context in batch_contexts
    ]
    revisions = {_taxonomy_revision(contract) for contract in contracts}
    if len(revisions) > 1:
        raise ValueError("Per-question taxonomy contracts must share one revision")
    first = contracts[0] if contracts else {}
    shared_knowledge_catalog: list[dict[str, Any]] = []
    seen_knowledge_ids: set[str] = set()
    for contract in contracts:
        for item in _contract_candidates(contract, "knowledge"):
            item_id = str(item.get("id") or "").strip()
            if not item_id or item_id in seen_knowledge_ids:
                continue
            seen_knowledge_ids.add(item_id)
            shared_knowledge_catalog.append(dict(item))
    return {
        "schema_version": 1,
        "taxonomy_revision": next(iter(revisions), 0),
        "knowledge_graph_release_id": str(
            first.get("knowledge_graph_release_id") or ""
        ),
        "shared_knowledge_catalog": shared_knowledge_catalog,
        "allowed_dimensions": list(first.get("allowed_dimensions", [])),
        "rules": {
            "selection": (
                "Knowledge uses shared_knowledge_catalog intersected with the "
                "current question's allowed_term_ids.knowledge; every other "
                "dimension uses only that question's candidate_contract."
            ),
            "unknown": "Return at most 1 knowledge proposal for the current question.",
        },
    }


def _normalize_batch_contracts(
    batch_contexts: list[tuple[int, TaggingContext]],
    taxonomy_contracts: Mapping[int, Mapping[str, Any]] | Mapping[str, Any] | None,
) -> dict[int, dict[str, Any]]:
    if taxonomy_contracts is None:
        return {
            question_id: _resolve_prompt_contract(None, context)
            for question_id, context in batch_contexts
        }
    if "taxonomy_revision" in taxonomy_contracts or "candidates" in taxonomy_contracts:
        shared = dict(taxonomy_contracts)
        return {
            question_id: dict(shared)
            for question_id, _context in batch_contexts
        }
    return {
        question_id: dict(taxonomy_contracts[question_id])
        for question_id, _context in batch_contexts
    }


def _batch_question_contract(
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Remove the batch-shared knowledge catalog from a per-question payload."""

    compact = dict(contract)
    for field in ("candidates", "truncated"):
        raw = compact.get(field)
        if not isinstance(raw, Mapping):
            continue
        values = dict(raw)
        values.pop("knowledge", None)
        compact[field] = values
    raw_allowed = compact.get("allowed_term_ids")
    if isinstance(raw_allowed, Mapping):
        compact["allowed_term_ids"] = {
            dimension: list(values) if isinstance(values, list) else values
            for dimension, values in raw_allowed.items()
        }
    return compact


def _batch_prompt_input(
    batch_contexts: list[tuple[int, TaggingContext]],
    taxonomy_contracts: Mapping[int, Mapping[str, Any]]
    | Mapping[str, Any]
    | None = None,
    images: Mapping[int, Sequence[Any]] | None = None,
) -> list[dict[str, Any]]:
    taxonomy_contracts = _normalize_batch_contracts(
        batch_contexts,
        taxonomy_contracts,
    )
    contract = _batch_shared_contract(batch_contexts, taxonomy_contracts)
    input_payloads = []
    image_manifest: list[dict[str, Any]] = []
    for question_id, context in batch_contexts:
        question_images = _normalize_question_images(
            (images or {}).get(question_id, ())
        )
        if question_images:
            image_manifest.append(
                {
                    "question_id": question_id,
                    "image_count": len(question_images),
                    "sha256": [
                        str(getattr(image, "sha256", "") or "")
                        for image in question_images
                    ],
                }
            )
        input_payloads.append({
            "question_id": question_id,
            "input": _prompt_question_input(context),
            "candidate_contract": _batch_question_contract(
                taxonomy_contracts[question_id]
            ),
        })

    user_payload = {
        "task": "Analyze and tag this batch of junior middle-school math questions.",
        "batch_inputs": input_payloads,
        "output_schema": {
            "results": [
                {
                    **_plain_output_schema(_taxonomy_revision(contract)),
                    "question_id": 0,
                }
            ]
        },
    }
    if image_manifest:
        user_payload["question_images"] = image_manifest
    if os.getenv("QUESTION_BANK_TAGGING_THINKING") == "1":
        user_payload["reasoning_instruction"] = _TAGGING_REASONING_INSTRUCTION

    if not image_manifest:
        return [
            {"role": "system", "content": _batch_system_prompt(contract)},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False),
            },
        ]
    user_content: list[dict[str, Any]] = [
        {"type": "input_text", "text": json.dumps(user_payload, ensure_ascii=False)}
    ]
    for question_id, _context in batch_contexts:
        question_images = _normalize_question_images(
            (images or {}).get(question_id, ())
        )
        if not question_images:
            continue
        user_content.append(
            {
                "type": "input_text",
                "text": f"question_id={question_id} 的题图，共 {len(question_images)} 张：",
            }
        )
        user_content.extend(_image_content_parts(question_images))
    return [
        {"role": "system", "content": _batch_system_prompt(contract)},
        {"role": "user", "content": user_content},
    ]


def _batch_contract_union(
    taxonomy_contracts: Mapping[Any, Mapping[str, Any]]
    | Mapping[str, Any]
    | None,
) -> dict[str, Any] | None:
    if taxonomy_contracts is None:
        return None
    if "candidates" in taxonomy_contracts:
        contracts = [taxonomy_contracts]
    else:
        contracts = [
            value
            for value in taxonomy_contracts.values()
            if isinstance(value, Mapping)
        ]
    if not contracts:
        return None
    dimensions = (
        "curriculum",
        "knowledge",
        "ability",
        "method",
        "model",
        "special_type",
    )
    merged_candidates: dict[str, list[dict[str, str]]] = {}
    for dimension in dimensions:
        items: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for contract in contracts:
            for item in _contract_candidates(contract, dimension):
                key = (item["id"], item["name"])
                if key in seen:
                    continue
                seen.add(key)
                items.append(item)
        merged_candidates[dimension] = items
    return {"candidates": merged_candidates}


def _batch_tag_analysis_response_format(
    taxonomy_contracts: Mapping[Any, Mapping[str, Any]]
    | Mapping[str, Any]
    | None = None,
) -> dict[str, Any]:
    union_contract = _batch_contract_union(taxonomy_contracts)
    single_properties = _tag_analysis_response_format(union_contract)[
        "schema"
    ]["properties"]
    question_analysis_properties = {
        "question_id": {"type": "integer"},
        **single_properties,
    }

    return {
        "type": "json_schema",
        "name": "question_bank_batch_tag_analysis",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": question_analysis_properties,
                        "required": list(question_analysis_properties),
                        "additionalProperties": False,
                    }
                }
            },
            "required": ["results"],
            "additionalProperties": False,
        },
    }


def _loads_model_json(text: str) -> Any:
    """Parse model JSON output with conservative, semantics-preserving cleanup.

    Only strips decorations that cannot change the payload (markdown fences,
    text outside the outermost brackets, trailing commas).  Anything still
    unparsable is raised so the caller can fall back to a fresh request.
    """
    cleaned = text.strip()
    fence = re.match(r"^```[a-zA-Z0-9]*\s*(?P<body>.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group("body").strip()
    candidates = [cleaned]
    start = min(
        (index for index in (cleaned.find("{"), cleaned.find("[")) if index >= 0),
        default=-1,
    )
    end = max(cleaned.rfind("}"), cleaned.rfind("]"))
    if start > 0 or (start >= 0 and end >= 0 and end < len(cleaned) - 1):
        if start >= 0 and end > start:
            candidates.append(cleaned[start : end + 1])
    for candidate in list(candidates):
        no_trailing_commas = re.sub(r",(\s*[}\]])", r"\1", candidate)
        if no_trailing_commas != candidate:
            candidates.append(no_trailing_commas)
    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise json.JSONDecodeError("empty model output", text, 0)


def _mock_batch_analysis(batch_contexts: list[tuple[int, TaggingContext]]) -> dict[int, AITaggingResult]:
    results = {}
    for qid, ctx in batch_contexts:
        results[qid] = AITaggingResult(ok=True, mock_mode=True, analysis=_mock_analysis(ctx))
    return results


def _analyze_one_batch(
    service: AITaggingService,
    batch_items: list[tuple[int, TaggingContext]],
    request_controller: "_TaggingRequestController",
    allow_batch_fallback: bool,
    quality_retry_limit: int,
    enable_review: bool,
    taxonomy_contracts: Mapping[int, Mapping[str, Any]] | None = None,
    images: Mapping[int, Sequence[Any]] | None = None,
) -> dict[int, AITaggingResult]:
    taxonomy_contracts = _normalize_batch_contracts(
        batch_items,
        taxonomy_contracts,
    )
    taxonomy_contract = _batch_shared_contract(
        batch_items,
        taxonomy_contracts,
    )
    batch_images = {
        qid: _normalize_question_images((images or {}).get(qid, ()))
        for qid, _context in batch_items
    }
    has_any_images = any(batch_images.values())
    if service.mock_mode:
        return _finalize_batch_results(
            service,
            batch_items,
            _mock_batch_analysis(batch_items),
            taxonomy_contracts=taxonomy_contracts,
            images=batch_images,
        )

    _batch_started = request_controller.begin("batch", tuple(qid for qid, _context in batch_items))

    try:
        if service.llm_client is not None:
            # Format prompt for llm_client
            batch_inputs = [
                {
                    "question_id": qid,
                    "input": _prompt_question_input(ctx),
                    "candidate_contract": _batch_question_contract(
                        taxonomy_contracts[qid]
                    ),
                }
                for qid, ctx in batch_items
            ]
            user_payload = {
                "task": "Analyze and tag this batch of junior middle-school math questions.",
                "batch_inputs": batch_inputs,
                "output_schema": {
                    "results": [
                        {
                            **_plain_output_schema(
                                _taxonomy_revision(taxonomy_contract)
                            ),
                            "question_id": 0,
                        }
                    ]
                },
            }
            image_blobs: list[bytes] = []
            if has_any_images:
                image_manifest = []
                for qid, _ctx in batch_items:
                    question_images = batch_images.get(qid) or []
                    if not question_images:
                        continue
                    image_manifest.append(
                        {
                            "question_id": qid,
                            "image_count": len(question_images),
                        }
                    )
                    image_blobs.extend(
                        image.content for image in question_images
                    )
                user_payload["question_images"] = image_manifest
            if os.getenv("QUESTION_BANK_TAGGING_THINKING") == "1":
                user_payload["reasoning_instruction"] = (
                    _TAGGING_REASONING_INSTRUCTION
                )
            prompt = (
                f"{_batch_system_prompt(taxonomy_contract)}\n\n"
                f"{json.dumps(user_payload, ensure_ascii=False)}"
            )
            if image_blobs:
                prompt += (
                    "\n\n题图按 question_images 中 question_id 的顺序随请求附带，"
                    "请结合题图判断。"
                )

            thinking_enabled = os.getenv("QUESTION_BANK_TAGGING_THINKING") == "1"
            extra_kwargs = {"thinking": True} if thinking_enabled else None
            response_format = _chat_response_format(
                _batch_tag_analysis_response_format(taxonomy_contracts)
            )
            if image_blobs:
                payload = _json_from_images_compat(
                    service.llm_client,
                    prompt,
                    image_blobs,
                    model=_model_for_llm_client(
                        service.llm_client, service.model
                    ),
                    extra_kwargs=extra_kwargs,
                    response_format=response_format,
                )
            else:
                payload = _json_from_text_compat(
                    service.llm_client,
                    prompt,
                    model=_model_for_llm_client(
                        service.llm_client, service.model
                    ),
                    extra_kwargs=extra_kwargs,
                    response_format=response_format,
                )
            if not isinstance(payload.get("results"), list):
                raise ValueError("LLM response did not include a batch results list")
            results = {}
            for item in payload.get("results", []):
                try:
                    qid = int(item["question_id"])
                    results[qid] = AITaggingResult(
                        ok=True,
                        mock_mode=False,
                        analysis=TagAnalysis.from_dict(item),
                        model_name=_model_for_llm_client(service.llm_client, service.model),
                    )
                except (KeyError, ValueError, TypeError):
                    continue
            for qid, ctx in batch_items:
                if qid not in results:
                    results[qid] = AITaggingResult(ok=False, mock_mode=False, error="LLM response did not include results for this question ID", model_name=service.model, quality_status="invalid")
            request_controller.finish(_batch_started, "succeeded", model_name=service.model)
            return _finalize_batch_results(
                service,
                batch_items,
                results,
                request_controller=request_controller,
                quality_retry_limit=quality_retry_limit,
                enable_review=enable_review,
                taxonomy_contracts=taxonomy_contracts,
                images=batch_images,
            )

        # Standard OpenAI-style / Google Responses API client
        response = service._protocol_adapter().responses(
            request_kind=LLMRequestKind.TAGGING,
            model=service.model,
            kwargs={
                "text": {
                    "format": _batch_tag_analysis_response_format(
                        taxonomy_contracts
                    )
                },
                "input": _batch_prompt_input(
                    batch_items, taxonomy_contracts, images=batch_images
                ),
            },
        )
        output_text = str(getattr(response, "output_text", "") or "").strip()
        payload = _loads_model_json(output_text)
        results = {}
        for item in payload.get("results", []):
            try:
                qid = int(item["question_id"])
                results[qid] = AITaggingResult(
                    ok=True,
                    mock_mode=False,
                    analysis=TagAnalysis.from_dict(item),
                    model_name=service.model,
                )
            except (KeyError, ValueError, TypeError):
                continue
        for qid, ctx in batch_items:
            if qid not in results:
                results[qid] = AITaggingResult(ok=False, mock_mode=False, error="API response did not include results for this question ID", model_name=service.model, quality_status="invalid")
        request_controller.finish(_batch_started, "succeeded", model_name=service.model)
        return _finalize_batch_results(
            service,
            batch_items,
            results,
            request_controller=request_controller,
            quality_retry_limit=quality_retry_limit,
            enable_review=enable_review,
            taxonomy_contracts=taxonomy_contracts,
            images=batch_images,
        )
    except Exception as exc:
        request_controller.finish(_batch_started, "failed", exc, model_name=service.model)
        if not allow_batch_fallback:
            fallback_results = {
                qid: AITaggingResult(
                    ok=False,
                    mock_mode=False,
                    error=str(exc),
                    model_name=service.model,
                    quality_status="invalid",
                    quality_notes=[str(exc)],
                )
                for qid, _context in batch_items
            }
        else:
            fallback_results = {}
            for qid, ctx in batch_items:
                try:
                    request_controller.begin("single_fallback", (qid,))
                    fallback_results[qid] = _analyze_question_with_contract(
                        service,
                        ctx,
                        taxonomy_contracts[qid],
                    )
                except Exception as single_exc:
                    fallback_results[qid] = AITaggingResult(
                        ok=False,
                        mock_mode=False,
                        error=str(single_exc),
                        model_name=service.model,
                        quality_status="invalid",
                        quality_notes=[str(single_exc)],
                    )
        return _finalize_batch_results(
            service,
            batch_items,
            fallback_results,
            request_controller=request_controller,
            quality_retry_limit=quality_retry_limit,
            enable_review=enable_review,
            taxonomy_contracts=taxonomy_contracts,
        )


def _adaptive_batches(items: list[tuple[int, TaggingContext]]) -> list[list[tuple[int, TaggingContext]]]:
    batches: list[list[tuple[int, TaggingContext]]] = []
    current: list[tuple[int, TaggingContext]] = []
    current_size = 0
    for item in items:
        size = _batch_size_for_context(item[1])
        if size == 1:
            if current:
                batches.append(current)
                current = []
                current_size = 0
            batches.append([item])
            continue
        if not current or current_size != size:
            if current:
                batches.append(current)
            current = [item]
            current_size = size
        else:
            current.append(item)
        if len(current) >= current_size:
            batches.append(current)
            current = []
            current_size = 0
    if current:
        batches.append(current)
    return batches


def _batch_size_for_context(context: TaggingContext) -> int:
    text = str(context.question_text or "")
    q_type = str(context.question_type or "")
    if context.has_images or "[[IMAGE:" in text or any(token in q_type for token in ("证明", "画图")):
        return 1
    if len(text) >= 480 or any(token in text for token in ("综合与实践", "【探究】", "【模型", "【定义】")):
        return 1
    if "解答" in q_type:
        return 3
    if any(token in q_type for token in ("选择", "填空")) and len(text) <= 320:
        return 5
    return 3


def _finalize_batch_results(
    service: AITaggingService,
    batch_items: list[tuple[int, TaggingContext]],
    raw_results: dict[int, AITaggingResult],
    *,
    request_controller: "_TaggingRequestController | None" = None,
    quality_retry_limit: int = 1,
    enable_review: bool = True,
    taxonomy_contracts: Mapping[int, Mapping[str, Any]] | None = None,
    images: Mapping[int, Sequence[Any]] | None = None,
) -> dict[int, AITaggingResult]:
    contexts = dict(batch_items)
    resolved_contracts = (
        taxonomy_contracts
        if taxonomy_contracts is not None
        else {
            question_id: service.taxonomy_contract(context)
            for question_id, context in batch_items
        }
    )
    question_images = dict(images or {})
    final: dict[int, AITaggingResult] = {}
    for qid, context in contexts.items():
        taxonomy_contract = resolved_contracts[qid]
        result = _with_quality(raw_results.get(qid) or AITaggingResult(
            ok=False,
            mock_mode=service.mock_mode,
            error="AI response missing",
            model_name=service.model,
            quality_status="invalid",
        ),
            context,
            governance=service.taxonomy_governance,
            taxonomy_contract=taxonomy_contract,
            question_ref=str(qid),
        )
        retries_remaining = max(0, int(quality_retry_limit))
        while retries_remaining > 0 and (
            (result.analysis is not None and result.quality_status == "invalid")
            or (result.analysis is None and not result.ok)
        ):
            if request_controller is not None:
                request_controller.begin("quality_retry", (qid,))
            retry = _with_quality(
                _analyze_question_with_contract(
                    service,
                    context,
                    taxonomy_contract,
                    images=question_images.get(qid, ()),
                ),
                context,
                governance=service.taxonomy_governance,
                taxonomy_contract=taxonomy_contract,
                question_ref=str(qid),
            )
            if _is_better_quality(retry, result):
                result = retry
            retries_remaining -= 1
        if (
            enable_review
            and result.analysis is not None
            and result.quality_status in {"low_confidence", "conflict"}
        ):
            result = _review_low_confidence_result(
                service,
                context,
                result,
                request_controller=request_controller,
                question_id=qid,
                taxonomy_contract=taxonomy_contract,
                images=question_images.get(qid, ()),
            )
        final[qid] = result
    return final


def _with_quality(
    result: AITaggingResult,
    context: TaggingContext,
    *,
    governance: Any | None = None,
    taxonomy_contract: Mapping[str, Any] | None = None,
    question_ref: str | None = None,
) -> AITaggingResult:
    contract = _resolve_prompt_contract(taxonomy_contract, context)
    revision = _taxonomy_revision(contract)
    if not result.ok or result.analysis is None:
        notes = list(result.quality_notes or [])
        if result.error:
            notes.append(result.error)
        return AITaggingResult(
            ok=result.ok,
            mock_mode=result.mock_mode,
            analysis=result.analysis,
            error=result.error,
            model_name=result.model_name,
            quality_status="invalid",
            quality_notes=notes,
            proposals=list(result.proposals or []),
            retrieval_misses=list(result.retrieval_misses or []),
            taxonomy_revision=revision or int(result.taxonomy_revision or 0),
        )
    taxonomy = governance or get_taxonomy_governance()
    resolved_analysis = _resolve_controlled_ids(result.analysis, contract)
    violation_notes = _controlled_field_violation_notes(
        resolved_analysis,
        contract,
    )
    raw_payload = resolved_analysis.to_dict()
    if result.proposals:
        raw_payload["proposed_tags"] = [
            *raw_payload.get("proposed_tags", []),
            *result.proposals,
        ]
    constrained = taxonomy.constrain(
        raw_payload,
        context={
            **_taxonomy_context(context),
            "persist_proposals": False,
            "question_ref": str(question_ref or ""),
            "model": str(result.model_name or ""),
            "expected_revision": revision,
            "allowed_term_ids": contract.get("allowed_term_ids", {}),
            "knowledge_catalog_revision": contract.get(
                "knowledge_catalog_revision"
            ),
        },
    )
    normalized_analysis, proposals, governance_status, governance_notes = (
        _analysis_from_constraint(
            resolved_analysis,
            constrained,
            fallback_revision=revision,
        )
    )
    governance_notes = [
        *violation_notes,
        *governance_notes,
    ]
    status, notes, confidence = _evaluate_analysis_quality(
        normalized_analysis,
        context,
        proposals=proposals,
        governance_status=governance_status,
        governance_notes=governance_notes,
    )
    payload = normalized_analysis.to_dict()
    payload["confidence"] = confidence
    payload["taxonomy_revision"] = (
        int(normalized_analysis.taxonomy_revision) or revision
    )
    retrieval_misses = [
        dict(item)
        for item in constrained.get("retrieval_misses", [])
        if isinstance(item, Mapping)
    ]
    return AITaggingResult(
        ok=True,
        mock_mode=result.mock_mode,
        analysis=TagAnalysis.from_dict(payload),
        error=result.error,
        model_name=result.model_name,
        quality_status=status,
        quality_notes=notes,
        proposals=proposals,
        retrieval_misses=retrieval_misses,
        taxonomy_revision=int(payload["taxonomy_revision"]),
    )


def converge_tag_analysis(
    analysis: TagAnalysis,
    context: TaggingContext,
    *,
    governance: Any | None = None,
    taxonomy_contract: Mapping[str, Any] | None = None,
    question_ref: str | None = None,
    model_name: str | None = None,
) -> AITaggingResult:
    """Run the established read-only taxonomy convergence for one tag result.

    This public seam is shared by ordinary question-bank tagging and deferred
    configuration analysis.  It intentionally never persists proposals;
    persistence remains the responsibility of a writer that already has a
    stable question-bank identity.
    """

    return _with_quality(
        AITaggingResult(
            ok=True,
            mock_mode=False,
            analysis=analysis,
            model_name=str(model_name or "").strip() or None,
        ),
        context,
        governance=governance,
        taxonomy_contract=taxonomy_contract,
        question_ref=question_ref,
    )


def _evaluate_analysis_quality(
    analysis: TagAnalysis,
    context: TaggingContext,
    *,
    proposals: list[dict[str, Any]] | None = None,
    governance_status: str = "complete",
    governance_notes: list[str] | None = None,
) -> tuple[str, list[str], float]:
    notes: list[str] = list(governance_notes or [])
    pending_proposals = list(proposals or [])
    proposal_dimensions = {
        str(item.get("dimension") or "").strip().casefold()
        for item in pending_proposals
        if isinstance(item, Mapping)
    }
    missing = []
    if not analysis.ability_tags and "ability" not in proposal_dimensions:
        missing.append("缺少能力标签")
    if analysis.difficulty is None:
        missing.append("难度必须是 1 至 10 的整数")
    if not analysis.part_features:
        missing.append("缺少逐小问难度特征")
    elif context.evidence_parts:
        expected = [str(item.get("part_id") or "").strip() for item in context.evidence_parts if isinstance(item, Mapping)]
        actual = [str(item.get("part_id") or "").strip() for item in analysis.part_features]
        if not all(expected) or len(actual) != len(set(actual)) or set(actual) != set(expected):
            missing.append("逐小问难度特征与当前判定点小问不完整对应（缺问、错号或重复号）")
    confidence = float(analysis.confidence)
    if context.has_images or "[[IMAGE:" in str(context.question_text or ""):
        confidence *= 0.95
        notes.append("图片依赖题已轻微降权")
    if missing:
        notes.extend(missing)
        return "invalid", notes, round(min(confidence * 0.4, REVIEW_CONFIDENCE_THRESHOLD - 0.01), 4)
    if governance_status == "invalid":
        notes.append("词表约束结果无效")
        return "invalid", _ordered_unique(notes), round(
            min(confidence * 0.4, REVIEW_CONFIDENCE_THRESHOLD - 0.01),
            4,
        )
    # 置信度只记录不拦截：低置信与图片降权均不影响保存，只有结构性缺失
    # （能力/难度/逐小问特征）或词表治理 invalid 才判 invalid。
    if pending_proposals or governance_status == "needs_review":
        notes.append("包含未入词表的新标签，已转入人工审核")
        return "needs_review", _ordered_unique(notes), round(
            min(1.0, confidence),
            4,
        )
    return "complete", notes, round(min(1.0, confidence), 4)


def _analysis_from_constraint(
    original: TagAnalysis,
    constrained: Mapping[str, Any],
    *,
    fallback_revision: int,
) -> tuple[TagAnalysis, list[dict[str, Any]], str, list[str]]:
    payload = original.to_dict()
    accepted_fields = constrained.get("accepted_fields")
    if isinstance(accepted_fields, Mapping):
        for field_name in _CONTROLLED_ID_FIELD_DIMENSIONS:
            values = accepted_fields.get(field_name)
            payload[field_name] = list(values) if isinstance(values, list) else []
    proposals = [
        dict(item)
        for item in constrained.get("proposals", [])
        if isinstance(item, Mapping)
    ]
    revision = _coerce_nonnegative_int(
        constrained.get("taxonomy_revision"),
        fallback=fallback_revision,
    )
    payload["taxonomy_revision"] = revision
    payload["proposed_tags"] = proposals
    analysis = TagAnalysis.from_dict(payload)
    status = str(constrained.get("status") or "complete").strip().casefold()
    raw_notes = constrained.get("notes") or constrained.get("conflicts") or []
    notes = (
        [str(item).strip() for item in raw_notes if str(item).strip()]
        if isinstance(raw_notes, list)
        else []
    )
    return analysis, proposals, status, notes


def _review_low_confidence_result(
    service: AITaggingService,
    context: TaggingContext,
    primary: AITaggingResult,
    *,
    request_controller: "_TaggingRequestController | None" = None,
    question_id: int | None = None,
    taxonomy_contract: Mapping[str, Any] | None = None,
    images: Sequence[Any] = (),
) -> AITaggingResult:
    if not service.review_configured:
        return primary
    if request_controller is not None and question_id is not None:
        request_controller.begin("review", (question_id,))
    contract = dict(taxonomy_contract or service.taxonomy_contract(context))
    review = _with_quality(
        _analyze_review_with_contract(service, context, contract, images=images),
        context,
        governance=service.taxonomy_governance,
        taxonomy_contract=contract,
        question_ref=str(question_id) if question_id is not None else None,
    )
    if not review.ok or review.analysis is None or primary.analysis is None:
        return AITaggingResult(
            ok=primary.ok,
            mock_mode=primary.mock_mode,
            analysis=primary.analysis,
            error=primary.error,
            model_name=primary.model_name,
            quality_status=primary.quality_status,
            quality_notes=[*primary.quality_notes, "复核模型未返回有效结果"],
            proposals=list(primary.proposals),
            retrieval_misses=list(primary.retrieval_misses),
            taxonomy_revision=primary.taxonomy_revision,
        )
    if _analyses_agree(primary.analysis, review.analysis):
        merged = _merge_agreed_analyses(primary.analysis, review.analysis)
        merged_result = _with_quality(
            AITaggingResult(
                ok=True,
                mock_mode=primary.mock_mode,
                analysis=merged,
                model_name="+".join(item for item in (primary.model_name, review.model_name) if item),
            ),
            context,
            governance=service.taxonomy_governance,
            taxonomy_contract=contract,
            question_ref=str(question_id) if question_id is not None else None,
        )
        return AITaggingResult(
            ok=True,
            mock_mode=primary.mock_mode,
            analysis=merged_result.analysis,
            model_name=merged_result.model_name,
            quality_status=merged_result.quality_status,
            quality_notes=_ordered_unique(
                [
                    *primary.quality_notes,
                    *merged_result.quality_notes,
                    "复核模型与主模型核心标签一致",
                ]
            ),
            proposals=list(merged_result.proposals),
            retrieval_misses=list(merged_result.retrieval_misses),
            taxonomy_revision=merged_result.taxonomy_revision,
        )
    return AITaggingResult(
        ok=True,
        mock_mode=primary.mock_mode,
        analysis=primary.analysis,
        model_name=primary.model_name,
        quality_status="conflict",
        quality_notes=[*primary.quality_notes, "复核模型与主模型核心标签冲突"],
        proposals=list(primary.proposals),
        retrieval_misses=list(primary.retrieval_misses),
        taxonomy_revision=primary.taxonomy_revision,
    )


def _analyses_agree(left: TagAnalysis, right: TagAnalysis) -> bool:
    def core_tags(analysis: TagAnalysis) -> set[str]:
        return {
            _compact(value)
            for field_name in (
                "method_tags",
                "thought_tags",
                "ability_tags",
                "math_model_tags",
                "special_type_tags",
            )
            for value in getattr(analysis, field_name, [])
            if _compact(value)
        }

    left_core, right_core = core_tags(left), core_tags(right)
    shared = left_core & right_core
    if shared:
        return True
    # 两份分析都没有任何受控标签时，按难度差判定是否一致。
    if not left_core and not right_core:
        if left.difficulty is None or right.difficulty is None:
            return False
        return abs(left.difficulty - right.difficulty) <= 2
    return False


def _merge_agreed_analyses(primary: TagAnalysis, review: TagAnalysis) -> TagAnalysis:
    payload = primary.to_dict()
    for field_name in (
        "method_tags",
        "thought_tags",
        "ability_tags",
        "math_model_tags",
        "special_type_tags",
    ):
        payload[field_name] = _ordered_unique([*primary.to_dict().get(field_name, []), *review.to_dict().get(field_name, [])])
    # 预测错法按触发位与名称合并；不同错误选项即使错法同名也必须各保留一条。
    seen_patterns = {
        (str(item.get("trigger_kind") or ""), str(item.get("trigger_value") or ""),
         str(item.get("pattern") or "").casefold())
        for item in primary.predicted_error_patterns
    }
    payload["predicted_error_patterns"] = [
        *primary.predicted_error_patterns,
        *[
            dict(item)
            for item in review.predicted_error_patterns
            if (str(item.get("trigger_kind") or ""), str(item.get("trigger_value") or ""),
                str(item.get("pattern") or "").casefold()) not in seen_patterns
        ],
    ]
    payload["proposed_tags"] = [
        *primary.proposed_tags,
        *review.proposed_tags,
    ]
    payload["confidence"] = max(primary.confidence, review.confidence, COMPLETE_CONFIDENCE_THRESHOLD)
    if not payload.get("reason") and review.reason:
        payload["reason"] = review.reason
    return TagAnalysis.from_dict(payload)


def _is_better_quality(candidate: AITaggingResult, current: AITaggingResult) -> bool:
    rank = {
        "invalid": 0,
        "conflict": 1,
        "low_confidence": 2,
        "needs_review": 3,
        "complete": 4,
    }
    return rank.get(candidate.quality_status, 0) > rank.get(current.quality_status, 0)


def _ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _compact(value: object) -> str:
    return re.sub(r"[\s\W_]+", "", str(value or "")).casefold()


def is_auto_saveable_result(result: AITaggingResult) -> bool:
    return bool(
        result.ok
        and result.analysis is not None
        and result.quality_status in {"complete", "needs_review"}
    )


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _bounded_int(value: int | None, default: int, minimum: int, maximum: int) -> int:
    try:
        resolved = int(value) if value is not None else int(default)
    except (TypeError, ValueError):
        resolved = int(default)
    return max(minimum, min(maximum, resolved))


class _RateLimiter:
    def __init__(self, requests_per_minute: int) -> None:
        self.interval_seconds = 60.0 / max(1, requests_per_minute)
        self._lock = threading.Lock()
        self._next_allowed_at = 0.0

    def acquire(self) -> None:
        wait_seconds = 0.0
        with self._lock:
            now = time.monotonic()
            wait_seconds = self._next_allowed_at - now
            if wait_seconds < 0.0:
                wait_seconds = 0.0
            self._next_allowed_at = max(now, self._next_allowed_at) + self.interval_seconds
        
        if wait_seconds > 0.0:
            time.sleep(wait_seconds)


class _TaggingRequestController:
    def __init__(
        self,
        requests_per_minute: int,
        callback: Callable[[TaggingRequestEvent], None] | None,
    ) -> None:
        self._rate_limiter = _RateLimiter(requests_per_minute)
        self._callback = callback
        self._lock = threading.Lock()
        self._request_number = 0

    def _emit(self, event: TaggingRequestEvent) -> None:
        if self._callback is not None:
            self._callback(event)

    def finish(
        self,
        started: TaggingRequestEvent,
        phase: str,
        exc: BaseException | None = None,
        *,
        model_name: str = "",
    ) -> None:
        self._emit(
            TaggingRequestEvent(
                request_number=started.request_number,
                request_kind=started.request_kind,
                question_ids=started.question_ids,
                phase=phase,
                error_category=classify_tagging_error(exc) if exc is not None else "",
                error_message=sanitize_tagging_error(exc) if exc is not None else "",
                model_name=str(model_name or ""),
            )
        )

    def begin(
        self,
        request_kind: str,
        question_ids: tuple[int, ...],
    ) -> TaggingRequestEvent:
        self._rate_limiter.acquire()
        with self._lock:
            self._request_number += 1
            event = TaggingRequestEvent(
                request_number=self._request_number,
                request_kind=str(request_kind),
                question_ids=tuple(int(question_id) for question_id in question_ids),
                phase="started",
            )
            self._emit(event)
        return event

    def run(
        self,
        request_kind: str,
        question_ids: tuple[int, ...],
        operation: Callable[[], Any],
        *,
        model_name: str = "",
    ) -> Any:
        started = self.begin(request_kind, question_ids)
        try:
            result = operation()
        except BaseException as exc:  # noqa: BLE001 - 分类后重新抛出
            self._emit(
                TaggingRequestEvent(
                    request_number=started.request_number,
                    request_kind=started.request_kind,
                    question_ids=started.question_ids,
                    phase="failed",
                    error_category=classify_tagging_error(exc),
                    error_message=sanitize_tagging_error(exc),
                    model_name=str(model_name or ""),
                )
            )
            raise
        self._emit(
            TaggingRequestEvent(
                request_number=started.request_number,
                request_kind=started.request_kind,
                question_ids=started.question_ids,
                phase="succeeded",
                model_name=str(model_name or ""),
            )
        )
        return result
