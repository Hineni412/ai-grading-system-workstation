"""命题训练原型取数。只读真实数据库，不调用模型，不写业务数据。

输出 tmp_exam_design_prototype/data.js（Git 忽略，只留本机）。用法：
    runtime\\python\\python.exe docs/requests/exam-design-prototype-20261012/build_data.py

考前预估的口径（原型）：
- 已批改考试当作“考前”回测：只用该场之前的考试观测拟合掌握度模型（与 tools/mastery_validation.py
  的前向切分相同），再用本场观测的训练目标关联与难度作模板逐生预测；不读本场作答结果。
- 模拟草稿：题库一份期中卷中第一、二章的题，用全部已有考试拟合；每个小问的关联取整题知识点标签
  （与考试整题回退口径相同），难度取小问公式难度，缺失时取整题难度。
- 评分项预估得分率 = 各生该项各判定点预测做对概率的平均，再对学生取平均（判定点等权）。
"""
import json
import sqlite3
import statistics as st
import sys
import time
from collections import defaultdict
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
DATA = ROOT / "user_data"
VOLUME = "bnu24-math-g8-upper"
DRAFT_PAPER = 45
DRAFT_CHAPTERS = ("1", "2")
OUT = ROOT / "tmp_exam_design_prototype" / "data.js"


def ro(path):
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def mean(values):
    values = [v for v in values if v is not None]
    return round(st.fmean(values), 4) if values else None


def section_labels():
    from question_bank.taxonomy.curriculum_catalog import load_curriculum_catalog
    labels = {}
    for volume in load_curriculum_catalog()["volumes"]:
        for chapter in volume["chapters"]:
            for section in chapter["sections"]:
                text = str(section.get("label") or section["knowledge_id"])
                number, _, rest = text.partition(" ")
                label = f'{chapter["order"]}.{number} {rest}' if number.isdigit() and rest else text
                for key in (section["id"], section["knowledge_id"]):
                    labels[key] = label
    return labels


def accuracy(papers):
    """系统预估与两种朴素基准的逐项平均绝对误差；基准只用本场之前考试的实际得分率。"""
    from math import sqrt
    done = []
    for paper in papers:
        items = [it for it in paper["items"] if it["pred"]]
        if paper["kind"] != "session" or not items:
            done.append(paper)
            continue
        prior = [i for p in done if p["kind"] == "session" for i in p["items"]]
        overall = sum(i["actual"]["all"] * i["max"] for i in prior) / sum(i["max"] for i in prior)
        band = lambda d: 0 if d < 4 else 1 if d < 7 else 2
        bands = {b: [i["actual"]["all"] for i in prior if i["difficulty"] is not None and band(i["difficulty"]) == b] for b in (0, 1, 2)}
        guess = lambda it: st.fmean(bands[band(it["difficulty"])]) if it["difficulty"] is not None and bands[band(it["difficulty"])] else overall
        p, a = [it["pred"]["all"] for it in items], [it["actual"]["all"] for it in items]
        mp, ma = st.fmean(p), st.fmean(a)
        sp, sa = sqrt(sum((x - mp) ** 2 for x in p)), sqrt(sum((y - ma) ** 2 for y in a))
        paper["accuracy"] = {"items": len(items), "system_mae": mean(abs(x - y) for x, y in zip(p, a)),
            "prior_mean_mae": mean(abs(overall - y) for y in a), "prior_band_mae": mean(abs(guess(it) - y) for it, y in zip(items, a)),
            "correlation": round(sum((x - mp) * (y - ma) for x, y in zip(p, a)) / (sp * sa), 3) if sp and sa else None}
        done.append(paper)


def fit(parent, observations, resolver):
    from question_bank.mastery.current import CURRENT_MASTERY_PARAMETERS
    from question_bank.mastery.model import MasteryModel
    from question_bank.recommendation.target_matching import target_index
    kinds = target_index(resolver)
    dynamic = {key for key, item in kinds.items() if item.get("kind") in {"topic", "skill"}}
    return MasteryModel(parent, CURRENT_MASTERY_PARAMETERS, dynamic_nodes=dynamic).fit(observations)


def item_predictions(model, templates):
    """templates: [(student, item_id, observation)] -> {item_id: {student: p}}"""
    points = defaultdict(lambda: defaultdict(list))
    for student, item_id, obs in templates:
        points[item_id][student].append(model.predict(obs))
    return {iid: {sid: st.fmean(ps) for sid, ps in by_student.items()} for iid, by_student in points.items()}


def summarize(by_student, class_of):
    classes = defaultdict(list)
    for sid, p in by_student.items():
        classes[class_of.get(sid, "未分班")].append(p)
    return {"all": mean(by_student.values()), "classes": {k: mean(v) for k, v in sorted(classes.items())}}


def bank_rows(bank, ids):
    from question_bank.question_types import is_type_key
    rows = {}
    for qid in ids:
        q = bank.execute("SELECT id, question_number, question_type, question_text, difficulty FROM questions WHERE id=?", (qid,)).fetchone()
        if q is None:
            continue
        tags = bank.execute("SELECT tag_type, tag_value FROM question_tags WHERE question_id=?", (qid,)).fetchall()
        scope = bank.execute("SELECT primary_section_id FROM question_scope_summary WHERE question_id=?", (qid,)).fetchone()
        parts = bank.execute("SELECT part_id, formula_difficulty FROM question_part_difficulty_features WHERE question_id=? AND is_active=1 ORDER BY part_id", (qid,)).fetchall()
        rows[qid] = {"number": q["question_number"], "type": q["question_type"], "stem": (q["question_text"] or "")[:160],
            "difficulty": q["difficulty"], "section": scope[0] if scope else None,
            "knowledge": [v for t, v in tags if t == "knowledge_point"],
            "primary": [v for t, v in tags if t == "knowledge_point" and is_type_key(v)],
            "parts": [(p["part_id"], p["formula_difficulty"]) for p in parts]}
    return rows


def main():
    start = time.perf_counter()
    from backend.error_patterns import session_bank_map
    from backend.reporting.paper_review import _bank_materials, _coverage_materials, coverage
    from backend.reporting.paper_review import build_paper_review
    from backend.repositories.grading_database import open_grading_repositories
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.mastery.model import week_of
    from question_bank.services.question_read_service import QuestionBankReadService
    from tools.mastery_validation import load_semester

    grading_path, bank_path = DATA / "databases/grading_system.db", DATA / "databases/question_bank.db"
    observations, parent, sessions, _ = load_semester(DATA.resolve(), VOLUME)
    resolver = CurrentKnowledgeResolver.from_active_database(bank_path)
    labels = section_labels()
    name = lambda key: (resolver.node(key).display_name if resolver.node(key) else key)
    papers, history = [], defaultdict(list)

    with closing(ro(grading_path)) as gconn, closing(ro(bank_path)) as bank:
        db = open_grading_repositories(grading_path, external_connection=gconn)
        class_of = {str(r["id"]): r["class_name"] for r in gconn.execute("SELECT id, class_name FROM students")}
        titles = {int(r["id"]): r["session_name"] for r in gconn.execute("SELECT id, session_name FROM grading_sessions")}
        reviews = {s: build_paper_review(db, s, DATA / "reports") for s in sessions}
        bank_maps = {s: session_bank_map(bank_path, s) for s in sessions}
        for s in sessions:
            for item in reviews[s]["items"]:
                if item["bank_question_id"]:
                    history[item["bank_question_id"]].append({"session_id": s, "title": titles.get(s), "item": item["id"],
                        "rate": item["rate"], "r_rest": item["r_rest"], "mark": reviews[s]["marks"].get(item["parent_id"])})

        for index, s in enumerate(sessions):
            review = reviews[s]
            test = [o for o in observations if o.get("session") == s]
            cutoff = min(o["occurred_at"] for o in test)
            train = [o for o in observations if o.get("session") in sessions[:index]
                     or (o["source"] == "training" and o["occurred_at"] < cutoff)]
            model = fit(parent, train, resolver) if train else None
            trained_students = {o["student"] for o in train}
            preds = item_predictions(model, [(o["student"], o["item"][1], o) for o in test]) if model else {}
            actual_points = defaultdict(lambda: defaultdict(list))
            for o in test:
                actual_points[o["item"][1]][o["student"]].append(o["y"])
            items = []
            for item in review["items"]:
                by_student = preds.get(item["id"], {})
                material = bank_rows(bank, [item["bank_question_id"]]).get(item["bank_question_id"]) if item["bank_question_id"] else None
                links = sorted({k for o in test if o["item"][1] == item["id"] for k in o["links"]})
                if not model:
                    source = "本学期第一场考试，之前没有作答，系统无法按本班预估"
                elif not links:
                    source = "这道题没有挂训练目标，系统无法按本班预估"
                else:
                    source = "按本班掌握度与难度预估"
                items.append({"id": item["id"], "parent_id": item["parent_id"], "type": item["type"], "max": item["max"],
                    "stem": (item["stem"] or "")[:160], "bank_question_id": item["bank_question_id"],
                    "section": labels.get(material["section"]) if material and material["section"] else None,
                    "targets": [name(k) for k in links][:3],
                    "difficulty": item["predicted_difficulty"], "difficulty_source": item["predicted_source"],
                    "pred": summarize(by_student, class_of) if by_student else None, "pred_source": source,
                    "actual": {"all": item["rate"], "classes": item["class_rates"]},
                    "actual_points": summarize({sid: st.fmean(v) for sid, v in actual_points[item["id"]].items()}, class_of)
                        if actual_points.get(item["id"]) else None,
                    "r_rest": item["r_rest"], "quadrant": item["quadrant"],
                    "history": [h for h in history.get(item["bank_question_id"], []) if h["session_id"] != s],
                    "mark": review["marks"].get(item["parent_id"])})
            predicted = [it for it in items if it["pred"]]
            full = sum(it["max"] for it in predicted)
            papers.append({"id": f"session-{s}", "kind": "session", "title": review["title"], "full_score": review["full_score"],
                "students": review["students"], "classes": review["classes"],
                "basis": {"fit_sessions": [titles.get(x) for x in sessions[:index]], "fit_observations": len(train),
                          "students_with_history": len({o["student"] for o in test} & trained_students),
                          "students": len({o["student"] for o in test})},
                "predicted_mean_rate": round(sum(it["pred"]["all"] * it["max"] for it in predicted) / full, 4) if full else None,
                "actual_mean_rate_same_items": round(sum(it["actual"]["all"] * it["max"] for it in predicted) / full, 4) if full else None,
                "actual_mean_rate": round(sum(it["actual"]["all"] * it["max"] for it in items) / sum(it["max"] for it in items), 4),
                "coverage": next(d for d in review["dimensions"] if d["key"] == "coverage"),
                "paper_review_total": review["total"], "items": items})

        accuracy(papers)
        draft_ids = [r["id"] for r in bank.execute(
            "SELECT q.id, s.primary_section_id FROM questions q JOIN question_scope_summary s ON s.question_id=q.id "
            "WHERE q.paper_id=? AND q.deleted_at IS NULL ORDER BY q.id", (DRAFT_PAPER,))
            if any((r["primary_section_id"] or "").endswith(f"_{c}_{n}") for c in DRAFT_CHAPTERS for n in range(1, 9))]
        rows = bank_rows(bank, draft_ids)
        model = fit(parent, observations, resolver)
        week = week_of(max(o["occurred_at"] for o in observations))
        students = sorted({o["student"] for o in observations})
        templates, items = [], []
        for position, qid in enumerate(draft_ids, 1):
            row = rows[qid]
            links = {t.stable_key for k in row["knowledge"] for t in resolver.resolve(k)}
            parts = row["parts"] if len(row["parts"]) > 1 else [(None, row["parts"][0][1] if row["parts"] else None)]
            for part_no, (part_id, part_difficulty) in enumerate(parts, 1):
                iid = f"Q{position}" + (f"(P{part_no})" if part_id else "")
                d = part_difficulty if part_difficulty is not None else row["difficulty"]
                if links and d is not None:
                    for sid in students:
                        templates.append((sid, iid, {"student": sid, "item": ("draft", iid), "week": week, "d": float(d),
                            "links": {k: 1 / len(links) for k in sorted(links)}, "source": "exam"}))
                items.append({"id": iid, "parent_id": f"Q{position}", "type": row["type"], "max": None, "stem": row["stem"],
                    "bank_question_id": qid, "section": labels.get(row["section"]) if row["section"] else None,
                    "targets": [name(k) for k in sorted(links)][:3], "difficulty": d,
                    "difficulty_source": "小问公式难度" if part_difficulty is not None else "整题难度",
                    "pred_source": "按本班掌握度与难度预估" if links and d is not None else "没有训练目标或难度，系统无法按本班预估",
                    "history": history.get(qid, []), "mark": None, "_links": links})
        preds = item_predictions(model, templates)
        taught = {k for o in observations for k in o["links"]}
        for item in items:
            item["pred"] = summarize(preds[item["id"]], class_of) if item["id"] in preds else None
            item["has_direct_evidence"] = bool(item.pop("_links") & taught)
        materials, difficulties = _bank_materials(bank_path, draft_ids)
        read_service = QuestionBankReadService(bank_path, data_root=DATA)
        cov_items = [{"id": it["id"], "bank_question_id": it["bank_question_id"]} for it in items]
        cov, _, _ = _coverage_materials(cov_items, materials, difficulties, read_service)
        score, detail = coverage(cov_items, cov)
        title = bank.execute("SELECT title FROM papers WHERE id=?", (DRAFT_PAPER,)).fetchone()[0]
        papers.insert(0, {"id": "draft", "kind": "draft", "title": "模拟组卷草稿（当前试卷篮为空）",
            "note": f"取自题库《{title}》第一、二章的 {len(draft_ids)} 道题，仅用于原型演示", "full_score": None,
            "students": len(students), "classes": dict(sorted({c: sum(1 for s in students if class_of.get(s) == c) for c in {class_of.get(s, '未分班') for s in students}}.items())),
            "basis": {"fit_sessions": [titles.get(x) for x in sessions], "fit_observations": len(observations),
                      "students_with_history": len(students), "students": len(students)},
            "predicted_mean_rate": mean(it["pred"]["all"] for it in items if it["pred"]),
            "coverage": {"key": "coverage", "name": "典型题覆盖", "score": score, "detail": detail}, "items": items})

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("window.EXAM_DESIGN_DATA = " + json.dumps({"generated_seconds": round(time.perf_counter() - start, 1),
        "papers": papers}, ensure_ascii=False, indent=1) + ";\n", encoding="utf-8")
    for p in papers:
        print(p["id"], p["title"], "items", len(p["items"]), "predicted", sum(1 for i in p["items"] if i["pred"]),
              "pred_mean", p.get("predicted_mean_rate"), "actual_same", p.get("actual_mean_rate_same_items"),
              "accuracy", p.get("accuracy"), "coverage", p["coverage"]["score"], "basis", p["basis"])
    print("seconds", round(time.perf_counter() - start, 1))


if __name__ == "__main__":
    main()
