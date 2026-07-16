from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Protocol

from answer_region_geometry import answer_regions_with_template_source_sizes
from db_manager import DBManager
from scanner import ScanAnalysis, Scanner, student_name_region_from_regions


class ScannerFactory(Protocol):
    def __call__(self, **kwargs: Any) -> Scanner:
        ...


def run_scan_analysis(
    *,
    db: DBManager,
    session_id: int,
    exams_dir: Path,
    session_work_dir: Path,
    data_root: Path,
    llm_client_factory: Callable[[], Any],
    scanner_factory: ScannerFactory = Scanner,
    enhance_images: bool = True,
    ocr_workers: int | None = None,
    front_page_parity: str | None = "odd",
    scan_batch_id: str | None = None,
    raise_if_cancelled: Callable[[], None] | None = None,
) -> dict[str, object]:
    session_id = int(session_id)
    _check_cancelled(raise_if_cancelled)
    if db.get_grading_session(session_id) is None:
        raise ValueError(f"session not found: {session_id}")
    if not db.is_template_ready(session_id):
        raise ValueError("session template mapping is not confirmed")

    exams_dir = Path(exams_dir)
    if not exams_dir.exists():
        raise FileNotFoundError(f"scan directory does not exist: {exams_dir}")
    if not _list_scan_input_files(exams_dir):
        raise FileNotFoundError("no PDF/JPG/PNG scan files found")

    students = db.list_students()
    if not students:
        raise ValueError("student list is empty")

    regions = answer_regions_with_template_source_sizes(db, session_id, data_root=Path(data_root))
    llm_client = llm_client_factory()
    scanner = scanner_factory(
        exams_dir=exams_dir,
        llm_client=llm_client,
        ocr_model=_ocr_model_for_client(llm_client),
        enhance_images=bool(enhance_images),
        ocr_workers=ocr_workers,
        name_region=student_name_region_from_regions(regions),
        front_page_parity=front_page_parity,
    )
    analysis = scanner.analyze(students)
    _check_cancelled(raise_if_cancelled)
    payload = analysis.to_dict() if isinstance(analysis, ScanAnalysis) else dict(analysis)
    payload["enhance_images"] = bool(enhance_images)
    if scan_batch_id:
        payload["scan_batch_id"] = str(scan_batch_id)

    session_work_dir = Path(session_work_dir)
    output_path = session_work_dir / "scan_analysis_latest.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=".scan_analysis_latest.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
        _check_cancelled(raise_if_cancelled)
        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return {
        "session_id": session_id,
        "scan_analysis_path": str(output_path),
        "summary": scan_analysis_summary(payload),
    }


def scan_analysis_summary(payload: dict[str, Any]) -> dict[str, int]:
    return {
        "auto_matched": len(payload.get("groups") or []),
        "issues": len(payload.get("issues") or []),
        "absent_candidates": len(payload.get("absent_students") or []),
        "total_pages": int(payload.get("total_pages") or 0),
    }


def _list_scan_input_files(path: Path) -> list[Path]:
    if not path.exists() or not path.is_dir():
        return []
    direct_files = [
        item
        for item in path.iterdir()
        if item.is_file() and item.suffix.lower() in {".pdf", ".jpg", ".jpeg", ".png"}
    ]
    rendered_pages_dir = path / "_pdf_pages"
    rendered_pages = list(rendered_pages_dir.rglob("page_*.jpg")) if rendered_pages_dir.exists() else []
    return sorted([*direct_files, *rendered_pages], key=lambda item: str(item))


def _ocr_model_for_client(client: Any) -> str | None:
    settings = getattr(client, "settings", None)
    return getattr(settings, "ocr_model", None)


def _check_cancelled(callback: Callable[[], None] | None) -> None:
    if callback is not None:
        callback()
