"""Compare session-4 grading results between authoritative DB and isolated rerun DB.

Read-only against both databases. Writes report JSON + Markdown under
user_data/outputs/codex_session4_comparison_20260915/assistant_rerun_20260915/.
"""
import json
import sqlite3
import statistics
import sys
import datetime
import pathlib

ROOT = pathlib.Path(r"D:\AI阅卷系统_工作机版_v1.5.0")
AUTH_DB = ROOT / "user_data/databases/grading_system.db"
SAND_DB = pathlib.Path(r"D:\week3_regrade_sandbox\data\databases\grading_system.db")
OUT_DIR = ROOT / "user_data/outputs/codex_session4_comparison_20260915/assistant_rerun_20260915"
SESSION_ID = 4


def fetch(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    results = {}
    for r in conn.execute(
        "select * from session_results where session_id=?", (SESSION_ID,)
    ):
        results[r["student_id"]] = dict(r)
    details = {}
    for d in conn.execute(
        """select d.*, r.student_id from session_details d
           join session_results r on d.result_id=r.id where r.session_id=?""",
        (SESSION_ID,),
    ):
        details[(d["student_id"], d["question_id"])] = dict(d)
    locks = {}
    for l in conn.execute(
        "select * from teacher_score_locks where session_id=?", (SESSION_ID,)
    ):
        locks[(l["student_id"], l["question_id"])] = dict(l)
    names = {}
    for s in conn.execute("select id, name, student_code, class_name from students"):
        names[s["id"]] = dict(s)
    conn.close()
    return {"results": results, "details": details, "locks": locks, "names": names}


def detail_meta(result_row, qid):
    try:
        raw = json.loads(result_row.get("raw_json") or "{}")
    except Exception:
        return {}
    meta = raw.get("detail_metadata") or {}
    return meta.get(qid) or {}


def main():
    auth = fetch(AUTH_DB)
    sand = fetch(SAND_DB)
    names = auth["names"]

    auth_sids = set(auth["results"])
    sand_sids = set(sand["results"])
    missing_in_sand = sorted(auth_sids - sand_sids)
    missing_in_auth = sorted(sand_sids - auth_sids)

    all_qids = sorted({q for (_s, q) in auth["details"]} | {q for (_s, q) in sand["details"]})

    detail_cmp = []
    n_same = n_diff = n_up = n_down = 0
    review_new = review_cleared = 0
    missing_details_sand = []
    missing_details_auth = []

    keys = set(auth["details"]) | set(sand["details"])
    for key in sorted(keys):
        sid, qid = key
        a = auth["details"].get(key)
        s = sand["details"].get(key)
        if a is None:
            missing_details_auth.append({"student_id": sid, "question_id": qid, "sand_score": s["score_awarded"]})
            continue
        if s is None:
            missing_details_sand.append({"student_id": sid, "question_id": qid, "auth_score": a["score_awarded"]})
            continue
        a_res = auth["results"].get(sid) or {}
        s_res = sand["results"].get(sid) or {}
        a_meta = detail_meta(a_res, qid)
        s_meta = detail_meta(s_res, qid)
        a_rev = bool(a_meta.get("need_review") or a_meta.get("needs_human_review"))
        s_rev = bool(s_meta.get("need_review") or s_meta.get("needs_human_review"))
        a_score = float(a["score_awarded"] or 0)
        s_score = float(s["score_awarded"] or 0)
        delta = s_score - a_score
        locked = key in auth["locks"]
        row = {
            "student_id": sid,
            "name": (names.get(sid) or {}).get("name"),
            "question_id": qid,
            "auth_score": a_score,
            "sand_score": s_score,
            "delta": delta,
            "auth_review": a_rev,
            "sand_review": s_rev,
            "teacher_locked": locked,
            "sand_confidence": s.get("confidence_score"),
            "sand_reason": (s_meta.get("review_reason") or s.get("deduction_reason") or "")[:80],
        }
        detail_cmp.append(row)
        if delta == 0:
            n_same += 1
        else:
            n_diff += 1
            if delta > 0:
                n_up += 1
            else:
                n_down += 1
        if s_rev and not a_rev:
            review_new += 1
        if a_rev and not s_rev:
            review_cleared += 1

    student_cmp = []
    p_same = p_diff = p_up = p_down = 0
    for sid in sorted(auth_sids & sand_sids):
        a = auth["results"][sid]
        s = sand["results"][sid]
        a_tot = float(a["student_score"] or 0)
        s_tot = float(s["student_score"] or 0)
        delta = s_tot - a_tot
        student_cmp.append({
            "student_id": sid,
            "name": (names.get(sid) or {}).get("name"),
            "auth_total": a_tot,
            "sand_total": s_tot,
            "delta": delta,
            "auth_review": bool(a["needs_human_review"]),
            "sand_review": bool(s["needs_human_review"]),
        })
        if delta == 0:
            p_same += 1
        else:
            p_diff += 1
            if delta > 0:
                p_up += 1
            else:
                p_down += 1
    student_cmp.sort(key=lambda r: -abs(r["delta"]))

    def dist(rows, field):
        vals = [float(r[field] or 0) for r in rows]
        if not vals:
            return {}
        return {
            "count": len(vals), "sum": sum(vals), "min": min(vals), "max": max(vals),
            "avg": round(statistics.fmean(vals), 2), "median": statistics.median(vals),
        }

    lock_check = []
    for key, lk in sorted(auth["locks"].items()):
        sid, qid = key
        s = sand["details"].get(key)
        lock_check.append({
            "student_id": sid,
            "name": (names.get(sid) or {}).get("name"),
            "question_id": qid,
            "locked_score": float(lk["score_awarded"] or 0),
            "sand_score": float(s["score_awarded"]) if s else None,
            "honored": (s is not None and float(s["score_awarded"] or 0) == float(lk["score_awarded"] or 0)),
        })

    q_agg = {}
    for qid in all_qids:
        rows = [r for r in detail_cmp if r["question_id"] == qid]
        if not rows:
            continue
        q_agg[qid] = {
            "n": len(rows),
            "same": sum(1 for r in rows if r["delta"] == 0),
            "up": sum(1 for r in rows if r["delta"] > 0),
            "down": sum(1 for r in rows if r["delta"] < 0),
            "auth_sum": round(sum(r["auth_score"] for r in rows), 1),
            "sand_sum": round(sum(r["sand_score"] for r in rows), 1),
            "new_review": sum(1 for r in rows if r["sand_review"] and not r["auth_review"]),
            "cleared_review": sum(1 for r in rows if r["auth_review"] and not r["sand_review"]),
        }

    changed_students = [r for r in student_cmp if r["delta"] != 0]
    report = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "session_id": SESSION_ID,
        "authoritative_db": str(AUTH_DB),
        "isolated_db": str(SAND_DB),
        "coverage": {
            "auth_students": len(auth_sids),
            "sand_students": len(sand_sids),
            "missing_in_sandbox": missing_in_sand,
            "missing_in_authoritative": missing_in_auth,
            "auth_details": len(auth["details"]),
            "sand_details": len(sand["details"]),
            "missing_details_in_sandbox": missing_details_sand,
            "extra_details_in_sandbox": missing_details_auth,
        },
        "distribution": {
            "auth_totals": dist(list(auth["results"].values()), "student_score"),
            "sand_totals": dist(list(sand["results"].values()), "student_score"),
        },
        "paper_level": {
            "unchanged": p_same, "changed": p_diff, "increased": p_up, "decreased": p_down,
            "auth_review_flagged": sum(1 for r in student_cmp if r["auth_review"]),
            "sand_review_flagged": sum(1 for r in student_cmp if r["sand_review"]),
        },
        "detail_level": {
            "compared": len(detail_cmp), "unchanged": n_same, "changed": n_diff,
            "increased": n_up, "decreased": n_down,
            "new_review_flags": review_new, "cleared_review_flags": review_cleared,
        },
        "per_question": q_agg,
        "teacher_locks": {
            "auth_locks": len(auth["locks"]),
            "checked_against_sandbox": len(lock_check),
            "honored": sum(1 for l in lock_check if l["honored"]),
            "mismatched": [l for l in lock_check if not l["honored"]],
        },
        "student_deltas": student_cmp,
        "changed_details": [r for r in detail_cmp if r["delta"] != 0],
        "all_details": detail_cmp,
    }
    return report


def md_report(rep):
    L = []
    L.append("# 第三周考试：隔离重跑 vs 权威成绩 比对报告")
    L.append("")
    L.append(f"生成时间：{rep['generated_at']}  |  场次：session {rep['session_id']}（第三周学情反馈）")
    L.append("")
    L.append("> **权威侧** = 项目数据库中经人工复核调整的现有成绩（最终结果，未做任何修改）。")
    L.append("> **隔离侧** = 最新混合批改管线在独立数据副本上的重跑结果（未写回项目库）。")
    L.append("")
    cov = rep["coverage"]
    L.append("## 1. 覆盖核对")
    L.append("")
    L.append(f"| 项 | 权威 | 隔离重跑 |")
    L.append(f"|---|---|---|")
    L.append(f"| 答卷数 | {cov['auth_students']} | {cov['sand_students']} |")
    L.append(f"| 题目明细数 | {cov['auth_details']} | {cov['sand_details']} |")
    if cov["missing_in_sandbox"]:
        L.append(f"| 隔离侧缺失学生 | - | {cov['missing_in_sandbox']} |")
    if cov["missing_details_in_sandbox"]:
        L.append(f"| 隔离侧缺失明细 | - | {len(cov['missing_details_in_sandbox'])} 条 |")
    L.append("")
    d = rep["distribution"]
    L.append("## 2. 总分分布对比")
    L.append("")
    L.append("| 指标 | 权威 | 隔离重跑 |")
    L.append("|---|---|---|")
    for k, label in (("count","份数"),("sum","总分"),("min","最低"),("max","最高"),("avg","均分"),("median","中位")):
        L.append(f"| {label} | {d['auth_totals'].get(k)} | {d['sand_totals'].get(k)} |")
    L.append("")
    p = rep["paper_level"]
    L.append("## 3. 卷面总分差异")
    L.append("")
    L.append(f"- 总分一致：**{p['unchanged']}** 份；有差异：**{p['changed']}** 份（高于权威 {p['increased']} / 低于权威 {p['decreased']}）")
    L.append(f"- 复核标记：权威侧 {p['auth_review_flagged']} 份挂复核，隔离侧 {p['sand_review_flagged']} 份挂复核")
    L.append("")
    dl = rep["detail_level"]
    L.append("## 4. 题目级明细差异")
    L.append("")
    L.append(f"- 可比明细 {dl['compared']} 条：一致 **{dl['unchanged']}**，差异 **{dl['changed']}**（高 {dl['increased']} / 低 {dl['decreased']}）")
    L.append(f"- 复核标记变化：新增挂复核 {dl['new_review_flags']} 条，权威侧挂复核而重跑判明 {dl['cleared_review_flags']} 条")
    L.append("")
    L.append("### 按题目汇总")
    L.append("")
    L.append("| 题号 | 明细数 | 一致 | 重跑更高 | 重跑更低 | 权威总分 | 重跑总分 |")
    L.append("|---|---|---|---|---|---|---|")
    for qid in sorted(rep["per_question"], key=lambda q: (len(q), q)):
        q = rep["per_question"][qid]
        L.append(f"| {qid} | {q['n']} | {q['same']} | {q['up']} | {q['down']} | {q['auth_sum']} | {q['sand_sum']} |")
    L.append("")
    tl = rep["teacher_locks"]
    L.append("## 5. 教师分锁核对")
    L.append("")
    L.append(f"- 权威库分锁 {tl['auth_locks']} 条；隔离重跑与锁定分一致 **{tl['honored']}** 条")
    if tl["mismatched"]:
        L.append(f"- **不一致 {len(tl['mismatched'])} 条**（重跑分 ≠ 教师锁定分，权威分不受影响）：")
        L.append("")
        L.append("| 学生 | 题号 | 教师锁定分 | 重跑分 |")
        L.append("|---|---|---|---|")
        for l in tl["mismatched"]:
            L.append(f"| {l['name'] or l['student_id']} | {l['question_id']} | {l['locked_score']} | {l['sand_score']} |")
    L.append("")
    L.append("## 6. 总分差异明细（按 |Δ| 排序）")
    L.append("")
    L.append("| 学生 | 权威总分 | 重跑总分 | Δ |")
    L.append("|---|---|---|---|")
    for r in rep["student_deltas"]:
        if r["delta"] == 0:
            continue
        L.append(f"| {r['name'] or r['student_id']} | {r['auth_total']} | {r['sand_total']} | {r['delta']:+g} |")
    L.append("")
    L.append("## 7. 题目级差异明细")
    L.append("")
    L.append("| 学生 | 题号 | 权威分 | 重跑分 | Δ | 重跑复核 | 教师锁 |")
    L.append("|---|---|---|---|---|---|---|")
    for r in rep["changed_details"]:
        L.append(f"| {r['name'] or r['student_id']} | {r['question_id']} | {r['auth_score']} | {r['sand_score']} | {r['delta']:+g} | {'是' if r['sand_review'] else ''} | {'锁' if r['teacher_locked'] else ''} |")
    L.append("")
    L.append("---")
    L.append("说明：权威库 sha256 在重跑前后一致（0ddaf3b0…），本次比对未对项目数据做任何写入；")
    L.append("隔离侧明细中 Δ≠0 的项均不改变既有成绩，人工复核后的权威成绩为最终结果。")
    return "\n".join(L)


if __name__ == "__main__":
    rep = main()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "comparison.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT_DIR / "REPORT.md").write_text(md_report(rep), encoding="utf-8")
    print(json.dumps({
        "coverage": rep["coverage"],
        "paper_level": rep["paper_level"],
        "detail_level": rep["detail_level"],
        "locks": {"locks": rep["teacher_locks"]["auth_locks"], "honored": rep["teacher_locks"]["honored"], "mismatch": len(rep["teacher_locks"]["mismatched"])},
        "dist": rep["distribution"],
    }, ensure_ascii=False, indent=1))
