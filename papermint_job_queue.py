"""Redis-backed background queue for PaperMint's synchronous document tools."""

from __future__ import annotations

import os
from pathlib import Path
import threading
from typing import Iterable

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, get_current_job
from rq.job import Job
from rq.exceptions import NoSuchJobError

from papermint_merge_engine import MergeError, merge_pdfs
from papermint_split_engine import SplitError, split_pdf
from papermint_compress_engine import CompressError, compress_pdf
from papermint_word_pdf_engine import WordPdfError, word_to_pdf
from papermint_rotate_engine import RotateError, rotate_pdf
from papermint_organize_engine import OrganizeError, organize_pdf
from papermint_protect_engine import ProtectError, protect_pdf
from papermint_unlock_engine import UnlockError, unlock_pdf
from papermint_sign_engine import SignError, sign_pdf
from papermint_watermark_engine import WatermarkError, watermark_pdf
from papermint_page_numbers_engine import PageNumbersError, add_page_numbers
from papermint_pdf_ppt_engine import PdfPowerPointError, pdf_to_powerpoint


QUEUE_NAME = os.getenv("PAPERMINT_QUEUE_NAME", "papermint")
QUEUE_MAX_WAITING = max(1, int(os.getenv("PAPERMINT_QUEUE_MAX_WAITING", "20")))
JOB_TIMEOUT = max(60, int(os.getenv("PAPERMINT_TOOL_JOB_TIMEOUT", "600")))
JOB_TTL = max(JOB_TIMEOUT, int(os.getenv("PAPERMINT_TOOL_JOB_TTL", "3600")))
RESULT_TTL = max(300, int(os.getenv("PAPERMINT_TOOL_RESULT_TTL", "3600")))
RESULT_FILE_RETENTION_SECONDS = max(
    300,
    int(os.getenv("PAPERMINT_RESULT_RETENTION_SECONDS", "1800")),
)


class QueueUnavailable(RuntimeError):
    """Redis cannot currently accept jobs."""


class QueueCapacityReached(RuntimeError):
    """The bounded waiting queue is full."""


KNOWN_TOOL_ERRORS = (
    MergeError,
    SplitError,
    CompressError,
    WordPdfError,
    RotateError,
    OrganizeError,
    ProtectError,
    UnlockError,
    SignError,
    WatermarkError,
    PageNumbersError,
    PdfPowerPointError,
)


def _delete_files(paths: Iterable[str | Path]) -> None:
    for path in paths:
        if not path:
            continue
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


def _schedule_result_cleanup(output: str | Path) -> None:
    timer = threading.Timer(
        RESULT_FILE_RETENTION_SECONDS,
        _delete_files,
        args=([output],),
    )
    timer.daemon = True
    timer.start()


def redis_connection() -> Redis:
    url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    return Redis.from_url(
        url,
        socket_connect_timeout=3,
        socket_timeout=5,
        health_check_interval=30,
    )


def queue_connection(*, verify: bool = True) -> tuple[Redis, Queue]:
    connection = redis_connection()
    try:
        if verify:
            connection.ping()
        return connection, Queue(QUEUE_NAME, connection=connection)
    except RedisError as exc:
        raise QueueUnavailable("The processing queue is temporarily unavailable.") from exc


def enqueue_tool_job(
    tool: str,
    sources: Iterable[str | Path],
    output: str | Path,
    *,
    pages: str = "",
    rotation: int = 90,
    password: str = "",
    signature_text: str = "",
    signature_page: int = 1,
    signature_x: float = 30,
    signature_y: float = 30,
    watermark_text: str = "",
    page_number_start: int = 1,
    page_number_position: str = "bottom-center",
    page_number_format: str = "number",
    page_number_skip_first: bool = False,
) -> dict:
    """Add one bounded background job and return its public identifier."""
    connection, queue = queue_connection()
    source_paths = [str(Path(path)) for path in sources]
    output_path = str(Path(output))

    reservation_key = "papermint:enqueue-reservations"
    reserved = False
    try:
        reservation = connection.incr(reservation_key)
        reserved = True
        connection.expire(reservation_key, 120)
        waiting = queue.count
        if waiting + reservation > QUEUE_MAX_WAITING:
            raise QueueCapacityReached(
                "The server is busy. Please try again after a current job finishes."
            )
        job = queue.enqueue_call(
            func=process_tool_job,
            kwargs={
                "tool": tool,
                "sources": source_paths,
                "output": output_path,
                "pages": pages,
                "rotation": rotation,
                "password": password,
                "signature_text": signature_text,
                "signature_page": signature_page,
                "signature_x": signature_x,
                "signature_y": signature_y,
                "watermark_text": watermark_text,
                "page_number_start": page_number_start,
                "page_number_position": page_number_position,
                "page_number_format": page_number_format,
                "page_number_skip_first": page_number_skip_first,
            },
            timeout=JOB_TIMEOUT,
            ttl=JOB_TTL,
            result_ttl=RESULT_TTL,
            failure_ttl=RESULT_TTL,
        )
        position = queue.get_job_position(job.id)
        if position is not None:
            position += 1
    except QueueCapacityReached:
        raise
    except RedisError as exc:
        raise QueueUnavailable("The processing queue is temporarily unavailable.") from exc
    finally:
        if reserved:
            try:
                remaining = connection.decr(reservation_key)
                if remaining <= 0:
                    connection.delete(reservation_key)
            except RedisError:
                pass

    return {
        "job_id": job.id,
        "status": "queued",
        "queue_position": position,
        "queue_depth": queue.count,
    }


def process_tool_job(
    *,
    tool: str,
    sources: list[str],
    output: str,
    pages: str = "",
    rotation: int = 90,
    password: str = "",
    signature_text: str = "",
    signature_page: int = 1,
    signature_x: float = 30,
    signature_y: float = 30,
    watermark_text: str = "",
    page_number_start: int = 1,
    page_number_position: str = "bottom-center",
    page_number_format: str = "number",
    page_number_skip_first: bool = False,
) -> dict:
    """Execute one job inside an RQ worker process."""
    job = get_current_job()
    if job:
        job.meta["public_status"] = "processing"
        job.save_meta()

    succeeded = False
    try:
        if tool == "merge":
            report = merge_pdfs(sources, output)
            download_name = "merged.pdf"
            media_type = "application/pdf"
        elif tool == "split":
            report = split_pdf(sources[0], output, pages)
            download_name = "split.zip"
            media_type = "application/zip"
        elif tool == "compress":
            report = compress_pdf(sources[0], output)
            download_name = "compressed.pdf"
            media_type = "application/pdf"
        elif tool == "word-pdf":
            report = word_to_pdf(sources[0], output)
            download_name = "converted.pdf"
            media_type = "application/pdf"
        elif tool == "rotate":
            report = rotate_pdf(sources[0], output, rotation)
            download_name = "rotated.pdf"
            media_type = "application/pdf"
        elif tool == "organize":
            report = organize_pdf(sources[0], output, pages)
            download_name = "organized.pdf"
            media_type = "application/pdf"
        elif tool == "protect":
            report = protect_pdf(sources[0], output, password)
            download_name = "protected.pdf"
            media_type = "application/pdf"
        elif tool == "unlock":
            report = unlock_pdf(sources[0], output, password)
            download_name = "unlocked.pdf"
            media_type = "application/pdf"
        elif tool == "sign":
            report = sign_pdf(
                sources[0],
                output,
                signature_text,
                signature_page,
                signature_x,
                signature_y,
            )
            download_name = "signed.pdf"
            media_type = "application/pdf"
        elif tool == "watermark":
            report = watermark_pdf(sources[0], output, watermark_text)
            download_name = "watermarked.pdf"
            media_type = "application/pdf"
        elif tool == "page-numbers":
            report = add_page_numbers(
                sources[0],
                output,
                page_number_start,
                page_number_position,
                page_number_format,
                page_number_skip_first,
            )
            download_name = "numbered.pdf"
            media_type = "application/pdf"
        elif tool == "pdf-ppt":
            report = pdf_to_powerpoint(sources[0], output)
            download_name = "converted.pptx"
            media_type = (
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            )
        else:
            raise RuntimeError("Unsupported queued tool.")
        succeeded = True
    except KNOWN_TOOL_ERRORS as exc:
        if job:
            job.meta["public_error"] = str(exc)
            job.save_meta()
        _delete_files([*sources, output])
        raise
    except Exception:
        if job:
            job.meta["public_error"] = "The document could not be processed."
            job.save_meta()
        _delete_files([*sources, output])
        raise
    finally:
        if succeeded:
            # The uploaded originals are no longer needed once the result exists.
            _delete_files(sources)
            # Keep only the downloadable result, and only for a short window.
            _schedule_result_cleanup(output)

        # The password is needed only while queued/running. Remove it from the
        # persisted RQ job data immediately after processing finishes.
        if job and (password or signature_text or watermark_text):
            try:
                clean_kwargs = dict(job.kwargs or {})
                clean_kwargs["password"] = ""
                clean_kwargs["signature_text"] = ""
                clean_kwargs["watermark_text"] = ""
                job.kwargs = clean_kwargs
                job.save()
            except Exception:
                pass

    return {
        "ok": True,
        "tool": tool,
        "sources": [],
        "output": output,
        "download_name": download_name,
        "media_type": media_type,
        "report": report,
    }


def fetch_job(job_id: str) -> Job:
    connection, _ = queue_connection()
    try:
        return Job.fetch(job_id, connection=connection)
    except NoSuchJobError as exc:
        raise KeyError(job_id) from exc


def public_job_status(job: Job) -> dict:
    status_value = job.get_status(refresh=True)
    status = getattr(status_value, "value", str(status_value))
    mapping = {
        "queued": "queued",
        "deferred": "queued",
        "scheduled": "queued",
        "ready_to_enqueue": "queued",
        "rate_limited": "queued",
        "started": "processing",
        "finished": "done",
        "failed": "error",
        "stopped": "error",
        "canceled": "error",
    }
    public_status = mapping.get(status, status)
    position = None
    if public_status == "queued":
        try:
            _, queue = queue_connection(verify=False)
            position = queue.get_job_position(job.id)
            if position is not None:
                position += 1
        except Exception:
            position = None

    error = None
    if public_status == "error":
        error = job.meta.get("public_error") or "The document could not be processed."

    return {
        "job_id": job.id,
        "status": public_status,
        "queue_position": position,
        "error": error,
    }
