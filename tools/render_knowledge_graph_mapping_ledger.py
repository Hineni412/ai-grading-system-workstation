from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
RELEASE_PATH = (
    ROOT
    / "question_bank"
    / "taxonomy"
    / "catalogs"
    / "knowledge_graph_release_v1.json"
)
OUTPUT_PATH = (
    ROOT
    / "output"
    / "knowledge-graph"
    / "knowledge-graph-mapping-ledger.md"
)


def render(payload: Mapping[str, Any]) -> str:
    dispositions = _objects(payload.get("fine_term_dispositions"))
    nodes = {
        str(item["stable_key"]): item
        for item in _objects(payload.get("core_nodes"))
    }
    mappings: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in _objects(payload.get("mappings")):
        mappings[str(item["fine_term_id"])].append(item)
    disposition_counts = Counter(
        str(item["disposition"]) for item in dispositions
    )
    active_nodes = [item for item in nodes.values() if item["status"] == "active"]
    retired_nodes = [item for item in nodes.values() if item["status"] == "retired"]
    high_impact = [
        item for item in dispositions if item["review_priority"] == "high_impact"
    ]
    lines = [
        "# 初中数学 294 个规范知识词映射账本",
        "",
        "> 本文件由 `tools/render_knowledge_graph_mapping_ledger.py` 从同一份发布包生成；",
        "> 用于教师集中核对，不是另一份可独立修改的数据源。",
        "",
        "## 发布摘要",
        "",
        f"- 发布编号：`{payload['release_id']}`",
        f"- 内容指纹：`{payload['content_hash']}`",
        f"- 规范知识词：{len(dispositions)}（已处置 {len(dispositions)}/{len(dispositions)}）",
        f"- 活动核心节点：{len(active_nodes)}",
        f"- 退役旧节点：{len(retired_nodes)}",
        f"- 映射记录：{len(_objects(payload.get('mappings')))}",
        f"- 正式关系：{len(_objects(payload.get('relations')))}",
        f"- 高影响集中确认项：{len(high_impact)}",
        "",
        "处置数量："
        + "；".join(
            f"`{key}` {value}"
            for key, value in sorted(disposition_counts.items())
        )
        + "。",
        "",
        "## 294 个词逐条账本",
        "",
        "| # | 规范词（稳定 ID） | 处置 | 核心去向 | 置信度 | 课标主题 |",
        "|---:|---|---|---|---:|---|",
    ]
    for index, item in enumerate(
        sorted(dispositions, key=lambda row: (str(row["display_name"]), str(row["fine_term_id"]))),
        start=1,
    ):
        targets = []
        for mapping in sorted(
            mappings.get(str(item["fine_term_id"]), []),
            key=lambda row: (0 if row["mapping_role"] == "primary" else 1, str(row["stable_key"])),
        ):
            node = nodes[str(mapping["stable_key"])]
            targets.append(
                f"{node['display_name']}（{mapping['mapping_role']}）"
            )
        anchors = "；".join(str(value) for value in item["curriculum_anchors"][:2])
        lines.append(
            "| "
            + " | ".join(
                (
                    str(index),
                    _cell(f"{item['display_name']}（{item['fine_term_id']}）"),
                    _cell(str(item["disposition"])),
                    _cell("；".join(targets) if targets else "不进入掌握度图谱"),
                    f"{float(item['confidence']):.3f}",
                    _cell(anchors),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 高影响集中确认项",
            "",
            "这些项目涉及一对多、维度纠正、超出常规初中范围或旧节点退役。",
            "普通同义归并不要求教师逐条填写。",
            "",
            "| 规范词 | 处置 | 核心去向 | 理由 |",
            "|---|---|---|---|",
        ]
    )
    for item in sorted(high_impact, key=lambda row: str(row["display_name"])):
        targets = [
            f"{nodes[str(mapping['stable_key'])]['display_name']}（{mapping['mapping_role']}）"
            for mapping in mappings.get(str(item["fine_term_id"]), [])
        ]
        lines.append(
            f"| {_cell(item['display_name'])} | {_cell(item['disposition'])} | "
            f"{_cell('；'.join(targets) if targets else '不进入掌握度图谱')} | "
            f"{_cell(item['rationale'])} |"
        )
    lines.extend(
        [
            "",
            "## 旧核心身份处置",
            "",
            "| 旧稳定身份 | 状态 | 替代去向 |",
            "|---|---|---|",
        ]
    )
    replacements: dict[str, list[str]] = defaultdict(list)
    for item in _objects(payload.get("replacements")):
        replacements[str(item["retired_key"])].append(
            str(nodes[str(item["replacement_key"])]["display_name"])
        )
    for node in sorted(nodes.values(), key=lambda row: str(row["display_name"])):
        if not str(node["stable_key"]).startswith("kp_"):
            continue
        target_names = replacements.get(str(node["stable_key"]), [])
        lines.append(
            f"| {_cell(node['display_name'])}（`{node['stable_key']}`） | "
            f"{_cell(node['status'])} | {_cell('；'.join(target_names) if target_names else '稳定保留')} |"
        )
    lines.append("")
    return "\n".join(lines)


def _objects(raw: object) -> list[Mapping[str, Any]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _cell(value: object) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = json.loads(RELEASE_PATH.read_text(encoding="utf-8"))
    rendered = render(payload)
    if args.check:
        if not OUTPUT_PATH.exists() or OUTPUT_PATH.read_text(encoding="utf-8") != rendered:
            raise SystemExit("knowledge graph mapping ledger is not up to date")
        return 0
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(OUTPUT_PATH.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
