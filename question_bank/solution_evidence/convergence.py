from __future__ import annotations

import copy
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class EvidenceTermConvergence:
    payload: dict[str, Any]
    canonical_term_ids: tuple[str, ...]
    retrieval_misses: tuple[dict[str, Any], ...]
    proposals: tuple[dict[str, Any], ...]
    secondary_matches: tuple[dict[str, Any], ...]

    def audit_dict(self) -> dict[str, Any]:
        return {
            "retrieval_misses": [dict(item) for item in self.retrieval_misses],
            "proposals": [dict(item) for item in self.proposals],
            "secondary_matches": [dict(item) for item in self.secondary_matches],
        }


def converge_evidence_terms(
    payload: Mapping[str, Any],
    *,
    taxonomy_contract: Mapping[str, Any],
    governance: Any,
    question_id: int,
    model_name: str,
    operation_id: str,
) -> EvidenceTermConvergence:
    """Converge model links and point text against the full local vocabulary."""

    normalized = copy.deepcopy(dict(payload))
    snapshot = governance.snapshot()
    dimensions = snapshot.get("terms_by_dimension")
    raw_terms = (
        dimensions.get("knowledge")
        if isinstance(dimensions, Mapping)
        else None
    )
    terms = [dict(item) for item in raw_terms or [] if isinstance(item, Mapping)]
    terms.sort(key=_term_sort_key)

    submitted_names: list[str] = []
    canonical_ids: list[str] = []
    secondary_matches: list[dict[str, Any]] = []
    parts = normalized.get("parts")
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            points = part.get("evidence_points")
            if not isinstance(points, list):
                continue
            for point in points:
                if not isinstance(point, dict):
                    continue
                links: list[dict[str, str]] = []
                linked_ids: set[str] = set()
                raw_links = point.get("fine_term_links")
                if isinstance(raw_links, list):
                    for raw_link in raw_links:
                        if not isinstance(raw_link, Mapping):
                            continue
                        name = str(raw_link.get("fine_term_name") or "").strip()
                        role = str(raw_link.get("role") or "").strip().casefold()
                        if name:
                            submitted_names.append(name)
                        if role not in {"direct", "supporting_prerequisite"}:
                            continue
                        term = governance.resolve_term("knowledge", name)
                        if not isinstance(term, Mapping):
                            continue
                        _append_link(
                            links,
                            linked_ids,
                            canonical_ids,
                            term,
                            role,
                        )

                text_fields = _evidence_text_fields(point)
                for term in terms:
                    term_id = str(term.get("id") or "").strip()
                    if not term_id or term_id in linked_ids:
                        continue
                    match = _first_exact_match(term, text_fields)
                    if match is None:
                        continue
                    source_field, matched_name = match
                    role = (
                        "direct"
                        if source_field == "target"
                        else "supporting_prerequisite"
                    )
                    if not _append_link(
                        links,
                        linked_ids,
                        canonical_ids,
                        term,
                        role,
                    ):
                        continue
                    canonical_name = str(term.get("name") or "").strip()
                    submitted_names.append(canonical_name)
                    secondary_matches.append(
                        {
                            "fine_term_id": term_id,
                            "fine_term_name": canonical_name,
                            "role": role,
                            "source_field": source_field,
                            "matched_name": matched_name,
                        }
                    )
                point["fine_term_links"] = links

    constrained = governance.constrain(
        {"knowledge_points": _unique_text(submitted_names)},
        context={
            "persist_proposals": True,
            "question_ref": str(int(question_id)),
            "model": str(model_name or "").strip(),
            "request_token": (
                f"solution-evidence:{operation_id}:question:{int(question_id)}:"
                f"taxonomy:{_taxonomy_revision(taxonomy_contract)}"
            ),
            "expected_revision": _taxonomy_revision(taxonomy_contract),
            "allowed_term_ids": _allowed_term_ids(taxonomy_contract),
        },
    )
    return EvidenceTermConvergence(
        payload=normalized,
        canonical_term_ids=tuple(canonical_ids),
        retrieval_misses=tuple(
            dict(item)
            for item in constrained.get("retrieval_misses", [])
            if isinstance(item, Mapping)
        ),
        proposals=tuple(
            dict(item)
            for item in constrained.get("proposals", [])
            if isinstance(item, Mapping)
        ),
        secondary_matches=tuple(secondary_matches),
    )


def _append_link(
    links: list[dict[str, str]],
    linked_ids: set[str],
    canonical_ids: list[str],
    term: Mapping[str, Any],
    role: str,
) -> bool:
    term_id = str(term.get("id") or "").strip()
    name = str(term.get("name") or "").strip()
    if not term_id or not name or term_id in linked_ids:
        return False
    links.append(
        {
            "fine_term_id": term_id,
            "fine_term_name": name,
            "role": role,
        }
    )
    linked_ids.add(term_id)
    if term_id not in canonical_ids:
        canonical_ids.append(term_id)
    return True


def _term_sort_key(item: Mapping[str, Any]) -> tuple[int, str]:
    lengths = [
        len(_normalize_name(value))
        for value in (item.get("name"), *(item.get("aliases") or []))
    ]
    return (-max(lengths, default=0), str(item.get("id") or ""))


def _evidence_text_fields(point: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    result = [
        ("target", str(point.get("target") or "")),
        ("observable_evidence", str(point.get("observable_evidence") or "")),
    ]
    for key in ("equivalent_rules", "counterexamples"):
        values = point.get(key)
        if isinstance(values, Sequence) and not isinstance(
            values, (str, bytes, bytearray)
        ):
            result.append((key, " ".join(str(item or "") for item in values)))
    return tuple(result)


def _first_exact_match(
    term: Mapping[str, Any],
    text_fields: Sequence[tuple[str, str]],
) -> tuple[str, str] | None:
    names = _unique_text([term.get("name"), *(term.get("aliases") or [])])
    names.sort(key=lambda value: (-len(_normalize_name(value)), value))
    for source_field, source_text in text_fields:
        normalized_text = _normalize_name(source_text)
        if not normalized_text:
            continue
        for name in names:
            normalized_name = _normalize_name(name)
            if normalized_name and normalized_name in normalized_text:
                return source_field, name
    return None


def _allowed_term_ids(contract: Mapping[str, Any]) -> dict[str, list[str]]:
    raw_allowed = contract.get("allowed_term_ids")
    if isinstance(raw_allowed, Mapping):
        return {
            str(dimension): _unique_text(values)
            for dimension, values in raw_allowed.items()
            if isinstance(values, Sequence)
            and not isinstance(values, (str, bytes, bytearray))
        }
    candidates = contract.get("candidates")
    knowledge = candidates.get("knowledge") if isinstance(candidates, Mapping) else []
    return {
        "knowledge": _unique_text(
            item.get("id")
            for item in knowledge or []
            if isinstance(item, Mapping)
        )
    }


def _taxonomy_revision(contract: Mapping[str, Any]) -> int:
    try:
        return max(0, int(contract.get("taxonomy_revision", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _normalize_name(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s\W_]+", "", normalized)


def _unique_text(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


__all__ = ["EvidenceTermConvergence", "converge_evidence_terms"]
