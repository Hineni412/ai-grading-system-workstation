"""备份工具 — 将 user_data 和 config 打包为 zip 备份。

用法:
    python update_tools/backup_data.py [--reason REASON] [--include-api-keys] [--include-logs] [--dry-run]

示例:
    python update_tools/backup_data.py --reason before_exam
    python update_tools/backup_data.py --reason before_update --include-api-keys
    python update_tools/backup_data.py --dry-run
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

from backup_core import VALID_REASONS, _format_bytes, create_backup


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AI 阅卷系统 — 数据备份工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "备份原因 (--reason):\n"
            "  before_exam    阅卷前备份\n"
            "  before_update  版本更新前备份\n"
            "  manual         手动备份（默认）\n"
            "  after_exam     阅卷后备份\n"
        ),
    )
    parser.add_argument(
        "--reason",
        choices=VALID_REASONS,
        default="manual",
        help="备份原因标签 (默认: manual)",
    )
    parser.add_argument(
        "--include-api-keys",
        action="store_true",
        help="包含 API Key 明文配置文件（默认不包含）",
    )
    parser.add_argument(
        "--include-logs",
        action="store_true",
        help="包含日志文件",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出会备份的文件，不实际创建 zip",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  AI 阅卷系统 — 数据备份")
    print("=" * 60)

    if args.dry_run:
        print("\n  [DRY-RUN] 模式：不会创建实际备份文件\n")

    result = create_backup(
        reason=args.reason,
        include_api_keys=args.include_api_keys,
        include_logs=args.include_logs,
        dry_run=args.dry_run,
    )

    if result.get("error"):
        print(f"\n  [ERROR] {result['error']}")
        return 1

    print(f"\n  备份原因:       {args.reason}")
    print(f"  文件数量:       {result['file_count']}")
    print(f"  原始大小:       {_format_bytes(result['total_size'])}")

    if result.get("skipped_sensitive"):
        print(f"  敏感文件跳过:   {len(result['skipped_sensitive'])} 个")
        for f in result["skipped_sensitive"]:
            print(f"    - {f}")

    if result.get("zip_path"):
        zip_size = Path(result["zip_path"]).stat().st_size
        print(f"  压缩后大小:     {_format_bytes(zip_size)}")
        print(f"  备份文件:       {result['zip_path']}")
        print("\n  [OK] 备份完成!")
    elif args.dry_run:
        print("\n  [DRY-RUN] 以上文件将被备份（未实际创建 zip）")
        if result["files"]:
            print(f"\n  文件列表 (共 {len(result['files'])} 个):")
            for f in result["files"][:50]:
                print(f"    {f}")
            if len(result["files"]) > 50:
                print(f"    ... 还有 {len(result['files']) - 50} 个文件")

    print("\n" + "=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
