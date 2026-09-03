"""学生个人考试分析报告导出 + 班级分析页面数据装配。

产物形态与数据口径见已确认实施方案（.zcode/plans/考试分析报告_实施方案.md）：
- personal_analysis_html：每名正常参考学生一份自包含 HTML，打包 zip，附未生成清单；
- 班级分析（教师版）已改为系统内嵌页面：本模块只提供数据装配、提示词与
  AI 叙述缓存，页面状态与生成 job 见 backend/class_analysis.py。

与计划的三处已确认偏差：
1. 解析失败不重发模型请求，由 LLMClient.json_from_text 本地确定性修复，
   仍失败则该报告降级为「无 AI 叙述版」（数据段照常渲染）；
2. 费用估算只给 token 粗估，不给金额；
3. AI 叙述按 (session_id, score_revision, rendition_version, 报告键) 缓存到
   受控目录，重新生成命中缓存不再调用模型。
"""

from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import math
import re
import statistics
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from analysis_report_prompts import (
    CLASS_ALIAS_PREFIX,
    PERSONAL_MAX_TOKENS,
    PERSONAL_STUDENT_ALIAS,
    PERSONAL_SYSTEM_PROMPT,
)
from answer_key_utils import answer_forms_map
from answer_region_geometry import (
    answer_regions_with_template_source_sizes,
    scaled_region_bbox,
)
from api_profiles import get_api_profile_store, resolve_profile_for_task
from backend.analytics.service import load_session_score_type_maps
from backend.llm.policy import policy_overrides_from_profile
from backend.llm.trace import safe_endpoint_host
from backend.repositories.access import (
    GradingRepositoryAccess,
    as_grading_repositories,
)
from backend.repositories.compat import open_grading_repositories
from export_names import safe_filename_fragment, session_export_path_name
from grading_completeness import resolve_grading_completeness
from llm_client import LLMSettings, normalize_openai_base_url
from path_manager import resolve_stored_file_path
from question_id_contract import (
    QuestionIdContractError,
    canonicalize_question_document,
    question_id_coordinates,
    resolve_known_question_id,
)
from report import _natural_question_order

PERSONAL_ANALYSIS_REPORT_TYPE = "personal_analysis_html"
ANALYSIS_REPORT_TYPES = (PERSONAL_ANALYSIS_REPORT_TYPE,)

# 方案 §7/§8 的固定标注文案。
AI_DISCLAIMER = "AI 分析 · 仅供参考"
AI_FAILED_NOTE = "AI 分析生成失败，可重新生成"
SMALL_SAMPLE_LIMIT = 10

# 方案 §4 截图裁剪参数；单报告 JPEG 字节预算约 225KB，base64 后约 300KB。
_SHOT_PADDING = 12
_SHOT_MAX_WIDTH = 900
_SHOT_JPEG_QUALITY = 85
_SHOT_BUDGET_BYTES = 225 * 1024

_QUESTION_TYPE_LABELS = {
    "choice": "选择",
    "single_choice": "选择",
    "multiple_choice": "选择",
    "fill_blank": "填空",
    "fill_in_blank": "填空",
    "calculation": "计算",
    "proof": "证明",
    "answer": "解答",
    "subjective": "解答",
    "comprehensive": "解答",
    "constructed_response": "解答",
    "judgement": "判断",
    "true_false": "判断",
    "direct_answer": "作答",
}

# 客观题批改记录里的内部调试码 → 家长可读文本；
# 口径与前端 frontend/src/utils/grading-reasons.ts 保持一致。
_OBJECTIVE_ANSWER_PREFIX = "objective_answer="
_REASON_CODE_LABELS = {
    "objective_api_disabled": "客观题识别未启用，已转教师复核",
    "objective_api_not_configured": "客观题识别模型配置不完整，已转教师复核",
    "objective_paper_model_failed": "客观题识别请求失败，已转教师复核",
    "objective_paper_region_failed": "无法读取选填题作答区域",
    "objective_region_not_found": "未找到此题的有效作答区域",
    "missing_question_result": "AI 未返回此题的识别结果",
    "duplicate_question_result": "AI 返回了重复的识别结果",
    "paper_key_mismatch": "识别结果与当前答卷不一致",
    "low_confidence": "作答辨识度较低，需要教师复核",
    "needs_review": "AI 建议教师复核",
    "objective_needs_review": "客观题识别结果需要教师复核",
    "objective_score_uncertain": "答案识别存在不确定性",
    "prompt_injection_or_score_bait": "作答区出现与答题无关的批改指令",
    "discarded_answer_only": "只识别到已经涂抹或作废的答案",
    "assignment_changed": "批改期间答卷匹配发生变化",
    "missing_detail_question_ids": "AI 未完整返回所有小问的评分结果",
    "duplicate_detail_question_id": "AI 重复返回了同一小问",
    "unexpected_detail_question_id": "AI 返回了不属于本题的小问",
    "no_numeric_value": "未能可靠识别填写的数值，需要教师确认",
    "multiple options selected": "识别到选择了多个选项",
}

_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))

# 分数段按满分等比缩放（满分 100 时即方案 §3 的固定分段）。
_BAND_CUTOFFS = (85, 70, 60, 40, 0)
_PASS_CUTOFF = 60


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


class AnalysisNarrativeCache:
    """AI 叙述 JSON 的本地缓存：命中即不再调用模型（重新生成幂等、不重复扣费）。"""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)

    @staticmethod
    def cache_key(
        *,
        session_id: int,
        score_revision: str,
        rendition_version: str,
        report_key: str,
    ) -> str:
        material = json.dumps(
            {
                "session_id": int(session_id),
                "score_revision": str(score_revision),
                "rendition_version": str(rendition_version),
                "report_key": str(report_key),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def load(self, key: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._path(key).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        narrative = payload.get("narrative") if isinstance(payload, dict) else None
        return narrative if isinstance(narrative, dict) else None

    def store(self, key: str, narrative: dict[str, Any]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self._path(key)
        temp = target.with_name(f".{target.stem}.tmp")
        try:
            temp.write_text(
                json.dumps(
                    {"version": 1, "narrative": narrative},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            temp.replace(target)
        except OSError:
            temp.unlink(missing_ok=True)


def estimate_prompt_tokens(text: str) -> int:
    """token 粗估：中文场景约 1.5 字符/token，仅用于生成前的量级提示。"""
    return math.ceil(len(text) / 1.5)


@dataclass
class _QuestionInfo:
    question_id: str
    question_type: str
    max_score: float
    stem_summary: str
    canonical_answer: str
    attempts: int = 0
    score_sum: float = 0.0

    @property
    def class_rate(self) -> float | None:
        full = self.max_score * self.attempts
        if full <= 0:
            return None
        return self.score_sum / full

    @property
    def class_avg(self) -> float | None:
        if self.attempts <= 0:
            return None
        return self.score_sum / self.attempts


@dataclass
class _StudentQuestionRecord:
    question_id: str
    score: float
    max_score: float
    deduction_reason: str
    error_category: str
    error_summary: str
    secondary_errors: list[str] = field(default_factory=list)
    student_answer: str = ""

    @property
    def lost(self) -> bool:
        return self.max_score > 0 and self.score < self.max_score - 1e-6

    @property
    def lost_points(self) -> float:
        return max(0.0, self.max_score - self.score) if self.lost else 0.0


@dataclass
class _StudentReportData:
    result_id: int
    student_id: int
    student_code: str
    student_name: str
    class_name: str
    student_score: float
    needs_review: bool
    graded_at: str
    rank: int = 0
    records: list[_StudentQuestionRecord] = field(default_factory=list)

    @property
    def lost_points_total(self) -> float:
        return sum(record.lost_points for record in self.records)


@dataclass
class _SessionAnalysisData:
    session_id: int
    session_name: str
    subject: str
    full_score: float
    graded_at: str
    present: int
    roster_absent: int
    small_sample: bool
    questions: list[_QuestionInfo]
    students: list[_StudentReportData]
    skipped: list[dict[str, str]]
    stats: dict[str, Any]
    knowledge_backfill: dict[str, list[dict[str, str]]]


def assemble_session_analysis(
    db: GradingRepositoryAccess | Any,
    session_id: int,
    *,
    data_root: Path | None = None,
) -> _SessionAnalysisData:
    """按方案 §3 的字段映射装配一次报告所需的全部数据。"""
    repositories = as_grading_repositories(db)
    resolved_data_root = data_root or _infer_data_root(repositories.db_path)
    snapshot = repositories.reports.get_session_report_snapshot(
        int(session_id),
        question_bank_path=_question_bank_db_path(repositories.db_path),
    )
    session_row = repositories.sessions.get_grading_session(int(session_id)) or {}
    rubric = _load_rubric(session_row, resolved_data_root)
    score_map, type_map = load_session_score_type_maps(
        session_row or None,
        data_root=resolved_data_root,
    )
    stem_map = _stem_summary_map(rubric)
    answer_map = _canonical_answer_map(session_row, resolved_data_root)

    attendance_by_identity = {
        (
            str(row.get("student_code") or ""),
            str(row.get("class_name") or "未分班"),
        ): str(row.get("attendance_status") or "").strip()
        for row in snapshot.attendance
    }
    present_count = sum(
        1
        for row in snapshot.attendance
        if str(row.get("attendance_status") or "").strip() == "present"
    )
    roster_absent = sum(
        1
        for row in snapshot.attendance
        if str(row.get("attendance_status") or "").strip() in {"absent", "scan_issue"}
    )

    details_by_result: dict[int, list[dict[str, Any]]] = {}
    for detail in snapshot.details:
        details_by_result.setdefault(int(detail.get("result_id") or 0), []).append(
            detail
        )

    students: list[_StudentReportData] = []
    skipped: list[dict[str, str]] = []
    covered_identities: set[tuple[str, str]] = set()
    for result in snapshot.results:
        identity = (
            str(result.get("student_code") or ""),
            str(result.get("class_name") or "未分班"),
        )
        covered_identities.add(identity)
        label = {
            "class_name": identity[1],
            "student_code": identity[0],
            "student_name": str(result.get("student_name") or ""),
        }
        attendance_status = attendance_by_identity.get(identity)
        if attendance_status in {"absent", "scan_issue"}:
            skipped.append(
                {**label, "reason": "缺考" if attendance_status == "absent" else "扫描异常"}
            )
            continue
        result_id = int(result.get("result_id") or 0)
        completeness = resolve_grading_completeness(
            result.get("raw_json"),
            rubric=rubric if isinstance(rubric, dict) else None,
            details=details_by_result.get(result_id, []),
        )
        status = (
            str(completeness.get("status") or "").strip()
            if isinstance(completeness, dict)
            else ""
        )
        if status != "complete":
            missing = (
                completeness.get("missing_question_ids")
                if isinstance(completeness, dict)
                else []
            )
            missing_text = (
                "、".join(str(item) for item in missing if str(item).strip())
                if isinstance(missing, list)
                else ""
            )
            skipped.append(
                {
                    **label,
                    "reason": (
                        f"批改不完整（缺失题目：{missing_text}）"
                        if missing_text
                        else "批改结果无效或不完整"
                    ),
                }
            )
            continue
        students.append(
            _StudentReportData(
                result_id=result_id,
                student_id=int(result.get("student_id") or 0),
                student_code=identity[0],
                student_name=str(result.get("student_name") or ""),
                class_name=identity[1],
                student_score=float(result.get("student_score") or 0),
                needs_review=bool(result.get("needs_human_review")),
                graded_at=str(result.get("graded_at") or "")[:10],
                records=_merge_student_records(
                    repositories,
                    result_id,
                    details_by_result.get(result_id, []),
                    score_map,
                    student_answers=_student_answer_map(result.get("raw_json")),
                ),
            )
        )
    # 名册中缺考且无结果的学生也要进入未生成清单。
    attendance_labels = {"absent": "缺考", "scan_issue": "扫描异常"}
    for row in snapshot.attendance:
        status = str(row.get("attendance_status") or "").strip()
        identity = (
            str(row.get("student_code") or ""),
            str(row.get("class_name") or "未分班"),
        )
        if status in attendance_labels and identity not in covered_identities:
            skipped.append(
                {
                    "class_name": identity[1],
                    "student_code": identity[0],
                    "student_name": str(row.get("student_name") or ""),
                    "reason": attendance_labels[status],
                }
            )

    # 名次：按总分降序，同分同名次（method="min"）。
    ordered = sorted(
        students,
        key=lambda item: (-item.student_score, item.student_code, item.student_name),
    )
    previous_score: float | None = None
    previous_rank = 0
    for index, student in enumerate(ordered, start=1):
        if previous_score is None or student.student_score < previous_score - 1e-9:
            previous_rank = index
            previous_score = student.student_score
        student.rank = previous_rank
    students = ordered

    questions: list[_QuestionInfo] = []
    for qid in _natural_question_order([str(qid) for qid in score_map]):
        questions.append(
            _QuestionInfo(
                question_id=qid,
                question_type=str(type_map.get(qid) or ""),
                max_score=float(score_map.get(qid) or 0),
                stem_summary=stem_map.get(qid) or "",
                canonical_answer=answer_map.get(qid) or "",
            )
        )
    info_by_qid = {info.question_id: info for info in questions}
    for student in students:
        for record in student.records:
            info = info_by_qid.get(record.question_id)
            if info is None:
                continue
            info.attempts += 1
            info.score_sum += min(record.score, record.max_score or record.score)

    scores = [student.student_score for student in students]
    stats = _score_distribution(scores, _full_score(rubric, score_map, snapshot))
    session_name = (
        str(session_row.get("session_name") or "").strip() or f"考试批改_{session_id}"
    )
    present = present_count if snapshot.attendance else len(students)
    return _SessionAnalysisData(
        session_id=int(session_id),
        session_name=session_name,
        subject=str(rubric.get("subject") or "数学").strip() or "数学",
        full_score=_full_score(rubric, score_map, snapshot),
        graded_at=max((student.graded_at for student in students), default=""),
        present=present,
        roster_absent=roster_absent,
        small_sample=present < SMALL_SAMPLE_LIMIT,
        questions=questions,
        students=students,
        skipped=skipped,
        stats=stats,
        knowledge_backfill=snapshot.knowledge_backfill,
    )


def _infer_data_root(db_path: Path) -> Path | None:
    path = Path(db_path)
    return path.parent.parent if path.parent.name == "databases" else None


def _question_bank_db_path(db_path: Path) -> Path | None:
    path = Path(db_path)
    data_root = path.parent.parent if path.parent.name == "databases" else path.parent
    candidate = data_root / "databases" / "question_bank.db"
    return candidate if candidate.is_file() else None


def _load_rubric(session_row: dict[str, Any], data_root: Path | None) -> dict[str, Any]:
    rubric_path = resolve_stored_file_path(
        session_row.get("rubric_path"),
        data_root=data_root,
    )
    try:
        rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        if not isinstance(rubric, dict):
            return {}
        return canonicalize_question_document(rubric)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        QuestionIdContractError,
    ):
        return {}


def _stem_summary_map(rubric: dict[str, Any]) -> dict[str, str]:
    stems: dict[str, str] = {}
    questions = rubric.get("questions") if isinstance(rubric, dict) else []
    if not isinstance(questions, list):
        return stems
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = str(question.get("question_id") or "").strip()
        stem = str(question.get("stem_summary") or "").strip()
        if question_id and stem:
            stems[question_id] = stem
        parts = question.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            part_id = str(part.get("part_id") or "").strip()
            part_stem = str(part.get("stem_summary") or "").strip()
            # 小问缺题干摘要时回退大问摘要，仍没有则渲染层用题号替代（方案 §9）。
            if part_id and (part_stem or stem):
                stems[part_id] = part_stem or stem
    return stems


# 标准答案展示收敛参数：判分用的等价变体常含标点/单位差异的重复全文，
# 报告展示前做规范化去重、限量、限长。
_ANSWER_FORM_MAX_LEN = 120
_ANSWER_DISPLAY_MAX_FORMS = 3
_ANSWER_DISPLAY_MAX_CHARS = 400

_ANSWER_NORMALIZE_PAIRS = (
    ("：", ":"),
    ("，", ","),
    ("。", "."),
    ("（", "("),
    ("）", ")"),
    ("＝", "="),
    ("；", ";"),
    ("°", "度"),
)


def _normalize_answer_form(text: str) -> str:
    normalized = re.sub(r"\s+", "", str(text))
    for full, half in _ANSWER_NORMALIZE_PAIRS:
        normalized = normalized.replace(full, half)
    return normalized.lower()


def _display_answer_text(forms: list[str]) -> str:
    """把判分用的全部等价变体收敛为家长可读的标准答案文本。"""
    seen: set[str] = set()
    unique: list[str] = []
    for form in forms:
        text = str(form or "").strip()
        key = _normalize_answer_form(text)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(text)
    if not unique:
        return ""
    # 长文本（证明过程等）只保留代表形式；短答案最多并列 3 个。
    if any(len(text) > _ANSWER_FORM_MAX_LEN for text in unique):
        unique = unique[:1]
    else:
        unique = unique[:_ANSWER_DISPLAY_MAX_FORMS]
    display = " 或 ".join(unique)
    if len(display) > _ANSWER_DISPLAY_MAX_CHARS:
        display = display[:_ANSWER_DISPLAY_MAX_CHARS].rstrip() + " ……"
    return display


def _canonical_answer_map(
    session_row: dict[str, Any],
    data_root: Path | None,
) -> dict[str, str]:
    answer_path = resolve_stored_file_path(
        session_row.get("answer_key_path"),
        data_root=data_root,
    )
    try:
        answer_key = json.loads(answer_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return {
        qid: display
        for qid, forms in answer_forms_map(answer_key).items()
        if (display := _display_answer_text(forms))
    }


def _full_score(
    rubric: dict[str, Any],
    score_map: dict[str, float],
    snapshot: Any,
) -> float:
    try:
        total = float(rubric.get("total_score") or 0)
    except (TypeError, ValueError):
        total = 0.0
    if total > 0:
        return total
    # 只加总大题满分，避免小问与大问重复计数。
    parent_total = 0.0
    for qid, max_score in score_map.items():
        coordinates = question_id_coordinates(qid)
        if coordinates is not None and coordinates[1] is not None:
            continue
        parent_total += float(max_score or 0)
    if parent_total > 0:
        return parent_total
    return max(
        (float(result.get("total_score") or 0) for result in snapshot.results),
        default=0.0,
    )


def _score_distribution(scores: list[float], full_score: float) -> dict[str, Any]:
    if not scores:
        return {
            "avg": None,
            "median": None,
            "max": None,
            "min": None,
            "pass_rate": None,
            "excellent_count": 0,
            "bands": [],
        }
    scale = full_score / 100 if full_score > 0 else 1.0
    bands: list[dict[str, Any]] = []
    for index, cutoff in enumerate(_BAND_CUTOFFS):
        lower = cutoff * scale
        upper = full_score if index == 0 else _BAND_CUTOFFS[index - 1] * scale
        members = [
            score for score in scores if lower <= score < upper + 1e-9
        ]
        bands.append(
            {
                "label": f"{_fmt_num(lower)} – {_fmt_num(upper)} 分",
                "count": len(members),
                "ratio": len(members) / len(scores),
            }
        )
    passing = [score for score in scores if score >= _PASS_CUTOFF * scale]
    return {
        "avg": round(sum(scores) / len(scores), 2),
        "median": round(statistics.median(scores), 2),
        "max": max(scores),
        "min": min(scores),
        "pass_rate": round(len(passing) / len(scores), 4),
        "excellent_count": len(
            [score for score in scores if score >= _BAND_CUTOFFS[0] * scale]
        ),
        "bands": bands,
    }


def _sanitize_grading_text(value: object) -> str:
    """批改记录文本转家长可读口径：内部调试码翻译，无中文的文本丢弃。

    阅卷模型偶发用英文写扣分原因；报告面向家长，没有中文信息的文本
    不如不显示（其余中文字段仍可说明情况）。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered.startswith(_OBJECTIVE_ANSWER_PREFIX):
        answer = text[len(_OBJECTIVE_ANSWER_PREFIX):].strip()
        return f"作答识别为「{answer}」，与参考答案不符" if answer else ""
    direct = _REASON_CODE_LABELS.get(lowered)
    if direct is not None:
        return direct
    for code, label in _REASON_CODE_LABELS.items():
        if code in lowered:
            return label
    return text if _has_cjk(text) else ""


def _format_secondary_error(item: object) -> str:
    """次要错因可能是 dict（category/summary/evidence），格式化为可读文本。"""
    if isinstance(item, dict):
        summary = _sanitize_grading_text(item.get("summary"))
        category = _sanitize_grading_text(item.get("category"))
        evidence = str(item.get("evidence") or "").strip()
        text = summary or category
        if evidence and evidence not in text:
            text = f"{text}（{evidence}）" if text else evidence
        return text
    return _sanitize_grading_text(item)


# 学生作答识别文本在 raw_json grading_details 里的兼容键（与 ai_grader 一致）。
_STUDENT_ANSWER_KEYS = ("observed_answer", "student_answer", "answer_observed")
_STUDENT_ANSWER_MAX_LEN = 200


def _student_answer_map(raw_json: Any) -> dict[str, str]:
    """从批改原始 JSON 提取每题学生作答识别文本，供报告展示与 AI 分析输入。"""
    parsed = raw_json
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            return {}
    if not isinstance(parsed, dict):
        return {}
    details = parsed.get("grading_details")
    if not isinstance(details, list):
        return {}
    answers: dict[str, str] = {}
    for item in details:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("question_id") or "").strip()
        if not qid or qid in answers:
            continue
        text = ""
        for key in _STUDENT_ANSWER_KEYS:
            value = item.get(key)
            if value:
                text = str(value).strip()
                break
        if text:
            answers[qid] = text[:_STUDENT_ANSWER_MAX_LEN]
    return answers


def _merge_student_records(
    repositories: GradingRepositoryAccess,
    result_id: int,
    details: list[dict[str, Any]],
    score_map: dict[str, float],
    student_answers: dict[str, str] | None = None,
) -> list[_StudentQuestionRecord]:
    """把同一学生同一题的多条明细合并为一条（兼容小问拆行存储）。"""
    secondary_map: dict[str, list[str]] = {}
    try:
        for row in repositories.results.get_result_details(result_id):
            qid = str(row.get("question_id") or "").strip()
            canonical = resolve_known_question_id(qid, score_map) or qid
            errors = row.get("secondary_errors")
            if canonical and isinstance(errors, list):
                merged = secondary_map.setdefault(canonical, [])
                for item in errors:
                    text = _format_secondary_error(item)
                    if text and text not in merged:
                        merged.append(text)
    except Exception:
        secondary_map = {}

    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for detail in details:
        raw_qid = str(detail.get("question_id") or "").strip()
        if not raw_qid:
            continue
        qid = resolve_known_question_id(raw_qid, score_map) or raw_qid
        if qid not in merged:
            merged[qid] = {
                "score": 0.0,
                "deduction_reasons": [],
                "error_categories": [],
                "error_summaries": [],
                "objective_observed": "",
            }
            order.append(qid)
        bucket = merged[qid]
        bucket["score"] += float(detail.get("score_awarded") or 0)
        for field_name, bucket_key in (
            ("deduction_reason", "deduction_reasons"),
            ("error_category", "error_categories"),
            ("error_summary", "error_summaries"),
        ):
            raw_text = str(detail.get(field_name) or "").strip()
            # 客观题的作答识别结果只存在于调试串里，先取出再清洗。
            if (
                field_name == "deduction_reason"
                and raw_text.lower().startswith(_OBJECTIVE_ANSWER_PREFIX)
                and not bucket["objective_observed"]
            ):
                bucket["objective_observed"] = raw_text.split("=", 1)[1].strip()
            text = _sanitize_grading_text(raw_text)
            if text and text not in bucket[bucket_key]:
                bucket[bucket_key].append(text)

    answers = student_answers or {}
    records: list[_StudentQuestionRecord] = []
    for qid in order:
        bucket = merged[qid]
        max_score = float(score_map.get(qid) or 0)
        score = min(bucket["score"], max_score) if max_score > 0 else bucket["score"]
        records.append(
            _StudentQuestionRecord(
                question_id=qid,
                score=score,
                max_score=max_score,
                deduction_reason="；".join(bucket["deduction_reasons"]),
                error_category="；".join(bucket["error_categories"]),
                error_summary="；".join(bucket["error_summaries"]),
                secondary_errors=secondary_map.get(qid, []),
                student_answer=(
                    answers.get(qid) or bucket["objective_observed"] or ""
                ),
            )
        )
    order_index = {
        qid: index for index, qid in enumerate(_natural_question_order(order))
    }
    records.sort(
        key=lambda record: order_index.get(record.question_id, len(order_index))
    )
    return records


def _parent_question_id(question_id: str) -> str:
    coordinates = question_id_coordinates(question_id)
    if coordinates is None:
        return str(question_id or "").strip()
    return f"Q{coordinates[0]}"


def _fmt_num(value: object) -> str:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value or "")
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _question_type_label(question_type: str) -> str:
    text = str(question_type or "").strip()
    if not text:
        return ""
    label = _QUESTION_TYPE_LABELS.get(text)
    if label is not None:
        return label
    # 未收录的英文类型码不原样渲染给家长；已是中文的兼容输入照常显示。
    return text if _has_cjk(text) else ""


def _question_display_label(question_id: str) -> str:
    """Q9 → 第9题；Q10(P1) → 第10(1)题。"""
    coordinates = question_id_coordinates(question_id)
    if coordinates is None:
        return str(question_id or "")
    parent, part = coordinates
    if part is None:
        return f"第{parent}题"
    return f"第{parent}({part})题"


def _esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


# ---------------------------------------------------------------------------
# 模型 payload（方案 §6.2 / §6.4；学生一律用代号，不含姓名与磁盘路径）
# ---------------------------------------------------------------------------


def build_personal_payload(
    data: _SessionAnalysisData,
    student: _StudentReportData,
) -> dict[str, Any]:
    info_by_qid = {info.question_id: info for info in data.questions}
    questions: list[dict[str, Any]] = []
    for record in student.records:
        info = info_by_qid.get(record.question_id)
        class_rate = info.class_rate if info is not None else None
        item: dict[str, Any] = {
            "question_id": record.question_id,
            "type": record_type_label(record, info),
            "max_score": record.max_score,
            "score": record.score,
            "class_rate": round(class_rate, 4) if class_rate is not None else None,
            "lost": record.lost,
        }
        if record.lost:
            item["stem_summary"] = (
                info.stem_summary if info is not None and info.stem_summary else record.question_id
            )
            item["canonical_answer"] = (
                info.canonical_answer if info is not None else ""
            )
            item["student_answer"] = record.student_answer or None
            item["grading_record"] = {
                "deduction_reason": record.deduction_reason or None,
                "error_category": record.error_category or None,
                "error_summary": record.error_summary or None,
                "secondary_errors": record.secondary_errors,
            }
        questions.append(item)
    return {
        "exam": {
            "title": data.session_name,
            "subject": data.subject,
            "full_score": data.full_score,
            "graded_at": student.graded_at,
        },
        "student": {
            "alias": PERSONAL_STUDENT_ALIAS,
            "total_score": student.student_score,
            "class_stats": {
                "n": data.present,
                "avg": data.stats["avg"],
                "median": data.stats["median"],
                "max": data.stats["max"],
                "min": data.stats["min"],
                "rank": student.rank,
            },
            "small_sample": data.small_sample,
        },
        "questions": questions,
    }


def record_type_label(
    record: _StudentQuestionRecord,
    info: _QuestionInfo | None,
) -> str:
    return str(info.question_type if info is not None else "") or ""


def build_class_payload(data: _SessionAnalysisData) -> dict[str, Any]:
    aliases = {
        student.student_id: f"{CLASS_ALIAS_PREFIX}{index}"
        for index, student in enumerate(data.students, start=1)
    }
    bands = {
        band["label"].replace(" ", ""): band["count"] for band in data.stats["bands"]
    }
    students_payload: list[dict[str, Any]] = []
    for student in data.students:
        students_payload.append(
            {
                "alias": aliases[student.student_id],
                "total": student.student_score,
                "needs_review": student.needs_review,
                "lost": [
                    {
                        "question_id": record.question_id,
                        "lost_points": round(record.lost_points, 2),
                        "record": _record_brief_text(record),
                    }
                    for record in student.records
                    if record.lost
                ],
            }
        )
    records_by_question: dict[str, list[dict[str, Any]]] = {}
    for student in data.students:
        for record in student.records:
            if not record.lost:
                continue
            records_by_question.setdefault(record.question_id, []).append(
                {
                    "alias": aliases[student.student_id],
                    "score": record.score,
                    "deduction_reason": record.deduction_reason or None,
                    "error_category": record.error_category or None,
                }
            )
    questions_payload = [
        {
            "question_id": info.question_id,
            "max_score": info.max_score,
            "class_rate": (
                round(info.class_rate, 4) if info.class_rate is not None else None
            ),
            "stem_summary": info.stem_summary or info.question_id,
            "canonical_answer": info.canonical_answer,
            "records": records_by_question.get(info.question_id, []),
        }
        for info in data.questions
    ]
    return {
        "exam": {
            "title": data.session_name,
            "subject": data.subject,
            "full_score": data.full_score,
            "present": data.present,
            "roster_absent": data.roster_absent,
        },
        "score_distribution": {
            "avg": data.stats["avg"],
            "median": data.stats["median"],
            "max": data.stats["max"],
            "min": data.stats["min"],
            "pass_rate": data.stats["pass_rate"],
            "bands": bands,
        },
        "students": students_payload,
        "questions": questions_payload,
    }


def _record_brief_text(record: _StudentQuestionRecord) -> str:
    parts = [
        text
        for text in (
            record.deduction_reason,
            record.error_category,
            record.error_summary,
        )
        if text
    ]
    return "；".join(parts) or "未作答或无批改记录"


# ---------------------------------------------------------------------------
# 班级分析内嵌页面的出参装配（教师本人页面，学生用真实姓名，不脱敏）
# ---------------------------------------------------------------------------


def build_class_page_data(data: _SessionAnalysisData) -> dict[str, Any]:
    """把班级版装配数据序列化为页面 JSON：考试信息、分数分布、逐题与逐生明细。

    出参形状与前端解码器 frontend/src/api/class-analysis.ts 冻结对齐：
    - present / roster_absent（姓名数组）在 data 顶层；
    - score_distribution.bands 为 {分数段: 人数} 字典；
    - students[].lost[] 为 {question_id, lost_points, record}；
    - questions[].class_rate 恒为数字（无作答记录时 0）。
    """
    bands = {
        str(band["label"]): int(band["count"]) for band in data.stats["bands"]
    }
    absent_names = [
        name
        for item in data.skipped
        if str(item.get("reason") or "") in {"缺考", "扫描异常"}
        and (name := str(item.get("student_name") or "").strip())
    ]
    questions: list[dict[str, Any]] = []
    for info in data.questions:
        records: list[dict[str, Any]] = []
        for student in data.students:
            for record in student.records:
                if record.question_id != info.question_id or not record.lost:
                    continue
                records.append(
                    {
                        "student_id": student.student_id,
                        "student_code": student.student_code,
                        "student_name": student.student_name,
                        "score": record.score,
                        "max_score": record.max_score,
                        "lost_points": round(record.lost_points, 2),
                        "deduction_reason": record.deduction_reason or None,
                        "error_category": record.error_category or None,
                        "error_summary": record.error_summary or None,
                    }
                )
        questions.append(
            {
                "question_id": info.question_id,
                "question_type": info.question_type,
                "max_score": info.max_score,
                "class_rate": (
                    round(info.class_rate, 4) if info.class_rate is not None else 0
                ),
                "class_avg": (
                    round(info.class_avg, 2) if info.class_avg is not None else None
                ),
                "stem_summary": info.stem_summary,
                "canonical_answer": info.canonical_answer,
                "records": records,
            }
        )
    students: list[dict[str, Any]] = []
    for student in data.students:
        lost_items: list[dict[str, Any]] = []
        for record in student.records:
            if not record.lost:
                continue
            record_payload = {
                "deduction_reason": record.deduction_reason or None,
                "error_category": record.error_category or None,
                "error_summary": record.error_summary or None,
            }
            if not any(record_payload.values()):
                record_payload = None
            lost_items.append(
                {
                    "question_id": record.question_id,
                    "lost_points": round(record.lost_points, 2),
                    "record": record_payload,
                }
            )
        students.append(
            {
                "student_id": student.student_id,
                "student_code": student.student_code,
                "student_name": student.student_name,
                "class_name": student.class_name,
                "total_score": student.student_score,
                "rank": student.rank,
                "needs_review": student.needs_review,
                "lost_points_total": round(student.lost_points_total, 2),
                "lost": lost_items,
            }
        )
    stats = data.stats
    return {
        "exam": {
            "session_id": data.session_id,
            "title": data.session_name,
            "subject": data.subject,
            "full_score": data.full_score,
            "graded_at": data.graded_at,
        },
        "present": data.present,
        "roster_absent": absent_names,
        "score_distribution": {
            "avg": stats["avg"],
            "median": stats["median"],
            "max": stats["max"],
            "min": stats["min"],
            "pass_rate": stats["pass_rate"],
            "bands": bands,
        },
        "questions": questions,
        "students": students,
        "skipped": data.skipped,
    }


def class_narrative_with_student_names(
    narrative: dict[str, Any],
    students: list[_StudentReportData],
) -> dict[str, Any]:
    """把班级叙述 student_notes 里的 S1/S2… 代号在服务端映射回真实姓名与学号。"""
    alias_to_student = {
        f"{CLASS_ALIAS_PREFIX}{index}": student
        for index, student in enumerate(students, start=1)
    }
    mapped = dict(narrative)
    notes: list[dict[str, Any]] = []
    for item in _narrative_items(narrative, "student_notes"):
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        student = alias_to_student.get(_narrative_text(item.get("alias")))
        if student is not None:
            entry["student_name"] = student.student_name
            entry["student_code"] = student.student_code
        notes.append(entry)
    mapped["student_notes"] = notes
    return mapped


def build_report_prompt(
    system_prompt: str,
    payload: dict[str, Any],
) -> str:
    return (
        system_prompt
        + "\n\n输入 JSON：\n"
        + json.dumps(payload, ensure_ascii=False)
    )


# ---------------------------------------------------------------------------
# 错题截图（方案 §4）
# ---------------------------------------------------------------------------


def _find_question_region(
    regions: list[dict[str, Any]],
    question_id: str,
) -> dict[str, Any] | None:
    """按题号找作答区域：精确坐标优先，小问回退父题，父题取唯一子区域。"""
    target = question_id_coordinates(question_id)

    def region_coordinates(region: dict[str, Any]) -> tuple[int, int | None] | None:
        return question_id_coordinates(
            str(
                region.get("mapped_question_id")
                or region.get("detected_question_id")
                or ""
            ).strip()
        )

    if target is not None:
        for region in regions:
            if region_coordinates(region) == target:
                return region
        target_parent, target_part = target
        if target_part is not None:
            for region in regions:
                if region_coordinates(region) == (target_parent, None):
                    return region
        else:
            children = [
                region
                for region in regions
                if (identity := region_coordinates(region)) is not None
                and identity[0] == target_parent
                and identity[1] is not None
            ]
            if len(children) == 1:
                return children[0]
        return None
    normalized = str(question_id or "").strip()
    if not normalized:
        return None
    exact_values = {
        normalized,
        normalized[1:] if normalized.upper().startswith("Q") else f"Q{normalized}",
    }
    for region in regions:
        if (
            str(
                region.get("mapped_question_id")
                or region.get("detected_question_id")
                or ""
            ).strip()
            in exact_values
        ):
            return region
    return None


def _region_page(region: dict[str, Any]) -> str:
    return (
        "back"
        if str(region.get("page") or "front").strip().lower() == "back"
        else "front"
    )


def _crop_region_data_uri(image_path: Path, region: dict[str, Any]) -> str | None:
    try:
        with Image.open(image_path) as image:
            image.load()
            bbox = scaled_region_bbox(
                region,
                int(image.width),
                int(image.height),
                padding=_SHOT_PADDING,
            )
            cropped = image.crop(bbox)
            if cropped.width > _SHOT_MAX_WIDTH:
                ratio = _SHOT_MAX_WIDTH / cropped.width
                cropped = cropped.resize(
                    (_SHOT_MAX_WIDTH, max(1, round(cropped.height * ratio)))
                )
            buffer = io.BytesIO()
            cropped.convert("RGB").save(
                buffer,
                format="JPEG",
                quality=_SHOT_JPEG_QUALITY,
            )
    except Exception:
        return None
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def capture_lost_question_shots(
    repositories: GradingRepositoryAccess,
    data: _SessionAnalysisData,
    student: _StudentReportData,
    *,
    regions: list[dict[str, Any]] | None,
    data_root: Path | None,
) -> dict[str, dict[str, str]]:
    """为丢分大题生成截图，key 为父题号；任何缺失都降级为无图，不中断。"""
    if not regions:
        return {}
    lost_records = [record for record in student.records if record.lost]
    if not lost_records:
        return {}

    annotated = None
    try:
        annotated = repositories.reviews.get_annotated_result(student.result_id)
    except Exception:
        annotated = None
    paper_context = None
    try:
        paper_context = repositories.results.get_result_context(student.result_id)
    except Exception:
        paper_context = None

    # 区域粒度是整道大题：同一大题多小问丢分只嵌一次，按丢分从大到小排序。
    lost_by_parent: dict[str, float] = {}
    for record in lost_records:
        parent = _parent_question_id(record.question_id)
        lost_by_parent[parent] = lost_by_parent.get(parent, 0.0) + record.lost_points
    ordered_parents = sorted(
        lost_by_parent,
        key=lambda parent: (-lost_by_parent[parent], parent),
    )

    shots: dict[str, dict[str, str]] = {}
    used_bytes = 0
    for parent in ordered_parents:
        region = _find_question_region(regions, parent)
        if region is None:
            continue
        page = _region_page(region)
        source_label = "批注截图"
        raw_path = (
            str(annotated.get(f"annotated_{page}_path") or "")
            if isinstance(annotated, dict)
            else ""
        )
        if not raw_path and isinstance(paper_context, dict):
            raw_path = str(paper_context.get(f"{page}_image") or "")
            source_label = "原卷截图"
        if not raw_path:
            continue
        image_path = resolve_stored_file_path(raw_path, data_root=data_root)
        data_uri = _crop_region_data_uri(image_path, region)
        if data_uri is None:
            continue
        encoded_size = len(data_uri) * 3 // 4
        if used_bytes + encoded_size > _SHOT_BUDGET_BYTES:
            break
        used_bytes += encoded_size
        shots[parent] = {
            "data_uri": data_uri,
            "caption": f"{_question_display_label(parent)}作答区截图（{source_label}）",
        }
    return shots


def load_session_regions(
    repositories: GradingRepositoryAccess,
    session_id: int,
    *,
    data_root: Path | None,
) -> list[dict[str, Any]]:
    try:
        return answer_regions_with_template_source_sizes(
            repositories,
            int(session_id),
            data_root=data_root,
        )
    except Exception:
        return []


# ---------------------------------------------------------------------------
# HTML 渲染（版面结构与视觉按已确认原型，自包含、截图 base64 内嵌）
# ---------------------------------------------------------------------------

_PERSONAL_CSS = """
  :root {
    --ink: #1f2937;
    --muted: #6b7280;
    --line: #e5e7eb;
    --brand: #2563eb;
    --brand-soft: #eff6ff;
    --good: #059669;
    --good-soft: #ecfdf5;
    --warn: #d97706;
    --warn-soft: #fffbeb;
    --bad: #dc2626;
    --bad-soft: #fef2f2;
    --bar-me: #2563eb;
    --bar-class: #cbd5e1;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: "Microsoft YaHei", "PingFang SC", system-ui, sans-serif;
    color: var(--ink);
    background: #f1f5f9;
    line-height: 1.65;
    font-size: 14px;
  }
  .page { max-width: 860px; margin: 0 auto; padding: 24px 16px 48px; }
  .sheet { background: #fff; border-radius: 14px; box-shadow: 0 1px 3px rgba(0,0,0,.08); overflow: hidden; }

  /* 顶栏 */
  .hero { background: linear-gradient(135deg, #1d4ed8, #3b82f6); color: #fff; padding: 26px 32px 22px; }
  .hero .tag { display: inline-block; font-size: 12px; background: rgba(255,255,255,.18); border: 1px solid rgba(255,255,255,.35); border-radius: 999px; padding: 2px 10px; margin-bottom: 10px; }
  .hero h1 { font-size: 22px; font-weight: 700; letter-spacing: .5px; }
  .hero .sub { margin-top: 6px; font-size: 13px; opacity: .92; }
  .hero .sub span + span::before { content: "　·　"; opacity: .6; }

  section { padding: 22px 32px; border-top: 1px solid var(--line); }
  h2 { font-size: 16px; margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }
  h2::before { content: ""; width: 4px; height: 16px; border-radius: 2px; background: var(--brand); }
  .note { font-size: 12px; color: var(--muted); }

  /* 总览卡 */
  .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
  .stat { border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; text-align: center; }
  .stat .num { font-size: 26px; font-weight: 700; color: var(--brand); }
  .stat .num small { font-size: 13px; color: var(--muted); font-weight: 400; }
  .stat .lbl { font-size: 12px; color: var(--muted); margin-top: 2px; }

  /* 得分对比条 */
  .qrow { display: grid; grid-template-columns: max-content 1fr max-content; align-items: center; gap: 10px; padding: 4px 0; }
  .qrow .qid { font-size: 12.5px; color: var(--ink); white-space: nowrap; }
  .bars { display: flex; flex-direction: column; gap: 3px; }
  .bar { height: 8px; border-radius: 4px; background: #f1f5f9; position: relative; overflow: hidden; }
  .bar i { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 4px; }
  .bar.me i { background: var(--bar-me); }
  .bar.cls i { background: var(--bar-class); }
  .qrow .val { font-size: 12px; color: var(--muted); text-align: right; white-space: nowrap; }
  .qrow.lost .qid { color: var(--bad); font-weight: 600; }
  .qrow.lost .bar.me i { background: var(--bad); }
  .legend { display: flex; gap: 18px; font-size: 12px; color: var(--muted); margin-bottom: 10px; }
  .legend i { display: inline-block; width: 18px; height: 8px; border-radius: 4px; vertical-align: middle; margin-right: 5px; }

  /* 丢分题卡片 */
  .qcard { border: 1px solid var(--line); border-left: 4px solid var(--bad); border-radius: 10px; padding: 14px 16px; margin-bottom: 14px; }
  .qcard .head { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
  .qcard .head b { font-size: 14.5px; }
  .qcard .score { font-size: 13px; color: var(--bad); font-weight: 700; }
  .qcard .stem { font-size: 13px; color: var(--muted); background: #f8fafc; border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; }
  .kv { display: grid; grid-template-columns: 88px 1fr; gap: 4px 10px; font-size: 13px; }
  .kv dt { color: var(--muted); }
  .kv dd b.ans { color: var(--good); }
  .aidraft { margin-top: 10px; background: var(--warn-soft); border: 1px dashed #f5d08c; border-radius: 8px; padding: 10px 12px; font-size: 13px; }
  .aidraft .cap { font-size: 11.5px; color: var(--warn); font-weight: 700; margin-bottom: 4px; }

  /* 答卷截图 */
  .shot { border: 1px solid var(--line); border-radius: 8px; padding: 8px; margin: 10px 0 2px; background: #fcfcfd; }
  .shot img { max-width: 100%; display: block; margin: 0 auto; border-radius: 4px; }
  .shot .cap { font-size: 11.5px; color: var(--muted); margin-top: 6px; text-align: center; }

  /* 亮点 */
  .goodbox { background: var(--good-soft); border: 1px solid #bbe7d4; border-radius: 10px; padding: 12px 16px; font-size: 13.5px; }
  .goodbox li { margin-left: 18px; }

  /* 问题与建议 */
  .pill { display: inline-block; font-size: 11px; font-weight: 700; border-radius: 999px; padding: 1px 9px; margin-right: 6px; vertical-align: 1px; }
  .pill.warn { background: var(--warn-soft); color: var(--warn); border: 1px solid #f2ce8f; }
  .plist > li { margin: 0 0 12px 18px; }
  .plist b.t { display: block; }
  .plist .why { color: var(--muted); font-size: 13px; }

  /* 知识板块 */
  .krow { display: grid; grid-template-columns: 170px 1fr 76px; align-items: center; gap: 10px; padding: 5px 0; font-size: 13px; }
  .krow .bar { height: 10px; background: #f1f5f9; border-radius: 5px; overflow: hidden; position: relative; }
  .krow .bar i { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 5px; }
  .krow .pct { text-align: right; font-weight: 600; }
  .krow .pct.p100 { color: var(--good); }
  .krow .pct.p0 { color: var(--bad); }

  /* 页脚 */
  footer { padding: 18px 32px 26px; border-top: 1px solid var(--line); font-size: 12px; color: var(--muted); }
  .sign { display: flex; justify-content: space-between; margin-top: 14px; padding-top: 14px; border-top: 1px dashed var(--line); }

  @media print {
    body { background: #fff; }
    .page { padding: 0; max-width: none; }
    .sheet { box-shadow: none; border-radius: 0; }
  }
"""


def _narrative_items(narrative: dict[str, Any] | None, key: str) -> list[Any]:
    if not isinstance(narrative, dict):
        return []
    value = narrative.get(key)
    return value if isinstance(value, list) else []


def _narrative_text(value: object) -> str:
    return str(value or "").strip()


def _knowledge_rows(
    backfill: dict[str, list[dict[str, str]]],
    records: list[_StudentQuestionRecord],
) -> list[dict[str, Any]]:
    """按题库知识点标签聚合得分率；无标签数据时返回空，渲染层省略该板块。"""
    buckets: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.max_score <= 0:
            continue
        entries = backfill.get(record.question_id)
        if entries is None:
            entries = backfill.get(_parent_question_id(record.question_id))
        for entry in entries or []:
            path = str(entry.get("path") or "").strip()
            if not path:
                continue
            bucket = buckets.setdefault(
                path,
                {
                    "label": str(entry.get("label") or "").strip() or "未命名知识点",
                    "score": 0.0,
                    "full": 0.0,
                },
            )
            bucket["score"] += min(record.score, record.max_score)
            bucket["full"] += record.max_score
    rows = []
    for bucket in buckets.values():
        full = float(bucket["full"])
        rows.append(
            {
                "label": bucket["label"],
                "rate": (float(bucket["score"]) / full) if full > 0 else None,
            }
        )
    rows.sort(key=lambda row: (row["rate"] is None, row["rate"] or 0))
    return rows


def _ai_block(text: str, *, failed: bool) -> str:
    note = AI_FAILED_NOTE if failed else text
    return (
        '<div class="aidraft">'
        f'<div class="cap">{_esc(AI_DISCLAIMER)}</div>{_esc(note)}</div>'
    )


def _render_personal_html(
    data: _SessionAnalysisData,
    student: _StudentReportData,
    narrative: dict[str, Any] | None,
    shots: dict[str, dict[str, str]],
) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d")
    info_by_qid = {info.question_id: info for info in data.questions}
    ai_failed = narrative is None
    analysis_by_qid = {
        _narrative_text(item.get("question_id")): _narrative_text(item.get("analysis"))
        for item in _narrative_items(narrative, "question_analyses")
        if isinstance(item, dict)
    }

    hero_review_note = (
        '<div class="sub"><span>⚠ 本卷有待复核题目，成绩可能调整</span></div>'
        if student.needs_review
        else ""
    )
    lost_questions = [record for record in student.records if record.lost]
    small_sample_note = (
        '<p class="note" style="margin-top:8px;">本场参考人数较少，班级对比仅供参考。</p>'
        if data.small_sample
        else ""
    )
    overall_comment = _narrative_text(
        (narrative or {}).get("overall_comment") if isinstance(narrative, dict) else ""
    )

    compare_rows: list[str] = []
    for record in student.records:
        info = info_by_qid.get(record.question_id)
        my_rate = (
            record.score / record.max_score if record.max_score > 0 else 0.0
        )
        class_rate = info.class_rate if info is not None else None
        class_avg = info.class_avg if info is not None else None
        # 题型标签只标在大题行；小问行题号已含层级信息（对齐已确认原型）。
        coordinates = question_id_coordinates(record.question_id)
        is_part_row = coordinates is not None and coordinates[1] is not None
        type_label = (
            ""
            if is_part_row
            else _question_type_label(info.question_type if info is not None else "")
        )
        row_class = "qrow lost" if record.lost else "qrow"
        avg_text = f"（班均 {_fmt_num(round(class_avg, 1))}）" if class_avg is not None else ""
        type_suffix = f" {_esc(type_label)}" if type_label else ""
        compare_rows.append(
            f'<div class="{row_class}"><span class="qid">'
            f'{_esc(_question_display_label(record.question_id))}{type_suffix}</span>'
            f'<div class="bars"><div class="bar me"><i style="width:{my_rate * 100:.0f}%"></i></div>'
            f'<div class="bar cls"><i style="width:{(class_rate or 0) * 100:.0f}%"></i></div></div>'
            f'<span class="val">{_fmt_num(record.score)} / {_fmt_num(record.max_score)}{_esc(avg_text)}</span></div>'
        )

    # 丢分题按大题聚合：同一大题多小问丢分合并为一张卡片。
    lost_groups: dict[str, list[_StudentQuestionRecord]] = {}
    for record in lost_questions:
        lost_groups.setdefault(_parent_question_id(record.question_id), []).append(record)
    ordered_groups = sorted(
        lost_groups.items(),
        key=lambda item: (
            -sum(record.lost_points for record in item[1]),
            _question_display_label(item[0]),
        ),
    )
    question_cards: list[str] = []
    for parent, group_records in ordered_groups:
        first = group_records[0]
        info = info_by_qid.get(first.question_id)
        group_score = sum(record.score for record in group_records)
        group_max = sum(record.max_score for record in group_records)
        group_avg = None
        if info is not None and info.class_avg is not None and len(group_records) == 1:
            group_avg = info.class_avg
        if len(group_records) > 1:
            part_labels = "、".join(
                str((question_id_coordinates(record.question_id) or (0, 0))[1] or "")
                for record in group_records
            )
            title = f"第{(question_id_coordinates(parent) or (0,))[0]}题（第 {part_labels} 问）"
        else:
            title = _question_display_label(first.question_id)
        type_label = _question_type_label(info.question_type if info is not None else "")
        stem = (info.stem_summary if info is not None else "") or title
        answer = info.canonical_answer if info is not None else ""
        record_text = "；".join(
            text
            for text in (
                first.deduction_reason,
                first.error_category,
                first.error_summary,
                *first.secondary_errors,
            )
            if text
        )
        kv_rows = []
        student_answer_text = "；".join(
            dict.fromkeys(
                record.student_answer
                for record in group_records
                if record.student_answer
            )
        )
        if student_answer_text:
            kv_rows.append(f"<dt>学生作答</dt><dd>{_esc(student_answer_text)}</dd>")
        if answer:
            kv_rows.append(
                f'<dt>标准答案</dt><dd><b class="ans">{_esc(answer)}</b></dd>'
            )
        if record_text:
            kv_rows.append(f"<dt>批改记录</dt><dd>{_esc(record_text)}</dd>")
        avg_suffix = (
            f"（班均 {_fmt_num(round(group_avg, 1))}）" if group_avg is not None else ""
        )
        shot = shots.get(parent)
        shot_html = ""
        if shot is not None:
            shot_html = (
                '<div class="shot">'
                f'<img src="{shot["data_uri"]}" alt="{_esc(title)}作答截图">'
                f'<div class="cap">{_esc(shot["caption"])}</div></div>'
            )
        analysis_texts = [
            text
            for record in group_records
            if (text := analysis_by_qid.get(record.question_id))
        ]
        question_cards.append(
            '<div class="qcard">'
            f'<div class="head"><b>{_esc(title)}'
            f'{(" · " + _esc(type_label)) if type_label else ""}</b>'
            f'<span class="score">得 {_fmt_num(group_score)} 分 / 满分 {_fmt_num(group_max)} 分{_esc(avg_suffix)}</span></div>'
            f'<div class="stem">{_esc(stem)}</div>'
            + (f'<dl class="kv">{"".join(kv_rows)}</dl>' if kv_rows else "")
            + shot_html
            + _ai_block("；".join(analysis_texts), failed=ai_failed or not analysis_texts)
            + "</div>"
        )

    strengths = [
        _narrative_text(item)
        for item in _narrative_items(narrative, "strengths")
        if _narrative_text(item)
    ][:3]
    if strengths:
        strengths_html = (
            '<div class="goodbox"><ul>'
            + "".join(f"<li>{_esc(item)}</li>" for item in strengths)
            + "</ul></div>"
        )
    else:
        strengths_html = f'<div class="goodbox">{_esc(AI_FAILED_NOTE)}</div>'

    problems_html = _render_titled_items(
        _narrative_items(narrative, "problems"),
        failed=ai_failed,
    )
    suggestions_items = []
    for item in _narrative_items(narrative, "suggestions"):
        if not isinstance(item, dict):
            continue
        timeframe = _narrative_text(item.get("timeframe"))
        title = _narrative_text(item.get("title"))
        detail = _narrative_text(item.get("detail"))
        label = f"{title}（{timeframe}）" if timeframe else title
        suggestions_items.append({"title": label, "detail": detail})
    suggestions_html = _render_titled_items(suggestions_items, failed=ai_failed)

    knowledge_rows = _knowledge_rows(data.knowledge_backfill, student.records)
    knowledge_html = ""
    if knowledge_rows:
        rows_html = []
        for row in knowledge_rows:
            rate = row["rate"]
            pct = f"{rate * 100:.0f}" if rate is not None else "0"
            pct_class = "pct"
            color = "var(--brand)"
            if rate is not None and rate >= 0.999:
                pct_class = "pct p100"
                color = "var(--good)"
            elif rate is not None and rate < 0.4:
                pct_class = "pct p0"
                color = "var(--bad)"
            rows_html.append(
                f'<div class="krow"><span>{_esc(row["label"])}</span>'
                f'<div class="bar"><i style="width:{pct}%; background:{color}"></i></div>'
                f'<span class="{pct_class}">{pct}%</span></div>'
            )
        knowledge_html = (
            "<section><h2>知识板块得分小结</h2>"
            + "".join(rows_html)
            + "</section>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>个人考试分析报告 · {_esc(student.student_name)} · {_esc(data.session_name)}</title>
<style>{_PERSONAL_CSS}</style>
</head>
<body>
<div class="page">
  <div class="sheet">

    <div class="hero">
      <span class="tag">个人考试分析报告 · 家长版</span>
      <h1>{_esc(student.student_name)} · {_esc(data.session_name)}</h1>
      <div class="sub">
        <span>{_esc(student.class_name)}</span><span>学号 {_esc(student.student_code)}</span><span>批改时间 {_esc(student.graded_at)}</span><span>满分 {_fmt_num(data.full_score)} 分</span>
      </div>
      {hero_review_note}
    </div>

    <section>
      <h2>成绩总览</h2>
      <div class="stats">
        <div class="stat"><div class="num">{_fmt_num(student.student_score)}<small> / {_fmt_num(data.full_score)}</small></div><div class="lbl">本次得分</div></div>
        <div class="stat"><div class="num">{student.rank}<small> / {data.present}</small></div><div class="lbl">班级名次</div></div>
        <div class="stat"><div class="num">{_fmt_num(data.stats["avg"])}</div><div class="lbl">班级平均分</div></div>
        <div class="stat"><div class="num">{_fmt_num(student.lost_points_total)}<small> 分</small></div><div class="lbl">总丢分（集中在 {len(ordered_groups)} 处）</div></div>
      </div>
      {small_sample_note}
      <p style="margin-top:12px; font-size:13.5px;">{_esc(overall_comment) if overall_comment else _esc(AI_FAILED_NOTE)}</p>
    </section>

    <section>
      <h2>逐题得分对比</h2>
      <div class="legend">
        <span><i style="background:var(--bar-me)"></i>{_esc(student.student_name)}得分率</span>
        <span><i style="background:var(--bar-class)"></i>班级平均得分率</span>
        <span style="color:var(--bad)">红色 = 本次丢分题</span>
      </div>
      {''.join(compare_rows)}
    </section>

    <section>
      <h2>丢分题逐题分析</h2>
      {''.join(question_cards) if question_cards else '<p class="note">本次考试没有丢分题。</p>'}
    </section>

    <section>
      <h2>本次亮点</h2>
      {strengths_html}
    </section>

    <section>
      <h2>本次考试暴露的问题 <span class="pill warn">{_esc(AI_DISCLAIMER)}</span></h2>
      {problems_html}
    </section>

    <section>
      <h2>针对性学习建议 <span class="pill warn">{_esc(AI_DISCLAIMER)}</span></h2>
      {suggestions_html}
    </section>

    {knowledge_html}

    <footer>
      <p>说明：本报告由系统根据阅卷评分记录自动生成。班级对比仅使用匿名统计（平均分、得分率），不含其他学生个人信息。
      标有“{_esc(AI_DISCLAIMER)}”的内容为人工智能辅助生成，仅供参考，如有疑问请与任课教师沟通。</p>
      <div class="sign">
        <span>AI 阅卷系统 · 自动生成</span>
        <span>报告生成时间：{generated_at}</span>
      </div>
    </footer>

  </div>
</div>
</body>
</html>
"""


def _render_titled_items(items: list[Any], *, failed: bool) -> str:
    entries: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = _narrative_text(item.get("title"))
        detail = _narrative_text(item.get("detail"))
        if not title and not detail:
            continue
        entries.append(
            f'<li><b class="t">{_esc(title)}</b><span class="why">{_esc(detail)}</span></li>'
        )
    if not entries:
        return f'<ol class="plist"><li><b class="t">{_esc(AI_FAILED_NOTE)}</b></li></ol>'
    return f'<ol class="plist">{"".join(entries)}</ol>'


# ---------------------------------------------------------------------------
# 生成器与 preflight
# ---------------------------------------------------------------------------


class AnalysisReportGenerator:
    """契约与 ReportGenerator 一致：(repositories, output_dir) 构造，导出返回 Path。"""

    def __init__(
        self,
        db: GradingRepositoryAccess | Path,
        output_dir: Path,
        *,
        llm_client_factory: Callable[[], Any] | None = None,
        narrative_cache_dir: Path | None = None,
        data_root: Path | None = None,
    ) -> None:
        self.repositories = (
            open_grading_repositories(Path(db))
            if isinstance(db, Path)
            else as_grading_repositories(db)
        )
        self.db_path = self.repositories.db_path
        self.output_dir = Path(output_dir)
        self.llm_client_factory = llm_client_factory
        self.cache = (
            AnalysisNarrativeCache(narrative_cache_dir)
            if narrative_cache_dir is not None
            else None
        )
        self.data_root = data_root or _infer_data_root(self.db_path)
        self._llm_client: Any = None
        self._llm_client_resolved = False

    def export_session(
        self,
        session_id: int,
        report_type: str,
        *,
        score_revision: str = "",
    ) -> Path:
        if report_type not in ANALYSIS_REPORT_TYPES:
            raise ValueError(f"不支持的分析报告类型: {report_type}")
        revision = str(score_revision or "").strip() or _compute_score_revision(
            self.repositories,
            int(session_id),
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        data = assemble_session_analysis(
            self.repositories,
            int(session_id),
            data_root=self.data_root,
        )
        return self._export_personal(data, revision)

    def _client(self) -> Any:
        # 未配置内容生成模型时返回 None，全部报告降级为无 AI 叙述版，
        # 不静默改用阅卷模型（方案 §5）。
        if not self._llm_client_resolved:
            self._llm_client_resolved = True
            self._llm_client = (
                self.llm_client_factory()
                if self.llm_client_factory is not None
                else None
            )
        return self._llm_client

    def _narrative(
        self,
        *,
        session_id: int,
        revision: str,
        report_type: str,
        report_key: str,
        prompt: str,
        max_tokens: int,
    ) -> dict[str, Any] | None:
        from backend.report_exports import report_rendition_version

        key = AnalysisNarrativeCache.cache_key(
            session_id=session_id,
            score_revision=revision,
            rendition_version=report_rendition_version(report_type),
            report_key=report_key,
        )
        if self.cache is not None:
            cached = self.cache.load(key)
            if cached is not None:
                return cached
        client = self._client()
        if client is None:
            return None
        try:
            narrative = client.json_from_text(
                prompt,
                extra_kwargs={"temperature": 0.3, "max_tokens": max_tokens},
            )
        except Exception:
            # 模型超时/解析失败：本地修复仍失败则降级，不暗中重发（已确认偏差）。
            return None
        if not isinstance(narrative, dict):
            return None
        if self.cache is not None:
            self.cache.store(key, narrative)
        return narrative

    def _export_personal(
        self,
        data: _SessionAnalysisData,
        revision: str,
    ) -> Path:
        if not data.students:
            raise ValueError("该场次没有可生成个人报告的学生。")
        regions = load_session_regions(
            self.repositories,
            data.session_id,
            data_root=self.data_root,
        )
        staging_subdir = self.output_dir / "personal_reports"
        staging_subdir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        used_names: set[str] = set()
        report_files: list[Path] = []
        for student in data.students:
            payload = build_personal_payload(data, student)
            narrative = self._narrative(
                session_id=data.session_id,
                revision=revision,
                report_type=PERSONAL_ANALYSIS_REPORT_TYPE,
                report_key=f"personal:{student.student_id}",
                prompt=build_report_prompt(PERSONAL_SYSTEM_PROMPT, payload),
                max_tokens=PERSONAL_MAX_TOKENS,
            )
            shots = capture_lost_question_shots(
                self.repositories,
                data,
                student,
                regions=regions,
                data_root=self.data_root,
            )
            html_text = _render_personal_html(data, student, narrative, shots)
            filename = (
                f"{safe_filename_fragment(student.student_code, '未知学号')}"
                f"_{safe_filename_fragment(student.student_name, '未知姓名')}"
                "_个人报告.html"
            )
            if filename in used_names:
                filename = filename.replace(".html", f"_{student.student_id}.html")
            used_names.add(filename)
            report_path = staging_subdir / filename
            report_path.write_text(html_text, encoding="utf-8")
            report_files.append(report_path)

        checklist_lines = [
            "未生成个人报告的学生清单",
            f"场次：{data.session_name}",
            "",
        ]
        if data.skipped:
            checklist_lines.extend(
                f"{item['class_name']} {item['student_code']} {item['student_name']}：{item['reason']}"
                for item in data.skipped
            )
        else:
            checklist_lines.append("无")
        checklist_path = staging_subdir / "未生成清单.txt"
        checklist_path.write_text("\n".join(checklist_lines) + "\n", encoding="utf-8")

        zip_path = self.output_dir / session_export_path_name(
            data.session_name,
            "个人分析报告",
            "zip",
            timestamp,
        )
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in [*report_files, checklist_path]:
                archive.write(path, arcname=path.name)
        return zip_path


def _compute_score_revision(
    repositories: GradingRepositoryAccess,
    session_id: int,
) -> str:
    from backend.report_exports import score_revision

    return score_revision(repositories, session_id)


def build_analysis_preflight(
    db: GradingRepositoryAccess | Any,
    session_id: int,
    report_type: str,
    *,
    score_revision: str,
    cache_dir: Path,
) -> dict[str, Any]:
    """生成前的费用与调用预估：只给 token 粗估，费用取决于服务商定价。"""
    if report_type not in ANALYSIS_REPORT_TYPES:
        raise ValueError(f"不支持的分析报告类型: {report_type}")
    from backend.report_exports import report_rendition_version

    repositories = as_grading_repositories(db)
    data = assemble_session_analysis(repositories, int(session_id))
    cache = AnalysisNarrativeCache(cache_dir)
    rendition = report_rendition_version(report_type)
    entries = [
        (
            f"personal:{student.student_id}",
            build_report_prompt(
                PERSONAL_SYSTEM_PROMPT,
                build_personal_payload(data, student),
            ),
            PERSONAL_MAX_TOKENS,
        )
        for student in data.students
    ]

    cache_hits = 0
    estimated_tokens = 0
    for report_key, prompt, max_tokens in entries:
        key = AnalysisNarrativeCache.cache_key(
            session_id=int(session_id),
            score_revision=score_revision,
            rendition_version=rendition,
            report_key=report_key,
        )
        if cache.load(key) is not None:
            cache_hits += 1
            continue
        # 粗估 = 输入 token（字符数/1.5）+ 输出上限。
        estimated_tokens += estimate_prompt_tokens(prompt) + max_tokens

    configured = resolve_content_generation_settings() is not None
    service_name, model_name = content_generation_public_info()
    return {
        "report_type": report_type,
        "configured": configured,
        "service_name": service_name if configured else None,
        "model_name": model_name if configured else None,
        "call_count": len(entries) - cache_hits,
        "estimated_total_tokens": estimated_tokens,
        "cache_hits": cache_hits,
    }
