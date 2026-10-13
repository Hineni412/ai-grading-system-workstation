"""学情肖像：个人报告中默认收起的本学期汇总卡。

掌握度按本场考试时间截取（见 session_analysis.enrich_personal_knowledge），
只表达本人情况，不与班级比较。
"""
from __future__ import annotations

import re
from typing import Any

from question_bank.mastery.current import recent_trend_improved

STATE_LABEL = {"stable": "较稳定", "allright": "都做对了", "weak": "明显薄弱",
               "unsteady": "还不稳", "watch": "待观察"}
STATE_CLASS = {"stable": "t-stable", "allright": "t-allright", "weak": "t-weak",
               "unsteady": "t-unsteady", "watch": "t-watch"}
DIGEST_ORDER = ("stable", "allright", "unsteady", "weak", "watch")
BAR_ORDER = ("weak", "unsteady", "watch", "allright", "stable")
GOOD_STATES = ("stable", "allright")
B_STATES = ("weak", "unsteady")
MAIN_CAP = 3
PAT_CAP = 3
GOOD_CAP = 6
ERROR_CATEGORIES = ("概念理解", "计算与化简", "审题与条件", "方法与思路",
                    "过程与依据", "书写与规范", "未作答")
BAR_CATEGORIES = ERROR_CATEGORIES[:6]

PORTRAIT_CSS = """
/* ---- 学情肖像 ---- */
details.portrait>summary{cursor:pointer;list-style:none}
details.portrait>summary::-webkit-details-marker{display:none}
details.portrait>summary .ptoggle{float:right;color:var(--accent);font-size:12px;font-weight:400;margin-left:10px}
details.portrait>summary .psum{font-size:16px;font-weight:700}
details.portrait>summary .pdigest{display:block;font-size:12.5px;color:var(--muted);margin-top:2px}
details.portrait[open]>summary{padding-bottom:10px;border-bottom:1px dashed var(--line);margin-bottom:4px}
.psec{padding:12px 0 4px}
.psec+.psec{border-top:1px solid var(--line)}
.psec h3{font-size:14.5px;margin-bottom:8px}
.prow{display:flex;flex-wrap:wrap;gap:6px;align-items:flex-start;padding:4px 0}
.prow .sname{flex:0 0 100%;font-size:12px;color:var(--muted)}
.chip{display:inline-block;border-radius:8px;padding:4px 9px;font-size:12.5px;line-height:1.55;max-width:100%}
.chip .csub{display:block;font-size:11px;opacity:.82}
.chip .trend,.pitem .trend{display:inline-block;font-size:10.5px;border-radius:999px;padding:0 6px;
margin-left:5px;border:1px solid currentColor;vertical-align:1px}
.t-weak{background:var(--bad-soft);color:var(--bad);border:1px solid #eec6c0}
.t-unsteady{background:var(--warn-soft);color:var(--warn);border:1px solid #f0d9ab}
.t-stable{background:var(--good-soft);color:var(--good);border:1px solid #bfe3d2}
.t-allright{background:#edf8f2;color:var(--good);border:1px solid #cdeadb}
.t-watch{background:var(--gray-soft);color:var(--gray);border:1px dashed #c2cbd6}
.legend .lg{display:inline-block;border-radius:6px;padding:0 8px;margin:0 4px 4px 0;font-size:12px}
.pchap-row{display:flex;align-items:center;gap:8px;padding:6px 0;flex-wrap:wrap}
.pchap-row .pchap{margin:0;flex:none;font-size:13px;font-weight:700}
.pchap-row .pbar{flex:1;display:flex;height:10px;border-radius:5px;overflow:hidden;
background:var(--gray-soft);min-width:60px}
.pchap-row .pbar i{display:block;height:100%;border:0;border-radius:0;padding:0}
.pchap-row .ptext{flex:none;font-size:12px;color:var(--muted)}
details.pchap-det{margin:0 0 6px}
details.pchap-det>summary{cursor:pointer;font-size:12px;color:var(--accent)}
.pitem{padding:8px 0;border-top:1px solid var(--line)}
.pitem:first-child{border-top:none}
.tier-badge{display:inline-block;font-size:11px;border-radius:999px;padding:0 8px;
margin-left:6px;vertical-align:1px}
.pchapname{font-size:12px;color:var(--muted);margin-left:8px}
.brow{display:flex;gap:12px;align-items:flex-start}
.brow .bleft{flex:0 0 40%;min-width:0;font-size:13.5px}
.brow .bright{flex:1;min-width:0;font-size:12.5px;color:var(--muted);line-height:1.8}
.bexam{display:inline-block;margin:0 14px 2px 0}
.bexam b{color:var(--ink);font-weight:600}
@media(max-width:560px){.brow{flex-direction:column;gap:2px}.brow .bleft{flex:none}}
details.pwatch{margin-top:6px}
details.pwatch>summary{cursor:pointer;font-size:12.5px;color:var(--accent)}
.cbar{display:grid;grid-template-columns:78px 1fr 44px;gap:2px 8px;align-items:center;
padding:4px 0;font-size:13px}
.cbar .track{height:14px;background:var(--gray-soft);border-radius:7px;overflow:hidden}
.cbar .track i{display:block;height:100%;background:var(--accent);border-radius:7px}
.cbar.zero .track i{background:#c2cbd6}
.cbar .cnt{text-align:right;font-size:12.5px;white-space:nowrap}
.cbar .cbreak{grid-column:2/4;font-size:11.5px;color:var(--muted)}
.cbar.zero{color:var(--muted)}
.pats{margin-top:10px}
.patline{font-size:13px;padding:4px 0;border-top:1px dashed var(--line)}
.patline:first-of-type{border-top:none}
.kstuck{margin-top:12px}
.kstuck h4{font-size:13px;margin:0 0 4px}
.kstuck-block{padding:6px 0;border-top:1px dashed var(--line)}
.kstuck-block>p{margin:0 0 4px;font-size:13px}
.kstuck-line{display:block;font-size:12.5px;line-height:1.7;padding:2px 0}
.kstuck-line .ksgoal{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted)}
.kstuck-line .ksreason{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--bad)}
.kstuck-more>summary{cursor:pointer;font-size:12.5px;color:var(--accent)}
.blankline{font-size:13px;color:var(--muted);margin-top:8px}
@media(max-width:560px){.cbar{grid-template-columns:66px 1fr 40px}}
@media print{
.chip,.pitem,.cbar,.patline,.blankline,.prow,.kstuck-block{break-inside:avoid}
details.kstuck-more{display:contents}
details.kstuck-more>summary{display:none}
.psec h3{break-after:avoid}
details.portrait>summary{break-after:avoid}
}
"""


def _state(tier: Any, obs: int, full: int) -> str:
    if tier == "stable":
        return "stable"
    if obs >= 1 and full == obs:
        return "allright"
    if tier == "weak" and obs >= 2:
        return "weak"
    if tier == "unsteady" and obs >= 2:
        return "unsteady"
    return "watch"


def _week_name(name: Any) -> str:
    m = re.match(r"^(第[0-9一二三四五六七八九十百]+周)", str(name or ""))
    return m.group(1) if m else str(name or "")


def _clean_label(name: Any) -> str:
    leaf = str(name or "").split("｜")[-1].split("|")[-1]
    return re.sub(r"^(?:技能|题型)[·・：:]\s*", "", leaf).strip()


def _lineage(catalog: dict[str, dict], key: str) -> tuple[str, str]:
    chapter = section = ""
    cursor, seen = catalog.get(key) or {}, set()
    while cursor and str(cursor.get("knowledge_key")) not in seen:
        seen.add(str(cursor.get("knowledge_key")))
        if cursor.get("node_kind") == "chapter":
            chapter = str(cursor["knowledge_point"]).split("｜")[-1].split("|")[-1]
        elif cursor.get("node_kind") == "section":
            section = str(cursor["knowledge_point"]).split("｜")[-1].split("|")[-1]
        cursor = catalog.get(str(cursor.get("parent_knowledge_key") or ""), {})
    return chapter, section


def build_portrait_view(data, student, history, error_map, error_history, key_step_repeats=None):
    """汇总本教学学期截至本场的考试与训练证据；有成绩的考试少于两场时不显示。"""
    sessions = [e for e in history if e.get("score") is not None]
    if len(sessions) < 2:
        return None
    mastery = getattr(student, "portrait_mastery", None) or {}
    catalog = {str(i.get("knowledge_key")): i
               for i in data.knowledge_structure.get("catalog", [])}
    session_ids = {int(e["session_id"]) for e in sessions}
    name_by_sid = {int(e["session_id"]): str(e.get("name") or "") for e in sessions}
    targets: list[dict[str, Any]] = []
    for item in data.knowledge_structure.get("catalog", []):
        key = str(item.get("knowledge_key"))
        m = mastery.get(key)
        if m is None:
            continue
        chapter, section = _lineage(catalog, key)
        if not chapter:
            continue
        obs = int(m.get("observation_count") or 0)
        full = int(m.get("full_correct_count") or 0)
        losses: dict[tuple, dict] = {}
        refs = (student.knowledge_mastery.get(key) or {}).get("source_question_refs") or []
        for ref in refs:
            sid = int(ref.get("session_id") or 0)
            if sid not in session_ids or (sid, str(ref.get("question_id") or "")) in losses:
                continue
            score, full_score = ref.get("score_awarded"), ref.get("full_score")
            if (not isinstance(score, (int, float)) or not isinstance(full_score, (int, float))
                    or score >= full_score):
                continue
            losses[sid, str(ref.get("question_id") or "")] = {
                "sid": sid, "session": name_by_sid.get(sid) or str(ref.get("session_name") or ""),
                "question_id": str(ref.get("question_id") or ""),
                "score": score, "full": full_score}
        targets.append({"key": key, "label": _clean_label(item.get("knowledge_point")),
                        "chapter": chapter, "section": section, "obs": obs, "full": full,
                        "trend": m.get("recent_trend"), "losses": list(losses.values()),
                        "state": _state(m.get("tier"), obs, full)})
    if not targets:
        return None

    cat_counts = {cat: 0 for cat in BAR_CATEGORIES}
    by_session = {name: {cat: 0 for cat in BAR_CATEGORIES} for name in name_by_sid.values()}
    categorized: set[tuple] = set()
    seen_cat: set[tuple] = set()

    def _add_evidence(sid, qid, session_name, cat):
        categorized.add((sid, qid))
        if cat not in BAR_CATEGORIES or (sid, qid, cat) in seen_cat:
            return
        seen_cat.add((sid, qid, cat))
        cat_counts[cat] += 1
        if session_name in by_session:
            by_session[session_name][cat] += 1

    for record in student.records:
        if not record.lost:
            continue
        for row in (error_map or {}).get(record.question_id) or []:
            _add_evidence(data.session_id, record.question_id,
                          str(data.session_name), str(row.get("category") or ""))
    for cat, evs in (error_history or {}).get("categories", {}).items():
        for ev in evs:
            sid = int(ev.get("session_id") or 0)
            if sid not in session_ids or sid == data.session_id:
                continue
            _add_evidence(sid, str(ev.get("question_id") or ""),
                          str(ev.get("session_name") or ""), cat)

    pat_hits: dict[str, list] = {}
    for record in student.records:
        if not record.lost:
            continue
        for row in (error_map or {}).get(record.question_id) or []:
            pat = str(row.get("pattern") or "")
            if pat and pat not in ERROR_CATEGORIES:
                hit = (data.session_id, str(data.session_name), record.question_id)
                if hit not in pat_hits.setdefault(pat, []):
                    pat_hits[pat].append(hit)
    for pat, evs in (error_history or {}).get("patterns", {}).items():
        if pat in ERROR_CATEGORIES:
            continue
        for ev in evs:
            sid = int(ev.get("session_id") or 0)
            if sid not in session_ids or sid == data.session_id:
                continue
            hit = (sid, str(ev.get("session_name") or ""), str(ev.get("question_id") or ""))
            if hit not in pat_hits.setdefault(pat, []):
                pat_hits[pat].append(hit)

    counts: dict[str, int] = {}
    for t in targets:
        counts[t["state"]] = counts.get(t["state"], 0) + 1
    chapters = []
    by_chapter: dict[str, dict] = {}
    for t in targets:
        by_chapter.setdefault(t["chapter"], {}).setdefault(t["section"], []).append(t)
    for chapter, sec_map in by_chapter.items():
        chapters.append({"chapter": chapter,
                         "sections": [{"section": s, "targets": ts}
                                      for s, ts in sec_map.items()]})
    return {
        "n_sessions": len(sessions),
        "session_name": str(data.session_name),
        "target_label": {"type": "题型", "mixed": "训练目标"}.get(
            data.knowledge_structure.get("target_kind"), "知识点"),
        "counts": counts, "targets": targets, "chapters": chapters,
        "cat_counts": cat_counts, "by_session": by_session,
        "session_order": [name_by_sid[int(e["session_id"])] for e in sessions],
        "patterns": [(pat, hits) for pat, hits in pat_hits.items()
                     if len({h[0] for h in hits}) >= 2],
        "blank_bits": " · ".join(
            f'{_week_name(e["name"])} {int(e.get("blank") or 0)}/{int(e.get("items") or 0)}'
            for e in sessions if int(e.get("blank") or 0) > 0),
        "lost_total": sum(len(e.get("lost") or []) for e in sessions),
        "categorized": len(categorized),
        "key_step_repeats": key_step_repeats,
    }


def _badge(state: str) -> str:
    return (f'<span class="tier-badge {STATE_CLASS.get(state, "t-watch")}">'
            f'{STATE_LABEL.get(state, state)}</span>')


def _chip(t: dict, esc) -> str:
    trend = (f'<span class="trend">{esc(t["trend"])}</span>' if t.get("trend") else "")
    return (f'<span class="chip {STATE_CLASS.get(t["state"], "t-watch")}">'
            f'<span class="cnm">{esc(t["label"])}{trend}</span>'
            f'<span class="csub">做过 {t["obs"]} 次 · 全对 {t["full"]} 次</span></span>')


def _b_item(t: dict, order_idx: dict, esc, fmt_num, qlabel, qcoords) -> str:
    by_session: dict[int, list] = {}
    for loss in t["losses"]:
        by_session.setdefault(loss["sid"], []).append(loss)
    groups = []
    for sid in sorted(by_session,
                      key=lambda s: order_idx.get(by_session[s][0]["session"], 10 ** 9)):
        losses = sorted(by_session[sid],
                        key=lambda l: qcoords(l["question_id"]) or (10 ** 9, 0))
        qs = " · ".join(f'{qlabel(l["question_id"])} '
                        f'{fmt_num(l["score"])}/{fmt_num(l["full"])}' for l in losses)
        groups.append(f'<span class="bexam" title="{esc(losses[0]["session"])}">'
                      f'<b>{esc(_week_name(losses[0]["session"]))}</b> {esc(qs)}</span>')
    trend = (f'<span class="trend">{esc(t["trend"])}</span>' if t.get("trend") else "")
    return (f'<div class="pitem brow"><div class="bleft"><b>{esc(t["label"])}</b>'
            f'{_badge(t["state"])}{trend}<div class="pchapname">{esc(t["chapter"])}</div></div>'
            f'<div class="bright">{"".join(groups)}</div></div>')


def portrait_card_html(view: dict | None) -> str:
    if not view:
        return ""
    from backend.session_analysis import esc, fmt_num
    from backend.question_id_contract import question_id_coordinates as _qcoords
    from backend.reporting.analysis_report_exporter import (
        _STEP_REASON_BOILERPLATE_RE, _question_display_label)

    tl = view["target_label"]
    n = view["n_sessions"]
    counts = view["counts"]
    digest = " · ".join(f"{STATE_LABEL[s]} {counts[s]}"
                       for s in DIGEST_ORDER if counts.get(s))

    good = [t for t in view["targets"] if t["state"] in GOOD_STATES]
    good.sort(key=lambda t: (0 if t["state"] == "stable" else 1, -t["obs"]))
    strengths = ""
    if good:
        chips = "".join(
            f'<span class="lg {STATE_CLASS[t["state"]]}">{esc(t["label"])}</span>'
            for t in good[:GOOD_CAP])
        more = (f'<span class="meta">等 {len(good)} 项</span>' if len(good) > GOOD_CAP else "")
        strengths = (f'<div class="psec"><h3>做得好的{tl}</h3>'
                     f'<div class="prow">{chips}{more}</div></div>')

    legend = "".join(f'<span class="lg {STATE_CLASS[s]}">{STATE_LABEL[s]}</span>'
                     for s in DIGEST_ORDER)
    chap_rows = []
    for chap in view["chapters"]:
        ts = [t for sec in chap["sections"] for t in sec["targets"]]
        cc: dict[str, int] = {}
        for t in ts:
            cc[t["state"]] = cc.get(t["state"], 0) + 1
        total = sum(cc.values()) or 1
        bar = "".join(f'<i class="{STATE_CLASS[s]}" '
                      f'style="width:{cc.get(s, 0) / total * 100:.1f}%"></i>'
                      for s in BAR_ORDER if cc.get(s))
        ctext = " · ".join(f"{STATE_LABEL[s]} {cc[s]}"
                           for s in BAR_ORDER if cc.get(s))
        rows = "".join(
            f'<div class="prow"><span class="sname">{esc(sec["section"])}</span>'
            + "".join(_chip(t, esc) for t in sorted(
                sec["targets"], key=lambda t: BAR_ORDER.index(t["state"])))
            + "</div>"
            for sec in chap["sections"])
        chap_rows.append(
            f'<div class="pchap-row"><span class="pchap">{esc(chap["chapter"])}</span>'
            f'<span class="pbar">{bar}</span><span class="ptext">{esc(ctext)}</span></div>'
            f'<details class="pchap-det"><summary>看各节{tl}</summary>{rows}</details>')
    section_a = f"""<div class="psec">
<h3>本学期各{tl}情况（截至本场，共 {n} 场考试）</h3>
<div class="legend">{legend}</div>
{''.join(chap_rows)}
<div class="note" style="margin-top:8px">“都做对了”：做过的都对，次数还少；“待观察”：记录还少，不代表不会。</div>
</div>"""

    order_idx = {name: i for i, name in enumerate(view["session_order"])}
    eligible = [t for t in view["targets"]
                if t["state"] in B_STATES and t["losses"]]

    def _rank(t):
        return (1 if recent_trend_improved(t.get("trend")) else 0,
                0 if t["state"] == "weak" else 1,
                -len({l["sid"] for l in t["losses"]}),
                -sum(l["full"] - l["score"] for l in t["losses"]), t["label"])

    repeated = sorted((t for t in eligible
                       if len({l["sid"] for l in t["losses"]}) >= 2), key=_rank)
    single = [t for t in eligible if len({l["sid"] for l in t["losses"]}) < 2]
    main, other = repeated[:MAIN_CAP], repeated[MAIN_CAP:]
    b_rows = [(_b_item(t, order_idx, esc, fmt_num, _question_display_label,
                       _qcoords)) for t in main]
    if not b_rows:
        b_rows = ['<div class="note">目前没有在两场及以上考试反复失分、'
                  f'需要先补的{tl}。</div>']
    b_extra = "".join(
        f'<details class="pwatch"><summary>{title}（{len(items)} 项）</summary>'
        + "".join(_b_item(t, order_idx, esc, fmt_num, _question_display_label,
                          _qcoords) for t in items)
        + "</details>"
        for title, items in ((f"其他反复失分的{tl}", other),
                             ("只在一场考试中失分", single)) if items)
    section_b = f"""<div class="psec">
<h3>最需要先补的{tl}</h3>
{''.join(b_rows)}
{b_extra}
<div class="note" style="margin-top:8px">只列明显薄弱或还不稳、且在两场及以上考试失分的{tl}；整题失分不表示这个{tl}单独造成全部失分。最近一次已做对的排在后面。</div>
</div>"""

    rows = []
    for cat in BAR_CATEGORIES:
        per = [(name, view["by_session"][name][cat])
               for name in view["session_order"]
               if view["by_session"].get(name, {}).get(cat)]
        rows.append((cat, view["cat_counts"].get(cat, 0), per))
    maxc = max([r[1] for r in rows] + [1])
    bars = "".join(
        f'<div class="cbar"><span class="cname">{esc(cat)}</span>'
        f'<span class="track"><i style="width:{n / maxc * 100:.0f}%"></i></span>'
        f'<span class="cnt">{n} 次</span>'
        f'<span class="cbreak">{esc(" · ".join(f"{_week_name(name)} {c}" for name, c in per))}</span></div>'
        for cat, n, per in rows if n)
    pat_lines = [
        f'{pat}：{"；".join(f"{s} {_question_display_label(q)}" for _sid, s, q in sorted(hits, key=lambda h: (order_idx.get(h[1], 10 ** 9), _qcoords(h[2]) or (10 ** 9, 0))))}'
        for pat, hits in view["patterns"]]
    shown, rest = pat_lines[:PAT_CAP], pat_lines[PAT_CAP:]
    more = (f'<details class="pwatch"><summary>另有 {len(rest)} 种</summary>'
            + "".join(f'<div class="patline">{esc(l)}</div>' for l in rest)
            + "</details>" if rest else "")
    pats = ('<div class="pats"><b style="font-size:13px">反复出现的同一错法</b>'
            + "".join(f'<div class="patline">{esc(l)}</div>' for l in shown) + more
            + "</div>") if shown else ""

    def _key_step_item(item: dict) -> str:
        reason = str(item.get("reason") or "").strip()
        return (f'<span class="kstuck-line"><b>{esc(_week_name(item.get("session_name")))} '
                f'{esc(_question_display_label(item.get("question_id")))}</b>'
                f'<span class="ksgoal">{esc(item.get("goal") or "该步要求暂无记录")}</span>'
                + (f'<span class="ksreason">{esc(reason)}</span>'
                   if reason and not _STEP_REASON_BOILERPLATE_RE.match(reason) else "")
                + '</span>')

    key_steps = ""
    if view.get("key_step_repeats") is not None:
        blocks = []
        for entry in sorted(view["key_step_repeats"], key=lambda item: (-int(item.get("fails") or 0), str(item.get("name") or "")))[:3]:
            items = entry.get("items") or []
            lines = "".join(_key_step_item(item) for item in items[:3])
            if len(items) > 3:
                lines += (f'<details class="kstuck-more"><summary>还有 {len(items) - 3} 处</summary>'
                          + "".join(_key_step_item(item) for item in items[3:]) + "</details>")
            blocks.append(f'<section class="kstuck-block"><p><b>{esc(entry.get("name") or entry.get("key"))}：</b>'
                          f'{int(entry.get("fails") or 0)} 道小问独立失分</p>{lines}</section>')
        key_steps = ('<div class="kstuck"><h4>反复卡在哪一步</h4>'
                     + "".join(blocks) + '</div>') if blocks else ""
    blank = (f'<div class="blankline">未作答小问：{esc(view["blank_bits"])}</div>'
             if view["blank_bits"] else "")
    section_c = f"""<div class="psec">
<h3>错因结构</h3>
{bars}
{blank}
{pats}
{key_steps}
<div class="note" style="margin-top:8px">按失分小问计，一题可归入多类。</div>
</div>"""

    return f"""<div class="card">
<details class="portrait">
<summary><span class="ptoggle">展开 / 收起</span>
<span class="psum">学情肖像 · 本学期 {n} 场考试（截至{esc(view["session_name"])}）</span>
<span class="pdigest">{sum(counts.values())} 个{tl}：{esc(digest)}</span></summary>
{strengths}
{section_a}
{section_b}
{section_c}
</details>
</div>"""
