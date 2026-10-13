"""命题训练原型：把本机试抽结果转成前端可加载的 data.js（只读，不改业务数据）。

读取 tmp_item_writing_trial/trial-20261012.json（10 条试抽），连同核定统计，
输出 tmp_item_writing_prototype/data.js（Git 忽略）。用法：
    runtime\\python\\python.exe docs/requests/item-writing-prototype-20261012/build_library_data.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "tmp_item_writing_trial" / "trial-20261012.json"
OUT = ROOT / "tmp_item_writing_prototype" / "data.js"

CHAPTER_OF = {"1": "第一章 勾股定理", "2": "第二章 实数", "3": "第三章 位置与坐标",
              "4": "第四章 一次函数", "5": "第五章 二元一次方程组", "6": "第六章 数据的分析", "7": "第七章 证明"}

# 2026-10-12 核定统计：[章名, 符合, 去重后, 已抽过]，照抄不重新查询
CHAPTERS = [
    ["第一章 勾股定理", 88, 79, 0],
    ["第二章 实数", 9, 8, 0],
    ["第三章 位置与坐标", 38, 34, 0],
    ["第四章 一次函数", 92, 83, 2],
    ["第五章 二元一次方程组", 54, 52, 2],
    ["第六章 数据的分析", 51, 51, 1],
    ["第七章 证明", 16, 15, 0],
]


def main():
    trial = json.loads(SRC.read_text(encoding="utf-8"))
    materials = []
    for r in trial["results"]:
        res = r["result"]
        materials.append({
            "id": f"q{r['question_id']}", "kind": "情境材料",
            "title": res["title"], "situation": res["situation"],
            "section": r["section"], "chapter": CHAPTER_OF.get(r["section"].split(".")[0]),
            "context_kind": r["context_kinds"][0] if r["context_kinds"] else None,
            "needs_figure": res["needs_figure"], "local_issues": r.get("local_issues", []),
            "source_question_ids": [r["question_id"]],
            "status": "pending" if res["usable"] else "disabled",
            "disabled_reason": None if res["usable"] else res["reason"],
            "origin": "ai_extract", "revision": 1,
            "source_text": r["source_text"],
        })
    data = {
        "source_note": "tmp_item_writing_trial/trial-20261012.json（10 条试抽）",
        "terms": {
            "八年级上册": {"has_candidates": True,
                "chapters": [{"name": n, "fit": f, "dedup": d, "extracted": e} for n, f, d, e in CHAPTERS]},
            "八年级下册": {"has_candidates": False, "note": "无候选题", "chapters": []},
        },
        "materials": materials,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("window.ITEM_LIBRARY_REAL = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n",
                   encoding="utf-8")
    print("wrote", OUT, "materials", len(materials),
          "pending", sum(1 for m in materials if m["status"] == "pending"),
          "disabled", sum(1 for m in materials if m["status"] == "disabled"))


if __name__ == "__main__":
    main()
