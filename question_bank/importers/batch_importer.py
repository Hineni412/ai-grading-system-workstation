from __future__ import annotations

import hashlib
import json
import logging
import re
import textwrap
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

from question_bank.database.schema import connect, initialize_database
from question_bank.importers.docx_importer import import_docx
from question_bank.importers.pdf_importer import import_pdf
from question_bank.importers.types import ExtractedDocument
from question_bank.services.duplicate_analysis_copy_service import exact_question_key, link_exact_duplicate
from question_bank.services.similarity_service import text_similarity
from question_bank.services.source_paper_archive_service import archive_source_paper
from question_bank.services.rich_content_service import save_question_rich_content
from question_bank.parsers.type_detector import (
    detect_essay_subtype,
    detect_question_type,
    split_legacy_question_type,
)


LOGGER = logging.getLogger(__name__)
SUPPORTED_PAPER_SUFFIXES = {".pdf", ".docx"}
_ANSWER_HEADING = re.compile(
    r"(?m)^[ \t]*(参考答案及评分标准|参考答案|答案与解析|答案解析|试题答案|答案|解析|评分标准)[ \t]*[:：]?.*$"
)
_MAIN_QUESTION_MARKER = re.compile(r"(?m)^[ \t]*(?P<number>\d{1,3})[ \t]*[.．、][ \t]*")
_PAREN_QUESTION_MARKER = re.compile(r"(?m)^[ \t]*[（(][ \t]*(?P<number>\d{1,3})[ \t]*[）)][ \t]*")
_SECTION_HEADING = re.compile(r"^[一二三四五六七八九十百]+[、.．][ \t]*\S+")
_IMAGE_MARKER = re.compile(r"\[\[IMAGE:(?P<path>.+?)\]\]")
_PAPER_TITLE_NOISE = re.compile(r"(学年|学校|集团|期末|期中|中考|模拟).{0,36}(数学|试卷)|数学试卷")
_PAPER_META_NOISE = re.compile(r"^(姓名|班级|考号|座号|准考证号|第\s*\d+\s*页|共\s*\d+\s*页)[：:\s]")
_DUPLICATE_NORMALIZE = re.compile(r"[\s\u3000，。！？；：、,.!?;:（）()【】\[\]{}《》<>“”\"'`~·…—_\-]+")

# New noise patterns
_PAGE_NUMBER_NOISE = re.compile(r"^[（(]\s*\d+\s*[）)]$|^第\s*\d+\s*页$|^共\s*\d+\s*页$")
_COPYRIGHT_META_NOISE = re.compile(r"声明\s*：\s*试题解析著作权属|著作权属|菁优网|发布日期\s*：|声明\s*:\s*试题解析著作权属")
# Teacher-edition answer blocks carry explicit labels; used as anchors when
# validating numbered boundaries inside the answer section.
_ANSWER_BLOCK_LABEL = re.compile(r"【(?:答案|分析|解答|点评|解析)】")


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
    ) -> None:
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
                "question_type": str(question_type or ""),
                "paper_title": str(paper_title or ""),
            }
        )


def _load_duplicate_index(db_path: Path, *, data_root: Path | None = None, connection: Any = None) -> _DuplicateIndex:
    with nullcontext(connection) if connection is not None else connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT q.id, q.question_text, q.answer_text, q.question_type, q.question_number, q.image_paths, q.has_images,
                   p.title AS paper_title
            FROM questions q
            LEFT JOIN papers p ON p.id = q.paper_id
            WHERE COALESCE(q.is_deleted, 0) = 0
              AND COALESCE(p.import_status, '') <> 'deleted'
            ORDER BY (SELECT COUNT(*) FROM question_tags t WHERE t.question_id=q.id) DESC, q.id
            """
        ).fetchall()
    index = _DuplicateIndex(data_root=data_root or _rich_content_root_for_database(db_path).parent.parent)
    for row in rows:
        index.add(
            int(row["id"]),
            question_text=row["question_text"],
            answer_text=row["answer_text"],
            question_type=row["question_type"],
            paper_title=row["paper_title"],
            question_number=row["question_number"], image_paths=row["image_paths"], has_images=bool(row["has_images"]),
        )
    return index


def _near_duplicate_hint(
    question: ParsedQuestion,
    index: _DuplicateIndex,
) -> dict[str, Any] | None:
    text = str(question.question_text or "")
    if not text.strip():
        return None
    new_type = str(question.question_type or "")
    best: dict[str, Any] | None = None
    for candidate in index.questions:
        candidate_type = str(candidate["question_type"])
        if new_type and candidate_type and candidate_type != new_type:
            continue
        candidate_text = str(candidate["question_text"])
        shorter = min(len(text), len(candidate_text))
        longer = max(len(text), len(candidate_text))
        if not shorter or shorter / longer < 0.6:
            continue
        score = text_similarity(text, candidate_text)
        if score < 0.7:
            continue
        if best is None or score > float(best["similarity"]):
            best = {
                "question_number": question.question_number,
                "matched_question_id": int(candidate["id"]),
                "matched_paper_title": str(candidate["paper_title"]),
                "similarity": score,
                "high": score >= 0.9,
            }
        if score >= 0.98:
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
) -> ParsedPaperText:
    # Document-level image flags stay in the signature for callers, but each
    # question now uses only the images extracted for that numbered block.
    _ = (has_images, image_paths)
    question_text, answer_text = _split_answer_text(text)
    doc_title = _document_title_hint(str(text or "").splitlines())
    answers = _answer_map(answer_text, source_file=source_file, doc_title=doc_title)
    range_filter = _parse_numeric_range(question_range)
    questions: list[ParsedQuestion] = []
    answer_match_count = 0
    all_block_numbers: list[str] = []
    merged_anomaly_count = 0

    for block in _split_numbered_blocks(question_text, source_file=source_file, doc_title=doc_title):
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
        q_type = override_type or detect_question_type(block.text)
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
                question_text=block.text,
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
                    duplicate_index=duplicate_index,
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
    duplicate_index: _DuplicateIndex | None = None,
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

    extracted = (
        _extract_paper(path, asset_root=asset_root)
        if asset_root is not None
        else _extract_paper(path)
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
        type_overrides=type_overrides,
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
    for item in parsed.questions:
        key = exact_question_key(asdict(item), data_root=duplicate_index.data_root, rich_content={
            "question_blocks": rich_content["question"].get(item.question_number, []),
            "answer_blocks": rich_content["answer"].get(item.question_number, []),
        })
        exact_keys[item.question_number] = key
        source_id = duplicate_index.exact.get(key) if key else None
        if source_id is not None:
            continue
        hint = _near_duplicate_hint(item, duplicate_index)
        if hint is not None:
            near_hints.append(hint)
    import_status = _import_status(extracted.needs_ocr, parsed)
    pending_rich_content: list[tuple[int, list[dict[str, object]], list[dict[str, object]]]] = []
    pending_analysis_reuse: list[tuple[int, int]] = []
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
        duplicate_index = _load_duplicate_index(db_path, data_root=duplicate_index.data_root, connection=conn)
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
                    int(item.needs_review),
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
            key = exact_keys[item.question_number]
            source_id = duplicate_index.exact.get(key) if key else None
            exact_source = (source_id, key) if source_id is not None else None
            if exact_source is not None:
                _link_exact_duplicate(
                    conn,
                    question_id=question_id,
                    source_id=exact_source[0],
                    signature=exact_source[1],
                )
                pending_analysis_reuse.append((question_id, exact_source[0]))
            elif key:
                duplicate_index.exact[key] = question_id
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

    analysis_reused_count = 0
    if duplicate_index is not None:
        # Rich content lives under <data_root>/question_bank/rich_content, so
        # the loader's data root is two levels up from the rich content root.
        rich_root = rich_content_root or _rich_content_root_for_database(db_path)
        loader_data_root = rich_root.parent.parent
        from question_bank.services.duplicate_analysis_copy_service import (
            copy_duplicate_analysis,
        )

        for question_id, source_id in pending_analysis_reuse:
            reused = copy_duplicate_analysis(
                db_path,
                source_question_id=source_id,
                target_question_id=question_id,
                data_root=loader_data_root,
            )
            if reused["evidence"] or reused["criteria"]:
                analysis_reused_count += 1
        for question_id, item in inserted_questions:
            duplicate_index.add(
                question_id,
                question_text=item.question_text,
                answer_text=item.answer_text,
                question_type=item.question_type,
                paper_title=source_title or path.stem,
                question_number=item.question_number, image_paths=item.image_paths, has_images=item.has_images,
            )

    return PaperImportFileResult(
        source_file=source_value,
        status=import_status,
        paper_id=paper_id,
        question_count=len(parsed.questions),
        answer_match_count=parsed.answer_match_count,
        review_count=parsed.review_count,
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


def _extract_paper(path: Path, *, asset_root: Path | None = None):
    if path.suffix.lower() == ".pdf":
        return import_pdf(path)
    return import_docx(path, asset_root=asset_root)


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
        "question": any(_MAIN_QUESTION_MARKER.match(text) for section, text, _ in sectioned_paragraphs if section == "question"),
        "answer": any(_MAIN_QUESTION_MARKER.match(text) for section, text, _ in sectioned_paragraphs if section == "answer"),
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
            marker = _MAIN_QUESTION_MARKER.match(text)
            if marker is None and use_paren_markers[section]:
                marker = _PAREN_QUESTION_MARKER.match(text)
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
    current_section = "question"
    inside_span = False
    for index, paragraph in enumerate(paragraphs):
        text = str(paragraph.get("text") or "").strip()
        if _ANSWER_HEADING.search(text):
            if inside_span:
                return index
            current_section = "answer"
            continue
        marker = _MAIN_QUESTION_MARKER.match(text) or _PAREN_QUESTION_MARKER.match(text)
        if marker is None:
            continue
        if inside_span:
            return index
        if current_section == section and _normalized_number(marker) == number:
            inside_span = True
    return len(paragraphs) if inside_span else None


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

    for index, paragraph in enumerate(rich_paragraphs):
        text = str(paragraph.get("text") or "").strip()
        if _ANSWER_HEADING.search(text):
            section = "answer"
            current_number = None
            normalized.append(paragraph)
            continue
        marker = _MAIN_QUESTION_MARKER.match(text) or _PAREN_QUESTION_MARKER.match(text)
        if marker is not None:
            current_number = _normalized_number(marker)

        next_number: str | None = None
        if current_number and _is_floating_image_only_paragraph(paragraph):
            future_section = section
            for following in rich_paragraphs[index + 1 :]:
                following_text = str(following.get("text") or "").strip()
                if _ANSWER_HEADING.search(following_text):
                    future_section = "answer"
                    if future_section != section:
                        break
                    continue
                following_marker = (
                    _MAIN_QUESTION_MARKER.match(following_text)
                    or _PAREN_QUESTION_MARKER.match(following_text)
                )
                if following_marker is not None:
                    next_number = _normalized_number(following_marker)
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
        if _ANSWER_HEADING.search(text):
            section = "answer"
            continue
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
        if _SECTION_HEADING.match(line):
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
            cleaned[body_start:body_end],
            keep_image_markers=keep_image_markers,
            source_file=source_file,
            doc_title=doc_title,
        )
        if body:
            blocks.append(_NumberedBlock(
                number=_normalized_number(match),
                text=body,
                image_paths=image_paths,
                merged_marker_count=merged_counts[index] if merged_counts else 0,
            ))
    return blocks


def _clean_block(
    text: str,
    *,
    keep_image_markers: bool = False,
    source_file: str | None = None,
    doc_title: str | None = None,
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

    for line in lines:
        line_clean = _IMAGE_MARKER.sub("", line).strip()
        if _SECTION_HEADING.match(line_clean):
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
    return _is_image_marker_only(text) and "<wp:anchor" in xml




def _link_exact_duplicate(
    conn: Any,
    *,
    question_id: int,
    source_id: int,
    signature: str,
) -> None:
    link_exact_duplicate(conn, question_id=question_id, source_id=source_id, signature=signature)


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
