from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Mapping


_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MEDIA_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
}


class DocumentState(str, Enum):
    PREPARED = "prepared"
    REVIEW_REQUIRED = "review_required"
    READY = "ready"
    FAILED = "failed"


class ReviewState(str, Enum):
    CANDIDATE = "candidate"
    TEACHER_VERIFIED = "teacher_verified"
    REJECTED = "rejected"
    MISSING = "missing"


class TextLayerState(str, Enum):
    EMBEDDED = "embedded"
    LOCAL_OCR = "local_ocr"
    EMPTY = "empty"


class RecognitionSource(str, Enum):
    EMBEDDED_TEXT = "embedded_text"
    LOCAL_OCR = "local_ocr"
    MANUAL = "manual"


class LayoutBlockKind(str, Enum):
    TEXT = "text"
    QUESTION_MARKER = "question_marker"
    ANSWER_MARKER = "answer_marker"
    FORMULA = "formula"
    FIGURE = "figure"
    TABLE = "table"
    HEADER = "header"
    FOOTER = "footer"


class PublishState(str, Enum):
    PUBLISHED = "published"
    RECOVERY_REQUIRED = "recovery_required"


class ExportState(str, Enum):
    COMPLETE = "complete"
    COMPLETE_WITH_FALLBACKS = "complete_with_fallbacks"


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(bytes(content)).hexdigest()


def canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(encoded)


def _safe_id(value: str, field_name: str) -> str:
    clean = str(value or "").strip()
    if not _ID_PATTERN.fullmatch(clean):
        raise ValueError(f"{field_name} is invalid")
    return clean


def _hash(value: str, field_name: str) -> str:
    clean = str(value or "").strip().casefold()
    if not _HASH_PATTERN.fullmatch(clean):
        raise ValueError(f"{field_name} must be a SHA-256 digest")
    return clean


def _confidence(value: float, field_name: str = "confidence") -> float:
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{field_name} must be between 0 and 1")
    return result


def _relative_asset(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    clean = str(value).replace("\\", "/").strip()
    path = PurePosixPath(clean)
    if (
        not clean
        or path.is_absolute()
        or ".." in path.parts
        or ":" in path.parts[0]
    ):
        raise ValueError(f"{field_name} must be a controlled relative path")
    return path.as_posix()


@dataclass(frozen=True, slots=True)
class SourceRegion:
    page_number: int
    polygon: tuple[tuple[float, float], ...]
    source: RecognitionSource
    confidence: float = 1.0
    coordinate_space: str = "normalized"

    def __post_init__(self) -> None:
        if int(self.page_number) < 1:
            raise ValueError("page_number must be positive")
        if self.coordinate_space != "normalized":
            raise ValueError("only normalized source regions are supported")
        points = tuple((float(x), float(y)) for x, y in self.polygon)
        if len(points) < 4:
            raise ValueError("source region polygon must have at least four points")
        if any(not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0) for x, y in points):
            raise ValueError("source region polygon must stay inside the page")
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        if max(xs) <= min(xs) or max(ys) <= min(ys):
            raise ValueError("source region polygon must have positive area")
        object.__setattr__(self, "page_number", int(self.page_number))
        object.__setattr__(self, "polygon", points)
        object.__setattr__(self, "confidence", _confidence(self.confidence))

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        xs = [point[0] for point in self.polygon]
        ys = [point[1] for point in self.polygon]
        return min(xs), min(ys), max(xs), max(ys)


@dataclass(frozen=True, slots=True)
class ImageTransform:
    rotation_degrees: int = 0
    perspective_corners: tuple[tuple[float, float], ...] = ()
    method: str = "identity"
    requires_review: bool = False

    def __post_init__(self) -> None:
        rotation = int(self.rotation_degrees)
        if rotation not in {0, 90, 180, 270}:
            raise ValueError("rotation_degrees must be 0, 90, 180 or 270")
        corners = tuple((float(x), float(y)) for x, y in self.perspective_corners)
        if corners and len(corners) != 4:
            raise ValueError("perspective_corners must contain four points")
        if any(not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0) for x, y in corners):
            raise ValueError("perspective corners must be normalized")
        method = str(self.method or "").strip()
        if not method or len(method) > 80:
            raise ValueError("image transform method is invalid")
        object.__setattr__(self, "rotation_degrees", rotation)
        object.__setattr__(self, "perspective_corners", corners)
        object.__setattr__(self, "method", method)


@dataclass(frozen=True, slots=True)
class LayoutBlock:
    block_id: str
    page_number: int
    kind: LayoutBlockKind
    text: str
    region: SourceRegion
    reading_order: int
    source: RecognitionSource
    confidence: float
    engine_version: str
    requires_review: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "block_id", _safe_id(self.block_id, "block_id"))
        if int(self.page_number) != self.region.page_number:
            raise ValueError("layout block page does not match its source region")
        if int(self.reading_order) < 0:
            raise ValueError("reading_order must be non-negative")
        text = str(self.text or "").strip()
        if len(text) > 200_000:
            raise ValueError("layout block text is too long")
        version = str(self.engine_version or "").strip()
        if not version or len(version) > 120:
            raise ValueError("engine_version is invalid")
        object.__setattr__(self, "page_number", int(self.page_number))
        object.__setattr__(self, "reading_order", int(self.reading_order))
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        object.__setattr__(self, "engine_version", version)


@dataclass(frozen=True, slots=True)
class PageSnapshot:
    page_number: int
    width: float
    height: float
    rendered_asset: str
    rendered_sha256: str
    text_layer_state: TextLayerState
    blocks: tuple[LayoutBlock, ...]
    transform: ImageTransform = field(default_factory=ImageTransform)
    rendered_width: int | None = None
    rendered_height: int | None = None
    source_to_rendered: tuple[float, float, float, float, float, float] = (
        1.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
    )

    def __post_init__(self) -> None:
        if int(self.page_number) < 1:
            raise ValueError("page_number must be positive")
        if float(self.width) <= 0 or float(self.height) <= 0:
            raise ValueError("page dimensions must be positive")
        rendered_width = int(self.rendered_width or round(float(self.width)))
        rendered_height = int(self.rendered_height or round(float(self.height)))
        if rendered_width < 1 or rendered_height < 1:
            raise ValueError("rendered page dimensions must be positive")
        coordinate_transform = tuple(float(item) for item in self.source_to_rendered)
        if len(coordinate_transform) != 6:
            raise ValueError("source_to_rendered must be a six-value affine transform")
        asset = _relative_asset(self.rendered_asset, "rendered_asset")
        assert asset is not None
        orders = [block.reading_order for block in self.blocks]
        if orders != sorted(orders) or len(orders) != len(set(orders)):
            raise ValueError("page layout blocks must have unique ordered positions")
        if any(block.page_number != int(self.page_number) for block in self.blocks):
            raise ValueError("page contains a layout block from another page")
        object.__setattr__(self, "page_number", int(self.page_number))
        object.__setattr__(self, "width", float(self.width))
        object.__setattr__(self, "height", float(self.height))
        object.__setattr__(self, "rendered_width", rendered_width)
        object.__setattr__(self, "rendered_height", rendered_height)
        object.__setattr__(self, "source_to_rendered", coordinate_transform)
        object.__setattr__(self, "rendered_asset", asset)
        object.__setattr__(
            self, "rendered_sha256", _hash(self.rendered_sha256, "rendered_sha256")
        )
        object.__setattr__(self, "blocks", tuple(self.blocks))


@dataclass(frozen=True, slots=True)
class MathExpression:
    expression_id: str
    restricted_latex: str
    semantic_mathml: str
    omml: str
    source_regions: tuple[SourceRegion, ...]
    review_state: ReviewState
    confidence: float
    engine_version: str
    fallback_asset: str | None = None
    fallback_sha256: str | None = None
    validation_error: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "expression_id", _safe_id(self.expression_id, "expression_id")
        )
        latex = str(self.restricted_latex or "").strip()
        if len(latex) > 20_000:
            raise ValueError("restricted_latex is too long")
        mathml = str(self.semantic_mathml or "").strip()
        omml = str(self.omml or "").strip()
        fallback = _relative_asset(self.fallback_asset, "fallback_asset")
        fallback_hash = self.fallback_sha256
        if fallback is None and fallback_hash is not None:
            raise ValueError("fallback_sha256 requires fallback_asset")
        if fallback is not None and fallback_hash is None:
            raise ValueError("fallback_asset requires fallback_sha256")
        if fallback_hash is not None:
            fallback_hash = _hash(fallback_hash, "fallback_sha256")
        if self.review_state == ReviewState.TEACHER_VERIFIED and not omml:
            raise ValueError("verified math must have validated OMML")
        if not omml and fallback is None and not self.validation_error:
            raise ValueError("unrenderable math must explain its failure")
        version = str(self.engine_version or "").strip()
        if not version or len(version) > 120:
            raise ValueError("math engine_version is invalid")
        object.__setattr__(self, "restricted_latex", latex)
        object.__setattr__(self, "semantic_mathml", mathml)
        object.__setattr__(self, "omml", omml)
        object.__setattr__(self, "source_regions", tuple(self.source_regions))
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        object.__setattr__(self, "engine_version", version)
        object.__setattr__(self, "fallback_asset", fallback)
        object.__setattr__(self, "fallback_sha256", fallback_hash)
        object.__setattr__(
            self, "validation_error", str(self.validation_error or "").strip()[:500]
        )


@dataclass(frozen=True, slots=True)
class AnswerLink:
    status: ReviewState
    text: str = ""
    source_regions: tuple[SourceRegion, ...] = ()
    basis: tuple[str, ...] = ()
    confidence: float = 0.0

    def __post_init__(self) -> None:
        text = str(self.text or "").strip()
        if len(text) > 200_000:
            raise ValueError("answer text is too long")
        basis = tuple(str(item).strip() for item in self.basis if str(item).strip())
        if self.status == ReviewState.TEACHER_VERIFIED and not (
            text or self.source_regions
        ):
            raise ValueError("verified answer must have text or a source region")
        if self.status == ReviewState.MISSING and (text or self.source_regions):
            raise ValueError("missing answer cannot carry content")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "source_regions", tuple(self.source_regions))
        object.__setattr__(self, "basis", basis)
        object.__setattr__(self, "confidence", _confidence(self.confidence))


@dataclass(frozen=True, slots=True)
class QuestionDraft:
    question_id: str
    question_number: str
    question_text: str
    source_regions: tuple[SourceRegion, ...]
    answer: AnswerLink
    math_expressions: tuple[MathExpression, ...]
    review_state: ReviewState
    blocking_issues: tuple[str, ...]
    content_revision: str
    manual_regions: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "question_id", _safe_id(self.question_id, "question_id"))
        number = str(self.question_number or "").strip()
        if not number or len(number) > 100:
            raise ValueError("question_number is invalid")
        text = str(self.question_text or "").strip()
        if len(text) > 500_000:
            raise ValueError("question_text is too long")
        if not self.source_regions:
            raise ValueError("question draft must retain at least one source region")
        issues = tuple(str(item).strip() for item in self.blocking_issues if str(item).strip())
        if self.review_state == ReviewState.TEACHER_VERIFIED and (not text or issues):
            raise ValueError("verified question must be complete and have no blocking issue")
        object.__setattr__(self, "question_number", number)
        object.__setattr__(self, "question_text", text)
        object.__setattr__(self, "source_regions", tuple(self.source_regions))
        object.__setattr__(self, "math_expressions", tuple(self.math_expressions))
        object.__setattr__(self, "blocking_issues", issues)
        object.__setattr__(
            self, "content_revision", _hash(self.content_revision, "content_revision")
        )
        if (
            self.content_revision != "0" * 64
            and self.content_revision != question_content_revision(self)
        ):
            raise ValueError("question content_revision does not match its content")


def question_content_revision(question: QuestionDraft) -> str:
    return canonical_hash(
        {
            "question_id": question.question_id,
            "question_number": question.question_number,
            "question_text": question.question_text,
            "source_regions": [asdict(item) for item in question.source_regions],
            "answer": asdict(question.answer),
            "math_expressions": [asdict(item) for item in question.math_expressions],
            "review_state": question.review_state,
            "blocking_issues": list(question.blocking_issues),
            "manual_regions": question.manual_regions,
        }
    )


def make_question_draft(**values: Any) -> QuestionDraft:
    draft = QuestionDraft(content_revision="0" * 64, **values)
    return replace(draft, content_revision=question_content_revision(draft))


@dataclass(frozen=True, slots=True)
class DocumentSnapshot:
    document_id: str
    operation_id: str
    source_id: str
    source_filename: str
    media_type: str
    source_sha256: str
    source_revision: str
    source_asset: str
    parser_profile: str
    parser_version: int
    state: DocumentState
    pages: tuple[PageSnapshot, ...]
    questions: tuple[QuestionDraft, ...]
    snapshot_revision: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "document_id", _safe_id(self.document_id, "document_id"))
        object.__setattr__(self, "operation_id", _safe_id(self.operation_id, "operation_id"))
        object.__setattr__(self, "source_id", _safe_id(self.source_id, "source_id"))
        filename = str(self.source_filename or "").strip()
        if not filename or filename != PurePosixPath(filename.replace("\\", "/")).name:
            raise ValueError("source_filename must not contain a path")
        if self.media_type not in _MEDIA_TYPES:
            raise ValueError("media_type is not supported")
        asset = _relative_asset(self.source_asset, "source_asset")
        assert asset is not None
        profile = str(self.parser_profile or "").strip()
        if not profile or len(profile) > 100:
            raise ValueError("parser_profile is invalid")
        if int(self.parser_version) < 1:
            raise ValueError("parser_version must be positive")
        page_numbers = [page.page_number for page in self.pages]
        if page_numbers != list(range(1, len(self.pages) + 1)):
            raise ValueError("pages must be complete and sequential")
        question_ids = [question.question_id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("question IDs must be unique")
        object.__setattr__(self, "source_filename", filename)
        object.__setattr__(self, "source_sha256", _hash(self.source_sha256, "source_sha256"))
        object.__setattr__(self, "source_revision", _hash(self.source_revision, "source_revision"))
        object.__setattr__(self, "snapshot_revision", _hash(self.snapshot_revision, "snapshot_revision"))
        object.__setattr__(self, "source_asset", asset)
        object.__setattr__(self, "parser_profile", profile)
        object.__setattr__(self, "parser_version", int(self.parser_version))
        object.__setattr__(self, "pages", tuple(self.pages))
        object.__setattr__(self, "questions", tuple(self.questions))
        if self.source_revision != "0" * 64 and self.source_revision != source_revision(self):
            raise ValueError("source_revision does not match the source contract")
        if (
            self.snapshot_revision != "0" * 64
            and self.snapshot_revision != snapshot_revision(self)
        ):
            raise ValueError("snapshot_revision does not match the snapshot")


def source_revision(snapshot: DocumentSnapshot) -> str:
    return canonical_hash(
        {
            "source_id": snapshot.source_id,
            "source_sha256": snapshot.source_sha256,
            "media_type": snapshot.media_type,
            "parser_profile": snapshot.parser_profile,
            "parser_version": snapshot.parser_version,
        }
    )


def snapshot_revision(snapshot: DocumentSnapshot) -> str:
    return canonical_hash(
        {
            "document_id": snapshot.document_id,
            "operation_id": snapshot.operation_id,
            "source_revision": snapshot.source_revision,
            "state": snapshot.state,
            "pages": [asdict(item) for item in snapshot.pages],
            "questions": [asdict(item) for item in snapshot.questions],
        }
    )


def make_document_snapshot(**values: Any) -> DocumentSnapshot:
    provisional = DocumentSnapshot(
        source_revision="0" * 64,
        snapshot_revision="0" * 64,
        **values,
    )
    with_source = replace(provisional, source_revision=source_revision(provisional))
    return replace(with_source, snapshot_revision=snapshot_revision(with_source))


@dataclass(frozen=True, slots=True)
class DocumentSource:
    source_id: str
    filename: str
    media_type: str
    content: bytes = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _safe_id(self.source_id, "source_id"))
        filename = str(self.filename or "").strip()
        if not filename or filename != PurePosixPath(filename.replace("\\", "/")).name:
            raise ValueError("filename must not contain a path")
        if self.media_type not in _MEDIA_TYPES:
            raise ValueError("media_type is not supported")
        if not self.content:
            raise ValueError("document source is empty")
        object.__setattr__(self, "filename", filename)
        object.__setattr__(self, "content", bytes(self.content))

    @property
    def sha256(self) -> str:
        return sha256_bytes(self.content)


@dataclass(frozen=True, slots=True)
class ManualQuestionRegion:
    question_id: str
    question_number: str
    question_regions: tuple[SourceRegion, ...]
    answer_regions: tuple[SourceRegion, ...] = ()
    answer_text: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "question_id", _safe_id(self.question_id, "question_id"))
        number = str(self.question_number or "").strip()
        if not number or len(number) > 100:
            raise ValueError("question_number is invalid")
        if not self.question_regions:
            raise ValueError("manual question requires at least one region")
        object.__setattr__(self, "question_number", number)
        object.__setattr__(self, "question_regions", tuple(self.question_regions))
        object.__setattr__(self, "answer_regions", tuple(self.answer_regions))
        object.__setattr__(self, "answer_text", str(self.answer_text or "").strip())


@dataclass(frozen=True, slots=True)
class PrepareSourceCommand:
    operation_id: str
    source: DocumentSource
    parser_profile: str = "question-document-v1"
    parser_version: int = 1
    manual_questions: tuple[ManualQuestionRegion, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_id", _safe_id(self.operation_id, "operation_id"))
        profile = str(self.parser_profile or "").strip()
        if not profile or len(profile) > 100:
            raise ValueError("parser_profile is invalid")
        if int(self.parser_version) < 1:
            raise ValueError("parser_version must be positive")
        ids = [item.question_id for item in self.manual_questions]
        if len(ids) != len(set(ids)):
            raise ValueError("manual question IDs must be unique")
        object.__setattr__(self, "parser_profile", profile)
        object.__setattr__(self, "parser_version", int(self.parser_version))
        object.__setattr__(self, "manual_questions", tuple(self.manual_questions))


@dataclass(frozen=True, slots=True)
class QuestionReviewDecision:
    question_id: str
    question_text: str | None = None
    question_regions: tuple[SourceRegion, ...] | None = None
    answer_text: str | None = None
    answer_regions: tuple[SourceRegion, ...] | None = None
    answer_status: ReviewState | None = None
    confirm_content: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "question_id", _safe_id(self.question_id, "question_id"))
        if self.question_regions is not None and not self.question_regions:
            raise ValueError("question_regions cannot be empty")
        if self.question_regions is not None:
            object.__setattr__(self, "question_regions", tuple(self.question_regions))
        if self.answer_regions is not None:
            object.__setattr__(self, "answer_regions", tuple(self.answer_regions))


@dataclass(frozen=True, slots=True)
class ApplyReviewCommand:
    operation_id: str
    expected_snapshot_revision: str
    decisions: tuple[QuestionReviewDecision, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_id", _safe_id(self.operation_id, "operation_id"))
        object.__setattr__(
            self,
            "expected_snapshot_revision",
            _hash(self.expected_snapshot_revision, "expected_snapshot_revision"),
        )
        ids = [item.question_id for item in self.decisions]
        if not self.decisions or len(ids) != len(set(ids)):
            raise ValueError("review decisions must be non-empty and unique")
        object.__setattr__(self, "decisions", tuple(self.decisions))


@dataclass(frozen=True, slots=True)
class PublishCommand:
    operation_id: str
    expected_snapshot_revision: str
    question_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_id", _safe_id(self.operation_id, "operation_id"))
        object.__setattr__(
            self,
            "expected_snapshot_revision",
            _hash(self.expected_snapshot_revision, "expected_snapshot_revision"),
        )
        ids = tuple(_safe_id(item, "question_id") for item in self.question_ids)
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("question_ids must be non-empty and unique")
        object.__setattr__(self, "question_ids", ids)


@dataclass(frozen=True, slots=True)
class ExportWordCommand:
    operation_id: str
    expected_snapshot_revision: str
    output_path: str
    title: str = "题目导出"
    question_ids: tuple[str, ...] = ()
    include_answers: bool = False
    render_mode: str = "normalized"

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_id", _safe_id(self.operation_id, "operation_id"))
        object.__setattr__(
            self,
            "expected_snapshot_revision",
            _hash(self.expected_snapshot_revision, "expected_snapshot_revision"),
        )
        output_path = str(self.output_path or "").strip()
        if not output_path:
            raise ValueError("output_path is required")
        title = str(self.title or "").strip()
        if not title or len(title) > 200:
            raise ValueError("export title is invalid")
        ids = tuple(_safe_id(item, "question_id") for item in self.question_ids)
        if len(ids) != len(set(ids)):
            raise ValueError("question_ids must be unique")
        mode = str(self.render_mode or "").strip()
        if mode not in {"faithful", "normalized"}:
            raise ValueError("render_mode must be faithful or normalized")
        object.__setattr__(self, "output_path", output_path)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "question_ids", ids)
        object.__setattr__(self, "render_mode", mode)


@dataclass(frozen=True, slots=True)
class PublishedQuestion:
    source_question_id: str
    bank_question_id: int
    content_revision: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_question_id", _safe_id(self.source_question_id, "source_question_id")
        )
        if int(self.bank_question_id) < 1:
            raise ValueError("bank_question_id must be positive")
        object.__setattr__(self, "bank_question_id", int(self.bank_question_id))
        object.__setattr__(
            self, "content_revision", _hash(self.content_revision, "content_revision")
        )


@dataclass(frozen=True, slots=True)
class PublishReceipt:
    operation_id: str
    source_revision: str
    snapshot_revision: str
    state: PublishState
    questions: tuple[PublishedQuestion, ...]
    manifest_sha256: str
    asset_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_id", _safe_id(self.operation_id, "operation_id"))
        object.__setattr__(
            self, "source_revision", _hash(self.source_revision, "source_revision")
        )
        object.__setattr__(
            self, "snapshot_revision", _hash(self.snapshot_revision, "snapshot_revision")
        )
        object.__setattr__(self, "questions", tuple(self.questions))
        object.__setattr__(
            self, "manifest_sha256", _hash(self.manifest_sha256, "manifest_sha256")
        )
        assets = tuple(
            _relative_asset(item, "asset_path") for item in self.asset_paths
        )
        object.__setattr__(self, "asset_paths", assets)


@dataclass(frozen=True, slots=True)
class FormulaFallback:
    question_id: str
    expression_id: str
    reason: str
    asset_path: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "question_id", _safe_id(self.question_id, "question_id"))
        object.__setattr__(
            self, "expression_id", _safe_id(self.expression_id, "expression_id")
        )
        reason = str(self.reason or "").strip()
        if not reason:
            raise ValueError("formula fallback must have a reason")
        object.__setattr__(self, "reason", reason[:500])
        object.__setattr__(
            self, "asset_path", _relative_asset(self.asset_path, "asset_path")
        )


@dataclass(frozen=True, slots=True)
class ExportReceipt:
    operation_id: str
    snapshot_revision: str
    state: ExportState
    artifact_path: str
    artifact_sha256: str
    question_ids: tuple[str, ...]
    content_revisions: tuple[str, ...]
    style_profile_sha256: str
    converter_version: str
    fallbacks: tuple[FormulaFallback, ...]
    validation_errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_id", _safe_id(self.operation_id, "operation_id"))
        object.__setattr__(
            self, "snapshot_revision", _hash(self.snapshot_revision, "snapshot_revision")
        )
        if not str(self.artifact_path or "").strip():
            raise ValueError("artifact_path is required")
        object.__setattr__(
            self, "artifact_sha256", _hash(self.artifact_sha256, "artifact_sha256")
        )
        object.__setattr__(
            self,
            "question_ids",
            tuple(_safe_id(item, "question_id") for item in self.question_ids),
        )
        revisions = tuple(
            _hash(item, "content_revision") for item in self.content_revisions
        )
        if len(revisions) != len(self.question_ids):
            raise ValueError("content_revisions must align with question_ids")
        object.__setattr__(self, "content_revisions", revisions)
        object.__setattr__(
            self,
            "style_profile_sha256",
            _hash(self.style_profile_sha256, "style_profile_sha256"),
        )
        converter_version = str(self.converter_version or "").strip()
        if not converter_version or len(converter_version) > 120:
            raise ValueError("converter_version is invalid")
        object.__setattr__(self, "converter_version", converter_version)
        object.__setattr__(self, "fallbacks", tuple(self.fallbacks))
        object.__setattr__(
            self,
            "validation_errors",
            tuple(str(item).strip()[:500] for item in self.validation_errors if str(item).strip()),
        )


def snapshot_to_payload(snapshot: DocumentSnapshot) -> dict[str, Any]:
    return asdict(snapshot)


def _region_from_payload(payload: Mapping[str, Any]) -> SourceRegion:
    return SourceRegion(
        page_number=int(payload["page_number"]),
        polygon=tuple(tuple(point) for point in payload["polygon"]),
        source=RecognitionSource(payload["source"]),
        confidence=float(payload.get("confidence", 1.0)),
        coordinate_space=str(payload.get("coordinate_space") or "normalized"),
    )


def math_expression_from_payload(payload: Mapping[str, Any]) -> MathExpression:
    return MathExpression(
        expression_id=str(payload["expression_id"]),
        restricted_latex=str(payload.get("restricted_latex") or ""),
        semantic_mathml=str(payload.get("semantic_mathml") or ""),
        omml=str(payload.get("omml") or ""),
        source_regions=tuple(
            _region_from_payload(item)
            for item in payload.get("source_regions") or ()
        ),
        review_state=ReviewState(payload["review_state"]),
        confidence=float(payload.get("confidence", 0.0)),
        engine_version=str(payload["engine_version"]),
        fallback_asset=payload.get("fallback_asset"),
        fallback_sha256=payload.get("fallback_sha256"),
        validation_error=str(payload.get("validation_error") or ""),
    )


def snapshot_from_payload(payload: Mapping[str, Any]) -> DocumentSnapshot:
    pages: list[PageSnapshot] = []
    for raw_page in payload.get("pages") or ():
        page = dict(raw_page)
        blocks = tuple(
            LayoutBlock(
                block_id=str(raw["block_id"]),
                page_number=int(raw["page_number"]),
                kind=LayoutBlockKind(raw["kind"]),
                text=str(raw.get("text") or ""),
                region=_region_from_payload(raw["region"]),
                reading_order=int(raw["reading_order"]),
                source=RecognitionSource(raw["source"]),
                confidence=float(raw["confidence"]),
                engine_version=str(raw["engine_version"]),
                requires_review=bool(raw.get("requires_review")),
            )
            for raw in page.get("blocks") or ()
        )
        raw_transform = page.get("transform") or {}
        pages.append(
            PageSnapshot(
                page_number=int(page["page_number"]),
                width=float(page["width"]),
                height=float(page["height"]),
                rendered_asset=str(page["rendered_asset"]),
                rendered_sha256=str(page["rendered_sha256"]),
                text_layer_state=TextLayerState(page["text_layer_state"]),
                blocks=blocks,
                transform=ImageTransform(
                    rotation_degrees=int(raw_transform.get("rotation_degrees", 0)),
                    perspective_corners=tuple(
                        tuple(point)
                        for point in raw_transform.get("perspective_corners") or ()
                    ),
                    method=str(raw_transform.get("method") or "identity"),
                    requires_review=bool(raw_transform.get("requires_review")),
                ),
                rendered_width=int(
                    page.get("rendered_width") or round(float(page["width"]))
                ),
                rendered_height=int(
                    page.get("rendered_height") or round(float(page["height"]))
                ),
                source_to_rendered=tuple(
                    float(item)
                    for item in page.get("source_to_rendered")
                    or (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
                ),
            )
        )
    questions: list[QuestionDraft] = []
    for raw_question in payload.get("questions") or ():
        raw_answer = raw_question["answer"]
        math_items = [
            math_expression_from_payload(raw_math)
            for raw_math in raw_question.get("math_expressions") or ()
        ]
        questions.append(
            QuestionDraft(
                question_id=str(raw_question["question_id"]),
                question_number=str(raw_question["question_number"]),
                question_text=str(raw_question.get("question_text") or ""),
                source_regions=tuple(
                    _region_from_payload(item)
                    for item in raw_question.get("source_regions") or ()
                ),
                answer=AnswerLink(
                    status=ReviewState(raw_answer["status"]),
                    text=str(raw_answer.get("text") or ""),
                    source_regions=tuple(
                        _region_from_payload(item)
                        for item in raw_answer.get("source_regions") or ()
                    ),
                    basis=tuple(raw_answer.get("basis") or ()),
                    confidence=float(raw_answer.get("confidence", 0.0)),
                ),
                math_expressions=tuple(math_items),
                review_state=ReviewState(raw_question["review_state"]),
                blocking_issues=tuple(raw_question.get("blocking_issues") or ()),
                content_revision=str(raw_question["content_revision"]),
                manual_regions=bool(raw_question.get("manual_regions")),
            )
        )
    return DocumentSnapshot(
        document_id=str(payload["document_id"]),
        operation_id=str(payload["operation_id"]),
        source_id=str(payload["source_id"]),
        source_filename=str(payload["source_filename"]),
        media_type=str(payload["media_type"]),
        source_sha256=str(payload["source_sha256"]),
        source_revision=str(payload["source_revision"]),
        source_asset=str(payload["source_asset"]),
        parser_profile=str(payload["parser_profile"]),
        parser_version=int(payload["parser_version"]),
        state=DocumentState(payload["state"]),
        pages=tuple(pages),
        questions=tuple(questions),
        snapshot_revision=str(payload["snapshot_revision"]),
    )


__all__ = [
    "AnswerLink",
    "ApplyReviewCommand",
    "DocumentSnapshot",
    "DocumentSource",
    "DocumentState",
    "ExportWordCommand",
    "ExportReceipt",
    "ExportState",
    "FormulaFallback",
    "ImageTransform",
    "LayoutBlock",
    "LayoutBlockKind",
    "ManualQuestionRegion",
    "MathExpression",
    "PageSnapshot",
    "PrepareSourceCommand",
    "PublishCommand",
    "PublishedQuestion",
    "PublishReceipt",
    "PublishState",
    "QuestionDraft",
    "QuestionReviewDecision",
    "RecognitionSource",
    "ReviewState",
    "SourceRegion",
    "TextLayerState",
    "canonical_hash",
    "make_document_snapshot",
    "make_question_draft",
    "math_expression_from_payload",
    "sha256_bytes",
    "snapshot_from_payload",
    "snapshot_to_payload",
]
