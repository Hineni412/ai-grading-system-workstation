from __future__ import annotations

import re


_WINDOWS_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
_SEPARATOR_TRIM = re.compile(r"\s+")


def safe_filename_fragment(value: object, fallback: str) -> str:
    text = _SEPARATOR_TRIM.sub(" ", str(value or "").strip())
    text = _WINDOWS_INVALID_FILENAME_CHARS.sub("_", text)
    text = text.rstrip(". ")
    return text or fallback


def session_export_path_name(session_name: object, purpose: str, extension: str, timestamp: str) -> str:
    safe_session = safe_filename_fragment(session_name, "考试批改")
    safe_purpose = safe_filename_fragment(purpose, "导出")
    safe_timestamp = safe_filename_fragment(timestamp, "latest")
    suffix = str(extension or "").strip().lstrip(".") or "dat"
    return f"{safe_session}_{safe_purpose}_{safe_timestamp}.{suffix}"
