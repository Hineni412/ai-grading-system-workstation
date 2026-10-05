from __future__ import annotations

import inspect
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from backend.answer_regions.answer_region_geometry import answer_regions_with_template_source_sizes
from backend.answer_regions.answer_region_session_lock import get_answer_region_session_lock
from backend.config_workspace.publish import load_editor_config
from backend.repositories.access import GradingRepositoryAccess
from backend.scan_grading.scanner import ScanAnalysis, Scanner, student_name_region_from_regions
from backend.exam_intake.template_upload_service import TemplateUploadError, TemplateUploadService


class ScannerFactory(Protocol):
    def __call__(self, **kwargs: Any) -> Scanner:
        ...


def run_scan_analysis(
    *,
    db: GradingRepositoryAccess,
    session_id: int,
    exams_dir: Path,
    session_work_dir: Path,
    data_root: Path,
    llm_client_factory: Callable[[], Any],
    scanner_factory: ScannerFactory = Scanner,
    enhance_images: bool = True,
    ocr_workers: int | None = None,
    front_page_parity: str | None = None,
    template_id: int | None = None,
    template_fingerprint: str | None = None,
    template_first_page_role: str | None = None,
    config_revision: str | None = None,
    scan_batch_id: str | None = None,
    raise_if_cancelled: Callable[[], None] | None = None,
    report: Callable[[float, str, str], None] | None = None,
) -> dict[str, object]:
    session_id = int(session_id)
    _check_cancelled(raise_if_cancelled)
    _report_scan_progress(
        report,
        raise_if_cancelled,
        0.06,
        "准备文件",
        "正在核对考试、模板和答卷文件",
    )
    if db.sessions.get_grading_session(session_id) is None:
        raise ValueError(f"session not found: {session_id}")
    if not db.templates.is_template_ready(session_id):
        raise ValueError("session template mapping is not confirmed")
    expected_config_revision = str(config_revision or "").strip()
    if not expected_config_revision:
        raise ValueError("scan analysis grading configuration binding is missing")
    if load_editor_config(db, session_id).revision != expected_config_revision:
        raise ValueError("grading configuration changed before scan analysis")

    bound_template = any(
        value is not None
        for value in (template_id, template_fingerprint, template_first_page_role)
    )
    if bound_template:
        if (
            template_id is None
            or not template_fingerprint
            or template_first_page_role not in {"front", "back"}
        ):
            raise ValueError("scan analysis template binding is incomplete")
        try:
            current_template = TemplateUploadService(
                Path(session_work_dir).parent
            ).load_current(
                db=db,
                session_id=session_id,
            )
        except (FileNotFoundError, TemplateUploadError) as exc:
            raise ValueError("session template is unavailable") from exc
        if not current_template.is_confirmed or current_template.regions_snapshot_pending:
            raise ValueError("session template mapping is not confirmed")
        if (
            current_template.template_id != int(template_id)
            or current_template.template_fingerprint != str(template_fingerprint)
            or current_template.first_page_role != template_first_page_role
        ):
            raise ValueError("session template changed before scan analysis")
        expected_parity = (
            "odd" if current_template.first_page_role == "front" else "even"
        )
        if front_page_parity is not None and front_page_parity != expected_parity:
            raise ValueError("scan analysis page assignment does not match the template")
        front_page_parity = expected_parity
        resolved_first_page_role = current_template.first_page_role
    else:
        if front_page_parity not in {None, "odd", "even"}:
            raise ValueError("scan analysis page parity is invalid")
        front_page_parity = front_page_parity or "odd"
        resolved_first_page_role = (
            "front" if front_page_parity == "odd" else "back"
        )

    exams_dir = Path(exams_dir)
    if not exams_dir.exists():
        raise FileNotFoundError(f"scan directory does not exist: {exams_dir}")
    if not _list_scan_input_files(exams_dir):
        raise FileNotFoundError("no PDF/JPG/PNG scan files found")

    students = db.students.list_students()
    if not students:
        raise ValueError("student list is empty")

    regions = answer_regions_with_template_source_sizes(db, session_id, data_root=Path(data_root))
    try:
        # 名单本机识别路径不需要远端模型；模型未配置时留空，旧路径再按原样报错。
        llm_client = llm_client_factory()
    except Exception:
        llm_client = None
    scanner = scanner_factory(
        exams_dir=exams_dir,
        llm_client=llm_client,
        ocr_model=_ocr_model_for_client(llm_client),
        enhance_images=bool(enhance_images),
        ocr_workers=ocr_workers,
        name_region=student_name_region_from_regions(regions),
        front_page_parity=front_page_parity,
    )
    analyze = scanner.analyze
    if _accepts_keyword(analyze, "report"):
        analysis = analyze(
            students,
            report=lambda progress, stage, detail="": _report_scan_progress(
                report,
                raise_if_cancelled,
                progress,
                stage,
                detail,
            ),
        )
    else:
        # Existing test and extension scanners only accept the student list.
        analysis = analyze(students)
    _check_cancelled(raise_if_cancelled)
    _report_scan_progress(
        report,
        raise_if_cancelled,
        0.97,
        "生成结果",
        "正在整理匹配结果和异常答卷",
    )
    payload = analysis.to_dict() if isinstance(analysis, ScanAnalysis) else dict(analysis)
    payload["enhance_images"] = bool(enhance_images)
    payload["front_page_parity"] = front_page_parity
    payload["template_first_page_role"] = resolved_first_page_role
    payload["config_revision"] = expected_config_revision
    if bound_template:
        payload["template_id"] = int(template_id)
        payload["template_fingerprint"] = str(template_fingerprint)
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
        with get_answer_region_session_lock(session_work_dir):
            if load_editor_config(db, session_id).revision != expected_config_revision:
                raise ValueError("grading configuration changed during scan analysis")
            if bound_template:
                try:
                    current_template = TemplateUploadService(
                        session_work_dir.parent
                    ).load_current(
                        db=db,
                        session_id=session_id,
                    )
                except (FileNotFoundError, TemplateUploadError) as exc:
                    raise ValueError("session template is unavailable") from exc
                if (
                    not current_template.is_confirmed
                    or current_template.regions_snapshot_pending
                    or current_template.template_id != int(template_id)
                    or current_template.template_fingerprint
                    != str(template_fingerprint)
                    or current_template.first_page_role
                    != template_first_page_role
                ):
                    raise ValueError("session template changed during scan analysis")
            _require_current_scan_batch(session_work_dir, scan_batch_id)
            os.replace(temporary_path, output_path)
            temporary_path = None
            if report is not None:
                # Publishing is the atomic commit point. A cancellation that arrives
                # after this replace must not turn the committed snapshot into a
                # cancelled job.
                report(
                    0.98,
                    "生成结果",
                    f"预检结果已生成，共处理 {int(payload.get('total_pages') or 0)} 页",
                )
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return {
        "session_id": session_id,
        "scan_analysis_path": str(output_path),
        "summary": scan_analysis_summary(payload),
    }


def _accepts_keyword(callable_value: Callable[..., Any], keyword: str) -> bool:
    try:
        parameters = inspect.signature(callable_value).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == keyword
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _report_scan_progress(
    report: Callable[[float, str, str], None] | None,
    raise_if_cancelled: Callable[[], None] | None,
    progress: float,
    stage: str,
    detail: str,
) -> None:
    _check_cancelled(raise_if_cancelled)
    if report is not None:
        report(progress, stage, detail)


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


def _require_current_scan_batch(
    session_work_dir: Path,
    scan_batch_id: str | None,
) -> None:
    if not scan_batch_id:
        return
    manifest_path = Path(session_work_dir) / "scan_upload_batch.json"
    if not manifest_path.exists():
        raise ValueError("scan batch manifest is unavailable")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("scan batch manifest is unavailable") from exc
    if not isinstance(manifest, dict) or str(manifest.get("batch_id") or "") != str(
        scan_batch_id
    ):
        raise ValueError("scan batch changed before analysis could be published")
