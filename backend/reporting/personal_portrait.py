"""学情肖像：个人报告中默认展开的本学期汇总卡。

掌握度按本场考试时间截取（见 session_analysis.enrich_personal_knowledge）。
两块内容：「成绩走势」折线（孩子实线与班级平均虚线对照）和
「学过的题型」题片图（按章列出，老问题带 ↻反复出错 / ↑已改正 徽标，
↑已改正 的题片用浅绿底色提示）。
"""
from __future__ import annotations

import json
import re
from typing import Any

STATE_LABEL = {"stable": "较稳定", "allright": "都做对了", "weak": "明显薄弱",
               "unsteady": "还不稳", "watch": "待观察"}
BAR_ORDER = ("weak", "unsteady", "watch", "allright", "stable")
STATE_SORT = {"weak": 0, "unsteady": 1, "watch": 2, "allright": 3, "stable": 4}
CELL_SYM = {"full": "✓", "part": "△", "zero": "✗"}
TRACK_LABEL = {"repeat": "反复出错", "new": "这次新出现",
               "pending": "还没再考", "fixed": "已改正"}

PORTRAIT_CSS = """
/* ---- 学情肖像 ---- */
.pv-sub{font-size:12.5px;color:var(--muted);margin:-6px 0 4px}
.pblock{padding:12px 0 4px}
.pblock+.pblock{border-top:1px solid var(--line)}
.pb-t{font-size:15px;font-weight:700;margin:0 0 8px}
.pvguide{font-size:12px;color:var(--muted);margin:-4px 0 6px}
.pv-svgbox{width:100%;overflow:hidden}
.pv-svg{display:block;max-width:100%}
.pvbar{display:flex;height:12px;border-radius:6px;overflow:hidden;
background:var(--gray-soft);margin:2px 0 8px}
.pvseg{display:block;height:100%}
.pvseg.pv-weak{background:var(--bad)} .pvseg.pv-unsteady{background:var(--warn)}
.pvseg.pv-watch{background:#aeb8c4} .pvseg.pv-allright{background:#7cc4a0}
.pvseg.pv-stable{background:var(--good)}
.pvlg-row{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:12px;
color:var(--muted);margin-bottom:10px}
.pvlg{display:inline-flex;align-items:center;gap:5px}
.pvsw{width:10px;height:10px;border-radius:3px;flex:none}
.pvsw.pv-weak{background:var(--bad)} .pvsw.pv-unsteady{background:var(--warn)}
.pvsw.pv-allright{background:#7cc4a0} .pvsw.pv-stable{background:var(--good)}
.pvsw.pv-watch{background:#fff;border:1px dashed #aeb8c4}
.pvchap{margin:4px 0 8px}
.pvchap-n{font-size:13px;font-weight:600;margin-bottom:5px}
.pvchips{display:flex;flex-wrap:wrap;gap:6px}
.pv-chip{font:inherit;font-size:13px;border-radius:999px;padding:4px 10px;
cursor:pointer;display:inline-flex;align-items:center;gap:4px;
border:1px solid transparent;background:#fff}
.pv-chip.pv-stable,.pv-chip.pv-allright{background:var(--good-soft);color:var(--good);
border-color:#b7d9c7}
.pv-chip.pv-unsteady{background:var(--warn-soft);color:var(--warn);border-color:#ecd3a8}
.pv-chip.pv-weak{background:var(--bad-soft);color:var(--bad);border-color:#e4b3ab}
.pv-chip.pv-watch{background:#fff;color:var(--muted);border:1px dashed #aeb8c4}
.pv-chip.pv-fixed{background:#e9f6ee}
.pv-chip.on{outline:2px solid var(--accent);outline-offset:1px}
.pv-cb{display:inline-flex;align-items:center;justify-content:center;
font-style:normal;font-size:10px;font-weight:700;width:14px;height:14px;
border-radius:50%;flex:none}
.pv-cb.cb-repeat{background:var(--bad);color:#fff}
.pv-cb.cb-fixed{background:var(--good);color:#fff}
.pvlg .pv-cb{width:13px;height:13px;font-size:9px}
.pvlg .pv-cb.cb-fixed{background:#e9f6ee;color:var(--good)}
.pv-chipinfo{font-size:13px;margin:7px 0 2px;line-height:1.6}
.pv-chiptrail{color:var(--muted);margin-top:2px}
.pv-chiplinks{display:flex;flex-wrap:wrap;gap:6px;margin-top:5px}
.pv-qpill{font:inherit;font-size:12px;border-radius:999px;padding:2px 10px;
border:1px solid var(--accent);color:var(--accent);background:#fff;
cursor:pointer;min-height:26px}
@media print{
.pv-chipinfo,.pv-chiplinks{display:none!important}
.pvchap{break-inside:avoid}
.pv-chip,.pvseg,.pvsw,.pv-cb{-webkit-print-color-adjust:exact;print-color-adjust:exact}
}
"""


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def build_portrait_view(data, student, history):
    """本学期截至本场的汇总；有成绩的考试少于两场时不显示。"""
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
        targets.append({"key": key, "label": _clean_label(item.get("knowledge_point")),
                        "chapter": chapter, "section": section, "obs": obs, "full": full,
                        "trend": m.get("recent_trend"),
                        "state": _state(m.get("tier"), obs, full)})
    if not targets:
        return None
    counts: dict[str, int] = {}
    for t in targets:
        counts[t["state"]] = counts.get(t["state"], 0) + 1
    by_chapter: dict[str, dict] = {}
    for t in targets:
        by_chapter.setdefault(t["chapter"], {}).setdefault(t["section"], []).append(t)
    chapters = [{"chapter": chapter,
                 "sections": [{"section": s, "targets": ts}
                              for s, ts in sec_map.items()]}
                for chapter, sec_map in by_chapter.items()]

    # 成绩走势：全部历史场次都在横轴上，未参加的场次标 missed。
    trend = []
    for e in history:
        score, full_s, avg = e.get("score"), _num(e.get("full")), _num(e.get("avg"))
        trend.append({
            "short": _week_name(e.get("name")),
            "score": score,
            "rate": (float(score) / full_s
                     if isinstance(score, (int, float)) and full_s and full_s > 0
                     else None),
            "avg_rate": (avg / full_s if avg is not None and full_s and full_s > 0
                         else None),
            "rank": e.get("rank"),
            "missed": score is None,
        })

    # 题片徽标与历次轨迹：同一题型在每场是失分、部分得分还是全对；
    # 从未失分过的题型不进轨迹，「这次新出现」也不挂徽标。
    scored_ids = [int(e["session_id"]) for e in sessions]
    scored_set = set(scored_ids)
    current_sid = int(data.session_id)
    track: dict[str, dict[str, Any]] = {}
    for t in targets:
        refs = (student.knowledge_mastery.get(t["key"]) or {}) \
            .get("source_question_refs") or []
        by_sess, seen = {}, set()
        for ref in refs:
            try:
                rsid = int(ref.get("session_id") or 0)
            except (TypeError, ValueError):
                continue
            qid = str(ref.get("question_id") or "")
            if rsid not in scored_set or (rsid, qid) in seen:
                continue
            seen.add((rsid, qid))
            by_sess.setdefault(rsid, []).append(ref)
        cells = []
        for esid in scored_ids:
            rs = by_sess.get(esid, [])
            qs = [{"q": str(r.get("question_id") or ""),
                   "score": r.get("score_awarded"),
                   "full": r.get("full_score")} for r in rs]
            if not rs:
                cells.append({"sid": esid, "state": "none", "qs": []})
                continue
            pairs = [(_num(r.get("score_awarded")), _num(r.get("full_score")))
                     for r in rs]
            if all(p[0] is not None and p[1] and p[1] > 0 and p[0] >= p[1]
                   for p in pairs):
                state = "full"
            elif all(p[0] is not None and p[0] == 0 for p in pairs):
                state = "zero"
            else:
                state = "part"
            cells.append({"sid": esid, "state": state, "qs": qs})
        if not any(c["state"] in ("part", "zero") for c in cells):
            continue
        last = max((i for i, c in enumerate(cells) if c["state"] != "none"),
                   default=None)
        n_loss = sum(1 for c in cells if c["state"] in ("part", "zero"))
        if cells[last]["state"] == "full":
            status = "fixed"
        elif n_loss >= 2:
            status = "repeat"
        elif cells[last]["sid"] == current_sid:
            status = "new"
        else:
            status = "pending"
        track[t["key"]] = {"status": status, "cells": cells}

    return {
        "n_sessions": len(sessions),
        "session_name": str(data.session_name),
        "target_label": {"type": "题型", "mixed": "训练目标"}.get(
            data.knowledge_structure.get("target_kind"), "知识点"),
        "counts": counts, "targets": targets, "chapters": chapters,
        "trend": trend,
        "exams": [{"sid": sid, "short": _week_name(name_by_sid[sid])}
                  for sid in scored_ids],
        "current_sid": current_sid,
        "track": track,
    }


def _trail_text(cells: list[dict[str, Any]], exams: list[dict[str, Any]],
                status: str) -> str:
    """「第二周 ✗ → 第三周 ✗ · 反复出错」式的一行历次轨迹。"""
    marks: list[tuple[str, bool]] = []
    for i, c in enumerate(cells):
        name = exams[i]["short"] if i < len(exams) else ""
        if c["state"] == "none":
            marks.append((f"{name}没考", False))
        else:
            marks.append((f"{name} {CELL_SYM[c['state']]}", True))
    text = ""
    for i, (part, arrow) in enumerate(marks):
        if i:
            text += " → " if arrow and marks[i - 1][1] else " · "
        text += part
    if status != "pending" and status in TRACK_LABEL:
        text += f" · {TRACK_LABEL[status]}"
    return text


def portrait_card_html(view: dict | None) -> str:
    if not view:
        return ""
    from backend.session_analysis import esc
    from backend.reporting.analysis_report_exporter import _question_display_label

    tl = view["target_label"]
    n = view["n_sessions"]
    counts = view["counts"]
    track = view.get("track") or {}
    exams = view.get("exams") or []
    current_sid = view.get("current_sid")
    n_targets = len(view["targets"])

    trend_json = esc(json.dumps(view.get("trend") or [], ensure_ascii=False))
    block_a = (
        '<div class="pblock pv-a"><h3 class="pb-t">成绩走势</h3>'
        '<div class="pvguide">实线是孩子，虚线是班级平均</div>'
        f'<div class="pv-svgbox" data-trend="{trend_json}"></div></div>'
    )

    bar = "".join(
        f'<i class="pvseg pv-{s}" '
        f'style="width:{counts.get(s, 0) / n_targets * 100:.1f}%"></i>'
        for s in BAR_ORDER if counts.get(s))
    legend = "".join(
        f'<span class="pvlg"><i class="pvsw pv-{s}"></i>{STATE_LABEL[s]} {counts[s]}</span>'
        for s in BAR_ORDER if counts.get(s))
    legend += ('<span class="pvlg"><i class="pv-cb cb-repeat">↻</i>反复出错</span>'
               '<span class="pvlg"><i class="pv-cb cb-fixed">↑</i>已改正</span>')

    def _rank(key: str) -> int:
        st = (track.get(key) or {}).get("status")
        return 0 if st == "repeat" else (1 if st == "fixed" else 2)

    chap_html = []
    for chap in view["chapters"]:
        cname = (re.sub(r"^第[0-9一二三四五六七八九十百]+章[　\s]*", "", chap["chapter"])
                 or chap["chapter"])
        targets = [t for sec in chap["sections"] for t in sec["targets"]]
        targets.sort(key=lambda t: (STATE_SORT.get(t["state"], 9), _rank(t["key"])))
        chips, infos = [], []
        for t in targets:
            tr = track.get(t["key"])
            status = (tr or {}).get("status")
            badge = ('<i class="pv-cb cb-repeat">↻</i>' if status == "repeat"
                     else '<i class="pv-cb cb-fixed">↑</i>' if status == "fixed" else "")
            fixed = " pv-fixed" if status == "fixed" else ""
            chips.append(
                f'<button type="button" class="pv-chip pv-{t["state"]}{fixed}" '
                f'data-tkey="{esc(t["key"])}">{esc(t["label"])}{badge}</button>')
            detail = (f'{esc(t["label"])} · {esc(STATE_LABEL.get(t["state"], t["state"]))}'
                      f' · 做过{t["obs"]}次，全对{t["full"]}次')
            if t.get("trend"):
                detail += f' · {esc(t["trend"])}'
            if tr:
                detail += f'<div class="pv-chiptrail">{esc(_trail_text(tr["cells"], exams, status))}</div>'
                cur_cell = next(
                    (c for c in tr["cells"]
                     if c["sid"] == current_sid and c["state"] in ("part", "zero")),
                    None)
                if cur_cell and cur_cell["qs"]:
                    ids = ",".join(q["q"] for q in cur_cell["qs"])
                    pills = "".join(
                        f'<button type="button" class="pv-qpill" '
                        f'data-open-q="{esc(q["q"])}" data-open-ctx="{esc(ids)}">'
                        f'{esc(_question_display_label(q["q"]))}</button>'
                        for q in cur_cell["qs"])
                    detail += f'<div class="pv-chiplinks">{pills}</div>'
            infos.append(
                f'<div class="pv-chipinfo" data-tkey="{esc(t["key"])}" hidden>{detail}</div>')
        chap_html.append(
            f'<div class="pvchap"><div class="pvchap-n">{esc(cname)}</div>'
            f'<div class="pvchips">{"".join(chips)}</div>{"".join(infos)}</div>')

    return f"""<div class="card">
<h2>本学期学情肖像</h2>
<div class="pv-sub">本学期 {n} 场考试，截至 {esc(view["session_name"])}</div>
{block_a}
<div class="pblock pv-map"><h3 class="pb-t">学过的{esc(tl)} · {n_targets} 个</h3>
<div class="pvbar">{bar}</div>
<div class="pvlg-row">{legend}</div>
{''.join(chap_html)}
</div>
</div>"""


# 题片点击展开详情（同一张图上同时只开一个）+ 成绩走势 SVG 按容器宽度绘制。
PORTRAIT_JS = """
(function(){
  document.addEventListener('click', function(e){
    var chip = e.target.closest && e.target.closest('.pv-chip');
    if(!chip) return;
    var blk = chip.closest('.pv-map') || document;
    var was = chip.classList.contains('on');
    Array.prototype.forEach.call(blk.querySelectorAll('.pv-chip.on'),
      function(c){c.classList.remove('on');});
    Array.prototype.forEach.call(blk.querySelectorAll('.pv-chipinfo'),
      function(i){i.hidden=true;});
    if(was) return;
    chip.classList.add('on');
    var info = Array.prototype.filter.call(
      blk.querySelectorAll('.pv-chipinfo'),
      function(i){return i.dataset.tkey === chip.dataset.tkey;})[0];
    if(info) info.hidden = false;
  });
  function fmtScore(v){var n=parseFloat(v);if(!isFinite(n))return '';
    return n%1?String(Math.round(n*10)/10):String(Math.round(n));}
  function escT(s){return String(s).replace(/&/g,'&amp;')
    .replace(/</g,'&lt;').replace(/>/g,'&gt;');}
  Array.prototype.forEach.call(
    document.querySelectorAll('.pv-svgbox[data-trend]'), function(box){
    var trend=[];
    try{trend=JSON.parse(box.dataset.trend||'[]');}catch(_){}
    if(!trend.length) return;
    function draw(){
      var w=Math.max(300, box.clientWidth||300);
      var H=150, padL=42, padR=42, padT=18, padB=42;
      var iw=w-padL-padR, ih=H-padT-padB, nx=trend.length;
      var X=function(i){return nx===1?padL+iw/2:padL+iw*i/(nx-1);};
      var Y=function(r){return padT+ih*(1-Math.max(0,Math.min(1,r)));};
      var svg='<svg class="pv-svg" width="'+w+'" height="'+H+
        '" viewBox="0 0 '+w+' '+H+'" role="img">';
      var avgs=[];
      trend.forEach(function(e,i){
        if(e.avg_rate!=null)avgs.push(X(i).toFixed(1)+','+Y(e.avg_rate).toFixed(1));});
      if(avgs.length>1) svg+='<polyline points="'+avgs.join(' ')+
        '" fill="none" stroke="#9aa7b8" stroke-width="1.5" stroke-dasharray="4 3"/>';
      var seg=[];
      var flush=function(){
        if(seg.length>1) svg+='<polyline points="'+seg.join(' ')+
          '" fill="none" stroke="#2f6fd6" stroke-width="2"/>';
        seg=[];};
      trend.forEach(function(e,i){
        if(e.rate==null)flush();else seg.push(X(i).toFixed(1)+','+Y(e.rate).toFixed(1));});
      flush();
      trend.forEach(function(e,i){
        var x=X(i).toFixed(1);
        if(e.rate!=null){
          var y=Y(e.rate).toFixed(1);
          svg+='<circle cx="'+x+'" cy="'+y+'" r="3.5" fill="#2f6fd6"/>';
          svg+='<text x="'+x+'" y="'+(y-7)+'" text-anchor="middle" font-size="11" font-weight="700" fill="#2f6fd6">'+escT(fmtScore(e.score))+'</text>';
        }
        var ly=padT+ih+17;
        var anchor=i===0?'start':(i===nx-1?'end':'middle');
        svg+='<text x="'+x+'" y="'+ly+'" text-anchor="'+anchor+
          '" font-size="12" fill="#3d4756">'+escT(e.short)+'</text>';
        var sub=e.missed?'未参加':(e.rank!=null?'第'+e.rank+'名':'');
        svg+='<text x="'+x+'" y="'+(ly+15)+'" text-anchor="'+anchor+
          '" font-size="11" fill="#8a94a6">'+escT(sub)+'</text>';
      });
      box.innerHTML=svg+'</svg>';
    }
    var t=null;
    window.addEventListener('resize',function(){
      clearTimeout(t);t=setTimeout(draw,250);});
    draw();
  });
})();
"""
