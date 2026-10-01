from __future__ import annotations

from pathlib import Path

from backend.jobs.default_handlers import register_default_job_handlers
from backend.jobs.assembly_export import run_assembly_export_job
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore
from question_bank.models.question import QuestionCreate
from question_bank.services.assembly_workspace_service import AssemblyWorkspaceService
from tests.question_bank_support import QuestionBankTestStore


def test_assembly_export_job_publishes_markdown_records_and_clears_same_draft(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    db_path = data_root / "databases" / "question_bank.db"
    question_service = QuestionBankTestStore(db_path)
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


def test_word_export_compact_book_preserves_math_images_and_final_answers(tmp_path: Path):
    from docx import Document
    from PIL import Image, ImageDraw
    from zipfile import ZipFile
    from question_bank.exporters.paper_docx_exporter import export_question_paper_docx
    from question_bank.services.assembly_basket_state import SectionSpec

    db_path = tmp_path / "data" / "databases" / "question_bank.db"
    bank = QuestionBankTestStore(db_path)
    image = tmp_path / "data" / "question_bank" / "assets" / "test_figure.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    figure = Image.new("RGB", (180, 100), "white")
    drawing = ImageDraw.Draw(figure)
    drawing.line([(25, 80), (90, 15), (155, 80), (25, 80)], fill="black", width=2)
    for point, label in [((15, 80), "A"), ((88, 2), "B"), ((158, 80), "C")]:
        drawing.text(point, label, fill="black")
    figure.save(image)
    first = bank.add_question(QuestionCreate(
        question_number="1", question_type="选择题", question_text="测试选择题 A. 1 B. 2 C. 3 D. 4", answer_text="测试选择答案 A",
    ))
    second = bank.add_question(QuestionCreate(
        question_number="10", question_type="解答题", question_text="测试几何原题 求 $x^2+1$，图见下方。",
        answer_text="测试几何解析 代入 $x=2$ 得到 $5$。", image_paths=[str(image)],
    ))
    sections = [SectionSpec(title="早期考试", question_ids=[first]), SectionSpec(title="后期考试", question_ids=[second])]
    compact = export_question_paper_docx(db_path, [first, second], tmp_path / "compact", title="测试学生 错题本", include_answer=True,
        sections=sections, include_answer_space=False, include_student_fields=False, page_header_text="八年级上学期 · 早期考试、后期考试")
    default = export_question_paper_docx(db_path, [first, second], tmp_path / "default", title="默认练习", include_answer=True, sections=sections)
    doc = Document(compact)
    text = "\n".join(p.text for p in doc.paragraphs)
    assert text.index("测试几何原题") < text.index("答案") < text.index("测试几何解析")
    assert "姓名：" not in text
    assert "姓名：" in "\n".join(p.text for p in Document(default).paragraphs)
    assert len(doc.tables) < len(Document(default).tables)
    assert "八年级上学期" in doc.sections[0].header.paragraphs[0].text
    with ZipFile(compact) as archive:
        assert b"<m:oMath" in archive.read("word/document.xml")
        assert any(name.startswith("word/media/") for name in archive.namelist())


def test_assembly_export_cancel_after_generation_does_not_publish_or_clear_draft(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    db_path = data_root / "databases" / "question_bank.db"
    question_service = QuestionBankTestStore(db_path)
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
