"""试卷评价的只读汇总与维度评分。"""
import statistics as st

WEIGHTS = {"coverage": 20, "time_fit": 10, "distribution": 15,
           "basic_hits": 30, "discrimination": 15, "independence": 10}
NAMES = {"coverage": "典型题覆盖", "time_fit": "题量与时间", "distribution": "分数分布",
         "basic_hits": "易错暴露", "discrimination": "题目区分", "independence": "题间独立"}
HARD_TYPE = 7.0         # 题型常见难度（期中期末该题型各题题库难度中位数）≥7 视为偏难，漏选不扣分
MIN_SECTION_ITEMS = 2   # 本卷不少于 2 个评分项的小节才做覆盖对照
TAIL_SHARE = 0.15       # 末尾连续空白达满分 15% 视为疑似没做完
BASIC_MAX = 4.0         # 题库预估难度 < 4 为基础题
WEAKER_MARGIN = 0.10    # 建议替换要求候选题型的考前掌握至少低 10 个百分点
DIFF_SLACK = 1.0        # 替换比较限定候选难度在本节已选题型难度 ±1 内


def clamp(v):
    return max(0.0, min(100.0, v))


def ramp(x, zero, full):
    """x 从 zero 线性到 full 时得分 0→100（zero 可大于 full）。"""
    if zero == full:
        return 100.0
    return clamp(100.0 * (x - zero) / (full - zero))


def coverage(items, cov, priors):
    """同等名额替换规则：本节已选名额不变（只计本小节题目占用的名额，
    其他小节已考的题型不参与比较），没选的题型更高频、难度在本节已选
    范围 ±1 内、且考前掌握不更高（至少低 10 个百分点或缺数据）才算一处
    可替换；被替换的名额不计入选题效率。"""
    secs, num, den = [], 0.0, 0.0
    for sec in cov:
        if sec.get("chapter") is None or len(sec["items"]) < MIN_SECTION_ITEMS:
            continue
        in_section = set(sec["items"])
        rows = []
        for t in sorted(sec["types"], key=lambda t: -int(t.get("group_count") or 0)):
            if int(t.get("group_count") or 0) < 2:
                continue
            primary_here = any(i in in_section for i in t["covered_by"])
            secondary_here = any(i in in_section for i in t["secondary_by"])
            credit = 1.0 if primary_here else (0.5 if secondary_here else 0.0)
            d = t.get("difficulty")
            rows.append({"key": t["key"], "name": t["name"], "group_count": int(t.get("group_count") or 0),
                         "credit": credit, "difficulty": d, "hard": d is not None and d >= HARD_TYPE,
                         "prior": priors.get(t["key"]),
                         "elsewhere": credit == 0 and bool(t["covered_by"] or t["secondary_by"])})
        picked = [r for r in rows if r["credit"] > 0]
        if not picked:
            continue
        ds = [r["difficulty"] for r in picked if r["difficulty"] is not None]
        band = (min(ds) - DIFF_SLACK, max(ds) + DIFF_SLACK) if ds else None

        def feasible(s):
            return (not s["hard"] and (band is None or s["difficulty"] is None
                                       or band[0] <= s["difficulty"] <= band[1]))

        def dominates(s, p):
            return (feasible(s) and s["group_count"] > p["group_count"]
                    and (s["prior"] is None or p["prior"] is None
                         or s["prior"] <= p["prior"] - WEAKER_MARGIN))

        skipped = [r for r in rows if r["credit"] == 0 and not r["elsewhere"]]
        used, replaced, suggestions = set(), set(), []
        for p in sorted(picked, key=lambda r: (r["group_count"], -(r["prior"] or 0))):
            candidates = [s for s in skipped if s["key"] not in used and dominates(s, p)]
            if not candidates:
                continue
            s = min(candidates, key=lambda r: (-r["group_count"],
                                               r["prior"] if r["prior"] is not None else float("inf")))
            used.add(s["key"])
            replaced.add(p["key"])
            suggestions.append({"out_key": p["key"], "out_name": p["name"], "out_count": p["group_count"],
                                "out_prior": p["prior"], "out_difficulty": p["difficulty"],
                                "in_key": s["key"], "in_name": s["name"], "in_count": s["group_count"],
                                "in_prior": s["prior"], "in_difficulty": s["difficulty"],
                                "by_frequency_only": s["prior"] is None or p["prior"] is None})
        floor = min(r["group_count"] for r in picked)
        for r in rows:
            r["shown"] = r["credit"] > 0 or r["group_count"] > floor
            if r["credit"] > 0:
                r["status"] = "replace" if r["key"] in replaced else "picked"
            elif r["elsewhere"]:
                r["status"] = "elsewhere"
            elif r["key"] in used:
                r["status"] = "swap_in"
            elif r["hard"]:
                r["status"] = "hard"
            elif not feasible(r) and r["group_count"] > floor:
                r["status"] = "out_of_range"
            elif r["group_count"] > floor:
                r["status"] = "tradeoff"
            else:
                r["status"] = "low"
        s_num = sum(r["group_count"] * r["credit"] for r in picked if r["key"] not in replaced)
        s_den = sum(r["group_count"] * r["credit"] for r in picked)
        num += s_num
        den += s_den
        secs.append({"section": sec["label"], "floor": floor,
                     "ratio": round(s_num / s_den, 3) if s_den else 1.0,
                     "band": [round(band[0], 1), round(band[1], 1)] if band else None,
                     "types": rows, "suggestions": suggestions})
    if not den:
        return None, {"sections": secs, "suggestions": []}
    all_suggestions = sorted((g for s in secs for g in s["suggestions"]),
                             key=lambda g: -(g["in_count"] - g["out_count"]))
    return round(100 * num / den), {"sections": secs, "suggestions": all_suggestions}


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


def _residual(values, rest):
    """values 对 rest 一元回归的残差；rest 无波动时只做去均值。"""
    vbar, rbar = st.fmean(values), st.fmean(rest)
    var = sum((r - rbar) ** 2 for r in rest)
    beta = sum((r - rbar) * (v - vbar) for r, v in zip(rest, values)) / var if var else 0.0
    return [v - vbar - beta * (r - rbar) for r, v in zip(rest, values)]


def _clusters(pairs):
    """冗余题对做并查集，返回成员数 ≥2 的簇。"""
    root = {}

    def find(x):
        root.setdefault(x, x)
        while root[x] != x:
            root[x] = root[root[x]]
            x = root[x]
        return x

    for a, b in pairs:
        root[find(a)] = find(b)
    clusters = {}
    for a, b in pairs:
        clusters.setdefault(find(a), set()).update((a, b))
    return list(clusters.values())


def _chains(siblings, scores, complete):
    """大题内相邻小问：前问 0 分的学生后问也 0 分的比例异常偏高（提示，不计分）。"""
    flagged = []
    for parent, parts in siblings.items():
        if len(parts) < 2:
            continue
        best = None
        for p, q in zip(parts, parts[1:]):
            zero = [sid for sid in complete if scores[sid][p] <= 1e-9]
            others = [sid for sid in complete if scores[sid][p] > 1e-9]
            if len(zero) < 5 or not others:
                continue
            both = sum(scores[sid][q] <= 1e-9 for sid in zero)
            ch, base = both / len(zero), sum(scores[sid][q] <= 1e-9 for sid in others) / len(others)
            if ch >= 0.8 and ch - base >= 0.5 and (best is None or ch - base > best[0]):
                best = (ch - base, {"parent": parent, "from": p, "to": q,
                                    "zero_from": len(zero), "zero_both": both,
                                    "others_rate": round(base, 3)})
        if best is not None:
            flagged.append(best[1])
    return flagged


def _type_name(key, read_service, cov_names):
    """题型键的显示名：优先题库现行知识节点短名，退回覆盖小节里的题型名。"""
    from question_bank.services.question_skill_index import short_node_name
    resolver = getattr(read_service, "current_knowledge", None) if read_service else None
    node = resolver.node(key) if resolver else None
    return short_node_name(str(node.display_name)) if node is not None else cov_names.get(key)


def independence(questions, siblings, scores, complete, bank_map, materials, type_name):
    """整题两两比较：剔除其余题得分后残差仍明显相关且题型相同，视为重复考查。"""
    parents = [p for p, _ in siblings.items()]
    types = {p: set(materials.get(bank_map.get(p), {}).get("primary") or ()) for p in parents}
    detail = {"pairs": [], "linked_other": [], "overlap_points": 0,
              "share": 0.0, "mean_q3": None,
              "chains": _chains(siblings, scores, complete)}
    if len(complete) < 10 or len(parents) < 3 or sum(bool(t) for t in types.values()) < 2:
        return None, detail
    max_by_id = {q.question_id: q.max_score for q in questions}
    pmax = {p: sum(max_by_id[part] for part in parts) for p, parts in siblings.items()}
    full = sum(pmax.values())
    ptotal = {p: [sum(scores[sid][part] for part in parts) for sid in complete]
              for p, parts in siblings.items()}
    total = [sum(scores[sid].values()) for sid in complete]
    q3 = {}
    for i, a in enumerate(parents):
        for b in parents[i + 1:]:
            rest = [t - pa - pb for t, pa, pb in zip(total, ptotal[a], ptotal[b])]
            q3[(a, b)] = pearson(_residual(ptotal[a], rest), _residual(ptotal[b], rest))
    values = [q for q in q3.values() if q is not None]
    mean_q3 = st.fmean(values) if values else None
    pairs, linked_other = [], []
    for (a, b), q in q3.items():
        if q is None or mean_q3 is None or q - mean_q3 < 0.20:
            continue
        shared = types[a] & types[b]
        if shared:
            pairs.append({"a": a, "b": b, "q3": q,
                          "types": sorted(n for n in (type_name(k) for k in shared) if n)})
        else:
            linked_other.append({"a": a, "b": b, "q3": round(q, 2)})
    pairs.sort(key=lambda e: -e["q3"])
    linked_other.sort(key=lambda e: -e["q3"])
    clusters = _clusters([(e["a"], e["b"]) for e in pairs])
    overlap = sum(sum(pmax[p] for p in c) - max(pmax[p] for p in c) for c in clusters)
    share = overlap / full if full else 0.0
    score = round(ramp(share, 0.30, 0.05))
    detail.update(pairs=[{**e, "q3": round(e["q3"], 2)} for e in pairs],
                  linked_other=linked_other[:3], overlap_points=overlap,
                  share=round(share, 3),
                  mean_q3=round(mean_q3, 3) if mean_q3 is not None else None)
    return score, detail


def reliability(siblings, scores, complete):
    """整题总分的 Cronbach α；学生或整题不足时返回 None。"""
    n, k = len(complete), len(siblings)
    if n < 10 or k < 3:
        return None
    ptotal = [[sum(scores[sid][part] for part in parts) for sid in complete]
              for _, parts in siblings.items()]
    total_var = st.pvariance([sum(scores[sid].values()) for sid in complete])
    if total_var <= 0:
        return None
    alpha = k / (k - 1) * (1 - sum(st.pvariance(t) for t in ptotal) / total_var)
    return {"alpha": round(alpha, 3), "units": k, "students": n}


def order_inversions(questions, siblings, scores, complete):
    """同题型内靠前的整题明显难于靠后的整题（提示，不计分）。"""
    if not complete:
        return []
    max_by_id = {q.question_id: q.max_score for q in questions}
    type_by_id = {q.question_id: q.question_type for q in questions}
    parents = list(siblings.items())
    rate = {p: st.fmean(sum(scores[sid][part] for part in parts) for sid in complete)
            / sum(max_by_id[part] for part in parts) for p, parts in parents}
    ptype = {p: type_by_id.get(parts[0]) for p, parts in parents}
    best = {}
    for i, (a, _) in enumerate(parents):
        for b, _ in parents[i + 1:]:
            if not ptype[a] or ptype[b] != ptype[a]:
                continue
            gap = rate[b] - rate[a]
            if rate[a] + 0.25 < rate[b] and (a not in best or gap > best[a][0]):
                best[a] = (gap, {"earlier": a, "later": b, "type": ptype[a],
                                 "earlier_rate": round(rate[a], 3), "later_rate": round(rate[b], 3)})
    return sorted((e for _, e in best.values()),
                  key=lambda e: -(e["later_rate"] - e["earlier_rate"]))[:5]


def evaluate(items, cov, priors, order, max_by_id, scores_by_student, blank_ids, independence):
    totals = [sum(v.values()) for v in scores_by_student.values()]
    full = sum(max_by_id.values())
    results = {
        "coverage": coverage(items, cov, priors),
        "time_fit": time_fit(order, max_by_id, scores_by_student, blank_ids),
        "distribution": distribution(totals, full),
        "basic_hits": basic_hits(items),
        "discrimination": discrimination(items),
        "independence": independence,
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

def _pre_exam_type_priors(repositories, bank_path, data_root, session_id, data):
    """各题型在本场之前的班级当前掌握度（组卷同款口径，只算本场参考学生）。
    任何一步不可用都返回空表，按考前无数据处理，不阻断页面。"""
    from datetime import timedelta
    import sqlite3
    from integration.diagnosis_profile_service import DiagnosisProfileService
    from question_bank.current_knowledge import CurrentKnowledgeUnavailable
    from question_bank.mastery.current import _parse_datetime, aggregate_current_mastery
    from question_bank.solution_evidence.part_assessments import reading
    session = repositories.sessions.get_grading_session(int(session_id)) or {}
    volume = str(session.get("curriculum_volume_id") or "").strip()
    if not volume or bank_path is None or data_root is None or not data.students:
        return {}
    try:
        exam_scope = {"mode": "semester", "curriculum_volume_id": volume}
        with reading(bank_path) as connection:
            service = DiagnosisProfileService(repositories.db_path, bank_path,
                                              grading_db=repositories,
                                              question_bank_connection=connection,
                                              data_root=data_root)
            session_time = _parse_datetime(
                service.mastery_session_times(exam_scope=exam_scope).get(str(session_id)))
            if session_time is None:
                return {}
            cutoff = session_time - timedelta(seconds=1)  # 严格早于本场，不纳入本场作答
            per_student = service.semester_mastery({"exam_scope": exam_scope},
                                                   as_of=cutoff, evidence_until=cutoff)
        reference = {str(s.student_id) for s in data.students}
        own = {key: item for key, item in per_student.items() if key[0] in reference}
        return {key: round(item.value, 3)
                for key, item in aggregate_current_mastery(own).items()
                if item.value is not None}
    except (CurrentKnowledgeUnavailable, OSError, sqlite3.Error, ValueError, TypeError):
        return {}


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
                    typical.append({"role": ref.get("role") or "参考典型题", "stem": reference_stems[ref["question_id"]] or "",
                                    "question_id": ref["question_id"]})
            types.append({"key": skill["key"], "name": skill["name"], "group_count": skill["group_count"],
                "difficulty": round(st.median(diffs), 1) if diffs else None,
                "covered_by": primary, "secondary_by": secondary})
            coverage_types[skill["key"]] = {"covered_by": primary, "secondary_by": secondary, "typical": typical}
        coverage.append({"label": section["label"], "chapter": section["chapter"], "items": item_ids, "types": types})
    return coverage, coverage_types, sum(section not in sections for section in touched)


_CACHE: dict[int, tuple[tuple, dict]] = {}


def _stamp(path):
    """文件的 (mtime_ns, size)；缺失或不可统计记 None。"""
    if path is None:
        return None
    import os
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _paper_review_signature(db, session_id, reports_dir):
    """试卷评价全部只读输入的廉价签名；任一输入文件变化即重新计算。"""
    from pathlib import Path
    from backend.class_analysis import ClassAnalysisStateStore
    from backend.repositories.access import as_grading_repositories
    from backend.session_analysis import infer_data_root, question_bank_db_path
    from path_manager import resolve_stored_file_path
    from question_bank.solution_evidence.evidence_snapshot import snapshot_path

    repositories = as_grading_repositories(db)
    db_path = repositories.db_path
    bank_path = question_bank_db_path(db_path)
    data_root = infer_data_root(db_path)
    session_row = repositories.sessions.get_grading_session(int(session_id)) or {}
    paths = [db_path, Path(f"{db_path}-wal"), bank_path,
             Path(f"{bank_path}-wal") if bank_path else None,
             ClassAnalysisStateStore(reports_dir)._path(session_id)]
    if session_row.get("rubric_path"):
        paths.append(resolve_stored_file_path(session_row["rubric_path"], data_root=data_root))
    if data_root is not None:
        paths.append(snapshot_path(data_root / "config" / "uploaded", session_id))
    return tuple((str(path), _stamp(path)) if path is not None else None
                 for path in paths) + _code_stamps()


def _code_stamps():
    """定义本计算的代码文件签名；实现更新后旧结果自动失效。"""
    import sys
    import backend.class_analysis
    import backend.reporting.analysis_report_exporter
    import backend.session_analysis
    import integration.diagnosis_profile_service
    import question_bank.mastery.current
    import question_bank.mastery.model
    modules = (sys.modules[__name__], backend.class_analysis,
               backend.session_analysis, backend.reporting.analysis_report_exporter,
               question_bank.mastery.current, question_bank.mastery.model,
               integration.diagnosis_profile_service)
    return tuple((str(getattr(m, "__file__", None)),
                  _stamp(getattr(m, "__file__", None))) for m in modules)


def _disk_store(data_root):
    from pathlib import Path
    from integration.persistent_entries import persistent_entry_store
    return persistent_entry_store(
        Path(data_root) / "cache" / "paper_reviews",
        max_entries=64, max_bytes=64 * 1024 * 1024)


def build_paper_review(db, session_id, reports_dir):
    """先查进程内缓存，再查磁盘持久副本；输入或计算代码变化即重算。"""
    signature = _paper_review_signature(db, session_id, reports_dir)
    cached = _CACHE.get(int(session_id))
    if cached is not None and cached[0] == signature:
        return cached[1]
    from backend.session_analysis import infer_data_root
    data_root = infer_data_root(db.db_path)
    store = _disk_store(data_root) if data_root is not None else None
    key = ("paper_review", int(session_id))
    if store is not None:
        saved = store.get(key, signature)
        if saved is not None:
            _CACHE[int(session_id)] = (signature, saved)
            return saved
    result = _build_paper_review(db, session_id, reports_dir)
    _CACHE[int(session_id)] = (signature, result)
    if store is not None:
        store.put(key, signature, result)
    return result


def _build_paper_review(db, session_id, reports_dir):
    from collections import Counter
    from backend.class_analysis import (
        ClassAnalysisStateStore, assemble_cause_data, build_cause_inputs,
        known_cause_patterns, apply_cause_results,
    )
    from backend.error_patterns import session_bank_map
    from backend.reporting.analysis_report_exporter import build_class_page_data, _personal_cell_status
    from backend.repositories.access import as_grading_repositories
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
        "groups": [], "coverage_types": {}, "unlinked_sections": 0, "reliability": None,
        "review_lists": {"fix": [], "keep": []}}
    if not data.students or not questions:
        return result
    bank_path = question_bank_db_path(db.db_path)
    bank_map = session_bank_map(bank_path, session_id)
    from backend.score_import.context import is_import_session_data_root
    option_only = is_import_session_data_root(infer_data_root(db.db_path), session_id)
    sources = build_cause_inputs(data, known_patterns=known_cause_patterns(store, bank_path, session_id))
    page = build_class_page_data(data)
    apply_cause_results(page, data, sources, state, session_id=session_id,
                        question_bank_path=bank_path, option_only=option_only)
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
    priors = _pre_exam_type_priors(as_grading_repositories(db), bank_path,
                                   infer_data_root(db.db_path), session_id, data)
    blank_ids = {q.question_id: {sid for sid, row in records.items() if q.question_id in row and _personal_cell_status(row[q.question_id]) == "blank"} for q in questions}
    complete = [sid for sid, row in scores.items() if all(q.question_id in row for q in questions)]
    cov_names = {t["key"]: t["name"] for sec in cov for t in sec["types"]}
    ind_score, ind_detail = independence(
        questions, siblings, scores, complete, bank_map, materials,
        lambda key: _type_name(key, read_service, cov_names))
    evaluation = evaluate(items, cov, priors, [q.question_id for q in questions],
                          {q.question_id: q.max_score for q in questions}, scores, blank_ids,
                          (ind_score, ind_detail))
    next(d["detail"] for d in evaluation["dimensions"] if d["key"] == "time_fit")["order_inversions"] = \
        order_inversions(questions, siblings, scores, complete)
    result["reliability"] = reliability(siblings, scores, complete)
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
