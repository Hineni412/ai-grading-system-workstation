from __future__ import annotations


class VaultError(RuntimeError):
    """A safe, user-facing vault failure without sensitive context."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = int(status_code)
        self.details = dict(details or {})


class VaultIntegrityError(VaultError):
    def __init__(self) -> None:
        super().__init__(
            "vault_integrity_error",
            "受保护数据未通过完整性校验，现有数据没有改变",
            status_code=409,
        )


__all__ = ["VaultError", "VaultIntegrityError"]
