"""Visible text-watermark support for PaperMint PDFs."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

import fitz


MAX_WATERMARK_LENGTH = 100
MAX_PAGES = 1_000
FONT_CANDIDATES = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
)


class WatermarkError(RuntimeError):
    """A PDF could not be watermarked with the requested text."""


def _font_file() -> Path | None:
    return next((path for path in FONT_CANDIDATES if path.is_file()), None)


def watermark_pdf(
    source: str | Path,
    output: str | Path,
    watermark_text: str,
) -> dict:
    """Add one centered diagonal visual watermark to every PDF page."""
    source_path = Path(source)
    output_path = Path(output)
    text = (watermark_text or "").strip()

    if not text:
        raise WatermarkError("Please enter the watermark text.")
    if len(text) > MAX_WATERMARK_LENGTH:
        raise WatermarkError(
            f"Watermark text can contain at most {MAX_WATERMARK_LENGTH} characters."
        )
    if "\x00" in text or any(ord(character) < 32 for character in text):
        raise WatermarkError("Watermark text contains unsupported characters.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    document: fitz.Document | None = None

    try:
        try:
            document = fitz.open(source_path)
        except Exception as exc:
            raise WatermarkError("The uploaded file is not a readable PDF.") from exc

        if document.needs_pass:
            raise WatermarkError("Unlock the PDF before adding a watermark.")

        input_pages = document.page_count
        if input_pages < 1:
            raise WatermarkError("The PDF does not contain any pages.")
        if input_pages > MAX_PAGES:
            raise WatermarkError(f"The PDF can contain at most {MAX_PAGES} pages.")

        font_file = _font_file()
        measurement_font = fitz.Font(fontfile=str(font_file)) if font_file else fitz.Font("hebo")
        font_name = "papermint-watermark" if font_file else "hebo"

        for page in document:
            if font_file:
                page.insert_font(fontname=font_name, fontfile=str(font_file))

            page_rect = page.rect
            target_width = max(72.0, page_rect.width * 0.72)
            unit_width = max(measurement_font.text_length(text, fontsize=1), 1.0)
            font_size = min(72.0, max(18.0, target_width / unit_width))
            text_width = measurement_font.text_length(text, fontsize=font_size)
            center = fitz.Point(page_rect.width / 2, page_rect.height / 2)
            start = fitz.Point(center.x - text_width / 2, center.y + font_size * 0.35)

            page.insert_text(
                start,
                text,
                fontname=font_name,
                fontfile=str(font_file) if font_file else None,
                fontsize=font_size,
                color=(0.32, 0.36, 0.42),
                fill_opacity=0.18,
                morph=(center, fitz.Matrix(1, 1).prerotate(45)),
                overlay=True,
            )

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
                raise WatermarkError("The watermarked PDF failed its final integrity check.")

        os.replace(temporary_path, output_path)
        temporary_path = None

        return {
            "pages": input_pages,
            "watermarked_pages": input_pages,
            "watermark_type": "visual_text",
        }
    except WatermarkError:
        raise
    except Exception as exc:
        raise WatermarkError("The watermark could not be added to this PDF.") from exc
    finally:
        if document is not None:
            document.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
