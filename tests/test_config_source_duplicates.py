"""Post-splitting duplicate pre-check for the active config source."""
from __future__ import annotations

import base64
import io
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from docx import Document
from PIL import Image

from backend.config_workspace.duplicates import source_duplicate_preview
from question_bank.current_knowledge import CurrentFineTermResolver
from question_bank.database.schema import connect, initialize_database
from question_bank.solution_evidence.contracts import QuestionSolutionEvidence
from question_bank.solution_evidence.repository import SolutionEvidenceRepository
from question_bank.training_criteria.adapters import QuestionAnalysisInputLoader
from question_bank.training_criteria.analysis import (
    JUDGMENT_POINTS_SCHEMA,
    solution_evidence_source_content_hash,
)
from question_bank.training_criteria.versioning import TrainingCriterionModule
from backend.repositories.grading_database import open_grading_repositories


class _EmptyResolver:
    def resolve(self, fine_term_id: str) -> object:
        raise KeyError(fine_term_id)


@pytest.fixture(autouse=True)
def empty_fine_term_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        CurrentFineTermResolver,
        "from_active_database",
        classmethod(lambda cls, _db_path: _EmptyResolver()),
    )


@pytest.fixture()
def bank(tmp_path: Path) -> dict[str, Path]:
    data_root = tmp_path / "user_data"
    db_path = data_root / "databases" / "question_bank.db"
    initialize_database(db_path)
    return {"db": db_path, "data_root": data_root}


def _png(color: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 12), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _insert_bank_questions(db_path: Path, rows: list[tuple]) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers(id,title,import_status) VALUES (1,'题库原卷','ready')"
        )
        conn.executemany(
            """INSERT INTO questions(
                   id,paper_id,question_number,question_text,answer_text,has_images
               ) VALUES (?,1,?,?,?,?)""",
            rows,
        )


def _seed_analysis(db_path: Path, data_root: Path, question_id: int) -> None:
    loader = QuestionAnalysisInputLoader(db_path=db_path, data_root=data_root)
    question_input = loader.load((question_id,))[0]
    evidence = QuestionSolutionEvidence.from_model_dict(
        {
            "schema_version": "question-solution-evidence-v2",
            "question_id": question_id,
            "parts": [
                {
                    "part_id": "part-1",
                    "label": "主问",
                    "response_mode": "process_required",
                    "canonical_answer": str(
                        question_input.tagging_context.answer_text or ""
                    ),
                    "accepted_forms": [],
                    "full_answer": "完整解答",
                    "proof_obligations": [],
                    "visual_requirements": [],
                    "deduction_policy": ["缺少某台阶只影响该台阶"],
                    "allow_alternative_methods": True,
                    "evidence_points": [
                        {
                            "evidence_point_id": "part-1-step-1",
                            "step_index": 1,
                            "target": "写出答案",
                            "justification": "直接计算",
                            "answer_anchor": "答案",
                            "observable_evidence": "写出答案",
                            "depends_on": [],
                            "fine_term_links": [],
                            "equivalent_rules": [],
                            "counterexamples": [],
                        }
                    ],
                }
            ],
            "auxiliary_rules": [],
            "rationale": "测试证据",
            "confidence": 0.9,
        },
        question_id=question_id,
        source_content_hash=solution_evidence_source_content_hash(question_input),
        resolver=_EmptyResolver(),
    )
    SolutionEvidenceRepository(db_path).save(
        evidence,
        source_kind="combined_model",
        source_reference="test-seed",
        created_by="tester",
    )
    TrainingCriterionModule(db_path).propose(
        question=question_input,
        draft={
            "schema_version": JUDGMENT_POINTS_SCHEMA,
            "question_id": question_id,
            "points": [
                {
                    "point_id": "jp-1",
                    "target": "写出正确答案",
                    "observable_evidence": "答案正确",
                    "equivalent_rules": [],
                    "counterexamples": [],
                    "depends_on": [],
                }
            ],
            "auxiliary_rules": [],
            "rationale": "测试判定点",
            "confidence": 0.9,
        },
        source_kind="combined_model",
        source_reference="test-seed",
        actor_ref="tester",
        reason="seed",
    )


def _service(
    questions: list[dict[str, object]],
    *,
    images: dict[str, dict[str, str]] | None = None,
) -> SimpleNamespace:
    record = SimpleNamespace(
        source_id="0" * 32,
        source_revision="a" * 64,
        private_blocks=list(questions),
        safe_filename="第七周测试.docx",
    )
    prepared = SimpleNamespace(
        confirmed_blocks=list(questions),
        question_images=dict(images or {}),
    )
    return SimpleNamespace(
        load_active_record=lambda *, session_id: record,
        prepare_generation_input=lambda record, decisions, mode, asset_decisions=(): prepared,
    )


def _preview(
    service: SimpleNamespace,
    bank: dict[str, Path],
    *,
    session_id: int = 7,
) -> dict[str, object]:
    return source_duplicate_preview(
        service=service,
        session_id=session_id,
        session_name="第七周测试",
        question_bank_db_path=bank["db"],
        data_root=bank["data_root"],
    )


def _by_kind(items: list[dict[str, object]], question_id: str) -> dict[str, object] | None:
    return next(
        (item for item in items if item["question_id"] == question_id), None
    )


def test_preview_classifies_every_duplicate_kind(bank: dict[str, Path]) -> None:
    image_dir = bank["data_root"] / "question_bank" / "extracted_images"
    image_dir.mkdir(parents=True)
    (image_dir / "bank-q6.png").write_bytes(_png("red"))
    _insert_bank_questions(
        bank["db"],
        [
            (1, "1", "这是长度足够的测试题目", "42", 0),
            (2, "2", "解方程3x+5=11", "x=2", 0),
            (3, "3", "求三角形面积是多少", "13", 0),
            (4, "4", "小明有5个苹果又买来2个共有几个", "7", 0),
            (5, "5", "某工厂原计划每天生产零件200个", "200", 0),
            (
                6,
                "6",
                "求阴影部分面积[[IMAGE:question_bank/extracted_images/bank-q6.png]]",
                "12",
                1,
            ),
            (7, "7", "判断题圆的直径是半径的两倍", "对", 0),
        ],
    )
    _seed_analysis(bank["db"], bank["data_root"], 1)
    with connect(bank["db"]) as conn:
        conn.execute(
            """INSERT INTO grading_question_links(
                   grading_session_id, source_question_id, bank_question_id,
                   link_method, confidence, status
               ) VALUES ('7', 'Q7', 7, 'exact_text', 1.0, 'confirmed')"""
        )

    service = _service(
        [
            {"question_id": "Q1", "question_number": "1",
             "question_text": "这是长度足够的测试题目", "answer_text": "42"},
            {"question_id": "Q2", "question_number": "2",
             "question_text": "解方程3x+5=11", "answer_text": "x=2"},
            {"question_id": "Q3", "question_number": "3",
             "question_text": "求三角形面积是多少", "answer_text": "12"},
            {"question_id": "Q4", "question_number": "4",
             "question_text": "小明有3个苹果又买来2个共有几个", "answer_text": "5"},
            {"question_id": "Q5", "question_number": "5",
             "question_text": "某工厂原计划每天生产零件200个（课堂练习）",
             "answer_text": "200"},
            {"question_id": "Q6", "question_number": "6",
             "question_text": "求阴影部分面积", "answer_text": "12"},
            {"question_id": "Q7", "question_number": "7",
             "question_text": "判断题圆的直径是半径的两倍", "answer_text": "对"},
            {"question_id": "Q8", "question_number": "8",
             "question_text": "完全无关的新题询问太阳直径大约多少千米",
             "answer_text": "139万"},
        ],
        images={"Q6": {"question": base64.b64encode(_png("blue")).decode()}},
    )

    result = _preview(service, bank)
    items = result["items"]
    kinds = {item["question_id"]: item["kind"] for item in items}

    assert result["source_id"] == "0" * 32
    assert result["source_revision"] == "a" * 64
    assert kinds["Q1"] == "exact_reusable"
    assert kinds["Q2"] == "exact_needs_analysis"
    assert kinds["Q3"] == "answer_conflict"
    assert kinds["Q4"] == "variant"
    assert kinds["Q5"] == "suspected"
    assert kinds["Q6"] == "image_uncertain"
    assert kinds["Q7"] == "same_session"
    assert kinds.get("Q8") is None

    reusable = _by_kind(items, "Q1")
    assert reusable["matched_question_id"] == 1
    assert reusable["matched_paper_title"] == "题库原卷"
    assert reusable["matched_question_number"] == "1"
    assert "长度足够" in reusable["matched_question_excerpt"]
    assert "复用" in reusable["reason"]

    conflict = _by_kind(items, "Q3")
    assert conflict["bank_answer_text"] == "13"
    assert conflict["matched_question_id"] == 3

    uncertain = _by_kind(items, "Q6")
    assert uncertain["matched_question_id"] == 6

    same = _by_kind(items, "Q7")
    assert same["matched_question_id"] == 7

    variant = _by_kind(items, "Q4")
    assert 0.7 <= variant["similarity"] <= 1.0


def _docx_bytes(*paragraphs: str) -> bytes:
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_duplicates_endpoint_reports_matches(
    tmp_path: Path,
    bank: dict[str, Path],
) -> None:
    from fastapi.testclient import TestClient

    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_config_source_service,
        get_data_root,
        get_grading_db,
        get_job_manager,
        get_question_bank_db_path,
        get_upload_config_dir,
    )
    from backend.config_workspace.sources import ConfigSourceService
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    

    _insert_bank_questions(
        bank["db"],
        [(1, "1", "这是长度足够的测试题目", "42", 0)],
    )
    _seed_analysis(bank["db"], bank["data_root"], 1)

    db = open_grading_repositories(tmp_path / "grading.db")
    db.initialize()
    rubric = tmp_path / "rubric.json"
    answer = tmp_path / "answer.json"
    rubric.write_text("{}", encoding="utf-8")
    answer.write_text("{}", encoding="utf-8")
    session_id = db.sessions.create_grading_session("第七周测试", str(rubric), str(answer))
    upload_root = tmp_path / "uploaded"
    app = create_app()
    manager = JobManager(JobStore(db.db_path), max_workers=1)
    app.dependency_overrides[get_grading_db] = lambda: db
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_upload_config_dir] = lambda: upload_root
    app.dependency_overrides[get_config_source_service] = (
        lambda: ConfigSourceService(upload_root)
    )
    app.dependency_overrides[get_data_root] = lambda: bank["data_root"]
    app.dependency_overrides[get_question_bank_db_path] = lambda: bank["db"]
    client = TestClient(app)

    missing = client.get(
        f"/api/sessions/{session_id}/config/sources/active/duplicates"
    )
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "config_source_changed"

    uploaded = client.post(
        f"/api/sessions/{session_id}/config/sources",
        content=_docx_bytes(
            "1. 这是长度足够的测试题目",
            "2. 完全无关的新题询问太阳直径大约多少千米",
            "答案和解析",
            "1.【答案】42",
            "2.【答案】139万",
        ),
        headers={
            "content-type": "application/octet-stream",
            "x-upload-filename": quote("第七周测试.docx"),
        },
    )
    assert uploaded.status_code == 201
    source = uploaded.json()

    response = client.get(
        f"/api/sessions/{session_id}/config/sources/active/duplicates"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source_id"] == source["source_id"]
    assert body["source_revision"] == source["source_revision"]
    kinds = {item["question_id"]: item["kind"] for item in body["items"]}
    assert kinds == {"Q1": "exact_reusable"}
    match = body["items"][0]
    assert match["matched_question_id"] == 1
    assert match["matched_paper_title"] == "题库原卷"
    assert "长度足够" in match["matched_question_excerpt"]


def test_exact_preview_skips_near_profiles_and_refreshes_changed_content(bank, monkeypatch):
    import question_bank.importers.batch_importer as importer
    import question_bank.services.duplicate_analysis_copy_service as identity

    _insert_bank_questions(bank["db"], [(1, "1", "计算三个连续整数的和是多少", "12", 0)])
    service = _service([{"question_id": "Q1", "question_number": "1",
                         "question_text": "计算三个连续整数的和是多少", "answer_text": "12"}])
    checks = []
    original = identity.ensure_content_index

    def checked(*args, **kwargs):
        checks.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(identity, "ensure_content_index", checked)
    original_profiles = importer._load_near_duplicate_questions
    profiles = []

    def load_profiles(conn):
        profiles.append(1)
        return original_profiles(conn)

    monkeypatch.setattr(importer, "_load_near_duplicate_questions", load_profiles)
    assert _preview(service, bank)["items"][0]["kind"] == "exact_needs_analysis"
    assert len(checks) == 1
    assert profiles == []
    with connect(bank["db"]) as conn:
        conn.execute("UPDATE questions SET question_text='判断正方形对角线的性质' WHERE id=1")
    assert _preview(service, bank)["items"] == []
    assert len(checks) == 2
    assert len(profiles) == 1
