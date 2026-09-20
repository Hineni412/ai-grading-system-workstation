from __future__ import annotations

from pypinyin import Style, lazy_pinyin


def student_name_initials(name: object) -> str:
    return "".join(
        lazy_pinyin(
            str(name or "").strip(),
            style=Style.FIRST_LETTER,
            strict=False,
        )
    ).casefold()


def student_name_pinyin(name: object) -> str:
    return "".join(
        lazy_pinyin(
            str(name or "").strip(),
            strict=False,
        )
    ).casefold()
