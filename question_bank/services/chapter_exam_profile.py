"""章节考情：本册期中/期末卷的出题统计与典型题挑选。只读，不调用模型。

统计口径：
- 试卷范围为本教学学期映射到的期中/期末卷（按 normalize_exam_type 归一），
  同源卷（较小一份卷中六成题能在另一份找到近乎相同的题）合并为一组，
  同组内近乎相同的题只计一次；
- 主考＝question_scope_summary.primary_section_id 落在本章节/小节；
  跨章＝主考不在本章、但直接判定点落在本章；
- 每道整题只计入一个主题型或主要技能（取决于当前知识标准；技能口径取
  判定点关联最多者，同数取稳定键小者，优先取锚定到该题所属小节的技能）；
- 出卷率＝至少 1 道本节主考题的卷组数 ÷ 该阶段卷组数。
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
import threading
from collections import Counter, OrderedDict, defaultdict
from itertools import chain
from pathlib import Path
from typing import Any

from question_bank.question_types import is_type_key
from question_bank.services.question_frequency_service import normalize_exam_type
from question_bank.services.question_skill_index import (
    short_node_name,
    skill_anchor_ids,
)
from question_bank.services.similar_question_ranker import clean_text, dice, grams

STAGES = (("midterm", "期中"), ("final", "期末"))
# 计数列＝难度档：基础 [1,4)、中档 [4,7)、提高 [7,10]；作答形式不再拆列。
COLS = ("basic", "mid", "hard")
TIERS = (("basic", 1.0, 4.0), ("mid", 4.0, 7.0), ("hard", 7.0, 10.01))
POS_BUCKETS = [str(i) for i in range(1, 20)] + ["20+"]
SAME_QUESTION = 0.9       # 三字片段 Dice 达到此值视为同一道题
SAME_SOURCE_SHARE = 0.6   # 两卷中较小一份有六成题相同，视为同源卷合并
SAME_TEMPLATE = 0.6       # 模板签名 Dice 达到此值视为同一题型模板
HARD_PICK_MAX = 8.5       # 提高变化题的题库难度上限
# 出现次数超过此值的三元片段不提供候选区分度，只为它们建倒排会拖慢统计；
# 近乎相同的两题仍有大量普通片段可形成候选。
GRAM_POSTING_LIMIT = 200
CANDIDATE_DICE_FLOOR = 0.7
UNLINKED = "__unlinked"


def _norm_type(raw: object) -> str:
    text = str(raw or "")
    if "选" in text:
        return "choice"
    if "填" in text:
        return "fill"
    return "written"


def _tier_of(d: float | None) -> str | None:
    if d is None:
        return None
    if d >= 10.01:
        return "hard"
    return next((key for key, lo, hi in TIERS if lo <= d < hi), None)



def _qn_int(raw: object) -> int | None:
    match = re.match(r"\s*(\d+)", str(raw or ""))
    return int(match.group(1)) if match else None


def _pos_bucket(n: int | None) -> str | None:
    if n is None:
        return None
    return "20+" if n >= 20 else str(n)


def _percent(part: int, whole: int) -> int | None:
    return int(100 * part / whole + 0.5) if whole else None


def _near_duplicates(gram_sets: dict[int, frozenset[str]]) -> dict[int, set[int]]:
    """近乎相同题的邻接表；只对共享足够片段的候选对计算 Dice。"""
    postings: dict[str, list[int]] = defaultdict(list)
    for qid, grams_set in gram_sets.items():
        for gram in grams_set:
            postings[gram].append(qid)
    kept = {
        qid: [gram for gram in grams_set if len(postings[gram]) <= GRAM_POSTING_LIMIT]
        for qid, grams_set in gram_sets.items()
    }
    neighbors: dict[int, set[int]] = {qid: set() for qid in gram_sets}
    for qid in kept:
        _near_duplicates_around(qid, gram_sets, postings, kept, neighbors)
        # 双向邻接已写入；后续题不再反向比较这一题。kept 保留原候选上界。
        for gram in kept[qid]:
            postings[gram].remove(qid)
    return neighbors


def _near_duplicates_around(
    qid: int,
    gram_sets: dict[int, frozenset[str]],
    postings: dict[str, list[int]],
    kept: dict[int, list[str]],
    neighbors: dict[int, set[int]],
) -> None:
    """重算一题的近重复邻接（与原全量算法逐对同价）。"""
    grams_list = kept[qid]
    shared = Counter(chain.from_iterable(postings[gram] for gram in grams_list))
    shared.pop(qid, None)
    for other, count in shared.items():
        # 候选上界按保留片段估算，再用完整片段 Dice 确认。
        if 2 * count < CANDIDATE_DICE_FLOOR * (len(grams_list) + len(kept[other])):
            continue
        if dice(gram_sets[qid], gram_sets[other]) >= SAME_QUESTION:
            neighbors[qid].add(other)
            neighbors[other].add(qid)


# 按题片段/模板签名缓存：键 = (库路径, 题号, 按题版本)。题面不变即片段不变。
_GRAM_MEMO_LIMIT = 8192
_GRAM_MEMO_LOCK = threading.Lock()
_GRAM_MEMO: OrderedDict[tuple, tuple[frozenset[str], frozenset[str]]] = OrderedDict()

# 近重复邻接增量缓存：键 = (库路径, 册标识)；存上次题集、版本、邻接表。
_DUP_CACHE_LIMIT = 8
_DUP_CACHE_LOCK = threading.Lock()
_DUP_CACHE: OrderedDict[tuple, dict[str, Any]] = OrderedDict()


def _gram_tpl_sets(
    rows: dict[int, dict[str, Any]],
    db_file: Path | None,
    versions: dict[int, int] | None,
) -> tuple[dict[int, frozenset[str]], dict[int, frozenset[str]]]:
    gram_sets: dict[int, frozenset[str]] = {}
    tpl_sets: dict[int, frozenset[str]] = {}
    path_key = str(db_file) if db_file is not None else None
    for qid, row in rows.items():
        cache_key = (path_key, qid, versions.get(qid, 0)) if versions is not None else None
        hit = None
        if cache_key is not None:
            with _GRAM_MEMO_LOCK:
                hit = _GRAM_MEMO.get(cache_key)
                if hit is not None:
                    _GRAM_MEMO.move_to_end(cache_key)
        if hit is None:
            text = row["question_text"]
            hit = (grams(clean_text(text)), _template_grams(text))
            if cache_key is not None:
                with _GRAM_MEMO_LOCK:
                    _GRAM_MEMO[cache_key] = hit
                    while len(_GRAM_MEMO) > _GRAM_MEMO_LIMIT:
                        _GRAM_MEMO.popitem(last=False)
        gram_sets[qid], tpl_sets[qid] = hit
    return gram_sets, tpl_sets


def _dup_neighbors(
    db_file: Path | None,
    scope_key: object,
    gram_sets: dict[int, frozenset[str]],
    state,
) -> dict[int, set[int]]:
    """近重复邻接：按题版本增量重算受影响题的对，其余沿用上次结果。"""
    from integration.data_generation import LAST_INCREMENTAL

    if db_file is None or state is None:
        neighbors = _near_duplicates(gram_sets)
        LAST_INCREMENTAL["near_duplicates"] = {
            "mode": "full", "recomputed": frozenset(gram_sets)}
        return neighbors
    key = (str(db_file), scope_key)
    with _DUP_CACHE_LOCK:
        prev = _DUP_CACHE.get(key)
        if prev is not None:
            _DUP_CACHE.move_to_end(key)
    if prev is None or prev["materials_global"] != state.materials_global:
        neighbors = _near_duplicates(gram_sets)
        LAST_INCREMENTAL["near_duplicates"] = {
            "mode": "full", "recomputed": frozenset(gram_sets)}
        with _DUP_CACHE_LOCK:
            _DUP_CACHE[key] = {
                "materials_global": state.materials_global,
                "versions": {qid: state.versions.get(qid, 0) for qid in gram_sets},
                "gram_sets": gram_sets, "neighbors": neighbors,
            }
            while len(_DUP_CACHE) > _DUP_CACHE_LIMIT:
                _DUP_CACHE.popitem(last=False)
        return neighbors
    prev_grams: dict[int, frozenset[str]] = prev["gram_sets"]
    prev_versions: dict[int, int] = prev["versions"]
    changed = {qid for qid in gram_sets
               if qid not in prev_grams
               or prev_versions.get(qid, 0) != state.versions.get(qid, 0)}
    changed |= set(prev_grams) - set(gram_sets)
    # 片段倒排是全局的：与变化题共享片段的未变题也需重判候选界。
    gram_diff = {
        qid for qid in changed
        if gram_sets.get(qid) != prev_grams.get(qid)
    }
    touched_grams: set[str] = set()
    for qid in gram_diff:
        touched_grams |= prev_grams.get(qid, frozenset())
        touched_grams |= gram_sets.get(qid, frozenset())
    postings: dict[str, list[int]] = defaultdict(list)
    for qid, grams_set in gram_sets.items():
        for gram in grams_set:
            postings[gram].append(qid)
    affected = set(changed) & set(gram_sets)
    for gram in touched_grams:
        affected.update(postings.get(gram, ()))
    neighbors = {
        qid: set(adj) & set(gram_sets)
        for qid, adj in prev["neighbors"].items()
        if qid in gram_sets
    }
    kept = {
        qid: [gram for gram in grams_set if len(postings[gram]) <= GRAM_POSTING_LIMIT]
        for qid, grams_set in gram_sets.items()
    }
    for qid in affected:
        if qid not in gram_sets:
            continue
        entry = neighbors.setdefault(qid, set())
        for other in list(entry):
            neighbors[other].discard(qid)
        entry.clear()
        _near_duplicates_around(qid, gram_sets, postings, kept, neighbors)
    from integration.data_generation import question_version_state
    after = question_version_state(db_file)
    if after != state:
        # 计算期间又有写入：结果与新状态不一致，按新状态全量重算。
        neighbors = _near_duplicates(gram_sets)
        LAST_INCREMENTAL["near_duplicates"] = {
            "mode": "full", "recomputed": frozenset(gram_sets)}
        if after is None:
            return neighbors
        state = after
    else:
        LAST_INCREMENTAL["near_duplicates"] = {
            "mode": "incremental", "recomputed": frozenset(affected)}
    with _DUP_CACHE_LOCK:
        _DUP_CACHE[key] = {
            "materials_global": state.materials_global,
            "versions": {qid: state.versions.get(qid, 0) for qid in gram_sets},
            "gram_sets": gram_sets, "neighbors": neighbors,
        }
        while len(_DUP_CACHE) > _DUP_CACHE_LIMIT:
            _DUP_CACHE.popitem(last=False)
    return neighbors


def _stage_cells() -> dict[str, dict[str, list[int]]]:
    return {stage: {col: [] for col in COLS} for stage, _label in STAGES}


def _stage_positions() -> dict[str, dict[str, list[int]]]:
    return {stage: {bucket: [] for bucket in POS_BUCKETS} for stage, _label in STAGES}


def _placeholders(values: list[int]) -> str:
    return ", ".join("?" for _ in values)


def _skill_home(key: str, skill_sections: dict[str, list[str]],
                section_label: dict[str, str]) -> str:
    anchors = skill_sections.get(key, [])
    return section_label.get(anchors[0], "其他册") if anchors else "其他册"


def _template_grams(text: object) -> frozenset[str]:
    """题型模板签名：数字、字母归一后取三字片段，只保留题面骨架。"""
    value = re.sub(r"\d+", "#", clean_text(text))
    value = re.sub(r"[a-zA-Z]+", "@", value)
    return grams(value)


def _year_of(raw: object) -> int | None:
    match = re.search(r"\d{4}", str(raw or ""))
    return int(match.group(0)) if match else None


def _pick_representative(
    cell: list[int],
    skill_key: str,
    recs: dict[int, dict],
    tpl_sets: dict[int, frozenset[str]],
    point_counts: dict[int, int],
    chosen: list[int],
    *,
    entry: bool,
    window: str,
    ceiling: float | None = None,
) -> int | None:
    """单元内选代表题：先排除与本节已选题同模板者；
    ceiling（提高变化 8.5）是硬上限，超出的题一律不候选；
    window="low"（基础入口/提高变化）只留难度不超过档内中位数的题，
    window="near"（常见变式）只留与中位数差 ≤1 的题——窗口筛完为空则不生效；
    有主要技能判定点占比过半的题时只保留它们，基础入口再优先独立小题
    （判定点总数 ≤4，避免把复杂大题中的容易小问当成基础入口）；
    其后取模板居中、难度贴近档内中位数、卷年新、题号小者。"""
    candidates = [
        qid for qid in cell
        if all(dice(tpl_sets[qid], tpl_sets[c]) < SAME_TEMPLATE for c in chosen)
    ]
    if ceiling is not None:
        candidates = [
            qid for qid in candidates
            if (d := recs[qid]["difficulty"]) is not None and d <= ceiling
        ]
    if not candidates:
        return None
    diffs = [recs[q]["difficulty"] for q in cell if recs[q]["difficulty"] is not None]
    median = statistics.median(diffs) if diffs else None
    if median is not None:
        if window == "near":
            kept = [
                qid for qid in candidates
                if (d := recs[qid]["difficulty"]) is not None
                and abs(d - median) <= 1.0
            ]
        else:
            kept = [
                qid for qid in candidates
                if (d := recs[qid]["difficulty"]) is not None and d <= median
            ]
        if kept:
            candidates = kept
    pure = [
        qid for qid in candidates
        if point_counts.get(qid)
        and recs[qid]["skill_hits"].get(skill_key, 0) / point_counts[qid] >= 0.5
    ]
    if pure:
        candidates = pure
    if entry:
        short = [qid for qid in candidates if (point_counts.get(qid) or 0) <= 4]
        if short:
            candidates = short

    def order(qid: int) -> tuple:
        others = [x for x in cell if x != qid]
        centrality = (
            sum(dice(tpl_sets[qid], tpl_sets[x]) for x in others) / len(others)
            if others else 0.0
        )
        d = recs[qid]["difficulty"]
        gap = abs(d - median) if d is not None and median is not None else float("inf")
        return (-round(centrality, 4), gap, -(recs[qid]["year"] or 0), qid)

    return min(candidates, key=order)


def _section_typical(
    smain: list[dict],
    recs: dict[int, dict],
    tpl_sets: dict[int, frozenset[str]],
    point_counts: dict[int, int],
    *,
    synthesis: bool,
    type_mode: bool,
) -> dict[str, list[dict]]:
    """按教师报告口径挑典型题：每个常考主题型（或主考技能）一张卡，
    卡片内按难度档给入口、中档变式与至多一个提高变化；
    只从至少 2 题且来自至少 2 个卷组的键×难度档单元里选。"""
    noun = "题型" if type_mode else "技能"
    skills: dict[str, dict] = {}
    names: dict[str, str] = {}
    cells: dict[tuple[str, str], list[int]] = {}
    for rec in smain:
        key = rec["skill_key"]
        if key == UNLINKED:
            continue
        row = skills.setdefault(key, {"groups": set(), "total": 0})
        row["groups"].add(rec["group"])
        row["total"] += 1
        names[key] = rec["skill_name"]
        if rec["tier"]:
            cells.setdefault((key, rec["tier"]), []).append(rec["id"])

    rank = sorted(
        skills,
        key=lambda key: (-len(skills[key]["groups"]), -skills[key]["total"],
                         names[key], key),
    )
    # 卡片上限：普通小节 10 张，章内综合 4 张；变式与提高不占用卡位。
    card_cap = 4 if synthesis else 10

    def is_typical(cell: list[int]) -> bool:
        return len(cell) >= 2 and len({recs[x]["group"] for x in cell}) >= 2

    chosen: list[int] = []
    picks: dict[str, dict[str, dict]] = {}

    def take(key: str, tier: str, slot: str, role: str, *, entry: bool = False,
             window: str = "low", ceiling: float | None = None) -> bool:
        cell = cells[(key, tier)]
        qid = _pick_representative(
            cell, key, recs, tpl_sets, point_counts, chosen, entry=entry,
            window=window, ceiling=ceiling,
        )
        if qid is None:
            return False
        chosen.append(qid)
        cell_groups = len({recs[x]["group"] for x in cell})
        if slot == "entry":
            reason = (f"本节第{rank.index(key) + 1}常考{noun}，"
                      f"出现在{len(skills[key]['groups'])}套试卷")
        elif slot == "variant":
            reason = f"同{noun}中档变式，同类{len(cell)}题"
        else:
            reason = f"出现在{cell_groups}套试卷"
        picks.setdefault(key, {})[slot] = {
            "tier": tier,
            "question_id": qid,
            "same_tier_count": len(cell),
            "group_count": cell_groups,
            "role": role,
            "reason": reason,
        }
        return True

    # 入口：覆盖至少 2 个卷组的键取最低的非难档典型单元，一个键一张卡。
    cards = 0
    for key in rank:
        if cards >= card_cap:
            break
        if len(skills[key]["groups"]) < 2:
            continue
        tier = next(
            (t for t in ("basic", "mid")
             if (key, t) in cells and is_typical(cells[(key, t)])),
            None,
        )
        if tier is None:
            continue
        if take(key, tier, "entry", "基础入口" if tier == "basic" else "常见变式",
                entry=True):
            cards += 1
    # 卡内变式与提高：只补有入口的卡；入口已是中档则跳过变式；
    # 提高单元需 ≥2 题且 ≥3 个卷组，每卡至多一个，不设小节限额。
    for key in rank:
        entry_pick = picks.get(key, {}).get("entry")
        if not entry_pick:
            continue
        cell = cells.get((key, "mid")) or []
        if (entry_pick["tier"] != "mid" and len(cell) >= 3
                and len({recs[x]["group"] for x in cell}) >= 2):
            take(key, "mid", "variant", "常见变式", window="near")
        hard_cell = cells.get((key, "hard")) or []
        if (is_typical(hard_cell)
                and len({recs[x]["group"] for x in hard_cell}) >= 3):
            take(key, "hard", "hard", "提高变化", ceiling=HARD_PICK_MAX)
    return {
        key: [picks[key][slot] for slot in ("entry", "variant", "hard")
              if slot in picks[key]]
        for key in rank
        if key in picks
    }


def _primary_skill_key(
    hits: dict[str, int],
    section: str,
    skill_sections: dict[str, list[str]],
) -> str | None:
    """主要技能：优先锚定到该题所属小节的技能，判定点最多者，同数取键小者。"""
    if not hits:
        return None
    own = [key for key in hits if section in skill_sections.get(key, [])]
    pool = own or list(hits)
    return sorted(pool, key=lambda key: (-hits[key], key))[0]


def _prepare(
    conn: sqlite3.Connection,
    snapshot: dict[str, Any],
    volume: dict[str, Any],
) -> dict[str, Any]:
    """期中/期末卷范围、同源卷组、逐题归属与主要技能归一——章节考情与考频共用。"""
    volume_id = str(volume["id"])
    stage_key = {"期中": "midterm", "期末": "final"}
    # 小节归属映射与带章序号的小节名（不改动目录对象本身）。
    section_chapter: dict[str, str] = {}
    section_label: dict[str, str] = {}
    for index, chapter in enumerate(volume["chapters"], start=1):
        cid = chapter["knowledge_id"]
        section_chapter[cid] = cid
        for section in chapter["sections"]:
            sid = section["knowledge_id"]
            section_chapter[sid] = cid
            label = str(section["label"])
            label = f"{index}.{label}" if label[:1].isdigit() else label
            section_label[sid] = label

    volume_qids = set(snapshot["volumes"].get(volume_id, ()))
    raw_paper_q: dict[int, list[int]] = {}
    for paper_id, members in snapshot["members"].items():
        selected = sorted(int(qid) for qid in members if int(qid) in volume_qids)
        if selected:
            raw_paper_q[int(paper_id)] = selected
    paper_ids = sorted(raw_paper_q)
    papers: dict[int, dict[str, Any]] = {}
    if paper_ids:
        for row in conn.execute(
            "SELECT id, title, exam_type, year FROM papers "
            f"WHERE id IN ({_placeholders(paper_ids)}) AND deleted_at IS NULL "
            "AND COALESCE(import_status,'')<>'deleted'",
            paper_ids,
        ):
            stage = stage_key.get(normalize_exam_type(row["exam_type"]))
            if stage:
                papers[int(row["id"])] = {
                    "id": int(row["id"]), "title": str(row["title"] or ""),
                    "stage": stage, "year": _year_of(row["year"]),
                }
    paper_ids = [pid for pid in paper_ids if pid in papers]
    candidate_qids = sorted({qid for pid in paper_ids for qid in raw_paper_q[pid]})
    questions: dict[int, dict[str, Any]] = {}
    if candidate_qids:
        for row in conn.execute(
            "SELECT id, paper_id, question_number, question_type, difficulty, "
            f"question_text FROM questions WHERE id IN ({_placeholders(candidate_qids)}) "
            "AND is_deleted = 0",
            candidate_qids,
        ):
            # 与原卷不在本册期中期末范围内的题不计入（含只通过引用出现的题）。
            if int(row["paper_id"]) in papers:
                questions[int(row["id"])] = dict(row)
    # 卷内题目先按原卷题序（按题号 id），引用出现的题排在后面，与原脚本口径一致。
    paper_q = {}
    for pid in paper_ids:
        originals = [qid for qid in raw_paper_q[pid]
                     if qid in questions and questions[qid]["paper_id"] == pid]
        referenced = [qid for qid in raw_paper_q[pid]
                      if qid in questions and questions[qid]["paper_id"] != pid]
        paper_q[pid] = originals + referenced
    from integration.data_generation import question_version_state
    db_row = conn.execute("PRAGMA database_list").fetchone()
    db_file = Path(db_row[2]) if db_row is not None and db_row[2] else None
    state = question_version_state(db_file) if db_file is not None else None
    versions = {qid: state.versions.get(qid, 0) for qid in questions} if state is not None else None
    gram_sets, tpl_sets = _gram_tpl_sets(questions, db_file, versions)
    neighbors = _dup_neighbors(db_file, volume["id"], gram_sets, state)

    # 同源卷合并：同阶段内，较小一份卷中至少六成题能在另一份找到近乎相同的题。
    parent = {pid: pid for pid in paper_ids}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    paper_sets = {pid: set(ids) for pid, ids in paper_q.items()}
    for index, first in enumerate(paper_ids):
        for second in paper_ids[index + 1:]:
            if papers[first]["stage"] != papers[second]["stage"]:
                continue
            qa, qb = (first, second) if len(paper_sets[first]) <= len(paper_sets[second]) else (second, first)
            small, big = paper_sets[qa], paper_sets[qb]
            if not small:
                continue
            hit = sum(1 for qid in small if neighbors[qid] & big)
            if hit / len(small) >= SAME_SOURCE_SHARE:
                parent[find(second)] = find(first)
    groups: dict[int, list[int]] = defaultdict(list)
    for pid in paper_ids:
        groups[find(pid)].append(pid)
    group_of = {pid: root for root, members in groups.items() for pid in members}

    # 同组卷内近乎相同的题只计一次。
    counted: set[int] = set()
    for root, members in groups.items():
        kept: list[int] = []
        kept_set: set[int] = set()
        for pid in members:
            for qid in paper_q[pid]:
                if neighbors[qid] & kept_set:
                    continue
                kept.append(qid)
                kept_set.add(qid)
        counted.update(kept)

    counted_list = sorted(counted)
    summary: dict[int, dict[str, Any]] = {}
    if counted_list:
        marks = _placeholders(counted_list)
        summary = {
            int(row["question_id"]): dict(row)
            for row in conn.execute(
                "SELECT question_id, primary_section_id, direct_section_ids_json "
                f"FROM question_scope_summary WHERE question_id IN ({marks})",
                counted_list,
            )
        }

    nodes = snapshot["nodes"]
    type_mode = any(is_type_key(key) for key in nodes)
    primary_types = snapshot.get('primary_types', {})
    if type_mode and not primary_types:
        for row in conn.execute("SELECT question_id,tag_value FROM question_tags WHERE tag_type='knowledge_point' ORDER BY id"):
            if is_type_key(row[1]) and str(row[1]) in nodes:
                primary_types.setdefault(int(row[0]), str(row[1]))
    skill_sections = {
        key: skill_anchor_ids(node, volume)
        for key, node in nodes.items()
        if (is_type_key(key) if type_mode else key.startswith("sk_"))
    }
    by_question = snapshot.get("type_hits", {}) if type_mode else snapshot["by_question"]
    point_counts = snapshot.get("point_counts") or {}

    recs: dict[int, dict[str, Any]] = {}
    for qid in counted_list:
        q = questions[qid]
        paper = papers[int(q["paper_id"])]
        try:
            difficulty = float(q["difficulty"])
        except (TypeError, ValueError):
            difficulty = None
        row_summary = summary.get(qid) or {}
        primary = str(row_summary.get("primary_section_id") or "")
        try:
            direct = json.loads(str(row_summary.get("direct_section_ids_json") or "[]"))
        except (json.JSONDecodeError, TypeError):
            direct = []
        qtype = _norm_type(q["question_type"])
        tier = _tier_of(difficulty)
        hits = by_question.get(qid, {})
        recs[qid] = {
            "id": qid,
            "paper_id": int(q["paper_id"]),
            "group": group_of[int(q["paper_id"])],
            "stage": paper["stage"],
            "year": paper["year"],
            "qn": _qn_int(q["question_number"]),
            "type": qtype,
            "difficulty": difficulty,
            "tier": tier,
            "section": primary,
            "chapter": section_chapter.get(primary, ""),
            "direct_chapters": sorted(
                {section_chapter[x] for x in direct if x in section_chapter}
            ),
            "skill_hits": {key: len(value) for key, value in hits.items()},
        }

    # 主要技能：优先锚定到该题所属小节的技能，判定点最多者，同数取键小者。
    for rec in recs.values():
        hits = rec["skill_hits"]
        own = [key for key in hits if rec["section"] in skill_sections.get(key, [])]
        best = primary_types.get(rec["id"]) if type_mode else _primary_skill_key(hits, rec["section"], skill_sections)
        if best is None:
            rec["skill_key"], rec["skill_own"] = UNLINKED, True
            continue
        rec["skill_key"] = best
        rec["skill_own"] = bool(own)
        node = nodes.get(best)
        rec["skill_name"] = (
            ("待归类" if type_mode else "未挂技能") if best == UNLINKED or node is None
            else short_node_name(str(node["display_name"]))
        )
    for rec in recs.values():
        if rec["skill_key"] == UNLINKED:
            rec["skill_name"] = "待归类" if type_mode else "未挂技能"

    stage_groups = {
        stage: [root for root, members in groups.items()
                if papers[members[0]]["stage"] == stage]
        for stage, _label in STAGES
    }
    unit = {
        stage: ("组" if any(len(groups[root]) > 1 for root in stage_groups[stage])
                else "份")
        for stage, _label in STAGES
    }
    return {
        "volume_id": volume_id, "type_mode": type_mode, "primary_types": primary_types,
        "volume_qids": volume_qids,
        "papers": papers,
        "groups": groups,
        "group_of": group_of,
        "counted": counted,
        "nodes": nodes,
        "skill_sections": skill_sections,
        "by_question": by_question,
        "point_counts": point_counts,
        "tpl_sets": tpl_sets,
        "recs": recs,
        "stage_groups": stage_groups,
        "unit": unit,
        "section_label": section_label,
    }


def build_chapter_exam_profile(
    conn: sqlite3.Connection,
    snapshot: dict[str, Any],
    volume: dict[str, Any],
) -> dict[str, Any]:
    """按本册期中/期末卷统计章节出卷情况；snapshot 为读服务的知识快照。"""
    prep = _prepare(conn, snapshot, volume)
    volume_id = prep["volume_id"]
    section_label = prep["section_label"]
    papers = prep["papers"]
    tpl_sets = prep["tpl_sets"]
    groups = prep["groups"]
    nodes = prep["nodes"]
    skill_sections = prep["skill_sections"]
    point_counts = prep["point_counts"]
    recs = prep["recs"]
    stage_groups = prep["stage_groups"]
    unit = prep["unit"]

    chapters_out: list[dict[str, Any]] = []
    for chapter in volume["chapters"]:
        cid = chapter["knowledge_id"]
        main = [rec for rec in recs.values() if rec["chapter"] == cid]
        cross = [rec for rec in recs.values()
                 if rec["chapter"] != cid and cid in rec["direct_chapters"]]
        if not main:
            continue
        raw_sections = [(section["knowledge_id"], section_label[section["knowledge_id"]], False)
                        for section in chapter["sections"]]
        raw_sections.append((cid, "章内综合", True))
        sections_out = []
        for raw_id, label, synthesis in raw_sections:
            smain = [rec for rec in main if rec["section"] == raw_id]
            if synthesis and not smain:
                continue
            coverage = {}
            overview = _stage_cells()
            for stage, _label in STAGES:
                hit = {rec["group"] for rec in smain if rec["stage"] == stage}
                coverage[stage] = {
                    "groups": len(hit),
                    "of": len(stage_groups[stage]),
                    "percent": _percent(len(hit), len(stage_groups[stage])),
                    "questions": sum(1 for rec in smain if rec["stage"] == stage),
                }
            skill_rows: dict[str, dict[str, Any]] = {}
            for rec in smain:
                key = rec["skill_key"]
                row = skill_rows.setdefault(key, {
                    "key": None if key == UNLINKED else key,
                    "name": rec["skill_name"],
                    "unlinked": key == UNLINKED,
                    "target_kind": "type" if prep["type_mode"] else "skill",
                    "home_section_label": (
                        "" if key == UNLINKED or rec["skill_own"]
                        else _skill_home(key, skill_sections, section_label)
                    ),
                    "definition": (
                        "" if key == UNLINKED
                        else str((nodes.get(key) or {}).get("observable_evidence") or "")
                    ),
                    "total": 0,
                    "cells": _stage_cells(),
                    "positions": _stage_positions(),
                    "typical": [],
                })
                row["total"] += 1
                if rec["tier"]:
                    row["cells"][rec["stage"]][rec["tier"]].append(rec["id"])
                    overview[rec["stage"]][rec["tier"]].append(rec["id"])
                bucket = _pos_bucket(rec["qn"])
                if bucket:
                    row["positions"][rec["stage"]][bucket].append(rec["id"])
            # 行按本节覆盖卷组数排序（与典型题卡片次序一致），未挂键的居末。
            skill_groups: dict[str, set[int]] = {}
            for rec in smain:
                if rec["skill_key"] != UNLINKED:
                    skill_groups.setdefault(rec["skill_key"], set()).add(rec["group"])
            ordered = sorted(
                skill_rows.values(),
                key=lambda row: (
                    row["unlinked"],
                    -len(skill_groups.get(row["key"] or "", ())),
                    -row["total"], row["name"], row["key"] or "",
                ),
            )
            typical_map = _section_typical(
                smain, recs, tpl_sets, point_counts, synthesis=synthesis,
                type_mode=prep["type_mode"],
            )
            for row in ordered:
                row["group_count"] = len(
                    skill_groups.get(row["key"] or "", ())
                )
                row["typical"] = typical_map.get(row["key"] or "", [])
            sections_out.append({
                "id": f"{cid}#synthesis" if synthesis else raw_id,
                "label": label,
                "synthesis": synthesis,
                "main_count": len(smain),
                "coverage": coverage,
                "overview": overview,
                "skills": ordered,
            })
        totals = {
            stage: {
                "main": sum(1 for rec in main if rec["stage"] == stage),
                "cross": sum(1 for rec in cross if rec["stage"] == stage),
            }
            for stage, _label in STAGES
        }
        difficulty = {}
        for stage, _label in STAGES:
            rows = [rec for rec in main if rec["stage"] == stage]
            tiers = {key: sum(1 for rec in rows if rec["tier"] == key) for key, *_ in TIERS}
            difficulty[stage] = {
                "total": len(rows),
                **tiers,
                "choice": sum(1 for rec in rows if rec["type"] == "choice"),
                "fill": sum(1 for rec in rows if rec["type"] == "fill"),
                "written": sum(1 for rec in rows if rec["type"] == "written"),
            }
        chapters_out.append({
            "id": cid,
            "label": str(chapter["label"]),
            "totals": totals,
            "difficulty": difficulty,
            "cross_question_ids": {
                stage: [rec["id"] for rec in cross if rec["stage"] == stage]
                for stage, _label in STAGES
            },
            "sections": sections_out,
        })

    merged = [
        {
            "stage": papers[members[0]]["stage"],
            "papers": [{"id": pid, "title": papers[pid]["title"]} for pid in members],
        }
        for _root, members in groups.items()
        if len(members) > 1
    ]
    return {
        "curriculum_volume_id": volume_id,
        "graph_release_id": snapshot.get("release"),
        "model_calls": 0,
        "counted_question_count": len(recs),
        "stages": [
            {
                "stage": stage,
                "label": label,
                "paper_count": sum(
                    1 for paper in papers.values() if paper["stage"] == stage
                ),
                "group_count": len(stage_groups[stage]),
                "unit": unit[stage],
            }
            for stage, label in STAGES
        ],
        "merged_groups": merged,
        "chapters": chapters_out,
    }


def build_exam_frequency(
    conn: sqlite3.Connection,
    snapshot: dict[str, Any],
    volume: dict[str, Any],
) -> dict[int, tuple[float | None, float | None]]:
    """每题考频：(期中同源卷组覆盖率, 期末同源卷组覆盖率)，与章节考情同一口径。

    某阶段覆盖率＝含有同主要技能同难度档计入题的该阶段卷组数 ÷ 该阶段卷组数；
    题自身所属卷组在分子分母中同时剔除，不在正式卷中的题（如阶段练习）
    没有可剔除的自有卷组；无主要技能或无难度档的题不出现。
    某阶段剔除自有卷组后没有卷组时该阶段为 None，表示没有分母而不是 0%。"""
    prep = _prepare(conn, snapshot, volume)
    stage_groups = {
        stage: set(prep["stage_groups"][stage]) for stage, _label in STAGES
    }
    if not any(stage_groups.values()):
        return {}
    recs = prep["recs"]
    group_of = prep["group_of"]
    skill_sections = prep["skill_sections"]
    by_question = prep["by_question"]

    cell_groups: dict[str, dict[tuple[str, str], set[int]]] = {
        stage: defaultdict(set) for stage, _label in STAGES
    }
    for rec in recs.values():
        if rec["skill_key"] != UNLINKED and rec["tier"] is not None:
            cell_groups[rec["stage"]][(rec["skill_key"], rec["tier"])].add(
                rec["group"]
            )

    def shares(
        skill_key: str, tier: str, own_group: int | None
    ) -> tuple[float | None, float | None]:
        own = {own_group} if own_group is not None else set()
        out = []
        for stage, _label in STAGES:
            pool = stage_groups[stage] - own
            covered = cell_groups[stage].get((skill_key, tier), set()) - own
            out.append(len(covered) / len(pool) if pool else None)
        return (out[0], out[1])

    results: dict[int, tuple[float | None, float | None]] = {}
    for rec in recs.values():
        if rec["skill_key"] == UNLINKED or rec["tier"] is None:
            continue
        results[rec["id"]] = shares(rec["skill_key"], rec["tier"], rec["group"])

    # 计入题之外的本册题（同组去重丢弃者、阶段练习等）也按同一口径取值。
    extras = sorted(prep["volume_qids"] - prep["counted"])
    if not extras:
        return results
    marks = _placeholders(extras)
    extra_rows = {
        int(row["id"]): dict(row)
        for row in conn.execute(
            "SELECT id, paper_id, difficulty FROM questions "
            f"WHERE id IN ({marks}) AND is_deleted = 0",
            extras,
        )
    }
    if not extra_rows:
        return results
    extra_list = sorted(extra_rows)
    extra_section = {
        int(row["question_id"]): str(row["primary_section_id"] or "")
        for row in conn.execute(
            "SELECT question_id, primary_section_id FROM question_scope_summary "
            f"WHERE question_id IN ({_placeholders(extra_list)})",
            extra_list,
        )
    }
    for qid, row in extra_rows.items():
        hits = {
            key: len(value)
            for key, value in by_question.get(qid, {}).items()
        }
        try:
            difficulty = float(row["difficulty"])
        except (TypeError, ValueError):
            continue
        tier = _tier_of(difficulty)
        if tier is None:
            continue
        best = prep["primary_types"].get(qid) if prep["type_mode"] else _primary_skill_key(
            hits, extra_section.get(qid, ""), skill_sections
        )
        if best is None:
            continue
        results[qid] = shares(best, tier, group_of.get(int(row["paper_id"])))
    return results
