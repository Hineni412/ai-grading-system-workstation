from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from session_originals import (
    OriginalPagesCleared, clear_session_originals, clear_legacy_annotations,
    originals_state, receipt_path,
    release_session_scans, storage_overview,
)
from tests.test_review_media_service import _seed_media


def _write(path: Path, content: bytes = b"test-original") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _scans(seed):
    directory = seed.exams_dir / f"session_{seed.session_id}" / "scan_batches" / "batch" / "files"
    pdf = _write(directory / "scan.pdf")
    pages = directory / "_pdf_pages" / "scan"
    _write(pages / "source_manifest.json", b'{"page_count": 1}')
    image = _write(pages / "page_0001.jpg")
    unrendered = _write(directory / "not-rendered.pdf")
    full_class = _write(seed.templates_dir / f"session_{seed.session_id}" / "template-versions" / "v1" / "template_source_full_class.pdf")
    return pdf, image, unrendered, full_class


def test_legacy_cleanup_includes_orphan_directory_and_invalidates_missing_relative_paths(tmp_path):
    seed = _seed_media(tmp_path)
    old = _write(seed.data_root / "annotated" / "session_1" / "test_annotated.jpg")
    orphan = _write(seed.data_root / "annotated" / "session_999" / "test_annotated.jpg")
    cached = _write(seed.data_root / "cache" / "annotated_pages" / "session_1" / "test.jpg")
    unrelated = _write(old.parent / "teacher-notes.txt")
    seed.db.reviews.upsert_annotated_result(seed.session_id, seed.result_id, "annotated/session_1/missing_annotated.jpg", str(cached))
    expected = old.stat().st_size + orphan.stat().st_size
    result = clear_legacy_annotations(seed.db, seed.data_root)
    assert result == {"freed_bytes": expected, "deleted_files": 2}
    row = seed.db.reviews.get_annotated_result(seed.result_id)
    assert row["annotated_front_path"] is None
    assert row["annotated_back_path"] == str(cached)
    assert cached.exists() and unrelated.exists()
    assert clear_legacy_annotations(seed.db, seed.data_root)["freed_bytes"] == 0


def test_release_keeps_working_pages_json_unrendered_and_shared_files(tmp_path):
    seed = _seed_media(tmp_path)
    pdf, image, unrendered, full_class = _scans(seed)
    expected = pdf.stat().st_size + full_class.stat().st_size
    result = release_session_scans(seed.db, seed.data_root, seed.session_id)
    assert result == {"originals_state": "scans_released", "freed_bytes": expected, "deleted_files": 2, "kept_unrendered": 1}
    assert not pdf.exists() and not full_class.exists()
    assert image.exists() and unrendered.exists() and seed.front_path.exists()
    assert (image.parent / "source_manifest.json").exists()
    assert release_session_scans(seed.db, seed.data_root, seed.session_id)["freed_bytes"] == 0
    shared = seed.db.sessions.create_grading_session("Shared test", "rubric.json", "answer.json")
    with sqlite3.connect(seed.db.db_path) as conn:
        conn.execute("INSERT INTO exam_papers (session_id, front_image, back_image, match_status) VALUES (?, ?, ?, 'unmatched')", (shared, str(image), str(image)))
    clear_session_originals(seed.db, seed.data_root, seed.session_id, clear_crop_cache=lambda: 0)
    assert image.exists()


def test_clear_preserves_score_rows_locks_json_and_templates(tmp_path):
    seed = _seed_media(tmp_path)
    _scans(seed)
    seed.db.reviews.confirm_teacher_score_lock(
        session_id=seed.session_id, scan_batch_id="test-batch", student_id=1,
        question_id="Q1", score_awarded=9, max_score=10, deduction_reason="老师确认",
        source_target_type="session_detail", source_target_id=seed.detail_id, expected_revision=0,
    )
    legacy = _write(seed.data_root / "annotated" / "session_1" / "front_annotated.jpg")
    cached = _write(seed.data_root / "cache" / "annotated_pages" / "session_1" / "back.jpg")
    seed.db.reviews.upsert_annotated_result(seed.session_id, seed.result_id, str(legacy), str(cached))
    sidecar = _write(seed.front_path.parent / "scan_analysis_latest.json", b'{"groups": []}')
    outside = _write(tmp_path / "outside.jpg")
    with sqlite3.connect(seed.db.db_path) as conn:
        conn.execute("UPDATE exam_papers SET back_image=?", (str(outside),))
        before = {name: conn.execute(f"SELECT * FROM {name}").fetchall()
                  for name in ("session_results", "session_details", "teacher_score_locks")}
    result = clear_session_originals(seed.db, seed.data_root, seed.session_id, clear_crop_cache=lambda: 0)
    assert result["originals_state"] == "cleared"
    assert not seed.front_path.exists() and not legacy.exists() and not cached.exists()
    assert outside.exists() and sidecar.exists()
    assert (seed.templates_dir / "session_1" / "template_front.jpg").exists()
    with sqlite3.connect(seed.db.db_path) as conn:
        assert {name: conn.execute(f"SELECT * FROM {name}").fetchall() for name in before} == before
        assert conn.execute("SELECT annotated_front_path, annotated_back_path FROM annotated_results").fetchone() == (None, None)
    receipt = json.loads(receipt_path(seed.data_root, seed.session_id).read_text(encoding="utf-8"))
    assert receipt["state"] == "cleared" and sum(receipt["freed_bytes"].values()) == result["freed_bytes"]
    assert clear_session_originals(seed.db, seed.data_root, seed.session_id, clear_crop_cache=lambda: 0)["freed_bytes"] == 0
    for variant in ("original", "annotated"):
        with pytest.raises(OriginalPagesCleared):
            seed.service.resolve_result_page(seed.session_id, seed.result_id, "front", variant=variant)
    with pytest.raises(OriginalPagesCleared):
        seed.service.render_detail_crop(seed.session_id, seed.result_id, seed.detail_id)


def test_interrupted_clear_can_resume_and_bad_receipt_blocks_media(tmp_path, monkeypatch):
    seed = _seed_media(tmp_path)
    real_unlink = Path.unlink
    def interrupted(path, *args, **kwargs):
        if path == seed.front_path:
            raise OSError("test file busy")
        return real_unlink(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupted)
        with pytest.raises(OSError):
            clear_session_originals(seed.db, seed.data_root, seed.session_id, clear_crop_cache=lambda: 0)
    assert originals_state(seed.data_root, seed.session_id) == "clearing"
    assert clear_session_originals(seed.db, seed.data_root, seed.session_id, clear_crop_cache=lambda: 0)["originals_state"] == "cleared"
    receipt_path(seed.data_root, seed.session_id).write_text("broken", encoding="utf-8")
    assert originals_state(seed.data_root, seed.session_id) == "clearing"


def test_storage_categories_add_up_without_writes(tmp_path):
    directories = ["exams/session_1/a.jpg", "annotated/session_1/a_annotated.jpg", "cache/annotated_pages/session_1/a.jpg", "question_bank/a", "reports/a", "backups/a", "databases/a", "templates/session_1/a.jpg"]
    for name in directories:
        _write(tmp_path / name, b"123")
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    overview = storage_overview(tmp_path)
    assert overview["total_bytes"] == 24
    assert {item["key"]: item["bytes"] for item in overview["categories"]} == {"originals": 3, "annotations": 6, "question_bank": 3, "reports": 3, "backups": 3, "databases": 3, "other": 3}
    assert overview["legacy_annotations"] == {"bytes": 3, "files": 1}
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before


def test_storage_reports_unreadable_directory_without_breaking_readable_totals(tmp_path, monkeypatch):
    import session_originals
    _write(tmp_path / "exams" / "test.jpg", b"123")
    blocked = tmp_path / "question_bank" / "test-busy"
    blocked.mkdir(parents=True)
    real_scandir = session_originals.os.scandir
    def scan(directory):
        if Path(directory) == blocked:
            raise PermissionError("test inaccessible staging")
        return real_scandir(directory)
    monkeypatch.setattr(session_originals.os, "scandir", scan)
    overview = storage_overview(tmp_path)
    assert overview["total_bytes"] == 3
    assert overview["unreadable_count"] == 1
