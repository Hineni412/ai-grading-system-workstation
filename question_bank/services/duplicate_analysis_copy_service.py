"""Identify identical questions and reuse their canonical analysis products.

New imports record paper occurrences against the existing canonical question.
Exam analysis re-anchors saved evidence to its source identity without a model
request. Legacy copy helpers remain for callers with existing distinct rows.
"""

from __future__ import annotations

import json
import logging
import hashlib
import re
import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping, Sequence

from question_bank.models.question import duplicate_question_key, normalize_identity_text, EXACT_QUESTION_KEY_PREFIX

from question_bank.current_knowledge import CurrentFineTermResolver
from question_bank.database.schema import connect
from question_bank.services.file_cache import (
    cached_parsed_file,
    cached_processed_image_digest,
    memoized_resolve,
)
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


_OPTION_CHARACTERS = frozenset("ABCDEFGH")
_OPTION_SEQUENCE = r"[A-Ha-h](?:\s*[,，、]?\s*[A-Ha-h])*"
# Whole-answer form: optional 答案/【答案】/答 prefix, then only option letters.
_BARE_OPTION_ANSWER = re.compile(
    rf"^\s*(?:(?:【\s*答\s*案\s*】)|答\s*案|答)?\s*[:：]?\s*({_OPTION_SEQUENCE})\s*[。．.；;]?\s*$"
)
# Explicit final-choice marker inside a longer analysis: last occurrence wins.
_FINAL_CHOICE_MARKER = re.compile(
    rf"(?:故\s*选|选|【\s*答\s*案\s*】|答\s*案\s*(?:是|为|选)?|答)\s*[:：]?\s*({_OPTION_SEQUENCE})"
)


def _option_letter_set(sequence: str) -> frozenset[str]:
    return frozenset(char.upper() for char in sequence if char.upper() in _OPTION_CHARACTERS)


def _bare_option_letters(value: object) -> frozenset[str] | None:
    match = _BARE_OPTION_ANSWER.match(normalize_identity_text(value))
    return _option_letter_set(match.group(1)) if match else None


def _final_choice_letters(value: object) -> frozenset[str] | None:
    letters = [
        _option_letter_set(match.group(1))
        for match in _FINAL_CHOICE_MARKER.finditer(normalize_identity_text(value))
    ]
    return letters[-1] if letters else None


def _option_letter_conflict(left: object, right: object) -> bool | None:
    """Compare a bare option answer against a marked final choice.

    Returns ``None`` when the special case does not apply so callers can keep
    the generic comparison; two bare answers deliberately fall through.
    """
    left_bare = _bare_option_letters(left)
    right_bare = _bare_option_letters(right)
    if left_bare is not None and right_bare is not None:
        return None
    for bare, other in ((left_bare, right), (right_bare, left)):
        if bare is None:
            continue
        marked = _final_choice_letters(other)
        if marked is not None:
            return bare != marked
    return None


def answers_conflict(left: object, right: object, *, data_root: Path) -> bool:
    """Different nonempty answers require review; equivalent image paths do not."""
    if not str(left or "").strip() or not str(right or "").strip():
        return False
    letter_verdict = _option_letter_conflict(left, right)
    if letter_verdict is not None:
        return letter_verdict
    if normalize_identity_text(left) == normalize_identity_text(right):
        return False
    first = exact_question_key({"question_text": str(left)}, data_root=data_root, rich_content={})
    second = exact_question_key({"question_text": str(right)}, data_root=data_root, rich_content={})
    return not first or not second or first != second


def _exam_original_text(question: Mapping[str, Any]) -> str:
    text = str(question.get("question_text") or "").strip()
    number = str(question.get("question_number") or "").strip()
    if number:
        text = re.sub(r"^\s*" + re.escape(number) + r"\s*[.．、)）](?!\d)\s*", "", text, count=1)
    return re.sub(r"^(?:[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]\s*)+", "", text)


def exam_original_text_key(question: Mapping[str, Any]) -> str:
    """Cheap necessary condition only; matching text still needs full figure checks."""
    text = re.sub(r"\[\[IMAGE:(.*?)\]\]", "[[IMAGE]]", _exam_original_text(question), flags=re.I | re.S)
    return re.sub(r"\s+", "", text)


def _question_content_key(question: Mapping[str, Any], *, data_root: Path,
                          rich_content: Mapping[str, Any] | None,
                          image_cache: dict[str, str] | None,
                          exam_printing: bool,
                          image_bytes: Sequence[bytes] = ()) -> str:
    from PIL import Image
    from question_bank.services.asset_path_service import resolve_question_bank_asset_path
    from question_bank.services.rich_content_service import load_question_rich_content
    value = dict(question)
    if exam_printing:
        value["question_text"] = _exam_original_text(value)
        value["question_number"] = ""
    cache = image_cache if image_cache is not None else {}
    try:
        if rich_content is None and value.get("id"):
            rich_content = load_question_rich_content(int(value["id"]), data_root / "question_bank" / "rich_content")
        rich = rich_content or {}
        marker = re.compile(r"\[\[IMAGE:([^\]|]*?)(?:\|[^\]]*)?\]\]", re.I | re.S)
        stem_paths = marker.findall(str(value.get("question_text") or ""))
        answer_paths = set(marker.findall(str(value.get("answer_text") or "")))
        paths = value.get("image_paths", value.get("_image_paths")) or []
        if isinstance(paths, str):
            paths = json.loads(paths)
        paths = list(dict.fromkeys([*stem_paths, *(str(path) for path in paths if path not in answer_paths)]))
        formulas = []
        math_nodes = []
        for block in rich.get("question_blocks", []):
            for path in (block.get("image_relationships") or {}).values():
                if str(path) not in paths:
                    paths.append(str(path))
            if block.get("xml"):
                import xml.etree.ElementTree as ET
                try:
                    root = ET.fromstring(str(block["xml"]))
                    math_nodes.extend(root.iter("{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath"))
                except ET.ParseError:
                    return ""
        def pixels(stored: str, polygon=None) -> str:
            token = str(stored) + json.dumps(polygon) + (":exam-printing" if exam_printing else "")
            if token not in cache:
                path = resolve_question_bank_asset_path(stored, data_root=data_root,
                    search_subdirs=("question_bank/extracted_images", "question_bank/document_pages"))

                def compute(resolved: Path) -> str:
                    with Image.open(resolved) as image:
                        picture = image.convert("RGBA")
                        if polygon:
                            xs, ys = zip(*polygon)
                            picture = picture.crop((round(min(xs) * image.width), round(min(ys) * image.height),
                                                    round(max(xs) * image.width), round(max(ys) * image.height)))
                        if exam_printing:
                            picture = _exam_printed_figure(picture)
                        else:
                            picture = _exact_resized_figure(picture)
                        return f"{picture.width}x{picture.height}:" + hashlib.sha256(picture.tobytes()).hexdigest()

                cache[token] = cached_processed_image_digest(path, variant=token, compute=compute)
            return cache[token]
        images = [pixels(path) for path in paths]
        regions = rich.get("identity_regions", rich.get("source_regions", []))
        for region in regions:
            page = rich.get("source_page_assets", [])[int(region["page_number"]) - 1]
            images.append(pixels(str(page), region["polygon"]))
        # Parsed analysis sources carry decoded image bodies instead of bank
        # asset paths; digest them through the identical RGBA pipeline so the
        # same figures still produce the same identity.
        if not images and image_bytes:
            from io import BytesIO
            for content in image_bytes:
                with Image.open(BytesIO(bytes(content))) as image:
                    picture = image.convert("RGBA")
                    if exam_printing:
                        picture = _exam_printed_figure(picture)
                    else:
                        picture = _exact_resized_figure(picture)
                    images.append(
                        f"{picture.width}x{picture.height}:"
                        + hashlib.sha256(picture.tobytes()).hexdigest()
                    )
            if images:
                value["has_images"] = True
        value["question_text"], formulas = _normalize_formula_content(
            str(value.get("question_text") or ""), math_nodes,
        )
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


def _formula_identity(element: Any) -> str:
    """Keep formula structure and semantic properties, discard only styling.

    The preview converter has a text fallback for unsupported formula nodes;
    that fallback is unsuitable for identity, so preserve unknown nodes here.
    """
    def node(item):
        name = item.tag.rsplit("}", 1)[-1]
        if name in {"rPr", "ctrlPr"}:
            return []
        if name == "t":
            return [normalize_identity_text(item.text)]
        children = []
        for child in item:
            for value in node(child):
                if isinstance(value, str) and children and isinstance(children[-1], str):
                    children[-1] += value
                else:
                    children.append(value)
        if name in {"r", "oMath"}:
            return children
        attributes = {}
        for key, value in item.attrib.items():
            key = key.rsplit("}", 1)[-1]
            if name in {"degHide", "subHide", "supHide", "grow", "hideTop", "hideBot", "hideLeft", "hideRight"}:
                value = "true" if str(value).lower() in {"1", "on", "true"} else "false"
            attributes[key] = normalize_identity_text(value)
        return [[name, attributes, children]]
    return json.dumps(node(element), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalize_formula_content(text: str, nodes: list) -> tuple[str, list[str]]:
    """Share a lossless subset of Word/LaTeX structure; retain unknown math.

    Positions are part of the stem identity. This does not simplify expressions,
    reorder operands, or discard unrecognised formula structures.
    """
    import xml.etree.ElementTree as ET
    from lxml import etree
    from question_bank.document_pipeline.math_omml import restricted_latex_to_omml, RestrictedMathError
    from question_bank.importers.docx_importer import _math_text

    def identity(node):
        # Default property containers carry no mathematical distinction.
        for parent in node.iter():
            for child in list(parent):
                if child.tag.rsplit("}", 1)[-1].endswith("Pr") and not list(child) and not child.attrib:
                    parent.remove(child)
        return _formula_identity(node)

    residual = []
    cursor = 0
    for node in nodes:
        xml = ET.tostring(node, encoding="unicode")
        visible = _math_text(etree.fromstring(xml.encode("utf-8")))
        token = "[[MATH:" + identity(node) + "]]"
        position = text.find(visible, cursor) if visible else -1
        if position < 0:
            residual.append(_formula_identity(node))
        else:
            text = text[:position] + token + text[position+len(visible):]
            cursor = position + len(token)

    def latex(match):
        source = match.group(1) or match.group(2)
        # The display converter tolerates matrix flattening; identity must not.
        if any(part in source for part in ("\\begin", "\\end", "&", "\\\\")):
            return match.group(0)
        try:
            return "[[MATH:" + identity(ET.fromstring(restricted_latex_to_omml(source))) + "]]"
        except (ValueError, ET.ParseError, RestrictedMathError):
            return match.group(0)
    text = re.sub(r"(?<!\\)\$([^$]+)\$|\\\((.*?)\\\)", latex, text, flags=re.S)
    return text, residual


def _exact_resized_figure(picture):
    """Remove provable uniform pixel replication, never blur away labels.

    Non-exact resampling/compression stays a candidate for human checking;
    similarity alone is not permission to merge mathematical diagrams.
    """
    import numpy as np
    from PIL import Image
    from math import gcd
    pixels = np.asarray(picture.convert("RGBA"))
    rows = np.flatnonzero(np.any(pixels[1:] != pixels[:-1], axis=(1, 2))) + 1
    cols = np.flatnonzero(np.any(pixels[:, 1:] != pixels[:, :-1], axis=(0, 2))) + 1
    scale = gcd(picture.width, picture.height)
    for boundary in (*rows, *cols):
        scale = gcd(scale, int(boundary))
        if scale == 1:
            return picture
    return Image.fromarray(pixels[::scale, ::scale].copy()) if scale > 1 else picture


_REVISION_CACHE: OrderedDict = OrderedDict()
_REVISION_LOCK = threading.Lock()
_REVISION_CACHE_LIMIT = 32768
_REVISION_CACHE_BUDGET = 64 * 1024 * 1024
_REVISION_CACHE_BYTES = 0


def _file_stamps(paths: Sequence[Path]) -> tuple:
    stamps = []
    for path in paths:
        try:
            stat = path.stat()
            stamps.append((str(path), stat.st_size, stat.st_mtime_ns))
        except OSError:
            stamps.append((str(path), None, None))
    return tuple(stamps)


def _content_revision(question: Mapping[str, Any], data_root: Path) -> str:
    """Cheap change detection; stat assets without decoding their pixels."""
    from question_bank.services.asset_path_service import resolve_question_bank_asset_path
    from question_bank.services.rich_content_service import rich_content_path
    global _REVISION_CACHE_BYTES
    data_root = memoized_resolve(data_root)
    fields = {key: question.get(key) for key in ("question_text", "answer_text", "question_number", "image_paths", "has_images", "options")}
    # Keep a copy of mutable input values. File stamps are still checked on
    # every call, including external edits with no corresponding SQL write.
    field_token = hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode()).digest()
    cache_key = (str(data_root), int(question["id"]))
    with _REVISION_LOCK:
        cached = _REVISION_CACHE.get(cache_key)
    if cached is not None and cached[0] == field_token:
        _, files, stamps, revision, _ = cached
        if _file_stamps(files) == stamps:
            with _REVISION_LOCK:
                if cache_key in _REVISION_CACHE:
                    _REVISION_CACHE.move_to_end(cache_key)
            return revision
    sidecar = rich_content_path(int(question["id"]), data_root / "question_bank" / "rich_content")
    paths = question.get("image_paths") or []
    if isinstance(paths, str):
        paths = json.loads(paths)
    paths = set(str(path) for path in paths)
    paths.update(re.findall(r"\[\[IMAGE:([^\]|]*?)(?:\|[^\]]*)?\]\]", str(question.get("question_text") or ""), re.I | re.S))
    if sidecar.is_file():
        rich = cached_parsed_file(
            sidecar, lambda p: json.loads(p.read_text(encoding="utf-8"))
        )
        paths.update(str(path) for path in rich.get("source_page_assets", []))
        for block in rich.get("question_blocks", []):
            paths.update(str(path) for path in (block.get("image_relationships") or {}).values())
    files = [sidecar]
    direct_paths = True
    for path in sorted(paths):
        try:
            resolved = resolve_question_bank_asset_path(path, data_root=data_root,
                         search_subdirs=("question_bank/extracted_images", "question_bank/document_pages"))
            files.append(resolved)
            stored = Path(path)
            # Legacy basename/remapped paths must continue through the normal
            # resolver: adding a sibling can make a previously unique match
            # ambiguous. Only canonical paths can skip that search.
            direct_paths = direct_paths and (
                bool(stored.parts) and stored.parts[0] == "question_bank"
                and not any(part.lower() in {"..", "data", "user_data"} for part in stored.parts)
                and data_root / stored == resolved
            )
        except (OSError, ValueError):
            fields.setdefault("missing", []).append(path)
            direct_paths = False
    stamps = _file_stamps(files)
    revision = hashlib.sha256(json.dumps([fields, stamps], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if direct_paths:
        # Conservative allowance for Python strings, path objects and tuples;
        # very long historical answers must not turn this into an unbounded
        # full-bank text cache.
        cost = 4 * len(field_token) + sum(8 * len(str(path)) + 512 for path in files) + 512
        with _REVISION_LOCK:
            previous = _REVISION_CACHE.pop(cache_key, None)
            _REVISION_CACHE_BYTES += cost - (previous[-1] if previous else 0)
            _REVISION_CACHE[cache_key] = (field_token, tuple(files), stamps, revision, cost)
            _REVISION_CACHE.move_to_end(cache_key)
            while len(_REVISION_CACHE) > _REVISION_CACHE_LIMIT or _REVISION_CACHE_BYTES > _REVISION_CACHE_BUDGET:
                _, evicted = _REVISION_CACHE.popitem(last=False)
                _REVISION_CACHE_BYTES -= evicted[-1]
    return revision


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
    # Elementwise comparisons give the identical three-channel extrema without
    # a slow reduction for every pixel in a full-resolution page image.
    darkest = np.minimum(np.minimum(pixels[:, :, 0], pixels[:, :, 1]), pixels[:, :, 2])
    lightest = np.maximum(np.maximum(pixels[:, :, 0], pixels[:, :, 1]), pixels[:, :, 2])
    # A one-channel rounding difference is not coloured printing. Treating it
    # as a watermark erased gray antialiasing in only one copy of a drawing.
    marks = (darkest >= 160) & (lightest - darkest >= 8)
    if marks.any() and (lightest < 96).any():
        _, _, components, _ = cv2.connectedComponentsWithStats(marks.astype(np.uint8), connectivity=8)
        filled = cv2.erode(marks.astype(np.uint8), np.ones((5, 5), dtype=np.uint8)).any()
        if (len(components) >= 4 and not filled
                and not any(width > 48 or height > 48 for _, _, width, height, _ in components[1:])):
            pixels[marks] = 255
            darkest[marks] = 255
            lightest[marks] = 255
    if ((lightest < 248) & (lightest - darkest >= 24)).any():
        return Image.fromarray(pixels)
    # Retain three intensity layers rather than dropping everything except
    # black strokes: a gray auxiliary line or shaded area must remain visible.
    layers = np.select([lightest < 96, lightest < 224, lightest < 248], [0, 96, 224], default=255)
    return Image.fromarray(layers.astype(np.uint8))


def exact_identity_map(conn: Any, *, data_root: Path,
                       question_ids: Sequence[int] | None = None,
                       persist: bool = True) -> dict[int, str]:
    # A filtered shortlist only needs its own identities and explicit exclusions.
    # Existing callers retain the full-bank comparison with identical semantics.
    ids = list(dict.fromkeys(question_ids)) if question_ids is not None else None
    if ids == []:
        return {}
    has_index = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_content_index'"
    ).fetchone() is not None
    index_columns = ", idx.content_key AS indexed_key, idx.source_revision AS indexed_revision" if has_index else ""
    index_join = " LEFT JOIN question_content_index idx ON idx.question_id=q.id" if has_index else ""
    rows = []
    for batch in ([None] if ids is None else [ids[i:i + 500] for i in range(0, len(ids), 500)]):
        condition = "" if batch is None else " AND q.id IN (" + ",".join("?" for _ in batch) + ")"
        rows.extend(conn.execute("SELECT q.*" + index_columns + """ FROM questions q
                           LEFT JOIN papers p ON p.id=q.paper_id""" + index_join + """
                           WHERE q.is_deleted=0 AND COALESCE(p.import_status,'')<>'deleted'"""
                           + condition + " ORDER BY q.id", batch or []).fetchall())
    cache: dict[str, str] = {}
    result: dict[int, str] = {}
    for row in rows:
        question = dict(row)
        indexed_key = str(question.pop("indexed_key", None) or "")
        indexed_revision = str(question.pop("indexed_revision", None) or "")
        if indexed_key.startswith(EXACT_QUESTION_KEY_PREFIX) and indexed_revision:
            try:
                # A matching stat-based revision means the indexed key is the
                # identity exact_question_key would recompute; skip decoding.
                if _content_revision(question, data_root) == indexed_revision:
                    result[int(question["id"])] = indexed_key
                    continue
            except (OSError, ValueError, TypeError):
                pass
        key = exact_question_key(question, data_root=data_root, image_cache=cache)
        result[int(question["id"])] = key
        try:
            revision = _content_revision(question, data_root)
        except (OSError, ValueError, TypeError):
            continue
        try:
            # Self-heal: older or missing index rows get the current key format
            # once, so later lookups skip decoding entirely.
            if persist:
                upsert_content_index(conn, question_id=int(question["id"]), key=key, source_revision=revision)
        except sqlite3.OperationalError:
            pass
    return result


def ensure_content_index(conn: Any, *, data_root: Path) -> None:
    """Persist each active question's exact identity key once.

    Rows missing from ``question_content_index`` get their key computed here;
    later lookups and imports only compute keys for new questions instead of
    decoding the whole bank's images on every batch.
    """
    missing = conn.execute(
        """SELECT q.*, idx.content_key AS indexed_key, idx.source_revision AS indexed_revision FROM questions q
           LEFT JOIN papers p ON p.id = q.paper_id
           LEFT JOIN question_content_index idx ON idx.question_id = q.id
           WHERE COALESCE(q.is_deleted, 0) = 0
             AND COALESCE(p.import_status, '') <> 'deleted'
           ORDER BY q.id"""
    ).fetchall()
    if not missing:
        return
    cache: dict[str, str] = {}
    for row in missing:
        question = dict(row)
        try:
            revision = _content_revision(question, data_root)
        except (OSError, ValueError, TypeError):
            upsert_content_index(conn, question_id=int(row["id"]), key="")
            continue
        if row["indexed_revision"] == revision and str(row["indexed_key"] or "").startswith(EXACT_QUESTION_KEY_PREFIX):
            continue
        key = exact_question_key(question, data_root=data_root, image_cache=cache)
        upsert_content_index(conn, question_id=int(row["id"]), key=key, source_revision=revision)


def upsert_content_index(conn: Any, *, question_id: int, key: str, source_revision: str = "") -> None:
    """Refresh one question's index row after its content is (re)written."""
    if not key:
        # A DELETE on an absent row still opens a write transaction and
        # touches the WAL file; only delete when there is a row to remove.
        if conn.execute(
            "SELECT 1 FROM question_content_index WHERE question_id = ?",
            (int(question_id),),
        ).fetchone() is not None:
            conn.execute(
                "DELETE FROM question_content_index WHERE question_id = ?",
                (int(question_id),),
            )
        return
    existing = conn.execute(
        "SELECT content_key, source_revision FROM question_content_index WHERE question_id = ?",
        (int(question_id),),
    ).fetchone()
    if (
        existing is not None
        and str(existing[0]) == str(key)
        and str(existing[1] or "") == str(source_revision)
    ):
        return
    conn.execute(
        """INSERT OR REPLACE INTO question_content_index
           (question_id, content_key, source_revision, updated_at)
           VALUES (?, ?, ?, datetime('now','localtime'))""",
        (int(question_id), str(key), source_revision),
    )


def canonical_question_ranks(conn: Any, question_ids: Sequence[int]) -> dict[int, tuple]:
    """Prefer reviewed evidence, then manual labels, then usable labels and id."""
    ranks = {int(qid): (0, 0, 0, int(qid)) for qid in question_ids}
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for offset in range(0, len(question_ids), 400):
        ids = list(question_ids[offset:offset+400])
        marks = ",".join("?" for _ in ids)
        approved = set()
        if "question_solution_evidence_versions" in tables:
            approved = {int(row[0]) for row in conn.execute(
                f"SELECT question_id FROM question_solution_evidence_versions v WHERE question_id IN ({marks}) "
                "AND status='approved' AND rowid=(SELECT v2.rowid FROM question_solution_evidence_versions v2 "
                "WHERE v2.question_id=v.question_id ORDER BY v2.created_at DESC, v2.rowid DESC LIMIT 1)", ids)}
        if "question_tags" in tables:
            for row in conn.execute(
                f"SELECT question_id, COUNT(DISTINCT tag_type), MAX(CASE WHEN source IN ('manual','teacher') THEN 1 ELSE 0 END) "
                f"FROM question_tags WHERE question_id IN ({marks}) GROUP BY question_id", ids):
                qid = int(row[0])
                ranks[qid] = (-int(qid in approved), -int(row[2] or 0), -int(row[1] or 0), qid)
        for qid in approved:
            ranks[qid] = (-1, *ranks[qid][1:])
    return ranks


def content_index_lookup(conn: Any, keys: list[str] | tuple[str, ...] | set[str],
                         *, data_root: Path | None = None) -> dict[str, int]:
    """Map exact identity keys to canonical bank question ids.

    Several historical rows may share one key; per the dedup contract the
    best-labelled candidate wins and the smallest id breaks ties, so a manual
    or fully analysed version is preferred over a bare one.
    """
    wanted = sorted({str(key) for key in keys if str(key or "").strip()})
    if not wanted:
        return {}
    placeholders = ",".join("?" for _ in wanted)
    rows = conn.execute(
        f"""SELECT idx.content_key AS content_key, idx.question_id AS question_id
            FROM question_content_index idx
            JOIN questions q ON q.id = idx.question_id
            LEFT JOIN papers p ON p.id = q.paper_id
            WHERE idx.content_key IN ({placeholders})
              AND COALESCE(q.is_deleted, 0) = 0
              AND COALESCE(p.import_status, '') <> 'deleted'""",
        wanted,
    ).fetchall()
    current = exact_identity_map(conn, data_root=data_root,
        question_ids=[int(row["question_id"]) for row in rows]) if data_root is not None else None
    ranks = canonical_question_ranks(conn, [int(row["question_id"]) for row in rows])
    best: dict[str, tuple] = {}
    for row in rows:
        key = str(row["content_key"])
        qid = int(row["question_id"])
        if current is not None and current.get(qid) != key:
            continue
        rank = ranks[qid]
        if key not in best or rank < best[key]:
            best[key] = rank
    return {key: rank[-1] for key, rank in best.items()}


def analysis_source_exact_key(question: Any, *, data_root: Path) -> str:
    """Exact-duplicate identity for a parsed exam-analysis source.

    ``QuestionAnalysisInput`` carries decoded image bodies rather than bank
    asset paths, so the same RGBA digest is applied to the in-memory images.
    Unresolvable figure references still produce no identity instead of a
    text-only key, matching the bank-side contract.
    """
    value = {
        "question_text": str(
            getattr(question.tagging_context, "question_text", "") or ""
        ),
        "question_number": "",
        "image_paths": [],
    }
    image_bytes = tuple(
        bytes(item.content)
        for item in question.images
        if item.role == "question"
    )
    return _question_content_key(
        value,
        data_root=Path(data_root),
        rich_content={
            "question_blocks": [
                dict(block) for block in question.rich_question_blocks
            ],
        },
        image_cache={},
        exam_printing=False,
        image_bytes=image_bytes,
    )


def uncertain_image_candidates(conn: Any, sources: Sequence[Any]) -> dict[str, int]:
    """Flag same-stem images for checking, never for automatic identity reuse.

    Exact pixels already matched upstream. Compression, resampling, watermarks
    and actual diagram changes cannot be separated by text alone.
    """
    def stem(question: Mapping[str, Any]) -> str:
        return normalize_identity_text(re.sub(r"\[\[IMAGE:.*?\]\]", "",
            _exam_original_text(question), flags=re.I | re.S))

    targets = {source.source_question_ref: stem({"question_text": source.question.tagging_context.question_text})
               for source in sources if any(image.role == "question" for image in source.question.images)}
    if not targets:
        return {}
    wanted = set(targets.values())
    matches: dict[str, int] = {}
    for row in conn.execute("""SELECT q.id, q.question_text, q.question_number
            FROM questions q LEFT JOIN papers p ON p.id=q.paper_id
            WHERE COALESCE(q.is_deleted,0)=0 AND COALESCE(p.import_status,'')<>'deleted'
              AND (q.has_images=1 OR (q.image_paths IS NOT NULL AND q.image_paths NOT IN ('','[]')))
            ORDER BY q.id"""):
        key = stem(dict(row))
        if key and key in wanted:
            matches.setdefault(key, int(row["id"]))
    return {reference: matches[key] for reference, key in targets.items() if key in matches}


def reusable_analysis(
    db_path: str | Path,
    *,
    bank_question_id: int,
    target_question: Any,
    data_root: str | Path,
    teacher_confirmed_same: bool = False,
) -> dict[str, Any] | None:
    """Return the canonical question's usable analysis re-anchored to a source.

    Used when an exam source is an exact duplicate of a bank question: the
    deferred analysis bundle can carry the stored products instead of issuing
    a new model request. Returns ``None`` when the canonical has no usable
    evidence for the identical content, or when re-anchoring fails.

    ``teacher_confirmed_same`` skips only the exact-key equality check — the
    teacher already identified the same question in review — while usable
    status, canonical content hash and answer-conflict checks still apply.
    """
    database = Path(db_path)
    repository = SolutionEvidenceRepository(database)
    latest = repository.latest(int(bank_question_id))
    if not latest or str(latest.get("status")) not in _USABLE_STATUSES:
        return None
    canonical = QuestionAnalysisInputLoader(db_path=database, data_root=Path(data_root)).load((int(bank_question_id),))
    if not canonical or str(latest.get("source_content_hash") or "") != solution_evidence_source_content_hash(canonical[0]):
        return None
    if answers_conflict(canonical[0].tagging_context.answer_text,
                        target_question.tagging_context.answer_text, data_root=Path(data_root)):
        return None
    # 相同题面的排版变体允许重新锚定，但旧证据仍须对应规范题当前内容。
    if not teacher_confirmed_same:
        canonical_key = analysis_source_exact_key(canonical[0], data_root=Path(data_root))
        if not canonical_key or canonical_key != analysis_source_exact_key(target_question, data_root=Path(data_root)):
            return None
    source_hash = solution_evidence_source_content_hash(target_question)
    payload = latest.get("evidence")
    if not isinstance(payload, dict):
        return None
    clean = _model_evidence_payload(payload)
    model_payload = {
        key: clean[key] for key in _EVIDENCE_MODEL_KEYS if key in clean
    }
    model_payload["question_id"] = int(target_question.question_id)
    resolver = CurrentFineTermResolver.from_active_database(database)
    try:
        evidence = QuestionSolutionEvidence.from_model_dict(
            model_payload,
            question_id=int(target_question.question_id),
            source_content_hash=source_hash,
            resolver=resolver,
        )
    except (KeyError, TypeError, ValueError) as exc:
        LOGGER.warning(
            "reusable analysis rejected for bank question %s: %s",
            bank_question_id,
            exc,
        )
        return None
    seen_links: dict[str, Any] = {}
    for part in evidence.parts:
        for point in part.evidence_points:
            for link in point.fine_term_links:
                seen_links.setdefault(link.fine_term_id, link)
    fine_term_links = tuple(
        {
            "fine_term_id": link.fine_term_id,
            "fine_term_name": link.fine_term_name,
        }
        for link in seen_links.values()
    )
    return {
        "evidence": evidence,
        "evidence_payload": model_payload,
        "model_name": str(latest.get("model_name") or "")
        or "question-bank-reuse",
        "fine_term_links": fine_term_links,
    }


def record_paper_occurrence(
    conn: Any,
    *,
    paper_id: int,
    question_id: int,
    question_number: str,
    signature: str,
) -> None:
    """Record that ``paper_id`` reuses canonical ``question_id``.

    Reused questions keep the importing paper's own question number here
    instead of gaining a second ``questions`` row.
    """
    conn.execute(
        """INSERT INTO paper_question_occurrences
           (paper_id, question_id, question_number, match_kind, signature)
           VALUES (?, ?, ?, 'exact', ?)""",
        (
            int(paper_id),
            int(question_id),
            str(question_number or "").strip(),
            str(signature or ""),
        ),
    )


def paper_occurrence_number_map(conn: Any, paper_id: int) -> dict[int, str]:
    """Map canonical question ids to this paper's own question numbers."""
    rows = conn.execute(
        """SELECT question_id, question_number FROM paper_question_occurrences
           WHERE paper_id = ?""",
        (int(paper_id),),
    ).fetchall()
    return {int(row["question_id"]): str(row["question_number"]) for row in rows}


def link_exact_duplicate(conn: Any, *, question_id: int, source_id: int, signature: str, copy_tags: bool = True) -> None:
    """Preserve the new paper occurrence while reusing the source's labels."""
    conn.execute("""INSERT OR IGNORE INTO question_duplicate_links
                    (question_id,duplicate_of_question_id,match_kind,signature) VALUES (?,?,'exact',?)""",
                 (question_id, source_id, signature))
    if not copy_tags:
        return
    occupied = tuple(row[0] for row in conn.execute(
        "SELECT DISTINCT tag_type FROM question_tags WHERE question_id=?", (question_id,)))
    omitted_types = " AND source.tag_type NOT IN (" + ",".join("?" for _ in occupied) + ")" if occupied else ""
    conn.execute(f"""INSERT INTO question_tags(question_id,tag_type,tag_value,confidence,source,model_name)
                    SELECT ?,tag_type,tag_value,confidence,source,model_name FROM question_tags source
                    WHERE question_id=? {omitted_types}""",
                 (question_id, source_id, *occupied))
    conn.execute("""UPDATE questions SET difficulty=COALESCE(NULLIF(difficulty,''),(SELECT NULLIF(difficulty,'') FROM questions WHERE id=?)),
                    reason=COALESCE(NULLIF(reason,''),(SELECT NULLIF(reason,'') FROM questions WHERE id=?)) WHERE id=?""", (source_id, source_id, question_id))


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
    ranks = canonical_question_ranks(conn, sources)
    source_id = min(sources, key=lambda qid: ranks[qid])
    answer_rows = conn.execute("SELECT answer_text,difficulty FROM questions WHERE id IN (?,?) ORDER BY id", (question_id, source_id)).fetchall()
    conflict = len(answer_rows) == 2 and answers_conflict(answer_rows[0][0], answer_rows[1][0], data_root=data_root)
    if len(answer_rows) == 2 and all(str(row[1] or "").strip() for row in answer_rows):
        conflict = conflict or str(answer_rows[0][1]) != str(answer_rows[1][1])
    link_exact_duplicate(conn, question_id=question_id, source_id=source_id, signature=key, copy_tags=not conflict)
    if conflict:
        conn.execute("UPDATE questions SET needs_review=1 WHERE id=?", (question_id,))
        return None
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
    if answers_conflict(rows[0]["answer_text"], rows[1]["answer_text"], data_root=Path(data_root)):
        return {"evidence": False, "criteria": False}
    if all(str(row["difficulty"] or "").strip() for row in rows) and str(rows[0]["difficulty"]) != str(rows[1]["difficulty"]):
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
            # 逐小问难度特征不随标签复制（标签复制不写该表），这里按行照搬；
            # 完全重复题的内容指纹相同，有效行可直接换到目标题。
            from question_bank.services import standard_difficulty
            with connect(database) as conn:
                conn.execute("BEGIN IMMEDIATE")
                feature_rows = (
                    conn.execute(
                        """SELECT part_id, features_json, model_name
                           FROM question_part_difficulty_features
                           WHERE question_id=? AND is_active=1""",
                        (int(source_question_id),),
                    ).fetchall()
                    if standard_difficulty.table_exists(conn)
                    else []
                )
                target_row = conn.execute(
                    "SELECT * FROM questions WHERE id=? AND is_deleted=0",
                    (int(target_question_id),),
                ).fetchone()
                if feature_rows and target_row is not None:
                    standard_difficulty.save_assessment(
                        conn,
                        question_id=int(target_question_id),
                        part_features=[
                            {**json.loads(str(row["features_json"])),
                             "part_id": str(row["part_id"])}
                            for row in feature_rows
                        ],
                        content_fingerprint=standard_difficulty.question_content_fingerprint(
                            dict(target_row)
                        ),
                        model_name=str(feature_rows[0]["model_name"] or "") or None,
                    )
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
