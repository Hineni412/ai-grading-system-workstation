from __future__ import annotations

from pathlib import Path

from backend.jobs.default_handlers import register_default_job_handlers
from backend.jobs.assembly_export import run_assembly_export_job
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from question_bank.models.question import QuestionCreate
from question_bank.services.assembly_workspace_service import AssemblyWorkspaceService
from question_bank.services.question_service import QuestionService


def test_assembly_export_job_publishes_markdown_records_and_clears_same_draft(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    db_path = data_root / "databases" / "question_bank.db"
    question_service = QuestionService(db_path)
    question_id = question_service.add_question(
        QuestionCreate(
            question_number="1",
            question_type="选择题",
            question_text="（5分）匿名题目",
            answer_text="A",
        )
    )
    workspace = AssemblyWorkspaceService(data_root)
    empty = workspace.load_draft()
    draft = workspace.save_draft(
        expected_revision=empty.revision,
        draft={
            "basket_ids": [question_id],
            "order_ids": [question_id],
            "sections": [],
            "title": "匿名练习",
            "header_text": "",
            "include_answer": True,
            "layout_mode": "sequential",
            "preview_mode": "student",
        },
    )
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    try:
        register_default_job_handlers(
            manager,
            db_path=tmp_path / "grading.db",
            reports_dir=tmp_path / "reports",
            data_root=data_root,
            question_bank_db_path=db_path,
        )
        job = manager.submit(
            "assembly_export",
            {
                "draft_revision": draft.revision,
                "draft": draft.to_payload(),
                "format": "markdown",
            },
        )
        manager.wait(job.id, timeout=5)

        loaded = manager.get(job.id)
        assert loaded is not None
        assert loaded.status == "succeeded"
        output = Path(loaded.result["file_path"])
        assert output.is_file()
        assert output.parent == workspace.exports_root
        assert loaded.result["filename"] == output.name
        assert loaded.result["record_id"]
        assert loaded.result["question_count"] == 1
        assert loaded.result["draft_cleared"] is True
        assert workspace.load_draft().basket_ids == ()
        assert workspace.list_records()[0].id == loaded.result["record_id"]
        assert not list(workspace.exports_root.glob(".job-*"))
    finally:
        manager.shutdown()


def test_assembly_export_cancel_after_generation_does_not_publish_or_clear_draft(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    db_path = data_root / "databases" / "question_bank.db"
    question_service = QuestionService(db_path)
    question_id = question_service.add_question(
        QuestionCreate(
            question_number="1",
            question_type="选择题",
            question_text="（5分）匿名题目",
            answer_text="A",
        )
    )
    workspace = AssemblyWorkspaceService(data_root)
    empty = workspace.load_draft()
    draft = workspace.save_draft(
        expected_revision=empty.revision,
        draft={
            "basket_ids": [question_id],
            "order_ids": [question_id],
            "sections": [],
            "title": "取消验证",
            "header_text": "",
            "include_answer": True,
            "layout_mode": "sequential",
            "preview_mode": "student",
        },
    )

    def runner(**kwargs: object) -> dict[str, object]:
        context = kwargs["context"]

        def cancelling_markdown_exporter(
            _db_path: Path,
            _question_ids: list[int],
            output_dir: Path,
            **_export_kwargs: object,
        ) -> str:
            output = output_dir / "cancelled.md"
            output.write_text("# cancelled", encoding="utf-8")
            context.store.request_cancel(context.job_id)
            return str(output)

        return run_assembly_export_job(
            **kwargs,
            markdown_exporter=cancelling_markdown_exporter,
        )

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    try:
        register_default_job_handlers(
            manager,
            db_path=tmp_path / "grading.db",
            reports_dir=tmp_path / "reports",
            data_root=data_root,
            question_bank_db_path=db_path,
            assembly_export_runner=runner,
        )
        job = manager.submit(
            "assembly_export",
            {
                "draft_revision": draft.revision,
                "draft": draft.to_payload(),
                "format": "markdown",
            },
        )
        manager.wait(job.id, timeout=5)

        loaded = manager.get(job.id)
        assert loaded is not None
        assert loaded.status == "cancelled"
        assert not list(workspace.exports_root.glob("*_job-*.md"))
        assert workspace.list_records() == []
        assert workspace.load_draft().basket_ids == (question_id,)
        assert not list(workspace.exports_root.glob(".job-*"))
    finally:
        manager.shutdown()
