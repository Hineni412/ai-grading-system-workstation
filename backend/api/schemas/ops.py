from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

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


class OpsImportUploadResponse(_OpsModel):
    upload_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    filename: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class OpsBackupPreflightRequest(_OpsModel):
    operation: Literal["backup"]
    reason: Literal[
        "before_exam",
        "before_update",
        "before_import",
        "before_restore",
        "manual",
        "after_exam",
    ]
    scopes: list[Literal["grading"]] = Field(
        default_factory=lambda: ["grading"],
        min_length=1,
        max_length=2,
    )


class OpsRestorePreflightRequest(_OpsModel):
    operation: Literal["restore"]
    backup_filename: str = Field(pattern=r"^backup_[A-Za-z0-9._-]+\.zip$")


class OpsMigrationPreflightRequest(_OpsModel):
    operation: Literal["migration"]
    target: Literal["grading", "question_bank", "all"]


class OpsTransferImportPreflightRequest(_OpsModel):
    operation: Literal["transfer_import"]
    upload_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class OpsTransferExportPreflightRequest(_OpsModel):
    operation: Literal["transfer_export"]
    scope: Literal["lean", "full"]


OpsPreflightRequest = Annotated[
    Union[
        OpsBackupPreflightRequest,
        OpsRestorePreflightRequest,
        OpsMigrationPreflightRequest,
        OpsTransferImportPreflightRequest,
        OpsTransferExportPreflightRequest,
    ],
    Field(discriminator="operation"),
]


class OpsPreflightSummary(_OpsModel):
    file_count: int | None = Field(default=None, ge=0)
    total_size_bytes: int | None = Field(default=None, ge=0)
    total_expanded_bytes: int | None = Field(default=None, ge=0)
    database_count: int | None = Field(default=None, ge=0)
    skipped_count: int | None = Field(default=None, ge=0)
    sensitive_skipped_count: int | None = Field(default=None, ge=0)
    target: Literal["grading", "question_bank", "all"] | None = None
    pending_migrations: int | None = Field(default=None, ge=0)
    applied_in_preview: int | None = Field(default=None, ge=0)
    integrity: Literal["ok"] | None = None
    scope: Literal["lean", "full"] | None = None
    warnings: list[str]


class OpsPreflightResponse(_OpsModel):
    operation: Literal[
        "backup", "restore", "migration", "transfer_import", "transfer_export"
    ]
    confirmation_token: str
    expires_at: datetime
    requires_restart: bool
    summary: OpsPreflightSummary


class OpsJobSubmitRequest(_OpsModel):
    confirmation_token: str = Field(min_length=1, max_length=512)


class OpsOperationRecovery(_OpsModel):
    code: str
    backup_filename: str | None = None


class OpsOperationResponse(_OpsModel):
    operation_id: str
    operation: Literal[
        "backup", "restore", "migration", "transfer_import", "transfer_export"
    ]
    status: Literal[
        "prepared",
        "restart_required",
        "applying",
        "applied",
        "rolled_back",
        "failed",
        "cancelled",
    ]
    result_code: str
    created_at: str
    updated_at: str
    recovery: OpsOperationRecovery
