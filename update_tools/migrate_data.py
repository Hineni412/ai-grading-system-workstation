"""数据迁移工具 — 将旧版数据目录迁移到 user_data/ 新结构。

用法:
    python update_tools/migrate_data.py

功能:
    - 检测项目根目录和 data/ 目录下的旧数据
    - 复制（不删除）到 user_data/ 新目录结构
    - 已存在的文件跳过，不会覆盖
    - 打印详细迁移报告
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _copy_file(src: Path, dst: Path, report: dict) -> None:
    """复制单个文件，跳过已存在的。"""
    if not src.exists() or not src.is_file():
        return
    if dst.exists():
        report["skipped"].append(f"  已存在，跳过: {dst}")
        return
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        report["copied"].append(f"  {src}  →  {dst}")
    except Exception as exc:
        report["failed"].append(f"  失败: {src} → {dst}: {exc}")


def _copy_dir_contents(src_dir: Path, dst_dir: Path, report: dict) -> None:
    """递归复制目录下所有文件。"""
    if not src_dir.exists() or not src_dir.is_dir():
        return
    for item in src_dir.rglob("*"):
        if item.is_file():
            relative = item.relative_to(src_dir)
            _copy_file(item, dst_dir / relative, report)


def run_migration(project_root: Path | None = None) -> dict:
    """执行数据迁移，返回迁移报告。

    Returns:
        dict with keys: "copied", "skipped", "failed" (lists of strings)
    """
    root = Path(project_root) if project_root else _PROJECT_ROOT
    report: dict = {"copied": [], "skipped": [], "failed": []}

    # 导入 PathManager 获取目标路径
    try:
        from path_manager import get_path_manager
        pm = get_path_manager()
    except Exception as exc:
        report["failed"].append(f"  无法加载 PathManager: {exc}")
        return report

    pm.ensure_directories()

    # ── 1. 阅卷数据库 ──
    # 优先从根目录复制，然后从 data/ 复制
    root_db = root / "grading_system.db"
    data_db = root / "data" / "grading_system.db"
    if root_db.exists():
        _copy_file(root_db, pm.db_path, report)
    elif data_db.exists():
        _copy_file(data_db, pm.db_path, report)

    # ── 2. 题库数据库 ──
    old_qb_db = root / "data" / "question_bank" / "question_bank.db"
    _copy_file(old_qb_db, pm.qb_db_path, report)

    # ── 3. 题库附属数据 ──
    qb_subdirs = [
        "raw_papers",
        "extracted_images",
        "rich_content",
        "previews",
        "outputs",
        "assembly_records",
    ]
    for subdir in qb_subdirs:
        old_path = root / "data" / "question_bank" / subdir
        _copy_dir_contents(old_path, pm.qb_data_dir / subdir, report)

    # ── 4. API 配置 ──
    old_api = root / "config" / "api_profiles.json"
    _copy_file(old_api, pm.api_profiles_path, report)
    # data/config/ 下可能也有
    old_api_data = root / "data" / "config" / "api_profiles.json"
    _copy_file(old_api_data, pm.api_profiles_path, report)

    # ── 5. 上传配置 ──
    old_uploaded = root / "config" / "uploaded"
    _copy_dir_contents(old_uploaded, pm.upload_config_dir, report)
    old_uploaded_data = root / "data" / "config" / "uploaded"
    _copy_dir_contents(old_uploaded_data, pm.upload_config_dir, report)

    # ── 6. 报表 ──
    _copy_dir_contents(root / "reports", pm.reports_dir, report)
    _copy_dir_contents(root / "data" / "reports", pm.reports_dir, report)

    # ── 7. 模板 ──
    _copy_dir_contents(root / "templates", pm.templates_dir, report)
    _copy_dir_contents(root / "data" / "templates", pm.templates_dir, report)

    # ── 8. 批注结果 ──
    _copy_dir_contents(root / "annotated", pm.annotated_dir, report)
    _copy_dir_contents(root / "data" / "annotated", pm.annotated_dir, report)

    # ── 9. 考试答卷（跳过 _enhanced 子目录） ──
    for exams_source in [root / "exams", root / "data" / "exams"]:
        if not exams_source.exists():
            continue
        for item in exams_source.rglob("*"):
            if item.is_file():
                # 跳过 _enhanced 子目录
                try:
                    relative = item.relative_to(exams_source)
                    if "_enhanced" in relative.parts:
                        continue
                    _copy_file(item, pm.exams_dir / relative, report)
                except ValueError:
                    continue

    # ── 10. 数据库备份 ──
    _copy_dir_contents(root / "backups", pm.backups_dir, report)

    return report


def print_report(report: dict) -> None:
    """打印迁移报告。"""
    print("\n" + "=" * 60)
    print("  数据迁移报告")
    print("=" * 60)

    if report["copied"]:
        print(f"\n[OK] 成功复制 {len(report['copied'])} 个文件:")
        for line in report["copied"]:
            print(line)

    if report["skipped"]:
        print(f"\n[SKIP] 跳过 {len(report['skipped'])} 个文件（目标已存在）:")
        for line in report["skipped"]:
            print(line)

    if report["failed"]:
        print(f"\n[FAIL] 失败 {len(report['failed'])} 个:")
        for line in report["failed"]:
            print(line)

    if not report["copied"] and not report["skipped"] and not report["failed"]:
        print("\n  没有发现需要迁移的旧数据。")

    print("\n" + "=" * 60)
    print("  旧数据未删除，确认新系统正常后可手动清理。")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    report = run_migration()
    print_report(report)
