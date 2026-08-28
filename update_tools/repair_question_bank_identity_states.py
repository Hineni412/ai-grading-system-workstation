"""题库身份状态一次性修复工具 — 让治理表身份状态对齐当前知识标准。

背景：现役题库库的当前知识标准是通过 bootstrap_release 安装的，该路径此前
只把发布包节点登记为 retired 占位身份，从未把身份状态同步为发布包定义的
active。结果是现行 bnu24 词表的 1124 个节点在 knowledge_tag_identities 里
全部 retired，导致关系治理（建议、审核队列）一律拒绝现行词表的稳定编号。
bootstrap_release 的代码缺口已修复；本工具把既有数据库补齐到相同状态。

用法（默认 dry-run，只报告不写入）：
    runtime/python/python.exe update_tools/repair_question_bank_identity_states.py
    确认报告无误后加 --apply 才真正写入：
    runtime/python/python.exe update_tools/repair_question_bank_identity_states.py --apply

--apply 会先把题库库文件复制为同目录下的 .bak-时间戳 备份，再在单个事务里
调用与发布启用相同语义的 _apply_identity_states 同步身份状态。

注意：
    - 只同步 knowledge_tag_identities 与其别名；不触碰映射与关系治理表。
    - 修复幂等：状态已对齐时 --apply 不产生任何更新。
    - 运行前请确认应用已停止，避免与运行中的写入冲突。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 确保项目根目录在 sys.path 中
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from question_bank.database.schema import connect  # noqa: E402
from question_bank.knowledge_graph_release.repository import (  # noqa: E402
    _apply_identity_states,
    load_active_release,
)

DEFAULT_DB = Path("user_data/databases/question_bank.db")


def _plan(connection, payload) -> dict[str, list]:
    nodes = [dict(item) for item in payload.get("core_nodes") or []]
    target_keys = {str(node["stable_key"]) for node in nodes}
    historical = {
        str(row[0])
        for row in connection.execute(
            "SELECT DISTINCT stable_key FROM knowledge_graph_node_profiles"
        )
    }
    plan: dict[str, list] = {
        "retire_not_in_release": sorted(historical - target_keys),
        "to_active": [],
        "to_retired": [],
        "name_updates": [],
        "missing": [],
    }
    for node in nodes:
        key = str(node["stable_key"])
        name = str(node["display_name"])
        wanted = "retired" if str(node["status"]) == "retired" else "active"
        row = connection.execute(
            "SELECT status, display_name FROM knowledge_tag_identities "
            "WHERE stable_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            plan["missing"].append(key)
            continue
        if str(row[0]) != wanted:
            plan[f"to_{wanted}"].append(key)
        if str(row[1]) != name:
            plan["name_updates"].append(key)
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="题库库路径")
    parser.add_argument("--apply", action="store_true", help="真正写入（默认只报告）")
    args = parser.parse_args()

    db_path = Path(args.db)
    release = load_active_release(db_path)
    if release is None:
        print("当前数据库没有活动知识标准，无需修复。")
        return 1
    print(f"活动知识标准: {release.release_id} (taxonomy_revision={release.taxonomy_revision})")

    with connect(db_path) as connection:
        plan = _plan(connection, release.payload)
        counts = {name: len(values) for name, values in plan.items()}
        print("修复计划:", counts)
        for name in ("missing", "retire_not_in_release", "name_updates"):
            sample = plan[name][:5]
            if sample:
                print(f"  {name} 示例: {sample}")
        if plan["missing"]:
            print("存在未登记身份的节点，请先排查发布安装是否完整，未写入。")
            return 1
        if not args.apply:
            print("dry-run：未写入任何数据。确认后加 --apply 执行。")
            return 0
        if not any(counts[name] for name in ("to_active", "to_retired", "name_updates")):
            print("身份状态已与活动标准一致，无需写入。")
            return 0

        backup = db_path.with_name(
            f"{db_path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}"
        )
        shutil.copyfile(db_path, backup)
        print(f"已备份: {backup}")

        connection.execute("BEGIN IMMEDIATE")
        _apply_identity_states(connection, release)
        connection.commit()

        active = connection.execute(
            "SELECT COUNT(*) FROM knowledge_tag_identities WHERE status = 'active'"
        ).fetchone()[0]
        retired = connection.execute(
            "SELECT COUNT(*) FROM knowledge_tag_identities WHERE status = 'retired'"
        ).fetchone()[0]
        print(f"修复完成: active={active} retired={retired}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
