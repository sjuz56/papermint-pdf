from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from pathlib import Path
from typing import List
import os
import shutil
import threading
import time
import uuid

from papermint_v28_engine import (
    V28JobManager,
    V28Policy,
    V28Error,
    V28Rejected,
    V28QueueFull,
)
from papermint_merge_engine import MergeError, merge_pdfs
from papermint_split_engine import SplitError, split_pdf
from papermint_compress_engine import CompressError, compress_pdf
from papermint_word_pdf_engine import WordPdfError, word_to_pdf
from papermint_rotate_engine import RotateError, rotate_pdf
from papermint_organize_engine import OrganizeError, organize_pdf
from papermint_job_queue import (
    QueueCapacityReached,
    QueueUnavailable,
    enqueue_tool_job,
    fetch_job,
    public_job_status,
)


# ============================================================
# PAPERMINT WEB + V28 PDF -> WORD ENGINE
# ============================================================

BASE = Path(__file__).parent
TMP = BASE / "tmp"
TMP.mkdir(exist_ok=True)

MAX_UPLOAD_MB = max(1, int(os.getenv("PAPERMINT_MAX_UPLOAD_MB", "50")))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_REQUEST_MB = max(MAX_UPLOAD_MB, int(os.getenv("PAPERMINT_MAX_REQUEST_MB", "100")))
MAX_REQUEST_BYTES = MAX_REQUEST_MB * 1024 * 1024
TEMP_RETENTION_SECONDS = max(
    3600,
    int(os.getenv("PAPERMINT_TEMP_RETENTION_SECONDS", "43200")),
)
RESULT_RETENTION_SECONDS = max(
    300,
    int(os.getenv("PAPERMINT_RESULT_RETENTION_SECONDS", "1800")),
)
JANITOR_INTERVAL_SECONDS = max(
    30,
    int(os.getenv("PAPERMINT_JANITOR_INTERVAL_SECONDS", "60")),
)
RESULT_FILE_PREFIXES = (
    "merged-",
    "split-",
    "compressed-",
    "word-pdf-",
    "rotated-",
    "organized-",
    "protected-",
    "unlocked-",
    "signed-",
    "watermarked-",
    "numbered-",
    "pdf-ppt-",
    "pdf-excel-",
    "pdf-jpg-",
    "ppt-pdf-",
    "excel-pdf-",
    "image-pdf-",
    "html-pdf-",
    "pdfa-",
    "repaired-",
    "ocr-",
    "comparison-",
    "redacted-",
    "cropped-",
)

PDF_WORD_OUTPUTS = TMP / "pdf-word-results"
PDF_WORD_OUTPUTS.mkdir(exist_ok=True)

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
    ("pdfa", "PDF to PDF/A", "Create a standards-oriented PDF/A-2b archival copy."),
    ("repair", "Repair PDF", "Rewrite a damaged/readable PDF into a fresh file."),
    ("page-numbers", "Page numbers", "Add page numbers to every page."),
    ("scan-pdf", "Scan to PDF", "Convert phone scans/images into a PDF."),
    ("ocr", "OCR PDF", "Recognize English text from scanned PDF pages into Word."),
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
            "id": tool_id,
            "name": name,
            "description": description,
        }
        for tool_id, name, description in TOOLS
    ]


# ============================================================
# V28 RUNTIME
# ============================================================

# These defaults can later be overridden in Render environment variables:
# PAPERMINT_WORKERS=1
# PAPERMINT_QUEUE_SIZE=6
# PAPERMINT_JOB_TIMEOUT=240
# PAPERMINT_MAX_INPUT_MB=50
# PAPERMINT_MAX_PAGES=300
# PAPERMINT_RETENTION_SECONDS=1800
PDF_WORD_POLICY = V28Policy.from_env()
PDF_WORD_MANAGER = V28JobManager(
    output_dir=PDF_WORD_OUTPUTS,
    policy=PDF_WORD_POLICY,
)

# We keep only lightweight web metadata here. The actual queue/status/output
# lifecycle lives inside V28JobManager.
PDF_WORD_META = {}
PDF_WORD_META_LOCK = threading.RLock()
JANITOR_STOP = threading.Event()
JANITOR_THREAD = None


def save_upload(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    path = TMP / f"upload-{threading.get_ident()}-{id(upload)}{suffix}"

    # Avoid an accidental name collision if the same request object id is reused.
    counter = 1
    while path.exists():
        path = TMP / f"upload-{threading.get_ident()}-{id(upload)}-{counter}{suffix}"
        counter += 1

    written = 0
    try:
        with path.open("wb") as stream:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        413,
                        f"Each uploaded file can be up to {MAX_UPLOAD_MB} MB.",
                    )
                stream.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise

    return path


def _delete_paths(paths) -> None:
    for path in paths:
        if not path:
            continue
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass


def cleanup_stale_temp_files() -> None:
    now = time.time()
    try:
        candidates = list(TMP.iterdir())
    except OSError:
        return

    for path in candidates:
        if not path.is_file():
            continue
        try:
            retention = (
                RESULT_RETENTION_SECONDS
                if path.name.startswith(RESULT_FILE_PREFIXES)
                else TEMP_RETENTION_SECONDS
            )
            if path.stat().st_mtime < now - retention:
                path.unlink(missing_ok=True)
        except OSError:
            pass


# ============================================================
# MERGE PDF API
# ============================================================


@app.post("/api/convert")
async def convert_tool(
    tool: str = Form(...),
    files: List[UploadFile] = File(default=[]),
    pages: str = Form(""),
    rotation: int = Form(90),
    password: str = Form(""),
    password_confirm: str = Form(""),
    text: str = Form(""),
    signature_page: int = Form(1),
    signature_x: float = Form(30),
    signature_y: float = Form(30),
    page_number_start: int = Form(1),
    page_number_position: str = Form("bottom-center"),
    page_number_format: str = Form("number"),
    page_number_skip_first: bool = Form(False),
    margin: float = Form(10.0),
):
    cleanup_stale_temp_files()

    if tool not in {
        "merge", "split", "compress", "word-pdf", "rotate", "organize",
        "protect", "unlock", "sign", "watermark", "page-numbers", "pdf-ppt",
        "pdf-excel", "pdf-jpg", "ppt-pdf", "excel-pdf", "jpg-pdf",
        "html-pdf", "pdfa", "repair", "scan-pdf", "ocr", "compare",
        "redact", "crop",
    }:
        raise HTTPException(400, "This tool is not available yet.")

    if tool == "merge":
        if len(files) < 2:
            raise HTTPException(400, "Please upload at least two PDF files.")
        if len(files) > 25:
            raise HTTPException(400, "You can merge up to 25 PDF files at once.")
    elif tool == "compare":
        if len(files) != 2:
            raise HTTPException(400, "Please upload exactly two PDF files to compare.")
    elif tool in {"jpg-pdf", "scan-pdf"}:
        if not files:
            raise HTTPException(400, "Please upload at least one image.")
        if len(files) > 100:
            raise HTTPException(400, "You can combine up to 100 images at once.")
    elif len(files) != 1:
        action = {
            "split": "split",
            "compress": "compress",
            "word-pdf": "convert",
            "rotate": "rotate",
            "organize": "organize",
            "protect": "protect",
            "unlock": "unlock",
            "sign": "sign",
            "watermark": "watermark",
            "page-numbers": "number",
            "pdf-ppt": "convert",
            "pdf-excel": "convert",
            "pdf-jpg": "convert",
            "ppt-pdf": "convert",
            "excel-pdf": "convert",
            "html-pdf": "convert",
            "pdfa": "convert",
            "repair": "repair",
            "ocr": "recognize",
            "redact": "redact",
            "crop": "crop",
        }[tool]
        file_kind = {
            "word-pdf": "Word file",
            "ppt-pdf": "PowerPoint file",
            "excel-pdf": "Excel file",
            "html-pdf": "HTML file",
        }.get(tool, "PDF file")
        raise HTTPException(400, f"Please upload exactly one {file_kind} to {action}.")

    for upload in files:
        suffix = Path(upload.filename or "").suffix.lower()
        if tool == "word-pdf":
            if suffix not in {".doc", ".docx"}:
                raise HTTPException(400, "Word to PDF accepts DOC and DOCX files only.")
        elif tool == "ppt-pdf":
            if suffix not in {".ppt", ".pptx"}:
                raise HTTPException(400, "PowerPoint to PDF accepts PPT and PPTX files only.")
        elif tool == "excel-pdf":
            if suffix not in {".xls", ".xlsx"}:
                raise HTTPException(400, "Excel to PDF accepts XLS and XLSX files only.")
        elif tool in {"jpg-pdf", "scan-pdf"}:
            if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}:
                raise HTTPException(400, "This tool accepts JPG, PNG, WebP and TIFF images.")
        elif tool == "html-pdf":
            if suffix not in {".html", ".htm"}:
                raise HTTPException(400, "HTML to PDF accepts HTML and HTM files only.")
        elif suffix != ".pdf":
            raise HTTPException(400, "This tool accepts PDF files only.")

    if tool == "protect":
        if password != password_confirm:
            raise HTTPException(400, "Passwords do not match.")
        if len(password) < 6:
            raise HTTPException(400, "Password must contain at least 6 characters.")
        if len(password) > 128:
            raise HTTPException(400, "Password can contain at most 128 characters.")
    elif tool == "unlock":
        if not password:
            raise HTTPException(400, "Please enter the PDF password.")
        if len(password) > 128:
            raise HTTPException(400, "Password can contain at most 128 characters.")
    elif tool == "sign":
        if not text.strip():
            raise HTTPException(400, "Please enter the signature text.")
        if len(text.strip()) > 200:
            raise HTTPException(400, "Signature text can contain at most 200 characters.")
        if signature_page < 1:
            raise HTTPException(400, "Page number must be at least 1.")
        if signature_x < 0 or signature_y < 0:
            raise HTTPException(400, "Signature position cannot be negative.")
    elif tool == "watermark":
        if not text.strip():
            raise HTTPException(400, "Please enter the watermark text.")
        if len(text.strip()) > 100:
            raise HTTPException(400, "Watermark text can contain at most 100 characters.")
    elif tool == "page-numbers":
        if page_number_start < 0 or page_number_start > 1_000_000:
            raise HTTPException(400, "Start number must be between 0 and 1,000,000.")
        if page_number_position not in {
            "top-left", "top-center", "top-right",
            "bottom-left", "bottom-center", "bottom-right",
        }:
            raise HTTPException(400, "Choose a valid page-number position.")
        if page_number_format not in {"number", "page", "page-total"}:
            raise HTTPException(400, "Choose a valid page-number format.")
    elif tool == "redact":
        if not text.strip():
            raise HTTPException(400, "Please enter the text to redact.")
        if len(text.strip()) > 200:
            raise HTTPException(400, "Redaction text can contain at most 200 characters.")
    elif tool == "crop":
        if margin < 0 or margin > 100:
            raise HTTPException(400, "Crop margin must be between 0 and 100 mm.")

    sources: List[Path] = []
    if tool == "merge":
        output = TMP / f"merged-{uuid.uuid4().hex}.pdf"
    elif tool == "split":
        output = TMP / f"split-{uuid.uuid4().hex}.zip"
    elif tool == "compress":
        output = TMP / f"compressed-{uuid.uuid4().hex}.pdf"
    elif tool == "rotate":
        output = TMP / f"rotated-{uuid.uuid4().hex}.pdf"
    elif tool == "organize":
        output = TMP / f"organized-{uuid.uuid4().hex}.pdf"
    elif tool == "protect":
        output = TMP / f"protected-{uuid.uuid4().hex}.pdf"
    elif tool == "unlock":
        output = TMP / f"unlocked-{uuid.uuid4().hex}.pdf"
    elif tool == "sign":
        output = TMP / f"signed-{uuid.uuid4().hex}.pdf"
    elif tool == "watermark":
        output = TMP / f"watermarked-{uuid.uuid4().hex}.pdf"
    elif tool == "page-numbers":
        output = TMP / f"numbered-{uuid.uuid4().hex}.pdf"
    elif tool == "pdf-ppt":
        output = TMP / f"pdf-ppt-{uuid.uuid4().hex}.pptx"
    elif tool == "pdf-excel":
        output = TMP / f"pdf-excel-{uuid.uuid4().hex}.xlsx"
    elif tool == "pdf-jpg":
        output = TMP / f"pdf-jpg-{uuid.uuid4().hex}.zip"
    elif tool == "ppt-pdf":
        output = TMP / f"ppt-pdf-{uuid.uuid4().hex}.pdf"
    elif tool == "excel-pdf":
        output = TMP / f"excel-pdf-{uuid.uuid4().hex}.pdf"
    elif tool in {"jpg-pdf", "scan-pdf"}:
        output = TMP / f"image-pdf-{uuid.uuid4().hex}.pdf"
    elif tool == "html-pdf":
        output = TMP / f"html-pdf-{uuid.uuid4().hex}.pdf"
    elif tool == "pdfa":
        output = TMP / f"pdfa-{uuid.uuid4().hex}.pdf"
    elif tool == "repair":
        output = TMP / f"repaired-{uuid.uuid4().hex}.pdf"
    elif tool == "ocr":
        output = TMP / f"ocr-{uuid.uuid4().hex}.docx"
    elif tool == "compare":
        output = TMP / f"comparison-{uuid.uuid4().hex}.txt"
    elif tool == "redact":
        output = TMP / f"redacted-{uuid.uuid4().hex}.pdf"
    elif tool == "crop":
        output = TMP / f"cropped-{uuid.uuid4().hex}.pdf"
    else:
        output = TMP / f"word-pdf-{uuid.uuid4().hex}.pdf"

    try:
        total_upload_bytes = 0
        for upload in files:
            source = save_upload(upload)
            sources.append(source)
            total_upload_bytes += source.stat().st_size
            if total_upload_bytes > MAX_REQUEST_BYTES:
                raise HTTPException(
                    413,
                    f"The combined upload can be up to {MAX_REQUEST_MB} MB.",
                )

        queued = await run_in_threadpool(
            enqueue_tool_job,
            tool,
            sources,
            output,
            pages=pages,
            rotation=rotation,
            password=password,
            signature_text=text,
            signature_page=signature_page,
            signature_x=signature_x,
            signature_y=signature_y,
            watermark_text=text,
            page_number_start=page_number_start,
            page_number_position=page_number_position,
            page_number_format=page_number_format,
            page_number_skip_first=page_number_skip_first,
            redaction_text=text,
            crop_margin=margin,
        )
    except QueueCapacityReached as exc:
        _delete_paths([*sources, output])
        raise HTTPException(429, str(exc))
    except QueueUnavailable as exc:
        _delete_paths([*sources, output])
        raise HTTPException(503, str(exc))
    except HTTPException:
        _delete_paths([*sources, output])
        raise
    except Exception as exc:
        _delete_paths([*sources, output])
        raise HTTPException(500, f"Could not queue the PDF operation: {exc}")

    return queued


@app.get("/api/jobs/status/{job_id}")
def queued_job_status(job_id: str):
    try:
        job = fetch_job(job_id)
        status = public_job_status(job)
    except KeyError:
        raise HTTPException(404, "Processing job not found.")
    except QueueUnavailable as exc:
        raise HTTPException(503, str(exc))

    if status["status"] == "error":
        kwargs = job.kwargs or {}
        _delete_paths([*(kwargs.get("sources") or []), kwargs.get("output")])

    return status


@app.get("/api/jobs/download/{job_id}")
def queued_job_download(job_id: str):
    try:
        job = fetch_job(job_id)
        status = public_job_status(job)
    except KeyError:
        raise HTTPException(404, "Processing job not found.")
    except QueueUnavailable as exc:
        raise HTTPException(503, str(exc))

    if status["status"] == "error":
        raise HTTPException(400, status.get("error") or "Processing failed.")
    if status["status"] != "done":
        raise HTTPException(409, "The document is not ready yet.")

    result = job.return_value(refresh=True)
    if not isinstance(result, dict) or not result.get("ok"):
        raise HTTPException(500, "The processing result is missing.")

    output = Path(result.get("output") or "")
    if not output.is_file():
        raise HTTPException(404, "The processed file has expired.")

    cleanup_paths = [*(result.get("sources") or []), output]
    return FileResponse(
        path=str(output),
        media_type=result.get("media_type") or "application/octet-stream",
        filename=result.get("download_name") or output.name,
        background=BackgroundTask(_delete_paths, cleanup_paths),
    )


def _public_status(internal_status: str) -> str:
    """Keep compatibility with the frontend we already had before V28."""
    mapping = {
        "queued": "queued",
        "running": "processing",
        "completed": "done",
        "failed": "error",
    }
    return mapping.get(internal_status, internal_status)


def _error_message(error) -> str | None:
    if not error:
        return None
    if isinstance(error, dict):
        return error.get("message") or error.get("error") or str(error)
    return str(error)


def _delete_source_for_job(job_id: str) -> None:
    with PDF_WORD_META_LOCK:
        meta = PDF_WORD_META.get(job_id)
        if not meta:
            return
        source = meta.get("source")
        if source:
            try:
                Path(source).unlink(missing_ok=True)
            except Exception:
                pass
            meta["source"] = None


def cleanup_pdf_word_jobs() -> None:
    """Clean finished outputs in V28 and remove no-longer-needed input PDFs."""
    try:
        PDF_WORD_MANAGER.cleanup_expired()
    except Exception:
        pass

    with PDF_WORD_META_LOCK:
        job_ids = list(PDF_WORD_META.keys())

    for job_id in job_ids:
        try:
            status = PDF_WORD_MANAGER.status(job_id)
        except KeyError:
            # V28 already expired this job. Remove any remaining upload metadata/file.
            _delete_source_for_job(job_id)
            with PDF_WORD_META_LOCK:
                PDF_WORD_META.pop(job_id, None)
            continue
        except Exception:
            continue

        if status.get("status") in {"completed", "failed"}:
            _delete_source_for_job(job_id)


def cleanup_janitor_loop() -> None:
    while not JANITOR_STOP.wait(JANITOR_INTERVAL_SECONDS):
        cleanup_stale_temp_files()
        cleanup_pdf_word_jobs()


@app.on_event("startup")
def start_cleanup_janitor():
    global JANITOR_THREAD
    cleanup_stale_temp_files()
    cleanup_pdf_word_jobs()
    JANITOR_STOP.clear()
    if JANITOR_THREAD is None or not JANITOR_THREAD.is_alive():
        JANITOR_THREAD = threading.Thread(
            target=cleanup_janitor_loop,
            name="papermint-file-janitor",
            daemon=True,
        )
        JANITOR_THREAD.start()


# ============================================================
# ASYNC PDF -> WORD API
# ============================================================


@app.post("/api/pdf-word/start")
async def pdf_word_start(file: UploadFile = File(...)):
    cleanup_stale_temp_files()
    cleanup_pdf_word_jobs()

    if not file.filename:
        raise HTTPException(400, "No file uploaded.")

    if Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(400, "Please upload a PDF file.")

    source = save_upload(file)

    try:
        # Production web requests use qa=False. V28 still performs preflight,
        # timeout protection, atomic publication and its final integrity check.
        job = PDF_WORD_MANAGER.submit(source, qa=False)

    except V28QueueFull as exc:
        source.unlink(missing_ok=True)
        raise HTTPException(429, exc.message)

    except V28Rejected as exc:
        source.unlink(missing_ok=True)
        raise HTTPException(400, exc.message)

    except V28Error as exc:
        source.unlink(missing_ok=True)
        raise HTTPException(500, exc.message)

    except Exception as exc:
        source.unlink(missing_ok=True)
        raise HTTPException(500, f"Could not start conversion: {exc}")

    job_id = job["job_id"]
    original_stem = Path(file.filename).stem.strip() or "converted"

    with PDF_WORD_META_LOCK:
        PDF_WORD_META[job_id] = {
            "source": str(source),
            "download_name": f"{original_stem}.docx",
        }

    return {
        "job_id": job_id,
        "status": "queued",
        "queue_depth": job.get("queue_depth", 0),
        "pages": (job.get("preflight") or {}).get("pages"),
    }


@app.get("/api/pdf-word/status/{job_id}")
def pdf_word_status(job_id: str):
    cleanup_pdf_word_jobs()

    try:
        job = PDF_WORD_MANAGER.status(job_id)
    except KeyError:
        raise HTTPException(404, "Conversion job not found.")

    internal_status = job.get("status", "unknown")

    if internal_status in {"completed", "failed"}:
        _delete_source_for_job(job_id)

    return {
        "job_id": job_id,
        "status": _public_status(internal_status),
        "internal_status": internal_status,
        "error": _error_message(job.get("error")),
        "queue_depth": job.get("queue_depth", 0),
        "pages": (job.get("preflight") or {}).get("pages"),
    }


@app.get("/api/pdf-word/download/{job_id}")
def pdf_word_download(job_id: str):
    cleanup_pdf_word_jobs()

    try:
        status = PDF_WORD_MANAGER.status(job_id)
    except KeyError:
        raise HTTPException(404, "Conversion job not found.")

    internal_status = status.get("status")

    if internal_status == "failed":
        raise HTTPException(
            500,
            _error_message(status.get("error")) or "Conversion failed.",
        )

    if internal_status != "completed":
        raise HTTPException(409, "Conversion is not finished yet.")

    try:
        full_job = PDF_WORD_MANAGER.result(job_id)
    except KeyError:
        raise HTTPException(404, "Conversion job not found.")

    output = full_job.get("output")

    if not output:
        raise HTTPException(500, "Converted DOCX path is missing.")

    output_path = Path(output)

    if not output_path.exists():
        raise HTTPException(404, "Converted DOCX file was not found.")

    _delete_source_for_job(job_id)

    with PDF_WORD_META_LOCK:
        meta = PDF_WORD_META.get(job_id, {})
        download_name = meta.get("download_name") or "converted.docx"

    return FileResponse(
        path=str(output_path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=download_name,
    )


@app.on_event("shutdown")
def shutdown_pdf_word_manager():
    JANITOR_STOP.set()
    if JANITOR_THREAD is not None:
        JANITOR_THREAD.join(timeout=2.0)
    try:
        PDF_WORD_MANAGER.shutdown()
    except Exception:
        pass
