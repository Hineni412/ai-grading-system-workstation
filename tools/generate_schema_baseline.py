"""Compatibility tombstone for the retired historical baseline generator.

P3-11 makes migration files the only schema authority.  Regenerating migration
000 from a current database would silently fold later migrations into history
and invalidate every installed database's recorded checksums, so this command
must never rewrite migration files.
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "已停用：历史 000 基线迁移不可重新生成。"
        "数据库结构变更必须新增顺序迁移文件。",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
