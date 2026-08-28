from __future__ import annotations

from typing import Iterable


# 成绩表常见变体列 → 归一展示名。归一并发生在读取层，不改库存数据。
_VARIANT_DISPLAY = {
    "英语笔试加听力": "英语",
    "英语笔试加听说": "英语",
}

# 始终不进入展示口径的原始列：听说单项已含在变体总分里，不单独成科。
_ALWAYS_HIDDEN = {"英语听说"}


def hidden_subjects(names: Iterable[str]) -> set[str]:
    """归一后不进入任何展示口径的原始列名。

    「英语听说」始终隐藏；同场存在变体列（带听说总分）时，原始「英语」列
    （笔试单项）也隐藏，避免与变体并列重复。
    """
    unique = list(dict.fromkeys(str(name) for name in names))
    hidden = {name for name in unique if name in _ALWAYS_HIDDEN}
    if any(name in _VARIANT_DISPLAY for name in unique) and "英语" in unique:
        hidden.add("英语")
    return hidden


def canonical_subjects(names: Iterable[str]) -> dict[str, str]:
    """原始科目名 → 归一展示名，只包含可见列；隐藏列不在映射中。

    变体列（如「英语笔试加听力」）并入「英语」；同场原始「英语」列与
    「英语听说」被隐藏（见 hidden_subjects）；只有原始「英语」时保持「英语」。
    其余名字原样保留。
    """
    unique = list(dict.fromkeys(str(name) for name in names))
    hidden = hidden_subjects(unique)
    return {
        name: _VARIANT_DISPLAY.get(name, name)
        for name in unique
        if name not in hidden
    }


__all__ = ["canonical_subjects", "hidden_subjects"]
