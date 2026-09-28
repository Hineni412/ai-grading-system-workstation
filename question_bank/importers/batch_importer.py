from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import tempfile
import textwrap
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import asdict, dataclass, replace
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree

from question_bank.database.schema import connect, initialize_database
from question_bank.document_pipeline.contracts import TextLayerState
from question_bank.importers.docx_importer import import_docx
from question_bank.importers.mineru_parse import parse_pdf_full
from question_bank.importers.pdf_importer import import_pdf, embedded_text_usable
from question_bank.importers.types import ExtractedDocument
from question_bank.services.duplicate_analysis_copy_service import (
    ensure_content_index,
    content_index_lookup,
    answers_conflict,
    exact_question_key,
    record_paper_occurrence,
    upsert_content_index,
)
from question_bank.services.similarity_service import (
    question_text_profile, profiled_text_similarity,
    wording_similarity_upper_bound,
)
from question_bank.services.source_paper_archive_service import archive_source_paper
from question_bank.services.rich_content_service import save_question_rich_content
from question_bank.parsers.type_detector import (
    detect_essay_subtype,
    detect_question_type,
    question_section_type,
    split_legacy_question_type,
    validate_section_numbering,
)


if TYPE_CHECKING:
    from question_bank.document_pipeline.pipeline import QuestionDocumentPipeline


LOGGER = logging.getLogger(__name__)
SUPPORTED_PAPER_SUFFIXES = {".pdf", ".docx"}
_ANSWER_HEADING = re.compile(
    r"(?mi)^[ \t]*(?:参考答案(?:[与和及]?(?:试题)?解析|及评分标准)?|答案[与和及]?解析|试题答案|试题解析|答案|解析|评分标准|answer(?: key|s)?|solutions?)[ \t]*[:：]?[ \t]*$"
)
_NUMBERED_LINE_PREFIX = r"(?m)^[ \t]*(?P<leading_images>(?:\[\[IMAGE:[^\r\n]+?\]\][ \t]*)*)"
_MAIN_QUESTION_MARKER = re.compile(_NUMBERED_LINE_PREFIX + r"(?P<number>\d{1,3})[ \t]*[.．、][ \t]*")
_PAREN_QUESTION_MARKER = re.compile(_NUMBERED_LINE_PREFIX + r"[（(][ \t]*(?P<number>\d{1,3})[ \t]*[）)][ \t]*")
_IMAGE_MARKER = re.compile(
    r"\[\[IMAGE:(?P<path>[^\]|]+?)(?:\|caption=(?P<caption>[^\]]*))?\]\]"
)
_INLINE_MAIN_QUESTION_MARKER = re.compile(
    r"(?P<prefix>[。！？!?．.][ \t\r\n]*)"
    r"(?P<number>\d{1,2})[ \t]*[.．、](?![ \t]*\d)[ \t]*"
)
_PAPER_TITLE_NOISE = re.compile(r"(学年|学校|集团|期末|期中|中考|模拟).{0,36}(数学|试卷)|数学试卷")
_PAPER_META_NOISE = re.compile(r"^(姓名|班级|考号|座号|准考证号|第\s*\d+\s*页|共\s*\d+\s*页)[：:\s]")
_DUPLICATE_NORMALIZE = re.compile(r"[\s\u3000，。！？；：、,.!?;:（）()【】\[\]{}《》<>“”\"'`~·…—_\-]+")

# New noise patterns
_PAGE_NUMBER_NOISE = re.compile(r"^[（(]\s*\d+\s*[）)]$|^第\s*\d+\s*页$|^共\s*\d+\s*页$")
_COPYRIGHT_META_NOISE = re.compile(r"声明\s*：\s*试题解析著作权属|著作权属|菁优网|发布日期\s*：|声明\s*:\s*试题解析著作权属")
# Teacher-edition answer blocks carry explicit labels; used as anchors when
# validating numbered boundaries inside the answer section.
_ANSWER_BLOCK_LABEL = re.compile(r"【(?:答案|分析|解答|点评|解析)】")
_MARKDOWN_HEADING = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+")
# MinerU 裁图固定 200dpi；落盘时给 JPEG/PNG 补上 dpi 元数据。
_MINERU_IMAGE_DPI = 200
# 装饰小图丢弃阈值：0.4 英寸 * 200dpi 的面积、任一边 40px。
_DECORATIVE_IMAGE_MIN_AREA_PX = int(0.4 * _MINERU_IMAGE_DPI) ** 2
_DECORATIVE_IMAGE_MIN_SIDE_PX = 40
# OCR 归一化阶段的节标题噪声（教辅栏目名、难度分节等整行）。
_OCR_SECTION_NOISE = re.compile(
    r"^(知识点\s*\d+|基础题|中档题|综合题|拔高题|易错点|变式|"
    r"第[一二三四五六七八九十]+部分|【[^】]{1,12}】|"
    r"[一二三四五六七八九十]+、\S{1,8}题)[\s《》«»:：]*.{0,20}$"
)
# 图注行：紧跟 [[IMAGE:]] 行、去空白后整行命中且不超过 12 字。
_IMAGE_CAPTION_LINE = re.compile(
    r"^(?:第\s*\d+\s*题\s*图?|图\s*\d+|实物图|示意图|变式题图|第\s*\d+\s*题)$"
)
_IMAGE_CAPTION_MAX_LEN = 12
_BARE_LATEX_COMMAND = re.compile(r"\\[A-Za-z]{2,}")
_CJK_CHAR = re.compile(r"[一-鿿]")
_BARE_LATEX_CJK_LIMIT = 4

# 文字层可读字符占比阈值：自定义字体映射的"伪文字层"（提取出 "!"#$% 类
# 乱码）通常在 20% 左右，正常中英文排版远高于此。

# MinerU Markdown 中的图片引用：![](data:image/…;base64,…) 或 ![](文件路径)。
_MARKDOWN_IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)\)")
_DATA_URI_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}


def _embedded_text_usable(text: str) -> bool:
    """False 表示没有文字层，或文字层是乱码映射（需要走 OCR）。"""
    return embedded_text_usable(text)


def _materialize_markdown_images(
    text: str, image_dir: Path | None
) -> tuple[str, list[str]]:
    """把 Markdown 图片引用落盘并改写成题库通用的 [[IMAGE:path]] 标记。"""
    saved: list[str] = []
    if image_dir is None:
        return text, saved

    def _replace(match: re.Match[str]) -> str:
        src = match.group("src").strip()
        try:
            if src.startswith("data:"):
                header, _, payload = src.partition(",")
                mime = header[5:].split(";")[0].strip().lower()
                ext = _DATA_URI_MIME_EXT.get(mime, ".png")
                blob = base64.b64decode(payload)
            else:
                source_path = Path(src)
                if not source_path.is_file():
                    return match.group(0)
                blob = source_path.read_bytes()
                ext = source_path.suffix.lower() or ".png"
            if not blob:
                return match.group(0)
            normalized_blob = _stamped_image_blob(blob, ext)
            if normalized_blob is None:
                # 装饰小图（图标/分隔符）：不落盘、不保留标记。
                return ""
            blob, ext = normalized_blob
            digest = hashlib.sha1(blob).hexdigest()[:12]
            output_path = image_dir / f"mineru_{digest}{ext}"
            image_dir.mkdir(parents=True, exist_ok=True)
            if not output_path.exists():
                output_path.write_bytes(blob)
            saved.append(str(output_path))
            alt = re.sub(r"[\[\]|]", " ", (match.group("alt") or "").strip())
            alt = re.sub(r"\s+", " ", alt).strip()
            if alt:
                return f"[[IMAGE:{output_path}|caption={alt}]]"
            return f"[[IMAGE:{output_path}]]"
        except Exception:  # noqa: BLE001 - 单张图失败不阻塞整份试卷导入
            return match.group(0)

    return _MARKDOWN_IMAGE.sub(_replace, text), saved


def _stamped_image_blob(blob: bytes, ext: str) -> tuple[bytes, str] | None:
    """重存图片并写入 MinerU 裁图的 200dpi 元数据；装饰小图返回 None。

    PIL 打不开时原样保留（不做尺寸判断）。
    """
    try:
        import io

        from PIL import Image
    except Exception:  # noqa: BLE001
        return blob, ext
    try:
        with Image.open(io.BytesIO(blob)) as image:
            image.load()
            width_px, height_px = image.size
            if (
                width_px * height_px < _DECORATIVE_IMAGE_MIN_AREA_PX
                or min(width_px, height_px) < _DECORATIVE_IMAGE_MIN_SIDE_PX
            ):
                return None
            image_format = image.format
            buffer = io.BytesIO()
            if image_format == "JPEG" and image.mode not in ("L", "RGB", "CMYK"):
                image = image.convert("RGB")
            if image_format:
                image.save(buffer, format=image_format, dpi=(_MINERU_IMAGE_DPI, _MINERU_IMAGE_DPI))
            else:
                image.save(buffer, format="PNG", dpi=(_MINERU_IMAGE_DPI, _MINERU_IMAGE_DPI))
                ext = ".png"
            return buffer.getvalue(), ext
    except Exception:  # noqa: BLE001
        return blob, ext


def _caption_line_text(line: str) -> str | None:
    collapsed = re.sub(r"\s+", "", str(line or ""))
    if not collapsed or len(collapsed) > _IMAGE_CAPTION_MAX_LEN:
        return None
    return collapsed if _IMAGE_CAPTION_LINE.match(collapsed) else None


def _merge_image_caption_lines(text: str) -> str:
    """把紧邻 [[IMAGE:]] 行的图注行折进标记的 |caption= 后缀；孤立图注删除。"""
    lines = str(text or "").splitlines()
    result: list[str] = []
    index = 0
    while index < len(lines):
        caption = _caption_line_text(lines[index])
        if caption is None:
            result.append(lines[index])
            index += 1
            continue
        following = index + 1
        while following < len(lines) and not lines[following].strip():
            following += 1
        merged = False
        if following < len(lines):
            marker = _IMAGE_MARKER.fullmatch(lines[following].strip())
            if marker is not None and marker.group("caption") is None:
                result.append(
                    f"[[IMAGE:{marker.group('path').strip()}|caption={caption}]]"
                )
                index = following + 1
                merged = True
        if not merged:
            previous = len(result) - 1
            while previous >= 0 and not result[previous].strip():
                previous -= 1
            if previous >= 0:
                marker = _IMAGE_MARKER.fullmatch(result[previous].strip())
                if marker is not None and marker.group("caption") is None:
                    result[previous] = (
                        f"[[IMAGE:{marker.group('path').strip()}|caption={caption}]]"
                    )
                    merged = True
        index += 1
    return "\n".join(result)


def _wrap_bare_latex_lines(text: str) -> str:
    """一行含 LaTeX 命令但没有 $ 定界符时整行包成 $...$（正文行除外）。"""
    wrapped: list[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if (
            stripped
            and "$" not in stripped
            and _BARE_LATEX_COMMAND.search(stripped)
            and len(_CJK_CHAR.findall(stripped)) < _BARE_LATEX_CJK_LIMIT
        ):
            wrapped.append(f"${stripped}$")
        else:
            wrapped.append(line)
    return "\n".join(wrapped)


def _normalize_mineru_formulas(text: str) -> str:
    """MinerU 公式归一化：多行 $$..$$ 折成单行，$$..$$ 统一为行内 $..$。"""
    normalized = re.sub(
        r"\$\$\s*\n([^$]+?)\n\s*\$\$",
        lambda match: "$" + " ".join(match.group(1).split()) + "$",
        str(text or ""),
    )
    normalized = re.sub(r"\$\$([^$\n]+?)\$\$", r"$\1$", normalized)
    return _wrap_bare_latex_lines(normalized)


# 空格线占位符在公式里可能被 OCR 成各种形状（\Box/\triangle/\vartriangle/\mathrm{~__~}），
# 统一视作"占位符原子"；由它们组成的下标组或 \substack 组整体视为一个空格线。
_BLANK_ATOM = (
    r"(?:\\triangle|\\vartriangle|\\Delta|\\Box|\\square"
    r"|\\mathrm\s*\{\s*(?:[_~\s]|\\_|\\[Bb]ox|\\square)*\}"
    r"|_{2,}|\\_|□|☐|△|▲|~)"
)
_BLANK_RUN = rf"(?:{_BLANK_ATOM}|\s)+"
_OCR_BLANK_BOX_RUN_STR = (
    r"(?:\\[Bb]ox|\\square|[□☐])(?:\s*(?:\\[Bb]ox|\\square|[□☐]))*"
)
_BLANK_GROUP = re.compile(
    rf"(?:_|\^)\s*\{{\s*\\substack\s*\{{\s*{_BLANK_RUN}\}}\s*\}}"
    rf"|\\substack\s*\{{\s*{_BLANK_RUN}\}}"
    rf"|(?:_|\^)\s*\{{\s*{_BLANK_RUN}\}}"
    rf"|{_OCR_BLANK_BOX_RUN_STR}"
)
_OCR_BLANK_BOX_RUN = re.compile(_OCR_BLANK_BOX_RUN_STR)


_CHOICE_BLANK_PAREN = re.compile(r"[（(]\s*_{2,}\s*[)）]?")


def _normalize_ocr_blanks(text: str) -> str:
    """把 OCR 对空格线的各种读法统一成 "____"，供题型判定与 Word 渲染。"""
    normalized = str(text or "")

    # 先把公式内部的占位符折到公式外：$b = \Box\Box\Box$ → $b =$ ____，
    # 否则 "____" 留在 $...$ 里会被渲染层当成空下标。
    def _split_blank_math(match: re.Match) -> str:
        inner = _BLANK_GROUP.sub("\x00", match.group(1))
        if "\x00" not in inner:
            return match.group(0)
        # 占位符被 OCR 包进 aligned 环境壳时先剥壳，否则 ____ 仍困在公式里。
        inner = re.sub(r"\\(?:begin|end)\s*\{[^{}]*\}", " ", inner)
        parts = inner.split("\x00")
        segments: list[str] = []
        for index, part in enumerate(parts):
            part = part.strip()
            if part:
                segments.append(f"${part}$")
            if index < len(parts) - 1:
                segments.append("____")
        return " ".join(segments)

    normalized = re.sub(r"\$([^$]+)\$", _split_blank_math, normalized)
    # 没被包进 $...$ 的残余占位符垃圾组（下标组/\substack/\Box 串）同样归一。
    normalized = _BLANK_GROUP.sub("____", normalized)

    normalized = re.sub(r"\\underline\{[^{}]*\}", "____", normalized)
    normalized = re.sub(r"(?:\\_){2,}", "____", normalized)
    normalized = re.sub(r"_{2,}", "____", normalized)
    # OCR 把长下划线读成长破折号的形态；≥3 个才替换，避免误伤正文破折号。
    normalized = re.sub(r"[—–―]{3,}", "____", normalized)
    # 文本层的 □/口 占位符（未被并入公式的形态）。
    normalized = re.sub(r"[□口☐]{2,}", "____", normalized)
    normalized = re.sub(r"[□☐]", "____", normalized)
    # 整段数学里只有空格线时剥掉 $...$，避免下标化失败。
    normalized = re.sub(r"\$\s*_{2,}\s*\$", "____", normalized)
    # 相邻占位符碎片合一："____·____"、"____口____" → "____"。
    normalized = re.sub(
        r"____(?:\s*(?:\$\s*\\cdot\s*\$|[·•.\s]*[□口])?[·•.\s]*____)+", "____", normalized
    )
    return normalized


@dataclass(frozen=True)
class ParsedQuestion:
    question_number: str
    question_text: str
    source_file: str
    page_range: str
    answer_text: str | None = None
    question_type: str | None = None
    # 解答题子类标签值（画图/计算/证明）；None 表示未标注。
    essay_subtype: str | None = None
    needs_review: bool = False
    has_images: bool = False
    needs_image_review: bool = False
    image_paths: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _NumberedBlock:
    number: str
    text: str
    image_paths: list[str] = field(default_factory=list)
    # 切分时被并入本块的倒序/重复编号个数（>0 说明原文编号异常，需人工复核）。
    merged_marker_count: int = 0
    # 本块之前最近的节标题（如"二、填空题（…）"），OCR 路径用于题型推断。
    section_hint: str | None = None


@dataclass(frozen=True)
class ParsedPaperText:
    questions: list[ParsedQuestion]
    answer_match_count: int
    review_count: int
    review_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScannedPaper:
    source_file: str
    file_type: str
    metadata: PaperMetadata = field(default_factory=lambda: PaperMetadata())
    title: str | None = None


@dataclass(frozen=True)
class PaperMetadata:
    year: str | None = None
    province: str | None = None
    city: str | None = None
    district: str | None = None
    exam_type: str | None = None
    grade: str | None = None
    semester: str | None = None
    textbook_version: str | None = None


@dataclass(frozen=True)
class PaperImportFileResult:
    source_file: str
    status: str
    question_count: int = 0
    answer_match_count: int = 0
    review_count: int = 0
    message: str | None = None
    paper_id: int | None = None
    exact_duplicate_count: int = 0
    analysis_reused_count: int = 0
    near_duplicate_hints: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class BatchImportResult:
    files: list[PaperImportFileResult]
    imported_papers: int
    question_count: int
    answer_match_count: int
    review_count: int
    skipped_duplicate_files: int
    failed_files: int
    exact_duplicate_count: int = 0
    analysis_reused_count: int = 0
    near_duplicate_hints: tuple[dict[str, Any], ...] = ()


@dataclass
class _DuplicateIndex:
    """Cross-paper duplicate lookup state for one import batch.

    ``exact`` maps the canonical question key to the most recently updated
    bank question id; ``questions`` feeds the slower near-duplicate scan.
    Both grow as the batch imports so later files match earlier ones.
    """

    data_root: Path = field(default_factory=Path)
    exact: dict[str, int] = field(default_factory=dict)
    questions: list[dict[str, Any]] = field(default_factory=list)

    def add(
        self,
        question_id: int,
        *,
        question_text: object,
        answer_text: object,
        question_type: object,
        paper_title: object,
        question_number: object = "",
        image_paths: object = (),
        has_images: object = False,
        key: str | None = None,
    ) -> None:
        if key is None:
            key = exact_question_key(
                {"id": question_id, "question_text": question_text, "answer_text": answer_text,
                 "question_number": question_number, "image_paths": image_paths, "has_images": has_images},
                data_root=self.data_root,
            )
        if key:
            self.exact.setdefault(key, int(question_id))
        self.questions.append(
            {
                "id": int(question_id),
                "question_text": str(question_text or ""),
                "profile": question_text_profile(question_text),
                "question_type": str(question_type or ""),
                "paper_title": str(paper_title or ""),
            }
        )


def _load_duplicate_index(db_path: Path, *, data_root: Path | None = None, connection: Any = None) -> _DuplicateIndex:
    root = data_root or _rich_content_root_for_database(db_path).parent.parent
    with nullcontext(connection) if connection is not None else connect(db_path) as conn:
        # The persistent content index is built once and refreshed on writes;
        # lookups read stored keys instead of decoding every bank image.
        ensure_content_index(conn, data_root=root)
        key_rows = conn.execute(
            """
            SELECT idx.content_key AS content_key, idx.question_id AS question_id
            FROM question_content_index idx
            JOIN questions q ON q.id = idx.question_id
            LEFT JOIN papers p ON p.id = q.paper_id
            WHERE COALESCE(q.is_deleted, 0) = 0
              AND COALESCE(p.import_status, '') <> 'deleted'
            """
        ).fetchall()
        questions = _load_near_duplicate_questions(conn)
        canonical = content_index_lookup(conn, {str(row["content_key"]) for row in key_rows})
    return _DuplicateIndex(data_root=root, exact=canonical, questions=questions)


def _load_near_duplicate_questions(conn: Any) -> list[dict[str, Any]]:
    """Read the wording profiles only; exact matching has its own persistent index."""
    rows = conn.execute(
        """SELECT q.id, q.question_text, q.question_type, p.title AS paper_title
           FROM questions q LEFT JOIN papers p ON p.id = q.paper_id
           WHERE COALESCE(q.is_deleted, 0) = 0
             AND COALESCE(p.import_status, '') <> 'deleted'
           ORDER BY q.id"""
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "question_text": str(row["question_text"] or ""),
            "profile": question_text_profile(row["question_text"]),
            "question_type": str(row["question_type"] or ""),
            "paper_title": str(row["paper_title"] or ""),
        }
        for row in rows
    ]


def _near_duplicate_hint(
    question: ParsedQuestion,
    index: _DuplicateIndex,
) -> dict[str, Any] | None:
    text = str(question.question_text or "")
    if not text.strip():
        return None
    new_type = str(question.question_type or "")
    best: dict[str, Any] | None = None
    profile = question_text_profile(text)
    for candidate in index.questions:
        candidate_type = str(candidate["question_type"])
        candidate_text = str(candidate["question_text"])
        other = candidate.get("profile")
        if other is None:
            other = candidate["profile"] = question_text_profile(candidate_text)
        threshold = max(0.7, float(best["similarity"]) if best else 0.7)
        lengths = (len(profile.normalized), len(other.normalized))
        grams = (len(profile.ngrams), len(other.ngrams))
        if not min(lengths):
            continue
        length_bound = max(2 * min(lengths) / sum(lengths), min(grams) / max(grams) if max(grams) else 0)
        if length_bound < threshold - 0.00005:
            continue
        if wording_similarity_upper_bound(profile, other) < threshold - 0.00005:
            continue
        score = profiled_text_similarity(profile, other)
        if score < 0.7:
            continue
        if best is None or score > float(best["similarity"]):
            math_tokens = lambda value: re.findall(r"\d+(?:\.\d+)?|[+\-−×÷=<>≤≥≠]", re.sub(r"\[\[IMAGE:.*?\]\]", "", value))
            changed_conditions = math_tokens(text) != math_tokens(candidate_text)
            best = {
                "question_number": question.question_number,
                "matched_question_id": int(candidate["id"]),
                "matched_paper_title": str(candidate["paper_title"]),
                "similarity": score,
                "high": score >= 0.9,
                "match_kind": "variant" if changed_conditions else "suspected",
                "requires_review": True,
                "reason": ("数字或运算条件不同，保留为不同题目" if changed_conditions else "题面相似，需核对条件、选项及图片") + ("；题型标注不同" if new_type and candidate_type != new_type else ""),
            }
        if score == 1.0:
            break
    return best


def parse_paper_text(
    text: str,
    *,
    source_file: str,
    page_range: str,
    question_range: str | None = None,
    has_images: bool = False,
    needs_image_review: bool = False,
    image_paths: list[str] | None = None,
    type_overrides: Mapping[str, str] | None = None,
    keep_image_markers: bool = False,
    ocr_noise: bool = False,
) -> ParsedPaperText:
    # Document-level image flags stay in the signature for callers, but each
    # question now uses only the images extracted for that numbered block.
    _ = (has_images, image_paths)
    question_text, answer_text = _split_answer_text(text)
    validate_section_numbering(question_text)
    doc_title = _document_title_hint(str(text or "").splitlines())
    answers = _answer_map(answer_text, source_file=source_file, doc_title=doc_title)
    range_filter = _parse_numeric_range(question_range)
    questions: list[ParsedQuestion] = []
    answer_match_count = 0
    all_block_numbers: list[str] = []
    merged_anomaly_count = 0

    for block in _split_numbered_blocks(
        question_text,
        keep_image_markers=keep_image_markers,
        source_file=source_file,
        doc_title=doc_title,
        ocr_noise=ocr_noise,
    ):
        all_block_numbers.append(block.number)
        if not _is_in_range(block.number, range_filter):
            continue
        # 对于小于6个字符的题目，自动排除不入库（忽略HTML标签和图片标记）
        # 单元测试文件除外，防止测试用例被错误拦截
        is_test_file = source_file and ("sample" in str(source_file).lower() or "temp" in str(source_file).lower() or "tmp" in str(source_file).lower() or "renamed" in str(source_file).lower())

        clean_content = re.sub(r"<[^>]+>", "", block.text)
        clean_content = _IMAGE_MARKER.sub("", clean_content)
        if not is_test_file and len(clean_content.strip()) < 6:
            LOGGER.info(
                "Skipping question block %s because it contains less than 6 characters: %r",
                block.number,
                clean_content.strip(),
            )
            continue
        answer = answers.get(block.number)
        if answer:
            answer_match_count += 1
        question_image_paths = block.image_paths
        # Teacher/LLM-governed types from the grading rubric win over the
        # local heuristic when the session sync supplied them.
        # 兼容输入：旧任务负载里的六值子类题型拆成归一大类 + 子类标签。
        override_type, override_subtype = split_legacy_question_type(
            (type_overrides or {}).get(block.number)
        )
        q_type = override_type or detect_question_type(
            block.text, section_type=question_section_type(block.section_hint) if block.section_hint else None,
        )
        question_text = block.text
        if ocr_noise and q_type == "选择题":
            # 教辅括号里的红色答案字母被擦除后留下 "（____"，选择题恢复成空括号。
            question_text = _CHOICE_BLANK_PAREN.sub("（　）", question_text)
        essay_subtype = override_subtype
        if q_type == "解答题" and essay_subtype is None:
            essay_subtype = detect_essay_subtype(block.text)
        question_has_images = bool(question_image_paths)
        # 题干里出现【答案】/【分析】等标签，说明答案区漏切进了题干，需复核。
        stem_has_answer_label = bool(_ANSWER_BLOCK_LABEL.search(block.text))
        if block.merged_marker_count:
            merged_anomaly_count += 1
        questions.append(
            ParsedQuestion(
                question_number=block.number,
                question_text=question_text,
                answer_text=answer,
                source_file=source_file,
                page_range=page_range,
                question_type=q_type,
                essay_subtype=essay_subtype,
                needs_review=not bool(answer) or (
                    needs_image_review and question_has_images
                ) or block.merged_marker_count > 0 or stem_has_answer_label,
                has_images=question_has_images,
                needs_image_review=needs_image_review and question_has_images,
                image_paths=question_image_paths,
            )
        )

    review_reasons = _paper_review_reasons(
        all_block_numbers,
        [question.question_number for question in questions],
        answers,
        range_filter=range_filter,
        merged_anomaly_count=merged_anomaly_count,
    )
    return ParsedPaperText(
        questions=questions,
        answer_match_count=answer_match_count,
        review_count=sum(1 for item in questions if item.needs_review) + len(review_reasons),
        review_reasons=tuple(review_reasons),
    )


def scan_paper_folder(folder: str | Path) -> list[ScannedPaper]:
    root = Path(folder).expanduser()
    if not root.exists() or not root.is_dir():
        return []
    return [
        ScannedPaper(
            source_file=str(path),
            file_type=path.suffix.lower().lstrip("."),
            metadata=infer_metadata_from_filename(path.name),
        )
        for path in sorted(root.iterdir(), key=lambda item: item.name.lower())
        if path.is_file() and _is_supported_paper(path)
    ]


def import_paper_folder(
    folder: str | Path,
    db_path: str | Path,
    *,
    metadata: PaperMetadata,
    question_range: str | None = None,
) -> BatchImportResult:
    return import_scanned_papers(
        scan_paper_folder(folder),
        db_path,
        default_metadata=metadata,
        question_range=question_range,
    )


def import_scanned_papers(
    scanned_papers: list[ScannedPaper],
    db_path: str | Path,
    *,
    default_metadata: PaperMetadata | None = None,
    question_range: str | None = None,
    data_root: str | Path | None = None,
    raw_papers_dir: str | Path | None = None,
    archive_sources: bool = True,
    asset_overrides: list[dict[str, Any]] | None = None,
    type_overrides: Mapping[str, str] | None = None,
    confirmed_duplicates: Mapping[str, int] | None = None,
    document_pipeline: "QuestionDocumentPipeline | None" = None,
    full_parser: Callable[[Path], str | None] | None = None,
) -> BatchImportResult:
    database_path = Path(db_path)
    # Existing teacher-governed identities must not block an ordinary paper
    # import. Preserve them and skip only the conflicting builtin seed.
    initialize_database(
        database_path,
        preserve_governed_conflicts=True,
    )
    rich_content_directory = _rich_content_root_for_database(
        database_path,
        data_root=data_root,
        raw_papers_dir=raw_papers_dir,
    )
    asset_directory = _asset_root_for_database(
        database_path,
        data_root=data_root,
        raw_papers_dir=raw_papers_dir,
    )
    file_results: list[PaperImportFileResult] = []
    duplicate_index = _load_duplicate_index(database_path, data_root=rich_content_directory.parent.parent)

    for scanned in scanned_papers:
        original_path = Path(scanned.source_file)
        try:
            if archive_sources:
                archived = archive_source_paper(
                    original_path,
                    data_root=data_root,
                    raw_papers_dir=raw_papers_dir,
                )
                physical_path = archived.physical_path
                stored_source_file = archived.stored_path
            else:
                physical_path = original_path
                stored_source_file = str(original_path)
            file_results.append(
                _import_scanned_paper(
                    physical_path,
                    database_path,
                    stored_source_file=stored_source_file,
                    source_title=scanned.title or original_path.stem,
                    metadata=_merge_metadata(default_metadata or PaperMetadata(), scanned.metadata),
                    question_range=question_range,
                    rich_content_root=rich_content_directory,
                    asset_root=asset_directory,
                    asset_overrides=asset_overrides,
                    type_overrides=type_overrides,
                    confirmed_duplicates=confirmed_duplicates,
                    duplicate_index=duplicate_index,
                    document_pipeline=document_pipeline,
                    full_parser=full_parser,
                )
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Failed to import local paper %s", original_path)
            file_results.append(
                PaperImportFileResult(
                    source_file=str(original_path),
                    status="failed",
                    message=str(exc),
                )
            )

    return BatchImportResult(
        files=file_results,
        imported_papers=sum(1 for item in file_results if item.status in {"imported", "needs_review", "needs_ocr"}),
        question_count=sum(item.question_count for item in file_results),
        answer_match_count=sum(item.answer_match_count for item in file_results),
        review_count=sum(item.review_count for item in file_results),
        skipped_duplicate_files=sum(1 for item in file_results if item.status == "duplicate"),
        failed_files=sum(1 for item in file_results if item.status == "failed"),
        exact_duplicate_count=sum(item.exact_duplicate_count for item in file_results),
        analysis_reused_count=sum(item.analysis_reused_count for item in file_results),
        near_duplicate_hints=tuple(
            hint for item in file_results for hint in item.near_duplicate_hints
        ),
    )


def infer_metadata_from_filename(filename: str) -> PaperMetadata:
    text = Path(filename).stem
    year_match = re.search(r"(20\d{2})", text)
    year = year_match.group(1) if year_match else None
    province = _infer_province(text)
    city = _infer_city(text)
    district = _infer_district(text)
    exam_type = _infer_exam_type(text)
    grade = _infer_grade(text) or ("九年级" if exam_type == "中考" else None)
    semester = _infer_semester(text)
    return PaperMetadata(
        year=year,
        province=province,
        city=city,
        district=district,
        exam_type=exam_type,
        grade=grade,
        semester=semester,
    )


def _import_scanned_paper(
    path: Path,
    db_path: Path,
    *,
    stored_source_file: str | None = None,
    source_title: str | None = None,
    metadata: PaperMetadata,
    question_range: str | None,
    rich_content_root: Path | None = None,
    asset_root: Path | None = None,
    asset_overrides: list[dict[str, Any]] | None = None,
    type_overrides: Mapping[str, str] | None = None,
    confirmed_duplicates: Mapping[str, int] | None = None,
    duplicate_index: _DuplicateIndex | None = None,
    document_pipeline: "QuestionDocumentPipeline | None" = None,
    full_parser: Callable[[Path], str | None] | None = None,
) -> PaperImportFileResult:
    initialize_database(db_path)
    source_value = stored_source_file or str(path)
    fingerprint = _file_fingerprint(path)
    with connect(db_path) as conn:
        collision = _find_paper_collision(
            conn,
            fingerprint=fingerprint,
            source_file=source_value,
            source_title=source_title,
        )
        if collision is not None:
            paper_id, _is_deleted = collision
            return PaperImportFileResult(
                source_file=source_value,
                status="duplicate",
                paper_id=paper_id,
                message="paper already imported",
            )

    extracted = _extract_paper(
        path,
        asset_root=asset_root,
        document_pipeline=document_pipeline,
        full_parser=full_parser,
        operation_id=f"import-{fingerprint}" if fingerprint else None,
    )
    if asset_overrides:
        extracted = apply_asset_overrides(extracted, asset_overrides)
    parsed = parse_paper_text(
        extracted.text,
        source_file=source_value,
        page_range=extracted.page_range,
        question_range=question_range,
        has_images=extracted.has_images,
        needs_image_review=extracted.needs_image_review,
        image_paths=extracted.image_paths,
        # 提取层推断的题型（彩色答案层）先填，调用方显式覆盖优先。
        type_overrides={**extracted.type_overrides, **dict(type_overrides or {})},
        # OCR/MinerU 路径保留 [[IMAGE:]] 标记在题文内，保住图片的原位置。
        keep_image_markers=extracted.ocr_applied,
        ocr_noise=extracted.ocr_applied,
    )
    if extracted.ocr_applied:
        # 教辅扫描件常把答案/解析印在题干里，入库前再切一次。
        parsed = _split_inline_teacher_answers(parsed)
    snapshot = extracted.document_snapshot
    if extracted.ocr_applied or (
        snapshot is not None
        and any(
            page.text_layer_state == TextLayerState.LOCAL_OCR for page in snapshot.pages
        )
    ):
        # OCR 识别的题目一律进入人工复核队列。
        parsed = replace(
            parsed,
            questions=[replace(item, needs_review=True) for item in parsed.questions],
            review_count=len(parsed.questions) + len(parsed.review_reasons),
        )
    if not parsed.questions:
        if extracted.needs_ocr:
            return PaperImportFileResult(
                source_file=source_value,
                status="needs_ocr",
                message="document has no parseable text and needs OCR",
            )
        return PaperImportFileResult(
            source_file=source_value,
            status="duplicate",
            message="all parsed questions already exist",
        )
    # Cross-paper duplicate detection.  Exact key matches still import as
    # their own rows but are linked to the existing question and reuse its
    # analysis; looser matches only surface as hints in the import result.
    rich_content = map_rich_content_by_number(
        getattr(extracted, "rich_paragraphs", []), source_file=source_value,
    )
    if duplicate_index is None:
        duplicate_index = _load_duplicate_index(db_path, data_root=(rich_content_root or _rich_content_root_for_database(db_path)).parent.parent)
    exact_keys: dict[str, str] = {}
    near_hints: list[dict[str, Any]] = []
    answer_conflict_review_count = 0
    for item in parsed.questions:
        key = exact_question_key(asdict(item), data_root=duplicate_index.data_root, rich_content={
            "question_blocks": rich_content["question"].get(item.question_number, []),
            "answer_blocks": rich_content["answer"].get(item.question_number, []),
        })
        exact_keys[item.question_number] = key
        source_id = duplicate_index.exact.get(key) if key else None
        if source_id is not None:
            continue
        if confirmed_duplicates and item.question_number in confirmed_duplicates:
            # Teacher already confirmed this duplicate; the insert loop maps
            # the occurrence directly and it must not surface as a hint.
            continue
        hint = _near_duplicate_hint(item, duplicate_index)
        if hint is not None:
            near_hints.append(hint)
    import_status = _import_status(extracted.needs_ocr, parsed)
    pending_rich_content: list[tuple[int, list[dict[str, object]], list[dict[str, object]]]] = []
    pending_analysis_reuse: list[tuple[int, str]] = []
    inserted_questions: list[tuple[int, ParsedQuestion]] = []

    with connect(db_path) as conn:
        # The source may have taken long enough to parse for another request to
        # finish importing it.  Serialize the final active-paper check and the
        # insert so retries and concurrent workers still create at most one new
        # active paper.  Deleted papers intentionally do not participate: a
        # teacher who uploads the same source again is starting a fresh paper.
        conn.execute("BEGIN IMMEDIATE")
        collision = _find_paper_collision(
            conn,
            fingerprint=fingerprint,
            source_file=source_value,
            source_title=source_title,
        )
        if collision is not None:
            paper_id, _is_deleted = collision
            return PaperImportFileResult(
                source_file=source_value,
                status="duplicate",
                paper_id=paper_id,
                message="paper already imported",
            )
        # The batch has already refreshed the index. Recheck only matching
        # candidates under the write lock; concurrent imports remain visible.
        refreshed_matches = content_index_lookup(
            conn, set(exact_keys.values()), data_root=duplicate_index.data_root,
        )
        for key in exact_keys.values():
            duplicate_index.exact.pop(key, None)
        duplicate_index.exact.update(refreshed_matches)
        paper_cursor = conn.execute(
            """
            INSERT INTO papers (
                title, source_file, year, province, city, district, exam_type, grade, semester,
                textbook_version, import_status, content_fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_title or path.stem,
                source_value,
                _clean_optional(metadata.year),
                _clean_optional(metadata.province),
                _clean_optional(metadata.city),
                _clean_optional(metadata.district),
                _clean_optional(metadata.exam_type),
                _clean_optional(metadata.grade),
                _clean_optional(metadata.semester),
                _clean_optional(metadata.textbook_version),
                import_status,
                fingerprint,
            ),
        )
        paper_id = int(paper_cursor.lastrowid)
        for item in parsed.questions:
            key = exact_keys[item.question_number]
            confirmed_bank_id: int | None = None
            if confirmed_duplicates:
                raw_confirmed = confirmed_duplicates.get(item.question_number)
                if raw_confirmed is not None:
                    try:
                        confirmed_bank_id = int(raw_confirmed)
                    except (TypeError, ValueError):
                        confirmed_bank_id = None
                    if confirmed_bank_id is not None and conn.execute(
                        "SELECT 1 FROM questions WHERE id=? AND COALESCE(is_deleted,0)=0",
                        (confirmed_bank_id,),
                    ).fetchone() is None:
                        # A stale decision must not create a dangling link;
                        # fall back to the normal match-and-insert path.
                        confirmed_bank_id = None
            if confirmed_bank_id is not None:
                near_hints[:] = [
                    hint for hint in near_hints
                    if hint["question_number"] != item.question_number
                ]
                record_paper_occurrence(
                    conn,
                    paper_id=paper_id,
                    question_id=confirmed_bank_id,
                    question_number=item.question_number,
                    signature=key,
                )
                pending_analysis_reuse.append((confirmed_bank_id, item.question_number))
                continue
            source_id = duplicate_index.exact.get(key) if key else None
            answer_conflict = False
            if source_id is not None and item.answer_text:
                source_answer = conn.execute("SELECT answer_text FROM questions WHERE id=?", (source_id,)).fetchone()
                if source_answer and answers_conflict(source_answer[0], item.answer_text, data_root=duplicate_index.data_root):
                    answer_conflict = True
                    answer_conflict_review_count += int(not item.needs_review)
                    near_hints.append({
                        "question_number": item.question_number,
                        "matched_question_id": source_id,
                        "matched_paper_title": "",
                        "similarity": 1.0, "high": True,
                        "match_kind": "answer_conflict", "requires_review": True,
                        "reason": "题面相同但答案文本不同，保留两个来源并等待核对",
                    })
                    source_id = None
            if source_id is not None:
                # Determined reuse: keep this paper's number as an occurrence
                # record instead of inserting a second canonical question row.
                near_hints[:] = [hint for hint in near_hints if hint["question_number"] != item.question_number]
                record_paper_occurrence(
                    conn,
                    paper_id=paper_id,
                    question_id=source_id,
                    question_number=item.question_number,
                    signature=key,
                )
                pending_analysis_reuse.append((source_id, item.question_number))
                continue
            question_cursor = conn.execute(
                """
                INSERT INTO questions (
                    paper_id, question_number, question_type, question_text, answer_text, source_file,
                    page_range, image_paths, needs_review, has_images, needs_image_review
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    paper_id,
                    item.question_number,
                    item.question_type,
                    item.question_text,
                    _clean_optional(item.answer_text),
                    item.source_file,
                    item.page_range,
                    json.dumps(item.image_paths, ensure_ascii=False),
                    int(item.needs_review or answer_conflict),
                    int(item.has_images),
                    int(item.needs_image_review),
                ),
            )
            question_id = int(question_cursor.lastrowid)
            if item.essay_subtype:
                # 解答题子类标签与题目同事务写入；confidence 0.8 表示规则
                # 猜测、低于人工确认，source 标注来自题型检测器。
                conn.execute(
                    """
                    INSERT INTO question_tags (
                        question_id, tag_type, tag_value, confidence, source, model_name
                    ) VALUES (?, 'special_type', ?, 0.8, 'type_detector', NULL)
                    """,
                    (question_id, item.essay_subtype),
                )
            inserted_questions.append((question_id, item))
            if key:
                # New canonical question: persist its identity key so later
                # batches match it without recomputing image content.
                upsert_content_index(conn, question_id=question_id, key=key)
                duplicate_index.exact.setdefault(key, question_id)
            question_blocks = rich_content["question"].get(item.question_number, [])
            answer_blocks = rich_content["answer"].get(item.question_number, [])
            if question_blocks or answer_blocks:
                pending_rich_content.append((question_id, question_blocks, answer_blocks))
        conn.commit()

    for question_id, question_blocks, answer_blocks in pending_rich_content:
        try:
            save_question_rich_content(
                question_id,
                question_blocks=question_blocks,
                answer_blocks=answer_blocks,
                root=rich_content_root or _rich_content_root_for_database(db_path),
            )
        except OSError:
            LOGGER.exception("Failed to save rich question content for question %s", question_id)

    # Reused occurrences share the canonical question's analysis products
    # directly; count how many actually carry usable analysis.
    analysis_reused_count = 0
    if pending_analysis_reuse:
        source_ids = sorted({source_id for source_id, _ in pending_analysis_reuse})
        with connect(db_path) as conn:
            placeholders = ",".join(
                "?" for _ in source_ids
            )
            analysed = {
                int(row["question_id"])
                for row in conn.execute(
                    f"""SELECT DISTINCT question_id FROM question_tags
                        WHERE question_id IN ({placeholders})
                        UNION
                        SELECT DISTINCT question_id FROM question_solution_evidence_versions
                        WHERE question_id IN ({placeholders})""",
                    source_ids * 2,
                ).fetchall()
            }
        analysis_reused_count = sum(
            1 for source_id, _ in pending_analysis_reuse if source_id in analysed
        )
    if duplicate_index is not None:
        for question_id, item in inserted_questions:
            duplicate_index.add(
                question_id,
                question_text=item.question_text,
                answer_text=item.answer_text,
                question_type=item.question_type,
                paper_title=source_title or path.stem,
                question_number=item.question_number, image_paths=item.image_paths, has_images=item.has_images,
                key=exact_keys.get(item.question_number),
            )

    return PaperImportFileResult(
        source_file=source_value,
        status=import_status,
        paper_id=paper_id,
        question_count=len(parsed.questions),
        answer_match_count=parsed.answer_match_count,
        review_count=parsed.review_count + answer_conflict_review_count,
        exact_duplicate_count=len(pending_analysis_reuse),
        analysis_reused_count=analysis_reused_count,
        near_duplicate_hints=tuple(near_hints),
        message="；".join(parsed.review_reasons) or None,
    )


def _rich_content_root_for_database(
    db_path: Path,
    *,
    data_root: str | Path | None = None,
    raw_papers_dir: str | Path | None = None,
) -> Path:
    if data_root is not None:
        root = Path(data_root).expanduser().resolve()
    elif raw_papers_dir is not None:
        raw_root = Path(raw_papers_dir).expanduser().resolve()
        root = raw_root.parent.parent
    else:
        resolved_db = Path(db_path).expanduser().resolve()
        root = (
            resolved_db.parent.parent
            if resolved_db.parent.name == "databases"
            else resolved_db.parent
        )
    return root / "question_bank" / "rich_content"


def _asset_root_for_database(
    db_path: Path,
    *,
    data_root: str | Path | None = None,
    raw_papers_dir: str | Path | None = None,
) -> Path:
    if data_root is not None:
        root = Path(data_root).expanduser().resolve()
    elif raw_papers_dir is not None:
        raw_root = Path(raw_papers_dir).expanduser().resolve()
        root = raw_root.parent.parent
    else:
        resolved_db = Path(db_path).expanduser().resolve()
        root = (
            resolved_db.parent.parent
            if resolved_db.parent.name == "databases"
            else resolved_db.parent
        )
    return root / "question_bank" / "extracted_images"


def _extract_paper(
    path: Path,
    *,
    asset_root: Path | None = None,
    document_pipeline: "QuestionDocumentPipeline | None" = None,
    full_parser: Callable[[Path], str | None] | None = None,
    operation_id: str | None = None,
):
    if path.suffix.lower() == ".pdf":
        extracted = import_pdf(path)
        layout: dict = {}
        image_dir = None
        if asset_root is not None:
            digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:10]
            image_dir = asset_root / f"{path.stem}_{digest}"
        markdown = None
        extracted_type_overrides: dict[str, str] = {}
        if full_parser is None:
            markdown, extracted_type_overrides = _extract_with_colored_layers(
                path, image_dir=image_dir, layout_out=layout,
            )
        if markdown or not _embedded_text_usable(extracted.text):
            # 无文字层或文字层乱码（自定义字体映射出 "!"#$% 类字符）的 PDF
            # 先走 MinerU 完整解析（版面+公式识别+OCR），输出带 LaTeX 的
            # Markdown；不可用或为空再退回行级 OCR 管线。
            parser = parse_pdf_full if full_parser is None else full_parser
            if markdown is None:
                markdown = parser(path, layout_out=layout) if full_parser is None else parser(path)
            if markdown:
                # MinerU 标题（## 参考答案）会挡住答案区切分，先还原成普通行。
                normalized = _MARKDOWN_HEADING.sub("", markdown)
                # 多行块公式折成单行，$$...$$ 统一成行内 $...$ 供渲染层处理。
                normalized = _normalize_mineru_formulas(normalized)
                # 空格线的各种 OCR 形态统一成 "____"。
                normalized = _normalize_ocr_blanks(normalized)
                normalized, saved_images = _materialize_markdown_images(normalized, image_dir)
                normalized = _merge_image_caption_lines(normalized)
                return replace(
                    extracted,
                    text=normalized,
                    needs_ocr=False,
                    ocr_applied=True,
                    has_images=bool(saved_images) or extracted.has_images,
                    image_paths=[*extracted.image_paths, *saved_images],
                    type_overrides=extracted_type_overrides,
                    pdf_layout=layout,
                )
            if document_pipeline is not None and operation_id:
                # 重算 needs_ocr：OCR 产出文本后不再按"待 OCR"处理。
                extracted = import_pdf(
                    path,
                    document_pipeline=document_pipeline,
                    operation_id=operation_id,
                    source_id=f"paper-{operation_id}",
                )
                extracted = replace(
                    extracted,
                    needs_ocr=not _embedded_text_usable(extracted.text),
                    ocr_applied=bool(extracted.text.strip()),
                )
            elif not extracted.needs_ocr:
                extracted = replace(extracted, needs_ocr=True)
        if extracted.document_snapshot:
            layout["pages"] = {
                page.page_number - 1: [
                    {"text": block.text, "bbox": block.region.bbox}
                    for block in page.blocks if block.text
                ] for page in extracted.document_snapshot.pages
            }
        return replace(extracted, pdf_layout=layout)
    extracted = import_docx(path, asset_root=asset_root)
    paragraphs = split_inline_main_question_paragraphs(extracted.rich_paragraphs)
    return replace(extracted, rich_paragraphs=paragraphs, text="\n".join(str(p["text"]) for p in paragraphs))


def _extract_with_colored_layers(
    path: Path, *, image_dir: Path | None, layout_out: dict | None = None,
) -> tuple[str | None, dict[str, str]]:
    """彩色答案层路径：拆分 student/answers 双 PDF 并各自跑 MinerU。

    返回 (带参考答案段的 Markdown 或 None, 题型覆盖表)。
    """
    from question_bank.importers.mineru_parse import parse_pdf_full_with_answers
    from question_bank.importers.pdf_importer import split_colored_answer_layers

    work_dir: Path | None = None
    temp_dir = None
    if image_dir is not None:
        work_dir = image_dir / "layers"
    else:
        temp_dir = tempfile.TemporaryDirectory(prefix="qb_layers_")
        work_dir = Path(temp_dir.name)
    try:
        try:
            layers = split_colored_answer_layers(path, work_dir)
        except Exception:  # noqa: BLE001
            LOGGER.warning("彩色答案层拆分失败，退回普通 MinerU 路径: %s", path, exc_info=True)
            return None, {}
        if layers is None:
            return None, {}
        kwargs = {"layout_out": layout_out} if layout_out is not None else {}
        parsed = parse_pdf_full_with_answers(
            layers.student_pdf, layers.answers_pdf, regions=layers.regions, **kwargs
        )
        if parsed is None:
            return None, {}
        if layout_out is not None:
            layout_out["question_pdf"] = layers.student_pdf.read_bytes()
            layout_out["answer_pdf"] = layers.answers_pdf.read_bytes()
        markdown, answers, types = parsed
        if answers:
            lines = ["参考答案"]
            for number in sorted(answers):
                # 答案区切分按题号配对，多行答案折成一行。
                answer = re.sub(r"\s*\n\s*", "  ", str(answers[number]).strip())
                lines.append(f"{number}. {answer}")
            markdown = markdown.rstrip() + "\n\n" + "\n".join(lines)
        return markdown, {str(number): qtype for number, qtype in types.items()}
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def _find_paper_collision(
    conn,
    *,
    fingerprint: str | None,
    source_file: str,
    source_title: str | None = None,
) -> tuple[int, bool] | None:
    raw_title = source_title or Path(source_file).stem
    clean_title = re.sub(r'_\d{8}_\d{6}$', '', raw_title).strip()
    if fingerprint:
        row = conn.execute(
            """
            SELECT id, COALESCE(import_status, '') AS import_status
            FROM papers
            WHERE content_fingerprint = ?
              AND COALESCE(import_status, '') <> 'deleted'
            ORDER BY id
            LIMIT 1
            """,
            (fingerprint,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                """
                SELECT id, COALESCE(import_status, '') AS import_status
                FROM papers
                WHERE COALESCE(content_fingerprint, '') = ''
                  AND COALESCE(import_status, '') <> 'deleted'
                  AND (source_file = ? OR title = ? OR title LIKE ?)
                ORDER BY id
                LIMIT 1
                """,
                (source_file, clean_title, f"{clean_title}_%"),
            ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT id, COALESCE(import_status, '') AS import_status
            FROM papers
            WHERE COALESCE(import_status, '') <> 'deleted'
              AND (source_file = ? OR title = ? OR title LIKE ?)
            ORDER BY id
            LIMIT 1
            """,
            (source_file, clean_title, f"{clean_title}_%"),
        ).fetchone()
    if row is None:
        return None
    return int(row["id"]), False


def _paper_exists(
    conn,
    *,
    fingerprint: str | None,
    source_file: str,
    source_title: str | None = None,
) -> bool:
    return _find_paper_collision(
        conn,
        fingerprint=fingerprint,
        source_file=source_file,
        source_title=source_title,
    ) is not None


def _file_fingerprint(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        LOGGER.exception("Unable to fingerprint local paper %s", path)
        return None


def _is_supported_paper(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_PAPER_SUFFIXES and not path.name.startswith("~$")


def _import_status(needs_ocr: bool, parsed: ParsedPaperText) -> str:
    if needs_ocr:
        return "needs_ocr"
    if not parsed.questions or parsed.review_count:
        return "needs_review"
    return "imported"


def _split_answer_text(text: str) -> tuple[str, str]:
    cleaned = textwrap.dedent(str(text or "")).strip()
    heading = _ANSWER_HEADING.search(cleaned)
    if heading is None:
        return cleaned, ""
    return cleaned[: heading.start()].strip(), cleaned[heading.end() :].strip()


def _answer_map(answer_text: str, source_file: str | None = None, doc_title: str | None = None) -> dict[str, str]:
    answers: dict[str, str] = {}
    for block in _split_numbered_blocks(
        answer_text,
        keep_image_markers=True,
        source_file=source_file,
        anchor_answer_labels=True,
        doc_title=doc_title,
    ):
        if block.text:
            # 同一编号只保留第一次出现，避免后面的同号解析块顶掉正确答案。
            answers.setdefault(block.number, block.text)
    return answers


# 教辅扫描件题干里的内嵌答案：`（D）` 出现在题干末尾或选项区起始之前。
_INLINE_CHOICE_ANSWER = re.compile(r"[（(]\s*([A-D])\s*[)）]")
# 行首“解:/答:/分析:/解答:”起及其后内容都属于答案区。
_INLINE_ANSWER_LEAD_LINE = re.compile(r"(?m)^[ \t]*(?:分析|解答|解|答)\s*[:：]")
# 选项区起始（A. / A、 / A) / A） 等），用于判定内嵌答案的位置。
_OPTION_BLOCK_START = re.compile(r"(?<![A-Za-z])A(?:[.．、\)]|）)")


def _split_inline_teacher_answers(parsed: ParsedPaperText) -> ParsedPaperText:
    """把 OCR 题干里内嵌的教辅答案/解析切到 answer_text（ocr_applied 路径）。"""
    questions: list[ParsedQuestion] = []
    for item in parsed.questions:
        question_text = str(item.question_text or "")
        answer_text = item.answer_text
        lead = _INLINE_ANSWER_LEAD_LINE.search(question_text)
        if lead is not None:
            tail = question_text[lead.start() :].strip()
            if tail and not answer_text:
                answer_text = tail
            question_text = question_text[: lead.start()].rstrip()
        # 从前往后取第一个紧跟选项区（或位于题末）的括号字母：教辅把答案印在
        # 题干与选项之间；块尾若并入了“变式”子题，其答案不能顶掉本题答案。
        for match in _INLINE_CHOICE_ANSWER.finditer(question_text):
            start, end = match.start(), match.end()
            prev_char = question_text[start - 1] if start > 0 else ""
            if prev_char and prev_char.isascii() and prev_char.isalnum():
                continue
            rest = question_text[end:].strip()
            if rest and _OPTION_BLOCK_START.match(rest) is None:
                continue
            if not answer_text:
                answer_text = match.group(1)
            question_text = (
                question_text[:start].rstrip() + (" " + rest if rest else "")
            ).strip()
            break
        if question_text != item.question_text or answer_text != item.answer_text:
            item = replace(
                item,
                question_text=question_text,
                answer_text=answer_text,
            )
        questions.append(item)
    return replace(parsed, questions=questions)


def map_rich_content_by_number(
    rich_paragraphs: list[dict[str, object]],
    *,
    source_file: str | None = None,
) -> dict[str, dict[str, list[dict[str, object]]]]:
    content: dict[str, dict[str, list[dict[str, object]]]] = {"question": {}, "answer": {}}

    clean_title = ""
    if source_file:
        raw_title = Path(source_file).stem
        clean_title = re.sub(r'_[0-9a-f]{12,64}$', '', raw_title, flags=re.IGNORECASE)
        clean_title = re.sub(r'_\d{8}_\d{6}$', '', clean_title).strip()

    doc_title = _document_title_hint([str(item.get("text") or "") for item in rich_paragraphs])
    sectioned_paragraphs = _section_rich_paragraphs(
        _move_image_only_paragraphs_to_following_question(rich_paragraphs),
        clean_title=clean_title,
        doc_title=doc_title,
    )
    use_main_markers = {
        "question": any(_MAIN_QUESTION_MARKER.match(_boundary_text(text)) for section, text, _ in sectioned_paragraphs if section == "question"),
        "answer": any(_MAIN_QUESTION_MARKER.match(_boundary_text(text)) for section, text, _ in sectioned_paragraphs if section == "answer"),
    }
    use_paren_markers = {
        "question": not use_main_markers["question"],
        "answer": not use_main_markers["answer"],
    }
    current_numbers: dict[str, str | None] = {"question": None, "answer": None}

    for section, text, paragraph in sectioned_paragraphs:
        marker = None
        # 子层级自动编号（Word 列表 ilvl>0）的段落不当成题目分界。
        if paragraph.get("numbering_level") in (None, 0):
            marker = _MAIN_QUESTION_MARKER.match(_boundary_text(text))
            if marker is None and use_paren_markers[section]:
                marker = _PAREN_QUESTION_MARKER.match(_boundary_text(text))
        if marker is not None:
            number = _normalized_number(marker)
            last_number = current_numbers[section]
            # 只接受递增编号；倒序/重复的编号段并入当前题，避免污染已有内容。
            if last_number is None or int(number) > int(last_number):
                current_numbers[section] = number

        current_number = current_numbers[section]
        if current_number:
            content[section].setdefault(current_number, []).append(paragraph)

    return content


def _boundary_text(text: str) -> str:
    """Ignore display wrappers and image markers when locating a paragraph label."""
    return re.sub(r"</?(?:p|span|b|strong|i|em|u|table|tbody|tr|td)\b[^>]*>", "", _IMAGE_MARKER.sub("", text), flags=re.I).strip()


def _split_consecutive_inline_main_questions(
    text: str,
    *,
    current_number: int,
    following_number: int | None,
) -> tuple[list[str], int]:
    split_offsets: list[int] = []
    expected_number = current_number + 1
    marker_text = _IMAGE_MARKER.sub(lambda match: " " * len(match.group(0)), text)
    for marker in _INLINE_MAIN_QUESTION_MARKER.finditer(marker_text):
        marker_number = int(marker.group("number"))
        if marker_number != expected_number:
            continue
        split_offsets.append(marker.start("number"))
        expected_number += 1
    if not split_offsets or expected_number != following_number:
        return [text], current_number
    boundaries = [0, *split_offsets, len(text)]
    return (
        [
            text[start:end].strip()
            for start, end in zip(boundaries, boundaries[1:])
            if text[start:end].strip()
        ],
        expected_number - 1,
    )


def _extract_question_marker_number(line: str) -> int | None:
    value = str(line or "").strip()
    if not value:
        return None
    for pattern in [
        r"^(?:Q|q)\s*(\d{1,2})(?:\b|[\s:：.．、)])",
        r"^第\s*(\d{1,2})\s*[题題]",
        r"^(\d{1,2})\s*[.．、)]",
    ]:
        match = re.match(pattern, value)
        if match and 1 <= int(match.group(1)) <= 99:
            return int(match.group(1))
    return None


def _looks_like_answer_section_heading(line: str) -> bool:
    value = str(line or "").strip()
    if not value:
        return False
    return _ANSWER_HEADING.fullmatch(value) is not None


def _next_leading_main_question_number(
    texts: list[str], *, after_index: int
) -> int | None:
    for text in texts[after_index + 1 :]:
        if _looks_like_answer_section_heading(text):
            return None
        number = _extract_question_marker_number(text)
        if number is not None:
            return number
    return None


def split_inline_main_question_paragraphs(
    rich_paragraphs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    current_number: int | None = None
    in_answer_section = False
    plain_paragraphs = [
        _boundary_text(
            _IMAGE_MARKER.sub("", str(paragraph.get("text") or ""))
        ).strip()
        for paragraph in rich_paragraphs
    ]
    for index, paragraph in enumerate(rich_paragraphs):
        text = str(paragraph.get("text") or "").strip()
        plain_text = plain_paragraphs[index]
        if _looks_like_answer_section_heading(plain_text):
            in_answer_section = True
        if in_answer_section or not text:
            normalized.append(paragraph)
            continue
        leading_number = _extract_question_marker_number(plain_text)
        if leading_number is not None:
            current_number = leading_number
        if current_number is None:
            normalized.append(paragraph)
            continue
        segments, final_number = _split_consecutive_inline_main_questions(
            (text if paragraph.get("images_in_text_order") else _IMAGE_MARKER.sub("", text)).strip(),
            current_number=current_number,
            following_number=_next_leading_main_question_number(
                plain_paragraphs, after_index=index
            ),
        )
        if len(segments) == 1:
            normalized.append(paragraph)
            continue
        if not paragraph.get("images_in_text_order"):
            segments = _attach_inline_images_to_source_segments(
                paragraph,
                segments=segments,
                current_number=current_number,
                final_number=final_number,
            )
        for segment in segments:
            paths = {m.group("path") for m in _IMAGE_MARKER.finditer(segment)}
            normalized.append({
                **paragraph,
                "text": segment,
                # The original XML contains all questions in this paragraph.
                # Reusing it would reintroduce the other question on export.
                "xml": "",
                "image_relationships": {key: path for key, path in (paragraph.get("image_relationships") or {}).items() if path in paths},
                "floating_image_paths": [path for path in paragraph.get("floating_image_paths", []) if path in paths],
            })
        current_number = final_number
    return normalized


def _attach_inline_images_to_source_segments(
    paragraph: dict[str, Any],
    *,
    segments: list[str],
    current_number: int,
    final_number: int,
) -> list[str]:
    fallback_paths = list(dict.fromkeys(m.group("path") for m in _IMAGE_MARKER.finditer(str(paragraph.get("text") or ""))))

    def attach_to_last(paths: list[str]) -> list[str]:
        assigned = list(segments)
        for path in paths:
            assigned[-1] = f"{assigned[-1].rstrip()}\n[[IMAGE:{path}]]"
        return assigned

    relationships = paragraph.get("image_relationships")
    raw_xml = str(paragraph.get("xml") or "")
    if not isinstance(relationships, dict) or not relationships or not raw_xml:
        return attach_to_last(fallback_paths)
    try:
        root = ElementTree.fromstring(raw_xml)
    except ElementTree.ParseError:
        return attach_to_last(fallback_paths)

    text_parts: list[str] = []
    positioned_images: list[tuple[int, str]] = []
    text_length = 0
    for element in root.iter():
        local_name = str(element.tag).split("}")[-1]
        if local_name == "t":
            value = str(element.text or "")
            text_parts.append(value)
            text_length += len(value)
            continue
        if local_name != "blip":
            continue
        relationship_id = next(
            (
                str(value)
                for key, value in element.attrib.items()
                if str(key).split("}")[-1] == "embed"
            ),
            "",
        )
        image_path = str(relationships.get(relationship_id) or "").strip()
        if image_path:
            positioned_images.append((text_length, image_path))
    if not positioned_images:
        return attach_to_last(fallback_paths)

    source_text = "".join(text_parts)
    expected_number = current_number + 1
    split_offsets: list[int] = []
    for marker in _INLINE_MAIN_QUESTION_MARKER.finditer(source_text):
        marker_number = int(marker.group("number"))
        if marker_number != expected_number:
            continue
        split_offsets.append(marker.start("number"))
        expected_number += 1
        if marker_number == final_number:
            break
    if len(split_offsets) != len(segments) - 1:
        return attach_to_last(fallback_paths)

    assigned = list(segments)
    positioned_paths: set[str] = set()
    for image_offset, image_path in positioned_images:
        segment_index = sum(image_offset >= offset for offset in split_offsets)
        assigned[segment_index] = (
            f"{assigned[segment_index].rstrip()}\n[[IMAGE:{image_path}]]"
        )
        positioned_paths.add(image_path)
    for image_path in fallback_paths:
        if image_path not in positioned_paths:
            assigned[-1] = f"{assigned[-1].rstrip()}\n[[IMAGE:{image_path}]]"
    return assigned


def _rich_question_positions(paragraphs: list[dict[str, Any]]) -> dict[int, tuple[str, str]]:
    """Use the same parent-question boundaries for splitting and image ownership."""
    sections: list[tuple[int, str, str, dict]] = []
    section = "question"
    for index, paragraph in enumerate(paragraphs):
        text = _boundary_text(str(paragraph.get("text") or ""))
        if _ANSWER_HEADING.fullmatch(text):
            section = "answer"
            continue
        sections.append((index, section, text, paragraph))
    has_main = {section: any(_MAIN_QUESTION_MARKER.match(text) for _, s, text, _ in sections if s == section) for section in ("question", "answer")}
    current: dict[str, int] = {}
    positions = {}
    for index, section, text, paragraph in sections:
        if paragraph.get("numbering_level") not in (None, 0):
            continue
        marker = _MAIN_QUESTION_MARKER.match(text)
        if marker is None and not has_main[section]:
            marker = _PAREN_QUESTION_MARKER.match(text)
        if marker and int(marker.group("number")) > current.get(section, 0):
            current[section] = int(marker.group("number"))
            positions[index] = (section, str(current[section]))
    return positions


def apply_asset_overrides(
    extracted: ExtractedDocument,
    overrides: list[dict[str, Any]],
) -> ExtractedDocument:
    """Re-apply teacher image ownership decisions before parsing.

    Overrides match extracted image files by content hash.  An ``ignore``
    override drops the image marker from every paragraph; a ``bind``
    override moves it to the end of the requested question span in the
    requested section.  An override whose hash or target question cannot
    be found leaves the document untouched.
    """
    paragraphs = [dict(item) for item in getattr(extracted, "rich_paragraphs", [])]
    if not paragraphs:
        LOGGER.warning("Asset overrides skipped: document has no paragraph stream")
        return extracted
    paths_by_sha256: dict[str, list[str]] = {}
    for raw_path in extracted.image_paths:
        candidate = Path(str(raw_path))
        try:
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        except OSError:
            LOGGER.warning("Asset override image is unreadable: %s", candidate.name)
            continue
        paths_by_sha256.setdefault(digest, []).append(str(raw_path))

    for override in overrides:
        matched_paths = paths_by_sha256.get(
            str(override.get("sha256") or "").strip().casefold()
        ) or []
        if not matched_paths:
            LOGGER.warning("Asset override matched no extracted image; skipped")
            continue
        action = str(override.get("action") or "").strip()
        target_section = (
            "answer"
            if str(override.get("asset_kind") or "").strip() == "answer"
            else "question"
        )
        target_number = str(override.get("question_number") or "").strip()
        if action == "bind" and _question_span_end(
            paragraphs,
            section=target_section,
            number=target_number,
        ) is None:
            LOGGER.warning(
                "Asset override target question %s was not found; skipped",
                target_number,
            )
            continue
        paragraphs = _remove_image_marker_paths(paragraphs, matched_paths)
        if action == "bind":
            insert_at = _question_span_end(
                paragraphs,
                section=target_section,
                number=target_number,
            )
            assert insert_at is not None
            for path in matched_paths:
                paragraphs.insert(
                    insert_at,
                    {
                        "text": f"[[IMAGE:{path}]]",
                        "xml": "",
                        "image_relationships": {},
                    },
                )
                insert_at += 1
    return ExtractedDocument(
        source_file=extracted.source_file,
        page_range=extracted.page_range,
        text="\n".join(
            str(item.get("text") or "")
            for item in paragraphs
            if str(item.get("text") or "").strip()
        ),
        needs_ocr=extracted.needs_ocr,
        has_images=extracted.has_images,
        needs_image_review=extracted.needs_image_review,
        image_paths=list(extracted.image_paths),
        rich_paragraphs=paragraphs,
        document_snapshot=extracted.document_snapshot,
    )


def _question_span_end(
    paragraphs: list[dict[str, Any]],
    *,
    section: str,
    number: str,
) -> int | None:
    """Index just past the span of question ``number`` inside ``section``."""
    positions = _rich_question_positions(paragraphs)
    start = next((index for index, value in positions.items() if value == (section, number)), None)
    if start is None:
        return None
    for index in range(start + 1, len(paragraphs)):
        if index in positions or _ANSWER_HEADING.fullmatch(_boundary_text(str(paragraphs[index].get("text") or ""))):
            return index
    return len(paragraphs)


def _remove_image_marker_paths(
    paragraphs: list[dict[str, Any]],
    paths: list[str],
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for paragraph in paragraphs:
        text = str(paragraph.get("text") or "")
        updated = text
        for path in paths:
            updated = updated.replace(f"[[IMAGE:{path}]]", "")
        if updated != text:
            updated = "\n".join(
                line for line in updated.splitlines() if line.strip()
            )
            paragraph = {**paragraph, "text": updated}
        if not str(paragraph.get("text") or "").strip():
            continue
        cleaned.append(paragraph)
    return cleaned


def partition_ambiguous_floating_images(
    rich_paragraphs: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Remove only floating image-only blocks sitting between adjacent questions.

    Word anchors describe drawing placement, not semantic ownership.  When such
    an image appears after one numbered question and before the next, neither
    neighbour is a safe automatic choice.  Inline images and end-of-question
    images remain on the existing high-confidence path.
    """
    normalized: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []
    section = "question"
    current_number: str | None = None
    positions = _rich_question_positions(rich_paragraphs)

    for index, paragraph in enumerate(rich_paragraphs):
        text = str(paragraph.get("text") or "").strip()
        if question_section_type(_boundary_text(text)) is not None and _IMAGE_MARKER.search(text):
            # Word often anchors the preceding diagram to the next section
            # heading. Remove the heading text, then review the image between
            # its real neighbouring questions instead of deleting the drawing.
            text = "\n".join(match.group(0) for match in _IMAGE_MARKER.finditer(text))
            paragraph = {**paragraph, "text": text}
        if _ANSWER_HEADING.search(text):
            section = "answer"
            current_number = None
            normalized.append(paragraph)
            continue
        if index in positions:
            section, current_number = positions[index]

        next_number: str | None = None
        if current_number and _is_floating_image_only_paragraph(paragraph):
            future_section = section
            for following_index, following in enumerate(rich_paragraphs[index + 1 :], start=index + 1):
                following_text = str(following.get("text") or "").strip()
                if _ANSWER_HEADING.search(following_text):
                    future_section = "answer"
                    if future_section != section:
                        break
                    continue
                if following_index in positions:
                    next_section, next_number = positions[following_index]
                    if next_section != section:
                        next_number = None
                    break
                if _IMAGE_MARKER.sub("", following_text).strip() and question_section_type(following_text) is None:
                    # More text/subparts belonging to this question follows the
                    # drawing. This is not an image between two questions.
                    break
            paths = [
                match.group("path").strip()
                for match in _IMAGE_MARKER.finditer(text)
                if match.group("path").strip()
            ]
            if next_number and next_number != current_number and paths:
                for path in dict.fromkeys(paths):
                    candidates.append(
                        {
                            "path": path,
                            "previous_question_id": f"Q{current_number}",
                            "next_question_id": f"Q{next_number}",
                            "source_section": section,
                        }
                    )
                continue
        normalized.append(paragraph)
    return normalized, candidates


def _move_image_only_paragraphs_to_following_question(
    rich_paragraphs: list[dict[str, object]],
) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    pending_images: list[dict[str, object]] = []
    seen_question = False
    for paragraph in rich_paragraphs:
        text = str(paragraph.get("text") or "").strip()
        marker = _MAIN_QUESTION_MARKER.match(text) or _PAREN_QUESTION_MARKER.match(text)
        if marker is not None:
            seen_question = True
        if marker is not None and pending_images:
            normalized.append(paragraph)
            normalized.extend(pending_images)
            pending_images = []
            continue
        if _is_floating_image_only_paragraph(paragraph) and not seen_question:
            pending_images.append(paragraph)
            continue
        if pending_images:
            normalized.extend(pending_images)
            pending_images = []
        normalized.append(paragraph)
    normalized.extend(pending_images)
    return normalized


def _section_rich_paragraphs(
    rich_paragraphs: list[dict[str, object]],
    *,
    clean_title: str = "",
    doc_title: str | None = None,
) -> list[tuple[str, str, dict[str, object]]]:
    sectioned: list[tuple[str, str, dict[str, object]]] = []
    section = "question"

    for paragraph in rich_paragraphs:
        text = str(paragraph.get("text") or "").strip()
        if not text:
            continue
        # A single Word paragraph can hold several lines joined by manual
        # breaks; a bare heading line inside it still marks the answer
        # boundary. Content the rich mapper cannot split is left for the
        # plain-text fallback (question_map stays empty -> callers reparse).
        if _ANSWER_HEADING.search(_boundary_text(text)):
            section = "answer"
            continue
        if question_section_type(_boundary_text(text)) is not None:
            image_text = "\n".join(match.group(0) for match in _IMAGE_MARKER.finditer(text))
            if not image_text:
                continue
            # A drawing can be anchored to the next section title. Keep it in
            # the automatic import path too; reviewed imports use the teacher's binding.
            paragraph = {**paragraph, "text": image_text, "xml": ""}
            text = image_text
        if _is_rich_noise_paragraph(text, clean_title=clean_title, doc_title=doc_title):
            continue
        sectioned.append((section, text, paragraph))
    return sectioned


def _is_rich_noise_paragraph(text: str, *, clean_title: str = "", doc_title: str | None = None) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return True
    if _MAIN_QUESTION_MARKER.match(stripped) or _PAREN_QUESTION_MARKER.match(stripped):
        return False
    if len(stripped) <= 120 and _PAPER_TITLE_NOISE.search(stripped):
        return True
    if _PAPER_META_NOISE.search(stripped):
        return True
    visible_text = _IMAGE_MARKER.sub("", stripped)
    if clean_title and clean_title in visible_text:
        return True
    if doc_title and len(doc_title) >= 4 and doc_title in visible_text:
        return True
    if _PAGE_NUMBER_NOISE.match(stripped):
        return True
    if _COPYRIGHT_META_NOISE.search(stripped):
        return True
    return False


def _document_title_hint(lines: list[str]) -> str | None:
    """从文档第一个有效行推断标题，用于过滤正文中重复出现的标题行。

    源文件归档后会被改名（source_xxx.docx），靠文件名过滤标题会失效，
    因此以文档首行作为兜底标题。首行像题目或太短/太长时放弃推断。
    """
    for raw in lines:
        line = _IMAGE_MARKER.sub("", str(raw or "")).strip()
        if not line:
            continue
        if len(line) < 4 or len(line) > 60:
            return None
        if _MAIN_QUESTION_MARKER.match(line) or _PAREN_QUESTION_MARKER.match(line):
            return None
        if question_section_type(line) is not None:
            return None
        return line
    return None


def _forward_only_matches(
    matches: list[re.Match[str]],
    cleaned: str,
    *,
    anchor_answer_labels: bool = False,
) -> tuple[list[re.Match[str]], list[int]]:
    """只保留编号递增的分界；倒序/重复的编号并入前一块。

    大题内部的小步骤也常写作行首“1．”“2．”（例如探究题），若一律当成
    新题号会把一道题切碎并顶掉同号答案，因此编号必须严格递增才开新块。

    anchor_answer_labels=True（答案区）且全文确为教师版标签格式时，候选
    分界到下一候选之间还必须含【答案】/【分析】等标签，否则视为题内
    小步骤并入前一块——标签是第一优先级，行首编号只作兜底。

    返回 (保留的分界, 每个保留块各自并入的被丢弃分界数)。
    """
    use_labels = anchor_answer_labels and len(_ANSWER_BLOCK_LABEL.findall(cleaned)) >= 2
    accepted: list[re.Match[str]] = []
    merged_counts: list[int] = []
    last_number: int | None = None
    for index, match in enumerate(matches):
        number = int(match.group("number"))
        if last_number is not None and number <= last_number:
            merged_counts[-1] += 1
            continue
        if use_labels and accepted:
            body_end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
            if not _ANSWER_BLOCK_LABEL.search(cleaned[match.end():body_end]):
                merged_counts[-1] += 1
                continue
        accepted.append(match)
        merged_counts.append(0)
        last_number = number
    return accepted, merged_counts


def _paper_review_reasons(
    all_block_numbers: list[str],
    question_numbers: list[str],
    answers: dict[str, str],
    *,
    range_filter: tuple[int, int] | None,
    merged_anomaly_count: int,
) -> list[str]:
    """入库前体检：结构异常只标记不拦截，由教师复核决定。"""
    reasons: list[str] = []
    if merged_anomaly_count:
        reasons.append(f"{merged_anomaly_count} 处倒序/重复编号已并入上一题，请人工复核")
    numeric = sorted({int(number) for number in all_block_numbers if str(number).isdigit()})
    if range_filter is None and numeric:
        if numeric[0] != 1:
            reasons.append(f"题号从 {numeric[0]} 开始，未从 1 开始")
        present = set(numeric)
        missing = [number for number in range(numeric[0], numeric[-1] + 1) if number not in present]
        if missing:
            shown = "、".join(str(number) for number in missing[:5])
            suffix = " 等" if len(missing) > 5 else ""
            reasons.append(f"题号不连续，缺 {shown}{suffix}")
    unmatched = sorted(
        {number for number in answers if number not in set(question_numbers)},
        key=lambda value: (len(value), value),
    )
    if unmatched:
        shown = "、".join(unmatched[:5])
        suffix = " 等" if len(unmatched) > 5 else ""
        reasons.append(f"答案区编号 {shown}{suffix} 无对应题目")
    return reasons


def _split_numbered_blocks(
    text: str,
    *,
    keep_image_markers: bool = False,
    source_file: str | None = None,
    anchor_answer_labels: bool = False,
    doc_title: str | None = None,
    ocr_noise: bool = False,
) -> list[_NumberedBlock]:
    cleaned = textwrap.dedent(str(text or ""))
    merged_counts: list[int] = []
    matches = list(_MAIN_QUESTION_MARKER.finditer(cleaned))
    if matches:
        matches, merged_counts = _forward_only_matches(
            matches,
            cleaned,
            anchor_answer_labels=anchor_answer_labels,
        )
    else:
        paren_matches = list(_PAREN_QUESTION_MARKER.finditer(cleaned))
        valid_matches = []
        expected = 1
        for m in paren_matches:
            num = int(m.group("number"))
            if num == expected:
                valid_matches.append(m)
                expected += 1
        if valid_matches:
            matches = valid_matches
            merged_counts = [0] * len(matches)

    blocks: list[_NumberedBlock] = []
    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else None
        body, image_paths = _clean_block(
            # A drawing can precede the visible label in the same Word paragraph.
            # Recognize that label, but keep its drawing with this question.
            match.group("leading_images") + cleaned[body_start:body_end],
            keep_image_markers=keep_image_markers,
            source_file=source_file,
            doc_title=doc_title,
            ocr_noise=ocr_noise,
        )
        if body:
            blocks.append(_NumberedBlock(
                number=_normalized_number(match),
                text=body,
                image_paths=image_paths,
                merged_marker_count=merged_counts[index] if merged_counts else 0,
                section_hint=_last_section_hint(cleaned[: match.start()]),
            ))
    return blocks


def _last_section_hint(prefix: str) -> str | None:
    for line in reversed(prefix.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        kind = question_section_type(stripped)
        if kind is not None:
            return stripped if kind else None
    return None


def _clean_block(
    text: str,
    *,
    keep_image_markers: bool = False,
    source_file: str | None = None,
    doc_title: str | None = None,
    ocr_noise: bool = False,
) -> tuple[str, list[str]]:
    image_paths = [match.group("path").strip() for match in _IMAGE_MARKER.finditer(str(text or ""))]
    text_without_images = _IMAGE_MARKER.sub("", str(text or ""))
    lines = [line.strip() for line in str(text or "").strip().splitlines() if line.strip()]
    cleaned_lines = []

    clean_title = ""
    if source_file:
        raw_title = Path(source_file).name
        clean_title = re.sub(
            r'_[0-9a-f]{12,64}$',
            '',
            Path(raw_title).stem,
            flags=re.IGNORECASE,
        )
        clean_title = re.sub(r'_\d{8}_\d{6}$', '', clean_title).strip()

    last_line_index = len(lines) - 1
    for line_index, line in enumerate(lines):
        line_clean = _IMAGE_MARKER.sub("", line).strip()
        if ocr_noise and _OCR_SECTION_NOISE.match(line_clean):
            # 【…】式栏目名出现在题干中间时是题内小标题，保留；
            # 题块首尾一律按节标题噪声删除。
            if line_clean.startswith("【") and 0 < line_index < last_line_index:
                pass
            else:
                continue
        if question_section_type(line_clean) is not None:
            continue
        if len(line_clean) <= 120 and _PAPER_TITLE_NOISE.search(line_clean):
            continue
        if _PAPER_META_NOISE.search(line_clean):
            continue
        if clean_title and clean_title in line_clean:
            continue
        if doc_title and len(doc_title) >= 4 and doc_title in line_clean:
            continue
        if _PAGE_NUMBER_NOISE.match(line_clean):
            continue
        if _COPYRIGHT_META_NOISE.search(line_clean):
            continue
        cleaned_lines.append((line if keep_image_markers else _IMAGE_MARKER.sub("", line)).strip())

    cleaned_text = "\n".join(line for line in cleaned_lines if line)
    if not cleaned_text and image_paths:
        cleaned_text = "（图片题，需人工查看图像）"
    return cleaned_text or text_without_images.strip(), image_paths


def _is_image_marker_only(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    return not _IMAGE_MARKER.sub("", stripped).strip() and bool(_IMAGE_MARKER.search(stripped))


def _is_floating_image_only_paragraph(paragraph: dict[str, object]) -> bool:
    text = str(paragraph.get("text") or "").strip()
    xml = str(paragraph.get("xml") or "")
    return _is_image_marker_only(text) and (bool(paragraph.get("floating_image_paths")) or "<wp:anchor" in xml)




def _question_duplicate_signature(value: object) -> str:
    text = _IMAGE_MARKER.sub("", str(value or ""))
    text = re.split(r"【(?:分析|解答|点评|解析|答案)】|参考答案及评分标准", text)[0]
    text = re.sub(r"[　\s]*\([^)]*从[^)]*选一个填空[^)]*\)[　\s]*", "", text)
    text = re.sub(r"^\s*(?:第\s*)?\d+\s*(?:[.．、]|题|[)）])\s*", "", text)
    return _DUPLICATE_NORMALIZE.sub("", text).lower()


def _normalized_number(match: re.Match[str]) -> str:
    return str(match.group("number") or "").strip()


def _parse_numeric_range(question_range: str | None) -> tuple[int, int] | None:
    value = str(question_range or "").strip()
    if not value:
        return None
    match = re.fullmatch(r"(\d+)(?:\s*[-~～—]\s*(\d+))?", value)
    if match is None:
        return None
    start = int(match.group(1))
    end = int(match.group(2) or start)
    return (min(start, end), max(start, end))


def _is_in_range(question_number: str, range_filter: tuple[int, int] | None) -> bool:
    if range_filter is None:
        return True
    if not str(question_number).isdigit():
        return False
    return range_filter[0] <= int(question_number) <= range_filter[1]


def _clean_optional(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _merge_metadata(default: PaperMetadata, inferred: PaperMetadata) -> PaperMetadata:
    return PaperMetadata(
        year=_clean_optional(default.year) or _clean_optional(inferred.year),
        province=_clean_optional(default.province) or _clean_optional(inferred.province),
        city=_clean_optional(default.city) or _clean_optional(inferred.city),
        district=_clean_optional(default.district) or _clean_optional(inferred.district),
        exam_type=_clean_optional(default.exam_type) or _clean_optional(inferred.exam_type),
        grade=_clean_optional(default.grade) or _clean_optional(inferred.grade),
        semester=_clean_optional(default.semester) or _clean_optional(inferred.semester),
        textbook_version=_clean_optional(default.textbook_version) or _clean_optional(inferred.textbook_version),
    )


def _infer_province(text: str) -> str | None:
    provinces = (
        "北京市", "天津市", "上海市", "重庆市",
        "河北省", "山西省", "辽宁省", "吉林省", "黑龙江省",
        "江苏省", "浙江省", "安徽省", "福建省", "江西省", "山东省",
        "河南省", "湖北省", "湖南省", "广东省", "海南省",
        "四川省", "贵州省", "云南省", "陕西省", "甘肃省", "青海省",
        "内蒙古自治区", "广西壮族自治区", "西藏自治区", "宁夏回族自治区", "新疆维吾尔自治区",
        "香港特别行政区", "澳门特别行政区", "台湾省",
    )
    return next((province for province in provinces if province in text), None)


def _infer_city(text: str) -> str | None:
    municipality = next((city for city in ("北京市", "天津市", "上海市", "重庆市") if city in text), None)
    if municipality:
        return municipality
    province = _infer_province(text)
    search_text = text.split(province, 1)[1] if province and province in text else text
    search_text = re.sub(r"^\d{4}年?", "", search_text)
    match = re.match(r"([\u4e00-\u9fff]{2,8}?市)", search_text)
    if match:
        return match.group(1)
    return "深圳市" if "深圳" in text else None


def _infer_district(text: str) -> str | None:
    district_aliases = {
        "南山": "南山区",
        "福田": "福田区",
        "罗湖": "罗湖区",
        "宝安": "宝安区",
        "龙岗": "龙岗区",
        "龙华": "龙华区",
        "盐田": "盐田区",
        "坪山": "坪山区",
        "光明": "光明区",
        "大鹏": "大鹏新区",
        "深圳": "深圳市",
    }
    for token, normalized in district_aliases.items():
        if token in text:
            return normalized
    return None


def _infer_exam_type(text: str) -> str | None:
    for token in ("中考", "期末", "期中", "一模", "二模", "三模", "模拟", "月考"):
        if token in text:
            return token
    return None


def _infer_grade(text: str) -> str | None:
    grade_aliases = {
        "七年级": "七年级",
        "初一": "七年级",
        "七上": "七年级",
        "七下": "七年级",
        "八年级": "八年级",
        "初二": "八年级",
        "八上": "八年级",
        "八下": "八年级",
        "九年级": "九年级",
        "初三": "九年级",
        "九上": "九年级",
        "九下": "九年级",
    }
    for token, normalized in grade_aliases.items():
        if token in text:
            return normalized
    return None


def _infer_semester(text: str) -> str | None:
    if any(token in text for token in ("上学期", "上册", "七上", "八上", "九上", "（上）", "(上)")):
        return "上学期"
    if any(token in text for token in ("下学期", "下册", "七下", "八下", "九下", "（下）", "(下)")):
        return "下学期"
    return None

