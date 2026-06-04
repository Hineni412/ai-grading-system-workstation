from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from api_profiles import load_api_profiles
from llm_client import LLMClient, LLMSettings, normalize_openai_base_url
from question_bank.database.paths import project_data_root
from question_bank.models.tag_schema import ERROR_PRONE_CATEGORIES, TagAnalysis, TaggingContext


DEFAULT_TAGGING_MODEL = "gpt-4o"
STUDENT_LEVELS = ("入门补缺", "基础巩固", "中档提升", "综合突破", "压轴拔高")
KNOWLEDGE_POINT_OPTIONS = (
    "有理数运算",
    "整式运算",
    "因式分解",
    "分式方程",
    "一元一次方程",
    "二元一次方程组",
    "一元一次不等式组",
    "平面直角坐标系",
    "一次函数",
    "反比例函数",
    "二次函数",
    "函数图像",
    "相交线与平行线",
    "三角形全等",
    "等腰三角形",
    "勾股定理",
    "四边形",
    "特殊平行四边形",
    "图形的平移与旋转",
    "轴对称",
    "图形相似",
    "圆",
    "锐角三角函数",
    "概率初步",
    "数据分析",
    "几何综合",
)
METHOD_TAG_OPTIONS = (
    "方程思想",
    "数形结合",
    "分类讨论",
    "转化与化归",
    "整体思想",
    "待定系数法",
    "配方法",
    "换元法",
    "构造辅助线",
    "构造全等",
    "构造相似",
    "角度转化",
    "面积法",
    "反证法",
    "函数思想",
    "模型思想",
)
ABILITY_TAG_OPTIONS = (
    "运算能力",
    "几何直观",
    "推理能力",
    "抽象能力",
    "模型观念",
    "空间观念",
    "数据观念",
    "应用意识",
    "创新意识",
    "阅读理解",
)
MATH_MODEL_OPTIONS = (
    "平行线角度模型",
    "角平分线模型",
    "中点模型",
    "倍长中线模型",
    "中位线模型",
    "一线三等角模型",
    "一线三垂直模型",
    "手拉手模型",
    "半角模型",
    "倍角模型",
    "旋转模型",
    "折叠模型",
    "将军饮马模型",
    "费马点模型",
    "隐圆模型",
    "胡不归模型",
    "阿氏圆模型",
    "瓜豆模型",
    "弦图模型",
    "相似三角形模型",
    "面积等积模型",
)
CURRICULUM_CHAPTERS = (
    "七年级上册 第一章 丰富的图形世界",
    "七年级上册 第二章 有理数及其运算",
    "七年级上册 第三章 字母表示数",
    "七年级上册 第四章 基本平面图形",
    "七年级上册 第五章 一元一次方程",
    "七年级上册 第六章 丰富的数据世界",
    "七年级下册 第一章 整式的乘除",
    "七年级下册 第二章 相交线与平行线",
    "七年级下册 第三章 变量之间的关系",
    "七年级下册 第四章 三角形",
    "七年级下册 第五章 生活中的轴对称",
    "七年级下册 第六章 概率初步",
    "八年级上册 第一章 勾股定理",
    "八年级上册 第二章 实数",
    "八年级上册 第三章 位置与坐标",
    "八年级上册 第四章 一次函数",
    "八年级上册 第五章 二元一次方程组",
    "八年级上册 第六章 数据的分析",
    "八年级上册 第七章 平行线的证明",
    "八年级下册 第一章 三角形的证明",
    "八年级下册 第二章 一元一次不等式与一元一次不等式组",
    "八年级下册 第三章 图形的平移与旋转",
    "八年级下册 第四章 因式分解",
    "八年级下册 第五章 分式与分式方程",
    "八年级下册 第六章 平行四边形",
    "九年级上册 第一章 特殊平行四边形",
    "九年级上册 第二章 一元二次方程",
    "九年级上册 第三章 概率的进一步认识",
    "九年级上册 第四章 图形的相似",
    "九年级上册 第五章 投影与视图",
    "九年级上册 第六章 反比例函数",
    "九年级下册 第一章 直角三角形的边角关系",
    "九年级下册 第二章 二次函数",
    "九年级下册 第三章 圆",
)


@dataclass(frozen=True)
class AITaggingResult:
    ok: bool
    mock_mode: bool
    analysis: TagAnalysis | None = None
    error: str | None = None


class AITaggingService:
    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        client: Any | None = None,
        llm_client: Any | None = None,
    ) -> None:
        self.env = dict(_dotenv_values())
        self.env.update(dict(os.environ if env is None else env))
        self.api_key = str(self.env.get("QUESTION_BANK_TAGGING_API_KEY") or self.env.get("OPENAI_API_KEY") or "").strip()
        self.model = str(self.env.get("QUESTION_BANK_TAGGING_MODEL") or DEFAULT_TAGGING_MODEL).strip()
        self.client = client
        
        # Build a dedicated LLM client specifically for tagging if configured
        tagging_api_key = str(self.env.get("QUESTION_BANK_TAGGING_API_KEY") or "").strip()
        if tagging_api_key:
            tagging_base_url = str(self.env.get("QUESTION_BANK_TAGGING_BASE_URL") or "https://api.openai.com/v1").strip()
            from llm_client import LLMSettings, LLMClient
            settings = LLMSettings(
                api_key=tagging_api_key,
                base_url=tagging_base_url,
                ocr_model=self.model,
                grading_model=self.model,
                config_model=self.model,
                config_api_key=tagging_api_key,
                config_base_url=tagging_base_url
            )
            self.llm_client = LLMClient(settings)
        else:
            self.llm_client = llm_client or (_llm_client_from_saved_profile(self.env) if env is None else None)

    @property
    def mock_mode(self) -> bool:
        return not bool(self.api_key or self.llm_client)

    def analyze_question(self, context: TaggingContext) -> AITaggingResult:
        if self.mock_mode:
            return AITaggingResult(ok=True, mock_mode=True, analysis=_mock_analysis(context))
        try:
            if self.llm_client is not None:
                thinking_enabled = os.getenv("QUESTION_BANK_TAGGING_THINKING") == "1"
                extra_kwargs = {"thinking": True} if thinking_enabled else None
                payload = _json_from_text_compat(
                    self.llm_client,
                    _prompt_text(context),
                    model=_model_for_llm_client(self.llm_client, self.model),
                    extra_kwargs=extra_kwargs,
                )
                return AITaggingResult(
                    ok=True,
                    mock_mode=False,
                    analysis=TagAnalysis.from_dict(payload),
                )
            response = self._client().responses.create(
                model=self.model,
                text={"format": _tag_analysis_response_format()},
                input=_prompt_input(context),
            )
            output_text = str(getattr(response, "output_text", "") or "").strip()
            return AITaggingResult(
                ok=True,
                mock_mode=False,
                analysis=TagAnalysis.from_dict(json.loads(output_text)),
            )
        except Exception as exc:  # noqa: BLE001
            return AITaggingResult(ok=False, mock_mode=False, error=str(exc))

    def analyze_questions(
        self,
        contexts: Mapping[int, TaggingContext],
        *,
        max_workers: int | None = None,
        requests_per_minute: int | None = None,
        progress_callback: Callable[[int, int, int, AITaggingResult], None] | None = None,
    ) -> dict[int, AITaggingResult]:
        items = list(contexts.items())
        if not items:
            return {}
            
        # Group items into batches of 5
        batch_size = 5
        batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
        
        worker_count = _bounded_int(
            max_workers,
            _env_int("QUESTION_BANK_TAGGING_MAX_WORKERS", _env_int("AI_GRADING_MAX_WORKERS", 8)),
            1,
            min(max(len(batches), 1), 64),
        )
        rpm_limit = _bounded_int(
            requests_per_minute,
            _env_int("QUESTION_BANK_TAGGING_REQUESTS_PER_MINUTE", _env_int("AI_GRADING_REQUESTS_PER_MINUTE", 1000)),
            1,
            10000,
        )
        rate_limiter = _RateLimiter(rpm_limit)
        results: dict[int, AITaggingResult] = {}
        completed = 0
        total = len(items)
        
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="qb-tagging") as executor:
            future_map = {
                executor.submit(_analyze_one_batch, self, batch, rate_limiter): batch
                for batch in batches
            }
            for future in as_completed(future_map):
                batch = future_map[future]
                try:
                    batch_results = future.result()
                except Exception as exc:
                    batch_results = {
                        qid: AITaggingResult(ok=False, mock_mode=self.mock_mode, error=str(exc))
                        for qid, _ in batch
                    }
                
                for qid, result in batch_results.items():
                    results[qid] = result
                    completed += 1
                    if progress_callback is not None:
                        progress_callback(completed, total, qid, result)
                        
        return results

    def _client(self):
        if self.client is None:
            from openai import OpenAI
            base_url = self.env.get("QUESTION_BANK_TAGGING_BASE_URL") or self.env.get("LLM_BASE_URL")
            if base_url:
                self.client = OpenAI(api_key=self.api_key, base_url=base_url)
            else:
                self.client = OpenAI(api_key=self.api_key)
        return self.client


def _mock_analysis(context: TaggingContext) -> TagAnalysis:
    payload = {
        "knowledge_points": context.existing_tags[:2] or ["几何综合"],
        "method_tags": ["角度转化"] if "角" in context.question_text else ["方程思想"],
        "ability_tags": ["数学推理"] if context.has_answer else ["信息提取"],
        "math_model_tags": ["平行线角度模型"] if "平行" in context.question_text else [],
        "difficulty": 3 if context.has_answer else 2,
        "typicality": 3,
        "error_prone_points": ["条件转化不完整"],
        "prerequisite_points": context.existing_tags[:2],
        "textbook_chapter": "八年级上册 第七章 平行线的证明" if "平行" in context.question_text else "九年级上册 第四章 图形的相似",
        "teaching_stage": "巩固",
        "suitable_student_level": "中档提升",
        "reason": "Mock mode uses stable middle-school math tags for page testing.",
    }
    return TagAnalysis.from_dict(payload)


def _prompt_text(context: TaggingContext) -> str:
    user_payload = {
        "task": "Tag this question for a local junior math question bank.",
        "input": context.to_dict(),
        "output_schema": _plain_output_schema(),
    }
    if os.getenv("QUESTION_BANK_TAGGING_THINKING") == "1":
        user_payload["reasoning_instruction"] = (
            "【推理引导】：请启动深思熟虑的推理过程！"
            "首先请你仔细阅读题目与参考答案，推导并阐述其考核的初中数学知识本质、"
            "解题的关键辅助线及步骤、所涉及的经典数学模型（例如中点模型、手拉手模型等），"
            "以及学生在做这道题时容易踩的典型错因（如条件遗漏、概念混淆等）。"
            "在完成了上述扎实的数学逻辑推导与诊断分析后，"
            "再按照 schema 规范，精心选择并确定最终输出的各项标签（各项标签内容必须与前述推导分析结论完全吻合，不得凭空臆造或漏标错标）。"
        )
    return f"{_system_prompt()}\n\n{json.dumps(user_payload, ensure_ascii=False)}"


def _prompt_input(context: TaggingContext) -> list[dict[str, str]]:
    user_payload = {
        "task": "Tag this question for a local junior math question bank.",
        "input": context.to_dict(),
        "output_schema": _plain_output_schema(),
    }
    if os.getenv("QUESTION_BANK_TAGGING_THINKING") == "1":
        user_payload["reasoning_instruction"] = (
            "【推理引导】：请启动深思熟虑的推理过程！"
            "首先请你仔细阅读题目与参考答案，推导并阐述其考核的初中数学知识本质、"
            "解题的关键辅助线及步骤、所涉及的经典数学模型（例如中点模型、手拉手模型等），"
            "以及学生在做这道题时容易踩的典型错因（如条件遗漏、概念混淆等）。"
            "在完成了上述扎实的数学逻辑推导与诊断分析后，"
            "再按照 schema 规范，精心选择并确定最终输出的各项标签（各项标签内容必须与前述推导分析结论完全吻合，不得凭空臆造或漏标错标）。"
        )
    return [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def _system_prompt() -> str:
    curriculum_list = "\n".join(f"- {ch}" for ch in CURRICULUM_CHAPTERS)
    return f"""
    You analyze junior middle-school math questions.
    Return one JSON object only with the requested schema.
    Prefer stable, countable Chinese junior math tags over long ad hoc phrases.
    
    Allowed student levels (choose exactly one): {", ".join(STUDENT_LEVELS)}
    
    Knowledge point options (choose relevant): {", ".join(KNOWLEDGE_POINT_OPTIONS)}
    
    Method tag options (choose relevant): {", ".join(METHOD_TAG_OPTIONS)}
    
    Ability tag options (choose relevant): {", ".join(ABILITY_TAG_OPTIONS)}
    
    Math model options (choose relevant): {", ".join(MATH_MODEL_OPTIONS)}
    
    Error-prone options (choose relevant): {", ".join(ERROR_PRONE_CATEGORIES)}
    
    Curriculum chapters (choose textbook_chapter from these candidates):
    {curriculum_list}
    
    Knowledge points should be concise, such as 平行线性质, 三角形全等, 二元一次方程组, 一元一次不等式组, 函数图像, 几何综合.
    Method tags should include stable ideas when applicable, such as 方程思想, 数形结合, 分类讨论, 构造辅助线, 构造全等, 角度转化, 面积法.
    Math model tags may include common exam models, such as 半角模型, 手拉手模型, 将军饮马模型, 角平分线模型, 中点模型, 旋转模型, 折叠模型, 平行线角度模型.
    Ability tags should be curriculum-friendly, such as 运算能力, 几何直观, 推理能力, 抽象能力, 模型观念, 应用意识, 创新意识.
    Error-prone points must be broad, reusable categories for statistics, not question-specific step descriptions. Prefer the provided error_prone_options such as 条件识别不完整, 图形关系识别错误, 辅助线思路缺失, 公式/定理误用, 运算化简错误, 书写依据不完整. Do not write labels like “第一问证明某三角形全等时漏找某条件”.
    Choose textbook_chapter from the provided 北师大版2024 初中数学教材章节候选 when possible.
    Scores difficulty and typicality must be integers from 1 to 10.
    Typicality should primarily be your own professional judgment from the question form, knowledge pattern, method pattern, and exam recurrence intuition. corpus_stats is only reference context for now; do not mechanically overwrite your judgment from it.
    suitable_student_level must be one of: 入门补缺, 基础巩固, 中档提升, 综合突破, 压轴拔高.
    """.strip()


def _plain_output_schema() -> dict[str, object]:
    return {
        "knowledge_points": [],
        "method_tags": [],
        "ability_tags": [],
        "math_model_tags": [],
        "difficulty": 1,
        "typicality": 1,
        "error_prone_points": [],
        "prerequisite_points": [],
        "textbook_chapter": "",
        "teaching_stage": "",
        "suitable_student_level": "",
        "reason": "",
    }


def _tag_analysis_response_format() -> dict[str, Any]:
    array_field = {"type": "array", "items": {"type": "string"}}
    score_field = {"type": "integer"}
    text_field = {"type": "string"}
    properties = {
        "knowledge_points": array_field,
        "method_tags": array_field,
        "ability_tags": array_field,
        "math_model_tags": array_field,
        "difficulty": score_field,
        "typicality": score_field,
        "error_prone_points": array_field,
        "prerequisite_points": array_field,
        "textbook_chapter": text_field,
        "teaching_stage": text_field,
        "suitable_student_level": text_field,
        "reason": text_field,
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
    for profile_path in _profile_paths():
        profiles = load_api_profiles(profile_path)
        if not profiles:
            continue
        profile = profiles[-1]
        api_key = str(profile.get("api_key") or "").strip()
        config_api_key = str(profile.get("config_api_key") or api_key).strip()
        if not api_key or not config_api_key:
            continue
        grading_model = str(profile.get("grading_model") or DEFAULT_TAGGING_MODEL)
        return LLMSettings(
            api_key=api_key,
            base_url=normalize_openai_base_url(str(profile.get("base_url") or "https://api.openai.com/v1")),
            ocr_model=str(profile.get("ocr_model") or grading_model),
            grading_model=grading_model,
            config_model=str(profile.get("config_model") or DEFAULT_TAGGING_MODEL),
            config_api_key=config_api_key,
            config_base_url=normalize_openai_base_url(str(profile.get("config_base_url") or profile.get("base_url") or "https://api.openai.com/v1")),
        )
    return None


def _profile_paths() -> list[Path]:
    project_root = Path(__file__).resolve().parents[2]
    candidates = [
        Path.cwd() / "config" / "api_profiles.json",
        project_root / "config" / "api_profiles.json",
        project_data_root() / "config" / "api_profiles.json",
        project_root / "data" / "config" / "api_profiles.json",
    ]
    unique: list[Path] = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    return unique


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
    if extra_kwargs is None:
        return llm_client.json_from_text(prompt, model=model)
    try:
        return llm_client.json_from_text(prompt, model=model, extra_kwargs=extra_kwargs)
    except TypeError as exc:
        if "extra_kwargs" not in str(exc):
            raise
        return llm_client.json_from_text(prompt, model=model)


def _analyze_one_question(service: AITaggingService, context: TaggingContext, rate_limiter: "_RateLimiter") -> AITaggingResult:
    rate_limiter.acquire()
    return service.analyze_question(context)


def _batch_system_prompt() -> str:
    base = _system_prompt()
    return f"""
    {base}
    
    CRITICAL: You are analyzing a BATCH of junior middle-school math questions.
    You must analyze each question in the batch and return the list of analyses under the "results" key in your JSON response.
    Each analysis object in "results" must include the exact "question_id" that was provided in the input.
    """.strip()


def _batch_prompt_input(batch_contexts: list[tuple[int, TaggingContext]]) -> list[dict[str, str]]:
    input_payloads = []
    for question_id, context in batch_contexts:
        input_payloads.append({
            "question_id": question_id,
            "input": context.to_dict(),
        })
        
    user_payload = {
        "task": "Analyze and tag this batch of junior middle-school math questions.",
        "batch_inputs": input_payloads,
    }
    if os.getenv("QUESTION_BANK_TAGGING_THINKING") == "1":
        user_payload["reasoning_instruction"] = (
            "【推理引导】：请启动深思熟虑的推理过程！"
            "首先请你仔细阅读题目与参考答案，推导并阐述其考核的初中数学知识本质、"
            "解题的关键辅助线及步骤、所涉及的经典数学模型（例如中点模型、手拉手模型等），"
            "以及学生在做这道题时容易踩的典型错因（如条件遗漏、概念混淆等）。"
            "在完成了上述扎实的数学逻辑推导与诊断分析后，"
            "再按照 schema 规范，精心选择并确定最终输出的各项标签（各项标签内容必须与前述推导分析结论完全吻合，不得凭空臆造或漏标错标）。"
        )
    
    return [
        {"role": "system", "content": _batch_system_prompt()},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def _batch_tag_analysis_response_format() -> dict[str, Any]:
    array_field = {"type": "array", "items": {"type": "string"}}
    score_field = {"type": "integer"}
    text_field = {"type": "string"}
    question_analysis_properties = {
        "question_id": {"type": "integer"},
        "knowledge_points": array_field,
        "method_tags": array_field,
        "ability_tags": array_field,
        "math_model_tags": array_field,
        "difficulty": score_field,
        "typicality": score_field,
        "error_prone_points": array_field,
        "prerequisite_points": array_field,
        "textbook_chapter": text_field,
        "teaching_stage": text_field,
        "suitable_student_level": text_field,
        "reason": text_field,
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
    rate_limiter: "_RateLimiter",
) -> dict[int, AITaggingResult]:
    rate_limiter.acquire()
    
    if service.mock_mode:
        return _mock_batch_analysis(batch_items)
        
    try:
        if service.llm_client is not None:
            # Format prompt for llm_client
            batch_inputs = [{'question_id': qid, 'input': ctx.to_dict()} for qid, ctx in batch_items]
            user_payload = {
                "batch_inputs": batch_inputs
            }
            if os.getenv("QUESTION_BANK_TAGGING_THINKING") == "1":
                user_payload["reasoning_instruction"] = (
                    "【推理引导】：请启动深思熟虑的推理过程！"
                    "首先请你仔细阅读题目与参考答案，推导并阐述其考核的初中数学知识本质、"
                    "解题的关键辅助线及步骤、所涉及的经典数学模型（例如中点模型、手拉手模型等），"
                    "以及学生在做这道题时容易踩的典型错因（如条件遗漏、概念混淆等）。"
                    "在完成了上述扎实的数学逻辑推导与诊断分析后，"
                    "再按照 schema 规范，精心选择并确定最终输出的各项标签（各项标签内容必须与前述推导分析结论完全吻合，不得凭空臆造或漏标错标）。"
                )
            prompt = f"{_batch_system_prompt()}\n\n{json.dumps(user_payload, ensure_ascii=False)}"
            
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
                    )
                except (KeyError, ValueError, TypeError):
                    continue
            for qid, ctx in batch_items:
                if qid not in results:
                    results[qid] = AITaggingResult(ok=False, mock_mode=False, error="LLM response did not include results for this question ID")
            return results
            
        # Standard OpenAI-style / Google Responses API client
        response = service._client().responses.create(
            model=service.model,
            text={"format": _batch_tag_analysis_response_format()},
            input=_batch_prompt_input(batch_items),
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
                )
            except (KeyError, ValueError, TypeError):
                continue
        for qid, ctx in batch_items:
            if qid not in results:
                results[qid] = AITaggingResult(ok=False, mock_mode=False, error="API response did not include results for this question ID")
        return results
    except Exception as exc:
        # Fallback to single-question tagging for the failed batch
        fallback_results = {}
        for qid, ctx in batch_items:
            try:
                fallback_results[qid] = service.analyze_question(ctx)
            except Exception as single_exc:
                fallback_results[qid] = AITaggingResult(ok=False, mock_mode=False, error=str(single_exc))
        return fallback_results


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
