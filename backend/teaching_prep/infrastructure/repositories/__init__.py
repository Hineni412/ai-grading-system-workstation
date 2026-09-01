from .catalog import TeachingCatalogRepository
from .material_units import MaterialPreviewRecord, MaterialUnitRepository
from .lesson_drafts import LessonDraftRepository
from .preparations import LessonPreparationRepository
from .preferences import TeachingPreferencesRepository
from .resource_packs import ResourcePackRepository
from .semesters import SemesterWorkspaceRepository
from .semester_mapping import SemesterMappingRepository
from .slide_plans import SlidePlanRepository
from .pptx_outputs import PptxLocalOutputRepository
from .workbench_iteration import WorkbenchIterationRepository
from .workspace_ai_adoptions import WorkspaceAIAdoptionRepository
from .slide_animations import SlideAnimationRepository
from .adaptation_traces import AdaptationTraceRepository

__all__ = [
    "LessonPreparationRepository",
    "TeachingPreferencesRepository",
    "LessonDraftRepository",
    "MaterialUnitRepository",
    "MaterialPreviewRecord",
    "ResourcePackRepository",
    "SemesterWorkspaceRepository",
    "SemesterMappingRepository",
    "SlidePlanRepository",
    "PptxLocalOutputRepository",
    "WorkbenchIterationRepository",
    "TeachingCatalogRepository",
    "WorkspaceAIAdoptionRepository",
    "SlideAnimationRepository",
    "AdaptationTraceRepository",
]
