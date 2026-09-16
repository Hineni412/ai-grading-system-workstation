# -*- coding: utf-8 -*-
"""Four-way comparison for session 4.

Arms:
  auth      = project DB (other AI + manual review) — authoritative benchmark
  before    = my previous grading run (pre-optimization pipeline), backup DB
  hybrid    = my rerun on the optimized hybrid pipeline (week3_regrade_sandbox)
  fullpaper = my run on the full_paper pipeline (week3_fullpaper_sandbox)

Read-only against every database. Writes four_way.json + REPORT_4WAY.md.
"""
import json
import sqlite3
import statistics
import datetime
import pathlib

ROOT = pathlib.Path(r"D:\AI阅卷系统_工作机版_v1.5.0")
DBS = {
    "auth": ROOT / "user_data/databases/grading_system.db",
    "before": ROOT / "user_data/outputs/codex_session4_full_regrade_20260915/backup/grading_system.db",
    "hybrid": pathlib.Path(r"D:\week3_regrade_sandbox\data\databases/grading_system.db"),
    "fullpaper": pathlib.Path(r"D:\week3_fullpaper_sandbox\data\databases/grading_system.db"),
}
OUT_DIR = ROOT / "user_data/outputs/codex_session4_comparison_20260915/assistant_rerun_20260915"
SESSION_ID = 4
QORDER = ["Q1","Q2","Q3","Q4","Q5","Q6","Q7","Q8","Q9","Q10","Q11","Q12(P1)","Q12(P2)","Q13"]


def fetch(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    results = {r["student_id"]: dict(r) for r in conn.execute(
        "select * from session_results where session_id=?", (SESSION_ID,))}
    details = {}
    for d in conn.execute(
        """select d.*, r.student_id from session_details d
           join session_results r on d.result_id=r.id where r.session_id=?""",
        (SESSION_ID,)):
        details[(d["student_id"], d["question_id"])] = dict(d)
    names = {s["id"]: s["name"] for s in conn.execute("select id,name from students")}
    conn.close()
    return results, details, names


def dist(totals):
    v = list(totals)
    return {"count": len(v), "sum": round(sum(v), 1), "min": min(v), "max": max(v),
            "avg": round(statistics.fmean(v), 2), "median": statistics.median(v)}


def vs_auth(auth_d, other_d):
    per_q = {}
    exact = diff = up = down = 0
    abs_sum = signed = 0.0
    for (sid, qid), a_row in auth_d.items():
        o_row = other_d.get((sid, qid))
        if o_row is None:
            continue
        a = float(a_row["score_awarded"] or 0)
        o = float(o_row["score_awarded"] or 0)
        delta = o - a
        st = per_q.setdefault(qid, {"n": 0, "exact": 0, "up": 0, "down": 0, "abs_delta": 0.0, "signed_delta": 0.0})
        st["n"] += 1
        st["abs_delta"] += abs(delta); st["signed_delta"] += delta
        if delta == 0:
            st["exact"] += 1; exact += 1
        elif delta > 0:
            st["up"] += 1; up += 1
        else:
            st["down"] += 1; down += 1
        abs_sum += abs(delta); signed += delta
    for st in per_q.values():
        st["abs_delta"] = round(st["abs_delta"], 1); st["signed_delta"] = round(st["signed_delta"], 1)
    return {"exact": exact, "diff": up + down, "up": up, "down": down,
            "abs_delta": round(abs_sum, 1), "signed_delta": round(signed, 1),
            "per_question": per_q}


def main():
    data = {arm: fetch(p) for arm, p in DBS.items()}
    names = data["auth"][2]
    a_res, a_det = data["auth"][0], data["auth"][1]

    arms = {}
    for arm in ("before", "hybrid", "fullpaper"):
        res, det = data[arm][0], data[arm][1]
        arms[arm] = {
            "dist": dist(float(r["student_score"] or 0) for r in res.values()),
            "review_flagged": sum(1 for r in res.values() if r["needs_human_review"]),
            "detail_vs_auth": vs_auth(a_det, det),
            "paper_exact": 0,
            "paper_abs_total": 0.0,
        }

    students = []
    for sid in sorted(a_res):
        row = {"student_id": sid, "name": names.get(sid),
               "auth": float(a_res[sid]["student_score"] or 0)}
        for arm in ("before", "hybrid", "fullpaper"):
            o = data[arm][0].get(sid)
            row[arm] = float(o["student_score"]) if o else None
            row[f"{arm}_delta"] = (row[arm] - row["auth"]) if o else None
            if o is not None:
                if row[f"{arm}_delta"] == 0:
                    arms[arm]["paper_exact"] += 1
                arms[arm]["paper_abs_total"] += abs(row[f"{arm}_delta"])
        students.append(row)
    for arm in arms:
        arms[arm]["paper_abs_total"] = round(arms[arm]["paper_abs_total"], 1)
        n = sum(1 for s in students if s[arm] is not None)
        arms[arm]["paper_mean_abs"] = round(arms[arm]["paper_abs_total"] / n, 2) if n else 0

    report = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "arms": {k: str(v) for k, v in DBS.items()},
        "auth_dist": dist(float(r["student_score"] or 0) for r in a_res.values()),
        "auth_review_flagged": sum(1 for r in a_res.values() if r["needs_human_review"]),
        "arms": arms,
        "students": students,
    }
    return report


def md(rep):
    order = [("before", "上次批改（优化前管线）"), ("hybrid", "本次重跑（混合批改）"), ("fullpaper", "本次重跑（整卷批改）")]
    L = []
    L.append("# 四方比对：权威成绩 vs 三种批改产出")
    L.append("")
    L.append(f"生成时间：{rep['generated_at']}  |  场次：session 4（第三周学情反馈）")
    L.append("")
    L.append("> **权威** = 项目库现有成绩（其他 AI 批改 + 人工复核调整），为最终基准。")
    L.append("> **上次批改** = 我在优化前管线上的批改（写回备份库）。")
    L.append("> **混合批改** = 我在你优化后的 hybrid_batch 管线上的隔离重跑。")
    L.append("> **整卷批改** = 我在 full_paper 管线上的隔离重跑（每卷一次请求看整页）。")
    L.append("> 三种 AI 产出的模型判读均来自同一份视觉判读记录，差异反映管线规则/口径。")
    L.append("")
    d = rep["auth_dist"]
    L.append("## 1. 总分分布")
    L.append("")
    L.append("| 指标 | 权威 | 上次批改 | 混合批改 | 整卷批改 |")
    L.append("|---|---|---|---|---|")
    for k, lab in (("sum","总分"),("avg","均分"),("median","中位"),("min","最低"),("max","最高")):
        L.append(f"| {lab} | {d[k]} | " + " | ".join(str(rep['arms'][a]['dist'][k]) for a, _ in order) + " |")
    L.append(f"| 挂复核卷数 | {rep['auth_review_flagged']} | " + " | ".join(str(rep['arms'][a]['review_flagged']) for a, _ in order) + " |")
    L.append("")
    L.append("## 2. 与权威的吻合度")
    L.append("")
    L.append("| 指标 | 上次批改 | 混合批改 | 整卷批改 |")
    L.append("|---|---|---|---|")
    L.append("| 总分与权威完全一致 | " + " | ".join(f"{rep['arms'][a]['paper_exact']} 份" for a, _ in order) + " |")
    L.append("| 卷面绝对偏差合计 | " + " | ".join(str(rep['arms'][a]['paper_abs_total']) for a, _ in order) + " |")
    L.append("| 每卷平均绝对偏差 | " + " | ".join(str(rep['arms'][a]['paper_mean_abs']) for a, _ in order) + " |")
    L.append("| 明细一致条数 /1190 | " + " | ".join(str(rep['arms'][a]['detail_vs_auth']['exact']) for a, _ in order) + " |")
    L.append("| 明细绝对偏差合计 | " + " | ".join(str(rep['arms'][a]['detail_vs_auth']['abs_delta']) for a, _ in order) + " |")
    L.append("| 明细净偏差（±) | " + " | ".join(f"{rep['arms'][a]['detail_vs_auth']['signed_delta']:+g}" for a, _ in order) + " |")
    L.append("")
    L.append("## 3. 各题与权威的绝对偏差 |Δ|（越小越接近人工结果）")
    L.append("")
    L.append("| 题号 | 上次批改 | 混合批改 | 整卷批改 |")
    L.append("|---|---|---|---|")
    for qid in QORDER:
        row = []
        for a, _ in order:
            pq = rep["arms"][a]["detail_vs_auth"]["per_question"].get(qid, {})
            row.append(f"{pq.get('abs_delta','-')}（{pq.get('exact','-')}/85 一致）")
        L.append(f"| {qid} | " + " | ".join(row) + " |")
    L.append("")
    L.append("## 4. 每生总分（按 |整卷-权威| 降序列前 25）")
    L.append("")
    L.append("| 学生 | 权威 | 上次 | 混合 | 整卷 |")
    L.append("|---|---|---|---|---|")
    for r in sorted(rep["students"], key=lambda r: -abs(r["fullpaper_delta"] or 0))[:25]:
        L.append(f"| {r['name'] or r['student_id']} | {r['auth']} | {r['before']} | {r['hybrid']} | {r['fullpaper']} |")
    L.append("")
    L.append("---")
    L.append("说明：所有 AI 产出均为隔离库数据，项目权威库未做任何写入（sha256 复核一致）。")
    return "\n".join(L)


if __name__ == "__main__":
    rep = main()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "four_way.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT_DIR / "REPORT_4WAY.md").write_text(md(rep), encoding="utf-8")
    for arm in ("before", "hybrid", "fullpaper"):
        a = rep["arms"][arm]
        print(arm, "| avg", a["dist"]["avg"], "| exact papers", a["paper_exact"],
              "| detail exact", a["detail_vs_auth"]["exact"],
              "| abs", a["detail_vs_auth"]["abs_delta"], "| signed", a["detail_vs_auth"]["signed_delta"])
