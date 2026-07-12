from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class OpsOperation(str, Enum):
    BACKUP = "backup"
    RESTORE = "restore"
    MIGRATION = "migration"
    TRANSFER_IMPORT = "transfer_import"
    TRANSFER_EXPORT = "transfer_export"


@dataclass(frozen=True, slots=True)
class OpsInternalPlan:
    operation: OpsOperation
    parameters: dict[str, Any]
    resource_fingerprint: str
    summary: dict[str, Any]
    created_monotonic: float


__all__ = ["OpsInternalPlan", "OpsOperation"]
