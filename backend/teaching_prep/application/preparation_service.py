from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import re
import shutil
import threading
import time
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from collections.abc import Callable, Mapping, Sequence
from uuid import uuid4

from PIL import Image

from backend.teaching_prep.application.ports import (
    AssessmentEvidenceReader,
    LessonModelAdapter,
    QuestionEvidenceReader,
    SlideAnimationModelAdapter,
)
from backend.teaching_prep.application.reference_ppt_collections import (
    ReferencePptInput,
    infer_reference_ppt_collection,
)
from backend.teaching_prep.application.lesson_drafts import (
    build_local_template,
    calculate_capacity,
    default_review_findings,
    draft_preflight,
    normalize_model_draft_payload,
    validate_draft_payload,
)
from backend.teaching_prep.application.preferences import (
    DEFAULT_TEACHING_PREFERENCES,
    normalize_teaching_preferences,
    resolve_teaching_preferences,
)
from backend.teaching_prep.application.slide_animation import (
    accept_slide_animation_run,
    cancel_slide_animation_run,
    discard_slide_animation_run,
    get_slide_animation_run,
    list_slide_animation_runs,
    process_slide_animation_run,
    slide_animation_download_path,
    slide_animation_preview_path,
    start_slide_animation_run,
)
from backend.teaching_prep.application.slide_plans import (
    build_slide_plan_payload,
    diff_preview,
    require_approval_allowed,
    validate_plan_payload,
)
from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepNotFoundError,
    TeachingPrepValidationError,
)
from backend.teaching_prep.domain.models import (
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
    UpClassPackage,
    TeachingPreferences,
    TeachingSemester,
    TeachingPrepAIAdoption,
)
from backend.teaching_prep.domain.states import (
    LessonPreparationState,
    require_transition,
)
from backend.teaching_prep.infrastructure.database import TeachingPrepDatabase
from backend.teaching_prep.infrastructure.materials import (
    PPT_OBJECT_SCHEMA_VERSION,
    PREVIEW_COMPOSITOR_VERSION,
    MaterialParser,
)
from backend.teaching_prep.infrastructure.repositories import (
    LessonDraftRepository,
    LessonPreparationRepository,
    MaterialPreviewRecord,
    MaterialUnitRepository,
    ResourcePackRepository,
    SemesterMappingRepository,
    SemesterWorkspaceRepository,
    SlidePlanRepository,
    PptxLocalOutputRepository,
    TeachingCatalogRepository,
    TeachingPreferencesRepository,
    WorkbenchIterationRepository,
    WorkspaceAIAdoptionRepository,
    SlideAnimationRepository,
    AdaptationTraceRepository,
)
from backend.teaching_prep.application.lesson_execution import (
    execute_confirmed_plan,
    generate_worksheet,
    worksheet_file,
)
from backend.teaching_prep.application.question_selection import (
    QuestionSelectionError,
    SelectionRequest,
    list_volume_sections,
    select_questions,
)
from backend.workspaces.ai_tasks.models import RevisionConflictError


LOGGER = logging.getLogger(__name__)


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
_PPTX_PRERENDER_PASSES = 2
_PPTX_PRERENDER_PAGE_TIMEOUT_SECONDS = 45.0
_PPTX_PRERENDER_POLL_SECONDS = 0.2
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
_LESSON_TYPES = {"new_lesson", "review"}
_LESSON_GENERATION_BUDGET_MS = 300_000
_PREVIEW_PUBLISH_BUDGET_MS = 60_000
_WPS_EXECUTION_TIMEOUT_SECONDS = 170
_SUGGESTION_IMAGE_LIMIT = 16
_SUGGESTION_IMAGE_BYTES = 15_000_000
_MODEL_IMAGE_MAX_EDGE = 1280
_MODEL_IMAGE_COMPRESS_OVER_BYTES = 180_000
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
        slide_animation_model_adapter: SlideAnimationModelAdapter | None = None,
        slide_animation_model_label: str | None = None,
        material_parser: MaterialParser | None = None,
        question_bank_path: str | Path | None = None,
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
        self.material_units = MaterialUnitRepository(self.database)
        self.resource_packs = ResourcePackRepository(self.database)
        self.lesson_drafts = LessonDraftRepository(self.database)
        self.slide_plans = SlidePlanRepository(self.database)
        self.semester_mapping = SemesterMappingRepository(self.database)
        self.workbench = WorkbenchIterationRepository(
            self.database,
            source_change_provider=self.resource_packs.source_change_flags,
        )
        self.workspace_ai_adoptions = WorkspaceAIAdoptionRepository(
            self.database,
            resource_source_status=self.resource_packs.source_status,
            selection_catalog=self.workbench.selection_catalog,
            semester_snapshot=self.semester_mapping.snapshot,
        )
        self.slide_animation_runs = SlideAnimationRepository(self.database)
        self.adaptation_traces = AdaptationTraceRepository(self.database)
        self.pptx_outputs = PptxLocalOutputRepository(self.database)
        self.material_parser = material_parser or MaterialParser()
        self._material_parse_lock = threading.Lock()
        self._active_material_parses: set[str] = set()
        self._pptx_preview_lock = threading.RLock()
        self.question_evidence_reader = question_evidence_reader
        self.assessment_evidence_reader = assessment_evidence_reader
        self.question_bank_path = (
            Path(question_bank_path) if question_bank_path else None
        )
        self.lesson_model_adapter = lesson_model_adapter
        self.slide_animation_model_adapter = slide_animation_model_adapter
        self.lesson_model_label = (
            _clean_optional_text(
                lesson_model_label,
                "lesson_model_label",
                maximum=120,
            )
            if lesson_model_label is not None
            else None
        )
        self.slide_animation_model_label = (
            _clean_optional_text(
                slide_animation_model_label,
                "slide_animation_model_label",
                maximum=120,
            )
            if slide_animation_model_label is not None
            else None
        )
        self.mark_interrupted_operations()

    def status(self) -> dict[str, object]:
        return {
            "module": "teaching-prep",
            "enabled": True,
            "schema_version": "023_pptx_local_outputs",
            "real_model_enabled": _model_adapter_available(
                self.lesson_model_adapter
            ),
            "slide_animation_model_available": (
                _model_adapter_available(
                    self.slide_animation_model_adapter
                )
            ),
            "local_execution_enabled": True,
        }

    def list_lesson_preparation_statuses(
        self,
        semester_id: str,
        *,
        ai_tasks: Sequence[object] = (),
    ) -> tuple[dict[str, object], ...]:
        return self.workbench.lesson_preparation_statuses(
            _clean_entity_id(semester_id),
            ai_tasks=ai_tasks,
        )

    def mark_interrupted_operations(self) -> int:
        material_deletions = self._recover_material_deletion_files()
        animations = self.slide_animation_runs.mark_interrupted()
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
            return max(
                material_deletions,
                animations,
                workbench,
                int(cursor.rowcount),
            )

    def adopt_workspace_ai_result(
        self,
        *,
        source_task_id: str,
        handoff_id: str,
        adoption_id: str,
        proposal_ref_id: str,
        draft_revision: str,
        target_revision: str,
        command: Mapping[str, object] | None = None,
    ) -> TeachingPrepAIAdoption:
        try:
            if command is not None:
                staged = self.workspace_ai_adoptions.stage_command(
                    source_task_id=source_task_id,
                    handoff_id=handoff_id,
                    adoption_id=adoption_id,
                    proposal_ref_id=proposal_ref_id,
                    draft_revision=draft_revision,
                    target_revision=target_revision,
                    command=command,
                )
            else:
                staged = self.workspace_ai_adoptions.load_staged_command(
                    source_task_id=source_task_id,
                    handoff_id=handoff_id,
                    adoption_id=adoption_id,
                    proposal_ref_id=proposal_ref_id,
                    draft_revision=draft_revision,
                    target_revision=target_revision,
                )
                if staged is None:
                    raise RevisionConflictError(
                        "a teacher adoption command is required"
                    )
            formal_object_id = self._execute_workspace_ai_adoption_command(
                adoption_id=adoption_id,
                task_kind=str(staged["task_kind"]),
                proposal_ref_id=str(staged["proposal_ref_id"]),
                command=dict(staged["command"]),
            )
        except (
            TeachingPrepConflictError,
            TeachingPrepNotFoundError,
            TeachingPrepValidationError,
        ) as exc:
            # These domain failures are known to happen before a formal object
            # is committed.  Release any staged command so the common handoff
            # can reopen and accept corrected/current teacher input.
            self.workspace_ai_adoptions.clear_staged_command(
                source_task_id=source_task_id,
                handoff_id=handoff_id,
                adoption_id=adoption_id,
            )
            raise RevisionConflictError(str(exc)) from exc
        return self.workspace_ai_adoptions.complete_command(
            source_task_id=source_task_id,
            handoff_id=handoff_id,
            adoption_id=adoption_id,
            draft_revision=draft_revision,
            target_revision=target_revision,
            formal_object_id=formal_object_id,
        )

    def _execute_workspace_ai_adoption_command(
        self,
        *,
        adoption_id: str,
        task_kind: str,
        proposal_ref_id: str,
        command: Mapping[str, object],
    ) -> str:
        command_kind = str(command.get("kind") or "")
        request_token = f"ai-adopt-{adoption_id}"
        if (
            task_kind == "teaching_prep.semester_mapping"
            and command_kind == "apply_semester_mapping"
        ):
            applied = self.apply_semester_mapping_proposal(
                proposal_ref_id,
                expected_revision=int(command["proposal_revision"]),
            )
            return applied.id
        if (
            task_kind == "teaching_prep.lesson_plan"
            and command_kind == "confirm_lesson_draft"
        ):
            revised, _created = self.revise_lesson_draft(
                proposal_ref_id,
                request_token=request_token,
                payload=dict(command["payload"]),
                confirmed=True,
            )
            return revised.id
        if (
            task_kind == "teaching_prep.exercise_suggestions"
            and command_kind == "finalize_exercise_suggestions"
        ):
            run, suggestions = self.get_exercise_suggestion_run(
                proposal_ref_id
            )
            if run.status != "succeeded" or not suggestions:
                raise TeachingPrepConflictError(
                    "exercise suggestions are unavailable for confirmation"
                )
            if any(item.decision == "pending" for item in suggestions):
                raise TeachingPrepConflictError(
                    "decide every exercise suggestion before confirmation"
                )
            return proposal_ref_id
        if (
            task_kind == "teaching_prep.slide_change_proposal"
            and command_kind == "review_slide_plan"
        ):
            raw_reviews = command.get("operation_reviews")
            if not isinstance(raw_reviews, list):
                raise TeachingPrepValidationError(
                    "slide plan reviews are invalid"
                )
            revised, _created = self.revise_slide_plan(
                proposal_ref_id,
                request_token=request_token,
                operation_reviews=[
                    dict(review) for review in raw_reviews
                    if isinstance(review, Mapping)
                ],
                approve_low_risk_deletions=bool(
                    command.get("approve_low_risk_deletions", False)
                ),
                review_note=(
                    str(command["review_note"])
                    if command.get("review_note") is not None
                    else None
                ),
            )
            return revised.id
        raise TeachingPrepValidationError(
            "teacher adoption command does not match the AI proposal"
        )

    def find_workspace_ai_adoption(
        self,
        adoption_id: str,
    ) -> TeachingPrepAIAdoption | None:
        return self.workspace_ai_adoptions.find(adoption_id)


    def _exercise_reference_images(
        self,
        model_input: Mapping[str, object],
    ) -> list[dict[str, object]]:
        return self._material_page_images(
            model_input,
            purposes=("exercise", "textbook"),
            max_images=_SUGGESTION_IMAGE_LIMIT,
            max_bytes=_SUGGESTION_IMAGE_BYTES,
        )

    def load_frozen_page_for_model(
        self,
        resource_pack: Mapping[str, object],
        source_ref: str,
    ) -> dict[str, object]:
        from backend.teaching_prep.infrastructure.llm.lesson_model import (
            page_catalog_entry,
            page_label,
        )

        entry = page_catalog_entry(resource_pack, source_ref)
        if entry is None:
            return {
                "ok": False,
                "error": "source_ref is not in the frozen pack",
                "source_ref": str(source_ref or ""),
            }
        unit_id = str(entry.get("unit_id") or "")
        if not unit_id:
            return {
                "ok": False,
                "error": "page unit is unavailable",
                "source_ref": str(source_ref or ""),
            }
        preview = self._preview_file_for_model(
            unit_id,
            regenerate_missing=True,
        )
        if preview is None:
            return {
                "ok": False,
                "error": "page preview is unavailable",
                "source_ref": str(source_ref or ""),
            }
        mime_type, content = _preview_payload_for_model(preview)
        purpose = entry.get("purpose")
        unit_index = entry.get("unit_index")
        label = page_label(purpose, unit_index)
        return {
            "ok": True,
            "source_ref": str(entry.get("source_ref") or source_ref),
            "purpose": purpose,
            "unit_index": unit_index,
            "unit_id": unit_id,
            "label": label,
            "mime_type": mime_type,
            "content": content,
            "preview_url": (
                f"/api/teaching-prep/material-units/{unit_id}/preview"
            ),
        }

    def get_adaptation_trace(
        self,
        lesson_node_id: str,
        *,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        clean_lesson = _clean_entity_id(lesson_node_id)
        if operation_id:
            return self.adaptation_traces.list_for_operation(
                lesson_node_id=clean_lesson,
                operation_id=_clean_token(operation_id),
            )
        return self.adaptation_traces.latest_for_lesson(clean_lesson)

    def _adaptation_observer(
        self,
        *,
        lesson_node_id: str,
        operation_id: str,
    ):
        def emit(event: Mapping[str, object]) -> None:
            try:
                self.adaptation_traces.append_event(
                    lesson_node_id=lesson_node_id,
                    operation_id=operation_id,
                    event=event,
                )
            except Exception:
                LOGGER.exception("adaptation trace could not be recorded")

        return emit

    def _validation_failure_trace_event(
        self,
        *,
        lesson_node_id: str,
        operation_id: str,
        reason: str,
    ) -> dict[str, object]:
        last_event: Mapping[str, object] = {}
        try:
            trace = self.adaptation_traces.list_for_operation(
                lesson_node_id=lesson_node_id,
                operation_id=operation_id,
            )
            events = trace.get("events")
            if isinstance(events, list) and events:
                candidate = events[-1]
                if isinstance(candidate, Mapping):
                    last_event = candidate
        except Exception:
            LOGGER.exception("adaptation trace could not be read")
        round_number = last_event.get("round")
        used = last_event.get("model_calls_used")
        maximum = last_event.get("model_calls_max")
        return {
            "round": round_number if isinstance(round_number, int) else 1,
            "phase": "failed",
            "summary": f"本次没有完成：{reason}",
            "thinking_excerpt": None,
            "tool": None,
            "result": None,
            "model_calls_used": used if isinstance(used, int) else 0,
            "model_calls_max": maximum if isinstance(maximum, int) else 6,
        }

    def _material_page_images(
        self,
        model_input: Mapping[str, object],
        *,
        purposes: Sequence[str],
        max_images: int,
        max_bytes: int,
    ) -> list[dict[str, object]]:
        buckets: dict[str, list[dict[str, object]]] = {
            purpose: [] for purpose in purposes
        }
        materials = model_input.get("materials")
        if not isinstance(materials, list):
            return []
        for material in materials:
            if not isinstance(material, Mapping):
                continue
            purpose = str(material.get("purpose") or "")
            if purpose not in buckets:
                continue
            units = material.get("units")
            if not isinstance(units, list):
                continue
            for unit in units:
                if not isinstance(unit, Mapping):
                    continue
                unit_id = str(unit.get("unit_id") or "")
                if not unit_id:
                    continue
                preview = self._preview_file_for_model(
                    unit_id,
                    regenerate_missing=purpose != "reference_ppt",
                )
                if preview is None:
                    continue
                mime_type, content = _preview_payload_for_model(preview)
                buckets[purpose].append(
                    {
                        "purpose": purpose,
                        "material_version_id": str(
                            material.get("material_version_id") or ""
                        ),
                        "material_name": str(material.get("material_name") or ""),
                        "material_unit_id": unit_id,
                        "unit_index": int(unit.get("unit_index") or 0),
                        "mime_type": mime_type,
                        "content": content,
                    }
                )
        return _select_model_page_images(
            buckets,
            purposes=purposes,
            max_images=max_images,
            max_bytes=max_bytes,
        )

    def _preview_file_for_model(
        self,
        unit_id: str,
        *,
        regenerate_missing: bool,
    ) -> Path | None:
        try:
            if regenerate_missing:
                return self.material_preview_path(unit_id)
            record = self.material_units.preview_record(
                _clean_entity_id(unit_id)
            )
            target = (self.root / record.preview_relpath).resolve(strict=False)
            preview_root = self.paths["previews"].resolve(strict=False)
            target.relative_to(preview_root)
            return target if target.is_file() else None
        except Exception:
            return None


    def start_slide_animation_run(
        self,
        lesson_node_id: str,
        *,
        operation_id: str,
        confirmed: bool,
        material_link_id: str,
        page_indexes: Sequence[int],
    ):
        return start_slide_animation_run(
            self,
            lesson_node_id,
            operation_id=operation_id,
            confirmed=confirmed,
            material_link_id=material_link_id,
            page_indexes=page_indexes,
        )

    def process_slide_animation_run(
        self,
        run_id: str,
        *,
        task_model_gateway: object | None = None,
    ) -> None:
        process_slide_animation_run(
            self,
            run_id,
            task_model_gateway=task_model_gateway,
        )

    def list_slide_animation_runs(self, lesson_node_id: str):
        return list_slide_animation_runs(self, lesson_node_id)

    def get_slide_animation_run(self, run_id: str):
        return get_slide_animation_run(self, run_id)

    def cancel_slide_animation_run(self, run_id: str):
        return cancel_slide_animation_run(self, run_id)

    def accept_slide_animation_run(
        self,
        run_id: str,
        *,
        expected_revision: int,
    ):
        return accept_slide_animation_run(
            self,
            run_id,
            expected_revision=expected_revision,
        )

    def discard_slide_animation_run(
        self,
        run_id: str,
        *,
        expected_revision: int,
    ):
        return discard_slide_animation_run(
            self,
            run_id,
            expected_revision=expected_revision,
        )

    def slide_animation_preview_path(self, run_id: str) -> Path:
        return slide_animation_preview_path(self, run_id)

    def slide_animation_download_path(self, run_id: str) -> Path:
        return slide_animation_download_path(self, run_id)


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
        workbook_series: str | None = None,
        workbook_volume: str | None = None,
    ) -> tuple[SemesterMaterialRecord, bool]:
        clean_role = str(material_role or "").strip()
        if clean_role not in _SEMESTER_MATERIAL_ROLES:
            raise TeachingPrepValidationError("material role is invalid")
        return self.semesters.attach_material(
            _clean_entity_id(semester_id),
            request_token=_clean_token(request_token),
            material_version_id=_clean_entity_id(material_version_id),
            material_role=clean_role,
            workbook_series=_clean_optional_text(workbook_series, "workbook_series", maximum=120),
            workbook_volume=_clean_workbook_volume(workbook_volume),
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
        workbook_series: str | None = None,
        workbook_volume: str | None = None,
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
            workbook_series=_clean_optional_text(workbook_series, "workbook_series", maximum=120),
            workbook_volume=_clean_workbook_volume(workbook_volume),
        )


    def _directory_page_images(
        self,
        snapshot: Mapping[str, object],
        *,
        directory_evidence: Mapping[str, object],
    ) -> list[dict[str, object]]:
        source_units = {
            int(item.get("source_unit") or 0)
            for item in directory_evidence.get("toc_entries", [])
            if isinstance(item, Mapping)
        }
        source_units.update(
            int(item)
            for item in directory_evidence.get(
                "directory_page_unit_indices",
                [],
            )
            if isinstance(item, int) and item > 0
        )
        source_units.discard(0)
        if not source_units:
            return []
        materials = snapshot.get("materials")
        if not isinstance(materials, list) or len(materials) != 1:
            return []
        material = materials[0]
        if not isinstance(material, Mapping):
            return []
        version_id = str(material.get("current_version_id") or "").strip()
        if not version_id:
            return []
        by_index = {
            int(item.unit_index): item.id
            for item in self.material_units.list_units(version_id)
        }
        result: list[dict[str, object]] = []
        total_bytes = 0
        for unit_index in sorted(source_units)[:6]:
            unit_id = str(by_index.get(unit_index) or "").strip()
            if not unit_id:
                continue
            preview = self.material_preview_path(unit_id)
            content = preview.read_bytes()
            total_bytes += len(content)
            if total_bytes > 15_000_000:
                break
            mime_type = (
                "image/jpeg"
                if preview.suffix.lower() in {".jpg", ".jpeg"}
                else "image/webp"
                if preview.suffix.lower() == ".webp"
                else "image/png"
            )
            result.append(
                {
                    "unit_index": unit_index,
                    "mime_type": mime_type,
                    "content": content,
                }
            )
        return result


    def create_reference_ppt_collection(
        self,
        semester_id: str,
        *,
        request_token: str,
        display_name: str,
        ignored_file_count: int,
        members: Sequence[Mapping[str, object]],
    ):
        clean_semester_id = _clean_entity_id(semester_id)
        if not 1 <= len(members) <= 500:
            raise TeachingPrepValidationError(
                "reference PPT collection requires 1 to 500 PPTX files"
            )
        clean_members: list[tuple[str, str]] = []
        for member in members:
            clean_members.append(
                (
                    _clean_entity_id(str(member.get("material_record_id") or "")),
                    _clean_text(
                        str(member.get("relative_path") or ""),
                        "relative_path",
                        maximum=600,
                    ),
                )
            )
        material_ids = tuple(record_id for record_id, _path in clean_members)
        if len(set(material_ids)) != len(material_ids):
            raise TeachingPrepValidationError(
                "reference PPT collection contains duplicate materials"
            )
        snapshot, _digest_value = self.semester_mapping.snapshot(
            clean_semester_id,
            material_ids,
        )
        material_by_id = {
            str(item["record_id"]): item for item in snapshot["materials"]
        }
        inputs: list[ReferencePptInput] = []
        for record_id, relative_path in clean_members:
            material = material_by_id[record_id]
            units = list(material.get("units") or [])
            first_unit = dict(units[0]) if units else {}
            first_title = str(first_unit.get("title") or "").strip() or None
            if first_title is None:
                first_lines = str(
                    first_unit.get("text_excerpt") or ""
                ).splitlines()
                first_title = (
                    first_lines[0].strip() or None
                    if first_lines
                    else None
                )
            inputs.append(
                ReferencePptInput(
                    material_record_id=record_id,
                    relative_path=relative_path,
                    file_name=Path(relative_path).name,
                    unit_count=int(material["unit_count"]),
                    first_slide_title=first_title,
                )
            )
        proposal_payload = infer_reference_ppt_collection(
            inputs,
            existing_lessons=list(snapshot["lessons"]),
        )
        return self.semester_mapping.create_local_reference_ppt_collection(
            semester_id=clean_semester_id,
            request_token=_clean_token(request_token),
            display_name=_clean_text(
                display_name,
                "display_name",
                maximum=160,
            ),
            ignored_file_count=_clean_nonnegative_int(
                ignored_file_count,
                "ignored_file_count",
                maximum=10_000,
            ),
            material_record_ids=material_ids,
            payload=proposal_payload,
        )

    def list_reference_ppt_collections(
        self,
        semester_id: str,
        *,
        include_inactive: bool = False,
    ):
        return self.semester_mapping.list_reference_ppt_collections(
            _clean_entity_id(semester_id),
            include_inactive=bool(include_inactive),
        )

    def update_reference_ppt_collection(
        self,
        collection_id: str,
        *,
        is_active: bool,
    ):
        return self.semester_mapping.update_reference_ppt_collection(
            _clean_entity_id(collection_id),
            is_active=bool(is_active),
        )

    def accept_local_reference_ppt_mappings(
        self,
        proposal_id: str,
        *,
        expected_revision: int,
    ) -> SemesterMappingProposal:
        return self.semester_mapping.accept_local_high_confidence(
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
        include_archived: bool = False,
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
            include_archived=bool(include_archived),
        )

    def update_material_source(
        self,
        source_id: str,
        *,
        expected_revision: int,
        display_name: str | None,
        archived: bool | None,
    ) -> MaterialVersion:
        clean_name = _clean_optional_text(display_name, "display_name", maximum=120)
        return self.catalog.update_material_source(
            _clean_entity_id(source_id),
            expected_revision=_clean_revision(expected_revision),
            display_name=clean_name,
            archived=archived,
        )

    def delete_material_source(
        self,
        source_id: str,
        *,
        expected_revision: int,
        operation_id: str,
        preview_version: str,
        confirmation_phrase: str,
    ) -> dict[str, object]:
        clean_id = _clean_entity_id(source_id)
        revision = _clean_revision(expected_revision)
        clean_operation_id = _clean_token(operation_id)
        clean_preview_version = str(preview_version or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", clean_preview_version) is None:
            raise TeachingPrepValidationError(
                "material deletion preview version is invalid"
            )
        if confirmation_phrase != "确认彻底删除资料":
            raise TeachingPrepValidationError(
                "material deletion confirmation is required"
            )
        request_hash = _stable_hash(
            {
                "source_id": clean_id,
                "expected_revision": revision,
                "preview_version": clean_preview_version,
                "confirmation_phrase": confirmation_phrase,
            }
        )
        # Parsing and deletion change the same controlled files.  Keep a
        # delete from starting while a parser owns any material, and keep a
        # parser from starting until the delete has completed.
        with self._material_parse_lock:
            if self._active_material_parses:
                raise TeachingPrepConflictError(
                    "material parsing is already in progress"
                )
            replay = self.catalog.find_material_deletion(
                clean_operation_id,
                request_hash=request_hash,
            )
            if replay is not None:
                if (
                    replay.get("status") == "interrupted"
                    and self.catalog.claim_recovered_material_deletion(
                        clean_operation_id,
                        request_hash=request_hash,
                        source_id=clean_id,
                        expected_revision=revision,
                        preview_version=clean_preview_version,
                    )
                ):
                    return self._delete_material_source(
                        clean_id,
                        expected_revision=revision,
                        operation_id=clean_operation_id,
                        preview_version=clean_preview_version,
                        request_hash=request_hash,
                        operation_already_started=True,
                    )
                return (
                    self.catalog.find_material_deletion(
                        clean_operation_id,
                        request_hash=request_hash,
                    )
                    or replay
                )
            return self._delete_material_source(
                clean_id,
                expected_revision=revision,
                operation_id=clean_operation_id,
                preview_version=clean_preview_version,
                request_hash=request_hash,
            )

    def preview_material_deletion(
        self,
        source_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, object]:
        impact = self.catalog.material_deletion_impact(
            _clean_entity_id(source_id),
            expected_revision=_clean_revision(expected_revision),
        )
        safe_paths, _execution_staging_dirs = (
            self._material_deletion_file_targets(impact)
        )
        impact["owned_file_count"] = sum(
            1 for path in safe_paths if path.is_file()
        )
        return impact

    def get_material_deletion(self, operation_id: str) -> dict[str, object]:
        return self.catalog.get_material_deletion(_clean_token(operation_id))

    def _delete_material_source(
        self,
        source_id: str,
        *,
        expected_revision: int,
        operation_id: str,
        preview_version: str,
        request_hash: str,
        operation_already_started: bool = False,
    ) -> dict[str, object]:
        self.close_preview_runtime()
        impact = self.catalog.material_deletion_impact(
            source_id,
            expected_revision=expected_revision,
        )
        if impact["preview_version"] != preview_version:
            raise TeachingPrepConflictError(
                "material deletion impact changed; preview it again"
            )
        if impact["can_delete"] is not True:
            raise TeachingPrepConflictError(
                "material has active courseware generation and cannot be deleted"
            )
        safe_paths, execution_staging_dirs = (
            self._material_deletion_file_targets(impact)
        )
        public_impact = dict(impact)
        existing_paths = tuple(path for path in safe_paths if path.is_file())
        public_impact["owned_file_count"] = len(existing_paths)
        if not operation_already_started:
            replay = self.catalog.begin_material_deletion(
                operation_id=operation_id,
                source_id=source_id,
                source_display_name=str(public_impact["display_name"]),
                expected_revision=expected_revision,
                preview_version=preview_version,
                request_hash=request_hash,
                impact=public_impact,
            )
            if replay is not None:
                return replay
        # Keep the temporary names deliberately short.  The application is
        # commonly installed below a long Chinese workspace path and Windows
        # can otherwise reject an otherwise valid move at its path limit.
        staging = self.paths["staging"] / (
            "md-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:16]
        )
        manifest = [
            {
                "source_relpath": source.relative_to(self.root).as_posix(),
                "staged_name": f"f{index:04d}",
            }
            for index, source in enumerate(existing_paths)
        ]
        self.catalog.save_material_deletion_staging_manifest(
            operation_id,
            manifest=manifest,
        )
        staged: list[tuple[Path, Path]] = []
        try:
            for source, entry in zip(existing_paths, manifest, strict=True):
                staging.mkdir(parents=True, exist_ok=True)
                target = staging / entry["staged_name"]
                os.replace(source, target)
                staged.append((source, target))
            result = self.catalog.delete_material_source(
                source_id,
                expected_revision=expected_revision,
                expected_preview_version=preview_version,
                operation_id=operation_id,
                deleted_file_count=len(staged),
            )
        except Exception:
            for source, target in reversed(staged):
                if target.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(target, source)
            shutil.rmtree(staging, ignore_errors=True)
            self.catalog.fail_material_deletion(
                operation_id,
                error_code="material_delete_failed",
            )
            raise
        shutil.rmtree(staging, ignore_errors=True)
        for execution_staging in execution_staging_dirs:
            self._remove_empty_directory_tree(execution_staging)
        if not staging.exists():
            self.catalog.clear_material_deletion_staging_manifest(operation_id)
        return result

    def _material_deletion_file_targets(
        self,
        impact: dict[str, object],
    ) -> tuple[list[Path], list[Path]]:
        owned_paths = tuple(
            Path(str(item)) for item in impact.pop("_owned_paths", [])
        )
        safe_paths = self._controlled_material_paths(owned_paths)
        raw_staging_names = impact.pop(
            "_terminal_generation_staging_names", []
        )
        if not isinstance(raw_staging_names, list):
            raise TeachingPrepConflictError(
                "material generation history is invalid"
            )
        execution_staging_dirs: list[Path] = []
        staging_root = self.paths["staging"].resolve(strict=False)
        for raw_staging_name in raw_staging_names:
            staging_name = str(raw_staging_name)
            if re.fullmatch(r"[0-9a-f]{32}", staging_name) is None:
                raise TeachingPrepConflictError(
                    "material generation staging identity is invalid"
                )
            staging = (staging_root / staging_name).resolve(strict=False)
            try:
                staging.relative_to(staging_root)
            except ValueError as exc:
                raise TeachingPrepConflictError(
                    "material generation staging path is invalid"
                ) from exc
            execution_staging_dirs.append(staging)
            if not staging.is_dir():
                continue
            for candidate in staging.rglob("*"):
                if not candidate.is_file():
                    continue
                resolved = candidate.resolve(strict=False)
                try:
                    resolved.relative_to(staging)
                except ValueError as exc:
                    raise TeachingPrepConflictError(
                        "PPTX execution staging contains an unsafe path"
                    ) from exc
                safe_paths.append(resolved)
        return list(dict.fromkeys(safe_paths)), list(
            dict.fromkeys(execution_staging_dirs)
        )


    @staticmethod
    def _remove_empty_directory_tree(root: Path) -> None:
        if not root.is_dir():
            return
        directories = sorted(
            (path for path in root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for directory in (*directories, root):
            try:
                directory.rmdir()
            except OSError:
                continue

    def _controlled_material_paths(
        self,
        owned_paths: Sequence[Path],
    ) -> list[Path]:
        controlled_roots = (
            self.paths["materials"].resolve(strict=False),
            self.paths["previews"].resolve(strict=False),
        )
        safe_paths: list[Path] = []
        for path in owned_paths:
            candidate = path if path.is_absolute() else self.root / path
            candidate = candidate.resolve(strict=False)
            if any(candidate.is_relative_to(root) for root in controlled_roots):
                safe_paths.append(candidate)
        return list(dict.fromkeys(safe_paths))

    def get_material_version(self, version_id: str) -> MaterialVersion:
        return self.catalog.get_material_version(_clean_entity_id(version_id))

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
        *,
        progress_callback: Callable[[str, int, int], None] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> tuple[MaterialUnit, ...]:
        clean_id = _clean_entity_id(version_id)
        version = self.catalog.get_material_version(clean_id)
        with self._material_parse_lock:
            if clean_id in self._active_material_parses:
                raise TeachingPrepConflictError(
                    "material parsing is already in progress"
                )
            self._active_material_parses.add(clean_id)
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
            total_units = self.material_parser.unit_count(
                source_path,
                material_type=version.material_type,
            )
            if total_units <= 0:
                raise TeachingPrepValidationError(
                    "material contains no previewable units"
                )
            self.material_units.set_parse_expected_count(
                clean_id,
                source_version_sha256=version.content_sha256,
                unit_count=total_units,
            )
            existing = self.material_units.list_units(clean_id)
            reusable_indexes: set[int] = set()
            for item in existing:
                try:
                    record = self.material_units.preview_record(item.id)
                except Exception:
                    continue
                target = (self.root / record.preview_relpath).resolve(strict=False)
                ppt_metadata_is_current = (
                    version.material_type != "pptx"
                    or (
                        "objects" in item.object_summary
                        and item.object_summary.get("object_schema_version")
                        == PPT_OBJECT_SCHEMA_VERSION
                    )
                )
                if (
                    record.source_version_sha256 == version.content_sha256
                    and target.is_file()
                    and 1 <= item.unit_index <= total_units
                    and ppt_metadata_is_current
                ):
                    reusable_indexes.add(item.unit_index)
            completed_previews = len(reusable_indexes)
            if progress_callback is not None:
                progress_callback(
                    "preview",
                    completed_previews,
                    total_units,
                )
            missing_indexes = (
                set(range(1, total_units + 1)) - reusable_indexes
            )
            for unit in self.material_parser.iter_preview_units(
                source_path,
                material_type=version.material_type,
                unit_indexes=missing_indexes,
            ):
                if cancel_check is not None:
                    cancel_check()
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
                self.material_units.save_partial_unit(
                    clean_id,
                    source_version_sha256=version.content_sha256,
                    unit=unit,
                    preview_relpath=relative.as_posix(),
                    preview_sha256=hashlib.sha256(unit.preview_png).hexdigest(),
                )
                completed_previews += 1
                if progress_callback is not None:
                    progress_callback(
                        "preview",
                        completed_previews,
                        total_units,
                    )
            self.material_units.publish_preview_count(
                clean_id,
                source_version_sha256=version.content_sha256,
                unit_count=total_units,
            )
            units = self.material_units.list_units(clean_id)
            ocr_candidates = [
                item
                for item in units
                if (
                    item.unit_kind == "pdf_page"
                    and item.text_status != "manual"
                    and not item.text_excerpt.strip()
                    and bool(item.object_summary.get("has_page_images"))
                    and item.object_summary.get("ocr_status") != "completed"
                )
            ]
            ocr_total = len(ocr_candidates)
            if progress_callback is not None:
                progress_callback("ocr", 0, ocr_total)
            ocr_engine: object | None = None
            if ocr_candidates:
                try:
                    ocr_engine = self.material_parser.create_ocr_engine()
                except Exception:
                    ocr_engine = None
            for completed_ocr, item in enumerate(ocr_candidates, start=1):
                if cancel_check is not None:
                    cancel_check()
                if ocr_engine is not None:
                    record = self.material_units.preview_record(item.id)
                    preview = self.root / record.preview_relpath
                    parsed_text = self.material_parser.ocr_preview(
                        preview.read_bytes(),
                        ocr_engine,
                    )
                    if (
                        parsed_text.layout_items
                        and version.material_type == "pdf"
                    ):
                        high_resolution = self.material_parser.ocr_pdf_page(
                            source_path,
                            unit_index=item.unit_index,
                            ocr_engine=ocr_engine,
                        )
                        if high_resolution.layout_items:
                            parsed_text = high_resolution
                    self.material_units.update_local_ocr(
                        item.id,
                        source_version_sha256=version.content_sha256,
                        extracted_text=parsed_text.extracted_text,
                        formula_review_required=(
                            parsed_text.formula_review_required
                        ),
                        printed_page_number=(
                            parsed_text.printed_page_number
                        ),
                        layout_items=parsed_text.layout_items,
                    )
                if progress_callback is not None:
                    progress_callback("ocr", completed_ocr, ocr_total)
            if cancel_check is not None:
                cancel_check()
            if progress_callback is not None:
                progress_callback("publishing", total_units, total_units)
            units = self.material_units.complete_parse(
                clean_id,
                source_version_sha256=version.content_sha256,
                unit_count=total_units,
            )
            self.semesters.mark_version_parsed(clean_id)
            if version.material_type == "pptx":
                try:
                    self._schedule_pptx_prerender(clean_id)
                except Exception:
                    LOGGER.exception(
                        "PPT prerender scheduling failed for %s", clean_id
                    )
            return units
        except Exception:
            self.semesters.mark_version_parse_failed(clean_id)
            raise
        finally:
            with self._material_parse_lock:
                self._active_material_parses.discard(clean_id)

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
        unit = self.material_units.get_unit(clean_id)
        if unit.unit_kind == "ppt_slide":
            summary = unit.object_summary
            kind = str(summary.get("preview_kind") or "")
            compositor = int(summary.get("preview_compositor") or 0)
            adapter = self._pptx_preview_adapter()
            replace_cached_wps = adapter is None and kind == "rendered"
            stale_compositor = (
                kind == "structural"
                and compositor < PREVIEW_COMPOSITOR_VERSION
            )
            corrupt_rendered = (
                kind == "rendered"
                and _sha256_file(target) != record.preview_sha256
            )
            if replace_cached_wps or stale_compositor or corrupt_rendered:
                with self._material_parse_lock, self._pptx_preview_lock:
                    record = self.material_units.preview_record(clean_id)
                    target = (self.root / record.preview_relpath).resolve(
                        strict=False
                    )
                    unit = self.material_units.get_unit(clean_id)
                    summary = unit.object_summary
                    kind = str(summary.get("preview_kind") or "")
                    compositor = int(summary.get("preview_compositor") or 0)
                    adapter = self._pptx_preview_adapter()
                    still_replace = adapter is None and kind == "rendered"
                    still_stale = (
                        kind == "structural"
                        and compositor < PREVIEW_COMPOSITOR_VERSION
                    )
                    still_corrupt = (
                        kind == "rendered"
                        and (
                            not target.is_file()
                            or _sha256_file(target) != record.preview_sha256
                        )
                    )
                    if still_replace or still_stale or still_corrupt:
                        try:
                            self._restore_structural_pptx_preview(
                                record=record,
                                unit=unit,
                            )
                        except Exception:
                            if still_replace or still_stale:
                                pass
                            else:
                                raise
                    record = self.material_units.preview_record(clean_id)
                    target = (self.root / record.preview_relpath).resolve(
                        strict=False
                    )
                    unit = self.material_units.get_unit(clean_id)
        return target

    def get_material_unit(self, unit_id: str) -> MaterialUnit:
        return self.material_units.get_unit(_clean_entity_id(unit_id))

    def request_pptx_preview_render(self, unit_id: str) -> MaterialUnit:
        clean_id = _clean_entity_id(unit_id)
        unit = self.material_units.get_unit(clean_id)
        if unit.unit_kind != "ppt_slide":
            raise TeachingPrepValidationError(
                "only PPT slides can request rendered previews"
            )
        if unit.object_summary.get("preview_kind") == "rendered":
            return unit
        if self._pptx_preview_adapter() is None:
            return unit
        if not self._should_render_pptx_preview(unit.object_summary):
            return unit
        record = self.material_units.preview_record(clean_id)
        self.material_units.mark_preview_render_progress(
            clean_id,
            source_version_sha256=record.source_version_sha256,
            status="queued",
        )
        with self._pptx_preview_worker_lock:
            self._pptx_preview_wanted = clean_id
            worker = self._pptx_preview_worker
            if worker is None or not worker.is_alive():
                self._pptx_preview_worker = threading.Thread(
                    target=self._pptx_preview_worker_loop,
                    name="pptx-preview-render",
                    daemon=True,
                )
                self._pptx_preview_worker.start()
        return self.material_units.get_unit(clean_id)

    def drain_pptx_preview_renders(self, timeout: float = 60) -> None:
        deadline = time.monotonic() + max(float(timeout), 0.0)
        while True:
            with self._pptx_preview_worker_lock:
                worker = self._pptx_preview_worker
            with self._pptx_prerender_lock:
                batches = tuple(self._pptx_prerender_threads.values())
            threads = tuple(
                thread
                for thread in (worker, *batches)
                if thread is not None and thread.is_alive()
            )
            if not threads:
                return
            for thread in threads:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                thread.join(timeout=remaining)
            if time.monotonic() >= deadline:
                return

    def _schedule_pptx_prerender(self, version_id: str) -> None:
        if self._pptx_preview_adapter() is None:
            return
        with self._pptx_prerender_lock:
            if version_id in self._pptx_prerender_threads:
                return
            thread = threading.Thread(
                target=self._pptx_prerender_loop,
                args=(version_id,),
                name=f"pptx-prerender-{version_id[:12]}",
                daemon=True,
            )
            self._pptx_prerender_threads[version_id] = thread
            thread.start()

    def _pptx_prerender_loop(self, version_id: str) -> None:
        try:
            for _ in range(_PPTX_PRERENDER_PASSES):
                units = self.material_units.list_units(version_id)
                candidates = [
                    unit
                    for unit in sorted(
                        units, key=lambda item: item.unit_index
                    )
                    if unit.unit_kind == "ppt_slide"
                    and self._is_pptx_prerender_candidate(
                        unit.object_summary
                    )
                ]
                if not candidates:
                    return
                for unit in candidates:
                    self._pptx_prerender_unit(unit.id)
        except Exception:
            LOGGER.exception("PPT prerender batch failed for %s", version_id)
        finally:
            with self._pptx_prerender_lock:
                self._pptx_prerender_threads.pop(version_id, None)

    @staticmethod
    def _is_pptx_prerender_candidate(summary: Mapping[str, object]) -> bool:
        if summary.get("preview_kind") == "rendered":
            return False
        status = str(summary.get("preview_render_status") or "")
        return status in {"pending", "queued", "running"}

    def _pptx_prerender_unit(self, unit_id: str) -> None:
        deadline = time.monotonic() + _PPTX_PRERENDER_PAGE_TIMEOUT_SECONDS
        try:
            unit = self.material_units.get_unit(unit_id)
            if not self._is_pptx_prerender_candidate(unit.object_summary):
                return
            record = self.material_units.preview_record(unit_id)
            self.material_units.mark_preview_render_progress(
                unit_id,
                source_version_sha256=record.source_version_sha256,
                status="queued",
            )
        except Exception:
            return
        # On-demand requests win the render slot: never overwrite another
        # page that is already waiting, and give up on own page when an
        # on-demand request replaces it (the next pass retries it later).
        while True:
            with self._pptx_preview_worker_lock:
                wanted = self._pptx_preview_wanted
                if wanted is None or wanted == unit_id:
                    self._pptx_preview_wanted = unit_id
                    worker = self._pptx_preview_worker
                    if worker is None or not worker.is_alive():
                        self._pptx_preview_worker = threading.Thread(
                            target=self._pptx_preview_worker_loop,
                            name="pptx-preview-render",
                            daemon=True,
                        )
                        self._pptx_preview_worker.start()
                    break
            if time.monotonic() >= deadline:
                return
            time.sleep(_PPTX_PRERENDER_POLL_SECONDS)
        while time.monotonic() < deadline:
            with self._pptx_preview_worker_lock:
                wanted = self._pptx_preview_wanted
            if wanted is not None and wanted != unit_id:
                return
            try:
                current = self.material_units.get_unit(unit_id)
            except Exception:
                return
            summary = current.object_summary
            if summary.get("preview_kind") == "rendered":
                return
            status = str(summary.get("preview_render_status") or "")
            if status in {"completed", "failed"}:
                return
            time.sleep(_PPTX_PRERENDER_POLL_SECONDS)


    def _pptx_preview_worker_loop(self) -> None:
        while True:
            with self._pptx_preview_worker_lock:
                wanted = self._pptx_preview_wanted
                self._pptx_preview_wanted = None
                if wanted is None:
                    self._pptx_preview_worker = None
                    return
            try:
                record = self.material_units.preview_record(wanted)
                self.material_units.mark_preview_render_progress(
                    wanted,
                    source_version_sha256=record.source_version_sha256,
                    status="running",
                )
                self._render_pptx_preview(wanted)
            except Exception:
                LOGGER.exception("PPT preview render worker failed")


    def _should_render_pptx_preview(self, summary: Mapping[str, object]) -> bool:
        status = str(summary.get("preview_render_status") or "")
        if status == "completed":
            return False
        if status == "failed":
            if self._pptx_preview_adapter() is None:
                return False
            error = str(summary.get("preview_render_error_code") or "")
            if error == "wps_preview_unavailable":
                return True
            if error == "wps_preview_failed":
                attempts = summary.get("preview_render_attempts")
                counted = 1 if attempts is None else int(attempts)
                return counted < 2
            return False
        return True

    def _restore_structural_pptx_preview(
        self,
        *,
        record: MaterialPreviewRecord,
        unit: MaterialUnit,
    ) -> None:
        material_version_id = record.material_version_id
        source_version_sha256 = record.source_version_sha256
        preview_relpath = record.preview_relpath
        version = self.catalog.get_material_version(material_version_id)
        if version.material_type != "pptx":
            raise TeachingPrepValidationError(
                "only PPT slides can rebuild structural previews"
            )
        source = self.catalog.get_material_location(material_version_id)
        if (
            not source.is_file()
            or _sha256_file(source) != source_version_sha256
        ):
            raise TeachingPrepConflictError(
                "material file changed; register a new version"
            )
        parsed = tuple(
            self.material_parser.iter_preview_units(
                source,
                material_type="pptx",
                unit_indexes=(unit.unit_index,),
            )
        )
        if len(parsed) != 1 or parsed[0].unit_index != unit.unit_index:
            raise TeachingPrepNotFoundError(
                "material structural preview is unavailable"
            )
        structural = parsed[0]
        target = (self.root / preview_relpath).resolve(strict=False)
        preview_root = self.paths["previews"].resolve(strict=False)
        if not target.is_relative_to(preview_root):
            raise TeachingPrepValidationError(
                "material preview path is invalid"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        previous = target.read_bytes() if target.is_file() else None
        try:
            temporary.write_bytes(structural.preview_png)
            os.replace(temporary, target)
            try:
                self.material_units.save_partial_unit(
                    material_version_id,
                    source_version_sha256=source_version_sha256,
                    unit=structural,
                    preview_relpath=preview_relpath,
                    preview_sha256=hashlib.sha256(
                        structural.preview_png
                    ).hexdigest(),
                )
            except Exception:
                if previous is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(previous)
                raise
        finally:
            temporary.unlink(missing_ok=True)

    def _render_pptx_preview(self, unit_id: str) -> None:
        with self._material_parse_lock:
            self._render_pptx_preview_under_material_lock(unit_id)

    def _render_pptx_preview_under_material_lock(self, unit_id: str) -> None:
        with self._pptx_preview_lock:
            adapter = self._pptx_preview_adapter()
            unit = self.material_units.get_unit(unit_id)
            summary = unit.object_summary
            if summary.get("preview_kind") == "rendered":
                return
            if not self._should_render_pptx_preview(summary):
                return
            record = self.material_units.preview_record(unit_id)
            version = self.catalog.get_material_version(
                record.material_version_id
            )
            if version.material_type != "pptx":
                return
            if adapter is None:
                self.material_units.mark_preview_render_failed(
                    unit_id,
                    source_version_sha256=record.source_version_sha256,
                    error_code="wps_preview_unavailable",
                )
                return
            source = self.catalog.get_material_location(
                record.material_version_id
            )
            if (
                not source.is_file()
                or _sha256_file(source) != record.source_version_sha256
            ):
                self.material_units.mark_preview_render_failed(
                    unit_id,
                    source_version_sha256=record.source_version_sha256,
                    error_code="pptx_source_changed",
                )
                return
            error_code = "wps_preview_failed"
            try:
                working = (
                    self.paths["temp"]
                    / "pptx-preview-session"
                    / record.source_version_sha256
                )
                working.mkdir(parents=True, exist_ok=True)
                source_copy = working / "source-copy.pptx"
                rendered_dir = working / "rendered"
                if (
                    not source_copy.is_file()
                    or _sha256_file(source_copy) != record.source_version_sha256
                ):
                    closer = getattr(adapter, "close_preview_session", None)
                    if callable(closer):
                        closer()
                    shutil.copy2(source, source_copy)
                if _sha256_file(source_copy) != record.source_version_sha256:
                    raise TeachingPrepConflictError(
                        "isolated PPTX preview copy changed"
                    )
                adapter.render_previews(
                    operation_id=(
                        f"pptx-preview-{record.source_version_sha256[:16]}-"
                        f"{unit.unit_index:05d}"
                    ),
                    source_copy=str(source_copy),
                    preview_directory=str(rendered_dir),
                    slide_indexes=[unit.unit_index],
                    source_sha256=record.source_version_sha256,
                    timeout_milliseconds=30_000,
                )
                candidate = rendered_dir / (
                    f"slide-{unit.unit_index:05d}.png"
                )
                if (
                    not candidate.is_file()
                    or candidate.stat().st_size <= 0
                    or candidate.stat().st_size > 32 * 1024 * 1024
                ):
                    raise TeachingPrepValidationError(
                        "WPS preview output is invalid"
                    )
                try:
                    with Image.open(candidate) as image:
                        if image.format != "PNG":
                            raise TeachingPrepValidationError(
                                "WPS preview output is invalid"
                            )
                        image.verify()
                    with Image.open(candidate) as image:
                        width, height = image.size
                except (OSError, ValueError) as exc:
                    raise TeachingPrepValidationError(
                        "WPS preview output is invalid"
                    ) from exc
                if (
                    width <= 0
                    or height <= 0
                    or width > 8_192
                    or height > 8_192
                    or _sha256_file(source) != record.source_version_sha256
                ):
                    raise TeachingPrepValidationError(
                        "WPS preview output is invalid"
                    )
                target = self.root / record.preview_relpath
                previous = target.read_bytes()
                temporary = target.with_name(
                    f".{target.name}.{uuid4().hex}.tmp"
                )
                try:
                    shutil.copyfile(candidate, temporary)
                    rendered_sha = _sha256_file(temporary)
                    os.replace(temporary, target)
                    try:
                        self.material_units.mark_preview_rendered(
                            unit_id,
                            source_version_sha256=(
                                record.source_version_sha256
                            ),
                            preview_sha256=rendered_sha,
                            width=width,
                            height=height,
                        )
                    except Exception:
                        target.write_bytes(previous)
                        raise
                finally:
                    temporary.unlink(missing_ok=True)
            except TimeoutError:
                error_code = "wps_preview_timeout"
            except Exception:
                error_code = "wps_preview_failed"
            else:
                return
            self.material_units.mark_preview_render_failed(
                unit_id,
                source_version_sha256=record.source_version_sha256,
                error_code=error_code,
            )

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
        question_selection: Mapping[str, object] | None = None,
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
            "question_selection": (
                json.loads(
                    json.dumps(
                        dict(question_selection),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                if question_selection
                else {}
            ),
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
            question_selection=(
                dict(question_selection) if question_selection else {}
            ),
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
        result = draft_preflight(
            pack,
            mode=clean_mode,
            model_available=_model_adapter_available(
                self.lesson_model_adapter
            ),
            model_label=self.lesson_model_label,
        )
        result["model_destination_fingerprint"] = _stable_hash(
            {"model_label": self.lesson_model_label or "unavailable"}
        )
        return result

    def generate_lesson_draft(
        self,
        pack_id: str,
        *,
        operation_id: str,
        mode: str,
        confirmed: bool,
        task_model_gateway: object | None = None,
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
                model_kwargs = {
                    "operation_id": clean_operation_id,
                    "resource_pack": model_payload,
                    "page_loader": (
                        lambda source_ref, pack_payload=model_payload: (
                            self.load_frozen_page_for_model(
                                pack_payload,
                                str(source_ref),
                            )
                        )
                    ),
                    "observer": self._adaptation_observer(
                        lesson_node_id=pack.lesson_node_id,
                        operation_id=clean_operation_id,
                    ),
                }
                if task_model_gateway is not None:
                    model_kwargs["task_model_gateway"] = task_model_gateway
                raw = normalize_model_draft_payload(
                    adapter.generate(**model_kwargs),
                    pack,
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
        revision_input = dict(payload)
        if "review_findings" not in revision_input:
            revision_input["review_findings"] = default_review_findings(pack)
        clean_payload = validate_draft_payload(revision_input, pack)
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
        model_proposal: bool = False,
        task_model_gateway: object | None = None,
    ) -> tuple[SlidePlanVersion, bool]:
        clean_draft_id = _clean_entity_id(draft_id)
        clean_token = _clean_token(request_token)
        request_hash = _stable_hash(
            {
                "lesson_draft_id": clean_draft_id,
                "model_proposal": bool(model_proposal),
            }
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
        proposal_payload: dict[str, object] | None = None
        if model_proposal and _model_adapter_available(
            self.lesson_model_adapter
        ):
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
            observer = self._adaptation_observer(
                lesson_node_id=pack.lesson_node_id,
                operation_id=clean_token,
            )
            model_kwargs = {
                "operation_id": clean_token,
                "resource_pack": model_payload,
                "page_loader": (
                    lambda source_ref, pack_payload=model_payload: (
                        self.load_frozen_page_for_model(
                            pack_payload,
                            str(source_ref),
                        )
                    )
                ),
                "observer": observer,
            }
            if task_model_gateway is not None:
                model_kwargs["task_model_gateway"] = task_model_gateway
            try:
                raw = normalize_model_draft_payload(
                    adapter.generate(**model_kwargs),
                    pack,
                )
                proposal_payload = validate_draft_payload(raw, pack)
                if not proposal_payload.get("slide_adaptations"):
                    raise TeachingPrepValidationError(
                        "lesson model must classify every frozen reference slide"
                    )
            except TeachingPrepValidationError as exc:
                observer(
                    self._validation_failure_trace_event(
                        lesson_node_id=pack.lesson_node_id,
                        operation_id=clean_token,
                        reason=str(exc),
                    )
                )
                raise
        payload, source_ppt_state = build_slide_plan_payload(
            pack,
            draft,
            proposal_payload=proposal_payload,
        )
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
                "overrides": {
                    "target_slide_number": (
                        _clean_positive_bounded_int(
                            raw.get("target_slide_number"),
                            "target_slide_number",
                            maximum=2_000,
                        )
                        if raw.get("target_slide_number") is not None
                        else None
                    ),
                    "position": (
                        _clean_slide_position(raw.get("position"))
                        if raw.get("position") is not None
                        else None
                    ),
                    "text": (
                        _clean_text(
                            str(raw.get("text") or ""),
                            "slide_operation_text",
                            maximum=64,
                        )
                        if raw.get("text") is not None
                        else None
                    ),
                },
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
            before_target = deepcopy(raw_operation.get("target"))
            before_details = deepcopy(raw_operation.get("details"))
            after = str(review["decision"])
            candidate = {
                **raw_operation,
                **{
                    key: review[key]
                    for key in (
                        "decision",
                        "reason",
                        "planned_minutes",
                        "teacher_note",
                    )
                },
            }
            _apply_slide_operation_overrides(
                candidate,
                (
                    dict(review["overrides"])
                    if isinstance(review.get("overrides"), Mapping)
                    else {}
                ),
                slides=[
                    dict(item)
                    for item in payload.get("slides", [])
                    if isinstance(item, Mapping)
                ],
            )
            if after == "approved":
                require_approval_allowed(candidate)
            raw_operation.update(candidate)
            if (
                before != after
                or before_reason != review["reason"]
                or before_minutes != review["planned_minutes"]
                or before_teacher_note != review["teacher_note"]
                or before_target != candidate.get("target")
                or before_details != candidate.get("details")
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

    def list_question_scope_sections(
        self,
        volume_id: str,
    ) -> tuple[dict[str, object], ...]:
        if self.question_bank_path is None:
            raise TeachingPrepValidationError(
                "question bank is unavailable"
            )
        try:
            sections = list_volume_sections(
                self.question_bank_path,
                _clean_text(volume_id, "volume_id", maximum=64),
            )
        except QuestionSelectionError as exc:
            raise TeachingPrepValidationError(str(exc)) from exc
        return tuple(sections)

    def preview_question_selection(
        self,
        *,
        volume_id: str,
        section_ids: Sequence[str],
        difficulty_max: int,
        stem_max_chars: int,
        limit: int,
        max_per_method: int = 2,
        exclude_question_ids: Sequence[int] = (),
    ) -> dict[str, object]:
        if self.question_bank_path is None:
            raise TeachingPrepValidationError(
                "question bank is unavailable"
            )
        try:
            request = SelectionRequest(
                volume_id=_clean_text(volume_id, "volume_id", maximum=64),
                section_ids=tuple(
                    _clean_text(item, "section_id", maximum=64)
                    for item in section_ids
                ),
                difficulty_max=int(difficulty_max),
                stem_max_chars=int(stem_max_chars),
                limit=int(limit),
                max_per_method=int(max_per_method),
                exclude_question_ids=tuple(
                    int(item) for item in exclude_question_ids
                ),
            )
            return select_questions(self.question_bank_path, request)
        except (QuestionSelectionError, ValueError) as exc:
            raise TeachingPrepValidationError(str(exc)) from exc

    def execute_slide_plan_locally(
        self,
        plan_id: str,
        *,
        request_token: str,
        confirmed: bool,
    ) -> dict[str, object]:
        if not confirmed:
            raise TeachingPrepValidationError(
                "local execution requires explicit confirmation"
            )
        clean_plan_id = _clean_entity_id(plan_id)
        _clean_token(request_token)
        plan = self._effective_slide_plan(
            self.slide_plans.get(clean_plan_id)
        )
        if plan.status != "ready":
            raise TeachingPrepConflictError(
                "slide plan is not ready for execution"
            )
        draft = self.lesson_drafts.get(plan.lesson_draft_id)
        if draft.status != "confirmed":
            raise TeachingPrepConflictError(
                "slide plan requires a teacher-confirmed lesson draft"
            )
        pack = self.resource_packs.get(draft.resource_pack_id)
        row = execute_confirmed_plan(
            database=self.database,
            outputs_root=self.paths["outputs"],
            outputs_repo=self.pptx_outputs,
            plan_payload=plan.payload,
            plan_id=plan.id,
            draft_id=draft.id,
            pack_id=pack.id,
            pack_payload=pack.payload,
            lesson_node_id=pack.lesson_node_id,
            question_bank_path=self.question_bank_path,
        )
        return row

    def get_pptx_local_output(
        self,
        output_id: str,
    ) -> dict[str, object]:
        return self.pptx_outputs.get(_clean_entity_id(output_id))

    def list_pptx_local_outputs(
        self,
        lesson_node_id: str,
    ) -> tuple[dict[str, object], ...]:
        return self.pptx_outputs.list_for_lesson(
            _clean_entity_id(lesson_node_id)
        )

    def pptx_local_output_file(
        self,
        output_id: str,
    ) -> tuple[Path, str]:
        row = self.pptx_outputs.get(_clean_entity_id(output_id))
        relpath = str(row.get("output_relpath") or "")
        filename = str(row.get("output_filename") or "output.pptx")
        base = self.root.resolve()
        candidate = (base / relpath).resolve()
        if not str(candidate).startswith(str(base)):
            raise TeachingPrepValidationError("output path is invalid")
        if not candidate.is_file():
            raise TeachingPrepNotFoundError("output file is missing")
        return candidate, filename

    def generate_worksheet_for_output(
        self,
        output_id: str,
    ) -> dict[str, object]:
        if self.question_bank_path is None:
            raise TeachingPrepValidationError("question bank is unavailable")
        return generate_worksheet(
            database=self.database,
            outputs_root=self.paths["outputs"],
            outputs_repo=self.pptx_outputs,
            output_id=_clean_entity_id(output_id),
            question_bank_path=self.question_bank_path,
        )

    def worksheet_download(
        self,
        output_id: str,
    ) -> tuple[Path, str]:
        row = self.pptx_outputs.get(_clean_entity_id(output_id))
        return worksheet_file(self.paths["outputs"], row)

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
        publish: bool = True,
    ) -> tuple[PptxExecutionRun, PptxVersion | None, bool]:
        return self._execute_slide_plan_sync(
            plan_id,
            operation_id=operation_id,
            confirmed=confirmed,
            continue_existing=False,
            publish=publish,
        )


    def _require_generation_budget(self, run_id: str) -> None:
        self._remaining_generation_budget_ms(run_id)


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


    def _recover_material_deletion_files(self) -> int:
        recovered = 0
        for recovery in self.catalog.material_deletions_needing_file_recovery():
            operation_id = str(recovery["operation_id"])
            status = str(recovery["status"])
            staging = self.paths["staging"] / (
                "md-"
                + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:16]
            )
            if status == "succeeded":
                execution_staging_dirs: set[Path] = set()
                manifest = recovery.get("manifest")
                if isinstance(manifest, list):
                    for entry in manifest:
                        if not isinstance(entry, dict):
                            continue
                        source_relpath = str(
                            entry.get("source_relpath") or ""
                        )
                        if not source_relpath:
                            continue
                        execution_staging = (
                            self._pptx_execution_staging_for_deletion_source(
                                self.root / source_relpath
                            )
                        )
                        if execution_staging is not None:
                            execution_staging_dirs.add(execution_staging)
                shutil.rmtree(staging, ignore_errors=True)
                for execution_staging in execution_staging_dirs:
                    self._remove_empty_directory_tree(execution_staging)
                if not staging.exists():
                    self.catalog.clear_material_deletion_staging_manifest(
                        operation_id
                    )
                    recovered += 1
                continue
            manifest = recovery.get("manifest")
            recovery_safe = isinstance(manifest, list)
            expected_sources: list[Path] = []
            if recovery_safe:
                for entry in manifest:
                    if not isinstance(entry, dict):
                        recovery_safe = False
                        break
                    source_relpath = str(entry.get("source_relpath") or "")
                    staged_name = str(entry.get("staged_name") or "")
                    if (
                        re.fullmatch(r"f[0-9]{4}", staged_name) is None
                        or not source_relpath
                    ):
                        recovery_safe = False
                        break
                    source = (self.root / source_relpath).resolve(strict=False)
                    if (
                        self._controlled_material_paths((source,)) != [source]
                        and self._pptx_execution_staging_for_deletion_source(
                            source
                        )
                        is None
                    ):
                        recovery_safe = False
                        break
                    staged_path = staging / staged_name
                    expected_sources.append(source)
                    if not staged_path.is_file():
                        continue
                    if source.exists():
                        if (
                            not source.is_file()
                            or _sha256_file(source) != _sha256_file(staged_path)
                        ):
                            recovery_safe = False
                            break
                        staged_path.unlink()
                        continue
                    source.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(staged_path, source)
            if recovery_safe:
                recovery_safe = all(path.is_file() for path in expected_sources)
            if recovery_safe:
                shutil.rmtree(staging, ignore_errors=True)
                recovery_safe = not staging.exists()
            error_code = (
                "application_restarted_during_material_delete"
                if recovery_safe
                else "material_delete_recovery_incomplete"
            )
            self.catalog.interrupt_material_deletion(
                operation_id,
                error_code=error_code,
            )
            if recovery_safe:
                self.catalog.clear_material_deletion_staging_manifest(
                    operation_id
                )
                recovered += 1
            else:
                LOGGER.error(
                    "Material deletion recovery was incomplete for %s",
                    operation_id,
                )
        return recovered


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
    if isinstance(lessons, list) and any(
        isinstance(item, Mapping) and item.get("node_type") == "lesson"
        for item in lessons
    ):
        return
    materials = snapshot.get("materials")
    if not isinstance(materials, list) or len(materials) != 1:
        raise TeachingPrepValidationError(
            "initial lesson tree requires one textbook, exercise workbook, or homework workbook"
        )
    material = materials[0]
    role = (
        str(material.get("material_role") or "")
        if isinstance(material, Mapping)
        else ""
    )
    if role not in {
        "textbook",
        "exercise_workbook",
        "homework_workbook",
    }:
        raise TeachingPrepValidationError(
            "initial lesson tree requires a textbook or homework workbook"
        )


def _preview_payload_for_model(preview: Path) -> tuple[str, bytes]:
    suffix = preview.suffix.lower()
    mime_type = (
        "image/jpeg"
        if suffix in {".jpg", ".jpeg"}
        else "image/webp"
        if suffix == ".webp"
        else "image/png"
    )
    content = preview.read_bytes()
    if len(content) <= _MODEL_IMAGE_COMPRESS_OVER_BYTES:
        return mime_type, content
    try:
        with Image.open(preview) as image:
            rgb = image.convert("RGB")
            rgb.thumbnail((_MODEL_IMAGE_MAX_EDGE, _MODEL_IMAGE_MAX_EDGE))
            buffer = io.BytesIO()
            rgb.save(buffer, format="JPEG", quality=75, optimize=True)
            compressed = buffer.getvalue()
        if compressed and len(compressed) < len(content):
            return "image/jpeg", compressed
    except Exception:
        return mime_type, content
    return mime_type, content


def _select_model_page_images(
    buckets: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    purposes: Sequence[str],
    max_images: int,
    max_bytes: int,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    total_bytes = 0
    cursors = {purpose: 0 for purpose in purposes}
    progressed = True
    while progressed and len(result) < max_images:
        progressed = False
        for purpose in purposes:
            items = buckets.get(purpose) or ()
            index = cursors[purpose]
            if index >= len(items) or len(result) >= max_images:
                continue
            item = dict(items[index])
            cursors[purpose] = index + 1
            progressed = True
            content = item.get("content")
            size = len(content) if isinstance(content, (bytes, bytearray)) else 0
            if size <= 0 or total_bytes + size > max_bytes:
                continue
            total_bytes += size
            result.append(item)
    return result


def _preview_ready(run: PptxExecutionRun) -> bool:
    return (
        run.status == "verifying"
        and run.published_version_id is None
        and isinstance(run.verification_report, Mapping)
        and bool(run.verification_report)
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


def _clean_nonnegative_int(
    value: int,
    field: str,
    *,
    maximum: int,
) -> int:
    clean = int(value)
    if clean < 0 or clean > maximum:
        raise TeachingPrepValidationError(f"{field} is invalid")
    return clean


def _clean_positive_bounded_int(
    value: object,
    field: str,
    *,
    maximum: int,
) -> int:
    if isinstance(value, bool):
        raise TeachingPrepValidationError(f"{field} is invalid")
    try:
        clean = int(value)
    except (TypeError, ValueError) as exc:
        raise TeachingPrepValidationError(f"{field} is invalid") from exc
    if clean < 1 or clean > maximum:
        raise TeachingPrepValidationError(f"{field} is invalid")
    return clean


def _clean_slide_position(value: object) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != {
        "x",
        "y",
        "width",
        "height",
    }:
        raise TeachingPrepValidationError("slide operation position is invalid")
    try:
        result = {
            key: float(value[key])
            for key in ("x", "y", "width", "height")
        }
    except (TypeError, ValueError) as exc:
        raise TeachingPrepValidationError(
            "slide operation position is invalid"
        ) from exc
    if (
        result["x"] < 0
        or result["y"] < 0
        or result["width"] < 0.03
        or result["height"] < 0.03
        or result["x"] + result["width"] > 1
        or result["y"] + result["height"] > 1
    ):
        raise TeachingPrepValidationError("slide operation position is invalid")
    return result


def _apply_slide_operation_overrides(
    operation: dict[str, object],
    overrides: Mapping[str, object],
    *,
    slides: Sequence[Mapping[str, object]],
) -> None:
    kind = str(operation.get("kind") or "")
    target = (
        dict(operation["target"])
        if isinstance(operation.get("target"), Mapping)
        else {}
    )
    details = (
        dict(operation["details"])
        if isinstance(operation.get("details"), Mapping)
        else {}
    )
    target_slide_number = overrides.get("target_slide_number")
    if target_slide_number is not None:
        if kind != "insert_static_image" or target.get("target_kind") != "existing_slide":
            raise TeachingPrepValidationError(
                "only an existing-slide insertion can change target slide"
            )
        matching = [
            item
            for item in slides
            if item.get("original_index") == target_slide_number
        ]
        if len(matching) != 1:
            raise TeachingPrepValidationError(
                "slide operation target page is unavailable"
            )
        target["slide_signature"] = matching[0].get("stable_signature")
        target["generated_page_number"] = matching[0].get("original_index")
    position = overrides.get("position")
    if position is not None:
        if kind not in {"add_text_box", "insert_static_image"}:
            raise TeachingPrepValidationError(
                "this slide operation cannot change position"
            )
        target["position"] = _clean_slide_position(position)
    text = overrides.get("text")
    if text is not None:
        clean_text = str(text).strip()
        if (
            kind != "add_text_box"
            or details.get("semantic_role") != "textbook_page_label"
            or re.fullmatch(r"教材 P\d{1,4}(?:、\d{1,4})*", clean_text) is None
        ):
            raise TeachingPrepValidationError(
                "textbook page label is invalid"
            )
        details["text"] = clean_text
        target["content_summary"] = clean_text
    operation["target"] = target
    operation["details"] = details


def _clean_exercise_minutes(value: int | None) -> int | None:
    if value is None:
        return None
    clean = int(value)
    if clean < 1 or clean > 60:
        raise TeachingPrepValidationError("estimated_minutes is invalid")
    return clean


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


def _clean_workbook_volume(value: str | None) -> str | None:
    clean = str(value or "").strip().upper() or None
    if clean is not None and clean not in {"A", "B"}:
        raise TeachingPrepValidationError("workbook_volume is invalid")
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
