from __future__ import annotations

import multiprocessing
import sqlite3
import threading
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image


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
    from db_manager import DBManager
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
            DBManager(Path(db_path)),
            Path(annotated_dir),
        ).render_result_annotation(result_id, highlight_qids=["Q1"])
    finally:
        completed.set()


def _seed_media(tmp_path: Path) -> SeededMedia:
    from backend.media.service import ReviewMediaService
    from db_manager import DBManager

    data_root = tmp_path / "data"
    exams_dir = data_root / "exams"
    templates_dir = data_root / "templates"
    annotated_dir = data_root / "annotated"
    db_path = data_root / "databases" / "grading.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = DBManager(db_path)
    db.initialize()

    front_path = exams_dir / "session_1" / "front.jpg"
    back_path = exams_dir / "session_1" / "back.jpg"
    template_front = templates_dir / "session_1" / "template_front.jpg"
    template_back = templates_dir / "session_1" / "template_back.jpg"
    for path in (front_path, back_path, template_front, template_back):
        _image(path)

    session_id = db.create_grading_session("Media Exam", "rubric.json", "answer.json")
    template_id = db.upsert_session_template(
        session_id,
        str(template_front),
        str(template_back),
    )
    db.add_answer_region(
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
                    knowledge_id, error_category, error_summary, confidence_score
                ) VALUES (?, 'Q1', 8, '需复核', 'K1', '需复核', 'unclear', 55)
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


def _set_front_path(seed: SeededMedia, path_value: object) -> None:
    with sqlite3.connect(seed.db.db_path) as conn:
        conn.execute(
            """
            UPDATE exam_papers
            SET front_image = ?
            WHERE id = (SELECT paper_id FROM session_results WHERE id = ?)
            """,
            (str(path_value), seed.result_id),
        )
        conn.commit()


def test_review_media_crop_uses_owned_detail_and_existing_region(tmp_path: Path) -> None:
    seed = _seed_media(tmp_path)

    payload = seed.service.render_detail_crop(
        seed.session_id,
        seed.result_id,
        seed.detail_id,
    )

    assert payload.startswith(b"\xff\xd8")
    with Image.open(BytesIO(payload)) as crop:
        assert crop.format == "JPEG"
        assert crop.width < 400
        assert crop.height < 300
        red_pixels = sum(
            1
            for r, g, b in crop.convert("RGB").get_flattened_data()
            if r > 150 and g < 100 and b < 100
        )
        assert red_pixels > 50


def test_review_media_resolves_original_and_annotated_pages(tmp_path: Path) -> None:
    seed = _seed_media(tmp_path)
    annotated_front = seed.annotated_dir / f"session_{seed.session_id}" / "front.jpg"
    annotated_back = seed.annotated_dir / f"session_{seed.session_id}" / "back.jpg"
    _image(annotated_front, color=(240, 220, 220))
    _image(annotated_back, color=(220, 240, 220))
    seed.db.upsert_annotated_result(
        seed.session_id,
        seed.result_id,
        str(annotated_front),
        str(annotated_back),
    )

    original = seed.service.resolve_result_page(
        seed.session_id,
        seed.result_id,
        "front",
        "original",
    )
    annotated = seed.service.resolve_result_page(
        seed.session_id,
        seed.result_id,
        "back",
        "annotated",
    )

    assert original.path == seed.front_path.resolve()
    assert original.media_type == "image/jpeg"
    assert annotated.path == annotated_back.resolve()


def test_review_media_rejects_annotated_row_with_forged_session_owner(
    tmp_path: Path,
) -> None:
    from backend.media.service import ReviewMediaNotFound

    seed = _seed_media(tmp_path)
    foreign_session_id = seed.db.create_grading_session(
        "Foreign media exam",
        "rubric.json",
        "answer.json",
    )
    annotated_front = seed.annotated_dir / "foreign-front.jpg"
    annotated_back = seed.annotated_dir / "foreign-back.jpg"
    _image(annotated_front)
    _image(annotated_back)
    # Simulate a malformed legacy row written before ownership validation existed.
    with sqlite3.connect(seed.db.db_path) as conn:
        conn.execute(
            """
            INSERT INTO annotated_results (
                session_id, result_id, annotated_front_path, annotated_back_path
            ) VALUES (?, ?, ?, ?)
            """,
            (
                foreign_session_id,
                seed.result_id,
                str(annotated_front),
                str(annotated_back),
            ),
        )
        conn.commit()

    with pytest.raises(ReviewMediaNotFound):
        seed.service.resolve_result_page(
            foreign_session_id,
            seed.result_id,
            "front",
            "annotated",
        )


def test_annotated_result_upsert_rejects_foreign_result_owner(tmp_path: Path) -> None:
    seed = _seed_media(tmp_path)
    foreign_session_id = seed.db.create_grading_session(
        "Foreign media exam",
        "rubric.json",
        "answer.json",
    )

    with pytest.raises(ValueError, match="result"):
        seed.db.upsert_annotated_result(
            foreign_session_id,
            seed.result_id,
            str(seed.annotated_dir / "foreign-front.jpg"),
            str(seed.annotated_dir / "foreign-back.jpg"),
        )


def test_annotation_rerender_failure_preserves_previous_published_pair(
    tmp_path: Path,
) -> None:
    from manual_review_service import ManualReviewService

    seed = _seed_media(tmp_path)
    service = ManualReviewService(seed.db, seed.annotated_dir)
    initial_paths = service.render_result_annotation(seed.result_id)
    assert initial_paths is not None
    published_before = seed.db.get_annotated_result(seed.result_id)
    assert published_before is not None
    previous_front = Path(published_before["annotated_front_path"]).read_bytes()
    previous_back = Path(published_before["annotated_back_path"]).read_bytes()

    _image(seed.front_path, color=(40, 180, 220))
    seed.back_path.write_text("not an image", encoding="utf-8")

    with pytest.raises(Exception):
        service.render_result_annotation(seed.result_id)

    published_after = seed.db.get_annotated_result(seed.result_id)
    assert published_after is not None
    assert published_after["annotated_front_path"] == published_before["annotated_front_path"]
    assert published_after["annotated_back_path"] == published_before["annotated_back_path"]
    assert Path(published_after["annotated_front_path"]).read_bytes() == previous_front
    assert Path(published_after["annotated_back_path"]).read_bytes() == previous_back


def test_successful_annotation_rerender_replaces_old_pair_without_orphans(
    tmp_path: Path,
) -> None:
    from manual_review_service import ManualReviewService

    seed = _seed_media(tmp_path)
    service = ManualReviewService(seed.db, seed.annotated_dir)
    first_paths = service.render_result_annotation(seed.result_id)
    assert first_paths is not None
    first_record = seed.db.get_annotated_result(seed.result_id)
    assert first_record is not None

    second_paths = service.render_result_annotation(seed.result_id)
    assert second_paths is not None
    current_record = seed.db.get_annotated_result(seed.result_id)
    assert current_record is not None

    assert current_record["annotated_front_path"] == second_paths["front"]
    assert current_record["annotated_back_path"] == second_paths["back"]
    assert current_record["annotated_front_path"] != first_record["annotated_front_path"]
    assert current_record["annotated_back_path"] != first_record["annotated_back_path"]
    assert not Path(first_record["annotated_front_path"]).exists()
    assert not Path(first_record["annotated_back_path"]).exists()
    assert Path(current_record["annotated_front_path"]).is_file()
    assert Path(current_record["annotated_back_path"]).is_file()
    assert len(
        list(
            (seed.annotated_dir / f"session_{seed.session_id}").glob(
                f"result_{seed.result_id}_*_annotated.jpg"
            )
        )
    ) == 2


def test_concurrent_annotation_renders_publish_latest_state_in_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import manual_review_service as manual_review_module
    from manual_review_service import ManualReviewService

    seed = _seed_media(tmp_path)
    first_render_started = threading.Event()
    release_first_render = threading.Event()
    first_done = threading.Event()
    second_done = threading.Event()
    render_scores: list[int] = []
    results: dict[str, dict[str, str] | None] = {}
    errors: list[BaseException] = []

    def fake_render(**kwargs):
        score = int(float(kwargs["question_scores"]["Q1"]["score_awarded"]))
        if score == 8:
            first_render_started.set()
            assert release_first_render.wait(5)
        output_front = kwargs["output_front"]
        output_back = kwargs["output_back"]
        output_front.parent.mkdir(parents=True, exist_ok=True)
        output_front.write_text(str(score), encoding="utf-8")
        output_back.write_text(str(score), encoding="utf-8")
        render_scores.append(score)
        return output_front, output_back

    monkeypatch.setattr(manual_review_module, "render_annotated_paper", fake_render)

    def render(label: str, done: threading.Event) -> None:
        try:
            results[label] = ManualReviewService(
                seed.db,
                seed.annotated_dir,
            ).render_result_annotation(seed.result_id, highlight_qids=["Q1"])
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            done.set()

    first = threading.Thread(target=render, args=("first", first_done))
    first.start()
    assert first_render_started.wait(3)

    with sqlite3.connect(seed.db.db_path) as conn:
        conn.execute(
            "UPDATE session_details SET score_awarded = 9 WHERE id = ?",
            (seed.detail_id,),
        )
        conn.commit()

    second = threading.Thread(target=render, args=("second", second_done))
    second.start()
    try:
        assert not second_done.wait(0.25)
    finally:
        release_first_render.set()
        first.join(5)
        second.join(5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert render_scores == [8, 9]
    current_record = seed.db.get_annotated_result(seed.result_id)
    assert current_record is not None
    assert Path(current_record["annotated_front_path"]).read_text(encoding="utf-8") == "9"
    assert Path(current_record["annotated_back_path"]).read_text(encoding="utf-8") == "9"
    assert results["first"] is not None
    assert results["second"] is not None


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
    current_record = seed.db.get_annotated_result(seed.result_id)
    assert current_record is not None
    assert Path(current_record["annotated_front_path"]).read_text(encoding="utf-8") == "9"
    assert Path(current_record["annotated_back_path"]).read_text(encoding="utf-8") == "9"


@pytest.mark.parametrize(
    ("session_offset", "result_offset", "detail_offset"),
    [(1, 0, 0), (0, 1, 0), (0, 0, 1)],
)
def test_review_media_rejects_wrong_ownership(
    tmp_path: Path,
    session_offset: int,
    result_offset: int,
    detail_offset: int,
) -> None:
    from backend.media.service import ReviewMediaNotFound

    seed = _seed_media(tmp_path)

    with pytest.raises(ReviewMediaNotFound):
        seed.service.render_detail_crop(
            seed.session_id + session_offset,
            seed.result_id + result_offset,
            seed.detail_id + detail_offset,
        )


def test_review_media_rejects_detail_without_matching_region(tmp_path: Path) -> None:
    from backend.media.service import ReviewMediaNotFound

    seed = _seed_media(tmp_path)
    with sqlite3.connect(seed.db.db_path) as conn:
        detail_id = int(
            conn.execute(
                """
                INSERT INTO session_details (
                    result_id, question_id, score_awarded, deduction_reason, knowledge_id
                ) VALUES (?, 'Q9', 0, 'missing', 'K9')
                """,
                (seed.result_id,),
            ).lastrowid
        )
        conn.commit()

    with pytest.raises(ReviewMediaNotFound):
        seed.service.render_detail_crop(seed.session_id, seed.result_id, detail_id)


def test_review_media_marks_deleted_source_page_expired(tmp_path: Path) -> None:
    from backend.file_access import ControlledFileExpired

    seed = _seed_media(tmp_path)
    seed.front_path.unlink()

    with pytest.raises(ControlledFileExpired):
        seed.service.render_detail_crop(seed.session_id, seed.result_id, seed.detail_id)


def test_review_media_rejects_existing_absolute_source_outside_exams_root(
    tmp_path: Path,
) -> None:
    from backend.file_access import ControlledFileForbidden

    seed = _seed_media(tmp_path)
    outside = tmp_path / "outside.jpg"
    _image(outside)
    _set_front_path(seed, outside)

    with pytest.raises(ControlledFileForbidden):
        seed.service.render_detail_crop(seed.session_id, seed.result_id, seed.detail_id)


def test_review_media_rejects_relative_source_traversal(tmp_path: Path) -> None:
    from backend.file_access import ControlledFileForbidden

    seed = _seed_media(tmp_path)
    outside = seed.data_root / "outside.jpg"
    _image(outside)
    _set_front_path(seed, "exams/../outside.jpg")

    with pytest.raises(ControlledFileForbidden):
        seed.service.render_detail_crop(seed.session_id, seed.result_id, seed.detail_id)


def test_review_media_rejects_disallowed_source_extension(tmp_path: Path) -> None:
    from backend.file_access import ControlledFileTypeError

    seed = _seed_media(tmp_path)
    unsafe = seed.exams_dir / "session_1" / "front.txt"
    unsafe.write_text("not an image", encoding="utf-8")
    _set_front_path(seed, unsafe)

    with pytest.raises(ControlledFileTypeError):
        seed.service.render_detail_crop(seed.session_id, seed.result_id, seed.detail_id)
