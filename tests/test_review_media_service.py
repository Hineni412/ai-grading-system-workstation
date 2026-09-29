from __future__ import annotations

import multiprocessing
import sqlite3
import threading
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from backend.repositories.grading_database import open_grading_repositories


@dataclass(frozen=True)
class SeededMedia:
    service: object
    db: object
    data_root: Path
    exams_dir: Path
    templates_dir: Path
    annotated_dir: Path
    session_id: int
    result_id: int
    detail_id: int
    front_path: Path
    back_path: Path


def _image(path: Path, *, color: tuple[int, int, int] = (245, 245, 245)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 300), color).save(path, format="JPEG", quality=95)


def _render_annotation_in_separate_process(
    db_path: str,
    annotated_dir: str,
    result_id: int,
    worker_ready,
    render_started,
    release_first_render,
    completed,
) -> None:
    import manual_review_service as manual_review_module
    
    from manual_review_service import ManualReviewService

    def fake_render(**kwargs):
        score = int(float(kwargs["question_scores"]["Q1"]["score_awarded"]))
        render_started.set()
        if score == 8:
            if not release_first_render.wait(10):
                raise TimeoutError("first render was not released")
        output_front = kwargs["output_front"]
        output_back = kwargs["output_back"]
        output_front.parent.mkdir(parents=True, exist_ok=True)
        output_front.write_text(str(score), encoding="utf-8")
        output_back.write_text(str(score), encoding="utf-8")
        return output_front, output_back

    manual_review_module.render_annotated_paper = fake_render
    worker_ready.set()
    try:
        ManualReviewService(
            open_grading_repositories(Path(db_path)),
            Path(annotated_dir),
        ).render_result_annotation(result_id, highlight_qids=["Q1"])
    finally:
        completed.set()


def _seed_media(tmp_path: Path) -> SeededMedia:
    from backend.media.service import ReviewMediaService
    

    data_root = tmp_path / "data"
    exams_dir = data_root / "exams"
    templates_dir = data_root / "templates"
    annotated_dir = data_root / "annotated"
    db_path = data_root / "databases" / "grading.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = open_grading_repositories(db_path)
    db.initialize()

    front_path = exams_dir / "session_1" / "front.jpg"
    back_path = exams_dir / "session_1" / "back.jpg"
    template_front = templates_dir / "session_1" / "template_front.jpg"
    template_back = templates_dir / "session_1" / "template_back.jpg"
    for path in (front_path, back_path, template_front, template_back):
        _image(path)

    session_id = db.sessions.create_grading_session("Media Exam", "rubric.json", "answer.json")
    template_id = db.templates.upsert_session_template(
        session_id,
        str(template_front),
        str(template_back),
    )
    db.templates.add_answer_region(
        session_id,
        template_id,
        {
            "region_uuid": "region-q1",
            "page": "front",
            "region_order": 1,
            "x": 80,
            "y": 60,
            "w": 160,
            "h": 100,
            "mapped_question_id": "Q1",
            "mapping_status": "manual",
            "is_confirmed": True,
        },
    )
    with sqlite3.connect(db.db_path) as conn:
        student_id = int(
            conn.execute(
                "INSERT INTO students (student_code, name, class_name) VALUES ('S001', 'Alice', '1班')"
            ).lastrowid
        )
        paper_id = int(
            conn.execute(
                """
                INSERT INTO exam_papers (
                    session_id, front_image, back_image, ocr_name, student_id,
                    match_status, processing_status
                ) VALUES (?, ?, ?, 'Alice', ?, 'matched', 'graded')
                """,
                (session_id, str(front_path), str(back_path), student_id),
            ).lastrowid
        )
        result_id = int(
            conn.execute(
                """
                INSERT INTO session_results (
                    session_id, student_id, paper_id, total_score, student_score,
                    needs_human_review, raw_json
                ) VALUES (?, ?, ?, 10, 8, 1, '{}')
                """,
                (session_id, student_id, paper_id),
            ).lastrowid
        )
        detail_id = int(
            conn.execute(
                """
                INSERT INTO session_details (
                    result_id, question_id, score_awarded, deduction_reason,
                    knowledge_ids, error_category, error_summary, confidence_score
                ) VALUES (?, 'Q1', 8, '需复核', '["K1"]', '需复核', 'unclear', 55)
                """,
                (result_id,),
            ).lastrowid
        )
        conn.commit()

    service = ReviewMediaService(
        db,
        data_root=data_root,
        exams_dir=exams_dir,
        templates_dir=templates_dir,
        annotated_dir=annotated_dir,
        crop_cache_dir=data_root / "cache" / "review_crops",
    )
    return SeededMedia(
        service=service,
        db=db,
        data_root=data_root,
        exams_dir=exams_dir,
        templates_dir=templates_dir,
        annotated_dir=annotated_dir,
        session_id=session_id,
        result_id=result_id,
        detail_id=detail_id,
        front_path=front_path,
        back_path=back_path,
    )


def test_annotation_rerender_failure_preserves_previous_published_pair(
    tmp_path: Path,
) -> None:
    from manual_review_service import ManualReviewService

    seed = _seed_media(tmp_path)
    service = ManualReviewService(seed.db, seed.annotated_dir)
    initial_paths = service.render_result_annotation(seed.result_id)
    assert initial_paths is not None
    published_before = seed.db.reviews.get_annotated_result(seed.result_id)
    assert published_before is not None
    previous_front = Path(published_before["annotated_front_path"]).read_bytes()
    previous_back = Path(published_before["annotated_back_path"]).read_bytes()

    _image(seed.front_path, color=(40, 180, 220))
    seed.back_path.write_text("not an image", encoding="utf-8")

    with pytest.raises(Exception):
        service.render_result_annotation(seed.result_id)

    published_after = seed.db.reviews.get_annotated_result(seed.result_id)
    assert published_after is not None
    assert (
        published_after["annotated_front_path"]
        == published_before["annotated_front_path"]
    )
    assert (
        published_after["annotated_back_path"]
        == published_before["annotated_back_path"]
    )
    assert Path(published_after["annotated_front_path"]).read_bytes() == previous_front
    assert Path(published_after["annotated_back_path"]).read_bytes() == previous_back


def test_annotation_render_lock_prevents_cross_process_stale_publish(
    tmp_path: Path,
) -> None:
    seed = _seed_media(tmp_path)
    context = multiprocessing.get_context("spawn")
    first_worker_ready = context.Event()
    first_render_started = context.Event()
    release_first_render = context.Event()
    first_done = context.Event()
    second_worker_ready = context.Event()
    second_render_started = context.Event()
    second_done = context.Event()
    first = context.Process(
        target=_render_annotation_in_separate_process,
        args=(
            str(seed.db.db_path),
            str(seed.annotated_dir),
            seed.result_id,
            first_worker_ready,
            first_render_started,
            release_first_render,
            first_done,
        ),
    )
    second = None
    first.start()
    try:
        assert first_worker_ready.wait(15)
        assert first_render_started.wait(15)
        with sqlite3.connect(seed.db.db_path) as conn:
            conn.execute(
                "UPDATE session_details SET score_awarded = 9 WHERE id = ?",
                (seed.detail_id,),
            )
            conn.commit()

        second = context.Process(
            target=_render_annotation_in_separate_process,
            args=(
                str(seed.db.db_path),
                str(seed.annotated_dir),
                seed.result_id,
                second_worker_ready,
                second_render_started,
                release_first_render,
                second_done,
            ),
        )
        second.start()
        assert second_worker_ready.wait(15)
        assert not second_render_started.wait(2)
    finally:
        release_first_render.set()
        first.join(15)
        if second is not None:
            second.join(15)
        for worker in (first, second):
            if worker is not None and worker.is_alive():
                worker.terminate()
                worker.join(5)

    assert first.exitcode == 0
    assert second is not None
    assert second.exitcode == 0
    current_record = seed.db.reviews.get_annotated_result(seed.result_id)
    assert current_record is not None
    assert (
        Path(current_record["annotated_front_path"]).read_text(encoding="utf-8") == "9"
    )
    assert (
        Path(current_record["annotated_back_path"]).read_text(encoding="utf-8") == "9"
    )
