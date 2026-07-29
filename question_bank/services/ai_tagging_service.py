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

from api_profiles import get_api_profile_store
from backend.llm import (
    LLMProtocolAdapter,
    LLMRequestKind,
    execution_snapshot_from_profile,
    policy_overrides_from_profile,
)
from llm_client import LLMClient, LLMSettings, normalize_openai_base_url
from question_bank.models.tag_schema import ERROR_PRONE_CATEGORIES, TagAnalysis, TaggingContext
from question_bank.taxonomy.governance import get_taxonomy_governance
from question_bank.taxonomy.curriculum_catalog import curriculum_volume_contract
from question_bank.taxonomy.registry import canonical_knowledge_options


DEFAULT_TAGGING_MODEL = "gpt-4o"
STUDENT_LEVELS = ("入门补缺", "基础巩固", "中档提升", "综合突破", "压轴拔高")
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
# Compatibility for older configuration-analysis prompts. New question-bank
# tagging obtains its candidates exclusively from TaxonomyGovernance.
KNOWLEDGE_POINT_OPTIONS = tuple(canonical_knowledge_options())


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
    ) -> AITaggingResult:
        taxonomy_contract = dict(
            taxonomy_contract or self.taxonomy_contract(context)
        )
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
                payload = _json_from_text_compat(
                    self.llm_client,
                    _prompt_text(context, taxonomy_contract),
                    model=_model_for_llm_client(self.llm_client, self.model),
                    extra_kwargs=extra_kwargs,
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
                    "text": {"format": _tag_analysis_response_format()},
                    "input": _prompt_input(context, taxonomy_contract),
                },
            )
            output_text = str(getattr(response, "output_text", "") or "").strip()
            result = AITaggingResult(
                ok=True,
                mock_mode=False,
                analysis=TagAnalysis.from_dict(json.loads(output_text)),
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
            payload = _json_from_text_once_compat(
                self.llm_client,
                prompt,
                model=_model_for_llm_client(self.llm_client, self.model),
            )
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
            payload = json.loads(output_text)
        if not isinstance(payload, Mapping) or not isinstance(
            payload.get("results"), list
        ):
            raise ValueError("AI 归并建议返回缺少 results 列表")
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
    ) -> AITaggingResult:
        if not self.review_configured:
            return AITaggingResult(ok=False, mock_mode=False, error="未配置低置信度复核模型", model_name=self.review_model or None, quality_status="invalid")
        try:
            assert self.review_llm_client is not None
            taxonomy_contract = dict(
                taxonomy_contract or self.taxonomy_contract(context)
            )
            payload = _json_from_text_compat(
                self.review_llm_client,
                _prompt_text(context, taxonomy_contract),
                model=_model_for_llm_client(self.review_llm_client, self.review_model),
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
                adapter = LLMProtocolAdapter(
                    self.api_key,
                    self._tagging_base_url,
                    policy_profile=self._tagging_policy_profile,
                    client=self.client,
                )
                self._protocol_adapter_instance = adapter
        return adapter


def _mock_analysis(context: TaggingContext) -> TagAnalysis:
    volume = curriculum_volume_contract(context.curriculum_volume_id)
    first_section = (
        str(volume["sections"][0]["id"])
        if volume is not None and volume["sections"]
        else ""
    )
    payload = {
        "knowledge_points": ["几何综合"],
        "method_tags": ["角度转化"] if "角" in context.question_text else ["方程思想"],
        "ability_tags": ["推理能力"] if context.has_answer else ["阅读理解"],
        "math_model_tags": ["平行线角度模型"] if "平行" in context.question_text else [],
        "special_type_tags": [],
        "difficulty": 3 if context.has_answer else 2,
        "error_prone_points": ["条件转化不完整"],
        "prerequisite_points": [],
        "textbook_chapters": [
            "八年级上册 第七章 平行线的证明"
            if "平行" in context.question_text
            else "九年级上册 第四章 图形的相似"
        ],
        "curriculum_sections": [first_section] if first_section else [],
        "suitable_student_level": "",
        "canonical_knowledge_id": "kp_geo_comprehensive",
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
        return dict(taxonomy_contract)
    prompt_context = _taxonomy_context(context) if context is not None else None
    return dict(get_taxonomy_governance().prompt_contract(prompt_context))


def _taxonomy_revision(contract: Mapping[str, Any]) -> int:
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
) -> list[dict[str, str]]:
    contract = _resolve_prompt_contract(taxonomy_contract, context)
    user_payload = {
        "task": "Tag this question for a local junior math question bank.",
        "input": _prompt_question_input(context),
        "output_schema": _plain_output_schema(_taxonomy_revision(contract)),
    }
    if os.getenv("QUESTION_BANK_TAGGING_THINKING") == "1":
        user_payload["reasoning_instruction"] = _TAGGING_REASONING_INSTRUCTION
    return [
        {"role": "system", "content": _system_prompt(contract)},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def _system_prompt(
    taxonomy_contract: Mapping[str, Any] | None = None,
) -> str:
    contract = _resolve_prompt_contract(taxonomy_contract)
    contract_json = json.dumps(contract, ensure_ascii=False, separators=(",", ":"))
    return f"""
    You analyze junior middle-school math questions.
    Return one JSON object only with the requested schema.
    The local taxonomy contract below is the only source for curriculum,
    knowledge, ability, method, model, and special-type tags:
    {contract_json}

    Allowed student levels (choose exactly one): {", ".join(STUDENT_LEVELS)}

    Error-prone options (choose relevant): {", ".join(ERROR_PRONE_CATEGORIES)}

    Controlled output rules:
    - taxonomy_revision must exactly echo {int(_taxonomy_revision(contract))}.
    - knowledge_points, prerequisite_points, ability_tags, method_tags,
      math_model_tags and special_type_tags may contain only exact approved
      names from the matching contract dimension. Never place a newly coined
      or approximate term there.
    - When curriculum_volume is present in the contract, curriculum_sections
      must contain only exact stable section IDs from that volume. Return the
      smallest accurate section set. Leave textbook_chapters empty because the
      local program derives chapters from those section IDs.
    - Without curriculum_volume, textbook_chapters is a list of exact approved
      curriculum names.
    - canonical_knowledge_id must be the approved ID corresponding to the first
      knowledge_points value. If no approved knowledge term fits, return "".
    - If no approved term accurately fits, add it to proposed_tags instead.
      Each proposal must state dimension
      (curriculum/knowledge/ability/method/model/special_type),
      name, definition, reason, nearest_id, and why_not_reuse. Also leave that
      unapproved value out of every normal field.
    - proposed_tags is always present, contains at most 2 items per question,
      and uses [] when every value is approved.
    - Do not output teaching_stage, sub_skills, measured_skills, or supporting_skills.

    Error-prone points must be broad, reusable categories for statistics, not question-specific step descriptions. Prefer the provided error_prone_options such as 条件识别不完整, 图形关系识别错误, 辅助线思路缺失, 公式/定理误用, 运算化简错误, 书写依据不完整. Do not write labels like “第一问证明某三角形全等时漏找某条件”.
    Choose the smallest accurate approved knowledge point.
    Difficulty must be an integer from 1 to 10.
    input.has_images is metadata only: it tells you the stored question contains images, but no image body is included in this tagging request.
    Historical saved tags are intentionally absent from the input and must not
    be inferred or preserved. Judge this question from its current content and
    the current taxonomy contract only.
    confidence must be a number from 0 to 1 for your overall confidence in the tag set. Lower it when the image is essential, the answer is missing, or the core knowledge point is uncertain.
    suitable_student_level is retained only for response compatibility and must be "".
    """.strip()


def _plain_output_schema(taxonomy_revision: int = 0) -> dict[str, object]:
    return {
        "knowledge_points": [],
        "method_tags": [],
        "ability_tags": [],
        "math_model_tags": [],
        "special_type_tags": [],
        "difficulty": 1,
        "error_prone_points": [],
        "prerequisite_points": [],
        "textbook_chapters": [],
        "curriculum_sections": [],
        "suitable_student_level": "",
        "canonical_knowledge_id": "",
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
        "maxItems": 2,
        "items": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


def _tag_analysis_response_format() -> dict[str, Any]:
    array_field = {"type": "array", "items": {"type": "string"}}
    score_field = {"type": "integer", "minimum": 1, "maximum": 10}
    text_field = {"type": "string"}
    properties = {
        "knowledge_points": array_field,
        "method_tags": array_field,
        "ability_tags": array_field,
        "math_model_tags": array_field,
        "special_type_tags": array_field,
        "difficulty": score_field,
        "error_prone_points": array_field,
        "prerequisite_points": array_field,
        "textbook_chapters": array_field,
        "curriculum_sections": array_field,
        "suitable_student_level": text_field,
        "canonical_knowledge_id": text_field,
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
    for raw in list(batch)[:20]:
        if not isinstance(raw, Mapping):
            continue
        candidates = []
        for candidate in list(raw.get("candidates") or [])[:12]:
            if not isinstance(candidate, Mapping):
                continue
            candidates.append(
                {
                    "id": _limited_text(candidate.get("id"), 100),
                    "name": _limited_text(candidate.get("name"), 160),
                }
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
                "candidates": candidates,
                "question_summaries": summaries,
            }
        )
    system_prompt = """
    You assist a teacher in reviewing newly coined taxonomy terms for a
    junior-middle-school mathematics question bank. Return suggestions only;
    never claim that a proposal has been approved or write any data.

    Prefer an existing candidate whenever its meaning fits the actual question.
    Use merge with exactly one candidate ID for an equivalent term. Use
    map_many only when the evidence genuinely spans two or more independent
    approved terms (especially multiple textbook chapters). Use approve only
    for a genuinely reusable new controlled term, reject for an unsuitable
    label, and uncertain when the summaries are insufficient. Target IDs must
    come from that proposal's candidates. Do not concatenate multiple chapter
    names into a new term. Return one result for every proposal ID.
    """.strip()
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "Suggest teacher-review decisions for these pending taxonomy terms.",
                    "proposals": safe_batch,
                },
                ensure_ascii=False,
            ),
        },
    ]


def _taxonomy_suggestion_response_format() -> dict[str, Any]:
    properties = {
        "proposal_id": {"type": "string"},
        "decision": {
            "type": "string",
            "enum": [
                "merge",
                "map_many",
                "approve",
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
    profiles = get_api_profile_store().load()
    return dict(profiles[-1]) if profiles else {}


def _model_for_llm_client(llm_client: Any, fallback: str) -> str:
    settings = getattr(llm_client, "settings", None)
    return str(getattr(settings, "config_model", "") or fallback)


def _json_from_text_compat(
    llm_client: Any,
    prompt: str,
    *,
    model: str | None = None,
    extra_kwargs: dict[str, Any] | None = None,
) -> Any:
    call_kwargs: dict[str, Any] = {
        "model": model,
        "request_kind": LLMRequestKind.TAGGING,
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


def _json_from_text_once_compat(
    llm_client: Any,
    prompt: str,
    *,
    model: str | None = None,
) -> Any:
    """Use the strict one-request interface; never fall back to AI repair."""

    method = getattr(llm_client, "json_from_text_once", None)
    if not callable(method):
        raise RuntimeError("AI 归并建议需要单次请求 JSON 接口")
    call_kwargs: dict[str, Any] = {
        "model": model,
        "request_kind": LLMRequestKind.TAGGING,
    }
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
) -> AITaggingResult:
    try:
        return service.analyze_question(
            context,
            taxonomy_contract=taxonomy_contract,
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
) -> AITaggingResult:
    try:
        return service.analyze_review_question(
            context,
            taxonomy_contract=taxonomy_contract,
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
    
    CRITICAL: You are analyzing a BATCH of junior middle-school math questions.
    You must analyze each question in the batch and return the list of analyses under the "results" key in your JSON response.
    Each analysis object in "results" must include the exact "question_id" that was provided in the input.
    Every batch input contains its own candidate_contract. For that question,
    only that contract's candidates may be placed in normal controlled fields.
    Do not borrow a candidate from another question in the same batch.
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
    return {
        "schema_version": 1,
        "taxonomy_revision": next(iter(revisions), 0),
        "allowed_dimensions": list(first.get("allowed_dimensions", [])),
        "rules": {
            "selection": (
                "Use only the candidate_contract attached to the current question."
            ),
            "unknown": "Return at most 2 proposed_tags for the current question.",
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


def _batch_prompt_input(
    batch_contexts: list[tuple[int, TaggingContext]],
    taxonomy_contracts: Mapping[int, Mapping[str, Any]]
    | Mapping[str, Any]
    | None = None,
) -> list[dict[str, str]]:
    taxonomy_contracts = _normalize_batch_contracts(
        batch_contexts,
        taxonomy_contracts,
    )
    contract = _batch_shared_contract(batch_contexts, taxonomy_contracts)
    input_payloads = []
    for question_id, context in batch_contexts:
        input_payloads.append({
            "question_id": question_id,
            "input": _prompt_question_input(context),
            "candidate_contract": dict(taxonomy_contracts[question_id]),
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
    if os.getenv("QUESTION_BANK_TAGGING_THINKING") == "1":
        user_payload["reasoning_instruction"] = _TAGGING_REASONING_INSTRUCTION
    
    return [
        {"role": "system", "content": _batch_system_prompt(contract)},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def _batch_tag_analysis_response_format() -> dict[str, Any]:
    array_field = {"type": "array", "items": {"type": "string"}}
    score_field = {"type": "number"}
    text_field = {"type": "string"}
    question_analysis_properties = {
        "question_id": {"type": "integer"},
        "knowledge_points": array_field,
        "method_tags": array_field,
        "ability_tags": array_field,
        "math_model_tags": array_field,
        "special_type_tags": array_field,
        "difficulty": score_field,
        "error_prone_points": array_field,
        "prerequisite_points": array_field,
        "textbook_chapters": array_field,
        "curriculum_sections": array_field,
        "suitable_student_level": text_field,
        "canonical_knowledge_id": text_field,
        "taxonomy_revision": {"type": "integer"},
        "proposed_tags": _proposal_response_schema(),
        "reason": text_field,
        "confidence": {"type": "number"},
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
) -> dict[int, AITaggingResult]:
    taxonomy_contracts = _normalize_batch_contracts(
        batch_items,
        taxonomy_contracts,
    )
    taxonomy_contract = _batch_shared_contract(
        batch_items,
        taxonomy_contracts,
    )
    if service.mock_mode:
        return _finalize_batch_results(
            service,
            batch_items,
            _mock_batch_analysis(batch_items),
            taxonomy_contracts=taxonomy_contracts,
        )

    _batch_started = request_controller.begin("batch", tuple(qid for qid, _context in batch_items))

    try:
        if service.llm_client is not None:
            # Format prompt for llm_client
            batch_inputs = [
                {
                    "question_id": qid,
                    "input": _prompt_question_input(ctx),
                    "candidate_contract": dict(taxonomy_contracts[qid]),
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
            if os.getenv("QUESTION_BANK_TAGGING_THINKING") == "1":
                user_payload["reasoning_instruction"] = (
                    _TAGGING_REASONING_INSTRUCTION
                )
            prompt = (
                f"{_batch_system_prompt(taxonomy_contract)}\n\n"
                f"{json.dumps(user_payload, ensure_ascii=False)}"
            )
            
            thinking_enabled = os.getenv("QUESTION_BANK_TAGGING_THINKING") == "1"
            extra_kwargs = {"thinking": True} if thinking_enabled else None
            
            payload = _json_from_text_compat(
                service.llm_client,
                prompt,
                model=_model_for_llm_client(service.llm_client, service.model),
                extra_kwargs=extra_kwargs,
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
            )

        # Standard OpenAI-style / Google Responses API client
        response = service._protocol_adapter().responses(
            request_kind=LLMRequestKind.TAGGING,
            model=service.model,
            kwargs={
                "text": {"format": _batch_tag_analysis_response_format()},
                "input": _batch_prompt_input(batch_items, taxonomy_contracts),
            },
        )
        output_text = str(getattr(response, "output_text", "") or "").strip()
        payload = json.loads(output_text)
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
        while (
            result.analysis is not None
            and result.quality_status == "invalid"
            and retries_remaining > 0
        ):
            if request_controller is not None:
                request_controller.begin("quality_retry", (qid,))
            retry = _with_quality(
                _analyze_question_with_contract(
                    service,
                    context,
                    taxonomy_contract,
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
    scoped_analysis, curriculum_notes = _normalize_scoped_curriculum(
        result.analysis,
        contract,
    )
    raw_payload = scoped_analysis.to_dict()
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
        },
    )
    normalized_analysis, proposals, governance_status, governance_notes = (
        _analysis_from_constraint(
            scoped_analysis,
            constrained,
            fallback_revision=revision,
        )
    )
    governance_notes = [*curriculum_notes, *governance_notes]
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


def _normalize_scoped_curriculum(
    analysis: TagAnalysis,
    contract: Mapping[str, Any],
) -> tuple[TagAnalysis, list[str]]:
    volume = contract.get("curriculum_volume")
    if not isinstance(volume, Mapping):
        return analysis, []
    raw_sections = volume.get("sections")
    section_rows = (
        [item for item in raw_sections if isinstance(item, Mapping)]
        if isinstance(raw_sections, list)
        else []
    )
    by_id = {
        str(item.get("id") or "").strip(): item
        for item in section_rows
        if str(item.get("id") or "").strip()
    }
    selected_ids: list[str] = []
    invalid_ids: list[str] = []
    for raw in analysis.curriculum_sections:
        section_id = str(raw or "").strip()
        if not section_id or section_id in selected_ids:
            continue
        if section_id not in by_id:
            invalid_ids.append(section_id)
            continue
        selected_ids.append(section_id)
    if invalid_ids:
        selected_ids = []
    chapters: list[str] = []
    for section_id in selected_ids:
        chapter_name = str(by_id[section_id].get("chapter_name") or "").strip()
        if chapter_name and chapter_name not in chapters:
            chapters.append(chapter_name)
    payload = analysis.to_dict()
    payload["curriculum_sections"] = selected_ids
    payload["textbook_chapters"] = chapters
    payload["textbook_chapter"] = chapters[0] if chapters else ""
    notes: list[str] = []
    if invalid_ids:
        notes.append(
            "教材小节超出老师确认的册别范围："
            + "、".join(invalid_ids[:3])
        )
    if not selected_ids:
        notes.append("未返回当前册别中的教材小节")
    return TagAnalysis.from_dict(payload), notes


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
    if not analysis.knowledge_points and "knowledge" not in proposal_dimensions:
        missing.append("缺少知识点")
    if not analysis.ability_tags and "ability" not in proposal_dimensions:
        missing.append("缺少能力标签")
    if not analysis.textbook_chapters and "curriculum" not in proposal_dimensions:
        missing.append("缺少教材章节")
    if context.curriculum_volume_id and not analysis.curriculum_sections:
        missing.append("缺少教材小节")
    if analysis.difficulty is None:
        missing.append("难度必须是 1 至 10 的整数")
    confidence = float(analysis.confidence)
    if context.has_images or "[[IMAGE:" in str(context.question_text or ""):
        confidence *= 0.95
        notes.append("图片依赖题已轻微降权")
    rule_conflict_notes = _rule_conflict_notes(context, analysis)
    notes.extend(rule_conflict_notes)
    if missing:
        notes.extend(missing)
        return "invalid", notes, round(min(confidence * 0.4, REVIEW_CONFIDENCE_THRESHOLD - 0.01), 4)
    if governance_status == "invalid":
        notes.append("词表约束结果无效")
        return "invalid", _ordered_unique(notes), round(
            min(confidence * 0.4, REVIEW_CONFIDENCE_THRESHOLD - 0.01),
            4,
        )
    if confidence < REVIEW_CONFIDENCE_THRESHOLD:
        notes.append("AI 自评置信度过低")
        return "invalid", notes, round(confidence, 4)
    if rule_conflict_notes:
        return "conflict", notes, round(min(confidence * 0.75, COMPLETE_CONFIDENCE_THRESHOLD - 0.03), 4)
    if pending_proposals or governance_status == "needs_review":
        notes.append("包含未入词表的新标签，已转入人工审核")
        return "needs_review", _ordered_unique(notes), round(
            min(1.0, confidence),
            4,
        )
    if confidence < COMPLETE_CONFIDENCE_THRESHOLD:
        notes.append("置信度低，需复核或人工确认")
        return "low_confidence", notes, round(confidence, 4)
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
        for field_name in (
            "knowledge_points",
            "prerequisite_points",
            "method_tags",
            "ability_tags",
            "math_model_tags",
            "special_type_tags",
        ):
            values = accepted_fields.get(field_name)
            payload[field_name] = list(values) if isinstance(values, list) else []
        curriculum_values = (
            accepted_fields.get("textbook_chapters")
            or accepted_fields.get("textbook_chapter")
        )
        payload["textbook_chapters"] = (
            list(curriculum_values) if isinstance(curriculum_values, list) else []
        )
    else:
        accepted = constrained.get("accepted_analysis")
        if isinstance(accepted, Mapping):
            field_by_dimension = {
                "knowledge": "knowledge_points",
                "method": "method_tags",
                "ability": "ability_tags",
                "model": "math_model_tags",
                "special_type": "special_type_tags",
                "curriculum": "textbook_chapters",
            }
            for dimension, field_name in field_by_dimension.items():
                values = accepted.get(dimension)
                payload[field_name] = list(values) if isinstance(values, list) else []
        else:
            payload["knowledge_points"] = []
            payload["method_tags"] = []
            payload["ability_tags"] = []
            payload["math_model_tags"] = []
            payload["special_type_tags"] = []
            payload["textbook_chapters"] = []
        payload["prerequisite_points"] = []
    payload["canonical_knowledge_id"] = ""
    accepted_terms = constrained.get("accepted_terms")
    knowledge_values = payload.get("knowledge_points")
    if (
        isinstance(accepted_terms, Mapping)
        and isinstance(knowledge_values, list)
        and knowledge_values
    ):
        primary_name = str(knowledge_values[0] or "").strip()
        knowledge_terms = accepted_terms.get("knowledge")
        if isinstance(knowledge_terms, list):
            primary_term = next(
                (
                    item
                    for item in knowledge_terms
                    if isinstance(item, Mapping)
                    and str(item.get("name") or "").strip() == primary_name
                ),
                None,
            )
            if isinstance(primary_term, Mapping):
                payload["canonical_knowledge_id"] = str(
                    primary_term.get("id") or ""
                ).strip()
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
    payload["teaching_stage"] = ""
    payload["sub_skills"] = []
    payload["measured_skills"] = []
    payload["supporting_skills"] = []
    analysis = TagAnalysis.from_dict(payload)
    status = str(constrained.get("status") or "complete").strip().casefold()
    raw_notes = constrained.get("notes") or constrained.get("conflicts") or []
    notes = (
        [str(item).strip() for item in raw_notes if str(item).strip()]
        if isinstance(raw_notes, list)
        else []
    )
    return analysis, proposals, status, notes


def _rule_conflict_notes(context: TaggingContext, analysis: TagAnalysis) -> list[str]:
    text = _compact(str(context.question_text or ""))
    knowledge = _compact(" ".join(analysis.knowledge_points))
    notes: list[str] = []
    if ("科学记数法" in text or re.search(r"0\.0{3,}\d", text)) and "有理数" not in knowledge and "科学记数法" not in knowledge:
        notes.append("疑似科学记数法题，主知识点未指向有理数/科学记数法")
    if ("第三边" in text or "两条边" in text) and "三角形全等" in knowledge and "三边" not in knowledge:
        notes.append("疑似三角形三边关系题，不应泛化为三角形全等")
    if ("角平分线" in text or "平分∠" in text) and "角平分线" not in knowledge and "轴对称" not in knowledge:
        notes.append("疑似角平分线性质题，知识点可能偏泛")
    return notes


def _review_low_confidence_result(
    service: AITaggingService,
    context: TaggingContext,
    primary: AITaggingResult,
    *,
    request_controller: "_TaggingRequestController | None" = None,
    question_id: int | None = None,
    taxonomy_contract: Mapping[str, Any] | None = None,
) -> AITaggingResult:
    if not service.review_configured:
        return primary
    if request_controller is not None and question_id is not None:
        request_controller.begin("review", (question_id,))
    contract = dict(taxonomy_contract or service.taxonomy_contract(context))
    review = _with_quality(
        _analyze_review_with_contract(service, context, contract),
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
    left_knowledge = {_compact(item) for item in left.knowledge_points if _compact(item)}
    right_knowledge = {_compact(item) for item in right.knowledge_points if _compact(item)}
    if not left_knowledge.intersection(right_knowledge):
        return False
    left_chapters = {
        _compact(item) for item in left.textbook_chapters if _compact(item)
    }
    right_chapters = {
        _compact(item) for item in right.textbook_chapters if _compact(item)
    }
    return (
        not left_chapters
        or not right_chapters
        or bool(left_chapters.intersection(right_chapters))
    )


def _merge_agreed_analyses(primary: TagAnalysis, review: TagAnalysis) -> TagAnalysis:
    payload = primary.to_dict()
    for field_name in (
        "knowledge_points",
        "method_tags",
        "ability_tags",
        "math_model_tags",
        "special_type_tags",
        "error_prone_points",
        "prerequisite_points",
        "textbook_chapters",
    ):
        payload[field_name] = _ordered_unique([*primary.to_dict().get(field_name, []), *review.to_dict().get(field_name, [])])
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
