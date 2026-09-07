"""PaperMint Compress PDF engine.

Uses PyMuPDF to optimise PDF objects, fonts, content streams and images while
keeping text and vector graphics as real PDF content. The smallest valid result
is selected so compression never makes the user's download larger.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import fitz


class CompressError(RuntimeError):
    """A user-safe compression failure."""


def _open_pdf(path: Path) -> fitz.Document:
    if path.suffix.lower() != ".pdf":
        raise CompressError("Compress PDF accepts a PDF file only.")
    if not path.is_file() or path.stat().st_size == 0:
        raise CompressError("The uploaded PDF is missing or empty.")

    try:
        document = fitz.open(path)
    except Exception as exc:
        raise CompressError("The uploaded file is not a readable PDF.") from exc

    if not document.is_pdf:
        document.close()
        raise CompressError("The uploaded file is not a readable PDF.")
    if document.needs_pass:
        document.close()
        raise CompressError(
            "Unlock the password-protected PDF before compressing it."
        )
    if document.page_count < 1:
        document.close()
        raise CompressError("The uploaded PDF contains no pages.")

    return document


def _save_optimised(document: fitz.Document, output: Path) -> None:
    document.save(
        output,
        garbage=4,
        clean=False,
        deflate=True,
        deflate_images=True,
        deflate_fonts=True,
        use_objstms=1,
        compression_effort=100,
    )


def _page_visual_signal(page: fitz.Page) -> float:
    """Estimate the proportion of non-white pixels in a low-resolution render."""
    pixmap = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), colorspace=fitz.csGRAY)
    samples = pixmap.samples
    if not samples:
        return 0.0
    return sum(value < 245 for value in samples) / len(samples)


def _validate_pdf(path: Path, source: Path, expected_pages: int) -> None:
    try:
        result = fitz.open(path)
        try:
            if not result.is_pdf or result.needs_pass:
                raise CompressError("The compressed PDF failed its integrity check.")
            if result.page_count != expected_pages:
                raise CompressError(
                    "The compressed PDF contains an incorrect number of pages."
                )
            # Loading every page catches damaged page trees before download.
            for page_number in range(result.page_count):
                result.load_page(page_number)

            # A syntactically valid PDF can still lose visible resources. Compare
            # low-resolution renders of representative pages and reject a result
            # that becomes nearly blank.
            original = _open_pdf(source)
            try:
                sample_pages = sorted(
                    {0, expected_pages // 4, expected_pages // 2,
                     (expected_pages * 3) // 4, expected_pages - 1}
                )
                for page_number in sample_pages:
                    before = _page_visual_signal(original.load_page(page_number))
                    after = _page_visual_signal(result.load_page(page_number))
                    if before > 0.005 and after < max(0.001, before * 0.05):
                        raise CompressError(
                            "The compressed PDF failed its visual integrity check."
                        )
            finally:
                original.close()
        finally:
            result.close()
    except CompressError:
        raise
    except Exception as exc:
        raise CompressError("The compressed PDF failed its integrity check.") from exc


def compress_pdf(
    source: str | Path,
    output: str | Path,
    *,
    max_input_mb: float = 100.0,
    max_pages: int = 1000,
) -> dict:
    """Create a balanced compressed PDF and return an integrity report."""
    source_path = Path(source)
    destination = Path(output)

    document = _open_pdf(source_path)
    input_bytes = source_path.stat().st_size
    page_count = document.page_count

    if input_bytes > max_input_mb * 1024 * 1024:
        document.close()
        raise CompressError(f"The uploaded PDF is larger than {max_input_mb:g} MB.")
    if page_count > max_pages:
        document.close()
        raise CompressError(f"The uploaded PDF exceeds the {max_pages}-page limit.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    balanced = destination.with_name(destination.name + ".balanced")
    lossless = destination.with_name(destination.name + ".lossless")

    try:
        # Downsample only high-resolution images. Text, vectors and ordinary
        # screenshots remain untouched; JPEG quality 75 is a balanced web default.
        rewrite_images = getattr(document, "rewrite_images", None)
        if callable(rewrite_images):
            rewrite_images(
                dpi_threshold=190,
                dpi_target=150,
                quality=75,
                lossy=True,
                lossless=True,
                bitonal=False,
                color=True,
                gray=True,
            )
        _save_optimised(document, balanced)
        document.close()

        # Also create a lossless-only candidate. On PDFs without large images it
        # can be smaller than the balanced rewrite.
        lossless_document = _open_pdf(source_path)
        try:
            _save_optimised(lossless_document, lossless)
        finally:
            lossless_document.close()

        candidates = [balanced, lossless, source_path]
        smallest = min(candidates, key=lambda path: path.stat().st_size)

        temporary = destination.with_name(destination.name + ".partial")
        shutil.copyfile(smallest, temporary)
        _validate_pdf(temporary, source_path, page_count)
        temporary.replace(destination)
    except CompressError:
        raise
    except Exception as exc:
        raise CompressError(f"PDF compression failed: {exc}") from exc
    finally:
        try:
            document.close()
        except Exception:
            pass
        balanced.unlink(missing_ok=True)
        lossless.unlink(missing_ok=True)
        destination.with_name(destination.name + ".partial").unlink(missing_ok=True)

    output_bytes = destination.stat().st_size
    saved_bytes = max(0, input_bytes - output_bytes)

    return {
        "ok": True,
        "output": str(destination),
        "pages": page_count,
        "input_bytes": input_bytes,
        "output_bytes": output_bytes,
        "saved_bytes": saved_bytes,
        "saved_percent": round((saved_bytes / input_bytes) * 100, 1),
    }
