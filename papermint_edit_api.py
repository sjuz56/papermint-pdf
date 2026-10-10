"""Opt-in experimental PDF text edit endpoint. Disabled unless explicitly enabled."""
import json
import base64
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, FileResponse
from starlette.concurrency import run_in_threadpool

from papermint_edit_engine import (PdfEditError, TextReplacement, replace_text, inspect_page,
                                   editing_bbox, editing_fonts, editing_font_path, EDIT_FONT_FAMILIES)

router = APIRouter()
MAX_EDIT_BYTES = 10 * 1024 * 1024
MAX_CHANGES = 50
MAX_PAGES = 20
MAX_PAGE_PIXELS = 8_000_000
MAX_TOTAL_PREVIEW_PIXELS = 24_000_000
MAX_PREVIEW_SPANS = 3_000





def _inspect_document(source: Path) -> dict:
    """Render PDF previews outside the async request event loop."""
    import fitz
    with fitz.open(source) as doc:
        if doc.needs_pass:
            raise PdfEditError("Password-protected PDF is unsupported")
        if len(doc) > MAX_PAGES:
            raise PdfEditError("Experimental editor supports up to 20 pages")
        pages = []
        total_pixels = 0
        for number in range(len(doc)):
            if doc[number].rotation != 0:
                raise PdfEditError("Editing rotated PDF pages is not supported yet")
            page_pixels = doc[number].rect.width * doc[number].rect.height * 1.4 * 1.4
            if page_pixels > MAX_PAGE_PIXELS:
                raise PdfEditError("Page too large for experimental preview")
            total_pixels += page_pixels
            if total_pixels > MAX_TOTAL_PREVIEW_PIXELS:
                raise PdfEditError("PDF exceeds experimental preview rendering limit")
            occurrences = {}
            spans = []
            blocks = inspect_page(doc[number])
            if len(blocks) > MAX_PREVIEW_SPANS:
                raise PdfEditError("Too many text spans on a page")
            drawings = doc[number].get_drawings()
            for raw in blocks:
                span = {
                    "text": raw["text"], "bbox": list(raw["bbox"]),
                    "font": raw["font"], "size": raw["size"], "color": raw["color"],
                    "flags": raw.get("flags", 0),
                    "edit_bbox": editing_bbox(doc[number], raw, blocks, drawings),
                }
                value = span["text"]
                occurrence = occurrences.get(value, 0)
                occurrences[value] = occurrence + 1
                # Text extraction uses unrotated coordinates, while the
                # rendered preview follows the page rotation.
                rotated = fitz.Rect(span["bbox"]) * doc[number].rotation_matrix
                if len(spans) >= MAX_PREVIEW_SPANS:
                    raise PdfEditError("Too many text spans on a page")
                spans.append({**span, "bbox": list(rotated), "occurrence": occurrence})
            pix = doc[number].get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
            pages.append({
                "image": "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode("ascii"),
                "page": number,
                "width": pix.width / 1.4,
                "height": pix.height / 1.4,
                "spans": spans,
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
            result = await run_in_threadpool(_inspect_document, source)
            return Response(
                content=json.dumps(result),
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )
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
    if len(changes) > 100_000:
        raise HTTPException(status_code=413, detail="Too many edit instructions")
    try:
        parsed = json.loads(changes)
        if not isinstance(parsed, list) or not 1 <= len(parsed) <= MAX_CHANGES:
            raise ValueError("Provide 1 to 50 changes")
        edits = []
        # The engine selects the original font or a server-owned fallback.
        # Never accept filesystem paths from uploaded JSON.
        for item in parsed:
            required = {"page", "old_text", "new_text", "occurrence"}
            if (not isinstance(item, dict) or not required.issubset(item)
                    or set(item) - required - {"bold", "width", "italic", "font_family", "font_size", "color"}):
                raise ValueError("Invalid change format")
            if type(item["page"]) is not int or type(item["occurrence"]) is not int:
                raise ValueError("Page and occurrence must be integers")
            if not all(isinstance(item[k], str) and len(item[k]) <= 2000 for k in ("old_text", "new_text")):
                raise ValueError("Invalid text")
            if "bold" in item and type(item["bold"]) is not bool:
                raise ValueError("Bold must be a boolean")
            if "italic" in item and type(item["italic"]) is not bool:
                raise ValueError("Italic must be a boolean")
            if "font_family" in item and (not isinstance(item["font_family"], str)
                    or item["font_family"] not in EDIT_FONT_FAMILIES):
                raise ValueError("Unknown font family")
            for field in ("font_size", "color"):
                if field in item and type(item[field]) not in ((int, float) if field == "font_size" else (int,)):
                    raise ValueError("Invalid " + field)
            if "width" in item and type(item["width"]) not in (int, float):
                raise ValueError("Text width must be a number")
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
            result = await run_in_threadpool(replace_text, str(source), str(output), edits)
        except (PdfEditError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail="PDF could not be edited") from exc
        return Response(
            content=output.read_bytes(),
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="edited.pdf"', "Cache-Control": "no-store",
                     "X-PDFaspect-Edit-Boxes": json.dumps(result["edit_boxes"]),
                     "X-PDFaspect-Font-Substitutions": str(len(result["font_substitutions"]))},
        )


@router.get("/api/experimental/edit-pdf-fonts")
def list_edit_fonts():
    if os.getenv("PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF") != "1":
        raise HTTPException(status_code=404, detail="Not available")
    return {"fonts": editing_fonts()}


@router.get("/api/experimental/edit-pdf-fonts/{family}/{variant}")
def get_edit_font(family: str, variant: str):
    if os.getenv("PAPERMINT_ENABLE_EXPERIMENTAL_EDIT_PDF") != "1":
        raise HTTPException(status_code=404, detail="Not available")
    variants = {"regular": (False, False), "bold": (True, False),
                "italic": (False, True), "bold-italic": (True, True)}
    if family not in EDIT_FONT_FAMILIES or variant not in variants:
        raise HTTPException(status_code=404, detail="Font not available")
    try:
        path = editing_font_path(family, *variants[variant])
    except PdfEditError as exc:
        raise HTTPException(status_code=404, detail="Font not available") from exc
    return FileResponse(path, media_type="font/ttf", headers={"Cache-Control": "public, max-age=86400"})
