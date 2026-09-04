from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from typing import List
import tempfile
import shutil
import subprocess
import uuid
import threading
import time
from zipfile import ZipFile, ZIP_DEFLATED

import fitz
from pypdf import PdfReader, PdfWriter
from docx import Document
from docx.shared import Inches
from pptx import Presentation
from pptx.util import Inches as PptInches
from openpyxl import Workbook
from PIL import Image
import pdfplumber
import pytesseract
from weasyprint import HTML
from lxml import etree


BASE = Path(__file__).parent
TMP = BASE / "tmp"
TMP.mkdir(exist_ok=True)

app = FastAPI(title="PaperMint PDF Toolbox")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


TOOLS = [
    ("merge", "Merge PDF", "Combine PDFs in the order you want."),
    ("split", "Split PDF", "Split a PDF into separate files or ranges."),
    ("compress", "Compress PDF", "Reduce PDF file size while preserving quality."),
    ("pdf-word", "PDF to Word", "Convert PDF to an editable Word document."),
    ("pdf-ppt", "PDF to PowerPoint", "Convert each PDF page to a PowerPoint slide."),
    ("pdf-excel", "PDF to Excel", "Extract detected tables into an XLSX workbook."),
    ("pdf-jpg", "PDF to JPG", "Render PDF pages as JPG images."),
    ("word-pdf", "Word to PDF", "Convert DOC/DOCX to PDF."),
    ("ppt-pdf", "PowerPoint to PDF", "Convert PPT/PPTX to PDF."),
    ("excel-pdf", "Excel to PDF", "Convert XLS/XLSX to PDF."),
    ("jpg-pdf", "JPG to PDF", "Combine images into a PDF."),
    ("sign", "Sign PDF", "Add a simple text signature to a PDF page."),
    ("watermark", "Watermark", "Add text watermark to every page."),
    ("rotate", "Rotate PDF", "Rotate every page by 90, 180 or 270 degrees."),
    ("html-pdf", "HTML to PDF", "Convert uploaded HTML into PDF."),
    ("unlock", "Unlock PDF", "Remove password protection when you know the password."),
    ("protect", "Protect PDF", "Encrypt a PDF with a password."),
    ("organize", "Organize PDF", "Reorder pages using a page list such as 3,1,2."),
    ("pdfa", "PDF to PDF/A", "Create an archival-style PDF copy."),
    ("repair", "Repair PDF", "Rewrite a damaged/readable PDF into a fresh file."),
    ("page-numbers", "Page numbers", "Add page numbers to every page."),
    ("scan-pdf", "Scan to PDF", "Convert phone scans/images into a PDF."),
    ("ocr", "OCR PDF", "Recognize text from scanned PDF pages."),
    ("compare", "Compare PDF", "Create a text difference report for two PDFs."),
    ("redact", "Redact PDF", "Search and permanently redact specified text."),
    ("crop", "Crop PDF", "Crop all pages by margins in millimeters."),
]


@app.get("/", response_class=HTMLResponse)
def home():
    return (BASE / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/tools")
def tools():
    return [
        {"id": a, "name": b, "description": c}
        for a, b, c in TOOLS
    ]


def save_upload(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "").suffix
    p = TMP / f"{uuid.uuid4().hex}{suffix}"

    with p.open("wb") as f:
        shutil.copyfileobj(upload.file, f)

    return p


def outpath(ext: str) -> Path:
    return TMP / f"{uuid.uuid4().hex}{ext}"


def libreoffice_to_pdf(src: Path) -> Path:
    outdir = Path(tempfile.mkdtemp(dir=TMP))

    cmd = [
        "libreoffice",
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(outdir),
        str(src),
    ]

    cp = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
    )

    candidates = list(outdir.glob("*.pdf"))

    if not candidates:
        raise HTTPException(
            500,
            f"LibreOffice conversion failed: {cp.stderr[-500
