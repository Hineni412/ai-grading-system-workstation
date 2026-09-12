"""Copy saved analysis products between exactly-duplicated questions.

When the importer detects that a newly imported question is identical
(full question text, formulas, options and figures) to an existing bank question, the two rows stay
separate but the new question can reuse the source's solution evidence and
training criteria.  Everything is re-anchored through the official write
paths so version ids and source-content hashes are recomputed for the new
question id; nothing is copied as raw rows.
"""

from __future__ import annotations

import json
import logging
import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

from question_bank.models.question import duplicate_question_key

from question_bank.current_knowledge import CurrentFineTermResolver
from question_bank.database.schema import connect
from question_bank.solution_evidence.contracts import QuestionSolutionEvidence
from question_bank.solution_evidence.repository import (
    SolutionEvidenceRepository,
    _model_evidence_payload,
)
from question_bank.training_criteria.adapters import QuestionAnalysisInputLoader
from question_bank.training_criteria.analysis import (
    solution_evidence_source_content_hash,
)
from question_bank.training_criteria.versioning import TrainingCriterionModule


LOGGER = logging.getLogger(__name__)

_EVIDENCE_MODEL_KEYS = (
    "schema_version",
    "question_id",
    "parts",
    "auxiliary_rules",
    "rationale",
    "confidence",
)
_USABLE_STATUSES = {"proposed", "approved"}


def exact_question_key(question: Mapping[str, Any], *, data_root: Path,
                       rich_content: Mapping[str, Any] | None = None,
                       image_cache: dict[str, str] | None = None) -> str:
    """Build the existing duplicate identity from full text and actual pixels."""
    return _question_content_key(question, data_root=data_root, rich_content=rich_content,
                                 image_cache=image_cache, exam_printing=False)


def exam_original_key(question: Mapping[str, Any], *, data_root: Path,
                      rich_content: Mapping[str, Any] | None = None,
                      image_cache: dict[str, str] | None = None) -> str:
    """Compare printed originals for recommendation exclusion only.

    Score prefixes and sparse pale printing do not create new exercises.
    The full stem, options, formulas and ordered figure contours still have to
    agree. This key must never merge bank records or copy their analysis.
    """
    return _question_content_key(question, data_root=data_root, rich_content=rich_content,
                                 image_cache=image_cache, exam_printing=True)


def _question_content_key(question: Mapping[str, Any], *, data_root: Path,
                          rich_content: Mapping[str, Any] | None,
                          image_cache: dict[str, str] | None,
                          exam_printing: bool) -> str:
    from PIL import Image
    from question_bank.services.asset_path_service import resolve_question_bank_asset_path
    from question_bank.services.rich_content_service import load_question_rich_content
    value = dict(question)
    if exam_printing:
        text = str(value.get("question_text") or "").strip()
        number = str(value.get("question_number") or "").strip()
        if number:
            text = re.sub(r"^\s*" + re.escape(number) + r"\s*[.．、)）](?!\d)\s*", "", text, count=1)
        value["question_text"] = re.sub(r"^(?:[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]\s*)+", "", text)
        value["question_number"] = ""
    cache = image_cache if image_cache is not None else {}
    try:
        if rich_content is None and value.get("id"):
            rich_content = load_question_rich_content(int(value["id"]), data_root / "question_bank" / "rich_content")
        rich = rich_content or {}
        marker = re.compile(r"\[\[IMAGE:(.*?)\]\]", re.I | re.S)
        stem_paths = marker.findall(str(value.get("question_text") or ""))
        answer_paths = set(marker.findall(str(value.get("answer_text") or "")))
        paths = value.get("image_paths", value.get("_image_paths")) or []
        if isinstance(paths, str):
            paths = json.loads(paths)
        paths = list(dict.fromkeys([*stem_paths, *(str(path) for path in paths if path not in answer_paths)]))
        formulas = []
        for block in rich.get("question_blocks", []):
            for path in (block.get("image_relationships") or {}).values():
                if str(path) not in paths:
                    paths.append(str(path))
            if block.get("xml"):
                import xml.etree.ElementTree as ET
                try:
                    root = ET.fromstring(str(block["xml"]))
                    formulas.extend(ET.tostring(item, encoding="unicode") for item in root.iter(
                        "{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath"))
                except ET.ParseError:
                    return ""
        def pixels(stored: str, polygon=None) -> str:
            token = str(stored) + json.dumps(polygon) + (":exam-printing" if exam_printing else "")
            if token not in cache:
                path = resolve_question_bank_asset_path(stored, data_root=data_root,
                    search_subdirs=("question_bank/extracted_images", "question_bank/document_pages"))
                with Image.open(path) as image:
                    picture = image.convert("RGBA")
                    if polygon:
                        xs, ys = zip(*polygon)
                        picture = picture.crop((round(min(xs) * image.width), round(min(ys) * image.height),
                                                round(max(xs) * image.width), round(max(ys) * image.height)))
                    if exam_printing:
                        picture = _exam_printed_figure(picture)
                    cache[token] = f"{picture.width}x{picture.height}:" + hashlib.sha256(picture.tobytes()).hexdigest()
            return cache[token]
        images = [pixels(path) for path in paths]
        regions = rich.get("identity_regions", rich.get("source_regions", []))
        for region in regions:
            page = rich.get("source_page_assets", [])[int(region["page_number"]) - 1]
            images.append(pixels(str(page), region["polygon"]))
        value.update(image_paths=paths, image_content_keys=images, source_regions=regions,
                     image_marker_keys={path: pixels(path) for path in stem_paths},
                     formula_content=[*formulas, *(str(item.get("restricted_latex") or item.get("semantic_mathml") or item.get("omml") or "")
                                      for item in rich.get("math_expressions", []))])
        # An unresolved figure must not silently turn into a text-only identity.
        if not images and re.search(r"如图|下图|右图|左图|图中", str(value.get("question_text") or "")):
            value["visual_evidence_missing"] = True
        if answer_paths and not paths and not regions:
            value["has_images"] = False
        key = duplicate_question_key(value)
        return "exam-original:" + key if key and exam_printing else key
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return ""


def _exam_printed_figure(picture):
    """Compare printed contours without requiring identical RGB encodings.

    Keep dark ink, gray shading and pale gray lines as separate bands. Visible
    colour remains pixel-exact. No resizing, blur or positional tolerance is
    used, so changed labels, lines and regions still change the identity.
    """
    import cv2
    import numpy as np
    from PIL import Image
    white = Image.new("RGB", picture.size, "white")
    white.paste(picture, mask=picture.getchannel("A"))
    pixels = np.array(white)
    darkest, lightest = pixels.min(axis=2), pixels.max(axis=2)
    # A one-channel rounding difference is not coloured printing. Treating it
    # as a watermark erased gray antialiasing in only one copy of a drawing.
    marks = (darkest >= 160) & (lightest - darkest >= 8)
    if marks.any() and (lightest < 96).any():
        _, _, components, _ = cv2.connectedComponentsWithStats(marks.astype(np.uint8), connectivity=8)
        filled = cv2.erode(marks.astype(np.uint8), np.ones((5, 5), dtype=np.uint8)).any()
        if (len(components) >= 4 and not filled
                and not any(width > 48 or height > 48 for _, _, width, height, _ in components[1:])):
            pixels[marks] = 255
    darkest, lightest = pixels.min(axis=2), pixels.max(axis=2)
    if ((lightest < 248) & (lightest - darkest >= 24)).any():
        return Image.fromarray(pixels)
    # Retain three intensity layers rather than dropping everything except
    # black strokes: a gray auxiliary line or shaded area must remain visible.
    layers = np.select([lightest < 96, lightest < 224, lightest < 248], [0, 96, 224], default=255)
    return Image.fromarray(layers.astype(np.uint8))


def exact_identity_map(conn: Any, *, data_root: Path) -> dict[int, str]:
    rows = conn.execute("""SELECT q.* FROM questions q LEFT JOIN papers p ON p.id=q.paper_id
                           WHERE q.is_deleted=0 AND COALESCE(p.import_status,'')<>'deleted' ORDER BY q.id""").fetchall()
    cache: dict[str, str] = {}
    return {int(row["id"]): exact_question_key(dict(row), data_root=data_root, image_cache=cache) for row in rows}


def link_exact_duplicate(conn: Any, *, question_id: int, source_id: int, signature: str, copy_tags: bool = True) -> None:
    """Preserve the new paper occurrence while reusing the source's labels."""
    conn.execute("""INSERT OR IGNORE INTO question_duplicate_links
                    (question_id,duplicate_of_question_id,match_kind,signature) VALUES (?,?,'exact',?)""",
                 (question_id, source_id, signature))
    if not copy_tags:
        return
    conn.execute("""INSERT INTO question_tags(question_id,tag_type,tag_value,confidence,source,model_name)
                    SELECT ?,tag_type,tag_value,confidence,source,model_name FROM question_tags source
                    WHERE question_id=? AND NOT EXISTS (SELECT 1 FROM question_tags target
                    WHERE target.question_id=? AND target.tag_type=source.tag_type AND target.tag_value=source.tag_value)""",
                 (question_id, source_id, question_id))
    conn.execute("""UPDATE questions SET difficulty=COALESCE((SELECT NULLIF(difficulty,'') FROM questions WHERE id=?),difficulty),
                    reason=COALESCE((SELECT NULLIF(reason,'') FROM questions WHERE id=?),reason) WHERE id=?""", (source_id, source_id, question_id))


def link_new_question_duplicate(conn: Any, *, question_id: int, data_root: Path,
                                identities: Mapping[int, str] | None = None) -> int | None:
    identities = identities if identities is not None else exact_identity_map(conn, data_root=data_root)
    key = identities.get(question_id)
    if not key:
        return None
    sources = [qid for qid, identity in identities.items() if qid < question_id and identity == key]
    if not sources:
        return None
    # Prefer a labelled source; use a stable id to break ties.
    def rank(qid):
        count = conn.execute("SELECT COUNT(DISTINCT tag_type) FROM question_tags WHERE question_id=?", (qid,)).fetchone()[0]
        return (-int(count), qid)
    source_id = min(sources, key=rank)
    link_exact_duplicate(conn, question_id=question_id, source_id=source_id, signature=key)
    return source_id


def copy_duplicate_analysis(
    db_path: str | Path,
    *,
    source_question_id: int,
    target_question_id: int,
    data_root: str | Path,
) -> dict[str, bool]:
    """Re-anchor the source question's usable analysis to the duplicate.

    Returns which products were actually copied.  Each product is independent
    and every failure is logged instead of raised, so a partial copy never
    blocks the surrounding import.
    """
    database = Path(db_path)
    with connect(database) as conn:
        rows = conn.execute("SELECT * FROM questions WHERE id IN (?,?)", (source_question_id, target_question_id)).fetchall()
    keys = [exact_question_key(dict(row), data_root=Path(data_root)) for row in rows]
    if len(keys) != 2 or not keys[0] or keys[0] != keys[1]:
        return {"evidence": False, "criteria": False}
    loader = QuestionAnalysisInputLoader(
        db_path=database,
        data_root=Path(data_root),
    )
    try:
        loaded = loader.load((int(source_question_id), int(target_question_id)))
    except (KeyError, OSError, TypeError, ValueError):
        LOGGER.exception(
            "duplicate analysis copy cannot load questions %s -> %s",
            source_question_id,
            target_question_id,
        )
        return {"evidence": False, "criteria": False}
    source_input, target_input = loaded
    result = {"evidence": False, "criteria": False}
    try:
        result["evidence"] = _copy_evidence(
            database,
            source_input=source_input,
            target_input=target_input,
        )
        if result["evidence"]:
            from question_bank.solution_evidence.part_assessments import load_profiles, save_profile
            profiles = load_profiles(database, [source_question_id], data_root=Path(data_root))
            profile = profiles.get(source_question_id)
            latest = SolutionEvidenceRepository(database).latest(target_question_id)
            if profile and profile["available"] and latest:
                save_profile(database, question_id=target_question_id,
                             evidence_version_id=latest["evidence_version_id"], parts=profile["parts"],
                             created_by="duplicate-import")
    except Exception:  # noqa: BLE001 - reuse must never break an import
        LOGGER.exception(
            "duplicate evidence copy failed: %s -> %s",
            source_question_id,
            target_question_id,
        )
    try:
        result["criteria"] = _copy_criteria(
            database,
            source_input=source_input,
            target_input=target_input,
        )
    except Exception:  # noqa: BLE001 - reuse must never break an import
        LOGGER.exception(
            "duplicate criteria copy failed: %s -> %s",
            source_question_id,
            target_question_id,
        )
    return result


def _copy_evidence(
    db_path: Path,
    *,
    source_input: Any,
    target_input: Any,
) -> bool:
    repository = SolutionEvidenceRepository(db_path)
    target_latest = repository.latest(int(target_input.question_id))
    if target_latest and target_latest.get("status") in _USABLE_STATUSES and target_latest.get("source_content_hash") == solution_evidence_source_content_hash(target_input):
        return True
    latest = repository.latest(int(source_input.question_id))
    if not latest or str(latest.get("status")) not in _USABLE_STATUSES:
        return False
    payload = latest.get("evidence")
    if not isinstance(payload, dict):
        return False
    # Only reuse evidence that still matches the source question's current
    # content; a stale analysis must not leak onto the new question.
    source_hash = solution_evidence_source_content_hash(source_input)
    if str(latest.get("source_content_hash") or "") != source_hash:
        return False
    clean = _model_evidence_payload(payload)
    model_payload = {
        key: clean[key] for key in _EVIDENCE_MODEL_KEYS if key in clean
    }
    model_payload["question_id"] = int(target_input.question_id)
    resolver = CurrentFineTermResolver.from_active_database(db_path)
    evidence = QuestionSolutionEvidence.from_model_dict(
        model_payload,
        question_id=int(target_input.question_id),
        source_content_hash=solution_evidence_source_content_hash(target_input),
        resolver=resolver,
    )
    repository.save(
        evidence,
        source_kind="import",
        source_reference=f"duplicate-import:{int(source_input.question_id)}",
        created_by="question-import",
    )
    return True


def _copy_criteria(
    db_path: Path,
    *,
    source_input: Any,
    target_input: Any,
) -> bool:
    with connect(db_path) as conn:
        current = conn.execute("""SELECT v.status,v.source_content_hash,v.quality_status FROM training_criterion_heads h
            JOIN training_criterion_versions v ON v.version_id=h.current_version_id WHERE h.question_id=?""",
            (int(target_input.question_id),)).fetchone()
    if current and current["status"] in _USABLE_STATUSES and current["quality_status"] == "passed" and current["source_content_hash"] == target_input.criterion_source_content_hash:
        return True
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT version.criteria_json AS criteria_json,
                   version.status AS status,
                   head.current_source_hash AS current_source_hash
            FROM training_criterion_heads head
            JOIN training_criterion_versions version
              ON version.version_id = head.current_version_id
            WHERE head.question_id = ?
            """,
            (int(source_input.question_id),),
        ).fetchone()
    if row is None or str(row["status"]) not in _USABLE_STATUSES:
        return False
    if str(row["current_source_hash"] or "") != (
        source_input.criterion_source_content_hash
    ):
        return False
    criteria = json.loads(str(row["criteria_json"]))
    if not isinstance(criteria, dict):
        return False
    # from_model_dict re-anchors source_content_hash to the target question;
    # the embedded question id is the only field that must be rewritten here.
    criteria["question_id"] = int(target_input.question_id)
    module = TrainingCriterionModule(db_path)
    module.propose(
        question=target_input,
        draft=criteria,
        source_kind="backfill",
        source_reference=f"duplicate-import:{int(source_input.question_id)}",
        actor_ref="question-import",
        reason="导入识别为完全相同题，复用已有判定点",
    )
    return True


__all__ = ["copy_duplicate_analysis"]
