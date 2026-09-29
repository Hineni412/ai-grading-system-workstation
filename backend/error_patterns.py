"""题目级典型错法与学生实际作答的确定性映射。

- 选择题：优先使用题库已有的逐选项预测；缺项时再做选项分析，
  产出 {选项字母: 错法}；
  学生识别到的选项字母直接对应，不做二次推理；未覆盖的选项记“原因未明”。
- 填空题：学生作答规范化分组；库中已有的错误答案直接复用错法；
  整理阶段（v3）新归纳出的错法按规范化答案回写入库，供后续复用。
- 题目分析的预测与考后整理的结果写入题库 ``question_error_patterns``；
  仅实际作答附出现记录。教师可事后修改（来源 ``teacher_edit``）。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from answer_normalizer import normalize_answer_text
from backend.error_causes import (
    CAUSE_CATEGORIES,
    CAUSE_KIND_CATEGORIES,
    normalize_cause_category,
)

CAUSE_CATEGORY_UNANSWERED = "未作答"

_CATEGORY_TO_KIND = {
    category: kind for kind, categories in CAUSE_KIND_CATEGORIES.items()
    for category in categories
}


def kind_for_category(category: str | None) -> str | None:
    return _CATEGORY_TO_KIND.get(str(category or ""))


def _parent_qid(question_id: str) -> str:
    from backend.session_analysis import parent_question_id

    return parent_question_id(question_id)

OPTION_ANALYSIS_VERSION = "option_analysis_v1"
CHOICE_TYPES = {"choice", "single_choice"}
FILL_TYPES = {"fill_blank", "fill_in_blank"}

OPTION_ANALYSIS_PROMPT = """你是初中数学教研员。请为这道选择题做“选项诊断”：对每个错误选项，
说明学生选它通常反映了哪类错误。

要求：
1. 只输出 JSON 对象：{"options": [{"option": "A", "category": "...", "pattern": "...", "explanation": "..."}]}
2. option 必须是题目中真实出现的错误选项字母；不要给正确选项写条目，也不要编造题目中没有的选项。
3. category 只能从固定 7 类中选：概念理解、计算与化简、审题与条件、方法与思路、
   过程与依据、书写与规范、未作答。
4. pattern 是这类错法的简短名称（≤20 字），同一道题里不同选项可以指向同一个错法。
5. explanation 用一句话说明学生为什么可能选它（≤40 字），只描述可观察的作答事实，
   不要推断学生“一定”犯了什么错。
6. 如果无法判断某个选项的错误原因，就不要为它写条目（宁可少写，不要编造）。"""

_OPTION_MARKER = re.compile(
    r"(?:^|[\s（(\[{；;，,])\s*([A-F])\s*[.、．)）:：]")
_ANSWER_BLOCK = re.compile(r"[【\[]?\s*答案\s*[】\]]?\s*[:：]?\s*([A-F])")
_LETTER_ONLY = re.compile(r"^[A-F]{1,6}$")
_LETTER_AFTER_HINT = re.compile(r"(?:选|答|填|涂|是|为)\S{0,4}?([A-F])\b", re.IGNORECASE)
_STANDALONE_LETTER = re.compile(r"(?<![A-Za-z0-9])([A-F])(?![A-Za-z0-9])")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_option_letters(question_text: str | None) -> list[str]:
    """从题干中解析选项字母（A./A、/A)/A：等写法），按出现顺序去重。"""
    if not question_text:
        return []
    letters: list[str] = []
    for match in _OPTION_MARKER.finditer(str(question_text)):
        letter = match.group(1)
        if letter not in letters:
            letters.append(letter)
    return letters


def extract_canonical_option(answer_text: str | None) -> str:
    """从题库 answer_text 的“【答案】X”中提取正确选项字母。"""
    match = _ANSWER_BLOCK.search(str(answer_text or ""))
    return match.group(1) if match else ""


def normalize_option_answer(value: Any) -> str:
    """把识别到的学生选项规整为字母串（"b"→"B"、"AC"→"AC" 去重排序）。"""
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if _LETTER_ONLY.fullmatch(text):
        return "".join(sorted(set(text)))
    match = _LETTER_AFTER_HINT.search(text)
    if match:
        return match.group(1)
    standalone = _STANDALONE_LETTER.findall(text)
    if len(standalone) == 1:
        return standalone[0]
    return ""


def normalize_wrong_answer(value: Any) -> str:
    """填空题错误答案库键：normalize_answer_text 规整 + 长度截断。"""
    normalized = normalize_answer_text(str(value or "").strip())
    if isinstance(normalized, list):
        normalized = ";".join(str(item) for item in normalized if item)
    return str(normalized or "").strip()[:80]


def question_fingerprint(question_text: Any, canonical_answer: Any) -> str:
    """题目内容指纹：题干/答案未变时选项分析可跨场次复用。"""
    payload = json.dumps(
        {"question_text": str(question_text or ""), "canonical_answer": str(canonical_answer or "")},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 题库关联与读取
# ---------------------------------------------------------------------------

def session_bank_context(
    question_bank_path: Path | None, session_id: int,
) -> dict[str, dict[str, Any]]:
    """{本场父级题号: {"bank_id", "bank_ids", "linked"}}。

    - bank_id：本题在题库的确认关联；bank_ids：含判重副本与同题其他场次
      关联的完整家族集合。
    - linked：{(其他场次id, 对方父级题号)}，与 class_analysis 的
      _linked_question_sources 同一套关联口径。
    """
    path = Path(question_bank_path) if question_bank_path else None
    if path is None or not path.is_file():
        return {}
    try:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True,
            isolation_level=None, timeout=5.0,
        )
    except (OSError, sqlite3.Error):
        return {}
    try:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "grading_question_links" not in tables:
            return {}
        own = connection.execute(
            "SELECT source_question_id, bank_question_id FROM grading_question_links"
            " WHERE CAST(grading_session_id AS INTEGER)=? AND status='confirmed'"
            " AND bank_question_id IS NOT NULL",
            (int(session_id),),
        ).fetchall()
        if not own:
            return {}
        graph: dict[int, set[int]] = {}
        if "question_duplicate_links" in tables and "questions" in tables:
            for qid, dup_of in connection.execute(
                "SELECT question_id, duplicate_of_question_id FROM question_duplicate_links WHERE match_kind='exact'"
            ):
                graph.setdefault(int(qid), set()).add(int(dup_of))
                graph.setdefault(int(dup_of), set()).add(int(qid))
        candidate_ids = {int(bank_id) for _, bank_id in own}
        frontier = list(candidate_ids)
        while frontier:
            for linked_id in graph.get(frontier.pop(), ()):
                if linked_id not in candidate_ids:
                    candidate_ids.add(linked_id)
                    frontier.append(linked_id)
        identities = {}
        if graph:
            from question_bank.services.duplicate_analysis_copy_service import exact_identity_map
            # The existing connection stays read-only; no index repair here.
            connection.row_factory = sqlite3.Row
            identities = exact_identity_map(connection, data_root=path.parent.parent,
                question_ids=sorted(candidate_ids), persist=False)
            connection.row_factory = None
        own_parent: dict[int, set[str]] = {}
        families: dict[int, set[int]] = {}
        for source_qid, bank_id in own:
            bank_id = int(bank_id)
            own_parent.setdefault(bank_id, set()).add(_parent_qid(str(source_qid)))
            if bank_id in families:
                continue
            family = {bank_id}
            key = identities.get(bank_id)
            if key:
                family.update(qid for qid, other_key in identities.items() if key == other_key)
            families[bank_id] = family
        all_ids = sorted({bid for family in families.values() for bid in family})
        marks = ",".join("?" * len(all_ids))
        others = connection.execute(
            f"SELECT grading_session_id, source_question_id, bank_question_id"
            f" FROM grading_question_links WHERE status='confirmed' AND bank_question_id IN ({marks})",
            all_ids,
        ).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for own_bank, parents in own_parent.items():
            family = families[own_bank]
            for parent in parents:
                bucket = out.setdefault(parent, {
                    "bank_id": own_bank, "bank_ids": set(family), "linked": set(),
                })
                bucket["bank_ids"].update(family)
        for other_sid, source_qid, bank_id in others:
            if int(other_sid) == int(session_id):
                continue
            bank_id = int(bank_id)
            other_parent = _parent_qid(str(source_qid))
            for own_bank, parents in own_parent.items():
                if bank_id in families[own_bank]:
                    for parent in parents:
                        bucket = out[parent]
                        bucket["linked"].add((int(other_sid), other_parent))
                        bucket["bank_ids"].add(bank_id)
        return out
    except sqlite3.Error:
        return {}
    finally:
        connection.close()


def session_bank_map(
    question_bank_path: Path | None, session_id: int,
) -> dict[str, int]:
    """{本场父级题号: 题库 question_id}；仅本场确认关联。"""
    return {
        parent: int(ctx["bank_id"])
        for parent, ctx in session_bank_context(question_bank_path, session_id).items()
    }


def bank_question_row(
    question_bank_path: Path | None, question_id: int,
) -> dict[str, Any] | None:
    """读取题库题目行（题干含内嵌选项文本）。"""
    if question_bank_path is None or not Path(question_bank_path).exists():
        return None
    from question_bank.database.schema import connect

    try:
        with connect(Path(question_bank_path)) as conn:
            row = conn.execute(
                "SELECT id, question_text, answer_text, question_type"
                " FROM questions WHERE id=?",
                (int(question_id),),
            ).fetchone()
    except Exception:
        return None
    return dict(row) if row else None


def bank_confirmed_triggers(
    question_bank_path: Path | None, question_ids: Iterable[int],
) -> dict[int, list[dict[str, Any]]]:
    """{题库题 id: 错法行}；含预测与驳回状态，供读取侧决定优先级。"""
    if question_bank_path is None or not Path(question_bank_path).exists():
        return {}
    from question_bank.database.schema import connect
    from question_bank.services.error_pattern_service import list_patterns

    ids = [int(qid) for qid in question_ids if qid]
    if not ids:
        return {}
    try:
        with connect(Path(question_bank_path)) as conn:
            return list_patterns(
                conn, ids, statuses=("confirmed", "candidate", "merged", "rejected")
            )
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 会话状态读写（.class_analysis 内嵌）
# ---------------------------------------------------------------------------

def option_analysis_entries(state: dict[str, Any] | None) -> dict[str, Any]:
    entries = (state or {}).get("option_analysis")
    return entries if isinstance(entries, dict) else {}


def save_option_analysis(store: Any, session_id: int, question_id: str, entry: dict[str, Any]) -> None:
    state = store.load(session_id) or {}
    entries = dict(option_analysis_entries(state))
    entries[str(question_id)] = entry
    store.save(session_id, option_analysis=entries)


def answer_pattern_map(state: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """{父题号: {规范化答案: 错法条目}}"""
    patterns = (state or {}).get("answer_patterns")
    return patterns if isinstance(patterns, dict) else {}


def record_answer_patterns(
    store: Any,
    session_id: int,
    parent_question_id: str,
    additions: Iterable[dict[str, Any]],
    *,
    bank_question_id: int | None = None,
) -> int:
    """把整理阶段新归纳出的错法按规范化答案写入候选库；返回新增条数。"""
    state = store.load(session_id) or {}
    library = {key: dict(value) for key, value in answer_pattern_map(state).items()}
    bucket = library.setdefault(str(parent_question_id), {})
    if bank_question_id and not bucket.get("_bank_question_id"):
        bucket["_bank_question_id"] = int(bank_question_id)
    added = 0
    for item in additions:
        answer = str(item.get("answer") or "").strip()
        if not answer or answer in bucket:
            continue
        category = normalize_cause_category(item.get("category"))
        bucket[answer] = {
            "category": category,
            "pattern": str(item.get("pattern") or "").strip()[:40],
            "explanation": str(item.get("explanation") or "").strip()[:80],
            "source": str(item.get("source") or "cause_v3"),
            "status": "candidate",
            "updated_at": _utc_now(),
        }
        added += 1
    if added or bank_question_id:
        store.save(session_id, answer_patterns=library)
    return added


def find_option_analysis(
    store: Any,
    session_id: int,
    parent_question_id: str,
    fingerprint: str,
    linked: Iterable[tuple[int, str]] = (),
) -> dict[str, Any] | None:
    """按“本场次 → 关联场次”的顺序找可复用的选项分析（指纹必须一致）。

    linked 为 ``(场次id, 该场次中的题号)`` 对：同一题库题在不同场次的
    题号可能不同，跨场次匹配必须用对方场次自己的题号。
    """
    own = _match_option_entry(option_analysis_entries(store.load(session_id)),
                              parent_question_id, fingerprint)
    if own is not None:
        return own
    for other in linked or ():
        try:
            other_id, other_parent = int(other[0]), str(other[1])
        except (TypeError, ValueError, IndexError):
            continue
        if other_id == int(session_id):
            continue
        entry = _match_option_entry(option_analysis_entries(store.load(other_id)),
                                    other_parent, fingerprint)
        if entry is not None:
            return entry
    return None


def _match_option_entry(
    entries: dict[str, Any], parent_question_id: str, fingerprint: str,
) -> dict[str, Any] | None:
    for qid, entry in entries.items():
        if _parent_qid(str(qid)) != str(parent_question_id):
            continue
        if not isinstance(entry, dict) or entry.get("version") != OPTION_ANALYSIS_VERSION:
            continue
        if entry.get("input_fingerprint") != fingerprint:
            continue
        return entry
    return None


def find_answer_patterns(
    store: Any,
    session_id: int,
    parent_question_id: int | str,
    linked_session_ids: Iterable[int],
) -> dict[str, dict[str, Any]]:
    """合并本场次与关联场次的填空错法库；键冲突时本场次优先。"""
    parent = str(parent_question_id)
    merged: dict[str, dict[str, Any]] = {}
    for other_id in linked_session_ids:
        if int(other_id) == int(session_id):
            continue
        bucket = answer_pattern_map(store.load(int(other_id))).get(parent) or {}
        for answer, entry in bucket.items():
            if answer.startswith("_") or not isinstance(entry, dict):
                continue
            merged.setdefault(answer, dict(entry))
    own = answer_pattern_map(store.load(session_id)).get(parent) or {}
    for answer, entry in own.items():
        if answer.startswith("_") or not isinstance(entry, dict):
            continue
        merged[answer] = dict(entry)
    return merged


# ---------------------------------------------------------------------------
# 选项分析校验与合成
# ---------------------------------------------------------------------------

def normalize_option_analysis(
    payload: dict[str, Any],
    *,
    option_letters: Iterable[str],
    correct_option: str,
) -> dict[str, dict[str, Any]]:
    """校验选项分析输出；非法选项字母、非法大类、正确选项条目直接剔除。"""
    if not isinstance(payload, dict):
        raise ValueError("option analysis payload must be a dict")
    allowed = {letter for letter in option_letters}
    entries: dict[str, dict[str, Any]] = {}
    options = payload.get("options")
    if not isinstance(options, list):
        raise ValueError("options must be a list")
    for item in options:
        if not isinstance(item, dict):
            continue
        letter = normalize_option_answer(item.get("option"))
        if not letter or letter not in allowed or letter == correct_option:
            continue
        category = normalize_cause_category(item.get("category"))
        if category not in CAUSE_CATEGORIES:
            continue
        pattern = str(item.get("pattern") or "").strip()[:40]
        if not pattern:
            continue
        entries[letter] = {
            "category": category,
            "pattern": pattern,
            "explanation": str(item.get("explanation") or "").strip()[:80],
        }
    if not entries:
        raise ValueError("no valid option entries")
    return entries


def _pattern_group(
    kind: str, category: str, pattern: str, manifestation: str,
    evidence_ids: list[str],
) -> dict[str, Any]:
    return {
        "kind": kind,
        "category": category,
        "reason": pattern,
        "manifestation": manifestation,
        "evidence_ids": sorted(set(evidence_ids)),
        "source_question_id": None,
        "step_id": None,
    }


def synthesize_option_result(
    source: dict[str, Any],
    option_patterns: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """学生所选项 → 错法组的确定性映射，输出 v3 模型负载形状。

    未作答证据进 response_state/未作答 组；识别不到选项字母或该选项
    没有诊断条目的计入 uncertain_ids（页面上显示为“原因未明”），不猜。
    """
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    uncertain: list[str] = []
    blank_ids: list[str] = []
    for evidence in source.get("evidence") or []:
        evidence_id = str(evidence.get("id") or "")
        raw_answer = str(evidence.get("student_answer") or "").strip()
        letter = normalize_option_answer(raw_answer)
        if not letter:
            (blank_ids if not raw_answer else uncertain).append(evidence_id)
            continue
        pattern = option_patterns.get(letter)
        if not isinstance(pattern, dict):
            uncertain.append(evidence_id)
            continue
        kind = kind_for_category(pattern.get("category"))
        if kind is None:
            uncertain.append(evidence_id)
            continue
        key = (str(pattern.get("category") or ""), str(pattern.get("pattern") or ""))
        bucket = groups.setdefault(key, {"kind": kind, "category": key[0], "reason": key[1],
                                         "manifestation": "", "evidence_ids": []})
        bucket["evidence_ids"].append(evidence_id)
        explanation = str(pattern.get("explanation") or "").strip() or f"选择{letter}项"
        if not bucket["manifestation"]:
            bucket["manifestation"] = explanation
    out_groups = [
        _pattern_group(bucket["kind"], bucket["category"], bucket["reason"],
                       bucket["manifestation"] or "选项作答表现", bucket["evidence_ids"])
        for bucket in groups.values() if bucket["reason"]
    ]
    if blank_ids:
        out_groups.append(_pattern_group(
            "response_state", CAUSE_CATEGORY_UNANSWERED, "未作答或无法辨认",
            "未作答或无法辨认", blank_ids,
        ))
    return {"groups": out_groups, "positive_ids": [], "uncertain_ids": sorted(set(uncertain))}


def synthesize_answer_result(
    source: dict[str, Any],
    library: dict[str, dict[str, Any]],
    canonical_answer: Any,
) -> dict[str, Any] | None:
    """填空错法库全覆盖时返回 v3 负载形状；出现库外/等价正确答案返回 None。"""
    canonical = normalize_wrong_answer(canonical_answer)
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    blank_ids: list[str] = []
    for evidence in source.get("evidence") or []:
        evidence_id = str(evidence.get("id") or "")
        raw_answer = str(evidence.get("student_answer") or "").strip()
        if not raw_answer:
            blank_ids.append(evidence_id)
            continue
        normalized = normalize_wrong_answer(raw_answer)
        if not normalized or (canonical and normalized == canonical):
            return None
        pattern = library.get(normalized)
        if not isinstance(pattern, dict):
            return None
        kind = kind_for_category(pattern.get("category"))
        if kind is None:
            return None
        key = (str(pattern.get("category") or ""), str(pattern.get("pattern") or ""))
        bucket = groups.setdefault(key, {"kind": kind, "category": key[0], "reason": key[1],
                                         "manifestation": "", "evidence_ids": []})
        bucket["evidence_ids"].append(evidence_id)
        explanation = str(pattern.get("explanation") or "").strip() or f"作答“{normalized}”"
        if not bucket["manifestation"]:
            bucket["manifestation"] = explanation
    out_groups = [
        _pattern_group(bucket["kind"], bucket["category"], bucket["reason"],
                       bucket["manifestation"] or "错误答案", bucket["evidence_ids"])
        for bucket in groups.values() if bucket["reason"]
    ]
    if blank_ids:
        out_groups.append(_pattern_group(
            "response_state", CAUSE_CATEGORY_UNANSWERED, "未作答或无法辨认",
            "未作答或无法辨认", blank_ids,
        ))
    return {"groups": out_groups, "positive_ids": [], "uncertain_ids": []}


def additions_from_v3_result(
    source: dict[str, Any],
    result: dict[str, Any],
    canonical_answer: Any,
) -> list[dict[str, Any]]:
    """把 v3 整理结果中的错误答案绑定为候选库条目（跳过与正确答案等价的）。"""
    evidence_by_id = {
        str(item.get("id") or ""): item
        for item in source.get("evidence") or []
    }
    canonical = normalize_wrong_answer(canonical_answer)
    additions: dict[str, dict[str, Any]] = {}
    for group in result.get("groups") or []:
        if group.get("kind") not in CAUSE_KIND_CATEGORIES:
            continue
        category = str(group.get("category") or "")
        if not category or category == CAUSE_CATEGORY_UNANSWERED:
            continue
        pattern = str(group.get("reason") or "").strip()
        if not pattern:
            continue
        explanation = "；".join(
            str(item.get("description") or "")
            for item in group.get("manifestations") or []
            if item.get("description")
        )[:80]
        for evidence_id in group.get("evidence_ids") or []:
            raw_answer = str((evidence_by_id.get(str(evidence_id)) or {}).get("student_answer") or "").strip()
            normalized = normalize_wrong_answer(raw_answer)
            if not normalized or (canonical and normalized == canonical):
                continue
            additions.setdefault(normalized, {
                "answer": normalized,
                "category": category,
                "pattern": pattern,
                "explanation": explanation,
                "source": "cause_v3",
            })
    return list(additions.values())


def build_option_analysis_input(
    question_text: str, canonical_answer: str, reference_analysis: str,
) -> dict[str, Any]:
    return {
        "question_text": str(question_text or ""),
        "correct_answer": str(canonical_answer or ""),
        "reference_analysis": str(reference_analysis or "")[:2000],
    }


def option_input_fingerprint(source: dict[str, Any]) -> str:
    payload = json.dumps(source, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def merge_bank_triggers_into_patterns(
    confirmed_rows: list[dict[str, Any]],
    *,
    trigger_kind: str,
    sources: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """题库可用行 → {trigger_value: 合成用条目}，教师调整优先。

    sources 非空时只取对应来源的行（例如只让 teacher_edit 覆盖会话内
    已有的选项分析结果）。
    """
    merged: dict[str, dict[str, Any]] = {}
    from question_bank.services.error_pattern_service import (
        pattern_preference, preferred_active_patterns,
    )

    ordered = sorted(
        preferred_active_patterns(
            row for row in confirmed_rows
            if sources is None or str(row.get("source") or "") in sources
        ),
        key=pattern_preference,
    )
    for row in ordered:
        if row.get("trigger_kind") != trigger_kind:
            continue
        value = str(row.get("trigger_value") or "").strip()
        if not value or value in merged:
            continue
        merged[value] = {
            "category": row.get("category"),
            "pattern": row.get("pattern"),
            "explanation": row.get("explanation") or "",
            "status": str(row.get("status") or "candidate"),
        }
    return merged


def sync_session_patterns_to_bank(
    store: Any,
    session_id: int,
    question_bank_path: Path | None,
    bank_context: dict[str, dict[str, Any]],
    *, current_sources: list[dict[str, Any]] | None = None,
) -> int:
    """把本场整理出的典型错法自动回挂题库（source='ai_auto'）；返回新增行数。

    扫描整场状态文件，按 (题, 触发, 触发值[, 错法名]) 幂等：重复运行只补
    出现快照、不重复建行。只写 bank_context 中已关联的题（写 ctx["bank_id"]，
    判重家族成员经 bank_ids 读取侧覆盖）；表缺失/未关联直接跳过。
    """
    from backend.class_analysis import CAUSE_ANALYSIS_VERSION
    from question_bank.services.error_pattern_service import record_auto_patterns

    if question_bank_path is None or not bank_context:
        return 0
    state = store.load(session_id) or {}
    if current_sources is not None:
        # 维护回填及正常整理只回挂当前输入匹配的成果；旧缓存不能制造新证据。
        from backend.class_analysis import cause_input_matches
        questions = ((state.get("cause_analysis") or {}).get("questions")) or {}
        valid = {str(source["question_id"]): source for source in current_sources
                 if cause_input_matches(questions.get(str(source["question_id"])) or {}, source)
                 and (questions.get(str(source["question_id"])) or {}).get("version") == CAUSE_ANALYSIS_VERSION}
        state = {**state, "cause_analysis": {"questions": {qid: questions[qid] for qid in valid}},
                 "option_analysis": {qid: entry for qid, entry in option_analysis_entries(state).items() if qid in valid},
                 "answer_patterns": {}}
        # 错误答案库本身不带学生证据；从本次有效的填空整理成果重新取有证据的条目。
        answers: dict[str, dict[str, Any]] = {}
        for qid, source in valid.items():
            ctx = bank_context.get(_parent_qid(qid)) or {}
            row = bank_question_row(question_bank_path, int(ctx.get("bank_id") or 0)) or {}
            if row.get("question_type") not in FILL_TYPES:
                continue
            additions = additions_from_v3_result(source, questions[qid].get("result") or {}, source.get("canonical_answer"))
            answers.setdefault(_parent_qid(qid), {}).update({item["answer"]: item for item in additions})
        state["answer_patterns"] = answers
    occurrence_base = {"session_id": int(session_id)}
    rows: list[dict[str, Any]] = []

    def ctx_for(qid: str) -> dict[str, Any] | None:
        ctx = bank_context.get(_parent_qid(str(qid))) or {}
        return ctx if ctx.get("bank_id") else None

    # 选项诊断写题目错法；只有实际选择该项的作答才附出现快照。
    for qid, entry in option_analysis_entries(state).items():
        ctx = ctx_for(qid)
        if ctx is None or not isinstance(entry, dict):
            continue
        if entry.get("failed"):
            continue
        analysis = entry.get("analysis")
        if not isinstance(analysis, dict):
            continue
        saved = (((state.get("cause_analysis") or {}).get("questions") or {}).get(qid) or {})
        saved_input = saved.get("input") or {}
        bank_id = int(entry.get("bank_question_id") or ctx["bank_id"])
        bank_row = bank_question_row(question_bank_path, bank_id) or {}
        current_text = str(saved_input.get("question_text") or "")
        if len(parse_option_letters(current_text)) < 2:
            current_text = str(bank_row.get("question_text") or "")
        correct = normalize_option_answer(saved_input.get("canonical_answer")) or extract_canonical_option(
            bank_row.get("answer_text")
        )
        if entry.get("input_fingerprint") != question_fingerprint(
            current_text, correct
        ):
            continue
        used_ids = {
            str(evidence_id)
            for group in ((saved.get("result") or {}).get("groups") or [])
            for evidence_id in group.get("evidence_ids") or []
        }
        observed = {
            normalize_option_answer(evidence.get("student_answer"))
            for evidence in saved_input.get("evidence") or []
            if str(evidence.get("id") or "") in used_ids
        }
        for letter, item in analysis.items():
            if not isinstance(item, dict):
                continue
            pattern = str(item.get("pattern") or "").strip()
            if not pattern:
                continue
            occurrence = (
                {**occurrence_base, "question_id": str(qid)}
                if str(letter).strip() in observed else None
            )
            if entry.get("source") != "model" and occurrence is None:
                continue
            rows.append({
                "question_id": bank_id,
                "category": normalize_cause_category(item.get("category")),
                "pattern": pattern,
                "explanation": str(item.get("explanation") or "").strip(),
                "trigger_kind": "option",
                "trigger_value": str(letter).strip(),
                "source": "ai_auto",
                "occurrence": occurrence,
            })

    # 填空错误答案库：v3 归纳出的条目按规范化答案写 wrong_answer 触发。
    for parent, bucket in answer_pattern_map(state).items():
        ctx = ctx_for(parent)
        if ctx is None:
            continue
        for answer, item in bucket.items():
            if str(answer).startswith("_") or not isinstance(item, dict):
                continue
            if str(item.get("source") or "") != "cause_v3":
                continue
            pattern = str(item.get("pattern") or "").strip()
            if not pattern:
                continue
            rows.append({
                "question_id": int(ctx["bank_id"]),
                "category": normalize_cause_category(item.get("category")),
                "pattern": pattern,
                "explanation": str(item.get("explanation") or "").strip(),
                "trigger_kind": "wrong_answer",
                "trigger_value": str(answer),
                "source": "ai_auto",
                "occurrence": {**occurrence_base, "question_id": str(parent)},
            })

    # v3 整题整理：error/process 组写 step（可定位判定点）或 observation。
    questions = ((state.get("cause_analysis") or {}).get("questions")) or {}
    for qid, entry in questions.items():
        ctx = ctx_for(qid)
        if ctx is None or not isinstance(entry, dict):
            continue
        if entry.get("version") != CAUSE_ANALYSIS_VERSION or entry.get("origin") != "model":
            continue
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        for group in result.get("groups") or []:
            if not isinstance(group, dict):
                continue
            if str(group.get("kind") or "") not in ("error", "process"):
                continue
            if not group.get("evidence_ids"):
                continue
            category = normalize_cause_category(group.get("category"))
            reason = str(group.get("reason") or "").strip()
            if not category or not reason:
                continue
            step_id = str(group.get("step_id") or "").strip()
            explanation = "；".join(
                str(item.get("description") or "")
                for item in group.get("manifestations") or []
                if item.get("description")
            )[:200]
            rows.append({
                "question_id": int(ctx["bank_id"]),
                "category": category,
                "pattern": reason,
                "explanation": explanation,
                "trigger_kind": "step" if step_id else "observation",
                "trigger_value": step_id,
                "source": "ai_auto",
                "occurrence": {**occurrence_base, "question_id": str(qid)},
            })
    return record_auto_patterns(Path(question_bank_path), rows)


__all__ = [
    "CHOICE_TYPES",
    "FILL_TYPES",
    "OPTION_ANALYSIS_PROMPT",
    "OPTION_ANALYSIS_VERSION",
    "additions_from_v3_result",
    "answer_pattern_map",
    "bank_confirmed_triggers",
    "bank_question_row",
    "build_option_analysis_input",
    "extract_canonical_option",
    "find_answer_patterns",
    "find_option_analysis",
    "kind_for_category",
    "merge_bank_triggers_into_patterns",
    "normalize_option_analysis",
    "normalize_option_answer",
    "normalize_wrong_answer",
    "option_analysis_entries",
    "option_input_fingerprint",
    "parse_option_letters",
    "question_fingerprint",
    "record_answer_patterns",
    "save_option_analysis",
    "session_bank_context",
    "session_bank_map",
    "sync_session_patterns_to_bank",
    "synthesize_answer_result",
    "synthesize_option_result",
]
