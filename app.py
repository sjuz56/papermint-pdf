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
import zipfile

import fitz
from pypdf import PdfReader, PdfWriter

from docx import Document
from docx.shared import Pt, Inches
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from pptx import Presentation
from pptx.util import Inches as PptInches
from openpyxl import Workbook
from PIL import Image

import pdfplumber
import pytesseract
from weasyprint import HTML


# ============================================================
# PAPERMINT PAGEGRID V2 COMPLETE
# ============================================================

BASE = Path(__file__).parent
TMP = BASE / "tmp"
TMP.mkdir(exist_ok=True)

app = FastAPI(title="PaperMint PDF Toolbox")

app.mount(
    "/static",
    StaticFiles(directory=BASE / "static"),
    name="static",
)


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
        {
            "id": a,
            "name": b,
            "description": c,
        }
        for a, b, c in TOOLS
    ]


def save_upload(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "").suffix

    path = TMP / f"{uuid.uuid4().hex}{suffix}"

    with path.open("wb") as f:
        shutil.copyfileobj(upload.file, f)

    return path


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
            f"LibreOffice conversion failed: {cp.stderr[-500:]}",
        )

    return candidates[0]


# ============================================================
# PAGEGRID PDF -> WORD ENGINE
# ============================================================


def _set_cell_margins(
    cell,
    top=20,
    start=35,
    bottom=20,
    end=35,
):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()

    tcMar = tcPr.first_child_found_in("w:tcMar")

    if tcMar is None:
        tcMar = OxmlElement("w:tcMar")
        tcPr.append(tcMar)

    values = {
        "top": top,
        "start": start,
        "bottom": bottom,
        "end": end,
    }

    for margin, value in values.items():

        node = tcMar.find(
            qn(f"w:{margin}")
        )

        if node is None:
            node = OxmlElement(
                f"w:{margin}"
            )
            tcMar.append(node)

        node.set(
            qn("w:w"),
            str(value),
        )

        node.set(
            qn("w:type"),
            "dxa",
        )


def _remove_table_borders(table):

    tbl = table._tbl
    tblPr = tbl.tblPr

    borders = tblPr.first_child_found_in(
        "w:tblBorders"
    )

    if borders is None:
        borders = OxmlElement(
            "w:tblBorders"
        )
        tblPr.append(borders)

    edges = (
        "top",
        "left",
        "bottom",
        "right",
        "insideH",
        "insideV",
    )

    for edge in edges:

        tag = f"w:{edge}"

        element = borders.find(
            qn(tag)
        )

        if element is None:
            element = OxmlElement(tag)
            borders.append(element)

        element.set(
            qn("w:val"),
            "nil",
        )


def _group_words_into_rows(
    words,
    tolerance=2.8,
):

    if not words:
        return []

    words = sorted(
        words,
        key=lambda w: (
            w[1],
            w[0],
        ),
    )

    rows = []

    for word in words:

        y = word[1]

        target = None

        for row in reversed(
            rows[-4:]
        ):

            if abs(
                row["y"] - y
            ) <= tolerance:

                target = row
                break

        if target is None:

            target = {
                "y": y,
                "words": [],
            }

            rows.append(target)

        target["words"].append(
            word
        )

    for row in rows:

        row["words"].sort(
            key=lambda w: w[0]
        )

    return rows


def _detect_columns(
    rows,
    page_width,
):
    """
    Stable normalized grid.

    Works better than floating text
    for statements, invoices and
    table-heavy PDFs.
    """

    fractions = [
        0.00,
        0.18,
        0.36,
        0.56,
        0.76,
        1.00,
    ]

    return [
        page_width * f
        for f in fractions
    ]


def _column_for_x(
    x,
    boundaries,
):

    for i in range(
        len(boundaries) - 1
    ):

        if (
            boundaries[i]
            <= x
            < boundaries[i + 1]
        ):
            return i

    return len(boundaries) - 2


def _clean_pdf_words(words):

    cleaned = []

    for word in words:

        text = str(
            word[4]
        ).strip()

        if not text:
            continue

        if text.upper() == "SIGN":
            continue

        cleaned.append(word)

    return cleaned


def _add_page_to_docx(
    doc,
    page,
):

    page_width_pt = page.rect.width
    page_height_pt = page.rect.height

    section = doc.sections[-1]

    section.page_width = Inches(
        page_width_pt / 72
    )

    section.page_height = Inches(
        page_height_pt / 72
    )

    section.top_margin = Inches(0.22)
    section.bottom_margin = Inches(0.22)
    section.left_margin = Inches(0.22)
    section.right_margin = Inches(0.22)

    words = page.get_text(
        "words"
    )

    words = _clean_pdf_words(
        words
    )

    rows = _group_words_into_rows(
        words
    )

    boundaries = _detect_columns(
        rows,
        page_width_pt,
    )

    column_count = (
        len(boundaries) - 1
    )

    table = doc.add_table(
        rows=0,
        cols=column_count,
    )

    table.alignment = (
        WD_TABLE_ALIGNMENT.CENTER
    )

    table.autofit = False

    _remove_table_borders(
        table
    )

    usable_width = (
        section.page_width
        - section.left_margin
        - section.right_margin
    )

    widths = []

    for i in range(
        column_count
    ):

        ratio = (
            boundaries[i + 1]
            - boundaries[i]
        ) / page_width_pt

        widths.append(
            int(
                usable_width
                * ratio
            )
        )

    previous_y = None

    for source_row in rows:

        y = source_row["y"]

        if previous_y is not None:

            gap = (
                y
                - previous_y
            )

            if gap > 24:

                spacer = (
                    table.add_row()
                )

                for cell in spacer.cells:
                    cell.text = ""

                spacer.height = Pt(
                    min(
                        max(
                            gap * 0.45,
                            4,
                        ),
                        18,
                    )
                )

        previous_y = y

        row = table.add_row()

        values = [
            []
            for _ in range(
                column_count
            )
        ]

        for word in source_row[
            "words"
        ]:

            col = _column_for_x(
                word[0],
                boundaries,
            )

            values[col].append(
                word
            )

        for (
            col_index,
            cell,
        ) in enumerate(
            row.cells
        ):

            cell.width = widths[
                col_index
            ]

            cell.vertical_alignment = (
                WD_CELL_VERTICAL_ALIGNMENT.CENTER
            )

            _set_cell_margins(
                cell
            )

            paragraph = (
                cell.paragraphs[0]
            )

            paragraph.paragraph_format.space_before = Pt(0)
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = 1

            source_words = values[
                col_index
            ]

            if not source_words:
                continue

            pieces = []

            last_x1 = None

            for word in source_words:

                (
                    x0,
                    _,
                    x1,
                    _,
                    text,
                ) = word[:5]

                if last_x1 is not None:

                    gap = (
                        x0
                        - last_x1
                    )

                    if gap > 12:
                        pieces.append(
                            "   "
                        )

                    elif gap > 4:
                        pieces.append(
                            " "
                        )

                pieces.append(
                    text
                )

                last_x1 = x1

            text = "".join(
                pieces
            )

            run = paragraph.add_run(
                text
            )

            heights = [
                max(
                    1,
                    w[3] - w[1],
                )
                for w
                in source_words
            ]

            avg_height = (
                sum(heights)
                / len(heights)
            )

            font_size = min(
                max(
                    avg_height * 0.72,
                    6.5,
                ),
                11,
            )

            run.font.size = Pt(
                font_size
            )

            if (
                col_index
                >= column_count - 2
            ):

                paragraph.alignment = (
                    WD_ALIGN_PARAGRAPH.RIGHT
                )

    return table


def pdf_to_word_editable(
    src: Path,
) -> Path:

    print(
        "PAGEGRID V2 STARTED",
        flush=True,
    )

    pdf = fitz.open(
        src
    )

    out = outpath(
        ".docx"
    )

    doc = Document()

    first_p = doc.paragraphs[0]

    p_element = (
        first_p._element
    )

    p_element.getparent().remove(
        p_element
    )

    for (
        page_index,
        page,
    ) in enumerate(pdf):

        print(
            f"PAGEGRID PAGE "
            f"{page_index + 1}/"
            f"{len(pdf)}",
            flush=True,
        )

        _add_page_to_docx(
            doc,
            page,
        )

        if (
            page_index
            < len(pdf) - 1
        ):

            paragraph = (
                doc.add_paragraph()
            )

            paragraph.paragraph_format.space_before = Pt(0)
            paragraph.paragraph_format.space_after = Pt(0)

            paragraph.add_run().add_break()

    doc.save(
        out
    )

    pdf.close()

    print(
        "PAGEGRID V2 FINISHED",
        flush=True,
    )

    return out


# ============================================================
# ASYNC PDF -> WORD
# ============================================================


PDF_WORD_JOBS = {}
PDF_WORD_LOCK = threading.Lock()
PDF_WORD_TTL = 60 * 60


def cleanup_pdf_word_jobs():

    now = time.time()

    with PDF_WORD_LOCK:

        expired = [
            job_id
            for (
                job_id,
                job,
            ) in PDF_WORD_JOBS.items()
            if (
                now
                - job.get(
                    "created",
                    now,
                )
                > PDF_WORD_TTL
            )
        ]

        for job_id in expired:

            job = (
                PDF_WORD_JOBS.pop(
                    job_id,
                    None,
                )
            )

            if not job:
                continue

            for key in (
                "source",
                "output",
            ):

                value = job.get(
                    key
                )

                if value:

                    try:
                        Path(
                            value
                        ).unlink(
                            missing_ok=True
                        )

                    except Exception:
                        pass


def run_pdf_word_job(
    job_id: str,
    source: Path,
):

    try:

        with PDF_WORD_LOCK:

            PDF_WORD_JOBS[
                job_id
            ][
                "status"
            ] = "processing"

        output = (
            pdf_to_word_editable(
                source
            )
        )

        with PDF_WORD_LOCK:

            PDF_WORD_JOBS[
                job_id
            ][
                "status"
            ] = "done"

            PDF_WORD_JOBS[
                job_id
            ][
                "output"
            ] = str(output)

    except Exception as e:

        print(
            f"PDF WORD JOB ERROR "
            f"{job_id}: {e}",
            flush=True,
        )

        with PDF_WORD_LOCK:

            if (
                job_id
                in PDF_WORD_JOBS
            ):

                PDF_WORD_JOBS[
                    job_id
                ][
                    "status"
                ] = "error"

                PDF_WORD_JOBS[
                    job_id
                ][
                    "error"
                ] = str(e)


@app.post(
    "/api/pdf-word/start"
)
async def pdf_word_start(
    file: UploadFile = File(...)
):

    cleanup_pdf_word_jobs()

    if not file.filename:

        raise HTTPException(
            400,
            "No file uploaded.",
        )

    if (
        Path(
            file.filename
        )
        .suffix
        .lower()
        != ".pdf"
    ):

        raise HTTPException(
            400,
            "Please upload a PDF file.",
        )

    source = save_upload(
        file
    )

    job_id = (
        uuid.uuid4().hex
    )

    with PDF_WORD_LOCK:

        PDF_WORD_JOBS[
            job_id
        ] = {
            "status": "queued",
            "created": time.time(),
            "source": str(source),
            "output": None,
            "error": None,
        }

    thread = threading.Thread(
        target=run_pdf_word_job,
        args=(
            job_id,
            source,
        ),
        daemon=True,
    )

    thread.start()

    return {
        "job_id": job_id,
        "status": "queued",
    }


@app.get(
    "/api/pdf-word/status/{job_id}"
)
def pdf_word_status(
    job_id: str
):

    cleanup_pdf_word_jobs()

    with PDF_WORD_LOCK:

        job = PDF_WORD_JOBS.get(
            job_id
        )

        if not job:

            raise HTTPException(
                404,
                "Conversion job not found.",
            )

        return {
            "job_id": job_id,
            "status": job[
                "status"
            ],
            "error": job.get(
                "error"
            ),
        }


@app.get(
    "/api/pdf-word/download/{job_id}"
)
def pdf_word_download(
    job_id: str
):

    with PDF_WORD_LOCK:

        job = PDF_WORD_JOBS.get(
            job_id
        )

        if not job:

            raise HTTPException(
                404,
                "Conversion job not found.",
            )

        if (
            job["status"]
            == "error"
        ):

            raise HTTPException(
                500,
                job.get("error")
                or "Conversion failed.",
            )

        if (
            job["status"]
            != "done"
        ):

            raise
