"""增量更新应用工具 — 在工作机上执行更新包。

用法:
    python update_tools/apply_update.py <更新包目录> [目标目录]
    python update_tools/apply_update.py <更新包目录> [目标目录] --dry-run
    python update_tools/apply_update.py --rollback [目标目录]

执行流程:
    1. 读取 update_manifest.json 验证更新包
    2. 备份当前 user_data (zip)
    3. 备份当前代码到 app_backup_v旧版本/
    4. 从更新包 app/ 复制代码到项目根
    5. 从更新包 migrations/ 复制到项目 migrations/
    6. 执行 migrate_db.py 数据库迁移
    7. 输出更新结果

安全规则:
    - 不覆盖 user_data/databases/
    - 不覆盖 user_data/exams/
    - 不删除任何用户数据
    - 迁移失败时停止并提示回滚
    - 所有操作写入 logs/backup.log
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 确保必要路径在 sys.path
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── 日志 ──────────────────────────────────────────────

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"


def _get_logger(target_dir: Path) -> logging.Logger:
    logger = logging.getLogger("apply_update")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        log_dir = target_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / "backup.log", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(_LOG_FORMAT))
        logger.addHandler(fh)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter(_LOG_FORMAT))
        logger.addHandler(ch)
    return logger


# ── 排除规则 ──────────────────────────────────────────

_SKIP_DIRS = {"__pycache__", ".git", ".venv", ".mypy_cache"}
_SKIP_EXTENSIONS = {".pyc", ".pyo", ".tmp", ".log"}


def _should_skip(rel_path: Path) -> bool:
    for part in rel_path.parts:
        if part in _SKIP_DIRS:
            return True
    if rel_path.suffix.lower() in _SKIP_EXTENSIONS:
        return True
    return False


# ── 核心逻辑 ──────────────────────────────────────────

def apply_update(
    update_dir: Path,
    target_dir: Path,
    *,
    dry_run: bool = False,
) -> dict:
    """执行增量更新。

    Args:
        update_dir: 更新包目录
        target_dir: 工作机项目根目录
        dry_run: 只检查，不实际操作

    Returns:
        dict with result details
    """
    logger = _get_logger(target_dir)
    result = {
        "success": False,
        "old_version": "unknown",
        "new_version": "unknown",
        "code_backup_dir": None,
        "data_backup_zip": None,
        "files_updated": 0,
        "migrations_applied": 0,
        "error": None,
    }

    # ── 1. 验证更新包 ──
    manifest_path = update_dir / "update_manifest.json"
    if not manifest_path.exists():
        result["error"] = f"未找到 update_manifest.json: {manifest_path}"
        logger.error(result["error"])
        return result

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        result["error"] = f"读取 manifest 失败: {exc}"
        logger.error(result["error"])
        return result

    new_version = manifest.get("app_version", "unknown")
    result["new_version"] = new_version

    # 获取当前版本
    old_version_file = target_dir / "VERSION"
    if old_version_file.exists():
        result["old_version"] = old_version_file.read_text(encoding="utf-8").strip()
    else:
        result["old_version"] = "0.0.0"

    app_dir = update_dir / "app"
    if not app_dir.exists():
        result["error"] = "更新包中缺少 app/ 目录"
        logger.error(result["error"])
        return result

    logger.info(
        "开始更新: %s -> %s (目标: %s)",
        result["old_version"], new_version, target_dir,
    )

    if dry_run:
        logger.info("[DRY-RUN] 模式，不会修改任何文件")

    # ── 2. 备份当前数据 ──
    if not dry_run:
        logger.info("步骤 1/4: 备份当前数据...")
        try:
            sys.path.insert(0, str(target_dir / "update_tools"))
            from backup_core import create_backup
            bk_result = create_backup("before_update", include_api_keys=True)
            if bk_result.get("error"):
                logger.warning("数据备份失败(非致命): %s", bk_result["error"])
            else:
                result["data_backup_zip"] = bk_result.get("zip_path")
                logger.info("数据备份完成: %s", result["data_backup_zip"])
        except Exception as exc:
            logger.warning("数据备份跳过: %s", exc)

    # ── 3. 备份当前代码 ──
    if not dry_run:
        logger.info("步骤 2/4: 备份当前代码...")
        backup_name = f"app_backup_v{result['old_version']}"
        code_backup = target_dir / backup_name
        # 如果已有同名备份，加时间戳
        if code_backup.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"app_backup_v{result['old_version']}_{ts}"
            code_backup = target_dir / backup_name

        code_backup.mkdir(parents=True, exist_ok=True)
        result["code_backup_dir"] = str(code_backup)

        # 复制当前代码文件到 backup
        for item in sorted(app_dir.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(app_dir)
            if _should_skip(rel):
                continue
            # 对应的当前文件
            current_file = target_dir / rel
            if current_file.exists():
                dest = code_backup / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(current_file, dest)

        logger.info("代码备份到: %s", code_backup)

    # ── 4. 复制新代码 ──
    if not dry_run:
        logger.info("步骤 3/4: 复制新代码...")

    files_updated = 0
    for item in sorted(app_dir.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(app_dir)
        if _should_skip(rel):
            continue
        dest = target_dir / rel
        if dry_run:
            action = "覆盖" if dest.exists() else "新增"
            logger.info("[DRY-RUN] %s: %s", action, rel)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
        files_updated += 1

    result["files_updated"] = files_updated
    logger.info("%s %d 个代码文件", "将更新" if dry_run else "已更新", files_updated)

    # 复制 migrations/
    migrations_src = update_dir / "migrations"
    if migrations_src.exists():
        if dry_run:
            logger.info("[DRY-RUN] 将复制 migrations/ 目录")
        else:
            migrations_dst = target_dir / "migrations"
            if migrations_dst.exists():
                shutil.rmtree(migrations_dst)
            shutil.copytree(
                migrations_src, migrations_dst,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            logger.info("已复制 migrations/ 目录")

    # 复制 update_tools/（使用更新包中的更新工具覆盖旧版）
    ut_src = update_dir / "update_tools"
    if ut_src.exists():
        if dry_run:
            logger.info("[DRY-RUN] 将更新 update_tools/ 目录")
        else:
            ut_dst = target_dir / "update_tools"
            if ut_dst.exists():
                shutil.rmtree(ut_dst)
            shutil.copytree(
                ut_src, ut_dst,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            logger.info("已更新 update_tools/ 目录")

    # ── 5. 执行数据库迁移 ──
    if not dry_run:
        logger.info("步骤 4/4: 执行数据库迁移...")
        try:
            # 重新加载模块以使用新代码
            if "migrate_db" in sys.modules:
                del sys.modules["migrate_db"]
            sys.path.insert(0, str(target_dir / "update_tools"))
            from migrate_db import run_migrations

            migration_error = False
            for target_name in ("grading", "question_bank"):
                report = run_migrations(target_name)
                applied = [r for r in report.results if r.status == "applied"]
                if applied:
                    result["migrations_applied"] += len(applied)
                    logger.info("[%s] 成功执行 %d 个迁移", target_name, len(applied))
                if report.error:
                    logger.error("[%s] 迁移错误: %s", target_name, report.error)
                    migration_error = True
                    result["error"] = (
                        f"数据库迁移失败 ({target_name}): {report.error}\n"
                        f"代码已更新但迁移未完成。\n"
                        f"代码备份: {result['code_backup_dir']}\n"
                        f"数据备份: {result['data_backup_zip']}\n"
                        f"请手动处理或回滚。"
                    )
                    break

            if not migration_error:
                logger.info("数据库迁移完成，共 %d 个", result["migrations_applied"])
        except Exception as exc:
            logger.error("数据库迁移异常: %s", exc)
            result["error"] = (
                f"数据库迁移异常: {exc}\n"
                f"代码已更新但迁移未完成。\n"
                f"代码备份: {result['code_backup_dir']}"
            )
    else:
        logger.info("[DRY-RUN] 将执行数据库迁移")

    if not result.get("error"):
        result["success"] = True
        logger.info(
            "更新完成: %s -> %s, 更新 %d 个文件, 迁移 %d 个",
            result["old_version"], new_version,
            files_updated, result["migrations_applied"],
        )

    return result


# ── 回滚 ──────────────────────────────────────────────

def rollback(target_dir: Path, backup_name: str | None = None) -> dict:
    """回滚到上一个代码备份。"""
    logger = _get_logger(target_dir)
    result = {"success": False, "error": None, "restored_from": None}

    # 查找最新的 app_backup
    if backup_name:
        backup_dir = target_dir / backup_name
    else:
        backups = sorted(
            [d for d in target_dir.iterdir()
             if d.is_dir() and d.name.startswith("app_backup_v")],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        if not backups:
            result["error"] = "未找到任何代码备份（app_backup_v* 目录）"
            logger.error(result["error"])
            return result
        backup_dir = backups[0]

    if not backup_dir.exists():
        result["error"] = f"备份目录不存在: {backup_dir}"
        logger.error(result["error"])
        return result

    logger.info("开始回滚，从备份: %s", backup_dir.name)
    result["restored_from"] = str(backup_dir)

    # 复制备份文件回项目根
    count = 0
    for item in sorted(backup_dir.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(backup_dir)
        dest = target_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, dest)
        count += 1

    logger.info("已从备份恢复 %d 个文件", count)
    result["success"] = True
    return result


# ── CLI ───────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="AI 阅卷系统 — 增量更新应用工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python update_tools/apply_update.py AI阅卷系统_update_v1.1.0/ ./\n"
            "  python update_tools/apply_update.py AI阅卷系统_update_v1.1.0/ ./ --dry-run\n"
            "  python update_tools/apply_update.py --rollback\n"
            "  python update_tools/apply_update.py --rollback --backup-name app_backup_v1.0.0\n"
        ),
    )
    parser.add_argument(
        "update_dir",
        nargs="?",
        default=None,
        help="更新包目录路径",
    )
    parser.add_argument(
        "target_dir",
        nargs="?",
        default=None,
        help="工作机项目根目录（默认: 当前脚本的上级目录）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只检查，不实际操作")
    parser.add_argument("--rollback", action="store_true", help="回滚到上一个代码备份")
    parser.add_argument("--backup-name", default=None, help="指定回滚的备份目录名")
    args = parser.parse_args()

    target = Path(args.target_dir).resolve() if args.target_dir else _PROJECT_ROOT

    print("=" * 60)

    if args.rollback:
        print("  AI 阅卷系统 — 代码回滚")
        print("=" * 60)

        # 列出可用备份
        backups = sorted(
            [d for d in target.iterdir()
             if d.is_dir() and d.name.startswith("app_backup_v")],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        if not backups:
            print("\n  未找到任何代码备份。")
            return 1

        print("\n  可用的代码备份:")
        for i, b in enumerate(backups, 1):
            mtime = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            print(f"    {i}. {b.name}  ({mtime})")

        r = rollback(target, args.backup_name)
        if r["success"]:
            print(f"\n  [OK] 已从 {Path(r['restored_from']).name} 恢复代码")
        else:
            print(f"\n  [ERROR] {r['error']}")
            return 1
        print("\n" + "=" * 60)
        return 0

    # 正常更新流程
    print("  AI 阅卷系统 — 应用增量更新")
    print("=" * 60)

    if not args.update_dir:
        print("\n  请指定更新包目录路径。")
        print("  用法: python update_tools/apply_update.py <更新包目录> [目标目录]")
        return 1

    update_dir = Path(args.update_dir).resolve()
    if not update_dir.exists():
        print(f"\n  [ERROR] 更新包目录不存在: {update_dir}")
        return 1

    print(f"\n  更新包: {update_dir}")
    print(f"  目标:   {target}")
    if args.dry_run:
        print(f"  模式:   DRY-RUN（不修改文件）")

    r = apply_update(update_dir, target, dry_run=args.dry_run)

    print(f"\n  {'─' * 56}")
    print(f"  版本变更:   {r['old_version']} -> {r['new_version']}")
    print(f"  更新文件:   {r['files_updated']} 个")
    print(f"  数据库迁移: {r['migrations_applied']} 个")

    if r.get("code_backup_dir"):
        print(f"  代码备份:   {Path(r['code_backup_dir']).name}")
    if r.get("data_backup_zip"):
        print(f"  数据备份:   {Path(r['data_backup_zip']).name}")

    if r.get("error"):
        print(f"\n  [ERROR] {r['error']}")
        print(f"\n  回滚方法:")
        print(f"  python update_tools/apply_update.py --rollback")
        return 1

    if r["success"] and not args.dry_run:
        print(f"\n  [OK] 更新完成!")
    elif args.dry_run:
        print(f"\n  [DRY-RUN] 以上操作将在实际更新时执行")

    print("\n" + "=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
