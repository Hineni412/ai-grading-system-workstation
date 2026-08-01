from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping

from question_bank.services.rich_content_service import load_question_rich_content

from .contracts import (
    ExportReceipt,
    ExportState,
    FormulaFallback,
    MathExpression,
    canonical_hash,
    math_expression_from_payload,
    sha256_bytes,
)
from .word_renderer import WordStyleProfile, validate_docx


@dataclass(frozen=True, slots=True)
class PublishedMathMetadata:
    content_revision: str
    expressions: tuple[MathExpression, ...]
    rich_content: Mapping[str, object] | None


def data_root_for_database(db_path: str | Path) -> Path:
    database = Path(db_path).resolve()
    if database.parent.name.casefold() in {"databases", "question_bank"}:
        return database.parent.parent
    return database.parent


def published_math_metadata(
    question_id: int,
    *,
    data_root: str | Path,
    question_text: str,
    answer_text: str = "",
) -> PublishedMathMetadata:
    root = Path(data_root).resolve()
    rich_root = root / "question_bank" / "rich_content"
    payload = load_question_rich_content(int(question_id), root=rich_root)
    expressions: list[MathExpression] = []
    if isinstance(payload, Mapping):
        raw_expressions = payload.get("math_expressions")
        if not isinstance(raw_expressions, (list, tuple)):
            raw_expressions = ()
        for raw in raw_expressions:
            if not isinstance(raw, Mapping):
                continue
            try:
                expressions.append(math_expression_from_payload(raw))
            except (KeyError, TypeError, ValueError):
                continue
    revision = str(payload.get("content_revision") or "") if payload else ""
    if len(revision) != 64:
        revision = canonical_hash(
            {
                "question_id": int(question_id),
                "question_text": str(question_text or ""),
                "answer_text": str(answer_text or ""),
            }
        )
    return PublishedMathMetadata(
        content_revision=revision,
        expressions=tuple(expressions),
        rich_content=payload,
    )


def export_receipt_path(artifact_path: str | Path) -> Path:
    artifact = Path(artifact_path)
    return artifact.with_suffix(f"{artifact.suffix}.receipt.json")


def save_validated_legacy_export(
    document,
    output_path: str | Path,
    *,
    operation_namespace: str,
    question_ids: Iterable[str],
    content_revisions: Iterable[str],
    style: WordStyleProfile,
    fallbacks: Iterable[FormulaFallback],
) -> ExportReceipt:
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{output.stem}-",
        suffix=".docx",
        dir=output.parent,
        delete=False,
    ) as handle:
        staging = Path(handle.name)
    try:
        document.save(staging)
        validation_errors = validate_docx(staging)
        if validation_errors:
            raise ValueError("; ".join(validation_errors))
        artifact_content = staging.read_bytes()
        artifact_hash = sha256_bytes(artifact_content)
        ids = tuple(str(item) for item in question_ids)
        revisions = tuple(str(item) for item in content_revisions)
        snapshot_revision = canonical_hash(
            {
                "operation_namespace": operation_namespace,
                "question_ids": ids,
                "content_revisions": revisions,
                "style_profile_sha256": style.sha256,
            }
        )
        fallback_items = tuple(fallbacks)
        receipt = ExportReceipt(
            operation_id=f"{operation_namespace}-{artifact_hash[:24]}",
            snapshot_revision=snapshot_revision,
            state=(
                ExportState.COMPLETE_WITH_FALLBACKS
                if fallback_items
                else ExportState.COMPLETE
            ),
            artifact_path=str(output),
            artifact_sha256=artifact_hash,
            question_ids=ids,
            content_revisions=revisions,
            style_profile_sha256=style.sha256,
            converter_version="legacy-export+restricted-math-omml-v1",
            fallbacks=fallback_items,
            validation_errors=(),
        )
        os.replace(staging, output)
        _atomic_write_json(export_receipt_path(output), asdict(receipt))
        return receipt
    finally:
        if staging.exists():
            staging.unlink()


def load_legacy_export_receipt(artifact_path: str | Path) -> ExportReceipt:
    artifact = Path(artifact_path).resolve()
    payload = json.loads(export_receipt_path(artifact).read_text(encoding="utf-8"))
    receipt = ExportReceipt(
        operation_id=str(payload["operation_id"]),
        snapshot_revision=str(payload["snapshot_revision"]),
        state=ExportState(str(payload["state"])),
        artifact_path=str(payload["artifact_path"]),
        artifact_sha256=str(payload["artifact_sha256"]),
        question_ids=tuple(str(item) for item in payload.get("question_ids") or ()),
        content_revisions=tuple(
            str(item) for item in payload.get("content_revisions") or ()
        ),
        style_profile_sha256=str(payload["style_profile_sha256"]),
        converter_version=str(payload["converter_version"]),
        fallbacks=tuple(
            FormulaFallback(
                question_id=str(item["question_id"]),
                expression_id=str(item["expression_id"]),
                reason=str(item["reason"]),
                asset_path=item.get("asset_path"),
            )
            for item in payload.get("fallbacks") or ()
        ),
        validation_errors=tuple(payload.get("validation_errors") or ()),
    )
    if not artifact.is_file() or sha256_bytes(artifact.read_bytes()) != receipt.artifact_sha256:
        raise ValueError("export artifact no longer matches its receipt")
    return receipt


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}-",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        staging = Path(handle.name)
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(staging, path)
    finally:
        if staging.exists():
            staging.unlink()


__all__ = [
    "PublishedMathMetadata",
    "data_root_for_database",
    "export_receipt_path",
    "load_legacy_export_receipt",
    "published_math_metadata",
    "save_validated_legacy_export",
]
