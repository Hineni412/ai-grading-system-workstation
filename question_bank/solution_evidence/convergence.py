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
    canonical_terms: tuple[dict[str, Any], ...]
    retrieval_misses: tuple[dict[str, Any], ...]
    proposals: tuple[dict[str, Any], ...]
    secondary_matches: tuple[dict[str, Any], ...]
    unresolved_links: tuple[dict[str, Any], ...]
    missing_link_points: tuple[dict[str, Any], ...]
    taxonomy_revision: int

    @property
    def canonical_term_ids(self) -> tuple[str, ...]:
        return tuple(
            str(item.get("id") or "").strip()
            for item in self.canonical_terms
            if str(item.get("id") or "").strip()
        )

    def audit_dict(self) -> dict[str, Any]:
        return {
            "retrieval_misses": [dict(item) for item in self.retrieval_misses],
            "proposals": [dict(item) for item in self.proposals],
            "secondary_matches": [dict(item) for item in self.secondary_matches],
            "unresolved_links": [dict(item) for item in self.unresolved_links],
            "missing_link_points": [
                dict(item) for item in self.missing_link_points
            ],
            "taxonomy_revision": self.taxonomy_revision,
        }


def converge_evidence_terms(
    payload: Mapping[str, Any],
    *,
    taxonomy_contract: Mapping[str, Any],
    governance: Any,
    question_ref: str | int | None = None,
    question_id: int | None = None,
    model_name: str,
    operation_id: str,
    persist_proposals: bool = False,
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
    terms_by_id = {
        str(item.get("id") or "").strip(): item
        for item in terms
        if str(item.get("id") or "").strip()
    }
    clean_question_ref = _question_reference(question_ref, question_id)

    submitted_names: list[str] = []
    canonical_terms: list[dict[str, Any]] = []
    canonical_ids: set[str] = set()
    secondary_matches: list[dict[str, Any]] = []
    unresolved_links: list[dict[str, Any]] = []
    missing_link_points: list[dict[str, Any]] = []
    parts = normalized.get("parts")
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            part_id = str(part.get("part_id") or "").strip()
            points = part.get("evidence_points")
            if not isinstance(points, list):
                continue
            for point in points:
                if not isinstance(point, dict):
                    continue
                evidence_point_id = str(
                    point.get("evidence_point_id") or ""
                ).strip()
                links: list[dict[str, str]] = []
                linked_keys: set[tuple[str, str]] = set()
                raw_links = point.get("fine_term_links")
                if isinstance(raw_links, list):
                    for raw_link in raw_links:
                        if not isinstance(raw_link, Mapping):
                            unresolved_links.append(
                                _unresolved_link(
                                    part_id,
                                    evidence_point_id,
                                    "",
                                    "",
                                    "",
                                    "invalid_link",
                                )
                            )
                            continue
                        submitted_id = str(
                            raw_link.get("fine_term_id") or ""
                        ).strip()
                        name = str(raw_link.get("fine_term_name") or "").strip()
                        role = str(raw_link.get("role") or "").strip().casefold()
                        if role not in {"direct", "supporting_prerequisite"}:
                            unresolved_links.append(
                                _unresolved_link(
                                    part_id,
                                    evidence_point_id,
                                    submitted_id,
                                    name,
                                    role,
                                    "invalid_role",
                                )
                            )
                            continue
                        term_by_id = terms_by_id.get(submitted_id)
                        resolved_name = governance.resolve_term("knowledge", name)
                        term_by_name = (
                            dict(resolved_name)
                            if isinstance(resolved_name, Mapping)
                            else None
                        )
                        if term_by_id is not None and term_by_name is not None:
                            if str(term_by_id.get("id") or "").strip() != str(
                                term_by_name.get("id") or ""
                            ).strip():
                                submitted_names.append(name)
                                unresolved_links.append(
                                    _unresolved_link(
                                        part_id,
                                        evidence_point_id,
                                        submitted_id,
                                        name,
                                        role,
                                        "id_name_conflict",
                                    )
                                )
                                continue
                            term = term_by_id
                            submitted_names.append(name)
                        elif term_by_id is not None:
                            term = term_by_id
                            canonical_name = str(term.get("name") or "").strip()
                            if canonical_name:
                                submitted_names.append(canonical_name)
                            secondary_matches.append(
                                _corrected_link_match(
                                    part_id=part_id,
                                    evidence_point_id=evidence_point_id,
                                    term=term,
                                    role=role,
                                    submitted_id=submitted_id,
                                    submitted_name=name,
                                    correction="display_name_replaced",
                                )
                            )
                        elif term_by_name is not None:
                            term = term_by_name
                            submitted_names.append(name)
                            secondary_matches.append(
                                _corrected_link_match(
                                    part_id=part_id,
                                    evidence_point_id=evidence_point_id,
                                    term=term,
                                    role=role,
                                    submitted_id=submitted_id,
                                    submitted_name=name,
                                    correction="term_id_replaced",
                                )
                            )
                        else:
                            if name:
                                submitted_names.append(name)
                            unresolved_links.append(
                                _unresolved_link(
                                    part_id,
                                    evidence_point_id,
                                    submitted_id,
                                    name,
                                    role,
                                    "unknown_term",
                                )
                            )
                            continue
                        _append_link(
                            links,
                            linked_keys,
                            canonical_terms,
                            canonical_ids,
                            term,
                            role,
                        )
                point["fine_term_links"] = links
                if not links:
                    missing_link_points.append(
                        {
                            "part_id": part_id,
                            "evidence_point_id": evidence_point_id,
                            "reason_code": "missing_formal_link",
                        }
                    )

    constrained = governance.constrain(
        {"knowledge_points": _unique_text(submitted_names)},
        context={
            "persist_proposals": bool(persist_proposals),
            "question_ref": clean_question_ref,
            "model": str(model_name or "").strip(),
            "request_token": (
                f"solution-evidence:{operation_id}:question:{clean_question_ref}:"
                f"taxonomy:{_taxonomy_revision(taxonomy_contract)}"
            ),
            "expected_revision": _taxonomy_revision(taxonomy_contract),
            "allowed_term_ids": _allowed_term_ids(taxonomy_contract),
        },
    )
    proposals = tuple(
        dict(item)
        for item in constrained.get("proposals", [])
        if isinstance(item, Mapping)
    )
    return EvidenceTermConvergence(
        payload=normalized,
        canonical_terms=tuple(canonical_terms),
        retrieval_misses=tuple(
            dict(item)
            for item in constrained.get("retrieval_misses", [])
            if isinstance(item, Mapping)
        ),
        proposals=proposals,
        secondary_matches=tuple(secondary_matches),
        unresolved_links=tuple(
            _attach_proposal_ids(unresolved_links, proposals)
        ),
        missing_link_points=tuple(missing_link_points),
        taxonomy_revision=_result_revision(
            constrained,
            fallback=_taxonomy_revision(taxonomy_contract),
        ),
    )


def _append_link(
    links: list[dict[str, str]],
    linked_keys: set[tuple[str, str]],
    canonical_terms: list[dict[str, Any]],
    canonical_ids: set[str],
    term: Mapping[str, Any],
    role: str,
) -> bool:
    term_id = str(term.get("id") or "").strip()
    name = str(term.get("name") or "").strip()
    key = (term_id, role)
    if not term_id or not name or key in linked_keys:
        return False
    links.append(
        {
            "fine_term_id": term_id,
            "fine_term_name": name,
            "role": role,
        }
    )
    linked_keys.add(key)
    if term_id not in canonical_ids:
        canonical_ids.add(term_id)
        canonical_terms.append(_canonical_term(term))
    return True


def _canonical_term(term: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(term.get("id") or "").strip(),
        "name": str(term.get("name") or "").strip(),
        "aliases": _unique_text(term.get("aliases")),
    }


def _unresolved_link(
    part_id: str,
    evidence_point_id: str,
    submitted_id: str,
    submitted_name: str,
    role: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "part_id": str(part_id or "").strip(),
        "evidence_point_id": str(evidence_point_id or "").strip(),
        "role": str(role or "").strip().casefold(),
        "submitted_id": str(submitted_id or "").strip(),
        "submitted_name": str(submitted_name or "").strip(),
        "proposal_id": "",
        "reason_code": str(reason or "").strip(),
    }


def _corrected_link_match(
    *,
    part_id: str,
    evidence_point_id: str,
    term: Mapping[str, Any],
    role: str,
    submitted_id: str,
    submitted_name: str,
    correction: str,
) -> dict[str, Any]:
    return {
        "fine_term_id": str(term.get("id") or "").strip(),
        "fine_term_name": str(term.get("name") or "").strip(),
        "role": role,
        "source_field": "fine_term_links",
        "part_id": part_id,
        "evidence_point_id": evidence_point_id,
        "submitted_id": submitted_id,
        "matched_name": submitted_name,
        "correction": correction,
    }


def _attach_proposal_ids(
    unresolved_links: Sequence[Mapping[str, Any]],
    proposals: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    proposal_ids = {
        _normalize_name(
            item.get("proposed_name") or item.get("name")
        ): str(item.get("id") or "").strip()
        for item in proposals
        if _normalize_name(item.get("proposed_name") or item.get("name"))
    }
    result: list[dict[str, Any]] = []
    for raw in unresolved_links:
        item = dict(raw)
        if item.get("reason_code") == "unknown_term":
            item["proposal_id"] = proposal_ids.get(
                _normalize_name(item.get("submitted_name")),
                "",
            )
        result.append(item)
    return result


def _term_sort_key(item: Mapping[str, Any]) -> tuple[int, str]:
    lengths = [
        len(_normalize_name(value))
        for value in (item.get("name"), *(item.get("aliases") or []))
    ]
    return (-max(lengths, default=0), str(item.get("id") or ""))


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


def _result_revision(result: Mapping[str, Any], *, fallback: int) -> int:
    try:
        return max(0, int(result.get("taxonomy_revision", fallback) or 0))
    except (TypeError, ValueError):
        return max(0, int(fallback))


def _question_reference(
    question_ref: str | int | None,
    question_id: int | None,
) -> str:
    value: object = question_ref if question_ref is not None else question_id
    clean = str(value or "").strip()
    if not clean:
        raise ValueError("question_ref must not be empty")
    return clean


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
