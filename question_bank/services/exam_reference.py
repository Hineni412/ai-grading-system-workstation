"""Shared Shenzhen reference eligibility and report, using saved question evidence.

No model calls, no writes, and no student evidence or mastery changes. Ranking
scores from the similar-question search are deliberately not eligibility scores.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from question_bank.services.similar_question_ranker import SimilarQuestionIndex, SimilarityQuestion

YEAR_DECAY = .8
MAX_DIFFICULTY_GAP = 2.0
RULE_VERSION = "sz-reference-v1"


def is_entrance_paper(paper: Mapping) -> bool:
    kind = str(paper.get("exam_type") or "")
    title = str(paper.get("title") or "")
    return ("中考" in kind or "中考" in title) and not re.search("模拟|模考|适应性|一模|二模|三模", kind + title)


def is_shenzhen_paper(paper: Mapping) -> bool:
    return "深圳" in str(paper.get("city") or "") or "深圳" in str(paper.get("title") or "")


def exam_year(paper: Mapping) -> dict:
    saved = str(paper.get("year") or "").strip()
    if paper.get("year_confirmed") and re.fullmatch(r"20\d{2}", saved):
        return {"year": int(saved), "status": "confirmed", "reason": "教师确认"}
    title = str(paper.get("title") or "")
    filename = str(paper.get("source_file") or paper.get("_source_file") or "").replace("\\", "/").rsplit("/", 1)[-1]

    def years(text: str) -> set[int]:
        # School-year ranges and compilation dates are not exam years.
        text = re.sub(r"20\d{2}\s*[—–\-/至]\s*20\d{2}\s*学年", "", text)
        text = re.sub(r"20\d{2}\s*年?\s*(?:整理|汇编|更新|收集|学年)", "", text)
        return {int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", text)}

    titled, named = years(title), years(filename)
    found = titled | named
    if re.fullmatch(r"20\d{2}", saved):
        found.add(int(saved))
    if len(found) > 1:
        return {"year": None, "status": "conflict", "reason": "年份冲突：" + "、".join(map(str, sorted(found)))}
    if len(found) == 1:
        return {"year": next(iter(found)), "status": "inferred", "reason": "根据试卷标题、文件名及已有年份识别"}
    return {"year": None, "status": "missing", "reason": "考试年份待核对"}


def _part_matches(left, right) -> bool:
    if not left.topics or not right.topics or not left.topics.issubset(right.topics):
        return False
    if left.skills and right.skills and not left.skills & right.skills:
        return False
    if not left.mode or left.mode != right.mode:
        return False
    if not left.operations or not right.operations or left.operations != right.operations:
        return False
    # Explicit mathematical outputs prevent e.g. length/area or range/value
    # questions from matching just because they share a chapter and a method.
    if left.outputs or right.outputs:
        return bool(left.outputs) and left.outputs == right.outputs
    return True


def pair_eligibility(index: SimilarQuestionIndex, candidate: SimilarityQuestion,
                     reference: SimilarityQuestion) -> tuple[bool, str]:
    if not candidate.parts or not reference.parts:
        return False, "缺少可用小问判定资料"
    if candidate.difficulty is None or reference.difficulty is None:
        return False, "难度资料待核对"
    if abs(candidate.difficulty - reference.difficulty) > MAX_DIFFICULTY_GAP:
        return False, "与参考题难度差超过 2 级"
    # Every candidate part must have a reference counterpart; a matching first
    # part cannot admit an unrelated or harder later part.
    for part in candidate.parts:
        if not part.operations or not part.mode:
            return False, "小问作答任务资料待核对"
        counterparts = [other for other in reference.parts if _part_matches(part, other)]
        if not counterparts:
            return False, "存在考查任务或作答要求不对应的小问"
        if part.difficulty is None or not any(other.difficulty is not None and
                abs(part.difficulty - other.difficulty) <= MAX_DIFFICULTY_GAP for other in counterparts):
            return False, "小问难度不对应或资料待核对"
    for dimension in ("type", "method", "model"):
        a, b = candidate.tags.get(dimension, frozenset()), reference.tags.get(dimension, frozenset())
        if a and b and not a & b:
            return False, "具体题型、关键方法或模型不同"
    return True, f"全部 {len(candidate.parts)} 个小问的直接知识、任务、作答要求与难度符合参考"


@dataclass
class ExamReferences:
    papers: dict[int, dict]
    members: dict[int, set[int]]
    rows: dict[int, dict]
    index: SimilarQuestionIndex | None
    aliases: dict[int, int] = field(default_factory=dict)
    numbers: dict[tuple[int, int], str] = field(default_factory=dict)
    originals: set[int] = field(default_factory=set)
    groups: list[dict] = field(default_factory=list)
    _results: dict[int, dict] = field(default_factory=dict)

    def __post_init__(self):
        self.by_question = {q.qid: q for q in self.index.questions} if self.index else {}
        self.sources = defaultdict(set)
        seen = {}
        for pid, paper in self.papers.items():
            for qid in self.members.get(pid, ()):
                self.sources[qid].add(pid)
            if is_entrance_paper(paper) and is_shenzhen_paper(paper):
                self.originals.update(self.members.get(pid, ()))
            if not paper.get("is_exam_reference") or not is_entrance_paper(paper) or not is_shenzhen_paper(paper):
                continue
            qids = self.members.get(pid, set())
            year_info = exam_year(paper)
            # A re-upload of the same canonical question set contributes once.
            identity = tuple(sorted(qids)) if qids else ("empty", pid)
            if identity in seen:
                seen[identity]["paper_ids"].append(pid)
                continue
            group = {"id": pid, "title": paper.get("title") or "未命名试卷", "paper_ids": [pid],
                     "year": year_info["year"], "year_status": year_info["status"],
                     "year_reason": year_info["reason"], "question_ids": sorted(qids), "weight": None}
            self.groups.append(group)
            seen[identity] = group
        years = [p["year"] for p in self.groups if p["year"] is not None]
        newest = max(years) if years else None
        for group in self.groups:
            if group["year"] is not None and newest is not None:
                group["weight"] = round(YEAR_DECAY ** (newest - group["year"]), 6)
        self.groups.sort(key=lambda p: (-(p["year"] or 0), p["id"]))

    def summary(self) -> dict:
        return {"active": bool(self.groups), "paper_count": len(self.groups),
                "papers": [{k: v for k, v in p.items() if k != "question_ids"} for p in self.groups],
                "rule_version": RULE_VERSION, "year_decay": YEAR_DECAY,
                "pending_year_count": sum(p["year"] is None for p in self.groups)}

    def assess(self, qid: int) -> dict:
        qid = self.aliases.get(qid, qid)
        if qid in self._results:
            return self._results[qid]
        result = self._assess(qid)
        self._results[qid] = result
        return result

    def _assess(self, qid: int) -> dict:
        base = {"status": "ordinary", "reason": "普通题沿用原选题规则", "matches": [],
                "reference_count": len(self.groups), "matched_paper_count": 0, "recent_weight": 0.}
        sources = [self.papers[p] for p in self.sources.get(qid, ())]
        if qid in self.originals:
            return {**base, "status": "reference", "reason": "深圳参考原题，不进入日常自动候选"}
        if not any(is_entrance_paper(p) for p in sources):
            return base
        if not self.groups:
            return {**base, "status": "unconfigured", "reason": "尚未星标深圳中考参考卷"}
        if not any(p.get("city") or p.get("province") not in (None, "", "广东", "广东省") for p in sources):
            return {**base, "status": "pending", "reason": "命题地区待核对"}
        candidate = self.by_question.get(qid)
        if candidate is None or not candidate.parts or candidate.difficulty is None:
            return {**base, "status": "pending", "reason": "题目判定资料或难度待核对"}
        matches, reasons = [], []
        for group in self.groups:
            passed = []
            for rid in group["question_ids"]:
                reference = self.by_question.get(rid)
                if reference is None:
                    continue
                ok, reason = pair_eligibility(self.index, candidate, reference)
                reasons.append(reason)
                if ok:
                    passed.append({"paper_id": group["id"], "paper_title": group["title"],
                                   "year": group["year"], "question_id": rid,
                                   "question_number": self.numbers.get((group["id"], rid), str(self.rows[rid].get("question_number") or "")),
                                   "reason": reason, "weight": group["weight"]})
            matches.extend(passed)
        if matches:
            matched = {m["paper_id"] for m in matches}
            return {**base, "status": "matched", "reason": "符合深圳参考", "matches": matches,
                    "matched_paper_count": len(matched),
                    "recent_weight": round(sum(p["weight"] or 0. for p in self.groups if p["id"] in matched), 6)}
        pending = not reasons or all("资料" in reason for reason in reasons)
        return {**base, "status": "pending" if pending else "unmatched",
                "reason": "参考资料待补齐" if pending else "未达到参考要求：" + next((r for r in reasons if "资料" not in r), reasons[0])}


def automatic_candidate(result: Mapping) -> bool:
    return result.get("status") in {"ordinary", "matched", "unconfigured"}


def reference_priority(candidate: Mapping) -> tuple:
    ref = candidate.get("exam_reference") or {}
    return (-int(ref.get("matched_paper_count") or 0), -float(ref.get("recent_weight") or 0.))
