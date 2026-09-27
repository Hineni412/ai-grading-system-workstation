"""命题练习（教师命题作品）模块：拆解与改编练习的作品、版本与母题快照。"""

from question_bank.authoring.service import (
    ADAPT_QUESTION_TYPES,
    SOLO_LEVELS,
    WORK_KINDS,
    AuthoringError,
    AuthoringNotFound,
    AuthoringService,
    AuthoringSourceNotFound,
    AuthoringStorageUnavailable,
    AuthoringTokenConflict,
    AuthoringValidationError,
    AuthoringVersionConflict,
    normalize_content,
)
from question_bank.authoring.task_cards import (
    TASK_CARDS,
    is_task_card_id,
    task_card_catalog,
)

__all__ = [
    "ADAPT_QUESTION_TYPES",
    "SOLO_LEVELS",
    "TASK_CARDS",
    "WORK_KINDS",
    "AuthoringError",
    "AuthoringNotFound",
    "AuthoringService",
    "AuthoringSourceNotFound",
    "AuthoringStorageUnavailable",
    "AuthoringTokenConflict",
    "AuthoringValidationError",
    "AuthoringVersionConflict",
    "is_task_card_id",
    "normalize_content",
    "task_card_catalog",
]
