"""导入七年级下册跨章主线关系候选（suggested 状态，不进入当前图）。

背景：当前知识标准 rev4 只有 parent 关系，推荐的先修补强/迁移应用阶段靠教材
顺序兜底。本脚本把跨章/跨册主线关系以候选身份写入 `knowledge_relations`，
教师在「知识结构」页关系审核队列确认后，随下一版标准发布才生效。

幂等：`source_operation_id` 固定，重复执行不会重复插入（create_suggestion 原样返回既有记录）。
执行（仓库根目录）：runtime/python/python.exe tools/import_mainline_relation_candidates.py --apply
"""

from __future__ import annotations

import argparse
import io
import sqlite3
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from question_bank.relations.contracts import (  # noqa: E402
    KnowledgeRelation,
    RelationType,
)
from question_bank.relations.repository import (  # noqa: E402
    KnowledgeRelationDuplicate,
    KnowledgeRelationRepository,
)

DB_PATH = ROOT / "user_data" / "databases" / "question_bank.db"
OPERATION_ID = "mainline-g7lower-2026-08-26"
SOURCE_REFERENCE = "主线关系候选·七年级下册·2026-08-26"

# (source_key, target_key, relation_type, rationale)
# 方向约定：prerequisite 的 source 是“依赖方”（后学内容），target 是“地基”（先学内容）。
CANDIDATES: tuple[tuple[str, str, str, str], ...] = (
    (
        "kp_bnu24_math_g7_lower_1",  # 七下 第一章 整式的乘除
        "kp_bnu24_math_g7_upper_3_2",  # 七上 第三章 2 整式的加减
        "prerequisite",
        "整式的乘除以整式的概念与加减运算为基础。",
    ),
    (
        "kp_bnu24_math_g7_lower_1",
        "kp_bnu24_math_g7_upper_2_4",  # 七上 第二章 4 有理数的乘方
        "prerequisite",
        "幂的乘除与乘法公式以有理数乘方的意义为基础。",
    ),
    (
        "kp_bnu24_math_g7_lower_2",  # 七下 第二章 相交线与平行线
        "kp_bnu24_math_g7_upper_4_1",  # 七上 第四章 1 线段、射线、直线
        "prerequisite",
        "两条直线的位置关系以对线段、射线、直线的基本认识为基础。",
    ),
    (
        "kp_bnu24_math_g7_lower_2",
        "kp_bnu24_math_g7_upper_4_2",  # 七上 第四章 2 角
        "prerequisite",
        "补角、对顶角与角度计算以角的概念与度量为基础。",
    ),
    (
        "kp_bnu24_math_g7_lower_4",  # 七下 第四章 三角形
        "kp_bnu24_math_g7_lower_2_3",  # 七下 第二章 3 平行线的性质
        "prerequisite",
        "三角形内角和定理的证明依赖平行线的性质。",
    ),
    (
        "kp_bnu24_math_g7_lower_4",
        "kp_bnu24_math_g7_upper_4_2",  # 七上 第四章 2 角
        "prerequisite",
        "三角形中与角有关的计算以角的概念与度量为基础。",
    ),
    (
        "kp_bnu24_math_g7_lower_5",  # 七下 第五章 图形的轴对称
        "kp_bnu24_math_g7_lower_4_3",  # 七下 第四章 3 探索三角形全等的条件
        "prerequisite",
        "等腰三角形、线段垂直平分线等轴对称图形性质的证明依赖三角形全等的判定。",
    ),
    (
        "kp_bnu24_math_g7_lower_6",  # 七下 第六章 变量之间的关系
        "kp_bnu24_math_g7_upper_3_1",  # 七上 第三章 1 代数式
        "prerequisite",
        "用关系式表示变量之间的关系以代数式（用字母表示数）为基础。",
    ),
    (
        "kp_bnu24_math_g7_lower_6",
        "kp_bnu24_math_g8_upper_4",  # 八上 第四章 一次函数
        "related",
        "变量之间的关系与一次函数内容直接衔接，可作为学有余力学生的迁移应用。",
    ),
)


def validate_identities(db_path: Path, keys: set[str]) -> list[str]:
    uri = db_path.resolve(strict=True).as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            "SELECT stable_key, status FROM knowledge_tag_identities"
        ).fetchall()
    active = {str(key) for key, status in rows if str(status) == "active"}
    return sorted(key for key in keys if key not in active)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际写入；不加则只打印将要插入的内容（dry-run）",
    )
    args = parser.parse_args()

    keys = {source for source, _t, _r, _ra in CANDIDATES} | {
        target for _s, target, _r, _ra in CANDIDATES
    }
    invalid = validate_identities(DB_PATH, keys)
    if invalid:
        print("以下 key 不是当前活动身份，终止，未写入任何数据：")
        for key in invalid:
            print("  -", key)
        return 1

    if not args.apply:
        print(f"dry-run：将写入 {len(CANDIDATES)} 条候选（suggested，不进入当前图）")
        for source, target, relation_type, rationale in CANDIDATES:
            print(f"  [{relation_type}] {source} -> {target}\n      {rationale}")
        return 0

    repository = KnowledgeRelationRepository(DB_PATH)
    created = 0
    skipped = 0
    for source, target, relation_type, rationale in CANDIDATES:
        relation = KnowledgeRelation(
            source_key=source,
            target_key=target,
            relation_type=RelationType(relation_type),
        )
        try:
            repository.create_suggestion(
                relation,
                source_kind="import",
                rationale=rationale,
                source_reference=SOURCE_REFERENCE,
                source_operation_id=OPERATION_ID,
            )
        except KnowledgeRelationDuplicate:
            skipped += 1
        else:
            created += 1
    print(f"完成：新写入 {created} 条，已存在跳过 {skipped} 条（均为 suggested 状态）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
