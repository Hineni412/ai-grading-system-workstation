import io
import os
import tempfile
import fitz  # PyMuPDF — module-level so helper functions can reference it
from PIL import Image


def convert_docx_to_pdf_images(docx_bytes: bytes) -> list[bytes]:
    """
    Converts a DOCX to PDF, and then renders each page as a JPEG image.
    Returns a list of JPEG image bytes (one for each page).
    """
    try:
        import win32com.client
    except ImportError:
        raise ImportError("win32com is not installed, cannot convert DOCX to PDF on Windows.")
    try:
        import fitz
    except ImportError:
        raise ImportError("PyMuPDF (fitz) is not installed, cannot read PDF.")

    import tempfile
    from pathlib import Path
    
    project_temp = Path(__file__).parent / "user_data" / "temp"
    project_temp.mkdir(parents=True, exist_ok=True)
    
    with tempfile.TemporaryDirectory(dir=str(project_temp)) as tmpdir:
        docx_path = os.path.join(tmpdir, "temp.docx")
        pdf_path = os.path.join(tmpdir, "temp.pdf")

        with open(docx_path, "wb") as f:
            f.write(docx_bytes)

        import subprocess
        
        ps_script = os.path.join(os.path.dirname(__file__), "docx2pdf.ps1")
        if not os.path.exists(ps_script):
            raise RuntimeError(f"Missing conversion script: {ps_script}")
            
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            ps_script,
            "-InputPath",
            os.path.abspath(docx_path),
            "-OutputPath",
            os.path.abspath(pdf_path)
        ]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Word to PDF conversion failed via PowerShell. Exit code {e.returncode}.\nStdout: {e.stdout}\nStderr: {e.stderr}")

        if not os.path.exists(pdf_path):
            raise RuntimeError(f"PDF was not created at {pdf_path}")

        # Parse PDF
        doc = fitz.open(pdf_path)
        image_blobs = []
        
        try:
            for page_idx in range(len(doc)):
                page = doc[page_idx]
                pix = page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=75)
                image_blobs.append(buffer.getvalue())
        finally:
            doc.close()

    return image_blobs

def extract_pdf_images(pdf_bytes: bytes) -> list[bytes]:
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    image_blobs = []
    try:
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            pix = page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=75)
            image_blobs.append(buffer.getvalue())
    finally:
        doc.close()
    return image_blobs

def extract_pdf_text(pdf_bytes: bytes) -> str:
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text_blocks = []
    try:
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            text_blocks.append(page.get_text())
    finally:
        doc.close()
    return "\n".join(text_blocks)


# ---------------------------------------------------------------------------
# Plan B: per-question bbox cropping  (question + answer/solution)
# ---------------------------------------------------------------------------

# Known Chinese/English headings that mark the start of the answer section
_ANSWER_SECTION_HEADINGS = [
    "参考答案",       # most common
    "试题解析",
    "答案与解析",
    "答案及解析",
    "解析",
    "Answer",
    "Answers",
    "Solution",
    "Solutions",
    "answer key",
    "Answer Key",
]


def _question_number_search_patterns(num: str) -> list[str]:
    """Priority-ordered search strings for question number *num*."""
    return [
        f"{num}．",       # fullwidth period (common in Chinese exams)
        f"{num}.",        # ASCII period
        f"{num}、",       # Chinese enumeration mark
        f"第{num}题",     # 第N题
        f"（{num}）",     # （N）
        f"({num})",       # (N)
        f"{num} ",        # bare number followed by space (fallback)
    ]


def _is_question_marker_word(text: str, num: str, x0: float) -> bool:
    text = text.strip()
    if not text:
        return False
    # Avoid matching diagram captions like "第5题图" or "图5"
    if "图" in text and "题图" in text:
        return False
    if text.startswith("图" + num):
        return False
        
    import re
    escaped_num = re.escape(num)
    
    # 1. Number parenthesized: e.g. "(5)", "（5）", "5)", "5）"
    if re.match(rf"^[（(]?{escaped_num}[)）]$", text):
        if not (x0 < 110 or (250 < x0 < 350)):
            return False
        return True
        
    # 2. Exactly number followed by period/comma/顿号 (e.g. "5.", "5．", "5、")
    if re.match(rf"^[（(]?{escaped_num}(?!\d)[.．、]$", text):
        if not (x0 < 150 or (240 < x0 < 380)):
            return False
        return True

    # 3. Number followed by period/bracket/comma/顿号, and then not followed by digit (prevents decimals like 5.0, e.g. "5.如图")
    if re.match(rf"^[（(]?{escaped_num}(?!\d)[.．、)）](?!\d).*", text):
        if not (x0 < 150 or (240 < x0 < 380)):
            return False
        return True

    # 4. Number followed by a Chinese character (prevents matching 50, e.g. "5如图")
    if re.match(rf"^[（(]?{escaped_num}(?!\d)[\u4e00-\u9fa5].*", text):
        # Exclude units / scores
        if re.match(rf"^[（(]?{escaped_num}(?!\d)(分|点|秒|个|元|只|cm|m|s|kg|℃)", text):
            return False
        if not (x0 < 150 or (240 < x0 < 380)):
            return False
        return True

    # 5. "第5题"
    if re.match(rf"^第{escaped_num}(?!\d)题.*", text):
        if not (x0 < 150 or (240 < x0 < 380)):
            return False
        return True

    return False


def _find_question_marker(
    doc,
    num: str,
    n_pages: int,
    *,
    after_page: int = 0,
    after_y: float = 0.0,
) -> tuple[int, float] | None:
    """Search pages for question *num*'s marker, optionally starting after a given position.

    Returns (page_index, y0_in_points) or None if not found.
    """
    for page_idx in range(after_page, n_pages):
        page = doc[page_idx]
        try:
            words = page.get_text("words")
        except Exception:
            words = []
        valid_words = []
        for w in words:
            x0, y0, x1, y1, text, _, _, _ = w
            if page_idx == after_page and y0 < after_y:
                continue
            if _is_question_marker_word(text, num, x0):
                valid_words.append(w)
        if valid_words:
            # Sort by y0 first, then x0
            best = min(valid_words, key=lambda w: (w[1], w[0]))
            return (page_idx, float(best[1]))
    return None


def _find_answer_section_start(doc, n_pages: int) -> tuple[int, float] | None:
    """Return (page_idx, y0) of the first answer-section heading found.

    Returns None when no separate answer section is detected (inline format).
    """
    for page_idx in range(n_pages):
        page = doc[page_idx]
        for heading in _ANSWER_SECTION_HEADINGS:
            rects = page.search_for(heading)
            if rects:
                best = min(rects, key=lambda r: (r.y0, r.x0))
                return (page_idx, float(best.y0))
    return None


def _crop_segments(
    doc,
    segments: list[tuple[int, float, float]],
    page_w: float,
    mat,
) -> list["Image.Image"]:
    """Render a list of (page_idx, y_start, y_end) segments as PIL images."""
    images = []
    for seg_page_idx, y0, y1 in segments:
        if y1 <= y0:
            continue
        seg_page = doc[seg_page_idx]
        clip = fitz.Rect(0, y0, page_w, y1)
        pix = seg_page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        images.append(Image.frombytes("RGB", [pix.width, pix.height], pix.samples))
    return images


def _stack_images(images: list["Image.Image"], bg: tuple = (255, 255, 255)) -> "Image.Image":
    """Stack PIL images vertically."""
    if not images:
        return Image.new("RGB", (1, 1), bg)
    if len(images) == 1:
        return images[0]
    total_h = sum(im.height for im in images)
    total_w = max(im.width for im in images)
    canvas = Image.new("RGB", (total_w, total_h), bg)
    y = 0
    for im in images:
        canvas.paste(im, (0, y))
        y += im.height
    return canvas


def _make_divider_banner(width_px: int, label: str = "[ 答案与解析 ]") -> "Image.Image":
    """Create a thin horizontal banner that visually separates question from answer."""
    from PIL import ImageDraw, ImageFont
    HEIGHT = 36
    banner = Image.new("RGB", (width_px, HEIGHT), (230, 240, 255))  # light blue-gray
    draw = ImageDraw.Draw(banner)
    # Draw top and bottom border lines
    draw.line([(0, 0), (width_px, 0)], fill=(160, 180, 220), width=2)
    draw.line([(0, HEIGHT - 1), (width_px, HEIGHT - 1)], fill=(160, 180, 220), width=2)
    # Center the label text
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except (OSError, IOError):
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (width_px - text_w) // 2
    y = (HEIGHT - text_h) // 2
    draw.text((x, y), label, fill=(80, 100, 160), font=font)
    return banner


def extract_pdf_question_images(
    pdf_bytes: bytes,
    question_blocks: list,
    *,
    scale: float = 2.0,
    top_padding_pt: float = 6.0,
    bottom_padding_pt: float = 10.0,
    jpeg_quality: int = 88,
    include_answer: bool = True,
) -> dict[str, bytes]:
    """Crop a JPEG image for each question from a PDF using bbox detection.

    Each output image contains:
    - The question stem (and diagrams / options) from the question section
    - [if a separate answer section is found] A divider banner + the answer/
      solution text from the answer section

    When the document uses inline answers (【答案】/【解析】 markers within the
    question text), the answer is already part of the question crop and no
    separate answer section is expected.

    Algorithm (Plan B):
    1. Search for the answer-section heading (参考答案 / Solutions / …).
    2. For every question block, locate its number marker in the question
       section (before the answer-section heading).
    3. Crop the question region (marker y → next marker y or answer-section
       start).
    4. If an answer section was found, locate each question's answer marker
       inside that section and crop the corresponding answer region.
    5. Stack [question crop] + [divider] + [answer crop] vertically.

    Args:
        pdf_bytes:         Raw PDF bytes.
        question_blocks:   Output of preview_question_blocks_from_docx_text.
        scale:             Render DPI multiplier (2.0 ≈ 150 dpi).
        top_padding_pt:    Extra space added above each question marker.
        bottom_padding_pt: Extra space added below each crop boundary.
        jpeg_quality:      JPEG compression quality (0–95).
        include_answer:    If False, only crop question regions (no answer).

    Returns:
        dict mapping question_id → JPEG bytes.  Questions whose marker could
        not be located in the question section are omitted.
    """
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    n_pages = len(doc)
    mat = fitz.Matrix(scale, scale)

    # ── 1. Find the answer section boundary ───────────────────────────────
    ans_section = _find_answer_section_start(doc, n_pages) if include_answer else None
    # ans_section: (page_idx, y0) or None

    # Upper search limit for question markers: stop just before answer section
    q_search_limit_page = ans_section[0] if ans_section else n_pages - 1
    q_search_limit_y = ans_section[1] if ans_section else float("inf")

    # ── 2. Locate every question marker (question section only) ───────────
    # Each entry: (qid, page_idx, y0_pt)
    q_markers: list[tuple[str, int, float]] = []
    q_missing: list[str] = []

    for block in question_blocks:
        qid = str(block.get("question_id") or "").strip()
        if not qid:
            continue
        num = qid.lstrip("Q").lstrip("q")
        found = _find_question_marker(doc, num, n_pages)
        if found is None:
            q_missing.append(qid)
            continue
        pg, y = found
        # Discard hits that are inside the answer section
        if ans_section and (pg > q_search_limit_page or
                            (pg == q_search_limit_page and y >= q_search_limit_y)):
            q_missing.append(qid)
            continue
        q_markers.append((qid, pg, y))

    q_markers.sort(key=lambda m: (m[1], m[2]))

    # ── 3. Locate every answer marker (answer section only) ───────────────
    # Each entry: (qid, page_idx, y0_pt)
    a_markers: list[tuple[str, int, float]] = []

    if ans_section and include_answer:
        a_sec_page, a_sec_y = ans_section
        for block in question_blocks:
            qid = str(block.get("question_id") or "").strip()
            if not qid:
                continue
            num = qid.lstrip("Q").lstrip("q")
            found = _find_question_marker(
                doc, num, n_pages,
                after_page=a_sec_page,
                after_y=a_sec_y,
            )
            if found:
                a_markers.append((qid, found[0], found[1]))

        a_markers.sort(key=lambda m: (m[1], m[2]))

    # Build answer-marker lookup: qid → index in a_markers
    a_marker_index: dict[str, int] = {qid: i for i, (qid, _, _) in enumerate(a_markers)}

    # ── 4. Crop question + answer regions and combine ─────────────────────
    result: dict[str, bytes] = {}

    for i, (qid, q_page, q_y_top) in enumerate(q_markers):
        page = doc[q_page]
        page_w = page.rect.width
        page_h = page.rect.height

        # ── 4a. Question crop boundary ────────────────────────────────────
        # Bottom = next question marker (or answer section start, or page bottom)
        if i + 1 < len(q_markers):
            _, nxt_page, nxt_y = q_markers[i + 1]
            nxt_padding = 0.0  # Do not add extra bottom padding when ending at a next question marker
        elif ans_section:
            nxt_page, nxt_y = ans_section
            nxt_padding = bottom_padding_pt
        else:
            nxt_page, nxt_y = q_page, page_h
            nxt_padding = bottom_padding_pt

        q_segments: list[tuple[int, float, float]] = []
        if nxt_page == q_page:
            q_segments.append((q_page,
                                max(0.0, q_y_top - top_padding_pt),
                                min(page_h, nxt_y + nxt_padding)))
        else:
            q_segments.append((q_page,
                                max(0.0, q_y_top - top_padding_pt),
                                page_h))
            for mid in range(q_page + 1, nxt_page):
                q_segments.append((mid, 0.0, doc[mid].rect.height))
            # Only crop the top of nxt_page if the next question starts sufficiently down the page
            if nxt_y >= 80.0:
                q_segments.append((nxt_page,
                                    0.0,
                                    min(doc[nxt_page].rect.height,
                                        nxt_y + nxt_padding)))

        q_imgs = _crop_segments(doc, q_segments, page_w, mat)
        if not q_imgs:
            continue
        q_combined = _stack_images(q_imgs)

        # ── 4b. Answer crop (only when separate answer section exists) ────
        a_combined: Image.Image | None = None

        if ans_section and include_answer and qid in a_marker_index:
            ai = a_marker_index[qid]
            _, a_page, a_y_top = a_markers[ai]
            a_page_doc = doc[a_page]
            a_page_w = a_page_doc.rect.width
            a_page_h = a_page_doc.rect.height

            # Answer bottom = next answer marker
            if ai + 1 < len(a_markers):
                _, an_page, an_y = a_markers[ai + 1]
                an_padding = 0.0  # Do not add extra bottom padding when ending at a next answer marker
            else:
                an_page = n_pages - 1
                an_y = doc[n_pages - 1].rect.height
                an_padding = bottom_padding_pt

            a_segments: list[tuple[int, float, float]] = []
            if an_page == a_page:
                a_segments.append((a_page,
                                   max(0.0, a_y_top - top_padding_pt),
                                   min(a_page_h, an_y + an_padding)))
            else:
                a_segments.append((a_page,
                                   max(0.0, a_y_top - top_padding_pt),
                                   a_page_h))
                for mid in range(a_page + 1, an_page):
                    a_segments.append((mid, 0.0, doc[mid].rect.height))
                # Only crop the top of an_page if the next answer starts sufficiently down the page,
                # or if it is the last page (end of PDF).
                if an_y >= 80.0 or an_page == n_pages - 1:
                    a_segments.append((an_page,
                                       0.0,
                                       min(doc[an_page].rect.height,
                                           an_y + an_padding)))

            a_imgs = _crop_segments(doc, a_segments, a_page_w, mat)
            if a_imgs:
                a_combined = _stack_images(a_imgs)

        # ── 4c. Save question and answer images separately ────────────────
        buf_q = io.BytesIO()
        q_combined.save(buf_q, format="JPEG", quality=jpeg_quality)
        q_bytes = buf_q.getvalue()

        a_bytes = None
        if a_combined is not None:
            buf_a = io.BytesIO()
            a_combined.save(buf_a, format="JPEG", quality=jpeg_quality)
            a_bytes = buf_a.getvalue()

        result[qid] = {
            "question": q_bytes,
            "answer": a_bytes
        }

    doc.close()

    all_missing = q_missing
    if all_missing:
        import warnings as _warnings
        _warnings.warn(
            f"extract_pdf_question_images: could not locate question markers for: "
            f"{', '.join(all_missing)}",
            stacklevel=2,
        )

    return result

