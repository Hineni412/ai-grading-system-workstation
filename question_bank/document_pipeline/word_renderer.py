from __future__ import annotations

import re
import posixpath
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable
from xml.etree import ElementTree

from docx import Document as WordDocument
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

from .contracts import FormulaFallback, MathExpression, canonical_hash
from .math_omml import build_math_expression


_MATH_RUN = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$", re.DOTALL)


@dataclass(frozen=True, slots=True)
class WordStyleProfile:
    body_font: str = "宋体"
    body_font_ascii: str = "Times New Roman"
    body_size_pt: float = 10.5
    line_spacing: float = 1.15
    formula_size_pt: float = 11.0
    image_width_inches: float = 4.8
    keep_question_together: bool = True

    def __post_init__(self) -> None:
        if not str(self.body_font or "").strip() or not str(self.body_font_ascii or "").strip():
            raise ValueError("Word fonts must be non-empty")
        if not 6.0 <= float(self.body_size_pt) <= 48.0:
            raise ValueError("body_size_pt is outside the supported range")
        if not 6.0 <= float(self.formula_size_pt) <= 48.0:
            raise ValueError("formula_size_pt is outside the supported range")
        if not 0.8 <= float(self.line_spacing) <= 3.0:
            raise ValueError("line_spacing is outside the supported range")
        if not 0.2 <= float(self.image_width_inches) <= 8.0:
            raise ValueError("image_width_inches is outside the supported range")

    @property
    def sha256(self) -> str:
        return canonical_hash(asdict(self))

    @classmethod
    def from_export_config(cls, config: object | None) -> "WordStyleProfile":
        if config is None:
            return cls()
        return cls(
            body_font=str(getattr(config, "body_font", "宋体")),
            body_font_ascii=str(getattr(config, "body_font_ascii", "Times New Roman")),
            body_size_pt=float(getattr(config, "body_size_pt", 10.5)),
            line_spacing=float(getattr(config, "line_spacing", 1.15)),
        )


class SharedWordQuestionRenderer:
    """One semantic math renderer shared by ordinary and personalized papers."""

    def __init__(
        self,
        *,
        style: WordStyleProfile | None = None,
        asset_resolver: Callable[[str], Path | None] | None = None,
    ) -> None:
        self.style = style or WordStyleProfile()
        self.asset_resolver = asset_resolver

    def add_text(
        self,
        document,
        text: str,
        *,
        question_id: str,
        expressions: Iterable[MathExpression] = (),
    ) -> tuple[FormulaFallback, ...]:
        expression_pool = list(expressions)
        fallbacks: list[FormulaFallback] = []
        lines = str(text or "").splitlines() or [""]
        expression_index = 0
        for line in lines:
            paragraph = document.add_paragraph()
            self._style_paragraph(paragraph)
            cursor = 0
            for match in _MATH_RUN.finditer(line):
                if match.start() > cursor:
                    self._add_text_run(paragraph, line[cursor : match.start()])
                raw_latex = match.group(1) if match.group(1) is not None else match.group(2)
                display = match.group(1) is not None
                expression = self._expression_for(
                    expression_pool,
                    raw_latex,
                    question_id=question_id,
                    index=expression_index,
                )
                expression_index += 1
                target = paragraph
                if display:
                    target = document.add_paragraph()
                    target.alignment = 1
                    self._style_paragraph(target)
                fallback = self._append_expression(
                    target,
                    expression,
                    question_id=question_id,
                )
                if fallback is not None:
                    fallbacks.append(fallback)
                cursor = match.end()
            if cursor < len(line):
                self._add_text_run(paragraph, line[cursor:])
        return tuple(fallbacks)

    def add_images(self, document, image_paths: Iterable[str]) -> None:
        for value in image_paths:
            resolved = self.asset_resolver(str(value)) if self.asset_resolver else Path(str(value))
            if resolved is None or not resolved.is_file():
                document.add_paragraph("[题图不可用]")
                continue
            try:
                document.add_picture(str(resolved), width=Inches(self.style.image_width_inches))
            except Exception:
                document.add_paragraph("[题图无法插入]")

    def add_to_paragraph(
        self,
        paragraph,
        text: str,
        *,
        question_id: str,
        expressions: Iterable[MathExpression] = (),
    ) -> tuple[FormulaFallback, ...]:
        """Render text and editable OMML into an existing paragraph or table cell."""

        pool = list(expressions)
        fallbacks: list[FormulaFallback] = []
        cursor = 0
        expression_index = 0
        value = str(text or "")
        self._style_paragraph(paragraph)
        for match in _MATH_RUN.finditer(value):
            if match.start() > cursor:
                self._add_text_run(paragraph, value[cursor : match.start()])
            raw_latex = match.group(1) if match.group(1) is not None else match.group(2)
            expression = self._expression_for(
                pool,
                raw_latex,
                question_id=question_id,
                index=expression_index,
            )
            expression_index += 1
            fallback = self._append_expression(
                paragraph,
                expression,
                question_id=question_id,
            )
            if fallback is not None:
                fallbacks.append(fallback)
            cursor = match.end()
        if cursor < len(value):
            self._add_text_run(paragraph, value[cursor:])
        return tuple(fallbacks)

    def _expression_for(
        self,
        pool: list[MathExpression],
        raw_latex: str,
        *,
        question_id: str,
        index: int,
    ) -> MathExpression:
        clean = str(raw_latex or "").strip()
        for expression in pool:
            if expression.restricted_latex.strip("$").strip() == clean:
                return expression
        return build_math_expression(
            expression_id=f"{_safe_question_id(question_id)}-math-{index + 1}",
            source=clean,
        )

    def _append_expression(
        self,
        paragraph,
        expression: MathExpression,
        *,
        question_id: str,
    ) -> FormulaFallback | None:
        if expression.omml:
            try:
                paragraph._p.append(parse_xml(expression.omml))  # noqa: SLF001
                return None
            except Exception:
                reason = "validated OMML could not be inserted"
        else:
            reason = expression.validation_error or "expression is not supported"
        fallback_path = expression.fallback_asset
        resolved = (
            self.asset_resolver(fallback_path)
            if fallback_path and self.asset_resolver
            else Path(fallback_path) if fallback_path else None
        )
        if resolved is not None and resolved.is_file():
            try:
                paragraph.add_run().add_picture(
                    str(resolved), width=Inches(min(3.0, self.style.image_width_inches))
                )
            except Exception:
                paragraph.add_run(f"${expression.restricted_latex}$")
                reason = f"{reason}; fallback image could not be inserted"
        else:
            paragraph.add_run(f"${expression.restricted_latex}$")
            if fallback_path:
                reason = f"{reason}; fallback image is unavailable"
        return FormulaFallback(
            question_id=_safe_question_id(question_id),
            expression_id=expression.expression_id,
            reason=reason,
            asset_path=fallback_path,
        )

    def _style_paragraph(self, paragraph) -> None:
        paragraph.paragraph_format.line_spacing = self.style.line_spacing
        paragraph.paragraph_format.widow_control = True
        if self.style.keep_question_together:
            paragraph.paragraph_format.keep_together = True

    def _add_text_run(self, paragraph, value: str) -> None:
        if not value:
            return
        run = paragraph.add_run(value)
        run.font.name = self.style.body_font_ascii
        run._element.rPr.rFonts.set(qn("w:eastAsia"), self.style.body_font)
        run.font.size = Pt(self.style.body_size_pt)


def validate_docx(path: str | Path) -> tuple[str, ...]:
    candidate = Path(path)
    errors: list[str] = []
    if not candidate.is_file() or candidate.suffix.casefold() != ".docx":
        return ("DOCX artifact is missing",)
    try:
        with zipfile.ZipFile(candidate) as archive:
            names = set(archive.namelist())
            for required in ("[Content_Types].xml", "word/document.xml"):
                if required not in names:
                    errors.append(f"DOCX member is missing: {required}")
            document_xml = archive.read("word/document.xml") if "word/document.xml" in names else b""
            if b"<m:oMath" in document_xml and b"officeDocument/2006/math" not in document_xml:
                errors.append("OMML namespace is missing")
            bad_members = [name for name in names if name.startswith("/") or ".." in Path(name).parts]
            if bad_members:
                errors.append("DOCX contains an unsafe member path")
            for relationship_name in sorted(
                name for name in names if name.endswith(".rels")
            ):
                try:
                    root = ElementTree.fromstring(archive.read(relationship_name))
                except ElementTree.ParseError:
                    errors.append(f"DOCX relationship XML is invalid: {relationship_name}")
                    continue
                relationship_path = PurePosixPath(relationship_name)
                owner_directory = relationship_path.parent.parent.as_posix()
                for relationship in root:
                    if relationship.attrib.get("TargetMode") == "External":
                        continue
                    target = relationship.attrib.get("Target", "")
                    resolved = posixpath.normpath(
                        posixpath.join(owner_directory, target)
                    )
                    escapes_package = (
                        resolved == ".."
                        or resolved.startswith("../")
                        or resolved.startswith("/")
                    )
                    package_target = resolved.removeprefix("./")
                    if escapes_package or package_target not in names:
                        errors.append(
                            "DOCX relationship target is missing: "
                            f"{package_target or target}"
                        )
    except (OSError, zipfile.BadZipFile, KeyError):
        errors.append("DOCX artifact cannot be reopened")
    if not errors:
        try:
            WordDocument(candidate)
        except Exception:
            errors.append("DOCX artifact cannot be reopened by python-docx")
    return tuple(errors)


def _safe_question_id(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(value or "").strip()).strip("-")
    return (clean or "question")[:128]


__all__ = ["SharedWordQuestionRenderer", "WordStyleProfile", "validate_docx"]
