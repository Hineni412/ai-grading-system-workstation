"""系统自检页面 — 检查系统环境、数据目录、数据库与 API 配置状态。"""

from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="系统自检", page_icon="🔍", layout="wide")
st.title("🔍 系统自检")
st.caption("检查系统环境与数据目录状态，帮助排查问题。")


# ── 辅助函数 ──────────────────────────────────────────

def _check_writable(directory: Path) -> bool:
    """检查目录是否可写。"""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        test_file = directory / ".write_test_tmp"
        test_file.write_text("test", encoding="utf-8")
        test_file.unlink()
        return True
    except Exception:
        return False


def _status_icon(ok: bool) -> str:
    return "✅" if ok else "❌"


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _get_latest_backup(backup_dir: Path) -> str:
    """获取最近一次备份的时间。"""
    if not backup_dir.exists():
        return "无备份"
    backups = sorted(backup_dir.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not backups:
        return "无备份"
    mtime = backups[0].stat().st_mtime
    dt = datetime.fromtimestamp(mtime)
    return f"{dt.strftime('%Y-%m-%d %H:%M:%S')}（{backups[0].name}）"


def _check_api_key(api_profiles_path: Path) -> tuple[bool, str]:
    """检查 API Key 是否已配置。"""
    if not api_profiles_path.exists():
        return False, "配置文件不存在"
    try:
        data = json.loads(api_profiles_path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or not data:
            return False, "配置文件为空"
        for profile in data:
            if isinstance(profile, dict) and str(profile.get("api_key", "")).strip():
                return True, f"已配置（方案: {profile.get('name', '默认')}）"
        return False, "所有方案的 API Key 为空"
    except Exception as exc:
        return False, f"读取失败: {exc}"


def _get_package_version(package_name: str) -> str:
    try:
        import importlib.metadata
        return importlib.metadata.version(package_name)
    except Exception:
        return "未安装"


# ── 主逻辑 ────────────────────────────────────────────

try:
    from path_manager import get_path_manager
    pm = get_path_manager()
    pm_ok = True
except Exception as exc:
    pm_ok = False
    pm_error = str(exc)

if not pm_ok:
    st.error(f"PathManager 加载失败: {pm_error}")
    st.info("请确认 path_manager.py 和 config/app_config.yaml 存在。")
    st.stop()

# ── 系统信息 ──────────────────────────────────────────

st.header("系统信息")

info_cols = st.columns(4)
with info_cols[0]:
    st.metric("系统版本", pm.version)
with info_cols[1]:
    st.metric("Python 版本", platform.python_version())
with info_cols[2]:
    st.metric("操作系统", platform.system())
with info_cols[3]:
    st.metric("架构", platform.machine())

# ── 关键依赖版本 ──────────────────────────────────────

st.header("关键依赖")

dep_cols = st.columns(6)
deps = [
    ("streamlit", "Streamlit"),
    ("openai", "OpenAI"),
    ("pandas", "Pandas"),
    ("Pillow", "Pillow"),
    ("python-docx", "python-docx"),
    ("openpyxl", "openpyxl"),
]
for col, (pkg, label) in zip(dep_cols, deps):
    with col:
        ver = _get_package_version(pkg)
        st.metric(label, ver)


# ── 数据目录检查 ──────────────────────────────────────

st.header("数据目录")

path_summary = pm.summary()
checks = []
for label, path_str in path_summary.items():
    path = Path(path_str)
    exists = path.exists()
    if path.is_file() or path_str.endswith(".db") or path_str.endswith(".json"):
        writable = exists  # 文件只检查存在性
        size = _format_size(path.stat().st_size) if exists and path.is_file() else "-"
    else:
        writable = _check_writable(path) if exists else False
        size = "-"
    checks.append({
        "名称": label,
        "路径": path_str,
        "存在": _status_icon(exists),
        "可写": _status_icon(writable),
        "大小": size,
    })

st.dataframe(checks, use_container_width=True, hide_index=True)


# ── 数据库状态与 Schema 版本 ──────────────────────────

st.header("数据库状态")

# 获取迁移状态
_migration_status = {}
try:
    sys.path.insert(0, str(pm.project_root / "update_tools"))
    from migrate_db import get_migration_status
    for _target in ("grading", "question_bank"):
        _migration_status[_target] = get_migration_status(_target)
except Exception:
    pass

db_cols = st.columns(2)
with db_cols[0]:
    db_exists = pm.db_path.exists()
    db_size = _format_size(pm.db_path.stat().st_size) if db_exists else "0"
    st.metric(
        "阅卷数据库",
        f"{_status_icon(db_exists)} {'正常' if db_exists else '不存在'}",
        delta=db_size if db_exists else None,
    )
    st.caption(str(pm.db_path))
    g_status = _migration_status.get("grading", {})
    if g_status:
        g_ver = g_status.get("schema_version", "?")
        g_pending = len(g_status.get("pending", []))
        g_last = g_status.get("last_migration_time") or "无"
        st.caption(f"Schema 版本: {g_ver} | 待迁移: {g_pending} | 最后迁移: {g_last}")

with db_cols[1]:
    qb_exists = pm.qb_db_path.exists()
    qb_size = _format_size(pm.qb_db_path.stat().st_size) if qb_exists else "0"
    st.metric(
        "题库数据库",
        f"{_status_icon(qb_exists)} {'正常' if qb_exists else '不存在'}",
        delta=qb_size if qb_exists else None,
    )
    st.caption(str(pm.qb_db_path))
    q_status = _migration_status.get("question_bank", {})
    if q_status:
        q_ver = q_status.get("schema_version", "?")
        q_pending = len(q_status.get("pending", []))
        q_last = q_status.get("last_migration_time") or "无"
        st.caption(f"Schema 版本: {q_ver} | 待迁移: {q_pending} | 最后迁移: {q_last}")

# 迁移按钮
total_pending = sum(len(s.get("pending", [])) for s in _migration_status.values())
if total_pending > 0:
    st.warning(f"有 {total_pending} 个数据库迁移待执行。")
    mig_cols = st.columns([1, 1, 2])
    with mig_cols[0]:
        if st.button("执行数据库迁移", type="primary", key="run_db_migration_btn"):
            with st.spinner("正在执行数据库迁移（每步自动备份）..."):
                try:
                    from migrate_db import run_migrations
                    for _target in ("grading", "question_bank"):
                        report = run_migrations(_target)
                        applied = [r for r in report.results if r.status == "applied"]
                        if applied:
                            st.success(f"[{_target}] 成功执行 {len(applied)} 个迁移")
                        if report.error:
                            st.error(f"[{_target}] {report.error}")
                except Exception as exc:
                    st.error(f"迁移失败: {exc}")
    with mig_cols[1]:
        st.caption("迁移前会自动备份数据库")
else:
    st.success("所有数据库 Schema 已是最新。")

st.code(
    "# 命令行执行迁移\n"
    "python update_tools/migrate_db.py\n\n"
    "# 查看迁移状态\n"
    "python update_tools/migrate_db.py --status\n\n"
    "# 预览（不执行）\n"
    "python update_tools/migrate_db.py --dry-run",
    language="bash",
)


# ── API 配置状态 ──────────────────────────────────────

st.header("API 配置")

api_ok, api_msg = _check_api_key(pm.api_profiles_path)
st.metric("API Key 状态", f"{_status_icon(api_ok)} {api_msg}")



# ── 备份与恢复 ────────────────────────────────────────

st.header("备份与恢复")

# -- 一键备份 --
st.subheader("创建备份")
st.caption("将 user_data/ 和 config/ 打包为 zip 备份。API Key 明文默认不包含。")

bk_cols = st.columns([1, 1, 1, 1])
with bk_cols[0]:
    bk_reason = st.selectbox(
        "备份原因",
        options=["manual", "before_exam", "before_update", "after_exam"],
        format_func=lambda x: {
            "manual": "手动备份",
            "before_exam": "阅卷前备份",
            "before_update": "更新前备份",
            "after_exam": "阅卷后备份",
        }.get(x, x),
        key="bk_reason",
    )
with bk_cols[1]:
    bk_include_keys = st.checkbox("包含 API Key", value=False, key="bk_include_keys")
with bk_cols[2]:
    bk_include_logs = st.checkbox("包含日志文件", value=False, key="bk_include_logs")
with bk_cols[3]:
    st.write("")  # spacing
    bk_run = st.button("立即备份", type="primary", key="bk_run_btn", use_container_width=True)

if bk_run:
    with st.spinner("正在创建备份..."):
        try:
            sys.path.insert(0, str(pm.project_root / "update_tools"))
            from backup_core import create_backup as _create_backup
            bk_result = _create_backup(
                reason=bk_reason,
                include_api_keys=bk_include_keys,
                include_logs=bk_include_logs,
            )
            if bk_result.get("error"):
                st.error(f"备份失败: {bk_result['error']}")
            else:
                zip_path = Path(bk_result["zip_path"])
                zip_size = _format_size(zip_path.stat().st_size)
                st.success(
                    f"备份完成! 包含 {bk_result['file_count']} 个文件, "
                    f"压缩后 {zip_size}\n\n"
                    f"文件: `{zip_path.name}`"
                )
                if bk_result.get("skipped_sensitive"):
                    st.info(f"已跳过 {len(bk_result['skipped_sensitive'])} 个敏感文件（API Key）")
        except Exception as exc:
            st.error(f"备份失败: {exc}")

# -- 备份列表 --
st.subheader("已有备份")

try:
    sys.path.insert(0, str(pm.project_root / "update_tools"))
    from backup_core import list_backups as _list_backups
    all_backups = _list_backups()
except Exception:
    all_backups = []

# 同时显示旧版 .db 备份
db_backups = []
if pm.backups_dir.exists():
    for db_file in sorted(pm.backups_dir.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            stat = db_file.stat()
            db_backups.append({
                "filename": db_file.name,
                "size_display": _format_size(stat.st_size),
                "time": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "reason": "DB 自动备份",
            })
        except OSError:
            continue

reason_labels = {
    "manual": "手动备份",
    "before_exam": "阅卷前",
    "before_update": "更新前",
    "after_exam": "阅卷后",
    "DB 自动备份": "DB 自动备份",
}

if all_backups or db_backups:
    display_rows = []
    for b in all_backups:
        display_rows.append({
            "类型": "ZIP 全量备份",
            "文件名": b["filename"],
            "时间": b["time"],
            "原因": reason_labels.get(b["reason"], b["reason"]),
            "大小": b["size_display"],
        })
    for b in db_backups[:10]:  # 最多显示 10 条 DB 备份
        display_rows.append({
            "类型": "DB 自动备份",
            "文件名": b["filename"],
            "时间": b["time"],
            "原因": b["reason"],
            "大小": b["size_display"],
        })
    st.dataframe(display_rows, use_container_width=True, hide_index=True)
else:
    st.info("暂无备份记录。")

# -- 恢复说明 --
st.subheader("恢复备份")
st.caption("为防止误操作，恢复功能仅通过命令行执行。恢复前会自动备份当前数据。")
st.code(
    "# 查看所有备份\n"
    "python update_tools/list_backups.py\n\n"
    "# 恢复指定备份（需输入 YES 确认）\n"
    "python update_tools/restore_backup.py backup_XXXXXXXX_XXXXXX_reason.zip\n\n"
    "# 先预览恢复内容，不实际操作\n"
    "python update_tools/restore_backup.py backup_XXXXXXXX_XXXXXX_reason.zip --dry-run",
    language="bash",
)


# ── 日志目录 ──────────────────────────────────────────

st.header("日志目录")

logs_writable = _check_writable(pm.logs_dir)
st.metric("日志目录可写", f"{_status_icon(logs_writable)} {'正常' if logs_writable else '不可写'}")
st.caption(str(pm.logs_dir))

# 检查备份日志
backup_log = pm.logs_dir / "backup.log"
if backup_log.exists():
    with st.expander("查看备份日志（最近 30 行）"):
        try:
            lines = backup_log.read_text(encoding="utf-8").splitlines()
            for line in lines[-30:]:
                st.text(line)
        except Exception:
            st.warning("无法读取备份日志。")

# 检查旧日志文件
old_logs = list(pm.project_root.glob("streamlit_server*.log"))
if old_logs:
    st.warning(f"发现 {len(old_logs)} 个旧日志文件在项目根目录下，建议清理。")


# ── 数据迁移 ──────────────────────────────────────────

st.header("数据迁移")
st.caption("如果从旧版升级，可以运行迁移工具将旧数据复制到新的 user_data/ 目录。旧数据不会被删除。")

# 检测旧数据
old_data_indicators = []
if (pm.project_root / "grading_system.db").exists():
    old_data_indicators.append("项目根目录下的 grading_system.db")
if (pm.project_root / "data" / "grading_system.db").exists():
    old_data_indicators.append("data/grading_system.db")
if (pm.project_root / "data" / "question_bank" / "question_bank.db").exists():
    old_data_indicators.append("data/question_bank/question_bank.db")

if old_data_indicators:
    st.info(f"检测到旧版数据位置:\n" + "\n".join(f"- {item}" for item in old_data_indicators))
else:
    st.success("未检测到需要迁移的旧数据。")

if st.button("运行数据迁移", type="primary", key="run_migration_btn"):
    with st.spinner("正在迁移数据..."):
        try:
            sys.path.insert(0, str(pm.project_root / "update_tools"))
            from migrate_data import run_migration
            report = run_migration(pm.project_root)

            if report["copied"]:
                st.success(f"成功复制 {len(report['copied'])} 个文件")
                with st.expander("查看复制详情"):
                    for line in report["copied"]:
                        st.text(line)

            if report["skipped"]:
                st.info(f"跳过 {len(report['skipped'])} 个已存在的文件")
                with st.expander("查看跳过详情"):
                    for line in report["skipped"]:
                        st.text(line)

            if report["failed"]:
                st.error(f"失败 {len(report['failed'])} 个")
                for line in report["failed"]:
                    st.text(line)

            if not report["copied"] and not report["skipped"] and not report["failed"]:
                st.info("没有发现需要迁移的旧数据。")

        except Exception as exc:
            st.error(f"迁移失败: {exc}")

