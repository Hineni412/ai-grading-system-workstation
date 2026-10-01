from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from annotation_renderer import render_annotated_paper
from answer_region_geometry import answer_regions_with_template_source_sizes
from backend.repositories.access import GradingRepositoryAccess, as_grading_repositories
from export_names import safe_filename_fragment, session_export_path_name
from image_preprocessor import _enhanced_name
from path_manager import resolve_stored_file_path
from question_id_contract import (
    QuestionIdCatalog,
    canonical_parent_id,
    question_id_coordinates,
)


class PrintableScoreContractError(ValueError):
    """Raised when stored grading details cannot become one parent score each."""


_PRINTED_QUESTION_NUMBER = re.compile(
    r"^\s*(?:第\s*)?(?:Q\s*)?(\d{1,3})\s*(?:题|[.．、:：])",
    re.IGNORECASE,
)


class OriginalPaperExporter:
    def __init__(
        self,
        db: GradingRepositoryAccess,
        output_dir: Path,
    ) -> None:
        self.db = as_grading_repositories(db)
        self.output_dir = output_dir

    def export_session_originals(self, session_id: int) -> Path:
        from session_originals import require_original_pages
        require_original_pages(self._data_root(), session_id)
        results = self.db.results.get_session_results(session_id)
        if not results:
            raise ValueError("当前考试批改暂无结果，无法导出原卷。")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        session = self.db.sessions.get_grading_session(session_id)
        session_name = session.get("session_name") if session else f"考试批改_{session_id}"
        work_dir = self.output_dir / f"{safe_filename_fragment(session_name, '考试批改')}_批注原卷页面_{ts}"
        work_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = self.output_dir / session_export_path_name(session_name, "批注原卷", "pdf", ts)

        regions = answer_regions_with_template_source_sizes(
            self.db,
            session_id,
            data_root=self._data_root(),
        )
        rubric = self._load_rubric(session_id)
        enhanced_path_map = self._load_enhanced_path_map(session_id)

        sorted_results = sorted(
            results,
            key=lambda item: (
                str(item.get("student_code") or ""),
                str(item.get("student_name") or ""),
                int(item.get("result_id") or 0),
            ),
        )
        prepared_results: list[tuple[dict[str, Any], dict[str, dict[str, float]]]] = []
        expected_parent_ids: list[str] = []
        for result in sorted_results:
            result_id = int(result["result_id"])
            details = self.db.results.get_result_details(result_id)
            question_scores = aggregate_parent_question_scores(details, rubric)
            prepared_results.append((result, question_scores))
            for question_id in question_scores:
                if question_id not in expected_parent_ids:
                    expected_parent_ids.append(question_id)

        question_anchors = detect_printed_question_anchors(
            self._anchor_page_paths(session_id, sorted_results),
            expected_parent_ids,
        )

        page_images: list[Path] = []
        for index, (result, question_scores) in enumerate(prepared_results, start=1):
            student_code = _safe_name(str(result.get("student_code") or f"S{index:03d}"))
            student_name = _safe_name(str(result.get("student_name") or "unknown"))
            prefix = f"{index:03d}_{student_code}_{student_name}"

            front_out = work_dir / f"{prefix}_01_front.jpg"
            back_out = work_dir / f"{prefix}_02_back.jpg"
            front_image = self._preferred_annotation_image(Path(str(result["front_image"])), enhanced_path_map)
            back_image = self._preferred_annotation_image(Path(str(result["back_image"])), enhanced_path_map)
            render_annotated_paper(
                front_image=front_image,
                back_image=back_image,
                regions=regions,
                question_scores=question_scores,
                question_anchors=question_anchors,
                output_front=front_out,
                output_back=back_out,
                annotate_only_deductions=False,
                annotation_layout="question_score_boxes",
            )
            page_images.extend([front_out, back_out])

        _save_images_as_pdf(page_images, pdf_path)

        return pdf_path

    def _load_rubric(self, session_id: int) -> dict[str, Any]:
        session = self.db.sessions.get_grading_session(session_id)
        if not session:
            raise PrintableScoreContractError("当前考试不存在，无法读取评分依据。")
        rubric_path = self._resolve_stored_file_path(session.get("rubric_path"))
        if not rubric_path.exists():
            raise PrintableScoreContractError("当前考试缺少评分依据，无法汇总原卷得分。")

        try:
            rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise PrintableScoreContractError(
                "评分依据无法读取，不能可靠汇总原卷得分。"
            ) from exc
        if not isinstance(rubric, dict) or not isinstance(rubric.get("questions"), list):
            raise PrintableScoreContractError("评分依据缺少题目列表，无法汇总原卷得分。")
        return rubric

    def _anchor_page_paths(
        self,
        session_id: int,
        results: list[dict[str, Any]],
    ) -> dict[str, Path]:
        paths: dict[str, Path] = {}
        template = self.db.templates.get_session_template(session_id)
        if template:
            for page in ("front", "back"):
                path_value = template.get(f"{page}_template_path")
                if not path_value:
                    continue
                path = self._resolve_stored_file_path(path_value)
                if path.exists():
                    paths[page] = path

        if results:
            first = results[0]
            for page in ("front", "back"):
                if page in paths:
                    continue
                path_value = first.get(f"{page}_image")
                if not path_value:
                    continue
                path = self._resolve_stored_file_path(path_value)
                if path.exists():
                    paths[page] = path
        return paths

    def _resolve_stored_file_path(self, path_value: object) -> Path:
        return resolve_stored_file_path(path_value, data_root=self._data_root())

    def _data_root(self) -> Path | None:
        return self.db.db_path.parent.parent if self.db.db_path.parent.name == "databases" else None

    def _load_enhanced_path_map(self, session_id: int) -> dict[str, Path]:
        data_dir = self.output_dir.parent
        session_template_dir = data_dir / "templates" / f"session_{session_id}"
        candidates = [
            session_template_dir / "scan_analysis_latest.json",
            *sorted(session_template_dir.glob("scan_analysis_*.json"), reverse=True),
        ]
        path_map: dict[str, Path] = {}
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                continue
            for section in ("groups", "issues"):
                items = payload.get(section)
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    self._add_enhanced_pair(path_map, item.get("front_image"), item.get("enhanced_front_image"))
                    self._add_enhanced_pair(path_map, item.get("back_image"), item.get("enhanced_back_image"))
            if path_map:
                break
        return path_map

    def _add_enhanced_pair(self, path_map: dict[str, Path], original: Any, enhanced: Any) -> None:
        if not original or not enhanced:
            return
        original_path = self._resolve_stored_file_path(original)
        enhanced_path = self._resolve_stored_file_path(enhanced)
        if enhanced_path.exists():
            path_map[str(Path(str(original)))] = enhanced_path
            path_map[str(original_path)] = enhanced_path

    def _preferred_annotation_image(self, image_path: Path, enhanced_path_map: dict[str, Path]) -> Path:
        mapped = enhanced_path_map.get(str(image_path))
        if mapped and mapped.exists():
            return mapped
        resolved_image_path = self._resolve_stored_file_path(image_path)
        mapped = enhanced_path_map.get(str(resolved_image_path))
        if mapped and mapped.exists():
            return mapped

        for enhanced_dir in self._candidate_enhanced_dirs(resolved_image_path):
            candidate = enhanced_dir / _enhanced_name(resolved_image_path)
            if candidate.exists():
                return candidate
        return resolved_image_path

    @staticmethod
    def _candidate_enhanced_dirs(image_path: Path) -> list[Path]:
        candidates = [image_path.parent / "_enhanced"]
        current = image_path.parent
        for _ in range(4):
            candidates.append(current / "_enhanced")
            candidates.append(current.parent / "_enhanced")
            current = current.parent
        unique: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key not in seen:
                unique.append(candidate)
                seen.add(key)
        return unique


def aggregate_parent_question_scores(
    details: list[dict[str, Any]],
    rubric: dict[str, Any],
) -> dict[str, dict[str, float]]:
    """Normalize stored aliases and emit one score-only item per scored parent."""

    try:
        catalog = QuestionIdCatalog.from_document(rubric)
    except Exception as exc:
        raise PrintableScoreContractError(
            "评分依据中的题号无法形成统一编号，不能可靠导出得分框。"
        ) from exc

    parent_full_scores: dict[str, float] = {}
    formal_children: dict[str, set[str]] = {}
    questions = rubric.get("questions")
    if not isinstance(questions, list):
        raise PrintableScoreContractError("评分依据缺少题目列表，无法汇总原卷得分。")

    for question in questions:
        if not isinstance(question, dict):
            continue
        parent_id = canonical_parent_id(question.get("question_id"))
        if parent_id is None or parent_id not in catalog.parent_ids:
            continue
        parts = question.get("parts")
        part_scores: list[float] = []
        child_ids: set[str] = set()
        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict):
                    continue
                resolved = catalog.resolve(
                    part.get("part_id"),
                    parent_id=parent_id,
                )
                if resolved is None:
                    continue
                child_ids.add(resolved)
                part_scores.append(_score_number(part.get("part_score"), default=0.0))
        formal_children[parent_id] = {
            child_id for child_id in child_ids if child_id != parent_id
        }
        explicit_max = _score_number(question.get("max_score"), default=0.0)
        parent_full_scores[parent_id] = explicit_max or sum(part_scores)

    resolved_details: dict[str, dict[str, Any]] = {}
    unresolved: list[str] = []
    duplicate: list[str] = []
    for detail in details:
        if detail.get("score_awarded") is None:
            continue
        raw_question_id = str(detail.get("question_id") or "").strip()
        resolved = catalog.resolve(raw_question_id)
        if resolved is None:
            unresolved.append(raw_question_id or "（空题号）")
            continue
        if resolved in resolved_details:
            duplicate.append(raw_question_id or resolved)
            continue
        resolved_details[resolved] = detail

    if unresolved:
        raise PrintableScoreContractError(
            "以下已评分题号无法对应当前评分依据，已停止导出以避免漏标："
            + ", ".join(unresolved)
        )
    if duplicate:
        raise PrintableScoreContractError(
            "以下题号归一后出现重复评分，已停止导出以避免重复计分："
            + ", ".join(duplicate)
        )

    scores: dict[str, dict[str, float]] = {}
    for parent_id in catalog.parent_ids:
        child_ids = formal_children.get(parent_id, set())
        scored_children = [
            resolved_details[child_id]
            for child_id in catalog.parts_by_parent.get(parent_id, ())
            if child_id in child_ids and child_id in resolved_details
        ]
        parent_detail = resolved_details.get(parent_id)
        selected = scored_children or ([parent_detail] if parent_detail is not None else [])
        if not selected:
            continue
        scores[parent_id] = {
            "score_awarded": round(
                sum(_score_number(item.get("score_awarded")) for item in selected),
                4,
            ),
            "max_score": round(parent_full_scores.get(parent_id, 0.0), 4),
        }
    return scores


def detect_printed_question_anchors(
    page_paths: dict[str, Path],
    expected_question_ids: list[str],
    *,
    ocr_engine: Any | None = None,
) -> dict[str, dict[str, Any]]:
    """Locate printed parent question numbers once on the confirmed template."""

    expected_parents = {
        parent_id
        for value in expected_question_ids
        if (coordinates := question_id_coordinates(value)) is not None
        and (parent_id := f"Q{coordinates[0]}")
    }
    if not expected_parents or not page_paths:
        return {}

    if ocr_engine is None:
        try:
            from local_ocr import get_local_ocr

            ocr_engine = get_local_ocr()
        except Exception:
            return {}

    candidates: dict[str, list[dict[str, Any]]] = {}
    for page in ("front", "back"):
        path = page_paths.get(page)
        if path is None or not path.exists():
            continue
        try:
            with Image.open(path) as source:
                rgb = source.convert("RGB")
                import numpy as np

                image_array = np.asarray(rgb)[:, :, ::-1]
            lines = read_ocr_lines(image_array, page=page, ocr_engine=ocr_engine)
        except Exception:
            continue
        for candidate in printed_question_candidates(lines, expected_parents):
            candidates.setdefault(candidate["question_id"], []).append(
                {key: value for key, value in candidate.items() if key != "question_id"}
            )

    anchors: dict[str, dict[str, Any]] = {}
    for question_id, items in candidates.items():
        anchors[question_id] = min(
            items,
            key=lambda item: (
                int(item["x"]),
                -float(item["confidence"]),
                1 if item["page"] == "back" else 0,
                int(item["y"]),
            ),
        )
    return anchors


def read_ocr_lines(
    image_array: Any,
    *,
    page: str,
    ocr_engine: Any,
    source_size: tuple[int, int] | None = None,
    offset: tuple[int, int] = (0, 0),
) -> list[dict[str, Any]]:
    """Read all text lines, translating crop coordinates to the source page."""
    height, width = image_array.shape[:2]
    source_width, source_height = source_size or (width, height)
    raw = ocr_engine(image_array)
    rows = raw[0] if isinstance(raw, tuple) else raw
    lines = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        try:
            bounds = _ocr_box_bounds(item[0], width=width, height=height)
        except (TypeError, ValueError, OverflowError):
            continue
        if bounds is None:
            continue
        x, y, w, h = bounds
        lines.append({
            "page": page, "x": x + offset[0], "y": y + offset[1], "w": w, "h": h,
            "text": str(item[1] or ""), "confidence": _score_number(item[2]),
            "source_width": source_width, "source_height": source_height,
        })
    return lines


def printed_question_candidates(
    lines: list[dict[str, Any]], expected_parents: set[str],
) -> list[dict[str, Any]]:
    """Keep every plausible printed number; callers choose their own anchors."""
    candidates = []
    for line in lines:
        match = _PRINTED_QUESTION_NUMBER.match(line["text"])
        if match is None or not line["confidence"] >= 0.55:
            continue
        question_id = f"Q{int(match.group(1))}"
        if question_id in expected_parents:
            candidates.append({
                **{key: value for key, value in line.items() if key != "text"},
                "question_id": question_id, "kind": "ocr",
            })
    return candidates


def _ocr_box_bounds(
    box: Any,
    *,
    width: int,
    height: int,
) -> tuple[int, int, int, int] | None:
    if not isinstance(box, (list, tuple)) or not box:
        return None
    points = [
        point
        for point in box
        if isinstance(point, (list, tuple)) and len(point) >= 2
    ]
    if not points:
        return None
    try:
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
    except (TypeError, ValueError):
        return None
    left = max(0, min(width - 1, int(round(min(xs)))))
    top = max(0, min(height - 1, int(round(min(ys)))))
    right = max(left + 1, min(width, int(round(max(xs)))))
    bottom = max(top + 1, min(height, int(round(max(ys)))))
    return left, top, right - left, bottom - top


def _score_number(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_name(value: str) -> str:
    value = re.sub(r'[\\/:*?"<>|\s]+', "_", value.strip())
    return value[:60] or "unknown"


def _save_images_as_pdf(image_paths: list[Path], output_path: Path) -> None:
    if not image_paths:
        raise ValueError("没有可写入 PDF 的原卷页面。")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pages: list[Image.Image] = []
    try:
        for image_path in image_paths:
            with Image.open(image_path) as image:
                pages.append(image.convert("RGB").copy())

        first_page = pages[0]
        rest_pages = pages[1:]
        first_page.save(output_path, "PDF", resolution=200.0, save_all=True, append_images=rest_pages)
    finally:
        for page in pages:
            page.close()
