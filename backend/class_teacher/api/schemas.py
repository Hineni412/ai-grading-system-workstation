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


class OperationResponse(BaseModel):
    completed: bool = True
    locked: bool | None = None


class TouchResponse(BaseModel):
    active: bool
    idle_timeout_seconds: int
    session_expires_in_seconds: int
    status_observed_at: str


__all__ = [
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
    "SessionResponse",
    "TouchResponse",
    "UnlockRequest",
    "VaultStatusResponse",
]
