from .config import router as config_router
from .files import router as files_router
from .grading import router as grading_router
from .graph import router as graph_router
from .jobs import router as jobs_router
from .media import router as media_router
from .question_bank import router as question_bank_router
from .reports import router as reports_router
from .review import router as review_router
from .scan import router as scan_router
from .sessions import router as sessions_router
from .students import router as students_router
from .templates import router as templates_router
from .training import router as training_router

__all__ = [
    "config_router",
    "files_router",
    "grading_router",
    "graph_router",
    "jobs_router",
    "media_router",
    "question_bank_router",
    "reports_router",
    "review_router",
    "scan_router",
    "sessions_router",
    "students_router",
    "templates_router",
    "training_router",
]
