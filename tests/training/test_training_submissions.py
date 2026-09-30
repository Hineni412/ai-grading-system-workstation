from __future__ import annotations

import hashlib
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import cv2
import fitz
import numpy as np
import pytest

from question_bank.database.schema import connect
from question_bank.personalized_papers import (
    FreezePaperCommand,
    PersonalizedPaperModule,
)
from question_bank.personalized_papers.rendering import _qr_png
from question_bank.recommendation.personalized import (
    PersonalizedRecommendationConfig,
    PersonalizedRecommendationModule,
)
from question_bank.training_submissions import (
    CreateScanBatchCommand,
    IngestUploadCommand,
    ResolvePageCommand,
    SubmissionRevisionConflict,
    TrainingSubmissionModule,
)
from tests.training.test_personalized_papers import (
    SyntheticPdfConverter,
    _create_command,
    _diagnosis as _paper_diagnosis,
    paper_workspace,
)
from tests.training.test_part_assessment_mastery import refined_training_source
from tests.training.test_personalized_recommendation import (
    NOW,
    _diagnosis,
)


class VariablePagePdfConverter:
    def __init__(self) -> None:
        self.calls = 0

    def convert(self, source_docx: Path, output_pdf: Path) -> None:
        self.calls += 1
        document = fitz.open()
        try:
            for page_number in range(1, self.calls + 1):
                page = document.new_page(width=595, height=842)
                page.insert_text(
                    fitz.Point(72, 96),
                    f"Synthetic variable page {page_number}",
                    fontsize=14,
                )
            document.save(output_pdf)
        finally:
            document.close()


def test_shuffled_pages_group_by_signed_identity_and_survive_restart(
    paper_workspace,
) -> None:
    paper, draft, db_path, data_root = paper_workspace
    instance = _freeze(paper, draft, token="d")
    frozen_path, _ = paper.artifact_path(
        str(instance["paper_instance_id"]),
        "frozen-pdf",
    )
    pages = _pdf_pages(frozen_path)
    module = TrainingSubmissionModule(db_path=db_path, data_root=data_root)
    batch = module.create_batch(
        CreateScanBatchCommand(
            operation_token="e" * 32,
            paper_instance_ids=(str(instance["paper_instance_id"]),),
            actor_ref="teacher-1",
        )
    )

    shuffled = (pages[1], _rotate_png(pages[0]))
    for index, content in enumerate(shuffled, start=1):
        batch = module.ingest(
            str(batch["batch_id"]),
            _upload(
                token=f"{index + 1:x}",
                revision=int(batch["revision"]),
                content=content,
            ),
            BytesIO(content),
        )

    assert batch["status"] == "ready", [
        (
            page["page_number"],
            page["state"],
            page["issue_code"],
            page["rotation_degrees"],
            page["width_pixels"],
            page["height_pixels"],
        )
        for page in batch["pages"]
    ]
    assert batch["submissions"][0]["missing_pages"] == []
    assert [
        page["page_number"]
        for page in sorted(
            batch["pages"],
            key=lambda item: int(item["page_number"] or 0),
        )
    ] == [1, 2]
    assert all(page["state"] == "assigned" for page in batch["pages"])
    assert any(page["rotation_degrees"] != 0 for page in batch["pages"])
    restarted = TrainingSubmissionModule(db_path=db_path, data_root=data_root)
    history = restarted.list_batches(str(batch["paper_batch_id"]))
    assert [item["batch_id"] for item in history] == [batch["batch_id"]]
    assert history[0]["submission_count"] == 1
    assert history[0]["status"] == "ready"
    assert restarted.list_batches("0" * 64) == []
    assert restarted.get_batch(str(batch["batch_id"])) == batch
    finalized = restarted.finalize_submission(
        str(batch["submissions"][0]["submission_id"])
    )
    assert finalized["submission_revision"] == 1
    assert [page["page_number"] for page in finalized["pages"]] == [1, 2]

    # Continue the actual recovered scan through assessment and publication.
    # Only the model is replaced; persistence and mastery use the real modules.
    from backend.training_assessment import (
        EvidenceSyncCommand,
        FakeTrainingAssessmentGateway,
        TrainingAssessmentModule,
    )
    from question_bank.mastery.current import CurrentMasteryCalculator
    from question_bank.current_knowledge import CurrentKnowledgeResolver

    resolver = CurrentKnowledgeResolver.from_active_database(db_path)
    # The submission stores the draft's diagnosis (paper_workspace applies the
    # 0.8-rate fixture); reproduce it so the mastery comparison is identical.
    profile = _paper_diagnosis(student_ids=("SYN-S01",))
    identity = ("SYN-S01", "kp_alg_linear_equation")
    before = CurrentMasteryCalculator(
        db_path, resolver, clock=lambda: NOW, data_root=data_root
    ).calculate(profile)[identity]
    gateway = FakeTrainingAssessmentGateway(
        lambda request: {
            "results": [
                {
                    "task_item_code": item.task_item_code,
                    "point_id": point["point_id"],
                    "state": "met",
                    "evidence": "合成答卷中的步骤和结论均已核对",
                }
                for item in request.items
                for point in item.points
            ]
        }
    )
    assessment = TrainingAssessmentModule(
        db_path=db_path, data_root=data_root, gateway=gateway, clock=lambda: NOW
    )
    submission_id = str(batch["submissions"][0]["submission_id"])
    outcome = assessment.assess(submission_id, 1)
    assert gateway.calls == 1
    # Candidate results do not count until the teacher confirms publication.
    assert (
        CurrentMasteryCalculator(
            db_path, resolver, clock=lambda: NOW, data_root=data_root
        ).calculate(profile)[identity]
        == before
    )
    command = EvidenceSyncCommand(
        operation_token="8" * 32,
        expected_review_revision=outcome.review_revision,
        action="publish",
        actor_ref="teacher-1",
        reason="确认归卷批改结果并更新掌握度",
    )
    feedback = assessment.sync_evidence(submission_id, 1, command)
    assert feedback["summary"]["published_question_count"] == len(outcome.questions)
    assert feedback["summary"]["published_question_count"] > 0
    assert assessment.sync_evidence(submission_id, 1, command) == feedback
    change = next(
        item
        for item in feedback["mastery_changes"]
        if item["stable_key"] == identity[1]
    )
    assert change["mastery_after"]["value"] > before.value
    reopened = TrainingAssessmentModule(
        db_path=db_path, data_root=data_root, gateway=gateway, clock=lambda: NOW
    )
    assert reopened.get_feedback(submission_id, 1) == feedback
    after = CurrentMasteryCalculator(
        db_path, resolver, clock=lambda: NOW, data_root=data_root
    ).calculate(profile)[identity]
    assert after.value == change["mastery_after"]["value"]
    assert after.training_evidence_count == len(outcome.questions)
    target = next(
        item
        for item in feedback["next_round"]["student"]["targets"]
        if item["stable_key"] == identity[1]
    )
    assert target["value"] == after.value
    assert paper.get(str(instance["paper_instance_id"])) == instance

    another = restarted.create_batch(
        CreateScanBatchCommand(
            operation_token="f" * 32,
            paper_instance_ids=(str(instance["paper_instance_id"]),),
            actor_ref="teacher-1",
        )
    )
    assert [
        item["batch_id"]
        for item in restarted.list_batches(str(batch["paper_batch_id"]))
    ] == [
        another["batch_id"],
        batch["batch_id"],
    ]


@pytest.mark.parametrize("semester_scope", [False, True])
def test_refined_scanned_paper_updates_each_part_and_survives_reopen(
    refined_training_source, semester_scope
):
    from backend.training_assessment import (
        EvidenceSyncCommand,
        FakeTrainingAssessmentGateway,
        TrainingAssessmentModule,
    )
    from question_bank.mastery.current import CurrentMasteryCalculator
    from question_bank.current_knowledge import CurrentKnowledgeResolver

    from db_manager import DBManager
    from integration.diagnosis_profile_service import DiagnosisProfileService
    from question_bank.training_criteria.analysis import (
        grading_config_skeleton_from_solution_evidence,
    )
    from backend.api.routers.training import create_personalized_recommendation_draft
    from backend.api.schemas.training import PersonalizedRecommendationCreateRequest
    import json

    db_path, data_root, question, evidence, _, _ = refined_training_source
    grading_path = data_root / "databases" / "grading.db"
    grading = DBManager(grading_path)
    grading.initialize()
    rubric = grading_config_skeleton_from_solution_evidence(
        evidence, question_ref="Q1"
    )["rubric_question"]
    for index, part in enumerate(rubric["parts"], 1):
        part.update(part_id=f"Q1(P{index})", part_score=5)
    rubric_path = data_root / "synthetic-rubric.json"
    rubric_path.write_text(json.dumps({"questions": [rubric]}), encoding="utf-8")
    # 设计 E §7.4：旧会话先迁移出冻结证据快照，投影按 evidence_point_ids 归因。
    from question_bank.solution_evidence.evidence_snapshot import (
        freeze_session_evidence_snapshot,
    )

    freeze_session_evidence_snapshot(
        db_path,
        grading_session_id=1,
        upload_config_dir=data_root / "config" / "uploaded",
        data_root=data_root,
    )
    with grading._connect() as conn:
        conn.execute(
            "INSERT INTO grading_sessions(id,session_name,rubric_path,answer_key_path,status,is_deleted) VALUES(1,'合成考试',?,'','completed',0)",
            (str(rubric_path),),
        )
        conn.execute(
            "UPDATE grading_sessions SET curriculum_volume_id='bnu24-math-g8-upper' WHERE id=1"
        )
        conn.execute(
            "INSERT INTO students(id,student_code,name,class_name) VALUES(1,'SYN-S01','合成学生','合成班')"
        )
        conn.execute(
            "INSERT INTO exam_papers(id,session_id,front_image,back_image,student_id,match_status,processing_status) VALUES(1,1,'','',1,'matched','graded')"
        )
        conn.execute(
            "INSERT INTO session_results(id,session_id,student_id,paper_id,total_score,student_score,needs_human_review,raw_json) VALUES(1,1,1,1,10,5,0,'{}')"
        )
        for part, score in (("Q1(P1)", 0), ("Q1(P2)", 5)):
            conn.execute(
                "INSERT INTO session_details(result_id,question_id,score_awarded,deduction_reason,knowledge_ids) VALUES(1,?,?,'',?)",
                (part, score, json.dumps(["UNKNOWN"])),
            )
    diagnosis_service = DiagnosisProfileService(
        grading_path, db_path, data_root=data_root
    )
    scope = {"mode": "selected", "student_ids": ["1"]}
    exam_scope = {"mode": "current", "session_ids": [1]}
    if semester_scope:
        exam_scope = {"mode": "semester", "curriculum_volume_id": "bnu24-math-g8-upper"}
    # Warm the real diagnosis cache before publishing training evidence.
    profile = diagnosis_service.build_profiles(scope=scope, exam_scope=exam_scope)
    # The diagnosis snapshot carries only teacher-visible top-level keys.
    assert not any(str(key).startswith("_") for key in profile)
    before = {
        point["knowledge_key"]: point["mastery"]
        for point in profile["students"][0]["weak_points"]
    }
    before_counts = {
        point["knowledge_key"]: point["evidence_count"]
        for point in profile["students"][0]["weak_points"]
    }
    frozen_profile = _diagnosis(student_ids=("SYN-S01",))
    frozen_profile["exam_scope"] = profile["exam_scope"]
    frozen_profile["students"][0]["student_id"] = "1"
    # The source exam is older than the three latest graded activities.
    graded_activities = [
        {"student_id": "1", "session_id": i, "occurred_at": f"2026-09-0{i}"}
        for i in (2, 3, 4)
    ]
    frozen_profile["students"][0]["weak_points"][0]["source_question_refs"][0][
        "question_difficulty"
    ] = 8
    # Strong-but-imperfect source: still a loss, but the readiness plan can
    # reach the difficulty-8 candidate instead of capping at foundation.
    frozen_profile["students"][0]["weak_points"][0]["source_question_refs"][0][
        "score_awarded"
    ] = 8
    recommendation = PersonalizedRecommendationModule(
        db_path=db_path, data_root=data_root, clock=lambda: NOW
    )
    draft = recommendation.create(
        request_token="1" * 32,
        diagnosis=frozen_profile,
        graded_activities=graded_activities,
        config=PersonalizedRecommendationConfig(
            question_count=8, difficulty_max=8, exclude_current_exam_originals=False
        ),
        actor_ref="synthetic",
    )
    assert len(draft["students"][0]["items"]) == 1
    paper = PersonalizedPaperModule(
        db_path=db_path,
        data_root=data_root,
        pdf_converter=SyntheticPdfConverter(),
        clock=lambda: NOW,
    )
    frozen = _freeze(paper, draft, token="a")
    path, _ = paper.artifact_path(frozen["paper_instance_id"], "frozen-pdf")
    scans = TrainingSubmissionModule(db_path=db_path, data_root=data_root)
    batch = scans.create_batch(
        CreateScanBatchCommand(
            operation_token="c" * 32,
            paper_instance_ids=(frozen["paper_instance_id"],),
            actor_ref="synthetic",
        )
    )
    for index, content in enumerate(_pdf_pages(path), 1):
        batch = scans.ingest(
            batch["batch_id"],
            _upload(token=str(index), revision=batch["revision"], content=content),
            BytesIO(content),
        )
    submission_id = batch["submissions"][0]["submission_id"]
    scans.finalize_submission(submission_id)
    first_points = {
        point.evidence_point_id for point in evidence.parts[0].evidence_points
    }
    gateway = FakeTrainingAssessmentGateway(
        lambda request: {
            "results": [
                {
                    "task_item_code": item.task_item_code,
                    "point_id": point["point_id"],
                    "state": "met" if point["point_id"] in first_points else "not_met",
                    "evidence": "合成第一问正确、第二问未达成",
                }
                for item in request.items
                for point in item.points
            ]
        }
    )
    module = TrainingAssessmentModule(
        db_path=db_path, data_root=data_root, gateway=gateway, clock=lambda: NOW
    )
    outcome = module.assess(submission_id, 1)
    feedback = module.sync_evidence(
        submission_id,
        1,
        EvidenceSyncCommand(
            operation_token="d" * 32,
            expected_review_revision=outcome.review_revision,
            action="publish",
            actor_ref="synthetic",
            reason="确认逐小问结果",
        ),
    )
    assert feedback["summary"]["published_question_count"] == 1
    resolver = CurrentKnowledgeResolver.from_active_database(db_path)
    current = CurrentMasteryCalculator(
        db_path, resolver, data_root=data_root, clock=lambda: NOW
    ).calculate(profile)
    first = current[("1", "kp_alg_linear_equation")]
    second = current[("1", "kp_geo_triangle_congruence")]
    assert first.training_evidence_count == second.training_evidence_count == 1
    assert first.value > before["kp_alg_linear_equation"]
    assert second.value < before["kp_geo_triangle_congruence"]
    reopened = TrainingAssessmentModule(
        db_path=db_path, data_root=data_root, gateway=gateway, clock=lambda: NOW
    )
    assert reopened.get_feedback(submission_id, 1) == feedback
    assert (
        CurrentMasteryCalculator(
            db_path, resolver, data_root=data_root, clock=lambda: NOW
        ).calculate(profile)
        == current
    )
    # A separate new-training request must refresh the cached diagnosis, while
    # the completed paper and its saved feedback remain unchanged.
    fresh = create_personalized_recommendation_draft(
        PersonalizedRecommendationCreateRequest(
            request_token="e" * 32,
            scope=scope,
            exam_scope=exam_scope,
            question_count=8,
            difficulty_max=8,
            exclude_current_exam_originals=False,
        ),
        diagnosis_service=diagnosis_service,
        module=recommendation,
    ).model_dump()
    target = next(
        item
        for item in fresh["students"][0]["targets"]
        if item["stable_key"] == "kp_alg_linear_equation"
    )
    assert target["value"] > before["kp_alg_linear_equation"]
    assert target["evidence_count"] == before_counts["kp_alg_linear_equation"] + 1
    assert recommendation.get(draft["draft_id"]) == draft
    assert reopened.get_feedback(submission_id, 1) == feedback
    assert paper.get(frozen["paper_instance_id"]) == frozen
    if semester_scope:
        other = diagnosis_service.build_profiles(
            scope=scope,
            exam_scope={
                "mode": "semester",
                "curriculum_volume_id": "bnu24-math-g7-lower",
            },
        )
        assert not other["exam_scope"]["session_ids"]
        assert not any(
            point["evidence_count"] for point in other["students"][0]["weak_points"]
        )
        # A current-term training result remains available with no exam attached.
        with grading._connect() as conn:
            conn.execute(
                "UPDATE grading_sessions SET curriculum_volume_id='bnu24-math-g7-lower' WHERE id=1"
            )
        training_only = diagnosis_service.build_profiles(
            scope=scope, exam_scope=exam_scope
        )
        assert training_only["students"][0]["score_rate"] is None
        assert not training_only["exam_scope"]["session_ids"]
        point = next(
            point
            for point in training_only["students"][0]["weak_points"]
            if point["knowledge_key"] == "kp_alg_linear_equation"
        )
        assert point["evidence_count"] == 1
        assert point["mastery"] is not None
        assert paper.get(frozen["paper_instance_id"]) == frozen
        assert reopened.get_feedback(submission_id, 1) == feedback


def test_duplicate_conflict_external_and_manual_replacement_are_explicit(
    paper_workspace,
) -> None:
    paper, draft, db_path, data_root = paper_workspace
    instance = _freeze(paper, draft, token="7")
    frozen_path, _ = paper.artifact_path(
        str(instance["paper_instance_id"]),
        "frozen-pdf",
    )
    first, second = _pdf_pages(frozen_path)
    module = TrainingSubmissionModule(db_path=db_path, data_root=data_root)
    batch = module.create_batch(
        CreateScanBatchCommand(
            operation_token="8" * 32,
            paper_instance_ids=(str(instance["paper_instance_id"]),),
            actor_ref="teacher-1",
        )
    )
    batch = module.ingest(
        str(batch["batch_id"]),
        _upload(token="9", revision=1, content=first),
        BytesIO(first),
    )
    duplicate_pdf = _images_pdf((first, first))
    batch = module.ingest(
        str(batch["batch_id"]),
        IngestUploadCommand(
            operation_token="a" * 32,
            expected_revision=int(batch["revision"]),
            filename="duplicate.pdf",
            media_type="application/pdf",
            content_sha256=hashlib.sha256(duplicate_pdf).hexdigest(),
            actor_ref="teacher-1",
        ),
        BytesIO(duplicate_pdf),
    )
    assert any(page["issue_code"] == "duplicate_page" for page in batch["pages"])

    altered = _mark_image(first)
    batch = module.ingest(
        str(batch["batch_id"]),
        _upload(token="b", revision=int(batch["revision"]), content=altered),
        BytesIO(altered),
    )
    conflicts = [
        page for page in batch["pages"] if page["issue_code"] == "page_content_conflict"
    ]
    assert conflicts, [
        (page["page_number"], page["state"], page["issue_code"])
        for page in batch["pages"]
    ]
    conflict = conflicts[0]
    blank = _blank_png()
    batch = module.ingest(
        str(batch["batch_id"]),
        _upload(token="c", revision=int(batch["revision"]), content=blank),
        BytesIO(blank),
    )
    assert any(page["issue_code"] == "identity_unreadable" for page in batch["pages"])
    with pytest.raises(SubmissionRevisionConflict):
        module.resolve_page(
            str(batch["batch_id"]),
            ResolvePageCommand(
                operation_token="d" * 32,
                expected_revision=int(batch["revision"]) - 1,
                scan_page_id=str(conflict["scan_page_id"]),
                action="replace",
                paper_instance_id=str(instance["paper_instance_id"]),
                page_number=1,
                actor_ref="teacher-1",
            ),
        )

    batch = module.resolve_page(
        str(batch["batch_id"]),
        ResolvePageCommand(
            operation_token="e" * 32,
            expected_revision=int(batch["revision"]),
            scan_page_id=str(conflict["scan_page_id"]),
            action="replace",
            paper_instance_id=str(instance["paper_instance_id"]),
            page_number=1,
            actor_ref="teacher-1",
        ),
    )
    assigned = [
        page
        for page in batch["pages"]
        if page["state"] == "assigned" and page["page_number"] == 1
    ]
    assert [page["scan_page_id"] for page in assigned] == [conflict["scan_page_id"]]
    assert any(page["state"] == "replaced" for page in batch["pages"])
    assert batch["status"] == "manual_review"
    assert batch["submissions"][0]["missing_pages"] == [2]

    batch = module.ingest(
        str(batch["batch_id"]),
        _upload(token="f", revision=int(batch["revision"]), content=second),
        BytesIO(second),
    )
    if not any(
        page["state"] == "assigned" and page["page_number"] == 2
        for page in batch["pages"]
    ):
        page_two = next(
            page
            for page in batch["pages"]
            if page["image_sha256"] == hashlib.sha256(second).hexdigest()
            and page["state"] != "assigned"
        )
        batch = module.resolve_page(
            str(batch["batch_id"]),
            ResolvePageCommand(
                operation_token="0" * 32,
                expected_revision=int(batch["revision"]),
                scan_page_id=str(page_two["scan_page_id"]),
                action="match",
                paper_instance_id=str(instance["paper_instance_id"]),
                page_number=2,
                actor_ref="teacher-1",
            ),
        )
    unresolved = [
        page
        for page in batch["pages"]
        if page["state"] in {"duplicate", "conflict", "unassigned"}
    ]
    for index, page in enumerate(unresolved):
        batch = module.resolve_page(
            str(batch["batch_id"]),
            ResolvePageCommand(
                operation_token=f"{index + 1:032x}",
                expected_revision=int(batch["revision"]),
                scan_page_id=str(page["scan_page_id"]),
                action="dismiss",
                actor_ref="teacher-1",
            ),
        )
    assert batch["status"] == "ready"


def test_blur_crop_and_invalid_signature_stay_in_manual_review(
    paper_workspace,
) -> None:
    paper, draft, db_path, data_root = paper_workspace
    instance = _freeze(paper, draft, token="5")
    path, _ = paper.artifact_path(str(instance["paper_instance_id"]), "frozen-pdf")
    second = _pdf_pages(path)[1]
    module = TrainingSubmissionModule(db_path=db_path, data_root=data_root)
    batch = module.create_batch(
        CreateScanBatchCommand(
            operation_token="6" * 32,
            paper_instance_ids=(str(instance["paper_instance_id"]),),
            actor_ref="teacher-1",
        )
    )
    samples = (
        ("7", _cropped_identity_png(db_path, str(instance["paper_instance_id"]))),
        ("8", _blur_png(second)),
        (
            "9",
            _tampered_identity_png(
                db_path,
                str(instance["paper_instance_id"]),
            ),
        ),
    )
    for token, content in samples:
        batch = module.ingest(
            str(batch["batch_id"]),
            _upload(
                token=token,
                revision=int(batch["revision"]),
                content=content,
            ),
            BytesIO(content),
        )
    issues = {page["issue_code"] for page in batch["pages"]}
    assert "severe_crop" in issues
    assert "image_blurry" in issues
    assert "invalid_identity" in issues
    assert batch["status"] == "manual_review"


def _freeze(paper, draft, *, token: str):
    instance = paper.create_review_instance(
        str(draft["draft_id"]),
        replace(
            _create_command(token, draft),
            student_id=str(draft["students"][0]["student_id"]),
        ),
    )
    review, _ = paper.artifact_path(str(instance["paper_instance_id"]), "review-docx")
    content = review.read_bytes()
    return paper.freeze(
        str(instance["paper_instance_id"]),
        FreezePaperCommand(
            operation_token=f"{int(token, 16) + 1:x}" * 32,
            expected_revision=int(instance["revision"]),
            content_sha256=hashlib.sha256(content).hexdigest(),
            filename="reviewed.docx",
            actor_ref="teacher-1",
        ),
        BytesIO(content),
    )


def _pdf_pages(path: Path) -> tuple[bytes, ...]:
    result: list[bytes] = []
    with fitz.open(path) as document:
        for page in document:
            pixmap = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height,
                pixmap.width,
                3,
            )
            success, encoded = cv2.imencode(
                ".png",
                cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
            )
            assert success
            result.append(bytes(encoded))
    return tuple(result)


def _upload(
    *,
    token: str,
    revision: int,
    content: bytes,
) -> IngestUploadCommand:
    return IngestUploadCommand(
        operation_token=token * 32,
        expected_revision=revision,
        filename="scan.png",
        media_type="image/png",
        content_sha256=hashlib.sha256(content).hexdigest(),
        actor_ref="teacher-1",
    )


def _mark_image(content: bytes) -> bytes:
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    cv2.rectangle(image, (80, 80), (180, 130), (0, 0, 0), thickness=-1)
    success, encoded = cv2.imencode(".png", image)
    assert success
    return bytes(encoded)


def _rotate_png(content: bytes) -> bytes:
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    rotated = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    success, encoded = cv2.imencode(".png", rotated)
    assert success
    return bytes(encoded)


def _cropped_identity_png(db_path: Path, paper_instance_id: str) -> bytes:
    # A severely cropped page carrying a crisp re-rendered QR with the real
    # page identity.  Decoding the stamped QR inside the cropped frozen-PDF
    # render was marginal: the payload embeds per-run hashes, so the decode
    # flickered between 'severe_crop' and 'identity_unreadable'.
    with connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT page_identity FROM personalized_paper_pages
            WHERE paper_instance_id = ? AND page_number = 1
            """,
            (paper_instance_id,),
        ).fetchone()
    identity = str(row["page_identity"])
    qr = cv2.imdecode(
        np.frombuffer(_qr_png(identity), dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    qr = cv2.resize(qr, (220, 220), interpolation=cv2.INTER_NEAREST)
    image = np.full((1800, 1273, 3), 255, dtype=np.uint8)
    image[-260:-40, -260:-40] = qr
    cropped = image[:, int(image.shape[1] * 0.22) :]
    success, encoded = cv2.imencode(".png", cropped)
    assert success
    return bytes(encoded)


def _blur_png(content: bytes) -> bytes:
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    blurred = cv2.GaussianBlur(image, (41, 41), 14)
    success, encoded = cv2.imencode(".png", blurred)
    assert success
    return bytes(encoded)


def _tampered_identity_png(db_path: Path, paper_instance_id: str) -> bytes:
    with connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT page_identity FROM personalized_paper_pages
            WHERE paper_instance_id = ? AND page_number = 1
            """,
            (paper_instance_id,),
        ).fetchone()
    identity = str(row["page_identity"])
    tampered = f"{identity[:-1]}{'0' if identity[-1] != '0' else '1'}"
    qr = cv2.imdecode(
        np.frombuffer(_qr_png(tampered), dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    image = np.full((1800, 1273, 3), 255, dtype=np.uint8)
    qr = cv2.resize(qr, (220, 220), interpolation=cv2.INTER_NEAREST)
    image[-260:-40, -260:-40] = qr
    success, encoded = cv2.imencode(".png", image)
    assert success
    return bytes(encoded)


def _blank_png() -> bytes:
    image = np.full((1200, 850, 3), 255, dtype=np.uint8)
    cv2.putText(
        image,
        "external synthetic page",
        (80, 140),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (0, 0, 0),
        2,
    )
    success, encoded = cv2.imencode(".png", image)
    assert success
    return bytes(encoded)


def _images_pdf(images: tuple[bytes, ...]) -> bytes:
    document = fitz.open()
    try:
        for content in images:
            image = cv2.imdecode(
                np.frombuffer(content, dtype=np.uint8),
                cv2.IMREAD_COLOR,
            )
            height, width = image.shape[:2]
            page = document.new_page(
                width=width / 2.75,
                height=height / 2.75,
            )
            page.insert_image(page.rect, stream=content)
        return document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()
