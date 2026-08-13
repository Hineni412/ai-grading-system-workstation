"""恢复备份工具 — 从 zip 备份恢复 user_data 和 config。

用法:
    python update_tools/restore_backup.py <备份文件名或路径>
    python update_tools/restore_backup.py <备份文件名> --dry-run
    python update_tools/restore_backup.py <备份文件名> --skip-pre-backup
    python update_tools/restore_backup.py --list

安全机制:
    - 恢复前会自动备份当前数据
    - 需要用户输入 "YES" 二次确认
    - 不会覆盖 app/ 代码文件
    - 所有操作写入 logs/backup.log
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from backup_core import _backup_root, list_backups, restore_backup


def _resolve_zip_path(name_or_path: str) -> Path:
    """尝试解析备份文件路径。"""
    # 直接路径
    p = Path(name_or_path)
    if p.exists():
        return p
    # 在备份目录下查找
    backup_dir = _backup_root()
    candidate = backup_dir / name_or_path
    if candidate.exists():
        return candidate
    # 尝试加 .zip 后缀
    if not name_or_path.endswith(".zip"):
        candidate = backup_dir / f"{name_or_path}.zip"
        if candidate.exists():
            return candidate
    return p  # 返回原始路径，让后续报错


def _print_backup_list() -> None:
    """打印备份列表。"""
    backups = list_backups()
    if not backups:
        print("  没有找到任何备份文件。")
        return
    print(f"  共 {len(backups)} 个备份:\n")
    for idx, backup in enumerate(backups, 1):
        print(f"  {idx:>3}. [{backup['time']}] {backup['filename']}  ({backup['size_display']}, {backup['reason']})")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AI 阅卷系统 — 备份恢复工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python update_tools/restore_backup.py backup_20260524_120000_manual.zip\n"
            "  python update_tools/restore_backup.py backup_20260524_120000_manual.zip --dry-run\n"
            "  python update_tools/restore_backup.py --list\n"
        ),
    )
    parser.add_argument(
        "backup_file",
        nargs="?",
        default=None,
        help="备份文件名或完整路径",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_backups",
        help="列出所有备份后退出",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出会恢复的文件，不实际操作",
    )
    parser.add_argument(
        "--skip-pre-backup",
        action="store_true",
        help="跳过恢复前的自动备份（不推荐）",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="跳过二次确认（脚本调用时使用）",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  AI 阅卷系统 — 备份恢复")
    print("=" * 60)

    if args.list_backups:
        _print_backup_list()
        print("\n" + "=" * 60)
        return 0

    if not args.backup_file:
        print("\n  请指定要恢复的备份文件。\n")
        print("  已有备份:")
        _print_backup_list()
        print("\n  用法: python update_tools/restore_backup.py <备份文件名>")
        print("\n" + "=" * 60)
        return 1

    zip_path = _resolve_zip_path(args.backup_file)
    if not zip_path.exists():
        print(f"\n  [ERROR] 备份文件不存在: {zip_path}")
        print("\n  已有备份:")
        _print_backup_list()
        print("\n" + "=" * 60)
        return 1

    print(f"\n  备份文件:  {zip_path.name}")
    print(f"  文件大小:  {zip_path.stat().st_size / (1024*1024):.1f} MB")
    print(f"  完整路径:  {zip_path}")

    if args.dry_run:
        print("\n  [DRY-RUN] 模式：不会修改任何文件\n")

    # 二次确认
    if not args.dry_run and not args.yes:
        print("\n  [WARNING] 恢复操作将用备份文件覆盖当前 user_data/ 和 config/ 中的同名文件！")
        if not args.skip_pre_backup:
            print("  系统会先自动备份当前数据再恢复。")
        print()
        try:
            confirm = input("  确认恢复？请输入 YES: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  已取消。")
            return 1
        if confirm != "YES":
            print("  输入不是 YES，已取消恢复。")
            return 1

    result = restore_backup(
        zip_path,
        skip_pre_backup=args.skip_pre_backup,
        dry_run=args.dry_run,
    )

    if result.get("error"):
        print(f"\n  [ERROR] {result['error']}")
        return 1

    if result.get("pre_backup_zip"):
        print(f"\n  恢复前备份:  {result['pre_backup_zip']}")

    restored_count = len(result.get("restored_files", []))
    skipped_count = len(result.get("skipped_files", []))

    if args.dry_run:
        print(f"\n  [DRY-RUN] 将恢复 {restored_count} 个文件")
        if result["restored_files"]:
            for f in result["restored_files"][:30]:
                print(f"    {f}")
            if restored_count > 30:
                print(f"    ... 还有 {restored_count - 30} 个文件")
    else:
        print(f"\n  恢复文件:  {restored_count} 个")
        if skipped_count:
            print(f"  跳过文件:  {skipped_count} 个")
            for f in result["skipped_files"]:
                print(f"    - {f}")
        print("\n  [OK] 恢复完成!")

    print("\n" + "=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
