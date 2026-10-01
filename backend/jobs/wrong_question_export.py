from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from backend.jobs.manager import JobCancellationRequested, JobContext
from backend.repositories.grading_database import open_grading_repositories
from backend.students.wrong_question_book import build_wrong_question_books
from question_bank.exporters.export_config import ExportConfig
from question_bank.exporters.paper_docx_exporter import export_question_paper_docx
from question_bank.services.assembly_basket_state import SectionSpec


def _filename_part(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip(' .') or '未命名'


def run_wrong_question_export(
    *, context: JobContext, db_path: Path, question_bank_db_path: Path,
    reports_dir: Path, docx_exporter=export_question_paper_docx,
) -> dict:
    context.raise_if_cancelled()
    context.report(0.02, "wrong_question_export", "正在读取错题")
    db = open_grading_repositories(db_path)
    try:
        plan = build_wrong_question_books(
            db, question_bank_db_path, context.payload["student_ids"],
            context.payload["curriculum_volume_id"], context.payload["session_ids"],
        )
    finally:
        db.close()
    selected = set(plan["session_ids"])
    exam_range = "、".join(item["session_name"] for item in plan["sessions"] if item["session_id"] in selected)
    output_root = Path(reports_dir) / "wrong_question_books"
    output_root.mkdir(parents=True, exist_ok=True)
    result = dict(generated_students=[], empty_students=[], failed_students=[], missing_items=plan["missing_items"], question_count=0)
    with tempfile.TemporaryDirectory(dir=output_root, prefix=f".job-{context.job_id}-") as staging:
        staging_root = Path(staging)
        files = []
        for index, book in enumerate(plan["books"]):
            context.raise_if_cancelled()
            student = book["student"]
            student_result = {"student_id": student["id"], "student_name": student["name"]}
            question_ids = [qid for section in book["sections"] for qid in section["question_ids"]]
            if not question_ids:
                result["empty_students"].append(dict(student_result, reason="没有错题" if not book["wrong_count"] else "错题均缺少题库原题"))
            else:
                student_root = staging_root / str(student["id"])
                student_root.mkdir()
                try:
                    file = Path(docx_exporter(
                        question_bank_db_path, question_ids, student_root,
                        title=f"{student['name']} 错题本", include_answer=True,
                        sections=[SectionSpec(title=section["session_name"], question_ids=section["question_ids"]) for section in book["sections"]],
                        config=ExportConfig(answer_key_position="end"),
                        include_answer_space=False, include_student_fields=False,
                        page_header_text=f"{plan['semester_label']} · 考试范围：{exam_range}",
                    ))
                    file.resolve(strict=True).relative_to(student_root.resolve())
                    if file.suffix.lower() != ".docx":
                        raise ValueError("exporter did not create Word")
                    filename = f"{_filename_part(student['student_code'])}_{_filename_part(student['name'])}_错题本.docx"
                    target = student_root / filename
                    if file != target:
                        os.replace(file, target)
                    files.append(target)
                    result["generated_students"].append(dict(student_result, question_count=len(question_ids)))
                    result["question_count"] += len(question_ids)
                except JobCancellationRequested:
                    raise
                except Exception:
                    result["failed_students"].append(dict(student_result, reason="Word 生成失败，其他学生继续导出"))
            context.report(0.05 + 0.85 * (index + 1) / len(plan["books"]), "wrong_question_export", f"已处理 {index + 1}/{len(plan['books'])} 名学生")
        if not files:
            return result
        if len(plan["students"]) == 1:
            staged = files[0]
            filename = staged.name
        else:
            classes = {student.get("class_name") for student in plan["students"] if student.get("class_name")}
            class_name = next(iter(classes)) if len(classes) == 1 else "所选学生"
            filename = f"{_filename_part(class_name)}_{datetime.now():%Y-%m-%d}_错题本.zip"
            staged = staging_root / filename
            with ZipFile(staged, "w", ZIP_DEFLATED) as archive:
                used_names = set()
                for file in files:
                    entry = file.name if file.name not in used_names else f"{file.stem}_{file.parent.name}.docx"
                    used_names.add(entry)
                    archive.write(file, entry)
        context.raise_if_cancelled()
        published_root = output_root / f"job-{context.job_id}"
        published_root.mkdir(exist_ok=True)
        published = published_root / filename
        os.replace(staged, published)
        result.update(file_path=str(published), filename=filename)
    return result
