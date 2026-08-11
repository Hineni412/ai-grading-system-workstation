from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath

from backend.teaching_prep.domain.errors import TeachingPrepValidationError


_NUMBER_PREFIX = re.compile(
    r"(?<!\d)(?P<chapter>\d{1,2})[.．·]"
    r"(?P<section>\d{1,2})"
    r"(?:[.．·](?P<subsection>\d{1,2}))?(?!\d)"
)
_CHAPTER = re.compile(r"第\s*(?P<number>[一二三四五六七八九十百\d]+)\s*章")
_LESSON = re.compile(
    r"(?:第\s*(?P<prefix>[一二三四五六七八九十百\d]+)\s*课时"
    r"|(?P<suffix>[一二三四五六七八九十百\d]+)\s*课时)"
)
_SPECIAL = re.compile(
    r"(?:小结|复习|练习|习题|测试|检测|策略|专题|章末|单元卷|评价卷|作业)"
)
_TITLE_NOISE = re.compile(
    r"(?:新版|教学课件|优质课件|精品课件|课件|PPTX?|北师(?:大)?版|BS)",
    re.IGNORECASE,
)
_SPACE = re.compile(r"\s+")
_SAFE_SEGMENT = re.compile(r"^[^:/\\\x00-\x1f]+$")


@dataclass(frozen=True, slots=True)
class ReferencePptInput:
    material_record_id: str
    relative_path: str
    file_name: str
    unit_count: int
    first_slide_title: str | None = None


@dataclass(frozen=True, slots=True)
class ReferencePptIdentity:
    material_record_id: str
    relative_path: str
    file_name: str
    chapter_number: int | None
    section_number: int | None
    subsection_number: int | None
    lesson_number: int | None
    title: str
    kind: str
    confidence: str
    evidence: tuple[str, ...]
    issues: tuple[str, ...]


def infer_reference_ppt_collection(
    entries: Sequence[ReferencePptInput],
    *,
    existing_lessons: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    """Infer a reviewable lesson tree and whole-deck mappings without a model."""
    if not entries:
        raise TeachingPrepValidationError(
            "reference PPT collection requires at least one PPTX"
        )
    identities = tuple(_identity(entry) for entry in entries)
    if len({item.material_record_id for item in identities}) != len(identities):
        raise TeachingPrepValidationError(
            "reference PPT collection contains duplicate materials"
        )
    # Only usable lesson nodes decide between matching the existing tree and
    # proposing a candidate tree; stray chapter/section skeletons without
    # lessons cannot receive mappings.
    has_existing_lessons = any(
        str(node.get("node_type") or "") == "lesson" for node in existing_lessons
    )
    tree = _candidate_tree(identities) if not has_existing_lessons else []
    lesson_refs = _proposal_lesson_refs(tree)
    mappings: list[dict[str, object]] = []
    uncertainties: list[str] = []
    for entry, identity in zip(entries, identities):
        if identity.kind != "lesson":
            uncertainties.append(
                f"{identity.relative_path} 已作为{_kind_label(identity.kind)}收录，"
                "默认不增加新授课时"
            )
            continue
        if has_existing_lessons:
            lesson_ref, match_confidence = _match_existing_lesson(
                identity,
                existing_lessons,
            )
        else:
            lesson_ref = lesson_refs.get(_identity_key(identity))
            match_confidence = identity.confidence
        if lesson_ref is None:
            uncertainties.append(
                f"{identity.relative_path} 尚不能可靠对应课时，请人工确认"
            )
            continue
        mappings.append(
            {
                "material_record_id": entry.material_record_id,
                "lesson_ref": lesson_ref,
                "start_unit": 1,
                "end_unit": entry.unit_count,
                "purpose": "reference_ppt",
                "confidence": match_confidence,
                "evidence": list(identity.evidence),
                "decision_reason": (
                    "本机依据课件相对路径、编号、课时标记和首屏标题提出"
                ),
            }
        )
        if match_confidence != "high":
            uncertainties.append(
                f"{identity.relative_path} 的课时对应置信度为"
                f"{'中' if match_confidence == 'medium' else '低'}，请重点核对"
            )
    return {
        "tree": tree,
        "mappings": mappings,
        "uncertainties": list(dict.fromkeys(uncertainties)),
        "source_material_record_ids": [
            item.material_record_id for item in identities
        ],
        "generation_source": "local_reference_ppt_names",
        "collection_members": [asdict(item) for item in identities],
        "summary": {
            "ppt_count": len(identities),
            "lesson_candidate_count": sum(
                item.kind == "lesson" for item in identities
            ),
            "special_count": sum(
                item.kind != "lesson" for item in identities
            ),
            "high_confidence_count": sum(
                item.kind == "lesson" and item.confidence == "high"
                for item in identities
            ),
            "needs_review_count": sum(
                item.kind == "lesson" and item.confidence != "high"
                for item in identities
            ),
            "chapter_count": len(
                {item.chapter_number for item in identities if item.chapter_number}
            ),
        },
    }


def _identity(entry: ReferencePptInput) -> ReferencePptIdentity:
    relative_path = _clean_relative_path(entry.relative_path, entry.file_name)
    sources = [
        unicodedata.normalize("NFKC", source)
        for source in [
            PurePosixPath(relative_path).stem,
            *reversed(PurePosixPath(relative_path).parts[:-1]),
        ]
    ]
    if entry.first_slide_title:
        sources.append(unicodedata.normalize("NFKC", entry.first_slide_title))
    numbered = next(
        (match for source in sources if (match := _NUMBER_PREFIX.search(source))),
        None,
    )
    chapter_match = next(
        (match for source in sources if (match := _CHAPTER.search(source))),
        None,
    )
    lesson_match = next(
        (match for source in sources if (match := _LESSON.search(source))),
        None,
    )
    chapter = int(numbered.group("chapter")) if numbered else None
    section = int(numbered.group("section")) if numbered else None
    subsection = (
        int(numbered.group("subsection"))
        if numbered and numbered.group("subsection")
        else None
    )
    if chapter is None and chapter_match:
        chapter = _chinese_number(chapter_match.group("number"))
    lesson = (
        _chinese_number(lesson_match.group("prefix") or lesson_match.group("suffix"))
        if lesson_match
        else subsection
    )
    combined = " ".join(sources)
    kind = _special_kind(combined)
    evidence: list[str] = []
    if numbered:
        evidence.append(f"编号 {numbered.group(0)}")
    if chapter_match:
        evidence.append(f"章节 {chapter_match.group(0)}")
    if lesson_match:
        evidence.append(f"课时 {lesson_match.group(0)}")
    if entry.first_slide_title:
        evidence.append("已核对首屏标题")
    issues: list[str] = []
    if chapter is None:
        issues.append("未识别章节号")
    if section is None and kind == "lesson":
        issues.append("未识别小节号")
    if kind == "lesson" and lesson is None:
        lesson = 1
        issues.append("未写课时序号，按本小节唯一课件提出第1课时")
    title_source = PurePosixPath(relative_path).stem
    title = _clean_title(title_source, numbered, lesson_match)
    if not title and entry.first_slide_title:
        title = _clean_title(entry.first_slide_title, None, None)
    if not title:
        title = "未命名课件"
        issues.append("未取得有效标题")
    confidence = _confidence(
        chapter=chapter,
        section=section,
        lesson=lesson,
        numbered=numbered is not None,
        lesson_marked=lesson_match is not None or subsection is not None,
        kind=kind,
        issues=issues,
    )
    return ReferencePptIdentity(
        material_record_id=entry.material_record_id,
        relative_path=relative_path,
        file_name=entry.file_name,
        chapter_number=chapter,
        section_number=section,
        subsection_number=subsection,
        lesson_number=lesson,
        title=title,
        kind=kind,
        confidence=confidence,
        evidence=tuple(evidence),
        issues=tuple(issues),
    )


def _candidate_tree(
    identities: Sequence[ReferencePptIdentity],
) -> list[dict[str, object]]:
    grouped: dict[int, dict[int, list[ReferencePptIdentity]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for item in identities:
        if (
            item.kind == "lesson"
            and item.chapter_number is not None
            and item.section_number is not None
        ):
            grouped[item.chapter_number][item.section_number].append(item)
    tree: list[dict[str, object]] = []
    for chapter_number in sorted(grouped):
        sections: list[dict[str, object]] = []
        for section_number in sorted(grouped[chapter_number]):
            candidates = sorted(
                grouped[chapter_number][section_number],
                key=lambda item: (
                    item.lesson_number or 999,
                    _natural_key(item.relative_path),
                ),
            )
            by_lesson: dict[int, list[ReferencePptIdentity]] = defaultdict(list)
            for item in candidates:
                by_lesson[item.lesson_number or 1].append(item)
            lessons: list[dict[str, object]] = []
            for lesson_number in sorted(by_lesson):
                choices = by_lesson[lesson_number]
                best = max(
                    choices,
                    key=lambda item: (
                        item.confidence == "high",
                        item.confidence == "medium",
                        len(item.title),
                    ),
                )
                lessons.append(
                    {
                        "key": _lesson_key(
                            chapter_number,
                            section_number,
                            lesson_number,
                        ),
                        "title": _lesson_title(best, lesson_number),
                        "duration_minutes": 45,
                    }
                )
            section_title = _section_title(
                chapter_number,
                section_number,
                candidates,
            )
            sections.append(
                {
                    "key": f"ppt-section-{chapter_number}-{section_number}",
                    "title": section_title,
                    "lessons": lessons,
                }
            )
        tree.append(
            {
                "key": f"ppt-chapter-{chapter_number}",
                "title": _chapter_title(
                    chapter_number,
                    [
                        item
                        for section_items in grouped[chapter_number].values()
                        for item in section_items
                    ],
                ),
                "sections": sections,
            }
        )
    return tree


def _proposal_lesson_refs(
    tree: Sequence[Mapping[str, object]],
) -> dict[tuple[int, int, int], str]:
    result: dict[tuple[int, int, int], str] = {}
    for chapter in tree:
        for section in chapter["sections"]:  # type: ignore[index]
            for lesson in section["lessons"]:  # type: ignore[index]
                key = str(lesson["key"])
                match = re.fullmatch(r"ppt-lesson-(\d+)-(\d+)-(\d+)", key)
                if match:
                    result[tuple(map(int, match.groups()))] = f"proposal:{key}"
    return result


def _identity_key(identity: ReferencePptIdentity) -> tuple[int, int, int]:
    return (
        int(identity.chapter_number or 0),
        int(identity.section_number or 0),
        int(identity.lesson_number or 1),
    )


def _match_existing_lesson(
    identity: ReferencePptIdentity,
    nodes: Sequence[Mapping[str, object]],
) -> tuple[str | None, str]:
    by_id = {str(item.get("id") or ""): item for item in nodes}
    candidates: list[tuple[float, str]] = []
    target_title = _semantic_title(identity.title)
    for node in nodes:
        if str(node.get("node_type") or "") != "lesson":
            continue
        node_id = str(node.get("id") or "")
        path = [node]
        parent_id = str(node.get("parent_id") or "")
        while parent_id and parent_id in by_id and len(path) < 4:
            parent = by_id[parent_id]
            path.append(parent)
            parent_id = str(parent.get("parent_id") or "")
        path_text = " ".join(str(item.get("title") or "") for item in path)
        title_score = max(
            (
                _title_overlap(
                    target_title,
                    _semantic_title(str(item.get("title") or "")),
                )
                for item in path
            ),
            default=0.0,
        )
        score = title_score * 0.55
        if identity.chapter_number and re.search(
            rf"(?:第\s*{identity.chapter_number}\s*章|(?<!\d){identity.chapter_number}[.．])",
            path_text,
        ):
            score += 0.2
        if identity.section_number and re.search(
            rf"(?<!\d){identity.chapter_number}[.．]{identity.section_number}(?!\d)",
            path_text,
        ):
            score += 0.2
        if identity.lesson_number and re.search(
            rf"第\s*{identity.lesson_number}\s*课时",
            path_text,
        ):
            score += 0.15
        candidates.append((score, node_id))
    candidates.sort(reverse=True)
    if not candidates or candidates[0][0] < 0.55:
        return None, "low"
    margin = candidates[0][0] - (candidates[1][0] if len(candidates) > 1 else 0)
    if candidates[0][0] >= 0.85 and margin >= 0.15:
        return candidates[0][1], "high"
    if candidates[0][0] >= 0.7 and margin >= 0.08:
        return candidates[0][1], "medium"
    return None, "low"


def _clean_relative_path(relative_path: str, file_name: str) -> str:
    value = str(relative_path or "").replace("\\", "/").strip()
    if value.startswith("/") or re.match(r"^[A-Za-z]:/", value):
        raise TeachingPrepValidationError(
            "reference PPT relative path is invalid"
        )
    value = value.strip("/")
    if not value:
        value = str(file_name or "").strip()
    parts = PurePosixPath(value).parts
    if (
        not parts
        or any(part in {"", ".", ".."} or not _SAFE_SEGMENT.fullmatch(part) for part in parts)
        or PurePosixPath(value).suffix.casefold() != ".pptx"
    ):
        raise TeachingPrepValidationError(
            "reference PPT relative path is invalid"
        )
    if len(value) > 600:
        raise TeachingPrepValidationError(
            "reference PPT relative path is too long"
        )
    return value


def _clean_title(
    value: str,
    numbered: re.Match[str] | None,
    lesson_match: re.Match[str] | None,
) -> str:
    text = unicodedata.normalize("NFKC", value)
    text = _TITLE_NOISE.sub("", text)
    if numbered:
        text = text.replace(numbered.group(0), "", 1)
    if lesson_match:
        text = text.replace(lesson_match.group(0), "", 1)
    text = text.strip(" -_—()（）[]【】·.．")
    return _SPACE.sub(" ", text).strip()


def _special_kind(value: str) -> str:
    match = _SPECIAL.search(value)
    if not match:
        return "lesson"
    token = match.group(0)
    if token in {"小结", "复习", "章末"}:
        return "review"
    if token == "策略":
        return "strategy"
    return "practice"


def _confidence(
    *,
    chapter: int | None,
    section: int | None,
    lesson: int | None,
    numbered: bool,
    lesson_marked: bool,
    kind: str,
    issues: Sequence[str],
) -> str:
    if kind != "lesson":
        return "high" if chapter is not None else "medium"
    if chapter is not None and section is not None and lesson is not None:
        if numbered and lesson_marked:
            return "high"
        return "medium" if issues else "high"
    return "low"


def _lesson_key(chapter: int, section: int, lesson: int) -> str:
    return f"ppt-lesson-{chapter}-{section}-{lesson}"


def _lesson_title(identity: ReferencePptIdentity, lesson_number: int) -> str:
    return f"第{lesson_number}课时 {identity.title}".strip()


def _section_title(
    chapter: int,
    section: int,
    candidates: Sequence[ReferencePptIdentity],
) -> str:
    titles = [item.title for item in candidates if item.title]
    if len({item.lesson_number for item in candidates}) == 1 and titles:
        return f"{chapter}.{section} {titles[0]}"
    common = _common_prefix(titles).strip(" -_—()（）")
    return f"{chapter}.{section}{f' {common}' if len(common) >= 2 else ''}"


def _chapter_title(
    chapter: int,
    candidates: Sequence[ReferencePptIdentity],
) -> str:
    labels: list[str] = []
    for item in candidates:
        for part in PurePosixPath(item.relative_path).parts[:-1]:
            match = _CHAPTER.search(part)
            if match and _chinese_number(match.group("number")) == chapter:
                labels.append(_SPACE.sub(" ", part).strip())
    return max(labels, key=len) if labels else f"第{chapter}章"


def _common_prefix(values: Sequence[str]) -> str:
    if not values:
        return ""
    prefix = values[0]
    for value in values[1:]:
        while prefix and not value.startswith(prefix):
            prefix = prefix[:-1]
    return prefix


def _kind_label(kind: str) -> str:
    return {
        "review": "复习课件",
        "strategy": "策略课件",
        "practice": "练习课件",
    }.get(kind, "特殊课件")


def _chinese_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        return digits.get(left, 1) * 10 + digits.get(right, 0)
    if value in digits:
        return digits[value]
    raise TeachingPrepValidationError("reference PPT contains an invalid number")


def _normal(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _semantic_title(value: str) -> str:
    text = _normal(value)
    text = re.sub(r"第[一二三四五六七八九十百\d]+课时", "", text)
    text = re.sub(r"^\d+", "", text)
    return text.replace("的", "")


def _title_overlap(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left in right or right in left:
        return min(len(left), len(right)) / max(len(left), len(right))
    left_pairs = {left[index : index + 2] for index in range(len(left) - 1)}
    right_pairs = {right[index : index + 2] for index in range(len(right) - 1)}
    if not left_pairs or not right_pairs:
        return 0.0
    return len(left_pairs & right_pairs) / len(left_pairs | right_pairs)


def _natural_key(value: str) -> tuple[object, ...]:
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", value)
    )
