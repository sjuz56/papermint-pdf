from pathlib import Path
import shutil
import uuid

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse

from papermint_v28_engine import (
    V28JobManager,
    V28Error,
    V28QueueFull,
    V28Rejected,
)

BASE = Path(__file__).parent
TMP = BASE / "tmp"
UPLOAD_DIR = TMP / "pdf_word_uploads"
OUTPUT_DIR = TMP / "pdf_word_v28"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter()

manager = V28JobManager(OUTPUT_DIR)


def _save_upload(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    path = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    with path.open("wb") as f:
        shutil.copyfileobj(upload.file, f)
    return path


def _safe_delete(path_value):
    if not path_value:
        return
    try:
        Path(path_value).unlink(missing_ok=True)
    except Exception:
        pass


def _public_status(status: str) -> str:
    return {
        "queued": "queued",
        "running": "processing",
        "completed": "done",
        "failed": "error",
    }.get(status, status)


def _cleanup_input_if_finished(job_id: str):
    try:
        job = manager.result(job_id)
    except KeyError:
        return
    if job.get("status") in {"completed", "failed"}:
        _safe_delete(job.get("input"))


@router.post("/api/pdf-word/start")
async def pdf_word_start(file: UploadFile = File(...)):
    manager.cleanup_expired()

    if not file.filename:
        raise HTTPException(400, "No file uploaded.")

    if Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(400, "Please upload a PDF file.")

    source = _save_upload(file)

    try:
        job = manager.submit(source, qa=False)
    except V28QueueFull as exc:
        _safe_delete(source)
        raise HTTPException(503, str(exc))
    except V28Rejected as exc:
        _safe_delete(source)
        raise HTTPException(400, str(exc))
    except V28Error as exc:
        _safe_delete(source)
        raise HTTPException(500, str(exc))
    except Exception as exc:
        _safe_delete(source)
        raise HTTPException(500, f"Could not start conversion: {exc}")

    return {
        "job_id": job["job_id"],
        "status": _public_status(job["status"]),
        "queue_depth": job.get("queue_depth", 0),
    }


@router.get("/api/pdf-word/status/{job_id}")
def pdf_word_status(job_id: str):
    manager.cleanup_expired()

    try:
        job = manager.status(job_id)
    except KeyError:
        raise HTTPException(404, "Conversion job not found.")

    _cleanup_input_if_finished(job_id)

    error = job.get("error")
    error_message = None
    if isinstance(error, dict):
        error_message = error.get("message") or error.get("error")
    elif error:
        error_message = str(error)

    return {
        "job_id": job_id,
        "status": _public_status(job["status"]),
        "error": error_message,
        "queue_depth": job.get("queue_depth", 0),
    }


@router.get("/api/pdf-word/download/{job_id}")
def pdf_word_download(job_id: str):
    try:
        job = manager.result(job_id)
    except KeyError:
        raise HTTPException(404, "Conversion job not found.")

    _cleanup_input_if_finished(job_id)

    status = job.get("status")

    if status == "failed":
        error = job.get("error") or {}
        if isinstance(error, dict):
            message = error.get("message") or error.get("error") or "Conversion failed."
        else:
            message = str(error)
        raise HTTPException(500, message)

    if status != "completed":
        raise HTTPException(409, "Conversion is not finished yet.")

    output = job.get("output")
    if not output or not Path(output).exists():
        raise HTTPException(404, "Converted file no longer exists.")

    return FileResponse(
        path=str(output),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="converted.docx",
    )
