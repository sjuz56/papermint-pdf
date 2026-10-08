"""Opt-in experimental PDF text edit endpoint. Disabled unless explicitly enabled."""
import json
import base64
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from papermint_edit_engine import PdfEditError, TextReplacement, replace_text, inspect_text

router = APIRouter()
MAX_EDIT_BYTES = 10 * 1024 * 1024
MAX_CHANGES = 50
MAX_PAGES = 20
MAX_PAGE_PIXELS = 8_000_000





def _inspect_document(source: Path) -> dict:
    """Render PDF previews outside the async request event loop."""
    import fitz
    with fitz.open(source) as doc:
        if doc.needs_pass:
            raise PdfEditError("Password-protected PDF is unsupported")
        if len(doc) > MAX_PAGES:
            raise PdfEditError("Experimental editor supports up to 20 pages")
        pages = []
        for number in range(len(doc)):
            occurrences = {}
            spans = []
            for span in inspect_text(str(source), number):
                value = span["text"]
                occurrence = occurrences.get(value, 0)
                occurrences[value] = occurrence + 1
                # Text extraction uses unrotated coordinates, while the
                # rendered preview follows the page rotation.
                import fitz
                rotated = fitz.Rect(span["bbox"]) * doc[number].rotation_matrix
                spans.append({**span, "bbox": list(rotated), "occurrence": occurrence})
            if doc[number].rect.width * doc[number].rect.height * 1.4 * 1.4 > MAX_PAGE_PIXELS:
                raise PdfEditError("Page too large for experimental preview")
            pix = doc[number].get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
            pages.append({
                "image": "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode("ascii"),
                "page": number,
                "width": pix.width / 1.4,
                "height": pix.height / 1.4,
                "spans": spans[:3000],
            })
    return {"pages": pages}

@router.post("/api/experimental/inspect-pdf")
async def inspect_pdf_experimental(file: UploadFile = File(...)):
    """Return server-authoritative selectable spans for the browser editor."""
    if os.getenv("PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF") != "1":
        raise HTTPException(status_code=404, detail="Not available")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="A PDF file is required")
    with tempfile.TemporaryDirectory(prefix="pdfaspect-inspect-") as folder:
        source = Path(folder) / "input.pdf"
        total = 0
        with source.open("wb") as dest:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_EDIT_BYTES:
                    raise HTTPException(status_code=413, detail="PDF exceeds 10 MB")
                dest.write(chunk)
        with source.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise HTTPException(status_code=400, detail="Invalid PDF header")
        try:
            return await run_in_threadpool(_inspect_document, source)
        except (PdfEditError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail="PDF could not be inspected") from exc


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
        # Only the server chooses a Unicode font. Never accept filesystem paths
        # from uploaded JSON.
        unicode_font = next((p for p in (
            Path(os.getenv("PAPERMINT_EDIT_UNICODE_FONT", "")) if os.getenv("PAPERMINT_EDIT_UNICODE_FONT") else None,
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        ) if p is not None and p.is_file()), None)
        for item in parsed:
            if not isinstance(item, dict) or set(item) != {"page", "old_text", "new_text", "occurrence"}:
                raise ValueError("Invalid change format")
            if type(item["page"]) is not int or type(item["occurrence"]) is not int:
                raise ValueError("Page and occurrence must be integers")
            if not all(isinstance(item[k], str) and len(item[k]) <= 2000 for k in ("old_text", "new_text")):
                raise ValueError("Invalid text")
            if any(ord(char) > 127 for char in item["new_text"]):
                if unicode_font is None:
                    raise ValueError("Unicode font is not configured on this server")
                edits.append(TextReplacement(**item, font_file=str(unicode_font)))
            else:
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
        with source.open("rb") as stream:
            header = stream.read(5)
        if total < 5 or header != b"%PDF-":
            raise HTTPException(status_code=400, detail="Invalid PDF header")
        try:
            import fitz
            with fitz.open(source) as doc:
                if len(doc) > MAX_PAGES:
                    raise PdfEditError("Experimental editor supports up to 20 pages")
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
