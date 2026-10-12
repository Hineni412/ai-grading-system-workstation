"""试卷评价的只读汇总、五维评分与整题标记。"""
import statistics as st

WEIGHTS = {"coverage": 20, "time_fit": 15, "distribution": 15,
           "basic_hits": 35, "discrimination": 15}
NAMES = {"coverage": "典型题覆盖", "time_fit": "题量与时间", "distribution": "分数分布",
         "basic_hits": "易错暴露", "discrimination": "题目区分"}
HARD_TYPE = 7.0         # 题型常见难度（期中期末该题型各题题库难度中位数）≥7 视为偏难，漏选不扣分
MIN_SECTION_ITEMS = 2   # 本卷不少于 2 个评分项的小节才做覆盖对照
TAIL_SHARE = 0.15       # 末尾连续空白达满分 15% 视为疑似没做完
BASIC_MAX = 4.0         # 题库预估难度 < 4 为基础题


def clamp(v):
    return max(0.0, min(100.0, v))


def ramp(x, zero, full):
    """x 从 zero 线性到 full 时得分 0→100（zero 可大于 full）。"""
    if zero == full:
        return 100.0
    return clamp(100.0 * (x - zero) / (full - zero))


def coverage(items, cov):
    """每个小节按本卷实际选了 k 个题型计：选中题型的出现套数之和，
    比上该小节可选（非偏难，或本卷已选）题型中最高频 k 个的套数之和。
    只看已选名额是否给了高频题型，不要求塞满全部典型题。"""
    secs, num, den, missing = [], 0.0, 0.0, []
    for sec in cov:
        if sec.get("chapter") is None or len(sec["items"]) < MIN_SECTION_ITEMS:
            continue
        rows = []
        for t in sorted(sec["types"], key=lambda t: -int(t.get("group_count") or 0)):
            if int(t.get("group_count") or 0) < 2:
                continue
            credit = 1.0 if t["covered_by"] else (0.5 if t["secondary_by"] else 0.0)
            d = t.get("difficulty")
            rows.append({"key": t["key"], "name": t["name"], "group_count": int(t.get("group_count") or 0),
                         "credit": credit, "difficulty": d, "hard": d is not None and d >= HARD_TYPE})
        picked = [r for r in rows if r["credit"] > 0]
        if not picked:
            continue
        ideal = [r for r in rows if r["credit"] > 0 or not r["hard"]][:len(picked)]
        s_num = sum(r["group_count"] * r["credit"] for r in picked)
        s_den = sum(r["group_count"] for r in ideal)
        ratio = min(1.0, s_num / s_den) if s_den else 1.0
        floor = min(r["group_count"] for r in picked)
        for r in rows:
            r["shown"] = r["credit"] > 0 or r["group_count"] > floor
            r["skipped"] = r["credit"] == 0 and r["group_count"] > floor and not r["hard"]
        missing += [r for r in rows if r["skipped"]]
        num += ratio * s_den
        den += s_den
        secs.append({"section": sec["label"], "floor": floor, "ratio": round(ratio, 3), "types": rows})
    if not den:
        return None, {"sections": secs, "missing": []}
    missing.sort(key=lambda r: -r["group_count"])
    return round(100 * num / den), {"sections": secs, "missing": [r["name"] for r in missing][:3]}


def time_fit(order, max_by_id, scores_by_student, blank_ids):
    full = sum(max_by_id.values())
    ranked = sorted(scores_by_student, key=lambda sid: sum(scores_by_student[sid].values()))
    n = len(ranked)
    mid = ranked[n // 4: n - n // 4]
    unfinished = 0
    tails = []
    for sid in mid:
        run = 0.0
        for iid in reversed(order):
            if sid in blank_ids.get(iid, ()):
                run += max_by_id[iid]
            else:
                break
        tails.append(run)
        unfinished += run >= TAIL_SHARE * full
    share = unfinished / len(mid) if mid else 0.0
    score = ramp(share, 0.40, 0.05)
    last = order[-1]
    return round(score), {"mid_students": len(mid), "unfinished": unfinished, "share": round(share, 3),
                          "last_item": last, "last_item_blank_mid": sum(sid in blank_ids.get(last, ()) for sid in mid)}


def distribution(totals, full):
    rates = [t / full for t in totals]
    mean, sd = st.fmean(rates), st.pstdev(rates)
    mean_s = 100.0 if 0.60 <= mean <= 0.80 else (ramp(mean, 0.40, 0.60) if mean < 0.60 else ramp(mean, 0.95, 0.80))
    sd_s = 100.0 if 0.12 <= sd <= 0.25 else (ramp(sd, 0.05, 0.12) if sd < 0.12 else ramp(sd, 0.40, 0.25))
    ceil = sum(r >= 0.95 for r in rates) / len(rates)
    floor = sum(r < 0.40 for r in rates) / len(rates)
    ext_s = clamp(100 - 200 * max(0, ceil - 0.10) - 200 * max(0, floor - 0.10))
    score = (mean_s + sd_s + ext_s) / 3
    bins = [0] * 10
    for r in rates:
        bins[max(0, min(9, int(r * 10 + 1e-9)))] += 1
    return round(score), {"mean": round(mean, 3), "sd": round(sd, 3), "ceiling_share": round(ceil, 3),
                          "bins": bins,
                          "floor_share": round(floor, 3),
                          "parts": {"mean": round(mean_s), "spread": round(sd_s), "extremes": round(ext_s)}}


def basic_hits(items):
    basic = [it for it in items if it["predicted_difficulty"] is not None and it["predicted_difficulty"] < BASIC_MAX]
    pts = sum(it["max"] for it in basic)
    if not pts:
        return None, {"basic_items": [], "hit_items": [], "too_easy": [], "weak": []}
    hit = [it for it in basic if 0.40 <= it["rate"] <= 0.85 and (it["r_rest"] or 0) >= 0.30]
    return round(100 * sum(it["max"] for it in hit) / pts), {
        "basic_items": [it["id"] for it in basic], "hit_items": [it["id"] for it in hit],
        "too_easy": [it["id"] for it in basic if it["rate"] > 0.85],
        "weak": [it["id"] for it in basic if it not in hit and it["rate"] <= 0.85]}


def discrimination(items):
    def g(r):
        r = r or 0
        return 1.0 if r >= 0.4 else 0.8 if r >= 0.3 else 0.4 if r >= 0.2 else 0.0
    pts = sum(it["max"] for it in items)
    score = 100 * sum(it["max"] * g(it["r_rest"]) for it in items) / pts
    return round(score), {"low": [it["id"] for it in items if (it["r_rest"] or 0) < 0.3]}


def evaluate(items, cov, order, max_by_id, scores_by_student, blank_ids):
    totals = [sum(v.values()) for v in scores_by_student.values()]
    full = sum(max_by_id.values())
    results = {
        "coverage": coverage(items, cov),
        "time_fit": time_fit(order, max_by_id, scores_by_student, blank_ids),
        "distribution": distribution(totals, full),
        "basic_hits": basic_hits(items),
        "discrimination": discrimination(items),
    }
    dims = [{"key": k, "name": NAMES[k], "weight": WEIGHTS[k], "score": s, "detail": d}
            for k, (s, d) in results.items()]
    used = [d for d in dims if d["score"] is not None]
    total = round(sum(d["score"] * d["weight"] for d in used) / sum(d["weight"] for d in used)) if used else None
    return {"total": total, "dimensions": dims}


def pearson(a, b):
    if len(a) < 3:
        return None
    ma, mb = st.fmean(a), st.fmean(b)
    sa = sum((x - ma) ** 2 for x in a) ** .5
    sb = sum((y - mb) ** 2 for y in b) ** .5
    return round(sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb), 3) if sa and sb else None


def quadrant(rate, r):
    return ("good" if (r or 0) >= .4 else "free") if rate >= .7 else (
        "high" if (r or 0) >= .4 else "suspect")


def predicted_flag(difficulty, rate):
    if difficulty is None:
        return "none"
    if difficulty < 4 and rate < .6:
        return "harder"
    if (4 <= difficulty < 7 and rate >= .85) or (difficulty >= 7 and rate >= .7):
        return "easier"
    return "fit"


def review_lists(items, dimensions):
    basic = next(d["detail"] for d in dimensions if d["key"] == "basic_hits")
    weak = set(basic["weak"])
    suspect = [item for item in items if item["quadrant"] == "suspect"]
    flagged = {item["id"] for item in suspect} | weak
    candidates = suspect + [item for item in items if item["id"] in weak and item not in suspect]
    fix = []
    for item in candidates[:3]:
        if item["quadrant"] == "suspect":
            reason = ("区分度略低（靠近误差范围）：可抽查题面或批改" if (item["r_rest"] or 0) >= .3
                      else "难度中等却分不开强弱：查题面或抽查批改")
        else:
            reason = ("基础题却很少人做对：查是否超纲或还没教到" if item["rate"] < .4
                      else "基础题分不开强弱")
        fix.append({"id": item["id"], "reason": reason})
    keep_items = [item for item in items if item["id"] in basic["hit_items"] and item["id"] not in flagged]
    keep_items.sort(key=lambda item: -(item["errors"][0]["count"] if item["errors"] else item["lost_students"]))
    keep = [{"id": item["id"], "reason": (
        f'{item["errors"][0]["count"]}人同错：{item["errors"][0]["reason"]}' if item["errors"]
        else "基础题，考出了差距")} for item in keep_items[:3]]
    return {"fix": fix, "keep": keep}, flagged


def _bank_materials(bank_path, bank_ids):
    import sqlite3
    from contextlib import closing
    if bank_path is None or not bank_path.exists():
        return {}, {}
    from question_bank.question_types import is_type_key
    with closing(sqlite3.connect(f"{bank_path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        difficulties = {row["id"]: row["difficulty"] for row in conn.execute("SELECT id, difficulty FROM questions")}
        materials = {}
        for qid in set(bank_ids):
            question = conn.execute("SELECT difficulty, question_text FROM questions WHERE id=?", (qid,)).fetchone()
            if question is None:
                continue
            tags = conn.execute("SELECT tag_type, tag_value FROM question_tags WHERE question_id=?", (qid,)).fetchall()
            scope = conn.execute("SELECT primary_section_id FROM question_scope_summary WHERE question_id=?", (qid,)).fetchone()
            parts = dict(conn.execute("SELECT part_id, formula_difficulty FROM question_part_difficulty_features WHERE question_id=? AND is_active=1", (qid,)))
            materials[qid] = {"difficulty": float(question["difficulty"]) if question["difficulty"] is not None else None, "stem": question["question_text"],
                "section": scope[0] if scope else None, "parts": parts,
                "primary": sorted({v for t, v in tags if t == "knowledge_point" and is_type_key(v)}),
                "secondary": sorted({v for t, v in tags if t == "secondary_type"})}
        return materials, difficulties


def _predicted(item_id, material, evidence_part=None):
    from backend.question_id_contract import question_id_coordinates
    if not material:
        return None, "无题库关联"
    coordinates = question_id_coordinates(item_id)
    part_num = coordinates[1] if coordinates else None
    parts = material["parts"]
    if evidence_part:
        if parts.get(evidence_part) is not None:
            return parts[evidence_part], "小问公式难度（已保存的小问关联）"
        return material["difficulty"], "整题难度" if material["difficulty"] is not None else "无难度"
    part = f"part-{part_num}"
    if part_num and parts.get(part) is not None:
        return parts[part], "小问公式难度（按小问顺序对应）"
    if not part_num and len(parts) == 1 and next(iter(parts.values())) is not None:
        return next(iter(parts.values())), "小问公式难度"
    return material["difficulty"], "整题难度" if material["difficulty"] is not None else "无难度"


def _evidence_parts(data, data_root, bank_map):
    from backend.config_generation.contract import iter_effective_rubric_item_refs
    from question_bank.solution_evidence.evidence_snapshot import load_snapshot, resolve_evidence_part_id

    snapshot = load_snapshot(data_root / "config" / "uploaded", data.session_id) if data_root else None
    questions = (snapshot or {}).get("questions") or {}
    result = {}
    for item_ref, parent_ref, _question, part in iter_effective_rubric_item_refs(data.rubric):
        frozen = questions.get(parent_ref) or {}
        explicit = part.get("evidence_part_id")
        if not explicit and not (frozen.get("usable") and frozen.get("bank_question_id") == bank_map.get(parent_ref)):
            continue
        resolved = resolve_evidence_part_id(part, frozen, item_ref)
        if resolved and (explicit or resolved != str(part.get("part_id") or item_ref)):
            result[item_ref] = resolved
    return result

def _coverage_materials(items, materials, difficulties, read_service):
    from question_bank.taxonomy.curriculum_catalog import load_curriculum_catalog
    section_volumes = {}
    for volume in load_curriculum_catalog()["volumes"]:
        for chapter in volume["chapters"]:
            for section in chapter["sections"]:
                section_volumes[section["id"]] = volume["id"]
                section_volumes[section["knowledge_id"]] = volume["id"]
    touched = {}
    for item in items:
        if section := materials.get(item["bank_question_id"], {}).get("section"):
            touched.setdefault(section, []).append(item["id"])
    sections = {}
    volumes = {section_volumes[s] for s in touched if s in section_volumes}
    for volume in sorted(volumes):
        profile = read_service.chapter_exam_profile(volume)
        for chapter in profile["chapters"]:
            for section in chapter["sections"]:
                sections[section["id"]] = {**section, "chapter": chapter["label"]}
    reference_ids = {ref["question_id"] for section_id in touched if section_id in sections
                     for skill in sections[section_id]["skills"]
                     if skill.get("key") and int(skill.get("group_count") or 0) >= 2
                     for ref in skill.get("typical") or [] if ref.get("question_id")}
    reference_stems = {}
    if reference_ids:
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(f"{read_service.db_path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
            marks = ",".join("?" for _ in reference_ids)
            reference_stems = dict(conn.execute(f"SELECT id, question_text FROM questions WHERE id IN ({marks})", tuple(reference_ids)))
    coverage, coverage_types = [], {}
    for section_id, item_ids in touched.items():
        section = sections.get(section_id)
        if section is None:
            continue
        types = []
        for skill in section["skills"]:
            if not skill.get("key") or int(skill.get("group_count") or 0) < 2:
                continue
            primary = [item["id"] for item in items if skill["key"] in materials.get(item["bank_question_id"], {}).get("primary", [])]
            secondary = [item["id"] for item in items if skill["key"] in materials.get(item["bank_question_id"], {}).get("secondary", [])]
            qids = [qid for stage in (skill.get("cells") or {}).values() for tier in stage.values() for qid in tier]
            diffs = [float(difficulties[qid]) for qid in qids if difficulties.get(qid) is not None]
            typical = []
            for ref in skill.get("typical") or []:
                if not ref.get("question_id"):
                    continue
                if ref["question_id"] in reference_stems:
                    typical.append({"role": ref.get("role") or "参考典型题", "stem": reference_stems[ref["question_id"]] or ""})
            types.append({"key": skill["key"], "name": skill["name"], "group_count": skill["group_count"],
                "difficulty": round(st.median(diffs), 1) if diffs else None,
                "covered_by": primary, "secondary_by": secondary})
            coverage_types[skill["key"]] = {"covered_by": primary, "secondary_by": secondary, "typical": typical}
        coverage.append({"label": section["label"], "chapter": section["chapter"], "items": item_ids, "types": types})
    return coverage, coverage_types, sum(section not in sections for section in touched)


def public_marks(state):
    return {qid: value["mark"] for qid, value in ((state or {}).get("paper_review_marks") or {}).items()
            if isinstance(value, dict) and value.get("mark") in {"keep", "fix"}}

def build_paper_review(db, session_id, reports_dir):
    from collections import Counter
    from backend.class_analysis import (
        ClassAnalysisStateStore, assemble_cause_data, build_cause_inputs,
        known_cause_patterns, apply_cause_results,
    )
    from backend.error_patterns import session_bank_map
    from backend.reporting.analysis_report_exporter import build_class_page_data, _personal_cell_status
    from backend.session_analysis import parent_question_id, question_bank_db_path, infer_data_root
    from question_bank.services.question_read_service import QuestionBankReadService

    store = ClassAnalysisStateStore(reports_dir)
    state = store.load(session_id)
    data = assemble_cause_data(db, session_id)
    questions = [q for q in data.questions if q.attempts and q.max_score > 0]
    result = {"status": "no_data", "session_id": session_id, "title": data.session_name,
        "graded_at": data.graded_at or None, "full_score": sum(q.max_score for q in questions),
        "students": len(data.students), "classes": dict(Counter(s.class_name for s in data.students)),
        "small_sample": len(data.students) < 10, "total": None, "dimensions": [], "items": [],
        "groups": [], "coverage_types": {}, "unlinked_sections": 0,
        "review_lists": {"fix": [], "keep": []}, "marks": public_marks(state)}
    if not data.students or not questions:
        return result
    bank_path = question_bank_db_path(db.db_path)
    bank_map = session_bank_map(bank_path, session_id)
    sources = build_cause_inputs(data, known_patterns=known_cause_patterns(store, bank_path, session_id))
    page = build_class_page_data(data)
    apply_cause_results(page, data, sources, state, session_id=session_id, question_bank_path=bank_path)
    causes = {q["question_id"]: q.get("causes") or [] for q in page["questions"]}
    materials, difficulties = _bank_materials(bank_path, bank_map.values())
    evidence_parts = _evidence_parts(data, infer_data_root(db.db_path), bank_map)
    records = {student.student_id: {record.question_id: record for record in student.records} for student in data.students}
    # 快照只纳入评分完整的参考学生。缺少某项的学生不补为满分或空白。
    scores = {sid: {q.question_id: row[q.question_id].score for q in questions if q.question_id in row}
              for sid, row in records.items()}
    totals = {sid: sum(row.values()) for sid, row in scores.items()}
    siblings = {}
    for q in questions:
        siblings.setdefault(parent_question_id(q.question_id), []).append(q.question_id)
    classes = {name: [s.student_id for s in data.students if s.class_name == name] for name in result["classes"]}
    items = []
    for q in questions:
        iid, parent = q.question_id, parent_question_id(q.question_id)
        ids = [sid for sid, row in scores.items() if iid in row]
        awarded = [scores[sid][iid] for sid in ids]
        rest = [totals[sid] - sum(scores[sid].get(part, 0) for part in siblings[parent]) for sid in ids]
        bank_id = bank_map.get(parent)
        pred, pred_source = _predicted(iid, materials.get(bank_id), evidence_parts.get(iid))
        errors = sorted(({"reason": c["reason"], "category": c.get("category"), "count": int(c.get("count") or 0)}
                         for c in causes.get(iid, []) if c.get("kind") == "error"), key=lambda c: -c["count"])[:5]
        lost = sum(score < q.max_score - 1e-9 for score in awarded)
        typical = errors[0] if errors and errors[0]["count"] >= 5 and errors[0]["count"] >= .3 * lost else None
        rate = round(sum(awarded) / (len(ids) * q.max_score), 4)
        r = pearson(awarded, rest)
        class_rates = {}
        for name, student_ids in classes.items():
            class_ids = [sid for sid in student_ids if iid in scores[sid]]
            class_rates[name] = round(sum(scores[sid][iid] for sid in class_ids) / (len(class_ids) * q.max_score), 4) if class_ids else None
        items.append({"id": iid, "parent_id": parent, "type": q.question_type, "max": q.max_score,
            "stem": q.question_text or q.stem_summary, "answer": q.canonical_answer,
            "bank_question_id": bank_id, "rate": rate, "class_rates": class_rates, "r_rest": r,
            "predicted_difficulty": pred, "predicted_source": pred_source, "quadrant": quadrant(rate, r),
            "pred_flag": predicted_flag(pred, rate), "lost_students": lost, "errors": errors,
            "typical_error": typical, "distractors": None})
    read_service = QuestionBankReadService(bank_path, data_root=infer_data_root(db.db_path)) if bank_path else None
    cov, coverage_types, unlinked = _coverage_materials(items, materials, difficulties, read_service)
    blank_ids = {q.question_id: {sid for sid, row in records.items() if q.question_id in row and _personal_cell_status(row[q.question_id]) == "blank"} for q in questions}
    evaluation = evaluate(items, cov, [q.question_id for q in questions],
                          {q.question_id: q.max_score for q in questions}, scores, blank_ids)
    lists, flagged = review_lists(items, evaluation["dimensions"])
    by_id = {item["id"]: item for item in items}
    groups = []
    for parent, parts in siblings.items():
        if len(parts) < 2:
            continue
        full = sum(by_id[part]["max"] for part in parts)
        ids = [sid for sid, row in scores.items() if all(part in row for part in parts)]
        awarded = [sum(scores[sid][part] for part in parts) for sid in ids]
        rate = round(sum(awarded) / (len(ids) * full), 4) if ids else 0
        r = pearson(awarded, [totals[sid] - score for sid, score in zip(ids, awarded)])
        pred = materials.get(bank_map.get(parent), {}).get("difficulty")
        class_rates = {name: (round(sum(by_id[p]["class_rates"][name] * by_id[p]["max"] for p in parts) / full, 4)
                             if all(by_id[p]["class_rates"][name] is not None for p in parts) else None) for name in classes}
        errors = [{"part_id": part, "reason": by_id[part]["typical_error"]["reason"], "count": by_id[part]["typical_error"]["count"]}
                  for part in parts if by_id[part]["typical_error"]]
        groups.append({"id": parent, "parts": parts, "max": full, "rate": rate, "class_rates": class_rates,
            "r_rest": r, "predicted_difficulty": pred, "quadrant": quadrant(rate, r),
            "pred_flag": predicted_flag(pred, rate), "top_error": max(errors, key=lambda e: e["count"]) if errors else None,
            "flagged": any(part in flagged for part in parts)})
    result.update(status="ready", items=items, groups=groups, coverage_types=coverage_types,
                  unlinked_sections=unlinked, review_lists=lists, **evaluation)
    return result


def save_paper_review_mark(db, session_id, parent_id, mark, reports_dir):
    from datetime import datetime
    from backend.class_analysis import ClassAnalysisStateStore
    from backend.error_patterns import session_bank_map
    from backend.session_analysis import assemble_session_analysis, parent_question_id, question_bank_db_path

    data = assemble_session_analysis(db, session_id, page_only=True)
    if parent_id not in {parent_question_id(q.question_id) for q in data.questions}:
        raise ValueError("本场不存在该父级题号")
    store = ClassAnalysisStateStore(reports_dir)
    state = store.load(session_id) or {}
    if state.get("read_error"):
        raise OSError(state["read_error"])
    marks = dict(state.get("paper_review_marks") or {})
    if mark is None:
        marks.pop(parent_id, None)
    else:
        bank_map = session_bank_map(question_bank_db_path(db.db_path), session_id)
        marks[parent_id] = {"mark": mark, "bank_question_id": bank_map.get(parent_id),
                            "updated_at": datetime.now().isoformat(timespec="seconds")}
    return {"marks": public_marks(store.save(session_id, paper_review_marks=marks))}
