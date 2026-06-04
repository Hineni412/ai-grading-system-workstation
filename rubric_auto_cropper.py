import io
import os
import tempfile
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
