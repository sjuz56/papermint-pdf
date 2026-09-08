"""Visual text-signature support for PaperMint PDFs."""

from __future__ import annotations

import math
import os
from pathlib import Path
import tempfile

import fitz


MM_TO_POINTS = 72 / 25.4
MAX_SIGNATURE_LENGTH = 200
MAX_PAGES = 1_000
FONT_CANDIDATES = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
)


class SignError(RuntimeError):
    """A PDF could not be signed with the requested visual signature."""


def _font_file() -> Path | None:
    return next((path for path in FONT_CANDIDATES if path.is_file()), None)


def sign_pdf(
    source: str | Path,
    output: str | Path,
    signature_text: str,
    page_number: int,
    x_mm: float,
    y_mm: float,
) -> dict:
    """Place a visible text signature on one page and atomically publish a PDF."""
    source_path = Path(source)
    output_path = Path(output)
    text = (signature_text or "").strip()

    if not text:
        raise SignError("Please enter the signature text.")
    if len(text) > MAX_SIGNATURE_LENGTH:
        raise SignError(f"Signature text can contain at most {MAX_SIGNATURE_LENGTH} characters.")
    if "\x00" in text or any(ord(character) < 32 and character not in "\t" for character in text):
        raise SignError("Signature text contains unsupported characters.")
    if page_number < 1:
        raise SignError("Page number must be at least 1.")
    if not all(math.isfinite(value) for value in (x_mm, y_mm)):
        raise SignError("Signature position must be a finite number.")
    if x_mm < 0 or y_mm < 0:
        raise SignError("Signature position cannot be negative.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    document: fitz.Document | None = None

    try:
        try:
            document = fitz.open(source_path)
        except Exception as exc:
            raise SignError("The uploaded file is not a readable PDF.") from exc

        if document.needs_pass:
            raise SignError("Unlock the PDF before adding a signature.")
        input_pages = document.page_count
        if input_pages < 1:
            raise SignError("The PDF does not contain any pages.")
        if input_pages > MAX_PAGES:
            raise SignError(f"The PDF can contain at most {MAX_PAGES} pages.")
        if page_number > input_pages:
            raise SignError(
                f"Page {page_number} does not exist. This PDF has {input_pages} pages."
            )

        page = document[page_number - 1]
        point = fitz.Point(x_mm * MM_TO_POINTS, y_mm * MM_TO_POINTS)
        if point.x >= page.rect.width or point.y >= page.rect.height:
            raise SignError("The signature position is outside the selected page.")

        font_file = _font_file()
        font_name = "papermint-signature" if font_file else "heit"
        if font_file:
            page.insert_font(fontname=font_name, fontfile=str(font_file))

        inserted = page.insert_text(
            point,
            text,
            fontname=font_name,
            fontfile=str(font_file) if font_file else None,
            fontsize=18,
            color=(0.08, 0.08, 0.12),
            overlay=True,
        )
        if inserted < 0:
            raise SignError("The signature does not fit at the selected position.")

        fd, temp_name = tempfile.mkstemp(
            prefix=f".{output_path.stem}-",
            suffix=".pdf",
            dir=output_path.parent,
        )
        os.close(fd)
        temporary_path = Path(temp_name)
        document.save(temporary_path, garbage=4, deflate=True, clean=True)
        document.close()
        document = None

        with fitz.open(temporary_path) as check:
            if check.needs_pass or check.page_count != input_pages:
                raise SignError("The signed PDF failed its final integrity check.")

        os.replace(temporary_path, output_path)
        temporary_path = None

        return {
            "pages": input_pages,
            "signed_page": page_number,
            "signature_type": "visual_text",
        }
    except SignError:
        raise
    except Exception as exc:
        raise SignError("The signature could not be added to this PDF.") from exc
    finally:
        if document is not None:
            document.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
