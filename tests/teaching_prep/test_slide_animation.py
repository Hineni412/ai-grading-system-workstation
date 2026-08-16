from __future__ import annotations

from pathlib import Path

import pytest

from backend.teaching_prep.application.slide_animation import (
    BILLED_LIMIT,
    PAGE_LIMIT,
    normalize_storyboard,
    render_slide_animation_html,
)
from backend.teaching_prep.domain.errors import (
    TeachingPrepStateError,
    TeachingPrepValidationError,
)
from backend.teaching_prep.infrastructure.fakes import FakeSlideAnimationModelAdapter

from .test_a01_foundation import _migrated_service
from .test_a02_catalog import _api_client, _lesson_tree
from .test_a03_material_units import _pptx


def _ready_lesson(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    _curriculum_id, _chapter_id, _section_id, lessons = _lesson_tree(service)
    lesson_id = lessons[0]
    version, _created = service.register_material_file(
        request_token="anim-pptx-register",
        path=_pptx(tmp_path / "anim-primary.pptx"),
        display_name="合成主课件",
    )
    service.parse_material_version(version.id)
    link, _created = service.create_material_link(
        request_token="anim-pptx-link",
        lesson_node_id=lesson_id,
        material_version_id=version.id,
        start_unit=1,
        end_unit=2,
        crop=None,
        purpose="reference_ppt",
        teacher_note=None,
        confirmation_status="confirmed",
    )
    service.slide_animation_model_adapter = FakeSlideAnimationModelAdapter()
    return service, lesson_id, link


def test_storyboard_rejects_markup_and_renderer_escapes_it() -> None:
    with pytest.raises(TeachingPrepValidationError):
        normalize_storyboard(
            {
                "title": "<script>alert(1)</script>",
                "scenes": [
                    {
                        "title": "引入",
                        "narration": "讲解定义。",
                        "duration_ms": 2000,
                        "source_page": 1,
                    }
                ],
            },
            allowed_pages=[1],
        )
    html = render_slide_animation_html(
        storyboard={
            "title": "<script>alert(1)</script>",
            "scenes": [
                {
                    "title": "引入",
                    "narration": "讲解定义。",
                    "duration_ms": 2000,
                    "source_page": 1,
                }
            ],
        },
        images={1: ("image/png", b"\x89PNG\r\n\x1a\n")},
    )
    assert "<script>alert(1)</script>" not in html
    assert "\\u003cscript" in html
    assert "http://" not in html
    assert "https://" not in html
    assert "data:image/png;base64," in html


def test_start_rejects_more_than_four_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, lesson_id, link = _ready_lesson(tmp_path, monkeypatch)
    with pytest.raises(TeachingPrepValidationError):
        service.start_slide_animation_run(
            lesson_id,
            operation_id="anim-too-many-pages",
            confirmed=True,
            material_link_id=link.id,
            page_indexes=[1, 2, 3, 4, 5],
        )


def test_process_writes_offline_html_and_requires_accept_for_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, lesson_id, link = _ready_lesson(tmp_path, monkeypatch)
    run, created = service.start_slide_animation_run(
        lesson_id,
        operation_id="anim-success-operation",
        confirmed=True,
        material_link_id=link.id,
        page_indexes=[1, 2],
    )
    assert created is True
    service.process_slide_animation_run(run.id)
    finished = service.get_slide_animation_run(run.id)
    assert finished.status == "succeeded"
    assert finished.model_call_count == 1
    preview = service.slide_animation_preview_path(run.id).read_text("utf-8")
    assert "合成课堂动画" in preview
    assert "data:image/" in preview
    with pytest.raises(TeachingPrepStateError):
        service.slide_animation_download_path(run.id)
    accepted = service.accept_slide_animation_run(
        run.id,
        expected_revision=finished.revision,
    )
    assert accepted.status == "accepted"
    downloaded = service.slide_animation_download_path(run.id).read_text("utf-8")
    assert downloaded == preview
    listed = service.list_slide_animation_runs(lesson_id)
    assert listed["billed_count"] == 1
    assert listed["billed_limit"] == BILLED_LIMIT
    assert listed["page_limit"] == PAGE_LIMIT


def test_markup_from_model_fails_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, lesson_id, link = _ready_lesson(tmp_path, monkeypatch)
    service.slide_animation_model_adapter = FakeSlideAnimationModelAdapter(
        {
            "title": "<script>alert(1)</script>",
            "scenes": [
                {
                    "title": "引入",
                    "narration": "讲解定义。",
                    "duration_ms": 2000,
                    "source_page": 1,
                }
            ],
        }
    )
    run, _created = service.start_slide_animation_run(
        lesson_id,
        operation_id="anim-xss-operation",
        confirmed=True,
        material_link_id=link.id,
        page_indexes=[1],
    )
    service.process_slide_animation_run(run.id)
    failed = service.get_slide_animation_run(run.id)
    assert failed.status == "failed"
    assert failed.error_code == "model_response_invalid"
    assert failed.model_call_count == 1


def test_cancel_before_model_call_is_not_billed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, lesson_id, link = _ready_lesson(tmp_path, monkeypatch)
    run, _created = service.start_slide_animation_run(
        lesson_id,
        operation_id="anim-cancel-operation",
        confirmed=True,
        material_link_id=link.id,
        page_indexes=[1],
    )
    cancelled = service.cancel_slide_animation_run(run.id)
    assert cancelled.status == "cancelled"
    assert cancelled.model_call_count == 0
    listed = service.list_slide_animation_runs(lesson_id)
    assert listed["billed_count"] == 0
    service.process_slide_animation_run(run.id)
    assert service.get_slide_animation_run(run.id).status == "cancelled"


def test_fourth_billed_send_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, lesson_id, link = _ready_lesson(tmp_path, monkeypatch)
    service.slide_animation_model_adapter = FakeSlideAnimationModelAdapter(
        failure=TimeoutError("synthetic timeout")
    )
    for index in range(3):
        run, created = service.start_slide_animation_run(
            lesson_id,
            operation_id=f"anim-billed-{index}",
            confirmed=True,
            material_link_id=link.id,
            page_indexes=[1],
        )
        assert created is True
        service.process_slide_animation_run(run.id)
        assert service.get_slide_animation_run(run.id).status == "failed"
    listed = service.list_slide_animation_runs(lesson_id)
    assert listed["billed_count"] == 3
    with pytest.raises(TeachingPrepValidationError):
        service.start_slide_animation_run(
            lesson_id,
            operation_id="anim-billed-4",
            confirmed=True,
            material_link_id=link.id,
            page_indexes=[2],
        )


def test_http_preview_and_download_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, lesson_id, link = _ready_lesson(tmp_path, monkeypatch)
    client = _api_client(service)
    started = client.post(
        f"/api/teaching-prep/lessons/{lesson_id}/slide-animation-runs",
        json={
            "operation_id": "anim-http-operation",
            "confirmed": True,
            "material_link_id": link.id,
            "page_indexes": [1],
        },
    )
    assert started.status_code == 202
    run_id = started.json()["id"]
    finished = client.get(f"/api/teaching-prep/slide-animation-runs/{run_id}")
    assert finished.status_code == 200
    assert finished.json()["status"] == "succeeded"
    preview = client.get(f"/api/teaching-prep/slide-animation-runs/{run_id}/preview")
    assert preview.status_code == 200
    assert "text/html" in preview.headers["content-type"]
    assert "sandbox" not in preview.text
    blocked = client.get(f"/api/teaching-prep/slide-animation-runs/{run_id}/download")
    assert blocked.status_code == 409
    accepted = client.post(
        f"/api/teaching-prep/slide-animation-runs/{run_id}/accept",
        json={"expected_revision": finished.json()["revision"]},
    )
    assert accepted.status_code == 200
    downloaded = client.get(
        f"/api/teaching-prep/slide-animation-runs/{run_id}/download"
    )
    assert downloaded.status_code == 200
    assert "attachment" in downloaded.headers["content-disposition"]
    assert "合成课堂动画" in downloaded.text
