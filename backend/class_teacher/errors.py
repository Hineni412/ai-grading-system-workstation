from __future__ import annotations


class VaultError(RuntimeError):
    """A safe, user-facing class-teacher failure."""

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


def unsupported_database_format_error() -> VaultError:
    return VaultError(
        "class_teacher_database_format_unsupported",
        "班主任数据文件不是当前版本支持的格式，已停止读取和写入。"
        "请换用当前版本的明文数据；如需保留这个文件中的内容，"
        "请勿继续操作并联系维护人员。",
        status_code=409,
    )


__all__ = ["VaultError", "unsupported_database_format_error"]
