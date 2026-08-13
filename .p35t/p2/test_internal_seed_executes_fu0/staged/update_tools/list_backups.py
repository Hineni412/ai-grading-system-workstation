"""列出已有备份工具。

用法:
    python update_tools/list_backups.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from backup_core import list_backups


def main() -> int:
    print("=" * 70)
    print("  AI 阅卷系统 — 已有备份列表")
    print("=" * 70)

    backups = list_backups()
    if not backups:
        print("\n  没有找到任何备份文件。")
        print("  运行 python update_tools/backup_data.py 创建备份。")
        print("\n" + "=" * 70)
        return 0

    print(f"\n  共 {len(backups)} 个备份:\n")
    print(f"  {'序号':>4}  {'时间':<20}  {'原因':<15}  {'大小':>10}  文件名")
    print(f"  {'----':>4}  {'----':<20}  {'----':<15}  {'----':>10}  ----")

    for idx, backup in enumerate(backups, 1):
        print(
            f"  {idx:>4}  {backup['time']:<20}  {backup['reason']:<15}  "
            f"{backup['size_display']:>10}  {backup['filename']}"
        )

    print("\n  恢复备份:")
    print(f"  python update_tools/restore_backup.py \"{backups[0]['filename']}\"")
    print("\n" + "=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
