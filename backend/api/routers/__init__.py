from .ai_diagnostics import router as ai_diagnostics_router
from .analytics import router as analytics_router
from .assembly import router as assembly_router
from .config import router as config_router
from .files import router as files_router
from .grading import router as grading_router
from .graph import router as graph_router
from .jobs import router as jobs_router
from .media import router as media_router
from .model_profiles import router as model_profiles_router
from .ops import router as ops_router
from .question_bank import router as question_bank_router
from .reports import router as reports_router
from .review import router as review_router
from .scan import router as scan_router
from .sessions import router as sessions_router
from .students import router as students_router
from .templates import router as templates_router
from .training import router as training_router
from .workbench import router as workbench_router

__all__ = [
    "ai_diagnostics_router",
    "analytics_router",
    "assembly_router",
    "config_router",
    "files_router",
    "grading_router",
    "graph_router",
    "jobs_router",
    "media_router",
    "model_profiles_router",
    "ops_router",
    "question_bank_router",
    "reports_router",
    "review_router",
    "scan_router",
    "sessions_router",
    "students_router",
    "templates_router",
    "training_router",
    "workbench_router",
]
