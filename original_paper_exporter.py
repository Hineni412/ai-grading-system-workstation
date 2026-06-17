from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from annotation_renderer import render_annotated_paper
from answer_region_geometry import answer_regions_with_template_source_sizes
from db_manager import DBManager
from export_names import safe_filename_fragment, session_export_path_name
from image_preprocessor import _enhanced_name
from path_manager import resolve_stored_file_path


class OriginalPaperExporter:
    def __init__(self, db: DBManager, output_dir: Path) -> None:
        self.db = db
        self.output_dir = output_dir

    def export_session_originals(self, session_id: int) -> Path:
        results = self.db.get_session_results(session_id)
        if not results:
            raise ValueError("当前考试批改暂无结果，无法导出原卷。")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        session = self.db.get_grading_session(session_id)
        session_name = session.get("session_name") if session else f"考试批改_{session_id}"
        work_dir = self.output_dir / f"{safe_filename_fragment(session_name, '考试批改')}_批注原卷页面_{ts}"
        work_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = self.output_dir / session_export_path_name(session_name, "批注原卷", "pdf", ts)

        regions = answer_regions_with_template_source_sizes(
            self.db,
            session_id,
            data_root=self._data_root(),
        )
        score_map, type_map = self._load_rubric_maps(session_id)
        enhanced_path_map = self._load_enhanced_path_map(session_id)

        page_images: list[Path] = []
        sorted_results = sorted(
            results,
            key=lambda item: (
                str(item.get("student_code") or ""),
                str(item.get("student_name") or ""),
                int(item.get("result_id") or 0),
            ),
        )
        for index, result in enumerate(sorted_results, start=1):
            result_id = int(result["result_id"])
            details = self.db.get_result_details(result_id)
            question_scores = self._build_question_scores(details, score_map, type_map)
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
                output_front=front_out,
                output_back=back_out,
                annotate_only_deductions=True,
                label_position="bottom_right",
                summary_labels=False,
            )
            page_images.extend([front_out, back_out])

        _save_images_as_pdf(page_images, pdf_path)

        return pdf_path

    def _build_question_scores(
        self,
        details: list[dict[str, Any]],
        score_map: dict[str, float],
        type_map: dict[str, str],
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for detail in details:
            qid = str(detail.get("question_id") or "").strip()
            if not qid:
                continue
            awarded = float(detail.get("score_awarded") or 0)
            full = score_map.get(qid, awarded)
            result[qid] = {
                "score_awarded": awarded,
                "max_score": full,
                "deduction_reason": detail.get("deduction_reason"),
                "question_type": type_map.get(qid, ""),
            }
        return result

    def _load_rubric_maps(self, session_id: int) -> tuple[dict[str, float], dict[str, str]]:
        session = self.db.get_grading_session(session_id)
        if not session:
            return {}, {}
        rubric_path = self._resolve_stored_file_path(session.get("rubric_path"))
        if not rubric_path.exists():
            return {}, {}

        try:
            rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        except Exception:
            return {}, {}

        score_map: dict[str, float] = {}
        type_map: dict[str, str] = {}
        questions = rubric.get("questions") if isinstance(rubric, dict) else []
        if not isinstance(questions, list):
            return score_map, type_map

        for question in questions:
            if not isinstance(question, dict):
                continue
            qid = str(question.get("question_id") or "").strip()
            qtype = str(question.get("question_type") or "").strip()
            if qid:
                score_map[qid] = float(question.get("max_score") or 0)
                type_map[qid] = qtype
            parts = question.get("parts")
            if isinstance(parts, list):
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    pid = str(part.get("part_id") or "").strip()
                    if pid:
                        score_map[pid] = float(part.get("part_score") or 0)
                        type_map[pid] = qtype
        return score_map, type_map

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
