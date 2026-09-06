from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from pathlib import Path
from typing import List
import shutil
import threading
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


# ============================================================
# PAPERMINT WEB + V28 PDF -> WORD ENGINE
# ============================================================

BASE = Path(__file__).parent
TMP = BASE / "tmp"
TMP.mkdir(exist_ok=True)

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


def save_upload(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    path = TMP / f"upload-{threading.get_ident()}-{id(upload)}{suffix}"

    # Avoid an accidental name collision if the same request object id is reused.
    counter = 1
    while path.exists():
        path = TMP / f"upload-{threading.get_ident()}-{id(upload)}-{counter}{suffix}"
        counter += 1

    with path.open("wb") as f:
        shutil.copyfileobj(upload.file, f)

    return path


def _delete_paths(paths) -> None:
    for path in paths:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass


# ============================================================
# MERGE PDF API
# ============================================================


@app.post("/api/convert")
async def convert_tool(
    tool: str = Form(...),
    files: List[UploadFile] = File(default=[]),
    pages: str = Form(""),
):
    # Other lightweight tools will be added here one by one after testing.
    if tool not in {"merge", "split"}:
        raise HTTPException(400, "This tool is not available yet.")

    if tool == "merge":
        if len(files) < 2:
            raise HTTPException(400, "Please upload at least two PDF files.")
        if len(files) > 25:
            raise HTTPException(400, "You can merge up to 25 PDF files at once.")
    elif len(files) != 1:
        raise HTTPException(400, "Please upload exactly one PDF file to split.")

    for upload in files:
        if not upload.filename or Path(upload.filename).suffix.lower() != ".pdf":
            raise HTTPException(400, "This tool accepts PDF files only.")

    sources: List[Path] = []
    output = TMP / (
        f"merged-{uuid.uuid4().hex}.pdf"
        if tool == "merge"
        else f"split-{uuid.uuid4().hex}.zip"
    )

    try:
        for upload in files:
            sources.append(save_upload(upload))

        if tool == "merge":
            await run_in_threadpool(merge_pdfs, sources, output)
        else:
            await run_in_threadpool(split_pdf, sources[0], output, pages)
    except (MergeError, SplitError) as exc:
        _delete_paths([*sources, output])
        raise HTTPException(400, str(exc))
    except Exception as exc:
        _delete_paths([*sources, output])
        raise HTTPException(500, f"PDF operation failed: {exc}")

    return FileResponse(
        path=str(output),
        media_type="application/pdf" if tool == "merge" else "application/zip",
        filename="merged.pdf" if tool == "merge" else "split.zip",
        background=BackgroundTask(_delete_paths, [*sources, output]),
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


# ============================================================
# ASYNC PDF -> WORD API
# ============================================================


@app.post("/api/pdf-word/start")
async def pdf_word_start(file: UploadFile = File(...)):
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
    try:
        PDF_WORD_MANAGER.shutdown()
    except Exception:
        pass
