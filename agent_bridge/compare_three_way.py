"""Three-way comparison: authoritative (now) vs previous run (backup) vs isolated rerun (latest pipeline).

Read-only. Writes three_way.json + REPORT_3WAY.md next to the rerun comparison report.
"""
import json
import sqlite3
import statistics
import datetime
import pathlib

ROOT = pathlib.Path(r"D:\AI阅卷系统_工作机版_v1.5.0")
AUTH_DB = ROOT / "user_data/databases/grading_system.db"
BEFORE_DB = ROOT / "user_data/outputs/codex_session4_full_regrade_20260915/backup/grading_system.db"
AFTER_DB = pathlib.Path(r"D:\week3_regrade_sandbox\data\databases/grading_system.db")
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


def vs_auth(auth_d, other_d, qids):
    """Return per-question and overall agreement stats of other vs auth."""
    exact = 0; diff = 0; up = 0; down = 0; abs_sum = 0.0; signed = 0.0
    per_q = {}
    for qid in qids:
        qe = qu = qd = 0; qabs = 0.0; qsum = 0.0; n = 0
        for (sid, q) in auth_d:
            if q != qid or (sid, q) not in other_d:
                continue
            n += 1
            a = float(auth_d[(sid, q)]["score_awarded"] or 0)
            o = float(other_d[(sid, q)]["score_awarded"] or 0)
            delta = o - a
            qabs += abs(delta); qsum += delta
            if delta == 0: qe += 1
            elif delta > 0: qu += 1
            else: qd += 1
        per_q[qid] = {"n": n, "exact": qe, "up": qu, "down": qd,
                      "abs_delta": round(qabs, 1), "signed_delta": round(qsum, 1)}
        exact += qe; up += qu; down += qd; abs_sum += qabs; signed += qsum
    diff = up + down
    return {"exact": exact, "diff": diff, "up": up, "down": down,
            "abs_delta": round(abs_sum, 1), "signed_delta": round(signed, 1),
            "per_question": per_q}


def main():
    a_res, a_det, names = fetch(AUTH_DB)
    b_res, b_det, _ = fetch(BEFORE_DB)
    s_res, s_det, _ = fetch(AFTER_DB)

    qids = [q for q in QORDER if any(k[1] == q for k in a_det | b_det | s_det)]
    common = sorted(set(a_res) & set(b_res) & set(s_res))

    students = []
    for sid in common:
        a = float(a_res[sid]["student_score"] or 0)
        b = float(b_res[sid]["student_score"] or 0)
        s = float(s_res[sid]["student_score"] or 0)
        students.append({"student_id": sid, "name": names.get(sid),
                         "auth": a, "before": b, "after": s,
                         "before_delta": b - a, "after_delta": s - a,
                         "closer": abs(s - a) < abs(b - a)})

    b_cmp = vs_auth(a_det, b_det, qids)
    s_cmp = vs_auth(a_det, s_det, qids)

    paper_match = {
        "before_exact": sum(1 for r in students if r["before_delta"] == 0),
        "after_exact": sum(1 for r in students if r["after_delta"] == 0),
        "before_abs_total": round(sum(abs(r["before_delta"]) for r in students), 1),
        "after_abs_total": round(sum(abs(r["after_delta"]) for r in students), 1),
        "before_mean_abs": round(statistics.fmean(abs(r["before_delta"]) for r in students), 2),
        "after_mean_abs": round(statistics.fmean(abs(r["after_delta"]) for r in students), 2),
        "after_closer": sum(1 for r in students if r["closer"]),
        "before_closer": sum(1 for r in students if abs(r["before_delta"]) < abs(r["after_delta"])),
        "tie": sum(1 for r in students if abs(r["before_delta"]) == abs(r["after_delta"])),
    }

    report = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "sides": {
            "auth": {"db": str(AUTH_DB), "desc": "现有成绩（其他AI批改+人工复核调整），权威基准"},
            "before": {"db": str(BEFORE_DB), "desc": "我上次批改（优化前管线），写回后已备份"},
            "after": {"db": str(AFTER_DB), "desc": "我本次按优化后管线的隔离重跑"},
        },
        "distribution": {
            "auth": dist(float(r["student_score"] or 0) for r in a_res.values()),
            "before": dist(float(r["student_score"] or 0) for r in b_res.values()),
            "after": dist(float(r["student_score"] or 0) for r in s_res.values()),
            "auth_review_flagged": sum(1 for r in a_res.values() if r["needs_human_review"]),
            "before_review_flagged": sum(1 for r in b_res.values() if r["needs_human_review"]),
            "after_review_flagged": sum(1 for r in s_res.values() if r["needs_human_review"]),
        },
        "paper_vs_auth": paper_match,
        "detail_vs_auth": {"before": b_cmp, "after": s_cmp},
        "students": sorted(students, key=lambda r: -abs(r["after_delta"])),
    }
    return report


def md(rep):
    L = []
    L.append("# 三方比对：权威（人工复核后） vs 上次批改（优化前） vs 本次重跑（优化后）")
    L.append("")
    L.append(f"生成时间：{rep['generated_at']}  |  场次：session 4（第三周学情反馈）")
    L.append("")
    L.append("> 基准 = 项目库现有成绩（其他 AI 批改 + 人工复核，权威）。")
    L.append("> 修改前 = 我上次批改结果（备份库 codex_session4_full_regrade_20260915/backup）。")
    L.append("> 修改后 = 我按最新优化管线的隔离重跑（沙箱库，未写回项目）。")
    L.append("")
    d = rep["distribution"]
    L.append("## 1. 总分分布")
    L.append("")
    L.append("| 指标 | 权威 | 修改前 | 修改后 |")
    L.append("|---|---|---|---|")
    for k, lab in (("count","份数"),("sum","总分"),("min","最低"),("max","最高"),("avg","均分"),("median","中位")):
        L.append(f"| {lab} | {d['auth'][k]} | {d['before'][k]} | {d['after'][k]} |")
    L.append(f"| 挂复核卷数 | {d['auth_review_flagged']} | {d['before_review_flagged']} | {d['after_review_flagged']} |")
    L.append("")
    p = rep["paper_vs_auth"]
    L.append("## 2. 卷面总分与权威的吻合度")
    L.append("")
    L.append("| 指标 | 修改前 | 修改后 |")
    L.append("|---|---|---|")
    L.append(f"| 总分与权威完全一致 | {p['before_exact']} 份 | {p['after_exact']} 份 |")
    L.append(f"| 总分绝对偏差合计 | {p['before_abs_total']} | {p['after_abs_total']} |")
    L.append(f"| 每卷平均绝对偏差 | {p['before_mean_abs']} | {p['after_mean_abs']} |")
    L.append("")
    L.append(f"逐卷看：修改后比修改前更接近权威 **{p['after_closer']}** 份，修改前更接近 **{p['before_closer']}** 份，打平 **{p['tie']}** 份。")
    L.append("")
    L.append("## 3. 题目级明细与权威的吻合度")
    L.append("")
    for side, lab in (("before","修改前"),("after","修改后")):
        c = rep["detail_vs_auth"][side]
        L.append(f"- **{lab}**：{c['exact']}/1190 与权威一致（{round(c['exact']/11.9,1)}%），差异 {c['diff']} 条（高 {c['up']} / 低 {c['down']}），绝对偏差合计 {c['abs_delta']}，净偏差 {c['signed_delta']:+g}")
    L.append("")
    L.append("### 各题与权威的绝对偏差（越小越接近人工结果）")
    L.append("")
    L.append("| 题号 | 修改前 |Δ|合计 | 修改后 |Δ|合计 | 修改前一致数 | 修改后一致数 |")
    L.append("|---|---|---|---|---|")
    for qid, qb in rep["detail_vs_auth"]["before"]["per_question"].items():
        qa = rep["detail_vs_auth"]["after"]["per_question"][qid]
        L.append(f"| {qid} | {qb['abs_delta']} | {qa['abs_delta']} | {qb['exact']}/85 | {qa['exact']}/85 |")
    L.append("")
    L.append("## 4. 每生三方总分（按 |修改后Δ| 降序列出差异最大的前 20）")
    L.append("")
    L.append("| 学生 | 权威 | 修改前 | 修改后 | 前-权威 | 后-权威 |")
    L.append("|---|---|---|---|---|---|")
    for r in rep["students"][:20]:
        L.append(f"| {r['name'] or r['student_id']} | {r['auth']} | {r['before']} | {r['after']} | {r['before_delta']:+g} | {r['after_delta']:+g} |")
    L.append("")
    L.append("---")
    L.append("说明：三方比对只做只读统计，未对项目数据库做任何写入；权威成绩为最终结果。")
    L.append("注意：两次重跑的模型侧判读大部分来自同一份视觉判读记录（重放翻译），差异主要反映管线规则变化（评分细则口径、校验、升级复核路径）。")
    return "\n".join(L)


if __name__ == "__main__":
    rep = main()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "three_way.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT_DIR / "REPORT_3WAY.md").write_text(md(rep), encoding="utf-8")
    d = rep["distribution"]; p = rep["paper_vs_auth"]
    print("dist auth/before/after:", d["auth"]["avg"], d["before"]["avg"], d["after"]["avg"])
    print("paper exact:", p["before_exact"], "->", p["after_exact"], "| abs total:", p["before_abs_total"], "->", p["after_abs_total"], "| closer after/before/tie:", p["after_closer"], p["before_closer"], p["tie"])
    for side in ("before","after"):
        c = rep["detail_vs_auth"][side]
        print(side, "detail exact:", c["exact"], "diff:", c["diff"], "abs:", c["abs_delta"], "signed:", c["signed_delta"])
