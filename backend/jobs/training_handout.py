from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path

from backend.jobs.manager import JobContext
from question_bank.exporters.paper_docx_exporter import export_question_paper_docx
from question_bank.recommendation.personalized import (
    PersonalizedRecommendationModule,
    RecommendationRevisionConflict,
)


def checked_handout_draft(module: PersonalizedRecommendationModule, draft_id: str,
                          expected_revision: int) -> dict:
    draft = module.ensure_current(draft_id)
    if draft["revision"] != expected_revision:
        raise RecommendationRevisionConflict(expected_revision, draft["revision"])
    if draft.get("config", {}).get("purpose", "training") != "handout":
        raise ValueError("请使用讲义草稿导出讲义。")
    personal_remediation = draft.get("config", {}).get("remediation_only") and draft.get("config", {}).get("paper_mode", "individual") == "individual"
    if not draft["students"] or not any(student["items"] for student in draft["students"]):
        raise ValueError("当前没有可导出的题目，请核对补弱依据与候选缺口。")
    if not personal_remediation and any(not student["items"] for student in draft["students"]):
        raise ValueError("讲义草稿含空卷，请调整规则后重新生成。")
    return draft


def run_training_handout_export(*, context: JobContext, question_bank_db_path: Path,
                                data_root: Path, reports_dir: Path) -> dict[str, object]:
    module = PersonalizedRecommendationModule(db_path=question_bank_db_path, data_root=data_root)
    draft_id = str(context.payload["draft_id"])
    revision = int(context.payload["expected_revision"])
    draft = checked_handout_draft(module, draft_id, revision)
    shared = draft["config"].get("paper_mode") == "shared"
    students = draft["students"][:1] if shared else [student for student in draft["students"] if student["items"]]
    skipped = 0 if shared else len(draft["students"]) - len(students)
    output_root = Path(reports_dir) / "training_handouts"
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_root, prefix=f".job-{context.job_id}-") as staging:
        staging_root = Path(staging)
        paths = []
        for index, student in enumerate(students, 1):
            context.raise_if_cancelled()
            # Each student gets a separate directory, so repeated names cannot overwrite a file.
            student_root = staging_root / str(index)
            student_root.mkdir()
            label = "小组" if shared else str(student.get("student_name") or student["student_id"])
            ids = [int(item["question_id"]) for item in sorted(student["items"], key=lambda item: item["item_order"])]
            path = export_question_paper_docx(question_bank_db_path, ids, student_root,
                title=f"{label} 讲义", include_answer=True, grouped_by_type=False)
            paths.append(path)
            context.report(.9 * index / len(students), "handout_export", "正在生成讲义")
        if shared:
            staged = paths[0]
            filename = f"小组讲义-{context.job_id}.docx"
        else:
            filename = f"个性化讲义-{context.job_id}.zip"
            staged = staging_root / filename
            with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for index, path in enumerate(paths, 1):
                    archive.write(path, f"{index:03d}-{path.name}")
        # Sources/revision may change while Word files are rendering. Publish only the checked content.
        checked_handout_draft(module, draft_id, revision)
        context.raise_if_cancelled()
        published = output_root / filename
        os.replace(staged, published)
    return {"file_path": str(published), "filename": filename,
            "format": "docx" if shared else "zip", "paper_count": len(students),
            "skipped_student_count": skipped,
            "question_count": sum(len(student["items"]) for student in students)}
