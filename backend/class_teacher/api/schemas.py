from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr


class VaultStatusResponse(BaseModel):
    initialized: bool
    locked: bool
    idle_timeout_seconds: int
    retry_after_seconds: int
    format_version: int
    protection_mode: Literal[
        "uninitialized",
        "legacy_password_v1",
        "pin_dpapi_current_user_v2",
        "plaintext_debug_v1",
        "legacy_migration_required",
    ]
    protection_state: Literal["pending", "active"] | None = None
    legacy_upgrade_available: bool = False
    session_expires_in_seconds: int = 0
    status_observed_at: str
    lock_reason: str | None = None


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


class PinInitializeRequest(BaseModel):
    pin: SecretStr
    operation_id: str = Field(min_length=8, max_length=128)


class PinUnlockRequest(BaseModel):
    pin: SecretStr


class PinRecoverRequest(BaseModel):
    recovery_key: SecretStr
    new_pin: SecretStr
    operation_id: str = Field(min_length=8, max_length=128)


class PinUpgradeRequest(BaseModel):
    current_password: SecretStr
    new_pin: SecretStr
    operation_id: str = Field(min_length=8, max_length=128)


class PinChangeRequest(BaseModel):
    current_pin: SecretStr
    new_pin: SecretStr
    operation_id: str = Field(min_length=8, max_length=128)


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
    source_relation: Literal["same_instance", "other_instance"]
    backup_schema_version: int
    current_schema_version: int
    migration_required: bool
    backup_scope_counts: dict[str, int]
    current_scope_counts: dict[str, int]
    mode: Literal["complete_replace"]
    will_replace_current: bool
    will_lock_after_confirm: bool
    confirmation_phrase: str


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
    session_expires_in_seconds: int
    status_observed_at: str


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
    "PinInitializeRequest",
    "PinChangeRequest",
    "PinRecoverRequest",
    "PinUpgradeRequest",
    "PinUnlockRequest",
    "RecoverRequest",
    "RestoreConfirmRequest",
    "RestoreConfirmResponse",
    "RestorePreviewResponse",
    "SessionResponse",
    "TouchResponse",
    "UnlockRequest",
    "VaultStatusResponse",
]
