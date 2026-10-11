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
    BAND_CUTOFFS,
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
) -> list[dict[str, Any]]:
    """为丢分大题生成全部截图；同题多图以序号区分，缺图不阻断报告。"""
    if not regions:
        return []
    lost_records = [record for record in student.records if record.lost]
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



def capture_lost_question_shots(repositories, data, student, *, regions, data_root, paper_context=None):
    from backend.files.session_originals import originals_state
    if data_root is not None and originals_state(data_root, data.session_id) in {"clearing", "cleared"}:
        return {}
    shots = {}
    used_bytes = 0
    for spec in lost_question_shot_specs(repositories, data, student, regions=regions,
                                       data_root=data_root, paper_context=paper_context):
        data_uri = _crop_region_data_uri(spec["image_path"], spec["region"])
        if data_uri is None:
            continue
        encoded_size = len(data_uri) * 3 // 4
        if used_bytes + encoded_size > _SHOT_BUDGET_BYTES:
            continue
        used_bytes += encoded_size
        shots[spec["key"]] = {k: spec[k] for k in ("parent_question_id", "region_question_id", "caption")}
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
.score-line .big{font-size:46px;font-weight:800;line-height:1}
.score-line .of{font-size:16px;color:var(--muted)}
.stat3{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px}
.stat3 .cell{border:1px solid var(--line);border-radius:10px;padding:9px 10px;text-align:center;min-width:0}
.stat3 .v{font-size:16px;font-weight:700;white-space:nowrap}
.stat3 .l{font-size:11.5px;color:var(--muted);white-space:nowrap}
.stat3 .change .l{white-space:normal}
.stat3 .change .v{white-space:normal}
.change .up{color:var(--good)} .change .down{color:var(--bad)}
.change .rank-change{font-size:12px;margin-top:2px}
.change .rank-context{font-size:10px;color:var(--muted);margin-top:2px}
.bands{margin:6px 0 12px}
.band{display:grid;grid-template-columns:96px 1fr 44px;align-items:center;gap:8px;
font-size:12px;color:var(--muted);padding:2px 0}
.band .track{height:12px;background:var(--gray-soft);border-radius:6px;overflow:hidden}
.band .track i{display:block;height:100%;background:#b9c6d4;border-radius:6px}
.band.me{color:var(--ink);font-weight:600}
.band.me .track i{background:var(--accent)}
.band .cnt{text-align:right}
.me-tag{display:inline-block;font-size:11px;color:var(--accent);margin-left:6px}
.concl{font-size:14.5px;margin-top:6px}
.strength{color:var(--good);font-size:13.5px;margin-top:6px}
/* B 历次成绩 */
.hchart{position:relative;height:140px;margin:4px 0 2px}
.hchart svg{position:absolute;inset:0;width:100%;height:100%}
.hdot{position:absolute;width:9px;height:9px;border-radius:50%;background:var(--accent);
border:2px solid #fff;transform:translate(-50%,-50%);box-shadow:0 0 0 1px var(--line)}
.hdot.cur{width:11px;height:11px}
.hval{position:absolute;transform:translate(-50%,-135%);font-size:12px;color:var(--ink);white-space:nowrap}
.hval.cur{font-weight:700}
.hcols{display:flex;margin-top:6px}
.hcol{flex:1;min-width:0;text-align:center;font-size:12px;color:var(--muted);line-height:1.5}
.hcol .hn{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hcol.cur{color:var(--ink);font-weight:700}
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
.legend{font-size:12px;color:var(--muted);margin-top:10px}
#qpanel{margin-top:10px}
.qdetail-box{border:1px solid var(--line);border-radius:10px;padding:14px 16px;background:#fcfdfe}
.qdetail-box .qd-head{font-size:14px;font-weight:700}
.qdetail-box .qd-points{font-size:13px;color:var(--muted);margin-top:4px}
.qdetail-box .qd-points .pt-tag{display:inline-block;background:var(--gray-soft);border-radius:6px;
padding:0 8px;margin:2px 4px 2px 0;font-size:12px;color:var(--ink)}
.qd-fb{font-size:13.5px;margin-top:8px}
.errline{font-size:12.5px;color:var(--warn);margin-top:5px}
.qmore-btn{margin-top:10px;border:1px solid var(--accent);color:var(--accent);background:#fff;
border-radius:8px;padding:5px 14px;font-size:13px;cursor:pointer;font-family:inherit}
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
/* D 跟进卡片 */
.dcard{border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:12px}
.dcard .dhead{display:flex;align-items:center;gap:9px}
.dcard .no{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;
border-radius:50%;background:var(--accent);color:#fff;font-size:12px;font-weight:700;flex:none}
.dcard .ttl{font-size:14.5px;font-weight:700;min-width:0;overflow-wrap:anywhere}
.dcard .meta2{font-size:12.5px;color:var(--muted);margin-top:5px}
.dcard .qtag{display:inline-block;background:var(--gray-soft);border-radius:6px;padding:0 7px;
margin-left:5px;font-size:11.5px;color:var(--ink);border:0;font-family:inherit;cursor:pointer}
.dcard .qtag:hover,.dcard .qtag:focus-visible{outline:1px solid var(--accent)}
.dcard .help{font-size:13.5px;margin-top:7px}
.dcard .when{font-size:11.5px;font-weight:400;color:var(--accent);margin-left:auto;white-space:nowrap}
.history-evidence{font-size:12.5px;color:var(--muted);margin-top:7px}
.history-evidence ul{padding-left:18px}
.recur{display:inline-block;font-size:11px;color:var(--warn);background:var(--warn-soft);
border:1px solid #ecd9b0;border-radius:999px;padding:0 8px;margin-left:8px;vertical-align:1px}
.dcard details{margin-top:9px;border-top:1px dashed var(--line);padding-top:8px}
.dcard summary{cursor:pointer;font-size:12.5px;color:var(--accent)}
.dgreen{background:var(--good-soft);border:1px solid #bfe3d2;border-radius:10px;
padding:14px 16px;color:var(--good);font-size:14px}
/* E 考查点 */
.pt-counts{font-size:13.5px;margin-bottom:10px}
.pt-counts b{margin-right:14px}
.pt-counts .g{color:var(--good)} .pt-counts .m{color:var(--warn)} .pt-counts .b{color:var(--bad)}
.pt-counts .u{color:var(--muted)}
.pt-row{display:flex;align-items:flex-start;gap:8px;padding:7px 0;border-top:1px solid var(--line);
font-size:13.5px;flex-wrap:wrap}
.pt-row .nm{min-width:0}
.pt-row .dots{display:inline-flex;gap:7px;margin-left:auto;flex-wrap:wrap}
.dot{display:inline-flex;flex-direction:column;align-items:center;width:30px}
.dot i{width:18px;height:18px;border-radius:50%;display:flex;align-items:center;justify-content:center;
font-size:11px;font-style:normal;line-height:1}
.dot s{text-decoration:none;font-size:9px;color:var(--muted);margin-top:1px}
.dot i.full{background:var(--good-soft);color:var(--good);border:1px solid #bfe3d2}
.dot i.part{background:var(--warn-soft);color:var(--warn);border:1px solid #f0d9ab}
.dot i.zero{background:var(--bad-soft);color:var(--bad);border:1px solid #eec6c0}
.dot i.unknown{background:var(--gray-soft);color:var(--muted);border:1px dashed #c2cbd6}
.pt-row .unconfirmed{font-size:11.5px;color:var(--muted);display:block}
.pt-good-head{color:var(--good);font-size:13.5px;font-weight:600;margin-top:10px}
.pt-good-head small{color:var(--muted);font-size:12px;font-weight:400;margin-left:6px}
details.pt-unknown{margin-top:8px}
details.pt-unknown>summary{cursor:pointer;font-size:12.5px;color:var(--accent)}
details.pt-unknown .note{margin:4px 0 6px}
.tagcloud{margin-top:8px}
.tagcloud .sec{font-size:12px;color:var(--muted);margin:6px 0 3px}
.tagcloud .tg{display:inline-block;background:var(--good-soft);color:var(--good);border-radius:6px;
padding:1px 9px;margin:2px 5px 2px 0;font-size:12.5px}
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
@media(max-width:560px){
 body{font-size:14px}
 .page{padding:10px 8px 28px}
 .card{padding:16px 14px}
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
 #qpanel{display:none!important}
 .qcell{outline:none!important}
 .shot img{max-height:95mm;width:auto;object-fit:contain}
 .dcard .shot{display:none} /* 同一作答图在失分题附录完整打印，避免跟进卡重复占页。 */
 .qpart,.shot,.dcard .help,.dcard .qd-fb,.qgrid,.pt-row,footer{break-inside:avoid}
 .dcard{break-inside:auto}
 .card h2,.dcard .dhead,.qcard .head,.qpart h3{break-after:avoid}
 p,dd,li{orphans:3;widows:3}
}
"""

# C 节方格 → 原位详情面板（<template> 片段克隆，不滚动、不跳转）。
_PERSONAL_GRID_JS = """
(function(){
  var panel = document.getElementById('qpanel');
  var openQ = null;
  function renderMath(root){
    root.querySelectorAll('.qm[data-latex]').forEach(function(el){
      try{ katex.render(el.dataset.latex, el, {throwOnError:true, output:'htmlAndMathml', displayMode:el.dataset.display === 'true'}); }
      catch(e){ el.classList.add('qm-error'); el.title='公式格式需核对，已保留原文'; }
    });
  }
  if (!panel) return;
  function showQuestion(q, toggle){
    var btn = Array.from(document.querySelectorAll('.qcell')).find(function(b){return b.dataset.q === q;});
    if(!btn) return false;
    if(toggle && openQ === q){
      panel.hidden = true; panel.innerHTML=''; openQ = null;
      btn.setAttribute('aria-expanded','false'); return false;
    }
    var tpl = Array.from(document.querySelectorAll('template[data-q]')).find(function(t){return t.dataset.q === q;});
    if(!tpl) return false;
    panel.innerHTML=''; panel.appendChild(tpl.content.cloneNode(true));
    panel.hidden=false; openQ=q; renderMath(panel);
    document.querySelectorAll('.qcell[aria-expanded="true"]').forEach(function(b){b.setAttribute('aria-expanded','false');});
    btn.setAttribute('aria-expanded','true');
    return true;
  }
  document.querySelectorAll('.qcell').forEach(function(btn){
    btn.addEventListener('click', function(){
      showQuestion(btn.dataset.q, true);
    });
  });
  document.querySelectorAll('[data-open-q]').forEach(function(btn){
    btn.addEventListener('click', function(){
      if(showQuestion(btn.dataset.openQ, false)){
        panel.focus({preventScroll:true});
        panel.scrollIntoView({block:'start',behavior:'smooth'});
      }
    });
  });
  panel.addEventListener('click', function(e){
    var t = e.target.closest('.qmore-btn');
    if(!t) return;
    var m = panel.querySelector('.qmore');
    if(!m) return;
    m.hidden = !m.hidden;
    t.textContent = m.hidden ? '看原卷和解法' : '收起原卷和解法';
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


def _question_analysis_html(item: dict[str, Any] | None) -> str:
    if not item:
        return _ai_block("", failed=True)
    feedback = _narrative_text(item.get("feedback")) or "\n".join(dict.fromkeys(
        text for key in ("observation", "possible_cause", "analysis")
        if (text := _narrative_text(item.get(key)))
    ))
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


def _score_card_conclusion(
    student: StudentReportData,
    info_by_qid: dict[str, QuestionInfo],
) -> str:
    scored = [r for r in student.records if r.max_score > 0]
    n = len(scored)
    full = sum(1 for r in scored if r.score >= r.max_score)
    lost = [r for r in scored if r.lost]
    if not lost:
        return f"全部 {n} 个小题都拿到满分。"
    by_parent: dict[str, float] = {}
    for r in lost:
        parent = parent_question_id(r.question_id)
        by_parent[parent] = by_parent.get(parent, 0.0) + r.lost_points
    ordered = sorted(
        by_parent.items(),
        key=lambda kv: (-kv[1], question_id_coordinates(kv[0]) or (10**9, 0)),
    )
    total_lost = sum(by_parent.values())
    if len(ordered) == 1:
        return (
            f"{n} 个小题中 {full} 个满分，丢的 {fmt_num(total_lost)} 分都在"
            f"{_lost_group_label(ordered[0][0], student, info_by_qid)}。"
        )
    (g1, x), (g2, y) = ordered[0], ordered[1]
    return (
        f"{n} 个小题中 {full} 个满分，丢分最多的是"
        f"{_lost_group_label(g1, student, info_by_qid)}（{fmt_num(x)}分）和"
        f"{_lost_group_label(g2, student, info_by_qid)}（{fmt_num(y)}分）。"
    )


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


def _history_chart_html(entries: list[dict[str, Any]], current_sid: int) -> str:
    """历次成绩：SVG 只画折线（不缩放描边），圆点/分数/场次名用 HTML 等宽列。"""
    n = len(entries)

    def x_of(i: int) -> float:
        return (i + 0.5) / n * 100

    def y_of(ratio: float) -> float:
        return 16 + (1 - min(1.0, max(0.0, ratio))) * 76  # 顶部留分数值空间，纵轴固定 0..满分

    avg_pts: list[tuple[float, float]] = []
    me_segs: list[list[tuple[float, float]]] = []
    cur_seg: list[tuple[float, float]] = []
    dots: list[tuple[int, float, float, float]] = []
    for i, e in enumerate(entries):
        full = e.get("full") or 100
        avg = e.get("avg")
        if avg is not None:
            avg_pts.append((x_of(i), y_of(float(avg) / full)))
        if e.get("score") is not None:
            cur_seg.append((x_of(i), y_of(float(e["score"]) / full)))
            dots.append((i, x_of(i), y_of(float(e["score"]) / full), float(e["score"])))
        else:
            if len(cur_seg) > 1:
                me_segs.append(cur_seg)
            cur_seg = []
    if len(cur_seg) > 1:
        me_segs.append(cur_seg)

    svg = ['<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">']
    if len(avg_pts) > 1:
        pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in avg_pts)
        svg.append(
            f'<polyline points="{pts}" fill="none" stroke="#8a97a8" stroke-width="1.5" '
            f'stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>'
        )
    for seg in me_segs:
        pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in seg)
        svg.append(
            f'<polyline points="{pts}" fill="none" stroke="#2b6cb0" stroke-width="2" '
            f'vector-effect="non-scaling-stroke"/>'
        )
    svg.append("</svg>")

    overlays = []
    for i, x, y, score in dots:
        cur = entries[i]["session_id"] == current_sid
        overlays.append(
            f'<span class="hdot{" cur" if cur else ""}" style="left:{x:.2f}%;top:{y:.2f}%"></span>'
            f'<span class="hval{" cur" if cur else ""}" style="left:{x:.2f}%;top:{y:.2f}%">{esc(fmt_num(score))}</span>'
        )

    cols = []
    for e in entries:
        name = str(e.get("name") or "")
        short = name if len(name) <= 6 else name[:6] + "…"
        rank = e.get("rank")
        rank_text = f"第{rank}名" if e.get("score") is not None and rank else (
            "有成绩" if e.get("score") is not None else "未参加"
        )
        cur = e["session_id"] == current_sid
        cols.append(
            f'<div class="hcol{" cur" if cur else ""}">'
            f'<div class="hn">{esc(short)}</div><div>{esc(rank_text)}</div></div>'
        )
    return (
        '<div class="hchart">'
        + "".join(svg)
        + "".join(overlays)
        + '</div><div class="hcols">'
        + "".join(cols)
        + "</div>"
    )


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
  if (!['Escape', 'ArrowLeft', 'ArrowRight'].includes(event.key)) return;
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


def _shots_for_parent(shots: dict[str, dict[str, str]], parent: str) -> list[dict[str, str]]:
    return [
        shot
        for key, shot in shots.items()
        if (shot.get("parent_question_id") or key) == parent
    ]


def _shots_html(shots: dict[str, dict[str, str]], parent: str, title: str) -> str:
    return "".join(
        f'<div class="shot"><img src="{esc(shot.get("src") or shot["data_uri"])}" alt="{esc(title)}作答截图">'
        f'<div class="cap">{esc(shot.get("caption") or "学生作答（原卷截图）")}</div></div>'
        for shot in _shots_for_parent(shots, parent)
    )


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
) -> str:
    """AI 叙述项为空或没有可展示内容时退化为老师批语/参考答案，不显示生成失败提示。"""
    if analysis:
        rendered = _question_analysis_html(analysis)
        if AI_FAILED_NOTE not in rendered:
            return rendered
    return _grading_fallback_html(
        record, info, show_answer=show_answer, show_comment=show_comment
    )


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


def _error_label_text(category: Any, pattern: Any) -> str:
    """大类 · 错法名；错法名与大类相同或以大类开头时只显示错法名。"""
    cat, pat = str(category or ""), str(pattern or "")
    if cat and pat and pat.startswith(cat):
        return pat
    return " · ".join(part for part in (cat, pat) if part)


def _question_detail_template(
    record: StudentQuestionRecord,
    info: QuestionInfo | None,
    points_labels: list[str],
    analysis: dict[str, Any] | None,
    shots: dict[str, dict[str, str]],
    errors: list[dict[str, Any]] | None = None,
    review_links: bool = False,
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
    if record.lost and errors:
        labels = "；".join(dict.fromkeys(
            _error_label_text(row.get("category"), row.get("pattern"))
            for row in errors
        ))
        if labels:
            body.append(
                f'<div class="errline">错误归类（AI 辅助）：{esc(labels)}</div>'
            )
    if points_labels:
        tags = "".join(f'<span class="pt-tag">{esc(t)}</span>' for t in points_labels)
        body.append(f'<div class="qd-points">考这些：{tags}</div>')
    if record.lost:
        feedback = _narrative_text((analysis or {}).get("feedback"))
        if analysis and feedback:
            body.append(f'<div class="qd-fb">{_report_paragraphs(feedback)}</div>')
        more = ['<div class="qmore" hidden>']
        fallback = (
            (info.question_text or info.stem_summary) if info is not None else ""
        ) or title
        parent_title = _question_display_label(parent)
        more.append(_stem_block_html(info, fallback, parent_title))
        more.append(_shots_html(shots, parent, parent_title))
        kv = []
        if record.student_answer:
            kv.append(f"<dt>学生作答</dt><dd>{_report_inline_math(record.student_answer)}</dd>")
        elif _personal_cell_status(record) == "blank":
            kv.append("<dt>学生作答</dt><dd>未作答</dd>")
        if kv:
            more.append(f'<dl class="kv">{"".join(kv)}</dl>')
        more.append(_analysis_or_fallback_html(record, info, analysis))
        more.append("</div>")
        body.append('<button type="button" class="qmore-btn">看原卷和解法</button>')
        body.append("".join(more))
    body.append("</div>")
    return "".join(body)


def _step_diagnosis_html(diagnosis: dict[str, Any] | None) -> str:
    if not diagnosis:
        return ""
    from backend.step_diagnosis import STEP_STATE_LABELS, STEP_SOURCE_LABELS
    if diagnosis.get("availability") == "teacher_total_only":
        return "<dt>卡在哪一步</dt><dd>缺少与教师最终分相符的步骤记录，暂不能定位失分步骤。</dd>"
    if diagnosis.get("availability") != "available":
        return "<dt>卡在哪一步</dt><dd>未保存逐步记录，暂不能定位失分步骤。</dd>"
    rows = []
    for step in diagnosis.get("steps") or []:
        if step["state"] == "met":
            continue
        names = "、".join(target["name"] for target in step.get("key_steps") or []) or ("" if diagnosis.get("scope") == "point_text" else "未挂关键步骤")
        text = " · ".join(value for value in (STEP_STATE_LABELS[step["state"]], step["step_id"], step["goal"], names) if value)
        reason = f'<div class="note">阅卷说明：{_report_inline_math(step["reason"])}</div>' if step.get("reason") else ""
        rows.append(f'<div>{_report_inline_math(text)}{reason}</div>')
    completed = "、".join(step["step_id"] for step in diagnosis.get("steps") or [] if step["state"] == "met")
    if completed:
        rows.append(f'<div class="note">已完成：{esc(completed)}</div>')
    source = STEP_SOURCE_LABELS[diagnosis["source"]]
    return f'<dt>卡在哪一步</dt><dd><div class="note">{esc(source)}</div>{"".join(rows)}</dd>'


def _lost_appendix_html(
    student: StudentReportData,
    info_by_qid: dict[str, QuestionInfo],
    analysis_by_qid: dict[str, dict[str, Any]],
    shots: dict[str, dict[str, str]],
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
        fallback = ((info.question_text or info.stem_summary) if info is not None else "") or title
        parts = [
            f'<div class="qcard"><div class="head"><b>{esc(title)}'
            f'{(" · " + esc(type_label)) if type_label else ""}</b>'
            f'<span class="score">得 {fmt_num(gscore)} 分 / 满分 {fmt_num(gmax)} 分</span></div>',
            _personal_review_link(parent) if review_links else "",
            _stem_block_html(info, fallback, title),
            _shots_html(shots, parent, title),
        ]
        for record in sorted(recs, key=lambda r: question_id_coordinates(r.question_id) or (10**9, 0)):
            part_info = info_by_qid.get(record.question_id)
            analysis = analysis_by_qid.get(record.question_id)
            if (
                analysis
                and analysis.get("solution_source") == "reference"
                and not (part_info and part_info.reference_analysis)
            ):
                analysis = {**analysis, "solution_source": "ai"}
            kv = [_step_diagnosis_html((step_diagnoses or {}).get(record.question_id))]
            if record.student_answer:
                kv.append(f"<dt>学生作答</dt><dd>{_report_inline_math(record.student_answer)}</dd>")
            elif _personal_cell_status(record) == "blank":
                kv.append("<dt>学生作答</dt><dd>未作答</dd>")
            answer = part_info.canonical_answer if part_info is not None else ""
            if answer and not (analysis and analysis.get("solution_steps")):
                kv.append(f'<dt>标准答案</dt><dd><b class="ans">{_report_inline_math(answer)}</b></dd>')
            teacher_comment = _real_teacher_comment(record)
            if teacher_comment:
                kv.append(f"<dt>老师批语</dt><dd>{_report_inline_math(teacher_comment)}</dd>")
            error_rows = (error_map or {}).get(record.question_id) or []
            error_text = "；".join(dict.fromkeys(
                _error_label_text(row.get("category"), row.get("pattern"))
                for row in error_rows
            ))
            if error_text:
                kv.append(f"<dt>错误归类</dt><dd>{esc(error_text)}（AI 辅助）</dd>")
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


def _follow_up_action_html(suggestion: dict[str, Any] | None) -> str:
    suggestion = suggestion or {}
    action = _narrative_text(suggestion.get("first_action")) or _narrative_text(suggestion.get("detail")) or "先看相关题，问清错在哪里，再订正。"
    check = _narrative_text(suggestion.get("completion_check")) or "不看解析，能说明订正理由并独立重做；仍有疑问的题请老师核对。"
    parts = [
        f'<div class="help"><b>先做：</b>{_report_inline_math(_report_display_text(action))}</div>',
        f'<div class="help"><b>检查：</b>{_report_inline_math(_report_display_text(check))}</div>',
    ]
    parent_help = _narrative_text(suggestion.get("parent_help"))
    if parent_help:
        parts.append(f'<div class="note">家长可以这样帮：{_report_inline_math(_report_display_text(parent_help))}</div>')
    return "".join(parts)


def _recurrence_evidence_html(rows: list[Any], label: str) -> str:
    items = []
    for row in rows:
        if isinstance(row, str):  # 兼容仅有场次名的已有调用。
            text = row
        elif isinstance(row, dict):
            text = " · ".join(
                part for part in (
                    str(row.get("session_name") or ""),
                    _question_display_label(str(row["question_id"])) if row.get("question_id") else "",
                    str(row.get("pattern") or row.get("category") or ""),
                ) if part
            )
        else:
            continue
        item = f'<li>{esc(text)}</li>'
        if text and item not in items:
            items.append(item)
    if not items:
        return ""
    note = '<p>仅匹配错误大类，不能据此认定是同一错法。</p>' if label == "同类问题曾出现" else ""
    return f'<div class="history-evidence"><b>{esc(label)}的依据</b>{note}<ul>{"".join(items)}</ul></div>'


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
    shots: dict[str, dict[str, str]],
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
    analysis_by_qid = _narrative_analysis_index(narrative, info_by_qid)
    history = [e for e in (history or []) if isinstance(e, dict)]
    error_map = error_map or {}
    error_history = error_history or {}

    def question_errors(question_id: str) -> list[dict[str, Any]]:
        return error_map.get(question_id) or []

    def error_label(question_id: str) -> str:
        """本题错误归类文案：大类 · 典型错法；多条用「；」连接。"""
        parts = []
        for row in question_errors(question_id):
            text = _error_label_text(row.get("category"), row.get("pattern"))
            if text and text not in parts:
                parts.append(text)
        return "；".join(parts)

    def error_recurrence(question_ids: list[str]) -> tuple[str, list[Any]]:
        """同错法与同大类分别表达；优先展示更具体的同错法证据。"""
        matches: dict[str, list[Any]] = {"patterns": [], "categories": []}
        for qid in question_ids:
            for row in question_errors(qid):
                for key, value in (("categories", row.get("category")), ("patterns", row.get("pattern"))):
                    if not value or (key == "patterns" and value == row.get("category")):
                        continue
                    for evidence in (error_history.get(key) or {}).get(str(value), []):
                        if evidence and evidence not in matches[key]:
                            matches[key].append(evidence)
        if matches["patterns"]:
            return "同一错法再次出现", matches["patterns"]
        if matches["categories"]:
            return "同类问题曾出现", matches["categories"]
        return "", []

    def question_tags(question_ids: list[str]) -> str:
        tags = []
        for qid in sorted(question_ids, key=lambda q: question_id_coordinates(q) or (10**9, 0)):
            label = _question_display_label(qid)
            tags.append(f'<button type="button" class="qtag" data-open-q="{esc(qid)}" aria-label="查看{esc(label)}的作答证据">{esc(label)}</button>')
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
    comparison = '<div class="v">暂无对比</div><div class="l">尚无上次成绩</div>'
    prior = [
        e
        for e in history
        if e["session_id"] != data.session_id and e.get("score") is not None
    ]
    if prior:
        prev = prior[-1]
        if prev.get("full") == data.full_score and data.full_score > 0:
            delta = student.student_score - float(prev["score"])
            direction = "up" if delta > 0 else "down" if delta < 0 else ""
            score_change = ("+" if delta > 0 else "−" if delta < 0 else "") + fmt_num(abs(delta)) + "分"
            if delta == 0:
                score_change = "分数持平"
            comparison = f'<div class="l">较上次</div><div class="v {direction}">{esc(score_change)}</div>'
        else:
            comparison = '<div class="l">较上次</div><div class="v">分数不直接比</div><div class="l">满分不同或缺失</div>'
        if prev.get("rank") and student.rank:
            delta_rank = int(prev["rank"]) - student.rank
            rank_change = f"名次上升{delta_rank}名" if delta_rank > 0 else f"名次下降{abs(delta_rank)}名" if delta_rank < 0 else "名次持平"
            comparison += f'<div class="rank-change">{esc(rank_change)}</div>'
            if prev.get("present") and prev["present"] != data.present:
                comparison += f'<div class="rank-context">参考人数{prev["present"]}→{data.present}</div>'
        else:
            comparison += '<div class="rank-change">名次暂无对比</div>'
    avg = data.stats.get("avg")
    avg_text = f"{float(avg):.1f}" if avg is not None else "—"
    bands = list(data.stats.get("bands") or [])
    scale = (data.full_score / 100) if data.full_score > 0 else 1.0
    my_band = None
    for i, cutoff in enumerate(BAND_CUTOFFS):
        lower = cutoff * scale
        upper = data.full_score if i == 0 else BAND_CUTOFFS[i - 1] * scale
        if lower <= student.student_score <= upper if i == 0 else lower <= student.student_score < upper:
            my_band = len(bands) - 1 - i
            break
    max_count = max((b["count"] for b in bands), default=1) or 1
    band_rows = []
    for disp_i, band in enumerate(reversed(bands)):  # 低分段在上
        width = band["count"] / max_count * 100
        me = disp_i == my_band
        index = len(bands) - 1 - disp_i
        lower = BAND_CUTOFFS[index] * scale
        upper = data.full_score if index == 0 else BAND_CUTOFFS[index - 1] * scale
        band_label = f"{fmt_num(lower)}≤分数{'≤' if index == 0 else '＜'}{fmt_num(upper)}"
        band_rows.append(
            f'<div class="band{" me" if me else ""}"><span>{esc(band_label)}'
            f'{"<span class=me-tag>孩子在这里</span>" if me else ""}</span>'
            f'<div class="track"><i style="width:{width:.0f}%"></i></div>'
            f'<span class="cnt">{band["count"]}人</span></div>'
        )
    review = (
        '<div class="review-banner">有题目等待老师复核，分数可能微调</div>'
        if student.needs_review
        else ""
    )
    strength = ""
    strengths = [
        _narrative_text(i)
        for i in _narrative_items(narrative, "strengths")
        if _narrative_text(i)
    ]
    if strengths:
        strength = f'<div class="strength">✓ 值得肯定：{_report_inline_math(strengths[0])}</div>'
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
  <div class="score-line"><span class="big">{fmt_num(student.student_score)}</span><span class="of">/ {fmt_num(data.full_score)}</span></div>
  <div class="stat3">
    <div class="cell"><div class="v">{student.rank}/{data.present}</div><div class="l">班级名次</div></div>
    <div class="cell"><div class="v">{avg_text}</div><div class="l">班级平均</div></div>
    <div class="cell change">{comparison}</div>
  </div>
  <div class="bands">{''.join(band_rows)}</div>
  {small_sample_note}
  <div class="concl">{esc(_score_card_conclusion(student, info_by_qid))}</div>
  {strength}
</div>"""

    # ---- B 历次成绩 ----
    card_b = ""
    scored_history = [e for e in history if e.get("score") is not None]
    if len(scored_history) >= 2:
        card_b = f"""
<div class="card">
  <h2>历次成绩</h2>
  <div class="hist">{_history_chart_html(history, data.session_id)}</div>
  <div class="note">实线：孩子的分数；虚线：班级平均。试卷难度和参考人数可能不同，进退仅作参考。</div>
</div>"""

    # ---- C 答题一览 ----
    scored_records = [r for r in student.records if r.max_score > 0]
    counts = {"full": 0, "part": 0, "zero": 0, "blank": 0}
    cells, templates = [], []
    for record in scored_records:
        info = info_by_qid.get(record.question_id)
        status = _personal_cell_status(record)
        counts[status] = counts.get(status, 0) + 1
        sym = {"full": "✓", "part": "△", "zero": "✗", "blank": "—"}[status]
        hard = info is not None and info.class_rate is not None and info.class_rate < 0.5
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
            )
            + "</template>"
        )
    card_c = f"""
<div class="card">
  <h2>本卷答题一览</h2>
  <div class="qgrid">{''.join(cells)}</div>
  <div class="legend">✓ 满分 {counts['full']}　△ 部分得分 {counts['part']}　✗ 零分 {counts['zero']}　— 未作答 {counts['blank']}　▲ 全班平均得分不到一半</div>
  {''.join(f'<div class="note">{esc(note)}</div>' for note in student.material_notes)}
   <div id="qpanel" tabindex="-1" hidden></div>
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
            if qids and lost_sum > 0:
                meta_line = f'<div class="meta2">关联题目失分 {fmt_num(lost_sum)} 分{question_tags(qids)}</div>'
            else:
                meta_line = ""
            recurrence_rows: list[Any] = []
            recurrence_label, error_recur = error_recurrence(qids)
            for q in qids:
                for p in points_by_qid.get(q, []):
                    for row in p["recur"]:
                        if row not in recurrence_rows:
                            recurrence_rows.append(row)
            if error_recur:
                recurrence_rows = error_recur
            elif recurrence_rows:
                recurrence_label = "该考查点曾失分"
            badge = f'<span class="recur">{esc(recurrence_label)}</span>' if recurrence_rows else ""
            why = [f'<div class="qd-fb">{_report_paragraphs(_narrative_text(problem.get("detail")))}</div>']
            full_action = _narrative_text((suggestion or {}).get("detail"))
            if _narrative_text((suggestion or {}).get("first_action")) and full_action:
                why.append(f'<div class="help"><b>具体做法：</b>{_report_inline_math(_report_display_text(full_action))}</div>')
            if recurrence_rows:
                why.append(_recurrence_evidence_html(recurrence_rows, recurrence_label))
            shown_parents = set()
            for q in qids:
                item = analysis_by_qid.get(q)
                fb = _narrative_text((item or {}).get("feedback"))
                if fb:
                    why.append(
                        f'<div class="qd-fb"><b>{esc(_question_display_label(q))}</b>'
                        f'{_report_paragraphs(fb)}</div>'
                    )
                elif item is None:
                    rec = next((r for r in student.records if r.question_id == q), None)
                    if rec is not None:
                        fallback = _grading_fallback_html(
                            rec, info_by_qid.get(q), heading=_question_display_label(q)
                        )
                        if fallback:
                            why.append(fallback)
                parent = parent_question_id(q)
                if parent not in shown_parents:
                    shown_parents.add(parent)
                    first = next(iter(_shots_for_parent(shots, parent)), None)
                    if first:
                        why.append(
                            f'<div class="shot"><img src="{esc(first.get("src") or first["data_uri"])}" alt="作答截图">'
                            f'<div class="cap">{esc(first.get("caption") or "学生作答（原卷截图）")}</div></div>'
                        )
            timeframe = _narrative_text((suggestion or {}).get("timeframe"))
            when = f'<span class="when">{esc(timeframe)}</span>' if timeframe in {"本周", "长期"} else ""
            dcards.append(
                f'<div class="dcard"><div class="dhead"><span class="no">{len(dcards) + 1}</span>'
                f'<span class="ttl">{esc(_report_display_text(_narrative_text(problem.get("title"))))}</span>{when}</div>'
                f'<div class="help"><b>发现：</b>{_report_inline_math(_report_display_text(_narrative_text(problem.get("parent_summary")) or _narrative_text(problem.get("detail"))))}</div>'
                f'{_follow_up_action_html(suggestion)}'
                f'<details><summary>看具体题目{f" · {len(qids)}个小问" if qids else ""}</summary>{meta_line}{badge}{"".join(why)}</details></div>'
            )
        overlap_note = '<div class="note">同一道题可能涉及多个问题，各项关联失分不能相加，也不表示某个问题单独造成全部失分。</div>' if overlapping else ""
        card_d = f"""
<div class="card">
  <h2>这次重点跟进<span class="aitag">{esc(AI_DISCLAIMER)}</span></h2>
  {overlap_note}
  {''.join(dcards)}
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
                recurrence_label, recurrence_rows = error_recurrence(qids)
                badge = (
                    f'<span class="recur">{esc(recurrence_label)}</span>'
                    if recurrence_rows
                    else ""
                )
                cards.append(
                    f'<div class="dcard"><div class="dhead"><span class="ttl">'
                    f'{esc(_lost_group_label(parent, student, info_by_qid))}</span></div>'
                    f'<div class="help"><b>发现：</b>{len(recs)}个小问有失分，先看原卷确认错在哪里。</div>'
                    + _follow_up_action_html(None)
                    + f'<details><summary>看具体题目 · {len(recs)}个小问</summary>'
                    + f'<div class="meta2">关联题目失分 {fmt_num(lost_sum)} 分{question_tags(qids)}</div>{badge}'
                    + (
                        f'<div class="errline">错误归类（AI 辅助）：{esc("；".join(dict.fromkeys(error_labels)))}</div>'
                        if error_labels
                        else ""
                    )
                    + (
                        f'<div class="help">老师批语：{_report_inline_math("；".join(comments[:2]))}</div>'
                        if comments
                        else ""
                    )
                    + (
                        _recurrence_evidence_html(recurrence_rows, recurrence_label)
                        if recurrence_rows else ""
                    )
                    + "</details></div>"
                )
            inner = "".join(cards)
        card_d = f"""
<div class="card">
  <h2>这次重点跟进</h2>
  {'<div class="note">以下按失分排序；AI 跟进建议整理后显示。</div>' if pending_ai else ""}
  {inner}
</div>"""

    # ---- E 考查点 ----
    n_good = sum(1 for p in points if p["status"] == "good")
    n_mid = sum(1 for p in points if p["status"] == "mid")
    n_bad = sum(1 for p in points if p["status"] == "bad")
    n_unknown = sum(1 for p in points if p["status"] == "unknown")

    def _point_row(p: dict[str, Any]) -> str:
        sym_map = {"full": "✓", "part": "△", "zero": "✗", "unknown": "?"}
        dots = "".join(
            f'<span class="dot"><i class="{r}">{sym_map[r]}</i>'
            f'<s>{esc(_question_cell_label(q["id"]))}</s></span>'
            for q, r in p["results"]
        )
        uncertainty = '<small class="unconfirmed">部分题有失分，这个知识点是否做对还不能判断</small>' if p["status"] == "unknown" else ""
        recurrence = (
            '<details class="history-evidence"><summary>该考查点曾失分</summary>'
            + _recurrence_evidence_html(p["recur"], "该考查点曾失分") + '</details>'
            if p["recur"] else ""
        )
        return (
            f'<div class="pt-row"><span class="nm">{esc(p["label"])}{uncertainty}{recurrence}</span>'
            f'<span class="dots">{dots}</span></div>'
        )

    rows_html = [
        _point_row(p) for status in ("bad", "mid")
        for p in points if p["status"] == status
    ]
    unknown_rows = [_point_row(p) for p in points if p["status"] == "unknown"]
    good_sections: dict[str, list[str]] = {}
    for p in points:
        if p["status"] == "good":
            good_sections.setdefault(p["section"] or "其他", []).append(p["label"])

    def _section_key(name: str) -> tuple[int, int]:
        num = re.match(r"\s*(\d+)", name)
        return (0, int(num.group(1))) if num else (1, 0)

    cloud = "".join(
        f'<div class="sec">{esc(sec)}</div><div>'
        + "".join(f'<span class="tg">{esc(label)}</span>' for label in labels)
        + "</div>"
        for sec, labels in sorted(good_sections.items(), key=lambda kv: _section_key(kv[0]))
    )
    card_e = ""
    if points:
        target_label = {'type': '题型', 'mixed': '训练目标'}.get(data.knowledge_structure.get('target_kind'), '知识点')
        score_note = ('这里只反映本次考试表现；题型下列出的分数是相关题目的整体得分。'
                      if target_label == '题型' else '这里只反映本次考试表现；同一道题可能同时考几个知识点。')
        good_head = (f'<div class="pt-good-head">这次做得好（{n_good} 项）'
                     f'<small>相关题目这次都做对了</small></div>' if cloud else "")
        unknown_details = ""
        if unknown_rows:
            unknown_details = (
                f'<details class="pt-unknown"><summary>暂不能判断的失分点'
                f'（{n_unknown} 项）</summary>'
                f'<div class="note">灰色“？”表示这道题有失分，但现有记录还不能确定这个{target_label}是否做对；'
                f'不代表孩子一定不会，也不需要家长逐项确认。可先看“这次重点跟进”了解具体问题。</div>'
                f'{"".join(unknown_rows)}</details>')
        card_e = f"""
<div class="card">
  <h2>本次考查点</h2>
  <div class="pt-counts"><b class="g">做得好 {n_good}</b><b class="m">部分做到 {n_mid}</b><b class="b">需要补 {n_bad}</b><b class="u">暂不能判断 {n_unknown}</b></div>
  {good_head}
  {f'<div class="tagcloud">{cloud}</div>' if cloud else ''}
  {''.join(rows_html)}
  {unknown_details}
  <div class="note" style="margin-top:8px">{score_note}</div>
</div>"""

    # ---- 学情肖像（本次考查点之后、失分题附录之前） ----
    from backend.reporting.personal_portrait import (
        PORTRAIT_CSS, build_portrait_view, portrait_card_html)
    try:
        card_portrait = portrait_card_html(
            build_portrait_view(data, student, history, error_map, error_history))
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "optional operation unavailable: portrait card (%s)", type(exc).__name__)
        card_portrait = ""

    # ---- F 附录 ----
    appendix = _lost_appendix_html(
        student, info_by_qid, analysis_by_qid, shots, error_map=error_map, review_links=review_links,
        step_diagnoses=step_diagnoses,
    )
    card_f = f'<div class="card">{appendix}</div>' if appendix else ""

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
{card_b}
{card_c}
{released_note}
{card_d}
{card_e}
{card_portrait}
{card_f}
<footer>
<p>本报告根据阅卷记录自动生成；标“{esc(AI_DISCLAIMER)}”的内容由人工智能辅助生成，仅供参考，如有疑问请联系任课老师。</p>
<p>班级对比只用匿名统计。{time_label}：{esc(generated_at)}。</p>
</footer>
</div>
<script>{_PERSONAL_KATEX_JS}{_PERSONAL_GRID_JS}{_PERSONAL_PRINT_JS}{review_script}{keyboard_script}</script>
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
        step_diagnoses = {}
        if bank_path is not None:
            from backend.step_diagnosis import build_session_step_diagnosis
            step_diagnoses = build_session_step_diagnosis(
                self.repositories, data.session_id,
                student_ids=[student.student_id for _group, student in scoped_students],
                question_bank_path=bank_path,
            ).students
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
                        html_text = _render_personal_html(
                            data,
                            student,
                            narrative,
                            shots,
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
    待整理题目不少于 2 时，整理完成后还有一次统一错法名的小调用，
    预估按 +1 计入 call_count。
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
    bank_context = session_bank_context(qb_path, int(session_id))
    confirmed_by_bank = bank_confirmed_triggers(
        qb_path,
        sorted({bid for ctx in bank_context.values() for bid in ctx["bank_ids"]}),
    )
    qtypes = {info.question_id: str(info.question_type or "") for info in cause_data.questions}
    pending_questions = 0
    call_count = 0
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
        )
        if plan["path"] == "skip":
            continue
        pending_questions += 1
        if not plan["needs_call"]:
            continue
        call_count += 1
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
        "call_count": call_count + (1 if pending_questions >= 2 else 0),
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
