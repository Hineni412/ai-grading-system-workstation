"""Build skill-layer candidates by clustering solution-evidence point targets.

Read-only against the question bank database. Implements the local-computation
stage of docs/requests/2026-09-16-criterion-based-tags-and-mastery-redesign.md
section 3.2: normalize evidence-point targets, bucket objective answer points,
cluster per chapter by dictionary + trigram similarity, name clusters by the
most frequent verb+term pair, and attach each cluster to a curriculum section
by member-question majority.

Usage:
    python tools/build_skill_layer.py [--db PATH] [--out DIR]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from question_bank.taxonomy.curriculum_catalog import load_curriculum_catalog


DEFAULT_DB = ROOT / "user_data" / "databases" / "question_bank.db"
DEFAULT_OUT = ROOT / "output" / "skill_layer_design"

CLUSTER_THRESHOLD = 0.45
MIN_CLUSTER_SIZE = 8
MAX_SKILLS_PER_CHAPTER = 30


def match_teaching_skill(target: str, chapter_keys: Sequence[str], standard: Mapping[str, Any]) -> str | None:
    """Conservative local repair: only one explicit, non-conflicting operation.

    Keep formulas intact. Never infer a skill from the question's story, its
    answer, cluster size, or text similarity. Ambiguity stays at section level.
    """
    text = re.sub(r"\s+", "", str(target or ""))
    matches = [skill['id'] for skill in standard['skills']
               if skill['section_key'].rsplit('_', 1)[0] in chapter_keys
               and not any(re.search(pattern, text) for pattern in skill['exclude_patterns'])
               and any(re.search(pattern, text) for pattern in skill['patterns'])]
    return matches[0] if len(matches) == 1 else None

VERBS = (
    "确认", "明确", "设", "表示", "列", "解", "求", "化简", "合并", "代入",
    "判定", "证明", "比较", "估算", "作", "写出", "读出", "联立", "展开",
    "验证",
)

# Terms are matched as substrings of the normalized (Chinese-only) target text.
# Longer forms are listed before their prefixes so naming prefers the more
# specific object.
TERM_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # 勾股定理 chapter
    ("勾股定理逆定理", ("勾股定理的逆定理", "勾股定理逆定理", "逆定理")),
    ("勾股定理", ("勾股定理", "股定理")),
    ("直角三角形", ("直角三角形", "直角三角")),
    ("等腰直角三角形", ("等腰直角三角形", "等腰直角三角", "腰直角三角形")),
    ("等腰三角形", ("等腰三角形", "等腰三角")),
    ("直角", ("直角",)),
    ("斜边", ("斜边",)),
    ("直角边", ("直角边", "两直角边")),
    ("三角形", ("三角形", "角形")),
    ("全等三角形", ("全等三角形", "全等三角")),
    ("四边形", ("四边形",)),
    ("正方形", ("正方形",)),
    ("长方形", ("长方形", "矩形")),
    ("面积", ("面积",)),
    ("周长", ("周长",)),
    ("最短路径", ("最短路径", "最短距离", "最小值", "最短")),
    ("展开图", ("展开图", "侧面展开", "展开")),
    ("折叠", ("折叠", "对折")),
    ("对称点", ("对称点", "轴对称", "对称")),
    ("距离", ("距离",)),
    ("高度", ("高度", "高为")),
    ("角度", ("角度", "度数")),
    ("平分线", ("角平分线", "平分线")),
    ("中点", ("中点",)),
    ("垂直平分线", ("垂直平分线", "中垂线")),
    ("网格", ("网格", "方格")),
    ("勾股数", ("勾股数",)),
    ("弦图", ("弦图",)),
    ("命题", ("命题",)),
    ("蚂蚁", ("蚂蚁",)),
    ("圆柱", ("圆柱",)),
    ("长方体", ("长方体",)),
    ("梯子", ("梯子",)),
    ("旗杆", ("旗杆",)),
    ("数轴", ("数轴",)),
    ("方程", ("方程",)),
    ("函数", ("函数",)),
    ("证明", ("证明", "证全等", "证")),
    ("判定", ("判定",)),
    # 实数 chapter
    ("二次根式", ("二次根式", "次根式")),
    ("同类二次根式", ("同类二次根式", "同类二次根")),
    ("最简二次根式", ("最简二次根式", "最简二次根", "最简")),
    ("分母有理化", ("分母有理化", "母有理化", "有理化")),
    ("平方根", ("平方根",)),
    ("算术平方根", ("算术平方根",)),
    ("立方根", ("立方根",)),
    ("被开方数", ("被开方数", "开方数")),
    ("无理数", ("无理数",)),
    ("有理数", ("有理数",)),
    ("实数", ("实数",)),
    ("绝对值", ("绝对值",)),
    ("相反数", ("相反数",)),
    ("倒数", ("倒数",)),
    ("零指数幂", ("零指数幂", "零指数")),
    ("负整数指数幂", ("负整数指数幂", "负整数指数", "负指数幂")),
    ("指数幂", ("指数幂", "指数")),
    ("平方差公式", ("平方差公式", "平方差")),
    ("完全平方公式", ("完全平方公式", "全平方公式", "完全平方")),
    ("乘法公式", ("乘法公式", "乘法法则", "除法法则", "法法则")),
    ("估算", ("估算", "估计", "近似")),
    ("整数部分", ("整数部分",)),
    ("小数部分", ("小数部分",)),
    ("化简", ("化简",)),
    ("计算", ("计算", "算出", "求得")),
    ("一元一次方程", ("一元一次方程", "元一次方程")),
    ("合并同类项", ("合并同类项", "同类项")),
    ("去括号", ("去括号", "括号")),
    ("数轴", ("数轴",)),
    ("幂", ("幂运算", "乘方", "幂")),
    ("单项式", ("单项式",)),
    ("多项式", ("多项式",)),
    ("科学记数法", ("科学记数法",)),
    # 位置与坐标 chapter
    ("平面直角坐标系", ("平面直角坐标系", "直角坐标系", "坐标系")),
    ("坐标", ("坐标",)),
    ("横坐标", ("横坐标",)),
    ("纵坐标", ("纵坐标",)),
    ("象限", ("象限",)),
    ("原点", ("原点",)),
    ("顶点", ("顶点",)),
    ("顺次连接", ("顺次连接", "次连接")),
    ("平均变化速度", ("平均变化速度", "变化速度")),
    ("位置", ("位置",)),
    ("平移", ("平移",)),
    ("作图", ("作图", "画出", "描点")),
    # 一次函数 chapter
    ("一次函数", ("一次函数", "次函数")),
    ("正比例函数", ("正比例函数",)),
    ("函数解析式", ("函数解析式", "数解析式")),
    ("解析式", ("解析式", "关系式", "表达式")),
    ("待定系数", ("待定系数", "定系数")),
    ("函数值", ("函数值",)),
    ("函数图象", ("函数图象", "数图象", "图象")),
    ("截距", ("截距",)),
    ("交点", ("交点",)),
    ("取值范围", ("取值范围", "值范围")),
    ("自变量", ("自变量",)),
    ("因变量", ("因变量",)),
    ("用电量", ("用电量",)),
    ("方案", ("方案",)),
    ("速度", ("速度",)),
    ("路程", ("路程",)),
    ("利润", ("利润",)),
    # 二元一次方程组 chapter
    ("二元一次方程组", ("二元一次方程组", "方程组")),
    ("代入消元", ("代入消元", "代入")),
    ("加减消元", ("加减消元", "两式相加", "两式相减", "式相加", "消元")),
    ("回代", ("回代",)),
    ("正整数解", ("正整数解", "正整数")),
    ("整数解", ("整数解",)),
    ("单价", ("单价",)),
    ("未知数", ("未知数",)),
    # 数据的分析 chapter
    ("平均数", ("平均数", "平均")),
    ("加权平均数", ("加权平均数",)),
    ("中位数", ("中位数",)),
    ("众数", ("众数",)),
    ("方差", ("方差",)),
    ("极差", ("极差",)),
    ("样本", ("样本",)),
    ("总体", ("总体",)),
    ("频数", ("频数",)),
    ("频率", ("频率",)),
    ("合格率", ("合格率", "合格")),
    ("统计图", ("统计图", "扇形", "条形图")),
    ("平均商", ("平均商",)),
    # 相交线与平行线 / 证明 chapters
    ("平行线", ("平行线", "平行")),
    ("同位角", ("同位角",)),
    ("内错角", ("内错角",)),
    ("同旁内角", ("同旁内角",)),
    ("对顶角", ("对顶角",)),
    ("余角", ("余角",)),
    ("补角", ("补角",)),
    ("垂直", ("垂直", "垂线")),
    ("内角和", ("内角和",)),
    ("外角", ("外角",)),
    ("底角", ("底角",)),
    ("顶角", ("顶角",)),
    ("推理依据", ("推理依据", "依据")),
    ("尺规作图", ("尺规", "尺规作图")),
    # 三角形 chapter
    ("高", ("边上的高", "高为", "求高")),
    ("中线", ("中线",)),
    # 概率 chapter
    ("概率", ("概率",)),
    ("随机事件", ("随机事件", "事件类型", "事件")),
    ("必然事件", ("必然事件",)),
    ("不可能事件", ("不可能事件",)),
    ("转盘", ("转盘", "指针")),
    ("摸球", ("摸球", "抽取")),
    # 变量之间的关系 chapter
    ("关系式", ("关系式",)),
    ("表格", ("表格",)),
    ("销量", ("销量",)),
    ("售价", ("售价",)),
    ("温度", ("温度",)),
    ("时间", ("时间",)),
    ("字母表示", ("字母表示", "字母")),
    # 轴对称 chapter
    ("对称轴", ("对称轴",)),
    ("像", ("镜像", "镜子", "的像")),
    # Generic step vocabulary: after symbol stripping many points keep only
    # the action word; these terms let them form honest "generic" skills.
    ("结果", ("解得", "求得", "算出", "得出", "得到", "求出", "计算得", "可知", "即为", "故", "结果是")),
    ("设元", ("设出", "设为", "用表示", "表示", "记为", "用字母", "设", "代数式")),
    ("推理", ("推出", "推导", "推得", "证得", "可知", "理由", "依据")),
    ("变形", ("变形", "整理", "化为", "转化成", "写成", "展开")),
    ("合并", ("合并", "相加", "各项", "并得")),
    ("移项", ("移项",)),
    ("去分母", ("去分母", "分母")),
    ("边长", ("边长", "底边", "线段", "长为", "长度", "边")),
    ("比较大小", ("比较", "大小", "大于", "小于")),
    ("意义", ("意义", "含义", "表示什么")),
    ("分类讨论", ("情形", "分类", "分别", "情况")),
    ("总数", ("总数", "总量", "共有", "个数", "数量", "人数")),
    ("费用", ("费用", "花费", "总价", "收费", "每分钟元", "购买", "售价")),
    ("时间", ("时间", "分钟", "小时", "时长")),
    ("阅读", ("阅读", "观察", "发现", "规律")),
)

# Canonical term id -> surface forms (deduplicated, longer first).
TERM_SURFACES: dict[str, tuple[str, ...]] = {}
for _term, _forms in TERM_GROUPS:
    forms = tuple(dict.fromkeys((_term, *_forms)))
    TERM_SURFACES[_term] = tuple(sorted(forms, key=len, reverse=True))

# Action-level terms describe what a step does (obtain, deduce, transform)
# rather than which knowledge object it uses. A point with at least one
# domain hit clusters on domain vocabulary only; generic hits alone group
# into the chapter's generic skills (求解结果 / 推理 / 设元 ...).
GENERIC_TERMS = frozenset(
    {
        "结果",
        "设元",
        "推理",
        "变形",
        "合并",
        "移项",
        "去分母",
        "比较大小",
        "意义",
        "分类讨论",
        "阅读",
    }
)

ANSWER_RESULT_RE = re.compile(r"^(作答为|作答是|答案为|给出.*答案|选择)")


def normalize_target(text: object) -> str:
    """Keep Chinese characters only; drop digits, letters, symbols, brackets."""
    raw = str(text or "")
    raw = re.sub(r"\([^)]*\)|（[^）]*）|\[[^\]]*\]|【[^】]*】", "", raw)
    return re.sub(r"[^一-鿿]+", "", raw)


def term_hits(normalized: str) -> frozenset[str]:
    if not normalized:
        return frozenset()
    hits = set()
    for term, forms in TERM_SURFACES.items():
        for form in forms:
            if form and form in normalized:
                hits.add(term)
                break
    # A point cannot both be about the generic theorem and its converse only;
    # keep both hits when both surface so similarity can separate them.
    return frozenset(hits)


def ngrams(text: str, n: int) -> frozenset[str]:
    if len(text) < n:
        return frozenset({text} if text else ())
    return frozenset(text[i : i + n] for i in range(len(text) - n + 1))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def similarity(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    return 0.6 * jaccard(a["cluster_terms"], b["cluster_terms"]) + 0.4 * jaccard(
        a["trigrams"], b["trigrams"]
    )


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


def cluster_points(points: list[dict[str, Any]]) -> list[list[int]]:
    """Single-linkage agglomerative clustering at the configured threshold.

    Only pairs sharing at least one dictionary term can reach the threshold
    (0.4 * trigram-Jaccard alone caps below it), so the merge graph is built
    through a term -> point index instead of all pairs.
    """
    index: dict[str, list[int]] = collections.defaultdict(list)
    for i, point in enumerate(points):
        for term in point["cluster_terms"]:
            index[term].append(i)
    uf = _UnionFind(len(points))
    for members in index.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                if uf.find(a) == uf.find(b):
                    continue
                if similarity(points[a], points[b]) >= CLUSTER_THRESHOLD:
                    uf.union(a, b)
    clusters: dict[int, list[int]] = collections.defaultdict(list)
    for i in range(len(points)):
        clusters[uf.find(i)].append(i)
    return list(clusters.values())


def cluster_similarity(
    points: list[dict[str, Any]],
    cluster_a: Sequence[int],
    cluster_b: Sequence[int],
    *,
    sample: int = 60,
) -> float:
    values = [
        similarity(points[a], points[b])
        for a in cluster_a[:sample]
        for b in cluster_b[:sample]
    ]
    return sum(values) / len(values) if values else 0.0


def merge_small_clusters(
    points: list[dict[str, Any]],
    clusters: list[list[int]],
) -> tuple[list[list[int]], list[int]]:
    """Merge clusters below MIN_CLUSTER_SIZE into their nearest big cluster.

    Returns (big_clusters, misc_members). A small cluster with no positively
    similar big cluster becomes misc for later manual handling.
    """
    big = [list(c) for c in clusters if len(c) >= MIN_CLUSTER_SIZE]
    small = [list(c) for c in clusters if len(c) < MIN_CLUSTER_SIZE]
    misc: list[int] = []
    for cluster in sorted(small, key=len):
        best_index = -1
        best_score = 0.0
        for index, target in enumerate(big):
            score = cluster_similarity(points, cluster, target)
            if score > best_score:
                best_index, best_score = index, score
        if best_index >= 0:
            big[best_index].extend(cluster)
        else:
            misc.extend(cluster)
    return big, misc


def cluster_name(
    points: list[dict[str, Any]],
    members: Sequence[int],
    used_names: set[str],
) -> tuple[str, list[str]]:
    """Name a cluster by the most frequent verb+domain-term pair.

    Degenerate pairs where the verb is contained in the term collapse to the
    bare term ("判定" not "判定判定"). Domain-majority clusters name domain
    objects; generic clusters keep action terms.
    """
    domain_count = sum(1 for i in members if points[i]["domain_terms"])
    use_domain = domain_count * 2 >= len(members)
    counts: collections.Counter[tuple[str, str]] = collections.Counter()
    term_counts: collections.Counter[str] = collections.Counter()
    for index in members:
        point = points[index]
        verbs = [verb for verb in VERBS if verb in point["normalized"]]
        terms = (
            point["domain_terms"]
            if use_domain and point["domain_terms"]
            else point["terms"]
        )
        term_counts.update(terms)
        for verb in verbs:
            for term in terms:
                counts[(verb, term)] += 1
    candidates: list[str] = []
    for (verb, term), _ in counts.most_common():
        candidates.append(term if verb in term else f"{verb}{term}")
    candidates.extend(term for term, _ in term_counts.most_common())
    candidates.append("手順")
    name = ""
    aliases: list[str] = []
    for candidate in candidates:
        clean = candidate[:12]
        if not clean or clean in used_names or clean in aliases:
            continue
        if not name:
            name = clean
        elif len(aliases) < 3:
            aliases.append(clean)
        if len(aliases) >= 3:
            break
    if not name:
        suffix = 2
        while f"手順{suffix}" in used_names:
            suffix += 1
        name = f"手順{suffix}"
    used_names.add(name)
    return name, aliases


def chapter_section_index(
    catalog: Mapping[str, Any],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, str],
]:
    """Return chapter-by-id, section-by-id and exam-scope-label -> chapter id."""
    chapters: dict[str, dict[str, Any]] = {}
    sections: dict[str, dict[str, Any]] = {}
    scope_to_chapter: dict[str, str] = {}
    for volume in catalog["volumes"]:
        for chapter in volume["chapters"]:
            chapters[chapter["id"]] = {**chapter, "volume": volume}
            for label in chapter["exam_scope_values"]:
                scope_to_chapter[label] = chapter["id"]
            for section in chapter["sections"]:
                sections[section["id"]] = {
                    **section,
                    "chapter_id": chapter["id"],
                    "volume_id": volume["id"],
                }
    return chapters, sections, scope_to_chapter


def load_points(
    db_path: Path,
    chapters: Mapping[str, Any],
    sections: Mapping[str, Any],
    scope_to_chapter: Mapping[str, str],
) -> list[dict[str, Any]]:
    connection = sqlite3.connect(
        Path(db_path).resolve().as_uri() + "?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT question_id, evidence_version_id, evidence_json
            FROM question_solution_evidence_versions
            ORDER BY question_id, created_at DESC, evidence_version_id DESC
            """
        ).fetchall()
        latest: dict[int, sqlite3.Row] = {}
        for row in rows:
            latest.setdefault(int(row["question_id"]), row)
        tag_rows = connection.execute(
            """
            SELECT question_id, tag_type, tag_value
            FROM question_tags
            WHERE tag_type IN ('exam_scope', 'curriculum_section')
            """
        ).fetchall()
        question_rows = connection.execute(
            "SELECT id, question_type FROM questions"
        ).fetchall()
    finally:
        connection.close()

    tags: dict[int, dict[str, list[str]]] = collections.defaultdict(
        lambda: collections.defaultdict(list)
    )
    for row in tag_rows:
        tags[int(row["question_id"])][str(row["tag_type"])].append(
            str(row["tag_value"])
        )
    question_type = {
        int(row["id"]): str(row["question_type"] or "")
        for row in question_rows
    }

    def question_chapter(question_id: int) -> str | None:
        section_ids = tags[question_id].get("curriculum_section") or []
        section_votes = collections.Counter(
            sections[section_id]["chapter_id"]
            for section_id in section_ids
            if section_id in sections
        )
        if section_votes:
            return section_votes.most_common(1)[0][0]
        for label in tags[question_id].get("exam_scope") or []:
            if label in scope_to_chapter:
                return scope_to_chapter[label]
        return None

    points: list[dict[str, Any]] = []
    for question_id, row in latest.items():
        evidence = json.loads(str(row["evidence_json"]))
        chapter_id = question_chapter(question_id)
        section_ids = tuple(
            dict.fromkeys(tags[question_id].get("curriculum_section") or [])
        )
        for part in evidence.get("parts", []):
            mode = str(part.get("response_mode") or "")
            for point in part.get("evidence_points", []):
                target = str(point.get("target") or "")
                normalized = normalize_target(target)
                is_answer = bool(
                    mode == "exact_objective"
                    and ANSWER_RESULT_RE.match(str(point.get("target") or "").strip())
                )
                points.append(
                    {
                        "question_id": question_id,
                        "evidence_version_id": str(row["evidence_version_id"]),
                        "part_id": str(part.get("part_id") or ""),
                        "evidence_point_id": str(
                            point.get("evidence_point_id") or ""
                        ),
                        "step_index": point.get("step_index"),
                        "target": target,
                        "normalized": normalized,
                        "response_mode": mode,
                        "question_type": question_type.get(question_id, ""),
                        "chapter_id": chapter_id,
                        "section_ids": section_ids,
                        "is_answer_result": is_answer,
                        "terms": term_hits(normalized),
                        "domain_terms": term_hits(normalized) - GENERIC_TERMS,
                        "cluster_terms": (
                            (term_hits(normalized) - GENERIC_TERMS)
                            or term_hits(normalized)
                            or frozenset({"__unmatched__"})
                        ),
                        "trigrams": ngrams(normalized, 3),
                    }
                )
    return points


def assign_section(
    points: list[dict[str, Any]],
    members: Sequence[int],
    sections: Mapping[str, Any],
    chapter_id: str,
    chapter: Mapping[str, Any],
) -> tuple[str, str]:
    """Return (section_id, note) for a cluster by member-question majority."""
    votes: collections.Counter[str] = collections.Counter()
    for index in members:
        for section_id in points[index]["section_ids"]:
            if sections.get(section_id, {}).get("chapter_id") == chapter_id:
                votes[section_id] += 1
    total = sum(votes.values())
    if votes:
        section_id, count = votes.most_common(1)[0]
        if total and count / total >= 0.5:
            return section_id, ""
    preferred = next(
        (
            section
            for section in chapter["sections"]
            if "综合" in str(section.get("title") or "")
        ),
        None,
    )
    if preferred is not None:
        return preferred["id"], "member_sections_divergent"
    first = chapter["sections"][0]
    return first["id"], "member_sections_divergent_attached_first_section"


def build(catalog: Mapping[str, Any], points: list[dict[str, Any]]) -> dict[str, Any]:
    chapters, sections, _ = chapter_section_index(catalog)
    by_chapter: dict[str, list[int]] = collections.defaultdict(list)
    answer_count = 0
    no_chapter = 0
    for index, point in enumerate(points):
        if point["is_answer_result"]:
            answer_count += 1
            continue
        if point["chapter_id"] is None:
            no_chapter += 1
            continue
        by_chapter[point["chapter_id"]].append(index)

    skills: list[dict[str, Any]] = []
    misc_report: dict[str, Any] = {}
    covered = 0
    for chapter_id in sorted(by_chapter):
        members = by_chapter[chapter_id]
        chapter = chapters[chapter_id]
        volume = chapter["volume"]
        chapter_num = int(chapter["order"])
        raw_clusters = cluster_points([points[i] for i in members])
        # cluster_points works on a local list; remap indices.
        local_points = [points[i] for i in members]
        big, misc = merge_small_clusters(local_points, raw_clusters)
        chapter_skills: list[dict[str, Any]] = []
        used_names: set[str] = set()
        for cluster in sorted(big, key=len, reverse=True):
            name, aliases = cluster_name(local_points, cluster, used_names)
            section_id, note = assign_section(
                local_points, cluster, sections, chapter_id, chapter
            )
            chapter_skills.append(
                {
                    "name": name,
                    "aliases": aliases,
                    "section_id": section_id,
                    "section_note": note,
                    "count": len(cluster),
                    "members": cluster,
                    "sample_targets": [
                        local_points[i]["target"] for i in cluster[:5]
                    ],
                }
            )
        # Cap per-chapter skills: fold smallest extras into the largest ones.
        chapter_skills.sort(key=lambda item: -item["count"])
        if len(chapter_skills) > MAX_SKILLS_PER_CHAPTER:
            extra = chapter_skills[MAX_SKILLS_PER_CHAPTER:]
            chapter_skills = chapter_skills[:MAX_SKILLS_PER_CHAPTER]
            misc.extend(i for item in extra for i in item["members"])
        # Number skills inside each section.
        section_numbers = {
            section["id"]: int(section["order"])
            for section in chapter["sections"]
        }
        counters: collections.Counter[int] = collections.Counter()
        chapter_skills.sort(
            key=lambda item: (
                section_numbers.get(item["section_id"], 0),
                -item["count"],
            )
        )
        volume_token = str(volume["id"]).replace("bnu24-math-", "").replace(
            "-", "_"
        )
        for item in chapter_skills:
            section_num = section_numbers.get(item["section_id"], 0)
            counters[section_num] += 1
            skill_key = (
                f"sk_bnu24_math_{volume_token}_{chapter_num}_{section_num}_"
                f"{counters[section_num]:02d}"
            )
            section = sections[item["section_id"]]
            skills.append(
                {
                    "id": skill_key,
                    "name": item["name"],
                    "section_id": item["section_id"],
                    "section_key": section["knowledge_id"],
                    "chapter_id": chapter_id,
                    "chapter_key": chapter["knowledge_id"],
                    "volume_id": volume["id"],
                    "count": item["count"],
                    "sample_targets": item["sample_targets"],
                    "aliases": item["aliases"],
                    "section_note": item["section_note"],
                    "question_ids": sorted(
                        {local_points[i]["question_id"] for i in item["members"]}
                    ),
                }
            )
            covered += len(item["members"])
        if misc:
            misc_report[chapter_id] = {
                "count": len(misc),
                "sample_targets": [local_points[i]["target"] for i in misc[:10]],
            }

    non_answer = len(points) - answer_count
    clusterable = non_answer - no_chapter
    by_volume: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for skill in skills:
        by_volume[skill["volume_id"]].append(skill)
    report = {
        "total_points": len(points),
        "answer_result_points": answer_count,
        "non_answer_points": non_answer,
        "no_chapter_points": no_chapter,
        "clustered_points": covered,
        "misc_points": sum(item["count"] for item in misc_report.values()),
        "coverage": (covered / clusterable) if clusterable else 0.0,
        "skill_count": len(skills),
        "skills_per_chapter": dict(
            collections.Counter(skill["chapter_id"] for skill in skills)
        ),
        "misc": misc_report,
    }
    return {"skills_by_volume": dict(by_volume), "report": report}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    catalog = load_curriculum_catalog()
    chapters, sections, scope_to_chapter = chapter_section_index(catalog)
    points = load_points(args.db, chapters, sections, scope_to_chapter)
    result = build(catalog, points)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for volume_id, skills in result["skills_by_volume"].items():
        path = out_dir / f"skills_{volume_id}.json"
        payload = {
            "volume_id": volume_id,
            "origin": "skill_cluster_2026_09",
            "skills": [
                {key: value for key, value in skill.items() if key != "question_ids"}
                for skill in skills
            ],
        }
        # keep question_ids in a separate evidence file for review/debugging
        evidence_path = out_dir / f"skills_{volume_id}.members.json"
        evidence_path.write_text(
            json.dumps(
                {skill["id"]: skill["question_ids"] for skill in skills},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"wrote {path.name}: {len(skills)} skills")
    report_path = out_dir / "coverage_report.json"
    report_path.write_text(
        json.dumps(result["report"], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    report = result["report"]
    print(
        "coverage {covered}/{clusterable} = {rate:.1%}; skills={skills}; "
        "misc={misc}; answer={answer}; no_chapter={no_chapter}".format(
            covered=report["clustered_points"],
            clusterable=report["non_answer_points"] - report["no_chapter_points"],
            rate=report["coverage"],
            skills=report["skill_count"],
            misc=report["misc_points"],
            answer=report["answer_result_points"],
            no_chapter=report["no_chapter_points"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
