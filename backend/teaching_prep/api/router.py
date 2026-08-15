from __future__ import annotations

from pathlib import Path
import threading
from urllib.parse import quote, unquote
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import FileResponse, JSONResponse

from backend.jobs import JobManager, JobRecord
from backend.public_data import (
    sanitize_public_diagnostic_text,
    sanitize_public_mapping,
)
from backend.teaching_prep.application import TeachingPrepService
from backend.teaching_prep.application.ai_task_adapter import (
    bind_adoption_command,
)
from backend.teaching_prep.domain.errors import (
    TeachingPrepConflictError,
    TeachingPrepNotFoundError,
    TeachingPrepRetryAvailableError,
    TeachingPrepStateError,
    TeachingPrepValidationError,
)
from backend.workspaces.ai_tasks.models import (
    HandoffNotFoundError,
    RevisionConflictError as WorkspaceAIRevisionConflictError,
    WorkspaceAITaskError,
)

from .schemas import (
    ActivateUpClassPackageRequest,
    AdoptWorkspaceAIHandoffRequest,
    ActivatePptxVersionRequest,
    ActivatePptxVersionResponse,
    AssessmentChoiceListResponse,
    AssessmentChoiceResponse,
    AttachSemesterMaterialRequest,
    ApplySemesterMappingProposalRequest,
    CreateCurriculumRequest,
    CreateSemesterWorkspaceRequest,
    CreatePostLessonReviewRequest,
    CreateUpClassPackageRequest,
    CreateLessonNodeRequest,
    CreateMaterialLinkRequest,
    CreateExerciseCandidateRequest,
    CreatePreparationRequest,
    CreateReferencePptCollectionRequest,
    CreateSlidePlanRequest,
    DeleteMaterialSourceRequest,
    DeleteMaterialSourceResponse,
    DiscardPptxStagingRequest,
    DeriveClassVariantRequest,
    ConfirmPptxPreviewRequest,
    ExecuteSlidePlanRequest,
    CurriculumListResponse,
    CurriculumResponse,
    ClassVariantListResponse,
    ClassVariantResponse,
    ClassVariantResultResponse,
    ExerciseCandidateListResponse,
    ExerciseCandidateResponse,
    ExerciseSuggestionPreflightResponse,
    ExerciseSuggestionResponse,
    ExerciseSuggestionRunResponse,
    FreezeResourcePackRequest,
    GenerateLessonDraftRequest,
    LessonDraftListResponse,
    LessonDraftGenerationCancellationResponse,
    LessonDraftPreflightResponse,
    LessonGenerationPerformanceResponse,
    LessonDraftResponse,
    LessonDraftCapacityPreviewRequest,
    LessonNodeResponse,
    LessonPreparationStatusListResponse,
    LessonPreparationStatusResponse,
    LessonTreeResponse,
    MaterialVersionListResponse,
    MaterialVersionResponse,
    MaterialLinkListResponse,
    MaterialLinkResponse,
    MaterialDeletionImpactResponse,
    MaterialParseJobListResponse,
    MaterialParseJobResponse,
    MaterialUnitListResponse,
    MaterialUnitResponse,
    PreparationListResponse,
    PreparationResponse,
    PptxExecutionListResponse,
    PptxExecutionResponse,
    PptxExecutionResultResponse,
    PptxVersionResponse,
    PptxVersionListItemResponse,
    PptxVersionListResponse,
    PostLessonReviewListResponse,
    PostLessonReviewResponse,
    QuestionEvidenceChoiceListResponse,
    QuestionEvidenceChoiceResponse,
    ReorderLessonNodesRequest,
    ResourcePackListResponse,
    ResourcePackResponse,
    ResourcePackSelectionPreflightRequest,
    ResourcePackStatusResponse,
    ReviseLessonDraftRequest,
    ReviseSlidePlanRequest,
    SlidePlanListResponse,
    SlidePlanPreviewResponse,
    SlidePlanResponse,
    SemesterLessonProgressListResponse,
    SemesterLessonProgressResponse,
    SemesterListResponse,
    SemesterMaterialListResponse,
    SemesterMaterialResponse,
    SemesterMappingJobListResponse,
    SemesterMappingJobResponse,
    SemesterMappingPreflightResponse,
    SemesterMappingProposalListResponse,
    SemesterMappingProposalResponse,
    SemesterMappingRequest,
    StartSemesterMappingJobRequest,
    SemesterResponse,
    SemesterWorkspaceResponse,
    ReviewSemesterMappingRowRequest,
    RejectSemesterMappingProposalRequest,
    ReferenceSelectionDraftResponse,
    ReferenceSelectionPreflightResponse,
    ReferenceSelectionSnapshotResponse,
    ReferencePptCollectionListResponse,
    ReferencePptCollectionResponse,
    SaveReferenceSelectionDraftRequest,
    FreezeReferenceSelectionSnapshotRequest,
    StartExerciseSuggestionRunRequest,
    StartSlideAnimationRunRequest,
    DecideSlideAnimationRunRequest,
    SlideAnimationRunResponse,
    SlideAnimationRunListResponse,
    ReviewExerciseSuggestionRequest,
    CreateSemesterRequest,
    SetSemesterLessonProgressRequest,
    TeachingPrepStatusResponse,
    TeachingPrepAIAdoptionResponse,
    TeachingPreferencesResponse,
    UpdateTeachingPreferencesRequest,
    UpdateLessonNodeRequest,
    UpdateSemesterMaterialRequest,
    UpdateReferencePptCollectionRequest,
    UpdateSemesterRequest,
    UpdateMaterialLinkRequest,
    UpdateExerciseCandidateRequest,
    UpdateMaterialUnitRequest,
    UpdateMaterialSourceRequest,
    UpdatePreparationRequest,
    UpClassPackageListResponse,
    UpClassPackageResponse,
)

_MAX_MATERIAL_UPLOAD_BYTES = 256 * 1024 * 1024
_MATERIAL_PARSE_JOB_TYPE = "teaching_prep.material_parse"
_MATERIAL_PARSE_SUBMIT_LOCK = threading.Lock()
_SEMESTER_MAPPING_JOB_TYPE = "teaching_prep.semester_mapping"
_SEMESTER_MAPPING_SUBMIT_LOCK = threading.Lock()


def _material_parse_job_response(job: JobRecord) -> MaterialParseJobResponse:
    public_error = None
    if job.error:
        public_error = (
            "资料处理没有完成，可保留已生成的预览后重试。"
            if job.status == "failed"
            else "资料处理意外结束，可重新打开资料继续。"
        )
    return MaterialParseJobResponse(
        id=job.id,
        job_type=job.job_type,
        payload=sanitize_public_mapping(job.payload),
        result=sanitize_public_mapping(job.result),
        status=job.status,
        progress=job.progress,
        stage=job.stage,
        detail=sanitize_public_diagnostic_text(job.detail) or "",
        error=public_error,
        cancel_requested=job.cancel_requested,
        created_at=job.created_at,
        started_at=job.started_at,
        updated_at=job.updated_at,
        finished_at=job.finished_at,
    )


def _active_material_parse_job(
    manager: JobManager,
    material_version_id: str,
) -> JobRecord | None:
    return manager.store.find_latest_job_by_payload(
        job_type=_MATERIAL_PARSE_JOB_TYPE,
        payload_equals={"material_version_id": material_version_id},
        statuses=("queued", "running", "paused"),
    )


def _latest_material_parse_jobs(manager: JobManager) -> tuple[JobRecord, ...]:
    return manager.store.list_latest_jobs_by_payload_key(
        job_type=_MATERIAL_PARSE_JOB_TYPE,
        payload_key="material_version_id",
        identity_length=32,
    )


def _semester_mapping_job_response(
    job: JobRecord,
) -> SemesterMappingJobResponse:
    payload_keys = (
        "semester_id",
        "material_record_id",
        "operation_id",
        "source_state_sha256",
    )
    result_keys = (
        "semester_id",
        "operation_id",
        "source_state_sha256",
        "proposal_id",
        "recovered_existing",
    )
    public_error = None
    if job.error:
        normalized_error = job.error.lower()
        if "response text is unavailable" in normalized_error:
            public_error = (
                "模型已返回，但没有可读取的正文；可重新检查后手动生成。"
            )
        elif "returned invalid json" in normalized_error:
            public_error = (
                "模型已返回，但目录格式不是有效 JSON；可重新检查后手动生成。"
            )
        elif "output was truncated" in normalized_error:
            public_error = (
                "模型输出达到长度上限，目录没有完整返回；系统未自动重试。"
            )
        elif "response must be an object" in normalized_error:
            public_error = (
                "模型已返回，但目录顶层结构不是对象；可重新检查后手动生成。"
            )
        elif "response failed local validation" in normalized_error:
            public_error = (
                "模型目录未通过页码和结构校验；可重新检查后手动生成。"
            )
        elif (
            "attempted to replace the existing lesson tree"
            in normalized_error
        ):
            public_error = (
                "模型尝试重建已有课时目录，本次建议已拦截；请重新生成映射。"
            )
        elif (
            "referred to a lesson outside the existing tree"
            in normalized_error
        ):
            public_error = (
                "模型引用了当前目录中不存在的课时，本次建议已拦截；请重新生成映射。"
            )
        elif "omitted uncertainty for unmapped pages" in normalized_error:
            public_error = (
                "模型没有说明未映射的资料页，本次建议已拦截；请重新生成映射。"
            )
        elif "model configuration is unavailable" in normalized_error:
            public_error = (
                "当前备课模型配置不可用，请先检查“大模型 API”设置。"
            )
        elif "request parameter is incompatible" in normalized_error:
            public_error = (
                "当前模型不接受目录请求参数，请检查模型配置后手动生成。"
            )
        elif job.stage in {"queued", "checking", "snapshotting", "claiming_operation"}:
            public_error = (
                "目录建议任务在模型请求前停止，未自动发出新的模型请求。"
            )
        elif (
            "restart" in normalized_error
            or "interrupted" in normalized_error
            or "result is unknown" in normalized_error
        ):
            public_error = (
                "应用重启时模型结果可能未知，系统已阻止自动重发。"
            )
        else:
            public_error = "目录建议没有完成，系统未自动重试模型请求。"
    return SemesterMappingJobResponse(
        id=job.id,
        job_type=job.job_type,
        payload=sanitize_public_mapping(
            {key: job.payload[key] for key in payload_keys if key in job.payload}
        ),
        result=sanitize_public_mapping(
            {key: job.result[key] for key in result_keys if key in job.result}
        ),
        status=job.status,
        progress=job.progress,
        stage=job.stage,
        detail=sanitize_public_diagnostic_text(job.detail) or "",
        error=public_error,
        cancel_requested=job.cancel_requested,
        created_at=job.created_at,
        started_at=job.started_at,
        updated_at=job.updated_at,
        finished_at=job.finished_at,
    )


def _matching_semester_mapping_job(
    manager: JobManager,
    *,
    semester_id: str,
    material_record_id: str,
    source_state_sha256: str,
) -> JobRecord | None:
    return manager.store.find_latest_job_by_payload(
        job_type=_SEMESTER_MAPPING_JOB_TYPE,
        payload_equals={
            "semester_id": semester_id,
            "material_record_id": material_record_id,
            "source_state_sha256": source_state_sha256,
        },
        statuses=("queued", "running", "paused", "succeeded"),
    )


def _latest_semester_mapping_jobs(
    manager: JobManager,
    semester_id: str,
) -> tuple[JobRecord, ...]:
    return manager.store.list_latest_jobs_by_payload_key(
        job_type=_SEMESTER_MAPPING_JOB_TYPE,
        payload_key="material_record_id",
        identity_length=32,
        payload_equals={"semester_id": semester_id},
    )


def get_teaching_prep_service(request: Request) -> TeachingPrepService:
    services = getattr(request.app.state, "workspace_services", {})
    service = services.get("teaching-prep") if isinstance(services, dict) else None
    if not isinstance(service, TeachingPrepService):
        raise _new_api_error(
            503,
            "teaching_prep_unavailable",
            "Teaching preparation workspace is unavailable",
        )
    return service


def get_teaching_prep_job_manager(request: Request) -> JobManager:
    manager = getattr(request.app.state, "job_manager", None)
    if not isinstance(manager, JobManager):
        raise _new_api_error(
            503,
            "job_manager_unavailable",
            "Background job manager is unavailable",
        )
    return manager


def create_router() -> APIRouter:
    router = APIRouter(tags=["teaching-prep"])

    @router.get("/status", response_model=TeachingPrepStatusResponse)
    def module_status(
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> TeachingPrepStatusResponse:
        return TeachingPrepStatusResponse.model_validate(service.status())

    @router.get(
        "/preferences",
        response_model=TeachingPreferencesResponse,
    )
    def get_teaching_preferences(
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> TeachingPreferencesResponse:
        try:
            item = service.get_teaching_preferences()
        except Exception as exc:
            raise _api_error(exc) from exc
        return TeachingPreferencesResponse.from_domain(item)

    @router.patch(
        "/preferences",
        response_model=TeachingPreferencesResponse,
    )
    def update_teaching_preferences(
        payload: UpdateTeachingPreferencesRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> TeachingPreferencesResponse:
        try:
            item = service.update_teaching_preferences(
                expected_revision=payload.expected_revision,
                payload=payload.payload.model_dump(mode="python"),
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return TeachingPreferencesResponse.from_domain(item)

    @router.get(
        "/preparations",
        response_model=PreparationListResponse,
    )
    def list_preparations(
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> PreparationListResponse:
        return PreparationListResponse(
            items=[
                PreparationResponse.from_domain(item)
                for item in service.list_preparations()
            ]
        )

    @router.post(
        "/preparations",
        response_model=PreparationResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_preparation(
        payload: CreatePreparationRequest,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> PreparationResponse:
        try:
            preparation, created = service.create_preparation(
                request_token=payload.request_token,
                title=payload.title,
                class_name=payload.class_name,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return PreparationResponse.from_domain(preparation)

    @router.get(
        "/preparations/{preparation_id}",
        response_model=PreparationResponse,
    )
    def get_preparation(
        preparation_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> PreparationResponse:
        try:
            preparation = service.get_preparation(preparation_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return PreparationResponse.from_domain(preparation)

    @router.patch(
        "/preparations/{preparation_id}",
        response_model=PreparationResponse,
    )
    def update_preparation(
        preparation_id: str,
        payload: UpdatePreparationRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> PreparationResponse:
        try:
            preparation = service.update_preparation(
                preparation_id,
                expected_revision=payload.expected_revision,
                title=payload.title,
                class_name=payload.class_name,
                target_state=payload.target_state,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return PreparationResponse.from_domain(preparation)

    @router.get(
        "/curricula",
        response_model=CurriculumListResponse,
    )
    def list_curricula(
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> CurriculumListResponse:
        return CurriculumListResponse(
            items=[
                CurriculumResponse.from_domain(item)
                for item in service.list_curricula()
            ]
        )

    @router.post(
        "/curricula",
        response_model=CurriculumResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_curriculum(
        payload: CreateCurriculumRequest,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> CurriculumResponse:
        try:
            curriculum, created = service.create_curriculum(
                request_token=payload.request_token,
                title=payload.title,
                grade_level=payload.grade_level,
                volume=payload.volume,
                publisher=payload.publisher,
                edition_label=payload.edition_label,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return CurriculumResponse.from_domain(curriculum)

    @router.post(
        "/semester-workspaces",
        response_model=SemesterWorkspaceResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_semester_workspace(
        payload: CreateSemesterWorkspaceRequest,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SemesterWorkspaceResponse:
        try:
            curriculum, semester, created = service.create_semester_workspace(
                request_token=payload.request_token,
                title=payload.curriculum.title,
                grade_level=payload.curriculum.grade_level,
                volume=payload.curriculum.volume,
                publisher=payload.curriculum.publisher,
                edition_label=payload.curriculum.edition_label,
                school_year=payload.school_year,
                term=payload.term,
                planned_new_lesson_count=payload.planned_new_lesson_count,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return SemesterWorkspaceResponse(
            curriculum=CurriculumResponse.from_domain(curriculum),
            semester=SemesterResponse.from_domain(semester),
        )

    @router.get(
        "/semesters",
        response_model=SemesterListResponse,
    )
    def list_semesters(
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SemesterListResponse:
        try:
            items = service.list_semesters()
        except Exception as exc:
            raise _api_error(exc) from exc
        return SemesterListResponse(
            items=[SemesterResponse.from_domain(item) for item in items]
        )

    @router.post(
        "/semesters",
        response_model=SemesterResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_semester(
        payload: CreateSemesterRequest,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SemesterResponse:
        try:
            item, created = service.create_semester(
                request_token=payload.request_token,
                curriculum_id=payload.curriculum_id,
                school_year=payload.school_year,
                term=payload.term,
                planned_new_lesson_count=payload.planned_new_lesson_count,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return SemesterResponse.from_domain(item)

    @router.patch(
        "/semesters/{semester_id}",
        response_model=SemesterResponse,
    )
    def update_semester(
        semester_id: str,
        payload: UpdateSemesterRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SemesterResponse:
        try:
            item = service.update_semester(
                semester_id,
                expected_revision=payload.expected_revision,
                planned_new_lesson_count=payload.planned_new_lesson_count,
                status=payload.status,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return SemesterResponse.from_domain(item)

    @router.get(
        "/semesters/{semester_id}/lesson-progress",
        response_model=SemesterLessonProgressListResponse,
    )
    def list_semester_lesson_progress(
        semester_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SemesterLessonProgressListResponse:
        try:
            items = service.list_semester_lesson_progress(semester_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return SemesterLessonProgressListResponse(
            items=[
                SemesterLessonProgressResponse.from_domain(item)
                for item in items
            ]
        )

    @router.put(
        "/semesters/{semester_id}/lesson-progress/{lesson_node_id}",
        response_model=SemesterLessonProgressResponse,
    )
    def set_semester_lesson_progress(
        semester_id: str,
        lesson_node_id: str,
        payload: SetSemesterLessonProgressRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SemesterLessonProgressResponse:
        try:
            item = service.set_semester_lesson_progress(
                semester_id,
                lesson_node_id,
                status=payload.status,
                expected_revision=payload.expected_revision,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return SemesterLessonProgressResponse.from_domain(item)

    @router.get(
        "/semesters/{semester_id}/materials",
        response_model=SemesterMaterialListResponse,
    )
    def list_semester_materials(
        semester_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SemesterMaterialListResponse:
        try:
            items = service.list_semester_materials(semester_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return SemesterMaterialListResponse(
            items=[
                SemesterMaterialResponse.from_domain(item)
                for item in items
            ]
        )

    @router.post(
        "/semesters/{semester_id}/materials",
        response_model=SemesterMaterialResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def attach_semester_material(
        semester_id: str,
        payload: AttachSemesterMaterialRequest,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SemesterMaterialResponse:
        try:
            item, created = service.attach_semester_material(
                semester_id,
                request_token=payload.request_token,
                material_version_id=payload.material_version_id,
                material_role=payload.material_role,
                workbook_series=payload.workbook_series,
                workbook_volume=payload.workbook_volume,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return SemesterMaterialResponse.from_domain(item)

    @router.patch(
        "/semester-materials/{record_id}",
        response_model=SemesterMaterialResponse,
    )
    def update_semester_material(
        record_id: str,
        payload: UpdateSemesterMaterialRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SemesterMaterialResponse:
        try:
            item = service.update_semester_material(
                record_id,
                expected_revision=payload.expected_revision,
                material_role=payload.material_role,
                mapping_status=payload.mapping_status,
                is_active=payload.is_active,
                workbook_series=payload.workbook_series,
                workbook_volume=payload.workbook_volume,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return SemesterMaterialResponse.from_domain(item)

    @router.get(
        "/semesters/{semester_id}/reference-ppt-collections",
        response_model=ReferencePptCollectionListResponse,
    )
    def list_reference_ppt_collections(
        semester_id: str,
        include_inactive: bool = Query(default=False),
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ReferencePptCollectionListResponse:
        try:
            items = service.list_reference_ppt_collections(
                semester_id,
                include_inactive=include_inactive,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return ReferencePptCollectionListResponse(
            items=[
                ReferencePptCollectionResponse.from_domain(item)
                for item in items
            ]
        )

    @router.post(
        "/semesters/{semester_id}/reference-ppt-collections",
        response_model=ReferencePptCollectionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_reference_ppt_collection(
        semester_id: str,
        payload: CreateReferencePptCollectionRequest,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ReferencePptCollectionResponse:
        try:
            item, created = service.create_reference_ppt_collection(
                semester_id,
                request_token=payload.request_token,
                display_name=payload.display_name,
                ignored_file_count=payload.ignored_file_count,
                members=[member.model_dump() for member in payload.members],
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return ReferencePptCollectionResponse.from_domain(item)

    @router.patch(
        "/reference-ppt-collections/{collection_id}",
        response_model=ReferencePptCollectionResponse,
    )
    def update_reference_ppt_collection(
        collection_id: str,
        payload: UpdateReferencePptCollectionRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ReferencePptCollectionResponse:
        try:
            item = service.update_reference_ppt_collection(
                collection_id,
                is_active=payload.is_active,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return ReferencePptCollectionResponse.from_domain(item)

    @router.post(
        "/semesters/{semester_id}/mapping-preflight",
        response_model=SemesterMappingPreflightResponse,
    )
    def semester_mapping_preflight(
        semester_id: str,
        payload: SemesterMappingRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SemesterMappingPreflightResponse:
        try:
            item = service.semester_mapping_preflight(
                semester_id,
                material_record_ids=payload.material_record_ids,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return SemesterMappingPreflightResponse.model_validate(item)

    @router.post(
        "/semesters/{semester_id}/mapping-proposal-jobs",
        response_model=SemesterMappingJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def start_semester_mapping_job(
        semester_id: str,
        payload: StartSemesterMappingJobRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
        manager: JobManager = Depends(get_teaching_prep_job_manager),
    ) -> SemesterMappingJobResponse:
        try:
            preflight = service.semester_mapping_preflight(
                semester_id,
                material_record_ids=[payload.material_record_id],
            )
            source_digest = str(preflight["source_state_sha256"])
            if source_digest != payload.expected_source_state_sha256:
                raise TeachingPrepConflictError(
                    "semester lessons or materials changed; check the send scope again"
                )
            if not bool(preflight["model_available"]):
                raise TeachingPrepValidationError(
                    "semester mapping model is unavailable"
                )
            with _SEMESTER_MAPPING_SUBMIT_LOCK:
                job = _matching_semester_mapping_job(
                    manager,
                    semester_id=semester_id,
                    material_record_id=payload.material_record_id,
                    source_state_sha256=source_digest,
                )
                if job is None:
                    job = manager.submit(
                        _SEMESTER_MAPPING_JOB_TYPE,
                        {
                            "semester_id": semester_id,
                            "material_record_id": payload.material_record_id,
                            "operation_id": payload.operation_id,
                            "source_state_sha256": source_digest,
                        },
                    )
        except Exception as exc:
            raise _api_error(exc) from exc
        return _semester_mapping_job_response(job)

    @router.get(
        "/semesters/{semester_id}/mapping-proposal-jobs",
        response_model=SemesterMappingJobListResponse,
    )
    def list_semester_mapping_jobs(
        semester_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
        manager: JobManager = Depends(get_teaching_prep_job_manager),
    ) -> SemesterMappingJobListResponse:
        try:
            service.list_semester_materials(semester_id)
            jobs = _latest_semester_mapping_jobs(manager, semester_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return SemesterMappingJobListResponse(
            items=[_semester_mapping_job_response(job) for job in jobs]
        )

    @router.get(
        "/semesters/{semester_id}/mapping-proposals",
        response_model=SemesterMappingProposalListResponse,
    )
    def list_semester_mapping_proposals(
        semester_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SemesterMappingProposalListResponse:
        try:
            items = service.list_semester_mapping_proposals(semester_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return SemesterMappingProposalListResponse(
            items=[
                SemesterMappingProposalResponse.from_domain(item)
                for item in items
            ]
        )

    @router.post(
        "/semester-mapping-proposals/{proposal_id}/apply",
        response_model=SemesterMappingProposalResponse,
    )
    def apply_semester_mapping_proposal(
        proposal_id: str,
        payload: ApplySemesterMappingProposalRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SemesterMappingProposalResponse:
        try:
            item = service.apply_semester_mapping_proposal(
                proposal_id,
                expected_revision=payload.expected_revision,
                chapter_key=payload.chapter_key,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return SemesterMappingProposalResponse.from_domain(item)

    @router.get(
        "/curricula/{curriculum_id}/lessons",
        response_model=LessonTreeResponse,
    )
    def list_lesson_tree(
        curriculum_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> LessonTreeResponse:
        try:
            nodes = service.list_lesson_nodes(curriculum_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return LessonTreeResponse(
            curriculum_id=curriculum_id,
            items=[LessonNodeResponse.from_domain(item) for item in nodes],
        )

    @router.post(
        "/curricula/{curriculum_id}/lessons",
        response_model=LessonNodeResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_lesson_node(
        curriculum_id: str,
        payload: CreateLessonNodeRequest,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> LessonNodeResponse:
        try:
            node, created = service.create_lesson_node(
                request_token=payload.request_token,
                curriculum_id=curriculum_id,
                parent_id=payload.parent_id,
                node_type=payload.node_type,
                title=payload.title,
                duration_minutes=payload.duration_minutes,
                source_kind=payload.source_kind,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return LessonNodeResponse.from_domain(node)

    @router.patch(
        "/lessons/{node_id}",
        response_model=LessonNodeResponse,
    )
    def update_lesson_node(
        node_id: str,
        payload: UpdateLessonNodeRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> LessonNodeResponse:
        try:
            node = service.update_lesson_node(
                node_id,
                expected_revision=payload.expected_revision,
                title=payload.title,
                duration_minutes=payload.duration_minutes,
                is_active=payload.is_active,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return LessonNodeResponse.from_domain(node)

    @router.post(
        "/curricula/{curriculum_id}/lessons/reorder",
        response_model=LessonTreeResponse,
    )
    def reorder_lesson_nodes(
        curriculum_id: str,
        payload: ReorderLessonNodesRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> LessonTreeResponse:
        try:
            nodes = service.reorder_lesson_nodes(
                curriculum_id=curriculum_id,
                parent_id=payload.parent_id,
                ordered_ids=payload.ordered_ids,
                expected_revisions=payload.expected_revisions,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return LessonTreeResponse(
            curriculum_id=curriculum_id,
            items=[LessonNodeResponse.from_domain(item) for item in nodes],
        )

    @router.get(
        "/materials",
        response_model=MaterialVersionListResponse,
    )
    def list_material_versions(
        search: str | None = Query(default=None, max_length=120),
        material_type: str | None = Query(default=None),
        availability: str | None = Query(default=None),
        include_archived: bool = Query(default=False),
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> MaterialVersionListResponse:
        try:
            items = service.list_material_versions(
                search=search,
                material_type=material_type,
                availability=availability,
                include_archived=include_archived,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return MaterialVersionListResponse(
            items=[
                MaterialVersionResponse.from_domain(item)
                for item in items
            ]
        )

    @router.patch(
        "/material-sources/{source_id}",
        response_model=MaterialVersionResponse,
    )
    def update_material_source(
        source_id: str,
        payload: UpdateMaterialSourceRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> MaterialVersionResponse:
        try:
            item = service.update_material_source(
                source_id,
                expected_revision=payload.expected_revision,
                display_name=payload.display_name,
                archived=payload.archived,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return MaterialVersionResponse.from_domain(item)

    @router.delete(
        "/material-sources/{source_id}",
        response_model=DeleteMaterialSourceResponse,
    )
    def delete_material_source(
        source_id: str,
        payload: DeleteMaterialSourceRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> DeleteMaterialSourceResponse:
        try:
            result = service.delete_material_source(
                source_id,
                expected_revision=payload.expected_revision,
                operation_id=payload.operation_id,
                preview_version=payload.preview_version,
                confirmation_phrase=payload.confirmation_phrase,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return DeleteMaterialSourceResponse.model_validate(result)

    @router.get(
        "/material-sources/{source_id}/deletion-preview",
        response_model=MaterialDeletionImpactResponse,
    )
    def preview_material_source_deletion(
        source_id: str,
        expected_revision: int = Query(gt=0),
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> MaterialDeletionImpactResponse:
        try:
            result = service.preview_material_deletion(
                source_id,
                expected_revision=expected_revision,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return MaterialDeletionImpactResponse.model_validate(result)

    @router.get(
        "/material-deletions/{operation_id}",
        response_model=DeleteMaterialSourceResponse,
    )
    def get_material_source_deletion(
        operation_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> DeleteMaterialSourceResponse:
        try:
            result = service.get_material_deletion(operation_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return DeleteMaterialSourceResponse.model_validate(result)

    @router.post(
        "/materials/import-copy",
        response_model=MaterialVersionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def import_material_copy(
        request: Request,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> MaterialVersionResponse:
        item, created = await _receive_material_copy(
            request=request,
            service=service,
            relocate_version_id=None,
        )
        if not created:
            response.status_code = status.HTTP_200_OK
        return MaterialVersionResponse.from_domain(item)

    @router.post(
        "/materials/{material_version_id}/relocate-copy",
        response_model=MaterialVersionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def relocate_material_copy(
        material_version_id: str,
        request: Request,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> MaterialVersionResponse:
        item, created = await _receive_material_copy(
            request=request,
            service=service,
            relocate_version_id=material_version_id,
        )
        if not created:
            response.status_code = status.HTTP_200_OK
        return MaterialVersionResponse.from_domain(item)

    @router.post(
        "/materials/{material_version_id}/parse",
        response_model=MaterialUnitListResponse,
    )
    def parse_material_version(
        material_version_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> MaterialUnitListResponse:
        try:
            units = service.parse_material_version(material_version_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return MaterialUnitListResponse(
            material_version_id=material_version_id,
            items=[MaterialUnitResponse.from_domain(item) for item in units],
        )

    @router.post(
        "/materials/{material_version_id}/parse-job",
        response_model=MaterialParseJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def start_material_parse_job(
        material_version_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
        manager: JobManager = Depends(get_teaching_prep_job_manager),
    ) -> MaterialParseJobResponse:
        try:
            material = service.get_material_version(material_version_id)
            with _MATERIAL_PARSE_SUBMIT_LOCK:
                job = _active_material_parse_job(manager, material.id)
                if job is None:
                    job = manager.submit(
                        _MATERIAL_PARSE_JOB_TYPE,
                        {"material_version_id": material.id},
                    )
        except Exception as exc:
            raise _api_error(exc) from exc
        return _material_parse_job_response(job)

    @router.get(
        "/material-parse-jobs",
        response_model=MaterialParseJobListResponse,
    )
    def list_material_parse_jobs(
        manager: JobManager = Depends(get_teaching_prep_job_manager),
    ) -> MaterialParseJobListResponse:
        return MaterialParseJobListResponse(
            items=[
                _material_parse_job_response(job)
                for job in _latest_material_parse_jobs(manager)
            ]
        )

    @router.get(
        "/materials/{material_version_id}/units",
        response_model=MaterialUnitListResponse,
    )
    def list_material_units(
        material_version_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> MaterialUnitListResponse:
        try:
            units = service.list_material_units(material_version_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return MaterialUnitListResponse(
            material_version_id=material_version_id,
            items=[MaterialUnitResponse.from_domain(item) for item in units],
        )

    @router.get(
        "/material-units/{unit_id}",
        response_model=MaterialUnitResponse,
    )
    def get_material_unit(
        unit_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> MaterialUnitResponse:
        try:
            unit = service.get_material_unit(unit_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return MaterialUnitResponse.from_domain(unit)

    @router.patch(
        "/material-units/{unit_id}",
        response_model=MaterialUnitResponse,
    )
    def update_material_unit(
        unit_id: str,
        payload: UpdateMaterialUnitRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> MaterialUnitResponse:
        try:
            unit = service.update_material_unit_label(
                unit_id,
                expected_revision=payload.expected_revision,
                title=payload.title,
                manual_text=payload.manual_text,
                formula_review_required=payload.formula_review_required,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return MaterialUnitResponse.from_domain(unit)

    @router.get("/material-units/{unit_id}/preview")
    def material_unit_preview(
        unit_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> FileResponse:
        try:
            preview_path = service.material_preview_path(unit_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return FileResponse(
            preview_path,
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=120"},
        )

    @router.post(
        "/material-units/{unit_id}/preview-render",
        response_model=MaterialUnitResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def request_material_unit_preview_render(
        unit_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> MaterialUnitResponse:
        try:
            unit = service.request_pptx_preview_render(unit_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return MaterialUnitResponse.from_domain(unit)

    @router.get(
        "/lessons/{lesson_node_id}/material-links",
        response_model=MaterialLinkListResponse,
    )
    def list_material_links(
        lesson_node_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> MaterialLinkListResponse:
        try:
            links = service.list_material_links(lesson_node_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return MaterialLinkListResponse(
            lesson_node_id=lesson_node_id,
            items=[MaterialLinkResponse.from_domain(item) for item in links],
        )

    @router.post(
        "/lessons/{lesson_node_id}/material-links",
        response_model=MaterialLinkResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_material_link(
        lesson_node_id: str,
        payload: CreateMaterialLinkRequest,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> MaterialLinkResponse:
        try:
            link, created = service.create_material_link(
                request_token=payload.request_token,
                lesson_node_id=lesson_node_id,
                material_version_id=payload.material_version_id,
                start_unit=payload.start_unit,
                end_unit=payload.end_unit,
                crop=(
                    payload.crop.model_dump()
                    if payload.crop is not None
                    else None
                ),
                purpose=payload.purpose,
                teacher_note=payload.teacher_note,
                confirmation_status=payload.confirmation_status,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return MaterialLinkResponse.from_domain(link)

    @router.patch(
        "/material-links/{link_id}",
        response_model=MaterialLinkResponse,
    )
    def update_material_link(
        link_id: str,
        payload: UpdateMaterialLinkRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> MaterialLinkResponse:
        try:
            link = service.update_material_link(
                link_id,
                expected_revision=payload.expected_revision,
                start_unit=payload.start_unit,
                end_unit=payload.end_unit,
                crop=(
                    payload.crop.model_dump()
                    if payload.crop is not None
                    else None
                ),
                purpose=payload.purpose,
                teacher_note=payload.teacher_note,
                confirmation_status=payload.confirmation_status,
                is_active=payload.is_active,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return MaterialLinkResponse.from_domain(link)

    @router.get(
        "/lessons/{lesson_node_id}/exercise-candidates",
        response_model=ExerciseCandidateListResponse,
    )
    def list_exercise_candidates(
        lesson_node_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ExerciseCandidateListResponse:
        try:
            items = service.list_exercise_candidates(lesson_node_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return ExerciseCandidateListResponse(
            lesson_node_id=lesson_node_id,
            items=[
                ExerciseCandidateResponse.from_domain(item)
                for item in items
            ],
        )

    @router.post(
        "/lessons/{lesson_node_id}/exercise-candidates",
        response_model=ExerciseCandidateResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_exercise_candidate(
        lesson_node_id: str,
        payload: CreateExerciseCandidateRequest,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ExerciseCandidateResponse:
        try:
            item, created = service.create_exercise_candidate(
                request_token=payload.request_token,
                lesson_node_id=lesson_node_id,
                question_number=payload.question_number,
                content_label=payload.content_label,
                difficulty=payload.difficulty,
                classroom_use=payload.classroom_use,
                estimated_minutes=payload.estimated_minutes,
                teaching_focus=payload.teaching_focus,
                teacher_note=payload.teacher_note,
                selection_status=payload.selection_status,
                answer_status=payload.answer_status,
                question_regions=[
                    region.model_dump()
                    for region in payload.question_regions
                ],
                answer_regions=[
                    region.model_dump()
                    for region in payload.answer_regions
                ],
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return ExerciseCandidateResponse.from_domain(item)

    @router.patch(
        "/exercise-candidates/{candidate_id}",
        response_model=ExerciseCandidateResponse,
    )
    def update_exercise_candidate(
        candidate_id: str,
        payload: UpdateExerciseCandidateRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ExerciseCandidateResponse:
        try:
            item = service.update_exercise_candidate(
                candidate_id,
                expected_revision=payload.expected_revision,
                question_number=payload.question_number,
                content_label=payload.content_label,
                difficulty=payload.difficulty,
                classroom_use=payload.classroom_use,
                estimated_minutes=payload.estimated_minutes,
                teaching_focus=payload.teaching_focus,
                teacher_note=payload.teacher_note,
                selection_status=payload.selection_status,
                answer_status=payload.answer_status,
                question_regions=[
                    region.model_dump()
                    for region in payload.question_regions
                ],
                answer_regions=[
                    region.model_dump()
                    for region in payload.answer_regions
                ],
                is_active=payload.is_active,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return ExerciseCandidateResponse.from_domain(item)

    @router.get("/exercise-regions/{region_id}/preview")
    def exercise_region_preview(
        region_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> FileResponse:
        try:
            preview_path = service.exercise_region_preview_path(region_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return FileResponse(
            preview_path,
            media_type="image/png",
            headers={"Cache-Control": "private, no-store"},
        )

    @router.get(
        "/evidence/assessments",
        response_model=AssessmentChoiceListResponse,
    )
    def list_available_assessments(
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> AssessmentChoiceListResponse:
        try:
            items = service.list_available_assessments()
        except Exception as exc:
            raise _api_error(exc) from exc
        return AssessmentChoiceListResponse(
            items=[
                AssessmentChoiceResponse.model_validate(item)
                for item in items
            ]
        )

    @router.get(
        "/evidence/questions",
        response_model=QuestionEvidenceChoiceListResponse,
    )
    def list_available_questions(
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> QuestionEvidenceChoiceListResponse:
        try:
            items = service.list_available_questions()
        except Exception as exc:
            raise _api_error(exc) from exc
        return QuestionEvidenceChoiceListResponse(
            items=[
                QuestionEvidenceChoiceResponse.model_validate(item)
                for item in items
            ]
        )

    @router.get(
        "/lessons/{lesson_node_id}/resource-packs/status",
        response_model=ResourcePackStatusResponse,
    )
    def resource_pack_status(
        lesson_node_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ResourcePackStatusResponse:
        try:
            payload = service.resource_pack_status(lesson_node_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return ResourcePackStatusResponse.model_validate(payload)

    @router.get(
        "/lessons/{lesson_node_id}/resource-packs",
        response_model=ResourcePackListResponse,
    )
    def list_resource_packs(
        lesson_node_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ResourcePackListResponse:
        try:
            items = service.list_resource_packs(lesson_node_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return ResourcePackListResponse(
            lesson_node_id=lesson_node_id,
            items=[ResourcePackResponse.from_domain(item) for item in items],
        )

    @router.post(
        "/lessons/{lesson_node_id}/resource-packs",
        response_model=ResourcePackResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def freeze_resource_pack(
        lesson_node_id: str,
        payload: FreezeResourcePackRequest,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ResourcePackResponse:
        try:
            item, created = service.freeze_resource_pack(
                request_token=payload.request_token,
                lesson_node_id=lesson_node_id,
                class_name=payload.class_name,
                lesson_type=payload.lesson_type,
                teacher_context=payload.teacher_context,
                reference_ppt_intents=payload.reference_ppt_intents,
                question_ids=payload.question_ids,
                assessment_ids=payload.assessment_ids,
                knowledge_scope=payload.knowledge_scope,
                preparation_preferences=payload.preparation_preferences.model_dump(
                    mode="python"
                ),
                selected_material_link_ids=payload.selected_material_link_ids,
                selected_exercise_candidate_ids=(
                    payload.selected_exercise_candidate_ids
                ),
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return ResourcePackResponse.from_domain(item)

    @router.get(
        "/resource-packs/{pack_id}",
        response_model=ResourcePackResponse,
    )
    def get_resource_pack(
        pack_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ResourcePackResponse:
        try:
            item = service.get_resource_pack(pack_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return ResourcePackResponse.from_domain(item)

    @router.get("/resource-packs/{pack_id}/manifest")
    def export_resource_pack_manifest(
        pack_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> JSONResponse:
        try:
            item = service.get_resource_pack(pack_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return JSONResponse(
            content=ResourcePackResponse.from_domain(item).model_dump(
                mode="json"
            ),
            headers={
                "Content-Disposition": (
                    f'attachment; filename="resource-pack-v'
                    f'{item.version_number}.json"'
                ),
                "Cache-Control": "private, no-store",
            },
        )

    @router.get(
        "/resource-packs/{pack_id}/draft-preflight",
        response_model=LessonDraftPreflightResponse,
    )
    def lesson_draft_preflight(
        pack_id: str,
        mode: str = Query(default="local_template"),
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> LessonDraftPreflightResponse:
        try:
            payload = service.lesson_draft_preflight(pack_id, mode=mode)
        except Exception as exc:
            raise _api_error(exc) from exc
        return LessonDraftPreflightResponse.model_validate(payload)

    @router.get(
        "/resource-packs/{pack_id}/lesson-drafts",
        response_model=LessonDraftListResponse,
    )
    def list_lesson_drafts(
        pack_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> LessonDraftListResponse:
        try:
            items = service.list_lesson_drafts(pack_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return LessonDraftListResponse(
            resource_pack_id=pack_id,
            items=[LessonDraftResponse.from_domain(item) for item in items],
        )

    @router.post(
        "/resource-packs/{pack_id}/lesson-drafts",
        response_model=LessonDraftResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def generate_lesson_draft(
        pack_id: str,
        payload: GenerateLessonDraftRequest,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> LessonDraftResponse:
        try:
            item, created = service.generate_lesson_draft(
                pack_id,
                operation_id=payload.operation_id,
                mode=payload.mode,
                confirmed=payload.confirmed,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return LessonDraftResponse.from_domain(item)

    @router.post(
        "/lesson-draft-generations/{operation_id}/cancel",
        response_model=LessonDraftGenerationCancellationResponse,
    )
    def cancel_lesson_draft_generation(
        operation_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> LessonDraftGenerationCancellationResponse:
        try:
            clean_id, newly_cancelled = (
                service.cancel_lesson_draft_generation(operation_id)
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return LessonDraftGenerationCancellationResponse(
            operation_id=clean_id,
            status="cancelled",
            newly_cancelled=newly_cancelled,
        )

    @router.get(
        "/lesson-drafts/{draft_id}",
        response_model=LessonDraftResponse,
    )
    def get_lesson_draft(
        draft_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> LessonDraftResponse:
        try:
            item = service.get_lesson_draft(draft_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return LessonDraftResponse.from_domain(item)

    @router.post(
        "/lesson-drafts/{draft_id}/revisions",
        response_model=LessonDraftResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def revise_lesson_draft(
        draft_id: str,
        payload: ReviseLessonDraftRequest,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> LessonDraftResponse:
        try:
            item, created = service.revise_lesson_draft(
                draft_id,
                request_token=payload.request_token,
                payload=payload.payload,
                confirmed=payload.confirmed,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return LessonDraftResponse.from_domain(item)

    @router.get(
        "/lesson-drafts/{draft_id}/slide-plans",
        response_model=SlidePlanListResponse,
    )
    def list_slide_plans(
        draft_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SlidePlanListResponse:
        try:
            items = service.list_slide_plans(draft_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return SlidePlanListResponse(
            lesson_draft_id=draft_id,
            items=[SlidePlanResponse.from_domain(item) for item in items],
        )

    @router.post(
        "/lesson-drafts/{draft_id}/slide-plans",
        response_model=SlidePlanResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_slide_plan(
        draft_id: str,
        payload: CreateSlidePlanRequest,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SlidePlanResponse:
        try:
            item, created = service.create_slide_plan(
                draft_id,
                request_token=payload.request_token,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return SlidePlanResponse.from_domain(item)

    @router.get(
        "/slide-plans/{plan_id}",
        response_model=SlidePlanResponse,
    )
    def get_slide_plan(
        plan_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SlidePlanResponse:
        try:
            item = service.get_slide_plan(plan_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return SlidePlanResponse.from_domain(item)

    @router.post(
        "/slide-plans/{plan_id}/revisions",
        response_model=SlidePlanResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def revise_slide_plan(
        plan_id: str,
        payload: ReviseSlidePlanRequest,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SlidePlanResponse:
        try:
            item, created = service.revise_slide_plan(
                plan_id,
                request_token=payload.request_token,
                operation_reviews=[
                    review.model_dump()
                    for review in payload.operation_reviews
                ],
                approve_low_risk_deletions=(
                    payload.approve_low_risk_deletions
                ),
                review_note=payload.review_note,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return SlidePlanResponse.from_domain(item)

    @router.get(
        "/slide-plans/{plan_id}/preview",
        response_model=SlidePlanPreviewResponse,
    )
    def slide_plan_preview(
        plan_id: str,
        include_proposed: bool = Query(default=True),
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SlidePlanPreviewResponse:
        try:
            payload = service.slide_plan_preview(
                plan_id,
                include_proposed=include_proposed,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return SlidePlanPreviewResponse.model_validate(payload)

    @router.get("/slide-plans/{plan_id}/checklist")
    def export_slide_plan_checklist(
        plan_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> JSONResponse:
        try:
            item = service.get_slide_plan(plan_id)
            preview = service.slide_plan_preview(
                plan_id,
                include_proposed=True,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return JSONResponse(
            content={
                "plan": SlidePlanResponse.from_domain(item).model_dump(
                    mode="json"
                ),
                "preview": preview,
            },
            headers={
                "Content-Disposition": (
                    f'attachment; filename="slide-plan-v'
                    f'{item.version_number}.json"'
                ),
                "Cache-Control": "private, no-store",
            },
        )

    @router.get(
        "/slide-plans/{plan_id}/executions",
        response_model=PptxExecutionListResponse,
    )
    def list_pptx_executions(
        plan_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> PptxExecutionListResponse:
        try:
            items = service.list_pptx_executions(plan_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return PptxExecutionListResponse(
            slide_plan_id=plan_id,
            items=[
                PptxExecutionResponse.from_domain(item) for item in items
            ],
        )

    @router.post(
        "/slide-plans/{plan_id}/executions",
        response_model=PptxExecutionResultResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def execute_slide_plan(
        plan_id: str,
        payload: ExecuteSlidePlanRequest,
        response: Response,
        background_tasks: BackgroundTasks,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> PptxExecutionResultResponse:
        try:
            execution, created = service.start_pptx_execution(
                plan_id,
                operation_id=payload.operation_id,
                confirmed=payload.confirmed,
                preview_only=payload.preview_only,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        else:
            background_tasks.add_task(
                service.process_pptx_execution,
                execution.id,
                not payload.preview_only,
            )
        return PptxExecutionResultResponse(
            execution=PptxExecutionResponse.from_domain(execution),
            version=None,
        )

    @router.get(
        "/pptx-executions/{run_id}",
        response_model=PptxExecutionResponse,
    )
    def get_pptx_execution(
        run_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> PptxExecutionResponse:
        try:
            item = service.get_pptx_execution(run_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return PptxExecutionResponse.from_domain(item)

    @router.get("/pptx-executions/{run_id}/preview")
    def pptx_execution_preview(
        run_id: str,
        slide: int = Query(default=1, ge=1),
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> FileResponse:
        try:
            preview = service.pptx_execution_preview_path(
                run_id, slide_number=slide
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return FileResponse(
            preview,
            media_type="image/png",
            headers={"Cache-Control": "private, no-store"},
        )

    @router.post(
        "/pptx-executions/{run_id}/confirm-preview",
        response_model=PptxExecutionResultResponse,
    )
    def confirm_pptx_preview(
        run_id: str,
        payload: ConfirmPptxPreviewRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> PptxExecutionResultResponse:
        try:
            execution, version = service.confirm_pptx_preview(
                run_id,
                confirmed=payload.confirmed,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return PptxExecutionResultResponse(
            execution=PptxExecutionResponse.from_domain(execution),
            version=(
                PptxVersionResponse.from_domain(version)
                if version is not None
                else None
            ),
        )

    @router.get(
        "/pptx-executions/{run_id}/performance",
        response_model=LessonGenerationPerformanceResponse,
    )
    def get_lesson_generation_performance(
        run_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> LessonGenerationPerformanceResponse:
        try:
            item = service.get_lesson_generation_performance(run_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return LessonGenerationPerformanceResponse.from_domain(item)

    @router.post(
        "/pptx-executions/{run_id}/cancel",
        response_model=PptxExecutionResponse,
    )
    def cancel_pptx_execution(
        run_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> PptxExecutionResponse:
        try:
            item = service.cancel_pptx_execution(run_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return PptxExecutionResponse.from_domain(item)

    @router.post(
        "/pptx-executions/{run_id}/recover",
        response_model=PptxExecutionResultResponse,
    )
    def recover_pptx_execution(
        run_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> PptxExecutionResultResponse:
        try:
            execution, version = service.recover_pptx_execution(run_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return PptxExecutionResultResponse(
            execution=PptxExecutionResponse.from_domain(execution),
            version=(
                PptxVersionResponse.from_domain(version)
                if version is not None
                else None
            ),
        )

    @router.post(
        "/pptx-executions/{run_id}/discard-staging",
        response_model=PptxExecutionResponse,
    )
    def discard_pptx_staging(
        run_id: str,
        payload: DiscardPptxStagingRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> PptxExecutionResponse:
        if not payload.confirmed:
            raise _new_api_error(
                422,
                "teaching_prep_validation_error",
                "Teaching preparation request is invalid",
            )
        try:
            item = service.discard_pptx_staging(run_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return PptxExecutionResponse.from_domain(item)

    @router.get("/pptx-versions/{version_id}/download")
    def download_pptx_version(
        version_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> FileResponse:
        try:
            path, filename = service.pptx_download(version_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return FileResponse(
            path,
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "presentationml.presentation"
            ),
            filename=filename,
            headers={"Cache-Control": "private, no-store"},
        )

    @router.post(
        "/resource-packs/{base_pack_id}/class-variants",
        response_model=ClassVariantResultResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def derive_class_variant(
        base_pack_id: str,
        payload: DeriveClassVariantRequest,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ClassVariantResultResponse:
        try:
            variant, pack, created = service.derive_class_variant(
                base_pack_id,
                request_token=payload.request_token,
                class_name=payload.class_name,
                teacher_context=payload.teacher_context,
                assessment_ids=payload.assessment_ids,
                knowledge_scope=payload.knowledge_scope,
                prior_review_ids=payload.prior_review_ids,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return ClassVariantResultResponse(
            variant=ClassVariantResponse.from_domain(variant),
            resource_pack=ResourcePackResponse.from_domain(pack),
        )

    @router.get(
        "/lessons/{lesson_node_id}/class-variants",
        response_model=ClassVariantListResponse,
    )
    def list_class_variants(
        lesson_node_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ClassVariantListResponse:
        try:
            items = service.list_class_variants(lesson_node_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return ClassVariantListResponse(
            items=[ClassVariantResponse.from_domain(item) for item in items]
        )

    @router.post(
        "/pptx-versions/{version_id}/up-class-package",
        response_model=UpClassPackageResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_up_class_package(
        version_id: str,
        payload: CreateUpClassPackageRequest,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> UpClassPackageResponse:
        try:
            item, created = service.create_up_class_package(
                version_id,
                request_token=payload.request_token,
                confirmed=payload.confirmed,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return UpClassPackageResponse.from_domain(item)

    @router.get(
        "/lessons/{lesson_node_id}/up-class-packages",
        response_model=UpClassPackageListResponse,
    )
    def list_up_class_packages(
        lesson_node_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> UpClassPackageListResponse:
        try:
            items = service.list_up_class_packages(lesson_node_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return UpClassPackageListResponse(
            items=[UpClassPackageResponse.from_domain(item) for item in items]
        )

    @router.get("/up-class-packages/{package_id}/download")
    def download_up_class_package(
        package_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> FileResponse:
        try:
            path, filename = service.up_class_package_download(package_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return FileResponse(
            path,
            media_type="application/zip",
            filename=filename,
            headers={"Cache-Control": "private, no-store"},
        )

    @router.post(
        "/up-class-packages/{package_id}/recover",
        response_model=UpClassPackageResponse,
    )
    def recover_up_class_package(
        package_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> UpClassPackageResponse:
        try:
            item = service.recover_up_class_package(package_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return UpClassPackageResponse.from_domain(item)

    @router.post(
        "/up-class-packages/{package_id}/discard-staging",
        response_model=UpClassPackageResponse,
    )
    def discard_up_class_package_staging(
        package_id: str,
        payload: DiscardPptxStagingRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> UpClassPackageResponse:
        if not payload.confirmed:
            raise _new_api_error(
                422,
                "teaching_prep_validation_error",
                "Teaching preparation request is invalid",
            )
        try:
            item = service.discard_up_class_package_staging(package_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return UpClassPackageResponse.from_domain(item)

    @router.post(
        "/up-class-packages/{package_id}/activate",
        response_model=UpClassPackageResponse,
    )
    def activate_up_class_package(
        package_id: str,
        payload: ActivateUpClassPackageRequest,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> UpClassPackageResponse:
        try:
            item, created = service.activate_up_class_package(
                package_id,
                request_token=payload.request_token,
                confirmed=payload.confirmed,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return UpClassPackageResponse.from_domain(item)

    @router.post(
        "/up-class-packages/{package_id}/reviews",
        response_model=PostLessonReviewResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_post_lesson_review(
        package_id: str,
        payload: CreatePostLessonReviewRequest,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> PostLessonReviewResponse:
        try:
            item, created = service.create_post_lesson_review(
                package_id,
                request_token=payload.request_token,
                timing=payload.timing,
                question_outcome=payload.question_outcome,
                reteach_points=payload.reteach_points,
                next_action=payload.next_action,
                note=payload.note,
                use_in_next_version=payload.use_in_next_version,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return PostLessonReviewResponse.from_domain(item)

    @router.get(
        "/lessons/{lesson_node_id}/post-lesson-reviews",
        response_model=PostLessonReviewListResponse,
    )
    def list_post_lesson_reviews(
        lesson_node_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> PostLessonReviewListResponse:
        try:
            items = service.list_post_lesson_reviews(lesson_node_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return PostLessonReviewListResponse(
            items=[
                PostLessonReviewResponse.from_domain(item) for item in items
            ]
        )

    @router.get(
        "/semesters/{semester_id}/lesson-preparation-statuses",
        response_model=LessonPreparationStatusListResponse,
    )
    def list_lesson_preparation_statuses(
        semester_id: str,
        request: Request,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> LessonPreparationStatusListResponse:
        try:
            workspace_ai_tasks = getattr(
                request.app.state,
                "workspace_ai_task_service",
                None,
            )
            ai_tasks = (
                workspace_ai_tasks.list_actionable_module_tasks("teaching_prep")
                if workspace_ai_tasks is not None
                else ()
            )
            items = service.list_lesson_preparation_statuses(
                semester_id,
                ai_tasks=ai_tasks,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return LessonPreparationStatusListResponse(
            semester_id=semester_id,
            items=[
                LessonPreparationStatusResponse.model_validate(item)
                for item in items
            ],
        )

    @router.post(
        "/ai-handoffs/{handoff_id}/adopt",
        response_model=TeachingPrepAIAdoptionResponse,
    )
    def adopt_workspace_ai_handoff(
        handoff_id: str,
        payload: AdoptWorkspaceAIHandoffRequest,
        request: Request,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> TeachingPrepAIAdoptionResponse:
        try:
            coordinator = getattr(
                request.app.state,
                "workspace_ai_task_service",
                None,
            )
            if coordinator is None:
                raise TeachingPrepStateError(
                    "workspace AI task coordinator is unavailable"
                )
            command = (
                payload.command.model_dump()
                if payload.command is not None
                else None
            )
            with bind_adoption_command(command):
                result = coordinator.adopt(
                    handoff_id,
                    module="teaching_prep",
                    draft_revision=payload.draft_revision,
                    target_revision=payload.target_revision,
                )
            adopted = service.find_workspace_ai_adoption(result.adoption_id)
            if adopted is None:
                raise TeachingPrepStateError(
                    "teaching-prep adoption receipt is unavailable"
                )
            return TeachingPrepAIAdoptionResponse.from_domain(adopted)
        except Exception as exc:
            raise _api_error(exc) from exc

    @router.get(
        "/ai-adoptions/{adoption_id}",
        response_model=TeachingPrepAIAdoptionResponse,
    )
    def get_workspace_ai_adoption(
        adoption_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> TeachingPrepAIAdoptionResponse:
        try:
            adopted = service.find_workspace_ai_adoption(adoption_id)
            if adopted is None:
                raise TeachingPrepNotFoundError(
                    "teaching-prep AI adoption was not found"
                )
            return TeachingPrepAIAdoptionResponse.from_domain(adopted)
        except Exception as exc:
            raise _api_error(exc) from exc

    @router.patch(
        "/semester-mapping-proposals/{proposal_id}/mappings/{mapping_id}",
        response_model=SemesterMappingProposalResponse,
    )
    def review_semester_mapping_row(
        proposal_id: str,
        mapping_id: str,
        payload: ReviewSemesterMappingRowRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SemesterMappingProposalResponse:
        try:
            item = service.review_semester_mapping_row(
                proposal_id,
                mapping_id,
                expected_revision=payload.expected_revision,
                decision=payload.model_dump(
                    exclude={"expected_revision"}, exclude_none=True
                ),
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return SemesterMappingProposalResponse.from_domain(item)

    @router.post(
        "/semester-mapping-proposals/{proposal_id}/accept-local-high-confidence",
        response_model=SemesterMappingProposalResponse,
    )
    def accept_local_reference_ppt_mappings(
        proposal_id: str,
        payload: ApplySemesterMappingProposalRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SemesterMappingProposalResponse:
        try:
            item = service.accept_local_reference_ppt_mappings(
                proposal_id,
                expected_revision=payload.expected_revision,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return SemesterMappingProposalResponse.from_domain(item)

    @router.post(
        "/semester-mapping-proposals/{proposal_id}/recompute-page-ranges",
        response_model=SemesterMappingProposalResponse,
    )
    def recompute_semester_mapping_page_ranges(
        proposal_id: str,
        payload: RejectSemesterMappingProposalRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SemesterMappingProposalResponse:
        try:
            item = service.recompute_semester_mapping_page_ranges(
                proposal_id,
                expected_revision=payload.expected_revision,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return SemesterMappingProposalResponse.from_domain(item)

    @router.post(
        "/semester-mapping-proposals/{proposal_id}/reject",
        response_model=SemesterMappingProposalResponse,
    )
    def reject_semester_mapping_proposal(
        proposal_id: str,
        payload: RejectSemesterMappingProposalRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SemesterMappingProposalResponse:
        try:
            item = service.reject_semester_mapping_proposal(
                proposal_id,
                expected_revision=payload.expected_revision,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return SemesterMappingProposalResponse.from_domain(item)

    @router.post(
        "/lessons/{lesson_node_id}/reference-selection-preflight",
        response_model=ReferenceSelectionPreflightResponse,
    )
    def reference_selection_preflight(
        lesson_node_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ReferenceSelectionPreflightResponse:
        try:
            payload = service.reference_selection_preflight(lesson_node_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return ReferenceSelectionPreflightResponse.model_validate(payload)

    @router.put(
        "/lessons/{lesson_node_id}/reference-selection-draft",
        response_model=ReferenceSelectionDraftResponse,
    )
    def save_reference_selection_draft(
        lesson_node_id: str,
        payload: SaveReferenceSelectionDraftRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ReferenceSelectionDraftResponse:
        try:
            item = service.save_reference_selection_draft(
                lesson_node_id,
                expected_revision=payload.expected_revision,
                source_state_sha256=payload.source_state_sha256,
                selection=payload.selection,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return ReferenceSelectionDraftResponse.from_domain(item)

    @router.post(
        "/lessons/{lesson_node_id}/reference-selection-snapshots",
        response_model=ReferenceSelectionSnapshotResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def freeze_reference_selection_snapshot(
        lesson_node_id: str,
        payload: FreezeReferenceSelectionSnapshotRequest,
        response: Response,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ReferenceSelectionSnapshotResponse:
        try:
            item, created = service.freeze_reference_selection_snapshot(
                lesson_node_id,
                request_token=payload.request_token,
                expected_draft_revision=payload.expected_draft_revision,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if not created:
            response.status_code = status.HTTP_200_OK
        return ReferenceSelectionSnapshotResponse.from_domain(item)

    @router.post(
        "/reference-selection-snapshots/{snapshot_id}/exercise-suggestion-preflight",
        response_model=ExerciseSuggestionPreflightResponse,
    )
    def exercise_suggestion_preflight(
        snapshot_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ExerciseSuggestionPreflightResponse:
        try:
            payload = service.exercise_suggestion_preflight(snapshot_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return ExerciseSuggestionPreflightResponse.model_validate(payload)

    @router.post(
        "/reference-selection-snapshots/{snapshot_id}/exercise-suggestion-runs",
        response_model=ExerciseSuggestionRunResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def start_exercise_suggestion_run(
        snapshot_id: str,
        payload: StartExerciseSuggestionRunRequest,
        background_tasks: BackgroundTasks,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ExerciseSuggestionRunResponse:
        try:
            run, created = service.start_exercise_suggestion_run(
                snapshot_id,
                operation_id=payload.operation_id,
                confirmed=payload.confirmed,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if created:
            background_tasks.add_task(
                service.process_exercise_suggestion_run, run.id
            )
        return ExerciseSuggestionRunResponse.from_domain(run)

    @router.get(
        "/exercise-suggestion-runs/{run_id}",
        response_model=ExerciseSuggestionRunResponse,
    )
    def get_exercise_suggestion_run(
        run_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ExerciseSuggestionRunResponse:
        try:
            run, items = service.get_exercise_suggestion_run(run_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return ExerciseSuggestionRunResponse.from_domain(run, items)

    @router.get(
        "/lessons/{lesson_node_id}/latest-exercise-suggestion-run",
        response_model=ExerciseSuggestionRunResponse,
    )
    def get_latest_exercise_suggestion_run(
        lesson_node_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ExerciseSuggestionRunResponse:
        try:
            run, items = service.get_latest_exercise_suggestion_run(
                lesson_node_id
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return ExerciseSuggestionRunResponse.from_domain(run, items)

    @router.post(
        "/exercise-suggestion-runs/{run_id}/cancel",
        response_model=ExerciseSuggestionRunResponse,
    )
    def cancel_exercise_suggestion_run(
        run_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ExerciseSuggestionRunResponse:
        try:
            run = service.cancel_exercise_suggestion_run(run_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return ExerciseSuggestionRunResponse.from_domain(run)

    @router.patch(
        "/exercise-suggestions/{suggestion_id}",
        response_model=ExerciseSuggestionResponse,
    )
    def review_exercise_suggestion(
        suggestion_id: str,
        payload: ReviewExerciseSuggestionRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ExerciseSuggestionResponse:
        try:
            item = service.review_exercise_suggestion(
                suggestion_id,
                expected_revision=payload.expected_revision,
                decision=payload.decision,
                teacher_payload=payload.teacher_payload,
                rejection_reason=payload.rejection_reason,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return ExerciseSuggestionResponse.from_domain(item)

    @router.post(
        "/lessons/{lesson_node_id}/resource-pack-preflight",
        response_model=dict[str, object],
    )
    def resource_pack_selection_preflight(
        lesson_node_id: str,
        payload: ResourcePackSelectionPreflightRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> dict[str, object]:
        try:
            return service.resource_pack_preflight(
                lesson_node_id,
                reference_ppt_intents=payload.reference_ppt_intents,
                selected_material_link_ids=payload.selected_material_link_ids,
                selected_exercise_candidate_ids=(
                    payload.selected_exercise_candidate_ids
                ),
            )
        except Exception as exc:
            raise _api_error(exc) from exc

    @router.post(
        "/lesson-drafts/{draft_id}/capacity-preview",
        response_model=dict[str, object],
    )
    def preview_lesson_draft_capacity(
        draft_id: str,
        payload: LessonDraftCapacityPreviewRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> dict[str, object]:
        try:
            return service.preview_lesson_draft_capacity(
                draft_id, payload=payload.payload
            )
        except Exception as exc:
            raise _api_error(exc) from exc

    @router.get(
        "/lessons/{lesson_node_id}/pptx-versions",
        response_model=PptxVersionListResponse,
    )
    def list_lesson_pptx_versions(
        lesson_node_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> PptxVersionListResponse:
        try:
            items = service.list_lesson_pptx_versions(lesson_node_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        payloads: list[PptxVersionListItemResponse] = []
        for item in items:
            payload = PptxVersionResponse.from_domain(item["version"]).model_dump()
            payload.update(
                {
                    "is_current": item["is_current"],
                    "current_revision": item["current_revision"],
                    "file_verified": item["file_verified"],
                    "preview_url": item["preview_url"],
                }
            )
            payloads.append(PptxVersionListItemResponse.model_validate(payload))
        return PptxVersionListResponse(
            lesson_node_id=lesson_node_id, items=payloads
        )

    @router.post(
        "/pptx-versions/{version_id}/activate",
        response_model=ActivatePptxVersionResponse,
    )
    def activate_pptx_version(
        version_id: str,
        payload: ActivatePptxVersionRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> ActivatePptxVersionResponse:
        try:
            version, revision, changed = service.activate_pptx_version(
                version_id, expected_revision=payload.expected_revision
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return ActivatePptxVersionResponse(
            version=PptxVersionResponse.from_domain(version),
            current_revision=revision,
            changed=changed,
        )

    @router.get("/pptx-versions/{version_id}/preview")
    def pptx_version_preview(
        version_id: str,
        slide: int = Query(default=1, ge=1),
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> FileResponse:
        try:
            preview = service.pptx_version_preview_path(
                version_id, slide_number=slide
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return FileResponse(
            preview,
            media_type="image/png",
            headers={"Cache-Control": "private, no-store"},
        )

    @router.get(
        "/lessons/{lesson_node_id}/slide-animation-runs",
        response_model=SlideAnimationRunListResponse,
    )
    def list_slide_animation_runs(
        lesson_node_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SlideAnimationRunListResponse:
        try:
            payload = service.list_slide_animation_runs(lesson_node_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return SlideAnimationRunListResponse(
            lesson_node_id=str(payload["lesson_node_id"]),
            items=[
                SlideAnimationRunResponse.from_domain(item)
                for item in payload["items"]
            ],
            billed_count=int(payload["billed_count"]),
            billed_limit=int(payload["billed_limit"]),
            page_limit=int(payload["page_limit"]),
        )

    @router.post(
        "/lessons/{lesson_node_id}/slide-animation-runs",
        response_model=SlideAnimationRunResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def start_slide_animation_run(
        lesson_node_id: str,
        payload: StartSlideAnimationRunRequest,
        background_tasks: BackgroundTasks,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SlideAnimationRunResponse:
        try:
            run, created = service.start_slide_animation_run(
                lesson_node_id,
                operation_id=payload.operation_id,
                confirmed=payload.confirmed,
                material_link_id=payload.material_link_id,
                page_indexes=payload.page_indexes,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        if created:
            background_tasks.add_task(
                service.process_slide_animation_run, run.id
            )
        return SlideAnimationRunResponse.from_domain(run)

    @router.get(
        "/slide-animation-runs/{run_id}",
        response_model=SlideAnimationRunResponse,
    )
    def get_slide_animation_run(
        run_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SlideAnimationRunResponse:
        try:
            run = service.get_slide_animation_run(run_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return SlideAnimationRunResponse.from_domain(run)

    @router.post(
        "/slide-animation-runs/{run_id}/cancel",
        response_model=SlideAnimationRunResponse,
    )
    def cancel_slide_animation_run(
        run_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SlideAnimationRunResponse:
        try:
            run = service.cancel_slide_animation_run(run_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return SlideAnimationRunResponse.from_domain(run)

    @router.post(
        "/slide-animation-runs/{run_id}/accept",
        response_model=SlideAnimationRunResponse,
    )
    def accept_slide_animation_run(
        run_id: str,
        payload: DecideSlideAnimationRunRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SlideAnimationRunResponse:
        try:
            run = service.accept_slide_animation_run(
                run_id,
                expected_revision=payload.expected_revision,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return SlideAnimationRunResponse.from_domain(run)

    @router.post(
        "/slide-animation-runs/{run_id}/discard",
        response_model=SlideAnimationRunResponse,
    )
    def discard_slide_animation_run(
        run_id: str,
        payload: DecideSlideAnimationRunRequest,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> SlideAnimationRunResponse:
        try:
            run = service.discard_slide_animation_run(
                run_id,
                expected_revision=payload.expected_revision,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return SlideAnimationRunResponse.from_domain(run)

    @router.get("/slide-animation-runs/{run_id}/preview")
    def preview_slide_animation_run(
        run_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> FileResponse:
        try:
            path = service.slide_animation_preview_path(run_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return FileResponse(
            path,
            media_type="text/html; charset=utf-8",
            headers=_animation_html_headers(),
        )

    @router.get("/slide-animation-runs/{run_id}/download")
    def download_slide_animation_run(
        run_id: str,
        service: TeachingPrepService = Depends(get_teaching_prep_service),
    ) -> FileResponse:
        try:
            path = service.slide_animation_download_path(run_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        filename = quote("课堂动画.html")
        headers = _animation_html_headers()
        headers["Content-Disposition"] = (
            "attachment; filename=\"classroom-animation.html\"; "
            f"filename*=UTF-8''{filename}"
        )
        return FileResponse(
            path,
            media_type="text/html; charset=utf-8",
            headers=headers,
        )

    return router


async def _receive_material_copy(
    *,
    request: Request,
    service: TeachingPrepService,
    relocate_version_id: str | None,
):
    filename = unquote(str(request.headers.get("x-upload-filename") or ""))
    display_name = unquote(
        str(request.headers.get("x-display-name") or "")
    ).strip()
    request_token = str(request.headers.get("x-request-token") or "")
    try:
        declared_size = int(request.headers.get("content-length") or 0)
        modified_ms = int(request.headers.get("x-file-modified-ms") or 0)
    except ValueError as exc:
        raise _api_error(
            TeachingPrepValidationError("material upload metadata is invalid")
        ) from exc
    if declared_size > _MAX_MATERIAL_UPLOAD_BYTES:
        raise _new_api_error(
            413,
            "teaching_prep_material_too_large",
            "Teaching material exceeds the 256 MB limit",
        )
    suffix = Path(filename).suffix.casefold()
    upload = (
        service.paths["temp"] / f"material-upload-{uuid4().hex}{suffix}.part"
    )
    received = 0
    try:
        with upload.open("xb") as handle:
            async for chunk in request.stream():
                received += len(chunk)
                if received > _MAX_MATERIAL_UPLOAD_BYTES:
                    raise _new_api_error(
                        413,
                        "teaching_prep_material_too_large",
                        "Teaching material exceeds the 256 MB limit",
                    )
                handle.write(chunk)
        try:
            return service.import_material_copy(
                request_token=request_token,
                staged_path=upload,
                original_filename=filename,
                display_name=display_name or Path(filename).stem,
                modified_ns=modified_ms * 1_000_000 if modified_ms > 0 else None,
                relocate_version_id=relocate_version_id,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
    finally:
        upload.unlink(missing_ok=True)


def _api_error(exc: Exception) -> Exception:
    if isinstance(exc, HandoffNotFoundError):
        return _new_api_error(
            404,
            "workspace_ai_handoff_not_found",
            "Workspace AI handoff was not found",
        )
    if isinstance(exc, WorkspaceAIRevisionConflictError):
        return _new_api_error(
            409,
            "workspace_ai_revision_conflict",
            "Workspace AI proposal or target changed; refresh and review again",
        )
    if isinstance(exc, WorkspaceAITaskError):
        return _new_api_error(
            422,
            "workspace_ai_task_invalid",
            "Workspace AI adoption request is invalid",
        )
    if isinstance(exc, TeachingPrepNotFoundError):
        return _new_api_error(
            404,
            "teaching_prep_not_found",
            "Teaching preparation record was not found",
        )
    if isinstance(exc, TeachingPrepRetryAvailableError):
        return _new_api_error(
            409,
            "semester_mapping_retry_available",
            "Semester mapping did not complete; submit again to start a new proposal",
        )
    if isinstance(exc, TeachingPrepConflictError):
        return _new_api_error(
            409,
            "teaching_prep_conflict",
            "Teaching preparation record changed; refresh and try again",
        )
    if isinstance(exc, TeachingPrepStateError):
        return _new_api_error(
            409,
            "teaching_prep_state_conflict",
            "Teaching preparation state does not allow this change",
        )
    if isinstance(exc, TeachingPrepValidationError):
        return _new_api_error(
            422,
            "teaching_prep_validation_error",
            "Teaching preparation request is invalid",
        )
    return _new_api_error(
        500,
        "teaching_prep_internal_error",
        "Teaching preparation request could not be completed",
    )


def _animation_html_headers() -> dict[str, str]:
    return {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": (
            "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
        ),
    }


def _new_api_error(
    status_code: int,
    code: str,
    message: str,
) -> Exception:
    # Import lazily: importing the application module while workspace features
    # are being discovered would recursively load this router.
    from backend.api.app import ApiError

    return ApiError(status_code, code, message)
