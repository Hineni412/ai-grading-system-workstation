"""批改运行的稳定指纹与同学生候选答卷判定（纯函数，不依赖数据库/UI/模型）。

三元幂等键 = (学生, 试卷内容指纹, 批改配置指纹)。据此在开始批改前决定每份候选答卷：
- ``grade``：需要批改。
- ``skipped_existing``：与已完成成绩三元完全一致，跳过。
- ``skipped_duplicate``：本批次内同学生同一份试卷的重复项。
- ``conflict``：同一学生出现多份不同内容的答卷，全部标冲突交人工。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class CandidatePaper:
    source_label: str
    student_id: int
    paper_fingerprint: str
    paper_id: int | None = None


@dataclass(frozen=True, slots=True)
class CompletedIdentity:
    student_id: int
    paper_fingerprint: str
    config_fingerprint: str
    complete: bool


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    source_label: str
    student_id: int
    paper_fingerprint: str
    action: str
    reason: str = ""
    paper_id: int | None = None


def paper_fingerprint(front: Any, back: Any) -> str:
    """对正反面原图内容做稳定 SHA-256（含长度分隔，避免拼接歧义）。"""
    digest = hashlib.sha256()
    for path in (front, back):
        data = Path(path).read_bytes()
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def grading_config_fingerprint(
    *,
    rubric: Mapping[str, Any],
    answer_key: Mapping[str, Any],
    answer_regions: Sequence[Mapping[str, Any]],
    grading_mode: object,
    grading_model: object,
) -> str:
    """对影响批改结果的配置做稳定哈希；并发/RPM 等运行参数不参与。"""
    from question_id_contract import canonicalize_question_document

    payload = {
        "rubric": canonicalize_question_document(rubric),
        "answer_key": canonicalize_question_document(answer_key),
        "answer_regions": sorted(
            (dict(item) for item in answer_regions),
            key=lambda item: (
                str(item.get("page") or ""),
                int(item.get("region_order") or 0),
                str(item.get("mapped_question_id") or ""),
            ),
        ),
        "grading_mode": str(grading_mode),
        "grading_model": str(grading_model or ""),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def classify_student_candidates(
    candidates: Sequence[CandidatePaper],
    completed: Sequence[CompletedIdentity],
    config_fingerprint: str,
) -> list[CandidateDecision]:
    completed_keys = {
        (item.student_id, item.paper_fingerprint)
        for item in completed
        if item.complete and item.config_fingerprint == config_fingerprint
    }

    fingerprints_by_student: dict[int, set[str]] = {}
    for candidate in candidates:
        fingerprints_by_student.setdefault(candidate.student_id, set()).add(
            candidate.paper_fingerprint
        )

    primary_emitted: set[tuple[int, str]] = set()
    decisions: list[CandidateDecision] = []
    for candidate in candidates:
        key = (candidate.student_id, candidate.paper_fingerprint)
        distinct = fingerprints_by_student.get(candidate.student_id, set())

        if len(distinct) > 1:
            action, reason = "conflict", "同一学生出现多份不同内容的答卷"
        elif key in completed_keys:
            if key not in primary_emitted:
                primary_emitted.add(key)
                action, reason = "skipped_existing", "已存在相同学生/试卷/配置的完整成绩"
            else:
                action, reason = "skipped_duplicate", "本批次内重复的同一份答卷"
        else:
            if key not in primary_emitted:
                primary_emitted.add(key)
                action, reason = "grade", ""
            else:
                action, reason = "skipped_duplicate", "本批次内重复的同一份答卷"

        decisions.append(
            CandidateDecision(
                source_label=candidate.source_label,
                student_id=candidate.student_id,
                paper_fingerprint=candidate.paper_fingerprint,
                action=action,
                reason=reason,
                paper_id=candidate.paper_id,
            )
        )
    return decisions
