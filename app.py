
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from typing import List
import tempfile, shutil, subprocess, uuid

import fitz
from pypdf import PdfReader, PdfWriter
from docx import Document
from pptx import Presentation
from pptx.util import Inches as PptInches
from openpyxl import Workbook
from PIL import Image
import pdfplumber
import pytesseract
from weasyprint import HTML


BASE = Path(__file__).parent
TMP = BASE / "tmp"
TMP.mkdir(exist_ok=True)

app = FastAPI(title="PDF Toolbox")
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
        {"id": tool_id, "name": name, "description": description}
        for tool_id, name, description in TOOLS
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

    result = subprocess.run(
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
            f"LibreOffice conversion failed: {result.stderr[-500:]}",
        )

    return candidates[0]


def pdf_to_word_editable(src: Path) -> Path:
    print("PDF2DOCX ENGINE STARTED", flush=True)

    try:
        from pdf2docx import Converter
    except Exception as exc:
        raise RuntimeError(
            f"PDF to Word engine could not be loaded: {exc}"
        ) from exc

    out = outpath(".docx")

    converter = None

    try:
        converter = Converter(str(src))

        converter.convert(
            str(out),
            start=0,
            end=None,
        )

        print("PDF2DOCX ENGINE FINISHED", flush=True)

        return out

    except Exception as exc:
        raise RuntimeError(
            f"PDF to Word conversion failed: {exc}"
        ) from exc

    finally:
        if converter is not None:
            converter.close()


@app.post("/api/convert")
async def convert(
    tool: str = Form(...),
    files: List[UploadFile] = File(default=[]),
    mode: str = Form("editable"),
    password: str = Form(""),
    text: str = Form(""),
    rotation: int = Form(90),
    pages: str = Form(""),
    margin: float = Form(10.0),
    page_number_start: int = Form(1),
    signature_page: int = Form(1),
    signature_x: float = Form(30),
    signature_y: float = Form(30),
):
    if tool != "html-pdf" and not files:
        raise HTTPException(400, "Upload at least one file.")

    paths = [save_upload(f) for f in files]

    try:

        if tool == "merge":
            writer = PdfWriter()

            for path in paths:
                reader = PdfReader(str(path))

                for page in reader.pages:
                    writer.add_page(page)

            out = outpath(".pdf")
            writer.write(str(out))

            return FileResponse(out, filename="merged.pdf")


        if tool == "split":
            reader = PdfReader(str(paths[0]))

            indexes = []

            if pages.strip():
                for token in pages.split(","):
                    token = token.strip()

                    if "-" in token:
                        a, b = map(int, token.split("-", 1))
                        indexes.extend(range(a - 1, b))

                    elif token:
                        indexes.append(int(token) - 1)

            else:
                indexes = [0]

            writer = PdfWriter()

            for i in indexes:
                if 0 <= i < len(reader.pages):
                    writer.add_page(reader.pages[i])

            out = outpath(".pdf")
            writer.write(str(out))

            return FileResponse(out, filename="split.pdf")


        if tool == "compress":
            doc = fitz.open(paths[0])

            out = outpath(".pdf")

            doc.save(
                out,
                garbage=4,
                deflate=True,
                clean=True,
            )

            return FileResponse(out, filename="compressed.pdf")


        if tool == "pdf-word":
            print("PDF WORD ROUTE CALLED", flush=True)

            out = pdf_to_word_editable(paths[0])

            return FileResponse(
                out,
                filename="converted.docx",
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )


        if tool == "pdf-ppt":
            pdf = fitz.open(paths[0])

            prs = Presentation()

            prs.slide_width = PptInches(13.333)
            prs.slide_height = PptInches(7.5)

            blank = prs.slide_layouts[6]

            for page in pdf:
                slide = prs.slides.add_slide(blank)

                pix = page.get_pixmap(
                    matrix=fitz.Matrix(2, 2),
                    alpha=False,
                )

                img = outpath(".png")
                pix.save(img)

                slide.shapes.add_picture(
                    str(img),
                    0,
                    0,
                    width=prs.slide_width,
                    height=prs.slide_height,
                )

            out = outpath(".pptx")

            prs.save(out)

            return FileResponse(
                out,
                filename="converted.pptx",
            )


        if tool == "pdf-excel":
            wb = Workbook()
            wb.remove(wb.active)

            with pdfplumber.open(paths[0]) as pdf:

                for page_number, page in enumerate(pdf.pages, 1):

                    tables = page.extract_tables()

                    if not tables:
                        ws = wb.create_sheet(
                            f"Page {page_number}"
                        )

                        extracted = page.extract_text() or ""

                        for row_number, line in enumerate(
                            extracted.splitlines(),
                            1,
                        ):
                            ws.cell(
                                row_number,
                                1,
                                line,
                            )

                    else:

                        for table_number, table in enumerate(
                            tables,
                            1,
                        ):
                            ws = wb.create_sheet(
                                f"P{page_number} T{table_number}"[:31]
                            )

                            for row, values in enumerate(
                                table,
                                1,
                            ):
                                for column, value in enumerate(
                                    values,
                                    1,
                                ):
                                    ws.cell(
                                        row,
                                        column,
                                        value,
                                    )

            out = outpath(".xlsx")

            wb.save(out)

            return FileResponse(
                out,
                filename="converted.xlsx",
            )


        if tool == "pdf-jpg":
            pdf = fitz.open(paths[0])

            if len(pdf) == 1:

                pix = pdf[0].get_pixmap(
                    matrix=fitz.Matrix(2, 2),
                    alpha=False,
                )

                out = outpath(".jpg")
                pix.save(out)

                return FileResponse(
                    out,
                    filename="page-1.jpg",
                )

            import zipfile

            out = outpath(".zip")

            with zipfile.ZipFile(out, "w") as z:

                for i, page in enumerate(pdf, 1):

                    img = outpath(".jpg")

                    page.get_pixmap(
                        matrix=fitz.Matrix(2, 2),
                        alpha=False,
                    ).save(img)

                    z.write(
                        img,
                        f"page-{i}.jpg",
                    )

            return FileResponse(
                out,
                filename="pages.zip",
            )


        if tool in {
            "word-pdf",
            "ppt-pdf",
            "excel-pdf",
        }:
            out = libreoffice_to_pdf(paths[0])

            return FileResponse(
                out,
                filename="converted.pdf",
            )


        if tool in {
            "jpg-pdf",
            "scan-pdf",
        }:
            images = [
                Image.open(path).convert("RGB")
                for path in paths
            ]

            out = outpath(".pdf")

            images[0].save(
                out,
                save_all=True,
                append_images=images[1:],
            )

            return FileResponse(
                out,
                filename="images.pdf",
            )


        if tool == "watermark":
            doc = fitz.open(paths[0])

            watermark = text or "WATERMARK"

            for page in doc:
                rect = page.rect

                page.insert_text(
                    (
                        rect.width * 0.22,
                        rect.height * 0.52,
                    ),
                    watermark,
                    fontsize=34,
                    overlay=True,
                )

            out = outpath(".pdf")

            doc.save(out)

            return FileResponse(
                out,
                filename="watermarked.pdf",
            )


        if tool == "rotate":
            doc = fitz.open(paths[0])

            for page in doc:
                page.set_rotation(
                    (page.rotation + rotation) % 360
                )

            out = outpath(".pdf")

            doc.save(out)

            return FileResponse(
                out,
                filename="rotated.pdf",
            )


        if tool == "protect":
            doc = fitz.open(paths[0])

            out = outpath(".pdf")

            doc.save(
                out,
                encryption=fitz.PDF_ENCRYPT_AES_256,
                owner_pw=password or "owner",
                user_pw=password or "password",
                permissions=(
                    fitz.PDF_PERM_ACCESSIBILITY
                    | fitz.PDF_PERM_PRINT
                ),
            )

            return FileResponse(
                out,
                filename="protected.pdf",
            )


        if tool == "unlock":
            doc = fitz.open(paths[0])

            if (
                doc.needs_pass
                and not doc.authenticate(password)
            ):
                raise HTTPException(
                    400,
                    "Incorrect password.",
                )

            out = outpath(".pdf")

            doc.save(out)

            return FileResponse(
                out,
                filename="unlocked.pdf",
            )


        if tool == "organize":
            doc = fitz.open(paths[0])

            order = [
                int(x.strip()) - 1
                for x in pages.split(",")
                if x.strip()
            ]

            if not order:
                raise HTTPException(
                    400,
                    "Enter page order, e.g. 3,1,2",
                )

            outdoc = fitz.open()

            for i in order:
                if 0 <= i < len(doc):
                    outdoc.insert_pdf(
                        doc,
                        from_page=i,
                        to_page=i,
                    )

            out = outpath(".pdf")

            outdoc.save(out)

            return FileResponse(
                out,
                filename="organized.pdf",
            )


        if tool in {
            "repair",
            "pdfa",
        }:
            doc = fitz.open(paths[0])

            out = outpath(".pdf")

            doc.save(
                out,
                garbage=4,
                deflate=True,
                clean=True,
            )

            filename = (
                "repaired.pdf"
                if tool == "repair"
                else "archive-copy.pdf"
            )

            return FileResponse(
                out,
                filename=filename,
            )


        if tool == "page-numbers":
            doc = fitz.open(paths[0])

            for number, page in enumerate(
                doc,
                page_number_start,
            ):
                rect = page.rect

                page.insert_text(
                    (
                        rect.width / 2 - 10,
                        rect.height - 18,
                    ),
                    str(number),
                    fontsize=10,
                )

            out = outpath(".pdf")

            doc.save(out)

            return FileResponse(
                out,
                filename="numbered.pdf",
            )


        if tool == "ocr":
            pdf = fitz.open(paths[0])

            doc = Document()

            for i, page in enumerate(pdf):

                pix = page.get_pixmap(
                    matrix=fitz.Matrix(2, 2),
                    alpha=False,
                )

                img = Image.frombytes(
                    "RGB",
                    [pix.width, pix.height],
                    pix.samples,
                )

                recognized = pytesseract.image_to_string(
                    img,
                    lang="eng",
                )

                doc.add_paragraph(recognized)

                if i < len(pdf) - 1:
                    doc.add_page_break()

            out = outpath(".docx")

            doc.save(out)

            return FileResponse(
                out,
                filename="ocr.docx",
            )


        if tool == "compare":
            if len(paths) < 2:
                raise HTTPException(
                    400,
                    "Upload two PDFs to compare.",
                )

            import difflib

            text_a = "\n".join(
                page.get_text()
                for page in fitz.open(paths[0])
            )

            text_b = "\n".join(
                page.get_text()
                for page in fitz.open(paths[1])
            )

            diff = "\n".join(
                difflib.unified_diff(
                    text_a.splitlines(),
                    text_b.splitlines(),
                    fromfile="PDF A",
                    tofile="PDF B",
                    lineterm="",
                )
            )

            out = outpath(".txt")

            out.write_text(
                diff,
                encoding="utf-8",
            )

            return FileResponse(
                out,
                filename="comparison.txt",
            )


        if tool == "redact":
            if not text.strip():
                raise HTTPException(
                    400,
                    "Enter text to redact.",
                )

            doc = fitz.open(paths[0])

            for page in doc:

                for rect in page.search_for(text):
                    page.add_redact_annot(
                        rect,
                        fill=(0, 0, 0),
                    )

                page.apply_redactions()

            out = outpath(".pdf")

            doc.save(out)

            return FileResponse(
                out,
                filename="redacted.pdf",
            )


        if tool == "crop":
            doc = fitz.open(paths[0])

            crop_margin = margin * 72 / 25.4

            for page in doc:

                rect = page.rect

                page.set_cropbox(
                    fitz.Rect(
                        rect.x0 + crop_margin,
                        rect.y0 + crop_margin,
                        rect.x1 - crop_margin,
                        rect.y1 - crop_margin,
                    )
                )

            out = outpath(".pdf")

            doc.save(out)

            return FileResponse(
                out,
                filename="cropped.pdf",
            )


        if tool == "sign":
            doc = fitz.open(paths[0])

            index = max(
                0,
                min(
                    signature_page - 1,
                    len(doc) - 1,
                ),
            )

            page = doc[index]

            page.insert_text(
                (
                    signature_x * 72 / 25.4,
                    signature_y * 72 / 25.4,
                ),
                text or "Signature",
                fontsize=18,
            )

            out = outpath(".pdf")

            doc.save(out)

            return FileResponse(
                out,
                filename="signed.pdf",
            )


        if tool == "html-pdf":

            if paths:
                html = paths[0].read_text(
                    encoding="utf-8",
                    errors="ignore",
                )

            else:
                html = text

            out = outpath(".pdf")

            HTML(
                string=html
            ).write_pdf(out)

            return FileResponse(
                out,
                filename="page.pdf",
            )


        raise HTTPException(
            400,
            "Unknown tool.",
        )

    except HTTPException:
        raise

    except Exception as exc:
        print(
            f"CONVERSION ERROR: {type(exc).__name__}: {exc}",
            flush=True,
        )

        raise HTTPException(
            500,
            f"Conversion failed: {exc}",
        )
