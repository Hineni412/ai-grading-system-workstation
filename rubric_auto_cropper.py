import io
import os

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
    pdf_bytes: bytes, question_blocks: list, *, scale: float = 2.0,
    top_padding_pt: float = 6.0, bottom_padding_pt: float = 10.0,
    jpeg_quality: int = 88, include_answer: bool = True,
    layout_pages: dict | None = None,
) -> dict[str, dict[str, bytes | None]]:
    """Crop in page/column reading order, using embedded or existing OCR boxes.

    OCR text locates regions only. Every returned semantic asset comes from the
    original page pixels. Missing/ambiguous markers are omitted for the caller
    to reject instead of silently analysing an incomplete question.
    """
    import re

    ids = {str(b.get("question_id") or "").lstrip("Qq"): str(b.get("question_id"))
           for b in question_blocks if b.get("question_id")}
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if not len(doc):
            return {}
        records = []
        for pg, page in enumerate(doc):
            supplied = (layout_pages or {}).get(pg, (layout_pages or {}).get(str(pg)))
            if supplied:
                for item in supplied:
                    box = tuple(float(v) for v in item.get("bbox", ()))
                    if len(box) != 4:
                        continue
                    if max(box) <= 1.01:
                        box = (box[0]*page.rect.width, box[1]*page.rect.height,
                               box[2]*page.rect.width, box[3]*page.rect.height)
                    records.append((pg, box, str(item.get("text") or "").strip()))
            else:
                for block in page.get_text("dict")["blocks"]:
                    for line in block.get("lines", []):
                        records.append((pg, tuple(line["bbox"]),
                                        "".join(s["text"] for s in line["spans"]).strip()))

        marker = re.compile(r"^\s*(\d{1,3})\s*[.．、)）](?!\d)")
        raw_markers = [(pg, box, m.group(1)) for pg, box, value in records
                       if (m := marker.match(value)) and m.group(1) in ids]
        # Main-number anchors on both sides are stronger evidence of columns
        # than illustrations or a wide table. Keep each page's own layout.
        splits = {}
        for pg, page in enumerate(doc):
            boxes = [b for p, b, _ in raw_markers if p == pg]
            left = [b for b in boxes if b[0] < page.rect.width * .35]
            right = [b for b in boxes if b[0] > page.rect.width * .45]
            if left and right:
                right_start = min(b[0] for b in right)
                left_lines = [b[2] for p, b, _ in records if p == pg and
                              b[0] < page.rect.width * .35 and b[2] < right_start]
                left_end = max(left_lines, default=page.rect.width * .45)
                splits[pg] = min(right_start - 3, max(page.rect.width * .4,
                                                    (left_end + right_start) / 2))
        # Continue a two-column question over a marker-free following page only
        # when the page has the same visible gutter (no line bridges the middle).
        for pg, page in enumerate(doc):
            if pg in splits or pg - 1 not in splits:
                continue
            boxes = [b for p, b, _ in records if p == pg]
            mid = page.rect.width / 2
            if (any(b[0] > mid for b in boxes) and any(b[2] < mid for b in boxes)
                    and not any(b[0] < mid - 15 and b[2] > mid + 15 for b in boxes)):
                splits[pg] = mid

        def position(pg, box):
            return (pg, int(pg in splits and box[0] >= splits[pg]), box[1])

        headings = [position(pg, box) for pg, box, value in records
                    if any(value.casefold().startswith(h.casefold())
                           for h in _ANSWER_SECTION_HEADINGS)
                    and len(value) < 45]
        answer_start = min(headings) if headings else None
        end = (len(doc)-1, int(len(doc)-1 in splits), doc[-1].rect.height)
        question_end = answer_start or end
        q_marks, a_marks = [], []
        for pg, box, number in raw_markers:
            pos = position(pg, box)
            (a_marks if answer_start and pos > answer_start else q_marks).append((pos, ids[number]))
        q_marks.sort()
        a_marks.sort()

        def crops(start, stop):
            images = []
            for pg in range(start[0], stop[0]+1):
                page = doc[pg]
                bounds = (0, splits[pg], page.rect.width) if pg in splits else (0, page.rect.width)
                for col in range(len(bounds)-1):
                    if (pg, col) < start[:2] or (pg, col) > stop[:2]:
                        continue
                    y0 = max(0, start[2] - top_padding_pt) if (pg, col) == start[:2] else 0
                    y1 = max(0, stop[2] - top_padding_pt) if (pg, col) == stop[:2] and stop != end else page.rect.height
                    if y1 <= y0:
                        continue
                    clip = fitz.Rect(bounds[col], y0, bounds[col+1], y1)
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
                    picture = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    from PIL import ImageChops
                    if ImageChops.difference(picture, Image.new("RGB", picture.size, "white")).getbbox():
                        images.append(picture)
            if not images:
                return None
            output = io.BytesIO()
            _stack_images(images).save(output, format="JPEG", quality=jpeg_quality)
            return output.getvalue()

        def collect(marks, stop):
            from collections import Counter
            counts = Counter(qid for _, qid in marks)
            return {qid: crops(pos, marks[i+1][0] if i+1 < len(marks) else stop)
                    for i, (pos, qid) in enumerate(marks) if counts[qid] == 1}

        questions = collect(q_marks, question_end)
        answers = collect(a_marks, end) if include_answer else {}
        return {qid: {"question": content, "answer": answers.get(qid)}
                for qid, content in questions.items() if content}
