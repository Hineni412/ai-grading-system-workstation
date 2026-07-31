from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr


class VaultStatusResponse(BaseModel):
    initialized: bool
    locked: bool
    idle_timeout_seconds: int
    retry_after_seconds: int
    format_version: int


class InitializeRequest(BaseModel):
    password: SecretStr
    operation_id: str = Field(min_length=8, max_length=128)


class InitializeResponse(BaseModel):
    session_token: str
    recovery_key: str
    recovery_key_shown_once: bool
    idle_timeout_seconds: int


class UnlockRequest(BaseModel):
    password: SecretStr


class SessionResponse(BaseModel):
    session_token: str
    idle_timeout_seconds: int
    recovery_key: str | None = None


class RecoverRequest(BaseModel):
    recovery_key: SecretStr
    new_password: SecretStr
    operation_id: str = Field(min_length=8, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: SecretStr
    new_password: SecretStr
    operation_id: str = Field(min_length=8, max_length=128)


class BackupCreateRequest(BaseModel):
    backup_password: SecretStr
    operation_id: str = Field(min_length=8, max_length=128)


class BackupCreateResponse(BaseModel):
    backup_id: str
    file_name: str
    created_at: str
    size_bytes: int
    source_instance_id: str


class BackupListItem(BaseModel):
    backup_id: str
    file_name: str
    created_at: str
    size_bytes: int
    status: str


class BackupListResponse(BaseModel):
    items: list[BackupListItem]


class BackupSecretRequest(BaseModel):
    file_name: str
    secret: SecretStr
    secret_kind: Literal["password", "recovery_key"]


class BackupSummaryResponse(BaseModel):
    backup_id: str
    source_instance_id: str
    created_at: str
    format_version: int
    scope: str


class RestorePreviewResponse(BackupSummaryResponse):
    preview_token: str
    expires_in_seconds: int
    requires_complete_replacement: bool


class RestoreConfirmRequest(BaseModel):
    preview_token: str = Field(min_length=16, max_length=256)
    operation_id: str = Field(min_length=8, max_length=128)
    confirmation_phrase: str


class RestoreConfirmResponse(BaseModel):
    restored: bool
    backup_id: str
    locked: bool


class OperationResponse(BaseModel):
    completed: bool = True
    locked: bool | None = None


class TouchResponse(BaseModel):
    active: bool
    idle_timeout_seconds: int


__all__ = [
    "BackupCreateRequest",
    "BackupCreateResponse",
    "BackupListItem",
    "BackupListResponse",
    "BackupSecretRequest",
    "BackupSummaryResponse",
    "ChangePasswordRequest",
    "InitializeRequest",
    "InitializeResponse",
    "OperationResponse",
    "RecoverRequest",
    "RestoreConfirmRequest",
    "RestoreConfirmResponse",
    "RestorePreviewResponse",
    "SessionResponse",
    "TouchResponse",
    "UnlockRequest",
    "VaultStatusResponse",
]
