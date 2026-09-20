from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from question_bank.solution_evidence.repository import (
    FineTermCoreMappingRepository,
)
from question_bank.taxonomy.registry import CANONICAL_KNOWLEDGE


TermKind = Literal["core_knowledge", "fine_knowledge", "procedure"]
BaselineStatus = Literal["resolved", "ambiguous", "unmapped"]
_DEFAULT_CATALOG = (
    Path(__file__).resolve().parents[1]
    / "taxonomy"
    / "catalogs"
    / "tag_vocabulary_v2.json"
)
_PROCEDURE_MARKERS = (
    "求解",
    "解出",
    "计算",
    "化简",
    "证明",
    "判定",
    "判断",
    "作图",
    "画出",
    "构造",
    "探究",
    "操作",
    "应用",
    "运用",
    "利用",
    "建立",
    "表示",
    "确定",
    "比较",
    "阅读",
)


@dataclass(frozen=True, slots=True)
class FineTermBaselineEntry:
    fine_term_id: str
    fine_term_name: str
    term_kind: TermKind
    status: BaselineStatus
    core_node_ids: tuple[str, ...]
    match_basis: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fine_term_id": self.fine_term_id,
            "fine_term_name": self.fine_term_name,
            "term_kind": self.term_kind,
            "status": self.status,
            "core_node_ids": list(self.core_node_ids),
            "match_basis": list(self.match_basis),
        }


@dataclass(frozen=True, slots=True)
class FineTermMappingBaseline:
    catalog_id: str
    catalog_revision: int
    entries: tuple[FineTermBaselineEntry, ...]

    def coverage(self) -> dict[str, Any]:
        by_status = {
            status: sum(1 for item in self.entries if item.status == status)
            for status in ("resolved", "ambiguous", "unmapped")
        }
        by_kind = {
            kind: sum(1 for item in self.entries if item.term_kind == kind)
            for kind in ("core_knowledge", "fine_knowledge", "procedure")
        }
        return {
            "total": len(self.entries),
            "by_status": by_status,
            "by_term_kind": by_kind,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalog_id": self.catalog_id,
            "catalog_revision": self.catalog_revision,
            "coverage": self.coverage(),
            "entries": [item.to_dict() for item in self.entries],
        }

    def confirmed_mapping_plan(self) -> dict[str, tuple[str, ...]]:
        return {
            item.fine_term_id: item.core_node_ids
            for item in self.entries
            if item.status == "resolved"
        }


def build_fine_term_mapping_baseline(
    catalog_path: Path | None = None,
) -> FineTermMappingBaseline:
    """Classify every knowledge term using exact, auditable matches only."""

    path = Path(catalog_path) if catalog_path is not None else _DEFAULT_CATALOG
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("taxonomy catalog must be an object")
    raw_terms = payload.get("terms")
    if not isinstance(raw_terms, list):
        raise ValueError("taxonomy catalog terms are missing")
    core_by_id = {
        str(item.canonical_id).strip().casefold(): str(item.canonical_id).strip().casefold()
        for item in CANONICAL_KNOWLEDGE
    }
    exact_index: dict[str, set[str]] = {}
    for item in CANONICAL_KNOWLEDGE:
        stable_key = str(item.canonical_id).strip().casefold()
        for value in (
            item.canonical_id,
            item.canonical_name,
            *item.aliases,
        ):
            normalized = _normalize(value)
            if normalized:
                exact_index.setdefault(normalized, set()).add(stable_key)
    entries: list[FineTermBaselineEntry] = []
    for raw in raw_terms:
        if not isinstance(raw, Mapping) or raw.get("dimension") != "knowledge":
            continue
        term_id = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not term_id or not name:
            raise ValueError("knowledge term is missing id or name")
        term_kind = _term_kind(term_id, name)
        candidates: dict[str, set[str]] = {}

        stable_from_id = core_by_id.get(term_id.casefold())
        if stable_from_id:
            candidates.setdefault(stable_from_id, set()).add("id")

        if stable_from_id is None:
            for label, value in [
                ("name", name),
                *(
                    ("alias", alias)
                    for alias in raw.get("aliases", [])
                    if isinstance(alias, str)
                ),
            ]:
                for stable_key in exact_index.get(_normalize(value), set()):
                    candidates.setdefault(stable_key, set()).add(label)

        # Procedure/action terms may have a knowledge ancestor in source_paths,
        # but ancestry alone is not evidence that the action is a core concept.
        if stable_from_id is None and term_kind != "procedure":
            source_paths = raw.get("source_paths")
            if isinstance(source_paths, list):
                for source_path in source_paths:
                    if not isinstance(source_path, list):
                        continue
                    for segment in source_path:
                        for stable_key in exact_index.get(
                            _normalize(segment),
                            set(),
                        ):
                            candidates.setdefault(stable_key, set()).add(
                                "source_path_exact"
                            )

        ordered_candidates = tuple(sorted(candidates))
        if len(ordered_candidates) == 1:
            status: BaselineStatus = "resolved"
        elif ordered_candidates:
            status = "ambiguous"
        else:
            status = "unmapped"
        basis = tuple(
            sorted(
                f"{stable_key}:{reason}"
                for stable_key, reasons in candidates.items()
                for reason in reasons
            )
        )
        entries.append(
            FineTermBaselineEntry(
                fine_term_id=term_id,
                fine_term_name=name,
                term_kind=term_kind,
                status=status,
                core_node_ids=ordered_candidates,
                match_basis=basis,
            )
        )
    return FineTermMappingBaseline(
        catalog_id=str(payload.get("catalog_id") or ""),
        catalog_revision=int(payload.get("revision") or 0),
        entries=tuple(entries),
    )


def install_fine_term_mapping_baseline(
    repository: FineTermCoreMappingRepository,
    baseline: FineTermMappingBaseline,
    *,
    actor_ref: str,
) -> dict[str, Any]:
    """Explicitly install only the deterministic resolved subset."""

    installed = repository.install_synthetic_mappings(
        baseline.confirmed_mapping_plan(),
        source_reference=(
            f"taxonomy:{baseline.catalog_id}:revision:{baseline.catalog_revision}"
        ),
        actor_ref=actor_ref,
    )
    return {
        **baseline.coverage(),
        "installed_mapping_count": installed,
    }


def _term_kind(term_id: str, name: str) -> TermKind:
    if term_id.casefold().startswith(("kp_", "ki_", "sk_")):
        return "core_knowledge"
    compact_name = _normalize(name)
    if any(_normalize(marker) in compact_name for marker in _PROCEDURE_MARKERS):
        return "procedure"
    return "fine_knowledge"


def _normalize(value: object) -> str:
    return re.sub(r"[\s\W_]+", "", str(value or "")).casefold()


__all__ = [
    "FineTermBaselineEntry",
    "FineTermMappingBaseline",
    "build_fine_term_mapping_baseline",
    "install_fine_term_mapping_baseline",
]
