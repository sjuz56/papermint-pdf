"""PaperMint Split PDF engine.

Examples:
    pages="1-3,5" -> pages_1-3.pdf and page_5.pdf inside split.zip
    pages=""      -> one PDF per source page inside split.zip
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from pypdf import PdfReader, PdfWriter


class SplitError(RuntimeError):
    """A user-safe split failure."""


def _open_pdf(path: Path) -> PdfReader:
    if path.suffix.lower() != ".pdf":
        raise SplitError("Split PDF accepts a PDF file only.")
    if not path.is_file() or path.stat().st_size == 0:
        raise SplitError("The uploaded PDF is missing or empty.")

    try:
        reader = PdfReader(str(path), strict=False)
    except Exception as exc:
        raise SplitError("The uploaded file is not a readable PDF.") from exc

    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception:
            unlocked = 0
        if not unlocked:
            raise SplitError("Unlock the password-protected PDF before splitting it.")

    if len(reader.pages) < 1:
        raise SplitError("The uploaded PDF contains no pages.")
    return reader


def parse_page_groups(value: str, page_count: int) -> list[tuple[str, list[int]]]:
    """Return output labels and zero-based page indices."""
    text = (value or "").strip().replace(" ", "")
    if not text:
        width = max(3, len(str(page_count)))
        return [
            (f"page_{number:0{width}d}", [number - 1])
            for number in range(1, page_count + 1)
        ]

    groups: list[tuple[str, list[int]]] = []
    for token in text.split(","):
        if not token:
            raise SplitError("Use page ranges such as 1-3,5.")

        if re.fullmatch(r"\d+", token):
            start = end = int(token)
        else:
            match = re.fullmatch(r"(\d+)-(\d+)", token)
            if not match:
                raise SplitError(f"Invalid page range: {token}.")
            start, end = map(int, match.groups())

        if start < 1 or end < 1:
            raise SplitError("Page numbers start at 1.")
        if start > end:
            raise SplitError(f"Page range {token} is reversed.")
        if end > page_count:
            raise SplitError(
                f"Page {end} does not exist. This PDF has {page_count} pages."
            )

        label = f"page_{start}" if start == end else f"pages_{start}-{end}"
        groups.append((label, list(range(start - 1, end))))

    if len(groups) > 100:
        raise SplitError("You can create up to 100 split files at once.")
    return groups


def _write_group(reader: PdfReader, indices: list[int], output: Path) -> None:
    writer = PdfWriter()
    try:
        for index in indices:
            writer.add_page(reader.pages[index])
        with output.open("wb") as stream:
            writer.write(stream)
    finally:
        writer.close()

    try:
        written = PdfReader(str(output), strict=False)
        actual_pages = len(written.pages)
    except Exception as exc:
        raise SplitError("A split PDF failed its integrity check.") from exc

    if actual_pages != len(indices):
        raise SplitError("A split PDF contains an incorrect number of pages.")


def split_pdf(source: str | Path, output_zip: str | Path, pages: str = "") -> dict:
    source_path = Path(source)
    destination = Path(output_zip)
    reader = _open_pdf(source_path)
    page_count = len(reader.pages)
    groups = parse_page_groups(pages, page_count)

    destination.parent.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="papermint-split-", dir=destination.parent))
    temporary_zip = destination.with_name(destination.name + ".partial")
    files: list[dict] = []

    try:
        with ZipFile(temporary_zip, "w", compression=ZIP_DEFLATED) as archive:
            used_names: dict[str, int] = {}
            for label, indices in groups:
                used_names[label] = used_names.get(label, 0) + 1
                suffix = "" if used_names[label] == 1 else f"_{used_names[label]}"
                filename = f"{label}{suffix}.pdf"
                split_path = work_dir / filename
                _write_group(reader, indices, split_path)
                archive.write(split_path, arcname=filename)
                files.append(
                    {
                        "name": filename,
                        "pages": [index + 1 for index in indices],
                    }
                )

        with ZipFile(temporary_zip, "r") as archive:
            if archive.testzip() is not None or len(archive.namelist()) != len(files):
                raise SplitError("The split ZIP failed its integrity check.")

        temporary_zip.replace(destination)
    except Exception:
        destination.unlink(missing_ok=True)
        temporary_zip.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return {
        "ok": True,
        "output": str(destination),
        "source_pages": page_count,
        "files_created": len(files),
        "files": files,
        "output_bytes": destination.stat().st_size,
    }
