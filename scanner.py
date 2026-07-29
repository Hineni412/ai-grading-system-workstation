from __future__ import annotations

import hashlib
import io
import inspect
import json
import math
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, List

from PIL import Image

from answer_region_geometry import scaled_region_bbox
from backend.domain_models import ExamPaperGroup
from backend.llm.execution import execution_snapshot_from_profile
from grading_limits import PRECHECK_WORKERS_MAX, PRECHECK_WORKERS_MIN, bounded_int
from image_preprocessor import ENHANCER_VERSION, enhance_for_ai, enhance_image_file
from llm_client import LLMClient

STUDENT_NAME_REGION_ID = "__student_name__"
STUDENT_NAME_REGION_ALIASES = {STUDENT_NAME_REGION_ID, "student_name", "name", "姓名", "姓名区域"}


@dataclass
class PageRecord:
    image_path: Path
    source_file: str
    page_number: int | None
    detected_name: str | None = None
    enhanced_image_path: Path | None = None

    @property
    def source_label(self) -> str:
        if self.page_number is None:
            return self.source_file
        return f"{self.source_file} 第 {self.page_number} 页"


@dataclass
class ScanIssue:
    issue_id: str
    issue_type: str
    message: str
    front_image: Path
    back_image: Path | None = None
    detected_name: str | None = None
    source_label: str = ""
    enhanced_front_image: Path | None = None
    enhanced_back_image: Path | None = None
    suggested_student_id: int | None = None
    suggested_student_name: str | None = None
    suggested_match_score: float | None = None


@dataclass
class ScanAnalysis:
    groups: list[ExamPaperGroup] = field(default_factory=list)
    issues: list[ScanIssue] = field(default_factory=list)
    absent_students: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total_pages: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "groups": [_group_to_dict(group) for group in self.groups],
            "issues": [_issue_to_dict(issue) for issue in self.issues],
            "absent_students": self.absent_students,
            "warnings": self.warnings,
            "total_pages": self.total_pages,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScanAnalysis":
        return cls(
            groups=[_group_from_dict(item) for item in data.get("groups", []) if isinstance(item, dict)],
            issues=[_issue_from_dict(item) for item in data.get("issues", []) if isinstance(item, dict)],
            absent_students=[dict(item) for item in data.get("absent_students", []) if isinstance(item, dict)],
            warnings=[str(item) for item in data.get("warnings", [])],
            total_pages=int(data.get("total_pages") or 0),
        )


PDF_RENDER_SCALE = 1.6
STANDARD_PAGE_QUALITY = 88
STANDARD_PAGE_MANIFEST = "source_manifest.json"
ScanProgressCallback = Callable[[float, str, str], None]


class _ScanProgressReporter:
    """Translate completed page work into monotonic, user-facing scan progress."""

    def __init__(
        self,
        report: ScanProgressCallback | None,
        *,
        total_pages: int,
        recognition_pages: int,
    ) -> None:
        self._report = report
        self._total_pages = max(0, int(total_pages))
        self._recognition_pages = max(0, int(recognition_pages))
        self._recognition_done = 0
        self._paired_pages = 0
        self._lock = threading.Lock()

    def prepared(self, prepared_pages: int) -> None:
        total = self._total_pages
        done = min(total, max(0, int(prepared_pages)))
        ratio = done / total if total else 1.0
        self._emit(
            0.08 + 0.07 * ratio,
            "转换页面",
            f"页面准备已处理 {done}/{total} 页",
        )

    def recognition_page_done(self) -> None:
        with self._lock:
            self._recognition_done += 1
            completed_work = min(self._recognition_done, self._recognition_pages)
            total_work = self._recognition_pages
            ratio = completed_work / total_work if total_work else 1.0
            self._emit(
                0.15 + 0.75 * ratio,
                "识别姓名",
                f"姓名识别已处理 {completed_work}/{total_work} 个候选正面页",
            )

    def recognition_complete(self) -> None:
        with self._lock:
            detail = (
                f"姓名识别阶段已完成，共处理 {self._recognition_done} 个候选正面页"
                if self._recognition_done
                else "没有需要识别姓名的页面"
            )
            self._emit(0.90, "识别姓名", detail)

    def pairing_pages_done(self, page_count: int) -> None:
        with self._lock:
            self._paired_pages = min(
                self._total_pages,
                self._paired_pages + max(0, int(page_count)),
            )
            total = self._total_pages
            done = self._paired_pages
            ratio = done / total if total else 1.0
            self._emit(
                0.90 + 0.06 * ratio,
                "整理答卷",
                f"答卷配对已处理 {done}/{total} 页",
            )

    def finalizing(self) -> None:
        self._emit(0.97, "生成结果", "正在核对匹配结果并生成预检清单")

    def _emit(self, progress: float, stage: str, detail: str) -> None:
        if self._report is not None:
            self._report(progress, stage, detail)


def _sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_pdf_stem(pdf_path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", pdf_path.stem).strip("_") or "pdf"


def _read_standard_page_manifest(page_dir: Path) -> dict[str, Any]:
    manifest_path = page_dir / STANDARD_PAGE_MANIFEST
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _pdf_page_count(pdf_path: Path) -> int:
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise RuntimeError("缺少 PyMuPDF，无法读取 PDF。请先安装 pymupdf。") from exc

    doc = fitz.open(Path(pdf_path))
    try:
        return int(doc.page_count)
    finally:
        doc.close()


def render_pdf_to_standard_pages(
    pdf_path: Path,
    render_root: Path,
    *,
    enhance_images: bool,
    delete_source_pdf: bool = False,
    page_prepared: Callable[[int, int], None] | None = None,
) -> list[PageRecord]:
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise RuntimeError("缺少 PyMuPDF，无法读取 PDF。请先安装 pymupdf。") from exc

    pdf_path = Path(pdf_path)
    render_root = Path(render_root)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    source_size = pdf_path.stat().st_size
    source_hash = _sha1_file(pdf_path)
    safe_stem = _safe_pdf_stem(pdf_path)
    output_dir = render_root / safe_stem
    output_dir.mkdir(parents=True, exist_ok=True)
    previous_manifest = _read_standard_page_manifest(output_dir)
    force_render = bool(previous_manifest) and bool(previous_manifest.get("enhance_images")) != bool(enhance_images)

    records: list[PageRecord] = []
    doc = fitz.open(pdf_path)
    try:
        for page_index in range(doc.page_count):
            image_path = output_dir / f"page_{page_index + 1:03d}.jpg"
            if force_render or not image_path.exists():
                page = doc.load_page(page_index)
                pix = page.get_pixmap(matrix=fitz.Matrix(PDF_RENDER_SCALE, PDF_RENDER_SCALE), alpha=False)
                image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                try:
                    if enhance_images:
                        image = enhance_for_ai(image)
                    image.save(image_path, format="JPEG", quality=STANDARD_PAGE_QUALITY, optimize=True)
                finally:
                    image.close()
            records.append(
                PageRecord(
                    image_path=image_path,
                    source_file=pdf_path.name,
                    page_number=page_index + 1,
                    enhanced_image_path=image_path if enhance_images else None,
                )
            )
            if page_prepared is not None:
                page_prepared(page_index + 1, doc.page_count)
        page_count = doc.page_count
    finally:
        doc.close()

    manifest = {
        "source_pdf_name": pdf_path.name,
        "source_pdf_size": source_size,
        "source_pdf_sha1": source_hash,
        "page_count": page_count,
        "rendered_at": datetime.now().isoformat(timespec="seconds"),
        "render_scale": PDF_RENDER_SCALE,
        "page_quality": STANDARD_PAGE_QUALITY,
        "enhance_images": bool(enhance_images),
        "enhancer_version": ENHANCER_VERSION if enhance_images else None,
    }
    (output_dir / STANDARD_PAGE_MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if delete_source_pdf:
        pdf_path.unlink(missing_ok=True)

    return records


class Scanner:
    def __init__(
        self,
        exams_dir: Path,
        llm_client: LLMClient,
        ocr_model: str | None = None,
        enhance_images: bool = True,
        ocr_workers: int | None = None,
        name_region: dict[str, Any] | None = None,
        front_page_parity: str | None = None,
        delete_source_pdfs: bool = False,
    ) -> None:
        self.exams_dir = exams_dir
        self.llm_client = llm_client
        self.ocr_model = ocr_model
        self.enhance_images = enhance_images
        execution_profile = getattr(
            getattr(self.llm_client, "settings", None),
            "policy_profile",
            None,
        )
        execution_snapshot = execution_snapshot_from_profile(execution_profile)
        self.ocr_workers = bounded_int(
            ocr_workers,
            execution_snapshot.max_in_flight,
            PRECHECK_WORKERS_MIN,
            min(
                PRECHECK_WORKERS_MAX,
                execution_snapshot.max_in_flight,
            ),
        )
        self.name_region = dict(name_region or {}) if name_region else None
        self.front_page_parity = _normalize_front_page_parity(front_page_parity)
        self.delete_source_pdfs = delete_source_pdfs
        self.render_dir = self.exams_dir / "_pdf_pages"
        self.enhanced_dir = self.exams_dir / "_enhanced"
        import threading
        self._ocr_lock = threading.Lock()

    def scan(self) -> List[ExamPaperGroup]:
        return self.analyze().groups

    def analyze(
        self,
        students: list[dict[str, Any]] | None = None,
        report: ScanProgressCallback | None = None,
    ) -> ScanAnalysis:
        student_lookup = _build_student_lookup(students or [])
        pdf_files = self._collect_pdfs()
        image_files = self._collect_images()
        rendered_page_sets = [] if pdf_files else self._collect_rendered_pdf_page_sets()
        if not image_files and not pdf_files and not rendered_page_sets:
            raise FileNotFoundError(f"试卷目录为空: {self.exams_dir}")

        pdf_page_counts = {
            pdf_path: _pdf_page_count(pdf_path)
            for pdf_path in pdf_files
        }
        total_pages = (
            len(image_files)
            + sum(pdf_page_counts.values())
            + sum(len(pages) for _source_name, pages in rendered_page_sets)
        )
        recognition_pages = len(image_files) // 2
        source_page_counts = list(pdf_page_counts.values()) + [
            len(pages)
            for _source_name, pages in rendered_page_sets
        ]
        for page_count in source_page_counts:
            if self.front_page_parity in {"odd", "even"}:
                recognition_pages += len(
                    _front_page_indices(page_count, self.front_page_parity)
                )
            else:
                # Without a fixed template side, the even pages may also need OCR.
                recognition_pages += page_count

        progress = _ScanProgressReporter(
            report,
            total_pages=total_pages,
            recognition_pages=recognition_pages,
        )
        prepared_pages = len(image_files) + sum(
            len(pages)
            for _source_name, pages in rendered_page_sets
        )
        progress.prepared(prepared_pages)

        pdf_page_sets: list[tuple[str, list[PageRecord]]] = []
        for pdf_path in pdf_files:
            def page_prepared(
                _file_page: int,
                _file_total: int,
            ) -> None:
                nonlocal prepared_pages
                prepared_pages += 1
                progress.prepared(prepared_pages)

            pages = self._render_pdf_pages(
                pdf_path,
                page_prepared=page_prepared,
            )
            pdf_page_sets.append((pdf_path.name, pages))

        analysis = ScanAnalysis()
        if students is not None and not students:
            analysis.warnings.append("当前未导入学生库，无法把 OCR 姓名匹配到班级学生；请先导入学生名单。")
        matched_student_ids: set[int] = set()

        legacy_detected_names: dict[int, str | None] = {}
        if image_files:
            paired_page_count = len(image_files) - (len(image_files) % 2)
            for index in range(0, paired_page_count, 2):
                legacy_detected_names[index] = self._extract_student_name(
                    image_files[index],
                    student_lookup,
                )
                progress.recognition_page_done()

        all_pdf_page_sets = pdf_page_sets + rendered_page_sets
        for _source_name, pages in all_pdf_page_sets:
            self._extract_pdf_page_names(
                pages,
                student_lookup,
                on_page_processed=progress.recognition_page_done,
            )

        progress.recognition_complete()

        if image_files:
            image_analysis = self._analyze_legacy_images(
                image_files,
                student_lookup,
                detected_names=legacy_detected_names,
            )
            analysis.groups.extend(image_analysis.groups)
            analysis.issues.extend(image_analysis.issues)
            analysis.warnings.extend(image_analysis.warnings)
            analysis.total_pages += image_analysis.total_pages
            progress.pairing_pages_done(len(image_files))

        for source_name, pages in all_pdf_page_sets:
            pdf_analysis = self._pair_pdf_pages(source_name, pages, student_lookup)
            analysis.groups.extend(pdf_analysis.groups)
            analysis.issues.extend(pdf_analysis.issues)
            analysis.warnings.extend(pdf_analysis.warnings)
            analysis.total_pages += len(pages)
            progress.pairing_pages_done(len(pages))

        progress.finalizing()
        refine_scan_analysis_matches(analysis, students or [])

        for group in analysis.groups:
            if group.student_id is not None:
                matched_student_ids.add(int(group.student_id))

        if students:
            analysis.absent_students = [
                dict(student)
                for student in students
                if int(student.get("id") or 0) not in matched_student_ids
            ]

        return analysis

    def _collect_images(self) -> list[Path]:
        if not self.exams_dir.exists():
            raise FileNotFoundError(f"试卷目录不存在: {self.exams_dir}")
        return sorted(
            [
                path
                for path in self.exams_dir.iterdir()
                if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
            ],
            key=lambda p: p.name,
        )

    def _collect_pdfs(self) -> list[Path]:
        if not self.exams_dir.exists():
            raise FileNotFoundError(f"试卷目录不存在: {self.exams_dir}")
        return sorted(
            [path for path in self.exams_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf"],
            key=lambda p: p.name,
        )

    def _collect_rendered_pdf_page_sets(self) -> list[tuple[str, list[PageRecord]]]:
        if not self.render_dir.exists() or not self.render_dir.is_dir():
            return []

        result: list[tuple[str, list[PageRecord]]] = []
        page_dirs = sorted((item for item in self.render_dir.iterdir() if item.is_dir()), key=lambda p: p.name)
        for page_dir in page_dirs:
            image_paths = sorted(page_dir.glob("page_*.jpg"), key=lambda p: p.name)
            if not image_paths:
                continue
            manifest = _read_standard_page_manifest(page_dir)
            source_name = str(manifest.get("source_pdf_name") or page_dir.name)
            is_enhanced = bool(manifest.get("enhance_images"))
            pages = [
                PageRecord(
                    image_path=path,
                    source_file=source_name,
                    page_number=index,
                    enhanced_image_path=path if is_enhanced else None,
                )
                for index, path in enumerate(image_paths, start=1)
            ]
            result.append((source_name, pages))
        return result

    def _analyze_legacy_images(
        self,
        image_files: list[Path],
        student_lookup: dict[str, dict[str, Any]],
        *,
        detected_names: dict[int, str | None] | None = None,
    ) -> ScanAnalysis:
        analysis = ScanAnalysis(total_pages=len(image_files))
        if len(image_files) % 2 != 0:
            analysis.warnings.append(f"图片总数为奇数，最后一张已进入异常队列: {image_files[-1].name}")
            issue_id = f"image_orphan_{len(image_files)}"
            analysis.issues.append(
                ScanIssue(
                    issue_id=issue_id,
                    issue_type="orphan_page",
                    message="图片总数为奇数，无法组成正反面。",
                    front_image=image_files[-1],
                    source_label=image_files[-1].name,
                )
            )
            image_files = image_files[:-1]

        for index in range(0, len(image_files), 2):
            front_image, back_image = image_files[index], image_files[index + 1]
            detected_name = (
                detected_names[index]
                if detected_names is not None and index in detected_names
                else self._extract_student_name(front_image, student_lookup)
            )
            match = _match_student(detected_name, student_lookup)
            student = match.student if match else None
            if student:
                analysis.groups.append(
                    ExamPaperGroup(
                        front_image=front_image,
                        back_image=back_image,
                        student_name=str(student["name"]),
                        student_id=int(student["id"]) if student.get("id") is not None else None,
                        detected_name=detected_name,
                        source_label=f"{front_image.name} + {back_image.name}",
                        enhanced_front_image=None,
                        enhanced_back_image=None,
                        match_method=match.method,
                        match_score=match.score,
                    )
                )
            else:
                issue_type = "unknown_name" if detected_name else "missing_name"
                message = "识别到姓名但不在学生库中。" if detected_name else "正面未识别到姓名。"
                analysis.issues.append(
                    ScanIssue(
                        issue_id=f"image_pair_{index + 1}_{index + 2}",
                        issue_type=issue_type,
                        message=message,
                        front_image=front_image,
                        back_image=back_image,
                        detected_name=detected_name,
                        source_label=f"{front_image.name} + {back_image.name}",
                        enhanced_front_image=None,
                        enhanced_back_image=None,
                        suggested_student_id=match.suggested_student_id if match else None,
                        suggested_student_name=match.suggested_student_name if match else None,
                        suggested_match_score=match.suggested_score if match else None,
                    )
                )
        return analysis

    def _render_pdf_pages(
        self,
        pdf_path: Path,
        *,
        page_prepared: Callable[[int, int], None] | None = None,
    ) -> list[PageRecord]:
        return render_pdf_to_standard_pages(
            pdf_path,
            self.render_dir,
            enhance_images=self.enhance_images,
            delete_source_pdf=self.delete_source_pdfs,
            page_prepared=page_prepared,
        )

    def _pair_pdf_pages(
        self,
        source_name: str,
        pages: list[PageRecord],
        student_lookup: dict[str, dict[str, Any]],
    ) -> ScanAnalysis:
        if self.front_page_parity in {"odd", "even"}:
            return _pair_pdf_pages_by_template_parity(source_name, pages, student_lookup, self.front_page_parity)

        analysis = ScanAnalysis()
        used: set[int] = set()
        page_matches = [_match_student(page.detected_name, student_lookup) for page in pages]

        split_analysis = _try_pair_split_front_back_order(source_name, pages, page_matches)
        if split_analysis is not None:
            return split_analysis

        for idx, page in enumerate(pages):
            if idx in used or not _is_front_candidate(page, page_matches[idx]):
                continue

            prev_idx = idx - 1 if _is_available_back_candidate(pages, page_matches, used, idx - 1) else None
            next_idx = idx + 1 if _is_available_back_candidate(pages, page_matches, used, idx + 1) else None
            back_idx = _choose_adjacent_back_index(pages, idx, prev_idx, next_idx)
            match = page_matches[idx]
            student = match.student if match else None

            if back_idx is None:
                analysis.issues.append(
                    ScanIssue(
                        issue_id=f"{_safe_id(source_name)}_p{page.page_number or idx + 1}",
                        issue_type="missing_back",
                        message="识别到正面姓名，但相邻页无法确定为反面。",
                        front_image=page.image_path,
                        detected_name=page.detected_name,
                        source_label=page.source_label,
                        enhanced_front_image=page.enhanced_image_path,
                    )
                )
                used.add(idx)
                continue

            back = pages[back_idx]
            used.update({idx, back_idx})
            source_label = f"{source_name} 第 {page.page_number}-{back.page_number} 页"
            if student:
                analysis.groups.append(
                    ExamPaperGroup(
                        front_image=page.image_path,
                        back_image=back.image_path,
                        student_name=str(student["name"]),
                        student_id=int(student["id"]) if student.get("id") is not None else None,
                        detected_name=page.detected_name,
                        source_label=source_label,
                        enhanced_front_image=page.enhanced_image_path,
                        enhanced_back_image=back.enhanced_image_path,
                        match_method=match.method,
                        match_score=match.score,
                    )
                )
            else:
                analysis.issues.append(
                    ScanIssue(
                        issue_id=f"{_safe_id(source_name)}_p{page.page_number}_{back.page_number}",
                        issue_type="unknown_name",
                        message="识别到姓名但不在学生库中，请人工匹配或标记无效卷。",
                        front_image=page.image_path,
                        back_image=back.image_path,
                        detected_name=page.detected_name,
                        source_label=source_label,
                        enhanced_front_image=page.enhanced_image_path,
                        enhanced_back_image=back.enhanced_image_path,
                        suggested_student_id=match.suggested_student_id if match else None,
                        suggested_student_name=match.suggested_student_name if match else None,
                        suggested_match_score=match.suggested_score if match else None,
                    )
                )

        idx = 0
        while idx < len(pages):
            if idx in used:
                idx += 1
                continue
            page = pages[idx]
            if idx + 1 < len(pages) and idx + 1 not in used:
                back = pages[idx + 1]
                analysis.issues.append(
                    ScanIssue(
                        issue_id=f"{_safe_id(source_name)}_p{page.page_number}_{back.page_number}",
                        issue_type="missing_name",
                        message="相邻两页均未可靠识别姓名，请人工匹配或标记无效卷。",
                        front_image=page.image_path,
                        back_image=back.image_path,
                        detected_name=page.detected_name,
                        source_label=f"{source_name} 第 {page.page_number}-{back.page_number} 页",
                    )
                )
                used.update({idx, idx + 1})
                idx += 2
            else:
                analysis.issues.append(
                    ScanIssue(
                        issue_id=f"{_safe_id(source_name)}_p{page.page_number or idx + 1}",
                        issue_type="orphan_page",
                        message="该页无法与相邻页组成一份双面答卷。",
                        front_image=page.image_path,
                        detected_name=page.detected_name,
                        source_label=page.source_label,
                    )
                )
                used.add(idx)
                idx += 1

        if len(pages) % 2 != 0:
            analysis.warnings.append(f"{source_name} 页数为奇数，请检查是否漏扫。")
        return analysis

    def _extract_pdf_page_names(
        self,
        pages: list[PageRecord],
        student_lookup: dict[str, dict[str, Any]],
        *,
        on_page_processed: Callable[[], None] | None = None,
    ) -> None:
        if not pages:
            return

        if self.front_page_parity in {"odd", "even"}:
            self._extract_pdf_page_names_for_indices(
                pages,
                _front_page_indices(len(pages), self.front_page_parity),
                student_lookup,
                on_page_processed=on_page_processed,
            )
            return

        odd_indices = [idx for idx in range(0, len(pages), 2)]
        self._extract_pdf_page_names_for_indices(
            pages,
            odd_indices,
            student_lookup,
            on_page_processed=on_page_processed,
        )
        if _front_side_confident(pages, odd_indices, student_lookup):
            return

        even_indices = [idx for idx in range(1, len(pages), 2)]
        self._extract_pdf_page_names_for_indices(
            pages,
            even_indices,
            student_lookup,
            on_page_processed=on_page_processed,
        )

    def _extract_pdf_page_names_for_indices(
        self,
        pages: list[PageRecord],
        indices: list[int],
        student_lookup: dict[str, dict[str, Any]] | None = None,
        *,
        on_page_processed: Callable[[], None] | None = None,
    ) -> None:
        pending = [idx for idx in indices if 0 <= idx < len(pages) and pages[idx].detected_name is None]
        if not pending:
            return

        worker_count = min(self.ocr_workers, len(pending))
        if worker_count <= 1:
            for idx in pending:
                page = pages[idx]
                page.detected_name = self._call_extract_student_name(
                    page.enhanced_image_path or page.image_path,
                    student_lookup,
                )
                if on_page_processed is not None:
                    on_page_processed()
            return

        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="scan-ocr") as executor:
            future_map = {
                executor.submit(
                    self._call_extract_student_name,
                    page.enhanced_image_path or page.image_path,
                    student_lookup,
                ): page
                for page in (pages[idx] for idx in pending)
            }
            for future in as_completed(future_map):
                page = future_map[future]
                try:
                    page.detected_name = future.result()
                except Exception as exc:  # noqa: BLE001
                    print(f"[WARNING] PDF第 {page.page_number} 页姓名识别失败: {exc}")
                    page.detected_name = None
                if on_page_processed is not None:
                    on_page_processed()

    def _call_extract_student_name(
        self,
        image_path: Path,
        student_lookup: dict[str, dict[str, Any]] | None = None,
        report: Any = None,
    ) -> str | None:
        parameters = list(inspect.signature(self._extract_student_name).parameters)
        if len(parameters) <= 1:
            return self._extract_student_name(image_path)
        if len(parameters) <= 2:
            return self._extract_student_name(image_path, student_lookup)
        return self._extract_student_name(image_path, student_lookup, report=report)

    def _do_local_ocr(self, image: Image.Image) -> str | None:
        try:
            import threading
            lock = getattr(self, "_ocr_lock", threading.Lock())
            with lock:
                from rapidocr_onnxruntime import RapidOCR
                if not hasattr(self, "_rapid_ocr"):
                    self._rapid_ocr = RapidOCR()
                
                import numpy as np
                img_cv = np.array(image.convert("RGB"))
                img_cv = img_cv[:, :, ::-1] # RGB to BGR
                
                result, _ = self._rapid_ocr(img_cv)
                if result:
                    texts = [res[1] for res in result]
                    return "".join(texts)
            return None
        except ImportError:
            print("[WARNING] rapidocr_onnxruntime 未安装，将跳过本地OCR")
            return None
        except Exception as exc:
            print(f"[WARNING] 本地 OCR 失败: {exc}")
            return None

    def _extract_student_name(self, front_image: Path, student_lookup: dict[str, dict[str, Any]] | None = None, report: Any = None) -> str | None:
        if report:
            report(None, "识别姓名", f"[{front_image.name}] 正在提取本地 OCR 文本...")
        with Image.open(front_image) as image:
            rgb_image = image.convert("RGB")
            width, height = rgb_image.size
            crop_box = _student_name_crop_box(self.name_region, width, height)
            top_region = rgb_image.crop(crop_box)
            
            local_text = self._do_local_ocr(top_region)
            local_match_score = 0.0
            best_local_text = None
            
            if local_text:
                cleaned = local_text.strip().replace("\n", "")
                for prefix in ["姓名", "学生", "考生", ":", "：", " "]:
                    cleaned = cleaned.replace(prefix, "")
                cleaned = cleaned.strip("`\"' ：:，,。 ")
                
                best_local_text = cleaned
                if student_lookup:
                    match = _match_student(cleaned, student_lookup)
                    if match:
                        if match.student:
                            if report:
                                report(None, "识别姓名", f"[{front_image.name}] ✔️ 本地完美匹配到: {match.student.get('name')}")
                            return str(match.student.get("name"))
                        else:
                            local_match_score = match.suggested_score or 0.0
                else:
                    if cleaned:
                        if report:
                            report(None, "识别姓名", f"[{front_image.name}] 本地提取到: {cleaned}")
                        return cleaned

            buffer = io.BytesIO()
            top_region.save(buffer, format="JPEG", quality=82)
            image_bytes = buffer.getvalue()

        prompt = (
            "你是OCR助手。请只提取学生姓名。"
            "如果这一页不是试卷正面，或无法确定姓名，严格输出 NOT_FOUND。"
            "只允许输出姓名本身或 NOT_FOUND，不要输出其他内容。"
        )
        
        system_prompt = None
        if student_lookup:
            names = list(student_lookup.keys())
            import json
            system_prompt = (
                "你必须在以下名单中寻找最匹配的学生姓名：\n"
                f"{json.dumps(names, ensure_ascii=False)}\n"
                "如果图片上的姓名明显与名单上的任何人都不相符，或者图片并不是试卷正面，请严格返回 NOT_FOUND。"
            )

        if report:
            report(None, "兜底识别", f"[{front_image.name}] ⚠️ 本地匹配失败，正触发大模型智能提取...")
            
        text = self.llm_client.text_from_images(prompt, [image_bytes], model=self.ocr_model, system_prompt=system_prompt)

        cleaned_ai = text.strip().replace("\n", "")
        if not cleaned_ai:
            if report: report(None, "兜底识别", f"[{front_image.name}] 大模型未返回内容，使用本地候选: {best_local_text}")
            return best_local_text

        normalized_ai = cleaned_ai.strip("`\"' ：:，,。 ")
        if normalized_ai.upper() == "NOT_FOUND":
            if report: report(None, "兜底识别", f"[{front_image.name}] 大模型判定非正面或无姓名，使用本地候选: {best_local_text}")
            return best_local_text

        blockers = {"未识别", "无法识别", "看不清", "无姓名", "not found", "unknown", "none"}
        if any(token in normalized_ai.lower() for token in {"not found", "unknown", "none"}):
            if report: report(None, "兜底识别", f"[{front_image.name}] 大模型识别失败，使用本地候选: {best_local_text}")
            return best_local_text
        if any(token in normalized_ai for token in blockers - {"not found", "unknown", "none"}):
            if report: report(None, "兜底识别", f"[{front_image.name}] 大模型识别失败，使用本地候选: {best_local_text}")
            return best_local_text

        if student_lookup and best_local_text and local_match_score > 0:
            ai_match = _match_student(normalized_ai, student_lookup)
            ai_score = 0.0
            if ai_match:
                if ai_match.student:
                    ai_score = ai_match.score
                else:
                    ai_score = ai_match.suggested_score or 0.0
                    
            if local_match_score > ai_score:
                if report: report(None, "兜底比较", f"[{front_image.name}] 本地相似度({local_match_score:.2f}) > AI相似度({ai_score:.2f})，采用本地: {best_local_text}")
                return best_local_text

        if report:
            report(None, "兜底识别", f"[{front_image.name}] 💡 AI提取返回: {normalized_ai}")
        return normalized_ai

    def _ai_image_path(self, image_path: Path) -> Path:
        if not self.enhance_images:
            return image_path
        try:
            return enhance_image_file(image_path, self.enhanced_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARNING] 图像增强失败，改用原图: {image_path} ({exc})")
            return image_path


def _build_student_lookup(students: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for student in students:
        name = str(student.get("name") or "").strip()
        if name:
            lookup[_normalize_name(name)] = student
    return lookup


def student_name_region_from_regions(regions: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> dict[str, Any] | None:
    for region in regions or []:
        qid = str(region.get("mapped_question_id") or region.get("detected_question_id") or "").strip()
        if qid in STUDENT_NAME_REGION_ALIASES and str(region.get("page") or "front") == "front":
            return dict(region)
    return None


def refine_scan_analysis_matches(analysis: ScanAnalysis, students: list[dict[str, Any]]) -> ScanAnalysis:
    if not students:
        return analysis
    perfect_student_ids = {
        int(group.student_id)
        for group in analysis.groups
        if group.student_id is not None
        and float(group.match_score or 0) >= 0.999
        and str(group.match_method or "") in {"exact", "reduced_exact", "manual"}
    }
    if not perfect_student_ids:
        return analysis

    reduced_students = [student for student in students if int(student.get("id") or 0) not in perfect_student_ids]
    reduced_lookup = _build_student_lookup(reduced_students)
    if not reduced_lookup:
        return analysis

    next_groups: list[ExamPaperGroup] = []
    demoted_issues: list[ScanIssue] = []
    for index, group in enumerate(analysis.groups, start=1):
        should_retry = (
            float(group.match_score or 0) < 0.999
            or str(group.match_method or "") == "fuzzy"
            or (group.student_id is not None and int(group.student_id) in perfect_student_ids and str(group.match_method or "") != "exact")
        )
        if not should_retry or not group.detected_name:
            next_groups.append(group)
            continue

        reduced_match = _match_student(group.detected_name, reduced_lookup)
        if reduced_match and reduced_match.student:
            student = reduced_match.student
            group.student_id = int(student["id"]) if student.get("id") is not None else None
            group.student_name = str(student.get("name") or "")
            group.match_method = f"reduced_{reduced_match.method}"
            group.match_score = reduced_match.score
            next_groups.append(group)
            continue

        if group.student_id is not None and int(group.student_id) in perfect_student_ids:
            demoted_issues.append(
                ScanIssue(
                    issue_id=f"reduced_match_{index}_{_safe_id(group.source_label or group.front_image.name)}",
                    issue_type="duplicate_low_confidence_name",
                    message="低可信姓名匹配与已 100% 匹配学生重复，已用剩余名单重试；请人工确认。",
                    front_image=group.front_image,
                    back_image=group.back_image,
                    detected_name=group.detected_name,
                    source_label=group.source_label,
                    enhanced_front_image=group.enhanced_front_image,
                    enhanced_back_image=group.enhanced_back_image,
                    suggested_student_id=reduced_match.suggested_student_id if reduced_match else None,
                    suggested_student_name=reduced_match.suggested_student_name if reduced_match else None,
                    suggested_match_score=reduced_match.suggested_score if reduced_match else None,
                )
            )
        else:
            next_groups.append(group)

    analysis.groups = next_groups
    analysis.issues.extend(demoted_issues)

    for issue in analysis.issues:
        if not issue.detected_name:
            continue
        reduced_match = _match_student(issue.detected_name, reduced_lookup)
        if not reduced_match:
            continue
        if reduced_match.student:
            issue.suggested_student_id = int(reduced_match.student["id"]) if reduced_match.student.get("id") is not None else None
            issue.suggested_student_name = str(reduced_match.student.get("name") or "")
            issue.suggested_match_score = reduced_match.score
        elif reduced_match.suggested_student_id is not None:
            issue.suggested_student_id = reduced_match.suggested_student_id
            issue.suggested_student_name = reduced_match.suggested_student_name
            issue.suggested_match_score = reduced_match.suggested_score

    matched_student_ids = {int(group.student_id) for group in analysis.groups if group.student_id is not None}
    analysis.absent_students = [
        dict(student)
        for student in students
        if int(student.get("id") or 0) not in matched_student_ids
    ]
    return analysis


@dataclass
class _StudentMatch:
    student: dict[str, Any] | None = None
    method: str = "none"
    score: float = 0.0
    suggested_student_id: int | None = None
    suggested_student_name: str | None = None
    suggested_score: float | None = None


def _match_student(name: str | None, student_lookup: dict[str, dict[str, Any]]) -> _StudentMatch | None:
    if not name:
        return None
    if not student_lookup:
        return None

    normalized = _normalize_name(name)
    exact = student_lookup.get(normalized)
    if exact:
        return _StudentMatch(student=exact, method="exact", score=1.0)

    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for candidate_name, student in student_lookup.items():
        score = _name_similarity(normalized, candidate_name)
        if score > 0:
            ranked.append((score, candidate_name, student))
    if not ranked:
        return None

    ranked.sort(key=lambda item: item[0], reverse=True)
    best_score, best_name, best_student = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    edit_distance = _levenshtein_distance(normalized, best_name)
    same_first_char = bool(normalized and best_name and normalized[0] == best_name[0])
    high_confidence = best_score >= 0.78 and best_score - second_score >= 0.08
    short_name_one_char_off = (
        len(normalized) >= 3
        and len(best_name) >= 3
        and edit_distance <= 1
        and same_first_char
        and best_score - second_score >= 0.05
    )

    if high_confidence or short_name_one_char_off:
        return _StudentMatch(student=best_student, method="fuzzy", score=round(best_score, 3))

    return _StudentMatch(
        student=None,
        method="suggested",
        score=round(best_score, 3),
        suggested_student_id=int(best_student["id"]) if best_student.get("id") is not None else None,
        suggested_student_name=str(best_student.get("name") or ""),
        suggested_score=round(best_score, 3),
    )


def _try_pair_split_front_back_order(
    source_name: str,
    pages: list[PageRecord],
    page_matches: list[_StudentMatch | None],
) -> ScanAnalysis | None:
    page_count = len(pages)
    if page_count < 4 or page_count % 2 != 0:
        return None
    half = page_count // 2
    first_half_fronts = sum(1 for match in page_matches[:half] if match and match.student)
    second_half_fronts = sum(1 for match in page_matches[half:] if match and match.student)
    threshold = max(1, int(half * 0.6))

    if first_half_fronts >= threshold and second_half_fronts <= max(1, half // 4):
        front_offset = 0
        back_offset = half
    elif second_half_fronts >= threshold and first_half_fronts <= max(1, half // 4):
        front_offset = half
        back_offset = 0
    else:
        return None

    analysis = ScanAnalysis()
    for pair_index in range(half):
        front_idx = front_offset + pair_index
        back_idx = back_offset + pair_index
        front = pages[front_idx]
        back = pages[back_idx]
        match = page_matches[front_idx]
        source_label = f"{source_name} 第 {front.page_number}-{back.page_number} 页"
        if match and match.student:
            student = match.student
            analysis.groups.append(
                ExamPaperGroup(
                    front_image=front.image_path,
                    back_image=back.image_path,
                    student_name=str(student["name"]),
                    student_id=int(student["id"]) if student.get("id") is not None else None,
                    detected_name=front.detected_name,
                    source_label=source_label,
                    enhanced_front_image=front.enhanced_image_path,
                    enhanced_back_image=back.enhanced_image_path,
                    match_method=match.method,
                    match_score=match.score,
                )
            )
        else:
            analysis.issues.append(
                ScanIssue(
                    issue_id=f"{_safe_id(source_name)}_p{front.page_number}_{back.page_number}",
                    issue_type="unknown_name" if front.detected_name else "missing_name",
                    message="批量正反面分组中未能可靠匹配姓名，请人工确认。",
                    front_image=front.image_path,
                    back_image=back.image_path,
                    detected_name=front.detected_name,
                    source_label=source_label,
                    enhanced_front_image=front.enhanced_image_path,
                    enhanced_back_image=back.enhanced_image_path,
                    suggested_student_id=match.suggested_student_id if match else None,
                    suggested_student_name=match.suggested_student_name if match else None,
                    suggested_match_score=match.suggested_score if match else None,
                )
            )
    return analysis


def _pair_pdf_pages_by_template_parity(
    source_name: str,
    pages: list[PageRecord],
    student_lookup: dict[str, dict[str, Any]],
    front_page_parity: str,
) -> ScanAnalysis:
    analysis = ScanAnalysis()
    used: set[int] = set()
    for front_idx in _front_page_indices(len(pages), front_page_parity):
        if front_idx in used:
            continue
        back_idx = front_idx + 1 if front_page_parity == "odd" else front_idx - 1
        front = pages[front_idx]
        if back_idx < 0 or back_idx >= len(pages):
            analysis.issues.append(
                ScanIssue(
                    issue_id=f"{_safe_id(source_name)}_p{front.page_number or front_idx + 1}",
                    issue_type="missing_back",
                    message="按样卷正反面顺序配对时，未找到该页对应的另一面。",
                    front_image=front.image_path,
                    detected_name=front.detected_name,
                    source_label=front.source_label,
                    enhanced_front_image=front.enhanced_image_path,
                )
            )
            used.add(front_idx)
            continue

        back = pages[back_idx]
        used.update({front_idx, back_idx})
        match = _match_student(front.detected_name, student_lookup)
        source_label = f"{source_name} 第 {front.page_number}-{back.page_number} 页"
        if match and match.student:
            student = match.student
            analysis.groups.append(
                ExamPaperGroup(
                    front_image=front.image_path,
                    back_image=back.image_path,
                    student_name=str(student["name"]),
                    student_id=int(student["id"]) if student.get("id") is not None else None,
                    detected_name=front.detected_name,
                    source_label=source_label,
                    enhanced_front_image=front.enhanced_image_path,
                    enhanced_back_image=back.enhanced_image_path,
                    match_method=match.method,
                    match_score=match.score,
                )
            )
            continue

        issue_type = "unknown_name" if front.detected_name else "missing_name"
        message = "识别到姓名但不在学生库中，请人工匹配。" if front.detected_name else "正面姓名识别不到，请人工查看正反面并匹配学生。"
        analysis.issues.append(
            ScanIssue(
                issue_id=f"{_safe_id(source_name)}_p{front.page_number}_{back.page_number}",
                issue_type=issue_type,
                message=message,
                front_image=front.image_path,
                back_image=back.image_path,
                detected_name=front.detected_name,
                source_label=source_label,
                enhanced_front_image=front.enhanced_image_path,
                enhanced_back_image=back.enhanced_image_path,
                suggested_student_id=match.suggested_student_id if match else None,
                suggested_student_name=match.suggested_student_name if match else None,
                suggested_match_score=match.suggested_score if match else None,
            )
        )

    for idx, page in enumerate(pages):
        if idx in used:
            continue
        analysis.issues.append(
            ScanIssue(
                issue_id=f"{_safe_id(source_name)}_p{page.page_number or idx + 1}",
                issue_type="orphan_page",
                message="该页未能按样卷正反面顺序组成一份双面答卷。",
                front_image=page.image_path,
                detected_name=page.detected_name,
                source_label=page.source_label,
                enhanced_front_image=page.enhanced_image_path,
            )
        )

    if len(pages) % 2 != 0:
        analysis.warnings.append(f"{source_name} 页数为奇数，请检查是否漏扫。")
    return analysis


def _front_page_indices(page_count: int, front_page_parity: str) -> list[int]:
    start = 0 if front_page_parity == "odd" else 1
    return list(range(start, page_count, 2))


def _is_front_candidate(page: PageRecord, match: _StudentMatch | None) -> bool:
    if not page.detected_name:
        return False
    if match and match.student:
        return True
    return bool(match and match.suggested_student_id is not None)


def _is_available_back_candidate(
    pages: list[PageRecord],
    page_matches: list[_StudentMatch | None],
    used: set[int],
    idx: int,
) -> bool:
    if idx < 0 or idx >= len(pages) or idx in used:
        return False
    match = page_matches[idx]
    return not (match and match.student)


def _front_side_confident(
    pages: list[PageRecord],
    candidate_indices: list[int],
    student_lookup: dict[str, dict[str, Any]],
) -> bool:
    if not candidate_indices or not student_lookup:
        return bool(candidate_indices)
    matched = 0
    checked = 0
    for idx in candidate_indices:
        if idx < 0 or idx >= len(pages):
            continue
        checked += 1
        match = _match_student(pages[idx].detected_name, student_lookup)
        if match and match.student:
            matched += 1
    if checked == 0:
        return False
    required = max(1, int(math.ceil(checked * 0.6)))
    return matched >= required


def _normalize_name(value: str) -> str:
    text = re.sub(r"\s+", "", value).strip().lower()
    text = re.sub(r"^(姓名|学生|名字|name)[:：]?", "", text)
    return text


def _student_name_crop_box(region: dict[str, Any] | None, width: int, height: int) -> tuple[int, int, int, int]:
    if region:
        try:
            x = int(float(region.get("x", 0)))
            y = int(float(region.get("y", 0)))
            w = int(float(region.get("w", 0)))
            h = int(float(region.get("h", 0)))
        except (TypeError, ValueError):
            x = y = w = h = 0
        if w > 8 and h > 8:
            return scaled_region_bbox(region, width, height)
    return (0, 0, width, int(height * 0.18))


def _name_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    sequence_score = SequenceMatcher(None, left, right).ratio()
    distance_score = 1.0 - (_levenshtein_distance(left, right) / max(len(left), len(right), 1))
    common = len(set(left) & set(right)) / max(len(set(left) | set(right)), 1)
    return max(sequence_score, distance_score, common * 0.85)


def _levenshtein_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[j - 1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (0 if left_char == right_char else 1),
                )
            )
        previous = current
    return previous[-1]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _normalize_front_page_parity(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"odd", "front_odd", "first_front", "front"}:
        return "odd"
    if normalized in {"even", "front_even", "first_back", "back"}:
        return "even"
    return None


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "scan"


def _choose_adjacent_back_index(
    pages: list[PageRecord],
    front_idx: int,
    prev_idx: int | None,
    next_idx: int | None,
) -> int | None:
    if prev_idx is None:
        return next_idx
    if next_idx is None:
        return prev_idx

    # Handles repeated reverse-order scans: back A, front A, back B, front B.
    if front_idx + 2 < len(pages) and pages[front_idx + 2].detected_name:
        return prev_idx
    if front_idx - 2 >= 0 and pages[front_idx - 2].detected_name:
        return next_idx
    return next_idx


def _group_to_dict(group: ExamPaperGroup) -> dict[str, Any]:
    data = asdict(group)
    data["front_image"] = str(group.front_image)
    data["back_image"] = str(group.back_image)
    data["enhanced_front_image"] = str(group.enhanced_front_image) if group.enhanced_front_image else None
    data["enhanced_back_image"] = str(group.enhanced_back_image) if group.enhanced_back_image else None
    return data


def _issue_to_dict(issue: ScanIssue) -> dict[str, Any]:
    data = asdict(issue)
    data["front_image"] = str(issue.front_image)
    data["back_image"] = str(issue.back_image) if issue.back_image else None
    data["enhanced_front_image"] = str(issue.enhanced_front_image) if issue.enhanced_front_image else None
    data["enhanced_back_image"] = str(issue.enhanced_back_image) if issue.enhanced_back_image else None
    return data


def _group_from_dict(data: dict[str, Any]) -> ExamPaperGroup:
    return ExamPaperGroup(
        front_image=Path(str(data["front_image"])),
        back_image=Path(str(data["back_image"])),
        student_name=str(data.get("student_name") or ""),
        student_id=int(data["student_id"]) if data.get("student_id") is not None else None,
        detected_name=data.get("detected_name"),
        source_label=str(data.get("source_label") or ""),
        enhanced_front_image=Path(str(data["enhanced_front_image"])) if data.get("enhanced_front_image") else None,
        enhanced_back_image=Path(str(data["enhanced_back_image"])) if data.get("enhanced_back_image") else None,
        match_method=str(data.get("match_method") or "exact"),
        match_score=float(data.get("match_score") or 1.0),
    )


def _issue_from_dict(data: dict[str, Any]) -> ScanIssue:
    back_image = data.get("back_image")
    return ScanIssue(
        issue_id=str(data.get("issue_id") or ""),
        issue_type=str(data.get("issue_type") or "unknown"),
        message=str(data.get("message") or ""),
        front_image=Path(str(data.get("front_image"))),
        back_image=Path(str(back_image)) if back_image else None,
        detected_name=data.get("detected_name"),
        source_label=str(data.get("source_label") or ""),
        enhanced_front_image=Path(str(data["enhanced_front_image"])) if data.get("enhanced_front_image") else None,
        enhanced_back_image=Path(str(data["enhanced_back_image"])) if data.get("enhanced_back_image") else None,
        suggested_student_id=int(data["suggested_student_id"]) if data.get("suggested_student_id") is not None else None,
        suggested_student_name=data.get("suggested_student_name"),
        suggested_match_score=float(data["suggested_match_score"]) if data.get("suggested_match_score") is not None else None,
    )
