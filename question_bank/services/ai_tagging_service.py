from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from api_profiles import load_api_profiles
from llm_client import LLMClient, LLMSettings, normalize_openai_base_url
from question_bank.database.paths import project_data_root
from question_bank.models.tag_schema import ERROR_PRONE_CATEGORIES, SUB_SKILL_DIMENSIONS, SUB_SKILL_KEYWORD_HINTS, TagAnalysis, TaggingContext
from question_bank.taxonomy.registry import CANONICAL_KNOWLEDGE, canonical_knowledge_seed_rows


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


def canonical_knowledge_prompt_table() -> str:
    """生成受控的 KP_* 候选表文本，注入到打标 prompt 中。

    要求 AI 必须从这个表里选 canonical_knowledge_id（小写 kp_* 形式），
    不在表内的知识点填 null 并在 reason 说明。
    """
    lines = []
    for item in CANONICAL_KNOWLEDGE:
        # 只取前 6 个别名避免 prompt 过长
        alias_preview = "、".join(item.aliases[:6])
        lines.append(f"- {item.canonical_id.casefold()} : {item.canonical_name}（别名: {alias_preview}）")
    return "\n".join(lines)


def is_valid_canonical_id(value: str) -> bool:
    """校验 AI 给的 canonical_id 是否在 registry 中（大小写不敏感）。"""
    if not value:
        return False
    target = value.casefold().strip()
    return any(item.canonical_id.casefold() == target for item in CANONICAL_KNOWLEDGE)


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
    model_name: str | None = None
    quality_status: str = "complete"
    quality_notes: list[str] = field(default_factory=list)


COMPLETE_CONFIDENCE_THRESHOLD = 0.72
REVIEW_CONFIDENCE_THRESHOLD = 0.45


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
        self.review_model = str(self.env.get("QUESTION_BANK_TAGGING_REVIEW_MODEL") or "").strip()
        self.review_llm_client: LLMClient | None = None
        
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
        self._configure_review_client(tagging_api_key=str(self.env.get("QUESTION_BANK_TAGGING_API_KEY") or "").strip())

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
                result = AITaggingResult(
                    ok=True,
                    mock_mode=False,
                    analysis=TagAnalysis.from_dict(payload),
                    model_name=_model_for_llm_client(self.llm_client, self.model),
                )
                return _with_quality(result, context)
            response = self._client().responses.create(
                model=self.model,
                text={"format": _tag_analysis_response_format()},
                input=_prompt_input(context),
            )
            output_text = str(getattr(response, "output_text", "") or "").strip()
            result = AITaggingResult(
                ok=True,
                mock_mode=False,
                analysis=TagAnalysis.from_dict(json.loads(output_text)),
                model_name=self.model,
            )
            return _with_quality(result, context)
        except Exception as exc:  # noqa: BLE001
            return AITaggingResult(ok=False, mock_mode=False, error=str(exc), model_name=self.model, quality_status="invalid", quality_notes=[str(exc)])

    def analyze_review_question(self, context: TaggingContext) -> AITaggingResult:
        if not self.review_configured:
            return AITaggingResult(ok=False, mock_mode=False, error="未配置低置信度复核模型", model_name=self.review_model or None, quality_status="invalid")
        try:
            assert self.review_llm_client is not None
            payload = _json_from_text_compat(
                self.review_llm_client,
                _prompt_text(context),
                model=_model_for_llm_client(self.review_llm_client, self.review_model),
            )
            result = AITaggingResult(
                ok=True,
                mock_mode=False,
                analysis=TagAnalysis.from_dict(payload),
                model_name=_model_for_llm_client(self.review_llm_client, self.review_model),
            )
            return _with_quality(result, context)
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
        max_workers: int | None = None,
        requests_per_minute: int | None = None,
        progress_callback: Callable[[int, int, int, AITaggingResult], None] | None = None,
    ) -> dict[int, AITaggingResult]:
        items = list(contexts.items())
        if not items:
            return {}
            
        batches = _adaptive_batches(items)
        
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
                        qid: AITaggingResult(ok=False, mock_mode=self.mock_mode, error=str(exc), model_name=self.model, quality_status="invalid", quality_notes=[str(exc)])
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
        "error_prone_points": ["条件转化不完整"],
        "prerequisite_points": context.existing_tags[:2],
        "textbook_chapter": "八年级上册 第七章 平行线的证明" if "平行" in context.question_text else "九年级上册 第四章 图形的相似",
        "teaching_stage": "巩固",
        "suitable_student_level": "中档提升",
        "canonical_knowledge_id": "kp_geo_comprehensive",
        "sub_skills": [],
        "reason": "Mock mode uses stable middle-school math tags for page testing.",
        "confidence": 0.8,
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
    canonical_table = canonical_knowledge_prompt_table()
    sub_skill_dims = "、".join(SUB_SKILL_DIMENSIONS)
    sub_skill_hints = "、".join(SUB_SKILL_KEYWORD_HINTS)
    return f"""
    You analyze junior middle-school math questions.
    Return one JSON object only with the requested schema.
    Prefer stable, countable Chinese junior math tags over long ad hoc phrases.

    Allowed student levels (choose exactly one): {", ".join(STUDENT_LEVELS)}

    Knowledge point options (choose relevant): {", ".join(KNOWLEDGE_POINT_OPTIONS)}

    === 主知识点稳定编码（canonical_knowledge_id，受控输出，极其重要）===
    你必须为题目选出一个 canonical_knowledge_id，从下列候选表中选取（输出小写 kp_* 形式）：
    {canonical_table}
    规则：
    - 必须输出表中的某个 kp_* 编码，不可自创、不可拼写错误。
    - 若题目的核心知识点确实不在表中，canonical_knowledge_id 填空字符串 ""，并在 reason 里说明原因。
    - 编码必须全小写（如 kp_geo_triangle_congruence），不要使用大写。

    === 子技能（sub_skills，半受控维度提炼，极其重要）===
    请按以下维度提炼子技能：{sub_skill_dims}
    参考词（可适度扩展但不强制封闭）：{sub_skill_hints}
    规则：
    - sub_skills 描述具体考法/题型/微技能（如 SAS判定、尺规作图、面积计算、手拉手模型），用于精准匹配学生薄弱考法。
    - 严禁复读 knowledge_points 原词或添加无信息量的词（如"性质""定义""概念""运算"等泛词）。
    - 若题目考法无明确细分，sub_skills 可为空数组 []。
    - 每个子技能应是简短词组（2-8 字），如"SAS判定""辅助线构造""角度计算"。

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
    Choose the smallest accurate primary knowledge point. Do not overgeneralize 三角形三边关系 as 三角形全等, or 科学记数法 as 整式运算.
    Difficulty must be a number from 1 to 10 (allow 1 decimal place, e.g., 4.5, 6.2, 8.0).
    confidence must be a number from 0 to 1 for your overall confidence in the tag set. Lower it when the image is essential, the answer is missing, or the core knowledge point is uncertain.
    suitable_student_level must be one of: 入门补缺, 基础巩固, 中档提升, 综合突破, 压轴拔高.
    """.strip()


def _plain_output_schema() -> dict[str, object]:
    return {
        "knowledge_points": [],
        "method_tags": [],
        "ability_tags": [],
        "math_model_tags": [],
        "difficulty": 1,
        "error_prone_points": [],
        "prerequisite_points": [],
        "textbook_chapter": "",
        "teaching_stage": "",
        "suitable_student_level": "",
        "canonical_knowledge_id": "",
        "sub_skills": [],
        "reason": "",
        "confidence": 0.8,
    }


def _tag_analysis_response_format() -> dict[str, Any]:
    array_field = {"type": "array", "items": {"type": "string"}}
    score_field = {"type": "number"}
    text_field = {"type": "string"}
    properties = {
        "knowledge_points": array_field,
        "method_tags": array_field,
        "ability_tags": array_field,
        "math_model_tags": array_field,
        "difficulty": score_field,
        "error_prone_points": array_field,
        "prerequisite_points": array_field,
        "textbook_chapter": text_field,
        "teaching_stage": text_field,
        "suitable_student_level": text_field,
        "canonical_knowledge_id": text_field,
        "sub_skills": array_field,
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
        "output_schema": {"results": [{**_plain_output_schema(), "question_id": 0}]},
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
    score_field = {"type": "number"}
    text_field = {"type": "string"}
    question_analysis_properties = {
        "question_id": {"type": "integer"},
        "knowledge_points": array_field,
        "method_tags": array_field,
        "ability_tags": array_field,
        "math_model_tags": array_field,
        "difficulty": score_field,
        "error_prone_points": array_field,
        "prerequisite_points": array_field,
        "textbook_chapter": text_field,
        "teaching_stage": text_field,
        "suitable_student_level": text_field,
        "canonical_knowledge_id": text_field,
        "sub_skills": array_field,
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
    rate_limiter: "_RateLimiter",
) -> dict[int, AITaggingResult]:
    rate_limiter.acquire()
    
    if service.mock_mode:
        return _finalize_batch_results(service, batch_items, _mock_batch_analysis(batch_items))
        
    try:
        if service.llm_client is not None:
            # Format prompt for llm_client
            batch_inputs = [{'question_id': qid, 'input': ctx.to_dict()} for qid, ctx in batch_items]
            user_payload = {
                "task": "Analyze and tag this batch of junior middle-school math questions.",
                "batch_inputs": batch_inputs,
                "output_schema": {"results": [{**_plain_output_schema(), "question_id": 0}]},
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
                        model_name=_model_for_llm_client(service.llm_client, service.model),
                    )
                except (KeyError, ValueError, TypeError):
                    continue
            for qid, ctx in batch_items:
                if qid not in results:
                    results[qid] = AITaggingResult(ok=False, mock_mode=False, error="LLM response did not include results for this question ID", model_name=service.model, quality_status="invalid")
            return _finalize_batch_results(service, batch_items, results)
            
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
                    model_name=service.model,
                )
            except (KeyError, ValueError, TypeError):
                continue
        for qid, ctx in batch_items:
            if qid not in results:
                results[qid] = AITaggingResult(ok=False, mock_mode=False, error="API response did not include results for this question ID", model_name=service.model, quality_status="invalid")
        return _finalize_batch_results(service, batch_items, results)
    except Exception as exc:
        # Fallback to single-question tagging for the failed batch
        fallback_results = {}
        for qid, ctx in batch_items:
            try:
                fallback_results[qid] = service.analyze_question(ctx)
            except Exception as single_exc:
                fallback_results[qid] = AITaggingResult(ok=False, mock_mode=False, error=str(single_exc), model_name=service.model, quality_status="invalid", quality_notes=[str(single_exc)])
        return _finalize_batch_results(service, batch_items, fallback_results)


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
) -> dict[int, AITaggingResult]:
    contexts = dict(batch_items)
    final: dict[int, AITaggingResult] = {}
    for qid, context in contexts.items():
        result = _with_quality(raw_results.get(qid) or AITaggingResult(
            ok=False,
            mock_mode=service.mock_mode,
            error="AI response missing",
            model_name=service.model,
            quality_status="invalid",
        ), context)
        if result.analysis is not None and result.quality_status == "invalid":
            retry = _with_quality(service.analyze_question(context), context)
            if _is_better_quality(retry, result):
                result = retry
        if result.analysis is not None and result.quality_status in {"low_confidence", "conflict"}:
            result = _review_low_confidence_result(service, context, result)
        final[qid] = result
    return final


def _with_quality(result: AITaggingResult, context: TaggingContext) -> AITaggingResult:
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
        )
    status, notes, confidence = _evaluate_analysis_quality(result.analysis, context)
    payload = result.analysis.to_dict()
    payload["confidence"] = confidence
    return AITaggingResult(
        ok=True,
        mock_mode=result.mock_mode,
        analysis=TagAnalysis.from_dict(payload),
        error=result.error,
        model_name=result.model_name,
        quality_status=status,
        quality_notes=notes,
    )


def _evaluate_analysis_quality(analysis: TagAnalysis, context: TaggingContext) -> tuple[str, list[str], float]:
    notes: list[str] = []
    missing = []
    if not analysis.knowledge_points:
        missing.append("缺少知识点")
    if not analysis.ability_tags:
        missing.append("缺少能力标签")
    if not analysis.textbook_chapter:
        missing.append("缺少教材章节")
    if not analysis.suitable_student_level:
        missing.append("缺少适合学生层级")
    # canonical_knowledge_id 受控校验：若 AI 给了值但不在 registry 中，记为冲突
    if analysis.canonical_knowledge_id and not is_valid_canonical_id(analysis.canonical_knowledge_id):
        conflict_notes = [f"AI 给出的 canonical_knowledge_id 不在标准词表中: {analysis.canonical_knowledge_id}"]
    else:
        conflict_notes = []
    confidence = float(analysis.confidence)
    if context.has_images or "[[IMAGE:" in str(context.question_text or ""):
        confidence *= 0.95
        notes.append("图片依赖题已轻微降权")
    rule_conflict_notes = _rule_conflict_notes(context, analysis)
    notes.extend(rule_conflict_notes)
    notes.extend(conflict_notes)
    if missing:
        notes.extend(missing)
        return "invalid", notes, round(min(confidence * 0.4, REVIEW_CONFIDENCE_THRESHOLD - 0.01), 4)
    if confidence < REVIEW_CONFIDENCE_THRESHOLD:
        notes.append("AI 自评置信度过低")
        return "invalid", notes, round(confidence, 4)
    if conflict_notes or rule_conflict_notes:
        return "conflict", notes, round(min(confidence * 0.75, COMPLETE_CONFIDENCE_THRESHOLD - 0.03), 4)
    if confidence < COMPLETE_CONFIDENCE_THRESHOLD:
        notes.append("置信度低，需复核或人工确认")
        return "low_confidence", notes, round(confidence, 4)
    return "complete", notes, round(min(1.0, confidence), 4)


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
) -> AITaggingResult:
    if not service.review_configured:
        return primary
    review = _with_quality(service.analyze_review_question(context), context)
    if not review.ok or review.analysis is None or primary.analysis is None:
        return AITaggingResult(
            ok=primary.ok,
            mock_mode=primary.mock_mode,
            analysis=primary.analysis,
            error=primary.error,
            model_name=primary.model_name,
            quality_status=primary.quality_status,
            quality_notes=[*primary.quality_notes, "复核模型未返回有效结果"],
        )
    if _analyses_agree(primary.analysis, review.analysis):
        merged = _merge_agreed_analyses(primary.analysis, review.analysis)
        return AITaggingResult(
            ok=True,
            mock_mode=primary.mock_mode,
            analysis=merged,
            model_name="+".join(item for item in (primary.model_name, review.model_name) if item),
            quality_status="complete",
            quality_notes=[*primary.quality_notes, "复核模型与主模型核心标签一致"],
        )
    return AITaggingResult(
        ok=True,
        mock_mode=primary.mock_mode,
        analysis=primary.analysis,
        model_name=primary.model_name,
        quality_status="conflict",
        quality_notes=[*primary.quality_notes, "复核模型与主模型核心标签冲突"],
    )


def _analyses_agree(left: TagAnalysis, right: TagAnalysis) -> bool:
    left_knowledge = {_compact(item) for item in left.knowledge_points if _compact(item)}
    right_knowledge = {_compact(item) for item in right.knowledge_points if _compact(item)}
    if not left_knowledge.intersection(right_knowledge):
        return False
    left_chapter = _compact(left.textbook_chapter)
    right_chapter = _compact(right.textbook_chapter)
    return not left_chapter or not right_chapter or left_chapter == right_chapter


def _merge_agreed_analyses(primary: TagAnalysis, review: TagAnalysis) -> TagAnalysis:
    payload = primary.to_dict()
    for field_name in ("knowledge_points", "method_tags", "ability_tags", "math_model_tags", "error_prone_points", "prerequisite_points"):
        payload[field_name] = _ordered_unique([*primary.to_dict().get(field_name, []), *review.to_dict().get(field_name, [])])
    payload["confidence"] = max(primary.confidence, review.confidence, COMPLETE_CONFIDENCE_THRESHOLD)
    if not payload.get("reason") and review.reason:
        payload["reason"] = review.reason
    return TagAnalysis.from_dict(payload)


def _is_better_quality(candidate: AITaggingResult, current: AITaggingResult) -> bool:
    rank = {"invalid": 0, "conflict": 1, "low_confidence": 2, "complete": 3}
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
    return bool(result.ok and result.analysis is not None and result.quality_status == "complete")


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
