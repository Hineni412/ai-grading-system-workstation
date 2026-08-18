"""班主任学生稳定学籍标识（roster ref）。

稳定标识直接引用当前成绩库花名册的学籍信息，取代旧的 HMAC 临时编号：
- 格式为 ``{班级}|{学号}``；学号为空时为 ``{班级}|{姓名}``，例如 ``9班|40``。
- 组成部分中的 ``\\`` 与 ``|`` 分别转义为 ``\\\\`` 与 ``\\|``，保证解析无歧义。
- 该标识不是秘密，可以出现在接口、草稿和 URL 中（姓名仍不得进入 URL，
  有学号时标识只含班级标签与学号；学号缺失的兼容情形不进 URL 场景由调用方控制）。

旧格式为 64 位小写十六进制临时编号，只在兼容输入路径上被识别并按
「引用已失效」处理，不再参与任何生成或解析。
"""

from __future__ import annotations

import hashlib
import json
import re

_SEPARATOR = "|"
_ESCAPE = "\\"

_LEGACY_OPAQUE_REF = re.compile(r"[0-9a-f]{64}")

# 学生引用要么是稳定学籍标识（班级|学号/姓名），要么是内部主体编号；
# 两者都是不含控制字符的短文本。
SUBJECT_REF_PATTERN = re.compile(r"[^\x00-\x1f]{1,240}")

# 与 backend/workspaces/ai_tasks/service.py 的 _SAFE_REF 保持一致：
# 共同 AI Task 元数据只接受这个字符集内的引用。
_TASK_SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}")


def _escape(value: str) -> str:
    return value.replace(_ESCAPE, _ESCAPE * 2).replace(
        _SEPARATOR, _ESCAPE + _SEPARATOR
    )


def student_stable_ref(
    *,
    class_label: str | None,
    student_code: str | None,
    display_name: str,
) -> str:
    """Return the stable identity: ``class|student_code`` or ``class|name``."""
    class_part = str(class_label or "").strip()
    key = str(student_code or "").strip() or str(display_name or "").strip()
    return f"{_escape(class_part)}{_SEPARATOR}{_escape(key)}"


def parse_stable_ref(value: str) -> tuple[str, str] | None:
    """Split a stable ref into ``(class_label, key)``; ``None`` when malformed."""
    text = str(value or "")
    parts: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == _ESCAPE:
            index += 1
            if index >= len(text) or text[index] not in {_ESCAPE, _SEPARATOR}:
                return None
            current.append(text[index])
        elif char == _SEPARATOR:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    parts.append("".join(current))
    if len(parts) != 2 or not parts[1]:
        return None
    return parts[0], parts[1]


def is_legacy_opaque_ref(value: str) -> bool:
    """兼容输入识别：旧格式 64 位十六进制临时编号。"""
    return _LEGACY_OPAQUE_REF.fullmatch(str(value or "")) is not None


def student_content_revision(
    *,
    source_key: str,
    student_code: str,
    display_name: str,
    class_label: str,
) -> str:
    """Content-hash revision used for optimistic locking on roster rows."""
    return hashlib.sha256(json.dumps(
        [source_key, student_code, display_name, class_label],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def legacy_opaque_ref(vmk: bytes, source_key: str) -> str:
    """旧格式临时编号，仅供真实数据修复工具识别兼容输入，不再用于生成。"""
    import hmac

    return hmac.new(
        vmk,
        f"class-teacher|roster-ref|{source_key}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def task_safe_ref_id(value: str) -> str:
    """共同 AI Task 只保存不透明引用：稳定学籍标识跨界时以确定性哈希存储。

    已符合共同层引用格式的值原样保留（含旧任务里的历史引用），
    稳定标识（含中文与 ``|``）映射为 ``r`` 前缀的 SHA-256 十六进制。
    共同层只按相等性比对引用，不解释内容；业务库始终是稳定标识的唯一事实来源。
    """
    text = str(value or "")
    if _TASK_SAFE_REF.fullmatch(text):
        return text
    return "r" + hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "SUBJECT_REF_PATTERN",
    "is_legacy_opaque_ref",
    "legacy_opaque_ref",
    "parse_stable_ref",
    "student_content_revision",
    "student_stable_ref",
    "task_safe_ref_id",
]
