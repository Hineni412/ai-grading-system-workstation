from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

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


def _seed_media(tmp_path: Path) -> SeededMedia:
    from backend.media.service import ReviewMediaService


    data_root = tmp_path / "data"
    exams_dir = data_root / "exams"
    templates_dir = data_root / "templates"
    annotated_dir = data_root / "cache" / "annotated_pages"
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
