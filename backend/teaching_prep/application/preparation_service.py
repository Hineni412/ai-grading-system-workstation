from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from collections.abc import Mapping, Sequence
from uuid import uuid4

from PIL import Image

from backend.teaching_prep.application.ports import (
    AssessmentEvidenceReader,
    ExerciseSuggestionModelAdapter,
    LessonModelAdapter,
    QuestionEvidenceReader,
    SemesterMappingModelAdapter,
    WpsAdapter,
)
from backend.teaching_prep.application.pptx_execution import (
    build_executor_request,
    digest as execution_digest,
    safe_execution_report,
)
from backend.teaching_prep.application.pptx_verification import (
    verify_candidate_with_deadline,
)
from backend.teaching_prep.application.lesson_drafts import (
    build_local_template,
    calculate_capacity,
    draft_preflight,
    validate_draft_payload,
)
from backend.teaching_prep.application.preferences import (
    DEFAULT_TEACHING_PREFERENCES,
    normalize_teaching_preferences,
    resolve_teaching_preferences,
)
from backend.teaching_prep.application.slide_plans import (
    build_slide_plan_payload,
    diff_preview,
    require_approval_allowed,
    validate_plan_payload,
)
from backend.teaching_prep.application.semester_mapping import (
    validate_semester_mapping_payload,
)
from backend.teaching_prep.application.workbench_iteration import (
    normalize_exercise_suggestion_payload,
    normalize_mapping_decision,
    normalize_reference_selection,
    snapshot_model_payload,
)
from backend.teaching_prep.application.up_class_packages import (
    build_up_class_package,
    verify_package_archive,
)
from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepNotFoundError,
    TeachingPrepRetryAvailableError,
    TeachingPrepValidationError,
)
from backend.teaching_prep.domain.models import (
    ClassVariant,
    CurriculumEdition,
    ExerciseCandidate,
    LessonMaterialLink,
    LessonNode,
    LessonDraftVersion,
    LessonGenerationPerformance,
    LessonPreparation,
    MaterialUnit,
    MaterialVersion,
    ResourcePackVersion,
    SemesterMappingProposal,
    SemesterLessonProgress,
    SemesterMaterialRecord,
    SlidePlanVersion,
    PptxExecutionRun,
    PptxVersion,
    PostLessonReview,
    UpClassPackage,
    TeachingPreferences,
    TeachingSemester,
)
from backend.teaching_prep.domain.states import (
    LessonPreparationState,
    require_transition,
)
from backend.teaching_prep.infrastructure.database import TeachingPrepDatabase
from backend.teaching_prep.infrastructure.materials import MaterialParser
from backend.teaching_prep.infrastructure.repositories import (
    ExerciseCandidateRepository,
    ExerciseRegionDraft,
    LessonDraftRepository,
    LessonPreparationRepository,
    MaterialUnitRepository,
    ResourcePackRepository,
    SemesterMappingRepository,
    SemesterWorkspaceRepository,
    SlidePlanRepository,
    PptxExecutionRepository,
    TeachingDeliveryRepository,
    TeachingCatalogRepository,
    TeachingPreferencesRepository,
    WorkbenchIterationRepository,
)


_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,95}$")
_ENTITY_ID = re.compile(r"^[0-9a-f]{32}$")
_MATERIAL_TYPES = {"pdf", "pptx", "image"}
_MATERIAL_SUFFIXES = {
    ".pdf": "pdf",
    ".pptx": "pptx",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
}
_VOLUMES = {"first", "second", "whole_year"}
_NODE_TYPES = {"chapter", "section", "lesson"}
_SOURCE_KINDS = {"teacher", "catalog"}
_SEMESTER_TERMS = {"first", "second"}
_SEMESTER_STATUSES = {"planning", "active", "completed", "archived"}
_LESSON_PROGRESS_STATUSES = {
    "not_started",
    "preparing",
    "ready",
    "taught",
    "skipped",
}
_SEMESTER_MATERIAL_ROLES = {
    "textbook",
    "reference_ppt",
    "exercise_workbook",
    "homework_workbook",
    "answer_book",
    "supplement",
}
_SEMESTER_MAPPING_STATUSES = {
    "unmapped",
    "proposed",
    "partial",
    "confirmed",
    "needs_review",
    "conflict",
}
_INSPECTION_STATUSES = {
    "uninspected",
    "ready",
    "scanned_no_text",
    "encrypted",
    "damaged",
    "locked",
}
_LINK_PURPOSES = {
    "textbook",
    "reference_ppt",
    "exercise",
    "answer",
    "supplement",
}
_CONFIRMATION_STATUSES = {"proposed", "confirmed"}
_DIFFICULTIES = {"unrated", "easy", "medium", "hard"}
_CLASSROOM_USES = {
    "introduction",
    "example",
    "guided_practice",
    "independent_practice",
    "diagnostic",
    "challenge",
    "summary",
}
_SELECTION_STATUSES = {"classroom_candidate", "backup", "excluded"}
_ANSWER_STATUSES = {
    "candidate",
    "teacher_verified",
    "rejected",
    "missing",
}
_REFERENCE_PPT_INTENTS = {"keep", "candidate_delete"}
_EXERCISE_PREVIEW_REF = re.compile(
    r"^/api/teaching-prep/exercise-regions/([0-9a-f]{32})/preview$"
)
_LESSON_TYPES = {"new_lesson"}
_LESSON_GENERATION_BUDGET_MS = 300_000
_WPS_EXECUTION_TIMEOUT_SECONDS = 170
_FORBIDDEN_EVIDENCE_KEYS = {
    "name",
    "student_name",
    "student_code",
    "student_id",
    "contact",
    "phone",
    "front_image",
    "back_image",
    "answer_image",
    "raw_json",
}


class TeachingPrepService:
    def __init__(
        self,
        root: str | Path,
        *,
        question_evidence_reader: QuestionEvidenceReader | None = None,
        assessment_evidence_reader: AssessmentEvidenceReader | None = None,
        lesson_model_adapter: LessonModelAdapter | None = None,
        lesson_model_label: str | None = None,
        semester_mapping_model_adapter: SemesterMappingModelAdapter | None = None,
        semester_mapping_model_label: str | None = None,
        exercise_suggestion_model_adapter: ExerciseSuggestionModelAdapter | None = None,
        exercise_suggestion_model_label: str | None = None,
        material_parser: MaterialParser | None = None,
        wps_adapter: WpsAdapter | None = None,
        wps_adapter_is_real: bool = False,
    ) -> None:
        self.root = Path(root)
        self.database_path = self.root / "teaching_prep.db"
        self.paths = {
            name: self.root / name
            for name in (
                "temp",
                "staging",
                "materials",
                "outputs",
                "previews",
                "exports",
            )
        }
        for path in self.paths.values():
            path.mkdir(parents=True, exist_ok=True)
        self.database = TeachingPrepDatabase(self.database_path)
        self.preparations = LessonPreparationRepository(self.database)
        self.preferences = TeachingPreferencesRepository(self.database)
        self.catalog = TeachingCatalogRepository(self.database)
        self.semesters = SemesterWorkspaceRepository(self.database)
        self.semester_mapping = SemesterMappingRepository(self.database)
        self.material_units = MaterialUnitRepository(self.database)
        self.exercises = ExerciseCandidateRepository(self.database)
        self.resource_packs = ResourcePackRepository(self.database)
        self.lesson_drafts = LessonDraftRepository(self.database)
        self.slide_plans = SlidePlanRepository(self.database)
        self.pptx_executions = PptxExecutionRepository(self.database)
        self.teaching_delivery = TeachingDeliveryRepository(self.database)
        self.workbench = WorkbenchIterationRepository(self.database)
        self.material_parser = material_parser or MaterialParser()
        self.question_evidence_reader = question_evidence_reader
        self.assessment_evidence_reader = assessment_evidence_reader
        self.lesson_model_adapter = lesson_model_adapter
        self.semester_mapping_model_adapter = semester_mapping_model_adapter
        self.exercise_suggestion_model_adapter = exercise_suggestion_model_adapter
        self.wps_adapter = wps_adapter
        self.wps_adapter_is_real = bool(wps_adapter_is_real)
        self.lesson_model_label = (
            _clean_optional_text(
                lesson_model_label,
                "lesson_model_label",
                maximum=120,
            )
            if lesson_model_label is not None
            else None
        )
        self.semester_mapping_model_label = (
            _clean_optional_text(
                semester_mapping_model_label,
                "semester_mapping_model_label",
                maximum=120,
            )
            if semester_mapping_model_label is not None
            else None
        )
        self.exercise_suggestion_model_label = (
            _clean_optional_text(
                exercise_suggestion_model_label,
                "exercise_suggestion_model_label",
                maximum=120,
            )
            if exercise_suggestion_model_label is not None
            else None
        )
        self.mark_interrupted_operations()

    def status(self) -> dict[str, object]:
        return {
            "module": "teaching-prep",
            "enabled": True,
            "schema_version": "013_workbench_iteration",
            "real_model_enabled": _model_adapter_available(
                self.lesson_model_adapter
            ),
            "semester_mapping_model_available": (
                _model_adapter_available(
                    self.semester_mapping_model_adapter
                )
            ),
            "exercise_suggestion_model_available": (
                _model_adapter_available(
                    self.exercise_suggestion_model_adapter
                )
            ),
            "real_wps_enabled": (
                self.wps_adapter is not None and self.wps_adapter_is_real
            ),
            "wps_execution_available": self.wps_adapter is not None,
        }

    def list_lesson_preparation_statuses(
        self,
        semester_id: str,
    ) -> tuple[dict[str, object], ...]:
        return self.workbench.lesson_preparation_statuses(
            _clean_entity_id(semester_id)
        )

    def review_semester_mapping_row(
        self,
        proposal_id: str,
        mapping_id: str,
        *,
        expected_revision: int,
        decision: Mapping[str, object],
    ) -> SemesterMappingProposal:
        if isinstance(expected_revision, bool) or expected_revision < 1:
            raise TeachingPrepValidationError(
                "expected mapping revision is invalid"
            )
        return self.semester_mapping.review_mapping(
            _clean_entity_id(proposal_id),
            _clean_entity_id(mapping_id),
            expected_revision=expected_revision,
            decision=normalize_mapping_decision(decision),
        )

    def reject_semester_mapping_proposal(
        self,
        proposal_id: str,
        *,
        expected_revision: int,
    ) -> SemesterMappingProposal:
        return self.semester_mapping.reject(
            _clean_entity_id(proposal_id),
            expected_revision=expected_revision,
        )

    def reference_selection_preflight(
        self,
        lesson_node_id: str,
    ) -> dict[str, object]:
        clean_lesson_id = _clean_entity_id(lesson_node_id)
        catalog, source_state = self.workbench.selection_catalog(
            clean_lesson_id
        )
        draft = self.workbench.get_reference_draft(clean_lesson_id)
        return {
            "lesson_node_id": clean_lesson_id,
            "source_state_sha256": source_state,
            "catalog": catalog,
            "draft": asdict(draft) if draft is not None else None,
            "model_available": _model_adapter_available(
                self.exercise_suggestion_model_adapter
            ),
            "model_label": self.exercise_suggestion_model_label,
            "will_call_model": False,
        }

    def save_reference_selection_draft(
        self,
        lesson_node_id: str,
        *,
        expected_revision: int | None,
        source_state_sha256: str,
        selection: Mapping[str, object],
    ):
        clean_lesson_id = _clean_entity_id(lesson_node_id)
        catalog, current_source = self.workbench.selection_catalog(
            clean_lesson_id
        )
        if current_source != source_state_sha256:
            raise TeachingPrepConflictError(
                "reference sources changed; refresh before saving"
            )
        normalized = normalize_reference_selection(selection, catalog=catalog)
        return self.workbench.save_reference_draft(
            lesson_node_id=clean_lesson_id,
            expected_revision=expected_revision,
            payload=normalized,
            source_state_sha256=current_source,
        )

    def freeze_reference_selection_snapshot(
        self,
        lesson_node_id: str,
        *,
        request_token: str,
        expected_draft_revision: int,
    ):
        clean_lesson_id = _clean_entity_id(lesson_node_id)
        clean_token = _clean_token(request_token)
        draft = self.workbench.get_reference_draft(clean_lesson_id)
        if draft is None or draft.revision != expected_draft_revision:
            raise TeachingPrepConflictError(
                "reference selection draft changed; refresh before freezing"
            )
        catalog, current_source = self.workbench.selection_catalog(
            clean_lesson_id
        )
        if current_source != draft.source_state_sha256:
            raise TeachingPrepConflictError(
                "reference sources changed; review the draft again"
            )
        model_payload = snapshot_model_payload(draft.payload, catalog=catalog)
        payload = {
            "selection": draft.payload,
            "model_input": model_payload,
            "draft_revision": draft.revision,
        }
        return self.workbench.freeze_reference_snapshot(
            request_token=clean_token,
            request_hash=_json_digest(
                {
                    "lesson_node_id": clean_lesson_id,
                    "source_state_sha256": current_source,
                    "payload": payload,
                }
            ),
            lesson_node_id=clean_lesson_id,
            source_state_sha256=current_source,
            payload=payload,
        )

    def exercise_suggestion_preflight(
        self,
        snapshot_id: str,
    ) -> dict[str, object]:
        snapshot = self.workbench.get_reference_snapshot(
            _clean_entity_id(snapshot_id)
        )
        available = _model_adapter_available(
            self.exercise_suggestion_model_adapter
        )
        return {
            "snapshot_id": snapshot.id,
            "lesson_node_id": snapshot.lesson_node_id,
            "source_state_sha256": snapshot.source_state_sha256,
            "model_available": available,
            "model_label": self.exercise_suggestion_model_label,
            "will_call_model": available,
            "automatic_retry_count": 0,
            "material_range_count": len(
                list(snapshot.payload["selection"]["material_selections"])
            ),
        }

    def start_exercise_suggestion_run(
        self,
        snapshot_id: str,
        *,
        operation_id: str,
        confirmed: bool,
    ):
        if not confirmed:
            raise TeachingPrepValidationError(
                "exercise suggestion generation requires explicit confirmation"
            )
        if not _model_adapter_available(self.exercise_suggestion_model_adapter):
            raise TeachingPrepValidationError(
                "exercise suggestion model is not configured"
            )
        clean_snapshot_id = _clean_entity_id(snapshot_id)
        clean_operation_id = _clean_token(operation_id)
        snapshot = self.workbench.get_reference_snapshot(clean_snapshot_id)
        _catalog, current_source = self.workbench.selection_catalog(
            snapshot.lesson_node_id
        )
        if current_source != snapshot.source_state_sha256:
            raise TeachingPrepConflictError(
                "reference sources changed before suggestion generation"
            )
        return self.workbench.begin_suggestion_run(
            snapshot_id=clean_snapshot_id,
            operation_id=clean_operation_id,
            request_hash=_json_digest(
                {
                    "snapshot_id": clean_snapshot_id,
                    "source_state_sha256": snapshot.source_state_sha256,
                }
            ),
        )

    def process_exercise_suggestion_run(self, run_id: str) -> None:
        clean_run_id = _clean_entity_id(run_id)
        run, _items = self.workbench.get_suggestion_run(clean_run_id)
        if run.status != "running":
            return
        snapshot = self.workbench.get_reference_snapshot(run.snapshot_id)
        adapter = self.exercise_suggestion_model_adapter
        if adapter is None:
            self.workbench.fail_suggestion_run(
                clean_run_id, "model_unavailable"
            )
            return
        try:
            self.workbench.mark_suggestion_call_started(clean_run_id)
            raw = adapter.generate(
                operation_id=run.operation_id,
                reference_snapshot=dict(snapshot.payload["model_input"]),
            )
            suggestions = normalize_exercise_suggestion_payload(
                raw,
                snapshot=dict(snapshot.payload["model_input"]),
            )
            self.workbench.finish_suggestion_run(
                run_id=clean_run_id,
                lesson_node_id=snapshot.lesson_node_id,
                source_state_sha256=snapshot.source_state_sha256,
                suggestions=suggestions,
            )
        except Exception as exc:
            self.workbench.fail_suggestion_run(
                clean_run_id, _model_error_code(exc)
            )

    def get_exercise_suggestion_run(self, run_id: str):
        return self.workbench.get_suggestion_run(_clean_entity_id(run_id))

    def cancel_exercise_suggestion_run(self, run_id: str):
        return self.workbench.cancel_suggestion_run(_clean_entity_id(run_id))

    def review_exercise_suggestion(
        self,
        suggestion_id: str,
        *,
        expected_revision: int,
        decision: str,
        teacher_payload: Mapping[str, object] | None,
        rejection_reason: str | None,
    ):
        clean_id = _clean_entity_id(suggestion_id)
        run, items = self.workbench.get_suggestion_run_for_suggestion(clean_id)
        suggestion = next(item for item in items if item.id == clean_id)
        clean_decision = _clean_choice(
            decision,
            {"accepted", "modified", "rejected"},
            "suggestion_decision",
        )
        payload = (
            dict(teacher_payload)
            if clean_decision == "modified"
            else suggestion.original_payload
        )
        candidate_id: str | None = None
        normalized_teacher: dict[str, object] | None = None
        if clean_decision in {"accepted", "modified"}:
            snapshot = self.workbench.get_reference_snapshot(run.snapshot_id)
            _catalog, current_source = self.workbench.selection_catalog(
                snapshot.lesson_node_id
            )
            if current_source != snapshot.source_state_sha256:
                raise TeachingPrepConflictError(
                    "reference sources changed before accepting the suggestion"
                )
            normalized_items = normalize_exercise_suggestion_payload(
                {"suggestions": [payload]},
                snapshot=dict(snapshot.payload["model_input"]),
            )
            normalized_teacher = normalized_items[0]
            candidate, _created = self.create_exercise_candidate(
                request_token=f"suggestion-{clean_id}-{expected_revision:04d}",
                lesson_node_id=suggestion.lesson_node_id,
                question_number=normalized_teacher["question_number"],
                content_label=normalized_teacher["content_label"],
                difficulty=str(normalized_teacher["difficulty"]),
                classroom_use=str(normalized_teacher["classroom_use"]),
                estimated_minutes=normalized_teacher["estimated_minutes"],
                teaching_focus=normalized_teacher["teaching_focus"],
                teacher_note=str(normalized_teacher["reason"]),
                selection_status="classroom_candidate",
                answer_status=(
                    "candidate"
                    if normalized_teacher["answer_regions"]
                    else "missing"
                ),
                question_regions=[
                    {
                        "material_unit_id": item["material_unit_id"],
                        "crop": item["crop"],
                    }
                    for item in normalized_teacher["question_regions"]
                ],
                answer_regions=[
                    {
                        "material_unit_id": item["material_unit_id"],
                        "crop": item["crop"],
                    }
                    for item in normalized_teacher["answer_regions"]
                ],
            )
            candidate_id = candidate.id
        return self.workbench.update_suggestion(
            clean_id,
            expected_revision=expected_revision,
            decision=clean_decision,
            teacher_payload=normalized_teacher,
            rejection_reason=_clean_optional_text(
                rejection_reason, "rejection_reason", maximum=500
            ),
            exercise_candidate_id=candidate_id,
        )

    def create_preparation(
        self,
        *,
        request_token: str,
        title: str,
        class_name: str | None,
    ) -> tuple[LessonPreparation, bool]:
        clean_token = _clean_token(request_token)
        clean_title = _clean_text(title, "title", maximum=160)
        clean_class_name = _clean_optional_text(
            class_name,
            "class_name",
            maximum=120,
        )
        return self.preparations.create(
            request_token=clean_token,
            title=clean_title,
            class_name=clean_class_name,
        )

    def get_teaching_preferences(self) -> TeachingPreferences:
        return self.preferences.get()

    def update_teaching_preferences(
        self,
        *,
        expected_revision: int,
        payload: Mapping[str, object],
    ) -> TeachingPreferences:
        if isinstance(expected_revision, bool) or expected_revision < 1:
            raise TeachingPrepValidationError(
                "expected preference revision is invalid"
            )
        return self.preferences.update(
            expected_revision=expected_revision,
            payload=payload,
        )

    def get_preparation(self, preparation_id: str) -> LessonPreparation:
        return self.preparations.get(_clean_entity_id(preparation_id))

    def list_preparations(self) -> tuple[LessonPreparation, ...]:
        return self.preparations.list()

    def update_preparation(
        self,
        preparation_id: str,
        *,
        expected_revision: int,
        title: str | None = None,
        class_name: str | None = None,
        target_state: LessonPreparationState | None = None,
    ) -> LessonPreparation:
        clean_id = _clean_entity_id(preparation_id)
        if int(expected_revision) <= 0:
            raise TeachingPrepValidationError(
                "expected_revision must be positive"
            )
        current = self.preparations.get(clean_id)
        next_state = target_state or current.state
        require_transition(current.state, next_state)
        next_title = (
            current.title
            if title is None
            else _clean_text(title, "title", maximum=160)
        )
        next_class_name = (
            current.class_name
            if class_name is None
            else _clean_optional_text(
                class_name,
                "class_name",
                maximum=120,
            )
        )
        return self.preparations.update(
            clean_id,
            expected_revision=int(expected_revision),
            title=next_title,
            class_name=next_class_name,
            state=next_state,
        )

    def create_curriculum(
        self,
        *,
        request_token: str,
        title: str,
        grade_level: int,
        volume: str,
        publisher: str | None = None,
        edition_label: str | None = None,
    ) -> tuple[CurriculumEdition, bool]:
        clean_grade = int(grade_level)
        if clean_grade not in {7, 8, 9}:
            raise TeachingPrepValidationError("grade_level is invalid")
        clean_volume = str(volume or "").strip()
        if clean_volume not in _VOLUMES:
            raise TeachingPrepValidationError("volume is invalid")
        return self.catalog.create_curriculum(
            request_token=_clean_token(request_token),
            title=_clean_text(title, "title", maximum=160),
            grade_level=clean_grade,
            volume=clean_volume,
            publisher=_clean_optional_text(
                publisher,
                "publisher",
                maximum=120,
            ),
            edition_label=_clean_optional_text(
                edition_label,
                "edition_label",
                maximum=120,
            ),
        )

    def list_curricula(self) -> tuple[CurriculumEdition, ...]:
        return self.catalog.list_curricula()

    def create_semester_workspace(
        self,
        *,
        request_token: str,
        title: str,
        grade_level: int,
        volume: str,
        publisher: str | None = None,
        edition_label: str | None = None,
        school_year: str,
        term: str,
        planned_new_lesson_count: int,
    ) -> tuple[CurriculumEdition, TeachingSemester, bool]:
        """Establish the curriculum and its semester in one transaction.

        This is deliberately separate from the legacy low-level creation
        methods.  A teacher-facing new-semester action must never leave a
        curriculum without the state library that gives it context.
        """
        clean_grade = int(grade_level)
        if clean_grade not in {7, 8, 9}:
            raise TeachingPrepValidationError("grade_level is invalid")
        clean_volume = str(volume or "").strip()
        if clean_volume not in _VOLUMES:
            raise TeachingPrepValidationError("volume is invalid")
        clean_term = str(term or "").strip()
        if clean_term not in _SEMESTER_TERMS:
            raise TeachingPrepValidationError("semester term is invalid")
        clean_count = int(planned_new_lesson_count)
        if clean_count < 0 or clean_count > 500:
            raise TeachingPrepValidationError(
                "planned lesson count is invalid"
            )
        return self.semesters.create_workspace(
            request_token=_clean_token(request_token),
            title=_clean_text(title, "title", maximum=160),
            grade_level=clean_grade,
            volume=clean_volume,
            publisher=_clean_optional_text(
                publisher,
                "publisher",
                maximum=120,
            ),
            edition_label=_clean_optional_text(
                edition_label,
                "edition_label",
                maximum=120,
            ),
            school_year=_clean_text(
                school_year,
                "school_year",
                maximum=20,
            ),
            term=clean_term,
            planned_new_lesson_count=clean_count,
        )

    def create_semester(
        self,
        *,
        request_token: str,
        curriculum_id: str,
        school_year: str,
        term: str,
        planned_new_lesson_count: int,
    ) -> tuple[TeachingSemester, bool]:
        clean_term = str(term or "").strip()
        if clean_term not in _SEMESTER_TERMS:
            raise TeachingPrepValidationError("semester term is invalid")
        clean_count = int(planned_new_lesson_count)
        if clean_count < 0 or clean_count > 500:
            raise TeachingPrepValidationError(
                "planned lesson count is invalid"
            )
        return self.semesters.create(
            request_token=_clean_token(request_token),
            curriculum_id=_clean_entity_id(curriculum_id),
            school_year=_clean_text(
                school_year,
                "school_year",
                maximum=20,
            ),
            term=clean_term,
            planned_new_lesson_count=clean_count,
        )

    def list_semesters(self) -> tuple[TeachingSemester, ...]:
        return self.semesters.list()

    def update_semester(
        self,
        semester_id: str,
        *,
        expected_revision: int,
        planned_new_lesson_count: int,
        status: str,
    ) -> TeachingSemester:
        clean_status = str(status or "").strip()
        if clean_status not in _SEMESTER_STATUSES:
            raise TeachingPrepValidationError("semester status is invalid")
        clean_count = int(planned_new_lesson_count)
        if clean_count < 0 or clean_count > 500:
            raise TeachingPrepValidationError(
                "planned lesson count is invalid"
            )
        return self.semesters.update(
            _clean_entity_id(semester_id),
            expected_revision=_clean_revision(expected_revision),
            planned_new_lesson_count=clean_count,
            status=clean_status,
        )

    def set_semester_lesson_progress(
        self,
        semester_id: str,
        lesson_node_id: str,
        *,
        status: str,
        expected_revision: int | None,
    ) -> SemesterLessonProgress:
        clean_status = str(status or "").strip()
        if clean_status not in _LESSON_PROGRESS_STATUSES:
            raise TeachingPrepValidationError(
                "lesson progress status is invalid"
            )
        return self.semesters.set_lesson_progress(
            _clean_entity_id(semester_id),
            _clean_entity_id(lesson_node_id),
            status=clean_status,
            expected_revision=(
                _clean_revision(expected_revision)
                if expected_revision is not None
                else None
            ),
        )

    def list_semester_lesson_progress(
        self,
        semester_id: str,
    ) -> tuple[SemesterLessonProgress, ...]:
        return self.semesters.list_lesson_progress(
            _clean_entity_id(semester_id)
        )

    def attach_semester_material(
        self,
        semester_id: str,
        *,
        request_token: str,
        material_version_id: str,
        material_role: str,
    ) -> tuple[SemesterMaterialRecord, bool]:
        clean_role = str(material_role or "").strip()
        if clean_role not in _SEMESTER_MATERIAL_ROLES:
            raise TeachingPrepValidationError("material role is invalid")
        return self.semesters.attach_material(
            _clean_entity_id(semester_id),
            request_token=_clean_token(request_token),
            material_version_id=_clean_entity_id(material_version_id),
            material_role=clean_role,
        )

    def list_semester_materials(
        self,
        semester_id: str,
    ) -> tuple[SemesterMaterialRecord, ...]:
        return self.semesters.list_materials(
            _clean_entity_id(semester_id)
        )

    def update_semester_material(
        self,
        record_id: str,
        *,
        expected_revision: int,
        material_role: str,
        mapping_status: str,
        is_active: bool,
    ) -> SemesterMaterialRecord:
        clean_role = str(material_role or "").strip()
        if clean_role not in _SEMESTER_MATERIAL_ROLES:
            raise TeachingPrepValidationError("material role is invalid")
        clean_mapping = str(mapping_status or "").strip()
        if clean_mapping not in _SEMESTER_MAPPING_STATUSES:
            raise TeachingPrepValidationError(
                "material mapping status is invalid"
            )
        return self.semesters.update_material(
            _clean_entity_id(record_id),
            expected_revision=_clean_revision(expected_revision),
            material_role=clean_role,
            mapping_status=clean_mapping,
            is_active=bool(is_active),
        )

    def semester_mapping_preflight(
        self,
        semester_id: str,
        *,
        material_record_ids: Sequence[str],
    ) -> dict[str, object]:
        clean_semester_id = _clean_entity_id(semester_id)
        clean_material_ids = tuple(
            _clean_entity_id(item) for item in material_record_ids
        )
        if len(clean_material_ids) != 1:
            raise TeachingPrepValidationError(
                "semester mapping handles one material at a time"
            )
        snapshot, digest = self.semester_mapping.snapshot(
            clean_semester_id,
            clean_material_ids,
        )
        _require_initial_tree_source(snapshot)
        materials = list(snapshot["materials"])
        lessons = list(snapshot["lessons"])
        return {
            "semester_id": clean_semester_id,
            "source_state_sha256": digest,
            "will_call_model": True,
            "model_available": (
                _model_adapter_available(
                    self.semester_mapping_model_adapter
                )
            ),
            "model_label": self.semester_mapping_model_label,
            "material_count": len(materials),
            "unit_count": sum(
                int(item["unit_count"])
                for item in materials
                if isinstance(item, Mapping)
            ),
            "existing_lesson_count": sum(
                item.get("node_type") == "lesson"
                for item in lessons
                if isinstance(item, Mapping)
            ),
            "creates_initial_tree": not lessons,
            "automatic_retry": False,
        }

    def generate_semester_mapping_proposal(
        self,
        semester_id: str,
        *,
        operation_id: str,
        material_record_ids: Sequence[str],
    ) -> tuple[SemesterMappingProposal, bool]:
        clean_semester_id = _clean_entity_id(semester_id)
        clean_operation_id = _clean_token(operation_id)
        clean_material_ids = tuple(
            dict.fromkeys(
                _clean_entity_id(item) for item in material_record_ids
            )
        )
        if len(clean_material_ids) != 1:
            raise TeachingPrepValidationError(
                "semester mapping handles one material at a time"
            )
        snapshot, source_digest = self.semester_mapping.snapshot(
            clean_semester_id,
            clean_material_ids,
        )
        _require_initial_tree_source(snapshot)
        request_hash = _stable_hash(
            {
                "semester_id": clean_semester_id,
                "material_record_ids": clean_material_ids,
                "source_state_sha256": source_digest,
            }
        )
        existing = self.semester_mapping.find_generation(
            operation_id=clean_operation_id,
            request_hash=request_hash,
        )
        if existing is not None:
            return existing, False
        if not _model_adapter_available(
            self.semester_mapping_model_adapter
        ):
            raise TeachingPrepValidationError(
                "semester mapping model is unavailable"
            )
        existing = self.semester_mapping.begin_generation(
            operation_id=clean_operation_id,
            request_hash=request_hash,
            semester_id=clean_semester_id,
        )
        if existing is not None:
            return existing, False
        try:
            raw = self.semester_mapping_model_adapter.generate(
                operation_id=clean_operation_id,
                semester_snapshot=snapshot,
            )
            normalized = validate_semester_mapping_payload(
                raw,
                snapshot=snapshot,
            )
            stored = {
                **normalized,
                "source_material_record_ids": list(clean_material_ids),
            }
            return (
                self.semester_mapping.finish_generation(
                    operation_id=clean_operation_id,
                    semester_id=clean_semester_id,
                    source_state_sha256=source_digest,
                    payload=stored,
                ),
                True,
            )
        except Exception as exc:
            self.semester_mapping.fail_generation(
                clean_operation_id,
                "semester_mapping_failed",
            )
            raise TeachingPrepRetryAvailableError(
                "semester mapping did not complete; the teacher may retry"
            ) from exc

    def list_semester_mapping_proposals(
        self,
        semester_id: str,
    ) -> tuple[SemesterMappingProposal, ...]:
        return self.semester_mapping.list(
            _clean_entity_id(semester_id)
        )

    def apply_semester_mapping_proposal(
        self,
        proposal_id: str,
        *,
        expected_revision: int,
    ) -> SemesterMappingProposal:
        return self.semester_mapping.apply(
            _clean_entity_id(proposal_id),
            expected_revision=_clean_revision(expected_revision),
        )

    def create_lesson_node(
        self,
        *,
        request_token: str,
        curriculum_id: str,
        parent_id: str | None,
        node_type: str,
        title: str,
        duration_minutes: int | None = None,
        source_kind: str = "teacher",
    ) -> tuple[LessonNode, bool]:
        clean_type = str(node_type or "").strip()
        if clean_type not in _NODE_TYPES:
            raise TeachingPrepValidationError("node_type is invalid")
        clean_source = str(source_kind or "").strip()
        if clean_source not in _SOURCE_KINDS:
            raise TeachingPrepValidationError("source_kind is invalid")
        clean_duration = _clean_duration(duration_minutes)
        return self.catalog.create_lesson_node(
            request_token=_clean_token(request_token),
            curriculum_id=_clean_entity_id(curriculum_id),
            parent_id=(
                _clean_entity_id(parent_id) if parent_id is not None else None
            ),
            node_type=clean_type,
            title=_clean_text(title, "title", maximum=160),
            duration_minutes=clean_duration,
            source_kind=clean_source,
        )

    def list_lesson_nodes(
        self,
        curriculum_id: str,
    ) -> tuple[LessonNode, ...]:
        return self.catalog.list_lesson_nodes(
            _clean_entity_id(curriculum_id)
        )

    def update_lesson_node(
        self,
        node_id: str,
        *,
        expected_revision: int,
        title: str,
        duration_minutes: int | None,
        is_active: bool,
    ) -> LessonNode:
        if int(expected_revision) <= 0:
            raise TeachingPrepValidationError(
                "expected_revision must be positive"
            )
        return self.catalog.update_lesson_node(
            _clean_entity_id(node_id),
            expected_revision=int(expected_revision),
            title=_clean_text(title, "title", maximum=160),
            duration_minutes=_clean_duration(duration_minutes),
            is_active=bool(is_active),
        )

    def reorder_lesson_nodes(
        self,
        *,
        curriculum_id: str,
        parent_id: str | None,
        ordered_ids: Sequence[str],
        expected_revisions: Mapping[str, int],
    ) -> tuple[LessonNode, ...]:
        clean_order = tuple(_clean_entity_id(item) for item in ordered_ids)
        clean_revisions = {
            _clean_entity_id(key): int(value)
            for key, value in expected_revisions.items()
        }
        if any(value <= 0 for value in clean_revisions.values()):
            raise TeachingPrepValidationError(
                "lesson node revision is invalid"
            )
        return self.catalog.reorder_lesson_nodes(
            curriculum_id=_clean_entity_id(curriculum_id),
            parent_id=(
                _clean_entity_id(parent_id) if parent_id is not None else None
            ),
            ordered_ids=clean_order,
            expected_revisions=clean_revisions,
        )

    def register_material_file(
        self,
        *,
        request_token: str,
        path: str | Path,
        display_name: str,
        source_id: str | None = None,
        file_name: str | None = None,
        unit_count: int | None = None,
        inspection_status: str = "uninspected",
    ) -> tuple[MaterialVersion, bool]:
        source_path = Path(path).resolve(strict=True)
        if not source_path.is_file():
            raise TeachingPrepValidationError(
                "material source is not a file"
            )
        material_type = _MATERIAL_SUFFIXES.get(source_path.suffix.casefold())
        if material_type not in _MATERIAL_TYPES:
            raise TeachingPrepValidationError(
                "material file type is unsupported"
            )
        clean_status = str(inspection_status or "").strip()
        if clean_status not in _INSPECTION_STATUSES:
            raise TeachingPrepValidationError(
                "inspection_status is invalid"
            )
        clean_unit_count = _clean_unit_count(unit_count)
        file_stat = source_path.stat()
        clean_file_name = str(file_name or source_path.name).strip()
        if (
            not clean_file_name
            or len(clean_file_name) > 240
            or clean_file_name != Path(clean_file_name).name
            or "/" in clean_file_name
            or "\\" in clean_file_name
        ):
            raise TeachingPrepValidationError("material filename is invalid")
        return self.catalog.register_material_version(
            request_token=_clean_token(request_token),
            source_id=(
                _clean_entity_id(source_id) if source_id is not None else None
            ),
            display_name=_clean_text(
                display_name,
                "display_name",
                maximum=200,
            ),
            material_type=material_type,
            content_sha256=_sha256_file(source_path),
            file_name=clean_file_name,
            local_path=str(source_path),
            size_bytes=int(file_stat.st_size),
            modified_ns=int(file_stat.st_mtime_ns),
            unit_count=clean_unit_count,
            inspection_status=clean_status,
        )

    def import_material_copy(
        self,
        *,
        request_token: str,
        staged_path: str | Path,
        original_filename: str,
        display_name: str,
        modified_ns: int | None = None,
        relocate_version_id: str | None = None,
    ) -> tuple[MaterialVersion, bool]:
        source_path = Path(staged_path).resolve(strict=True)
        staging_root = self.paths["temp"].resolve(strict=False)
        if not source_path.is_relative_to(staging_root) or not source_path.is_file():
            raise TeachingPrepValidationError(
                "material upload is outside the controlled staging directory"
            )
        clean_filename = str(original_filename or "").strip()
        if (
            not clean_filename
            or len(clean_filename) > 240
            or clean_filename != Path(clean_filename).name
            or "/" in clean_filename
            or "\\" in clean_filename
        ):
            raise TeachingPrepValidationError("material filename is invalid")
        suffix = Path(clean_filename).suffix.casefold()
        if suffix not in _MATERIAL_SUFFIXES:
            raise TeachingPrepValidationError(
                "material file type is unsupported"
            )
        clean_modified_ns = (
            int(modified_ns) if modified_ns is not None else None
        )
        if clean_modified_ns is not None and clean_modified_ns < 0:
            raise TeachingPrepValidationError(
                "material modified time is invalid"
            )
        if source_path.stat().st_size <= 0:
            raise TeachingPrepValidationError("material upload is empty")

        fingerprint = _sha256_file(source_path)
        destination = (
            self.paths["materials"] / f"{fingerprint}{suffix}"
        ).resolve(strict=False)
        materials_root = self.paths["materials"].resolve(strict=False)
        if not destination.is_relative_to(materials_root):
            raise TeachingPrepValidationError(
                "material destination is outside the controlled directory"
            )
        if not destination.exists():
            temporary = materials_root / f".{fingerprint}-{uuid4().hex}.part"
            shutil.copy2(source_path, temporary)
            os.replace(temporary, destination)
        if clean_modified_ns is not None:
            os.utime(
                destination,
                ns=(clean_modified_ns, clean_modified_ns),
            )

        if relocate_version_id is not None:
            current = self.catalog.get_material_version(
                _clean_entity_id(relocate_version_id)
            )
            if fingerprint == current.content_sha256:
                return (
                    self.catalog.update_material_location(
                        current.id,
                        local_path=str(destination),
                    ),
                    False,
                )
            source_id = current.source_id
            effective_display_name = current.display_name
        else:
            source_id = None
            effective_display_name = display_name

        item, created = self.register_material_file(
            request_token=request_token,
            path=destination,
            display_name=effective_display_name,
            source_id=source_id,
            file_name=clean_filename,
            inspection_status="uninspected",
        )
        if not created:
            item = self.catalog.update_material_location(
                item.id,
                local_path=str(destination),
            )
        return item, created

    def list_material_versions(
        self,
        *,
        search: str | None = None,
        material_type: str | None = None,
        availability: str | None = None,
    ) -> tuple[MaterialVersion, ...]:
        clean_type = str(material_type or "").strip() or None
        if clean_type is not None and clean_type not in _MATERIAL_TYPES:
            raise TeachingPrepValidationError("material_type is invalid")
        clean_availability = str(availability or "").strip() or None
        if (
            clean_availability is not None
            and clean_availability
            not in {"available", "missing", "needs_relocation"}
        ):
            raise TeachingPrepValidationError("availability is invalid")
        return self.catalog.list_material_versions(
            search=_clean_optional_text(search, "search", maximum=120),
            material_type=clean_type,
            availability=clean_availability,
        )

    def refresh_material_availability(
        self,
        version_id: str,
    ) -> MaterialVersion:
        clean_id = _clean_entity_id(version_id)
        location = self.catalog.get_material_location(clean_id)
        if location.is_file():
            return self.catalog.get_material_version(clean_id)
        return self.catalog.mark_material_missing(clean_id)

    def relocate_material(
        self,
        version_id: str,
        *,
        request_token: str,
        path: str | Path,
    ) -> tuple[MaterialVersion, bool]:
        clean_id = _clean_entity_id(version_id)
        current = self.catalog.get_material_version(clean_id)
        source_path = Path(path).resolve(strict=True)
        if not source_path.is_file():
            raise TeachingPrepValidationError(
                "material relocation target is not a file"
            )
        fingerprint = _sha256_file(source_path)
        if fingerprint == current.content_sha256:
            return (
                self.catalog.update_material_location(
                    clean_id,
                    local_path=str(source_path),
                ),
                False,
            )
        return self.register_material_file(
            request_token=request_token,
            path=source_path,
            display_name=current.display_name,
            source_id=current.source_id,
            unit_count=None,
            inspection_status="uninspected",
        )

    def parse_material_version(
        self,
        version_id: str,
    ) -> tuple[MaterialUnit, ...]:
        clean_id = _clean_entity_id(version_id)
        version = self.catalog.get_material_version(clean_id)
        try:
            source_path = self.catalog.get_material_location(clean_id)
            if not source_path.is_file():
                self.catalog.mark_material_missing(clean_id)
                raise TeachingPrepValidationError(
                    "material file requires relocation"
                )
            current_sha = _sha256_file(source_path)
            if current_sha != version.content_sha256:
                self.catalog.mark_material_missing(clean_id)
                raise TeachingPrepConflictError(
                    "material file changed; register a new version"
                )
            parsed = self.material_parser.parse(
                source_path,
                material_type=version.material_type,
            )
            relative_paths: list[str] = []
            preview_hashes: list[str] = []
            for unit in parsed:
                relative = (
                    Path("previews")
                    / clean_id
                    / f"unit-{unit.unit_index:05d}.png"
                )
                target = self.root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(
                    f".{target.name}.{uuid4().hex}.tmp"
                )
                temporary.write_bytes(unit.preview_png)
                temporary.replace(target)
                relative_paths.append(relative.as_posix())
                preview_hashes.append(
                    hashlib.sha256(unit.preview_png).hexdigest()
                )
            units = self.material_units.save_parsed_units(
                clean_id,
                source_version_sha256=version.content_sha256,
                units=parsed,
                preview_relpaths=tuple(relative_paths),
                preview_hashes=tuple(preview_hashes),
            )
            self.semesters.mark_version_parsed(clean_id)
            return units
        except Exception:
            self.semesters.mark_version_parse_failed(clean_id)
            raise

    def list_material_units(
        self,
        version_id: str,
    ) -> tuple[MaterialUnit, ...]:
        return self.material_units.list_units(_clean_entity_id(version_id))

    def update_material_unit_label(
        self,
        unit_id: str,
        *,
        expected_revision: int,
        title: str | None,
        manual_text: str,
        formula_review_required: bool,
    ) -> MaterialUnit:
        if int(expected_revision) <= 0:
            raise TeachingPrepValidationError(
                "expected_revision must be positive"
            )
        clean_title = _clean_optional_text(title, "title", maximum=160)
        clean_text = str(manual_text or "").strip()
        if len(clean_text) > 4_000:
            raise TeachingPrepValidationError("manual_text is too long")
        return self.material_units.update_manual_label(
            _clean_entity_id(unit_id),
            expected_revision=int(expected_revision),
            title=clean_title,
            manual_text=clean_text,
            formula_review_required=bool(formula_review_required),
        )

    def material_preview_path(self, unit_id: str) -> Path:
        clean_id = _clean_entity_id(unit_id)
        record = self.material_units.preview_record(clean_id)
        target = (self.root / record.preview_relpath).resolve(strict=False)
        preview_root = self.paths["previews"].resolve(strict=False)
        try:
            target.relative_to(preview_root)
        except ValueError as exc:
            raise TeachingPrepValidationError(
                "material preview path is invalid"
            ) from exc
        if not target.is_file():
            self.parse_material_version(record.material_version_id)
            record = self.material_units.preview_record(clean_id)
            target = (self.root / record.preview_relpath).resolve(strict=False)
        if not target.is_file():
            raise TeachingPrepNotFoundError(
                "material preview is unavailable"
            )
        return target

    def create_material_link(
        self,
        *,
        request_token: str,
        lesson_node_id: str,
        material_version_id: str,
        start_unit: int,
        end_unit: int,
        crop: Mapping[str, float] | None,
        purpose: str,
        teacher_note: str | None,
        confirmation_status: str,
    ) -> tuple[LessonMaterialLink, bool]:
        return self.material_units.create_link(
            request_token=_clean_token(request_token),
            lesson_node_id=_clean_entity_id(lesson_node_id),
            material_version_id=_clean_entity_id(material_version_id),
            start_unit=int(start_unit),
            end_unit=int(end_unit),
            crop=_clean_crop(crop),
            purpose=_clean_choice(purpose, _LINK_PURPOSES, "purpose"),
            teacher_note=_clean_optional_text(
                teacher_note,
                "teacher_note",
                maximum=500,
            ),
            confirmation_status=_clean_choice(
                confirmation_status,
                _CONFIRMATION_STATUSES,
                "confirmation_status",
            ),
        )

    def list_material_links(
        self,
        lesson_node_id: str,
    ) -> tuple[LessonMaterialLink, ...]:
        return self.material_units.list_links(
            _clean_entity_id(lesson_node_id)
        )

    def update_material_link(
        self,
        link_id: str,
        *,
        expected_revision: int,
        start_unit: int,
        end_unit: int,
        crop: Mapping[str, float] | None,
        purpose: str,
        teacher_note: str | None,
        confirmation_status: str,
        is_active: bool,
    ) -> LessonMaterialLink:
        if int(expected_revision) <= 0:
            raise TeachingPrepValidationError(
                "expected_revision must be positive"
            )
        return self.material_units.update_link(
            _clean_entity_id(link_id),
            expected_revision=int(expected_revision),
            start_unit=int(start_unit),
            end_unit=int(end_unit),
            crop=_clean_crop(crop),
            purpose=_clean_choice(purpose, _LINK_PURPOSES, "purpose"),
            teacher_note=_clean_optional_text(
                teacher_note,
                "teacher_note",
                maximum=500,
            ),
            confirmation_status=_clean_choice(
                confirmation_status,
                _CONFIRMATION_STATUSES,
                "confirmation_status",
            ),
            is_active=bool(is_active),
        )

    def create_exercise_candidate(
        self,
        *,
        request_token: str,
        lesson_node_id: str,
        question_number: str | None,
        content_label: str | None,
        difficulty: str,
        classroom_use: str,
        estimated_minutes: int | None,
        teaching_focus: str | None,
        teacher_note: str | None,
        selection_status: str,
        answer_status: str,
        question_regions: Sequence[Mapping[str, object]],
        answer_regions: Sequence[Mapping[str, object]],
    ) -> tuple[ExerciseCandidate, bool]:
        return self.exercises.create(
            request_token=_clean_token(request_token),
            lesson_node_id=_clean_entity_id(lesson_node_id),
            question_number=_clean_optional_text(
                question_number,
                "question_number",
                maximum=80,
            ),
            content_label=_clean_optional_text(
                content_label,
                "content_label",
                maximum=500,
            ),
            difficulty=_clean_choice(
                difficulty,
                _DIFFICULTIES,
                "difficulty",
            ),
            classroom_use=_clean_choice(
                classroom_use,
                _CLASSROOM_USES,
                "classroom_use",
            ),
            estimated_minutes=_clean_exercise_minutes(estimated_minutes),
            teaching_focus=_clean_optional_text(
                teaching_focus,
                "teaching_focus",
                maximum=500,
            ),
            teacher_note=_clean_optional_text(
                teacher_note,
                "teacher_note",
                maximum=1_000,
            ),
            selection_status=_clean_choice(
                selection_status,
                _SELECTION_STATUSES,
                "selection_status",
            ),
            answer_status=_clean_choice(
                answer_status,
                _ANSWER_STATUSES,
                "answer_status",
            ),
            question_regions=_clean_exercise_regions(question_regions),
            answer_regions=_clean_exercise_regions(answer_regions),
        )

    def list_exercise_candidates(
        self,
        lesson_node_id: str,
    ) -> tuple[ExerciseCandidate, ...]:
        return self.exercises.list_for_lesson(
            _clean_entity_id(lesson_node_id)
        )

    def update_exercise_candidate(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        question_number: str | None,
        content_label: str | None,
        difficulty: str,
        classroom_use: str,
        estimated_minutes: int | None,
        teaching_focus: str | None,
        teacher_note: str | None,
        selection_status: str,
        answer_status: str,
        question_regions: Sequence[Mapping[str, object]],
        answer_regions: Sequence[Mapping[str, object]],
        is_active: bool,
    ) -> ExerciseCandidate:
        if int(expected_revision) <= 0:
            raise TeachingPrepValidationError(
                "expected_revision must be positive"
            )
        return self.exercises.update(
            _clean_entity_id(candidate_id),
            expected_revision=int(expected_revision),
            question_number=_clean_optional_text(
                question_number,
                "question_number",
                maximum=80,
            ),
            content_label=_clean_optional_text(
                content_label,
                "content_label",
                maximum=500,
            ),
            difficulty=_clean_choice(
                difficulty,
                _DIFFICULTIES,
                "difficulty",
            ),
            classroom_use=_clean_choice(
                classroom_use,
                _CLASSROOM_USES,
                "classroom_use",
            ),
            estimated_minutes=_clean_exercise_minutes(estimated_minutes),
            teaching_focus=_clean_optional_text(
                teaching_focus,
                "teaching_focus",
                maximum=500,
            ),
            teacher_note=_clean_optional_text(
                teacher_note,
                "teacher_note",
                maximum=1_000,
            ),
            selection_status=_clean_choice(
                selection_status,
                _SELECTION_STATUSES,
                "selection_status",
            ),
            answer_status=_clean_choice(
                answer_status,
                _ANSWER_STATUSES,
                "answer_status",
            ),
            question_regions=_clean_exercise_regions(question_regions),
            answer_regions=_clean_exercise_regions(answer_regions),
            is_active=bool(is_active),
        )

    def exercise_region_preview_path(self, region_id: str) -> Path:
        clean_id = _clean_entity_id(region_id)
        record = self.exercises.region_preview_record(clean_id)
        source = self.material_preview_path(record.material_unit_id)
        target = (
            self.paths["previews"]
            / "exercise-regions"
            / f"{clean_id}.png"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        preview_root = self.paths["previews"].resolve(strict=False)
        try:
            target.resolve(strict=False).relative_to(preview_root)
        except ValueError as exc:
            raise TeachingPrepValidationError(
                "exercise preview path is invalid"
            ) from exc
        if not target.is_file():
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            with Image.open(source) as image:
                width, height = image.size
                crop = record.crop
                box = (
                    max(0, min(width - 1, int(crop["x0"] * width))),
                    max(0, min(height - 1, int(crop["y0"] * height))),
                    max(1, min(width, math.ceil(crop["x1"] * width))),
                    max(1, min(height, math.ceil(crop["y1"] * height))),
                )
                if box[2] <= box[0] or box[3] <= box[1]:
                    raise TeachingPrepValidationError(
                        "exercise crop is too small"
                    )
                image.crop(box).convert("RGB").save(temporary, format="PNG")
            temporary.replace(target)
        return target

    def list_available_assessments(self) -> tuple[dict[str, object], ...]:
        if self.assessment_evidence_reader is None:
            return ()
        items = self.assessment_evidence_reader.list_assessments()
        _validate_evidence_value(items)
        return tuple(
            dict(item)
            for item in items
            if isinstance(item, Mapping)
        )

    def list_available_questions(self) -> tuple[dict[str, object], ...]:
        if self.question_evidence_reader is None:
            return ()
        items = self.question_evidence_reader.list_questions()
        _validate_evidence_value(items)
        return tuple(
            dict(item)
            for item in items
            if isinstance(item, Mapping)
        )

    def freeze_resource_pack(
        self,
        *,
        request_token: str,
        lesson_node_id: str,
        class_name: str | None,
        lesson_type: str,
        teacher_context: str | None,
        reference_ppt_intents: Mapping[str, str],
        question_ids: Sequence[int],
        assessment_ids: Sequence[int],
        knowledge_scope: Sequence[str],
        preparation_preferences: Mapping[str, object] | None = None,
        selected_material_link_ids: Sequence[str] | None = None,
        selected_exercise_candidate_ids: Sequence[str] | None = None,
    ) -> tuple[ResourcePackVersion, bool]:
        clean_token = _clean_token(request_token)
        clean_lesson_id = _clean_entity_id(lesson_node_id)
        clean_class = _clean_optional_text(
            class_name,
            "class_name",
            maximum=120,
        )
        clean_lesson_type = _clean_choice(
            lesson_type,
            _LESSON_TYPES,
            "lesson_type",
        )
        clean_context = _clean_optional_text(
            teacher_context,
            "teacher_context",
            maximum=2_000,
        )
        clean_question_ids = _clean_positive_ids(
            question_ids,
            "question_ids",
            maximum=500,
        )
        clean_assessment_ids = _clean_positive_ids(
            assessment_ids,
            "assessment_ids",
            maximum=50,
        )
        if clean_assessment_ids and clean_class is None:
            raise TeachingPrepValidationError(
                "class_name is required for assessment evidence"
            )
        clean_scope = tuple(
            dict.fromkeys(
                _clean_text(value, "knowledge_scope", maximum=160)
                for value in knowledge_scope
            )
        )
        if len(clean_scope) > 100:
            raise TeachingPrepValidationError(
                "knowledge_scope contains too many items"
            )
        clean_preferences = normalize_teaching_preferences(
            (
                preparation_preferences
                if preparation_preferences is not None
                else DEFAULT_TEACHING_PREFERENCES
            )
        )
        clean_intents = {
            _clean_entity_id(link_id): _clean_choice(
                intent,
                _REFERENCE_PPT_INTENTS,
                "reference_ppt_intent",
            )
            for link_id, intent in reference_ppt_intents.items()
        }
        clean_material_link_ids = (
            tuple(
                dict.fromkeys(
                    _clean_entity_id(item)
                    for item in selected_material_link_ids
                )
            )
            if selected_material_link_ids is not None
            else None
        )
        clean_exercise_candidate_ids = (
            tuple(
                dict.fromkeys(
                    _clean_entity_id(item)
                    for item in selected_exercise_candidate_ids
                )
            )
            if selected_exercise_candidate_ids is not None
            else None
        )
        if clean_material_link_ids is not None and not clean_material_link_ids:
            raise TeachingPrepValidationError(
                "at least one material link must be selected"
            )
        request_values = {
            "lesson_node_id": clean_lesson_id,
            "class_name": clean_class,
            "lesson_type": clean_lesson_type,
            "teacher_context": clean_context,
            "reference_ppt_intents": clean_intents,
            "question_ids": clean_question_ids,
            "assessment_ids": clean_assessment_ids,
            "knowledge_scope": clean_scope,
            "preparation_preferences": clean_preferences,
            "selected_material_link_ids": clean_material_link_ids,
            "selected_exercise_candidate_ids": clean_exercise_candidate_ids,
        }
        request_hash = hashlib.sha256(
            json.dumps(
                request_values,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        existing = self.resource_packs.find_idempotent(
            request_token=clean_token,
            request_hash=request_hash,
        )
        if existing is not None:
            return existing, False
        question_evidence = (
            self.question_evidence_reader.read(
                {"question_ids": list(clean_question_ids)}
            )
            if self.question_evidence_reader is not None
            else _unavailable_evidence(
                "question",
                "question_evidence_reader_unavailable",
            )
        )
        assessment_evidence = (
            self.assessment_evidence_reader.read(
                {
                    "assessment_ids": list(clean_assessment_ids),
                    "class_name": clean_class,
                    "knowledge_scope": list(clean_scope),
                }
            )
            if self.assessment_evidence_reader is not None
            else _unavailable_evidence(
                "assessment",
                "assessment_evidence_reader_unavailable",
                class_name=clean_class,
            )
        )
        _validate_evidence_value(question_evidence)
        _validate_evidence_value(assessment_evidence)
        if (
            clean_assessment_ids
            and str(assessment_evidence.get("class_name") or "").strip()
            != clean_class
        ):
            raise TeachingPrepConflictError(
                "assessment evidence class does not match resource pack"
            )
        return self.resource_packs.freeze(
            request_token=clean_token,
            request_hash=request_hash,
            lesson_node_id=clean_lesson_id,
            class_name=clean_class,
            lesson_type=clean_lesson_type,
            teacher_context=clean_context,
            reference_ppt_intents=clean_intents,
            question_evidence=dict(question_evidence),
            assessment_evidence=dict(assessment_evidence),
            preparation_preferences=clean_preferences,
            selected_material_link_ids=clean_material_link_ids,
            selected_exercise_candidate_ids=clean_exercise_candidate_ids,
        )

    def resource_pack_preflight(
        self,
        lesson_node_id: str,
        *,
        reference_ppt_intents: Mapping[str, str],
        selected_material_link_ids: Sequence[str] | None,
        selected_exercise_candidate_ids: Sequence[str] | None,
    ) -> dict[str, object]:
        clean_intents = {
            _clean_entity_id(link_id): _clean_choice(
                intent,
                _REFERENCE_PPT_INTENTS,
                "reference_ppt_intent",
            )
            for link_id, intent in reference_ppt_intents.items()
        }
        return self.resource_packs.preflight_selection(
            lesson_node_id=_clean_entity_id(lesson_node_id),
            reference_ppt_intents=clean_intents,
            selected_material_link_ids=(
                tuple(
                    dict.fromkeys(
                        _clean_entity_id(item)
                        for item in selected_material_link_ids
                    )
                )
                if selected_material_link_ids is not None
                else None
            ),
            selected_exercise_candidate_ids=(
                tuple(
                    dict.fromkeys(
                        _clean_entity_id(item)
                        for item in selected_exercise_candidate_ids
                    )
                )
                if selected_exercise_candidate_ids is not None
                else None
            ),
        )

    def list_resource_packs(
        self,
        lesson_node_id: str,
    ) -> tuple[ResourcePackVersion, ...]:
        return self.resource_packs.list_for_lesson(
            _clean_entity_id(lesson_node_id)
        )

    def get_resource_pack(self, pack_id: str) -> ResourcePackVersion:
        return self.resource_packs.get(_clean_entity_id(pack_id))

    def resource_pack_status(
        self,
        lesson_node_id: str,
    ) -> dict[str, object]:
        return self.resource_packs.status(
            _clean_entity_id(lesson_node_id)
        )

    def lesson_draft_preflight(
        self,
        pack_id: str,
        *,
        mode: str,
    ) -> dict[str, object]:
        clean_mode = _clean_choice(
            mode,
            {"local_template", "model"},
            "draft_mode",
        )
        pack = self.resource_packs.get(_clean_entity_id(pack_id))
        return draft_preflight(
            pack,
            mode=clean_mode,
            model_available=_model_adapter_available(
                self.lesson_model_adapter
            ),
            model_label=self.lesson_model_label,
        )

    def generate_lesson_draft(
        self,
        pack_id: str,
        *,
        operation_id: str,
        mode: str,
        confirmed: bool,
    ) -> tuple[LessonDraftVersion, bool]:
        clean_pack_id = _clean_entity_id(pack_id)
        clean_operation_id = _clean_token(operation_id)
        clean_mode = _clean_choice(
            mode,
            {"local_template", "model"},
            "draft_mode",
        )
        if not confirmed:
            raise TeachingPrepValidationError(
                "draft generation requires teacher confirmation"
            )
        if clean_mode == "model" and not _model_adapter_available(
            self.lesson_model_adapter
        ):
            raise TeachingPrepValidationError(
                "lesson model is unavailable or not authorized"
            )
        pack = self.resource_packs.get(clean_pack_id)
        generation_hash = _stable_hash(
            {
                "resource_pack_id": pack.id,
                "resource_pack_sha256": pack.pack_sha256,
                "mode": clean_mode,
            }
        )
        existing = self.lesson_drafts.find_generation(
            operation_id=clean_operation_id,
            request_hash=generation_hash,
        )
        if existing is not None:
            return existing, False
        self.lesson_drafts.begin_generation(
            operation_id=clean_operation_id,
            request_hash=generation_hash,
            resource_pack_id=pack.id,
            source_kind=clean_mode,
        )
        try:
            if clean_mode == "local_template":
                raw = build_local_template(pack)
            else:
                adapter = self.lesson_model_adapter
                if adapter is None:
                    raise TeachingPrepValidationError(
                        "lesson model is unavailable or not authorized"
                    )
                model_payload = deepcopy(pack.payload)
                model_payload["preparation_preferences"] = (
                    resolve_teaching_preferences(
                        pack.payload.get("preparation_preferences")
                    )
                )
                raw = adapter.generate(
                    operation_id=clean_operation_id,
                    resource_pack=model_payload,
                )
            draft = validate_draft_payload(raw, pack)
            if clean_mode == "model" and not draft.get("slide_adaptations"):
                raise TeachingPrepValidationError(
                    "lesson model must classify every frozen reference slide"
                )
            capacity = calculate_capacity(pack, draft)
            saved = self.lesson_drafts.finish_generation(
                operation_id=clean_operation_id,
                request_token=clean_operation_id,
                request_hash=generation_hash,
                resource_pack_id=pack.id,
                source_kind=clean_mode,
                model_label=(
                    self.lesson_model_label
                    if clean_mode == "model"
                    else None
                ),
                payload=draft,
                capacity=capacity,
            )
        except Exception as exc:
            self.lesson_drafts.fail_generation(
                clean_operation_id,
                (
                    "draft_output_invalid"
                    if isinstance(exc, TeachingPrepValidationError)
                    else "draft_generation_failed"
                ),
            )
            raise
        return saved, True

    def revise_lesson_draft(
        self,
        draft_id: str,
        *,
        request_token: str,
        payload: Mapping[str, object],
        confirmed: bool,
    ) -> tuple[LessonDraftVersion, bool]:
        current = self.lesson_drafts.get(_clean_entity_id(draft_id))
        pack = self.resource_packs.get(current.resource_pack_id)
        clean_payload = validate_draft_payload(payload, pack)
        capacity = calculate_capacity(pack, clean_payload)
        revision_hash = _stable_hash(
            {
                "based_on_draft_id": current.id,
                "payload": clean_payload,
                "confirmed": bool(confirmed),
            }
        )
        return self.lesson_drafts.create_revision(
            request_token=_clean_token(request_token),
            request_hash=revision_hash,
            based_on_draft_id=current.id,
            payload=clean_payload,
            capacity=capacity,
            status="confirmed" if confirmed else "draft",
        )

    def cancel_lesson_draft_generation(
        self,
        operation_id: str,
    ) -> tuple[str, bool]:
        clean_operation_id = _clean_token(operation_id)
        created = self.lesson_drafts.cancel_generation(clean_operation_id)
        return clean_operation_id, created

    def get_lesson_draft(self, draft_id: str) -> LessonDraftVersion:
        return self.lesson_drafts.get(_clean_entity_id(draft_id))

    def preview_lesson_draft_capacity(
        self,
        draft_id: str,
        *,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        draft = self.lesson_drafts.get(_clean_entity_id(draft_id))
        pack = self.resource_packs.get(draft.resource_pack_id)
        normalized = validate_draft_payload(payload, pack)
        return calculate_capacity(pack, normalized)

    def list_lesson_drafts(
        self,
        pack_id: str,
    ) -> tuple[LessonDraftVersion, ...]:
        return self.lesson_drafts.list_for_pack(_clean_entity_id(pack_id))

    def create_slide_plan(
        self,
        draft_id: str,
        *,
        request_token: str,
    ) -> tuple[SlidePlanVersion, bool]:
        clean_draft_id = _clean_entity_id(draft_id)
        clean_token = _clean_token(request_token)
        request_hash = _stable_hash(
            {"lesson_draft_id": clean_draft_id}
        )
        existing = self.slide_plans.find_idempotent(
            request_token=clean_token,
            request_hash=request_hash,
        )
        if existing is not None:
            return self._effective_slide_plan(existing), False
        draft = self.lesson_drafts.get(clean_draft_id)
        if draft.status != "confirmed":
            raise TeachingPrepValidationError(
                "slide plan requires a teacher-confirmed lesson draft"
            )
        source_status = self.resource_packs.source_status(
            draft.resource_pack_id
        )
        if source_status["sources_changed"]:
            raise TeachingPrepConflictError(
                "resource pack sources changed; freeze and confirm a new draft"
            )
        pack = self.resource_packs.get(draft.resource_pack_id)
        payload, source_ppt_state = build_slide_plan_payload(pack, draft)
        clean_payload = validate_plan_payload(payload)
        item, created = self.slide_plans.create(
            request_token=clean_token,
            request_hash=request_hash,
            lesson_draft_id=draft.id,
            resource_pack_id=pack.id,
            based_on_plan_id=None,
            source_ppt_state_sha256=source_ppt_state,
            status="in_review",
            payload=clean_payload,
        )
        return self._effective_slide_plan(item), created

    def revise_slide_plan(
        self,
        plan_id: str,
        *,
        request_token: str,
        operation_reviews: Sequence[Mapping[str, object]],
        approve_low_risk_deletions: bool,
        review_note: str | None,
    ) -> tuple[SlidePlanVersion, bool]:
        current = self.slide_plans.get(_clean_entity_id(plan_id))
        clean_token = _clean_token(request_token)
        reviews: dict[str, dict[str, object]] = {}
        for raw in operation_reviews:
            operation_id = _clean_entity_id(
                str(raw.get("operation_id") or "")
            )
            if operation_id in reviews:
                raise TeachingPrepValidationError(
                    "slide operation review is duplicated"
                )
            decision = _clean_choice(
                str(raw.get("decision") or ""),
                {"proposed", "approved", "rejected"},
                "slide_operation_decision",
            )
            planned = raw.get("planned_minutes")
            if (
                isinstance(planned, bool)
                or not isinstance(planned, int)
                or not 0 <= planned <= 120
            ):
                raise TeachingPrepValidationError(
                    "planned_minutes is invalid"
                )
            reviews[operation_id] = {
                "decision": decision,
                "reason": _clean_text(
                    str(raw.get("reason") or ""),
                    "reason",
                    maximum=1_000,
                ),
                "planned_minutes": planned,
                "teacher_note": _clean_optional_text(
                    (
                        str(raw["teacher_note"])
                        if raw.get("teacher_note") is not None
                        else None
                    ),
                    "teacher_note",
                    maximum=1_000,
                ),
            }
        clean_review_note = _clean_optional_text(
            review_note,
            "review_note",
            maximum=1_000,
        )
        revision_hash = _stable_hash(
            {
                "based_on_plan_id": current.id,
                "operation_reviews": reviews,
                "approve_low_risk_deletions": bool(
                    approve_low_risk_deletions
                ),
                "review_note": clean_review_note,
            }
        )
        existing = self.slide_plans.find_idempotent(
            request_token=clean_token,
            request_hash=revision_hash,
        )
        if existing is not None:
            return self._effective_slide_plan(existing), False
        if self.resource_packs.source_status(
            current.resource_pack_id
        )["sources_changed"]:
            raise TeachingPrepConflictError(
                "slide plan is invalid because frozen sources changed"
            )
        payload = deepcopy(current.payload)
        operations = payload.get("operations")
        if not isinstance(operations, list):
            raise RuntimeError("stored slide plan operations are invalid")
        known_ids = {
            str(item.get("operation_id"))
            for item in operations
            if isinstance(item, Mapping)
        }
        if set(reviews) - known_ids:
            raise TeachingPrepValidationError(
                "review refers to an unknown slide operation"
            )
        history = payload.get("approval_history")
        if not isinstance(history, list):
            raise RuntimeError("stored slide plan history is invalid")
        history_start = len(history)
        changed_at = datetime.now(UTC).isoformat()
        for raw_operation in operations:
            if not isinstance(raw_operation, dict):
                raise RuntimeError("stored slide operation is invalid")
            operation_id = str(raw_operation.get("operation_id") or "")
            review = reviews.get(operation_id)
            if (
                review is None
                and approve_low_risk_deletions
                and raw_operation.get("kind") == "delete_slide"
                and raw_operation.get("risk") == "low"
                and raw_operation.get("decision") == "proposed"
            ):
                review = {
                    "decision": "approved",
                    "reason": raw_operation.get("reason"),
                    "planned_minutes": raw_operation.get(
                        "planned_minutes",
                        0,
                    ),
                    "teacher_note": clean_review_note,
                }
            if review is None:
                continue
            before = str(raw_operation.get("decision") or "")
            before_reason = raw_operation.get("reason")
            before_minutes = raw_operation.get("planned_minutes")
            before_teacher_note = raw_operation.get("teacher_note")
            after = str(review["decision"])
            candidate = {
                **raw_operation,
                **review,
            }
            if after == "approved":
                require_approval_allowed(candidate)
            raw_operation.update(review)
            if (
                before != after
                or before_reason != review["reason"]
                or before_minutes != review["planned_minutes"]
                or before_teacher_note != review["teacher_note"]
            ):
                history.append(
                    {
                        "operation_id": operation_id,
                        "from": before,
                        "to": after,
                        "at": changed_at,
                        "note": clean_review_note,
                    }
                )
        if len(history) == history_start:
            raise TeachingPrepValidationError(
                "slide plan review made no changes"
            )
        clean_payload = validate_plan_payload(payload)
        decisions = {
            str(item["decision"])
            for item in clean_payload["operations"]
        }
        status_value = (
            "approved" if "proposed" not in decisions else "in_review"
        )
        item, created = self.slide_plans.create(
            request_token=clean_token,
            request_hash=revision_hash,
            lesson_draft_id=current.lesson_draft_id,
            resource_pack_id=current.resource_pack_id,
            based_on_plan_id=current.id,
            source_ppt_state_sha256=current.source_ppt_state_sha256,
            status=status_value,
            payload=clean_payload,
        )
        return self._effective_slide_plan(item), created

    def get_slide_plan(self, plan_id: str) -> SlidePlanVersion:
        return self._effective_slide_plan(
            self.slide_plans.get(_clean_entity_id(plan_id))
        )

    def list_slide_plans(
        self,
        draft_id: str,
    ) -> tuple[SlidePlanVersion, ...]:
        return tuple(
            self._effective_slide_plan(item)
            for item in self.slide_plans.list_for_draft(
                _clean_entity_id(draft_id)
            )
        )

    def slide_plan_preview(
        self,
        plan_id: str,
        *,
        include_proposed: bool,
    ) -> dict[str, object]:
        item = self.slide_plans.get(_clean_entity_id(plan_id))
        changed = bool(
            self.resource_packs.source_status(
                item.resource_pack_id
            )["sources_changed"]
        )
        return diff_preview(
            item.payload,
            include_proposed=bool(include_proposed),
            source_changed=changed,
        )

    def _effective_slide_plan(
        self,
        item: SlidePlanVersion,
    ) -> SlidePlanVersion:
        changed = bool(
            self.resource_packs.source_status(
                item.resource_pack_id
            )["sources_changed"]
        )
        return replace(item, status="invalidated") if changed else item

    def execute_slide_plan(
        self,
        plan_id: str,
        *,
        operation_id: str,
        confirmed: bool,
    ) -> tuple[PptxExecutionRun, PptxVersion | None, bool]:
        return self._execute_slide_plan_sync(
            plan_id,
            operation_id=operation_id,
            confirmed=confirmed,
            continue_existing=False,
        )

    def start_pptx_execution(
        self,
        plan_id: str,
        *,
        operation_id: str,
        confirmed: bool,
    ) -> tuple[PptxExecutionRun, bool]:
        if not confirmed:
            raise TeachingPrepValidationError(
                "PPTX copy execution requires explicit confirmation"
            )
        if self.wps_adapter is None:
            raise TeachingPrepValidationError(
                "WPS execution adapter is not configured"
            )
        clean_plan_id = _clean_entity_id(plan_id)
        clean_operation_id = _clean_token(operation_id)
        plan = self._effective_slide_plan(self.slide_plans.get(clean_plan_id))
        if plan.status != "approved":
            raise TeachingPrepValidationError(
                "only a current approved slide plan can execute"
            )
        source_presentations = plan.payload.get("source_presentations")
        if (
            not isinstance(source_presentations, list)
            or len(source_presentations) != 1
            or not isinstance(source_presentations[0], Mapping)
        ):
            raise TeachingPrepValidationError(
                "automatic execution requires exactly one source PPTX"
            )
        source_record = dict(source_presentations[0])
        source_version_id = _clean_entity_id(
            str(source_record.get("material_version_id") or "")
        )
        source_sha256 = str(source_record.get("content_sha256") or "")
        source_version = self.catalog.get_material_version(source_version_id)
        if (
            source_version.material_type != "pptx"
            or source_version.content_sha256 != source_sha256
        ):
            raise TeachingPrepConflictError(
                "source PPTX version no longer matches the approved plan"
            )
        source_path = self.catalog.get_material_location(source_version_id)
        if not source_path.is_file() or _sha256_file(source_path) != source_sha256:
            raise TeachingPrepConflictError(
                "source PPTX is missing or changed after plan approval"
            )
        preview = self.slide_plan_preview(clean_plan_id, include_proposed=False)
        if not preview["valid_for_execution"]:
            raise TeachingPrepValidationError(
                "slide plan is not valid for execution"
            )
        request_hash = execution_digest(
            {
                "slide_plan_id": clean_plan_id,
                "source_material_version_id": source_version_id,
                "source_sha256": source_sha256,
                "expected_slide_count": int(preview["after_slide_count"]),
            }
        )
        run, created = self.pptx_executions.begin(
            operation_id=clean_operation_id,
            request_hash=request_hash,
            slide_plan_id=clean_plan_id,
            source_material_version_id=source_version_id,
            source_sha256=source_sha256,
            expected_slide_count=int(preview["after_slide_count"]),
        )
        return self._execution_with_storage(run), created

    def process_pptx_execution(self, run_id: str) -> None:
        run = self.pptx_executions.get(_clean_entity_id(run_id))
        if run.status != "running":
            return
        self._execute_slide_plan_sync(
            run.slide_plan_id,
            operation_id=run.operation_id,
            confirmed=True,
            continue_existing=True,
        )

    def _execute_slide_plan_sync(
        self,
        plan_id: str,
        *,
        operation_id: str,
        confirmed: bool,
        continue_existing: bool,
    ) -> tuple[PptxExecutionRun, PptxVersion | None, bool]:
        if not confirmed:
            raise TeachingPrepValidationError(
                "PPTX copy execution requires explicit confirmation"
            )
        if self.wps_adapter is None:
            raise TeachingPrepValidationError(
                "WPS execution adapter is not configured"
            )
        clean_plan_id = _clean_entity_id(plan_id)
        clean_operation_id = _clean_token(operation_id)
        plan = self._effective_slide_plan(
            self.slide_plans.get(clean_plan_id)
        )
        if plan.status != "approved":
            raise TeachingPrepValidationError(
                "only a current approved slide plan can execute"
            )
        source_presentations = plan.payload.get("source_presentations")
        if (
            not isinstance(source_presentations, list)
            or len(source_presentations) != 1
            or not isinstance(source_presentations[0], Mapping)
        ):
            raise TeachingPrepValidationError(
                "automatic execution requires exactly one source PPTX"
            )
        source_record = dict(source_presentations[0])
        source_version_id = _clean_entity_id(
            str(source_record.get("material_version_id") or "")
        )
        source_sha256 = str(source_record.get("content_sha256") or "")
        source_version = self.catalog.get_material_version(source_version_id)
        if (
            source_version.material_type != "pptx"
            or source_version.content_sha256 != source_sha256
        ):
            raise TeachingPrepConflictError(
                "source PPTX version no longer matches the approved plan"
            )
        source_path = self.catalog.get_material_location(source_version_id)
        if not source_path.is_file():
            raise TeachingPrepValidationError(
                "source PPTX requires relocation"
            )
        if _sha256_file(source_path) != source_sha256:
            raise TeachingPrepConflictError(
                "source PPTX changed after plan approval"
            )
        preview = self.slide_plan_preview(
            clean_plan_id,
            include_proposed=False,
        )
        if not preview["valid_for_execution"]:
            raise TeachingPrepValidationError(
                "slide plan is not valid for execution"
            )
        expected_slide_count = int(preview["after_slide_count"])
        request_hash = execution_digest(
            {
                "slide_plan_id": clean_plan_id,
                "source_material_version_id": source_version_id,
                "source_sha256": source_sha256,
                "expected_slide_count": expected_slide_count,
            }
        )
        run, created = self.pptx_executions.begin(
            operation_id=clean_operation_id,
            request_hash=request_hash,
            slide_plan_id=clean_plan_id,
            source_material_version_id=source_version_id,
            source_sha256=source_sha256,
            expected_slide_count=expected_slide_count,
        )
        if not created and not (continue_existing and run.status == "running"):
            version = (
                self.pptx_executions.get_version(run.published_version_id)
                if run.published_version_id is not None
                and run.status == "published"
                else None
            )
            return self._execution_with_storage(run), version, False
        staging = self._execution_staging(run.id)
        source_copy = staging / "source-copy.pptx"
        candidate = staging / "candidate.pptx"
        preview_dir = staging / "wps-previews"
        output: Path | None = None
        publication_moved = False
        published = False
        reserved_version_id: str | None = None
        published_preview_dir: Path | None = None
        try:
            self._require_generation_budget(run.id)
            staging.mkdir(parents=True, exist_ok=False)
            preview_dir.mkdir(parents=True, exist_ok=False)
            shutil.copy2(source_path, source_copy)
            if (
                _sha256_file(source_copy) != source_sha256
                or _sha256_file(source_path) != source_sha256
            ):
                raise TeachingPrepConflictError(
                    "source PPTX changed while creating the isolated copy"
                )
            assets_dir = staging / "assets"

            def resolve_asset(asset_ref: str, item_id: str) -> Path:
                match = _EXERCISE_PREVIEW_REF.fullmatch(asset_ref)
                if match is None:
                    raise TeachingPrepValidationError(
                        "approved image asset reference is invalid"
                    )
                source_asset = self.exercise_region_preview_path(
                    match.group(1)
                )
                assets_dir.mkdir(parents=True, exist_ok=True)
                target = assets_dir / f"{item_id}.png"
                shutil.copy2(source_asset, target)
                return target

            executor_request = build_executor_request(
                plan.payload,
                source_sha256=source_sha256,
                source_copy=source_copy,
                candidate=candidate,
                preview_dir=preview_dir,
                resolve_asset=resolve_asset,
            )
            remaining_budget_ms = self._remaining_generation_budget_ms(run.id)
            executor_request["performance_budget"] = {
                "timeout_milliseconds": min(
                    _WPS_EXECUTION_TIMEOUT_SECONDS * 1000,
                    remaining_budget_ms,
                ),
                "total_budget_ms": _LESSON_GENERATION_BUDGET_MS,
                "remaining_budget_ms": remaining_budget_ms,
            }
            self.pptx_executions.mark_wps_started(run.id)
            raw_report = self.wps_adapter.execute(
                operation_id=clean_operation_id,
                plan=executor_request,
            )
            self._require_generation_budget(run.id)
            report = safe_execution_report(raw_report)
            self.pptx_executions.set_verifying(run.id, report)
            verification = verify_candidate_with_deadline(
                candidate=candidate,
                preview_dir=preview_dir,
                plan_payload=plan.payload,
                expected_slide_count=expected_slide_count,
                source_path=source_path,
                source_sha256=source_sha256,
                execution_report=report,
                parser=self.material_parser,
                timeout_seconds=(
                    self._remaining_generation_budget_ms(run.id) / 1000
                ),
            )
            self._require_generation_budget(run.id)
            publication_deadline_at = self._generation_publish_deadline(run.id)
            publication_verification = {
                **verification,
                "generation_deadline_at": publication_deadline_at,
            }
            pack = self.resource_packs.get(plan.resource_pack_id)
            version, relative = self.pptx_executions.begin_publish(
                run_id=run.id,
                lesson_node_id=pack.lesson_node_id,
                slide_plan_id=plan.id,
                slide_count=expected_slide_count,
                verification_report=publication_verification,
            )
            reserved_version_id = version.id
            output = self._controlled_output(relative)
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists():
                raise TeachingPrepConflictError(
                    "reserved PPTX output already exists"
                )
            self._require_generation_budget(run.id)
            os.rename(candidate, output)
            publication_moved = True
            self._require_generation_budget(run.id)
            version = self.pptx_executions.finish_publish(
                run_id=run.id,
                version_id=version.id,
                output_sha256=str(verification["candidate_sha256"]),
                deadline_at=publication_deadline_at,
            )
            published = True
            self._require_generation_budget(run.id)
            published_preview_dir = (
                self.paths["previews"] / "pptx-versions" / version.id
            )
            published_preview_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(preview_dir, published_preview_dir)
            self._require_generation_budget(run.id)
            return (
                self._execution_with_storage(
                    self.pptx_executions.get(run.id)
                ),
                version,
                True,
            )
        except Exception as exc:
            if published_preview_dir is not None:
                shutil.rmtree(published_preview_dir, ignore_errors=True)
            error_code = _execution_error_code(exc)
            if published and reserved_version_id is not None:
                self.pptx_executions.revoke_published(
                    run_id=run.id,
                    version_id=reserved_version_id,
                    error_code=error_code,
                )
                _restore_candidate_from_output(
                    candidate=candidate,
                    output=output,
                )
            else:
                if publication_moved:
                    _restore_candidate_from_output(
                        candidate=candidate,
                        output=output,
                    )
                self.pptx_executions.fail(run.id, _execution_error_code(exc))
            return (
                self._execution_with_storage(
                    self.pptx_executions.get(run.id)
                ),
                None,
                True,
            )

    def get_pptx_execution(self, run_id: str) -> PptxExecutionRun:
        return self._execution_with_storage(
            self.pptx_executions.get(_clean_entity_id(run_id))
        )

    def list_lesson_pptx_versions(
        self,
        lesson_node_id: str,
    ) -> tuple[dict[str, object], ...]:
        clean_lesson_id = _clean_entity_id(lesson_node_id)
        result: list[dict[str, object]] = []
        for version, is_current, current_revision in (
            self.pptx_executions.list_versions_for_lesson(clean_lesson_id)
        ):
            output = self._controlled_output(
                self.pptx_executions.version_output_relpath(version.id)
            )
            file_ok = (
                output.is_file()
                and version.output_sha256 is not None
                and _sha256_file(output) == version.output_sha256
            )
            result.append(
                {
                    "version": version,
                    "is_current": is_current,
                    "current_revision": current_revision,
                    "file_verified": file_ok,
                    "preview_url": (
                        f"/api/teaching-prep/pptx-versions/{version.id}/preview"
                    ),
                }
            )
        return tuple(result)

    def activate_pptx_version(
        self,
        version_id: str,
        *,
        expected_revision: int | None,
    ) -> tuple[PptxVersion, int, bool]:
        clean_id = _clean_entity_id(version_id)
        version = self.pptx_executions.get_version(clean_id)
        output = self._controlled_output(
            self.pptx_executions.version_output_relpath(clean_id)
        )
        if (
            not output.is_file()
            or version.output_sha256 is None
            or _sha256_file(output) != version.output_sha256
        ):
            raise TeachingPrepConflictError(
                "PPTX file is missing or changed; current version was not updated"
            )
        return self.pptx_executions.activate_version(
            clean_id,
            expected_revision=expected_revision,
        )

    def pptx_version_preview_path(
        self,
        version_id: str,
        *,
        slide_number: int = 1,
    ) -> Path:
        clean_id = _clean_entity_id(version_id)
        version = self.pptx_executions.get_version(clean_id)
        if slide_number < 1 or slide_number > version.slide_count:
            raise TeachingPrepValidationError("preview slide number is invalid")
        root = (self.paths["previews"] / "pptx-versions" / clean_id).resolve(
            strict=False
        )
        target = (root / f"slide-{slide_number:05d}.png").resolve(
            strict=False
        )
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise TeachingPrepValidationError(
                "PPTX preview path is invalid"
            ) from exc
        if not target.is_file():
            raise TeachingPrepNotFoundError("PPTX preview is unavailable")
        return target

    def get_lesson_generation_performance(
        self,
        run_id: str,
    ) -> LessonGenerationPerformance:
        return self.pptx_executions.performance(
            _clean_entity_id(run_id),
            budget_ms=_LESSON_GENERATION_BUDGET_MS,
        )

    def _require_generation_budget(self, run_id: str) -> None:
        self._remaining_generation_budget_ms(run_id)

    def _remaining_generation_budget_ms(self, run_id: str) -> int:
        performance = self.pptx_executions.performance(
            run_id,
            budget_ms=_LESSON_GENERATION_BUDGET_MS,
        )
        remaining = (
            _LESSON_GENERATION_BUDGET_MS
            - performance.total_machine_elapsed_ms
        )
        if remaining <= 0:
            raise TimeoutError("lesson generation budget exceeded")
        return remaining

    def _generation_publish_deadline(self, run_id: str) -> str:
        """Persist the remaining hard deadline alongside a reserved version.

        The in-process monotonic clock protects live validation.  A restart
        needs an equivalent durable wall-clock deadline so recovery cannot
        turn a timed-out publication into a downloadable file.
        """
        remaining = self._remaining_generation_budget_ms(run_id)
        return (
            datetime.now(UTC)
            + timedelta(milliseconds=remaining)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def list_pptx_executions(
        self,
        plan_id: str,
    ) -> tuple[PptxExecutionRun, ...]:
        return tuple(
            self._execution_with_storage(item)
            for item in self.pptx_executions.list_for_plan(
                _clean_entity_id(plan_id)
            )
        )

    def cancel_pptx_execution(self, run_id: str) -> PptxExecutionRun:
        return self._execution_with_storage(
            self.pptx_executions.cancel(_clean_entity_id(run_id))
        )

    def recover_pptx_execution(
        self,
        run_id: str,
    ) -> tuple[PptxExecutionRun, PptxVersion | None]:
        clean_id = _clean_entity_id(run_id)
        run = self.pptx_executions.get(clean_id)
        if run.status != "interrupted":
            raise TeachingPrepConflictError(
                "only an interrupted execution can be recovered"
            )
        if run.published_version_id is None:
            raise TeachingPrepConflictError(
                "interrupted execution has no verified publication to recover"
            )
        verification = run.verification_report
        expected_output_sha256 = (
            str(verification.get("candidate_sha256") or "")
            if isinstance(verification, Mapping)
            else ""
        )
        if not re.fullmatch(r"[0-9a-f]{64}", expected_output_sha256):
            raise TeachingPrepConflictError(
                "interrupted execution has no trusted candidate fingerprint"
            )
        publication_deadline_at = _generation_deadline_from_verification(
            verification
        )
        output = self._controlled_output(
            self.pptx_executions.version_output_relpath(
                run.published_version_id
            )
        )
        candidate = self._execution_staging(run.id) / "candidate.pptx"
        if (
            publication_deadline_at is None
            or not _generation_deadline_is_open(publication_deadline_at)
        ):
            self.pptx_executions.fail(
                run.id,
                "generation_budget_exceeded",
            )
            _restore_candidate_from_output(
                candidate=candidate,
                output=output,
            )
            return (
                self._execution_with_storage(
                    self.pptx_executions.get(run.id)
                ),
                None,
            )
        source_path = self.catalog.get_material_location(
            run.source_material_version_id
        )
        if (
            not source_path.is_file()
            or _sha256_file(source_path) != run.source_sha256
        ):
            raise TeachingPrepConflictError(
                "source PPTX changed before publication recovery"
            )
        if not output.is_file():
            if not candidate.is_file():
                raise TeachingPrepConflictError(
                    "interrupted publication has no recoverable candidate"
                )
            if _sha256_file(candidate) != expected_output_sha256:
                raise TeachingPrepConflictError(
                    "interrupted candidate fingerprint changed"
                )
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists():
                raise TeachingPrepConflictError(
                    "reserved PPTX output already exists"
                )
            _require_generation_deadline(publication_deadline_at)
            os.rename(candidate, output)
        if _sha256_file(output) != expected_output_sha256:
            raise TeachingPrepConflictError(
                "interrupted output fingerprint changed"
            )
        published = False
        try:
            version = self.pptx_executions.finish_publish(
                run_id=run.id,
                version_id=run.published_version_id,
                output_sha256=expected_output_sha256,
                deadline_at=publication_deadline_at,
            )
            published = True
            _require_generation_deadline(publication_deadline_at)
        except TimeoutError:
            if published:
                self.pptx_executions.revoke_published(
                    run_id=run.id,
                    version_id=run.published_version_id,
                    error_code="generation_budget_exceeded",
                )
            else:
                self.pptx_executions.fail(
                    run.id,
                    "generation_budget_exceeded",
                )
            _restore_candidate_from_output(
                candidate=candidate,
                output=output,
            )
            return (
                self._execution_with_storage(
                    self.pptx_executions.get(run.id)
                ),
                None,
            )
        return (
            self._execution_with_storage(
                self.pptx_executions.get(run.id)
            ),
            version,
        )

    def discard_pptx_staging(self, run_id: str) -> PptxExecutionRun:
        clean_id = _clean_entity_id(run_id)
        run = self.pptx_executions.get(clean_id)
        if run.status not in {
            "failed",
            "cancelled",
            "interrupted",
            "published",
        }:
            raise TeachingPrepConflictError(
                "active execution staging cannot be discarded"
            )
        staging = self._execution_staging(run.id)
        if staging.exists():
            shutil.rmtree(staging)
        return self._execution_with_storage(
            self.pptx_executions.get(run.id)
        )

    def pptx_download(
        self,
        version_id: str,
    ) -> tuple[Path, str]:
        clean_id = _clean_entity_id(version_id)
        version = self.pptx_executions.get_version(clean_id)
        output = self._controlled_output(
            self.pptx_executions.version_output_relpath(clean_id)
        )
        if (
            not output.is_file()
            or _sha256_file(output) != version.output_sha256
        ):
            raise TeachingPrepConflictError(
                "published PPTX file is unavailable or changed"
            )
        return output, version.output_filename

    def derive_class_variant(
        self,
        base_pack_id: str,
        *,
        request_token: str,
        class_name: str,
        teacher_context: str | None,
        assessment_ids: Sequence[int],
        knowledge_scope: Sequence[str],
        prior_review_ids: Sequence[str] = (),
    ) -> tuple[ClassVariant, ResourcePackVersion, bool]:
        clean_token = _clean_token(request_token)
        base = self.resource_packs.get(_clean_entity_id(base_pack_id))
        clean_class = _clean_text(
            class_name,
            "class_name",
            maximum=120,
        )
        clean_context = _clean_optional_text(
            teacher_context,
            "teacher_context",
            maximum=2_000,
        )
        clean_assessment_ids = _clean_positive_ids(
            assessment_ids,
            "assessment_ids",
            maximum=50,
        )
        clean_scope = tuple(
            dict.fromkeys(
                _clean_text(value, "knowledge_scope", maximum=160)
                for value in knowledge_scope
            )
        )
        if len(clean_scope) > 100:
            raise TeachingPrepValidationError(
                "knowledge_scope contains too many items"
            )
        review_ids = tuple(
            dict.fromkeys(_clean_entity_id(value) for value in prior_review_ids)
        )
        if len(review_ids) > 20:
            raise TeachingPrepValidationError(
                "too many post-lesson reviews were selected"
            )
        reviews = self.teaching_delivery.get_reviews(review_ids)
        for review in reviews:
            if review.lesson_node_id != base.lesson_node_id:
                raise TeachingPrepConflictError(
                    "post-lesson review belongs to another lesson"
                )
            if (
                not review.use_in_next_version
                or (review.class_name or "").casefold()
                != clean_class.casefold()
            ):
                raise TeachingPrepConflictError(
                    "post-lesson review is not available for this class variant"
                )
        assessment_evidence = (
            self.assessment_evidence_reader.read(
                {
                    "assessment_ids": list(clean_assessment_ids),
                    "class_name": clean_class,
                    "knowledge_scope": list(clean_scope),
                }
            )
            if self.assessment_evidence_reader is not None
            else _unavailable_evidence(
                "assessment",
                "assessment_evidence_reader_unavailable",
                class_name=clean_class,
            )
        )
        _validate_evidence_value(assessment_evidence)
        if (
            clean_assessment_ids
            and str(assessment_evidence.get("class_name") or "").strip()
            != clean_class
        ):
            raise TeachingPrepConflictError(
                "assessment evidence class does not match class variant"
            )
        request_values = {
            "base_resource_pack_id": base.id,
            "base_resource_pack_sha256": base.pack_sha256,
            "class_name": clean_class,
            "teacher_context": clean_context,
            "assessment_ids": clean_assessment_ids,
            "knowledge_scope": clean_scope,
            "prior_review_ids": review_ids,
        }
        request_hash = _stable_hash(request_values)
        prior_review_payload = [
            {
                "review_id": review.id,
                "package_id": review.package_id,
                "payload": review.payload,
                "created_at": review.created_at,
            }
            for review in reviews
        ]
        derived, _pack_created = self.resource_packs.clone_for_class(
            request_token=f"class-variant-pack:{clean_token}",
            request_hash=request_hash,
            base_pack=base,
            class_name=clean_class,
            teacher_context=clean_context,
            assessment_evidence=dict(assessment_evidence),
            prior_reviews=prior_review_payload,
        )
        if derived.source_state_sha256 != base.source_state_sha256:
            raise TeachingPrepConflictError(
                "class variant did not preserve the frozen teaching sources"
            )
        variant, created = self.teaching_delivery.create_class_variant(
            request_token=clean_token,
            request_hash=request_hash,
            base_resource_pack_id=base.id,
            resource_pack_id=derived.id,
            lesson_node_id=base.lesson_node_id,
            class_name=clean_class,
            prior_review_ids=review_ids,
        )
        return variant, derived, created

    def list_class_variants(
        self,
        lesson_node_id: str,
    ) -> tuple[ClassVariant, ...]:
        return self.teaching_delivery.list_class_variants(
            _clean_entity_id(lesson_node_id)
        )

    def create_up_class_package(
        self,
        pptx_version_id: str,
        *,
        request_token: str,
        confirmed: bool,
    ) -> tuple[UpClassPackage, bool]:
        if not confirmed:
            raise TeachingPrepValidationError(
                "teacher confirmation is required before final packaging"
            )
        clean_token = _clean_token(request_token)
        pptx = self.pptx_executions.get_version(
            _clean_entity_id(pptx_version_id)
        )
        pptx_path, _filename = self.pptx_download(pptx.id)
        plan = self.slide_plans.get(pptx.slide_plan_id)
        draft = self.lesson_drafts.get(plan.lesson_draft_id)
        if draft.status != "confirmed":
            raise TeachingPrepConflictError(
                "up-class package requires a confirmed lesson draft"
            )
        pack = self.resource_packs.get(draft.resource_pack_id)
        classroom = dict(pack.payload.get("classroom") or {})
        class_name = _clean_optional_text(
            classroom.get("class_name"),
            "class_name",
            maximum=120,
        )
        scope_key = _stable_hash(
            {
                "lesson_node_id": pack.lesson_node_id,
                "class_name": (class_name or "").casefold(),
            }
        )
        request_hash = _stable_hash(
            {
                "pptx_version_id": pptx.id,
                "pptx_sha256": pptx.output_sha256,
                "slide_plan_id": plan.id,
                "lesson_draft_id": draft.id,
                "resource_pack_id": pack.id,
                "resource_pack_sha256": pack.pack_sha256,
                "class_scope_key": scope_key,
            }
        )
        package, created = self.teaching_delivery.begin_package(
            request_token=clean_token,
            request_hash=request_hash,
            pptx_version_id=pptx.id,
            slide_plan_id=plan.id,
            lesson_draft_id=draft.id,
            resource_pack_id=pack.id,
            lesson_node_id=pack.lesson_node_id,
            class_name=class_name,
            class_scope_key=scope_key,
        )
        if not created:
            return self._package_with_storage(package), False
        staging = self._package_staging(package.id)
        publication_reserved = False
        try:
            archive, manifest, package_sha = build_up_class_package(
                staging,
                pptx_path=pptx_path,
                pptx_version=pptx,
                plan=plan,
                draft=draft,
                pack=pack,
                resolve_region=self.exercise_region_preview_path,
            )
            filename = (
                f"lesson-{pack.lesson_node_id[:8]}-"
                f"class-package-v{package.version_number:04d}-"
                f"{package.id[:8]}.zip"
            )
            relative = (
                f"exports/up-class-packages/{pack.lesson_node_id}/{filename}"
            )
            publishing = self.teaching_delivery.set_package_publishing(
                package.id,
                output_relpath=relative,
                output_filename=filename,
                package_sha256=package_sha,
                manifest=manifest,
            )
            publication_reserved = True
            output = self._controlled_export(relative)
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists():
                raise TeachingPrepConflictError(
                    "reserved up-class package output already exists"
                )
            os.rename(archive, output)
            if _sha256_file(output) != package_sha:
                raise TeachingPrepConflictError(
                    "published up-class package fingerprint changed"
                )
            package = self.teaching_delivery.finish_package(publishing.id)
            return self._package_with_storage(package), True
        except Exception as exc:
            if publication_reserved:
                self.teaching_delivery.interrupt_package(
                    package.id,
                    _package_error_code(exc),
                )
            else:
                self.teaching_delivery.fail_package(
                    package.id,
                    _package_error_code(exc),
                )
            raise

    def get_up_class_package(self, package_id: str) -> UpClassPackage:
        return self._package_with_storage(
            self.teaching_delivery.get_package(_clean_entity_id(package_id))
        )

    def list_up_class_packages(
        self,
        lesson_node_id: str,
    ) -> tuple[UpClassPackage, ...]:
        return tuple(
            self._package_with_storage(item)
            for item in self.teaching_delivery.list_packages(
                _clean_entity_id(lesson_node_id)
            )
        )

    def up_class_package_download(
        self,
        package_id: str,
    ) -> tuple[Path, str]:
        package = self.teaching_delivery.get_package(
            _clean_entity_id(package_id)
        )
        if (
            package.status != "complete"
            or package.output_filename is None
            or package.package_sha256 is None
            or package.manifest is None
        ):
            raise TeachingPrepNotFoundError(
                "complete up-class package was not found"
            )
        _staging_name, relative = self.teaching_delivery.package_storage(
            package.id
        )
        if relative is None:
            raise TeachingPrepConflictError(
                "up-class package output location is missing"
            )
        output = self._controlled_export(relative)
        if (
            not output.is_file()
            or _sha256_file(output) != package.package_sha256
        ):
            raise TeachingPrepConflictError(
                "up-class package is unavailable or changed"
            )
        verify_package_archive(output, package.manifest)
        return output, package.output_filename

    def recover_up_class_package(
        self,
        package_id: str,
    ) -> UpClassPackage:
        package = self.teaching_delivery.get_package(
            _clean_entity_id(package_id)
        )
        if package.status != "interrupted":
            raise TeachingPrepConflictError(
                "up-class package is not awaiting recovery"
            )
        if (
            package.package_sha256 is None
            or package.manifest is None
        ):
            raise TeachingPrepConflictError(
                "interrupted package has no trusted publication manifest"
            )
        _staging_name, relative = self.teaching_delivery.package_storage(
            package.id
        )
        if relative is None:
            raise TeachingPrepConflictError(
                "interrupted package has no reserved output"
            )
        output = self._controlled_export(relative)
        candidate = self._package_staging(package.id) / "candidate.zip"
        if not output.is_file():
            if (
                not candidate.is_file()
                or _sha256_file(candidate) != package.package_sha256
            ):
                raise TeachingPrepConflictError(
                    "interrupted package has no matching candidate"
                )
            verify_package_archive(candidate, package.manifest)
            output.parent.mkdir(parents=True, exist_ok=True)
            os.rename(candidate, output)
        if _sha256_file(output) != package.package_sha256:
            raise TeachingPrepConflictError(
                "interrupted package fingerprint changed"
            )
        verify_package_archive(output, package.manifest)
        return self._package_with_storage(
            self.teaching_delivery.finish_package(package.id)
        )

    def discard_up_class_package_staging(
        self,
        package_id: str,
    ) -> UpClassPackage:
        package = self.teaching_delivery.get_package(
            _clean_entity_id(package_id)
        )
        if package.status not in {"failed", "interrupted", "complete"}:
            raise TeachingPrepConflictError(
                "active up-class package staging cannot be discarded"
            )
        staging = self._package_staging(package.id)
        if staging.exists():
            shutil.rmtree(staging)
        return self._package_with_storage(
            self.teaching_delivery.get_package(package.id)
        )

    def activate_up_class_package(
        self,
        package_id: str,
        *,
        request_token: str,
        confirmed: bool,
    ) -> tuple[UpClassPackage, bool]:
        if not confirmed:
            raise TeachingPrepValidationError(
                "teacher confirmation is required before changing final version"
            )
        clean_id = _clean_entity_id(package_id)
        clean_token = _clean_token(request_token)
        request_hash = _stable_hash(
            {"package_id": clean_id, "reason": "rollback"}
        )
        package, created = self.teaching_delivery.activate_package(
            clean_id,
            request_token=clean_token,
            request_hash=request_hash,
        )
        return self._package_with_storage(package), created

    def create_post_lesson_review(
        self,
        package_id: str,
        *,
        request_token: str,
        timing: str,
        question_outcome: str,
        reteach_points: Sequence[str],
        next_action: str,
        note: str | None,
        use_in_next_version: bool,
    ) -> tuple[PostLessonReview, bool]:
        package = self.teaching_delivery.get_package(
            _clean_entity_id(package_id)
        )
        if package.status != "complete":
            raise TeachingPrepConflictError(
                "post-lesson review requires a complete up-class package"
            )
        payload = {
            "timing": _clean_choice(
                timing,
                {"on_time", "over", "early"},
                "timing",
            ),
            "question_outcome": _clean_choice(
                question_outcome,
                {
                    "appropriate",
                    "too_hard",
                    "too_easy",
                    "ineffective",
                    "not_observed",
                },
                "question_outcome",
            ),
            "reteach_points": list(
                dict.fromkeys(
                    _clean_text(value, "reteach_point", maximum=300)
                    for value in reteach_points
                )
            ),
            "next_action": _clean_choice(
                next_action,
                {"keep", "delete", "adjust"},
                "next_action",
            ),
            "note": _clean_optional_text(note, "note", maximum=1_000),
        }
        if len(payload["reteach_points"]) > 10:
            raise TeachingPrepValidationError(
                "reteach_points contains too many items"
            )
        request_hash = _stable_hash(
            {
                "package_id": package.id,
                "payload": payload,
                "use_in_next_version": bool(use_in_next_version),
            }
        )
        return self.teaching_delivery.create_review(
            request_token=_clean_token(request_token),
            request_hash=request_hash,
            package_id=package.id,
            pptx_version_id=package.pptx_version_id,
            lesson_node_id=package.lesson_node_id,
            class_name=package.class_name,
            payload=payload,
            use_in_next_version=bool(use_in_next_version),
        )

    def list_post_lesson_reviews(
        self,
        lesson_node_id: str,
    ) -> tuple[PostLessonReview, ...]:
        return self.teaching_delivery.list_reviews(
            _clean_entity_id(lesson_node_id)
        )

    def _package_staging(self, package_id: str) -> Path:
        name, _relative = self.teaching_delivery.package_storage(package_id)
        root = (self.paths["staging"] / "up-class-packages").resolve(
            strict=False
        )
        target = (root / name).resolve(strict=False)
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise RuntimeError("up-class package staging path is invalid") from exc
        return target

    def _controlled_export(self, relative: str) -> Path:
        target = (self.root / relative).resolve(strict=False)
        root = self.paths["exports"].resolve(strict=False)
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise RuntimeError("up-class package output path is invalid") from exc
        return target

    def _package_with_storage(
        self,
        package: UpClassPackage,
    ) -> UpClassPackage:
        retained = self._package_staging(package.id).is_dir()
        actions: tuple[str, ...] = ()
        if package.status == "interrupted":
            actions = (
                ("resume_publish", "discard_staging")
                if package.manifest is not None
                else ("discard_staging",)
            )
        elif package.status in {"failed", "complete"} and retained:
            actions = ("discard_staging",)
        return replace(
            package,
            staging_retained=retained,
            recovery_actions=actions,
        )

    def _execution_staging(self, run_id: str) -> Path:
        name = self.pptx_executions.staging_name(run_id)
        target = (self.paths["staging"] / name).resolve(strict=False)
        root = self.paths["staging"].resolve(strict=False)
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise RuntimeError("execution staging path is invalid") from exc
        return target

    def _controlled_output(self, relative: str) -> Path:
        target = (self.root / relative).resolve(strict=False)
        root = self.paths["outputs"].resolve(strict=False)
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise RuntimeError("PPTX output path is invalid") from exc
        return target

    def _execution_with_storage(
        self,
        run: PptxExecutionRun,
    ) -> PptxExecutionRun:
        retained = self._execution_staging(run.id).is_dir()
        actions: tuple[str, ...] = ()
        if run.status == "interrupted":
            actions = (
                ("resume_publication", "discard_staging")
                if (
                    run.published_version_id is not None
                    and _generation_deadline_is_open(
                        _generation_deadline_from_verification(
                            run.verification_report
                        )
                    )
                )
                else ("discard_staging",)
            )
        elif run.status in {"failed", "cancelled", "published"} and retained:
            actions = ("discard_staging",)
        return replace(
            run,
            staging_retained=retained,
            recovery_actions=actions,
        )

    def mark_interrupted_operations(self) -> int:
        detailed = self.pptx_executions.mark_interrupted()
        delivery = self.teaching_delivery.mark_interrupted_packages()
        workbench = self.workbench.mark_interrupted()
        with self.database.connect(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE teaching_prep_operations
                SET status = 'interrupted',
                    error_code = 'application_restarted',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE status IN ('pending', 'running')
                """
            )
            return max(detailed, delivery, workbench, int(cursor.rowcount))


def _clean_token(value: str) -> str:
    clean = str(value or "").strip()
    if not _TOKEN.fullmatch(clean):
        raise TeachingPrepValidationError("request_token is invalid")
    return clean


def _model_adapter_available(adapter: object | None) -> bool:
    if adapter is None:
        return False
    probe = getattr(adapter, "is_available", None)
    if not callable(probe):
        return True
    try:
        return bool(probe())
    except (OSError, ValueError):
        return False


def _model_error_code(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "model_timeout"
    if isinstance(exc, TeachingPrepValidationError):
        return "model_response_invalid"
    return "model_request_failed"


def _json_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _require_initial_tree_source(snapshot: Mapping[str, object]) -> None:
    lessons = snapshot.get("lessons")
    if isinstance(lessons, list) and lessons:
        return
    materials = snapshot.get("materials")
    if not isinstance(materials, list) or len(materials) != 1:
        raise TeachingPrepValidationError(
            "initial lesson tree requires one textbook or homework workbook"
        )
    material = materials[0]
    role = (
        str(material.get("material_role") or "")
        if isinstance(material, Mapping)
        else ""
    )
    if role not in {"textbook", "homework_workbook"}:
        raise TeachingPrepValidationError(
            "initial lesson tree requires a textbook or homework workbook"
        )


def _execution_error_code(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        if "generation budget" in str(exc):
            return "generation_budget_exceeded"
        return "wps_helper_timeout"
    if isinstance(exc, PermissionError):
        return "source_or_output_locked"
    if isinstance(exc, TeachingPrepConflictError):
        return "execution_state_conflict"
    if isinstance(exc, TeachingPrepValidationError):
        return "verification_failed"
    return "wps_execution_failed"


def _generation_deadline_from_verification(
    verification: Mapping[str, object] | None,
) -> str | None:
    if not isinstance(verification, Mapping):
        return None
    value = verification.get("generation_deadline_at")
    return value if isinstance(value, str) else None


def _generation_deadline_is_open(deadline_at: str | None) -> bool:
    if not deadline_at:
        return False
    try:
        parsed = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        return False
    return datetime.now(UTC) < parsed.astimezone(UTC)


def _require_generation_deadline(deadline_at: str) -> None:
    if not _generation_deadline_is_open(deadline_at):
        raise TimeoutError("lesson generation budget exceeded")


def _restore_candidate_from_output(
    *,
    candidate: Path,
    output: Path | None,
) -> None:
    """Return a failed publication to its isolated staging area when able."""
    if output is None:
        return
    try:
        if output.is_file() and not candidate.exists():
            os.rename(output, candidate)
    except OSError:
        # The database version has already been made unavailable.  A locked
        # file cannot be safely moved here and is never exposed for download.
        return


def _package_error_code(exc: Exception) -> str:
    if isinstance(exc, PermissionError):
        return "package_output_locked"
    if isinstance(exc, TeachingPrepConflictError):
        return "package_state_conflict"
    if isinstance(exc, TeachingPrepValidationError):
        return "package_preflight_failed"
    return "package_build_failed"


def _clean_entity_id(value: str) -> str:
    clean = str(value or "").strip().casefold()
    if not _ENTITY_ID.fullmatch(clean):
        raise TeachingPrepValidationError("preparation ID is invalid")
    return clean


def _clean_revision(value: int) -> int:
    clean = int(value)
    if clean < 1:
        raise TeachingPrepValidationError(
            "expected_revision must be positive"
        )
    return clean


def _clean_text(value: str, field: str, *, maximum: int) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > maximum:
        raise TeachingPrepValidationError(f"{field} is invalid")
    return clean


def _clean_optional_text(
    value: str | None,
    field: str,
    *,
    maximum: int,
) -> str | None:
    if value is None:
        return None
    clean = str(value).strip()
    if not clean:
        return None
    if len(clean) > maximum:
        raise TeachingPrepValidationError(f"{field} is invalid")
    return clean


def _clean_duration(value: int | None) -> int | None:
    if value is None:
        return None
    clean = int(value)
    if clean < 1 or clean > 300:
        raise TeachingPrepValidationError("duration_minutes is invalid")
    return clean


def _clean_unit_count(value: int | None) -> int | None:
    if value is None:
        return None
    clean = int(value)
    if clean < 0:
        raise TeachingPrepValidationError("unit_count is invalid")
    return clean


def _clean_exercise_minutes(value: int | None) -> int | None:
    if value is None:
        return None
    clean = int(value)
    if clean < 1 or clean > 60:
        raise TeachingPrepValidationError("estimated_minutes is invalid")
    return clean


def _clean_exercise_regions(
    values: Sequence[Mapping[str, object]],
) -> tuple[ExerciseRegionDraft, ...]:
    if len(values) > 24:
        raise TeachingPrepValidationError("too many exercise regions")
    result: list[ExerciseRegionDraft] = []
    for value in values:
        if set(value) != {"material_unit_id", "crop"}:
            raise TeachingPrepValidationError(
                "exercise region is invalid"
            )
        crop_value = value["crop"]
        if not isinstance(crop_value, Mapping):
            raise TeachingPrepValidationError(
                "exercise region crop is invalid"
            )
        crop = _clean_crop(crop_value)
        if crop is None:
            raise TeachingPrepValidationError(
                "exercise region crop is required"
            )
        result.append(
            ExerciseRegionDraft(
                material_unit_id=_clean_entity_id(
                    str(value["material_unit_id"])
                ),
                crop=crop,
            )
        )
    return tuple(result)


def _clean_positive_ids(
    values: Sequence[int],
    field: str,
    *,
    maximum: int,
) -> tuple[int, ...]:
    if len(values) > maximum:
        raise TeachingPrepValidationError(f"{field} contains too many items")
    result: list[int] = []
    for value in values:
        try:
            clean = int(value)
        except (TypeError, ValueError) as exc:
            raise TeachingPrepValidationError(
                f"{field} is invalid"
            ) from exc
        if clean <= 0:
            raise TeachingPrepValidationError(f"{field} is invalid")
        if clean not in result:
            result.append(clean)
    return tuple(result)


def _unavailable_evidence(
    kind: str,
    code: str,
    *,
    class_name: str | None = None,
) -> dict[str, object]:
    return {
        "kind": kind,
        "availability": "unavailable",
        "class_name": class_name,
        "coverage": 0.0,
        "items": [],
        "missing": [{"code": code}],
        "source_version": hashlib.sha256(code.encode("utf-8")).hexdigest(),
    }


def _validate_evidence_value(
    value: object,
    *,
    key: str | None = None,
) -> None:
    if key is not None:
        normalized = key.casefold()
        if (
            normalized in _FORBIDDEN_EVIDENCE_KEYS
            or normalized.endswith("_path")
            or normalized.endswith("_image_path")
        ):
            raise TeachingPrepValidationError(
                "evidence contains a forbidden personal or path field"
            )
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            _validate_evidence_value(child, key=str(child_key))
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _validate_evidence_value(child)
        return
    if isinstance(value, str):
        if (
            re.match(r"^[A-Za-z]:[\\/]", value)
            or value.startswith("\\\\")
            or value.casefold().startswith("file:")
        ):
            raise TeachingPrepValidationError(
                "evidence contains an absolute path"
            )
        return
    if value is not None and not isinstance(value, (bool, int, float)):
        raise TeachingPrepValidationError(
            "evidence contains an unsupported value"
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_choice(value: str, allowed: set[str], field: str) -> str:
    clean = str(value or "").strip()
    if clean not in allowed:
        raise TeachingPrepValidationError(f"{field} is invalid")
    return clean


def _stable_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _clean_crop(
    value: Mapping[str, float] | None,
) -> dict[str, float] | None:
    if value is None:
        return None
    if set(value) != {"x0", "y0", "x1", "y1"}:
        raise TeachingPrepValidationError("crop is invalid")
    try:
        crop = {key: float(value[key]) for key in ("x0", "y0", "x1", "y1")}
    except (TypeError, ValueError) as exc:
        raise TeachingPrepValidationError("crop is invalid") from exc
    if (
        any(coordinate < 0 or coordinate > 1 for coordinate in crop.values())
        or crop["x1"] <= crop["x0"]
        or crop["y1"] <= crop["y0"]
    ):
        raise TeachingPrepValidationError("crop is invalid")
    return crop
