"""个人报告生成与只读导出，班级分析页面装配。

每生每场保存一份成功正文。导出读取已保存内容，不初始化模型配置。
"""

from __future__ import annotations

import logging

import base64
import io
import json
import math
import re
import statistics
import zipfile
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image

from backend.reporting.analysis_report_prompts import (
    PERSONAL_STUDENT_ALIAS,
    PERSONAL_SYSTEM_PROMPT,
    personal_output_token_limit,
)
from backend.answer_regions.answer_region_geometry import (
    answer_regions_with_template_source_sizes,
    scaled_region_bbox,
)
from backend.analysis_review_notes import load_session_notes, merge_session_notes
from backend.error_causes import display_error_category
from backend.model_profiles.content_generation import (
    content_generation_public_info,
    resolve_content_generation_settings,
)
from backend.repositories.access import (
    GradingRepositoryAccess,
    as_grading_repositories,
)
from backend.repositories.grading_database import open_grading_repositories
from backend.review.service import REVIEW_CONFIRMED_REASON
from backend.session_analysis import (
    QuestionInfo,
    SessionAnalysisData,
    StudentQuestionRecord,
    StudentReportData,
    assemble_session_analysis,
    enrich_personal_knowledge,
    enrich_personal_questions,
    esc,
    fmt_num,
    has_cjk,
    infer_data_root,
    parent_question_id,
    question_bank_db_path,
    report_math_span,
    split_session_analysis_by_class,
)
from backend.reporting.export_names import safe_filename_fragment, session_export_path_name
from path_manager import resolve_stored_file_path
from backend.question_id_contract import (
    question_id_coordinates,
    resolve_known_question_id,
)

PERSONAL_ANALYSIS_REPORT_TYPE = "personal_analysis_html"
ANALYSIS_REPORT_TYPES = (PERSONAL_ANALYSIS_REPORT_TYPE,)

# 方案 §7/§8 的固定标注文案。
AI_DISCLAIMER = "AI 分析 · 仅供参考"
AI_FAILED_NOTE = "AI 分析生成失败，可重新生成"

# 题目截图同时用于图文分析与报告展示，保留手写公式的清晰度。
_SHOT_PADDING = 12
_SHOT_MAX_WIDTH = 1600
_SHOT_JPEG_QUALITY = 85
_SHOT_BUDGET_BYTES = 12 * 1024 * 1024

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


def estimate_prompt_tokens(text: str) -> int:
    """token 粗估：中文场景约 1.5 字符/token，仅用于生成前的量级提示。"""
    return math.ceil(len(text) / 1.5)


def _question_type_label(question_type: str) -> str:
    text = str(question_type or "").strip()
    if not text:
        return ""
    label = _QUESTION_TYPE_LABELS.get(text)
    if label is not None:
        return label
    # 未收录的英文类型码不原样渲染给家长；已是中文的兼容输入照常显示。
    return text if has_cjk(text) else ""


def _question_display_label(question_id: str) -> str:
    """Q9 → 第9题；Q10(P1) → 第10(1)题。"""
    coordinates = question_id_coordinates(question_id)
    if coordinates is None:
        return str(question_id or "")
    parent, part = coordinates
    if part is None:
        return f"第{parent}题"
    return f"第{parent}({part})题"


# ---------------------------------------------------------------------------
# 模型 payload（方案 §6.2 / §6.4；学生一律用代号，不含姓名与磁盘路径）
# ---------------------------------------------------------------------------


def build_personal_payload(
    data: SessionAnalysisData,
    student: StudentReportData,
) -> dict[str, Any]:
    info_by_qid = {info.question_id: info for info in data.questions}
    questions: list[dict[str, Any]] = []
    for record in student.records:
        info = info_by_qid.get(record.question_id)
        class_rate = info.class_rate if info is not None else None
        direct_knowledge = data.knowledge_backfill.get(
            record.question_id,
            data.knowledge_backfill.get(parent_question_id(record.question_id), []),
        )
        item: dict[str, Any] = {
            "question_id": record.question_id,
            "display_label": _question_display_label(record.question_id),
            "type": record_type_label(record, info),
            "max_score": record.max_score,
            "score": record.score,
            "class_rate": round(class_rate, 4) if class_rate is not None else None,
            "lost": record.lost,
            "direct_knowledge": direct_knowledge,
            "part_assessment": data.question_assessments.get(record.question_id),
        }
        if record.lost:
            focus: dict[str, list[str]] = {"topics": [], "skills": []}
            for entry in direct_knowledge:
                label = str(entry.get("label") or entry.get("path") or "")
                label = label.replace("|", "｜").split("｜")[-1].strip()
                is_skill = label.startswith("技能·") or str(
                    entry.get("stable_key") or ""
                ).startswith("sk_")
                label = label.removeprefix("技能·")
                labels = focus["skills" if is_skill else "topics"]
                if label and has_cjk(label) and label not in labels:
                    labels.append(label)
            item["knowledge_focus"] = focus
            item["stem_summary"] = (
                info.stem_summary if info is not None and info.stem_summary else record.question_id
            )
            item["canonical_answer"] = (
                info.canonical_answer if info is not None else ""
            )
            item["student_answer"] = record.student_answer or None
            item["question_text"] = info.question_text if info is not None else ""
            item["reference_analysis"] = info.reference_analysis if info is not None else ""
            item["grading_record"] = {
                "deduction_reason": record.deduction_reason or None,
                "error_category": display_error_category(record.error_category) or None,
                "error_summary": record.error_summary or None,
                "secondary_errors": record.secondary_errors,
                "evidence_steps": record.evidence_steps,
                "missing_steps": record.missing_steps,
                "teacher_confirmed": record.teacher_confirmed,
                "teacher_comment": record.teacher_comment or None,
            }
        questions.append(item)
    return {
        "exam": {
            "title": data.session_name,
            "class_name": data.class_name,
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
        "knowledge_statistics_note": "知识点按可匹配的小问直接考查范围统计；小问难度是公式难度，得分率是本次考试表现，均不能直接当作当前掌握度。综合小问的失分不能推断为其中每个知识点或步骤都不会。",
    }


def record_type_label(
    record: StudentQuestionRecord,
    info: QuestionInfo | None,
) -> str:
    return str(info.question_type if info is not None else "") or ""


# ---------------------------------------------------------------------------
# 班级分析内嵌页面的出参装配（教师本人页面，学生用真实姓名，不脱敏）
# ---------------------------------------------------------------------------


def build_class_page_data(data: SessionAnalysisData, *, compact: bool = False) -> dict[str, Any]:
    """把班级版装配数据序列化为页面 JSON。

    出参形状与前端解码器 frontend/src/api/class-analysis.ts 冻结对齐：
    - present / roster_absent（姓名数组）在 data 顶层；
    - score_distribution.bands 为 {分数段: 人数} 字典；
    - students[].lost[] 为 {question_id, lost_points, record}；
    - questions[].class_rate 恒为数字（无作答记录时 0）。
    - compact 模式只返回逐题失分名单与归并错因，省略逐人清单和重复错因正文。
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
    records_by_question: dict[str, list[tuple[StudentReportData, StudentQuestionRecord]]] = {}
    for student in data.students:
        for record in student.records:
            if record.lost:
                records_by_question.setdefault(record.question_id, []).append((student, record))
    questions: list[dict[str, Any]] = []
    for info in data.questions:
        records: list[dict[str, Any]] = []
        causes: dict[str, dict[str, Any]] = {}
        for student, record in records_by_question.get(info.question_id, []):
            if compact:
                records.append({
                    "student_id": student.student_id,
                    "student_code": student.student_code,
                    "student_name": student.student_name,
                    "class_name": student.class_name,
                    "score": record.score,
                })
                # 导入的成绩没有学生作答内容，不按扣分理由归并错因；
                # 选择题的结构化错因由 apply_cause_results 另行写入。
                if str(getattr(data, "session_kind", "") or "") == "zhixue_import":
                    continue
                reason = record.error_summary or record.deduction_reason or display_error_category(record.error_category) or "未记录具体错因"
                # 仅归并已有记录中的相同错因，不把不同表述推断为同一知识错误。
                seen = set()
                for text in re.split(r"[；;\n]+", reason):
                    text = text.strip().rstrip("。.")
                    if not text:
                        continue
                    key = re.sub(r"\s+", "", text).replace("，", ",").replace("：", ":")
                    if key in seen:
                        continue
                    seen.add(key)
                    bucket = causes.setdefault(key, {"reason": text, "count": 0})
                    bucket["count"] += 1
                if not seen:
                    bucket = causes.setdefault("未记录具体错因", {"reason": "未记录具体错因", "count": 0})
                    bucket["count"] += 1
            else:
                records.append({
                    "student_id": student.student_id,
                    "student_code": student.student_code,
                    "student_name": student.student_name,
                    "score": record.score,
                    "max_score": record.max_score,
                    "lost_points": round(record.lost_points, 2),
                    "deduction_reason": record.deduction_reason or None,
                    "error_category": display_error_category(record.error_category) or None,
                    "error_summary": record.error_summary or None,
                })
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
                **({"causes": sorted(causes.values(), key=lambda item: -item["count"])} if compact else {}),
            }
        )
    students: list[dict[str, Any]] = []
    for student in ([] if compact else data.students):
        lost_items: list[dict[str, Any]] = []
        for record in student.records:
            if not record.lost:
                continue
            record_payload = {
                "deduction_reason": record.deduction_reason or None,
                "error_category": display_error_category(record.error_category) or None,
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


def _find_question_regions(
    regions: list[dict[str, Any]],
    parent: str,
) -> list[dict[str, Any]]:
    """保留父题的全部作答区域；未标整题区域时使用其全部小问区域。"""
    parent_regions: list[dict[str, Any]] = []
    child_regions: list[dict[str, Any]] = []
    for region in regions:
        region_id = str(region.get("mapped_question_id") or region.get("detected_question_id") or "").strip()
        if parent_question_id(region_id) != parent:
            continue
        coordinates = question_id_coordinates(region_id)
        if coordinates is None or coordinates[1] is None:
            parent_regions.append(region)
        else:
            child_regions.append(region)
    return parent_regions or child_regions


def _region_page(region: dict[str, Any]) -> str:
    return (
        "back"
        if str(region.get("page") or "front").strip().lower() == "back"
        else "front"
    )


def _crop_region_data_uri(
    image_path: Path,
    region: dict[str, Any] | None,
    *,
    max_width: int = _SHOT_MAX_WIDTH,
    decoded_image=None,
) -> str | None:
    try:
        from contextlib import nullcontext
        with Image.open(image_path) if decoded_image is None else nullcontext(decoded_image) as image:
            image.load()
            bbox = (
                scaled_region_bbox(region, image.width, image.height, padding=_SHOT_PADDING)
                if region is not None else (0, 0, image.width, image.height)
            )
            cropped = image.crop(bbox)
            if cropped.width > max_width:
                ratio = max_width / cropped.width
                cropped = cropped.resize(
                    (max_width, max(1, round(cropped.height * ratio))),
                    Image.Resampling.LANCZOS,
                )
            buffer = io.BytesIO()
            cropped.convert("RGB").save(
                buffer,
                format="JPEG",
                quality=_SHOT_JPEG_QUALITY,
            )
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "optional operation unavailable: _crop_region_data_uri (%s)", type(exc).__name__,
        )
        return None
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _student_paper_context(
    repositories: GradingRepositoryAccess,
    data: SessionAnalysisData,
    student: StudentReportData,
) -> dict[str, Any] | None:
    if student.result_id > 0:
        context = repositories.results.get_result_context(student.result_id)
        if (
            isinstance(context, dict)
            and int(context.get("session_id") or 0) == data.session_id
            and int(context.get("student_id") or 0) == student.student_id
        ):
            return context
        return None
    # Manual scores have no session_results row.  The current exam-paper
    # assignment is authoritative; do not guess among duplicate assignments.
    candidates = [
        row for row in repositories.papers.get_session_paper_identities(data.session_id)
        if int(row.get("student_id") or 0) == student.student_id
        and str(row.get("match_status") or "") == "matched"
    ]
    return candidates[0] if len(candidates) == 1 else None


def lost_question_shot_specs(
    repositories: GradingRepositoryAccess,
    data: SessionAnalysisData,
    student: StudentReportData,
    *,
    regions: list[dict[str, Any]] | None,
    data_root: Path | None,
    paper_context: dict[str, Any] | None = None,
    include_full: bool = False,
) -> list[dict[str, Any]]:
    """默认取失分题截图；报告阅读可取满分题，不扩大模型用图范围。"""
    if not regions:
        return []
    lost_records = [record for record in student.records if record.lost or include_full]
    if not lost_records:
        return []

    if paper_context is None:
        paper_context = _student_paper_context(repositories, data, student)

    # 区域粒度是整道大题：同一大题多小问丢分只嵌一次，按丢分从大到小排序。
    lost_by_parent: dict[str, float] = {}
    for record in lost_records:
        parent = parent_question_id(record.question_id)
        lost_by_parent[parent] = lost_by_parent.get(parent, 0.0) + record.lost_points
    ordered_parents = sorted(
        lost_by_parent,
        key=lambda parent: (-lost_by_parent[parent], parent),
    )

    shots: list[dict[str, Any]] = []
    for parent in ordered_parents:
        matching = _find_question_regions(regions, parent)
        for index, region in enumerate(matching):
            page = _region_page(region)
            raw_path = str((paper_context or {}).get(f"{page}_image") or "")
            if not raw_path:
                continue
            try:
                image_path = resolve_stored_file_path(raw_path, data_root=data_root)
            except ValueError:
                continue
            if data_root is None:
                continue
            try:
                image_path.resolve().relative_to(Path(data_root).resolve())
            except (ValueError, OSError):
                continue
            region_id = str(region.get("mapped_question_id") or region.get("detected_question_id") or parent)
            shots.append({
                "key": parent if index == 0 else f"{parent}:{index}",
                "image_path": image_path, "region": region, "page": page,
                "parent_question_id": parent,
                "region_question_id": region_id,
                "caption": f"{_question_display_label(region_id)}作答区截图（原卷截图）",
            })
    return shots



def capture_lost_question_shots(repositories, data, student, *, regions, data_root, paper_context=None, include_full=False):
    from backend.files.session_originals import originals_state
    if data_root is not None and originals_state(data_root, data.session_id) in {"clearing", "cleared"}:
        return {}
    shots = {}
    used_bytes = 0
    for spec in lost_question_shot_specs(repositories, data, student, regions=regions,
                                       data_root=data_root, paper_context=paper_context, include_full=include_full):
        data_uri = _crop_region_data_uri(spec["image_path"], spec["region"])
        if data_uri is None:
            continue
        encoded_size = len(data_uri) * 3 // 4
        if used_bytes + encoded_size > _SHOT_BUDGET_BYTES:
            continue
        used_bytes += encoded_size
        shots[spec["key"]] = {
            k: spec[k] for k in ("parent_question_id", "region_question_id", "caption", "page", "region")
        }
        shots[spec["key"]]["data_uri"] = data_uri
    return shots


def _personal_image_inputs(
    data: SessionAnalysisData,
    student: StudentReportData,
    shots: dict[str, dict[str, str]],
    paper_context: dict[str, Any] | None,
    data_root: Path | None,
) -> tuple[list[bytes], list[dict[str, Any]]]:
    """有序组织原卷、题图和作答概览；序号与同一次图文请求严格对应。"""
    images: list[bytes] = []
    image_map: list[dict[str, Any]] = []
    total_bytes = 0
    student.material_notes = []

    def append(blob: bytes, *, kind: str, label: str, question_ids: list[str]) -> None:
        nonlocal total_bytes
        for index, existing in enumerate(images):
            if blob == existing and image_map[index]["kind"] == kind:
                image_map[index]["question_ids"] = list(dict.fromkeys(
                    [*image_map[index]["question_ids"], *question_ids]
                ))
                return
        if len(images) >= 48 or total_bytes + len(blob) > _SHOT_BUDGET_BYTES:
            student.material_notes.append(f"{label}未纳入图片分析，不能推断图中细节。")
            return
        images.append(blob)
        total_bytes += len(blob)
        image_map.append({
            "image_number": len(images), "kind": kind,
            "label": label, "question_ids": question_ids,
        })

    lost = [record for record in student.records if record.lost]
    for key, shot in shots.items():
        parent = shot.get("parent_question_id") or key
        region_id = shot.get("region_question_id") or parent
        region_coordinates = question_id_coordinates(region_id)
        question_ids = [
            record.question_id for record in student.records
            if record.question_id == region_id or (
                region_coordinates is not None and (
                    question_id_coordinates(record.question_id) == region_coordinates
                    or (region_coordinates[1] is None and parent_question_id(record.question_id) == parent)
                )
            )
        ]
        append(
            base64.b64decode(shot["data_uri"].split(",", 1)[1]),
            kind="student_work", label=f"{_question_display_label(region_id)}学生作答原图",
            question_ids=question_ids,
        )
    info_by_qid = {info.question_id: info for info in data.questions}
    for record in lost:
        info = info_by_qid.get(record.question_id)
        if info is None:
            continue
        for role, blob in info.reference_images:
            append(
                blob, kind=f"reference_{role}",
                label=f"{_question_display_label(parent_question_id(record.question_id))}{'原题及题图' if role == 'question' else '参考答案解析'}",
                question_ids=[record.question_id],
            )
    # The overview supplies distribution context, not a record of working time.
    for page, label in (("front", "正面"), ("back", "反面")):
        raw_path = str((paper_context or {}).get(f"{page}_image") or "")
        if not raw_path:
            continue
        uri = _crop_region_data_uri(
            resolve_stored_file_path(raw_path, data_root=data_root), None,
            max_width=1200,
        )
        if uri is not None:
            append(
                base64.b64decode(uri.split(",", 1)[1]), kind="student_overview",
                label=f"学生答卷{label}概览，仅用于观察全卷作答分布",
                question_ids=[record.question_id for record in student.records],
            )
    if not any(item["kind"].startswith("student_") for item in image_map):
        student.material_notes.append("未取得该生可读取的答卷图片；缺少作答文字不代表空白，不能推断未作答原因。")
    return images, image_map


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
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "optional operation unavailable: load_session_regions (%s)", type(exc).__name__,
        )
        return []


# ---------------------------------------------------------------------------
# HTML 渲染（版面结构与视觉按已确认原型，自包含、截图 base64 内嵌）
# ---------------------------------------------------------------------------

# 学生与家长个人报告：成绩卡 → 历次成绩 → 答题一览 → 重点跟进 →
# 考查点 → 失分题详解附录。
_PERSONAL_CSS = """
:root{--ink:#1f2d3d;--muted:#6b7a8c;--line:#e3e8ef;--bg:#f4f6f9;--card:#fff;
--good:#2f855a;--good-soft:#e8f5ee;--warn:#b7791f;--warn-soft:#fdf3e1;
--bad:#c0392b;--bad-soft:#fbeaea;--gray:#8a97a8;--gray-soft:#eef1f5;--accent:#2b6cb0}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"PingFang SC","Microsoft YaHei UI","Microsoft YaHei",sans-serif;
color:var(--ink);background:var(--bg);line-height:1.7;font-size:15px;
font-variant-numeric:tabular-nums}
.page{max-width:760px;margin:0 auto;padding:18px 14px 40px}
.card{background:var(--card);border-radius:12px;box-shadow:0 1px 2px rgba(31,45,61,.06);
padding:20px 22px;margin-bottom:16px}
h2{font-size:17px;margin-bottom:12px}
h2 .aitag{font-size:11px;font-weight:400;color:var(--warn);background:var(--warn-soft);
border:1px solid #ecd9b0;border-radius:999px;padding:1px 9px;margin-left:8px;vertical-align:2px}
.note{font-size:12px;color:var(--muted)}
.meta{font-size:12.5px;color:var(--muted)}
.review-banner{background:var(--warn-soft);color:var(--warn);border-radius:8px;
padding:8px 12px;font-size:13px;margin-bottom:12px}
.name{font-size:24px;font-weight:700;margin-top:6px}
.score-line{display:flex;align-items:baseline;gap:4px;margin:8px 0 14px}
.score-line.has-heat{align-items:center;gap:16px}
.score-line .big{font-size:44px;font-weight:800;line-height:1}
.score-line .of{font-size:16px;color:var(--muted)}
.pheat{flex:1 1 auto;min-width:0;max-width:430px}
.pheat-strip{position:relative;display:flex;gap:2px;height:30px;margin:18px 0 16px}
.pheat-cell{position:relative;flex:1;border-radius:4px;display:flex;align-items:center;
justify-content:center;font-size:11px;background:var(--gray-soft);color:var(--gray)}
.pheat-cell.me{outline:2px solid var(--accent);outline-offset:-1px}
.pheat-me{position:absolute;bottom:calc(100% + 4px);left:50%;transform:translateX(-50%);
font-style:normal;font-size:11px;font-weight:700;color:var(--accent);white-space:nowrap}
.pheat-me::after{content:'';position:absolute;top:100%;left:50%;transform:translateX(-50%);
border:3px solid transparent;border-top-color:var(--accent)}
.pheat-avg{position:absolute;top:-4px;bottom:-4px;width:0;z-index:1}
.pheat-avg i{position:absolute;left:-1px;top:0;bottom:0;width:2px;background:var(--gray)}
.pheat-avglb{position:absolute;top:calc(100% + 2px);transform:translateX(-50%);
font-style:normal;font-size:10px;color:var(--gray);white-space:nowrap}
.pheat-ends{display:flex;justify-content:space-between;font-size:10px;color:var(--gray)}
.pheat-cap{font-size:11px;color:var(--gray);margin-top:3px}
.stat3{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:4px}
.stat3 .cell{display:flex;flex-direction:column;justify-content:center;min-height:66px;
border:1px solid var(--line);border-radius:10px;padding:8px 10px;text-align:center;min-width:0}
.stat3 .l{font-size:11.5px;color:var(--muted);white-space:nowrap}
.stat3 .v{font-size:22px;font-weight:800;white-space:nowrap;line-height:1.3}
.stat3 .v.up{color:var(--good)} .stat3 .v.down{color:var(--bad)}
.stat3 .v.muted{font-size:15px;font-weight:600;color:var(--gray)}
.stat3 .sub{font-size:11px;color:var(--muted);white-space:nowrap;margin-top:2px}
/* C 答题一览 */
.qgrid{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}
@media(min-width:700px){.qgrid{grid-template-columns:repeat(10,1fr)}}
.qcell{border:1px solid var(--line);border-radius:8px;background:#fff;padding:6px 2px 4px;
text-align:center;cursor:pointer;position:relative;font-family:inherit}
.qcell .qn{display:block;font-size:13px;font-weight:700}
.qcell .qs{display:block;font-size:11px;color:var(--muted)}
.qcell .sym{position:absolute;left:4px;top:3px;font-size:11px;line-height:1}
.qcell .hard{position:absolute;right:3px;top:2px;font-size:9px;color:var(--warn);line-height:1}
.qcell.full{border-color:#bfe3d2;background:var(--good-soft)} .qcell.full .sym{color:var(--good)}
.qcell.part{border-color:#f0d9ab;background:var(--warn-soft)} .qcell.part .sym{color:var(--warn)}
.qcell.zero{border-color:#eec6c0;background:var(--bad-soft)} .qcell.zero .sym{color:var(--bad)}
.qcell.blank{border-style:dashed;background:var(--gray-soft)} .qcell.blank .sym{color:var(--gray)}
.qcell[aria-expanded="true"]{outline:2px solid var(--accent);outline-offset:1px}
.plegend{margin-top:10px;font-size:12px;color:var(--muted)}
.psw{display:inline-flex;align-items:center;gap:6px;color:var(--ink)}
.psw i{width:12px;height:12px;border-radius:4px;flex:none}
.psw.g i{background:var(--good)} .psw.a i{background:var(--warn)}
.psw.r i{background:var(--bad)} .psw.gr i{background:var(--gray-soft);border:1px dashed #aeb8c4}
.plegend-hard{color:var(--muted);margin-top:4px}
.pstrength{display:flex;align-items:baseline;gap:7px;margin-top:8px;
font-size:14px;color:var(--ink);line-height:1.6}
.pstrength-tag{flex:none;font-size:11px;font-weight:600;color:var(--good);
background:var(--good-soft);border:1px solid #b7d9c7;border-radius:4px;padding:0 5px}
.phint{color:var(--muted);margin-top:6px;font-size:12px}
/* 共享题目详情抽屉：手机底部弹出，桌面右侧滑出 */
html.psheet-lock,html.psheet-lock body{overflow:hidden}
.psheet-backdrop{position:fixed;inset:0;background:rgba(20,28,36,.45);z-index:900}
.psheet{position:fixed;z-index:901;background:#fff;display:flex;flex-direction:column}
.psheet[hidden],.psheet-backdrop[hidden]{display:none!important}
@media screen and (max-width:560px){
.psheet{left:0;right:0;bottom:0;height:88vh;border-radius:14px 14px 0 0;
box-shadow:0 -6px 28px rgba(0,0,0,.28)}
.psheet-head{border-radius:14px 14px 0 0}}
@media screen and (min-width:561px){
.psheet{top:0;right:0;bottom:0;width:520px;box-shadow:-6px 0 28px rgba(0,0,0,.2)}}
.psheet-head{display:flex;align-items:center;flex-wrap:wrap;gap:8px;flex:none;
padding:10px 14px;border-bottom:1px solid var(--line);background:#fff}
.psheet-title{flex:1 1 auto;font-size:16px;font-weight:700;min-width:0}
.psheet-pos{font-size:14px;color:var(--muted);white-space:nowrap}
.psheet-nav,.psheet-close{font:inherit;font-size:14px;min-height:36px;padding:4px 12px;
border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--ink);cursor:pointer}
.psheet-nav[disabled]{opacity:.35;cursor:default}
.psheet-close{color:var(--accent)}
.psheet-body{flex:1 1 auto;overflow-y:auto;padding:14px 16px;min-height:0;
-webkit-overflow-scrolling:touch}
.psheet .qdetail-box{border:0;background:transparent;padding:0}
.psheet .qmore-btn{display:none}
.qdetail-box{border:1px solid var(--line);border-radius:10px;padding:14px 16px;background:#fcfdfe}
.qdetail-box .qd-head{font-size:14px;font-weight:700}
.qdetail-box .pt-tag{display:inline-block;background:var(--gray-soft);border-radius:6px;
padding:0 8px;margin:2px 4px 2px 0;font-size:12px;color:var(--ink)}
.qd-fb{font-size:13.5px;margin-top:8px}
.errline{font-size:12.5px;color:var(--warn);margin-top:5px}
.qmore-btn{margin-top:10px;border:1px solid var(--accent);color:var(--accent);background:#fff;
border-radius:8px;padding:5px 14px;min-height:44px;font-size:14px;cursor:pointer;font-family:inherit}
.qmore{margin-top:10px;border-top:1px dashed var(--line);padding-top:10px}
/* 共用题干/截图/分析 */
.stem{white-space:normal;overflow-wrap:anywhere;font-size:13.5px;color:var(--ink)}
.stem table{border-collapse:collapse;width:100%;margin:8px 0}
.stem td,.stem th{border:1px solid #cbd5e1;padding:5px 8px;text-align:center}
.reference-figures{display:flex;flex-wrap:wrap;gap:12px;margin:10px 0}
.reference-figures img{max-width:100%;max-height:180px;object-fit:contain}
.shot{border:1px solid var(--line);border-radius:8px;padding:8px;margin:10px 0 2px;background:#fcfcfd}
.shot img{max-width:100%;display:block;margin:0 auto;border-radius:4px}
.shot .cap{font-size:11.5px;color:var(--muted);margin-top:6px;text-align:center}
.question-stem>summary{cursor:pointer;font-size:13px;color:var(--accent)}
.shot-reference{font-size:12.5px;margin-top:8px}
.shot-reference a{color:var(--accent)}
.shot-reference button{border:0;padding:0;background:none;color:var(--accent);font:inherit;cursor:pointer;text-decoration:underline}
.shot-preview{margin-top:12px}
.shot-preview-title{font-size:12.5px;color:var(--muted);margin-bottom:6px}
.shot-enlarge{display:block;max-width:100%;margin:auto;border:0;padding:0;background:none;cursor:zoom-in}
.shot-enlarge:focus-visible{outline:2px solid var(--accent);outline-offset:4px}
.report-print-only{display:none}
.shot-viewer{max-width:96vw;max-height:96vh;padding:12px;border:1px solid var(--line);border-radius:8px}
.shot-viewer::backdrop{background:rgba(0,0,0,.65)}
.shot-viewer-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:8px;font-size:14px}
.shot-viewer-head button{font:inherit;padding:6px 12px;min-height:44px;border:1px solid var(--line);border-radius:6px;background:#fff;cursor:pointer}
.shot-viewer-scroll{max-width:calc(96vw - 28px);max-height:calc(96vh - 70px);overflow:auto}
.shot-viewer-scroll img{display:block;max-width:none;width:auto;height:auto}
.objective-answers{margin:8px 0}
.question-stem{margin-top:8px}
.question-stem .stem{margin-top:6px}
.kv{display:grid;grid-template-columns:88px 1fr;gap:4px 10px;font-size:13px}
.kv dt{color:var(--muted)} .kv dd{min-width:0;white-space:pre-wrap;overflow-wrap:anywhere}
.kv dd b.ans{color:var(--good)}
.compact-analysis{font-size:13.5px;line-height:1.75}
.compact-analysis p{margin:3px 0}
.compact-analysis b{color:#334155}
.feedback,.solution,.revision{margin:10px 0}
.solution{border-left:3px solid #b8cde9;padding:2px 12px}
.solution ol{margin:4px 0;padding-left:22px}
.review-note{background:#fff7e8;border-left:3px solid #e9972d;padding:8px 12px;margin:10px 0}
.review-note b{color:#a45b00}
.compact-analysis details{margin:8px 0;color:#475569}
.compact-analysis summary{cursor:pointer;color:var(--accent)}
.aidraft{margin-top:10px;background:var(--warn-soft);border:1px dashed #f5d08c;border-radius:8px;
padding:10px 12px;font-size:13px}
.aidraft .cap{font-size:11.5px;color:var(--warn);font-weight:700;margin-bottom:4px}
.aidraft p+p{margin-top:6px}
.qm{display:inline-block;max-width:100%;vertical-align:baseline;padding:3px 2px 6px}
.qm-display{display:block;text-align:center;margin:8px 0}
.qm-error{color:#9a3412}
.katex{font-size:1.04em}
.stem,.kv dd,.compact-analysis p{overflow-x:auto;overflow-y:hidden;overflow-wrap:anywhere}
/* D 问题卡片 */
.dcard{border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:12px}
.dcard .dhead{display:flex;align-items:flex-start;gap:8px;flex-wrap:wrap}
.dcard .no{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;
border-radius:50%;background:var(--accent);color:#fff;font-size:12px;font-weight:700;flex:none;margin-top:2px}
.dcard .ttl{flex:1 1 calc(100% - 40px);font-size:14.5px;font-weight:700;min-width:0;overflow-wrap:anywhere}
.dcard .help{font-size:13.5px;margin-top:7px}
.dhead-r{margin-left:auto;display:flex;align-items:center;gap:8px;flex:none}
.dcard .when{font-size:11.5px;font-weight:400;color:var(--accent);white-space:nowrap}
.plost{font-size:12px;font-weight:700;color:var(--bad);background:var(--bad-soft);
border-radius:999px;padding:2px 8px;white-space:nowrap}
.pmissed{display:inline-block;font-size:11px;color:var(--bad);font-weight:400;
border:1px solid var(--bad);border-radius:999px;padding:0 7px;margin-left:8px;
vertical-align:1px;white-space:nowrap}
.dcard .pfind{font-size:14px;color:var(--muted);line-height:1.6;margin:6px 0 10px}
.ptask{background:#eef4fb;border-radius:10px;padding:10px 12px;margin:2px 0 8px}
.ptask-do{display:flex;gap:8px;align-items:flex-start;font-size:15px;font-weight:500;line-height:1.55}
.ptask-sq{flex:none;width:13px;height:13px;border:1.6px solid var(--accent);border-radius:3px;margin-top:6px}
.ptask-done{display:flex;gap:8px;align-items:flex-start;font-size:14px;line-height:1.55;margin-top:6px}
.ptask-ok{flex:none;font-style:normal;margin-top:1px}
.ptask-ok::before{content:'✓';color:var(--good);font-weight:700}
.dcard .pparent{font-size:13.5px;color:var(--muted);line-height:1.6;margin:2px 0 8px}
.dcard .pparent b{color:inherit;font-weight:600}
.prel{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.prel .qtag{font:inherit;font-size:12.5px;min-height:32px;border-radius:999px;
padding:4px 12px;border:0;background:var(--gray-soft);color:var(--ink);cursor:pointer}
.prel .qtag:hover,.prel .qtag:focus-visible{outline:1px solid var(--accent)}
.prel .qtag.qtag-full{background:var(--good-soft);color:var(--good)}
.prel .qtag.qtag-part{background:var(--warn-soft);color:var(--warn)}
.prel .qtag.qtag-zero{background:var(--bad-soft);color:var(--bad)}
.prel .qtag.qtag-blank{background:var(--gray-soft);color:var(--gray);border:1px dashed #aeb8c4}
.prel .qtag .qtag-s{margin-left:5px;font-weight:600;opacity:.85}
.dgreen{background:var(--good-soft);border:1px solid #bfe3d2;border-radius:10px;
padding:14px 16px;color:var(--good);font-size:14px}
/* F 附录 */
details.appendix summary{cursor:pointer;font-size:13px;color:var(--accent)}
.qcard{border:1px solid var(--line);border-left:4px solid var(--bad);border-radius:10px;
padding:14px 16px;margin:12px 0}
.qcard .head{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;
gap:6px;margin-bottom:8px}
.qcard .head b{font-size:14.5px}
.qcard .score{font-size:13px;color:var(--bad);font-weight:700}
.qpart+.qpart{border-top:1px solid var(--line);margin-top:14px;padding-top:14px}
.qpart h3{display:flex;justify-content:space-between;gap:12px;font-size:13px;margin-bottom:8px}
.qpart h3 span{color:var(--muted);font-weight:400}
/* footer */
footer{font-size:12px;color:var(--muted);padding:6px 4px;line-height:1.8}
@media screen and (max-width:560px){
 body{font-size:16px}
 .page{padding:10px 8px 28px}
 .card{padding:16px 14px}
 .dcard .help,.dcard .pfind{font-size:16px;line-height:1.8}
 .dcard .ttl{font-size:16px;line-height:1.6}
 .ptask-do,.ptask-done{font-size:16px}
 .qgrid{grid-template-columns:repeat(6,1fr)}
 .kv{grid-template-columns:70px minmax(0,1fr)}
}
@page{size:A4;margin:13mm 14mm}
@media print{
 body{background:#fff}
 .page{max-width:none;padding:0}
 .card{box-shadow:none;border:1px solid var(--line);border-radius:0;padding:16px 18px;margin-bottom:12px}
 details:not([open])>*:not(summary){display:block}
 details::details-content{content-visibility:visible}
 #qpanel,.psheet,.psheet-backdrop,.phint{display:none!important}
 .report-print-only{display:block}
 .shot-viewer{display:none!important}
 .qcell{outline:none!important}
 .shot img{max-height:95mm;width:auto;object-fit:contain}
 .shot-reference{display:none}
 .qcard.objective{padding:8px 12px;margin:8px 0}
 .qcard.objective .head{margin-bottom:4px}
 .qcard.objective .qpart+.qpart{margin-top:8px;padding-top:8px}
 .qcard.objective .shot{padding:5px;margin-top:6px}
 .question-stem>summary{display:none}
 .pheat,.pheat-cell,.pheat-avg i,.psw i{-webkit-print-color-adjust:exact;print-color-adjust:exact}
 .qpart,.shot,.dcard .help,.dcard .qd-fb,.qgrid,.pvchap,footer{break-inside:avoid}
 .dcard{break-inside:avoid}
 .card h2,.dcard .dhead,.qcard .head,.qpart h3{break-after:avoid}
 p,dd,li{orphans:3;widows:3}
}
"""

# 题号方格 / 问题卡题号 / 肖像题号 → 共享题目详情抽屉；作答截图大图盖在抽屉上。
_PERSONAL_GRID_JS = """
(function(){
  function renderMath(root){
    root.querySelectorAll('.qm[data-latex]').forEach(function(el){
      try{ katex.render(el.dataset.latex, el, {throwOnError:true, output:'htmlAndMathml', displayMode:el.dataset.display === 'true'}); }
      catch(e){ el.classList.add('qm-error'); el.title='公式格式需核对，已保留原文'; }
    });
  }
  function hydrateShots(root){
    if(!root) return;
    root.querySelectorAll('[data-shot-group]').forEach(function(slot){
      if(slot.dataset.loaded) return;
      var source = document.getElementById(slot.dataset.shotGroup);
      if(!source) return;
      Array.from(source.childNodes).forEach(function(node){slot.appendChild(node.cloneNode(true));});
      slot.dataset.loaded = 'true';
    });
  }
  var viewer = document.getElementById('report-shot-viewer');
  document.addEventListener('click', function(e){
    var shot = e.target.closest('.shot-enlarge');
    if(shot && viewer){
      var img = shot.querySelector('img');
      var enlarged = document.createElement('img');
      enlarged.src=img.src; enlarged.alt=img.alt;
      viewer.querySelector('.shot-viewer-scroll').replaceChildren(enlarged);
      viewer.showModal();
    }
    if(e.target.closest('[data-close-shot]') && viewer) viewer.close();
  });
  if(viewer) viewer.addEventListener('click',function(e){if(e.target===viewer) viewer.close();});
  var panel = document.getElementById('qpanel');
  if (!panel) return;
  var $ = function(s, r){ return (r || document).querySelector(s); };
  var $$ = function(s, r){ return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  function el(tag, cls, text){
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  var backdrop = el('div', 'psheet-backdrop'); backdrop.hidden = true;
  var sheet = el('div', 'psheet'); sheet.hidden = true;
  sheet.setAttribute('role', 'dialog');
  var head = el('div', 'psheet-head');
  var title = el('div', 'psheet-title');
  var pos = el('div', 'psheet-pos');
  var prev = el('button', 'psheet-nav', '‹ 上一题'); prev.type = 'button';
  var next = el('button', 'psheet-nav', '下一题 ›'); next.type = 'button';
  var close = el('button', 'psheet-close', '关闭'); close.type = 'button';
  head.append(title, pos, prev, next, close);
  var body = el('div', 'psheet-body');
  body.appendChild(panel);
  sheet.append(head, body);
  document.body.append(backdrop, sheet);
  var ctx = [], idx = 0, open = false, savedY = 0;
  var cells = function(){ return $$('.qcell'); };
  function cellFor(q){
    return cells().filter(function(x){ return x.dataset.q === q; })[0]
      || cells().filter(function(x){ return q && x.dataset.q.indexOf(q + '(') === 0; })[0];
  }
  function renderCur(){
    var q = ctx[idx];
    var btn = cellFor(q);
    var qid = btn ? btn.dataset.q : q;
    var tpl = $$('template[data-q]').filter(function(t){ return t.dataset.q === qid; })[0]
      || $$('template[data-q]').filter(function(t){ return t.dataset.q === q; })[0];
    panel.innerHTML = '';
    if (tpl) panel.appendChild(tpl.content.cloneNode(true));
    panel.hidden = false;
    var more = panel.querySelector('.qmore');
    if (more) {
      more.hidden = false;
      more.querySelectorAll('details').forEach(function(d){ d.open = true; });
    }
    renderMath(panel);
    hydrateShots(panel);
    cells().forEach(function(b){
      b.setAttribute('aria-expanded', b === btn ? 'true' : 'false');
    });
    var qn = btn && btn.querySelector('.qn') ? btn.querySelector('.qn').textContent.trim() : q;
    var qs = btn && btn.querySelector('.qs') ? btn.querySelector('.qs').textContent.trim() : '';
    title.textContent = '第' + qn + '题' + (qs ? ' · ' + qs : '');
    pos.textContent = (idx + 1) + ' / ' + ctx.length;
    prev.disabled = idx <= 0;
    next.disabled = idx >= ctx.length - 1;
    body.scrollTop = 0;
  }
  function openSheet(q, ids){
    ctx = ids && ids.length ? ids.slice() : [q];
    var i = ctx.indexOf(q);
    idx = i < 0 ? 0 : i;
    if (!open) {
      open = true;
      savedY = window.scrollY;
      backdrop.hidden = false;
      sheet.hidden = false;
      document.documentElement.classList.add('psheet-lock');
    }
    renderCur();
  }
  function closeSheet(){
    if (!open) return;
    open = false;
    backdrop.hidden = true;
    sheet.hidden = true;
    document.documentElement.classList.remove('psheet-lock');
    cells().forEach(function(b){ b.setAttribute('aria-expanded', 'false'); });
    window.scrollTo(0, savedY);
  }
  document.addEventListener('click', function(e){
    if (e.target.closest && e.target.closest('#report-shot-viewer')) return;
    var cell = e.target.closest && e.target.closest('.qcell');
    var openQ = e.target.closest && e.target.closest('[data-open-q]');
    if (!cell && !openQ) return;
    e.preventDefault();
    e.stopPropagation();
    if (cell) {
      openSheet(cell.dataset.q, cells().map(function(b){ return b.dataset.q; }));
    } else {
      var q = openQ.dataset.openQ;
      var ids = openQ.dataset.openCtx
        ? openQ.dataset.openCtx.split(',').filter(Boolean) : null;
      if (!ids || !ids.length) {
        var wrap = openQ.parentElement;
        ids = wrap ? $$('[data-open-q]', wrap).map(function(b){ return b.dataset.openQ; }) : [];
      }
      if (!ids.length || ids.indexOf(q) < 0) ids = [q];
      openSheet(q, ids);
    }
  }, true);
  prev.addEventListener('click', function(){ if (idx > 0) { idx--; renderCur(); } });
  next.addEventListener('click', function(){ if (idx < ctx.length - 1) { idx++; renderCur(); } });
  close.addEventListener('click', closeSheet);
  backdrop.addEventListener('click', closeSheet);
  document.addEventListener('keydown', function(e){
    if (!open) return;
    if (document.querySelector('#report-shot-viewer[open]')) return;
    if (e.key === 'Escape') { closeSheet(); e.preventDefault(); return; }
    if (e.key === 'ArrowLeft' && idx > 0) { idx--; renderCur(); e.preventDefault(); }
    if (e.key === 'ArrowRight' && idx < ctx.length - 1) { idx++; renderCur(); e.preventDefault(); }
  });
})();
"""

_PERSONAL_PRINT_JS = """
(function(){
  var states = null;
  function expand(){
    if(states !== null) return;
    states = Array.from(document.querySelectorAll('details')).map(function(el){
      var state = [el, el.open]; el.open = true; return state;
    });
  }
  function restore(){
    if(states === null) return;
    states.forEach(function(state){state[0].open = state[1];});
    states = null;
  }
  window.addEventListener('beforeprint', expand);
  window.addEventListener('afterprint', restore);
  window.matchMedia('print').addEventListener('change', function(e){e.matches ? expand() : restore();});
})();
"""

_PERSONAL_KATEX_JS = """
document.querySelectorAll('.qm[data-latex]').forEach(el => {
  try { katex.render(el.dataset.latex, el, {throwOnError:true, output:'htmlAndMathml', displayMode:el.dataset.display === 'true'}); }
  catch (_) { el.classList.add('qm-error'); el.title = '公式格式需核对，已保留原文'; }
});
// 收起的附录不会触发浏览器加载所需字体；打印展开时再加载会丢失变量字母。
if(document.fonts){
  document.fonts.forEach(function(font){
    if(font.family.indexOf('KaTeX_') === 0) font.load().catch(function(){});
  });
}
"""


def _narrative_items(narrative: dict[str, Any] | None, key: str) -> list[Any]:
    if not isinstance(narrative, dict):
        return []
    value = narrative.get(key)
    return value if isinstance(value, list) else []


def _narrative_text(value: object) -> str:
    return str(value or "").strip()


def _collect_review_notes(
    narrative: dict[str, Any] | None,
    student: StudentReportData,
    info_by_qid: dict[str, QuestionInfo],
    lock_revisions: dict[tuple[int, str], int],
) -> list[dict[str, Any]]:
    """从一条个人叙述中收集"建议核对"条目，附带生成时的教师复核锁修订号。"""
    items: list[dict[str, Any]] = []
    for item in _narrative_items(narrative, "question_analyses"):
        if not isinstance(item, dict):
            continue
        note = _narrative_text(item.get("review_note"))
        if not note:
            continue
        raw = _narrative_text(item.get("question_id"))
        question_id = resolve_known_question_id(raw, info_by_qid) or raw
        if not question_id:
            continue
        items.append(
            {
                "student_id": int(student.student_id),
                "student_code": student.student_code,
                "student_name": student.student_name,
                "class_name": student.class_name,
                "question_id": question_id,
                "display_label": _question_display_label(question_id),
                "note": note,
                "lock_revision": lock_revisions.get(
                    (int(student.student_id), question_id), 0
                ),
            }
        )
    return items


def _knowledge_rows(
    backfill: dict[str, list[dict[str, str]]],
    records: list[StudentQuestionRecord],
) -> list[dict[str, Any]]:
    """按题库知识点标签聚合得分率；无标签数据时返回空，渲染层省略该板块。"""
    buckets: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.max_score <= 0:
            continue
        entries = backfill.get(record.question_id)
        if entries is None:
            entries = backfill.get(parent_question_id(record.question_id))
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


def _personal_knowledge_view(data: SessionAnalysisData, student: StudentReportData) -> dict[str, Any]:
    from question_bank.question_types import is_type_key

    snapshot = data.knowledge_structure
    target_kind = snapshot.get('target_kind') or 'skill'
    type_mode = target_kind == 'type'
    catalog = {str(item['knowledge_key']): item for item in snapshot.get('catalog', [])}
    nodes: dict[str, dict[str, Any]] = {}
    for record in student.records:
        entries = data.knowledge_backfill.get(record.question_id)
        if entries is None:
            entries = data.knowledge_backfill.get(parent_question_id(record.question_id), [])
        for entry in entries:
            key = str(entry.get('stable_key') or entry.get('path') or '')
            if not key or record.max_score <= 0:
                continue
            item = catalog.get(key, {})
            path = str(item.get('knowledge_point') or entry.get('path') or entry.get('label') or '')
            segments = [part.strip() for part in re.split('[|｜]', path) if part.strip()]
            label = str(entry.get('label') or (segments[-1] if segments else key))
            entry_target_kind = str(entry.get('target_kind') or item.get('target_kind') or target_kind)
            kind = item.get('node_kind') or (
                'type' if is_type_key(key)
                else 'skill' if key.startswith('sk_') or label.startswith('技能') else 'topic')
            if kind not in {'topic', 'skill', 'type'}:
                kind = 'topic'
            if entry_target_kind == 'type' and kind != 'type':
                continue
            if entry_target_kind == 'knowledge' and kind != 'topic':
                continue
            chapter, section, cursor, seen = '', '', item, set()
            parent_references = []
            while cursor and str(cursor.get('knowledge_key')) not in seen:
                seen.add(str(cursor.get('knowledge_key')))
                parent_key = str(cursor.get('knowledge_key'))
                parent_mastery = student.knowledge_mastery.get(parent_key, {})
                if parent_key != key and parent_mastery.get('mastery') is not None:
                    parent_references.append({'label': str(cursor.get('knowledge_point') or parent_key).split('｜')[-1].split('|')[-1],
                                              **{field: parent_mastery.get(field) for field in ('mastery', 'tier', 'interval_low', 'interval_high')}})
                if cursor.get('node_kind') == 'chapter':
                    chapter = str(cursor['knowledge_point']).split('｜')[-1].split('|')[-1]
                elif cursor.get('node_kind') == 'section':
                    section = str(cursor['knowledge_point']).split('｜')[-1].split('|')[-1]
                cursor = catalog.get(str(cursor.get('parent_knowledge_key') or ''), {})
            chapter = chapter or ('｜'.join(segments[:-2]) if len(segments) >= 3 else '本卷题型' if type_mode else '本卷知识与技能')
            section = section or (segments[-2] if len(segments) >= 3 else '')
            mastery = student.knowledge_mastery.get(key, {})
            value = mastery.get('mastery')
            if value is not None and (not isinstance(value, (float, int)) or not math.isfinite(value)):
                value = None
            node = nodes.setdefault(key, {
                'key': key, 'label': re.sub(r'^(?:技能|题型)[·・：:]\s*', '', label), 'kind': kind,
                'chapter': chapter, 'section': section, 'mastery': value,
                'target_kind': entry_target_kind,
                **{field: mastery.get(field) for field in ('interval_low', 'interval_high', 'observation_count', 'full_correct_count', 'recent_trend', 'parameter_version')},
                'tier': mastery.get('tier') or 'insufficient',
                'parent_references': parent_references,
                'evidence_count': int(mastery.get('evidence_count') or 0),
                'questions': [], 'score': 0.0, 'full': 0.0,
                'step_questions': sorted({str(ref.get('question_id')) for ref in mastery.get('source_question_refs', [])
                                          if int(ref.get('session_id') or 0) == data.session_id
                                          and (ref.get('assessment') or {}).get('granularity') == 'step'}),
            })
            if record.question_id not in {question['id'] for question in node['questions']}:
                node['score'] += min(record.score, record.max_score)
                node['full'] += record.max_score
                node['questions'].append({'id': record.question_id, 'label': _question_display_label(record.question_id),
                                          'score': record.score, 'full': record.max_score, 'lost': record.lost})
    edges = [dict(edge) for edge in snapshot.get('associations', [])
             if edge.get('topic_key') in nodes and edge.get('skill_key') in nodes]
    return {'nodes': list(nodes.values()), 'edges': edges, 'as_of': snapshot.get('as_of') or datetime.now().strftime('%Y-%m-%d %H:%M'),
            'target_kind': target_kind,
            'note': snapshot.get('note') or '当前掌握度暂不可用；以下保留本卷考查范围与得分，不以得分率代替掌握度。'}


def _knowledge_view_html(view: dict[str, Any]) -> str:
    if not view['nodes']:
        return ''
    chapters: dict[str, list[dict[str, Any]]] = {}
    for node in view['nodes']:
        chapters.setdefault(node['chapter'], []).append(node)
    boards = []
    counts = {'good': 0, 'mid': 0, 'low': 0, 'missing': 0}
    bands = {'weak': 'low', 'unsteady': 'mid', 'stable': 'good', 'insufficient': 'missing'}

    def node_html(node):
        value = node['mastery']
        band = bands.get(node.get('tier'), 'missing')
        counts[band] += 1
        percent = '证据不足' if value is None else f'{fmt_num(math.floor(value * 1000) / 10)}%'
        status = {'missing': '证据不足', 'low': '明显薄弱', 'mid': '还不稳', 'good': '较稳定'}[band]
        detail = f'本卷 {fmt_num(node["score"])} / {fmt_num(node["full"])} 分'
        value_label = f'当前掌握度：{status}'
        return (f'<button type="button" class="kn-node kn-{band}" data-key="{esc(node["key"])}" aria-pressed="false">'
                f'<span class="kn-node-top"><span class="kn-name">{esc(node["label"])}</span>'
                f'<strong class="kn-value" title="{value_label}">{percent}</strong></span>'
                f'<span class="kn-node-meta">{esc(node["section"])}<span>{detail}</span></span>'
                f'</button>')

    type_mode = view.get('target_kind') == 'type'
    for chapter, nodes in chapters.items():
        columns = []
        chapter_kinds = {node.get('target_kind') or view.get('target_kind') or 'skill' for node in nodes}
        chapter_type_mode = chapter_kinds == {'type'} or all(node['kind'] == 'type' for node in nodes)
        chapter_knowledge_mode = chapter_kinds == {'knowledge'}
        if chapter_type_mode:
            groups = [('题型 · 考什么', nodes, 'types')]
        elif chapter_knowledge_mode:
            groups = [('知识点 · 学什么', nodes, 'topics')]
        else:
            topics, skills = [n for n in nodes if n['kind'] == 'topic'], [n for n in nodes if n['kind'] == 'skill']
            groups = [('知识点', topics, 'topics'), ('技能点', skills, 'skills')]
            types = [n for n in nodes if n['kind'] == 'type']
            if types:
                groups.append(('题型 · 考什么', types, 'types'))
        for title, group, kind in groups:
            columns.append(f'<div class="kn-column kn-{kind}"><h4>{title}<span>{len(group)} 项</span></h4>'
                           + (''.join(node_html(node) for node in group) or '<p class="kn-empty">本卷暂无直接考查记录</p>') + '</div>')
        boards.append(f'<div class="kn-chapter"><h3>{esc(chapter)}</h3>'
                      f'<div class="kn-board{" is-types" if chapter_type_mode or chapter_knowledge_mode else ""}">'
                      '<svg class="kn-lines" aria-hidden="true"></svg>' + ''.join(columns) + '</div></div>')
    data_json = json.dumps(view, ensure_ascii=False, separators=(',', ':')).replace('<', r'\u003c').replace('>', r'\u003e').replace('&', r'\u0026')
    item_label = {'type': '题型', 'knowledge': '知识点', 'mixed': '训练目标'}.get(view.get('target_kind'), '知识与技能')
    title = f'{item_label}掌握图'
    footnote = ('节点百分比为参考难度步骤做对的可能性，详情附 80% 把握区间；“本卷”分数为相关小问的整体得分，不能直接归因到其中每一步。')
    help_text = ('点选训练目标，查看本卷表现。' if view.get('target_kind') in {'type', 'knowledge', 'mixed'} else
                 '点选知识点或技能点，查看关联与本卷表现。实线：有同小问依据；虚线：仅同题出现。关联不表示掌握度相同。')
    return (f'<section class="knowledge-map" id="knowledge-map"><div class="kn-heading"><h2>{title}</h2>'
            f'<span class="kn-date">截至 {esc(view["as_of"])}</span></div>'
            f'<p class="kn-intro">{esc(view["note"])}</p>'
            + '<p class="kn-thresholds">较稳定 / 还不稳 / 明显薄弱 / 证据不足，档位综合估计值与把握程度</p>'
            + '<div class="kn-legend">'
            + ''.join(f'<span><i class="kn-dot kn-{band}"></i>{label} <b>{counts[band]} 项</b></span>'
                      for band, label in [('low', '明显薄弱'), ('mid', '还不稳'), ('good', '较稳定'), ('missing', '证据不足')])
            + f'</div><p class="kn-help">{help_text}</p>'
            + ''.join(boards)
            + '<div class="kn-inspector" aria-live="polite"><p>点选上方节点，查看相关题目与证据。</p></div>'
            f'<p class="kn-footnote">{footnote}未达满分的教师复核步骤按未达成计入证据。</p>'
            f'<script type="application/json" class="kn-data">{data_json}</script></section>')


def _ai_block(text: str, *, failed: bool) -> str:
    note = AI_FAILED_NOTE if failed else text
    return (
        '<div class="aidraft">'
        f'<div class="cap">{esc(AI_DISCLAIMER)}</div>{_report_paragraphs(note)}</div>'
    )


def _report_paragraphs(value: object) -> str:
    text = _report_display_text(value)
    # Split prose after recognizing TeX so multiline display math stays whole.
    return ''.join(f'<p>{line}</p>' for line in _report_inline_math(text.strip()).split('<br>') if line.strip())


def _report_inline_math(text: str) -> str:
    """Keep authored TeX intact; convert only explicit linear math notation."""
    explicit = re.compile(r'(?<!\\)(\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$(?:\\.|[^$])+?\$)')
    math_chars = r'A-Za-z0-9∠△°√∛±²³=＝＋+−－÷×·*/:：.()（）^_≤≥≠⊥∥?\-'
    tokens = re.compile(r'[A-Za-z0-9∠△°√∛±(（][' + math_chars + r']*(?:[ \t]+[' + math_chars + r']+)*')

    def plain(value):
        return esc(value).replace('\n', '<br>')

    def linear(match):
        value = match.group(0)
        # A Chinese explanatory parenthesis immediately after an expression
        # belongs to the prose (e.g. √18（字迹模糊）), not its radicand.
        suffix = '（' if value.endswith('（') else ''
        if suffix:
            value = value[:-1]
        if not re.search(r'[√∛∠△°=＝÷×⊥∥²³≤≥≠±]', value):
            return plain(value + suffix)
        tex = _report_linear_tex(value)
        return (report_math_span(tex, value) if tex else plain(value)) + plain(suffix)

    result, end = [], 0
    for match in explicit.finditer(text):
        # Escape prose before substituting formulas, never regex over HTML.
        preceding, cursor = text[end:match.start()], 0
        for token in tokens.finditer(preceding):
            result.extend((plain(preceding[cursor:token.start()]), linear(token)))
            cursor = token.end()
        result.append(plain(preceding[cursor:]))
        authored = match.group(0)
        width = 1 if authored.startswith('$') and not authored.startswith('$$') else 2
        result.append(report_math_span(authored[width:-width], authored,
                                       display=authored.startswith(('$$', r'\['))))
        end = match.end()
    remaining, cursor = text[end:], 0
    for token in tokens.finditer(remaining):
        result.extend((plain(remaining[cursor:token.start()]), linear(token)))
        cursor = token.end()
    result.append(plain(remaining[cursor:]))
    return ''.join(result)


def _report_linear_tex(value: str) -> str | None:
    """Parse grouping/root boundaries without calculating or simplifying answers.

    Parenthesized denominators are explicit; ambiguous a/bc remains a/bc.
    A bare digit before √ is a coefficient. Root indices come from native
    Word math, authored TeX, or the unambiguous ∛ character.
    """
    source = value.translate(str.maketrans({'＝':'=', '＋':'+', '－':'-', '−':'-', '（':'(', '）':')', '：':':'}))
    symbols = {'∠':r'\angle ', '△':r'\triangle ', '°':r'^{\circ}',
               '÷':r'\div ', '×':r'\times ', '·':r'\cdot ', '⊥':r'\perp ', '∥':r'\parallel ',
               '²':'^{2}', '³':'^{3}', '±':r'\pm ', '≤':r'\leq ', '≥':r'\geq ', '≠':r'\neq '}

    def atom(pos, depth):
        if depth > 24 or pos >= len(source):
            raise ValueError('incomplete formula')
        start, char = pos, source[pos]
        if char == '(':
            inner, pos = expression(pos + 1, depth + 1, True)
            if pos >= len(source) or source[pos] != ')':
                raise ValueError('unbalanced formula')
            return '(' + inner + ')', inner, pos + 1
        if char in '√∛':
            wrapped, bare, pos = atom(pos + 1, depth + 1)
            tex = (r'\sqrt[3]{' if char == '∛' else r'\sqrt{') + bare + '}'
            return tex, tex, pos
        if char.isdigit():
            match = re.match(r'\d+(?:\.\d+)?', source[pos:])
            pos += len(match.group(0))
        elif char.isalpha():
            match = re.match(r'[A-Za-z]+', source[pos:])
            if not match:
                raise ValueError('unknown atom')
            pos += len(match.group(0))
        else:
            raise ValueError('unknown atom')
        text = source[start:pos]
        if pos < len(source) and source[pos] == '°':
            text += symbols['°']
            pos += 1
        return text, text, pos

    def expression(pos, depth, nested=False):
        parts = []
        while pos < len(source):
            char = source[pos]
            if char == ')':
                if nested:
                    break
                raise ValueError('unbalanced formula')
            # ``str.isalnum()`` also matches superscript characters such as
            # ²/³, but those are operators handled by ``symbols`` below and
            # cannot be consumed by the ASCII digit/letter regexes in atom().
            if ('0' <= char <= '9' or 'A' <= char <= 'Z' or
                    'a' <= char <= 'z' or char in '(√∛'):
                wrapped, bare, pos = atom(pos, depth)
                if pos < len(source) and source[pos] == '/':
                    denominator_start = pos + 1
                    try:
                        dw, db, after = atom(denominator_start, depth)
                    except ValueError:
                        parts.append(wrapped)
                        continue
                    explicit_denominator = source[denominator_start] == '(' or source[denominator_start] in '√∛'
                    simple_denominator = len(db) == 1 or db.replace('.', '').isdigit()
                    adjacent = after < len(source) and (source[after].isalnum() or source[after] in '(√∛')
                    if explicit_denominator or (simple_denominator and not adjacent):
                        wrapped = r'\frac{' + bare + '}{' + db + '}'
                        pos = after
                parts.append(wrapped)
            else:
                parts.append(symbols.get(char, char))
                pos += 1
        return ''.join(parts), pos
    try:
        return expression(0, 0)[0]
    except (ValueError, RecursionError):
        return None


def _report_display_text(value: object) -> str:
    return re.sub(
        r"\bQ\d+(?:\s*\(P?\d+\))?",
        lambda match: _question_display_label(match.group(0)),
        str(value or ""), flags=re.IGNORECASE,
    )


def _question_analysis_html(item: dict[str, Any] | None, *, include_feedback: bool = True) -> str:
    if not item:
        return _ai_block("", failed=True)
    feedback = (_narrative_text(item.get("feedback")) or "\n".join(dict.fromkeys(
        text for key in ("observation", "possible_cause", "analysis")
        if (text := _narrative_text(item.get(key)))
    ))) if include_feedback else ""
    review = _narrative_text(item.get("review_note"))
    body = f'<div class="feedback"><b>本题反馈</b>{_report_paragraphs(feedback)}</div>' if feedback else ""
    if review:
        body += f'<div class="review-note"><b>报告分析提示 · 建议核对</b>{_report_paragraphs(review)}</div>'
    steps = _narrative_items(item, "solution_steps")
    if steps:
        label = "参考解法" if item.get("solution_source") == "reference" else "AI 参考解法"
        body += f'<div class="solution"><b>{label}</b><ol>' + "".join(
            f'<li>{_report_paragraphs(step)}</li>' for step in steps if isinstance(step, str) and step.strip()
        ) + '</ol></div>'
        detail = _narrative_text(item.get("full_solution"))
        if detail:
            body += '<details class="solution-detail"><summary>查看完整解法</summary>' + _report_paragraphs(detail) + '</details>'
    # Compatibility for previously generated narratives; keep supporting evidence
    # available without restoring five repetitive rows to the main report.
    if "feedback" not in item:
        details = "\n".join(_narrative_text(item.get(key)) for key in ("evidence", "verification"))
        if details.strip():
            body += '<details><summary>查看分析依据</summary>' + _report_paragraphs(details) + '</details>'
    if not body:
        return _ai_block("", failed=True)
    return (
        '<div class="compact-analysis">' + body + '</div>'
    )


def _report_stem_html(info: QuestionInfo | None, fallback: str) -> str:
    """Render source table cells as cells, instead of model-input pipe text."""
    if info is None or not info.question_markup:
        return _report_paragraphs(fallback.replace("[图片]", ""))
    from html.parser import HTMLParser

    from backend.document_parsing.question_blocks import _INLINE_IMAGE_MARKER

    class StemMarkup(HTMLParser):
        tags = {"p", "div", "br", "table", "thead", "tbody", "tr", "td", "th", "sup", "sub", "b", "strong", "em", "span"}

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []
            self.math_stack = []

        def handle_starttag(self, tag, attrs):
            if tag in self.tags:
                spans = "".join(f' {key}="{value}"' for key, value in attrs
                                if tag in {"td", "th"} and key in {"colspan", "rowspan"}
                                and value and value.isdigit())
                latex = dict(attrs).get("data-latex")
                if tag == "span" and latex:
                    spans += f' class="qm" data-latex="{esc(latex)}"'
                if tag != 'br':
                    self.math_stack.append(bool(latex))
                self.parts.append(f"<{tag}{spans}>")

        def handle_endtag(self, tag):
            if tag in self.tags and tag != "br":
                if self.math_stack:
                    self.math_stack.pop()
                self.parts.append(f"</{tag}>")

        def handle_data(self, value):
            if value.strip():
                value = value.replace("[图片]", "")
                self.parts.append(esc(value) if any(self.math_stack) else _report_inline_math(value))

    parser = StemMarkup()
    parser.feed(_INLINE_IMAGE_MARKER.sub("", info.question_markup))
    return "".join(parser.parts)


@lru_cache(maxsize=1)
def _report_knowledge_assets() -> tuple[str, str]:
    root = Path(__file__).resolve().parents[1] / 'report_assets'
    return ((root / 'personal_knowledge.css').read_text(encoding='utf-8'),
            (root / 'personal_knowledge.js').read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def _report_math_assets() -> str:
    """Embed the same KaTeX engine as the question bank, including offline fonts."""
    root = Path(__file__).resolve().parents[1] / "report_assets" / "katex"
    css = (root / "katex.min.css").read_text(encoding="utf-8")
    def font_source(match):
        font = root / match.group(1)
        return 'src:url(data:font/woff2;base64,' + base64.b64encode(font.read_bytes()).decode() + ') format("woff2")'
    css = re.sub(r'src:url\((fonts/[^)]+\.woff2)\)[^;}]*(?=[;}])', font_source, css)
    js = (root / "katex.min.js").read_text(encoding="utf-8")
    return '<style>' + css + '</style><script>' + js.replace('</script', '<\\/script') + '</script>'


def _question_cell_label(question_id: str) -> str:
    """Q14(P8) → 14(8)；Q7 → 7。"""
    coordinates = question_id_coordinates(question_id)
    if coordinates is None:
        return str(question_id)
    parent, part = coordinates
    return f"{parent}({part})" if part is not None else str(parent)


def _report_class_label(class_name: str) -> str:
    name = str(class_name or "").strip() or "未分班"
    return name if "班" in name else f"{name}班"


def _personal_cell_status(record: StudentQuestionRecord) -> str:
    if record.score == 0 and (
        record.error_category == "未作答"
        or "未作答" in (record.deduction_reason or "")
        or (record.error_summary or "") in {"blank", "blank_or_no_valid_work"}
    ):
        return "blank"
    if record.max_score > 0 and record.score >= record.max_score:
        return "full"
    if record.score == 0:
        return "zero"
    return "part"


def _narrative_analysis_index(
    narrative: dict[str, Any] | None,
    info_by_qid: dict[str, QuestionInfo],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in _narrative_items(narrative, "question_analyses"):
        if not isinstance(item, dict):
            continue
        raw = str(item.get("question_id") or "").strip()
        result[resolve_known_question_id(raw, info_by_qid) or raw] = item
    return result


def _point_question_result(
    student: StudentReportData,
    key: str,
    question: dict[str, Any],
    info_by_qid: dict[str, QuestionInfo],
    session_id: int,
) -> str:
    """步骤观测决定考查点结果；整题失分不推断为每个关联点都未做到。"""
    mastery = student.knowledge_mastery.get(key) or {}
    for ref in mastery.get("source_question_refs") or []:
        if int(ref.get("session_id") or 0) != session_id:
            continue
        raw = str(ref.get("question_id") or "")
        if (resolve_known_question_id(raw, info_by_qid) or raw) != question["id"]:
            continue
        values = _point_observation_values(ref, key)
        if values:
            mean = sum(values) / len(values)
            return "full" if mean == 1 else "zero" if mean == 0 else "part"
    if question["full"] > 0 and question["score"] >= question["full"]:
        return "full"
    return "unknown"


def _point_observation_values(ref: dict[str, Any], key: str) -> list[float]:
    values = []
    for observation in ((ref.get("assessment") or {}).get("point_observations") or []):
        if not isinstance(observation, dict) or observation.get("stable_key") != key:
            continue
        value = observation.get("achieved")
        if isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1:
            values.append(float(value))
    return values


def _point_recurrence_sessions(
    student: StudentReportData, key: str, session_id: int,
    historical_session_ids: set[int] | None = None,
) -> list[dict[str, str]]:
    """仅明确的历史步骤失分支持考查点复发，并保留考试和题号。"""
    rows: list[dict[str, str]] = []
    mastery = student.knowledge_mastery.get(key) or {}
    for ref in mastery.get("source_question_refs") or []:
        sid = int(ref.get("session_id") or 0)
        if sid == session_id or (historical_session_ids is not None and sid not in historical_session_ids):
            continue
        if any(value < 1 for value in _point_observation_values(ref, key)):
            name = str(ref.get("session_name") or "").strip()
            row = {"session_name": name, "question_id": str(ref.get("question_id") or ""), "pattern": "该考查点有步骤失分证据"}
            if name and row not in rows:
                rows.append(row)
    return rows


def _personal_exam_points(
    view: dict[str, Any],
    student: StudentReportData,
    info_by_qid: dict[str, QuestionInfo],
    session_id: int,
    historical_session_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """本次考查点集合：全部 skill/type 节点 + 有未覆盖小题的 topic 节点。"""
    nodes = view.get("nodes") or []
    skill_covered = {
        q["id"] for n in nodes if n.get("kind") in ("skill", "type") for q in n.get("questions") or []
    }
    points = []
    for node in nodes:
        kind = node.get("kind")
        if kind in ("skill", "type"):
            questions = list(node.get("questions") or [])
        elif kind == "topic":
            questions = [
                q for q in node.get("questions") or [] if q["id"] not in skill_covered
            ]
            if not questions:
                continue
        else:
            continue
        results = [
            (q, _point_question_result(student, node["key"], q, info_by_qid, session_id))
            for q in questions
        ]
        syms = [r for _q, r in results]
        if all(s == "full" for s in syms):
            status = "good"
        elif "unknown" in syms:
            status = "unknown"
        elif all(s == "zero" for s in syms):
            status = "bad"
        else:
            status = "mid"
        points.append(
            {
                "key": node["key"],
                "label": node["label"],
                "section": node.get("section") or "",
                "questions": questions,
                "results": results,
                "status": status,
                "recur": _point_recurrence_sessions(student, node["key"], session_id, historical_session_ids),
            }
        )
    # 只关联一个考查点的小问没有步骤观测时，直接按整题得分判定；
    # 关联多个点的题仍保持 unknown，不把整题失分套到每个点。
    targets_by_qid: dict[str, int] = {}
    for p in points:
        for q in p["questions"]:
            targets_by_qid[q["id"]] = targets_by_qid.get(q["id"], 0) + 1
    for p in points:
        results = []
        resolved = False
        for q, r in p["results"]:
            if r == "unknown" and targets_by_qid.get(q["id"]) == 1:
                r = ("full" if q["full"] > 0 and q["score"] >= q["full"]
                     else "zero" if q["score"] == 0 else "part")
                resolved = True
            results.append((q, r))
        if resolved:
            p["results"] = results
            syms = [r for _q, r in results]
            if all(s == "full" for s in syms):
                p["status"] = "good"
            elif "unknown" in syms:
                p["status"] = "unknown"
            elif all(s == "zero" for s in syms):
                p["status"] = "bad"
            else:
                p["status"] = "mid"
    return points


def _points_for_question(points: list[dict[str, Any]], qid: str) -> list[str]:
    labels: list[str] = []
    for p in points:
        if any(q["id"] == qid for q in p["questions"]) and p["label"] not in labels:
            labels.append(p["label"])
    return labels


_CARD_QSEG_RE = re.compile(r"第([0-9０-９、，,和及与\s()（）\-—~～至]+?)题")
_CARD_QGROUP_RE = re.compile(
    r"(\d+)((?:\s*[（(]\s*\d+\s*[）)](?:\s*[-—~～至]\s*[（(]?\s*\d*\s*[）)]?)?)*)"
)
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_CARD_TYPE_WORDS = {
    "选择题": {"choice", "single_choice", "multiple_choice"},
    "填空题": {"fill_blank", "fill_in_blank"},
}


def _part_numbers(group_text: str) -> list[int]:
    """(2)(6)(7)(8) → [2,6,7,8]；(1)-(4)/(1)至(4) → [1,2,3,4]。"""
    nums: list[int] = []
    prev = None
    pos = 0
    for match in re.finditer(r"\d+", group_text):
        sep = group_text[pos : match.start()]
        num = int(match.group(0))
        if prev is not None and re.search(r"[-—~～至]", sep):
            nums.extend(range(prev + 1, num + 1))
        else:
            nums.append(num)
        prev = num
        pos = match.end()
    return nums


def _follow_up_question_ids(
    problem: dict[str, Any],
    suggestion: dict[str, Any] | None,
    student: StudentReportData,
    info_by_qid: dict[str, QuestionInfo],
) -> list[str]:
    """重点跟进卡片的关联题号：优先叙述里的 question_ids，否则按正文文字解析。

    两种来源都只保留该生有失分记录的题号；全部为空时调用方隐藏元信息行。
    """
    lost_ids = {r.question_id for r in student.records if r.lost}
    out: list[str] = []

    def add(qid: str) -> None:
        if qid in lost_ids and qid not in out:
            out.append(qid)

    raw_ids = problem.get("question_ids")
    if isinstance(raw_ids, (list, tuple)):
        for raw in raw_ids:
            text = str(raw).strip()
            if not text:
                continue
            add(resolve_known_question_id(text, info_by_qid) or text)
    if out:
        return out

    # 兼容旧版叙述：正文里的「第…题」片段 + 「选择题/填空题」类别。
    text = (
        str(problem.get("title") or "")
        + str(problem.get("detail") or "")
        + str((suggestion or {}).get("detail") or "")
    ).translate(_FULLWIDTH_DIGITS)

    def add_parent(num: str) -> None:
        parent = resolve_known_question_id(f"Q{num}", info_by_qid) or f"Q{num}"
        for r in student.records:
            if parent_question_id(r.question_id) == parent:
                add(r.question_id)

    for seg in _CARD_QSEG_RE.finditer(text):
        for grp in _CARD_QGROUP_RE.finditer(seg.group(1)):
            parent_num, part_group = grp.group(1), grp.group(2)
            parts = _part_numbers(part_group) if part_group.strip() else []
            if parts:
                for p in parts:
                    raw = f"Q{parent_num}(P{p})"
                    add(resolve_known_question_id(raw, info_by_qid) or raw)
            else:
                add_parent(parent_num)
    remainder = _CARD_QSEG_RE.sub(" ", text)
    for word, types in _CARD_TYPE_WORDS.items():
        if word in remainder:
            for r in student.records:
                if not r.lost:
                    continue
                info = info_by_qid.get(r.question_id)
                if info is not None and info.question_type in types:
                    add(r.question_id)
    return out


def _personal_review_link(question_id: str) -> str:
    return f'<a href="#" class="review-link" data-question-id="{esc(question_id)}">去复核这题 ›</a>'


_PERSONAL_KEYBOARD_JS = """
document.addEventListener('keydown', function(event) {
  if (event.defaultPrevented) return;
  if (!['Escape', 'ArrowLeft', 'ArrowRight'].includes(event.key)) return;
  if (document.querySelector('#report-shot-viewer[open], .psheet:not([hidden])')) return;
  if (event.key !== 'Escape' && event.target.closest('input, textarea, select, [contenteditable="true"]')) return;
  event.preventDefault();
  window.parent.postMessage({type: 'personal-report:key', key: event.key}, location.origin);
});
"""


_PERSONAL_REVIEW_JS = """
document.addEventListener('click', function(event) {
  var link = event.target.closest('.review-link');
  if (!link) return;
  event.preventDefault();
  window.parent.postMessage({type: 'personal-report:open-review', questionId: link.dataset.questionId}, location.origin);
});
"""


def _objective_question(info: QuestionInfo | None) -> bool:
    return _question_type_label(info.question_type if info is not None else "") in {"选择", "填空"}


def _shot_region_bounds(shot: dict[str, Any]) -> tuple[Any, ...] | None:
    region = shot.get("region") or {}
    try:
        x, y, w, h = (float(region[name]) for name in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (x, y, w, h)) or w <= 0 or h <= 0:
        return None
    width = next((region.get(key) for key in ("source_image_width", "template_image_width", "image_width")
                  if region.get(key)), None)
    height = next((region.get(key) for key in ("source_image_height", "template_image_height", "image_height")
                   if region.get(key)), None)
    if width and height:
        x, w = x / float(width), w / float(width)
        y, h = y / float(height), h / float(height)
    frame = (shot.get("page") or _region_page(region), bool(width and height))
    return frame, x, y, x + w, y + h


def _same_evidence_row(left: dict[str, Any], right: dict[str, Any]) -> bool:
    a, b = _shot_region_bounds(left), _shot_region_bounds(right)
    if a is None or b is None or a[0] != b[0]:
        return False
    overlap_x = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    overlap_y = max(0.0, min(a[4], b[4]) - max(a[2], b[2]))
    return (
        overlap_x >= .9 * max(a[3] - a[1], b[3] - b[1])
        and overlap_y >= .9 * max(a[4] - a[2], b[4] - b[2])
    )


def _display_shot_groups(
    shots: dict[str, dict[str, Any]], info_by_qid: dict[str, QuestionInfo],
) -> dict[str, dict[str, Any]]:
    """只组织报告显示；模型仍使用原始截图与题号。"""
    groups: list[dict[str, Any]] = []
    for key, shot in shots.items():
        parent = shot.get("parent_question_id") or key
        region_id = shot.get("region_question_id") or parent
        info = info_by_qid.get(region_id) or info_by_qid.get(parent)
        objective = _objective_question(info)
        group = next((item for item in groups
                      if item["objective"] == objective and (
                          all(_same_evidence_row(shot, existing) for existing in item["items"])
                          if objective else item["parents"] == [parent]
                      )), None)
        if group is None:
            group = {"parents": [], "region_ids": [], "items": [], "objective": objective}
            groups.append(group)
        if parent not in group["parents"]:
            group["parents"].append(parent)
        if region_id not in group["region_ids"]:
            group["region_ids"].append(region_id)
        group["items"].append(shot)
    result = {}
    for index, group in enumerate(groups):
        kept = []
        for shot in group["items"]:
            bounds = _shot_region_bounds(shot)
            contained = False
            if group["objective"] and bounds is not None:
                for other in group["items"]:
                    if other is shot:
                        continue
                    outer = _shot_region_bounds(other)
                    if outer is None or outer[0] != bounds[0]:
                        continue
                    contains = outer[1] <= bounds[1] and outer[2] <= bounds[2] and outer[3] >= bounds[3] and outer[4] >= bounds[4]
                    if contains and (outer != bounds or any(other is previous for previous in kept)):
                        contained = True
                        break
            if not contained:
                kept.append(shot)
        group["items"] = kept
        group["region_ids"].sort(key=lambda q: question_id_coordinates(q) or (10**9, 0))
        group["label"] = "、".join(_question_display_label(q) for q in group["region_ids"])
        result[f"answer-evidence-{index + 1}"] = group
    return result


def _shots_for_parent(shots: dict[str, dict[str, Any]], parent: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (key, shot)
        for key, shot in shots.items()
        if parent in shot["parents"]
    ]


def _shots_html(
    shots: dict[str, dict[str, Any]], parent: str, title: str, *,
    inline: bool = False, shown_groups: set[str] | None = None,
) -> str:
    parts = []
    for key, group in _shots_for_parent(shots, parent):
        label = group["label"] or title
        if not inline:
            parts.append(f'<div class="shot-reference"><button type="button" data-open-q="{esc(parent)}" data-open-more>'
                         f'查看作答与解法 · {esc(label)}</button></div>')
            continue
        if shown_groups is not None and key in shown_groups:
            continue
        if shown_groups is not None:
            shown_groups.add(key)
            evidence = _shot_sources_html({key: group}, hidden=False)
        else:
            evidence = f'<div data-shot-group="{esc(key)}"></div>'
        parts.append(f'<div class="shot-preview"><div class="shot-preview-title">作答截图 · {esc(label)} · 点击放大</div>'
                     f'{evidence}</div>')
    return "".join(parts)


def _shot_sources_html(shots: dict[str, dict[str, Any]], *, hidden: bool = True) -> str:
    sources = []
    for key, group in shots.items():
        label = group["label"]
        images = "".join(
            f'<div class="shot"><button type="button" class="shot-enlarge" aria-label="放大{esc(label)}作答截图">'
            f'<img src="{esc(shot.get("src") or shot["data_uri"])}" alt="{esc(label)}作答截图"></button>'
            f'<div class="cap">{esc(shot.get("caption") or "学生作答（原卷截图）")}</div></div>'
            for shot in group["items"]
        )
        note = '<div class="note">区域只部分重叠，保留各张截图，避免遗漏边缘作答。</div>' if group["objective"] and len(group["items"]) > 1 else ""
        sources.append(f'<div{" hidden" if hidden else ""} id="{esc(key)}" data-shot-source>{note}{images}</div>')
    return "".join(sources)


def _objective_answers_html(record: StudentQuestionRecord, info: QuestionInfo | None) -> str:
    student_answer = record.student_answer or ("未作答" if _personal_cell_status(record) == "blank" else "未记录")
    answer = info.canonical_answer if info is not None else ""
    return (f'<dl class="kv objective-answers"><dt>学生作答</dt><dd>{_report_inline_math(student_answer)}</dd>'
            f'<dt>标准答案</dt><dd><b class="ans">{_report_inline_math(answer or "未记录")}</b></dd></dl>')


# 教师确认流程写入 teacher_comment 的占位文案：不算真实批语，不向家长展示。
_TEACHER_COMMENT_PLACEHOLDERS = frozenset(
    {REVIEW_CONFIRMED_REASON, "教师已确认", "教师已确认最终分", "已复核"}
)


def _real_teacher_comment(record: StudentQuestionRecord) -> str:
    """真实老师批语；占位确认文案视为无批语。"""
    comment = (record.teacher_comment or "").strip()
    return "" if comment in _TEACHER_COMMENT_PLACEHOLDERS else comment


def _grading_fallback_html(
    record: StudentQuestionRecord,
    info: QuestionInfo | None,
    heading: str = "",
    show_answer: bool = True,
    show_comment: bool = True,
) -> str:
    """无 AI 叙述项时的降级块：真实老师批语 + 参考答案。

    不展示 deduction_reason/error_summary 等原始批改记录与确认占位文案；
    两者都没有时不输出块。
    """
    comment = _real_teacher_comment(record) if show_comment else ""
    answer = (info.canonical_answer if info is not None else "") or ""
    if not comment and not (show_answer and answer):
        return ""
    head = f"<b>{esc(heading)}</b>" if heading else ""
    body = ""
    if comment:
        body += f"<div>老师批语：{_report_inline_math(comment)}</div>"
    if show_answer and answer:
        body += f'<div>参考答案：<b class="ans">{_report_inline_math(answer)}</b></div>'
    return f'<div class="qd-fb">{head}{body}</div>'


def _analysis_or_fallback_html(
    record: StudentQuestionRecord,
    info: QuestionInfo | None,
    analysis: dict[str, Any] | None,
    *,
    show_answer: bool = True,
    show_comment: bool = True,
    include_feedback: bool = True,
) -> str:
    """AI 叙述项为空或没有可展示内容时退化为老师批语/参考答案，不显示生成失败提示。"""
    rendered = ""
    if analysis:
        rendered = _question_analysis_html(analysis, include_feedback=include_feedback)
        if AI_FAILED_NOTE in rendered:
            rendered = ""
    if not rendered:
        rendered = _grading_fallback_html(
            record, info, show_answer=show_answer, show_comment=show_comment
        )
    if info is not None and info.reference_analysis and not _narrative_items(analysis, "solution_steps"):
        reference = _strip_repeated_stem(
            info.reference_analysis,
            (info.question_text or info.stem_summary) or "",
        )
        if reference:
            rendered += f'<div class="solution"><b>参考解法</b>{_report_paragraphs(reference)}</div>'
    return rendered


def _stem_block_html(info: QuestionInfo | None, fallback: str, title: str) -> str:
    html_text = f'<div class="stem">{_report_stem_html(info, fallback)}</div>'
    if info is not None:
        figures = "".join(
            f'<img src="data:image/png;base64,{base64.b64encode(blob).decode()}" alt="{esc(title)}题图">'
            for role, blob in info.reference_images
            if role == "question"
        )
        if figures:
            html_text += f'<div class="reference-figures">{figures}</div>'
    return html_text


def _wrong_way_text(row: dict[str, Any]) -> str:
    """单行错因的报告用词：错法名，无错法名时用错误大类。"""
    return str(row.get("pattern") or row.get("category") or "").strip()


_STEP_REASON_BOILERPLATE_RE = re.compile(r"^按.{0,8}(?:有效)?步骤给分[。.]?$")
_STEM_STRIP_RE = re.compile(r"[\s\^_(){}\（\）．.,、，:：；;　\$\\]")


def _normalized_stem_text(text: object) -> str:
    line = re.sub(r"^\s*\d+\s*[．.、]\s*", "", str(text or ""))
    return _STEM_STRIP_RE.sub("", line)


def _strip_repeated_stem(reference_analysis: str, stem: str) -> str:
    """题库参考解法开头照抄题面（含题号、选项与[图片]行）的部分不再重复显示。"""
    norm_stem = _normalized_stem_text(stem)
    lines = str(reference_analysis or "").splitlines()
    while lines:
        first = lines[0].strip()
        normalized = _normalized_stem_text(first)
        if first == "[图片]" or (normalized and normalized in norm_stem):
            lines.pop(0)
            continue
        break
    return "\n".join(lines).strip()


def _question_detail_template(
    record: StudentQuestionRecord,
    info: QuestionInfo | None,
    points_labels: list[str],
    analysis: dict[str, Any] | None,
    shots: dict[str, dict[str, Any]],
    errors: list[dict[str, Any]] | None = None,
    review_links: bool = False,
    step_diagnosis: dict[str, Any] | None = None,
    originals_released: bool = False,
) -> str:
    """答题一览方格对应的隐藏详情片段（<template>，点击方格原位展开）。"""
    parent = parent_question_id(record.question_id)
    title = _question_display_label(record.question_id)
    type_label = _question_type_label(info.question_type if info is not None else "")
    avg = info.class_avg if info is not None else None
    head_parts = [title]
    if type_label:
        head_parts.append(type_label)
    head_parts.append(f"得 {fmt_num(record.score)}/{fmt_num(record.max_score)}")
    if avg is not None:
        head_parts.append(f"全班平均 {fmt_num(round(avg, 1))}")
    head = " · ".join(head_parts)
    body = [f'<div class="qdetail-box"><div class="qd-head">{esc(head)}</div>']
    if review_links:
        body.append(_personal_review_link(record.question_id))
    errline_parts = []
    if points_labels:
        tags = "、".join(f'<span class="pt-tag">{esc(t)}</span>' for t in points_labels)
        errline_parts.append(f"考点：{tags}")
    if record.lost and errors:
        wrong = "；".join(dict.fromkeys(
            text for row in errors if (text := _wrong_way_text(row))
        ))
        if wrong:
            errline_parts.append(f"错在：{esc(wrong)}（AI 辅助）")
    if errline_parts:
        body.append(f'<div class="errline">{" · ".join(errline_parts)}</div>')
    objective = _objective_question(info)
    if objective:
        body.append(_objective_answers_html(record, info))
    feedback = _narrative_text((analysis or {}).get("feedback"))
    top_feedback = bool(record.lost and feedback)
    if top_feedback:
        body.append(f'<div class="qd-fb">{_report_paragraphs(feedback)}</div>')
    more = ['<div class="qmore" hidden>']
    fallback = ((info.question_text or info.stem_summary) if info is not None else "") or title
    more.append(_stem_block_html(info, fallback, _question_display_label(parent)))
    evidence = _shots_html(shots, parent, title, inline=True)
    more.append(evidence or ('<div class="note">原卷已释放，无法显示作答图；分数与批语不受影响。</div>'
                            if originals_released else '<div class="note">未找到本题作答截图；分数与批语保留。</div>'))
    kv = []
    if not objective and not evidence:
        answer = record.student_answer or ("未作答" if _personal_cell_status(record) == "blank" else "未记录")
        kv.append(f'<dt>学生作答</dt><dd>{_report_inline_math(answer)}</dd>')
    comment = _real_teacher_comment(record)
    if comment:
        kv.append(f'<dt>老师批语</dt><dd>{_report_inline_math(comment)}</dd>')
    if record.lost:
        step_row = _step_diagnosis_html(step_diagnosis, _personal_cell_status(record))
        if step_row:
            kv.append(step_row)
    if kv:
        more.append(f'<dl class="kv">{"".join(kv)}</dl>')
    if analysis and analysis.get("solution_source") == "reference" and not (info and info.reference_analysis):
        analysis = {**analysis, "solution_source": "ai"}
    more.append(_analysis_or_fallback_html(record, info, analysis, show_answer=not objective,
                                           show_comment=False, include_feedback=not top_feedback))
    more.append("</div>")
    body.append('<button type="button" class="qmore-btn">看题目、作答和解法</button>')
    body.append("".join(more))
    body.append("</div>")
    return "".join(body)


def _step_diagnosis_html(
    diagnosis: dict[str, Any] | None,
    record_status: str = "",
) -> str:
    if not diagnosis or record_status == "blank":
        return ""
    from backend.step_diagnosis import STEP_STATE_LABELS, STEP_SOURCE_LABELS
    if diagnosis.get("availability") != "available":
        return ""
    independent = next(
        (key for key, label in STEP_STATE_LABELS.items() if label == "独立失分"),
        "independent",
    )
    rows = []
    for step in diagnosis.get("steps") or []:
        if step.get("state") != independent:
            continue
        reason = str(step.get("reason") or "").strip()
        note = (
            f'<div class="note">{_report_inline_math(reason)}</div>'
            if reason and not _STEP_REASON_BOILERPLATE_RE.match(reason)
            else ""
        )
        rows.append(f'<div>{_report_inline_math(str(step.get("goal") or ""))}{note}</div>')
    if not rows:
        return ""
    source = STEP_SOURCE_LABELS.get(diagnosis.get("source"), "")
    title = f"卡在哪一步（{source}）" if source else "卡在哪一步"
    return f'<dt>{esc(title)}</dt><dd>{"".join(rows)}</dd>'


def _lost_appendix_html(
    student: StudentReportData,
    info_by_qid: dict[str, QuestionInfo],
    analysis_by_qid: dict[str, dict[str, Any]],
    shots: dict[str, dict[str, Any]],
    error_map: dict[str, list[dict[str, Any]]] | None = None,
    review_links: bool = False,
    step_diagnoses: dict[str, dict[str, Any]] | None = None,
) -> str:
    """失分题详解附录：全部失分小问按大题分组，默认收起、打印展开。"""
    lost = [r for r in student.records if r.lost]
    if not lost:
        return ""
    groups: dict[str, list[StudentQuestionRecord]] = {}
    for r in lost:
        groups.setdefault(parent_question_id(r.question_id), []).append(r)
    ordered = sorted(
        groups.items(), key=lambda kv: question_id_coordinates(kv[0]) or (10**9, 0)
    )
    cards = []
    shown_groups: set[str] = set()
    for parent, recs in ordered:
        info = info_by_qid.get(parent) or next(
            (info_by_qid.get(r.question_id) for r in recs), None
        )
        parent_records = [
            r for r in student.records if parent_question_id(r.question_id) == parent
        ]
        gscore = sum(r.score for r in parent_records)
        gmax = sum(r.max_score for r in parent_records)
        title = _question_display_label(parent)
        type_label = _question_type_label(info.question_type if info is not None else "")
        objective = _objective_question(info)
        fallback = ((info.question_text or info.stem_summary) if info is not None else "") or title
        parts = [
            f'<div class="qcard{" objective" if objective else ""}"><div class="head"><b>{esc(title)}'
            f'{(" · " + esc(type_label)) if type_label else ""}</b>'
            f'<span class="score">得 {fmt_num(gscore)} 分 / 满分 {fmt_num(gmax)} 分</span></div>',
            _personal_review_link(parent) if review_links else "",
        ]
        if not objective:
            parts.append(_stem_block_html(info, fallback, title))
        group_has_shots = bool(_shots_for_parent(shots, parent))
        for record in sorted(recs, key=lambda r: question_id_coordinates(r.question_id) or (10**9, 0)):
            part_info = info_by_qid.get(record.question_id)
            analysis = analysis_by_qid.get(record.question_id)
            if (
                analysis
                and analysis.get("solution_source") == "reference"
                and not (part_info and part_info.reference_analysis)
            ):
                analysis = {**analysis, "solution_source": "ai"}
            step_row = _step_diagnosis_html(
                (step_diagnoses or {}).get(record.question_id),
                _personal_cell_status(record),
            )
            kv = [step_row] if step_row else []
            if objective or not group_has_shots:
                if record.student_answer:
                    kv.append(f"<dt>学生作答</dt><dd>{_report_inline_math(record.student_answer)}</dd>")
                elif _personal_cell_status(record) == "blank":
                    kv.append("<dt>学生作答</dt><dd>未作答</dd>")
                elif objective:
                    kv.append("<dt>学生作答</dt><dd>未记录</dd>")
            answer = part_info.canonical_answer if part_info is not None else ""
            if answer and (objective or not (analysis and analysis.get("solution_steps"))):
                kv.append(f'<dt>标准答案</dt><dd><b class="ans">{_report_inline_math(answer)}</b></dd>')
            elif objective and not answer:
                kv.append("<dt>标准答案</dt><dd>未记录</dd>")
            teacher_comment = _real_teacher_comment(record)
            if teacher_comment:
                kv.append(f"<dt>老师批语</dt><dd>{_report_inline_math(teacher_comment)}</dd>")
            error_rows = (error_map or {}).get(record.question_id) or []
            error_text = "；".join(dict.fromkeys(
                text for row in error_rows if (text := _wrong_way_text(row))
            ))
            if error_text:
                kv.append(f"<dt>错在</dt><dd>{esc(error_text)}（AI 辅助）</dd>")
            heading = (
                f'<h3>{esc(_question_display_label(record.question_id))}'
                f'<span>得 {fmt_num(record.score)} / {fmt_num(record.max_score)} 分</span></h3>'
                if len(parent_records) > 1
                else ""
            )
            # kv 中已有标准答案与老师批语行，降级块不重复输出
            analysis_html = _analysis_or_fallback_html(
                record, part_info, analysis, show_answer=False, show_comment=False
            )
            parts.append(
                f'<div class="qpart">{heading}'
                + (f'<dl class="kv">{"".join(kv)}</dl>' if kv else "")
                + analysis_html
                + "</div>"
            )
        if objective:
            parts.append(f'<details class="question-stem"><summary>查看题面 · {esc(title)}</summary>'
                         f'{_stem_block_html(info, fallback, title)}</details>')
        parts.append(_shots_html(shots, parent, title, inline=True, shown_groups=shown_groups))
        parts.append("</div>")
        cards.append("".join(parts))
    return (
        f'<details class="appendix"><summary>全部失分题详解（{len(lost)} 道小题）</summary>'
        + "".join(cards)
        + "</details>"
    )


def _load_student_histories(
    repositories: GradingRepositoryAccess,
    data: SessionAnalysisData,
    data_root: Path | None,
) -> dict[int, list[dict[str, Any]]]:
    """同教学学期内、不晚于本场的历次成绩，每次导出只读取一轮。

    任何历史读取异常都按无历史处理，不阻断报告导出。
    """
    try:
        session_row = (
            repositories.sessions.get_grading_session(data.session_id) or {}
        )
        volume = str(session_row.get("curriculum_volume_id") or "").strip()
        if not volume:
            return {}
        candidate_ids = [
            int(row["id"])
            for row in repositories.sessions.list_grading_sessions()
            if str(row.get("curriculum_volume_id") or "").strip() == volume
        ]
        assembled: list[tuple[int, SessionAnalysisData, dict[int, tuple]]] = []
        for sid in candidate_ids:
            try:
                session_data = assemble_session_analysis(
                    repositories, sid, data_root=data_root, page_only=True
                )
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "optional operation unavailable: _load_student_histories (%s)", type(exc).__name__,
                )
                continue
            if (
                sid != data.session_id
                and data.graded_at
                and session_data.graded_at
                and session_data.graded_at > data.graded_at
            ):
                continue
            by_student = {
                s.student_id: (group, s)
                for group in split_session_analysis_by_class(session_data).values()
                for s in group.students
            }
            assembled.append((sid, session_data, by_student))
        assembled.sort(key=lambda item: (item[1].graded_at or "\uffff", item[0]))
        histories: dict[int, list[dict[str, Any]]] = {
            s.student_id: [] for s in data.students
        }
        for sid, session_data, by_student in assembled:
            for student in data.students:
                entry: dict[str, Any] = {
                    "session_id": sid,
                    "name": session_data.session_name,
                    "full": session_data.full_score,
                    "avg": session_data.stats.get("avg"),
                    "score": None,
                    "rank": None,
                    "present": None,
                    "blank": 0,
                    "items": 0,
                    "lost": [],
                }
                hit = by_student.get(student.student_id)
                if hit is not None:
                    group, matched = hit
                    scored = [r for r in matched.records if r.max_score > 0]
                    entry.update(
                        score=matched.student_score,
                        rank=matched.rank,
                        present=group.present,
                        avg=group.stats.get("avg"),
                        blank=sum(
                            1 for r in scored
                            if _personal_cell_status(r) == "blank"
                        ),
                        items=len(scored),
                        lost=[r.question_id for r in scored if r.lost],
                    )
                histories[student.student_id].append(entry)
        return histories
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "optional operation unavailable: _load_student_histories (%s)", type(exc).__name__,
        )
        return {}


def _lost_group_label(
    parent: str,
    student: StudentReportData,
    info_by_qid: dict[str, QuestionInfo],
) -> str:
    """「第14题计算」形式：大题显示名 + 题型标签。"""
    info = info_by_qid.get(parent)
    if info is None:
        info = next(
            (
                info_by_qid.get(r.question_id)
                for r in student.records
                if parent_question_id(r.question_id) == parent
            ),
            None,
        )
    type_label = _question_type_label(info.question_type if info is not None else "")
    return _question_display_label(parent) + type_label


def _follow_up_action_html(suggestion: dict[str, Any] | None) -> str:
    """问题卡里的跟进任务框：□ 先做 / ✓ 做到，可选「家长：」一行。"""
    suggestion = suggestion or {}
    action = _narrative_text(suggestion.get("first_action")) or _narrative_text(suggestion.get("detail")) or "先看相关题，弄清错在哪里再订正。"
    check = _narrative_text(suggestion.get("completion_check")) or "不看解析能独立重做。"
    parts = [
        f'<div class="ptask"><div class="ptask-do"><i class="ptask-sq"></i><div>{_report_inline_math(_report_display_text(action))}</div></div>'
        f'<div class="ptask-done"><i class="ptask-ok"></i><div>{_report_inline_math(_report_display_text(check))}</div></div></div>',
    ]
    parent_help = _narrative_text(suggestion.get("parent_help"))
    if parent_help:
        parts.append(f'<div class="pparent"><b>家长：</b>{_report_inline_math(_report_display_text(parent_help))}</div>')
    return "".join(parts)


def _load_personal_error_histories(
    repositories: GradingRepositoryAccess,
    histories: dict[int, list[dict[str, Any]]],
    session_id: int,
    reports_dir: Path,
    data_root: Path | None,
) -> dict[int, dict[str, dict[str, list[dict[str, str]]]]]:
    """复用有效错因记录，一次读取每场历史，保留报告所需的题号和错法。"""
    from backend.class_analysis import session_error_records

    session_names = {
        int(entry["session_id"]): str(entry.get("name") or "")
        for entries in histories.values() for entry in entries
        if entry.get("session_id") != session_id
    }
    index: dict[int, dict[str, dict[str, list[dict[str, str]]]]] = {}
    for sid, name in sorted(session_names.items()):
        for student_id, questions in session_error_records(
            repositories, sid, reports_dir, data_root=data_root,
        ).items():
            bucket = index.setdefault(student_id, {"categories": {}, "patterns": {}})
            for qid, records in questions.items():
                for row in records:
                    evidence = {"session_name": name, "question_id": qid,
                                "session_id": sid,
                                "category": str(row.get("category") or ""),
                                "pattern": str(row.get("pattern") or "")}
                    for key, value in (("categories", evidence["category"]), ("patterns", evidence["pattern"])):
                        if value and not (key == "patterns" and value == evidence["category"]):
                            entries = bucket[key].setdefault(value, [])
                            if evidence not in entries:
                                entries.append(evidence)
    return index


def _render_personal_html(
    data: SessionAnalysisData,
    student: StudentReportData,
    narrative: dict[str, Any] | None,
    shots: dict[str, dict[str, Any]],
    history: list[dict[str, Any]] | None = None,
    error_map: dict[str, list[dict[str, Any]]] | None = None,
    error_history: dict[str, dict[str, list[Any]]] | None = None,
    *, online: bool = False, review_links: bool = False, originals_released: bool = False,
    ai_placeholder: bool | None = None,
    narrative_saved_at: str | None = None,
    step_diagnoses: dict[str, dict[str, Any]] | None = None,
) -> str:
    # 在线页 narrative_mode="none" 的数据版：AI 段落显示占位文案而不是失败提示。
    if ai_placeholder is None:
        ai_placeholder = online and narrative is None
    pending_ai = bool(ai_placeholder) and narrative is None
    info_by_qid = {info.question_id: info for info in data.questions}
    shots = _display_shot_groups(shots, info_by_qid)
    analysis_by_qid = _narrative_analysis_index(narrative, info_by_qid)
    history = [e for e in (history or []) if isinstance(e, dict)]
    error_map = error_map or {}
    error_history = error_history or {}

    def question_errors(question_id: str) -> list[dict[str, Any]]:
        return error_map.get(question_id) or []

    def error_label(question_id: str) -> str:
        """本题错法文案：错法名，无错法名时用大类；多条用「；」连接。"""
        parts = []
        for row in question_errors(question_id):
            text = _wrong_way_text(row)
            if text and text not in parts:
                parts.append(text)
        return "；".join(parts)

    def error_recurrence(question_ids: list[str]) -> tuple[str, list[Any]]:
        """只在错法名一致时提示同一错法再次出现；仅同大类不提示。"""
        matches: list[Any] = []
        for qid in question_ids:
            for row in question_errors(qid):
                pattern = row.get("pattern")
                if not pattern or pattern == row.get("category"):
                    continue
                for evidence in (error_history.get("patterns") or {}).get(str(pattern), []):
                    if evidence and evidence not in matches:
                        matches.append(evidence)
        if matches:
            return "同一错法再次出现", matches
        return "", []

    def question_tags(question_ids: list[str]) -> str:
        """失分题号小片：按作答状态着色、带当前得分；点击在本卡题号范围内翻详情。"""
        record_by_qid = {r.question_id: r for r in student.records}
        ordered = sorted(question_ids, key=lambda q: question_id_coordinates(q) or (10**9, 0))
        ctx = esc(",".join(ordered))
        tags = []
        for qid in ordered:
            rec = record_by_qid.get(qid)
            status = _personal_cell_status(rec) if rec is not None else "blank"
            label = _question_display_label(qid)
            score = (
                f'<span class="qtag-s">{fmt_num(rec.score)}/{fmt_num(rec.max_score)}</span>'
                if rec is not None else ""
            )
            tags.append(
                f'<button type="button" class="qtag qtag-{status}" data-open-q="{esc(qid)}" '
                f'data-open-ctx="{ctx}" aria-label="查看{esc(label)}的作答证据">'
                f'{esc(label)}{score}</button>')
        return "".join(tags)

    points = _personal_exam_points(
        _personal_knowledge_view(data, student), student, info_by_qid, data.session_id,
        {int(entry["session_id"]) for entry in history if entry.get("session_id") != data.session_id},
    )

    # ---- A 成绩卡 ----
    meta = (
        f"{esc(data.session_name)} · {esc(data.subject)} · "
        f"{esc(_report_class_label(student.class_name))} · {esc(student.graded_at)}"
    )
    # 名次格：主值 名次/参考人数；仅当本次与上次参考人数不同才加一行小注。
    prior = [
        e
        for e in history
        if e["session_id"] != data.session_id and e.get("score") is not None
    ]
    prev = prior[-1] if prior else None
    rank_sub = ""
    if prev is not None and prev.get("present") and prev["present"] != data.present:
        rank_sub = f'<div class="sub">参考人数 {fmt_num(prev["present"])}→{data.present}</div>'

    # 较上次格：主值是名次变化（名次数小是进步），分数只作小注；规则不变。
    if prev is None:
        change_cell = '<div class="v muted">暂无对比</div>'
    else:
        if prev.get("full") == data.full_score and data.full_score > 0:
            delta = student.student_score - float(prev["score"])
            if delta == 0:
                score_sub = "分数持平"
            else:
                sign = "+" if delta > 0 else "−"
                score_sub = f"分数 {sign}{fmt_num(abs(delta))}分"
        else:
            score_sub = "满分不同，分数不直接比"
        if prev.get("rank") and student.rank:
            delta_rank = int(prev["rank"]) - student.rank
            if delta_rank > 0:
                change_v = f'<div class="v up">↑ {delta_rank} 名</div>'
            elif delta_rank < 0:
                change_v = f'<div class="v down">↓ {abs(delta_rank)} 名</div>'
            else:
                change_v = '<div class="v">名次持平</div>'
        else:
            change_v = '<div class="v muted">暂无对比</div>'
        change_cell = f'{change_v}<div class="sub">{esc(score_sub)}</div>'
    avg = data.stats.get("avg")
    avg_text = f"{float(avg):.1f}" if avg is not None else "—"

    # 班级分数分布：与班级统计同一份学生总分，10 等分；孩子所在格描边。
    def _score_bin(score_value: float) -> int:
        # 先按 6 位小数取整再取整格，避免满分比例的浮点误差把孩子错落到前一格。
        return min(9, max(0, int(round(score_value * 10 / data.full_score, 6))))
    heatmap = ""
    class_scores = [float(s.student_score) for s in data.students]
    if data.full_score > 0 and class_scores:
        bins = [0] * 10
        for score_value in class_scores:
            bins[_score_bin(score_value)] += 1
        max_bin = max(bins) or 1
        my_bin = _score_bin(float(student.student_score or 0))
        heat_cells = []
        for i, count in enumerate(bins):
            style = ""
            if count:
                ratio = count / max_bin
                color = "#fff" if ratio > 0.55 else "#2b3a4e"
                style = (f' style="background:rgba(43,108,176,{0.15 + 0.72 * ratio:.2f});'
                         f'color:{color}"')
            me = " me" if i == my_bin else ""
            marker = (f'<em class="pheat-me">{fmt_num(student.student_score)}</em>'
                      if i == my_bin else "")
            text = str(count) if count else ""
            heat_cells.append(f'<div class="pheat-cell{me}"{style}>{marker}{text}</div>')
        avg_marker = ""
        if data.stats.get("avg") is not None:
            pct = max(0.0, min(100.0, float(data.stats["avg"]) / data.full_score * 100))
            label_pct = max(7.0, min(93.0, pct))
            avg_marker = (
                f'<span class="pheat-avg" style="left:{pct:.1f}%"><i></i></span>'
                f'<em class="pheat-avglb" style="left:{label_pct:.1f}%">班均</em>'
            )
        heatmap = (
            '<div class="pheat"><div class="pheat-strip">'
            + "".join(heat_cells) + avg_marker
            + '</div>'
            + f'<div class="pheat-ends"><span>0</span><span>{fmt_num(data.full_score)}</span></div>'
            + '<div class="pheat-cap">全班分数分布（每格是人数）</div></div>'
        )
    review = (
        '<div class="review-banner">有题目等待老师复核，分数可能微调</div>'
        if student.needs_review
        else ""
    )
    strengths = [
        _narrative_text(i)
        for i in _narrative_items(narrative, "strengths")
        if _narrative_text(i)
    ]
    small_sample_note = (
        '<div class="note" style="margin-top:6px">本场参考人数较少，班级对比仅供参考。</div>'
        if data.small_sample
        else ""
    )
    card_a = f"""
<div class="card">
  {review}
  <div class="meta">{meta}</div>
  <div class="name">{esc(student.student_name)}</div>
  <div class="score-line{' has-heat' if heatmap else ''}"><span class="big">{fmt_num(student.student_score)}</span><span class="of">/ {fmt_num(data.full_score)}</span>{heatmap}</div>
  <div class="stat3">
    <div class="cell"><div class="l">班级名次</div><div class="v">{student.rank}/{data.present}</div>{rank_sub}</div>
    <div class="cell"><div class="l">较上次</div>{change_cell}</div>
    <div class="cell"><div class="l">班级平均</div><div class="v">{avg_text}</div></div>
  </div>
  {small_sample_note}
</div>"""

    # ---- C 这次考试对错 ----
    scored_records = [r for r in student.records if r.max_score > 0]
    counts = {"full": 0, "part": 0, "zero": 0, "blank": 0}
    any_hard = False
    cells, templates = [], []
    for record in scored_records:
        info = info_by_qid.get(record.question_id)
        status = _personal_cell_status(record)
        counts[status] = counts.get(status, 0) + 1
        sym = {"full": "✓", "part": "△", "zero": "✗", "blank": "—"}[status]
        hard = info is not None and info.class_rate is not None and info.class_rate < 0.5
        any_hard = any_hard or hard
        cells.append(
            f'<button type="button" class="qcell {status}" data-q="{esc(record.question_id)}" '
            f'aria-expanded="false"><span class="sym">{sym}</span>'
            f'{"<span class=hard>▲</span>" if hard else ""}'
            f'<span class="qn">{esc(_question_cell_label(record.question_id))}</span>'
            f'<span class="qs">{fmt_num(record.score)}/{fmt_num(record.max_score)}</span></button>'
        )
        analysis = analysis_by_qid.get(record.question_id)
        labels = _points_for_question(points, record.question_id)
        templates.append(
            f'<template data-q="{esc(record.question_id)}">'
            + _question_detail_template(
                record, info, labels, analysis, shots, errors=question_errors(record.question_id), review_links=review_links,
                step_diagnosis=(step_diagnoses or {}).get(record.question_id), originals_released=originals_released,
            )
            + "</template>"
        )
    legend_html = " · ".join(
        f'<span class="psw {cls}"><i></i>{esc(label)} {counts[key]}</span>'
        for cls, key, label in (
            ("g", "full", "满分"), ("a", "part", "部分得分"),
            ("r", "zero", "零分"), ("gr", "blank", "未作答"),
        )
    )
    if any_hard:
        legend_html += '<div class="plegend-hard">▲ 全班平均得分不到一半</div>'
    strength_line = (
        f'<div class="pstrength"><span class="pstrength-tag">亮点</span>{_report_inline_math(strengths[0])}</div>'
        if strengths else ""
    )
    card_c = f"""
<div class="card">
  <h2>这次考试对错</h2>
  <div class="qgrid">{''.join(cells)}</div>
  <div class="plegend">{legend_html}</div>
  {strength_line}
  <div class="phint">点题号看题目、作答和解法</div>
  {''.join(f'<div class="note">{esc(note)}</div>' for note in student.material_notes)}
  <div id="qpanel" hidden></div>
  {''.join(templates)}
</div>"""

    # ---- D 重点跟进 ----
    points_by_qid: dict[str, list[dict[str, Any]]] = {}
    for p in points:
        for q in p["questions"]:
            points_by_qid.setdefault(q["id"], []).append(p)
    problem_pairs = [
        (idx, item) for idx, item in enumerate(_narrative_items(narrative, "problems"))
        if isinstance(item, dict) and _narrative_text(item.get("title")) and _narrative_text(item.get("detail"))
    ][:3]
    if problem_pairs:
        suggestions = _narrative_items(narrative, "suggestions")
        dcards = []
        linked_questions: set[str] = set()
        overlapping = False
        for idx, problem in problem_pairs:
            suggestion = suggestions[idx] if idx < len(suggestions) and isinstance(suggestions[idx], dict) else None
            qids = _follow_up_question_ids(problem, suggestion, student, info_by_qid)
            overlapping = overlapping or bool(linked_questions.intersection(qids))
            linked_questions.update(qids)
            lost_sum = sum(
                r.lost_points for r in student.records if r.question_id in qids
            )
            lost_pill = (
                f'<span class="plost">失 {fmt_num(lost_sum)} 分</span>'
                if qids and lost_sum > 0 else ""
            )
            recurrence_rows: list[Any] = []
            _, error_recur = error_recurrence(qids)
            for q in qids:
                for p in points_by_qid.get(q, []):
                    for row in p["recur"]:
                        if row not in recurrence_rows:
                            recurrence_rows.append(row)
            if error_recur:
                recurrence_rows = error_recur
            badge = '<span class="pmissed">以前也错过</span>' if recurrence_rows else ""
            timeframe = _narrative_text((suggestion or {}).get("timeframe"))
            when = f'<span class="when">{esc(timeframe)}</span>' if timeframe in {"本周", "长期"} else ""
            dcards.append(
                f'<div class="dcard"><div class="dhead"><span class="no">{len(dcards) + 1}</span>'
                f'<span class="ttl">{esc(_report_display_text(_narrative_text(problem.get("title"))))}</span>{badge}'
                f'<span class="dhead-r">{when}{lost_pill}</span></div>'
                f'<div class="pfind">{_report_inline_math(_report_display_text(_narrative_text(problem.get("parent_summary")) or _narrative_text(problem.get("detail"))))}</div>'
                f'{_follow_up_action_html(suggestion)}'
                f'<div class="prel">{question_tags(qids)}</div></div>'
            )
        overlap_note = '<div class="note">题目有重叠，各项失分不能相加。</div>' if overlapping else ""
        card_d = f"""
<div class="card">
  <h2>这次暴露的问题<span class="aitag">{esc(AI_DISCLAIMER)}</span></h2>
  {''.join(dcards)}
  {overlap_note}
</div>"""
    else:
        lost = [r for r in student.records if r.lost]
        if not lost:
            inner = '<div class="dgreen">这次没有失分，保持现在的做题习惯。</div>'
        else:
            by_parent: dict[str, list[StudentQuestionRecord]] = {}
            for r in lost:
                by_parent.setdefault(parent_question_id(r.question_id), []).append(r)
            ordered = sorted(
                by_parent.items(),
                key=lambda kv: (
                    -sum(r.lost_points for r in kv[1]),
                    question_id_coordinates(kv[0]) or (10**9, 0),
                ),
            )
            cards = []
            for parent, recs in ordered[:3]:
                comments = []
                for r in recs:
                    text = _real_teacher_comment(r)
                    if text and text not in comments:
                        comments.append(text)
                lost_sum = sum(r.lost_points for r in recs)
                error_labels = [
                    error_label(r.question_id) for r in recs if error_label(r.question_id)
                ]
                qids = [r.question_id for r in recs]
                _, recurrence_rows = error_recurrence(qids)
                badge = '<span class="pmissed">以前也错过</span>' if recurrence_rows else ""
                evidence = []
                if error_labels:
                    evidence.append(
                        f'<div class="errline">错在：{esc("；".join(dict.fromkeys(error_labels)))}（AI 辅助）</div>')
                if comments:
                    evidence.append(
                        f'<div class="help">老师批语：{_report_inline_math("；".join(comments[:2]))}</div>')
                cards.append(
                    f'<div class="dcard"><div class="dhead"><span class="no">{len(cards) + 1}</span>'
                    f'<span class="ttl">{esc(_lost_group_label(parent, student, info_by_qid))}</span>{badge}'
                    f'<span class="dhead-r"><span class="plost">失 {fmt_num(lost_sum)} 分</span></span></div>'
                    f'<div class="pfind">{len(recs)}个小问有失分，先看原卷确认错在哪里。</div>'
                    + "".join(evidence)
                    + _follow_up_action_html(None)
                    + f'<div class="prel">{question_tags(qids)}</div></div>'
                )
            inner = "".join(cards)
        card_d = f"""
<div class="card">
  <h2>这次暴露的问题</h2>
  {'<div class="note">以下按失分排序；AI 跟进建议整理后显示。</div>' if pending_ai else ""}
  {inner}
</div>"""

    # ---- 学情肖像（问题卡之后、失分题附录之前；本学期有成绩的考试≥2场才显示） ----
    from backend.reporting.personal_portrait import (
        PORTRAIT_CSS, PORTRAIT_JS, build_portrait_view, portrait_card_html)
    try:
        card_portrait = portrait_card_html(
            build_portrait_view(data, student, history))
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "optional operation unavailable: portrait card (%s)", type(exc).__name__)
        card_portrait = ""

    # ---- F 附录 ----
    appendix = _lost_appendix_html(
        student, info_by_qid, analysis_by_qid, shots, error_map=error_map, review_links=review_links,
        step_diagnoses=step_diagnoses,
    )
    card_f = f'<div class="card report-print-only"><h2>失分题详解</h2>{appendix}</div>' if appendix else ""
    printed_parents = {parent_question_id(record.question_id) for record in student.records if record.lost}
    remaining_sources = {key: group for key, group in shots.items()
                         if not any(parent in printed_parents for parent in group["parents"])}

    released_note = '<div class="card">原卷已释放，无法显示作答图；分数与批语不受影响</div>' if originals_released else ""
    pending_ai_note = (
        '<div class="card"><div class="review-banner">AI 分析部分尚未整理，整理后自动显示；'
        '分数、名次与老师批语为当前数据。</div></div>'
        if pending_ai else ""
    )
    review_script = _PERSONAL_REVIEW_JS if review_links else ""
    keyboard_script = _PERSONAL_KEYBOARD_JS if online else ""
    time_label = 'AI 分析保存时间' if narrative is not None else '页面生成时间'
    generated_at = (str(narrative_saved_at or '未记录').replace('T', ' ')[:16]
                    if narrative is not None else datetime.now().strftime('%Y-%m-%d %H:%M'))
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(student.student_name)} · 家长报告 · {esc(data.session_name)}</title>
<style>{_PERSONAL_CSS}{PORTRAIT_CSS}</style>
{_report_math_assets()}
</head>
<body>
<div class="page">
{pending_ai_note}
{card_a}
{card_c}
{released_note}
{card_d}
{card_portrait}
{card_f}
<footer>
<p>标“{esc(AI_DISCLAIMER)}”的内容由 AI 辅助生成，有疑问请联系任课老师；班级对比只用匿名统计。{time_label}：{esc(generated_at)}。</p>
</footer>
</div>
{_shot_sources_html(remaining_sources)}
<dialog class="shot-viewer" id="report-shot-viewer">
<div class="shot-viewer-head"><span>原尺寸作答截图，可滚动查看</span><button type="button" data-close-shot>关闭截图</button></div>
<div class="shot-viewer-scroll"></div>
</dialog>
<script>{_PERSONAL_KATEX_JS}{_PERSONAL_GRID_JS}{PORTRAIT_JS}{_PERSONAL_PRINT_JS}{review_script}{keyboard_script}</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# 生成器与 preflight
# ---------------------------------------------------------------------------


def render_saved_narrative(narrative: dict[str, Any], *, title: str, owner: str,
                           session_name: str, generated_at: str | None = None) -> str:
    """没有当前成绩时展示保存的正文，保留旧正文中全部字段。"""
    labels = {'strengths': '已掌握的内容', 'problems': '需要关注', 'suggestions': '训练建议',
              'question_analyses': '逐题分析', 'key_findings': '主要发现', 'common_issues': '共性问题',
              'student_notes': '学生情况', 'grouping_advice': '分组建议', 'summary': '分析说明',
              'title': '主题', 'detail': '说明', 'feedback': '作答分析', 'question_id': '题号',
              'review_note': '建议核对', 'parent_summary': '学习情况', 'first_action': '先做什么',
              'completion_check': '完成要求', 'full_solution': '完整解答', 'solution_steps': '解题步骤',
              'parent_help': '家长协助', 'timeframe': '时间安排'}
    def content(value):
        if isinstance(value, dict):
            return ''.join(f'<section><h3>{esc(labels.get(key, key))}</h3>{content(item)}</section>'
                           for key, item in value.items())
        if isinstance(value, list):
            return '<ul>' + ''.join(f'<li>{content(item)}</li>' for item in value) + '</ul>'
        return _report_paragraphs(value)
    heading = f'{owner} · {title}'
    return (f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>{esc(heading)}</title>'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<style>body{font:16px/1.7 system-ui,sans-serif;max-width:900px;margin:32px auto;padding:0 24px;'
            'color:#24323a;overflow-wrap:anywhere}h1{font-size:26px}h3{font-size:17px}section{margin:18px 0}'
            'li{margin:10px 0}.note{color:#61717a;background:#f1f5f6;padding:12px}</style>'
            f'<body><h1>{esc(heading)}</h1><p>{esc(session_name)}</p>'
            f'<p class="note">已保存的正文。当前没有可展示的成绩，仍可查看这份内容。'
            f'生成时间：{esc(str(generated_at or "未记录"))}。</p>{content(narrative)}</body></html>')


class AnalysisReportGenerator:
    """契约与 ReportGenerator 一致：(repositories, output_dir) 构造，导出返回 Path。"""

    def __init__(
        self,
        db: GradingRepositoryAccess | Path,
        output_dir: Path,
        *,
        llm_client_factory: Callable[[], Any] | None = None,
        data_root: Path | None = None,
        reports_dir: Path | None = None,
    ) -> None:
        self.repositories = (
            open_grading_repositories(Path(db))
            if isinstance(db, Path)
            else as_grading_repositories(db)
        )
        self.db_path = self.repositories.db_path
        self.output_dir = Path(output_dir)
        self.llm_client_factory = llm_client_factory
        self.data_root = data_root or infer_data_root(self.db_path)
        # 受控 reports 目录：读取 .class_analysis 里的学生错因记录；缺省时不展示归类。
        self.reports_dir = Path(reports_dir) if reports_dir is not None else None
        self._llm_client: Any = None
        self._llm_client_resolved = False
        self._input_sampled_at: str | None = None
        # 最近一次个人报告导出写入集中提示清单后的条目总数（无 reports_dir 时为 0）。
        self.last_review_note_count = 0
        self.last_personal_summary = dict(generated=0, failed=0, skipped=0)
        self.last_personal_files = {}
        self.last_personal_missing = []

    def export_session(
        self,
        session_id: int,
        report_type: str,
        *,
        student_ids: set[int] | None = None,
        html_only: bool = False,
        narrative_mode: str = "generate",
        regenerate_existing: bool = False,
        progress_callback=None,
        cancel_check=None,
    ) -> Path:
        """默认返回下载 ZIP；html_only 返回只含各学生 HTML 的目录。"""
        if report_type not in ANALYSIS_REPORT_TYPES:
            raise ValueError(f"不支持的分析报告类型: {report_type}")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._input_sampled_at = datetime.now().isoformat(timespec="seconds")
        data = assemble_session_analysis(
            self.repositories,
            int(session_id),
            data_root=self.data_root,
        )
        return self._export_personal(data, student_ids=student_ids, html_only=html_only,
            narrative_mode=narrative_mode, regenerate_existing=regenerate_existing, progress_callback=progress_callback, cancel_check=cancel_check)

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
        prompt: str,
        max_tokens: int,
        image_blobs: list[bytes] | None = None,
    ) -> dict[str, Any] | None:
        """恰好调用 1 次模型；写回 PersonalReportStore 由调用方在主线程完成。"""
        client = self._client()
        if client is None:
            return None
        try:
            options = {"temperature": 0.3, "max_tokens": max_tokens}
            if image_blobs:
                narrative = client.json_from_images_once(
                    prompt, image_blobs,
                    use_config_client=True,
                    extra_kwargs=options,
                )
            else:
                narrative = client.json_from_text(prompt, extra_kwargs=options)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "optional operation unavailable: _narrative (%s)", type(exc).__name__,
            )
            # 模型超时/解析失败：本地修复仍失败则降级，不暗中重发（已确认偏差）。
            return None
        return narrative if isinstance(narrative, dict) else None

    def _export_saved_personal(self, data, *, student_ids=None, html_only=False,
                               progress_callback=None, cancel_check=None) -> Path:
        """导出保存正文，不依赖当前成绩完整性，也不解析模型配置。"""
        from backend.personal_reports import render_personal_report
        from backend.report_results import PersonalReportStore

        saved = PersonalReportStore(self.reports_dir).list(data.session_id) if self.reports_dir is not None else {}
        identities = {student.student_id: dict(student_name=student.student_name,
            student_code=student.student_code, class_name=student.class_name) for student in data.students}
        identities.update({int(item['student_id']): dict(item) for item in data.skipped})
        selected_ids = sorted(student_ids if student_ids is not None else set(identities) | set(saved))
        staging = self.output_dir / 'personal_reports'
        staging.mkdir(parents=True, exist_ok=True)
        self.last_personal_files = {}
        self.last_personal_missing = []
        self.last_personal_summary = dict(generated=0, failed=0, skipped=0)
        used_names = set()
        skipped_reasons = {int(item['student_id']): item['reason'] for item in data.skipped}
        for index, student_id in enumerate(selected_ids, 1):
            if cancel_check is not None:
                cancel_check()
            entry = saved.get(student_id) or {}
            identity = dict(entry.get('source') or {})
            identity.update(identities.get(student_id) or {})
            identity = dict(student_name=identity.get('student_name') or f'学生{student_id}',
                            student_code=identity.get('student_code') or str(student_id),
                            class_name=identity.get('class_name') or '未分班')
            if not isinstance(entry.get('narrative'), dict):
                self.last_personal_missing.append(dict(student_id=student_id, **identity, reason=entry.get('read_error') or skipped_reasons.get(student_id) or '未生成'))
                self.last_personal_summary['skipped'] += 1
            else:
                name = (f"{safe_filename_fragment(identity['student_code'], '学号')}_"
                        f"{safe_filename_fragment(identity['student_name'], '姓名')}_个人报告.html")
                if name in used_names:
                    name = name.replace('.html', f'_{student_id}.html')
                used_names.add(name)
                target = staging / name
                try:
                    html_text = render_personal_report(self.repositories, data.session_id, student_id,
                                                        self.reports_dir, online=False)
                    target.write_text(html_text, encoding='utf-8')
                    self.last_personal_files[student_id] = target
                    self.last_personal_summary['generated'] += 1
                except (OSError, ValueError) as error:
                    self.last_personal_missing.append(dict(student_id=student_id, **identity, reason='渲染失败'))
                    self.last_personal_summary['failed'] += 1
            if progress_callback is not None:
                progress_callback(index)
        if html_only:
            return staging
        checklist = staging / '未生成清单.txt'
        lines = [f"场次：{data.session_name}", '未生成个人报告的学生清单']
        lines.extend(f"{item['class_name']} {item['student_code']} {item['student_name']}：{item['reason']}"
                     for item in self.last_personal_missing)
        if not self.last_personal_missing:
            lines.append('无')
        checklist.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        target = self.output_dir / session_export_path_name(data.session_name, '个人分析报告', 'zip',
                    datetime.now().strftime('%Y%m%d_%H%M%S'))
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in [*self.last_personal_files.values(), checklist]:
                archive.write(path, arcname=path.name)
        return target

    def _export_personal(
        self,
        data: SessionAnalysisData,
        *,
        student_ids: set[int] | None = None,
        html_only: bool = False,
        narrative_mode: str = "generate",
        regenerate_existing: bool = False,
        progress_callback=None,
        cancel_check=None,
    ) -> Path:
        if narrative_mode == 'cache_only':
            return self._export_saved_personal(data, student_ids=student_ids, html_only=html_only,
                                              progress_callback=progress_callback, cancel_check=cancel_check)
        if not data.students:
            raise ValueError("该场次没有可生成个人报告的学生。")
        enrich_personal_questions(self.repositories, data, self.data_root)
        enrich_personal_knowledge(self.repositories, data, self.data_root, student_ids=student_ids)
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
        scoped_students = [
            (group, student)
            for group in split_session_analysis_by_class(data).values()
            for student in group.students
            if student_ids is None or student.student_id in student_ids
        ]
        if not scoped_students and narrative_mode != "cache_only":
            raise ValueError("所选学生没有可生成个人报告的成绩。")
        session_data = data
        bank_path = question_bank_db_path(Path(self.repositories.db_path))
        batch_student_ids = [student.student_id for _group, student in scoped_students]
        current_step_diagnosis = None
        step_diagnoses = {}
        if bank_path is not None:
            from backend.step_diagnosis import build_session_step_diagnosis
            current_step_diagnosis = build_session_step_diagnosis(
                self.repositories, data.session_id,
                student_ids=batch_student_ids,
                question_bank_path=bank_path,
            )
            step_diagnoses = current_step_diagnosis.students
        # Resolve once on the caller thread. Only model work runs in workers;
        # repository access, result-store writes, image preparation and rendering
        # stay on this thread.
        from backend.report_results import PersonalReportStore
        personal_store = PersonalReportStore(self.reports_dir) if self.reports_dir is not None else None
        def _stored_narrative(student: Any) -> dict[str, Any] | None:
            """只有明确需要更新时重做；旧分析方法不影响已有正文使用。"""
            if personal_store is None or regenerate_existing:
                return None
            entry = personal_store.load(data.session_id, student.student_id)
            narrative = entry.get("narrative") if isinstance(entry, dict) else None
            if not isinstance(narrative, dict):
                return None
            return narrative

        cached_by_student = {
            student.student_id: _stored_narrative(student)
            for _group, student in scoped_students
        }
        blocked_read_errors = {}
        if personal_store is not None and not regenerate_existing:
            for _group, student in scoped_students:
                saved = personal_store.load(data.session_id, student.student_id) or {}
                if saved.get('read_error') and not isinstance(saved.get('narrative'), dict):
                    blocked_read_errors[student.student_id] = saved['read_error']
            scoped_students = [(group, student) for group, student in scoped_students
                               if student.student_id not in blocked_read_errors]
        self.last_personal_files = {}
        self.last_personal_missing = [dict(item) for item in data.skipped
            if student_ids is None or int(item["student_id"]) in student_ids]
        self.last_personal_missing.extend(dict(student_id=student_id, student_name=f'学生{student_id}',
            student_code=str(student_id), class_name='未分班', reason=read_error)
            for student_id, read_error in blocked_read_errors.items())
        self.last_personal_summary = dict(generated=0, failed=0, skipped=len(self.last_personal_missing))
        all_cached = all(cached_by_student[student.student_id] is not None for _g, student in scoped_students)
        client = None if all_cached else self._client()
        # 同教学学期历次成绩每次导出只读取一轮，渲染时按学生取用。
        histories = _load_student_histories(
            self.repositories, session_data, self.data_root
        )
        # "建议核对"提示集中存档：与教师复核锁的修订号对账，导出后可在
        # 成绩中心集中查看并跳转复核。
        note_info_by_qid = {info.question_id: info for info in session_data.questions}
        lock_revisions: dict[tuple[int, str], int] = {}
        if self.reports_dir is not None:
            for lock in self.repositories.reviews.list_teacher_score_locks(
                session_data.session_id
            ):
                key = (
                    int(lock.get("student_id") or 0),
                    str(lock.get("question_id") or ""),
                )
                lock_revisions[key] = max(
                    lock_revisions.get(key, 0), int(lock.get("revision") or 0)
                )
        review_note_items: list[dict[str, Any]] = []
        updated_student_ids: set[int] = set()
        # 错因整理产物：本场学生×题记录 + 历次同类/同错法场次索引。
        # 未整理的题不返回记录，报告相应位置不显示错误类型。
        error_state: dict[str, Any] = {}
        error_sources: list[dict[str, Any]] = []
        history_error_index: dict[int, Any] = {}
        if self.reports_dir is not None:
            from backend.class_analysis import (
                ClassAnalysisStateStore,
                build_cause_inputs,
                student_error_map,
            )

            store = ClassAnalysisStateStore(self.reports_dir)
            error_state = store.load(data.session_id) or {}
            error_sources = build_cause_inputs(data)
            history_error_index = _load_personal_error_histories(
                self.repositories, histories, data.session_id, self.reports_dir, self.data_root,
            )
            error_maps = {
                student.student_id: student_error_map(error_state, student, error_sources, data)
                for _group, student in scoped_students
            }
        else:
            error_maps = {}
        execution = getattr(getattr(client, "config_gateway", None), "execution_snapshot", None)
        # 并发数跟随 AI 服务设置页的并发配置（自动=20、自定义=N、保守=1）；
        # 网关仍按实际限速节流。
        parallel_limit = max(1, int(getattr(execution, "max_in_flight", 1)))

        def prepare(executor, data, student):
            paper_context = _student_paper_context(self.repositories, data, student)
            shots = capture_lost_question_shots(
                self.repositories, data, student, regions=regions,
                data_root=self.data_root, paper_context=paper_context,
            )
            cached = cached_by_student[student.student_id]
            images, image_map = ([], []) if cached is not None else _personal_image_inputs(
                data, student, shots, paper_context, self.data_root,
            )
            payload = build_personal_payload(data, student)
            payload["image_map"] = image_map
            payload["material_notes"] = student.material_notes
            future = executor.submit(lambda value=cached: value) if cached is not None else executor.submit(
                self._narrative,
                prompt=build_report_prompt(PERSONAL_SYSTEM_PROMPT, payload),
                max_tokens=personal_output_token_limit(sum(record.lost for record in student.records)),
                image_blobs=images,
            )
            filename = (
                f"{safe_filename_fragment(student.student_code, '未知学号')}"
                f"_{safe_filename_fragment(student.student_name, '未知姓名')}"
                "_个人报告.html"
            )
            if filename in used_names:
                filename = filename.replace(".html", f"_{student.student_id}.html")
            used_names.add(filename)
            report_path = staging_subdir / filename
            report_files.append(report_path)
            return future, (data, student, shots, report_path, cached is not None)

        students = iter(scoped_students)
        with ThreadPoolExecutor(
            max_workers=parallel_limit, thread_name_prefix="personal-report"
        ) as executor:
            pending = {}
            while True:
                # Bound prepared images as well as in-flight model requests.
                while len(pending) < parallel_limit:
                    if cancel_check is not None:
                        cancel_check()
                    entry = next(students, None)
                    if entry is None:
                        break
                    future, context = prepare(executor, *entry)
                    pending[future] = context
                if not pending:
                    break
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in completed:
                    data, student, shots, report_path, from_store = pending.pop(future)
                    history_index = history_error_index.get(student.student_id)
                    error_history = history_index or None
                    narrative = future.result()
                    attempt_failed = narrative is None and not from_store
                    if attempt_failed and personal_store is not None:
                        previous = personal_store.load(data.session_id, student.student_id) or {}
                        if isinstance(previous.get('narrative'), dict):
                            narrative = previous['narrative']
                            from_store = True
                    if (
                        personal_store is not None
                        and narrative_mode == "generate"
                        and narrative is not None
                        and not from_store
                    ):
                        personal_store.save(
                            data.session_id,
                            student.student_id,
                            narrative=narrative,
                            source=dict(session_name=data.session_name, student_name=student.student_name,
                                        student_code=student.student_code, class_name=student.class_name),
                            generated_at=self._input_sampled_at,
                        )
                    if self.reports_dir is not None and narrative_mode == "generate" and not from_store and narrative is not None:
                        updated_student_ids.add(student.student_id)
                        review_note_items.extend(
                            _collect_review_notes(
                                narrative,
                                student,
                                note_info_by_qid,
                                lock_revisions,
                            )
                        )
                    from backend.files.session_originals import originals_state
                    try:
                        display_shots = capture_lost_question_shots(
                            self.repositories, data, student, regions=regions,
                            data_root=self.data_root, include_full=True,
                        )
                        html_text = _render_personal_html(
                            data,
                            student,
                            narrative,
                            display_shots,
                            history=histories.get(student.student_id, []),
                            error_map=error_maps.get(student.student_id),
                            error_history=error_history,
                            step_diagnoses=step_diagnoses.get(student.student_id),
                            originals_released=self.data_root is not None and originals_state(self.data_root, data.session_id) in {"clearing", "cleared"},
                            narrative_saved_at=(personal_store.load(data.session_id, student.student_id) or {}).get("generated_at") if personal_store is not None else None,
                        )
                        report_path.write_text(html_text, encoding="utf-8")
                        self.last_personal_files[student.student_id] = report_path
                        self.last_personal_summary["generated" if narrative is not None and not attempt_failed else "failed"] += 1
                    except Exception:
                        self.last_personal_summary["failed"] += 1
                        self.last_personal_missing.append(dict(student_id=student.student_id, student_name=student.student_name,
                            student_code=student.student_code, class_name=student.class_name, reason="渲染失败"))
                        report_files.remove(report_path)
                    if progress_callback is not None:
                        progress_callback(len(self.last_personal_files) + self.last_personal_summary["failed"])
                    if cancel_check is not None:
                        cancel_check()

        data = session_data

        if self.reports_dir is not None and narrative_mode == 'generate':
            if updated_student_ids:
                self.last_review_note_count = merge_session_notes(self.reports_dir, data.session_id,
                    items=review_note_items, scoped_student_ids=updated_student_ids)
            else:
                self.last_review_note_count = len((load_session_notes(self.reports_dir, data.session_id) or {}).get('items') or [])

        if html_only:
            return staging_subdir

        checklist_lines = [
            "未生成个人报告的学生清单",
            f"场次：{data.session_name}",
            "",
        ]
        if self.last_personal_missing:
            checklist_lines.extend(
                f"{item['class_name']} {item['student_code']} {item['student_name']}：{item['reason']}"
                for item in self.last_personal_missing
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


def plan_cause_calls(
    db: GradingRepositoryAccess | Any,
    session_id: int,
    *,
    reports_dir: Path,
    retry_failed: bool,
    regenerate_existing: bool = False,
) -> dict[str, int]:
    """错因整理的工作量预估，与 run_cause_analysis 同口径的跳题/重试规则。

    retry_failed=False 对应导出前置阶段与自动管线：失败题不重发；
    已保存的整理结果照常复用，不按输入内容或分析方法变化重做。
    统一错法名的小调用只在存在模型调用时出现（fresh 只来自模型结果）；
    只要有一题需要发模型就按 +1 计入 call_count。
    """
    from backend.class_analysis import (
        CAUSE_ANALYSIS_PROMPT,
        ClassAnalysisStateStore,
        assemble_cause_data,
        build_cause_inputs,
        known_cause_patterns,
        plan_cause_question,
    )
    from backend.error_patterns import (
        OPTION_ANALYSIS_PROMPT,
        bank_confirmed_triggers,
        build_option_analysis_input,
        session_bank_context,
    )

    repositories = as_grading_repositories(db)
    store = ClassAnalysisStateStore(Path(reports_dir))
    stored = (
        (store.load(int(session_id)) or {}).get("cause_analysis") or {}
    ).get("questions") or {}
    cause_data = assemble_cause_data(repositories, int(session_id))
    sources = build_cause_inputs(
        cause_data,
        known_patterns=known_cause_patterns(
            store, question_bank_db_path(repositories.db_path), int(session_id),
        ),
    )
    qb_path = question_bank_db_path(repositories.db_path)
    option_scope = True
    # 成绩导入考试：预估口径与执行一致，只保留选择题选项路径。
    from backend.score_import.context import is_import_session_data_root

    data_root = infer_data_root(repositories.db_path)
    option_only = is_import_session_data_root(data_root, session_id)
    bank_context = session_bank_context(qb_path, int(session_id))
    confirmed_by_bank = bank_confirmed_triggers(
        qb_path,
        sorted({bid for ctx in bank_context.values() for bid in ctx["bank_ids"]}),
    )
    qtypes = {info.question_id: str(info.question_type or "") for info in cause_data.questions}
    pending_questions = 0
    call_count = 0
    fresh_name_call_possible = False
    estimated_tokens = 0
    for source in sources:
        saved = stored.get(source['question_id']) or {}
        has_result = isinstance(saved.get('result'), dict)
        if has_result and not regenerate_existing:
            continue
        if not retry_failed and saved.get('failed'):
            continue
        plan = plan_cause_question(
            store, int(session_id), source,
            qtype=qtypes.get(source["question_id"], ""),
            option_scope=option_scope,
            ctx=bank_context.get(parent_question_id(source["question_id"])) or {},
            confirmed_by_bank=confirmed_by_bank,
            question_bank_path=qb_path,
            retry_failed=retry_failed,
            regenerate_existing=regenerate_existing,
            option_only=option_only,
        )
        if plan["path"] == "skip":
            continue
        pending_questions += 1
        if not plan["needs_call"]:
            continue
        call_count += 1
        # 名称归并调用只发生在产生新错法名称的 v3/fill_v3 路径之后；
        # 选项路径不产生新名称，不触发归并调用。
        if plan["path"] in {"v3", "fill_v3"}:
            fresh_name_call_possible = True
        if plan["path"] == "option":
            option = plan["option"]
            estimated_tokens += (
                estimate_prompt_tokens(
                    OPTION_ANALYSIS_PROMPT + "\n" + json.dumps(
                        build_option_analysis_input(
                            option["text"], option["correct"],
                            str(source.get("reference_analysis") or "")),
                        ensure_ascii=False)
                ) + 8000
            )
        else:
            estimated_tokens += (
                estimate_prompt_tokens(
                    CAUSE_ANALYSIS_PROMPT + "\n" + json.dumps(source, ensure_ascii=False)
                ) + 12000
            )
    return {
        "total_questions": len(sources),
        "pending_questions": pending_questions,
        "call_count": call_count + (1 if fresh_name_call_possible else 0),
        "estimated_tokens": estimated_tokens,
    }


def build_analysis_preflight(
    db: GradingRepositoryAccess | Any,
    session_id: int,
    report_type: str,
    *,
    reports_dir: Path,
    student_ids: set[int] | None = None,
    include_causes: bool = True,
    regenerate_existing: bool = False,
) -> dict[str, Any]:
    """生成前的费用与调用预估：错因整理与报告叙述分开计数；只给 token 粗估。

    include_causes=False 时跳过错因整理预估（调用方已单独计算），
    cause_* 字段按 0 返回。cache_hits 统计已保存结果；明确选择重新生成时不复用。
    """
    if report_type not in ANALYSIS_REPORT_TYPES:
        raise ValueError(f"不支持的分析报告类型: {report_type}")
    from backend.report_results import PersonalReportStore

    repositories = as_grading_repositories(db)
    data = assemble_session_analysis(repositories, int(session_id))
    enrich_personal_questions(repositories, data, None)
    personal_store = PersonalReportStore(reports_dir)
    entries = [
        (
            student.student_id,
            build_report_prompt(
                PERSONAL_SYSTEM_PROMPT,
                build_personal_payload(data, student),
            ),
            personal_output_token_limit(sum(record.lost for record in student.records)),
        )
        for data in split_session_analysis_by_class(data).values()
        for student in data.students
        if student_ids is None or student.student_id in student_ids
    ]

    cache_hits = 0
    blocked_read_errors = 0
    estimated_tokens = 0
    for sid, prompt, max_tokens in entries:
        entry = personal_store.load(int(session_id), sid)
        hit = not regenerate_existing and isinstance((entry or {}).get('narrative'), dict)
        if hit:
            cache_hits += 1
            continue
        if not regenerate_existing and (entry or {}).get('read_error'):
            blocked_read_errors += 1
            continue
        # 文本粗估 = 输入 token（字符数/1.5）+ 输出上限；图片计费由模型决定。
        estimated_tokens += estimate_prompt_tokens(prompt) + max_tokens

    # 错因整理的工作量与实际任务同口径（见 plan_cause_calls）；报告导出自身
    # 不再执行整理，这里只展示手动补齐所需的预估。
    cause_call_count = 0
    cause_total_questions = 0
    cause_estimated_tokens = 0
    if include_causes and report_type == PERSONAL_ANALYSIS_REPORT_TYPE:
        cause_plan = plan_cause_calls(
            repositories,
            int(session_id),
            reports_dir=Path(reports_dir),
            retry_failed=False,
            regenerate_existing=regenerate_existing,
        )
        cause_call_count = cause_plan["call_count"]
        cause_total_questions = cause_plan["total_questions"]
        cause_estimated_tokens = cause_plan["estimated_tokens"]

    configured = resolve_content_generation_settings() is not None
    service_name, model_name = content_generation_public_info()
    return {
        "report_type": report_type,
        "configured": configured,
        "service_name": service_name if configured else None,
        "model_name": model_name if configured else None,
        "call_count": len(entries) - cache_hits - blocked_read_errors,
        "estimated_total_tokens": estimated_tokens,
        "cache_hits": cache_hits,
        "cause_call_count": cause_call_count,
        "cause_total_questions": cause_total_questions,
        "cause_estimated_tokens": cause_estimated_tokens,
    }
