"""Opt-in experimental PDF text edit endpoint. Disabled unless explicitly enabled."""
import json
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from papermint_edit_engine import PdfEditError, TextReplacement, replace_text

router = APIRouter()
MAX_EDIT_BYTES = 10 * 1024 * 1024
MAX_CHANGES = 50


@router.post("/api/experimental/edit-pdf")
async def edit_pdf_experimental(file: UploadFile = File(...), changes: str = Form(...)):
    if os.getenv("PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF") != "1":
        raise HTTPException(status_code=404, detail="Not available")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="A PDF file is required")
    try:
        parsed = json.loads(changes)
        if not isinstance(parsed, list) or not 1 <= len(parsed) <= MAX_CHANGES:
            raise ValueError("Provide 1 to 50 changes")
        edits = []
        for item in parsed:
            if not isinstance(item, dict) or set(item) != {"page", "old_text", "new_text", "occurrence"}:
                raise ValueError("Invalid change format")
            if type(item["page"]) is not int or type(item["occurrence"]) is not int:
                raise ValueError("Page and occurrence must be integers")
            if not all(isinstance(item[k], str) and len(item[k]) <= 2000 for k in ("old_text", "new_text")):
                raise ValueError("Invalid text")
            edits.append(TextReplacement(**item))
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with tempfile.TemporaryDirectory(prefix="pdfaspect-edit-") as folder:
        source = Path(folder) / "input.pdf"
        output = Path(folder) / "output.pdf"
        total = 0
        with source.open("wb") as dest:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_EDIT_BYTES:
                    raise HTTPException(status_code=413, detail="PDF exceeds 10 MB")
                dest.write(chunk)
        if total < 5 or source.open("rb").read(5) != b"%PDF-":
            raise HTTPException(status_code=400, detail="Invalid PDF header")
        try:
            await run_in_threadpool(replace_text, str(source), str(output), edits)
        except (PdfEditError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail="PDF could not be edited") from exc
        return Response(
            content=output.read_bytes(),
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="edited.pdf"', "Cache-Control": "no-store"},
        )
