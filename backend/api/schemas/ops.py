from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


CheckStatus = Literal["ok", "warning", "error"]


class _OpsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpsDirectoryCheck(_OpsModel):
    key: str
    exists: bool
    writable: bool
    status: CheckStatus


class OpsDatabaseCheck(_OpsModel):
    key: str
    exists: bool
    size_bytes: int = Field(ge=0)
    integrity: Literal["ok", "missing", "unavailable"]
    migration_version: str
    pending_migrations: int = Field(ge=0)
    status: CheckStatus


class OpsToolCheck(_OpsModel):
    key: str
    available: bool
    status: Literal["ok", "warning"]


class OpsSelfCheckResponse(_OpsModel):
    version: str
    status: CheckStatus
    api_configured: bool
    directories: list[OpsDirectoryCheck]
    databases: list[OpsDatabaseCheck]
    tools: list[OpsToolCheck]
    warnings: list[str]


class OpsBackupItem(_OpsModel):
    kind: Literal["zip", "database"]
    filename: str
    created_at: str
    reason: str
    size_bytes: int = Field(ge=0)


class OpsBackupListResponse(_OpsModel):
    items: list[OpsBackupItem]
    returned: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
