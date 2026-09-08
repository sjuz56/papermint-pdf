"""PaperMint Organize PDF engine.

Creates a new PDF whose pages follow a user-supplied order such as 3,1,2.
Ranges are supported in either direction, for example 1-3 or 5-3.
"""

from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader, PdfWriter


class OrganizeError(RuntimeError):
    """A user-safe PDF organization failure."""


def parse_page_order(value: str, page_count: int, *, max_output_pages: int = 1000) -> list[int]:
    """Parse a one-based page order and return zero-based page indices."""
    text = (value or "").strip().replace(" ", "")
    if not text:
        raise OrganizeError("Enter the new page order, for example 3,1,2.")

    indices: list[int] = []
    for token in text.split(","):
        if not token:
            raise OrganizeError("Use page numbers such as 3,1,2.")

        if re.fullmatch(r"\d+", token):
            numbers = [int(token)]
        else:
            match = re.fullmatch(r"(\d+)-(\d+)", token)
            if not match:
                raise OrganizeError(f"Invalid page number or range: {token}.")
            start, end = map(int, match.groups())
            step = 1 if end >= start else -1
            numbers = list(range(start, end + step, step))

        for number in numbers:
            if number < 1:
                raise OrganizeError("Page numbers start at 1.")
            if number > page_count:
                raise OrganizeError(
                    f"Page {number} does not exist. This PDF has {page_count} pages."
                )
            indices.append(number - 1)
            if len(indices) > max_output_pages:
                raise OrganizeError(
                    f"The organized PDF can contain up to {max_output_pages} pages."
                )

    return indices


def organize_pdf(
    source: str | Path,
    output: str | Path,
    pages: str,
    *,
    max_input_mb: float = 100.0,
    max_source_pages: int = 1000,
    max_output_pages: int = 1000,
) -> dict:
    input_path = Path(source)
    destination = Path(output)

    if input_path.suffix.lower() != ".pdf":
        raise OrganizeError("Organize PDF accepts a PDF file only.")
    if not input_path.is_file() or input_path.stat().st_size == 0:
        raise OrganizeError("The uploaded PDF is missing or empty.")
    if input_path.stat().st_size > max_input_mb * 1024 * 1024:
        raise OrganizeError(f"The uploaded PDF is larger than {max_input_mb:g} MB.")

    try:
        reader = PdfReader(str(input_path), strict=False)
    except Exception as exc:
        raise OrganizeError("The uploaded file is not a readable PDF.") from exc

    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception:
            unlocked = 0
        if not unlocked:
            raise OrganizeError(
                "The PDF is password-protected. Unlock it before organizing pages."
            )

    source_page_count = len(reader.pages)
    if source_page_count == 0:
        raise OrganizeError("The uploaded PDF contains no pages.")
    if source_page_count > max_source_pages:
        raise OrganizeError(f"The PDF exceeds the {max_source_pages}-page limit.")

    indices = parse_page_order(
        pages,
        source_page_count,
        max_output_pages=max_output_pages,
    )
    writer = PdfWriter()
    temporary = destination.with_name(destination.name + ".partial")

    try:
        for index in indices:
            writer.add_page(reader.pages[index])

        if reader.metadata:
            metadata = {
                str(key): str(value)
                for key, value in reader.metadata.items()
                if key and value is not None
            }
            if metadata:
                writer.add_metadata(metadata)

        destination.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("wb") as stream:
            writer.write(stream)
        temporary.replace(destination)
    except OrganizeError:
        destination.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        raise
    except Exception as exc:
        destination.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        raise OrganizeError("The PDF pages could not be organized.") from exc
    finally:
        writer.close()

    try:
        result = PdfReader(str(destination), strict=False)
        actual_pages = len(result.pages)
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise OrganizeError("The organized PDF failed its final integrity check.") from exc
    finally:
        temporary.unlink(missing_ok=True)

    if actual_pages != len(indices):
        destination.unlink(missing_ok=True)
        raise OrganizeError("The organized PDF has an unexpected page count.")

    return {
        "ok": True,
        "output": str(destination),
        "source_pages": source_page_count,
        "pages": [index + 1 for index in indices],
        "output_pages": actual_pages,
        "input_bytes": input_path.stat().st_size,
        "output_bytes": destination.stat().st_size,
    }
