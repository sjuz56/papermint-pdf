"""PaperMint Merge PDF engine.

Combines complete PDF files in the supplied order and validates the result
before it is returned to the web layer.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader, PdfWriter


class MergeError(RuntimeError):
    """A user-safe merge failure."""


def _open_pdf(path: Path) -> PdfReader:
    if path.suffix.lower() != ".pdf":
        raise MergeError(f"{path.name} is not a PDF file.")
    if not path.is_file():
        raise MergeError(f"{path.name} was not found.")
    if path.stat().st_size == 0:
        raise MergeError(f"{path.name} is empty.")

    try:
        reader = PdfReader(str(path), strict=False)
    except Exception as exc:
        raise MergeError(f"{path.name} is not a readable PDF.") from exc

    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception:
            unlocked = 0
        if not unlocked:
            raise MergeError(
                f"{path.name} is password-protected. Unlock it before merging."
            )
    if len(reader.pages) == 0:
        raise MergeError(f"{path.name} contains no pages.")
    return reader


def merge_pdfs(
    inputs: Iterable[str | Path],
    output: str | Path,
    *,
    max_files: int = 25,
    max_total_mb: float = 100.0,
    max_pages: int = 1000,
) -> dict:
    paths = [Path(value) for value in inputs]
    destination = Path(output)

    if len(paths) < 2:
        raise MergeError("Upload at least two PDF files.")
    if len(paths) > max_files:
        raise MergeError(f"You can merge up to {max_files} PDF files at once.")

    total_bytes = sum(path.stat().st_size for path in paths if path.is_file())
    if total_bytes > max_total_mb * 1024 * 1024:
        raise MergeError(f"The combined upload is larger than {max_total_mb:g} MB.")

    writer = PdfWriter()
    source_pages: list[dict] = []
    expected_pages = 0

    try:
        for path in paths:
            reader = _open_pdf(path)
            pages = len(reader.pages)
            expected_pages += pages
            if expected_pages > max_pages:
                raise MergeError(
                    f"The merged document would exceed the {max_pages}-page limit."
                )
            for page in reader.pages:
                writer.add_page(page)
            source_pages.append({"name": path.name, "pages": pages})

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".partial")
        with temporary.open("wb") as stream:
            writer.write(stream)
        temporary.replace(destination)
    except Exception:
        destination.unlink(missing_ok=True)
        destination.with_name(destination.name + ".partial").unlink(missing_ok=True)
        raise
    finally:
        writer.close()

    try:
        result = PdfReader(str(destination), strict=False)
        actual_pages = len(result.pages)
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise MergeError("The merged PDF failed its final integrity check.") from exc

    if actual_pages != expected_pages:
        destination.unlink(missing_ok=True)
        raise MergeError(
            f"The merged PDF has {actual_pages} pages instead of {expected_pages}."
        )

    return {
        "ok": True,
        "output": str(destination),
        "files": len(paths),
        "pages": actual_pages,
        "input_bytes": total_bytes,
        "output_bytes": destination.stat().st_size,
        "sources": source_pages,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="PaperMint Merge PDF")
    parser.add_argument("inputs", nargs="+")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        report = merge_pdfs(args.inputs, args.output)
    except MergeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
