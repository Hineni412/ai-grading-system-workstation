from __future__ import annotations

import hashlib
import json
import zipfile
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.teaching_prep.api import create_router
from backend.teaching_prep.application import TeachingPrepService
from backend.teaching_prep.application.up_class_packages import build_up_class_package
from backend.teaching_prep.domain.errors import TeachingPrepConflictError
from backend.teaching_prep.infrastructure.fakes import (
    FakeAssessmentEvidenceReader,
    FakeWpsAdapter,
)

from .test_a01_foundation import _migrated_service
from .test_a07_slide_plans import _confirmed_draft


EXPECTED_PACKAGE_FILES = (
    "lesson-slides.pptx",
    "class-exercise.pdf",
    "teacher-answer.pdf",
    "lesson-flow.pdf",
    "sources.json",
    "preflight.json",
    "manifest.json",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _published_pptx(
    service: TeachingPrepService,
    tmp_path: Path,
):
    _pack, draft, _reference_link = _confirmed_draft(service, tmp_path)
    payload = deepcopy(draft.payload)
    for item in payload["exercise_recommendations"]:
        if item["source_ref"].startswith("question:"):
            item["action"] = "backup"
    printable_draft, _created = service.revise_lesson_draft(
        draft.id,
        request_token="a09-printable-draft",
        payload=payload,
        confirmed=True,
    )
    plan, _created = service.create_slide_plan(
        printable_draft.id,
        request_token="a09-create-slide-plan",
    )
    reviews = [
        {
            "operation_id": item["operation_id"],
            "decision": (
                "rejected"
                if item["execution_mode"] == "manual_only"
                else "approved"
            ),
            "reason": item["reason"],
            "planned_minutes": item["planned_minutes"],
            "teacher_note": "合成 A09 审批",
        }
        for item in plan.payload["operations"]
    ]
    plan, _created = service.revise_slide_plan(
        plan.id,
        request_token="a09-approve-slide-plan",
        operation_reviews=reviews,
        approve_low_risk_deletions=False,
        review_note="合成 A09 审批完成",
    )
    service.wps_adapter = FakeWpsAdapter()
    _run, version, _created = service.execute_slide_plan(
        plan.id,
        operation_id="a09-synthetic-pptx",
        confirmed=True,
    )
    assert version is not None
    return version


def test_complete_up_class_package_is_atomic_offline_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    version = _published_pptx(service, tmp_path)
    pptx_path, _name = service.pptx_download(version.id)
    pptx_before = _sha256(pptx_path)

    package, created = service.create_up_class_package(
        version.id,
        request_token="a09-complete-package",
        confirmed=True,
    )
    repeated, repeated_created = service.create_up_class_package(
        version.id,
        request_token="a09-complete-package",
        confirmed=True,
    )

    assert created is True
    assert repeated_created is False
    assert repeated.id == package.id
    assert package.status == "complete"
    assert package.is_current is True
    assert package.manifest is not None
    archive, filename = service.up_class_package_download(package.id)
    assert filename == package.output_filename
    assert _sha256(archive) == package.package_sha256
    assert _sha256(pptx_path) == pptx_before
    with zipfile.ZipFile(archive) as bundle:
        assert tuple(bundle.namelist()) == EXPECTED_PACKAGE_FILES
        manifest = json.loads(bundle.read("manifest.json"))
        preflight = json.loads(bundle.read("preflight.json"))
        sources = json.loads(bundle.read("sources.json"))
        assert manifest["preflight"]["complete"] is True
        assert preflight["complete"] is True
        assert preflight["offline_ready"] is True
        assert sources["versions"]["pptx_version_id"] == version.id
        serialized = json.dumps(
            {"manifest": manifest, "preflight": preflight, "sources": sources},
            ensure_ascii=False,
        )
        assert str(tmp_path) not in serialized


def test_question_only_package_omits_teacher_answer_without_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    version = _published_pptx(service, tmp_path)
    plan = service.get_slide_plan(version.slide_plan_id)
    draft = service.get_lesson_draft(plan.lesson_draft_id)
    pack = service.get_resource_pack(draft.resource_pack_id)
    payload = deepcopy(pack.payload)
    assert payload["exercises"]
    for exercise in payload["exercises"]:
        exercise["formal_answer_usable"] = False
        exercise["answer_status"] = "missing"
        exercise["answer_regions"] = []
    question_only_pack = replace(pack, payload=payload)
    pptx_path, _name = service.pptx_download(version.id)

    archive, _manifest, _sha = build_up_class_package(
        tmp_path / "question-only-package",
        pptx_path=pptx_path,
        pptx_version=version,
        plan=plan,
        draft=draft,
        pack=question_only_pack,
        resolve_region=service.exercise_region_preview_path,
    )

    with zipfile.ZipFile(archive) as bundle:
        names = tuple(bundle.namelist())
        assert "class-exercise.pdf" in names
        assert "teacher-answer.pdf" not in names
        preflight = json.loads(bundle.read("preflight.json"))
        assert preflight["complete"] is True
        assert preflight["all_answers_available_and_frozen"] is False
        assert preflight["answer_output_optional"] is True


def test_class_variants_isolate_class_evidence_and_selected_reviews(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    version = _published_pptx(service, tmp_path)
    package, _created = service.create_up_class_package(
        version.id,
        request_token="a09-review-package",
        confirmed=True,
    )
    review, review_created = service.create_post_lesson_review(
        package.id,
        request_token="a09-post-review",
        timing="over",
        question_outcome="too_hard",
        reteach_points=["一次函数图像斜率辨认"],
        next_action="adjust",
        note="合成复盘，不含学生信息",
        use_in_next_version=True,
    )
    base = service.get_resource_pack(package.resource_pack_id)
    original_payload = json.loads(
        json.dumps(base.payload, ensure_ascii=False)
    )
    service.assessment_evidence_reader = FakeAssessmentEvidenceReader(
        {
            "version": "synthetic-class-b",
            "class_name": "合成七年级二班",
            "assessments": [],
            "knowledge_summary": [],
            "missing": [],
        }
    )
    variant_b, pack_b, created_b = service.derive_class_variant(
        base.id,
        request_token="a09-class-b-variant",
        class_name="合成七年级二班",
        teacher_context="二班合成整体情况",
        assessment_ids=[],
        knowledge_scope=[],
    )
    service.assessment_evidence_reader = FakeAssessmentEvidenceReader(
        {
            "version": "synthetic-class-a-next",
            "class_name": "合成七年级一班",
            "assessments": [],
            "knowledge_summary": [],
            "missing": [],
        }
    )
    variant_a, pack_a, created_a = service.derive_class_variant(
        base.id,
        request_token="a09-class-a-next",
        class_name="合成七年级一班",
        teacher_context="一班下一版合成情况",
        assessment_ids=[],
        knowledge_scope=[],
        prior_review_ids=[review.id],
    )

    assert review_created is True
    assert created_b is True and created_a is True
    assert variant_b.resource_pack_id == pack_b.id
    assert pack_b.source_state_sha256 == base.source_state_sha256
    assert pack_b.payload["classroom"]["class_name"] == "合成七年级二班"
    assert pack_b.payload["prior_reviews"] == []
    assert variant_a.prior_review_ids == (review.id,)
    assert pack_a.payload["prior_reviews"][0]["review_id"] == review.id
    assert service.get_resource_pack(base.id).payload == original_payload
    preflight = service.lesson_draft_preflight(
        pack_a.id,
        mode="local_template",
    )
    assert preflight["data_scope"]["prior_review_count"] == 1
    draft, _created = service.generate_lesson_draft(
        pack_a.id,
        operation_id="a09-draft-with-review",
        mode="local_template",
        confirmed=True,
    )
    assert any(
        f"post_review:{review.id}" in item["citations"]
        for item in draft.payload["anticipated_difficulties"]
    )


def test_restart_recovers_only_exact_package_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    version = _published_pptx(service, tmp_path)

    with monkeypatch.context() as patch:
        patch.setattr(
            "backend.teaching_prep.application.preparation_service.os.rename",
            lambda _source, _target: (_ for _ in ()).throw(SystemExit()),
        )
        with pytest.raises(SystemExit):
            service.create_up_class_package(
                version.id,
                request_token="a09-interrupted-package",
                confirmed=True,
            )

    restarted = TeachingPrepService(service.root)
    interrupted = restarted.list_up_class_packages(version.lesson_node_id)[0]
    assert interrupted.status == "interrupted"
    assert interrupted.recovery_actions == (
        "resume_publish",
        "discard_staging",
    )
    recovered = restarted.recover_up_class_package(interrupted.id)
    assert recovered.status == "complete"
    archive, _filename = restarted.up_class_package_download(recovered.id)
    assert archive.is_file()

    archive.write_bytes(archive.read_bytes() + b"tampered")
    with pytest.raises(TeachingPrepConflictError):
        restarted.up_class_package_download(recovered.id)


def test_missing_required_printable_file_never_completes_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    version = _published_pptx(service, tmp_path)
    service.exercise_region_preview_path = lambda _region_id: (
        (_ for _ in ()).throw(FileNotFoundError("synthetic missing preview"))
    )

    with pytest.raises(FileNotFoundError):
        service.create_up_class_package(
            version.id,
            request_token="a09-missing-required-file",
            confirmed=True,
        )

    failed = service.list_up_class_packages(version.lesson_node_id)
    assert len(failed) == 1
    assert failed[0].status == "failed"
    assert failed[0].is_current is False
    assert list(service.paths["exports"].rglob("*.zip")) == []


def test_teacher_can_restore_previous_complete_package_without_deleting_newer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    first_version = _published_pptx(service, tmp_path)
    first_package, _created = service.create_up_class_package(
        first_version.id,
        request_token="a09-first-final-package",
        confirmed=True,
    )
    first_plan = service.get_slide_plan(first_version.slide_plan_id)
    second_plan, _created = service.create_slide_plan(
        first_plan.lesson_draft_id,
        request_token="a09-second-slide-plan",
    )
    reviews = [
        {
            "operation_id": item["operation_id"],
            "decision": (
                "rejected"
                if item["execution_mode"] == "manual_only"
                else "approved"
            ),
            "reason": item["reason"],
            "planned_minutes": item["planned_minutes"],
            "teacher_note": "合成第二版审批",
        }
        for item in second_plan.payload["operations"]
    ]
    approved, _created = service.revise_slide_plan(
        second_plan.id,
        request_token="a09-approve-second-plan",
        operation_reviews=reviews,
        approve_low_risk_deletions=False,
        review_note="合成第二版",
    )
    service.wps_adapter = FakeWpsAdapter()
    _run, second_version, _created = service.execute_slide_plan(
        approved.id,
        operation_id="a09-second-pptx",
        confirmed=True,
    )
    assert second_version is not None
    second_package, _created = service.create_up_class_package(
        second_version.id,
        request_token="a09-second-final-package",
        confirmed=True,
    )
    assert second_package.is_current is True

    restored, changed = service.activate_up_class_package(
        first_package.id,
        request_token="a09-restore-first-package",
        confirmed=True,
    )
    packages = service.list_up_class_packages(first_version.lesson_node_id)

    assert changed is True
    assert restored.is_current is True
    assert {item.id for item in packages} == {
        first_package.id,
        second_package.id,
    }
    assert next(
        item for item in packages if item.id == second_package.id
    ).is_current is False
    for item in packages:
        path, _filename = service.up_class_package_download(item.id)
        assert path.is_file()


def test_a09_api_never_discloses_workspace_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths, service = _migrated_service(tmp_path, monkeypatch)
    version = _published_pptx(service, tmp_path)
    app = FastAPI()
    app.state.workspace_services = {"teaching-prep": service}
    app.include_router(create_router(), prefix="/api/teaching-prep")
    client = TestClient(app)

    response = client.post(
        f"/api/teaching-prep/pptx-versions/{version.id}/up-class-package",
        json={
            "request_token": "a09-api-package",
            "confirmed": True,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "complete"
    assert payload["download_url"].endswith("/download")
    assert str(tmp_path) not in json.dumps(payload, ensure_ascii=False)
    download = client.get(payload["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/zip")
