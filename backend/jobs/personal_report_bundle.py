"""多人多场个人报告的只读导出；模型客户端不会进入这条路径。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from analysis_report_exporter import AnalysisReportGenerator
from backend.repositories.grading_database import open_grading_repositories
from backend.session_analysis import assemble_session_analysis
from export_names import safe_filename_fragment


def bundle_filename(session_names, student_count: int, scope_label: str, volume_label="本学期") -> str:
    fragment = lambda value: safe_filename_fragment(str(value), "未命名")
    if len(session_names) == 1:
        return f"{fragment(session_names[0])}_个人报告_{fragment(scope_label)}.zip"
    return f"{fragment(volume_label)}_个人报告_{len(session_names)}场_{student_count}人.zip"


def run_personal_report_bundle(*, context, db_path: Path, reports_dir: Path, data_root: Path,
                               exporter_factory=AnalysisReportGenerator) -> dict:
    context.raise_if_cancelled()
    session_ids = context.payload["session_ids"]
    student_ids = set(context.payload["student_ids"])
    total = len(session_ids) * len(student_ids)
    output_root = Path(reports_dir) / "personal_report_bundles"
    output_root.mkdir(parents=True, exist_ok=True)
    result = dict(generated=0, failed=0, skipped=0, missing_items=[])
    db = open_grading_repositories(db_path)
    try:
        with tempfile.TemporaryDirectory(dir=output_root, prefix=f".job-{context.job_id}-") as staging:
            root = Path(staging)
            files = []
            session_names = []
            volume_ids = set()
            completed = 0
            used_names = set()
            for sid in session_ids:
                context.raise_if_cancelled()
                data = assemble_session_analysis(db, sid, data_root=data_root, page_only=True)
                session_names.append(data.session_name)
                volume_ids.add((db.sessions.get_grading_session(sid) or {}).get("curriculum_volume_id"))
                identities = {s.student_id: dict(student_name=s.student_name, student_code=s.student_code,
                              class_name=s.class_name) for s in data.students}
                identities.update({int(s["student_id"]): s for s in data.skipped})
                exporter = exporter_factory(db, root / f"session-{sid}", data_root=data_root,
                    reports_dir=reports_dir, narrative_cache_dir=reports_dir / ".analysis_narrative_cache",
                    llm_client_factory=None)
                exporter.export_session(sid, "personal_analysis_html", student_ids=student_ids, html_only=True,
                    narrative_mode="cache_only", cancel_check=context.raise_if_cancelled,
                    progress_callback=lambda count, base=completed: context.report(
                        0.05 + 0.85 * min(total, base + count) / total,
                        "personal_report_bundle", f"已处理 {min(total, base + count)}/{total} 人次"))
                missing = {int(s["student_id"]): s for s in exporter.last_personal_missing}
                for student_id in sorted(student_ids):
                    identity = identities.get(student_id) or dict(student_name=f"学生{student_id}", student_code=str(student_id), class_name="未分班")
                    file = exporter.last_personal_files.get(student_id)
                    if file is not None:
                        name = (f"{safe_filename_fragment(identity.get('class_name') or '未分班', '未分班')}/"
                                f"{safe_filename_fragment(identity.get('student_code') or str(student_id), '学号')}_"
                                f"{safe_filename_fragment(identity['student_name'], '姓名')}_"
                                f"{safe_filename_fragment(data.session_name, '考试')}_个人报告.html")
                        if name in used_names:
                            name = name.removesuffix(".html") + f"_{student_id}_{sid}.html"
                        used_names.add(name)
                        files.append((Path(file), name))
                        result["generated"] += 1
                    else:
                        reason = (missing.get(student_id) or {}).get("reason") or "本场无成绩"
                        result["failed" if reason == "渲染失败" else "skipped"] += 1
                        result["missing_items"].append(dict(student_id=student_id, student_name=identity["student_name"],
                                                           session_id=sid, session_name=data.session_name, reason=reason))
                    completed += 1
                    context.report(0.05 + 0.85 * completed / total, "personal_report_bundle", f"已处理 {completed}/{total} 人次")
                    context.raise_if_cancelled()
                context.raise_if_cancelled()
            if total == 1 and len(files) == 1:
                staged = files[0][0]
                filename = Path(files[0][1]).name
            else:
                from question_bank.taxonomy.curriculum_catalog import curriculum_volume
                volume = curriculum_volume(volume_id=next(iter(volume_ids))) if len(volume_ids) == 1 else None
                filename = bundle_filename(session_names, len(student_ids), context.payload.get("scope_label") or "指定学生",
                    (volume or {}).get("label") or "本学期")
                staged = root / filename
                lines = ["未导出清单", ""] + [f"{s['student_name']} · {s['session_name']}：{s['reason']}" for s in result["missing_items"]]
                if not result["missing_items"]:
                    lines.append("无")
                with ZipFile(staged, "w", ZIP_DEFLATED) as archive:
                    for file, name in files:
                        file.resolve().relative_to(root.resolve())
                        archive.write(file, name)
                    archive.writestr("未导出清单.txt", "\n".join(lines) + "\n")
            context.raise_if_cancelled()
            target = output_root / f"job-{context.job_id}"
            target.mkdir(exist_ok=True)
            published = target / filename
            os.replace(staged, published)
            result.update(file_path=str(published), filename=filename)
        return result
    finally:
        db.close()
