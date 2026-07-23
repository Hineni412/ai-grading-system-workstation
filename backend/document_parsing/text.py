from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterator
from xml.etree import ElementTree

from docx import Document
from docx.document import Document as DocumentType
from docx.table import Table
from docx.text.paragraph import Paragraph


def extract_docx_text(file_bytes: bytes) -> str:
    """Extract stable plain text from paragraphs, tables, formulas and shapes."""
    document = Document(io.BytesIO(file_bytes))
    lines: list[str] = []
    seen_lines: set[str] = set()

    for block in _iter_doc_blocks(document):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if text and text not in seen_lines:
                lines.append(text)
                seen_lines.add(text)
        elif isinstance(block, Table):
            for row in block.rows:
                cells = []
                for cell in row.cells:
                    cell_text = "\n".join(
                        paragraph.text.strip()
                        for paragraph in cell.paragraphs
                        if paragraph.text.strip()
                    )
                    if cell_text:
                        cells.append(cell_text)
                row_text = " | ".join(cells)
                if row_text and row_text not in seen_lines:
                    lines.append(row_text)
                    seen_lines.add(row_text)

    for text in _extract_docx_xml_text(file_bytes):
        if text and text not in seen_lines:
            lines.append(text)
            seen_lines.add(text)

    if not lines:
        raise ValueError("Word 文档未解析到有效文本")
    return "\n".join(lines)


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract page text in PDF page order without rendering images."""
    import fitz

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    text_blocks: list[str] = []
    try:
        for page_index in range(len(document)):
            text_blocks.append(document[page_index].get_text())
    finally:
        document.close()
    return "\n".join(text_blocks)


def _iter_doc_blocks(document: DocumentType) -> Iterator[Paragraph | Table]:
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield Table(child, document)


def _extract_docx_xml_text(file_bytes: bytes) -> list[str]:
    xml_names = [
        "word/document.xml",
        "word/footnotes.xml",
        "word/endnotes.xml",
    ]
    result: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
            archive_names = archive.namelist()
            archive_name_set = set(archive_names)
            xml_names.extend(
                name
                for name in archive_names
                if re.match(r"word/(header|footer)\d+\.xml$", name)
            )
            for name in xml_names:
                if name not in archive_name_set:
                    continue
                root = ElementTree.fromstring(archive.read(name))
                for formula in root.iter():
                    if not (
                        formula.tag.endswith("}oMath")
                        or formula.tag.endswith("}oMathPara")
                    ):
                        continue
                    math_chunks: list[str] = []
                    for node in formula.iter():
                        if node.tag.endswith("}t") and node.text:
                            math_chunks.append(node.text)
                        elif node.tag.endswith("}r") and node.text:
                            math_chunks.append(node.text)
                    formula_text = "".join(math_chunks).strip()
                    placeholder = (
                        f"[公式: {formula_text}]" if formula_text else "[数学公式]"
                    )
                    if placeholder not in result:
                        result.append(placeholder)

                for paragraph in root.iter():
                    if not paragraph.tag.endswith("}p"):
                        continue
                    chunks: list[str] = []
                    for node in paragraph.iter():
                        if node.tag.endswith("}t") and node.text:
                            chunks.append(node.text)
                        elif node.tag.endswith("}tab"):
                            chunks.append("\t")
                        elif node.tag.endswith("}br"):
                            chunks.append("\n")
                    text = re.sub(r"[ \t]+", " ", "".join(chunks)).strip()
                    if text and text not in result:
                        result.append(text)
    except Exception:
        return []
    return result
