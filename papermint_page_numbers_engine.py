"""Page-numbering support for PaperMint PDFs."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

import fitz


MAX_PAGES = 1_000
POSITIONS = {
    "top-left": ("top", "left"),
    "top-center": ("top", "center"),
    "top-right": ("top", "right"),
    "bottom-left": ("bottom", "left"),
    "bottom-center": ("bottom", "center"),
    "bottom-right": ("bottom", "right"),
}
FORMATS = {"number", "page", "page-total"}


class PageNumbersError(RuntimeError):
    """Page numbers could not be added to the PDF."""


def _label(number: int, final_number: int, format_name: str) -> str:
    if format_name == "number":
        return str(number)
    if format_name == "page":
        return f"Page {number}"
    return f"Page {number} of {final_number}"


def _number_point(
    page: fitz.Page,
    text_width: float,
    vertical: str,
    horizontal: str,
    font_size: float,
) -> fitz.Point:
    width = page.rect.width
    height = page.rect.height
    side_margin = min(36.0, max(10.0, width * 0.06))
    edge_margin = min(24.0, max(8.0, height * 0.025))

    if horizontal == "left":
        x = side_margin
    elif horizontal == "center":
        x = (width - text_width) / 2
    else:
        x = width - side_margin - text_width

    y = edge_margin + font_size if vertical == "top" else height - edge_margin
    visual_point = fitz.Point(x, y)
    return visual_point * page.derotation_matrix


def add_page_numbers(
    source: str | Path,
    output: str | Path,
    start_number: int = 1,
    position: str = "bottom-center",
    format_name: str = "number",
    skip_first: bool = False,
) -> dict:
    """Add visible page numbers and atomically publish a verified PDF."""
    source_path = Path(source)
    output_path = Path(output)

    if start_number < 0 or start_number > 1_000_000:
        raise PageNumbersError("Start number must be between 0 and 1,000,000.")
    if position not in POSITIONS:
        raise PageNumbersError("Choose a valid page-number position.")
    if format_name not in FORMATS:
        raise PageNumbersError("Choose a valid page-number format.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    document: fitz.Document | None = None

    try:
        try:
            document = fitz.open(source_path)
        except Exception as exc:
            raise PageNumbersError("The uploaded file is not a readable PDF.") from exc

        if document.needs_pass:
            raise PageNumbersError("Unlock the PDF before adding page numbers.")

        input_pages = document.page_count
        if input_pages < 1:
            raise PageNumbersError("The PDF does not contain any pages.")
        if input_pages > MAX_PAGES:
            raise PageNumbersError(f"The PDF can contain at most {MAX_PAGES} pages.")

        first_page_index = 1 if skip_first else 0
        numbered_pages = input_pages - first_page_index
        final_number = start_number + max(numbered_pages - 1, 0)
        vertical, horizontal = POSITIONS[position]
        font_size = 10.0

        for page_index in range(first_page_index, input_pages):
            page = document[page_index]
            number = start_number + page_index - first_page_index
            label = _label(number, final_number, format_name)
            text_width = fitz.get_text_length(label, fontname="helv", fontsize=font_size)
            point = _number_point(
                page,
                text_width,
                vertical,
                horizontal,
                font_size,
            )
            inserted = page.insert_text(
                point,
                label,
                fontname="helv",
                fontsize=font_size,
                color=(0.18, 0.20, 0.24),
                rotate=page.rotation,
                overlay=True,
            )
            if inserted < 0:
                raise PageNumbersError("A page is too small to fit its page number.")

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
                raise PageNumbersError("The numbered PDF failed its final integrity check.")

        os.replace(temporary_path, output_path)
        temporary_path = None

        return {
            "pages": input_pages,
            "numbered_pages": numbered_pages,
            "start_number": start_number,
            "position": position,
            "format": format_name,
            "skipped_first_page": skip_first,
        }
    except PageNumbersError:
        raise
    except Exception as exc:
        raise PageNumbersError("Page numbers could not be added to this PDF.") from exc
    finally:
        if document is not None:
            document.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
